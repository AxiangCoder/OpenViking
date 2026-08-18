"""Principal 解析：登录 Session → AuthenticatedUserPrincipal（05 §11.1/§11.4）。

P1-E3 先交付 Session→Principal；P1-E4 将 API Key/OAuth Token 收敛进同一
Resolver（14 号计划 §96.4：复用并收敛本模块的 session→principal 构造）。

角色与权限实时从 IAM 计算（03 §8.1：不把角色/权限快照固化进长期凭证）；
权限变更下一次请求立即生效（03 §9.4 双版本缓存由 RbacService 承载）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.errors import AuthenticationError
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.iam.service import RbacService

# 稳定对外码（05 §12.2）
SESSION_EXPIRED = "SESSION_EXPIRED"
USER_DISABLED = "USER_DISABLED"
INVALID_CREDENTIAL = "INVALID_CREDENTIAL"


@dataclass(frozen=True)
class AuthenticatedUserPrincipal:
    """已认证用户主体（02 §7.1）。

    所有凭证类型（登录 Session/API Key/OAuth，05 §11.1）最终产出同一结构；
    `authentication_method` 区分来源，`session_id/credential_id` 关联具体凭证。
    """

    actor_user_id: uuid.UUID
    actor_account_id: uuid.UUID | None
    actor_ov_user_id: str | None
    actor_ov_account_id: str | None
    user_status: str
    authentication_method: str  # session | api_key（P1-E4）
    session_id: uuid.UUID | None = None
    credential_id: uuid.UUID | None = None
    role_codes: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset()
    ov_base_role: str | None = None
    role_rank: int = 0


async def resolve_session_principal(
    session: AsyncSession,
    repo: IamRepository,
    rbac: RbacService,
    raw_token: str,
) -> AuthenticatedUserPrincipal:
    """Cookie token → Principal（03 §8.1）。

    失败统一抛 AuthenticationError（401 语义）：
    - 空/非法 token、会话不存在 → INVALID_CREDENTIAL/SESSION_EXPIRED；
    - 已撤销、空闲过期、绝对过期 → SESSION_EXPIRED；
    - 用户不存在/已删除/非 active → USER_DISABLED。
    """
    if not raw_token:
        raise AuthenticationError(INVALID_CREDENTIAL)
    row = await repo.get_session_by_token_hash(session, sha256_hex(raw_token))
    if row is None or row.revoked_at is not None:
        raise AuthenticationError(SESSION_EXPIRED)
    now = datetime.now(timezone.utc)
    if row.idle_expires_at < now or row.absolute_expires_at < now:
        raise AuthenticationError(SESSION_EXPIRED)

    user = await repo.get_user(session, row.user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        raise AuthenticationError(USER_DISABLED)

    account = None
    if user.account_id is not None:
        account = await repo.get_account(session, user.account_id)
    perms = await rbac.get_user_permissions(session, user.id)
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=user.account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=account.ov_account_id if account else None,
        user_status=user.status,
        authentication_method="session",
        session_id=row.id,
        role_codes=perms.role_codes,
        permissions=perms.permissions,
        ov_base_role=perms.ov_base_role,
        role_rank=perms.rank,
    )
