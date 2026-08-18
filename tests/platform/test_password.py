"""P1-E3 密码哈希与凭证原语（14 号计划 §96.3，spike security.py 正式化）。

覆盖：Argon2id 哈希/校验与异常统一吞并（VerifyMismatchError/VerificationError/
InvalidHashError，spike §4.2 #6）；Session Token ≥256bit；CSRF secret；
SHA-256 与常量时间比较（04 §10.7）。
"""

from __future__ import annotations

from openviking.server.platform.auth.password import (
    constant_time_eq,
    hash_ip,
    hash_password,
    new_csrf_secret,
    new_session_token,
    random_initial_password,
    sha256_hex,
    verify_password,
)


def test_argon2id_roundtrip() -> None:
    h = hash_password("S3cure-Pass-2026!")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "S3cure-Pass-2026!")


def test_wrong_password_returns_false() -> None:
    h = hash_password("correct-horse-battery")
    assert not verify_password(h, "wrong-password")


def test_verify_mismatch_error_swallowed() -> None:
    """VerifyMismatchError 路径：不匹配即 False，不抛出。"""
    h = hash_password("aaa")
    assert verify_password(h, "bbb") is False


def test_invalid_hash_error_swallowed() -> None:
    """InvalidHashError 路径：非法/篡改 hash → False（spike §4.2 #6）。"""
    assert verify_password("not-a-valid-hash", "whatever") is False
    assert verify_password("", "whatever") is False


def test_verification_error_swallowed() -> None:
    """VerificationError 路径：格式合法但参数不可解 → False。"""
    assert verify_password("$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$c29tZWhhc2g", "x") is False


def test_hashes_salted_and_unique() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_session_token_at_least_256bit() -> None:
    """AC ① 基础：Cookie 值 ≥256bit（03 §8.1），base64url 编码 ≥40 字符。"""
    token = new_session_token()
    assert len(token) >= 40
    assert len({new_session_token() for _ in range(200)}) == 200  # 高熵无碰撞


def test_csrf_secret_strength() -> None:
    assert len(new_csrf_secret()) >= 32


def test_sha256_db_only_hash_semantics() -> None:
    """04 §10.7：DB 仅存 SHA-256(token)——64 位 hex、确定性、不可逆。"""
    assert len(sha256_hex("anything")) == 64
    assert sha256_hex("a") == sha256_hex("a")
    assert sha256_hex("a") != sha256_hex("b")


def test_constant_time_eq() -> None:
    assert constant_time_eq("abc", "abc")
    assert not constant_time_eq("abc", "abd")
    assert not constant_time_eq("abc", "")


def test_hash_ip_is_privacy_preserving() -> None:
    """04 §10.7 `ip_hash`：不落完整 IP。"""
    h = hash_ip("203.0.113.7")
    assert len(h) == 64
    assert "203.0.113.7" not in h


def test_random_initial_password_strength() -> None:
    """03 §8.3：初始/重置密码随机 16 字符（只展示一次）。"""
    password = random_initial_password()
    assert len(password) == 16
    assert password != random_initial_password()
