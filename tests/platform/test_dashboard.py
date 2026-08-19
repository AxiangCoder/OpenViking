"""P2-E5 Dashboard 产品 API 集成测试（05 §12.5，14 号计划 §97.5）。

验收映射（AC⑨）：dashboard 仅返回受控聚合（个人内容、处理状态、最近
活动），不暴露 Queue/锁/模型/VectorDB 等底层状态。
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.registry.service import ContentRegistryService
from tests.platform.helpers import build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE = "/api/platform/v1"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _login(client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    resp = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["csrf_token"]


def _headers(csrf: str | None = None) -> dict:
    return {"x-csrf-token": csrf} if csrf else {}


async def _login_and_headers(client: httpx.AsyncClient, email: str) -> dict:
    csrf = await _login(client, email)
    return _headers(csrf)


async def _create_session(client: httpx.AsyncClient, headers: dict, key: str) -> dict:
    resp = await client.post(
        f"{BASE}/sessions", json={"client_name": "Codex", "idempotency_key": key}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def test_dashboard_controlled_aggregation(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")

    # 构造个人内容：2 个 Session + 1 次 Commit + 1 个私有 Resource 引用
    s1 = await _create_session(platform_client, headers, key="dash-1")
    s2 = await _create_session(platform_client, headers, key="dash-2")
    resp = await platform_client.post(
        f"{BASE}/sessions/{s1['id']}/messages",
        json={"role": "user", "content": "remember x", "idempotency_key": "dm1"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await platform_client.post(
        f"{BASE}/sessions/{s1['id']}/commit", json={"idempotency_key": "dc1"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    registry = ContentRegistryService()
    ref = await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="resource",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/resources/notes.md",
        canonical_name="notes",
        display_name="notes",
        idempotency_key="dash-res-1",
    )
    await registry.activate(session, ref.id, actor_user_id=setup.alice.id)
    await session.commit()

    resp = await platform_client.get(f"{BASE}/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    summary = result["summary"]
    assert summary["sessions"] == 2
    assert summary["messages"] == 1
    assert summary["resources_private"] == 1
    assert summary["sessions_in_recycle"] == 0
    # Commit 已创建但未轮询 Phase 2 → pending（服务端状态区分，AC⑥/AC⑨）
    assert summary["commits_by_phase2"]["pending"] == 1
    assert len(result["recent_activity"]) <= 5
    assert result["generated_at"] is not None

    # 软删的 Session 从 dashboard 隐藏（AC⑦ 与 AC⑨ 一致）
    await platform_client.delete(f"{BASE}/sessions/{s2['id']}", headers=headers)
    resp = await platform_client.get(f"{BASE}/dashboard", headers=headers)
    summary = resp.json()["result"]["summary"]
    assert summary["sessions"] == 1
    assert summary["sessions_in_recycle"] == 1


async def test_dashboard_no_internal_state(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    await _create_session(platform_client, headers, key="dash-ns")

    resp = await platform_client.get(f"{BASE}/dashboard", headers=headers)
    raw = str(resp.json())
    # AC⑨：不暴露 Queue/锁/模型/VectorDB/引擎等底层状态
    for forbidden in ("queue", "lock", "vector", "model", "engine", "viking://", "uri", "pending_tokens"):
        assert forbidden not in raw.lower()

    # 其他用户看不到 alice 的内容
    admin_headers = await _login_and_headers(platform_client, "admin@acme.com")
    resp = await platform_client.get(f"{BASE}/dashboard", headers=admin_headers)
    assert resp.json()["result"]["summary"]["sessions"] == 0
