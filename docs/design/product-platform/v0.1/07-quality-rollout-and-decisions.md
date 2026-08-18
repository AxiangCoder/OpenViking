# 07 测试、实施边界与架构决策

> Design v0.1 · [返回版本索引](README.md)

## 18. 测试设计

### 18.1 单元测试

- 密码哈希和验证。
- Session 签发、hash、到期、撤销和轮换。
- CSRF 校验。
- Permission 并集和角色禁用。
- `PlatformPrincipal + DataAccessContext -> RequestContext` 映射。
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

### 18.3 兼容测试

- 现有 Root API Key 行为不变。
- 现有 User API Key 行为不变。
- OAuth access/refresh token 行为不变。
- MCP、Python SDK、TypeScript SDK、CLI 基本冒烟测试通过。
- `/studio` 现有页面仍可访问。
- 原 v0.4.12 User/Peer/Session 路径可读写。

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

### 18.5 安全测试

- 修改 URL/请求体中的 Account/User ID 不能越权。
- Header spoofing 无效。
- Cookie Secure/HttpOnly/SameSite 生效。
- CSRF、登录爆破、Session fixation、Token replay 测试。
- 日志中不出现密码、Cookie、API Key 和 Token。
- 删除/禁用最后一个 Account Admin 被拒绝。
- 篡改 Subject Account/User、伪造角色或绕过确认弹窗均不能绕过后端授权。

## 19. 分阶段实施

### Phase 0：契约冻结

- 确认产品首版页面和 Permission Catalog。
- 为当前 v0.4.12 跑一套兼容冒烟测试。
- 固定 Account/User/Peer/Role 术语。

### Phase 1：IAM 基础

- PostgreSQL schema、migration。
- Account/User/Role/Permission repository。
- 密码登录、Session、CSRF、`auth/me`。
- 默认角色和权限种子。
- 审计基础。

### Phase 2：Provisioning 与 Product API

- Provisioning outbox/worker/reconciler。
- `PlatformPrincipal -> RequestContext`。
- Memory、Resource、Session 产品 Facade API。
- 跨 Account/User 隔离测试。

### Phase 3：产品前端

- 新建 `web-platform`。
- 登录、产品首页、记忆、资源、Session。
- 基于 Permission 的路由与按钮控制。

### Phase 4：管理后台

- 用户、角色、权限、审计页面。
- 邀请、禁用、角色分配与 30 天软删除恢复。
- Provisioning 状态与重试。

### Phase 5：迁移与生产加固

- 导入现有 Account/User/Role。
- 保留并验证旧 API Key。
- 安全测试、备份恢复和回滚演练。
- 限制 `/studio` 和低层 Admin API 的网络边界。

### Phase 6：可选增强

- OIDC/企业微信等外部登录。
- PAT 多凭据与 Scope。
- Studio SSO。
- APIKeyManager PostgreSQL Repository。
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
- `openviking/server/config.py` 或主配置模型：接入 Platform 配置。
- `pyproject.toml`：增加 PostgreSQL/SQLAlchemy/Alembic 依赖。
- Dockerfile / Compose / Helm：增加 `web-platform` 构建和 PostgreSQL 配置。

### 第一阶段避免修改

- `openviking/storage/viking_fs.py` 的路径规则。
- `openviking/core/namespace.py` 的 User/Peer 隔离规则。
- Session、Memory、Resource 的核心存储格式。
- 现有 OAuth Token 格式。
- 现有 SDK/MCP/CLI API 契约。

## 21. 验收标准

满足以下条件才允许进入生产：

1. 普通用户可通过产品登录进入 `/app`，浏览器中不存在 OpenViking API Key。
2. Account Admin 可在 `/admin` 管理本 Account 用户和角色。
3. Platform Super Admin、Account Admin 和 User 的 Permission 与数据范围在后端真实生效。
4. Account Admin 可读取当前 Account 用户数据，Platform Super Admin 可读取全平台数据；审计同时记录 Actor 与 Subject。
5. Account/User 身份完全由服务端 Session 派生。
6. 跨 Account、跨 User、Header spoofing 和 IDOR 测试全部通过。
7. 现有 `/studio`、SDK、CLI、MCP、API Key 和 OAuth 冒烟测试通过。
8. 现有 v0.4.12 数据无需移动即可由迁移后的用户读取。
9. 用户禁用、密码修改和角色变更能立即影响会话与权限。
10. 所有管理和高风险操作产生脱敏审计记录。
11. Account/User Provisioning 失败可见、可重试、不会产生重复对象。
12. 已完成备份恢复和版本回滚演练。
13. Account、User 和业务数据软删除后 30 天内可恢复，期满物理清理且审计仍保留。
14. 普通用户和 Account Admin 均不能切换到其他 Account。

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
| 管理员数据访问 | Actor/Subject 分离 | 管理员按范围查看数据，同时保留真实操作者和数据归属者 |
| 高风险确认 | 展示影响范围的确认弹窗 | v0.1 以防误触为目标，不重输密码、不输入名称、不做双人审批 |
| 删除策略 | 30 天软删除 | 提供误操作恢复窗口，期满后异步物理清理 |
| 迁移 | 保留旧 registry，先桥接再抽象 | 保证现有 API Key 和数据可回滚 |

## 23. 实施前必须确认的产品决策

这些问题不阻塞架构设计，但在编码前必须定稿：

1. 邮箱是否作为登录标识，以及采用全局唯一还是 Account 内唯一。
2. 用户注册是开放注册、邀请码，还是只允许管理员创建。
3. Account 共享资源是否允许普通 User 写入。
4. v0.1 是否开放自定义角色，或只开放三个内置角色。
5. 是否需要第一阶段即支持企业微信/OIDC。
6. Account 和首位 Account Admin 的创建入口。
7. Studio 的生产访问边界是 VPN、Tailscale、IP allowlist 还是反向代理 SSO。

在这些决策确认前，可以先实现与产品策略无关的底座：PostgreSQL schema、Session、CSRF、Permission Engine、Provisioning Bridge 和兼容测试。
