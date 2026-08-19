"""Session 产品服务（11 §70–§72，05 §12.5，14 号计划 §97.5）。

产品写入链路（11 §70.6）：集成客户端用 User API Key/OAuth Token 以当前
User 身份创建/追加/Commit；服务端校验 Session 归属、幂等标识与删除状态。

- **幂等**（AC④，11 §70.8）：创建按 `(account, owner, idempotency_key)`
  幂等键；追加按 `(session, idempotency_key)` 去重 + 服务端 seq 保序；
  Commit 由 sync_status 状态机防重；
- **Retention 服务端统一**（AC⑤，11 §71.1）：Commit 固定携带
  `RETENTION_PARAMS`（3 Turn/12000 Token/至少 1 个最新 Assistant Step），
  客户端不能调低（产品 API 不接受保留预算字段）；
- **软删/恢复**（AC⑦，11 §72）：软删立即隐藏并拒绝写入（`SESSION_DELETED`），
  30 天后 Purge Worker 经 Backend 幂等物理删除；恢复不回滚已产生的 Memory；
- **跨 User/Account 不可见语义**（AC⑧）：不存在/已删除/不可见统一
  `SESSION_NOT_FOUND`（404）；
- **成员只读**（11 §73）：admin/platform 成员数据接口同时记录 Actor 与
  Subject，不提供删除/恢复/Commit 等修改动作。

PostgreSQL 是归属/幂等/顺序/软删除事实来源；消息存储/归档/Memory 提取
由受控 `SessionBackend` 执行（真实 OpenViking 接线属 P5-E1）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.identity import RequestContext, Role
from openviking.server.platform.auth.access import DataAccessContext
from openviking.server.platform.auth.ov_context import to_ov_context
from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.errors import (
    EntityNotFoundError,
    SessionDeletedError,
    SessionNotFoundError,
    SessionWriteConflictError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import (
    IamAccount,
    IamDeletionJob,
    IamUser,
    PlatformSessionCommit,
    PlatformSessionMessage,
    PlatformSessionRef,
)
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.session.backend import (
    RETENTION_PARAMS,
    BackendMessage,
    CommitOutcome,
    SessionBackend,
)
from openviking.server.platform.session.repository import (
    PHASE2_COMPLETED,
    PHASE2_FAILED,
    PHASE2_PENDING,
    SYNC_ACTIVE,
    SYNC_COMMIT_FAILED,
    SYNC_COMMIT_PENDING,
    SYNC_COMMITTING,
    SessionRepository,
)
from openviking_cli.session.user_id import UserIdentifier

OBJECT_SESSION = "session"

# 会话永远 User 私有（11 §68：Memory/Session 无 Account 共享）。
SESSION_VISIBILITY = "user_private"


@dataclass(frozen=True)
class CreateResult:
    ref: PlatformSessionRef
    created: bool


@dataclass(frozen=True)
class AppendResult:
    message: PlatformSessionMessage
    message_count: int
    duplicate: bool


@dataclass(frozen=True)
class CommitResult:
    commit: PlatformSessionCommit
    created: bool


class SessionProductService:
    """Session 产品服务（事务由调用方控制；Router 负责 commit/rollback）。"""

    def __init__(
        self,
        repo: IamRepository,
        store: SessionRepository | None = None,
        registry: RegistryRepository | None = None,
        backend: SessionBackend | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._repo = repo
        self._store = store or SessionRepository()
        self._registry = registry or RegistryRepository()
        self._backend = backend
        self._config = config

    @property
    def backend(self) -> SessionBackend | None:
        return self._backend

    # ── 归属/上下文 ──

    async def _load_owner(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> tuple[IamUser, IamAccount]:
        """加载 Subject User 与 Account（未删除），供成员只读视图构造上下文。"""
        user = await self._repo.get_user(session, user_id)
        if user is None or user.deleted_at is not None or user.account_id is None:
            raise EntityNotFoundError(f"user {user_id} not visible")
        account = await self._repo.get_account(session, user.account_id)
        if account is None or account.deleted_at is not None:
            raise EntityNotFoundError(f"account {user.account_id} not visible")
        return user, account

    def _self_ov_context(
        self, principal, *, action: str, ov_session_id: str, request_id: str
    ) -> RequestContext:
        assert principal.actor_user_id is not None
        assert principal.actor_account_id is not None
        assert principal.actor_ov_account_id is not None
        assert principal.actor_ov_user_id is not None
        access = DataAccessContext(
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            subject_account_id=principal.actor_account_id,
            subject_user_id=principal.actor_user_id,
            subject_ov_account_id=principal.actor_ov_account_id,
            subject_ov_user_id=principal.actor_ov_user_id,
            visibility=SESSION_VISIBILITY,
            canonical_ov_uri=f"viking://user/{principal.actor_ov_user_id}/sessions/{ov_session_id}",
            action=action,
            request_id=request_id,
        )
        return to_ov_context(principal, access)

    async def _subject_ov_context(
        self,
        principal,
        session: AsyncSession,
        *,
        action: str,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        ov_session_id: str,
        request_id: str,
    ) -> RequestContext:
        """成员只读视图上下文：Subject 为目标 User，Actor 仍是管理员（11 §73）。"""
        user, account = await self._load_owner(session, subject_user_id)
        assert user.ov_user_id is not None and account.ov_account_id is not None
        access = DataAccessContext(
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            subject_account_id=account.id,
            subject_user_id=user.id,
            subject_ov_account_id=account.ov_account_id,
            subject_ov_user_id=user.ov_user_id,
            visibility=SESSION_VISIBILITY,
            canonical_ov_uri=f"viking://user/{user.ov_user_id}/sessions/{ov_session_id}",
            action=action,
            request_id=request_id,
        )
        return to_ov_context(principal, access)

    async def _owner_ov_context(
        self, session: AsyncSession, *, account_id: uuid.UUID, subject_user_id: uuid.UUID
    ) -> RequestContext:
        """受控系统路径（Purge）使用属主的最小权限上下文，不经过 Actor 授权门。"""
        user, account = await self._load_owner(session, subject_user_id)
        assert user.ov_user_id is not None and account.ov_account_id is not None
        return RequestContext(
            user=UserIdentifier(account.ov_account_id, user.ov_user_id),
            role=Role.USER,
            actor_peer_id=None,
        )

    def _require_backend(self) -> SessionBackend:
        if self._backend is None:
            raise RuntimeError("session backend not configured")
        return self._backend

    # ── 可见性（AC⑧：不存在/已删除/不可见统一 SESSION_NOT_FOUND）──

    def _owned_ref(
        self,
        ref: PlatformSessionRef | None,
        *,
        owner_user_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> PlatformSessionRef:
        if ref is None or ref.owner_user_id != owner_user_id or ref.account_id != account_id:
            raise SessionNotFoundError()
        return ref

    def _visible_ref(self, ref: PlatformSessionRef | None) -> PlatformSessionRef:
        if ref is None or ref.status != "active" or ref.deleted_at is not None:
            raise SessionNotFoundError()
        return ref

    # ── 幂等创建（11 §70.8，AC④）──

    async def create(
        self,
        session: AsyncSession,
        *,
        principal,
        client_name: str,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> CreateResult:
        assert principal.actor_account_id is not None
        assert principal.actor_user_id is not None
        assert principal.actor_ov_user_id is not None
        if idempotency_key:
            existing = await self._store.get_ref_by_idempotency_key(
                session, principal.actor_account_id, principal.actor_user_id, idempotency_key
            )
            if existing is not None:
                self._owned_ref(
                    existing,
                    owner_user_id=principal.actor_user_id,
                    account_id=principal.actor_account_id,
                )
                if existing.status != "active" or existing.deleted_at is not None:
                    raise SessionDeletedError()
                return CreateResult(ref=existing, created=False)

        backend = self._require_backend()
        ov_session_id = uuid.uuid4().hex
        ov_ctx = self._self_ov_context(
            principal, action="session.create", ov_session_id=ov_session_id, request_id=request_id or ""
        )
        try:
            ov_uri = await backend.create(
                account_ov_id=ov_ctx.user.account_id,
                user_ov_id=ov_ctx.user.user_id,
                ov_session_id=ov_session_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise SessionWriteConflictError(str(exc)) from exc

        ref = PlatformSessionRef(
            account_id=principal.actor_account_id,
            owner_user_id=principal.actor_user_id,
            ov_session_id=ov_session_id,
            ov_uri=ov_uri,
            client_name=client_name,
            idempotency_key=idempotency_key,
            sync_status=SYNC_ACTIVE,
            created_by_actor_user_id=principal.actor_user_id,
        )
        await self._store.insert_ref(session, ref)
        await session.flush()
        await self._audit(
            session,
            request_id=request_id,
            principal=principal,
            subject_user_id=principal.actor_user_id,
            action="session.create",
            target_id=str(ref.id),
            metadata={"client_name": client_name, "idempotency": bool(idempotency_key)},
        )
        return CreateResult(ref=ref, created=True)

    # ── 幂等追加（11 §70.6/§70.8，AC④）──

    async def append_message(
        self,
        session: AsyncSession,
        *,
        principal,
        session_id: uuid.UUID,
        role: str,
        content: str,
        idempotency_key: str | None,
        turn_id: str | None,
        client_created_at: str | None,
        request_id: str | None,
    ) -> AppendResult:
        ref = await self._load_owned_for_write(session, principal, session_id)
        if idempotency_key:
            existing = await self._store.get_message_by_idempotency_key(
                session, ref.id, idempotency_key
            )
            if existing is not None:
                if existing.role != role or existing.content != content:
                    raise SessionWriteConflictError()
                return AppendResult(
                    message=existing, message_count=ref.message_count, duplicate=True
                )

        seq = await self._store.next_seq(session, ref.id)
        now = datetime.now(timezone.utc)
        message = PlatformSessionMessage(
            session_id=ref.id,
            seq=seq,
            idempotency_key=idempotency_key,
            role=role,
            content=content,
            turn_id=turn_id,
            client_created_at=client_created_at,
        )
        await self._store.insert_message(session, message)
        await session.flush()

        backend = self._require_backend()
        ov_ctx = self._self_ov_context(
            principal,
            action="session.append",
            ov_session_id=ref.ov_session_id,
            request_id=request_id or "",
        )
        try:
            await backend.append(
                account_ov_id=ov_ctx.user.account_id,
                user_ov_id=ov_ctx.user.user_id,
                ov_session_id=ref.ov_session_id,
                message=BackendMessage(
                    seq=seq,
                    role=role,
                    content=content,
                    idempotency_key=idempotency_key,
                    turn_id=turn_id,
                    client_created_at=client_created_at,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise SessionWriteConflictError(str(exc)) from exc

        ref.message_count = int(ref.message_count or 0) + 1
        await self._store.bump_ref_activity(
            session, ref.id, now=now, message_count=ref.message_count
        )
        await session.flush()
        await self._audit(
            session,
            request_id=request_id,
            principal=principal,
            subject_user_id=principal.actor_user_id,
            action="session.append",
            target_id=str(ref.id),
            metadata={
                "message_count": ref.message_count,
                "content_length": len(content),
                "client_name": ref.client_name,
                "sequence": seq,
            },
        )
        return AppendResult(message=message, message_count=ref.message_count, duplicate=False)

    # ── Commit（11 §71，AC⑤）──

    async def commit(
        self,
        session: AsyncSession,
        *,
        principal,
        session_id: uuid.UUID,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> CommitResult:
        ref = await self._load_owned_for_write(session, principal, session_id)
        if idempotency_key:
            existing = await self._store.get_commit_by_idempotency_key(
                session, ref.id, idempotency_key
            )
            if existing is not None:
                return CommitResult(commit=existing, created=False)
        if ref.sync_status in (SYNC_COMMIT_PENDING, SYNC_COMMITTING):
            commits = await self._store.list_commits(session, ref.id)
            if commits:
                return CommitResult(commit=commits[-1], created=False)

        now = datetime.now(timezone.utc)
        ref.sync_status = SYNC_COMMITTING
        await self._store.update_ref(session, ref)
        await session.flush()

        backend = self._require_backend()
        ov_ctx = self._self_ov_context(
            principal,
            action="session.commit",
            ov_session_id=ref.ov_session_id,
            request_id=request_id or "",
        )
        try:
            outcome: CommitOutcome = await backend.commit(
                account_ov_id=ov_ctx.user.account_id,
                user_ov_id=ov_ctx.user.user_id,
                ov_session_id=ref.ov_session_id,
                retention=dict(RETENTION_PARAMS),
            )
        except Exception as exc:  # noqa: BLE001
            ref.sync_status = SYNC_COMMIT_FAILED
            await self._store.update_ref(session, ref)
            raise SessionWriteConflictError(str(exc)) from exc

        commit = PlatformSessionCommit(
            session_id=ref.id,
            commit_number=outcome.commit_number,
            idempotency_key=idempotency_key,
            ov_task_id=outcome.ov_task_id,
            phase2_status=PHASE2_PENDING,
            message_count_at_commit=outcome.message_count_at_commit,
        )
        await self._store.insert_commit(session, commit)
        await session.flush()
        ref.commit_count = int(ref.commit_count or 0) + 1
        ref.sync_status = SYNC_ACTIVE
        await self._store.bump_ref_activity(
            session,
            ref.id,
            now=now,
            commit_count=ref.commit_count,
            sync_status=SYNC_ACTIVE,
        )
        await session.flush()
        await self._audit(
            session,
            request_id=request_id,
            principal=principal,
            subject_user_id=principal.actor_user_id,
            action="session.commit",
            target_id=str(ref.id),
            metadata={
                "commit_number": commit.commit_number,
                "messages": outcome.message_count_at_commit,
                "client_name": ref.client_name,
            },
        )
        return CommitResult(commit=commit, created=True)

    # ── 读取（11 §70.3/§70.4/§71.3）──

    async def list_sessions(self, session: AsyncSession, *, principal) -> list[dict]:
        assert principal.actor_account_id is not None
        rows = await self._store.list_refs(
            session,
            account_id=principal.actor_account_id,
            owner_user_id=principal.actor_user_id,
            status="active",
        )
        return [self._list_dto(r) for r in rows]

    async def get_detail(
        self, session: AsyncSession, *, principal, session_id: uuid.UUID
    ) -> dict:
        ref = await self._load_owned(session, principal, session_id)
        return self._detail_dto(ref)

    async def get_messages(
        self, session: AsyncSession, *, principal, session_id: uuid.UUID
    ) -> list[dict]:
        ref = await self._load_owned(session, principal, session_id)
        backend = self._require_backend()
        ov_ctx = self._self_ov_context(
            principal, action="session.read", ov_session_id=ref.ov_session_id, request_id=""
        )
        messages = await backend.get_messages(
            account_ov_id=ov_ctx.user.account_id,
            user_ov_id=ov_ctx.user.user_id,
            ov_session_id=ref.ov_session_id,
        )
        return self._message_dtos(messages)

    async def get_memory_impact(
        self, session: AsyncSession, *, principal, session_id: uuid.UUID
    ) -> dict:
        ref = await self._load_owned(session, principal, session_id)
        commits = await self._store.list_commits(session, ref.id)
        for commit in commits:
            await self._refresh_phase2(session, principal, ref, commit)
        totals = {"added": 0, "updated": 0, "deleted": 0}
        action_key = {"add": "added", "update": "updated", "delete": "deleted"}
        items = []
        for commit in commits:
            for entry in commit.diff_json or []:
                key = action_key.get(entry.get("action", ""))
                if key is not None:
                    totals[key] += 1
            items.append(self._commit_dto(commit))
        return {
            "session_id": str(ref.id),
            "commit_count": int(ref.commit_count or 0),
            "totals": totals,
            "items": items,
        }

    async def _refresh_phase2(
        self,
        session: AsyncSession,
        principal,
        ref: PlatformSessionRef,
        commit: PlatformSessionCommit,
    ) -> None:
        """轮询后端 Phase 2 状态并落库（11 §71.3：pending/running/completed/failed）。"""
        if commit.phase2_status in (PHASE2_COMPLETED, PHASE2_FAILED):
            return
        backend = self._require_backend()
        ov_ctx = self._self_ov_context(
            principal,
            action="session.impact",
            ov_session_id=ref.ov_session_id,
            request_id="",
        )
        status, diff, error = await backend.get_commit_status(
            account_ov_id=ov_ctx.user.account_id,
            user_ov_id=ov_ctx.user.user_id,
            ov_session_id=ref.ov_session_id,
            commit_number=commit.commit_number,
        )
        now = datetime.now(timezone.utc)
        if status == PHASE2_COMPLETED:
            await self._store.update_commit_phase2(
                session, commit.id, phase2_status=status, now=now, diff=diff
            )
        else:
            await self._store.update_commit_phase2(
                session, commit.id, phase2_status=status, now=now, phase2_error=error
            )
        await session.flush()
        await session.refresh(commit)

    # ── 软删（11 §72：DeletionService 统一回收站/恢复）──

    async def soft_delete(
        self,
        session: AsyncSession,
        *,
        principal,
        session_id: uuid.UUID,
        request_id: str | None,
    ) -> IamDeletionJob:
        """软删立即隐藏并拒绝写入（AC⑦）；重复删除幂等返回同一 job。"""
        ref = await self._store.get_ref(session, session_id)
        ref = self._owned_ref(
            ref,
            owner_user_id=principal.actor_user_id,
            account_id=principal.actor_account_id,
        )
        assert principal.actor_account_id is not None
        existing = await self._registry.get_active_deletion_job(
            session, OBJECT_SESSION, str(ref.id)
        )
        if existing is not None:
            return existing
        if ref.status != "active" or ref.deleted_at is not None:
            raise SessionNotFoundError()
        now = datetime.now(timezone.utc)
        ref.status = "deleted"
        ref.deleted_at = now
        ref.purge_after = now + timedelta(days=self._config.deletion_purge_days)
        ref.deleted_by = principal.actor_user_id
        await self._store.update_ref(session, ref)
        job = IamDeletionJob(
            account_id=ref.account_id,
            resource_type=OBJECT_SESSION,
            resource_id=str(ref.id),
            ov_uri=ref.ov_uri,
            deleted_by=principal.actor_user_id,
            deleted_at=now,
            purge_after=ref.purge_after,
            status="pending",
        )
        await self._registry.insert_deletion_job(session, job)
        await session.flush()
        await self._audit(
            session,
            request_id=request_id,
            principal=principal,
            subject_user_id=principal.actor_user_id,
            action="session.delete",
            target_id=str(ref.id),
            metadata={"deletion_job_id": str(job.id), "purge_after": job.purge_after.isoformat()},
        )
        return job

    # ── 成员只读（11 §73：Actor/Subject 同时记录；无修改动作）──

    async def list_member_sessions(
        self,
        session: AsyncSession,
        *,
        principal,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
    ) -> list[dict]:
        user, _ = await self._load_owner(session, subject_user_id)
        if user.account_id != account_id:
            raise EntityNotFoundError(f"user {subject_user_id} not visible")
        rows = await self._store.list_refs_by_user(
            session, account_id=account_id, owner_user_id=subject_user_id, status="active"
        )
        await self._audit(
            session,
            request_id=None,
            principal=principal,
            subject_user_id=subject_user_id,
            action="session.list.admin",
            target_id=str(subject_user_id),
            metadata={"subject_user_id": str(subject_user_id), "count": len(rows)},
        )
        return [self._list_dto(r) for r in rows]

    async def get_member_detail(
        self,
        session: AsyncSession,
        *,
        principal,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> dict:
        ref = await self._member_owned_ref(session, account_id, subject_user_id, session_id)
        await self._audit(
            session,
            request_id=None,
            principal=principal,
            subject_user_id=subject_user_id,
            action="session.read.admin",
            target_id=str(ref.id),
            metadata={"subject_user_id": str(subject_user_id)},
        )
        return self._detail_dto(ref)

    async def get_member_messages(
        self,
        session: AsyncSession,
        *,
        principal,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> list[dict]:
        ref = await self._member_owned_ref(session, account_id, subject_user_id, session_id)
        ov_ctx = await self._subject_ov_context(
            principal,
            session,
            action="session.read",
            account_id=account_id,
            subject_user_id=subject_user_id,
            ov_session_id=ref.ov_session_id,
            request_id="",
        )
        backend = self._require_backend()
        messages = await backend.get_messages(
            account_ov_id=ov_ctx.user.account_id,
            user_ov_id=ov_ctx.user.user_id,
            ov_session_id=ref.ov_session_id,
        )
        await self._audit(
            session,
            request_id=None,
            principal=principal,
            subject_user_id=subject_user_id,
            action="session.messages.admin",
            target_id=str(ref.id),
            metadata={"subject_user_id": str(subject_user_id), "count": len(messages)},
        )
        return self._message_dtos(messages)

    async def get_member_memory_impact(
        self,
        session: AsyncSession,
        *,
        principal,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> dict:
        ref = await self._member_owned_ref(session, account_id, subject_user_id, session_id)
        commits = await self._store.list_commits(session, ref.id)
        for commit in commits:
            await self._refresh_member_phase2(
                session, principal, account_id, subject_user_id, ref, commit
            )
        totals = {"added": 0, "updated": 0, "deleted": 0}
        action_key = {"add": "added", "update": "updated", "delete": "deleted"}
        items = []
        for commit in commits:
            for entry in commit.diff_json or []:
                key = action_key.get(entry.get("action", ""))
                if key is not None:
                    totals[key] += 1
            items.append(self._commit_dto(commit))
        await self._audit(
            session,
            request_id=None,
            principal=principal,
            subject_user_id=subject_user_id,
            action="session.impact.admin",
            target_id=str(ref.id),
            metadata={"subject_user_id": str(subject_user_id), "commits": len(items)},
        )
        return {
            "session_id": str(ref.id),
            "commit_count": int(ref.commit_count or 0),
            "totals": totals,
            "items": items,
        }

    async def _refresh_member_phase2(
        self,
        session: AsyncSession,
        principal,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        ref: PlatformSessionRef,
        commit: PlatformSessionCommit,
    ) -> None:
        if commit.phase2_status in (PHASE2_COMPLETED, PHASE2_FAILED):
            return
        ov_ctx = await self._subject_ov_context(
            principal,
            session,
            action="session.impact",
            account_id=account_id,
            subject_user_id=subject_user_id,
            ov_session_id=ref.ov_session_id,
            request_id="",
        )
        backend = self._require_backend()
        status, diff, error = await backend.get_commit_status(
            account_ov_id=ov_ctx.user.account_id,
            user_ov_id=ov_ctx.user.user_id,
            ov_session_id=ref.ov_session_id,
            commit_number=commit.commit_number,
        )
        now = datetime.now(timezone.utc)
        if status == PHASE2_COMPLETED:
            await self._store.update_commit_phase2(
                session, commit.id, phase2_status=status, now=now, diff=diff
            )
        else:
            await self._store.update_commit_phase2(
                session, commit.id, phase2_status=status, now=now, phase2_error=error
            )
        await session.flush()
        await session.refresh(commit)

    async def _member_owned_ref(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> PlatformSessionRef:
        ref = await self._store.get_ref(session, session_id)
        if (
            ref is None
            or ref.account_id != account_id
            or ref.owner_user_id != subject_user_id
            or ref.status != "active"
            or ref.deleted_at is not None
        ):
            raise SessionNotFoundError()
        return ref

    # ── 物理删除（Purge Worker，11 §72：30 天后幂等；AC⑦）──

    async def purge(self, session: AsyncSession, job: IamDeletionJob) -> None:
        """Session 期满物理清理：Backend 幂等删除 + 本表状态归位。"""
        ref = await self._store.get_ref(session, uuid.UUID(job.resource_id), include_deleted=True)
        if ref is None:
            return
        if ref.ov_session_id:
            ov_ctx = await self._owner_ov_context(
                session, account_id=ref.account_id, subject_user_id=ref.owner_user_id
            )
            backend = self._require_backend()
            await backend.delete(
                account_ov_id=ov_ctx.user.account_id,
                user_ov_id=ov_ctx.user.user_id,
                ov_session_id=ref.ov_session_id,
            )
        ref.status = "purged"
        await self._store.update_ref(session, ref)
        await session.flush()

    # ── DTO（产品输出，不返回 URI/内部标识，AC③/AC⑥）──

    def _list_dto(self, ref: PlatformSessionRef) -> dict:
        return {
            "id": str(ref.id),
            "client_name": ref.client_name,
            "title": self._display_title(ref),
            "sync_status": ref.sync_status,
            "commit_count": int(ref.commit_count or 0),
            "message_count": int(ref.message_count or 0),
            "last_sync_at": ref.last_sync_at.isoformat() if ref.last_sync_at else None,
            "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
            "created_at": ref.created_at.isoformat() if ref.created_at else None,
        }

    def _detail_dto(self, ref: PlatformSessionRef) -> dict:
        return {
            **self._list_dto(ref),
            "pending_tokens": 0,
        }

    @staticmethod
    def _display_title(ref: PlatformSessionRef) -> str:
        """11 §70.2：v0.1 不新增标题字段，列表显示「来源客户端 + ID 短标识」。"""
        return f"{ref.client_name} #{ref.ov_session_id[:8]}"

    @staticmethod
    def _message_dtos(messages: list[dict]) -> list[dict]:
        return [
            {
                "role": m.get("role"),
                "content": m.get("content"),
                "sequence": m.get("seq"),
                "turn_id": m.get("turn_id"),
                "created_at": m.get("created_at"),
            }
            for m in messages
        ]

    @staticmethod
    def _commit_dto(commit: PlatformSessionCommit) -> dict:
        return {
            "commit_id": str(commit.id),
            "commit_number": commit.commit_number,
            "phase2_status": commit.phase2_status,
            "phase2_error": commit.phase2_error,
            "message_count_at_commit": int(commit.message_count_at_commit or 0),
            "created_at": commit.created_at.isoformat() if commit.created_at else None,
            "completed_at": commit.completed_at.isoformat() if commit.completed_at else None,
            "has_operations": bool(commit.diff_json),
            "diffs": commit.diff_json or [],
        }

    # ── 内部 ──

    async def _load_owned(
        self, session: AsyncSession, principal, session_id: uuid.UUID
    ) -> PlatformSessionRef:
        ref = await self._store.get_ref(session, session_id)
        return self._visible_ref(
            self._owned_ref(
                ref,
                owner_user_id=principal.actor_user_id,
                account_id=principal.actor_account_id,
            )
        )

    async def _load_owned_for_write(
        self, session: AsyncSession, principal, session_id: uuid.UUID
    ) -> PlatformSessionRef:
        """写入门禁（AC⑦/AC⑧）：不可见 → 404；回收期 → SESSION_DELETED。"""
        ref = await self._store.get_ref(session, session_id)
        ref = self._owned_ref(
            ref,
            owner_user_id=principal.actor_user_id,
            account_id=principal.actor_account_id,
        )
        if ref.status != "active" or ref.deleted_at is not None:
            raise SessionDeletedError()
        return ref

    async def _audit(
        self,
        session: AsyncSession,
        *,
        request_id: str | None,
        principal,
        subject_user_id: uuid.UUID | None,
        action: str,
        target_id: str | None,
        metadata: dict | None = None,
    ) -> None:
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=principal.actor_account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_account_id=principal.actor_account_id,
            subject_user_id=subject_user_id,
            action=action,
            target_type="platform_session_refs",
            target_id=target_id,
            target_visibility=SESSION_VISIBILITY,
            scope="self",
            result="success",
            metadata=metadata,
        )
