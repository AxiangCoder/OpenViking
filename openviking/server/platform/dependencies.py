"""认证/授权 FastAPI 依赖（05 §11.1 依赖链，14 号计划 §96.3）。

- `get_current_principal`：统一 Principal Resolver（P1-E4）——
  `__Host-ov_session` Cookie 优先（失效不自动回退 Bearer），
  `Authorization: Bearer` 与 `X-Api-Key` 同一解析器（03 §8.4）；
  Session 路径每次请求顺延 `last_seen_at`/空闲到期（03 §8.1，04 §10.7）；
- `require_permission`：403 `PERMISSION_NOT_GRANTED`（权限实时计算，P1-E2）；
- `verify_csrf`：非安全方法校验 Origin/Referer + `X-CSRF-Token`（03 §8.2）；
  无 Token 写请求被拒；非 Session 凭据（session_id 为空，如 API Key）不能
  执行 CSRF 写（验收 ⑧，14 号计划 §96.4）；
- `apply_session_cookie`：登录/改密轮换后下发新 Cookie（05 §12.3 回填发现）。

app.state 注入（create_app 挂载时由 P5-E1 统一设置，本模块不持全局单例）：
- `iam_repository`：P1-E1 PostgresIamRepository；
- `iam_rbac_service`：P1-E2 RbacService；
- `iam_auth_service`：本 Epic AuthService（持有进程内限流器，必须单例）。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.csrf import origin_allowed
from openviking.server.platform.auth.principals import (
    AuthenticatedUserPrincipal,
    resolve_principal,
)
from openviking.server.platform.auth.service import AuthService
from openviking.server.platform.auth.sessions import SessionService
from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.db import get_session
from openviking.server.platform.errors import AuthenticationError
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.iam.service import RbacService
from openviking.server.platform.models import IamSession

CSRF_INVALID = "CSRF_INVALID"
PERMISSION_NOT_GRANTED = "PERMISSION_NOT_GRANTED"


@dataclass(frozen=True)
class IamServices:
    """app.state 中注入的 IAM 依赖集合（create_app 挂载时装配，P5-E1）。"""

    repository: IamRepository
    rbac: RbacService
    auth: AuthService


def get_iam_services(request: Request) -> IamServices:
    return IamServices(
        repository=request.app.state.iam_repository,
        rbac=request.app.state.iam_rbac_service,
        auth=request.app.state.iam_auth_service,
    )


async def get_current_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUserPrincipal:
    """统一认证依赖（05 §11.4：Session/API Key 产出同一 Principal）。

    - Cookie 优先、失效不自动回退 Bearer（03 §8.4）；
    - `Authorization: Bearer` 与 `X-Api-Key` 进入同一解析器；
    - Session 路径每次请求顺延空闲到期（04 §10.7）。
    """
    services = get_iam_services(request)
    raw_token = request.cookies.get(platform_config.cookie_name)
    bearer = None
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    try:
        principal = await resolve_principal(
            session,
            services.repository,
            services.rbac,
            session_token=raw_token or None,
            bearer_key=bearer,
            x_api_key=request.headers.get("x-api-key"),
        )
    except AuthenticationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": exc.code}) from exc
    if principal.session_id is not None:
        await SessionService(services.repository, platform_config).touch_session(
            session, principal.session_id
        )
        await session.commit()
    return principal


def require_permission(permission_code: str):
    """权限守卫：403 `PERMISSION_NOT_GRANTED`（有效权限来自 P1-E2，实时计算）。"""

    def checker(
        principal: AuthenticatedUserPrincipal = Depends(get_current_principal),
    ) -> AuthenticatedUserPrincipal:
        if permission_code not in principal.permissions:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail={"code": PERMISSION_NOT_GRANTED}
            )
        return principal

    return checker


async def verify_csrf(
    request: Request,
    principal: AuthenticatedUserPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """写请求 CSRF 校验（03 §8.2）：Origin/Referer + X-CSRF-Token。"""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not origin_allowed(request, platform_config):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": CSRF_INVALID})
    if principal.session_id is None:
        # API Key/OAuth 等非 Session 凭据不能执行 CSRF 写（03 §8.2/§8.4；
        # P1-E4 api_key principal 同样落入本分支）
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": CSRF_INVALID})
    provided = request.headers.get("x-csrf-token", "")
    row = (
        await session.execute(
            select(IamSession.csrf_secret_hash).where(IamSession.id == principal.session_id)
        )
    ).scalar_one_or_none()
    if row is None or not SessionService.verify_csrf_token(row, provided):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": CSRF_INVALID})


async def verify_integration_write(
    request: Request,
    principal: AuthenticatedUserPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """集成写入口校验（P2-E5：11 §70.6 Session 写链路）。

    浏览器 Session 凭证必须通过完整 CSRF 校验（03 §8.2）；User API Key /
    OAuth 委托凭证不携带 Cookie，不存在浏览器伪造风险，直接放行
    （浏览器无法自动携带 Key/Token，05 §11.4）。Origin 校验对两种凭据
    都执行（防第三方站点在 Session 场景的跨站写）。
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not origin_allowed(request, platform_config):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": CSRF_INVALID})
    if principal.session_id is None:
        return  # API Key / OAuth：非浏览器凭据，无需 CSRF Token
    provided = request.headers.get("x-csrf-token", "")
    row = (
        await session.execute(
            select(IamSession.csrf_secret_hash).where(IamSession.id == principal.session_id)
        )
    ).scalar_one_or_none()
    if row is None or not SessionService.verify_csrf_token(row, provided):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": CSRF_INVALID})


def apply_session_cookie(
    response: Response,
    raw_token: str,
    config: PlatformConfig = platform_config,
) -> None:
    """登录/改密轮换后下发 Cookie（03 §8.1）：__Host-ov_session，
    HttpOnly/Secure/SameSite=Lax/Path=/，Max-Age=绝对 TTL。"""
    response.set_cookie(
        key=config.cookie_name,
        value=raw_token,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
        max_age=config.session_absolute_ttl_seconds,
    )


def clear_session_cookie(response: Response, config: PlatformConfig = platform_config) -> None:
    response.delete_cookie(key=config.cookie_name, path="/")


def request_ip(request: Request) -> str:
    """客户端 IP（限流/审计用；TestClient 下为 testclient）。"""
    if request.client is None:
        return "unknown"
    return request.client.host
