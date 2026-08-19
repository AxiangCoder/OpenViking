"""/api/v1 与 /mcp 认证插件切换为 IAM Principal（07 §20 少量修改项，14 号计划 §97.6）。

`PlatformIamAuthPlugin` 把低层入口（现有 `/api/v1` Router 与 MCP Tool）的认证
从旧 User Key registry 切换到 PostgreSQL IAM：

- 登录 Session Cookie、用户 API Key（`ovk_u.*`）、MCP OAuth Token 三凭证
  统一解析为 IAM Principal（`resolve_principal`/`resolve_oauth_token_principal`），
  与 `/api/platform/v1` 同一套实时 RBAC（05 §11.4）；
- Cookie 优先、失效**不自动回退** Bearer（AC⑥，03 §8.4 防凭据混淆）；
- `X-OpenViking-Account/User` Header 不可信：静默移除，不能切换身份
  （AC⑤ Header spoofing，02 §7.4）；
- 解析成功把 `AuthenticatedUserPrincipal` 写入 `request.state.platform_principal`，
  低层守卫与 MCP Tool 据此按 Key/Token 归属者身份授权与审计（AC④）；
- OpenViking Base Role 映射：`ov_base_role=admin`（Account Admin）→
  `Role.ADMIN`，其余（User/PSA）→ `Role.USER` 最小权限（平台 rank 与
  OpenViking rank 隔离，04 §10.4/02 §7.3——PSA 的跨 Account 管理不经过
  低层入口，由平台管理 API 承载）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request

from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.platform.auth.principals import (
    resolve_api_key_principal,
    resolve_oauth_token_principal,
    resolve_session_principal,
)
from openviking.server.platform.config import platform_config
from openviking.server.platform.db import session_factory as platform_session_factory
from openviking_cli.exceptions import UnauthenticatedError

# MCP OAuth 访问令牌前缀（openviking.server.oauth.provider）。
try:
    from openviking.server.oauth.provider import ACCESS_TOKEN_PREFIX
except Exception:  # pragma: no cover - oauth 模块始终存在
    ACCESS_TOKEN_PREFIX = "ov_mcp_tok_"


def _remove_header(request: Request, name: bytes) -> None:
    """从底层 request scope 移除 Header（Starlette Headers 不可变）。

    与 ApiKeyAuthPlugin 同模式：身份断言 Header 在 api_key 模式静默清除，
    避免老客户端习惯性发送这些 Header 时产生弱化或歧义。
    """
    scope_headers = request.scope.get("headers", [])
    request.scope["headers"] = [
        (key, value) for key, value in scope_headers if key.lower() != name.lower()
    ]


def _to_resolved_identity(principal) -> ResolvedIdentity:
    """IAM Principal → OpenViking ResolvedIdentity（低层 ctx 身份）。"""
    role = Role.ADMIN if principal.ov_base_role == "admin" else Role.USER
    return ResolvedIdentity(
        role=role,
        account_id=principal.actor_ov_account_id,
        user_id=principal.actor_ov_user_id,
        from_oauth=principal.authentication_method == "oauth",
    )


class PlatformIamAuthPlugin(AuthPlugin):
    """平台模式（platform_enabled=True）低层入口认证插件（07 §20）。"""

    auth_mode = "platform_iam"

    def validate_config(self, config) -> None:
        return None

    async def initialize(self, app, service, config) -> None:
        return None

    def requires_api_key_manager(self) -> bool:
        # 平台模式使用 PostgreSQL IAM，不需要旧 APIKeyManager
        return False

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        return False

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        """三凭证统一解析（05 §11.4）：Cookie 优先、失效不回退 Bearer。

        - 无任何凭证 → `UnauthenticatedError`（401）；
        - Cookie 存在 → Session 解析（失败即 401，不回退 Bearer，AC⑥）；
        - 无 Cookie 且 Bearer/X-Api-Key 带 MCP OAuth 前缀 → OAuth Token 解析；
        - 否则按用户 API Key 解析（`ovk_u.*`）。
        """
        # 身份断言 Header 不可信：静默移除（AC⑤，02 §7.4）
        if x_openviking_account:
            _remove_header(request, b"x-openviking-account")
        if x_openviking_user:
            _remove_header(request, b"x-openviking-user")

        repo = getattr(request.app.state, "iam_repository", None)
        rbac = getattr(request.app.state, "iam_rbac_service", None)
        if repo is None or rbac is None:
            raise RuntimeError("platform IAM services not initialized")
        session_factory = (
            getattr(request.app.state, "platform_session_factory", None)
            or platform_session_factory
        )
        oauth_store = getattr(request.app.state, "platform_oauth_store", None)

        raw_token = request.cookies.get(platform_config.cookie_name)
        async with session_factory() as session:
            if raw_token:
                try:
                    principal = await resolve_session_principal(session, repo, rbac, raw_token)
                except Exception as exc:
                    raise _to_unauthenticated(exc) from exc
            elif api_key:
                try:
                    if oauth_store is not None and api_key.startswith(ACCESS_TOKEN_PREFIX):
                        principal = await resolve_oauth_token_principal(
                            session, repo, rbac, oauth_store, api_key
                        )
                    else:
                        principal = await resolve_api_key_principal(session, repo, rbac, api_key)
                except Exception as exc:
                    raise _to_unauthenticated(exc) from exc
            else:
                raise UnauthenticatedError("Missing credentials when resolving identity.")

        request.state.platform_principal = principal
        return _to_resolved_identity(principal)


def _to_unauthenticated(exc: Exception) -> UnauthenticatedError:
    from openviking.server.platform.errors import AuthenticationError

    if isinstance(exc, AuthenticationError):
        return UnauthenticatedError(str(exc.code))
    return UnauthenticatedError(str(exc))
