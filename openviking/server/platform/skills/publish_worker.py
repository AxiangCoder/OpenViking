"""SkillPublishWorker（10 §58.4 步骤 3–4，14 号计划 §97.4）。

发布 Worker 用 `SystemPrincipal` 消费 `skill.publish` outbox 事件：

- 领取到期事件（pending/failed + 退避到期，只处理 `skill.publish` 类型）→
  processing → 迁移 → completed；失败：status=failed、attempts 递增、
  `last_error` 脱敏、`next_attempt_at` 指数退避（与 ProvisioningWorker 同模式）；
- 幂等（AC⑤）：`fs.mv` 源缺失时只清理孤儿索引，事件重放不产生重复对象或
  重复移动；Operation 状态机保护，不允许"已转换未迁移/已迁移未转换"
  持久不一致（失败 Operation 置 `failed` 可重试，成功才 `succeeded`）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.provisioning.service import sanitize_error
from openviking.server.platform.skills.service import EVENT_SKILL_PUBLISH, SkillService


class SkillPublishWorker:
    """skill.publish 事件消费 Worker（单次轮询语义，由调用方决定调度周期）。"""

    def __init__(
        self,
        repo: IamRepository,
        outbox: ProvisioningRepository,
        service: SkillService,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._service = service
        self._outbox = outbox
        self._config = config

    async def run_once(
        self,
        session: AsyncSession,
        *,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """处理一批到期 `skill.publish` 事件；返回处理条数（每条独立提交）。"""
        now = now or datetime.now(timezone.utc)
        events = await self._outbox.claim_ready(
            session,
            limit=limit or self._config.provisioning_batch_size,
            now=now,
            event_types=(EVENT_SKILL_PUBLISH,),
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
                await self._service.process_publish_event(session, event)
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
