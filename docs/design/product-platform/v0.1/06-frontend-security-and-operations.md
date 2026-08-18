# 06 前端、安全、初始部署与运维

> Design v0.1 · [返回版本索引](README.md)

## 13. 前端设计

### 13.1 新建 `web-platform`

不直接把产品页面塞入现有 `web-studio`，避免：

- 与上游 Studio 更新产生大量冲突。
- API Key 连接状态和网页登录状态互相污染。
- 产品导航、权限和运维导航混在一起。
- 普通用户误触底层 OpenViking 操作。

建议目录：

```text
web-platform/
  src/
    routes/
      login/
      app/
        home/
        memories/
        resources/
        sessions/
        profile/
          api-keys/
      admin/
        users/
        roles/
        audit/
        settings/
      platform/
        accounts/
        users/
        audit/
    components/
    features/
      auth/
      memories/
      resources/
      sessions/
      iam/
    lib/
      platform-client/
      permissions.ts
```

技术栈可沿用 Studio：React、TypeScript、TanStack Router、TanStack Query、Vite 和现有 UI 组件风格。

### 13.2 路由

```text
/login
/forgot-password

/app
/app/memories
/app/resources
/app/sessions
/app/profile
/app/profile/api-keys

/admin/users
/admin/users/$userId/api-keys
/admin/roles
/admin/audit
/admin/settings

/platform/accounts
/platform/accounts/$accountId/users
/platform/accounts/$accountId/users/$userId/data
/platform/accounts/$accountId/users/$userId/api-keys
/platform/audit
```

`/platform` 仅 Platform Super Admin 可进入。这里选择目标 Account 是查看管理对象，不会改变登录者的 Actor 身份，也不是普通用户意义上的“切换 Account”。Account Admin 和 User 的 Account 始终固定。

### 13.3 前端权限策略

- 应用启动调用 `/auth/me`。
- 未登录访问 `/app`、`/admin` 或 `/platform` 跳转 `/login`。
- 路由 Guard 依据 Permission 隐藏或阻止页面。
- 按钮依据 Permission 隐藏或禁用。
- 后端仍必须重复鉴权；前端权限只负责体验，不是安全边界。
- 权限变化或收到 401/403 时刷新 `/auth/me` 并更新界面。

“路由 Guard”就是进入页面前的门卫；它能避免用户看到不该看的入口，但真正的锁必须在后端。

### 13.4 前端本地存储规则

允许保存：

- 主题、语言、表格列、最近打开页面等非敏感偏好。

禁止保存：

- Root API Key。
- User API Key。
- Session Token。
- 密码。
- 权限快照作为安全依据。

`/app/profile/api-keys` 允许用户显式创建个人 API Key。创建成功后通过专用一次性结果页展示完整 Key，并明确提示立即复制；离开页面后不能再次查看。前端只在当前内存状态中短暂持有明文，不写入任何 Web Storage、URL、错误上报、埋点或剪贴板历史管理逻辑。

### 13.5 Studio 处理

- `/studio` 保持现有 bundle 和路由。
- 第一阶段只允许受控网络、VPN 或管理员访问。
- Studio 继续使用 API Key 连接模型，但用户凭证必须由新 IAM 签发；Root API Key 只允许受控运维人员在隔离环境使用。
- Root 管理密钥不预置进公开静态资源。
- 后续可用反向代理 SSO 给 `/studio` 再加一层访问保护。

## 14. 数据隔离与安全

### 14.1 双层授权

```text
第一层：Platform Permission
  决定“能不能执行这个产品动作”

第二层：OpenViking Namespace ACL
  决定“这个身份能不能访问这份具体数据”
```

两层之间由服务端根据已授权的 DataAccessContext 构造目标 Subject 的最小权限 `RequestContext`。任何一层拒绝都终止请求，原 Actor 只进入授权与审计上下文，不会被客户端 Header 覆盖。

### 14.2 防止 IDOR

IDOR 是“改一下 URL 里的 ID 就读到别人数据”的漏洞。防护要求：

- 普通产品接口不接收当前 `account_id/user_id`，固定使用 Actor 自己的数据范围。
- 管理接口允许提供 Subject ID，但 Account Admin 查询必须附加当前 Account 条件；只有 Platform Super Admin 可使用 platform Scope 指定其他 Account。
- 由后端从数据库映射产品 ID 到 OpenViking URI。
- 映射后再由 OpenViking `_ensure_access` 校验。

