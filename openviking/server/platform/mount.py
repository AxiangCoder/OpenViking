"""create_app 开发态挂载（14 号计划 §97.1 AC⑥，Spike 风险 9 的开发态验证点）。

将 Platform 路由（auth/me/admin/platform，05 §12.3–12.6）挂载进真实
`create_app()`（openviking/server/app.py），由 `ServerConfig.platform_enabled`
开启（开发态开关，默认关闭）。本 Epic 仅验证 `/api/platform/v1` 与 `/api/v1`、
`/mcp` 并存不冲突；生产路由与公网边界（反代/最低权限/系统入口 404）由
P5-E1 闭合（14 号计划 §95 Phase 5、§97.1）。

开发态装配：app.state 注入 repository/RBAC/AuthService/AdminService/
ProvisioningService/DeletionService/AggregateService；Provisioning 控制面
使用 `FakeControlPlane`（真实 OpenViking namespace 初始化属 P5-E1 初始化
阶段，见 provisioning/control_plane.py）。DB 连接使用 platform_config 的
`OV_PLATFORM_DATABASE_URL`（db.py 进程级单例）。
"""

from __future__ import annotations

from fastapi import FastAPI


def mount_platform_routers(app: FastAPI, config) -> None:
    """`platform_enabled=True` 时挂载 Platform 路由并注入 app.state 依赖。"""
    if not getattr(config, "platform_enabled", False):
        return
    from openviking.server.platform.admin.service import AdminService
    from openviking.server.platform.aggregates import AggregateService
    from openviking.server.platform.auth.service import AuthService
    from openviking.server.platform.config import platform_config
    from openviking.server.platform.deletion.service import DeletionService
    from openviking.server.platform.iam import PostgresIamRepository, RbacService
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.provisioning.repository import ProvisioningRepository
    from openviking.server.platform.provisioning.service import ProvisioningService
    from openviking.server.platform.registry.repository import RegistryRepository
    from openviking.server.platform.routers import (
        admin_router,
        auth_router,
        me_router,
        platform_router,
    )

    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    auth = AuthService(repo, rbac)
    provisioning = ProvisioningService(repo, ProvisioningRepository(), FakeControlPlane())
    admin = AdminService(repo, rbac, auth, provisioning=provisioning)
    registry_store = RegistryRepository()
    deletion = DeletionService(repo, registry_store)
    aggregates = AggregateService(registry_store)

    app.state.iam_repository = repo
    app.state.iam_rbac_service = rbac
    app.state.iam_auth_service = auth
    app.state.iam_admin_service = admin
    app.state.iam_provisioning_service = provisioning
    app.state.iam_registry_store = registry_store
    app.state.iam_deletion_service = deletion
    app.state.iam_aggregate_service = aggregates
    app.state.platform_config = platform_config

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(admin_router)
    app.include_router(platform_router)
