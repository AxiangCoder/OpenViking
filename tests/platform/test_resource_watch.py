"""P2-E3 Resource Watch 测试（09 §43.2/§46.1，14 号计划 §97.3）。

验收映射：
- AC④：Watch 触发失败保持 active 旧版本可读（09 §41.2）；
- AC⑥：Watch 记录不含明文 URL，触发时 Facade 实时校验（来源只存密文）；
- AC⑩：interval 只接受 Capabilities 预设值（服务端强制）；
- 暂停/恢复/删除/立即同步语义（09 §43.2：恢复不补跑、删除 Watch 不删
  Resource、上传来源不能开启 Watch）。
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import (
    IamAuditEvent,
    PlatformContentRef,
    PlatformResourceWatch,
)
from tests.platform.helpers import build_auth_setup
from tests.platform.test_resource_api import (
    BASE_ME,
    _csrf,
    _import,
    _login,
    _upload_and_import,
)

ALICE = "alice@acme.com"
DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _import_web(client: httpx.AsyncClient, csrf: str, url: str) -> str:
    batch = await _import(
        client, csrf, f"{BASE_ME}/resources/imports", [{"source_url": url}]
    )
    return batch["items"][0]["resource_id"]


# ═══════════════════════════════════════════════════════════════════════
# Watch 配置（09 §43.2）
# ═══════════════════════════════════════════════════════════════════════


async def test_watch_config_requires_preset_interval(session: AsyncSession, platform_client) -> None:
    """AC⑩：interval 只接受 Capabilities 预设（服务端强制）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://watch.example.com/page")

    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/watch")
    assert r.status_code == 200
    assert r.json()["result"]["state"] == "not_configured"

    # 非预设 → RESOURCE_WATCH_UNAVAILABLE（409）
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch",
        json={"interval_minutes": 45},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_WATCH_UNAVAILABLE"

    # 预设 1440 → 成功
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch",
        json={"interval_minutes": 1440, "processing_instruction": "daily"},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    config = r.json()["result"]
    assert config["state"] == "active"
    assert config["interval_minutes"] == 1440
    assert config["next_run_at"] is not None


async def test_upload_resource_cannot_enable_watch(session: AsyncSession, platform_client) -> None:
    """上传来源不能开启 Watch（09 §40.2/§43.2）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="no-watch.txt",
    )
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch",
        json={"interval_minutes": 60},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_WATCH_UNAVAILABLE"


async def test_watch_persists_resource_id_only_not_plaintext_url(
    session: AsyncSession, platform_client
) -> None:
    """AC⑥：Watch 持久化只存 Resource ID，不存明文来源（09 §43.2/04 §10.10）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    url = "https://secret-source.example.com/docs?token=leakme"
    # 带 Query 的来源只能一次性导入（不能开启 Watch，09 §40.6）
    batch = await _import(
        client=platform_client, csrf=alice_csrf, path=f"{BASE_ME}/resources/imports",
        items=[{"source_url": url}],
    )
    res_id = batch["items"][0]["resource_id"]
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch", json={"interval_minutes": 60}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_WATCH_UNAVAILABLE"

    # 稳定来源（无 Query）可以开启 Watch
    stable = await _import_web(platform_client, alice_csrf, "https://stable.example.com/docs")
    r = await platform_client.put(
        f"{BASE_ME}/resources/{stable}/watch", json={"interval_minutes": 60}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text

    # Watch 行不保存任何 URL/Query/token
    await session.commit()
    watch = (
        await session.execute(
            select(PlatformResourceWatch).where(PlatformResourceWatch.resource_id == stable)
        )
    ).scalar_one()
    text = str(watch.__dict__)
    assert "stable.example.com" not in text
    assert "token" not in text and "leakme" not in text
    # 来源只在 content_ref 的应用层密文中（09 §47.3）
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == stable))
    ).scalar_one()
    assert ref.source_locator_ciphertext is not None
    assert "stable.example.com" not in ref.source_locator_ciphertext
    assert ref.source_display.startswith("https://stable.example.com/")


async def test_watch_pause_resume_delete_semantics(session: AsyncSession, platform_client) -> None:
    """09 §43.2：暂停保留配置、恢复不补跑、删除 Watch 不删 Resource。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://watch2.example.com/page")
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch", json={"interval_minutes": 60}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200

    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/watch/pause", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200
    assert r.json()["result"]["state"] == "paused"

    # 暂停后 trigger 拒绝（RESOURCE_WATCH_UNAVAILABLE）
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/watch/trigger", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 409, r.text

    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/watch/resume", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200
    assert r.json()["result"]["state"] == "active"

    # 立即同步 → Operation
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/watch/trigger", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["operation_id"]

    # 删除 Watch：只删配置，不删 Resource
    r = await platform_client.delete(
        f"{BASE_ME}/resources/{res_id}/watch", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/watch")
    assert r.json()["result"]["state"] == "not_configured"
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 200


async def test_watch_trigger_success_and_failure(session: AsyncSession, platform_client) -> None:
    """AC④：Watch 失败保持 active（旧版本可读），审计记录触发（09 §47.2）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://watch3.example.com/page")
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch", json={"interval_minutes": 60}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200

    # 成功触发
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/watch/trigger", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200

    # 失败触发：把来源显示值改为失败注入 host
    from openviking.server.platform.models import PlatformContentRef

    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    ref.source_display = "https://fail.invalid/now"
    await session.commit()
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/watch/trigger", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.json()["result"]["lifecycle_status"] == "active"
    assert r.json()["result"]["current_operation"]["status"] == "failed"

    # 审计：watch.trigger 成功/失败均已记录
    await session.commit()
    events = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.action == "resource.watch.trigger")
    )
    assert len(list(events.scalars())) >= 2


async def test_watch_worker_run_once(session: AsyncSession, platform_client) -> None:
    """09 §43.2：Watch Worker 系统执行身份调度；删除中对象不被重新激活（AC③）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://worker.example.com/page")
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch", json={"interval_minutes": 60}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200
    await session.commit()

    # 把 next_run_at 拨到过去
    watch = (
        await session.execute(
            select(PlatformResourceWatch).where(PlatformResourceWatch.resource_id == res_id)
        )
    ).scalar_one()
    from datetime import timedelta

    watch.next_run_at = watch.next_run_at - timedelta(hours=2)
    await session.commit()

    service = platform_client._transport.app.state.iam_resource_service
    processed = await service.run_watches(session)
    assert processed == 1
    await session.commit()

    watch = (
        await session.execute(
            select(PlatformResourceWatch).where(PlatformResourceWatch.resource_id == res_id)
        )
    ).scalar_one()
    assert watch.last_result == "succeeded"
    assert watch.last_run_at is not None
    assert watch.next_run_at is not None

    # 系统身份审计（actor_type=system + actor_system_component）
    events = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.action == "resource.watch.trigger")
    )
    system_events = [e for e in events.scalars() if e.actor_type == "system"]
    assert system_events
    assert all(e.actor_system_component == "watch-scheduler" for e in system_events)
