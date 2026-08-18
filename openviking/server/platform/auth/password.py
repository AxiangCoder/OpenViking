"""密码哈希与凭证原语（05 §11 `auth/password.py`）。

- 密码：Argon2id（argon2-cffi，版本锁定 ==25.1.0，异常名稳定，spike §4.2 #6）；
  校验异常统一吞并为 False：`VerifyMismatchError`（密码不匹配）、
  `VerificationError`（参数/版本校验失败）、`InvalidHashError`（hash 非法/篡改）。
- 登录 Session Token：不透明，≥256 bit，base64url（03 §8.1；DB 仅存 SHA-256，04 §10.7）。
- CSRF secret：与登录 Session 绑定（03 §8.2），DB 仅存 SHA-256。
- 常量时间比较：API Key 校验复用（P1-E4）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

_SESSION_TOKEN_BYTES = 32  # 256 bit（03 §8.1：Cookie 值至少 256 bit）
_CSRF_SECRET_BYTES = 32
_INITIAL_PASSWORD_LENGTH = 16

# 去除易混字符（0/O、1/l/I 等）的随机密码字母表（03 §8.3）
_INITIAL_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*-_"


def hash_password(password: str) -> str:
    """Argon2id 哈希（argon2-cffi 默认参数）。"""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """常量时间校验密码；异常（不匹配/参数版本/非法 hash）统一返回 False。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def hash_ip(ip: str) -> str:
    """隐私化 IP（04 §10.7 `ip_hash`；14 号计划 §96.3 限流与审计均用 hash）。"""
    return sha256_hex(ip)


def new_session_token() -> str:
    """不透明登录 Session Token：32 字节（256 bit）随机数 base64url（≥40 字符）。"""
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(_SESSION_TOKEN_BYTES))
        .rstrip(b"=")
        .decode("ascii")
    )


def new_csrf_secret() -> str:
    """CSRF secret（与登录 Session 绑定；DB 仅存 SHA-256，04 §10.7）。"""
    return secrets.token_urlsafe(_CSRF_SECRET_BYTES)


def random_initial_password() -> str:
    """随机初始/重置密码（03 §8.3）：16 字符，只展示一次。"""
    return "".join(secrets.choice(_INITIAL_PASSWORD_ALPHABET) for _ in range(_INITIAL_PASSWORD_LENGTH))
