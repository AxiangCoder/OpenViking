# 09 Resource 页面与产品契约

> Design v0.1 · 页面、状态、动作与 API 契约<br>
> 上游源码基线：OpenViking v0.4.12<br>
> 本文只完善 Resource 产品设计，不是开发计划或任务拆分。

## 36. 设计范围与术语

本设计覆盖 User 私有 Resource、Account 共享 Resource，以及与 Resource 直接相关的导入、解析、检索、Watch、Task、软删除和恢复流程。

专业上，这组设计称为 **Resource Ingestion and Lifecycle Contract（资源摄取与生命周期契约）**：它规定一个外部文件、网页或代码仓库如何进入系统，何时对用户可见，处理失败如何恢复，以及不同角色可以做什么。

大白话说：用户看到的是一个“资料”，不是一棵可以随意操作的底层文件系统；系统负责把资料解析成 OpenViking 可检索的内容，页面只提供安全、稳定的业务动作。

### 36.1 v0.1 核心决策

1. Resource 是稳定产品对象，使用 `resource_id`；Viking URI 是内部实现，不进入地址栏、表单或普通产品 DTO。
2. “我的 Resource”和“Account 共享 Resource”是两个明确入口，不提供可随意切换的 `visibility` 或目标 URI 输入框。
3. v0.1 支持本地文件、公开 HTTPS 页面和公开 HTTPS Git 仓库；不提供本地服务器路径、任意 Connector、私有仓库凭证或 `args.auth_config` 表单。
4. 一个上传文件生成一个 Resource；一次最多选择多个文件只是 Batch UI，不把它们合成一个不可拆分对象。
5. 解析后的正文、目录树、L0 Abstract 和 L1 Overview 是引擎派生结果，v0.1 页面只读；用户只能编辑产品名称、说明和标签。
6. Watch 只支持有稳定远程来源的 Resource，放在 Resource 详情页，不建立顶级 Watch 菜单；上传文件不能开启 Watch。
7. 首次导入只有成功后才向普通共享读者可见；Refresh/Watch 期间继续提供上一次成功版本，不能暴露半处理状态。
8. 删除进入 30 天回收期；删除时立即停止新的同步，恢复后 Watch 默认保持暂停，不自动访问外部来源。
9. 从私有区发布到共享区采用复制/发布，生成新的 Resource ID；不是修改一个 `visibility` 字段，也不是底层 `mv`。
10. `content.write/batch-write/reindex`、任意 URI 浏览和原始 Watch/Task ID 不进入产品页面。

## 37. v0.4.12 源码基线与产品化差异

| 源码现状 | 源码能力 | v0.1 产品决策 |
| --- | --- | --- |
| `POST /api/v1/resources/temp_upload` | 临时上传，返回 `temp_file_id` | 包装为作用域绑定、短期、一次性的 Product Upload ID |
| `POST /api/v1/resources` | path/temp file、任意 `to/parent`、解析参数、Watch | 拆成 `/me` 与 `/account` 导入 API；目标由路由和权限决定 |
| Studio Add Resource | 用户可填写 `viking://resources/` 目标 URI | 删除 URI 输入框；私有页固定私有，共享管理页固定共享 |
| `strict/create_parent/directly_upload_media/processing_mode` | 引擎级解析开关 | 由产品默认值和部署能力配置决定，不作为 v0.1 普通表单字段 |
| `ignore_dirs/include/exclude` | 目录和仓库解析过滤 | 只在 Git 高级选项中提供受控输入 |
| `reason/instruction` | 处理提示与内部关联信息 | 页面只提供“处理要求”；审计原因由业务动作生成 |
| `GET /api/v1/fs/*` | 任意 URI 目录、状态、移动和删除 | 只由 Resource Facade 读取指定 Resource 内部节点；不暴露原始 URI |
| `content.read/abstract/overview/download` | 正文、摘要、概览、二进制下载 | 转换为受控详情、预览和下载 DTO |
| `content.write/batch-write/reindex` | 修改派生内容与重建索引 | 不进入 v0.1 Resource 产品 API；Refresh 走重新摄取流程 |
| `GET/PATCH/DELETE/trigger /api/v1/watches` | 以 Task ID 或 URI 控制 Watch，当前 `WatchTask.path` 会持久化来源 | 包装为 `resource_id/watch`；权限继承目标 Resource；持久化层改存 Resource 引用，不保存明文来源 |
| `GET/POST /api/v1/tasks` | 按底层 Task ID 查询/取消 | 包装为 Operation ID；只返回脱敏阶段和错误摘要 |
| Studio 文件限制 | 前端默认每批 10 个、单文件 10 MiB | 保留为默认值但由后端 Capabilities 返回，服务端重复校验 |

