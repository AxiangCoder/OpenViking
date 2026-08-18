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
        search/
        memories/
        resources/
        skills/
        sessions/
        activity/
        recycle-bin/
        profile/
          api-keys/
          connections/
      admin/
        users/
        shared-resources/
        shared-skills/
        roles/
        audit/
        settings/
      platform/
        accounts/
        users/
        audit/
      oauth/
        consent/
        verify/
    components/
    features/
      auth/
      resources/
      skills/
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

/app
/app/search
/app/resources
/app/resources/private
/app/resources/private/$resourceId
/app/resources/shared
/app/resources/shared/$resourceId
/app/skills
/app/skills/private
/app/skills/private/new
/app/skills/private/$skillId
/app/skills/shared
/app/skills/shared/$skillId
/app/sessions
/app/activity
/app/recycle-bin
/app/profile
/app/profile/api-keys
/app/profile/connections

/admin/users
/admin/users/$userId/data
/admin/users/$userId/resources/$resourceId
/admin/users/$userId/api-keys
/admin/shared-resources
/admin/shared-resources/$resourceId
/admin/shared-skills
/admin/shared-skills/new
/admin/shared-skills/$skillId
/admin/users/$userId/skills/$skillId
/admin/roles
/admin/audit
/admin/activity
/admin/monitoring
/admin/recycle-bin
/admin/settings

/platform/accounts
/platform/accounts/$accountId/users
/platform/accounts/$accountId/resources
/platform/accounts/$accountId/resources/$resourceId
/platform/accounts/$accountId/skills
/platform/accounts/$accountId/skills/$skillId
/platform/accounts/$accountId/users/$userId/skills/$skillId
/platform/accounts/$accountId/users/$userId/data
/platform/accounts/$accountId/users/$userId/resources/$resourceId
/platform/accounts/$accountId/users/$userId/api-keys
/platform/audit
/platform/activity
/platform/monitoring
/platform/recycle-bin

/oauth/consent
/oauth/verify
```

`/platform` 仅 Platform Super Admin 可进入。这里选择目标 Account 是查看管理对象，不会改变登录者的 Actor 身份，也不是普通用户意义上的“切换 Account”。Account Admin 和 User 的 Account 始终固定。

`/oauth/consent` 和 `/oauth/verify` 是 MCP OAuth 的短流程页面，不属于产品登录方式，也不进入侧边栏。页面使用现有登录 Session 确认当前 User；未登录时先跳 `/login`，登录后只允许回到同源的这两个授权路由。

### 13.3 Resource/Skill 信息架构

产品页面使用“我的”和“Account 共享”两个清晰分区，不使用容易被误解为互联网公开的“公共”标签：

| 页面 | 内容 | User 操作 | Account Admin 操作 |
| --- | --- | --- | --- |
| 我的 Resource | 当前用户 `viking://user/{ov_user_id}/resources/**` | 查看、新增、编辑元数据、替换/Refresh、Watch、删除 | 管理自己的 |
| Account 共享 Resource | 当前 Account `viking://resources/**` | 查看、检索、引用 | 查看、新增、编辑元数据、替换/Refresh、Watch、删除 |
| 我的 Skill | 当前用户 `viking://user/{ov_user_id}/skills/**` | 查看、使用、管理 | 管理自己的 |
| Account 共享 Skill | 当前 Account `viking://agent/skills/**` | 查看、使用 | 查看、使用、管理 |

普通 User 在共享页不显示“新建、上传、编辑、移动、标签、删除、恢复”入口，并显示“共享内容由 Account 管理员维护”。Account Admin 可在 `/admin/shared-resources`、`/admin/shared-skills` 集中管理，也可在 `/app` 对应共享页看到相同管理能力。

“添加 Resource”默认进入“我的 Resource”，界面不得把默认目标设为 Account 共享。管理员想发布到共享区时必须从共享页发起明确动作并看到目标 Account；从私有区发布为共享对象采用复制/发布语义，原私有对象保留，新对象获得独立产品 ID 和审计记录。

