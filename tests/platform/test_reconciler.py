"""P2-E1 Reconciler 测试（14 号计划 §97.1：卡死恢复、对账）。

验收映射：
- AC⑦：Reconciler 恢复卡死事件且不重复执行；
- AC⑧：控制面同步与 PG ov 映射一致（对账后差异为零/重放后 namespace 存在即通过）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import platform_config
from openviking.server.platform.iam import PostgresIamRepository
from openviking.server.platform.models import IamOutbox
from openviking.server.platform.provisioning.control_plane import FakeControlPlane
from openviking.server.platform.provisioning.reconciler import ProvisioningReconciler
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.provisioning.worker import ProvisioningWorker
from tests.platform.helpers import build_auth_setup


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _reconciler(session: AsyncSession, control: FakeControlPlane):
    return ProvisioningReconciler(PostgresIamRepository(), ProvisioningRepository(), control)


async def _create_provisioned_account(session: AsyncSession, *, code: str = "beta"):
    """干净环境（仅 PSA）：创建 Account+首位 Admin 并用 Worker 置为 active。

    不经过 helpers.build_auth_setup（其直建 active 的 acme 会污染对账对比）。
    """
    from openviking.server.platform.admin.service import AdminService
    from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
    from openviking.server.platform.auth.service import AuthService
    from openviking.server.platform.iam.service import RbacService
    from tests.platform.helpers import create_user

    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    await rbac.seed_catalog(session)
    psa = await create_user(repo, session, None, email="psa@platform.local", username="psa")
    await session.commit()
    await rbac.assign_platform_super_admin(session, target_user_id=psa.id)
    await session.commit()

    service = AdminService(repo, rbac, AuthService(repo, rbac))
    result = await service.create_account_with_first_admin(
        session,
        actor=AuthenticatedUserPrincipal(
            actor_user_id=psa.id,
            actor_account_id=None,
            actor_ov_user_id=None,
            actor_ov_account_id=None,
            user_status="active",
            authentication_method="session",
        ),
        account_code=code,
        account_name=code,
        admin_email=f"badmin@{code}.com",
        admin_username="badmin",
    )
    await session.commit()
    control = FakeControlPlane()
    worker = ProvisioningWorker(repo, ProvisioningRepository(), control)
    assert await worker.run_once(session) == 2
    return repo, result, control


# ── AC⑦：卡死事件恢复 ──


async def test_recover_stuck_processing_events(session: AsyncSession) -> None:
    """processing 超时事件重置 pending，由 Worker 重新领取执行一次。"""
    repo, result, control = await _create_provisioned_account(session, code="stuck")
    outbox = ProvisioningRepository()
    events = list((await session.execute(select(IamOutbox))).scalars())
    assert all(e.status == "completed" for e in events)

    # 人为制造卡死：完成事件改回 processing + 超时 processing_started_at
    stale = _now() - timedelta(
        seconds=platform_config.provisioning_stuck_timeout_seconds + 60
    )
    stuck_event = events[0]
    await outbox.update_status(
        session,
        stuck_event,
        status="processing",
        now=_now(),
        attempts=1,
        processing_started_at=stale,
    )
    recent_event = events[1]
    await outbox.update_status(
        session,
        recent_event,
        status="processing",
        now=_now(),
        attempts=1,
        processing_started_at=_now(),
    )
    await session.commit()

    reconciler = await _reconciler(session, control)
    recovered = await reconciler.recover_stuck(session)
    assert recovered == 1  # 仅超时事件被恢复；近期 processing 不受影响
    row = await outbox.get(session, stuck_event.id)
    assert row.status == "pending"
    assert row.processing_started_at is None
    recent = await outbox.get(session, recent_event.id)
    assert recent.status == "processing"


async def test_recover_stuck_then_worker_processes_once(session: AsyncSession) -> None:
    """AC⑦：恢复后 Worker 领取执行，且不重复执行（仅一次 → completed）。"""
    repo, result, control = await _create_provisioned_account(session, code="gamma")
    outbox = ProvisioningRepository()
    event = (
        await session.execute(select(IamOutbox).where(IamOutbox.event_type == "account.provision"))
    ).scalar_one()
    stale = _now() - timedelta(
        seconds=platform_config.provisioning_stuck_timeout_seconds + 60
    )
    await outbox.update_status(
        session, event, status="processing", now=_now(), attempts=1, processing_started_at=stale
    )
    await session.commit()

    reconciler = await _reconciler(session, control)
    assert await reconciler.recover_stuck(session) == 1

    # 恢复后控制面 namespace 已存在（重放幂等，AC③）：Worker 处理仍成功且不重复
    worker = ProvisioningWorker(repo, outbox, control)
    assert await worker.run_once(session) == 1
    row = await outbox.get(session, event.id)
    assert row.status == "completed"
    # 不重复执行：已 completed 不再领取；恢复再次执行为 0
    assert await worker.run_once(session) == 0
    assert await reconciler.recover_stuck(session) == 0


# ── AC⑧：控制面对账（差异为零/重放后 namespace 存在）──


async def test_reconcile_in_sync_when_no_drift(session: AsyncSession) -> None:
    """正常 provisioning 后：PG ov 映射与控制面一致，对账差异为零。"""
    _, result, control = await _create_provisioned_account(session, code="delta")
    reconciler = await _reconciler(session, control)
    report = await reconciler.reconcile(session)
    assert report.in_sync is True
    assert report.missing_accounts == ()
    assert report.missing_users == ()
    assert report.requeued_events == 0


async def test_reconcile_requeues_missing_and_reaches_zero(session: AsyncSession) -> None:
    """AC⑧：控制面丢失 namespace（漂移）→ 对账重放事件 → Worker 重放后
    namespace 存在 → 再次对账差异为零。"""
    repo, result, control = await _create_provisioned_account(session, code="eps")
    control.reset()  # 模拟控制面 namespace 丢失

    reconciler = await _reconciler(session, control)
    report = await reconciler.reconcile(session)
    assert report.in_sync is False
    assert len(report.missing_accounts) == 1
    assert len(report.missing_users) == 1
    assert report.requeued_events == 2

    # 防重复入队：再次对账（未执行 Worker）不再新增事件
    report2 = await reconciler.reconcile(session)
    assert report2.requeued_events == 0

    # 重放后 namespace 存在即通过
    worker = ProvisioningWorker(repo, ProvisioningRepository(), control)
    assert await worker.run_once(session) == 2
    assert result.account.ov_account_id in await control.list_provisioned_accounts()
    assert (
        result.admin.ov_user_id
        in await control.list_provisioned_users(result.account.ov_account_id)
    )

    final = await reconciler.reconcile(session)
    assert final.in_sync is True
    assert final.requeued_events == 0


async def test_reconcile_only_considers_active(session: AsyncSession) -> None:
    """provisioning/failed 状态的 Account/User 不参与对账（产品请求门禁已拦截）。"""
    from openviking.server.platform.admin.service import AdminService
    from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal

    setup = await build_auth_setup(session)
    service = AdminService(setup.repo, setup.rbac, setup.auth)
    result = await service.create_account_with_first_admin(
        session,
        actor=AuthenticatedUserPrincipal(
            actor_user_id=setup.psa.id,
            actor_account_id=None,
            actor_ov_user_id=None,
            actor_ov_account_id=None,
            user_status="active",
            authentication_method="session",
        ),
        account_code="semi",
        account_name="Semi",
        admin_email="sadmin@semi.com",
        admin_username="sadmin",
    )
    await session.commit()  # Account/Admin 仍 provisioning（Worker 未运行）

    control = FakeControlPlane()
    reconciler = await _reconciler(session, control)
    report = await reconciler.reconcile(session)
    # provisioning 目标不进入 missing（未 active）；仅 helpers 直建的 acme 成员进入
    assert str(result.account.id) not in report.missing_accounts
    assert all(acc != result.account.ov_account_id for acc, _uid in report.missing_users)
    assert report.requeued_events > 0  # acme(active) 与控制面不一致被重放
