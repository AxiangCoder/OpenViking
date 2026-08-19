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
