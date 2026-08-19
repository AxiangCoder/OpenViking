# 03 认证与授权

> Design v0.1 · [返回版本索引](README.md)

## 8. 认证设计

### 8.1 浏览器登录认证决策

第一阶段使用“不透明服务端登录 Session”，不使用浏览器长期 JWT。本文的“登录 Session”只表示网页登录状态，不是 OpenViking 保存对话内容的业务 Session：

- Cookie 名：`__Host-ov_session`
- 属性：`HttpOnly; Secure; SameSite=Lax; Path=/`
- Cookie 值：至少 256 bit 的随机 token。
- 数据库仅保存 `SHA-256(token)`，不保存明文 token。
- 默认空闲有效期：24 小时，可配置。
- 默认绝对有效期：30 天，可配置。
- 登录、用户主动修改密码后轮换当前登录 Session；角色提升不轮换当前登录 Session（提升只影响下一次请求的权限计算，不强制重登）。
- 登出、禁用用户后撤销相关登录 Session；管理员重置密码后撤销目标用户全部登录 Session。

选择不透明登录 Session 的理由：

- 可立即撤销。
- 不把角色和权限快照固化进长期 Token。
- 权限变更下一次请求立即生效。
- 与当前 OAuth 存储“明文只给客户端、服务端存 hash”的安全习惯一致。

### 8.2 CSRF 防护

所有修改请求同时满足：

1. Cookie `SameSite=Lax`。
2. 校验 `Origin` 或 `Referer` 属于允许的产品源。
3. 非 `GET/HEAD/OPTIONS` 请求要求 `X-CSRF-Token`。
4. CSRF Token 与登录 Session 绑定，前端只能读取 CSRF Token，不能读取登录 Session Cookie。

### 8.3 用户创建、密码交接与重置

基础规则：

- 邮箱是必填且全局唯一的登录标识；登录页面只提交邮箱和密码，由服务端解析所属 Account。
- 密码使用 Argon2id 哈希；数据库保存 hash、算法版本和最近修改时间，不保存明文或可逆密文。
- 登录错误使用统一响应，避免枚举账号；按 IP 和登录标识限流，连续失败进入递增冷却。
- v0.1 不提供开放注册、邀请码、邀请链接、邀请邮件、邮箱激活或自助找回密码。

管理员直接创建流程：

1. Platform Super Admin 创建 Account 时同时创建首位 Account Admin。
2. Account Admin 只能在自己的 Account 内直接创建普通 User；创建结果默认角色为 `user`。
3. Account Admin 不能创建、提升或重置另一个 Account Admin；Account Admin 的创建、提升和密码重置只能由 Platform Super Admin 执行。
4. 系统生成随机初始登录密码，创建成功页只展示一次并提供复制按钮；服务端只保存 Argon2id hash。
5. 创建者通过系统之外的方式自行把密码交给目标用户，系统不负责邀请或发送密码。
6. 初始密码没有单独到期时间，可以长期使用；首次登录不强制修改。用户修改自己的密码时必须提交当前密码（`old_password`），Argon2id 校验失败返回统一 `LOGIN_FAILED` 并计入登录限流；成功后轮换当前登录 Session。

管理员密码重置采用严格的角色层级：

```text
允许：actor_role_rank > target_role_rank
拒绝：actor_role_rank <= target_role_rank
```

`actor_role_rank/target_role_rank` 一律使用平台 `iam_roles.rank`（`platform_super_admin=3`、`account_admin=2`、`user=1`）。禁止使用或混用 OpenViking `Role` 的内置 rank（USER=0/ADMIN=1/ROOT=2，`openviking/server/identity.py`）——两者都是"越大越高级"且同名，但数值与角色集不同，混用会静默产生错误的等级比较结果。

- Platform Super Admin 可以重置 Account Admin 和 User，不能重置另一个 Platform Super Admin。
- Account Admin 只能重置本 Account 的普通 User，不能重置另一个 Account Admin。
- User 只能主动修改自己的密码，不能重置他人密码。
- 重置时系统生成新的可复制密码并只展示一次；旧密码立即失效，新密码同样可长期使用且不强制修改。
- 重置成功后撤销目标用户全部登录 Session，迫使已登录浏览器重新认证；不删除 OpenViking 对话 Session、Memory 或 Resource，也不自动撤销用户 API Key。
- Platform Super Admin 丢失密码后的网页紧急恢复或 Break-glass 机制不属于 v0.1，后续版本再设计。

