"""用户 API Key 服务层（05 §11.4 `auth/api_keys.py`，04 §10.3）。

承载 P1-E4 业务规则（14 号计划 §96.4）：
- Key 格式 `ovk_u.<public_id>.<secret>`：点号分段，`public_id` 为 16 字节
  base64url 非敏感定位 ID，`secret` 为 32 字节（256 bit）base64url（03 §8.4）；
- DB 只存 `public_id` + `SHA-256(secret)` + 末四位（04 §10.3），
  明文完整 Key 只在创建响应返回一次；
- 固定绑定 `user_id` 与所属 `account_id`，请求参数/Header 不能改变身份；
- Platform Super Admin 不签发平台级个人 API Key（03 §8.4，05 §12.4）；
  服务层强校验（PSA 权限集合含 `credential.create.self`，仅靠路由权限不够）；
- 撤销幂等按名独立：重复撤销不报错（API 层映射 404），不影响其他 Key；
- 签发/撤销审计直写 P1-E1 repository（04 §10.8，metadata 脱敏，
  不记录 Key 明文与完整 hash）。

校验与 Principal 构造在 `auth/principals.py`（统一 Resolver，REST 与 MCP 复用）。
"""

from __future__ import annotations

import base64
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.errors import ApiCredentialError
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import IamApiCredential, IamUser

API_KEY_PREFIX = "ovk_u"
_PUBLIC_ID_BYTES = 16
_SECRET_BYTES = 32  # ≥256 bit（03 §8.4）

PSA_API_KEY_NOT_SUPPORTED = "PSA_API_KEY_NOT_SUPPORTED"
INVALID_EXPIRATION = "INVALID_EXPIRATION"


def new_api_key_parts() -> tuple[str, str]:
    """生成 (public_id, secret)；完整 Key 由调用方拼装为 `ovk_u.<p>.<s>`。"""
    public_id = (
        base64.urlsafe_b64encode(secrets.token_bytes(_PUBLIC_ID_BYTES))
        .rstrip(b"=")
        .decode("ascii")
    )
    secret = (
        base64.urlsafe_b64encode(secrets.token_bytes(_SECRET_BYTES))
        .rstrip(b"=")
        .decode("ascii")
    )
    return public_id, secret


def format_full_key(public_id: str, secret: str) -> str:
    return f"{API_KEY_PREFIX}.{public_id}.{secret}"


@dataclass(frozen=True)
class ApiKeyCreated:
    """创建产物；`full_key` 明文只允许出现在这一次响应（05 §12.4）。"""

    record: IamApiCredential
    full_key: str


class ApiCredentialService:
    """用户 API Key 生命周期：签发、列表、按名撤销（04 §10.3，05 §11.4）。"""

    def __init__(self, repo: IamRepository) -> None:
        self._repo = repo

    async def create_key(
        self,
        session: AsyncSession,
        *,
        user: IamUser,
        name: str,
        expires_at: datetime | None = None,
        request_id: str | None = None,
    ) -> ApiKeyCreated:
        """创建具名 Key；完整明文只返回一次。

        - PSA（account_id 为空）拒绝签发（03 §8.4）；
        - 过期时间必须晚于当前时刻（到期立即拒绝，04 §10.3）；
        - 服务层不缓存权限/角色快照（Key 无 Scope，03 §8.4）。
        """
        if user.account_id is None:
            raise ApiCredentialError(PSA_API_KEY_NOT_SUPPORTED)
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            raise ApiCredentialError(INVALID_EXPIRATION)

        public_id, secret = new_api_key_parts()
        record = await self._repo.create_api_credential(
            session,
            account_id=user.account_id,
            user_id=user.id,
            name=name,
            public_id=public_id,
            key_hash=sha256_hex(secret),
            key_last_four=secret[-4:],
            created_by=user.id,
            expires_at=expires_at,
        )
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=user.account_id,
            actor_type="user",
            actor_user_id=user.id,
            actor_account_id=user.account_id,
            authentication_method="session",
            subject_user_id=user.id,
            subject_account_id=user.account_id,
            action="credential.create",
            target_type="iam_api_credentials",
            target_id=str(record.id),
            scope="self",
            result="success",
            metadata={"name": name, "key_last_four": record.key_last_four},
        )
        return ApiKeyCreated(record=record, full_key=format_full_key(public_id, secret))

    async def list_keys(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[IamApiCredential]:
        """当前用户 Key 元数据列表（无明文、无完整 hash，04 §10.3）。"""
        return await self._repo.list_api_credentials_for_user(session, user_id)

    async def revoke_key(
        self,
        session: AsyncSession,
        *,
        credential_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> bool:
        """按名撤销自己的 Key（幂等）。

        - 仅能撤销本人 Key：非本人/不存在/已撤销统一返回 False（API 层 404，
          不泄露他人凭证存在性，对齐 P1-E2 not-found 语义）；
        - 重复撤销幂等：第二次返回 False，不产生副作用（04 §10.3 撤销幂等）。
        """
        owned = {
            c.id: c
            for c in await self._repo.list_api_credentials_for_user(session, user_id)
        }
        cred = owned.get(credential_id)
        if cred is None or cred.revoked_at is not None:
            return False
        row = await self._repo.revoke_api_credential(
            session, credential_id, revoked_by=user_id
        )
        if row is None:
            return False
        row.status = "revoked"
        await self._repo.append_audit_event(
            session,
            request_id=request_id,
            account_id=row.account_id,
            actor_type="user",
            actor_user_id=user_id,
            actor_account_id=row.account_id,
            authentication_method="session",
            subject_user_id=row.user_id,
            subject_account_id=row.account_id,
            action="credential.revoke",
            target_type="iam_api_credentials",
            target_id=str(row.id),
            scope="self",
            result="success",
            metadata={"name": row.name, "key_last_four": row.key_last_four},
        )
        return True
