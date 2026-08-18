from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_session
from deps import require_permission, verify_csrf
from models import IamRole, IamUser, IamUserRole
from permissions import ACCOUNT_ADMIN, USER, ROLE_RANKS
from principals import AuthenticatedUserPrincipal
from services.user_service import (
    create_account_user,
    promote_to_account_admin,
    reset_user_password,
)

router = APIRouter(prefix="/api/platform/v1", tags=["admin"])


class CreateUserRequest(BaseModel):
    email: str
    username: str = Field(min_length=1, max_length=128)
    display_name: str | None = None


@router.post("/admin/users", dependencies=[Depends(verify_csrf)])
async def create_user(
    body: CreateUserRequest,
    principal: AuthenticatedUserPrincipal = Depends(require_permission("user.create")),
    session: AsyncSession = Depends(get_session),
):
    if principal.actor_account_id is None:
        raise HTTPException(403, detail={"code": "PLATFORM_USER_CREATE_FORBIDDEN"})
    try:
        created = await create_account_user(
            session,
            principal.actor_account_id,
            principal.actor_user_id,
            body.email,
            body.username,
            body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc
    return {
        "status": "ok",
        "result": {
            "id": str(created.user.id),
            "code": created.user.username,
            "email": created.user.email,
            "role": USER,
            "initial_password": created.initial_password,
        },
    }


@router.get("/admin/users", dependencies=[Depends(require_permission("user.read"))])
async def list_users(
    principal: AuthenticatedUserPrincipal = Depends(require_permission("user.read")),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(IamUser).where(
                IamUser.account_id == principal.actor_account_id,
                IamUser.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return {
        "status": "ok",
        "result": [
            {
                "id": str(u.id),
                "code": u.username,
                "email": u.email,
                "display_name": u.display_name,
                "status": u.status,
                "ov_user_id": u.ov_user_id,
            }
            for u in rows
        ],
    }


async def _load_user(session: AsyncSession, user_id: str) -> IamUser:
    user = (
        await session.execute(select(IamUser).where(IamUser.id == uuid.UUID(user_id)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND"})
    return user


async def _load_role_rank(session: AsyncSession, user: IamUser) -> int:
    rows = (
        await session.execute(
            select(IamRole.rank)
            .join(IamUserRole, IamUserRole.role_id == IamRole.id)
            .where(IamUserRole.user_id == user.id)
        )
    ).scalars().all()
    return max(rows) if rows else 0


@router.post("/admin/users/{user_id}/password/reset", dependencies=[Depends(verify_csrf)])
async def reset_password(
    user_id: str,
    principal: AuthenticatedUserPrincipal = Depends(
        require_permission("user.password.reset.account")
    ),
    session: AsyncSession = Depends(get_session),
):
    target = await _load_user(session, user_id)
    if target.account_id != principal.actor_account_id:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND"})
    target_rank = await _load_role_rank(session, target)
    if principal.role_rank <= target_rank:
        raise HTTPException(
            403, detail={"code": "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN"}
        )
    new_password = await reset_user_password(session, target, principal.actor_user_id)
    return {
        "status": "ok",
        "result": {"new_password": new_password, "sessions_revoked": True},
    }


@router.post("/admin/users/{user_id}/disable", dependencies=[Depends(verify_csrf)])
async def disable_user(
    user_id: str,
    principal: AuthenticatedUserPrincipal = Depends(require_permission("user.disable")),
    session: AsyncSession = Depends(get_session),
):
    target = await _load_user(session, user_id)
    if target.account_id != principal.actor_account_id:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND"})
    target_rank = await _load_role_rank(session, target)
    if principal.role_rank <= target_rank:
        raise HTTPException(403, detail={"code": "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN"})
    target.status = "disabled"
    from services.session_service import SessionService

    await SessionService(session).revoke_all_user_sessions(target.id, "disabled")
    from permissions import bump_user_permission_version

    await bump_user_permission_version(session, target.id)
    await session.commit()
    return {"status": "ok"}


@router.patch("/admin/users/{user_id}", dependencies=[Depends(verify_csrf)])
async def update_user_status(
    user_id: str,
    body: dict,
    principal: AuthenticatedUserPrincipal = Depends(require_permission("user.update")),
    session: AsyncSession = Depends(get_session),
):
    """Enable/disable a user via status (13 §85.3)."""
    target = await _load_user(session, user_id)
    if target.account_id != principal.actor_account_id:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND"})
    status_value = body.get("status")
    if status_value not in ("active", "disabled"):
        raise HTTPException(422, detail={"code": "INVALID_STATUS"})
    target_rank = await _load_role_rank(session, target)
    if principal.role_rank <= target_rank:
        raise HTTPException(403, detail={"code": "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN"})
    target.status = status_value
    if status_value == "disabled":
        from services.session_service import SessionService

        await SessionService(session).revoke_all_user_sessions(target.id, "disabled")
    from permissions import bump_user_permission_version

    await bump_user_permission_version(session, target.id)
    await session.commit()
    return {"status": "ok", "result": {"status": target.status}}


@router.put(
    "/platform/accounts/{account_id}/users/{user_id}/role",
    dependencies=[Depends(verify_csrf)],
)
async def set_role(
    account_id: str,
    user_id: str,
    principal: AuthenticatedUserPrincipal = Depends(require_permission("role.assign.platform")),
    session: AsyncSession = Depends(get_session),
):
    target = await _load_user(session, user_id)
    if str(target.account_id) != account_id:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND"})
    if principal.actor_account_id is not None:
        raise HTTPException(403, detail={"code": "PERMISSION_NOT_GRANTED"})
    await promote_to_account_admin(session, target)
    return {"status": "ok", "result": {"role": ACCOUNT_ADMIN}}


@router.get("/admin/roles", dependencies=[Depends(require_permission("role.read"))])
async def list_roles(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(IamRole).where(IamRole.is_system.is_(True), IamRole.status == "active")
        )
    ).scalars().all()
    return {
        "status": "ok",
        "result": [
            {
                "code": r.code,
                "name": r.name,
                "ov_base_role": r.ov_base_role,
                "rank": r.rank,
            }
            for r in rows
        ],
    }


@router.get(
    "/admin/users/{user_id}/actor-subject",
    dependencies=[Depends(require_permission("user.read"))],
)
async def actor_subject_preview(
    user_id: str,
    principal: AuthenticatedUserPrincipal = Depends(require_permission("user.read")),
    session: AsyncSession = Depends(get_session),
):
    """Spike-only: proves the Actor/Subject split — an Account Admin reading a member
    keeps the real Actor in the audit context and the member as Subject."""
    target = await _load_user(session, user_id)
    if target.account_id != principal.actor_account_id:
        raise HTTPException(404, detail={"code": "USER_NOT_FOUND"})
    return {
        "status": "ok",
        "result": {
            "actor_user_id": str(principal.actor_user_id),
            "actor_account_id": str(principal.actor_account_id),
            "subject_user_id": str(target.id),
            "subject_account_id": str(target.account_id),
            "subject_ov_user_id": target.ov_user_id,
            "visibility": "user_private",
            "action": "admin.member.preview",
        },
    }
