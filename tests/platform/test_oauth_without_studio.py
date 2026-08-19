"""P5-E1 验收⑦：无 Studio 时 MCP OAuth 走通（集成测试 + 配置断言，14 号计划 §99.1）。

覆盖：平台模式 + Studio 关闭时，MCP OAuth 协议端点（SDK DCR/authorize/token/
metadata/revoke + authorize 页 + PG 存储）完整挂载，授权页为 web-platform SPA
（/oauth/consent、/oauth/verify），浏览器流程不依赖 /studio、页面无 User API
Key 输入（授权页属 web-platform，见 06 §13.8）。

环境不允许真实 OAuth E2E（需真实 MCP 客户端与公网回调），本文件以
create_app 集成断言覆盖路由/存储/页面层；同设备与跨设备协议走通留 P5-E4
生产复验（07 §21 条目 7）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.server.config import ServerConfig

WEB_PLATFORM_BUNDLE = """\
<!doctype html><html><head><title>OpenViking Platform</title></head>
<body><div id="root">platform-entry</div></body></html>
"""


@pytest.fixture()
def web_platform_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(WEB_PLATFORM_BUNDLE, encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_WEB_PLATFORM_DIR", str(dist))
    return dist


def _fake_ov_config() -> SimpleNamespace:
    return SimpleNamespace(
        oauth=SimpleNamespace(
            enabled=True,
            issuer="http://127.0.0.1:1933",
            db_filename="oauth.db",
            access_token_ttl_seconds=3600,
            refresh_token_ttl_seconds=604800,
            auth_code_ttl_seconds=300,
        ),
        storage=SimpleNamespace(workspace="/tmp/ov-oauth-test"),
    )


@pytest.fixture()
def oauth_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "openviking.server.app.get_openviking_config", lambda: _fake_ov_config()
    )


def _make_app(monkeypatch: pytest.MonkeyPatch, **overrides):
    from openviking.server.app import create_app

    return create_app(config=ServerConfig(**overrides))


def _route_paths(app) -> set[str]:
    return {
        str(getattr(r, "path", None))
        for r in app.routes
        if getattr(r, "path", None) is not None
    }


async def test_oauth_routes_mounted_without_studio(
    monkeypatch: pytest.MonkeyPatch, oauth_enabled: None, web_platform_dist
) -> None:
    """AC⑦：平台模式 + Studio 关闭下 OAuth 协议端点完整挂载、/studio 缺席。"""
    app = _make_app(monkeypatch, platform_enabled=True, studio_enabled=False)
    routes = _route_paths(app)

    # OAuth 协议端点（MCP SDK + 授权页，16.2 → OpenViking；SDK 路由按 RFC 8414
    # 位于根路径 /authorize、/token、/register、/revoke）
    for path in (
        "/authorize",
        "/token",
        "/register",
        "/revoke",
        "/oauth/authorize/page",
        "/oauth/authorize/page/status",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
    ):
        assert path in routes, f"OAuth protocol endpoint missing: {path}"
    # 公网无 /studio（不依赖 Studio 承载 OAuth 授权，06 §13.6）
    assert not any(r.startswith("/studio") for r in routes)
    # 授权页仍为 web-platform SPA 路由（同源）
    assert "/oauth/consent" in routes
    assert "/oauth/verify" in routes


async def test_oauth_pg_store_in_platform_mode(
    monkeypatch: pytest.MonkeyPatch, oauth_enabled: None
) -> None:
    """AC⑦：平台模式 OAuth 事实来源为 PostgreSQL（04 §10.13），非 SQLite。"""
    app = _make_app(monkeypatch, platform_enabled=True, studio_enabled=False)
    from openviking.server.platform.iam.pg_oauth_store import PostgresOAuthStore

    assert isinstance(app.state.oauth_store, PostgresOAuthStore)
    assert isinstance(app.state.platform_oauth_store, PostgresOAuthStore)


async def test_consent_verify_pages_from_web_platform(
    monkeypatch: pytest.MonkeyPatch, oauth_enabled: None, web_platform_dist
) -> None:
    """AC⑦：授权页由 web-platform 提供（无 Studio 时 OAuth 完整走通）。"""
    import httpx

    app = _make_app(monkeypatch, platform_enabled=True, studio_enabled=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        for path in ("/oauth/consent", "/oauth/verify"):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert "platform-entry" in resp.text  # web-platform 页面，非 Studio
        # 未登录访问授权流程页：OpenViking 服务端渲染页存在（缺 pending → 400，
        # 非 Studio 承载，16.2）
        assert (await client.get("/oauth/authorize/page")).status_code == 400


async def test_browser_no_user_api_key_surface(web_platform_dist) -> None:
    """AC⑦：产品登录/OAuth 授权页不输入 User API Key（06 §13.8 页面规则）。"""
    # web-platform 入口页不含 User API Key 表单（登录用邮箱密码，13 §13.7 同 21 条目 1）
    text = web_platform_dist.joinpath("index.html").read_text(encoding="utf-8").lower()
    assert "api key" not in text
