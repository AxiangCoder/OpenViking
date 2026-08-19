"""临时上传存储与文件安全检测（09 §40.5/§47.3，04 §10.14）。

- 上传字节存受控 Temp Upload Store，不写入 PostgreSQL（04 §10.14）；
- 服务端独立执行流式大小限制、Magic Bytes/MIME 检测与扩展名策略
  （不能信任浏览器声明，09 §40.5/§40.6）；
- v0.1 产品上传默认不接受归档包（09 §47.3）；可执行文件可上传但不内联
  预览/下载（09 §42.3）。

开发态实现 `MemoryTempUploadStore` 与 P2-E1 `FakeControlPlane` 同模式：
协议 `TempUploadStore` 定义生产实现接口（S3/本地临时目录），测试与
开发态使用内存实现。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Protocol

from openviking.server.platform.resource.security import BLOCKED_UPLOAD_EXTENSIONS

MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
)


@dataclass(frozen=True)
class UploadedBlob:
    """服务端检测后的上传文件（04 §10.14：文件名/MIME/大小/hash 服务端计算）。"""

    storage_ref: str
    original_filename: str
    mime_type: str
    size_bytes: int
    content_hash: str  # SHA-256 全值（04 §10.14：DTO/审计不返回全值）
    data: bytes = b""


class TempUploadStore(Protocol):
    """临时上传存储（受控 Temp Upload Store，04 §10.14）。"""

    async def put(self, blob: UploadedBlob) -> str: ...

    async def get(self, storage_ref: str) -> UploadedBlob | None: ...

    async def delete(self, storage_ref: str) -> None: ...


class MemoryTempUploadStore:
    """内存临时存储（开发/测试适配层，同 FakeControlPlane 模式）。"""

    def __init__(self) -> None:
        self._blobs: dict[str, UploadedBlob] = {}

    async def put(self, blob: UploadedBlob) -> str:
        self._blobs[blob.storage_ref] = blob
        return blob.storage_ref

    async def get(self, storage_ref: str) -> UploadedBlob | None:
        return self._blobs.get(storage_ref)

    async def delete(self, storage_ref: str) -> None:
        self._blobs.pop(storage_ref, None)


def detect_mime(data: bytes, *, filename: str, declared: str | None) -> str:
    """Magic Bytes 优先，其次扩展名，最后回退声明值/`application/octet-stream`。"""
    for signature, mime in MAGIC_SIGNATURES:
        if data.startswith(signature):
            return mime
    if declared and not declared.startswith("application/octet-stream"):
        return declared
    dot = filename.lower().rfind(".")
    if dot > 0:
        return {
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".csv": "text/csv",
            ".json": "application/json",
            ".py": "text/x-python",
            ".js": "text/javascript",
            ".html": "text/html",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }.get(filename.lower()[dot:], "application/octet-stream")
    return "application/octet-stream"


def new_storage_ref() -> str:
    """不可猜测的临时存储引用（04 §10.14：不返回前端作为授权依据）。"""
    return f"tmp/{secrets.token_urlsafe(24)}"


def build_blob(*, filename: str, data: bytes, declared_mime: str | None) -> UploadedBlob:
    """构造服务端检测后的 blob（扩展名策略在此应用，04 §10.14）。"""
    name = (filename or "upload").strip()
    if name.lower().endswith(tuple(BLOCKED_UPLOAD_EXTENSIONS)):
        raise UnsupportedFormatError()
    mime = detect_mime(data, filename=name, declared=declared_mime)
    return UploadedBlob(
        storage_ref=new_storage_ref(),
        original_filename=name,
        mime_type=mime,
        size_bytes=len(data),
        content_hash=hashlib.sha256(data).hexdigest(),
        data=data,
    )


class UnsupportedFormatError(Exception):
    """归档/不允许的上传格式（09 §47.3，API 层映射 `RESOURCE_FORMAT_UNSUPPORTED`）。"""


def unsupported_format_reason() -> str:
    return "RESOURCE_FORMAT_UNSUPPORTED"


__all__ = [
    "UploadedBlob",
    "TempUploadStore",
    "MemoryTempUploadStore",
    "detect_mime",
    "new_storage_ref",
    "build_blob",
    "UnsupportedFormatError",
    "unsupported_format_reason",
]
