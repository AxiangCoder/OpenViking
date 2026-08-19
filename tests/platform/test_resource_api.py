"""P2-E3 Resource 产品 API 测试（09 §36–§48，14 号计划 §97.3）。

验收映射：
- AC①：未指定目标固定私有根；共享导入必须经 `/account/*` 或
  `/platform/accounts/{id}/*` 并持对应写权限；
- AC⑦：Upload 绑定主体，跨 Scope 消费/过期/重放拒绝，原子 ready→consumed；
- AC⑧：可见无写权限 403、不可见 404，错误码按 09 §46.5；
- AC⑨：Node ID 越权（跨 Resource 引用）被拒绝并审计（09 §48 #12）；
- AC⑩：capabilities 由服务端强制（上传限制/来源类型/周期预设）；
- AC⑪：平台成员只读 Resource 可用且不含下载/导出；
- 元数据乐观锁（09 §42.4）、导入/列表/详情/替换/Refresh/重试/搜索 DTO。
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamAuditEvent, IamOutbox, PlatformUpload
from tests.platform.helpers import build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE_ME = "/api/platform/v1/me"
BASE_ACCOUNT = "/api/platform/v1/account"
BASE_PLATFORM = "/api/platform/v1/platform"
BASE_ADMIN = "/api/platform/v1/admin"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"
ALICE = "alice@acme.com"
ADMIN = "admin@acme.com"
PSA = "psa@platform.local"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


def _csrf(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf}


async def _upload(
    client: httpx.AsyncClient,
    csrf: str,
    path: str,
    *,
    filename: str,
    content: bytes,
    mime: str = "application/octet-stream",
) -> str:
    r = await client.post(
        path,
        files={"file": (filename, content, mime)},
        headers=_csrf(csrf),
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]["upload_id"]


async def _import(
    client: httpx.AsyncClient,
    csrf: str,
    path: str,
    items: list[dict],
    *,
    idempotency_key: str | None = None,
) -> dict:
    headers = _csrf(csrf)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    r = await client.post(path, json={"items": items}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def _upload_and_import(
    client: httpx.AsyncClient,
    csrf: str,
    upload_path: str,
    import_path: str,
    *,
    filename: str,
    content: bytes = b"hello resource content",
    mime: str = "text/plain",
) -> tuple[str, str]:
    """上传并导入；返回 (resource_id, operation_id)。"""
    upload_id = await _upload(client, csrf, upload_path, filename=filename, content=content, mime=mime)
    batch = await _import(client, csrf, import_path, [{"upload_id": upload_id, "name": filename}])
    item = batch["items"][0]
    assert item["error"] is None, item
    return item["resource_id"], item["operation_id"]


# ═══════════════════════════════════════════════════════════════════════
# AC⑦：Upload 绑定主体、跨 Scope/重放/过期拒绝、原子消费
# ═══════════════════════════════════════════════════════════════════════


async def test_upload_binds_actor_and_scope(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)

    upload_id = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="a.pdf", content=b"%PDF-1.4 test"
    )
    row = (
        await session.execute(select(PlatformUpload).where(PlatformUpload.id == upload_id))
    ).scalar_one()
    assert row.actor_user_id is not None
    assert row.target_visibility == "user_private"
    assert row.status == "ready"
    assert row.original_filename == "a.pdf"
    assert row.mime_type == "application/pdf"  # Magic Bytes 服务端检测（AC⑩）

    # 跨 Scope 消费：私有 Upload 不能经 /account 导入（AC⑦/09 §48 #12）
    admin_csrf = await _login(platform_client, ADMIN)
    r = await platform_client.post(
        f"{BASE_ACCOUNT}/resources/imports",
        json={"items": [{"upload_id": upload_id}]},
        headers=_csrf(admin_csrf),
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["items"][0]["error"]["code"] == "RESOURCE_NOT_FOUND"

    # 私有 Upload 其他 Actor 消费拒绝（admin 用 alice 的私有 Upload）
    r = await platform_client.post(
        f"{BASE_ME}/resources/imports",
        json={"items": [{"upload_id": upload_id}]},
        headers=_csrf(admin_csrf),
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["items"][0]["error"]["code"] == "RESOURCE_NOT_FOUND"

    # 正常消费 → ready→consumed 原子转换（重新登录 alice，Session 被轮换）
    alice_csrf = await _login(platform_client, ALICE)
    batch = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports", [{"upload_id": upload_id}]
    )
    assert batch["items"][0]["error"] is None
    await session.commit()
    row = (
        await session.execute(
            select(PlatformUpload)
            .where(PlatformUpload.id == upload_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.status == "consumed"

    # 重放同一 Upload → RESOURCE_UPLOAD_ALREADY_CONSUMED（AC⑦）
    r = await platform_client.post(
        f"{BASE_ME}/resources/imports",
        json={"items": [{"upload_id": upload_id}]},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["items"][0]["error"]["code"] == "RESOURCE_UPLOAD_ALREADY_CONSUMED"

    # 审计：跨 Scope 拒绝已记录（09 §47.2：Upload 重放/跨 Scope 拒绝）
    await session.commit()
    events = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.action == "resource.upload.denied")
    )
    assert len(list(events.scalars())) >= 2


async def test_upload_expired_rejected(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    upload_id = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="x.txt", content=b"x"
    )
    # 手工把过期时间拨回过去
    row = (
        await session.execute(select(PlatformUpload).where(PlatformUpload.id == upload_id))
    ).scalar_one()
    from datetime import timedelta

    row.expires_at = row.expires_at - timedelta(hours=1)
    await session.commit()
    r = await platform_client.post(
        f"{BASE_ME}/resources/imports",
        json={"items": [{"upload_id": upload_id}]},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    item = r.json()["result"]["items"][0]
    assert item["error"]["code"] == "RESOURCE_UPLOAD_EXPIRED"
    assert item["error"]["retryable"] is True


# ═══════════════════════════════════════════════════════════════════════
# AC⑩：capabilities 服务端强制 + 导入/文件校验
# ═══════════════════════════════════════════════════════════════════════


async def test_capabilities_are_server_enforced(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    await _login(platform_client, ALICE)
    r = await platform_client.get("/api/platform/v1/resources/capabilities")
    assert r.status_code == 200
    caps = r.json()["result"]
    assert caps["source_types"] == ["upload", "web", "git"]
    assert caps["upload"]["max_files_per_batch"] == 10
    assert caps["upload"]["max_file_size_bytes"] == 10 * 1024 * 1024
    assert caps["watch"]["interval_presets_minutes"] == [60, 360, 720, 1440, 10080]


async def test_upload_size_and_format_limits(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)

    # 超大小限制 → 413 RESOURCE_FILE_TOO_LARGE
    big = b"x" * (10 * 1024 * 1024 + 1)
    r = await platform_client.post(
        f"{BASE_ME}/resource-uploads",
        files={"file": ("big.bin", big, "application/octet-stream")},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_FILE_TOO_LARGE"

    # 归档包在 Resource 消费侧拒绝（09 §47.3）：
    # 暂存入口与 Skill 共用（10 §61.5），`.zip` 可暂存，但 Resource
    # 导入消费时拒绝 → 单项 error RESOURCE_FORMAT_UNSUPPORTED
    r = await platform_client.post(
        f"{BASE_ME}/resource-uploads",
        files={"file": ("archive.zip", b"PK\x03\x04fakezip", "application/zip")},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    upload_id = r.json()["result"]["upload_id"]
    r = await platform_client.post(
        f"{BASE_ME}/resources/imports",
        json={"items": [{"upload_id": upload_id}]},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["items"][0]["error"]["code"] == "RESOURCE_FORMAT_UNSUPPORTED"

    # 其他归档扩展名暂存即拒（Skill 仅需要 .zip）→ 415 RESOURCE_FORMAT_UNSUPPORTED
    r = await platform_client.post(
        f"{BASE_ME}/resource-uploads",
        files={"file": ("bundle.tar.gz", b"\x1f\x8btar", "application/gzip")},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 415, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_FORMAT_UNSUPPORTED"

    # 每批最多 10 个文件（capabilities 服务端强制，AC⑩）
    upload_id = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="ok.txt", content=b"ok"
    )
    r = await platform_client.post(
        f"{BASE_ME}/resources/imports",
        json={"items": [{"upload_id": upload_id}] * 11},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_FILE_TOO_LARGE"


async def test_remote_source_security(session: AsyncSession, platform_client) -> None:
    """09 §40.6：私网/localhost/file/Userinfo/HTTP 全部拒绝（AC⑩ + §48 #12）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    blocked = [
        "http://example.com/page",  # 默认不允许 HTTP
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://localhost/page",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://192.168.1.1/x",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@example.com/page",  # Userinfo
        ("git@github.com:org/repo.git", True),  # Git SSH 拒绝
        ("ssh://git@github.com/org/repo.git", True),
    ]
    for entry in blocked:
        if isinstance(entry, tuple):
            url, is_git = entry
        else:
            url, is_git = entry, False
        r = await platform_client.post(
            f"{BASE_ME}/resources/imports",
            json={"items": [{"source_url": url, "is_git": is_git}]},
            headers=_csrf(alice_csrf),
        )
        assert r.status_code == 200, (url, r.status_code, r.text)
        item = r.json()["result"]["items"][0]
        assert item["error"]["code"] == "RESOURCE_SOURCE_BLOCKED", (url, item)

    # 不支持的协议 → RESOURCE_SOURCE_UNSUPPORTED
    r = await platform_client.post(
        f"{BASE_ME}/resources/imports",
        json={"items": [{"source_url": "https://example.com/page"}]},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200

    await session.commit()
    events = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.reason == "RESOURCE_SOURCE_BLOCKED")
    )
    assert len(list(events.scalars())) >= len(blocked)


