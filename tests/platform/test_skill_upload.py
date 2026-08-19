"""P2-E4 收尾：ZIP/SKILL.md 上传与 P2-E3 Upload 契约联合验证（10 §61.5）。

覆盖（14 号计划 §97.4 story「ZIP/SKILL.md 上传与 P2-E3 联合验证」）：

- 端到端链路：`me/resource-uploads`/`account/resource-uploads`（P2-E3）
  上传 → upload_id → `/me/skills`/`/account/skills` 消费创建/整体替换，
  无独立 skill-uploads 端点（AC：上传走 upload_id 链路）；
- 上传消费原子性（04 §10.14，AC⑦）：ready → consumed、跨 User/Scope
  消费、过期、重放统一拒绝（UPLOAD_SCOPE_MISMATCH/EXPIRED/CONSUMED）；
- ZIP 安全校验（10 §54.2）：路径穿越/绝对路径/符号链接/控制字符/
  文件数量/单文件大小/解压总量/加密与损坏成员全部拒绝，且校验失败
  不占用名称（10 §61.5：重传合法包可再建同名 Skill）；
- 整体替换 PUT：新包 name 必须一致（AC②，ZIP 路径 SKILL_NAME_IMMUTABLE），
  失败后无残留 pending Operation、对象与内容不变。
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import PlatformOperationRef, PlatformUpload
from tests.platform.helpers import build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE_ME = "/api/platform/v1/me"
BASE_ACCOUNT = "/api/platform/v1/account"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _http_upload(
    client: httpx.AsyncClient,
    csrf: str,
    *,
    path: str,
    data: bytes,
    filename: str,
    mime: str,
) -> uuid.UUID:
    """走真实 P2-E3 上传端点（04 §10.14：服务端检测大小/Magic/MIME）。"""
    r = await client.post(
        path,
        files={"file": (filename, data, mime)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["result"]["upload_id"])


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


def _make_zip(name: str, *, bad_member: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", _make_skill_md(name))
        zf.writestr("scripts/run.py", "print('hello')\n")
        if bad_member is not None:
            zf.writestr(bad_member, "evil")
    return buf.getvalue()


async def _pending_operation_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(PlatformOperationRef).where(
                    PlatformOperationRef.status == "pending"
                )
            )
        ).scalar_one()
    )


# ═══════════════════════════════════════════════════════════════════════
# 端到端：上传 → 消费 → 创建 / 替换（10 §61.5，无独立 skill-uploads 端点）
# ═══════════════════════════════════════════════════════════════════════


async def test_end_to_end_skill_md_upload_via_http(
    session: AsyncSession, platform_client, platform_app
) -> None:
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")

    upload_id = await _http_upload(
        platform_client,
        csrf,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_skill_md("md-e2e").encode("utf-8"),
        filename="SKILL.md",
        mime="text/markdown",
    )
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "md-e2e", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["name"] == "md-e2e"
    assert body["source_type"] == "skill_md"

    # 已消费 Upload 重放 → 400 UPLOAD_CONSUMED（04 §10.14 原子性）
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "md-e2e-2", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "UPLOAD_CONSUMED"

    # 详情可读；无 pending Operation 残留
    detail = await platform_client.get(f"{BASE_ME}/skills/{body['id']}")
    assert detail.status_code == 200
    assert await _pending_operation_count(session) == 0


async def test_end_to_end_zip_create_and_replace_via_http(
    session: AsyncSession, platform_client, platform_app
) -> None:
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")

    # ZIP 上传创建
    upload_id = await _http_upload(
        platform_client,
        csrf,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_zip("zip-e2e"),
        filename="skill.zip",
        mime="application/zip",
    )
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "zip-e2e", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    skill_id = body["id"]
    assert body["source_type"] == "zip"
    assert body["has_auxiliary_files"] is True

    # 整体替换：新包 name 不一致 → 409 SKILL_NAME_IMMUTABLE（AC②，ZIP 路径）
    upload_id = await _http_upload(
        platform_client,
        csrf,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_zip("renamed-package"),
        filename="skill.zip",
        mime="application/zip",
    )
    r = await platform_client.put(
        f"{BASE_ME}/skills/{skill_id}",
        json={"upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "SKILL_NAME_IMMUTABLE"
    # 失败路径整体回滚（get_session 异常即 rollback）：无 pending Operation、
    # Skill 保持原状、Upload 未被烧毁（重放同一 upload_id 依旧只按名称
    # 不一致拒绝，不会产生第二对象）
    assert await _pending_operation_count(session) == 0
    r = await platform_client.put(
        f"{BASE_ME}/skills/{skill_id}",
        json={"upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "SKILL_NAME_IMMUTABLE"
    detail = await platform_client.get(f"{BASE_ME}/skills/{skill_id}")
    assert detail.status_code == 200
    assert detail.json()["result"]["name"] == "zip-e2e"

    # 新包 name 一致 → 整体替换成功
    upload_id = await _http_upload(
        platform_client,
        csrf,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_zip("zip-e2e"),
        filename="skill.zip",
        mime="application/zip",
    )
    r = await platform_client.put(
        f"{BASE_ME}/skills/{skill_id}",
        json={"upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["name"] == "zip-e2e"
    assert await _pending_operation_count(session) == 0


async def test_account_scope_zip_upload_shared_skill(
    session: AsyncSession, platform_client, platform_app
) -> None:
    await _seed(session)
    csrf_admin = await _login(platform_client, "admin@acme.com")

    upload_id = await _http_upload(
        platform_client,
        csrf_admin,
        path=f"{BASE_ACCOUNT}/resource-uploads",
        data=_make_zip("shared-zip"),
        filename="skill.zip",
        mime="application/zip",
    )
    r = await platform_client.post(
        f"{BASE_ACCOUNT}/skills",
        json={"name": "shared-zip", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["visibility"] == "account_shared"
    assert body["source_type"] == "zip"

    # 普通 User 只读可见（10 §61.2）
    await _login(platform_client, "alice@acme.com")
    r = await platform_client.get(f"{BASE_ACCOUNT}/skills")
    assert r.status_code == 200
    assert [s["id"] for s in r.json()["result"]["items"]] == [body["id"]]


# ═══════════════════════════════════════════════════════════════════════
# 消费原子性（04 §10.14，AC⑦：跨 Scope/过期/重放拒绝）
# ═══════════════════════════════════════════════════════════════════════


async def test_upload_scope_atomicity_matrix(session: AsyncSession, platform_client, platform_app) -> None:
    await _seed(session)

    # 私有 Upload 被其他 User 消费 → UPLOAD_SCOPE_MISMATCH
    csrf_alice = await _login(platform_client, "alice@acme.com")
    upload_id = await _http_upload(
        platform_client,
        csrf_alice,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_skill_md("cross-user").encode("utf-8"),
        filename="SKILL.md",
        mime="text/markdown",
    )
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "cross-user", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "UPLOAD_SCOPE_MISMATCH"

    # 私有 Upload 在共享入口消费 → UPLOAD_SCOPE_MISMATCH（目标可见性不符）
    csrf_alice = await _login(platform_client, "alice@acme.com")
    upload_id = await _http_upload(
        platform_client,
        csrf_alice,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_skill_md("vis-mismatch").encode("utf-8"),
        filename="SKILL.md",
        mime="text/markdown",
    )
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        f"{BASE_ACCOUNT}/skills",
        json={"name": "vis-mismatch", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "UPLOAD_SCOPE_MISMATCH"

    # 共享 Upload 在私有入口消费 → UPLOAD_SCOPE_MISMATCH
    upload_id = await _http_upload(
        platform_client,
        csrf_admin,
        path=f"{BASE_ACCOUNT}/resource-uploads",
        data=_make_skill_md("shared-into-private").encode("utf-8"),
        filename="SKILL.md",
        mime="text/markdown",
    )
    csrf_alice = await _login(platform_client, "alice@acme.com")
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "shared-into-private", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "UPLOAD_SCOPE_MISMATCH"

    # 未知 upload_id → 404 UPLOAD_NOT_FOUND
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "ghost", "upload_id": str(uuid.uuid4())},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "UPLOAD_NOT_FOUND"

    # 过期 Upload → 400 UPLOAD_EXPIRED（回拨 expires_at）
    upload_id = await _http_upload(
        platform_client,
        csrf_alice,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_skill_md("expired").encode("utf-8"),
        filename="SKILL.md",
        mime="text/markdown",
    )
    upload_row = await session.get(PlatformUpload, upload_id)
    upload_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "expired", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "UPLOAD_EXPIRED"

    # 全部拒绝路径无 pending Operation 残留
    assert await _pending_operation_count(session) == 0


# ═══════════════════════════════════════════════════════════════════════
# ZIP 安全校验矩阵（10 §54.2：服务端强制；校验失败不占用名称，10 §61.5）
# ═══════════════════════════════════════════════════════════════════════


def _zip_with_symlink() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", _make_skill_md("symlink-case"))
        info = zipfile.ZipInfo("link.md")
        info.create_system = 3
        info.external_attr = 0o120777 << 16  # symlink
        zf.writestr(info, "SKILL.md")
    return buf.getvalue()


def _zip_many_entries(count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(count):
            zf.writestr("SKILL.md" if i == 0 else f"f{i}.txt", _make_skill_md("limit-case") if i == 0 else "x")
    return buf.getvalue()


def _zip_oversize_entry() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", b"\x00" * (4 * 1024 * 1024 + 1))
    return buf.getvalue()


def _zip_oversize_total() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(9):
            zf.writestr("SKILL.md" if i == 0 else f"big{i}.bin", b"\x00" * (4 * 1024 * 1024))
    return buf.getvalue()


def _zip_two_top_dirs() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("d1/SKILL.md", _make_skill_md("dirs-case"))
        zf.writestr("d2/other.md", "x")
    return buf.getvalue()


def _zip_encrypted() -> bytes:
    """手工构造加密标记 ZIP（stdlib 只读加密、不可写；只补中央目录
    flag 位，读取方按中央目录判断加密，04 §10.14：不受信任内容拒绝）。"""
    import struct

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", _make_skill_md("encrypted-case"))
    data = bytearray(buf.getvalue())
    # EOCD：尾部 22+ 字节，中央目录偏移位于 +16
    eocd = data.rfind(b"PK\x05\x06")
    cd_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    pos = cd_offset
    while data[pos:pos + 4] == b"PK\x01\x02":  # 中央目录头：flag 位于 +8
        struct.pack_into("<H", data, pos + 8, struct.unpack_from("<H", data, pos + 8)[0] | 1)
        name_len = struct.unpack_from("<H", data, pos + 28)[0]
        extra_len = struct.unpack_from("<H", data, pos + 30)[0]
        comment_len = struct.unpack_from("<H", data, pos + 32)[0]
        pos += 46 + name_len + extra_len + comment_len
    return bytes(data)


async def test_zip_safety_validation_matrix(
    session: AsyncSession, platform_client, platform_app
) -> None:
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")

    traversal_cases = [
        "../evil.md",
        "a/../../evil.md",
        "/abs/path.md",
        r"..\evil.md",
        "C:/evil.md",
        "bad\x01name.md",
    ]
    safety_cases: list[tuple[bytes, str]] = [
        (_make_zip("s1", bad_member=member), f"traverse-{i}") for i, member in enumerate(traversal_cases)
    ]
    safety_cases += [
        (_zip_with_symlink(), "symlink-case"),
        (_zip_many_entries(201), "entries-case"),
        (_zip_oversize_entry(), "entry-size-case"),
        (_zip_oversize_total(), "total-size-case"),
        (_zip_two_top_dirs(), "dirs-case"),
        (_zip_encrypted(), "encrypted-case"),
        (b"PK\x03\x04not-a-real-zip-body", "corrupt-case"),
        (_make_zip("x")[:80], "truncated-case"),
        (_make_zip("x")[:0], "empty-case"),
    ]
    for index, (data, name) in enumerate(safety_cases):
        upload_id = await _http_upload(
            platform_client,
            csrf,
            path=f"{BASE_ME}/resource-uploads",
            data=data,
            filename="skill.zip",
            mime="application/zip",
        )
        r = await platform_client.post(
            f"{BASE_ME}/skills",
            json={"name": name, "upload_id": str(upload_id)},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 400, (index, name, r.text)
        assert r.json()["detail"]["code"] == "SKILL_INVALID_FORMAT", (index, name, r.text)

        # 10 §61.5：校验失败不占用名称——同名合法包可再次创建
        retry_upload = await _http_upload(
            platform_client,
            csrf,
            path=f"{BASE_ME}/resource-uploads",
            data=_make_skill_md(name).encode("utf-8"),
            filename="SKILL.md",
            mime="text/markdown",
        )
        r = await platform_client.post(
            f"{BASE_ME}/skills",
            json={"name": name, "upload_id": str(retry_upload)},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, (index, name, r.text)

    # 无 pending Operation 残留
    assert await _pending_operation_count(session) == 0


async def test_create_package_name_mismatch_releases_name(
    session: AsyncSession, platform_client, platform_app
) -> None:
    """创建请求 name 与包内 name 不一致 → SKILL_INVALID_FORMAT 且不占用名称。"""
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")

    upload_id = await _http_upload(
        platform_client,
        csrf,
        path=f"{BASE_ME}/resource-uploads",
        data=_make_skill_md("real-name").encode("utf-8"),
        filename="SKILL.md",
        mime="text/markdown",
    )
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "requested-name", "upload_id": str(upload_id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "SKILL_INVALID_FORMAT"

    # 请求名未被占用：直接以 requested-name 在线创建成功
    r = await platform_client.post(
        f"{BASE_ME}/skills",
        json={"name": "requested-name", "description": "desc", "content": "body"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["name"] == "requested-name"
    assert await _pending_operation_count(session) == 0
