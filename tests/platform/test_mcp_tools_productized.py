"""P2-E6a MCP 13 Tool 产品化接入测试（08 §28.5，14 号计划 §97.6）。

验收映射：
- AC③：MCP `forget` 与产品删除一致进 30 天回收期（不调用底层 rm）；
  `grep/glob` 对产品凭证不可用；WebDAV/Snapshot/Pack/系统修复入口应用层
  404（08 §28.6，create_app 不挂载，见 test_mount/test_lowlevel_app）；
  `health` 脱敏（不返回 Queue/模型/路径/租户数据）；
- 05 §11.5：`cancel_watch` 需目标 Resource 当前写权限；`add_resource`
  未显式目标强制 User 私有根。

legacy（非平台）模式下各 Tool 行为保持不变（tests/server 既有用例覆盖）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.dependencies import set_service
from openviking.server.mcp_endpoint import (
    _mcp_app_ctx,
    _mcp_principal_ctx,
    add_resource,
    cancel_watch,
    forget,
    glob,
    grep,
    health,
)
from openviking.server.platform.auth.uri_policy import AuthorizationService
from openviking.server.platform.facade import ProductFacadeService
from openviking.server.platform.iam import PostgresIamRepository, RbacService
from openviking.server.platform.lowlevel.bridge import McpPlatformBridge
from openviking.server.platform.lowlevel.guard import LowLevelPolicyGuard
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.registry.service import ContentRegistryService
from openviking.server.platform.target_policy import TargetPolicy
from tests.platform.helpers import build_auth_setup


class _FakeMcpService:
    def __init__(self) -> None:
        self.fs = SimpleNamespace(
            rm=AsyncMock(return_value="rm-ok"),
            read=AsyncMock(return_value="content"),
            grep=AsyncMock(return_value={"matches": []}),
            glob=AsyncMock(return_value={"matches": []}),
        )
        self.resources = SimpleNamespace(
            add_resource=AsyncMock(
                return_value={"root_uri": "viking://user/ov_user_alice/resources/x"}
            )
        )
        self.viking_fs = type("VikingFS", (), {})()


@pytest_asyncio.fixture
async def mcp_env(session_factory: async_sessionmaker[AsyncSession]):
    """产品模式 MCP 环境：bridge + 守卫 + 平台服务（装配约定同 mount.py）。"""
    app = FastAPI(title="ovp-mcp-test")
    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    registry_store = RegistryRepository()
    registry_service = ContentRegistryService(registry_store, ProvisioningRepository())
    authorization = AuthorizationService(
        TargetPolicy(), ProductFacadeService.build_ov_mapper(repo, session_factory)
    )
    guard = LowLevelPolicyGuard(authorization, audit=None)
    app.state.platform_enabled = True
    app.state.platform_mcp_bridge = McpPlatformBridge(
        guard=guard,
        registry=registry_service,
        registry_store=registry_store,
        session_factory=session_factory,
        deletion_purge_days=30,
    )
    fake = _FakeMcpService()
    set_service(fake)
    yield SimpleNamespace(
        app=app,
        repo=repo,
        rbac=rbac,
        guard=guard,
        registry_store=registry_store,
        registry_service=registry_service,
        session_factory=session_factory,
        fake=fake,
    )


def _set_mcp_request(env, principal):
    """模拟中间件：写入 app/principal/ctx contextvar（请求结束后 reset）。

    `_mcp_ctx` 是既有 MCP 请求身份 contextvar（工具内 `_get_ctx()` 使用）。
    """
    from openviking.server.identity import RequestContext, Role
    from openviking.server.mcp_endpoint import _mcp_ctx
    from openviking_cli.session.user_id import UserIdentifier

    ov_ctx = RequestContext(
        user=UserIdentifier(
            principal.actor_ov_account_id or "default",
            principal.actor_ov_user_id or "default",
        ),
        role=Role.USER,
    )
    ta = _mcp_app_ctx.set(env.app)
    tp = _mcp_principal_ctx.set(principal)
    tc = _mcp_ctx.set(ov_ctx)
    return (ta, tp, tc)


def _reset(env, tokens):
    from openviking.server.mcp_endpoint import _mcp_ctx

    vars_order = (_mcp_app_ctx, _mcp_principal_ctx, _mcp_ctx)
    for token, var in zip(tokens, vars_order, strict=False):
        var.reset(token)


async def _principal_of(mcp_env, session, setup, user) -> object:
    """构造与 IAM 实时权限一致的 Principal（与插件解析路径同构）。"""
    from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal

    perms = await mcp_env.rbac.get_user_permissions(session, user.id)
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=setup.acme.id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=setup.acme.ov_account_id,
        user_status="active",
        authentication_method="api_key",
        role_codes=perms.role_codes,
        permissions=perms.permissions,
        ov_base_role=perms.ov_base_role,
        role_rank=perms.rank,
    )


async def _seed_ref(session: AsyncSession, setup, *, ov_uri: str, object_type="resource"):
    """直写一条 active 私有 Resource ref（服务装配的种子路径在 P2-E3 已有）。"""
    from openviking.server.platform.models import PlatformContentRef

    ref = PlatformContentRef(
        id=uuid.uuid4(),
        account_id=setup.acme.id,
        object_type=object_type,
        visibility="user_private",
        owner_user_id=setup.alice.id,
        ov_uri=ov_uri,
        status="active",
    )
    session.add(ref)
    await session.flush()
    return ref


# ── AC③：forget → 30 天软删除 ──


async def test_product_forget_soft_deletes_into_30_day_window(mcp_env, session) -> None:
    """MCP forget 与产品删除一致：进 30 天回收期，不调用底层 rm（AC③）。"""
    setup = await build_auth_setup(session)
    alice = setup.alice
    target_uri = f"viking://user/{alice.ov_user_id}/resources/demo"
    ref = await _seed_ref(session, setup, ov_uri=target_uri)
    await session.commit()
    principal = await _principal_of(mcp_env, session, setup, alice)

    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await forget(uri=target_uri)
    finally:
        _reset(mcp_env, (ta, tp, tc))

    assert "30-day soft delete" in result
    mcp_env.fake.fs.rm.assert_not_awaited()  # 不能调用不可恢复的底层 rm

    from sqlalchemy import select

    from openviking.server.platform.models import IamDeletionJob, PlatformContentRef

    async with mcp_env.session_factory() as s:
        job = (
            await s.execute(select(IamDeletionJob).where(IamDeletionJob.resource_id == str(ref.id)))
        ).scalar_one_or_none()
        refreshed = await s.get(PlatformContentRef, ref.id)
    assert job is not None
    assert job.resource_type == "resource"
    assert job.status == "pending"
    now = datetime.now(timezone.utc)
    assert timedelta(days=29) <= job.purge_after - now <= timedelta(days=31)
    assert refreshed.deleted_at is not None
    assert refreshed.status == "pending_deletion"


async def test_product_forget_denied_without_delete_permission(mcp_env, session) -> None:
    """forget 需要目标 delete Permission：普通 User 删共享区被拒。"""
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await forget(uri="viking://resources/team.md")
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert result.startswith("Error:")
    assert "PERMISSION_NOT_GRANTED" in result
    mcp_env.fake.fs.rm.assert_not_awaited()


async def test_product_forget_unknown_uri_rejected(mcp_env, session) -> None:
    """未登记的产品对象不能 forget（不能绕过 Content Registry 物理删除）。"""
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await forget(
            uri=f"viking://user/{setup.alice.ov_user_id}/resources/never-registered"
        )
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert "no product-managed object found" in result
    mcp_env.fake.fs.rm.assert_not_awaited()


async def test_product_forget_invalid_uri_rejected(mcp_env, session) -> None:
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await forget(uri="http://evil/x")
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert result.startswith("Error:")
    mcp_env.fake.fs.rm.assert_not_awaited()


async def test_product_forget_recursive_soft_deletes_children(mcp_env, session) -> None:
    """recursive=True：根与子树内已登记对象全部进 30 天回收期（批量软删）。"""
    setup = await build_auth_setup(session)
    alice = setup.alice
    root = f"viking://user/{alice.ov_user_id}/resources/dirtree"
    child = f"{root}/child.md"
    await _seed_ref(session, setup, ov_uri=root)
    await _seed_ref(session, setup, ov_uri=child)
    await session.commit()
    principal = await _principal_of(mcp_env, session, setup, alice)

    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await forget(uri=root, recursive=True)
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert "30-day soft delete" in result
    mcp_env.fake.fs.rm.assert_not_awaited()
    async with mcp_env.session_factory() as s:
        rows = list(
            (
                await s.execute(
                    __import__("sqlalchemy").select(
                        __import__(
                            "openviking.server.platform.models", fromlist=["IamDeletionJob"]
                        ).IamDeletionJob
                    )
                )
            ).scalars()
        )
    assert len(rows) == 2


# ── AC③：grep/glob 不向产品凭证发布、health 脱敏 ──


async def test_product_grep_glob_unavailable(mcp_env, session) -> None:
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        assert "not available for product credentials" in await grep(
            uri="viking://user/ov_user_alice", pattern="secret"
        )
        assert "not available for product credentials" in await glob(
            pattern="**/*.md", uri="viking://user/ov_user_alice"
        )
        mcp_env.fake.fs.grep.assert_not_awaited()
        mcp_env.fake.fs.glob.assert_not_awaited()
    finally:
        _reset(mcp_env, (ta, tp, tc))


async def test_product_health_sanitized(mcp_env, session) -> None:
    """health 脱敏：不返回 Queue/模型/存储类型/路径/租户数据（AC③）。"""
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await health()
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert result == "OpenViking is healthy."
    for forbidden in ("VikingFS", "queue", "Queue", "storage:", "viking://", "model"):
        assert forbidden not in result


# ── 05 §11.5：cancel_watch 需目标写权限 ──


async def test_product_cancel_watch_requires_write_permission(mcp_env, session) -> None:
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    # 共享目标：普通 User 无写权限 → 拒绝（取消不执行）
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await cancel_watch(to_uri="viking://resources/team.md")
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert "PERMISSION_NOT_GRANTED" in result


async def test_product_cancel_watch_own_private_passes_guard(mcp_env, session) -> None:
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await cancel_watch(
            to_uri=f"viking://user/{setup.alice.ov_user_id}/resources/own.md"
        )
    finally:
        _reset(mcp_env, (ta, tp, tc))
    # 守卫通过后走既有流程（测试环境无 scheduler → 明确提示，而非权限错误）
    assert "PERMISSION_NOT_GRANTED" not in result
    assert "Error" in result  # Watch scheduler not running（fake 环境无调度器）


# ── 05 §11.5 默认目标规则：add_resource ──


async def test_product_add_resource_forces_private_default_target(mcp_env, session) -> None:
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await add_resource(path="https://1.1.1.1/doc.html")
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert "Resource added" in result
    kwargs = mcp_env.fake.resources.add_resource.call_args.kwargs
    assert kwargs["to"] == f"viking://user/{setup.alice.ov_user_id}/resources/"


async def test_product_add_resource_shared_target_denied_for_plain_user(
    mcp_env, session
) -> None:
    setup = await build_auth_setup(session)
    principal = await _principal_of(mcp_env, session, setup, setup.alice)
    ta, tp, tc = _set_mcp_request(mcp_env, principal)
    try:
        result = await add_resource(
            path="https://1.1.1.1/doc.html", to="viking://resources/team/"
        )
    finally:
        _reset(mcp_env, (ta, tp, tc))
    assert "PERMISSION_NOT_GRANTED" in result
    mcp_env.fake.resources.add_resource.assert_not_awaited()


async def test_legacy_tools_unchanged_when_bridge_absent(mcp_env, session) -> None:
    """非产品模式（无 bridge）：forget/grep/glob/health 保持既有行为。"""
    app_legacy = FastAPI()
    app_legacy.state.platform_enabled = False
    from openviking.server.identity import RequestContext, Role
    from openviking.server.mcp_endpoint import _mcp_ctx
    from openviking_cli.session.user_id import UserIdentifier

    legacy_ctx = RequestContext(
        user=UserIdentifier("default", "default"), role=Role.ROOT
    )
    ta = _mcp_app_ctx.set(app_legacy)
    tp = _mcp_principal_ctx.set(None)
    tc = _mcp_ctx.set(legacy_ctx)
    try:
        result = await health()
        assert "VikingFS" in result  # legacy 健康消息不变
        assert "not available for product credentials" not in await grep(
            uri="viking://user/x", pattern="p"
        )
        await forget(uri="viking://user/x/resources/y.md")
        mcp_env.fake.fs.rm.assert_awaited()
    finally:
        _mcp_app_ctx.reset(ta)
        _mcp_principal_ctx.reset(tp)
        _mcp_ctx.reset(tc)