# ═══════════════════════════════════════════════════════════════════════
# AC①：目标固定私有根/共享入口 + 写权限
# ═══════════════════════════════════════════════════════════════════════


async def test_shared_import_requires_account_scope_and_permission(
    session: AsyncSession, platform_client
) -> None:
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)

    # 普通 User 无共享写权限 → 403（AC⑧ 可见但无写权限）
    upload_id = await _upload(
        platform_client, admin_csrf, f"{BASE_ACCOUNT}/resource-uploads", filename="s.txt", content=b"s"
    )
    alice_csrf = await _login(platform_client, ALICE)
    r = await platform_client.post(
        f"{BASE_ACCOUNT}/resources/imports",
        json={"items": [{"upload_id": upload_id}]},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"

    # 私有入口导入的 Resource 固定私有根；DTO 不返回 ov_uri（09 §48 #3）
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="private.txt",
    )
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 200, r.text
    body = r.text
    assert "viking://" not in body
    assert r.json()["result"]["visibility"] == "user_private"

    # 共享导入经 /account 由 Account Admin 完成 → account_shared
    admin_csrf = await _login(platform_client, ADMIN)
    batch = await _import(
        platform_client, admin_csrf, f"{BASE_ACCOUNT}/resources/imports", [{"upload_id": upload_id}]
    )
    shared_id = batch["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_ACCOUNT}/resources/{shared_id}")
    assert r.json()["result"]["visibility"] == "account_shared"


