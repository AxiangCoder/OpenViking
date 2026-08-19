"""P2-E6b：MCP OAuth 产品端点集成测试（05 §12.4，P3-E2 联调契约）。

契约对照（web-platform/src/features/oauth/oauth.ts、
web-platform/src/features/profile/connections.ts）：
- ① pending 响应字段 OAuthPendingInfo：client_id/client_name/redirect_uri_host/
  scopes/expires_in/data_access/impact；
- ② authorize 不带 decision → 返回待授权信息（先展示再决策）；
- ③ authorize approve → redirect_url（服务端登记回调 + 授权码），浏览器跳转；
- ④ grants DTO 字段 OauthGrant：id/client_id/client_name/scope/status/
  created_at/last_used_at；DELETE 幂等。
AC②：同意只接受登录 Session（API Key 不能代替浏览器批准）。
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.oauth_service import (
    OAUTH_AUTHORIZE_SESSION_REQUIRED,
    OAuthService,
)
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.errors import OAuthProtocolError
from openviking.server.platform.models import IamAuditEvent, IamOAuthGrant, IamOAuthToken
from tests.platform.helpers import AuthSetup, build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
OAUTH_PREFIX = "/api/platform/v1/integrations/mcp/oauth"
GRANTS = "/api/platform/v1/me/oauth-grants"
ALICE_PASSWORD = "Init-Pass-2026-Dev!"

REDIRECT_URI = "http://127.0.0.1:9999/callback"
CLIENT_ID = "web-client-test"


async def _seed(setup: AuthSetup, oauth_app) -> str:
    """注册 Client + 创建 pending；返回 pending_id。"""
    store = oauth_app.state.platform_oauth_store
    await store.register_client(
        client_id=CLIENT_ID,
        redirect_uris=[REDIRECT_URI],
        client_name="Web MCP Client",
        scope="mcp",
    )
    return await store.create_pending_authorization(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        redirect_uri_provided_explicitly=True,
        code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        scopes=["mcp"],
        resource=None,
        state="state-abc",
        display_code="VERIFY1",
        ttl_seconds=600,
    )


async def _login(client: httpx.AsyncClient, email: str = "alice@acme.com") -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": ALICE_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _seed_and_login(session: AsyncSession, client: httpx.AsyncClient, oauth_app):
    setup = await build_auth_setup(session)
    csrf = await _login(client)
    pending_id = await _seed(setup, oauth_app)
    return setup, csrf, pending_id


# ── AC①：pending 响应字段（OAuthPendingInfo 契约）──


async def test_pending_info_public_fields(session: AsyncSession, oauth_client, oauth_app) -> None:
    _, _, pending_id = await _seed_and_login(session, oauth_client, oauth_app)

    r = await oauth_client.get(f"{OAUTH_PREFIX}/pending/{pending_id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    result = r.json()["result"]
    # OAuthPendingInfo 契约字段
    assert result["client_id"] == CLIENT_ID
    assert result["client_name"] == "Web MCP Client"
    assert result["redirect_uri_host"] == "127.0.0.1:9999"
    assert result["scopes"] == ["mcp"]
    assert isinstance(result["expires_in"], int) and result["expires_in"] > 0
    assert result["data_access"]
    assert result["impact"]
    # 防仿冒（06 §13.8）：不返回 display code/完整 redirect URI/Code
    assert "display_code" not in result
    assert "redirect_uri" not in result
    assert "code" not in result

    # 未知/过期 → 404
    assert (await oauth_client.get(f"{OAUTH_PREFIX}/pending/unknown-pending")).status_code == 404


# ── AC②：authorize 不带 decision → 待授权信息（先展示再决策）──


async def test_authorize_preview_returns_pending_info(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    _, csrf, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    headers = {"X-CSRF-Token": csrf}

    r = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize", json={"pending_id": pending_id}, headers=headers
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert "redirect_url" not in result
    assert result["client_id"] == CLIENT_ID
    assert result["client_name"] == "Web MCP Client"
    assert result["redirect_uri_host"] == "127.0.0.1:9999"
    assert result["scopes"] == ["mcp"]

    # 跨设备：display code 预览（联调契约 #2）
    r2 = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize", json={"code": "verify1"}, headers=headers
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["result"]["client_name"] == "Web MCP Client"

    # 两者都缺 → 400
    r3 = await oauth_client.post(f"{OAUTH_PREFIX}/authorize", json={}, headers=headers)
    assert r3.status_code == 400
    assert r3.json()["detail"]["code"] == "OAUTH_PENDING_REQUIRED"


# ── AC③：authorize approve → redirect_url 跳转（服务端登记回调 + 授权码）──


async def test_authorize_approve_returns_redirect_url(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    _, csrf, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    headers = {"X-CSRF-Token": csrf}

    r = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "approve"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert "redirect_url" in result and result["redirect_url"].startswith(REDIRECT_URI)
    assert "code=ovac_" in result["redirect_url"]
    assert "state=state-abc" in result["redirect_url"]
    assert result["client_name"] == "Web MCP Client"

    # Grant 建立 + 授权码可交换（store 视角）
    grant = (await session.execute(select(IamOAuthGrant))).scalars().one()
    assert grant.status == "active" and grant.client_id == CLIENT_ID
    code = result["redirect_url"].split("code=")[1].split("&")[0]
    consumed = await oauth_app.state.platform_oauth_store.consume_auth_code(code)
    assert consumed is not None and consumed["client_id"] == CLIENT_ID
    # 单次消费：再次交换失败
    assert await oauth_app.state.platform_oauth_store.consume_auth_code(code) is None

    # 审计：oauth.authorize.approve（04 §10.13）
    events = list((await session.execute(select(IamAuditEvent))).scalars())
    assert any(e.action == "oauth.authorize.approve" for e in events)

    # 已处理 pending 重复 approve → 404（一次性）
    r2 = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "approve"},
        headers=headers,
    )
    assert r2.status_code == 404
    assert r2.json()["detail"]["code"] == "OAUTH_PENDING_NOT_FOUND"


async def test_authorize_approve_cross_device_code(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    _, csrf, _ = await _seed_and_login(session, oauth_client, oauth_app)
    headers = {"X-CSRF-Token": csrf}

    r = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"code": "VERIFY1", "decision": "approve"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert "code=ovac_" in r.json()["result"]["redirect_url"]


async def test_authorize_reject_deletes_pending(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    _, csrf, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    headers = {"X-CSRF-Token": csrf}

    r = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "reject"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert "redirect_url" not in r.json()["result"]

    # pending 已删除；再次拒绝 → 404
    r2 = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "reject"},
        headers=headers,
    )
    assert r2.status_code == 404
    events = list((await session.execute(select(IamAuditEvent))).scalars())
    assert any(e.action == "oauth.authorize.deny" for e in events)


# ── AC②：同意只接受登录 Session ──


async def test_authorize_rejects_api_key(session: AsyncSession, oauth_client, oauth_app) -> None:
    setup, _, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    from openviking.server.platform.auth.password import sha256_hex

    secret = "known-secret-" + "b" * 16
    cred = await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=setup.alice.id,
        name="Codex",
        public_id="pub_alice_key",
        key_hash=sha256_hex(secret),
        key_last_four=secret[-4:],
        created_by=setup.alice.id,
    )
    await session.commit()
    r = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "approve"},
        headers={"Authorization": f"Bearer ovk_u.{cred.public_id}.{secret}"},
    )
    # API Key 不能代替浏览器批准：CSRF 写门禁直接拒绝（03 §8.2/§8.4）
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"


async def test_authorize_session_required_service_level(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    """服务层显式门禁：authentication_method != session → 拒绝（AC② 纵深防御）。"""
    setup, _, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    service = OAuthService(
        store=oauth_app.state.platform_oauth_store,
        repo=oauth_app.state.iam_repository,
    )
    fake_principal = AuthenticatedUserPrincipal(
        actor_user_id=setup.alice.id,
        actor_account_id=setup.acme.id,
        actor_ov_user_id="ov_user_alice",
        actor_ov_account_id="ov_account_acme",
        user_status="active",
        authentication_method="api_key",
        session_id=None,
        credential_id=uuid.uuid4(),
        permissions=set(),
    )
    with pytest.raises(OAuthProtocolError) as exc:
        await service.approve(
            session,
            principal=fake_principal,
            pending_id=pending_id,
        )
    assert exc.value.reason == OAUTH_AUTHORIZE_SESSION_REQUIRED


async def test_authorize_missing_csrf_token(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    _, _, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    r = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "approve"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"


# ── AC④：grants DTO（OauthGrant 契约）与撤销 ──


async def test_list_oauth_grants_dto(session: AsyncSession, oauth_client, oauth_app) -> None:
    setup, csrf, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    headers = {"X-CSRF-Token": csrf}
    r = await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "approve"},
        headers=headers,
    )
    assert r.status_code == 200

    r2 = await oauth_client.get(GRANTS)
    assert r2.status_code == 200, r2.text
    result = r2.json()["result"]
    assert len(result) == 1
    grant = result[0]
    # OauthGrant 契约字段（P3-E2 connections.ts）
    assert set(grant) == {
        "id",
        "client_id",
        "client_name",
        "scope",
        "status",
        "created_at",
        "last_used_at",
    }
    assert grant["client_id"] == CLIENT_ID
    assert grant["client_name"] == "Web MCP Client"
    assert grant["scope"] == "mcp"
    assert grant["status"] == "active"
    assert grant["created_at"] is not None

    # 其他用户看不到（隔离）
    admin_key = await _login(oauth_client, "admin@acme.com")
    assert admin_key
    r3 = await oauth_client.get(GRANTS)
    assert r3.json()["result"] == []

    # 未登录 → 401
    import httpx as _httpx

    transport = _httpx.ASGITransport(app=oauth_app)
    async with _httpx.AsyncClient(
        transport=transport, base_url="https://testserver"
    ) as anon:
        r4 = await anon.get(GRANTS)
    assert r4.status_code == 401


async def test_revoke_oauth_grant_idempotent(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    _, csrf, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    headers = {"X-CSRF-Token": csrf}
    await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "approve"},
        headers=headers,
    )
    grant_id = (await session.execute(select(IamOAuthGrant))).scalars().one().id

    r = await oauth_client.delete(f"{GRANTS}/{grant_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["result"] == {"status": "ok"}

    # Token family 全部撤销（store 断言：无 active token）
    rows = list((await session.execute(select(IamOAuthToken))).scalars())
    assert rows and all(t.status != "active" for t in rows)

    # 列表显示 revoked
    grants = (await oauth_client.get(GRANTS)).json()["result"]
    assert grants[0]["status"] == "revoked"

    # 幂等：重复撤销仍 200
    r2 = await oauth_client.delete(f"{GRANTS}/{grant_id}", headers=headers)
    assert r2.status_code == 200

    # 审计：oauth.grant.revoke（04 §10.13）
    events = list((await session.execute(select(IamAuditEvent))).scalars())
    assert any(e.action == "oauth.grant.revoke" for e in events)


async def test_revoke_oauth_grant_not_owner_404(
    session: AsyncSession, oauth_client, oauth_app
) -> None:
    _, csrf, pending_id = await _seed_and_login(session, oauth_client, oauth_app)
    headers = {"X-CSRF-Token": csrf}
    await oauth_client.post(
        f"{OAUTH_PREFIX}/authorize",
        json={"pending_id": pending_id, "decision": "approve"},
        headers=headers,
    )
    grant_id = (await session.execute(select(IamOAuthGrant))).scalars().one().id

    # admin（非属主）撤销 alice 的 Grant → 404（05 §12.4：/me/* 只属主）
    admin_csrf = await _login(oauth_client, "admin@acme.com")
    r = await oauth_client.delete(
        f"{GRANTS}/{grant_id}", headers={"X-CSRF-Token": admin_csrf}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "OAUTH_GRANT_NOT_FOUND"

    # 非法 UUID → 404（使用当前登录的 admin 会话与 CSRF）
    r2 = await oauth_client.delete(
        f"{GRANTS}/not-a-uuid", headers={"X-CSRF-Token": admin_csrf}
    )
    assert r2.status_code == 404
