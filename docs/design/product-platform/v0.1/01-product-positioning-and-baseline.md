# 01 产品定位、术语与源码基线

> Design v0.1 · [返回版本索引](README.md)

## 1. 结论摘要

本设计把 OpenViking 定位为“上下文与记忆数据引擎”，在其外层增加产品平台能力：

- 新建 `web-platform`，承载登录、产品页面和租户管理后台。
- 保留现有 `web-studio`，继续挂载在 `/studio`，作为 OpenViking 运维与底层能力控制台。
- 在现有 FastAPI 进程中增加 Platform API、IAM 和 RBAC 模块，第一阶段采用模块化单体，不立即拆微服务。
- 使用 PostgreSQL 保存账号、登录凭据、角色、权限、会话、审计和配置同步状态。
- 浏览器登录使用服务端不透明会话和 `HttpOnly` Cookie，不使用或持久化 Root API Key 或用户 API Key；仅在用户主动创建 API Key 时一次性显示明文。
- Platform API 在服务端把登录身份转换成现有 `RequestContext(account_id, user_id, role)`，直接调用 `OpenVikingService`，不在同一进程内绕一圈 HTTP。
- SDK、CLI、插件和 MCP 使用用户 API Key 或用户 OAuth Token，解析为与网页登录相同的用户主体和 RBAC 权限。
- 产品角色和细粒度权限由 Platform API 强制执行；OpenViking 核心继续承担 account/user/peer 命名空间隔离，形成双层防护。

第一阶段目标不是把 OpenViking 改造成通用身份提供商，而是在复用其核心能力的前提下，构建一个可登录、可授权、可管理、可审计的产品平台。v0.1 是产品初版，不承担旧 Account/User/API Key 门禁的迁移与兼容。

## 2. 术语与边界

| 术语 | 专业含义 | 大白话解释 |
| --- | --- | --- |
| 产品前端 | 面向最终用户的业务 SPA | 用户真正使用产品的页面 |
| 管理后台 | 面向租户管理员的用户、内置角色权限和审计页面 | 管理员管人并查看固定权限规则的页面 |
| Studio | OpenViking 自带的底层操作与运维控制台 | 开发、排障、观察 OpenViking 的工具页 |
| Platform API | 面向产品前端的业务 API / BFF | 前端只找这个后端，不直接裸连 OpenViking |
| IAM | Identity and Access Management | 管“谁能登录、账号是否可用” |
| RBAC | Role-Based Access Control | 把一组权限装进角色，再把角色分给用户 |
| 登录 Session / 认证会话 | 用户登录后由服务端保存的登录状态 | 让浏览器保持登录；撤销后需要重新输入邮箱和密码 |
| OpenViking Session / 对话 Session | OpenViking 保存的对话与上下文业务数据 | 聊天和记忆内容，不是登录状态 |
| Permission | 一个原子操作能力 | 例如“能读记忆”“能删用户” |
| Account / Tenant | OpenViking 中的数据与管理隔离单元 | 一个组织、团队或工作空间 |
| User | Account 下的自然人或业务使用者 | 谁在使用系统 |
| User API Key | 绑定到一个 User 的长期 API 访问凭证 | 插件拿着用户自己的工作证替用户办事 |
| 用户委托型集成 | 使用用户 API Key 或用户 OAuth Token，以该用户身份调用 API 的客户端 | Codex、OpenClaw 或 MCP 客户端替当前用户读写数据 |
| Service Account | 独立于自然人的非人类访问主体 | 程序自己的账号；v0.1 不提供 |
| Peer | User 下的交互对象或 Agent 视图 | 和这个用户交互的客户、Agent 或对象，不是权限角色 |
| Root | OpenViking 实例级最高控制身份 | 系统密钥，不是普通可分配用户角色 |
| Platform Super Admin | 产品平台的人类超级管理员 | 可以管理和查看所有 Account、用户及数据，但不等于 Root API Key |
| Actor | 实际发起操作的登录用户 | 谁在看、谁在操作 |
| Subject | 被访问数据的归属用户 | 正在查看谁的数据 |

### 2.1 三类页面的明确划分

