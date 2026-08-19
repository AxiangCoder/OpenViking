"""P5-E4（14 号计划 §99.4）：06 §14.5 高风险操作确认弹窗 + 审计复验。

14.5 六类高风险操作 → 后端重校验 + 审计复验：
- 删除 Account（platform account.delete）；
- 删除用户全部数据（admin user.delete）；
- 重置其他用户密码 / 撤销其他用户 API Key（admin + platform）；
- 修改/导出/删除其他用户数据（成员数据只读 Subject 视图，无写路径）；
- 导出大量记忆或资源（成员数据/下载只读路径，写操作一律需 CSRF+权限）；
- 删除/批量覆盖 Account 共享 Resource/Skill（account resource/skill delete）。

断言语义（06 §14.5）：
1. 前端弹窗不是安全边界——提交不携带任何「确认」标记，后端独立重校验
   CSRF / Permission / Actor-Subject Scope / 目标状态；
2. 成功、失败与拒绝都写审计（result=success/failed/denied）；
3. 重复提交经幂等键或资源状态检查避免重复执行（删除返回同一 deletion job）。
4. deletion-preview 提供弹窗数据源：影响范围 / 可恢复 / 恢复截止。

本文件依赖 P5-E4 修复：高风险写拒绝审计（dependencies.py
require_high_risk_write，06 §14.5「拒绝都写入审计」的缺口修复）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamAuditEvent
from tests.platform.helpers import build_auth_setup, create_api_key, create_user
from tests.platform.test_admin_api import (
    BASE_ADMIN,
    BASE_PLATFORM,
    _login,
)
from tests.platform.test_resource_api import (
    BASE_ACCOUNT,
    _csrf,
    _import,
    _upload,
)

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


def _skill_zip_bytes() -> bytes:
    """最小合法 Skill ZIP（10 §61：SKILL.md + 允许工具，zip 安全校验放行）。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "SKILL.md",
            "---\nname: shared-skill\ndescription: 测试\ntags: []\n"
            "allowed-tools: read_file\n---\n\n# 使用说明\n",
        )
    return buf.getvalue()


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _plain_user(session: AsyncSession, platform_client, setup, email="eve@acme.com"):
    """额外普通 User（用于拒绝路径，避免目标被删/改密后无法登录）。"""
    from openviking.server.platform.iam.permissions import USER

    eve = await create_user(setup.repo, session, setup.acme, email=email, username=email.split("@")[0])
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=eve.id,
        role_code=USER,
    )
    await session.commit()
    return await _login(platform_client, email)


async def _audit(session: AsyncSession, action: str) -> list[IamAuditEvent]:
    return list(
        (
            await session.execute(
                select(IamAuditEvent).where(IamAuditEvent.action == action)
            )
        ).scalars()
    )


# ═══════════════════════════════════════════════════════════════════════
# ① 删除 Account（06 §14.5 第 1 项）
# ═══════════════════════════════════════════════════════════════════════


async def test_delete_account_audit_success_and_denial(
    session: AsyncSession, platform_client
) -> None:
    """PSA 删除 Account：成功 + 拒绝都写审计；Account Admin 被拒且审计。"""
    setup = await _seed(session)
    csrf = await _login(platform_client, "psa@platform.local")
    # 成功：进入 30 天回收期（幂等：重复删除同一 job）
    r = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}", headers=_csrf(csrf)
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["result"]["deletion_job_id"]
    r2 = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}", headers=_csrf(csrf)
    )
    assert r2.json()["result"]["deletion_job_id"] == job_id
    # 拒绝：Account Admin 删除 Account → 403 + 拒绝审计
    admin_csrf = await _login(platform_client, "admin@acme.com")
    r3 = await platform_client.delete(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}", headers=_csrf(admin_csrf)
    )
    assert r3.status_code == 403

    await session.commit()
    events = await _audit(session, "account.delete")
    assert any(e.result == "success" for e in events)
    assert any(e.result == "denied" and e.reason == "PERMISSION_NOT_GRANTED" for e in events)
    for e in events:
        assert e.authentication_method in ("session", "api_key")


# ═══════════════════════════════════════════════════════════════════════
# ② 删除用户全部数据（06 §14.5 第 2 项）
# ═══════════════════════════════════════════════════════════════════════


