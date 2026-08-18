# 07 测试、实施边界与架构决策

> Design v0.1 · [返回版本索引](README.md)

## 18. 测试设计

### 18.1 单元测试

- 密码哈希和验证。
- 登录 Session 签发、hash、到期、撤销和轮换。
- 全局唯一邮箱、随机初始密码生成及 Argon2id hash。
- 严格角色等级密码重置判断，`actor_role_rank <= target_role_rank` 一律拒绝。
- CSRF 校验。
- Permission 并集和角色禁用。
- `AuthenticatedUserPrincipal + DataAccessContext -> RequestContext` 映射。
- PostgreSQL 产品 ID 与 `ov_account_id/ov_user_id` 映射不可混用，OpenViking URI 只能使用服务端查得的 `ov_*` ID。
- `platform-gateway` 执行占位标识只能用于已授权的 Account 共享 URI，不能登录、签发 Key、访问 User 私有根或从外部请求声明。
- 登录 Session、用户 API Key、OAuth 到同一用户 Principal 的解析。
- API Key hash 校验、一次性明文、到期和撤销。
- Actor/Subject Scope 授权矩阵。
- 产品 ID 到 OpenViking URI 的安全映射。
- URI 分类器能稳定区分 `user_private/account_shared/internal`，并拒绝 URI、Owner User、Account 与可见性不一致的对象。
- Resource 未指定目标时强制落入 Actor 的 User 私有区，不继承源码当前共享区默认值。
- 审计脱敏。

### 18.2 API 集成测试

- 未登录请求返回 401。
- 无 Permission 返回 403。
- Account Admin 只能管理和查看当前 Account。
- Platform Super Admin 创建 Account 和首位 Account Admin；Account Admin 直接创建普通 User，不存在邀请流程。
- 相同规范化邮箱不能在不同 Account 重复创建。
- Account Admin 创建用户时不能指定 `account_admin`，也不能提升或重置同级 Account Admin。
- Platform Super Admin 不能通过产品 API 创建或重置另一个 Platform Super Admin。
- 上级管理员重置低级别用户密码后，目标用户全部登录 Session 立即失败，但 OpenViking 对话 Session 和 API Key 不受影响。
- User A 不能读取 User B 的 Memory/OpenViking 对话 Session。
- User A 可管理自己的私有 Resource/Skill，但不能读取 User B 的私有 Resource/Skill。
- 普通 User 可读取 Account 共享 Resource、读取和使用共享 Skill，但所有共享新增、写入、改名、移动、标签、恢复和删除操作均返回 403。
- Account Admin 可管理本 Account 的共享 Resource/Skill，不能管理其他 Account 的共享内容，也不能默认修改其他用户的私有内容。
- Account 共享对象删除创建者后仍保留，且授权不取决于 `created_by`。
- Account Admin 能读取当前 Account 成员数据，但不能修改、导出或删除他人数据。
- Account Admin 不能访问其他 Account；Platform Super Admin 可以按平台权限读取任意 Account/User 数据。
- 管理员跨用户读取的审计事件同时包含正确 Actor 与 Subject。
- 禁用用户后已有登录 Session 立即失败。
- 权限变更后无需重新登录即可生效。
- CSRF 缺失或错误时写请求失败。
- 重复 `Idempotency-Key` 不创建重复用户。
- 软删除对象立即隐藏，30 天内可恢复，期满清理任务幂等。

### 18.3 凭证与集成测试

- 同一 User 使用登录 Session、用户 API Key 和 OAuth 调用同一动作时，Permission 结果一致。
- 用户角色变更、禁用或进入删除期后，所有 API Key 在下一次请求立即受影响。
- 一个具名 Key 被撤销不影响同一用户的其他 Key；被撤销或到期的 Key 返回统一 401。
- Key 创建响应只返回一次明文，列表、日志、审计和错误响应均不出现明文或完整 hash。
- API Key 解析出的 Account/User 不能被 Header、URL 或请求体覆盖。
- MCP、Python SDK、TypeScript SDK、CLI 和 Codex/OpenClaw/OpenCode 插件可使用新签发的用户 API Key。
- MCP 与 REST 复用同一 Principal Resolver 和 AuthorizationService。
- 同一普通 User 通过 `/api/platform/v1`、`/api/v1`、MCP、SDK/CLI 或插件访问相同 URI 时得到一致的共享/私有授权结果。
- `/api/v1` 的 `write/rm/mv/set_tags/add_resource` 及对应 MCP Tool 不能绕过共享区只读策略；`mv` 的源和目标都要授权。
- 搜索只返回调用者自己的私有根与当前 Account 共享根，不返回同 Account 其他 User 私有数据或其他 Account 数据。
- 系统不接受 Service Account Principal 或 Service Account Key；Root API Key 不能作为产品用户凭证。
- 生产公网访问 `/studio` 返回 404；在显式启用的开发/私网环境中，Studio 仍可使用受控 API Key 完成底层排障。

