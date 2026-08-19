"""Resource Purge 处理器（09 §45.2 步骤 6，05 §11.3 PurgeHandler 协议）。

- 30 天后由 Purge Worker 物理清理 OpenViking 内容与派生索引（本 Epic 为
  受控 fake 执行面 + Watch 配置行清理）；
- 幂等：重放不产生重复副作用；
- 审计事件不随物理清理（04 §10.11）。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.deletion.worker import PurgeHandler
from openviking.server.platform.models import IamDeletionJob
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.resource.execution import ResourceExecutionPlane
from openviking.server.platform.resource.repository import ResourceRepository


class ResourcePurgeHandler(PurgeHandler):
    """Resource 物理清理（幂等）：Watch 行 + 执行面内容 + 引用标记。"""

    def __init__(
        self,
        *,
        execution: ResourceExecutionPlane,
        store: RegistryRepository | None = None,
        resource_store: ResourceRepository | None = None,
    ) -> None:
        self._execution = execution
        self._store = store or RegistryRepository()
        self._resource_store = resource_store or ResourceRepository()

    async def purge(self, session: AsyncSession, job: IamDeletionJob) -> None:
        ref_id = uuid.UUID(job.resource_id)
        # 幂等：执行面内容清理
        await self._execution.delete(ref_id=ref_id)
        # Watch 配置行物理清理
        await self._resource_store.hard_delete_watch(session, ref_id)
        # 引用标记 deleted（物理清理完成语义；审计事件不随清理删除）
        ref = await self._store.get_ref(session, ref_id)
        if ref is not None:
            ref.status = "deleted"
            ref.ov_uri = f"{ref.ov_uri}"
            await self._store.update_ref(session, ref)
            if ref.source_locator_ciphertext is not None:
                # 终态清除短期/长期来源密文（09 §40.6：任务终态/对象删除后清除）
                ref.source_locator_ciphertext = None
                ref.source_locator_key_version = None
                await self._store.update_ref(session, ref)
