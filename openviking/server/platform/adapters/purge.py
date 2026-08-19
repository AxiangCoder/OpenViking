"""真实 Resource 期满物理清理处理器（04 §10.11，P5-E1 接线）。

`PurgeHandler` 协议（providers/deletion/worker.py）的真实实现：期满删除
任务经 `IamDeletionJob.ov_uri` + Account 映射 → `VikingFS.rm`（文件 + 向量
索引一体删除，幂等：不存在时成功）。审计不随物理清理（Worker 骨架语义）。
"""

from __future__ import annotations

from typing import Optional

from openviking.server.platform.adapters.runtime import (
    AdapterRuntimeUnavailable,
    ServiceProvider,
    default_service_provider,
    require_service,
    user_ctx,
)

PLATFORM_GATEWAY_USER = "platform-gateway"


class RealResourcePurgeHandler:
    """真实 OpenViking 数据清理（VikingFS.rm，惰性运行时解析）。"""

    def __init__(
        self,
        service_provider: Optional[ServiceProvider] = None,
    ) -> None:
        self._provider: ServiceProvider = service_provider or default_service_provider()

    async def purge(self, session, job) -> None:
        """物理清理 job.resource_type 对应的 OpenViking 内容（幂等）。"""
        uri = getattr(job, "ov_uri", None)
        if not uri:
            return
        service = require_service(self._provider, "resource_purge")
        viking_fs = getattr(service, "viking_fs", None)
        if viking_fs is None:
            raise AdapterRuntimeUnavailable("resource_purge")
        ov_account_id = await self._ov_account_id(session, job)
        ctx = self._ctx_for_uri(uri, ov_account_id)
        await viking_fs.rm(uri, recursive=True, ctx=ctx)

    async def _ov_account_id(self, session, job) -> str:
        from openviking.server.platform.iam import PostgresIamRepository

        account = await PostgresIamRepository().get_account(session, job.account_id)
        if account is None or account.ov_account_id is None:
            raise AdapterRuntimeUnavailable("resource_purge (account not mapped)")
        return account.ov_account_id

    def _ctx_for_uri(self, uri: str, ov_account_id: str):
        rest = uri.rstrip("/")
        if rest.startswith("viking://user/"):
            return user_ctx(ov_account_id, rest[len("viking://user/") :].split("/", 1)[0])
        return user_ctx(ov_account_id, PLATFORM_GATEWAY_USER)
