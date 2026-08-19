"""URI 分类与统一授权门（02 §7.5，14 号计划 §97.2）。

所有外部入口在调用 OpenVikingService 前执行同一条链路（02 §7.5）：

```text
认证凭证 -> Principal Resolver -> URI Canonicalizer -> Target Classifier
  -> AuthorizationService（Action + Visibility + Data Scope）
  -> OpenViking RequestContext -> OpenVikingService
```

- `canonicalize_uri`：规范化目标 URI（去尾斜杠/折叠重复斜杠，保持确定性）；
- `classify_target`：分类为 `user_private` / `account_shared` / `internal`；
  分类规则至少覆盖 `viking://user/**`（user_private）、`viking://resources/**`
  与 `viking://agent/skills/**`（account_shared）、`agent/endpoints/tools/
  payments` 与内部文件系统根（internal，默认拒绝）；未知根一律 internal；
- `AuthorizationService`：URI 分类 + 数据范围（02 §7.2）+ TargetPolicy
  动作→Permission 的统一授权门；返回已授权的 `DataAccessContext`
  （canonical_ov_uri 由服务端构造，客户端提交的 visibility/ID 不能单独
  成为授权依据，AC④）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from openviking.server.platform.auth.access import (
    PLATFORM_SUPER_ADMIN_ROLE,
    DataAccessContext,
    authorize_data_access,
)
from openviking.server.platform.errors import (
    AccessDeniedError,
    CanonicalUriMismatchError,
    InvalidTargetError,
)
from openviking.server.platform.target_policy import TargetPolicy

TargetVisibility = Literal["user_private", "account_shared", "internal"]

_VINKING_SCHEME = "viking://"

# internal 根：产品/用户集成默认拒绝（02 §7.5，05 §11.5），
# 除非另有显式控制面 Permission（v0.1 不定义）。
_INTERNAL_ROOTS = (
    "viking://agent/endpoints/",
    "viking://tools/",
    "viking://payments/",
    "viking://internal/",
)


def canonicalize_uri(uri: str) -> str:
    """规范化目标 URI（02 §7.5）。

    - 非 `viking://` 或空目标 → `InvalidTargetError`；
    - 折叠重复 `/`、去尾 `/`（保持确定性，不改变大小写）。
    """
    if not isinstance(uri, str) or not uri.startswith(_VINKING_SCHEME):
        raise InvalidTargetError("INVALID_URI")
    body = uri[len(_VINKING_SCHEME):]
    if not body:
        raise InvalidTargetError("INVALID_URI")
    segments = [part for part in body.split("/") if part != ""]
    canonical = f"{_VINKING_SCHEME}{'/'.join(segments)}"
    return canonical


def classify_target(uri: str) -> tuple[TargetVisibility, str | None]:
    """目标分类（02 §7.5，AC③）。

    返回 `(visibility, subject_ov_user_id)`；`user_private` 时返回 URI 中的
    OpenViking User ID（服务端随后与 IAM 映射做一致性校验），其余为 None。

    - `viking://user/{ov_user_id}/...` → user_private（须与映射 User 一致）；
    - `viking://resources/**`、`viking://agent/skills/**` → account_shared；
    - `viking://agent/endpoints/**`、`tools/**`、`payments/**`、内部根及
      未知根 → internal（产品外部入口默认拒绝）。
    """
    if not uri.startswith(_VINKING_SCHEME):
        raise InvalidTargetError("INVALID_URI")
    rest = uri[len(_VINKING_SCHEME):]
    segments = [s for s in rest.split("/") if s]
    if not segments:
        raise InvalidTargetError("INVALID_URI")
    head = segments[0]
    if head == "user":
        if len(segments) < 2:
            raise InvalidTargetError("INVALID_URI")
        return "user_private", segments[1]
    if head == "resources":
        return "account_shared", None
    if head == "agent":
        if len(segments) >= 2 and segments[1] == "skills":
            return "account_shared", None
        return "internal", None
    if head in ("tools", "payments", "internal") or uri.startswith(_INTERNAL_ROOTS):
        return "internal", None
    # 未知根：默认 internal（产品外部入口默认拒绝，AC③）。
    return "internal", None


@dataclass(frozen=True)
class OVMapper:
    """服务端 IAM 映射（02 §7.2：subject_ov_* 只能由服务端映射得到）。

    输入产品 ID，返回 OpenViking 映射 ID（异步：IamRepository 查询）；
    由 ProductFacadeService 用 IamRepository 装配（客户端不能提交 ov ID
    参与授权）。
    """

    resolve_user: Callable[[uuid.UUID], "object | None"] | None = None
    resolve_account: Callable[[uuid.UUID], "object | None"] | None = None


class AuthorizationService:
    """统一授权门（02 §7.5 + 05 §11.5，AC③④）。

    `authorize()` 完成：canonicalize → classify → 构造 DataAccessContext →
    `authorize_data_access`（数据范围 + URI 一致性）→ TargetPolicy 动作
    →Permission 检查。任何一步失败即拒。

    普通用户接口固定使用 Actor 自己的 Account/User；Account Admin/PSA 接口
    允许指定目标 Subject（`subject_account_id/subject_user_id`），但必须
    先通过数据范围授权，且 Subject 的 ov 映射必须来自 `ov_mapper`。
    """

    def __init__(self, policy: TargetPolicy, ov_mapper: OVMapper | None = None) -> None:
        self._policy = policy
        self._ov_mapper = ov_mapper

    @property
    def policy(self) -> TargetPolicy:
        return self._policy

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
        """统一授权门：返回已授权的 DataAccessContext（canonical URI 由本方法构造）。

        Args:
            action: TargetPolicy 动作（read/write/delete/manage/use/publish）；
            uri: 客户端提交的原始目标 URI（服务端规范化后作为唯一授权依据）；
            object_type: resource/skill（权限映射需要）；
            subject_*: 管理接口指定目标 Subject（须通过数据范围授权）；
                None → Actor 自己的 Account/User。
        """
        canonical = canonicalize_uri(uri)
        visibility, uri_ov_user = classify_target(canonical)
        if visibility == "internal":
            raise AccessDeniedError("INTERNAL_TARGET_DENIED")

        actor_account_id = principal.actor_account_id
        explicit_subject = subject_account_id is not None or subject_user_id is not None
        if not explicit_subject:
            if actor_account_id is None:
                raise AccessDeniedError("SUBJECT_REQUIRED")
            resolved_account_id = actor_account_id
            resolved_user_id = principal.actor_user_id
        else:
            if subject_account_id is None:
                raise AccessDeniedError("SUBJECT_REQUIRED")
            resolved_account_id = subject_account_id
            resolved_user_id = subject_user_id

        if visibility == "account_shared":
            # Account 共享数据没有 Subject User（02 §7.2）；显式提交 User 即拒。
            if subject_user_id is not None:
                raise AccessDeniedError("SUBJECT_MISMATCH")
            resolved_user_id = None
            subject_ov_user_id = None
            ov_account = await self._resolve_ov_account(resolved_account_id)
            if ov_account is None:
                raise AccessDeniedError("SUBJECT_REQUIRED")
            subject_ov_account_id = ov_account

        if visibility == "user_private":
            if resolved_user_id is None:
                raise AccessDeniedError("SUBJECT_REQUIRED")
            if resolved_user_id == principal.actor_user_id and principal.actor_ov_user_id:
                # Subject 为 Actor 自己：直接用 Principal 的映射（认证时已加载）。
                subject_ov_account_id = principal.actor_ov_account_id or ""
                subject_ov_user_id = principal.actor_ov_user_id
                if subject_ov_user_id != uri_ov_user:
                    raise CanonicalUriMismatchError("uri user mismatch with actor mapping")
            else:
                # 管理接口指定他人 Subject：ov 映射必须来自服务端 IAM 映射。
                ov = await self._resolve_ov_user(resolved_user_id)
                if ov is None or ov[1] != uri_ov_user:
                    raise CanonicalUriMismatchError("uri user mismatch with subject mapping")
                subject_ov_account_id, subject_ov_user_id = ov

        access = DataAccessContext(
            actor_user_id=principal.actor_user_id,
            actor_account_id=actor_account_id,
            subject_account_id=resolved_account_id,
            subject_user_id=resolved_user_id,
            subject_ov_account_id=subject_ov_account_id,
            subject_ov_user_id=subject_ov_user_id,
            visibility=visibility,
            canonical_ov_uri=canonical,
            action=action,
            request_id=request_id,
        )
        authorize_data_access(principal, access)

        permission = self._policy.permission_for(
            object_type=object_type,
            visibility=visibility,
            action=action,
            scope=self._scope_of(
                principal,
                visibility=visibility,
                resolved_user_id=resolved_user_id,
            ),
        )
        if permission is None or permission not in principal.permissions:
            raise AccessDeniedError("PERMISSION_NOT_GRANTED")
        return access

    def default_target_uri(self, principal, *, object_type: str) -> str:
        """默认目标规则（05 §11.5）：未显式指定目标时强制 Actor 私有根。"""
        if principal.actor_ov_user_id is None:
            raise AccessDeniedError("SUBJECT_REQUIRED")
        return self._policy.default_target_uri(principal.actor_ov_user_id, object_type=object_type)

    def _scope_of(
        self,
        principal,
        *,
        visibility: str,
        resolved_user_id: uuid.UUID | None,
    ) -> str:
        if principal.actor_account_id is None and PLATFORM_SUPER_ADMIN_ROLE in (
            principal.role_codes or ()
        ):
            return "platform"
        if visibility == "account_shared":
            # 共享对象对 Account 成员统一按 Account 范围授权（普通 User 只读）。
            return "account"
        if resolved_user_id is not None and resolved_user_id == principal.actor_user_id:
            return "self"
        # Account Admin 读取本 Account 其他 User 的私有对象（05 §11.5）。
        return "account"

    async def _resolve_ov_user(self, user_id: uuid.UUID) -> tuple[str, str] | None:
        if self._ov_mapper is None or self._ov_mapper.resolve_user is None:
            return None
        result = self._ov_mapper.resolve_user(user_id)
        if hasattr(result, "__await__"):
            result = await result
        return result  # type: ignore[return-value]

    async def _resolve_ov_account(self, account_id: uuid.UUID) -> str | None:
        if self._ov_mapper is None or self._ov_mapper.resolve_account is None:
            return None
        result = self._ov_mapper.resolve_account(account_id)
        if hasattr(result, "__await__"):
            result = await result
        return result  # type: ignore[return-value]
