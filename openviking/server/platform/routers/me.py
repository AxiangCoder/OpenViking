"""用户个人 API Key 管理路由（05 §12.4 `/api/platform/v1/me/api-keys`，14 号计划 §96.4）。

- `GET /api-keys`：元数据列表，无明文（`credential.read.self`）；
- `POST /api-keys`：创建具名 Key，完整明文只返回一次
  （Session+CSRF，`credential.create.self`）；
- `DELETE /api-keys/{id}`：按名撤销自己的 Key，幂等（Session+CSRF，
  `credential.revoke.self`；非本人/已撤销/不存在 → 404 `KEY_NOT_FOUND`）。

只面向 `account_id` 非空的 Account Admin/User（05 §12.4）；PSA 不签发
平台级个人 Key，由 ApiCredentialService 强校验兜底（PSA 权限集合含
`credential.create.self`，不能只依赖路由权限）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.api_keys import ApiCredentialService
from openviking.server.platform.db import get_session
from openviking.server.platform.dependencies import (
    get_iam_services,
    require_permission,
    verify_csrf,
)
from openviking.server.platform.errors import ApiCredentialError
from openviking.server.platform.models import IamApiCredential, IamUser

router = APIRouter(prefix="/api/platform/v1/me", tags=["me"])

KEY_NOT_FOUND = "KEY_NOT_FOUND"
KEY_NAME_MAX_LENGTH = 128


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=KEY_NAME_MAX_LENGTH)
    expires_at: datetime | None = None


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _credential_service(request: Request) -> ApiCredentialService:
    return ApiCredentialService(get_iam_services(request).repository)


def _key_dto(credential: IamApiCredential) -> dict:
    return {
        "id": str(credential.id),
        "name": credential.name,
        "key_last_four": credential.key_last_four,
        "status": "revoked" if credential.revoked_at is not None else credential.status,
        "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
        "last_used_at": (
            credential.last_used_at.isoformat() if credential.last_used_at else None
        ),
        "created_at": credential.created_at.isoformat(),
    }


@router.get("/api-keys")
async def list_api_keys(
    request: Request,
    principal=Depends(require_permission("credential.read.self")),
    session: AsyncSession = Depends(get_session),
):
    """只返回 Key 元数据与掩码，不返回明文（04 §10.3，验收 ①）。"""
    service = _credential_service(request)
    keys = await service.list_keys(session, principal.actor_user_id)
    return {"status": "ok", "result": [_key_dto(k) for k in keys]}


@router.post("/api-keys", dependencies=[Depends(verify_csrf)])
async def create_api_key(
    request: Request,
    body: CreateApiKeyRequest,
    principal=Depends(require_permission("credential.create.self")),
    session: AsyncSession = Depends(get_session),
):
    """创建具名 Key；完整明文只在本次响应返回一次（05 §12.4，验收 ①）。"""
    user: IamUser | None = await get_iam_services(request).repository.get_user(
        session, principal.actor_user_id
    )
    if user is None or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "USER_DISABLED"})
    service = _credential_service(request)
    try:
        created = await service.create_key(
            session,
            user=user,
            name=body.name,
            expires_at=body.expires_at,
            request_id=_request_id(request),
        )
    except ApiCredentialError as exc:
        if exc.reason == "PSA_API_KEY_NOT_SUPPORTED":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.reason}) from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": exc.reason}) from exc
    await session.commit()
    return {
        "status": "ok",
        "result": {**_key_dto(created.record), "api_key": created.full_key},
    }


@router.delete("/api-keys/{key_id}", dependencies=[Depends(verify_csrf)])
async def revoke_api_key(
    request: Request,
    key_id: str,
    principal=Depends(require_permission("credential.revoke.self")),
    session: AsyncSession = Depends(get_session),
):
    """按名撤销自己的 Key（幂等）：重复撤销/非本人 → 404（验收 ⑤，spike ==9 复跑）。"""
    try:
        credential_id = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": KEY_NOT_FOUND}) from None
    service = _credential_service(request)
    revoked = await service.revoke_key(
        session,
        credential_id=credential_id,
        user_id=principal.actor_user_id,
        request_id=_request_id(request),
    )
    if not revoked:
        await session.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": KEY_NOT_FOUND})
    await session.commit()
    return {"status": "ok"}
