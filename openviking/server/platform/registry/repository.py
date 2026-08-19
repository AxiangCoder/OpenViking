"""Content Registry 数据访问（04 §10.10/§10.12/§10.14）。

完整性问题统一包装 `ConstraintViolationError`；失败语句会 abort 当前事务，
捕获后调用方按需 `session.rollback()`（对齐 iam/postgres_repository.py）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.errors import ConstraintViolationError, EntityNotFoundError
from openviking.server.platform.models import (
    IamAccount,
    IamDeletionJob,
    IamUser,
    PlatformContentRef,
    PlatformOperationRef,
    PlatformUpload,
)

# 对象可见状态（04 §10.10）
STATUS_ACTIVE = "active"
STATUS_PROVISIONING = "provisioning"
STATUS_FAILED = "failed"
STATUS_PENDING_DELETION = "pending_deletion"
STATUS_DELETED = "deleted"

# 删除任务状态（04 §10.11）
JOB_PENDING = "pending"
JOB_RESTORED = "restored"
JOB_PURGING = "purging"
JOB_PURGED = "purged"
JOB_FAILED = "failed"

# Upload 状态（04 §10.14）
UPLOAD_UPLOADING = "uploading"
UPLOAD_READY = "ready"
UPLOAD_CONSUMED = "consumed"
UPLOAD_EXPIRED = "expired"
UPLOAD_FAILED = "failed"


class RegistryRepository:
    """platform_* 与 iam_deletion_jobs 表的数据访问（服务层编排，事务由调用方控制）。"""

    # ── platform_content_refs（04 §10.10）──

    async def insert_ref(
        self,
        session: AsyncSession,
        ref: PlatformContentRef,
    ) -> PlatformContentRef:
        session.add(ref)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConstraintViolationError(str(exc)) from exc
        return ref

    async def get_ref(
        self,
        session: AsyncSession,
        ref_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> PlatformContentRef | None:
        return await session.get(PlatformContentRef, ref_id)

    async def get_ref_by_idempotency_key(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        object_type: str,
        idempotency_key: str,
    ) -> PlatformContentRef | None:
        stmt = select(PlatformContentRef).where(
            PlatformContentRef.account_id == account_id,
            PlatformContentRef.object_type == object_type,
            PlatformContentRef.idempotency_key == idempotency_key,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_ref_by_uri(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        ov_uri: str,
    ) -> PlatformContentRef | None:
        stmt = select(PlatformContentRef).where(
            PlatformContentRef.account_id == account_id,
            PlatformContentRef.ov_uri == ov_uri,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def update_ref(self, session: AsyncSession, ref: PlatformContentRef) -> PlatformContentRef:
        await session.flush()
        return ref

    async def list_refs(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        object_type: str | None = None,
        visibility: str | None = None,
        owner_user_id: uuid.UUID | None = None,
        status: str | None = STATUS_ACTIVE,
    ) -> list[PlatformContentRef]:
        """列表；默认只返回 `active` 引用（AC⑤：产品列表只返回 active）。"""
        stmt = select(PlatformContentRef).where(PlatformContentRef.account_id == account_id)
        if object_type is not None:
            stmt = stmt.where(PlatformContentRef.object_type == object_type)
        if visibility is not None:
            stmt = stmt.where(PlatformContentRef.visibility == visibility)
        if owner_user_id is not None:
            stmt = stmt.where(PlatformContentRef.owner_user_id == owner_user_id)
        if status is not None:
            stmt = stmt.where(PlatformContentRef.status == status)
        stmt = stmt.order_by(PlatformContentRef.created_at)
        return list((await session.execute(stmt)).scalars())

    async def find_skill_by_canonical_name(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        canonical_name: str,
        *,
        include_deleted: bool = False,
    ) -> PlatformContentRef | None:
        """Skill 名称定位（04 §10.10：Account 内未删除 Skill canonical_name 全局唯一）。

        冲突检查只用于服务层预检（返回"名称不可用"稳定码，不泄露占用者，AC⑥）；
        DB 部分唯一索引是最终强制。
        """
        stmt = select(PlatformContentRef).where(
            PlatformContentRef.account_id == account_id,
            PlatformContentRef.object_type == "skill",
            PlatformContentRef.canonical_name == canonical_name,
            PlatformContentRef.deleted_at.is_(None),
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    # ── platform_operation_refs（04 §10.12）──

    async def insert_operation(
        self,
        session: AsyncSession,
        operation: PlatformOperationRef,
    ) -> PlatformOperationRef:
        session.add(operation)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConstraintViolationError(str(exc)) from exc
        return operation

    async def update_operation(
        self,
        session: AsyncSession,
        operation_id: uuid.UUID,
        **fields,
    ) -> PlatformOperationRef:
        fields = {k: v for k, v in fields.items() if v is not None}
        stmt = (
            update(PlatformOperationRef)
            .where(PlatformOperationRef.id == operation_id)
            .values(**fields)
            .returning(PlatformOperationRef)
        )
        operation = (await session.execute(stmt)).scalar_one_or_none()
        if operation is None:
            raise EntityNotFoundError(f"operation {operation_id} not found")
        return operation

    async def list_operations(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID | None,
        target_visibility: str | None = None,
        owner_user_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor_id: uuid.UUID | None = None,
    ) -> list[PlatformOperationRef]:
        stmt = select(PlatformOperationRef)
        if account_id is not None:
            stmt = stmt.where(PlatformOperationRef.account_id == account_id)
        if target_visibility is not None:
            stmt = stmt.where(PlatformOperationRef.target_visibility == target_visibility)
        if owner_user_id is not None:
            stmt = stmt.where(PlatformOperationRef.owner_user_id == owner_user_id)
        if status is not None:
            stmt = stmt.where(PlatformOperationRef.status == status)
        if cursor_id is not None:
            stmt = stmt.where(PlatformOperationRef.id < cursor_id)
        stmt = stmt.order_by(PlatformOperationRef.id.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    # ── platform_uploads（04 §10.14）──

    async def insert_upload(self, session: AsyncSession, upload: PlatformUpload) -> PlatformUpload:
        session.add(upload)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConstraintViolationError(str(exc)) from exc
        return upload

    async def get_upload(
        self,
        session: AsyncSession,
        upload_id: uuid.UUID,
    ) -> PlatformUpload | None:
        return await session.get(PlatformUpload, upload_id)

    async def consume_upload_atomic(
        self,
        session: AsyncSession,
        *,
        upload_id: uuid.UUID,
        consumed_by_operation_id: uuid.UUID,
        now: datetime,
    ) -> PlatformUpload | None:
        """原子 `ready → consumed`（04 §10.14，AC⑦）：仅 ready 可被消费。

        并发安全：`UPDATE ... WHERE status='ready'` 行锁语义保证同一
        Upload 只被一个消费方成功转换；重复消费返回 None。
        """
        stmt = (
            update(PlatformUpload)
            .where(PlatformUpload.id == upload_id)
            .where(PlatformUpload.status == UPLOAD_READY)
            .values(
                status=UPLOAD_CONSUMED,
                consumed_by_operation_id=consumed_by_operation_id,
                consumed_at=now,
            )
            .returning(PlatformUpload)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def expire_uploads(self, session: AsyncSession, now: datetime, limit: int = 100) -> int:
        """到期清理：`ready|uploading` 且 `expires_at <= now` → `expired`（幂等）。

        到期/失败/取消/已消费文件的临时字节由 Cleanup Worker 删除；
        本方法只做状态转换（04 §10.14）。
        """
        stmt = (
            update(PlatformUpload)
            .where(PlatformUpload.status.in_([UPLOAD_UPLOADING, UPLOAD_READY]))
            .where(PlatformUpload.expires_at <= now)
            .values(status=UPLOAD_EXPIRED)
            .returning(PlatformUpload.id)
        )
        rows = list((await session.execute(stmt)).scalars())
        return len(rows)

    # ── iam_deletion_jobs（04 §10.11）──

    async def insert_deletion_job(
        self,
        session: AsyncSession,
        job: IamDeletionJob,
    ) -> IamDeletionJob:
        session.add(job)
        await session.flush()
        return job

    async def get_deletion_job(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> IamDeletionJob | None:
        return await session.get(IamDeletionJob, job_id)

    async def get_active_deletion_job(
        self,
        session: AsyncSession,
        resource_type: str,
        resource_id: str,
    ) -> IamDeletionJob | None:
        """目标对象当前未恢复的删除任务（幂等删除复用）。"""
        stmt = select(IamDeletionJob).where(
            IamDeletionJob.resource_type == resource_type,
            IamDeletionJob.resource_id == resource_id,
            IamDeletionJob.status.in_([JOB_PENDING, JOB_PURGING, JOB_FAILED]),
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_deletion_jobs(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID | None = None,
        resource_types: tuple[str, ...] | None = None,
        status: str | None = JOB_PENDING,
        limit: int = 100,
        cursor_id: uuid.UUID | None = None,
    ) -> list[IamDeletionJob]:
        stmt = select(IamDeletionJob)
        if account_id is not None:
            stmt = stmt.where(IamDeletionJob.account_id == account_id)
        if resource_types is not None:
            stmt = stmt.where(IamDeletionJob.resource_type.in_(resource_types))
        if status is not None:
            stmt = stmt.where(IamDeletionJob.status == status)
        if cursor_id is not None:
            stmt = stmt.where(IamDeletionJob.id < cursor_id)
        stmt = stmt.order_by(IamDeletionJob.id.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars())

    async def claim_purge_jobs(
        self,
        session: AsyncSession,
        now: datetime,
        limit: int = 50,
    ) -> list[IamDeletionJob]:
        """Purge Worker 抢领（04 §10.11）：`pending` 且 `purge_after <= now` →
        `purging`。幂等：同一任务只会被领取一次（AC⑧）。

        先按到期时间排序选出候选，再逐条 `UPDATE ... WHERE status='pending'`
        原子抢占；并发 Worker 抢不到的候选不返回（防止重复清理）。
        """
        candidates = list(
            (
                await session.execute(
                    select(IamDeletionJob)
                    .where(IamDeletionJob.status == JOB_PENDING)
                    .where(IamDeletionJob.purge_after <= now)
                    .order_by(IamDeletionJob.purge_after, IamDeletionJob.id)
                    .limit(limit)
                )
            ).scalars()
        )
        claimed: list[IamDeletionJob] = []
        for candidate in candidates:
            stmt = (
                update(IamDeletionJob)
                .where(IamDeletionJob.id == candidate.id)
                .where(IamDeletionJob.status == JOB_PENDING)
                .values(status=JOB_PURGING)
                .returning(IamDeletionJob)
            )
            claimed_row = (await session.execute(stmt)).scalar_one_or_none()
            if claimed_row is not None:
                claimed.append(claimed_row)
        return claimed

    async def mark_job_purged(self, session: AsyncSession, job_id: uuid.UUID) -> None:
        await session.execute(
            update(IamDeletionJob)
            .where(IamDeletionJob.id == job_id)
            .values(status=JOB_PURGED, last_error=None)
        )

    async def mark_job_failed(self, session: AsyncSession, job_id: uuid.UUID, error: str) -> None:
        await session.execute(
            update(IamDeletionJob)
            .where(IamDeletionJob.id == job_id)
            .values(status=JOB_FAILED, last_error=error)
        )

    # ── 恢复（05 §11.3：回收期内清除 deleted_at/purge_after/deleted_by）──
    # iam/ repository 的 update_user/update_account 为 P1-E1 冻结白名单字段，
    # 恢复清理走本仓库直接 UPDATE（只清理回收字段，不触碰权限/状态字段）。

    async def restore_user(self, session: AsyncSession, user_id: uuid.UUID) -> int:
        result = await session.execute(
            update(IamUser)
            .where(IamUser.id == user_id)
            .values(deleted_at=None, purge_after=None, deleted_by=None)
        )
        return result.rowcount or 0

    async def restore_account(self, session: AsyncSession, account_id: uuid.UUID) -> int:
        result = await session.execute(
            update(IamAccount)
            .where(IamAccount.id == account_id)
            .values(deleted_at=None, purge_after=None, deleted_by=None)
        )
        return result.rowcount or 0
