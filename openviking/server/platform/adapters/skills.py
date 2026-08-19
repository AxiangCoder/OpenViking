"""真实 Skill 内容写入/发布迁移适配器（10 §58.4/§61.5，P5-E1 接线）。

`SkillContentAdapter`/`SkillMigrationAdapter` 协议的真实实现：VikingFS
文件操作（mkdir/write_file/rm/stat/mv——mv 含向量索引重写与失败回滚语义）。

**共享根写入未接线**：v0.4.12 文件层按 account 隔离（/local/{account_id}/
agent/skills/...），"Account 共享"内容落在发布方 Account 的 namespace；
`SkillContentAdapter` 协议不携带 Actor Account，共享根 URI 无法确定物理
归属，显式抛 `AdapterRuntimeUnavailable`（避免写错位置，供 P5-E4 评审）。
发布迁移以源私有根账号为准，私有→共享迁移已接线。

私有根上下文：`ov_user_id` 从 URI 解析，Account 经 PostgreSQL IAM 反查
（`get_account_id_for_ov_user` + `get_account.ov_account_id`）。
"""

from __future__ import annotations

from typing import Optional

from openviking.server.platform.adapters.runtime import (
    AdapterRuntimeUnavailable,
    ServiceProvider,
    default_service_provider,
    require_service,
    user_ctx,
)
from openviking.server.platform.skills.control_plane import CONTROL_FILE_NAMES
from openviking.server.platform.skills.packages import AuxiliaryFile

_SKILL_MD_NAME = "SKILL.md"
_PRIVATE_ROOT_PREFIX = "viking://user/"
_SHARED_ROOT_PREFIXES = ("viking://agent/", "viking://resources/")


def _uri_scope(uri: str) -> str:
    if uri.startswith(_SHARED_ROOT_PREFIXES):
        return "shared"
    return "user"


def _ov_user_from_private_uri(uri: str) -> str:
    rest = uri.rstrip("/")
    if not rest.startswith("viking://"):
        raise AdapterRuntimeUnavailable(f"skill_content (unsupported uri: {uri})")
    parts = rest[len("viking://") :].split("/")
    if len(parts) < 2 or parts[0] != "user":
        raise AdapterRuntimeUnavailable(f"skill_content (unsupported uri: {uri})")
    return parts[1]


class RealSkillContentAdapter:
    """真实 Skill 内容写入面（VikingFS 文件操作；私有根，共享根未接线）。"""

    def __init__(self, service_provider: Optional[ServiceProvider] = None) -> None:
        self._provider: ServiceProvider = service_provider or default_service_provider()

    def _runtime(self):
        service = require_service(self._provider, "skill_content")
        viking_fs = getattr(service, "viking_fs", None)
        if viking_fs is None:
            raise AdapterRuntimeUnavailable("skill_content")
        return service, viking_fs

    async def _ctx_for_uri(self, service, uri: str):
        if _uri_scope(uri) == "shared":
            raise AdapterRuntimeUnavailable(
                "skill_content (shared root wiring pending deployment layout confirmation, P5-E1)"
            )
        ov_user_id = _ov_user_from_private_uri(uri)
        ov_account_id = await self._resolve_account(service, ov_user_id)
        return user_ctx(ov_account_id, ov_user_id)

    async def add(
        self, *, uri: str, skill_md: str, auxiliary_files: list[AuxiliaryFile] | None = None
    ) -> None:
        _, viking_fs = self._runtime()
        ctx = await self._ctx_for_uri(_, uri)
        await viking_fs.mkdir(uri, exist_ok=True, ctx=ctx)
        await viking_fs.write_file(f"{uri}/{_SKILL_MD_NAME}", skill_md.encode("utf-8"), ctx=ctx)
        for aux in auxiliary_files or []:
            if aux.name.split("/")[-1] in CONTROL_FILE_NAMES:
                continue
            await viking_fs.write_file(f"{uri}/{aux.name}", aux.content, ctx=ctx)

    async def replace(
        self, *, uri: str, skill_md: str, auxiliary_files: list[AuxiliaryFile] | None = None
    ) -> None:
        _, viking_fs = self._runtime()
        ctx = await self._ctx_for_uri(_, uri)
        try:
            await viking_fs.stat(uri, ctx=ctx)
        except Exception as exc:
            raise FileNotFoundError(f"skill not found: {uri}") from exc
        await self.add(uri=uri, skill_md=skill_md, auxiliary_files=auxiliary_files)

    async def remove(self, *, uri: str) -> None:
        _, viking_fs = self._runtime()
        ctx = await self._ctx_for_uri(_, uri)
        await viking_fs.rm(uri, recursive=True, ctx=ctx)

    async def read(self, *, uri: str) -> dict | None:
        _, viking_fs = self._runtime()
        ctx = await self._ctx_for_uri(_, uri)
        try:
            skill_md = await viking_fs.read_file_text(f"{uri}/{_SKILL_MD_NAME}", ctx=ctx)
        except Exception:
            return None
        entries = []
        try:
            listing = await viking_fs.ls(uri, ctx=ctx)
            for entry in listing:
                name = entry.get("name")
                if name in CONTROL_FILE_NAMES or name == _SKILL_MD_NAME:
                    continue
                entries.append({"name": name, "size_bytes": int(entry.get("size") or 0)})
        except Exception:
            pass
        return {"skill_md": skill_md, "files": sorted(entries, key=lambda e: e["name"])}

    async def _resolve_account(self, service, ov_user_id: str) -> str:
        from openviking.server.platform.db import session_factory
        from openviking.server.platform.iam import PostgresIamRepository

        repo = PostgresIamRepository()
        async with session_factory() as session:
            account_id = await repo.get_account_id_for_ov_user(session, ov_user_id)
            account = await repo.get_account(session, account_id) if account_id else None
        if account is None or account.ov_account_id is None:
            raise AdapterRuntimeUnavailable(f"skill_content (ov_user {ov_user_id} not mapped)")
        return account.ov_account_id


