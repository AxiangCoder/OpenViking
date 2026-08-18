# Memory、Search 与 Session 产品契约

> 状态：v0.1 规则已按 OpenViking v0.4.12 源码收敛。2026-08-18 更正：正式产品不提供网页聊天，VikingBot 不是必选组件。
> 源码基线：`server/routers/search.py`、`server/routers/sessions.py`、`server/routers/bot.py`、`service/session_service.py`、`session/session.py`、`web-studio/src/routes/retrieval`、`web-studio/src/routes/sessions`、`bot/vikingbot`。

## 66. 术语与组件责任

| 术语 | 专业含义 | 产品中的大白话 |
| --- | --- | --- |
| 登录 Session | Platform IAM 保存的网页登录状态 | 浏览器“已经登录” |
| OpenViking Session | 对话消息、上下文、归档、摘要和 Memory 提取的业务对象 | AI 对话记录和长期沉淀来源 |
| Agent/Client Session | Codex、其他 Agent 或客户端自身保存的运行历史 | 用户实际与 AI 聊天的地方 |
| VikingBot | 源码附带的一种可选 Agent Runtime，也是 OpenViking 的客户端之一 | 可选聊天机器人，不是本产品网页聊天后端 |
| Commit | 把 OpenViking Session 当前消息归档，并异步生成摘要和提取 Memory | 把一段聊天整理归档，沉淀为长期记忆 |
| Memory Impact | 某次 Commit 对 Memory 的新增、更新和删除差异 | 这段对话让系统记住、改掉或忘掉了什么 |

登录 Session 与 OpenViking Session 必须在代码、DTO、日志和页面文案中明确区分。本文出现的 Session 若无“登录”前缀，均指 OpenViking 对话 Session。

组件责任固定为：

```text
Codex / 其他 Agent / 插件 / MCP / SDK
  -> Product API / MCP Endpoint（用户凭证、RBAC、Actor/Subject）
      -> OpenViking Session（接收消息、归档、上下文、Commit、Memory Diff）

产品浏览器
  -> Product Facade（登录、RBAC、产品 DTO）
      -> 查看和管理已经写入的 Session
      -> Search（Resource / Memory / Skill 统一检索）
```

OpenViking 不负责生成 AI 回复。对话发生在 Codex、其他 Agent 或可选 VikingBot 中；这些客户端通过插件、MCP、SDK 或 API 把消息和 Commit 写入 OpenViking。VikingBot 与 Codex 是并列接入方，v0.1 可以不部署 VikingBot。

## 67. v0.1 能力边界

### 67.1 正式产品能力

- `/app/search`：快速检索、结合会话检索、受控筛选、产品化结果列表与详情抽屉。
- `/app/sessions`：已接入 Session 的列表、历史查看、Memory Impact、软删除和恢复；不提供消息输入框。
- `/admin/users/{userId}/data`：Account Admin 按 Subject 只读检索本 Account 成员 Memory，并查看其 Session。
- `/platform/accounts/{accountId}/users/{userId}/data`：Platform Super Admin 按 Subject 只读检索 Memory，并查看 Session。
- 插件、MCP、SDK、CLI 和 API 使用同一用户身份与权限写入 Session、触发 Commit。

### 67.2 不进入正式产品

- 独立 `/app/memories` 页面和 Memory 新建、编辑、删除、恢复、下载接口。
- Search `grep/glob`、任意 URI、自定义原始 Filter、分数阈值、层级、Provenance 和 Query Plan。
- Session 手工 Commit、Extract、Used、Tool Result 原始读取/搜索、Token Budget、任意 Archive ID 和原始 Context。
- VikingBot Compile、Health、Gateway Token、运行配置和底层 Session 管理页面。
- 网页 Chat/Stream、Composer、模型选择、Reasoning/Tool 实时流和“停止生成”。
- Studio 的原始 URI、Playground 跳转、调试 JSON 和命令终端。

