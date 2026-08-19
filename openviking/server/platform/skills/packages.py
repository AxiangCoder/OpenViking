"""Skill 包解析与校验（10 §54.1/§54.2/§61.5）。

产品层用共享校验能力解析名称、描述、标签、`allowed-tools` 和文件清单：

- 在线表单序列化为合法 `SKILL.md`（前端不要求普通用户手写 YAML Frontmatter，
  也不允许提交 Viking URI 或 `target_uri`，10 §54.1）；
- 上传包解析复用引擎 `SkillLoader` 的 Frontmatter 规则（`openviking/core/
  skill_loader.py`），名称按 `validate_skill_name`（≤64 字符、ASCII
  字母/数字/下划线/连字符），tags 按产品统一 `key=value` 校验
  （04 §10.10，05 §12.5）；
- ZIP 的路径穿越、绝对路径、符号链接、文件数量和大小限制由服务端校验
  （10 §54.2），错误原因不暴露给页面；
- `SkillPackageAdapter` 是受控上传字节适配层（开发态 `FakeSkillPackageAdapter`
  内存实现，与 P2-E1 `FakeControlPlane` 同模式；真实临时对象存储在
  P2-E3 upload 基础设施收口后按本协议替换）。
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Protocol

from openviking.server.platform.errors import SkillInvalidFormatError
from openviking.server.platform.registry.tags import normalize_tags

# 注意：`SkillLoader`/`validate_skill_name` 属引擎模块，模块顶层导入会触发
# `openviking.storage.queuefs` 循环导入（Platform 路由不挂载引擎 Router）；
# 一律在函数内延迟导入（与既有 Platform 模块规避策略一致）。

MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_ALLOWED_TOOLS = 50
MAX_ALLOWED_TOOL_LENGTH = 128
MAX_ZIP_ENTRIES = 200
MAX_ZIP_ENTRY_SIZE = 4 * 1024 * 1024  # 4 MiB
MAX_ZIP_TOTAL_SIZE = 32 * 1024 * 1024  # 解压总量上限（压缩炸弹防线，09 §47.3）
MAX_SKILL_MD_SIZE = 512 * 1024  # 512 KiB

CONTROL_FILES = {".abstract.md", ".overview.md", ".source.json"}

_SKILL_MD_NAME = "SKILL.md"


def _load_skill_loader():
    from openviking.core.skill_loader import SkillLoader

    return SkillLoader


def _validate_skill_name(name):
    from openviking.utils.skill_processor import validate_skill_name as _validate

    return _validate(name)


@dataclass(frozen=True)
class AuxiliaryFile:
    """ZIP 中 SKILL.md 之外的辅助文件（name 为相对路径，服务端校验后）。"""

    name: str
    content: bytes


@dataclass(frozen=True)
class ParsedSkill:
    """SKILL.md/ZIP 解析结果（10 §54.2 共享校验产物）。"""

    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    content: str = ""
    auxiliary_files: list[AuxiliaryFile] = field(default_factory=list)

    def to_skill_md(self) -> str:
        """序列化回合法 SKILL.md（标签/工具范围按产品规范化）。"""
        return _load_skill_loader().to_skill_md(
            {
                "name": self.name,
                "description": self.description,
                "allowed_tools": self.allowed_tools,
                "tags": self.tags,
                "content": self.content,
            }
        )


def parse_skill_md_text(text: str) -> ParsedSkill:
    """解析单个 SKILL.md 文本（10 §54.2）。

    - Frontmatter 缺失/非法、name/description 缺失 → `SKILL_INVALID_FORMAT`；
    - name 按 `validate_skill_name` 规范化；tags 按产品 `key=value` 校验；
    - `allowed-tools` 支持空格分隔字符串或字符串数组（引擎既有语义）。
    """
    if text is None or not isinstance(text, str) or len(text.encode("utf-8")) > MAX_SKILL_MD_SIZE:
        raise SkillInvalidFormatError("MISSING_SKILL_MD")
    try:
        skill = _load_skill_loader().parse(text)
    except ValueError as exc:
        raise SkillInvalidFormatError("INVALID_FRONTMATTER") from exc
    try:
        name = _validate_skill_name(skill["name"])
    except ValueError as exc:
        raise SkillInvalidFormatError("INVALID_SKILL_NAME") from exc
    description = str(skill.get("description") or "").strip()
    if not description:
        raise SkillInvalidFormatError("DESCRIPTION_REQUIRED")
    if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        raise SkillInvalidFormatError("DESCRIPTION_TOO_LONG")
    allowed_tools = list(skill.get("allowed_tools") or [])
    if len(allowed_tools) > MAX_ALLOWED_TOOLS:
        raise SkillInvalidFormatError("ALLOWED_TOOLS_LIMIT")
    if any(len(t) > MAX_ALLOWED_TOOL_LENGTH or not t for t in allowed_tools):
        raise SkillInvalidFormatError("ALLOWED_TOOLS_INVALID")
    try:
        tags = normalize_tags(skill.get("tags") or [])
    except ValueError as exc:
        raise SkillInvalidFormatError("TAGS_INVALID") from exc
    return ParsedSkill(
        name=name,
        description=description,
        tags=tags,
        allowed_tools=allowed_tools,
        content=skill.get("content") or "",
    )


def _is_safe_member_name(name: str) -> bool:
    """ZIP 成员名安全校验（10 §54.2）：无路径穿越、无绝对路径、无符号链接。

    Windows 反斜杠路径、`.`/`..` 段、空段、盘符前缀与控制字符一律拒绝。
    """
    if not name or "\\" in name:
        return False
    if any(ord(c) < 32 for c in name):
        return False
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    return True


def parse_skill_zip(data: bytes) -> ParsedSkill:
    """解析并安全校验 ZIP Skill 包（10 §54.2）。

    - ZIP 根目录或唯一一级子目录中必须存在 `SKILL.md`（10 §51）；
    - 路径穿越/绝对路径/符号链接/控制字符成员 → `SKILL_INVALID_FORMAT`；
    - 文件数量（≤200）、单文件大小（≤4 MiB）与解压总量（≤32 MiB）由
      服务端限制（压缩炸弹防线，09 §47.3）；
    - 加密/CRC 损坏/截断成员统一拒绝（不暴露内部异常）。
    """
    if not data:
        raise SkillInvalidFormatError("EMPTY_ZIP")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillInvalidFormatError("INVALID_ZIP") from exc
    try:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_ZIP_ENTRIES:
            raise SkillInvalidFormatError("ZIP_ENTRY_LIMIT")
        entries: dict[str, bytes] = {}
        symlinks: list[str] = []
        total_size = 0
        for info in infos:
            if info.is_dir():
                continue
            name = info.filename
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                symlinks.append(name)
                continue
            if not _is_safe_member_name(name):
                raise SkillInvalidFormatError("ZIP_PATH_TRAVERSAL")
            if info.file_size > MAX_ZIP_ENTRY_SIZE:
                raise SkillInvalidFormatError("ZIP_ENTRY_TOO_LARGE")
            total_size += info.file_size
            if total_size > MAX_ZIP_TOTAL_SIZE:
                raise SkillInvalidFormatError("ZIP_TOTAL_TOO_LARGE")
            try:
                entries[name] = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile, zlib.error, NotImplementedError) as exc:
                # 加密成员（RuntimeError）/CRC 损坏（BadZipFile）/截断
                # （zlib.error）/压缩方法不支持（NotImplementedError）
                raise SkillInvalidFormatError("ZIP_READ_FAILED") from exc
    finally:
        archive.close()

    if symlinks:
        # 符号链接一律拒绝（10 §54.2 服务端校验，不进入文件清单）
        raise SkillInvalidFormatError("ZIP_SYMLINK_FORBIDDEN")

    skill_md_path = _locate_skill_md(entries)
    if skill_md_path is None:
        raise SkillInvalidFormatError("MISSING_SKILL_MD")
    skill_md = entries.pop(skill_md_path).decode("utf-8", errors="replace")

    parsed = parse_skill_md_text(skill_md)
    aux = []
    for name, content in sorted(entries.items()):
        if name.split("/")[-1] in CONTROL_FILES:
            continue
        aux.append(AuxiliaryFile(name=name, content=content))
    return ParsedSkill(
        name=parsed.name,
        description=parsed.description,
        tags=parsed.tags,
        allowed_tools=parsed.allowed_tools,
        content=parsed.content,
        auxiliary_files=aux,
    )


def _locate_skill_md(entries: dict[str, bytes]) -> str | None:
    """定位 ZIP 内的 `SKILL.md`（10 §51：根目录或唯一一级子目录）。

    返回完整成员名；未找到返回 None。
    """
    if _SKILL_MD_NAME in entries:
        return _SKILL_MD_NAME
    top_level = {name.split("/")[0] for name in entries}
    if len(top_level) == 1:
        (root,) = top_level
        candidate = f"{root}/{_SKILL_MD_NAME}"
        if candidate in entries:
            return candidate
    return None


def parse_skill_bytes(kind: str, data: bytes) -> ParsedSkill:
    """按包类型解析字节（`zip` → ZIP 安全解析，否则单个 SKILL.md 文本）。"""
    if kind == "zip":
        return parse_skill_zip(data)
    return parse_skill_md_text(data.decode("utf-8", errors="replace"))


class SkillPackageAdapter(Protocol):
    """受控上传字节适配层（10 §61.5：消费 upload_id 后取包解析）。"""

    async def fetch(self, storage_ref: str) -> bytes: ...

    async def upload_kind(self, storage_ref: str) -> str:
        """`skill_md`（单个 SKILL.md）或 `zip`（ZIP 包）。"""


class FakeSkillPackageAdapter:
    """开发态/单元测试适配层：内存 storage_ref → 原始字节。

    `store_upload` 供单元测试注册字节；解析语义与
    `TempUploadStorePackageAdapter` 一致（`parse_skill_bytes`）。
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._kinds: dict[str, str] = {}

    def store_upload(self, storage_ref: str, data: bytes, *, kind: str = "skill_md") -> None:
        self._store[storage_ref] = data
        self._kinds[storage_ref] = kind

    async def fetch(self, storage_ref: str) -> bytes:
        try:
            return self._store[storage_ref]
        except KeyError as exc:
            raise SkillInvalidFormatError("UPLOAD_CONTENT_MISSING") from exc

    async def upload_kind(self, storage_ref: str) -> str:
        return self._kinds.get(storage_ref, "skill_md")

    def parse(self, storage_ref: str) -> ParsedSkill:
        kind = self._kinds.get(storage_ref, "skill_md")
        data = self._store.get(storage_ref)
        if data is None:
            raise SkillInvalidFormatError("UPLOAD_CONTENT_MISSING")
        return parse_skill_bytes(kind, data)


