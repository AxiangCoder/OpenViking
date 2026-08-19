"""P2-E4 Skill 产品 API 集成测试（10 §61.1–§61.4，14 号计划 §97.4）。

验收映射（§97.4 验收标准）：
- ① Account 内任意两个未删除 Skill 不得同名（跨 User/可见性）；
- ② name 不可修改（JSON 与 ZIP 均拒，`SKILL_NAME_IMMUTABLE`）；
- ③ 冲突响应只说明「该名称在当前 Account 不可用」，不泄露占用者；
- ④ 软删立即释放名称、恢复同名冲突保持删除状态不改名不覆盖；
- ⑦ 普通 User 不能发布、Account Admin 可发布任意成员私有 Skill 但不能
  编辑/删除/恢复他人私有 Skill、PSA 全 Skill 只读（含平台只读路由）；
- 上传消费 upload_id（10 §61.5，P2-E3 `me/resource-uploads` 契约；
  端到端 HTTP 链路见 test_skill_upload.py）；ZIP 安全校验（10 §54.2）。
"""

from __future__ import annotations

import io
import uuid
import zipfile

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.registry.service import ContentRegistryService
from openviking.server.platform.skills.service import EVENT_SKILL_PUBLISH
from tests.platform.helpers import build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE_ME = "/api/platform/v1/me"
BASE_ACCOUNT = "/api/platform/v1/account"
BASE_ADMIN = "/api/platform/v1/admin"
BASE_PLATFORM = "/api/platform/v1/platform"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _create_skill(
    client: httpx.AsyncClient,
    csrf: str,
    *,
    path: str,
    name: str,
    description: str = "测试 Skill",
    tags: list | None = None,
    content: str = "使用说明正文",
    idempotency_key: str | None = None,
) -> httpx.Response:
    return await client.post(
        path,
        json={
            "name": name,
            "description": description,
            "tags": tags or ["type=helper"],
            "content": content,
            "idempotency_key": idempotency_key,
        },
        headers={"X-CSRF-Token": csrf},
    )


def _make_skill_md(name: str, description: str = "测试 Skill", tags: list | None = None) -> str:
    tag_line = "\n".join(f"- {t}" for t in (tags or []))
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"allowed-tools: read_file, search\n"
        f"tags:\n{tag_line}\n"
        "---\n\n"
        "# 使用说明\n\n执行步骤...\n"
    )


