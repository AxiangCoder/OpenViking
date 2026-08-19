"""Session 产品数据访问（11 §70–§72，P2-E5）。

`platform_session_refs` / `platform_session_messages` /
`platform_session_commits` 的数据访问：归属/幂等键/顺序/软删除是 PostgreSQL
事实来源；消息正文与 Memory Diff 由受控 Session Backend 保存，本模块只做
幂等发号与状态记录。完整性问题统一包装 `ConstraintViolationError`
（对齐 registry/repository.py）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.errors import ConstraintViolationError
from openviking.server.platform.models import (
    PlatformSessionCommit,
    PlatformSessionMessage,
    PlatformSessionRef,
)

# 同步状态（11 §75.1）
SYNC_ACTIVE = "active"
SYNC_COMMIT_PENDING = "commit_pending"
SYNC_COMMITTING = "committing"
SYNC_COMMIT_FAILED = "commit_failed"
SYNC_RETRYING = "retrying"

# Phase 2 状态（11 §71.3）
PHASE2_PENDING = "pending"
PHASE2_RUNNING = "running"
PHASE2_COMPLETED = "completed"
PHASE2_FAILED = "failed"


class SessionRepository:
    """platform_session_* 表数据访问（服务层编排，事务由调用方控制）。"""

    # ── platform_session_refs ──

    async def insert_ref(
        self, session: AsyncSession, ref: PlatformSessionRef
    ) -> PlatformSessionRef:
        session.add(ref)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConstraintViolationError(str(exc)) from exc
        return ref

    async def get_ref(
        self, session: AsyncSession, ref_id: uuid.UUID, *, include_deleted: bool = False
    ) -> PlatformSessionRef | None:
        return await session.get(PlatformSessionRef, ref_id)

    async def get_ref_by_idempotency_key(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        idempotency_key: str,
    ) -> PlatformSessionRef | None:
        stmt = select(PlatformSessionRef).where(
            PlatformSessionRef.account_id == account_id,
            PlatformSessionRef.owner_user_id == owner_user_id,
            PlatformSessionRef.idempotency_key == idempotency_key,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_refs(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        status: str | None = "active",
        limit: int = 100,
    ) -> list[PlatformSessionRef]:
        """当前 User 未删除 Session 列表，按 `updated_at` 降序（11 §70.3）。"""
        stmt = select(PlatformSessionRef).where(
            PlatformSessionRef.account_id == account_id,
            PlatformSessionRef.owner_user_id == owner_user_id,
        )
        if status is not None:
            stmt = stmt.where(PlatformSessionRef.status == status)
        stmt = stmt.order_by(PlatformSessionRef.updated_at.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    async def list_refs_by_user(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        status: str | None = "active",
        limit: int = 100,
    ) -> list[PlatformSessionRef]:
        """按 Subject User 列表（admin/platform 成员只读复用，11 §73）。"""
        return await self.list_refs(
            session,
            account_id=account_id,
            owner_user_id=owner_user_id,
            status=status,
            limit=limit,
        )

    async def update_ref(self, session: AsyncSession, ref: PlatformSessionRef) -> PlatformSessionRef:
        await session.flush()
        return ref

    async def bump_ref_activity(
        self, session: AsyncSession, ref_id: uuid.UUID, *, now: datetime, **fields
    ) -> None:
        """追加/Commit 后更新同步元数据（last_sync_at/message_count 等）。"""
        fields["last_sync_at"] = now
        fields["updated_at"] = now
        await session.execute(
            update(PlatformSessionRef).where(PlatformSessionRef.id == ref_id).values(**fields)
        )

    # ── platform_session_messages（11 §70.8：服务端序列号 + 幂等键去重）──

    async def next_seq(self, session: AsyncSession, ref_id: uuid.UUID) -> int:
        """服务端发号：当前最大 seq + 1（同一 Session 顺序稳定，AC④）。"""
        current = (
            await session.execute(
                select(func.max(PlatformSessionMessage.seq)).where(
                    PlatformSessionMessage.session_id == ref_id
                )
            )
        ).scalar_one()
        return int(current or 0) + 1

    async def get_message_by_idempotency_key(
        self,
        session: AsyncSession,
        ref_id: uuid.UUID,
        idempotency_key: str,
    ) -> PlatformSessionMessage | None:
        stmt = select(PlatformSessionMessage).where(
            PlatformSessionMessage.session_id == ref_id,
            PlatformSessionMessage.idempotency_key == idempotency_key,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def insert_message(
        self, session: AsyncSession, message: PlatformSessionMessage
    ) -> PlatformSessionMessage:
        session.add(message)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConstraintViolationError(str(exc)) from exc
        return message

    async def count_messages(self, session: AsyncSession, ref_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(PlatformSessionMessage)
            .where(PlatformSessionMessage.session_id == ref_id)
        )
        return int((await session.execute(stmt)).scalar_one())

    # ── platform_session_commits（11 §71）──

    async def insert_commit(
        self, session: AsyncSession, commit: PlatformSessionCommit
    ) -> PlatformSessionCommit:
        session.add(commit)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConstraintViolationError(str(exc)) from exc
        return commit

    async def list_commits(
        self, session: AsyncSession, ref_id: uuid.UUID
    ) -> list[PlatformSessionCommit]:
        stmt = (
            select(PlatformSessionCommit)
            .where(PlatformSessionCommit.session_id == ref_id)
            .order_by(PlatformSessionCommit.commit_number)
        )
        return list((await session.execute(stmt)).scalars())

    async def get_commit_by_number(
        self, session: AsyncSession, ref_id: uuid.UUID, commit_number: int
    ) -> PlatformSessionCommit | None:
        stmt = select(PlatformSessionCommit).where(
            PlatformSessionCommit.session_id == ref_id,
            PlatformSessionCommit.commit_number == commit_number,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_commit_by_idempotency_key(
        self,
        session: AsyncSession,
        ref_id: uuid.UUID,
        idempotency_key: str,
    ) -> PlatformSessionCommit | None:
        stmt = select(PlatformSessionCommit).where(
            PlatformSessionCommit.session_id == ref_id,
            PlatformSessionCommit.idempotency_key == idempotency_key,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def update_commit_phase2(
        self,
        session: AsyncSession,
        commit_id: uuid.UUID,
        *,
        phase2_status: str,
        now: datetime,
        phase2_error: str | None = None,
        diff: list | None = None,
    ) -> None:
        values: dict = {
            "phase2_status": phase2_status,
            "phase2_error": phase2_error,
        }
        if diff is not None:
            values["diff_json"] = diff
        if phase2_status in (PHASE2_COMPLETED, PHASE2_FAILED):
            values["completed_at"] = now
        await session.execute(
            update(PlatformSessionCommit)
            .where(PlatformSessionCommit.id == commit_id)
            .values(**values)
        )
