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
    api_credentials.py
    mcp_oauth_principal.py
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
    dashboard.py
    memories.py
    resources.py
    skills.py
    sessions.py
    search.py
    activity.py
    watches.py
    privacy_configs.py
    target_policy.py
    data_access.py
    recycle_bin.py

  integrations/
    mcp_oauth.py
    oauth_repository.py

  audit/
    service.py
    repository.py

  routers/
    auth.py
    me.py
    api_credentials.py
    product_memories.py
    product_resources.py
    product_skills.py
    product_sessions.py
    product_search.py
    product_activity.py
    product_integrations.py
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
Browser Cookie ----------> PlatformSessionDependency ---+
User API Key ------------> ApiCredentialDependency -----+--> AuthenticatedUserPrincipal
OAuth Access Token ------> OAuthPrincipalDependency ----+             |
                                                                      v
                                                        require_permission(...)
                                                                      |
                                                        resolve_data_access(actor, subject)
                                                                      |
                                                        ProductFacadeService
                                                                      |
                                                        to_ov_context(principal, access)
                                                                      |
                                                        OpenVikingService
```

三个入口只负责证明“这个请求是谁发起的”。Role、Permission 和数据范围统一从 PostgreSQL IAM 计算，不在 API Key 或 OAuth Token 中维护另一套授权结果。

### 11.2 ProductFacadeService

该层负责：

- 把产品 ID 转换成受控 OpenViking URI。
- 把目标 URI 规范化并分类为 `user_private/account_shared/internal`，校验 URI、Account、Owner User 与 PostgreSQL 引用记录一致。
- 普通用户接口固定使用 Actor 自己的 Account/User，不接受其他 Subject。
- Account Admin/Platform Super Admin 接口允许指定目标 Subject，但必须先执行 account/platform Scope 授权。
- 普通用户新增 Resource/Skill 时固定写入自己的 User 私有区；只有共享管理接口才能写入 Account 共享区，且必须拥有对应 `account_shared.*` Permission。
- Resource Upload ID 绑定 Actor、Account 和目标 Visibility；私有 Upload 不能被共享导入接口消费，过期或已消费 ID 不能重放。
- Resource 名称、说明和标签是产品元数据；修改名称不移动 canonical URI。解析正文、Abstract、Overview 和内部目录在 v0.1 只读。
- Refresh/Watch 使用 staging/版本指针，成功后原子切换；处理期间继续提供上一次成功版本，旧 Operation generation 不能覆盖新状态。
- 注入 `RequestContext`。
- 将 Actor、Subject、Action、Scope 和 Request ID 写入审计事件。
- 查询 `iam_deletion_jobs`，保证软删除对象不出现在正常列表，也不能被普通详情接口读取。
- 统一处理等待、任务、分页和错误。
- 为对外异步 Task/Watch 建立 `platform_operation_refs`，使状态列表、取消和触发都能回到目标对象重新授权。
- 将 OpenViking 底层响应转换成稳定产品 DTO。

示例：产品前端请求“在我的 Memory 中检索”，后端固定构造当前用户的 canonical User URI，不接受 `user_id` 参数；Account Admin 请求“检索成员 Memory”时，路由中的 `user_id` 只作为 Subject，后端验证其属于当前 Account 后再构造 URI。v0.1 不为 Memory 建立独立列表或 CRUD API。

Account 共享数据没有 Subject User。共享 Resource/Skill 访问只记录 `subject_account_id`，`subject_user_id` 为空；`created_by_actor_user_id` 是审计信息，不参与授权。不得为了复用“我的数据”接口，把共享对象伪装成创建者的私有对象。

### 11.3 Provisioning 与单一身份事实来源

v0.1 没有旧门禁兼容要求，PostgreSQL 从第一天起就是 Account、User、Role、Permission、登录 Session 和用户 API Key 的唯一身份事实来源。OpenViking accounts/users JSON 不作为产品鉴权来源，也不进行凭证双写。

创建流程：

```text
1. PostgreSQL transaction
   - 创建 account/user，status=provisioning
   - 写 iam_outbox
2. Commit
3. Provisioning Worker
   - 使用内部 SystemPrincipal 调用 OpenVikingService
   - 使用 ov_account_id/ov_user_id 初始化 OpenViking namespace 与目录
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

### 11.4 用户 API Key 生命周期与解析

`ApiCredentialService` 直接使用 PostgreSQL `iam_api_credentials`：

