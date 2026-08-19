"""低层入口统一 TargetPolicy 守卫（05 §11.5，14 号计划 §97.6）。

低层 `/api/v1` Router 与 MCP Tool 在调用 OpenVikingService 前统一执行：

- 所有「会改变」动作（add_resource/write/mkdir/mv/set_tags/归档/导入/恢复/
  批量）经 `TargetPolicy.low_level_action` 映射为 write/delete Permission
  （05 §11.5 最低映射规则）；
- 未显式目标强制 Actor 私有根（API Key/OAuth 不能借源码默认值把个人内容
  写入共享区）；
- 显式共享目标触发共享写权限检查（普通 User 403）；
- `mv` 的源与目标都检查，且跨可见性移动拒绝（必须走"复制/发布为新对象"
  业务动作，不能作为普通 mv 放行）；
- 拒绝与成功写动作都写脱敏审计（插件以 Key 归属者身份读写审计，AC④）。

平台模式（`platform_enabled=True`）由 mount.py 装配 `LowLevelPolicyGuard`
到 `app.state.platform_lowlevel_guard`；非平台模式保持既有行为不变。
"""

from __future__ import annotations

import uuid
from typing import Awaitable, Callable

from fastapi import HTTPException, Request, status

from openviking.server.platform.auth.access import DataAccessContext
from openviking.server.platform.auth.uri_policy import (
    AuthorizationService,
    canonicalize_uri,
    classify_target,
)
from openviking.server.platform.errors import (
    AccessDeniedError,
    CanonicalUriMismatchError,
    CrossVisibilityMoveError,
    InvalidTargetError,
    LowLevelGuardError,
    PlatformError,
)
from openviking.server.platform.target_policy import TargetPolicy

# 审计写入协议（mount.py 注入真实实现；测试可注入记录型 fake）。
AuditFn = Callable[
    ...,
    Awaitable[None],
]


def object_type_for_uri(uri: str) -> str | None:
    """URI → 对象类型（resource/skill；未知根 None，权限映射默认拒绝）。

    - `viking://resources/**` 与 `viking://user/*/resources/**` → resource；
    - `viking://agent/skills/**` 与 `viking://user/*/skills/**` → skill；
    - 其余（internal/未知根）→ None。
    """
    try:
        canonical = canonicalize_uri(uri)
    except InvalidTargetError:
        return None
    visibility, ov_user = classify_target(canonical)
    if visibility == "internal":
        return None
    segments = [s for s in canonical[len("viking://"):].split("/") if s]
    if visibility == "user_private" and len(segments) >= 3:
        return "resource" if segments[2] == "resources" else ("skill" if segments[2] == "skills" else None)
    if visibility == "account_shared" and segments:
        if segments[0] == "resources":
            return "resource"
        if segments[0] == "agent" and len(segments) >= 2 and segments[1] == "skills":
            return "skill"
    return None


def lowlevel_guard_for(request: Request) -> "LowLevelPolicyGuard | None":
    """平台模式下返回低层守卫；非平台模式返回 None（保持既有行为）。

    `platform_enabled=True` 但守卫未装配 → RuntimeError（装配遗漏是服务端
    错误，不能静默放行）。
    """
    if not getattr(request.app.state, "platform_enabled", False):
        return None
    guard = getattr(request.app.state, "platform_lowlevel_guard", None)
    if guard is None:
        raise RuntimeError("platform low-level guard not initialized")
    return guard


def principal_from_request(request: Request):
    """当前请求的 IAM Principal（平台插件在 `resolve_identity` 时写入）。

    `request.state.platform_principal` 由 PlatformIamAuthPlugin 设置；
    缺失即视为守卫不可用（安全起见直接拒绝）。
    """
    principal = getattr(request.state, "platform_principal", None)
    if principal is None:
        raise LowLevelGuardError("PRINCIPAL_REQUIRED")
    return principal


def _http_for(exc: PlatformError) -> HTTPException:
    if isinstance(exc, InvalidTargetError):
        code = getattr(exc, "reason", "INVALID_URI")
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": code})
    if isinstance(exc, CanonicalUriMismatchError):
        # 伪造 URI 与 Subject 不一致：403（不区分失败原因，防枚举）
        return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "URI_MISMATCH"})
    code = getattr(exc, "reason", getattr(exc, "code", "ACCESS_DENIED"))
    return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": code})


