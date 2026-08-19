"""Skill 产品 API 服务层与受控适配层（14 号计划 §97.4 P2-E4）。

开发态使用受控 fake 适配器（与 P2-E1 `FakeControlPlane` 同模式）：
真实 OpenViking 内容/迁移/私密配置存储在 P5-E1 接线真实实现。
"""

from openviking.server.platform.skills.configs import (
    FakeSkillConfigsAdapter,
    SkillConfigsAdapter,
    SkillConfigSnapshot,
)
from openviking.server.platform.skills.control_plane import (
    FakeSkillContentAdapter,
    FakeSkillMigrationAdapter,
    SkillContentAdapter,
    SkillMigrationAdapter,
)
from openviking.server.platform.skills.packages import (
    FakeSkillPackageAdapter,
    ParsedSkill,
    SkillPackageAdapter,
)
from openviking.server.platform.skills.publish_worker import SkillPublishWorker
from openviking.server.platform.skills.service import (
    EVENT_SKILL_PUBLISH,
    PublishResult,
    SkillService,
)

__all__ = [
    "EVENT_SKILL_PUBLISH",
    "FakeSkillConfigsAdapter",
    "FakeSkillContentAdapter",
    "FakeSkillMigrationAdapter",
    "FakeSkillPackageAdapter",
    "ParsedSkill",
    "PublishResult",
    "SkillConfigsAdapter",
    "SkillConfigSnapshot",
    "SkillContentAdapter",
    "SkillMigrationAdapter",
    "SkillPackageAdapter",
    "SkillPublishWorker",
    "SkillService",
]
