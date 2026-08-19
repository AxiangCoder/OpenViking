"""P1-E5 AdminService 服务层验收（14 号计划 §96.5 验收标准 ①②③④⑤⑦ 的服务面）。

依赖真实 PostgreSQL（conftest 会话级重建测试库并 upgrade head）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.admin.service import AdminService, decode_cursor
from openviking.server.platform.auth.password import verify_password
from openviking.server.platform.auth.principals import (
    AuthenticatedUserPrincipal,
    resolve_session_principal,
)
from openviking.server.platform.errors import (
    AdminActionForbiddenError,
    AuthenticationError,
    ConstraintViolationError,
    EntityNotFoundError,
    LastAccountAdminError,
)
from openviking.server.platform.iam.permissions import ACCOUNT_ADMIN, USER
from openviking.server.platform.models import IamRole, IamUser, IamUserRole
from tests.platform.helpers import (
    build_auth_setup,
    create_account,
    create_api_key,
    create_login_session,
    create_user,
    run_provisioning,
)


def _principal(user: IamUser) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=None,
        user_status=user.status,
        authentication_method="session",
    )


async def _admin_service(session: AsyncSession) -> tuple[AdminService, object]:
    setup = await build_auth_setup(session)
    return AdminService(setup.repo, setup.rbac, setup.auth), setup


async def _role_code(session: AsyncSession, user_id) -> str | None:
    link = (
        await session.execute(select(IamUserRole).where(IamUserRole.user_id == user_id))
    ).scalar_one_or_none()
    if link is None:
        return None
    role = (await session.execute(select(IamRole).where(IamRole.id == link.role_id))).scalar_one_or_none()
    return role.code if role is not None else None


# ── AC ①：PSA 创建 Account + 首位 Admin（一次返回初始密码、ov 映射）──


async def test_create_account_with_first_admin(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    result = await admin_service.create_account_with_first_admin(
        session,
        actor=_principal(setup.psa),
        account_code="beta",
        account_name="Beta",
        admin_email="badmin@beta.com",
        admin_username="badmin",
    )
    await session.commit()

    assert result.account.ov_account_id.startswith("ov_account_")
    assert result.admin.ov_user_id.startswith("ov_user_")
    assert len(result.initial_password) >= 16
    # 服务端只存 Argon2id hash（03 §8.3：不保存明文或可逆密文）
    assert result.admin.password_hash.startswith("$argon2id$")
    assert verify_password(result.admin.password_hash, result.initial_password)
    assert not verify_password(result.admin.password_hash, "Init-Pass-2026-Dev!")
    # 角色固定 account_admin（03 §8.3 首位 Admin）
    assert await _role_code(session, result.admin.id) == ACCOUNT_ADMIN
    # 审计：Actor=PSA、Subject=Account+首位 Admin，无密码明文（AC ⑥）
    events = await setup.repo.list_audit_events(session, action="account.create")
    assert len(events) == 1
    event = events[0]
    assert event.actor_user_id == setup.psa.id
    assert event.subject_account_id == result.account.id
    assert event.subject_user_id == result.admin.id
    assert event.result == "success"
    assert "password" not in str(event.metadata_json)
    assert result.initial_password not in str(event.metadata_json)


async def test_create_account_duplicate_code_and_email(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    with pytest.raises(ConstraintViolationError, match="ACCOUNT_CODE_ALREADY_EXISTS"):
        await admin_service.create_account_with_first_admin(
            session,
            actor=_principal(setup.psa),
            account_code="acme",
            account_name="Dup",
            admin_email="dup@acme.com",
            admin_username="dup",
        )
    with pytest.raises(ConstraintViolationError, match="EMAIL_ALREADY_EXISTS"):
        await admin_service.create_account_with_first_admin(
            session,
            actor=_principal(setup.psa),
            account_code="beta",
            account_name="Beta",
            admin_email="admin@acme.com",
            admin_username="badmin",
        )
    await session.rollback()
    accounts = await setup.repo.list_accounts(session)
    assert len(accounts) == 1  # 失败路径不留半成品


# ── AC ②：Account Admin 直建 User（角色固定 user）──


async def test_create_account_user_role_fixed_user(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    result = await admin_service.create_account_user(
        session,
        actor=_principal(setup.admin),
        email="bob@acme.com",
        username="bob",
    )
    await session.commit()

    assert result.user.account_id == setup.acme.id
    assert len(result.initial_password) >= 16
    assert verify_password(result.user.password_hash, result.initial_password)
    assert await _role_code(session, result.user.id) == USER  # 角色固定 user
    events = await setup.repo.list_audit_events(session, action="user.create")
    assert len(events) == 1
    assert events[0].actor_user_id == setup.admin.id
    assert events[0].subject_user_id == result.user.id
    assert result.initial_password not in str(events[0].metadata_json)


async def test_create_account_user_duplicate_email_username(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    with pytest.raises(ConstraintViolationError, match="EMAIL_ALREADY_EXISTS"):
        await admin_service.create_account_user(
            session, actor=_principal(setup.admin), email="alice@acme.com", username="x"
        )
    with pytest.raises(ConstraintViolationError, match="USERNAME_ALREADY_EXISTS"):
        await admin_service.create_account_user(
            session, actor=_principal(setup.admin), email="x@acme.com", username="alice"
        )
    await session.rollback()


async def test_create_account_user_without_account_context_404(session: AsyncSession) -> None:
    """PSA 无 Account 上下文（走平台路径）→ 404 不可见语义，不能直建 User。"""
    admin_service, setup = await _admin_service(session)
    with pytest.raises(EntityNotFoundError):
        await admin_service.create_account_user(
            session, actor=_principal(setup.psa), email="bob@acme.com", username="bob"
        )


# ── AC ⑤⑦：禁用即时杀 Session+全部 Key；最后一名 Account Admin 被拒 ──


async def test_disable_revokes_sessions_and_keys(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    raw1, _c1, _s1 = await create_login_session(setup, session, setup.alice)
    raw2, _c2, _s2 = await create_login_session(setup, session, setup.alice)
    await create_api_key(setup.repo, session, setup.acme, setup.alice, name="Codex")
    await create_api_key(setup.repo, session, setup.acme, setup.alice, name="OpenClaw")

    result = await admin_service.set_user_status(
        session, actor=_principal(setup.admin), target_user_id=setup.alice.id, status="disabled"
    )
    await session.commit()

    assert result.changed is True
    assert result.sessions_revoked == 2
    assert result.keys_revoked == 2
    user = await setup.repo.get_user(session, setup.alice.id)
    assert user.status == "disabled"
    # 权限缓存版本递增（03 §9.4：禁用状态即时生效）
    assert user.permission_version > 0
    # 全部 Session 立即失效
    for raw in (raw1, raw2):
        with pytest.raises(AuthenticationError):
            await resolve_session_principal(session, setup.repo, setup.rbac, raw)
    # 全部 API Key 撤销（AC ⑤）
    for cred in await setup.repo.list_api_credentials_for_user(session, setup.alice.id):
        assert cred.revoked_at is not None
    # 审计含撤销计数、无密钥/密码明文（AC ⑥）
    events = await setup.repo.list_audit_events(session, action="user.disable")
    assert len(events) == 1
    assert events[0].metadata_json == {"sessions_revoked": 2, "keys_revoked": 2}
    assert events[0].actor_user_id == setup.admin.id
    assert events[0].subject_user_id == setup.alice.id


async def test_disable_last_account_admin_rejected(session: AsyncSession) -> None:
    """AC ⑦：最后一名 Account Admin 禁用被拒（LAST_ACCOUNT_ADMIN_REQUIRED）。"""
    admin_service, setup = await _admin_service(session)
    result = await admin_service.create_account_with_first_admin(
        session,
        actor=_principal(setup.psa),
        account_code="solo",
        account_name="Solo",
        admin_email="solo@acme.com",
        admin_username="solo",
    )
    # P2-E1：ProvisioningWorker 转 active 后最后一名 Admin 守卫方可生效
    await run_provisioning(session)
    await session.commit()

    with pytest.raises(LastAccountAdminError):
        await admin_service.set_user_status(
            session, actor=_principal(result.admin), target_user_id=result.admin.id, status="disabled"
        )
    await session.commit()
    # 目标仍 active（未被禁用）
    user = await setup.repo.get_user(session, result.admin.id)
    assert user.status == "active"
    # denied 审计（AC ⑥）
    events = await setup.repo.list_audit_events(session, action="user.disable", result="denied")
    assert len(events) == 1
    assert events[0].reason == "LAST_ACCOUNT_ADMIN_REQUIRED"

    # 两个 Account Admin 时（提升 alice），其中一个可被禁用
    await admin_service.promote_user(
        session, actor=_principal(setup.psa), target_user_id=setup.alice.id, account_id=setup.acme.id
    )
    await session.commit()
    # acme 有 admin + alice 两位 Account Admin：禁用 alice 不再触发守卫
    ok = await admin_service.set_user_status(
        session, actor=_principal(setup.admin), target_user_id=setup.alice.id, status="disabled"
    )
    await session.commit()
    assert ok.changed is True


async def test_enable_after_disable(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    await admin_service.set_user_status(
        session, actor=_principal(setup.admin), target_user_id=setup.alice.id, status="disabled"
    )
    await session.commit()
    result = await admin_service.set_user_status(
        session, actor=_principal(setup.admin), target_user_id=setup.alice.id, status="active"
    )
    await session.commit()
    assert result.changed is True
    assert (await setup.repo.get_user(session, setup.alice.id)).status == "active"
    events = await setup.repo.list_audit_events(session, action="user.enable")
    assert len(events) == 1


async def test_set_user_status_idempotent(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    result = await admin_service.set_user_status(
        session, actor=_principal(setup.admin), target_user_id=setup.alice.id, status="active"
    )
    await session.commit()
    assert result.changed is False
    assert await setup.repo.list_audit_events(session, action="user.enable") == []


# ── AC ④：平台级提升（仅 user→account_admin、即时生效、免重登）──


async def test_promote_user_to_account_admin_immediate(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    raw, _csrf, _sid = await create_login_session(setup, session, setup.alice)
    await admin_service.promote_user(
        session, actor=_principal(setup.psa), target_user_id=setup.alice.id, account_id=setup.acme.id
    )
    await session.commit()

    assert await _role_code(session, setup.alice.id) == ACCOUNT_ADMIN
    # 提升不轮换 Session（03 §8.1）；免重登即时生效（AC ④ / spike ==11）
    principal = await resolve_session_principal(session, setup.repo, setup.rbac, raw)
    assert ACCOUNT_ADMIN in principal.role_codes
    assert "user.create" in principal.permissions
    # 审计：Actor=PSA、Subject=目标（AC ⑥）
    events = await setup.repo.list_audit_events(session, action="role.assign", result="success")
    assert len(events) == 1
    assert events[0].actor_user_id == setup.psa.id
    assert events[0].subject_user_id == setup.alice.id
    assert events[0].metadata_json == {"role_from": USER, "role_to": ACCOUNT_ADMIN}


async def test_promote_guards(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    # 目标已是 account_admin → 403 ROLE_ASSIGNMENT_ONLY_FROM_USER
    with pytest.raises(AdminActionForbiddenError, match="ROLE_ASSIGNMENT_ONLY_FROM_USER"):
        await admin_service.promote_user(
            session, actor=_principal(setup.psa), target_user_id=setup.admin.id, account_id=setup.acme.id
        )
    await session.commit()
    events = await setup.repo.list_audit_events(session, action="role.assign", result="denied")
    assert len(events) == 1
    assert events[0].reason == "ROLE_ASSIGNMENT_ONLY_FROM_USER"

    # 跨 Account 目标 → 404（不可见语义）
    beta = await create_account(setup.repo, session, "beta")
    bob = await create_user(setup.repo, session, beta, email="bob@beta.com", username="bob")
    await session.commit()
    with pytest.raises(EntityNotFoundError):
        await admin_service.promote_user(
            session, actor=_principal(setup.psa), target_user_id=bob.id, account_id=setup.acme.id
        )
    # PSA（无 Account）不可作为提升目标 → 404
    with pytest.raises(EntityNotFoundError):
        await admin_service.promote_user(
            session, actor=_principal(setup.psa), target_user_id=setup.psa.id, account_id=setup.acme.id
        )


# ── AC ③：分级重置（平台路径补路径归属校验，等级比较在 AuthService）──


async def test_reset_platform_path_revokes_sessions(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    raw, _c, _s = await create_login_session(setup, session, setup.alice)
    result = await admin_service.reset_user_password(
        session,
        actor=_principal(setup.psa),
        target_user_id=setup.alice.id,
        scope_account_id=setup.acme.id,
    )
    await session.commit()
    assert len(result.new_password) == 16
    assert result.sessions_revoked >= 1
    with pytest.raises(AuthenticationError):
        await resolve_session_principal(session, setup.repo, setup.rbac, raw)


async def test_reset_platform_path_cross_account_404(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    beta = await create_account(setup.repo, session, "beta")
    await session.commit()
    with pytest.raises(EntityNotFoundError):
        await admin_service.reset_user_password(
            session,
            actor=_principal(setup.psa),
            target_user_id=setup.alice.id,
            scope_account_id=beta.id,
        )
    # 目标 PSA（不在任何 Account）→ 404（禁目标 PSA，05 §12.6 平台表）
    with pytest.raises(EntityNotFoundError):
        await admin_service.reset_user_password(
            session,
            actor=_principal(setup.psa),
            target_user_id=setup.psa.id,
            scope_account_id=setup.acme.id,
        )


# ── API Key 元数据只读/撤销（04 §10.3，P1-E4 未合并前直用 E1 repository）──


async def test_api_key_metadata_list_and_revoke(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    key1 = await create_api_key(setup.repo, session, setup.acme, setup.alice, name="Codex")
    key2 = await create_api_key(setup.repo, session, setup.acme, setup.alice, name="OpenClaw")
    await session.commit()

    creds = await admin_service.list_user_api_keys(
        session, scope_account_id=setup.acme.id, target_user_id=setup.alice.id
    )
    assert {c.name for c in creds} == {"Codex", "OpenClaw"}
    # 元数据不含明文/完整 hash（04 §10.3）
    for c in creds:
        assert len(c.key_last_four) == 4  # 仅末四位掩码
        assert "dev-secret" not in c.key_hash  # 不存 secret，仅 SHA-256

    revoked = await admin_service.revoke_user_api_key(
        session,
        actor=_principal(setup.admin),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
        credential_id=key1.id,
    )
    await session.commit()
    assert revoked.revoked_at is not None
    # 按名独立：key2 不受影响；key1 保留元数据但标记撤销（spike ==9 语义）
    remaining = await admin_service.list_user_api_keys(
        session, scope_account_id=setup.acme.id, target_user_id=setup.alice.id
    )
    by_id = {c.id: c for c in remaining}
    assert set(by_id) == {key1.id, key2.id}
    assert by_id[key1.id].revoked_at is not None
    assert by_id[key2.id].revoked_at is None
    # 重复撤销 → 404（幂等）
    with pytest.raises(EntityNotFoundError):
        await admin_service.revoke_user_api_key(
            session,
            actor=_principal(setup.admin),
            scope_account_id=setup.acme.id,
            target_user_id=setup.alice.id,
            credential_id=key1.id,
        )
    await session.commit()
    # 跨用户撤销不可见 → 404
    with pytest.raises(EntityNotFoundError):
        await admin_service.revoke_user_api_key(
            session,
            actor=_principal(setup.admin),
            scope_account_id=setup.acme.id,
            target_user_id=setup.admin.id,
            credential_id=key2.id,
        )
    # 撤销审计：Actor=管理员、Subject=Key 属主、metadata 仅 Key 名（AC ⑥）
    events = await setup.repo.list_audit_events(session, action="credential.revoke")
    assert len(events) == 1
    assert events[0].actor_user_id == setup.admin.id
    assert events[0].subject_user_id == setup.alice.id
    assert events[0].metadata_json == {"key_name": "Codex"}


# ── 审计列表：Account 过滤与平台范围 ──


async def test_audit_list_scopes(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    await admin_service.create_account_with_first_admin(
        session,
        actor=_principal(setup.psa),
        account_code="beta",
        account_name="Beta",
        admin_email="badmin@beta.com",
        admin_username="badmin",
    )
    await session.commit()

    acme_events, _ = await admin_service.list_audit_events(session, account_id=setup.acme.id)
    assert all(e.account_id == setup.acme.id for e in acme_events)
    assert any(e.action == "auth.login" for e in acme_events) is False  # 种子阶段无登录
    platform_events, _ = await admin_service.list_audit_events(session)
    assert any(e.action == "account.create" for e in platform_events)
    assert len(platform_events) > len(acme_events)


# ── 分页（05 §12.2：items + next_cursor）──


async def test_list_pagination_cursor(session: AsyncSession) -> None:
    admin_service, setup = await _admin_service(session)
    for i in range(5):  # acme + page0..page4 = 6 个 Account
        await create_account(setup.repo, session, f"page{i}")
    await session.commit()

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        rows, next_cursor = await admin_service.list_accounts(
            session, limit=2, cursor=decode_cursor(cursor)
        )
        seen.extend(str(r.id) for r in rows)
        pages += 1
        if next_cursor is None:
            break
        cursor = next_cursor
    assert pages == 3
    assert len(set(seen)) == 6  # 无重叠、无遗漏
