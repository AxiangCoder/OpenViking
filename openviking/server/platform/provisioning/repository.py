"""Provisioning 数据访问（04 §10.9 `iam_outbox`，14 号计划 §97.1）。

独立的轻量 Repository（iam/ repository 为 P1-E1 冻结模块，只调用不修改）：
outbox 事件入队/领取/状态迁移/卡死恢复查询。所有方法接收调用方传入的
AsyncSession（Unit of Work 模式），与 Account/User 创建在同一 PG 事务内
提交（05 §11.3 创建流程第 1–2 步）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamOutbox

EVENT_ACCOUNT_PROVISION = "account.provision"
EVENT_USER_PROVISION = "user.provision"

PROVISIONING_EVENT_TYPES = (EVENT_ACCOUNT_PROVISION, EVENT_USER_PROVISION)

_UNSET = object()


class ProvisioningRepository:
    """`iam_outbox` 数据访问（04 §10.9）。"""

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        aggregate_id: uuid.UUID,
        payload: dict,
        now: datetime | None = None,
    ) -> IamOutbox:
        """入队（status=pending，next_attempt_at=now）。"""
        record = IamOutbox(
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload=payload,
            status="pending",
            attempts=0,
            next_attempt_at=now,
        )
        session.add(record)
        await session.flush()
        return record

    async def get(self, session: AsyncSession, event_id: uuid.UUID) -> IamOutbox | None:
        return await session.get(IamOutbox, event_id)

    async def claim_ready(
        self,
        session: AsyncSession,
        *,
        limit: int,
        now: datetime,
        event_types: tuple[str, ...] | None = None,
    ) -> list[IamOutbox]:
        """领取可执行事件：status IN (pending, failed) 且到期（next_attempt_at 为空或
        已到）。失败事件按退避时间到期后自动重试（05 §11.3）。

        `event_types` 可选过滤：P2-E4 发布 Worker 只领取 `skill.publish`
        事件，避免与 Provisioning Worker 抢领（缺省 None = 全部，保持
        既有语义）。"""
        stmt = (
            select(IamOutbox)
            .where(
                IamOutbox.status.in_(("pending", "failed")),
                or_(IamOutbox.next_attempt_at.is_(None), IamOutbox.next_attempt_at <= now),
            )
            .order_by(
                IamOutbox.next_attempt_at.nullsfirst(),
                IamOutbox.created_at,
                IamOutbox.id,  # 同一事务内 created_at 相同，id 兜底保证确定性
            )
            .limit(limit)
        )
        if event_types is not None:
            stmt = stmt.where(IamOutbox.event_type.in_(event_types))
        return list((await session.execute(stmt)).scalars())

    async def list_by_account(self, session: AsyncSession, account_id: uuid.UUID) -> list[IamOutbox]:
        """Account 相关事件（payload 内含 account_id；04 §10.9 无独立列）。"""
        stmt = (
            select(IamOutbox)
            .where(IamOutbox.payload["account_id"].astext == str(account_id))
            .order_by(IamOutbox.created_at)
        )
        return list((await session.execute(stmt)).scalars())

    async def list_stuck_processing(
        self,
        session: AsyncSession,
        *,
        older_than: datetime,
    ) -> list[IamOutbox]:
        """卡死事件：status=processing 且 processing_started_at 早于阈值
        （Reconciler 恢复输入，AC⑦）。"""
        stmt = select(IamOutbox).where(
            IamOutbox.status == "processing",
            IamOutbox.processing_started_at.is_not(None),
            IamOutbox.processing_started_at < older_than,
        )
        return list((await session.execute(stmt)).scalars())

    async def has_open_event(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        aggregate_id: uuid.UUID,
    ) -> bool:
        """是否存在未完成事件（对账/重放防重复入队，AC⑦ 不重复执行）。"""
        stmt = (
            select(IamOutbox.id)
            .where(
                IamOutbox.event_type == event_type,
                IamOutbox.aggregate_id == aggregate_id,
                IamOutbox.status != "completed",
            )
            .limit(1)
        )
        return (await session.execute(stmt)).first() is not None

    async def update_status(
        self,
        session: AsyncSession,
        event: IamOutbox,
        *,
        status: str,
        now: datetime,
        attempts: int | None | object = _UNSET,
        last_error: str | None | object = _UNSET,
        next_attempt_at: datetime | None | object = _UNSET,
        completed_at: datetime | None | object = _UNSET,
        processing_started_at: datetime | None | object = _UNSET,
    ) -> IamOutbox:
        """状态迁移（worker/retry/reconciler 共用）。

        显式传 `None` 即把该列清空（如 completed 时清除 last_error）；
        不传（`_UNSET`）保持原值。
        """
        values: dict = {"status": status}
        if attempts is not _UNSET:
            values["attempts"] = attempts
        if last_error is not _UNSET:
            values["last_error"] = last_error
        if next_attempt_at is not _UNSET:
            values["next_attempt_at"] = next_attempt_at
        if completed_at is not _UNSET:
            values["completed_at"] = completed_at
        if processing_started_at is not _UNSET:
            values["processing_started_at"] = processing_started_at
        stmt = (
            update(IamOutbox)
            .where(IamOutbox.id == event.id)
            .values(**values)
            .returning(IamOutbox)
        )
        return (await session.execute(stmt)).scalar_one()
