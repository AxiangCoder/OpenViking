"""真实适配器共享运行时（P5-E1 接线基础设施）。

- `AdapterRuntimeUnavailable`：真实 OpenViking 运行时缺失/未初始化时的统一
  异常（产品错误映射层按 503 处理）；
- `service_provider`：默认从进程注册表取 `OpenVikingService`
  （`openviking.server.dependencies.get_service_or_none`，create_app
  lifespan 中 `set_service` 注册）；
- `user_ctx`：ov 映射 → OpenViking `RequestContext`（Role.USER 最小权限，
  平台 rank 与 OpenViking rank 隔离，04 §10.4/02 §7.3）。

引擎模块一律延迟导入（与既有 Platform 模块规避循环导入的策略一致）。
"""

from __future__ import annotations

from typing import Callable, Optional

from openviking.server.dependencies import get_service_or_none
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


class AdapterRuntimeUnavailable(RuntimeError):
    """真实 OpenViking 运行时不可用（服务未启动/未配置工作区）。"""

    def __init__(self, component: str) -> None:
        super().__init__(
            f"OpenViking runtime unavailable for {component}: the product server "
            "requires a live OpenViking runtime (workspace/VectorDB configured). "
            "Set OV_PLATFORM_ADAPTER_MODE=fake to run with the controlled fakes."
        )
        self.component = component


ServiceProvider = Callable[[], Optional[object]]


def default_service_provider() -> ServiceProvider:
    return get_service_or_none


def require_service(provider: ServiceProvider, component: str):
    """取真实 OpenVikingService；缺失/未初始化时抛 AdapterRuntimeUnavailable。

    鸭子类型判定（`_initialized` 标记），便于受控 stub 验证接线。
    """
    service = provider()
    if service is None or not getattr(service, "_initialized", False):
        raise AdapterRuntimeUnavailable(component)
    return service


def user_ctx(ov_account_id: str, ov_user_id: str) -> RequestContext:
    """ov 映射 → 最小权限 User 上下文（02 §7.3）。"""
    return RequestContext(
        user=UserIdentifier(ov_account_id, ov_user_id),
        role=Role.USER,
    )
