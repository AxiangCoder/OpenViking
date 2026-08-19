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


# ── P1-E4：用户 API Key（只 append，不修改既有码）──


class ApiCredentialError(PlatformError):
    """API Key 签发/撤销被拒（04 §10.3，05 §11.4）。

    `reason` 为稳定原因码（`PSA_API_KEY_NOT_SUPPORTED`/`INVALID_EXPIRATION`），
    API 层映射 400/403；服务层先写审计再抛出（对齐 P1-E2 拒绝语义）。
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


# ── P2-E1：Provisioning Outbox/Worker/Reconciler（只 append，不修改既有码）──


class ProvisioningError(PlatformError):
    """Provisioning 处理失败（05 §11.3）：Worker/控制面同步错误。

    `last_error` 一律存脱敏错误（不含路径/密钥/口令，04 §10.9）。
    """


class ProvisioningNotRetryableError(PlatformError):
    """`provisioning/retry` 拒绝（05 §12.6）：目标已 active 且无未完成事件。

    `reason` 为稳定对外码（`PROVISIONING_NOT_RETRYABLE`，spike ==12：active 重试 409）。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


# ── P2-E2：身份上下文/URI Policy/Content Registry/删除回收（只 append，不修改既有码）──


class AccessDeniedError(PlatformError):
    """数据访问授权被拒（02 §7.2/§7.5 统一授权门）。

    `reason` 为稳定原因码（`CROSS_ACCOUNT_ACCESS`/`SUBJECT_MISMATCH` 等，
    API 层映射 403/404）；拒绝审计由调用方在抛出前按 P1-E2 语义写入。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class InvalidTargetError(PlatformError):
    """目标 URI 规范化/分类失败（02 §7.5）：非 `viking://` URI、不可解析段等。

    `reason` 为稳定原因码（`INVALID_URI`/`INTERNAL_TARGET_DENIED` 等）。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class CanonicalUriMismatchError(PlatformError):
    """canonical URI 与 visibility/Subject 一致性校验失败（02 §7.2/§7.5，AC④）。

    客户端提交的 visibility/account_id/user_id 不能单独作为授权依据；
    服务端构造的 canonical URI 不一致即拒（不区分失败原因，防枚举）。
    """


class TagValidationError(PlatformError):
    """产品 tags 校验失败（04 §10.10：≤20 项、每项 ≤40 字符、`key=value`、
    key/value 非空、整体小写并去重；AC 由 05 §12.5 统一强制）。

    `reason` 为稳定原因码（`TAG_LIMIT_EXCEEDED`/`TAG_INVALID_FORMAT` 等）。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class UploadNotConsumableError(PlatformError):
    """Upload 不可消费（04 §10.14）：跨 User/Account/Visibility/Object Type
    消费、过期、已消费、重放统一拒绝。

    `reason` 为稳定原因码（`UPLOAD_NOT_FOUND`/`UPLOAD_EXPIRED`/`UPLOAD_CONSUMED`/
    `UPLOAD_SCOPE_MISMATCH`）。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class DeletionJobError(PlatformError):
    """删除任务/恢复被拒（04 §10.11，05 §12.6 注）。

    `reason` 为稳定原因码（`RESTORE_WINDOW_EXPIRED`/`ALREADY_RESTORED`/
    `NOT_RESTORABLE`/`NOT_RESTORABLE_ALREADY_PURGED`），API 层映射 409/403。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class PurgeError(PlatformError):
    """Purge Worker 物理清理失败（04 §10.11）：`last_error` 只存脱敏错误。"""


# ── P2-E4：Skill 产品 API（10 §55/§58/§63，只 append，不修改既有码）──


class SkillNameConflictError(PlatformError):
    """Skill 名称在当前 Account 不可用（10 §55.1/§55.3，AC①③）。

    创建/上传/发布/恢复冲突统一映射 409 `SKILL_NAME_CONFLICT`；
    响应只说明"该名称在当前 Account 不可用"，不泄露占用者。
    """


class SkillNameImmutableError(PlatformError):
    """Skill 名称创建后不可修改（10 §55.2，AC②）：JSON 更新与 ZIP 替换均拒。"""


class SkillInvalidFormatError(PlatformError):
    """SKILL.md/Frontmatter/ZIP 结构不合法（10 §63 `SKILL_INVALID_FORMAT`）。

    `reason` 为内部原因码（`MISSING_SKILL_MD`/`ZIP_PATH_TRAVERSAL`/
    `PACKAGE_NAME_MISMATCH` 等），API 层统一映射 400。
    """

    def __init__(self, reason: str, *args: object) -> None:
        super().__init__(reason, *args)
        self.reason = reason


class SkillSharedWriteForbiddenError(PlatformError):
    """普通 User 或 PSA 尝试写共享 Skill（10 §63 `SKILL_SHARED_WRITE_FORBIDDEN`）。"""


class SkillPrivateManageForbiddenError(PlatformError):
    """Account Admin/Platform 尝试编辑、删除或恢复他人私有 Skill（10 §59/§63）。"""


class SkillPublishForbiddenError(PlatformError):
    """非 Account Admin、跨 Account、Platform 或非私有 Skill 发起发布
    （10 §58.1/§63 `SKILL_PUBLISH_FORBIDDEN`）。"""


class SkillUnpublishUnsupportedError(PlatformError):
    """请求共享转私有或取消发布（10 §63 `SKILL_UNPUBLISH_UNSUPPORTED`）。"""


class SkillPublishMigrationError(PlatformError):
    """发布 Worker 迁移失败（10 §58.4）：Operation 置 failed 可重试。

    `retryable`：瞬时迁移失败可自动退避重试；目标占用等持久冲突不可重试。
    `code` 为稳定产品错误码（`SKILL_PUBLISH_TARGET_CONFLICT` 等）。
    """

    def __init__(self, code: str, *args: object, retryable: bool = True) -> None:
        super().__init__(code, *args)
        self.code = code
        self.retryable = retryable

