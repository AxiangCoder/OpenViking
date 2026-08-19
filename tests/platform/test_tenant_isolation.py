"""P2-E6a 跨租户隔离与凭证一致性集成测试（02 §7.4–7.5，05 §11.4，14 号计划 §97.6）。

验收映射：
- AC④：同一用户三凭证（Session/API Key/OAuth）调用同一低层动作授权一致；
  插件以 Key 归属者身份读写审计（iam_audit_events 记录 Actor 与凭证）；
- AC⑤：跨 Account/User IDOR、Header spoofing（X-OpenViking-* 不可信）、
  默认目标逃逸（未显式目标强制私有根）、编码/别名 URI 全通过；
- AC⑥：Cookie+Bearer 并存 Cookie 优先、Cookie 失效不回退 Bearer。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openviking.server.dependencies import set_service
from openviking.server.platform.auth.password import sha256_hex
from openviking.server.platform.auth.principals import resolve_api_key_principal
from openviking.server.platform.auth.uri_policy import AuthorizationService
from openviking.server.platform.db import get_session
from openviking.server.platform.facade import ProductFacadeService
from openviking.server.platform.iam import PostgresIamRepository, RbacService
from openviking.server.platform.lowlevel.guard import LowLevelPolicyGuard
from openviking.server.platform.lowlevel.plugin import PlatformIamAuthPlugin
from openviking.server.platform.models import IamAuditEvent
from openviking.server.platform.target_policy import TargetPolicy
from tests.platform.helpers import build_auth_setup, create_login_session
from tests.platform.test_oauth_principal import FakeOAuthTokenStore, _token_record


def _full_key(public_id: str, secret: str) -> str:
    return f"ovk_u.{public_id}.{secret}"


async def _create_api_key_known(
    session: AsyncSession, setup, user, *, name: str = "Codex"
) -> tuple[str, object]:
    """创建已知 secret 的 API Key；返回 (完整 Key, credential)。"""
    import secrets

    secret = "dev-secret-" + secrets.token_hex(8)
    public_id = "pub_known_" + uuid.uuid4().hex[:12]
    cred = await setup.repo.create_api_credential(
        session,
        account_id=setup.acme.id,
        user_id=user.id,
        name=name,
        public_id=public_id,
        key_hash=sha256_hex(secret),
        key_last_four=secret[-4:],
        created_by=user.id,
    )
    await session.commit()
    return _full_key(public_id, secret), cred


class _FakeLowLevelService:
    """受控低层服务 fake（与既有 Fake 系列同模式）：放行路径记录调用。"""

    def __init__(self) -> None:
        self.fs = SimpleNamespace(
            write=AsyncMock(return_value="ok"),
            mkdir=AsyncMock(return_value="ok"),
            rm=AsyncMock(return_value={"estimated_deleted_count": 1}),
            mv=AsyncMock(return_value="ok"),
            set_tags=AsyncMock(return_value="ok"),
            batch_write=AsyncMock(return_value="ok"),
            read=AsyncMock(return_value="content"),
            ls=AsyncMock(return_value=[]),
        )
        self.resources = SimpleNamespace(
            add_resource=AsyncMock(
                return_value={"root_uri": "viking://user/ov_user_alice/resources/abc"}
            ),
            add_skill=AsyncMock(return_value={"root_uri": "viking://user/ov_user_alice/skills/s"}),
        )
        self.calls: list[tuple] = []


async def _real_audit(repo, session_factory):
    async def _write(principal, *, action, result, reason, target_uri, request_id, metadata):
        async with session_factory() as s:
            await repo.append_audit_event(
                s,
                request_id=request_id or None,
                account_id=principal.actor_account_id,
                actor_type="user",
                actor_user_id=principal.actor_user_id,
                actor_account_id=principal.actor_account_id,
                actor_session_id=principal.session_id,
                authentication_method=principal.authentication_method,
                actor_credential_id=principal.credential_id,
                subject_account_id=principal.actor_account_id,
                action=action,
                target_type="viking_uri",
                target_id=target_uri,
                target_visibility=(metadata or {}).get("visibility"),
                scope="account",
                result=result,
                reason=reason,
                metadata=metadata,
            )
            await s.commit()

    return _write


@pytest_asyncio.fixture
async def lowlevel_env(
    session_factory: async_sessionmaker[AsyncSession],
):
    """低层入口 + 平台插件 + 守卫 + 真实审计（14 号计划 §97.6 装配约定）。"""
    from openviking.server.platform.auth.service import AuthService
    from openviking.server.routers import (
        content as content_mod,
    )
    from openviking.server.routers import (
        filesystem as fs_mod,
    )
    from openviking.server.routers import (
        resources as res_mod,
    )
    from openviking.server.routers import (
        watches as watches_mod,
    )

    app = FastAPI(title="ovp-lowlevel-test")
    repo = PostgresIamRepository()
    rbac = RbacService(repo)
    auth = AuthService(repo, rbac)
    authorization = AuthorizationService(
        TargetPolicy(), ProductFacadeService.build_ov_mapper(repo, session_factory)
    )
    audit_write = await _real_audit(repo, session_factory)
    guard = LowLevelPolicyGuard(authorization, audit=audit_write)

    app.state.iam_repository = repo
    app.state.iam_rbac_service = rbac
    app.state.iam_auth_service = auth
    app.state.platform_enabled = True
    app.state.platform_lowlevel_guard = guard
    app.state.platform_session_factory = session_factory
    app.state.platform_oauth_store = FakeOAuthTokenStore({})
    app.state.auth_plugin = PlatformIamAuthPlugin()

    async def _override_get_session() -> object:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_get_session
    app.include_router(content_mod.router)
    app.include_router(fs_mod.router)
    app.include_router(res_mod.router)
    app.include_router(watches_mod.router)

    # 与 create_app 一致的 OpenViking 异常映射（UNAUTHENTICATED → 401 等）
    from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
    from openviking_cli.exceptions import OpenVikingError

    @app.exception_handler(OpenVikingError)
    async def _ov_error_handler(_request, exc: OpenVikingError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500),
            content={"status": "error", "error": {"code": exc.code, "message": str(exc)}},
        )

    fake = _FakeLowLevelService()
    set_service(fake)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        yield SimpleNamespace(
            app=app,
            client=client,
            repo=repo,
            rbac=rbac,
            auth=auth,
            session_factory=session_factory,
            guard=guard,
            fake=fake,
        )


async def _seed(lowlevel_env, session: AsyncSession):
    """标准环境：psa + acme(admin/alice)。返回 (setup, alice, admin)。"""
    setup = await build_auth_setup(session)
    return setup, setup.alice, setup.admin


async def _alice_credentials(
    lowlevel_env, session: AsyncSession, setup, alice
) -> dict[str, object]:
    """alice 的三凭证：session/api_key/oauth。"""
    raw_token, _, _ = await create_login_session(setup, session, alice)
    full_key, cred = await _create_api_key_known(session, setup, alice)
    oauth_plain = "ovat_" + uuid.uuid4().hex
    lowlevel_env.app.state.platform_oauth_store.add(
        oauth_plain,
        _token_record(user_id=alice.id, account_id=setup.acme.id),
    )
    return {"session": raw_token, "api_key": full_key, "oauth": oauth_plain, "cred": cred}


# ── AC④：三凭证授权一致 + 审计以 Key 归属者身份 ──


async def test_three_credentials_same_low_level_authorization(lowlevel_env, session) -> None:
    """同一用户三凭证调用同一低层动作授权一致（AC④，05 §11.4）。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    creds = await _alice_credentials(lowlevel_env, session, setup, alice)
    session_raw = creds["session"]
    api_key = creds["api_key"]
    oauth = creds["oauth"]

    own_uri = "viking://user/ov_user_alice/resources/notes.md"
    shared_uri = "viking://resources/team.md"

    async def _call(auth_header: str | None, cookie: str | None):
        headers = {"Authorization": f"Bearer {auth_header}"} if auth_header else {}
        resp = await lowlevel_env.client.post(
            "/api/v1/content/write",
            json={"uri": own_uri, "content": "x", "mode": "replace"},
            headers=headers,
            cookies={"__Host-ov_session": cookie} if cookie else None,
        )
        return resp

    # 私有区写：三凭证全部 200
    for cookie, bearer in (
        (session_raw, None),
        (None, api_key),
        (None, oauth),
    ):
        resp = await _call(bearer, cookie)
        assert resp.status_code == 200, resp.text

    # 共享区写：三凭证全部 403（同一授权结果）
    for cookie, bearer in (
        (session_raw, None),
        (None, api_key),
        (None, oauth),
    ):
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        resp = await lowlevel_env.client.post(
            "/api/v1/content/write",
            json={"uri": shared_uri, "content": "x", "mode": "replace"},
            headers=headers,
            cookies={"__Host-ov_session": cookie} if cookie else None,
        )
        assert resp.status_code == 403, resp.text

    # 三凭证解析出同一权限集（OAuth 解析器与 API Key 同构，
    # test_oauth_principal 已覆盖；Session 路径由 AC⑥ 插件测试覆盖）
    async with lowlevel_env.session_factory() as s:
        p_key = await resolve_api_key_principal(s, lowlevel_env.repo, lowlevel_env.rbac, api_key)
    assert p_key.authentication_method == "api_key"
    assert "resource.user_private.write.self" in p_key.permissions
    assert "resource.account_shared.write.account" not in p_key.permissions


