"""P2-E2 删除回收与 Purge Worker 测试（04 §10.11，05 §11.3/§12.6，14 号计划 §97.2）。

验收映射：
- AC⑧：删除任务与 Purge 幂等、`purge_after` 默认 30 天、审计不随物理清理；
- AC⑨（服务层部分）：User/Account deletion-preview/DELETE 进入回收期并
  返回 deletion job ID；回收站恢复按 05 §12.6 注类型化权限；
- 05 §12.6 注：恢复权限映射（user → account `user.delete`；account →
  platform `account.delete`）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.principals import AuthenticatedUserPrincipal
from openviking.server.platform.deletion.service import DeletionService
from openviking.server.platform.deletion.worker import PurgeWorker
from openviking.server.platform.errors import (
    AdminActionForbiddenError,
    DeletionJobError,
    EntityNotFoundError,
    LastAccountAdminError,
)
from openviking.server.platform.models import IamAuditEvent, IamDeletionJob
from openviking.server.platform.registry.repository import (
    JOB_PENDING,
    JOB_PURGED,
    JOB_RESTORED,
    RegistryRepository,
)
from tests.platform.helpers import build_auth_setup


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _psa_principal(setup) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        actor_ov_user_id=None,
        actor_ov_account_id=None,
        user_status="active",
        authentication_method="session",
        role_codes=("platform_super_admin",),
        permissions=frozenset(
            {"account.delete", "account.read.platform", "account.manage.platform"}
        ),
    )


def _admin_principal(setup) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=setup.admin.id,
        actor_account_id=setup.acme.id,
        actor_ov_user_id=setup.admin.ov_user_id,
        actor_ov_account_id=setup.acme.ov_account_id,
        user_status="active",
        authentication_method="session",
        role_codes=("account_admin",),
        permissions=frozenset({"user.delete", "user.read"}),
    )


def _alice_principal(setup) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=setup.alice.id,
        actor_account_id=setup.acme.id,
        actor_ov_user_id=setup.alice.ov_user_id,
        actor_ov_account_id=setup.acme.ov_account_id,
        user_status="active",
        authentication_method="session",
        role_codes=("user",),
        permissions=frozenset({"session.delete.self"}),
    )


def _deletion(session, setup) -> DeletionService:
    return DeletionService(setup.repo, RegistryRepository())


# ── AC⑨：User deletion-preview / DELETE 进入回收期并返回 deletion job ID ──


async def test_user_delete_enters_recycle_window_with_job_id(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    preview = await service.preview_user(
        session, scope_account_id=setup.acme.id, target_user_id=setup.alice.id
    )
    assert preview.resource_type == "user"
    assert preview.target_name == "alice"
    assert "login_sessions" in preview.impacted
    assert preview.recoverable is True

    result = await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()
    assert result.resource_type == "user"
    assert result.deletion_job_id is not None

    job = await RegistryRepository().get_deletion_job(session, result.deletion_job_id)
    assert job is not None
    assert job.status == JOB_PENDING
    assert job.deleted_by == setup.admin.id

    # 对象从正常查询隐藏（05 §11.3）
    user = await setup.repo.get_user(session, setup.alice.id)
    assert user is not None and user.deleted_at is not None

    # 删除幂等：重复删除复用同一任务（AC⑧）
    again = await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    assert again.deletion_job_id == result.deletion_job_id


async def test_delete_last_account_admin_rejected(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    with pytest.raises(LastAccountAdminError):
        await service.delete_user(
            session,
            actor=_admin_principal(setup),
            scope_account_id=setup.acme.id,
            target_user_id=setup.admin.id,  # 最后一名 Account Admin
        )


async def test_user_delete_cross_account_not_visible(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    with pytest.raises(EntityNotFoundError):
        await service.delete_user(
            session,
            actor=_admin_principal(setup),
            scope_account_id=uuid.uuid4(),  # 另一 Account
            target_user_id=setup.alice.id,
        )


async def test_account_delete_enters_recycle_window(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    preview = await service.preview_account(session, target_account_id=setup.acme.id)
    assert preview.resource_type == "account"
    assert preview.impacted["users"] >= 2

    result = await service.delete_account(
        session, actor=_psa_principal(setup), target_account_id=setup.acme.id
    )
    await session.commit()
    job = await RegistryRepository().get_deletion_job(session, result.deletion_job_id)
    assert job is not None and job.resource_type == "account"
    account = await setup.repo.get_account(session, setup.acme.id)
    assert account.deleted_at is not None


# ── 回收站与恢复（05 §12.6 注：类型化权限）──


async def test_recycle_bin_lists_and_typed_restore_permissions(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()

    # admin scope：恢复权限按对象类型计算（user → user.delete）
    rows = await service.list_recycle_bin(
        session,
        scope="account",
        principal=_admin_principal(setup),
        account_id=setup.acme.id,
    )
    assert len(rows) == 1
    assert rows[0].job.resource_type == "user"
    assert rows[0].restore_allowed is True
    assert rows[0].restore_permission == "user.delete"

    # 普通 User（无 user.delete）→ restore_allowed False
    rows = await service.list_recycle_bin(
        session,
        scope="account",
        principal=_alice_principal(setup),
        account_id=setup.acme.id,
    )
    assert rows[0].restore_allowed is False

    # 无权限恢复 → 403（AdminActionForbiddenError）
    with pytest.raises(AdminActionForbiddenError):
        await service.restore(
            session,
            scope="account",
            principal=_alice_principal(setup),
            account_id=setup.acme.id,
            job_id=rows[0].job.id,
        )


async def test_user_restore_clears_deleted_at_and_writes_audit(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    result = await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()

    restored = await service.restore(
        session,
        scope="account",
        principal=_admin_principal(setup),
        account_id=setup.acme.id,
        job_id=result.deletion_job_id,
        request_id="req-restore-1",
    )
    await session.commit()
    assert restored.resource_type == "user"
    user = await setup.repo.get_user(session, setup.alice.id)
    assert user.deleted_at is None and user.purge_after is None

    job = await RegistryRepository().get_deletion_job(session, result.deletion_job_id)
    assert job.status == JOB_RESTORED
    assert job.restored_by == setup.admin.id
    assert job.restored_at is not None

    # 重复恢复 → ALREADY_RESTORED（幂等拒绝）
    with pytest.raises(DeletionJobError) as excinfo:
        await service.restore(
            session,
            scope="account",
            principal=_admin_principal(setup),
            account_id=setup.acme.id,
            job_id=result.deletion_job_id,
        )
    assert excinfo.value.reason == "ALREADY_RESTORED"

    # 恢复动作写审计（04 §10.11）
    events = list((await session.execute(select(IamAuditEvent))).scalars())
    assert any(e.action == "user.restore" and e.request_id == "req-restore-1" for e in events)


async def test_account_restore_only_platform_scope(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    result = await service.delete_account(
        session, actor=_psa_principal(setup), target_account_id=setup.acme.id
    )
    await session.commit()

    # Account Admin 不能恢复 Account（映射要求 platform scope + account.delete）
    with pytest.raises(AdminActionForbiddenError):
        await service.restore(
            session,
            scope="account",
            principal=_admin_principal(setup),
            account_id=setup.acme.id,
            job_id=result.deletion_job_id,
        )
    # PSA 可恢复
    restored = await service.restore(
        session,
        scope="platform",
        principal=_psa_principal(setup),
        account_id=None,
        job_id=result.deletion_job_id,
    )
    await session.commit()
    assert restored.resource_type == "account"
    account = await setup.repo.get_account(session, setup.acme.id)
    assert account.deleted_at is None


# ── AC⑧：Purge 幂等、purge_after 默认 30 天、审计不随物理清理 ──


async def test_purge_after_defaults_to_30_days(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    result = await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()
    job = await RegistryRepository().get_deletion_job(session, result.deletion_job_id)
    assert job is not None
    assert job.deleted_at is not None
    assert (job.purge_after - job.deleted_at) == timedelta(days=30)


async def test_purge_worker_idempotent_and_preserves_audit(session: AsyncSession) -> None:
    """AC⑧：Purge 幂等（同一任务只清理一次）；审计不随物理清理。"""
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    result = await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()
    audit_before = list((await session.execute(select(IamAuditEvent))).scalars())
    assert len(audit_before) >= 1  # user.delete 审计已写入

    class _CountingHandler:
        def __init__(self) -> None:
            self.calls = 0

        async def purge(self, session: AsyncSession, job: IamDeletionJob) -> None:
            self.calls += 1

    handler = _CountingHandler()
    worker = PurgeWorker(setup.repo, RegistryRepository(), handlers={"user": handler})
    due = _now() + timedelta(days=31)
    processed = await worker.run_once(session, now=due)
    assert processed == 1
    assert handler.calls == 1

    job = await RegistryRepository().get_deletion_job(session, result.deletion_job_id)
    assert job.status == JOB_PURGED

    # 幂等：再次运行不再领取（任务已 purged）
    processed = await worker.run_once(session, now=due)
    assert processed == 0
    assert handler.calls == 1

    # 审计不随物理清理（04 §10.11，AC⑧）
    audit_after = list((await session.execute(select(IamAuditEvent))).scalars())
    assert len(audit_after) >= len(audit_before)
    assert any(e.action == "purge.user" for e in audit_after)


async def test_purge_worker_failure_marks_failed_with_sanitized_error(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    result = await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()

    class _FailingHandler:
        async def purge(self, session: AsyncSession, job: IamDeletionJob) -> None:
            raise RuntimeError("purge failed at /private/tmp secret=abc")

    worker = PurgeWorker(setup.repo, RegistryRepository(), handlers={"user": _FailingHandler()})
    processed = await worker.run_once(session, now=_now() + timedelta(days=31))
    assert processed == 1
    job = await RegistryRepository().get_deletion_job(session, result.deletion_job_id)
    assert job.status == "failed"
    assert job.last_error is not None
    assert "/private/tmp" not in job.last_error and "abc" not in job.last_error

    # failed 任务不会再次被领取（保持幂等状态机）
    processed = await worker.run_once(session, now=_now() + timedelta(days=32))
    assert processed == 0


async def test_restore_after_purge_window_expired(session: AsyncSession) -> None:
    """AC⑧：`purge_after` 过期后恢复被拒（RESTORE_WINDOW_EXPIRED，05 §12.2）。"""
    setup = await build_auth_setup(session)
    service = _deletion(session, setup)
    result = await service.delete_user(
        session,
        actor=_admin_principal(setup),
        scope_account_id=setup.acme.id,
        target_user_id=setup.alice.id,
    )
    await session.commit()
    # 把 purge_after 拨到过去
    job = await RegistryRepository().get_deletion_job(session, result.deletion_job_id)
    job.purge_after = _now() - timedelta(days=1)
    await session.commit()
    with pytest.raises(DeletionJobError) as excinfo:
        await service.restore(
            session,
            scope="account",
            principal=_admin_principal(setup),
            account_id=setup.acme.id,
            job_id=result.deletion_job_id,
        )
    assert excinfo.value.reason == "RESTORE_WINDOW_EXPIRED"
