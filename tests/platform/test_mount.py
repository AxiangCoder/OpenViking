"""P2-E1 create_app 真实挂载验证（14 号计划 §97.1 AC⑥，Spike 风险 9 开发态验证点）。

仅验证 `/api/platform/v1` 与 `/api/v1`、`/mcp` 并存不冲突（路由注册共存）；
生产路由与公网边界（反代/系统入口 404）由 P5-E1 闭合。
"""

from __future__ import annotations

from openviking.server.config import ServerConfig


def _route_paths(app) -> tuple[set[str], set[str]]:
    paths = {getattr(r, "path", None) for r in app.routes}
    paths.discard(None)
    platform_paths = {p for p in paths if p.startswith("/api/platform/v1")}
    return paths, platform_paths


def test_create_app_with_platform_enabled_mounts_all_surfaces() -> None:
    """AC⑥：create_app(ServerConfig(platform_enabled=True)) 后三面并存。"""
    from openviking.server.app import create_app

    app = create_app(config=ServerConfig(platform_enabled=True))
    paths, platform_paths = _route_paths(app)

    # Platform 面完整挂载
    assert "/api/platform/v1/auth/login" in paths
    assert "/api/platform/v1/me/api-keys" in paths
    assert "/api/platform/v1/admin/users" in paths
    assert "/api/platform/v1/platform/accounts" in paths
    assert "/api/platform/v1/platform/accounts/{account_id}/provisioning/retry" in paths

    # P2-E4：Skill 产品 API 与 skill-configs 挂载（10 §61.1–§61.5，05 §12.5/§12.6）
    assert "/api/platform/v1/me/skills" in paths
    assert "/api/platform/v1/me/skills/{skill_id}/restore" in paths
    assert "/api/platform/v1/me/skill-configs/{skill_id}" in paths
    assert "/api/platform/v1/account/skills" in paths
    assert "/api/platform/v1/admin/users/{user_id}/skills" in paths
    assert "/api/platform/v1/admin/users/{user_id}/skills/{skill_id}/publish" in paths
    assert "/api/platform/v1/platform/accounts/{account_id}/skills" in paths
    assert "/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/skills/{skill_id}" in paths

    # OpenViking 原生面与 MCP 并存
    native_paths = {p for p in paths if p.startswith("/api/v1")}
    assert native_paths, "native /api/v1 routes must be mounted"
    assert "/mcp" in paths

    # 不冲突：Platform 路径与原生 /api/v1 路径无交集、无重复精确路径
    assert platform_paths.isdisjoint(native_paths)
    assert len(platform_paths) == len(
        {getattr(r, "path", "") for r in app.routes if getattr(r, "path", "").startswith("/api/platform/v1")}
    )
    # app.state 依赖装配完成（create_app 挂载约定，P5-E1 沿用）
    assert app.state.iam_repository is not None
    assert app.state.iam_provisioning_service is not None


def test_create_app_default_does_not_mount_platform() -> None:
    """默认关闭：platform_enabled=False 不挂载 Platform 路由（不影响既有行为）。"""
    from openviking.server.app import create_app

    app = create_app(config=ServerConfig())
    paths, platform_paths = _route_paths(app)
    assert platform_paths == set()
    assert "/mcp" in paths  # 原生面不受影响


def test_create_app_platform_mode_unmounts_ops_routers() -> None:
    """AC③（08 §28.6，14 号计划 §97.6）：平台模式下运维/低层 Router 应用层
    不挂载 → 404（WebDAV/Snapshot/Pack/Debug/Observer/Console）；
    公网路由网络边界由 P5-E1 闭合。"""
    from openviking.server.app import create_app

    app = create_app(config=ServerConfig(platform_enabled=True))
    paths = {getattr(r, "path", None) for r in app.routes}
    paths.discard(None)

    for prefix in (
        "/api/v1/console",
        "/api/v1/snapshot",
        "/api/v1/pack",
        "/api/v1/debug",
        "/api/v1/observer",
        "/webdav",
    ):
        assert not any(p.startswith(prefix) for p in paths), prefix
    # 产品面与低层产品入口仍挂载
    assert "/api/v1/content/write" in paths
    assert "/api/v1/fs/mv" in paths
    assert "/api/v1/resources" in paths


def test_create_app_legacy_mode_keeps_ops_routers() -> None:
    """非平台模式：运维 Router 保持挂载（既有行为不变）。"""
    from openviking.server.app import create_app

    app = create_app(config=ServerConfig())
    paths = {getattr(r, "path", None) for r in app.routes}
    paths.discard(None)
    assert any(p.startswith("/api/v1/console") for p in paths)
    assert any(p.startswith("/webdav") for p in paths)
    assert any(p.startswith("/api/v1/snapshot") for p in paths)
