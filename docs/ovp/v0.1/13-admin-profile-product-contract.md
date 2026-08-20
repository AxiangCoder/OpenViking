# 13 管理与个人设置产品契约

> Design v0.1 · 认证、个人设置、管理后台与平台管理页面级契约<br>
> 上游源码基线：OpenViking v0.4.12<br>
> 本文覆盖 `/login`、`/app/profile/*`、`/admin/*`、`/platform/*`、`/oauth/consent`、`/oauth/verify` 的页面结构、字段、状态、动作、权限、异常与验收规则。业务页面（Resource/Skill/Search/Session）契约见 09、10、11 号文档。

## 79. 设计范围与通用规则

### 79.1 页面组

| 页面组 | 入口 | 使用者 | 依据 |
| --- | --- | --- | --- |
| 认证 | `/login` | 所有角色 | 03 §8、05 §12.3 |
| 个人设置 | `/app/profile`、`/app/profile/api-keys`、`/app/profile/connections` | Account Admin/User | 03 §8.4/§8.5、05 §12.3/§12.4 |
| 管理后台 | `/admin/*` | Account Admin | 05 §12.6、06 §13.9 |
| 平台管理 | `/platform/*` | Platform Super Admin | 05 §12.6 |
| MCP OAuth 授权 | `/oauth/consent`、`/oauth/verify` | 已登录用户（瞬时页面） | 06 §13.8 |

### 79.2 通用页面规则

- 路由 Guard 依据 Permission 隐藏或阻止页面；按钮依据 Permission 隐藏或禁用；后端重复鉴权（06 §13.4）。
- 管理员查看成员数据的页面为 **Subject 数据视图**：页面固定显示「操作者（Actor）」与「数据所属（Subject）」横幅，隐藏全部修改、导出、Watch、发布、删除按钮（09 §38.2、11 §73）。
- 页面与 DTO 不返回密码、Cookie、API Key 明文或完整 hash、登录 Session Token、OAuth Code/Token、完整凭证 hash 与业务正文（06 §14.4、§17）。
- 收到 401 时刷新 `/auth/me`；会话过期则回登录页且不丢失安全状态（06 §13.4）。
- 本地存储只允许非敏感偏好；禁止保存 Key、Token、密码、权限快照、标题等（06 §13.5）。
- 列表分页统一 Cursor；时间 RFC 3339 UTC；业务错误用稳定 code（05 §12.2）。
- 无页面权限时路由 Guard 阻止；直接请求由后端返回 403；不可见对象返回 404（09 §39.4）。

### 79.3 Actor/Subject 展示规范

管理页任何涉及成员数据的行、卡片或详情必须同时呈现：

- Actor：当前登录管理员（`/auth/me` 身份），页面级横幅。
- Subject：被查看的用户或 Account，行/页头级标识。

文案统一为「以 {Actor} 身份查看 {Subject} 的数据」，审计事件同时记录两者（03 §9.3、06 §17.2）。不允许把管理员身份无痕替换为目标用户（02 §7.4）。

## 80. 登录页 `/login`

### 80.1 路由与守卫

- `/login` 对所有角色开放；已登录用户访问 `/login` 跳转其默认入口（`/app` 或 `/platform`）。
- 未登录访问 `/app`、`/admin`、`/platform`、`/oauth/consent`、`/oauth/verify` 跳转 `/login`，登录成功后回到原目标（OAuth 页面只允许返回同源授权路由，06 §13.8）。
- v0.1 不存在注册、邀请、激活、找回密码页面与入口（01 §3.2）。

### 80.2 页面结构

- 邮箱输入框（必填）。
- 密码输入框（必填）。
- 登录按钮；提交后禁用并显示进度。
- 统一错误提示区（不区分「邮箱不存在」与「密码错误」）。
- 页脚不展示企业登录按钮、OIDC 或第三方登录入口（03 §8.3）。

### 80.3 数据契约

| 动作 | API | 说明 |
| --- | --- | --- |
| 登录 | `POST /api/platform/v1/auth/login` | 提交 `{email, password}`；成功后服务端签发 `__Host-ov_session` Cookie |
| 当前用户 | `GET /api/platform/v1/auth/me` | 登录后获取 Account、User、角色、权限摘要与 CSRF Token，决定跳转目标 |

