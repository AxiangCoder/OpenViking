# OpenViking 产品化平台 Design v0.1

> 状态：Design Frozen（标签 `design-v0.1.0`，2026-08-18 重新冻结）<br>
> 上游源码基线：OpenViking v0.4.12<br>
> 基线提交：`c1d38eb47ff2ebf9ff4cee46756728893fd8caf3`<br>
> 目标读者：产品负责人、前端工程师、后端工程师、运维与安全负责人

## 设计推进原则

- OpenViking v0.4.12 源码已有且符合已确认产品边界的能力，直接复用并写入设计，不逐项重复确认。
- 只有源码没有对应能力、源码行为与产品规则冲突，或复用会改变已确认的权限/业务语义时，才提出产品决策问题。

## 文档导航

1. [产品定位、术语与源码基线](01-product-positioning-and-baseline.md)
2. [目标架构与身份上下文](02-architecture-and-identity.md)
3. [认证与授权](03-authentication-and-authorization.md)
4. [PostgreSQL 数据模型](04-data-model.md)
5. [后端模块与 API](05-backend-and-api.md)
6. [前端、安全、初始部署与运维](06-frontend-security-and-operations.md)
7. [测试、实施边界与架构决策](07-quality-rollout-and-decisions.md)
8. [产品能力归属设计](08-product-capability-ownership.md)
9. [Resource 页面与产品契约](09-resource-product-contract.md)
10. [Skill 页面与产品契约](10-skill-product-contract.md)
11. [Memory、Search 与 Session 产品契约](11-memory-search-session-product-contract.md)
12. [设计审查问题清单](12-design-review-issues.md)
13. [管理与个人设置产品契约](13-admin-profile-product-contract.md)
14. [开发计划（Development Plan）](14-development-plan.md)

## 已确认设计决策

