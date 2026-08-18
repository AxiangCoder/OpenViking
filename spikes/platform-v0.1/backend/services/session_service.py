from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from models import IamSession, IamUser
from security import (
    constant_time_eq,
    new_csrf_secret,
    new_session_token,
    sha256_hex,
    verify_password,
)


class SessionService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_login_session(
        self, user: IamUser, ip_hash: str | None = None, user_agent: str | None = None
    ) -> tuple[str, str, uuid.UUID]:
        """Create a login session; returns (raw_token, csrf_token, session_id)."""
        raw_token = new_session_token()
        csrf_secret = new_csrf_secret()
        now = datetime.now(timezone.utc)
        idle_expires = now + timedelta(seconds=config.session_idle_ttl_seconds)
        absolute_expires = now + timedelta(seconds=config.session_absolute_ttl_seconds)
        row = IamSession(
            token_hash=sha256_hex(raw_token),
            account_id=user.account_id,
            user_id=user.id,
            csrf_secret_hash=sha256_hex(csrf_secret),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=idle_expires,
            absolute_expires_at=absolute_expires,
            ip_hash=ip_hash,
            user_agent=(user_agent or "")[:512] or None,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.execute(
            update(IamUser).where(IamUser.id == user.id).values(last_login_at=now)
        )
        return raw_token, csrf_secret, row.id

    async def rotate_session(
        self, user: IamUser, old_session_id: uuid.UUID | None, ip_hash: str | None = None
    ) -> tuple[str, str, uuid.UUID]:
        """Password change rotates the current login session (03 §8.1/§8.3); role elevation does NOT rotate (only used by password/change, not by promote_to_account_admin)."""
        if old_session_id is not None:
            await self.session.execute(
                update(IamSession)
                .where(IamSession.id == old_session_id)
                .values(revoked_at=datetime.now(timezone.utc), revoked_reason="rotated")
            )
        return await self.create_login_session(user, ip_hash)

    async def revoke_session(self, session_id: uuid.UUID, reason: str = "logout") -> None:
        await self.session.execute(
            update(IamSession)
            .where(IamSession.id == session_id)
            .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
        )

    async def revoke_all_user_sessions(self, user_id: uuid.UUID, reason: str) -> int:
        """Revoke every unrevoked login session for a user (password reset / disable)."""
        result = await self.session.execute(
            update(IamSession)
            .where(IamSession.user_id == user_id, IamSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
        )
        return result.rowcount or 0

    async def touch_session(self, session_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(IamSession)
            .where(IamSession.id == session_id)
            .values(
                last_seen_at=now,
                idle_expires_at=now + timedelta(seconds=config.session_idle_ttl_seconds),
            )
        )

    @staticmethod
    def verify_csrf(csrf_secret_hash: str, provided: str) -> bool:
        return constant_time_eq(csrf_secret_hash, sha256_hex(provided))


async def authenticate_user(session: AsyncSession, email: str, password: str) -> IamUser | None:
    """Unified login error: always LOGIN_FAILED for bad email or password."""
    normalized = email.strip().lower()
    user = (
        await session.execute(select(IamUser).where(IamUser.email == normalized))
    ).scalar_one_or_none()
    if user is None:
        return None
    if user.status not in ("active",):
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user