1. 已登录用户显式创建具名 Key，服务端生成随机 secret。
2. 事务中写入随机 `public_id`、`SHA-256(secret)`、末四位、到期时间和创建 Actor；Key 不编码 Account/User/Role。
3. 完整 Key 只在成功响应中返回一次；后续接口无法重新取回。
4. `/api/v1/*`、`/mcp` 和需要 API 鉴权的集成入口从 Bearer 或 `X-Api-Key` 提取 Key。
5. 解析器按 `public_id` 定位凭证、常量时间校验 secret hash，再加载所属 User、Account、Role 和 Permission，生成 `AuthenticatedUserPrincipal(authentication_method="api_key")`。
6. 业务 Router 使用同一 AuthorizationService 检查 Permission，并由 OpenViking namespace ACL 兜底。
7. 撤销、到期、用户禁用或进入删除期后立即拒绝；`last_used_at` 可异步、限频更新。

API Key 不自动随用户创建而签发，Account Admin 也不能替用户创建并看到明文。管理员只能按权限查看 Key 元数据和撤销疑似泄露的 Key。

OAuth Access Token 最终也映射到同一 `AuthenticatedUserPrincipal`。MCP middleware 与 REST auth dependency 必须复用同一个 Principal Resolver，避免 MCP 成为绕过 RBAC 的旁路。

v0.1 不定义 Service Account 的 repository、resolver 或 credential type。内部 Worker 使用不可从公网提交、不可导出的 `SystemPrincipal`，不能拿 Root/User API Key 代替。

### 11.5 低层 OpenViking API、MCP 与 URI Policy

只给 `/api/platform/v1` 加权限检查是不够的。现有 `/api/v1`、MCP、SDK/CLI 和插件最终都可以触达 OpenViking 低层动作，因此必须在 Router/MCP Tool 调用 `OpenVikingService` 之前复用 `TargetPolicy + AuthorizationService`。

最低映射规则：

| 目标/动作 | 必需 Permission |
| --- | --- |
| 读取/检索自己的 `viking://user/{actor_ov_user_id}/resources/**` | `resource.user_private.read.self` |
| 新增、写入、改名、移动、打标签、恢复自己的私有 Resource | `resource.user_private.write.self` |
| 删除自己的私有 Resource | `resource.user_private.delete.self` |
| 读取/检索 `viking://resources/**` | 当前 Account 使用 `resource.account_shared.read.account`；平台管理使用 `.platform` |
| 任何会改变 `viking://resources/**` 的动作 | `resource.account_shared.write.account/platform`；删除使用对应 `.delete.*` |
| 读取、使用、管理自己的 User 私有 Skill | `skill.user_private.read.self`、`.use.self`、`.manage.self` |
| 查看其他 User 的私有 Skill | Account Admin 使用 `.read.account`；Platform Super Admin 使用 `.read.platform`，两者均不获得编辑、删除或恢复 |
| 发布其他 User 的私有 Skill | 仅 Account Admin 使用 `skill.user_private.publish.account`；保持 ID/名称不变并转为 Account 共享 |
| 读取、使用、管理 `viking://agent/skills/**` | 普通 User/Account Admin 按 `.read/.use.account`；仅 Account Admin 有 `.manage.account`；Platform 只有 `.read.platform` |
| 访问 `agent/endpoints/tools/payments` 或内部根 | 产品/用户集成默认拒绝；必须另行定义控制面 Permission |

“会改变”包括但不限于 `add_resource`、`write`、`mkdir`、`mv` 的源与目标、`set_tags`、归档、导入、恢复和批量操作；不能只保护 POST 创建接口。跨可见性移动不得作为普通 `mv` 放行，必须走“复制/发布为新对象”业务动作。

为避免 Product API 与低层入口形成两份内容目录，所有对外创建 Resource/Skill 的入口还必须共用 Content Registry Service：先按 `Idempotency-Key` 建立 `platform_content_refs(status=provisioning)`，再调用 OpenViking，成功后写入 canonical URI 并置为 `active`；失败置为 `failed` 并由 Reconciler 清理或重试。产品列表只返回 `active` 引用。低层 `write/mkdir` 不允许在未登记的新顶级内容根下直接创建产品对象；应改走 `add_resource/add_skill`，已有对象内部写入则解析现有引用并更新审计。这样 API Key、MCP 或插件创建的内容会立即出现在相同产品页面中。

默认目标规则：

- 用户 API Key/OAuth 调用 `add_resource` 且未显式指定目标时，服务端强制使用 `viking://user/{actor_ov_user_id}/resources/**`，不能沿用源码当前的 `viking://resources` 默认值。
- Skill 默认目标保持 User 私有 `viking://user/{actor_ov_user_id}/skills/**`。
- 显式指定 Account 共享目标不会改变身份，只会触发共享写 Permission 检查；普通 User 返回 403。
- 搜索必须由服务端按有效权限注入可见根：自己的私有根 + 当前 Account 共享根；客户端不能通过额外 URI 扩大搜索范围。
- Platform Super Admin 的跨 Account 低层调用只能来自平台管理 API，不接受普通用户凭证通过 Header 切换目标 Account。

