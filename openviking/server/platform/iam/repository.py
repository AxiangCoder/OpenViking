"""IAM Repository（05 §11 `iam/repository.py`）。

Repository 负责表级事务性 CRUD 与完整性边界，业务规则（RBAC 计算、
角色授予约束、PSA 初始化等）由 service 层（P1-E2+）承载。

所有方法接收调用方传入的 AsyncSession（Unit of Work 模式），
多步操作由调用方在同一个事务内提交。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import (
    IamAccount,
    IamApiCredential,
    IamAuditEvent,
    IamPermission,
    IamRole,
    IamRolePermission,
    IamSession,
    IamUser,
    IamUserRole,
)


class IamRepository(ABC):
    """IAM 数据访问接口。"""

    # ── accounts（04 §10.1，version 乐观锁）──

    @abstractmethod
    async def create_account(
        self,
        session: AsyncSession,
        *,
        ov_account_id: str,
        code: str,
        display_name: str,
        status: str = "provisioning",
    ) -> IamAccount:
        """创建 Account。code 与 ov_account_id 各自唯一（双唯一）。"""

    @abstractmethod
    async def get_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
    ) -> IamAccount | None:
        """按内部 ID 读取（含已删除行，过滤语义由调用方处理）。"""

    @abstractmethod
    async def get_account_by_code(
        self,
        session: AsyncSession,
        code: str,
    ) -> IamAccount | None:
        """按展示/路径 code 读取。"""

    @abstractmethod
    async def get_account_by_ov_account_id(
        self,
        session: AsyncSession,
        ov_account_id: str,
    ) -> IamAccount | None:
        """按 OpenViking account_id 映射读取。"""

    @abstractmethod
    async def list_accounts(
        self,
        session: AsyncSession,
        *,
        include_deleted: bool = False,
    ) -> list[IamAccount]:
        """列表；默认排除进入回收期的 Account。"""

    @abstractmethod
    async def update_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        expected_version: int,
        **fields,
    ) -> IamAccount:
        """乐观锁更新：version 不一致抛 OptimisticLockError；成功 version+1。"""

    @abstractmethod
    async def soft_delete_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
    ) -> IamAccount:
        """进入 30 天回收期：写 deleted_at/purge_after/deleted_by（04 §10.1）。"""

    # ── users（04 §10.2）──

    @abstractmethod
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
        """创建 User。account_id 为 None 仅限 platform_super_admin 路径
        （DB 部分唯一索引 + service invariant 双重强制）。"""

    @abstractmethod
    async def get_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> IamUser | None:
        """按内部 ID 读取。"""

    @abstractmethod
    async def get_user_by_normalized_email(
        self,
        session: AsyncSession,
        normalized_email: str,
    ) -> IamUser | None:
        """按规范化邮箱读取（全局唯一）。"""

    @abstractmethod
    async def list_users_by_account(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> list[IamUser]:
        """按 Account 列表；默认排除已删除。"""

    @abstractmethod
    async def update_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        **fields,
    ) -> IamUser:
        """更新 User 字段（无乐观锁；permission_version 递增除外，见下）。"""

    @abstractmethod
    async def bump_permission_version(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> None:
        """权限缓存失效：permission_version+1（03 §9.4）。"""

    @abstractmethod
    async def soft_delete_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
    ) -> IamUser:
        """进入 30 天回收期（04 §10.2）。"""

    # ── sessions（04 §10.7）──

    @abstractmethod
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
        """创建登录 Session；token_hash 全局唯一。"""

    @abstractmethod
    async def get_session_by_token_hash(
        self,
        session: AsyncSession,
        token_hash: str,
    ) -> IamSession | None:
        """按 token hash 读取。"""

    @abstractmethod
    async def revoke_session(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
        *,
        reason: str,
    ) -> IamSession | None:
        """撤销单个 Session（幂等）。"""

    @abstractmethod
    async def revoke_all_sessions_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        reason: str,
    ) -> int:
        """批量撤销目标用户全部未撤销 Session；返回撤销条数。"""

    # ── api_credentials（04 §10.3）──

    @abstractmethod
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
        """创建 API Key 记录；public_id/key_hash 各自唯一。"""

    @abstractmethod
    async def get_api_credential_by_public_id(
        self,
        session: AsyncSession,
        public_id: str,
    ) -> IamApiCredential | None:
        """按 public_id 定位（解析器入口）。"""

    @abstractmethod
    async def list_api_credentials_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[IamApiCredential]:
        """用户 Key 列表（元数据，无明文）。"""

    @abstractmethod
    async def revoke_api_credential(
        self,
        session: AsyncSession,
        credential_id: uuid.UUID,
        *,
        revoked_by: uuid.UUID,
    ) -> IamApiCredential | None:
        """按名撤销（幂等：已撤销返回原记录）。"""

    @abstractmethod
    async def revoke_all_api_credentials_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        revoked_by: uuid.UUID,
    ) -> int:
        """用户禁用/删除期撤销全部 Key；返回撤销条数。"""

    # ── roles / permissions（04 §10.4–10.6，只读；种子归 P1-E2）──

    @abstractmethod
    async def get_role_by_code(
        self,
        session: AsyncSession,
        code: str,
    ) -> IamRole | None:
        """按角色 code 读取（如 platform_super_admin）。"""

    @abstractmethod
    async def list_roles(self, session: AsyncSession) -> list[IamRole]:
        """全部角色（v0.1 仅三内置角色）。"""

    @abstractmethod
    async def list_permissions_for_roles(
        self,
        session: AsyncSession,
        role_ids: list[uuid.UUID],
    ) -> list[IamRolePermission]:
        """角色→权限映射（有效权限计算输入，03 §9.4）。"""

    @abstractmethod
    async def list_permissions(
        self,
        session: AsyncSession,
    ) -> list[IamPermission]:
        """权限目录（03 §9.1 种子）。"""

    @abstractmethod
    async def assign_role(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        assigned_by: uuid.UUID | None = None,
    ) -> IamUserRole:
        """授予角色。单角色/Account 一致性约束由 P1-E2 service 校验。"""

    @abstractmethod
    async def get_role_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> IamUserRole | None:
        """目标用户的角色绑定（v0.1 每用户至多一条）。"""

    # ── audit（04 §10.8）──

    @abstractmethod
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
        """追加审计事件。调用方负责脱敏 metadata。"""

    @abstractmethod
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
        """审计列表（管理/平台审计 API 的基础查询）。"""
