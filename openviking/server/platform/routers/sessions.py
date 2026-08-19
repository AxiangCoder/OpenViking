"""Session / Search / Dashboard 产品 API（11 §74，05 §12.5，14 号计划 §97.5）。

`/api/platform/v1` 下：

- `GET/POST /sessions`：当前 User 未删除 Session 列表 / 集成客户端幂等创建
  （11 §70.3/§70.6；`session.read.self`/`session.write.self`）；
- `GET /sessions/{id}`、`GET /sessions/{id}/messages`、
  `GET /sessions/{id}/memory-impact`：详情/已组装历史/脱敏 Memory Diff
  （11 §70.4/§71.3；`session.read.self`）；
- `POST /sessions/{id}/messages`、`POST /sessions/{id}/commit`：集成客户端
  幂等追加/Commit（11 §70.8/§71.1；`session.write.self`；Commit 不接受任何
  Retention 参数，服务端统一 3 Turn/12000 Token/至少 1 个最新 Assistant Step，
  AC⑤）；
- `DELETE /sessions/{id}`、`GET /recycle-bin`、`POST /recycle-bin/{id}/restore`：
  30 天软删除/回收站/恢复（11 §72；`session.delete.self`）；
- `POST /search/find`、`POST /search/search`：快速/结合会话检索，严格 DTO
  白名单（11 §69.2/§74.1；`session_id` 仅 search 接受且必须属当前 User，
  AC①②）；
- `GET /dashboard`：受控聚合（05 §12.5；AC⑨，当前 User 基础访问）。

写入口统一使用 `verify_integration_write`：浏览器 Session 走完整 CSRF，
User API Key/OAuth 委托凭证直接放行（11 §70.6 集成客户端链路）。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.db import get_session
from openviking.server.platform.dependencies import (
    get_current_principal,
    require_permission,
    verify_csrf,
    verify_integration_write,
)
from openviking.server.platform.errors import (
    DeletionJobError,
    EntityNotFoundError,
    InvalidSearchFilterError,
    SearchUnavailableError,
    SessionDeletedError,
    SessionNotFoundError,
    SessionWriteConflictError,
)
from openviking.server.platform.routers.platform import (
    deletion_job_result_dto,
    recycle_bin_row_dto,
)
from openviking.server.platform.search.service import SearchParams

router = APIRouter(prefix="/api/platform/v1", tags=["sessions"])

NOT_FOUND = "NOT_FOUND"


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _sessions(request: Request):
    return request.app.state.iam_session_service


def _search(request: Request):
    return request.app.state.iam_search_service


def _dashboard(request: Request):
    return request.app.state.iam_dashboard_service


def _deletion(request: Request):
    return request.app.state.iam_deletion_service


def _session_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SessionNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "SESSION_NOT_FOUND"})
    if isinstance(exc, SessionDeletedError):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "SESSION_DELETED"})
    if isinstance(exc, SessionWriteConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "SESSION_WRITE_CONFLICT"})
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"code": "INTERNAL_ERROR"})


def _search_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidSearchFilterError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": exc.reason})
    if isinstance(exc, SessionNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "SESSION_NOT_FOUND"})
    if isinstance(exc, SearchUnavailableError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "SEARCH_UNAVAILABLE"})
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"code": "INTERNAL_ERROR"})


# ── 严格 DTO（AC②：白名单外字段严格拒绝不静默透传，11 §69.2/§74）──


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_name: str = Field(min_length=1, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class AppendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=1_000_000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    created_at: str | None = Field(default=None, max_length=64)


class CommitRequest(BaseModel):
    """Commit 不接受任何 Retention 参数（11 §71.1：服务端统一，AC⑤）。

    只接受可选的 `idempotency_key`（11 §70.8：重复请求返回原写入结果，
    不重复触发 Commit，AC④）。
    """

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    context_type: Literal["memory", "resource", "skill"] | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    since: str | None = Field(default=None, max_length=64)
    until: str | None = Field(default=None, max_length=64)


class SessionSearchRequest(SearchRequest):
    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID


# ── Session（11 §74.2）──


@router.get("/sessions", dependencies=[Depends(require_permission("session.read.self"))])
async def list_sessions(
    request: Request,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    rows = await service.list_sessions(session, principal=principal)
    return {"status": "ok", "result": {"items": rows, "next_cursor": None}}


@router.post(
    "/sessions",
    dependencies=[
        Depends(verify_integration_write),
        Depends(require_permission("session.write.self")),
    ],
)
async def create_session(
    request: Request,
    body: CreateSessionRequest,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await service.create(
            session,
            principal=principal,
            client_name=body.client_name,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
        )
    except (SessionDeletedError, SessionWriteConflictError) as exc:
        raise _session_error(exc)
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "id": str(result.ref.id),
            "client_name": result.ref.client_name,
            "sync_status": result.ref.sync_status,
            "created": result.created,
        },
    }


@router.get(
    "/sessions/{session_id}",
    dependencies=[Depends(require_permission("session.read.self"))],
)
async def get_session_detail(
    request: Request,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        detail = await service.get_detail(session, principal=principal, session_id=session_id)
    except SessionNotFoundError as exc:
        raise _session_error(exc)
    return {"status": "ok", "result": detail}


@router.get(
    "/sessions/{session_id}/messages",
    dependencies=[Depends(require_permission("session.read.self"))],
)
async def get_session_messages(
    request: Request,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        messages = await service.get_messages(session, principal=principal, session_id=session_id)
    except SessionNotFoundError as exc:
        raise _session_error(exc)
    return {"status": "ok", "result": {"items": messages, "next_cursor": None}}


@router.post(
    "/sessions/{session_id}/messages",
    dependencies=[
        Depends(verify_integration_write),
        Depends(require_permission("session.write.self")),
    ],
)
async def append_message(
    request: Request,
    session_id: uuid.UUID,
    body: AppendMessageRequest,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await service.append_message(
            session,
            principal=principal,
            session_id=session_id,
            role=body.role,
            content=body.content,
            idempotency_key=body.idempotency_key,
            turn_id=body.turn_id,
            client_created_at=body.created_at,
            request_id=_request_id(request),
        )
    except (SessionNotFoundError, SessionDeletedError, SessionWriteConflictError) as exc:
        raise _session_error(exc)
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "session_id": str(session_id),
            "message_id": str(result.message.id),
            "sequence": result.message.seq,
            "message_count": result.message_count,
            "duplicate": result.duplicate,
        },
    }


@router.post(
    "/sessions/{session_id}/commit",
    dependencies=[
        Depends(verify_integration_write),
        Depends(require_permission("session.write.self")),
    ],
)
async def commit_session(
    request: Request,
    session_id: uuid.UUID,
    body: CommitRequest,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await service.commit(
            session,
            principal=principal,
            session_id=session_id,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
        )
    except (SessionNotFoundError, SessionDeletedError, SessionWriteConflictError) as exc:
        raise _session_error(exc)
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "commit_id": str(result.commit.id),
            "commit_number": result.commit.commit_number,
            "phase2_status": result.commit.phase2_status,
            "created": result.created,
        },
    }


@router.get(
    "/sessions/{session_id}/memory-impact",
    dependencies=[Depends(require_permission("session.read.self"))],
)
async def get_memory_impact(
    request: Request,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        impact = await service.get_memory_impact(session, principal=principal, session_id=session_id)
    except SessionNotFoundError as exc:
        raise _session_error(exc)
    return {"status": "ok", "result": impact}


@router.delete(
    "/sessions/{session_id}",
    dependencies=[
        Depends(verify_integration_write),
        Depends(require_permission("session.delete.self")),
    ],
)
async def delete_session(
    request: Request,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        job = await service.soft_delete(
            session, principal=principal, session_id=session_id, request_id=_request_id(request)
        )
    except SessionNotFoundError as exc:
        raise _session_error(exc)
    await session.commit()
    return {
        "status": "ok",
        "result": {
            "resource_type": "session",
            "resource_id": str(session_id),
            "deletion_job_id": str(job.id),
            "deleted_at": job.deleted_at.isoformat(),
            "restore_until": job.purge_after.isoformat(),
        },
    }


# ── 回收站（05 §12.5：当前 User；session.delete.self）──


@router.get("/recycle-bin", dependencies=[Depends(require_permission("session.delete.self"))])
async def list_recycle_bin(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal=Depends(get_current_principal),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    rows = await deletion.list_recycle_bin(
        session,
        scope="self",
        principal=principal,
        account_id=principal.actor_account_id,
        limit=limit,
    )
    return {
        "status": "ok",
        "result": {"items": [recycle_bin_row_dto(r) for r in rows], "next_cursor": None},
    }


@router.post("/recycle-bin/{job_id}/restore", dependencies=[Depends(verify_csrf)])
async def restore_session(
    request: Request,
    job_id: uuid.UUID,
    principal=Depends(require_permission("session.delete.self")),
    deletion=Depends(_deletion),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await deletion.restore(
            session,
            scope="self",
            principal=principal,
            account_id=principal.actor_account_id,
            job_id=job_id,
            request_id=_request_id(request),
        )
    except (SessionNotFoundError, EntityNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "SESSION_NOT_FOUND"}) from exc
    except DeletionJobError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.reason}) from exc
    await session.commit()
    return {"status": "ok", "result": deletion_job_result_dto(result)}


# ── Search（11 §74.1，05 §12.5）──


@router.post(
    "/search/find",
    dependencies=[Depends(require_permission("memory.read.self"))],
)
async def search_find(
    request: Request,
    body: SearchRequest,
    principal=Depends(get_current_principal),
    service=Depends(_search),
    session: AsyncSession = Depends(get_session),
):
    try:
        items = await service.find(
            session,
            principal=principal,
            params=SearchParams(
                query=body.query,
                context_type=body.context_type,
                tags=body.tags,
                since=body.since,
                until=body.until,
            ),
            request_id=_request_id(request),
        )
    except (InvalidSearchFilterError, SearchUnavailableError) as exc:
        raise _search_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": items}}


@router.post(
    "/search/search",
    dependencies=[
        Depends(require_permission("memory.read.self")),
        Depends(require_permission("session.read.self")),
    ],
)
async def search_with_session(
    request: Request,
    body: SessionSearchRequest,
    principal=Depends(get_current_principal),
    service=Depends(_search),
    session: AsyncSession = Depends(get_session),
):
    try:
        items = await service.search_with_session(
            session,
            principal=principal,
            params=SearchParams(
                query=body.query,
                context_type=body.context_type,
                tags=body.tags,
                since=body.since,
                until=body.until,
            ),
            session_id=body.session_id,
            request_id=_request_id(request),
        )
    except (InvalidSearchFilterError, SearchUnavailableError, SessionNotFoundError) as exc:
        raise _search_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": items}}


# ── Dashboard（05 §12.5，AC⑨）──


@router.get("/dashboard")
async def dashboard(
    request: Request,
    principal=Depends(get_current_principal),
    service=Depends(_dashboard),
    session: AsyncSession = Depends(get_session),
):
    return {"status": "ok", "result": await service.dashboard(session, principal=principal)}
