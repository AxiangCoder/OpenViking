"""Dashboard 产品服务（05 §12.5 `GET /api/platform/v1/dashboard`，14 号计划 §97.5）。

个人内容、处理状态和最近活动的**受控聚合**（AC⑨）：
- 只返回当前 User 自己的对象与当前 Account 共享对象的业务计数和脱敏活动；
- 不暴露 Queue/锁/模型/VectorDB 等底层状态（05 §12.5 dashboard 同一约束）；
- 所有计数过滤软删除（Session 从正常列表隐藏，AC⑦）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import (
    PlatformSessionCommit,
    PlatformSessionMessage,
)
from openviking.server.platform.registry.repository import (
    JOB_PENDING,
    STATUS_ACTIVE,
    RegistryRepository,
)
from openviking.server.platform.session.repository import (
    SessionRepository,
)


class DashboardService:
    """dashboard 聚合（权限由 Router 按「当前 User 基础访问」校验）。"""

    def __init__(
        self,
        store: SessionRepository | None = None,
        registry: RegistryRepository | None = None,
    ) -> None:
        self._store = store or SessionRepository()
        self._registry = registry or RegistryRepository()

    async def dashboard(self, session: AsyncSession, *, principal) -> dict:
        assert principal.actor_account_id is not None
        account_id = principal.actor_account_id
        user_id = principal.actor_user_id
        now = datetime.now(timezone.utc)

        session_refs = await self._store.list_refs(
            session, account_id=account_id, owner_user_id=user_id, status="active"
        )
        session_ids = [r.id for r in session_refs]

        commit_totals = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
        if session_ids:
            rows = list(
                (
                    await session.execute(
                        select(PlatformSessionCommit.phase2_status, func.count())
                        .where(PlatformSessionCommit.session_id.in_(session_ids))
                        .group_by(PlatformSessionCommit.phase2_status)
                    )
                ).all()
            )
            for status, count in rows:
                if status in commit_totals:
                    commit_totals[status] += count

        message_count = 0
        if session_ids:
            message_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(PlatformSessionMessage)
                        .where(PlatformSessionMessage.session_id.in_(session_ids))
                    )
                ).scalar_one()
            )

        private_resources = await self._registry.list_refs(
            session,
            account_id=account_id,
            object_type="resource",
            visibility="user_private",
            owner_user_id=user_id,
            status=STATUS_ACTIVE,
        )
        private_skills = await self._registry.list_refs(
            session,
            account_id=account_id,
            object_type="skill",
            visibility="user_private",
            owner_user_id=user_id,
            status=STATUS_ACTIVE,
        )
        shared_resources = await self._registry.list_refs(
            session,
            account_id=account_id,
            object_type="resource",
            visibility="account_shared",
            status=STATUS_ACTIVE,
        )
        shared_skills = await self._registry.list_refs(
            session,
            account_id=account_id,
            object_type="skill",
            visibility="account_shared",
            status=STATUS_ACTIVE,
        )

        recycle_jobs = await self._registry.list_deletion_jobs(
            session,
            account_id=account_id,
            resource_types=("session",),
            status=JOB_PENDING,
            limit=100,
        )
        own_recycle_count = 0
        for job in recycle_jobs:
            ref = await self._store.get_ref(session, uuid.UUID(job.resource_id), include_deleted=True)
            if ref is not None and ref.owner_user_id == user_id:
                own_recycle_count += 1

        recent = [self._session_activity_dto(r) for r in session_refs[:5]]

        return {
            "generated_at": now.isoformat(),
            "summary": {
                "sessions": len(session_refs),
                "messages": message_count,
                "resources_private": len(private_resources),
                "skills_private": len(private_skills),
                "resources_account_shared": len(shared_resources),
                "skills_account_shared": len(shared_skills),
                "sessions_in_recycle": own_recycle_count,
                "commits_by_phase2": commit_totals,
                "sessions_with_commit_failed": sum(
                    1 for r in session_refs if r.sync_status == "commit_failed"
                ),
            },
            "recent_activity": recent,
        }

    @staticmethod
    def _session_activity_dto(ref) -> dict:
        return {
            "id": str(ref.id),
            "client_name": ref.client_name,
            "sync_status": ref.sync_status,
            "commit_count": int(ref.commit_count or 0),
            "message_count": int(ref.message_count or 0),
            "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
        }