这些底层能力可继续存在于源码、SDK 或私网 Studio，但不成为 `/app` 页面动作。产品 API 与低层 API 仍经过统一 Principal Resolver、RBAC 和 URI Target Policy。

## 68. 数据归属与权限

| 对象/动作 | User | Account Admin | Platform Super Admin |
| --- | --- | --- | --- |
| 搜索自己的私有 Memory/Resource/Skill | 允许 | 允许 | 不适用 |
| 搜索当前 Account 共享 Resource/Skill | 允许 | 允许 | 按目标 Account 只读 |
| 查看自己的 Session/Memory Impact | 允许 | 允许 | 不适用 |
| 通过插件/MCP/API 创建和追加自己的 Session | 允许自己的 | 允许自己的 | 不提供代用户写入 |
| 软删除/恢复自己的 Session | 允许 | 允许 | 不适用 |
| 查看其他 User Memory/Session | 禁止 | 本 Account 只读 | 全平台按 Account/User 只读 |
| 修改、删除、恢复他人 Memory/Session | 禁止 | 禁止 | v0.1 禁止 |

普通 User 的 Search 默认根为“自己的 User 私有根 + 当前 Account 共享根”。Account Admin/Platform Super Admin 查看成员数据时，Actor 仍是管理员，Subject 是目标 User；不能把管理员伪装成目标 User，也不能把搜索扩大到其他 Subject。

Memory 永远是 User 私有对象，不存在 Account 共享 Memory。Account 共享能力只适用于 Resource/Skill。

## 69. `/app/search` 页面

### 69.1 页面模式

页面直接复用源码 `find/search` 两种语义：

| 产品名称 | 源码能力 | 规则 |
| --- | --- | --- |
| 快速检索 | `POST /api/v1/search/find` | 默认模式，不使用 Session 上下文 |
| 结合会话检索 | `POST /api/v1/search/search` | 必须选择当前 User 自己且未删除的 Session；启用时结合 Session 和 Intent Analysis |

`recall` 只供 Agent/插件/MCP 等受控调用链使用，不显示为页面模式。`grep/glob` 只留私网 Studio，不向产品 MCP 凭证发布。

### 69.2 筛选

主表单：

- 检索词。
- 模式：快速检索 / 结合会话检索。
- 内容类型：全部 / Memory / Resource / Skill。
- Session：仅结合会话检索时显示。

“更多筛选”：

- 结构化标签：严格 `key=value`；多项按 AND 关系匹配。
- 更新时间范围：固定映射 `time_field=updated_at`。

浏览器不得提交 `target_uri`、原始 `filter`、`score_threshold`、`level`、`include_provenance`、`limit/node_limit` 或 `time_field`。额外字段使用严格 DTO 拒绝，不静默透传。

### 69.3 标签规则

- Resource、Skill、Search 共用 OpenViking `search_tags`。
- 每项恰好包含一个 `=`，key/value 均非空。
- 去除首尾空白后整体小写，大小写不敏感去重。
- 每个对象最多 20 项，每项最多 40 字符。
- 多标签筛选要求结果同时具备全部标签。
- 不建立自由标签到 `search_tags` 的第二套转换层。

### 69.4 结果交互

直接复用 Studio 的“结果列表 + 右侧详情抽屉”：

- 保持引擎返回顺序，不提供用户可调分数或排序参数。
- 列表显示类型、产品显示名称、我的/Account 共享归属和摘要。
- Resource/Skill 点击后可进入相应产品详情页。
- Memory 点击后只打开只读抽屉，显示 Memory 类型、摘要和匹配原因。
- 搜索结果卡片不显示标签或更新时间；当前筛选条件显示在结果区上方。Resource/Skill 的标签与更新时间进入详情页查看。

产品结果不显示 URI、Score、L0/L1/L2、Query Plan、Provenance、Relations、底层 Category、原始 JSON或“在 Playground 打开”。Memory 抽屉不额外调用 `content/read`，也不提供编辑、删除、恢复或下载。

