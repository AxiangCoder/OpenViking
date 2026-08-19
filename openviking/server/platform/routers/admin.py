"""Admin 管理 API（05 §12.6 `/api/platform/v1/admin/*`，14 号计划 §96.5）。

- `GET/POST /admin/users`：列表 / Account Admin 直建 User（角色固定 `user`，
  初始密码只返回一次，03 §8.3）；
- `PATCH /admin/users/{id}`：display_name / status（active|disabled）；
  `POST /admin/users/{id}/disable`：禁用即时杀 Session + 全部 API Key（AC ⑤）；
- `POST /admin/users/{id}/password/reset`：分级重置（目标必须普通 User，03 §8.3）；
- `GET /admin/roles`：三内置角色只读（03 §9.2/9.3）；
- `GET /admin/audit-events`：当前 Account 审计（04 §10.8）；
- `GET /admin/users/{id}/api-keys`、`DELETE .../{credential_id}`：API Key
  元数据只读/撤销（P1-E4 未合并前直接用 E1 repository 实现，合并后协调者验证）。

Account 固定来自当前登录 Session（05 §12.6）；跨 Account 目标统一 404（防枚举）。
写操作全部要求登录 Session + CSRF（03 §8.2）；权限按 05 §12.6 表逐端点校验。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.admin.service import (
    AdminService,
    CreatedUserResult,
    UserRow,
    decode_cursor,
)
from openviking.server.platform.db import get_session
from openviking.server.platform.dependencies import (
    get_current_principal,
    get_iam_services,
    require_high_risk_write,
    require_permission,
    verify_csrf,
)
from openviking.server.platform.errors import (
    AdminActionForbiddenError,
    ConstraintViolationError,
    DeletionJobError,
    EntityNotFoundError,
    InvalidCursorError,
    LastAccountAdminError,
    PasswordResetForbiddenError,
)
from openviking.server.platform.iam.service import RbacService

router = APIRouter(prefix="/api/platform/v1/admin", tags=["admin"])

NOT_FOUND = "NOT_FOUND"


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    username: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class PatchUserRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "PatchUserRequest":
        if self.display_name is None and self.status is None:
            raise ValueError("at least one of display_name/status must be provided")
        return self


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _admin(request: Request) -> AdminService:
    return request.app.state.iam_admin_service


def _rbac(request: Request) -> RbacService:
    return get_iam_services(request).rbac


def constraint_code(exc: ConstraintViolationError) -> str:
    """稳定错误码：服务层预检查直接给出码（ACCOUNT_CODE_ALREADY_EXISTS 等）；
    DB 约束兜底统一 409 CONSTRAINT_VIOLATION。"""
    message = str(exc)
    if len(message) <= 64 and message.replace("_", "").isalnum() and message.isupper():
        return message
    return "CONSTRAINT_VIOLATION"


def user_dto(row: UserRow) -> dict:
    u = row.user
    return {
        "id": str(u.id),
        "username": u.username,
        "email": u.email,
        "display_name": u.display_name,
        "status": u.status,
        "role": row.role_code,
        "ov_user_id": u.ov_user_id,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


def created_user_dto(result: CreatedUserResult) -> dict:
    dto = user_dto(UserRow(user=result.user, role_code="user"))
    dto["initial_password"] = result.initial_password
    return dto


def api_key_dto(credential) -> dict:
    return {
        "id": str(credential.id),
        "name": credential.name,
        "key_last_four": credential.key_last_four,
        "status": credential.status,
        "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
        "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
        "created_at": credential.created_at.isoformat() if credential.created_at else None,
        "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
    }


def audit_dto(event) -> dict:
    return {
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "request_id": event.request_id,
        "account_id": str(event.account_id) if event.account_id else None,
        "actor_type": event.actor_type,
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "actor_account_id": str(event.actor_account_id) if event.actor_account_id else None,
        "actor_system_component": event.actor_system_component,
        "actor_session_id": str(event.actor_session_id) if event.actor_session_id else None,
        "authentication_method": event.authentication_method,
        "actor_credential_id": str(event.actor_credential_id) if event.actor_credential_id else None,
        "subject_account_id": str(event.subject_account_id) if event.subject_account_id else None,
        "subject_user_id": str(event.subject_user_id) if event.subject_user_id else None,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "target_visibility": event.target_visibility,
        "scope": event.scope,
        "result": event.result,
        "reason": event.reason,
        "metadata": event.metadata_json,
    }


def role_view_dto(view) -> dict:
    return {
        "id": str(view.id),
        "code": view.code,
        "name": view.name,
        "description": view.description,
        "ov_base_role": view.ov_base_role,
        "rank": view.rank,
        "is_system": view.is_system,
        "status": view.status,
        "permissions": sorted(view.permissions),
    }


@router.get("/users", dependencies=[Depends(require_permission("user.read"))])
async def list_users(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows, next_cursor = await admin.list_users(
            session,
            account_id=principal.actor_account_id,
            limit=limit,
            cursor=decode_cursor(cursor),
        )
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_CURSOR"}) from exc
    return {"status": "ok", "result": {"items": [user_dto(r) for r in rows], "next_cursor": next_cursor}}


@router.post("/users", dependencies=[Depends(verify_csrf), Depends(require_permission("user.create"))])
async def create_user(
    request: Request,
    body: CreateUserRequest,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await admin.create_account_user(
            session,
            actor=principal,
            email=body.email,
            username=body.username,
            display_name=body.display_name,
            request_id=_request_id(request),
        )
    except ConstraintViolationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": constraint_code(exc)}) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": created_user_dto(result)}


@router.patch("/users/{user_id}", dependencies=[Depends(verify_csrf), Depends(require_permission("user.update"))])
async def patch_user(
    request: Request,
    user_id: uuid.UUID,
    body: PatchUserRequest,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await admin.patch_user(
            session,
            actor=principal,
            target_user_id=user_id,
            display_name=body.display_name,
            status=body.status,
            request_id=_request_id(request),
        )
    except LastAccountAdminError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "LAST_ACCOUNT_ADMIN_REQUIRED"}
        ) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": user_dto(row)}


@router.post(
    "/users/{user_id}/disable",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="user.disable", permission_code="user.disable", scope="account"
            )
        )
    ],
)
async def disable_user(
    request: Request,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await admin.set_user_status(
            session,
            actor=principal,
            target_user_id=user_id,
            status="disabled",
            request_id=_request_id(request),
        )
    except LastAccountAdminError as exc:
        # 服务层已写 denied 审计（06 §14.5），必须在异常路径提交
        await session.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "LAST_ACCOUNT_ADMIN_REQUIRED"}
        ) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "id": str(result.user.id),
            "status": "disabled",
            "sessions_revoked": result.sessions_revoked,
            "keys_revoked": result.keys_revoked,
        },
    }


@router.post(
    "/users/{user_id}/password/reset",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="user.password.reset",
                permission_code="user.password.reset.account",
                scope="account",
            )
        )
    ],
)
async def reset_password(
    request: Request,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await admin.reset_user_password(
            session,
            actor=principal,
            target_user_id=user_id,
            request_id=_request_id(request),
        )
    except PasswordResetForbiddenError as exc:
        # 服务层已写 denied 审计（06 §14.5），必须在异常路径提交
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.reason}) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {
        "status": "ok",
        "result": {"new_password": result.new_password, "sessions_revoked": result.sessions_revoked},
    }


@router.get("/roles", dependencies=[Depends(require_permission("role.read"))])
async def list_roles(
    request: Request,
    rbac: RbacService = Depends(_rbac),
    session: AsyncSession = Depends(get_session),
):
    views = await rbac.get_role_views(session)
    return {"status": "ok", "result": [role_view_dto(v) for v in views]}


@router.get("/audit-events", dependencies=[Depends(require_permission("audit.read"))])
async def list_audit_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None),
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    # /admin 是 Account 作用域：无 Account 上下文（PSA）→ 空列表（走平台审计端点）
    if principal.actor_account_id is None:
        return {"status": "ok", "result": {"items": [], "next_cursor": None}}
    try:
        rows, next_cursor = await admin.list_audit_events(
            session,
            account_id=principal.actor_account_id,
            limit=limit,
            cursor=decode_cursor(cursor),
        )
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_CURSOR"}) from exc
    return {
        "status": "ok",
        "result": {"items": [audit_dto(e) for e in rows], "next_cursor": next_cursor},
    }


@router.get(
    "/users/{user_id}/api-keys",
    dependencies=[Depends(require_permission("credential.read.account"))],
)
async def list_user_api_keys(
    request: Request,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        creds = await admin.list_user_api_keys(
            session, scope_account_id=principal.actor_account_id, target_user_id=user_id
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {
        "status": "ok",
        "result": {"items": [api_key_dto(c) for c in creds], "next_cursor": None},
    }


@router.delete(
    "/users/{user_id}/api-keys/{credential_id}",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="credential.revoke",
                permission_code="credential.revoke.account",
                scope="account",
            )
        )
    ],
)
async def revoke_user_api_key(
    request: Request,
    user_id: uuid.UUID,
    credential_id: uuid.UUID,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        revoked = await admin.revoke_user_api_key(
            session,
            actor=principal,
            scope_account_id=principal.actor_account_id,
            target_user_id=user_id,
            credential_id=credential_id,
            request_id=_request_id(request),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": {"id": str(revoked.id), "name": revoked.name, "revoked": True}}


# ── P2-E2：User 删除回收与 admin 聚合（05 §12.6，14 号计划 §97.2，AC⑨）──


def _deletion(request: Request):
    return request.app.state.iam_deletion_service


def _aggregates(request: Request):
    return request.app.state.iam_aggregate_service


def _deletion_error(exc: Exception, request: Request):
    """DeletionJobError/AdminActionForbiddenError → 409/403 稳定码。"""
    if isinstance(exc, DeletionJobError):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.reason}) from exc
    if isinstance(exc, AdminActionForbiddenError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.reason}) from exc
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc


def deletion_preview_dto(preview) -> dict:
    return {
        "resource_type": preview.resource_type,
        "resource_id": preview.resource_id,
        "target_name": preview.target_name,
        "impacted": preview.impacted,
        "recoverable": preview.recoverable,
        "purge_after": preview.purge_after,
    }


def deletion_job_result_dto(result) -> dict:
    return {
        "resource_type": result.resource_type,
        "resource_id": result.resource_id,
        "deletion_job_id": str(result.deletion_job_id),
        "deleted_at": result.deleted_at,
        "restore_until": result.restore_until,
    }


def recycle_bin_row_dto(row) -> dict:
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


@router.get(
    "/users/{user_id}/deletion-preview",
    dependencies=[Depends(require_permission("user.delete"))],
)
async def user_deletion_preview(
    request: Request,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    try:
        preview = await deletion.preview_user(
            session,
            scope_account_id=principal.actor_account_id,
            target_user_id=user_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": deletion_preview_dto(preview)}


@router.delete(
    "/users/{user_id}",
    dependencies=[
        Depends(
            require_high_risk_write(
                action="user.delete", permission_code="user.delete", scope="account"
            )
        )
    ],
)
async def delete_user(
    request: Request,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    """`DELETE /admin/users/{id}`：软删除进入回收期并返回 deletion job ID（AC⑨）。"""
    try:
        result = await deletion.delete_user(
            session,
            actor=principal,
            scope_account_id=principal.actor_account_id,
            target_user_id=user_id,
            request_id=_request_id(request),
        )
    except LastAccountAdminError as exc:
        # 服务层已写 denied 审计（06 §14.5），必须在异常路径提交
        await session.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "LAST_ACCOUNT_ADMIN_REQUIRED"}
        ) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": deletion_job_result_dto(result)}


@router.get("/activity", dependencies=[Depends(require_permission("task.read.account_shared"))])
async def admin_activity(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    principal=Depends(get_current_principal),
    aggregates=Depends(_aggregates),
    session: AsyncSession = Depends(get_session),
):
    """`GET /admin/activity`：仅当前 Account 共享对象任务（05 §12.6）。"""
    if principal.actor_account_id is None:
        return {"status": "ok", "result": {"items": [], "next_cursor": None}}
    rows = await aggregates.list_account_shared_activity(
        session, account_id=principal.actor_account_id, limit=limit
    )
    return {"status": "ok", "result": {"items": rows, "next_cursor": None}}


@router.get("/monitoring", dependencies=[Depends(require_permission("monitoring.read"))])
async def admin_monitoring(
    request: Request,
    principal=Depends(get_current_principal),
    aggregates=Depends(_aggregates),
    session: AsyncSession = Depends(get_session),
):
    """`GET /admin/monitoring`：仅当前 Account 业务摘要（05 §12.6）。"""
    if principal.actor_account_id is None:
        return {"status": "ok", "result": {"scope": "account", "summary": {}}}
    return {
        "status": "ok",
        "result": await aggregates.account_monitoring(
            session, account_id=principal.actor_account_id
        ),
    }


@router.get("/recycle-bin")
async def admin_recycle_bin(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal=Depends(get_current_principal),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    """`GET /admin/recycle-bin`：权限按对象类型分别校验（05 §12.6 注）。"""
    if principal.actor_account_id is None:
        return {"status": "ok", "result": {"items": [], "next_cursor": None}}
    rows = await deletion.list_recycle_bin(
        session,
        scope="account",
        principal=principal,
        account_id=principal.actor_account_id,
        limit=limit,
    )
    return {
        "status": "ok",
        "result": {"items": [recycle_bin_row_dto(r) for r in rows], "next_cursor": None},
    }


@router.post("/recycle-bin/{job_id}/restore", dependencies=[Depends(verify_csrf)])
async def admin_restore(
    request: Request,
    job_id: uuid.UUID,
    principal=Depends(get_current_principal),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    """`POST /admin/recycle-bin/{id}/restore`：类型化恢复权限（05 §12.6 注）。"""
    try:
        result = await deletion.restore(
            session,
            scope="account",
            principal=principal,
            account_id=principal.actor_account_id,
            job_id=job_id,
            request_id=_request_id(request),
        )
    except (DeletionJobError, AdminActionForbiddenError, EntityNotFoundError) as exc:
        raise _deletion_error(exc, request)
    await session.commit()
    return {"status": "ok", "result": deletion_job_result_dto(result)}
