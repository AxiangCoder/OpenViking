"""P1-E1 迁移验收（AC ① ② ③ ⑤）+ P1-E2 新增表（iam_permission_schema，04 §10.5）
+ P2-E1 新增表（iam_outbox，04 §10.9）+ P2-E2 新增表（iam_deletion_jobs，04 §10.11）。

- ① 全新库执行迁移后全部 iam_* 表创建成功，upgrade/downgrade 可重复
  （P1-E2 新增 iam_permission_schema，见 versions/b2c3d4e5f6a7；
   P2-E1 新增 iam_outbox，见 versions/c3d4e5f6a7b8；
   P2-E2 新增 iam_deletion_jobs，见 versions/d4e5f6a7b8c9；
   platform_content_refs/platform_operation_refs/platform_uploads 见 test_content_registry.py）
- ② normalized_email、(account_id, ov_user_id)、(account_id, normalized_username)、
     public_id/key_hash、token_hash、account code/ov_account_id 唯一生效
- ③ iam_roles.rank 列存在（"ranks 3/2/1" 数据断言由 P1-E2 种子测试承载）
- ⑤ 外键循环按 Spike §4.2 #5 手工编排（accounts.deleted_by 外键补齐、downgrade 干净）
"""

from __future__ import annotations

import datetime
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import (
    IamAccount,
    IamApiCredential,
    IamSession,
    IamUser,
)

EXPECTED_TABLES = {
    "iam_accounts",
    "iam_users",
    "iam_api_credentials",
    "iam_roles",
    "iam_permissions",
    "iam_role_permissions",
    "iam_user_roles",
    "iam_sessions",
    "iam_audit_events",
    "iam_permission_schema",
    "iam_outbox",
    "iam_deletion_jobs",
}


async def _iam_tables(session: AsyncSession) -> set[str]:
    result = await session.execute(
        text(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' and table_name like 'iam_%'"
        )
    )
    return {row[0] for row in result}


async def _count_iam_tables(session: AsyncSession) -> int:
    return len(await _iam_tables(session))


async def test_fresh_upgrade_creates_all_iam_tables(session: AsyncSession) -> None:
    """AC ①：全新库执行迁移后全部 iam_* 表创建成功（P1-E1 9 张 + P1-E2 1 张 + P2-E1 1 张 + P2-E2 1 张）。"""
    assert await _iam_tables(session) == EXPECTED_TABLES


async def test_permission_schema_singleton_constraint(session: AsyncSession) -> None:
    """P1-E2：iam_permission_schema 单行约束（id=1）由 DB 强制。"""
    await session.execute(
        text(
            "INSERT INTO iam_permission_schema (id, schema_version, catalog_fingerprint) "
            "VALUES (1, 1, 'x' || repeat('0', 63))"
        )
    )
    await session.commit()
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO iam_permission_schema (id, schema_version, catalog_fingerprint) "
                "VALUES (2, 1, 'y' || repeat('0', 63))"
            )
        )
        await session.commit()


async def test_upgrade_downgrade_repeatable(session: AsyncSession, test_database: str) -> None:
    """AC ①⑤：upgrade/downgrade 可重复，且 downgrade 干净无残留表。"""
    cfg = _alembic_config(test_database)
    command.downgrade(cfg, "base")
    assert await _count_iam_tables(session) == 0
    command.upgrade(cfg, "head")
    assert await _iam_tables(session) == EXPECTED_TABLES


async def test_fk_cycle_orchestration_present(session: AsyncSession) -> None:
    """AC ⑤：accounts.deleted_by → users 外键已手工补齐（Spike §4.2 #5）。"""
    result = await session.execute(
        text(
            "select 1 from pg_constraint c "
            "join pg_class t on t.oid = c.conrelid "
            "where t.relname = 'iam_accounts' "
            "and c.conname = 'fk_iam_accounts_deleted_by_iam_users'"
        )
    )
    assert result.scalar() == 1


async def test_rank_column_exists(session: AsyncSession) -> None:
    """AC ③：iam_roles.rank 列存在（BigInteger）。
    "ranks 3/2/1" 数据断言由 P1-E2 种子测试承载（verify.py 对应断言）。"""
    result = await session.execute(
        text(
            "select data_type from information_schema.columns "
            "where table_name = 'iam_roles' and column_name = 'rank'"
        )
    )
    assert result.scalar() == "bigint"


async def _insert_account(session: AsyncSession, code: str, ov_account_id: str) -> IamAccount:
    account = IamAccount(
        ov_account_id=ov_account_id, code=code, display_name="Acme", status="provisioning"
    )
    session.add(account)
    await session.flush()
    return account


async def test_account_code_dual_unique_enforced(session: AsyncSession) -> None:
    """AC ②：account code 与 ov_account_id 各自全局唯一（双唯一，Spike §4.2 #8）。"""
    await _insert_account(session, "acme", "ov_acct_1")
    await session.commit()
    with pytest.raises(IntegrityError):
        await _insert_account(session, "acme", "ov_acct_2")
    await session.rollback()
    with pytest.raises(IntegrityError):
        await _insert_account(session, "beta", "ov_acct_1")
    await session.rollback()


