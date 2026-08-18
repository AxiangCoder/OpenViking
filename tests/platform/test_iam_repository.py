"""P1-E1 repository 验收（AC ② ④ ⑥）。

- ⑥ repository 提供事务性 CRUD 与乐观锁
- ④ account_id IS NULL 仅 PSA（部分唯一索引 + service invariant 文档化）
- ② 唯一约束经 repository 层统一包装为 ConstraintViolationError
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.errors import (
    ConstraintViolationError,
    EntityNotFoundError,
    OptimisticLockError,
)
from openviking.server.platform.iam import PostgresIamRepository
from openviking.server.platform.models import (
    IamAccount,
    IamPermission,
    IamRole,
    IamUser,
)

NOW = datetime.now(timezone.utc)


async def _account(
    repo: PostgresIamRepository,
    session: AsyncSession,
    *,
    code: str = "acme",
    ov_account_id: str | None = None,
    status: str = "provisioning",
) -> IamAccount:
    return await repo.create_account(
        session,
        ov_account_id=ov_account_id or f"ov_account_{code}",
        code=code,
        display_name="Acme Corp",
        status=status,
    )


async def _user(
    repo: PostgresIamRepository,
    session: AsyncSession,
    account: IamAccount,
    *,
    email: str = "alice@example.com",
    username: str = "alice",
) -> IamUser:
    return await repo.create_user(
        session,
        account_id=account.id,
        ov_user_id=f"ov_user_{username}",
        username=username,
        email=email,
        display_name="Alice",
        password_hash="x" * 64,
        status="active",
    )


# ── accounts：CRUD ──


async def test_account_crud_roundtrip(repo: PostgresIamRepository, session: AsyncSession) -> None:
    account = await _account(repo, session, code="acme")
    await session.commit()

    assert (await repo.get_account(session, account.id)).id == account.id
    assert (await repo.get_account_by_code(session, "acme")).id == account.id
    assert (await repo.get_account_by_code(session, "nope")) is None
    assert (await repo.get_account_by_ov_account_id(session, "ov_account_acme")).id == account.id
    assert [a.id for a in await repo.list_accounts(session)] == [account.id]
    assert (await repo.get_account_by_code(session, "nope")) is None


async def test_account_duplicate_code_raises(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    await _account(repo, session, code="acme")
    await session.commit()
    with pytest.raises(ConstraintViolationError):
        await _account(repo, session, code="acme", ov_account_id="ov_account_beta")


async def test_account_duplicate_ov_account_id_raises(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    await _account(repo, session, code="acme")
    await session.commit()
    with pytest.raises(ConstraintViolationError):
        await _account(repo, session, code="beta", ov_account_id="ov_account_acme")


# ── accounts：乐观锁（AC ⑥）──


async def test_optimistic_lock_stale_version_rejected(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    await session.commit()
    assert account.version == 1

    updated = await repo.update_account(
        session, account.id, expected_version=1, display_name="Acme Updated"
    )
    await session.commit()
    assert updated.version == 2

    with pytest.raises(OptimisticLockError):
        await repo.update_account(
            session, account.id, expected_version=1, display_name="Stale Write"
        )


async def test_optimistic_lock_concurrent_writers_only_one_wins(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    await session.commit()

    await repo.update_account(session, account.id, expected_version=1, display_name="A")
    with pytest.raises(OptimisticLockError):
        await repo.update_account(session, account.id, expected_version=1, display_name="B")
    await session.rollback()


async def test_update_account_ignores_unknown_fields(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    await session.commit()
    updated = await repo.update_account(
        session, account.id, expected_version=1, id=uuid.uuid4(), display_name="Ok"
    )
    assert updated.id == account.id


# ── accounts：软删（AC ⑥ 事务性 + 04 §10.1 purge_after 默认）──


async def test_soft_delete_account_sets_recycle_fields(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    actor = await _user(repo, session, account, email="admin@acme.com")
    await session.commit()

    deleted = await repo.soft_delete_account(session, account.id, actor_id=actor.id)
    await session.commit()
    assert deleted.deleted_at is not None
    assert deleted.deleted_by == actor.id
    assert deleted.purge_after is not None
    assert abs((deleted.purge_after - deleted.deleted_at).days - 30) <= 1
    assert [a.id for a in await repo.list_accounts(session)] == []


async def test_soft_delete_account_idempotent_not_found(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    actor = await _user(repo, session, account, email="admin@acme.com")
    await session.commit()
    await repo.soft_delete_account(session, account.id, actor_id=actor.id)
    await session.commit()
    with pytest.raises(EntityNotFoundError):
        await repo.soft_delete_account(session, account.id, actor_id=actor.id)


# ── users：CRUD ──


async def test_user_crud_roundtrip(repo: PostgresIamRepository, session: AsyncSession) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    await session.commit()

    assert (await repo.get_user(session, user.id)).id == user.id
    assert (await repo.get_user_by_normalized_email(session, "ALICE@example.com")).id == user.id
    assert (await repo.get_user_by_normalized_email(session, "nobody@example.com")) is None
    assert [u.id for u in await repo.list_users_by_account(session, account.id)] == [user.id]


async def test_user_normalized_email_unique_across_accounts(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account_a = await _account(repo, session, code="acme")
    account_b = await _account(repo, session, code="beta")
    await _user(repo, session, account_a, email="Alice@Example.com")
    await session.commit()
    with pytest.raises(ConstraintViolationError):
        await _user(repo, session, account_b, email="alice@example.com")


async def test_psa_null_account_single_row(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """AC ④：account_id IS NULL 仅允许 platform_super_admin。
    DB 层强制至多一行；角色约束由 P1-E2 初始化路径（service invariant）承载。"""
    psa = await repo.create_user(
        session,
        account_id=None,
        ov_user_id=None,
        username="psa",
        email="psa@platform.local",
        password_hash="x" * 64,
        status="active",
    )
    await session.commit()
    assert psa.account_id is None
    with pytest.raises(ConstraintViolationError):
        await repo.create_user(
            session,
            account_id=None,
            ov_user_id=None,
            username="psa2",
            email="psa2@platform.local",
            password_hash="x" * 64,
            status="active",
        )


async def test_user_soft_delete(repo: PostgresIamRepository, session: AsyncSession) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    actor = await _user(repo, session, account, email="admin@acme.com", username="admin")
    await session.commit()

    deleted = await repo.soft_delete_user(session, user.id, actor_id=actor.id)
    await session.commit()
    assert deleted.deleted_at is not None
    assert abs((deleted.purge_after - deleted.deleted_at).days - 30) <= 1
    assert [u.id for u in await repo.list_users_by_account(session, account.id)] == [actor.id]


async def test_bump_permission_version(repo: PostgresIamRepository, session: AsyncSession) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    await session.commit()
    v0 = user.permission_version
    await repo.bump_permission_version(session, user.id)
    await session.commit()
    assert (await repo.get_user(session, user.id)).permission_version == v0 + 1


# ── sessions ──


async def test_session_crud_and_revoke(repo: PostgresIamRepository, session: AsyncSession) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    await session.commit()

    s = await repo.create_session(
        session,
        user_id=user.id,
        account_id=account.id,
        token_hash="t" * 64,
        csrf_secret_hash="c" * 64,
        last_seen_at=NOW,
        idle_expires_at=NOW + timedelta(hours=24),
        absolute_expires_at=NOW + timedelta(days=30),
    )
    await session.commit()
    assert (await repo.get_session_by_token_hash(session, "t" * 64)).id == s.id
    assert await repo.get_session_by_token_hash(session, "u" * 64) is None

    revoked = await repo.revoke_session(session, s.id, reason="logout")
    await session.commit()
    assert revoked.revoked_at is not None
    assert revoked.revoked_reason == "logout"
    assert await repo.revoke_session(session, s.id, reason="logout") is None


async def test_revoke_all_sessions_for_user(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    await session.commit()
    for i in range(2):
        await repo.create_session(
            session,
            user_id=user.id,
            account_id=account.id,
            token_hash=(f"tok_{i}".ljust(64, "x")),
            csrf_secret_hash="c" * 64,
            last_seen_at=NOW,
            idle_expires_at=NOW + timedelta(hours=24),
            absolute_expires_at=NOW + timedelta(days=30),
        )
    await session.commit()
    count = await repo.revoke_all_sessions_for_user(session, user.id, reason="reset")
    await session.commit()
    assert count == 2
    assert await repo.revoke_all_sessions_for_user(session, user.id, reason="reset") == 0


# ── api_credentials ──


async def test_api_credential_crud_and_revoke(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    await session.commit()

    cred = await repo.create_api_credential(
        session,
        account_id=account.id,
        user_id=user.id,
        name="codex",
        public_id="pub_1",
        key_hash="h" * 64,
        key_last_four="abcd",
        created_by=user.id,
    )
    await session.commit()
    assert (await repo.get_api_credential_by_public_id(session, "pub_1")).id == cred.id
    assert await repo.get_api_credential_by_public_id(session, "pub_x") is None
    assert [c.id for c in await repo.list_api_credentials_for_user(session, user.id)] == [cred.id]

    revoked = await repo.revoke_api_credential(session, cred.id, revoked_by=user.id)
    await session.commit()
    assert revoked.revoked_at is not None
    assert revoked.revoked_by == user.id
    assert await repo.revoke_api_credential(session, cred.id, revoked_by=user.id) is None


async def test_revoke_all_api_credentials_for_user(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    await session.commit()
    for i in range(2):
        await repo.create_api_credential(
            session,
            account_id=account.id,
            user_id=user.id,
            name=f"key-{i}",
            public_id=f"pub_{i}",
            key_hash=f"{i}" * 64,
            key_last_four="abcd",
            created_by=user.id,
        )
    await session.commit()
    count = await repo.revoke_all_api_credentials_for_user(session, user.id, revoked_by=user.id)
    await session.commit()
    assert count == 2
    assert await repo.revoke_all_api_credentials_for_user(session, user.id, revoked_by=user.id) == 0


# ── roles / permissions（种子归 P1-E2，此处验证只读接口与绑定）──


async def test_roles_read_empty_before_seed(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    """P1-E2 前无种子：只读接口返回空，verify.py "ranks 3/2/1" 断言随种子成立。"""
    assert await repo.list_roles(session) == []
    assert await repo.get_role_by_code(session, "platform_super_admin") is None
    assert await repo.list_permissions(session) == []
    assert await repo.list_permissions_for_roles(session, [uuid.uuid4()]) == []


async def test_role_assign_and_read(repo: PostgresIamRepository, session: AsyncSession) -> None:
    account = await _account(repo, session, code="acme")
    user = await _user(repo, session, account)
    role = IamRole(
        code="user",
        name="User",
        rank=1,
        ov_base_role="user",
        is_system=True,
        status="active",
    )
    permission = IamPermission(
        code="memory.read.self", domain="memory", action="read", risk_level="low"
    )
    session.add_all([role, permission])
    await session.flush()

    link = await repo.assign_role(session, user_id=user.id, role_id=role.id)
    await session.commit()
    assert link.role_id == role.id
    found = await repo.get_role_for_user(session, user.id)
    assert found is not None and found.role_id == role.id
    assert (await repo.get_role_by_code(session, "user")).rank == 1


# ── audit ──


async def test_audit_append_and_filter(repo: PostgresIamRepository, session: AsyncSession) -> None:
    account = await _account(repo, session, code="acme")
    actor = await _user(repo, session, account, email="admin@acme.com", username="admin")
    subject = await _user(repo, session, account, email="bob@acme.com", username="bob")
    await session.commit()

    await repo.append_audit_event(
        session,
        request_id="req-1",
        account_id=account.id,
        actor_type="user",
        actor_user_id=actor.id,
        actor_account_id=account.id,
        authentication_method="session",
        subject_account_id=account.id,
        subject_user_id=subject.id,
        action="user.read",
        scope="account",
        result="success",
        metadata={"ok": True},
    )
    await session.commit()

    events = await repo.list_audit_events(session, account_id=account.id, action="user.read")
    assert len(events) == 1
    assert events[0].actor_user_id == actor.id
    assert events[0].subject_user_id == subject.id
    assert events[0].request_id == "req-1"
    assert events[0].metadata_json == {"ok": True}
    assert await repo.list_audit_events(session, action="user.write") == []
    assert await repo.list_audit_events(session, subject_user_id=subject.id, result="denied") == []


# ── 事务性（AC ⑥）──


async def test_uncommitted_changes_rollback(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    await _account(repo, session, code="acme")
    await session.rollback()
    assert await repo.list_accounts(session) == []


async def test_rollback_after_constraint_error_preserves_prior_rows(
    repo: PostgresIamRepository, session: AsyncSession
) -> None:
    account = await _account(repo, session, code="acme")
    await session.commit()
    with pytest.raises(ConstraintViolationError):
        await _account(repo, session, code="acme", ov_account_id="ov_account_beta")
    # repository 捕获 IntegrityError 后已回滚，可继续用同一 Session
    account2 = await _account(repo, session, code="beta")
    await session.commit()
    assert [a.code for a in await repo.list_accounts(session)] == ["acme", "beta"]
    assert account.id != account2.id
