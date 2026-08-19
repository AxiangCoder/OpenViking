"""P5-E1 create_app 生产挂载与路由边界验证（14 号计划 §99.1，06 §16.2/§16.3）。

覆盖验收标准 ①–⑤ 的应用层部分：

- ① 平台模式公网路由表无 /studio、请求 /studio/* 404 且不重定向登录页；
  裸根 / 返回平台入口页；
- ② 同源 SPA 深链（/app/*、/admin/*、/platform/*、/login、/oauth/consent、
  /oauth/verify）返回 200；
- ③ 16.2 路由表逐条验证路径与处理器对应；
- ④ create_app 真实配置完整启动、Platform Routers 注册、web-platform 静态
  可访问、既有路由回归无破坏（test_mount.py 既有用例 + 本文件）；
- ⑤ 低层运维入口应用层未挂载（两级断言之一；代理层 deny 见 deploy/nginx
  测试）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openviking.server.config import ServerConfig

SPA_EXACT_PATHS = ("/", "/login", "/oauth/consent", "/oauth/verify")
SPA_PREFIX_PATHS = ("/app", "/admin", "/platform")

WEB_PLATFORM_BUNDLE = """\
<!doctype html><html><head><title>OpenViking Platform</title></head>
<body><div id="root">platform-entry</div></body></html>
"""


@pytest.fixture()
def web_platform_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 web-platform bundle（SPA 入口 + 一个静态资源）。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(WEB_PLATFORM_BUNDLE, encoding="utf-8")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('platform')", encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_WEB_PLATFORM_DIR", str(dist))
    return dist


@pytest.fixture()
def web_studio_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 web-studio bundle（验证 Studio 开关）。"""
    dist = tmp_path / "studio-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>studio</html>", encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_WEB_STUDIO_DIR", str(dist))
    return dist


def _routes(app) -> set[str]:
    return {
        str(getattr(r, "path", None))
        for r in app.routes
        if getattr(r, "path", None) is not None
    }


def _make_app(**overrides):
    from openviking.server.app import create_app

    return create_app(config=ServerConfig(**overrides))


# ── 验收 ①：公网路由表无 /studio、裸根 / 返回平台入口页 ──


async def test_platform_mode_no_studio_route(
    web_platform_dist: Path, web_studio_dist: Path
) -> None:
    """AC①：platform 模式下 bundle 存在也不挂载 /studio、不注册根重定向。"""
    app = _make_app(platform_enabled=True)
    routes = _routes(app)
    assert not any(r.startswith("/studio") for r in routes), "public route table has /studio"
    root_handlers = [
        r for r in app.routes if getattr(r, "path", None) == "/" and "GET" in getattr(r, "methods", [])
    ]
    assert root_handlers, "root route missing"
    # 裸根 / 返回 web-platform 入口（不 302 到 /studio/）
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/studio/", follow_redirects=False)
        assert resp.status_code == 404
        resp_root = await client.get("/", follow_redirects=False)
        assert resp_root.status_code == 200
        assert "platform-entry" in resp_root.text


