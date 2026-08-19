"""Resource 内容执行面适配层（09 §37/§40.7，05 §11.2）。

真实 OpenViking 的摄取/目录/检索/清理在 P5-E1 初始化阶段接线（与 P2-E1
`FakeControlPlane` 同模式）；本 Epic 交付受控 fake 适配层，验证产品语义：

- `ingest`：受控目标执行摄取（成功 → 生成节点树/Abstract/Overview；
  失败 → 脱敏错误码与 retryable）；
- `list_nodes/read_node`：Resource 内部只读树（隐藏控制文件在 nodes 层过滤）；
- `search`：Resource 内检索（09 §46.2）；
- `copy`：私有→共享发布复制内容（生成新 Resource 的独立内容，09 §44.2）；
- `delete`：Purge 物理清理（幂等）；
- `cancel`：协作取消（可取消 Task，09 §43.3）。

Fake 的行为设计为确定性的，便于测试断言：
- 上传来源 → 单文件节点（原文件名，内容为上传字节）；
- 网页来源 → 模拟 `index.html` + 派生 Abstract/Overview；
- Git 来源 → 固定仓库结构（README.md/src/main.py/docs/guide.md + 一个
  二进制文件验证 `strict=false` 跳过语义）；
- 失败注入：host 含 `fail.invalid` → `RESOURCE_PARSE_FAILED`(retryable)；
  host 含 `fatal.invalid` → `RESOURCE_INDEX_FAILED`(retryable=False)；
  文件名以 `fail-` 开头 → `RESOURCE_PARSE_FAILED`(retryable=True)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from openviking.server.platform.resource.nodes import normalize_rel_path

RESOURCE_PARSE_FAILED = "RESOURCE_PARSE_FAILED"
RESOURCE_INDEX_FAILED = "RESOURCE_INDEX_FAILED"


@dataclass(frozen=True)
class NodeRecord:
    """Resource 内部节点（09 §42.3：路径为 Resource 根内相对路径）。"""

    path: str
    kind: str  # file | dir
    size_bytes: int
    mime_type: str | None
    text: bytes | None = None

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class SearchHit:
    """Resource 内检索命中（09 §46.2：不返回底层 URI/score）。"""

    node_id: str
    name: str
    snippet: str


@dataclass(frozen=True)
class IngestOutcome:
    """摄取结果（09 §40.7：失败返回脱敏错误码与 retryable）。"""

    ok: bool
    nodes: list[NodeRecord] = field(default_factory=list)
    abstract: str | None = None
    overview: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    retryable: bool = False


class ResourceExecutionPlane(Protocol):
    """受控内容执行面（生产实现按本协议替换，测试不依赖 fake 具体存储）。"""

    async def ingest(
        self, *, ref_id: uuid.UUID, source: dict, instruction: str | None
    ) -> IngestOutcome: ...

    async def list_nodes(self, *, ref_id: uuid.UUID) -> list[NodeRecord]: ...

    async def read_node(self, *, ref_id: uuid.UUID, rel_path: str) -> NodeRecord | None: ...

    async def search(self, *, ref_id: uuid.UUID, query: str, limit: int = 10) -> list[dict]: ...

    async def copy(self, *, source_ref_id: uuid.UUID, target_ref_id: uuid.UUID) -> None: ...

    async def delete(self, *, ref_id: uuid.UUID) -> None: ...

    async def cancel(self, ov_operation_id: str) -> bool: ...


class FakeResourceExecutionPlane:
    """内存执行面（开发/测试适配层，P2-E1 FakeControlPlane 同模式）。"""

    def __init__(self) -> None:
        self._trees: dict[uuid.UUID, dict] = {}
        self._tasks: dict[str, str] = {}

    # ── 摄取 ──

    async def ingest(
        self, *, ref_id: uuid.UUID, source: dict, instruction: str | None
    ) -> IngestOutcome:
        source_type = source.get("source_type")
        display = source.get("source_display") or "unknown"
        if source_type == "upload":
            blob = source.get("blob")
            if blob is None:
                return IngestOutcome(ok=False, error_code=RESOURCE_PARSE_FAILED, error_summary="missing upload blob", retryable=False)
            if blob.original_filename.startswith("fail-"):
                return IngestOutcome(ok=False, error_code=RESOURCE_PARSE_FAILED, error_summary="simulated upload parse failure", retryable=True)
            self._trees[ref_id] = {
                "nodes": [
                    NodeRecord(
                        path=blob.original_filename,
                        kind="file",
                        size_bytes=blob.size_bytes,
                        mime_type=blob.mime_type,
                        text=blob.data,
                    )
                ],
                "abstract": f"上传文件 {blob.original_filename}",
                "overview": f"单文件 Resource：{blob.original_filename}（{blob.size_bytes} 字节）",
            }
            return IngestOutcome(ok=True, nodes=self._trees[ref_id]["nodes"], abstract=self._trees[ref_id]["abstract"], overview=self._trees[ref_id]["overview"])

        if source_type == "web":
            if "fail.invalid" in display:
                return IngestOutcome(ok=False, error_code=RESOURCE_PARSE_FAILED, error_summary="simulated web fetch failure", retryable=True)
            if "fatal.invalid" in display:
                return IngestOutcome(ok=False, error_code=RESOURCE_INDEX_FAILED, error_summary="simulated web index failure", retryable=False)
            host = display.split("/", 2)[2] if "://" in display else display
            title = f"{host} 页面"
            html = (
                f"<html><head><title>{title}</title></head><body>"
                f"<h1>{title}</h1><p>模拟公开网页正文，来源 {host}。</p></body></html>"
            ).encode("utf-8")
            self._trees[ref_id] = {
                "nodes": [NodeRecord(path="index.html", kind="file", size_bytes=len(html), mime_type="text/html", text=html)],
                "abstract": title,
                "overview": f"公开网页来源：{host}（模拟内容）",
            }
            return IngestOutcome(ok=True, nodes=self._trees[ref_id]["nodes"], abstract=self._trees[ref_id]["abstract"], overview=self._trees[ref_id]["overview"])

        if source_type == "git":
            if "fail.invalid" in display:
                return IngestOutcome(ok=False, error_code=RESOURCE_PARSE_FAILED, error_summary="simulated git clone failure", retryable=True)
            host = display.split("/", 2)[2] if "://" in display else display
            files = {
                "README.md": f"# {host} 仓库\n\n模拟公开 Git 仓库。",
                "src/main.py": "def main():\n    print('hello')\n",
                "docs/guide.md": "# 使用指南\n\n公开仓库文档。",
                "assets/logo.bin": b"\x00\x01\x02\x03binary-placeholder",
            }
            nodes: list[NodeRecord] = []
            for rel, content in files.items():
                data = content if isinstance(content, bytes) else content.encode("utf-8")
                mime = "application/octet-stream" if rel.endswith(".bin") else "text/plain"
                nodes.append(NodeRecord(path=rel, kind="file", size_bytes=len(data), mime_type=mime, text=data))
            nodes.append(NodeRecord(path="src", kind="dir", size_bytes=0, mime_type=None))
            nodes.append(NodeRecord(path="docs", kind="dir", size_bytes=0, mime_type=None))
            nodes.append(NodeRecord(path="assets", kind="dir", size_bytes=0, mime_type=None))
            self._trees[ref_id] = {
                "nodes": nodes,
                "abstract": f"Git 仓库 {host}",
                "overview": f"公开 HTTPS Git 仓库来源：{host}（目录结构保留）",
            }
            return IngestOutcome(ok=True, nodes=nodes, abstract=self._trees[ref_id]["abstract"], overview=self._trees[ref_id]["overview"])

        return IngestOutcome(ok=False, error_code=RESOURCE_PARSE_FAILED, error_summary="unsupported source type", retryable=False)

    # ── 只读节点/检索 ──

    async def list_nodes(self, *, ref_id: uuid.UUID) -> list[NodeRecord]:
        tree = self._trees.get(ref_id)
        if tree is None:
            return []
        return list(tree["nodes"])

    async def read_node(self, *, ref_id: uuid.UUID, rel_path: str) -> NodeRecord | None:
        tree = self._trees.get(ref_id)
        if tree is None:
            return None
        for node in tree["nodes"]:
            if node.path == rel_path:
                return node
        return None

    async def search(self, *, ref_id: uuid.UUID, query: str, limit: int = 10) -> list[dict]:
        tree = self._trees.get(ref_id)
        if tree is None:
            return []
        needle = query.lower()
        hits: list[dict] = []
        for node in tree["nodes"]:
            if node.kind != "file" or node.text is None:
                continue
            text = node.text.decode("utf-8", errors="replace").lower()
            idx = text.find(needle)
            if idx >= 0:
                snippet = text[max(0, idx - 40) : idx + len(query) + 60].replace("\n", " ")
                hits.append({"name": node.name, "path": node.path, "snippet": snippet})
            if len(hits) >= limit:
                break
        return hits

    # ── 发布复制 / 清理 / 取消 ──

    async def copy(self, *, source_ref_id: uuid.UUID, target_ref_id: uuid.UUID) -> None:
        tree = self._trees.get(source_ref_id)
        if tree is None:
            raise KeyError(f"source content {source_ref_id} missing")
        self._trees[target_ref_id] = {
            "nodes": list(tree["nodes"]),
            "abstract": tree.get("abstract"),
            "overview": tree.get("overview"),
        }

    async def delete(self, *, ref_id: uuid.UUID) -> None:
        self._trees.pop(ref_id, None)

    async def cancel(self, ov_operation_id: str) -> bool:
        if ov_operation_id not in self._tasks:
            return False
        self._tasks[ov_operation_id] = "cancelling"
        return True


def build_node_tree(nodes: list[NodeRecord]) -> dict[str, NodeRecord]:
    """按相对路径建树（隐藏控制文件已在 nodes 层过滤，09 §42.3）。"""
    tree: dict[str, NodeRecord] = {}
    for node in nodes:
        normalized = normalize_rel_path(node.path)
        if normalized is None:
            continue
        tree[normalized] = node
        parts = normalized.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent not in tree:
                tree[parent] = NodeRecord(path=parent, kind="dir", size_bytes=0, mime_type=None)
    return tree
