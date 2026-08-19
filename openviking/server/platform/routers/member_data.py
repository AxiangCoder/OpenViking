"""admin/platform 成员只读 Session/Search（11 §73/§74，14 号计划 §97.5）。

- `GET /admin/users/{user_id}/sessions[/{session_id}[/messages|memory-impact]]`：
  Account Admin 按 Subject 只读（`session.read.account`）；
- `GET /platform/accounts/{account_id}/users/{user_id}/sessions[...]`：
  Platform Super Admin 先固定 Account 再选择 User（`session.read.platform`）；
- `POST /admin/users/{user_id}/search/find`：Account 成员只读检索
  （`memory.read.account` 等目标读取权限）；
- `POST /platform/accounts/{account_id}/users/{user_id}/search/find`：
  平台成员只读检索（`memory.read.platform` 等目标读取权限）。

成员数据页显示「正在查看 Subject User 数据」，审计同时记录 Actor 与
Subject（11 §73）；不提供删除/恢复/Commit/Extract 等修改动作。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.db import get_session
from openviking.server.platform.dependencies import (
    get_current_principal,
    require_permission,
)
from openviking.server.platform.errors import (
    EntityNotFoundError,
    InvalidSearchFilterError,
    SearchUnavailableError,
    SessionNotFoundError,
)
from openviking.server.platform.routers.sessions import SearchRequest
from openviking.server.platform.search.service import SearchParams

router = APIRouter(prefix="/api/platform/v1", tags=["member-data"])

NOT_FOUND = "NOT_FOUND"


def _sessions(request: Request):
    return request.app.state.iam_session_service


def _search(request: Request):
    return request.app.state.iam_search_service


def _member_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EntityNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND})
    if isinstance(exc, SessionNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "SESSION_NOT_FOUND"})
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"code": "INTERNAL_ERROR"})


def _member_search_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EntityNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": NOT_FOUND})
    if isinstance(exc, InvalidSearchFilterError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": exc.reason})
    if isinstance(exc, SearchUnavailableError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "SEARCH_UNAVAILABLE"}
        )
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"code": "INTERNAL_ERROR"})


# Account Admin 成员只读（11 §74.2：session.read.account）


@router.get(
    "/admin/users/{user_id}/sessions",
    dependencies=[Depends(require_permission("session.read.account"))],
)
async def admin_list_member_sessions(
    request: Request,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await service.list_member_sessions(
            session,
            principal=principal,
            account_id=principal.actor_account_id,
            subject_user_id=user_id,
        )
    except EntityNotFoundError as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": rows, "next_cursor": None}}


@router.get(
    "/admin/users/{user_id}/sessions/{session_id}",
    dependencies=[Depends(require_permission("session.read.account"))],
)
async def admin_get_member_session(
    request: Request,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        detail = await service.get_member_detail(
            session,
            principal=principal,
            account_id=principal.actor_account_id,
            subject_user_id=user_id,
            session_id=session_id,
        )
    except (EntityNotFoundError, SessionNotFoundError) as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": detail}


@router.get(
    "/admin/users/{user_id}/sessions/{session_id}/messages",
    dependencies=[Depends(require_permission("session.read.account"))],
)
async def admin_get_member_messages(
    request: Request,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        messages = await service.get_member_messages(
            session,
            principal=principal,
            account_id=principal.actor_account_id,
            subject_user_id=user_id,
            session_id=session_id,
        )
    except (EntityNotFoundError, SessionNotFoundError) as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": messages, "next_cursor": None}}


@router.get(
    "/admin/users/{user_id}/sessions/{session_id}/memory-impact",
    dependencies=[Depends(require_permission("session.read.account"))],
)
async def admin_get_member_memory_impact(
    request: Request,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        impact = await service.get_member_memory_impact(
            session,
            principal=principal,
            account_id=principal.actor_account_id,
            subject_user_id=user_id,
            session_id=session_id,
        )
    except (EntityNotFoundError, SessionNotFoundError) as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": impact}


# Platform Super Admin 成员只读（11 §74.2：session.read.platform，先固定 Account）


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/sessions",
    dependencies=[Depends(require_permission("session.read.platform"))],
)
async def platform_list_member_sessions(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await service.list_member_sessions(
            session,
            principal=principal,
            account_id=account_id,
            subject_user_id=user_id,
        )
    except EntityNotFoundError as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": rows, "next_cursor": None}}


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/sessions/{session_id}",
    dependencies=[Depends(require_permission("session.read.platform"))],
)
async def platform_get_member_session(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        detail = await service.get_member_detail(
            session,
            principal=principal,
            account_id=account_id,
            subject_user_id=user_id,
            session_id=session_id,
        )
    except (EntityNotFoundError, SessionNotFoundError) as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": detail}


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/sessions/{session_id}/messages",
    dependencies=[Depends(require_permission("session.read.platform"))],
)
async def platform_get_member_messages(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        messages = await service.get_member_messages(
            session,
            principal=principal,
            account_id=account_id,
            subject_user_id=user_id,
            session_id=session_id,
        )
    except (EntityNotFoundError, SessionNotFoundError) as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": messages, "next_cursor": None}}


@router.get(
    "/platform/accounts/{account_id}/users/{user_id}/sessions/{session_id}/memory-impact",
    dependencies=[Depends(require_permission("session.read.platform"))],
)
async def platform_get_member_memory_impact(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    principal=Depends(get_current_principal),
    service=Depends(_sessions),
    session: AsyncSession = Depends(get_session),
):
    try:
        impact = await service.get_member_memory_impact(
            session,
            principal=principal,
            account_id=account_id,
            subject_user_id=user_id,
            session_id=session_id,
        )
    except (EntityNotFoundError, SessionNotFoundError) as exc:
        raise _member_error(exc)
    await session.commit()
    return {"status": "ok", "result": impact}


# 成员只读检索（11 §74.1：admin → memory.read.account；platform → memory.read.platform）


@router.post(
    "/admin/users/{user_id}/search/find",
    dependencies=[Depends(require_permission("memory.read.account"))],
)
async def admin_member_search_find(
    request: Request,
    user_id: uuid.UUID,
    body: SearchRequest,
    principal=Depends(get_current_principal),
    service=Depends(_search),
    session: AsyncSession = Depends(get_session),
):
    try:
        items = await service.member_find(
            session,
            principal=principal,
            scope="admin",
            account_id=principal.actor_account_id,
            subject_user_id=user_id,
            params=SearchParams(
                query=body.query,
                context_type=body.context_type,
                tags=body.tags,
                since=body.since,
                until=body.until,
            ),
            request_id=request.headers.get("x-request-id"),
        )
    except (EntityNotFoundError, InvalidSearchFilterError, SearchUnavailableError) as exc:
        raise _member_search_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": items}}


@router.post(
    "/platform/accounts/{account_id}/users/{user_id}/search/find",
    dependencies=[Depends(require_permission("memory.read.platform"))],
)
async def platform_member_search_find(
    request: Request,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    body: SearchRequest,
    principal=Depends(get_current_principal),
    service=Depends(_search),
    session: AsyncSession = Depends(get_session),
):
    try:
        items = await service.member_find(
            session,
            principal=principal,
            scope="platform",
            account_id=account_id,
            subject_user_id=user_id,
            params=SearchParams(
                query=body.query,
                context_type=body.context_type,
                tags=body.tags,
                since=body.since,
                until=body.until,
            ),
            request_id=request.headers.get("x-request-id"),
        )
    except (EntityNotFoundError, InvalidSearchFilterError, SearchUnavailableError) as exc:
        raise _member_search_error(exc)
    await session.commit()
    return {"status": "ok", "result": {"items": items}}
