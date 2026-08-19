"""P5-E2（14 号计划 §99.2）：bootstrap 正式部署命令测试。

验收映射：
- ② 初始化命令幂等（重复执行拒绝且不覆盖密码）、密码仅一次展示（env 注入
  或缺省生成）、库中仅 Argon2id hash（06 §14.4/§15.2）；
- ③ 无旧 Key 导入路径：新 Key 均为 `ovk_u.*` 且由 IAM 签发（verify 链路）；
- ⑦ 三角色经 Session/API Key/OAuth 三凭证获得一致权限与数据范围
  （06 §15.2 步骤 8，`ov platform verify` 落地）；
- 15.2 八步初始化第 1–2、8 步的可重复执行路径（runbook 见
  docs/design/product-platform/v0.1/p5-e2-init-runbook.md）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.platform.auth.password import sha256_hex, verify_password
from openviking.server.platform.bootstrap_cli import (
    cmd_init,
    cmd_status,
    cmd_verify,
)
from openviking.server.platform.models import IamOAuthGrant, IamOAuthToken, IamUser
from tests.platform.helpers import (
    build_auth_setup,
    create_login_session,
)


@pytest.fixture()
def init_env(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
    test_database: str,
) -> None:
    """CLI 环境：数据库 URL 指向测试库 + init/verify 输入。"""
    monkeypatch.setenv(
        "OV_PLATFORM_DATABASE_URL",
        test_database,
    )


async def _run_init(
    monkeypatch: pytest.MonkeyPatch,
    *,
    password: str | None = None,
) -> tuple[int, str]:
    """执行 `init`（跳过 migration，测试库已 head）；返回 (exit_code, stdout)。"""
    import io
    from contextlib import redirect_stdout

    monkeypatch.setenv("OV_PLATFORM_INIT_PSA_EMAIL", "psa@platform.local")
    monkeypatch.setenv("OV_PLATFORM_INIT_PSA_USERNAME", "psa")
    if password is not None:
        monkeypatch.setenv("OV_PLATFORM_INIT_PSA_PASSWORD", password)

    class _Args:
        skip_migrations = True

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = await cmd_init(_Args())
    return code, buffer.getvalue()


# ── ② 初始化命令：建 PSA、密码只展示一次、库中仅 Argon2id ──


async def test_init_creates_psa_password_printed_once(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    init_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC②：env 不注入密码时，生成密码仅一次展示；库中仅 Argon2id hash。"""
    code, out = await _run_init(monkeypatch)
    assert code == 0
    assert "首位 Platform Super Admin 已创建" in out
    assert ">>> " in out  # 密码仅本次展示

    user = (
        await session.execute(select(IamUser).where(IamUser.account_id.is_(None)))
    ).scalar_one()
    assert user.email == "psa@platform.local"
    assert user.password_hash.startswith("$argon2id$"), "库中必须仅存 Argon2id hash"
    # 展示的密码可登录（Argon2id 可校验），且不再可读
    shown = out.split(">>> ")[1].split(" <<<")[0].strip()
    assert verify_password(user.password_hash, shown)
    assert code == 0


