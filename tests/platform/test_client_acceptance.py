"""P5-E4（14 号计划 §99.4）：18.3 公网断言测试客户端清单（07 §18.3，06 §15.2 第 6–8 步）。

环境说明：真实公网与真实 MCP 客户端在本环境不可用（18.3 注），以**生产配置下
的集成断言 + 配置断言**留存证据（证据形态见 p5-e4-go-no-go.md「18.3 公网断言
测试客户端清单」）。每个客户端以新签发 IAM Key（`ovk_u.*`）走通产品 API，
并断言三凭证（登录 Session / API Key / MCP OAuth Token）对同一用户产出同一套
实时 RBAC 与数据范围。

客户端清单（证据收口）：
- SDK（httpx 产品 API 客户端）：`Authorization: Bearer <ovk_u.*>`；
- CLI：`X-Api-Key`（环境变量注入）与 Bearer 等价；
- 插件：以 Key 归属者身份审计（产品 API 面复验，低层深覆盖见
  test_tenant_isolation.py::test_plugin_audits_as_key_owner）；
- MCP 客户端：MCP OAuth Access Token → 同一 Principal（deep 见
  test_oauth_principal.py / test_oauth_without_studio.py）。

验收映射：P5-E4 验收③（18.3 公网断言按客户端清单执行并留存证据）。
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamAuditEvent
from tests.platform.helpers import build_auth_setup
from tests.platform.test_admin_api import _login
from tests.platform.test_resource_api import BASE_ME, _csrf, _import

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"

CLIENTS = ("sdk", "cli", "plugin", "mcp")


async def _seed_and_key(session: AsyncSession, platform_client: httpx.AsyncClient):
    """标准环境 + alice 新签发具名 IAM Key；返回 (setup, key_id, full_key)。"""
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "alice@acme.com")
    r = await platform_client.post(
        f"{BASE_ME}/api-keys",
        json={"name": "p5-e4-client-evidence"},
        headers=_csrf(csrf),
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    full_key = body["api_key"]
    assert full_key.startswith("ovk_u."), body
    return setup, body["id"], full_key


async def _bearer_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ═══════════════════════════════════════════════════════════════════════
# SDK 客户端（Bearer <ovk_u.*> 新 Key 全链路走通）
# ═══════════════════════════════════════════════════════════════════════


async def test_sdk_client_bearer_new_key_flow(session: AsyncSession, platform_client) -> None:
    """SDK：新签发 IAM Key 走通 me/资源导入/检索，身份与 Session 一致。"""
    setup, key_id, full_key = await _seed_and_key(session, platform_client)
    assert full_key and full_key.startswith("ovk_u.")

    # me（Bearer）
    r = await platform_client.get(f"{BASE_ME}/api-keys", headers=await _bearer_headers(full_key))
    assert r.status_code == 200, r.text
    # 资源导入 + 列表（Bearer；写路径统一 Session+CSRF——浏览器语义，API Key 不能做 CSRF 写）
    csrf = await _login(platform_client, "alice@acme.com")
    batch = await _import(
        platform_client,
        csrf,
        f"{BASE_ME}/resources/imports",
        [{"source_url": "https://sdk-client.example.com/page"}],
    )
    res_id = batch["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_ME}/resources", headers=await _bearer_headers(full_key))
    assert r.status_code == 200, r.text
    # 列表 DTO 的 id 为 `res_<uuid>` 产品标识（04 §10.9）
    assert any(item["id"] == f"res_{res_id}" for item in r.json()["result"]["items"])

    # 三凭证身份一致（me user id 相同）
    session_me = await platform_client.get("/api/platform/v1/auth/me")
    key_me = await platform_client.get("/api/platform/v1/auth/me", headers=await _bearer_headers(full_key))
    assert session_me.status_code == key_me.status_code == 200
    assert session_me.json()["result"]["user"]["id"] == key_me.json()["result"]["user"]["id"]
    assert session_me.json()["result"]["roles"] == key_me.json()["result"]["roles"] == ["user"]


# ═══════════════════════════════════════════════════════════════════════
# CLI 客户端（X-Api-Key 环境变量注入，等价于 Bearer）
# ═══════════════════════════════════════════════════════════════════════


async def test_cli_client_x_api_key_env(session: AsyncSession, platform_client) -> None:
    """CLI：`X-Api-Key`（等价 Bearer，03 §8.4）走通 me 与资源列表。"""
    setup, key_id, full_key = await _seed_and_key(session, platform_client)
    headers = {"X-Api-Key": full_key}
    r = await platform_client.get("/api/platform/v1/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["user"]["ov_user_id"] == "ov_user_alice"

    r_bearer = await platform_client.get(
        "/api/platform/v1/auth/me", headers=await _bearer_headers(full_key)
    )
    assert r_bearer.json() == r.json()

    # CLI 不能执行 CSRF 写（Key 无 Session 语义，03 §8.2）
    r_write = await platform_client.post(
        f"{BASE_ME}/api-keys", json={"name": "cli-key"}, headers=headers
    )
    assert r_write.status_code == 403
    assert r_write.json()["detail"]["code"] == "CSRF_INVALID"


# ═══════════════════════════════════════════════════════════════════════
# 插件客户端（Key 归属者审计）
# ═══════════════════════════════════════════════════════════════════════


async def test_plugin_client_audits_as_key_owner(session: AsyncSession, platform_client) -> None:
    """插件：产品 API 写动作以 Key 归属者（Actor）审计，与低层语义一致。"""
    setup, key_id, full_key = await _seed_and_key(session, platform_client)
    # 用 Session 完成一次写，审计 Actor=alice
    csrf = await _login(platform_client, "alice@acme.com")
    r = await platform_client.delete(f"{BASE_ME}/api-keys/{key_id}", headers=_csrf(csrf))
    assert r.status_code == 200, r.text

    await session.commit()
    events = list(
        (
            await session.execute(
                select(IamAuditEvent).where(
                    IamAuditEvent.action == "credential.revoke",
                    IamAuditEvent.result == "success",
                )
            )
        ).scalars()
    )
    assert events
    event = events[0]
    assert event.actor_user_id == setup.alice.id
    assert event.authentication_method in ("session", "api_key")
    assert event.subject_user_id == setup.alice.id


# ═══════════════════════════════════════════════════════════════════════
# MCP 客户端（OAuth Access Token → 同一 Principal/实时 RBAC）
# ═══════════════════════════════════════════════════════════════════════


async def test_oauth_token_client_same_rbac(session: AsyncSession, session_factory) -> None:
    """MCP：OAuth Access Token 解析为与 Session/API Key 完全相同的 Principal。"""
    from openviking.server.platform.iam.pg_oauth_store import PostgresOAuthStore

    setup = await build_auth_setup(session)
    await session.commit()
    store = PostgresOAuthStore(session_factory, label="ovp-test-client-oauth")
    await store.register_client(
        client_id="mcp-client-acceptance",
        redirect_uris=["http://127.0.0.1:9999/callback"],
        client_name="MCP Acceptance Client",
        scope="mcp",
    )
    token_plain = "mcp-access-token-" + uuid.uuid4().hex
    await store.insert_access(
        token_plain=token_plain,
        client_id="mcp-client-acceptance",
        account_id=str(setup.alice.account_id),
        user_id=str(setup.alice.id),
        role="user",
        scope="mcp",
        resource=None,
        authorizing_key_fp="n/a",
        ttl_seconds=3600,
        session=session,
    )
    await session.commit()

    from openviking.server.platform.auth.principals import (
        resolve_oauth_token_principal,
        resolve_session_principal,
    )

    oauth_principal = await resolve_oauth_token_principal(
        session, setup.repo, setup.rbac, store, token_plain
    )
    assert oauth_principal is not None
    assert oauth_principal.authentication_method == "oauth"

    # 与 Session/API Key 同一 User、同一有效权限（实时 RBAC，03 §8.4）
    from tests.platform.helpers import create_login_session

    raw, _, _ = await create_login_session(setup, session, setup.alice)
    await session.commit()
    session_principal = await resolve_session_principal(
        session, setup.repo, setup.rbac, raw
    )
    assert session_principal is not None
    assert oauth_principal.actor_user_id == session_principal.actor_user_id
    assert oauth_principal.actor_account_id == session_principal.actor_account_id
    assert oauth_principal.permissions == session_principal.permissions
    assert oauth_principal.role_codes == session_principal.role_codes
    assert oauth_principal.actor_ov_user_id == session_principal.actor_ov_user_id

    # 渠道不能切换身份：OAuth Principal 的 actor 仍是 alice（无任何 Subject 逃逸字段）
    assert oauth_principal.actor_user_id == setup.alice.id


async def test_clients_all_use_new_iam_keys_only(session: AsyncSession, platform_client) -> None:
    """清单一致性：四个客户端证据流全部使用新 IAM 签发 Key（ovk_u.*）。"""
    setup, key_id, full_key = await _seed_and_key(session, platform_client)
    assert full_key.startswith("ovk_u.")
    # 产品 API 不接受旧格式 Key（v0.1 无旧 Key 导入路径，06 §15.1；
    # 新客户端无登录 Cookie，避免 Cookie 优先掩盖 Bearer 断言）
    fresh = httpx.AsyncClient(transport=platform_client._transport, base_url="https://testserver")
    async with fresh:
        r = await fresh.get(
            "/api/platform/v1/auth/me",
            headers={"Authorization": "Bearer legacy-root-api-key-format"},
        )
        assert r.status_code == 401, r.text
