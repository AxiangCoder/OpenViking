"""One-off bootstrap: seed permission catalog + built-in roles, create the first
Platform Super Admin (design 06 §15.2). Not exposed over HTTP."""

from __future__ import annotations

import asyncio
import sys

from db import session_factory
from permissions import seed_catalog
from services.user_service import create_platform_super_admin

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "psa@example.com"
USERNAME = sys.argv[2] if len(sys.argv) > 2 else "psa"


async def main() -> None:
    async with session_factory() as session:
        await seed_catalog(session)
    async with session_factory() as session:
        created = await create_platform_super_admin(session, EMAIL, USERNAME, "Platform Admin")
        print("Platform Super Admin created:")
        print(f"  email:            {created.user.email}")
        print(f"  username (code):  {created.user.username}")
        print(f"  initial_password: {created.initial_password}")


if __name__ == "__main__":
    asyncio.run(main())
