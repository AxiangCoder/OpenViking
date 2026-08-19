"""规范化产品标签校验（04 §10.10，05 §12.5，14 号计划 §97.2）。

Resource、Skill 与 Search 共用 OpenViking 的结构化标签语义：
- 每项必须恰好包含一个 `=`，key/value 均非空；
- 去除首尾空白后整体转小写并去重；
- 每项最多 40 字符、每个对象最多 20 项（20/40 上限是产品新增约束，
  引擎本身无此限制，由 Product Facade/Content Registry 强制）。

Skill Frontmatter 中的 `tags` 与客户端提交的 `tags` 走同一校验
（10 §61.5），前端不能直接调用底层 `set_tags` 形成双写分叉。
"""

from __future__ import annotations

from openviking.server.platform.errors import TagValidationError

MAX_TAGS = 20
MAX_TAG_LENGTH = 40


def normalize_tags(tags: list | tuple | None) -> list[str]:
    """校验并规范化 tags（04 §10.10，AC：05 §12.5 三接口统一）。

    Raises:
        TagValidationError: 数量/长度/格式非法（`reason` 为稳定码）。
    """
    if not tags:
        return []
    if not isinstance(tags, (list, tuple)):
        raise TagValidationError("TAG_INVALID_FORMAT")
    if len(tags) > MAX_TAGS:
        raise TagValidationError("TAG_LIMIT_EXCEEDED")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            raise TagValidationError("TAG_INVALID_FORMAT")
        item = raw.strip().lower()
        if len(item) > MAX_TAG_LENGTH:
            raise TagValidationError("TAG_TOO_LONG")
        if "=" not in item:
            raise TagValidationError("TAG_INVALID_FORMAT")
        key, _, value = item.partition("=")
        if not key.strip() or not value.strip():
            raise TagValidationError("TAG_INVALID_FORMAT")
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized
