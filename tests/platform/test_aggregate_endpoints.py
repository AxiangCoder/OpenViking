"""P2-E2 admin/platform 聚合端点 API 测试（05 §12.6，14 号计划 §97.2，AC⑨）。

验收映射（AC⑨）：
- User deletion-preview/DELETE（admin，`user.delete`）与 Account
  deletion-preview/DELETE（platform，`account.delete`）进入回收期并返回
  deletion job ID；
- admin/platform 聚合 `/activity`、`/monitoring`、`/recycle-bin` 可用，
  权限按 05 §12.6 表分别校验；
- 回收站恢复按 05 §12.6 注类型化权限（user → admin `user.delete`；
  account → platform `account.delete`）。
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.iam.permissions import USER
from openviking.server.platform.registry.service import ContentRegistryService
from tests.platform.helpers import build_auth_setup, create_user

BASE_AUTH = "/api/platform/v1/auth"
BASE_ADMIN = "/api/platform/v1/admin"
BASE_PLATFORM = "/api/platform/v1/platform"


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    """登录并返回 CSRF Token（04 §10.7：仅在登录/改密响应中下发一次）。"""
    resp = await client.post(
        f"{BASE_AUTH}/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["csrf_token"]


def _headers(csrf: str | None = None) -> dict:
    return {"x-csrf-token": csrf} if csrf else {}


async def _seed_account_user(session: AsyncSession):
    """标准环境 acme(admin/alice) + PSA（平台范围断言直接使用既有 Account）。"""
    return await build_auth_setup(session)


# ── AC⑨：User deletion-preview / DELETE（admin，user.delete）──


async def test_admin_user_deletion_preview_and_delete(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    user_id = str(setup.alice.id)

    resp = await platform_client.get(
        f"{BASE_ADMIN}/users/{user_id}/deletion-preview", headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    preview = resp.json()["result"]
    assert preview["resource_type"] == "user"
    assert preview["recoverable"] is True

    resp = await platform_client.delete(
        f"{BASE_ADMIN}/users/{user_id}", headers=_headers(csrf)
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["deletion_job_id"] is not None
    assert result["restore_until"] is not None

    # 已删除用户从列表隐藏（05 §11.3）
    resp = await platform_client.get(f"{BASE_ADMIN}/users")
    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()["result"]["items"]]
    assert user_id not in ids

    # 幂等删除返回同一 job
    resp = await platform_client.delete(
        f"{BASE_ADMIN}/users/{user_id}", headers=_headers(csrf)
    )
    assert resp.json()["result"]["deletion_job_id"] == result["deletion_job_id"]


async def test_admin_user_delete_last_admin_conflict(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.admin.id}", headers=_headers(csrf)
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "LAST_ACCOUNT_ADMIN_REQUIRED"


async def test_admin_user_delete_forbidden_for_plain_user(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    await _login(platform_client, "alice@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.get(f"{BASE_ADMIN}/users/{setup.admin.id}/deletion-preview")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"


# ── AC⑨：Account deletion-preview / DELETE（platform，account.delete）──


async def test_platform_account_deletion_preview_and_delete(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await _seed_account_user(session)
    csrf = await _login(platform_client, "psa@platform.local", "Init-Pass-2026-Dev!")
    account_id = str(setup.acme.id)

    resp = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{account_id}/deletion-preview", headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["resource_type"] == "account"
    assert resp.json()["result"]["impacted"]["users"] >= 2

    resp = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{account_id}", headers=_headers(csrf)
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["deletion_job_id"] is not None

    # Account 从列表隐藏
    resp = await platform_client.get(f"{BASE_PLATFORM}/accounts")
    ids = [a["id"] for a in resp.json()["result"]["items"]]
    assert account_id not in ids


async def test_account_delete_requires_psa(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/deletion-preview"
    )
    assert resp.status_code == 403


# ── AC⑨：recycle-bin（类型化恢复权限，05 §12.6 注）──


async def test_admin_recycle_bin_list_and_restore(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    # 删除 alice
    resp = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.alice.id}", headers=_headers(csrf)
    )
    job_id = resp.json()["result"]["deletion_job_id"]

    resp = await platform_client.get(f"{BASE_ADMIN}/recycle-bin")
    assert resp.status_code == 200, resp.text
    items = resp.json()["result"]["items"]
    assert any(i["id"] == job_id for i in items)
    row = next(i for i in items if i["id"] == job_id)
    assert row["resource_type"] == "user"
    assert row["target_name"] == "alice"
    assert row["restore_allowed"] is True
    assert row["restore_permission"] == "user.delete"

    # 恢复
    resp = await platform_client.post(
        f"{BASE_ADMIN}/recycle-bin/{job_id}/restore", headers=_headers(csrf)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["resource_type"] == "user"

    # 重复恢复 → 409 ALREADY_RESTORED
    resp = await platform_client.post(
        f"{BASE_ADMIN}/recycle-bin/{job_id}/restore", headers=_headers(csrf)
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ALREADY_RESTORED"


async def test_admin_recycle_bin_restore_requires_user_delete(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """普通 User 不能恢复他人 User（05 §12.6 注：user → user.delete）。"""
    setup = await build_auth_setup(session)
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
    csrf = await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.delete(
        f"{BASE_ADMIN}/users/{bob.id}", headers=_headers(csrf)
    )
    job_id = resp.json()["result"]["deletion_job_id"]
    # 切换到 alice（普通 User，仍 active）
    csrf = await _login(platform_client, "alice@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.post(
        f"{BASE_ADMIN}/recycle-bin/{job_id}/restore", headers=_headers(csrf)
    )
    assert resp.status_code == 403


async def test_platform_recycle_bin_account_restore(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await _seed_account_user(session)
    csrf = await _login(platform_client, "psa@platform.local", "Init-Pass-2026-Dev!")
    resp = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}", headers=_headers(csrf)
    )
    job_id = resp.json()["result"]["deletion_job_id"]

    resp = await platform_client.get(f"{BASE_PLATFORM}/recycle-bin")
    assert resp.status_code == 200
    items = resp.json()["result"]["items"]
    row = next(i for i in items if i["id"] == job_id)
    assert row["resource_type"] == "account"
    assert row["restore_allowed"] is True

    resp = await platform_client.post(
        f"{BASE_PLATFORM}/recycle-bin/{job_id}/restore", headers=_headers(csrf)
    )
    assert resp.status_code == 200, resp.text


async def test_platform_recycle_bin_account_restore_denied_for_account_admin(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Account 恢复仅 Platform Super Admin（05 §12.6 注：account → account.delete）。"""
    setup = await _seed_account_user(session)
    csrf = await _login(platform_client, "psa@platform.local", "Init-Pass-2026-Dev!")
    resp = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}", headers=_headers(csrf)
    )
    job_id = resp.json()["result"]["deletion_job_id"]
    # Account Admin 尝试恢复 → 403
    csrf = await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.post(
        f"{BASE_PLATFORM}/recycle-bin/{job_id}/restore", headers=_headers(csrf)
    )
    assert resp.status_code == 403


