"""过期登录 Session 周期物理清理 Worker（14 号计划 §96.3，04 §10.7）。

- **物理删除**（非撤销）：`idle_expires_at < now` 或 `absolute_expires_at < now`
  即删除；撤销记录随到期一并清理；
- 不影响未过期 Session（验收⑪）；清理结果可观测
  （`run_once` 返回值 + `last_result`，健康检查挂载留 P5-E2）；
- 周期调度：`run_periodically`（asyncio 后台任务），周期入配置。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
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

    async def run_once(self, session: AsyncSession) -> SessionCleanupResult:
        """执行一轮清理（UoW：调用方负责 commit）。"""
        now = datetime.now(timezone.utc)
        result = await session.execute(
            delete(IamSession).where(
                or_(IamSession.idle_expires_at < now, IamSession.absolute_expires_at < now)
            )
        )
        removed = result.rowcount or 0
        remaining = (
            await session.execute(select(func.count()).select_from(IamSession))
        ).scalar_one()
        cleanup = SessionCleanupResult(removed=removed, remaining=remaining, ran_at=now)
        self.last_result = cleanup
        self._log.info(
            "session cleanup run: removed=%d remaining=%d", removed, remaining
        )
        return cleanup

    async def run_periodically(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """周期执行循环（asyncio 后台任务）；停止/健康检查协调留 P5-E2。"""
        interval = self._config.session_cleanup_interval_seconds
        while True:
            async with session_factory() as session:
                await self.run_once(session)
                await session.commit()
            await asyncio.sleep(interval)

    def start(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """启动后台周期任务（幂等；进程退出前调用 stop）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_periodically(session_factory))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
