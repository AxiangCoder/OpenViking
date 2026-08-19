"""真实 Skill 私密配置适配器（10 §53.3，08 §28.1，P5-E1 接线）。

`SkillConfigsAdapter` 协议（providers/skills/configs.py）的真实实现：对接
`UserPrivacyConfigService`（openviking/privacy/service.py，`service.privacy_configs`）。
category 固定 `skills`，target_key = skill_id（UUID 字符串）。

语义与 Fake 对齐：未配置 → None；内容规范化相等复用当前版本（不产生空版本）；
activate 校验版本存在；返回值一律为脱敏快照（DTO 层不接触 Secret 明文）。
"""

from __future__ import annotations

from typing import Optional

from openviking.server.platform.adapters.runtime import (
    ServiceProvider,
    default_service_provider,
    require_service,
    user_ctx,
)
from openviking.server.platform.skills.configs import SkillConfigSnapshot, mask_value

SKILL_CONFIG_CATEGORY = "skills"


class RealSkillConfigsAdapter:
    """真实 Skill 私密配置存储（UserPrivacyConfigService，惰性运行时解析）。"""

    def __init__(
        self,
        service_provider: Optional[ServiceProvider] = None,
    ) -> None:
        self._provider: ServiceProvider = service_provider or default_service_provider()

    def _privacy(self):
        service = require_service(self._provider, "skill_configs")
        privacy = getattr(service, "privacy_configs", None)
        if privacy is None:
            from openviking.server.platform.adapters.runtime import AdapterRuntimeUnavailable

            raise AdapterRuntimeUnavailable("skill_configs")
        return service, privacy

    async def get(self, *, owner_ov_user_id: str, skill_id: str) -> SkillConfigSnapshot | None:
        service, privacy = self._privacy()
        ov_account_id = await self._resolve_account(service, owner_ov_user_id)
        ctx = user_ctx(ov_account_id, owner_ov_user_id)
        if not await privacy.exists(ctx, SKILL_CONFIG_CATEGORY, skill_id):
            return None
        meta = await privacy.get_meta(ctx, SKILL_CONFIG_CATEGORY, skill_id)
        current = await privacy.get_current(ctx, SKILL_CONFIG_CATEGORY, skill_id)
        versions = await privacy.list_versions(ctx, SKILL_CONFIG_CATEGORY, skill_id)
        return SkillConfigSnapshot(
            skill_id=skill_id,
            configured=True,
            active_version=meta.active_version if meta else None,
            latest_version=meta.latest_version if meta else None,
            versions=list(versions),
            masked_values={
                k: mask_value(v) for k, v in (current.values if current else {}).items()
            },
        )

    async def upsert(
        self,
        *,
        owner_ov_user_id: str,
        skill_id: str,
        values: dict,
        updated_by: str,
        change_reason: str = "",
    ) -> SkillConfigSnapshot:
        if not isinstance(values, dict) or not values:
            raise ValueError("privacy values must be a non-empty dict")
        service, privacy = self._privacy()
        ov_account_id = await self._resolve_account(service, owner_ov_user_id)
        ctx = user_ctx(ov_account_id, owner_ov_user_id)
        version = await privacy.upsert(
            ctx,
            SKILL_CONFIG_CATEGORY,
            skill_id,
            values,
            updated_by=updated_by,
            change_reason=change_reason,
        )
        meta = await privacy.get_meta(ctx, SKILL_CONFIG_CATEGORY, skill_id)
        versions = await privacy.list_versions(ctx, SKILL_CONFIG_CATEGORY, skill_id)
        return SkillConfigSnapshot(
            skill_id=skill_id,
            configured=True,
            active_version=version.version,
            latest_version=(meta.latest_version if meta else version.version),
            versions=list(versions),
            masked_values={k: mask_value(v) for k, v in version.values.items()},
        )

    async def activate(
        self,
        *,
        owner_ov_user_id: str,
        skill_id: str,
        version: int,
        updated_by: str,
    ) -> SkillConfigSnapshot:
        service, privacy = self._privacy()
        ov_account_id = await self._resolve_account(service, owner_ov_user_id)
        ctx = user_ctx(ov_account_id, owner_ov_user_id)
        activated = await privacy.activate_version(
            ctx,
            SKILL_CONFIG_CATEGORY,
            skill_id,
            version,
            updated_by=updated_by,
        )
        meta = await privacy.get_meta(ctx, SKILL_CONFIG_CATEGORY, skill_id)
        versions = await privacy.list_versions(ctx, SKILL_CONFIG_CATEGORY, skill_id)
        return SkillConfigSnapshot(
            skill_id=skill_id,
            configured=True,
            active_version=activated.version,
            latest_version=(meta.latest_version if meta else activated.version),
            versions=list(versions),
            masked_values={k: mask_value(v) for k, v in activated.values.items()},
        )

    async def _resolve_account(self, service, ov_user_id: str) -> str:
        """ov_user_id → ov_account_id（进程级缓存；IAM 未映射时抛运行时错误）。"""
        from openviking.server.platform.db import session_factory
        from openviking.server.platform.iam import PostgresIamRepository

        async with session_factory() as session:
            account_id = await PostgresIamRepository().get_account_id_for_ov_user(
                session, ov_user_id
            )
            account = (
                await PostgresIamRepository().get_account(session, account_id)
                if account_id is not None
                else None
            )
        if account is None or account.ov_account_id is None:
            from openviking.server.platform.adapters.runtime import AdapterRuntimeUnavailable

            raise AdapterRuntimeUnavailable(f"skill_configs (ov_user {ov_user_id} not mapped)")
        return account.ov_account_id
