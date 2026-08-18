"""认证服务层（05 §11 `auth/service.py`，03 §8.1–8.3）。

承载 P1-E3 业务规则（14 号计划 §96.3）：
- 统一 `LOGIN_FAILED` 防枚举 + IP+标识限流递增冷却（03 §8.3）；
- 登录/改密轮换当前登录 Session；登出/禁用/密码重置撤销（03 §8.1）；
- `auth/me` 权限摘要来自 RbacService 有效权限（与 P1-E2 一致）；
- 分级密码重置（服务层交付）：`actor_role_rank > target_role_rank`，
  只用 `iam_roles.rank`（3/2/1），与 OpenViking `Role` 内置 rank 无映射
  （03 §8.3 回填发现）；重置撤销目标全部登录 Session、不删对话数据、
  不撤销 API Key；admin/platform 端点由 P1-E5 交付；
- 首次 PSA 初始化/bootstrap 路径（06 §15.2 一次性部署命令）；
- 登录成功/失败/登出/会话撤销审计直写 P1-E1 repository（04 §10.8，
  Actor/Subject 分离、metadata 脱敏）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import (
    hash_ip,
    hash_password,
    random_initial_password,
    verify_password,
)
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.auth.rate_limit import LoginRateLimiter
from openviking.server.platform.auth.sessions import SessionService
from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.errors import (
    ConstraintViolationError,
    EntityNotFoundError,
    LoginFailedError,
    LoginRateLimitedError,
    PasswordResetForbiddenError,
    PlatformError,
)
from openviking.server.platform.iam.permissions import PLATFORM_SUPER_ADMIN
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.iam.service import RbacService

# 登录/改密失败审计中标识限流的稳定原因码
RATE_LIMITED = "RATE_LIMITED"
LOGIN_FAILED = "LOGIN_FAILED"
# bootstrap 审计的固定 system 组件名（04 §10.8：actor_type=system 时必填）
AUTH_SYSTEM_COMPONENT = "iam_auth"


@dataclass(frozen=True)
class LoginResult:
    """登录成功产物；`raw_token` 由 API 层下发 Set-Cookie，`csrf_token` 返回前端。"""

    raw_token: str
    csrf_token: str
    session_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class ChangedPasswordResult:
    """改密成功产物；`raw_token` 必须同步 Set-Cookie（05 §12.3 回填发现）。"""

    raw_token: str
    csrf_token: str
    session_id: uuid.UUID


@dataclass(frozen=True)
class PasswordResetResult:
    """分级密码重置结果（服务层）。"""

    new_password: str
    sessions_revoked: int


@dataclass(frozen=True)
class BootstrapResult:
    """首次 PSA 初始化结果；`initial_password` 只返回一次（03 §8.3）。"""

    user_id: uuid.UUID
    initial_password: str


def normalize_email(email: str) -> str:
    """邮箱规范化（03 §8.3/04 §10.2）：仅大小写与首尾空白规范化，
    与 P1-E1 repository 的 normalize_email 语义保持一致（casefold 对齐，
    不做点号/+tag 折叠）。"""
    return email.strip().casefold()


class AuthService:
    """认证服务：登录/登出/改密/分级重置/PSA bootstrap + 审计。"""

    def __init__(
        self,
        repo: IamRepository,
        rbac: RbacService,
        limiter: LoginRateLimiter | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._repo = repo
        self._rbac = rbac
        self._config = config
        self._limiter = limiter or LoginRateLimiter(
            max_attempts=config.login_max_attempts,
            cooldown_base_seconds=config.login_cooldown_base_seconds,
            cooldown_max_seconds=config.login_cooldown_max_seconds,
        )

    # ── 登录（03 §8.3：统一 LOGIN_FAILED + IP+标识限流递增冷却）──

    async def login(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        ip: str,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> LoginResult:
        """邮箱+密码登录。未知邮箱/错误密码/非 active 用户同一 LOGIN_FAILED
        （防枚举）；连续失败进入递增冷却（冷却期间正确密码同样被拒）。"""
        normalized_email = normalize_email(email)
        key = LoginRateLimiter.make_key(hash_ip(ip), normalized_email)

        retry_after = self._limiter.retry_after_seconds(key)
        if retry_after is not None:
            await self._audit_login_failed(
                session, normalized_email, reason=RATE_LIMITED, request_id=request_id
            )
            raise LoginRateLimitedError(retry_after)

        user = await self._repo.get_user_by_normalized_email(session, normalized_email)
        if (
            user is None
            or user.deleted_at is not None
            or user.status != "active"
            or not verify_password(user.password_hash, password)
        ):
            self._limiter.record_failure(key)
            await self._audit_login_failed(
                session, normalized_email, reason=LOGIN_FAILED, request_id=request_id
            )
            raise LoginFailedError()

        self._limiter.record_success(key)
        raw_token, csrf_token, session_id = await SessionService(
            self._repo, self._config
        ).create_login_session(
            session,
            user=user,
            ip_hash=hash_ip(ip),
            user_agent=user_agent,
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=user.account_id,
            actor_type="user",
            actor_user_id=user.id,
            actor_account_id=user.account_id,
            actor_session_id=session_id,
            authentication_method="session",
            subject_user_id=user.id,
            subject_account_id=user.account_id,
            action="auth.login",
            target_type="iam_sessions",
            target_id=str(session_id),
            scope="self",
            result="success",
            metadata={"method": "password"},
        )
        return LoginResult(
            raw_token=raw_token, csrf_token=csrf_token, session_id=session_id, user_id=user.id
        )

    # ── 登出（03 §8.1：撤销；审计直写 E1 repository）──

    async def logout(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedUserPrincipal,
        request_id: str | None = None,
    ) -> None:
        """撤销当前登录 Session 并写审计。"""
        if principal.session_id is not None:
            await self._repo.revoke_session(session, principal.session_id, reason="logout")
        await self._audit_auth_action(
            session,
            principal,
            action="auth.logout",
            metadata={"session_revoked": principal.session_id is not None},
            request_id=request_id,
        )

    async def logout_all(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedUserPrincipal,
        request_id: str | None = None,
    ) -> int:
        """撤销当前用户全部登录 Session（含当前），返回撤销条数。"""
        count = await self._repo.revoke_all_sessions_for_user(
            session, principal.actor_user_id, reason="logout_all"
        )
        await self._audit_auth_action(
            session,
            principal,
            action="auth.logout_all",
            metadata={"sessions_revoked": count},
            request_id=request_id,
        )
        return count

    # ── 修改密码（03 §8.3：必填旧密码；错误 → LOGIN_FAILED 计入限流；
    #      成功 → 轮换 Session 并同步 Set-Cookie，05 §12.3）──

    async def change_password(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedUserPrincipal,
        old_password: str,
        new_password: str,
        ip: str,
        request_id: str | None = None,
    ) -> ChangedPasswordResult:
        user = await self._repo.get_user(session, principal.actor_user_id)
        if user is None or user.deleted_at is not None:
            raise EntityNotFoundError(f"user {principal.actor_user_id} not found")

        key = LoginRateLimiter.make_key(hash_ip(ip), user.normalized_email)
        retry_after = self._limiter.retry_after_seconds(key)
        if retry_after is not None:
            await self._audit_auth_failed(
                session,
                principal,
                action="auth.password_change",
                reason=RATE_LIMITED,
                request_id=request_id,
            )
            raise LoginRateLimitedError(retry_after)

        if not verify_password(user.password_hash, old_password):
            self._limiter.record_failure(key)
            await self._audit_auth_failed(
                session,
                principal,
                action="auth.password_change",
                reason=LOGIN_FAILED,
                request_id=request_id,
            )
            raise LoginFailedError()

        self._limiter.record_success(key)
        await self._repo.update_user(
            session,
            user.id,
            password_hash=hash_password(new_password),
            password_changed_at=datetime.now(timezone.utc),
        )
        raw_token, csrf_token, new_session_id = await SessionService(
            self._repo, self._config
        ).rotate_session(
            session,
            user=user,
            old_session_id=principal.session_id,
            ip_hash=hash_ip(ip),
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=user.account_id,
            actor_type="user",
            actor_user_id=user.id,
            actor_account_id=user.account_id,
            actor_session_id=new_session_id,
            authentication_method="session",
            subject_user_id=user.id,
            subject_account_id=user.account_id,
            action="auth.password_change",
            target_type="iam_users",
            target_id=str(user.id),
            scope="self",
            result="success",
            metadata={"session_rotated": True},
        )
        return ChangedPasswordResult(
            raw_token=raw_token, csrf_token=csrf_token, session_id=new_session_id
        )

    # ── 分级密码重置（03 §8.3，服务层交付；admin/platform 端点 P1-E5）──

    async def reset_user_password(
        self,
        session: AsyncSession,
        *,
        actor_user_id: uuid.UUID,
        actor_account_id: uuid.UUID | None,
        target_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> PasswordResetResult:
        """分级密码重置：`actor_role_rank > target_role_rank`。

        等级只用 `iam_roles.rank`（3/2/1），与 OpenViking `Role` 内置 rank
        （USER=0/ADMIN=1/ROOT=2）完全独立、禁止混用（03 §8.3 回填发现）。

        - 目标不存在/已删除/跨 Account 不可见 → EntityNotFoundError（不写审计，
          防枚举，对齐 P1-E2 assign_role 的 not-found 语义）；
        - `actor_role_rank <= target_role_rank` → PasswordResetForbiddenError
          （先写 denied 审计再抛出）；
        - 成功：生成新随机密码（只返回一次），撤销目标全部登录 Session
          （reason=password_reset）；**不删对话数据、不撤销 API Key**（03 §8.3）。
        """
        actor = await self._repo.get_user(session, actor_user_id)
        target = await self._repo.get_user(session, target_user_id)
        if (
            actor is None
            or actor.deleted_at is not None
            or target is None
            or target.deleted_at is not None
        ):
            raise EntityNotFoundError(f"reset password for user {target_user_id}")
        if actor_account_id is not None and target.account_id != actor_account_id:
            # Account 作用域 actor 对跨 Account 目标统一 404（不可见语义）
            raise EntityNotFoundError(f"reset password for user {target_user_id}")

        actor_perms = await self._rbac.get_user_permissions(session, actor_user_id)
        target_perms = await self._rbac.get_user_permissions(session, target_user_id)
        if actor_perms.rank <= target_perms.rank:
            await self._repo.append_audit_event(
                session,
                request_id=request_id,
                account_id=target.account_id,
                actor_type="user",
                actor_user_id=actor.id,
                actor_account_id=actor.account_id,
                subject_user_id=target.id,
                subject_account_id=target.account_id,
                action="user.password.reset",
                target_type="iam_users",
                target_id=str(target.id),
                scope="platform" if actor_account_id is None else "account",
                result="denied",
                reason="PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN",
            )
            raise PasswordResetForbiddenError("PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN")

        new_password = random_initial_password()
        await self._repo.update_user(
            session,
            target.id,
            password_hash=hash_password(new_password),
            password_changed_at=datetime.now(timezone.utc),
        )
        revoked = await self._repo.revoke_all_sessions_for_user(
            session, target.id, reason="password_reset"
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=target.account_id,
            actor_type="user",
            actor_user_id=actor.id,
            actor_account_id=actor.account_id,
            subject_user_id=target.id,
            subject_account_id=target.account_id,
            action="user.password.reset",
            target_type="iam_users",
            target_id=str(target.id),
            scope="platform" if actor_account_id is None else "account",
            result="success",
            metadata={"sessions_revoked": revoked},
        )
        return PasswordResetResult(new_password=new_password, sessions_revoked=revoked)

    # ── 首次 PSA 初始化 / bootstrap 路径（06 §15.2 一次性部署命令）──

    async def bootstrap_platform_super_admin(
        self,
        session: AsyncSession,
        *,
        email: str,
        username: str,
        display_name: str | None = None,
        request_id: str | None = None,
    ) -> BootstrapResult:
        """创建首位 Platform Super Admin（account_id IS NULL，04 §10.4 部分唯一
        索引 + service invariant 双重强制；角色经 RbacService 平台初始化路径授予）。

        幂等守卫：平台已存在 PSA（无 Account 用户）时拒绝重复初始化。
        """
        await self._rbac.seed_catalog(session, request_id=request_id)
        normalized_email = normalize_email(email)
        existing = await self._repo.get_user_by_normalized_email(session, normalized_email)
        if existing is not None:
            if existing.account_id is None:
                raise PlatformError("PSA_ALREADY_BOOTSTRAPPED")
            raise ConstraintViolationError("EMAIL_ALREADY_EXISTS")

        initial_password = random_initial_password()
        user = await self._repo.create_user(
            session,
            account_id=None,
            ov_user_id=None,
            username=username,
            email=normalized_email,
            display_name=display_name,
            password_hash=hash_password(initial_password),
            status="active",
        )
        await session.flush()
        await self._rbac.assign_platform_super_admin(
            session, target_user_id=user.id, request_id=request_id
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            actor_type="system",
            actor_system_component=AUTH_SYSTEM_COMPONENT,
            authentication_method="system",
            action="platform.bootstrap",
            target_type="iam_users",
            target_id=str(user.id),
            scope="platform",
            result="success",
            metadata={"role": PLATFORM_SUPER_ADMIN},
        )
        return BootstrapResult(user_id=user.id, initial_password=initial_password)

    # ── 审计辅助（04 §10.8：Actor/Subject 分离、脱敏）──

    async def _audit_login_failed(
        self,
        session: AsyncSession,
        normalized_email: str,
        *,
        reason: str,
        request_id: str | None,
    ) -> None:
        """登录失败审计：Actor/Subject 未知（防枚举），仅记录规范化邮箱。"""
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            actor_type="user",
            action="auth.login",
            target_type="iam_users",
            result="failed",
            reason=reason,
            metadata={"normalized_email": normalized_email},
        )

    async def _audit_auth_action(
        self,
        session: AsyncSession,
        principal: AuthenticatedUserPrincipal,
        *,
        action: str,
        metadata: dict | None,
        request_id: str | None,
    ) -> None:
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=principal.actor_account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_user_id=principal.actor_user_id,
            subject_account_id=principal.actor_account_id,
            action=action,
            target_type="iam_sessions",
            scope="self",
            result="success",
            metadata=metadata,
        )

    async def _audit_auth_failed(
        self,
        session: AsyncSession,
        principal: AuthenticatedUserPrincipal,
        *,
        action: str,
        reason: str,
        request_id: str | None,
    ) -> None:
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=principal.actor_account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_user_id=principal.actor_user_id,
            subject_account_id=principal.actor_account_id,
            action=action,
            target_type="iam_users",
            scope="self",
            result="failed",
            reason=reason,
        )