- 登录请求不提交 Account；服务端按规范化邮箱解析固定的 User/Account（05 §12.3）。
- Cookie：`HttpOnly; Secure; SameSite=Lax; Path=/`（03 §8.1）。

### 80.4 交互状态机

```text
初始 -> 提交中 -> 成功（签发 Session，跳转默认入口）
                -> 失败（统一 LOGIN_FAILED 文案；限流时显示「尝试过多，请稍后再试」）
```

### 80.5 错误与空状态

| 场景 | 页面行为 |
| --- | --- |
| 凭证错误 | 统一文案「邮箱或密码不正确」，不区分账号是否存在 |
| 登录限流 | 提示稍后重试，按钮进入冷却 |
| `ACCOUNT_SUSPENDED` | 提示「账号已暂停，请联系管理员」 |
| `USER_DISABLED` | 提示「账号已停用，请联系管理员」 |
| `PROVISIONING_PENDING/FAILED` | 提示「账号正在开通/开通失败，请联系管理员」 |
| 网络错误 | 保留输入，显示可重试错误 |

### 80.6 验收规则

1. 页面只提供邮箱和密码，无注册、邀请、激活、找回密码、企业登录入口。
2. 错误文案统一，不泄露邮箱是否注册。
3. 登录成功设置 HttpOnly Cookie，浏览器 JS 无法读取。
4. 登录后进入与角色匹配的默认入口；未登录访问受保护页跳转登录并回跳。
5. 浏览器网络与存储中不出现 User API Key 或 Root Key。

## 81. 个人设置 `/app/profile`

### 81.1 路由与守卫

- `/app/profile`：Account Admin/User（`session.read.self` 等自身权限）。
- 子路由：`/app/profile/api-keys`、`/app/profile/connections`（06 §13.2）。

### 81.2 页面结构（概览页）

- 基本信息区：显示名、邮箱、所属 Account（固定展示，无 Account 切换入口）。
- 修改密码区：当前密码、新密码、确认新密码。
- 登录设备区：当前登录 Session 摘要（最后活动时间、浏览器/设备摘要）。
- 操作区：「退出所有设备」按钮（撤销全部登录 Session）。

### 81.3 修改密码

| 动作 | API | 权限 | 规则 |
| --- | --- | --- | --- |
| 修改密码 | `POST /api/platform/v1/auth/password/change` | 登录 Session + CSRF | 请求体必填 `old_password` 与 `new_password`；旧密码校验失败返回统一 `LOGIN_FAILED` 并计入登录限流；成功后轮换当前登录 Session（03 §8.3、05 §12.3） |

- 成功后前端清除本页表单并提示重新登录（Session 已轮换）。
- 密码强度规则由后端校验（`min_length=12` 等，06 §16.3）。
- 前端不保存、不记录任何密码明文。

### 81.4 登录设备区

- v0.1 只提供「退出所有设备」（`POST /api/platform/v1/auth/logout-all`，登录 Session + CSRF）。
- 不提供单会话列表与单会话撤销接口（明确不在 v0.1；03 §8.1 仅要求撤销全部的能力）。
- 设备摘要仅展示脱敏信息（IP hash、User-Agent 截断），不显示完整 IP。

### 81.5 验收规则

1. 修改密码必须提交旧密码；错误旧密码返回统一 `LOGIN_FAILED` 并计入限流。
2. 改密成功后当前登录会话轮换，页面提示重新登录。
3. 概览页无 Account 切换入口；显示名与邮箱只读展示。
4. 「退出所有设备」撤销当前用户全部登录 Session，不影响 API Key 与 OAuth Grant。
5. 页面无任何明文凭证存储。

## 82. 个人 API Key `/app/profile/api-keys`

### 82.1 路由与守卫

- 仅 Account Admin/User（`credential.read.self`）；Platform Super Admin 不创建平台级个人 Key（03 §8.4）。

### 82.2 页面结构

- 列表区：名称、末四位（`key_last_four`）、状态（`active/revoked`）、到期时间、最近使用时间、创建时间；操作：撤销。
- 创建区：名称输入（必填）、可选到期时间；提交后跳转一次性明文展示页。

### 82.3 数据契约

| 动作 | API | 权限 |
| --- | --- | --- |
| 列表 | `GET /api/platform/v1/me/api-keys` | `credential.read.self` |
| 创建 | `POST /api/platform/v1/me/api-keys`（登录 Session + CSRF） | `credential.create.self` |
| 撤销 | `DELETE /api/platform/v1/me/api-keys/{id}`（登录 Session + CSRF） | `credential.revoke.self` |

