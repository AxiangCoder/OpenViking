"""Skill 私密配置产品后端（05 §12.5 三接口，10 §53.3，08 §28.1）。

- 仅当前 User 读写自己的配置（`privacy_config.read/write.self` + Skill
  read/use）；发布不迁移、不共享、不删除 User 私密配置（10 §52.16）；
- 存储侧保留完整版本历史（适配层语义对应 `UserPrivacyConfigService`
  upsert/activate_version：内容变化才递增版本，重复提交返回当前版本）；
- DTO 层脱敏：只返回配置状态与掩码值，不返回可恢复 Secret（AC⑨）。

开发态实现为受控 fake（内存存储，与 P2-E1 `FakeControlPlane` 同模式）；
真实实现按 `SkillConfigsAdapter` Protocol 对接 `viking://user/**/privacy`
（`openviking/privacy/service.py`），P5-E1 接线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

_MASK = "••••••••"


def mask_value(value: str) -> str:
    """脱敏展示值：固定掩码，不携带任何可恢复信息（AC⑨）。"""
    return _MASK if value else ""


@dataclass(frozen=True)
class SkillConfigSnapshot:
    """脱敏配置快照（返回给产品 DTO；不含任何 Secret 明文）。"""

    skill_id: str
    configured: bool
    active_version: int | None
    latest_version: int | None
    versions: list[int] = field(default_factory=list)
    masked_values: dict[str, str] = field(default_factory=dict)

    def dto(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "configured": self.configured,
            "active_version": self.active_version,
            "latest_version": self.latest_version,
            "versions": list(self.versions),
            "values": [
                {"key": key, "configured": True, "value_masked": masked}
                for key, masked in sorted(self.masked_values.items())
                if masked
            ],
        }


class SkillConfigsAdapter(Protocol):
    """Skill 私密配置存储适配层（仅当前 User 的私有空间）。"""

    async def get(self, *, owner_ov_user_id: str, skill_id: str) -> SkillConfigSnapshot | None: ...

    async def upsert(
        self,
        *,
        owner_ov_user_id: str,
        skill_id: str,
        values: dict,
        updated_by: str,
        change_reason: str = "",
    ) -> SkillConfigSnapshot: ...

    async def activate(
        self,
        *,
        owner_ov_user_id: str,
        skill_id: str,
        version: int,
        updated_by: str,
    ) -> SkillConfigSnapshot: ...


class FakeSkillConfigsAdapter:
    """开发态适配层：内存 {(owner_ov_user_id, skill_id): 配置}。

    语义对齐 `UserPrivacyConfigService`：内容规范化相等则复用当前版本
    （不产生空版本），否则递增 `latest_version`；activate 校验版本存在。
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict] = {}

    def _key(self, owner_ov_user_id: str, skill_id: str) -> tuple[str, str]:
        return (owner_ov_user_id, skill_id)

    def _snapshot(self, owner_ov_user_id: str, skill_id: str, entry: dict) -> SkillConfigSnapshot:
        active = entry.get("active_version")
        latest = entry.get("latest_version")
        values = entry.get("values") or {}
        return SkillConfigSnapshot(
            skill_id=skill_id,
            configured=True,
            active_version=active,
            latest_version=latest,
            versions=entry.get("versions") or [],
            masked_values={k: mask_value(v) for k, v in values.items()},
        )

    async def get(self, *, owner_ov_user_id: str, skill_id: str) -> SkillConfigSnapshot | None:
        entry = self._store.get(self._key(owner_ov_user_id, skill_id))
        if entry is None:
            return None
        return self._snapshot(owner_ov_user_id, skill_id, entry)

    async def upsert(
        self,
        *,
        owner_ov_user_id: str,
        skill_id: str,
        values: dict,
        updated_by: str,
        change_reason: str = "",
    ) -> SkillConfigSnapshot:
        if not isinstance(values, dict) or not values:
            raise ValueError("privacy values must be a non-empty dict")
        key = self._key(owner_ov_user_id, skill_id)
        entry = self._store.setdefault(
            key,
            {"values": {}, "active_version": None, "latest_version": 0, "versions": []},
        )
        if entry["values"] == values:
            return self._snapshot(owner_ov_user_id, skill_id, entry)
        version = entry["latest_version"] + 1
        entry["values"] = dict(values)
        entry["active_version"] = version
        entry["latest_version"] = version
        entry["versions"] = entry.get("versions") or []
        entry["versions"].append(version)
        entry["updated_by"] = updated_by
        return self._snapshot(owner_ov_user_id, skill_id, entry)

    async def activate(
        self,
        *,
        owner_ov_user_id: str,
        skill_id: str,
        version: int,
        updated_by: str,
    ) -> SkillConfigSnapshot:
        key = self._key(owner_ov_user_id, skill_id)
        entry = self._store.get(key)
        if entry is None or version not in (entry.get("versions") or []):
            raise ValueError(f"privacy config version not found: {version}")
        entry["active_version"] = version
        entry["updated_by"] = updated_by
        return self._snapshot(owner_ov_user_id, skill_id, entry)
