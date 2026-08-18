from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import IamAccount, IamRole, IamUser, IamUserRole
from permissions import (
    ACCOUNT_ADMIN,
    PLATFORM_SUPER_ADMIN,
    USER,
    bump_user_permission_version,
)
from security import hash_password, random_initial_password


@dataclass
class CreatedUser:
    user: IamUser
    initial_password: str


async def _get_role_by_code(session: AsyncSession, code: str) -> IamRole:
    role = (
        await session.execute(select(IamRole).where(IamRole.code == code))
    ).scalar_one_or_none()
    if role is None:
        raise RuntimeError(f"built-in role {code} not seeded")
    return role


async def create_platform_super_admin(
    session: AsyncSession, email: str, username: str, display_name: str | None = None
) -> CreatedUser:
    """First Platform Super Admin is created by a one-off deploy command (06 §15.2)."""
    password = random_initial_password()
    user = IamUser(
        account_id=None,
        ov_user_id=None,
        username=username,
        email=email.strip().lower(),
        display_name=display_name,
        password_hash=hash_password(password),
        password_changed_at=None,
        status="active",
    )
    session.add(user)
    await session.flush()
    role = await _get_role_by_code(session, PLATFORM_SUPER_ADMIN)
    session.add(IamUserRole(user_id=user.id, role_id=role.id))
    await session.commit()
    return CreatedUser(user=user, initial_password=password)


async def create_account_with_first_admin(
    session: AsyncSession,
    ov_account_id: str,
    account_code: str,
    account_name: str,
    admin_email: str,
    admin_username: str,
    admin_display_name: str | None = None,
) -> tuple[IamAccount, CreatedUser]:
    """Platform Super Admin creates an Account plus its first Account Admin (03 §8.3)."""
    account = IamAccount(ov_account_id=ov_account_id, code=account_code, display_name=account_name)
    session.add(account)
    await session.flush()

    password = random_initial_password()
    admin = IamUser(
        account_id=account.id,
        ov_user_id=f"ov_user_{uuid.uuid4().hex[:10]}",
        username=admin_username,
        email=admin_email.strip().lower(),
        display_name=admin_display_name,
        password_hash=hash_password(password),
        status="active",
    )
    session.add(admin)
    await session.flush()
    role = await _get_role_by_code(session, ACCOUNT_ADMIN)
    session.add(IamUserRole(user_id=admin.id, role_id=role.id))
    await session.commit()
    return account, CreatedUser(user=admin, initial_password=password)


async def create_account_user(
    session: AsyncSession,
    account_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    email: str,
    username: str,
    display_name: str | None = None,
) -> CreatedUser:
    """Account Admin creates a plain User; role is always `user` (05 §12.6)."""
    existing = (
        await session.execute(
            select(IamUser).where(IamUser.email == email.strip().lower())
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError("EMAIL_ALREADY_EXISTS")
    password = random_initial_password()
    user = IamUser(
        account_id=account_id,
        ov_user_id=f"ov_user_{uuid.uuid4().hex[:10]}",
        username=username,
        email=email.strip().lower(),
        display_name=display_name,
        password_hash=hash_password(password),
        status="active",
    )
    session.add(user)
    await session.flush()
    role = await _get_role_by_code(session, USER)
    session.add(IamUserRole(user_id=user.id, role_id=role.id, assigned_by=actor_user_id))
    await session.commit()
    return CreatedUser(user=user, initial_password=password)


async def reset_user_password(
    session: AsyncSession,
    target_user: IamUser,
    reset_by: uuid.UUID,
) -> str:
    """Generate a new password; old password immediately invalid. Caller must have
    already checked actor_role_rank > target_role_rank."""
    password = random_initial_password()
    target_user.password_hash = hash_password(password)
    await session.flush()
    await bump_user_permission_version(session, target_user.id)
    from services.session_service import SessionService

    await SessionService(session).revoke_all_user_sessions(target_user.id, "password_reset")
    await session.commit()
    return password


async def promote_to_account_admin(session: AsyncSession, user: IamUser) -> None:
    role = await _get_role_by_code(session, ACCOUNT_ADMIN)
    existing = (
        await session.execute(
            select(IamUserRole).where(
                IamUserRole.user_id == user.id, IamUserRole.role_id == role.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(IamUserRole(user_id=user.id, role_id=role.id))
    await bump_user_permission_version(session, user.id)
    await session.commit()
