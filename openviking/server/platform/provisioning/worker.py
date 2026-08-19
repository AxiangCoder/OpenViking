"""ProvisioningWorker（05 §11.3，14 号计划 §97.1）。

- 领取到期事件（pending/failed + 退避到期）→ processing → 处理 → completed；
- 失败：status=failed、attempts 递增、`last_error` 脱敏、`next_attempt_at`
  按指数退避（`base * 2^(attempts-1)`，封顶 max，05 §11.3）；
- 幂等（AC③）：控制面动作幂等，事件重放不产生重复 namespace；
- 多实例 Worker 协调（锁/抢领）属 Phase 6，本 Epic 单进程内按状态机防重。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.provisioning.control_plane import ControlPlaneAdapter
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.provisioning.service import ProvisioningService, sanitize_error


class ProvisioningWorker:
    """outbox 事件消费 Worker（单次轮询语义，由调用方决定调度周期）。"""

    def __init__(
        self,
        repo: IamRepository,
        outbox: ProvisioningRepository,
        control_plane: ControlPlaneAdapter,
        service: ProvisioningService | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._service = service or ProvisioningService(repo, outbox, control_plane, config)
        self._outbox = outbox
        self._config = config

    async def run_once(
        self,
        session: AsyncSession,
        *,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """处理一批到期事件；返回处理条数（每条独立提交，失败不影响后续）。"""
        now = now or datetime.now(timezone.utc)
        events = await self._outbox.claim_ready(
            session, limit=limit or self._config.provisioning_batch_size, now=now
        )
        processed = 0
        for event in events:
            attempts = event.attempts + 1
            await self._outbox.update_status(
                session,
                event,
                status="processing",
                now=now,
                attempts=attempts,
                processing_started_at=now,
            )
            await session.flush()
            try:
                await self._service.process_event(session, event)
            except Exception as exc:  # noqa: BLE001
                backoff = self._backoff_seconds(attempts)
                await self._outbox.update_status(
                    session,
                    event,
                    status="failed",
                    now=now,
                    last_error=sanitize_error(exc),
                    next_attempt_at=now + timedelta(seconds=backoff),
                )
                await session.commit()
                processed += 1
                continue
            await self._outbox.update_status(
                session,
                event,
                status="completed",
                now=now,
                completed_at=now,
                last_error=None,
                next_attempt_at=None,
            )
            await session.commit()
            processed += 1
        return processed

    def _backoff_seconds(self, attempts: int) -> int:
        """指数退避（05 §11.3）：`base * 2^(attempts-1)`，封顶 max。"""
        exponential = self._config.provisioning_retry_base_seconds * (2 ** max(attempts - 1, 0))
        return min(exponential, self._config.provisioning_retry_max_seconds)