### 82.4 一次性明文展示

- 创建成功后进入专用结果页，展示完整 Key（`ovk_u.<public_id>.<secret>`）与复制按钮。
- 页面明确提示「明文只显示这一次，离开后无法再次查看」。
- 前端只在当前内存状态中短暂持有明文；不写入 localStorage、sessionStorage、URL、错误上报、埋点或剪贴板历史管理逻辑（06 §13.5）。
- 刷新页面后无法再次获取明文；列表接口永不返回完整 Key（05 §12.4）。

### 82.5 错误与空状态

| 场景 | 页面行为 |
| --- | --- |
| 没有 Key | 空状态说明「可为 Codex、插件或 MCP 客户端创建个人访问凭证」，提供创建入口 |
| 撤销已撤销 Key | 幂等成功提示（05 §12.4） |
| Key 到期 | 列表标记「已到期」，不影响其他 Key |

### 82.6 验收规则

1. 创建成功后明文只在结果页出现一次；刷新/离开后不能再次获取。
2. 列表只显示元数据与掩码；浏览器存储与网络请求中无完整 Key。
3. 撤销单个 Key 不影响其他 Key；撤销后下一次请求立即失败。
4. 权限变更（禁用/角色变化）无需轮换 Key，下一次请求按新权限生效。

## 83. MCP OAuth 连接管理 `/app/profile/connections` 与授权页

### 83.1 `/app/profile/connections`

- 路由守卫：`integration.oauth.read.self`。
- 列表：已授权客户端（Client 名称、授权时间、最近使用时间、Scope=`mcp`、状态）；操作：撤销。
- 撤销确认弹窗提示「将断开该客户端并使用户所有相关连接失效」；不影响用户的其他 API Key 与其他客户端授权（03 §8.5）。
- 数据契约：`GET /api/platform/v1/me/oauth-grants`、`DELETE /api/platform/v1/me/oauth-grants/{grant_id}`（登录 Session + CSRF，`integration.oauth.revoke.self`）。

### 83.2 `/oauth/consent`（同设备授权）

- 瞬时产品页面，不进入导航（08 §29.5）。
- 流程：MCP 客户端发起 OAuth → 未登录先跳 `/login` → 登录成功只允许返回同源授权路由 → 展示待授权信息 → 允许/拒绝。
- 展示内容：客户端名称、回调域名（服务端登记值，不允许客户端提供的名称替代展示，同时展示 Client ID 与回调 host）、请求的 Scope、当前 Account、可访问数据范围、主要操作影响（06 §13.8）。
- 明确提示「该客户端将以你的身份运行，并受你当前角色权限限制」。
- 提交：登录 Session + CSRF（`integration.oauth.authorize.self`），不接受 API Key/OAuth Token（05 §12.4）。
- 页面不要求输入密码、Account 名称或 User API Key。

### 83.3 `/oauth/verify`（跨设备授权）

- 输入短期 display code → 展示与 consent 相同的待授权信息 → 允许/拒绝。
- `pending_id`、display code、authorization code、Token 不进入 URL 分析、错误上报、埋点或访问日志正文（06 §13.8）。

### 83.4 验收规则

1. 授权页完整走通同设备与跨设备流程，不依赖 Studio，浏览器不需要 User API Key。
2. 授权身份来自服务端登录 Session；API Key 不能代替浏览器批准 OAuth 客户端。
3. 授权页展示服务端登记的 Client 名称与回调域名，防仿冒。
4. 撤销 Grant 后该客户端 Token family 全部失效；用户其他 Key 与其他客户端不受影响。

## 84. 管理后台通用契约 `/admin/*`

### 84.1 信息架构与守卫

- 入口：`/admin/users`、`/admin/shared-resources`、`/admin/shared-skills`、`/admin/roles`、`/admin/audit`、`/admin/activity`、`/admin/monitoring`、`/admin/recycle-bin`、`/admin/users/{userId}/data`（06 §13.2）。
- 仅 Account Admin 可进入；普通 User 路由 Guard 阻止，直接请求返回 403/404。
- 所有 `/admin` 页面操作固定作用于当前登录 Session 的 Account，路径中的用户只作为 Subject 且必须属于该 Account（05 §12.6）。

