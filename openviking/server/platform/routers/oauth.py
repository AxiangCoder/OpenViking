"""MCP OAuth 产品端点（05 §12.4 `/integrations/mcp/oauth/*`、`/me/oauth-grants`）。

供 P3-E2 授权页（`/oauth/consent`、`/oauth/verify`）与
`/app/profile/connections` 消费：

- `GET /integrations/mcp/oauth/pending/{pending_id}`：公开待授权元数据
  （Client 名称/ID/回调 host/Scope，06 §13.8；不返回 display code/完整
  redirect URI/Code/Token 明文）；
- `POST /integrations/mcp/oauth/authorize`：登录 Session + CSRF，
  `integration.oauth.authorize.self`；使用 `pending_id` 或跨设备 display
  code 批准/拒绝；请求体不带 decision 时返回待授权信息（跨设备
  「先展示再决策」联调契约）；不接受 API Key/OAuth Token（AC②）；
- `GET /me/oauth-grants`：当前用户已授权的 MCP 客户端（Session，
  `integration.oauth.read.self`）；
- `DELETE /me/oauth-grants/{grant_id}`：撤销 Grant 及其 Token family
  （Session + CSRF，`integration.oauth.revoke.self`；幂等，不影响其他
  Key/客户端）。

服务端从登录 Session 确定 User，忽略客户端提交的任何 Account/User/Role
（05 §12.4，08 §29.5）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.oauth_service import (
    OAUTH_GRANT_NOT_FOUND,
    OAuthService,
)
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.db import get_session
from openviking.server.platform.dependencies import (
    get_iam_services,
    require_permission,
    verify_csrf,
)
from openviking.server.platform.errors import OAuthProtocolError

router = APIRouter(prefix="/api/platform/v1", tags=["oauth"])

NOT_FOUND = "NOT_FOUND"


def _oauth_service(request: Request) -> OAuthService:
    return OAuthService(
        store=request.app.state.platform_oauth_store,
        repo=get_iam_services(request).repository,
    )


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _protocol_http_error(exc: OAuthProtocolError) -> HTTPException:
    """OAuthProtocolError → 稳定 HTTP 错误（05 §12.2 错误码）。"""
    if exc.reason in ("OAUTH_PENDING_NOT_FOUND", "OAUTH_GRANT_NOT_FOUND"):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": exc.reason})
    if exc.reason in ("OAUTH_AUTHORIZE_SESSION_REQUIRED", "OAUTH_CLIENT_DISABLED"):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.reason})
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": exc.reason})


# ── 公开待授权信息（无鉴权；06 §13.8 白名单字段）──


@router.get("/integrations/mcp/oauth/pending/{pending_id}")
async def oauth_pending_info(
    request: Request,
    pending_id: str,
    session: AsyncSession = Depends(get_session),
):
    """返回服务端登记的 Client 名称/ID/回调 host/Scope 等公开安全元数据。"""
    try:
        info = await _oauth_service(request).pending_info(session, pending_id)
    except OAuthProtocolError as exc:
        raise _protocol_http_error(exc) from exc
    return {"status": "ok", "result": info}


class OAuthAuthorizeRequest(BaseModel):
    """05 §12.4 authorize 请求体：`pending_id`（同设备）或 `code`（跨设备）。

    两者至少一个；`decision` 缺省 = 仅查询待授权信息（先展示再决策）。
    """

    pending_id: Optional[str] = Field(default=None, max_length=64)
    code: Optional[str] = Field(default=None, max_length=32)
    decision: Optional[str] = Field(default=None, pattern="^(approve|reject)$")


@router.post(
    "/integrations/mcp/oauth/authorize",
    dependencies=[Depends(verify_csrf)],
)
async def oauth_authorize(
    request: Request,
    body: OAuthAuthorizeRequest,
    principal: Annotated[
        AuthenticatedUserPrincipal,
        Depends(require_permission("integration.oauth.authorize.self")),
    ],
    session: AsyncSession = Depends(get_session),
):
    """批准/拒绝 OAuth 授权，或返回待授权信息（不带 decision）。

    只接受登录 Session（AC②：API Key/OAuth Token 不能代替浏览器批准）；
    approve 返回 `redirect_url`（服务端登记回调 + 一次性 Auth Code），
    由浏览器跳转完成协议闭环（06 §13.8）。
    """
    service = _oauth_service(request)
    try:
        if body.decision is None:
            result = await service.preview(
                session, pending_id=body.pending_id, code=body.code
            )
        elif body.decision == "approve":
            result = await service.approve(
                session,
                principal=principal,
                pending_id=body.pending_id,
                code=body.code,
                request_id=_request_id(request),
            )
        else:
            result = await service.deny(
                session,
                principal=principal,
                pending_id=body.pending_id,
                code=body.code,
                request_id=_request_id(request),
            )
    except OAuthProtocolError as exc:
        raise _protocol_http_error(exc) from exc
    await session.commit()
    return {"status": "ok", "result": result}


# ── Grants（/app/profile/connections，13 §83.1）──


@router.get("/me/oauth-grants")
async def list_oauth_grants(
    request: Request,
    principal: Annotated[
        AuthenticatedUserPrincipal,
        Depends(require_permission("integration.oauth.read.self")),
    ],
    session: AsyncSession = Depends(get_session),
):
    """当前用户已授权的 MCP 客户端（Client 名称、Scope、授权/最近使用时间）。"""
    grants = await _oauth_service(request).list_grants(session, principal.actor_user_id)
    return {"status": "ok", "result": grants}


@router.delete(
    "/me/oauth-grants/{grant_id}",
    dependencies=[Depends(verify_csrf)],
)
async def revoke_oauth_grant(
    request: Request,
    grant_id: str,
    principal: Annotated[
        AuthenticatedUserPrincipal,
        Depends(require_permission("integration.oauth.revoke.self")),
    ],
    session: AsyncSession = Depends(get_session),
):
    """撤销 Grant 及其 Token family（幂等：已撤销返回成功；非本人/不存在 404）。"""
    try:
        parsed = uuid.UUID(grant_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND}) from None
    result = await _oauth_service(request).revoke_grant(
        session,
        principal=principal,
        grant_id=parsed,
        request_id=_request_id(request),
    )
    if result is None:
        await session.commit()
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": OAUTH_GRANT_NOT_FOUND}
        )
    await session.commit()
    return {"status": "ok", "result": {"status": "ok"}}
