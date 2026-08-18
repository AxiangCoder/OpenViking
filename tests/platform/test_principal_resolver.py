"""P1-E4 统一 Principal Resolver 测试（05 §11.4，14 号计划 §96.4）。

服务级断言（对应验收 ②③④ 与 spike `==4` 的 Principal 构造部分）：
- Session 与 API Key 产出同一 Principal：同 user_id/account/权限集，
  仅 `authentication_method` 与凭证 ID 不同（验收 ②）；
- Bearer 与 X-Api-Key 同一解析器、结果一致（验收 ③）；
- Cookie 优先、失效不自动回退 Bearer（验收 ④）；
- 撤销/到期/禁用/删除立即拒绝（验收 ⑤⑥，spike `==9/==10` 复跑切片）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.api_keys import ApiCredentialService
from openviking.server.platform.auth.principals import (
    INVALID_CREDENTIAL,
    SESSION_EXPIRED,
    USER_DISABLED,
    resolve_api_key_principal,
    resolve_principal,
    resolve_session_principal,
)
from openviking.server.platform.errors import AuthenticationError
from openviking.server.platform.iam.permissions import USER
from openviking.server.platform.models import IamApiCredential, IamUser
from tests.platform.helpers import AuthSetup, build_auth_setup, create_login_session

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _create_key(
    setup: AuthSetup, session: AsyncSession, user: IamUser, name: str = "resolver-key"
) -> str:
    svc = ApiCredentialService(setup.repo)
    created = await svc.create_key(session, user=user, name=name)
    await session.commit()
    return created.full_key


async def _create_user(setup: AuthSetup, session: AsyncSession, username: str) -> IamUser:
    user = await setup.repo.create_user(
        session,
        account_id=setup.acme.id,
        ov_user_id=f"ov_user_{username}",
        username=username,
        email=f"{username}@acme.com",
        display_name=username,
        password_hash="unused",
        status="active",
    )
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=user.id,
        role_code=USER,
    )
    await session.commit()
    return user


async def _session_token(setup: AuthSetup, session: AsyncSession, user: IamUser) -> str:
    raw, _csrf, _sid = await create_login_session(setup, session, user)
    return raw


def _assert_same_identity(a: object, b: object) -> None:
    assert a.actor_user_id == b.actor_user_id
    assert a.actor_account_id == b.actor_account_id
    assert a.actor_ov_user_id == b.actor_ov_user_id
    assert a.actor_ov_account_id == b.actor_ov_account_id
    assert a.role_codes == b.role_codes
    assert a.permissions == b.permissions
    assert a.ov_base_role == b.ov_base_role
    assert a.role_rank == b.role_rank


# ── AC ②：Session 与 API Key 同一 Principal、仅认证方式不同 ──


async def test_session_and_api_key_resolve_to_same_identity(
    session: AsyncSession,
) -> None:
    setup = await build_auth_setup(session)
    raw = await _session_token(setup, session, setup.alice)
    key = await _create_key(setup, session, setup.alice)

    via_session = await resolve_session_principal(session, setup.repo, setup.rbac, raw)
    via_key = await resolve_api_key_principal(session, setup.repo, setup.rbac, key)

    _assert_same_identity(via_session, via_key)
    assert via_session.authentication_method == "session"
    assert via_key.authentication_method == "api_key"
    assert via_session.session_id is not None and via_session.credential_id is None
    assert via_key.credential_id is not None and via_key.session_id is None
    assert via_key.permissions == via_session.permissions  # 同权限集（验收 ②）


async def test_api_key_resolver_rejects_invalid_forms(session: AsyncSession) -> None:
    """格式/前缀/段数错误 → 统一 INVALID_CREDENTIAL（03 §8.4，不泄露存在性）。"""
    setup = await build_auth_setup(session)
    for bad in ("", "not-a-key", "ovk_u.only-two-parts", "ovk_other.a.b.c", "ovk_u.."):
        with pytest.raises(AuthenticationError) as exc:
            await resolve_api_key_principal(session, setup.repo, setup.rbac, bad)
        assert exc.value.code == INVALID_CREDENTIAL


async def test_api_key_wrong_secret_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    full = await _create_key(setup, session, setup.alice)
    public_id = full.split(".")[1]
    with pytest.raises(AuthenticationError) as exc:
        await resolve_api_key_principal(
            session, setup.repo, setup.rbac, f"ovk_u.{public_id}.wrong-secret"
        )
    assert exc.value.code == INVALID_CREDENTIAL


async def test_api_key_unknown_public_id_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    with pytest.raises(AuthenticationError) as exc:
        await resolve_api_key_principal(
            session, setup.repo, setup.rbac, "ovk_u.unknown-public-id.some-secret"
        )
    assert exc.value.code == INVALID_CREDENTIAL


# ── AC ③：Bearer 与 X-Api-Key 同一解析器 ──


async def test_bearer_and_x_api_key_resolve_identically(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    key = await _create_key(setup, session, setup.alice)
    via_bearer = await resolve_principal(
        session, setup.repo, setup.rbac, bearer_key=key
    )
    via_header = await resolve_principal(
        session, setup.repo, setup.rbac, x_api_key=key
    )
    _assert_same_identity(via_bearer, via_header)
    assert via_bearer.authentication_method == via_header.authentication_method == "api_key"
    assert via_bearer.credential_id == via_header.credential_id


# ── AC ④：Cookie 优先、失效不自动回退 Bearer ──


async def test_cookie_wins_over_bearer(session: AsyncSession) -> None:
    """同时携带 Cookie 与 Bearer 时以 Cookie 为准（03 §8.4，spike 回填发现）。"""
    setup = await build_auth_setup(session)
    bob = await _create_user(setup, session, "bob")
    alice_raw = await _session_token(setup, session, setup.alice)
    bob_key = await _create_key(setup, session, bob)

    principal = await resolve_principal(
        session,
        setup.repo,
        setup.rbac,
        session_token=alice_raw,
        bearer_key=bob_key,
    )
    assert principal.actor_user_id == setup.alice.id
    assert principal.authentication_method == "session"


async def test_invalid_cookie_does_not_fallback_to_bearer(session: AsyncSession) -> None:
    """Cookie 已失效 → 401，不自动回退到有效 Bearer（验收 ④，防凭据混淆）。"""
    setup = await build_auth_setup(session)
    bob = await _create_user(setup, session, "bob")
    bob_key = await _create_key(setup, session, bob)
    alice_raw = await _session_token(setup, session, setup.alice)
    # 撤销 alice 全部 Session → Cookie 失效但 Bearer 仍有效
    await setup.repo.revoke_all_sessions_for_user(session, setup.alice.id, reason="test")
    await session.commit()

    with pytest.raises(AuthenticationError) as exc:
        await resolve_principal(
            session,
            setup.repo,
            setup.rbac,
            session_token=alice_raw,
            bearer_key=bob_key,
        )
    assert exc.value.code == SESSION_EXPIRED  # 不回退：不是 bob 的身份


async def test_no_credentials_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    with pytest.raises(AuthenticationError) as exc:
        await resolve_principal(session, setup.repo, setup.rbac)
    assert exc.value.code == INVALID_CREDENTIAL


# ── AC ⑤：撤销立即拒绝 ──


async def test_revoked_key_rejected_immediately(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    svc = ApiCredentialService(setup.repo)
    created = await svc.create_key(session, user=setup.alice, name="to-revoke")
    await session.commit()
    assert await svc.revoke_key(
        session, credential_id=created.record.id, user_id=setup.alice.id
    )
    await session.commit()

    with pytest.raises(AuthenticationError) as exc:
        await resolve_api_key_principal(
            session, setup.repo, setup.rbac, created.full_key
        )
    assert exc.value.code == INVALID_CREDENTIAL


# ── AC ⑥：到期/禁用/删除立即拒绝（spike ==10 复跑切片）──


async def test_expired_key_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    key = await _create_key(setup, session, setup.alice)
    row = await setup.repo.get_api_credential_by_public_id(
        session, key.split(".")[1]
    )
    await session.execute(
        update(IamApiCredential)
        .where(IamApiCredential.id == row.id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await session.commit()
    with pytest.raises(AuthenticationError) as exc:
        await resolve_api_key_principal(session, setup.repo, setup.rbac, key)
    assert exc.value.code == INVALID_CREDENTIAL


async def test_disabled_user_blocks_session_and_all_keys(session: AsyncSession) -> None:
    """禁用后全部 Key 立即失效（验收 ⑥，03 §8.4）。"""
    setup = await build_auth_setup(session)
    raw = await _session_token(setup, session, setup.alice)
    key1 = await _create_key(setup, session, setup.alice, "k1")
    key2 = await _create_key(setup, session, setup.alice, "k2")

    await setup.repo.update_user(session, setup.alice.id, status="disabled")
    await session.commit()

    with pytest.raises(AuthenticationError) as exc:
        await resolve_session_principal(session, setup.repo, setup.rbac, raw)
    assert exc.value.code == USER_DISABLED
    for key in (key1, key2):
        with pytest.raises(AuthenticationError) as exc:
            await resolve_api_key_principal(session, setup.repo, setup.rbac, key)
        assert exc.value.code == USER_DISABLED


async def test_deleted_user_blocks_key(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    key = await _create_key(setup, session, setup.alice)
    await setup.repo.soft_delete_user(session, setup.alice.id, actor_id=setup.admin.id)
    await session.commit()
    with pytest.raises(AuthenticationError) as exc:
        await resolve_api_key_principal(session, setup.repo, setup.rbac, key)
    assert exc.value.code == USER_DISABLED


# ── AC ⑦：密码重置/改密不撤销 Key ──


async def test_password_reset_keeps_api_key_valid(session: AsyncSession) -> None:
    """重置只撤销登录 Session，不撤销 API Key（03 §8.3，验收 ⑦ 服务级切片）。"""
    setup = await build_auth_setup(session)
    key = await _create_key(setup, session, setup.alice)
    result = await setup.auth.reset_user_password(
        session,
        actor_user_id=setup.admin.id,
        actor_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()
    assert result.sessions_revoked >= 0
    principal = await resolve_api_key_principal(session, setup.repo, setup.rbac, key)
    assert principal.actor_user_id == setup.alice.id


# ── 其他：凭证不编码身份（03 §8.4）──


async def test_key_does_not_encode_identity(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    created = await _create_key(setup, session, setup.alice)
    public_id = created.split(".")[1]
    assert str(setup.alice.id) not in public_id
    assert "ov_user_" not in created
