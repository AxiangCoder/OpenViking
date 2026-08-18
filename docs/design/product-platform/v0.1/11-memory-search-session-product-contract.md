# Memory、Search、Session 与 VikingBot 产品契约

> 状态：除“Product Session 如何稳定绑定 VikingBot/OpenViking Session、注入预选 Skill 并安全保存标题”外，其余 v0.1 规则已按 OpenViking v0.4.12 源码收敛。
> 源码基线：`server/routers/search.py`、`server/routers/sessions.py`、`server/routers/bot.py`、`service/session_service.py`、`session/session.py`、`web-studio/src/routes/retrieval`、`web-studio/src/routes/sessions`、`bot/vikingbot`。

## 60. 术语与组件责任

| 术语 | 专业含义 | 产品中的大白话 |
| --- | --- | --- |
| 登录 Session | Platform IAM 保存的网页登录状态 | 浏览器“已经登录” |
| OpenViking Session | 对话消息、上下文、归档、摘要和 Memory 提取的业务对象 | AI 对话记录和长期沉淀来源 |
| VikingBot Session | VikingBot 保存的 Agent 运行历史、工具事件和渠道状态 | AI 当前怎么聊、怎么调用工具的运行记录 |
| VikingBot | 模型推理、工具调用、Skill 执行和流式回复的 Agent Runtime | 真正生成回复、干活的 AI |
| Commit | 把 OpenViking Session 当前消息归档，并异步生成摘要和提取 Memory | 把一段聊天整理归档，沉淀为长期记忆 |
| Memory Impact | 某次 Commit 对 Memory 的新增、更新和删除差异 | 这段对话让系统记住、改掉或忘掉了什么 |

登录 Session 与 OpenViking Session 必须在代码、DTO、日志和页面文案中明确区分。本文出现的 Session 若无“登录”前缀，均指 OpenViking 对话 Session。

组件责任固定为：

```text
产品浏览器
  -> Product Session Facade（登录、RBAC、Actor/Subject、产品 DTO）
      -> VikingBot（模型、工具、流式事件、运行态 Session）
      -> OpenViking Session（消息、归档、上下文、Commit、Memory Diff）
      -> Search（Resource / Memory / Skill 统一检索）
```

VikingBot 是 v0.1 必选部署组件。OpenViking Session 不生成 AI 回复；VikingBot 不替代 OpenViking 的长期归档与 Memory 提取。

## 61. v0.1 能力边界

### 61.1 正式产品能力

- `/app/search`：快速检索、结合会话检索、受控筛选、产品化结果列表与详情抽屉。
- `/app/sessions`：Session 列表、创建、完整聊天、历史加载、流式状态、Memory Impact、软删除。
- `/admin/users/{userId}/data`：Account Admin 按 Subject 只读检索本 Account 成员 Memory，并查看其 Session。
- `/platform/accounts/{accountId}/users/{userId}/data`：Platform Super Admin 按 Subject 只读检索 Memory，并查看 Session。
- VikingBot 与 Product Session Facade 的受控 Chat/Stream 链路。

### 61.2 不进入正式产品

- 独立 `/app/memories` 页面和 Memory 新建、编辑、删除、恢复、下载接口。
- Search `grep/glob`、任意 URI、自定义原始 Filter、分数阈值、层级、Provenance 和 Query Plan。
- Session 手工 Commit、Extract、Used、Tool Result 原始读取/搜索、Token Budget、任意 Archive ID 和原始 Context。
- VikingBot Compile、Health、Gateway Token、运行配置和底层 Session 管理页面。
- Studio 的原始 URI、Playground 跳转、调试 JSON 和命令终端。

这些底层能力可继续存在于源码、SDK 或私网 Studio，但不成为 `/app` 页面动作。产品 API 与低层 API 仍经过统一 Principal Resolver、RBAC 和 URI Target Policy。

## 62. 数据归属与权限

| 对象/动作 | User | Account Admin | Platform Super Admin |
| --- | --- | --- | --- |
| 搜索自己的私有 Memory/Resource/Skill | 允许 | 允许 | 不适用 |
| 搜索当前 Account 共享 Resource/Skill | 允许 | 允许 | 按目标 Account 只读 |
| 查看自己的 Session/Memory Impact | 允许 | 允许 | 不适用 |
| 创建 Session、聊天、取消生成 | 允许自己的 | 允许自己的 | 不提供产品聊天 |
| 软删除/恢复自己的 Session | 允许 | 允许 | 不适用 |
| 查看其他 User Memory/Session | 禁止 | 本 Account 只读 | 全平台按 Account/User 只读 |
| 修改、删除、恢复他人 Memory/Session | 禁止 | 禁止 | v0.1 禁止 |

