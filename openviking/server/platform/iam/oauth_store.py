"""MCP OAuth Token 存储适配层（04 §10.13，05 §11.1，14 号计划 §97.2）。

OAuth 数据表（`iam_oauth_clients/grants/pending_authorizations/tokens`）的
PostgreSQL 落地与协议端点是 P2-E6b 的交付（Spike 风险 10）。本 Epic 只交付
**OAuth Token→Principal 解析契约**（05 §11.1 `mcp_oauth_principal`）与
存储适配接口：

- `OAuthAccessTokenRecord`：解析器需要的 Token/Grant 最小状态视图
  （不返回明文，只返回 hash 已校验后的记录）；
- `OAuthTokenStore`：协议接口；P2-E6b 提供 PG 实现，本 Epic 测试使用
  契约一致的假实现（14 号计划 §97.2："先解析 PG 中已有的 oauth token 或
  按契约实现解析器+测试用假数据"）。

授权约束（04 §10.13）：每次 OAuth Token 调用都重新加载 User、Account、
角色、Permission、Grant 和 Token 状态，生成与 API Key 相同语义的 User
Principal——`resolve_oauth_token_principal` 不缓存任何授权结果。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class OAuthAccessTokenRecord:
    """解析器视角的 Access Token 记录（04 §10.13 `iam_oauth_tokens`）。

    只包含解析所需的授权状态；明文 Token 永不进入本结构（存储层只保存
    hash，04 §10.13）。
    """

    token_id: uuid.UUID
    account_id: uuid.UUID
    user_id: uuid.UUID
    status: str  # active | revoked
    expires_at: datetime | None
    revoked_at: datetime | None
    grant_status: str | None = None  # active | revoked
    grant_revoked_at: datetime | None = None


class OAuthTokenStore(Protocol):
    """Access Token 查找协议（PG 实现在 P2-E6b 交付）。

    `token_hash` 由解析器计算（SHA-256 明文）；实现负责按 hash 定位
    `iam_oauth_tokens` 并 join Grant 状态。找不到返回 None。
    """

    async def get_active_access_token(
        self,
        session: AsyncSession,
        token_hash: str,
    ) -> OAuthAccessTokenRecord | None: ...


class OAuthTokenNotFound(Exception):
    """测试假实现的内部信号：token 不存在（解析器统一映射 INVALID_CREDENTIAL）。"""