### 84.2 Subject 数据视图

- `/admin/users/{userId}/*` 数据页固定显示「操作者与数据所属用户」横幅。
- 成员数据只读：检索、Session 历史、Memory Impact、Resource/Skill 只读预览；不提供下载/导出、修改、删除、发布（11 §73、09 §38.2）。
- 管理员只读查看不授予修改、导出或删除能力；查看权限不能自动推导写入权限（03 §9.3）。

### 84.3 通用交互

- 分页 Cursor；筛选条件保留在结果区上方。
- 加载失败保留页面框架，显示 Request ID 与重试，不展示底层异常（09 §39.4）。
- 列表加载与筛选不调用底层引擎诊断字段。

## 85. `/admin/users` 用户管理

### 85.1 页面结构

- 列表：`code`（username）、显示名、邮箱、状态（`provisioning/active/disabled/failed/pending_deletion`）、角色、最近登录、创建时间；按状态筛选。
- 操作（按行）：禁用/启用、重置密码、删除、查看数据（Subject 视图）、查看 API Key 元数据。
- 创建入口：右上「创建用户」。

### 85.2 创建用户

| 动作 | API | 权限 |
| --- | --- | --- |
| 创建 | `POST /api/platform/v1/admin/users` | `user.create`；只创建 `user` 角色 |

- 表单：邮箱（必填、全局唯一）、显示名、`code`（Account 内唯一，创建后不可修改，即 04 §10.2 `username`）。
- 提交成功后弹出一次性初始密码展示（可复制），提示「密码只会显示这一次，请通过线下方式交给用户」；不发送邀请（06 §13.9）。
- 初始密码无到期时间、不强制首次修改；用户可自愿修改（03 §8.3）。
- 创建时不能选择 `account_admin`；Account Admin 的创建与提升只能由 Platform Super Admin 执行（03 §8.3）。

### 85.3 禁用/启用

| 动作 | API | 权限 |
| --- | --- | --- |
| 禁用 | `POST /api/platform/v1/admin/users/{id}/disable` | `user.disable` |
| 启用 | `PATCH /api/platform/v1/admin/users/{id}`（status） | `user.update` |

- 禁用确认弹窗提示「该用户所有登录会话立即失效、API Key 立即拒绝；OpenViking 对话与记忆不受影响」。

### 85.4 重置密码

| 动作 | API | 权限 |
| --- | --- | --- |
| 重置 | `POST /api/platform/v1/admin/users/{id}/password/reset` | `user.password.reset.account`；目标必须是普通 User |

- 仅对严格低级别目标显示按钮（`actor_role_rank > target_role_rank`）；隐藏按钮不是安全边界，后端强制校验（03 §8.3、06 §13.9）。
- 确认弹窗提示「将使该用户所有网页登录设备退出；不会删除 OpenViking 对话和记忆，也不会撤销 API Key」。
- 成功后一次性展示新密码；旧密码立即失效（05 §12.6）。

### 85.5 删除与恢复

| 动作 | API | 权限 |
| --- | --- | --- |
| 删除预览 | `GET /api/platform/v1/admin/users/{id}/deletion-preview` | `user.delete` |
| 删除 | `DELETE /api/platform/v1/admin/users/{id}` | `user.delete` |
| 恢复 | `POST /api/platform/v1/admin/recycle-bin/{id}/restore` | 按对象类型校验（05 §12.6 注） |

- 删除确认弹窗展示影响范围（名称、所属 Account、预计影响、30 天恢复截止时间），只确认/取消，不重输密码或 Account 名（06 §14.5）。
- 删除进入 30 天回收期；期满由 Purge Worker 物理清理（05 §11.3）。

### 85.6 错误与验收

| 错误码 | 场景 |
| --- | --- |
| `LAST_ACCOUNT_ADMIN_REQUIRED` | 删除/禁用最后一个 Account Admin 被拒绝（06 §14.1 测试） |
| `PASSWORD_RESET_SAME_OR_HIGHER_ROLE_FORBIDDEN` | 同级或上级重置被拒 |
| `PROVISIONING_PENDING/FAILED` | 未开通/开通失败的用户操作被拒 |

1. Account Admin 只能创建普通 User；不能创建、提升或重置同级。
2. 初始/重置密码只展示一次；后续查询不返回密码。
3. 成员数据页为只读 Subject 视图，无任何写/导出按钮。
4. 删除预览与确认弹窗展示影响范围与恢复截止时间。

