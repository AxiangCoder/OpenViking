"""DeletionService（04 §10.11，05 §11.3/§12.6，14 号计划 §97.2）。

软删除统一语义（05 §11.3）：
- 先禁用登录/撤销会话，再写 `deleted_at` 与 `purge_after=deleted_at+30 天`，
  对象从正常查询隐藏；
- 进入回收期时创建 `iam_deletion_jobs`（AC⑨：DELETE 返回 deletion job ID）；
- 回收期内允许有权 Actor 恢复并写审计；期满后由 Purge Worker 幂等清理
  （审计不随物理清理，AC⑧）；
- 不把"删除登录用户"与"立即物理删除全部记忆"绑定成一个不可恢复请求。

回收站恢复权限（05 §12.6 注）：恢复是删除/管理类动作的逆操作，必须按对象
类型分别校验权限，不存在单一的「Account/平台范围恢复权限」放行所有类型。
v0.1 本 Epic 交付 User（Account Admin `user.delete`）与 Account（仅 PSA
`account.delete`）；Session/Resource/Skill 由 E3–E5 按同一映射表扩展。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.errors import (
    AdminActionForbiddenError,
    DeletionJobError,
    EntityNotFoundError,
    LastAccountAdminError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import IamDeletionJob, IamUser
from openviking.server.platform.registry.repository import (
    JOB_PENDING,
    JOB_RESTORED,
    RegistryRepository,
)

# 05 §12.6 注：类型化恢复权限映射（v0.1 交付 user/account，其余 E3–E5 扩展）
# resource_type → (scope, permission_code, 说明)
RESTORE_PERMISSION_MAP: dict[str, tuple[str, str, str]] = {
    "user": ("account", "user.delete", "Account Admin（本 Account）"),
    "account": ("platform", "account.delete", "仅 Platform Super Admin"),
    # E3–E5 扩展点（04 §10.11 同表承载，类型化权限按 05 §12.6 注逐条落地）：
    # "session":  ("self", "session.delete.self", "仅属主本人；管理员不能恢复他人 Session"),
    # "resource": ("account", "resource.user_private.delete.self", "属主本人（私有）..."),
    # "skill":    ("account", "skill.user_private.manage.self", "属主本人（私有）..."),
}


@dataclass(frozen=True)
class DeletionPreview:
    """deletion-preview 响应（05 §12.6：目标名称、影响分类与数量、可恢复、purge_after）。"""

    resource_type: str
    resource_id: str
    target_name: str
    impacted: dict[str, int]
    recoverable: bool
    purge_after: str | None


@dataclass(frozen=True)
class DeletionJobResult:
    """DELETE 成功进入回收期（AC⑨：返回 deletion job ID 与恢复截止时间）。"""

    resource_type: str
    resource_id: str
    deletion_job_id: uuid.UUID
    deleted_at: str
    restore_until: str


@dataclass(frozen=True)
class RecycleBinRow:
    """回收站行（恢复权限按对象类型实时计算，DTO 不含内部 URI）。"""

    job: IamDeletionJob
    target_name: str | None
    restore_allowed: bool
    restore_permission: str | None


class DeletionService:
    """软删除/回收站/恢复编排（Repository 事务由调用方控制）。"""

    def __init__(
        self,
        repo: IamRepository,
        store: RegistryRepository | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._repo = repo
        self._store = store or RegistryRepository()
        self._config = config

    # ── deletion-preview（05 §12.6）──

    async def preview_user(
        self,
        session: AsyncSession,
        *,
        scope_account_id: uuid.UUID,
        target_user_id: uuid.UUID,
    ) -> DeletionPreview:
        """User deletion-preview：影响范围 = 登录 Session + API Key + OAuth 关联
        （v0.1 已建模项），均随删除进入不可用/撤销。"""
        user = await self._load_user_in_scope(session, scope_account_id, target_user_id)
        counts = {
            "login_sessions": await self._count_sessions(session, target_user_id),
            "api_keys": await self._count_api_keys(session, target_user_id),
        }
        return DeletionPreview(
            resource_type="user",
            resource_id=str(user.id),
            target_name=user.display_name or user.username,
            impacted=counts,
            recoverable=True,
            purge_after=None,
        )

    async def preview_account(
        self,
        session: AsyncSession,
        *,
        target_account_id: uuid.UUID,
    ) -> DeletionPreview:
        """Account deletion-preview（05 §12.6 平台表 `account.delete`）。"""
        account = await self._repo.get_account(session, target_account_id)
        if account is None or account.deleted_at is not None:
            raise EntityNotFoundError(f"account {target_account_id} not visible")
        users = await self._repo.list_users_by_account(session, target_account_id)
        return DeletionPreview(
            resource_type="account",
            resource_id=str(account.id),
            target_name=account.display_name,
            impacted={
                "users": len(users),
                "content_refs": await self._count_content_refs(session, target_account_id),
            },
            recoverable=True,
            purge_after=None,
        )

    # ── 软删除（05 §11.3，AC⑨）──

    async def delete_user(
        self,
        session: AsyncSession,
        *,
        actor,
        scope_account_id: uuid.UUID,
        target_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> DeletionJobResult:
        """`DELETE /admin/users/{id}`（user.delete，05 §12.6）。

        - 幂等（AC⑧）：目标已有未恢复删除任务时复用并返回同一 job；
        - 撤销目标全部登录 Session（05 §11.3：先禁用登录和撤销会话）；
        - `soft_delete_user` 写 deleted_at/purge_after（30 天，04 §10.2）；
        - 建立 `iam_deletion_jobs` 并写审计；
        - 对象立即从正常查询隐藏（`_load_user_in_scope` 过滤 deleted_at）。
        """
        existing = await self._store.get_active_deletion_job(
            session, "user", str(target_user_id)
        )
        if existing is not None:
            return _job_result(existing, "user", str(target_user_id))
        await self._load_user_in_scope(session, scope_account_id, target_user_id)
        # 最后一名 Account Admin 不可删除（05 §12.2 `LAST_ACCOUNT_ADMIN_REQUIRED`，
        # 对齐 P1-E5 disable 语义）。
        role_code = await self._role_code_of(session, target_user_id)
        if role_code == "account_admin":
            count = await self._count_active_account_admins(session, scope_account_id)
            if count <= 1:
                raise LastAccountAdminError()
        now = datetime.now(timezone.utc)
        await self._repo.revoke_all_sessions_for_user(
            session, target_user_id, reason="user_deleted"
        )
        deleted = await self._repo.soft_delete_user(
            session, target_user_id, actor_id=actor.actor_user_id
        )
        job = await self._store.insert_deletion_job(
            session,
            _deletion_job(
                account_id=scope_account_id,
                resource_type="user",
                resource_id=str(target_user_id),
                deleted_by=actor.actor_user_id,
                ov_uri=None,
                now=now,
                purge_days=self._config.deletion_purge_days,
            ),
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=scope_account_id,
            actor_type="user",
            actor_user_id=actor.actor_user_id,
            actor_account_id=actor.actor_account_id,
            actor_session_id=actor.session_id,
            authentication_method=actor.authentication_method,
            subject_account_id=scope_account_id,
            subject_user_id=target_user_id,
            action="user.delete",
            target_type="iam_users",
            target_id=str(target_user_id),
            scope="account",
            result="success",
            metadata={"deletion_job_id": str(job.id), "purge_after": job.purge_after.isoformat()},
        )
        return DeletionJobResult(
            resource_type="user",
            resource_id=str(deleted.id),
            deletion_job_id=job.id,
            deleted_at=now.isoformat(),
            restore_until=job.purge_after.isoformat(),
        )

    async def delete_account(
        self,
        session: AsyncSession,
        *,
        actor,
        target_account_id: uuid.UUID,
        request_id: str | None = None,
    ) -> DeletionJobResult:
        """`DELETE /platform/accounts/{id}`（account.delete，05 §12.6 平台表）。

        - 幂等（AC⑧）：目标已有未恢复删除任务时复用并返回同一 job；
        - 撤销 Account 全部登录 Session；
        - `soft_delete_account` 写 deleted_at/purge_after（30 天，04 §10.1）；
        - 建立 `iam_deletion_jobs` 并写审计（Actor 与 Subject 同时保留，
          05 §12.6 注）。
        """
        existing = await self._store.get_active_deletion_job(
            session, "account", str(target_account_id)
        )
        if existing is not None:
            return _job_result(existing, "account", str(target_account_id))
        account = await self._repo.get_account(session, target_account_id)
        if account is None or account.deleted_at is not None:
            raise EntityNotFoundError(f"account {target_account_id} not visible")
        now = datetime.now(timezone.utc)
        users = await self._repo.list_users_by_account(session, target_account_id)
        for user in users:
            await self._repo.revoke_all_sessions_for_user(
                session, user.id, reason="account_deleted"
            )
        deleted = await self._repo.soft_delete_account(
            session, target_account_id, actor_id=actor.actor_user_id
        )
        job = await self._store.insert_deletion_job(
            session,
            _deletion_job(
                account_id=target_account_id,
                resource_type="account",
                resource_id=str(target_account_id),
                deleted_by=actor.actor_user_id,
                ov_uri=None,
                now=now,
                purge_days=self._config.deletion_purge_days,
            ),
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=target_account_id,
            actor_type="user",
            actor_user_id=actor.actor_user_id,
            actor_account_id=actor.actor_account_id,
            actor_session_id=actor.session_id,
            authentication_method=actor.authentication_method,
            subject_account_id=target_account_id,
            action="account.delete",
            target_type="iam_accounts",
            target_id=str(target_account_id),
            scope="platform",
            result="success",
            metadata={"deletion_job_id": str(job.id), "purge_after": job.purge_after.isoformat()},
        )
        return DeletionJobResult(
            resource_type="account",
            resource_id=str(deleted.id),
            deletion_job_id=job.id,
            deleted_at=now.isoformat(),
            restore_until=job.purge_after.isoformat(),
        )

    # ── 回收站（05 §12.6：admin/platform 聚合，权限按对象类型分别校验）──

    async def list_recycle_bin(
        self,
        session: AsyncSession,
        *,
        scope: str,
        principal,
        account_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[RecycleBinRow]:
        """回收站列表；`restore_allowed` 按 05 §12.6 注映射实时计算。

        - admin scope：当前 Account 的 user 任务；
        - platform scope：account 任务（Platform Super Admin 专属）。
        """
        if scope == "account":
            if account_id is None:
                raise EntityNotFoundError("account scope required")
            jobs = await self._store.list_deletion_jobs(
                session, account_id=account_id, resource_types=("user",), limit=limit
            )
        else:
            jobs = await self._store.list_deletion_jobs(
                session, account_id=account_id, resource_types=("account",), limit=limit
            )
        rows: list[RecycleBinRow] = []
        for job in jobs:
            allowed, permission = self._restore_permission_for(scope, job.resource_type, principal)
            rows.append(
                RecycleBinRow(
                    job=job,
                    target_name=await self._target_name(session, job),
                    restore_allowed=allowed,
                    restore_permission=permission,
                )
            )
        return rows

    async def restore(
        self,
        session: AsyncSession,
        *,
        scope: str,
        principal,
        account_id: uuid.UUID | None,
        job_id: uuid.UUID,
        request_id: str | None = None,
    ) -> DeletionJobResult:
        """回收站恢复（05 §12.6 注：按对象类型分别校验权限，AC⑨）。

        - `RESTORE_WINDOW_EXPIRED`（409）：已过 purge_after 或已 purged；
        - `ALREADY_RESTORED`（409）：重复恢复幂等拒绝；
        - 恢复动作必须写审计；User/Account 恢复接口同时保留 Actor 与 Subject。
        """
        job = await self._store.get_deletion_job(session, job_id)
        if job is None or (account_id is not None and job.account_id != account_id):
            raise EntityNotFoundError(f"deletion job {job_id} not visible")
        if job.status == JOB_RESTORED:
            raise DeletionJobError("ALREADY_RESTORED")
        if job.status == "purged":
            raise DeletionJobError("RESTORE_WINDOW_EXPIRED")
        if job.purge_after <= datetime.now(timezone.utc):
            raise DeletionJobError("RESTORE_WINDOW_EXPIRED")

        allowed, permission = self._restore_permission_for(scope, job.resource_type, principal)
        if not allowed:
            raise AdminActionForbiddenError("PERMISSION_NOT_GRANTED")
        assert permission is not None

        now = datetime.now(timezone.utc)
        job.status = JOB_RESTORED
        job.restored_by = principal.actor_user_id
        job.restored_at = now
        await session.flush()

        if job.resource_type == "user":
            await self._store.restore_user(session, uuid.UUID(job.resource_id))
        elif job.resource_type == "account":
            await self._store.restore_account(session, uuid.UUID(job.resource_id))
        else:
            raise DeletionJobError("NOT_RESTORABLE")

        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=job.account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_account_id=job.account_id,
            subject_user_id=uuid.UUID(job.resource_id) if job.resource_type == "user" else None,
            action=f"{job.resource_type}.restore",
            target_type=job.resource_type,
            target_id=job.resource_id,
            scope=scope,
            result="success",
            metadata={"deletion_job_id": str(job.id)},
        )
        return DeletionJobResult(
            resource_type=job.resource_type,
            resource_id=job.resource_id,
            deletion_job_id=job.id,
            deleted_at=job.deleted_at.isoformat(),
            restore_until=job.purge_after.isoformat(),
        )

    # ── 内部工具 ──

    def _restore_permission_for(
        self, scope: str, resource_type: str, principal
    ) -> tuple[bool, str | None]:
        """05 §12.6 注：类型化恢复权限映射表。"""
        mapping = RESTORE_PERMISSION_MAP.get(resource_type)
        if mapping is None:
            return False, None
        expected_scope, permission_code, _ = mapping
        if scope != expected_scope:
            return False, None
        return permission_code in principal.permissions, permission_code

    async def _load_user_in_scope(
        self, session: AsyncSession, scope_account_id: uuid.UUID, target_user_id: uuid.UUID
    ) -> IamUser:
        user = await self._repo.get_user(session, target_user_id)
        if user is None or user.deleted_at is not None or user.account_id != scope_account_id:
            raise EntityNotFoundError(f"user {target_user_id} not visible")
        return user

    async def _count_sessions(self, session: AsyncSession, user_id: uuid.UUID) -> int:
        return await self._count(session, "iam_sessions", "user_id", user_id)

    async def _count_api_keys(self, session: AsyncSession, user_id: uuid.UUID) -> int:
        return await self._count(session, "iam_api_credentials", "user_id", user_id)

    async def _count_content_refs(self, session: AsyncSession, account_id: uuid.UUID) -> int:
        return await self._count(session, "platform_content_refs", "account_id", account_id)

    async def _count(
        self, session: AsyncSession, table: str, column: str, value: uuid.UUID
    ) -> int:
        result = await session.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :v").bindparams(v=value)
        )
        return len(list(result.scalars()))

    async def _target_name(self, session: AsyncSession, job: IamDeletionJob) -> str | None:
        if job.resource_type == "user":
            user = await self._repo.get_user(session, uuid.UUID(job.resource_id))
            if user is None:
                return None
            return user.display_name or user.username
        if job.resource_type == "account":
            account = await self._repo.get_account(session, uuid.UUID(job.resource_id))
            if account is None:
                return None
            return account.display_name
        return None

    async def _role_code_of(self, session: AsyncSession, user_id: uuid.UUID) -> str | None:
        from openviking.server.platform.models import IamRole, IamUserRole

        result = await session.execute(
            select(IamRole.code)
            .join(IamUserRole, IamUserRole.role_id == IamRole.id)
            .where(IamUserRole.user_id == user_id)
        )
        return result.scalars().first()

    async def _count_active_account_admins(
        self, session: AsyncSession, account_id: uuid.UUID
    ) -> int:
        from openviking.server.platform.models import IamRole, IamUser, IamUserRole

        stmt = (
            select(func.count())
            .select_from(IamUser)
            .join(IamUserRole, IamUserRole.user_id == IamUser.id)
            .join(IamRole, IamRole.id == IamUserRole.role_id)
            .where(
                IamUser.account_id == account_id,
                IamRole.code == "account_admin",
                IamUser.deleted_at.is_(None),
                IamUser.status == "active",
            )
        )
        return (await session.execute(stmt)).scalar_one()


def _deletion_job(
    *,
    account_id: uuid.UUID,
    resource_type: str,
    resource_id: str,
    deleted_by: uuid.UUID,
    ov_uri: str | None,
    now: datetime,
    purge_days: int,
) -> IamDeletionJob:
    from datetime import timedelta

    return IamDeletionJob(
        account_id=account_id,
        resource_type=resource_type,
        resource_id=resource_id,
        ov_uri=ov_uri,
        deleted_by=deleted_by,
        deleted_at=now,
        purge_after=now + timedelta(days=purge_days),
        status=JOB_PENDING,
    )


def _job_result(job: IamDeletionJob, resource_type: str, resource_id: str) -> DeletionJobResult:
    """幂等删除复用：由既有任务构造结果（AC⑧：重复删除不产生重复任务）。"""
    return DeletionJobResult(
        resource_type=resource_type,
        resource_id=resource_id,
        deletion_job_id=job.id,
        deleted_at=job.deleted_at.isoformat(),
        restore_until=job.purge_after.isoformat(),
    )
