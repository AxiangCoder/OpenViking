"""Platform 管理 API（05 §12.6 `/api/platform/v1/platform/*`，14 号计划 §96.5）。

- `POST /platform/accounts`：PSA 创建 Account + 首位 Admin（一次返回初始密码、
  ov 映射，03 §8.3；Provisioning outbox/Worker 状态流归 P2-E1）；
- `GET /platform/accounts[/{id}/users]`：平台范围 Account/用户列表；
- `POST /platform/accounts/{id}/users/{uid}/password/reset`：平台级重置
  （禁目标 PSA，03 §8.3 等级比较只用平台 rank）；
- `PUT /platform/accounts/{id}/users/{uid}/role`：仅 `user → account_admin`
  提升（04 §10.6，即时生效免重登）；
- `GET/DELETE .../users/{uid}/api-keys[/{credential_id}]`：API Key 元数据只读/撤销
  （P1-E4 未合并前直接用 E1 repository 实现，合并后协调者验证）；
- `GET /platform/audit-events`：平台范围审计读取（04 §10.8）。

Platform Super Admin 选择目标 Account 是管理浏览行为，不是切换登录用户：
每个请求保留原 Actor，目标 Account/User 记录为 Subject（05 §12.6 注）。
写操作全部要求登录 Session + CSRF；权限按 05 §12.6 平台表逐端点校验。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.admin.service import AdminService, decode_cursor
from openviking.server.platform.db import get_session
from openviking.server.platform.dependencies import (
    get_current_principal,
    require_permission,
    verify_csrf,
)
from openviking.server.platform.errors import (
    AdminActionForbiddenError,
    ConstraintViolationError,
    EntityNotFoundError,
    InvalidCursorError,
    PasswordResetForbiddenError,
)
from openviking.server.platform.iam.permissions import ACCOUNT_ADMIN
from openviking.server.platform.routers.admin import (
    api_key_dto,
    audit_dto,
    constraint_code,
    user_dto,
)

router = APIRouter(prefix="/api/platform/v1/platform", tags=["platform"])

NOT_FOUND = "NOT_FOUND"


class CreateAccountRequest(BaseModel):
    account_code: str = Field(min_length=1, max_length=64)
    account_name: str = Field(min_length=1, max_length=128)
    admin_email: str = Field(min_length=1, max_length=320)
    admin_username: str = Field(min_length=1, max_length=128)
    admin_display_name: str | None = Field(default=None, max_length=128)


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _admin(request: Request) -> AdminService:
    return request.app.state.iam_admin_service


def account_dto(account) -> dict:
    return {
        "id": str(account.id),
        "code": account.code,
        "name": account.display_name,
        "status": account.status,
        "ov_account_id": account.ov_account_id,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
    }


@router.get("/accounts", dependencies=[Depends(require_permission("account.read.platform"))])
async def list_accounts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows, next_cursor = await admin.list_accounts(session, limit=limit, cursor=decode_cursor(cursor))
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_CURSOR"}) from exc
    return {"status": "ok", "result": {"items": [account_dto(a) for a in rows], "next_cursor": next_cursor}}


@router.post(
    "/accounts",
    dependencies=[Depends(verify_csrf), Depends(require_permission("account.manage.platform"))],
)
async def create_account(
    request: Request,
    body: CreateAccountRequest,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await admin.create_account_with_first_admin(
            session,
            actor=principal,
            account_code=body.account_code,
            account_name=body.account_name,
            admin_email=body.admin_email,
            admin_username=body.admin_username,
            admin_display_name=body.admin_display_name,
            request_id=_request_id(request),
        )
    except ConstraintViolationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": constraint_code(exc)}) from exc
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "account": account_dto(result.account),
            "first_admin": {
                "id": str(result.admin.id),
                "username": result.admin.username,
                "email": result.admin.email,
                "role": ACCOUNT_ADMIN,
                "ov_user_id": result.admin.ov_user_id,
                "initial_password": result.initial_password,
            },
        },
    }


@router.get(
    "/accounts/{account_id}/users",
    dependencies=[Depends(require_permission("user.read.platform"))],
)
async def list_account_users(
    request: Request,
    account_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows, next_cursor = await admin.list_account_users(
            session, account_id=account_id, limit=limit, cursor=decode_cursor(cursor)
        )
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_CURSOR"}) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {"status": "ok", "result": {"items": [user_dto(r) for r in rows], "next_cursor": next_cursor}}


@router.post(
    "/accounts/{account_id}/users/{user_id}/password/reset",
    dependencies=[Depends(verify_csrf), Depends(require_permission("user.password.reset.platform"))],
)
async def reset_user_password(
    request: Request,
    account_id: uuid.UUID,
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
            scope_account_id=account_id,
            request_id=_request_id(request),
        )
    except PasswordResetForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.reason}) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {
        "status": "ok",
        "result": {"new_password": result.new_password, "sessions_revoked": result.sessions_revoked},
    }


@router.put(
    "/accounts/{account_id}/users/{user_id}/role",
    dependencies=[Depends(verify_csrf), Depends(require_permission("role.assign.platform"))],
)
async def promote_user_role(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await admin.promote_user(
            session,
            actor=principal,
            target_user_id=user_id,
            account_id=account_id,
            request_id=_request_id(request),
        )
    except AdminActionForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.reason}) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": user_dto(row)}


@router.get(
    "/accounts/{account_id}/users/{user_id}/api-keys",
    dependencies=[Depends(require_permission("credential.read.platform"))],
)
async def list_user_api_keys(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        creds = await admin.list_user_api_keys(
            session, scope_account_id=account_id, target_user_id=user_id
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    return {
        "status": "ok",
        "result": {"items": [api_key_dto(c) for c in creds], "next_cursor": None},
    }


@router.delete(
    "/accounts/{account_id}/users/{user_id}/api-keys/{credential_id}",
    dependencies=[Depends(verify_csrf), Depends(require_permission("credential.revoke.platform"))],
)
async def revoke_user_api_key(
    request: Request,
    account_id: uuid.UUID,
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
            scope_account_id=account_id,
            target_user_id=user_id,
            credential_id=credential_id,
            request_id=_request_id(request),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from exc
    await session.commit()
    return {"status": "ok", "result": {"id": str(revoked.id), "name": revoked.name, "revoked": True}}


@router.get("/audit-events", dependencies=[Depends(require_permission("audit.read"))])
async def list_platform_audit_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None),
    admin: AdminService = Depends(_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows, next_cursor = await admin.list_audit_events(session, limit=limit, cursor=decode_cursor(cursor))
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "INVALID_CURSOR"}) from exc
    return {
        "status": "ok",
        "result": {"items": [audit_dto(e) for e in rows], "next_cursor": next_cursor},
    }
