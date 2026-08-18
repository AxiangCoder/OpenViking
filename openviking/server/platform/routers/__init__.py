"""Platform 产品 API 路由（05 §12 `routers/`）。

P1-E3 交付 auth.py；P1-E5 交付 admin.py 与 platform.py（IAM 管理 API 与审计
基础，14 号计划 §96.5）。`create_app()` 真实挂载留 P5-E1。
"""

from openviking.server.platform.routers.admin import router as admin_router
from openviking.server.platform.routers.auth import router as auth_router
from openviking.server.platform.routers.platform import router as platform_router

__all__ = ["auth_router", "admin_router", "platform_router"]
