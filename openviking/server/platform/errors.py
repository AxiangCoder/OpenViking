"""Platform 领域异常（05 §11 错误分层）。

对外稳定错误码（`RESOURCE_*`/`SKILL_*` 等）由 API 层映射，本模块只定义
数据层/服务层抛出的内部异常。
"""

from __future__ import annotations


class PlatformError(Exception):
    """Platform 领域异常基类。"""


class EntityNotFoundError(PlatformError):
    """按标识查找的实体不存在（跨 Account 访问语义统一归调用方处理）。"""


class OptimisticLockError(PlatformError):
    """乐观锁冲突：目标实体 version 与预期不一致，需重读后重试。"""


class ConstraintViolationError(PlatformError):
    """唯一约束/外键约束等完整性冲突（IntegrityError 的领域包装）。"""


# ── P1-E2：RBAC 服务层（只 append，不修改既有码）──


class RoleAssignmentError(PlatformError):
    """角色授予被拒（04 §10.6）：Account 一致性 / 单角色 / PSA 仅平台初始化路径。

    `reason` 为稳定原因码（如 `CROSS_ACCOUNT_ROLE_ASSIGNMENT`），供 API 层映射为
    对外错误码；拒绝事件由 RbacService 先写审计（result=denied）再抛出。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


# ── P1-E3：认证与登录 Session（只 append，不修改既有码）──


class AuthenticationError(PlatformError):
    """认证链路失败（401 语义，03 §8）。

    `code` 为稳定对外错误码（`LOGIN_FAILED`/`SESSION_EXPIRED`/`USER_DISABLED`/
    `INVALID_CREDENTIAL`，05 §12.2），供 API 层原样映射。
    """

    def __init__(self, code: str, *args: object) -> None:
        super().__init__(code, *args)
        self.code = code


class LoginFailedError(AuthenticationError):
    """登录/改密统一失败：未知邮箱、错误密码、非 active 用户同一 `LOGIN_FAILED`
    （防枚举，03 §8.3）。"""

    def __init__(self) -> None:
        super().__init__("LOGIN_FAILED")


class LoginRateLimitedError(LoginFailedError):
    """连续失败进入限流冷却（03 §8.3）：对外仍 `LOGIN_FAILED`（防枚举），
    附带 `retry_after_seconds` 供 API 层下发 Retry-After。"""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds


class PasswordResetForbiddenError(PlatformError):
    """分级密码重置被拒（03 §8.3）：`actor_role_rank <= target_role_rank`。

    `reason` 为稳定原因码（`PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN`），
    API 层映射 403；拒绝事件先写审计（result=denied）再抛出。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


# ── P1-E5：IAM 管理 API 与审计基础（只 append，不修改既有码）──


class LastAccountAdminError(PlatformError):
    """最后一名 Account Admin 的禁用/删除被拒（05 §12.2 `LAST_ACCOUNT_ADMIN_REQUIRED`）。

    拒绝事件先写审计（result=denied）再抛出；User 删除接口归属 P2-E2。
    """


class AdminActionForbiddenError(PlatformError):
    """管理动作守卫拒绝（如平台级提升仅 `user → account_admin`）。

    `reason` 为稳定原因码；拒绝事件先写审计（result=denied）再抛出。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class InvalidCursorError(PlatformError):
    """分页 cursor 格式非法（05 §12.2：cursor 不透明、API 层映射 400）。"""
