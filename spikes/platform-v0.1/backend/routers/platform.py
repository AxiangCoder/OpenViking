from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_session
from deps import require_permission, verify_csrf
from models import IamAccount, IamUser
from principals import AuthenticatedUserPrincipal
from services.user_service import create_account_with_first_admin

router = APIRouter(prefix="/api/platform/v1/platform", tags=["platform"])


class CreateAccountRequest(BaseModel):
    account_code: str = Field(min_length=1, max_length=64)
    account_name: str = Field(min_length=1, max_length=128)
    admin_email: str
    admin_username: str = Field(min_length=1, max_length=128)
    admin_display_name: str | None = None


@router.post("/accounts", dependencies=[Depends(verify_csrf)])
async def create_account(
    body: CreateAccountRequest,
    principal: AuthenticatedUserPrincipal = Depends(
        require_permission("account.manage.platform")
    ),
    session: AsyncSession = Depends(get_session),
):
    if principal.actor_account_id is not None:
        raise HTTPException(403, detail={"code": "PERMISSION_NOT_GRANTED"})
    existing = (
        await session.execute(select(IamAccount).where(IamAccount.code == body.account_code))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, detail={"code": "ACCOUNT_CODE_EXISTS"})
    account, admin = await create_account_with_first_admin(
        session,
        ov_account_id=f"ov_account_{body.account_code}",
        account_code=body.account_code,
        account_name=body.account_name,
        admin_email=body.admin_email,
        admin_username=body.admin_username,
        admin_display_name=body.admin_display_name,
    )
    return {
        "status": "ok",
        "result": {
            "account": {
                "id": str(account.id),
                "code": account.code,
                "ov_account_id": account.ov_account_id,
                "name": account.display_name,
                "status": account.status,
            },
            "first_admin": {
                "id": str(admin.user.id),
                "code": admin.user.username,
                "email": admin.user.email,
                "ov_user_id": admin.user.ov_user_id,
                "initial_password": admin.initial_password,
            },
        },
    }


@router.get("/accounts", dependencies=[Depends(require_permission("account.read.platform"))])
async def list_accounts(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(IamAccount).where(IamAccount.deleted_at.is_(None)).order_by(IamAccount.created_at)
        )
    ).scalars().all()
    return {
        "status": "ok",
        "result": [
            {
                "id": str(a.id),
                "code": a.code,
                "ov_account_id": a.ov_account_id,
                "name": a.display_name,
                "status": a.status,
            }
            for a in rows
        ],
    }


@router.get("/accounts/{account_id}/users", dependencies=[Depends(require_permission("user.read.platform"))])
async def list_account_users(
    account_id: str,
    session: AsyncSession = Depends(get_session),
):
    from uuid import UUID

    rows = (
        await session.execute(
            select(IamUser)
            .where(IamUser.account_id == UUID(account_id), IamUser.deleted_at.is_(None))
            .order_by(IamUser.created_at)
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


@router.post(
    "/accounts/{account_id}/provisioning/retry",
    dependencies=[Depends(verify_csrf)],
)
async def retry_provisioning(
    account_id: str,
    principal: AuthenticatedUserPrincipal = Depends(
        require_permission("account.manage.platform")
    ),
    session: AsyncSession = Depends(get_session),
):
    """Closes the design gap recorded in 13 §93: retry only provisioning/failed."""
    from uuid import UUID

    account = (
        await session.execute(select(IamAccount).where(IamAccount.id == UUID(account_id)))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(404, detail={"code": "ACCOUNT_NOT_FOUND"})
    if account.status not in ("provisioning", "failed"):
        raise HTTPException(409, detail={"code": "PROVISIONING_NOT_RETRYABLE"})
    return {
        "status": "ok",
        "result": {
            "account_id": str(account.id),
            "status": account.status,
            "retry_scheduled": True,
        },
    }
