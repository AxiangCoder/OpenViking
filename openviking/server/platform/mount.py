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
    from openviking.server.platform.registry.service import ContentRegistryService
    from openviking.server.platform.routers import (
        admin_router,
        auth_router,
        me_router,
        platform_router,
        resources_router,
        skills_router,
    )
    from openviking.server.platform.skills.configs import FakeSkillConfigsAdapter
    from openviking.server.platform.skills.control_plane import (
        FakeSkillContentAdapter,
        FakeSkillMigrationAdapter,
    )
    from openviking.server.platform.skills.packages import FakeSkillPackageAdapter
    from openviking.server.platform.skills.service import SkillService

    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    auth = AuthService(repo, rbac)
    provisioning = ProvisioningService(repo, ProvisioningRepository(), FakeControlPlane())
    admin = AdminService(repo, rbac, auth, provisioning=provisioning)
    registry_store = RegistryRepository()
    deletion = DeletionService(repo, registry_store)
    aggregates = AggregateService(registry_store)
    registry_service = ContentRegistryService(registry_store, ProvisioningRepository())
    facade = _build_facade(rbac, registry_service, registry_store, repo, config)
    content = FakeSkillContentAdapter()
    skills = SkillService(
        iam=repo,
        store=registry_store,
        deletion=deletion,
        packages=FakeSkillPackageAdapter(),
        content=content,
        migration=FakeSkillMigrationAdapter(content=content),
        configs=FakeSkillConfigsAdapter(),
    )

    app.state.iam_repository = repo
    app.state.iam_rbac_service = rbac
    app.state.iam_auth_service = auth
    app.state.iam_admin_service = admin
    app.state.iam_provisioning_service = provisioning
    app.state.iam_registry_store = registry_store
    app.state.iam_registry_service = registry_service
    app.state.iam_facade_service = facade
    app.state.iam_deletion_service = deletion
    app.state.iam_aggregate_service = aggregates
    app.state.iam_skill_service = skills
    app.state.platform_config = platform_config
    _mount_resource_services(app, repo, registry_store, registry_service, facade, deletion)

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(admin_router)
    app.include_router(platform_router)
    app.include_router(resources_router)
    app.include_router(skills_router)


def _build_facade(rbac, registry_service, registry_store, repo, config):
    """ProductFacadeService 装配（05 §11.2：OVMapper + AuthorizationService）。"""
    from openviking.server.platform.auth.uri_policy import AuthorizationService
    from openviking.server.platform.db import session_factory
    from openviking.server.platform.facade import ProductFacadeService
    from openviking.server.platform.target_policy import TargetPolicy

    authorization = AuthorizationService(
        TargetPolicy(), ProductFacadeService.build_ov_mapper(repo, session_factory)
    )
    return ProductFacadeService(
        authorization=authorization,
        registry=registry_service,
        control_plane=None,
        registry_store=registry_store,
    )


def _mount_resource_services(app, repo, registry_store, registry_service, facade, deletion) -> None:
    """P2-E3：Resource 服务装配（app.state 注入 + Purge 处理器注册）。"""
    from openviking.server.platform.config import platform_config
    from openviking.server.platform.resource.execution import FakeResourceExecutionPlane
    from openviking.server.platform.resource.purge import ResourcePurgeHandler
    from openviking.server.platform.resource.service import ResourceService
    from openviking.server.platform.resource.storage import MemoryTempUploadStore

    execution = FakeResourceExecutionPlane()
    uploads = MemoryTempUploadStore()
    resource_service = ResourceService(
        facade=facade,
        registry=registry_service,
        iam_repo=repo,
        execution=execution,
        uploads=uploads,
        config=platform_config,
    )
    app.state.iam_resource_service = resource_service
    app.state.iam_resource_execution = execution
    app.state.iam_resource_uploads = uploads

    if hasattr(app.state, "iam_purge_worker"):
        purge_worker = app.state.iam_purge_worker
    else:
        from openviking.server.platform.deletion.worker import PurgeWorker

        purge_worker = PurgeWorker(repo, registry_store)
        app.state.iam_purge_worker = purge_worker
    purge_worker.register_handler(
        "resource", ResourcePurgeHandler(execution=execution, store=registry_store)
    )
