"""登录 Session 服务（04 §10.7，03 §8.1）。

- 不透明 token ≥256 bit，DB 仅存 SHA-256(token)（`token_hash` 唯一）；
- CSRF secret 与登录 Session 绑定，DB 仅存 SHA-256（`csrf_secret_hash`）；
- 空闲 24h / 绝对 30 天可配置（`idle_expires_at`/`absolute_expires_at`）；
- 轮换语义（03 §8.1 消解）：登录/改密轮换（rotate_session），
  角色提升不轮换（只影响下一次请求的权限计算，RbacService 不触达本模块）；
- 撤销：登出（单会话）/ logout-all / 禁用 / 密码重置（批量，repository 提供）；
- touch：每次请求顺延 `last_seen_at` 与空闲到期（get_current_principal 调用）；
- 过期 Session 物理清理：auth/worker.py（周期 Worker，14 号计划 §96.3）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import (
    constant_time_eq,
    new_csrf_secret,
    new_session_token,
    sha256_hex,
)
from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import IamSession, IamUser


@dataclass(frozen=True)
class SessionCleanupResult:
    """过期 Session 物理清理结果（可观测，14 号计划 §96.3）。"""

    removed: int  # 本次物理删除的过期 Session 数
    remaining: int  # 清理后仍存活的未过期 Session 数
    ran_at: datetime


class SessionService:
    """登录 Session 生命周期（04 §10.7）。数据访问经 P1-E1 repository。"""

    def __init__(
        self,
        repo: IamRepository,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._repo = repo
        self._config = config

    async def create_login_session(
        self,
        session: AsyncSession,
        *,
        user: IamUser,
        ip_hash: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, str, uuid.UUID]:
        """创建登录 Session；返回 (raw_token, csrf_token, session_id)。

        同步更新用户 `last_login_at`（04 §10.2）。token/csrf 仅存 SHA-256。
        """
        raw_token = new_session_token()
        csrf_secret = new_csrf_secret()
        now = datetime.now(timezone.utc)
        row = await self._repo.create_session(
            session,
            user_id=user.id,
            account_id=user.account_id,
            token_hash=sha256_hex(raw_token),
            csrf_secret_hash=sha256_hex(csrf_secret),
            last_seen_at=now,
            idle_expires_at=now + timedelta(seconds=self._config.session_idle_ttl_seconds),
            absolute_expires_at=now + timedelta(seconds=self._config.session_absolute_ttl_seconds),
            ip_hash=ip_hash,
            user_agent=(user_agent or "")[:512] or None,
        )
        await self._repo.update_user(session, user.id, last_login_at=now)
        return raw_token, csrf_secret, row.id

    async def rotate_session(
        self,
        session: AsyncSession,
        *,
        user: IamUser,
        old_session_id: uuid.UUID | None,
        ip_hash: str | None = None,
    ) -> tuple[str, str, uuid.UUID]:
        """轮换当前登录 Session（登录/改密后，03 §8.1/§8.3）。

        撤销旧会话（reason=rotated）并签发新会话。**改密调用方必须同步把
        新 raw_token 下发为 Set-Cookie**，否则已登录客户端下一次请求因旧会话
        被撤销而立即掉线（05 §12.3 回填发现，spike §4.1 #3）。

        角色提升**不**调用本方法（03 §8.1 消解后的语义）。
        """
        if old_session_id is not None:
            await self._repo.revoke_session(session, old_session_id, reason="rotated")
        return await self.create_login_session(session, user=user, ip_hash=ip_hash)

    async def touch_session(self, session: AsyncSession, session_id: uuid.UUID) -> None:
        """请求活动：顺延 `last_seen_at` 与空闲到期（repository 无此接口，
        服务层直接 SQL，沿用 P1-E2 service.py 对冻结模块的处理约定）。"""
        now = datetime.now(timezone.utc)
        await session.execute(
            update(IamSession)
            .where(IamSession.id == session_id)
            .values(
                last_seen_at=now,
                idle_expires_at=now + timedelta(seconds=self._config.session_idle_ttl_seconds),
            )
        )

    @staticmethod
    def verify_csrf_token(csrf_secret_hash: str, provided: str) -> bool:
        """常量时间校验 X-CSRF-Token（hash 比较，04 §10.7）。"""
        return constant_time_eq(csrf_secret_hash, sha256_hex(provided))
