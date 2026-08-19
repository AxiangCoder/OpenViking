"""PostgreSQL IAM Repository 实现（05 §11 `iam/postgres_repository.py`）。

完整性冲突（唯一/外键）统一包装为 ConstraintViolationError；失败语句会
abort 当前事务，捕获后执行 session.rollback() 再抛出，调用方按需重试。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.errors import (
    ConstraintViolationError,
    EntityNotFoundError,
    OptimisticLockError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import (
    IamAccount,
    IamApiCredential,
    IamAuditEvent,
    IamOAuthGrant,
    IamOAuthToken,
    IamPermission,
    IamRole,
    IamRolePermission,
    IamSession,
    IamUser,
    IamUserRole,
)

SOFT_DELETE_RECYCLE_DAYS = 30


def normalize_email(email: str) -> str:
    """04 §10.2：仅 Unicode/大小写与首尾空白规范化，不做点号/+tag 折叠。"""
    return email.strip().casefold()


def normalize_username(username: str) -> str:
    """username 规范化：去除首尾空白（完整规则由服务层扩展）。"""
    return username.strip()


def _recycle_times(now: datetime) -> tuple[datetime, datetime]:
    purge_after = now + timedelta(days=SOFT_DELETE_RECYCLE_DAYS)
    return now, purge_after


class PostgresIamRepository(IamRepository):
    """基于 SQLAlchemy async + asyncpg 的 IAM 数据访问实现。"""

    # ── accounts ──

    async def create_account(
        self,
        session: AsyncSession,
        *,
        ov_account_id: str,
        code: str,
        display_name: str,
        status: str = "provisioning",
    ) -> IamAccount:
        account = IamAccount(
            ov_account_id=ov_account_id,
            code=code,
            display_name=display_name,
            status=status,
        )
        return await self._insert(session, account)

    async def get_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
    ) -> IamAccount | None:
        return await session.get(IamAccount, account_id)

    async def get_account_by_code(
        self,
        session: AsyncSession,
        code: str,
    ) -> IamAccount | None:
        stmt = select(IamAccount).where(IamAccount.code == code)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_account_by_ov_account_id(
        self,
        session: AsyncSession,
        ov_account_id: str,
    ) -> IamAccount | None:
        stmt = select(IamAccount).where(IamAccount.ov_account_id == ov_account_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_accounts(
        self,
        session: AsyncSession,
        *,
        include_deleted: bool = False,
    ) -> list[IamAccount]:
        stmt = select(IamAccount).order_by(IamAccount.created_at)
        if not include_deleted:
            stmt = stmt.where(IamAccount.deleted_at.is_(None))
        return list((await session.execute(stmt)).scalars())

    async def update_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        expected_version: int,
        **fields,
    ) -> IamAccount:
        allowed = {"display_name", "status", "provisioning_error"}
        values = {k: v for k, v in fields.items() if k in allowed}
        values["version"] = IamAccount.version + 1
        stmt = (
            update(IamAccount)
            .where(IamAccount.id == account_id)
            .where(IamAccount.version == expected_version)
            .values(**values)
            .returning(IamAccount)
        )
        account = (await session.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise OptimisticLockError(f"account {account_id} version {expected_version} is stale")
        return account

    async def soft_delete_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
    ) -> IamAccount:
        now, purge_after = _recycle_times(datetime.now(timezone.utc))
        stmt = (
            update(IamAccount)
            .where(IamAccount.id == account_id)
            .where(IamAccount.deleted_at.is_(None))
            .values(
                deleted_at=now,
                purge_after=purge_after,
                deleted_by=actor_id,
                updated_at=now,
            )
            .returning(IamAccount)
        )
        account = (await session.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise EntityNotFoundError(f"account {account_id} not found")
        return account

    # ── users ──

    async def create_user(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID | None,
        ov_user_id: str | None,
        username: str,
        email: str,
        display_name: str | None = None,
        password_hash: str,
        status: str = "provisioning",
    ) -> IamUser:
        user = IamUser(
            account_id=account_id,
            ov_user_id=ov_user_id,
            username=username,
            normalized_username=normalize_username(username),
            email=email,
            normalized_email=normalize_email(email),
            display_name=display_name,
            password_hash=password_hash,
            status=status,
        )
        return await self._insert(session, user)

    async def get_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> IamUser | None:
        return await session.get(IamUser, user_id)

    async def get_user_by_normalized_email(
        self,
        session: AsyncSession,
        normalized_email: str,
    ) -> IamUser | None:
        stmt = select(IamUser).where(IamUser.normalized_email == normalize_email(normalized_email))
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_users_by_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> list[IamUser]:
        stmt = select(IamUser).where(IamUser.account_id == account_id).order_by(IamUser.created_at)
        if not include_deleted:
            stmt = stmt.where(IamUser.deleted_at.is_(None))
        return list((await session.execute(stmt)).scalars())

    async def update_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        **fields,
    ) -> IamUser:
        allowed = {
            "ov_user_id",
            "display_name",
            "password_hash",
            "password_changed_at",
            "status",
            "last_login_at",
            "username",
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        stmt = update(IamUser).where(IamUser.id == user_id).values(**values).returning(IamUser)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user is None:
            raise EntityNotFoundError(f"user {user_id} not found")
        return user

    async def bump_permission_version(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> None:
        stmt = (
            update(IamUser)
            .where(IamUser.id == user_id)
            .values(permission_version=IamUser.permission_version + 1)
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            raise EntityNotFoundError(f"user {user_id} not found")

    async def soft_delete_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
    ) -> IamUser:
        now, purge_after = _recycle_times(datetime.now(timezone.utc))
        stmt = (
            update(IamUser)
            .where(IamUser.id == user_id)
            .where(IamUser.deleted_at.is_(None))
            .values(
                deleted_at=now,
                purge_after=purge_after,
                deleted_by=actor_id,
                updated_at=now,
            )
            .returning(IamUser)
        )
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user is None:
            raise EntityNotFoundError(f"user {user_id} not found")
        return user

    # ── sessions ──

    async def create_session(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        account_id: uuid.UUID | None,
        token_hash: str,
        csrf_secret_hash: str,
        last_seen_at: datetime,
        idle_expires_at: datetime,
        absolute_expires_at: datetime,
        ip_hash: str | None = None,
        user_agent: str | None = None,
    ) -> IamSession:
        record = IamSession(
            user_id=user_id,
            account_id=account_id,
            token_hash=token_hash,
            csrf_secret_hash=csrf_secret_hash,
            last_seen_at=last_seen_at,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            ip_hash=ip_hash,
            user_agent=user_agent,
        )
        return await self._insert(session, record)

    async def get_session_by_token_hash(
        self,
        session: AsyncSession,
        token_hash: str,
    ) -> IamSession | None:
        stmt = select(IamSession).where(IamSession.token_hash == token_hash)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def revoke_session(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
        *,
        reason: str,
    ) -> IamSession | None:
        stmt = (
            update(IamSession)
            .where(IamSession.id == session_id)
            .where(IamSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
            .returning(IamSession)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def revoke_all_sessions_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        reason: str,
    ) -> int:
        stmt = (
            update(IamSession)
            .where(IamSession.user_id == user_id)
            .where(IamSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
        )
        result = await session.execute(stmt)
        return result.rowcount

    # ── api_credentials ──

    async def create_api_credential(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str,
        public_id: str,
        key_hash: str,
        key_last_four: str,
        created_by: uuid.UUID,
        expires_at: datetime | None = None,
    ) -> IamApiCredential:
        record = IamApiCredential(
            account_id=account_id,
            user_id=user_id,
            name=name,
            public_id=public_id,
            key_hash=key_hash,
            key_last_four=key_last_four,
            created_by=created_by,
            expires_at=expires_at,
        )
        return await self._insert(session, record)

    async def get_api_credential_by_public_id(
        self,
        session: AsyncSession,
        public_id: str,
    ) -> IamApiCredential | None:
        stmt = select(IamApiCredential).where(IamApiCredential.public_id == public_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_api_credentials_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[IamApiCredential]:
        stmt = (
            select(IamApiCredential)
            .where(IamApiCredential.user_id == user_id)
            .order_by(IamApiCredential.created_at)
        )
        return list((await session.execute(stmt)).scalars())

    async def revoke_api_credential(
        self,
        session: AsyncSession,
        credential_id: uuid.UUID,
        *,
        revoked_by: uuid.UUID,
    ) -> IamApiCredential | None:
        stmt = (
            update(IamApiCredential)
            .where(IamApiCredential.id == credential_id)
            .where(IamApiCredential.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc), revoked_by=revoked_by)
            .returning(IamApiCredential)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def revoke_all_api_credentials_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        revoked_by: uuid.UUID,
    ) -> int:
        stmt = (
            update(IamApiCredential)
            .where(IamApiCredential.user_id == user_id)
            .where(IamApiCredential.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc), revoked_by=revoked_by)
        )
        result = await session.execute(stmt)
        return result.rowcount

    # ── P2-E6b：MCP OAuth 生命周期撤销（04 §10.13）──

    async def revoke_all_oauth_grants_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        revoked_by: uuid.UUID | None,
    ) -> int:
        now = datetime.now(timezone.utc)
        grants = (
            await session.execute(
                update(IamOAuthGrant)
                .where(IamOAuthGrant.user_id == user_id)
                .where(IamOAuthGrant.status != "revoked")
                .values(status="revoked", revoked_at=now, revoked_by=revoked_by)
            )
        ).rowcount or 0
        grant_ids = (
            await session.execute(
                select(IamOAuthGrant.id).where(
                    IamOAuthGrant.user_id == user_id, IamOAuthGrant.status == "revoked"
                )
            )
        ).scalars().all()
        if grant_ids:
            await session.execute(
                update(IamOAuthToken)
                .where(IamOAuthToken.grant_id.in_(grant_ids))
                .where(IamOAuthToken.status != "revoked")
                .values(status="revoked", revoked_at=now)
            )
        return grants

    async def revoke_all_oauth_grants_for_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        revoked_by: uuid.UUID | None,
    ) -> int:
        now = datetime.now(timezone.utc)
        grant_ids = (
            await session.execute(
                select(IamOAuthGrant.id).where(
                    IamOAuthGrant.account_id == account_id, IamOAuthGrant.status != "revoked"
                )
            )
        ).scalars().all()
        grants = (
            await session.execute(
                update(IamOAuthGrant)
                .where(IamOAuthGrant.account_id == account_id)
                .where(IamOAuthGrant.status != "revoked")
                .values(status="revoked", revoked_at=now, revoked_by=revoked_by)
            )
        ).rowcount or 0
        if grant_ids:
            await session.execute(
                update(IamOAuthToken)
                .where(IamOAuthToken.grant_id.in_(grant_ids))
                .where(IamOAuthToken.status != "revoked")
                .values(status="revoked", revoked_at=now)
            )
        return grants

    # ── roles / permissions ──

    async def get_role_by_code(
        self,
        session: AsyncSession,
        code: str,
    ) -> IamRole | None:
        stmt = select(IamRole).where(IamRole.code == code)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_roles(self, session: AsyncSession) -> list[IamRole]:
        stmt = select(IamRole).order_by(IamRole.rank.desc())
        return list((await session.execute(stmt)).scalars())

    async def list_permissions_for_roles(
        self,
        session: AsyncSession,
        role_ids: list[uuid.UUID],
    ) -> list[IamRolePermission]:
        if not role_ids:
            return []
        stmt = select(IamRolePermission).where(IamRolePermission.role_id.in_(role_ids))
        return list((await session.execute(stmt)).scalars())

    async def list_permissions(
        self,
        session: AsyncSession,
    ) -> list[IamPermission]:
        stmt = select(IamPermission).order_by(IamPermission.code)
        return list((await session.execute(stmt)).scalars())

    async def assign_role(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        assigned_by: uuid.UUID | None = None,
    ) -> IamUserRole:
        link = IamUserRole(user_id=user_id, role_id=role_id, assigned_by=assigned_by)
        return await self._insert(session, link)

    async def get_role_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> IamUserRole | None:
        stmt = select(IamUserRole).where(IamUserRole.user_id == user_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    # ── audit ──

    async def append_audit_event(
        self,
        session: AsyncSession,
        *,
        request_id: str | None = None,
        account_id: uuid.UUID | None = None,
        actor_type: str,
        actor_user_id: uuid.UUID | None = None,
        actor_account_id: uuid.UUID | None = None,
        actor_system_component: str | None = None,
        actor_session_id: uuid.UUID | None = None,
        authentication_method: str | None = None,
        actor_credential_id: uuid.UUID | None = None,
        subject_account_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        target_visibility: str | None = None,
        scope: str | None = None,
        result: str,
        reason: str | None = None,
        metadata: dict | None = None,
    ) -> IamAuditEvent:
        event = IamAuditEvent(
            request_id=request_id,
            account_id=account_id,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            actor_account_id=actor_account_id,
            actor_system_component=actor_system_component,
            actor_session_id=actor_session_id,
            authentication_method=authentication_method,
            actor_credential_id=actor_credential_id,
            subject_account_id=subject_account_id,
            subject_user_id=subject_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_visibility=target_visibility,
            scope=scope,
            result=result,
            reason=reason,
            metadata_json=metadata,
        )
        return await self._insert(session, event)

    async def list_audit_events(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
        action: str | None = None,
        result: str | None = None,
        limit: int = 100,
    ) -> list[IamAuditEvent]:
        stmt = select(IamAuditEvent).order_by(IamAuditEvent.occurred_at.desc())
        if account_id is not None:
            stmt = stmt.where(IamAuditEvent.account_id == account_id)
        if actor_user_id is not None:
            stmt = stmt.where(IamAuditEvent.actor_user_id == actor_user_id)
        if subject_user_id is not None:
            stmt = stmt.where(IamAuditEvent.subject_user_id == subject_user_id)
        if action is not None:
            stmt = stmt.where(IamAuditEvent.action == action)
        if result is not None:
            stmt = stmt.where(IamAuditEvent.result == result)
        stmt = stmt.limit(limit)
        return list((await session.execute(stmt)).scalars())

    # ── 内部工具 ──

    async def _insert(self, session: AsyncSession, record):
        session.add(record)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ConstraintViolationError(
                f"constraint violation on {record.__class__.__tablename__}: {exc.orig}"
            ) from exc
        return record
