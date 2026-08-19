"""P2-E6b：MCP OAuth PostgreSQL 存储测试（04 §10.13，14 号计划 §97.7）。

覆盖协议接口（与 SQLite OAuthStore 同形状，SDK provider 可无缝换存）与
产品操作：
- 协议：register/get_client、pending 生命周期、auth code 单次消费、
  access/refresh 存取与撤销、gc_expired；
- P2-E2 OAuthTokenStore 协议：get_active_access_token（含 Grant 状态 join）；
- 产品：approve（Grant 建立/复用 + Auth Code + pending 一次性）、deny、
  list/revoke_grant（Token family 全撤）、revoke_user_oauth/account_oauth
  （禁用/删除期，04 §10.13）、client disabled 拒绝新授权；
- 刷新轮换与重放撤销（严格重放经 peek_refresh 放行 → family 撤销）详见
  test_oauth_refresh_rotation.py。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.errors import OAuthProtocolError
from openviking.server.platform.iam.oauth_store import OAuthAccessTokenRecord
from openviking.server.platform.iam.pg_oauth_store import (
    OAUTH_CLIENT_DISABLED,
    OAUTH_PENDING_NOT_FOUND,
    PostgresOAuthStore,
)
from openviking.server.platform.models import (
    IamAuditEvent,
    IamOAuthClient,
    IamOAuthGrant,
    IamOAuthPendingAuthorization,
    IamOAuthToken,
)

MCP_SCOPE = "mcp"


def _principal(user) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=None,
        user_status=user.status,
        authentication_method="session",
    )


async def _seed_identity(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """真实 Account/User（iam_oauth_grants FK 完整性，04 §10.13）。"""
    from openviking.server.platform.iam import PostgresIamRepository
    from tests.platform.helpers import create_account, create_user

    repo = PostgresIamRepository()
    account = await create_account(repo, session, "oauth-test")
    user = await create_user(
        repo, session, account, email="oauth@test.local", username="oauthuser"
    )
    await session.commit()
    return account.id, user.id


async def _store(
    session_factory: async_sessionmaker[AsyncSession],
) -> PostgresOAuthStore:
    return PostgresOAuthStore(session_factory, label="ovp-test-pg-oauth")


async def _seed_client(
    store: PostgresOAuthStore,
    client_id: str = "mcp-client-test",
    client_name: str = "Test MCP Client",
) -> dict:
    return await store.register_client(
        client_id=client_id,
        redirect_uris=["http://127.0.0.1:9999/callback"],
        client_name=client_name,
        scope=MCP_SCOPE,
    )


async def _seed_pending(
    store: PostgresOAuthStore,
    *,
    client_id: str = "mcp-client-test",
    display_code: str = "ABC123",
) -> str:
    return await store.create_pending_authorization(
        client_id=client_id,
        redirect_uri="http://127.0.0.1:9999/callback",
        redirect_uri_provided_explicitly=True,
        code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        scopes=[MCP_SCOPE],
        resource=None,
        state="csrf-state-xyz",
        display_code=display_code,
        ttl_seconds=600,
    )


async def _active_token_rows(session: AsyncSession) -> list[IamOAuthToken]:
    return list(
        (
            await session.execute(
                select(IamOAuthToken).where(IamOAuthToken.status == "active")
            )
        ).scalars()
    )


# ── DCR：Client 注册/读取（协议接口与 SQLite 同形状）──


async def test_register_and_get_client(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    record = await _seed_client(store, client_name="Codex")
    assert record["client_id"] == "mcp-client-test"
    assert record["client_name"] == "Codex"
    assert record["redirect_uris"] == ["http://127.0.0.1:9999/callback"]
    assert record["token_endpoint_auth_method"] == "none"
    assert record["grant_types"] == ["authorization_code", "refresh_token"]
    assert record["response_types"] == ["code"]
    assert record["status"] == "active"
    assert record["client_secret_hash"] is None  # 公开客户端不存 secret

    fetched = await store.get_client("mcp-client-test")
    assert fetched is not None
    assert fetched["client_id"] == "mcp-client-test"
    assert fetched["scope"] == MCP_SCOPE
    assert await store.get_client("unknown-client") is None


# ── Pending 生命周期 ──


async def test_pending_cycle_and_display_code_lookup(
    session_factory, session: AsyncSession
) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    pending_id = await _seed_pending(store, display_code="AbC-9Xy")

    record = await store.load_pending_authorization(pending_id)
    assert record is not None
    assert record["client_id"] == "mcp-client-test"
    assert record["redirect_uri"] == "http://127.0.0.1:9999/callback"
    assert record["scopes"] == [MCP_SCOPE]
    assert record["state"] == "csrf-state-xyz"
    assert record["verified"] is False

    # display code 不区分大小写（跨设备手输路径）
    by_code = await store.find_pending_by_display_code("abc-9xy")
    assert by_code is not None and by_code["pending_id"] == pending_id
    assert await store.find_pending_by_display_code("") is None
    assert await store.find_pending_by_display_code("ZZZ999") is None

    # mark verified 一次性
    assert await store.mark_pending_verified(
        pending_id=pending_id,
        account_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        role="USER",
        verified_key_fp="fp",
    )
    # 已验证的 pending 不再按 display code 命中（find 过滤 verified）
    assert await store.find_pending_by_display_code("abc-9xy") is None

    await store.delete_pending_authorization(pending_id)
    assert await store.load_pending_authorization(pending_id) is None


# ── Auth code 单次消费 ──


async def test_auth_code_one_shot_consume(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    account_id, uid = await _seed_identity(session)
    await store.insert_auth_code(
        code_plain="ovac_code1",
        client_id="mcp-client-test",
        redirect_uri="http://127.0.0.1:9999/callback",
        code_challenge="challenge",
        code_challenge_method="S256",
        scope=MCP_SCOPE,
        resource=None,
        account_id=str(account_id),
        user_id=str(uid),
        role="USER",
        authorizing_key_fp="",
        ttl_seconds=300,
    )
    peek = await store.peek_auth_code("ovac_code1")
    assert peek is not None and peek["user_id"] == str(uid)
    consumed = await store.consume_auth_code("ovac_code1")
    assert consumed is not None and consumed["client_id"] == "mcp-client-test"
    assert await store.consume_auth_code("ovac_code1") is None  # 单次
    assert await store.peek_auth_code("ovac_code1") is None
    assert await store.consume_auth_code("never-issued") is None

    # 协议插入同时建立 Grant（04 §10.13：user_id+client_id+scope）
    grant = (await session.execute(select(IamOAuthGrant))).scalars().one()
    assert grant.status == "active" and grant.scope == MCP_SCOPE


# ── Access token 存取与撤销 ──


async def test_access_token_load_and_revoke(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    account_id, uid = await _seed_identity(session)
    await store.insert_access(
        token_plain="ovat_access1",
        client_id="mcp-client-test",
        account_id=str(account_id),
        user_id=str(uid),
        role="USER",
        scope=MCP_SCOPE,
        resource=None,
        authorizing_key_fp="",
        ttl_seconds=3600,
    )
    loaded = await store.load_access("ovat_access1")
    assert loaded is not None
    assert loaded["account_id"] == str(account_id)
    assert loaded["user_id"] == str(uid)
    assert loaded["scope"] == MCP_SCOPE

    grant = (await session.execute(select(IamOAuthGrant))).scalars().one()
    assert grant.last_used_at is not None  # load_access 触摸最近使用时间

    assert await store.revoke_access("ovat_access1") is True
    assert await store.load_access("ovat_access1") is None
    assert await store.revoke_access("ovat_access1") is False  # 幂等


# ── OAuthTokenStore 协议：get_active_access_token（P2-E2 解析器输入）──


async def test_get_active_access_token_record(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    account_id, uid = await _seed_identity(session)
    await store.insert_access(
        token_plain="ovat_active1",
        client_id="mcp-client-test",
        account_id=str(account_id),
        user_id=str(uid),
        role="USER",
        scope=MCP_SCOPE,
        resource=None,
        authorizing_key_fp="",
        ttl_seconds=3600,
    )
    record = await store.get_active_access_token(session, sha256_hex("ovat_active1"))
    assert isinstance(record, OAuthAccessTokenRecord)
    assert record.user_id == uid
    assert record.status == "active"
    assert record.grant_status == "active"
    assert record.grant_revoked_at is None

    # 未知/类型不匹配 → None
    assert await store.get_active_access_token(session, sha256_hex("unknown")) is None

    # Grant 撤销后 join 出 revoked 状态（解析器统一映射 INVALID_CREDENTIAL）
    grant = (await session.execute(select(IamOAuthGrant))).scalars().one()
    await store.revoke_grant(session, grant_id=grant.id, user_id=uid, revoked_by=uid)
    record = await store.get_active_access_token(session, sha256_hex("ovat_active1"))
    assert record is not None and record.grant_status == "revoked"


# ── 产品：approve（Grant 建立/复用 + Auth Code + pending 一次性）──


async def test_approve_pending_creates_grant_and_code(
    session_factory, session: AsyncSession
) -> None:
    store = await _store(session_factory)
    await _seed_client(store, client_name="Codex")
    pending_id = await _seed_pending(store)
    account_id, uid = await _seed_identity(session)

    result = await store.approve_pending(
        session,
        pending_id=pending_id,
        user_id=uid,
        account_id=account_id,
        role="USER",
        auth_code="ovac_minted1",
        auth_code_ttl_seconds=300,
    )
    assert result["client_id"] == "mcp-client-test"
    assert result["client_name"] == "Codex"
    assert result["redirect_uri"] == "http://127.0.0.1:9999/callback"
    assert result["state"] == "csrf-state-xyz"
    assert result["scopes"] == [MCP_SCOPE]
    assert result["auth_code"] == "ovac_minted1"
    await session.commit()

    # pending 一次性删除 + Auth Code 可消费
    assert await store.load_pending_authorization(pending_id) is None
    assert (await store.consume_auth_code("ovac_minted1")) is not None

    grant = (await session.execute(select(IamOAuthGrant))).scalars().one()
    assert grant.user_id == uid and grant.status == "active" and grant.scope == MCP_SCOPE

    # 同一 (user, client, scope) 再次授权 → 复用同一 Grant（连接 ID 稳定）
    pending2 = await _seed_pending(store)
    result2 = await store.approve_pending(
        session,
        pending_id=pending2,
        user_id=uid,
        account_id=account_id,
        role="USER",
        auth_code="ovac_minted2",
        auth_code_ttl_seconds=300,
    )
    assert result2["grant_id"] == result["grant_id"]
    assert (await session.execute(select(IamOAuthGrant))).scalars().all() and len(
        (await session.execute(select(IamOAuthGrant))).scalars().all()
    ) == 1

    # 已处理/未知 pending 拒绝重复批准
    with pytest.raises(OAuthProtocolError) as exc:
        await store.approve_pending(
            session,
            pending_id=pending2,
            user_id=uid,
            account_id=uid,
            role="USER",
            auth_code="ovac_replay",
            auth_code_ttl_seconds=300,
        )
    assert exc.value.reason == OAUTH_PENDING_NOT_FOUND


async def test_approve_pending_revived_grant(session_factory, session: AsyncSession) -> None:
    """已撤销 Grant 重新授权 → 原地复活（revoked 清零），ID 不变。"""
    store = await _store(session_factory)
    await _seed_client(store)
    pending_id = await _seed_pending(store)
    account_id, uid = await _seed_identity(session)
    approved = await store.approve_pending(
        session,
        pending_id=pending_id,
        user_id=uid,
        account_id=account_id,
        role="USER",
        auth_code="ovac_g1",
    )
    await session.commit()
    await store.revoke_grant(session, grant_id=uuid.UUID(approved["grant_id"]), user_id=uid, revoked_by=uid)
    assert (await session.execute(select(IamOAuthGrant))).scalars().one().status == "revoked"

    pending2 = await _seed_pending(store)
    approved2 = await store.approve_pending(
        session,
        pending_id=pending2,
        user_id=uid,
        account_id=account_id,
        role="USER",
        auth_code="ovac_g2",
    )
    await session.commit()
    assert approved2["grant_id"] == approved["grant_id"]
    grant = (await session.execute(select(IamOAuthGrant))).scalars().one()
    assert grant.status == "active" and grant.revoked_at is None


async def test_approve_pending_client_disabled(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    pending_id = await _seed_pending(store)
    account_id, uid = await _seed_identity(session)
    await session.execute(
        update(IamOAuthClient)
        .where(IamOAuthClient.client_id == "mcp-client-test")
        .values(status="disabled")
    )
    await session.commit()

    with pytest.raises(OAuthProtocolError) as exc:
        await store.approve_pending(
            session,
            pending_id=pending_id,
            user_id=uid,
            account_id=uid,
            role="USER",
            auth_code="ovac_x",
        )
    assert exc.value.reason == OAUTH_CLIENT_DISABLED
    # 拒绝后 pending 保留（未被消费），客户端恢复后可重试
    assert await store.load_pending_authorization(pending_id) is not None


async def test_deny_pending_one_shot(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    pending_id = await _seed_pending(store)
    assert await store.deny_pending(session, pending_id) is True
    await session.commit()
    assert await store.deny_pending(session, pending_id) is False
    assert await store.load_pending_authorization(pending_id) is None


# ── 产品：pending info（公开安全元数据，06 §13.8）──


async def test_get_pending_info_public_fields(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store, client_name="Codex")
    pending_id = await _seed_pending(store)
    info = await store.get_pending_info(session, pending_id)
    assert info["client_id"] == "mcp-client-test"
    assert info["client_name"] == "Codex"
    assert info["redirect_uri_host"] == "127.0.0.1:9999"
    assert info["scopes"] == [MCP_SCOPE]
    assert "display_code" not in info  # 不泄露 display code
    # 完整 redirect_uri 只对授权端点可见（get_pending_info 属产品内部）
    assert info["redirect_uri"] == "http://127.0.0.1:9999/callback"

    with pytest.raises(OAuthProtocolError) as exc:
        await store.get_pending_info(session, "unknown-pending")
    assert exc.value.reason == OAUTH_PENDING_NOT_FOUND


# ── 产品：grants 列表与撤销 ──


async def test_list_and_revoke_grants(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store, client_name="Codex")
    account_id, uid = await _seed_identity(session)
    await store.insert_access(
        token_plain="ovat_for_revoke",
        client_id="mcp-client-test",
        account_id=str(account_id),
        user_id=str(uid),
        role="USER",
        scope=MCP_SCOPE,
        resource=None,
        authorizing_key_fp="",
        ttl_seconds=3600,
    )
    grants = await store.list_grants_for_user(session, uid)
    assert len(grants) == 1
    g = grants[0]
    assert g["client_name"] == "Codex"
    assert g["scope"] == MCP_SCOPE
    assert g["status"] == "active"
    assert g["created_at"] is not None

    grant_id = uuid.UUID(g["id"])
    result = await store.revoke_grant(session, grant_id=grant_id, user_id=uid, revoked_by=uid)
    assert result is not None and result["changed"] is True
    assert result["tokens_revoked"] >= 1
    await session.commit()
    # Token family 全部失效（04 §10.13 / 13 §83.1）；同事务共享 session，
    # 避免 store 内部会话与 fixture 会话互相锁行（touch last_used_at）。
    assert await _active_token_rows(session) == []
    assert await store.load_access("ovat_for_revoke", session=session) is None

    # 幂等：再次撤销 changed=False
    again = await store.revoke_grant(session, grant_id=grant_id, user_id=uid, revoked_by=uid)
    assert again is not None and again["changed"] is False

    # 非本人/不存在 → None（404 语义）
    other = uuid.uuid4()
    assert await store.revoke_grant(session, grant_id=grant_id, user_id=other, revoked_by=other) is None
    assert (
        await store.revoke_grant(
            session, grant_id=uuid.uuid4(), user_id=uid, revoked_by=uid
        )
        is None
    )


# ── 生命周期：禁用/删除期撤销（04 §10.13，恢复后不自动恢复）──


async def test_revoke_user_oauth_and_account_oauth(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    account_id, uid = await _seed_identity(session)
    await store.insert_access(
        token_plain="ovat_u1",
        client_id="mcp-client-test",
        account_id=str(account_id),
        user_id=str(uid),
        role="USER",
        scope=MCP_SCOPE,
        resource=None,
        authorizing_key_fp="",
        ttl_seconds=3600,
    )
    counts = await store.revoke_user_oauth(session, user_id=uid, revoked_by=uid)
    await session.commit()
    assert counts["grants_revoked"] == 1 and counts["tokens_revoked"] == 1
    grant = (await session.execute(select(IamOAuthGrant))).scalars().one()
    assert grant.status == "revoked" and grant.revoked_at is not None
    # 列表仍展示 revoked 记录（connections 页保留历史）
    assert (await store.list_grants_for_user(session, uid))[0]["status"] == "revoked"

    # 恢复后不自动恢复：再次授权才复活（test_approve_pending_revived_grant 覆盖）
    counts2 = await store.revoke_user_oauth(session, user_id=uid, revoked_by=uid)
    assert counts2 == {"grants_revoked": 0, "tokens_revoked": 0}

    # Account 级撤销（Account 内另一用户视角 token）
    aid = account_id
    await store.insert_access(
        token_plain="ovat_a1",
        client_id="mcp-client-test",
        account_id=str(aid),
        user_id=str(uid),
        role="USER",
        scope=MCP_SCOPE,
        resource=None,
        authorizing_key_fp="",
        ttl_seconds=3600,
    )
    ac = await store.revoke_account_oauth(session, account_id=aid, revoked_by=uid)
    assert ac["grants_revoked"] == 1 and ac["tokens_revoked"] == 1

    # 审计写入（04 §10.13：撤销必须写审计，脱敏元数据）
    events = list((await session.execute(select(IamAuditEvent))).scalars())
    actions = {e.action for e in events}
    assert {"oauth.user.revoke", "oauth.account.revoke"} <= actions


# ── Maintenance：gc_expired ──


async def test_gc_expired_cleans_up(session_factory, session: AsyncSession) -> None:
    store = await _store(session_factory)
    await _seed_client(store)
    account_id, uid = await _seed_identity(session)
    await store.insert_auth_code(
        code_plain="ovac_gc1",
        client_id="mcp-client-test",
        redirect_uri="http://127.0.0.1:9999/callback",
        code_challenge="c",
        code_challenge_method="S256",
        scope=MCP_SCOPE,
        resource=None,
        account_id=str(account_id),
        user_id=str(uid),
        role="USER",
        authorizing_key_fp="",
        ttl_seconds=300,
    )
    pending_id = await _seed_pending(store)

    # 过期 + 已消费清理（pending 过期由 gc 删除）
    await session.execute(
        update(IamOAuthToken)
        .where(IamOAuthToken.token_hash == sha256_hex("ovac_gc1"))
        .values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            status="consumed",
        )
    )
    await session.execute(
        update(IamOAuthPendingAuthorization)
        .where(IamOAuthPendingAuthorization.pending_id == pending_id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await session.commit()

    counts = await store.gc_expired()
    assert counts["codes_deleted"] == 1
    assert counts["pending_authorizations_deleted"] == 1
    assert counts["refresh_tokens_deleted"] == 0
    assert counts["access_tokens_deleted"] == 0


# ── 生命周期接入：禁用/删除期撤销全部 Grant/Token（04 §10.13，AC①）──


async def _grant_for_alice(
    store: PostgresOAuthStore, session: AsyncSession, setup, user
) -> tuple[IamOAuthGrant, IamOAuthToken]:
    """alice 完成一次授权（产品 approve 等价路径），返回 Grant + 活跃 token。"""
    await store.register_client(
        client_id="lifecycle-client",
        redirect_uris=["http://127.0.0.1:9999/callback"],
        client_name="Lifecycle Client",
        scope=MCP_SCOPE,
    )
    pending_id = await store.create_pending_authorization(
        client_id="lifecycle-client",
        redirect_uri="http://127.0.0.1:9999/callback",
        redirect_uri_provided_explicitly=True,
        code_challenge="challenge",
        scopes=[MCP_SCOPE],
        resource=None,
        state="s",
        display_code="LIFE01",
        ttl_seconds=600,
    )
    approved = await store.approve_pending(
        session,
        pending_id=pending_id,
        user_id=user.id,
        account_id=setup.acme.id,
        role="USER",
        auth_code="ovac_lifecycle",
        auth_code_ttl_seconds=300,
    )
    await session.commit()
    grant = (
        await session.execute(select(IamOAuthGrant).where(IamOAuthGrant.id == uuid.UUID(approved["grant_id"])))
    ).scalar_one()
    token = (
        await session.execute(
            select(IamOAuthToken).where(IamOAuthToken.token_hash == sha256_hex("ovac_lifecycle"))
        )
    ).scalar_one()
    return grant, token


async def test_user_disable_revokes_all_grants_and_tokens(
    session: AsyncSession, session_factory
) -> None:
    """禁用即撤销全部 Grant/Token（04 §10.13），恢复后不自动恢复。"""
    from openviking.server.platform.admin.service import AdminService
    from tests.platform.helpers import build_auth_setup

    setup = await build_auth_setup(session)
    store = await _store(session_factory)
    grant, token = await _grant_for_alice(store, session, setup, setup.alice)
    assert grant.status == "active" and token.status == "active"

    admin_service = AdminService(setup.repo, setup.rbac, setup.auth)
    result = await admin_service.set_user_status(
        session,
        actor=_principal(setup.admin),
        target_user_id=setup.alice.id,
        status="disabled",
    )
    await session.commit()
    assert result.changed is True
    assert result.oauth_grants_revoked == 1

    grant = (
        await session.execute(select(IamOAuthGrant).where(IamOAuthGrant.id == grant.id))
    ).scalar_one()
    assert grant.status == "revoked" and grant.revoked_at is not None
    token = (
        await session.execute(select(IamOAuthToken).where(IamOAuthToken.id == token.id))
    ).scalar_one()
    assert token.status == "revoked"
    # 恢复用户后不自动恢复（重新授权才复活，见 test_approve_pending_revived_grant）
    await admin_service.set_user_status(
        session,
        actor=_principal(setup.admin),
        target_user_id=setup.alice.id,
        status="active",
    )
    await session.commit()
    grant = (
        await session.execute(select(IamOAuthGrant).where(IamOAuthGrant.id == grant.id))
    ).scalar_one()
    assert grant.status == "revoked"


async def test_user_delete_revokes_all_grants_and_tokens(
    session: AsyncSession, session_factory
) -> None:
    """进入删除期即撤销全部 Grant/Token（04 §10.13，14 号计划 §97.7）。"""
    from openviking.server.platform.deletion.service import DeletionService
    from tests.platform.helpers import build_auth_setup

    setup = await build_auth_setup(session)
    store = await _store(session_factory)
    grant, token = await _grant_for_alice(store, session, setup, setup.alice)

    from openviking.server.platform.config import PlatformConfig
    from openviking.server.platform.registry.repository import RegistryRepository

    deletion = DeletionService(setup.repo, RegistryRepository(), config=PlatformConfig())
    await deletion.delete_user(
        session,
        actor=_principal(setup.admin),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()

    grant = (
        await session.execute(select(IamOAuthGrant).where(IamOAuthGrant.id == grant.id))
    ).scalar_one()
    token = (
        await session.execute(select(IamOAuthToken).where(IamOAuthToken.id == token.id))
    ).scalar_one()
    assert grant.status == "revoked"
    assert token.status == "revoked"
