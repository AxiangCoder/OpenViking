"""Skill 内容写入与发布迁移适配层（10 §58.4，05 §11.5）。

开发态实现为受控 fake（与 P2-E1 `FakeControlPlane` 同模式）：内存镜像
OpenViking 文件树与向量记录，验证语义而无需真实 VikingFS/VectorDB 环境；
真实适配器按本文件 Protocol 实现即可替换（P5-E1 接线）。

- `SkillContentAdapter`：Skill 新增/整体替换/删除/读取（等价引擎
  `add_skill`/整体替换，不暴露底层 Task/索引细节）；
- `SkillMigrationAdapter`：发布迁移（10 §58.4 步骤 3）——整目录复制
  （`SKILL.md`、`.abstract.md`、`.overview.md`、辅助文件与 `.source.json`）、
  向量 URI/ID 重写（保留原向量、不重新 embedding）、源目录删除、清洗
  残留 `owner_user_id`，一条 `migrate` 调用完成；源缺失时只清理孤儿索引
  且幂等（`fs.mv` 语义），失败重试不产生重复对象或重复移动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openviking.server.platform.skills.packages import AuxiliaryFile

CONTROL_FILE_NAMES = (".abstract.md", ".overview.md", ".source.json")


@dataclass(frozen=True)
class SkillFileEntry:
    """Skill 目录内文件清单条目（DTO 层去重/过滤后输出）。"""

    name: str
    size_bytes: int


class SkillContentAdapter(Protocol):
    """Skill 内容写入面（05 §11.5：所有对外创建/更新经 Content Registry）。"""

    async def add(
        self, *, uri: str, skill_md: str, auxiliary_files: list[AuxiliaryFile] | None = None
    ) -> None:
        """在 `uri` 写入完整 Skill 目录（创建语义，目录不存在时新建）。"""

    async def replace(
        self, *, uri: str, skill_md: str, auxiliary_files: list[AuxiliaryFile] | None = None
    ) -> None:
        """整体替换 Skill 目录内容（10 §56：ZIP 只能整体重传）。"""

    async def remove(self, *, uri: str) -> None:
        """删除 Skill 目录（产品层不直接调用，仅供受控 Purge 语义验证）。"""

    async def read(self, *, uri: str) -> dict | None:
        """读取 `SKILL.md` 文本与文件清单（详情页使用说明/文件树数据源）。"""


class FakeSkillContentAdapter:
    """开发态内容适配层：内存 {uri: {文件名: bytes}} 镜像。"""

    def __init__(self) -> None:
        self._dirs: dict[str, dict[str, bytes]] = {}

    async def add(
        self, *, uri: str, skill_md: str, auxiliary_files: list[AuxiliaryFile] | None = None
    ) -> None:
        files: dict[str, bytes] = {"SKILL.md": skill_md.encode("utf-8")}
        for aux in auxiliary_files or []:
            files[aux.name] = aux.content
        self._dirs[uri] = files

    async def replace(
        self, *, uri: str, skill_md: str, auxiliary_files: list[AuxiliaryFile] | None = None
    ) -> None:
        if uri not in self._dirs:
            raise FileNotFoundError(f"skill not found: {uri}")
        await self.add(uri=uri, skill_md=skill_md, auxiliary_files=auxiliary_files)

    async def remove(self, *, uri: str) -> None:
        self._dirs.pop(uri, None)

    async def read(self, *, uri: str) -> dict | None:
        files = self._dirs.get(uri)
        if files is None:
            return None
        skill_md = files.get("SKILL.md", b"").decode("utf-8", errors="replace")
        entries = [
            SkillFileEntry(name=name, size_bytes=len(content))
            for name, content in sorted(files.items())
            if name != "SKILL.md"
        ]
        return {"skill_md": skill_md, "files": entries}


class SkillMigrationAdapter(Protocol):
    """发布迁移面（10 §58.4 步骤 3：受控 fs.mv 语义）。"""

    async def target_exists(self, uri: str) -> bool:
        """目标共享根是否已有同名占用（磁盘侧复查，防事务外变更）。"""

    async def source_exists(self, uri: str) -> bool:
        """源私有 Skill 目录是否存在。"""

    async def migrate(self, *, from_uri: str, to_uri: str) -> None:
        """整目录迁移：复制文件树 + 向量 URI/ID 重写（保留原向量）+ 源目录
        删除，一条调用完成；源缺失时只清理孤儿索引且幂等。"""

    async def clean_owner_user_id(self, *, uri: str) -> None:
        """清洗向量记录残留的旧 `owner_user_id`（10 §58.4 步骤 3）。"""


class FakeSkillMigrationAdapter:
    """开发态迁移适配层：内存文件树 + 向量记录镜像，可注入阶段故障。

    行为对齐 `fs.mv`（openviking/storage/viking_fs.py:711）：

    - 向量重写失败 → 清理已复制目标、源保持完整（不出现"已迁移未转换"）；
    - 源缺失 → 只清理孤儿向量索引（幂等，重试不产生重复移动）；
    - 迁移前目标已存在 → 拒绝（发布前置/Worker 复查语义）。
    """

    def __init__(self, content: FakeSkillContentAdapter | None = None) -> None:
        self._content = content or FakeSkillContentAdapter()
        self._vectors: list[dict] = []
        self._fail_next_phase: str | None = None

    def seed_content(self, uri: str, files: dict[str, bytes]) -> None:
        self._content._dirs[uri] = files

    def seed_vectors(self, vectors: list[dict]) -> None:
        """向量镜像：`{uri, id, owner_user_id, vector_data}`；uri 为 Skill 目录。"""
        self._vectors.extend(vectors)

    @property
    def vectors(self) -> list[dict]:
        return list(self._vectors)

    @property
    def content(self) -> FakeSkillContentAdapter:
        return self._content

    def fail_next(self, phase: str) -> None:
        """注入下一次 migrate 的阶段故障：`copy`/`vector_rewrite`/`source_rm`。"""
        self._fail_next_phase = phase

    async def target_exists(self, uri: str) -> bool:
        return uri in self._content._dirs

    async def source_exists(self, uri: str) -> bool:
        return uri in self._content._dirs

    async def migrate(self, *, from_uri: str, to_uri: str) -> None:
        if from_uri not in self._content._dirs:
            # fs.mv 源缺失：只清理孤儿索引（幂等，10 §58.4 步骤 4）
            self._remove_orphan_vectors(from_uri)
            return
        if to_uri in self._content._dirs:
            raise FileExistsError(f"migration target already exists: {to_uri}")

        if self._fail_next_phase == "copy":
            self._fail_next_phase = None
            raise RuntimeError("injected failure: copy")

        copied = dict(self._content._dirs[from_uri])
        moved = self._rewrite_vectors(from_uri, to_uri)

        if self._fail_next_phase == "vector_rewrite":
            self._fail_next_phase = None
            # fs.mv：向量重写失败 → 清理已复制目标、源保持完整
            self._rollback_moved_vectors(moved)
            raise RuntimeError("injected failure: vector rewrite")

        self._content._dirs[to_uri] = copied

        if self._fail_next_phase == "source_rm":
            self._fail_next_phase = None
            del self._content._dirs[from_uri]
            raise RuntimeError("injected failure: source remove")

        del self._content._dirs[from_uri]
        for vector in moved:
            vector.pop("_moved_from", None)

    async def clean_owner_user_id(self, *, uri: str) -> None:
        for vector in self._vectors:
            if vector.get("uri") == uri or str(vector.get("uri", "")).startswith(uri.rstrip("/") + "/"):
                vector["owner_user_id"] = None

    # ── 内部工具（镜像 fs.mv 的向量重写/回滚/孤儿清理语义）──

    def _rewrite_vectors(self, from_uri: str, to_uri: str) -> list[dict]:
        moved: list[dict] = []
        for vector in self._vectors:
            uri = str(vector.get("uri", ""))
            if uri == from_uri:
                vector["_moved_from"] = uri
                vector["uri"] = to_uri
                moved.append(vector)
            elif uri.startswith(from_uri.rstrip("/") + "/"):
                vector["_moved_from"] = uri
                vector["uri"] = to_uri + uri[len(from_uri) :]
                moved.append(vector)
        return moved

    def _rollback_moved_vectors(self, moved: list[dict]) -> None:
        for vector in moved:
            prev = vector.pop("_moved_from", None)
            if prev is not None:
                vector["uri"] = prev

    def _remove_orphan_vectors(self, from_uri: str) -> None:
        self._vectors = [
            v
            for v in self._vectors
            if str(v.get("uri", "")) != from_uri
            and not str(v.get("uri", "")).startswith(from_uri.rstrip("/") + "/")
        ]
