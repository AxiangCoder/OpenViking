from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from db import get_session
from deps import apply_session_cookie, get_current_principal, hash_ip, require_permission, verify_csrf
from models import IamUser
from permissions import USER
from principals import AuthenticatedUserPrincipal
from services.session_service import SessionService, authenticate_user
from security import LOGIN_FAILED, hash_password, verify_password

router = APIRouter(prefix="/api/platform/v1/auth", tags=["auth"])

get_current_user = get_current_principal


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=12)


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    user = await authenticate_user(session, body.email, body.password)
    if user is None:
        raise HTTPException(401, detail={"code": LOGIN_FAILED})
    svc = SessionService(session)
    raw_token, csrf_token, _sid = await svc.create_login_session(
        user, ip_hash=hash_ip(request.client.host) if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    apply_session_cookie(response, raw_token)
    return {"status": "ok", "result": {"csrf_token": csrf_token}}


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(
    request: Request,
    response: Response,
    principal: AuthenticatedUserPrincipal = Depends(require_permission("session.read.self")),
    session: AsyncSession = Depends(get_session),
):
    if principal.session_id is not None:
        await SessionService(session).revoke_session(principal.session_id, "logout")
        await session.commit()
    response.delete_cookie(config.cookie_name, path="/")
    return {"status": "ok"}


@router.post("/logout-all", dependencies=[Depends(verify_csrf)])
async def logout_all(
    principal: AuthenticatedUserPrincipal = Depends(require_permission("session.read.self")),
    session: AsyncSession = Depends(get_session),
):
    await SessionService(session).revoke_all_user_sessions(principal.actor_user_id, "logout_all")
    await session.commit()
    return {"status": "ok"}


@router.get("/me")
async def me(principal: AuthenticatedUserPrincipal = Depends(get_current_user)):
    return {
        "status": "ok",
        "result": {
            "account": {"id": str(principal.actor_account_id) if principal.actor_account_id else None},
            "user": {"id": str(principal.actor_user_id), "ov_user_id": principal.actor_ov_user_id},
            "roles": list(principal.role_codes),
            "permissions": sorted(principal.permissions),
            "authentication_method": principal.authentication_method,
            "can_switch_account": False,
        },
    }


@router.post("/password/change", dependencies=[Depends(verify_csrf)])
async def password_change(
    body: PasswordChangeRequest,
    response: Response,
    principal: AuthenticatedUserPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    user = (
        await session.execute(select(IamUser).where(IamUser.id == principal.actor_user_id))
    ).scalar_one()
    if not verify_password(user.password_hash, body.old_password):
        raise HTTPException(401, detail={"code": LOGIN_FAILED})
    user.password_hash = hash_password(body.new_password)
    await session.flush()
    new_token, new_csrf, _sid = await SessionService(session).rotate_session(
        user, principal.session_id
    )
    await session.commit()
    apply_session_cookie(response, new_token)
    return {"status": "ok", "result": {"csrf_token": new_csrf, "session_rotated": True}}
