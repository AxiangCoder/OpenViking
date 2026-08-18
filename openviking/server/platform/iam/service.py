"""RBAC 服务层（05 §11 `iam/service.py`）。

承载业务规则（P1-E2，04 §10.4–10.6 / 03 §9.2–9.4）：
- 幂等种子：完整 Permission 目录（03 §9.1）+ 三内置角色全局单行（04 §10.4）；
- 全局 `permission_schema_version` 维护（04 §10.5，参与全部权限缓存键）；
- 有效权限计算：`union(active role permissions) − disabled`（03 §9.4）；
- 双版本缓存（用户级 `permission_version` + 全局 `permission_schema_version`，
  进程内短 TTL，06 §16.4 单实例许可）；
- 角色授予约束：Account 一致性、单角色、PSA 仅平台初始化路径（04 §10.6）；
- 权限拒绝与种子变更审计直写 E1 repository（04 §10.8，不依赖 P1-E5 审计服务）。

数据访问一律经 IamRepository（P1-E1 冻结模块，只调用不重构）；种子涉及的
`iam_role_permissions` 差量同步与 `iam_user_roles` 替换属服务层职责，直接使用
SQLAlchemy（repository 无对应只读/删除接口，避免为服务层需求改动冻结模块）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from openviking.server.platform.errors import EntityNotFoundError, RoleAssignmentError
from openviking.server.platform.iam.cache import CacheKey, PermissionCache
from openviking.server.platform.iam.permissions import (
    BUILTIN_ROLE_PERMISSIONS,
    BUILTIN_ROLES,
    PERMISSION_SPECS,
    PLATFORM_SUPER_ADMIN,
    RoleView,
    UserPermissions,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import (
    IamPermission,
    IamPermissionSchema,
    IamRole,
    IamRolePermission,
    IamUserRole,
)

_SCHEMA_ROW_ID = 1

# 种子变更审计的固定 system 组件名（04 §10.8：actor_type=system 时必填）
SEED_SYSTEM_COMPONENT = "iam_seed"
RBAC_SYSTEM_COMPONENT = "iam_rbac"


@dataclass(frozen=True)
class SeedResult:
    """seed_catalog 结果。changed=False 表示幂等重跑（无版本递增、无审计）。"""

    permissions_count: int
    roles_count: int
    schema_version: int
    changed: bool


def _catalog_fingerprint() -> str:
    """目录内容指纹：权限 code/description/risk_level + 内置角色权限集合。"""
    payload = {
        "permissions": sorted(PERMISSION_SPECS),
        "role_permissions": {
            code: sorted(perms) for code, perms in BUILTIN_ROLE_PERMISSIONS.items()
        },
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class RbacService:
    """RBAC 内核：种子、有效权限计算、角色授予约束与相关审计。"""

    def __init__(
        self,
        repo: IamRepository,
        cache: PermissionCache | None = None,
    ) -> None:
        self._repo = repo
        self._cache = cache if cache is not None else PermissionCache()

    # ── 种子与全局 schema 版本 ──

    async def seed_catalog(
        self,
        session: AsyncSession,
        *,
        request_id: str | None = None,
    ) -> SeedResult:
        """幂等种子：Permission 目录（03 §9.1 全部 code）+ 三内置角色（04 §10.4）。

        目录内容变更（指纹变化）时递增 `permission_schema_version` 并写一条
        种子变更审计（actor_type=system）；内容未变（幂等重跑）不递增、不审计。
        """
        # 1. Permission 目录 upsert（code 主键，04 §10.5）
        for code, description, risk_level in PERMISSION_SPECS:
            domain, action = code.split(".", 1)
            stmt = (
                pg_insert(IamPermission)
                .values(
                    code=code,
                    domain=domain,
                    action=action,
                    description=description,
                    risk_level=risk_level,
                )
                .on_conflict_do_nothing(index_elements=["code"])
            )
            await session.execute(stmt)

        # 2. 三内置角色 upsert（全局单行，code 唯一；rank/ov_base_role 以代码为准）
        for spec in BUILTIN_ROLES.values():
            stmt = (
                pg_insert(IamRole)
                .values(
                    code=spec.code,
                    name=spec.name,
                    description=spec.description,
                    ov_base_role=spec.ov_base_role,
                    rank=spec.rank,
                    is_system=True,
                    status="active",
                )
                .on_conflict_do_update(
                    index_elements=["code"],
                    set_={
                        "name": spec.name,
                        "description": spec.description,
                        "ov_base_role": spec.ov_base_role,
                        "rank": spec.rank,
                        "is_system": True,
                        "status": "active",
                    },
                )
            )
            await session.execute(stmt)

        # 3. iam_role_permissions 差量同步（多余链接删除、缺失链接补齐）
        for code, expected in BUILTIN_ROLE_PERMISSIONS.items():
            role = (
                await session.execute(select(IamRole).where(IamRole.code == code))
            ).scalar_one()
            existing = set(
                (
                    await session.execute(
                        select(IamRolePermission.permission_code).where(
                            IamRolePermission.role_id == role.id
                        )
                    )
                ).scalars()
            )
            for perm_code in sorted(expected - existing):
                await session.execute(
                    pg_insert(IamRolePermission)
                    .values(role_id=role.id, permission_code=perm_code)
                    .on_conflict_do_nothing(index_elements=["role_id", "permission_code"])
                )
            if existing - expected:
                await session.execute(
                    delete(IamRolePermission).where(
                        IamRolePermission.role_id == role.id,
                        IamRolePermission.permission_code.in_(sorted(existing - expected)),
                    )
                )

        # 4. 全局 schema 版本（04 §10.5）：指纹变化才递增。
        #    列查询 + Core update，避免会话 identity map 缓存旧行导致漏检测
        #    （种子可能在同一事务中与外部 SQL 变更混用）。
        fingerprint = _catalog_fingerprint()
        row = (
            await session.execute(
                select(IamPermissionSchema.catalog_fingerprint).where(
                    IamPermissionSchema.id == _SCHEMA_ROW_ID
                )
            )
        ).first()
        schema_version = 1
        changed = False
        if row is None:
            session.add(
                IamPermissionSchema(
                    id=_SCHEMA_ROW_ID,
                    schema_version=1,
                    catalog_fingerprint=fingerprint,
                )
            )
        elif row[0] != fingerprint:
            result = await session.execute(
                update(IamPermissionSchema)
                .where(IamPermissionSchema.id == _SCHEMA_ROW_ID)
                .values(
                    schema_version=IamPermissionSchema.schema_version + 1,
                    catalog_fingerprint=fingerprint,
                    updated_at=func.now(),
                )
                .returning(IamPermissionSchema.schema_version)
            )
            schema_version = result.scalar_one()
            changed = True
            self._cache.invalidate_global()

        if changed:
            await self._repo.append_audit_event(
                session,
                request_id=request_id,
                actor_type="system",
                actor_system_component=SEED_SYSTEM_COMPONENT,
                authentication_method="system",
                action="permission_catalog.seed",
                target_type="iam_permission_schema",
                target_id=str(_SCHEMA_ROW_ID),
                scope="platform",
                result="success",
                reason=None,
                metadata={
                    "schema_version": schema_version,
                    "catalog_fingerprint": fingerprint,
                },
            )

        return SeedResult(
            permissions_count=len(PERMISSION_SPECS),
            roles_count=len(BUILTIN_ROLES),
            schema_version=schema_version,
            changed=changed,
        )

    async def get_permission_schema_version(self, session: AsyncSession) -> int:
        """当前全局权限 Schema 版本；未种子（表空）时返回 0。"""
        row = (
            await session.execute(
                select(IamPermissionSchema.schema_version).where(
                    IamPermissionSchema.id == _SCHEMA_ROW_ID
                )
            )
        ).scalar_one_or_none()
        return row if row is not None else 0

    # ── 角色只读视图 ──

    async def get_role_views(self, session: AsyncSession) -> list[RoleView]:
        """全部角色视图（含权限集合；GET /admin/roles 数据基础，05 §12.6）。"""
        roles = await self._repo.list_roles(session)
        if not roles:
            return []
        perms_by_role: dict[uuid.UUID, set[str]] = {}
        for link in await self._repo.list_permissions_for_roles(
            session, [r.id for r in roles]
        ):
            perms_by_role.setdefault(link.role_id, set()).add(link.permission_code)
        return [
            RoleView(
                id=r.id,
                code=r.code,
                name=r.name,
                description=r.description,
                ov_base_role=r.ov_base_role,
                rank=r.rank,
                is_system=r.is_system,
                status=r.status,
                permissions=frozenset(perms_by_role.get(r.id, ())),
            )
            for r in roles
        ]

    # ── 有效权限计算（03 §9.4）──

    async def get_user_permissions(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> UserPermissions:
        """有效权限 = union(active role permissions) − disabled。

        - 用户禁用（status != active）→ 权限集合为空（03 §9.4）；
        - 仅 status=active 的内置角色参与并集；
        - 结果按 `(account_id, user_id, permission_version, permission_schema_version)`
          短 TTL 缓存；两个版本任一变化即重新计算。
        """
        user = await self._repo.get_user(session, user_id)
        if user is None:
            raise EntityNotFoundError(f"user {user_id} not found")

        schema_version = await self.get_permission_schema_version(session)
        key: CacheKey = (
            str(user.account_id),
            user.id,
            user.permission_version,
            schema_version,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if user.status != "active":
            result = UserPermissions(
                role_codes=(), permissions=frozenset(), ov_base_role=None, rank=0
            )
            self._cache.put(key, result)
            return result

        roles = {r.id: r for r in await self._repo.list_roles(session)}
        link = await self._repo.get_role_for_user(session, user.id)
        active_role_ids: list[uuid.UUID] = []
        role_codes: list[str] = []
        ov_base_role: str | None = None
        rank = 0
        if link is not None:
            role = roles.get(link.role_id)
            if role is not None and role.status == "active":
                active_role_ids.append(role.id)
                role_codes.append(role.code)
                if role.ov_base_role:
                    ov_base_role = role.ov_base_role
                rank = max(rank, role.rank)

        permission_codes = frozenset(
            rp.permission_code
            for rp in await self._repo.list_permissions_for_roles(session, active_role_ids)
        )
        result = UserPermissions(
            role_codes=tuple(role_codes),
            permissions=permission_codes,
            ov_base_role=ov_base_role,
            rank=rank,
        )
        self._cache.put(key, result)
        return result

    # ── 角色授予（04 §10.6）──

    async def assign_role(
        self,
        session: AsyncSession,
        *,
        actor_user_id: uuid.UUID,
        actor_account_id: uuid.UUID | None,
        target_user_id: uuid.UUID,
        role_code: str,
        request_id: str | None = None,
    ) -> IamUserRole:
        """授予/提升内置角色（account_admin 或 user）。

        约束（04 §10.6）：
        - Account 一致性：目标用户必须属于某 Account；Account 作用域的授予者
          （actor_account_id 非空）必须与目标用户同 Account，跨 Account 拒绝；
        - 单角色：v0.1 每用户至多一个内置角色，已持有角色时执行替换（提升路径，
          P1-E5 `PUT .../role` 仅 user→account_admin 的基础）；
        - `platform_super_admin` 禁止经本方法授予（仅平台初始化路径，
          见 assign_platform_super_admin）。

        每次拒绝都会写一条审计（Actor/Subject 分离、result=denied，04 §10.8），
        然后抛 RoleAssignmentError；成功路径递增目标用户 permission_version 并
        失效其权限缓存。
        """
        actor = await self._repo.get_user(session, actor_user_id)
        target = await self._repo.get_user(session, target_user_id)
        role = await self._repo.get_role_by_code(session, role_code)
        if (
            actor is None
            or actor.deleted_at is not None
            or target is None
            or target.deleted_at is not None
            or role is None
        ):
            # 不存在/已删除统一按 not-found 语义，不写审计（避免枚举泄露）
            raise EntityNotFoundError(
                f"assign role {role_code} to user {target_user_id}: actor or target not found"
            )

        if role.code == PLATFORM_SUPER_ADMIN:
            await self._audit_denied(
                session,
                request_id=request_id,
                actor_user_id=actor_user_id,
                actor_account_id=actor_account_id,
                subject_user_id=target_user_id,
                subject_account_id=target.account_id,
                action="role.assign",
                target_id=role_code,
                scope="platform" if actor_account_id is None else "account",
                reason="PLATFORM_ROLE_BOOTSTRAP_ONLY",
            )
            raise RoleAssignmentError("PLATFORM_ROLE_BOOTSTRAP_ONLY")

        if target.account_id is None:
            await self._audit_denied(
                session,
                request_id=request_id,
                actor_user_id=actor_user_id,
                actor_account_id=actor_account_id,
                subject_user_id=target_user_id,
                subject_account_id=None,
                action="role.assign",
                target_id=role_code,
                scope="account",
                reason="ROLE_REQUIRES_ACCOUNT",
            )
            raise RoleAssignmentError("ROLE_REQUIRES_ACCOUNT")

        if actor_account_id is not None and actor_account_id != target.account_id:
            await self._audit_denied(
                session,
                request_id=request_id,
                actor_user_id=actor_user_id,
                actor_account_id=actor_account_id,
                subject_user_id=target_user_id,
                subject_account_id=target.account_id,
                action="role.assign",
                target_id=role_code,
                scope="account",
                reason="CROSS_ACCOUNT_ROLE_ASSIGNMENT",
            )
            raise RoleAssignmentError("CROSS_ACCOUNT_ROLE_ASSIGNMENT")

        return await self._replace_or_create_role(
            session,
            target_user_id=target_user_id,
            role_id=role.id,
            assigned_by=actor_user_id,
        )

    async def assign_platform_super_admin(
        self,
        session: AsyncSession,
        *,
        target_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> IamUserRole:
        """平台初始化路径专用：授予 `platform_super_admin`（04 §10.6）。

        仅允许 P1-E3 bootstrap 等受控初始化路径调用，不得从任何产品流程调用；
        目标用户必须 `account_id IS NULL`（与 DB 部分唯一索引配合，
        保证至多一个无 Account 用户且其角色为 PSA）。
        """
        target = await self._repo.get_user(session, target_user_id)
        role = await self._repo.get_role_by_code(session, PLATFORM_SUPER_ADMIN)
        if (
            target is None
            or target.deleted_at is not None
            or role is None
        ):
            raise EntityNotFoundError(
                f"assign {PLATFORM_SUPER_ADMIN} to user {target_user_id}: target or role not found"
            )

        if target.account_id is not None:
            await self._audit_denied(
                session,
                request_id=request_id,
                actor_user_id=None,
                actor_account_id=None,
                subject_user_id=target_user_id,
                subject_account_id=target.account_id,
                action="role.assign",
                target_id=PLATFORM_SUPER_ADMIN,
                scope="platform",
                reason="PSA_REQUIRES_NULL_ACCOUNT",
            )
            raise RoleAssignmentError("PSA_REQUIRES_NULL_ACCOUNT")

        return await self._replace_or_create_role(
            session,
            target_user_id=target_user_id,
            role_id=role.id,
            assigned_by=None,
        )

    async def _replace_or_create_role(
        self,
        session: AsyncSession,
        *,
        target_user_id: uuid.UUID,
        role_id: uuid.UUID,
        assigned_by: uuid.UUID | None,
    ) -> IamUserRole:
        existing = await self._repo.get_role_for_user(session, target_user_id)
        if existing is not None and existing.role_id == role_id:
            return existing  # 幂等：同一角色已授予
        if existing is not None:
            # 单角色替换（提升路径）：删除旧绑定再写入新绑定
            await session.execute(
                delete(IamUserRole).where(IamUserRole.user_id == target_user_id)
            )
            await session.flush()
        link = await self._repo.assign_role(
            session, user_id=target_user_id, role_id=role_id, assigned_by=assigned_by
        )
        await self._repo.bump_permission_version(session, target_user_id)
        self._cache.invalidate_user(target_user_id)
        return link

    # ── 权限拒绝与种子变更审计（04 §10.8，直写 E1 repository）──

    async def check_permission(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        permission_code: str,
        request_id: str | None = None,
    ) -> bool:
        """校验用户有效权限；未授予时写一条 denied 审计并返回 False。

        供 P1-E3+ 鉴权链路复用；HTTP 层可进一步细化 actor/subject 上下文。
        """
        user = await self._repo.get_user(session, user_id)
        if user is None:
            raise EntityNotFoundError(f"user {user_id} not found")
        perms = await self.get_user_permissions(session, user_id)
        if permission_code in perms.permissions:
            return True
        await self._audit_denied(
            session,
            request_id=request_id,
            actor_user_id=user_id,
            actor_account_id=user.account_id,
            subject_user_id=user_id,
            subject_account_id=user.account_id,
            action="permission.check",
            target_id=permission_code,
            scope="self",
            reason="PERMISSION_NOT_GRANTED",
        )
        return False

    async def _audit_denied(
        self,
        session: AsyncSession,
        *,
        request_id: str | None,
        actor_user_id: uuid.UUID | None,
        actor_account_id: uuid.UUID | None,
        subject_user_id: uuid.UUID | None,
        subject_account_id: uuid.UUID | None,
        action: str,
        target_id: str,
        scope: str,
        reason: str,
    ) -> None:
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=subject_account_id,
            actor_type="user" if actor_user_id is not None else "system",
            actor_user_id=actor_user_id,
            actor_account_id=actor_account_id,
            actor_system_component=(
                None if actor_user_id is not None else RBAC_SYSTEM_COMPONENT
            ),
            subject_user_id=subject_user_id,
            subject_account_id=subject_account_id,
            action=action,
            target_type="iam_user_roles" if action == "role.assign" else "iam_permissions",
            target_id=target_id,
            scope=scope,
            result="denied",
            reason=reason,
        )
