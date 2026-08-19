"""SystemPrincipal（02 §7.1，14 号计划 §97.1）。

v0.1 不定义 `ServiceAccountPrincipal`。内部 Provisioning、清理等 Worker 使用
仅限进程内部的 `SystemPrincipal`，不签发可供外部插件使用的 API Key：

- 只能由受控 Worker 代码路径构造（本模块仅在 provisioning Worker/Reconciler
  内实例化）；没有登录入口、API Key 或 OAuth Token，也不能通过 HTTP/MCP 请求
  声明（主体验证器 `resolve_principal` 不接受任何 system 形态的输入）；
- 只允许执行代码中明确列出的系统动作，并在审计中记录组件名、任务 ID、
  Subject 和结果（04 §10.8：`actor_type=system` + `actor_system_component`）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemPrincipal:
    """进程内系统主体（02 §7.1）。

    `component` 为系统任务组件名（如 `provisioning.worker`），写审计时必填
    （04 §10.8）；`task_id` 关联任务（outbox 事件 ID）。

    HTTP/MCP 路径禁止构造本主体：任何 REST/MCP 请求的 Principal 必须来自
    `resolve_principal`（仅产出 `AuthenticatedUserPrincipal`）。
    """

    component: str
    task_id: str | None = None

    @property
    def label(self) -> str:
        return f"system:{self.component}"
