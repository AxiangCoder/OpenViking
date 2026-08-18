"""过期登录 Session 周期物理清理 Worker（14 号计划 §96.3，04 §10.7）。

- **物理删除**（非撤销）：`idle_expires_at < now` 或 `absolute_expires_at < now`
  即删除；撤销记录随到期一并清理；
- 不影响未过期 Session（验收⑪）；清理结果可观测
  （`run_once` 返回值 + `last_result`，健康检查挂载留 P5-E2）；
- 周期调度：`run_periodically`（asyncio 后台任务），周期入配置；
  `stop()` 采用**协作式停止**（事件唤醒等待，不在 DB 查询中途取消任务，
  asyncpg 取消语义下会挂起连接），保证每轮清理完整提交。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.platform.auth.sessions import SessionCleanupResult
from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.models import IamSession

logger = logging.getLogger("openviking.platform.session_cleanup")


class SessionCleanupWorker:
    """周期物理清理过期登录 Session（单实例进程内，06 §16.4）。"""

    def __init__(
        self,
        config: PlatformConfig = platform_config,
        log: logging.Logger = logger,
    ) -> None:
        self._config = config
        self._log = log
        self.last_result: SessionCleanupResult | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def run_once(self, session: AsyncSession) -> SessionCleanupResult:
        """执行一轮清理（UoW：调用方负责 commit）。

        使用表级 DELETE（纯 Core），避免 ORM 实体 delete 与 identity map 的
        同步语义（与 touch_session 相同的处理约定）。
        """
        now = datetime.now(timezone.utc)
        result = await session.execute(
            IamSession.__table__.delete().where(
                or_(
                    IamSession.__table__.c.idle_expires_at < now,
                    IamSession.__table__.c.absolute_expires_at < now,
                )
            )
        )
        removed = result.rowcount or 0
        remaining = (
            await session.execute(select(func.count()).select_from(IamSession))
        ).scalar_one()
        cleanup = SessionCleanupResult(removed=removed, remaining=remaining, ran_at=now)
        self.last_result = cleanup
        self._log.info("session cleanup run: removed=%d remaining=%d", removed, remaining)
        return cleanup

    async def run_periodically(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """周期执行循环（asyncio 后台任务）。

        停止语义：`stop()` 置位事件后，当前轮清理完整提交，
        下一次等待被事件唤醒退出（不在查询中途取消）。
        """
        interval = self._config.session_cleanup_interval_seconds
        while not self._stop_event.is_set():
            async with session_factory() as session:
                await self.run_once(session)
                await session.commit()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                continue

    def start(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """启动后台周期任务（幂等；进程退出前调用 stop）。"""
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self.run_periodically(session_factory))

    async def stop(self) -> None:
        """协作式停止：等待当前轮清理完成（幂等）。"""
        if self._task is not None:
            self._stop_event.set()
            await self._task
            self._task = None
