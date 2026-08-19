"""add platform session tables (session_refs / session_messages / session_commits)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-19 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create P2-E5 session tables (11 §70–§72，04 §10.11 软删除同族)。

    - `platform_session_refs`：OpenViking Session 产品授权映射（归属/幂等键/
      同步状态/软删除）；Session 永远 User 私有（11 §68）；
      `(account_id, ov_session_id)` 唯一、幂等键部分唯一；
    - `platform_session_messages`：追加消息产品账本，`(session_id, seq)`
      唯一保序、`idempotency_key` 部分唯一去重（11 §70.8，AC④）；
    - `platform_session_commits`：Commit 产品记录与脱敏 Memory Diff
      （11 §71.3，Phase 2 状态 pending/running/completed/failed）。
    """
    op.create_table(
        "platform_session_refs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("ov_session_id", sa.String(length=128), nullable=False),
        sa.Column("ov_uri", sa.String(length=1024), nullable=True),
        sa.Column("client_name", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("commit_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("message_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
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
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["deleted_by"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "ov_session_id", name="uq_sessions_account_ov"),
    )
    op.create_index(
        "uq_sessions_idempotency",
        "platform_session_refs",
        ["account_id", "owner_user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_sessions_owner_status",
        "platform_session_refs",
        ["owner_user_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_sessions_purge_status",
        "platform_session_refs",
        ["purge_after", "status"],
    )

    op.create_table(
        "platform_session_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=True),
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["platform_session_refs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_messages_seq"),
    )
    op.create_index(
        "uq_session_messages_idempotency",
        "platform_session_messages",
        ["session_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_session_messages_session_seq",
        "platform_session_messages",
        ["session_id", "seq"],
    )

    op.create_table(
        "platform_session_commits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("commit_number", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("ov_task_id", sa.String(length=128), nullable=True),
        sa.Column(
            "phase2_status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("phase2_error", sa.String(length=512), nullable=True),
        sa.Column("diff_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "message_count_at_commit",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["platform_session_refs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "commit_number", name="uq_session_commits_number"),
    )
    op.create_index(
        "uq_session_commits_idempotency",
        "platform_session_commits",
        ["session_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_session_commits_status", "platform_session_commits", ["phase2_status"])
    op.create_index(
        "ix_session_commits_session",
        "platform_session_commits",
        ["session_id", "commit_number"],
    )


def downgrade() -> None:
    """Drop P2-E5 session tables."""
    op.drop_index("ix_session_commits_session", table_name="platform_session_commits")
    op.drop_index("ix_session_commits_status", table_name="platform_session_commits")
    op.drop_index("uq_session_commits_idempotency", table_name="platform_session_commits")
    op.drop_table("platform_session_commits")
    op.drop_index("ix_session_messages_session_seq", table_name="platform_session_messages")
    op.drop_index("uq_session_messages_idempotency", table_name="platform_session_messages")
    op.drop_table("platform_session_messages")
    op.drop_index("ix_sessions_purge_status", table_name="platform_session_refs")
    op.drop_index("ix_sessions_owner_status", table_name="platform_session_refs")
    op.drop_index("uq_sessions_idempotency", table_name="platform_session_refs")
    op.drop_table("platform_session_refs")
