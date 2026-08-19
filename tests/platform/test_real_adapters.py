"""P5-E1 真实适配器单元验证（14 号计划 §99.1 真实接线清单）。

用受控 stub OpenVikingService 验证真实适配器结构接线：正确解析运行时、
按 Protocol 签名调用真实服务接口、运行时缺失时抛 AdapterRuntimeUnavailable。
真实运行环境的端到端行为属 P5-E4 生产复验输入（本文件只验证接线正确性）。
"""

from __future__ import annotations

import pytest

from openviking.server.platform.adapters import (
    RealControlPlaneAdapter,
    RealResourcePurgeHandler,
    RealSearchEngine,
    RealSessionBackend,
    RealSkillConfigsAdapter,
    RealSkillContentAdapter,
    RealSkillMigrationAdapter,
)
from openviking.server.platform.adapters.runtime import AdapterRuntimeUnavailable


class _StubVikingFs:
    def __init__(self) -> None:
        self.ops: list[tuple] = []
        self.files: dict[str, bytes] = {}

    async def mkdir(self, uri, *, exist_ok=False, ctx=None):
        self.ops.append(("mkdir", uri, ctx))

    async def write_file(self, uri, data, *, ctx=None):
        self.ops.append(("write_file", uri, ctx))
        self.files[uri] = data

    async def stat(self, uri, *, ctx=None):
        self.ops.append(("stat", uri, ctx))
        if uri not in self.files:
            raise FileNotFoundError(uri)

    async def rm(self, uri, *, recursive=False, ctx=None):
        self.ops.append(("rm", uri, ctx))

    async def ls(self, uri, *, ctx=None):
        self.ops.append(("ls", uri, ctx))
        return [{"name": "u1", "isDir": True}, {"name": "file.md", "isDir": False}]

    async def mv(self, old_uri, new_uri, *, ctx=None):
        self.ops.append(("mv", old_uri, new_uri, ctx))

    async def read_file_text(self, uri, *, ctx=None):
        self.ops.append(("read_file_text", uri, ctx))
        return "SKILL.md text"


class _StubService:
    _initialized = True

    def __init__(self) -> None:
        self.viking_fs = _StubVikingFs()
        self.initialized_account_dirs: list = []
        self.initialized_user_dirs: list = []
        self._config = type("Cfg", (), {"default_account": "acct-1"})()
        self._privacy = _StubPrivacy()
        self._search = _StubSearch()
        self._sessions = _StubSessions()

    async def initialize_account_directories(self, ctx):
        self.initialized_account_dirs.append(ctx)

    async def initialize_user_directories(self, ctx):
        self.initialized_user_dirs.append(ctx)

    @property
    def privacy_configs(self):
        return self._privacy

    @property
    def search(self):
        return self._search

    @property
    def sessions(self):
        return self._sessions


class _StubPrivacy:
    async def exists(self, ctx, category, target_key):
        return target_key == "sk-1"

    async def get_meta(self, ctx, category, target_key):
        return type("Meta", (), {"active_version": 2, "latest_version": 3})()

    async def get_current(self, ctx, category, target_key):
        return type("Cur", (), {"values": {"k": "secret-value"}})()

    async def list_versions(self, ctx, category, target_key):
        return [1, 2, 3]

    async def upsert(self, ctx, category, target_key, values, *, updated_by="", change_reason=""):
        return type("Ver", (), {"version": 4, "values": values})()

    async def activate_version(self, ctx, category, target_key, version, *, updated_by=""):
        return type("Ver", (), {"version": version, "values": {"k": "v"}})()


class _StubSearch:
    async def find(self, query, ctx, target_uri, filter=None, **kwargs):
        return _StubFindResult()

    async def search(self, query, ctx, target_uri, session=None, filter=None, **kwargs):
        return _StubFindResult()


class _StubHit:
    uri = "viking://user/u1/memories/m1"
    context_type = "memory"
    level = 0
    abstract = "abstract text"
    score = 0.9
    match_reason = "语义相关"
    category = "memory"
    relations = []
    overview = None


class _StubFindResult:
    memories = [_StubHit()]
    resources = []
    skills = []
    query_plan = None
    total = 1


class _StubSessionObj:
    uri = "viking://user/u1/sessions/s1"
    meta = type("Meta", (), {"pending_tokens": 5, "commit_count": 2, "created_at": "t"})()

    async def load(self):
        self.messages = [
            type(
                "M",
                (),
                {
                    "to_dict": lambda self: {
                        "role": "user",
                        "parts": [{"type": "text", "text": "hi"}],
                        "created_at": "c",
                    },
                    "turn_id": "t1",
                },
            )()
        ]

    async def add_messages_async(self, messages_spec):
        return []

    async def commit_async(self, **kwargs):
        return {"task_id": "task-1", "commit_number": 1, "message_count_at_commit": 3}


