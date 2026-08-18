from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from models import IamApiCredential, IamUser
from security import new_api_key_secret, sha256_hex


class ApiCredentialService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_key(
        self, user: IamUser, name: str, expires_at: datetime | None = None
    ) -> tuple[IamApiCredential, str]:
        """Create a named key; full plaintext key is returned exactly once."""
        if user.account_id is None:
            raise ValueError("platform super admin keys are not supported in v0.1")
        public_id, secret = new_api_key_secret()
        row = IamApiCredential(
            account_id=user.account_id,
            user_id=user.id,
            name=name,
            public_id=public_id,
            key_hash=sha256_hex(secret),
            key_last_four=secret[-4:],
            status="active",
            expires_at=expires_at,
            created_by=user.id,
        )
        self.session.add(row)
        await self.session.flush()
        full_key = f"{config.api_key_prefix}.{public_id}.{secret}"
        return row, full_key

    async def list_keys(self, user_id: uuid.UUID) -> list[IamApiCredential]:
        rows = (
            await self.session.execute(
                select(IamApiCredential)
                .where(IamApiCredential.user_id == user_id)
                .order_by(IamApiCredential.created_at.desc())
            )
        ).scalars().all()
        return list(rows)

    async def revoke_key(self, key_id: uuid.UUID, revoked_by: uuid.UUID) -> bool:
        row = (
            await self.session.execute(select(IamApiCredential).where(IamApiCredential.id == key_id))
        ).scalar_one_or_none()
        if row is None or row.status == "revoked":
            return False
        row.status = "revoked"
        row.revoked_at = datetime.now(timezone.utc)
        row.revoked_by = revoked_by
        return True
