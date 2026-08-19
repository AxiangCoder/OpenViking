"""P2-E2 Content Registry 测试（04 §10.10/§10.12/§10.14，14 号计划 §97.2）。

验收映射：
- AC⑤：对外创建先 `platform_content_refs(provisioning)` 再调 OpenViking
  （begin_create → activate/fail），列表只返回 active；
- AC⑥：Skill 部分唯一约束生效（服务层预检 + DB 部分唯一索引兜底），冲突
  响应不泄露占用者；
- AC⑦：Idempotency-Key 重复提交不产生重复对象；Upload 原子 ready→consumed
  与跨 Scope 拒绝；
- 迁移：platform_* 三张新表创建成功、Skill 部分唯一索引存在、downgrade 干净。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openviking.server.platform.errors import (
    ConstraintViolationError,
    EntityNotFoundError,
    UploadNotConsumableError,
)
from openviking.server.platform.models import (
    PlatformContentRef,
    PlatformOperationRef,
)
from openviking.server.platform.registry.repository import RegistryRepository
from openviking.server.platform.registry.service import (
    EVENT_CONTENT_ACTIVATED,
    EVENT_TAGS_SYNC,
    SKILL_NAME_UNAVAILABLE,
    ContentRegistryService,
)
from openviking.server.platform.registry.tags import TagValidationError, normalize_tags
from tests.platform.helpers import build_auth_setup


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── tags 校验（04 §10.10，05 §12.5）──


async def test_tags_normalization() -> None:
    assert normalize_tags(["  Alpha=1 ", "beta=2", "BETA=2", "a=b"]) == [
        "alpha=1",
        "beta=2",
        "a=b",
    ]
    assert normalize_tags(None) == []
    assert normalize_tags([]) == []


async def test_tags_validation_errors() -> None:
    with pytest.raises(TagValidationError) as e:
        normalize_tags([f"k{i}=v" for i in range(21)])
    assert e.value.reason == "TAG_LIMIT_EXCEEDED"
    with pytest.raises(TagValidationError) as e:
        normalize_tags(["nokey"])
    assert e.value.reason == "TAG_INVALID_FORMAT"
    with pytest.raises(TagValidationError) as e:
        normalize_tags(["k="])
    assert e.value.reason == "TAG_INVALID_FORMAT"
    with pytest.raises(TagValidationError) as e:
        normalize_tags(["key=" + "x" * 41])
    assert e.value.reason == "TAG_TOO_LONG"


# ── AC⑤：provisioning → active/failed，列表只返回 active ──


async def test_create_activate_fail_lifecycle(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()

    ref = await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="resource",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/resources/foo.md",
        display_name="Foo",
        tags=[" alpha=1 "],
        idempotency_key="idem-1",
    )
    # 对外创建先 provisioning（AC⑤）
    assert ref.status == "provisioning"
    assert ref.tags == ["alpha=1"]
    assert ref.idempotency_key == "idem-1"
    await session.commit()

    # 未 activate 前列表为空（只返回 active，AC⑤）
    active = await registry.list_active(
        session, account_id=setup.acme.id, object_type="resource", visibility="user_private"
    )
    assert active == []

    # OpenViking 调用成功后 → active + 事件
    activated = await registry.activate(
        session, ref.id, actor_user_id=setup.alice.id, generation=1, now=_now()
    )
    assert activated.status == "active"
    assert activated.active_generation == 1
    await session.commit()

    active = await registry.list_active(
        session, account_id=setup.acme.id, object_type="resource", visibility="user_private"
    )
    assert [r.id for r in active] == [ref.id]

    # 失败 → failed；列表仍不出现
    ref2 = await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="resource",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/resources/bar.md",
        display_name="Bar",
    )
    await registry.fail(session, ref2.id)
    await session.commit()
    active = await registry.list_active(
        session, account_id=setup.acme.id, object_type="resource", visibility="user_private"
    )
    assert len(active) == 1
    assert active[0].id == ref.id


async def test_get_active_ref_rejects_non_active(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    ref = await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="resource",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/resources/x.md",
    )
    with pytest.raises(EntityNotFoundError):
        await registry.get_active_ref(session, ref.id)


# ── AC⑥：Skill 名称唯一（服务层 + DB 部分唯一索引，不泄露占用者）──


async def test_skill_name_conflict_service_level(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="skill",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/skills/analyzer/",
        canonical_name="analyzer",
    )
    await session.commit()
    with pytest.raises(ConstraintViolationError) as excinfo:
        await registry.begin_create(
            session,
            account_id=setup.acme.id,
            object_type="skill",
            visibility="account_shared",
            owner_user_id=None,
            actor_user_id=setup.alice.id,
            ov_uri="viking://agent/skills/analyzer/",
            canonical_name="analyzer",
        )
    # 冲突响应只说明名称不可用，不泄露占用者（AC⑥）
    assert str(excinfo.value) == SKILL_NAME_UNAVAILABLE
    assert "alice" not in str(excinfo.value).lower()


async def test_skill_name_partial_unique_index_db_enforced(session: AsyncSession) -> None:
    """AC⑥：DB 部分唯一索引兜底（(account_id, canonical_name) WHERE
    object_type='skill' AND deleted_at IS NULL）。"""
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    acme_id = setup.acme.id  # 回滚会过期 ORM 实例，先固化引用
    alice_id = setup.alice.id
    ref = await registry.begin_create(
        session,
        account_id=acme_id,
        object_type="skill",
        visibility="user_private",
        owner_user_id=alice_id,
        actor_user_id=alice_id,
        ov_uri="viking://user/ov_user_alice/skills/analyzer/",
        canonical_name="analyzer",
    )
    await session.commit()  # 先提交，使回滚只影响 dup，不带走首条引用
    ref_id = ref.id  # 回滚会过期 ORM 实例，先固化主键
    # 直写同 Account 同名称第二个 Skill（绕过服务层预检）→ DB 唯一约束拒绝
    dup = PlatformContentRef(
        account_id=acme_id,
        object_type="skill",
        visibility="account_shared",
        owner_user_id=None,
        ov_uri="viking://agent/skills/analyzer/",
        canonical_name="analyzer",
        display_name="analyzer",
        status="provisioning",
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    # 软删除后名称可复用（04 §10.10：软删除记录不参与唯一约束）
    store = RegistryRepository()
    ref = await store.get_ref(session, ref_id)
    assert ref is not None
    ref.deleted_at = _now()
    ref.status = "deleted"
    await session.commit()
    again = await registry.begin_create(
        session,
        account_id=acme_id,
        object_type="skill",
        visibility="account_shared",
        owner_user_id=None,
        actor_user_id=alice_id,
        ov_uri="viking://agent/skills/analyzer/",
        canonical_name="analyzer",
    )
    assert again.id != ref.id
    await session.commit()


async def test_skill_partial_unique_index_exists(session: AsyncSession) -> None:
    """AC⑥：迁移创建的部分唯一索引存在（object_type='skill' AND deleted_at IS NULL）。"""
    result = await session.execute(
        text(
            "select indexdef from pg_indexes "
            "where tablename='platform_content_refs' and indexname='uq_content_refs_skill_name_active'"
        )
    )
    indexdef = result.scalar()
    assert indexdef is not None
    assert "uq_content_refs_skill_name_active" in indexdef
    assert "'skill'" in indexdef and "deleted_at IS NULL" in indexdef


# ── AC⑦：Idempotency-Key 重复提交不产生重复对象 ──


async def test_idempotency_key_replay_returns_same_ref(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    first = await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="resource",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/resources/foo.md",
        display_name="Foo",
        idempotency_key="idem-abc",
    )
    await session.commit()
    replay = await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="resource",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/resources/foo.md",
        display_name="Foo",
        idempotency_key="idem-abc",
    )
    assert replay.id == first.id
    rows = await registry.list_active(
        session, account_id=setup.acme.id, object_type="resource", visibility="user_private"
    )
    assert len(rows) == 0  # 尚未 activate，仍只有一条 provisioning
    await session.commit()
    # 全表只有一条引用（不产生重复对象，AC⑦）
    from sqlalchemy import select

    all_refs = list((await session.execute(select(PlatformContentRef))).scalars())
    assert len(all_refs) == 1


async def test_activate_outbox_events_written(session: AsyncSession) -> None:
    """activate → content.activated outbox；tags 同步走 search_tags Outbox（04 §10.10）。"""
    from sqlalchemy import select

    from openviking.server.platform.models import IamOutbox

    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    ref = await registry.begin_create(
        session,
        account_id=setup.acme.id,
        object_type="resource",
        visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        ov_uri="viking://user/ov_user_alice/resources/foo.md",
        tags=["x=1"],
    )
    await registry.activate(session, ref.id, actor_user_id=setup.alice.id)
    await registry.sync_tags_outbox(session, ref_id=ref.id, tags=["x=1"])
    await session.commit()

    events = list((await session.execute(select(IamOutbox))).scalars())
    event_types = {e.event_type for e in events}
    assert EVENT_CONTENT_ACTIVATED in event_types
    assert EVENT_TAGS_SYNC in event_types


# ── platform_operation_refs（04 §10.12）──


async def test_operation_ref_generation_and_status(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    op = await registry.create_operation(
        session,
        account_id=setup.acme.id,
        operation_kind="task",
        operation_type="resource_import",
        ov_operation_id="ov-task-1",
        target_type="resource",
        target_id=str(uuid.uuid4()),
        target_visibility="user_private",
        owner_user_id=setup.alice.id,
        actor_user_id=setup.alice.id,
        generation=1,
        cancellable=True,
    )
    assert op.status == "pending" and op.generation == 1
    finished = await registry.finish_operation(
        session, op.id, status="succeeded", stage="finalizing", now=_now()
    )
    assert finished.status == "succeeded" and finished.completed_at is not None
    # (account_id, operation_kind, ov_operation_id) 唯一
    await registry.create_operation(
        session,
        account_id=setup.acme.id,
        operation_kind="task",
        operation_type="resource_import",
        ov_operation_id="ov-task-2",
        target_type="resource",
        target_id=str(uuid.uuid4()),
        target_visibility="account_shared",
        owner_user_id=None,
        actor_user_id=setup.alice.id,
    )
    await session.commit()
    dup = PlatformOperationRef(
        account_id=setup.acme.id,
        operation_kind="task",
        operation_type="resource_import",
        ov_operation_id="ov-task-1",
        target_visibility="account_shared",
        status="pending",
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


# ── platform_uploads（04 §10.14：15 分钟 / 原子消费）──


async def test_upload_created_with_default_15min_ttl(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    upload = await registry.create_upload(
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        target_visibility="user_private",
        owner_user_id=setup.alice.id,
        object_type="resource",
        storage_ref="tmp/obj-1",
        original_filename="doc.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
    )
    assert upload.status == "ready"
    assert upload.expires_at <= _now() + timedelta(minutes=16)
    assert upload.expires_at > _now() + timedelta(minutes=14)


async def test_upload_atomic_consume_and_scope_rejection(session: AsyncSession) -> None:
    """AC⑦ + 04 §10.14：原子 ready→consumed；跨 Scope/过期/已消费拒绝。"""
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    operation_id = uuid.uuid4()
    upload = await registry.create_upload(
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        target_visibility="user_private",
        owner_user_id=setup.alice.id,
        object_type="resource",
        storage_ref="tmp/obj-2",
    )
    await session.commit()

    # 跨 Visibility 消费拒绝
    with pytest.raises(UploadNotConsumableError) as e:
        await registry.consume_upload(
            session,
            upload_id=upload.id,
            account_id=setup.acme.id,
            actor_user_id=setup.alice.id,
            visibility="account_shared",
            object_type="resource",
            operation_id=operation_id,
        )
    assert e.value.reason == "UPLOAD_SCOPE_MISMATCH"

    # 其他 Actor 消费私有 Upload 拒绝
    with pytest.raises(UploadNotConsumableError) as e:
        await registry.consume_upload(
            session,
            upload_id=upload.id,
            account_id=setup.acme.id,
            actor_user_id=setup.admin.id,
            visibility="user_private",
            object_type="resource",
            operation_id=operation_id,
        )
    assert e.value.reason == "UPLOAD_SCOPE_MISMATCH"

    # 正常消费 → ready→consumed（原子）
    consumed = await registry.consume_upload(
        session,
        upload_id=upload.id,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        visibility="user_private",
        object_type="resource",
        operation_id=operation_id,
    )
    assert consumed.status == "consumed"
    assert consumed.consumed_by_operation_id == operation_id
    await session.commit()

    # 重复消费拒绝（不产生第二次结果）
    with pytest.raises(UploadNotConsumableError) as e:
        await registry.consume_upload(
            session,
            upload_id=upload.id,
            account_id=setup.acme.id,
            actor_user_id=setup.alice.id,
            visibility="user_private",
            object_type="resource",
            operation_id=uuid.uuid4(),
        )
    assert e.value.reason == "UPLOAD_CONSUMED"


async def test_upload_expired_and_expire_worker(session: AsyncSession) -> None:
    setup = await build_auth_setup(session)
    registry = ContentRegistryService()
    upload = await registry.create_upload(
        session,
        account_id=setup.acme.id,
        actor_user_id=setup.alice.id,
        target_visibility="user_private",
        owner_user_id=setup.alice.id,
        object_type="resource",
        storage_ref="tmp/obj-3",
        ttl_minutes=1,
    )
    await session.commit()
    future = _now() + timedelta(minutes=2)
    with pytest.raises(UploadNotConsumableError) as e:
        await registry.consume_upload(
            session,
            upload_id=upload.id,
            account_id=setup.acme.id,
            actor_user_id=setup.alice.id,
            visibility="user_private",
            object_type="resource",
            operation_id=uuid.uuid4(),
            now=future,
        )
    assert e.value.reason == "UPLOAD_EXPIRED"
    assert await registry.expire_uploads(session, now=future) == 1


# ── 迁移验收（04 §10.10/§10.12/§10.14）──


async def test_platform_tables_created_and_downgrade_clean(session: AsyncSession, test_database: str) -> None:
    """platform_* 表创建成功（P2-E2 四张 + P2-E3 watch）；downgrade 后无残留。"""
    result = await session.execute(
        text(
            "select table_name from information_schema.tables "
            "where table_schema='public' and table_name like 'platform_%' order by table_name"
        )
    )
    assert {row[0] for row in result} == {
        "platform_content_refs",
        "platform_operation_refs",
        "platform_uploads",
        "platform_resource_watches",
    }

    from alembic import command

    import tests.platform.conftest as conftest

    cfg = conftest._alembic_config(test_database)
    command.downgrade(cfg, "base")
    result = await session.execute(
        text(
            "select 1 from information_schema.tables "
            "where table_schema='public' and table_name like 'platform_%' limit 1"
        )
    )
    assert result.first() is None
    command.upgrade(cfg, "head")
