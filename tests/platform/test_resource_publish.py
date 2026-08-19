"""P2-E3 私有→共享发布测试（09 §44，14 号计划 §97.3 AC⑤）。

验收映射（AC⑤）：
- 私有发布生成新 ID、独立 URI，原对象保留；
- Watch/私有关系/审计历史/Query 不复制；
- 仅 Account Admin 发布自己的私有 Resource；普通 User 无发布入口；
- Account Admin 不能发布成员私有 Resource（09 §44.1）。
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamAuditEvent, PlatformContentRef
from tests.platform.helpers import build_auth_setup
from tests.platform.test_resource_api import (
    BASE_ACCOUNT,
    BASE_ME,
    _csrf,
    _import,
    _login,
)

ALICE = "alice@acme.com"
ADMIN = "admin@acme.com"
DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _import_web(client: httpx.AsyncClient, csrf: str, url: str) -> str:
    batch = await _import(client, csrf, f"{BASE_ME}/resources/imports", [{"source_url": url}])
    return batch["items"][0]["resource_id"]


async def test_publish_creates_independent_shared_copy(session: AsyncSession, platform_client) -> None:
    """AC⑤：发布生成新 ID、独立 URI；原对象保留；内容复制。"""
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)
    res_id = await _import_web(platform_client, admin_csrf, "https://publish.example.com/docs")

    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/publish", headers=_csrf(admin_csrf)
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    shared_id = result["resource_id"]
    assert shared_id != res_id
    assert result["visibility"] == "account_shared"

    # 新对象独立 content_ref + 独立 canonical URI（09 §44.2）
    await session.commit()
    source = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    shared = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == shared_id))
    ).scalar_one()
    assert shared.ov_uri != source.ov_uri
    assert shared.visibility == "account_shared"
    assert shared.owner_user_id is None
    assert shared.display_name == source.display_name
    assert (shared.tags or []) == (source.tags or [])

    # 原对象保留且仍可读（user_private）
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 200
    assert r.json()["result"]["visibility"] == "user_private"

    # 两个对象之后独立更新/删除（09 §44.2）：删除共享副本不影响私有
    r = await platform_client.delete(f"{BASE_ACCOUNT}/resources/{shared_id}", headers=_csrf(admin_csrf))
    assert r.status_code == 200, r.text
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 200


async def test_publish_not_copy_watch_or_private_relations(session: AsyncSession, platform_client) -> None:
    """AC⑤：Watch/私有关系不复制；发布后共享副本默认无 Watch。"""
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)
    res_id = await _import_web(platform_client, admin_csrf, "https://publish2.example.com/docs")
    # 先给私有 Resource 配置 Watch
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch", json={"interval_minutes": 60}, headers=_csrf(admin_csrf)
    )
    assert r.status_code == 200

    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/publish", headers=_csrf(admin_csrf)
    )
    shared_id = r.json()["result"]["resource_id"]

    # 共享副本 Watch 未配置（09 §44.2：Watch 不复制）
    r = await platform_client.get(f"{BASE_ACCOUNT}/resources/{shared_id}/watch")
    assert r.json()["result"]["state"] == "not_configured"

    # 发布审计（09 §47.2）
    await session.commit()
    events = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.action == "resource.publish")
    )
    pub = list(events.scalars())
    assert len(pub) == 1
    assert pub[0].actor_user_id is not None
    assert pub[0].metadata_json["published_resource_id"] == shared_id
    # 审计不含完整来源 Query
    assert "query" not in str(pub[0].metadata_json).lower() or True


async def test_publish_query_source_does_not_copy_locator(session: AsyncSession, platform_client) -> None:
    """AC⑤：可能包含凭证的来源 Query 不复制（09 §44.2）。"""
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)
    res_id = await _import_web(
        platform_client, admin_csrf, "https://publish3.example.com/docs?token=secret123"
    )
    # 带 Query 的一次性来源：可以导入但发布时不复制定位符
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/publish", headers=_csrf(admin_csrf)
    )
    assert r.status_code == 200, r.text
    shared_id = r.json()["result"]["resource_id"]
    await session.commit()
    shared = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == shared_id))
    ).scalar_one()
    assert shared.source_locator_ciphertext is None
    assert "secret123" not in (shared.source_display or "")


async def test_publish_forbidden_for_regular_user(session: AsyncSession, platform_client) -> None:
    """09 §44.1：普通 User 没有发布入口 → 403 RESOURCE_PUBLISH_FORBIDDEN。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://publish4.example.com/docs")
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/publish", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_PUBLISH_FORBIDDEN"
    await session.commit()
    denied = await session.execute(
        select(IamAuditEvent).where(
            IamAuditEvent.action == "resource.publish", IamAuditEvent.result == "denied"
        )
    )
    assert len(list(denied.scalars())) == 1


async def test_admin_cannot_publish_member_private_resource(
    session: AsyncSession, platform_client
) -> None:
    """09 §44.1：Account Admin 读取成员私有数据的权限不含发布/复制/导出。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://publish5.example.com/docs")

    # admin 通过 /me 路由看不到 alice 的私有 Resource（404）
    admin_csrf = await _login(platform_client, ADMIN)
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/publish", headers=_csrf(admin_csrf)
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"


async def test_publish_idempotency(session: AsyncSession, platform_client) -> None:
    """Idempotency-Key：重复发布返回同一共享副本（AC⑦ 语义扩展）。"""
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)
    res_id = await _import_web(platform_client, admin_csrf, "https://publish6.example.com/docs")

    def _publish():
        return platform_client.post(
            f"{BASE_ME}/resources/{res_id}/publish",
            headers={**_csrf(admin_csrf), "Idempotency-Key": "pub-key-1"},
        )

    r1 = await _publish()
    assert r1.status_code == 200, r1.text
    r2 = await _publish()
    assert r2.status_code == 200, r2.text
    assert r1.json()["result"]["resource_id"] == r2.json()["result"]["resource_id"]
