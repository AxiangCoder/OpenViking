# 07 测试、实施边界与架构决策

> Design v0.1 · [返回版本索引](README.md)

## 18. 测试设计

### 18.1 单元测试

- 密码哈希和验证。
- Session 签发、hash、到期、撤销和轮换。
- CSRF 校验。
- Permission 并集和角色禁用。
- `AuthenticatedUserPrincipal + DataAccessContext -> RequestContext` 映射。
- Session、用户 API Key、OAuth 到同一用户 Principal 的解析。
- API Key hash 校验、一次性明文、到期和撤销。
- Actor/Subject Scope 授权矩阵。
- 产品 ID 到 OpenViking URI 的安全映射。
- 审计脱敏。

### 18.2 API 集成测试

- 未登录请求返回 401。
- 无 Permission 返回 403。
- Account Admin 只能管理和查看当前 Account。
- User A 不能读取 User B 的 Memory/Session。
- Account Admin 能读取当前 Account 成员数据，但不能修改、导出或删除他人数据。
- Account Admin 不能访问其他 Account；Platform Super Admin 可以按平台权限读取任意 Account/User 数据。
- 管理员跨用户读取的审计事件同时包含正确 Actor 与 Subject。
- 禁用用户后已有 Session 立即失败。
- 权限变更后无需重新登录即可生效。
- CSRF 缺失或错误时写请求失败。
- 重复 `Idempotency-Key` 不创建重复用户。
- 软删除对象立即隐藏，30 天内可恢复，期满清理任务幂等。

### 18.3 凭证与集成测试

- 同一 User 使用 Session、用户 API Key 和 OAuth 调用同一动作时，Permission 结果一致。
- 用户角色变更、禁用或进入删除期后，所有 API Key 在下一次请求立即受影响。
- 一个具名 Key 被撤销不影响同一用户的其他 Key；被撤销或到期的 Key 返回统一 401。
- Key 创建响应只返回一次明文，列表、日志、审计和错误响应均不出现明文或完整 hash。
- API Key 解析出的 Account/User 不能被 Header、URL 或请求体覆盖。
- MCP、Python SDK、TypeScript SDK、CLI 和 Codex/OpenClaw/OpenCode 插件可使用新签发的用户 API Key。
- MCP 与 REST 复用同一 Principal Resolver 和 AuthorizationService。
- 系统不接受 Service Account Principal 或 Service Account Key；Root API Key 不能作为产品用户凭证。
- `/studio` 仍可访问，但生产访问受网络边界控制。

### 18.4 前端 E2E

- 登录、刷新页面、退出。
- Route Guard。
- 普通用户看不到 `/admin`。
- Account Admin 能管理用户并查看当前 Account 的用户数据，但看不到跨 Account 数据。
- Platform Super Admin 可从 `/platform` 查看所有 Account、用户和数据，页面始终保留当前 Actor 身份。
- 普通用户没有 Account 切换入口。
- 高风险操作弹窗展示影响范围，不要求输入密码或 Account 名称。
- Role 变更后导航和按钮即时更新。
- Session 过期后回到登录页且不丢失安全状态。
- 用户可创建、复制一次、查看元数据和撤销自己的具名 API Key，页面刷新后不能再次获取明文。

### 18.5 安全测试

- 修改 URL/请求体中的 Account/User ID 不能越权。
- Header spoofing 无效。
- Cookie Secure/HttpOnly/SameSite 生效。
- CSRF、登录爆破、Session fixation、Token replay 测试。
- 日志中不出现密码、Cookie、API Key 明文、完整凭证 hash 和 Token。
- 删除/禁用最后一个 Account Admin 被拒绝。
- 篡改 Subject Account/User、伪造角色或绕过确认弹窗均不能绕过后端授权。

## 19. 分阶段实施

### Phase 0：契约冻结

- 确认产品首版页面和 Permission Catalog。
- 固定 `/api/v1/*`、`/mcp`、Bearer 和 `X-Api-Key` 中纳入 v0.1 的集成契约。
- 固定 Account/User/Peer/Role 术语。
- 固定用户 API Key 的格式、hash 算法、一次性展示和错误语义。

### Phase 1：IAM 基础

- PostgreSQL schema、migration。
- Account/User/Role/Permission repository。
- 密码登录、Session、CSRF、`auth/me`。
- `iam_api_credentials`、用户 API Key 创建/列表/撤销和统一 Principal Resolver。
- 默认角色和权限种子。
- 审计基础。

### Phase 2：Provisioning 与 Product API

- Provisioning outbox/worker/reconciler。
- `AuthenticatedUserPrincipal -> RequestContext`。
- Memory、Resource、Session 产品 Facade API。
- 跨 Account/User 隔离测试。

### Phase 3：产品前端

- 新建 `web-platform`。
- 登录、产品首页、记忆、资源、Session。
- 个人设置中的 API Key 管理与一次性明文展示。
- 基于 Permission 的路由与按钮控制。

### Phase 4：管理后台

- 用户、角色、权限、审计页面。
- 邀请、禁用、角色分配与 30 天软删除恢复。
- Provisioning 状态与重试。

### Phase 5：初始部署与生产加固

- 一次性初始化 Platform Super Admin、Account 和首位 Account Admin。
- 使用新 IAM 签发用户 API Key，不导入或接受旧 Key。
- 安全测试、备份恢复和回滚演练。
- 限制 `/studio` 和低层 Admin API 的网络边界。

### Phase 6：可选增强