class LowLevelPolicyGuard:
    """低层入口守卫：TargetPolicy 写动作映射 + 默认目标 + mv 规则 + 审计。

    所有方法以 `principal`（AuthenticatedUserPrincipal）为调用者；URI 在
    服务端规范化/分类，客户端提交的 visibility/ID 不能单独成为授权依据
    （02 §7.2，AC④）。
    """

    def __init__(
        self,
        authorization: AuthorizationService,
        *,
        audit: AuditFn | None = None,
    ) -> None:
        self._authorization = authorization
        self._audit = audit

    @property
    def authorization(self) -> AuthorizationService:
        return self._authorization

    @property
    def policy(self) -> TargetPolicy:
        return self._authorization.policy

    def default_target_uri(self, principal, *, object_type: str) -> str:
        """默认目标规则（05 §11.5）：未显式目标强制 Actor 私有根。"""
        return self._authorization.default_target_uri(principal, object_type=object_type)

    async def authorize(
        self,
        principal,
        *,
        action: str,
        uri: str,
        object_type: str | None = None,
        subject_account_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
        request_id: str = "",
    ) -> DataAccessContext:
        """低层动作统一授权门（canonicalize → classify → data scope → permission）。

        - `action` 为低层动作名（add_resource/write/mkdir/mv/set_tags/...），
          经 TargetPolicy 映射为 write/delete/read；未映射动作默认拒绝；
        - 拒绝与成功写动作（write/delete）都写脱敏审计（AC④）。

        Raises:
            InvalidTargetError: URI 非法/内部根；
            CanonicalUriMismatchError: URI 与 Subject 不一致（伪造）；
            AccessDeniedError / LowLevelGuardError: 越权或未映射动作。
        """
        policy_action = self.policy.low_level_action(action)
        if policy_action is None:
            raise LowLevelGuardError("ACTION_NOT_MAPPED")
        try:
            access = await self._authorization.authorize(
                principal,
                action=policy_action,
                uri=uri,
                object_type=object_type,
                subject_account_id=subject_account_id,
                subject_user_id=subject_user_id,
                request_id=request_id,
            )
        except (AccessDeniedError, InvalidTargetError, CanonicalUriMismatchError) as exc:
            await self._audit_denied(
                principal,
                action=action,
                reason=getattr(exc, "reason", "DENIED"),
                uri=uri,
                request_id=request_id,
            )
            raise
        if policy_action in ("write", "delete"):
            await self._audit_success(principal, action=action, access=access, request_id=request_id)
        return access

    async def authorize_default_or_explicit(
        self,
        principal,
        *,
        action: str,
        explicit_uri: str | None,
        object_type: str,
        request_id: str = "",
    ) -> DataAccessContext:
        """默认目标规则（05 §11.5，AC①）。

        - 未显式指定目标 → 服务端强制 Actor 私有根；
        - 显式目标（可能为 Account 共享）→ 触发共享写权限检查（普通 User 403）。
        """
        if explicit_uri:
            return await self.authorize(
                principal,
                action=action,
                uri=explicit_uri,
                object_type=object_type,
                request_id=request_id,
            )
        uri = self.default_target_uri(principal, object_type=object_type)
        return await self.authorize(
            principal, action=action, uri=uri, object_type=object_type, request_id=request_id
        )

    async def authorize_move(
        self,
        principal,
        *,
        from_uri: str,
        to_uri: str,
        object_type: str | None = None,
        request_id: str = "",
    ) -> tuple[DataAccessContext, DataAccessContext]:
        """`mv` 守卫（AC②）：源与目标都经 TargetPolicy；跨可见性移动拒绝。

        源需要 read+write（移动=从源移除并写入目标），目标需要 write；
        源与目标 `visibility` 必须一致（05 §11.5：跨可见性移动不走普通 mv）。
        """
        src = await self.authorize(
            principal,
            action="read",
            uri=from_uri,
            object_type=object_type,
            request_id=request_id,
        )
        await self.authorize(
            principal,
            action="write",
            uri=from_uri,
            object_type=object_type,
            request_id=request_id,
        )
        dst = await self.authorize(
            principal,
            action="write",
            uri=to_uri,
            object_type=object_type,
            request_id=request_id,
        )
        if src.visibility != dst.visibility:
            raise CrossVisibilityMoveError()
        return src, dst

    # ── 审计（AC④：拒绝与成功写动作都记录，Actor=Key 归属者）──

    async def _audit_denied(
        self,
        principal,
        *,
        action: str,
        reason: str,
        uri: str,
        request_id: str,
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit(
                principal,
                action=f"lowlevel.{action}.denied",
                result="denied",
                reason=reason,
                target_uri=uri,
                request_id=request_id,
                metadata={"target_uri": uri},
            )
        except Exception:
            # 审计失败不阻断操作（05 §11.5：审计为可观测性而非数据面）
            pass

    async def _audit_success(
        self,
        principal,
        *,
        action: str,
        access: DataAccessContext,
        request_id: str,
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit(
                principal,
                action=f"lowlevel.{action}",
                result="success",
                reason=None,
                target_uri=access.canonical_ov_uri,
                request_id=request_id,
                metadata={"visibility": access.visibility},
            )
        except Exception:
            pass


# ── Router 便捷入口（非平台模式 no-op，保持既有行为）──


def guard_active(request: Request) -> bool:
    """平台模式下低层守卫是否激活（用于需要逐项守卫的批量入口）。"""
    return lowlevel_guard_for(request) is not None


async def guard_request(
    request: Request,
    *,
    action: str,
    uri: str,
    object_type: str | None = None,
    request_id: str = "",
) -> DataAccessContext | None:
    """低层写守卫便捷入口：平台模式授权（拒则抛 HTTPException），否则 no-op。"""
    guard = lowlevel_guard_for(request)
    if guard is None:
        return None
    try:
        return await guard.authorize(
            principal_from_request(request),
            action=action,
            uri=uri,
            object_type=object_type,
            request_id=request_id,
        )
    except PlatformError as exc:
        raise _http_for(exc) from exc


async def guard_move_request(
    request: Request,
    *,
    from_uri: str,
    to_uri: str,
    object_type: str | None = None,
    request_id: str = "",
) -> tuple[DataAccessContext, DataAccessContext] | None:
    """`mv` 守卫便捷入口（跨可见性移动 403）。"""
    guard = lowlevel_guard_for(request)
    if guard is None:
        return None
    try:
        return await guard.authorize_move(
            principal_from_request(request),
            from_uri=from_uri,
            to_uri=to_uri,
            object_type=object_type,
            request_id=request_id,
        )
    except PlatformError as exc:
        raise _http_for(exc) from exc
