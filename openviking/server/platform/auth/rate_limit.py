"""登录限流器（03 §8.3：IP+标识限流递增冷却，14 号计划 §96.3）。

进程内实现（06 §16.4 单实例许可；多实例共享后端不在 v0.1）。

key = `(ip_hash, 规范化登录标识)`。连续失败计数达到阈值后进入冷却；
冷却期间所有尝试（含正确密码）统一对外 `LOGIN_FAILED`（防枚举，
由 LoginRateLimitedError 携带 Retry-After）；冷却时长随持续失败递增
（`base * 2 ** (fail_count - max_attempts)`，封顶 `max_seconds`）。
冷却期满后计数衰减；登录成功即重置该 key。
"""

from __future__ import annotations

import time


class LoginRateLimiter:
    """进程内登录限流器。非线程安全（FastAPI 单事件循环单实例使用）。"""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        cooldown_base_seconds: int = 60,
        cooldown_max_seconds: int = 3600,
        clock=time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._base_seconds = cooldown_base_seconds
        self._max_seconds = cooldown_max_seconds
        self._clock = clock
        self._state: dict[str, tuple[int, float]] = {}  # key -> (fail_count, blocked_until)

    @staticmethod
    def make_key(ip_hash: str, login_identifier: str) -> str:
        return f"{ip_hash}|{login_identifier}"

    def retry_after_seconds(self, key: str) -> int | None:
        """冷却中的剩余秒数（向上取整）；未冷却返回 None。"""
        now = self._clock()
        state = self._state.get(key)
        if state is None:
            return None
        _, blocked_until = state
        remaining = blocked_until - now
        if remaining <= 0:
            return None
        return int(remaining) + 1

    def is_limited(self, key: str) -> bool:
        return self.retry_after_seconds(key) is not None

    def record_failure(self, key: str) -> int:
        """记录一次失败；返回本次进入冷却的秒数（0 表示尚未冷却）。

        冷却期满后计数衰减（下一次失败重新从 1 计），避免过期冷却被无限放大。
        """
        now = self._clock()
        fail_count, blocked_until = self._state.get(key, (0, 0.0))
        if fail_count and blocked_until and now >= blocked_until:
            fail_count = 0
        fail_count += 1
        cooldown_seconds = 0
        if fail_count >= self._max_attempts:
            cooldown_seconds = min(
                self._base_seconds * (2 ** (fail_count - self._max_attempts)),
                self._max_seconds,
            )
            blocked_until = now + cooldown_seconds
        else:
            blocked_until = 0.0
        self._state[key] = (fail_count, blocked_until)
        return cooldown_seconds

    def record_success(self, key: str) -> None:
        """登录成功：重置该 key。"""
        self._state.pop(key, None)

    def clear(self) -> None:
        self._state.clear()