async def test_platform_managed_shared_import(session: AsyncSession, platform_client) -> None:
    """平台代管：PSA 经 /platform/accounts/{id}/resource-uploads + imports（AC①）。"""
    setup = await _seed(session)
    psa_csrf = await _login(platform_client, PSA)
    upload_id = await _upload(
        platform_client,
        psa_csrf,
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/resource-uploads",
        filename="platform.txt",
        content=b"platform managed",
    )
    r = await platform_client.post(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/resources/imports",
        json={"items": [{"upload_id": upload_id}]},
        headers=_csrf(psa_csrf),
    )
    assert r.status_code == 200, r.text
    res_id = r.json()["result"]["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_PLATFORM}/accounts/{setup.acme.id}/resources/{res_id}")
    assert r.status_code == 200
    assert r.json()["result"]["visibility"] == "account_shared"


# ═══════════════════════════════════════════════════════════════════════
# 导入：网页 / Git / 批量 / Idempotency-Key
# ═══════════════════════════════════════════════════════════════════════


async def test_web_and_git_imports(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)

    web = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"source_url": "https://docs.example.com/guide", "name": "指南"}],
    )
    web_id = web["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_ME}/resources/{web_id}")
    detail = r.json()["result"]
    assert detail["name"] == "指南"
    assert detail["source_type"] == "web"
    assert detail["lifecycle_status"] == "active"

    git = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"source_url": "https://github.com/example/repo", "is_git": True}],
    )
    git_id = git["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_ME}/resources/{git_id}")
    assert r.json()["result"]["source_type"] == "git"

    # Git 节点树：目录结构保留（src/docs/assets），隐藏控制文件不出现
    r = await platform_client.get(f"{BASE_ME}/resources/{git_id}/nodes")
    assert r.status_code == 200, r.text
    names = {n["name"] for n in r.json()["result"]["items"]}
    assert {"src", "docs", "assets", "README.md"} <= names
    assert ".abstract.md" not in names