已接受的 v0.1 风险：创建者可能保存并长期知道目标用户的初始密码，因此审计中的 Actor 能证明“使用了哪个用户凭证”，不能绝对证明键盘前一定是该自然人。若用户主动修改密码，这一风险从修改成功后消除。

产品网页登录固定使用本地邮箱和密码。设计中不定义 OIDC、企业微信登录、企业单点登录、外部身份绑定或自动 Provisioning；因此不需要 Identity Provider、Issuer、Subject 映射和相关登录回调接口。

当前 `openviking/server/oauth` 只服务于 MCP 客户端授权，不用于产品网页登录。

### 8.4 用户 API Key

用户 API Key 是绑定到一个 Account User 的长期 API 访问凭证，也可称为个人访问凭证。它用于 SDK、CLI、Codex/OpenClaw/OpenCode 等插件和非交互式 MCP 连接，但不创建新的程序身份。Platform Super Admin 在 v0.1 不签发平台级个人 API Key，只使用网页登录 Session。

规则：

- 一个用户可以创建多个具名 API Key，便于按设备或插件单独撤销；每个 Key 仍代表同一个用户。
- Key 使用 `ovk_u.<public_id>.<secret>` 形式：点号是分段符，`public_id` 是非敏感随机定位 ID，`secret` 至少 256 bit 并采用 base64url；客户端必须把完整字符串当作不透明值。
- Key 字符串不编码 `account_id`、`user_id`、Role 或 Permission；身份与权限只能由服务端根据 `public_id` 查库得到。
- 明文只在创建成功响应中展示一次；数据库保存 `public_id`、`SHA-256(secret)`、末尾掩码和使用元数据，不保存完整 Key。
- API Key 固定绑定 `user_id` 和该用户所属 `account_id`，请求参数或 Header 不能改变其身份。
- 每次请求根据当前用户状态和 Role 计算 Permission；Key 不保存独立角色，不复制权限快照，也不能扩大用户权限。
- 用户禁用、进入删除期或 Key 被撤销/到期后，下一次请求立即失败。
- v0.1 不提供独立 Key Scope，避免形成第二套权限系统；未来若增加限制性 Scope，最终权限只能是 `用户有效权限 ∩ Key Scope`。
- 产品网页登录不使用 API Key。用户在设置页主动创建时可以看到一次明文，但前端不得写入 `localStorage`、`sessionStorage`、日志或埋点。
- API 接受 `Authorization: Bearer <key>`；为适配 OpenViking 客户端也可接受 `X-Api-Key`，两者进入同一解析器。
- 请求同时携带登录 Session Cookie 与 API Key 时，**Cookie 优先**；Cookie 已失效**不自动回退** Bearer（防凭据混淆攻击，避免一个通道的失效扩大另一个通道的信任）。插件/MCP/SDK 客户端请求通常不带 Cookie，不受该优先级影响（技术验证确认）。

### 8.5 插件、MCP 与 OAuth 的身份语义

- 插件、MCP、SDK 和 CLI 是调用渠道，不是 Principal 类型。
- 客户端使用用户 API Key 时，属于“用户委托型集成”：服务端 Actor 始终是 Key 所属用户。
- 交互式 MCP 客户端使用 OAuth 2.1 时，Access Token 代表完成授权的用户，权限同样实时受该用户 RBAC 限制。
- OAuth Client ID 只标识客户端软件，不等于 Service Account，也不拥有业务数据权限。
- 相同用户通过登录 Session、API Key 或 OAuth 调用同一动作时，授权结果必须一致；审计额外记录认证方式和凭证 ID。
- 系统内部 Worker 使用内部 `SystemPrincipal`，不借用用户 API Key，也不对外暴露系统凭证。

MCP OAuth 是“用户把自己的 OpenViking 权限授权给一个 MCP 客户端”，不是产品登录，也不是企业单点登录。当前 v0.4.12 源码把同意页和跨设备验证码页放在 Studio 的 `/oauth/consent`、`/oauth/verify` 路由（web-studio 是独立 SPA，挂载 `/studio` 后 URL 为 `/studio/oauth/consent`、`/studio/oauth/verify`），并依赖 Studio 中已配置的 API Key；这一实现不能进入产品 v0.1，因为生产公网不挂载 Studio，浏览器也不应保存 User API Key。

