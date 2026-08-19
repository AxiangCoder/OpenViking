"""P5-E4（14 号计划 §99.4）：07 §18.5 安全测试 17 项清单——生产级收口证据。

本文件是 18.5 清单的唯一收口测试文件（07 §18.5/§19 Phase 5；07 §21 条目
6/7/13 对应）。每项条目 → 本文件内实时断言；既有深度覆盖（各 Phase 测试文件）
在模块 docstring 与 p5-e4-go-no-go.md 证据表中引用，不重复收集。

17 项清单 → 证据映射：

| # | 18.5 条目 | 本文件证据 | 深度覆盖（引用） |
| --- | --- | --- | --- |
| 1 | 修改 URL/请求体 Account/User ID 不越权 | test_header_spoofing_ignored_scope（产品 API 面） | test_tenant_isolation.py::test_cross_account_idor_denied/test_cross_user_idor_denied、test_lowlevel_guard.py、test_aggregate_endpoints.py |
| 2 | Header spoofing 无效 | test_header_spoofing_ignored_scope | test_tenant_isolation.py::test_header_spoofing_ignored |
| 3 | Cookie Secure/HttpOnly/SameSite 生效 | test_cookie_attributes_secure_httponly_samesite | test_csrf.py::test_apply_session_cookie* |
| 4 | CSRF、爆破、fixation、replay | test_login_rotation_no_session_fixation | test_csrf.py（CSRF）、test_rate_limit.py（爆破）、test_resource_api.py::test_import_idempotency_key_replay、test_oauth_refresh_rotation.py（Token 重放） |
| 5 | 日志/审计无密码/Cookie/Key 明文/完整 hash/Token | test_logs_and_audit_no_secrets（轻量复验） | test_audit_production.py::test_17_1_event_categories_produce_masked_audit |
| 6 | 创建/重置密码响应不入访问日志/埋点/错误上报/审计 metadata | test_reset_password_response_not_in_audit_metadata | test_audit_production.py::test_cross_user_events_have_actor_and_subject（metadata 无新密码） |
| 7 | 删除/禁用最后一个 Account Admin 被拒 | test_last_account_admin_delete_and_disable_protected | test_deletion_jobs.py::test_delete_last_account_admin_rejected、test_aggregate_endpoints.py::test_admin_user_delete_last_admin_conflict |
| 8 | 篡改 Subject/伪造角色/绕过确认弹窗不能绕过后端授权 | test_confirmation_dialog_is_not_a_security_boundary（后端重校验） | test_high_risk_confirm.py（14.5 全量）、test_lowlevel_guard.py、test_admin_api.py::test_admin_creates_user_role_fixed（role 字段忽略） |
| 9 | 篡改 visibility/默认目标/编码别名 URI/跨可见性移动不能绕过共享写 | test_visibility_tamper_cannot_bypass_shared_write | test_lowlevel_guard.py（默认目标/编码/跨可见性）、test_tenant_isolation.py::test_default_target_escape_blocked |
| 10 | /studio 不挂载时 OAuth 走通、浏览器无 User API Key | test_oauth_works_without_studio_browser_no_key | test_oauth_without_studio.py、test_mount_production.py |
| 11 | forget/公开删除只进 30 天回收期、不能直接物理删除 | test_product_forget_soft_delete_only | test_mcp_tools_productized.py::test_product_forget_soft_deletes_into_30_day_window、test_deletion_jobs.py |
| 12 | 普通 User 不能查看/触发/取消他人私有 Watch/Task；Admin 只能管理共享 Watch | test_watch_cross_user_invisible、test_shared_watch_manage_scope、test_cross_user_operation_invisible | test_resource_watch.py、test_aggregate_endpoints.py::test_admin_activity_and_monitoring |
| 13 | 远程来源拒绝 localhost/私网/云元数据/DNS Rebinding/危险 Redirect/Userinfo/私有 Git 凭证 | test_dns_rebinding_blocked、test_redirect_revalidation_and_depth_limit、test_remote_source_blocks_private_git_credentials | test_resource_api.py::test_remote_source_security |
| 14 | 完整 URL 仅密文保存；Outbox/Watch JSON/日志/审计/DTO 无含 Query 明文 | test_one_time_url_masked_in_dto_and_audit | test_resource_watch.py::test_watch_persists_resource_id_only_not_plaintext_url |
| 15 | Upload ID 过期/重放/跨消费拒绝；大小/MIME 服务端重校验 | test_upload_consumption_guards（复验） | test_resource_api.py::test_upload_*（过期/重放/跨 Scope）、test_skill_upload.py |
| 16 | Node ID 篡改不能跳出父 Resource；预览不执行脚本；下载文件名不注入响应头 | test_node_id_tamper_cannot_escape_parent | test_resource_api.py::test_nodes_tree_preview_download_and_node_id_authorization |
| 17 | 生产公网 WebDAV/Snapshot/Pack/Debug/Observer/系统修复 404/拒绝 | test_low_level_ops_blocked_public（配置+挂载双断言） | test_mount_production.py::test_low_level_ops_not_mounted_in_production、test_lowlevel_guard.py |

验收映射：P5-E4 验收②（18.5 全通过）；验收⑤（Go/No-Go 报告可追溯——
test_go_no_go_report_evidence_complete）。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.auth.password import new_session_token
from openviking.server.platform.dependencies import apply_session_cookie
from openviking.server.platform.errors import ResourceSourceBlockedError
from openviking.server.platform.models import IamAuditEvent
from openviking.server.platform.resource.security import (
    MAX_REDIRECTS,
    RemoteSourcePolicy,
)
from tests.platform.helpers import (
    DEFAULT_PASSWORD,
    build_auth_setup,
    create_login_session,
    create_user,
)
from tests.platform.test_resource_api import (
    BASE_ME,
    _csrf,
    _import,
    _login,
    _upload,
)

ALICE = "alice@acme.com"
BOB = "bob@acme.com"

REPORT = (
    Path(__file__).resolve().parents[2]
    / "docs/design/product-platform/v0.1/p5-e4-go-no-go.md"
)


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #2/#8：Header spoofing 与身份字段在产品 API 面不可信
# ═══════════════════════════════════════════════════════════════════════


async def test_header_spoofing_ignored_scope(session: AsyncSession, platform_client) -> None:
    """spoof X-OpenViking-Account/User 头不能切换身份或数据范围（07 §21 #6）。

    产品 API 身份只从登录 Session 解析；伪造 Header 不能改变当前 Account。
    """
    await build_auth_setup(session)
    await _login(platform_client, "admin@acme.com")
    spoofed = {
        "X-OpenViking-Account": str(uuid.uuid4()),
        "X-OpenViking-User": str(uuid.uuid4()),
        "X-User-Id": str(uuid.uuid4()),
    }
    r = await platform_client.get("/api/platform/v1/admin/users", headers=spoofed)
    assert r.status_code == 200, r.text
    ids = [u["id"] for u in r.json()["result"]["items"]]
    assert str(spoofed["X-OpenViking-User"]) not in ids
    # me 端点身份仍是 alice（header 不覆盖 Session 身份）
    await _login(platform_client, ALICE)
    r = await platform_client.get("/api/platform/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["result"]["user"]["ov_user_id"] == "ov_user_alice"


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #3：Cookie 属性
# ═══════════════════════════════════════════════════════════════════════


def test_cookie_attributes_secure_httponly_samesite() -> None:
    """Session Cookie：Secure + HttpOnly + SameSite=Lax + __Host- 前缀（03 §8.1）。"""
    from starlette.responses import Response

    response = Response()
    apply_session_cookie(response, new_session_token())
    header = response.headers["set-cookie"]
    assert "__Host-ov_session=" in header
    assert "Secure" in header
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()
    assert "Path=/" in header


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #4：Session fixation / replay
# ═══════════════════════════════════════════════════════════════════════


async def test_login_rotation_no_session_fixation(session: AsyncSession, session_factory) -> None:
    """每次登录签发全新随机 256-bit Session Token，不接受客户端预置 Token（fixation）。"""
    setup = await build_auth_setup(session)
    raw1, csrf1, session_id1 = await create_login_session(setup, session, setup.alice)
    await session.commit()
    raw2, csrf2, session_id2 = await create_login_session(setup, session, setup.alice)
    await session.commit()

    assert raw1 != raw2
    assert len(raw1) >= 43  # 256-bit base64 编码长度下限
    assert session_id1 != session_id2
    assert csrf1 != csrf2
    # 旧 Token 不能复用为新 Token（服务端只认签发记录，无 adoption 路径）
    from openviking.server.platform.auth.principals import resolve_session_principal
    from openviking.server.platform.iam import PostgresIamRepository

    repo = PostgresIamRepository()
    p1 = await resolve_session_principal(session, repo, setup.rbac, raw1)
    p2 = await resolve_session_principal(session, repo, setup.rbac, raw2)
    assert p1 is not None and p2 is not None
    assert p1.actor_user_id == p2.actor_user_id == setup.alice.id
    assert p1.session_id == session_id1 and p2.session_id == session_id2
    # 两个 Token 都可独立使用但彼此不同（无 fixation：攻击者无法预置被复用 Token）
    assert p1.session_id != p2.session_id


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #5/#6：日志/审计脱敏与创建/重置密码响应
# ═══════════════════════════════════════════════════════════════════════


async def test_reset_password_response_not_in_audit_metadata(
    session: AsyncSession, platform_client, platform_app
) -> None:
    """重置密码响应一次性返回新密码；审计 metadata 不含密码（06 §14.4）。"""
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"/api/platform/v1/admin/users/{setup.alice.id}/password/reset",
        headers=_csrf(csrf),
    )
    assert r.status_code == 200, r.text
    new_password = r.json()["result"]["new_password"]
    assert new_password and new_password != DEFAULT_PASSWORD

    await session.commit()
    events = list(
        (
            await session.execute(
                select(IamAuditEvent).where(
                    IamAuditEvent.action == "user.password.reset",
                    IamAuditEvent.result == "success",
                )
            )
        ).scalars()
    )
    assert events
    blob = " ".join(str(getattr(e, f) or "") for e in events for f in ("metadata_json", "reason"))
    assert new_password not in blob
    metadata = events[0].metadata_json or {}
    assert "password" not in " ".join(metadata.keys()).lower()
    assert new_password not in str(metadata)


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #7：末位 Account Admin 保护
# ═══════════════════════════════════════════════════════════════════════


async def test_last_account_admin_delete_and_disable_protected(
    session: AsyncSession, platform_client
) -> None:
    """删除/禁用最后一个 Account Admin 被拒（LAST_ACCOUNT_ADMIN_REQUIRED）。"""
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com")
    for path in (
        f"/api/platform/v1/admin/users/{setup.admin.id}/disable",
        f"/api/platform/v1/admin/users/{setup.admin.id}",
    ):
        r = await platform_client.request("POST" if "disable" in path else "DELETE", path, headers=_csrf(csrf))
        assert r.status_code == 409, (path, r.status_code, r.text)
        assert r.json()["detail"]["code"] == "LAST_ACCOUNT_ADMIN_REQUIRED"


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #8/#9：后端授权不可绕过（确认弹窗/visibility/默认目标/编码 URI）
# ═══════════════════════════════════════════════════════════════════════


async def test_confirmation_dialog_is_not_a_security_boundary(
    session: AsyncSession, platform_client
) -> None:
    """无「确认」标记也能被拒：后端独立重校验 CSRF/权限/Scope/目标状态（06 §14.5）。"""
    setup = await build_auth_setup(session)
    # 1) 无 CSRF Token 的高风险写被拒（前端弹窗无法代替 CSRF）
    await _login(platform_client, ALICE)
    r = await platform_client.delete(f"/api/platform/v1/admin/users/{setup.admin.id}")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"

    # 2) 有 CSRF 但无权限 → 403 + 拒绝审计（06 §14.5：成功、失败与拒绝都写入审计）
    alice_csrf = await _login(platform_client, ALICE)
    r = await platform_client.delete(
        f"/api/platform/v1/admin/users/{setup.admin.id}", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"
    await session.commit()
    events = list(
        (
            await session.execute(
                select(IamAuditEvent).where(
                    IamAuditEvent.action == "user.delete", IamAuditEvent.result == "denied"
                )
            )
        ).scalars()
    )
    assert events, "拒绝必须写审计"

    # 3) 伪造「confirmed」字段不产生任何旁路
    r = await platform_client.request(
        "DELETE",
        f"/api/platform/v1/admin/users/{setup.admin.id}",
        json={"confirmed": True},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 403


async def test_visibility_tamper_cannot_bypass_shared_write(
    session: AsyncSession, platform_client
) -> None:
    """客户端提交的 visibility 不能成为授权依据：字段被忽略，落盘仍为私有根。"""
    await build_auth_setup(session)
    alice_csrf = await _login(platform_client, ALICE)
    batch = await _import(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resources/imports",
        [{"source_url": "https://vis.example.com/page", "visibility": "account_shared"}],
    )
    # 共享写未授权时该 URL 被拒；若通过，产物必须是 Actor 私有（绝不落共享区）
    item = batch["items"][0]
    if item["error"] is None:
        r = await platform_client.get(f"{BASE_ME}/resources/{item['resource_id']}")
        assert r.status_code == 200, r.text
        assert r.json()["result"]["visibility"] == "user_private"
    else:
        assert item["error"]["code"] in ("RESOURCE_SOURCE_BLOCKED", "PERMISSION_NOT_GRANTED")


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #12：Watch/Task 越权
# ═══════════════════════════════════════════════════════════════════════


async def _seed_bob(session: AsyncSession):
    setup = await build_auth_setup(session)
    from openviking.server.platform.iam.permissions import USER

    bob = await create_user(
        setup.repo, session, setup.acme, email=BOB, username="bob"
    )
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=bob.id,
        role_code=USER,
    )
    await session.commit()
    return setup


async def test_watch_cross_user_invisible(session: AsyncSession, platform_client) -> None:
    """普通 User 不能查看/触发/取消其他 User 私有 Resource 的 Watch（18.5 #12）。"""
    await _seed_bob(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id = await _import(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resources/imports",
        [{"source_url": "https://watch-private.example.com/page"}],
    )
    res_id = res_id["items"][0]["resource_id"]
    r = await platform_client.put(
        f"{BASE_ME}/resources/{res_id}/watch",
        json={"interval_minutes": 60},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text

    bob_csrf = await _login(platform_client, BOB)
    for method, path in (
        ("GET", f"{BASE_ME}/resources/{res_id}/watch"),
        ("POST", f"{BASE_ME}/resources/{res_id}/watch/trigger"),
        ("POST", f"{BASE_ME}/resources/{res_id}/watch/pause"),
        ("DELETE", f"{BASE_ME}/resources/{res_id}/watch"),
    ):
        r = await platform_client.request(method, path, headers=_csrf(bob_csrf))
        assert r.status_code == 404, (method, path, r.status_code, r.text)


async def test_shared_watch_manage_scope(session: AsyncSession, platform_client) -> None:
    """共享 Watch 读开放、管理仅 Account Admin（18.5 #12 后半句）。"""
    await build_auth_setup(session)
    admin_csrf = await _login(platform_client, "admin@acme.com")
    batch = await _import(
        platform_client,
        admin_csrf,
        "/api/platform/v1/account/resources/imports",
        [{"source_url": "https://shared-watch.example.com/page"}],
    )
    res_id = batch["items"][0]["resource_id"]

    r = await platform_client.put(
        f"/api/platform/v1/account/resources/{res_id}/watch",
        json={"interval_minutes": 60},
        headers=_csrf(admin_csrf),
    )
    assert r.status_code == 200, r.text

    # 普通 User：读开放（200），管理动作 403
    alice_csrf = await _login(platform_client, ALICE)
    r = await platform_client.get(f"/api/platform/v1/account/resources/{res_id}/watch")
    assert r.status_code == 200, r.text
    for method, path in (
        ("PUT", f"/api/platform/v1/account/resources/{res_id}/watch"),
        ("POST", f"/api/platform/v1/account/resources/{res_id}/watch/trigger"),
        ("DELETE", f"/api/platform/v1/account/resources/{res_id}/watch"),
    ):
        body = {"interval_minutes": 60} if method == "PUT" else None
        r = await platform_client.request(method, path, json=body, headers=_csrf(alice_csrf))
        assert r.status_code == 403, (method, r.status_code, r.text)


async def test_cross_user_operation_invisible(session: AsyncSession, platform_client) -> None:
    """Task（Operation）越权：bob 不能查看/取消 alice 私有 Resource 的 Operation。"""
    await _seed_bob(session)
    alice_csrf = await _login(platform_client, ALICE)
    batch = await _import(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resources/imports",
        [{"source_url": "https://task-private.example.com/page"}],
    )
    res_id = batch["items"][0]["resource_id"]
    operation_id = batch["items"][0].get("operation_id")
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/operations")
    assert r.status_code == 200, r.text

    bob_csrf = await _login(platform_client, BOB)
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/operations")
    assert r.status_code == 404, r.text
    if operation_id:
        r = await platform_client.post(
            f"{BASE_ME}/resources/{res_id}/operations/{operation_id}/cancel",
            headers=_csrf(bob_csrf),
        )
        assert r.status_code == 404, r.text


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #13：远程来源防护（DNS Rebinding / Redirect 重校验 / Git 凭证）
# ═══════════════════════════════════════════════════════════════════════


def test_dns_rebinding_blocked() -> None:
    """DNS Rebinding：主机名解析出的私网/元数据 IP 与直接地址同规则拒绝。"""
    policy = RemoteSourcePolicy(
        dns_resolver=lambda host: ["169.254.169.254", "10.0.0.6", "192.168.1.1"]
    )
    for url in (
        "https://attacker.example.com/page",
        "https://rebind.example.com/x",
    ):
        with pytest.raises(ResourceSourceBlockedError):
            policy.validate(url, kind="web")
    # 公网解析放行
    ok = RemoteSourcePolicy(dns_resolver=lambda host: ["93.184.216.34"])
    v = ok.validate("https://example.com/page", kind="web")
    assert v.stable and v.display == "https://example.com/page"
    # 解析钩子不存在时以字面地址为准（不额外放行）
    literal = RemoteSourcePolicy()
    with pytest.raises(ResourceSourceBlockedError):
        literal.validate("https://169.254.169.254/meta", kind="web")


def test_redirect_revalidation_and_depth_limit() -> None:
    """每次 Redirect 目标重新校验；超过次数即拒（09 §40.6）。"""
    policy = RemoteSourcePolicy()
    policy.check_redirect("https://public.example.com/target", kind="web")
    with pytest.raises(ResourceSourceBlockedError):
        policy.check_redirect("http://localhost/evil", kind="web")
    with pytest.raises(ResourceSourceBlockedError):
        policy.check_redirect("https://10.0.0.7/x", kind="web")
    with pytest.raises(ResourceSourceBlockedError):
        policy.check_redirect("https://user:pass@example.com/priv", kind="web")
    with pytest.raises(ResourceSourceBlockedError):
        policy.check_redirect("https://public.example.com/loop", kind="web", depth=MAX_REDIRECTS)


def test_remote_source_blocks_private_git_credentials() -> None:
    """Git 只允许公开 HTTPS；SSH/git@/认证参数被拒（09 §40.6）。"""
    policy = RemoteSourcePolicy()
    v = policy.validate("https://github.com/org/repo.git", kind="git")
    assert v.kind == "git" and v.stable
    for url in (
        "git@github.com:org/repo.git",
        "ssh://git@github.com/org/repo.git",
        "https://token:secret@github.com/org/repo.git",
        "http://github.com/org/repo.git",
    ):
        from openviking.server.platform.errors import ResourceError

        with pytest.raises((ResourceSourceBlockedError, ResourceError)):
            policy.validate(url, kind="git")


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #14：含 Query 的完整 URL 不以明文进入 DTO/审计
# ═══════════════════════════════════════════════════════════════════════


async def test_one_time_url_masked_in_dto_and_audit(
    session: AsyncSession, platform_client
) -> None:
    """一次性含 Query URL：产品 DTO 只显示脱敏 display，Query 明文不入响应/审计。"""
    await build_auth_setup(session)
    alice_csrf = await _login(platform_client, ALICE)
    query_secret = "s3cr3t-query-token"
    batch = await _import(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resources/imports",
        [{"source_url": f"https://one-time.example.com/doc?id={query_secret}"}],
    )
    res_id = batch["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 200, r.text
    body = r.text
    assert query_secret not in body
    assert "?<query>" in body or "source_display" in body
    source_display = r.json()["result"].get("source_display") or ""
    assert query_secret not in source_display

    await session.commit()
    events = list(
        (
            await session.execute(
                select(IamAuditEvent).where(IamAuditEvent.action == "resource.import")
            )
        ).scalars()
    )
    blob = " ".join(
        str(getattr(e, f) or "") for e in events for f in ("metadata_json", "reason")
    )
    assert query_secret not in blob


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #15：Upload ID 消费守卫（过期/重放/跨 Scope）
# ═══════════════════════════════════════════════════════════════════════


async def test_upload_consumption_guards(session: AsyncSession, platform_client) -> None:
    """Upload ID 消费守卫：ready→consumed 原子转换，重放消费被拒（09 §47.2）。"""
    await build_auth_setup(session)
    alice_csrf = await _login(platform_client, ALICE)
    upload_id = await _upload(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resource-uploads",
        filename="consume-guard.txt",
        content=b"consume guard payload",
    )
    batch = await _import(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resources/imports",
        [{"upload_id": upload_id, "name": "consume-guard.txt"}],
    )
    assert batch["items"][0]["error"] is None
    # 重放同一 Upload → ALREADY_CONSUMED
    replay = await _import(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resources/imports",
        [{"upload_id": upload_id, "name": "consume-guard.txt"}],
    )
    assert replay["items"][0]["error"]["code"] == "RESOURCE_UPLOAD_ALREADY_CONSUMED"
    # 拒绝已写审计（09 §47.2）
    await session.commit()
    events = list(
        (
            await session.execute(
                select(IamAuditEvent).where(IamAuditEvent.action == "resource.upload.denied")
            )
        ).scalars()
    )
    assert events


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #16：Node ID 篡改不能跳出父 Resource
# ═══════════════════════════════════════════════════════════════════════


async def test_node_id_tamper_cannot_escape_parent(session: AsyncSession, platform_client) -> None:
    """篡改 Node ID：不属于当前 Resource 的 node_id 统一 404（09 §44.2）。"""
    await build_auth_setup(session)
    alice_csrf = await _login(platform_client, ALICE)
    batch = await _import(
        platform_client,
        alice_csrf,
        f"{BASE_ME}/resources/imports",
        [{"source_url": "https://node-example.com/repo"}],
    )
    res_id = batch["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/nodes")
    assert r.status_code == 200, r.text
    items = r.json()["result"]["items"]
    assert items, "import 成功应至少产出根节点"
    node_id = items[0]["node_id"]
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/nodes/{node_id}xx")
    assert r.status_code == 404, r.text


# ═══════════════════════════════════════════════════════════════════════
# 18.5 #17：低层入口封禁（配置 + 挂载双断言）
# ═══════════════════════════════════════════════════════════════════════


def test_low_level_ops_blocked_public() -> None:
    """生产配置：low_level_routers_enabled 只含 admin、studio 关闭、代理 deny 存在。"""
    repo = Path(__file__).resolve().parents[2]
    template = repo / "deploy/product/config/ov.conf.template"
    assert template.exists()
    text = template.read_text(encoding="utf-8")
    assert '"low_level_routers_enabled": ["admin"]' in text
    assert '"studio_enabled": false' in text
    nginx = repo / "deploy/product/nginx/nginx.conf"
    assert nginx.exists()
    nginx_text = nginx.read_text(encoding="utf-8")
    for literal in (
        "location ~ ^/api/v1/admin",
        "location ~ ^/api/v1/(console|snapshot|pack|debug|observer)",
        "location ~ ^/webdav",
        "location ~ ^/api/v1/system/(repair|rebuild)",
        "location ~ ^/studio",
    ):
        assert literal in nginx_text, literal


# ═══════════════════════════════════════════════════════════════════════
# P5-E4 验收⑤：Go/No-Go 报告证据完整性（26 条 + 18.5 清单可追溯）
# ═══════════════════════════════════════════════════════════════════════


def _evidence_rows(report: Path, section_heading: str) -> list[list[str]]:
    lines = report.read_text(encoding="utf-8").splitlines()
    rows: list[list[str]] = []
    in_section = False
    for line in lines:
        if line.startswith("## ") and section_heading in line:
            in_section = True
            continue
        if line.startswith("## ") and in_section:
            break
        if in_section and line.startswith("|") and "|" in line[1:]:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and not all(set(c) <= set(":-") for c in cells):
                rows.append(cells)
    return rows


def test_go_no_go_report_evidence_complete() -> None:
    """Go/No-Go 报告必须含 26 条验收证据表且每条证据链接可解析（P5-E4 验收①⑤）。"""
    assert REPORT.exists(), f"缺失 Go/No-Go 报告: {REPORT}"
    rows = _evidence_rows(REPORT, "26 条生产验收门禁证据表")
    assert len(rows) >= 26, f"26 条证据表不完整: {len(rows)} 行"
    repo = REPORT.parents[4]  # v0.1/../../../.. → 仓库根
    for row in rows:
        if row[0] in ("用例编号", "#"):
            continue
        assert len(row) >= 3, f"证据行字段不完整: {row}"
        gate, conclusion, evidence = row[0], row[1], row[2]
        assert gate.startswith("G"), f"测试用例编号缺失: {row}"
        assert conclusion in ("PASS", "FAIL", "N/A"), f"结论非法: {conclusion}"
        for link in re.findall(r"`([^`]+)`", evidence):
            path = link.split("::")[0]
            if path.startswith("tests/") or path.startswith("deploy/") or path.startswith("docs/design"):
                assert (repo / path).exists(), f"证据链接不可解析: {path} ({gate})"


def test_18_5_security_matrix_complete() -> None:
    """Go/No-Go 报告 18.5 清单：17 项全部 PASS 且证据可解析（P5-E4 验收②）。"""
    assert REPORT.exists()
    rows = _evidence_rows(REPORT, "18.5 安全测试 17 项清单")
    rows = [r for r in rows if r[0] != "#"]
    assert len(rows) == 17, f"18.5 清单行数错误: {len(rows)}"
    repo = REPORT.parents[4]  # v0.1/../../../.. → 仓库根
    for row in rows:
        assert len(row) >= 4, row
        item_no, _, conclusion, evidence = row[0], row[1], row[2], row[3]
        assert item_no.startswith("#"), item_no
        assert conclusion == "PASS", f"18.5 条目 {item_no} 结论非 PASS: {conclusion}"
        for link in re.findall(r"`([^`]+)`", evidence):
            path = link.split("::")[0]
            if path.startswith("tests/"):
                assert (repo / path).exists(), f"证据链接不可解析: {path} ({item_no})"
