"""ResourceService（09 §36–§48 全生命周期产品 API 编排）。

入口范围（09 §40.1/§46）：`me`（user_private）/`account`（account_shared）/
`platform`（平台代管 account_shared）/`member`（admin/platform 成员只读）。

关键不变量：

- 目标固定（AC①）：me 固定 Actor 私有根；account/platform 固定共享根；
  共享导入必须经 `/account/*` 或 `/platform/accounts/{id}/*` 并持对应写权限；
- 删除六步事务（09 §45.2，AC②）：pending_deletion + deletion_jobs +
  Watch 暂停 + 协作取消 + 列表/Search 排除；物理清理仅 30 天后由
  Purge Worker 执行（ResourcePurgeHandler 接线）；
- generation 防旧任务覆盖（AC③）：旧 Operation 完成只能记录终态，
  不能切换 active_generation 或复活删除中对象；
- Refresh/Watch 失败保护（AC④）：旧 active 版本保持可读、失败保持
  active 显示「最近同步失败」；首次失败保持 failed 仅管理者可见；
- 私有→共享发布复制新建（AC⑤，09 §44）：新 ID、独立 URI、原对象保留，
  Watch/私有关系/审计历史/Query 不复制；
- Watch 记录不含明文 URL（AC⑥）：来源在 `source_locator_ciphertext`，
  执行时临时解密；
- Upload 绑定主体、原子 ready→consumed（AC⑦）；跨 Scope 消费拒绝；
- Node ID 越权统一拒绝并审计（AC⑨，09 §48 #12）；
- capabilities 服务端强制（AC⑩，09 §40.5）。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.errors import (
    EntityNotFoundError,
    ResourceBusyError,
    ResourceError,
    ResourceVersionConflictError,
    UploadNotConsumableError,
)
from openviking.server.platform.iam.permissions import ACCOUNT_ADMIN
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import (
    PlatformContentRef,
    PlatformOperationRef,
    PlatformUpload,
)
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.registry.service import ContentRegistryService
from openviking.server.platform.resource.cipher import SourceLocatorCipher
from openviking.server.platform.resource.execution import (
    RESOURCE_PARSE_FAILED,
    FakeResourceExecutionPlane,
    IngestOutcome,
    NodeRecord,
    ResourceExecutionPlane,
)
from openviking.server.platform.resource.nodes import (
    MAX_CHILDREN_PER_PAGE,
    NodeIdCodec,
    safe_download_filename,
)
from openviking.server.platform.resource.repository import ResourceRepository
from openviking.server.platform.resource.security import (
    RemoteSourcePolicy,
    is_archive_filename,
    is_executable_extension,
)
from openviking.server.platform.resource.storage import (
    MemoryTempUploadStore,
    TempUploadStore,
    UploadedBlob,
    build_blob,
)
from openviking.server.platform.resource.watch import WATCH_SCHEDULER_COMPONENT

VIS_USER_PRIVATE = "user_private"
VIS_ACCOUNT_SHARED = "account_shared"
OBJECT_RESOURCE = "resource"

_PREVIEW_BYTES = 64 * 1024
_INLINE_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
)


@dataclass(frozen=True)
class ResourceScope:
    """请求入口确定的数据范围（09 §40.1：路由+权限确定，客户端不能切换）。"""

    visibility: str
    account_id: uuid.UUID
    owner_user_id: uuid.UUID | None
    kind: str  # me | account | platform | member(admin/platform 成员只读)


class ResourceService:
    """Resource 全生命周期编排（P2-E3 新增模块，不触碰 P2-E2 冻结点）。"""

    def __init__(
        self,
        *,
        facade,
        registry: ContentRegistryService,
        iam_repo: IamRepository,
        resource_store: ResourceRepository | None = None,
        execution: ResourceExecutionPlane | None = None,
        uploads: TempUploadStore | None = None,
        security: RemoteSourcePolicy | None = None,
        nodes: NodeIdCodec | None = None,
        cipher: SourceLocatorCipher | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self.facade = facade
        self.registry = registry
        self._iam = iam_repo
        self._store = resource_store or ResourceRepository()
        self._execution = execution or FakeResourceExecutionPlane()
        self._uploads = uploads or MemoryTempUploadStore()
        self._security = security or RemoteSourcePolicy(http_allowed=config.http_sources_allowed)
        self._nodes = nodes or NodeIdCodec(config.node_id_secret)
        self._cipher = cipher or SourceLocatorCipher(config.source_cipher_key)
        self._config = config
        self._watch = None

    def registry_store(self) -> RegistryRepository:
        return self.facade.registry_store

    # ═══════════════════════════════════════════════════════════════════
    # Watch 配置（09 §43.2；来源稳定性/写权限在 Service 校验）
    # ═══════════════════════════════════════════════════════════════════

    def _watch_service(self):
        if self._watch is None:
            from openviking.server.platform.resource.watch import WatchService

            self._watch = WatchService(iam_repo=self._iam, store=self._store, config=self._config)
        return self._watch

    async def watch_config(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
    ) -> dict:
        ref = await self._load_visible_ref(session, scope, resource_id)
        return self._watch_service().dto(await self._store.get_watch(session, ref.id))

    async def configure_watch(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        interval_minutes: int,
        instruction: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        ref = await self._load_visible_ref(session, scope, resource_id)
        self._require_watchable_source(ref)
        watch = await self._watch_service().configure(
            session,
            principal=principal,
            ref=ref,
            interval_minutes=interval_minutes,
            instruction=instruction,
            request_id=request_id,
        )
        return self._watch_dto(watch)

    async def pause_watch(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        ref = await self._load_visible_ref(session, scope, resource_id)
        watch = await self._watch_service().pause(
            session, principal=principal, ref=ref, request_id=request_id
        )
        return self._watch_dto(watch)

    async def resume_watch(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        ref = await self._load_visible_ref(session, scope, resource_id)
        watch = await self._watch_service().resume(
            session, principal=principal, ref=ref, request_id=request_id
        )
        return self._watch_dto(watch)

    async def delete_watch(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        ref = await self._load_visible_ref(session, scope, resource_id)
        await self._watch_service().delete(
            session, principal=principal, ref=ref, request_id=request_id
        )

    # ═══════════════════════════════════════════════════════════════════
    # 跨对象 Activity 与取消（09 §43.3，05 §12.5 `/activity`）
    # ═══════════════════════════════════════════════════════════════════

    async def list_user_activity(
        self,
        session: AsyncSession,
        *,
        principal,
        limit: int = 50,
    ) -> list[dict]:
        """当前用户私有对象任务 + 当前 Account 共享对象任务（09 §43.3/04 §10.12）。"""
        if principal.actor_account_id is None:
            return []
        ops = await self._store.list_user_activity(
            session,
            account_id=principal.actor_account_id,
            user_id=principal.actor_user_id,
            limit=limit,
        )
        return [self._operation_item_dto(op) for op in ops]

    async def cancel_activity_operation(
        self,
        session: AsyncSession,
        *,
        principal,
        operation_id: uuid.UUID,
        scope_kind: str = "self",
        request_id: str | None = None,
    ) -> dict:
        """`/activity/{id}/cancel`：可取消状态 + 目标对象写权限（09 §46.2/05 §12.5）。"""
        op = await self._store.get_operation(session, operation_id)
        if op is None:
            raise EntityNotFoundError(f"operation {operation_id} not visible")
        if scope_kind == "self":
            if op.account_id != principal.actor_account_id:
                raise EntityNotFoundError(f"operation {operation_id} not visible")
            if op.target_visibility == VIS_USER_PRIVATE and op.owner_user_id != principal.actor_user_id:
                raise EntityNotFoundError(f"operation {operation_id} not visible")
        elif scope_kind == "account":
            if op.account_id != principal.actor_account_id or op.target_visibility != VIS_ACCOUNT_SHARED:
                raise EntityNotFoundError(f"operation {operation_id} not visible")
        # 权限：私有 → task.cancel.self；共享 → task.cancel.account_shared/platform
        perm = (
            "task.cancel.self"
            if op.target_visibility == VIS_USER_PRIVATE
            else ("task.cancel.platform" if scope_kind == "platform" else "task.cancel.account_shared")
        )
        if perm not in principal.permissions:
            raise ResourceError("PERMISSION_NOT_GRANTED")
        if op.status not in ("pending", "running") or not op.cancellable:
            raise ResourceError("RESOURCE_OPERATION_NOT_CANCELLABLE")
        # 同时校验目标对象写权限（09 §46.2）
        if op.target_type == OBJECT_RESOURCE and op.target_id:
            ref = await self.registry_store().get_ref(session, uuid.UUID(op.target_id))
            if ref is not None:
                write_perm = (
                    "resource.user_private.write.self"
                    if ref.visibility == VIS_USER_PRIVATE
                    else "resource.account_shared.write.account"
                )
                if write_perm not in principal.permissions:
                    raise ResourceError("RESOURCE_OPERATION_NOT_CANCELLABLE")
        await self.registry.finish_operation(session, op.id, status="cancelling")
        await self._execution.cancel(op.ov_operation_id)
        await self._audit(
            session,
            principal=principal,
            scope=None,
            action="resource.operation.cancel",
            result="success",
            request_id=request_id,
            target_id=op.target_id,
            account_id=op.account_id,
            subject_user_id=op.owner_user_id,
            metadata={"operation_id": str(op.id), "operation_type": op.operation_type},
        )
        return {"operation_id": str(op.id), "status": "cancelling"}

    # ═══════════════════════════════════════════════════════════════════
    # Capabilities（09 §40.5，AC⑩：服务端强制，不依赖客户端常量）
    # ═══════════════════════════════════════════════════════════════════

    def capabilities(self) -> dict:
        return {
            "source_types": ["upload", "web", "git"],
            "upload": {
                "max_files_per_batch": self._config.upload_max_files_per_batch,
                "max_file_size_bytes": self._config.upload_max_size_bytes,
                "ttl_minutes": self._config.upload_ttl_minutes,
                "accepts_archives": False,
            },
            "watch": {
                "enabled": self._config.watch_enabled,
                "interval_presets_minutes": list(
                    self._config.watch_interval_presets or (60, 360, 720, 1440, 10080)
                ),
                "manual_refresh_min_interval_seconds": self._config.refresh_min_interval_seconds,
            },
            "git": {
                "ignore_dirs_max": 50,
                "include_exclude_max": 20,
                "processing_mode_fixed": "semantic_and_vectors",
            },
        }

    # ═══════════════════════════════════════════════════════════════════
    # Upload（09 §40.5/§46.1，AC⑦：绑定 Actor/Account/Visibility/类型）
    # ═══════════════════════════════════════════════════════════════════

    async def create_upload(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        filename: str,
        data: bytes,
        declared_mime: str | None = None,
    ) -> PlatformUpload:
        """服务端独立校验大小/Magic Bytes/MIME/扩展名（09 §40.5：不能只靠前端）。"""
        if len(data) > self._config.upload_max_size_bytes:
            raise ResourceError("RESOURCE_FILE_TOO_LARGE", retryable=True)
        from openviking.server.platform.resource.storage import UnsupportedFormatError

        try:
            blob = build_blob(filename=filename, data=data, declared_mime=declared_mime)
        except UnsupportedFormatError as exc:
            raise ResourceError("RESOURCE_FORMAT_UNSUPPORTED") from exc
        await self._uploads.put(blob)
        upload = await self.registry.create_upload(
            session,
            account_id=scope.account_id,
            actor_user_id=principal.actor_user_id,
            target_visibility=scope.visibility,
            owner_user_id=scope.owner_user_id,
            object_type=OBJECT_RESOURCE,
            storage_ref=blob.storage_ref,
            original_filename=blob.original_filename,
            mime_type=blob.mime_type,
            size_bytes=blob.size_bytes,
            content_hash=blob.content_hash,
        )
        return upload

    # ═══════════════════════════════════════════════════════════════════
    # 导入（09 §40.7 异步事务，AC⑦）
    # ═══════════════════════════════════════════════════════════════════

    async def import_resources(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        items: list[dict],
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict:
        """批量导入：refs+ops 同一事务；OpenViking 调用事务后编排。

        每项独立成功/失败（09 §40.5：不做全批回滚），返回 batch_id 与每项
        `resource_id/operation_id/error`；`Idempotency-Key` 重放返回相同
        Batch/Operation（AC⑦）。
        """
        if not items:
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
        if len(items) > self._config.upload_max_files_per_batch:
            raise ResourceError("RESOURCE_FILE_TOO_LARGE")

        batch_id = uuid.uuid4()
        results: list[dict] = []
        pending: list[dict] = []

        for index, item in enumerate(items):
            try:
                # 每项独立事务（savepoint）：失败项回滚自身 ref/op，
                # 不影响同批其他项（09 §40.5 不做全批回滚）
                async with session.begin_nested():
                    prepared = await self._prepare_import_item(
                        session,
                        principal=principal,
                        scope=scope,
                        item=item,
                        batch_id=batch_id,
                        idempotency_key=_per_item_key(idempotency_key, index),
                    )
            except (ResourceError, EntityNotFoundError) as exc:
                reason = getattr(exc, "reason", "RESOURCE_NOT_FOUND")
                # 09 §47.2：Upload 重放/跨 Scope 消费拒绝必须审计（在 savepoint
                # 外写入，避免随失败项回滚）
                action = (
                    "resource.upload.denied"
                    if reason
                    in ("RESOURCE_UPLOAD_EXPIRED", "RESOURCE_UPLOAD_ALREADY_CONSUMED", "RESOURCE_NOT_FOUND")
                    else "resource.import.denied"
                )
                await self._audit(
                    session,
                    principal=principal,
                    scope=scope,
                    action=action,
                    result="denied",
                    reason=reason,
                    request_id=request_id,
                    metadata={"batch_id": str(batch_id), "index": index},
                )
                results.append(
                    {
                        "resource_id": None,
                        "operation_id": None,
                        "error": {
                            "code": reason,
                            "message": _error_message(reason),
                            "retryable": getattr(exc, "retryable", False),
                        },
                    }
                )
                continue
            results.append(
                {
                    "resource_id": str(prepared["ref"].id),
                    "operation_id": str(prepared["operation"].id),
                    "error": None,
                }
            )
            pending.append(prepared)

        await self._audit(
            session,
            principal=principal,
            scope=scope,
            action="resource.import.request",
            result="success",
            request_id=request_id,
            metadata={"batch_id": str(batch_id), "items": len(results), "accepted": len(pending)},
        )
        # Idempotency-Key 重放：复用首次 Batch ID（AC⑦ 相同 Batch/Operation）
        replayed_batch_ids = {
            str(p["operation"].batch_id) for p in pending if p["operation"].batch_id is not None
        }
        if len(replayed_batch_ids) == 1 and len(replayed_batch_ids & {None}) == 0:
            batch_id = uuid.UUID(next(iter(replayed_batch_ids)))
        return {
            "batch_id": str(batch_id),
            "items": results,
            "_pending": pending,
        }

    async def process_import_batch(
        self,
        session: AsyncSession,
        *,
        batch: dict,
        principal,
        scope: ResourceScope,
        request_id: str | None = None,
    ) -> list[dict]:
        """事务后编排：受控执行面摄取 → activate/fail + Operation 终态（09 §40.7）。

        返回每项最终结果（resource_id/operation_id/error），同步执行面在
        批量响应中表达独立成功/失败（09 §40.5）；准备阶段拒绝的项原样保留。
        """
        outcomes: dict[str, dict] = {}
        for prepared in batch.get("_pending", []):
            ref = prepared["ref"]
            operation = prepared["operation"]
            if operation.status != "pending" or ref.status != "provisioning":
                # 幂等重放：已终态对象不重复摄取（AC⑦）
                outcomes[str(operation.id)] = {
                    "resource_id": str(ref.id),
                    "operation_id": str(operation.id),
                    "error": None,
                }
                continue
            outcome = await self._execution.ingest(
                ref_id=ref.id,
                source=prepared["source"],
                instruction=prepared.get("instruction"),
            )
            await self._apply_outcome(
                session,
                ref=ref,
                operation=operation,
                outcome=outcome,
                actor_user_id=principal.actor_user_id,
                request_id=request_id,
                scope=scope,
                action_success="resource.import.succeeded",
                action_failed="resource.import.failed",
            )
            outcomes[str(operation.id)] = {
                "resource_id": str(ref.id),
                "operation_id": str(operation.id),
                "error": (
                    None
                    if outcome.ok
                    else {
                        "code": outcome.error_code or RESOURCE_PARSE_FAILED,
                        "message": outcome.error_summary or "处理失败",
                        "retryable": outcome.retryable,
                    }
                ),
            }
        results: list[dict] = []
        for item in batch.get("items", []):
            operation_id = item.get("operation_id")
            if operation_id in outcomes:
                results.append(outcomes[operation_id])
            else:
                results.append(item)
        return results

    async def _prepare_import_item(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        item: dict,
        batch_id: uuid.UUID,
        idempotency_key: str | None,
    ) -> dict:
        """单对象导入准备：ref + operation 同一事务（09 §40.7 步骤 2–4）。"""
        upload_id = item.get("upload_id")
        source_url = item.get("source_url")
        if upload_id:
            source_type = "upload"
        elif source_url:
            source_type = "git" if item.get("is_git") else "web"
        else:
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
        try:
            upload_uuid = uuid.UUID(str(upload_id)) if upload_id else None
        except ValueError as exc:
            raise EntityNotFoundError(f"upload {upload_id} not consumable") from exc

        ref_id = uuid.uuid4()
        root = (
            f"viking://user/{principal.actor_ov_user_id}/resources/{ref_id}/"
            if scope.visibility == VIS_USER_PRIVATE
            else f"viking://resources/{ref_id}/"
        )
        # 统一授权门（AC①：目标由入口+权限确定，客户端不能改变）
        # me 入口用 Actor 隐含 Subject；account/platform 入口显式指定目标 Account
        await self.facade.authorize_uri(
            principal,
            action="write",
            uri=root,
            object_type=OBJECT_RESOURCE,
            subject_account_id=scope.account_id if scope.kind != "me" else None,
            subject_user_id=(
                scope.owner_user_id
                if scope.kind not in ("me",) and scope.visibility == VIS_USER_PRIVATE
                else None
            ),
            request_id="",
        )

        ref = await self.registry.begin_create(
            session,
            account_id=scope.account_id,
            object_type=OBJECT_RESOURCE,
            visibility=scope.visibility,
            owner_user_id=scope.owner_user_id,
            actor_user_id=principal.actor_user_id,
            ov_uri=root,
            display_name=item.get("name"),
            description=item.get("description"),
            tags=item.get("tags"),
            idempotency_key=idempotency_key or None,
        )
        if ref.status != "provisioning":
            # Idempotency-Key 重放：返回既有对象与既有 Operation（AC⑦）
            existing_op = await self._store.get_operation_for_target(
                session,
                account_id=scope.account_id,
                target_type=OBJECT_RESOURCE,
                target_id=str(ref.id),
                operation_type="resource_import",
            )
            if existing_op is None:
                raise ResourceError("RESOURCE_NOT_FOUND")
            source = _source_dict(ref, blob=None)
            if ref.source_type == "upload":
                source["blob"] = None
            return {
                "ref": ref,
                "operation": existing_op,
                "source": source,
                "instruction": item.get("instruction"),
            }

        operation = await self.registry.create_operation(
            session,
            account_id=scope.account_id,
            operation_kind="task",
            operation_type="resource_import",
            ov_operation_id=f"ov-res-{uuid.uuid4()}",
            target_type=OBJECT_RESOURCE,
            target_id=str(ref.id),
            target_visibility=scope.visibility,
            owner_user_id=scope.owner_user_id,
            actor_user_id=principal.actor_user_id,
            generation=1,
            cancellable=True,
        )
        operation.batch_id = batch_id

        blob: UploadedBlob | None = None
        validation = None
        source: dict = {"source_type": source_type, "source_display": None, "blob": None}
        if upload_id:
            try:
                upload = await self.registry.consume_upload(
                    session,
                    upload_id=upload_uuid,
                    account_id=scope.account_id,
                    actor_user_id=principal.actor_user_id,
                    visibility=scope.visibility,
                    object_type=OBJECT_RESOURCE,
                    operation_id=operation.id,
                )
            except UploadNotConsumableError as exc:
                if exc.reason == "UPLOAD_EXPIRED":
                    raise ResourceError("RESOURCE_UPLOAD_EXPIRED", retryable=True) from exc
                if exc.reason == "UPLOAD_CONSUMED":
                    raise ResourceError("RESOURCE_UPLOAD_ALREADY_CONSUMED") from exc
                raise EntityNotFoundError(f"upload {upload_id} not consumable") from exc
            blob = await self._uploads.get(upload.storage_ref)
            if blob is None:
                raise ResourceError("RESOURCE_UPLOAD_EXPIRED", retryable=True)
            self._reject_archive_blob(blob)
            source["source_display"] = blob.original_filename
            source["blob"] = blob
            ref.source_display = blob.original_filename
            ref.mime_type = blob.mime_type
            ref.size_bytes = blob.size_bytes
            ref.source_fingerprint = _blob_fingerprint(blob)
        else:
            validation = self._security.validate(
                source_url, kind="git" if source_type == "git" else "web"
            )
            ref.source_display = validation.display
            ref.source_fingerprint = validation.fingerprint
            if validation.stable:
                # 稳定来源（无 Userinfo/Query/Fragment）以应用层密文保存，
                # 是 Watch/Refresh 的唯一可用标记（09 §40.6/§43.2）
                ref.source_locator_ciphertext, ref.source_locator_key_version = (
                    self._cipher.new_one_time_locator(validation.canonical_url)
                )
            # 含 Query 的一次性 URL：只在导入 Operation 期间使用，
            # 不写入产品引用（不能 Refresh/Watch，09 §40.6）
            source["source_display"] = validation.display
            if not ref.display_name:
                ref.display_name = _default_name(None, validation)

        if not ref.display_name and source["source_display"]:
            ref.display_name = source["source_display"]
        ref.source_type = source_type
        await self.registry_store().update_ref(session, ref)
        return {
            "ref": ref,
            "operation": operation,
            "source": source,
            "instruction": item.get("instruction"),
        }

    # ═══════════════════════════════════════════════════════════════════
    # 列表 / 详情 / PATCH（09 §39/§42/§46.2，AC⑧）
    # ═══════════════════════════════════════════════════════════════════

    async def list_resources(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """列表（09 §39）：私有含属主自己的 provisioning/failed 占位行；
        共享只对管理者显示占位行，普通 User 只看到 active。"""
        include_placeholders = scope.visibility == VIS_USER_PRIVATE or _is_admin(principal)
        cursor_dt, cursor_id = _decode_cursor(cursor)
        rows = await self._store.list_resources(
            session,
            account_id=scope.account_id,
            visibility=scope.visibility,
            owner_user_id=scope.owner_user_id,
            include_placeholders=include_placeholders,
            source_type=source_type,
            status=status,
            limit=limit,
            cursor_created_at=cursor_dt,
            cursor_id=cursor_id,
        )
        next_cursor = None
        if rows and len(rows) == limit:
            last = rows[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
        return [self.summary_dto(r) for r in rows], next_cursor

    async def get_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        """详情（09 §42：不返回 ov_uri/宿主机路径/Watch Task ID，09 §48 #3）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        nodes = await self._execution.list_nodes(ref_id=ref.id)
        watch = await self._store.get_watch(session, ref.id)
        current_op = None
        if ref.latest_operation_id is not None:
            op = await self._store.get_operation(session, ref.latest_operation_id)
            if op is not None and op.status in ("pending", "running", "cancelling", "failed"):
                current_op = self._operation_item_dto(op)
        return self.detail_dto(ref, nodes=nodes, watch_dto=self._watch_dto(watch), current_op=current_op)

    async def patch_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        version: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        """只改名称/说明/标签（09 §42.4，乐观锁 If-Match/请求体版本号）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        if version is not None and version != ref.version:
            raise ResourceVersionConflictError()
        changed = False
        if display_name is not None and display_name != ref.display_name:
            ref.display_name = display_name
            changed = True
        if description is not None and description != ref.description:
            ref.description = description
            changed = True
        if tags is not None and list(tags) != (ref.tags or []):
            from openviking.server.platform.registry.tags import normalize_tags

            ref.tags = normalize_tags(tags)
            await self.registry.sync_tags_outbox(session, ref_id=ref.id, tags=ref.tags)
            changed = True
        if changed:
            ref.version = (ref.version or 1) + 1
            ref.updated_by_actor_user_id = principal.actor_user_id
            await self.registry_store().update_ref(session, ref)
            # onupdate 列（updated_at）在 flush 后过期；async 下惰性刷新会触发
            # MissingGreenlet，显式取回后再序列化 DTO
            await session.refresh(ref)
        await self._audit(
            session,
            principal=principal,
            scope=scope,
            action="resource.metadata.update",
            result="success",
            request_id=request_id,
            target_id=str(ref.id),
            metadata={"version": ref.version},
        )
        return self.summary_dto(ref)

    # ═══════════════════════════════════════════════════════════════════
    # replace / refresh / retry（09 §43.1/§46.2）
    # ═══════════════════════════════════════════════════════════════════

    async def replace_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        upload_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        """上传来源替换（09 §43.1：新文件 → 同一 Resource `resource_replace` Operation）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        if ref.source_type != "upload":
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
        operation, blob = await self._begin_ingest_operation(
            session,
            principal=principal,
            scope=scope,
            ref=ref,
            operation_type="resource_replace",
            upload_id=upload_id,
        )
        outcome = await self._run_ingest(session, ref=ref, operation=operation, blob=blob)
        await self._apply_outcome(
            session,
            ref=ref,
            operation=operation,
            outcome=outcome,
            actor_user_id=principal.actor_user_id,
            request_id=request_id,
            scope=scope,
            action_success="resource.source.replace",
            action_failed="resource.source.replace",
        )
        return {
            "resource_id": str(ref.id),
            "operation_id": str(operation.id),
            "status": operation.status,
        }

    async def refresh_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        """稳定远程来源手动 Refresh（09 §43.1：5 分钟限频，Capabilities 返回）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        if ref.source_type not in ("web", "git") or ref.source_locator_ciphertext is None:
            raise ResourceError("RESOURCE_SOURCE_NOT_STABLE")
        await self._check_refresh_rate_limit(session, ref)
        operation, _ = await self._begin_ingest_operation(
            session,
            principal=principal,
            scope=scope,
            ref=ref,
            operation_type="resource_refresh",
        )
        outcome = await self._run_ingest(session, ref=ref, operation=operation)
        await self._apply_outcome(
            session,
            ref=ref,
            operation=operation,
            outcome=outcome,
            actor_user_id=principal.actor_user_id,
            request_id=request_id,
            scope=scope,
            action_success="resource.refresh",
            action_failed="resource.refresh",
        )
        return {
            "resource_id": str(ref.id),
            "operation_id": str(operation.id),
            "status": operation.status,
        }

    async def retry_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        upload_id: uuid.UUID | None = None,
        source_url: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        """重试首次失败的导入（09 §46.2：上传/一次性 URL 必须重新提交来源）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        if ref.status in ("provisioning", "active"):
            raise ResourceBusyError()
        if ref.status != "failed":
            raise ResourceError("RESOURCE_NOT_FOUND")
        operation, blob = await self._begin_ingest_operation(
            session,
            principal=principal,
            scope=scope,
            ref=ref,
            operation_type="resource_import",
            upload_id=upload_id,
            source_url=source_url,
            retry=True,
        )
        outcome = await self._run_ingest(session, ref=ref, operation=operation, blob=blob)
        await self._apply_outcome(
            session,
            ref=ref,
            operation=operation,
            outcome=outcome,
            actor_user_id=principal.actor_user_id,
            request_id=request_id,
            scope=scope,
            action_success="resource.import.retried",
            action_failed="resource.import.retried",
        )
        return {
            "resource_id": str(ref.id),
            "operation_id": str(operation.id),
            "status": operation.status,
        }

    # ═══════════════════════════════════════════════════════════════════
    # 发布（09 §44，AC⑤）
    # ═══════════════════════════════════════════════════════════════════

    async def publish_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        """仅 Account Admin 发布自己的私有 Resource 为独立共享副本（09 §44.1）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        if scope.visibility != VIS_USER_PRIVATE or scope.owner_user_id != principal.actor_user_id:
            raise EntityNotFoundError(f"resource {resource_id} not visible")
        if ACCOUNT_ADMIN not in (principal.role_codes or ()):
            await self._audit(
                session,
                principal=principal,
                scope=scope,
                action="resource.publish",
                result="denied",
                reason="RESOURCE_PUBLISH_FORBIDDEN",
                request_id=request_id,
                target_id=str(ref.id),
            )
            raise ResourceError("RESOURCE_PUBLISH_FORBIDDEN")
        if ref.status != "active":
            raise ResourceBusyError()

        new_ref = await self.registry.begin_create(
            session,
            account_id=scope.account_id,
            object_type=OBJECT_RESOURCE,
            visibility=VIS_ACCOUNT_SHARED,
            owner_user_id=None,
            actor_user_id=principal.actor_user_id,
            ov_uri=f"viking://resources/{uuid.uuid4()}/",
            display_name=ref.display_name,
            description=ref.description,
            tags=ref.tags,
            idempotency_key=idempotency_key or None,
            source_type=ref.source_type,
            source_display=ref.source_display,
            source_fingerprint=ref.source_fingerprint,
        )
        if new_ref.status != "active":
            # 复制当前成功内容（09 §44.2：独立内容；Watch/审计历史/Query 不复制）
            await self._execution.copy(source_ref_id=ref.id, target_ref_id=new_ref.id)
            await self.registry.activate(
                session, new_ref.id, actor_user_id=principal.actor_user_id, generation=1
            )
        # 稳定来源的加密定位符可复制（同一公开来源）；Query/临时上传信息不复制
        if ref.source_locator_ciphertext is not None and ref.source_type in ("web", "git"):
            new_ref.source_locator_ciphertext = ref.source_locator_ciphertext
            new_ref.source_locator_key_version = ref.source_locator_key_version
            await self.registry_store().update_ref(session, new_ref)

        await self._audit(
            session,
            principal=principal,
            scope=scope,
            action="resource.publish",
            result="success",
            request_id=request_id,
            target_id=str(ref.id),
            metadata={
                "published_resource_id": str(new_ref.id),
                "target_account_id": str(scope.account_id),
            },
        )
        return {
            "resource_id": str(new_ref.id),
            "visibility": VIS_ACCOUNT_SHARED,
            "source_resource_id": str(ref.id),
        }

    # ═══════════════════════════════════════════════════════════════════
    # 删除六步事务（09 §45.2，AC②）
    # ═══════════════════════════════════════════════════════════════════

    async def deletion_preview(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        """删除影响范围（09 §45.1，弹窗数据）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        nodes = await self._execution.list_nodes(ref_id=ref.id)
        watch = await self._store.get_watch(session, ref.id)
        inflight = await self._store.list_inflight_operations(session, resource_id=str(ref.id))
        now = datetime.now(timezone.utc)
        await self._audit(
            session,
            principal=principal,
            scope=scope,
            action="resource.deletion-preview",
            result="success",
            request_id=request_id,
            target_id=str(ref.id),
        )
        return {
            "resource_id": str(ref.id),
            "name": ref.display_name,
            "visibility": ref.visibility,
            "content": {
                "node_count": len([n for n in nodes if n.kind == "file"]),
                "size_bytes": sum(n.size_bytes for n in nodes),
            },
            "watch": {
                "configured": watch is not None,
                "will_be_paused": watch is not None and watch.state == "active",
            },
            "inflight_cancelled": [str(o.id) for o in inflight],
            "restore_until": (now + timedelta(days=self._config.deletion_purge_days)).isoformat(),
            "shared_impact_note": (
                None
                if ref.visibility == VIS_USER_PRIVATE
                else "当前 Account 成员及其已连接 Agent 将无法继续检索或读取。"
            ),
        }

    async def delete_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        version: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        """删除六步事务（09 §45.2，AC②）。"""
        # 幂等（AC⑧）：已有未恢复删除任务时复用并返回同一 job
        existing = await self.registry_store().get_active_deletion_job(
            session, OBJECT_RESOURCE, str(resource_id)
        )
        if existing is not None:
            return {
                "resource_id": str(resource_id),
                "deletion_job_id": str(existing.id),
                "deleted_at": existing.deleted_at.isoformat(),
                "restore_until": existing.purge_after.isoformat(),
            }
        ref = await self._load_visible_ref(session, scope, resource_id)
        if version is not None and version != ref.version:
            raise ResourceVersionConflictError()
        now = datetime.now(timezone.utc)

        # 步骤 2：pending_deletion + iam_deletion_jobs
        ref.status = "pending_deletion"
        ref.deleted_at = now
        await self.registry_store().update_ref(session, ref)
        job = await self.registry.create_deletion_job(
            session,
            account_id=scope.account_id,
            resource_type=OBJECT_RESOURCE,
            resource_id=str(ref.id),
            deleted_by=principal.actor_user_id,
            now=now,
            ov_uri=ref.ov_uri,
        )

        # 步骤 3：Watch 立即暂停，新的 Refresh/Watch 调度被拒绝
        await self._store.pause_all_for_resource(session, ref.id, now)

        # 步骤 4：可取消在途 Operation 协作取消
        for op in await self._store.list_inflight_operations(session, resource_id=str(ref.id)):
            await self.registry.finish_operation(session, op.id, status="cancelling")
            await self._execution.cancel(op.ov_operation_id)

        # 步骤 5：列表/Search 排除（status 过滤在查询层统一生效）
        await self._audit(
            session,
            principal=principal,
            scope=scope,
            action="resource.delete",
            result="success",
            request_id=request_id,
            target_id=str(ref.id),
            metadata={"deletion_job_id": str(job.id), "purge_after": job.purge_after.isoformat()},
        )
        return {
            "resource_id": str(ref.id),
            "deletion_job_id": str(job.id),
            "deleted_at": now.isoformat(),
            "restore_until": job.purge_after.isoformat(),
        }

    # ═══════════════════════════════════════════════════════════════════
    # Operation 终态提交（AC③⑩：Worker/执行面回写统一入口）
    # ═══════════════════════════════════════════════════════════════════

    async def complete_operation(
        self,
        session: AsyncSession,
        *,
        operation_id: uuid.UUID,
        status: str,
        stage: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        retryable: bool = False,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> PlatformOperationRef:
        """Worker/执行面结果提交：旧 generation 只能记录终态（AC③）。

        - 无论新旧，Operation 终态总是记录（09 §45.3）；
        - 只有 `op.generation == ref.active_generation + 1` 且对象不在删除中
          才允许切换内容（activate）；
        - 删除中对象晚到的任务结果不能重新激活（09 §45.3/§48 #10）。
        """
        now = now or datetime.now(timezone.utc)
        operation = await self._store.get_operation(session, operation_id)
        if operation is None:
            raise EntityNotFoundError(f"operation {operation_id} not found")

        ref = None
        if operation.target_type == OBJECT_RESOURCE and operation.target_id:
            ref = await self.registry_store().get_ref(session, uuid.UUID(operation.target_id))

        switchable = (
            ref is not None
            and ref.status in ("provisioning", "active", "failed")
            and operation.generation == (ref.active_generation or 0) + 1
        )

        if status == "succeeded" and ref is not None and switchable:
            if ref.status == "provisioning":
                # 首次激活：经 ContentRegistryService.activate（冻结点扩展点）
                await self.registry.activate(
                    session,
                    ref.id,
                    actor_user_id=ref.owner_user_id or ref.created_by_actor_user_id,
                    ov_uri=None,
                    generation=operation.generation,
                    operation_id=operation.id,
                    now=now,
                )
            else:
                # Refresh/Watch/替换/重试成功：对象已 active（或 failed 重试成功），
                # 直接原子切换代数（04 §10.10：Operation 成功且 generation 为最新）
                if ref.status != "active":
                    ref.status = "active"
                ref.active_generation = operation.generation
                ref.latest_operation_id = operation.id
                ref.last_processed_at = now
                ref.updated_by_actor_user_id = ref.owner_user_id or ref.created_by_actor_user_id
                await self.registry_store().update_ref(session, ref)
        elif status == "failed" and ref is not None and switchable:
            if ref.status == "provisioning":
                # 首次导入失败：保持 failed 仅管理者可见（AC④）
                await self.registry.fail(session, ref.id, now=now)
            else:
                # Refresh/Watch 失败：Resource 保持 active，最近成功版本可读；
                # latest_operation_id 指向失败任务 → 详情显示「最近同步失败」（AC④）
                ref.latest_operation_id = operation.id
                await self.registry_store().update_ref(session, ref)
        elif (
            status == "cancelled"
            and ref is not None
            and switchable
            and ref.status == "provisioning"
        ):
            await self.registry.fail(session, ref.id, now=now)

        return await self.registry.finish_operation(
            session,
            operation_id,
            status=status,
            stage=stage,
            error_code=error_code,
            error_summary=error_summary,
            retryable=retryable,
            now=now,
        )

    # ═══════════════════════════════════════════════════════════════════
    # Activity 与取消（09 §43.3）
    # ═══════════════════════════════════════════════════════════════════

    async def list_operations(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        limit: int = 50,
    ) -> list[dict]:
        await self._load_visible_ref(session, scope, resource_id)
        ops = await self._store.list_operations_for_resource(
            session, account_id=scope.account_id, resource_id=str(resource_id), limit=limit
        )
        return [self._operation_item_dto(op) for op in ops]

    async def cancel_operation(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        operation_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        """协作取消（09 §43.3：仅 pending/running 且可取消；同时校验目标写权限）。"""
        await self._load_visible_ref(session, scope, resource_id)
        op = await self._store.get_operation(session, operation_id)
        if (
            op is None
            or op.target_type != OBJECT_RESOURCE
            or op.target_id != str(resource_id)
            or op.account_id != scope.account_id
        ):
            raise EntityNotFoundError(f"operation {operation_id} not visible")
        if op.status not in ("pending", "running") or not op.cancellable:
            raise ResourceError("RESOURCE_OPERATION_NOT_CANCELLABLE")
        await self.registry.finish_operation(session, op.id, status="cancelling")
        await self._execution.cancel(op.ov_operation_id)
        await self._audit(
            session,
            principal=principal,
            scope=scope,
            action="resource.operation.cancel",
            result="success",
            request_id=request_id,
            target_id=str(resource_id),
            metadata={"operation_id": str(op.id), "operation_type": op.operation_type},
        )
        return {"operation_id": str(op.id), "status": "cancelling"}

    # ═══════════════════════════════════════════════════════════════════
    # Nodes / 搜索（09 §42.3/§46.2，AC⑨）
    # ═══════════════════════════════════════════════════════════════════

    async def list_nodes(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        node_id: str | None = None,
        limit: int = MAX_CHILDREN_PER_PAGE,
    ) -> list[dict]:
        """目录按需展开（09 §42.3：单页节点数限制，大仓库不一次返回整树）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        parent_path = ""
        if node_id:
            parent_path = await self._resolve_node_or_deny(session, principal, scope, ref, node_id)
        records = await self._execution.list_nodes(ref_id=ref.id)
        children = _children_of(records, parent_path)[:limit]
        return [
            {
                "node_id": self._nodes.encode(ref.id, n.path),
                "name": n.name,
                "path": n.path,
                "type": n.kind,
                "size_bytes": n.size_bytes,
                "mime_type": n.mime_type,
            }
            for n in children
        ]

    async def read_node(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        node_id: str,
        request_id: str | None = None,
    ) -> dict:
        """节点元数据/安全预览（09 §42.3：文本按区块分页，不支持预览只返回元数据）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        rel_path = await self._resolve_node_or_deny(
            session, principal, scope, ref, node_id, request_id=request_id
        )
        node = await self._execution.read_node(ref_id=ref.id, rel_path=rel_path)
        if node is None or node.kind != "file":
            raise EntityNotFoundError(f"node {node_id} not found")
        return {
            "node_id": node_id,
            "name": node.name,
            "path": rel_path,
            "size_bytes": node.size_bytes,
            "mime_type": node.mime_type,
            "preview": _text_preview(node),
        }

    async def download_node(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        node_id: str,
        request_id: str | None = None,
    ) -> dict:
        """单节点下载（09 §42.3：安全文件名/Content-Type/Content-Disposition，AC⑪）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        rel_path = await self._resolve_node_or_deny(
            session, principal, scope, ref, node_id, request_id=request_id
        )
        node = await self._execution.read_node(ref_id=ref.id, rel_path=rel_path)
        if node is None or node.kind != "file":
            raise EntityNotFoundError(f"node {node_id} not found")
        filename = safe_download_filename(node.name, rel_path)
        mime = node.mime_type or "application/octet-stream"
        inline = not is_executable_extension(filename) and mime.split("/", 1)[0] in ("text", "image")
        return {
            "content": node.text or b"",
            "filename": filename,
            "mime_type": mime,
            "inline": inline,
            "size_bytes": node.size_bytes,
        }

    async def search_resource(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        query: str,
        limit: int = 10,
    ) -> dict:
        """仅在当前 Resource 内检索（09 §46.2，AC⑨ 节点不越权）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        query = (query or "").strip()
        if not query:
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
        hits = await self._execution.search(ref_id=ref.id, query=query, limit=min(limit, 50))
        items = [
            {
                "node_id": self._nodes.encode(ref.id, h["path"]),
                "name": h["name"],
                "snippet": h["snippet"],
            }
            for h in hits
        ]
        return {"resource_id": str(ref.id), "query": query, "items": items}

    # ═══════════════════════════════════════════════════════════════════
    # Watch 执行（09 §43.2，AC⑥：只存 Resource ID，触发时 Facade 实时校验）
    # ═══════════════════════════════════════════════════════════════════

    async def trigger_watch(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict:
        """立即同步（09 §43.2：异步返回 Operation ID；已有运行任务拒绝重复触发）。"""
        ref = await self._load_visible_ref(session, scope, resource_id)
        self._require_watchable_source(ref)
        watch = await self._store.get_watch(session, ref.id)
        if watch is None or watch.state != "active":
            raise ResourceError("RESOURCE_WATCH_UNAVAILABLE")
        operation, _ = await self._begin_ingest_operation(
            session,
            principal=principal,
            scope=scope,
            ref=ref,
            operation_type="resource_watch",
        )
        outcome = await self._run_ingest(session, ref=ref, operation=operation)
        await self._apply_outcome(
            session,
            ref=ref,
            operation=operation,
            outcome=outcome,
            actor_user_id=principal.actor_user_id,
            request_id=request_id,
            scope=scope,
            action_success="resource.watch.trigger",
            action_failed="resource.watch.trigger",
        )
        return {"resource_id": str(ref.id), "operation_id": str(operation.id)}

    async def run_watches(
        self, session: AsyncSession, *, now: datetime | None = None, limit: int = 10
    ) -> int:
        """Watch Worker 单轮调度（09 §43.2：系统执行身份，Facade 实时校验）。"""
        now = now or datetime.now(timezone.utc)
        watches = await self._store.list_watches_due(session, now=now, limit=limit)
        processed = 0
        for watch in watches:
            ref = await self.registry_store().get_ref(session, watch.resource_id)
            if ref is None or ref.status != "active" or ref.deleted_at is not None:
                watch.state = "error"
                watch.last_error = "RESOURCE_NOT_FOUND"
                watch.last_result = "failed"
                await self._store.update_watch(session, watch)
                processed += 1
                continue
            try:
                self._require_watchable_source(ref)
            except ResourceError as exc:
                watch.state = "error"
                watch.last_error = exc.reason
                watch.last_result = "failed"
                watch.next_run_at = now + timedelta(minutes=watch.interval_minutes)
                await self._store.update_watch(session, watch)
                processed += 1
                continue
            operation = await self._create_ingest_operation(
                session,
                account_id=ref.account_id,
                owner_user_id=ref.owner_user_id,
                actor_user_id=None,
                ref=ref,
                operation_type="resource_watch",
                batch_id=None,
            )
            outcome = await self._run_ingest(session, ref=ref, operation=operation)
            await self._apply_outcome(
                session,
                ref=ref,
                operation=operation,
                outcome=outcome,
                actor_user_id=ref.owner_user_id or ref.created_by_actor_user_id,
                request_id=None,
                scope=None,
                action_success=None,
                action_failed=None,
            )
            watch.last_run_at = now
            watch.last_result = "succeeded" if operation.status == "succeeded" else "failed"
            watch.last_error = operation.error_summary
            watch.next_run_at = now + timedelta(minutes=watch.interval_minutes)
            await self._store.update_watch(session, watch)
            await self._audit(
                session,
                principal=None,
                scope=None,
                action="resource.watch.trigger",
                result="success" if operation.status == "succeeded" else "failed",
                request_id=None,
                target_id=str(ref.id),
                account_id=ref.account_id,
                subject_user_id=ref.owner_user_id,
                system_component=WATCH_SCHEDULER_COMPONENT,
                metadata={"operation_id": str(operation.id)},
            )
            processed += 1
        return processed

    # ═══════════════════════════════════════════════════════════════════
    # DTO（09 §46.4：不返回 ov_uri/内部 Operation ID/Watch Task ID）
    # ═══════════════════════════════════════════════════════════════════

    def summary_dto(self, ref: PlatformContentRef) -> dict:
        return {
            "id": f"res_{ref.id}",
            "visibility": ref.visibility,
            "name": ref.display_name,
            "description": ref.description,
            "source_type": ref.source_type,
            "source_display": ref.source_display,
            "tags": ref.tags or [],
            "lifecycle_status": ref.status,
            "processing": {
                "state": _processing_state(ref),
                "stage": _processing_state(ref),
                "latest_operation_id": str(ref.latest_operation_id) if ref.latest_operation_id else None,
                "last_succeeded_at": ref.last_processed_at.isoformat() if ref.last_processed_at else None,
            },
            "version": ref.version or 1,
            "created_at": ref.created_at.isoformat() if ref.created_at else None,
            "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
        }

    def detail_dto(
        self,
        ref: PlatformContentRef,
        *,
        nodes: list[NodeRecord],
        watch_dto: dict,
        current_op: dict | None,
    ) -> dict:
        dto = self.summary_dto(ref)
        dto["overview"] = None
        dto["content"] = {
            "node_count": len([n for n in nodes if n.kind == "file"]),
            "size_bytes": sum(n.size_bytes for n in nodes),
        }
        dto["watch"] = watch_dto
        dto["current_operation"] = current_op
        return dto

    def _operation_item_dto(self, op: PlatformOperationRef) -> dict:
        return {
            "id": str(op.id),
            "operation_type": op.operation_type,
            "status": op.status,
            "stage": op.stage,
            "initiated_by": "user" if op.initiated_by_actor_user_id is not None else "system",
            "created_at": op.created_at.isoformat() if op.created_at else None,
            "completed_at": op.completed_at.isoformat() if op.completed_at else None,
            "cancellable": op.cancellable and op.status in ("pending", "running"),
            "error": (
                {
                    "code": op.error_code,
                    "summary": op.error_summary,
                    "retryable": op.retryable,
                }
                if op.error_code
                else None
            ),
            "generation": op.generation,
        }

    def _watch_dto(self, watch) -> dict:
        if watch is None:
            return {"state": "not_configured"}
        return {
            "state": watch.state,
            "interval_minutes": watch.interval_minutes,
            "last_run_at": watch.last_run_at.isoformat() if watch.last_run_at else None,
            "next_run_at": watch.next_run_at.isoformat() if watch.next_run_at else None,
            "last_result": watch.last_result,
            "last_error": watch.last_error,
        }

    # ═══════════════════════════════════════════════════════════════════
    # 内部工具
    # ═══════════════════════════════════════════════════════════════════

    async def audit_member_read(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        resource_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        """管理员/平台跨用户读取审计（09 §47.2：管理员/平台跨用户读取与下载）。"""
        await self._audit(
            session,
            principal=principal,
            scope=scope,
            action="resource.admin.read",
            result="success",
            request_id=request_id,
            target_id=str(resource_id),
        )

    async def _load_visible_ref(
        self, session: AsyncSession, scope: ResourceScope, resource_id: uuid.UUID
    ) -> PlatformContentRef:
        """可见性过滤（AC⑧）：不可见一律 404 RESOURCE_NOT_FOUND。"""
        ref = await self.registry_store().get_ref(session, resource_id)
        if ref is None or ref.deleted_at is not None or ref.status == "deleted":
            raise EntityNotFoundError(f"resource {resource_id} not visible")
        if ref.account_id != scope.account_id or ref.object_type != OBJECT_RESOURCE:
            raise EntityNotFoundError(f"resource {resource_id} not visible")
        if ref.visibility != scope.visibility:
            raise EntityNotFoundError(f"resource {resource_id} not visible")
        if scope.visibility == VIS_USER_PRIVATE and ref.owner_user_id != scope.owner_user_id:
            raise EntityNotFoundError(f"resource {resource_id} not visible")
        if scope.visibility == VIS_ACCOUNT_SHARED and ref.owner_user_id is not None:
            raise EntityNotFoundError(f"resource {resource_id} not visible")
        if ref.status == "pending_deletion":
            # 09 §41.2/§45.2 步骤 5：删除中从正常入口统一 404
            raise EntityNotFoundError(f"resource {resource_id} pending deletion")
        return ref

    async def _begin_ingest_operation(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope,
        ref: PlatformContentRef,
        operation_type: str,
        upload_id: uuid.UUID | None = None,
        source_url: str | None = None,
        retry: bool = False,
    ) -> tuple[PlatformOperationRef, UploadedBlob | None]:
        """replace/refresh/retry/watch-trigger 共用：busy 检查 + 新 Operation。"""
        await self._check_busy(session, ref)
        blob: UploadedBlob | None = None
        if upload_id is not None:
            try:
                consumed = await self._consume_upload(
                    session, principal=principal, scope=scope, upload_id=upload_id
                )
            except ResourceError:
                raise
            blob = await self._uploads.get(consumed.storage_ref)
            if blob is None:
                raise ResourceError("RESOURCE_UPLOAD_EXPIRED", retryable=True)
            self._reject_archive_blob(blob)
        if retry and upload_id is None and ref.source_type == "upload":
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
        if source_url is not None:
            if ref.source_type == "upload":
                raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
            validation = self._security.validate(
                source_url, kind="git" if ref.source_type == "git" else "web"
            )
            if validation.stable:
                ref.source_locator_ciphertext, ref.source_locator_key_version = (
                    self._cipher.new_one_time_locator(validation.canonical_url)
                )
            else:
                # 一次性 URL 重试：本次使用，不写入引用（不能 Refresh/Watch）
                ref.source_locator_ciphertext = None
                ref.source_locator_key_version = None
            ref.source_display = validation.display
            ref.source_fingerprint = validation.fingerprint
            await self.registry_store().update_ref(session, ref)
        operation = await self._create_ingest_operation(
            session,
            account_id=scope.account_id,
            owner_user_id=scope.owner_user_id,
            actor_user_id=principal.actor_user_id,
            ref=ref,
            operation_type=operation_type,
            batch_id=None,
        )
        return operation, blob

    def _reject_archive_blob(self, blob: UploadedBlob) -> None:
        """Resource 消费侧归档拒绝（09 §47.3）：`me/resource-uploads` 暂存
        放行 `.zip`（10 §61.5 Skill 包复用同一暂存入口），但 Resource
        导入/替换不接受任何归档包。"""
        if is_archive_filename(blob.original_filename):
            raise ResourceError("RESOURCE_FORMAT_UNSUPPORTED")

    async def _consume_upload(
        self, session: AsyncSession, *, principal, scope: ResourceScope, upload_id: uuid.UUID
    ) -> PlatformUpload:
        try:
            return await self.registry.consume_upload(
                session,
                upload_id=upload_id,
                account_id=scope.account_id,
                actor_user_id=principal.actor_user_id,
                visibility=scope.visibility,
                object_type=OBJECT_RESOURCE,
                operation_id=uuid.uuid4(),
            )
        except UploadNotConsumableError as exc:
            await self._audit(
                session,
                principal=principal,
                scope=scope,
                action="resource.upload.denied",
                result="denied",
                reason=exc.reason,
                request_id=None,
                metadata={"upload_id": str(upload_id)},
            )
            if exc.reason == "UPLOAD_EXPIRED":
                raise ResourceError("RESOURCE_UPLOAD_EXPIRED", retryable=True) from exc
            if exc.reason == "UPLOAD_CONSUMED":
                raise ResourceError("RESOURCE_UPLOAD_ALREADY_CONSUMED") from exc
            raise EntityNotFoundError(f"upload {upload_id} not consumable") from exc

    async def _create_ingest_operation(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        owner_user_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None,
        ref: PlatformContentRef,
        operation_type: str,
        batch_id: uuid.UUID | None,
    ) -> PlatformOperationRef:
        operation = await self.registry.create_operation(
            session,
            account_id=account_id,
            operation_kind="task",
            operation_type=operation_type,
            ov_operation_id=f"ov-res-{uuid.uuid4()}",
            target_type=OBJECT_RESOURCE,
            target_id=str(ref.id),
            target_visibility=ref.visibility,
            owner_user_id=owner_user_id,
            actor_user_id=actor_user_id,
            generation=(ref.active_generation or 0) + 1,
            cancellable=True,
        )
        if batch_id is not None:
            operation.batch_id = batch_id
        return operation

    async def _run_ingest(
        self,
        session: AsyncSession,
        *,
        ref: PlatformContentRef,
        operation: PlatformOperationRef,
        blob: UploadedBlob | None = None,
    ) -> IngestOutcome:
        """受控执行面摄取（09 §40.7 步骤 5：失败返回脱敏错误与 retryable）。"""
        source = {"source_type": ref.source_type, "source_display": ref.source_display, "blob": blob}
        return await self._execution.ingest(ref_id=ref.id, source=source, instruction=None)

    async def _apply_outcome(
        self,
        session: AsyncSession,
        *,
        ref: PlatformContentRef,
        operation: PlatformOperationRef,
        outcome: IngestOutcome,
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
        scope: ResourceScope | None,
        action_success: str | None,
        action_failed: str | None,
    ) -> None:
        """摄取结果回写：只允许当前代数切换内容（AC③）。"""
        if outcome.ok:
            await self.complete_operation(
                session,
                operation_id=operation.id,
                status="succeeded",
                stage="succeeded",
                request_id=request_id,
            )
        else:
            await self.complete_operation(
                session,
                operation_id=operation.id,
                status="failed",
                stage="failed",
                error_code=outcome.error_code or RESOURCE_PARSE_FAILED,
                error_summary=outcome.error_summary or "处理失败",
                retryable=outcome.retryable,
                request_id=request_id,
            )
        action = action_success if outcome.ok else (action_failed or action_success)
        if action and scope is not None:
            await self._audit(
                session,
                principal=_ActorRef(actor_user_id, scope),
                scope=scope,
                action=action,
                result="success" if outcome.ok else "failed",
                request_id=request_id,
                target_id=str(ref.id),
                metadata={"operation_id": str(operation.id)},
            )

    async def _check_busy(self, session: AsyncSession, ref: PlatformContentRef) -> None:
        """同一 Resource 同时只允许一个摄取类 Operation（09 §43.1，RESOURCE_BUSY）。"""
        ops = await self._store.list_operations_for_resource(
            session, account_id=ref.account_id, resource_id=str(ref.id), limit=20
        )
        if any(o.status in ("pending", "running") for o in ops):
            raise ResourceBusyError()

    async def _check_refresh_rate_limit(self, session: AsyncSession, ref: PlatformContentRef) -> None:
        op = await self._store.get_operation_for_target(
            session,
            account_id=ref.account_id,
            target_type=OBJECT_RESOURCE,
            target_id=str(ref.id),
            operation_type="resource_refresh",
        )
        if op is not None and op.created_at is not None:
            elapsed = (datetime.now(timezone.utc) - op.created_at).total_seconds()
            if elapsed < self._config.refresh_min_interval_seconds:
                raise ResourceBusyError()

    def _require_watchable_source(self, ref: PlatformContentRef) -> None:
        """Watch 只支持稳定远程来源（09 §43.2/§40.2；上传不支持 Watch）。"""
        if ref.source_type not in ("web", "git") or ref.source_locator_ciphertext is None:
            raise ResourceError("RESOURCE_WATCH_UNAVAILABLE")

    async def _resolve_node_or_deny(
        self,
        session: AsyncSession,
        principal,
        scope: ResourceScope,
        ref: PlatformContentRef,
        node_id: str,
        request_id: str | None = None,
    ) -> str:
        """Node ID 越权（跨 Resource 引用）统一拒绝并审计（09 §48 #12，AC⑨）。"""
        rel_path = self._nodes.decode(ref.id, node_id)
        if rel_path is None:
            await self._audit(
                session,
                principal=principal,
                scope=scope,
                action="resource.node.denied",
                result="denied",
                reason="NODE_ACCESS_DENIED",
                request_id=request_id,
                target_id=str(ref.id),
            )
            raise ResourceNodeDenied()
        return rel_path

    async def _audit(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: ResourceScope | None,
        action: str,
        result: str,
        request_id: str | None = None,
        target_id: str | None = None,
        reason: str | None = None,
        metadata: dict | None = None,
        system_component: str | None = None,
        account_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
    ) -> None:
        account_id = account_id or (scope.account_id if scope else None)
        subject_user_id = subject_user_id or (scope.owner_user_id if scope else None)
        if principal is None or getattr(principal, "actor_user_id", None) is None:
            actor_type = "system"
            actor_user = None
            actor_account = None
            session_id = None
            auth_method = "system"
        else:
            actor_type = "user"
            actor_user = principal.actor_user_id
            actor_account = principal.actor_account_id
            session_id = getattr(principal, "session_id", None)
            auth_method = getattr(principal, "authentication_method", None)
        await self._iam.append_audit_event(
            session,
            request_id=request_id,
            account_id=account_id,
            actor_type=actor_type,
            actor_user_id=actor_user,
            actor_account_id=actor_account,
            actor_system_component=system_component,
            actor_session_id=session_id,
            authentication_method=auth_method,
            subject_account_id=account_id,
            subject_user_id=subject_user_id,
            action=action,
            target_type=OBJECT_RESOURCE,
            target_id=target_id,
            target_visibility=scope.visibility if scope else None,
            scope=scope.kind if scope else "system",
            result=result,
            reason=reason,
            metadata=metadata,
        )


class ResourceWatchUnavailable(ResourceError):
    def __init__(self) -> None:
        super().__init__("RESOURCE_WATCH_UNAVAILABLE")


class ResourceNodeDenied(ResourceError):
    def __init__(self) -> None:
        super().__init__("RESOURCE_NOT_FOUND")


class _ActorRef:
    """审计 Actor 封装（执行面完成时用发起 User，非真实请求上下文）。"""

    def __init__(self, actor_user_id, scope: ResourceScope) -> None:
        self.actor_user_id = actor_user_id
        self.actor_account_id = scope.account_id
        self.session_id = None
        self.authentication_method = "system"


# ── 工具函数 ──


def _per_item_key(batch_key: str | None, index: int) -> str | None:
    if not batch_key:
        return None
    return f"{batch_key}:{index}"


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, uuid.UUID | None]:
    if not cursor:
        return None, None
    try:
        ts, uid = cursor.split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(uid)
    except (ValueError, TypeError):
        raise ResourceError("RESOURCE_NOT_FOUND") from None


def _encode_cursor(created_at: datetime, ref_id: uuid.UUID) -> str:
    return f"{created_at.isoformat()}|{ref_id}"


def _is_admin(principal) -> bool:
    return ACCOUNT_ADMIN in (principal.role_codes or ())


def _default_name(blob, validation) -> str:
    if blob is not None:
        return blob.original_filename
    display = validation.display if validation is not None else "resource"
    tail = display.rstrip("/").rsplit("/", 1)[-1]
    return tail or display


def _blob_fingerprint(blob: UploadedBlob) -> str:
    return hashlib.sha256(f"{blob.storage_ref}|{blob.content_hash}".encode()).hexdigest()[:32]


def _source_dict(ref: PlatformContentRef, blob: UploadedBlob | None) -> dict:
    return {"source_type": ref.source_type, "source_display": ref.source_display, "blob": blob}


def _processing_state(ref: PlatformContentRef) -> str:
    if ref.status == "active":
        return "succeeded"
    if ref.status == "failed":
        return "failed"
    if ref.status == "provisioning":
        return "queued"
    return ref.status


def _children_of(records: list[NodeRecord], parent_path: str) -> list[NodeRecord]:
    """返回指定目录的直接子项（parent_path="" 表示根层）。"""
    prefix = f"{parent_path}/" if parent_path else ""
    children: dict[str, NodeRecord] = {}
    for node in records:
        if not node.path.startswith(prefix):
            continue
        rest = node.path[len(prefix):]
        if not rest:
            continue
        first = rest.split("/", 1)[0]
        child_path = f"{prefix}{first}".rstrip("/")
        if node.path == child_path:
            children[child_path] = node
        else:
            children.setdefault(
                child_path, NodeRecord(path=child_path, kind="dir", size_bytes=0, mime_type=None)
            )
    return sorted(children.values(), key=lambda n: (n.kind != "dir", n.name))


def _text_preview(node: NodeRecord) -> str | None:
    if node.kind != "file" or node.text is None:
        return None
    if node.mime_type is None or not node.mime_type.startswith(_INLINE_MIME_PREFIXES):
        return None
    return node.text[:_PREVIEW_BYTES].decode("utf-8", errors="replace")


def _error_message(code: str) -> str:
    return {
        "RESOURCE_UPLOAD_EXPIRED": "上传已过期，请重新上传文件。",
        "RESOURCE_UPLOAD_ALREADY_CONSUMED": "上传已被使用，请重新上传文件。",
        "RESOURCE_SOURCE_BLOCKED": "远程来源不符合安全策略，已拒绝。",
        "RESOURCE_SOURCE_UNSUPPORTED": "来源类型不受支持。",
        "RESOURCE_FILE_TOO_LARGE": "文件超过大小限制。",
        "RESOURCE_FORMAT_UNSUPPORTED": "文件格式不受支持。",
        "RESOURCE_NOT_FOUND": "Resource 不存在或不可见。",
    }.get(code, "请求失败。")