产品化后的 OAuth 授权规则：

- 同意页和跨设备验证页迁入 `web-platform`，使用独立路由 `/oauth/consent`、`/oauth/verify`，但不进入日常产品导航。
- 未登录用户先跳转 `/login`，登录成功后只允许返回同源、白名单授权路由；授权身份来自服务端登录 Session。
- 同意页展示客户端名称、回调域名、请求的 MCP Scope、当前 Account、可访问的数据范围和主要操作影响，并提供“允许/拒绝”；不要求输入密码、Account 名称或 User API Key。
- 授权提交必须使用登录 Session + CSRF；OAuth Access Token 不能再次批准新的 OAuth 客户端，避免 Token 链式扩权。
- Access/Refresh Token 只保存 hash，绑定授权用户、客户端和 Grant；用户禁用、删除、角色变化、Grant 撤销或 Token 到期后立即重新校验并拒绝。
- 用户在 `/app/profile/connections` 查看并撤销自己已授权的客户端；撤销 Grant 同时撤销其 Token family，不影响该用户的其他 API Key 或其他客户端授权。
- v0.1 的 MCP Scope 可以保持协议层单一 `mcp`，但 Scope 只表示“允许调用 MCP”；最终有效权限仍是 `用户当前 RBAC ∩ MCP Tool 对应动作`，不能用 Scope 扩大角色权限。

### 8.6 Service Account 决策

v0.1 不提供 Service Account、Service Account Key、机器角色或相关管理页面。当前产品场景是每个用户为自己的插件/MCP 配置个人 API Key，没有已确认的 Account 级共享机器主体需求。

只有出现以下明确业务需求时，才在后续版本单独设计 Service Account：

- 一个集成服务由整个 Account 共用，不应归属于某个员工。
- 程序需要在创建者离职、禁用或退出登录后继续运行。
- 外部 CI、定时同步或公共 Gateway 需要独立授权、停用和审计身份。

届时 Service Account 必须是独立 Principal，而不是给某个用户 API Key 改名；其角色、数据范围、密钥生命周期和审计字段需要单独设计。

## 9. RBAC 权限设计

### 9.1 权限命名规则

一般数据类 Permission Code 采用 `<domain>.<action>.<scope>`；Resource/Skill 同时存在两种可见性，采用 `<domain>.<visibility>.<action>.<scope>` 避免把“可读取团队共享内容”误解成“可读取团队内所有用户私有内容”。管理类 Permission 可采用 `<domain>.<action>`：

```text
account.read
account.update
account.delete
account.read.platform
account.manage.platform

user.read
user.create
user.update
user.disable
user.delete
user.read.account
user.read.platform
user.password.reset.account
user.password.reset.platform

credential.read.self
credential.create.self
credential.revoke.self
credential.read.account
credential.revoke.account
credential.read.platform
credential.revoke.platform

role.read
role.assign.platform

memory.read.self
memory.read.account
memory.read.platform

resource.user_private.read.self
resource.user_private.write.self
resource.user_private.delete.self
resource.user_private.read.account
resource.user_private.write.account
resource.user_private.delete.account
resource.user_private.read.platform
resource.user_private.write.platform
resource.user_private.delete.platform
resource.account_shared.read.account
resource.account_shared.write.account
resource.account_shared.delete.account
resource.account_shared.read.platform
resource.account_shared.write.platform
resource.account_shared.delete.platform

session.read.self
session.write.self
session.delete.self
session.commit.self
session.read.account
session.read.platform

skill.user_private.read.self
skill.user_private.use.self
skill.user_private.manage.self
skill.user_private.read.account
skill.user_private.publish.account
skill.user_private.read.platform
skill.account_shared.read.account
skill.account_shared.use.account
skill.account_shared.manage.account
skill.account_shared.read.platform

audit.read
monitoring.read
privacy_config.read.self
privacy_config.write.self

integration.oauth.authorize.self
integration.oauth.read.self
integration.oauth.revoke.self

task.read.self
task.cancel.self
task.read.account_shared
task.cancel.account_shared
task.read.platform
task.cancel.platform
```

不使用 `admin=true` 之类布尔值代替 Permission。角色只是 Permission 的集合。

其中：

