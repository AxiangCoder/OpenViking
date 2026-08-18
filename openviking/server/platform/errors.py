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
