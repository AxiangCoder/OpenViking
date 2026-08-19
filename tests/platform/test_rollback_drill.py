"""P5-E3 回滚演练测试（14 号计划 §99.3，06 §15.4，07 §21 条目 12，AC③）。

- test_single_step_downgrade_upgrade_keeps_data：migration 可验证 down ——
  downgrade 一个版本再 upgrade 后业务数据保留（发布顺序要求，§15.4 步骤 2）；
- test_pg_credentials_authorize_after_downgrade_upgrade_cycle：版本回滚后仍以
  PG IAM 鉴权 —— 迁移往返后 Session/API Key 仍从 PG 解析成功（§15.4 步骤 3，
  不回退旧 JSON registry，05 §11.3 单一事实来源）；
- test_retry_recovers_failed_and_keeps_single_event：恢复侧 outbox 一致 ——
  failed 事件经重试接口幂等恢复、不产生重复对象（AC⑥，05 §11.3/§12.6）。
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.auth.principals import (
    AuthenticatedUserPrincipal,
    resolve_api_key_principal,
    resolve_session_principal,
)
from openviking.server.platform.auth.sessions import SessionService
from openviking.server.platform.config import platform_config
from openviking.server.platform.iam import RbacService
from openviking.server.platform.models import IamAccount
from openviking.server.platform.provisioning.repository import (
    EVENT_ACCOUNT_PROVISION,
    ProvisioningRepository,
)
from tests.platform.helpers import build_auth_setup, run_provisioning

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _previous_revision() -> str:
    """head 的直接前驱 revision（downgrade 用显式 revision：alembic CLI 会把
    `downgrade -1` 的相对修订解析为 base（全量回退），显式 ID 规避且未来可维护）。"""
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    cfg = AlembicConfig(str(_REPO_ROOT / "openviking/server/platform/alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    prev = ScriptDirectory.from_config(cfg).get_revision(head).down_revision
    assert isinstance(prev, str)
    return prev


def _run_alembic(*args: str, database_url: str) -> None:
    """独立进程执行迁移（env.py 在线路径，与部署一致的 alembic CLI 进程）。

    stdout/stderr 重定向到临时文件而非管道：macOS + Python 3.14 下
    capture_output 的管道在子进程已退出后 select 仍不返回 EOF（挂起），
    文件重定向已验证稳定（probe：build_auth_setup 后 downgrade 通过）。
    """
    env = {
        **os.environ,
        "OV_PLATFORM_DATABASE_URL": database_url,
    }
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "stdout.log"), "w+") as out, open(
            os.path.join(td, "stderr.log"), "w+"
        ) as err:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    str(_REPO_ROOT / "openviking/server/platform/alembic.ini"),
                    *args,
                ],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env=env,
                timeout=120,
            )
        if result.returncode != 0:
            err.seek(0)
            raise RuntimeError(
                f"alembic {args} failed (rc={result.returncode}): {err.read()[-2000:]}"
            )


async def test_single_step_downgrade_upgrade_keeps_data(
    session: AsyncSession, test_database: str
) -> None:
    """AC③（§15.4 步骤 2 可验证 down）：downgrade -1 → upgrade head 数据保留。"""
    setup = await build_auth_setup(session)
    before_users = int((await session.execute(
        text("SELECT count(*) FROM iam_users"))).scalar())
    before_accounts = int((await session.execute(
        text("SELECT count(*) FROM iam_accounts"))).scalar())
    # 提交释放快照读锁：子进程迁移的 DROP TABLE 需要 ACCESS EXCLUSIVE，
    # 未提交事务持有的 ACCESS SHARE 会阻塞子进程（fixture session 自动开启事务）。
    await session.commit()

    _run_alembic("downgrade", _previous_revision(), database_url=test_database)
    _run_alembic("upgrade", "head", database_url=test_database)

    after_users = int((await session.execute(
        text("SELECT count(*) FROM iam_users"))).scalar())
    after_accounts = int((await session.execute(
        text("SELECT count(*) FROM iam_accounts"))).scalar())
    assert after_users == before_users, "downgrade/upgrade 后用户数据丢失"
    assert after_accounts == before_accounts
    # 凭证/角色引用仍在（PG IAM 未因迁移往返破坏）
    psa = await setup.repo.get_user_by_normalized_email(
        session, "psa@platform.local")
    assert psa is not None and psa.status == "active"


async def test_pg_credentials_authorize_after_downgrade_upgrade_cycle(
    session: AsyncSession, test_database: str
) -> None:
    """AC③（§15.4 步骤 3）：版本回滚（迁移往返）后，登录 Session 与 API Key
    仍由 PG IAM 解析成功——不回退旧 JSON registry（05 §11.3 单一事实来源）。"""
    setup = await build_auth_setup(session)
    raw_session, _, _ = await SessionService(
        setup.repo, platform_config
    ).create_login_session(
        session, user=setup.alice, ip_hash=sha256_hex("127.0.0.1"),
        user_agent="rollback-drill",
    )
    secret = "rollback-secret-" + secrets.token_hex(8)
    cred = await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=setup.alice.id,
        name="rollback-key",
        public_id="pub_rollback_" + secrets.token_hex(8),
        key_hash=sha256_hex(secret),
        key_last_four=secret[-4:],
        created_by=setup.alice.id,
    )
    await session.commit()

    # 迁移往返（模拟应用版本回滚时的 schema 收敛；已提交，无锁阻塞）
    _run_alembic("downgrade", _previous_revision(), database_url=test_database)
    _run_alembic("upgrade", "head", database_url=test_database)

    rbac = RbacService(setup.repo)
    session_p = await resolve_session_principal(
        session, setup.repo, rbac, raw_session)
    assert session_p.authentication_method == "session"
    assert session_p.actor_user_id == setup.alice.id

    key_p = await resolve_api_key_principal(
        session, setup.repo, rbac, f"ovk_u.{cred.public_id}.{secret}")
    assert key_p.authentication_method == "api_key"
    assert key_p.actor_user_id == setup.alice.id


async def test_retry_recovers_failed_and_keeps_single_event(
    session: AsyncSession,
) -> None:
    """AC⑥（05 §11.3/§12.6）：恢复后 failed 事件经 retry 幂等恢复、
    不产生重复事件（同一 PG 事务事实来源，重复重试 409）。"""
    setup = await build_auth_setup(session)
    # 模拟恢复后遗留 failed：直接入队并置 failed
    broken = IamAccount(
        ov_account_id="ov_account_rollback",
        code="rollback",
        display_name="Rollback",
        status="failed",
        provisioning_error="simulated (drill)",
    )
    session.add(broken)
    await session.flush()
    outbox = ProvisioningRepository()
    await outbox.enqueue(
        session, event_type=EVENT_ACCOUNT_PROVISION,
        aggregate_id=broken.id, payload={"account_id": str(broken.id)},
    )
    events = await outbox.list_by_account(session, broken.id)
    assert events, "outbox 事件应已入队"
    event = events[0]
    await outbox.update_status(
        session, event, status="failed", now=datetime.now(timezone.utc),
        attempts=3, last_error="simulated (drill)",
    )
    await session.commit()

    from openviking.server.platform.provisioning.service import (
        ProvisioningNotRetryableError,
        ProvisioningService,
    )

    principal = AuthenticatedUserPrincipal(
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        actor_ov_user_id=None,
        actor_ov_account_id=None,
        user_status="active",
        authentication_method="session",
    )
    prov = ProvisioningService(setup.repo, ProvisioningRepository(), None)
    first = await prov.retry_account(
        session, actor=principal, account_id=broken.id,
        request_id="rollback-retry-1",
    )
    await session.commit()
    assert first.status == "provisioning" and first.retried_events == 1

    # Worker 跑通（FakeControlPlane，test 内部）→ 事件 completed、Account active
    processed, _ = await run_provisioning(session)
    assert processed >= 1
    rows = (await session.execute(
        text("SELECT event_type, status FROM iam_outbox WHERE aggregate_id = :aid"),
        {"aid": str(broken.id)},
    )).all()
    assert [(r.event_type, r.status) for r in rows] == [(EVENT_ACCOUNT_PROVISION, "completed")]

    broken_id = str(broken.id)  # rollback 会过期 ORM 对象，先取 ID
    # 幂等：active 且无未完成事件 → 409，事件数不变（不产生重复事件）
    with pytest.raises(ProvisioningNotRetryableError):
        await prov.retry_account(
            session, actor=principal, account_id=broken.id,
            request_id="rollback-retry-2",
        )
    await session.rollback()
    rows_after = (await session.execute(
        text("SELECT count(*) FROM iam_outbox WHERE aggregate_id = :aid"),
        {"aid": broken_id},
    )).scalar()
    assert rows_after == 1, "重复重试产生重复事件"