- `user_private` 表示对象归属于一个 User；`self/account/platform` 表示 Actor 可以触达的 Subject 数据范围。
- `account_shared` 表示对象归属于 Account；`account` 表示当前固定 Account，`platform` 表示由 Platform Super Admin 选择的目标 Account。
- “共享”不是一个无限范围：`resource.account_shared.read.account` 只能读取 Actor 所属 Account 的共享 Resource，不能读取其他 Account。
- Account 共享对象不授予创建者额外 Permission。`created_by` 只用于审计，不参与 v0.1 授权。
- Search、Relations 和 Watch 不以“换一个入口”获得独立数据权限：Search/Relations 对每个结果或关系端点重新检查读取权限；Watch 的查看继承目标 Resource 的读取权限，新增、修改、触发和取消继承目标 Resource 的写权限。
- `task.*` 只控制任务记录的查看和取消，不能替代目标对象权限。取消任务时必须同时满足 Task Permission、任务可取消状态和目标对象当前写权限。
- `privacy_config.*.self` 只允许用户管理自己的 Skill 私密配置；管理员的数据查看权限不自动包含读取他人 Secret 的权限。
- `skill.user_private.publish.account` 是 Account Admin 改变 Skill 归属的独立高风险权限，不包含编辑、删除或恢复其他 User 私有 Skill。
- Platform Super Admin 的 Skill 权限固定为 `skill.user_private.read.platform` 与 `skill.account_shared.read.platform`，不从平台最高角色推导任何 Skill 写入、发布、恢复或使用权限。

### 9.2 内置角色与数据范围

| 角色 | OpenViking Base Role | 数据范围 | 用途 |
| --- | --- | --- | --- |
| `platform_super_admin` | 不直接映射为 `root` | 全平台 | 管理并查看 Account、用户和大部分数据；Skill 只读 |
| `account_admin` | `admin` | 当前 Account | 管理当前 Account 用户和 Account 共享 Resource/Skill，并查看当前 Account 全部用户数据 |
| `user` | `user` | 自己 + 当前 Account 共享只读/使用 | 管理自己的私有数据，读取共享 Resource、读取和使用共享 Skill，不能切换 Account |

`platform_super_admin` 是可登录的人类平台角色；`root` 是 OpenViking 机器控制身份。两者权限范围可以相近，但凭据、请求上下文和审计身份不能混用。产品登录不会签发 Root API Key，也不会生成 `Role.ROOT`。

v0.1 只提供以上三个内置角色，不开放自定义角色创建、编辑或删除。普通 User 由 Account Admin 创建；Account Admin 只能由 Platform Super Admin 创建或提升；Platform Super Admin 不通过产品页面创建同级账号。

### 9.3 默认角色权限矩阵

| 权限组 | Platform Super Admin | Account Admin | User |
| --- | ---: | ---: | ---: |
| 查看 Account | 全部 | 当前 Account |  |
| 创建、软删除、恢复 Account | ✓ |  |  |
| 管理用户 | 全部 Account（仅查看、密码重置、角色提升、凭据管理） | 当前 Account |  |
| 创建普通 User | — | 当前 Account |  |
| 创建、提升 Account Admin | ✓ |  |  |
| 重置严格低级别用户密码 | Account Admin、User | 当前 Account 的 User |  |
| 重置同级密码 | 禁止 | 禁止 | 禁止 |
| 查看自己的记忆与对话 Session | ✓ | ✓ | ✓ |
| 查看其他用户的记忆与对话 Session | 全部 Account | 当前 Account |  |
| 修改其他用户 Resource | 预留独立高风险 Permission；v0.1 平台 API 不提供端点 | 默认无 |  |
| 导出、删除其他用户 Resource | 预留独立高风险 Permission；v0.1 平台 API 不提供端点 | 默认无 |  |
| 修改、删除或恢复其他用户 Memory/Session | v0.1 禁止 | 禁止 |  |
| 管理自己的 User 私有 Resource | 不适用；可按平台范围管理目标对象 | ✓ | ✓ |
| 查看其他用户的私有 Resource | 全部 Account | 当前 Account |  |
| 修改或删除其他用户的私有 Resource | 预留独立高风险 Permission；v0.1 平台 API 不提供端点 | 默认无 |  |
| 修改、删除或恢复其他用户的私有 Skill | 禁止 | 禁止 |  |
| Account 共享 Resource 读取 | 全部 Account | 当前 Account | 当前 Account |
| Account 共享 Resource 写入、删除 | 全部 Account | 当前 Account |  |
| 将自己的私有 Resource 发布为共享副本 | 不适用 | 当前 Account |  |
| 管理自己的 User 私有 Skill | 不适用；Platform 对 Skill 只读 | ✓ | ✓ |
| 查看其他 User 私有 Skill | 全部 Account 只读 | 当前 Account 只读 |  |
| 将 User 私有 Skill 发布为 Account 共享 | 禁止 | 当前 Account 任意 User | 禁止 |
| Account 共享 Skill 读取 | 全部 Account 只读 | 当前 Account | 当前 Account |
| Account 共享 Skill 使用 | 禁止 | 当前 Account | 当前 Account |
| Account 共享 Skill 管理 | 禁止 | 当前 Account |  |
| 管理自己的 API Key |  | ✓ | ✓ |
| 管理自己的 MCP 客户端授权 |  | ✓ | ✓ |
| 查看并撤销其他用户的 API Key 元数据 | 全部 Account | 当前 Account |  |
| 查看自己的处理任务 |  | ✓ | ✓ |
| 查看 Account 共享对象处理状态 | 全部 Account | 当前 Account | 当前 Account |
| 取消 Account 共享对象任务 | 全部 Account | 当前 Account |  |
| 查看审计 | 全平台 | 当前 Account |  |
| 系统监控 | ✓ | 当前 Account 视图 |  |