### 69.5 空状态和错误

- 未检索：提示输入检索内容。
- 当前 User 没有可检索数据：提示先添加 Resource/Skill 或开始聊天，不直接跳 Studio。
- 无结果：保留检索条件，提示修改检索词或缩小筛选。
- Session 已删除/无权限：返回 `SESSION_NOT_FOUND`，不泄露对象是否属于他人。
- Search 引擎不可用：显示可重试错误，不降级为跨权限文件遍历。

## 70. `/app/sessions` 页面

### 70.1 页面结构

复用 Studio 的双栏浏览结构，但删除聊天功能：

- 左栏：Session 数量、来源客户端、最近活动时间、Session 列表和删除入口。
- 右栏：Session 标识、来源、同步状态、Memory Impact 和完整消息历史。
- 没有选中 Session 时提示用户先在 Codex、其他 Agent、插件或 MCP 客户端中连接 OpenViking。
- 页面没有“新建会话”、Composer、发送、停止生成、模型选择或附件上传。

### 70.2 Session 标题

v0.1 不新增标题字段，也不接收或持久化客户端提交的标题。当前 OpenViking `SessionMeta` 没有 `display_name` 字段（`openviking/session/session.py`），产品不为此扩展源码。Session 列表显示「来源客户端名称 + Session ID 短标识」。

标题不得参与授权、数据归属、Session 唯一性或底层 URI。Studio 从首条消息截取标题并写入 `localStorage` 的行为不进入正式产品；产品浏览器不得从消息正文派生标题并持久化。

### 70.3 接入与打开

- Session 由插件、MCP、SDK、CLI、API 或可选 VikingBot 以当前 User 身份创建和追加，不由产品浏览器创建。
- 每个接入方维护自己的客户端 Session 标识，并通过产品接入层映射到唯一 OpenViking Session。
- 列表只返回当前 User 未删除 Session，按 `mod_time/updated_at` 降序。
- URL 只携带产品 Session ID，不携带 Viking URI。
- 浏览器不能指定 User、Account、目标 URI、消息、Commit 参数或 Memory Policy。

### 70.4 历史加载

复用源码的完整历史拼装方式：

1. 读取 Session Meta 和当前 Context 中尚未归档的消息。
2. 根据 `commit_count` 读取已完成 Archive。
3. Archive 按编号排序，归档消息在前，当前消息在后。
4. 按 Message ID 去重。
5. Archive 暂时缺失时跳过该 Archive；其他错误正常返回。

产品浏览器只调用组装后的消息 DTO，不直接获取 Archive URI、`.meta.json`、`.done`、`.failed` 或 `messages.jsonl` 文件。

### 70.5 消息展示

复用当前历史消息的只读展示能力：

- User 与 Assistant 消息、相对时间和复制文本。
- Markdown、代码块、表格、引用等渲染。
- 已同步的 Reasoning、Iteration 和 Tool 事件仅在接入协议明确提供时展示。
- 不展示流式 Typing，不提供取消生成；生成过程属于外部 Agent 客户端。

产品版不得在 Tool 卡片中显示原始 Viking URI、任意宿主机路径、完整 Secret 或未经脱敏的底层 JSON。Tool 参数/结果由服务端按 Tool Schema 生成产品摘要；不能安全转换的内容只显示状态和脱敏错误，原始调试内容留私网 Studio。

### 70.6 外部写入链路

1. Codex、其他 Agent、插件或 MCP 客户端使用 User API Key 或用户委托型 OAuth Token 接入。
2. Principal Resolver 解析当前 User 和 Account；请求不能切换 Subject。
3. 接入方创建 Session、追加消息并在适当时机 Commit；服务端校验 Session 归属和幂等标识。
4. OpenViking 保存消息、归档、上下文及 Memory 提取结果。
5. 产品页面只读取最终已同步状态，不代理模型请求，也不持有 Agent Runtime 的 Gateway Token。

