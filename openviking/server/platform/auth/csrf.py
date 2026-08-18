"""CSRF 防护策略（03 §8.2：SameSite=Lax + Origin/Referer + X-CSRF-Token）。

- Cookie 属性（HttpOnly/Secure/SameSite=Lax/Path=/）由 `apply_session_cookie`
  （platform/dependencies.py）保证；
- 非 GET/HEAD/OPTIONS 写请求必须携带与登录 Session 绑定的 `X-CSRF-Token`
  （`csrf_secret_hash` 常量时间校验，04 §10.7）；无 Token 即拒（spike
  deps.py:83 的正式测试断言，14 号计划 §96.3 验收⑥）；
- 写请求同时校验 `Origin`（缺失时回退 `Referer`）属于允许的产品源
  （`OV_CSRF_ALLOWED_ORIGINS`；为空时按请求 Host 同源）；
- 非 Session 凭据（API Key/OAuth）`session_id` 为空，不能执行 CSRF 写
  （03 §8.2/§8.4；P1-E4 解析出 api_key principal 后同样被拒）。
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request

from openviking.server.platform.config import PlatformConfig, platform_config

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def origin_allowed(request: Request, config: PlatformConfig = platform_config) -> bool:
    """Origin/Referer 属于允许的产品源；两者都缺失视为非浏览器客户端，放行
    （X-CSRF-Token 仍是写请求的硬门槛，见 dependencies.verify_csrf）。"""
    candidate = request.headers.get("origin") or request.headers.get("referer")
    if not candidate:
        return True
    if config.csrf_allowed_origins:
        return candidate in config.csrf_allowed_origins
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    candidate_origin = f"{parsed.scheme}://{parsed.netloc}"
    return candidate_origin == f"{request.url.scheme}://{request.url.netloc}"