当前源码中 `Namespace.is_accessible()` 会允许同 Account 用户触达 `viking://resources/**`，但它没有表达“普通 User 只读、Account Admin 可写”的产品策略。因此 OpenViking Namespace ACL 仍作为隔离兜底，新的 URI Policy 才是共享区写权限的强制边界。

额外的入口约束：

- MCP `forget` 在 v0.1 仍可保留 Tool 名称，但产品语义改为进入 30 天软删除回收期；它不能调用不可恢复的底层 `rm`。物理清理由 Purge Worker 执行。
- MCP `list_watches/cancel_watch` 以及原生 Watch Router 必须先通过 `platform_operation_refs` 解析目标 Resource；取消 Watch 需要目标 Resource 当前写权限。
- WebDAV 当前固定映射到 Account 共享 `viking://resources/**`，又包含 PUT、DELETE、MKCOL、MOVE 等写操作。v0.1 没有已确认的 WebDAV 业务需求，因此生产公网不挂载 `/webdav/resources`，避免形成共享区写入旁路。
- Snapshot、Pack、Backup、Import、Restore、任意 URI 文件系统写入和系统修复接口不进入 v0.1 产品 API；只能在私网运维面以 Root/System 身份执行。

## 12. API 设计

### 12.1 路径和版本

- 产品平台 API：`/api/platform/v1/*`
- OpenViking 原生 API：`/api/v1/*`
- MCP：`/mcp`

产品 API 不占用现有 `/api/v1`，避免与上游 OpenViking 升级冲突。

### 12.2 通用约定

- 复用 OpenViking `{status, result, error}` 响应 envelope。
- 写操作支持 `Idempotency-Key`，至少覆盖创建用户、创建 Account 和批量导入。
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
PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN
DELETION_PENDING
RESTORE_WINDOW_EXPIRED
```

### 12.3 Auth API

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/platform/v1/auth/login` | 无 | 登录并签发登录 Session Cookie |
| POST | `/api/platform/v1/auth/logout` | 登录 Session + CSRF | 撤销当前登录会话并清 Cookie |
| POST | `/api/platform/v1/auth/logout-all` | 登录 Session + CSRF | 撤销当前用户全部登录会话 |
| GET | `/api/platform/v1/auth/me` | 登录 Session | 返回当前用户、角色、权限摘要 |
| POST | `/api/platform/v1/auth/password/change` | 登录 Session + CSRF | 修改密码：请求体必填 `old_password` 与 `new_password`；旧密码校验失败返回统一 `LOGIN_FAILED` 并计入登录限流；成功后轮换当前登录会话 |

邮箱是全局唯一登录标识，登录请求不提交 Account；服务端根据规范化邮箱解析固定的 User/Account：

```json
{
  "email": "alice@example.com",
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
    "permissions": ["memory.read.self", "session.read.self", "session.write.self"],
    "can_switch_account": false,
    "csrf_token": "..."
  }
}
```

### 12.4 用户 API Key 与集成入口

用户在产品设置页管理自己的 API Key：

该组接口只面向 `account_id` 非空的 Account Admin/User；Platform Super Admin 不创建平台级个人 API Key。

| 方法 | 路径 | 鉴权 | Permission | 说明 |
| --- | --- | --- | --- | --- |
| GET | `/api/platform/v1/me/api-keys` | 登录 Session | `credential.read.self` | 只返回 Key 元数据和掩码 |
| POST | `/api/platform/v1/me/api-keys` | 登录 Session + CSRF | `credential.create.self` | 创建具名 Key，完整明文只返回一次 |
| DELETE | `/api/platform/v1/me/api-keys/{id}` | 登录 Session + CSRF | `credential.revoke.self` | 撤销自己的 Key，幂等 |

创建请求：

```json
{
  "name": "Codex on MacBook",
  "expires_at": "2026-11-18T00:00:00Z"
}
```

成功响应中的 `api_key` 只出现这一次；列表接口不得再次返回：

```json
{
  "status": "ok",
  "result": {
    "id": "credential-id",
    "name": "Codex on MacBook",
    "api_key": "ovk_u.public-id.secret",
    "key_last_four": "4f2a",
    "expires_at": "2026-11-18T00:00:00Z"
  }
}
```

集成入口统一规则：

