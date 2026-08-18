"""init platform iam tables

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-08-19 00:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the 9 IAM tables (04 §10.1–10.8).

    外键循环（iam_accounts.deleted_by ↔ iam_users.account_id）手工编排
    （Spike §4.2 #5）：iam_accounts 先建（deleted_by 不带 FK 约束），
    iam_users 建后再补 accounts.deleted_by → users.id 外键。
    """
    # 1. iam_permissions（无依赖，code 主键）
    op.create_table(
        "iam_permissions",
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    # 2. iam_accounts（04 §10.1；code 与 ov_account_id 双唯一，Spike §4.2 #8）
    op.create_table(
        "iam_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ov_account_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provisioning_error", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("ov_account_id"),
    )

    # 3. iam_users（04 §10.2；自引用 deleted_by 可内联）
    op.create_table(
        "iam_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("ov_user_id", sa.String(length=64), nullable=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("normalized_username", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "permission_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["deleted_by"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "ov_user_id", name="uq_iam_users_account_ov_user"),
        sa.UniqueConstraint(
            "account_id", "normalized_username", name="uq_iam_users_account_username"
        ),
    )
    # normalized_email 全局唯一索引（04 §10.2）
    op.create_index("uq_iam_users_normalized_email", "iam_users", ["normalized_email"], unique=True)
    # account_id IS NULL 仅允许 platform_super_admin（04 §10.2）：
    # 部分唯一索引强制至多一个无 Account 用户（NULLS NOT DISTINCT，PG15+），
    # 角色约束由 service invariant 强制。
    op.create_index(
        "uq_iam_users_psa_null_account",
        "iam_users",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NULL"),
        postgresql_nulls_not_distinct=True,
    )

    # 4. 外键循环收口：accounts.deleted_by → users.id（Spike §4.2 #5）
    op.create_foreign_key(
        "fk_iam_accounts_deleted_by_iam_users",
        "iam_accounts",
        "iam_users",
        ["deleted_by"],
        ["id"],
    )

    # 5. iam_api_credentials（04 §10.3）
    op.create_table(
        "iam_api_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_last_four", sa.String(length=4), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["revoked_by"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
        sa.UniqueConstraint("public_id"),
    )

    # 6. iam_roles（04 §10.4；rank 3/2/1 平台等级，与 OpenViking Role rank 独立）
    op.create_table(
        "iam_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ov_base_role", sa.String(length=24), nullable=True),
        sa.Column("rank", sa.BigInteger(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    # 7. iam_sessions（04 §10.7）
    op.create_table(
        "iam_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("csrf_secret_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_iam_sessions_user_revoked", "iam_sessions", ["user_id", "revoked_at"])
    op.create_index("ix_iam_sessions_absolute_expires", "iam_sessions", ["absolute_expires_at"])

    # 8. iam_role_permissions（04 §10.6，复合主键）
    op.create_table(
        "iam_role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_code", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["permission_code"], ["iam_permissions.code"]),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"]),
        sa.PrimaryKeyConstraint("role_id", "permission_code"),
    )

    # 9. iam_user_roles（04 §10.6，复合主键）
    op.create_table(
        "iam_user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assigned_by"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    # 10. iam_audit_events（04 §10.8）
    op.create_table(
        "iam_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_account_id", sa.Uuid(), nullable=True),
        sa.Column("actor_system_component", sa.String(length=64), nullable=True),
        sa.Column("actor_session_id", sa.Uuid(), nullable=True),
        sa.Column("authentication_method", sa.String(length=16), nullable=True),
        sa.Column("actor_credential_id", sa.Uuid(), nullable=True),
        sa.Column("subject_account_id", sa.Uuid(), nullable=True),
        sa.Column("subject_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("target_visibility", sa.String(length=24), nullable=True),
        sa.Column("scope", sa.String(length=24), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["actor_account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["subject_account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["subject_user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_iam_audit_events_occurred_at", "iam_audit_events", ["occurred_at"])
    op.create_index("ix_iam_audit_events_actor_user", "iam_audit_events", ["actor_user_id"])
    op.create_index("ix_iam_audit_events_subject_user", "iam_audit_events", ["subject_user_id"])
    op.create_index("ix_iam_audit_events_account", "iam_audit_events", ["account_id"])
    op.create_index("ix_iam_audit_events_request_id", "iam_audit_events", ["request_id"])


def downgrade() -> None:
    """Drop the 9 IAM tables（逆序 + 先解外键循环）。"""
    op.drop_constraint("fk_iam_accounts_deleted_by_iam_users", "iam_accounts", type_="foreignkey")
    op.drop_table("iam_audit_events")
    op.drop_table("iam_user_roles")
    op.drop_table("iam_role_permissions")
    op.drop_table("iam_sessions")
    op.drop_table("iam_roles")
    op.drop_table("iam_api_credentials")
    op.drop_table("iam_users")
    op.drop_table("iam_accounts")
    op.drop_table("iam_permissions")
