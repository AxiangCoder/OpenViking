"""Purge Worker（04 §10.11，05 §11.3，14 号计划 §97.2，AC⑧）。

- 幂等领取：`pending` 且 `purge_after <= now` → `purging`（原子 UPDATE，
  同一任务只被领取一次；单实例内状态机防重）；
- 物理清理由 `PurgeHandler`（按 resource_type 注册）执行；成功 → `purged`，
  失败 → `failed` + 脱敏 `last_error`（指数退避由调用方按 ProvisioningWorker
  模式调度，本 Worker 单次轮询语义）；
- **审计不随物理清理**：只更新删除任务状态，不删除 `iam_audit_events`；
- 真实 OpenViking 数据清理（namespace/记忆/文件）属 P5-E1/E3 接线，
  本 Epic 交付 Worker 骨架 + 幂等状态机 + 可插拔 PurgeHandler 协议。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import IamDeletionJob
from openviking.server.platform.provisioning.principals import SystemPrincipal
from openviking.server.platform.provisioning.service import sanitize_error
from openviking.server.platform.registry.repository import RegistryRepository

PURGE_COMPONENT = "deletion.purge"


class PurgeHandler(Protocol):
    """物理清理处理器（按 resource_type 注册）。

    v0.1 本 Epic 交付幂等状态机；OpenViking 数据清理在 P5-E1 初始化阶段
    接线真实实现。`purge` 必须幂等（重放不产生重复副作用）。
    """

    async def purge(self, session: AsyncSession, job: IamDeletionJob) -> None: ...


class NoopPurgeHandler:
    """骨架默认：不执行任何外部清理（E3+ 接线真实处理器前保持安全空操作）。"""

    async def purge(self, session: AsyncSession, job: IamDeletionJob) -> None:
        return None


class PurgeWorker:
    """删除任务期满清理 Worker（单次轮询语义，由调用方决定调度周期）。"""

    def __init__(
        self,
        repo: IamRepository,
        store: RegistryRepository | None = None,
        handlers: dict[str, PurgeHandler] | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._repo = repo
        self._store = store or RegistryRepository()
        self._handlers = dict(handlers or {})
        self._config = config
        # P5-E2（14 号计划 §99.2，06 §16.3/§17.3）：Purge Worker 可观测状态。
        # `running` 由调度方置位（周期循环运行中）；`last_run_at` 每轮刷新。
        self.running: bool = False
        self.last_run_at: datetime | None = None
        self.last_processed: int = 0

    def register_handler(self, resource_type: str, handler: PurgeHandler) -> None:
        self._handlers[resource_type] = handler

    async def run_once(
        self,
        session: AsyncSession,
        *,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """处理一批到期删除任务；返回处理条数（每条独立提交，失败不影响后续）。"""
        now = now or datetime.now(timezone.utc)
        self.running = True
        self.last_run_at = now
        jobs = await self._store.claim_purge_jobs(
            session, now=now, limit=limit or self._config.purge_batch_size
        )
        processed = 0
        for job in jobs:
            principal = SystemPrincipal(component=PURGE_COMPONENT, task_id=str(job.id))
            try:
                handler = self._handlers.get(job.resource_type) or NoopPurgeHandler()
                await handler.purge(session, job)
                await self._store.mark_job_purged(session, job.id)
            except Exception as exc:  # noqa: BLE001
                await self._store.mark_job_failed(session, job.id, sanitize_error(exc))
                await self._append_purge_audit(
                    session, principal=principal, job=job, result="failed", reason=sanitize_error(exc)
                )
                await session.commit()
                processed += 1
                continue
            await self._append_purge_audit(
                session, principal=principal, job=job, result="success"
            )
            await session.commit()
            processed += 1
        self.last_processed = processed
        return processed

    async def _append_purge_audit(
        self,
        session: AsyncSession,
        *,
        principal: SystemPrincipal,
        job: IamDeletionJob,
        result: str,
        reason: str | None = None,
    ) -> None:
        """Purge 审计（04 §10.11：审计不随业务数据物理清理，AC⑧）。"""
        await self._repo.append_audit_event(
            session,
            account_id=job.account_id,
            actor_type="system",
            actor_system_component=principal.component,
            authentication_method="system",
            subject_account_id=job.account_id,
            action=f"purge.{job.resource_type}",
            target_type=job.resource_type,
            target_id=job.resource_id,
            scope="platform",
            result=result,
            reason=reason,
            metadata={"deletion_job_id": str(job.id)},
        )
