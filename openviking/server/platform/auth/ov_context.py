"""转换到 OpenViking RequestContext（02 §7.3，14 号计划 §97.2）。

- `to_ov_context`：仅 User 私有数据，使用目标 Subject 的最小权限
  OpenViking 上下文（`Role.USER`）；
- `to_ov_account_context`：仅 Account 共享数据，Subject User 必须为空；
  同 Account 的 User/Account Admin 使用自己的 OpenViking User ID 作为执行
  载体，Platform Super Admin 跨 Account 时使用保留的 `platform-gateway`
  **执行占位标识**（02 §7.3：不创建 IAM User/可登录用户/Role/API Key/OAuth
  Client，也不能从 HTTP/MCP 声明，因此不是 Service Account）；
- 两种转换都使用 `Role.USER` 作为最小 OpenViking Base Role；Account
  Admin/PSA 的共享管理能力来自前置 Platform Permission，不依赖把执行
  上下文提升为 Root/Admin（平台 rank 与 OpenViking rank 隔离，04 §10.4）。
"""

from __future__ import annotations

from openviking.server.identity import RequestContext, Role
from openviking.server.platform.auth.access import DataAccessContext, authorize_data_access
from openviking_cli.session.user_id import UserIdentifier

# 跨 Account 共享操作的保留执行占位标识（02 §7.3）。
PLATFORM_GATEWAY_USER = "platform-gateway"


def to_ov_context(principal, access: DataAccessContext) -> RequestContext:
    """User 私有数据 → 最小权限 OpenViking 上下文（02 §7.3）。

    断言 `visibility=user_private` 且 Subject User/ov 映射非空；
    对数据读取使用目标 Subject 的最小权限（`Role.USER`）。
    """
    authorize_data_access(principal, access)
    assert access.visibility == "user_private"
    assert access.subject_user_id is not None and access.subject_ov_user_id is not None
    return RequestContext(
        user=UserIdentifier(access.subject_ov_account_id, access.subject_ov_user_id),
        role=Role.USER,
        actor_peer_id=None,
    )


def to_ov_account_context(principal, access: DataAccessContext) -> RequestContext:
    """Account 共享数据 → 最小权限 OpenViking 上下文（02 §7.3）。

    同 Account 的 User/Account Admin 使用自己的 OpenViking User ID 作为
    执行载体；Platform Super Admin 跨 Account 时使用保留的
    `platform-gateway` 执行占位标识（不是 Service Account）。
    """
    authorize_data_access(principal, access)
    assert access.visibility == "account_shared"
    assert access.subject_user_id is None and access.subject_ov_user_id is None
    if principal.actor_account_id == access.subject_account_id:
        execution_user_id = principal.actor_ov_user_id
    else:
        execution_user_id = PLATFORM_GATEWAY_USER
    assert execution_user_id is not None
    return RequestContext(
        user=UserIdentifier(access.subject_ov_account_id, execution_user_id),
        role=Role.USER,
        actor_peer_id=None,
    )
