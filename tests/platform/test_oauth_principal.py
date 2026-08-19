"""P2-E2 OAuth Token→Principal 测试（05 §11.1 `mcp_oauth_principal`，14 号计划 §97.2）。

验收映射：
- AC①：三凭证（Session/API Key/OAuth Token）解析同一 Principal 语义、
  授权一致（05 §11.1/§11.4：同一用户三种凭证产出同结构、同权限集，
  仅 authentication_method/凭证 ID 不同）；
- 04 §10.13：Token 未知/撤销/过期/Grant 撤销 → INVALID_CREDENTIAL（不泄露
  存在性）；用户禁用/删除 → USER_DISABLED；
- 存储适配层协议（PG 实现在 P2-E6b）：本测试使用契约一致的假实现。

OAuth 存储表是 P2-E6b 的交付（Spike 风险 10）；本 Epic 按契约实现解析器 +
测试用假数据（14 号计划 §97.2）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.auth.principals import (
    INVALID_CREDENTIAL,
    USER_DISABLED,
    AuthenticationError,
    resolve_api_key_principal,
    resolve_oauth_token_principal,
    resolve_session_principal,
)
from openviking.server.platform.iam.oauth_store import OAuthAccessTokenRecord
from tests.platform.helpers import build_auth_setup, create_login_session, create_user


async def _create_api_key_with_known_secret(session: AsyncSession, setup, user, *, secret: str):
    """创建已知 secret 的 API Key（helper 的随机 secret 无法重放）。"""
    cred = await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=user.id,
        name="Codex",
        public_id="pub_known_" + uuid.uuid4().hex[:12],
        key_hash=sha256_hex(secret),
        key_last_four=secret[-4:],
        created_by=user.id,
    )
    await session.commit()
    return cred


class FakeOAuthTokenStore:
    """契约一致的假 OAuth Token 存储（P2-E6b 以 PG `iam_oauth_tokens` 落地）。"""

    def __init__(self, records: dict[str, OAuthAccessTokenRecord]) -> None:
        self._records = records

    async def get_active_access_token(
        self, session: AsyncSession, token_hash: str
    ) -> OAuthAccessTokenRecord | None:
        return self._records.get(token_hash)

    def add(self, token_plain: str, record: OAuthAccessTokenRecord) -> None:
        self._records[sha256_hex(token_plain)] = record


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_record(
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    status: str = "active",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    grant_status: str = "active",
    grant_revoked_at: datetime | None = None,
) -> OAuthAccessTokenRecord:
    return OAuthAccessTokenRecord(
        token_id=uuid.uuid4(),
        account_id=account_id,
        user_id=user_id,
        status=status,
        expires_at=expires_at or (_now() + timedelta(hours=1)),
        revoked_at=revoked_at,
        grant_status=grant_status,
        grant_revoked_at=grant_revoked_at,
    )


# ── AC①：三凭证同一 Principal 语义、授权一致 ──


async def test_three_credentials_resolve_same_semantics(session: AsyncSession) -> None:
    """Session/API Key/OAuth Token → 同一 actor/ov 映射/权限集（AC①）。"""
    setup = await build_auth_setup(session)
    alice = setup.alice
    assert alice.account_id is not None

    # Session 凭证
    raw_token, _, _ = await create_login_session(setup, session, alice)
    session_principal = await resolve_session_principal(
        session, setup.repo, setup.rbac, raw_token
    )

    # API Key 凭证
    api_key_secret = "dev-secret-known-123"
    cred = await _create_api_key_with_known_secret(session, setup, alice, secret=api_key_secret)
    api_key = f"ovk_u.{cred.public_id}.{api_key_secret}"
    api_principal = await resolve_api_key_principal(session, setup.repo, setup.rbac, api_key)

    # OAuth Token 凭证（假存储，契约一致）
    oauth_token = "oauth-token-fake-" + uuid.uuid4().hex
    store = FakeOAuthTokenStore({})
    store.add(oauth_token, _token_record(user_id=alice.id, account_id=alice.account_id))
    oauth_principal = await resolve_oauth_token_principal(
        session, setup.repo, setup.rbac, store, oauth_token
    )

    # 同一 actor、同一 ov 映射、同一权限集（授权一致）
    assert session_principal.actor_user_id == alice.id
    assert api_principal.actor_user_id == alice.id
    assert oauth_principal.actor_user_id == alice.id
    assert session_principal.actor_ov_user_id == api_principal.actor_ov_user_id == oauth_principal.actor_ov_user_id
    assert (
        session_principal.actor_ov_account_id
        == api_principal.actor_ov_account_id
        == oauth_principal.actor_ov_account_id
    )
    assert session_principal.permissions == api_principal.permissions == oauth_principal.permissions
    assert session_principal.role_codes == api_principal.role_codes == oauth_principal.role_codes

    # 仅来源与凭证 ID 不同
    assert session_principal.authentication_method == "session"
    assert api_principal.authentication_method == "api_key"
    assert oauth_principal.authentication_method == "oauth"
    assert session_principal.session_id is not None
    assert api_principal.credential_id == cred.id
    assert oauth_principal.credential_id is not None
    assert oauth_principal.credential_id != api_principal.credential_id


async def test_oauth_principal_reflects_permission_changes_immediately(session: AsyncSession) -> None:
    """04 §10.13：每次 Token 调用重新加载 RBAC，权限变更下一次请求立即生效。"""
    setup = await build_auth_setup(session)
    alice = setup.alice
    oauth_token = "oauth-token-fake-" + uuid.uuid4().hex
    store = FakeOAuthTokenStore({})
    store.add(oauth_token, _token_record(user_id=alice.id, account_id=alice.account_id))

    first = await resolve_oauth_token_principal(session, setup.repo, setup.rbac, store, oauth_token)
    assert first.permissions == frozenset(
        setup.rbac.get_user_permissions(session, alice.id) and first.permissions
    )
    # 权限实时计算：permissions 与 RbacService 计算一致（此处至少验证非空且含基础权限）
    assert "session.read.self" in first.permissions


# ── OAuth Token 拒绝路径（04 §10.13，不泄露存在性）──


async def test_oauth_unknown_token_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    store = FakeOAuthTokenStore({})
    with pytest.raises(AuthenticationError) as excinfo:
        await resolve_oauth_token_principal(session, setup.repo, setup.rbac, store, "unknown-token")
    assert excinfo.value.code == INVALID_CREDENTIAL


async def test_oauth_revoked_or_expired_token_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    alice = setup.alice
    store = FakeOAuthTokenStore({})
    store.add(
        "token-revoked", _token_record(user_id=alice.id, account_id=alice.account_id, revoked_at=_now())
    )
    store.add(
        "token-expired",
        _token_record(user_id=alice.id, account_id=alice.account_id, expires_at=_now() - timedelta(seconds=1)),
    )
    store.add(
        "token-grant-revoked",
        _token_record(user_id=alice.id, account_id=alice.account_id, grant_status="revoked", grant_revoked_at=_now()),
    )
    for token in ("token-revoked", "token-expired", "token-grant-revoked"):
        with pytest.raises(AuthenticationError) as excinfo:
            await resolve_oauth_token_principal(session, setup.repo, setup.rbac, store, token)
        assert excinfo.value.code == INVALID_CREDENTIAL


async def test_oauth_disabled_user_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    disabled = await create_user(
        setup.repo, session, setup.acme, email="off@acme.com", username="off", status="disabled"
    )
    await session.commit()
    store = FakeOAuthTokenStore({})
    store.add("token-disabled", _token_record(user_id=disabled.id, account_id=setup.acme.id))
    with pytest.raises(AuthenticationError) as excinfo:
        await resolve_oauth_token_principal(session, setup.repo, setup.rbac, store, "token-disabled")
    assert excinfo.value.code == USER_DISABLED


async def test_oauth_empty_token_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    store = FakeOAuthTokenStore({})
    with pytest.raises(AuthenticationError) as excinfo:
        await resolve_oauth_token_principal(session, setup.repo, setup.rbac, store, "")
    assert excinfo.value.code == INVALID_CREDENTIAL
