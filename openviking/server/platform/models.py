"""Platform IAM ORM 模型（04 §10.1–10.8，共 9 张 iam_* 表）。

约束与语义：
- `iam_accounts.code` 与 `ov_account_id` 双唯一（04 §10.1 回填，Spike §4.2 #8）。
- `iam_users.normalized_email` 全局唯一索引；规范化只做 Unicode/大小写和首尾
  空白，不做邮箱服务商点号/`+tag` 折叠（04 §10.2）。
- `(account_id, normalized_username)` 唯一：normalized_username = 去除首尾空白
  后的 username（完整规范化规则由服务层 E3+ 提供）。
- `account_id IS NULL` 仅允许 platform_super_admin：DB 层以部分唯一索引
  `uq_iam_users_psa_null_account` 强制至多一个无 Account 用户，角色约束
  由 service invariant 强制（P1-E2 初始化路径）。
- `iam_roles.rank`（3/2/1）是平台等级体系，与 OpenViking `Role` 内置 rank
  （USER=0/ADMIN=1/ROOT=2，见 openviking/server/identity.py）完全独立、
  禁止混用与数值映射（04 §10.4，03 §8.3）。
- `iam_users.permission_version` 是权限缓存失效版本，不是乐观锁。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from openviking.server.platform.db import Base


class IamAccount(Base):
    """04 §10.1 `iam_accounts`。version 为乐观锁版本（04 §10.1）。"""

    __tablename__ = "iam_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ov_account_id: Mapped[str] = mapped_column(String(64), unique=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="provisioning")
    provisioning_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(BigInteger, default=1)


class IamUser(Base):
    """04 §10.2 `iam_users`。"""

    __tablename__ = "iam_users"
    __table_args__ = (
        UniqueConstraint("account_id", "ov_user_id", name="uq_iam_users_account_ov_user"),
        UniqueConstraint("account_id", "normalized_username", name="uq_iam_users_account_username"),
        Index("uq_iam_users_normalized_email", "normalized_email", unique=True),
        Index(
            "uq_iam_users_psa_null_account",
            "account_id",
            unique=True,
            postgresql_where=text("account_id IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_accounts.id"), nullable=True
    )
    ov_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username: Mapped[str] = mapped_column(String(128))
    normalized_username: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_hash: Mapped[str] = mapped_column(Text)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="provisioning")
    permission_version: Mapped[int] = mapped_column(BigInteger, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IamRole(Base):
    """04 §10.4 `iam_roles`。三内置角色全局单行（account_id 可空）。

    rank 与 OpenViking `Role` 内置 rank（USER=0/ADMIN=1/ROOT=2）独立，
    禁止混用（04 §10.4）。
    """

    __tablename__ = "iam_roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_accounts.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ov_base_role: Mapped[str | None] = mapped_column(String(24), nullable=True)
    rank: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IamPermission(Base):
    """04 §10.5 `iam_permissions`。code 为主键，由代码与 migration 注册。"""

    __tablename__ = "iam_permissions"

    code: Mapped[str] = mapped_column(String(128), primary_key=True)
    domain: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16))


class IamRolePermission(Base):
    """04 §10.6 `iam_role_permissions`，复合主键 (role_id, permission_code)。"""

    __tablename__ = "iam_role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_roles.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("iam_permissions.code"), primary_key=True
    )


class IamUserRole(Base):
    """04 §10.6 `iam_user_roles`，复合主键 (user_id, role_id)。

    v0.1 每个 User 强制单内置角色；多角色叠加由 P1-E2 service 校验，
    本表保留关联形式供权限查询。
    """

    __tablename__ = "iam_user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_users.id"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_roles.id"), primary_key=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_users.id"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IamSession(Base):
    """04 §10.7 `iam_sessions`：仅网页登录会话，不保存对话 Session。"""

    __tablename__ = "iam_sessions"
    __table_args__ = (
        Index("ix_iam_sessions_user_revoked", "user_id", "revoked_at"),
        Index("ix_iam_sessions_absolute_expires", "absolute_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_accounts.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_users.id"))
    csrf_secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


class IamApiCredential(Base):
    """04 §10.3 `iam_api_credentials`。不存 secret，仅存 SHA-256 hash。"""

    __tablename__ = "iam_api_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_accounts.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_users.id"))
    name: Mapped[str] = mapped_column(String(128))
    public_id: Mapped[str] = mapped_column(String(64), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_last_four: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(16), default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_users.id"), nullable=True)


class IamAuditEvent(Base):
    """04 §10.8 `iam_audit_events`。metadata 为 JSONB 且必须脱敏。

    Actor 与 Subject 分离：管理员访问他人数据时两者同时存在；
    内部 Worker 使用 actor_type=system + actor_system_component。
    """

    __tablename__ = "iam_audit_events"
    __table_args__ = (
        Index("ix_iam_audit_events_occurred_at", "occurred_at"),
        Index("ix_iam_audit_events_actor_user", "actor_user_id"),
        Index("ix_iam_audit_events_subject_user", "subject_user_id"),
        Index("ix_iam_audit_events_account", "account_id"),
        Index("ix_iam_audit_events_request_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_accounts.id"), nullable=True
    )
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_users.id"), nullable=True
    )
    actor_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_accounts.id"), nullable=True
    )
    actor_system_component: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    authentication_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actor_credential_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    subject_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_accounts.id"), nullable=True
    )
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(128))
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_visibility: Mapped[str | None] = mapped_column(String(24), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)


class IamOutbox(Base):
    """04 §10.9 `iam_outbox`：PostgreSQL → OpenViking Provisioning 可靠同步。

    - `aggregate_id` 指向 Account 或 User 内部 ID（04 §10.9，无 FK）；
    - `status` pending/processing/completed/failed，`attempts` 递增、
      指数退避（05 §11.3），`last_error` 仅存脱敏错误；
    - `processing_started_at` 为 Reconciler 卡死恢复的检测时间（P2-E1 实现细节）。
    """

    __tablename__ = "iam_outbox"
    __table_args__ = (
        Index("ix_iam_outbox_status_next_attempt", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[uuid.UUID] = mapped_column()
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(BigInteger, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IamPermissionSchema(Base):
    """04 §10.5 全局权限 Schema 版本（单行，`iam_permission_schema`）。

    内置角色权限种子或 migration 变更时由 `RbacService.seed_catalog` 递增
    `schema_version`（03 §9.4），参与全部权限缓存键；与用户级
    `permission_version`（iam_users.permission_version）独立。

    `catalog_fingerprint` 是当前 code 定义目录（权限 code × 内置角色权限集合）的
    SHA-256，种子用它检测内容变更——内容变更即递增版本，幂等重跑不递增。
    单行约束由 migration 的 `ck_iam_permission_schema_singleton`（id = 1）强制。
    """

    __tablename__ = "iam_permission_schema"

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    catalog_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlatformContentRef(Base):
    """04 §10.10 `platform_content_refs`：产品稳定 ID ↔ OpenViking URI 授权映射。

    OpenViking 是内容事实来源；本表是可见性、归属与审计关联的事实来源，
    不复制业务内容正文。约束（04 §10.10）：

    - `(account_id, ov_uri)` 在 Account 内唯一（Account 共享 Skill 发布
      从 User 私有根迁入共享根时保持 ID/名称不变、URI 变化）；
    - Skill 部分唯一约束：`(account_id, canonical_name)` 且
      `object_type='skill' AND deleted_at IS NULL`（覆盖全部私有+共享未删除）；
    - `visibility=user_private` 时 `owner_user_id` 非空；`account_shared` 时为空。
    """

    __tablename__ = "platform_content_refs"
    __table_args__ = (
        Index(
            "ix_content_refs_account_type_vis_status",
            "account_id",
            "object_type",
            "visibility",
            "status",
        ),
        Index("ix_content_refs_owner_type_status", "owner_user_id", "object_type", "status"),
        UniqueConstraint("account_id", "ov_uri", name="uq_content_refs_account_ov_uri"),
        Index(
            "uq_content_refs_skill_name_active",
            "account_id",
            "canonical_name",
            unique=True,
            postgresql_where=text("object_type = 'skill' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_content_refs_idempotency",
            "account_id",
            "object_type",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_accounts.id"))
    object_type: Mapped[str] = mapped_column(String(24))  # resource | skill
    visibility: Mapped[str] = mapped_column(String(24))  # user_private | account_shared
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_users.id"), nullable=True
    )
    ov_uri: Mapped[str] = mapped_column(String(1024))
    canonical_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    source_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source_display: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_locator_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_locator_key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latest_operation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by_actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    updated_by_actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="provisioning")
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IamDeletionJob(Base):
    """04 §10.11 `iam_deletion_jobs`：Account/User/Session/Resource/Skill
    统一软删除、恢复与期满清理跟踪。

    - `resource_id` 按类型引用 IAM/Content 对象稳定 ID；`ov_uri` 只供受控
      Purge Worker 使用，不替代创建任务时的可见性与 Permission 校验；
    - `status` pending/restored/purging/purged/failed；到达 `purge_after`
      后由 Purge Worker 幂等清理并置 `purged`；审计事件不随物理清理；
    - 恢复动作必须写审计（05 §12.6 注：按对象类型分别校验权限）。
    """

    __tablename__ = "iam_deletion_jobs"
    __table_args__ = (
        Index("ix_deletion_jobs_purge_status", "purge_after", "status"),
        Index("ix_deletion_jobs_type_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_accounts.id"))
    resource_type: Mapped[str] = mapped_column(String(24))  # account|user|session|resource|skill
    resource_id: Mapped[str] = mapped_column(String(128))
    ov_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_users.id"), nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    restored_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_users.id"), nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlatformOperationRef(Base):
    """04 §10.12 `platform_operation_refs`：OpenViking 异步 Task/Watch ↔
    产品对象关联（授权索引与产品状态展示，不复制运行日志/任务正文）。

    - `ov_operation_id` 与 `account_id + operation_kind` 组成唯一约束；
      `ov_operation_id` 不返回为可枚举主 ID（产品用本表 `id`）；
    - `generation` 为目标对象操作代数：旧任务完成时不得覆盖更新一代或
      删除中的对象（04 §10.10 原子切换规则）。
    """

    __tablename__ = "platform_operation_refs"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "operation_kind", "ov_operation_id", name="uq_operations_account_kind_ov"
        ),
        Index(
            "ix_operations_target_generation",
            "account_id",
            "target_type",
            "target_id",
            "generation",
        ),
        Index("ix_operations_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_accounts.id"))
    operation_kind: Mapped[str] = mapped_column(String(16))  # task | watch
    operation_type: Mapped[str] = mapped_column(String(64))
    ov_operation_id: Mapped[str] = mapped_column(String(128))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_visibility: Mapped[str] = mapped_column(String(24))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_users.id"), nullable=True
    )
    initiated_by_actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cancellable: Mapped[bool] = mapped_column(Boolean, default=False)
    generation: Mapped[int] = mapped_column(BigInteger, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformUpload(Base):
    """04 §10.14 `platform_uploads`：Product Upload ID 授权元数据。

    上传字节存临时对象存储/受控 Temp Upload Store，不写入 PostgreSQL。

    - `expires_at` 默认创建后 15 分钟（可由 Capabilities 配置）；
    - 消费使用数据库原子状态转换 `ready → consumed`（04 §10.14），
      重复请求经 `Idempotency-Key` 返回第一次结果；
    - Upload ID 不允许跨 User/Account/Visibility/Object Type 使用。
    """

    __tablename__ = "platform_uploads"
    __table_args__ = (
        Index("ix_platform_uploads_expires", "expires_at"),
        Index("ix_platform_uploads_account_status", "account_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_accounts.id"))
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_users.id"))
    target_visibility: Mapped[str] = mapped_column(String(24))  # user_private | account_shared
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("iam_users.id"), nullable=True
    )
    object_type: Mapped[str] = mapped_column(String(24))  # v0.1: resource
    storage_ref: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="uploading")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_by_operation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformResourceWatch(Base):
    """09 §43.2 `platform_resource_watches`：Resource 自动同步配置。

    - 持久化记录只保存 Resource ID 与调度参数，**不保存明文远程 URL**
      （远程来源在 `platform_content_refs.source_locator_ciphertext`，
      执行时由 Product Facade 解析并临时解密，09 §43.2/04 §10.10）；
    - `state`：active/paused/error；`not_configured` 用无记录表达；
    - `interval_minutes` 只接受 Capabilities 预设值（服务端强制，09 §40.5）；
    - `last_result`：succeeded/failed/cancelled，`last_error` 只存脱敏摘要；
    - 暂停保留周期与配置；删除 Watch 只删本行，不删除 Resource。
    """

    __tablename__ = "platform_resource_watches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_accounts.id"))
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_content_refs.id"), unique=True
    )
    state: Mapped[str] = mapped_column(String(16), default="active")
    interval_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    processing_instruction: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