## 86. 共享内容管理页 `/admin/shared-resources`、`/admin/shared-skills`

### 86.1 共享 Resource 管理

- 动作矩阵引用 09 §38.2：列表/详情/导入/改元数据/替换/Refresh/Watch/删除/恢复。
- 列表显示 `provisioning/failed` 占位行（09 §39.2），普通 User 的共享列表不显示。
- 导入弹窗显示「保存到：{Account} 共享 Resource」，无归属下拉切换（09 §40.1）。
- 删除确认弹窗展示 Watch 暂停、在途任务取消、成员影响与恢复截止时间（09 §45.1）。

### 86.2 共享 Skill 管理

- 动作矩阵引用 10 §60：创建/上传/整体替换/删除/恢复；名称不可编辑。
- `/admin/users/{userId}/skills/{skillId}`：成员私有 Skill 只读详情 + 「发布为共享」按钮。
- 发布确认弹窗展示：Skill 名称、当前所属 User、目标 Account、发布后所有成员可读取和使用、原私有区不保留、不可取消发布（10 §58.3）。
- 发布是归属转换（保持 ID/名称），执行细节见 10 §58.4；发布后由 Account Admin 按共享 Skill 权限管理。

### 86.3 验收规则

1. 共享页管理入口只对 Account Admin 可见；普通 User 共享页无写/删/恢复按钮（09 §38.2、10 §53.1）。
2. 发布成员私有 Skill 的确认弹窗完整展示影响与不可取消提示。
3. 成员私有 Skill 只读详情页无编辑、删除、恢复入口（10 §61.3）。

## 87. 角色、审计、Activity 与监控页

### 87.1 `/admin/roles`

- 只读展示三个内置角色（`platform_super_admin/account_admin/user`）与权限矩阵（03 §9.3）。
- 无角色创建、编辑、删除、分配入口（v0.1 仅三内置角色，03 §9.2）。

### 87.2 `/admin/audit`

- 数据契约：`GET /api/platform/v1/admin/audit-events`（`audit.read`）；仅当前 Account（05 §12.6）。
- 列表字段：时间、Actor（User/系统组件）、Subject（User/Account）、动作、Scope、结果（成功/拒绝/失败）、Request ID；支持按时间、Actor、Subject、动作、结果筛选。
- 详情不展示：密码、Cookie、API Key 明文或完整 hash、Token、业务正文、完整来源 URL（06 §17、04 §10.8）。
- 管理员跨用户访问事件同时展示 Actor 与 Subject（06 §17.2）。

### 87.3 `/admin/activity`

- 数据契约：`GET /api/platform/v1/admin/activity`（`task.read.account_shared`）；仅当前 Account 共享对象任务（05 §12.6、04 §10.12）。
- 列表字段：操作类型、目标对象、状态、阶段、发起方式、时间；可对可取消任务发起取消（`task.cancel.account_shared` + 目标对象写权限，05 §12.6）。
- 不显示成员私有对象任务日志（04 §10.12）；不显示原始 Task ID、堆栈与 Worker 路径。

### 87.4 `/admin/monitoring`

- 数据契约：`GET /api/platform/v1/admin/monitoring`（`monitoring.read`）；仅当前 Account 业务摘要（05 §12.6）。
- 展示：共享对象失败任务、Provisioning 状态摘要、软删除待清理数量。
- 不展示：Queue、锁、模型、VectorDB、文件系统与原始请求日志（08 §28.4、06 §13.7）。

## 88. `/admin/recycle-bin`

### 88.1 页面结构

- 按对象类型分组：Account 共享 Resource、Account 共享 Skill、User（本 Account）、属主自己的私有对象（如适用）。
- 每项展示：名称、类型、删除时间、删除者、恢复截止时间、是否可恢复。
- 不展示：其他 User 的私有 Skill（不可恢复，不显示）、其他 User 的 Session（仅属主可恢复）、管理员不可恢复的对象类型。

### 88.2 恢复动作

- 恢复权限按对象类型分别校验，不存在单一「Account 范围恢复权限」（05 §12.6 注、06 §14.6）：
  - Account 共享 Resource/Skill：Account Admin 可恢复（对应共享恢复权限）。
  - 成员私有 Skill：不可恢复（10 §59）。
  - 成员 Session：不显示（11 §72 仅属主本人恢复）。
  - User：按数据范围恢复权限。