async def test_delete_user_audit_success_denied_and_preview(
    session: AsyncSession, platform_client
) -> None:
    """删除用户：preview 提供影响范围；成功/拒绝审计；幂等删除同一 job。"""
    setup = await _seed(session)
    csrf = await _login(platform_client, "admin@acme.com")
    user_id = str(setup.alice.id)

    # preview（弹窗数据源：影响数量/可恢复/恢复截止）
    r = await platform_client.get(f"{BASE_ADMIN}/users/{user_id}/deletion-preview")
    assert r.status_code == 200, r.text
    preview = r.json()["result"]
    assert preview["resource_type"] == "user"
    assert preview["recoverable"] is True
    assert preview["impacted"]["login_sessions"] >= 0
    assert preview["impacted"]["api_keys"] >= 0

    r = await platform_client.delete(f"{BASE_ADMIN}/users/{user_id}", headers=_csrf(csrf))
    assert r.status_code == 200, r.text
    job_id = r.json()["result"]["deletion_job_id"]
    assert r.json()["result"]["restore_until"] is not None

    # 幂等：重复提交返回同一 job（避免重复执行，06 §14.5）
    r2 = await platform_client.delete(f"{BASE_ADMIN}/users/{user_id}", headers=_csrf(csrf))
    assert r2.json()["result"]["deletion_job_id"] == job_id

    # 拒绝：普通 User 删除他人 → 403 + 拒绝审计
    eve_csrf = await _plain_user(session, platform_client, setup)
    r3 = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.admin.id}", headers=_csrf(eve_csrf)
    )
    assert r3.status_code == 403

    await session.commit()
    events = await _audit(session, "user.delete")
    assert any(e.result == "success" for e in events)
    assert any(e.result == "denied" for e in events)
    denied = next(e for e in events if e.result == "denied")
    assert denied.subject_user_id == setup.admin.id


# ═══════════════════════════════════════════════════════════════════════
# ③ 重置其他用户密码 / 撤销其他用户 API Key（06 §14.5 第 3 项）
# ═══════════════════════════════════════════════════════════════════════


async def test_reset_password_audit_success_and_denied(
    session: AsyncSession, platform_client
) -> None:
    """重置他人密码：成功 + 权限拒绝都写审计，拒绝含 Actor/Subject。"""
    setup = await _seed(session)
    csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/password/reset", headers=_csrf(csrf)
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["new_password"]

    # 拒绝：普通 User 重置他人 → 403 + 拒绝审计
    eve_csrf = await _plain_user(session, platform_client, setup)
    r2 = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.admin.id}/password/reset", headers=_csrf(eve_csrf)
    )
    assert r2.status_code == 403

    await session.commit()
    events = await _audit(session, "user.password.reset")
    assert any(e.result == "success" for e in events)
    assert any(e.result == "denied" for e in events)


async def test_revoke_other_user_key_audit_success_and_denied(
    session: AsyncSession, platform_client
) -> None:
    """撤销他人 API Key：成功 + 拒绝都写审计（14.5 第 3 项）。"""
    setup = await _seed(session)
    key = await create_api_key(setup.repo, session, setup.acme, setup.alice, name="victim-key")
    await session.commit()
    csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.alice.id}/api-keys/{key.id}", headers=_csrf(csrf)
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["revoked"] is True

    key2 = await create_api_key(setup.repo, session, setup.acme, setup.alice, name="victim-key-2")
    await session.commit()
    eve_csrf = await _plain_user(session, platform_client, setup)
    r2 = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.alice.id}/api-keys/{key2.id}", headers=_csrf(eve_csrf)
    )
    assert r2.status_code == 403

    await session.commit()
    events = await _audit(session, "credential.revoke")
    assert any(e.result == "success" for e in events)
    denied = [e for e in events if e.result == "denied"]
    assert denied
    assert denied[0].actor_user_id != denied[0].subject_user_id
    assert denied[0].subject_user_id == setup.alice.id


# ═══════════════════════════════════════════════════════════════════════
# ④ 修改/导出/删除其他用户数据（06 §14.5 第 4 项：成员数据只读 Subject 视图）
# ═══════════════════════════════════════════════════════════════════════


async def test_member_data_read_only_no_write_bypass(
    session: AsyncSession, platform_client
) -> None:
    """成员数据视图只读：GET 端点存在，任何写方法 → 405，无修改/导出/删除路径。"""
    setup = await _seed(session)
    await _login(platform_client, "admin@acme.com")
    path = f"/api/platform/v1/admin/users/{setup.alice.id}/sessions"
    r = await platform_client.get(path)
    assert r.status_code == 200, (path, r.status_code, r.text)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        r = await platform_client.request(method, path)
        assert r.status_code == 405, (method, r.status_code)


# ═══════════════════════════════════════════════════════════════════════
# ⑤ 删除/批量覆盖 Account 共享 Resource/Skill（06 §14.5 第 6 项）
# ═══════════════════════════════════════════════════════════════════════


async def _shared_resource(session: AsyncSession, platform_client) -> str:
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com")
    batch = await _import(
        platform_client,
        csrf,
        f"{BASE_ACCOUNT}/resources/imports",
        [{"source_url": "https://shared-hr.example.com/page"}],
    )
    return setup, csrf, batch["items"][0]["resource_id"]