- OpenViking 作为上下文与记忆引擎，产品平台采用模块化单体。
- 新建产品前端与管理界面；现有 `/studio` 仅作为可选的旧运维入口保留，不属于正式产品页面，生产公网默认不挂载。
- 浏览器登录使用服务端登录 Session，不使用或持久化 Root/User API Key；用户主动创建 API Key 时只展示一次明文，随后由用户保存到集成客户端。
- 权限层级包含 Platform Super Admin、Account Admin 和 User。
- 邮箱是必填且全局唯一的登录标识，一个 User 只属于一个 Account。
- Platform Super Admin 创建 Account 和首位 Account Admin；Account Admin 直接创建本 Account 的普通 User，不提供注册、邀请或激活流程。
- 产品网页登录只支持本地邮箱和密码；不提供 OIDC、企业微信登录或其他企业单点登录，也不列入后续版本计划。
- 创建用户或由上级重置密码时，系统生成可复制的初始密码并只展示一次；该密码可长期使用，用户不被强制修改，由管理员在线下自行交接。
- 管理员只能重置严格低级别用户的密码，不能重置同级；重置后撤销目标用户全部登录 Session，但不删除 OpenViking 对话 Session，也不自动撤销 API Key。
- 管理员跨用户读取必须同时保留 Actor（操作者）与 Subject（数据归属者）。
- 高风险操作使用展示影响范围的确认弹窗，不要求重输密码、输入 Account 名称或双人审批。
- Account、用户及其数据使用 30 天软删除与恢复窗口，期满后异步物理清理。
- SDK、CLI、插件和 MCP 使用用户 API Key 或用户 OAuth Token，均代表授权用户本人，并实时继承同一套 RBAC 与数据范围。
- 业务数据不使用含糊的“公共/私有”表述，统一分为 **Account 共享数据** 与 **User 私有数据**；共享只表示同一 Account 内可见，不表示互联网公开或跨 Account 可见。
- `viking://resources/**` 是 Account 共享 Resource；`viking://user/{ov_user_id}/resources/**` 是 User 私有 Resource。普通 User 默认新增到自己的私有区，只读 Account 共享 Resource；Account Admin 管理本 Account 共享 Resource。
- `viking://agent/skills/**` 是 Account 共享 Skill；`viking://user/{ov_user_id}/skills/**` 是 User 私有 Skill。普通 User 可读取和使用共享 Skill，但只能管理自己的私有 Skill；Account Admin 管理本 Account 共享 Skill，并可把本 Account 任意 User 的私有 Skill 原地发布为共享 Skill。
- 同一 Account 内所有未删除 Skill 的名称全局唯一且创建后不可修改；软删除立即释放名称，恢复时若已被同名占用则失败。
- Skill 发布保持产品 ID 和名称不变，直接把归属与 URI 从 User 私有转换为 Account 共享，不保留副本且不支持取消发布；普通 User 和 Platform Super Admin 均无发布权限。
- Platform Super Admin 对全平台 Skill 只有读取权限，不能创建、上传、编辑、发布、删除、恢复或使用 Skill。
- Skill 页面支持在线创建和上传 `SKILL.md`/ZIP；Skill 由 Codex、其他 Agent、插件或 MCP 按权限发现和使用，网页不提供“在新 Session 中使用”或独立 Skill Runner。
- v0.1 不建立独立 Memory 页面或 Memory CRUD API；Memory 继续由 OpenViking 在 Session Commit 后提取和更新，用户通过 `/app/search` 检索，并在 Session 中查看本次 Commit 的 Memory Impact。
- OpenViking 是 Session、上下文和记忆服务；实际对话发生在 Codex、其他 Agent 或可选 VikingBot 中，并通过插件、MCP、SDK 或 API 同步。VikingBot 不是 v0.1 必选组件。
- `/app/search` 提供“快速检索”和“结合会话检索”两个产品模式，分别复用源码 `find` 与 `search`；`recall` 只供 Agent/插件/MCP 等调用链使用，`grep/glob` 只留私网 Studio。
- Search 基础筛选只有内容类型；结合会话检索额外选择当前 User 自己的 Session。结构化标签和更新时间范围放入“更多筛选”；时间固定按 `updated_at`，分数阈值、索引层级、来源追踪、结果数量和自定义 URI 均由后端控制，不进入产品 UI。
- Resource、Skill 与 Search 统一使用源码兼容的 `key=value` 结构化标签；标签规范化为小写并去重，多个检索标签采用 AND 关系，不建立普通标签到检索标签的转换层。
- Search 结果复用 Studio 的“结果列表 + 右侧详情抽屉”交互；Resource/Skill 跳转产品详情，Memory 只在抽屉显示摘要和匹配原因。产品页面不显示 URI、分数、索引层级、检索计划、来源追踪或原始 JSON。
- Search 结果卡片不额外补查标签和更新时间；当前筛选条件显示在结果区上方，Resource/Skill 的完整元数据进入详情页查看。
- `/app/sessions` 复用 Studio 的双栏浏览、消息展示、历史归档加载和 Memory Impact，但删除 Composer、SSE 和停止生成；Session Create/Append/Commit 由插件、MCP、SDK、CLI、API 或可选 VikingBot 完成。
- `/api/platform/v1`、`/api/v1`、MCP、SDK、CLI 和插件必须经过同一个后端授权门；换一种调用渠道不能扩大权限，也不能绕过上述共享/私有规则。
- 产品能力归属、业务数据归属、控制责任和调用渠道分开建模；源码 Router 不等于正式产品能力，`/app`、`/admin`、`/platform` 与 `/studio` 的边界由能力目录决定。
- MCP OAuth 属于用户委托型集成，不是 OIDC 登录；同意页和跨设备验证页迁到 `web-platform` 的 `/oauth/consent`、`/oauth/verify`，使用产品登录 Session，不依赖 Studio 或浏览器内 API Key。
- Watch/自动同步作为 Resource 详情子功能，Task 通过 Activity 展示；Relations/Graph 只作为 Search/Session/Context 的内部能力。
- Resource 产品页面不暴露 Viking URI 或底层文件写入；支持文件、公开 HTTPS 页面和公开 HTTPS Git，解析内容只读，Refresh 期间继续提供上一次成功版本。
- Account Admin 只能把自己的私有 Resource 发布为新的 Account 共享副本，不能把其他 User 私有数据直接发布到共享区。
- Snapshot、Pack、Backup、Import、Restore 只留私网运维；当前 WebDAV 共享根写入口在 v0.1 生产禁用。
- v0.1 不提供 Service Account 或 Service Account Key；内部 Worker 使用不对外签发凭证的系统身份。
- v0.1 是产品初版，不导入或兼容旧 Account/User/API Key 门禁；现有协议入口可被选为首版契约，但鉴权统一接入新 IAM。