源码已经提供摄取能力，但不提供完整的产品对象目录、软删除、稳定 ID、共享只读策略、上次成功版本保护或跨入口统一授权；这些由 Product Facade、Content Registry 和 Operation Registry 补齐。

## 38. 页面路由与角色边界

### 38.1 正式路由

```text
/app/resources
/app/resources/private
/app/resources/private/$resourceId
/app/resources/shared
/app/resources/shared/$resourceId

/admin/shared-resources
/admin/shared-resources/$resourceId
/admin/users/$userId/resources/$resourceId

/platform/accounts/$accountId/resources
/platform/accounts/$accountId/resources/$resourceId
/platform/accounts/$accountId/users/$userId/resources/$resourceId
```

`/app/resources` 默认跳到最近使用的合法分区；首次默认进入 `/app/resources/private`。URL 中只有产品 ID，不出现 Account ID、User ID 或 Viking URI。

### 38.2 角色与动作矩阵

| 场景 | User | Account Admin | Platform Super Admin |
| --- | --- | --- | --- |
| 管理自己的私有 Resource | 查看、导入、改元数据、Refresh/Watch、删除、恢复 | 同 User | 无自身 Account 私有区 |
| 读取当前 Account 共享 Resource | 查看、检索、预览、下载、在 Session 中使用 | 同 User | 选择 Account 后读取 |
| 管理当前 Account 共享 Resource | 不允许 | 导入、改元数据、Refresh/Watch、删除、恢复 | 按目标 Account 代管 |
| 查看其他 User 私有 Resource | 不允许 | 当前 Account 只读 | 平台范围只读或按独立高风险 Permission 处理 |
| 修改/删除其他 User 私有 Resource | 不允许 | 默认不允许 | 必须拥有对应平台级高风险 Permission |
| 将私有 Resource 发布为共享 | 不允许 | 只能发布自己的私有 Resource | v0.1 不从他人私有区直接发布 |

管理员查看他人私有 Resource 的页面是 Subject 数据查看，不是切换身份；页面固定显示“操作者”和“数据所属用户”，并隐藏编辑、Watch、发布、下载/导出和删除按钮，除非平台角色确实拥有对应独立 Permission。`resource.user_private.read.account/platform` 只允许受控页面预览，不自动推导出原文件下载或批量导出。

## 39. Resource 列表页契约

### 39.1 页面结构

列表页包含：

- 页面标题和当前范围：`我的 Resource` 或 `Account 共享 Resource`。
- 关键词搜索：匹配产品名称、说明、标签和可检索摘要，不接受 URI。
- 筛选：来源类型、处理状态、标签、更新时间。
- 排序：最近更新、名称、最近处理；默认最近更新倒序。
- 新增按钮：私有页对 Account User 可见；共享页只对 Account Admin/Platform Super Admin 可见。
- Resource 表格或卡片列表。
- Cursor 分页，不提供任意大页码跳转。

### 39.2 列表字段

| 字段 | 说明 |
| --- | --- |
| `name` | 用户可编辑的产品名称，不等于路径 |
| `source_type` | 文件、网页、Git 仓库 |
| `summary` | L0 Abstract 的安全截断；未生成时显示状态说明 |
| `tags` | 最多展示前三个，其余显示数量 |
| `lifecycle_status` | 首次处理中、可用、首次处理失败、待删除 |
| `latest_operation` | 可选；Refresh/Watch 的处理中或最近失败，不改变已有可用版本 |
| `watch_state` | 未启用、运行中、已暂停、最近同步失败；上传文件不显示 |
| `updated_at` | 产品元数据或最新成功内容更新时间 |
| `created_by` | 仅共享列表与管理视图显示；不影响权限 |

普通 User 的共享列表只返回 `active` 对象。私有所有者、Account Admin 的共享管理页和 Platform 代管页可以看到自己负责范围内的 `provisioning/failed` 占位行，以便查看处理状态和重试。

### 39.3 列表动作

- 点击行进入详情。
- 有写权限时提供“编辑信息”“Refresh/替换来源”“删除”。
- 普通共享读者只提供“查看”“在 Session 中使用”和允许时的单文件下载。
- 列表不提供批量移动、批量覆盖、直接改为共享或任意目录操作。
- v0.1 不做批量删除；共享删除需要逐项展示影响范围。

### 39.4 空状态与错误状态

