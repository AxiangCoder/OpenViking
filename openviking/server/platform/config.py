"""Platform 配置模型（05 §11 Platform 依赖链）。

配置只从环境变量注入，不硬编码生产口令。默认 DSN 仅面向本地开发
（Spike 约定端口 55432），生产由部署层提供 OV_PLATFORM_DATABASE_URL。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class PlatformConfig:
    """Platform v0.1 配置。

    后续 Phase（Session TTL、Cookie、限流阈值等）在此扩展，
    保持单实例进程内配置事实来源（06 §16.4）。
    """

    database_url: str = field(
        default_factory=lambda: _env(
            "OV_PLATFORM_DATABASE_URL",
            "postgresql+asyncpg://ov_platform:ov_platform_dev@127.0.0.1:55432/ov_platform",
        )
    )

    def with_database_url(self, url: str) -> "PlatformConfig":
        """返回仅替换 database_url 的副本（测试用）。"""
        return PlatformConfig(database_url=url)


platform_config = PlatformConfig()
