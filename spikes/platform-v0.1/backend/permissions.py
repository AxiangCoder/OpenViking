"""Permission catalog, built-in roles and effective-permission computation.

Spike decision: role hierarchy ranks are stored on iam_roles.rank (3/2/1) for the
strict password-reset check (actor_role_rank > target_role_rank). The design doc
(04 §10.4) does not list a rank column; this is a recorded spike decision.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import IamPermission, IamRole, IamRolePermission, IamUser, IamUserRole

PLATFORM_SUPER_ADMIN = "platform_super_admin"
ACCOUNT_ADMIN = "account_admin"
USER = "user"

ROLE_RANKS: dict[str, int] = {
    PLATFORM_SUPER_ADMIN: 3,
    ACCOUNT_ADMIN: 2,
    USER: 1,
}

# Global permission schema version: bumped when built-in role seeds or migrations change.
# Participates in every permission cache key (design 03 §9.4).
GLOBAL_PERMISSION_SCHEMA_VERSION = 1

# Permission catalog — a representative subset of design 03 §9.1 for the spike.
PERMISSION_CATALOG: dict[str, str] = {
    "account.read.platform": "view all accounts",
    "account.manage.platform": "create/manage accounts",
    "account.delete": "soft-delete accounts",
    "user.read": "view account users",
    "user.create": "create account users",
    "user.update": "update account users",
    "user.disable": "disable account users",
    "user.delete": "soft-delete account users",
    "user.read.platform": "view platform users",
    "user.password.reset.account": "reset lower-ranked account user passwords",
    "user.password.reset.platform": "reset lower-ranked platform user passwords",
    "role.read": "view built-in roles",
    "role.assign.platform": "promote users to account_admin",
    "credential.read.self": "view own api keys",
    "credential.create.self": "create own api keys",
    "credential.revoke.self": "revoke own api keys",
    "credential.read.account": "view account user api key metadata",
    "credential.revoke.account": "revoke account user api keys",
    "credential.read.platform": "view platform api key metadata",
    "credential.revoke.platform": "revoke platform api keys",
    "memory.read.self": "read own memories",
    "memory.read.account": "read account member memories",
    "memory.read.platform": "read platform memories",
    "session.read.self": "read own openviking sessions",
    "session.write.self": "write own openviking sessions",
    "session.delete.self": "soft-delete own sessions",
    "resource.user_private.read.self": "read own private resources",
    "resource.user_private.write.self": "write own private resources",
    "resource.user_private.delete.self": "delete own private resources",
    "resource.user_private.read.account": "read account member private resources",
    "resource.account_shared.read.account": "read account shared resources",
    "resource.account_shared.write.account": "write account shared resources",
    "resource.account_shared.delete.account": "delete account shared resources",
    "resource.account_shared.read.platform": "read target account shared resources",
    "resource.account_shared.write.platform": "write target account shared resources",
    "resource.account_shared.delete.platform": "delete target account shared resources",
    "skill.user_private.read.self": "read own private skills",
    "skill.user_private.manage.self": "manage own private skills",
    "skill.user_private.read.account": "read account member private skills",
    "skill.user_private.publish.account": "publish account member private skills",
    "skill.user_private.read.platform": "read platform private skills",
    "skill.account_shared.read.account": "read account shared skills",
    "skill.account_shared.use.account": "use account shared skills",
    "skill.account_shared.manage.account": "manage account shared skills",
    "skill.account_shared.read.platform": "read platform shared skills",
    "audit.read": "read account audit events",
    "task.read.self": "read own tasks",
    "task.cancel.self": "cancel own tasks",
    "task.read.account_shared": "read account shared tasks",
    "task.cancel.account_shared": "cancel account shared tasks",
    "integration.oauth.authorize.self": "authorize mcp oauth clients",
    "integration.oauth.read.self": "read own oauth grants",
    "integration.oauth.revoke.self": "revoke own oauth grants",
}

USER_PERMISSIONS = frozenset(
    {
        "credential.read.self",
        "credential.create.self",
        "credential.revoke.self",
        "memory.read.self",
        "session.read.self",
        "session.write.self",
        "session.delete.self",
        "resource.user_private.read.self",
        "resource.user_private.write.self",
        "resource.user_private.delete.self",
        "resource.account_shared.read.account",
        "skill.user_private.read.self",
        "skill.user_private.manage.self",
        "skill.account_shared.read.account",
        "skill.account_shared.use.account",
        "task.read.self",
        "task.cancel.self",
        "integration.oauth.authorize.self",
        "integration.oauth.read.self",
        "integration.oauth.revoke.self",
    }
)

ACCOUNT_ADMIN_PERMISSIONS = USER_PERMISSIONS | frozenset(
    {
        "user.read",
        "user.create",
        "user.update",
        "user.disable",
        "user.delete",
        "user.password.reset.account",
        "role.read",
        "credential.read.account",
        "credential.revoke.account",
        "memory.read.account",
        "resource.user_private.read.account",
        "resource.account_shared.write.account",
        "resource.account_shared.delete.account",
        "skill.user_private.read.account",
        "skill.user_private.publish.account",
        "skill.account_shared.manage.account",
        "audit.read",
        "task.read.account_shared",
        "task.cancel.account_shared",
    }
)

# Skill permissions the platform role must NOT inherit (design 03 §9.3 / 10 §52.10):
# Platform Super Admin is strictly read-only over all Skills.
SKILL_WRITE_OR_USE_PERMISSIONS = frozenset(
    {
        "skill.user_private.manage.self",
        "skill.user_private.publish.account",
        "skill.account_shared.use.account",
        "skill.account_shared.manage.account",
    }
)

PLATFORM_PERMISSIONS = (ACCOUNT_ADMIN_PERMISSIONS - SKILL_WRITE_OR_USE_PERMISSIONS) | frozenset(
    {
        "account.read.platform",
        "account.manage.platform",
        "account.delete",
        "user.read.platform",
        "user.password.reset.platform",
        "role.assign.platform",
        "credential.read.platform",
        "credential.revoke.platform",
        "memory.read.platform",
        "resource.account_shared.read.platform",
        "resource.account_shared.write.platform",
        "resource.account_shared.delete.platform",
        "skill.user_private.read.platform",
        "skill.account_shared.read.platform",
    }
)

BUILTIN_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    USER: USER_PERMISSIONS,
    ACCOUNT_ADMIN: ACCOUNT_ADMIN_PERMISSIONS,
    PLATFORM_SUPER_ADMIN: PLATFORM_PERMISSIONS,
}


@dataclass(frozen=True)
class RoleView:
    id: uuid.UUID
    code: str
    name: str
    ov_base_role: str | None
    rank: int
    permissions: frozenset[str]


@dataclass(frozen=True)
class UserPermissions:
    role_codes: tuple[str, ...]
    permissions: frozenset[str]
    ov_base_role: str | None
    rank: int


def validate_password_length(password: str, min_length: int) -> bool:
    return len(password) >= min_length


async def seed_catalog(session: AsyncSession) -> None:
    """Idempotently seed permission catalog + three built-in roles."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    for code, description in PERMISSION_CATALOG.items():
        domain, action = code.split(".", 1)
        stmt = pg_insert(IamPermission).values(
            code=code, domain=domain, action=action, description=description
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
        await session.execute(stmt)

    for code, perms in BUILTIN_ROLE_PERMISSIONS.items():
        stmt = pg_insert(IamRole).values(
            code=code,
            name=code,
            description=f"built-in role {code}",
            ov_base_role={"platform_super_admin": None, "account_admin": "admin", "user": "user"}[
                code
            ],
            rank=ROLE_RANKS[code],
            is_system=True,
            status="active",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["code"], set_={"rank": ROLE_RANKS[code], "status": "active"}
        )
        await session.execute(stmt)
        role = (
            await session.execute(select(IamRole).where(IamRole.code == code))
        ).scalar_one()
        await session.execute(IamRolePermission.__table__.delete().where(IamRolePermission.role_id == role.id))
        for perm in perms:
            await session.execute(
                pg_insert(IamRolePermission)
                .values(role_id=role.id, permission_code=perm)
                .on_conflict_do_nothing(constraint="uq_iam_role_permissions")
            )
    await session.commit()


async def get_user_permissions(
    session: AsyncSession, user: IamUser, use_cache: bool = True
) -> UserPermissions:
    """Compute effective permissions; validates the permission_version cache in the spike.

    The spike keeps the computation DB-backed and exercises the cache-key semantics
    (user permission_version + global schema version) via the calling layer.
    """
    from cache import PermissionCache

    key = (user.id, user.permission_version, GLOBAL_PERMISSION_SCHEMA_VERSION)
    cached = await PermissionCache.get(key) if use_cache else None
    if cached is not None:
        return cached

    rows = (
        await session.execute(
            select(IamRole, IamUserRole)
            .join(IamUserRole, IamUserRole.role_id == IamRole.id)
            .where(IamUserRole.user_id == user.id, IamRole.status == "active")
        )
    ).all()
    role_codes: list[str] = []
    perms: set[str] = set()
    ov_base_role: str | None = None
    rank = 0
    for role, _link in rows:
        role_codes.append(role.code)
        rank = max(rank, role.rank)
        if role.ov_base_role:
            ov_base_role = role.ov_base_role
        perm_rows = (
            await session.execute(
                select(IamRolePermission.permission_code).where(
                    IamRolePermission.role_id == role.id
                )
            )
        ).scalars().all()
        perms.update(perm_rows)

    result = UserPermissions(
        role_codes=tuple(role_codes),
        permissions=frozenset(perms),
        ov_base_role=ov_base_role,
        rank=rank,
    )
    await PermissionCache.put(key, result)
    return result


async def bump_user_permission_version(session: AsyncSession, user_id: uuid.UUID) -> None:
    from sqlalchemy import update

    await session.execute(
        update(IamUser).where(IamUser.id == user_id).values(permission_version=IamUser.permission_version + 1)
    )
    from cache import PermissionCache

    await PermissionCache.invalidate_user(user_id)
