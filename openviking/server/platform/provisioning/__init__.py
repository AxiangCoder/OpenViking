"""Provisioning 模块（05 §11.3，14 号计划 §97.1）。

- `SystemPrincipal`（02 §7.1）：进程内系统主体，仅受控 Worker/Reconciler
  代码路径构造，无登录入口/API Key/OAuth Token，不能通过 HTTP/MCP 请求声明；
  审计记录 `actor_type=system` + 组件名（04 §10.8）；
- `ProvisioningService`：PG 事务建 account/user(provisioning)+outbox → commit →
  Worker 用 SystemPrincipal 初始化 OpenViking namespace → active/failed；
- `ProvisioningWorker`：attempts/指数退避/脱敏错误（05 §11.3）；
- `ProvisioningReconciler`：卡死事件恢复、对账（控制面同步）。
"""

from openviking.server.platform.provisioning.control_plane import (
    ControlPlaneAdapter,
    FakeControlPlane,
)
from openviking.server.platform.provisioning.principals import SystemPrincipal
from openviking.server.platform.provisioning.reconciler import (
    ProvisioningReconciler,
    ReconcileReport,
)
from openviking.server.platform.provisioning.repository import ProvisioningRepository
from openviking.server.platform.provisioning.service import ProvisioningService
from openviking.server.platform.provisioning.worker import ProvisioningWorker

__all__ = [
    "ControlPlaneAdapter",
    "FakeControlPlane",
    "ProvisioningReconciler",
    "ProvisioningRepository",
    "ProvisioningService",
    "ProvisioningWorker",
    "ReconcileReport",
    "SystemPrincipal",
]