普通 User 的 Search 默认根为“自己的 User 私有根 + 当前 Account 共享根”。Account Admin/Platform Super Admin 查看成员数据时，Actor 仍是管理员，Subject 是目标 User；不能把管理员伪装成目标 User，也不能把搜索扩大到其他 Subject。

Memory 永远是 User 私有对象，不存在 Account 共享 Memory。Account 共享能力只适用于 Resource/Skill。

## 63. `/app/search` 页面

### 63.1 页面模式

页面直接复用源码 `find/search` 两种语义：

| 产品名称 | 源码能力 | 规则 |
| --- | --- | --- |
| 快速检索 | `POST /api/v1/search/find` | 默认模式，不使用 Session 上下文 |
| 结合会话检索 | `POST /api/v1/search/search` | 必须选择当前 User 自己且未删除的 Session；启用时结合 Session 和 Intent Analysis |

`recall` 只供 VikingBot/MCP 等受控调用链使用，不显示为页面模式。`grep/glob` 只留私网 Studio，不向产品 MCP 凭证发布。

### 63.2 筛选

主表单：

- 检索词。
- 模式：快速检索 / 结合会话检索。
- 内容类型：全部 / Memory / Resource / Skill。
- Session：仅结合会话检索时显示。

“更多筛选”：

- 结构化标签：严格 `key=value`；多项按 AND 关系匹配。
- 更新时间范围：固定映射 `time_field=updated_at`。

浏览器不得提交 `target_uri`、原始 `filter`、`score_threshold`、`level`、`include_provenance`、`limit/node_limit` 或 `time_field`。额外字段使用严格 DTO 拒绝，不静默透传。

### 63.3 标签规则

- Resource、Skill、Search 共用 OpenViking `search_tags`。
- 每项恰好包含一个 `=`，key/value 均非空。
- 去除首尾空白后整体小写，大小写不敏感去重。
- 每个对象最多 20 项，每项最多 40 字符。
- 多标签筛选要求结果同时具备全部标签。
- 不建立自由标签到 `search_tags` 的第二套转换层。

### 63.4 结果交互

直接复用 Studio 的“结果列表 + 右侧详情抽屉”：

- 保持引擎返回顺序，不提供用户可调分数或排序参数。
- 列表显示类型、产品显示名称、我的/Account 共享归属和摘要。
- Resource/Skill 点击后可进入相应产品详情页。
- Memory 点击后只打开只读抽屉，显示 Memory 类型、摘要和匹配原因。
- 搜索结果卡片不显示标签或更新时间；当前筛选条件显示在结果区上方。Resource/Skill 的标签与更新时间进入详情页查看。

产品结果不显示 URI、Score、L0/L1/L2、Query Plan、Provenance、Relations、底层 Category、原始 JSON或“在 Playground 打开”。Memory 抽屉不额外调用 `content/read`，也不提供编辑、删除、恢复或下载。

### 63.5 空状态和错误

- 未检索：提示输入检索内容。
- 当前 User 没有可检索数据：提示先添加 Resource/Skill 或开始聊天，不直接跳 Studio。
- 无结果：保留检索条件，提示修改检索词或缩小筛选。
- Session 已删除/无权限：返回 `SESSION_NOT_FOUND`，不泄露对象是否属于他人。
- Search 引擎不可用：显示可重试错误，不降级为跨权限文件遍历。

## 64. `/app/sessions` 页面

### 64.1 页面结构

直接复用 Studio 双栏布局：

- 左栏：Session 数量、新建按钮、按最近活动时间倒序的 Session 列表、删除入口。
- 右栏：当前 Session 标题、Memory Impact、消息历史、流式消息区和文本 Composer。
- 没有选中 Session 时显示空状态和新建提示。
- 保留 `Ctrl/Cmd + N` 新建 Session。

v0.1 Composer 复用现有 Studio，只支持文本输入；VikingBot HTTP API 已有图片能力，但当前产品页面不新增附件上传交互。

### 64.2 Session 标题

当前源码没有服务端标题字段，Studio 使用当前身份隔离的 `localStorage`，首轮消息前 20 个字符作为标题。该行为与本设计“Web Storage 只保存非敏感偏好”的规则冲突，因为首条用户消息可能包含隐私，不能直接复用。