class _StubSessions:
    def __init__(self) -> None:
        self.deleted: list = []

    def session(self, ctx, session_id):
        return _StubSessionObj()

    async def create(self, ctx, session_id=None, memory_policy=None):
        return _StubSessionObj()

    async def delete(self, session_id, ctx):
        self.deleted.append(session_id)


def _provider(service):
    return lambda: service


def _no_provider():
    return lambda: None


# ── 运行时缺失 → AdapterRuntimeUnavailable ──


@pytest.mark.parametrize(
    "factory,method",
    [
        (lambda: RealControlPlaneAdapter(_no_provider()), "provision_account"),
        (lambda: RealSkillConfigsAdapter(_no_provider()), "get"),
        (lambda: RealSkillContentAdapter(_no_provider()), "read"),
        (lambda: RealSkillMigrationAdapter(_no_provider()), "target_exists"),
        (lambda: RealSearchEngine(_no_provider()), "find"),
        (lambda: RealSessionBackend(_no_provider()), "get_meta"),
        (lambda: RealResourcePurgeHandler(_no_provider()), "purge"),
    ],
)
@pytest.mark.asyncio
async def test_adapters_require_runtime(factory, method) -> None:
    adapter = factory()
    with pytest.raises(AdapterRuntimeUnavailable):
        if method == "provision_account":
            await adapter.provision_account("acct-1")
        elif method == "get":
            await adapter.get(owner_ov_user_id="u-1", skill_id="sk-1")
        elif method == "read":
            await adapter.read(uri="viking://user/u-1/skills/x")
        elif method == "target_exists":
            await adapter.target_exists(uri="viking://agent/skills/x")
        elif method == "find":
            await adapter.find(
                account_ov_id="a",
                user_ov_id="u",
                roots=[],
                query="q",
                context_type=None,
                tags=[],
                since=None,
                until=None,
            )
        elif method == "get_meta":
            await adapter.get_meta(account_ov_id="a", user_ov_id="u", ov_session_id="s")
        else:
            await adapter.purge("session", type("J", (), {"ov_uri": "viking://resources/r1"})())


# ── RealControlPlaneAdapter ──


@pytest.mark.asyncio
async def test_real_control_plane_provisions_namespaces() -> None:
    service = _StubService()
    adapter = RealControlPlaneAdapter(_provider(service))

    await adapter.provision_account("acct-1")
    assert len(service.initialized_account_dirs) == 1
    assert service.initialized_account_dirs[0].user.account_id == "acct-1"

    await adapter.provision_user("acct-1", "u-1")
    assert len(service.initialized_user_dirs) == 1
    assert service.initialized_user_dirs[0].user.user_id == "u-1"

    assert await adapter.list_provisioned_accounts() == set()
    assert await adapter.list_provisioned_users("acct-1") == {"u1"}


# ── RealSkillConfigsAdapter ──


@pytest.mark.asyncio
async def test_real_skill_configs_get(monkeypatch) -> None:
    service = _StubService()
    adapter = RealSkillConfigsAdapter(_provider(service))

    async def _resolve(service, ov_user_id):
        return "acct-1"

    monkeypatch.setattr(adapter, "_resolve_account", _resolve)
    snapshot = await adapter.get(owner_ov_user_id="u-1", skill_id="sk-1")
    assert snapshot is not None and snapshot.configured
    assert snapshot.latest_version == 3
    assert snapshot.masked_values["k"] == "••••••••"  # 脱敏，无 Secret 明文

    assert await adapter.get(owner_ov_user_id="u-1", skill_id="sk-missing") is None


@pytest.mark.asyncio
async def test_real_skill_configs_upsert_activate(monkeypatch) -> None:
    service = _StubService()
    adapter = RealSkillConfigsAdapter(_provider(service))

    async def _resolve(service, ov_user_id):
        return "acct-1"

    monkeypatch.setattr(adapter, "_resolve_account", _resolve)
    snapshot = await adapter.upsert(
        owner_ov_user_id="u-1", skill_id="sk-1", values={"api_key": "s"}, updated_by="u-1"
    )
    assert snapshot.active_version == 4
    activated = await adapter.activate(
        owner_ov_user_id="u-1", skill_id="sk-1", version=2, updated_by="u-1"
    )
    assert activated.active_version == 2


# ── RealSearchEngine ──


@pytest.mark.asyncio
async def test_real_search_engine_maps_find_result() -> None:
    service = _StubService()
    adapter = RealSearchEngine(_provider(service))

    hits = await adapter.find(
        account_ov_id="acct-1",
        user_ov_id="u-1",
        roots=["viking://user/u-1"],
        query="hello",
        context_type=None,
        tags=[],
        since=None,
        until=None,
    )
    assert len(hits) == 1
    assert hits[0].uri == "viking://user/u1/memories/m1"
    assert hits[0].context_type == "memory"
    assert hits[0].display_name == "m1"

    hits2 = await adapter.search(
        account_ov_id="acct-1",
        user_ov_id="u-1",
        roots=["viking://user/u-1"],
        query="hello",
        context_type="memory",
        tags=["a=b"],
        since="2026-01-01",
        until=None,
        session_ov_id="s1",
    )
    assert len(hits2) == 1


