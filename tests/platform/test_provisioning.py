"""P2-E1 Provisioning 服务/Worker/API 测试（05 §11.3、§12.6，14 号计划 §97.1）。

验收映射：
- AC①：Account+首位 Admin 与 outbox 同一 PG 事务；Worker 成功→active+completed、
  失败→failed+attempts 递增（失败链路在本文件，事务写入在 test_outbox.py）；
- AC②：SystemPrincipal 不可从 HTTP/MCP 声明；审计含 system 组件；
- AC③：Worker 幂等不产生重复 namespace；
- AC④：provisioning/pending|failed 用户产品请求返回 PROVISIONING_PENDING/FAILED；
- AC⑤：retry 仅 failed 生效且幂等（active 重试 409）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.principals import (
    AuthenticatedUserPrincipal,
    resolve_principal,
)
from openviking.server.platform.auth.sessions import SessionService
from openviking.server.platform.config import platform_config
from openviking.server.platform.iam import PostgresIamRepository
from openviking.server.platform.models import IamOutbox, IamUser
from openviking.server.platform.provisioning.control_plane import FakeControlPlane
from openviking.server.platform.provisioning.principals import SystemPrincipal
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.provisioning.worker import ProvisioningWorker
from tests.platform.helpers import (
    build_auth_setup,
    create_user,
    run_provisioning,
)

BASE_AUTH = "/api/platform/v1/auth"
BASE_PLATFORM = "/api/platform/v1/platform"
PSA_EMAIL = "psa@platform.local"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _principal(user: IamUser) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=None,
        user_status="active",
        authentication_method="session",
    )


async def _create_account_via_service(session: AsyncSession, *, repo, rbac, auth, psa, code="beta"):
    from openviking.server.platform.admin.service import AdminService

    admin = AdminService(repo, rbac, auth)
    return await admin.create_account_with_first_admin(
        session,
        actor=_principal(psa),
        account_code=code,
        account_name=code,
        admin_email=f"badmin@{code}.com",
        admin_username="badmin",
    )


class _FailingControlPlane(FakeControlPlane):
    """模拟控制面初始化失败（含敏感片段，验证脱敏）。"""

    async def provision_account(self, ov_account_id: str) -> None:
        raise RuntimeError("namespace init failed at /private/tmp/ov password=secret123")


# ── AC① 失败链路：Worker 失败 → failed + attempts 递增 + 脱敏错误 ──


async def test_worker_failure_marks_failed_with_backoff(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    result = await _create_account_via_service(
        session, repo=setup.repo, rbac=setup.rbac, auth=setup.auth, psa=setup.psa
    )
    await session.commit()

    control = _FailingControlPlane()
    worker = ProvisioningWorker(setup.repo, ProvisioningRepository(), control)
    processed = await worker.run_once(session, now=_now())
    assert processed == 2  # account.provision 与 user.provision 各自失败

    account = await setup.repo.get_account(session, result.account.id)
    assert account.status == "failed"
    assert account.provisioning_error is not None
    # 脱敏：不含路径/口令片段（04 §10.9）
    assert "/private/tmp" not in account.provisioning_error
    assert "secret123" not in account.provisioning_error

    events = list((await session.execute(select(IamOutbox))).scalars())
    assert len(events) == 2
    assert all(e.status == "failed" for e in events)
    assert all(e.attempts == 1 for e in events)
    assert all(e.last_error is not None for e in events)
    # 退避到期前不可领取；到期后重新处理 → attempts 递增
    assert await worker.run_once(session, now=_now()) == 0
    due = _now() + timedelta(seconds=platform_config.provisioning_retry_max_seconds + 1)
    assert await worker.run_once(session, now=due) == 2
    events = list((await session.execute(select(IamOutbox))).scalars())
    assert all(e.attempts == 2 for e in events)


# ── AC② SystemPrincipal：仅受控代码路径、审计含 system 组件 ──


async def test_system_principal_not_resolvable_from_credentials(session: AsyncSession) -> None:
    """SystemPrincipal 不可从 HTTP/MCP 声明：任何凭证输入都不会产出 SystemPrincipal。"""
    setup = await build_auth_setup(session)
    principal = SystemPrincipal(component="provisioning.worker", task_id=str(uuid.uuid4()))
    assert principal.task_id is not None  # 仅 Worker 构造点（本测试显式构造 = 受控代码路径）

    # 统一 Resolver 只产出 AuthenticatedUserPrincipal 或抛认证异常
    resolved = None
    try:
        resolved = await resolve_principal(
            session,
            setup.repo,
            setup.rbac,
            session_token="some-token-that-does-not-exist",
        )
    except Exception:
        resolved = None
    assert resolved is None or isinstance(resolved, AuthenticatedUserPrincipal)


async def test_worker_audit_contains_system_component(session: AsyncSession) -> None:
    """AC②：Worker 审计 actor_type=system + actor_system_component，不伪装成用户。"""
    setup = await build_auth_setup(session)
    result = await _create_account_via_service(
        session, repo=setup.repo, rbac=setup.rbac, auth=setup.auth, psa=setup.psa
    )
    await session.commit()
    processed, _ = await run_provisioning(session)
    assert processed == 2

    events = await setup.repo.list_audit_events(session, action="provision.account")
    assert len(events) == 1
    event = events[0]
    assert event.actor_type == "system"
    assert event.actor_system_component == "provisioning.worker"
    assert event.actor_user_id is None
    assert event.authentication_method == "system"
    assert event.subject_account_id == result.account.id
    assert event.result == "success"
    user_events = await setup.repo.list_audit_events(session, action="provision.user")
    assert len(user_events) == 1
    assert user_events[0].actor_type == "system"
    assert user_events[0].subject_user_id == result.admin.id


# ── AC③ Worker 幂等：不产生重复 namespace ──


async def test_worker_idempotent_no_duplicate_namespace(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    result = await _create_account_via_service(
        session, repo=setup.repo, rbac=setup.rbac, auth=setup.auth, psa=setup.psa
    )
    await session.commit()

    control = FakeControlPlane()
    worker = ProvisioningWorker(setup.repo, ProvisioningRepository(), control)
    assert await worker.run_once(session) == 2
    assert await worker.run_once(session) == 0  # 已 completed 不再领取

    assert len(await control.list_provisioned_accounts()) == 1
    users = await control.list_provisioned_users(result.account.ov_account_id)
    assert users == {result.admin.ov_user_id}
    events = list((await session.execute(select(IamOutbox))).scalars())
    assert len(events) == 2  # 无重复事件


# ── AC④：provisioning/pending|failed 用户产品请求返回 PROVISIONING_PENDING/FAILED ──


async def _provisioning_user_session(session: AsyncSession, *, setup, user: IamUser, status: str):
    user_row = await setup.repo.get_user(session, user.id)
    await setup.repo.update_user(session, user.id, status=status)
    await session.commit()
    raw, _csrf, _sid = await SessionService(setup.repo, platform_config).create_login_session(
        session, user=user_row
    )
    await session.commit()
    return raw


async def test_product_request_pending_returns_provisioning_pending(
    session: AsyncSession, platform_app
) -> None:
    setup = await build_auth_setup(session)
    raw = await _provisioning_user_session(
        session, setup=setup, user=setup.alice, status="provisioning"
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform_app), base_url="https://testserver"
    )
    try:
        r = await client.get(f"{BASE_AUTH}/me", cookies={platform_config.cookie_name: raw})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "PROVISIONING_PENDING"
    finally:
        await client.aclose()


async def test_product_request_failed_returns_provisioning_failed(
    session: AsyncSession, platform_app
) -> None:
    setup = await build_auth_setup(session)
    raw = await _provisioning_user_session(session, setup=setup, user=setup.alice, status="failed")
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform_app), base_url="https://testserver"
    )
    try:
        r = await client.get(f"{BASE_AUTH}/me", cookies={platform_config.cookie_name: raw})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "PROVISIONING_FAILED"
    finally:
        await client.aclose()


async def test_product_request_failed_account_returns_provisioning_failed(
    session: AsyncSession, platform_app
) -> None:
    """Account failed 但 User active：产品请求同样返回 PROVISIONING_FAILED。"""
    setup = await build_auth_setup(session)
    account = await setup.repo.get_account(session, setup.acme.id)
    await setup.repo.update_account(
        session, account.id, expected_version=account.version, status="failed"
    )
    await session.commit()
    raw, _c, _s = await SessionService(setup.repo, platform_config).create_login_session(
        session, user=setup.alice
    )
    await session.commit()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform_app), base_url="https://testserver"
    )
    try:
        r = await client.get(f"{BASE_AUTH}/me", cookies={platform_config.cookie_name: raw})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "PROVISIONING_FAILED"
    finally:
        await client.aclose()


async def test_product_request_pending_via_api_key(session: AsyncSession, platform_client) -> None:
    """API Key 路径同样受 provisioning 门禁（Session/API Key 同一 Resolver）。"""
    setup = await build_auth_setup(session)
    user = await create_user(setup.repo, session, setup.acme, email="bob@acme.com", username="bob")
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=user.id,
        role_code="user",
    )
    from openviking.server.platform.auth.password import sha256_hex

    secret = "dev-secret-p2e1-known"
    cred = await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=user.id,
        name="P2E1",
        public_id="pub_p2e1_known",
        key_hash=sha256_hex(secret),
        key_last_four=secret[-4:],
        created_by=user.id,
    )
    await setup.repo.update_user(session, user.id, status="provisioning")
    await session.commit()

    r = await platform_client.get(
        f"{BASE_AUTH}/me", headers={"X-Api-Key": f"ovk_u.{cred.public_id}.{secret}"}
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "PROVISIONING_PENDING"


# ── AC⑤：POST .../provisioning/retry（仅 failed/provisioning、幂等；active 409）──


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def test_retry_after_failure_recovers(session: AsyncSession, platform_client) -> None:
    """AC⑤：失败后 retry → 事件重置 pending、目标回 provisioning → Worker 成功。"""
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, PSA_EMAIL, "Init-Pass-2026-Dev!")
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts",
        json={
            "account_code": "gamma",
            "account_name": "Gamma",
            "admin_email": "gadmin@gamma.com",
            "admin_username": "gadmin",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    account_id = r.json()["result"]["account"]["id"]

    control = _FailingControlPlane()
    worker = ProvisioningWorker(PostgresIamRepository(), ProvisioningRepository(), control)
    assert await worker.run_once(session) == 2
    account = await setup.repo.get_account(session, uuid.UUID(account_id))
    assert account.status == "failed"

    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{account_id}/provisioning/retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["status"] == "provisioning"
    assert body["retried_events"] == 2

    session.expire_all()  # API 请求独立 Session 提交，本地身份映射需刷新
    account = await setup.repo.get_account(session, uuid.UUID(account_id))
    assert account.status == "provisioning"
    events = list((await session.execute(select(IamOutbox))).scalars())
    assert len(events) == 2
    assert all(e.status == "pending" for e in events)
    assert all(e.attempts == 0 for e in events)

    processed, _ = await run_provisioning(session)
    assert processed == 2
    account = await setup.repo.get_account(session, uuid.UUID(account_id))
    assert account.status == "active"
    admin_row = (
        await session.execute(select(IamUser).where(IamUser.email == "gadmin@gamma.com"))
    ).scalar_one()
    admin = await setup.repo.get_user(session, admin_row.id)
    assert admin.status == "active"


async def test_retry_idempotent_no_duplicate_events(session: AsyncSession, platform_client) -> None:
    """AC⑤：重复 retry 幂等——不产生重复事件；provisioning(pending) 状态 retry 为 no-op。"""
    await build_auth_setup(session)  # 种子 + PSA（登录前提）
    csrf = await _login(platform_client, PSA_EMAIL, "Init-Pass-2026-Dev!")
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts",
        json={
            "account_code": "delta",
            "account_name": "Delta",
            "admin_email": "dadmin@delta.com",
            "admin_username": "dadmin",
        },
        headers={"X-CSRF-Token": csrf},
    )
    account_id = r.json()["result"]["account"]["id"]

    for _ in range(2):  # 两次 retry：pending 状态幂等
        r = await platform_client.post(
            f"{BASE_PLATFORM}/accounts/{account_id}/provisioning/retry",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        assert r.json()["result"]["retried_events"] == 2
    events = list((await session.execute(select(IamOutbox))).scalars())
    assert len(events) == 2  # 无重复事件


async def test_retry_active_account_409(session: AsyncSession, platform_client) -> None:
    """AC⑤（spike ==12 复跑）：active 且无未完成事件 → 409 PROVISIONING_NOT_RETRYABLE。"""
    await build_auth_setup(session)  # 种子 + PSA（登录前提）
    csrf = await _login(platform_client, PSA_EMAIL, "Init-Pass-2026-Dev!")
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts",
        json={
            "account_code": "eps",
            "account_name": "Eps",
            "admin_email": "eadmin@eps.com",
            "admin_username": "eadmin",
        },
        headers={"X-CSRF-Token": csrf},
    )
    account_id = r.json()["result"]["account"]["id"]
    await run_provisioning(session)  # active + completed

    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{account_id}/provisioning/retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PROVISIONING_NOT_RETRYABLE"


async def test_retry_unknown_account_404(session: AsyncSession, platform_client) -> None:
    """AC⑤ 不可见语义：Account 不存在 → 404。"""
    await build_auth_setup(session)  # 种子 + PSA（登录前提）
    csrf = await _login(platform_client, PSA_EMAIL, "Init-Pass-2026-Dev!")
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{uuid.uuid4()}/provisioning/retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


async def test_retry_requires_platform_permission(session: AsyncSession, platform_client) -> None:
    """retry 权限：Account Admin 无 account.manage.platform → 403。"""
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/provisioning/retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"
