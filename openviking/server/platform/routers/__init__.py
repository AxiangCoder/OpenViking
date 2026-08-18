"""Platform 产品 API 路由（05 §12 `routers/`）。

P1-E3 交付 auth.py；P1-E4 交付 me.py（用户 API Key 管理，05 §12.4）；
`create_app()` 真实挂载留 P5-E1（14 号计划 §96.3「同进程集成基线」）。
"""

from openviking.server.platform.routers.auth import router as auth_router
from openviking.server.platform.routers.me import router as me_router

__all__ = ["auth_router", "me_router"]
