"""P1-E3 CSRF 防护测试（03 §8.2，14 号计划 §96.3 验收⑥）。

覆盖：
- Origin/Referer 校验（允许源 / 跨站拒绝 / Referer 回退 / 缺失放行 / 配置源）；
- verify_csrf：安全方法放行；非 Session 凭据（API Key 语义，session_id 为空）
  不能执行 CSRF 写（P1-E4 解析出 api_key principal 后同一分支，`==4` 复跑）；
- apply_session_cookie：__Host-ov_session，HttpOnly/Secure/SameSite=Lax/Path=/。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from openviking.server.platform.auth.csrf import origin_allowed
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.config import PlatformConfig
from openviking.server.platform.dependencies import (
    apply_session_cookie,
    clear_session_cookie,
    verify_csrf,
)


def _request(method: str = "POST", origin: str | None = None, referer: str | None = None) -> Request:
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    if referer:
        headers.append((b"referer", referer.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": "/api/platform/v1/auth/logout",
        "headers": headers,
        "query_string": b"",
        "server": ("testserver", 443),
        "scheme": "https",
        "client": ("127.0.0.1", 54321),
    }
    return Request(scope)


def _api_key_principal() -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=uuid.uuid4(),
        actor_account_id=None,
        actor_ov_user_id="ov_user_x",
        actor_ov_account_id=None,
        user_status="active",
        authentication_method="api_key",
        session_id=None,
    )


# ── Origin/Referer 校验（03 §8.2）──


def test_origin_allowed_same_origin() -> None:
    assert origin_allowed(_request(origin="https://testserver")) is True


def test_origin_rejected_cross_site() -> None:
    assert origin_allowed(_request(origin="https://evil.example")) is False
    assert origin_allowed(_request(origin="http://testserver")) is False  # 跨 scheme 也拒


def test_referer_fallback_allowed() -> None:
    assert origin_allowed(_request(referer="https://testserver/app/login")) is True


def test_referer_rejected_cross_site() -> None:
    assert origin_allowed(_request(referer="https://evil.example/phish")) is False


def test_missing_origin_and_referer_allowed() -> None:
    """非浏览器客户端（无 Origin/Referer）放行；X-CSRF-Token 仍是写请求硬门槛。"""
    assert origin_allowed(_request()) is True


def test_configured_allowed_origins() -> None:
    config = PlatformConfig(csrf_allowed_origins=("https://app.example.com",))
    assert origin_allowed(_request(origin="https://app.example.com"), config) is True
    assert origin_allowed(_request(origin="https://testserver"), config) is False


# ── verify_csrf 依赖（03 §8.2）──


async def test_safe_methods_skip_csrf(repo, session) -> None:
    """GET/HEAD/OPTIONS 不要求 CSRF（05 §12.3：GET /auth/me 仅登录 Session）。"""
    await verify_csrf(_request(method="GET"), _api_key_principal(), session)
    await verify_csrf(_request(method="HEAD"), _api_key_principal(), session)
    await verify_csrf(_request(method="OPTIONS"), _api_key_principal(), session)


async def test_api_key_principal_cannot_do_csrf_write(repo, session) -> None:
    """AC ⑥：API Key 不能做 CSRF 写——非 Session 凭据（session_id 为空）
    一律 403 CSRF_INVALID（P1-E4 的 ==4 复跑经同一分支）。"""
    with pytest.raises(HTTPException) as exc:
        await verify_csrf(_request(), _api_key_principal(), session)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "CSRF_INVALID"


async def test_csrf_bad_origin_rejected_before_token_check(repo, session) -> None:
    with pytest.raises(HTTPException) as exc:
        await verify_csrf(_request(origin="https://evil.example"), _api_key_principal(), session)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "CSRF_INVALID"


# ── Cookie 属性（03 §8.1）──


def test_apply_session_cookie_attributes() -> None:
    """__Host-ov_session：HttpOnly/Secure/SameSite=Lax/Path=/，无 Domain。
    conftest 已设 OV_COOKIE_SECURE=1，Secure 默认生效。"""
    response = Response()
    apply_session_cookie(response, "token-123", PlatformConfig())
    raw = response.headers["set-cookie"]
    assert "__Host-ov_session=token-123" in raw
    assert "HttpOnly" in raw
    assert "Secure" in raw
    assert "SameSite=" in raw and "lax" in raw.lower()
    assert "Path=/" in raw
    assert "Domain=" not in raw
    assert "Max-Age=2592000" in raw  # 绝对 TTL 30 天（03 §8.1）


def test_apply_session_cookie_secure_off_for_local_dev() -> None:
    """本地 http 开发可关闭 Secure（OV_COOKIE_SECURE=0 等价注入）。"""
    response = Response()
    apply_session_cookie(response, "token-123", PlatformConfig(cookie_secure=False))
    raw = response.headers["set-cookie"]
    assert "Secure" not in raw


def test_clear_session_cookie() -> None:
    response = Response()
    clear_session_cookie(response)
    raw = response.headers["set-cookie"]
    assert "__Host-ov_session=" in raw
    assert "Max-Age=0" in raw