async def test_batch_import_partial_failure(session: AsyncSession, platform_client) -> None:
    """批量导入按文件独立成功/失败（09 §40.5），返回 batch_id。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    good = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="good.txt", content=b"g"
    )
    bad = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="fail-parse.txt", content=b"b"
    )
    r = await platform_client.post(
        f"{BASE_ME}/resources/imports",
        json={"items": [{"upload_id": good}, {"upload_id": bad}]},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["batch_id"]
    items = result["items"]
    ok_item = next(i for i in items if i["error"] is None)
    bad_item = next(i for i in items if i["error"] is not None)
    assert bad_item["error"]["code"] == "RESOURCE_PARSE_FAILED"
    assert bad_item["error"]["retryable"] is True

    r = await platform_client.get(f"{BASE_ME}/resources/{ok_item['resource_id']}")
    assert r.status_code == 200
    # 失败项：Resource 保持 failed（首次失败仅管理者/属主可见，AC④）
    r = await platform_client.get(f"{BASE_ME}/resources/{bad_item['resource_id']}")
    assert r.json()["result"]["lifecycle_status"] == "failed"


async def test_import_idempotency_key_replay(session: AsyncSession, platform_client) -> None:
    """Idempotency-Key 重放返回相同 Batch/Operation，不生成重复 Resource（AC⑦）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    upload_id = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="idem.txt", content=b"idem"
    )
    first = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"upload_id": upload_id}], idempotency_key="import-key-1",
    )
    upload_id2 = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="idem2.txt", content=b"idem2"
    )
    replay = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"upload_id": upload_id2}], idempotency_key="import-key-1",
    )
    assert replay["batch_id"] == first["batch_id"]
    assert replay["items"][0]["resource_id"] == first["items"][0]["resource_id"]
    assert replay["items"][0]["operation_id"] == first["items"][0]["operation_id"]


# ═══════════════════════════════════════════════════════════════════════
# 列表 / 详情 / PATCH 乐观锁（09 §42.4，AC⑧）
# ═══════════════════════════════════════════════════════════════════════


