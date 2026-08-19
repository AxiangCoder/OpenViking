"""真实 OpenViking namespace 初始化适配器（05 §11.3，14 号计划 §99.1）。

`ControlPlaneAdapter` 协议（providers/provisioning/control_plane.py）的
真实实现：Provisioning Worker/Reconciler 用 `SystemPrincipal` 初始化
Account/User namespace，保证 PostgreSQL 与 OpenViking 控制面 ov 映射一致。

- `provision_account` → `OpenVikingService.initialize_account_directories(ctx)`
  （幂等：预设目录按需创建，viking://resources 共享根）；
- `provision_user` → `initialize_user_directories(ctx)`（viking://user/{id} 预设
  树，幂等）；同时保证 Account 级目录已初始化（与 Fake 语义一致）；
- `list_provisioned_accounts/users`：v0.4.12 控制面无"已初始化账号"枚举接口，
  实现为 viking_fs.stat 存在性检查（viking://resources / viking://user/{id}）。
  账号枚举不可用（全局共享根 + 无元数据索引），返回空集合并仅用于对账
  提示——重放语义保证幂等（重复初始化不产生重复副作用），漂移检测由
  Provisioning 重放兜底。
"""

from __future__ import annotations

from typing import Optional

from openviking.server.platform.adapters.runtime import (
    ServiceProvider,
    default_service_provider,
    require_service,
    user_ctx,
)

ACCOUNT_ROOT_URI = "viking://resources"


class RealControlPlaneAdapter:
    """真实 OpenViking namespace 初始化（惰性运行时解析）。"""

    def __init__(self, service_provider: Optional[ServiceProvider] = None) -> None:
        self._provider: ServiceProvider = service_provider or default_service_provider()

    async def provision_account(self, ov_account_id: str) -> None:
        service = require_service(self._provider, "control_plane.provision_account")
        ctx = user_ctx(ov_account_id, "default")
        await service.initialize_account_directories(ctx)

    async def provision_user(self, ov_account_id: str, ov_user_id: str) -> None:
        service = require_service(self._provider, "control_plane.provision_user")
        account_ctx = user_ctx(ov_account_id, "default")
        await service.initialize_account_directories(account_ctx)
        await service.initialize_user_directories(user_ctx(ov_account_id, ov_user_id))

    async def list_provisioned_accounts(self) -> set[str]:
        service = require_service(self._provider, "control_plane.list_provisioned_accounts")
        viking_fs = getattr(service, "viking_fs", None)
        if viking_fs is None:
            return set()
        ov_config = getattr(service, "_config", None)
        default_account = getattr(ov_config, "default_account", "") or ""
        try:
            await viking_fs.stat(ACCOUNT_ROOT_URI, ctx=user_ctx(default_account, "default"))
        except Exception:
            return set()
        return set()

    async def list_provisioned_users(self, ov_account_id: str) -> set[str]:
        service = require_service(self._provider, "control_plane.list_provisioned_users")
        viking_fs = getattr(service, "viking_fs", None)
        if viking_fs is None:
            return set()
        root = f"viking://user/{ov_account_id}"
        try:
            entries = await viking_fs.ls(root, ctx=user_ctx(ov_account_id, ov_account_id))
        except Exception:
            return set()
        return {entry.get("name") for entry in entries if entry.get("isDir")}
