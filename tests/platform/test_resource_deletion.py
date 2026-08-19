"""P2-E3 Resource 删除/恢复测试（09 §45，14 号计划 §97.3）。

验收映射：
- AC②：删除按 09 §45.2 六步事务（pending_deletion + deletion_jobs +
  Watch 暂停 + 协作取消 + 列表/Search 排除），物理清理仅 30 天后由
  Purge Worker 执行；
- AC③：旧 Operation generation 过期只能记录终态、不能切换
  active_generation 或复活删除中对象（09 §45.3/§48 #10）；
- 恢复：仅回收期内、类型化权限（05 §12.6 注）、无成功版本保持 failed、
  Watch 保持 paused（09 §45.3）。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import (
    IamAuditEvent,
    IamDeletionJob,
    PlatformContentRef,
    PlatformOperationRef,
    PlatformResourceWatch,
)
from tests.platform.helpers import build_auth_setup
from tests.platform.test_resource_api import (
    BASE_ACCOUNT,
    BASE_ME,
    _csrf,
    _import,
    _login,
    _upload,
    _upload_and_import,
)

ALICE = "alice@acme.com"
ADMIN = "admin@acme.com"
PSA = "psa@platform.local"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _import_web(client: httpx.AsyncClient, csrf: str, url: str, path: str = f"{BASE_ME}/resources/imports") -> str:
    batch = await _import(client, csrf, path, [{"source_url": url}])
    return batch["items"][0]["resource_id"]


async def _delete(client: httpx.AsyncClient, csrf: str, path: str) -> dict:
    r = await client.delete(path, headers=_csrf(csrf))
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ═══════════════════════════════════════════════════════════════════════
# 删除预览（09 §45.1）
# ═══════════════════════════════════════════════════════════════════════


async def test_deletion_preview_content(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="preview.txt",
    )
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/deletion-preview")
    assert r.status_code == 200, r.text
    preview = r.json()["result"]
    assert preview["name"] == "preview.txt"
    assert preview["visibility"] == "user_private"
    assert preview["content"]["node_count"] == 1
    assert preview["restore_until"] is not None


async def test_deletion_preview_shared_impact_note(session: AsyncSession, platform_client) -> None:
    """共享 Resource 预览含成员影响提示（09 §45.1）。"""
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)
    res_id = await _import_web(platform_client, admin_csrf, "https://del.example.com/docs", f"{BASE_ACCOUNT}/resources/imports")
    r = await platform_client.get(f"{BASE_ACCOUNT}/resources/{res_id}/deletion-preview")
    preview = r.json()["result"]
    assert preview["visibility"] == "account_shared"
    assert "Account 成员" in (preview["shared_impact_note"] or "")


# ═══════════════════════════════════════════════════════════════════════
# AC②：删除六步事务
# ═══════════════════════════════════════════════════════════════════════


async def test_delete_six_step_transaction(session: AsyncSession, platform_client) -> None:
    """AC②：pending_deletion + deletion_jobs + Watch 暂停 + 协作取消 +
    列表/Search 排除；物理清理仅 30 天后由 Purge Worker 执行。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://del6.example.com/docs")
    # 配置 Watch + 制造一个可取消在途 Operation
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch", json={"interval_minutes": 60}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200

    result = await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    job_id = result["deletion_job_id"]

    # 步骤 2：pending_deletion + iam_deletion_jobs
    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    assert ref.status == "pending_deletion"
    assert ref.deleted_at is not None
    job = (
        await session.execute(select(IamDeletionJob).where(IamDeletionJob.id == job_id))
    ).scalar_one()
    assert job.resource_type == "resource"
    assert job.resource_id == str(res_id)
    assert job.purge_after > job.deleted_at

    # 步骤 3：Watch 立即暂停（09 §45.2）
    watch = (
        await session.execute(
            select(PlatformResourceWatch).where(PlatformResourceWatch.resource_id == res_id)
        )
    ).scalar_one()
    assert watch.state == "paused"

    # 步骤 5：正常列表/详情/Search 排除（删除中统一 404/不出现）
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 404
    r = await platform_client.get(f"{BASE_ME}/resources")
    assert all(i["id"] != f"res_{res_id}" for i in r.json()["result"]["items"])

    # 删除后新的 Refresh/Watch 调度被拒绝（步骤 3）
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/refresh", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 404

    # 幂等删除：返回同一 job（AC⑧ 语义）
    again = await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    assert again["deletion_job_id"] == job_id


