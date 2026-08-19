"""P5-E3 演练库（14 号计划 §99.3，06 §14.4/§15.4）：业务数据准备、故障注入、
恢复后一致性校验。供真实演练（drill-backup-restore.sh）与自动化测试复用。

命令行（真实演练）：
    python deploy/product/scripts/drill_lib.py prepare <database_url> [--psa-email E] [--psa-password P]
    python deploy/product/scripts/drill_lib.py snapshot <database_url> [-o snapshot.json]
    python deploy/product/scripts/drill_lib.py fault-inject <database_url>
    python deploy/product/scripts/drill_lib.py verify <database_url> [-o report.json]

校验内容（验收②⑤⑥）：恢复后登录/Key/审计正常、PG 与数据卷一致（抽查
Resource/Skill/Session 无重复）、outbox 一致、failed 经重试接口幂等恢复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from openviking.server.platform.auth.password import hash_password, sha256_hex  # noqa: E402
from openviking.server.platform.auth.principals import (  # noqa: E402
    AuthenticatedUserPrincipal,
    resolve_api_key_principal,
    resolve_session_principal,
)
from openviking.server.platform.auth.service import AuthService  # noqa: E402
from openviking.server.platform.config import platform_config  # noqa: E402
from openviking.server.platform.db import build_engine, build_session_factory  # noqa: E402
from openviking.server.platform.iam import PostgresIamRepository, RbacService  # noqa: E402

DRILL_PASSWORD = "Drill-User-2026-Dev!"

# 演练快照关注的表（恢复后逐表对照；audit/outbox 单独断言）
SNAPSHOT_TABLES = (
    "iam_accounts",
    "iam_users",
    "iam_user_roles",
    "iam_api_credentials",
    "iam_sessions",
    "iam_audit_events",
    "iam_outbox",
    "platform_content_refs",
    "platform_session_refs",
)


def _psa_principal(psa) -> AuthenticatedUserPrincipal:
    return AuthenticatedUserPrincipal(
        actor_user_id=psa.id,
        actor_account_id=None,
        actor_ov_user_id=None,
        actor_ov_account_id=None,
        user_status="active",
        authentication_method="session",
    )


async def _counts(session: AsyncSession, tables: tuple[str, ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tables:
        row = await session.execute(text(f"SELECT count(*) FROM {t}"))
        out[t] = int(row.scalar() or 0)
    return out


async def _with_engine(url: str, fn) -> None:
    """执行 fn(session_factory)；异常路径也 dispose engine（避免测试库 DROP 阻塞）。"""
    engine = build_engine(url)
    factory = build_session_factory(engine)
    try:
        await fn(factory)
    finally:
        await engine.dispose()


async def prepare_business_data(
    url: str,
    *,
    psa_email: str = "psa@drill.local",
    psa_password: str = "Drill-PSA-2026-Dev!",
) -> dict:
    """在已迁移、已有 PSA 的演练库上构建业务数据：

    - acme Account + 首位 Admin（AdminService，同事务写 outbox）+ alice User；
    - ProvisioningWorker（FakeControlPlane）跑通 outbox → active；
    - alice 登录 Session + 具名 API Key（ovk_u.*）+ 审计事件；
    - Resource/Skill 内容引用（platform_content_refs）、Session 引用；
    - 一个 failed outbox 事件 + failed Account（模拟 Worker 失败，供 retry 校验）。

    返回 {alice_email, alice_password, alice_key, session_raw, failed_account_id}
    供校验阶段使用（演练脚本写入工作目录 json）。
    """
    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    auth = AuthService(repo, rbac)
    result: dict = {}

    async def _run(factory: async_sessionmaker[AsyncSession]) -> None:
        nonlocal result
        async with factory() as session:
            await rbac.seed_catalog(session)
            psa = await repo.get_user_by_normalized_email(session, psa_email.strip().casefold())
            if psa is None:
                raise SystemExit(f"PSA not found: {psa_email}（先运行 bootstrap_cli init）")

            # ── acme Account + 首位 Admin（真实服务路径：同事务写 outbox + 审计）──
            from openviking.server.platform.admin.service import AdminService

            admin_svc = AdminService(repo, rbac, auth)
            acct = await admin_svc.create_account_with_first_admin(
                session,
                actor=_psa_principal(psa),
                account_code="acme",
                account_name="Acme Drills",
                admin_email="admin@acme.drill",
                admin_username="admin",
            )
            account_id = acct.account.id

            # ── alice 普通用户（已知口令，便于演练后登录校验）──
            alice = await repo.create_user(
                session,
                account_id=account_id,
                ov_user_id="ov_user_drill_alice",
                username="alice",
                email="alice@acme.drill",
                display_name="Alice Drill",
                password_hash=hash_password(DRILL_PASSWORD),
                status="active",
            )
            await session.flush()
            await rbac.assign_role(
                session,
                actor_user_id=psa.id,
                actor_account_id=None,
                target_user_id=alice.id,
                role_code="user",
            )
            await repo.append_audit_event(
                session,
                account_id=account_id,
                actor_type="user",
                actor_user_id=psa.id,
                actor_account_id=None,
                authentication_method="session",
                subject_account_id=account_id,
                subject_user_id=alice.id,
                action="user.create",
                target_type="iam_users",
                target_id=str(alice.id),
                scope="platform",
                result="success",
            )
            from openviking.server.platform.provisioning.repository import (
                EVENT_USER_PROVISION,
                ProvisioningRepository,
            )

            outbox = ProvisioningRepository()
            await outbox.enqueue(
                session, event_type=EVENT_USER_PROVISION,
                aggregate_id=alice.id, payload={"account_id": str(account_id)},
            )
            await session.commit()

            # ── Provisioning Worker 跑通 outbox（FakeControlPlane）──
            from openviking.server.platform.provisioning.control_plane import FakeControlPlane
            from openviking.server.platform.provisioning.worker import ProvisioningWorker

            await ProvisioningWorker(repo, outbox, FakeControlPlane()).run_once(
                session, limit=10
            )
            await session.commit()

            # ── 登录 Session + 具名 API Key + 审计 ──
            from openviking.server.platform.auth.sessions import SessionService

            raw_token, csrf, session_id = await SessionService(
                repo, platform_config
            ).create_login_session(
                session, user=alice, ip_hash="ip_hash_drill", user_agent="drill-client"
            )
            key_secret = "drill-secret-" + uuid.uuid4().hex
            key = await repo.create_api_credential(
                session,
                account_id=account_id,
                user_id=alice.id,
                name="drill-codex",
                public_id="drill_pub_" + uuid.uuid4().hex,
                key_hash=sha256_hex(key_secret),
                key_last_four=key_secret[-4:],
                created_by=alice.id,
            )
            await repo.append_audit_event(
                session,
                account_id=account_id,
                actor_type="user",
                actor_user_id=alice.id,
                actor_account_id=account_id,
                actor_session_id=session_id,
                authentication_method="session",
                subject_user_id=alice.id,
                action="credential.create",
                target_type="iam_api_credentials",
                target_id=str(key.id),
                scope="account",
                result="success",
                metadata={"name": "drill-codex"},
            )

            # ── 业务引用数据：Resource/Skill/Session refs ──
            from openviking.server.platform.models import (
                PlatformContentRef,
                PlatformSessionRef,
            )

            session.add(
                PlatformContentRef(
                    account_id=account_id,
                    object_type="resource",
                    visibility="user_private",
                    owner_user_id=alice.id,
                    ov_uri=f"viking://resources/drill/acme/alice/notes-{uuid.uuid4().hex[:8]}",
                    display_name="Drill Notes",
                    source_type="manual",
                    status="active",
                    version=1,
                )
            )
            session.add(
                PlatformContentRef(
                    account_id=account_id,
                    object_type="skill",
                    visibility="user_private",
                    owner_user_id=alice.id,
                    ov_uri=f"viking://skills/drill/acme/{uuid.uuid4().hex[:10]}",
                    canonical_name=f"alice-helper-{uuid.uuid4().hex[:10]}",
                    display_name="Alice Helper",
                    source_type="manual",
                    status="active",
                    version=1,
                )
            )
            session.add(
                PlatformSessionRef(
                    account_id=account_id,
                    owner_user_id=alice.id,
                    ov_session_id="ov_session_drill_" + uuid.uuid4().hex[:8],
                    client_name="drill-web",
                    ov_uri=f"viking://sessions/drill/{uuid.uuid4().hex[:8]}",
                    status="active",
                )
            )
            await session.commit()

            # ── failed outbox 事件 + failed Account（模拟 Worker 失败）──
            from openviking.server.platform.models import IamAccount
            from openviking.server.platform.provisioning.repository import (
                EVENT_ACCOUNT_PROVISION,
            )

            broken = IamAccount(
                ov_account_id="ov_account_broken",
                code="broken",
                display_name="Broken",
                status="failed",
                provisioning_error="simulated failure (drill)",
            )
            session.add(broken)
            await session.flush()
            await outbox.enqueue(
                session, event_type=EVENT_ACCOUNT_PROVISION,
                aggregate_id=broken.id, payload={"account_id": str(broken.id)},
            )
            events = await outbox.list_by_account(session, broken.id)
            assert events, "outbox 事件应已入队"
            await outbox.update_status(
                session,
                events[0],
                status="failed",
                now=datetime.now(timezone.utc),
                attempts=3,
                last_error="simulated failure (drill)",
            )
            await session.commit()
            result = {
                "alice_email": alice.email,
                "alice_password": DRILL_PASSWORD,
                "alice_key": f"ovk_u.{key.public_id}.{key_secret}",
                "session_raw": raw_token,
                "failed_account_id": str(broken.id),
                "prepared_at": datetime.now(timezone.utc).isoformat(),
            }

    await _with_engine(url, _run)
    return result


async def snapshot_counts(url: str) -> dict:
    counts: dict = {}

    async def _run(factory: async_sessionmaker[AsyncSession]) -> None:
        nonlocal counts
        async with factory() as session:
            counts = await _counts(session, SNAPSHOT_TABLES)

    await _with_engine(url, _run)
    return counts


async def fault_inject(url: str) -> None:
    """故障注入（模拟灾难性丢失）：删除 alice/admin（级联清凭证/Session/引用）、
    清空审计、删除 Resource/Skill/Session 引用与 broken 的 outbox 事件。"""

    async def _run(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            # 先清引用表（audit/roles/refs 有 FK 指向 users），再删用户
            await session.execute(
                text("TRUNCATE TABLE iam_audit_events, platform_content_refs, "
                     "platform_session_refs, iam_sessions, iam_api_credentials, "
                     "iam_user_roles CASCADE")
            )
            await session.execute(
                text("DELETE FROM iam_users WHERE email IN "
                     "('alice@acme.drill', 'admin@acme.drill')")
            )
            await session.execute(
                text("DELETE FROM iam_outbox WHERE aggregate_id IN "
                     "(SELECT id FROM iam_accounts WHERE code = 'broken')")
            )
            await session.commit()

    await _with_engine(url, _run)


async def verify_restore(
    url: str,
    *,
    before: dict | None = None,
    session_raw: str | None = None,
    alice_key: str | None = None,
    alice_email: str = "alice@acme.drill",
    alice_password: str = DRILL_PASSWORD,
    retry_failed_account_id: str | None = None,
    psa_email: str = "psa@drill.local",
    require_restore_audit: bool = True,
) -> dict:
    """恢复后一致性校验（验收②⑤⑥）。返回 {ok, checks: [...]}。

    require_restore_audit=False 用于非恢复型演练（版本回滚 Drill B：
    无备份/恢复动作，不要求 backup.restore 审计）。

    - 登录：alice 密码登录成功（Password 侧）；Session/API Key 解析成功；
    - 审计连续性：恢复前快照计数内的审计存在，且新增 backup.restore 审计；
    - 业务数据一致：Resource/Skill/Session 引用计数与快照一致、抽查无重复；
    - outbox 一致：无重复 (event_type, aggregate_id, status) 组合；
    - failed 经 retry 幂等恢复：retry_account 后 Worker 跑通为 completed、
      不产生重复事件。
    """
    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    auth = AuthService(repo, rbac)
    checks: list[dict] = []
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        checks.append({"name": name, "ok": bool(passed), "detail": detail})
        ok = ok and bool(passed)

    async def _run(factory: async_sessionmaker[AsyncSession]) -> None:
        nonlocal ok
        async with factory() as session:
            await rbac.seed_catalog(session)

            # ① 密码登录（Password → Session 侧；登录成功会新增一条 iam_sessions）
            login_extra_sessions = 0
            try:
                login = await auth.login(
                    session, email=alice_email, password=alice_password,
                    ip="127.0.0.1", user_agent="drill-verify",
                )
                check("login_password", True, f"user_id={login.user_id}")
                login_extra_sessions = 1
            except Exception as exc:  # noqa: BLE001
                check("login_password", False, f"{type(exc).__name__}: {exc}")

            # ② Session 解析
            try:
                if session_raw:
                    p = await resolve_session_principal(session, repo, rbac, session_raw)
                    check("session_resolve", p.authentication_method == "session",
                          f"user={p.actor_user_id}")
                else:
                    check("session_resolve", True, "no raw token provided (skipped)")
            except Exception as exc:  # noqa: BLE001
                check("session_resolve", False, f"{type(exc).__name__}: {exc}")

            # ③ API Key 解析
            try:
                if alice_key:
                    p = await resolve_api_key_principal(session, repo, rbac, alice_key)
                    check("api_key_resolve", p.authentication_method == "api_key",
                          f"user={p.actor_user_id}")
                else:
                    check("api_key_resolve", True, "no key provided (skipped)")
            except Exception as exc:  # noqa: BLE001
                check("api_key_resolve", False, f"{type(exc).__name__}: {exc}")

            # ④ 审计连续性 + 恢复审计
            n_audit = int((await session.execute(
                text("SELECT count(*) FROM iam_audit_events"))).scalar())
            n_restore_audits = int((await session.execute(
                text("SELECT count(*) FROM iam_audit_events WHERE action = 'backup.restore'")
            )).scalar())
            before_audit = (before or {}).get("iam_audit_events", 0)
            check("audit_continuity", n_audit >= before_audit,
                  f"audit={n_audit} before={before_audit}")
            if require_restore_audit:
                check("restore_audited", n_restore_audits >= 1,
                      "iam_audit_events 含 backup.restore 记录（恢复操作有审计）")

            # ⑤ 业务数据一致 + 无重复
            counts = await _counts(session, SNAPSHOT_TABLES)
            for t in SNAPSHOT_TABLES:
                exp = (before or {}).get(t)
                if exp is None or t == "iam_audit_events":
                    continue  # 审计单独断言（恢复后新增）
                if t == "iam_sessions":
                    # 校验中的登录动作新增一条 Session（演练校验自身副作用）
                    check(f"count_{t}", counts[t] == exp + login_extra_sessions,
                          f"{counts[t]} == {exp} + {login_extra_sessions}")
                    continue
                check(f"count_{t}", counts[t] == exp, f"{counts[t]} == {exp}")
            dups = int((await session.execute(text(
                "SELECT count(*) FROM (SELECT account_id, ov_uri FROM platform_content_refs "
                "GROUP BY account_id, ov_uri HAVING count(*) > 1) d"))).scalar())
            check("no_dup_content_refs", dups == 0, f"duplicates={dups}")
            dups = int((await session.execute(text(
                "SELECT count(*) FROM (SELECT account_id, ov_session_id FROM platform_session_refs "
                "GROUP BY account_id, ov_session_id HAVING count(*) > 1) d"))).scalar())
            check("no_dup_session_refs", dups == 0, f"duplicates={dups}")

            # ⑥ outbox 一致 + failed 幂等重试恢复
            dups = int((await session.execute(text(
                "SELECT count(*) FROM (SELECT event_type, aggregate_id, status FROM iam_outbox "
                "GROUP BY event_type, aggregate_id, status HAVING count(*) > 1) d"))).scalar())
            check("no_dup_outbox", dups == 0, f"duplicates={dups}")

            if retry_failed_account_id:
                from openviking.server.platform.provisioning.control_plane import FakeControlPlane
                from openviking.server.platform.provisioning.repository import (
                    ProvisioningRepository,
                )
                from openviking.server.platform.provisioning.service import (
                    ProvisioningNotRetryableError,
                    ProvisioningService,
                )
                from openviking.server.platform.provisioning.worker import ProvisioningWorker

                prov = ProvisioningService(repo, ProvisioningRepository(), FakeControlPlane())
                psa = await repo.get_user_by_normalized_email(
                    session, psa_email.strip().casefold()
                )
                if psa is None:
                    check("retry_failed_account", False, "PSA not found for retry")
                else:
                    try:
                        first = await prov.retry_account(
                            session, actor=_psa_principal(psa),
                            account_id=uuid.UUID(retry_failed_account_id),
                            request_id="drill-retry-1",
                        )
                        await session.commit()
                        processed = await ProvisioningWorker(
                            repo, ProvisioningRepository(), FakeControlPlane()
                        ).run_once(session, limit=10)
                        await session.commit()
                        n_events = int((await session.execute(
                            text("SELECT count(*) FROM iam_outbox WHERE aggregate_id = :aid"),
                            {"aid": retry_failed_account_id})).scalar())
                        n_failed = int((await session.execute(
                            text("SELECT count(*) FROM iam_outbox WHERE aggregate_id = :aid "
                                 "AND status = 'failed'"),
                            {"aid": retry_failed_account_id})).scalar())
                        check("retry_failed_account",
                              n_events == 1 and n_failed == 0,
                              f"events={n_events} failed_left={n_failed} "
                              f"retried={first.retried_events} worker_processed={processed}")
                        # 幂等：已 active 且无未完成事件 → 409，事件数不变
                        second_result = "no-raise"
                        try:
                            await prov.retry_account(
                                session, actor=_psa_principal(psa),
                                account_id=uuid.UUID(retry_failed_account_id),
                                request_id="drill-retry-2",
                            )
                        except ProvisioningNotRetryableError:
                            second_result = "409"
                        n_events_after = int((await session.execute(
                            text("SELECT count(*) FROM iam_outbox WHERE aggregate_id = :aid"),
                            {"aid": retry_failed_account_id})).scalar())
                        check("retry_idempotent_no_dup",
                              second_result == "409" and n_events_after == n_events,
                              f"second={second_result} events_after={n_events_after}")
                    except Exception as exc:  # noqa: BLE001
                        check("retry_failed_account", False, f"{type(exc).__name__}: {exc}")

    await _with_engine(url, _run)
    return {"ok": ok, "checks": checks}


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="P5-E3 drill library")
    parser.add_argument("command", choices=["prepare", "snapshot", "fault-inject", "verify"])
    parser.add_argument("url")
    parser.add_argument("--psa-email", default="psa@drill.local")
    parser.add_argument("--psa-password", default="Drill-PSA-2026-Dev!")
    parser.add_argument("--session-raw", default=None)
    parser.add_argument("--alice-key", default=None)
    parser.add_argument("--failed-account-id", default=None)
    parser.add_argument("--snapshot", default=None, help="verify 时对照的 snapshot json")
    parser.add_argument("--skip-restore-audit", action="store_true",
                        help="非恢复型演练（版本回滚）不要求 backup.restore 审计")
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args(argv)

    if args.command == "prepare":
        data = await prepare_business_data(
            args.url, psa_email=args.psa_email, psa_password=args.psa_password
        )
        out = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(out)
        print(out)
    elif args.command == "snapshot":
        counts = await snapshot_counts(args.url)
        out = json.dumps(counts, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(out)
        print(out)
    elif args.command == "fault-inject":
        await fault_inject(args.url)
        print("fault injected")
    elif args.command == "verify":
        before = json.loads(Path(args.snapshot).read_text()) if args.snapshot else None
        report = await verify_restore(
            args.url,
            before=before,
            session_raw=args.session_raw,
            alice_key=args.alice_key,
            retry_failed_account_id=args.failed_account_id,
            require_restore_audit=not args.skip_restore_audit,
        )
        out = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(out)
        print(out)
        return 0 if report.get("ok", True) else 1
    return 0


def main() -> int:
    return asyncio.run(_main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