async def test_init_password_env_injected_not_printed(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    init_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC②：密码经环境变量注入时不打印明文；库中仅 Argon2id hash。"""
    code, out = await _run_init(monkeypatch, password="Sup3r-Secure-Init-Pass-2026")
    assert code == 0
    assert ">>> " not in out
    assert "Sup3r-Secure-Init-Pass-2026" not in out

    user = (
        await session.execute(select(IamUser).where(IamUser.account_id.is_(None)))
    ).scalar_one()
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(user.password_hash, "Sup3r-Secure-Init-Pass-2026")
    assert "Sup3r-Secure-Init-Pass-2026" not in user.password_hash


async def test_init_idempotent_rejection(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    init_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC②：重复执行 init 幂等拒绝，不覆盖现有密码（06 §15.2 步骤 2）。"""

    code1, _ = await _run_init(monkeypatch, password="First-Secure-Pass-2026")
    assert code1 == 0

    code2, _ = await _run_init(monkeypatch, password="Second-Secure-Pass-2026")
    assert code2 == 1
    # 密码未被覆盖：仍是第一次的
    user = (
        await session.execute(select(IamUser).where(IamUser.account_id.is_(None)))
    ).scalar_one()
    assert verify_password(user.password_hash, "First-Secure-Pass-2026")
    assert not verify_password(user.password_hash, "Second-Secure-Pass-2026")
    # PSA 仍唯一
    rows = list(
        (await session.execute(select(IamUser).where(IamUser.account_id.is_(None)))).scalars()
    )
    assert len(rows) == 1


# ── ③ 新 IAM 签发 Key 全链路：ovk_u.* 前缀、无旧 Key 导入路径 ──


async def test_keys_issued_by_iam_are_ovk_u_prefixed(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    """AC③：用户建 Key 走 IAM ApiCredentialService，完整 Key 均为 `ovk_u.*`。"""
    from openviking.server.platform.auth.api_keys import ApiCredentialService

    setup = await build_auth_setup(session)
    service = ApiCredentialService(setup.repo)
    created = await service.create_key(session, user=setup.alice, name="Codex")
    await session.commit()

    assert created.full_key.startswith("ovk_u.")
    parts = created.full_key.split(".")
    assert len(parts) == 3 and parts[0] == "ovk_u"
    # 库中仅存 hash + 前缀 + 末四位（04 §10.3，06 §14.4）
    cred = await setup.repo.get_api_credential_by_public_id(session, parts[1])
    assert cred is not None
    assert created.full_key not in cred.key_hash
    assert cred.key_hash == sha256_hex(parts[2])
    assert cred.key_last_four == parts[2][-4:]
    assert cred.status == "active"


# ── ⑦ `ov platform verify`：三凭证一致验证（06 §15.2 步骤 8）──


async def _mint_oauth_access_token(
    session: AsyncSession, setup, user: IamUser, *, secret: str
) -> IamOAuthToken:
    """直写 IAM 数据库构造有效 access token + grant（04 §10.13 字段契约）。"""
    from openviking.server.platform.models import IamOAuthClient

    client = IamOAuthClient(
        client_id="test-client",
        client_name="verify-client",
        redirect_uris=["http://127.0.0.1:9999/cb"],
        grant_types=["authorization_code"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="mcp",
        status="active",
    )
    session.add(client)
    await session.flush()
    grant = IamOAuthGrant(
        account_id=setup.acme.id,
        user_id=user.id,
        client_id="test-client",
        scope="mcp",
        status="active",
    )
    session.add(grant)
    await session.flush()
    token = IamOAuthToken(
        token_type="access",
        token_hash=sha256_hex(secret),
        client_id="test-client",
        grant_id=grant.id,
        account_id=setup.acme.id,
        user_id=user.id,
        role="user",
        scope="mcp",
        status="active",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(token)
    await session.commit()
    return token


async def test_verify_three_credentials_consistent(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    init_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC⑦：同一用户的 Session/API Key/OAuth 三凭证解析一致（步骤 8）。"""
    setup = await build_auth_setup(session)

    # Session（Cookie token）
    raw, _, _ = await create_login_session(setup, session, setup.alice)
    # API Key（已知 secret，格式 ovk_u.*）
    key_secret = "s3cret-" + uuid.uuid4().hex
    cred = await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=setup.alice.id,
        name="Codex",
        public_id="pub2_" + uuid.uuid4().hex[:12],
        key_hash=sha256_hex(key_secret),
        key_last_four=key_secret[-4:],
        created_by=setup.alice.id,
    )
    await session.commit()
    full_key = f"ovk_u.{cred.public_id}.{key_secret}"
    # OAuth access token
    oauth_secret = "oauth-" + uuid.uuid4().hex
    await _mint_oauth_access_token(session, setup, setup.alice, secret=oauth_secret)

    monkeypatch.setenv("OV_PLATFORM_VERIFY_EMAIL", "alice@acme.com")
    monkeypatch.setenv("OV_PLATFORM_VERIFY_SESSION_TOKEN", raw)
    monkeypatch.setenv("OV_PLATFORM_VERIFY_API_KEY", full_key)
    monkeypatch.setenv("OV_PLATFORM_VERIFY_OAUTH_TOKEN", oauth_secret)

    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = await cmd_verify(_Args())
    assert code == 0
    out = buffer.getvalue()
    assert "session" in out and "api_key" in out and "oauth" in out
    assert "三凭证权限与数据范围一致" in out


async def test_verify_mismatch_fails(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    init_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC⑦ 反向：凭证与目标用户不一致时 verify 非零退出。"""
    setup = await build_auth_setup(session)
    raw, _, _ = await create_login_session(setup, session, setup.admin)  # admin 的 session

    monkeypatch.setenv("OV_PLATFORM_VERIFY_EMAIL", "alice@acme.com")  # 目标是 alice
    monkeypatch.setenv("OV_PLATFORM_VERIFY_SESSION_TOKEN", raw)

    import io
    from contextlib import redirect_stderr

    buffer = io.StringIO()
    with redirect_stderr(buffer):
        code = await cmd_verify(_Args())
    assert code == 1
    assert "不一致" in buffer.getvalue()


async def test_status_cmd_reads_deployment_state(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    init_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status 只读巡检：migration 版本 + 对象计数，不修改数据。"""
    await build_auth_setup(session)

    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = await cmd_status(_Args())
    assert code == 0
    out = buffer.getvalue()
    assert "accounts" in out and "users" in out and "api_keys" in out
    assert "migration" in out and "head" in out


class _Args:
    skip_migrations = True