| 页面入口 | 使用者 | 主要职责 | 是否新建 |
| --- | --- | --- | --- |
| `/app/*` | 普通用户 | 记忆、资源、检索、会话、个人设置 | 是 |
| `/admin/*` | Account 管理员 | 用户管理、内置角色与权限查看、凭据、审计 | 是 |
| `/platform/*` | Platform Super Admin | 全部 Account、用户、数据和平台审计 | 是 |
| `/studio/*` | 运维、开发、受控管理员 | OpenViking 底层功能、监控、任务、调试 | 否，保留现状 |

`/studio` 不能等同于产品管理后台。当前 Studio 虽然已有用户管理、监控和设置页面，但其认证方式、信息架构和权限粒度仍是 OpenViking 控制台模型。

## 3. 目标与非目标

### 3.1 第一阶段目标

1. 支持账号密码登录、退出、查看当前用户和撤销登录会话。
2. 支持 Platform Super Admin 创建 Account 和首位 Account Admin，支持 Account Admin 直接创建本 Account 普通 User。
3. 支持普通用户访问自己的 OpenViking 记忆、对话 Session 和 User 资源。
4. 支持 Account 共享资源的权限控制。
5. 支持 Account Admin 管理并查看当前 Account 的用户和数据，支持 Platform Super Admin 查看全平台 Account、用户和数据。
6. 支持用户创建、查看元数据和撤销个人 API Key，并用于 SDK、CLI、插件和 MCP。
7. 用户 API Key 与 OAuth Token 只代表所属用户，实时继承用户状态、角色、Permission 和数据范围。
8. 所有高风险管理操作可审计、可追踪、可撤销。
9. Account、用户及其数据删除后进入 30 天恢复窗口，期满再异步物理清理。

### 3.2 第一阶段非目标

- 不做跨地域多活。
- 不立即拆分 IAM、Platform API 和 OpenViking 为独立微服务。
- 不替换 OpenViking 的 VikingFS、VectorDB、对话 Session 和 QueueFS。
- 不让产品前端直接编辑任意 `viking://` URI。
- 不把 Root API Key 变成普通用户登录凭据。
- 不在第一阶段实现复杂组织树、部门继承和 ABAC 策略语言。
- 不在 v0.1 引入 Service Account、Service Account Key 或 Account 级共享机器身份。
- 不导入或继续接受旧 Account/User/API Key registry 中的凭证；v0.1 从新的 IAM 数据开始。
- 不提供开放注册、邀请码、邀请链接、邀请邮件、邮箱激活或用户自助找回密码。
- 不在 v0.1 提供 Platform Super Admin 的网页紧急恢复或 Break-glass 通道；作为后续迭代处理。
- 不在第一阶段实现双人审批或强制重新输入密码的高风险操作确认流程。
- 不强制改造现有 Studio 为账号密码登录；Studio SSO 放在后续阶段。

## 4. 当前源码基线

### 4.1 已有能力

当前源码已经具备以下基础：

| 能力 | 当前实现位置 | 设计复用方式 |
| --- | --- | --- |
| FastAPI 服务与 Router 聚合 | `openviking/server/app.py` | 在同一进程注册 Platform Router |
| Request 身份上下文 | `openviking/server/identity.py` | Platform 身份转换为现有 `RequestContext` |
| `dev/api_key/trusted` 认证模式 | `openviking/server/auth/plugins/` | 复用认证入口结构，`api_key` 改为解析产品 IAM 用户凭证 |
| 认证插件扩展点 | `openviking/server/auth/plugin.py` | 后续可增加 OIDC/JWT 直连模式 |
| Account/User 管理 API | `openviking/server/routers/admin.py` | 第一阶段作为 Provisioning Bridge 复用 |
| Root/Admin/User 角色 | `openviking/server/identity.py` | 作为 OpenViking Base Role 保留 |
| User API Key | `openviking/server/api_keys/` | 源码证明当前 Key 表示 `(account_id, user_id)`；v0.1 保留“代表用户”的语义，但凭证存储与授权改接 PostgreSQL IAM |
| MCP 身份中间件 | `openviking/server/mcp_endpoint.py` | 当前与 REST 共用 `resolve_identity`；v0.1 继续共用新的 Principal Resolver 和 RBAC |
| Codex/OpenClaw/OpenCode 集成 | `examples/*-plugin/` | 当前均可配置用户 API Key，确认这些插件属于用户委托型集成 |
| account 物理路径隔离 | `openviking/storage/viking_fs.py` | 继续作为核心数据隔离层 |
| user/peer URI ACL | `openviking/core/namespace.py` | 继续阻止客户端绕过 Platform 授权直接跨用户读取 |
| OAuth 2.1 | `openviking/server/oauth/` | 复用 MCP 授权协议，最终仍解析为授权用户主体 |
| Web Studio | `web-studio/` | 原样保留在 `/studio` |