def _make_zip(name: str, *, with_aux: bool = True, bad_member: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", _make_skill_md(name))
        if with_aux:
            zf.writestr("scripts/run.py", "print('hello')\n")
            zf.writestr("references/guide.md", "# guide\n")
        if bad_member is not None:
            zf.writestr(bad_member, "evil")
    return buf.getvalue()


async def _register_upload(
    platform_app, session: AsyncSession, *, account_id, actor_user_id, visibility, data: bytes, kind: str
) -> uuid.UUID:
    """按 P2-E3 `me/resource-uploads` 语义注册 upload 记录（04 §10.14）：

    `build_blob` 服务端检测（大小/Magic/MIME/扩展名）→ 受控临时上传存储 →
    registry.create_upload（object_type=resource，与 E3 端点一致）。
    """
    from openviking.server.platform.resource.storage import build_blob

    filename = "skill.zip" if kind == "zip" else "SKILL.md"
    declared_mime = "application/zip" if kind == "zip" else "text/markdown"
    blob = build_blob(filename=filename, data=data, declared_mime=declared_mime)
    await platform_app.state.iam_resource_uploads.put(blob)
    registry = ContentRegistryService(RegistryRepository())
    upload = await registry.create_upload(
        session,
        account_id=account_id,
        actor_user_id=actor_user_id,
        target_visibility=visibility,
        owner_user_id=actor_user_id if visibility == "user_private" else None,
        object_type="resource",
        storage_ref=blob.storage_ref,
        original_filename=blob.original_filename,
        mime_type=blob.mime_type,
        size_bytes=blob.size_bytes,
        content_hash=blob.content_hash,
    )
    await session.commit()
    return upload.id


# ── AC①：在线创建/列表/详情 ──


async def test_online_create_list_detail(session: AsyncSession, platform_client, platform_app) -> None:
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")

    r = await _create_skill(
        platform_client,
        csrf,
        path=f"{BASE_ME}/skills",
        name="data-analyzer",
        tags=["type=helper", "domain=data"],
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["name"] == "data-analyzer"
    assert body["visibility"] == "user_private"
    assert body["source_type"] == "online"
    assert body["tags"] == ["type=helper", "domain=data"]
    assert body["has_auxiliary_files"] is False
    skill_id = body["id"]

    r = await platform_client.get(f"{BASE_ME}/skills")
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    assert [s["id"] for s in items] == [skill_id]
    assert "viking://" not in r.text

    r = await platform_client.get(f"{BASE_ME}/skills/{skill_id}")
    assert r.status_code == 200
    detail = r.json()["result"]
    assert "使用说明正文" in detail["content"]
    # 在线创建未声明 allowed_tools → 序列化 SKILL.md 不包含 → 详情为空列表
    assert detail["allowed_tools"] == []
    assert detail["files"] == []

    # 未删除 Skill 详情 404（防 IDOR：他人/不存在统一 404）
    r = await platform_client.get(f"{BASE_ME}/skills/{uuid.uuid4()}")
    assert r.status_code == 404


# ── AC①③：Account 范围名称唯一（跨 User/可见性，不泄露占用者）──


async def test_name_conflict_across_users_and_visibility(session: AsyncSession, platform_client, platform_app) -> None:
    await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    r = await _create_skill(platform_client, csrf_alice, path=f"{BASE_ME}/skills", name="shared-name")
    assert r.status_code == 200, r.text

    # 另一 User 同 Account 同名 → 409 SKILL_NAME_CONFLICT（AC① 跨 User）
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _create_skill(platform_client, csrf_admin, path=f"{BASE_ME}/skills", name="shared-name")
    assert r.status_code == 409
    err = r.json()
    assert err["detail"]["code"] == "SKILL_NAME_CONFLICT"
    assert err["detail"]["message"] == "该名称在当前 Account 不可用"
    # 不泄露占用者/所属 User/可见性/URI（AC③）
    text = r.text.lower()
    assert "alice" not in text
    assert "user_private" not in text
    assert "viking://" not in text

    # Account Admin 创建共享 Skill 同名 → 409（AC① 跨可见性）
    r = await _create_skill(platform_client, csrf_admin, path=f"{BASE_ACCOUNT}/skills", name="shared-name")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SKILL_NAME_CONFLICT"

    # 不同 Account 同名不受影响（唯一范围是 Account 内，10 §55.1）
    other_account = await platform_app.state.iam_repository.create_account(
        session, ov_account_id="ov_account_other", code="other", display_name="other", status="active"
    )
    await session.commit()
    r = await _create_skill(platform_client, csrf_admin, path=f"{BASE_ME}/skills", name="cross-account")
    assert r.status_code == 200, r.text

    # 跨 Account 成员 Skill 管理视图 → 404（防 IDOR，10 §63）
    r = await platform_client.get(f"{BASE_ADMIN}/users/{other_account.id}/skills")
    assert r.status_code == 404


# ── AC②：name 不可修改（JSON 拒）──


async def test_update_json_rejects_name(session: AsyncSession, platform_client, platform_app) -> None:
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")
    r = await _create_skill(platform_client, csrf, path=f"{BASE_ME}/skills", name="fix-me")
    assert r.status_code == 200, r.text
    skill_id = r.json()["result"]["id"]

    r = await platform_client.put(
        f"{BASE_ME}/skills/{skill_id}",
        json={"name": "renamed", "description": "新描述", "content": "新正文"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SKILL_NAME_IMMUTABLE"

    # 正常更新（无 name）成功；name 保持
    r = await platform_client.put(
        f"{BASE_ME}/skills/{skill_id}",
        json={"description": "新描述", "tags": ["type=updated"], "content": "新正文"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["name"] == "fix-me"
    assert r.json()["result"]["tags"] == ["type=updated"]


# ── AC④：软删立即释放名称、恢复同名冲突保持删除状态 ──


async def test_soft_delete_releases_name_restore_conflict(session: AsyncSession, platform_client, platform_app) -> None:
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")
    r = await _create_skill(platform_client, csrf, path=f"{BASE_ME}/skills", name="recycled")
    assert r.status_code == 200, r.text
    skill_id = r.json()["result"]["id"]

    # 软删除 → 立即释放名称
    r = await platform_client.delete(f"{BASE_ME}/skills/{skill_id}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["resource_type"] == "skill"
    assert result["resource_id"] == skill_id
    assert result["deletion_job_id"]

    # 列表不再出现
    r = await platform_client.get(f"{BASE_ME}/skills")
    assert r.json()["result"]["items"] == []

    # 新 Skill 立即复用同名（AC④ 软删立即释放）
    r = await _create_skill(platform_client, csrf, path=f"{BASE_ME}/skills", name="recycled")
    assert r.status_code == 200, r.text
    new_id = r.json()["result"]["id"]
    assert new_id != skill_id

    # 恢复旧 Skill → SKILL_NAME_CONFLICT，保持删除状态（AC④ 不改名不覆盖）
    r = await platform_client.post(f"{BASE_ME}/skills/{skill_id}/restore", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SKILL_NAME_CONFLICT"

    # 旧 Skill 仍不可见；同名新 Skill 未被覆盖
    r = await platform_client.get(f"{BASE_ME}/skills/{skill_id}")
    assert r.status_code == 404
    r = await platform_client.get(f"{BASE_ME}/skills/{new_id}")
    assert r.status_code == 200
    assert r.json()["result"]["name"] == "recycled"

    # 释放同名后恢复成功（名称未被占用 → 恢复原 ID/名称）
    await platform_client.delete(f"{BASE_ME}/skills/{new_id}", headers={"X-CSRF-Token": csrf})
    r = await platform_client.post(f"{BASE_ME}/skills/{skill_id}/restore", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["resource_id"] == skill_id


# ── 上传消费 upload_id（10 §61.5）与 ZIP 安全校验（10 §54.2）──


async def test_upload_skill_md_and_zip_create(session: AsyncSession, platform_client, platform_app) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")

    # 单个 SKILL.md 上传创建
    upload_id = await _register_upload(
        platform_app,
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        data=_make_skill_md("md-uploaded").encode("utf-8"),
        kind="skill_md",
    )
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "md-uploaded", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["source_type"] == "skill_md"
    assert body["name"] == "md-uploaded"

    # ZIP 上传创建（辅助文件 + 工具范围解析）
    upload_id = await _register_upload(
        platform_app,
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        data=_make_zip("zip-uploaded"),
        kind="zip",
    )
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "zip-uploaded", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["source_type"] == "zip"
    assert body["has_auxiliary_files"] is True
    detail = await platform_client.get(f"{BASE_ME}/skills/{body['id']}")
    assert detail.status_code == 200
    files = detail.json()["result"]["files"]
    assert {f["name"] for f in files} == {"scripts/run.py", "references/guide.md"}

    # 包内 name 与请求 name 不一致 → SKILL_INVALID_FORMAT（包被消费）
    upload_id = await _register_upload(
        platform_app,
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        data=_make_skill_md("real-name").encode("utf-8"),
        kind="skill_md",
    )
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "wrong-name", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "SKILL_INVALID_FORMAT"

    # 跨 User 消费 → UPLOAD_SCOPE_MISMATCH 拒绝（04 §10.14）
    upload_id = await _register_upload(
        platform_app,
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        data=_make_skill_md("steal").encode("utf-8"),
        kind="skill_md",
    )
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "steal", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UPLOAD_SCOPE_MISMATCH"


async def test_zip_safety_validation(session: AsyncSession, platform_client, platform_app) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")

    cases = [
        ("../evil.md", "ZIP_PATH_TRAVERSAL"),
        ("/abs/path.md", "ZIP_PATH_TRAVERSAL"),
    ]
    for member, _code in cases:
        upload_id = await _register_upload(
            platform_app,
            session,
            account_id=setup.acme.id,
            actor_user_id=setup.alice.id,
            visibility="user_private",
            data=_make_zip("safety", bad_member=member),
            kind="zip",
        )
        r = await platform_client.post(
            f"{BASE_ME}/skills",
            json={"name": "safety", "upload_id": str(upload_id)},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 400, (member, r.text)
        assert r.json()["detail"]["code"] == "SKILL_INVALID_FORMAT"

    # 无 SKILL.md → 拒绝
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notes.md", "no skill here")
    upload_id = await _register_upload(
        platform_app,
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        data=buf.getvalue(),
        kind="zip",
    )
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "noskill", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "SKILL_INVALID_FORMAT"


# ── AC②：ZIP 整体替换 name 必须一致 ──


async def test_upload_replace_name_mismatch(session: AsyncSession, platform_client, platform_app) -> None:
    setup = await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")
    r = await _create_skill(platform_client, csrf, path=f"{BASE_ME}/skills", name="replace-me")
    assert r.status_code == 200, r.text
    skill_id = r.json()["result"]["id"]

    upload_id = await _register_upload(
        platform_app,
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        data=_make_zip("different-name"),
        kind="zip",
    )
    r = await platform_client.put(
        f"{BASE_ME}/skills/{skill_id}",
        json={"upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SKILL_NAME_IMMUTABLE"

    # 新包 name 一致 → 整体替换成功
    upload_id = await _register_upload(
        platform_app,
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        data=_make_zip("replace-me"),
        kind="zip",
    )
    r = await platform_client.put(
        f"{BASE_ME}/skills/{skill_id}",
        json={"upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["has_auxiliary_files"] is True
    assert r.json()["result"]["name"] == "replace-me"


# ── AC⑦：Account Admin 成员 Skill 只读 + 发布权限；PSA 全只读 ──


async def test_admin_member_skills_readonly_and_publish_permissions(
    session: AsyncSession, platform_client, platform_app
) -> None:
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    r = await _create_skill(platform_client, csrf_alice, path=f"{BASE_ME}/skills", name="member-skill")
    assert r.status_code == 200, r.text
    skill_id = r.json()["result"]["id"]

    # 普通 User 不能发布（AC⑦：即使目标是自己的 Skill）
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/skills/{skill_id}/publish",
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert r.status_code == 403

    # Account Admin 读取成员 Skill（含 owner 字段）；不能编辑/删除/恢复他人私有
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.get(f"{BASE_ADMIN}/users/{setup.alice.id}/skills")
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    assert [s["id"] for s in items] == [skill_id]
    assert items[0]["owner_user_id"] == str(setup.alice.id)

    r = await platform_client.get(f"{BASE_ADMIN}/users/{setup.alice.id}/skills/{skill_id}")
    assert r.status_code == 200
    assert "member-skill" in r.json()["result"]["content"]

    # 成员 Skill 不提供 PUT/DELETE/restore 管理接口（10 §61.3）：
    # PUT/DELETE 与 GET 同路径 → 405；restore 无对应路由 → 404
    r = await platform_client.put(
        f"{BASE_ADMIN}/users/{setup.alice.id}/skills/{skill_id}",
        json={"description": "hack"},
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 405
    r = await platform_client.delete(
        f"{BASE_ADMIN}/users/{setup.alice.id}/skills/{skill_id}",
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 405
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/skills/{skill_id}/restore",
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 404

    # 跨 Account 目标 → 404（防 IDOR）
    other_account = await platform_app.state.iam_repository.create_account(
        session, ov_account_id="ov_account_x", code="xcorp", display_name="xcorp", status="active"
    )
    await session.commit()
    r = await platform_client.get(f"{BASE_ADMIN}/users/{setup.admin.id}/skills")
    assert r.status_code == 200  # admin 自己的私有 Skill 列表（空）
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{other_account.id}/users/{setup.alice.id}/skills"
    )
    assert r.status_code == 403  # 非 PSA 无平台只读权限（权限守卫先于路由）

    # PSA 全 Skill 只读（AC⑦：平台只读路由可读；写动作 403）
    csrf_psa = await _login(platform_client, "psa@platform.local")
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/skills"
    )
    assert r.status_code == 200
    assert [s["id"] for s in r.json()["result"]["items"]] == [skill_id]
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/skills/{skill_id}"
    )
    assert r.status_code == 200
    r = await platform_client.put(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/skills/{skill_id}",
        json={"description": "psa-hack"},
        headers={"X-CSRF-Token": csrf_psa},
    )
    # 平台路由全只读：无 PUT 端点（405），不存在任何 Skill 写入口（10 §61.4）
    assert r.status_code == 405
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/skills/{skill_id}/publish",
        headers={"X-CSRF-Token": csrf_psa},
    )
    assert r.status_code == 403

    # PSA 跨 Account 平台只读目标校验 → 404（alice 不属于 other Account）
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{other_account.id}/users/{setup.alice.id}/skills"
    )
    assert r.status_code == 404


# ── 共享 Skill：普通 User 只读、Account Admin 管理 ──


async def test_shared_skill_readonly_for_user_manage_for_admin(session: AsyncSession, platform_client, platform_app) -> None:
    await _seed(session)
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _create_skill(platform_client, csrf_admin, path=f"{BASE_ACCOUNT}/skills", name="shared-tool")
    assert r.status_code == 200, r.text
    shared_id = r.json()["result"]["id"]
    assert r.json()["result"]["visibility"] == "account_shared"

    # 普通 User 可读不可写
    csrf_alice = await _login(platform_client, "alice@acme.com")
    r = await platform_client.get(f"{BASE_ACCOUNT}/skills")
    assert r.status_code == 200
    assert [s["id"] for s in r.json()["result"]["items"]] == [shared_id]
    r = await platform_client.put(
        f"{BASE_ACCOUNT}/skills/{shared_id}",
        json={"description": "hack"},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert r.status_code == 403
    r = await platform_client.delete(f"{BASE_ACCOUNT}/skills/{shared_id}", headers={"X-CSRF-Token": csrf_alice})
    assert r.status_code == 403

    # Account Admin 软删 + 恢复共享 Skill（重新登录以切回 admin Session）
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.delete(f"{BASE_ACCOUNT}/skills/{shared_id}", headers={"X-CSRF-Token": csrf_admin})
    assert r.status_code == 200, r.text
    r = await platform_client.post(
        f"{BASE_ACCOUNT}/skills/{shared_id}/restore", headers={"X-CSRF-Token": csrf_admin}
    )
    assert r.status_code == 200, r.text
    r = await platform_client.get(f"{BASE_ACCOUNT}/skills/{shared_id}")
    assert r.status_code == 200


async def test_publish_outbox_event_created(session: AsyncSession, platform_client, platform_app) -> None:
    """发布请求在 PG 事务内写 outbox 事件（两段式第 2 步，10 §58.4）。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    r = await _create_skill(platform_client, csrf_alice, path=f"{BASE_ME}/skills", name="publish-me")
    skill_id = r.json()["result"]["id"]
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ADMIN}/users/{setup.alice.id}/skills/{skill_id}/publish",
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["visibility"] == "account_shared"

    store = RegistryRepository()
    ref = await store.get_ref(session, uuid.UUID(skill_id))
    assert ref.visibility == "account_shared"
    assert ref.owner_user_id is None
    assert ref.ov_uri == "viking://agent/skills/publish-me"

    from sqlalchemy import select

    from openviking.server.platform.models import IamOutbox

    events = list(
        (
            await session.execute(
                select(IamOutbox).where(IamOutbox.event_type == EVENT_SKILL_PUBLISH)
            )
        ).scalars()
    )
    assert len(events) == 1
    assert events[0].payload["to_uri"] == "viking://agent/skills/publish-me"
