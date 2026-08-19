"""P1-E3 测试共享夹具：种子 + PSA + Account + 用户 + 登录 Session 快速构造。

与 test_rbac.py 的本地 helper 同构，但用户使用真实 Argon2id 密码哈希
（登录/改密/重置测试需要）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import hash_password, new_session_token, sha256_hex
from openviking.server.platform.auth.rate_limit import LoginRateLimiter
from openviking.server.platform.auth.service import AuthService
from openviking.server.platform.auth.sessions import SessionService
from openviking.server.platform.config import platform_config
from openviking.server.platform.iam import PostgresIamRepository, RbacService
from openviking.server.platform.iam.permissions import ACCOUNT_ADMIN, USER
from openviking.server.platform.models import IamAccount, IamApiCredential, IamUser

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def run_provisioning(
    session: AsyncSession,
    *,
    control_plane=None,
    limit: int | None = None,
) -> tuple[int, object]:
    """P2-E1：执行一轮 ProvisioningWorker（开发态 FakeControlPlane）。

    返回 (processed, worker)；worker._service._control_plane 为实际使用的
    控制面（需共享实例的幂等/重放测试用同一 worker/control_plane）。
    """
    from openviking.server.platform.provisioning.control_plane import FakeControlPlane
    from openviking.server.platform.provisioning.repository import ProvisioningRepository
    from openviking.server.platform.provisioning.worker import ProvisioningWorker

    control_plane = control_plane or FakeControlPlane()
    worker = ProvisioningWorker(PostgresIamRepository(), ProvisioningRepository(), control_plane)
    processed = await worker.run_once(session, limit=limit)
    return processed, worker


async def create_account(
    repo: PostgresIamRepository,
    session: AsyncSession,
    code: str,
) -> IamAccount:
    account = await repo.create_account(
        session,
        ov_account_id=f"ov_account_{code}",
        code=code,
        display_name=code,
        status="active",
    )
    await session.commit()
    return account


async def create_user(
    repo: PostgresIamRepository,
    session: AsyncSession,
    account: IamAccount | None,
    *,
    email: str,
    username: str,
    status: str = "active",
    password: str = DEFAULT_PASSWORD,
) -> IamUser:
    return await repo.create_user(
        session,
        account_id=account.id if account is not None else None,
        ov_user_id=None if account is None else f"ov_user_{username}",
        username=username,
        email=email,
        display_name=username,
        password_hash=hash_password(password),
        status=status,
    )


@dataclass
class AuthSetup:
    """P1-E3 标准测试环境：三内置角色 + PSA + acme Account(admin/alice)。"""

    repo: PostgresIamRepository
    rbac: RbacService
    auth: AuthService
    sessions: SessionService
    psa: IamUser
    acme: IamAccount
    admin: IamUser
    alice: IamUser


async def build_auth_setup(
    session: AsyncSession,
    *,
    limiter: LoginRateLimiter | None = None,
) -> AuthSetup:
    """种子 + PSA + acme(admin=account_admin, alice=user)，全部 active。"""
    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    auth = AuthService(repo, rbac, limiter=limiter)
    sessions = SessionService(repo, platform_config)

    await rbac.seed_catalog(session)
    psa = await create_user(repo, session, None, email="psa@platform.local", username="psa")
    await session.commit()
    await rbac.assign_platform_super_admin(session, target_user_id=psa.id)

    acme = await create_account(repo, session, "acme")
    admin = await create_user(repo, session, acme, email="admin@acme.com", username="admin")
    alice = await create_user(repo, session, acme, email="alice@acme.com", username="alice")
    await session.commit()

    await rbac.assign_role(
        session,
        actor_user_id=psa.id,
        actor_account_id=None,
        target_user_id=admin.id,
        role_code=ACCOUNT_ADMIN,
    )
    await rbac.assign_role(
        session,
        actor_user_id=psa.id,
        actor_account_id=None,
        target_user_id=alice.id,
        role_code=USER,
    )
    await session.commit()
    return AuthSetup(repo=repo, rbac=rbac, auth=auth, sessions=sessions, psa=psa, acme=acme, admin=admin, alice=alice)


async def create_login_session(
    setup: AuthSetup,
    session: AsyncSession,
    user: IamUser,
    *,
    ip: str = "127.0.0.1",
    user_agent: str | None = None,
) -> tuple[str, str, object]:
    """创建登录 Session；返回 (raw_token, csrf_token, session_row_id)。"""
    raw, csrf, session_id = await setup.sessions.create_login_session(
        session, user=user, ip_hash=sha256_hex(ip), user_agent=user_agent
    )
    await session.commit()
    return raw, csrf, session_id


def new_raw_token() -> str:
    return new_session_token()


async def create_api_key(
    repo: PostgresIamRepository,
    session: AsyncSession,
    account: IamAccount,
    user: IamUser,
    *,
    name: str = "Codex",
    public_id: str | None = None,
) -> IamApiCredential:
    """创建 API Key 记录（P1-E4 交付 me/api-keys 前，管理端测试直写 repository；
    仅存 SHA-256(secret)+末四位，04 §10.3）。"""
    import secrets

    secret = "dev-secret-" + secrets.token_hex(8)
    cred = await repo.create_api_credential(
        session,
        account_id=account.id,
        user_id=user.id,
        name=name,
        public_id=public_id or f"pub_{secrets.token_hex(8)}",
        key_hash=sha256_hex(secret),
        key_last_four=secret[-4:],
        created_by=user.id,
    )
    await session.commit()
    return cred