- 决策前，新 Session 可安全回退显示“新对话”或 Session ID。
- 标题不得写入 `localStorage/sessionStorage`，不得用于授权或审计对象定位。
- 服务端标题方案与 Session ID/Skill 绑定一起在第 72 节确认。

无论采用何种标题方案，标题都不参与授权、数据归属、Session 唯一性或底层 URI。

### 64.3 创建与打开

- 新建调用 Product Session Facade，由服务端生成 Session ID；浏览器不能指定 URI、User、Account 或 Memory Policy。
- 缺失 Session 不使用 `auto_create=true`；只有显式新建或发送到已经授权的 Session 才能创建。
- 列表只返回当前 User 未删除 Session，按 `mod_time/updated_at` 降序。
- URL 只携带产品 Session ID，不携带 Viking URI。

### 64.4 历史加载

复用源码的完整历史拼装方式：

1. 读取 Session Meta 和当前 Context 中尚未归档的消息。
2. 根据 `commit_count` 读取已完成 Archive。
3. Archive 按编号排序，归档消息在前，当前消息在后。
4. 按 Message ID 去重。
5. Archive 暂时缺失时跳过该 Archive；其他错误正常返回。

产品浏览器只调用组装后的消息 DTO，不直接获取 Archive URI、`.meta.json`、`.done`、`.failed` 或 `messages.jsonl` 文件。

### 64.5 消息展示

直接复用当前组件行为：

- User 与 Assistant 气泡、相对时间、复制文本。
- Markdown、代码块、表格、引用等渲染。
- Reasoning 默认折叠，生成中可展开查看。
- Iteration 分隔、Tool 名称与运行/成功/失败状态。
- 流式 Typing、取消生成和自动滚动。

产品版不得在 Tool 卡片中显示原始 Viking URI、任意宿主机路径、完整 Secret 或未经脱敏的底层 JSON。Tool 参数/结果由服务端按 Tool Schema 生成产品摘要；不能安全转换的内容只显示状态和脱敏错误，原始调试内容留私网 Studio。

### 64.6 Chat Stream

浏览器调用：

```text
POST /api/platform/v1/sessions/{session_id}/chat/stream
Content-Type: application/json
Accept: text/event-stream

{"message": "...", "request_id": "客户端幂等 ID"}
```

服务端流程：

1. 从登录 Session 解析 Actor，不接受 Account/User/Role/API Key。
2. 校验 Session 属于当前 User 且未删除。
3. 以 `request_id + session_id + actor_user_id` 做幂等控制，同一 Session 同时只允许一个生成请求。
4. 将当前 User Principal 转为只在服务端使用的短期委托身份，注入 VikingBot request-scoped OpenViking connection；浏览器不持有 User API Key。
5. 调用 VikingBot `/bot/v1/chat/stream`，复用 `iteration/reasoning_delta/content_delta/tool_call/tool_result/response` SSE 事件。
6. 最终消息进入 VikingBot 运行 Session，并同步到对应 OpenViking Session；同步状态可观察、失败可重试。
7. SSE 结束事件返回 `response_id`、保存状态和可重试标识。

Product Facade 不向浏览器开放 `disabled_tools`、`openviking_connection`、`user_id`、`channel_id` 或 Gateway Token。

### 64.7 保存可靠性

Studio 当前实现会在流结束后分别追加 User/Assistant 消息，并静默忽略保存失败。产品版不得复用这一失败语义：

- User 消息或最终 Assistant 消息同步失败必须返回可见状态，不能显示为“已保存”。
- Bot 本地 Session metadata 保留最后同步下标、最后 Commit 下标、Pending Token、同步状态和脱敏错误；重试只追加尚未同步消息。
- 使用源码 `get_unsynced_messages` 与索引游标实现增量、幂等同步，不能每次重复追加整段历史。
- 基础聊天已经生成但长期同步失败时，页面保留回复并显示“同步待重试”；后台按同一 Session 重试，不能让用户重复发送才能保存。
- 同步恢复后刷新 Session Meta、消息历史和 Activity；失败详情不包含 API Key、完整 Tool Output 或内部路径。

### 64.8 取消与并发

- 用户点击停止时中止当前 SSE；已生成的部分内容按 VikingBot 已持久化状态显示。
- 同一 User 的不同 Session 可以并发；同一 Session 同时只允许一个 Chat Stream。
- 重复 `request_id` 返回原执行状态或重放已完成结果，不重复调用模型。
- Bot 超时、断线或 5xx 映射为产品错误并允许重试；不能自动切换到 Root 身份。