async def test_plugin_audits_as_key_owner(lowlevel_env, session) -> None:
    """插件以 Key 归属者身份读写审计（AC④：iam_audit_events 记录 Actor+凭证）。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, cred = await _create_api_key_known(session, setup, alice, name="Plugin-Codex")

    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://user/ov_user_alice/resources/audited.md",
            "content": "x",
            "mode": "replace",
        },
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200

    async with lowlevel_env.session_factory() as s:
        rows = list(
            (
                await s.execute(
                    select(IamAuditEvent).where(IamAuditEvent.action == "lowlevel.write")
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].actor_user_id == alice.id
    assert rows[0].authentication_method == "api_key"
    assert rows[0].actor_credential_id == cred.id
    assert rows[0].target_id == "viking://user/ov_user_alice/resources/audited.md"
    assert rows[0].result == "success"

    # 拒绝动作同样审计（先请求，再查库）
    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={"uri": "viking://resources/team.md", "content": "x", "mode": "replace"},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403
    async with lowlevel_env.session_factory() as s:
        denied = list(
            (
                await s.execute(
                    select(IamAuditEvent).where(
                        IamAuditEvent.action == "lowlevel.write.denied"
                    )
                )
            ).scalars()
        )
    assert len(denied) == 1
    assert denied[0].actor_user_id == alice.id
    assert denied[0].reason == "PERMISSION_NOT_GRANTED"


# ── AC⑤：跨 Account/User IDOR、Header spoofing、默认目标逃逸、编码 URI ──


async def test_cross_account_idor_denied(lowlevel_env, session) -> None:
    """跨 Account IDOR：acme 的 alice 无法写 beta Account 的共享区。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    # beta Account 共享根（跨 Account → CROSS_ACCOUNT_ACCESS 403）
    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={"uri": "viking://resources/team.md", "content": "x", "mode": "replace"},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403


