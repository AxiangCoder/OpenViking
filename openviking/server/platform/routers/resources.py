"""Resource 产品 API 路由（09 §46，05 §12.5/§12.6，14 号计划 §97.3）。

入口范围（09 §40.1：路由 + 权限确定目标，客户端不能切换）：

- `/me/resources/*`：当前 User 私有（`resource.user_private.*.self`）；
- `/account/resources/*`：当前 Account 共享（读对普通 User 开放，
  写/Watch/删除仅 Account Admin，`resource.account_shared.*.account`）；
- `/platform/accounts/{id}/resources/*`：平台代管共享（`*.platform`）；
- `/platform/accounts/{id}/users/{uid}/resources`、`/admin/users/{uid}/resources`：
  成员私有只读（`resource.user_private.read.platform/.account`，不含下载/导出，
  AC⑪，05 §12.6）；
- `resources/capabilities`、`me|account|platform resource-uploads`：Capabilities
  与 Upload（09 §46.1）；
- `/activity`、`/recycle-bin`：跨对象聚合（05 §12.5）。

错误映射按 09 §46.5 稳定错误码；可见无写权限 403、不可见 404（AC⑧）。
"""

from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.db import get_session
from openviking.server.platform.deletion.service import DeletionService
from openviking.server.platform.dependencies import (
    get_current_principal,
    require_high_risk_write,
    require_permission,
    verify_csrf,
)
from openviking.server.platform.errors import (
    AccessDeniedError,
    AdminActionForbiddenError,
    CanonicalUriMismatchError,
    DeletionJobError,
    EntityNotFoundError,
    ResourceError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.resource.service import ResourceScope, ResourceService

router = APIRouter(prefix="/api/platform/v1", tags=["resources"])

# 09 §46.5 稳定错误码 → HTTP 状态（服务层 reason 与对外 code 一致）
_RESOURCE_STATUS = {
    "RESOURCE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "RESOURCE_BUSY": status.HTTP_409_CONFLICT,
    "RESOURCE_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
    "RESOURCE_SOURCE_UNSUPPORTED": status.HTTP_400_BAD_REQUEST,
    "RESOURCE_SOURCE_NOT_STABLE": status.HTTP_409_CONFLICT,
    "RESOURCE_SOURCE_BLOCKED": status.HTTP_403_FORBIDDEN,
    "RESOURCE_UPLOAD_EXPIRED": status.HTTP_400_BAD_REQUEST,
    "RESOURCE_UPLOAD_ALREADY_CONSUMED": status.HTTP_409_CONFLICT,
    "RESOURCE_FILE_TOO_LARGE": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "RESOURCE_FORMAT_UNSUPPORTED": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "RESOURCE_WATCH_UNAVAILABLE": status.HTTP_409_CONFLICT,
    "RESOURCE_WATCH_CONFLICT": status.HTTP_409_CONFLICT,
    "RESOURCE_OPERATION_NOT_CANCELLABLE": status.HTTP_409_CONFLICT,
    "RESOURCE_DELETION_PENDING": status.HTTP_409_CONFLICT,
    "RESOURCE_RESTORE_WINDOW_EXPIRED": status.HTTP_409_CONFLICT,
    "RESOURCE_PUBLISH_FORBIDDEN": status.HTTP_403_FORBIDDEN,
    "PERMISSION_NOT_GRANTED": status.HTTP_403_FORBIDDEN,
}

_PERMISSION_STATUS = {
    "PERMISSION_NOT_GRANTED": status.HTTP_403_FORBIDDEN,
    "INTERNAL_TARGET_DENIED": status.HTTP_403_FORBIDDEN,
    "SUBJECT_MISMATCH": status.HTTP_404_NOT_FOUND,
    "SUBJECT_REQUIRED": status.HTTP_404_NOT_FOUND,
    "CROSS_ACCOUNT_ACCESS": status.HTTP_404_NOT_FOUND,
}


class ImportItem(BaseModel):
    upload_id: str | None = None
    source_url: str | None = None
    is_git: bool = False
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = None
    instruction: str | None = Field(default=None, max_length=2000)


class ImportRequest(BaseModel):
    items: list[ImportItem] = Field(min_length=1)


class PatchResourceRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = None
    version: int | None = None


class RetryRequest(BaseModel):
    upload_id: str | None = None
    source_url: str | None = None


class WatchRequest(BaseModel):
    interval_minutes: int
    processing_instruction: str | None = Field(default=None, max_length=2000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _resources(request: Request) -> ResourceService:
    return request.app.state.iam_resource_service


def _deletion(request: Request) -> DeletionService:
    return request.app.state.iam_deletion_service


def _iam(request: Request) -> IamRepository:
    return request.app.state.iam_repository


def _scope_me(principal) -> ResourceScope:
    if principal.actor_account_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "RESOURCE_NOT_FOUND"})
    return ResourceScope(
        visibility="user_private",
        account_id=principal.actor_account_id,
        owner_user_id=principal.actor_user_id,
        kind="me",
    )