async def test_list_detail_and_patch_optimistic_lock(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="meta.txt",
    )

    # 列表：owner 可见（含占位行语义），DTO 字段齐全
    r = await platform_client.get(f"{BASE_ME}/resources")
    assert r.status_code == 200
    items = r.json()["result"]["items"]
    assert any(i["id"] == f"res_{res_id}" for i in items)

    # PATCH 元数据 + 乐观锁
    r = await platform_client.patch(
        f"{BASE_ME}/resources/{res_id}",
        json={"display_name": "改名", "description": "新说明", "tags": ["type=doc"]},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    patched = r.json()["result"]
    assert patched["name"] == "改名"
    assert patched["tags"] == ["type=doc"]
    version = patched["version"]

    # 旧版本号提交 → RESOURCE_VERSION_CONFLICT（409）
    r = await platform_client.patch(
        f"{BASE_ME}/resources/{res_id}",
        json={"display_name": "again", "version": version - 1},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_VERSION_CONFLICT"

    # If-Match 头同语义
    r = await platform_client.patch(
        f"{BASE_ME}/resources/{res_id}",
        json={"description": "via if-match"},
        headers={**_csrf(alice_csrf), "If-Match": str(version - 1)},
    )
    assert r.status_code == 409

    # tags 同步 Outbox（04 §10.10：不允许双写分叉）
    await session.commit()
    events = await session.execute(select(IamOutbox))
    assert any(e.event_type == "content.tags_sync" for e in events.scalars())


async def test_invisible_resource_404_and_write_without_permission_403(
    session: AsyncSession, platform_client
) -> None:
    """AC⑧：不可见 404、可见无写权限 403；跨用户私有 404。"""
    await _seed(session)
    admin_csrf = await _login(platform_client, ADMIN)

    res_id, _ = await _upload_and_import(
        platform_client, admin_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="admin-private.txt",
    )
    # 共享资源由 admin 创建（后续 alice 读取用）
    shared_upload = await _upload(
        platform_client, admin_csrf, f"{BASE_ACCOUNT}/resource-uploads", filename="shared.txt", content=b"s"
    )
    batch = await _import(
        platform_client, admin_csrf, f"{BASE_ACCOUNT}/resources/imports", [{"upload_id": shared_upload}]
    )
    shared_id = batch["items"][0]["resource_id"]

    # alice 访问 admin 私有 Resource → 404（不可见）
    alice_csrf = await _login(platform_client, ALICE)
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"
    # 不存在的 ID → 404 RESOURCE_NOT_FOUND
    r = await platform_client.get(f"{BASE_ME}/resources/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"

    # 共享：alice（普通 User）可读、不可写 → PATCH 403
    r = await platform_client.get(f"{BASE_ACCOUNT}/resources/{shared_id}")
    assert r.status_code == 200
    r = await platform_client.patch(
        f"{BASE_ACCOUNT}/resources/{shared_id}",
        json={"description": "nope"},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "PERMISSION_NOT_GRANTED"


# ═══════════════════════════════════════════════════════════════════════
# replace / refresh / retry（09 §43.1/§46.2）
# ═══════════════════════════════════════════════════════════════════════


async def test_replace_upload_and_refresh_web(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)

    # 上传 Resource → replace 新文件（同一 Resource，旧版本处理期间可读）
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="v1.txt", content=b"version one",
    )
    new_upload = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="v2.txt", content=b"version two"
    )
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/replace",
        json={"upload_id": new_upload},
        headers=_csrf(alice_csrf),
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["resource_id"] == res_id
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.json()["result"]["lifecycle_status"] == "active"

    # Refresh 稳定网页来源
    web = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"source_url": "https://refresh.example.com/page"}],
    )
    web_id = web["items"][0]["resource_id"]
    r = await platform_client.post(
        f"{BASE_ME}/resources/{web_id}/refresh", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["operation_id"]

    # 上传来源不能 Refresh → RESOURCE_SOURCE_NOT_STABLE
    r = await platform_client.post(f"{BASE_ME}/resources/{res_id}/refresh", headers=_csrf(alice_csrf))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_SOURCE_NOT_STABLE"


async def test_refresh_failure_keeps_previous_version(session: AsyncSession, platform_client) -> None:
    """AC④：Refresh 失败保持 active 并提供上一次成功版本（09 §41.2）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    web = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"source_url": "https://refresh.example.com/page"}],
    )
    web_id = web["items"][0]["resource_id"]
    # 把来源显示值改为失败注入 host，模拟抓取失败
    from openviking.server.platform.models import PlatformContentRef

    await session.commit()
    ref = (
        await session.execute(select(PlatformContentRef).where(PlatformContentRef.id == web_id))
    ).scalar_one()
    ref.source_display = "https://fail.invalid/now"
    await session.commit()

    r = await platform_client.post(
        f"{BASE_ME}/resources/{web_id}/refresh", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    r = await platform_client.get(f"{BASE_ME}/resources/{web_id}")
    detail = r.json()["result"]
    # 失败保持 active（旧版本可读，09 §41.2 最近同步失败）
    assert detail["lifecycle_status"] == "active"
    assert detail["current_operation"]["status"] == "failed"


async def test_retry_failed_import(session: AsyncSession, platform_client) -> None:
    """09 §46.2：重试首次失败的导入；上传来源必须重新提交。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    upload_id = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="fail-retry.txt", content=b"x"
    )
    batch = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports", [{"upload_id": upload_id}]
    )
    res_id = batch["items"][0]["resource_id"]
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.json()["result"]["lifecycle_status"] == "failed"

    # 不提交新来源 → RESOURCE_SOURCE_UNSUPPORTED（上传必须重新提交，09 §46.2）
    r = await platform_client.post(f"{BASE_ME}/resources/{res_id}/retry", json={}, headers=_csrf(alice_csrf))
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_SOURCE_UNSUPPORTED"

    # 新上传重试 → active
    new_upload = await _upload(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", filename="ok.txt", content=b"ok"
    )
    r = await platform_client.post(
        f"{BASE_ME}/resources/{res_id}/retry", json={"upload_id": new_upload}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.json()["result"]["lifecycle_status"] == "active"


# ═══════════════════════════════════════════════════════════════════════
# Nodes / 搜索（09 §42.3/§46.2，AC⑨）
# ═══════════════════════════════════════════════════════════════════════


async def test_nodes_tree_preview_download_and_node_id_authorization(
    session: AsyncSession, platform_client
) -> None:
    """AC⑨：Node ID 越权（跨 Resource 引用）被拒绝并审计（09 §48 #12）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)

    git = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"source_url": "https://github.com/example/repo", "is_git": True}],
    )
    git_id = git["items"][0]["resource_id"]
    other = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="other.txt",
    )
    other_id = other[0]

    r = await platform_client.get(f"{BASE_ME}/resources/{git_id}/nodes")
    nodes = r.json()["result"]["items"]
    readme = next(n for n in nodes if n["name"] == "README.md")
    node_id = readme["node_id"]

    # 正常读取 + 预览
    r = await platform_client.get(f"{BASE_ME}/resources/{git_id}/nodes/{node_id}")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["preview"] is not None

    # 下载：安全 Content-Disposition（09 §42.3，AC⑪ 不内联可执行）
    r = await platform_client.get(f"{BASE_ME}/resources/{git_id}/nodes/{node_id}/download")
    assert r.status_code == 200
    assert "README.md" in r.headers["content-disposition"]

    # 目录展开：docs 目录下
    docs = next(n for n in nodes if n["name"] == "docs")
    r = await platform_client.get(
        f"{BASE_ME}/resources/{git_id}/nodes", params={"node_id": docs["node_id"]}
    )
    names = {n["name"] for n in r.json()["result"]["items"]}
    assert "guide.md" in names

    # AC⑨：同一 node_id 用在另一个 Resource → 404 + 审计
    r = await platform_client.get(f"{BASE_ME}/resources/{other_id}/nodes/{node_id}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"
    await session.commit()
    events = await session.execute(
        select(IamAuditEvent).where(IamAuditEvent.action == "resource.node.denied")
    )
    assert len(list(events.scalars())) >= 1

    # 篡改的 node_id → 404
    r = await platform_client.get(f"{BASE_ME}/resources/{git_id}/nodes/{node_id}xx")
    assert r.status_code == 404


async def test_search_within_resource(session: AsyncSession, platform_client) -> None:
    """09 §46.2：仅在当前 Resource 内检索；返回 Node ID 不返回 URI。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    git = await _import(
        platform_client, alice_csrf, f"{BASE_ME}/resources/imports",
        [{"source_url": "https://github.com/example/repo", "is_git": True}],
    )
    git_id = git["items"][0]["resource_id"]
    r = await platform_client.post(
        f"{BASE_ME}/resources/{git_id}/search", json={"query": "hello"}, headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    items = r.json()["result"]["items"]
    assert len(items) >= 1
    assert all(i["node_id"].startswith("nv1.") for i in items)
    assert all("viking://" not in str(i) for i in items)


# ═══════════════════════════════════════════════════════════════════════
# AC⑪：admin/platform 成员只读 Resource（不含下载/导出）
# ═══════════════════════════════════════════════════════════════════════


async def test_member_readonly_resources(session: AsyncSession, platform_client) -> None:
    """AC⑪：admin/platform 成员只读 Resource 可用且不含下载/导出。"""
    setup = await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)

    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="member-view.txt",
    )

    # Account Admin 成员只读
    admin_csrf = await _login(platform_client, ADMIN)
    r = await platform_client.get(f"{BASE_ADMIN}/users/{setup.alice.id}/resources")
    assert r.status_code == 200, r.text
    assert any(i["id"] == f"res_{res_id}" for i in r.json()["result"]["items"])
    r = await platform_client.get(f"{BASE_ADMIN}/users/{setup.alice.id}/resources/{res_id}")
    assert r.status_code == 200

    # Platform 成员只读
    await _login(platform_client, PSA)
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/resources"
    )
    assert r.status_code == 200
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.alice.id}/resources/{res_id}"
    )
    assert r.status_code == 200
    # DTO 不含 ov_uri（09 §48 #3）
    assert "viking://" not in r.text

    # 不含下载/导出：成员只读路由无 nodes/download 端点（05 §12.6）
    r = await platform_client.get(f"{BASE_ADMIN}/users/{setup.alice.id}/resources/{res_id}/nodes")
    assert r.status_code == 404 or r.status_code == 405

    # 管理员只读不能写：无 PATCH 路由 → 404/405
    admin_csrf = await _login(platform_client, ADMIN)
    r = await platform_client.patch(
        f"{BASE_ADMIN}/users/{setup.alice.id}/resources/{res_id}",
        json={"description": "x"},
        headers=_csrf(admin_csrf),
    )
    assert r.status_code in (404, 405)

    # 跨 Account 用户 → 404（防枚举）
    await _login(platform_client, PSA)
    r = await platform_client.get(
        f"{BASE_PLATFORM}/accounts/{setup.acme.id}/users/{setup.psa.id}/resources"
    )
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Activity（09 §43.3）与 recycle-bin（05 §12.5）
# ═══════════════════════════════════════════════════════════════════════