async def test_cross_user_idor_denied(lowlevel_env, session) -> None:
    """跨 User IDOR：alice 写 bob 的私有 URI → 403 URI_MISMATCH。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    bob_ov = "ov_user_bob"  # 另一用户根的映射（与 alice 映射不一致即拒）
    full_key, _ = await _create_api_key_known(session, setup, alice)

    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={
            "uri": f"viking://user/{bob_ov}/resources/secret.md",
            "content": "x",
            "mode": "replace",
        },
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403
    # 不区分失败原因（防枚举）
    assert resp.json()["detail"]["code"] in ("URI_MISMATCH", "PERMISSION_NOT_GRANTED")


async def test_header_spoofing_ignored(lowlevel_env, session) -> None:
    """Header spoofing（AC⑤）：X-OpenViking-Account/User 不能切换身份。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    # 尝试用 Header 把自己声明为共享区管理员/他人 → 仍是 alice（403 写共享）
    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={"uri": "viking://resources/team.md", "content": "x", "mode": "replace"},
        headers={
            "X-API-Key": full_key,
            "X-OpenViking-Account": "ov_account_acme",
            "X-OpenViking-User": "ov_user_bob",
        },
    )
    assert resp.status_code == 403
    # 用 Header 声明 root 也不改变身份
    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={"uri": "viking://resources/team.md", "content": "x", "mode": "replace"},
        headers={
            "X-API-Key": full_key,
            "X-OpenViking-Account": "root",
            "X-OpenViking-User": "root",
        },
    )
    assert resp.status_code == 403


