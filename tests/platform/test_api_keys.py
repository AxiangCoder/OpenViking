"""P1-E4 用户 API Key API 集成测试（05 §12.4，14 号计划 §96.4 验收 ①-⑧）。

同进程集成基线（spike `==4/==9/==10` 复跑 + 新增断言）：
- ① 创建返回 `ovk_u.<public>.<secret>`、列表无明文、DB 仅存 SHA-256；
- ② Session 与 API Key 同 user_id、同权限集（authentication_method 差异
  由 test_principal_resolver 服务级断言）；
- ③ Bearer 与 X-Api-Key 解析一致（HTTP 层）；
- ④ Cookie+Bearer 并存以 Cookie 为准、Cookie 失效不回退（新增断言）；
- ⑤ 按名撤销互不影响、重复撤销幂等 404（spike ==9 复跑）；
- ⑥ 禁用后全部 Key 立即失效（spike ==10 复跑切片，服务级在
  test_principal_resolver，此处断言 HTTP 401）；
- ⑦ 重置/改密不撤销 Key（==7 经 P1-E5 复跑，此处服务级切片）；
- ⑧ Key 不能通过 CSRF 写接口（403 CSRF_INVALID）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.iam.permissions import USER_PERMISSIONS
from openviking.server.platform.models import IamApiCredential
from tests.platform.helpers import AuthSetup, build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE_ME = "/api/platform/v1/me"
ALICE_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession) -> AuthSetup:
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str = "alice@acme.com") -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": ALICE_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _create_key(
    client: httpx.AsyncClient, csrf: str, name: str = "Codex on MacBook"
) -> dict:
    r = await client.post(
        f"{BASE_ME}/api-keys", json={"name": name}, headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def _make_user(
    session: AsyncSession, setup: AuthSetup, username: str, email: str
) -> object:
    """创建 acme 下第二个普通用户（Cookie vs Bearer 不同身份测试用）。"""
    from openviking.server.platform.auth.password import hash_password
    from openviking.server.platform.iam.permissions import USER

    user = await setup.repo.create_user(
        session,
        account_id=setup.acme.id,
        ov_user_id=f"ov_user_{username}",
        username=username,
        email=email,
        display_name=username,
        password_hash=hash_password(ALICE_PASSWORD),
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


# ── AC ①：创建返回完整 Key（仅一次）、列表无明文、DB 仅存 SHA-256 ──


async def test_create_returns_full_key_once_format_valid(
    session: AsyncSession, auth_client
) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    result = await _create_key(auth_client, csrf, "Codex on MacBook")

    full_key = result["api_key"]
    parts = full_key.split(".")
    assert len(parts) == 3 and parts[0] == "ovk_u"  # 03 §8.4 格式
    public_id, secret = parts[1], parts[2]
    assert len(secret) >= 43  # 32 字节 base64url（≥256 bit）
    assert re.fullmatch(r"[A-Za-z0-9_-]+", secret)
    assert result["key_last_four"] == secret[-4:]
    assert result["name"] == "Codex on MacBook"
    assert result["status"] == "active"
    assert result["expires_at"] is None

    # DB 仅存 public_id + SHA-256(secret) + 末四位（04 §10.3）
    rows = list((await session.execute(select(IamApiCredential))).scalars())
    assert len(rows) == 1
    assert rows[0].public_id == public_id
    assert rows[0].key_hash == sha256_hex(secret)
    assert full_key not in str(rows[0].key_hash)
    assert rows[0].key_last_four == secret[-4:]


async def test_list_returns_metadata_only_no_plaintext(
    session: AsyncSession, auth_client
) -> None:
    """列表只返回元数据与掩码，不返回明文（验收 ①，spike ==4 复跑）。"""
    await _seed(session)
    csrf = await _login(auth_client)
    first = await _create_key(auth_client, csrf, "Codex on MacBook")
    second = await _create_key(auth_client, csrf, "OpenClaw")

    r = await auth_client.get(f"{BASE_ME}/api-keys")
    assert r.status_code == 200
    result = r.json()["result"]
    assert len(result) == 2
    dumped = r.text
    assert "api_key" not in dumped  # 无明文字段
    assert first["api_key"].split(".")[2] not in dumped  # secret 不出现在响应
    for item in result:
        assert item["name"] in ("Codex on MacBook", "OpenClaw")
        assert re.fullmatch(r".{4}", item["key_last_four"])
        assert item["status"] == "active"
    _ = second


# ── AC ②：Session 与 API Key 同 user_id、同权限集 ──


async def test_no_credentials_401(session: AsyncSession, auth_client) -> None:
    """无任何凭据 → 401（spike ==4「no credential -> 401」复跑）。"""
    await _seed(session)
    r = await auth_client.get(f"{BASE_AUTH}/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_CREDENTIAL"


async def test_session_and_api_key_have_same_identity_and_permissions(
    session: AsyncSession, auth_client
) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    key = (await _create_key(auth_client, csrf))["api_key"]

    me_session = (await auth_client.get(f"{BASE_AUTH}/me")).json()["result"]
    r = await auth_client.get(f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    me_key = r.json()["result"]

    assert me_session["user"]["id"] == me_key["user"]["id"]
    assert me_session["account"] == me_key["account"]
    assert me_session["roles"] == me_key["roles"] == ["user"]
    assert sorted(me_session["permissions"]) == sorted(me_key["permissions"]) == sorted(
        USER_PERMISSIONS
    )


# ── AC ③：Bearer 与 X-Api-Key 解析一致（HTTP 层）──


async def test_x_api_key_header_equivalent_to_bearer(
    session: AsyncSession, auth_client, auth_app
) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    key = (await _create_key(auth_client, csrf))["api_key"]

    r_bearer = await auth_client.get(
        f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {key}"}
    )
    r_header = await auth_client.get(f"{BASE_AUTH}/me", headers={"X-Api-Key": key})
    assert r_bearer.status_code == 200 and r_header.status_code == 200
    assert r_bearer.json()["result"] == r_header.json()["result"]

    # fresh client 无 Cookie：坏 Key 独立解析失败
    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        r_bad = await fresh.get(
            f"{BASE_AUTH}/me", headers={"X-Api-Key": "ovk_u.garbage.parts"}
        )
        assert r_bad.status_code == 401
        assert r_bad.json()["detail"]["code"] == "INVALID_CREDENTIAL"
    finally:
        await fresh.aclose()


# ── AC ④：Cookie+Bearer 并存以 Cookie 为准、Cookie 失效不回退 ──


async def test_cookie_wins_over_bearer_at_http_level(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """同请求携带 alice Cookie 与 bob 有效 Bearer → 以 Cookie（alice）为准。"""
    setup = await _seed(session)
    bob = await _make_user(session, setup, "bob", "bob@acme.com")
    await _login(auth_client)  # alice session cookie
    from openviking.server.platform.auth.api_keys import ApiCredentialService

    bob_key = (
        await ApiCredentialService(setup.repo).create_key(session, user=bob, name="bob-key")
    ).full_key
    await session.commit()

    r = await auth_client.get(
        f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {bob_key}"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["user"]["id"] == str(setup.alice.id)  # Cookie 优先


async def test_dead_cookie_does_not_fallback_to_valid_bearer(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """alice 的 Cookie 已失效 + bob 的有效 Bearer → 401，不回退（验收 ④）。"""
    setup = await _seed(session)
    bob = await _make_user(session, setup, "bob", "bob@acme.com")
    csrf = await _login(auth_client)
    dead_cookie = auth_client.cookies.get("__Host-ov_session")
    await auth_client.post(f"{BASE_AUTH}/logout-all", headers={"X-CSRF-Token": csrf})
    assert (await auth_client.get(f"{BASE_AUTH}/me")).status_code == 401

    from openviking.server.platform.auth.api_keys import ApiCredentialService

    bob_key = (
        await ApiCredentialService(setup.repo).create_key(session, user=bob, name="bob-key")
    ).full_key
    await session.commit()

    stale = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        stale.cookies.set("__Host-ov_session", dead_cookie)
        r = await stale.get(
            f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {bob_key}"}
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "SESSION_EXPIRED"  # 不是 bob 身份
    finally:
        await stale.aclose()


# ── AC ⑤：按名撤销互不影响、重复撤销幂等 ──


async def test_revoke_is_per_key_and_idempotent(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """spike ==9 复跑：撤销 key1 不影响 key2；重复撤销 404。"""
    await _seed(session)
    csrf = await _login(auth_client)
    key1 = await _create_key(auth_client, csrf, "Codex on MacBook")
    key2 = await _create_key(auth_client, csrf, "OpenClaw")

    keys = (await auth_client.get(f"{BASE_ME}/api-keys")).json()["result"]
    key1_id = next(k["id"] for k in keys if k["name"] == "Codex on MacBook")

    r = await auth_client.delete(
        f"{BASE_ME}/api-keys/{key1_id}", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200, r.text
    # 重复撤销幂等 → 404 KEY_NOT_FOUND（spike ==9 语义）
    r = await auth_client.delete(
        f"{BASE_ME}/api-keys/{key1_id}", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "KEY_NOT_FOUND"

    # 撤销后列表标记 revoked；key2 不受影响
    keys = (await auth_client.get(f"{BASE_ME}/api-keys")).json()["result"]
    by_name = {k["name"]: k for k in keys}
    assert by_name["Codex on MacBook"]["status"] == "revoked"
    assert by_name["OpenClaw"]["status"] == "active"

    # 纯 Bearer 校验：key1 死、key2 活（fresh client 无 Cookie）
    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        r = await fresh.get(
            f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {key1['api_key']}"}
        )
        assert r.status_code == 401
        r = await fresh.get(
            f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {key2['api_key']}"}
        )
        assert r.status_code == 200
    finally:
        await fresh.aclose()


async def test_revoke_other_users_key_not_found(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """不能撤销他人 Key：返回 404（不泄露他人凭证存在性，04 §10.3）。"""
    setup = await _seed(session)
    bob = await _make_user(session, setup, "bob", "bob@acme.com")
    from openviking.server.platform.auth.api_keys import ApiCredentialService

    bob_key = await ApiCredentialService(setup.repo).create_key(
        session, user=bob, name="bob-key"
    )
    await session.commit()

    csrf = await _login(auth_client)  # alice
    r = await auth_client.delete(
        f"{BASE_ME}/api-keys/{bob_key.record.id}", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "KEY_NOT_FOUND"
    # bob 的 Key 仍有效
    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        r = await fresh.get(
            f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {bob_key.full_key}"}
        )
        assert r.status_code == 200
    finally:
        await fresh.aclose()


# ── AC ⑥：禁用后全部 Key 立即失效（HTTP 层）──


async def test_disabled_user_keys_and_session_rejected(
    session: AsyncSession, auth_client, auth_app
) -> None:
    setup = await _seed(session)
    csrf = await _login(auth_client)
    key = (await _create_key(auth_client, csrf))["api_key"]

    await setup.repo.update_user(session, setup.alice.id, status="disabled")
    await session.commit()

    assert (await auth_client.get(f"{BASE_AUTH}/me")).status_code == 401
    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        r = await fresh.get(
            f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {key}"}
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "USER_DISABLED"
    finally:
        await fresh.aclose()


# ── AC ⑦：改密/重置不撤销 Key ──


async def test_password_change_and_reset_keep_key_valid(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """改密（HTTP）与分级重置（服务级）后 Key 仍有效（03 §8.3，验收 ⑦）。"""
    setup = await _seed(session)
    csrf = await _login(auth_client)
    key = (await _create_key(auth_client, csrf))["api_key"]

    r = await auth_client.post(
        f"{BASE_AUTH}/password/change",
        json={"old_password": ALICE_PASSWORD, "new_password": "NewPass-2026-strong!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text

    result = await setup.auth.reset_user_password(
        session,
        actor_user_id=setup.admin.id,
        actor_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()
    assert result.sessions_revoked >= 0

    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        r = await fresh.get(
            f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {key}"}
        )
        assert r.status_code == 200  # Key 未被撤销
        assert r.json()["result"]["user"]["id"] == str(setup.alice.id)
    finally:
        await fresh.aclose()


# ── AC ⑧：Key 不能通过 CSRF 写接口 ──


async def test_api_key_cannot_do_csrf_writes(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """API Key 不能执行 CSRF 写：创建/撤销均 403 CSRF_INVALID（验收 ⑧）。"""
    setup = await _seed(session)
    csrf = await _login(auth_client)
    key = (await _create_key(auth_client, csrf))["api_key"]
    keys = (await auth_client.get(f"{BASE_ME}/api-keys")).json()["result"]
    key_id = keys[0]["id"]

    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        headers = {
            "Authorization": f"Bearer {key}",
            "X-CSRF-Token": "whatever",
            "Origin": "https://testserver",
        }
        r = await fresh.post(f"{BASE_ME}/api-keys", json={"name": "x"}, headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CSRF_INVALID"
        r = await fresh.delete(f"{BASE_ME}/api-keys/{key_id}", headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CSRF_INVALID"
    finally:
        await fresh.aclose()
    _ = setup


# ── 到期语义与输入校验 ──


async def test_create_with_past_expiration_rejected(
    session: AsyncSession, auth_client
) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = await auth_client.post(
        f"{BASE_ME}/api-keys",
        json={"name": "stale", "expires_at": past},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_EXPIRATION"


async def test_expired_key_rejected_at_http(
    session: AsyncSession, auth_client, auth_app
) -> None:
    """到期立即拒绝（04 §10.3：expires_at 过后下一次请求失败）。"""
    setup = await _seed(session)
    csrf = await _login(auth_client)
    key = (await _create_key(auth_client, csrf))["api_key"]
    row = await setup.repo.get_api_credential_by_public_id(
        session, key.split(".")[1]
    )
    await session.execute(
        update(IamApiCredential)
        .where(IamApiCredential.id == row.id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await session.commit()

    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="https://testserver"
    )
    try:
        r = await fresh.get(
            f"{BASE_AUTH}/me", headers={"Authorization": f"Bearer {key}"}
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "INVALID_CREDENTIAL"
    finally:
        await fresh.aclose()


async def test_create_without_csrf_rejected(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    await _login(auth_client)
    r = await auth_client.post(f"{BASE_ME}/api-keys", json={"name": "no-csrf"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"


async def test_create_name_validation(session: AsyncSession, auth_client) -> None:
    await _seed(session)
    csrf = await _login(auth_client)
    for bad_name in ("", "x" * 129):
        r = await auth_client.post(
            f"{BASE_ME}/api-keys",
            json={"name": bad_name},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 422


# ── PSA 不签发平台级个人 Key（03 §8.4，05 §12.4）──


async def test_psa_cannot_create_platform_api_key(
    session: AsyncSession, auth_client
) -> None:
    await _seed(session)
    csrf = await _login(auth_client, "psa@platform.local")
    r = await auth_client.post(
        f"{BASE_ME}/api-keys",
        json={"name": "psa-key"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PSA_API_KEY_NOT_SUPPORTED"


# ── 签发/撤销审计（04 §10.8：Actor/Subject 分离、脱敏）──


async def test_create_and_revoke_write_audit_events(
    session: AsyncSession, auth_client, repo
) -> None:
    setup = await _seed(session)
    csrf = await _login(auth_client)
    key = (await _create_key(auth_client, csrf, "audited-key"))["api_key"]
    keys = (await auth_client.get(f"{BASE_ME}/api-keys")).json()["result"]
    r = await auth_client.delete(
        f"{BASE_ME}/api-keys/{keys[0]['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200

    events = await repo.list_audit_events(session)
    by_action = {e.action: e for e in events}
    assert {"credential.create", "credential.revoke"} <= set(by_action)

    create_event = by_action["credential.create"]
    assert create_event.actor_user_id == setup.alice.id
    assert create_event.subject_user_id == setup.alice.id
    assert create_event.actor_account_id == setup.acme.id
    assert create_event.authentication_method == "session"
    assert create_event.metadata_json["name"] == "audited-key"
    assert "api_key" not in str(create_event.metadata_json)
    assert key.split(".")[2] not in str(create_event.metadata_json)  # 无明文

    revoke_event = by_action["credential.revoke"]
    assert revoke_event.actor_user_id == setup.alice.id
    assert revoke_event.result == "success"
    assert "api_key" not in str(revoke_event.metadata_json)
