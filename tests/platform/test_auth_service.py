"""P1-E3 AuthService 验收（14 号计划 §96.3 验收 ①–⑤⑦⑨⑩）。

覆盖：统一 LOGIN_FAILED 防枚举、限流递增冷却、改密轮换、分级密码重置
（跨 Account 404 / 同级 403 / 只撤 Session 不撤 API Key）、PSA bootstrap、
登录成功/失败/登出/会话撤销审计（Actor/Subject 分离、脱敏）。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex, verify_password
from openviking.server.platform.auth.rate_limit import LoginRateLimiter
from openviking.server.platform.auth.service import AuthService
from openviking.server.platform.errors import (
    AuthenticationError,
    EntityNotFoundError,
    LoginFailedError,
    LoginRateLimitedError,
    PasswordResetForbiddenError,
    PlatformError,
)
from openviking.server.platform.iam.permissions import (
    ACCOUNT_ADMIN,
    PLATFORM_PERMISSIONS,
    PLATFORM_SUPER_ADMIN,
    USER,
)
from tests.platform.helpers import (
    DEFAULT_PASSWORD,
    AuthSetup,
    build_auth_setup,
    create_account,
    create_login_session,
    create_user,
)

ALICE_PASSWORD = DEFAULT_PASSWORD

LOGIN_IP = "203.0.113.10"


# ── AC ①⑨：登录成功 + 审计 ──


async def test_login_success_issues_session_and_audit(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    result = await setup.auth.login(
        session,
        email=" alice@acme.com ",
        password="Init-Pass-2026-Dev!",
        ip=LOGIN_IP,
        user_agent="test-agent",
        request_id="req-1",
    )
    await session.commit()

    assert len(result.raw_token) >= 40  # ≥256bit（03 §8.1）
    assert len(result.csrf_token) >= 32
    assert result.user_id == setup.alice.id
    # DB 仅存 SHA-256（04 §10.7）
    row = await setup.repo.get_session_by_token_hash(session, sha256_hex(result.raw_token))
    assert row is not None and row.user_id == setup.alice.id
    assert row.csrf_secret_hash == sha256_hex(result.csrf_token)
    # last_login_at 更新（04 §10.2）
    assert (await setup.repo.get_user(session, setup.alice.id)).last_login_at is not None

    # AC ⑨：登录成功审计（Actor/Subject 分离、脱敏）
    events = await setup.repo.list_audit_events(session, action="auth.login", result="success")
    assert len(events) == 1
    event = events[0]
    assert event.actor_user_id == setup.alice.id == event.subject_user_id
    assert event.actor_account_id == setup.acme.id == event.subject_account_id
    assert event.actor_session_id == result.session_id
    assert event.authentication_method == "session"
    assert event.request_id == "req-1"
    assert event.metadata_json == {"method": "password"}
    assert ALICE_PASSWORD not in str(event.metadata_json)  # 无密码明文


# ── AC ②：统一 LOGIN_FAILED 防枚举 ──


async def test_login_unknown_email_and_wrong_password_same_code(
    repo, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    cases = [
        {"email": "nobody@acme.com", "password": "whatever-1234"},  # 未知邮箱
        {"email": "alice@acme.com", "password": "wrong-password"},  # 错误密码
    ]
    for case in cases:
        with pytest.raises(LoginFailedError) as exc:
            await setup.auth.login(session, ip=LOGIN_IP, **case)
        assert exc.value.code == "LOGIN_FAILED"
        await session.commit()

    events = await setup.repo.list_audit_events(session, action="auth.login", result="failed")
    assert len(events) == 2
    for event in events:
        assert event.reason == "LOGIN_FAILED"
        assert event.actor_user_id is None  # Actor 未知（防枚举）
        assert event.subject_user_id is None
        assert event.metadata_json["normalized_email"] in ("nobody@acme.com", "alice@acme.com")


async def test_login_disabled_user_same_code(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    await setup.repo.update_user(session, setup.alice.id, status="disabled")
    await session.commit()
    with pytest.raises(LoginFailedError):
        await setup.auth.login(
            session, email="alice@acme.com", password="Init-Pass-2026-Dev!", ip=LOGIN_IP
        )
    await session.commit()


async def test_login_updates_last_login_at(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    await setup.auth.login(
        session, email="alice@acme.com", password="Init-Pass-2026-Dev!", ip=LOGIN_IP
    )
    await session.commit()
    user = await setup.repo.get_user(session, setup.alice.id)
    assert user.last_login_at is not None


# ── AC ⑦：连续失败触发限流冷却 ──


async def test_login_rate_limit_cooldown_blocks_correct_password(
    repo, session: AsyncSession
) -> None:
    """AC ⑦：连续失败进入递增冷却；冷却期间正确密码同样被拒（Retry-After）。"""
    setup = await build_auth_setup(
        session,
        limiter=LoginRateLimiter(max_attempts=3, cooldown_base_seconds=60, cooldown_max_seconds=3600),
    )
    for _ in range(3):
        with pytest.raises(LoginFailedError):
            await setup.auth.login(
                session, email="alice@acme.com", password="wrong-password", ip=LOGIN_IP
            )
        await session.commit()

    # 冷却期间正确密码也拒绝，且携带 retry_after
    with pytest.raises(LoginRateLimitedError) as exc:
        await setup.auth.login(
            session, email="alice@acme.com", password="Init-Pass-2026-Dev!", ip=LOGIN_IP
        )
    assert exc.value.code == "LOGIN_FAILED"  # 对外统一码（防枚举）
    assert exc.value.retry_after_seconds > 0
    await session.commit()
    # 审计记录 rate_limited 失败（list_audit_events 按 occurred_at DESC，需过滤）
    events = await setup.repo.list_audit_events(session, action="auth.login", result="failed")
    limited = [e for e in events if e.reason == "RATE_LIMITED"]
    assert len(limited) == 1


async def test_login_rate_limit_keyed_by_ip_and_identifier(
    repo, session: AsyncSession
) -> None:
    """限流 key = IP+登录标识：换 IP/换邮箱不受影响。"""
    setup = await build_auth_setup(
        session,
        limiter=LoginRateLimiter(max_attempts=2, cooldown_base_seconds=60, cooldown_max_seconds=3600),
    )
    for _ in range(2):
        with pytest.raises(LoginFailedError):
            await setup.auth.login(
                session, email="alice@acme.com", password="wrong", ip=LOGIN_IP
            )
        await session.commit()
    # 同一 IP 不同标识不受影响
    with pytest.raises(LoginFailedError):
        await setup.auth.login(
            session, email="admin@acme.com", password="wrong", ip=LOGIN_IP
        )
    # 不同 IP 同一标识不受影响
    with pytest.raises(LoginFailedError):
        await setup.auth.login(
            session, email="alice@acme.com", password="wrong", ip="198.51.100.7"
        )
    await session.commit()


async def test_login_success_resets_rate_limit(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(
        session,
        limiter=LoginRateLimiter(max_attempts=3, cooldown_base_seconds=60, cooldown_max_seconds=3600),
    )
    for _ in range(2):  # 未到阈值
        with pytest.raises(LoginFailedError):
            await setup.auth.login(
                session, email="alice@acme.com", password="wrong", ip=LOGIN_IP
            )
        await session.commit()
    await setup.auth.login(
        session, email="alice@acme.com", password="Init-Pass-2026-Dev!", ip=LOGIN_IP
    )
    await session.commit()
    with pytest.raises(LoginFailedError):  # 重置后重新计数，不直接进冷却
        await setup.auth.login(
            session, email="alice@acme.com", password="wrong", ip=LOGIN_IP
        )
    await session.commit()


# ── AC ③：改密（旧密码必填 + 轮换）──


async def _principal(setup: AuthSetup, session: AsyncSession, raw_token: str):
    from openviking.server.platform.auth.principals import resolve_session_principal

    return await resolve_session_principal(session, setup.repo, setup.rbac, raw_token)


async def test_change_password_wrong_old_password(repo, session: AsyncSession) -> None:
    """AC ③：错旧密码 → LOGIN_FAILED，密码不变，审计 failed。"""
    setup = await build_auth_setup(session)
    raw, csrf, session_id = await create_login_session(setup, session, setup.alice)
    principal = await _principal(setup, session, raw)

    with pytest.raises(LoginFailedError) as exc:
        await setup.auth.change_password(
            session,
            principal=principal,
            old_password="bad-old-password",
            new_password="NewPass-2026-strong!",
            ip=LOGIN_IP,
            request_id="req-2",
        )
    assert exc.value.code == "LOGIN_FAILED"
    await session.commit()

    user = await setup.repo.get_user(session, setup.alice.id)
    assert verify_password(user.password_hash, "Init-Pass-2026-Dev!")  # 密码未变
    assert not verify_password(user.password_hash, "NewPass-2026-strong!")
    # 会话未被撤销
    await _principal(setup, session, raw)

    events = await setup.repo.list_audit_events(
        session, action="auth.password_change", result="failed"
    )
    assert len(events) == 1
    assert events[0].reason == "LOGIN_FAILED"
    assert events[0].request_id == "req-2"


async def test_change_password_success_rotates_session(repo, session: AsyncSession) -> None:
    """AC ③：改密成功 → 密码更新、旧 Session 撤销（rotated）、新 Session 可用。"""
    setup = await build_auth_setup(session)
    old_raw, _csrf, old_session_id = await create_login_session(setup, session, setup.alice)
    principal = await _principal(setup, session, old_raw)

    result = await setup.auth.change_password(
        session,
        principal=principal,
        old_password="Init-Pass-2026-Dev!",
        new_password="NewPass-2026-strong!",
        ip=LOGIN_IP,
    )
    await session.commit()

    assert result.session_id != old_session_id
    assert result.raw_token != old_raw
    assert len(result.raw_token) >= 40
    user = await setup.repo.get_user(session, setup.alice.id)
    assert verify_password(user.password_hash, "NewPass-2026-strong!")
    assert user.password_changed_at is not None
    # 旧 Cookie 失效（AC ③ 补断言）
    with pytest.raises(AuthenticationError) as exc:
        await _principal(setup, session, old_raw)
    assert exc.value.code == "SESSION_EXPIRED"
    new_principal = await _principal(setup, session, result.raw_token)
    assert new_principal.session_id == result.session_id
    # 新 Session 的 CSRF secret 与响应一致
    row = await setup.repo.get_session_by_token_hash(session, sha256_hex(result.raw_token))
    assert setup.sessions.verify_csrf_token(row.csrf_secret_hash, result.csrf_token)

    events = await setup.repo.list_audit_events(
        session, action="auth.password_change", result="success"
    )
    assert len(events) == 1
    assert events[0].metadata_json == {"session_rotated": True}


async def test_change_password_rate_limited(repo, session: AsyncSession) -> None:
    """改密错旧密码计入登录限流（03 §8.3）：冷却后正确旧密码也被拒。"""
    setup = await build_auth_setup(
        session,
        limiter=LoginRateLimiter(max_attempts=2, cooldown_base_seconds=60, cooldown_max_seconds=3600),
    )
    raw, _csrf, _sid = await create_login_session(setup, session, setup.alice)
    principal = await _principal(setup, session, raw)

    for _ in range(2):
        with pytest.raises(LoginFailedError):
            await setup.auth.change_password(
                session,
                principal=principal,
                old_password="bad-old",
                new_password="NewPass-2026-strong!",
                ip=LOGIN_IP,
            )
        await session.commit()

    with pytest.raises(LoginRateLimitedError) as exc:
        await setup.auth.change_password(
            session,
            principal=principal,
            old_password="Init-Pass-2026-Dev!",
            new_password="NewPass-2026-strong!",
            ip=LOGIN_IP,
        )
    assert exc.value.retry_after_seconds > 0
    await session.commit()


# ── AC ⑧⑨：登出 / 登出全部 ──


async def test_logout_revokes_and_audits(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    raw, _csrf, session_id = await create_login_session(setup, session, setup.alice)
    principal = await _principal(setup, session, raw)

    await setup.auth.logout(session, principal=principal, request_id="req-3")
    await session.commit()

    row = await setup.repo.get_session_by_token_hash(session, sha256_hex(raw))
    assert row is not None and row.revoked_at is not None
    assert row.revoked_reason == "logout"
    with pytest.raises(AuthenticationError):
        await _principal(setup, session, raw)

    events = await setup.repo.list_audit_events(session, action="auth.logout", result="success")
    assert len(events) == 1
    assert events[0].actor_user_id == setup.alice.id
    assert events[0].actor_session_id == session_id
    assert events[0].metadata_json == {"session_revoked": True}
    assert events[0].request_id == "req-3"


async def test_logout_all_revokes_every_session(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    raw1, _c1, _s1 = await create_login_session(setup, session, setup.alice)
    raw2, _c2, _s2 = await create_login_session(setup, session, setup.alice)
    principal = await _principal(setup, session, raw1)

    count = await setup.auth.logout_all(session, principal=principal)
    await session.commit()
    assert count == 2

    for raw in (raw1, raw2):
        with pytest.raises(AuthenticationError):
            await _principal(setup, session, raw)
    events = await setup.repo.list_audit_events(session, action="auth.logout_all")
    assert len(events) == 1
    assert events[0].metadata_json == {"sessions_revoked": 2}


# ── AC ④⑤⑩：分级密码重置（服务层；admin/platform 端点 P1-E5）──


async def _reset_for(
    setup: AuthSetup,
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    actor_account_id: uuid.UUID | None,
    target_user_id: uuid.UUID,
):
    return await setup.auth.reset_user_password(
        session,
        actor_user_id=actor_user_id,
        actor_account_id=actor_account_id,
        target_user_id=target_user_id,
    )


async def test_reset_account_admin_resets_user(repo, session: AsyncSession) -> None:
    """AC ④⑩：account_admin(rank2) 重置 user(rank1) → 新密码生效、
    目标全部登录 Session 撤销、审计 success（sessions_revoked）。"""
    setup = await build_auth_setup(session)
    raw1, _c1, _s1 = await create_login_session(setup, session, setup.alice)
    raw2, _c2, _s2 = await create_login_session(setup, session, setup.alice)
    # API Key 不受影响（AC ④）
    await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=setup.alice.id,
        name="Codex",
        public_id="pub_1",
        key_hash=sha256_hex("secret-1"),
        key_last_four="4f2a",
        created_by=setup.alice.id,
    )
    await session.commit()

    result = await _reset_for(
        setup,
        session,
        actor_user_id=setup.admin.id,
        actor_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()

    assert len(result.new_password) == 16
    assert result.sessions_revoked == 2
    user = await setup.repo.get_user(session, setup.alice.id)
    assert verify_password(user.password_hash, result.new_password)
    assert not verify_password(user.password_hash, "Init-Pass-2026-Dev!")  # 旧密码立即失效
    # 全部登录 Session 撤销（AC ⑩）
    for raw in (raw1, raw2):
        with pytest.raises(AuthenticationError):
            await _principal(setup, session, raw)
    # 重置后新密码可登录
    login = await setup.auth.login(
        session, email="alice@acme.com", password=result.new_password, ip=LOGIN_IP
    )
    await session.commit()
    assert login.user_id == setup.alice.id
    # API Key 未被撤销（AC ④）
    creds = await setup.repo.list_api_credentials_for_user(session, setup.alice.id)
    assert len(creds) == 1 and creds[0].status == "active"
    # 会话撤销审计（Actor/Subject 分离、脱敏，AC ⑨）
    events = await setup.repo.list_audit_events(session, action="user.password.reset")
    assert len(events) == 1
    event = events[0]
    assert event.actor_user_id == setup.admin.id
    assert event.subject_user_id == setup.alice.id
    assert event.actor_account_id == setup.acme.id
    assert event.result == "success"
    assert event.metadata_json == {"sessions_revoked": 2}
    assert "password" not in str(event.metadata_json)


async def test_reset_cross_account_404(repo, session: AsyncSession) -> None:
    """AC ⑤：跨 Account 重置 → EntityNotFoundError（404 语义，不写审计防枚举）。"""
    setup = await build_auth_setup(session)
    beta = await create_account(setup.repo, session, "beta")
    admin_beta = await create_user(
        setup.repo, session, beta, email="admin@beta.com", username="admin_beta"
    )
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=admin_beta.id,
        actor_account_id=beta.id,
        target_user_id=admin_beta.id,
        role_code=ACCOUNT_ADMIN,
    )
    await session.commit()

    with pytest.raises(EntityNotFoundError):
        await _reset_for(
            setup,
            session,
            actor_user_id=admin_beta.id,
            actor_account_id=beta.id,
            target_user_id=setup.alice.id,
        )
    await session.commit()
    assert await setup.repo.list_audit_events(session, action="user.password.reset") == []


async def test_reset_same_rank_forbidden(repo, session: AsyncSession) -> None:
    """AC ⑤：同级（admin 2→2、alice 1→1）→ 403 语义 + denied 审计。"""
    setup = await build_auth_setup(session)
    # 再建一个同 Account 的 account_admin（bob）
    bob = await create_user(setup.repo, session, setup.acme, email="bob@acme.com", username="bob")
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=bob.id,
        role_code=ACCOUNT_ADMIN,
    )
    await session.commit()

    with pytest.raises(PasswordResetForbiddenError) as exc:
        await _reset_for(
            setup,
            session,
            actor_user_id=setup.admin.id,
            actor_account_id=setup.acme.id,
            target_user_id=bob.id,
        )
    assert exc.value.reason == "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN"
    await session.commit()
    events = await setup.repo.list_audit_events(session, action="user.password.reset", result="denied")
    assert len(events) == 1
    assert events[0].reason == "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN"
    assert events[0].actor_user_id == setup.admin.id
    assert events[0].subject_user_id == bob.id


async def test_reset_user_cannot_reset_peer(repo, session: AsyncSession) -> None:
    """AC ⑤：user(1→1) 同级重置被拒（服务层无端点，语义即等级规则）。"""
    setup = await build_auth_setup(session)
    bob = await create_user(setup.repo, session, setup.acme, email="bob@acme.com", username="bob")
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=bob.id,
        role_code=USER,
    )
    await session.commit()
    with pytest.raises(PasswordResetForbiddenError):
        await _reset_for(
            setup,
            session,
            actor_user_id=setup.alice.id,
            actor_account_id=setup.acme.id,
            target_user_id=bob.id,
        )
    await session.commit()


async def test_reset_psa_resets_account_admin(repo, session: AsyncSession) -> None:
    """平台 rank 3>2：PSA 可重置 Account Admin；密码可用新密码登录。"""
    setup = await build_auth_setup(session)
    raw, _c, _s = await create_login_session(setup, session, setup.admin)
    result = await _reset_for(
        setup,
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=setup.admin.id,
    )
    await session.commit()
    assert result.sessions_revoked == 1
    with pytest.raises(AuthenticationError):
        await _principal(setup, session, raw)
    login = await setup.auth.login(
        session, email="admin@acme.com", password=result.new_password, ip=LOGIN_IP
    )
    await session.commit()
    assert login.user_id == setup.admin.id


async def test_reset_psa_self_forbidden(repo, session: AsyncSession) -> None:
    """AC ⑤：PSA 不能重置 PSA（3<=3）。"""
    setup = await build_auth_setup(session)
    with pytest.raises(PasswordResetForbiddenError):
        await _reset_for(
            setup,
            session,
            actor_user_id=setup.psa.id,
            actor_account_id=None,
            target_user_id=setup.psa.id,
        )
    await session.commit()


async def test_reset_unknown_target_not_audited(repo, session: AsyncSession) -> None:
    """not-found 语义：目标不存在 → EntityNotFoundError，不写审计（防枚举）。"""
    setup = await build_auth_setup(session)
    with pytest.raises(EntityNotFoundError):
        await _reset_for(
            setup,
            session,
            actor_user_id=setup.psa.id,
            actor_account_id=None,
            target_user_id=uuid.uuid4(),
        )
    await session.commit()
    assert await setup.repo.list_audit_events(session, action="user.password.reset") == []


# ── 首次 PSA 初始化 / bootstrap 路径 ──


async def test_bootstrap_creates_psa(repo, session: AsyncSession) -> None:
    """首次初始化：无 PSA 时创建首位 PSA（account_id IS NULL + PSA 角色）。"""
    from openviking.server.platform.iam import RbacService

    rbac = RbacService(repo)
    auth = AuthService(repo, rbac)
    result = await auth.bootstrap_platform_super_admin(
        session,
        email="  Root@Platform.Local ",
        username="root",
        display_name="Root",
        request_id="req-boot",
    )
    await session.commit()

    user = await repo.get_user(session, result.user_id)
    assert user.account_id is None  # 04 §10.4：无 Account 用户 = PSA
    assert user.normalized_email == "root@platform.local"
    perms = await rbac.get_user_permissions(session, result.user_id)
    assert perms.role_codes == (PLATFORM_SUPER_ADMIN,)
    assert perms.permissions == PLATFORM_PERMISSIONS
    assert verify_password(user.password_hash, result.initial_password)
    assert len(result.initial_password) == 16

    events = await repo.list_audit_events(session, action="platform.bootstrap")
    assert len(events) == 1
    assert events[0].actor_type == "system"
    assert events[0].actor_user_id is None
    assert events[0].metadata_json == {"role": PLATFORM_SUPER_ADMIN}
    assert events[0].request_id == "req-boot"


async def test_bootstrap_rejects_when_psa_exists(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    with pytest.raises(PlatformError) as exc:
        await setup.auth.bootstrap_platform_super_admin(
            session, email="root2@platform.local", username="root2"
        )
    assert exc.value.args[0] == "PSA_ALREADY_BOOTSTRAPPED"
    await session.commit()
