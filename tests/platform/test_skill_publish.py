"""P2-E4 Skill 发布两段式测试（10 §58.1–§58.4，14 号计划 §97.4）。

验收映射（§97.4 验收标准）：
- ⑤ 发布按 10 §58.4 两段式：PG 归属转换 + `platform_operation_refs
  (skill_publish)` + outbox → Worker 受控 `fs.mv` 迁移；任一步失败由
  Operation 状态机保护、重试幂等；
- ⑥ 发布后目录完整迁移、向量不重新 embedding、残留 owner 被清洗；
- ⑦ 普通 User 不能发布、Account Admin 可发布任意成员私有 Skill、
  PSA 不能发布；
- ⑧ 发布审计含 Actor/Subject/前后归属，不可取消。

发布 Worker 使用 SystemPrincipal（02 §7.1）消费 `skill.publish` outbox
事件；迁移走受控 `FakeSkillMigrationAdapter`（fs.mv 语义，与 P2-E1
FakeControlPlane 同模式，P5-E1 接线真实实现）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.models import IamAuditEvent, IamOutbox, PlatformOperationRef
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.skills.publish_worker import SkillPublishWorker
from openviking.server.platform.skills.service import EVENT_SKILL_PUBLISH
from tests.platform.helpers import build_auth_setup

BASE_AUTH = "/api/platform/v1/auth"
BASE_ME = "/api/platform/v1/me"
BASE_ADMIN = "/api/platform/v1/admin"

DEFAULT_PASSWORD = "Init-Pass-2026-Dev!"


async def _seed(session: AsyncSession):
    return await build_auth_setup(session)


async def _login(client: httpx.AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    r = await client.post(f"{BASE_AUTH}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["result"]["csrf_token"]


async def _create_private_skill(
    client: httpx.AsyncClient, csrf: str, name: str, content: str = "正文"
) -> str:
    r = await client.post(
        f"{BASE_ME}/skills",
        json={"name": name, "description": "待发布 Skill", "content": content},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]["id"]


async def _publish(client: httpx.AsyncClient, csrf: str, user_id: str, skill_id: str):
    return await client.post(
        f"{BASE_ADMIN}/users/{user_id}/skills/{skill_id}/publish",
        headers={"X-CSRF-Token": csrf},
    )


async def _run_publish_worker(platform_app, session: AsyncSession) -> int:
    """执行一轮发布 Worker；返回处理条数。"""
    service = platform_app.state.iam_skill_service
    worker = SkillPublishWorker(
        platform_app.state.iam_repository,
        platform_app.state.iam_skill_service._outbox,
        service,
    )
    processed = await worker.run_once(session)
    await session.commit()
    return processed


def _migration(platform_app) -> object:
    return platform_app.state.iam_skill_service._migration


async def _ref(session: AsyncSession, skill_id):
    return await RegistryRepository().get_ref(session, uuid.UUID(skill_id))


async def test_publish_two_stage_converts_and_migrates(
    session: AsyncSession, platform_client, platform_app
) -> None:
    """AC⑤⑥：PG 归属转换 → Worker 迁移（目录完整、向量保留、owner 清洗）。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "stage-skill")
    private_uri = "viking://user/ov_user_alice/skills/stage-skill"

    migration = _migration(platform_app)
    # 预置源目录 + 向量记录（.abstract.md/.overview.md/.source.json 等控制文件）
    migration.seed_content(
        private_uri,
        {
            "SKILL.md": b"---\nname: stage-skill\n---\n\nbody\n",
            "scripts/tool.py": b"print(1)",
            ".abstract.md": b"abstract",
            ".overview.md": b"overview",
            ".source.json": b"{}",
        },
    )
    migration.seed_vectors(
        [
            {"uri": private_uri, "id": "vec-1", "owner_user_id": "ov_user_alice", "vector_data": "v1"},
            {"uri": private_uri + "/scripts/tool.py", "id": "vec-2", "owner_user_id": "ov_user_alice", "vector_data": "v2"},
        ]
    )

    # 阶段 1：发布请求（归属转换 + operation + outbox）
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["visibility"] == "account_shared"
    operation_id = result["operation_id"]

    ref = await _ref(session, skill_id)
    assert ref.visibility == "account_shared"
    assert ref.owner_user_id is None
    assert ref.ov_uri == "viking://agent/skills/stage-skill"

    operation = (
        await session.execute(select(PlatformOperationRef).where(PlatformOperationRef.id == operation_id))
    ).scalar_one()
    assert operation.operation_kind == "task"
    assert operation.operation_type == "skill_publish"
    assert operation.target_id == skill_id
    assert operation.status == "pending"
    assert operation.cancellable is False  # 发布不可取消（10 §58.4 步骤 5）

    # 阶段 2：Worker 迁移
    processed = await _run_publish_worker(platform_app, session)
    assert processed == 1

    # 目录完整迁移：源删除、目标含全部文件（AC⑥）
    assert await migration.source_exists(private_uri) is False
    assert await migration.target_exists("viking://agent/skills/stage-skill") is True
    target_files = migration.content._dirs["viking://agent/skills/stage-skill"]
    assert set(target_files) == {
        "SKILL.md",
        "scripts/tool.py",
        ".abstract.md",
        ".overview.md",
        ".source.json",
    }

    # 向量不重新 embedding（id/vector_data 原样保留）+ URI 重写 + owner 清洗（AC⑥）
    vectors = migration.vectors
    by_uri = {v["uri"]: v for v in vectors}
    assert set(by_uri) == {
        "viking://agent/skills/stage-skill",
        "viking://agent/skills/stage-skill/scripts/tool.py",
    }
    assert by_uri["viking://agent/skills/stage-skill"]["id"] == "vec-1"
    assert by_uri["viking://agent/skills/stage-skill"]["vector_data"] == "v1"
    assert by_uri["viking://agent/skills/stage-skill"]["owner_user_id"] is None

    # Operation succeeded；Content Ref 保持 active
    operation = (
        await session.execute(select(PlatformOperationRef).where(PlatformOperationRef.id == operation_id))
    ).scalar_one()
    assert operation.status == "succeeded"
    ref = await _ref(session, skill_id)
    assert ref.status == "active"

    # 原私有区不保留副本：me 列表为空（10 §58.2 发布不是复制）
    r = await platform_client.get(f"{BASE_ME}/skills")
    assert r.json()["result"]["items"] == []