- 恢复确认弹窗：名称、类型、恢复截止时间；Skill 恢复同名冲突时返回 `SKILL_NAME_CONFLICT` 并保持删除状态（10 §55.3）。

### 88.3 验收规则

1. 回收站列表只显示当前 Account 内有权恢复的对象类型。
2. 恢复请求按对象类型校验；越权恢复返回 403。
3. `RESTORE_WINDOW_EXPIRED` 时提示恢复窗口已过，不可恢复。
4. 恢复动作写审计（Actor/Subject/对象/结果）。

## 88A. `/app` 个人回收站

> 属主本人范围（当前 User 可恢复自己的对象）；与 `/admin/recycle-bin` 共用类型分组/恢复弹窗/错误码展示组件，数据契约见 05 §12.5 `GET /api/platform/v1/recycle-bin`、`POST .../recycle-bin/{id}/restore`。

### 88A.1 页面结构

- 按对象类型分组：自己的私有 Resource、自己的私有 Skill、自己的 Session。
- 每项展示：名称、类型、删除时间、删除者（自己）、恢复截止时间、是否可恢复。
- 空状态：无恢复窗口内对象时展示说明文案与链接入口（新增 Resource / 在线创建 Skill）。
- 不展示：其他 User 的私有对象、Account 共享对象（共享对象恢复入口在 `/admin`，13 §88）。

### 88A.2 恢复动作

- 恢复权限按对象类型校验（05 §12.6 注）：
  - 自己的私有 Resource：`resource.user_private.delete.self`。
  - 自己的私有 Skill：`skill.user_private.manage.self`；同名已被占用时返回 `SKILL_NAME_CONFLICT` 并保持删除状态（10 §55.3）。
  - 自己的 Session：`session.delete.self`（11 §72）。
- 恢复确认弹窗：名称、类型、恢复截止时间；与 `/admin/recycle-bin` 同组件。

### 88A.3 验收规则

1. 仅展示当前 User 可恢复的对象；不可恢复类型不显示。
2. 越权恢复直接请求后端返回 403 并审计。
3. `RESTORE_WINDOW_EXPIRED` 正确展示、不可恢复。
4. 恢复动作写审计；Skill 恢复冲突展示 `SKILL_NAME_CONFLICT`。

## 89. 平台管理 `/platform/*`

### 89.1 信息架构与守卫

- 入口：`/platform/accounts`、`/platform/accounts/{accountId}/users`、`/platform/audit`、`/platform/activity`、`/platform/monitoring`、`/platform/recycle-bin`（06 §13.2）。
- 仅 Platform Super Admin 可进入。
- 选择目标 Account 是管理浏览（指定 Subject），不改变登录者 Actor 身份，不提供普通用户意义上的 Account 切换（06 §13.2）。

### 89.2 `/platform/accounts`

| 动作 | API | 权限 |
| --- | --- | --- |
| 列表 | `GET /api/platform/v1/platform/accounts` | `account.read.platform` |
| 创建 | `POST /api/platform/v1/platform/accounts` | `account.manage.platform` |
| 删除预览 | `GET /api/platform/v1/platform/accounts/{account_id}/deletion-preview` | `account.delete` |
| 删除 | `DELETE /api/platform/v1/platform/accounts/{account_id}` | `account.delete` |
| 重试开通 | `POST /api/platform/v1/platform/accounts/{account_id}/provisioning/retry` | `account.manage.platform`；仅 `provisioning/failed` 状态可重试，幂等 |

- 列表字段：名称、`code`、状态（`provisioning/active/suspended/failed/pending_deletion`）、成员数、创建时间；按状态筛选。`suspended` 状态只展示不操作（v0.1 无暂停/恢复产品端点，暂停以软删除表达，04 §10.1）。
- 创建表单：Account 名称、`code`、首位 Account Admin 的邮箱与显示名；成功后一次性展示首位 Admin 的初始密码（05 §12.6）。
- 页面不提供创建或重置另一个 Platform Super Admin 的入口（03 §9.2）。
- 删除确认弹窗展示影响范围（成员数、共享内容、30 天恢复截止时间）。

#### 89.2.1 视觉实现层（Aceternity 风格，2026-08-20 定稿）