async def test_shared_resource_delete_audit_and_permission(
    session: AsyncSession, platform_client
) -> None:
    """删除共享 Resource：Admin 成功 + 普通 User 拒绝都写审计。"""
    setup, csrf, res_id = await _shared_resource(session, platform_client)
    r = await platform_client.delete(
        f"{BASE_ACCOUNT}/resources/{res_id}", headers=_csrf(csrf)
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["deletion_job_id"]

    # 再建一个共享 Resource，普通 User 删除 → 403 + 拒绝审计
    batch2 = await _import(
        platform_client,
        csrf,
        f"{BASE_ACCOUNT}/resources/imports",
        [{"source_url": "https://shared-hr2.example.com/page"}],
    )
    res_id2 = batch2["items"][0]["resource_id"]
    eve_csrf = await _plain_user(session, platform_client, setup)
    r2 = await platform_client.delete(
        f"{BASE_ACCOUNT}/resources/{res_id2}", headers=_csrf(eve_csrf)
    )
    assert r2.status_code == 403

    await session.commit()
    events = await _audit(session, "resource.delete")
    assert any(e.result == "success" for e in events)
    assert any(e.result == "denied" for e in events)


async def test_shared_skill_delete_audit_and_permission(
    session: AsyncSession, platform_client
) -> None:
    """删除共享 Skill：Admin 成功 + 普通 User 拒绝都写审计。"""
    setup = await build_auth_setup(session)
    csrf = await _login(platform_client, "admin@acme.com")
    # 建共享 Skill（上传走 account/resource-uploads，无独立 skill-uploads 端点）
    upload_id = await _upload(
        platform_client,
        csrf,
        f"{BASE_ACCOUNT}/resource-uploads",
        filename="shared-skill.zip",
        content=_skill_zip_bytes(),
        mime="application/zip",
    )
    r = await platform_client.post(
        "/api/platform/v1/account/skills",
        json={"name": "shared-skill", "upload_id": str(uuid.UUID(upload_id))},
        headers=_csrf(csrf),
    )
    assert r.status_code == 200, r.text
    skill_id = r.json()["result"]["id"]

    r = await platform_client.delete(
        f"/api/platform/v1/account/skills/{skill_id}", headers=_csrf(csrf)
    )
    assert r.status_code == 200, r.text

    # 普通 User 删除共享 Skill → 403 + 拒绝审计
    eve_csrf = await _plain_user(session, platform_client, setup)
    r2 = await platform_client.delete(
        f"/api/platform/v1/account/skills/{skill_id}", headers=_csrf(eve_csrf)
    )
    assert r2.status_code == 403

    await session.commit()
    events = await _audit(session, "skill.delete")
    assert any(e.result == "success" for e in events)
    assert any(e.result == "denied" for e in events)


# ═══════════════════════════════════════════════════════════════════════
# ⑥ CSRF 与重复提交防护（06 §14.5：后端独立重校验）
# ═══════════════════════════════════════════════════════════════════════


async def test_high_risk_write_requires_csrf_even_with_session(
    session: AsyncSession, platform_client
) -> None:
    """无 CSRF Token 的高风险写被拒并审计（弹窗不能代替 CSRF）。"""
    setup = await _seed(session)
    await _login(platform_client, "admin@acme.com")
    r = await platform_client.delete(f"{BASE_ADMIN}/users/{setup.alice.id}")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_INVALID"
    await session.commit()
    events = await _audit(session, "user.delete")
    assert any(e.result == "denied" and e.reason == "CSRF_INVALID" for e in events)


async def test_reset_rank_and_cross_account_denied_audited(
    session: AsyncSession, platform_client
) -> None:
    """同级重置拒绝（rank 校验）与跨 Account 404 都留下审计。"""
    setup = await _seed(session)
    # 同级：第二个 account_admin
    bob = await create_user(setup.repo, session, setup.acme, email="bob2@acme.com", username="bob2")
    await session.commit()
    await setup.rbac.assign_role(
        session,
        actor_user_id=setup.psa.id,
        actor_account_id=None,
        target_user_id=bob.id,
        role_code="account_admin",
    )
    await session.commit()
    csrf = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{bob.id}/password/reset", headers=_csrf(csrf)
    )
    assert r.status_code == 403
    # 跨 Account（random id）→ 404（防枚举，05 §12.6）
    r2 = await platform_client.post(
        f"{BASE_ADMIN}/users/{uuid.uuid4()}/password/reset", headers=_csrf(csrf)
    )
    assert r2.status_code == 404

    await session.commit()
    events = await _audit(session, "user.password.reset")
    assert any(e.result == "denied" for e in events)
