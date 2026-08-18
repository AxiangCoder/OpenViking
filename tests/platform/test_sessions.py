"""P1-E3 登录 Session 生命周期与 Principal 解析（03 §8.1，04 §10.7）。

验收映射：
- ① 登录下发 Cookie ≥40 字符且 DB 仅存 SHA-256；
- ⑧ 权限摘要与 P1-E2 有效权限一致（principal.permissions == RbacService 结果）；
- ③ 轮换语义（旧 Cookie 失效由 test_auth_api 补断言）；
- ⑩ 角色提升不轮换当前登录 Session；
- ⑪ 过期 Session 清理 Worker 周期执行且不影响未过期 Session。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.auth.principals import (
    INVALID_CREDENTIAL,
    SESSION_EXPIRED,
    USER_DISABLED,
    resolve_session_principal,
)
from openviking.server.platform.auth.sessions import SessionCleanupResult
from openviking.server.platform.auth.worker import SessionCleanupWorker
from openviking.server.platform.config import PlatformConfig
from openviking.server.platform.errors import AuthenticationError
from openviking.server.platform.iam.permissions import (
    ACCOUNT_ADMIN,
    USER_PERMISSIONS,
)
from openviking.server.platform.models import IamSession
from tests.platform.helpers import (
    AuthSetup,
    build_auth_setup,
    create_login_session,
)


async def _resolve_raw(setup: AuthSetup, session: AsyncSession, raw_token: str):
    return await resolve_session_principal(session, setup.repo, setup.rbac, raw_token)


async def _count_sessions(session: AsyncSession) -> int:
    return len((await session.execute(select(IamSession.id))).scalars().all())


# ── AC ①：DB 仅存 SHA-256 ──


async def test_create_login_session_db_stores_hash_only(
    repo, session: AsyncSession
) -> None:
    """AC ①：Cookie token ≥40 字符；DB 仅存 SHA-256(token)（04 §10.7）。"""
    setup = await build_auth_setup(session)
    raw, csrf, session_id = await create_login_session(
        setup, session, setup.alice, user_agent="test-agent"
    )

    assert len(raw) >= 40
    assert len(csrf) >= 32
    row = await setup.repo.get_session_by_token_hash(session, sha256_hex(raw))
    assert row is not None
    assert row.id == session_id
    # 明文 token / csrf 绝不出现在任何会话行
    rows = list((await session.execute(select(IamSession))).scalars())
    assert len(rows) == 1
    assert all(r.token_hash == sha256_hex(raw) for r in rows)
    assert all(r.csrf_secret_hash == sha256_hex(csrf) for r in rows)
    assert all(raw not in (r.token_hash, r.csrf_secret_hash) for r in rows)
    assert row.account_id == setup.acme.id
    assert row.user_id == setup.alice.id
    assert row.ip_hash == sha256_hex("127.0.0.1")
    assert row.user_agent == "test-agent"
    assert row.revoked_at is None


# ── AC ⑧：Principal 权限与 P1-E2 一致 ──


async def test_resolve_principal_matches_rbac_effective_permissions(
    repo, session: AsyncSession
) -> None:
    """AC ⑧：Session Principal 的权限摘要与 RbacService 有效权限完全一致。"""
    setup = await build_auth_setup(session)
    raw, _csrf, _sid = await create_login_session(setup, session, setup.alice)

    principal = await _resolve_raw(setup, session, raw)
    assert principal.actor_user_id == setup.alice.id
    assert principal.actor_account_id == setup.acme.id
    assert principal.actor_ov_user_id == setup.alice.ov_user_id
    assert principal.actor_ov_account_id == setup.acme.ov_account_id
    assert principal.authentication_method == "session"
    assert principal.session_id is not None

    expected = await setup.rbac.get_user_permissions(session, setup.alice.id)
    assert principal.permissions == expected.permissions == USER_PERMISSIONS
    assert principal.role_codes == expected.role_codes == ("user",)
    assert principal.role_rank == expected.rank == 1
    assert principal.ov_base_role == expected.ov_base_role == "user"
    assert principal.user_status == "active"


# ── Session 失效语义（03 §8.1）──


async def test_resolve_unknown_or_empty_token(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    with pytest.raises(AuthenticationError) as exc:
        await _resolve_raw(setup, session, "")
    assert exc.value.code == INVALID_CREDENTIAL
    with pytest.raises(AuthenticationError) as exc:
        await _resolve_raw(setup, session, "no-such-token")
    assert exc.value.code == SESSION_EXPIRED


async def test_resolve_revoked_session_expired(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    raw, _csrf, session_id = await create_login_session(setup, session, setup.alice)
    await setup.repo.revoke_session(session, session_id, reason="logout")
    await session.commit()
    with pytest.raises(AuthenticationError) as exc:
        await _resolve_raw(setup, session, raw)
    assert exc.value.code == SESSION_EXPIRED


async def test_resolve_idle_expired_session(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    raw, _csrf, session_id = await create_login_session(setup, session, setup.alice)
    await session.execute(
        IamSession.__table__.update()
        .where(IamSession.id == session_id)
        .values(idle_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await session.commit()
    with pytest.raises(AuthenticationError) as exc:
        await _resolve_raw(setup, session, raw)
    assert exc.value.code == SESSION_EXPIRED


async def test_resolve_absolute_expired_session(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    raw, _csrf, session_id = await create_login_session(setup, session, setup.alice)
    await session.execute(
        IamSession.__table__.update()
        .where(IamSession.id == session_id)
        .values(absolute_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await session.commit()
    with pytest.raises(AuthenticationError) as exc:
        await _resolve_raw(setup, session, raw)
    assert exc.value.code == SESSION_EXPIRED


async def test_resolve_disabled_user(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    raw, _csrf, _sid = await create_login_session(setup, session, setup.alice)
    await setup.repo.update_user(session, setup.alice.id, status="disabled")
    await session.commit()
    with pytest.raises(AuthenticationError) as exc:
        await _resolve_raw(setup, session, raw)
    assert exc.value.code == USER_DISABLED


async def test_touch_extends_idle_expiry(repo, session: AsyncSession) -> None:
    """touch 顺延 last_seen_at 与空闲到期（04 §10.7）。

    用 raw SQL 断言（identity map 对已加载行会缓存旧值，raw 读取直接看 DB）。
    """
    setup = await build_auth_setup(session)
    raw, _csrf, session_id = await create_login_session(setup, session, setup.alice)
    before = (
        await session.execute(
            text("SELECT last_seen_at, idle_expires_at FROM iam_sessions WHERE id = :sid"),
            {"sid": session_id},
        )
    ).one()

    await setup.sessions.touch_session(session, session_id)
    await session.commit()
    after = (
        await session.execute(
            text("SELECT last_seen_at, idle_expires_at FROM iam_sessions WHERE id = :sid"),
            {"sid": session_id},
        )
    ).one()
    assert after[0] >= before[0]
    assert after[1] > before[1]


# ── AC ③：轮换语义（改密轮换；旧 Session 撤销）──


async def test_rotate_revokes_old_and_issues_new(repo, session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    old_raw, _csrf, old_session_id = await create_login_session(setup, session, setup.alice)

    new_raw, new_csrf, new_session_id = await setup.sessions.rotate_session(
        session, user=setup.alice, old_session_id=old_session_id, ip_hash=sha256_hex("127.0.0.1")
    )
    await session.commit()

    assert new_raw != old_raw
    assert len(new_csrf) >= 32
    assert new_session_id != old_session_id
    old_row = await setup.repo.get_session_by_token_hash(session, sha256_hex(old_raw))
    assert old_row is not None and old_row.revoked_at is not None
    assert old_row.revoked_reason == "rotated"
    # 新会话可用，旧会话不可用
    await _resolve_raw(setup, session, new_raw)
    with pytest.raises(AuthenticationError) as exc:
        await _resolve_raw(setup, session, old_raw)
    assert exc.value.code == SESSION_EXPIRED


async def test_csrf_token_bound_to_session(repo, session: AsyncSession) -> None:
    """03 §8.2：CSRF Token 与登录 Session 绑定；常量时间 hash 校验。"""
    setup = await build_auth_setup(session)
    raw, csrf, _sid = await create_login_session(setup, session, setup.alice)
    row = await setup.repo.get_session_by_token_hash(session, sha256_hex(raw))
    assert setup.sessions.verify_csrf_token(row.csrf_secret_hash, csrf)
    assert not setup.sessions.verify_csrf_token(row.csrf_secret_hash, "wrong-token")
    assert not setup.sessions.verify_csrf_token(row.csrf_secret_hash, "")


# ── AC ⑩：角色提升不轮换当前登录 Session ──


async def test_role_promotion_does_not_rotate_session(repo, session: AsyncSession) -> None:
    """AC ⑩：user→account_admin 提升后，同一 Session 不轮换（03 §8.1 消解语义）。"""
    setup = await build_auth_setup(session)
    raw, _csrf, session_id = await create_login_session(setup, session, setup.alice)
    sessions_before = await _count_sessions(session)

    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=setup.alice.id,
        role_code=ACCOUNT_ADMIN,
    )
    await session.commit()

    # 会话未撤销、无新增会话行
    row = await setup.repo.get_session_by_token_hash(session, sha256_hex(raw))
    assert row is not None and row.revoked_at is None
    assert await _count_sessions(session) == sessions_before
    # 提升免重登生效：同一 Session 立即拿到新权限
    principal = await _resolve_raw(setup, session, raw)
    assert principal.role_codes == (ACCOUNT_ADMIN,)
    assert "user.create" in principal.permissions


# ── AC ⑪：过期 Session 清理 Worker ──


async def _insert_session_with_expiry(repo, session: AsyncSession, user, *, idle_delta, abs_delta):
    now = datetime.now(timezone.utc)
    return await repo.create_session(
        session,
        user_id=user.id,
        account_id=user.account_id,
        token_hash=sha256_hex(f"token-{time.monotonic_ns()}"),
        csrf_secret_hash=sha256_hex(f"csrf-{time.monotonic_ns()}"),
        last_seen_at=now,
        idle_expires_at=now + timedelta(seconds=idle_delta),
        absolute_expires_at=now + timedelta(seconds=abs_delta),
    )


async def test_cleanup_worker_removes_only_expired(repo, session: AsyncSession) -> None:
    """AC ⑪：Worker 物理删除过期 Session（空闲/绝对过期），不影响未过期。"""
    setup = await build_auth_setup(session)
    valid_raw, _csrf, _sid = await create_login_session(setup, session, setup.alice)
    await _insert_session_with_expiry(
        setup.repo, session, setup.alice, idle_delta=-60, abs_delta=3600
    )
    await _insert_session_with_expiry(
        setup.repo, session, setup.alice, idle_delta=3600, abs_delta=-60
    )
    await session.commit()

    worker = SessionCleanupWorker(config=PlatformConfig(session_cleanup_interval_seconds=3600))
    result = await worker.run_once(session)
    await session.commit()

    assert isinstance(result, SessionCleanupResult)
    assert result.removed == 2
    assert result.remaining == 1
    assert worker.last_result is result  # 可观测
    # 未过期 Session 不受影响，仍可解析
    await _resolve_raw(setup, session, valid_raw)


async def test_cleanup_worker_periodic_run(repo, session: AsyncSession, session_factory) -> None:
    """AC ⑪：周期执行（asyncio 后台任务），多次运行幂等且不影响未过期 Session。"""
    setup = await build_auth_setup(session)
    valid_raw, _csrf, _sid = await create_login_session(setup, session, setup.alice)
    await _insert_session_with_expiry(
        setup.repo, session, setup.alice, idle_delta=-60, abs_delta=3600
    )
    await session.commit()

    worker = SessionCleanupWorker(config=PlatformConfig(session_cleanup_interval_seconds=0.05))
    worker.start(session_factory)
    try:
        deadline = time.monotonic() + 5
        while worker.last_result is None and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert worker.last_result is not None
        assert worker.last_result.removed >= 1
        # 未过期 Session 仍可解析
        await _resolve_raw(setup, session, valid_raw)
    finally:
        await worker.stop()
