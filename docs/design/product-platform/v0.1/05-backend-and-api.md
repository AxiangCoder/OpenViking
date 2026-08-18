# 05 后端模块与 API

> Design v0.1 · [返回版本索引](README.md)

## 11. 后端模块设计

建议新增目录：

```text
openviking/server/platform/
  __init__.py
  config.py
  models.py
  errors.py
  dependencies.py

  auth/
    password.py
    sessions.py
    csrf.py
    service.py

  iam/
    entities.py
    repository.py
    postgres_repository.py
    service.py
    permissions.py
    policy.py

  provisioning/
    service.py
    worker.py
    reconciler.py

  product/
    memories.py
    resources.py
    sessions.py
    search.py
    data_access.py
    recycle_bin.py

  audit/
    service.py
    repository.py

  routers/
    auth.py
    me.py
    product_memories.py
    product_resources.py
    product_sessions.py
    admin_accounts.py
    admin_users.py
    admin_user_data.py
    admin_roles.py
    admin_audit.py
    platform_accounts.py
    platform_user_data.py
```

数据库 migration：

```text
openviking/server/platform/migrations/
```

技术选型：

- SQLAlchemy 2.x async。
- `asyncpg` PostgreSQL driver。
- Alembic migration。
- Pydantic 2 request/response model。
- 复用现有 FastAPI、异常 envelope、Request ID 和 OpenTelemetry。

### 11.1 Platform 依赖链

```text
Cookie
  -> PlatformSessionDependency
  -> PlatformPrincipal
  -> require_permission(...)
  -> resolve_data_access(actor, subject)
  -> ProductFacadeService
  -> to_ov_context(principal, access)
  -> OpenVikingService
```

### 11.2 ProductFacadeService

该层负责：

- 把产品 ID 转换成受控 OpenViking URI。
- 普通用户接口固定使用 Actor 自己的 Account/User，不接受其他 Subject。
- Account Admin/Platform Super Admin 接口允许指定目标 Subject，但必须先执行 account/platform Scope 授权。
- 注入 `RequestContext`。
- 将 Actor、Subject、Action、Scope 和 Request ID 写入审计事件。
- 查询 `iam_deletion_jobs`，保证软删除对象不出现在正常列表，也不能被普通详情接口读取。
- 统一处理等待、任务、分页和错误。
- 将 OpenViking 底层响应转换成稳定产品 DTO。

示例：产品前端请求“我的记忆列表”，后端固定构造当前用户的 canonical User URI，不接受 `user_id` 参数；Account Admin 请求“查看成员记忆”时，路由中的 `user_id` 只作为 Subject，后端验证其属于当前 Account 后再构造 URI。

### 11.3 Provisioning 与双存储一致性

第一阶段仍需让现有 OpenViking APIKeyManager 知道 Account/User，因此会短期存在两个身份存储：

- PostgreSQL：产品 IAM 的事实来源。
- OpenViking accounts/users JSON：现有 API Key 和底层 API 的兼容镜像。

创建流程：

```text
1. PostgreSQL transaction
   - 创建 account/user，status=provisioning
   - 写 iam_outbox
2. Commit
3. Provisioning Worker
   - 调用现有 APIKeyManager / OpenVikingService 初始化 account/user
   - 初始化 OpenViking 目录
4. 成功：IAM status=active，outbox=completed
5. 失败：记录错误并指数退避重试
```

产品请求只允许 `active` 状态。管理页面展示 `provisioning/failed` 并支持安全重试。

删除采用 30 天软删除和异步清理：

- 先禁用登录和撤销会话。
- 写入 `deleted_at` 与 `purge_after=deleted_at+30 days`，对象从正常查询隐藏。
- 回收期内允许有权 Actor 恢复，并记录恢复审计。
- 期满后由幂等清理任务删除 OpenViking 数据；失败进入重试队列并对运维可见。
- 不把“删除登录用户”与“立即物理删除全部记忆”绑定成一个不可恢复请求。

