"""Actor、Subject 与数据访问上下文（02 §7.2，14 号计划 §97.2）。

`DataAccessContext` 同时携带 Actor（实际操作者）与 Subject（数据归属者）：
- User 私有数据的 Subject 是数据所属 User；Account 共享数据的 Subject 是
  目标 Account，`subject_user_id=None`——不虚构"共享资源所属用户"；
- `subject_*` 是 PostgreSQL 产品 ID，供 RBAC/数据范围/审计使用；
  `subject_ov_*` 只能由服务端通过 IAM 映射得到，客户端不能提交；
- `canonical_ov_uri` 必须由服务端构造或规范化，并与 `visibility`、Subject
  做一致性校验；客户端提交的 visibility/account_id/user_id 不能单独成为
  授权依据（02 §7.2，AC④）。

`authorize_data_access` 授权规则（02 §7.2）：
- User 私有数据：User 只能访问同 Account 自己的对象；Account Admin 可读取
  本 Account 内任意 Subject；Platform Super Admin 可按平台范围读取任意 Subject；
- Account 共享数据：User/Account Admin 只能访问同 Account 的共享对象；
  Platform Super Admin 可选择任意目标 Account；
- 对他人数据的写入/导出/删除不从"可读取"自动推导（由 TargetPolicy 检查
  独立 Permission）；本函数只做数据范围与 canonical URI 一致性校验。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from openviking.server.platform.errors import (
    AccessDeniedError,
    CanonicalUriMismatchError,
)

Visibility = Literal["user_private", "account_shared"]

ACCOUNT_ADMIN_ROLE = "account_admin"
PLATFORM_SUPER_ADMIN_ROLE = "platform_super_admin"

# Account 共享内容根（04 §10.10：Resource → viking://resources/**，
# Skill → viking://agent/skills/**）。
_ACCOUNT_SHARED_ROOTS = ("viking://resources/", "viking://agent/skills/")


@dataclass(frozen=True)
class DataAccessContext:
    """02 §7.2：Actor + Subject + 已授权目标（canonical URI/action/request_id）。"""

    actor_user_id: uuid.UUID
    actor_account_id: uuid.UUID | None
    subject_account_id: uuid.UUID
    subject_user_id: uuid.UUID | None
    subject_ov_account_id: str
    subject_ov_user_id: str | None
    visibility: Visibility
    canonical_ov_uri: str
    action: str
    request_id: str


def _is_platform_super_admin(principal) -> bool:
    return principal.actor_account_id is None and PLATFORM_SUPER_ADMIN_ROLE in (
        principal.role_codes or ()
    )


def _is_account_admin(principal) -> bool:
    return ACCOUNT_ADMIN_ROLE in (principal.role_codes or ())


def _uri_consistent_with_target(access: DataAccessContext) -> bool:
    """canonical URI 与 visibility/Subject 的一致性校验（02 §7.2，AC④）。

    - `user_private`：URI 必须位于 Subject User 的
      `viking://user/{subject_ov_user_id}/` canonical root；
    - `account_shared`：Subject User 必须为空，URI 必须位于 Account 共享根
      （`viking://resources/**` 或 `viking://agent/skills/**`）；
    - 不满足即视为伪造，拒绝时不区分失败原因（防枚举）。
    """
    if access.visibility == "user_private":
        if access.subject_user_id is None or access.subject_ov_user_id is None:
            return False
        prefix = f"viking://user/{access.subject_ov_user_id}/"
        if not access.canonical_ov_uri.startswith(prefix):
            return False
        return True
    # account_shared
    if access.subject_user_id is not None or access.subject_ov_user_id is not None:
        return False
    return access.canonical_ov_uri.startswith(_ACCOUNT_SHARED_ROOTS)


def authorize_data_access(principal, access: DataAccessContext) -> None:
    """统一数据范围授权（02 §7.2，05 §11.1 依赖链的 resolve_data_access）。

    只负责"数据范围 + canonical URI 一致性"；动作级 Permission 由
    TargetPolicy/AuthorizationService 另查（对他人数据的写/删/导出不自动推导）。

    Raises:
        CanonicalUriMismatchError: canonical URI 与 visibility/Subject 不一致（AC④）；
        AccessDeniedError: 数据范围越权（跨 Account/跨 User 等）。
    """
    if not _uri_consistent_with_target(access):
        raise CanonicalUriMismatchError(
            "canonical uri inconsistent with visibility/subject"
        )

    if access.visibility == "user_private":
        same_account = (
            access.actor_account_id is not None
            and access.actor_account_id == access.subject_account_id
        )
        is_self = (
            same_account and access.actor_user_id == access.subject_user_id
        )
        if is_self:
            return
        if same_account and _is_account_admin(principal):
            # Account Admin 可读取本 Account 内任意 Subject（02 §7.2）；
            # 写/删等仍由独立 Permission 校验。
            return
        if _is_platform_super_admin(principal):
            return
        raise AccessDeniedError("CROSS_ACCOUNT_ACCESS")

    # account_shared
    same_account = (
        access.actor_account_id is not None
        and access.actor_account_id == access.subject_account_id
    )
    if same_account:
        return
    if _is_platform_super_admin(principal):
        return
    raise AccessDeniedError("CROSS_ACCOUNT_ACCESS")
