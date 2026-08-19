"""P5-E2（14 号计划 §99.2）：17.3 健康检查扩展与 06 §16.3 阈值测试。

验收映射（AC④）：
- `/health`、`/ready` 在 PG 故障 / migration 落后 / backlog 超阈值 / Purge
  停滞时非 ready 且含可读诊断；
- 阈值可精确复测：backlog > 100（默认）、migration 落后 >= 1 版本、Purge
  停滞（待清理 > max_pending 或最早 purge_after 落后 > max_stall_days 或
  Purge Worker 未运行）——全部经 PlatformConfig 可配置并可精确断言；
- 非平台模式既有 /health、/ready 行为零变化（回归由 tests/server 覆盖）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.platform.config import PlatformConfig
from openviking.server.platform.health import PlatformHealthChecks, head_migration_revision
from openviking.server.platform.models import IamDeletionJob, IamOutbox

NOW = datetime.now(timezone.utc)


def _make_config(**overrides) -> PlatformConfig:
    return PlatformConfig(**overrides)


@pytest.fixture()
def checks(
    session_factory: async_sessionmaker[AsyncSession],
) -> PlatformHealthChecks:
    return PlatformHealthChecks(session_factory, _make_config())


async def _enqueue_outbox(
    session: AsyncSession,
    *,
    count: int,
    status: str = "pending",
) -> None:
    for _ in range(count):
        session.add(
            IamOutbox(
                event_type="account.provision",
                aggregate_id=uuid.uuid4(),
                payload={},
                status=status,
                attempts=0,
                next_attempt_at=NOW,
            )
        )
    await session.commit()


async def _insert_deletion_job(
    session: AsyncSession,
    *,
    account_id,
    purge_after: datetime,
    status: str = "pending",
) -> IamDeletionJob:
    job = IamDeletionJob(
        account_id=account_id,
        resource_type="user",
        resource_id=str(uuid.uuid4()),
        deleted_by=None,
        deleted_at=NOW,
        purge_after=purge_after,
        status=status,
    )
    session.add(job)
    await session.commit()
    return job


async def _acme_account_id(session: AsyncSession):
    from tests.platform.helpers import build_auth_setup

    setup = await build_auth_setup(session)
    return setup.acme.id


# ── 基础：migration 版本解析 ──


async def test_head_migration_revision_resolves_current_head(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    """head 解析 = 测试库迁移到的版本（e5f6a7b8c9d2）。"""
    from openviking.server.platform.health import _current_migration_revision

    current = await _current_migration_revision(session)
    assert current == "e5f6a7b8c9d2"
    assert head_migration_revision() == current


# ── 17.3：PG 连通 / migration 版本 ──


async def test_pg_connectivity_ok(checks: PlatformHealthChecks) -> None:
    result = await checks.pg_connectivity()
    assert result["status"] == "ok"


async def test_pg_connectivity_failure_not_ready() -> None:
    from openviking.server.platform.db import build_engine, build_session_factory

    engine = build_engine(
        "postgresql+asyncpg://ov_platform:wrong@127.0.0.1:1/ov_platform_test"
    )
    bad_factory = build_session_factory(engine)
    try:
        checks = PlatformHealthChecks(bad_factory, _make_config())
        result = await checks.pg_connectivity()
        assert result["status"] == "error"
        assert "unreachable" in result["detail"]
    finally:
        await engine.dispose()


async def test_migration_ok_when_current_matches_head(
    checks: PlatformHealthChecks,
) -> None:
    result = await checks.migration()
    assert result["status"] == "ok"
    assert result["detail"]["lag_versions"] == 0


async def test_migration_lag_not_ready(
    session_factory: async_sessionmaker[AsyncSession],
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    """AC④：migration 落后 >= 1 版本 → 非 ready（可精确复测）。"""
    from sqlalchemy import text

    # 直接更新 alembic_version 模拟"落后一版"（测试库属本会话独占，随后恢复）
    await session.execute(
        text("UPDATE alembic_version SET version_num = 'e5f6a7b8c9d0'")
    )
    await session.commit()
    try:
        result = await checks.migration()
        assert result["status"] == "error"
        assert result["detail"]["current"] == "e5f6a7b8c9d0"
        assert result["detail"]["head"] == "e5f6a7b8c9d2"
        assert result["detail"]["lag_versions"] == 1
        assert "lag" in result["threshold"]
    finally:
        await session.execute(
            text("UPDATE alembic_version SET version_num = 'e5f6a7b8c9d2'")
        )
        await session.commit()


# ── 17.3：Provisioning backlog 阈值 ──


async def test_provisioning_backlog_within_threshold(
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    await _enqueue_outbox(session, count=100)  # == 阈值 → ok
    result = await checks.provisioning()
    assert result["status"] == "ok"
    assert result["detail"]["backlog"] == 100


async def test_provisioning_backlog_over_threshold_not_ready(
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    """AC④：backlog > 100（默认阈值）→ 非 ready；阈值可精确复测。"""
    await _enqueue_outbox(session, count=101)
    result = await checks.provisioning()
    assert result["status"] == "error"
    assert result["detail"]["backlog"] == 101
    assert "backlog > 100" in result["threshold"]

    # 阈值可配置：120 条在自定义阈值 150 下 ok
    custom = PlatformHealthChecks(
        checks._session_factory, _make_config(provisioning_backlog_threshold=150)
    )
    result_custom = await custom.provisioning()
    assert result_custom["status"] == "ok"


async def test_provisioning_failed_count_reported(
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    await _enqueue_outbox(session, count=5, status="failed")
    result = await checks.provisioning()
    assert result["status"] == "ok"  # 5 条在阈值内
    assert result["detail"]["failed"] == 5


# ── 17.3：Session cleanup worker 状态 ──


async def test_session_cleanup_reports_remaining_and_worker(
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    result = await checks.session_cleanup()
    assert result["status"] == "ok"
    assert "remaining_sessions" in result["detail"]
    assert result["detail"]["worker"] in ("not_configured", "idle", "running")


async def test_session_cleanup_reflects_worker_last_result(
    checks: PlatformHealthChecks,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from openviking.server.platform.auth.worker import SessionCleanupWorker

    worker = SessionCleanupWorker(config=_make_config())
    worker.last_result = SimpleNamespace(removed=0, remaining=3, ran_at=NOW)
    checks.session_cleanup_worker = worker
    result = await checks.session_cleanup()
    assert result["status"] == "ok"
    assert result["detail"]["last_run_at"] is not None


# ── 17.3：Purge 停滞判定 ──


async def test_purge_ok_when_nothing_pending(
    checks: PlatformHealthChecks,
) -> None:
    result = await checks.purge()
    assert result["status"] == "ok"


async def test_purge_pending_without_worker_not_ready(
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    """AC④：有待清理但 Purge Worker 未运行 → 非 ready（06 §16.3）。"""
    account_id = await _acme_account_id(session)
    await _insert_deletion_job(
        session, account_id=account_id, purge_after=NOW + timedelta(days=10)
    )
    result = await checks.purge()
    assert result["status"] == "error"
    assert "purge worker not running" in result["detail"]["reasons"]


async def test_purge_pending_over_max_not_ready(
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    """AC④：待清理数量 > max_pending（默认 500）→ 非 ready。"""
    account_id = await _acme_account_id(session)
    checks.purge_worker = SimpleNamespace(running=True)
    for _ in range(501):
        session.add(
            IamDeletionJob(
                account_id=account_id,
                resource_type="user",
                resource_id=str(uuid.uuid4()),
                deleted_by=None,
                deleted_at=NOW,
                purge_after=NOW + timedelta(days=10),
                status="pending",
            )
        )
    await session.commit()
    result = await checks.purge()
    assert result["status"] == "error"
    assert any("pending 501" in r for r in result["detail"]["reasons"])

    # 阈值可配置：501 在自定义 max_pending=1000 下 ok
    custom = PlatformHealthChecks(
        checks._session_factory, _make_config(purge_stall_max_pending=1000)
    )
    custom.purge_worker = SimpleNamespace(running=True)
    result_custom = await custom.purge()
    assert result_custom["status"] == "ok"


async def test_purge_stall_by_earliest_purge_after(
    checks: PlatformHealthChecks,
    session: AsyncSession,
) -> None:
    """AC④：最早 purge_after 落后 > max_stall_days（默认 3）天 → 非 ready。"""
    account_id = await _acme_account_id(session)
    checks.purge_worker = SimpleNamespace(running=True)
    await _insert_deletion_job(
        session, account_id=account_id, purge_after=NOW - timedelta(days=5)
    )
    result = await checks.purge()
    assert result["status"] == "error"
    assert any("stalled" in r for r in result["detail"]["reasons"])

    # 阈值可配置：5 天在自定义 max_stall_days=7 下 ok
    custom = PlatformHealthChecks(
        checks._session_factory, _make_config(purge_stall_max_stall_days=7)
    )
    custom.purge_worker = SimpleNamespace(running=True)
    result_custom = await custom.purge()
    assert result_custom["status"] == "ok"


# ── `/health`、`/ready` HTTP 集成（system.py 消费 platform_health_checks）──


async def _health_app(session_factory: async_sessionmaker[AsyncSession], config=None):
    from fastapi import FastAPI

    from openviking.server.routers.system import router as system_router

    app = FastAPI(title="ovp-health-test")
    app.include_router(system_router)
    app.state.api_key_manager = None
    app.state.platform_health_checks = PlatformHealthChecks(
        session_factory, config or _make_config()
    )
    return app


def _ready_service_env(monkeypatch: pytest.MonkeyPatch):
    """/ready 前置：服务已初始化 + 基础设施探测 stub（本环境无本地 embedder）。"""
    monkeypatch.setattr(
        "openviking.server.dependencies._service",
        SimpleNamespace(_initialized=True),
    )
    monkeypatch.setattr(
        "openviking.server.routers.system.get_viking_fs",
        lambda: SimpleNamespace(
            ls=async_ls,
            system_sync_status=async_sync_status,
            _get_vector_store=lambda: None,
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config",
        lambda config: (False, None, None),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.OpenVikingConfigSingleton.get_instance",
        lambda: SimpleNamespace(
            embedding=SimpleNamespace(get_embedder=lambda: None),
        ),
    )
    return monkeypatch


async def async_ls(path, ctx=None):
    return []


async def async_sync_status(uri, ctx=None):
    return {"path": uri, "entry_count": 0}


async def test_ready_platform_ok_when_checks_pass(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC④ 正向：全部检查 ok → /ready 200 ready，含 platform 明细。"""
    _ready_service_env(monkeypatch)
    import httpx

    app = await _health_app(session_factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    platform = body["checks"]["platform"]
    assert platform["status"] == "ok"
    assert platform["checks"]["postgresql"]["status"] == "ok"
    assert platform["checks"]["migration"]["status"] == "ok"


async def test_ready_not_ready_when_pg_down(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC④：PG 故障 → /ready 503 + 可读诊断。"""
    _ready_service_env(monkeypatch)
    from openviking.server.platform.db import build_engine
    from openviking.server.platform.db import build_session_factory as _bsf

    engine = build_engine("postgresql+asyncpg://ov_platform:wrong@127.0.0.1:1/x")
    bad_factory = _bsf(engine)
    try:
        import httpx

        app = await _health_app(bad_factory)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        platform = body["checks"]["platform"]
        assert platform["status"] == "error"
        assert "unreachable" in platform["checks"]["postgresql"]["detail"]
    finally:
        await engine.dispose()


async def test_ready_not_ready_when_backlog_over_threshold(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC④：backlog 超阈值 → /ready 503 + provisioning 诊断。"""
    _ready_service_env(monkeypatch)
    await _enqueue_outbox(session, count=101)

    import httpx

    app = await _health_app(session_factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    platform = body["checks"]["platform"]
    assert platform["status"] == "error"
    assert platform["checks"]["provisioning"]["detail"]["backlog"] == 101


async def test_health_non_ready_in_platform_mode(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC④：/health 在平台检查失败时 503 且含可读诊断（06 §17.3）。"""
    await _enqueue_outbox(session, count=101)

    import httpx

    app = await _health_app(session_factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["healthy"] is False
    platform = body["checks"]["platform"]
    assert platform["status"] == "error"
    assert platform["checks"]["provisioning"]["status"] == "error"
    assert platform["checks"]["provisioning"]["detail"]["backlog"] == 101


async def test_health_ready_in_platform_mode(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/health 平台检查全 ok → 200 ready。"""
    import httpx

    app = await _health_app(session_factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["healthy"] is True
    assert body["checks"]["platform"]["status"] == "ok"


async def test_health_without_platform_checks_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """非平台模式：无 platform_health_checks 时 /health 行为零变化。"""
    from fastapi import FastAPI

    from openviking.server.routers.system import router as system_router

    app = FastAPI(title="legacy-health")
    app.include_router(system_router)
    app.state.api_key_manager = None

    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "checks" not in body