async def test_platform_mode_studio_404_not_redirect(web_platform_dist: Path) -> None:
    """AC①：/studio/* 404 且不重定向登录页（应用层断言）。"""
    import httpx

    app = _make_app(platform_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in ("/studio", "/studio/", "/studio/index.html", "/studio/app"):
            resp = await client.get(path, follow_redirects=False)
            assert resp.status_code == 404, path


async def test_legacy_mode_studio_behavior_unchanged(web_studio_dist: Path) -> None:
    """非平台模式保持既有行为：bundle 存在 → /studio 挂载 + 根 302。"""
    import httpx

    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/studio/"
        assert (await client.get("/studio/")).status_code == 200


async def test_legacy_mode_studio_explicitly_disabled(web_studio_dist: Path) -> None:
    """非平台模式显式 studio_enabled=false → 不挂载 /studio、根不重定向。"""
    import httpx

    app = _make_app(studio_enabled=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/studio/")).status_code == 404
        assert (await client.get("/", follow_redirects=False)).status_code == 404  # 无根处理器


async def test_platform_mode_studio_explicitly_enabled(web_studio_dist: Path) -> None:
    """平台模式显式 studio_enabled=true → 私网运维入口可挂载 /studio。"""
    import httpx

    app = _make_app(platform_enabled=True, studio_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/studio/")).status_code == 200


# ── 验收 ②：同源 SPA 深链 200 ──


async def test_platform_mode_spa_deep_links_200(web_platform_dist: Path) -> None:
    """AC②：SPA 深链（/app/*、/admin/*、/platform/*、精确页）返回 200。"""
    import httpx

    app = _make_app(platform_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for exact in SPA_EXACT_PATHS:
            resp = await client.get(exact)
            assert resp.status_code == 200, exact
            assert "platform-entry" in resp.text
        for prefix in SPA_PREFIX_PATHS:
            for deep in (f"{prefix}/", f"{prefix}/users/abc", f"{prefix}/skills/x/detail"):
                resp = await client.get(deep)
                assert resp.status_code == 200, deep
                assert "platform-entry" in resp.text
        # 静态资源按真实文件返回
        assert (await client.get("/assets/app.js")).status_code == 200


async def test_platform_mode_oauth_pages_are_spa(web_platform_dist: Path) -> None:
    """AC②/⑦：/oauth/consent 与 /oauth/verify 为 web-platform SPA 授权页。"""
    import httpx

    app = _make_app(platform_enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in ("/oauth/consent", "/oauth/verify"):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert "platform-entry" in resp.text


# ── 验收 ③：16.2 路由表逐条验证 ──


def test_route_table_16_2(web_platform_dist: Path) -> None:
    """AC③：16.2 路由表逐条验证路径与处理器对应。"""
    from openviking.server.app import create_app

    app = create_app(config=ServerConfig(platform_enabled=True))
    routes = _routes(app)

    # /、/login、/app/*、/admin/*、/platform/* → web-platform SPA
    assert "/" in routes
    assert "/login" in routes
    assert "/app/{path:path}" in routes
    assert "/admin/{path:path}" in routes
    assert "/platform/{path:path}" in routes
    # /oauth/consent、/oauth/verify → web-platform 授权页（精确路由）
    assert "/oauth/consent" in routes
    assert "/oauth/verify" in routes
    # /studio/* → 公网不注册
    assert not any(r.startswith("/studio") for r in routes)
    # /api/platform/v1/* → Platform Router
    assert "/api/platform/v1/auth/login" in routes
    assert "/api/platform/v1/admin/users" in routes
    assert "/api/platform/v1/platform/accounts" in routes
    assert "/api/platform/v1/me/api-keys" in routes
    # /api/v1/* → OpenViking Router（低层白名单仅 admin）
    assert "/api/v1/content/write" in routes
    assert any(r.startswith("/api/v1/admin") for r in routes)
    assert not any(r.startswith("/api/v1/snapshot") for r in routes)
    assert not any(r.startswith("/api/v1/debug") for r in routes)
    assert not any(r.startswith("/api/v1/observer") for r in routes)
    assert not any(r.startswith("/api/v1/pack") for r in routes)
    assert not any(r.startswith("/api/v1/console") for r in routes)
    # /mcp、OAuth 协议端点 → OpenViking（/mcp 始终注册；OAuth 端点依赖 ov.conf
    # 存在，见 test_mount.py 既有断言）
    assert "/mcp" in routes


# ── 低层 router 条件挂载开关（06 §16.3）──


def test_low_level_routers_explicit_whitelist(web_platform_dist: Path) -> None:
    """显式白名单：仅挂载所列低层 router（平台模式）。"""
    app = _make_app(platform_enabled=True, low_level_routers_enabled=["admin", "debug"])
    routes = _routes(app)
    assert any(r.startswith("/api/v1/admin") for r in routes)
    assert any(r.startswith("/api/v1/debug") for r in routes)
    assert not any(r.startswith("/api/v1/observer") for r in routes)
    assert not any(r.startswith("/api/v1/snapshot") for r in routes)
    assert not any(r.startswith("/api/v1/pack") for r in routes)
    assert not any(r.startswith("/api/v1/console") for r in routes)
    assert not any(r.startswith("/webdav") for r in routes)


def test_low_level_routers_explicit_all_in_legacy(web_platform_dist: Path) -> None:
    """非平台模式显式白名单同样生效（开关与模式正交）。"""
    app = _make_app(low_level_routers_enabled=["console"])
    routes = _routes(app)
    assert any(r.startswith("/api/v1/console") for r in routes)
    assert not any(r.startswith("/api/v1/debug") for r in routes)
    assert not any(r.startswith("/api/v1/observer") for r in routes)


def test_low_level_routers_unknown_name_rejected() -> None:
    """未知 router 名 → 配置错误快速失败（防 typo 静默放行）。"""
    with pytest.raises(ValueError, match="Unknown low_level_routers_enabled"):
        _make_app(low_level_routers_enabled=["admin", "backdoor"])


# ── 验收 ④：create_app 真实配置完整启动 ──


async def test_production_config_mount_full(web_platform_dist: Path) -> None:
    """AC④：生产配置（platform + real 适配器）完整启动、Platform Routers 注册、
    web-platform 静态可访问、app.state 装配完整。"""
    app = _make_app(platform_enabled=True, platform_adapter_mode="real")
    assert app.state.iam_repository is not None
    assert app.state.iam_provisioning_service is not None
    assert app.state.iam_skill_service is not None
    assert app.state.iam_session_service is not None
    assert app.state.iam_search_service is not None
    # real 适配器装配（惰性，不触碰运行时）
    from openviking.server.platform.adapters import (
        RealControlPlaneAdapter,
        RealSearchEngine,
        RealSessionBackend,
        RealSkillConfigsAdapter,
        RealSkillContentAdapter,
    )

    assert isinstance(app.state.iam_provisioning_service._control_plane, RealControlPlaneAdapter)
    assert isinstance(app.state.iam_skill_service._configs, RealSkillConfigsAdapter)
    assert isinstance(app.state.iam_skill_service._content, RealSkillContentAdapter)
    assert isinstance(app.state.iam_session_service._backend, RealSessionBackend)
    assert isinstance(app.state.iam_search_service._engine, RealSearchEngine)
    # Purge 真实处理器注册
    purge_handlers = app.state.iam_purge_worker._handlers
    from openviking.server.platform.adapters import RealResourcePurgeHandler

    assert isinstance(purge_handlers.get("resource"), RealResourcePurgeHandler)
    # web-platform 入口可访问
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/")).status_code == 200


def test_fake_adapter_mode_default(web_platform_dist: Path) -> None:
    """默认 fake 适配器模式：开发/既有行为不变（既有 tests 全绿）。"""
    app = _make_app(platform_enabled=True)
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.session.backend import FakeSearchEngine, FakeSessionBackend
    from openviking.server.platform.skills.configs import FakeSkillConfigsAdapter
    from openviking.server.platform.skills.control_plane import (
        FakeSkillContentAdapter,
    )

    assert isinstance(app.state.iam_provisioning_service._control_plane, FakeControlPlane)
    assert isinstance(app.state.iam_skill_service._configs, FakeSkillConfigsAdapter)
    assert isinstance(app.state.iam_skill_service._content, FakeSkillContentAdapter)
    assert isinstance(app.state.iam_session_service._backend, FakeSessionBackend)
    assert isinstance(app.state.iam_search_service._engine, FakeSearchEngine)


def test_invalid_adapter_mode_rejected() -> None:
    with pytest.raises(ValueError, match="platform_adapter_mode"):
        _make_app(platform_enabled=True, platform_adapter_mode="hybrid")


# ── 验收 ⑤：低层入口应用层未挂载（第一级断言）──


def test_low_level_ops_not_mounted_in_production(web_platform_dist: Path) -> None:
    """AC⑤：生产配置（real 模式）下 WebDAV/Snapshot/Pack/Debug/Observer/
    Console 应用层未挂载；admin 保留。"""
    app = _make_app(platform_enabled=True, platform_adapter_mode="real")
    routes = _routes(app)
    for prefix in (
        "/api/v1/console",
        "/api/v1/snapshot",
        "/api/v1/pack",
        "/api/v1/debug",
        "/api/v1/observer",
        "/webdav",
    ):
        assert not any(r.startswith(prefix) for r in routes), prefix
    assert any(r.startswith("/api/v1/admin") for r in routes)


# ── 验收 ⑥：镜像/仓库无明文密钥（静态扫描，部署配置侧）──


def test_no_secret_plaintext_in_repo() -> None:
    """AC⑥：仓库（deploy/、docs 配置模板）无 Root Key/数据库口令/签名密钥明文。

    扫描 deploy/ 与产品配置模板中的占位符约定：口令类字段必须为空或
    ${ENV} 引用，不得出现可复用的明文口令。
    """
    import re

    repo_root = Path(__file__).resolve().parents[3]
    deploy_root = repo_root / "deploy"
    suspicious = []
    secret_patterns = [
        re.compile(r"postgresql://[^$/\s]+:[^$@/\s]+@"),  # 带口令的 DSN（非 env 引用）
        re.compile(r"(root_api_key|ROOT_API_KEY|PASSWORD|PASSWORD:|password:)\s*[:=]?\s*['\"]?[A-Za-z0-9_\-]{12,}['\"]?"),
    ]
    for path in deploy_root.rglob("*"):
        if path.is_file() and path.suffix in (".yml", ".yaml", ".json", ".conf", ".template", ".env"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in secret_patterns:
                for match in pattern.finditer(text):
                    # 跳过 env 占位符与开发默认值的注释说明
                    snippet = match.group(0)
                    if "$" in snippet or "{env}" in snippet or "dev" in snippet.lower():
                        continue
                    suspicious.append(f"{path.relative_to(repo_root)}: {snippet[:60]}")
    assert not suspicious, "\n".join(suspicious)