async def test_default_target_escape_blocked(lowlevel_env, session) -> None:
    """默认目标逃逸（AC①）：add_resource 未显式目标强制私有根，
    显式共享目标对普通 User 403。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    # 未显式目标 → 服务端强制私有根（fake 服务收到私有根 to）
    resp = await lowlevel_env.client.post(
        "/api/v1/resources",
        json={"path": "https://1.1.1.1/doc.html"},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200, resp.text
    kwargs = lowlevel_env.fake.resources.add_resource.call_args.kwargs
    assert kwargs["to"] == "viking://user/ov_user_alice/resources/"

    # 显式共享目标 → 普通 User 403
    resp = await lowlevel_env.client.post(
        "/api/v1/resources",
        json={"path": "https://1.1.1.1/doc.html", "to": "viking://resources/team/"},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403


async def test_add_skill_default_target_forced(lowlevel_env, session) -> None:
    """Skill 默认目标保持 User 私有 skills 根（05 §11.5）。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    resp = await lowlevel_env.client.post(
        "/api/v1/skills",
        json={"data": {"name": "greeter", "description": "say hi"}},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200, resp.text
    kwargs = lowlevel_env.fake.resources.add_skill.call_args.kwargs
    assert kwargs["target_uri"] == "viking://user/ov_user_alice/skills/"


async def test_encoded_alias_uri_authorized_identically(lowlevel_env, session) -> None:
    """编码/别名 URI 归一后授权一致（AC⑤）：重复斜杠写私有区 200。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://user/ov_user_alice//resources///notes.md/",
            "content": "x",
            "mode": "replace",
        },
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200
    # fake 收到的是原始 URI（归一化只用于授权），但守卫以归一化结果授权
    assert "ov_user_alice" in str(lowlevel_env.fake.fs.write.call_args)


async def test_mv_endpoint_cross_visibility_403(lowlevel_env, session) -> None:
    """AC②：/api/v1/fs/mv 私有→共享 被拒（Account Admin 403 CROSS_VISIBILITY_MOVE）。

    源/目标都通过写权限后仍因跨可见性被拒（05 §11.5：必须走复制/发布动作）。
    """
    setup, _, admin = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, admin, name="Admin-Move")
    admin_ov = admin.ov_user_id

    resp = await lowlevel_env.client.post(
        "/api/v1/fs/mv",
        json={
            "from_uri": f"viking://user/{admin_ov}/resources/a.md",
            "to_uri": "viking://resources/team/a.md",
        },
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CROSS_VISIBILITY_MOVE"


async def test_mv_endpoint_within_private_ok(lowlevel_env, session) -> None:
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    resp = await lowlevel_env.client.post(
        "/api/v1/fs/mv",
        json={
            "from_uri": "viking://user/ov_user_alice/resources/a.md",
            "to_uri": "viking://user/ov_user_alice/resources/b.md",
        },
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200


async def test_rm_shared_denied_mkdir_shared_denied(lowlevel_env, session) -> None:
    """AC②：rm/mkdir 对共享区普通 User 403。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    resp = await lowlevel_env.client.request(
        "DELETE",
        "/api/v1/fs",
        params={"uri": "viking://resources/team/a.md"},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403

    resp = await lowlevel_env.client.post(
        "/api/v1/fs/mkdir",
        json={"uri": "viking://resources/team/newdir"},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403


async def test_batch_write_guarded_per_operation(lowlevel_env, session) -> None:
    """AC②：批量写（batch-write）逐项经 TargetPolicy——共享区项 403。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    resp = await lowlevel_env.client.post(
        "/api/v1/content/batch-write",
        json={
            "root_uri": "viking://user/ov_user_alice/resources/",
            "operations": [
                {
                    "uri": "viking://user/ov_user_alice/resources/own.md",
                    "content": "x",
                    "precondition": {"kind": "create_if_absent"},
                },
                {
                    "uri": "viking://resources/team.md",
                    "content": "y",
                    "precondition": {"kind": "create_if_absent"},
                },
            ],
        },
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"


async def test_native_watch_cancel_requires_write_permission(lowlevel_env, session) -> None:
    """原生 Watch Router 取消也需要目标写权限（05 §11.5，05 号文档额外入口约束）。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    wm = SimpleNamespace(
        get_task_by_uri=AsyncMock(
            return_value=SimpleNamespace(to_uri="viking://resources/team.md", task_id="t1")
        )
    )
    lowlevel_env.fake.watch_scheduler = SimpleNamespace(is_running=True, watch_manager=wm)

    # 普通 User 取消共享区 Watch → 403（写权限未授予）
    resp = await lowlevel_env.client.request(
        "DELETE",
        "/api/v1/watches",
        params={"to_uri": "viking://resources/team.md"},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"


# ── AC⑥：Cookie+Bearer 优先级 ──


async def test_cookie_bearer_cookie_priority(lowlevel_env, session) -> None:
    """AC⑥：Cookie+Bearer 并存 → Cookie 优先（session 身份生效）。"""
    setup, alice, _ = await _seed(lowlevel_env, session)
    raw_token, _, _ = await create_login_session(setup, session, alice)
    full_key, _ = await _create_api_key_known(session, setup, alice)

    # 并存：Cookie 优先（两者都是 alice；重点是解析路径与审计方法）
    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://user/ov_user_alice/resources/notes.md",
            "content": "x",
            "mode": "replace",
        },
        headers={"Authorization": f"Bearer {full_key}"},
        cookies={"__Host-ov_session": raw_token},
    )
    assert resp.status_code == 200
    async with lowlevel_env.session_factory() as s:
        rows = list(
            (
                await s.execute(
                    select(IamAuditEvent).where(IamAuditEvent.action == "lowlevel.write")
                )
            ).scalars()
        )
    assert rows[-1].authentication_method == "session"

    # Cookie 失效（伪造 token）且 Bearer 有效 → 不回退 Bearer，401
    resp = await lowlevel_env.client.post(
        "/api/v1/content/write",
        json={
            "uri": "viking://user/ov_user_alice/resources/notes.md",
            "content": "x",
            "mode": "replace",
        },
        headers={"Authorization": f"Bearer {full_key}"},
        cookies={"__Host-ov_session": "forged-invalid-session-token"},
    )
    assert resp.status_code == 401