- `/api/v1/*` 和 `/mcp` 接受用户 API Key 或用户 OAuth Token。
- API Key/OAuth 解析出的 Account/User 是 Actor，客户端不得通过 Header 或请求体切换身份。
- 每个低层 API/MCP Tool 必须映射到 Permission Code；同一用户通过登录 Session、API Key、OAuth 调用相同业务动作时授权结果一致。
- Codex、OpenClaw、OpenCode 等插件使用谁的 Key，就以谁的身份读写和审计。
- 所有低层目标先规范化并分类；普通 User 可读取 Account 共享 Resource、读取/使用共享 Skill，但写入和删除共享区返回 403。
- 未显式指定 Resource 目标时默认写入调用用户的 User 私有区；客户端不能利用源码默认值把个人内容写入共享区。
- v0.1 不接受 `principal_type=service_account`，也不签发 Service Account Key。

MCP OAuth 的协议端点（Discovery、Dynamic Client Registration、Authorize、Token）继续遵循 OAuth 2.1/PKCE；产品授权页面和身份确认改走以下接口：

| 方法 | 路径 | 鉴权 | Permission | 说明 |
| --- | --- | --- | --- | --- |
| GET | `/api/platform/v1/integrations/mcp/oauth/pending/{pending_id}` | 无 | 无 | 只返回客户端名称、回调域名和 Scope 等公开安全元数据 |
| POST | `/api/platform/v1/integrations/mcp/oauth/authorize` | 登录 Session + CSRF | `integration.oauth.authorize.self` | 使用 `pending_id` 或跨设备 code 批准/拒绝，不接受 API Key/OAuth Token |
| GET | `/api/platform/v1/me/oauth-grants` | 登录 Session | `integration.oauth.read.self` | 当前用户已经授权的 MCP 客户端 |
| DELETE | `/api/platform/v1/me/oauth-grants/{grant_id}` | 登录 Session + CSRF | `integration.oauth.revoke.self` | 撤销 Grant 及其 Token family |

`/oauth/consent` 与 `/oauth/verify` 是 `web-platform` 的独立页面路由，不是 OIDC 登录页，也不属于 `/studio`。同设备流程提交 `pending_id`；跨设备流程提交用户看到的短期 display code。服务端从登录 Session 确定 User，忽略任何客户端提交的 Account/User/Role。

### 12.5 产品 API