def _scope_account(principal) -> ResourceScope:
    if principal.actor_account_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "RESOURCE_NOT_FOUND"})
    return ResourceScope(
        visibility="account_shared",
        account_id=principal.actor_account_id,
        owner_user_id=None,
        kind="account",
    )


def _scope_platform(account_id: uuid.UUID) -> ResourceScope:
    return ResourceScope(
        visibility="account_shared",
        account_id=account_id,
        owner_user_id=None,
        kind="platform",
    )


async def _scope_member(session: AsyncSession, iam: IamRepository, account_id: uuid.UUID, user_id: uuid.UUID, kind: str) -> ResourceScope:
    user = await iam.get_user(session, user_id)
    if user is None or user.deleted_at is not None or user.account_id != account_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "RESOURCE_NOT_FOUND"})
    return ResourceScope(
        visibility="user_private",
        account_id=account_id,
        owner_user_id=user_id,
        kind=kind,
    )


def _resource_error(exc: Exception):
    """09 §46.5 稳定错误码映射（AC⑧：可见无写权限 403、不可见 404）。"""
    if isinstance(exc, ResourceError):
        http_status = _RESOURCE_STATUS.get(exc.reason, status.HTTP_400_BAD_REQUEST)
        detail = {"code": exc.reason}
        if getattr(exc, "retryable", False):
            detail["retryable"] = True
        raise HTTPException(http_status, detail=detail) from exc
    if isinstance(exc, EntityNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "RESOURCE_NOT_FOUND"}) from exc
    if isinstance(exc, AccessDeniedError):
        http_status = _PERMISSION_STATUS.get(exc.reason, status.HTTP_403_FORBIDDEN)
        raise HTTPException(http_status, detail={"code": exc.reason}) from exc
    if isinstance(exc, CanonicalUriMismatchError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"code": "RESOURCE_SOURCE_UNSUPPORTED"}
        ) from exc
    if isinstance(exc, DeletionJobError):
        reason = {"RESTORE_WINDOW_EXPIRED": "RESOURCE_RESTORE_WINDOW_EXPIRED"}.get(
            exc.reason, exc.reason
        )
        http_status = _RESOURCE_STATUS.get(reason, status.HTTP_409_CONFLICT)
        raise HTTPException(http_status, detail={"code": reason}) from exc
    if isinstance(exc, AdminActionForbiddenError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.reason}) from exc
    raise exc


def _not_found(exc: Exception):
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "RESOURCE_NOT_FOUND"}) from exc


# ═══════════════════════════════════════════════════════════════════════
# Capabilities（09 §46.1）
# ═══════════════════════════════════════════════════════════════════════


@router.get("/resources/capabilities")
async def resource_capabilities(
    request: Request,
    principal=Depends(get_current_principal),
):
    return {"status": "ok", "result": _resources(request).capabilities()}


# ═══════════════════════════════════════════════════════════════════════
# Upload（09 §46.1：三入口）
# ═══════════════════════════════════════════════════════════════════════


async def _create_upload(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    file: UploadFile,
):
    data = await file.read()
    service = _resources(request)
    try:
        upload = await service.create_upload(
            session,
            principal=principal,
            scope=scope,
            filename=file.filename or "upload",
            data=data,
            declared_mime=file.content_type,
        )
    except ResourceError as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "upload_id": str(upload.id),
            "filename": upload.original_filename,
            "mime_type": upload.mime_type,
            "size_bytes": upload.size_bytes,
            "expires_at": upload.expires_at.isoformat(),
        },
    }


