"""P1-E3 Auth API 集成测试（05 §12.3，14 号计划 §96.3）。

同进程集成基线（Spike 14/14 已验；create_app 真实挂载留 P5-E1）。
验收映射：
- ① 登录下发 Cookie（≥40 字符，HttpOnly/Secure/SameSite=Lax/Path=/）+ DB 仅存 SHA-256；
- ② 错误密码/未知邮箱同码 401（LOGIN_FAILED）；
- ③ 改密错旧密码→LOGIN_FAILED；成功响应含新 Set-Cookie，旧 Cookie 401；
- ⑥ 无 CSRF Token 写请求被拒、API Key 不能做 CSRF 写（verify_csrf 单测补分支）；
- ⑦ 连续失败触发限流冷却（Retry-After）；
- ⑧ auth/me 权限摘要与 P1-E2 一致、logout 后立即失效；
- ⑨ 登录成功/失败/登出/会话撤销审计（Actor/Subject、脱敏）；
- ⑪ session-summary 脱敏（13 §81.4）。
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.iam.permissions import USER_PERMISSIONS
from openviking.server.platform.models import IamSession
from tests.platform.helpers import build_auth_setup

BASE = "/api/platform/v1/auth"
ALICE_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str = "alice@acme.com") -> dict:
    r = await client.post(
        f"{BASE}/login", json={"email": email, "password": ALICE_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


# ── AC ①：登录下发 Cookie + DB 仅存 SHA-256 ──


async def test_login_issues_secure_session_cookie(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    r = await auth_client.post(
        f"{BASE}/login", json={"email": "alice@acme.com", "password": ALICE_PASSWORD}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert len(body["result"]["csrf_token"]) >= 32

    cookie = auth_client.cookies.get("__Host-ov_session")
    assert cookie is not None
    assert len(cookie) >= 40  # ≥256bit（03 §8.1）
    set_cookie = r.headers["set-cookie"]
    assert "__Host-ov_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=" in set_cookie and "lax" in set_cookie.lower()  # starlette 小写 lax
    assert "Path=/" in set_cookie
    assert "Domain=" not in set_cookie

    # DB 仅存 SHA-256(token)（04 §10.7）
    rows = list((await session.execute(select(IamSession))).scalars())
    assert len(rows) == 1
    assert rows[0].token_hash == sha256_hex(cookie)
    assert cookie not in rows[0].token_hash


# ── AC ⑧：auth/me 权限摘要与 P1-E2 一致 ──


async def test_me_permissions_match_catalog(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    await _login(auth_client)
    r = await auth_client.get(f"{BASE}/me")
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["account"]["id"] is not None
    assert result["user"]["ov_user_id"].startswith("ov_user_")
    assert result["roles"] == ["user"]
    assert sorted(result["permissions"]) == sorted(USER_PERMISSIONS)
    assert result["can_switch_account"] is False
    assert result["csrf_token"] is None  # CSRF 只在登录/改密响应下发一次


# ── AC ②：统一 LOGIN_FAILED 防枚举 ──


async def test_login_failure_unified_401(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    for payload in (
        {"email": "alice@acme.com", "password": "wrong-password"},
        {"email": "nobody@acme.com", "password": "whatever-1234"},
    ):
        r = await auth_client.post(f"{BASE}/login", json=payload)
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "LOGIN_FAILED"
        assert auth_client.cookies.get("__Host-ov_session") is None


# ── AC ⑦：连续失败触发限流冷却 ──


async def test_login_rate_limit_returns_retry_after(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    for _ in range(5):  # OV_LOGIN_MAX_ATTEMPTS 默认 5
        await auth_client.post(
            f"{BASE}/login", json={"email": "alice@acme.com", "password": "wrong"}
        )
    r = await auth_client.post(
        f"{BASE}/login", json={"email": "alice@acme.com", "password": ALICE_PASSWORD}
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "LOGIN_FAILED"
    retry_after = int(r.headers["retry-after"])
    assert 0 < retry_after <= 61


# ── AC ⑥：CSRF（无 Token 写请求被拒；API Key 不能做 CSRF 写）──


async def test_csrf_write_without_token_rejected(session: AsyncSession, auth_client) -> None:
    """AC ⑥：带 Session Cookie 但无 X-CSRF-Token 的写请求 → 403 CSRF_INVALID。"""
    await _seed(session)
    csrf = await _login(auth_client)
    assert csrf
    r = await auth_client.post(f"{BASE}/logout")  # 无 Token
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"
    # 会话未被撤销（Token 校验失败不产生副作用）
    me = await auth_client.get(f"{BASE}/me")
    assert me.status_code == 200


async def test_csrf_write_with_wrong_token_rejected(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    await _login(auth_client)
    r = await auth_client.post(f"{BASE}/logout", headers={"X-CSRF-Token": "wrong-token"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"


async def test_csrf_write_without_session_cookie_rejected(session: AsyncSession, auth_client) -> None:
    """AC ⑥（API Key 语义）：非 Session 凭据不能执行 CSRF 写——无 Cookie 的
    写请求统一 401（P1-E4 的 api_key principal 解析后由 verify_csrf 403 兜底）。"""
    await _seed(session)
    r = await auth_client.post(
        f"{BASE}/password/change",
        json={"old_password": "x", "new_password": "NewPass-2026-strong!"},
        headers={"Authorization": "Bearer ovk_u.some.public.secret"},
    )
    assert r.status_code in (401, 403)
    assert r.json()["detail"]["code"] in ("INVALID_CREDENTIAL", "CSRF_INVALID")


async def test_csrf_bad_origin_rejected(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    r = await auth_client.post(
        f"{BASE}/logout",
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"


async def test_csrf_same_origin_allowed(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    r = await auth_client.post(
        f"{BASE}/logout",
        headers={"X-CSRF-Token": csrf, "Origin": "https://testserver"},
    )
    assert r.status_code == 200


async def test_csrf_token_bound_to_session(session: AsyncSession, auth_client) -> None:
    """另一个 Session 的 CSRF Token 无效（Token 与登录 Session 绑定，03 §8.2）。"""
    setup = await _seed(session)
    _other_raw, other_csrf, _sid = await build_login_for(setup, session)
    await _login(auth_client)
    r = await auth_client.post(
        f"{BASE}/logout", headers={"X-CSRF-Token": other_csrf}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"


# ── AC ③：改密（旧密码必填 + 轮换 + 同步 Set-Cookie）──


async def test_password_change_wrong_old_password(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    r = await auth_client.post(
        f"{BASE}/password/change",
        json={"old_password": "bad-old", "new_password": "NewPass-2026-strong!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "LOGIN_FAILED"
    # 会话未被轮换：旧 Cookie 仍有效
    assert (await auth_client.get(f"{BASE}/me")).status_code == 200


async def test_password_change_rotates_cookie_old_cookie_dead(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """AC ③：成功响应含新 Set-Cookie；旧 Cookie 401（spike ==6 补断言）。"""
    await _seed(session)
    csrf = await _login(auth_client)
    old_cookie = auth_client.cookies.get("__Host-ov_session")

    r = await auth_client.post(
        f"{BASE}/password/change",
        json={"old_password": ALICE_PASSWORD, "new_password": "NewPass-2026-strong!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["session_rotated"] is True
    assert len(body["csrf_token"]) >= 32

    new_cookie = auth_client.cookies.get("__Host-ov_session")
    assert new_cookie is not None and new_cookie != old_cookie
    set_cookie = r.headers["set-cookie"]
    assert f"__Host-ov_session={new_cookie}" in set_cookie
    assert "HttpOnly" in set_cookie

    # 新 Cookie 可用（权限一致），新 CSRF Token 可写
    assert (await auth_client.get(f"{BASE}/me")).status_code == 200
    r = await auth_client.post(
        f"{BASE}/logout", headers={"X-CSRF-Token": body["csrf_token"]}
    )
    assert r.status_code == 200

    # 旧 Cookie 立即失效（DB 侧 rotated 撤销）
    stale = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        stale.cookies.set("__Host-ov_session", old_cookie)
        r = await stale.get(f"{BASE}/me")
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "SESSION_EXPIRED"
    finally:
        await stale.aclose()


# ── AC ⑧：logout 后立即失效 ──


async def test_logout_invalidates_session_immediately(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    assert (await auth_client.get(f"{BASE}/me")).status_code == 200

    r = await auth_client.post(f"{BASE}/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert auth_client.cookies.get("__Host-ov_session") is None
    assert (await auth_client.get(f"{BASE}/me")).status_code == 401


async def test_logout_all_revokes_all_sessions(
    session: AsyncSession, auth_client, auth_app
) -> None:
    await _seed(session)
    # 两个独立客户端（两个 Session）
    other = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        csrf1 = await _login(auth_client)
        csrf2 = await _login(other)
        assert (await auth_client.get(f"{BASE}/me")).status_code == 200
        assert (await other.get(f"{BASE}/me")).status_code == 200

        r = await auth_client.post(f"{BASE}/logout-all", headers={"X-CSRF-Token": csrf1})
        assert r.status_code == 200
        assert r.json()["result"]["sessions_revoked"] == 2
        assert (await auth_client.get(f"{BASE}/me")).status_code == 401
        assert (await other.get(f"{BASE}/me")).status_code == 401
        _ = csrf2
    finally:
        await other.aclose()


# ── AC ⑨：登录成功/失败/登出/会话撤销审计 ──


async def test_audit_events_for_login_logout(session: AsyncSession, auth_client, repo) -> None:
    setup = await _seed(session)
    csrf = await _login(auth_client)
    await auth_client.post(f"{BASE}/logout", headers={"X-CSRF-Token": csrf})

    events = await repo.list_audit_events(session)
    by_action = {e.action: e for e in events}
    assert set(by_action) == {"auth.login", "auth.logout"}

    login_event = by_action["auth.login"]
    assert login_event.result == "success"
    assert login_event.actor_user_id == setup.alice.id
    assert login_event.subject_user_id == setup.alice.id
    assert login_event.actor_account_id == setup.acme.id
    assert login_event.authentication_method == "session"
    assert login_event.metadata_json == {"method": "password"}

    logout_event = by_action["auth.logout"]
    assert logout_event.result == "success"
    assert logout_event.actor_user_id == setup.alice.id
    assert logout_event.metadata_json == {"session_revoked": True}

    # 脱敏：无密码/Token 明文
    for event in events:
        serialized = str(event.metadata_json)
        assert ALICE_PASSWORD not in serialized
        assert "__Host-ov_session" not in serialized


async def test_audit_login_failure_has_no_actor(repo, session: AsyncSession, auth_client) -> None:
    await _seed(session)
    await auth_client.post(
        f"{BASE}/login", json={"email": "nobody@acme.com", "password": "x"}
    )
    events = await repo.list_audit_events(session, action="auth.login", result="failed")
    assert len(events) == 1
    assert events[0].actor_user_id is None
    assert events[0].reason == "LOGIN_FAILED"
    assert events[0].metadata_json == {"normalized_email": "nobody@acme.com"}


# ── ⑪ session-summary 脱敏（13 §81.4）──


async def test_session_summary_sanitized(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    long_ua = "Mozilla/5.0 " + "X" * 200
    await auth_client.post(
        f"{BASE}/login",
        json={"email": "alice@acme.com", "password": ALICE_PASSWORD},
        headers={"user-agent": long_ua},
    )
    r = await auth_client.get(f"{BASE}/me/session-summary")
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["session_id"]
    assert result["last_seen_at"]
    assert len(result["ip_hash"]) == 64  # 隐私化 IP（04 §10.7）
    assert result["ip_hash"] != "127.0.0.1"
    assert result["user_agent"].startswith("Mozilla/5.0")
    assert len(result["user_agent"]) <= 81  # 截断（13 §81.4）


# ── 辅助 ──


async def build_login_for(setup, session: AsyncSession):
    """为另一用户创建登录 Session（跨 Session CSRF 无效测试用）。"""
    from tests.platform.helpers import create_login_session

    return await create_login_session(setup, session, setup.admin)
