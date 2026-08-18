"""Principal 解析：登录 Session / 用户 API Key → AuthenticatedUserPrincipal（05 §11.1/§11.4）。

P1-E3 先交付 Session→Principal；P1-E4 将 API Key 收敛进统一 Resolver
（14 号计划 §96.4：复用并收敛 P1-E3 的 session→principal 构造）：
- `resolve_session_principal` / `resolve_api_key_principal`：单凭证解析；
- `resolve_principal`：统一入口（REST 依赖与 MCP 复用，05 §11.4），
  凭据优先级：Cookie 优先、失效不自动回退 Bearer（03 §8.4 回填发现）；
- 角色与权限实时从 IAM 计算（03 §8.1：不把角色/权限快照固化进长期凭证）；
  权限变更下一次请求立即生效（03 §9.4 双版本缓存由 RbacService 承载）。

API Key 校验（03 §8.4 / 05 §11.4）：
- 格式 `ovk_u.<public_id>.<secret>`；`public_id` 定位 → 常量时间校验
  `SHA-256(secret)` → 加载 User/Account/Role/Permission；
- 撤销、到期、用户禁用或进入删除期后立即拒绝（INVALID_CREDENTIAL/USER_DISABLED）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.api_keys import API_KEY_PREFIX
from openviking.server.platform.auth.password import constant_time_eq, sha256_hex
from openviking.server.platform.errors import AuthenticationError
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.iam.service import RbacService
from openviking.server.platform.models import IamUser

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


async def _build_user_principal(
    session: AsyncSession,
    repo: IamRepository,
    rbac: RbacService,
    *,
    user: IamUser,
    account_id: uuid.UUID | None,
    authentication_method: str,
    session_id: uuid.UUID | None = None,
    credential_id: uuid.UUID | None = None,
) -> AuthenticatedUserPrincipal:
    """User → Principal 构造（Session 与 API Key 共用，05 §11.1/§11.4）。

    实时加载 Account 映射与有效权限（RbacService，03 §9.4 双版本缓存）；
    两个凭证路径产出同一结构、同权限集，仅 `authentication_method` 与
    凭证 ID 不同（验收 ②，14 号计划 §96.4）。
    """
    account = None
    if account_id is not None:
        account = await repo.get_account(session, account_id)
    perms = await rbac.get_user_permissions(session, user.id)
    return AuthenticatedUserPrincipal(
        actor_user_id=user.id,
        actor_account_id=account_id,
        actor_ov_user_id=user.ov_user_id,
        actor_ov_account_id=account.ov_account_id if account else None,
        user_status=user.status,
        authentication_method=authentication_method,
        session_id=session_id,
        credential_id=credential_id,
        role_codes=perms.role_codes,
        permissions=perms.permissions,
        ov_base_role=perms.ov_base_role,
        role_rank=perms.rank,
    )


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

    return await _build_user_principal(
        session,
        repo,
        rbac,
        user=user,
        account_id=user.account_id,
        authentication_method="session",
        session_id=row.id,
    )


async def resolve_api_key_principal(
    session: AsyncSession,
    repo: IamRepository,
    rbac: RbacService,
    api_key: str,
) -> AuthenticatedUserPrincipal:
    """`ovk_u.<public_id>.<secret>` → Principal（03 §8.4，05 §11.4）。

    - `public_id` 定位凭证，`SHA-256(secret)` 常量时间校验；
    - 未知/撤销/到期统一 INVALID_CREDENTIAL（不泄露存在性）；
    - 用户不存在/已删除/非 active → USER_DISABLED（禁用/删除期立即拒绝）；
    - 产出与 Session 同一结构，`authentication_method="api_key"`、
      `credential_id` 关联凭证（验收 ②，14 号计划 §96.4）。
    """
    if not api_key:
        raise AuthenticationError(INVALID_CREDENTIAL)
    parts = api_key.split(".")
    if len(parts) != 3 or parts[0] != API_KEY_PREFIX:
        raise AuthenticationError(INVALID_CREDENTIAL)
    public_id, secret = parts[1], parts[2]

    cred = await repo.get_api_credential_by_public_id(session, public_id)
    if (
        cred is None
        or cred.status != "active"
        or cred.revoked_at is not None
        or not constant_time_eq(cred.key_hash, sha256_hex(secret))
    ):
        raise AuthenticationError(INVALID_CREDENTIAL)
    if cred.expires_at is not None and cred.expires_at < datetime.now(timezone.utc):
        raise AuthenticationError(INVALID_CREDENTIAL)

    user = await repo.get_user(session, cred.user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        raise AuthenticationError(USER_DISABLED)

    return await _build_user_principal(
        session,
        repo,
        rbac,
        user=user,
        account_id=cred.account_id,
        authentication_method="api_key",
        credential_id=cred.id,
    )


async def resolve_principal(
    session: AsyncSession,
    repo: IamRepository,
    rbac: RbacService,
    *,
    session_token: str | None = None,
    bearer_key: str | None = None,
    x_api_key: str | None = None,
) -> AuthenticatedUserPrincipal:
    """统一 Principal Resolver（05 §11.4：REST 依赖与 MCP 复用）。

    凭据优先级（03 §8.4 回填发现）：
    - Session Cookie 优先；Cookie 已失效**不自动回退** Bearer（防凭据混淆，
      避免一个通道的失效扩大另一个通道的信任）；
    - 无 Cookie 时 `Bearer` 与 `X-Api-Key` 进入同一解析器（03 §8.4）；
    - 均无 → INVALID_CREDENTIAL。
    """
    if session_token:
        return await resolve_session_principal(session, repo, rbac, session_token)
    api_key = bearer_key or x_api_key
    if api_key:
        return await resolve_api_key_principal(session, repo, rbac, api_key)
    raise AuthenticationError(INVALID_CREDENTIAL)