async def test_publish_audit_actor_subject_ownership(session: AsyncSession, platform_client, platform_app) -> None:
    """AC⑧：发布审计含 Actor/Subject/前后归属，不可取消。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "audit-skill")
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 200, r.text

    event = (
        await session.execute(
            select(IamAuditEvent)
            .where(IamAuditEvent.action == "skill.publish")
            .order_by(IamAuditEvent.occurred_at.desc())
        )
    ).scalars().first()
    assert event is not None
    assert event.actor_user_id == setup.admin.id          # Actor = Account Admin
    assert event.subject_user_id == setup.alice.id        # Subject = 所属 User
    assert event.target_id == skill_id
    assert event.target_visibility == "account_shared"
    assert event.metadata_json["visibility_from"] == "user_private"
    assert event.metadata_json["visibility_to"] == "account_shared"
    assert event.metadata_json["operation_id"]

    # 发布不可取消：operation cancellable=False（10 §58.4 步骤 5）
    operation_id = uuid.UUID(event.metadata_json["operation_id"])
    operation = (
        await session.execute(select(PlatformOperationRef).where(PlatformOperationRef.id == operation_id))
    ).scalar_one()
    assert operation.cancellable is False


async def test_publish_permissions_roles(session: AsyncSession, platform_client, platform_app) -> None:
    """AC⑦：普通 User 不能发布（即使自己的 Skill）；PSA 不能发布。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "role-skill")

    # 普通 User 发布自己的 Skill → 403（10 §58.1）
    r = await _publish(platform_client, csrf_alice, str(setup.alice.id), skill_id)
    assert r.status_code == 403

    # PSA 不能发布（即使目标 Account 内）→ 403（10 §58.1）
    csrf_psa = await _login(platform_client, "psa@platform.local")
    r = await _publish(platform_client, csrf_psa, str(setup.alice.id), skill_id)
    assert r.status_code == 403

    # Account Admin 发布成功（可发布任意成员私有 Skill）
    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 200, r.text

    # 已共享 Skill 不可重复发布 → 403
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 403

    # 跨 Account 发布目标 → 404（防 IDOR）
    other_account = await platform_app.state.iam_repository.create_account(
        session, ov_account_id="ov_account_y", code="ycorp", display_name="ycorp", status="active"
    )
    other_user = await platform_app.state.iam_repository.create_user(
        session,
        account_id=other_account.id,
        ov_user_id="ov_user_y1",
        username="y1",
        email="y1@ycorp.com",
        display_name="y1",
        password_hash="x",
        status="active",
    )
    await session.commit()
    r = await _publish(platform_client, csrf_admin, str(other_user.id), skill_id)
    assert r.status_code == 404


