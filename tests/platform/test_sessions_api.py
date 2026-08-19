"""P2-E5 Session 产品 API 集成测试（11 §70–§72/§74.2，14 号计划 §97.5）。

验收映射（AC）：
- ④ 创建/追加/Commit 幂等、消息顺序稳定（11 §70.8）；
- ⑤ Retention 服务端统一、客户端更低预算被拒（11 §71.1）；
- ⑥ memory-impact 区分 pending/running/completed(有/无操作)/failed，
  不返回 Archive/Memory URI（11 §71.3）；
- ⑦ 软删立即隐藏、写入返回 SESSION_DELETED、恢复不回滚 Memory、
  30 天后 Worker 幂等物理删除（11 §72）；
- ⑧ 跨 User/Account 访问统一不可见语义 SESSION_NOT_FOUND/404（11 §75.2）；
- 集成客户端（User API Key）写链路（11 §70.6）；成员只读视图
  Actor/Subject 同时记录（11 §73）。
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.deletion.worker import PurgeWorker
from openviking.server.platform.session.purge import SessionPurgeHandler
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


async def _create_session(client: httpx.AsyncClient, headers: dict, client_name: str = "Codex", key: str | None = "create-key-1") -> dict:
    body = {"client_name": client_name}
    if key is not None:
        body["idempotency_key"] = key
    resp = await client.post(f"{BASE}/sessions", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def _append(client: httpx.AsyncClient, headers: dict, session_id: str, role: str, content: str, key: str | None = None) -> httpx.Response:
    body = {"role": role, "content": content}
    if key is not None:
        body["idempotency_key"] = key
    return await client.post(f"{BASE}/sessions/{session_id}/messages", json=body, headers=headers)


# ── AC④：创建/追加/Commit 幂等、消息顺序稳定 ──


async def test_create_session_idempotent_by_key(platform_client: httpx.AsyncClient, session: AsyncSession) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")

    first = await _create_session(platform_client, headers, key="create-dup-1")
    second = await _create_session(platform_client, headers, key="create-dup-1")
    assert first["id"] == second["id"]
    assert first["created"] is True
    assert second["created"] is False

    # 无幂等键 → 每次新建
    third = await _create_session(platform_client, headers, key=None)
    assert third["id"] != first["id"]
    assert third["created"] is True


async def test_append_idempotent_and_order_stable(platform_client: httpx.AsyncClient, session: AsyncSession) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="append-1")

    r1 = await _append(platform_client, headers, created["id"], "user", "hello", key="msg-1")
    assert r1.status_code == 200, r1.text
    assert r1.json()["result"]["sequence"] == 1

    r2 = await _append(platform_client, headers, created["id"], "assistant", "hi there", key="msg-2")
    assert r2.json()["result"]["sequence"] == 2

    # 幂等重放：同 key 返回原写入结果，不重复追加
    r3 = await _append(platform_client, headers, created["id"], "user", "hello", key="msg-1")
    assert r3.json()["result"]["duplicate"] is True
    assert r3.json()["result"]["message_id"] == r1.json()["result"]["message_id"]
    assert r3.json()["result"]["message_count"] == 2

    # 同 key 不同内容 → SESSION_WRITE_CONFLICT
    r4 = await _append(platform_client, headers, created["id"], "user", "different", key="msg-1")
    assert r4.status_code == 409
    assert r4.json()["detail"]["code"] == "SESSION_WRITE_CONFLICT"

    # 顺序稳定（AC④：服务端序列号保持顺序）
    resp = await platform_client.get(f"{BASE}/sessions/{created['id']}/messages", headers=headers)
    items = resp.json()["result"]["items"]
    assert [m["sequence"] for m in items] == [1, 2]
    assert [m["role"] for m in items] == ["user", "assistant"]
    assert items[0]["content"] == "hello"


async def test_commit_idempotent_by_key(platform_client: httpx.AsyncClient, session: AsyncSession) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="commit-1")
    await _append(platform_client, headers, created["id"], "user", "remember tea", key="cm1")

    r1 = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "commit-key-1"}, headers=headers
    )
    assert r1.status_code == 200, r1.text
    first = r1.json()["result"]
    assert first["created"] is True
    assert first["commit_number"] == 1
    # Phase 2 异步：Commit 响应为 pending（11 §71.3，AC⑥）
    assert first["phase2_status"] == "pending"

    # 重放同 key：返回原 Commit，不重复触发（AC④，11 §70.8）
    r2 = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "commit-key-1"}, headers=headers
    )
    second = r2.json()["result"]
    assert second["created"] is False
    assert second["commit_id"] == first["commit_id"]
    assert second["commit_number"] == 1

    # 新 key → 新 Commit（追加消息后）
    await _append(platform_client, headers, created["id"], "user", "remember milk", key="cm2")
    r3 = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "commit-key-2"}, headers=headers
    )
    assert r3.json()["result"]["commit_number"] == 2


async def test_messages_survive_commit_with_stable_order(platform_client: httpx.AsyncClient, session: AsyncSession) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="history-1")
    for i, (role, text) in enumerate(
        [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")], start=1
    ):
        resp = await _append(platform_client, headers, created["id"], role, text, key=f"h-{i}")
        assert resp.status_code == 200, resp.text

    resp = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "hk-1"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    await _append(platform_client, headers, created["id"], "user", "q3", key="h-5")

    # 11 §70.4：Archive 在前、当前消息在后、按 Message ID 去重
    resp = await platform_client.get(f"{BASE}/sessions/{created['id']}/messages", headers=headers)
    items = resp.json()["result"]["items"]
    assert [m["sequence"] for m in items] == [1, 2, 3, 4, 5]
    assert items[-1]["content"] == "q3"


# ── AC⑤：Retention 服务端统一、客户端更低预算被拒 ──


async def test_commit_rejects_client_retention_budget(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="ret-1")
    await _append(platform_client, headers, created["id"], "user", "remember x", key="r1")

    for bad_field in (
        {"keep_recent_turn_count": 1},
        {"retained_message_token_budget": 6000},
        {"min_raw_tail_steps": 0},
        {"retention_mode": "turn_budget"},
        {"keep_recent_count": 10},
    ):
        resp = await platform_client.post(
            f"{BASE}/sessions/{created['id']}/commit", json=bad_field, headers=headers
        )
        assert resp.status_code == 422, (bad_field, resp.text)

    # 服务端统一执行 3 Turn/12000 Token/至少 1 个最新 Assistant Step
    for i in range(1, 5):
        await _append(platform_client, headers, created["id"], "user", f"remember v{i}", key=f"r{i+1}")
        await _append(platform_client, headers, created["id"], "assistant", f"answer v{i}", key=f"ra{i}")
    resp = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "ret-c1"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    # Retention 作用于 Live Context：归档后后端当前上下文保留 ≤3 个 user Turn
    # 且至少 1 个最新 Assistant Step；完整历史仍可读（Archive + 当前，11 §70.4）
    ov_id = await _ov_session_id(session, created["id"])
    key = ("ov_account_acme", "ov_user_alice", ov_id)
    live = platform_app.state.iam_session_backend._sessions[key].messages
    live_users = sum(1 for m in live if m["role"] == "user")
    live_assistants = sum(1 for m in live if m["role"] == "assistant")
    assert live_users <= 3
    assert live_assistants >= 1
    history = await platform_client.get(f"{BASE}/sessions/{created['id']}/messages", headers=headers)
    assert len(history.json()["result"]["items"]) >= 9


# ── AC⑥：memory-impact 状态区分与脱敏 ──


async def _ov_session_id(session: AsyncSession, ref_id: str) -> str:
    from sqlalchemy import text

    return (
        await session.execute(
            text("SELECT ov_session_id FROM platform_session_refs WHERE id = :id").bindparams(
                id=uuid.UUID(ref_id)
            )
        )
    ).scalar_one()


async def test_memory_impact_phase2_states_and_sanitization(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="impact-1")
    # ≥4 个 user Turn：超过 Retention（3 Turn）的消息在 Commit 时进入 Archive，
    # Fake 后端从已归档消息提取 Memory 操作（与真实语义一致）
    for i, text in enumerate(
        ["remember tea", "no memory op", "remember milk", "update plan", "forget sugar"], start=1
    ):
        await _append(platform_client, headers, created["id"], "user", text, key=f"im{i}")

    resp = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "ic1"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    backend = platform_app.state.iam_session_backend
    ref = created["id"]
    ov_id = await _ov_session_id(session, ref)

    # running：注入 stuck 模式后再轮询
    backend.set_phase2_mode(
        account_ov_id="ov_account_acme", user_ov_id="ov_user_alice", ov_session_id=ov_id, mode="stuck"
    )
    impact = await platform_client.get(f"{BASE}/sessions/{ref}/memory-impact", headers=headers)
    item = impact.json()["result"]["items"][0]
    assert item["phase2_status"] == "running"

    # completed（有操作）：切换 immediate 并轮询
    backend.set_phase2_mode(
        account_ov_id="ov_account_acme", user_ov_id="ov_user_alice", ov_session_id=ov_id, mode="immediate"
    )
    impact = await platform_client.get(f"{BASE}/sessions/{ref}/memory-impact", headers=headers)
    item = impact.json()["result"]["items"][0]
    assert item["phase2_status"] == "completed"
    assert item["has_operations"] is True
    assert impact.json()["result"]["totals"]["added"] == 1
    diff = item["diffs"][0]
    assert diff["action"] == "add"
    assert diff["after"] == "tea"
    # 不返回 Archive/Memory URI（AC⑥）
    raw = str(impact.json())
    assert "viking://" not in raw
    assert "uri" not in item
    assert "task" not in item
    assert "archive" not in item

    # completed（无操作）：无 Memory 指令的消息 → 空差异
    created2 = await _create_session(platform_client, headers, key="impact-2")
    await _append(platform_client, headers, created2["id"], "user", "plain message", key="im3")
    await platform_client.post(
        f"{BASE}/sessions/{created2['id']}/commit", json={"idempotency_key": "ic2"}, headers=headers
    )
    impact2 = await platform_client.get(f"{BASE}/sessions/{created2['id']}/memory-impact", headers=headers)
    item2 = impact2.json()["result"]["items"][0]
    assert item2["phase2_status"] == "completed"
    assert item2["has_operations"] is False
    assert impact2.json()["result"]["totals"] == {"added": 0, "updated": 0, "deleted": 0}

    # failed：注入 failed 模式
    created3 = await _create_session(platform_client, headers, key="impact-3")
    await _append(platform_client, headers, created3["id"], "user", "remember x", key="im4")
    await platform_client.post(
        f"{BASE}/sessions/{created3['id']}/commit", json={"idempotency_key": "ic3"}, headers=headers
    )
    ov_id3 = await _ov_session_id(session, created3["id"])
    backend.set_phase2_mode(
        account_ov_id="ov_account_acme", user_ov_id="ov_user_alice", ov_session_id=ov_id3, mode="failed"
    )
    impact3 = await platform_client.get(f"{BASE}/sessions/{created3['id']}/memory-impact", headers=headers)
    item3 = impact3.json()["result"]["items"][0]
    assert item3["phase2_status"] == "failed"
    assert item3["phase2_error"] == "memory_extraction_failed"


# ── AC⑦：软删/恢复/Worker 物理删除 ──


async def test_soft_delete_hides_and_blocks_writes(platform_client: httpx.AsyncClient, session: AsyncSession) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="del-1")
    await _append(platform_client, headers, created["id"], "user", "remember x", key="d1")

    resp = await platform_client.delete(f"{BASE}/sessions/{created['id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["result"]["deletion_job_id"]
    assert resp.json()["result"]["restore_until"] is not None

    # 立即从列表隐藏
    rows = (await platform_client.get(f"{BASE}/sessions", headers=headers)).json()["result"]["items"]
    assert all(r["id"] != created["id"] for r in rows)

    # 详情 → SESSION_NOT_FOUND（统一不可见语义）
    detail = await platform_client.get(f"{BASE}/sessions/{created['id']}", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["detail"]["code"] == "SESSION_NOT_FOUND"

    # 写入 → SESSION_DELETED（AC⑦：删除期间拒绝写入）
    append = await _append(platform_client, headers, created["id"], "user", "blocked", key="d2")
    assert append.status_code == 409
    assert append.json()["detail"]["code"] == "SESSION_DELETED"
    commit = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={}, headers=headers
    )
    assert commit.status_code == 409
    assert commit.json()["detail"]["code"] == "SESSION_DELETED"

    # 重复删除幂等返回同一 job
    again = await platform_client.delete(f"{BASE}/sessions/{created['id']}", headers=headers)
    assert again.json()["result"]["deletion_job_id"] == job_id

    # 回收站可见且可恢复
    bin_rows = (await platform_client.get(f"{BASE}/recycle-bin", headers=headers)).json()["result"]["items"]
    assert any(r["id"] == job_id for r in bin_rows)
    restore = await platform_client.post(f"{BASE}/recycle-bin/{job_id}/restore", headers=headers)
    assert restore.status_code == 200, restore.text

    # 恢复后重新显示；Memory 不回滚（AC⑦）
    rows = (await platform_client.get(f"{BASE}/sessions", headers=headers)).json()["result"]["items"]
    assert any(r["id"] == created["id"] for r in rows)
    impact = await platform_client.get(f"{BASE}/sessions/{created['id']}/memory-impact", headers=headers)
    assert impact.status_code == 200
    # 恢复前已产生 0 次 Commit（未 commit），不影响已验证的写入恢复语义


async def test_restore_does_not_rollback_memory(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="restore-1")
    await _append(platform_client, headers, created["id"], "user", "remember coffee", key="m1")
    await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "c1"}, headers=headers
    )
    impact_before = await platform_client.get(
        f"{BASE}/sessions/{created['id']}/memory-impact", headers=headers
    )
    totals_before = impact_before.json()["result"]["totals"]

    await platform_client.delete(f"{BASE}/sessions/{created['id']}", headers=headers)
    job = (await platform_client.get(f"{BASE}/recycle-bin", headers=headers)).json()["result"]["items"][0]
    restore = await platform_client.post(f"{BASE}/recycle-bin/{job['id']}/restore", headers=headers)
    assert restore.status_code == 200, restore.text

    impact_after = await platform_client.get(
        f"{BASE}/sessions/{created['id']}/memory-impact", headers=headers
    )
    assert impact_after.json()["result"]["totals"] == totals_before


async def test_purge_worker_physical_delete_after_30_days(
    platform_client: httpx.AsyncClient, platform_app, session: AsyncSession
) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, headers, key="purge-1")
    await _append(platform_client, headers, created["id"], "user", "remember x", key="p1")
    resp = await platform_client.delete(f"{BASE}/sessions/{created['id']}", headers=headers)
    job_id = resp.json()["result"]["deletion_job_id"]

    # 把 purge_after 拨到过去（30 天期满模拟）
    from sqlalchemy import text

    await session.execute(
        text("UPDATE iam_deletion_jobs SET purge_after = now() - interval '1 day' WHERE id = :id").bindparams(
            id=uuid.UUID(job_id)
        )
    )
    await session.commit()

    from openviking.server.platform.iam import PostgresIamRepository

    worker = PurgeWorker(
        repo=PostgresIamRepository(),
        handlers={"session": SessionPurgeHandler(platform_app.state.iam_session_service)},
    )
    processed = await worker.run_once(session, limit=10)
    assert processed == 1

    row = (
        await session.execute(
            text("SELECT status FROM iam_deletion_jobs WHERE id = :id").bindparams(id=uuid.UUID(job_id))
        )
    ).scalar_one()
    assert row == "purged"

    # 物理删除幂等：重复 run 不再处理
    processed = await worker.run_once(session, limit=10)
    assert processed == 0


# ── AC⑧：跨 User 不可见语义 ──


async def test_cross_user_session_invisible(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    alice_headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, alice_headers, key="xuser-1")

    admin_headers = await _login_and_headers(platform_client, "admin@acme.com")
    detail = await platform_client.get(f"{BASE}/sessions/{created['id']}", headers=admin_headers)
    assert detail.status_code == 404
    assert detail.json()["detail"]["code"] == "SESSION_NOT_FOUND"

    # 管理员（同 Account）也不能通过 self 端点读取他人 Session（11 §77.1）
    messages = await platform_client.get(
        f"{BASE}/sessions/{created['id']}/messages", headers=admin_headers
    )
    assert messages.status_code == 404

    # 跨 Account：admin 的普通 User 也看不见
    from openviking.server.platform.iam.permissions import USER
    from tests.platform.helpers import create_user

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
    bob_headers = await _login_and_headers(platform_client, "bob@acme.com")
    detail = await platform_client.get(f"{BASE}/sessions/{created['id']}", headers=bob_headers)
    assert detail.status_code == 404


# ── 集成客户端写链路（11 §70.6：User API Key，无 CSRF）──


async def test_integration_client_writes_via_api_key(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    import secrets

    secret = "s3cret-" + secrets.token_hex(8)
    public_id = "pub-" + secrets.token_hex(8)
    await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=setup.alice.id,
        name="Codex Integration",
        public_id=public_id,
        key_hash=sha256_hex(secret),
        key_last_four=secret[-4:],
        created_by=setup.alice.id,
    )
    await session.commit()
    api_key = f"ovk_u.{public_id}.{secret}"
    headers = {"X-Api-Key": api_key}

    # API Key 写链路无需 CSRF（verify_integration_write）
    created = await _create_session(platform_client, headers, client_name="Codex", key="api-key-1")
    assert created["created"] is True
    resp = await _append(platform_client, headers, created["id"], "user", "hello from codex", key="ak-1")
    assert resp.status_code == 200, resp.text
    commit = await platform_client.post(
        f"{BASE}/sessions/{created['id']}/commit", json={"idempotency_key": "ak-c1"}, headers=headers
    )
    assert commit.status_code == 200, commit.text

    # 浏览器 Session 在 API Key 场景下仍需 CSRF 的对照在 test_csrf 已有；
    # API Key 凭据不带 session_id，verify_csrf 拒绝它（不用于集成写端点）。
    read = await platform_client.get(f"{BASE}/sessions", headers=headers)
    assert read.status_code == 200
    assert any(r["id"] == created["id"] for r in read.json()["result"]["items"])


# ── 成员只读视图（11 §73：Actor/Subject 同时记录）──


async def test_member_readonly_sessions_admin(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    alice_headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, alice_headers, key="member-1")
    await _append(platform_client, headers=alice_headers, session_id=created["id"], role="user", content="remember a", key="mm1")

    admin_headers = await _login_and_headers(platform_client, "admin@acme.com")
    rows = await platform_client.get(
        f"{BASE_ADMIN}/users/{setup.alice.id}/sessions", headers=admin_headers
    )
    assert rows.status_code == 200, rows.text
    assert any(r["id"] == created["id"] for r in rows.json()["result"]["items"])

    detail = await platform_client.get(
        f"{BASE_ADMIN}/users/{setup.alice.id}/sessions/{created['id']}", headers=admin_headers
    )
    assert detail.status_code == 200, detail.text

    messages = await platform_client.get(
        f"{BASE_ADMIN}/users/{setup.alice.id}/sessions/{created['id']}/messages", headers=admin_headers
    )
    assert messages.status_code == 200, messages.text
    assert messages.json()["result"]["items"][0]["content"] == "remember a"

    impact = await platform_client.get(
        f"{BASE_ADMIN}/users/{setup.alice.id}/sessions/{created['id']}/memory-impact", headers=admin_headers
    )
    assert impact.status_code == 200, impact.text

    # 管理员不能恢复他人 Session（self scope 恢复 → 404 不可见）
    other_job = uuid.uuid4()
    resp = await platform_client.post(f"{BASE}/recycle-bin/{other_job}/restore", headers=admin_headers)
    assert resp.status_code == 404

    # 审计同时记录 Actor(admin) 与 Subject(alice)
    from sqlalchemy import select

    from openviking.server.platform.models import IamAuditEvent

    audit = (
        await session.execute(
            select(IamAuditEvent).where(
                IamAuditEvent.action == "session.read.admin",
                IamAuditEvent.actor_user_id == setup.admin.id,
                IamAuditEvent.subject_user_id == setup.alice.id,
            )
        )
    ).scalars().first()
    assert audit is not None


async def test_member_readonly_sessions_platform(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    alice_headers = await _login_and_headers(platform_client, "alice@acme.com")
    created = await _create_session(platform_client, alice_headers, key="member-p1")

    psa_headers = await _login_and_headers(platform_client, "psa@platform.local")
    rows = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/sessions", headers=psa_headers
    )
    assert rows.status_code == 200, rows.text
    assert any(r["id"] == created["id"] for r in rows.json()["result"]["items"])

    detail = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/sessions/{created['id']}",
        headers=psa_headers,
    )
    assert detail.status_code == 200, detail.text

    # 普通 User 无成员只读权限（即使同 Account）
    alice_again = await _login_and_headers(platform_client, "alice@acme.com")
    forbidden = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/sessions", headers=alice_again
    )
    assert forbidden.status_code == 403


# ── 列表排序与基本读取 ──


async def test_session_list_ordering(platform_client: httpx.AsyncClient, session: AsyncSession) -> None:
    await build_auth_setup(session)
    headers = await _login_and_headers(platform_client, "alice@acme.com")
    first = await _create_session(platform_client, headers, client_name="Codex", key="order-1")
    second = await _create_session(platform_client, headers, client_name="MCP", key="order-2")

    rows = (await platform_client.get(f"{BASE}/sessions", headers=headers)).json()["result"]["items"]
    assert [r["id"] for r in rows] == [second["id"], first["id"]]
    assert rows[0]["title"].startswith("MCP #")

    detail = await platform_client.get(f"{BASE}/sessions/{second['id']}", headers=headers)
    assert detail.json()["result"]["client_name"] == "MCP"
    assert "ov_session_id" not in detail.json()["result"]
    assert "ov_uri" not in detail.json()["result"]
    assert "viking" not in str(detail.json())