| 状态 | 页面行为 |
| --- | --- |
| 没有私有 Resource | 解释可上传文件或添加公开链接，并提供新增按钮 |
| 没有共享 Resource | 普通 User 显示“暂无 Account 共享资料”；管理员显示新增按钮 |
| 当前筛选无结果 | 保留筛选条件，提供清除筛选 |
| 列表加载失败 | 保留页面框架，显示 Request ID 和重试，不展示底层异常 |
| 无页面 Permission | 路由 Guard 阻止；直接请求由后端返回 403 |
| Resource ID 不属于可见范围 | 返回 404，防止对象枚举 |

## 40. 新增 Resource 契约

### 40.1 新增入口决定数据归属

| 发起页面 | 固定目标 | 必需 Permission |
| --- | --- | --- |
| `/app/resources/private` | 当前 User 私有区 | `resource.user_private.write.self` |
| `/admin/shared-resources` | 当前 Account 共享区 | `resource.account_shared.write.account` |
| `/platform/accounts/$accountId/resources` | 选中 Account 共享区 | `resource.account_shared.write.platform` |

新增弹窗显示“保存到：我的 Resource”或“保存到：{Account} 共享 Resource”，但没有下拉切换。用户若进入了错误入口，应关闭弹窗并回到正确页面，而不是在表单中改变归属。

### 40.2 支持的来源类型

| 来源类型 | v0.1 支持 | Watch | 说明 |
| --- | --- | --- | --- |
| 本地文件 | 支持 | 不支持 | 每个文件生成一个 Resource |
| 公开 HTTPS 网页/文档 | 支持 | 支持稳定 URL | 禁止内网、localhost、文件协议和用户凭证 |
| 公开 HTTPS Git 仓库 | 支持 | 支持 | 可设置目录过滤；不接收 Token/SSH Key |
| 带认证的网页、私有 Git | 不支持 | 不支持 | 需要后续独立的 Connector/Secret 设计 |
| Feishu/TOS 等 `add_type` Connector | 不进入产品页面 | 不进入产品页面 | 当前底层能力不能直接变成通用凭证表单 |
| 服务器本地路径 | 禁止 | 禁止 | 防止读取宿主机文件 |
| 手工新建文本 | v0.1 不提供 | 不适用 | 避免把 Resource 与 Memory/Note 概念混合 |

### 40.3 基础字段

| 字段 | 是否必填 | 规则 |
| --- | --- | --- |
| 来源 | 是 | 文件、公开 HTTPS URL 或公开 HTTPS Git URL |
| 名称 | 否 | 默认从文件名、页面标题或仓库名生成；1–128 字符，不参与 URI |
| 说明 | 否 | 最多 1000 字符，只描述业务用途 |
| 标签 | 否 | 最多 20 个，每个最多 40 字符，大小写不敏感去重，不允许 Secret |
| 处理要求 | 否 | 最多 2000 字符，映射到引擎 `instruction`；页面不显示 `reason` |
| 自动同步 | 否 | 只对稳定网页/Git 来源显示；默认关闭 |

### 40.4 Git 高级字段

- 忽略目录：最多 50 项，只允许相对目录名或相对路径；禁止 `..` 和绝对路径。
- Include/Exclude：最多各 20 个 Glob；服务端限制表达式长度与复杂度，防止遍历和 ReDoS 风险。
- 默认保留仓库目录结构。
- `strict=false`：不支持文件被跳过并形成处理报告，不因仓库内一个二进制文件让整个仓库失败。
- 产品固定 `processing_mode=semantic_and_vectors`；`vectors_only` 不作为用户选项。

### 40.5 文件上传

- 默认每批最多 10 个文件、单文件 10 MiB，与当前 Studio 基线一致；最终数值由 `ResourceCapabilities` 返回，不能只靠前端常量。
- 浏览器先检查数量、扩展名和大小，服务端再次做流式大小限制、Magic Bytes/MIME 检测、压缩包与恶意文件策略检查。
- 每个文件先获得与 Actor、目标 Scope 和过期时间绑定的 Upload ID；默认 15 分钟过期、只能消费一次。
- 批量导入按文件独立成功或失败，返回 `batch_id` 和每项 `resource_id/operation_id/error`；不做全批事务回滚。
- 上传完成不等于 Resource 可用。页面从“上传中”进入“处理中”，再根据 Operation 状态更新。
- 上传来源不能开启 Watch；处理失败后若临时文件已失效，页面显示“重新选择文件”，不提供虚假的一键重试。

### 40.6 远程来源安全