async def test_normalized_email_global_unique(session: AsyncSession) -> None:
    """AC ②：normalized_email 全局唯一（跨 Account 也拒绝）。"""
    account_a = await _insert_account(session, "acme", "ov_acct_1")
    account_b = await _insert_account(session, "beta", "ov_acct_2")

    def _user(account, email: str) -> IamUser:
        return IamUser(
            account_id=account.id,
            ov_user_id=str(uuid.uuid4()),
            username="alice",
            normalized_username="alice",
            email=email,
            normalized_email=email.strip().casefold(),
            password_hash="x" * 64,
            status="active",
        )

    session.add(_user(account_a, "Alice@Example.com"))
    await session.flush()
    with pytest.raises(IntegrityError):
        session.add(_user(account_b, "alice@example.com"))
        await session.flush()
    await session.rollback()


async def test_account_scoped_user_uniques(session: AsyncSession) -> None:
    """AC ②：(account_id, ov_user_id) 与 (account_id, normalized_username)
    唯一；同 Account 冲突拒绝，跨 Account 允许。"""
    account_a = await _insert_account(session, "acme", "ov_acct_1")
    account_b = await _insert_account(session, "beta", "ov_acct_2")
    a_id, b_id = account_a.id, account_b.id

    def _user(account_id, username: str, ov_user_id: str, email: str) -> IamUser:
        return IamUser(
            account_id=account_id,
            ov_user_id=ov_user_id,
            username=username,
            normalized_username=username,
            email=email,
            normalized_email=email,
            password_hash="x" * 64,
            status="active",
        )

    session.add(_user(a_id, "alice", "ov_user_1", "a1@example.com"))
    await session.commit()
    # 同 Account 重复 ov_user_id
    with pytest.raises(IntegrityError):
        session.add(_user(a_id, "alice2", "ov_user_1", "a2@example.com"))
        await session.flush()
    await session.rollback()
    # 同 Account 重复 username（normalized 相同）
    with pytest.raises(IntegrityError):
        session.add(_user(a_id, "alice", "ov_user_2", "a3@example.com"))
        await session.flush()
    await session.rollback()
    # 跨 Account 相同 username/ov_user_id 允许（约束按 Account 范围）
    session.add(_user(b_id, "alice", "ov_user_1", "b1@example.com"))
    await session.commit()


async def test_psa_null_account_partial_unique(session: AsyncSession) -> None:
    """AC ④：account_id IS NULL 仅允许 platform_super_admin——
    部分唯一索引强制至多一个无 Account 用户（角色约束归 P1-E2 service）。"""

    def _psa(email: str) -> IamUser:
        return IamUser(
            account_id=None,
            ov_user_id=None,
            username="psa",
            normalized_username="psa",
            email=email,
            normalized_email=email,
            password_hash="x" * 64,
            status="active",
        )

    session.add(_psa("psa@platform.local"))
    await session.commit()
    with pytest.raises(IntegrityError):
        session.add(_psa("psa2@platform.local"))
        await session.flush()
    await session.rollback()


async def test_public_id_key_hash_unique(session: AsyncSession) -> None:
    """AC ②：api_credentials public_id/key_hash 唯一。"""
    account = await _insert_account(session, "acme", "ov_acct_1")
    user = IamUser(
        account_id=account.id,
        ov_user_id="ov_user_1",
        username="alice",
        normalized_username="alice",
        email="alice@example.com",
        normalized_email="alice@example.com",
        password_hash="x" * 64,
        status="active",
    )
    session.add(user)
    await session.flush()
    account_id, user_id = account.id, user.id

    def _cred(public_id: str, key_hash: str) -> IamApiCredential:
        return IamApiCredential(
            account_id=account_id,
            user_id=user_id,
            name="codex",
            public_id=public_id,
            key_hash=key_hash,
            key_last_four="abcd",
            status="active",
            created_by=user_id,
        )

    session.add(_cred("pub_1", "h" * 64))
    await session.commit()
    with pytest.raises(IntegrityError):
        session.add(_cred("pub_1", "i" * 64))
        await session.flush()
    await session.rollback()
    with pytest.raises(IntegrityError):
        session.add(_cred("pub_2", "h" * 64))
        await session.flush()
    await session.rollback()


async def test_token_hash_unique(session: AsyncSession) -> None:
    """AC ②：sessions token_hash 唯一。"""
    account = await _insert_account(session, "acme", "ov_acct_1")
    user = IamUser(
        account_id=account.id,
        ov_user_id="ov_user_1",
        username="alice",
        normalized_username="alice",
        email="alice@example.com",
        normalized_email="alice@example.com",
        password_hash="x" * 64,
        status="active",
    )
    session.add(user)
    await session.flush()

    now = datetime.datetime.now(datetime.timezone.utc)

    def _session(token_hash: str) -> IamSession:
        return IamSession(
            user_id=user.id,
            account_id=account.id,
            token_hash=token_hash,
            csrf_secret_hash="c" * 64,
            last_seen_at=now,
            idle_expires_at=now + datetime.timedelta(hours=24),
            absolute_expires_at=now + datetime.timedelta(days=30),
        )

    session.add(_session("tok_1"))
    await session.flush()
    with pytest.raises(IntegrityError):
        session.add(_session("tok_1"))
        await session.flush()
    await session.rollback()


def _alembic_config(database_url: str) -> Config:
    cfg = Config(_alembic_ini_path())
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _alembic_ini_path() -> str:

    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "openviking/server/platform/alembic.ini",
    )
