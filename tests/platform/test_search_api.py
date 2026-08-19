"""P2-E5 Search 产品 API 集成测试（11 §69/§74.1，05 §12.5，14 号计划 §97.5）。

验收映射（AC）：
- ① find 不加载 Session、search 的 session_id 必须属当前 User 且未删除
  （11 §69.1/§77.2）；
- ② 白名单外字段严格拒绝不静默透传（11 §69.2/§98，05 §12.5）；
- ③ 结果不含 uri/score/level/query_plan/provenance/relations/category，
  Memory 结果只含类型/摘要/匹配原因；visibility 服务端分类（05 §12.5）；
- 固定检索根（11 §69.2）：User 私有根 + Account 共享根；
- tags/since/until 白名单字段生效（AND 关系、updated_at 范围）；
- 成员只读检索（11 §73/§74.1）：admin/platform scope，Actor/Subject 审计；
- 引擎不可用 → SEARCH_UNAVAILABLE（11 §75.2）。
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.session.backend import FakeKnowledgeItem
from tests.platform.helpers import build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE = "/api/platform/v1"
BASE_ADMIN = "/api/platform/v1/admin"
BASE_PLATFORM = "/api/platform/v1/platform"

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


def _seed_knowledge(platform_app) -> None:
    """标准检索数据：alice 私有 memory/resource + acme 共享 resource/skill +
    bob 私有 memory（跨 User 隔离验证）。"""
    engine = platform_app.state.iam_search_engine
    engine.seed(
        FakeKnowledgeItem(
            uri="viking://user/ov_user_alice/memories/pref1",
            context_type="memory",
            display_name="Alice 偏好",
            abstract="Alice 喜欢喝绿茶",
            match_reason="包含 Alice 偏好",
            tags=["kind=preference"],
            updated_at="2026-08-01T00:00:00+00:00",
            memory_type="preference",
            owner_ov_user="ov_user_alice",
        ),
        FakeKnowledgeItem(
            uri="viking://user/ov_user_alice/resources/notes.md",
            context_type="resource",
            display_name="Alice 笔记",
            abstract="产品调研笔记",
            match_reason="与检索词相关",
            tags=["kind=notes"],
            updated_at="2026-08-10T00:00:00+00:00",
            account_ov_id="ov_account_acme",
        ),
        FakeKnowledgeItem(
            uri="viking://resources/ov_account_acme/shared/guide.md",
            context_type="resource",
            display_name="共享指南",
            abstract="团队共享指南",
            match_reason="与检索词相关",
            tags=["kind=guide"],
            updated_at="2026-08-05T00:00:00+00:00",
            account_ov_id="ov_account_acme",
        ),
        FakeKnowledgeItem(
            uri="viking://agent/skills/ov_account_acme/shared/reader",
            context_type="skill",
            display_name="共享 Reader",
            abstract="共享读取技能",
            match_reason="与检索词相关",
            tags=[],
            updated_at="2026-08-02T00:00:00+00:00",
            account_ov_id="ov_account_acme",
        ),
        FakeKnowledgeItem(
            uri="viking://user/ov_user_bob/memories/pref9",
            context_type="memory",
            display_name="Bob 偏好",
            abstract="Bob 喜欢红茶",
            match_reason="包含 Bob 偏好",
            tags=[],
            updated_at="2026-08-03T00:00:00+00:00",
            memory_type="preference",
            owner_ov_user="ov_user_bob",
        ),
    )


# ── AC②：严格 DTO 白名单 ──


async def test_search_find_rejects_whitelist_out_fields(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    _seed_knowledge(platform_app)

    for bad_field in (
        {"target_uri": "viking://user/ov_user_bob/"},
        {"filter": {"op": "and"}},
        {"score_threshold": 0.5},
        {"level": 1},
        {"include_provenance": True},
        {"limit": 5},
        {"node_limit": 5},
        {"time_field": "created_at"},
        {"session_id": str(uuid.uuid4())},  # find 不接受 session_id
        {"unknown_field": "x"},
    ):
        resp = await platform_client.post(f"{BASE}/search/find", json={"query": "偏好", **bad_field}, headers=headers)
        assert resp.status_code == 422, (bad_field, resp.text)


# ── AC①：find 不加载 Session；search 校验 Session 归属 ──


async def test_find_does_not_load_session(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    _seed_knowledge(platform_app)

    resp = await platform_client.post(f"{BASE}/search/find", json={"query": "偏好"}, headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["result"]["items"]
    assert [i["display_name"] for i in items] == ["Alice 偏好"]

    # find 引擎追踪不含 session（AC①：find 不加载 Session）
    traces = platform_app.state.iam_search_engine.trace()
    assert all("session" not in t for t in traces)


async def test_search_requires_own_active_session(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    _seed_knowledge(platform_app)

    # 他人 Session / 不存在 / 随机 ID → SESSION_NOT_FOUND
    other_id = uuid.uuid4()
    for bad_id in (other_id, uuid.uuid4(), uuid.uuid4()):
        resp = await platform_client.post(
            f"{BASE}/search/search",
            json={"query": "偏好", "session_id": str(bad_id)},
            headers=headers,
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["code"] == "SESSION_NOT_FOUND"

    # 已删除的自己的 Session → 不可见（SESSION_NOT_FOUND）
    created = await _create_session(platform_client, headers, key="search-s1")
    await platform_client.delete(f"{BASE}/sessions/{created['id']}", headers=headers)
    resp = await platform_client.post(
        f"{BASE}/search/search",
        json={"query": "偏好", "session_id": str(created["id"])},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SESSION_NOT_FOUND"


async def test_search_with_own_session_ok(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    _seed_knowledge(platform_app)
    created = await _create_session(platform_client, headers, key="search-ok1")

    resp = await platform_client.post(
        f"{BASE}/search/search",
        json={"query": "偏好", "session_id": str(created["id"])},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert [i["display_name"] for i in resp.json()["result"]["items"]] == ["Alice 偏好"]
    traces = platform_app.state.iam_search_engine.trace()
    assert any(t.get("mode") == "search" and t.get("session") for t in traces)


# ── AC③：结果 DTO 脱敏 + visibility 服务端分类 ──


async def test_search_dto_sanitized_and_visibility_classified(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    _seed_knowledge(platform_app)

    resp = await platform_client.post(f"{BASE}/search/find", json={"query": "指南"}, headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["result"]["items"]
    shared = [i for i in items if i["display_name"] == "共享指南"]
    assert len(shared) == 1
    item = shared[0]
    assert item["visibility"] == "account_shared"
    # 脱敏：不含内部字段（AC③）
    assert "uri" not in item
    assert "score" not in item
    assert "level" not in item
    assert "query_plan" not in item
    assert "provenance" not in item
    assert "relations" not in item
    assert "category" not in item
    raw = str(resp.json())
    assert "viking://" not in raw
    assert "0.83" not in raw

    # Memory 结果只含类型/摘要/匹配原因（AC③）
    resp = await platform_client.post(f"{BASE}/search/find", json={"query": "偏好"}, headers=headers)
    mem = resp.json()["result"]["items"][0]
    assert set(mem.keys()) == {
        "context_type",
        "display_name",
        "abstract",
        "match_reason",
        "visibility",
        "memory_type",
    }
    assert mem["visibility"] == "user_private"
    assert mem["memory_type"] == "preference"


async def test_search_cross_user_memory_invisible(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    _seed_knowledge(platform_app)

    # Bob 的私有 Memory 不在 alice 的检索根内（AC⑧ 数据范围隔离）
    resp = await platform_client.post(f"{BASE}/search/find", json={"query": "红茶"}, headers=headers)
    assert resp.json()["result"]["items"] == []

    resp = await platform_client.post(f"{BASE}/search/find", json={"query": "偏好"}, headers=headers)
    names = [i["display_name"] for i in resp.json()["result"]["items"]]
    assert "Bob 偏好" not in names


# ── 白名单字段生效：context_type / tags(AND) / since / until ──


async def test_search_filters_context_type_tags_time(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    _seed_knowledge(platform_app)

    # context_type=resource 只返回 resource
    resp = await platform_client.post(
        f"{BASE}/search/find", json={"query": "指南", "context_type": "resource"}, headers=headers
    )
    items = resp.json()["result"]["items"]
    assert all(i["context_type"] == "resource" for i in items)
    assert any(i["display_name"] == "共享指南" for i in items)

    # 多个标签 AND 关系：任一不满足即排除
    resp = await platform_client.post(
        f"{BASE}/search/find",
        json={"query": "指南", "tags": ["kind=guide", "kind=notes"]},
        headers=headers,
    )
    assert resp.json()["result"]["items"] == []

    # since/until 固定映射 updated_at（05 §12.5）
    resp = await platform_client.post(
        f"{BASE}/search/find",
        json={"query": "偏好", "since": "2026-09-01T00:00:00+00:00"},
        headers=headers,
    )
    assert resp.json()["result"]["items"] == []
    resp = await platform_client.post(
        f"{BASE}/search/find",
        json={"query": "偏好", "until": "2026-07-31T00:00:00+00:00"},
        headers=headers,
    )
    assert resp.json()["result"]["items"] == []
    resp = await platform_client.post(
        f"{BASE}/search/find",
        json={"query": "偏好", "since": "2026-07-01T00:00:00+00:00", "until": "2026-08-02T00:00:00+00:00"},
        headers=headers,
    )
    # Alice 偏好 updated_at=2026-08-01，在范围内
    assert [i["display_name"] for i in resp.json()["result"]["items"]] == ["Alice 偏好"]

    # 非法时间范围 → INVALID_SEARCH_FILTER
    resp = await platform_client.post(
        f"{BASE}/search/find",
        json={"query": "x", "since": "not-a-date"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_TIME_RANGE"


# ── 成员只读检索（11 §73/§74.1）──


async def test_member_search_find_admin_and_platform(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    _seed_knowledge(platform_app)

    admin_headers = await _login_and_headers(platform_client, "admin@acme.com")
    resp = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/search/find",
        json={"query": "偏好"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["result"]["items"]
    assert [i["display_name"] for i in items] == ["Alice 偏好"]
    assert all(i["visibility"] == "user_private" for i in items)

    psa_headers = await _login_and_headers(platform_client, "psa@platform.local")
    resp = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/search/find",
        json={"query": "偏好"},
        headers=psa_headers,
    )
    assert resp.status_code == 200, resp.text
    assert [i["display_name"] for i in resp.json()["result"]["items"]] == ["Alice 偏好"]

    # 普通 User 无成员检索权限
    alice_headers = await _login_and_headers(platform_client, "alice@acme.com")
    resp = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/search/find", json={"query": "偏好"}, headers=alice_headers
    )
    assert resp.status_code == 403


async def test_member_search_cross_account_invisible(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    _seed_knowledge(platform_app)

    # PSA 固定 Account=acme 才能检索 acme 成员；alice 不在其他 Account 时统一 404
    psa_headers = await _login_and_headers(platform_client, "psa@platform.local")
    resp = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{uuid.uuid4()}/search/find",
        json={"query": "偏好"},
        headers=psa_headers,
    )
    assert resp.status_code == 404


async def test_search_engine_unavailable(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    # 移除引擎 → SEARCH_UNAVAILABLE（11 §75.2；不降级为跨权限文件遍历）
    platform_app.state.iam_search_service._engine = None
    resp = await platform_client.post(f"{BASE}/search/find", json={"query": "偏好"}, headers=headers)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "SEARCH_UNAVAILABLE"


# ── 工具 ──


async def _create_session(client: httpx.AsyncClient, headers: dict, client_name: str = "Codex", key: str = "sk") -> dict:
    resp = await client.post(
        f"{BASE}/sessions", json={"client_name": client_name, "idempotency_key": key}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]