- 产品入口默认只接受 HTTPS；是否允许 HTTP 由部署策略决定，前端不能自行放宽。
- 禁止 `file://`、`ftp://`、localhost、Loopback、Link-local、私网地址、云元数据地址和 DNS Rebinding。
- 每次 Redirect 都重新执行目标校验；限制 Redirect 次数、响应大小、下载时长和 Git clone 规模。
- URL 禁止 `username:password@host`。包含 Query 的 URL 可以做一次性导入，完整值仅在加密的短期 Operation 输入中保存并于任务终态清除；产品只保存去除 Query/Fragment 的展示值，不提供自动 Refresh/Watch。需要 Watch 的稳定 URL 必须没有 Userinfo、Query 和 Fragment，并以应用层密文保存。
- Git v0.1 只允许公开 HTTPS Repository URL；SSH、`git@` 和客户端提交的认证参数被 Product API 拒绝。
- 日志、审计和错误上报只记录脱敏 Host/Path 与 URL fingerprint，不记录 Query、Fragment、Header 或页面正文。

### 40.7 异步导入流程

```text
用户提交
  -> Product API 校验登录、Permission、Scope、Capabilities
  -> 创建 platform_content_refs(status=provisioning)
  -> 创建 platform_operation_refs(kind=task, type=resource_import)
  -> 获取/消费 Upload 或校验远程来源
  -> OpenViking 在受控目标执行摄取
  -> 成功：绑定 canonical ov_uri，Resource=active，Operation=succeeded
  -> 失败：Resource=failed，Operation=failed，返回脱敏错误与 retryable
```

创建 Content Ref、Operation Ref 和 Outbox 必须在同一 PostgreSQL 事务中完成；调用 OpenViking 由事务后编排。客户端重复提交同一个 `Idempotency-Key` 必须获得相同 Batch/Operation，不生成重复 Resource。

## 41. 生命周期与处理状态

### 41.1 两组状态不能混用

**Resource Lifecycle Status** 表示产品对象能否使用：

```text
provisioning --success--> active --delete--> pending_deletion --purge--> deleted
      |                     ^                    |
      +--failure--> failed  |                    +--restore--> active
                 |          |
                 +--retry--> provisioning
```

**Operation Status** 表示某一次导入/Refresh 任务的进度：

```text
pending -> running -> succeeded
                   -> failed
        -> cancelling -> cancelled
```

产品处理阶段只使用稳定枚举：

| 产品阶段 | 可映射的源码阶段 | 用户文案 |
| --- | --- | --- |
| `queued` | `queued` | 等待处理 |
| `fetching` | `fetching` | 正在获取来源 |
| `parsing` | `parsing` | 正在解析内容 |
| `indexing` | `processing_queue` | 正在生成摘要与索引 |
| `finalizing` | `finalizing` | 正在完成处理 |
| `succeeded/failed/cancelled` | Task 终态 | 处理完成/失败/已取消 |

前端不得依赖 OpenViking 内部字符串作为业务状态；映射由 Product Facade 完成。

### 41.2 可见性规则

- 私有首次导入：所有者可看到 provisioning/failed 占位行，其他任何 User 不可见。
- 共享首次导入：Account Admin/Platform 代管页可见占位行；普通 User 只在 Resource active 后看到。
- Refresh/Watch：旧 active 版本保持可读，页面额外显示“正在同步”；新版本成功后原子切换。
- Refresh 失败：Resource 保持 active 并继续提供上一次成功版本，同时显示“最近同步失败”；不能把整个 Resource 置为不可用。
- 首次导入失败：没有成功版本，因此 Resource 保持 failed，只对负责处理的管理者可见。
- 删除中：从正常列表立即隐藏，详情只通过回收站入口访问。

### 41.3 原子更新要求

Refresh/Watch 必须先写入 staging target 或引擎版本区，完成解析和索引后再切换当前版本。若 v0.4.12 的直接覆盖流程不能保证这一点，Product Adapter 必须使用影子 URI/版本指针实现；不能让用户在处理中读到新旧文件混合状态。

v0.1 不提供内容版本浏览和回滚页面。系统仅保留“上一次成功版本”用于 Refresh 失败保护，不等同于对外开放 Snapshot。

## 42. Resource 详情页契约

### 42.1 页面头部

头部展示：

- 名称、私有/Account 共享标识。
- 来源类型与脱敏来源。
- 可用/处理中/最近同步失败/待删除状态。
- 最近成功处理时间、更新时间。
- 标签。
- 根据 Permission 显示“在 Session 中使用”“编辑信息”“Refresh/替换文件”“自动同步”“删除”。

“复制链接”复制产品 URL，不复制 Viking URI。“在 Session 中使用”创建或打开当前 User 自己的 Session，并记录对该 Resource 的引用，不改变 Resource 所有权。

### 42.2 详情标签页