- OIDC/企业微信等外部登录。
- 用户 API Key 的限制性 Scope，且只允许缩小用户有效权限。
- Service Account；仅在出现 Account 级共享、独立生命周期的机器集成需求后另行设计。
- Studio SSO。
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
- `openviking/server/config.py` 或主配置模型：接入 Platform 配置。
- `pyproject.toml`：增加 PostgreSQL/SQLAlchemy/Alembic 依赖。
- Dockerfile / Compose / Helm：增加 `web-platform` 构建和 PostgreSQL 配置。

### 第一阶段避免修改

- `openviking/storage/viking_fs.py` 的路径规则。
- `openviking/core/namespace.py` 的 User/Peer 隔离规则。
- Session、Memory、Resource 的核心存储格式。
- VikingFS、VectorDB 与 OpenViking 业务数据布局。

## 21. 验收标准

满足以下条件才允许进入生产：

1. 普通用户可通过产品登录进入 `/app`，浏览器登录不依赖 API Key；除创建成功页的一次性明文外，浏览器不持久化 API Key。
2. Account Admin 可在 `/admin` 管理本 Account 用户和角色。
3. Platform Super Admin、Account Admin 和 User 的 Permission 与数据范围在后端真实生效。
4. Account Admin 可读取当前 Account 用户数据，Platform Super Admin 可读取全平台数据；审计同时记录 Actor 与 Subject。
5. Account/User 身份完全由服务端从 Session、用户 API Key 或 OAuth 凭证解析，不能由客户端身份字段指定。
6. 跨 Account、跨 User、Header spoofing 和 IDOR 测试全部通过。
7. `/studio` 可访问；SDK、CLI、插件和 MCP 可使用新签发的用户 API Key 或用户 OAuth Token。
8. Session、用户 API Key 和 OAuth 对同一用户使用同一套实时 RBAC；任何渠道都不能切换到其他 Account/User。
9. 用户禁用、密码修改和角色变更能立即影响会话与权限。
10. 所有管理和高风险操作产生脱敏审计记录。
11. Account/User Provisioning 失败可见、可重试、不会产生重复对象。
12. 已完成备份恢复和版本回滚演练。
13. Account、User 和业务数据软删除后 30 天内可恢复，期满物理清理且审计仍保留。
14. 普通用户和 Account Admin 均不能切换到其他 Account。
15. 用户可为不同插件创建并分别撤销具名 API Key，完整明文只展示一次，禁用用户会立即阻断全部 Key。
16. v0.1 不存在 Service Account、Service Account Key 或可由外部使用的机器 Principal。

## 22. 关键架构决策记录

| 决策 | 选择 | 原因 |
| --- | --- | --- |
| 后端形态 | 模块化单体 | 最小化二次开发和部署复杂度，保留未来拆分边界 |
| 产品 API | 新建 `/api/platform/v1` | 避免污染上游 `/api/v1` 契约 |
| 产品前端 | 新建 `web-platform` | 与 Studio 生命周期和认证模型解耦 |
| Studio | 保留 `/studio` | 复用现有运维和底层能力页面 |
| 浏览器认证 | 不透明服务端 Session | 可立即撤销，权限变更即时生效 |
| IAM 存储 | PostgreSQL | 事务、约束、查询、审计和迁移能力适合账号系统 |
| Redis | 第一阶段不强制 | 减少初期部署单元，需要多实例时再引入 |
| OpenViking 调用 | 同进程直接 Service 调用 | 避免内部 HTTP 和重复鉴权 |
| 权限模型 | Platform RBAC + OpenViking ACL | 业务授权与数据归属双层防护 |
| Root | 仅内部实例身份 | 避免把系统密钥降格成普通用户角色 |
| 用户 API Key | 多个具名 Key，全部代表所属 User | 支持插件按设备撤销，同时保持身份、RBAC 和数据范围统一 |
| 插件与 MCP 身份 | 用户委托型集成 | 调用渠道不产生新身份，使用谁的 Key 就代表谁 |
| Service Account | v0.1 不提供 | 当前没有独立于自然人的 Account 级共享机器主体需求 |
| 管理员数据访问 | Actor/Subject 分离 | 管理员按范围查看数据，同时保留真实操作者和数据归属者 |
| 高风险确认 | 展示影响范围的确认弹窗 | v0.1 以防误触为目标，不重输密码、不输入名称、不做双人审批 |
| 删除策略 | 30 天软删除 | 提供误操作恢复窗口，期满后异步物理清理 |
| 旧门禁 | 不迁移、不双写、不兼容旧用户 API Key | 产品处于初版，直接以 PostgreSQL IAM 作为唯一身份事实来源 |

## 23. 实施前必须确认的产品决策

这些问题不阻塞架构设计，但在编码前必须定稿：

1. 邮箱是否作为登录标识，以及采用全局唯一还是 Account 内唯一。
2. 用户注册是开放注册、邀请码，还是只允许管理员创建。
3. Account 共享资源是否允许普通 User 写入。
4. v0.1 是否开放自定义角色，或只开放三个内置角色。
5. 是否需要第一阶段即支持企业微信/OIDC。
6. Account 和首位 Account Admin 的创建入口。
7. Studio 的生产访问边界是 VPN、Tailscale、IP allowlist 还是反向代理 SSO。

在这些决策确认前，可以先实现与产品策略无关的底座：PostgreSQL schema、Session、用户 API Key、统一 Principal Resolver、CSRF、Permission Engine、Provisioning Bridge 和集成契约测试。
