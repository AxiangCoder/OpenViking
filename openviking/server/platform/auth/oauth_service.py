"""MCP OAuth 产品端点服务（05 §12.4，14 号计划 §97.7，P3-E2 联调契约）。

- 待授权信息（pending / 跨设备 display code 预览）：只返回服务端登记的
  Client 名称/ID/回调 host/Scope 等公开安全元数据（06 §13.8 防仿冒）；
- 批准：登录 Session 确定 User（05 §12.4：忽略客户端提交的任何身份字段），
  Grant 按 `user_id + client_id + scope` 建立/复用，Auth Code 单次消费；
- 拒绝：一次性删除 pending；
- 同意只接受 `authentication_method=session`：API Key/OAuth Token 不能
  代替浏览器批准（04 §10.13，AC②）；
- 撤销 Grant 及其全部 Token family，不影响其他 Key/客户端（13 §83.1）；
- 创建/批准/拒绝/撤销均写审计，不记录 Code/Token/PKCE/display code 明文
  （04 §10.13）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.errors import OAuthProtocolError
from openviking.server.platform.iam.oauth_store import OAuthTokenStore

OAUTH_PENDING_NOT_FOUND = "OAUTH_PENDING_NOT_FOUND"
OAUTH_PENDING_REQUIRED = "OAUTH_PENDING_REQUIRED"
OAUTH_DECISION_INVALID = "OAUTH_DECISION_INVALID"
OAUTH_CLIENT_DISABLED = "OAUTH_CLIENT_DISABLED"
OAUTH_AUTHORIZE_SESSION_REQUIRED = "OAUTH_AUTHORIZE_SESSION_REQUIRED"
OAUTH_GRANT_NOT_FOUND = "OAUTH_GRANT_NOT_FOUND"
OAUTH_ACCOUNT_REQUIRED = "OAUTH_ACCOUNT_REQUIRED"

# 06 §13.8 展示项（服务端统一文案，不随客户端提交值变化）。
OAUTH_DATA_ACCESS = "当前用户私有数据与所属 Account 共享数据（受当前角色权限限制）"
OAUTH_IMPACT = "以你的身份调用 MCP 工具；读取、写入与删除均受统一权限控制"


class OAuthService:
    """MCP OAuth 产品端点（05 §12.4）：pending/authorize/grants。"""

    def __init__(self, store: OAuthTokenStore, repo) -> None:
        self._store = store
        self._repo = repo

    # ── 解析目标 pending（pending_id 或跨设备 display code）──

    async def _resolve_pending(
        self,
        session: AsyncSession,
        *,
        pending_id: str | None,
        code: str | None,
    ) -> dict[str, Any]:
        if pending_id:
            record = await self._store.load_pending_authorization(
                pending_id=pending_id, session=session
            )
            if record is None or record.get("verified"):
                raise OAuthProtocolError(OAUTH_PENDING_NOT_FOUND)
            return record
        if code:
            record = await self._store.find_pending_by_display_code(
                display_code=code, session=session
            )
            if record is None:
                raise OAuthProtocolError(OAUTH_PENDING_NOT_FOUND)
            return record
        raise OAuthProtocolError(OAUTH_PENDING_REQUIRED)

    # ── 公开待授权信息（06 §13.8 白名单字段）──

    async def pending_info(
        self,
        session: AsyncSession,
        pending_id: str,
    ) -> dict[str, Any]:
        info = await self._store.get_pending_info(session, pending_id)
        expires_in = max(
            int((info["expires_at"] - datetime.now(timezone.utc)).total_seconds()), 0
        )
        return {
            "client_id": info["client_id"],
            "client_name": info["client_name"],
            "redirect_uri_host": info["redirect_uri_host"],
            "scopes": info["scopes"],
            "expires_in": expires_in,
            "data_access": OAUTH_DATA_ACCESS,
            "impact": OAUTH_IMPACT,
        }

    # ── 批准 / 拒绝（Session + CSRF，integration.oauth.authorize.self）──

    async def approve(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedUserPrincipal,
        pending_id: str | None = None,
        code: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if principal.authentication_method != "session":
            raise OAuthProtocolError(OAUTH_AUTHORIZE_SESSION_REQUIRED)
        if principal.actor_account_id is None:
            # 平台级身份（PSA）没有 Account，不能成为 Grant 归属（04 §10.13）。
            raise OAuthProtocolError(OAUTH_ACCOUNT_REQUIRED)
        pending = await self._resolve_pending(session, pending_id=pending_id, code=code)
        auth_code = self._store.mint_authorization_code()
        approved = await self._store.approve_pending(
            session,
            pending_id=pending["pending_id"],
            user_id=principal.actor_user_id,
            account_id=principal.actor_account_id,
            role="USER",
            auth_code=auth_code,
            auth_code_ttl_seconds=self._store.code_ttl_seconds,
        )
        params: dict[str, str] = {"code": auth_code}
        if approved.get("state"):
            params["state"] = approved["state"]
        sep = "&" if "?" in approved["redirect_uri"] else "?"
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=principal.actor_account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_account_id=principal.actor_account_id,
            subject_user_id=principal.actor_user_id,
            action="oauth.authorize.approve",
            target_type="iam_oauth_grants",
            target_id=approved["grant_id"],
            scope="oauth",
            result="success",
            metadata={
                "client_id": approved["client_id"],
                "scope": " ".join(approved["scopes"]) if approved["scopes"] else None,
            },
        )
        return {
            "redirect_url": f"{approved['redirect_uri']}{sep}{urlencode(params)}",
            "client_id": approved["client_id"],
            "client_name": approved["client_name"],
            "redirect_uri_host": approved["redirect_uri_host"],
            "scopes": approved["scopes"],
            "data_access": OAUTH_DATA_ACCESS,
            "impact": OAUTH_IMPACT,
        }

    async def deny(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedUserPrincipal,
        pending_id: str | None = None,
        code: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if principal.authentication_method != "session":
            raise OAuthProtocolError(OAUTH_AUTHORIZE_SESSION_REQUIRED)
        pending = await self._resolve_pending(session, pending_id=pending_id, code=code)
        if not await self._store.deny_pending(session, pending["pending_id"]):
            raise OAuthProtocolError(OAUTH_PENDING_NOT_FOUND)
        client = await self._store.get_client(
            client_id=pending["client_id"], session=session
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=principal.actor_account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_account_id=principal.actor_account_id,
            subject_user_id=principal.actor_user_id,
            action="oauth.authorize.deny",
            target_type="iam_oauth_pending_authorizations",
            target_id=pending["pending_id"],
            scope="oauth",
            result="success",
            metadata={"client_id": pending["client_id"]},
        )
        return {
            "client_id": pending["client_id"],
            "client_name": client.get("client_name") if client else None,
        }

    # ── 预览（authorize 不带 decision：跨设备先展示再决策联调契约）──

    async def preview(
        self,
        session: AsyncSession,
        *,
        pending_id: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        pending = await self._resolve_pending(session, pending_id=pending_id, code=code)
        info = await self.pending_info(session, pending["pending_id"])
        info.pop("expires_in", None)
        return info

    # ── Grants（/me/oauth-grants）──

    async def list_grants(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        return await self._store.list_grants_for_user(session, user_id)

    async def revoke_grant(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedUserPrincipal,
        grant_id: uuid.UUID,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        result = await self._store.revoke_grant(
            session,
            grant_id=grant_id,
            user_id=principal.actor_user_id,
            revoked_by=principal.actor_user_id,
        )
        if result is None:
            return None
        if result["changed"]:
            await self._repo.append_audit_event(
                session,
                request_id=request_id,
                account_id=principal.actor_account_id,
                actor_type="user",
                actor_user_id=principal.actor_user_id,
                actor_account_id=principal.actor_account_id,
                actor_session_id=principal.session_id,
                authentication_method=principal.authentication_method,
                subject_account_id=principal.actor_account_id,
                subject_user_id=principal.actor_user_id,
                action="oauth.grant.revoke",
                target_type="iam_oauth_grants",
                target_id=result["id"],
                scope="oauth",
                result="success",
                metadata={"tokens_revoked": result["tokens_revoked"]},
            )
        return result