VikingBot 若被部署，也严格走同一用户接入链路；它不能因为是源码内置组件而获得 Root 权限或特殊数据范围。

### 70.7 保存可靠性

接入客户端负责确认写入结果，不能像 Studio 演示链路一样静默忽略失败：

- 创建、追加和 Commit 都必须返回稳定幂等结果。
- 支持增量同步的客户端记录最后成功游标，重试只追加未同步消息，不能重复提交整段历史。
- 写入失败时，产品页可以显示该 Session 最后成功同步时间；不能把客户端本地尚未上传的内容伪装为已保存。
- 失败详情不包含 API Key、完整 Tool Output 或内部路径。

### 70.8 并发与幂等

- 不同 Session 可以并发写入；同一 Session 的追加操作按服务端序列号或幂等键去重并保持顺序。
- 重复请求返回原写入结果，不重复追加消息或触发 Commit。
- 客户端断线后从最后确认游标续传；不得切换到 Root 身份重试。

## 71. Commit、Archive 与 Memory

### 71.1 自动提交

产品页面不提供手工 Commit/Extract。接入客户端直接复用 OpenViking 流程：

- 插件、MCP、SDK、CLI、API 或可选 VikingBot 增量同步未同步消息。
- 接入客户端达到自身提交阈值、会话阶段结束或收到显式记忆请求时调用 Commit。
- 使用源码 Turn-aware Retention：产品默认保留最近 3 个逻辑 Turn、12000 Token 预算（取引擎兜底值，`openviking/session/session.py`；不采用 VikingBot 插件默认的 6000）、至少 1 个最新 Assistant Step。产品服务端统一按此预算执行，接入客户端不得通过参数把预算降得更低。
- 旧 `keep_recent_count` 不作为产品配置或页面字段。

Commit Phase 1 同步完成归档并返回 Task；Phase 2 异步生成 Working Memory、摘要、Memory 提取和 `memory_diff.json`。Phase 2 不阻塞当前聊天。

### 71.2 显式“记住”

用户在 Codex 或其他 Agent 中明确要求“记住”时，由对应插件/MCP Tool 调用现有 `openviking_memory_commit`，同步并 Commit 当前 Session。VikingBot 只是可使用该工具的客户端之一。产品不增加 Memory 创建表单，也不允许浏览器指定 Memory URI、类型或正文文件。

### 71.3 Memory Impact

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

### 71.4 Memory 生命周期

Memory 的 Add/Merge/Delete 由提取器和 Memory Policy 决定。v0.1 不把 Memory 纳入独立回收站，也不创建 `iam_deletion_jobs`。删除整个 User/Account 时，Memory 作为主体数据在 30 天总恢复窗口期满后随 User/Account 清理，不产生单条 Memory 恢复入口。

## 72. 删除与恢复

源码 `DELETE /api/v1/sessions/{id}` 会立即递归物理删除。产品版必须使用已确认的 30 天软删除：

1. Product Facade 创建 Session 删除任务并从正常列表/Search 隐藏，后续接入写入返回 `SESSION_DELETED`。
2. 删除确认弹窗显示 Session 标识（客户端名称 + Session ID 短标识）、消息数量、Commit 数、Memory Impact 记录是否仍可查看以及恢复截止时间。
3. 回收期内 User 可恢复自己的 Session；管理员不能恢复他人 Session。
4. 恢复后重新显示历史，但不会回滚该 Session 过去已经产生的 Memory 变更。
5. 30 天后 Worker 使用 canonical URI 幂等调用源码物理删除。

Session 删除期间禁止继续 Commit、Extract 或追加消息。重复删除幂等返回当前删除状态。

## 73. 管理员只读视图

Account Admin 与 Platform Super Admin 的成员数据页复用产品 Search 列表、Session 列表和消息展示组件，但：

