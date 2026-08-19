"""Platform 配置模型（05 §11 Platform 依赖链）。

配置只从环境变量注入，不硬编码生产口令。默认 DSN 仅面向本地开发
（Spike 约定端口 55432），生产由部署层提供 OV_PLATFORM_DATABASE_URL。

P1-E3 扩展（14 号计划 §96.3）：登录 Session TTL、Cookie 属性、登录限流、
CSRF 信任源、Session 清理周期。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in os.environ.get(name, "").split(",") if p.strip())


@dataclass(frozen=True)
class PlatformConfig:
    """Platform v0.1 配置。

    - Session：空闲 24h / 绝对 30 天可配置（03 §8.1，04 §10.7）；
    - Cookie：`__Host-ov_session`，HttpOnly/Secure/SameSite=Lax/Path=/（03 §8.1）。
      `OV_COOKIE_SECURE` 默认 1（生产安全默认）；本地 http 开发可置 0，
      但 `__Host-` 前缀按标准要求 Secure，仅建议开发态关闭；
    - 登录限流：连续失败进入递增冷却（03 §8.3，14 号计划 §96.3）；
    - CSRF：`OV_CSRF_ALLOWED_ORIGINS` 为空时按请求 Host 同源校验（03 §8.2）；
    - 过期 Session 物理清理 Worker 周期（14 号计划 §96.3，健康检查随 P5-E2）。
    """

    database_url: str = field(
        default_factory=lambda: _env(
            "OV_PLATFORM_DATABASE_URL",
            "postgresql+asyncpg://ov_platform:ov_platform_dev@127.0.0.1:55432/ov_platform",
        )
    )

    # ── P1-E3：登录 Session（03 §8.1，04 §10.7）──

    session_idle_ttl_seconds: int = field(
        default_factory=lambda: _env_int("OV_SESSION_IDLE_TTL", 86400)
    )
    session_absolute_ttl_seconds: int = field(
        default_factory=lambda: _env_int("OV_SESSION_ABSOLUTE_TTL", 2592000)
    )
    cookie_name: str = "__Host-ov_session"
    cookie_secure: bool = field(default_factory=lambda: _env_bool("OV_COOKIE_SECURE", True))
    password_min_length: int = field(default_factory=lambda: _env_int("OV_PASSWORD_MIN_LENGTH", 12))

    # ── P1-E3：登录限流（03 §8.3）──

    login_max_attempts: int = field(default_factory=lambda: _env_int("OV_LOGIN_MAX_ATTEMPTS", 5))
    login_cooldown_base_seconds: int = field(
        default_factory=lambda: _env_int("OV_LOGIN_COOLDOWN_BASE", 60)
    )
    login_cooldown_max_seconds: int = field(
        default_factory=lambda: _env_int("OV_LOGIN_COOLDOWN_MAX", 3600)
    )

    # ── P1-E3：CSRF 信任源（03 §8.2）──

    csrf_allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: _env_list("OV_CSRF_ALLOWED_ORIGINS")
    )

    # ── P1-E3：过期 Session 清理 Worker ──

    session_cleanup_interval_seconds: int = field(
        default_factory=lambda: _env_int("OV_SESSION_CLEANUP_INTERVAL", 3600)
    )

    # ── P2-E1：Provisioning Worker/Reconciler（05 §11.3）──

    provisioning_batch_size: int = field(
        default_factory=lambda: _env_int("OV_PROVISIONING_BATCH_SIZE", 10)
    )
    provisioning_retry_base_seconds: int = field(
        default_factory=lambda: _env_int("OV_PROVISIONING_RETRY_BASE", 60)
    )
    provisioning_retry_max_seconds: int = field(
        default_factory=lambda: _env_int("OV_PROVISIONING_RETRY_MAX", 3600)
    )
    provisioning_stuck_timeout_seconds: int = field(
        default_factory=lambda: _env_int("OV_PROVISIONING_STUCK_TIMEOUT", 600)
    )

    # ── P2-E2：删除回收期与 Upload（04 §10.11/§10.14）──

    deletion_purge_days: int = field(default_factory=lambda: _env_int("OV_DELETION_PURGE_DAYS", 30))
    upload_ttl_minutes: int = field(default_factory=lambda: _env_int("OV_UPLOAD_TTL_MINUTES", 15))
    purge_batch_size: int = field(default_factory=lambda: _env_int("OV_PURGE_BATCH_SIZE", 50))

    # ── P5-E2：健康检查阈值（06 §16.3，14 号计划 §99.2，17.3）──

    provisioning_backlog_threshold: int = field(
        default_factory=lambda: _env_int("OV_HEALTH_PROVISIONING_BACKLOG_THRESHOLD", 100)
    )
    migration_lag_versions: int = field(
        default_factory=lambda: _env_int("OV_HEALTH_MIGRATION_LAG_VERSIONS", 1)
    )
    purge_stall_max_pending: int = field(
        default_factory=lambda: _env_int("OV_HEALTH_PURGE_STALL_MAX_PENDING", 500)
    )
    purge_stall_max_stall_days: int = field(
        default_factory=lambda: _env_int("OV_HEALTH_PURGE_STALL_MAX_DAYS", 3)
    )

    # ── P2-E3：Resource 摄取能力（09 §40/§43，服务端强制，AC⑩）──

    upload_max_files_per_batch: int = field(
        default_factory=lambda: _env_int("OV_UPLOAD_MAX_FILES_BATCH", 10)
    )
    upload_max_size_bytes: int = field(
        default_factory=lambda: _env_int("OV_UPLOAD_MAX_SIZE_BYTES", 10 * 1024 * 1024)
    )
    watch_enabled: bool = field(default_factory=lambda: _env_bool("OV_WATCH_ENABLED", True))
    watch_interval_presets: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in _env("OV_WATCH_INTERVAL_PRESETS", "60,360,720,1440,10080").split(",") if x
        )
    )
    refresh_min_interval_seconds: int = field(
        default_factory=lambda: _env_int("OV_REFRESH_MIN_INTERVAL", 300)
    )
    http_sources_allowed: bool = field(
        default_factory=lambda: _env_bool("OV_HTTP_SOURCES_ALLOWED", False)
    )
    # Node ID HMAC 签名密钥（09 §42.3：服务端生成、带版本的不透明标识）。
    # 生产由 Secret Manager 注入；开发态默认值只用于本地测试。
    node_id_secret: str = field(
        default_factory=lambda: _env("OV_NODE_ID_SECRET", "dev-node-id-secret-p2e3")
    )
    # 远程来源应用层加密密钥（09 §47.3：Envelope Encryption，明文不落库）。
    source_cipher_key: str = field(
        default_factory=lambda: _env("OV_SOURCE_CIPHER_KEY", "dev-source-cipher-key-p2e3-32bytes!")
    )

    def with_database_url(self, url: str) -> "PlatformConfig":
        """返回仅替换 database_url 的副本（测试用）。"""
        return PlatformConfig(database_url=url)


platform_config = PlatformConfig()
