"""P1-E5 Admin API 集成测试（05 §12.6 `/api/platform/v1/admin/*`，14 号计划 §96.5）。

验收映射：
- ② 建 User 角色固定 user（`==3` 补「密码仅一次不可再取」断言）；
- ③ 重置遵循平台 rank：跨 Account 404、同级 403、成功撤销目标全部 Session
  （`==7`/`==8` 复跑，admin 面）；
- ⑤ 禁用即时杀 Session+全部 Key（`==10` 复跑，admin 面）；
- ⑦ 最后一名 Account Admin 禁用被拒（`LAST_ACCOUNT_ADMIN_REQUIRED`；删除部分
  标注待 P2-E2）；
- ⑥ 写操作 CSRF（03 §8.2）与权限守卫（05 §12.6）。
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamUser
from tests.platform.helpers import build_auth_setup, create_api_key, run_provisioning

BASE_AUTH = "/api/platform/v1/auth"
BASE_ADMIN = "/api/platform/v1/admin"
BASE_PLATFORM = "/api/platform/v1/platform"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _create_account(
    client: httpx.AsyncClient, csrf: str, *, code: str, email: str, username: str
) -> dict:
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


# ── AC ②：Account Admin 直建 User（角色固定 user + 密码仅一次）──


async def test_admin_creates_user_role_fixed(session: AsyncSession, platform_client, platform_app) -> None:
    await _seed(session)
    csrf = await _login(platform_client, "admin@acme.com")
    # 请求体携带 role 字段必须被忽略（角色固定 user，05 §12.6）
    r = await platform_client.post(
        f"{BASE_ADMIN}/users",
        json={"email": "bob@acme.com", "username": "bob", "role": "account_admin"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["role"] == "user"
    assert len(body["initial_password"]) >= 16
    assert body["email"] == "bob@acme.com"

    # 「密码仅一次不可再取」：列表/详情查询永不返回密码（03 §8.3）
    r = await platform_client.get(f"{BASE_ADMIN}/users")
    assert r.status_code == 200
    text = r.text
    assert "initial_password" not in text
    assert "password" not in text
    assert body["initial_password"] not in text

    # DB 仅存 Argon2id hash
    rows = list((await session.execute(select(IamUser).where(IamUser.email == "bob@acme.com"))).scalars())
    assert len(rows) == 1
    assert rows[0].password_hash.startswith("$argon2id$")
    assert body["initial_password"] not in rows[0].password_hash

    # 初始密码可登录（03 §8.3：密码交接只发生在创建响应）
    r = await platform_client.post(
        f"{BASE_AUTH}/login", json={"email": "bob@acme.com", "password": body["initial_password"]}
    )
    assert r.status_code == 200


async def test_admin_lists_users_with_roles(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    await _login(platform_client, "admin@acme.com")
    r = await platform_client.get(f"{BASE_ADMIN}/users")
    assert r.status_code == 200
    result = r.json()["result"]
    by_email = {u["email"]: u for u in result["items"]}
    assert by_email["admin@acme.com"]["role"] == "account_admin"
    assert by_email["alice@acme.com"]["role"] == "user"
    assert result["next_cursor"] is None


# ── 权限与 CSRF 守卫 ──


async def test_plain_user_cannot_access_admin_api(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    await _login(platform_client, "alice@acme.com")
    r = await platform_client.get(f"{BASE_ADMIN}/users")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"


async def test_admin_writes_require_csrf(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ADMIN}/users", json={"email": "bob@acme.com", "username": "bob"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"


async def test_psa_admin_path_has_no_account_context(session: AsyncSession, platform_client) -> None:
    """PSA 走平台路径；/admin 列表无 Account 上下文返回空，直建 User → 404。"""
    await _seed(session)
    csrf = await _login(platform_client, "psa@platform.local")
    r = await platform_client.get(f"{BASE_ADMIN}/users")
    assert r.status_code == 200
    assert r.json()["result"]["items"] == []
    r = await platform_client.post(
        f"{BASE_ADMIN}/users",
        json={"email": "bob@acme.com", "username": "bob"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


# ── AC ③：分级重置（同级 403 / 跨 Account 404 / 撤销目标全部 Session）──


async def test_admin_resets_user_revokes_sessions(session: AsyncSession, platform_client, platform_app) -> None:
    setup = await _seed(session)
    alice = httpx.AsyncClient(transport=httpx.ASGITransport(app=platform_app), base_url="https://testserver")
    try:
        await _login(alice, "alice@acme.com")
        assert (await alice.get(f"{BASE_AUTH}/me")).status_code == 200
        csrf = await _login(platform_client, "admin@acme.com")

        r = await platform_client.post(
            f"{BASE_ADMIN}/users/{setup.alice.id}/password/reset",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        body = r.json()["result"]
        assert len(body["new_password"]) >= 16
        assert body["sessions_revoked"] >= 1
        # 旧 Session 立即失效（`==7` 复跑）
        assert (await alice.get(f"{BASE_AUTH}/me")).status_code == 401
        # 新密码可登录
        r = await platform_client.post(
            f"{BASE_AUTH}/login", json={"email": "alice@acme.com", "password": body["new_password"]}
        )
        assert r.status_code == 200
    finally:
        await alice.aclose()


async def test_reset_same_rank_403(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.admin.id}/password/reset", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN"


async def test_reset_cross_account_404(session: AsyncSession, platform_client) -> None:
    """`==8` 复跑：跨 Account 重置统一 404（不可见语义）。"""
    setup = await _seed(session)
    psa_csrf = await _login(platform_client, "psa@platform.local")
    beta = await _create_account(
        platform_client, psa_csrf, code="beta", email="badmin@beta.com", username="badmin"
    )
    # P2-E1：ProvisioningWorker 转 active 后首位 Admin 方可登录
    await run_provisioning(session)
    beta_admin_id = beta["first_admin"]["id"]

    # beta Account Admin 重置 acme 的 alice → 404
    beta_csrf = await _login(platform_client, "badmin@beta.com", beta["first_admin"]["initial_password"])
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/password/reset", headers={"X-CSRF-Token": beta_csrf}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"
    # 目标不存在 → 404（统一不可见语义）
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{beta_admin_id}/password/reset", headers={"X-CSRF-Token": beta_csrf}
    )
    assert r.status_code == 403  # 同 Account 但同级（admin→admin）→ 403


# ── AC ⑤：禁用即时杀 Session+全部 Key（`==10` 复跑）──


async def test_disable_kills_sessions_and_keys(session: AsyncSession, platform_client, platform_app) -> None:
    setup = await _seed(session)
    await create_api_key(setup.repo, session, setup.acme, setup.alice, name="Codex")
    await create_api_key(setup.repo, session, setup.acme, setup.alice, name="OpenClaw")
    alice = httpx.AsyncClient(transport=httpx.ASGITransport(app=platform_app), base_url="https://testserver")
    try:
        await _login(alice, "alice@acme.com")
        assert (await alice.get(f"{BASE_AUTH}/me")).status_code == 200
        csrf = await _login(platform_client, "admin@acme.com")

        r = await platform_client.post(
            f"{BASE_ADMIN}/users/{setup.alice.id}/disable", headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 200, r.text
        body = r.json()["result"]
        assert body["status"] == "disabled"
        assert body["sessions_revoked"] >= 1
        assert body["keys_revoked"] == 2

        # Session 立即失效；禁用后不可登录
        assert (await alice.get(f"{BASE_AUTH}/me")).status_code == 401
        r = await alice.post(
            f"{BASE_AUTH}/login", json={"email": "alice@acme.com", "password": DEFAULT_PASSWORD}
        )
        assert r.status_code == 401
        # 全部 Key 撤销（DB 侧 revoked_at）
        creds = await setup.repo.list_api_credentials_for_user(session, setup.alice.id)
        assert len(creds) == 2
        assert all(c.revoked_at is not None for c in creds)
    finally:
        await alice.aclose()


# ── AC ⑦：最后一名 Account Admin 禁用被拒 ──


async def test_disable_last_account_admin_409(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    psa_csrf = await _login(platform_client, "psa@platform.local")
    solo = await _create_account(
        platform_client, psa_csrf, code="solo", email="solo@acme.com", username="solo"
    )
    # P2-E1：ProvisioningWorker 转 active 后首位 Admin 方可登录/守卫生效
    await run_provisioning(session)
    solo_admin_id = solo["first_admin"]["id"]
    solo_csrf = await _login(platform_client, "solo@acme.com", solo["first_admin"]["initial_password"])

    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{solo_admin_id}/disable", headers={"X-CSRF-Token": solo_csrf}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LAST_ACCOUNT_ADMIN_REQUIRED"
    # 会话未被杀（请求被拒无副作用）
    assert (await platform_client.get(f"{BASE_AUTH}/me")).status_code == 200


async def test_disable_with_two_admins_allowed(session: AsyncSession, platform_client) -> None:
    """两位 Account Admin 时可禁用其中一位（删除接口归属 P2-E2，删除部分待 P2）。"""
    setup = await _seed(session)
    psa_csrf = await _login(platform_client, "psa@platform.local")
    r = await platform_client.put(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/role",
        headers={"X-CSRF-Token": psa_csrf},
    )
    assert r.status_code == 200, r.text
    admin_csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/disable", headers={"X-CSRF-Token": admin_csrf}
    )
    assert r.status_code == 200


# ── PATCH：启用禁用 / display_name ──


async def test_patch_enable_after_disable(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, "admin@acme.com")
    await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/disable", headers={"X-CSRF-Token": csrf}
    )
    r = await platform_client.patch(
        f"{BASE_ADMIN}/users/{setup.alice.id}",
        json={"status": "active"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["status"] == "active"
    # 恢复后可登录（Session 不自动恢复，重新登录）
    r = await platform_client.post(
        f"{BASE_AUTH}/login", json={"email": "alice@acme.com", "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 200


async def test_patch_display_name(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.patch(
        f"{BASE_ADMIN}/users/{setup.alice.id}",
        json={"display_name": "Alice Renamed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json()["result"]["display_name"] == "Alice Renamed"
    assert r.json()["result"]["status"] == "active"


async def test_patch_empty_body_rejected(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.patch(
        f"{BASE_ADMIN}/users/{setup.alice.id}", json={}, headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 422


# ── GET /admin/roles：三内置角色只读（`==3` 复跑）──


async def test_admin_roles_three_builtin(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    await _login(platform_client, "admin@acme.com")
    r = await platform_client.get(f"{BASE_ADMIN}/roles")
    assert r.status_code == 200
    roles = {x["code"]: x for x in r.json()["result"]}
    assert set(roles) == {"platform_super_admin", "account_admin", "user"}
    assert (
        roles["platform_super_admin"]["rank"] == 3
        and roles["account_admin"]["rank"] == 2
        and roles["user"]["rank"] == 1
    )
    assert (
        roles["account_admin"]["ov_base_role"] == "admin"
        and roles["user"]["ov_base_role"] == "user"
        and roles["platform_super_admin"]["ov_base_role"] is None
    )


# ── 管理员 API Key 元数据只读/撤销（04 §10.3；P1-E4 未合并前直用 E1 repository）──


async def test_admin_api_key_metadata_and_revoke(session: AsyncSession, platform_client) -> None:
    setup = await _seed(session)
    key1 = await create_api_key(setup.repo, session, setup.acme, setup.alice, name="Codex")
    csrf = await _login(platform_client, "admin@acme.com")

    r = await platform_client.get(f"{BASE_ADMIN}/users/{setup.alice.id}/api-keys")
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Codex"
    assert len(items[0]["key_last_four"]) == 4  # 仅末四位掩码
    text = r.text
    assert "api_key" not in text and "key_hash" not in text and "secret" not in text

    r = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.alice.id}/api-keys/{key1.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json()["result"]["revoked"] is True
    # 重复撤销 → 404（幂等，spike ==9）
    r = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.alice.id}/api-keys/{key1.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
    # 跨 Account 目标不可见 → 404
    r = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.psa.id}/api-keys/{key1.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


async def test_admin_audit_events_account_scoped(session: AsyncSession, platform_client) -> None:
    """GET /admin/audit-events 仅返回当前 Account 审计（05 §12.6）。"""
    setup = await _seed(session)
    psa_csrf = await _login(platform_client, "psa@platform.local")
    await _create_account(
        platform_client, psa_csrf, code="beta", email="badmin@beta.com", username="badmin"
    )
    await _login(platform_client, "admin@acme.com")
    r = await platform_client.get(f"{BASE_ADMIN}/audit-events")
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    assert items  # admin@acme 登录等审计
    assert all(i["account_id"] == str(setup.acme.id) for i in items)
    assert all(i["actor_user_id"] == str(setup.admin.id) for i in items)