## 65. Commit、Archive 与 Memory

### 65.1 自动提交

产品页面不提供手工 Commit/Extract。直接复用 VikingBot/OpenViking 自动流程：

- VikingBot 增量同步未同步消息。
- 达到 `commit_token_threshold`、消息窗口阈值或显式记忆提交时执行 Commit。
- 使用源码 Turn-aware Retention：默认保留最近 3 个逻辑 Turn、6000 Token 预算、至少 1 个最新 Assistant Step。
- 旧 `keep_recent_count` 不作为产品配置或页面字段。

Commit Phase 1 同步完成归档并返回 Task；Phase 2 异步生成 Working Memory、摘要、Memory 提取和 `memory_diff.json`。Phase 2 不阻塞当前聊天。

### 65.2 显式“记住”

用户在对话中明确要求“记住”时，由 VikingBot 现有 `openviking_memory_commit` 工具同步并 Commit 当前 Session。产品不增加 Memory 创建表单，也不允许浏览器指定 Memory URI、类型或正文文件。

### 65.3 Memory Impact

直接复用 Studio 的统计与抽屉交互：

- 只有 `commit_count > 0` 时显示入口。
- 展示 Commit 数、总新增/更新/删除数量。
- 可按 Memory 类型筛选。
- 按 Commit 时间倒序展示每次差异。
- Add 显示新增内容；Update 显示 Before/After；Delete 显示删除前内容。

产品 DTO 删除 Archive URI 和 Memory URI，只返回 Commit 产品标识、提取时间、Memory 类型、动作和必要的 Before/After 内容。该视图只读，不提供回滚、恢复、编辑或删除。

Phase 2 状态：

- `pending/running`：显示“正在整理记忆”。
- `completed` 且无操作：显示“本次未产生长期记忆变更”。
- `completed` 且有操作：显示统计和差异。
- `failed`：显示可重试状态并进入 Activity；不伪造空结果。

### 65.4 Memory 生命周期

Memory 的 Add/Merge/Delete 由提取器和 Memory Policy 决定。v0.1 不把 Memory 纳入独立回收站，也不创建 `iam_deletion_jobs`。删除整个 User/Account 时，Memory 作为主体数据在 30 天总恢复窗口期满后随 User/Account 清理，不产生单条 Memory 恢复入口。

## 66. 删除与恢复

源码 `DELETE /api/v1/sessions/{id}` 会立即递归物理删除。产品版必须使用已确认的 30 天软删除：

1. Product Facade 创建 Session 删除任务并从正常列表/Search/Chat 隐藏。
2. 删除确认弹窗显示 Session 标题、消息数量、Commit 数、Memory Impact 记录是否仍可查看以及恢复截止时间。
3. 回收期内 User 可恢复自己的 Session；管理员不能恢复他人 Session。
4. 恢复后重新显示历史，但不会回滚该 Session 过去已经产生的 Memory 变更。
5. 30 天后 Worker 使用 canonical URI 幂等调用源码物理删除。

Session 删除期间禁止继续 Chat、Commit、Extract 或追加消息。重复删除幂等返回当前删除状态。

## 67. 管理员只读视图

Account Admin 与 Platform Super Admin 的成员数据页复用产品 Search 列表、Session 列表和消息展示组件，但：

- 显示“正在查看 Subject User 数据”，审计同时记录 Actor 与 Subject。
- 不显示新建 Session、Composer、取消生成、删除、恢复、Commit、Extract、复制完整 Tool Output 或“在 Session 中使用”。
- Memory 只能通过成员范围快速检索和该成员 Session 的 Memory Impact 查看。
- Account Admin 只能选择本 Account User；Platform Super Admin 必须先固定 Account，再选择 User。

## 68. Product API

### 68.1 Search