| 方法 | 路径 | Permission | OpenViking 映射 |
| --- | --- | --- | --- |
| GET | `/api/platform/v1/dashboard` | 当前 User 基础访问 | 个人内容、处理状态和最近活动的受控聚合 |
| POST | `/api/platform/v1/search/find` | 按每个目标对象的 read Permission | 快速语义检索；复用源码 `find`，固定检索本人私有根 + 当前 Account 共享根 |
| POST | `/api/platform/v1/search/search` | 按每个目标对象的 read Permission；`session_id` 另需 `session.read.self` | 结合调用者自己的 Session 上下文检索；复用源码 `search`，检索根同上 |
| GET | `/api/platform/v1/resources/capabilities` | 当前 User 基础访问 | 返回来源类型、上传限制、Watch 能力和周期预设 |
| POST | `/api/platform/v1/me/resource-uploads` | `resource.user_private.write.self` | 创建当前 User 私有作用域的一次性 Upload ID |
| GET | `/api/platform/v1/me/resources` | `resource.user_private.read.self` | 当前 User 私有 Resource |
| POST | `/api/platform/v1/me/resources/imports` | `resource.user_private.write.self` | 文件/公开网页/公开 Git，固定添加到当前 User 私有区 |
| GET | `/api/platform/v1/me/resources/{id}` | `resource.user_private.read.self` | 产品详情，不返回 Viking URI |
| PATCH | `/api/platform/v1/me/resources/{id}` | `resource.user_private.write.self` | 只修改名称、说明和标签；使用乐观锁 |
| POST | `/api/platform/v1/me/resources/{id}/replace` | `resource.user_private.write.self` | 上传文件替换，旧成功版本在处理期间保持可读 |
| POST | `/api/platform/v1/me/resources/{id}/refresh` | `resource.user_private.write.self` | 稳定远程来源 Refresh |
| POST | `/api/platform/v1/me/resources/{id}/retry` | `resource.user_private.write.self` | 重试首次失败导入；上传/一次性 URL 需重新提交来源 |
| POST | `/api/platform/v1/me/resources/{id}/publish` | 私有 read + `resource.account_shared.write.account` | 仅 Account Admin 发布自己的私有 Resource 为独立共享副本 |
| DELETE | `/api/platform/v1/me/resources/{id}` | `resource.user_private.delete.self` | 软删除；30 天后 rm |
| GET | `/api/platform/v1/me/resources/{id}/watch` | 对应私有 Resource read | 查看自己的自动同步设置和状态 |
| PUT | `/api/platform/v1/me/resources/{id}/watch` | `resource.user_private.write.self` | 创建或更新自己的 Resource Watch |
| POST | `/api/platform/v1/me/resources/{id}/watch/pause` | `resource.user_private.write.self` | 暂停并保留配置 |
| POST | `/api/platform/v1/me/resources/{id}/watch/resume` | `resource.user_private.write.self` | 恢复后重新计算下次执行时间 |
| POST | `/api/platform/v1/me/resources/{id}/watch/trigger` | `resource.user_private.write.self` | 手动触发一次同步并产生 Task |
| DELETE | `/api/platform/v1/me/resources/{id}/watch` | `resource.user_private.write.self` | 停止自动同步，不删除 Resource |
| GET | `/api/platform/v1/me/resources/{id}/nodes` | `resource.user_private.read.self` | 当前 Resource 内部只读节点分页，隐藏控制文件 |
| GET | `/api/platform/v1/me/resources/{id}/operations` | `task.read.self` | 当前 Resource 的脱敏 Activity |
| GET | `/api/platform/v1/me/resources/{id}/deletion-preview` | `resource.user_private.delete.self` | 删除影响范围和恢复截止时间 |
| POST | `/api/platform/v1/account/resource-uploads` | `resource.account_shared.write.account` | Account 共享作用域的一次性 Upload ID |
| GET | `/api/platform/v1/account/resources` | `resource.account_shared.read.account` | 当前 Account 共享 Resource，只读接口对普通 User 开放 |
| POST | `/api/platform/v1/account/resources/imports` | `resource.account_shared.write.account` | Account Admin 导入共享 Resource |
| GET | `/api/platform/v1/account/resources/{id}` | `resource.account_shared.read.account` | 普通 User 可读取的共享详情 |
| PATCH | `/api/platform/v1/account/resources/{id}` | `resource.account_shared.write.account` | Account Admin 只修改名称、说明和标签 |
| POST | `/api/platform/v1/account/resources/{id}/refresh` | `resource.account_shared.write.account` | Account Admin Refresh 稳定远程来源 |
| DELETE | `/api/platform/v1/account/resources/{id}` | `resource.account_shared.delete.account` | Account Admin 软删除共享 Resource |
| GET | `/api/platform/v1/account/resources/{id}/watch` | `resource.account_shared.read.account` | 共享 Resource Watch 状态 |
| PUT/POST/DELETE | `/api/platform/v1/account/resources/{id}/watch/*` | `resource.account_shared.write.account` | Account Admin 配置、暂停、恢复、触发或删除共享 Watch |
| GET | `/api/platform/v1/me/skills` | `skill.user_private.read.self` | 当前 User 私有 Skill |
| POST | `/api/platform/v1/me/skills` | `skill.user_private.manage.self` | 在线创建或上传自己的私有 Skill；Account 范围名称唯一 |
| GET | `/api/platform/v1/me/skills/{id}` | `skill.user_private.read.self` | 自己的私有 Skill 详情 |
| PUT | `/api/platform/v1/me/skills/{id}` | `skill.user_private.manage.self` | 整体修改自己的私有 Skill；名称不可变 |
| DELETE | `/api/platform/v1/me/skills/{id}` | `skill.user_private.manage.self` | 软删除自己的私有 Skill |
| POST | `/api/platform/v1/me/skills/{id}/restore` | `skill.user_private.manage.self` | 恢复自己的私有 Skill；同名已占用时失败 |
| GET | `/api/platform/v1/me/skill-configs/{skill_id}` | `privacy_config.read.self` + Skill read/use | 读取自己为私有或共享 Skill 保存的配置状态和脱敏值，不返回可恢复 Secret |
| PUT | `/api/platform/v1/me/skill-configs/{skill_id}` | `privacy_config.write.self` + Skill read/use | 写入新版本的个人 Skill 私密配置 |
| POST | `/api/platform/v1/me/skill-configs/{skill_id}/versions/{version}/activate` | `privacy_config.write.self` + Skill read/use | 激活自己的历史配置版本 |
| GET | `/api/platform/v1/account/skills` | `skill.account_shared.read.account` | 当前 Account 共享 Skill |
| GET | `/api/platform/v1/account/skills/{id}` | `skill.account_shared.read.account` | 当前 Account 共享 Skill 详情 |
| POST | `/api/platform/v1/account/skills` | `skill.account_shared.manage.account` | Account Admin 新建共享 Skill |
| PUT | `/api/platform/v1/account/skills/{id}` | `skill.account_shared.manage.account` | Account Admin 整体修改共享 Skill；名称不可变 |
| DELETE | `/api/platform/v1/account/skills/{id}` | `skill.account_shared.manage.account` | Account Admin 软删除共享 Skill |
| POST | `/api/platform/v1/account/skills/{id}/restore` | `skill.account_shared.manage.account` | Account Admin 恢复共享 Skill；同名已占用时失败 |
| GET | `/api/platform/v1/sessions` | `session.read.self` | current user sessions |
| POST | `/api/platform/v1/sessions` | `session.write.self` | 创建 Session：请求体含客户端标识与幂等键（`client_name/idempotency_key`），不接收身份、URI、Memory Policy；返回产品 Session ID（幂等重试返回同一 Session） |
| GET | `/api/platform/v1/sessions/{id}` | `session.read.self` | Session、消息和状态的产品 DTO |
| GET | `/api/platform/v1/sessions/{id}/messages` | `session.read.self` | 组装 Archive 与当前 Context 后的完整消息历史，不返回 URI |
| POST | `/api/platform/v1/sessions/{id}/messages` | `session.write.self` | 插件/MCP/SDK/API 幂等追加消息；产品网页不调用 |
| POST | `/api/platform/v1/sessions/{id}/commit` | `session.write.self` | 集成客户端归档并触发 Memory 提取；产品网页不显示按钮 |
| GET | `/api/platform/v1/sessions/{id}/memory-impact` | `session.read.self` | 脱敏 Commit/Memory Diff，只读 |
| DELETE | `/api/platform/v1/sessions/{id}` | `session.delete.self` | 软删除；30 天后 delete session |
| GET | `/api/platform/v1/activity` | `task.read.self`；共享项另需 `task.read.account_shared` | 当前用户私有对象任务和当前 Account 共享对象任务 |
| POST | `/api/platform/v1/activity/{id}/cancel` | `task.cancel.self` 或 `task.cancel.account_shared` | 仅可取消状态；同时校验目标对象写权限 |
| GET | `/api/platform/v1/recycle-bin` | 对应资源的 self read | 当前用户回收站 |
| POST | `/api/platform/v1/recycle-bin/{id}/restore` | 按对象类型校验（详见 §12.6 注） | 30 天内恢复 |