### 14.3 低层 API 暴露策略

生产推荐：

- `/api/platform/v1/*` 面向浏览器和产品用户。
- `/api/v1/*`、`/mcp` 仅按需要公开，并继续要求 API Key/OAuth。
- `/api/v1/admin/*` 限制在内网、VPN 或受控管理员凭据。
- `trusted` auth mode 只用于受保护的内部网关链路，不能直接暴露给公网浏览器。

### 14.4 密钥与敏感数据

- Root API Key 存 Secret Manager 或部署环境，不写进代码、镜像和前端环境变量。
- 用户 API Key 明文只返回一次；PostgreSQL 只保存 hash、前缀、末四位、状态和使用元数据。
- 用户 API Key 的角色和 Permission 不写入凭证记录，每次请求从当前 IAM 用户实时计算。
- 一个插件泄露时只撤销对应具名 Key；不要求用户同时替换其他设备和插件的 Key。
- 生产全站 HTTPS。
- 日志中统一脱敏 Authorization、Cookie、API Key、密码和 Token。
- 数据库备份加密，恢复操作审计。
- CORS 只允许正式产品源；优先同源部署。

### 14.5 高风险操作

以下操作要求“展示影响范围的破坏性操作确认弹窗”和完整审计：

- 删除 Account。
- 删除用户全部 OpenViking 数据。
- 发起其他用户密码重置，或撤销其他用户 API Key。
- 修改、导出或删除其他用户数据。
- 导出大量记忆或资源。

确认弹窗必须展示操作对象、目标 Account/User、预计影响数量、是否可恢复和恢复截止时间。用户只需点击“确认”或“取消”，v0.1 不要求重新输入密码、输入 Account 名称或第二人审批。

前端弹窗只用于防误触，不是安全边界。提交后后端仍需重新校验 Session、CSRF、Permission、Actor/Subject Scope 和目标当前状态；成功、失败与拒绝都写入审计。重复提交必须通过幂等键或资源状态检查避免重复执行。

### 14.6 软删除与回收站

- Account、User、Memory、Session 和 Resource 默认先软删除。
- 恢复窗口固定为 30 天，删除后从正常列表隐藏并进入回收站。
- Account/User 进入回收期时立即禁止登录、撤销 Session，并停止新的业务写入。
- User 可恢复自己误删且仍在回收期内的数据；Account Admin 可恢复当前 Account 范围对象；Platform Super Admin 可恢复全平台范围对象。
- 期满后后台 Worker 执行幂等物理清理；清理失败不延长对象可访问性，但必须告警并重试。
- 审计事件独立保留，不随业务对象物理清理。

## 15. 初始部署与凭证启用

### 15.1 初版前提

- v0.1 是产品系统第一次正式建立 IAM，不存在需要保留的旧产品用户门禁。
- 不导入当前 OpenViking accounts/users/API Key registry，也不设置新旧鉴权并行期。
- PostgreSQL 是 Account、User、Role、Permission、Session 和用户 API Key 的唯一身份事实来源。
- `/api/v1/*`、`/mcp`、Bearer 和 `X-Api-Key` 可以作为 v0.1 的正式集成协议继续使用，但这属于首版接口选择，不代表接受旧凭证或旧权限结果。

### 15.2 首次初始化顺序

1. 部署 PostgreSQL 并执行全新的 Platform schema migration。
2. 通过一次性部署命令创建首位 Platform Super Admin；不使用 Root API Key 代替人类管理员。
3. 由 Platform Super Admin 创建 Account 和首位 Account Admin。
4. Provisioning Worker 使用内部 `SystemPrincipal` 初始化对应 OpenViking namespace。
5. Account Admin 创建或邀请用户并分配角色。
6. 用户登录 `/app/profile/api-keys`，按自己的插件或设备创建具名 API Key。
7. 将一次性显示的 Key 配置到 Codex、OpenClaw、OpenCode、SDK、CLI 或 MCP 客户端。
8. 验证 Session、API Key 和 OAuth 对同一用户产生一致的 Permission 和数据范围。

Root API Key 只保留为受控部署与底层运维能力，不写入产品用户、角色或凭证表，也不能作为插件的常规凭证。

### 15.3 开发数据处理

由于不存在生产旧门禁，开发阶段允许清空并重新生成 IAM 测试数据。若需要保留已有 OpenViking 业务样本，应通过明确的测试数据初始化脚本绑定到新建 Account/User；不能自动把旧 Key 当成新用户登录凭证。