- 显示“正在查看 Subject User 数据”，审计同时记录 Actor 与 Subject。
- 不显示删除、恢复、Commit、Extract、复制完整 Tool Output 等修改动作。
- Memory 只能通过成员范围快速检索和该成员 Session 的 Memory Impact 查看。
- Account Admin 只能选择本 Account User；Platform Super Admin 必须先固定 Account，再选择 User。

## 74. Product API

### 74.1 Search

| 方法 | 路径 | Permission | 说明 |
| --- | --- | --- | --- |
| POST | `/api/platform/v1/search/find` | 各结果 read Permission | 快速检索 |
| POST | `/api/platform/v1/search/search` | 各结果 read + `session.read.self` | 结合自己的 Session 检索 |
| POST | `/api/platform/v1/admin/users/{user_id}/search/find` | `memory.read.account` 等目标读取权限 | Account 成员只读检索 |
| POST | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/search/find` | `memory.read.platform` 等目标读取权限 | 平台成员只读检索 |

### 74.2 Session

| 方法 | 路径 | Permission | 说明 |
| --- | --- | --- | --- |
| GET | `/api/platform/v1/sessions` | `session.read.self` | 当前 User 未删除 Session |
| POST | `/api/platform/v1/sessions` | `session.write.self` | 集成客户端创建；产品网页不调用，不接受自定义身份/URI/Memory Policy |
| GET | `/api/platform/v1/sessions/{id}` | `session.read.self` | 产品 Meta 和同步/Commit 摘要 |
| GET | `/api/platform/v1/sessions/{id}/messages` | `session.read.self` | 已组装完整消息历史 |
| POST | `/api/platform/v1/sessions/{id}/messages` | `session.write.self` | 集成客户端幂等追加消息；产品网页不调用 |
| POST | `/api/platform/v1/sessions/{id}/commit` | `session.write.self` | 集成客户端归档并触发 Memory 提取；产品网页不显示按钮 |
| GET | `/api/platform/v1/sessions/{id}/memory-impact` | `session.read.self` | 脱敏 Memory Diff |
| DELETE | `/api/platform/v1/sessions/{id}` | `session.delete.self` | 30 天软删除 |
| POST | `/api/platform/v1/recycle-bin/{deletion_id}/restore` | `session.delete.self` | 恢复自己的 Session |
| GET | `/api/platform/v1/admin/users/{user_id}/sessions` | `session.read.account` | Account 成员只读列表 |
| GET | `/api/platform/v1/admin/users/{user_id}/sessions/{id}` | `session.read.account` | Account 成员只读历史/Impact |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/sessions` | `session.read.platform` | 平台只读列表 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/sessions/{id}` | `session.read.platform` | 平台只读历史/Impact |

Extract/Used/Tool Result/Archive/Context 不属于浏览器 Product API。Session Create/Append/Commit 是插件、MCP、SDK、CLI、API 和可选 VikingBot 所需的正式集成能力，但不成为网页按钮；所有渠道必须使用同一 User Principal 和实时 Permission。

## 75. 状态与错误码

### 75.1 Session 同步状态

```text
active -> commit_pending -> committing -> active
                         |-> commit_failed -> retrying
