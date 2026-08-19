"""MCP OAuth PostgreSQL 存储实现（04 §10.13，05 §11.1，14 号计划 §97.7）。

P2-E2 交付的 `OAuthTokenStore` 协议与解析器（`resolve_oauth_token_principal`）
保持冻结；本模块实现 PG 存储并接线（Spike 风险 10 闭合）：

- 四张表 `iam_oauth_clients/grants/pending_authorizations/tokens` 落地
  PostgreSQL，SQLite 不再作为产品生产环境的授权事实来源；
- 协议端点复用 `mcp.server.auth` SDK：`PostgresOAuthStore` 与 SQLite
  `OAuthStore`（openviking/server/oauth/storage.py）保持同一接口形状，
  `OpenVikingOAuthProvider`/oauth router 只换存储不重写协议；
- Refresh Token 强制轮换（原子 `active → consumed` + `replaced_by_hash`
  链接）；检测到已消费 Token 重放时撤销整个 Token family（RFC 9700 §4.14；
  本实现按 (account, user) 全量撤销，是 family 的超集，与 SQLite 行为一致）；
- 同意只接受 `authentication_method=session`（产品 authorize 端点强制）；
  用户禁用/删除期撤销全部 Grant/Token，恢复后不自动恢复（04 §10.13）。

会话语义：协议方法（provider/SDK 调用）不带 session，本类从
`session_factory` 开临时会话并自行 commit；产品端点方法（approve/deny/
grants 等）接受调用方显式 session，与审计事件共享同一事务。
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.oauth.otp import hash_secret
from openviking.server.oauth.provider import AUTH_CODE_PREFIX, MCP_SCOPE
from openviking.server.platform.errors import OAuthProtocolError
from openviking.server.platform.iam.oauth_store import (
    OAuthAccessTokenRecord,
    OAuthTokenStore,
)
from openviking.server.platform.models import (
    IamOAuthClient,
    IamOAuthGrant,
    IamOAuthPendingAuthorization,
    IamOAuthToken,
)

OAUTH_PENDING_NOT_FOUND = "OAUTH_PENDING_NOT_FOUND"
OAUTH_CLIENT_DISABLED = "OAUTH_CLIENT_DISABLED"

# 协议方法内部会话默认 TTL（与 SQLite 实现一致，provider 的 TTL 参数优先）。
_CODE_TTL_DEFAULT = 300
_PENDING_TTL_DEFAULT = 600

AuditSink = Callable[
    ...,
    Awaitable[None],
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(dt: datetime | None) -> Optional[int]:
    return int(dt.timestamp()) if dt is not None else None


def _default_audit_sink(
    session: AsyncSession,
    *,
    action: str,
    account_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    target_type: str,
    target_id: str,
    metadata: dict | None,
) -> Awaitable[None]:
    """默认审计写入（04 §10.13：创建/批准/拒绝/轮换/重放拒绝/撤销均写审计）。

    仅记录脱敏元数据，不记录 Code/Token/PKCE verifier/display code 明文。
    协议链路（SDK token 端点）没有 Request/Principal 上下文，Actor 取
    Token 归属的 (account, user)，`authentication_method="oauth"`。
    """
    from openviking.server.platform.iam.postgres_repository import PostgresIamRepository

    async def _write() -> None:
        await PostgresIamRepository().append_audit_event(
            session,
            account_id=account_id,
            actor_type="user" if user_id is not None else "system",
            actor_user_id=user_id,
            actor_account_id=account_id,
            actor_system_component=None if user_id is not None else "oauth-store",
            authentication_method="oauth" if user_id is not None else None,
            subject_account_id=account_id,
            subject_user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            scope="oauth",
            result="success",
            metadata=metadata,
        )

    return _write()


class PostgresOAuthStore(OAuthTokenStore):
    """MCP OAuth 数据 PostgreSQL 实现（04 §10.13）。

    `session_factory`：协议方法内部会话工厂（生产为 db.session_factory，
    测试为 fixture 构造的隔离 factory）；`audit_sink` 缺省写 IAM 审计。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        audit_sink: AuditSink | None = None,
        label: str = "iam_oauth_* (PostgreSQL)",
        code_ttl_seconds: int = _CODE_TTL_DEFAULT,
    ) -> None:
        self._session_factory = session_factory
        self._audit_sink = audit_sink or _default_audit_sink
        self.label = label
        self._code_ttl = code_ttl_seconds
        # 兼容 lifespan 日志（app.py 使用 getattr(store, "_db_path")）。
        self._db_path = label

    def mint_authorization_code(self) -> str:
        """与 provider 同格式的授权码（`ovac_` 前缀，协议端点一致）。"""
        return AUTH_CODE_PREFIX + secrets.token_urlsafe(40)

    @property
    def code_ttl_seconds(self) -> int:
        return self._code_ttl

    # ── 生命周期兼容（SQLite OAuthStore 接口）──

    async def initialize(self) -> None:
        """PG 连接池由 engine 管理；无需预热，no-op。"""

    async def close(self) -> None:
        """engine 为进程级单例，不在此关闭；no-op。"""

    # ── 内部会话助手 ──

    @asynccontextmanager
    async def _maybe_session(self, session: AsyncSession | None):
        if session is not None:
            yield session
            return
        async with self._session_factory() as owned:
            try:
                yield owned
                await owned.commit()
            except BaseException:
                await owned.rollback()
                raise

    # ── 协议方法内部工具 ──

    @staticmethod
    def _client_claims(client: IamOAuthClient) -> dict[str, Any]:
        return {
            "client_id": client.client_id,
            "client_secret_hash": None,  # 公开客户端永不存 secret（04 §10.13）
            "redirect_uris": list(client.redirect_uris),
            "token_endpoint_auth_method": client.token_endpoint_auth_method,
            "grant_types": list(client.grant_types),
            "response_types": list(client.response_types),
            "client_name": client.client_name,
            "scope": client.scope,
            "status": client.status,
            "created_at": _epoch(client.created_at),
        }

    @staticmethod
    def _token_claims(token: IamOAuthToken) -> dict[str, Any]:
        claims = {
            "client_id": token.client_id,
            "account_id": str(token.account_id),
            "user_id": str(token.user_id),
            "role": token.role,
            "scope": token.scope,
            "resource": token.resource,
            "authorizing_key_fp": token.authorizing_key_fp,
            "expires_at": _epoch(token.expires_at),
            "created_at": _epoch(token.created_at),
        }
        # auth_code 专用字段（provider load_authorization_code 需要）
        if token.token_type == "auth_code":
            claims["redirect_uri"] = token.redirect_uri
            claims["code_challenge"] = token.code_challenge
            claims["code_challenge_method"] = token.code_challenge_method or "S256"
        return claims

    async def _grant_for(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        client_id: str,
        scope: str,
    ) -> IamOAuthGrant:
        """定位或建立 active Grant（user_id + client_id + scope，04 §10.13）。

        active 唯一索引保证并发只出现一个 active；已撤销的同三元组在用户
        重新批准时原地复活（granted_at 更新），连接 ID 保持稳定。
        """
        stmt = select(IamOAuthGrant).where(
            IamOAuthGrant.user_id == user_id,
            IamOAuthGrant.client_id == client_id,
            IamOAuthGrant.scope == scope,
        )
        grant = (await session.execute(stmt)).scalars().first()
        if grant is not None:
            if grant.status != "active":
                grant.status = "active"
                grant.revoked_at = None
                grant.revoked_by = None
                grant.granted_at = _now()
                await session.flush()
            return grant
        grant = IamOAuthGrant(
            account_id=account_id,
            user_id=user_id,
            client_id=client_id,
            scope=scope,
            status="active",
            granted_at=_now(),
        )
        session.add(grant)
        await session.flush()
        return grant

    async def _insert_token(
        self,
        session: AsyncSession,
        *,
        token_type: str,
        token_plain: str,
        client_id: str,
        grant_id: uuid.UUID,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        scope: str | None,
        resource: str | None,
        authorizing_key_fp: str | None,
        ttl_seconds: int,
        token_family_id: uuid.UUID | None = None,
        parent_token_id: uuid.UUID | None = None,
        redirect_uri: str | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
    ) -> datetime:
        expires_at = _now() + timedelta(seconds=ttl_seconds)
        row = IamOAuthToken(
            token_type=token_type,
            token_hash=hash_secret(token_plain),
            client_id=client_id,
            grant_id=grant_id,
            token_family_id=token_family_id,
            parent_token_id=parent_token_id,
            account_id=account_id,
            user_id=user_id,
            role=role,
            scope=scope,
            resource=resource,
            authorizing_key_fp=authorizing_key_fp or None,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            status="active",
            expires_at=expires_at,
        )
        session.add(row)
        await session.flush()
        return expires_at

    async def _touch_grant_last_used(self, session: AsyncSession, grant_id: uuid.UUID) -> None:
        await session.execute(
            update(IamOAuthGrant)
            .where(IamOAuthGrant.id == grant_id)
            .values(last_used_at=_now())
        )

    async def _emit_audit(
        self,
        session: AsyncSession,
        *,
        action: str,
        account_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        target_type: str,
        target_id: str,
        metadata: dict | None = None,
    ) -> None:
        await self._audit_sink(
            session,
            action=action,
            account_id=account_id,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
        )

    # ── DCR：Client 注册（协议端点复用 SDK，只换存储）──

    async def register_client(
        self,
        *,
        client_id: Optional[str] = None,
        redirect_uris: list[str],
        client_name: Optional[str] = None,
        token_endpoint_auth_method: str = "none",
        grant_types: Optional[list[str]] = None,
        response_types: Optional[list[str]] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        if not redirect_uris:
            raise ValueError("redirect_uris must be non-empty")
        if client_id is None:
            client_id = secrets.token_urlsafe(16)
        async with self._maybe_session(session) as s:
            client = IamOAuthClient(
                client_id=client_id,
                client_name=client_name,
                redirect_uris=list(redirect_uris),
                token_endpoint_auth_method=token_endpoint_auth_method,
                grant_types=grant_types or ["authorization_code", "refresh_token"],
                response_types=response_types or ["code"],
                scope=scope,
                status="active",
            )
            s.add(client)
            await s.flush()
            return self._client_claims(client)

    async def get_client(
        self,
        client_id: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        async with self._maybe_session(session) as s:
            client = (
                await s.execute(
                    select(IamOAuthClient).where(IamOAuthClient.client_id == client_id)
                )
            ).scalar_one_or_none()
            return self._client_claims(client) if client is not None else None

    # ── Auth code 生命周期 ──

    async def insert_auth_code(
        self,
        *,
        code_plain: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: Optional[str],
        resource: Optional[str],
        account_id: str,
        user_id: str,
        role: str,
        authorizing_key_fp: str,
        ttl_seconds: int,
        session: AsyncSession | None = None,
    ) -> int:
        async with self._maybe_session(session) as s:
            grant = await self._grant_for(
                s,
                account_id=uuid.UUID(account_id),
                user_id=uuid.UUID(user_id),
                client_id=client_id,
                scope=scope or MCP_SCOPE,
            )
            expires_at = await self._insert_token(
                s,
                token_type="auth_code",
                token_plain=code_plain,
                client_id=client_id,
                grant_id=grant.id,
                account_id=grant.account_id,
                user_id=grant.user_id,
                role=role,
                scope=grant.scope,
                resource=resource,
                authorizing_key_fp=authorizing_key_fp,
                ttl_seconds=ttl_seconds,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
            )
            return int(expires_at.timestamp())

    async def peek_auth_code(
        self,
        code_plain: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        async with self._maybe_session(session) as s:
            token = (
                await s.execute(
                    select(IamOAuthToken).where(
                        IamOAuthToken.token_hash == hash_secret(code_plain),
                        IamOAuthToken.token_type == "auth_code",
                        IamOAuthToken.status == "active",
                        IamOAuthToken.expires_at > _now(),
                    )
                )
            ).scalar_one_or_none()
            return self._token_claims(token) if token is not None else None

    async def consume_auth_code(
        self,
        code_plain: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        async with self._maybe_session(session) as s:
            now = _now()
            result = await s.execute(
                update(IamOAuthToken)
                .where(
                    IamOAuthToken.token_hash == hash_secret(code_plain),
                    IamOAuthToken.token_type == "auth_code",
                    IamOAuthToken.status == "active",
                    IamOAuthToken.expires_at > now,
                )
                .values(status="consumed", consumed_at=now)
                .returning(IamOAuthToken)
            )
            token = result.scalar_one_or_none()
            return self._token_claims(token) if token is not None else None

    # ── Refresh token：强制轮换 + family 重放撤销 ──

    async def insert_refresh(
        self,
        *,
        token_plain: str,
        client_id: str,
        account_id: str,
        user_id: str,
        role: str,
        scope: Optional[str],
        resource: Optional[str],
        authorizing_key_fp: str,
        ttl_seconds: int,
        session: AsyncSession | None = None,
    ) -> int:
        async with self._maybe_session(session) as s:
            grant = await self._grant_for(
                s,
                account_id=uuid.UUID(account_id),
                user_id=uuid.UUID(user_id),
                client_id=client_id,
                scope=scope or MCP_SCOPE,
            )
            # 轮换链：新 Refresh 是被 consume_refresh(replaced_by_plain=新 token)
            # 消费的旧 token 的子代——由 replaced_by_hash 确定性定位父代，
            # 继承 family 并记录父子轮换关系（04 §10.13）。
            parent = (
                await s.execute(
                    select(IamOAuthToken)
                    .where(
                        IamOAuthToken.token_type == "refresh",
                        IamOAuthToken.status == "consumed",
                        IamOAuthToken.replaced_by_hash == hash_secret(token_plain),
                    )
                    .order_by(IamOAuthToken.consumed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if parent is not None:
                family_id = parent.token_family_id or parent.id
                parent_id = parent.id
            else:
                family_id = uuid.uuid4()
                parent_id = None
            expires_at = await self._insert_token(
                s,
                token_type="refresh",
                token_plain=token_plain,
                client_id=client_id,
                grant_id=grant.id,
                account_id=grant.account_id,
                user_id=grant.user_id,
                role=role,
                scope=grant.scope,
                resource=resource,
                authorizing_key_fp=authorizing_key_fp,
                ttl_seconds=ttl_seconds,
                token_family_id=family_id,
                parent_token_id=parent_id,
            )
            return int(expires_at.timestamp())

    async def peek_refresh(
        self,
        token_plain: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        """非破坏性查找（`load_refresh_token` 用）。

        与 SQLite 不同：**已消费但未过期的 Refresh 也返回**——SDK token
        端点据此进入 `exchange_refresh_token`，触发「已消费 Token 重放 →
        撤销整个 Token family」（04 §10.13，RFC 9700 §4.14）。SQLite 在
        peek 阶段就过滤掉 consumed 行，导致严格重放只返回 invalid_grant
        而不撤销 family，正是本 Epic 修正的缺口。
        """
        async with self._maybe_session(session) as s:
            token = (
                await s.execute(
                    select(IamOAuthToken).where(
                        IamOAuthToken.token_hash == hash_secret(token_plain),
                        IamOAuthToken.token_type == "refresh",
                        IamOAuthToken.status.in_(("active", "consumed")),
                        IamOAuthToken.expires_at > _now(),
                    )
                )
            ).scalar_one_or_none()
            return self._token_claims(token) if token is not None else None

    async def consume_refresh(
        self,
        *,
        token_plain: str,
        replaced_by_plain: Optional[str],
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        """原子轮换：`active → consumed` 并记录 `replaced_by_hash`。

        返回原 token 的声明；未知/过期/已消费返回 None（调用方按
        RFC 9700 §4.14 对已知已消费 token 做 family 重放撤销）。
        """
        async with self._maybe_session(session) as s:
            now = _now()
            result = await s.execute(
                update(IamOAuthToken)
                .where(
                    IamOAuthToken.token_hash == hash_secret(token_plain),
                    IamOAuthToken.token_type == "refresh",
                    IamOAuthToken.status == "active",
                    IamOAuthToken.expires_at > now,
                )
                .values(
                    status="consumed",
                    consumed_at=now,
                    replaced_by_hash=(
                        hash_secret(replaced_by_plain) if replaced_by_plain else None
                    ),
                )
                .returning(IamOAuthToken)
            )
            token = result.scalar_one_or_none()
            return self._token_claims(token) if token is not None else None

    async def is_refresh_known_but_consumed(
        self,
        token_plain: str,
        session: AsyncSession | None = None,
    ) -> bool:
        async with self._maybe_session(session) as s:
            row = (
                await s.execute(
                    select(IamOAuthToken.id).where(
                        IamOAuthToken.token_hash == hash_secret(token_plain),
                        IamOAuthToken.token_type == "refresh",
                        IamOAuthToken.status == "consumed",
                    )
                )
            ).first()
            return row is not None

    async def _revoke_families_of_user(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
    ) -> int:
        """重放响应：撤销该用户全部 Token family（超集包含重放链）。

        family 撤销范围 = 用户全部 refresh family + 其下 access/auth_code
        token（防止在途交换完成）；Grant 保持 active，客户端重走授权即可。
        """
        now = _now()
        families = (
            await session.execute(
                select(IamOAuthToken.token_family_id)
                .where(
                    IamOAuthToken.account_id == account_id,
                    IamOAuthToken.user_id == user_id,
                    IamOAuthToken.token_family_id.is_not(None),
                )
                .distinct()
            )
        ).scalars().all()
        family_ids = [f for f in families if f is not None]
        revoked = 0
        if family_ids:
            result = await session.execute(
                update(IamOAuthToken)
                .where(
                    IamOAuthToken.token_family_id.in_(family_ids),
                    IamOAuthToken.status != "revoked",
                )
                .values(status="revoked", revoked_at=now)
            )
            revoked = result.rowcount or 0
        # 无 family 的孤儿 token（并发竞态/异常链）一并撤销，保持 fail-closed。
        orphan = await session.execute(
            update(IamOAuthToken)
            .where(
                IamOAuthToken.account_id == account_id,
                IamOAuthToken.user_id == user_id,
                IamOAuthToken.token_family_id.is_(None),
                IamOAuthToken.status != "revoked",
            )
            .values(status="revoked", revoked_at=now)
        )
        revoked += orphan.rowcount or 0
        if revoked:
            await self._emit_audit(
                session,
                action=action,
                account_id=account_id,
                user_id=user_id,
                target_type="iam_oauth_tokens",
                target_id=str(user_id),
                metadata={"tokens_revoked": revoked},
            )
        return revoked

    async def revoke_user_tokens(
        self,
        *,
        account_id: str,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> dict[str, int]:
        """Refresh 重放检测后的全量撤销（provider `exchange_refresh_token` 调用）。

        兼容 SQLite 返回形态；counts 语义：codes/access/refresh 撤销条数。
        """
        async with self._maybe_session(session) as s:
            now = _now()
            codes = await s.execute(
                update(IamOAuthToken)
                .where(
                    IamOAuthToken.account_id == uuid.UUID(account_id),
                    IamOAuthToken.user_id == uuid.UUID(user_id),
                    IamOAuthToken.token_type == "auth_code",
                    IamOAuthToken.status != "consumed",
                )
                .values(status="consumed", consumed_at=now)
            )
            revoked = await self._revoke_families_of_user(
                s,
                account_id=uuid.UUID(account_id),
                user_id=uuid.UUID(user_id),
                action="oauth.token.replay_revoke",
            )
            return {
                "codes_revoked": codes.rowcount or 0,
                "refresh_tokens_revoked": revoked,
                "access_tokens_revoked": revoked,
            }

    # ── Access token ──

    async def insert_access(
        self,
        *,
        token_plain: str,
        client_id: str,
        account_id: str,
        user_id: str,
        role: str,
        scope: Optional[str],
        resource: Optional[str],
        authorizing_key_fp: str,
        ttl_seconds: int,
        session: AsyncSession | None = None,
    ) -> int:
        async with self._maybe_session(session) as s:
            grant = await self._grant_for(
                s,
                account_id=uuid.UUID(account_id),
                user_id=uuid.UUID(user_id),
                client_id=client_id,
                scope=scope or MCP_SCOPE,
            )
            expires_at = await self._insert_token(
                s,
                token_type="access",
                token_plain=token_plain,
                client_id=client_id,
                grant_id=grant.id,
                account_id=grant.account_id,
                user_id=grant.user_id,
                role=role,
                scope=grant.scope,
                resource=resource,
                authorizing_key_fp=authorizing_key_fp,
                ttl_seconds=ttl_seconds,
            )
            return int(expires_at.timestamp())

    async def load_access(
        self,
        token_plain: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        async with self._maybe_session(session) as s:
            token = (
                await s.execute(
                    select(IamOAuthToken).where(
                        IamOAuthToken.token_hash == hash_secret(token_plain),
                        IamOAuthToken.token_type == "access",
                        IamOAuthToken.status == "active",
                        IamOAuthToken.expires_at > _now(),
                    )
                )
            ).scalar_one_or_none()
            if token is None:
                return None
            await self._touch_grant_last_used(s, token.grant_id)
            return self._token_claims(token)

    async def revoke_access(
        self,
        token_plain: str,
        session: AsyncSession | None = None,
    ) -> bool:
        async with self._maybe_session(session) as s:
            result = await s.execute(
                update(IamOAuthToken)
                .where(
                    IamOAuthToken.token_hash == hash_secret(token_plain),
                    IamOAuthToken.token_type == "access",
                    IamOAuthToken.status == "active",
                )
                .values(status="revoked", revoked_at=_now())
            )
            return (result.rowcount or 0) > 0

    # ── P2-E2 协议：Access Token → Principal（05 §11.1）──

    async def get_active_access_token(
        self,
        session: AsyncSession,
        token_hash: str,
    ) -> OAuthAccessTokenRecord | None:
        """按 hash 定位 access token 并 join Grant 状态（OAuthTokenStore 协议）。

        不判断状态/过期/撤销——解析器统一映射（04 §10.13：每次调用重载
        User/Account/角色/Permission/Grant/Token，不缓存授权结果）。
        """
        row = (
            await session.execute(
                select(IamOAuthToken, IamOAuthGrant)
                .join(IamOAuthGrant, IamOAuthToken.grant_id == IamOAuthGrant.id)
                .where(
                    IamOAuthToken.token_hash == token_hash,
                    IamOAuthToken.token_type == "access",
                )
            )
        ).first()
        if row is None:
            return None
        token, grant = row
        await self._touch_grant_last_used(session, grant.id)
        return OAuthAccessTokenRecord(
            token_id=token.id,
            account_id=token.account_id,
            user_id=token.user_id,
            status=token.status,
            expires_at=token.expires_at,
            revoked_at=token.revoked_at,
            grant_status=grant.status,
            grant_revoked_at=grant.revoked_at,
        )

    # ── Pending authorizations（协议 + 产品共用）──

    async def create_pending_authorization(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        redirect_uri_provided_explicitly: bool,
        code_challenge: str,
        scopes: Optional[list[str]],
        resource: Optional[str],
        state: Optional[str],
        display_code: str,
        ttl_seconds: int = _PENDING_TTL_DEFAULT,
        session: AsyncSession | None = None,
    ) -> str:
        async with self._maybe_session(session) as s:
            pending_id = secrets.token_urlsafe(16)
            s.add(
                IamOAuthPendingAuthorization(
                    pending_id=pending_id,
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
                    code_challenge=code_challenge,
                    code_challenge_method="S256",
                    scopes=list(scopes) if scopes else None,
                    resource=resource,
                    state=state,
                    # 与 find_pending_by_display_code 的规范化（strip+upper）对齐，
                    # 保证跨设备手输大小写不敏感一致。
                    display_code=display_code.strip().upper(),
                    verified=False,
                    expires_at=_now() + timedelta(seconds=ttl_seconds),
                )
            )
            await s.flush()
            return pending_id

    @staticmethod
    def _pending_claims(pending: IamOAuthPendingAuthorization) -> dict[str, Any]:
        return {
            "pending_id": pending.pending_id,
            "client_id": pending.client_id,
            "redirect_uri": pending.redirect_uri,
            "redirect_uri_provided_explicitly": pending.redirect_uri_provided_explicitly,
            "code_challenge": pending.code_challenge,
            "code_challenge_method": pending.code_challenge_method,
            "scopes": list(pending.scopes) if pending.scopes else None,
            "resource": pending.resource,
            "state": pending.state,
            "display_code": pending.display_code,
            "verified": bool(pending.verified),
            "verified_account_id": (
                str(pending.verified_account_id) if pending.verified_account_id else None
            ),
            "verified_user_id": str(pending.verified_user_id) if pending.verified_user_id else None,
            "verified_role": pending.verified_role,
            "verified_key_fp": pending.verified_key_fp,
            "expires_at": _epoch(pending.expires_at),
            "created_at": _epoch(pending.created_at),
        }

    async def load_pending_authorization(
        self,
        pending_id: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        async with self._maybe_session(session) as s:
            pending = (
                await s.execute(
                    select(IamOAuthPendingAuthorization).where(
                        IamOAuthPendingAuthorization.pending_id == pending_id,
                        IamOAuthPendingAuthorization.expires_at > _now(),
                    )
                )
            ).scalar_one_or_none()
            return self._pending_claims(pending) if pending is not None else None

    async def find_pending_by_display_code(
        self,
        display_code: str,
        session: AsyncSession | None = None,
    ) -> Optional[dict[str, Any]]:
        if not display_code:
            return None
        normalized = display_code.strip().upper()
        async with self._maybe_session(session) as s:
            pending = (
                await s.execute(
                    select(IamOAuthPendingAuthorization).where(
                        IamOAuthPendingAuthorization.display_code == normalized,
                        IamOAuthPendingAuthorization.expires_at > _now(),
                        IamOAuthPendingAuthorization.verified.is_(False),
                    )
                )
            ).scalars().first()
            return self._pending_claims(pending) if pending is not None else None

    async def mark_pending_verified(
        self,
        *,
        pending_id: str,
        account_id: str,
        user_id: str,
        role: str,
        verified_key_fp: str,
        session: AsyncSession | None = None,
    ) -> bool:
        async with self._maybe_session(session) as s:
            result = await s.execute(
                update(IamOAuthPendingAuthorization)
                .where(
                    IamOAuthPendingAuthorization.pending_id == pending_id,
                    IamOAuthPendingAuthorization.verified.is_(False),
                    IamOAuthPendingAuthorization.expires_at > _now(),
                )
                .values(
                    verified=True,
                    verified_account_id=uuid.UUID(account_id),
                    verified_user_id=uuid.UUID(user_id),
                    verified_role=role,
                    verified_key_fp=verified_key_fp,
                )
            )
            return (result.rowcount or 0) > 0

    async def delete_pending_authorization(
        self,
        pending_id: str,
        session: AsyncSession | None = None,
    ) -> None:
        async with self._maybe_session(session) as s:
            await s.execute(
                sa_delete(IamOAuthPendingAuthorization).where(
                    IamOAuthPendingAuthorization.pending_id == pending_id
                )
            )

    # ── 产品端点（05 §12.4；调用方提供 session，与审计同一事务）──

    async def get_pending_info(
        self,
        session: AsyncSession,
        pending_id: str,
    ) -> dict[str, Any]:
        """`GET integrations/mcp/oauth/pending/{pending_id}` 公开安全元数据。

        只返回 Client 名称/ID/回调 host/Scope 等公开信息，绝不返回
        display code 或完整 redirect URI（06 §13.8 防仿冒）。
        """
        pending = (
            await session.execute(
                select(IamOAuthPendingAuthorization).where(
                    IamOAuthPendingAuthorization.pending_id == pending_id,
                    IamOAuthPendingAuthorization.expires_at > _now(),
                    IamOAuthPendingAuthorization.verified.is_(False),
                )
            )
        ).scalar_one_or_none()
        if pending is None:
            raise OAuthProtocolError(OAUTH_PENDING_NOT_FOUND)
        client = (
            await session.execute(
                select(IamOAuthClient).where(IamOAuthClient.client_id == pending.client_id)
            )
        ).scalar_one_or_none()
        if client is None or client.status != "active":
            raise OAuthProtocolError(OAUTH_CLIENT_DISABLED)
        redirect_host = urlparse(pending.redirect_uri).netloc or None
        return {
            "pending_id": pending.pending_id,
            "client_id": client.client_id,
            "client_name": client.client_name,
            "redirect_uri_host": redirect_host,
            "scopes": list(pending.scopes) if pending.scopes else [],
            "expires_at": pending.expires_at,
            "redirect_uri": pending.redirect_uri,
            "state": pending.state,
        }

    async def approve_pending(
        self,
        session: AsyncSession,
        *,
        pending_id: str,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        role: str,
        auth_code: str,
        auth_code_ttl_seconds: int = _CODE_TTL_DEFAULT,
    ) -> dict[str, Any]:
        """产品授权（05 §12.4 authorize approve）：批准 → Grant + Auth Code。

        一次性（`pending_id` 过期/已处理 → OAUTH_PENDING_NOT_FOUND）；
        Grant 按 user_id + client_id + scope 建立/复用；Auth Code 与
        pending 消费在同一事务完成，防止重复批准双发 Code。
        """
        pending = (
            await session.execute(
                select(IamOAuthPendingAuthorization).where(
                    IamOAuthPendingAuthorization.pending_id == pending_id,
                    IamOAuthPendingAuthorization.expires_at > _now(),
                    IamOAuthPendingAuthorization.verified.is_(False),
                )
            )
        ).scalar_one_or_none()
        if pending is None:
            raise OAuthProtocolError(OAUTH_PENDING_NOT_FOUND)
        client = (
            await session.execute(
                select(IamOAuthClient).where(IamOAuthClient.client_id == pending.client_id)
            )
        ).scalar_one_or_none()
        if client is None or client.status != "active":
            raise OAuthProtocolError(OAUTH_CLIENT_DISABLED)
        scope = " ".join(pending.scopes) if pending.scopes else (client.scope or MCP_SCOPE)
        grant = await self._grant_for(
            session,
            account_id=account_id,
            user_id=user_id,
            client_id=pending.client_id,
            scope=scope,
        )
        expires_at = await self._insert_token(
            session,
            token_type="auth_code",
            token_plain=auth_code,
            client_id=pending.client_id,
            grant_id=grant.id,
            account_id=grant.account_id,
            user_id=grant.user_id,
            role=role,
            scope=grant.scope,
            resource=pending.resource,
            authorizing_key_fp=None,
            ttl_seconds=auth_code_ttl_seconds,
            redirect_uri=pending.redirect_uri,
            code_challenge=pending.code_challenge,
            code_challenge_method=pending.code_challenge_method,
        )
        await session.execute(
            sa_delete(IamOAuthPendingAuthorization).where(
                IamOAuthPendingAuthorization.pending_id == pending_id
            )
        )
        # flush：pending 删除对同事务内的后续读取立即可见（防重复批准双发 Code）。
        await session.flush()
        redirect_host = urlparse(pending.redirect_uri).netloc or None
        return {
            "grant_id": str(grant.id),
            "client_id": pending.client_id,
            "client_name": client.client_name,
            "redirect_uri": pending.redirect_uri,
            "redirect_uri_host": redirect_host,
            "state": pending.state,
            "scopes": list(pending.scopes) if pending.scopes else [],
            "auth_code": auth_code,
            "auth_code_expires_at": expires_at,
        }

    async def deny_pending(
        self,
        session: AsyncSession,
        pending_id: str,
    ) -> bool:
        """产品授权拒绝：删除 pending（一次性；未知/已处理 → False）。"""
        result = await session.execute(
            sa_delete(IamOAuthPendingAuthorization).where(
                IamOAuthPendingAuthorization.pending_id == pending_id,
                IamOAuthPendingAuthorization.expires_at > _now(),
                IamOAuthPendingAuthorization.verified.is_(False),
            )
        )
        return (result.rowcount or 0) > 0

    async def list_grants_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """`GET me/oauth-grants`：当前用户已授权客户端（含 Client 名称）。"""
        rows = (
            await session.execute(
                select(IamOAuthGrant, IamOAuthClient)
                .join(IamOAuthClient, IamOAuthGrant.client_id == IamOAuthClient.client_id)
                .where(IamOAuthGrant.user_id == user_id)
                .order_by(IamOAuthGrant.granted_at.desc())
            )
        ).all()
        return [
            {
                "id": str(grant.id),
                "client_id": grant.client_id,
                "client_name": client.client_name,
                "scope": grant.scope,
                "status": grant.status,
                "created_at": grant.granted_at.isoformat() if grant.granted_at else None,
                "last_used_at": (
                    grant.last_used_at.isoformat() if grant.last_used_at else None
                ),
            }
            for grant, client in rows
        ]

    async def revoke_grant(
        self,
        session: AsyncSession,
        *,
        grant_id: uuid.UUID,
        user_id: uuid.UUID,
        revoked_by: uuid.UUID,
    ) -> dict[str, Any] | None:
        """`DELETE me/oauth-grants/{id}`：撤销 Grant 及其全部 Token family。

        幂等：已撤销返回 changed=False；非本人/不存在返回 None（404）。
        """
        grant = (
            await session.execute(
                select(IamOAuthGrant).where(
                    IamOAuthGrant.id == grant_id, IamOAuthGrant.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if grant is None:
            return None
        if grant.status == "revoked":
            return {
                "id": str(grant.id),
                "status": "revoked",
                "tokens_revoked": 0,
                "changed": False,
            }
        now = _now()
        grant.status = "revoked"
        grant.revoked_at = now
        grant.revoked_by = revoked_by
        result = await session.execute(
            update(IamOAuthToken)
            .where(IamOAuthToken.grant_id == grant.id, IamOAuthToken.status != "revoked")
            .values(status="revoked", revoked_at=now)
        )
        return {
            "id": str(grant.id),
            "status": "revoked",
            "tokens_revoked": result.rowcount or 0,
            "changed": True,
        }

    # ── 生命周期：禁用/删除期撤销全部 Grant/Token（04 §10.13）──

    async def revoke_user_oauth(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        revoked_by: uuid.UUID | None,
    ) -> dict[str, int]:
        """用户禁用/进入删除期：撤销全部 Grant/Token，恢复后不自动恢复。"""
        now = _now()
        grants = (
            await session.execute(
                update(IamOAuthGrant)
                .where(IamOAuthGrant.user_id == user_id, IamOAuthGrant.status != "revoked")
                .values(status="revoked", revoked_at=now, revoked_by=revoked_by)
            )
        ).rowcount or 0
        tokens = (
            await session.execute(
                update(IamOAuthToken)
                .where(IamOAuthToken.user_id == user_id, IamOAuthToken.status != "revoked")
                .values(status="revoked", revoked_at=now)
            )
        ).rowcount or 0
        if grants or tokens:
            await self._emit_audit(
                session,
                action="oauth.user.revoke",
                account_id=None,
                user_id=user_id,
                target_type="iam_oauth_grants",
                target_id=str(user_id),
                metadata={"grants_revoked": grants, "tokens_revoked": tokens},
            )
        return {"grants_revoked": grants, "tokens_revoked": tokens}

    async def revoke_account_oauth(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        revoked_by: uuid.UUID | None,
    ) -> dict[str, int]:
        """Account 删除期：撤销 Account 内全部 Grant/Token。"""
        now = _now()
        grant_ids = (
            await session.execute(
                select(IamOAuthGrant.id).where(
                    IamOAuthGrant.account_id == account_id, IamOAuthGrant.status != "revoked"
                )
            )
        ).scalars().all()
        grants = (
            await session.execute(
                update(IamOAuthGrant)
                .where(IamOAuthGrant.account_id == account_id, IamOAuthGrant.status != "revoked")
                .values(status="revoked", revoked_at=now, revoked_by=revoked_by)
            )
        ).rowcount or 0
        tokens = 0
        if grant_ids:
            tokens = (
                await session.execute(
                    update(IamOAuthToken)
                    .where(
                        IamOAuthToken.grant_id.in_(grant_ids),
                        IamOAuthToken.status != "revoked",
                    )
                    .values(status="revoked", revoked_at=now)
                )
            ).rowcount or 0
        if grants or tokens:
            await self._emit_audit(
                session,
                action="oauth.account.revoke",
                account_id=account_id,
                user_id=None,
                target_type="iam_oauth_grants",
                target_id=str(account_id),
                metadata={"grants_revoked": grants, "tokens_revoked": tokens},
            )
        return {"grants_revoked": grants, "tokens_revoked": tokens}

    # ── Maintenance ──

    async def gc_expired(self, session: AsyncSession | None = None) -> dict[str, int]:
        """清理过期/已消费数据（SQLite 语义对齐）。

        - pending/auth_code 过期或已消费即删；
        - access 过期或已撤销即删；
        - refresh tombstone（consumed）保留到自然过期——重放检测依赖区分
          「已消费」与「从未见过」（RFC 9700 §4.14）。
        """
        async with self._maybe_session(session) as s:
            now = _now()
            pending = (
                await s.execute(
                    sa_delete(IamOAuthPendingAuthorization).where(
                        IamOAuthPendingAuthorization.expires_at < now
                    )
                )
            ).rowcount or 0
            codes = (
                await s.execute(
                    sa_delete(IamOAuthToken).where(
                        IamOAuthToken.token_type == "auth_code",
                        (IamOAuthToken.expires_at < now)
                        | (IamOAuthToken.status != "active"),
                    )
                )
            ).rowcount or 0
            access = (
                await s.execute(
                    sa_delete(IamOAuthToken).where(
                        IamOAuthToken.token_type == "access",
                        (IamOAuthToken.expires_at < now)
                        | (IamOAuthToken.status != "active"),
                    )
                )
            ).rowcount or 0
            refresh = (
                await s.execute(
                    sa_delete(IamOAuthToken).where(
                        IamOAuthToken.token_type == "refresh",
                        IamOAuthToken.expires_at < now,
                    )
                )
            ).rowcount or 0
            return {
                "codes_deleted": codes,
                "refresh_tokens_deleted": refresh,
                "access_tokens_deleted": access,
                "pending_authorizations_deleted": pending,
            }