async def test_publish_disk_conflict_precheck(session: AsyncSession, platform_client, platform_app) -> None:
    """10 §58.4 步骤 1：磁盘侧目标共享根已有同名 → 发布请求 409 不转换。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "disk-conflict")
    # 磁盘上目标根已被占用（事务外变更场景）
    _migration(platform_app).seed_content("viking://agent/skills/disk-conflict", {"SKILL.md": b"other"})

    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SKILL_NAME_CONFLICT"
    assert "不可用" in r.json()["detail"]["message"]

    # PG 未转换（事务回滚）
    ref = await _ref(session, skill_id)
    assert ref.visibility == "user_private"
    assert ref.owner_user_id == setup.alice.id
    # 无 outbox 事件
    events = (
        await session.execute(select(IamOutbox).where(IamOutbox.event_type == EVENT_SKILL_PUBLISH))
    ).scalars().all()
    assert events == []


async def test_publish_failure_retry_idempotent(session: AsyncSession, platform_client, platform_app) -> None:
    """AC⑤：迁移失败 → Operation failed 可重试；重试成功且不产生重复对象。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "retry-skill")
    private_uri = "viking://user/ov_user_alice/skills/retry-skill"
    migration = _migration(platform_app)
    migration.seed_content(private_uri, {"SKILL.md": b"---\nname: retry-skill\n---\n\nbody\n"})
    migration.seed_vectors([{"uri": private_uri, "id": "vec-r", "owner_user_id": "ov_user_alice", "vector_data": "vr"}])

    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 200, r.text
    operation_id = r.json()["result"]["operation_id"]

    # 注入向量重写失败 → Worker 失败
    migration.fail_next("vector_rewrite")
    processed = await _run_publish_worker(platform_app, session)
    assert processed == 1

    operation = (
        await session.execute(select(PlatformOperationRef).where(PlatformOperationRef.id == operation_id))
    ).scalar_one()
    assert operation.status == "failed"
    assert operation.retryable is True
    assert operation.error_code == "SKILL_PUBLISH_MIGRATION_FAILED"

    # fs.mv 语义：向量重写失败 → 目标已复制内容被清理、源保持完整
    assert await migration.source_exists(private_uri) is True
    assert await migration.target_exists("viking://agent/skills/retry-skill") is False

    # outbox 事件 failed（退避到期后自动重试）
    event = (
        await session.execute(
            select(IamOutbox)
            .where(IamOutbox.event_type == EVENT_SKILL_PUBLISH)
            .order_by(IamOutbox.created_at.desc())
        )
    ).scalars().first()
    assert event.status == "failed"

    # 重试（把退避时间拨回过去）→ 成功；不产生重复目录/向量（AC⑤ 幂等）
    now = datetime.now(timezone.utc)
    event.next_attempt_at = now - timedelta(seconds=1)
    await session.commit()
    processed = await _run_publish_worker(platform_app, session)
    assert processed == 1

    operation = (
        await session.execute(select(PlatformOperationRef).where(PlatformOperationRef.id == operation_id))
    ).scalar_one()
    assert operation.status == "succeeded"
    assert await migration.target_exists("viking://agent/skills/retry-skill") is True
    assert await migration.source_exists(private_uri) is False
    # 向量仅一条且已迁移 + owner 清洗（重试不重复）
    vectors = migration.vectors
    assert len(vectors) == 1
    assert vectors[0]["uri"] == "viking://agent/skills/retry-skill"
    assert vectors[0]["owner_user_id"] is None


