"""Session Purge 处理器（11 §72：30 天后 Worker 幂等物理删除，AC⑦）。

`PurgeHandler` 协议实现（deletion/worker.py）：到期清理由 Worker 领取
（pending → purging），本处理器调用 `SessionProductService.purge`
（Backend 幂等物理删除 + 本表状态归位），成功由 Worker 置 `purged`、
失败置 `failed`（脱敏 `last_error`）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamDeletionJob
from openviking.server.platform.session.service import SessionProductService


class SessionPurgeHandler:
    """Session 期满物理清理处理器（幂等：重放不产生重复副作用）。"""

    def __init__(self, service: SessionProductService) -> None:
        self._service = service

    async def purge(self, session: AsyncSession, job: IamDeletionJob) -> None:
        await self._service.purge(session, job)