| 标签页 | 内容 | 规则 |
| --- | --- | --- |
| 概览 | 说明、L0 Abstract、L1 Overview、来源、标签和处理摘要 | Abstract/Overview 未完成时显示处理中，不显示内部占位文件正文 |
| 内容 | Resource 内部只读树、文件预览、单文件下载、Resource 内搜索 | 根固定为当前 Resource；隐藏内部文件和绝对路径 |
| 自动同步 | Watch 状态、周期、上次/下次执行、最近结果 | 仅稳定远程来源显示；无写权限时只读 |
| 活动 | 导入、Refresh、Watch、元数据更新和删除事件 | 返回产品 Operation/Audit 摘要，不返回堆栈和 Worker 路径 |

### 42.3 内容树与预览

- 只允许从 `resource_id` 解析到该 Resource canonical root；客户端不能提交另一个 URI。
- `node_id` 是服务端生成并绑定 `resource_id + relative_path` 的不透明标识，可使用带版本的 HMAC 编码或服务端映射；解析后必须再次确认规范化路径仍位于该 Resource 根内。
- 隐藏 `.abstract.md`、`.overview.md`、`.relations.json`、锁文件、临时目录和其他内部控制文件。
- 目录按需展开，服务端限制单页节点数和最大深度；大仓库不能一次返回整棵树。
- 文本按区块分页读取；PDF、Office、图片、音视频根据安全预览能力展示；不支持预览时只显示元数据和下载。
- HTML、SVG 和富文本在隔离 Sandbox 中渲染或以纯文本展示，禁止执行来源脚本。
- 下载响应使用安全文件名、正确 Content-Type 和 `Content-Disposition`；可执行文件与高风险类型不内联。
- v0.1 不允许编辑解析后的文件。上传新的源文件或 Refresh 是唯一内容更新入口。

### 42.4 元数据编辑

可编辑字段只有 `display_name`、`description` 和 `tags`。更新使用 `If-Match: <version>` 或请求体版本号做乐观锁；版本冲突返回 `RESOURCE_VERSION_CONFLICT`，前端重新加载后让用户决定是否再次提交。

名称修改只更新 PostgreSQL 产品元数据，不移动 `ov_uri`、不重建索引，也不改变分享链接。标签更新由 Product Facade 同步到 OpenViking Search Tags；若同步失败，事务记录为待重试并在管理视图告警，不能静默出现两套标签。

## 43. Refresh、Watch 与 Activity

### 43.1 Refresh/替换来源

| 来源 | 用户动作 | 行为 |
| --- | --- | --- |
| 上传文件 | 替换文件 | 上传新文件并对同一 Resource 产生 `resource_replace` Operation |
| 网页 | Refresh | 重新抓取已保存的稳定公开 URL |
| Git | Refresh | 重新拉取同一公开 Repository 与原过滤规则 |

v0.1 不允许直接修改来源 URL。来源发生变化时创建新 Resource，避免一个 ID 的来源审计含义漂移。

同一个 Resource 同时只允许一个摄取类 Operation。已有导入、Refresh 或 Watch 执行时再次触发返回 `RESOURCE_BUSY`；页面禁用按钮并显示当前 Operation。手动 Refresh 默认每个 Resource 5 分钟限频，具体由 Capabilities 返回。

### 43.2 Watch 配置

| 字段 | 说明 |
| --- | --- |
| `state` | `active/paused/not_configured/error` |
| `interval_minutes` | 只接受 Capabilities 返回的预设值；建议 60、360、720、1440、10080 |
| `last_run_at/next_run_at` | 最近和下次计划时间 |
| `last_result` | `succeeded/failed/cancelled` |
| `last_error` | 脱敏错误摘要，可空 |
| `processing_instruction` | 可选；更新后用于后续同步 |

动作：

- 启用：目标 Resource 必须可写、来源稳定、Scheduler 可用且当前没有冲突 Watch。
- 暂停：保留周期与配置，不再调度。
- 恢复：重新计算下次执行时间，不补跑暂停期间次数。
- 立即同步：异步返回 Operation ID；已有运行任务时拒绝重复触发。
- 删除 Watch：只删除自动同步配置，不删除 Resource。

Watch 持久化记录只保存 Resource ID 和调度参数，不保存明文远程 URL。Watch Worker 使用系统执行身份，在执行时由 Product Facade 校验 Resource 仍为 active、解析并临时解密来源；审计同时保存配置/触发 Actor、目标 Resource Subject 和 `actor_system_component`。Worker 不能继承创建者过期的登录 Session，也不能借用用户 API Key。

### 43.3 Activity

