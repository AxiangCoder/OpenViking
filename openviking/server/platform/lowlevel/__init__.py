"""低层入口统一守卫、IAM 认证插件与 MCP 产品化桥（P2-E6a）。

- `guard.py`：LowLevelPolicyGuard（05 §11.5 TargetPolicy 写守卫 + 默认目标
  + mv 规则 + 审计）；
- `plugin.py`：PlatformIamAuthPlugin（07 §20：/api/v1、/mcp 认证切换为
  IAM Principal）；
- `bridge.py`：McpPlatformBridge（08 §28.5：MCP Tool 访问平台服务的窗口）。
"""

from openviking.server.platform.lowlevel.bridge import McpPlatformBridge
from openviking.server.platform.lowlevel.guard import (
    LowLevelPolicyGuard,
    guard_move_request,
    guard_request,
    lowlevel_guard_for,
    object_type_for_uri,
    principal_from_request,
)
from openviking.server.platform.lowlevel.plugin import PlatformIamAuthPlugin

__all__ = [
    "LowLevelPolicyGuard",
    "McpPlatformBridge",
    "PlatformIamAuthPlugin",
    "guard_move_request",
    "guard_request",
    "lowlevel_guard_for",
    "object_type_for_uri",
    "principal_from_request",
]
