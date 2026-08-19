"""SkillService：Skill 产品 API 编排（10 §52–§64，14 号计划 §97.4）。

覆盖：

- 私有/共享列表与详情（10 §61.1/§61.2）；
- 在线创建（10 §54.1）与 SKILL.md/ZIP 上传（10 §54.2/§61.5：消费
  `me/resource-uploads`/`account/resource-uploads` upload_id，不新增
  skill-uploads 端点，P2-E3 交付后联合验证）；
- 整体更新 PUT（name 不可变，JSON 与 ZIP 均拒，10 §55.2，AC②）；
- Account 范围名称唯一（覆盖全部私有+共享未删除，冲突不泄露占用者，AC①③）；
- 软删立即释放名称、恢复唯一性复查冲突 `SKILL_NAME_CONFLICT`（10 §55.3，AC④）；
- 发布两段式（10 §58.4，AC⑤⑥）：PG 归属转换 + `platform_operation_refs
  (skill_publish)` + outbox → Worker 受控 `fs.mv` 迁移（向量 URI 重写保留
  原向量、`owner_user_id` 残留清洗、失败幂等重试）；
- 发布确认与审计（10 §58.3，AC⑧）；
- `me/skill-configs`（05 §12.5，AC⑨）：仅当前 User 读写、脱敏不返回
  可恢复 Secret、历史版本可激活。

权限由路由层（05 §12.5/§12.6 权限表）与 Subject/Account 校验双层强制；
本服务对跨 Account/越权目标统一 `EntityNotFoundError`（404 防 IDOR，10 §63）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.deletion.service import DeletionService
from openviking.server.platform.errors import (
    ConstraintViolationError,
    DeletionJobError,
    EntityNotFoundError,
    SkillInvalidFormatError,
    SkillNameConflictError,
    SkillNameImmutableError,
    SkillPublishForbiddenError,
    SkillPublishMigrationError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import IamUser, PlatformContentRef
from openviking.server.platform.provisioning.principals import SystemPrincipal
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.provisioning.service import sanitize_error
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.registry.service import (
    SKILL_NAME_UNAVAILABLE,
    ContentRegistryService,
)
from openviking.server.platform.registry.tags import normalize_tags
from openviking.server.platform.skills.configs import SkillConfigsAdapter, SkillConfigSnapshot
from openviking.server.platform.skills.control_plane import (
    SkillContentAdapter,
    SkillMigrationAdapter,
)
from openviking.server.platform.skills.packages import (
    ParsedSkill,
    SkillPackageAdapter,
    parse_skill_md_text,
)

# `validate_skill_name` 属引擎模块，延迟导入规避 queuefs 循环导入
# （与 packages.py 同策略）。

EVENT_SKILL_PUBLISH = "skill.publish"
SKILL_PUBLISH_COMPONENT = "skill.publish.worker"

OBJECT_SKILL = "skill"
SKILL_SOURCE_ONLINE = "online"
SKILL_SOURCE_SKILL_MD = "skill_md"
SKILL_SOURCE_ZIP = "zip"

UPLOAD_OBJECT_TYPE_RESOURCE = "resource"


@dataclass(frozen=True)
class PublishResult:
    """发布请求结果（10 §58.4：操作进入异步迁移，状态由 Operation 状态机承载）。"""

    skill_id: uuid.UUID
    operation_id: uuid.UUID
    status: str


def skill_private_uri(ov_user_id: str, name: str) -> str:
    return f"viking://user/{ov_user_id}/skills/{name}"


def skill_shared_uri(name: str) -> str:
    return f"viking://agent/skills/{name}"


def _validate_skill_name(name: str) -> str:
    from openviking.utils.skill_processor import validate_skill_name as _validate

    return _validate(name)


class SkillService:
    """Skill 产品服务（Repository/Registry 事务由调用方控制）。"""

    def __init__(
        self,
        *,
        iam: IamRepository | None = None,
        registry: ContentRegistryService | None = None,
        store: RegistryRepository | None = None,
        deletion: DeletionService | None = None,
        packages: SkillPackageAdapter | None = None,
        content: SkillContentAdapter | None = None,
        migration: SkillMigrationAdapter | None = None,
        configs: SkillConfigsAdapter | None = None,
        outbox: ProvisioningRepository | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._iam = iam or IamRepository()
        self._registry = registry or ContentRegistryService()
        self._store = store or RegistryRepository()
        self._deletion = deletion
        self._packages = packages
        self._content = content
        self._migration = migration
        self._configs = configs
        self._outbox = outbox or ProvisioningRepository()
        self._config = config

    # ── 列表 / 详情（10 §61.1/§61.2，AC：列表只返回 active）──

    async def list_skills(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        visibility: str,
        owner_user_id: uuid.UUID | None = None,
    ) -> list[PlatformContentRef]:
        return await self._store.list_refs(
            session,
            account_id=account_id,
            object_type=OBJECT_SKILL,
            visibility=visibility,
            owner_user_id=owner_user_id,
            status="active",
        )

    async def get_skill(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        skill_id: uuid.UUID,
        visibility: str,
        owner_user_id: uuid.UUID | None = None,
    ) -> PlatformContentRef:
        """详情读取：visibility/owner 与 Account 严格匹配，否则 404（防 IDOR）。"""
        ref = await self._store.get_ref(session, skill_id)
        if (
            ref is None
            or ref.object_type != OBJECT_SKILL
            or ref.account_id != account_id
            or ref.visibility != visibility
            or ref.deleted_at is not None
            or ref.status != "active"
        ):
            raise EntityNotFoundError(f"skill {skill_id} not visible")
        if owner_user_id is not None and ref.owner_user_id != owner_user_id:
            raise EntityNotFoundError(f"skill {skill_id} not visible")
        return ref

    # ── 在线创建（10 §54.1，AC①③）──

    async def create_online(
        self,
        session: AsyncSession,
        *,
        actor,
        name: str,
        description: str,
        tags: list | None,
        allowed_tools: list | None,
        content: str,
        visibility: str,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> PlatformContentRef:
        parsed = self._validate_online_payload(
            name=name, description=description, tags=tags, allowed_tools=allowed_tools, content=content
        )
        uri = self._target_uri(actor, parsed.name, visibility)
        ref = await self._begin_skill_create(
            session,
            actor=actor,
            parsed=parsed,
            uri=uri,
            visibility=visibility,
            source_type=SKILL_SOURCE_ONLINE,
            idempotency_key=idempotency_key,
        )
        if ref.status == "active":
            return ref
        operation = await self._create_import_operation(session, actor, ref)
        try:
            await self._content.add(uri=uri, skill_md=parsed.to_skill_md())
            await self._registry.activate(
                session, ref.id, actor_user_id=actor.actor_user_id, generation=1, operation_id=operation.id
            )
            await self._registry.finish_operation(session, operation.id, status="succeeded")
            await self._audit_skill(
                session,
                actor=actor,
                ref=ref,
                action="skill.create",
                request_id=request_id,
                metadata={"source_type": SKILL_SOURCE_ONLINE},
            )
            await session.refresh(ref)
            return ref
        except Exception as exc:
            await self._fail_import(session, operation.id, ref.id, exc)
            raise

    # ── 上传创建（10 §54.2/§61.5：消费 upload_id，不新增 skill-uploads 端点）──

    async def create_from_upload(
        self,
        session: AsyncSession,
        *,
        actor,
        name: str,
        upload_id: uuid.UUID,
        visibility: str,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> PlatformContentRef:
        uri = self._target_uri(actor, _validate_skill_name(name), visibility)
        ref = await self._begin_skill_create(
            session,
            actor=actor,
            parsed=ParsedSkill(name=_validate_skill_name(name), description="", content=""),
            uri=uri,
            visibility=visibility,
            source_type=SKILL_SOURCE_SKILL_MD,
            idempotency_key=idempotency_key,
        )
        if ref.status == "active":
            return ref
        operation = await self._create_import_operation(session, actor, ref)
        parsed = await self._consume_and_parse_upload(
            session, actor=actor, upload_id=upload_id, visibility=visibility,
            operation_id=operation.id,
        )
        if parsed.name != ref.canonical_name:
            await self._fail_import(session, operation.id, ref.id, SkillInvalidFormatError("PACKAGE_NAME_MISMATCH"))
            raise SkillInvalidFormatError("PACKAGE_NAME_MISMATCH")
        source_type = SKILL_SOURCE_ZIP if parsed.auxiliary_files else SKILL_SOURCE_SKILL_MD
        ref.source_type = source_type
        ref.description = parsed.description
        ref.tags = parsed.tags
        try:
            await self._content.add(
                uri=uri, skill_md=parsed.to_skill_md(), auxiliary_files=parsed.auxiliary_files
            )
            await self._registry.activate(
                session, ref.id, actor_user_id=actor.actor_user_id, generation=1, operation_id=operation.id
            )
            await self._registry.finish_operation(session, operation.id, status="succeeded")
            await self._audit_skill(
                session,
                actor=actor,
                ref=ref,
                action="skill.create",
                request_id=request_id,
                metadata={"source_type": source_type},
            )
            await session.refresh(ref)
            return ref
        except Exception as exc:
            await self._fail_import(session, operation.id, ref.id, exc)
            raise

    # ── 整体更新（10 §55.2/§56，AC②④：name 不可变）──

    async def update_online(
        self,
        session: AsyncSession,
        *,
        actor,
        ref: PlatformContentRef,
        description: str,
        tags: list | None,
        allowed_tools: list | None,
        content: str,
        request_id: str | None = None,
    ) -> PlatformContentRef:
        parsed = self._validate_online_payload(
            name=ref.canonical_name,
            description=description,
            tags=tags,
            allowed_tools=allowed_tools,
            content=content,
        )
        ref.description = parsed.description
        ref.tags = parsed.tags
        ref.updated_by_actor_user_id = actor.actor_user_id
        await self._store.update_ref(session, ref)
        await self._content.replace(uri=ref.ov_uri, skill_md=parsed.to_skill_md())
        await self._registry.sync_tags_outbox(session, ref_id=ref.id, tags=parsed.tags)
        await self._audit_skill(
            session, actor=actor, ref=ref, action="skill.update", request_id=request_id
        )
        await session.refresh(ref)
        return ref

    async def replace_from_upload(
        self,
        session: AsyncSession,
        *,
        actor,
        ref: PlatformContentRef,
        upload_id: uuid.UUID,
        request_id: str | None = None,
    ) -> PlatformContentRef:
        """ZIP 整体替换（10 §56.2）：新包 `name` 必须等于当前名称，否则
        `SKILL_NAME_IMMUTABLE`（AC②）。"""
        visibility = ref.visibility
        operation = await self._create_import_operation(session, actor, ref)
        parsed = await self._consume_and_parse_upload(
            session, actor=actor, upload_id=upload_id, visibility=visibility,
            operation_id=operation.id,
        )
        if parsed.name != ref.canonical_name:
            raise SkillNameImmutableError()
        ref.description = parsed.description
        ref.tags = parsed.tags
        ref.source_type = SKILL_SOURCE_ZIP if parsed.auxiliary_files else SKILL_SOURCE_SKILL_MD
        ref.updated_by_actor_user_id = actor.actor_user_id
        operation = await self._create_import_operation(session, actor, ref)
        try:
            await self._content.replace(
                uri=ref.ov_uri, skill_md=parsed.to_skill_md(), auxiliary_files=parsed.auxiliary_files
            )
            await self._store.update_ref(session, ref)
            await self._registry.sync_tags_outbox(session, ref_id=ref.id, tags=parsed.tags)
            await self._registry.finish_operation(session, operation.id, status="succeeded")
            await self._audit_skill(
                session,
                actor=actor,
                ref=ref,
                action="skill.update",
                request_id=request_id,
                metadata={"source_type": ref.source_type},
            )
            await session.refresh(ref)
            return ref
        except Exception as exc:
            await self._registry.finish_operation(
                session, operation.id, status="failed", error_code="SKILL_IMPORT_FAILED",
                error_summary=sanitize_error(exc), retryable=False,
            )
            raise

    # ── 软删除 / 恢复（10 §55.3/§59，AC④：软删立即释放名称、恢复冲突保持删除）──

    async def soft_delete(
        self,
        session: AsyncSession,
        *,
        actor,
        ref: PlatformContentRef,
        request_id: str | None = None,
    ) -> dict:
        """软删除（10 §55.3/§59，AC④）：立即释放名称。

        `(account_id, ov_uri)` 全量唯一约束（04 §10.10）使删除记录仍占用原
        URI；因此软删时把 `ov_uri` 改写为 tombstone
        （`viking://recycled/skills/{id}`，internal 根不可达），原 URI 存入
        deletion job——恢复时从 job 还原原 URI 并复查占用；Purge 按 job 的
        原 URI 清理（04 §10.11）。
        """
        now = datetime.now(timezone.utc)
        original_uri = ref.ov_uri
        job = await self._registry.create_deletion_job(
            session,
            account_id=ref.account_id,
            resource_type=OBJECT_SKILL,
            resource_id=str(ref.id),
            deleted_by=actor.actor_user_id,
            now=now,
            ov_uri=original_uri,
        )
        ref.status = "pending_deletion"
        ref.deleted_at = now
        ref.ov_uri = f"viking://recycled/skills/{ref.id}"
        ref.updated_by_actor_user_id = actor.actor_user_id
        await self._store.update_ref(session, ref)
        await self._audit_skill(
            session,
            actor=actor,
            ref=ref,
            action="skill.delete",
            request_id=request_id,
            metadata={"deletion_job_id": str(job.id), "purge_after": job.purge_after.isoformat()},
        )
        return {
            "resource_type": OBJECT_SKILL,
            "resource_id": str(ref.id),
            "deletion_job_id": str(job.id),
            "deleted_at": now.isoformat(),
            "restore_until": job.purge_after.isoformat(),
        }

    async def restore(
        self,
        session: AsyncSession,
        *,
        actor,
        ref: PlatformContentRef,
        scope: str,
        request_id: str | None = None,
    ) -> dict:
        """恢复（10 §55.3/§59，AC④）：恢复前重查 Account 范围名称唯一，
        冲突 `SKILL_NAME_CONFLICT` 保持删除状态不改名不覆盖。"""
        if self._deletion is None:
            raise DeletionJobError("NOT_RESTORABLE")
        job = await self._store.get_active_deletion_job(session, OBJECT_SKILL, str(ref.id))
        if job is None:
            raise DeletionJobError("NOT_RESTORABLE")
        result = await self._deletion.restore(
            session,
            scope=scope,
            principal=actor,
            account_id=ref.account_id,
            job_id=job.id,
            request_id=request_id,
        )
        return {
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "deletion_job_id": str(result.deletion_job_id),
            "deleted_at": result.deleted_at,
            "restore_until": result.restore_until,
        }

    # ── 发布两段式（10 §58.1–§58.4，AC⑤⑥⑦⑧）──

    async def publish(
        self,
        session: AsyncSession,
        *,
        actor,
        target_user_id: uuid.UUID,
        skill_id: uuid.UUID,
        request_id: str | None = None,
    ) -> PublishResult:
        """发布（10 §58.4 步骤 1–2）：同一 PG 事务完成归属转换 +
        `platform_operation_refs(skill_publish)` + outbox 事件 + 审计。

        前置检查（步骤 1）：Skill 为 active 未删除、成员私有；目标共享根
        `viking://agent/skills/{name}` 在 PostgreSQL 与磁盘上均无同名占用。
        物理迁移由发布 Worker（SystemPrincipal）异步执行（步骤 3–4）。
        """
        ref = await self._store.get_ref(session, skill_id)
        if (
            ref is None
            or ref.object_type != OBJECT_SKILL
            or ref.account_id != actor.actor_account_id
            or ref.deleted_at is not None
            or ref.status != "active"
        ):
            raise EntityNotFoundError(f"skill {skill_id} not visible")
        if ref.visibility != "user_private" or ref.owner_user_id != target_user_id:
            raise SkillPublishForbiddenError()

        holder = await self._store.find_skill_by_canonical_name(
            session, ref.account_id, ref.canonical_name
        )
        if holder is not None and holder.id != ref.id:
            raise SkillNameConflictError()
        to_uri = skill_shared_uri(ref.canonical_name)
        if await self._migration.target_exists(to_uri):
            # 磁盘侧复查（10 §58.4 步骤 1：防事务外变更；不泄露占用者）
            raise SkillNameConflictError()

        from_uri = ref.ov_uri
        now = datetime.now(timezone.utc)
        ref.visibility = "account_shared"
        ref.owner_user_id = None
        ref.ov_uri = to_uri
        ref.updated_by_actor_user_id = actor.actor_user_id
        await self._store.update_ref(session, ref)

        operation = await self._registry.create_operation(
            session,
            account_id=ref.account_id,
            operation_kind="task",
            operation_type="skill_publish",
            ov_operation_id=str(uuid.uuid4()),
            target_type=OBJECT_SKILL,
            target_id=str(ref.id),
            target_visibility="account_shared",
            owner_user_id=None,
            actor_user_id=actor.actor_user_id,
            generation=ref.active_generation,
            cancellable=False,
        )
        await self._outbox.enqueue(
            session,
            event_type=EVENT_SKILL_PUBLISH,
            aggregate_id=ref.id,
            payload={
                "content_ref_id": str(ref.id),
                "operation_id": str(operation.id),
                "account_id": str(ref.account_id),
                "skill_name": ref.canonical_name,
                "from_uri": from_uri,
                "to_uri": to_uri,
                "owner_user_id": str(target_user_id),
            },
            now=now,
        )
        await self._audit_skill(
            session,
            actor=actor,
            ref=ref,
            action="skill.publish",
            request_id=request_id,
            subject_user_id=target_user_id,
            metadata={
                "operation_id": str(operation.id),
                "visibility_from": "user_private",
                "visibility_to": "account_shared",
            },
        )
        return PublishResult(skill_id=ref.id, operation_id=operation.id, status="pending")

    async def process_publish_event(self, session: AsyncSession, event) -> None:
        """发布 Worker 事件处理（10 §58.4 步骤 3–4，SystemPrincipal）。

        - 再次校验目标共享根无同名；复用受控 `fs.mv` 完成整目录迁移
          （文件树复制 + 向量 URI/ID 重写保留原向量 + 源目录删除）；
        - 清洗向量记录残留 `owner_user_id`；
        - 成功：Operation `succeeded`，Content Ref 保持 `active`；
        - 失败：Operation `failed`（瞬时错误可重试，幂等），抛出让 outbox
          退避重试；不允许"已转换未迁移/已迁移未转换"持久不一致。
        """
        payload = event.payload or {}
        ref_id = uuid.UUID(str(payload.get("content_ref_id")))
        operation_id = uuid.UUID(str(payload.get("operation_id")))
        from_uri = str(payload.get("from_uri"))
        to_uri = str(payload.get("to_uri"))
        principal = SystemPrincipal(component=SKILL_PUBLISH_COMPONENT, task_id=str(event.id))
        ref = await self._store.get_ref(session, ref_id)
        if ref is None:
            # 目标不存在/已物理清理：无事可做（幂等完成）
            return

        try:
            # 再次校验目标共享根无同名（10 §58.4 步骤 3）
            if await self._migration.target_exists(to_uri):
                raise SkillPublishMigrationError(
                    "SKILL_PUBLISH_TARGET_CONFLICT", retryable=False,
                )
            await self._migration.migrate(from_uri=from_uri, to_uri=to_uri)
            await self._migration.clean_owner_user_id(uri=to_uri)
        except Exception as exc:  # noqa: BLE001
            error = sanitize_error(exc)
            if isinstance(exc, SkillPublishMigrationError):
                retryable = exc.retryable
                code = exc.code
            else:
                retryable = True
                code = "SKILL_PUBLISH_MIGRATION_FAILED"
            await self._registry.finish_operation(
                session,
                operation_id,
                status="failed",
                stage="migrating",
                error_code=code,
                error_summary=error,
                retryable=retryable,
            )
            await self._append_system_audit(
                session,
                principal=principal,
                ref=ref,
                action="skill.publish.migrate",
                result="failed",
                reason=error,
                operation_id=operation_id,
            )
            raise
        await self._registry.finish_operation(
            session, operation_id, status="succeeded", stage="finalized"
        )
        await self._append_system_audit(
            session,
            principal=principal,
            ref=ref,
            action="skill.publish.migrate",
            result="success",
            operation_id=operation_id,
        )

    # ── me/skill-configs（05 §12.5 三接口，AC⑨）──

    async def get_skill_config(
        self, session: AsyncSession, *, actor, skill_ref: PlatformContentRef
    ) -> SkillConfigSnapshot:
        snapshot = await self._configs.get(
            owner_ov_user_id=actor.actor_ov_user_id, skill_id=str(skill_ref.id)
        )
        if snapshot is None:
            return SkillConfigSnapshot(skill_id=str(skill_ref.id), configured=False,
                                       active_version=None, latest_version=None)
        return snapshot

    async def put_skill_config(
        self,
        session: AsyncSession,
        *,
        actor,
        skill_ref: PlatformContentRef,
        values: dict,
        change_reason: str = "",
    ) -> SkillConfigSnapshot:
        return await self._configs.upsert(
            owner_ov_user_id=actor.actor_ov_user_id,
            skill_id=str(skill_ref.id),
            values=values,
            updated_by=str(actor.actor_user_id),
            change_reason=change_reason,
        )

    async def activate_skill_config(
        self, session: AsyncSession, *, actor, skill_ref: PlatformContentRef, version: int
    ) -> SkillConfigSnapshot:
        return await self._configs.activate(
            owner_ov_user_id=actor.actor_ov_user_id,
            skill_id=str(skill_ref.id),
            version=version,
            updated_by=str(actor.actor_user_id),
        )

    # ── Subject 校验工具（admin/platform 路由复用，404 防枚举）──

    async def require_user_in_account(
        self, session: AsyncSession, *, account_id: uuid.UUID, user_id: uuid.UUID
    ) -> IamUser:
        user = await self._iam.get_user(session, user_id)
        if user is None or user.deleted_at is not None or user.account_id != account_id:
            raise EntityNotFoundError(f"user {user_id} not visible")
        return user

    async def require_account(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        account = await self._iam.get_account(session, account_id)
        if account is None or account.deleted_at is not None:
            raise EntityNotFoundError(f"account {account_id} not visible")

    async def get_deleted_skill_for_restore(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        skill_id: uuid.UUID,
        visibility: str,
        owner_user_id: uuid.UUID | None = None,
    ) -> PlatformContentRef:
        """恢复目标加载（10 §55.3）：只接受处于删除回收期、Account/owner 匹配
        的 Skill；未删除/他人/跨 Account → 404（防 IDOR）。"""
        ref = await self._store.get_ref(session, skill_id)
        if (
            ref is None
            or ref.object_type != OBJECT_SKILL
            or ref.account_id != account_id
            or ref.visibility != visibility
            or ref.deleted_at is None
        ):
            raise EntityNotFoundError(f"skill {skill_id} not visible")
        if owner_user_id is not None and ref.owner_user_id != owner_user_id:
            raise EntityNotFoundError(f"skill {skill_id} not visible")
        return ref

    # ── 详情内容（10 §53.3：使用说明渲染 + 工具范围 + 文件清单）──

    async def read_skill_content(self, session: AsyncSession, *, uri: str) -> dict | None:
        """读取 `SKILL.md` 文本、`allowed-tools` 与文件清单（详情页数据源）。"""
        if self._content is None:
            return None
        raw = await self._content.read(uri=uri)
        if raw is None:
            return None
        result = {"skill_md": raw["skill_md"], "files": raw["files"], "allowed_tools": []}
        try:
            parsed = parse_skill_md_text(raw["skill_md"])
            result["allowed_tools"] = parsed.allowed_tools
        except Exception:  # noqa: BLE001 - 正文解析失败不回退详情（低层为事实来源）
            pass
        return result

    # ── 内部工具 ──

    def _target_uri(self, actor, name: str, visibility: str) -> str:
        if visibility == "user_private":
            if actor.actor_ov_user_id is None:
                raise EntityNotFoundError(f"user {actor.actor_user_id} not visible")
            return skill_private_uri(actor.actor_ov_user_id, name)
        return skill_shared_uri(name)

    def _validate_online_payload(
        self,
        *,
        name: str,
        description: str,
        tags: list | None,
        allowed_tools: list | None,
        content: str,
    ) -> ParsedSkill:
        normalized_name = _validate_skill_name(name)
        description = (description or "").strip()
        if not description:
            raise SkillInvalidFormatError("DESCRIPTION_REQUIRED")
        if len(description) > 1024:
            raise SkillInvalidFormatError("DESCRIPTION_TOO_LONG")
        try:
            normalized_tags = normalize_tags(tags)
        except ValueError as exc:
            raise SkillInvalidFormatError("TAGS_INVALID") from exc
        allowed_tools = [str(t) for t in (allowed_tools or []) if str(t)]
        if len(allowed_tools) > 50 or any(len(t) > 128 for t in allowed_tools):
            raise SkillInvalidFormatError("ALLOWED_TOOLS_INVALID")
        return ParsedSkill(
            name=normalized_name,
            description=description,
            tags=normalized_tags,
            allowed_tools=allowed_tools,
            content=content or "",
        )

    async def _begin_skill_create(
        self,
        session: AsyncSession,
        *,
        actor,
        parsed: ParsedSkill,
        uri: str,
        visibility: str,
        source_type: str,
        idempotency_key: str | None,
    ) -> PlatformContentRef:
        try:
            return await self._registry.begin_create(
                session,
                account_id=actor.actor_account_id,
                object_type=OBJECT_SKILL,
                visibility=visibility,
                owner_user_id=actor.actor_user_id if visibility == "user_private" else None,
                actor_user_id=actor.actor_user_id,
                ov_uri=uri,
                canonical_name=parsed.name,
                description=parsed.description,
                tags=parsed.tags,
                idempotency_key=idempotency_key,
                source_type=source_type,
                source_display=None,
            )
        except ConstraintViolationError as exc:
            if str(exc) == SKILL_NAME_UNAVAILABLE:
                raise SkillNameConflictError() from exc
            raise

    async def _consume_and_parse_upload(
        self,
        session: AsyncSession,
        *,
        actor,
        upload_id: uuid.UUID,
        visibility: str,
        operation_id: uuid.UUID,
    ) -> ParsedSkill:
        """消费 upload_id（10 §61.5，04 §10.14）：绑定 Actor/Account/目标入口
        的 `ready → consumed` 原子消费后取包解析。

        object_type 传 `resource`（04 §10.14 v0.1 upload 由
        `me/resource-uploads`/`account/resource-uploads` 生成，P2-E3 交付；
        消费契约按 10 §61.5 实现，待 P2-E3 联合验证）。"""
        upload = await self._registry.consume_upload(
            session,
            upload_id=upload_id,
            account_id=actor.actor_account_id,
            actor_user_id=actor.actor_user_id,
            visibility=visibility,
            object_type=UPLOAD_OBJECT_TYPE_RESOURCE,
            operation_id=operation_id,
        )
        return self._packages.parse(upload.storage_ref)

    async def _create_import_operation(
        self, session: AsyncSession, actor, ref: PlatformContentRef
    ) -> object:
        return await self._registry.create_operation(
            session,
            account_id=ref.account_id,
            operation_kind="task",
            operation_type="skill_import",
            ov_operation_id=str(uuid.uuid4()),
            target_type=OBJECT_SKILL,
            target_id=str(ref.id),
            target_visibility=ref.visibility,
            owner_user_id=ref.owner_user_id,
            actor_user_id=actor.actor_user_id,
            generation=ref.active_generation + 1,
            cancellable=False,
        )

    async def _fail_import(self, session: AsyncSession, operation_id: uuid.UUID, ref_id: uuid.UUID, exc: Exception) -> None:
        await self._registry.finish_operation(
            session,
            operation_id,
            status="failed",
            error_code="SKILL_IMPORT_FAILED",
            error_summary=sanitize_error(exc),
            retryable=False,
        )
        try:
            await self._registry.fail(session, ref_id)
        except Exception:  # noqa: BLE001
            pass

    async def _audit_skill(
        self,
        session: AsyncSession,
        *,
        actor,
        ref: PlatformContentRef,
        action: str,
        request_id: str | None = None,
        subject_user_id: uuid.UUID | None = None,
        metadata: dict | None = None,
    ) -> None:
        await self._iam.append_audit_event(
            session,
            request_id=request_id,
            account_id=ref.account_id,
            actor_type="user",
            actor_user_id=actor.actor_user_id,
            actor_account_id=actor.actor_account_id,
            actor_session_id=actor.session_id,
            authentication_method=actor.authentication_method,
            subject_account_id=ref.account_id,
            subject_user_id=subject_user_id,
            action=action,
            target_type="platform_content_refs",
            target_id=str(ref.id),
            target_visibility=ref.visibility,
            scope="account",
            result="success",
            metadata=metadata,
        )

    async def _append_system_audit(
        self,
        session: AsyncSession,
        *,
        principal: SystemPrincipal,
        ref: PlatformContentRef,
        action: str,
        result: str,
        reason: str | None = None,
        operation_id: uuid.UUID | None = None,
    ) -> None:
        await self._iam.append_audit_event(
            session,
            account_id=ref.account_id,
            actor_type="system",
            actor_system_component=principal.component,
            authentication_method="system",
            subject_account_id=ref.account_id,
            action=action,
            target_type="platform_content_refs",
            target_id=str(ref.id),
            scope="platform",
            result=result,
            reason=reason,
            metadata={"operation_id": str(operation_id)} if operation_id else None,
        )