产品 API 返回产品 DTO，不原样泄露内部绝对文件路径、系统目录和控制字段。普通用户请求永远不能切换 Account；`auth/me` 的 Account 是固定归属，不提供 Account 切换列表。

不提供含义不清的通用写接口 `/api/platform/v1/resources` 或 `/api/platform/v1/skills`。`/me/*` 明确表示 User 私有目标，`/account/*` 明确表示 Account 共享目标；后端仍根据对象引用和 canonical URI 二次校验，不能只相信路径名称。

Skill 不提供独立 `/execute` API，也不提供网页“在新 Session 中使用”。Codex、其他 Agent、插件或 MCP 在运行时检索/读取 Skill，并按当前 User 凭证实时鉴权。

Memory 不提供独立 `/memories` 产品 API。Session Commit 继续复用 OpenViking 现有的同步归档与异步 Memory 提取流程；产品只通过 Search 返回有权查看的 Memory 结果，并在 Session 详情返回脱敏的 Memory Impact。底层 Memory 文件写接口不进入 Product API。Commit 是集成客户端能力但不显示为网页按钮；Extract/Used/Tool Result/Context/Archive 也不作为浏览器 Product API，完整契约见 [Memory、Search 与 Session 产品契约](11-memory-search-session-product-contract.md)。

产品 Search API 不暴露 `target_uri`、任意 `filter`、`level` 或 `include_provenance` 等调试参数，也不暴露 `grep/glob`。`recall` 保留给 Agent、插件、MCP 等受控调用链，不作为 `/app/search` 页面动作。

两个 Search Product API 只接受稳定产品字段：

- `query`：必填检索文本。
- `context_type`：可空；只允许 `memory/resource/skill`，为空表示全部。
- `tags`：可空；作为“更多筛选”，每项必须是规范化 `key=value`，多个标签按 AND 关系缩小结果范围。
- `since/until`：可空；作为“更多筛选”的更新时间范围，后端固定映射为源码 `time_field=updated_at`。
- `session_id`：只允许 `/search/search` 接受，且必须属于当前 User；`/search/find` 不接受。

`target_uri`、原始 `filter`、`score_threshold`、`level`、`include_provenance`、`limit/node_limit` 和 `time_field` 均不属于 Product API。后端固定使用 `updated_at`，并根据部署配置控制结果数量、分数阈值、索引层级和来源信息；客户端不能通过参数改变安全范围或索引执行策略。

所有 Resource/Skill 创建、上传和编辑 API 对 `tags` 使用同一校验：最多 20 项、每项最多 40 字符、严格 `key=value`、key/value 均非空、整体小写并去重。Product Facade 在提交 PostgreSQL 后通过 Outbox 同步 OpenViking `search_tags`；不得把任意自由文本标签静默转换成另一套内部格式。

