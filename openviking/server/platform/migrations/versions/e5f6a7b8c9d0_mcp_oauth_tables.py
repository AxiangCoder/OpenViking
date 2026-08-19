"""add MCP OAuth tables (clients / grants / pending / tokens)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-19 14:00:00.000000

04 §10.13（P2-E6b，Spike 风险 10）：MCP OAuth 的 Client、Grant、Pending
Authorization 和 Token 数据迁入 PostgreSQL，不继续以工作目录 SQLite 作为
产品生产环境的授权事实来源。

- `iam_oauth_clients`：OAuth Public Client 注册（v0.1 固定
  token_endpoint_auth_method='none' + PKCE，04 §10.13）；
- `iam_oauth_grants`：完成授权的 Account User ↔ Client 关系
  （user_id + client_id + scope 唯一且只对 active 生效；
  id 即 `/app/profile/connections` 的连接 ID）；
- `iam_oauth_pending_authorizations`：短期 pending_id + display code +
  精确 redirect URI + PKCE challenge + state + 过期/批准状态；只能由
  OAuth 协议端点与产品授权端点访问（04 §10.13）；
- `iam_oauth_tokens`：Auth Code / Refresh / Access Token 只存 SHA-256 hash、
  类型、Grant、Token family、父子轮换关系、过期/消费/撤销状态，不保存
  可再次读取的明文（04 §10.13）；Refresh 必须轮换，重放撤销整个 family。
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
    """Create MCP OAuth tables (04 §10.13)."""
    op.create_table(
        "iam_oauth_clients",
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("client_name", sa.String(length=256), nullable=True),
        sa.Column("redirect_uris", postgresql.JSONB(), nullable=False),
        sa.Column("grant_types", postgresql.JSONB(), nullable=False),
        sa.Column("response_types", postgresql.JSONB(), nullable=False),
        sa.Column(
            "token_endpoint_auth_method",
            sa.String(length=32),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_table(
        "iam_oauth_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["iam_oauth_clients.client_id"]),
        sa.ForeignKeyConstraint(["revoked_by"], ["iam_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oauth_grants_user_status", "iam_oauth_grants", ["user_id", "status"]
    )
    op.create_index("ix_oauth_grants_account", "iam_oauth_grants", ["account_id"])
    op.create_index(
        "uq_oauth_grants_user_client_scope_active",
        "iam_oauth_grants",
        ["user_id", "client_id", "scope"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "iam_oauth_pending_authorizations",
        sa.Column("pending_id", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column(
            "redirect_uri_provided_explicitly",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("code_challenge", sa.String(length=256), nullable=False),
        sa.Column(
            "code_challenge_method",
            sa.String(length=16),
            server_default=sa.text("'S256'"),
            nullable=False,
        ),
        sa.Column("scopes", postgresql.JSONB(), nullable=True),
        sa.Column("resource", sa.String(length=1024), nullable=True),
        sa.Column("state", sa.String(length=1024), nullable=True),
        sa.Column("display_code", sa.String(length=32), nullable=False),
        sa.Column(
            "verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("verified_account_id", sa.Uuid(), nullable=True),
        sa.Column("verified_user_id", sa.Uuid(), nullable=True),
        sa.Column("verified_role", sa.String(length=32), nullable=True),
        sa.Column("verified_key_fp", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["iam_oauth_clients.client_id"]),
        sa.PrimaryKeyConstraint("pending_id"),
    )
    op.create_index(
        "ix_oauth_pending_expires", "iam_oauth_pending_authorizations", ["expires_at"]
    )
    op.create_index(
        "ix_oauth_pending_display_code",
        "iam_oauth_pending_authorizations",
        ["display_code"],
    )
    op.create_table(
        "iam_oauth_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_type", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("token_family_id", sa.Uuid(), nullable=True),
        sa.Column("parent_token_id", sa.Uuid(), nullable=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=True),
        sa.Column("resource", sa.String(length=1024), nullable=True),
        sa.Column("authorizing_key_fp", sa.String(length=64), nullable=True),
        # auth_code 专用（provider load_authorization_code 需要 PKCE challenge
        # 与精确 redirect URI；refresh/access 行为 NULL）
        sa.Column("redirect_uri", sa.String(length=2048), nullable=True),
        sa.Column("code_challenge", sa.String(length=256), nullable=True),
        sa.Column("code_challenge_method", sa.String(length=16), nullable=True),
        sa.Column("replaced_by_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["grant_id"], ["iam_oauth_grants.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["iam_oauth_clients.client_id"]),
        sa.ForeignKeyConstraint(["account_id"], ["iam_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["iam_users.id"]),
        sa.ForeignKeyConstraint(["parent_token_id"], ["iam_oauth_tokens.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oauth_tokens_hash", "iam_oauth_tokens", ["token_hash"], unique=True
    )
    op.create_index("ix_oauth_tokens_grant", "iam_oauth_tokens", ["grant_id"])
    op.create_index("ix_oauth_tokens_family", "iam_oauth_tokens", ["token_family_id"])
    op.create_index(
        "ix_oauth_tokens_user", "iam_oauth_tokens", ["user_id", "token_type", "status"]
    )
    op.create_index("ix_oauth_tokens_expires", "iam_oauth_tokens", ["expires_at"])


def downgrade() -> None:
    """Drop MCP OAuth tables (04 §10.13)."""
    op.drop_index("ix_oauth_tokens_expires", table_name="iam_oauth_tokens")
    op.drop_index("ix_oauth_tokens_user", table_name="iam_oauth_tokens")
    op.drop_index("ix_oauth_tokens_family", table_name="iam_oauth_tokens")
    op.drop_index("ix_oauth_tokens_grant", table_name="iam_oauth_tokens")
    op.drop_index("ix_oauth_tokens_hash", table_name="iam_oauth_tokens")
    op.drop_table("iam_oauth_tokens")
    op.drop_index(
        "ix_oauth_pending_display_code", table_name="iam_oauth_pending_authorizations"
    )
    op.drop_index("ix_oauth_pending_expires", table_name="iam_oauth_pending_authorizations")
    op.drop_table("iam_oauth_pending_authorizations")
    op.drop_index(
        "uq_oauth_grants_user_client_scope_active", table_name="iam_oauth_grants"
    )
    op.drop_index("ix_oauth_grants_account", table_name="iam_oauth_grants")
    op.drop_index("ix_oauth_grants_user_status", table_name="iam_oauth_grants")
    op.drop_table("iam_oauth_grants")
    op.drop_table("iam_oauth_clients")
