"""ProvisioningReconciler（14 号计划 §97.1：卡死事件恢复、对账）。

- `recover_stuck`：status=processing 且 `processing_started_at` 超过
  卡死阈值（默认 10 分钟，可配置）的事件重置为 pending（AC⑦），
  由 Worker 重新领取；恢复只影响 processing 事件、且置空
  processing_started_at，不会重复执行（Worker 按状态机防重）；
- `reconcile`：以 PG 为单一身份事实来源（05 §11.3），对照控制面
  namespace 注册表，差异（missing）幂等重放 outbox 事件（AC⑧：
  对账后差异为零/重放后 namespace 存在即通过）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.provisioning.control_plane import ControlPlaneAdapter
from openviking.server.platform.provisioning.repository import (
    EVENT_ACCOUNT_PROVISION,
    EVENT_USER_PROVISION,
    ProvisioningRepository,
)


@dataclass(frozen=True)
class ReconcileReport:
    """对账报告：missing = PG active 但控制面缺失的 ov 映射。"""

    missing_accounts: tuple[str, ...]
    missing_users: tuple[tuple[str, str], ...]
    requeued_events: int

    @property
    def in_sync(self) -> bool:
        """差异为零：PG ov 映射与控制面一致（AC⑧）。"""
        return not self.missing_accounts and not self.missing_users and self.requeued_events == 0


class ProvisioningReconciler:
    """卡死恢复 + 控制面对账。"""

    def __init__(
        self,
        repo: IamRepository,
        outbox: ProvisioningRepository,
        control_plane: ControlPlaneAdapter,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._repo = repo
        self._outbox = outbox
        self._control_plane = control_plane
        self._config = config

    async def recover_stuck(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> int:
        """恢复卡死事件（processing 超时 → pending）；返回恢复条数（AC⑦）。"""
        now = now or datetime.now(timezone.utc)
        threshold = now - timedelta(seconds=self._config.provisioning_stuck_timeout_seconds)
        stuck = await self._outbox.list_stuck_processing(session, older_than=threshold)
        for event in stuck:
            await self._outbox.update_status(
                session,
                event,
                status="pending",
                now=now,
                next_attempt_at=now,
                processing_started_at=None,
            )
        return len(stuck)

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> ReconcileReport:
        """对账：PG active 的 Account/User 与控制面 namespace 逐一比对，
        缺失映射幂等重放 outbox 事件（已有未完成事件不重复入队）。"""
        now = now or datetime.now(timezone.utc)
        missing_accounts: list[uuid.UUID] = []
        missing_users: list[tuple[uuid.UUID, str, str]] = []  # (user_id, ov_account_id, ov_user_id)

        accounts = [
            a for a in await self._repo.list_accounts(session) if a.status == "active"
        ]
        cp_accounts = await self._control_plane.list_provisioned_accounts()
        for account in accounts:
            if account.ov_account_id not in cp_accounts:
                missing_accounts.append(account.id)
            cp_users = await self._control_plane.list_provisioned_users(account.ov_account_id)
            for user in await self._repo.list_users_by_account(session, account.id):
                if (
                    user.status == "active"
                    and user.ov_user_id is not None
                    and user.ov_user_id not in cp_users
                ):
                    missing_users.append((user.id, account.ov_account_id, user.ov_user_id))

        requeued = 0
        for account_id in missing_accounts:
            account = await self._repo.get_account(session, account_id)
            if account is None:
                continue
            if await self._outbox.has_open_event(
                session, event_type=EVENT_ACCOUNT_PROVISION, aggregate_id=account.id
            ):
                continue
            await self._outbox.enqueue(
                session,
                event_type=EVENT_ACCOUNT_PROVISION,
                aggregate_id=account.id,
                payload={
                    "account_id": str(account.id),
                    "ov_account_id": account.ov_account_id,
                    "code": account.code,
                    "display_name": account.display_name,
                },
                now=now,
            )
            requeued += 1
        for user_id, _ov_account_id, ov_user_id in missing_users:
            user = await self._repo.get_user(session, user_id)
            if user is None or user.ov_user_id is None:
                continue
            if await self._outbox.has_open_event(
                session, event_type=EVENT_USER_PROVISION, aggregate_id=user.id
            ):
                continue
            await self._outbox.enqueue(
                session,
                event_type=EVENT_USER_PROVISION,
                aggregate_id=user.id,
                payload={
                    "account_id": str(user.account_id) if user.account_id else None,
                    "user_id": str(user.id),
                    "ov_user_id": ov_user_id,
                    "email": user.email,
                },
                now=now,
            )
            requeued += 1
        return ReconcileReport(
            missing_accounts=tuple(str(a) for a in missing_accounts),
            missing_users=tuple((acc, uid) for _uid, acc, uid in missing_users),
            requeued_events=requeued,
        )