Resource 详情 Activity 只显示与当前 Resource 绑定的 Operation；`/app/activity` 是跨对象聚合。每项至少包含：

- Operation 产品 ID、类型、状态和产品阶段。
- 发起方式：页面、API Key、OAuth/MCP、Watch Scheduler 或 System。
- 发起时间、开始时间、结束时间。
- 是否可取消、进度文案。
- 脱敏错误码、用户可理解的错误摘要和 `retryable`。

仅 `pending/running` 且底层 Task 类型支持协作取消时显示取消。取消成功表示“系统已接受并最终停止”，不是保证立即终止；状态先进入 `cancelling`。

## 44. 私有与共享之间的发布

### 44.1 发布规则

- 只有 Account Admin 可以把**自己的**私有 Resource 发布到自己所属 Account 的共享区。
- 普通 User 没有发布入口；v0.1 不设计申请/审批流程。
- Account Admin 读取其他用户私有数据的权限不包含发布、复制或导出，因此不能把成员私有 Resource 发布为共享。
- Platform Super Admin v0.1 不从用户私有区直接发布；平台可以在目标 Account 共享页以新来源重新创建共享 Resource。
- 发布前弹窗展示来源 Resource、目标 Account、文件/节点数量、大小、是否携带标签和是否创建新 Watch。

### 44.2 发布结果

发布生成新的 Account 共享 `resource_id`、独立 `platform_content_refs` 和独立 canonical URI。原私有 Resource 保留，两个对象之后独立更新和删除。

默认复制名称、说明、标签和当前成功内容，不复制：

- 私有 Resource 的 Watch 配置。
- 私有 Activity、错误日志和审计历史。
- 可能包含凭证的来源 Query/临时上传信息。
- 与私有 Session 或 User Memory 的关系。

若发布对象需要持续同步，Account Admin 必须在新共享 Resource 详情中重新显式启用 Watch。

## 45. 删除、恢复与并发

### 45.1 删除预览

删除前请求 `deletion-preview`，弹窗展示：

- Resource 名称和私有/共享范围。
- 内部文件/节点数量和估算大小。
- Active Watch 是否会被暂停。
- 当前处理任务是否会请求取消。
- 30 天恢复截止时间。
- 共享 Resource 还要显示“当前 Account 成员将无法继续查看和在 Session 中引用”。

用户只点击确认或取消；不重输密码、Account 名称，也不需要第二个人审批。

### 45.2 删除事务

1. 后端重新校验登录 Session/凭证、Permission、Actor/Subject 和对象版本。
2. `platform_content_refs.status=pending_deletion`，创建 `iam_deletion_jobs`。
3. Watch 立即暂停，新的 Refresh/Watch 调度被拒绝。
4. 对可取消的在途 Operation 发出协作取消；即使取消未立即完成，对象也保持不可见。
5. 正常列表和 Search 立即排除对象，所有入口统一返回删除中状态或 404。
6. 30 天后 Purge Worker 物理清理 OpenViking 内容和派生索引。

### 45.3 恢复

- 只有回收期内、未开始不可逆 Purge 的对象可以恢复。
- 恢复后回到最近一次成功 active 版本；没有成功版本的失败占位对象不能恢复为可用内容。
- Watch 保持 paused，用户或管理员必须手工恢复。
- 删除期间晚到的 Task 结果不能重新激活对象；Worker 提交结果时必须比较 Resource 状态和 Operation generation。

### 45.4 并发优先级

```text
删除/进入回收期 > Refresh/Watch/替换 > 元数据修改 > 读取
```

- 删除开始后拒绝新的写操作。
- Refresh 不阻塞读取上次成功版本。
- 元数据修改与删除使用 `version` 乐观锁。
- Operation 使用 `generation` 防止旧任务覆盖新任务结果。
- 所有写 API 支持 `Idempotency-Key`；重试不能创建重复 Resource、Watch 或删除任务。

## 46. Product API 契约