Resource 新增页不显示 Viking URI、`visibility`、父目录、`create_parent` 或 `processing_mode`。页面入口已经决定目标：私有页只能创建私有 Resource，共享管理页只能创建当前 Account 共享 Resource。解析后的目录树、正文、Abstract 和 Overview 在 v0.1 中只读；更新内容通过替换上传文件或 Refresh 稳定远程来源完成。

Account Admin 只能把自己的私有 Resource 发布为共享副本，不能利用“可读取成员数据”把其他 User 的私有 Resource 复制到共享区。完整页面、字段、状态和 Watch 交互以 [Resource 页面与产品契约](09-resource-product-contract.md) 为准。

Skill 使用不同的发布语义：同一 Account 内所有未删除 Skill 名称全局唯一，只有 Account Admin 可以把本 Account 任意 User 的私有 Skill 原地转换为共享 Skill，Skill ID 和名称不变，私有区不保留副本且不能取消发布。普通 User 不能发布；Platform Super Admin 的 Skill 页面全部只读。在线 Skill 可编辑名称以外的字段，ZIP Skill 更新时整体重新上传；详情页只提供“在新 Session 中使用”，不提供独立运行器。完整规则以 [Skill 页面与产品契约](10-skill-product-contract.md) 为准。

共享列表可展示创建者和更新时间帮助追溯，但不能显示“只有创建者可编辑”的暗示；v0.1 中共享对象归 Account，由有权限的管理员统一管理。

### 13.4 前端权限策略

- 应用启动调用 `/auth/me`。
- 未登录访问 `/app`、`/admin` 或 `/platform` 跳转 `/login`。
- 路由 Guard 依据 Permission 隐藏或阻止页面。
- 按钮依据 Permission 隐藏或禁用。
- 后端仍必须重复鉴权；前端权限只负责体验，不是安全边界。
- 权限变化或收到 401/403 时刷新 `/auth/me` 并更新界面。

“路由 Guard”就是进入页面前的门卫；它能避免用户看到不该看的入口，但真正的锁必须在后端。

### 13.5 前端本地存储规则

允许保存：

- 主题、语言、表格列、最近打开页面等非敏感偏好。

禁止保存：

- Root API Key。
- User API Key。
- 登录 Session Token。
- 密码。
- 权限快照作为安全依据。

`/app/profile/api-keys` 允许用户显式创建个人 API Key。创建成功后通过专用一次性结果页展示完整 Key，并明确提示立即复制；离开页面后不能再次查看。前端只在当前内存状态中短暂持有明文，不写入任何 Web Storage、URL、错误上报、埋点或剪贴板历史管理逻辑。

### 13.6 Studio 处理

- 保留现有 `web-studio` bundle，便于本地开发、底层排障和上游能力对照，但正式产品不依赖它。
- 生产公网反向代理默认不注册 `/studio` 路由；外部请求应得到 404，而不是进入产品登录或权限页面。
- 需要排障时，只能在开发环境或独立私网运维入口显式启用 `/studio`；具体使用 VPN、Tailscale 或固定 IP 属于部署选择，不再是产品设计未决项。
- `/studio` 不出现在 `/app`、`/admin`、`/platform` 导航中，也不分配给 User、Account Admin 或 Platform Super Admin 作为产品权限。
- 用户、共享内容、凭据、审计、监控等正式能力必须按角色进入对应产品页面；不能保留“只有 Studio 能完成”的正式业务流程。
- 原始 URI 操作、底层任务调试和实验性设置可继续只存在于 Studio，它们是运维能力，不计入产品页面功能覆盖。
- Studio 被启用时继续使用 API Key 连接模型；用户凭证必须由新 IAM 签发，Root API Key 只允许受控运维人员在隔离环境使用。
- 当前 Studio 中的 `/studio/oauth/consent` 和 `/studio/oauth/verify` 不再作为产品 OAuth 入口；对应页面迁入 `web-platform` 的 `/oauth/consent`、`/oauth/verify`，并以产品登录 Session 授权，避免 MCP OAuth 依赖不对公网开放的 Studio。
- Root 管理密钥不预置进公开静态资源。