async def test_delete_cancels_inflight_operations(session: AsyncSession, platform_client) -> None:
    """09 §45.2 步骤 4：对可取消在途 Operation 发出协作取消。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://del4.example.com/docs")
    # 制造一个 running Operation
    await session.commit()
    op = PlatformOperationRef(
        account_id=(await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))).scalar_one().account_id,
        operation_kind="task",
        operation_type="resource_refresh",
        ov_operation_id=f"ov-res-{uuid.uuid4()}",
        target_type="resource",
        target_id=str(res_id),
        target_visibility="user_private",
        status="running",
        cancellable=True,
        generation=2,
    )
    session.add(op)
    await session.commit()

    await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    await session.commit()
    cancelled = (
        await session.execute(
            select(PlatformOperationRef)
            .where(PlatformOperationRef.id == op.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert cancelled.status == "cancelling"


async def test_purge_worker_physical_cleanup_after_30_days(session: AsyncSession, platform_client) -> None:
    """AC②：物理清理仅 30 天后由 Purge Worker 执行；审计不随清理。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="purge.txt",
    )
    await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")

    # 未到期：Worker 不领取（30 天后才物理清理）
    await session.commit()
    from openviking.server.platform.deletion.worker import PurgeWorker
    from openviking.server.platform.iam import PostgresIamRepository
    from openviking.server.platform.registry.repository import RegistryRepository
    from openviking.server.platform.resource.execution import FakeResourceExecutionPlane
    from openviking.server.platform.resource.purge import ResourcePurgeHandler

    execution = FakeResourceExecutionPlane()
    worker = PurgeWorker(PostgresIamRepository(), RegistryRepository())
    worker.register_handler("resource", ResourcePurgeHandler(execution=execution))
    processed = await worker.run_once(session, limit=10)
    assert processed == 0

    # 到期后 Worker 物理清理（执行面内容 + Watch 行 + 引用标记 deleted）
    job = (
        await session.execute(select(IamDeletionJob).where(IamDeletionJob.resource_id == str(res_id)))
    ).scalar_one()
    job.purge_after = job.purge_after - timedelta(days=31)
    await session.commit()
    processed = await worker.run_once(session, limit=10)
    assert processed == 1
    await session.commit()
    job = (
        await session.execute(select(IamDeletionJob).where(IamDeletionJob.resource_id == str(res_id)))
    ).scalar_one()
    assert job.status == "purged"
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    assert ref.status == "deleted"

    # Purge 审计（actor_type=system，审计不随物理清理）
    audit = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.action == "purge.resource")
    )
    assert len(list(audit.scalars())) >= 1


# ═══════════════════════════════════════════════════════════════════════
# AC③：旧 generation 不能切换内容/复活删除中对象
# ═══════════════════════════════════════════════════════════════════════