## 变更记录

| 日期 | 修订 | 说明 |
| --- | --- | --- |
| 2026-08-18 | Design v0.1 初稿 | 基于 OpenViking v0.4.12 建立产品化、IAM 与 RBAC 总体设计。 |
| 2026-08-18 | Design v0.1 补充 | 明确三级数据范围、Actor/Subject 审计、弹窗确认与 30 天软删除。 |
| 2026-08-18 | Design v0.1 凭证修订 | 明确插件/MCP 为用户委托型集成，用户 API Key 统一继承 RBAC；Service Account 和旧门禁兼容均不进入 v0.1。 |
| 2026-08-18 | Design v0.1 用户创建与密码修订 | 取消注册和邀请流程；明确管理员直建用户、长期初始密码手工交接、禁止同级重置及登录 Session 撤销。 |
| 2026-08-18 | Design v0.1 数据可见性修订 | 根据 v0.4.12 源码核对 Resource/Skill 命名空间；明确 Account 共享与 User 私有边界、默认写入位置及全入口统一授权。 |
| 2026-08-18 | Design v0.1 登录方式收敛 | 删除 OIDC、企业微信和企业单点登录设计及后续计划；产品网页登录固定为本地邮箱密码。 |
| 2026-08-18 | Design v0.1 Studio 边界收敛 | 正式能力统一进入 `/app`、`/admin`、`/platform`；`/studio` 降级为可选的私网维护入口，生产公网默认不挂载。 |
| 2026-08-18 | Design v0.1 能力归属收敛 | 完成 Studio、REST、MCP、SDK/CLI 能力盘点；Watch 纳入 Resource 子功能，Relations 保持内部能力，Snapshot/Pack 与 WebDAV 不进入公网产品；MCP OAuth 授权页迁出 Studio。 |
| 2026-08-18 | Design v0.1 Resource 契约收敛 | 明确 Resource 列表、详情、导入来源、异步状态、Watch、发布、删除恢复、安全边界和 Product API。 |
| 2026-08-18 | Design v0.1 Skill 契约收敛 | 明确 Skill 创建上传、Account 全局名称唯一、角色权限、原地发布、整体替换、Agent 接入使用和删除恢复边界。 |
| 2026-08-18 | Design v0.1 Memory 与 Session 收敛 | 取消独立 Memory 页面和 CRUD；Memory 通过 Search 与 Session Impact 查看；VikingBot 是可选接入方，不承担产品网页聊天。 |
| 2026-08-18 | Design v0.1 Search 模式收敛 | 产品页保留快速检索与结合会话检索；Recall 留给调用链，Grep/Glob 留在私网 Studio。 |
| 2026-08-18 | Design v0.1 Search 筛选收敛 | 类型作为基础筛选，标签与时间作为更多筛选；隐藏分数、层级、来源追踪、数量和 URI 等调试参数。 |
| 2026-08-18 | Design v0.1 标签模型收敛 | Resource、Skill 与 Search 统一采用 `key=value` 结构化标签，多标签检索要求全部匹配。 |
| 2026-08-18 | Design v0.1 Search 时间语义收敛 | 时间范围固定按 `updated_at` 筛选，页面不提供创建时间/更新时间切换。 |
| 2026-08-18 | Design v0.1 源码复用原则与 Search 结果 | 明确已有合规能力直接复用；Search 复用列表和详情抽屉，同时移除引擎诊断字段。 |
| 2026-08-18 | Design v0.1 Memory/Search/Session 详细契约 | 完成页面、API、权限、Chat Stream、自动 Commit、Memory Impact、软删除、错误与验收规则；Bot Session/Skill 绑定与服务端标题源码冲突已消解：不展示标题、VikingBot 不承担网页聊天。 |
| 2026-08-18 | Design v0.1 一致性修订 | 移除 Platform Super Admin 代建普通 User（普通 User 仅由 Account Admin 创建）；Session 删除弹窗不展示标题；回收站恢复权限按对象类型补全权限 code；Skill 契约 API 表格格式修正。 |
| 2026-08-18 | Design v0.1 权限矩阵对齐 | Account 删除「停用」（suspended 状态保留、无产品操作端点）；PSA 管理用户收窄到现有 API（仅查看、密码重置、角色提升、凭据管理）；修改/导出/删除他人私有 Resource 改为预留独立高风险权限、v0.1 不提供端点；06 §14.6、08 §29.3、09 §38.2、01 §4.3 同步对齐。 |
| 2026-08-18 | Design v0.1 审查修订 | 19 项设计审查问题全部处置（详见 12 号清单）：Session 不展示标题；Skill 发布采用受控 mv 迁移任务（10 §58.4）；改密要求旧密码；权限缓存增加全局版本；回收站按对象类型校验恢复；内置角色全局单行；标签 20/40 限制归产品层；Retention 默认 12000 Token；11 号文档重编号 §66-78。 |
| 2026-08-18 | Design v0.1 管理与个人设置契约 | 新增 13 号文档：认证、个人设置、API Key、MCP OAuth 连接、管理后台与平台管理页面级契约；记录 3 个冻结前缺口（Account Provisioning 重试 API、个人 Session 列表 API、/admin/settings 占位）。 |
| 2026-08-18 | Design v0.1 冻结 | 设计审查 19 项全部闭合（12 号清单）；13 号文档引用按 11 号重编号校正；状态改为 Design Frozen，标签 `design-v0.1.0`。 |
| 2026-08-18 | Design v0.1 冻结解除 | 按用户要求解除冻结，状态回到讨论中；标签 `design-v0.1.0` 已删除。 |
| 2026-08-18 | Design v0.1 重新冻结 | 闭合 13 号文档 3 个冻结前缺口：05 §12.6 补 Provisioning 重试接口（13 §89.2 同步页面动作）、`/admin/settings` 定为仅展示 Account 基本信息占位（06 §13.2 标注）、Session 列表维持仅 logout-all 决策；按源码核对修正 5 处引用偏差（01 §4.1/§4.2、08 §28.1、10 §51、12 号行号）；状态改回 Design Frozen，打标签 `design-v0.1.0`。 |
| 2026-08-18 | Design v0.1 角色等级回填 | 技术验证发现：04 §10.4 角色表补 `rank` 字段（3/2/1）；03 §8.3 明确等级比较只用平台 `iam_roles.rank`，禁止混用 OpenViking `Role` 内置 rank（0/1/2）；03 §9.4 声明 OpenViking 原始角色权限体系不改动、平台 RBAC 为唯一业务授权来源。 |
| 2026-08-18 | Design v0.1 技术验证回填 | 03 §8.4 补凭证优先级语义（Cookie 优先、失效不自动回退 Bearer）；05 §12.3 补改密轮换必须同步 Set-Cookie 的实现约束；03 §9.3 补 Platform 角色权限不能继承 Account Admin、必须剔除 Skill 写/用权限。 |
| 2026-08-18 | Development Plan v0.1 | 新增 14 号文档：基于冻结设计与 Spike 结论拆分 Phase 1–5 共 24 个 Epic（含范围/依赖/验收/设计章节/Plane 字段），附测试策略映射与 07 §21 26 条验收归属表。 |
| 2026-08-18 | Development Plan Phase 1 拆分审查修正 | 14 号文档 §95/§96 修正：Phase 1 门禁改「verify.py 除 `==5`、`==12` 外」；P1-E5 范围依赖放宽至 E1–E3（门禁依赖需 E4 单独注明）；登录/权限拒绝/种子变更审计写入归属明确（直写 E1 repository）；Session cleanup worker 归 P1-E3；提权不轮换 Session 语义消解；`==1` 覆盖范围与 `==4` CSRF 断言如实标注；P1-E3 Out 修正 MCP OAuth 归属；§94 交叉引用与 §100.2 行 8 摘要修正。 |
| 2026-08-18 | Development Plan Phase 2 拆分审查修正 | 14 号文档 §95/§97/§100/§101/§102 修正：执行序统一 P2-E1→E2→（E3‖E4‖E5‖E6b）→E6a；拆 P2-E6a/E6b（Epic 数 24→25，Plane 导入同步）；E2 补 OAuth Token→Principal 解析与管理后端归属（User deletion-preview/DELETE、admin/platform 聚合 recycle-bin/activity/monitoring）；E3 补 admin 成员只读 Resource、search/deletion-preview、`/activity`/`/recycle-bin` 归属与两条验收；E4 补平台只读 Skill、skill-configs 后端（与 P3-E5 矛盾消解）与 P2-E1 依赖；E5 补 dashboard 与 P2-E1 依赖；低层入口 IAM 空窗条款入 §101。 |
| 2026-08-18 | Development Plan Phase 3 拆分审查修正 | 14 号文档 §95/§98/§100.1/§101 修正：跨页面跳转契约在 P3-E1 冻结、E3 跳详情子项收口阶段联合验收；P3-E2 OAuth 子项↔P2-E6b 双向依赖入风险表；前端共享组件归属表入 §101；E5 私密配置管理范围补全（读/写/激活）；07 §21 条目 14 AC 归属补入 E3；18.4 条目→Epic 映射表新增；Skill 上传复用 resource-uploads（05 §12.5 回填注释）；裸路由默认分区跳转；登录设备区摘要；路由组织与 UI 基线冻结；E4 Out 修正 P4 仅只读 Subject 视图；13 号文档新增 §88A `/app` 个人回收站契约。 |
| 2026-08-18 | Development Plan Phase 4 拆分审查修正 | 14 号文档 §97/§98/§100.1/§100.3 修正：P4-E4 增平台级共享 Resource 管理页（07 §21 条目 22 后半段补承接）；User 删除/聚合回收站/activity/monitoring 后端归属 P2-E2（P4-E1/E3 依赖同步）；共享两页由 P4-E2 移入 P4-E3（组件复用 P3-E4/E5）；P4-E4 依赖补 P4-E2/P4-E3；各 Epic 验收承载 18.2 管理类集成用例；发布失败态/密码交接提示/设计禁止标注/回收站组件复用等验收补全。 |
| 2026-08-18 | Development Plan Phase 5 拆分审查修正 | 14 号文档 §95/§97/§99/§100/§101 修正：P5-E1 低层 router 条件挂载+代理 deny 双保险、Studio 启用开关（06 §16.2 补 `/` 根路径、§16.3 补配置与健康阈值）；P5-E2 验收⑦ 补 OAuth 三凭证、告警边界声明、阈值定义；P5-E3 备份工具选型入范围；P5-E4 18.3 客户端清单与 Go/No-Go 证据模板、26 条唯一证据收口；执行序改 P5-E1→E2→E3→E4 串行；P2-E1 验收⑥「闭合」改开发态验证点；§101 风险 9/10 措辞同步。 |
| 2026-08-18 | 拆分审查复核修正（第二轮） | 按 review/ 目录 5 份审查记录第二轮结果修订：03 §8.1 删除「提权」轮换表述（角色提升不轮换 Session）；05 §12.3 新增 `auth/me/session-summary` 契约、§12.5 回填 09 §46.2 的 4 个端点（nodes/{node_id}、download、operations/cancel、resources/{id}/search）；06 §16.3 补 Purge 停滞阈值（`purge_stall_threshold`）；spike `session_service.py` docstring 同步；14 号文档修正 23 处（轮换语义、`==7` 主归属 P1-E5、`==3/4/6` 复跑如实标注、18.1 计数 20 项、monitoring 单归属 P2-E2、Account deletion-preview/DELETE 认领、P2-E3 平台只读 Resource、04 §10.10/§10.12/§10.14 引用、P4-E2/E3/E4 依赖补全、18.4 条目 17 改跨域、P5-E1 `/oauth/*` 通配收敛、P5-E3 去自引用、P5-E4 18.5 清单补第 6 条、P5-E2 对应章节修正、18.5 条目 16 对应修正、§103 新增本轮记录）。 |
| 2026-08-20 | Design v0.1 风格定稿（浅色玻璃拟态） | 主色由单一 `#2563eb` 改为 `#7c3aed`（紫罗兰）+ 紫→蓝渐变；字体栈前缀 Geist / Geist Mono（中文回退到系统字体）；Sider 形态改为「图标 + 文字」；角色徽标改紫色；界面文案统一改为中文；DESIGN-SYSTEM v0.1.3；.op 文件改名 .pen；删除 4 个变体对话框帧（v0.1.x 增量补帧）。同步 docs/ovp/README.md §12、`v0.1/design/` 目录、06 §13.5 字体栈、13 §89.2 视觉实现层。 |
