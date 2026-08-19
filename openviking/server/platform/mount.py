"""create_app 平台挂载（P2-E1 §97.1 AC⑥ 开发态 + P5-E1 §99.1 生产挂载）。

将 Platform 路由（auth/me/admin/platform/oauth 等，05 §12.3–12.6）挂载进
真实 `create_app()`（openviking/server/app.py），由 `ServerConfig.platform_enabled`
开启。P5-E1 闭合生产态：

- `mount_web_platform_spa`：web-platform 静态 + SPA 深链 fallback（16.2 路由表
  `/`、`/login`、`/app/*`、`/admin/*`、`/platform/*`、`/oauth/consent`、
  `/oauth/verify`；不注册 `/studio` 与 `/`→`/studio/` 重定向）；
- 真实适配器接线：`ServerConfig.platform_adapter_mode`（默认 `fake` 保持开发态
  行为与既有测试；staging/生产配置模板设 `real`）→ Fake 适配层替换为
  `adapters/` 包的真实实现（ControlPlane/SkillConfigs/Search/SessionBackend/
  SkillContent 私有根/SkillMigration/Purge），全部惰性解析 OpenViking 运行时。

装配：app.state 注入 repository/RBAC/AuthService/AdminService/
ProvisioningService/DeletionService/AggregateService；DB 连接使用
platform_config 的 `OV_PLATFORM_DATABASE_URL`（db.py 进程级单例）。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse


def mount_web_platform_spa(app: FastAPI) -> None:
    """P5-E1（06 §16.2 路由表）：web-platform SPA 静态挂载 + 深链 fallback。

    - `/`、`/login`、`/oauth/consent`、`/oauth/verify` → index.html；
    - `/app/*`、`/admin/*`、`/platform/*`、`/assets/*` → 真实文件优先，
      深链回退 index.html（同源 SPA 深链 200）；
    - 不注册 `/studio` 与 `/`→`/studio/` 重定向（06 §13.6/§16.2；
      /studio 由 app.py Studio 开关控制，平台模式默认不挂载）。

    bundle 解析：`OPENVIKING_WEB_PLATFORM_DIR`（开发态）→ 包内
    `openviking/web_platform/dist`（镜像/包构建产物，Dockerfile web-platform
    构建阶段产出）。bundle 缺失时仅告警，产品 API 不受影响。
    """
    env_dir = os.environ.get("OPENVIKING_WEB_PLATFORM_DIR", "").strip()
    if env_dir:
        root = Path(env_dir)
    else:
        root = Path(__file__).resolve().parent.parent / "web_platform" / "dist"
    if not root.is_dir() or not (root / "index.html").is_file():
        from openviking_cli.utils import get_logger

        get_logger(__name__).warning(
            "web-platform bundle not found at %s; product entry pages unavailable", root
        )
        return

    from openviking_cli.utils import get_logger

    logger = get_logger(__name__)
    root = root.resolve()
    index = root / "index.html"

    def _response(path: Path, *, no_store: bool = False) -> Response:
        headers = (
            {"Cache-Control": "no-store"}
            if no_store
            else {"Cache-Control": "public, max-age=3600"}
        )
        return FileResponse(path, headers=headers)

    async def _entry() -> Response:
        return _response(index, no_store=True)

    app.get("/", include_in_schema=False)(_entry)
    for _exact in ("/login", "/oauth/consent", "/oauth/verify"):

        def _exact_page(_path: str = _exact) -> Response:
            return _response(index, no_store=True)

        app.get(_exact, include_in_schema=False)(_exact_page)

    def _spa_fallback(path: str) -> Response:
        try:
            requested = (root / path).resolve()
        except OSError:
            return _response(index, no_store=True)
        if requested.is_relative_to(root) and requested.is_file():
            return _response(requested)
        return _response(index, no_store=True)

    for _prefix in ("/app", "/admin", "/platform", "/assets"):
        app.get(f"{_prefix}/{{path:path}}", include_in_schema=False)(_spa_fallback)

    logger.info("web-platform mounted at / (SPA) from %s", root)


def mount_platform_routers(app: FastAPI, config) -> None:
    """`platform_enabled=True` 时挂载 Platform 路由并注入 app.state 依赖。"""
    if not getattr(config, "platform_enabled", False):
        return
    from openviking.server.platform.admin.service import AdminService
    from openviking.server.platform.aggregates import AggregateService
    from openviking.server.platform.auth.service import AuthService
    from openviking.server.platform.config import platform_config
    from openviking.server.platform.dashboard.service import DashboardService
    from openviking.server.platform.deletion.service import DeletionService
    from openviking.server.platform.iam import PostgresIamRepository, RbacService
    from openviking.server.platform.provisioning.repository import ProvisioningRepository
    from openviking.server.platform.provisioning.service import ProvisioningService
    from openviking.server.platform.registry.repository import RegistryRepository
    from openviking.server.platform.registry.service import ContentRegistryService
    from openviking.server.platform.routers import (
        admin_router,
        auth_router,
        me_router,
        member_data_router,
        oauth_router,
        platform_router,
        resources_router,
        sessions_router,
        skills_router,
    )
    from openviking.server.platform.search.service import SearchProductService
    from openviking.server.platform.session.service import SessionProductService
    from openviking.server.platform.skills.packages import TempUploadStorePackageAdapter
    from openviking.server.platform.skills.service import SkillService

    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    auth = AuthService(repo, rbac)
    adapter_mode = _resolve_adapter_mode(config)
    control_plane, content, migration, configs, session_backend, search_engine = (
        _build_adapters(adapter_mode)
    )
    provisioning = ProvisioningService(repo, ProvisioningRepository(), control_plane)
    admin = AdminService(repo, rbac, auth, provisioning=provisioning)
    registry_store = RegistryRepository()
    deletion = DeletionService(repo, registry_store)
    aggregates = AggregateService(registry_store)
    registry_service = ContentRegistryService(registry_store, ProvisioningRepository())
    facade = _build_facade(rbac, registry_service, registry_store, repo, config)

    # P2-E4 收尾：Skill 包消费复用 P2-E3 受控临时上传存储（10 §61.5，
    # me/resource-uploads 链路），Resource 与 Skill 共享同一 store。
    from openviking.server.platform.resource.storage import MemoryTempUploadStore

    uploads = MemoryTempUploadStore()
    skills = SkillService(
        iam=repo,
        store=registry_store,
        deletion=deletion,
        packages=TempUploadStorePackageAdapter(uploads),
        content=content,
        migration=migration,
        configs=configs,
    )
    sessions = SessionProductService(repo, backend=session_backend, registry=registry_store)
    search = SearchProductService(repo, engine=search_engine, registry=registry_store)
    dashboard = DashboardService()

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
    app.state.iam_session_service = sessions
    app.state.iam_search_service = search
    app.state.iam_dashboard_service = dashboard
    app.state.iam_session_backend = session_backend
    app.state.iam_search_engine = search_engine
    app.state.platform_config = platform_config
    _mount_resource_services(
        app, repo, registry_store, registry_service, facade, deletion, uploads, config=config
    )

    # P2-E6a（05 §11.5，14 号计划 §97.6）：低层入口统一守卫装配。
    # 平台 Session 工厂与审计写入（插件/守卫以 Key 归属者身份审计，AC④）。
    from openviking.server.platform.db import session_factory as _platform_session_factory
    from openviking.server.platform.lowlevel.bridge import McpPlatformBridge
    from openviking.server.platform.lowlevel.guard import LowLevelPolicyGuard

    async def _lowlevel_audit(
        principal,
        *,
        action: str,
        result: str,
        reason: str | None,
        target_uri: str,
        request_id: str,
        metadata: dict | None,
    ) -> None:
        async with _platform_session_factory() as audit_session:
            await repo.append_audit_event(
                audit_session,
                request_id=request_id or None,
                account_id=principal.actor_account_id,
                actor_type="user",
                actor_user_id=principal.actor_user_id,
                actor_account_id=principal.actor_account_id,
                actor_session_id=principal.session_id,
                authentication_method=principal.authentication_method,
                actor_credential_id=principal.credential_id,
                subject_account_id=principal.actor_account_id,
                action=action,
                target_type="viking_uri",
                target_id=target_uri,
                target_visibility=(metadata or {}).get("visibility"),
                scope="platform" if principal.actor_account_id is None else "account",
                result=result,
                reason=reason,
                metadata=metadata,
            )
            await audit_session.commit()

    guard = LowLevelPolicyGuard(facade.authorization, audit=_lowlevel_audit)
    app.state.platform_enabled = True
    app.state.platform_lowlevel_guard = guard
    app.state.platform_session_factory = _platform_session_factory
    app.state.platform_mcp_bridge = McpPlatformBridge(
        guard=guard,
        registry=registry_service,
        registry_store=registry_store,
        session_factory=_platform_session_factory,
        deletion_purge_days=platform_config.deletion_purge_days,
    )

    # P5-E2（14 号计划 §99.2，06 §16.3/§17.3）：健康检查装配。检查集合挂载
    # app.state.platform_health_checks，由 system.py /health、/ready 消费；
    # Session cleanup / Purge Worker 状态如实注入（周期调度由部署层负责，
    # 与 ProvisioningWorker 同模式，runbook 见 docs/design/.../p5-e2-init-runbook.md）。
    from openviking.server.platform.auth.worker import SessionCleanupWorker
    from openviking.server.platform.health import PlatformHealthChecks

    session_cleanup_worker = SessionCleanupWorker(config=platform_config)
    app.state.iam_session_cleanup_worker = session_cleanup_worker
    app.state.platform_health_checks = PlatformHealthChecks(_platform_session_factory)
    app.state.platform_health_checks.session_cleanup_worker = session_cleanup_worker
    app.state.platform_health_checks.purge_worker = app.state.iam_purge_worker

    # P2-E6b：MCP OAuth PostgreSQL 存储（04 §10.13）。dev 态挂载后，
    # 产品 OAuth 端点使用 PG 存储；SDK 协议端点换存由 app.py 在
    # platform_enabled 时完成（同样指向 PG），保证单一事实来源。
    from openviking.server.platform.iam.pg_oauth_store import PostgresOAuthStore

    app.state.platform_oauth_store = PostgresOAuthStore(
        _platform_session_factory, label="iam_oauth_* (PostgreSQL)"
    )

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(admin_router)
    app.include_router(platform_router)
    app.include_router(resources_router)
    app.include_router(skills_router)
    app.include_router(sessions_router)
    app.include_router(member_data_router)
    app.include_router(oauth_router)

    # P5-E1（06 §16.2 路由表）：web-platform SPA 静态挂载 + 深链 fallback
    # （/、/login、/app/*、/admin/*、/platform/*、/oauth/consent、
    #  /oauth/verify；不注册 /studio 与 /→/studio/ 重定向）。
    mount_web_platform_spa(app)


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


def _mount_resource_services(
    app, repo, registry_store, registry_service, facade, deletion, uploads=None, config=None
) -> None:
    """P2-E3：Resource 服务装配（app.state 注入 + Purge 处理器注册）。

    `uploads`：受控临时上传存储（04 §10.14）；缺省自建，Skill 装配
    （mount_platform_routers）传入共享实例以打通 10 §61.5 复用链路。
    `config`：ServerConfig（P5-E1 适配器模式判定）。
    """
    from openviking.server.platform.config import platform_config
    from openviking.server.platform.resource.execution import FakeResourceExecutionPlane
    from openviking.server.platform.resource.purge import ResourcePurgeHandler
    from openviking.server.platform.resource.service import ResourceService
    from openviking.server.platform.resource.storage import MemoryTempUploadStore

    # P5-E1：Resource 执行面真实摄取管线未接线（需完整运行时，见 adapters/
    # __init__.py 未接线项）；Purge 物理清理按适配器模式切换（real → VikingFS.rm）。
    execution = FakeResourceExecutionPlane()
    uploads = uploads or MemoryTempUploadStore()
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
    if _resolve_adapter_mode(config) == "real":
        from openviking.server.platform.adapters import RealResourcePurgeHandler

        purge_handler = RealResourcePurgeHandler()
    else:
        purge_handler = ResourcePurgeHandler(execution=execution, store=registry_store)
    purge_worker.register_handler("resource", purge_handler)


def _resolve_adapter_mode(config) -> str:
    """适配器模式：`platform_adapter_mode`（ServerConfig，real/fake，默认 fake）。"""
    mode = str(getattr(config, "platform_adapter_mode", "fake") or "fake").strip().lower()
    if mode not in ("real", "fake"):
        raise ValueError(f"platform_adapter_mode must be 'real' or 'fake', got {mode!r}")
    return mode


def _build_adapters(mode: str):
    """P5-E1：按适配器模式装配 Protocol 实现（real → 真实 OpenViking 接线）。

    `real` 模式全部惰性解析运行时（create_app lifespan 后可用），装配期
    零运行时依赖；`fake` 模式保持既有受控适配层（开发/测试默认）。
    """
    if mode == "real":
        from openviking.server.platform.adapters import (
            RealControlPlaneAdapter,
            RealSearchEngine,
            RealSessionBackend,
            RealSkillConfigsAdapter,
            RealSkillContentAdapter,
            RealSkillMigrationAdapter,
        )

        return (
            RealControlPlaneAdapter(),
            RealSkillContentAdapter(),
            RealSkillMigrationAdapter(),
            RealSkillConfigsAdapter(),
            RealSessionBackend(),
            RealSearchEngine(),
        )
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.session.backend import FakeSearchEngine, FakeSessionBackend
    from openviking.server.platform.skills.configs import FakeSkillConfigsAdapter
    from openviking.server.platform.skills.control_plane import (
        FakeSkillContentAdapter,
        FakeSkillMigrationAdapter,
    )

    content = FakeSkillContentAdapter()
    return (
        FakeControlPlane(),
        content,
        FakeSkillMigrationAdapter(content=content),
        FakeSkillConfigsAdapter(),
        FakeSessionBackend(),
        FakeSearchEngine(),
    )