### 11.4 中长期单一事实来源

第二阶段把 APIKeyManager 持久层抽象为 `IdentityCredentialRepository`：

- `VikingFsIdentityCredentialRepository`：兼容旧数据。
- `PostgresIdentityCredentialRepository`：新事实来源。
- APIKeyManager 继续提供现有方法和返回契约。

完成迁移后可停止双写，但这不属于第一阶段的必做项。

## 12. API 设计

### 12.1 路径和版本

- 产品平台 API：`/api/platform/v1/*`
- OpenViking 原生 API：`/api/v1/*`
- MCP：`/mcp`
- Studio：`/studio/*`

产品 API 不占用现有 `/api/v1`，避免与上游 OpenViking 升级冲突。

### 12.2 通用约定

- 复用 OpenViking `{status, result, error}` 响应 envelope。
- 写操作支持 `Idempotency-Key`，至少覆盖邀请、创建用户、创建 Account 和批量导入。
- 分页统一使用 cursor，列表返回 `items` 和 `next_cursor`。
- 时间统一 RFC 3339 UTC。
- 资源使用内部 UUID/ULID；OpenViking URI 不作为产品页面主 ID。
- 业务错误使用稳定 code，不依赖英文 message 判断。

新增错误码建议：

```text
ACCOUNT_SUSPENDED
USER_DISABLED
LOGIN_FAILED
SESSION_EXPIRED
CSRF_INVALID
ROLE_IN_USE
LAST_ACCOUNT_ADMIN_REQUIRED
PROVISIONING_PENDING
PROVISIONING_FAILED
PERMISSION_NOT_GRANTED
DELETION_PENDING
RESTORE_WINDOW_EXPIRED
```

