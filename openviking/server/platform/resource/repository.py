"""Resource 数据访问（09 §43.2 Watch 配置 + 产品列表/Activity 查询）。

只 append 新方法，不修改既有模块：Watch 行与产品列表查询属 P2-E3 新增，
集中在本仓库；`platform_content_refs`/`platform_operation_refs` 的既有
读写仍走 `RegistryRepository`（P2-E2 冻结点）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import (
    PlatformContentRef,
    PlatformOperationRef,
    PlatformResourceWatch,
)

WATCH_ACTIVE = "active"
WATCH_PAUSED = "paused"
WATCH_ERROR = "error"

_WATCH_VISIBLE_STATUSES = ("active", "provisioning", "failed")


class ResourceRepository:
    """Watch 配置与产品列表数据访问（事务由调用方控制）。"""

    # ── platform_resource_watches（09 §43.2）──

    async def get_watch(self, session: AsyncSession, resource_id: uuid.UUID) -> PlatformResourceWatch | None:
        stmt = select(PlatformResourceWatch).where(
            PlatformResourceWatch.resource_id == resource_id,
            PlatformResourceWatch.deleted_at.is_(None),
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def insert_watch(self, session: AsyncSession, watch: PlatformResourceWatch) -> PlatformResourceWatch:
        session.add(watch)
        await session.flush()
        return watch

    async def update_watch(self, session: AsyncSession, watch: PlatformResourceWatch) -> PlatformResourceWatch:
        await session.flush()
        return watch

    async def soft_delete_watch(self, session: AsyncSession, resource_id: uuid.UUID, now: datetime) -> bool:
        result = await session.execute(
            update(PlatformResourceWatch)
            .where(PlatformResourceWatch.resource_id == resource_id)
            .where(PlatformResourceWatch.deleted_at.is_(None))
            .values(deleted_at=now, state=WATCH_PAUSED)
        )
        return (result.rowcount or 0) > 0

    async def pause_all_for_resource(self, session: AsyncSession, resource_id: uuid.UUID, now: datetime) -> int:
        """删除六步事务第 3 步：Watch 立即暂停（09 §45.2）。"""
        result = await session.execute(
            update(PlatformResourceWatch)
            .where(PlatformResourceWatch.resource_id == resource_id)
            .where(PlatformResourceWatch.deleted_at.is_(None))
            .where(PlatformResourceWatch.state.in_([WATCH_ACTIVE, WATCH_ERROR]))
            .values(state=WATCH_PAUSED, updated_at=now)
        )
        return result.rowcount or 0

    async def list_watches_due(
        self, session: AsyncSession, *, now: datetime, limit: int = 50
    ) -> list[PlatformResourceWatch]:
        """Watch Worker 调度输入（09 §43.2：只返回 active 且到期的 Watch）。"""
        stmt = (
            select(PlatformResourceWatch)
            .where(
                PlatformResourceWatch.state == WATCH_ACTIVE,
                PlatformResourceWatch.deleted_at.is_(None),
                or_(
                    PlatformResourceWatch.next_run_at.is_(None),
                    PlatformResourceWatch.next_run_at <= now,
                ),
            )
            .order_by(PlatformResourceWatch.next_run_at.nullsfirst())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars())

    async def list_account_watches(
        self, session: AsyncSession, *, account_id: uuid.UUID, limit: int = 200
    ) -> list[PlatformResourceWatch]:
        stmt = (
            select(PlatformResourceWatch)
            .where(
                PlatformResourceWatch.account_id == account_id,
                PlatformResourceWatch.deleted_at.is_(None),
            )
            .order_by(PlatformResourceWatch.created_at)
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars())

    async def hard_delete_watch(self, session: AsyncSession, resource_id: uuid.UUID) -> None:
        """Purge 物理清理：删除 Watch 配置行（09 §45.2 步骤 6）。"""
        await session.execute(
            update(PlatformResourceWatch)
            .where(PlatformResourceWatch.resource_id == resource_id)
            .values(deleted_at=datetime.now(timezone_utc()))
        )

    # ── 产品列表（09 §39：私有含 provisioning/failed 占位行；共享仅 active）──

    async def list_resources(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        visibility: str,
        owner_user_id: uuid.UUID | None = None,
        include_placeholders: bool = False,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: uuid.UUID | None = None,
    ) -> list[PlatformContentRef]:
        stmt = select(PlatformContentRef).where(
            PlatformContentRef.account_id == account_id,
            PlatformContentRef.visibility == visibility,
            PlatformContentRef.deleted_at.is_(None),
        )
        if owner_user_id is not None:
            stmt = stmt.where(PlatformContentRef.owner_user_id == owner_user_id)
        if include_placeholders:
            stmt = stmt.where(PlatformContentRef.status.in_(_WATCH_VISIBLE_STATUSES))
        elif status is not None:
            stmt = stmt.where(PlatformContentRef.status == status)
        else:
            stmt = stmt.where(PlatformContentRef.status == "active")
        if source_type is not None:
            stmt = stmt.where(PlatformContentRef.source_type == source_type)
        if cursor_created_at is not None:
            stmt = stmt.where(
                or_(
                    PlatformContentRef.created_at < cursor_created_at,
                    (
                        (PlatformContentRef.created_at == cursor_created_at)
                        & (PlatformContentRef.id < cursor_id)
                    ),
                )
            )
        stmt = stmt.order_by(PlatformContentRef.created_at.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    # ── 产品 Activity（09 §43.3：私有本人 + 当前 Account 共享）──

    async def list_user_activity(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = 50,
        cursor_id: uuid.UUID | None = None,
    ) -> list[PlatformOperationRef]:
        """`/activity`：本人私有对象任务 + 当前 Account 共享对象任务（09 §43.3/04 §10.12）。"""
        stmt = select(PlatformOperationRef).where(
            PlatformOperationRef.account_id == account_id,
            or_(
                (
                    (PlatformOperationRef.target_visibility == "user_private")
                    & (PlatformOperationRef.owner_user_id == user_id)
                ),
                PlatformOperationRef.target_visibility == "account_shared",
            ),
        )
        if cursor_id is not None:
            stmt = stmt.where(PlatformOperationRef.id < cursor_id)
        stmt = stmt.order_by(PlatformOperationRef.id.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    # ── Resource 操作查询（09 §43.3 Activity / 09 §45.2 协作取消）──

    async def get_operation_for_target(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        target_type: str,
        target_id: str,
        operation_type: str | None = None,
        operation_kind: str | None = None,
    ) -> PlatformOperationRef | None:
        stmt = select(PlatformOperationRef).where(
            PlatformOperationRef.account_id == account_id,
            PlatformOperationRef.target_type == target_type,
            PlatformOperationRef.target_id == target_id,
        )
        if operation_type is not None:
            stmt = stmt.where(PlatformOperationRef.operation_type == operation_type)
        if operation_kind is not None:
            stmt = stmt.where(PlatformOperationRef.operation_kind == operation_kind)
        stmt = stmt.order_by(PlatformOperationRef.created_at.desc()).limit(1)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_operations_for_resource(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        resource_id: str,
        limit: int = 50,
    ) -> list[PlatformOperationRef]:
        stmt = (
            select(PlatformOperationRef)
            .where(
                PlatformOperationRef.account_id == account_id,
                PlatformOperationRef.target_type == "resource",
                PlatformOperationRef.target_id == resource_id,
            )
            .order_by(PlatformOperationRef.created_at.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars())

    async def get_operation(
        self, session: AsyncSession, operation_id: uuid.UUID
    ) -> PlatformOperationRef | None:
        return await session.get(PlatformOperationRef, operation_id)

    async def list_inflight_operations(
        self, session: AsyncSession, *, resource_id: str
    ) -> list[PlatformOperationRef]:
        """删除六步第 4 步输入：可取消在途任务（09 §45.2）。"""
        stmt = (
            select(PlatformOperationRef)
            .where(
                PlatformOperationRef.target_type == "resource",
                PlatformOperationRef.target_id == resource_id,
                PlatformOperationRef.status.in_(("pending", "running")),
                PlatformOperationRef.cancellable.is_(True),
            )
            .order_by(PlatformOperationRef.created_at)
        )
        return list((await session.execute(stmt)).scalars())


def timezone_utc():
    from datetime import timezone

    return timezone.utc
