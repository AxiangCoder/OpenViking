"""远程来源应用层加密（09 §47.3/04 §10.10）。

- 稳定远程来源以应用层密文保存于 `platform_content_refs.source_locator_ciphertext`；
- 明文不能进入 Outbox、QueueFS、Watch JSON、日志或审计（09 §47.3）；
- `key_version` 记录当前加密密钥版本；密钥来自配置/Secret Manager；
- 含 Query 的一次性 URL 只在导入 Operation 期间加密保存、任务终态后清除
  （v0.1 由导入流程持有，Watch/Refresh 永不使用）。

v0.1 开发态使用配置密钥做对称加密（Fernet 兼容实现的独立加密类，
生产可替换为 KMS Envelope Encryption，密钥版本列已预留）。
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


class SourceLocatorCipher:
    """远程来源密文编解码（应用层，key version 由调用方记录）。"""

    def __init__(self, key_material: str) -> None:
        digest = hashlib.sha256(key_material.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))
        self.key_version = "v1"

    def encrypt(self, url: str) -> str:
        return self._fernet.encrypt(url.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("source locator ciphertext invalid") from exc

    def new_one_time_locator(self, url: str) -> tuple[str, str]:
        """一次性 URL 的短期密文（导入终态后由调用方清除，09 §40.6）。"""
        return self.encrypt(url), self.key_version


def fingerprint_of(text: str) -> str:
    """URL fingerprint（09 §40.6：审计只记录 fingerprint 不记录完整来源）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_dev_key() -> str:
    return os.urandom(32).hex()