class RealSkillMigrationAdapter:
    """真实发布迁移适配层（VikingFS.mv：整目录复制 + 向量 URI 重写 + 源删除）。

    `fs.mv` 语义与 Fake 对齐：向量重写失败 → 清理已复制目标、源保持完整；
    源缺失 → 只清理孤儿索引且幂等（失败重试不产生重复移动）。`clean_owner_user_id`
    由 fs.mv 的向量重写语义覆盖（迁移后 URI 变化即按新 URI 重算归属），
    真实层为空操作并记录原因。

    上下文：v0.4.12 文件层按 account 隔离，"Account 共享"内容落在发布方
    Account 的 namespace（/local/{ov_account_id}/agent/skills/...，与产品
    04 §10.1 Account 级共享语义一致）；源为私有根（viking://user/{id}/...），
    目标共享根沿用同一 Account 上下文完成迁移。
    """

    def __init__(self, service_provider: Optional[ServiceProvider] = None) -> None:
        self._provider: ServiceProvider = service_provider or default_service_provider()

    def _runtime(self):
        service = require_service(self._provider, "skill_migration")
        viking_fs = getattr(service, "viking_fs", None)
        if viking_fs is None:
            raise AdapterRuntimeUnavailable("skill_migration")
        return service, viking_fs

    async def _ctx_for_uri(self, service, uri: str):
        if _uri_scope(uri) == "shared":
            # 共享根 URI 本身不含账号；迁移调用方以源私有根账号为准，
            # 本方法仅供 source_exists/target_exists 存在性检查（无账号语义）。
            return user_ctx("default", "default")
        ov_user_id = _ov_user_from_private_uri(uri)
        ov_account_id = await self._resolve_account(service, ov_user_id)
        return user_ctx(ov_account_id, ov_user_id)

    async def target_exists(self, uri: str) -> bool:
        service, viking_fs = self._runtime()
        try:
            await viking_fs.stat(uri, ctx=await self._ctx_for_uri(service, uri))
            return True
        except Exception:
            return False

    async def source_exists(self, uri: str) -> bool:
        service, viking_fs = self._runtime()
        try:
            await viking_fs.stat(uri, ctx=await self._ctx_for_uri(service, uri))
            return True
        except Exception:
            return False

    async def migrate(self, *, from_uri: str, to_uri: str) -> None:
        service, viking_fs = self._runtime()
        ctx = await self._ctx_for_uri(service, from_uri)
        try:
            await viking_fs.mv(from_uri, to_uri, ctx=ctx)
        except Exception as exc:
            raise RuntimeError(f"skill migration failed: {exc}") from exc

    async def clean_owner_user_id(self, *, uri: str) -> None:
        # fs.mv 的向量重写已按新 URI 重算归属；v0.4.12 无单条向量字段
        # 变更 API，此步为空操作（语义由 mv 覆盖）。
        return None

    async def _resolve_account(self, service, ov_user_id: str) -> str:
        from openviking.server.platform.db import session_factory
        from openviking.server.platform.iam import PostgresIamRepository

        repo = PostgresIamRepository()
        async with session_factory() as session:
            account_id = await repo.get_account_id_for_ov_user(session, ov_user_id)
            account = await repo.get_account(session, account_id) if account_id else None
        if account is None or account.ov_account_id is None:
            raise AdapterRuntimeUnavailable(f"skill_migration (ov_user {ov_user_id} not mapped)")
        return account.ov_account_id
