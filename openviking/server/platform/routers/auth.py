"""Auth API 路由（05 §12.3 `/api/platform/v1/auth/*`，14 号计划 §96.3）。

- `POST /login`：仅 email+password，统一 LOGIN_FAILED 防枚举、IP+标识限流递增冷却；
  成功下发 `__Host-ov_session` Cookie 并返回一次 CSRF Token；
- `POST /logout` / `POST /logout-all`：登录 Session + CSRF；撤销并清 Cookie；
- `GET /me`：当前用户/角色/权限摘要（权限与 P1-E2 有效权限一致）；
- `GET /me/session-summary`：当前登录 Session 脱敏摘要（13 §81.4）；
- `POST /password/change`：必填旧密码，错误 → LOGIN_FAILED 计入限流；
  成功轮换 Session 并同步 Set-Cookie（05 §12.3 回填发现）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.service import AuthService
from openviking.server.platform.db import get_session
from openviking.server.platform.dependencies import (
    apply_session_cookie,
    clear_session_cookie,
    get_current_principal,
    get_iam_services,
    request_ip,
    verify_csrf,
)
from openviking.server.platform.errors import LoginFailedError, LoginRateLimitedError
from openviking.server.platform.models import IamSession

router = APIRouter(prefix="/api/platform/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12)


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _auth(request: Request) -> AuthService:
    return get_iam_services(request).auth


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(_auth),
):
    try:
        result = await auth.login(
            session,
            email=body.email,
            password=body.password,
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
            request_id=_request_id(request),
        )
    except LoginRateLimitedError as exc:
        await session.commit()
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "LOGIN_FAILED"}) from exc
    except LoginFailedError as exc:
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": exc.code}) from exc
    await session.commit()
    apply_session_cookie(response, result.raw_token)
    return {"status": "ok", "result": {"csrf_token": result.csrf_token}}


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(
    request: Request,
    response: Response,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(_auth),
):
    await auth.logout(session, principal=principal, request_id=_request_id(request))
    await session.commit()
    clear_session_cookie(response)
    return {"status": "ok"}


@router.post("/logout-all", dependencies=[Depends(verify_csrf)])
async def logout_all(
    request: Request,
    response: Response,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(_auth),
):
    count = await auth.logout_all(session, principal=principal, request_id=_request_id(request))
    await session.commit()
    clear_session_cookie(response)
    return {"status": "ok", "result": {"sessions_revoked": count}}


@router.get("/me")
async def me(
    request: Request,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """当前用户/角色/权限摘要（05 §12.3）。

    CSRF Token 只在登录/改密响应中下发一次（04 §10.7：DB 仅存 SHA-256，
    服务端无法重建明文）；P3 前端在会话内持有该 Token。
    """
    return {
        "status": "ok",
        "result": {
            "account": (
                {"id": str(principal.actor_account_id)}
                if principal.actor_account_id is not None
                else None
            ),
            "user": {
                "id": str(principal.actor_user_id),
                "ov_user_id": principal.actor_ov_user_id,
            },
            "roles": list(principal.role_codes),
            "permissions": sorted(principal.permissions),
            "can_switch_account": False,
            "csrf_token": None,
        },
    }


@router.get("/me/session-summary")
async def session_summary(
    request: Request,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """当前登录 Session 的脱敏摘要（05 §12.3，13 §81.4）：
    最后活动时间、IP hash、User-Agent 截断；不返回完整 IP 或单会话列表。"""
    row = (
        await session.execute(
            select(IamSession).where(IamSession.id == principal.session_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "SESSION_EXPIRED"})
    return {
        "status": "ok",
        "result": {
            "session_id": str(row.id),
            "last_seen_at": row.last_seen_at.isoformat(),
            "created_at": row.created_at.isoformat(),
            "ip_hash": row.ip_hash,
            "user_agent": _truncate_user_agent(row.user_agent),
        },
    }


def _truncate_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return user_agent
    return user_agent[:80] + "…" if len(user_agent) > 80 else user_agent


@router.post("/password/change", dependencies=[Depends(verify_csrf)])
async def password_change(
    request: Request,
    body: PasswordChangeRequest,
    response: Response,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(_auth),
):
    try:
        result = await auth.change_password(
            session,
            principal=principal,
            old_password=body.old_password,
            new_password=body.new_password,
            ip=request_ip(request),
            request_id=_request_id(request),
        )
    except LoginRateLimitedError as exc:
        await session.commit()
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "LOGIN_FAILED"}) from exc
    except LoginFailedError as exc:
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": exc.code}) from exc
    await session.commit()
    # 轮换必须同步下发新 Set-Cookie，否则客户端下一次请求立即掉线（05 §12.3）
    apply_session_cookie(response, result.raw_token)
    return {
        "status": "ok",
        "result": {"csrf_token": result.csrf_token, "session_rotated": True},
    }