### 13.7 首页、检索、活动与 Resource Watch

- `/app` 首页只展示当前 User 的内容数量、最近 Session、最近 Resource/Skill 和处理失败摘要，不返回 Queue、锁、模型、VectorDB 等底层状态。
- `/app/search` 提供“快速检索”和“结合会话检索”两个模式，分别调用源码 `find` 与 `search`。快速检索是默认模式；结合会话检索要求用户选择自己有权读取的 Session。两种模式的默认范围均为“我的私有数据 + 当前 Account 共享数据”，可缩小到 Memory、Resource 或 Skill，但前端不能输入任意 Viking URI 或扩大根目录。
- Search 主表单只显示检索词、模式和内容类型（全部、Memory、Resource、Skill）；选择“结合会话检索”后显示当前 User 自己的 Session 选择器。“更多筛选”只包含结构化标签和“更新时间范围”。标签输入提示使用 `key=value`，例如 `project=openviking`；多个标签表示必须全部匹配。页面不提供创建时间/更新时间切换。
- 相似度分数阈值、索引层级、来源追踪开关、结果数量和自定义 URI 不显示在产品页面，也不能通过 URL Query 或浏览器请求透传到底层 API。
- `recall` 不显示为页面模式，只供 VikingBot/MCP 等受控调用链使用；`grep/glob` 属于文件与检索调试能力，只留私网 Studio。
- Search 结果复用 Studio 现有的“结果列表 + 右侧详情抽屉”交互。列表按引擎返回顺序展示类型、产品显示名称、我的/Account 共享归属和摘要；点击 Resource/Skill 可进入对应产品详情，点击 Memory 只打开抽屉展示 Memory 类型、摘要和匹配原因。
- Search 列表和抽屉不展示 Viking URI、相似度分数、L0/L1/L2、Query Plan、Provenance、Relations、原始 JSON，也不提供“在 Playground 打开”。Memory 抽屉不读取原始文件，不显示编辑、删除、恢复或下载动作。
- 不设置 `/app/memories` 顶级页面。Memory 由 Session Commit 自动提取和更新：用户从 `/app/search` 找到 Memory，在 `/app/sessions/{id}` 查看该 Session Commit 的 Memory Impact；不显示 Memory 新建、编辑、删除或恢复按钮。
- `/app/sessions` 复用现有 Studio Session 的完整聊天方向，但使用产品登录 Session 和统一 RBAC。VikingBot 是 v0.1 必选部署组件；OpenViking Session 保存消息、归档、上下文及 Memory 提取状态，VikingBot 负责生成 AI 回复，前端不直接持有或转发 User API Key。
- `/app/activity` 聚合当前 User 私有对象的导入、索引、Session Commit 等异步 Task，以及其有权查看的 Account 共享对象 Task。原始 Task ID、内部堆栈和 Worker 路径不展示。
- Resource 自动同步不建立独立顶级 Watch 菜单。用户在自己的 Resource 详情页设置同步周期、查看最近同步和手动触发；Account Admin 在共享 Resource 详情页执行相同操作。
- 取消 Task 或 Watch 前展示目标 Resource、任务类型、当前状态和影响；后端再次校验 Task Permission、目标 Resource 写权限和可取消状态。
- `/admin/monitoring` 只展示当前 Account 的业务健康摘要和共享对象失败任务；`/platform/monitoring` 展示平台聚合。Queue、锁、模型、VectorDB、文件系统和原始请求日志只留私网 Studio/监控系统。

### 13.8 MCP OAuth 授权页

同设备授权流程：

```text
MCP Client -> OAuth authorize -> /oauth/consent?pending=...
                               -> 未登录则 /login
                               -> 展示 Client/回调域名/Scope/数据范围
                               -> 允许或拒绝
                               -> OAuth Server 回调 MCP Client
```

跨设备流程由 MCP 客户端显示短期验证码；用户在任意已登录产品浏览器打开 `/oauth/verify`，输入验证码后看到同一份影响说明，再允许或拒绝。