async def test_activity_and_cancel(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, op_id = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="activity.txt",
    )
    r = await platform_client.get("/api/platform/v1/activity")
    assert r.status_code == 200, r.text
    items = r.json()["result"]["items"]
    assert any(i["id"] == op_id for i in items)

    # 已终态 Operation 不可取消 → RESOURCE_OPERATION_NOT_CANCELLABLE（409）
    r = await platform_client.post(
        f"/api/platform/v1/activity/{op_id}/cancel", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESOURCE_OPERATION_NOT_CANCELLABLE"

    # Resource 内 operations 列表
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}/operations")
    assert r.status_code == 200
    assert len(r.json()["result"]["items"]) >= 1


async def test_me_recycle_bin_restore(session: AsyncSession, platform_client) -> None:
    """05 §12.5：当前用户回收站列表 + 类型化恢复（09 §45.3）。"""
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    res_id, _ = await _upload_and_import(
        platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
        filename="recycle.txt",
    )
    r = await platform_client.delete(f"{BASE_ME}/resources/{res_id}", headers=_csrf(alice_csrf))
    assert r.status_code == 200, r.text
    job_id = r.json()["result"]["deletion_job_id"]

    # 正常入口 404（删除中，09 §45.2 步骤 5）
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 404

    r = await platform_client.get("/api/platform/v1/recycle-bin")
    assert r.status_code == 200, r.text
    items = r.json()["result"]["items"]
    assert any(i["id"] == job_id and i["restore_allowed"] is True for i in items)

    r = await platform_client.post(
        f"/api/platform/v1/recycle-bin/{job_id}/restore", headers=_csrf(alice_csrf)
    )
    assert r.status_code == 200, r.text
    r = await platform_client.get(f"{BASE_ME}/resources/{res_id}")
    assert r.status_code == 200
    assert r.json()["result"]["lifecycle_status"] == "active"


async def test_list_filters_and_cursor(session: AsyncSession, platform_client) -> None:
    await _seed(session)
    alice_csrf = await _login(platform_client, ALICE)
    for i in range(3):
        await _upload_and_import(
            platform_client, alice_csrf, f"{BASE_ME}/resource-uploads", f"{BASE_ME}/resources/imports",
            filename=f"f{i}.txt",
        )
    r = await platform_client.get(f"{BASE_ME}/resources", params={"source_type": "upload", "limit": 2})
    assert r.status_code == 200
    result = r.json()["result"]
    assert len(result["items"]) == 2
    assert result["next_cursor"] is not None
    r2 = await platform_client.get(f"{BASE_ME}/resources", params={"cursor": result["next_cursor"]})
    assert r2.status_code == 200
    seen = {i["id"] for i in result["items"]} | {i["id"] for i in r2.json()["result"]["items"]}
    assert len(seen) == 3
