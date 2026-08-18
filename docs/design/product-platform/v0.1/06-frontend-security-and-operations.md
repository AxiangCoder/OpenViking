# 06 前端、安全、迁移与运维

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

/admin/users
/admin/roles
/admin/audit
/admin/settings

/platform/accounts
/platform/accounts/$accountId/users
/platform/accounts/$accountId/users/$userId/data
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

### 13.5 Studio 处理

- `/studio` 保持现有 bundle 和路由。
- 第一阶段只允许受控网络、VPN 或管理员访问。
- Studio 继续使用现有 API Key 连接模型。
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
- 开启 `encryption.api_key_hashing.enabled=true`。
- 生产全站 HTTPS。
- 日志中统一脱敏 Authorization、Cookie、API Key、密码和 Token。
- 数据库备份加密，恢复操作审计。
- CORS 只允许正式产品源；优先同源部署。

### 14.5 高风险操作

以下操作要求“展示影响范围的破坏性操作确认弹窗”和完整审计：

- 删除 Account。
- 删除用户全部 OpenViking 数据。
- 重置其他用户凭据。
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

## 15. 迁移与兼容方案

### 15.1 迁移对象

现有 OpenViking 数据分为两类：

1. 控制元数据：Account、User、Role、API Key。
2. 业务数据：VikingFS、VectorDB、Session、Memory、Resource、Skill。

本次迁移只复制/映射控制元数据，不移动业务数据路径。保持 `ov_account_id` 和 `ov_user_id` 不变即可继续访问原数据。

### 15.2 迁移步骤

1. 冻结基线版本并备份当前 OpenViking 数据、配置和 API Key 元数据。
2. 创建 PostgreSQL schema 和默认 Permission。
3. 读取现有 accounts/users registry，写入：
   - `iam_accounts`
   - `iam_users`
   - 默认 Role
   - UserRole 绑定
4. 角色映射：
   - 原 `admin` -> `account_admin`
   - 原 `user` -> `user`
   - Root API Key 不导入用户表，也不自动生成 Platform Super Admin；首位平台管理员走独立初始化流程
5. 现有用户没有密码：状态设为 `invited` 或 `active_without_login`，通过安全邀请设置密码。
6. 保持现有 User API Key 有效，SDK/CLI 不受影响。
7. 逐用户验证 Platform Session 转换后的 `RequestContext` 能读取原数据。
8. 启用 Platform API 和 `/app`。
9. 稳定后再决定是否把 APIKeyManager 持久层迁移到 PostgreSQL。

### 15.3 兼容期规则

- 产品登录与 API Key 登录可并行存在。
- Password/Session 身份由 PostgreSQL 解析。
- User API Key 身份继续由现有 APIKeyManager 解析。
- 两者必须解析到相同 `(account_id, user_id)`。
- 角色不一致时：
  - Platform API 以 PostgreSQL RBAC 为准。
  - OpenViking 低层 API 以现有 Base Role 为准。
  - Provisioning Reconciler 报警并修复 Base Role 镜像。

### 15.4 回滚

第一阶段不删除原 Account/User/API Key registry，因此可回滚：

1. 关闭 `/app` 和 `/api/platform/v1` 路由。
2. 恢复原镜像或原进程版本。
3. OpenViking 业务数据和原 API Key 继续可用。
4. PostgreSQL IAM 数据保留但停止写入，供后续排查或重新迁移。

回滚不能依赖从 PostgreSQL 反向重建所有 OpenViking 数据。

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
- 用户邀请、启用、禁用、删除。
- 密码重置和凭据轮换。
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

管理员跨用户或跨 Account 访问时，HTTP 日志只记录脱敏标识；Platform Audit Event 必须记录 `actor_user_id`、`actor_account_id`、`subject_account_id`、`subject_user_id`、`action`、`scope` 和结果。

不把 `user_id`、邮箱等高基数字段无条件放进 Prometheus Label。需要 Account 维度时沿用现有 allowlist 和数量上限思想。

### 17.3 健康检查

在现有 `/health`、`/ready` 基础上增加：

- PostgreSQL 连通性。
- IAM migration 版本。
- Provisioning backlog 和失败数量。
- Session cleanup worker 状态。
- 软删除待清理数量、最早 `purge_after` 和 Purge Worker 状态。
