"""add iam_outbox

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-19 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create iam_outbox (04 §10.9): PostgreSQL → OpenViking Provisioning 可靠同步。

    - `aggregate_id` 指向 Account 或 User 内部 ID（04 §10.9，不设 FK）；
    - `status` pending/processing/completed/failed；attempts 递增、指数退避
      （05 §11.3）；`last_error` 仅存脱敏错误；
    - `processing_started_at` 为 Reconciler 卡死恢复的检测时间（P2-E1 实现细节，
      04 §10.9 字段表不含，仅本 Epic 使用）。
    """
    op.create_table(
        "iam_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iam_outbox_status_next_attempt", "iam_outbox", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    """Drop iam_outbox。"""
    op.drop_index("ix_iam_outbox_status_next_attempt", table_name="iam_outbox")
    op.drop_table("iam_outbox")
