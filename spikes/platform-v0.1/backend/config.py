from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformConfig:
    database_url: str = os.environ.get(
        "OV_PLATFORM_DATABASE_URL",
        "postgresql+asyncpg://ov_platform:ov_platform_dev@127.0.0.1:55432/ov_platform",
    )
    session_idle_ttl_seconds: int = int(os.environ.get("OV_SESSION_IDLE_TTL", "86400"))
    session_absolute_ttl_seconds: int = int(os.environ.get("OV_SESSION_ABSOLUTE_TTL", "2592000"))
    cookie_secure: bool = os.environ.get("OV_COOKIE_SECURE", "0") == "1"
    cookie_name: str = "__Host-ov_session"
    password_min_length: int = 12
    api_key_prefix: str = "ovk_u"


config = PlatformConfig()
