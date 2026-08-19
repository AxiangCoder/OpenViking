"""P5-E2（14 号计划 §99.2）：一次性初始化与生产运维部署命令。

按 06 §15.2 八步初始化顺序落地为可重复执行的部署命令（06 §15.1：全新
PostgreSQL 是唯一身份事实来源，无旧凭证导入/并行期）：

- `init`：步骤 1+2 —— 全新 Platform schema migration（alembic upgrade head）
  后用一次性命令创建首位 Platform Super Admin；密码经环境变量注入
  （`OV_PLATFORM_INIT_PSA_PASSWORD`，Secret Manager 推荐）或服务端生成
  仅展示一次；已存在 PSA 时幂等拒绝（不重复初始化、不覆盖密码）。
- `status`：只读巡检 —— migration 版本、PSA/Account/User/Key 计数、
  Provisioning backlog、软删除待清理数量与最早 purge_after（运维排障）。
- `verify`：步骤 8 —— 三凭证一致验证：对同一用户，Session/API Key/OAuth
  解析出的 Principal 权限集合与数据范围必须一致（07 §21 条目 8）。

环境变量（06 §16.3：数据库 URL 与口令必须经环境变量/Secret Manager 注入）：
- `OV_PLATFORM_DATABASE_URL`：Platform PostgreSQL DSN（缺省本地开发默认值）；
- `OV_PLATFORM_INIT_PSA_EMAIL` / `OV_PLATFORM_INIT_PSA_USERNAME` /
  `OV_PLATFORM_INIT_PSA_DISPLAY_NAME` / `OV_PLATFORM_INIT_PSA_PASSWORD`；
- `OV_PLATFORM_VERIFY_*`：verify 的实时凭证输入（见 verify 子命令帮助）。

用法：
    OV_PLATFORM_DATABASE_URL='postgresql://...' \\
      OV_PLATFORM_INIT_PSA_EMAIL=psa@example.com \\
      OV_PLATFORM_INIT_PSA_USERNAME=psa \\
      python -m openviking.server.platform.bootstrap_cli init
    python -m openviking.server.platform.bootstrap_cli status
    python -m openviking.server.platform.bootstrap_cli verify
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from openviking.server.platform.config import PlatformConfig
from openviking.server.platform.db import build_engine, build_session_factory

ENV_PSA_EMAIL = "OV_PLATFORM_INIT_PSA_EMAIL"
ENV_PSA_USERNAME = "OV_PLATFORM_INIT_PSA_USERNAME"
ENV_PSA_DISPLAY_NAME = "OV_PLATFORM_INIT_PSA_DISPLAY_NAME"
ENV_PSA_PASSWORD = "OV_PLATFORM_INIT_PSA_PASSWORD"

ENV_VERIFY_EMAIL = "OV_PLATFORM_VERIFY_EMAIL"
ENV_VERIFY_SESSION = "OV_PLATFORM_VERIFY_SESSION_TOKEN"
ENV_VERIFY_API_KEY = "OV_PLATFORM_VERIFY_API_KEY"
ENV_VERIFY_OAUTH = "OV_PLATFORM_VERIFY_OAUTH_TOKEN"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _alembic_config(database_url: str):
    from alembic.config import Config

    cfg = Config(str(_REPO_ROOT / "openviking/server/platform/alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _platform_config() -> PlatformConfig:
    return PlatformConfig()


def _validate_init_env() -> tuple[str, str, str | None, str | None]:
    email = os.environ.get(ENV_PSA_EMAIL, "").strip()
    username = os.environ.get(ENV_PSA_USERNAME, "").strip()
    if not email or not username:
        raise SystemExit(
            f"init 需要 {ENV_PSA_EMAIL} 与 {ENV_PSA_USERNAME}（06 §15.2 步骤 2）"
        )
    password = os.environ.get(ENV_PSA_PASSWORD, "").strip() or None
    if password is not None and len(password) < 12:
        raise SystemExit("OV_PLATFORM_INIT_PSA_PASSWORD 长度必须 >= 12（03 §8.3）")
    display_name = os.environ.get(ENV_PSA_DISPLAY_NAME, "").strip() or None
    return email, username, display_name, password


async def _run_migrations(database_url: str) -> None:
    from alembic import command

    command.upgrade(_alembic_config(database_url), "head")


async def cmd_init(args: argparse.Namespace) -> int:
    """步骤 1+2：全新 schema migration → 一次性创建首位 PSA。"""
    email, username, display_name, password = _validate_init_env()
    config = _platform_config()

    if not args.skip_migrations:
        print(f"[init] 步骤 1/2：执行 Platform schema migration -> head（{config.database_url.split('@')[-1]}）")
        await _run_migrations(config.database_url)

    from openviking.server.platform.auth.service import AuthService
    from openviking.server.platform.errors import PlatformError
    from openviking.server.platform.iam import PostgresIamRepository, RbacService

    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    try:
        repo = PostgresIamRepository()
        auth = AuthService(repo, RbacService(repo))
        async with factory() as session:
            try:
                result = await auth.bootstrap_platform_super_admin(
                    session,
                    email=email,
                    username=username,
                    display_name=display_name,
                    password=password,
                    request_id="platform-init",
                )
            except PlatformError as exc:
                if "PSA_ALREADY_BOOTSTRAPPED" in str(exc):
                    print(
                        "[init] 拒绝重复初始化：平台已存在 Platform Super Admin"
                        "（幂等拒绝，不覆盖现有密码，06 §15.2 步骤 2）。",
                        file=sys.stderr,
                    )
                    return 1
                if "EMAIL_ALREADY_EXISTS" in str(exc):
                    print("[init] 失败：该邮箱已存在。", file=sys.stderr)
                    return 1
                raise
            await session.commit()
    finally:
        await engine.dispose()

    print("[init] 步骤 2/2：首位 Platform Super Admin 已创建。")
    print(f"  user_id : {result.user_id}")
    print(f"  email   : {email}")
    print(f"  username: {username}")
    if password is None:
        print("  初始密码（仅本次展示，请立即安全保存并线下交接，07 §21 条目 18）：")
        print(f"  >>> {result.initial_password} <<<")
    else:
        print("  密码已由 OV_PLATFORM_INIT_PSA_PASSWORD 注入（库中仅存 Argon2id hash）。")
    print("  后续步骤见 06 §15.2 步骤 3–8（PSA 建 Account → Worker 初始化 → "
          "Admin 建 User → 用户建 Key → 配置客户端 → 三凭证验证）。")
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    """只读巡检：migration 版本、对象计数与 backlog/清理待办。"""
    from openviking.server.platform.health import PlatformHealthChecks
    from openviking.server.platform.models import IamAccount, IamApiCredential, IamUser

    config = _platform_config()
    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    try:
        checks = PlatformHealthChecks(factory, config)
        platform = await checks.all_checks()
        async with factory() as session:
            psa = (
                await session.execute(
                    select(func.count()).select_from(IamUser).where(IamUser.account_id.is_(None))
                )
            ).scalar_one()
            accounts = (
                await session.execute(select(func.count()).select_from(IamAccount))
            ).scalar_one()
            users = (
                await session.execute(select(func.count()).select_from(IamUser))
            ).scalar_one()
            keys = (
                await session.execute(select(func.count()).select_from(IamApiCredential))
            ).scalar_one()
        print("[status] Platform 部署状态")
        print(f"  migration : {platform['migration']['detail']}")
        print(f"  accounts  : {accounts}")
        print(f"  users     : {users}  (PSA 存在: {psa > 0})")
        print(f"  api_keys  : {keys}")
        print(f"  provisioning : {platform['provisioning']}")
        print(f"  session_cleanup : {platform['session_cleanup']}")
        print(f"  purge : {platform['purge']}")
        ready = checks.is_ready(platform)
        print(f"  整体判定 : {'ready' if ready else 'NOT READY'}")
        return 0 if ready else 1
    finally:
        await engine.dispose()


async def cmd_verify(args: argparse.Namespace) -> int:
    """步骤 8：三凭证一致验证（Session/API Key/OAuth → 同一权限与数据范围）。"""
    from openviking.server.platform.auth.principals import (
        resolve_api_key_principal,
        resolve_oauth_token_principal,
        resolve_session_principal,
    )
    from openviking.server.platform.iam import PostgresIamRepository, RbacService

    email = os.environ.get(ENV_VERIFY_EMAIL, "").strip().casefold()
    session_token = os.environ.get(ENV_VERIFY_SESSION, "").strip() or None
    api_key = os.environ.get(ENV_VERIFY_API_KEY, "").strip() or None
    oauth_token = os.environ.get(ENV_VERIFY_OAUTH, "").strip() or None
    if not email:
        raise SystemExit(f"verify 需要 {ENV_VERIFY_EMAIL}")
    if not (session_token or api_key or oauth_token):
        raise SystemExit(
            f"verify 至少需要一个凭证：{ENV_VERIFY_SESSION} / "
            f"{ENV_VERIFY_API_KEY} / {ENV_VERIFY_OAUTH}"
        )

    config = _platform_config()
    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    try:
        repo = PostgresIamRepository()
        rbac = RbacService(repo)
        async with factory() as session:
            user = await repo.get_user_by_normalized_email(session, email)
            if user is None:
                print("[verify] 用户不存在。", file=sys.stderr)
                return 1
            expected_perms = await rbac.get_user_permissions(session, user_id=user.id)
            expected_scope = {
                "account_id": user.account_id,
                "user_id": user.id,
            }
            results: list[tuple[str, dict]] = []
            failures: list[str] = []
            if session_token:
                principal = await resolve_session_principal(session, repo, rbac, session_token)
                results.append(("session", principal.permissions))
                _check_consistency("session", principal, expected_scope, expected_perms, failures)
            if api_key:
                principal = await resolve_api_key_principal(session, repo, rbac, api_key)
                results.append(("api_key", principal.permissions))
                _check_consistency("api_key", principal, expected_scope, expected_perms, failures)
            if oauth_token:
                from openviking.server.platform.iam.pg_oauth_store import PostgresOAuthStore

                store = PostgresOAuthStore(factory, label="platform-verify")
                principal = await resolve_oauth_token_principal(
                    session, repo, rbac, store, oauth_token
                )
                results.append(("oauth", principal.permissions))
                _check_consistency("oauth", principal, expected_scope, expected_perms, failures)

            for method, perms in results:
                print(f"  {method:8s} : {len(perms)} permissions")
            if failures:
                for failure in failures:
                    print(f"  [FAIL] {failure}", file=sys.stderr)
                return 1
            print("[verify] 三凭证权限与数据范围一致（06 §15.2 步骤 8，07 §21 条目 8）。")
            return 0
    finally:
        await engine.dispose()


def _check_consistency(
    method: str,
    principal,
    expected_scope: dict,
    expected_perms,
    failures: list[str],
) -> None:
    if (principal.actor_user_id, principal.actor_account_id) != (
        expected_scope["user_id"],
        expected_scope["account_id"],
    ):
        failures.append(f"{method} 解析出的 Actor 与目标用户不一致")
    if set(principal.permissions) != set(expected_perms.permissions):
        failures.append(f"{method} 权限集合与实时 RBAC 不一致")
    if set(principal.role_codes) != set(expected_perms.role_codes):
        failures.append(f"{method} 角色集合与实时 RBAC 不一致")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ov platform",
        description="OpenViking 产品化平台一次性初始化与运维命令（06 §15.2，14 号计划 §99.2）。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="步骤 1+2：schema migration + 创建首位 PSA（幂等拒绝重复）")
    init.add_argument(
        "--skip-migrations",
        action="store_true",
        help="跳过 migration（已迁移过、只需初始化 PSA 时使用）",
    )
    init.set_defaults(handler=cmd_init)

    status = sub.add_parser("status", help="只读巡检：migration 版本、对象计数、backlog/清理待办")
    status.set_defaults(handler=cmd_status)

    verify = sub.add_parser("verify", help="步骤 8：三凭证一致验证（Session/API Key/OAuth）")
    verify.set_defaults(handler=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
