"""add platform_resource_watches (P2-E3 Resource Watch config, 09 §43.2)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-19 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Resource Watch config table (09 §43.2 / 04 §10.10).

    - 每 Resource 至多一行 Watch（`resource_id` 唯一）；
    - 只存 Resource ID 与调度参数，不保存明文远程 URL；
    - `interval_minutes` 预设校验由服务层强制（09 §40.5 AC⑩）。
    """
    op.create_table(
        "platform_resource_watches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("interval_minutes", sa.BigInteger(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("processing_instruction", sa.String(length=2000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["resource_id"], ["platform_content_refs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_id", name="uq_resource_watches_resource"),
    )
    op.create_index(
        "ix_resource_watches_next_run", "platform_resource_watches", ["state", "next_run_at"]
    )
    op.create_index(
        "ix_resource_watches_account", "platform_resource_watches", ["account_id", "resource_id"]
    )


def downgrade() -> None:
    """Drop Resource Watch config table."""
    op.drop_index("ix_resource_watches_account", table_name="platform_resource_watches")
    op.drop_index("ix_resource_watches_next_run", table_name="platform_resource_watches")
    op.drop_table("platform_resource_watches")
