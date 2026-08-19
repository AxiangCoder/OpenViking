"""OpenViking 控制面适配层（05 §11.3，14 号计划 §97.1 AC⑧）。

Provisioning Worker 用 `SystemPrincipal` 初始化 OpenViking namespace
（`ov_account_id`/`ov_user_id` 映射），保证 PostgreSQL 与 OpenViking
控制面的 ov 映射一致。控制面动作必须幂等（AC③：Worker 重跑不产生重复
namespace）。

v0.1 开发态实现：`FakeControlPlane`（内存 namespace 注册表）。OpenViking
namespace 的真实初始化需要完整 `OpenVikingService`/存储环境，属 P5-E1
（初始化与部署）收口范围；本 Epic 以受控 fake 适配层验证语义，真实适配器
按本文件 `ControlPlaneAdapter` 协议实现即可替换（对账/重放验收不依赖
fake 的具体存储）。
"""

from __future__ import annotations

from typing import Protocol


class ControlPlaneAdapter(Protocol):
    """OpenViking 控制面接口（实现须幂等）。"""

    async def provision_account(self, ov_account_id: str) -> None:
        """初始化 Account 级 namespace；重复调用不产生重复副作用。"""

    async def provision_user(self, ov_account_id: str, ov_user_id: str) -> None:
        """初始化 User 级 namespace（含其所属 Account 的保证）；幂等。"""

    async def list_provisioned_accounts(self) -> set[str]:
        """当前已初始化的 Account namespace 集合（对账输入，04 §10.1 ov 映射）。"""

    async def list_provisioned_users(self, ov_account_id: str) -> set[str]:
        """指定 Account 下已初始化的 User namespace 集合（对账输入）。"""


class FakeControlPlane:
    """内存 namespace 注册表（开发/测试适配层）。

    行为与真实控制面一致：`provision_*` 幂等（重复调用集合不变）、
    `list_*` 返回当前已初始化的 ov 映射。`reset()` 模拟控制面丢失
    （对账漂移场景，AC⑧）。
    """

    def __init__(self) -> None:
        self._accounts: set[str] = set()
        self._users: dict[str, set[str]] = {}

    async def provision_account(self, ov_account_id: str) -> None:
        self._accounts.add(ov_account_id)

    async def provision_user(self, ov_account_id: str, ov_user_id: str) -> None:
        await self.provision_account(ov_account_id)
        self._users.setdefault(ov_account_id, set()).add(ov_user_id)

    async def list_provisioned_accounts(self) -> set[str]:
        return set(self._accounts)

    async def list_provisioned_users(self, ov_account_id: str) -> set[str]:
        return set(self._users.get(ov_account_id, set()))

    def reset(self) -> None:
        """模拟控制面 namespace 全部丢失（对账漂移测试用）。"""
        self._accounts.clear()
        self._users.clear()
