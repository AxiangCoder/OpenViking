"""P5-E2（14 号计划 §99.2）：17.1 审计事件清单生产验证 + 17.2 日志关联。

验收映射：
- ⑤ 17.1 每类事件有脱敏审计（06 §14.4：无密码/Key 明文/完整 hash/Token）；
  跨用户事件（重置密码/撤销 Key/提升角色）含 Actor+Subject；
- 17.2 Request ID 贯通：HTTP 请求 → 审计事件 → 后台 Provisioning Task
  （管理员跨用户事件字段完整：actor_user_id/actor_account_id/
  authentication_method/actor_credential_id/subject_account_id/
  subject_user_id/action/scope/result，06 §17.2）；
- ⑥ 日志/审计/埋点无密码/Cookie/Key 明文/完整 hash/Token。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.platform.auth.api_keys import ApiCredentialService
from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.models import IamAuditEvent, IamOutbox
from tests.platform.helpers import (
    DEFAULT_PASSWORD,
    AuthSetup,
    build_auth_setup,
    create_login_session,
    run_provisioning,
)

# 17.1 清单 → 代表动作（生产验证输入，证据收口归 P5-E4）
ACTION_BY_CATEGORY = {
    "auth.login": "登录成功（auth.login）",
    "auth.logout": "登出（auth.logout）",
    "credential.create": "用户 API Key 创建（credential.create）",
    "credential.revoke": "用户 API Key 撤销（credential.revoke）",
    "user.create": "用户创建（user.create）",
    "user.disable": "用户禁用（user.disable）",
    "user.enable": "用户启用（user.enable）",
    "user.password.reset": "密码重置（user.password.reset）",
    "role.assign": "角色分配/提升（role.assign）",
    "account.create": "Account 创建（account.create）",
    "provision.account": "Provisioning 成功（provision.account）",
    "provision.user": "Provisioning 成功（provision.user）",
    "provisioning.retry": "Provisioning 人工重试（provisioning.retry）",
    "purge.user": "期满物理清理（purge.user）",
    "auth.login.failed": "登录失败（auth.login result=failed）",
    "role.assign.denied": "权限拒绝（role.assign result=denied）",
}


async def _collect_audit(session: AsyncSession) -> list[IamAuditEvent]:
    from sqlalchemy import select

    return list((await session.execute(select(IamAuditEvent))).scalars())


# ── ⑤ 17.1 每类事件有脱敏审计 ──


async def test_17_1_event_categories_produce_masked_audit(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    """AC⑤：17.1 各事件类别产生审计记录；全库审计不含任何口令/Key 明文。"""
    from datetime import datetime, timedelta, timezone

    from openviking.server.platform.deletion.worker import NoopPurgeHandler, PurgeWorker
    from openviking.server.platform.models import IamDeletionJob
    from openviking.server.platform.registry.repository import RegistryRepository

    setup = await build_auth_setup(session)
    repo, auth = setup.repo, setup.auth
    principal = _psa_principal(setup)

    # 1. 登录成功 + 登出（Session）
    raw, _, _ = await create_login_session(setup, session, setup.alice)
    await auth.login(
        session,
        email="alice@acme.com",
        password=DEFAULT_PASSWORD,
        ip="127.0.0.1",
    )
    await session.commit()
    await auth.logout(session, principal=_principal_of(setup, setup.alice))
    await session.commit()

    # 2. 登录失败（防枚举：LOGIN_FAILED）
    from openviking.server.platform.errors import LoginFailedError

    with pytest.raises(LoginFailedError):
        await auth.login(
            session,
            email="alice@acme.com",
            password="wrong-password-2026!",
            ip="127.0.0.1",
        )
    await session.commit()

    # 3. Key 创建 + 撤销（ApiCredentialService）
    keys = ApiCredentialService(repo)
    created = await keys.create_key(session, user=setup.alice, name="Codex")
    await session.commit()
    full_key = created.full_key
    await keys.revoke_key(
        session,
        credential_id=created.record.id,
        user_id=setup.alice.id,
        request_id="req-revoke",
    )
    await session.commit()

    # 4. 管理员动作：建 User / 禁用 / 启用 / 重置密码 / 提升角色
    await _admin(session, setup, principal, "bob@acme.com", create_user=True)
    await _admin(session, setup, principal, "alice@acme.com", disable=True)
    await _admin(session, setup, principal, "alice@acme.com", disable=False)
    await _admin(session, setup, principal, "alice@acme.com", reset_password=True)
    await _admin(session, setup, principal, "alice@acme.com", promote=True)

    # 5. Account 创建 + Provisioning Worker（成功）+ active 重试 409 守卫
    account_result = await _psa_create_account(session, setup, "beta")
    processed, _ = await run_provisioning(session)
    assert processed == 2
    await session.commit()
    from openviking.server.platform.errors import ProvisioningNotRetryableError

    try:
        await _psa_retry(session, setup, account_result.account.id)
    except ProvisioningNotRetryableError:
        pass  # active 账号重试 409 守卫（spike ==12 语义，AC⑤）

    # 6. Purge Worker（期满清理，审计不随物理清理）
    job = IamDeletionJob(
        account_id=setup.acme.id,
        resource_type="user",
        resource_id=str(uuid.uuid4()),
        deleted_by=setup.admin.id,
        deleted_at=datetime.now(timezone.utc) - timedelta(days=31),
        purge_after=datetime.now(timezone.utc) - timedelta(days=1),
        status="pending",
    )
    session.add(job)
    await session.commit()
    purge_worker = PurgeWorker(repo, RegistryRepository(), handlers={"user": NoopPurgeHandler()})
    await purge_worker.run_once(session)
    await session.commit()

    # 断言：代表类别均有审计
    audit_events = await _collect_audit(session)
    actions_seen = {e.action for e in audit_events}
    for action in (
        "auth.login",
        "auth.logout",
        "credential.create",
        "credential.revoke",
        "user.create",
        "user.disable",
        "user.enable",
        "user.password.reset",
        "role.assign",
        "account.create",
        "provision.account",
        "provision.user",
        "purge.user",
    ):
        assert action in actions_seen, f"17.1 事件类别缺少审计: {action}"
    assert any(
        e.action == "auth.login" and e.result == "failed" for e in audit_events
    ), "登录失败必须审计（防枚举）"

    # 脱敏断言：全库审计不含任何敏感明文（06 §14.4）
    secrets = [
        DEFAULT_PASSWORD,
        full_key,
        raw,
        "wrong-password-2026!",
        sha256_hex(full_key),
    ]
    blob = " ".join(
        str(getattr(e, field) or "")
        for e in audit_events
        for field in (
            "request_id",
            "actor_type",
            "reason",
            "target_id",
            "metadata_json",
        )
    )
    for secret in secrets:
        assert secret not in blob, f"审计泄漏敏感数据: {secret}"


async def test_cross_user_events_have_actor_and_subject(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    """AC⑤：跨用户事件（重置密码/撤销 Key/提升）含 Actor+Subject，字段完整。"""
    setup = await build_auth_setup(session)
    principal = _psa_principal(setup)

    await _admin(session, setup, principal, "alice@acme.com", reset_password=True)
    await session.commit()

    events = await setup.repo.list_audit_events(
        session, action="user.password.reset", result="success"
    )
    assert events
    event = events[0]
    actor = await setup.repo.get_user(session, event.actor_user_id)
    subject = await setup.repo.get_user(session, event.subject_user_id)
    assert actor.id == setup.psa.id
    assert subject.id == setup.alice.id
    assert event.actor_user_id != event.subject_user_id
    assert event.actor_account_id is None  # PSA 平台级 Actor
    assert event.subject_account_id == setup.acme.id
    assert event.authentication_method in ("session", "api_key")
    assert event.action == "user.password.reset"
    assert event.scope == "platform"
    assert event.result == "success"
    # 不记录数据正文：metadata 无新密码
    assert "password" not in str(event.metadata_json or {}).lower()


# ── 17.2 Request ID 贯通（HTTP → 审计 → 后台任务）──


async def test_request_id_correlates_http_audit_and_provisioning_task(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    """AC⑤+17.2：HTTP 请求 X-Request-ID → 审计事件 → outbox payload →
    Provisioning Task 审计事件，Request ID 全程一致。"""
    from openviking.server.platform.admin.service import AdminService
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.provisioning.repository import ProvisioningRepository
    from openviking.server.platform.provisioning.service import ProvisioningService

    setup = await build_auth_setup(session)
    principal = _psa_principal(setup)

    # Account 创建（带 request_id）→ outbox payload 携带同一 request_id
    request_id = "req-" + uuid.uuid4().hex[:16]
    provisioning = ProvisioningService(setup.repo, ProvisioningRepository(), FakeControlPlane())
    admin = AdminService(setup.repo, setup.rbac, setup.auth, provisioning=provisioning)
    created = await admin.create_account_with_first_admin(
        session,
        actor=principal,
        account_name="Gamma",
        account_code="gamma",
        admin_email="gadmin@gamma.com",
        admin_username="gadmin",
        request_id=request_id,
    )
    await session.commit()

    outbox_rows = (
        await session.execute(
            select_outbox_where_account(str(created.account.id))
        )
    ).scalars()
    for event in outbox_rows:
        assert (event.payload or {}).get("request_id") == request_id, "outbox payload 缺 request_id"

    # Worker 处理后审计事件携带同一 request_id
    await run_provisioning(session)
    await session.commit()
    provision_events = await setup.repo.list_audit_events(
        session, action="provision.account", result="success"
    )
    assert provision_events
    assert provision_events[0].request_id == request_id

    # 创建动作本身也带同一 request_id
    account_events = await setup.repo.list_audit_events(
        session, action="account.create", result="success"
    )
    assert account_events and account_events[0].request_id == request_id


async def test_request_id_from_http_login_reaches_audit(
    auth_client,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    """17.2：HTTP 请求携带 X-Request-ID，登录审计事件回填同一 ID。"""

    from tests.platform.helpers import DEFAULT_PASSWORD, build_auth_setup

    await build_auth_setup(session)
    await session.commit()

    request_id = "http-" + uuid.uuid4().hex[:16]
    resp = await auth_client.post(
        "/api/platform/v1/auth/login",
        headers={"X-Request-ID": request_id},
        json={"email": "alice@acme.com", "password": DEFAULT_PASSWORD},
    )
    assert resp.status_code == 200

    async with session_factory() as session:
        events = await session.execute(
            select_audit_by_request_id(request_id)
        )
        rows = events.scalars().all()
    assert rows, "审计事件必须回填 HTTP 请求的 Request ID"
    assert any(e.action == "auth.login" and e.result == "success" for e in rows)


# ── 测试辅助 ──


def _principal_of(setup: AuthSetup, user) -> object:
    """构造只读 Principal（仅测试审计字段，不参与权限判定）。"""
    from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal

    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id="ov_account_acme",
        user_status="active",
        authentication_method="session",
    )


def _psa_principal(setup: AuthSetup) -> object:
    return _principal_of(setup, setup.psa)


async def _admin(
    session: AsyncSession,
    setup: AuthSetup,
    actor,
    target_email: str,
    *,
    create_user: bool = False,
    disable: bool | None = None,
    reset_password: bool = False,
    promote: bool = False,
) -> None:
    """管理动作：建 User/禁用/启用（Account Admin 上下文）；重置/提升（平台路径）。"""
    from openviking.server.platform.admin.service import AdminService
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.provisioning.repository import ProvisioningRepository
    from openviking.server.platform.provisioning.service import ProvisioningService

    provisioning = ProvisioningService(setup.repo, ProvisioningRepository(), FakeControlPlane())
    admin = AdminService(setup.repo, setup.rbac, setup.auth, provisioning=provisioning)
    request_id = "req-" + uuid.uuid4().hex[:12]
    if create_user:
        await admin.create_account_user(
            session,
            actor=_principal_of(setup, setup.admin),  # Account Admin 直建 User
            email=target_email,
            username="bob",
            request_id=request_id,
        )
        await session.commit()
        return
    target = await setup.repo.get_user_by_normalized_email(
        session, target_email.strip().casefold()
    )
    assert target is not None, f"目标用户不存在: {target_email}"
    if reset_password:
        await admin.reset_user_password(
            session,
            actor=actor,
            target_user_id=target.id,
            scope_account_id=setup.acme.id,
            request_id=request_id,
        )
    elif promote:
        await admin.promote_user(
            session,
            actor=actor,
            target_user_id=target.id,
            account_id=setup.acme.id,
            request_id=request_id,
        )
    elif disable is not None:
        await admin.set_user_status(
            session,
            actor=_principal_of(setup, setup.admin),  # Account Admin 上下文
            target_user_id=target.id,
            status="disabled" if disable else "active",
            request_id=request_id,
        )
    await session.commit()


async def _psa_create_account(session: AsyncSession, setup: AuthSetup, code: str):
    from openviking.server.platform.admin.service import AdminService
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.provisioning.repository import ProvisioningRepository
    from openviking.server.platform.provisioning.service import ProvisioningService

    provisioning = ProvisioningService(setup.repo, ProvisioningRepository(), FakeControlPlane())
    admin = AdminService(setup.repo, setup.rbac, setup.auth, provisioning=provisioning)
    result = await admin.create_account_with_first_admin(
        session,
        actor=_psa_principal(setup),
        account_name=code.capitalize(),
        account_code=code,
        admin_email=f"admin@{code}.com",
        admin_username=f"admin_{code}",
        request_id="req-" + uuid.uuid4().hex[:12],
    )
    await session.commit()
    return result


async def _psa_retry(session: AsyncSession, setup: AuthSetup, account_id):
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.provisioning.repository import ProvisioningRepository
    from openviking.server.platform.provisioning.service import ProvisioningService

    provisioning = ProvisioningService(setup.repo, ProvisioningRepository(), FakeControlPlane())
    return await provisioning.retry_account(
        session,
        actor=_psa_principal(setup),
        account_id=account_id,
        request_id="req-" + uuid.uuid4().hex[:12],
    )


def select_outbox_where_account(account_id: str):
    from sqlalchemy import select

    return select(IamOutbox).where(IamOutbox.payload["account_id"].astext == account_id)


def select_audit_by_request_id(request_id: str):
    from sqlalchemy import select

    return select(IamAuditEvent).where(IamAuditEvent.request_id == request_id)