页面规则：

- 不显示或要求输入 User API Key、Root API Key、密码或 Account 名称。
- 不允许客户端提供的名称替代回调域名展示；同时展示服务端登记的 Client ID 和回调 host，降低仿冒风险。
- 明确提示“该客户端将以你的身份运行，并受你当前角色权限限制”；普通 User 不会因同意 OAuth 获得共享写权限。
- 用户可以在 `/app/profile/connections` 查看 Client、授权时间、最近使用时间和 Scope，并单独撤销。
- `pending`、display code、authorization code 和 Token 不进入 URL 分析、错误上报、埋点或访问日志正文。

### 13.9 管理员直接创建用户与密码交接

- Platform Super Admin 创建 Account 时填写首位 Account Admin 的邮箱和展示信息；成功页展示系统生成的初始密码。
- Account Admin 在 `/admin/users` 直接创建本 Account 的普通 User，不提供邀请按钮、邀请状态、邀请邮件或激活页面。
- 创建和重置成功弹窗显示用户、角色、所属 Account 和初始/新密码，并提供复制按钮；密码只在该次响应和当前弹窗中存在，关闭后不能重新查看。
- 密码可长期使用，用户首次登录不强制修改；创建者负责通过系统之外的方式交给目标用户。
- Account Admin 创建的账号固定为 `user`，不能在创建时选择 `account_admin`；Account Admin 的创建或提升只能由 Platform Super Admin 完成。
- 密码重置按钮只对严格低级别目标显示，后端仍按 `actor_role_rank > target_role_rank` 强制校验；隐藏按钮不是安全边界。
- 重置确认弹窗明确提示“将使该用户所有网页登录设备退出；不会删除 OpenViking 对话和记忆，也不会撤销 API Key”。
- v0.1 不提供 Platform Super Admin 同级重置或网页紧急恢复入口。

由于创建者可以复制并长期保留密码，系统无法从密码登录事件中绝对区分目标用户本人和持有该密码的创建者。管理页面必须明确提示这一限制；审计仍记录凭证对应 User，但不能宣称具备自然人不可抵赖性。

## 14. 数据隔离与安全

### 14.1 双层授权

```text
第一层：Platform Permission
  决定“能不能执行这个产品动作”

第二层：OpenViking Namespace ACL
  决定“这个身份能不能访问这份具体数据”
```

两层之间由服务端先将目标分类为 `user_private/account_shared/internal`，再根据已授权的 DataAccessContext 构造最小权限 `RequestContext`。任何一层拒绝都终止请求，原 Actor 只进入授权与审计上下文，不会被客户端 Header 覆盖。OpenViking 现有 Namespace ACL 对同 Account 的 `viking://resources/**` 可达，不等于普通 User 有共享写权限；共享写限制必须由 Platform AuthorizationService 强制执行。

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
- `/api/v1/*` 与 `/mcp` 不是“原生接口所以不受产品权限控制”；对外开放的每个 Router/Tool 都必须接入与 Platform API 相同的 URI 分类和 Permission 检查。
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
- 删除或批量覆盖 Account 共享 Resource/Skill。

确认弹窗必须展示操作对象、目标 Account/User、预计影响数量、是否可恢复和恢复截止时间。用户只需点击“确认”或“取消”，v0.1 不要求重新输入密码、输入 Account 名称或第二人审批。

前端弹窗只用于防误触，不是安全边界。提交后后端仍需重新校验登录 Session、CSRF、Permission、Actor/Subject Scope 和目标当前状态；成功、失败与拒绝都写入审计。重复提交必须通过幂等键或资源状态检查避免重复执行。

### 14.6 软删除与回收站

