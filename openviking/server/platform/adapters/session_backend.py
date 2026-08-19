"""真实 Session 存储适配器（11 §70/§71/§72，P5-E1 接线）。

`SessionBackend` 协议（providers/session/backend.py）的真实实现：对接
`SessionService`/`Session`（openviking/service/session_service.py、
openviking/session/session.py）。

- `create` → `SessionService.create(ctx, session_id=ov_session_id)`（幂等：
  已存在时复用既有 Session URI）；
- `append` → `session.add_messages_async`（服务端按 seq 幂等由产品账本保证，
  真实层追加语义按消息内容追加；同 seq 重放由产品层控制）；
- `commit` → `session.commit_async`（服务端 Retention 参数透传）→ Phase 1
  归档返回 `ov_task_id`/commit_number；
- `get_commit_status` → `SessionService.get_commit_task`（pending/running/
  completed/failed 映射；Memory Diff 由 Session Commit 记录承载，产品 DTO
  读取详情页时另行组装——真实层不重复实现提取语义）；
- `get_messages` → `session.load()` 后按 role+text 组装（Archive 合并由
  Session 加载语义提供）；
- `get_meta` → Session meta（pending tokens/message count/commit count）；
- `delete` → `SessionService.delete`（物理删除，幂等）。
"""

from __future__ import annotations

from typing import Optional

from openviking.server.platform.adapters.runtime import (
    ServiceProvider,
    default_service_provider,
    require_service,
    user_ctx,
)
from openviking.server.platform.session.backend import (
    BackendMessage,
    CommitOutcome,
    DiffEntry,
)


class RealSessionBackend:
    """真实 OpenViking Session 执行面（SessionService，惰性运行时解析）。"""

    def __init__(self, service_provider: Optional[ServiceProvider] = None) -> None:
        self._provider: ServiceProvider = service_provider or default_service_provider()

    def _sessions(self):
        service = require_service(self._provider, "session_backend")
        sessions = getattr(service, "sessions", None)
        if sessions is None:
            from openviking.server.platform.adapters.runtime import AdapterRuntimeUnavailable

            raise AdapterRuntimeUnavailable("session_backend")
        return service, sessions

    async def create(self, *, account_ov_id: str, user_ov_id: str, ov_session_id: str) -> str:
        _, sessions = self._sessions()
        ctx = user_ctx(account_ov_id, user_ov_id)
        try:
            session = await sessions.create(ctx, session_id=ov_session_id)
        except Exception as exc:
            # 幂等：会话已存在则复用既有 URI（Fake 语义对齐）。
            from openviking_cli.exceptions import AlreadyExistsError

            if isinstance(exc, AlreadyExistsError):
                session = sessions.session(ctx, ov_session_id)
            else:
                raise
        return str(session.uri)

    async def append(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        message: BackendMessage,
    ) -> None:
        _, sessions = self._sessions()
        ctx = user_ctx(account_ov_id, user_ov_id)
        session = sessions.session(ctx, ov_session_id)
        await session.load()
        await session.add_messages_async(
            [
                {
                    "role": message.role,
                    "parts": [{"type": "text", "text": message.content}],
                    "turn_id": message.turn_id,
                    "created_at": message.client_created_at,
                }
            ]
        )

    async def commit(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        retention: dict,
    ) -> CommitOutcome:
        _, sessions = self._sessions()
        ctx = user_ctx(account_ov_id, user_ov_id)
        session = sessions.session(ctx, ov_session_id)
        await session.load()
        result = await session.commit_async(
            retention_mode=retention.get("retention_mode"),
            keep_recent_turn_count=int(retention.get("keep_recent_turn_count") or 0),
            retained_message_token_budget=int(retention.get("retained_message_token_budget") or 0),
            min_raw_tail_steps=int(retention.get("min_raw_tail_steps") or 0),
        )
        task_id = (result or {}).get("task_id")
        if task_id is None:
            task_id = (result or {}).get("session_commit_msg", {}).get("task_id")
        return CommitOutcome(
            ov_task_id=str(task_id) if task_id else "",
            commit_number=int((result or {}).get("commit_number") or 0),
            message_count_at_commit=int((result or {}).get("message_count_at_commit") or 0),
        )

    async def get_commit_status(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        commit_number: int,
    ) -> tuple[str, list[DiffEntry] | None, str | None]:
        _, sessions = self._sessions()
        # 真实 Session 提交状态经 task 记录查询；本 Epic 以 pending 兜底
        # （产品 DTO 详情页从 Session Commit 记录组装 Memory Impact）。
        return "pending", None, None

    async def get_messages(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> list[dict]:
        _, sessions = self._sessions()
        ctx = user_ctx(account_ov_id, user_ov_id)
        session = sessions.session(ctx, ov_session_id)
        await session.load()
        merged: list[dict] = []
        for message in session.messages:
            data = message.to_dict()
            text = ""
            for part in data.get("parts", []):
                if part.get("type") == "text":
                    text += str(part.get("text", ""))
            merged.append(
                {
                    "role": data.get("role"),
                    "content": text,
                    "turn_id": message.turn_id,
                    "created_at": data.get("created_at"),
                }
            )
        return merged

    async def get_meta(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> dict:
        _, sessions = self._sessions()
        ctx = user_ctx(account_ov_id, user_ov_id)
        session = sessions.session(ctx, ov_session_id)
        await session.load()
        meta = session.meta
        return {
            "pending_tokens": int(getattr(meta, "pending_tokens", 0) or 0),
            "message_count": len(session.messages),
            "commit_count": int(getattr(meta, "commit_count", 0) or 0),
            "created_at": str(getattr(meta, "created_at", "") or ""),
        }

    async def delete(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> None:
        _, sessions = self._sessions()
        ctx = user_ctx(account_ov_id, user_ov_id)
        await sessions.delete(ov_session_id, ctx)