### 12.3 Auth API

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/platform/v1/auth/login` | 无 | 登录并签发 Session Cookie |
| POST | `/api/platform/v1/auth/logout` | Session | 撤销当前会话并清 Cookie |
| POST | `/api/platform/v1/auth/logout-all` | Session | 撤销当前用户全部会话 |
| GET | `/api/platform/v1/auth/me` | Session | 返回当前用户、角色、权限摘要 |
| POST | `/api/platform/v1/auth/password/change` | Session | 修改密码并轮换会话 |
| POST | `/api/platform/v1/auth/password/reset/request` | 无 | 申请重置，不泄露账号是否存在 |
| POST | `/api/platform/v1/auth/password/reset/confirm` | Reset token | 完成重置 |

登录标识与邮箱唯一性仍待产品确认，以下请求体是 Account 范围登录方案的暂定形式；Platform Super Admin 不携带 Account。冻结认证设计后再固定 OpenAPI 契约：

```json
{
  "account": "acme",
  "login": "alice@example.com",
  "password": "***"
}
```

`auth/me` 响应：

```json
{
  "status": "ok",
  "result": {
    "account": {"id": "...", "code": "acme", "name": "Acme"},
    "user": {"id": "...", "code": "alice", "display_name": "Alice"},
    "roles": ["user"],
    "permissions": ["memory.read.self", "memory.write.self"],
    "can_switch_account": false,
    "csrf_token": "..."
  }
}
```

### 12.4 产品 API

| 方法 | 路径 | Permission | OpenViking 映射 |
| --- | --- | --- | --- |
| GET | `/api/platform/v1/memories` | `memory.read.self` | 当前 User memory roots |
| GET | `/api/platform/v1/memories/{id}` | `memory.read.self` | 受控 URI read |
| POST | `/api/platform/v1/memories/search` | `memory.read.self` | search/find |
| PUT | `/api/platform/v1/memories/{id}` | `memory.write.self` | content write |
| DELETE | `/api/platform/v1/memories/{id}` | `memory.delete.self` | 软删除；30 天后 rm |
| GET | `/api/platform/v1/resources` | `resource.read.shared` | account shared resources |
| POST | `/api/platform/v1/resources` | `resource.write.shared` | add_resource |
| DELETE | `/api/platform/v1/resources/{id}` | `resource.delete.shared` | 软删除；30 天后 rm |
| GET | `/api/platform/v1/sessions` | `session.read.self` | current user sessions |
| POST | `/api/platform/v1/sessions` | `session.write.self` | create session |
| POST | `/api/platform/v1/sessions/{id}/messages` | `session.write.self` | add message |
| POST | `/api/platform/v1/sessions/{id}/commit` | `session.commit.self` | commit |
| DELETE | `/api/platform/v1/sessions/{id}` | `session.delete.self` | 软删除；30 天后 delete session |
| GET | `/api/platform/v1/recycle-bin` | 对应资源的 self read | 当前用户回收站 |
| POST | `/api/platform/v1/recycle-bin/{id}/restore` | 对应资源的 self write | 30 天内恢复 |

产品 API 返回产品 DTO，不原样泄露内部绝对文件路径、系统目录和控制字段。普通用户请求永远不能切换 Account；`auth/me` 的 Account 是固定归属，不提供 Account 切换列表。

### 12.5 管理 API

| 方法 | 路径 | Permission |
| --- | --- | --- |
| GET | `/api/platform/v1/admin/users` | `user.read` |
| POST | `/api/platform/v1/admin/users` | `user.create` |
| PATCH | `/api/platform/v1/admin/users/{id}` | `user.update` |
| POST | `/api/platform/v1/admin/users/{id}/disable` | `user.disable` |
| DELETE | `/api/platform/v1/admin/users/{id}` | `user.delete` |
| GET | `/api/platform/v1/admin/users/{id}/deletion-preview` | `user.delete` |
| GET/POST | `/api/platform/v1/admin/roles` | `role.read/role.create` |
| PATCH/DELETE | `/api/platform/v1/admin/roles/{id}` | `role.update/role.delete` |
| PUT | `/api/platform/v1/admin/users/{id}/roles` | `role.assign` |
| GET | `/api/platform/v1/admin/audit-events` | `audit.read` |
| GET | `/api/platform/v1/admin/users/{id}/memories` | `memory.read.account` |
| GET | `/api/platform/v1/admin/users/{id}/sessions` | `session.read.account` |
| GET | `/api/platform/v1/admin/recycle-bin` | Account 范围恢复权限 |
| POST | `/api/platform/v1/admin/recycle-bin/{id}/restore` | Account 范围恢复权限 |

Account Admin API 的 Account 固定来自当前 Session；路径中的用户只作为 Subject，且必须属于该 Account。读取其他用户数据不授予修改、导出或删除能力。

Platform Super Admin 使用独立的平台级接口：

| 方法 | 路径 | Permission |
| --- | --- | --- |
| GET/POST | `/api/platform/v1/platform/accounts` | `account.read.platform/account.manage.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users` | `user.read.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/memories` | `memory.read.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/sessions` | `session.read.platform` |
| DELETE | `/api/platform/v1/platform/accounts/{account_id}` | `account.delete` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/deletion-preview` | `account.delete` |
| GET | `/api/platform/v1/platform/audit-events` | 平台审计读取权限 |
| GET | `/api/platform/v1/platform/recycle-bin` | 平台范围恢复权限 |
| POST | `/api/platform/v1/platform/recycle-bin/{id}/restore` | 平台范围恢复权限 |

Platform Super Admin 选择目标 Account 是管理浏览行为，不是把登录用户切换成该 Account 成员。每个管理请求都保留原 Actor，并将目标 Account/User 记录为 Subject。

`deletion-preview` 返回目标名称、影响对象分类及数量、是否可恢复和 `purge_after`。DELETE 成功只进入回收期，并返回 deletion job ID 与恢复截止时间；弹窗不产生可绕过后端授权的“已确认”凭据。