# ── RealSessionBackend ──


@pytest.mark.asyncio
async def test_real_session_backend_flow() -> None:
    service = _StubService()
    adapter = RealSessionBackend(_provider(service))

    uri = await adapter.create(account_ov_id="acct-1", user_ov_id="u-1", ov_session_id="s1")
    assert uri == "viking://user/u1/sessions/s1"
    await adapter.append(
        account_ov_id="acct-1",
        user_ov_id="u-1",
        ov_session_id="s1",
        message=type(
            "M", (), {"role": "user", "content": "hi", "turn_id": "t", "client_created_at": "c"}
        )(),
    )
    outcome = await adapter.commit(
        account_ov_id="acct-1",
        user_ov_id="u-1",
        ov_session_id="s1",
        retention={
            "keep_recent_turn_count": 3,
            "retained_message_token_budget": 12000,
            "min_raw_tail_steps": 1,
        },
    )
    assert outcome.ov_task_id == "task-1"
    messages = await adapter.get_messages(
        account_ov_id="acct-1", user_ov_id="u-1", ov_session_id="s1"
    )
    assert messages[0]["content"] == "hi"
    meta = await adapter.get_meta(account_ov_id="acct-1", user_ov_id="u-1", ov_session_id="s1")
    assert meta["message_count"] == 1
    await adapter.delete(account_ov_id="acct-1", user_ov_id="u-1", ov_session_id="s1")
    assert service.sessions.deleted == ["s1"]


# ── RealSkillContentAdapter（私有根）──


@pytest.mark.asyncio
async def test_real_skill_content_private_root(monkeypatch) -> None:
    service = _StubService()
    adapter = RealSkillContentAdapter(_provider(service))

    async def _resolve(service, ov_user_id):
        return "acct-1"

    monkeypatch.setattr(adapter, "_resolve_account", _resolve)
    uri = "viking://user/u-1/skills/helper"
    await adapter.add(uri=uri, skill_md="---\nname: helper\n---\ncontent")
    ops = [op[0] for op in service.viking_fs.ops]
    assert "mkdir" in ops and "write_file" in ops

    await adapter.remove(uri=uri)
    rm_ops = [op for op in service.viking_fs.ops if op[0] == "rm"]
    assert len(rm_ops) == 1 and rm_ops[0][1] == uri and rm_ops[0][2] is not None

    read = await adapter.read(uri=uri)
    assert read is not None and read["skill_md"] == "SKILL.md text"


@pytest.mark.asyncio
async def test_real_skill_content_shared_root_unwired() -> None:
    """共享根写入未接线：显式报错，避免写错物理位置（P5-E1 未接线项）。"""
    service = _StubService()
    adapter = RealSkillContentAdapter(_provider(service))
    with pytest.raises(AdapterRuntimeUnavailable):
        await adapter.add(uri="viking://agent/skills/helper", skill_md="---\nname: helper\n---\n")


# ── RealSkillMigrationAdapter ──


@pytest.mark.asyncio
async def test_real_skill_migration_uses_fs_mv(monkeypatch) -> None:
    service = _StubService()
    adapter = RealSkillMigrationAdapter(_provider(service))

    async def _resolve(service, ov_user_id):
        return "acct-1"

    monkeypatch.setattr(adapter, "_resolve_account", _resolve)
    await adapter.migrate(
        from_uri="viking://user/u-1/skills/helper",
        to_uri="viking://agent/skills/helper",
    )
    assert any(op[0] == "mv" for op in service.viking_fs.ops)
    await adapter.clean_owner_user_id(uri="viking://agent/skills/helper")  # 空操作


# ── RealResourcePurgeHandler ──


@pytest.mark.asyncio
async def test_real_purge_handler_rm_with_ov_uri(monkeypatch) -> None:
    service = _StubService()
    adapter = RealResourcePurgeHandler(_provider(service))

    async def _ov_account_id(session, job):
        return "acct-1"

    monkeypatch.setattr(adapter, "_ov_account_id", _ov_account_id)
    job = type("J", (), {"account_id": "aid", "ov_uri": "viking://user/u-1/resources/doc"})()
    await adapter.purge("session", job)
    rm_ops = [op for op in service.viking_fs.ops if op[0] == "rm"]
    assert len(rm_ops) == 1 and rm_ops[0][1] == job.ov_uri


@pytest.mark.asyncio
async def test_real_purge_handler_no_uri_noop() -> None:
    service = _StubService()
    adapter = RealResourcePurgeHandler(_provider(service))
    job = type("J", (), {"account_id": "aid", "ov_uri": None})()
    await adapter.purge("session", job)
    assert service.viking_fs.ops == []