async def test_stale_generation_completion_cannot_switch_content(session: AsyncSession, platform_client) -> None:
    """AC③：旧 Operation generation 过期只能记录终态，不能切换 active_generation。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://gen.example.com/docs")
    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    assert ref.active_generation == 1

    service = platform_client._transport.app.state.iam_resource_service
    from openviking.server.platform.registry.service import ContentRegistryService

    # gen=2 的 Refresh 在途；gen=1 的旧任务晚到完成
    stale_op = await ContentRegistryService().create_operation(
        session,
        account_id=ref.account_id,
        operation_kind="task",
        operation_type="resource_refresh",
        ov_operation_id=f"ov-res-{uuid.uuid4()}",
        target_type="resource",
        target_id=str(res_id),
        target_visibility="user_private",
        owner_user_id=ref.owner_user_id,
        actor_user_id=ref.owner_user_id,
        generation=1,
        cancellable=True,
    )
    fresh_op = await ContentRegistryService().create_operation(
        session,
        account_id=ref.account_id,
        operation_kind="task",
        operation_type="resource_refresh",
        ov_operation_id=f"ov-res-{uuid.uuid4()}",
        target_type="resource",
        target_id=str(res_id),
        target_visibility="user_private",
        owner_user_id=ref.owner_user_id,
        actor_user_id=ref.owner_user_id,
        generation=2,
        cancellable=True,
    )
    await session.commit()

    # 旧任务（gen=1）成功晚到：只记录终态，不切换 active_generation
    await service.complete_operation(
        session, operation_id=stale_op.id, status="succeeded", stage="succeeded"
    )
    await session.commit()
    ref = (
        await session.execute(
            select(PlatformContentRef)
            .where(PlatformContentRef.id == res_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert ref.active_generation == 1
    stale = (
        await session.execute(
            select(PlatformOperationRef)
            .where(PlatformOperationRef.id == stale_op.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert stale.status == "succeeded"  # 终态已记录

    # 新任务（gen=2）成功 → active_generation=2
    await service.complete_operation(
        session, operation_id=fresh_op.id, status="succeeded", stage="succeeded"
    )
    await session.commit()
    ref = (
        await session.execute(
            select(PlatformContentRef)
            .where(PlatformContentRef.id == res_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert ref.active_generation == 2


async def test_late_result_cannot_reactivate_deleted_resource(
    session: AsyncSession, session_factory, platform_client
) -> None:
    """AC③/09 §45.3：删除期间晚到的 Task 结果不能重新激活对象（§48 #10）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://late.example.com/docs")
    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()

    # 在途 Refresh（gen=2）
    from openviking.server.platform.registry.service import ContentRegistryService

    inflight = await ContentRegistryService().create_operation(
        session,
        account_id=ref.account_id,
        operation_kind="task",
        operation_type="resource_refresh",
        ov_operation_id=f"ov-res-{uuid.uuid4()}",
        target_type="resource",
        target_id=str(res_id),
        target_visibility="user_private",
        owner_user_id=ref.owner_user_id,
        actor_user_id=ref.owner_user_id,
        generation=2,
        cancellable=True,
    )
    await session.commit()

    # 删除对象
    await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    await session.commit()

    # 晚到的成功结果 → Operation 记录终态，但对象保持 pending_deletion（不复活）
    # Worker 使用独立 Session（生产语义），避免测试 Session 的 identity map 陈旧值
    service = platform_client._transport.app.state.iam_resource_service
    async with session_factory() as worker_session:
        await service.complete_operation(
            worker_session, operation_id=inflight.id, status="succeeded", stage="succeeded"
        )
        await worker_session.commit()
    ref = (
        await session.execute(
            select(PlatformContentRef)
            .where(PlatformContentRef.id == res_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert ref.status == "pending_deletion"
    finished = (
        await session.execute(
            select(PlatformOperationRef)
            .where(PlatformOperationRef.id == inflight.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert finished.status == "succeeded"


# ═══════════════════════════════════════════════════════════════════════
# 恢复（09 §45.3：仅回收期内、类型化权限、Watch 保持 paused）
# ═══════════════════════════════════════════════════════════════════════


async def test_restore_keeps_watch_paused_and_requires_permission(
    session: AsyncSession, platform_client
) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import_web(platform_client, alice_csrf, "https://restore.example.com/docs")
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch", json={"interval_minutes": 60}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200
    result = await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    job_id = result["deletion_job_id"]

    # 恢复后回到 active；Watch 保持 paused（09 §45.3，AC⑨ 恢复表）
    r = await platform_client.post(
        f"/api/platform/v1/recycle-bin/{job_id}/restore", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    assert ref.status == "active"
    watch = (
        await session.execute(
            select(PlatformResourceWatch).where(PlatformResourceWatch.resource_id == res_id)
        )
    ).scalar_one()
    assert watch.state == "paused"
    # 恢复动作写审计（09 §47.2）
    audit = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.action == "resource.restore")
    )
    assert len(list(audit.scalars())) == 1


async def test_restore_window_expired_rejected(session: AsyncSession, platform_client) -> None:
    """09 §45.3：仅回收期内可恢复；期满 → RESOURCE_RESTORE_WINDOW_EXPIRED。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="expired.txt",
    )
    result = await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    await session.commit()
    job = (
        await session.execute(select(IamDeletionJob).where(IamDeletionJob.id == result["deletion_job_id"]))
    ).scalar_one()
    job.purge_after = job.purge_after - timedelta(days=31)
    await session.commit()
    r = await platform_client.post(
        f"/api/platform/v1/recycle-bin/{result['deletion_job_id']}/restore", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_RESTORE_WINDOW_EXPIRED"


async def test_restore_failed_placeholder_stays_failed(session: AsyncSession, platform_client) -> None:
    """09 §45.3：没有成功版本的失败占位对象恢复后不能变为可用内容。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    upload_id = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="fail-restore.txt", content=b"x"
    )
    batch = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports", [{"upload_id": upload_id}]
    )
    res_id = batch["items"][0]["resource_id"]
    result = await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    r = await platform_client.post(
        f"/api/platform/v1/recycle-bin/{result['deletion_job_id']}/restore", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200
    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    assert ref.status == "failed"


async def test_shared_resource_restore_admin_and_owner_rules(session: AsyncSession, platform_client) -> None:
    """05 §12.6 注：共享 Resource 恢复权限 = Account Admin（本 Account）。"""
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)
    res_id = await _import_web(
        platform_client, admin_csrf, "https://shared-restore.example.com/docs", f"{BASE_ACCOUNT}/resources/imports"
    )
    result = await _delete(platform_client, admin_csrf, f"{BASE_ACCOUNT}/resources/{res_id}")
    job_id = result["deletion_job_id"]

    # 普通 User 恢复共享 → 403 PERMISSION_NOT_GRANTED（无恢复权限）
    alice_csrf = await _login(platform_client, ALICE)
    r = await platform_client.post(
        f"/api/platform/v1/recycle-bin/{job_id}/restore", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"

    # Account Admin 恢复共享成功
    admin_csrf = await _login(platform_client, ADMIN)
    r = await platform_client.post(
        f"/api/platform/v1/recycle-bin/{job_id}/restore", headers=_csrf(admin_csrf)
    )
    assert r.status_code == 200, r.text
    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == res_id))
    ).scalar_one()
    assert ref.status == "active"


async def test_member_private_resource_cannot_be_restored_by_admin(
    session: AsyncSession, platform_client
) -> None:
    """05 §12.6 注：其他 User 的私有 Resource 不允许恢复（admin 无恢复权限）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="member-del.txt",
    )
    result = await _delete(platform_client, alice_csrf, f"{BASE_ME}/resources/{res_id}")
    # 05 §12.6 注：其他 User 的私有 Resource 不允许恢复 → 403（类型化权限拒绝）
    admin_csrf = await _login(platform_client, ADMIN)
    r = await platform_client.post(
        "/api/platform/v1/admin/recycle-bin/{job_id}/restore".format(job_id=result["deletion_job_id"]),
        headers=_csrf(admin_csrf),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"
