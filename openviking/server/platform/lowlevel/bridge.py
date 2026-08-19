"""MCP Tool 产品化接入桥（08 §28.5，14 号计划 §97.6）。

`McpPlatformBridge` 是 `mcp_endpoint` 访问平台服务的受控窗口，由 mount.py
装配到 `app.state.platform_mcp_bridge`（平台模式），非平台模式不存在：

- `guard`：MCP Tool 的 TargetPolicy 检查（forget 删除权限、cancel_watch
  目标写权限、add_resource 默认目标）；
- `registry_store`/`registry_service`：`forget` 经 Content Registry 解析
  ref 并进入 30 天软删除回收期（05 §11.5：不能调用不可恢复的底层 rm）；
- `session_factory`：删除任务/审计事务。

MCP Tool 通过 `_mcp_app_ctx` 读取当前请求所属 app 的 bridge，不引入
模块级单例（每个 app 独立状态，测试友好）。
"""

from __future__ import annotations

from dataclasses import dataclass

from openviking.server.platform.lowlevel.guard import LowLevelPolicyGuard
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.registry.service import ContentRegistryService


@dataclass(frozen=True)
class McpPlatformBridge:
    """平台模式 MCP Tool 服务窗口（08 §28.5）。"""

    guard: LowLevelPolicyGuard
    registry: ContentRegistryService
    registry_store: RegistryRepository
    session_factory: object
    deletion_purge_days: int = 30


__all__ = ["McpPlatformBridge"]
