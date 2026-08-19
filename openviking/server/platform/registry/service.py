"""ContentRegistryService（04 §10.10/§10.12/§10.14，14 号计划 §97.2）。

对外创建/编辑/搜索的注册表编排（05 §11.5）：

- **对外创建**：先按 `Idempotency-Key` 建立 `platform_content_refs
  (status=provisioning)`，再调用 OpenViking（由调用方/Product Facade
  注入控制面适配器），成功后写入 canonical URI 并置 `active`；失败置
  `failed` 并由 Reconciler 清理或重试（AC⑤⑦）；
- **列表**：只返回 `active` 引用（AC⑤）；
- **Skill 名称唯一**：Account 内未删除 Skill `canonical_name` 全局唯一
  （DB 部分唯一索引强制，AC⑥）；冲突响应只说明"名称不可用"，不泄露占用者；
- **tags**：统一校验（04 §10.10），PostgreSQL 写入与 OpenViking
  `search_tags` 的同步走 Outbox/Reconciler（本 Epic 交付 Outbox 事件入队，
  事件消费由 P2-E1 Worker 机制扩展；05 §12.5 禁止双写分叉）；
- **Operation**：`platform_operation_refs` 建立/状态更新，`generation`
  防旧任务覆盖（04 §10.12）；
- **Upload**：15 分钟过期、`ready → consumed` 原子消费、跨 Scope 拒绝
  （04 §10.14，AC⑦）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.errors import (
    ConstraintViolationError,
    EntityNotFoundError,
    TagValidationError,
    UploadNotConsumableError,
)
from openviking.server.platform.models import (
    IamDeletionJob,
    PlatformContentRef,
    PlatformOperationRef,
    PlatformUpload,
)
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.registry.tags import normalize_tags

EVENT_TAGS_SYNC = "content.tags_sync"
EVENT_CONTENT_ACTIVATED = "content.activated"

OBJECT_RESOURCE = "resource"
OBJECT_SKILL = "skill"

SKILL_NAME_UNAVAILABLE = "SKILL_NAME_UNAVAILABLE"


class ContentRegistryService:
    """产品对象注册表编排（骨架冻结层：E3–E5 在其上增量扩展，不修改本模块）。"""

    def __init__(
        self,
        repo: RegistryRepository | None = None,
        outbox: ProvisioningRepository | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._store = repo or RegistryRepository()
        self._outbox = outbox or ProvisioningRepository()
        self._config = config

    # ── 对外创建：provisioning → active/failed（04 §10.10，AC⑤⑦）──

    async def begin_create(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        object_type: str,
        visibility: str,
        owner_user_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        ov_uri: str,
        canonical_name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        tags: list | tuple | None = None,
        idempotency_key: str | None = None,
        source_type: str | None = None,
        source_display: str | None = None,
        source_fingerprint: str | None = None,
    ) -> PlatformContentRef:
        """先建 `provisioning` 引用（Idempotency-Key 事务，AC⑤⑦）。

        同一 `(account_id, object_type, idempotency_key)` 重复提交返回
        既有引用（不产生重复对象，AC⑦）；`status=provisioning/failed` 可
        重试复用，`active` 重复返回同一引用（幂等语义由调用方 DTO 承载）。

        Skill 创建前置检查名称唯一（AC⑥）；DB 部分唯一索引兜底。
        """
        normalized_tags = normalize_tags(tags)

        if object_type == OBJECT_SKILL:
            if not canonical_name:
                raise TagValidationError("SKILL_NAME_REQUIRED")
            existing = await self._store.find_skill_by_canonical_name(
                session, account_id, canonical_name
            )
            if existing is not None:
                raise ConstraintViolationError(SKILL_NAME_UNAVAILABLE)
            display_name = canonical_name

        if idempotency_key:
            existing = await self._store.get_ref_by_idempotency_key(
                session, account_id, object_type, idempotency_key
            )
            if existing is not None:
                return existing

        if visibility == "user_private" and owner_user_id is None:
            raise ConstraintViolationError("OWNER_REQUIRED")
        if visibility == "account_shared" and owner_user_id is not None:
            raise ConstraintViolationError("OWNER_FORBIDDEN")

        ref = PlatformContentRef(
            account_id=account_id,
            object_type=object_type,
            visibility=visibility,
            owner_user_id=owner_user_id,
            ov_uri=ov_uri,
            canonical_name=canonical_name,
            display_name=display_name,
            description=description,
            tags=normalized_tags,
            source_type=source_type,
            source_display=source_display,
            source_fingerprint=source_fingerprint,
            idempotency_key=idempotency_key,
            created_by_actor_user_id=actor_user_id,
            status="provisioning",
        )
        try:
            await self._store.insert_ref(session, ref)
        except ConstraintViolationError:
            # DB 约束兜底（Skill 部分唯一/ov_uri 唯一等）——rollback 由调用方
            # 按既有 repository 语义处理；冲突码不泄露占用者（AC⑥）。
            raise
        return ref

    async def activate(
        self,
        session: AsyncSession,
        ref_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        ov_uri: str | None = None,
        generation: int = 1,
        operation_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> PlatformContentRef:
        """OpenViking 调用成功 → 写入 canonical URI 并置 `active`（AC⑤）。

        `active_generation` 仅在仍是目标最新代数时更新（04 §10.10 原子切换）；
        旧任务晚到只能记录终态，不能切换内容（AC：04 §10.12 generation）。
        """
        ref = await self._store.get_ref(session, ref_id)
        if ref is None:
            raise EntityNotFoundError(f"content ref {ref_id} not found")
        if ref.status == "active":
            return ref
        if ov_uri is not None:
            ref.ov_uri = ov_uri
        ref.status = "active"
        if generation > ref.active_generation:
            ref.active_generation = generation
        ref.updated_by_actor_user_id = actor_user_id
        if operation_id is not None:
            ref.latest_operation_id = operation_id
        if now is not None:
            ref.last_processed_at = now
        await self._store.update_ref(session, ref)
        await self._outbox.enqueue(
            session,
            event_type=EVENT_CONTENT_ACTIVATED,
            aggregate_id=ref.id,
            payload={"content_ref_id": str(ref.id), "object_type": ref.object_type},
            now=now,
        )
        return ref

    async def fail(
        self,
        session: AsyncSession,
        ref_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> PlatformContentRef:
        """OpenViking 调用失败 → `failed`（由 Reconciler 清理或重试）。"""
        ref = await self._store.get_ref(session, ref_id)
        if ref is None:
            raise EntityNotFoundError(f"content ref {ref_id} not found")
        if ref.status in ("active", "pending_deletion", "deleted"):
            return ref
        ref.status = "failed"
        await self._store.update_ref(session, ref)
        return ref

    async def list_active(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        object_type: str | None = None,
        visibility: str | None = None,
        owner_user_id: uuid.UUID | None = None,
    ) -> list[PlatformContentRef]:
        """产品列表：只返回 `active` 引用（AC⑤）。"""
        return await self._store.list_refs(
            session,
            account_id=account_id,
            object_type=object_type,
            visibility=visibility,
            owner_user_id=owner_user_id,
            status="active",
        )

    async def get_active_ref(
        self,
        session: AsyncSession,
        ref_id: uuid.UUID,
    ) -> PlatformContentRef:
        """详情读取：只允许 active（软删除对象不出现在正常查询，04 §10.10）。"""
        ref = await self._store.get_ref(session, ref_id)
        if ref is None or ref.status != "active" or ref.deleted_at is not None:
            raise EntityNotFoundError(f"content ref {ref_id} not visible")
        return ref

    async def sync_tags_outbox(
        self,
        session: AsyncSession,
        *,
        ref_id: uuid.UUID,
        tags: list[str],
        now: datetime | None = None,
    ) -> None:
        """tags 同步 Outbox（04 §10.10/§10.14，05 §12.5）：PostgreSQL 提交后
        入队 `content.tags_sync`，由 Worker 同步 OpenViking `search_tags`；
        不得把自由文本静默转换成另一套内部格式。"""
        await self._outbox.enqueue(
            session,
            event_type=EVENT_TAGS_SYNC,
            aggregate_id=ref_id,
            payload={"content_ref_id": str(ref_id), "tags": list(tags)},
            now=now,
        )

    # ── platform_operation_refs（04 §10.12）──

    async def create_operation(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        operation_kind: str,
        operation_type: str,
        ov_operation_id: str,
        target_type: str | None,
        target_id: str | None,
        target_visibility: str,
        owner_user_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None,
        generation: int = 0,
        cancellable: bool = False,
    ) -> PlatformOperationRef:
        """建立 Task/Watch 与产品对象的授权索引（04 §10.12）。

        `ov_operation_id` 不返回为可枚举主 ID；产品 API 使用本表 `id`。
        """
        operation = PlatformOperationRef(
            account_id=account_id,
            operation_kind=operation_kind,
            operation_type=operation_type,
            ov_operation_id=ov_operation_id,
            target_type=target_type,
            target_id=target_id,
            target_visibility=target_visibility,
            owner_user_id=owner_user_id,
            initiated_by_actor_user_id=actor_user_id,
            generation=generation,
            cancellable=cancellable,
            status="pending",
        )
        return await self._store.insert_operation(session, operation)

    async def finish_operation(
        self,
        session: AsyncSession,
        operation_id: uuid.UUID,
        *,
        status: str,
        stage: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        retryable: bool = False,
        now: datetime | None = None,
    ) -> PlatformOperationRef:
        """操作终态（04 §10.12）：旧 Operation 完成时只有其 `generation` 仍
        是目标最新待处理代数才允许切换内容（由调用方在 activate 前校验）。"""
        now = now or datetime.now(timezone.utc)
        fields: dict = {"status": status}
        if stage is not None:
            fields["stage"] = stage
        if error_code is not None:
            fields["error_code"] = error_code
        if error_summary is not None:
            fields["error_summary"] = error_summary
        fields["retryable"] = retryable
        if status in ("succeeded", "failed", "cancelled"):
            fields["completed_at"] = now
        return await self._store.update_operation(session, operation_id, **fields)

    # ── platform_uploads（04 §10.14）──

    async def create_upload(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        target_visibility: str,
        owner_user_id: uuid.UUID | None,
        object_type: str,
        storage_ref: str,
        original_filename: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        content_hash: str | None = None,
        ttl_minutes: int | None = None,
        now: datetime | None = None,
    ) -> PlatformUpload:
        """创建 Upload ID（04 §10.14）：`expires_at` 默认 15 分钟。

        私有 Upload 只能由相同 Actor 消费；共享 Upload 只能由相同 Account、
        仍有共享写权限的管理入口消费（消费时二次校验）。
        """
        now = now or datetime.now(timezone.utc)
        ttl = ttl_minutes if ttl_minutes is not None else self._config.upload_ttl_minutes
        upload = PlatformUpload(
            account_id=account_id,
            actor_user_id=actor_user_id,
            target_visibility=target_visibility,
            owner_user_id=owner_user_id,
            object_type=object_type,
            storage_ref=storage_ref,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_hash=content_hash,
            status="ready",
            expires_at=now + timedelta(minutes=ttl),
        )
        return await self._store.insert_upload(session, upload)

    async def consume_upload(
        self,
        session: AsyncSession,
        *,
        upload_id: uuid.UUID,
        account_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        visibility: str,
        object_type: str,
        operation_id: uuid.UUID,
        now: datetime | None = None,
    ) -> PlatformUpload:
        """消费 Upload：原子 `ready → consumed`（04 §10.14，AC⑦）。

        跨 User/Account/Visibility/Object Type 消费、过期、已消费、重放
        统一拒绝（`UploadNotConsumableError`，即使知道 ID 也返回拒绝，
        不泄露存在性）。
        """
        now = now or datetime.now(timezone.utc)
        upload = await self._store.get_upload(session, upload_id)
        if upload is None:
            raise UploadNotConsumableError("UPLOAD_NOT_FOUND")
        if upload.status == "consumed":
            raise UploadNotConsumableError("UPLOAD_CONSUMED")
        if upload.expires_at <= now:
            raise UploadNotConsumableError("UPLOAD_EXPIRED")
        if upload.status != "ready":
            raise UploadNotConsumableError("UPLOAD_NOT_READY")
        if (
            upload.account_id != account_id
            or upload.object_type != object_type
            or upload.target_visibility != visibility
        ):
            raise UploadNotConsumableError("UPLOAD_SCOPE_MISMATCH")
        if upload.target_visibility == "user_private" and upload.actor_user_id != actor_user_id:
            raise UploadNotConsumableError("UPLOAD_SCOPE_MISMATCH")

        consumed = await self._store.consume_upload_atomic(
            session,
            upload_id=upload_id,
            consumed_by_operation_id=operation_id,
            now=now,
        )
        if consumed is None:
            # 并发消费竞态：另一请求已消费
            raise UploadNotConsumableError("UPLOAD_CONSUMED")
        return consumed

    async def expire_uploads(self, session: AsyncSession, now: datetime | None = None) -> int:
        """到期 Upload 状态转换（幂等；临时字节清理由 Cleanup Worker）。"""
        now = now or datetime.now(timezone.utc)
        return await self._store.expire_uploads(session, now)

    # ── 删除任务（04 §10.11 公共基础设施）──

    async def create_deletion_job(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        resource_type: str,
        resource_id: str,
        deleted_by: uuid.UUID,
        now: datetime | None = None,
        ov_uri: str | None = None,
    ) -> IamDeletionJob:
        """建立删除任务（幂等，AC⑧）：`purge_after` 默认 30 天后物理清理。

        同一 `(resource_type, resource_id)` 已有未恢复任务时复用（重试删除
        不产生重复任务）。
        """
        now = now or datetime.now(timezone.utc)
        existing = await self._store.get_active_deletion_job(session, resource_type, resource_id)
        if existing is not None:
            return existing
        job = IamDeletionJob(
            account_id=account_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ov_uri=ov_uri,
            deleted_by=deleted_by,
            deleted_at=now,
            purge_after=now + timedelta(days=self._config.deletion_purge_days),
            status="pending",
        )
        return await self._store.insert_deletion_job(session, job)
