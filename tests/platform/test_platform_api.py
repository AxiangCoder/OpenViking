"""P1-E5 Platform API 集成测试（05 §12.6 `/api/platform/v1/platform/*`，14 号计划 §96.5）。

验收映射：
- ① PSA 建 Account+首位 Admin 返回 ov 映射与一次性密码（`==3` 复跑；
  「密码仅一次不可再取」断言）；
- ③ 平台级重置：跨 Account 404、成功撤销目标全部 Session（`==7`/`==8` 复跑）；
- ④ 平台级提升：即时生效、仅 user→account_admin、产品 API 不能建/提 PSA；
- ⑥ 平台审计读取、API Key 元数据只读/撤销。
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.iam.permissions import ACCOUNT_ADMIN, USER
from openviking.server.platform.models import IamAccount, IamRole, IamUser, IamUserRole
from tests.platform.helpers import build_auth_setup, create_api_key, run_provisioning

BASE_AUTH = "/api/platform/v1/auth"
BASE_PLATFORM = "/api/platform/v1/platform"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"
PSA_EMAIL = "psa@platform.local"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _create_account(client, csrf: str, *, code: str, email: str, username: str) -> dict:
    r = await client.post(
        f"{BASE_PLATFORM}/accounts",
        json={
            "account_code": code,
            "account_name": code,
            "admin_email": email,
            "admin_username": username,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def _role_code(session: AsyncSession, user_id) -> str | None:
    link = (
        await session.execute(select(IamUserRole).where(IamUserRole.user_id == user_id))
    ).scalar_one_or_none()
    if link is None:
        return None
    role = (
        await session.execute(select(IamRole).where(IamRole.id == link.role_id))
    ).scalar_one_or_none()
    return role.code if role is not None else None


# ── AC ①：PSA 建 Account + 首位 Admin（ov 映射 + 一次性密码）──


async def test_psa_creates_account_with_first_admin(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, PSA_EMAIL)
    result = await _create_account(
        platform_client, csrf, code="acme2", email="admin2@acme2.com", username="admin2"
    )
    # P2-E1（AC①）：Account+首位 Admin 初始 provisioning + outbox 事件；
    # ProvisioningWorker 初始化 namespace 后转 active
    processed, _ = await run_provisioning(session)
    assert processed == 2

    # ov 映射（`==3` 复跑）
    assert result["account"]["ov_account_id"].startswith("ov_account_")
    assert result["first_admin"]["ov_user_id"].startswith("ov_user_")
    assert result["first_admin"]["role"] == ACCOUNT_ADMIN
    initial_password = result["first_admin"]["initial_password"]
    assert len(initial_password) >= 16

    # DB：Account + 首位 Admin（角色 account_admin、Argon2id hash）
    account = (
        await session.execute(select(IamAccount).where(IamAccount.code == "acme2"))
    ).scalar_one()
    admin = (
        await session.execute(select(IamUser).where(IamUser.email == "admin2@acme2.com"))
    ).scalar_one()
    assert account.status == "active"
    assert admin.status == "active"
    assert admin.password_hash.startswith("$argon2id$")
    assert initial_password not in admin.password_hash
    assert await _role_code(session, admin.id) == ACCOUNT_ADMIN

    # 「密码仅一次不可再取」：后续任何查询不再返回密码（03 §8.3）
    r = await platform_client.get(f"{BASE_PLATFORM}/accounts")
    text = r.text
    assert "initial_password" not in text and "password" not in text
    r = await platform_client.get(f"{BASE_PLATFORM}/accounts/{account.id}/users")
    text = r.text
    assert "initial_password" not in text and "password" not in text
    assert initial_password not in text

    # 首位 Admin 用初始密码可登录（密码交接发生在创建响应）
    r = await platform_client.post(
        f"{BASE_AUTH}/login", json={"email": "admin2@acme2.com", "password": initial_password}
    )
    assert r.status_code == 200

    # 审计：account.create（Actor=PSA、Subject=Account+首位 Admin，无密码明文）
    events = await setup.repo.list_audit_events(session, action="account.create")
    assert len(events) == 1
    assert events[0].actor_user_id == setup.psa.id
    assert events[0].subject_account_id == account.id
    assert events[0].subject_user_id == admin.id
    assert initial_password not in str(events[0].metadata_json)


async def test_duplicate_account_code_and_email_409(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    csrf = await _login(platform_client, PSA_EMAIL)
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts",
        json={
            "account_code": "acme",
            "account_name": "Dup",
            "admin_email": "dup@acme.com",
            "admin_username": "dup",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ACCOUNT_CODE_ALREADY_EXISTS"
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts",
        json={
            "account_code": "beta",
            "account_name": "Beta",
            "admin_email": "admin@acme.com",
            "admin_username": "badmin",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "EMAIL_ALREADY_EXISTS"


async def test_platform_accounts_list(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    await _login(platform_client, PSA_EMAIL)
    r = await platform_client.get(f"{BASE_PLATFORM}/accounts")
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    acme = next(a for a in items if a["code"] == "acme")
    assert acme["id"] == str(setup.acme.id)
    assert acme["ov_account_id"] == setup.acme.ov_account_id
    assert r.json()["result"]["next_cursor"] is None


async def test_platform_account_users_list(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    await _login(platform_client, PSA_EMAIL)
    r = await platform_client.get(f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users")
    assert r.status_code == 200
    by_email = {u["email"]: u for u in r.json()["result"]["items"]}
    assert by_email["admin@acme.com"]["role"] == ACCOUNT_ADMIN
    assert by_email["alice@acme.com"]["role"] == USER
    # 不存在 Account → 404
    import uuid

    r = await platform_client.get(f"{BASE_PLATFORM}/accounts/{uuid.uuid4()}/users")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


async def test_account_admin_cannot_access_platform_api(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    await _login(platform_client, "admin@acme.com")
    r = await platform_client.get(f"{BASE_PLATFORM}/accounts")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"


# ── AC ③：平台级重置（`==7`/`==8` 复跑，platform 面）──


async def test_platform_reset_revokes_sessions(session: AsyncSession, platform_client, platform_app) -> None:
    setup = await _seed(session)
    admin = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform_app), base_url="https://testserver"
    )
    try:
        await _login(admin, "admin@acme.com")
        assert (await admin.get(f"{BASE_AUTH}/me")).status_code == 200
        csrf = await _login(platform_client, PSA_EMAIL)

        r = await platform_client.post(
            f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.admin.id}/password/reset",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        body = r.json()["result"]
        assert len(body["new_password"]) >= 16
        assert body["sessions_revoked"] >= 1
        # 旧 Session 立即失效（`==7` 复跑）
        assert (await admin.get(f"{BASE_AUTH}/me")).status_code == 401
        # 新密码可登录
        r = await platform_client.post(
            f"{BASE_AUTH}/login", json={"email": "admin@acme.com", "password": body["new_password"]}
        )
        assert r.status_code == 200
    finally:
        await admin.aclose()


async def test_platform_reset_cross_account_404(session: AsyncSession, platform_client) -> None:
    """`==8` 复跑：路径 Account 与目标归属不一致 → 404。"""
    setup = await _seed(session)
    csrf = await _login(platform_client, PSA_EMAIL)
    beta = await _create_account(
        platform_client, csrf, code="beta", email="badmin@beta.com", username="badmin"
    )
    beta_account_id = beta["account"]["id"]
    # acme 的 alice 挂在 beta 路径下 → 404
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{beta_account_id}/users/{setup.alice.id}/password/reset",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"
    # 目标 PSA（无 Account，不可寻址）→ 404（禁目标 PSA，05 §12.6）
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.psa.id}/password/reset",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


# ── AC ④：平台级提升（仅 user→account_admin、即时生效免重登）──


async def test_platform_promote_effective_immediately(session: AsyncSession, platform_client, platform_app) -> None:
    setup = await _seed(session)
    alice = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform_app), base_url="https://testserver"
    )
    try:
        await _login(alice, "alice@acme.com")
        assert (await alice.get(f"{BASE_AUTH}/me")).json()["result"]["roles"] == ["user"]
        csrf = await _login(platform_client, PSA_EMAIL)

        r = await platform_client.put(
            f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/role",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        assert r.json()["result"]["role"] == ACCOUNT_ADMIN
        # 免重登即时生效（`==11` 复跑：permission_version 缓存失效）
        me = (await alice.get(f"{BASE_AUTH}/me")).json()["result"]
        assert ACCOUNT_ADMIN in me["roles"]
        assert "user.create" in me["permissions"]
        assert await _role_code(session, setup.alice.id) == ACCOUNT_ADMIN
    finally:
        await alice.aclose()


async def test_platform_promote_guards(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, PSA_EMAIL)
    # 目标已是 account_admin → 403（仅 user→account_admin，04 §10.6）
    r = await platform_client.put(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.admin.id}/role",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "ROLE_ASSIGNMENT_ONLY_FROM_USER"
    # 目标在另一 Account → 404（不可见语义）
    r = await platform_client.put(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.psa.id}/role",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
    # Account Admin 无平台提升权限
    admin_csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.put(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/role",
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"


# ── API Key 元数据只读/撤销（platform 面；P1-E4 未合并前直用 E1 repository）──


async def test_platform_api_key_metadata_and_revoke(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    key1 = await create_api_key(setup.repo, session, setup.acme, setup.alice, name="Codex")
    await create_api_key(setup.repo, session, setup.acme, setup.alice, name="OpenClaw")
    csrf = await _login(platform_client, PSA_EMAIL)

    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/api-keys"
    )
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    assert {i["name"] for i in items} == {"Codex", "OpenClaw"}
    assert "api_key" not in r.text and "key_hash" not in r.text
    assert all(len(i["key_last_four"]) == 4 for i in items)  # 仅末四位掩码

    r = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/api-keys/{key1.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    # 按名独立：key1 标记撤销、key2 不受影响（spike ==9 语义）
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/api-keys"
    )
    by_name = {i["name"]: i for i in r.json()["result"]["items"]}
    assert by_name["Codex"]["revoked_at"] is not None
    assert by_name["OpenClaw"]["revoked_at"] is None
    # 重复撤销 → 404（幂等）
    r = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/api-keys/{key1.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


# ── 平台审计读取 ──


async def test_platform_audit_events_platform_scope(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    csrf = await _login(platform_client, PSA_EMAIL)
    beta = await _create_account(
        platform_client, csrf, code="beta", email="badmin@beta.com", username="badmin"
    )
    r = await platform_client.get(f"{BASE_PLATFORM}/audit-events")
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    actions = {i["action"] for i in items}
    assert "account.create" in actions
    # 平台范围跨 Account 可见（含无 Account 的系统事件与目标 Account 事件）
    assert {i["account_id"] for i in items} >= {str(beta["account"]["id"])}
