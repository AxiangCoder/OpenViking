"""进程内权限缓存（03 §9.4；06 §16.4 单实例许可，Redis 共享缓存归 P2）。

缓存键 = `(account_id, user_id, permission_version, permission_schema_version)`：
- `permission_version`：用户自身状态或角色变更时递增（04 §10.2）；
- `permission_schema_version`：内置角色权限种子或 migration 变更时递增（04 §10.5）。

两者独立，任一版本变化都会使旧缓存键不可达，保证全局权限变更后不会返回旧权限。
写入路径（角色授予/状态变更）额外调用 invalidate_user/invalidate_global 主动释放内存，
正确性不依赖主动失效（版本键天然隔离），TTL 仅兜底回收。
"""

from __future__ import annotations

import time
import uuid

from openviking.server.platform.iam.permissions import UserPermissions

CacheKey = tuple[str | None, uuid.UUID, int, int]


class PermissionCache:
    """进程内短 TTL 权限缓存。单实例进程内使用（06 §16.4）。"""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._store: dict[CacheKey, tuple[float, UserPermissions]] = {}

    def get(self, key: CacheKey) -> UserPermissions | None:
        item = self._store.get(key)
        if item is None:
            return None
        ts, value = item
        if time.monotonic() - ts > self._ttl_seconds:
            self._store.pop(key, None)
            return None
        return value

    def put(self, key: CacheKey, value: UserPermissions) -> None:
        self._store[key] = (time.monotonic(), value)

    def invalidate_user(self, user_id: uuid.UUID) -> None:
        """用户权限变更（角色授予/提升/状态变更）后主动失效该用户全部条目。"""
        for key in [k for k in self._store if k[1] == user_id]:
            self._store.pop(key, None)

    def invalidate_global(self) -> None:
        """全局 schema 版本变更后清空（种子/migration 变更路径调用）。"""
        self._store.clear()

    def clear(self) -> None:
        self._store.clear()
