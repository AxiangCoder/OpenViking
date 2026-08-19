"""P5-E2（14 号计划 §99.2）：17.3 健康检查扩展与 06 §16.3 阈值判定。

在既有 `/health`、`/ready`（openviking/server/routers/system.py）之上增加
Platform 生产运维维度，全部阈值入 `PlatformConfig`（06 §16.3）：

- PostgreSQL 连通性（SELECT 1）；
- IAM migration 版本（`alembic_version` vs 代码内 head）；
- Provisioning backlog 与失败数量（`iam_outbox` status != completed）；
- 登录 Session cleanup worker 状态（`last_result`，清理周期入配置）；
- 软删除待清理数量、最早 `purge_after` 与 Purge Worker 状态。

`/ready` 判定（本 Epic 定义，06 §16.3，验收④可精确复测）：
- provisioning_backlog > `provisioning_backlog_threshold`（默认 100）→ 非 ready；
- migration 落后 >= `migration_lag_versions`（默认 1）→ 非 ready；
- Purge 停滞：待清理数量 > `purge_stall_max_pending`（默认 500）、
  或最早 `purge_after` 落后 > `purge_stall_max_stall_days`（默认 3）天、
  或 Purge Worker 未运行 → 非 ready。

每个检查返回统一结构 `{"status": "ok"|"error", "detail": ..., "threshold": ...}`，
`status != "ok"` 即判定非 ready；诊断文案可读且不包含口令/Token/完整 hash/
Key 明文（06 §14.4）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.models import IamDeletionJob, IamOutbox, IamSession

logger = logging.getLogger("openviking.platform.health")

_OK = {"status": "ok"}


def _extract_identifier(source: str, name: str) -> str:
    """从 Alembic 迁移文件源码提取 `revision`/`down_revision` 值。

    兼容两种写法：`revision: str = "xxx"` 与
    `down_revision: Union[str, Sequence[str], None] = "xxx"`（取 `=` 后引号）。
    """
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith(f"{name}:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        if "=" in value:
            value = value.rsplit("=", 1)[1].strip()
        if value.startswith(('"', "'")):
            return value[1:].split(value[0], 1)[0]
        return ""  # None 等无引号默认值：该 revision 无 down（head）
    return ""


def head_migration_revision() -> str:
    """代码内迁移 head 版本号（不被任何 revision 引为 down 的即 head）。"""
    versions_dir = Path(__file__).resolve().parent / "migrations" / "versions"
    revisions: dict[str, str] = {}
    for path in versions_dir.glob("*.py"):
        if path.name.startswith("_") or path.name == "script.py.mako":
            continue
        source = path.read_text(encoding="utf-8")
        revision = _extract_identifier(source, "revision")
        if revision:
            revisions[revision] = _extract_identifier(source, "down_revision")
    heads = [rev for rev in revisions if rev not in revisions.values()]
    return heads[0] if heads else ""


async def _current_migration_revision(session: AsyncSession) -> str | None:
    """读 `alembic_version` 当前版本号（无迁移 → None）。"""
    return (
        await session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one_or_none()


class PlatformHealthChecks:
    """Platform 17.3 健康检查集合（挂载于 `app.state.platform_health_checks`）。

    `session_cleanup_worker` / `purge_worker` 由挂载方注入（缺省 None 时
    worker 状态如实报告 not_configured/idle，DB 维度照常判定）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: PlatformConfig = platform_config,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self.session_cleanup_worker: Any = None
        self.purge_worker: Any = None

    # ── 17.3：PostgreSQL 连通性 ──

    async def pg_connectivity(self) -> dict[str, Any]:
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: PostgreSQL unreachable: %s", exc)
            return {"status": "error", "detail": "postgresql unreachable"}
        return dict(_OK)

    # ── 17.3：IAM migration 版本 ──

    async def migration(self) -> dict[str, Any]:
        try:
            async with self._session_factory() as session:
                current = await _current_migration_revision(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: migration version check failed: %s", exc)
            return {"status": "error", "detail": "migration version check failed"}
        head = head_migration_revision()
        lag = 0 if current == head else 1
        result: dict[str, Any] = {
            "detail": {
                "current": current or "none",
                "head": head,
                "lag_versions": lag,
            },
            "threshold": f"lag >= {self._config.migration_lag_versions} version",
        }
        result["status"] = "ok" if lag < self._config.migration_lag_versions else "error"
        return result

    # ── 17.3：Provisioning backlog 与失败数量 ──

    async def provisioning(self) -> dict[str, Any]:
        threshold = self._config.provisioning_backlog_threshold
        try:
            async with self._session_factory() as session:
                total = (
                    await session.execute(
                        select(func.count())
                        .select_from(IamOutbox)
                        .where(IamOutbox.status != "completed")
                    )
                ).scalar_one()
                failed = (
                    await session.execute(
                        select(func.count())
                        .select_from(IamOutbox)
                        .where(IamOutbox.status == "failed")
                    )
                ).scalar_one()
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: provisioning backlog check failed: %s", exc)
            return {"status": "error", "detail": "provisioning backlog check failed"}
        result: dict[str, Any] = {
            "detail": {"backlog": total, "failed": failed},
            "threshold": f"backlog > {threshold}",
        }
        result["status"] = "ok" if total <= threshold else "error"
        return result

    # ── 17.3：登录 Session cleanup worker 状态 ──

    async def session_cleanup(self) -> dict[str, Any]:
        try:
            async with self._session_factory() as session:
                remaining = (
                    await session.execute(select(func.count()).select_from(IamSession))
                ).scalar_one()
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: session cleanup check failed: %s", exc)
            return {"status": "error", "detail": "session cleanup check failed"}
        worker = self.session_cleanup_worker
        if worker is None:
            worker_state = "not_configured"
            last_run_at = None
        else:
            task = getattr(worker, "_task", None)
            worker_state = "running" if task is not None and not task.done() else "idle"
            last = getattr(worker, "last_result", None)
            last_run_at = getattr(last, "ran_at", None)
            if last_run_at is not None:
                last_run_at = last_run_at.isoformat()
        return {
            "status": "ok",
            "detail": {
                "remaining_sessions": remaining,
                "worker": worker_state,
                "last_run_at": last_run_at,
            },
        }

    # ── 17.3：软删除待清理数量、最早 purge_after 与 Purge Worker 状态 ──

    async def purge(self) -> dict[str, Any]:
        max_pending = self._config.purge_stall_max_pending
        max_stall_days = self._config.purge_stall_max_stall_days
        try:
            async with self._session_factory() as session:
                pending = (
                    await session.execute(
                        select(func.count())
                        .select_from(IamDeletionJob)
                        .where(IamDeletionJob.status.in_(("pending", "purging")))
                    )
                ).scalar_one()
                earliest = (
                    await session.execute(
                        select(func.min(IamDeletionJob.purge_after)).where(
                            IamDeletionJob.status.in_(("pending", "purging"))
                        )
                    )
                ).scalar_one()
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: purge status check failed: %s", exc)
            return {"status": "error", "detail": "purge status check failed"}

        worker = self.purge_worker
        worker_running = bool(getattr(worker, "running", False))
        now = datetime.now(timezone.utc)
        stall_days: float | None = None
        if earliest is not None:
            stall_days = max((now - earliest).total_seconds() / 86400.0, 0.0)
        detail: dict[str, Any] = {
            "pending": pending,
            "earliest_purge_after": earliest.isoformat() if earliest is not None else None,
            "purge_worker_running": worker_running,
        }
        reasons: list[str] = []
        if pending > max_pending:
            reasons.append(f"pending {pending} > max {max_pending}")
        if stall_days is not None and stall_days > max_stall_days:
            reasons.append(f"earliest purge_after stalled {stall_days:.1f}d > {max_stall_days}d")
        if not worker_running and pending:
            reasons.append("purge worker not running")
        if reasons:
            detail["reasons"] = reasons
        return {
            "status": "ok" if not reasons else "error",
            "detail": detail,
            "threshold": (
                f"pending > {max_pending} or stall > {max_stall_days}d or worker not running"
            ),
        }

    # ── 汇总：`/ready` 判定（任一非 ok → 非 ready）──

    async def all_checks(self) -> dict[str, dict[str, Any]]:
        return {
            "postgresql": await self.pg_connectivity(),
            "migration": await self.migration(),
            "provisioning": await self.provisioning(),
            "session_cleanup": await self.session_cleanup(),
            "purge": await self.purge(),
        }

    def is_ready(self, checks: dict[str, dict[str, Any]]) -> bool:
        return all(check.get("status") == "ok" for check in checks.values())