管理员查看他人数据属于授权的数据范围访问，不称为“冒充登录”。每次访问都必须记录 Actor、Subject、Action、Scope、Request ID 和结果；查看权限不能自动推导出写入、导出或删除权限。

Platform Super Admin 的内置角色包含 Account 共享对象的平台级修改、导出和删除 Permission 以及 Account 级管理；修改、导出或删除其他用户私有数据属于预留的独立高风险 Permission，v0.1 不提供平台 API 端点。Skill 是明确例外，只授予跨 Account 读取，不授予写入、发布、恢复或使用。Account Admin 默认不包含修改、导出或删除他人数据的 Permission，`skill.user_private.publish.account` 仅允许经确认和审计的 Skill 归属转换。

Platform Super Admin 的内置角色权限**不能通过直接继承 Account Admin 权限集合实现**：种子实现必须显式剔除全部 Skill 写/用权限（`skill.user_private.manage.self`、`skill.user_private.publish.account`、`skill.account_shared.use.account`、`skill.account_shared.manage.account`），否则平台角色会意外获得 Skill 管理能力（技术验证确认）。

普通 User 的“只读共享 Resource”包括列表、详情、检索和在其自己的会话/工作流中引用，不包括新增、覆盖正文、改名、移动、打标签、归档、恢复或删除。“使用共享 Skill”表示在被允许的执行入口调用 Skill，不等于修改 Skill 定义。Account Admin 的共享管理权只在自己固定所属的 Account 生效。

### 9.4 有效权限计算

```text
effective_permissions
  = union(all active role permissions)
  - disabled permissions
```

规则：

- Account Admin/User 与 Role 必须属于同一 Account；Platform Super Admin 使用平台级 System Role。
- 三个内置 Role 由代码和 migration 固定，不允许通过 v0.1 产品 UI/API 创建、删除或修改。
- v0.1 每个用户只绑定一个内置角色；自定义 Role 和多角色叠加属于后续版本。
- `ov_base_role=admin` 只影响 OpenViking 控制面能力映射，不自动授予任何 Platform Permission。
- **OpenViking 原始角色权限体系不改动**：源码 `Role`（root/admin/user）、namespace ACL 与现有 Router 的角色判断保持原样，仅作为执行上下文映射（`ov_base_role`）与数据隔离兜底；平台 PostgreSQL RBAC（`iam_roles/iam_permissions/iam_user_roles/iam_role_permissions`）是唯一业务授权来源，两套体系通过 `ov_base_role` 单点衔接，不引入任何其他映射。
- 登录 Session、用户 API Key 和 OAuth 使用同一份有效权限；API Key 不参与 Permission 并集计算。
- 用户禁用后，有效权限为空且所有登录会话失效。
- 权限结果可按 `(account_id, user_id, permission_version, permission_schema_version)` 短期缓存；用户自身状态或角色变更递增用户级 `permission_version`；内置角色权限种子或 migration 变更递增全局 `permission_schema_version`（参与所有用户的缓存键），两者独立，避免全局权限变更后缓存继续返回旧权限。
