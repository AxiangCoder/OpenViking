"""ProductFacadeService 骨架（05 §11.2，14 号计划 §97.2）。

本层负责（E3–E6 冻结点，后续 Epic 只在此扩展、不改已有签名）：

- 把产品 ID 转换成受控 OpenViking URI；
- 目标 URI 规范化/分类（user_private/account_shared/internal），校验 URI、
  Account、Owner User 与 PostgreSQL 引用记录一致；
- 普通用户接口固定使用 Actor 自己的 Account/User；Account Admin/PSA 接口
  允许指定目标 Subject，但必须先执行 account/platform Scope 授权；
- 注入 `RequestContext`（to_ov_context / to_ov_account_context，02 §7.3）；
- 对外创建先经 Content Registry（`platform_content_refs` provisioning →
  OpenViking 调用 → active/failed，05 §11.5，AC⑤）；
- 查询 `iam_deletion_jobs`，保证软删除对象不出现在正常列表；
- 统一处理等待、任务、分页和错误（E3+ 端点逐层落地）。

v0.1 骨架交付：装配 + 授权门 + 上下文转换 + 注册表入口。完整业务端点
（Resource/Skill/Session/Search CRUD）在 E3–E5 增量实现。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.identity import RequestContext
from openviking.server.platform.auth.access import DataAccessContext
from openviking.server.platform.auth.ov_context import to_ov_account_context, to_ov_context
from openviking.server.platform.auth.uri_policy import AuthorizationService, OVMapper
from openviking.server.platform.registry.service import ContentRegistryService

if TYPE_CHECKING:
    from openviking.server.platform.registry.repository import RegistryRepository


class OpenVikingControlPlane(Protocol):
    """受控 OpenViking 调用适配器（05 §11.2 注入 RequestContext 的执行面）。

    P2-E1 的 ControlPlaneAdapter 负责 Provisioning namespace 初始化；
    内容写入/读取适配在 E3–E5 按对象接入，本 Epic 只声明骨架协议。
    """

    async def call(self, request_context: RequestContext, operation: str, **kwargs): ...


class ProductFacadeService:
    """产品门面骨架：授权门 + 上下文转换 + Content Registry 编排。"""

    def __init__(
        self,
        *,
        authorization: AuthorizationService,
        registry: ContentRegistryService,
        control_plane: OpenVikingControlPlane | None = None,
        registry_store: "RegistryRepository | None" = None,
    ) -> None:
        self.authorization = authorization
        self.registry = registry
        self.control_plane = control_plane
        if registry_store is None:
            from openviking.server.platform.registry.repository import RegistryRepository

            registry_store = RegistryRepository()
        self.registry_store = registry_store

    # ── 授权门（02 §7.5：统一入口供 E3–E6a 复用）──

    async def authorize_uri(
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
        """统一授权门（canonicalize → classify → data scope → permission）。"""
        return await self.authorization.authorize(
            principal,
            action=action,
            uri=uri,
            object_type=object_type,
            subject_account_id=subject_account_id,
            subject_user_id=subject_user_id,
            request_id=request_id,
        )

    def default_target_uri(self, principal, *, object_type: str) -> str:
        """默认目标规则（05 §11.5）：未显式目标强制 Actor 私有根。"""
        return self.authorization.default_target_uri(principal, object_type=object_type)

    # ── OpenViking 上下文注入（02 §7.3）──

    def to_ov_context(self, principal, access: DataAccessContext) -> RequestContext:
        """User 私有数据最小权限上下文（`to_ov_context`，02 §7.3）。"""
        return to_ov_context(principal, access)

    def to_ov_account_context(self, principal, access: DataAccessContext) -> RequestContext:
        """Account 共享数据最小权限上下文（02 §7.3，跨 Account 固定
        `platform-gateway` 执行占位）。"""
        return to_ov_account_context(principal, access)

    # ── 装配工具（05 §11.2：产品 ID → 受控 URI 的映射入口）──

    @staticmethod
    def build_ov_mapper(repo, session_factory) -> OVMapper:
        """用 IamRepository 装配服务端 ov 映射（02 §7.2：客户端不能提交 ov ID）。

        `resolve_user/resolve_account` 异步查询 IAM 映射；Subject 为 Actor
        自己时直接使用 Principal 字段（不走映射，减少一次查询）。
        """

        async def resolve_user(user_id: uuid.UUID):
            async with session_factory() as s:
                user = await repo.get_user(s, user_id)
                if user is None or user.ov_user_id is None or user.account_id is None:
                    return None
                account = await repo.get_account(s, user.account_id)
                if account is None:
                    return None
                return (account.ov_account_id, user.ov_user_id)

        async def resolve_account(account_id: uuid.UUID):
            async with session_factory() as s:
                account = await repo.get_account(s, account_id)
                if account is None:
                    return None
                return account.ov_account_id

        return OVMapper(resolve_user=resolve_user, resolve_account=resolve_account)

    # ── 删除任务可见性（05 §11.2：软删除对象不出现在正常列表）──

    async def is_in_recycle_window(
        self, session: AsyncSession, resource_type: str, resource_id: str
    ) -> bool:
        """目标对象是否处于删除回收期（E3+ 列表/详情过滤使用）。"""
        job = await self.registry_store.get_active_deletion_job(
            session, resource_type, str(resource_id)
        )
        return job is not None
