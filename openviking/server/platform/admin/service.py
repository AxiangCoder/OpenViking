"""IAM 管理服务层（05 §11，14 号计划 §96.5）。

承载 P1-E5 业务规则（本模块为新增服务层，既有 iam/、auth/ 模块只调用、复用）：
- PSA 创建 Account + 首位 Admin：一次返回初始密码、ov 映射、角色 account_admin
  （03 §8.3 / 05 §12.6 平台表；P2-E1：同一 PG 事务写 iam_outbox，Account/User
  初始 provisioning，由 ProvisioningWorker 转 active）；
- Account Admin 直建 User：角色固定 `user`，一次返回初始密码（05 §12.6）；
- 用户管理：列表 / PATCH 启用禁用 / disable / 分级密码重置（复用 AuthService）；
- 平台级提升：`PUT .../role` 仅 `user → account_admin`，即时生效（04 §10.6）；
- 管理员查看/撤销用户 API Key 元数据：P1-E4 未合并前直接用 E1 repository
  实现（04 §10.3），合并后由协调者验证（14 号计划 §96.5）；
- 守卫：跨 Account 404（统一不可见语义）、同级 403、`LAST_ACCOUNT_ADMIN_REQUIRED`、
  密码仅一次返回（服务端只存 Argon2id hash，任何查询不可再取）；
- 全部管理动作写审计：Actor/Subject 分离、metadata 脱敏（04 §10.8），
  拒绝动作先写 denied 审计再抛出。

数据访问：既有 E1 repository 只调用不修改；repository 未提供的查询
（分页、用户名唯一、角色链接批量）按 `iam/service.py` 既有约定直接使用
SQLAlchemy（P1-E1 冻结模块不因服务层需求改动）。
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import hash_password, random_initial_password
from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.auth.service import (
    AuthService,
    PasswordResetResult,
    normalize_email,
)
from openviking.server.platform.errors import (
    AdminActionForbiddenError,
    ConstraintViolationError,
    EntityNotFoundError,
    InvalidCursorError,
    LastAccountAdminError,
)
from openviking.server.platform.iam.permissions import ACCOUNT_ADMIN, USER
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.iam.service import RbacService
from openviking.server.platform.models import (
    IamAccount,
    IamApiCredential,
    IamAuditEvent,
    IamRole,
    IamUser,
    IamUserRole,
)
from openviking.server.platform.provisioning.control_plane import FakeControlPlane
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.provisioning.service import ProvisioningService


@dataclass(frozen=True)
class CreatedAccountResult:
    """PSA 创建 Account + 首位 Admin 的结果；`initial_password` 只返回一次。"""

    account: IamAccount
    admin: IamUser
    initial_password: str


@dataclass(frozen=True)
class CreatedUserResult:
    """Account Admin 直建 User 的结果；`initial_password` 只返回一次。"""

    user: IamUser
    initial_password: str


@dataclass(frozen=True)
class UserStatusResult:
    """启用/禁用结果；changed=False 表示目标已处于目标状态（幂等，不写审计）。"""

    user: IamUser
    sessions_revoked: int
    keys_revoked: int
    changed: bool


@dataclass(frozen=True)
class UserRow:
    """用户列表行：User + 其 v0.1 单内置角色 code（只读视图数据基础）。"""

    user: IamUser
    role_code: str | None


def encode_cursor(ts: datetime, entity_id: uuid.UUID) -> str:
    """不透明分页 cursor（05 §12.2）：`occurred_at/created_at|id` 的 base64url。"""
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{entity_id}".encode("utf-8")).decode("ascii")


def decode_cursor(raw: str | None) -> tuple[datetime, uuid.UUID] | None:
    """解析 cursor；非法格式抛 InvalidCursorError（API 层映射 400 INVALID_CURSOR）。"""
    if not raw:
        return None
    try:
        payload = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        ts_str, id_str = payload.split("|", 1)
        return datetime.fromisoformat(ts_str), uuid.UUID(id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("INVALID_CURSOR") from exc


class AdminService:
    """IAM 管理服务：Account/User 生命周期、守卫、API Key 元数据与审计读取。

    P2-E1（14 号计划 §97.1）：创建流程引入 outbox 事务写入——Account+首位
    Admin 与 iam_outbox 事件同一 PG 事务（05 §11.3 步骤 1），Account/User
    初始 status=provisioning，由 ProvisioningWorker 初始化 OpenViking
    namespace 后转 active；`provisioning` 依赖注入失败时回退开发态
    FakeControlPlane（测试/未装配环境，生产装配经 create_app 注入）。
    """

    def __init__(
        self,
        repo: IamRepository,
        rbac: RbacService,
        auth: AuthService,
        provisioning: ProvisioningService | None = None,
    ) -> None:
        self._repo = repo
        self._rbac = rbac
        self._auth = auth
        self._provisioning = provisioning or ProvisioningService(
            repo, ProvisioningRepository(), FakeControlPlane()
        )

    # ── PSA：创建 Account + 首位 Admin（03 §8.3 / 05 §12.6 平台表）──

    async def create_account_with_first_admin(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        account_code: str,
        account_name: str,
        admin_email: str,
        admin_username: str,
        admin_display_name: str | None = None,
        request_id: str | None = None,
    ) -> CreatedAccountResult:
        """创建 Account + 首位 Account Admin（同一 PG 事务），一次返回初始密码。

        - `code`/`ov_account_id` 双唯一（04 §10.1），`normalized_email` 全局唯一；
        - 首位 Admin 角色固定 `account_admin`（经 RbacService 平台路径授予）；
        - P2-E1：Account/User 初始 status=provisioning，并在**同一事务**写入
          iam_outbox（account.provision + user.provision，05 §11.3 步骤 1）；
          失败路径由 repository 的 ConstraintViolationError 包装（事务已回滚），
          预检查给出稳定错误码（ACCOUNT_CODE_ALREADY_EXISTS / EMAIL_ALREADY_EXISTS）；
        - 审计：action=`account.create`，Actor=PSA、Subject=Account+首位 Admin。
        """
        code = account_code.strip()
        if await self._repo.get_account_by_code(session, code) is not None:
            raise ConstraintViolationError("ACCOUNT_CODE_ALREADY_EXISTS")
        normalized_email = normalize_email(admin_email)
        if await self._repo.get_user_by_normalized_email(session, normalized_email) is not None:
            raise ConstraintViolationError("EMAIL_ALREADY_EXISTS")

        initial_password = random_initial_password()
        account = await self._repo.create_account(
            session,
            ov_account_id=f"ov_account_{uuid.uuid4().hex[:12]}",
            code=code,
            display_name=account_name,
            status="provisioning",
        )
        await session.flush()
        admin = await self._repo.create_user(
            session,
            account_id=account.id,
            ov_user_id=f"ov_user_{uuid.uuid4().hex[:10]}",
            username=admin_username,
            email=normalized_email,
            display_name=admin_display_name,
            password_hash=hash_password(initial_password),
            status="provisioning",
        )
        await session.flush()
        await self._rbac.assign_role(
            session,
            actor_user_id=actor.actor_user_id,
            actor_account_id=None,
            target_user_id=admin.id,
            role_code=ACCOUNT_ADMIN,
            request_id=request_id,
        )
        await self._provisioning.create_provisioning_events(
            session, account=account, admin=admin
        )
        await self._append_admin_audit(
            session,
            actor=actor,
            action="account.create",
            account_id=account.id,
            subject_user_id=admin.id,
            target_type="iam_accounts",
            target_id=str(account.id),
            metadata={"account_code": code, "role": ACCOUNT_ADMIN},
            request_id=request_id,
        )
        return CreatedAccountResult(account=account, admin=admin, initial_password=initial_password)

    # ── Account Admin：直建 User（角色固定 user，05 §12.6）──

    async def create_account_user(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        email: str,
        username: str,
        display_name: str | None = None,
        request_id: str | None = None,
    ) -> CreatedUserResult:
        """Account Admin 在自己的 Account 内创建普通 User（角色固定 `user`）。

        - 请求体携带的任何角色字段都被忽略（API 层不接收）；
        - Actor 无 Account 上下文（PSA 走平台路径）→ 404 不可见语义；
        - 审计：action=`user.create`，Actor/Subject 分离。
        """
        if actor.actor_account_id is None:
            raise EntityNotFoundError("create user requires an account context")
        normalized_email = normalize_email(email)
        if await self._repo.get_user_by_normalized_email(session, normalized_email) is not None:
            raise ConstraintViolationError("EMAIL_ALREADY_EXISTS")
        existing_username = (
            await session.execute(
                select(IamUser.id).where(
                    IamUser.account_id == actor.actor_account_id,
                    IamUser.normalized_username == username.strip(),
                )
            )
        ).first()
        if existing_username is not None:
            raise ConstraintViolationError("USERNAME_ALREADY_EXISTS")

        initial_password = random_initial_password()
        user = await self._repo.create_user(
            session,
            account_id=actor.actor_account_id,
            ov_user_id=f"ov_user_{uuid.uuid4().hex[:10]}",
            username=username,
            email=normalized_email,
            display_name=display_name,
            password_hash=hash_password(initial_password),
            status="active",
        )
        await session.flush()
        await self._rbac.assign_role(
            session,
            actor_user_id=actor.actor_user_id,
            actor_account_id=actor.actor_account_id,
            target_user_id=user.id,
            role_code=USER,
            request_id=request_id,
        )
        await self._append_admin_audit(
            session,
            actor=actor,
            action="user.create",
            account_id=user.account_id,
            subject_user_id=user.id,
            target_type="iam_users",
            target_id=str(user.id),
            metadata={"role": USER},
            request_id=request_id,
        )
        return CreatedUserResult(user=user, initial_password=initial_password)

    # ── 用户列表（05 §12.6 GET /admin/users、GET /platform/accounts/{id}/users）──

    async def list_accounts(
        self,
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> tuple[list[IamAccount], str | None]:
        """Account 列表（排除回收期；cursor 分页，05 §12.2）。"""
        return await self._keyset_page(
            session, IamAccount, IamAccount.created_at, [IamAccount.deleted_at.is_(None)], limit, cursor
        )

    async def list_users(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID | None,
        limit: int = 50,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> tuple[list[UserRow], str | None]:
        """Account 用户列表（排除已删除；无 Account 上下文返回空列表）。"""
        if account_id is None:
            return [], None
        rows, next_cursor = await self._keyset_page(
            session,
            IamUser,
            IamUser.created_at,
            [IamUser.account_id == account_id, IamUser.deleted_at.is_(None)],
            limit,
            cursor,
        )
        role_map = await self._role_codes(session, rows)
        return [UserRow(user=u, role_code=role_map.get(u.id)) for u in rows], next_cursor

    async def list_account_users(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        limit: int = 50,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> tuple[list[UserRow], str | None]:
        """平台路径：目标 Account 不存在/已删除 → 404（不可见语义）。"""
        account = await self._repo.get_account(session, account_id)
        if account is None or account.deleted_at is not None:
            raise EntityNotFoundError(f"account {account_id} not found")
        return await self.list_users(session, account_id=account_id, limit=limit, cursor=cursor)

    # ── 用户管理：PATCH 启用禁用 / disable（05 §12.6）──

    async def patch_user(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        target_user_id: uuid.UUID,
        display_name: str | None = None,
        status: str | None = None,
        request_id: str | None = None,
    ) -> UserRow:
        """PATCH 用户：display_name 与 status（active/disabled）至少一项。

        状态切换经 `_apply_status`（启用禁用守卫 + 即时失效 + 审计）。
        """
        target, link = await self._load_scoped_user(session, actor.actor_account_id, target_user_id)
        if display_name is not None and display_name != target.display_name:
            target = await self._repo.update_user(session, target.id, display_name=display_name)
            await self._append_admin_audit(
                session,
                actor=actor,
                action="user.update",
                account_id=target.account_id,
                subject_user_id=target.id,
                target_type="iam_users",
                target_id=str(target.id),
                metadata={"field": "display_name"},
                request_id=request_id,
            )
        if status is not None:
            await self._apply_status(session, actor=actor, target=target, link=link, status=status, request_id=request_id)
        role_code = await self._role_code_of(session, link)
        return UserRow(user=target, role_code=role_code)

    async def set_user_status(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        target_user_id: uuid.UUID,
        status: str,
        request_id: str | None = None,
    ) -> UserStatusResult:
        """启用/禁用（POST /admin/users/{id}/disable 与 PATCH status 共用）。

        禁用（AC ⑤）：即时杀全部登录 Session + 全部 API Key，并递增
        `permission_version`（03 §9.4 缓存键）保证禁用状态即时生效；
        守卫（AC ⑦）：目标为最后一名 active Account Admin 时拒绝
        `LAST_ACCOUNT_ADMIN_REQUIRED`（先写 denied 审计再抛出）。
        """
        target, link = await self._load_scoped_user(session, actor.actor_account_id, target_user_id)
        return await self._apply_status(session, actor=actor, target=target, link=link, status=status, request_id=request_id)

    async def _apply_status(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        target: IamUser,
        link: IamUserRole | None,
        status: str,
        request_id: str | None,
    ) -> UserStatusResult:
        if target.status == status:
            return UserStatusResult(user=target, sessions_revoked=0, keys_revoked=0, changed=False)
        if status == "disabled":
            admin_role = await self._repo.get_role_by_code(session, ACCOUNT_ADMIN)
            if (
                link is not None
                and admin_role is not None
                and link.role_id == admin_role.id
                and await self._count_active_account_admins(session, target.account_id) <= 1
            ):
                await self._append_admin_audit(
                    session,
                    actor=actor,
                    action="user.disable",
                    account_id=target.account_id,
                    subject_user_id=target.id,
                    target_type="iam_users",
                    target_id=str(target.id),
                    result="denied",
                    reason="LAST_ACCOUNT_ADMIN_REQUIRED",
                    request_id=request_id,
                )
                raise LastAccountAdminError("LAST_ACCOUNT_ADMIN_REQUIRED")

        await self._repo.update_user(session, target.id, status=status)
        await self._repo.bump_permission_version(session, target.id)
        sessions_revoked = 0
        keys_revoked = 0
        if status == "disabled":
            sessions_revoked = await self._repo.revoke_all_sessions_for_user(
                session, target.id, reason="user_disabled"
            )
            keys_revoked = await self._repo.revoke_all_api_credentials_for_user(
                session, target.id, revoked_by=actor.actor_user_id
            )
        await self._append_admin_audit(
            session,
            actor=actor,
            action="user.disable" if status == "disabled" else "user.enable",
            account_id=target.account_id,
            subject_user_id=target.id,
            target_type="iam_users",
            target_id=str(target.id),
            metadata={"sessions_revoked": sessions_revoked, "keys_revoked": keys_revoked},
            request_id=request_id,
        )
        return UserStatusResult(
            user=target, sessions_revoked=sessions_revoked, keys_revoked=keys_revoked, changed=True
        )

    # ── 分级密码重置（服务层复用 AuthService，本层补路径归属校验）──

    async def reset_user_password(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        target_user_id: uuid.UUID,
        scope_account_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> PasswordResetResult:
        """分级密码重置（03 §8.3：`actor_role_rank > target_role_rank`）。

        - 平台路径（scope_account_id 指定）：目标必须属于路径 Account（404），
          Actor 平台级（禁目标 PSA：PSA 不在任何 Account → 404）；
        - admin 路径（scope_account_id=None）：AuthService 以 Actor Account
          校验同 Account（404）与 rank（同级 403）。
        - 成功撤销目标全部登录 Session、不撤 API Key（04 §10.8 审计复用 AuthService）。
        """
        if scope_account_id is not None:
            await self._load_scoped_user(session, scope_account_id, target_user_id)
        return await self._auth.reset_user_password(
            session,
            actor_user_id=actor.actor_user_id,
            actor_account_id=None if scope_account_id is not None else actor.actor_account_id,
            target_user_id=target_user_id,
            request_id=request_id,
        )

    # ── 平台级提升：PUT .../role 仅 user→account_admin（05 §12.6 / 04 §10.6）──

    async def promote_user(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        target_user_id: uuid.UUID,
        account_id: uuid.UUID,
        request_id: str | None = None,
    ) -> UserRow:
        """平台级角色提升（仅 `user → account_admin`，AC ④）。

        - 目标必须属于路径 Account（跨 Account/PSA → 404）；
        - 目标当前角色必须恰为 `user`，否则 403 `ROLE_ASSIGNMENT_ONLY_FROM_USER`；
        - 提升不轮换目标 Session：RbacService 递增 `permission_version` + 缓存失效，
          下一次请求即时生效（免重登，spike ==11）；
        - 成功写一条 role.assign 审计（Actor=PSA、Subject=目标 User）。
        """
        target, link = await self._load_scoped_user(session, account_id, target_user_id)
        user_role = await self._repo.get_role_by_code(session, USER)
        current_code = None
        if link is not None and user_role is not None and link.role_id == user_role.id:
            current_code = USER
        if current_code != USER:
            await self._append_admin_audit(
                session,
                actor=actor,
                action="role.assign",
                account_id=target.account_id,
                subject_user_id=target.id,
                target_type="iam_user_roles",
                target_id=str(target.id),
                result="denied",
                reason="ROLE_ASSIGNMENT_ONLY_FROM_USER",
                request_id=request_id,
            )
            raise AdminActionForbiddenError("ROLE_ASSIGNMENT_ONLY_FROM_USER")
        await self._rbac.assign_role(
            session,
            actor_user_id=actor.actor_user_id,
            actor_account_id=None,
            target_user_id=target.id,
            role_code=ACCOUNT_ADMIN,
            request_id=request_id,
        )
        await self._append_admin_audit(
            session,
            actor=actor,
            action="role.assign",
            account_id=target.account_id,
            subject_user_id=target.id,
            target_type="iam_user_roles",
            target_id=str(target.id),
            metadata={"role_from": USER, "role_to": ACCOUNT_ADMIN},
            request_id=request_id,
        )
        return UserRow(user=target, role_code=ACCOUNT_ADMIN)

    # ── 管理员查看/撤销用户 API Key 元数据（04 §10.3，05 §12.6）──

    async def list_user_api_keys(
        self,
        session: AsyncSession,
        *,
        scope_account_id: uuid.UUID | None,
        target_user_id: uuid.UUID,
    ) -> list[IamApiCredential]:
        """目标用户 API Key 元数据列表（仅名称/掩码/状态/使用时间，无明文）。
        P1-E4 未合并前直接经 E1 repository；合并后由协调者验证。"""
        await self._load_scoped_user(session, scope_account_id, target_user_id)
        return await self._repo.list_api_credentials_for_user(session, target_user_id)

    async def revoke_user_api_key(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        scope_account_id: uuid.UUID | None,
        target_user_id: uuid.UUID,
        credential_id: uuid.UUID,
        request_id: str | None = None,
    ) -> IamApiCredential:
        """撤销目标用户的具名 API Key。

        - 凭据不存在 / 不属于目标用户 / 已撤销 → 404（不写审计防枚举）；
        - 按名独立：只撤销指定 Key（spike ==9 语义）；撤销后写审计
          （Actor=管理员、Subject=Key 属主，metadata 仅 Key 名）。
        """
        target, _ = await self._load_scoped_user(session, scope_account_id, target_user_id)
        credential = await session.get(IamApiCredential, credential_id)
        if (
            credential is None
            or credential.user_id != target.id
            or credential.account_id != target.account_id
            or credential.revoked_at is not None
        ):
            raise EntityNotFoundError(f"api credential {credential_id} not visible")
        revoked = await self._repo.revoke_api_credential(
            session, credential_id, revoked_by=actor.actor_user_id
        )
        await self._append_admin_audit(
            session,
            actor=actor,
            action="credential.revoke",
            account_id=target.account_id,
            subject_user_id=target.id,
            target_type="iam_api_credentials",
            target_id=str(credential_id),
            metadata={"key_name": credential.name},
            request_id=request_id,
        )
        return revoked

    # ── 审计读取（05 §12.6 GET /admin/audit-events、GET /platform/audit-events）──

    async def list_audit_events(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID | None = None,
        limit: int = 100,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> tuple[list[IamAuditEvent], str | None]:
        """审计列表；account_id=None 表示平台范围（全部 Account）。"""
        filters = []
        if account_id is not None:
            filters.append(IamAuditEvent.account_id == account_id)
        return await self._keyset_page(
            session, IamAuditEvent, IamAuditEvent.occurred_at, filters, limit, cursor
        )

    # ── 内部工具 ──

    async def _load_scoped_user(
        self,
        session: AsyncSession,
        scope_account_id: uuid.UUID | None,
        target_user_id: uuid.UUID,
    ) -> tuple[IamUser, IamUserRole | None]:
        """加载目标 User；不存在/已删除/不属于作用域 Account → 404（防枚举）。

        无 Account 的 PSA 不可作为管理目标（`account_id IS NULL` 一律拒绝）。
        """
        user = await self._repo.get_user(session, target_user_id)
        if (
            user is None
            or user.deleted_at is not None
            or user.account_id is None
            or user.account_id != scope_account_id
        ):
            raise EntityNotFoundError(f"user {target_user_id} not found in scope")
        link = await self._repo.get_role_for_user(session, target_user_id)
        return user, link

    async def _count_active_account_admins(self, session: AsyncSession, account_id: uuid.UUID) -> int:
        """Account 内 status=active 且未删除的 account_admin 数量（AC ⑦ 基数）。"""
        active_user_ids = list(
            (
                await session.execute(
                    select(IamUser.id).where(
                        IamUser.account_id == account_id,
                        IamUser.deleted_at.is_(None),
                        IamUser.status == "active",
                    )
                )
            ).scalars()
        )
        if not active_user_ids:
            return 0
        admin_role = await self._repo.get_role_by_code(session, ACCOUNT_ADMIN)
        if admin_role is None:
            return 0
        links = list(
            (
                await session.execute(
                    select(IamUserRole.user_id).where(
                        IamUserRole.user_id.in_(active_user_ids),
                        IamUserRole.role_id == admin_role.id,
                    )
                )
            ).scalars()
        )
        return len(links)

    async def _role_codes(
        self, session: AsyncSession, users: list[IamUser]
    ) -> dict[uuid.UUID, str | None]:
        """批量读取用户角色 code（v0.1 单角色；避免 N+1）。"""
        if not users:
            return {}
        links = list(
            (
                await session.execute(
                    select(IamUserRole).where(IamUserRole.user_id.in_([u.id for u in users]))
                )
            ).scalars()
        )
        role_ids = {link.role_id for link in links}
        role_map: dict[uuid.UUID, str] = {}
        if role_ids:
            role_map = {
                r.id: r.code
                for r in (
                    await session.execute(select(IamRole).where(IamRole.id.in_(role_ids)))
                ).scalars()
            }
        return {link.user_id: role_map.get(link.role_id) for link in links}

    async def _role_code_of(self, session: AsyncSession, link: IamUserRole | None) -> str | None:
        if link is None:
            return None
        role = await session.get(IamRole, link.role_id)
        return role.code if role is not None else None

    async def _keyset_page(
        self,
        session: AsyncSession,
        model: type,
        order_col: Any,
        filters: list,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None,
    ) -> tuple[list, str | None]:
        """cursor 分页（05 §12.2）：按 order_col desc, id desc 键集分页；
        恰好取满 limit 行时返回 next_cursor（可能还有下一页）。"""
        stmt = select(model).where(*filters)
        if cursor is not None:
            ts, cid = cursor
            stmt = stmt.where(or_(order_col < ts, and_(order_col == ts, model.id < cid)))
        stmt = stmt.order_by(order_col.desc(), model.id.desc()).limit(limit + 1)
        rows = list((await session.execute(stmt)).scalars())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if rows and has_more:
            last = rows[-1]
            next_cursor = encode_cursor(getattr(last, order_col.key), last.id)
        return rows, next_cursor

    async def _append_admin_audit(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedUserPrincipal,
        action: str,
        account_id: uuid.UUID | None,
        subject_user_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        result: str = "success",
        reason: str | None = None,
        metadata: dict | None = None,
        request_id: str | None,
    ) -> None:
        """管理动作审计（04 §10.8）：Actor/Subject 分离、metadata 脱敏。"""
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=account_id,
            actor_type="user",
            actor_user_id=actor.actor_user_id,
            actor_account_id=actor.actor_account_id,
            actor_session_id=actor.session_id,
            authentication_method=actor.authentication_method,
            subject_account_id=account_id,
            subject_user_id=subject_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            scope="platform" if actor.actor_account_id is None else "account",
            result=result,
            reason=reason,
            metadata=metadata,
        )