| 方法 | 路径 | Permission | 说明 |
| --- | --- | --- | --- |
| POST | `/api/platform/v1/search/find` | 各结果 read Permission | 快速检索 |
| POST | `/api/platform/v1/search/search` | 各结果 read + `session.read.self` | 结合自己的 Session 检索 |
| POST | `/api/platform/v1/admin/users/{user_id}/search/find` | `memory.read.account` 等目标读取权限 | Account 成员只读检索 |
| POST | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/search/find` | `memory.read.platform` 等目标读取权限 | 平台成员只读检索 |

### 68.2 Session

| 方法 | 路径 | Permission | 说明 |
| --- | --- | --- | --- |
| GET | `/api/platform/v1/sessions` | `session.read.self` | 当前 User 未删除 Session |
| POST | `/api/platform/v1/sessions` | `session.write.self` | 显式新建，不接受自定义身份/URI/Memory Policy |
| GET | `/api/platform/v1/sessions/{id}` | `session.read.self` | 产品 Meta 和同步/Commit 摘要 |
| GET | `/api/platform/v1/sessions/{id}/messages` | `session.read.self` | 已组装完整消息历史 |
| POST | `/api/platform/v1/sessions/{id}/chat/stream` | `session.write.self` | VikingBot SSE Chat |
| GET | `/api/platform/v1/sessions/{id}/memory-impact` | `session.read.self` | 脱敏 Memory Diff |
| DELETE | `/api/platform/v1/sessions/{id}` | `session.delete.self` | 30 天软删除 |
| POST | `/api/platform/v1/recycle-bin/{deletion_id}/restore` | `session.delete.self` | 恢复自己的 Session |
| GET | `/api/platform/v1/admin/users/{user_id}/sessions` | `session.read.account` | Account 成员只读列表 |
| GET | `/api/platform/v1/admin/users/{user_id}/sessions/{id}` | `session.read.account` | Account 成员只读历史/Impact |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/sessions` | `session.read.platform` | 平台只读列表 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/sessions/{id}` | `session.read.platform` | 平台只读历史/Impact |

手工 Commit/Extract/Used/Tool Result/Archive/Context 不属于 Product API；VikingBot、Worker、SDK/MCP 的底层入口可继续调用，但必须使用同一 User Principal 和实时 Permission。

## 69. 状态与错误码

### 69.1 Chat 状态

```text
idle -> submitting -> streaming -> saving -> completed
                     |           |-> sync_pending -> completed
                     |-> cancelled
                     |-> failed