### 18.4 前端 E2E

- 登录、刷新页面、退出。
- Route Guard。
- 普通用户看不到 `/admin`。
- Account Admin 能管理用户并查看当前 Account 的用户数据，但看不到跨 Account 数据。
- Platform Super Admin 可从 `/platform` 查看所有 Account、用户和数据，页面始终保留当前 Actor 身份。
- 普通用户没有 Account 切换入口。
- Resource/Skill 页面明确分为“我的”和“Account 共享”；普通 User 的共享页没有写入、删除或恢复入口。
- 新增 Resource 默认进入“我的 Resource”；管理员发布到共享区时页面明确展示目标 Account，并创建独立共享对象。
- 高风险操作弹窗展示影响范围，不要求输入密码或 Account 名称。
- Role 变更后导航和按钮即时更新。
- 登录 Session 过期后回到登录页且不丢失安全状态。
- 用户可创建、复制一次、查看元数据和撤销自己的具名 API Key，页面刷新后不能再次获取明文。
- 管理员创建或重置低级别用户时可复制一次系统生成的密码；该密码可长期登录，首次登录不强制修改。
- 产品中不存在注册、邀请、激活和自助找回密码页面。
- `/login` 只提供邮箱和密码，不显示企业登录按钮，也不存在 OIDC Provider 配置、登录回调或自动创建用户流程。

### 18.5 安全测试

- 修改 URL/请求体中的 Account/User ID 不能越权。
- Header spoofing 无效。
- Cookie Secure/HttpOnly/SameSite 生效。
- CSRF、登录爆破、登录 Session fixation、Token replay 测试。
- 日志中不出现密码、Cookie、API Key 明文、完整凭证 hash 和 Token。
- 创建/重置密码响应不会进入访问日志、前端埋点、错误上报或审计 metadata。
- 删除/禁用最后一个 Account Admin 被拒绝。
- 篡改 Subject Account/User、伪造角色或绕过确认弹窗均不能绕过后端授权。
- 篡改 `visibility`、直接提交 `viking://resources`、利用默认目标、编码/别名 URI 或跨可见性移动均不能绕过共享写权限。

## 19. 分阶段实施

### Phase 0：契约冻结

- 确认产品首版页面和 Permission Catalog。
- 固定 `/api/v1/*`、`/mcp`、Bearer 和 `X-Api-Key` 中纳入 v0.1 的集成契约。
- 固定 Account/User/Peer/Role 术语。
- 固定用户 API Key 的格式、hash 算法、一次性展示和错误语义。

### Phase 1：IAM 基础

- PostgreSQL schema、migration。
- Account/User/Role/Permission repository。
- 全局唯一邮箱、管理员直建用户、密码登录、登录 Session、CSRF、`auth/me`。
- `iam_api_credentials`、用户 API Key 创建/列表/撤销和统一 Principal Resolver。
- 默认角色和权限种子。
- 审计基础。

### Phase 2：Provisioning 与 Product API

- Provisioning outbox/worker/reconciler。
- `AuthenticatedUserPrincipal -> RequestContext`。
- Memory、Resource、OpenViking 对话 Session 产品 Facade API。
- 跨 Account/User 隔离测试。

### Phase 3：产品前端

- 新建 `web-platform`。
- 登录、产品首页、记忆、资源、OpenViking 对话 Session。
- 个人设置中的 API Key 管理与一次性明文展示。
- 基于 Permission 的路由与按钮控制。

### Phase 4：管理后台

- 用户、三个内置角色、权限和审计页面。
- 直接创建用户、分级密码重置、禁用和 30 天软删除恢复。
- Provisioning 状态与重试。

### Phase 5：初始部署与生产加固

- 一次性初始化 Platform Super Admin、Account 和首位 Account Admin。
- 使用新 IAM 签发用户 API Key，不导入或接受旧 Key。
- 安全测试、备份恢复和回滚演练。
- 确认生产公网未挂载 `/studio`，并限制低层 Admin API 的网络边界。

### Phase 6：可选增强