### 46.1 Capabilities 与 Upload

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/platform/v1/resources/capabilities` | 返回来源类型、格式、上传限制、Watch 是否启用及周期预设 |
| POST | `/api/platform/v1/me/resource-uploads` | 创建当前 User 私有作用域的临时 Upload |
| POST | `/api/platform/v1/account/resource-uploads` | Account Admin 创建共享作用域临时 Upload |
| POST | `/api/platform/v1/platform/accounts/{account_id}/resource-uploads` | Platform 为目标 Account 创建共享 Upload |

Upload ID 必须绑定 Actor、Account、目标可见性、文件名、大小、hash、过期时间和消费状态。不同 Scope 的导入 API 不能交叉消费。

### 46.2 私有 Resource API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/platform/v1/me/resources` | 私有列表、筛选、排序和 Cursor 分页 |
| POST | `/api/platform/v1/me/resources/imports` | 文件/网页/Git 导入，固定当前 User 私有目标 |
| GET | `/api/platform/v1/me/resources/{id}` | 私有 Resource 详情 |
| PATCH | `/api/platform/v1/me/resources/{id}` | 只改名称、说明和标签 |
| POST | `/api/platform/v1/me/resources/{id}/replace` | 上传来源替换文件 |
| POST | `/api/platform/v1/me/resources/{id}/refresh` | 稳定远程来源手动 Refresh |
| POST | `/api/platform/v1/me/resources/{id}/retry` | 重试首次失败的导入；上传或一次性 URL 必须重新提交来源 |
| POST | `/api/platform/v1/me/resources/{id}/publish` | 仅 Account Admin 发布自己的私有 Resource 为共享副本 |
| GET/PUT/DELETE | `/api/platform/v1/me/resources/{id}/watch` | 查询、配置或删除 Watch |
| POST | `/api/platform/v1/me/resources/{id}/watch/pause` | 暂停 Watch |
| POST | `/api/platform/v1/me/resources/{id}/watch/resume` | 恢复 Watch |
| POST | `/api/platform/v1/me/resources/{id}/watch/trigger` | 立即同步 |
| GET | `/api/platform/v1/me/resources/{id}/operations` | 当前 Resource Activity |
| POST | `/api/platform/v1/me/resources/{id}/operations/{operation_id}/cancel` | 取消可取消任务 |
| GET | `/api/platform/v1/me/resources/{id}/nodes` | Resource 内部只读节点分页 |
| GET | `/api/platform/v1/me/resources/{id}/nodes/{node_id}` | 节点元数据/安全预览 |
| GET | `/api/platform/v1/me/resources/{id}/nodes/{node_id}/download` | 单节点下载 |
| POST | `/api/platform/v1/me/resources/{id}/search` | 仅在当前 Resource 内检索 |
| GET | `/api/platform/v1/me/resources/{id}/deletion-preview` | 删除影响范围 |
| DELETE | `/api/platform/v1/me/resources/{id}` | 进入 30 天回收期 |

### 46.3 共享 Resource API

相同动作使用 `/api/platform/v1/account/resources/*`，读取对普通 User 开放，写入/Watch/删除只允许 Account Admin。Platform 代管使用 `/api/platform/v1/platform/accounts/{account_id}/resources/*`，不能通过 Header 改变目标 Account。

管理员只读查看成员私有 Resource 使用 `/api/platform/v1/admin/users/{user_id}/resources/{id}`；该路由不复用 `/me` 写接口，也不提供 Node Download，不返回原始 URI、远程 Query、内部 Operation ID 或可恢复 Secret。

### 46.4 主要 DTO

`ResourceSummary`：

```json
{
  "id": "res_...",
  "visibility": "user_private",
  "name": "产品需求文档",
  "description": "供产品讨论检索",
  "source_type": "upload",
  "source_display": "requirements.pdf",
  "tags": ["产品", "需求"],
  "lifecycle_status": "active",
  "processing": {
    "state": "succeeded",
    "stage": "succeeded",
    "latest_operation_id": "op_...",
    "last_succeeded_at": "2026-08-18T08:00:00Z"
  },
  "watch": {"state": "not_configured"},
  "version": 3,
  "created_at": "2026-08-18T07:50:00Z",
  "updated_at": "2026-08-18T08:00:00Z"
}
```

`ResourceDetail` 在 Summary 基础上增加 `overview`、脱敏来源信息、内容统计、Capabilities、当前 Operation 和 Watch 摘要；不增加 `ov_uri`、宿主机路径、原始 Watch Task ID、API Key 或 Connector Secret。

### 46.5 稳定错误码

```text
RESOURCE_NOT_FOUND
RESOURCE_BUSY
RESOURCE_VERSION_CONFLICT
RESOURCE_SOURCE_UNSUPPORTED
RESOURCE_SOURCE_NOT_STABLE
RESOURCE_SOURCE_BLOCKED
RESOURCE_UPLOAD_EXPIRED
RESOURCE_UPLOAD_ALREADY_CONSUMED
RESOURCE_FILE_TOO_LARGE
RESOURCE_FORMAT_UNSUPPORTED
RESOURCE_PARSE_FAILED
RESOURCE_INDEX_FAILED
RESOURCE_WATCH_UNAVAILABLE
RESOURCE_WATCH_CONFLICT
RESOURCE_OPERATION_NOT_CANCELLABLE
RESOURCE_DELETION_PENDING
RESOURCE_RESTORE_WINDOW_EXPIRED
RESOURCE_PUBLISH_FORBIDDEN
```