### 4.2 当前身份与权限模型的限制

1. Account/User/API Key 元数据主要保存于 VikingFS JSON：
   - `/local/_system/accounts.json`
   - `/local/{account_id}/_system/users.json`
2. 普通 Account 用户只能持有 `user` 或 `admin`；`root` 是服务端配置身份。
3. `Role.register()` 提供了自定义字符串和 rank 的扩展点，但账户用户校验和绝大多数 Router 仍按固定角色判断。
4. 当前权限主要是“角色硬编码 + URI ACL”，缺少可持久化的 Permission、RolePermission 和 UserRole。
5. Studio 使用 API Key 连接 OpenViking，不是产品登录会话。
6. 现有 Admin API 适合 OpenViking 控制面，不足以表达产品登录、管理员直建用户、分级密码重置、禁用和审计。
7. 当前 API Key 与 Account/User registry、Base Role 绑定，尚未与产品 PostgreSQL RBAC 统一，也没有独立 Service Account 主体。

### 4.3 必须保留的底层不变量与产品约束

- `account_id` 是 OpenViking 物理路径和向量数据隔离边界。
- `user_id` 是 User URI、OpenViking 对话 Session、Memory 和 Peer 数据归属边界。
- User 只能访问自己的 User namespace，且不能切换 Account。
- Account Admin 跨用户读取、Platform Super Admin 跨 Account/用户读取只能经过 Platform API 的数据范围授权，不改变 OpenViking 低层 API 的默认 ACL。
- 管理员读取目标用户数据时必须同时保留 Actor 与 Subject，不能把管理员身份无痕替换成目标用户。
- Root 是实例级控制身份，不写入普通角色绑定表，不通过产品 UI 分配。
- API Key 模式中，客户端提供的 Account/User Header 不能覆盖 API Key 解析出的身份。
- 用户 API Key、OAuth Token 和网页登录 Session 解析出的 Actor 必须是同一个 User；凭证本身不能持有独立角色或扩大用户权限。
- 插件、MCP、SDK 和 CLI 是调用渠道，不是新的身份类型。v0.1 的这些渠道全部属于用户委托型集成。
- `actor_peer_id` 只改变当前用户的 Peer 视图，不改变 Account/User 身份。

## 5. 设计原则

1. **身份由服务端派生**：产品 API 不接受客户端传入的 `account_id` 或 `user_id` 作为当前登录身份；管理员接口中的目标 ID 只表示 Subject，必须单独授权。
2. **权限先于业务调用**：Platform API 先检查 Permission，再调用 OpenViking。
3. **OpenViking ACL 再兜底**：即使 Platform API 漏检，OpenViking 的 Account/User URI ACL 仍阻止越权。
4. **浏览器不持有长期服务密钥**：产品前端只持有 Cookie 和 CSRF Token。
5. **Actor 与 Subject 分离**：管理员可按角色范围读取目标用户数据，但权限判断、数据归属和审计身份必须分别保存。
6. **先模块化单体，后按压力拆分**：先减少分布式事务和部署复杂度。
7. **多入口、同一身份与权限**：登录 Session、用户 API Key 和 OAuth 只改变认证方式，不产生第二套角色和门禁。
8. **初版不背兼容债**：不导入旧用户凭证或维持双身份存储；协议入口是否沿用由首版客户端需求决定。
9. **删除默认可恢复**：Account、用户和业务数据先软删除并保留 30 天，物理清理异步执行。