- 用户 API Key 的限制性 Scope，且只允许缩小用户有效权限。
- Service Account；仅在出现 Account 级共享、独立生命周期的机器集成需求后另行设计。
- Platform Super Admin 的部署侧紧急恢复/Break-glass 机制。
- 多实例 Redis 限流和缓存。

## 20. 预计源码改动边界

### 新增

- `openviking/server/platform/**`
- `openviking/server/platform/migrations/**`
- `web-platform/**`
- `tests/platform/**`
- Platform 配置模型与文档。

### 少量修改

- `openviking/server/app.py`：挂 Platform Routers 和 `web-platform` 静态资源。
- `openviking/server/routers/__init__.py`：若沿用现有聚合方式，导出 Platform Router。
- `openviking/server/auth/plugins/api_key.py` 与 `openviking/server/api_keys/**`：改为从 PostgreSQL 用户凭证生成统一 Principal，不读取旧用户 Key registry。
- `openviking/server/mcp_endpoint.py`：复用与 REST 相同的 Principal Resolver 和 AuthorizationService。
- `openviking/server/routers/content.py`、`filesystem.py`、`resources.py`、`skills.py` 等外部低层入口：接入统一 URI Target Policy，覆盖读、写、移动、标签、删除与默认添加目标。
- `openviking/server/config.py` 或主配置模型：接入 Platform 配置。
- `pyproject.toml`：增加 PostgreSQL/SQLAlchemy/Alembic 依赖。
- Dockerfile / Compose / Helm：增加 `web-platform` 构建和 PostgreSQL 配置。

### 第一阶段避免修改

- `openviking/storage/viking_fs.py` 的路径规则。
- `openviking/core/namespace.py` 的 User/Peer 隔离规则。
- OpenViking 对话 Session、Memory、Resource 的核心存储格式。
- VikingFS、VectorDB 与 OpenViking 业务数据布局。

## 21. 验收标准

满足以下条件才允许进入生产：

1. 普通用户可通过产品登录进入 `/app`，浏览器登录不依赖 API Key；除创建成功页的一次性明文外，浏览器不持久化 API Key。
2. Account Admin 可在 `/admin` 管理本 Account 普通 User，并查看三个内置角色及权限；不能创建、编辑、删除角色或提升 Account Admin。
3. Platform Super Admin、Account Admin 和 User 的 Permission 与数据范围在后端真实生效。
4. Account Admin 可读取当前 Account 用户数据，Platform Super Admin 可读取全平台数据；审计同时记录 Actor 与 Subject。
5. Account/User 身份完全由服务端从登录 Session、用户 API Key 或 OAuth 凭证解析，不能由客户端身份字段指定。
6. 跨 Account、跨 User、Header spoofing 和 IDOR 测试全部通过。
7. 生产公网不挂载 `/studio`；SDK、CLI、插件和 MCP 可使用新签发的用户 API Key 或用户 OAuth Token，Studio 只可在显式启用的开发/私网入口使用。
8. 登录 Session、用户 API Key 和 OAuth 对同一用户使用同一套实时 RBAC；任何渠道都不能切换到其他 Account/User。
9. 用户禁用、密码修改和角色变更能立即影响会话与权限。
10. 所有管理和高风险操作产生脱敏审计记录。
11. Account/User Provisioning 失败可见、可重试、不会产生重复对象。
12. 已完成备份恢复和版本回滚演练。
13. Account、User 和业务数据软删除后 30 天内可恢复，期满物理清理且审计仍保留。
14. 普通用户和 Account Admin 均不能切换到其他 Account。
15. 用户可为不同插件创建并分别撤销具名 API Key，完整明文只展示一次，禁用用户会立即阻断全部 Key。
16. v0.1 不存在 Service Account、Service Account Key 或可由外部使用的机器 Principal。
17. Platform Super Admin 创建 Account 和首位 Account Admin；Account Admin 直接创建普通 User，系统没有注册、邀请和激活流程。
18. 初始/重置密码可复制、可长期使用且不强制修改；后端不保存明文，页面关闭后不能再次获取。
19. 密码重置只允许严格上级操作下级；同级重置被拒绝，成功后只撤销目标登录 Session，不删除 OpenViking 对话数据或自动撤销 API Key。
20. Resource/Skill 在页面、产品 API、低层 API、MCP 和审计中都能明确区分 User 私有与 Account 共享，不出现把 Account 共享称为互联网“公共”的含糊语义。
21. 普通 User 默认新增 Resource/Skill 到自己的私有区，只读共享 Resource、读取/使用共享 Skill；任何渠道均不能写入或删除 Account 共享区。
22. Account Admin 可管理本 Account 共享 Resource/Skill；Platform Super Admin 可管理目标 Account；共享对象不因创建者变化而改变授权或被自动删除。
23. 产品只提供本地邮箱密码登录，不存在 OIDC/企业登录接口、页面、Provider 配置、身份映射表或后续版本占位设计；MCP OAuth 仍仅用于客户端授权。

