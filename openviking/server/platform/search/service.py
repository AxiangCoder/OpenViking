"""Search 产品服务（11 §69/§74.1，05 §12.5，14 号计划 §97.5）。

- 固定检索根（11 §69.2）：当前 User 私有根 + 当前 Account 共享根，由
  服务端构造（`viking://user/{ov_user}/` + `viking://resources/` +
  `viking://agent/skills/`）；客户端不能提交 `target_uri`/`filter`/
  `score_threshold`/`level`/`limit`/`time_field` 等调试字段（严格 DTO，
  AC②）；
- 字段白名单：`query`（必填）、`context_type`（memory/resource/skill）、
  `tags`（严格 `key=value`，AND 关系）、`since/until`（固定映射
  `updated_at`）；`session_id` 仅 `/search/search` 接受且必须属当前 User
  并未删除（AC①），`/search/find` 不接受（find 不加载 Session）；
- 结果 DTO 脱敏（AC③，05 §12.5）：删除 uri/score/level/query_plan/
  provenance/relations/category；`visibility` 由服务端按已授权 canonical
  URI 分类（user_private/account_shared）；Memory 结果只含类型/摘要/
  匹配原因，不提供详情读取；
- 每目标对象权限（11 §74.1「各结果 read Permission」）：DTO 阶段按
  context_type × visibility 过滤无对应读取权限的命中；
- 管理员成员只读检索（11 §73/§74.1）：Subject 固定为目标 User 私有根，
  Actor/Subject 同时写审计（记录模式/类型/标签数/时间范围/结果数 +
  脱敏 Query 哈希，11 §76）。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.errors import (
    EntityNotFoundError,
    InvalidSearchFilterError,
    SearchUnavailableError,
    SessionNotFoundError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.registry.repository import (
    STATUS_ACTIVE,
    RegistryRepository,
)
from openviking.server.platform.registry.tags import normalize_tags
from openviking.server.platform.session.backend import RawHit, SearchEngine
from openviking.server.platform.session.repository import SessionRepository

CONTEXT_TYPES = ("memory", "resource", "skill")

# 05 §12.5：后端固定时间字段（客户端不能提交 time_field）。
TIME_FIELD = "updated_at"

# 每目标对象读取权限（11 §74.1「各结果 read Permission」）。
_PERMISSION_BY_CONTEXT: dict[str, dict[str, str]] = {
    "memory": {"user_private": "memory.read.self"},
    "resource": {
        "user_private": "resource.user_private.read.self",
        "account_shared": "resource.account_shared.read.account",
    },
    "skill": {
        "user_private": "skill.user_private.read.self",
        "account_shared": "skill.account_shared.read.account",
    },
}

# 管理员成员只读检索的每对象权限（admin/platform scope）。
_MEMBER_PERMISSION_BY_CONTEXT: dict[str, dict[str, dict[str, str]]] = {
    "admin": {
        "memory": {"user_private": "memory.read.account"},
        "resource": {
            "user_private": "resource.user_private.read.account",
            "account_shared": "resource.account_shared.read.account",
        },
        "skill": {
            "user_private": "skill.user_private.read.account",
            "account_shared": "skill.account_shared.read.account",
        },
    },
    "platform": {
        "memory": {"user_private": "memory.read.platform"},
        "resource": {
            "user_private": "resource.user_private.read.platform",
            "account_shared": "resource.account_shared.read.platform",
        },
        "skill": {
            "user_private": "skill.user_private.read.platform",
            "account_shared": "skill.account_shared.read.platform",
        },
    },
}

_ACCOUNT_SHARED_ROOTS = ("viking://resources/", "viking://agent/skills/")


@dataclass(frozen=True)
class SearchParams:
    """产品检索参数（严格白名单；Router 的严格 DTO 已拒绝调试字段，AC②）。"""

    query: str
    context_type: str | None = None
    tags: list[str] | None = None
    since: str | None = None
    until: str | None = None


def search_roots(ov_user_id: str) -> list[str]:
    """固定检索根（11 §69.2/§68）：User 私有根 + Account 共享根。"""
    return [f"viking://user/{ov_user_id}/", *_ACCOUNT_SHARED_ROOTS]


def validate_time_range(since: str | None, until: str | None) -> None:
    """时间范围校验（11 §75.2 `INVALID_SEARCH_FILTER`）。"""
    try:
        if since is not None:
            datetime.fromisoformat(since.replace("Z", "+00:00"))
        if until is not None:
            datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidSearchFilterError("INVALID_TIME_RANGE") from exc
    if since and until and since > until:
        raise InvalidSearchFilterError("INVALID_TIME_RANGE")


def _hash_query(query: str) -> str:
    """11 §76：审计不记录原始 Query，记录 SHA-256 摘要。"""
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()


class SearchProductService:
    """Search 产品服务（事务由调用方控制）。"""

    def __init__(
        self,
        repo: IamRepository,
        store: SessionRepository | None = None,
        registry: RegistryRepository | None = None,
        engine: SearchEngine | None = None,
    ) -> None:
        self._repo = repo
        self._store = store or SessionRepository()
        self._registry = registry or RegistryRepository()
        self._engine = engine

    @property
    def engine(self) -> SearchEngine | None:
        return self._engine

    def _require_engine(self) -> SearchEngine:
        if self._engine is None:
            raise SearchUnavailableError()
        return self._engine

    # ── 自检索（11 §74.1：/search/find、/search/search）──

    async def find(
        self,
        session: AsyncSession,
        *,
        principal,
        params: SearchParams,
        request_id: str | None,
    ) -> list[dict]:
        return await self._search(
            session,
            principal=principal,
            subject_user_id=principal.actor_user_id,
            scope="self",
            params=params,
            request_id=request_id,
            session_id=None,
        )

    async def search_with_session(
        self,
        session: AsyncSession,
        *,
        principal,
        params: SearchParams,
        session_id: uuid.UUID,
        request_id: str | None,
    ) -> list[dict]:
        return await self._search(
            session,
            principal=principal,
            subject_user_id=principal.actor_user_id,
            scope="self",
            params=params,
            request_id=request_id,
            session_id=session_id,
        )

    # ── 成员只读检索（11 §73/§74.1：admin/platform）──

    async def member_find(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: str,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        params: SearchParams,
        request_id: str | None,
    ) -> list[dict]:
        user, account = await self._load_subject(session, account_id, subject_user_id)
        assert user.ov_user_id is not None and account.ov_account_id is not None
        roots = [f"viking://user/{user.ov_user_id}/"]
        items = await self._run_engine(
            session=session,
            principal=principal,
            scope=scope,
            account_id=account_id,
            subject_user_id=subject_user_id,
            ov_account_id=account.ov_account_id,
            ov_user_id=user.ov_user_id,
            roots=roots,
            params=params,
            session_id=None,
        )
        self._audit_search(
            session,
            principal=principal,
            subject_user_id=subject_user_id,
            scope=scope,
            mode="find",
            params=params,
            result_count=len(items),
            request_id=request_id,
        )
        return items

    # ── 内部 ──

    async def _search(
        self,
        session: AsyncSession,
        *,
        principal,
        subject_user_id: uuid.UUID,
        scope: str,
        params: SearchParams,
        request_id: str | None,
        session_id: uuid.UUID | None,
    ) -> list[dict]:
        assert principal.actor_account_id is not None
        assert principal.actor_ov_account_id is not None
        assert principal.actor_ov_user_id is not None
        ov_session_id: str | None = None
        if session_id is not None:
            ref = await self._store.get_ref(session, session_id)
            if (
                ref is None
                or ref.account_id != principal.actor_account_id
                or ref.owner_user_id != principal.actor_user_id
                or ref.status != "active"
                or ref.deleted_at is not None
            ):
                raise SessionNotFoundError()
            ov_session_id = ref.ov_session_id
        roots = search_roots(principal.actor_ov_user_id)
        items = await self._run_engine(
            session=session,
            principal=principal,
            scope=scope,
            account_id=principal.actor_account_id,
            subject_user_id=subject_user_id,
            ov_account_id=principal.actor_ov_account_id,
            ov_user_id=principal.actor_ov_user_id,
            roots=roots,
            params=params,
            session_id=ov_session_id,
        )
        self._audit_search(
            session,
            principal=principal,
            subject_user_id=subject_user_id,
            scope=scope,
            mode="search" if session_id is not None else "find",
            params=params,
            result_count=len(items),
            request_id=request_id,
            session_id=str(session_id) if session_id is not None else None,
        )
        return items

    async def _run_engine(
        self,
        session: AsyncSession,
        *,
        principal,
        scope: str,
        account_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        ov_account_id: str,
        ov_user_id: str,
        roots: list[str],
        params: SearchParams,
        session_id: str | None,
    ) -> list[dict]:
        validate_time_range(params.since, params.until)
        try:
            tags = normalize_tags(params.tags)
        except Exception as exc:  # noqa: BLE001
            raise InvalidSearchFilterError(str(exc)) from exc

        engine = self._require_engine()
        try:
            if session_id is None:
                raw_hits = await engine.find(
                    account_ov_id=ov_account_id,
                    user_ov_id=ov_user_id,
                    roots=roots,
                    query=params.query,
                    context_type=params.context_type,
                    tags=tags,
                    since=params.since,
                    until=params.until,
                )
            else:
                raw_hits = await engine.search(
                    account_ov_id=ov_account_id,
                    user_ov_id=ov_user_id,
                    roots=roots,
                    query=params.query,
                    context_type=params.context_type,
                    tags=tags,
                    since=params.since,
                    until=params.until,
                    session_ov_id=session_id,
                )
        except Exception as exc:  # noqa: BLE001
            raise SearchUnavailableError(str(exc)) from exc

        refs = await self._registry.list_refs(
            session,
            account_id=account_id,
            owner_user_id=subject_user_id if scope != "self" else None,
            status=STATUS_ACTIVE,
        )
        ref_by_uri = {r.ov_uri: r for r in refs}

        permissions = self._permissions_of(scope, principal=principal)
        items: list[dict] = []
        for hit in raw_hits:
            visibility = self._classify_visibility(
                hit, subject_ov_user_id=ov_user_id
            )
            if visibility is None:
                continue
            dto = self._dto(hit, visibility=visibility, scope=scope, permissions=permissions)
            if dto is None:
                continue  # 无对应读取权限（11 §74.1 每目标对象 read Permission）
            if hit.context_type in ("resource", "skill"):
                ref = ref_by_uri.get(hit.uri)
                dto["ref_id"] = str(ref.id) if ref is not None else None
            items.append(dto)
        return items

    @staticmethod
    def _permissions_of(scope: str, *, principal) -> frozenset[str]:
        """权限集合：self 使用主权限全集；成员只读由 Router 门禁保证基础码。"""
        return frozenset(getattr(principal, "permissions", ()))

    def _classify_visibility(self, hit: RawHit, *, subject_ov_user_id: str) -> str | None:
        """visibility 服务端分类（05 §12.5；客户端不能提交，AC③）。"""
        if hit.uri.startswith(f"viking://user/{subject_ov_user_id}/"):
            return "user_private"
        if hit.uri.startswith(_ACCOUNT_SHARED_ROOTS):
            return "account_shared"
        return None

    def _dto(
        self, hit: RawHit, *, visibility: str, scope: str, permissions: frozenset[str]
    ) -> dict | None:
        """结果 DTO 脱敏（AC③）+ 每目标对象权限过滤（11 §74.1）。"""
        needed = self._read_permission(hit.context_type, visibility, scope)
        if needed is not None and needed not in permissions:
            return None  # 无对应读取权限的命中不返回
        dto: dict = {
            "context_type": hit.context_type,
            "display_name": hit.display_name,
            "abstract": hit.abstract,
            "match_reason": hit.match_reason,
            "visibility": visibility,
        }
        if hit.context_type == "memory":
            dto["memory_type"] = hit.memory_type
        return dto

    def _read_permission(
        self, context_type: str, visibility: str, scope: str
    ) -> str | None:
        """返回该命中所需的读取权限码；scope 表缺失映射时返回 None。"""
        table = (
            _MEMBER_PERMISSION_BY_CONTEXT.get(scope, {})
            if scope != "self"
            else _PERMISSION_BY_CONTEXT
        )
        return table.get(context_type, {}).get(visibility)

    async def _load_subject(
        self, session: AsyncSession, account_id: uuid.UUID, subject_user_id: uuid.UUID
    ) -> tuple[object, object]:
        """成员只读 Subject 校验（11 §73：只能选择本 Account User）。"""
        user = await self._repo.get_user(session, subject_user_id)
        if user is None or user.deleted_at is not None or user.account_id != account_id:
            raise EntityNotFoundError(f"user {subject_user_id} not visible")
        account = await self._repo.get_account(session, account_id)
        if account is None or account.deleted_at is not None:
            raise EntityNotFoundError(f"account {account_id} not visible")
        return user, account

    def _audit_search(
        self,
        session: AsyncSession,
        *,
        principal,
        subject_user_id: uuid.UUID,
        scope: str,
        mode: str,
        params: SearchParams,
        result_count: int,
        request_id: str | None,
        session_id: str | None = None,
    ) -> None:
        """11 §76：Search 审计记录模式/类型/标签数/时间范围/结果数 +
        脱敏 Query 哈希（不记录原始 Query）。"""
        self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=principal.actor_account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_account_id=principal.actor_account_id,
            subject_user_id=subject_user_id if scope != "self" else None,
            action="search.find" if mode == "find" else "search.search",
            target_type="search",
            scope=scope,
            result="success",
            metadata={
                "mode": mode,
                "context_type": params.context_type,
                "tag_count": len(params.tags or []),
                "time_range": [params.since, params.until],
                "result_count": result_count,
                "query_hash": _hash_query(params.query),
                "session_id": session_id,
            },
        )