```

切换 Session 或删除 Session 必须先终止当前流。`sync_pending` 不丢弃已生成回复；后台同步成功后自动转为 `completed`。

### 69.2 稳定错误码

| 错误码 | 含义 |
| --- | --- |
| `SESSION_NOT_FOUND` | Session 不存在、已删除或不可见 |
| `SESSION_BUSY` | 同一 Session 已有进行中的生成 |
| `SESSION_DELETED` | Session 处于回收期 |
| `CHAT_REQUEST_DUPLICATE` | request_id 冲突且请求内容不同 |
| `BOT_UNAVAILABLE` | VikingBot 不可用或超时 |
| `BOT_STREAM_FAILED` | SSE 在完成前失败 |
| `SESSION_SYNC_PENDING` | 回复已生成，OpenViking 长期同步待重试 |
| `SESSION_SYNC_FAILED` | 同步多次失败，需要 Activity 处理 |
| `SEARCH_UNAVAILABLE` | 检索引擎不可用 |
| `INVALID_SEARCH_FILTER` | 标签、时间或类型筛选不合法 |

对跨 User/Account IDOR 请求统一返回不可见语义，不说明目标是否真实存在。

## 70. 审计与可观测性

至少记录：

- Session 创建、软删除、恢复和期满清理。
- Chat 开始、完成、取消、失败；只记录消息长度、模型/Agent 标识、Tool 名称、耗时和 Token 摘要，不记录完整正文。
- VikingBot 到 OpenViking 的增量同步、Commit Task 和重试状态。
- 管理员查看成员 Search/Session/Memory Impact 的 Actor、Subject、Account 和理由字段（如页面来源）。
- Search 模式、类型、标签数量、时间范围和结果数量；不记录原始敏感 Query 时应使用脱敏或 Hash 策略。

日志和审计不得记录登录 Cookie、User API Key、Gateway Token、完整 Tool Input/Output、完整 Memory Before/After 或原始 URI。

## 71. 验收规则

1. User A 无法通过列表、URL、Search Session 选择器、Chat、Memory Impact 或回收站访问 User B Session。
2. 快速检索不加载 Session；结合会话检索只能使用当前 User 未删除 Session。
3. Search 筛选只接受内容类型、结构化标签和 `updated_at` 范围，额外调试字段被拒绝。
4. Search 结果不包含 URI、分数、层级、Query Plan、Provenance、Relations、标签或更新时间。
5. Resource/Skill 结果跳产品详情；Memory 只显示只读摘要和匹配原因。
6. `/app/sessions` 可创建、切换、加载归档历史、流式聊天、取消和复制消息。
7. 浏览器网络与存储中不出现 User API Key、Root Key 或 Gateway Token。
8. 同一 Session 并发 Chat 返回 `SESSION_BUSY`；同一 request_id 不重复调用模型或重复追加消息。
9. 同步失败不能静默成功；回复保留并显示 `sync_pending`，重试不重复追加历史。
10. Commit Phase 2 不阻塞聊天；Memory Impact 能区分 pending/completed/failed/空差异。
11. Memory Impact 不返回 Archive/Memory URI，管理员视图只读且同时记录 Actor/Subject。
12. Session 删除进入 30 天回收期，恢复不回滚已产生的 Memory；期满后才物理删除。
13. 产品 UI 不出现手工 Commit、Extract、Used、Tool Result、原始 Context/Archive、Compile 或 Playground 跳转。
14. Tool 卡片不显示原始 URI、宿主机路径、Secret 或未经脱敏的底层 JSON。

## 72. 唯一未决冲突包：Session 受信任绑定

### 72.1 Session ID 双写问题

当前 Studio 先创建一个 OpenViking Session，并把该 ID 作为 VikingBot HTTP `session_id`。但 VikingBot 的本地 `SessionKey.safe_name()` 会形成类似 `cli__default__<session_id>` 的 OpenViking 同步 ID；Studio 又在浏览器流结束后向原始 `<session_id>` 追加一份消息，而且会静默忽略追加失败。

直接照搬会产生两个风险：

- VikingBot 自动同步/Commit 的 Session 与产品页面读取的 Session 不是同一个稳定对象。
- 浏览器补写与 Bot 自动同步形成双写，可能重复、丢失或出现不同历史。

### 72.2 预选 Skill 缺口

已确认的 Skill 契约要求“在新 Session 中使用”携带稳定 `skill_id`，并在真正使用时再次鉴权；但 v0.4.12 VikingBot `ChatRequest` 只有 `message/images/session_id/disabled_tools/openviking_connection` 等字段，没有 `skill_id` 或受控 Skill 注入字段。

### 72.3 标题存储冲突

Studio 从首条用户消息截取标题并写入 `localStorage`。这会把潜在敏感正文持久化到浏览器，且跨设备不一致；当前 OpenViking `SessionMeta` 又没有 title 字段，因此没有可直接复用且符合安全规则的标题存储方式。

### 72.4 可选方案

方案 A：保持当前 Studio 双写。

- 不修改 VikingBot 请求模型。
- 浏览器或 Product Facade 继续在流结束后补写原 OpenViking Session。
- 预选 Skill 只能在 Composer 预填“请使用某 Skill”文本，不能保证加载或按稳定 ID 再鉴权。
- 标题继续保存在 `localStorage`，或者永久显示 Session ID。
- 无法消除两个 Session ID、双写和静默失败问题，不满足本文已收敛的保存可靠性规则。

方案 B：扩展受信任的 Product Facade -> VikingBot 请求。

- 增加服务端专用 `openviking_session_id`，强制 VikingBot 本地 Session、自动同步和 Product Session 绑定到同一 OpenViking Session。
- 增加服务端专用 `selected_skill_id`；Product Facade 先把产品 ID 解析为 canonical Skill、校验 use Permission，再由 Agent Runtime 显式加载。
- 扩展 OpenViking `SessionMeta.title`（不建立 PostgreSQL Session 影子目录）；初始为“新对话”，首轮完成后由服务端截取或生成，浏览器只读取，不写 Web Storage。
- 浏览器仍只提交产品 Session ID/普通消息，不接触 URI、User API Key 或这两个受信任字段。
- 取消浏览器流结束后的手工双写，统一由 VikingBot 增量同步游标负责持久化与 Commit。

这会修改 VikingBot 受信任请求契约，但同时解决两个缺口，并保持已确认的 Session 与 Skill 产品语义。

以下做法不采用：

- 让浏览器提交 `openviking_session_id`、`selected_skill_id` 对应 URI、Account/User 或任何内部身份字段。
- 使用 Root Key 让 Bot 绕过目标 Session/Skill 的当前 User 权限。
- 把用户消息派生标题继续写入 `localStorage/sessionStorage`。

在该决策前，Search、Commit、Memory Impact、删除恢复和管理员只读契约均不受影响；Session Chat 的最终调用 DTO 和 Skill 预选链路保持未决。
