"""Resource 远程来源安全校验（09 §40.6，SSRF/私网/凭证/重定向防护）。

产品入口规则（09 §40.6，服务端强制，AC⑩）：

- 默认只接受 HTTPS；是否允许 HTTP 由部署策略（`OV_HTTP_SOURCES_ALLOWED`）决定，
  前端不能自行放宽；
- 禁止 `file://`/`ftp://`、localhost、Loopback、Link-local、私网地址、
  CGNAT、云元数据地址（169.254.169.254）与 DNS Rebinding（可插拔解析钩子）；
- URL 禁止 `username:password@host`；
- 含 Query 的 URL 只能一次性导入（`stable=False`），不能 Refresh/Watch；
  稳定 URL 必须无 Userinfo、Query、Fragment；
- Git v0.1 只允许公开 HTTPS Repository URL；SSH、`git@` 与认证参数被拒；
- 每次 Redirect 都重新执行目标校验（`check_redirect`），并限制次数。

`fingerprint` 只包含 scheme://host[:port]/path 的规范化哈希，不含 Query，
供审计与幂等使用（09 §40.6：日志/审计只记录脱敏 Host/Path 与 fingerprint）。
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from openviking.server.platform.errors import ResourceError, ResourceSourceBlockedError

MAX_REDIRECTS = 5
MAX_HOST_LABEL_LENGTH = 63

# 云元数据地址与链路本地（09 §40.6 明列禁止）
_CLOUD_METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})

# Git 来源 host 黑名单（不可变宿主；v0.1 只允许公开 HTTPS 仓库）
_BLOCKED_GIT_HOSTS = frozenset()

# 高危/不可内联预览的扩展名（09 §42.3 下载安全；执行文件不内联）
EXECUTABLE_EXTENSIONS = frozenset(
    {".exe", ".msi", ".dll", ".so", ".dylib", ".bin", ".sh", ".bat", ".cmd", ".com", ".app", ".dmg", ".pkg", ".pyc", ".apk", ".jar"}
)

# v0.1 产品上传默认不接受归档包（09 §47.3）。
#
# 说明（P2-E4 收尾，10 §61.5）：`me/resource-uploads`/`account/resource-uploads`
# 是 Resource 与 Skill 共用的受控暂存入口——Skill 的 ZIP 包必须能经该入口
# 暂存（不新增 skill-uploads 端点）。因此暂存阶段放行 `.zip`（Skill 包唯一
# 需要的归档格式），Resource 导入/替换在消费侧按 `is_archive_filename`
# 拒绝全部归档（含 `.zip`），保持 09 §47.3 的 Resource 语义不变。
BLOCKED_UPLOAD_EXTENSIONS = frozenset(
    {".tar", ".tgz", ".gz", ".bz2", ".xz", ".rar", ".7z", ".tar.gz"}
)

# Resource 消费侧拒绝的归档扩展名（09 §47.3；含 .zip，覆盖暂存放行后
# 被 Resource 入口消费的情形）
RESOURCE_BLOCKED_ARCHIVE_EXTENSIONS = frozenset(
    {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".rar", ".7z", ".tar.gz"}
)


def is_archive_filename(name: str) -> bool:
    """文件名是否为 Resource 消费侧拒绝的归档（09 §47.3）。"""
    dot = (name or "").lower().rfind(".")
    if dot <= 0:
        return False
    return (name or "").lower()[dot:] in RESOURCE_BLOCKED_ARCHIVE_EXTENSIONS


@dataclass(frozen=True)
class SourceValidation:
    """远程来源校验结果（仅存脱敏值，09 §40.6）。"""

    kind: str  # web | git
    display: str  # 脱敏展示值（无 Query/Userinfo/Fragment）
    fingerprint: str  # scheme://host/path 规范化 SHA-256（64 hex）
    stable: bool  # 无 Userinfo/Query/Fragment → 可 Refresh/Watch
    canonical_url: str  # 规范化 URL（不含 Query/Fragment/Userinfo）

    @property
    def error_summary(self) -> str:
        return self.display


def _host_is_blocked(hostname: str) -> str | None:
    """返回拒绝原因码；None 表示允许。hostname 已小写。"""
    host = hostname.rstrip(".").lower()
    if not host:
        return "EMPTY_HOST"
    if host in _CLOUD_METADATA_HOSTS:
        return "CLOUD_METADATA"
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return "LOCALHOST"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback:
            return "LOOPBACK"
        if ip.is_link_local:
            return "LINK_LOCAL"
        if ip.is_private:
            return "PRIVATE_IP"
        if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return "RESERVED_IP"
        if ip.is_global and int(ip) in _CGNAT_RANGE:
            return "PRIVATE_IP"
        return None
    if any(label == "localhost" or label.endswith(".localhost") for label in host.split(".")):
        return "LOCALHOST"
    if len(host.split(".")) == 1:
        # 单标签主机名（不含点）不可解析为公网 FQDN
        return "LOCALHOST"
    return None


# 100.64.0.0/10 CGNAT（RFC 6598，非公网可路由）
_CGNAT_RANGE = range(int(ipaddress.ip_address("100.64.0.0")), int(ipaddress.ip_address("100.127.255.255")) + 1)


class RemoteSourcePolicy:
    """远程来源安全策略（09 §40.6；`dns_resolver` 钩子用于 DNS Rebinding 防护）。

    `dns_resolver(hostname) -> list[str]`：可选；设置后每次校验都把主机名
    解析出的 IP 逐一执行同一私网/元数据检查（防 DNS Rebinding）。
    """

    def __init__(
        self,
        *,
        http_allowed: bool = False,
        dns_resolver: Callable[[str], list[str]] | None = None,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._http_allowed = http_allowed
        self._dns_resolver = dns_resolver
        self._max_redirects = max_redirects

    # ── 统一入口 ──

    def validate(self, url: str, *, kind: str) -> SourceValidation:
        """校验公开 HTTPS 远程来源（09 §40.6）。

        Args:
            url: 客户端提交的原始 URL；
            kind: web | git。

        Raises:
            ResourceError: `RESOURCE_SOURCE_UNSUPPORTED`（协议/格式不支持）；
            ResourceSourceBlockedError: `RESOURCE_SOURCE_BLOCKED`（安全拒绝）。
        """
        normalized = self._parse(url, kind=kind)
        return self._to_validation(normalized)

    def check_redirect(self, url: str, *, kind: str, depth: int = 0) -> None:
        """每次 Redirect 重新执行目标校验（09 §40.6）；超过次数即拒。"""
        if depth >= self._max_redirects:
            raise ResourceSourceBlockedError()
        self.validate(url, kind=kind)

    # ── 内部 ──

    def _parse(self, url: str, *, kind: str) -> dict:
        if not isinstance(url, str) or not url.strip():
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
        try:
            parsed = urlparse(url.strip())
        except ValueError as exc:
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED") from exc
        scheme = (parsed.scheme or "").lower()
        if kind == "git" and scheme != "https":
            # Git v0.1 只允许公开 HTTPS Repository URL；SSH、git@ 被拒（09 §40.6）
            raise ResourceSourceBlockedError()
        if scheme == "https":
            pass
        elif scheme == "http" and self._http_allowed:
            pass
        elif scheme in (
            "file",
            "ftp",
            "sftp",
            "ssh",
            "git",
            "data",
            "javascript",
            "ws",
            "wss",
            "http",
        ):
            # HTTP（部署策略未放开）与其他协议同为安全拒绝（09 §40.6）
            raise ResourceSourceBlockedError()
        else:
            raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
        if parsed.username is not None or parsed.password is not None:
            # URL 禁止 username:password@host（09 §40.6）
            raise ResourceSourceBlockedError()
        if parsed.hostname is None:
            raise ResourceSourceBlockedError()
        host = parsed.hostname.lower()
        blocked = _host_is_blocked(host)
        if blocked is not None:
            raise ResourceSourceBlockedError()
        if self._dns_resolver is not None:
            for ip in self._dns_resolver(host):
                if _host_is_blocked(ip) is not None:
                    raise ResourceSourceBlockedError()

        path = parsed.path or "/"
        # 归一化路径：去多余斜杠、`.` 段（09 §40.6 确定性规范化）
        path = "/" + "/".join(seg for seg in path.split("/") if seg and seg not in (".", ".."))
        port = parsed.port
        host_display = host if port is None else f"{host}:{port}"

        if kind == "git":
            if scheme == "http":
                raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
            if parsed.query:
                raise ResourceError("RESOURCE_SOURCE_UNSUPPORTED")
            if host in _BLOCKED_GIT_HOSTS:
                raise ResourceSourceBlockedError()

        canonical = f"{scheme}://{host_display}{path}" if port is None else f"{scheme}://{host}:{port}{path}"
        stable = not parsed.query and not parsed.fragment
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            "scheme": scheme,
            "host": host_display,
            "path": path,
            "canonical": canonical,
            "query": parsed.query,
            "stable": stable,
            "fingerprint": fingerprint,
            "kind": kind,
        }

    def _to_validation(self, normalized: dict) -> SourceValidation:
        display = normalized["canonical"]
        if not normalized["stable"]:
            display = f"{display}?<query>"
        return SourceValidation(
            kind=normalized["kind"],
            display=display,
            fingerprint=normalized["fingerprint"],
            stable=normalized["stable"],
            canonical_url=normalized["canonical"],
        )


def sanitize_error_summary(summary: str) -> str:
    """脱敏错误摘要：只保留前 200 字符并去除可能泄露的换行/引用。"""
    cleaned = re.sub(r"[\r\n\t]+", " ", summary or "").strip()
    return cleaned[:200]


def is_executable_extension(name: str) -> bool:
    """下载响应安全策略：可执行/高风险类型不内联（09 §42.3）。"""
    dot = name.lower().rfind(".")
    if dot <= 0:
        return False
    return name.lower()[dot:] in EXECUTABLE_EXTENSIONS
