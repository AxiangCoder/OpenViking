"""P2-E1 outbox 数据层测试（04 §10.9，14 号计划 §97.1）。

覆盖：Account+首位 Admin 创建与 outbox 同一 PG 事务（AC① 的写入侧）、
outbox 事件生命周期（pending→processing→completed/failed）、
退避到期领取语义。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.admin.service import AdminService
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.iam import PostgresIamRepository
from openviking.server.platform.models import IamOutbox
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from tests.platform.helpers import build_auth_setup, run_provisioning


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _principal(user) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=None,
        user_status="active",
        authentication_method="session",
    )


async def _create_account_with_admin(session: AsyncSession) -> tuple[AdminService, object, object]:
    setup = await build_auth_setup(session)
    admin = AdminService(setup.repo, setup.rbac, setup.auth)
    result = await admin.create_account_with_first_admin(
        session,
        actor=_principal(setup.psa),
        account_code="beta",
        account_name="Beta",
        admin_email="badmin@beta.com",
        admin_username="badmin",
    )
    return admin, result.account, result.admin


async def test_account_create_writes_outbox_in_same_transaction(session: AsyncSession) -> None:
    """AC① 写入侧：Account+首位 Admin 与 outbox 同一 PG 事务（05 §11.3 步骤 1）。

    事务回滚时 outbox 事件一并回滚（不留半成品）；提交后恰 2 条 pending
    事件（account.provision + user.provision），payload 仅必要 ID。
    """
    setup = await build_auth_setup(session)
    admin_service = AdminService(setup.repo, setup.rbac, setup.auth)
    psa_id = setup.psa.id

    result = await admin_service.create_account_with_first_admin(
        session,
        actor=_principal(setup.psa),
        account_code="beta",
        account_name="Beta",
        admin_email="badmin@beta.com",
        admin_username="badmin",
    )
    await session.rollback()

    events = list((await session.execute(select(IamOutbox))).scalars())
    assert events == []  # 回滚后无事件（事务原子性）

    psa = await setup.repo.get_user(session, psa_id)  # 回滚后对象已过期，重取
    result = await admin_service.create_account_with_first_admin(
        session,
        actor=_principal(psa),
        account_code="beta",
        account_name="Beta",
        admin_email="badmin@beta.com",
        admin_username="badmin",
    )
    account, admin = result.account, result.admin
    assert account.status == "provisioning"
    assert admin.status == "provisioning"
    await session.commit()

    events = list(
        (await session.execute(select(IamOutbox).order_by(IamOutbox.created_at))).scalars()
    )
    assert [e.event_type for e in events] == ["account.provision", "user.provision"]
    for e in events:
        assert e.status == "pending"
        assert e.attempts == 0
        assert e.payload["account_id"] == str(account.id)
    assert events[0].aggregate_id == account.id
    assert events[1].aggregate_id == admin.id
    assert "password" not in str(events[1].payload)


async def test_outbox_event_lifecycle(session: AsyncSession) -> None:
    """pending → processing → completed；失败事件按退避到期后重新可领取。"""
    outbox = ProvisioningRepository()
    event = await outbox.enqueue(
        session,
        event_type="account.provision",
        aggregate_id=uuid.uuid4(),
        payload={"account_id": str(uuid.uuid4())},
        now=_now(),
    )
    await session.commit()

    due = await outbox.claim_ready(session, limit=10, now=_now())
    assert [e.id for e in due] == [event.id]

    await outbox.update_status(session, event, status="processing", now=_now(), attempts=1)
    await session.commit()
    assert await outbox.claim_ready(session, limit=10, now=_now()) == []

    future = _now() + timedelta(minutes=5)
    await outbox.update_status(
        session, event, status="failed", now=_now(), last_error="err", next_attempt_at=future
    )
    await session.commit()
    assert await outbox.claim_ready(session, limit=10, now=_now()) == []  # 退避未到期

    await outbox.update_status(session, event, status="failed", now=_now(), next_attempt_at=_now())
    await session.commit()
    due = await outbox.claim_ready(session, limit=10, now=_now())
    assert [e.id for e in due] == [event.id]  # 到期后重新可领取


async def test_claim_ready_orders_and_batches(session: AsyncSession) -> None:
    outbox = ProvisioningRepository()
    agg = uuid.uuid4()
    first = await outbox.enqueue(
        session, event_type="account.provision", aggregate_id=agg, payload={}, now=_now()
    )
    second = await outbox.enqueue(
        session, event_type="user.provision", aggregate_id=agg, payload={}, now=_now()
    )
    await session.commit()
    due = await outbox.claim_ready(session, limit=1, now=_now())
    assert len(due) == 1 and due[0].id in (first.id, second.id)  # 确定性顺序（id 兜底）
    first_claimed = due[0].id
    await outbox.update_status(session, due[0], status="processing", now=_now(), attempts=1)
    await session.commit()
    due = await outbox.claim_ready(session, limit=1, now=_now())
    assert [e.id for e in due] == [
        second.id if first_claimed == first.id else first.id
    ]
    await session.commit()


async def test_worker_completes_account_and_admin(session: AsyncSession) -> None:
    """AC① 全链路：Worker 成功 → Account/User active + outbox completed。"""
    _, account, admin = await _create_account_with_admin(session)
    await session.commit()

    processed, _ = await run_provisioning(session)
    assert processed == 2

    account = await PostgresIamRepository().get_account(session, account.id)
    admin = await PostgresIamRepository().get_user(session, admin.id)
    assert account.status == "active"
    assert admin.status == "active"

    events = list((await session.execute(select(IamOutbox))).scalars())
    assert all(e.status == "completed" for e in events)
    assert all(e.completed_at is not None for e in events)