class TempUploadStorePackageAdapter:
    """P2-E3 `me/resource-uploads` 链路适配（10 §61.5 联合验证）。

    消费 `upload_id` 后，SkillService 以 upload 记录的 `storage_ref` 从
    P2-E3 受控临时上传存储（`TempUploadStore`）取回服务端检测的字节；
    包类型由服务端检测的 MIME/原始文件名决定（`application/zip` 或
    `.zip` 后缀 → ZIP，否则按单个 `SKILL.md` 文本解析，04 §10.14：
    文件名/MIME/大小由服务端计算，不信任浏览器声明）。

    开发态装配（mount.py/conftest）与生产接线统一使用本适配器；
    `FakeSkillPackageAdapter` 保留用于不接临时存储的单元级测试。
    """

    def __init__(self, store) -> None:
        self._store = store

    async def fetch(self, storage_ref: str) -> bytes:
        blob = await self._store.get(storage_ref)
        if blob is None or not blob.data:
            raise SkillInvalidFormatError("UPLOAD_CONTENT_MISSING")
        return blob.data

    async def upload_kind(self, storage_ref: str) -> str:
        blob = await self._store.get(storage_ref)
        if blob is None:
            raise SkillInvalidFormatError("UPLOAD_CONTENT_MISSING")
        name = (blob.original_filename or "").lower()
        mime = (blob.mime_type or "").lower()
        if mime == "application/zip" or name.endswith(".zip"):
            return "zip"
        return "skill_md"