active -> deletion_pending -> deleted -> purged
```

外部 Agent 自己的生成、流式和取消状态不属于 OpenViking Session 状态。产品页只展示最后同步时间、Commit/Memory 提取状态和删除状态。

### 75.2 稳定错误码

| 错误码 | 含义 |
| --- | --- |
| `SESSION_NOT_FOUND` | Session 不存在、已删除或不可见 |
| `SESSION_DELETED` | Session 处于回收期 |
| `SESSION_WRITE_CONFLICT` | 同一 Session 的序列号或幂等键冲突 |
| `SESSION_SYNC_FAILED` | 集成客户端写入或续传失败 |
| `SESSION_COMMIT_FAILED` | Commit 或异步 Memory 提取失败 |
| `SEARCH_UNAVAILABLE` | 检索引擎不可用 |
| `INVALID_SEARCH_FILTER` | 标签、时间或类型筛选不合法 |

对跨 User/Account IDOR 请求统一返回不可见语义，不说明目标是否真实存在。

## 76. 审计与可观测性

至少记录：

- Session 创建、软删除、恢复和期满清理。
- 集成客户端创建 Session、追加消息、Commit 和失败重试；只记录消息数量/长度、客户端标识和耗时摘要，不记录完整正文。
- 各接入客户端到 OpenViking 的增量同步、Commit Task 和重试状态。
- 管理员查看成员 Search/Session/Memory Impact 的 Actor、Subject、Account 和理由字段（如页面来源）。
- Search 模式、类型、标签数量、时间范围和结果数量；不记录原始敏感 Query 时应使用脱敏或 Hash 策略。

日志和审计不得记录登录 Cookie、User API Key、Gateway Token、完整 Tool Input/Output、完整 Memory Before/After 或原始 URI。

## 77. 验收规则

1. User A 无法通过列表、URL、Search Session 选择器、Chat、Memory Impact 或回收站访问 User B Session。
2. 快速检索不加载 Session；结合会话检索只能使用当前 User 未删除 Session。
3. Search 筛选只接受内容类型、结构化标签和 `updated_at` 范围，额外调试字段被拒绝。
4. Search 结果不包含 URI、分数、层级、Query Plan、Provenance、Relations、标签或更新时间。
5. Resource/Skill 结果跳产品详情；Memory 只显示只读摘要和匹配原因。
6. `/app/sessions` 可切换、加载归档历史、查看 Memory Impact 和复制消息，但不存在网页聊天输入框。
7. 浏览器网络与存储中不出现 User API Key、Root Key 或 Gateway Token。
8. 插件/MCP/API 重复幂等键不重复追加消息或触发 Commit，同一 Session 消息顺序稳定。
9. 同步失败不能静默成功；客户端可从最后确认游标续传，产品页不声称本地未上传内容已保存。
10. Commit Phase 2 不阻塞外部 Agent；Memory Impact 能区分 pending/completed/failed/空差异。
11. Memory Impact 不返回 Archive/Memory URI，管理员视图只读且同时记录 Actor/Subject。
12. Session 删除进入 30 天回收期，恢复不回滚已产生的 Memory；期满后才物理删除。
13. 产品 UI 不出现手工 Commit、Extract、Used、Tool Result、原始 Context/Archive、Compile 或 Playground 跳转。
14. 已同步 Tool 事件不显示原始 URI、宿主机路径、Secret 或未经脱敏的底层 JSON。

## 78. Studio 与 VikingBot 边界更正

Studio 中把 Session 浏览、VikingBot Chat 和浏览器补写放在同一界面，是源码自带的演示/调试组合，不代表正式产品必须采用同一调用链。v0.1 明确不复制这条链路，因此不存在“产品浏览器与 VikingBot 双写”的产品冲突，也不需要为了网页聊天扩展 `openviking_session_id` 或 `selected_skill_id`。

- Studio：可选私网开发与排障工具。
- VikingBot：可选 Agent 客户端，与 Codex、其他 Agent 并列。
- 插件/MCP/SDK/API：正式 Session 写入和检索渠道。
- `/app/sessions`：已同步 Session 的查看与管理页面，不是消息发送页面。
- Skill 的实际发现、读取和执行发生在有权访问它的 Agent/客户端；产品网页 v0.1 不提供“在新 Session 中使用”或 Skill Runner。

如果未来版本要提供平台内置 AI 对话，那是新增的 Agent Chat 产品能力，需要单独设计模型、Tool、Skill、流式协议、计费和 Session 映射，不能直接把当前 Studio Chat 当作既定产品架构。
