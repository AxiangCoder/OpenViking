"""P2-E6b：MCP OAuth Refresh 轮换与 family 重放撤销（04 §10.13，AC①）。

经真实 `OpenVikingOAuthProvider`（mcp.server.auth SDK 协议适配器）+ PG 存储
验证：
- ① Refresh 强制轮换：一次 refresh 交换后旧 token 不可再用，新 token
  继承同一 family（token_family_id）并记录父子轮换关系（parent_token_id）；
- ② 严格重放（已消费 Refresh 再次使用）：peek_refresh 放行 consumed 行 →
  SDK 进入 exchange_refresh_token → 检测重放 → 撤销用户全部 Token family
  + 写审计（oauth.token.replay_revoke）；
- ③ Grant 撤销后其全部 Token（含活跃 family）立即失效；
- ④ 授权码单次交换；交换后 mint 的 token pair 归属同一 Grant。
"""

from __future__ import annotations

import urllib.parse

import pytest
from mcp.server.auth.provider import AuthorizationParams, TokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.oauth.otp import hash_secret
from openviking.server.oauth.provider import OpenVikingOAuthProvider, OVAuthorizationCode
from openviking.server.platform.iam.pg_oauth_store import PostgresOAuthStore
from openviking.server.platform.models import IamAuditEvent, IamOAuthToken

MCP_SCOPE = "mcp"


async def _setup(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    """store + provider + client + 真实 Account/User（Grant FK 完整性）。"""
    from openviking.server.platform.iam import PostgresIamRepository
    from tests.platform.helpers import create_account, create_user

    repo = PostgresIamRepository()
    account = await create_account(repo, session, "rot-oauth")
    user = await create_user(
        repo, session, account, email="rot@test.local", username="rotuser"
    )
    await session.commit()
    store = PostgresOAuthStore(session_factory, label="ovp-test-pg-oauth")
    provider = OpenVikingOAuthProvider(
        store=store,
        issuer="https://testserver",
        access_token_ttl_seconds=3600,
        refresh_token_ttl_seconds=86400,
        auth_code_ttl_seconds=300,
    )
    await store.register_client(
        client_id="rotation-client",
        redirect_uris=["http://127.0.0.1:9999/callback"],
        client_name="Rotation Client",
        scope=MCP_SCOPE,
    )
    return store, provider, account.id, user.id


async def _mint_code_and_exchange(store, provider, *, user_id, account_id):
    """走 provider.authorize + 产品 approve 路径 mint auth code，再交换 token pair。"""
    client_info = await provider.get_client("rotation-client")
    assert client_info is not None
    authorize_url = await provider.authorize(
        client_info,
        AuthorizationParams(
            redirect_uri="http://127.0.0.1:9999/callback",
            redirect_uri_provided_explicitly=True,
            scopes=[MCP_SCOPE],
            code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            code_challenge_method="S256",
            state="rotation-state",
        ),
    )
    assert "pending=" in authorize_url

    pending_id = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url).query)["pending"][0]

    async with store._session_factory() as session:  # noqa: SLF001 - 测试直接访问工厂
        approved = await store.approve_pending(
            session,
            pending_id=pending_id,
            user_id=user_id,
            account_id=account_id,
            role="USER",
            auth_code=provider.mint_authorization_code(),
            auth_code_ttl_seconds=provider.code_ttl_seconds,
        )
        await session.commit()

    code = approved["auth_code"]
    loaded = await provider.load_authorization_code(client_info, code)
    assert isinstance(loaded, OVAuthorizationCode)
    tokens = await provider.exchange_authorization_code(client_info, loaded)
    return tokens