错误响应包含稳定 code、用户可理解 message、`request_id` 和可选 `retryable`，不包含远程响应正文、绝对路径、堆栈、Token 或底层 URI。

## 47. 授权、审计与安全

### 47.1 每个入口都执行相同授权

- Product API 根据 `/me`、`/account` 或 `/platform/accounts/{id}` 确定 DataAccessContext。
- 原生 REST、产品 MCP `add_resource/read/find/search/list_watches/cancel_watch/forget`、SDK 和 CLI 使用同一 Content Registry 与 Target Policy。源码已有的 `grep/glob` 在 v0.1 不向产品凭证发布，只留私网 Studio。
- MCP/SDK 未指定目标时固定 User 私有；普通 User 显式提交共享 URI 返回 403。
- Resource Node ID 只能在父 Resource 内解析，防止用另一个节点 ID 读取跨对象数据。
- 看不见的 Resource 返回 404；看得见但没有写权限的共享 Resource 写操作返回 403。

### 47.2 必须审计的事件

- 导入请求、成功、失败、取消和重试。
- 元数据修改、替换来源、Refresh。
- Watch 创建、修改、暂停、恢复、触发和删除。
- 私有发布为共享。
- 删除预览、删除、恢复和期满物理清理。
- 管理员/平台跨用户读取和下载。
- 权限拒绝、来源安全拒绝和 Upload 重放。

审计记录 Actor、Subject Account/User、Resource ID、Visibility、Action、Authentication Method、Credential ID、Request ID 和结果；不记录文件正文、完整来源 Query、处理要求中的敏感内容或上传文件 hash 全值。

### 47.3 其他安全约束

- 文件名、页面标题、Git 仓库名和生成摘要都是不可信文本，输出统一转义。
- 标签和说明不能插入 HTML；Markdown 渲染使用白名单 Sanitizer。
- 上传接口做速率、并发、总大小和 Account 配额限制。
- 远程来源使用应用层 Envelope Encryption，密钥来自 Secret Manager 并记录 key version；明文不能进入 Outbox、QueueFS、Watch JSON、日志或审计。
- 远程抓取与 Git clone 使用隔离 Worker、临时目录、超时和磁盘配额。
- 解压缩文件若未来支持，必须防 Zip Slip、压缩炸弹、符号链接逃逸；v0.1 产品上传默认不接受归档包。
- 普通业务指标不以 Resource ID、文件名、URL、User ID 作为无限基数 Label。

## 48. 页面与契约验收标准

1. 普通 User 无法在任何页面或请求中把新增目标改为 Account 共享区。
2. 列表、详情、Search、Session 引用、MCP 和 SDK 对同一身份产生一致的 Resource 可见范围。
3. 页面和 Product API 均不返回 Viking URI、宿主机路径、原始 Watch Task ID 或内部控制文件。
4. 本地文件、网页和 Git 的成功、失败、取消、重试状态可由 Operation 完整表达。
5. 普通共享读者看不到尚未成功的共享导入；Refresh 失败时仍可读取上一次成功版本。
6. 上传文件不能开启 Watch；稳定远程来源可以暂停、恢复和手动触发。
7. Account Admin 不能发布、修改、删除或配置 Watch 于其他 User 的私有 Resource。
8. 私有发布为共享生成新 ID，原对象保留，Watch 和私有关系不被复制。
9. 删除立即停止新同步并进入 30 天回收期；恢复不自动恢复 Watch。
10. 处理结果晚到时不能重新激活已删除对象，也不能覆盖更新一代的 Resource。
11. 预览不能执行来源脚本，下载不能通过文件名或 Content-Type 注入响应头。
12. SSRF、私网 URL、服务器路径、Upload ID 重放、跨 Scope Upload 消费和节点 ID 越权均被拒绝并审计。

## 49. 本轮收敛结论

Resource 页面不复刻 Studio 的文件管理器。它由四个用户可理解的概念构成：

1. 资料属于“我的”还是“Account 共享”。
2. 资料来自文件、网页还是 Git。
3. 资料当前是否可用，最近一次处理是否成功。
4. 稳定远程资料是否需要自动同步。

底层 URI、目录创建、解析模式、原始 Task、原始 Watch、Content 写入和 Reindex 都由系统内部处理。Skill 的私有/共享、创建上传、名称、发布、文件结构和调用边界见 [Skill 页面与产品契约](10-skill-product-contract.md)。
