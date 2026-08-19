"""P5-E1 真实 OpenViking 适配器接线（14 号计划 §99.1，Spike 风险 9 生产配置态闭合）。

此前各 Epic 以受控 Fake 适配层交付（FakeControlPlane、FakeResourceExecutionPlane、
FakeSkillContentAdapter、FakeSkillMigrationAdapter、FakeSkillConfigsAdapter、
FakeSessionBackend、FakeSearchEngine、NoopPurgeHandler 等）；本包按各 Protocol
提供对接 OpenViking 真实服务的实现：

- `RealControlPlaneAdapter`：namespace 初始化（OpenVikingService
  initialize_account_directories / initialize_user_directories）；
- `RealSkillConfigsAdapter`：Skill 私密配置（UserPrivacyConfigService）；
- `RealSearchEngine`：语义检索（SearchService.find/search）；
- `RealSessionBackend`：Session 存储（SessionService/Session）；
- `RealSkillContentAdapter`/`RealSkillMigrationAdapter`：VikingFS 文件操作
  （私有根；共享根物理布局需部署确认，见各模块 docstring）；
- `RealResourcePurgeHandler`：Resource 期满物理清理（VikingFS.rm，含向量索引）。

所有真实适配器**惰性**解析运行时：构造时不接触 OpenVikingService；每次调用
经 `service_provider`（默认 `get_service_or_none`，create_app lifespan 中
`set_service` 注册）获取真实服务。运行时缺失/未初始化时抛
`AdapterRuntimeUnavailable`（映射 503）。默认行为不破坏既有测试与开发态
（mount 默认仍装配 Fake；staging/生产配置模板显式切换 `platform_adapter_mode=real`）。

**P5-E1 未接线项**（供 P5-E4 门禁评审）：
- `ResourceExecutionPlane`：真实摄取管线（上传/网页/Git → ResourceProcessor）
  依赖完整运行时（VectorDB/Embedder/AGFS 抓取）与任务编排，产品脱敏语义
  与底层 Task 解耦，保留 Fake；
- `SkillContentAdapter` 的**共享根**（viking://agent/skills/...）：v0.4.12
  文件层按 account 隔离，"Account 共享"内容落在发布方 Account 的
  namespace；内容写入协议不携带 Actor Account，共享根写入无法确定物理
  归属，真实实现仅覆盖私有根并显式报错（避免写错位置）。
  发布迁移（`RealSkillMigrationAdapter`）以源私有根账号为准完成私有→共享
  迁移，已接线。
"""

from __future__ import annotations

from openviking.server.platform.adapters.control_plane import RealControlPlaneAdapter
from openviking.server.platform.adapters.purge import RealResourcePurgeHandler
from openviking.server.platform.adapters.search import RealSearchEngine
from openviking.server.platform.adapters.session_backend import RealSessionBackend
from openviking.server.platform.adapters.skill_configs import RealSkillConfigsAdapter
from openviking.server.platform.adapters.skills import (
    RealSkillContentAdapter,
    RealSkillMigrationAdapter,
)

__all__ = [
    "RealControlPlaneAdapter",
    "RealSkillConfigsAdapter",
    "RealSkillContentAdapter",
    "RealSkillMigrationAdapter",
    "RealSearchEngine",
    "RealSessionBackend",
    "RealResourcePurgeHandler",
]
