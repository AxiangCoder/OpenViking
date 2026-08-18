"""P1-E3 登录限流器（03 §8.3：IP+标识限流递增冷却，14 号计划 §96.3 验收⑦）。"""

from __future__ import annotations

from openviking.server.platform.auth.rate_limit import LoginRateLimiter


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_threshold_triggers_cooldown() -> None:
    limiter = LoginRateLimiter(max_attempts=3, cooldown_base_seconds=60, cooldown_max_seconds=3600)
    key = LoginRateLimiter.make_key("ip1", "a@example.com")
    assert not limiter.is_limited(key)
    assert limiter.record_failure(key) == 0
    assert limiter.record_failure(key) == 0
    cooldown = limiter.record_failure(key)  # 第 3 次失败进入冷却
    assert cooldown == 60
    assert limiter.is_limited(key)
    assert 0 < limiter.retry_after_seconds(key) <= 61


def test_cooldown_escalates_with_continued_failures() -> None:
    """递增冷却：冷却期间继续失败 → base * 2 ** n。"""
    clock = _FakeClock()
    limiter = LoginRateLimiter(
        max_attempts=2, cooldown_base_seconds=10, cooldown_max_seconds=1000, clock=clock
    )
    key = LoginRateLimiter.make_key("ip1", "a@example.com")
    assert limiter.record_failure(key) == 0
    assert limiter.record_failure(key) == 10
    clock.advance(1)
    assert limiter.record_failure(key) == 20
    clock.advance(1)
    assert limiter.record_failure(key) == 40
    clock.advance(1)
    assert limiter.record_failure(key) == 80


def test_cooldown_capped() -> None:
    clock = _FakeClock()
    limiter = LoginRateLimiter(
        max_attempts=2, cooldown_base_seconds=10, cooldown_max_seconds=100, clock=clock
    )
    key = LoginRateLimiter.make_key("ip1", "a@example.com")
    for _ in range(30):
        clock.advance(1)
        limiter.record_failure(key)
    assert limiter.retry_after_seconds(key) is not None
    # 冷却封顶（retry_after 向上取整 +1）
    assert limiter.retry_after_seconds(key) <= 100 + 1


def test_success_resets_key() -> None:
    clock = _FakeClock()
    limiter = LoginRateLimiter(
        max_attempts=2, cooldown_base_seconds=60, cooldown_max_seconds=3600, clock=clock
    )
    key = LoginRateLimiter.make_key("ip1", "a@example.com")
    for _ in range(3):
        limiter.record_failure(key)
    assert limiter.is_limited(key)
    limiter.record_success(key)
    assert not limiter.is_limited(key)
    assert limiter.record_failure(key) == 0  # 从 1 重新计


def test_keys_isolated_by_ip_and_identifier() -> None:
    limiter = LoginRateLimiter(max_attempts=2, cooldown_base_seconds=60, cooldown_max_seconds=3600)
    k1 = LoginRateLimiter.make_key("ip-a", "alice@example.com")
    k1_same_ip_other_user = LoginRateLimiter.make_key("ip-a", "bob@example.com")
    k1_other_ip_same_user = LoginRateLimiter.make_key("ip-b", "alice@example.com")
    limiter.record_failure(k1)
    limiter.record_failure(k1)
    assert limiter.is_limited(k1)
    assert not limiter.is_limited(k1_same_ip_other_user)
    assert not limiter.is_limited(k1_other_ip_same_user)


def test_counter_decays_after_cooldown_expires() -> None:
    """冷却期满后计数衰减：下一次失败重新从 1 计（过期冷却不被无限放大）。"""
    clock = _FakeClock()
    limiter = LoginRateLimiter(
        max_attempts=2, cooldown_base_seconds=10, cooldown_max_seconds=1000, clock=clock
    )
    key = LoginRateLimiter.make_key("ip1", "a@example.com")
    limiter.record_failure(key)
    assert limiter.record_failure(key) == 10  # 冷却
    clock.advance(11)
    assert not limiter.is_limited(key)
    assert limiter.record_failure(key) == 0  # 衰减后重新计数
    assert limiter.record_failure(key) == 10