## 22. 关键架构决策记录

| 决策 | 选择 | 原因 |
| --- | --- | --- |
| 后端形态 | 模块化单体 | 最小化二次开发和部署复杂度，保留未来拆分边界 |
| 产品 API | 新建 `/api/platform/v1` | 避免污染上游 `/api/v1` 契约 |
| 产品前端 | 新建 `web-platform` | 与 Studio 生命周期和认证模型解耦 |
| Studio | 保留代码、生产公网默认不挂载 `/studio` | 正式产品能力进入权限页面；旧 Studio 只用于可选私网排障，不成为产品依赖 |
| 浏览器认证 | 不透明服务端登录 Session | 可立即撤销，权限变更即时生效 |
| 用户开通 | 管理员直接创建 | v0.1 不提供注册、邀请、邮件和激活流程 |
| 登录标识 | 全局唯一邮箱 | 登录时无需客户端指定 Account，一个用户固定属于一个 Account |
| 产品网页登录 | 仅本地邮箱密码 | 不提供 OIDC、企业微信或企业单点登录，也不列入后续版本计划 |
| 初始密码 | 系统生成、一次展示、可复制并长期使用 | 管理员自行线下交接，用户不被强制首次修改；接受管理员可能长期知晓密码的审计限制 |
| 密码重置 | 只允许严格上级重置下级 | 禁止同级控制；成功后撤销目标全部登录 Session |
| 最高管理员恢复 | v0.1 不提供网页恢复 | 部署侧 Break-glass 留待后续版本 |
| IAM 存储 | PostgreSQL | 事务、约束、查询、审计和迁移能力适合账号系统 |
| Redis | 第一阶段不强制 | 减少初期部署单元，需要多实例时再引入 |
| OpenViking 调用 | 同进程直接 Service 调用 | 避免内部 HTTP 和重复鉴权 |
| 权限模型 | Platform RBAC + OpenViking ACL | 业务授权与数据归属双层防护 |
| Root | 仅内部实例身份 | 避免把系统密钥降格成普通用户角色 |
| 用户 API Key | 多个具名 Key，全部代表所属 User | 支持插件按设备撤销，同时保持身份、RBAC 和数据范围统一 |
| 插件与 MCP 身份 | 用户委托型集成 | 调用渠道不产生新身份，使用谁的 Key 就代表谁 |
| Service Account | v0.1 不提供 | 当前没有独立于自然人的 Account 级共享机器主体需求 |
| Resource/Skill 可见性 | `user_private` 与 `account_shared` | “共享”严格限定在同一 Account；不使用含糊的“公共”表示跨租户或互联网可见 |
| 普通 User 的共享权限 | Resource 只读；Skill 可读、可使用、不可管理 | 团队共享内容由 Account Admin 维护，避免所有成员直接改写共同知识 |
| 默认新增位置 | User 私有区 | 当前源码 Resource 默认落入共享根，不符合产品最小权限原则；服务端必须显式覆盖 |
| 共享对象所有权 | 归 Account，不归创建者 | `created_by` 只审计，不引入 v0.1 的贡献者/内容所有者权限模型 |
| 多入口授权 | Platform API、低层 API、MCP 共用 Principal Resolver、URI Policy 和 RBAC | API Key、SDK/CLI 或插件只是调用渠道，不能成为权限旁路 |
| 管理员数据访问 | Actor/Subject 分离 | 管理员按范围查看数据，同时保留真实操作者和数据归属者 |
| 高风险确认 | 展示影响范围的确认弹窗 | v0.1 以防误触为目标，不重输密码、不输入名称、不做双人审批 |
| 删除策略 | 30 天软删除 | 提供误操作恢复窗口，期满后异步物理清理 |
| 旧门禁 | 不迁移、不双写、不兼容旧用户 API Key | 产品处于初版，直接以 PostgreSQL IAM 作为唯一身份事实来源 |

## 23. 设计收敛状态

当前已列出的 Design v0.1 未决项均已收敛。Studio 边界固定为“代码可选保留、生产公网默认不挂载”；若某个部署需要启用私网 Studio，可自行选择 VPN、Tailscale 或固定 IP，不改变产品架构与权限模型。后续发现新的设计问题时继续补充讨论。
