"""Reset the spike database to a known state: drop schema, re-migrate, reseed,
recreate the first Platform Super Admin with a fixed password so verify.py is
repeatable. Spike-only; never used in production.

Usage: .venv/bin/python scripts/reset_db.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

PSA_PASSWORD = os.environ.get("OV_PSA_PASSWORD", "Spike-PSA-Pass-2026-Dev")


def run_alembic() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = AlembicConfig(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def seed_and_create_psa() -> None:
    from sqlalchemy import delete, select

    from db import session_factory
    from models import IamRole, IamUser, IamUserRole
    from permissions import seed_catalog
    from security import hash_password

    async with session_factory() as session:
        await seed_catalog(session)
    async with session_factory() as session:
        role = (
            await session.execute(select(IamRole).where(IamRole.code == "platform_super_admin"))
        ).scalar_one()
        await session.execute(delete(IamUser).where(IamUser.email == "psa@example.com"))
        user = IamUser(
            account_id=None,
            ov_user_id=None,
            username="psa",
            email="psa@example.com",
            display_name="Platform Admin",
            password_hash=hash_password(PSA_PASSWORD),
            status="active",
        )
        session.add(user)
        await session.flush()
        session.add(IamUserRole(user_id=user.id, role_id=role.id))
        await session.commit()
        print(f"reset done; PSA password: {PSA_PASSWORD}")


if __name__ == "__main__":
    run_alembic()
    asyncio.run(seed_and_create_psa())
