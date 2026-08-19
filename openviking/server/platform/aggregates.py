"""admin/platform 聚合端点（05 §12.6，14 号计划 §97.2，AC⑨）。

- `admin/activity`（`task.read.account_shared`）：仅当前 Account 共享对象任务
  （04 §10.12：Account Admin 的管理视图只显示共享对象任务，不默认暴露所有
  成员的私有任务日志）；
- `platform/activity`（`task.read.platform`）：平台范围任务，可按目标
  Account 过滤；每次查询仍记录 Actor、Subject Account/User 与 Scope；
- `admin/monitoring` / `platform/monitoring`（`monitoring.read`）：仅业务
  摘要（计数），不暴露 Queue/锁/模型/VectorDB 等底层状态（05 §12.5
  dashboard 受控聚合原则的同一约束）。

E3–E5 交付业务对象端点后，本模块的计数与任务视图在其数据上增量扩展；
v0.1 骨架以已建模表（platform_operation_refs / iam_deletion_jobs /
platform_content_refs / iam_accounts / iam_users / platform_uploads）聚合。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import (
    IamAccount,
    IamDeletionJob,
    IamUser,
    PlatformContentRef,
    PlatformOperationRef,
    PlatformUpload,
)
from openviking.server.platform.registry.repository import (
    JOB_PENDING,
    STATUS_ACTIVE,
    RegistryRepository,
)


@dataclass(frozen=True)
class ActivityRow:
    """脱敏任务行（04 §10.12：`ov_operation_id` 不返回为可枚举主 ID）。"""

    id: str
    operation_type: str
    status: str
    stage: str | None
    target_type: str | None
    target_id: str | None
    target_visibility: str
    generation: int
    cancellable: bool
    error_code: str | None
    created_at: str
    completed_at: str | None


def _operation_dto(op: PlatformOperationRef) -> dict:
    return {
        "id": str(op.id),
        "operation_type": op.operation_type,
        "status": op.status,
        "stage": op.stage,
        "target_type": op.target_type,
        "target_id": op.target_id,
        "target_visibility": op.target_visibility,
        "generation": op.generation,
        "cancellable": op.cancellable,
        "error_code": op.error_code,
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "completed_at": op.completed_at.isoformat() if op.completed_at else None,
    }


class AggregateService:
    """admin/platform 聚合查询（05 §12.6；权限由 Router 逐端点校验）。"""

    def __init__(self, store: RegistryRepository | None = None) -> None:
        self._store = store or RegistryRepository()

    # ── /activity（05 §12.6）──

    async def list_account_shared_activity(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        limit: int = 50,
        cursor_id: uuid.UUID | None = None,
    ) -> list[dict]:
        """admin/activity：仅当前 Account 共享对象任务（task.read.account_shared）。"""
        ops = await self._store.list_operations(
            session,
            account_id=account_id,
            target_visibility="account_shared",
            limit=limit,
            cursor_id=cursor_id,
        )
        return [_operation_dto(op) for op in ops]

    async def list_platform_activity(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID | None = None,
        limit: int = 50,
        cursor_id: uuid.UUID | None = None,
    ) -> list[dict]:
        """platform/activity：平台范围任务（task.read.platform），可按 Account 过滤。"""
        ops = await self._store.list_operations(
            session,
            account_id=account_id,
            limit=limit,
            cursor_id=cursor_id,
        )
        return [_operation_dto(op) for op in ops]

    # ── /monitoring（05 §12.6：仅业务摘要，不暴露底层状态）──

    async def account_monitoring(self, session: AsyncSession, *, account_id: uuid.UUID) -> dict:
        """admin/monitoring：仅当前 Account 业务摘要。"""
        summary = await self._summary_counts(session, account_id=account_id)
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": "account", "summary": summary}

    async def platform_monitoring(self, session: AsyncSession) -> dict:
        """platform/monitoring：平台聚合业务摘要。"""
        summary = await self._summary_counts(session, account_id=None)
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": "platform", "summary": summary}

    async def _summary_counts(self, session: AsyncSession, *, account_id: uuid.UUID | None) -> dict:
        async def count(model) -> int:
            stmt = select(func.count()).select_from(model)
            if account_id is not None and hasattr(model, "account_id"):
                stmt = stmt.where(model.account_id == account_id)
            return (await session.execute(stmt)).scalar_one()

        async def count_active(model) -> int:
            stmt = (
                select(func.count())
                .select_from(model)
                .where(model.deleted_at.is_(None), model.status == STATUS_ACTIVE)
            )
            if account_id is not None and hasattr(model, "account_id"):
                stmt = stmt.where(model.account_id == account_id)
            return (await session.execute(stmt)).scalar_one()

        content_by_type: dict[str, int] = {}
        if account_id is not None:
            for object_type in ("resource", "skill"):
                content_by_type[object_type] = len(
                    await self._store.list_refs(
                        session,
                        account_id=account_id,
                        object_type=object_type,
                        status=STATUS_ACTIVE,
                    )
                )
        else:
            stmt = (
                select(PlatformContentRef.object_type, func.count())
                .where(PlatformContentRef.status == STATUS_ACTIVE)
                .group_by(PlatformContentRef.object_type)
            )
            content_by_type = dict((await session.execute(stmt)).all())

        jobs_stmt = (
            select(func.count())
            .select_from(IamDeletionJob)
            .where(IamDeletionJob.status == JOB_PENDING)
        )
        if account_id is not None:
            jobs_stmt = jobs_stmt.where(IamDeletionJob.account_id == account_id)
        jobs_in_recycle = (await session.execute(jobs_stmt)).scalar_one()

        return {
            "accounts": await count(IamAccount),
            "active_accounts": await count_active(IamAccount),
            "users": await count(IamUser),
            "active_users": await count_active(IamUser),
            "content_refs_active_by_type": content_by_type,
            "deletion_jobs_in_recycle": jobs_in_recycle,
            "pending_uploads": await self._count_pending_uploads(session, account_id),
            "open_operations": await count(PlatformOperationRef),
        }

    async def _count_pending_uploads(self, session: AsyncSession, account_id: uuid.UUID | None) -> int:
        stmt = (
            select(func.count())
            .select_from(PlatformUpload)
            .where(PlatformUpload.status.in_(["uploading", "ready"]))
        )
        if account_id is not None:
            stmt = stmt.where(PlatformUpload.account_id == account_id)
        return (await session.execute(stmt)).scalar_one()
