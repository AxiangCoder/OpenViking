from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_session
from deps import get_current_principal, require_permission, verify_csrf
from models import IamUser
from principals import AuthenticatedUserPrincipal
from services.api_credential_service import ApiCredentialService

router = APIRouter(prefix="/api/platform/v1/me", tags=["me"])


class CreateKeyRequest(BaseModel):
    name: str
    expires_at: datetime | None = None


def _key_dto(cred):
    return {
        "id": str(cred.id),
        "name": cred.name,
        "key_last_four": cred.key_last_four,
        "status": cred.status,
        "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
        "last_used_at": cred.last_used_at.isoformat() if cred.last_used_at else None,
        "created_at": cred.created_at.isoformat(),
    }


@router.get("/api-keys")
async def list_api_keys(
    principal: AuthenticatedUserPrincipal = Depends(
        require_permission("credential.read.self")
    ),
    session: AsyncSession = Depends(get_session),
):
    rows = await ApiCredentialService(session).list_keys(principal.actor_user_id)
    return {"status": "ok", "result": [_key_dto(r) for r in rows]}


@router.post("/api-keys", dependencies=[Depends(verify_csrf)])
async def create_api_key(
    body: CreateKeyRequest,
    principal: AuthenticatedUserPrincipal = Depends(
        require_permission("credential.create.self")
    ),
    session: AsyncSession = Depends(get_session),
):
    user = (
        await session.execute(select(IamUser).where(IamUser.id == principal.actor_user_id))
    ).scalar_one()
    cred, full_key = await ApiCredentialService(session).create_key(
        user, body.name, body.expires_at
    )
    await session.commit()
    return {
        "status": "ok",
        "result": {**_key_dto(cred), "api_key": full_key},
    }


@router.delete("/api-keys/{key_id}", dependencies=[Depends(verify_csrf)])
async def revoke_api_key(
    key_id: str,
    principal: AuthenticatedUserPrincipal = Depends(
        require_permission("credential.revoke.self")
    ),
    session: AsyncSession = Depends(get_session),
):
    from uuid import UUID

    revoked = await ApiCredentialService(session).revoke_key(UUID(key_id), principal.actor_user_id)
    await session.commit()
    if not revoked:
        raise HTTPException(404, detail={"code": "KEY_NOT_FOUND"})
    return {"status": "ok"}


@router.get("/debug/actor-context")
async def debug_actor_context(
    principal: AuthenticatedUserPrincipal = Depends(get_current_principal),
):
    """Spike-only endpoint: proves API Key resolves to the same user principal
    as the session and that permissions match (design 07 §18.3)."""
    return {
        "status": "ok",
        "result": {
            "user_id": str(principal.actor_user_id),
            "account_id": str(principal.actor_account_id) if principal.actor_account_id else None,
            "authentication_method": principal.authentication_method,
            "role_codes": list(principal.role_codes),
            "permission_count": len(principal.permissions),
        },
    }
