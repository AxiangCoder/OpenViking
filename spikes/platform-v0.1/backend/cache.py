"""In-process permission cache with (user_id, permission_version, schema_version) keys.

Spike decision (design 03 §9.4 / 04 §10.5): two independent versions participate in
the cache key — the per-user permission_version and the global
GLOBAL_PERMISSION_SCHEMA_VERSION. A single-instance dict stands in for the
"short-TTL process-local cache" the design allows before Redis (06 §16.4).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

_TTL_SECONDS = 60
_store: dict[tuple[uuid.UUID, int, int], tuple[float, Any]] = {}
_user_generation: dict[uuid.UUID, int] = {}
_lock = asyncio.Lock()


class PermissionCache:
    @staticmethod
    async def get(key: tuple[uuid.UUID, int, int]) -> Any | None:
        async with _lock:
            item = _store.get(key)
            if item is None:
                return None
            ts, value = item
            if time.monotonic() - ts > _TTL_SECONDS:
                _store.pop(key, None)
                return None
            return value

    @staticmethod
    async def put(key: tuple[uuid.UUID, int, int], value: Any) -> None:
        async with _lock:
            _store[key] = (time.monotonic(), value)

    @staticmethod
    async def invalidate_user(user_id: uuid.UUID) -> None:
        async with _lock:
            for key in [k for k in _store if k[0] == user_id]:
                _store.pop(key, None)

    @staticmethod
    async def invalidate_global() -> None:
        async with _lock:
            _store.clear()