本节补充设计实现层规则，**不改变 89.2 的字段契约与权限边界**。具体像素与组件 ID 见 [DESIGN-SYSTEM.md](../DESIGN-SYSTEM.md) §7.9（表格规范）、§12.2（节点 ID）。

- **名称列**：左侧 36×36 圆角 10px Account Avatar（紫色 `#7c3aed` 底 + 白色 14px 字母，2 个字母缩写），右侧两行：主名 14px 600 + `acct_xxx` ID 11px Geist Mono 灰；视觉权重比 ID 徽标高 6 倍。
- **code 列**：灰底 `#f5f5f5` 圆角 6px 胶囊 + 12px Geist Mono 500 `#3f3f46`；账号 code 创建后不可修改（04 §10.1 约束），胶囊视觉强调「只读」。
- **状态列**：5 状态徽标（DESIGN-SYSTEM §7.6），胶囊 24-28px 高 + 1px 同色描边让徽标"发光"；5 状态背景：绿 `provisioning/active`、橙 `provisioning`、红 `failed`、中性灰 `pending_deletion` / `suspended`。
- **成员数列**：14px Geist Mono 500；如 `iam_users` 计数尚未同步（`provisioning` 状态）显示 `—`。
- **创建时间列**：12px Geist Mono `#71717a`，24h 制 `YYYY-MM-DD HH:MM`；后端返回 RFC 3339 UTC，前端按用户时区展示（DESIGN-SYSTEM §10.2）。
- **操作列**：主操作按钮 + More 按钮组（详见下条）。
- **排序**：表头 4 列（名称 / 状态 / 成员数 / 创建时间）显示 `chevrons-up-down` 排序图标；后端按 query 参数 `sort_by` + `order` 处理（05 §12.2）。v0.1 不强制要求前端实现，但必须保留视觉锚点。
- **行交互**：hover 高亮紫色 `#7c3aed08`（10% alpha 紫）+ 紫色描边 `#7c3aed40`（25% alpha 紫，1px 边框）；非响应式行（rowId 不可点击进入详情）。
- **操作按钮**：
  - 默认行：紫色描边「View」按钮（图标 `eye` + 文字 12px 600 `#7c3aed`），点击进入 `/platform/accounts/{accountId}` 详情（13 §89.3）；右侧 28×28 More 按钮（lucide `ellipsis` 图标）下拉菜单（查看 / 删除 / 复制 code）。
  - `failed` 失败行：替换「View」为红色描边「Retry」按钮（图标 `refresh-cw` + 文字 12px 600 `#dc2626`），点击调用 `POST /api/platform/v1/platform/accounts/{account_id}/provisioning/retry`（13 §89.2 / 05 §12.6），请求中按钮变 loading 态。
- **筛选栏**：48px 高 + 12px 圆角 + 半透明白底（`#ffffffd9`）；左侧「Status」label + 自定义 Select 下拉选项（全部 / provisioning / active / failed / pending_deletion / suspended / 需关注）；右侧 hint 文字说明 suspended 无操作端点。
- **5 状态文案映射**（DESIGN-SYSTEM §10.2）：
  - `active` → 「正常」
  - `provisioning` → 「开通中」
  - `failed` → 「开通失败」
  - `pending_deletion` → 「删除中」
  - `suspended` → 「已暂停」
- **Page Header 副标题**「以 Platform Super Admin 身份管理平台；选择目标 Account 是管理浏览，不改变登录者身份」+ 跟随当前筛选状态计数（如「5 个Account · 1 个失败」紫色徽标）。
- **Create Account 按钮**：紫色主按钮，hover 加深至 `#6d28d9`；点击进入新建表单（13 §89.2 + 06 §13.9 校验）。
- **空状态**（v0.1.x 增量补帧）：DESIGN-SYSTEM §7.12，居中 72×72 紫色 `+` 胶囊 + 标题「还没有任何 Account」+ 副标题「创建第一个 Account 以开始使用 OpenViking 平台」+ 主按钮。

### 89.3 `/platform/accounts/{accountId}/users`

| 动作 | API | 权限 |
| --- | --- | --- |
| 列表 | `GET /api/platform/v1/platform/accounts/{account_id}/users` | `user.read.platform` |
| 提升 Account Admin | `PUT /api/platform/v1/platform/accounts/{account_id}/users/{user_id}/role` | `role.assign.platform`；仅 `user -> account_admin` |
| 重置密码 | `POST /api/platform/v1/platform/accounts/{account_id}/users/{user_id}/password/reset` | `user.password.reset.platform`；禁止目标为 Platform Super Admin |
| 查看 API Key | `GET .../users/{user_id}/api-keys` | `credential.read.platform` |
| 撤销 API Key | `DELETE .../users/{user_id}/api-keys/{credential_id}` | `credential.revoke.platform` |