- Account、User、OpenViking 对话 Session、Resource 和 Skill 默认先软删除。Memory 不提供独立删除或恢复入口，其生命周期由 Session Commit 提取流程管理。
- 恢复窗口固定为 30 天，删除后从正常列表隐藏并进入回收站。
- Account/User 进入回收期时立即禁止登录、撤销登录 Session，并停止新的业务写入。
- User 可恢复自己误删且仍在回收期内的私有数据；Account Admin 可恢复本 Account 的共享 Resource/Skill 和自己的私有数据，但不能恢复、修改或删除其他用户的私有 Skill。Platform Super Admin 可按独立平台级高风险 Permission 恢复其他类型的全平台范围对象，但对 Skill 始终只读。
- 期满后后台 Worker 执行幂等物理清理；清理失败不延长对象可访问性，但必须告警并重试。
- 审计事件独立保留，不随业务对象物理清理。

## 15. 初始部署与凭证启用

### 15.1 初版前提

- v0.1 是产品系统第一次正式建立 IAM，不存在需要保留的旧产品用户门禁。
- 不导入当前 OpenViking accounts/users/API Key registry，也不设置新旧鉴权并行期。
- PostgreSQL 是 Account、User、Role、Permission、登录 Session 和用户 API Key 的唯一身份事实来源。
- `/api/v1/*`、`/mcp`、Bearer 和 `X-Api-Key` 可以作为 v0.1 的正式集成协议继续使用，但这属于首版接口选择，不代表接受旧凭证或旧权限结果。

### 15.2 首次初始化顺序

1. 部署 PostgreSQL 并执行全新的 Platform schema migration。
2. 通过一次性部署命令创建首位 Platform Super Admin；不使用 Root API Key 代替人类管理员。
3. 由 Platform Super Admin 创建 Account 和首位 Account Admin。
4. Provisioning Worker 使用内部 `SystemPrincipal` 初始化对应 OpenViking namespace。
5. Account Admin 直接创建普通 User，复制系统生成的长期初始密码并自行交接；不发送邀请。
6. 用户登录 `/app/profile/api-keys`，按自己的插件或设备创建具名 API Key。
7. 将一次性显示的 Key 配置到 Codex、OpenClaw、OpenCode、SDK、CLI 或 MCP 客户端。
8. 验证登录 Session、API Key 和 OAuth 对同一用户产生一致的 Permission 和数据范围。

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
| `/login`, `/app/*`, `/admin/*`, `/platform/*` | `web-platform` SPA |
| `/oauth/consent`, `/oauth/verify` | `web-platform` MCP OAuth 授权 SPA 页面 |
| `/studio/*` | 公网不注册；仅可选私网入口指向现有 `web-studio` SPA |
| `/api/platform/v1/*` | Platform Router |
| `/api/v1/*` | OpenViking Router |
| `/mcp`、OAuth well-known、authorize/register/token 协议端点 | OpenViking MCP/OAuth Provider |

同源部署可以减少 CORS 和 Cookie 配置错误。

生产公网路由表中不存在 `/studio`。即使 Studio bundle 随镜像构建，也只有私网运维 listener 或开发配置可以挂载它；“代码保留”和“公网可访问”是两个独立概念。

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

数据库 URL、Cookie 签名密钥和 Root API Key 必须通过环境变量或 Secret Manager 注入。

### 16.4 Redis 决策

第一阶段不强制 Redis：

- 登录 Session 存 PostgreSQL。
- 权限缓存先使用进程内短 TTL，并以 `permission_version` 校验。
- 登录限流先使用 PostgreSQL 或单实例内存实现，但多实例前必须迁移到共享限流后端。

当进入多实例、较高登录 QPS 或需要实时全局限流时，再引入 Redis。

## 17. 审计与可观测性

### 17.1 必须审计的事件

- 登录成功、登录失败、登出、会话撤销。
- 用户 API Key 创建、撤销、到期拒绝和认证失败；成功业务请求在对应审计事件中记录认证方式与凭证 ID，不额外为每次调用生成重复 Key 事件。
- 用户创建、启用、禁用、删除。
- 密码重置、用户 API Key 撤销和 Root 凭据轮换。
- 内置角色分配、用户角色提升和权限种子变更。
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
- 登录 Session cleanup worker 状态。
- 软删除待清理数量、最早 `purge_after` 和 Purge Worker 状态。
