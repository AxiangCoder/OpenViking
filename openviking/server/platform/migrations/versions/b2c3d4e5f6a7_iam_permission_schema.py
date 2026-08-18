"""add iam_permission_schema meta table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-19 01:00:00.000000

P1-E2：全局权限 Schema 版本单行表（04 §10.5，03 §9.4）。行数据由
`RbacService.seed_catalog` 维护（幂等种子；内容变更递增 schema_version）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the single-row permission schema version table."""
    op.create_table(
        "iam_permission_schema",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.BigInteger(), nullable=False),
        sa.Column("catalog_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_iam_permission_schema_singleton"),
    )


def downgrade() -> None:
    op.drop_table("iam_permission_schema")
