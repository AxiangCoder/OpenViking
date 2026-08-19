"""Platform 产品 API 路由（05 §12 `routers/`）。

P1-E3 交付 auth.py；P1-E4 交付 me.py（用户 API Key 管理，05 §12.4）；
P1-E5 交付 admin.py 与 platform.py（IAM 管理 API 与审计基础，14 号计划 §96.5）；
P2-E3 交付 resources.py（Resource 产品 API：me/account/platform + admin 成员
只读 + `/activity`/`/recycle-bin` 跨对象聚合，09 §46、05 §12.5/§12.6）；
P2-E4 交付 skills.py（10 §61.1–§61.5：me/account/admin/platform Skill 与
me/skill-configs，14 号计划 §97.4）；
P2-E5 交付 sessions.py（Session/Search/Dashboard 产品 API，11 §74，05 §12.5）
与 member_data.py（admin/platform 成员只读 Session/Search，11 §73）；
P2-E6b 交付 oauth.py（MCP OAuth 产品端点，05 §12.4，14 号计划 §97.7）；
`create_app()` 真实挂载留 P5-E1（14 号计划 §96.3「同进程集成基线」）。
"""

from openviking.server.platform.routers.admin import router as admin_router
from openviking.server.platform.routers.auth import router as auth_router
from openviking.server.platform.routers.me import router as me_router
from openviking.server.platform.routers.member_data import router as member_data_router
from openviking.server.platform.routers.oauth import router as oauth_router
from openviking.server.platform.routers.platform import router as platform_router
from openviking.server.platform.routers.resources import router as resources_router
from openviking.server.platform.routers.sessions import router as sessions_router
from openviking.server.platform.routers.skills import router as skills_router

__all__ = [
    "auth_router",
    "me_router",
    "admin_router",
    "platform_router",
    "resources_router",
    "skills_router",
    "sessions_router",
    "member_data_router",
    "oauth_router",
]