Search Product Facade 复用源码分组结果，但必须转换为产品 DTO：

- 保留 `context_type`、可显示名称、`abstract/overview` 摘要和 `match_reason`。
- Resource/Skill 的内部 URI 映射为 `platform_content_refs.id` 和产品详情链接；Memory 只返回当前结果抽屉所需的类型、显示名称、摘要和匹配原因。
- `visibility` 由服务端根据已授权 canonical URI 分类为 `user_private/account_shared`，不能由客户端提交。
- 删除原始 `uri`、`score`、`level`、`query_plan`、`provenance`、`relations` 和底层 category；浏览器不能据产品结果还原 Account/User 存储路径。
- Memory 结果不提供独立详情读取、文件下载或修改动作；抽屉直接使用 Search 响应，不再调用底层 `content/read`。

### 12.6 管理 API

| 方法 | 路径 | Permission |
| --- | --- | --- |
| GET | `/api/platform/v1/admin/users` | `user.read` |
| POST | `/api/platform/v1/admin/users` | `user.create`；只创建 `user` 角色 |
| PATCH | `/api/platform/v1/admin/users/{id}` | `user.update` |
| POST | `/api/platform/v1/admin/users/{id}/disable` | `user.disable` |
| POST | `/api/platform/v1/admin/users/{id}/password/reset` | `user.password.reset.account`；目标必须是普通 User |
| DELETE | `/api/platform/v1/admin/users/{id}` | `user.delete` |
| GET | `/api/platform/v1/admin/users/{id}/deletion-preview` | `user.delete` |
| GET | `/api/platform/v1/admin/roles` | `role.read`；只读三个内置角色 |
| GET | `/api/platform/v1/admin/audit-events` | `audit.read` |
| GET | `/api/platform/v1/admin/activity` | `task.read.account_shared`；仅当前 Account 共享对象任务 |
| POST | `/api/platform/v1/admin/activity/{id}/cancel` | `task.cancel.account_shared` + 目标对象写权限 |
| GET | `/api/platform/v1/admin/monitoring` | `monitoring.read`；仅当前 Account 业务摘要 |
| POST | `/api/platform/v1/admin/users/{id}/search/find` | `memory.read.account` 等目标对象读取权限；只读成员范围检索 |
| GET | `/api/platform/v1/admin/users/{id}/sessions` | `session.read.account` |
| GET | `/api/platform/v1/admin/users/{id}/sessions/{session_id}` | `session.read.account`；只读历史与 Memory Impact |
| GET | `/api/platform/v1/admin/users/{id}/resources` | `resource.user_private.read.account` |
| GET | `/api/platform/v1/admin/users/{id}/resources/{resource_id}` | `resource.user_private.read.account`；只读预览，不提供下载/导出 |
| GET | `/api/platform/v1/admin/users/{id}/skills` | `skill.user_private.read.account` |
| GET | `/api/platform/v1/admin/users/{id}/skills/{skill_id}` | `skill.user_private.read.account` |
| POST | `/api/platform/v1/admin/users/{id}/skills/{skill_id}/publish` | `skill.user_private.publish.account`；原地转为共享，不需要所属 User 审批 |
| GET | `/api/platform/v1/admin/users/{id}/api-keys` | `credential.read.account` |
| DELETE | `/api/platform/v1/admin/users/{id}/api-keys/{credential_id}` | `credential.revoke.account` |
| GET | `/api/platform/v1/admin/recycle-bin` | 按对象类型校验（见下注） |
| POST | `/api/platform/v1/admin/recycle-bin/{id}/restore` | 按对象类型校验（见下注） |

Account Admin API 的 Account 固定来自当前登录 Session；路径中的用户只作为 Subject，且必须属于该 Account。读取其他用户数据不授予修改、导出或删除能力。管理员只能查看 API Key 的名称、掩码、状态和使用时间并执行撤销，不能获取明文，也不能代用户创建 Key。

`POST /admin/users` 由服务端生成长期有效的初始密码，响应中只返回一次 `initial_password`，前端提供复制按钮，不发送邀请。`POST /password/reset` 采用相同返回方式，且成功事务必须同时撤销目标用户全部登录 Session。两个接口都不能在后续查询中重新返回密码。

Platform Super Admin 使用独立的平台级接口：

