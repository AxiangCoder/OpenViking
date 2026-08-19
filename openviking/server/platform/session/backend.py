"""受控 Session/Search 后端适配层（11 §70/§71/§69，P2-E5）。

产品 API 不直接调用真实 OpenViking Session/Search 底层，而是经本层协议
执行（对齐 P2-E1 `FakeControlPlane` 模式）：PostgreSQL 是归属/幂等/顺序/
软删除的事实来源，本层是消息存储、归档、Memory 提取与向量检索的执行面。

- `SessionBackend`：Session 创建/追加/Commit/归档组装/Phase 2 状态/
  Memory Diff/物理删除；全部动作幂等；
- `SearchEngine`：`find`（不加载 Session）与 `search`（携带 Session）
  语义检索，返回**带内部字段**的原始命中（uri/score/level/query_plan/
  provenance/relations/category），由产品 DTO 层统一脱敏（11 §69.4；
  AC③），客户端永远看不到内部字段；
- 真实 OpenViking 接线属 P5-E1；本模块的 Fake 实现按同一协议行为提供
  确定性测试语义（保留最近 3 个逻辑 Turn / 12000 Token / 至少 1 个最新
  Assistant Step，11 §71.1）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

# 产品服务端统一 Turn-aware Retention（11 §71.1：3 Turn / 12000 Token /
# 至少 1 个最新 Assistant Step；客户端不能调低，AC⑤）。
RETENTION_KEEP_RECENT_TURN_COUNT = 3
RETENTION_MESSAGE_TOKEN_BUDGET = 12000
RETENTION_MIN_RAW_TAIL_STEPS = 1

RETENTION_PARAMS: dict = {
    "retention_mode": "turn_budget",
    "keep_recent_turn_count": RETENTION_KEEP_RECENT_TURN_COUNT,
    "retained_message_token_budget": RETENTION_MESSAGE_TOKEN_BUDGET,
    "min_raw_tail_steps": RETENTION_MIN_RAW_TAIL_STEPS,
}


@dataclass(frozen=True)
class BackendMessage:
    """追加消息执行面输入（产品账本发号后交给后端保存）。"""

    seq: int
    role: str
    content: str
    idempotency_key: str | None = None
    turn_id: str | None = None
    client_created_at: str | None = None


@dataclass(frozen=True)
class CommitOutcome:
    """Commit 执行面结果（Phase 1 同步归档；Phase 2 异步由状态轮询推进）。"""

    ov_task_id: str
    commit_number: int
    message_count_at_commit: int


@dataclass(frozen=True)
class DiffEntry:
    """脱敏 Memory Diff 条目（11 §71.3：不含 Archive/Memory URI，AC⑥）。

    `before` 仅在 Update/Delete 时非空；`after` 仅在 Add/Update 时非空。
    """

    memory_type: str
    action: str  # add | update | delete
    before: str | None = None
    after: str | None = None


class SessionBackend(Protocol):
    """OpenViking Session 执行面（全部幂等，重放不产生重复副作用）。"""

    async def create(
        self, *, account_ov_id: str, user_ov_id: str, ov_session_id: str
    ) -> str:
        """创建 Session；返回 canonical session URI。重复调用复用既有 URI。"""

    async def append(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        message: BackendMessage,
    ) -> None:
        """按服务端 seq 追加消息（幂等：同 seq 重复调用不重复追加）。"""

    async def commit(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        retention: dict,
    ) -> CommitOutcome:
        """归档 + 触发异步 Memory 提取（Phase 1 同步，Phase 2 异步）。"""

    async def get_commit_status(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        commit_number: int,
    ) -> tuple[str, list[DiffEntry] | None, str | None]:
        """Phase 2 状态：pending/running/completed/failed；
        completed 附带脱敏 Diff，failed 附带脱敏错误。"""

    async def get_messages(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> list[dict]:
        """已组装完整历史（11 §70.4：Archive 按编号在前、当前消息在后、去重）。"""

    async def get_meta(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> dict:
        """Session Meta 摘要（不含 URI；产品 DTO 只取允许字段）。"""

    async def delete(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> None:
        """物理删除（11 §72：30 天后 Purge Worker 幂等调用；重放安全）。"""


# ── 消息/归档/内存提取状态（Fake 后端事实源）──


@dataclass
class _CommitRecord:
    number: int
    archived: list[dict]
    status: str = "pending"
    diff: list[dict] | None = None
    error: str | None = None


@dataclass
class _FakeSession:
    ov_session_id: str
    uri: str
    messages: list[dict] = field(default_factory=list)  # live context（按 seq）
    commits: dict[int, _CommitRecord] = field(default_factory=dict)
    phase2_mode: str = "immediate"  # immediate | stuck | failed（测试注入）


def _extract_memory_ops(messages: list[dict]) -> list[DiffEntry]:
    """确定性 Memory 提取规则（Fake）：user 消息含 `remember X` → add、
    `update X` → update、`forget X` → delete；其余消息无操作。"""
    ops: list[DiffEntry] = []
    for msg in messages:
        text = (msg.get("content") or "").strip()
        lowered = text.lower()
        if lowered.startswith("remember "):
            ops.append(DiffEntry(memory_type="preference", action="add", after=text[9:]))
        elif lowered.startswith("update "):
            ops.append(
                DiffEntry(
                    memory_type="preference",
                    action="update",
                    before=text[9:],
                    after=f"updated:{text[9:]}",
                )
            )
        elif lowered.startswith("forget "):
            ops.append(DiffEntry(memory_type="preference", action="delete", before=text[7:]))
    return ops


class FakeSessionBackend:
    """内存 Session 后端（开发/测试适配层，对齐 P2-E1 FakeControlPlane）。

    行为与真实 OpenViking 一致：Commit Phase 1 同步归档（按 11 §71.1
    服务端 Retention 保留尾部）、Phase 2 异步生成 Diff（`get_commit_status`
    轮询推进，测试可用 `set_phase2_mode` 注入 stuck/failed）。
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str, str], _FakeSession] = {}

    # ── 测试注入 ──

    def set_phase2_mode(
        self, *, account_ov_id: str, user_ov_id: str, ov_session_id: str, mode: str
    ) -> None:
        self._sessions[(account_ov_id, user_ov_id, ov_session_id)].phase2_mode = mode

    def reset(self) -> None:
        self._sessions.clear()

    # ── SessionBackend 实现 ──

    async def create(
        self, *, account_ov_id: str, user_ov_id: str, ov_session_id: str
    ) -> str:
        key = (account_ov_id, user_ov_id, ov_session_id)
        uri = f"viking://user/{user_ov_id}/sessions/{ov_session_id}"
        if key not in self._sessions:
            self._sessions[key] = _FakeSession(
                ov_session_id=ov_session_id, uri=uri, phase2_mode="immediate"
            )
        return self._sessions[key].uri

    async def append(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        message: BackendMessage,
    ) -> None:
        session = self._sessions[(account_ov_id, user_ov_id, ov_session_id)]
        if any(m.get("seq") == message.seq for m in session.messages):
            return  # 幂等：同 seq 不重复追加
        session.messages.append(
            {
                "seq": message.seq,
                "role": message.role,
                "content": message.content,
                "turn_id": message.turn_id,
                "created_at": message.client_created_at,
            }
        )
        session.messages.sort(key=lambda m: m["seq"])

    async def commit(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        retention: dict,
    ) -> CommitOutcome:
        session = self._sessions[(account_ov_id, user_ov_id, ov_session_id)]
        commit_number = len(session.commits) + 1
        keep_turns = int(retention.get("keep_recent_turn_count") or 0)
        min_tail_steps = int(retention.get("min_raw_tail_steps") or 0)

        user_turn_indexes = [
            i for i, m in enumerate(session.messages) if m.get("role") == "user"
        ]
        assistant_indexes = [
            i for i, m in enumerate(session.messages) if m.get("role") == "assistant"
        ]
        if len(user_turn_indexes) > keep_turns:
            cutoff_index = user_turn_indexes[len(user_turn_indexes) - keep_turns]
            # 至少保留 min_tail_steps 个最新 Assistant Step：若所需保留的
            # assistant 起点在当前 cutoff 之前，向前扩展 cutoff（11 §71.1）。
            if assistant_indexes and min_tail_steps > 0:
                needed_index = assistant_indexes[-min_tail_steps]
                if needed_index < cutoff_index:
                    cutoff_index = needed_index
            archived, live = session.messages[:cutoff_index], session.messages[cutoff_index:]
        else:
            archived, live = [], session.messages

        session.commits[commit_number] = _CommitRecord(
            number=commit_number, archived=list(archived), status="pending"
        )
        session.messages = live
        return CommitOutcome(
            ov_task_id=f"task-{uuid.uuid4().hex[:12]}",
            commit_number=commit_number,
            message_count_at_commit=len(archived) + len(live),
        )

    async def get_commit_status(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
        commit_number: int,
    ) -> tuple[str, list[DiffEntry] | None, str | None]:
        session = self._sessions[(account_ov_id, user_ov_id, ov_session_id)]
        record = session.commits[commit_number]
        if record.status in ("completed", "failed"):
            return record.status, record.diff, record.error
        if session.phase2_mode == "stuck":
            record.status = "running"
            return "running", None, None
        if session.phase2_mode == "failed":
            record.status = "failed"
            record.error = "memory_extraction_failed"
            return "failed", None, record.error
        # immediate：首次轮询完成 Phase 2，并生成脱敏 Memory Diff
        ops = _extract_memory_ops(record.archived)
        record.diff = [{"memory_type": o.memory_type, "action": o.action, "before": o.before, "after": o.after} for o in ops]
        record.status = "completed"
        return "completed", record.diff, None

    async def get_messages(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> list[dict]:
        session = self._sessions[(account_ov_id, user_ov_id, ov_session_id)]
        seen: set[int] = set()
        merged: list[dict] = []
        for number in sorted(session.commits):
            for msg in session.commits[number].archived:
                if msg["seq"] not in seen:
                    seen.add(msg["seq"])
                    merged.append(msg)
        for msg in session.messages:
            if msg["seq"] not in seen:
                seen.add(msg["seq"])
                merged.append(msg)
        merged.sort(key=lambda m: m["seq"])
        return merged

    async def get_meta(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> dict:
        session = self._sessions[(account_ov_id, user_ov_id, ov_session_id)]
        return {
            "pending_tokens": 0,
            "message_count": len(session.messages),
            "commit_count": len(session.commits),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    async def delete(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        ov_session_id: str,
    ) -> None:
        self._sessions.pop((account_ov_id, user_ov_id, ov_session_id), None)


# ── 检索执行面 ──


@dataclass(frozen=True)
class RawHit:
    """原始检索命中（含内部字段；产品 DTO 层统一脱敏，AC③）。"""

    uri: str
    context_type: str
    display_name: str
    abstract: str
    match_reason: str
    score: float = 0.0
    level: int = 1
    query_plan: dict | None = None
    provenance: list[dict] | None = None
    relations: list[dict] | None = None
    category: str = ""
    memory_type: str | None = None


class SearchEngine(Protocol):
    """受控语义检索执行面（根集合由产品服务端固定，客户端不能改，11 §69.2）。"""

    async def find(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        roots: list[str],
        query: str,
        context_type: str | None,
        tags: list[str],
        since: str | None,
        until: str | None,
    ) -> list[RawHit]:
        """快速检索：签名不含 Session（find 不加载 Session，AC①）。"""

    async def search(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        roots: list[str],
        query: str,
        context_type: str | None,
        tags: list[str],
        since: str | None,
        until: str | None,
        session_ov_id: str,
    ) -> list[RawHit]:
        """结合会话检索（session_ov_id 已由产品服务端校验归属/未删除）。"""


@dataclass
class FakeKnowledgeItem:
    """Fake 检索索引条目（uri 决定归属根；account_ov/owner_ov 模拟 namespace 隔离）。"""

    uri: str
    context_type: str
    display_name: str
    abstract: str
    match_reason: str = "语义相关"
    tags: list[str] = field(default_factory=list)
    updated_at: str = ""
    memory_type: str | None = None
    account_ov_id: str | None = None
    owner_ov_user: str | None = None


class FakeSearchEngine:
    """内存检索引擎（开发/测试适配层）。

    固定检索根由调用方（产品服务端）传入：User 私有根
    `viking://user/{ov_user_id}/` + Account 共享根 `viking://resources/`、
    `viking://agent/skills/`（11 §69.2/§68）。命中按 uri 前缀分类
    visibility；共享条目按 `account_ov_id`、私有条目按 `owner_ov_user`
    做 namespace 隔离（模拟真实引擎的 RequestContext 隔离）。
    """

    def __init__(self) -> None:
        self._items: list[FakeKnowledgeItem] = []
        self._traces: list[dict] = []

    def seed(self, *items: FakeKnowledgeItem) -> None:
        self._items.extend(items)

    def trace(self) -> list[dict]:
        return list(self._traces)

    def reset(self) -> None:
        self._items.clear()
        self._traces.clear()

    async def find(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        roots: list[str],
        query: str,
        context_type: str | None,
        tags: list[str],
        since: str | None,
        until: str | None,
    ) -> list[RawHit]:
        self._traces.append({"mode": "find", "roots": list(roots), "query": query})
        return self._query(
            account_ov_id=account_ov_id,
            user_ov_id=user_ov_id,
            roots=roots,
            query=query,
            context_type=context_type,
            tags=tags,
            since=since,
            until=until,
        )

    async def search(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        roots: list[str],
        query: str,
        context_type: str | None,
        tags: list[str],
        since: str | None,
        until: str | None,
        session_ov_id: str,
    ) -> list[RawHit]:
        self._traces.append(
            {"mode": "search", "roots": list(roots), "query": query, "session": session_ov_id}
        )
        return self._query(
            account_ov_id=account_ov_id,
            user_ov_id=user_ov_id,
            roots=roots,
            query=query,
            context_type=context_type,
            tags=tags,
            since=since,
            until=until,
        )

    def _query(
        self,
        *,
        account_ov_id: str,
        user_ov_id: str,
        roots: list[str],
        query: str,
        context_type: str | None,
        tags: list[str],
        since: str | None,
        until: str | None,
    ) -> list[RawHit]:
        normalized_tags = [t.strip().lower() for t in tags if t.strip()]
        query_lower = (query or "").strip().lower()
        hits: list[RawHit] = []
        for item in self._items:
            if not any(item.uri.startswith(root) for root in roots):
                continue
            if item.context_type == "memory":
                if item.owner_ov_user != user_ov_id:
                    continue
            else:
                if item.account_ov_id is not None and item.account_ov_id != account_ov_id:
                    continue
            if context_type and item.context_type != context_type:
                continue
            if normalized_tags and not all(
                tag in [t.strip().lower() for t in item.tags] for tag in normalized_tags
            ):
                continue
            if since and item.updated_at < since:
                continue
            if until and item.updated_at > until:
                continue
            if query_lower and query_lower not in (
                item.display_name.lower()
                + item.abstract.lower()
                + item.match_reason.lower()
            ):
                continue
            hits.append(
                RawHit(
                    uri=item.uri,
                    context_type=item.context_type,
                    display_name=item.display_name,
                    abstract=item.abstract,
                    match_reason=item.match_reason,
                    score=0.83,
                    level=1,
                    query_plan={"type": "semantic"},
                    provenance=[{"source": "index"}],
                    relations=[{"type": "related"}],
                    category="internal_category",
                    memory_type=item.memory_type,
                )
            )
        return hits