async def test_refresh_rotation_inherits_family(session_factory, session: AsyncSession) -> None:
    store, provider, account_id, user_id = await _setup(session_factory, session)
    tokens = await _mint_code_and_exchange(store, provider, user_id=user_id, account_id=account_id)

    # 首次交换：access + refresh 同 Grant
    rows = (await session.execute(select(IamOAuthToken))).scalars().all()
    refresh1 = next(t for t in rows if t.token_type == "refresh")
    access1 = next(t for t in rows if t.token_type == "access")
    assert refresh1.status == "active" and access1.status == "active"
    assert refresh1.grant_id == access1.grant_id

    # 轮换：消费旧 refresh，签发新 refresh（同一 family + parent 链）
    loaded1 = await provider.load_refresh_token(
        (await provider.get_client("rotation-client")), tokens.refresh_token
    )
    new_tokens = await provider.exchange_refresh_token(
        (await provider.get_client("rotation-client")), loaded1, [MCP_SCOPE]
    )
    rows = (await session.execute(select(IamOAuthToken))).scalars().all()
    refresh2 = next(t for t in rows if t.token_type == "refresh" and t.token_hash == hash_secret(new_tokens.refresh_token))
    assert refresh2.token_family_id == refresh1.token_family_id
    assert refresh2.parent_token_id == refresh1.id
    # 旧 refresh 已被消费（轮换强制）；跨会话行重新加载避免 identity map 缓存
    consumed1 = (
        await session.execute(
            select(IamOAuthToken)
            .where(IamOAuthToken.id == refresh1.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert consumed1.status == "consumed" and consumed1.consumed_at is not None

    # 新 pair 可用（load_access）
    new_access = await store.load_access(new_tokens.access_token)
    assert new_access is not None and new_access["scope"] == MCP_SCOPE


async def test_refresh_replay_revokes_family(session_factory, session: AsyncSession) -> None:
    """严格重放：已消费 Refresh 再次交换 → family 全量撤销 + 审计。"""
    store, provider, account_id, user_id = await _setup(session_factory, session)
    tokens = await _mint_code_and_exchange(store, provider, user_id=user_id, account_id=account_id)
    client = await provider.get_client("rotation-client")

    # 第一次轮换成功
    loaded1 = await provider.load_refresh_token(client, tokens.refresh_token)
    rotated = await provider.exchange_refresh_token(client, loaded1, [MCP_SCOPE])
    assert rotated.access_token != tokens.access_token

    # 严格重放旧 refresh（RFC 9700 §4.14）：invalid_grant + family 撤销
    replayed = await provider.load_refresh_token(client, tokens.refresh_token)
    assert replayed is not None  # peek_refresh 放行 consumed 行（设计修正点）

    with pytest.raises(TokenError) as exc:
        await provider.exchange_refresh_token(client, replayed, [MCP_SCOPE])
    assert exc.value.error == "invalid_grant"

    # 撤销后：用户全部 token 非 active（family 全撤）
    rows = (await session.execute(select(IamOAuthToken))).scalars().all()
    assert rows and all(t.status != "active" for t in rows)
    assert await store.load_access(rotated.access_token) is None
    # 被撤销（非 consumed）的 refresh 不再被 peek 放行
    revoked_refresh = next(
        t for t in rows if t.token_hash == hash_secret(rotated.refresh_token)
    )
    assert revoked_refresh.status == "revoked"
    assert not await store.is_refresh_known_but_consumed(rotated.refresh_token)

    # 审计：重放撤销写入 oauth.token.replay_revoke（04 §10.13）
    events = list((await session.execute(select(IamAuditEvent))).scalars())
    assert any(e.action == "oauth.token.replay_revoke" for e in events)


async def test_grant_revoke_kills_active_family(session_factory, session: AsyncSession) -> None:
    store, provider, account_id, user_id = await _setup(session_factory, session)
    tokens = await _mint_code_and_exchange(store, provider, user_id=user_id, account_id=account_id)
    client = await provider.get_client("rotation-client")

    # 轮换一次，形成活跃 family 链
    loaded = await provider.load_refresh_token(client, tokens.refresh_token)
    rotated = await provider.exchange_refresh_token(client, loaded, [MCP_SCOPE])
    assert await store.load_access(rotated.access_token) is not None

    async with store._session_factory() as s:
        grant_id = (
            await s.execute(select(IamOAuthToken.grant_id).where(IamOAuthToken.token_type == "refresh").limit(1))
        ).scalar_one()
        result = await store.revoke_grant(s, grant_id=grant_id, user_id=user_id, revoked_by=user_id)
        await s.commit()
    assert result is not None and result["changed"] is True

    # Grant 撤销 → 全部 Token 失效（13 §83.1：Token family 全部失效）
    assert await store.load_access(rotated.access_token) is None
    assert await provider.load_refresh_token(client, rotated.refresh_token) is None
    assert await store.peek_refresh(rotated.refresh_token) is None