| 方法 | 路径 | Permission |
| --- | --- | --- |
| GET/POST | `/api/platform/v1/platform/accounts` | `account.read.platform/account.manage.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users` | `user.read.platform` |
| POST | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/search/find` | `memory.read.platform` 等目标对象读取权限；只读成员范围检索 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/sessions` | `session.read.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/sessions/{session_id}` | `session.read.platform`；只读历史与 Memory Impact |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/resources` | `resource.user_private.read.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/resources/{resource_id}` | `resource.user_private.read.platform`；默认只读预览 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/resources` | `resource.account_shared.read.platform` |
| POST | `/api/platform/v1/platform/accounts/{account_id}/resource-uploads` | `resource.account_shared.write.platform` |
| POST | `/api/platform/v1/platform/accounts/{account_id}/resources/imports` | `resource.account_shared.write.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/resources/{id}` | `resource.account_shared.read.platform` |
| PATCH | `/api/platform/v1/platform/accounts/{account_id}/resources/{id}` | `resource.account_shared.write.platform` |
| POST | `/api/platform/v1/platform/accounts/{account_id}/resources/{id}/refresh` | `resource.account_shared.write.platform` |
| DELETE | `/api/platform/v1/platform/accounts/{account_id}/resources/{id}` | `resource.account_shared.delete.platform` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/skills` | `skill.account_shared.read.platform`；只读 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/skills/{id}` | `skill.account_shared.read.platform`；只读 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/skills` | `skill.user_private.read.platform`；只读 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/skills/{id}` | `skill.user_private.read.platform`；只读 |
| POST | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/password/reset` | `user.password.reset.platform`；禁止目标为 Platform Super Admin |
| PUT | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/role` | `role.assign.platform`；仅 `user -> account_admin` |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/api-keys` | `credential.read.platform` |
| DELETE | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/api-keys/{credential_id}` | `credential.revoke.platform` |
| DELETE | `/api/platform/v1/platform/accounts/{account_id}` | `account.delete` |
| POST | `/api/platform/v1/platform/accounts/{account_id}/provisioning/retry` | `account.manage.platform` | 重试失败的 Account/首位 Account Admin Provisioning；幂等，仅 `provisioning/failed` 状态可重试 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/deletion-preview` | `account.delete` |
| GET | `/api/platform/v1/platform/audit-events` | 平台审计读取权限 |
| GET | `/api/platform/v1/platform/activity` | `task.read.platform`；可按目标 Account 过滤 |
| POST | `/api/platform/v1/platform/activity/{id}/cancel` | `task.cancel.platform` + 目标对象写权限；内部任务仍禁止取消 |
| GET | `/api/platform/v1/platform/monitoring` | `monitoring.read`；平台聚合业务摘要 |
| GET | `/api/platform/v1/platform/recycle-bin` | 按对象类型校验（见下注） |
| POST | `/api/platform/v1/platform/recycle-bin/{id}/restore` | 按对象类型校验（见下注） |

注（回收站恢复权限）：恢复是删除/管理类动作的逆操作，恢复接口必须按对象类型分别校验权限，不存在单一的「Account/平台范围恢复权限」放行所有类型。权限映射如下：

| 对象类型 | 谁可以恢复 | 权限 code |
| --- | --- | --- |
| 自己的私有 Resource | 属主本人 | `resource.user_private.delete.self` |
| 自己的私有 Skill | 属主本人 | `skill.user_private.manage.self` |
| Account 共享 Resource | Account Admin（本 Account）；Platform Super Admin（目标 Account） | `resource.account_shared.delete.account` / `resource.account_shared.delete.platform` |
| Account 共享 Skill | 仅 Account Admin（本 Account）；发布后已归 Account | `skill.account_shared.manage.account` |
| 其他 User 的私有 Skill | 不允许恢复 | — |
| 自己的 Session | 仅属主本人；管理员不能恢复他人 Session | `session.delete.self` |
| User | Account Admin（本 Account） | `user.delete` |
| Account | 仅 Platform Super Admin | `account.delete` |

Platform Super Admin 对 Skill 始终只读，任何 Skill 的恢复均不允许。恢复动作必须写审计；User/Account 恢复接口同时保留 Actor 与 Subject（详见各资源契约与 06 §14.6）。

Platform Super Admin 选择目标 Account 是管理浏览行为，不是把登录用户切换成该 Account 成员。每个管理请求都保留原 Actor，并将目标 Account/User 记录为 Subject。

创建 Account 的请求必须同时包含首位 Account Admin 的邮箱和展示信息；成功响应一次性返回该 Account Admin 的初始密码。产品页面不提供创建或重置另一个 Platform Super Admin 的接口。密码重置授权必须比较内置角色等级并满足 `actor_role_rank > target_role_rank`，不能只判断是否拥有通用管理 Permission。

`deletion-preview` 返回目标名称、影响对象分类及数量、是否可恢复和 `purge_after`。DELETE 成功只进入回收期，并返回 deletion job ID 与恢复截止时间；弹窗不产生可绕过后端授权的“已确认”凭据。
