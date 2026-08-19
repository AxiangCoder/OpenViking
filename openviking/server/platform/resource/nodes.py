"""Resource 内部节点访问控制（09 §42.3/§48 #12，AC⑨）。

- `node_id` 是服务端生成、绑定 `resource_id + relative_path` 的不透明标识
  （带版本的 HMAC 编码，09 §42.3）；
- 解析后必须再次确认规范化路径仍位于该 Resource 根内；跨 Resource 引用
  因 HMAC 不匹配被拒绝并审计（09 §48 #12）；
- 隐藏内部控制文件（`.abstract.md`/`.overview.md`/`.relations.json`、
  锁文件、临时目录等，09 §42.3）；
- 目录按需展开、限制单页节点数和最大深度（09 §42.3）。

Node ID 格式：`nv1.<resource_id>.<hmac_hex(32)>.<b64url(relative_path)>`。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass

MAX_DEPTH = 32
MAX_PATH_LENGTH = 512
MAX_CHILDREN_PER_PAGE = 200

# 内部控制文件/目录（09 §42.3：隐藏，不进入节点树）
HIDDEN_NAMES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        ".relations.json",
        ".ov.json",
        ".tmp",
        ".lock",
        ".git",
    }
)


@dataclass(frozen=True)
class DecodedNode:
    """解析成功且归属当前 Resource 的节点定位。"""

    resource_id: uuid.UUID
    relative_path: str


def normalize_rel_path(path: str) -> str | None:
    """规范化 Resource 内相对路径；越界（`..`/绝对路径/超长/超深）返回 None。

    - 只允许相对路径；禁止 `..`、绝对路径（leading `/`）、反斜杠；
    - 隐藏控制文件段直接返回 None（内部文件不进产品树）。
    """
    if not isinstance(path, str):
        return None
    raw = path.strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        return None
    if len(parts) > MAX_DEPTH:
        return None
    normalized = "/".join(parts)
    if len(normalized) > MAX_PATH_LENGTH:
        return None
    for part in parts:
        if part in HIDDEN_NAMES or part.startswith(".ov_"):
            return None
    return normalized


class NodeIdCodec:
    """带版本 HMAC 的 Node ID 编解码（09 §42.3，密钥来自配置，不落库）。"""

    VERSION = "nv1"

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def encode(self, resource_id: uuid.UUID, relative_path: str) -> str:
        normalized = normalize_rel_path(relative_path)
        if normalized is None:
            raise ValueError(f"invalid relative path: {relative_path!r}")
        payload = f"{resource_id}|{normalized}"
        digest = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        encoded_path = (
            base64.urlsafe_b64encode(normalized.encode("utf-8")).rstrip(b"=").decode("ascii")
        )
        return f"{self.VERSION}.{resource_id}.{digest}.{encoded_path}"

    def decode(self, resource_id: uuid.UUID, node_id: str) -> str | None:
        """在指定 Resource 内解析 Node ID；归属不符/篡改返回 None（AC⑨）。"""
        try:
            version, res_id, digest, encoded_path = node_id.split(".", 3)
        except ValueError:
            return None
        if version != self.VERSION:
            return None
        try:
            if uuid.UUID(res_id) != resource_id:
                return None
        except ValueError:
            return None
        try:
            padding = "=" * (-len(encoded_path) % 4)
            relative_path = base64.urlsafe_b64decode(encoded_path + padding).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
        normalized = normalize_rel_path(relative_path)
        if normalized is None:
            return None
        expected = hmac.new(
            self._secret, f"{resource_id}|{normalized}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, digest):
            return None
        return normalized


def safe_download_filename(display_name: str, source_name: str | None) -> str:
    """下载响应安全文件名（09 §42.3：注入响应头防护，09 §48 #11）。

    只保留可打印 ASCII，替换控制字符/引号/换行；空结果回退 `download`。
    """
    raw = source_name or display_name or "download"
    cleaned = "".join(ch for ch in raw if 32 <= ord(ch) < 127 and ch not in '"\\;')
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return "download"
    if len(cleaned) > 180:
        cleaned = cleaned[-180:]
    return cleaned
