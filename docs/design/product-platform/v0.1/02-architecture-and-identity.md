# 02 目标架构与身份上下文

> Design v0.1 · [返回版本索引](README.md)

## 6. 目标架构

```text
Browser
  |
  | HTTPS
  v
Caddy / Nginx
  |-- /login, /app, /admin,
  |   /platform ------------------> web-platform static bundle
  |-- /studio --------------------> existing web-studio bundle
  |-- /api/platform/v1 ----------> Platform Routers
  |-- /api/v1, /mcp -------------> existing OpenViking Routers
  |
  v
OpenViking Product Server (one FastAPI process in Phase 1)
  |-- IAM Module
  |-- Login Session Authentication
  |-- RBAC Authorization
  |-- Platform/BFF API
  |-- Provisioning Bridge
  |-- Existing OpenVikingService
  |
  |-- PostgreSQL: identity, role, session, audit, outbox
  |-- VikingFS / VectorDB: memory, resource, session, index
```

### 6.1 逻辑组件

| 组件 | 职责 | 不能承担的职责 |
| --- | --- | --- |
| `web-platform` | 登录、产品 UI、租户管理 UI | 不保存 Root/User API Key |
| Platform Router | 面向页面的稳定业务 API | 不直接信任客户端身份字段 |
| AuthService | 登录、密码校验、会话签发与撤销 | 不决定业务资源权限 |
| ApiCredentialService | 签发、校验和撤销用户 API Key | 不保存角色，不创建机器身份 |
| AuthorizationService | 计算 Permission 并授权 | 不读取或修改 OpenViking 数据 |
| IdentityRepository | IAM 数据持久化 | 不保存记忆、资源、OpenViking 对话 Session 正文 |
| ProductFacadeService | 将业务操作映射到 OpenViking Service | 不绕过 Permission 检查 |
| ProvisioningService | 同步 Account/User 到 OpenViking 控制面 | 不负责用户登录 |
| OpenVikingService | 存储、检索、对话 Session、语义处理 | 不保存密码和网页登录会话 |

### 6.2 为什么第一阶段不拆微服务

同一 FastAPI 进程内可以：

- 共用 OpenViking `RequestContext` 和 `OpenVikingService`。
- 避免 Platform API 到 OpenViking 的内部 HTTP 网络开销。
- 避免登录用户创建与 OpenViking User 初始化之间出现更多分布式故障点。
- 保持一个镜像、一个版本和一套回滚流程。

模块边界仍需按可拆分方式设计：IAM 不直接导入 VikingFS 私有实现，Platform API 只通过服务接口调用 OpenViking。

## 7. 身份上下文设计

### 7.1 AuthenticatedUserPrincipal

网页登录 Session、用户 API Key 和用户 OAuth Token 最终统一生成同一种用户主体：

```python
@dataclass(frozen=True)
class AuthenticatedUserPrincipal:
    actor_user_id: str
    actor_account_id: str | None
    user_status: str
    authentication_method: Literal["session", "api_key", "oauth"]
    session_id: str | None
    credential_id: str | None
    role_codes: tuple[str, ...]
    permissions: frozenset[str]
    ov_base_role: Role | None
```

字段说明：

- `actor_user_id` 标识实际操作者；API Key 和 OAuth 不会把插件变成新的 Actor。
- Platform Super Admin 可使用独立的平台登录主体，其 `actor_account_id` 可为空；上例表示 Account 用户调用路径。
- Account Admin 和 User 的 `actor_account_id` 在登录后固定，产品不提供 Account 切换。
- `authentication_method` 只说明“通过什么方式证明身份”，不影响角色和数据范围。
- `session_id` 仅登录 Session 认证时存在；`credential_id` 用于 API Key/OAuth 凭证审计，任何位置都不保存明文密钥。
- `role_codes` 用于页面展示和审计，不直接作为授权判断。
- `permissions` 按当前用户状态和角色实时计算；API Key 不保存独立角色，也不能扩大此集合。
- Account Admin/User 的 `ov_base_role` 只允许 `Role.ADMIN` 或 `Role.USER`；Platform Super Admin 为 `None`，由平台策略决定每次目标操作的最小 OpenViking 上下文。
- 产品登录永远不会生成 `Role.ROOT`。

v0.1 不定义 `ServiceAccountPrincipal`。内部 Provisioning、清理等 Worker 使用仅限进程内部的 `SystemPrincipal`，不签发可供外部插件使用的 API Key。

`SystemPrincipal` 不是 Service Account：它只能由受控 Worker 代码路径构造，没有登录入口、API Key 或 OAuth Token，也不能通过 HTTP/MCP 请求声明。它只允许执行代码中明确列出的系统动作，并在审计中记录组件名、任务 ID、Subject 和结果。

### 7.2 Actor、Subject 与数据访问上下文

产品请求不能只携带一个“当前用户”。管理员访问他人数据时需要额外构造：

```python
@dataclass(frozen=True)
class DataAccessContext:
    actor_user_id: str
    actor_account_id: str | None
    subject_account_id: str
    subject_user_id: str
    action: str
    request_id: str
```

授权规则：

- User：`actor_account_id == subject_account_id` 且 `actor_user_id == subject_user_id`。
- Account Admin：`actor_account_id == subject_account_id`，允许读取该 Account 下任意 Subject。
- Platform Super Admin：允许选择任意 Subject Account/User。
- 对他人数据的写入、导出和删除不从“可读取”自动推导，必须检查独立 Permission。

### 7.3 转换到 OpenViking RequestContext

```python
def to_ov_context(
    principal: AuthenticatedUserPrincipal,
    access: DataAccessContext,
) -> RequestContext:
    authorize_data_access(principal, access)
    return RequestContext(
        user=UserIdentifier(access.subject_account_id, access.subject_user_id),
        role=Role.USER,
        actor_peer_id=None,
    )
```

对数据读取使用目标 Subject 的最小权限 OpenViking 上下文；Account/User 控制面操作仍走受控的管理服务。Platform Audit Event 始终记录原始 Actor、Subject 和 Request ID，因此不会把管理员误记成目标用户。

若产品操作明确针对某个 Peer，可由服务端校验后设置 `actor_peer_id`。客户端不能通过任意 Header 覆盖它。

### 7.4 信任边界

| 来源 | 是否可信 | 处理方式 |
| --- | --- | --- |
| 登录 Session Cookie | 需服务端查库验证 | 验证 token hash、状态、到期时间和用户状态 |
| URL 中的 account/user | 不作为 Actor 身份 | 只作为 Subject，校验角色、Permission 和数据范围后才能使用 |
| `X-OpenViking-*` Header | 产品公网入口不可信 | 网关删除；仅内部 trusted 链路允许 |
| Root API Key | 高敏内部密钥 | Secret Manager/环境变量注入，不进入浏览器和日志 |
| User API Key | 用户委托型集成凭据 | 解析到固定 Account/User，继承实时 RBAC；不用于产品网页登录 |
| OAuth Token | 用户授权给 MCP 客户端的委托凭据 | 解析到授权 User，权限不超过该用户当前权限 |
| 插件/MCP 传入的 Account/User | 不可信 | 不能覆盖凭证解析出的 Actor，只能在已授权管理 API 中作为 Subject |