@router.post(
    "/me/resource-uploads",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_resource_upload(
    request: Request,
    file: UploadFile = File(...),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _create_upload(request, session, principal, _scope_me(principal), file)


@router.post(
    "/account/resource-uploads",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_resource_upload(
    request: Request,
    file: UploadFile = File(...),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _create_upload(request, session, principal, _scope_account(principal), file)


@router.post(
    "/platform/accounts/{account_id}/resource-uploads",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_resource_upload(
    request: Request,
    account_id: uuid.UUID,
    file: UploadFile = File(...),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _create_upload(request, session, principal, _scope_platform(account_id), file)


# ═══════════════════════════════════════════════════════════════════════
# me（09 §46.2 私有 Resource API）
# ═══════════════════════════════════════════════════════════════════════


@router.get("/me/resources", dependencies=[Depends(require_permission("resource.user_private.read.self"))])
async def me_list_resources(
    request: Request,
    source_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        items, next_cursor = await service.list_resources(
            session,
            principal=principal,
            scope=_scope_me(principal),
            source_type=source_type,
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except ResourceError as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": next_cursor}}


@router.post(
    "/me/resources/imports",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_import_resources(
    request: Request,
    body: ImportRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    service = _resources(request)
    try:
        batch = await service.import_resources(
            session,
            principal=principal,
            scope=_scope_me(principal),
            items=[item.model_dump() for item in body.items],
            idempotency_key=idempotency_key,
            request_id=_request_id(request),
        )
    except ResourceError as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    items = await service.process_import_batch(
        session,
        batch=batch,
        principal=principal,
        scope=_scope_me(principal),
        request_id=_request_id(request),
    )
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "batch_id": batch["batch_id"],
            "items": items,
        },
    }


@router.get("/me/resources/{resource_id}", dependencies=[Depends(require_permission("resource.user_private.read.self"))])
async def me_get_resource(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        detail = await service.get_resource(
            session,
            principal=principal,
            scope=_scope_me(principal),
            resource_id=resource_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": detail}


@router.patch(
    "/me/resources/{resource_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_patch_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: PatchResourceRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    if_match: str | None = Header(default=None),
):
    service = _resources(request)
    version = body.version
    if if_match and version is None:
        try:
            version = int(if_match)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "RESOURCE_VERSION_CONFLICT"}
            ) from None
    try:
        item = await service.patch_resource(
            session,
            principal=principal,
            scope=_scope_me(principal),
            resource_id=resource_id,
            display_name=body.display_name,
            description=body.description,
            tags=body.tags,
            version=version,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": item}


async def _replace(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    upload_id: uuid.UUID,
):
    service = _resources(request)
    try:
        result = await service.replace_resource(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            upload_id=upload_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


class ReplaceRequest(BaseModel):
    upload_id: uuid.UUID


@router.post(
    "/me/resources/{resource_id}/replace",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_replace_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: ReplaceRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _replace(request, session, principal, _scope_me(principal), resource_id, body.upload_id)


async def _refresh(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
):
    service = _resources(request)
    try:
        result = await service.refresh_resource(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.post(
    "/me/resources/{resource_id}/refresh",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_refresh_resource(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _refresh(request, session, principal, _scope_me(principal), resource_id)


async def _retry(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    body: RetryRequest,
):
    service = _resources(request)
    try:
        result = await service.retry_resource(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            upload_id=uuid.UUID(body.upload_id) if body.upload_id else None,
            source_url=body.source_url,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.post(
    "/me/resources/{resource_id}/retry",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_retry_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: RetryRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _retry(request, session, principal, _scope_me(principal), resource_id, body)


def _publish_permission():
    """发布：私有 read（05 §12.5 另需 `resource.account_shared.write.account`，
    由服务层以 Account Admin 角色强校验 → 稳定码 `RESOURCE_PUBLISH_FORBIDDEN`）。"""

    def checker(principal=Depends(get_current_principal)):
        if "resource.user_private.read.self" not in principal.permissions:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail={"code": "PERMISSION_NOT_GRANTED"}
            )
        return principal

    return checker


@router.post("/me/resources/{resource_id}/publish", dependencies=[Depends(verify_csrf), Depends(_publish_permission())])
async def me_publish_resource(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    service = _resources(request)
    try:
        result = await service.publish_resource(
            session,
            principal=principal,
            scope=_scope_me(principal),
            resource_id=resource_id,
            idempotency_key=idempotency_key,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


# ── Watch（09 §46.2 私有）──


@router.get(
    "/me/resources/{resource_id}/watch",
    dependencies=[Depends(require_permission("resource.user_private.read.self"))],
)
async def me_get_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.watch_config(
            session, principal=principal, scope=_scope_me(principal), resource_id=resource_id
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": config}


@router.put(
    "/me/resources/{resource_id}/watch",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_put_watch(
    request: Request,
    resource_id: uuid.UUID,
    body: WatchRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.configure_watch(
            session,
            principal=principal,
            scope=_scope_me(principal),
            resource_id=resource_id,
            interval_minutes=body.interval_minutes,
            instruction=body.processing_instruction,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/me/resources/{resource_id}/watch/pause",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_pause_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.pause_watch(
            session, principal=principal, scope=_scope_me(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/me/resources/{resource_id}/watch/resume",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_resume_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.resume_watch(
            session, principal=principal, scope=_scope_me(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/me/resources/{resource_id}/watch/trigger",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_trigger_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        result = await service.trigger_watch(
            session, principal=principal, scope=_scope_me(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.delete(
    "/me/resources/{resource_id}/watch",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.user_private.write.self"))],
)
async def me_delete_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        await service.delete_watch(
            session, principal=principal, scope=_scope_me(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok"}


# ── Operations（09 §46.2 Activity）──


@router.get(
    "/me/resources/{resource_id}/operations",
    dependencies=[Depends(require_permission("task.read.self"))],
)
async def me_list_operations(
    request: Request,
    resource_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        items = await service.list_operations(
            session,
            principal=principal,
            scope=_scope_me(principal),
            resource_id=resource_id,
            limit=limit,
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": None}}


async def _cancel_operation(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    operation_id: uuid.UUID,
):
    service = _resources(request)
    try:
        result = await service.cancel_operation(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            operation_id=operation_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.post(
    "/me/resources/{resource_id}/operations/{operation_id}/cancel",
    dependencies=[Depends(verify_csrf), Depends(require_permission("task.cancel.self"))],
)
async def me_cancel_operation(
    request: Request,
    resource_id: uuid.UUID,
    operation_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _cancel_operation(
        request, session, principal, _scope_me(principal), resource_id, operation_id
    )


# ── Nodes / 搜索（09 §46.2，AC⑨）──


async def _list_nodes(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    node_id: str | None,
    limit: int,
):
    service = _resources(request)
    try:
        items = await service.list_nodes(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            node_id=node_id,
            limit=limit,
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": None}}


@router.get(
    "/me/resources/{resource_id}/nodes",
    dependencies=[Depends(require_permission("resource.user_private.read.self"))],
)
async def me_list_nodes(
    request: Request,
    resource_id: uuid.UUID,
    node_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _list_nodes(request, session, principal, _scope_me(principal), resource_id, node_id, limit)


async def _read_node(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    node_id: str,
):
    service = _resources(request)
    try:
        node = await service.read_node(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            node_id=node_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": node}


@router.get(
    "/me/resources/{resource_id}/nodes/{node_id}",
    dependencies=[Depends(require_permission("resource.user_private.read.self"))],
)
async def me_read_node(
    request: Request,
    resource_id: uuid.UUID,
    node_id: str,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _read_node(request, session, principal, _scope_me(principal), resource_id, node_id)


async def _download_node(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    node_id: str,
):
    service = _resources(request)
    try:
        result = await service.download_node(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            node_id=node_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    from fastapi.responses import Response

    content = result["content"]
    mime = result["mime_type"]
    disposition = "inline" if result["inline"] else "attachment"
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f'{disposition}; filename="{result["filename"]}"',
        },
    )


@router.get(
    "/me/resources/{resource_id}/nodes/{node_id}/download",
    dependencies=[Depends(require_permission("resource.user_private.read.self"))],
)
async def me_download_node(
    request: Request,
    resource_id: uuid.UUID,
    node_id: str,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _download_node(request, session, principal, _scope_me(principal), resource_id, node_id)


async def _search_resource(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    body: SearchRequest,
):
    service = _resources(request)
    try:
        result = await service.search_resource(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            query=body.query,
            limit=body.limit,
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": result}


@router.post(
    "/me/resources/{resource_id}/search",
    dependencies=[Depends(require_permission("resource.user_private.read.self"))],
)
async def me_search_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: SearchRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _search_resource(request, session, principal, _scope_me(principal), resource_id, body)


# ── 删除（09 §46.2）──


async def _deletion_preview(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
):
    service = _resources(request)
    try:
        preview = await service.deletion_preview(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": preview}


@router.get(
    "/me/resources/{resource_id}/deletion-preview",
    dependencies=[Depends(require_permission("resource.user_private.delete.self"))],
)
async def me_deletion_preview(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _deletion_preview(request, session, principal, _scope_me(principal), resource_id)


async def _delete_resource(
    request: Request,
    session: AsyncSession,
    principal,
    scope: ResourceScope,
    resource_id: uuid.UUID,
    version: int | None = None,
):
    service = _resources(request)
    try:
        result = await service.delete_resource(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            version=version,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.delete(
    "/me/resources/{resource_id}",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="resource.delete",
                permission_code="resource.user_private.delete.self",
                scope="self",
            )
        )
    ],
)
async def me_delete_resource(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    if_match: str | None = Header(default=None),
):
    version = None
    if if_match:
        try:
            version = int(if_match)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "RESOURCE_VERSION_CONFLICT"}
            ) from None
    return await _delete_resource(request, session, principal, _scope_me(principal), resource_id, version)


# ═══════════════════════════════════════════════════════════════════════
# account（09 §46.3 共享 Resource API：读对普通 User 开放）
# ═══════════════════════════════════════════════════════════════════════


@router.get(
    "/account/resources",
    dependencies=[Depends(require_permission("resource.account_shared.read.account"))],
)
async def account_list_resources(
    request: Request,
    source_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        items, next_cursor = await service.list_resources(
            session,
            principal=principal,
            scope=_scope_account(principal),
            source_type=source_type,
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except ResourceError as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": next_cursor}}


@router.post(
    "/account/resources/imports",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_import_resources(
    request: Request,
    body: ImportRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    service = _resources(request)
    scope = _scope_account(principal)
    try:
        batch = await service.import_resources(
            session,
            principal=principal,
            scope=scope,
            items=[item.model_dump() for item in body.items],
            idempotency_key=idempotency_key,
            request_id=_request_id(request),
        )
    except ResourceError as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    items = await service.process_import_batch(
        session, batch=batch, principal=principal, scope=scope, request_id=_request_id(request)
    )
    await session.commit()
    return {"status": "ok", "result": {"batch_id": batch["batch_id"], "items": items}}


@router.get(
    "/account/resources/{resource_id}",
    dependencies=[Depends(require_permission("resource.account_shared.read.account"))],
)
async def account_get_resource(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        detail = await service.get_resource(
            session,
            principal=principal,
            scope=_scope_account(principal),
            resource_id=resource_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": detail}


@router.patch(
    "/account/resources/{resource_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_patch_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: PatchResourceRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    if_match: str | None = Header(default=None),
):
    service = _resources(request)
    version = body.version
    if if_match and version is None:
        try:
            version = int(if_match)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "RESOURCE_VERSION_CONFLICT"}
            ) from None
    try:
        item = await service.patch_resource(
            session,
            principal=principal,
            scope=_scope_account(principal),
            resource_id=resource_id,
            display_name=body.display_name,
            description=body.description,
            tags=body.tags,
            version=version,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": item}


@router.post(
    "/account/resources/{resource_id}/replace",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_replace_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: ReplaceRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _replace(request, session, principal, _scope_account(principal), resource_id, body.upload_id)


@router.post(
    "/account/resources/{resource_id}/refresh",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_refresh_resource(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _refresh(request, session, principal, _scope_account(principal), resource_id)


@router.post(
    "/account/resources/{resource_id}/retry",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_retry_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: RetryRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _retry(request, session, principal, _scope_account(principal), resource_id, body)


# account Watch（读开放，写仅 Account Admin）


@router.get(
    "/account/resources/{resource_id}/watch",
    dependencies=[Depends(require_permission("resource.account_shared.read.account"))],
)
async def account_get_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.watch_config(
            session, principal=principal, scope=_scope_account(principal), resource_id=resource_id
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": config}


@router.put(
    "/account/resources/{resource_id}/watch",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_put_watch(
    request: Request,
    resource_id: uuid.UUID,
    body: WatchRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.configure_watch(
            session,
            principal=principal,
            scope=_scope_account(principal),
            resource_id=resource_id,
            interval_minutes=body.interval_minutes,
            instruction=body.processing_instruction,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/account/resources/{resource_id}/watch/pause",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_pause_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.pause_watch(
            session, principal=principal, scope=_scope_account(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/account/resources/{resource_id}/watch/resume",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_resume_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.resume_watch(
            session, principal=principal, scope=_scope_account(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/account/resources/{resource_id}/watch/trigger",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_trigger_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        result = await service.trigger_watch(
            session, principal=principal, scope=_scope_account(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.delete(
    "/account/resources/{resource_id}/watch",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.account"))],
)
async def account_delete_watch(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        await service.delete_watch(
            session, principal=principal, scope=_scope_account(principal), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok"}


@router.get(
    "/account/resources/{resource_id}/operations",
    dependencies=[Depends(require_permission("task.read.account_shared"))],
)
async def account_list_operations(
    request: Request,
    resource_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        items = await service.list_operations(
            session,
            principal=principal,
            scope=_scope_account(principal),
            resource_id=resource_id,
            limit=limit,
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": None}}


@router.post(
    "/account/resources/{resource_id}/operations/{operation_id}/cancel",
    dependencies=[Depends(verify_csrf), Depends(require_permission("task.cancel.account_shared"))],
)
async def account_cancel_operation(
    request: Request,
    resource_id: uuid.UUID,
    operation_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _cancel_operation(
        request, session, principal, _scope_account(principal), resource_id, operation_id
    )


@router.get(
    "/account/resources/{resource_id}/nodes",
    dependencies=[Depends(require_permission("resource.account_shared.read.account"))],
)
async def account_list_nodes(
    request: Request,
    resource_id: uuid.UUID,
    node_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _list_nodes(request, session, principal, _scope_account(principal), resource_id, node_id, limit)


@router.get(
    "/account/resources/{resource_id}/nodes/{node_id}",
    dependencies=[Depends(require_permission("resource.account_shared.read.account"))],
)
async def account_read_node(
    request: Request,
    resource_id: uuid.UUID,
    node_id: str,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _read_node(request, session, principal, _scope_account(principal), resource_id, node_id)


@router.get(
    "/account/resources/{resource_id}/nodes/{node_id}/download",
    dependencies=[Depends(require_permission("resource.account_shared.read.account"))],
)
async def account_download_node(
    request: Request,
    resource_id: uuid.UUID,
    node_id: str,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _download_node(request, session, principal, _scope_account(principal), resource_id, node_id)


@router.post(
    "/account/resources/{resource_id}/search",
    dependencies=[Depends(require_permission("resource.account_shared.read.account"))],
)
async def account_search_resource(
    request: Request,
    resource_id: uuid.UUID,
    body: SearchRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _search_resource(request, session, principal, _scope_account(principal), resource_id, body)


@router.get(
    "/account/resources/{resource_id}/deletion-preview",
    dependencies=[Depends(require_permission("resource.account_shared.delete.account"))],
)
async def account_deletion_preview(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _deletion_preview(request, session, principal, _scope_account(principal), resource_id)


@router.delete(
    "/account/resources/{resource_id}",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="resource.delete",
                permission_code="resource.account_shared.delete.account",
                scope="account",
            )
        )
    ],
)
async def account_delete_resource(
    request: Request,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    if_match: str | None = Header(default=None),
):
    version = None
    if if_match:
        try:
            version = int(if_match)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "RESOURCE_VERSION_CONFLICT"}
            ) from None
    return await _delete_resource(request, session, principal, _scope_account(principal), resource_id, version)


# ═══════════════════════════════════════════════════════════════════════
# platform 代管共享（05 §12.6 平台表）
# ═══════════════════════════════════════════════════════════════════════


@router.get(
    "/platform/accounts/{account_id}/resources",
    dependencies=[Depends(require_permission("resource.account_shared.read.platform"))],
)
async def platform_list_resources(
    request: Request,
    account_id: uuid.UUID,
    source_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        items, next_cursor = await service.list_resources(
            session,
            principal=principal,
            scope=_scope_platform(account_id),
            source_type=source_type,
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except ResourceError as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": next_cursor}}


@router.post(
    "/platform/accounts/{account_id}/resources/imports",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_import_resources(
    request: Request,
    account_id: uuid.UUID,
    body: ImportRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    service = _resources(request)
    scope = _scope_platform(account_id)
    try:
        batch = await service.import_resources(
            session,
            principal=principal,
            scope=scope,
            items=[item.model_dump() for item in body.items],
            idempotency_key=idempotency_key,
            request_id=_request_id(request),
        )
    except ResourceError as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    items = await service.process_import_batch(
        session, batch=batch, principal=principal, scope=scope, request_id=_request_id(request)
    )
    await session.commit()
    return {"status": "ok", "result": {"batch_id": batch["batch_id"], "items": items}}


@router.get(
    "/platform/accounts/{account_id}/resources/{resource_id}",
    dependencies=[Depends(require_permission("resource.account_shared.read.platform"))],
)
async def platform_get_resource(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        detail = await service.get_resource(
            session,
            principal=principal,
            scope=_scope_platform(account_id),
            resource_id=resource_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": detail}


@router.patch(
    "/platform/accounts/{account_id}/resources/{resource_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_patch_resource(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    body: PatchResourceRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    if_match: str | None = Header(default=None),
):
    service = _resources(request)
    version = body.version
    if if_match and version is None:
        try:
            version = int(if_match)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "RESOURCE_VERSION_CONFLICT"}
            ) from None
    try:
        item = await service.patch_resource(
            session,
            principal=principal,
            scope=_scope_platform(account_id),
            resource_id=resource_id,
            display_name=body.display_name,
            description=body.description,
            tags=body.tags,
            version=version,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": item}


@router.post(
    "/platform/accounts/{account_id}/resources/{resource_id}/refresh",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_refresh_resource(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _refresh(
        request, session, principal, _scope_platform(account_id), resource_id
    )


@router.delete(
    "/platform/accounts/{account_id}/resources/{resource_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.delete.platform"))],
)
async def platform_delete_resource(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    if_match: str | None = Header(default=None),
):
    version = None
    if if_match:
        try:
            version = int(if_match)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "RESOURCE_VERSION_CONFLICT"}
            ) from None
    return await _delete_resource(
        request, session, principal, _scope_platform(account_id), resource_id, version
    )


@router.get(
    "/platform/accounts/{account_id}/resources/{resource_id}/watch",
    dependencies=[Depends(require_permission("resource.account_shared.read.platform"))],
)
async def platform_get_watch(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.watch_config(
            session, principal=principal, scope=_scope_platform(account_id), resource_id=resource_id
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": config}


@router.put(
    "/platform/accounts/{account_id}/resources/{resource_id}/watch",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_put_watch(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    body: WatchRequest,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.configure_watch(
            session,
            principal=principal,
            scope=_scope_platform(account_id),
            resource_id=resource_id,
            interval_minutes=body.interval_minutes,
            instruction=body.processing_instruction,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/platform/accounts/{account_id}/resources/{resource_id}/watch/pause",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_pause_watch(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.pause_watch(
            session, principal=principal, scope=_scope_platform(account_id), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/platform/accounts/{account_id}/resources/{resource_id}/watch/resume",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_resume_watch(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        config = await service.resume_watch(
            session, principal=principal, scope=_scope_platform(account_id), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": config}


@router.post(
    "/platform/accounts/{account_id}/resources/{resource_id}/watch/trigger",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_trigger_watch(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        result = await service.trigger_watch(
            session, principal=principal, scope=_scope_platform(account_id), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.delete(
    "/platform/accounts/{account_id}/resources/{resource_id}/watch",
    dependencies=[Depends(verify_csrf), Depends(require_permission("resource.account_shared.write.platform"))],
)
async def platform_delete_watch(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        await service.delete_watch(
            session, principal=principal, scope=_scope_platform(account_id), resource_id=resource_id, request_id=_request_id(request)
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok"}


@router.get(
    "/platform/accounts/{account_id}/resources/{resource_id}/nodes",
    dependencies=[Depends(require_permission("resource.account_shared.read.platform"))],
)
async def platform_list_nodes(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    node_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    return await _list_nodes(
        request, session, principal, _scope_platform(account_id), resource_id, node_id, limit
    )


@router.get(
    "/platform/accounts/{account_id}/resources/{resource_id}/operations",
    dependencies=[Depends(require_permission("task.read.platform"))],
)
async def platform_list_operations(
    request: Request,
    account_id: uuid.UUID,
    resource_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        items = await service.list_operations(
            session,
            principal=principal,
            scope=_scope_platform(account_id),
            resource_id=resource_id,
            limit=limit,
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": None}}


# ═══════════════════════════════════════════════════════════════════════
# admin/platform 成员私有只读（AC⑪：不含下载/导出，05 §12.6）
# ═══════════════════════════════════════════════════════════════════════


@router.get(
    "/admin/users/{user_id}/resources",
    dependencies=[Depends(require_permission("resource.user_private.read.account"))],
)
async def admin_member_resources(
    request: Request,
    user_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        scope = await _scope_member(session, _iam(request), principal.actor_account_id, user_id, "member")
        items, next_cursor = await service.list_resources(
            session, principal=principal, scope=scope, limit=limit, cursor=cursor
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": next_cursor}}


@router.get(
    "/admin/users/{user_id}/resources/{resource_id}",
    dependencies=[Depends(require_permission("resource.user_private.read.account"))],
)
async def admin_member_resource(
    request: Request,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        scope = await _scope_member(session, _iam(request), principal.actor_account_id, user_id, "member")
        detail = await service.get_resource(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await _audit_member_read(request, session, principal, scope, resource_id)
    return {"status": "ok", "result": detail}


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/resources",
    dependencies=[Depends(require_permission("resource.user_private.read.platform"))],
)
async def platform_member_resources(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        scope = await _scope_member(session, _iam(request), account_id, user_id, "member")
        items, next_cursor = await service.list_resources(
            session, principal=principal, scope=scope, limit=limit, cursor=cursor
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    return {"status": "ok", "result": {"items": items, "next_cursor": next_cursor}}


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/resources/{resource_id}",
    dependencies=[Depends(require_permission("resource.user_private.read.platform"))],
)
async def platform_member_resource(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        scope = await _scope_member(session, _iam(request), account_id, user_id, "member")
        detail = await service.get_resource(
            session,
            principal=principal,
            scope=scope,
            resource_id=resource_id,
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await _audit_member_read(request, session, principal, scope, resource_id)
    return {"status": "ok", "result": detail}


async def _audit_member_read(request: Request, session: AsyncSession, principal, scope: ResourceScope, resource_id: uuid.UUID) -> None:
    """管理员/平台跨用户读取审计（09 §47.2：管理员/平台跨用户读取）。"""
    await _resources(request).audit_member_read(
        session,
        principal=principal,
        scope=scope,
        resource_id=resource_id,
        request_id=_request_id(request),
    )


# ═══════════════════════════════════════════════════════════════════════
# 跨对象聚合（05 §12.5：/activity、/recycle-bin）
# ═══════════════════════════════════════════════════════════════════════


def _activity_permission():
    """`/activity`：`task.read.self`；共享项另需 `task.read.account_shared`（05 §12.5）。"""

    def checker(principal=Depends(get_current_principal)):
        if "task.read.self" not in principal.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "PERMISSION_NOT_GRANTED"})
        return principal

    return checker


@router.get("/activity", dependencies=[Depends(_activity_permission())])
async def me_activity(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    items = await service.list_user_activity(session, principal=principal, limit=limit)
    return {"status": "ok", "result": {"items": items, "next_cursor": None}}


def _cancel_self_permission():
    """`/activity/{id}/cancel`：`task.cancel.self` 或 `task.cancel.account_shared`（09 §43.3）。"""

    def checker(principal=Depends(get_current_principal)):
        if not {"task.cancel.self", "task.cancel.account_shared"} & principal.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "PERMISSION_NOT_GRANTED"})
        return principal

    return checker


@router.post("/activity/{operation_id}/cancel", dependencies=[Depends(verify_csrf), Depends(_cancel_self_permission())])
async def me_cancel_activity(
    request: Request,
    operation_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        result = await service.cancel_activity_operation(
            session,
            principal=principal,
            operation_id=operation_id,
            scope_kind="self",
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.post(
    "/admin/activity/{operation_id}/cancel",
    dependencies=[Depends(verify_csrf), Depends(require_permission("task.cancel.account_shared"))],
)
async def admin_cancel_activity(
    request: Request,
    operation_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        result = await service.cancel_activity_operation(
            session,
            principal=principal,
            operation_id=operation_id,
            scope_kind="account",
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


@router.post(
    "/platform/activity/{operation_id}/cancel",
    dependencies=[Depends(verify_csrf), Depends(require_permission("task.cancel.platform"))],
)
async def platform_cancel_activity(
    request: Request,
    operation_id: uuid.UUID,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    service = _resources(request)
    try:
        result = await service.cancel_activity_operation(
            session,
            principal=principal,
            operation_id=operation_id,
            scope_kind="platform",
            request_id=_request_id(request),
        )
    except (ResourceError, EntityNotFoundError) as exc:
        await session.commit()
        raise _resource_error(exc)
    await session.commit()
    return {"status": "ok", "result": result}


# ── recycle-bin（05 §12.5：当前用户回收站；恢复按对象类型校验）──


def _recycle_bin_row_dto(row) -> dict:
    job = row.job
    return {
        "id": str(job.id),
        "resource_type": job.resource_type,
        "resource_id": job.resource_id,
        "target_name": row.target_name,
        "deleted_at": job.deleted_at.isoformat(),
        "purge_after": job.purge_after.isoformat(),
        "status": job.status,
        "restore_allowed": row.restore_allowed,
        "restore_permission": row.restore_permission,
        "restored_at": job.restored_at.isoformat() if job.restored_at else None,
    }


@router.get("/recycle-bin")
async def me_recycle_bin(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal=Depends(get_current_principal),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    if principal.actor_account_id is None:
        return {"status": "ok", "result": {"items": [], "next_cursor": None}}
    rows = await deletion.list_recycle_bin(
        session,
        scope="self",
        principal=principal,
        account_id=principal.actor_account_id,
        limit=limit,
    )
    return {
        "status": "ok",
        "result": {"items": [_recycle_bin_row_dto(r) for r in rows], "next_cursor": None},
    }


@router.post("/recycle-bin/{job_id}/restore", dependencies=[Depends(verify_csrf)])
async def me_restore_resource(
    request: Request,
    job_id: uuid.UUID,
    principal=Depends(get_current_principal),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await deletion.restore(
            session,
            scope="self",
            principal=principal,
            account_id=principal.actor_account_id,
            job_id=job_id,
            request_id=_request_id(request),
        )
    except (DeletionJobError, EntityNotFoundError, AdminActionForbiddenError) as exc:
        raise _resource_error(exc)
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "deletion_job_id": str(result.deletion_job_id),
            "deleted_at": result.deleted_at,
            "restore_until": result.restore_until,
        },
    }