async def test_publish_source_missing_idempotent(session: AsyncSession, platform_client, platform_app) -> None:
    """10 §58.4 步骤 4：Worker 迁移时源缺失（fs.mv 幂等）→ 只清理孤儿索引。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "missing-source")
    migration = _migration(platform_app)
    # 模拟磁盘侧源目录已丢失（事务外清理），只留孤儿向量残留索引
    migration.content._dirs.pop("viking://user/ov_user_alice/skills/missing-source", None)
    migration.seed_vectors([{"uri": "viking://user/ov_user_alice/skills/missing-source", "id": "vec-o", "owner_user_id": "ov_user_alice", "vector_data": "vo"}])

    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 200, r.text
    operation_id = r.json()["result"]["operation_id"]

    processed = await _run_publish_worker(platform_app, session)
    assert processed == 1

    # 孤儿索引被清理；Operation succeeded；不产生重复对象
    assert migration.vectors == []
    operation = (
        await session.execute(select(PlatformOperationRef).where(PlatformOperationRef.id == operation_id))
    ).scalar_one()
    assert operation.status == "succeeded"


async def test_publish_worker_target_conflict_not_retryable(
    session: AsyncSession, platform_client, platform_app
) -> None:
    """Worker 复查目标共享根被占用 → Operation failed 不可重试。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "late-conflict")
    migration = _migration(platform_app)
    migration.seed_content(
        "viking://user/ov_user_alice/skills/late-conflict", {"SKILL.md": b"---\nname: late-conflict\n---\n\nbody\n"}
    )

    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 200, r.text
    operation_id = r.json()["result"]["operation_id"]

    # 请求后、Worker 前目标被外部占用
    migration.seed_content("viking://agent/skills/late-conflict", {"SKILL.md": b"occupied"})

    processed = await _run_publish_worker(platform_app, session)
    assert processed == 1
    operation = (
        await session.execute(select(PlatformOperationRef).where(PlatformOperationRef.id == operation_id))
    ).scalar_one()
    assert operation.status == "failed"
    assert operation.retryable is False
    assert operation.error_code == "SKILL_PUBLISH_TARGET_CONFLICT"
    # 源保持完整（未迁移）
    assert await migration.source_exists("viking://user/ov_user_alice/skills/late-conflict") is True


async def test_publish_system_audit_worker(session: AsyncSession, platform_client, platform_app) -> None:
    """发布 Worker 系统审计：actor_type=system + 组件名（04 §10.8）。"""
    setup = await _seed(session)
    csrf_alice = await _login(platform_client, "alice@acme.com")
    skill_id = await _create_private_skill(platform_client, csrf_alice, "sys-audit")
    private_uri = "viking://user/ov_user_alice/skills/sys-audit"
    migration = _migration(platform_app)
    migration.seed_content(private_uri, {"SKILL.md": b"---\nname: sys-audit\n---\n\nbody\n"})

    csrf_admin = await _login(platform_client, "admin@acme.com")
    r = await _publish(platform_client, csrf_admin, str(setup.alice.id), skill_id)
    assert r.status_code == 200, r.text

    await _run_publish_worker(platform_app, session)
    event = (
        await session.execute(
            select(IamAuditEvent)
            .where(IamAuditEvent.action == "skill.publish.migrate")
            .order_by(IamAuditEvent.occurred_at.desc())
        )
    ).scalars().first()
    assert event is not None
    assert event.actor_type == "system"
    assert event.actor_system_component == "skill.publish.worker"
    assert event.result == "success"
    assert event.target_id == skill_id
