"""Principal resolution: session cookie and user API key -> AuthenticatedUserPrincipal.

Mirrors design 02 §7.1 / 05 §11.4: all credential kinds resolve to the same user
principal; roles/permissions are computed live from IAM, never stored in the
credential.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cache import PermissionCache
from models import IamAccount, IamApiCredential, IamSession, IamUser
from permissions import GLOBAL_PERMISSION_SCHEMA_VERSION, UserPermissions, get_user_permissions
from security import constant_time_eq, sha256_hex

SESSION_EXPIRED = "SESSION_EXPIRED"
USER_DISABLED = "USER_DISABLED"
INVALID_CREDENTIAL = "INVALID_CREDENTIAL"


@dataclass(frozen=True)
class AuthenticatedUserPrincipal:
    actor_user_id: uuid.UUID
    actor_account_id: uuid.UUID | None
    actor_ov_user_id: str | None
    actor_ov_account_id: str | None
    user_status: str
    authentication_method: str  # session | api_key
    session_id: uuid.UUID | None = None
    credential_id: uuid.UUID | None = None
    role_codes: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset()
    ov_base_role: str | None = None
    role_rank: int = 0


@dataclass(frozen=True)
class DataAccessContext:
    """Actor + Subject for administrator cross-user access (design 02 §7.2)."""

    actor_user_id: uuid.UUID
    actor_account_id: uuid.UUID | None
    subject_account_id: uuid.UUID
    subject_user_id: uuid.UUID | None
    visibility: str  # user_private | account_shared
    canonical_ov_uri: str
    action: str


async def _load_user_permissions(session: AsyncSession, user: IamUser) -> UserPermissions:
    return await get_user_permissions(session, user, use_cache=True)


async def resolve_session_principal(
    session: AsyncSession, raw_token: str
) -> AuthenticatedUserPrincipal:
    if not raw_token:
        raise _Unauthenticated(INVALID_CREDENTIAL)
    token_hash = sha256_hex(raw_token)
    row = (
        await session.execute(select(IamSession).where(IamSession.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        raise _Unauthenticated(SESSION_EXPIRED)
    user = (
        await session.execute(select(IamUser).where(IamUser.id == row.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise _Unauthenticated(USER_DISABLED)
    account = None
    if user.account_id is not None:
        account = (
            await session.execute(select(IamAccount).where(IamAccount.id == user.account_id))
        ).scalar_one_or_none()
    if user.status != "active":
        raise _Unauthenticated(USER_DISABLED)
    perms = await _load_user_permissions(session, user)
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=account.ov_account_id if account else None,
        user_status=user.status,
        authentication_method="session",
        session_id=row.id,
        role_codes=perms.role_codes,
        permissions=perms.permissions,
        ov_base_role=perms.ov_base_role,
        role_rank=perms.rank,
    )


async def resolve_api_key_principal(
    session: AsyncSession, api_key: str
) -> AuthenticatedUserPrincipal:
    parts = api_key.split(".")
    if len(parts) != 3 or parts[0] != "ovk_u":
        raise _Unauthenticated(INVALID_CREDENTIAL)
    public_id, secret = parts[1], parts[2]
    cred = (
        await session.execute(
            select(IamApiCredential).where(IamApiCredential.public_id == public_id)
        )
    ).scalar_one_or_none()
    if cred is None or cred.status != "active":
        raise _Unauthenticated(INVALID_CREDENTIAL)
    if not constant_time_eq(cred.key_hash, sha256_hex(secret)):
        raise _Unauthenticated(INVALID_CREDENTIAL)
    user = (
        await session.execute(select(IamUser).where(IamUser.id == cred.user_id))
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        raise _Unauthenticated(USER_DISABLED)
    account = (
        await session.execute(select(IamAccount).where(IamAccount.id == cred.account_id))
    ).scalar_one_or_none()
    perms = await _load_user_permissions(session, user)
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=cred.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=account.ov_account_id if account else None,
        user_status=user.status,
        authentication_method="api_key",
        credential_id=cred.id,
        role_codes=perms.role_codes,
        permissions=perms.permissions,
        ov_base_role=perms.ov_base_role,
        role_rank=perms.rank,
    )


class _Unauthenticated(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def resolve_principal(
    session: AsyncSession, session_token: str | None, bearer_key: str | None
) -> AuthenticatedUserPrincipal:
    """Common resolver used by REST dependencies (and, in production, MCP)."""
    if session_token:
        return await resolve_session_principal(session, session_token)
    if bearer_key:
        return await resolve_api_key_principal(session, bearer_key)
    raise _Unauthenticated(INVALID_CREDENTIAL)
