"""认证与登录 Session（05 §11 `auth/`，P1-E3）。

- password.py：Argon2id / 不透明 Session Token / SHA-256 / 常量时间比较；
- rate_limit.py：IP+标识限流递增冷却（03 §8.3）；
- principals.py：Session → AuthenticatedUserPrincipal（P1-E4 收敛 API Key/OAuth）；
- sessions.py：登录 Session 生命周期（04 §10.7，03 §8.1）；
- csrf.py：Origin/Referer + X-CSRF-Token 校验（03 §8.2）；
- service.py：AuthService（登录/登出/改密/分级重置/PSA bootstrap + 审计）；
- worker.py：过期 Session 周期物理清理（14 号计划 §96.3）。
"""

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
from openviking.server.platform.auth.principals import (
    AuthenticatedUserPrincipal,
    resolve_session_principal,
)
from openviking.server.platform.auth.rate_limit import LoginRateLimiter
from openviking.server.platform.auth.service import (
    AuthService,
    BootstrapResult,
    ChangedPasswordResult,
    LoginResult,
    PasswordResetResult,
    normalize_email,
)
from openviking.server.platform.auth.sessions import SessionCleanupResult, SessionService
from openviking.server.platform.auth.worker import SessionCleanupWorker

__all__ = [
    "AuthenticatedUserPrincipal",
    "AuthService",
    "BootstrapResult",
    "ChangedPasswordResult",
    "LoginRateLimiter",
    "LoginResult",
    "PasswordResetResult",
    "SessionCleanupResult",
    "SessionCleanupWorker",
    "SessionService",
    "constant_time_eq",
    "hash_ip",
    "hash_password",
    "new_csrf_secret",
    "new_session_token",
    "normalize_email",
    "random_initial_password",
    "resolve_session_principal",
    "sha256_hex",
    "verify_password",
]
