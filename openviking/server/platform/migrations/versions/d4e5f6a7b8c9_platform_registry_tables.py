"""add platform registry tables (content_refs / deletion_jobs / operation_refs / uploads)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-19 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create P2-E2 registry tables (04 §10.10–§10.12/§10.14).

    - `platform_content_refs`：产品稳定 ID ↔ OpenViking URI 授权映射；
      Skill 部分唯一索引 `(account_id, canonical_name)` 仅覆盖
      `object_type='skill' AND deleted_at IS NULL`（04 §10.10）；
      `(account_id, ov_uri)` 唯一；`idempotency_key` 部分唯一（Idempotency-Key 事务）；
    - `iam_deletion_jobs`：软删除/恢复/期满清理统一跟踪（04 §10.11）；
    - `platform_operation_refs`：Task/Watch ↔ 产品对象关联，generation 防旧任务
      覆盖（04 §10.12）；
    - `platform_uploads`：Upload ID 授权元数据，15 分钟/原子消费（04 §10.14）。
    """
    op.create_table(
        "platform_content_refs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("object_type", sa.String(length=24), nullable=False),
        sa.Column("visibility", sa.String(length=24), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("ov_uri", sa.String(length=1024), nullable=False),
        sa.Column("canonical_name", sa.String(length=128), nullable=True),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=True),
        sa.Column("source_display", sa.String(length=512), nullable=True),
        sa.Column("source_locator_ciphertext", sa.Text(), nullable=True),
        sa.Column("source_locator_key_version", sa.String(length=32), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("latest_operation_id", sa.Uuid(), nullable=True),
        sa.Column("last_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_by_actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'provisioning'"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "ov_uri", name="uq_content_refs_account_ov_uri"),
    )
    op.create_index(
        "ix_content_refs_account_type_vis_status",
        "platform_content_refs",
        ["account_id", "object_type", "visibility", "status"],
    )
    op.create_index(
        "ix_content_refs_owner_type_status",
        "platform_content_refs",
        ["owner_user_id", "object_type", "status"],
    )
    op.create_index(
        "uq_content_refs_skill_name_active",
        "platform_content_refs",
        ["account_id", "canonical_name"],
        unique=True,
        postgresql_where=sa.text("object_type = 'skill' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_content_refs_idempotency",
        "platform_content_refs",
        ["account_id", "object_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "iam_deletion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(length=24), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("ov_uri", sa.String(length=1024), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_by", sa.Uuid(), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["deleted_by"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["restored_by"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deletion_jobs_purge_status", "iam_deletion_jobs", ["purge_after", "status"]
    )
    op.create_index(
        "ix_deletion_jobs_type_resource", "iam_deletion_jobs", ["resource_type", "resource_id"]
    )

    op.create_table(
        "platform_operation_refs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("operation_kind", sa.String(length=16), nullable=False),
        sa.Column("operation_type", sa.String(length=64), nullable=False),
        sa.Column("ov_operation_id", sa.String(length=128), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(length=24), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("target_visibility", sa.String(length=24), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("initiated_by_actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("cancellable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=512), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "operation_kind", "ov_operation_id", name="uq_operations_account_kind_ov"
        ),
    )
    op.create_index(
        "ix_operations_target_generation",
        "platform_operation_refs",
        ["account_id", "target_type", "target_id", "generation"],
    )
    op.create_index("ix_operations_status", "platform_operation_refs", ["status"])

    op.create_table(
        "platform_uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("target_visibility", sa.String(length=24), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("object_type", sa.String(length=24), nullable=False),
        sa.Column("storage_ref", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'uploading'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_by_operation_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_uploads_expires", "platform_uploads", ["expires_at"])
    op.create_index(
        "ix_platform_uploads_account_status", "platform_uploads", ["account_id", "status"]
    )


def downgrade() -> None:
    """Drop P2-E2 registry tables（顺序：uploads → operation_refs → deletion_jobs → content_refs）。"""
    op.drop_index("ix_platform_uploads_account_status", table_name="platform_uploads")
    op.drop_index("ix_platform_uploads_expires", table_name="platform_uploads")
    op.drop_table("platform_uploads")

    op.drop_index("ix_operations_status", table_name="platform_operation_refs")
    op.drop_index("ix_operations_target_generation", table_name="platform_operation_refs")
    op.drop_table("platform_operation_refs")

    op.drop_index("ix_deletion_jobs_type_resource", table_name="iam_deletion_jobs")
    op.drop_index("ix_deletion_jobs_purge_status", table_name="iam_deletion_jobs")
    op.drop_table("iam_deletion_jobs")

    op.drop_index("uq_content_refs_idempotency", table_name="platform_content_refs")
    op.drop_index("uq_content_refs_skill_name_active", table_name="platform_content_refs")
    op.drop_index("ix_content_refs_owner_type_status", table_name="platform_content_refs")
    op.drop_index("ix_content_refs_account_type_vis_status", table_name="platform_content_refs")
    op.drop_table("platform_content_refs")
