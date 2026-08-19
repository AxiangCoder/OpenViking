"""Resource Watch 配置（09 §43.2，04 §10.10 关联约束）。

- Watch 持久化只保存 Resource ID 与调度参数，**不保存明文远程 URL**
  （AC⑥）；执行时由 ResourceService/Product Facade 解析并临时解密来源；
- `interval_minutes` 只接受 Capabilities 预设（服务端强制，AC⑩）；
- 暂停保留周期与配置；恢复重新计算下次执行时间，不补跑暂停期间次数；
- 删除 Watch 只删除自动同步配置，不删除 Resource；
- Worker 不能继承创建者过期的登录 Session，也不能借用用户 API Key
  （09 §43.2：Watch Worker 使用系统执行身份，`actor_system_component`
  = `watch-scheduler`，审计同时保存配置/触发 Actor 与系统组件）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.config import PlatformConfig, platform_config
from openviking.server.platform.errors import (
    ResourceError,
    ResourceWatchError,
)
from openviking.server.platform.iam.repository import IamRepository
from openviking.server.platform.models import PlatformContentRef, PlatformResourceWatch
from openviking.server.platform.resource.repository import ResourceRepository

WATCH_SCHEDULER_COMPONENT = "watch-scheduler"

# 09 §43.2：建议预设（Capabilities 返回；服务端强制校验）
DEFAULT_WATCH_PRESETS = (60, 360, 720, 1440, 10080)


class WatchService:
    """Watch 配置 CRUD（触发/调度执行在 ResourceService，本类只做配置与审计）。"""

    def __init__(
        self,
        *,
        iam_repo: IamRepository,
        store: ResourceRepository | None = None,
        config: PlatformConfig = platform_config,
    ) -> None:
        self._iam = iam_repo
        self._store = store or ResourceRepository()
        self._config = config

    @property
    def presets(self) -> tuple[int, ...]:
        return tuple(self._config.watch_interval_presets or DEFAULT_WATCH_PRESETS)

    # ── DTO（09 §43.2 字段）──

    def dto(self, watch: PlatformResourceWatch | None) -> dict:
        if watch is None:
            return {"state": "not_configured"}
        return {
            "state": watch.state,
            "interval_minutes": watch.interval_minutes,
            "last_run_at": watch.last_run_at.isoformat() if watch.last_run_at else None,
            "next_run_at": watch.next_run_at.isoformat() if watch.next_run_at else None,
            "last_result": watch.last_result,
            "last_error": watch.last_error,
            "processing_instruction": watch.processing_instruction,
        }

    # ── 查询 ──

    async def get_config(self, session: AsyncSession, resource_id: uuid.UUID) -> dict:
        return self.dto(await self._store.get_watch(session, resource_id))

    # ── 配置（09 §43.2：启用/更新）──

    async def configure(
        self,
        session: AsyncSession,
        *,
        principal,
        ref: PlatformContentRef,
        interval_minutes: int,
        instruction: str | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> PlatformResourceWatch:
        """创建或更新 Watch（写权限由调用方校验；来源稳定性在调用方校验）。"""
        now = now or datetime.now(timezone.utc)
        if not self._config.watch_enabled:
            raise ResourceWatchError("RESOURCE_WATCH_UNAVAILABLE")
        if interval_minutes not in self.presets:
            raise ResourceWatchError("RESOURCE_WATCH_UNAVAILABLE")

        watch = await self._store.get_watch(session, ref.id)
        created = watch is None
        if watch is None:
            watch = PlatformResourceWatch(
                account_id=ref.account_id,
                resource_id=ref.id,
                state="active",
                interval_minutes=interval_minutes,
                processing_instruction=instruction,
                created_by=principal.actor_user_id,
                next_run_at=now + timedelta(minutes=interval_minutes),
            )
            await self._store.insert_watch(session, watch)
        else:
            watch.interval_minutes = interval_minutes
            watch.state = "active"
            watch.processing_instruction = instruction
            watch.last_error = None
            watch.next_run_at = now + timedelta(minutes=interval_minutes)
            await self._store.update_watch(session, watch)

        await self._audit_watch(
            session,
            principal=principal,
            ref=ref,
            action="resource.watch.create" if created else "resource.watch.update",
            request_id=request_id,
            metadata={
                "interval_minutes": interval_minutes,
                "state": watch.state,
            },
        )
        return watch

    async def pause(
        self,
        session: AsyncSession,
        *,
        principal,
        ref: PlatformContentRef,
        request_id: str | None = None,
    ) -> PlatformResourceWatch:
        """暂停：保留周期与配置，不再调度（09 §43.2）。"""
        watch = await self._require_watch(session, ref.id)
        watch.state = "paused"
        await self._store.update_watch(session, watch)
        await self._audit_watch(
            session, principal=principal, ref=ref, action="resource.watch.pause", request_id=request_id
        )
        return watch

    async def resume(
        self,
        session: AsyncSession,
        *,
        principal,
        ref: PlatformContentRef,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> PlatformResourceWatch:
        """恢复：重新计算下次执行时间，不补跑暂停期间次数（09 §43.2）。"""
        now = now or datetime.now(timezone.utc)
        watch = await self._require_watch(session, ref.id)
        watch.state = "active"
        watch.next_run_at = now + timedelta(minutes=watch.interval_minutes)
        watch.last_error = None
        await self._store.update_watch(session, watch)
        await self._audit_watch(
            session, principal=principal, ref=ref, action="resource.watch.resume", request_id=request_id
        )
        return watch

    async def delete(
        self,
        session: AsyncSession,
        *,
        principal,
        ref: PlatformContentRef,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """删除 Watch：只删自动同步配置，不删除 Resource（09 §43.2）。"""
        now = now or datetime.now(timezone.utc)
        if not await self._store.soft_delete_watch(session, ref.id, now):
            raise ResourceError("RESOURCE_NOT_FOUND")
        await self._audit_watch(
            session, principal=principal, ref=ref, action="resource.watch.delete", request_id=request_id
        )

    # ── 内部 ──

    async def _require_watch(self, session: AsyncSession, resource_id: uuid.UUID) -> PlatformResourceWatch:
        watch = await self._store.get_watch(session, resource_id)
        if watch is None:
            raise ResourceError("RESOURCE_NOT_FOUND")
        return watch

    async def _audit_watch(
        self,
        session: AsyncSession,
        *,
        principal,
        ref: PlatformContentRef,
        action: str,
        request_id: str | None,
        metadata: dict | None = None,
    ) -> None:
        await self._iam.append_audit_event(
            session,
            request_id=request_id,
            account_id=ref.account_id,
            actor_type="user",
            actor_user_id=principal.actor_user_id,
            actor_account_id=principal.actor_account_id,
            actor_session_id=principal.session_id,
            authentication_method=principal.authentication_method,
            subject_account_id=ref.account_id,
            subject_user_id=ref.owner_user_id,
            action=action,
            target_type="resource",
            target_id=str(ref.id),
            target_visibility=ref.visibility,
            scope="self" if ref.visibility == "user_private" else "account",
            result="success",
            metadata=metadata,
        )
