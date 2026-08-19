"""P2-E4 me/skill-configs 测试（05 §12.5 三接口，10 §53.3，08 §28.1）。

验收映射（§97.4 验收标准⑨）：
- skill-configs 仅当前 User 读写（私有/共享 Skill 均可，跨 User 隔离）；
- 脱敏存储不返回可恢复 Secret（DTO 只含掩码值）；
- 历史版本可激活（GET/PUT /me/skill-configs/{skill_id}、
  /versions/{version}/activate）。

存储走受控 `FakeSkillConfigsAdapter`（对应 `UserPrivacyConfigService`
upsert/activate_version 语义，P5-E1 接线真实实现）。
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.platform.helpers import build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE_ME = "/api/platform/v1/me"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"
SECRET_VALUE = "sk-live-very-secret-token-1234567890"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _create_skill(client: httpx.AsyncClient, csrf: str, name: str) -> str:
    r = await client.post(
        f"{BASE_ME}/skills",
        json={"name": name, "description": "配置目标 Skill", "content": "正文"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]["id"]


async def test_config_put_get_masked_activate(session: AsyncSession, platform_client, platform_app) -> None:
    """AC⑨：写入新版本 → 脱敏读取 → 激活历史版本。"""
    await _seed(session)
    csrf = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_skill(platform_client, csrf, "config-skill")

    # 未配置时：configured=False，无 Secret
    r = await platform_client.get(f"{BASE_ME}/skill-configs/{skill_id}")
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["configured"] is False
    assert body["values"] == []

    # 写入 v1（含 Secret）
    r = await platform_client.put(
        f"{BASE_ME}/skill-configs/{skill_id}",
        json={"values": {"api_key": SECRET_VALUE, "org": "acme"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["configured"] is True
    assert body["active_version"] == 1
    assert body["latest_version"] == 1
    assert body["versions"] == [1]

    # 脱敏读取：掩码值不含可恢复 Secret（AC⑨）
    r = await platform_client.get(f"{BASE_ME}/skill-configs/{skill_id}")
    assert r.status_code == 200
    body = r.json()["result"]
    values = {v["key"]: v["value_masked"] for v in body["values"]}
    assert set(values) == {"api_key", "org"}
    assert values["api_key"] == "••••••••"
    assert values["org"] == "••••••••"
    assert SECRET_VALUE not in r.text
    assert SECRET_VALUE[:8] not in r.text

    # 写入 v2；历史版本可激活（AC⑨）
    r = await platform_client.put(
        f"{BASE_ME}/skill-configs/{skill_id}",
        json={"values": {"api_key": "second-secret-9876", "region": "cn"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.json()["result"]["latest_version"] == 2
    assert r.json()["result"]["versions"] == [1, 2]

    r = await platform_client.post(
        f"{BASE_ME}/skill-configs/{skill_id}/versions/1/activate",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["active_version"] == 1
    assert "second-secret-9876" not in r.text

    # 不存在的版本 → 404
    r = await platform_client.post(
        f"{BASE_ME}/skill-configs/{skill_id}/versions/99/activate",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


async def test_config_isolation_between_users(session: AsyncSession, platform_client, platform_app) -> None:
    """AC⑨：同一共享 Skill 上不同 User 的配置相互隔离（仅当前 User 读写）。"""
    await _seed(session)
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.post(
        "/api/platform/v1/account/skills",
        json={"name": "shared-config-skill", "description": "共享 Skill", "content": "正文"},
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 200, r.text
    shared_id = r.json()["result"]["id"]

    # alice 写入自己的配置
    csrf_alice = await _login(platform_client, "alice@acme.com")
    r = await platform_client.put(
        f"{BASE_ME}/skill-configs/{shared_id}",
        json={"values": {"alice_secret": SECRET_VALUE}},
        headers={"X-CSRF-Token": csrf_alice},
    )
    assert r.status_code == 200, r.text

    # admin（重新登录后）读取的是自己的配置（未配置），看不到 alice 的 Secret
    await _login(platform_client, "admin@acme.com")
    r = await platform_client.get(f"{BASE_ME}/skill-configs/{shared_id}")
    assert r.status_code == 200
    assert r.json()["result"]["configured"] is False
    assert SECRET_VALUE not in r.text

    # alice 自己仍可读取自己的配置（脱敏）
    await _login(platform_client, "alice@acme.com")
    r = await platform_client.get(f"{BASE_ME}/skill-configs/{shared_id}")
    assert r.status_code == 200
    assert r.json()["result"]["configured"] is True
    assert SECRET_VALUE not in r.text

    # 不存在的 Skill 配置目标 → 404（防 IDOR）
    import uuid

    r = await platform_client.get(f"{BASE_ME}/skill-configs/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_config_requires_skill_read_visibility(session: AsyncSession, platform_client, platform_app) -> None:
    """配置目标必须对当前 User 可见（私有 Skill 只允许属主本人）。"""
    await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_skill(platform_client, csrf_alice, "private-config-skill")

    # 其他 User 访问 alice 的私有 Skill 配置 → 404（不可见语义）
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await platform_client.get(f"{BASE_ME}/skill-configs/{skill_id}")
    assert r.status_code == 404
    r = await platform_client.put(
        f"{BASE_ME}/skill-configs/{skill_id}",
        json={"values": {"k": "v"}},
        headers={"X-CSRF-Token": csrf_admin},
    )
    assert r.status_code == 404