### 15.4 回滚

回滚以同一套 PostgreSQL IAM 为边界：

1. 发布前备份 PostgreSQL 与 OpenViking 数据卷。
2. 应用和 schema migration 必须提供对应的向下兼容发布顺序或可验证的 down migration。
3. 回滚应用版本时仍使用 PostgreSQL 用户和凭证，不回退到旧 JSON registry 门禁。
4. 若凭证 schema 已发生不可逆变化，先恢复备份到独立实例验证，再切换流量。

## 16. 部署设计

### 16.1 第一阶段部署单元

```text
reverse-proxy
openviking-product-server
postgresql（生产可使用托管 RDS）
```

沿用现有 VectorDB、模型服务和 OpenViking 数据卷配置。

### 16.2 路由建议

| 路径 | 上游/处理器 |
| --- | --- |
| `/login`, `/app/*`, `/admin/*` | `web-platform` SPA |
| `/studio/*` | 现有 `web-studio` SPA |
| `/api/platform/v1/*` | Platform Router |
| `/api/v1/*` | OpenViking Router |
| `/mcp` 和 OAuth well-known | OpenViking MCP/OAuth |

同源部署可以减少 CORS 和 Cookie 配置错误。

### 16.3 配置建议

新增配置示例：

```json
{
  "platform": {
    "enabled": true,
    "public_base_url": "https://product.example.com",
    "database_url_env": "OPENVIKING_PLATFORM_DATABASE_URL",
    "session": {
      "idle_ttl_seconds": 86400,
      "absolute_ttl_seconds": 2592000,
      "cookie_secure": true,
      "same_site": "lax"
    },
    "password": {
      "min_length": 12
    },
    "provisioning": {
      "worker_enabled": true,
      "max_attempts": 10
    },
    "deletion": {
      "retention_days": 30,
      "purge_worker_enabled": true
    }
  }
}
```

数据库 URL、Cookie 签名密钥、邮件 Provider 密钥和 Root API Key 必须通过环境变量或 Secret Manager 注入。

### 16.4 Redis 决策

第一阶段不强制 Redis：

- Session 存 PostgreSQL。
- 权限缓存先使用进程内短 TTL，并以 `permission_version` 校验。
- 登录限流先使用 PostgreSQL 或单实例内存实现，但多实例前必须迁移到共享限流后端。

当进入多实例、较高登录 QPS 或需要实时全局限流时，再引入 Redis。

## 17. 审计与可观测性

### 17.1 必须审计的事件

- 登录成功、登录失败、登出、会话撤销。
- 用户 API Key 创建、撤销、到期拒绝和认证失败；成功业务请求在对应审计事件中记录认证方式与凭证 ID，不额外为每次调用生成重复 Key 事件。
- 用户邀请、启用、禁用、删除。
- 密码重置、用户 API Key 撤销和 Root 凭据轮换。
- Role 创建、修改、删除和分配。
- 高风险数据删除、批量导出和管理员跨范围数据访问。
- Account Admin/Platform Super Admin 跨用户读取；记录 Actor 与 Subject，不记录数据正文。
- Provisioning 成功、失败和人工重试。
- 权限拒绝。

### 17.2 日志关联

每个请求沿用 OpenViking Request ID，并写入：

- HTTP 日志。
- Platform Audit Event。
- OpenTelemetry trace。
- 后台 Provisioning Task。

管理员跨用户或跨 Account 访问时，HTTP 日志只记录脱敏标识；Platform Audit Event 必须记录 `actor_user_id`、`actor_account_id`、`authentication_method`、脱敏的 `actor_credential_id`、`subject_account_id`、`subject_user_id`、`action`、`scope` 和结果。通过插件/MCP 使用用户 API Key 时，Actor 仍是该用户，`actor_credential_id` 用于区分具体设备或插件。

不把 `user_id`、邮箱等高基数字段无条件放进 Prometheus Label。需要 Account 维度时沿用现有 allowlist 和数量上限思想。

### 17.3 健康检查

在现有 `/health`、`/ready` 基础上增加：

- PostgreSQL 连通性。
- IAM migration 版本。
- Provisioning backlog 和失败数量。
- Session cleanup worker 状态。
- 软删除待清理数量、最早 `purge_after` 和 Purge Worker 状态。