# ── AC⑨：admin/platform /activity、/monitoring ──


async def test_admin_activity_and_monitoring(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await build_auth_setup(session)
    # 制造一条共享对象任务与一条私有任务
    registry = ContentRegistryService()
    await registry.create_operation(
        session,
        account_id=setup.acme.id,
        operation_kind="task",
        operation_type="resource_import",
        ov_operation_id="ov-task-shared-1",
        target_type="resource",
        target_id=str(uuid.uuid4()),
        target_visibility="account_shared",
        owner_user_id=None,
        actor_user_id=setup.admin.id,
    )
    await registry.create_operation(
        session,
        account_id=setup.acme.id,
        operation_kind="task",
        operation_type="resource_import",
        ov_operation_id="ov-task-private-1",
        target_type="resource",
        target_id=str(uuid.uuid4()),
        target_visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
    )
    await session.commit()

    await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.get(f"{BASE_ADMIN}/activity")
    assert resp.status_code == 200, resp.text
    items = resp.json()["result"]["items"]
    # 仅当前 Account 共享对象任务（05 §12.6：不默认暴露成员私有任务日志）
    assert len(items) == 1
    assert items[0]["target_visibility"] == "account_shared"
    assert items[0]["operation_type"] == "resource_import"
    # ov_operation_id 不返回为可枚举主 ID（04 §10.12）
    assert "ov_operation_id" not in items[0]

    resp = await platform_client.get(f"{BASE_ADMIN}/monitoring")
    assert resp.status_code == 200, resp.text
    summary = resp.json()["result"]["summary"]
    assert summary["accounts"] >= 1
    assert "content_refs_active_by_type" in summary


async def test_admin_monitoring_requires_monitoring_permission(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await build_auth_setup(session)  # acme 环境（alice 无 monitoring.read）
    await _login(platform_client, "alice@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.get(f"{BASE_ADMIN}/monitoring")
    assert resp.status_code == 403


async def test_platform_activity_filterable_by_account(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    setup = await _seed_account_user(session)
    registry = ContentRegistryService()
    await registry.create_operation(
        session,
        account_id=setup.acme.id,
        operation_kind="task",
        operation_type="resource_import",
        ov_operation_id="ov-task-a-1",
        target_type="resource",
        target_id=str(uuid.uuid4()),
        target_visibility="account_shared",
        owner_user_id=None,
        actor_user_id=setup.admin.id,
    )
    await session.commit()

    await _login(platform_client, "psa@platform.local", "Init-Pass-2026-Dev!")
    resp = await platform_client.get(f"{BASE_PLATFORM}/activity")
    assert resp.status_code == 200
    assert len(resp.json()["result"]["items"]) == 1

    resp = await platform_client.get(
        f"{BASE_PLATFORM}/activity", params={"account_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 200
    assert len(resp.json()["result"]["items"]) == 0

    resp = await platform_client.get(f"{BASE_PLATFORM}/monitoring")
    assert resp.status_code == 200
    assert resp.json()["result"]["summary"]["active_accounts"] >= 1


async def test_platform_activity_requires_task_read_platform(
    platform_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await build_auth_setup(session)  # acme 环境（admin 非 PSA，无 task.read.platform）
    await _login(platform_client, "admin@acme.com", "Init-Pass-2026-Dev!")
    resp = await platform_client.get(f"{BASE_PLATFORM}/activity")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"
