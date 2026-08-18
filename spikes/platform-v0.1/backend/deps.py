from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from db import get_session
from models import IamSession, IamUser
from principals import (
    AuthenticatedUserPrincipal,
    _Unauthenticated,
    resolve_api_key_principal,
    resolve_session_principal,
)
from services.session_service import SessionService


class AuthError(Exception):
    def __init__(self, code: str):
        self.code = code


def _unauthorized(code: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": code})


def _forbidden(code: str = "PERMISSION_NOT_GRANTED") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": code})


def hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()


async def get_current_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> AuthenticatedUserPrincipal:
    cookie_token = request.cookies.get(config.cookie_name)
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    try:
        if cookie_token:
            principal = await resolve_session_principal(session, cookie_token)
            await SessionService(session).touch_session(principal.session_id)
            await session.commit()
            return principal
        if bearer:
            return await resolve_api_key_principal(session, bearer)
        raise _Unauthenticated("INVALID_CREDENTIAL")
    except _Unauthenticated as exc:
        raise _unauthorized(exc.code) from exc


def require_permission(permission_code: str):
    def checker(principal: AuthenticatedUserPrincipal = Depends(get_current_principal)):
        if permission_code not in principal.permissions:
            raise _forbidden()
        return principal

    return checker


async def verify_csrf(
    request: Request,
    principal: AuthenticatedUserPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    provided = request.headers.get("x-csrf-token", "")
    row = (
        await session.execute(select(IamSession).where(IamSession.id == principal.session_id))
    ).scalar_one_or_none() if principal.session_id else None
    if row is None or not SessionService.verify_csrf(row.csrf_secret_hash, provided):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "CSRF_INVALID"})


def apply_session_cookie(response, raw_token: str) -> None:
    """Cookie: __Host-ov_session; HttpOnly; Secure (configurable for local spike);
    SameSite=Lax; Path=/ (design 03 §8.1)."""
    response.set_cookie(
        key=config.cookie_name,
        value=raw_token,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
        max_age=config.session_absolute_ttl_seconds,
    )