- 成员数据页为 Subject 视图（同 84.2），可进入检索、Session、Resource、Skill 只读查看（05 §12.6、11 §73）。
- 提升与重置密码确认弹窗遵循 06 §13.9 规则（分级校验、退出登录设备提示、一次密码展示）。

## 90. `/platform` 审计、Activity、监控与回收站

- `/platform/audit`：`GET /api/platform/v1/platform/audit-events`，平台范围，可按目标 Account 筛选；展示规则同 87.2。
- `/platform/activity`：`GET /api/platform/v1/platform/activity`（`task.read.platform`），可按目标 Account 过滤；内部任务禁止取消（05 §12.6）。
- `/platform/monitoring`：`GET /api/platform/v1/platform/monitoring`，平台聚合业务摘要；不含底层组件状态。
- `/platform/recycle-bin`：平台范围回收站，恢复按对象类型校验（同 88.2）；对 Skill 始终只读（10 §60）。
- 所有平台级查询记录 Actor、Subject Account/User 与 Scope（04 §10.8、06 §17）。

## 91. 前端安全与本地存储

- 页面遵守 06 §13.5 本地存储规则与 06 §14 数据隔离规则。
- 管理页不缓存成员数据于 localStorage；Session 过期后回到登录页且不丢失安全状态（06 §13.4）。
- 高风险操作（删除 Account、删除用户全部数据、重置他人密码、撤销他人 API Key、删除/批量覆盖共享对象）必须展示影响范围确认弹窗并完整审计；弹窗只防误触，不是安全边界（06 §14.5）。

## 92. 页面级验收标准（汇总）

1. `/login` 只提供邮箱密码；无任何第三方登录、注册、邀请、找回入口。
2. 个人设置改密必须提交旧密码；成功轮换会话；「退出所有设备」生效。
3. API Key 明文只出现一次；列表仅掩码；浏览器无明文存储。
4. MCP OAuth 同设备/跨设备授权完整可走通，不依赖 Studio；授权页展示服务端登记信息；connections 可单独撤销。
5. Account Admin 的 `/admin` 页面固定作用于当前 Account；不能创建/提升/重置同级。
6. 成员数据视图为只读 Subject 视图，横幅展示 Actor 与 Subject，无写/导出按钮。
7. 初始/重置密码一次性展示；后续不可再次获取。
8. 回收站按对象类型显示与恢复；越权对象类型不可见；恢复按类型校验权限。
9. Platform 页面选择目标 Account 不改变 Actor；平台对 Skill 始终只读。
10. 所有管理页符合 06 §14 安全规则（CSRF、脱敏、幂等、确认弹窗）。

## 93. 与其他文档的关系与缺口记录

| 文档 | 关系 |
| --- | --- |
| 03 | 本文的认证与权限规则来源（Session、API Key、OAuth、角色矩阵） |
| 04 | 数据模型（用户、凭证、会话、审计、删除任务） |
| 05 §12.3/§12.4/§12.6 | 本文全部数据契约来源 |
| 06 | 前端路由、本地存储、安全、部署规则 |
| 09/10/11 | 共享内容与成员数据的业务动作矩阵来源 |
| 07 | 本文验收标准进入 Phase 3/4 的 E2E 与集成测试清单 |

冻结前缺口的处置记录（随一致性检查与重新冻结一并闭合）：

1. **Account Provisioning 重试接口**：已补齐——05 §12.6 平台 API 表新增 `POST /api/platform/v1/platform/accounts/{account_id}/provisioning/retry`（`account.manage.platform`，仅 `provisioning/failed` 状态可重试、幂等）；本节 §89.2 增加对应页面动作「重试开通」。
2. **个人登录 Session 列表 API**：维持决策——v0.1 只提供「退出所有设备」（`logout-all`），不做单会话列表/撤销（03 §8.1、§81.4）。
3. **`/admin/settings` 路由占位**：定为仅展示本 Account 基本信息占位，不承载正式功能（06 §13.2 已标注）。
