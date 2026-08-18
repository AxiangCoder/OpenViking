# 08 产品能力归属设计

> Design v0.1 · 源码能力盘点与边界设计<br>
> 上游源码基线：OpenViking v0.4.12<br>
> 本文讨论“能力属于哪一层、由谁负责、从哪里提供”，不替代 03 号文档中的 RBAC 权限设计。

## 24. 设计目标

OpenViking v0.4.12 的源码已经具备记忆、Resource、Skill、Session、检索、文件系统、任务、快照、备份、MCP 和 Studio 等能力，但这些能力在源码层面以 Router、Service 和底层存储接口混合呈现。产品化不能简单地把所有 `/api/v1/*` 路由都变成页面或产品 API。

本设计建立一套产品能力归属模型，解决以下问题：

1. 一项能力属于 Platform、Account、User，还是只属于系统内部。
2. 一项能力操作的数据属于 User、Account，还是系统运行状态。
3. 谁负责控制和维护这项能力，谁只是使用它。
4. 这项能力应该出现在 `/app`、`/admin`、`/platform`、集成接口，还是只保留在私网 Studio。
5. 源码中的低层 API 如何映射为稳定的产品 Facade，而不是形成权限旁路。

本设计的结论是：**产品能力归属、数据归属、权限和调用渠道是四个不同维度，必须分开建模。**

每项源码能力只能进入以下一种 v0.1 状态，不能使用含糊的“系统已支持”表述：

| 状态 | 含义 | 是否有正式产品闭环 |
| --- | --- | --- |
| 正式产品能力 | 有产品页面、Product API、Permission、审计和数据范围规则 | 有 |
| 正式集成能力 | 不一定有日常菜单，但 MCP/SDK/CLI 等对外入口受统一 IAM/RBAC 管理 | 有集成闭环 |
| 私网运维能力 | 只给部署/运维人员排障或维护，不属于三个产品角色 | 无产品业务闭环 |
| v0.1 禁用 | 当前入口会造成旁路或尚无业务需求，生产不挂载 | 无 |

## 25. 术语纠正与专业模型

“产品能力归属设计”是可以使用的工作称呼。更精确的专业名称是 **产品能力域与责任边界设计**（Capability Taxonomy and Responsibility Boundary Design），其中“归属”至少包含下面四种含义：

| 维度 | 专业术语 | 专业解释 | 大白话解释 |
| --- | --- | --- | --- |
| 能力属于哪一层 | Capability Domain / Ownership Layer | 能力在产品架构中的业务层级 | 这项功能是平台的、团队的、个人的，还是系统自己的 |
| 数据属于谁 | Data Ownership | 被能力操作的数据主体和生命周期归属 | 这份内容到底算谁的 |
| 谁负责控制 | Control Responsibility | 负责配置、维护、发布和撤销能力或对象的主体 | 谁能管它、改它、关掉它 |
| 从哪里使用 | Product Surface / Delivery Channel | 能力对用户暴露的产品页面或技术入口 | 用户在哪个页面或接口用它 |

这四者不能互相替代。例如：

- Account 共享 Resource 的**数据归属**是 Account；Account Admin 负责维护；普通 User 只能读取和引用；Platform Super Admin 可以按平台权限代管。不能因为普通 User 能读取，就说 Resource 归 User。
- Search 的**能力归属**是上下文引擎/产品能力层；它检索的**数据归属**可以是当前 User 私有数据和当前 Account 共享数据。不能把 Search 简化成“Account 功能”或“User 功能”。
- User API Key 的**凭证归属**是 User；密钥的签发、撤销和审计由 Platform IAM 控制；使用该 Key 的 MCP 或插件不是新的数据所有者。

因此，本文中的“归属”默认指 Capability Domain；涉及数据时明确写成 Data Ownership。

## 26. 能力归属模型

### 26.1 五类能力层

| 能力层 | 责任范围 | 典型能力 | 是否是业务数据所有者 |
| --- | --- | --- | --- |
| Platform Control Plane | 全局身份、Account 生命周期、角色权限、审计、跨 Account 管理和平台策略 | 登录、IAM、Provisioning、全局审计、平台运维 | 是平台治理记录的所有者；不是普通 Account 业务数据的语义所有者 |
| Account Collaboration Plane | 一个 Account 内的成员协作和 Account 共享对象 | 共享 Resource、共享 Skill、成员管理、Account 级视图 | 是 Account 共享对象的业务所有者 |
| User Experience Plane | 单个 User 的个人上下文和个人工作空间 | Memory、对话 Session、Peer、私有 Resource、私有 Skill、个人凭证 | 是 User 私有业务数据和个人凭证的业务所有者 |
| Engine Capability Plane | 为上面三层提供存储、解析、检索、版本和关系能力 | VikingFS、VectorDB、Content、Search、Relations、Snapshot | 通常不是业务数据所有者；它是能力提供者 |
| System Operations Plane | 异步执行、调度、健康检查、指标、备份和内部状态 | QueueFS、Task、Watch Scheduler、Metrics、Observer、Worker | 只拥有系统运行状态，不拥有 User/Account 的业务内容 |

### 26.2 集成入口不是归属层

REST `/api/v1`、Platform API、MCP、SDK、CLI、OAuth、WebDAV、Bot 和 Studio 是 **Delivery Channel / 交付渠道**，不是独立的业务归属层。

它们只回答“通过什么方式调用”，不回答“数据属于谁”。除网页登录使用服务端 Session 外，SDK、CLI、MCP 和插件使用用户 API Key 或用户 OAuth Token 时，都代表同一个 User，继承该 User 当前的 RBAC 和数据范围。渠道不能通过请求参数、Header、URI 或不同的 Router 获得更高权限。

### 26.3 能力、数据、控制和执行关系

```text
调用渠道（Browser / SDK / CLI / MCP / Plugin）
                         |
                         v
              Principal Resolver + RBAC
                         |
                         v
             Product Facade + Target Policy
               /            |             \
              v             v              v
       User 私有数据   Account 共享数据   Platform 控制面
              \             |              /
               \            v             /
                 OpenViking Engine
                         |
                         v
                System Worker / Storage
```

调用渠道只提供 Actor；Product Facade 决定 Subject、数据可见性和业务动作；OpenViking Engine 执行受控的存储、检索或处理；System Worker 执行异步任务。任何一层都不能因为自己“能调用底层 Service”就自动获得更大的业务归属。

## 27. 产品能力域总表

下表是 v0.1 的产品能力目录。`控制责任`表示谁负责管理，不等于每个相关角色都拥有全部操作权限；具体允许的动作仍以 03 号文档的 Permission 为准。

| 能力域 | 能力归属层 | 主要数据归属 | 控制责任 | 正式产品入口 | v0.1 定位 |
| --- | --- | --- | --- | --- | --- |
| 身份与登录 | Platform | Platform IAM、User 登录状态 | Platform Super Admin / IAM | `/login`、`/api/platform/v1/auth/*` | 正式产品能力 |
| Account 生命周期 | Platform | Account 元数据与 Provisioning 状态 | Platform Super Admin | `/platform`、Platform API | 正式产品能力 |
| 成员与角色 | Platform + Account | User、Role、Permission 关系 | Platform Super Admin 管全局；Account Admin 管本 Account 普通 User | `/admin`、`/platform` | 正式产品能力 |
| 个人上下文与记忆 | User Experience | User 私有 Memory、Peer、Privacy | User；管理员按数据范围读取 | `/app/search`、Session Memory Impact、用户集成入口 | 正式产品能力；不设独立 Memory 页面或 CRUD |
| 对话 Session 与上下文提交 | User Experience | User 私有 OpenViking Session | User；系统 Worker 执行异步提交 | `/app`、MCP、SDK/CLI | 正式产品能力 |
| User 私有 Resource | User Experience | `viking://user/{ov_user_id}/resources/**` | User；Account Admin/Platform 按数据范围读取，修改/删除需独立 Permission | `/app`、用户集成入口 | 正式产品能力 |
| Account 共享 Resource | Account Collaboration | `viking://resources/**` | Account Admin；Platform Super Admin 可平台代管 | `/app` 读取/引用、`/admin` 管理、`/platform` 代管 | 正式产品能力 |
| User 私有 Skill | User Experience | `viking://user/{ov_user_id}/skills/**` | User 管理自己的；Account Admin 可读并发布本 Account 任意 User 的；Platform 只读 | `/app`、`/admin` 成员只读/发布、`/platform` 只读 | 正式产品能力 |
| Account 共享 Skill | Account Collaboration | `viking://agent/skills/**` | Account Admin 管理；Platform Super Admin 只读 | `/app` 读取/使用、`/admin` 管理、`/platform` 只读 | 正式产品能力 |
| 检索与召回 | Engine Capability + Product Facade | 当前 User 私有区 + 当前 Account 共享区 | Product Facade 根据 Actor/Subject 控制范围 | `/app`、Platform API、MCP、SDK/CLI | 正式产品能力；不暴露任意根目录 |
| Resource 导入与同步 | Account/User 按目标归属 | 默认 User 私有；显式共享目标为 Account | User 管理私有导入；Account Admin 管理共享导入；Worker 执行同步 | `/app`、用户集成入口、受控任务状态 | 正式产品能力；共享目标必须显式授权 |
| 个人 API Key | User + Platform IAM | User 凭证元数据和密钥哈希 | User 自主管理；Platform IAM 签发、撤销、审计 | `/app/profile/api-keys`、Platform API | 正式产品能力 |
| Skill 私密配置 | User Experience | `viking://user/{ov_user_id}/privacy/**` | 只能由配置所属 User 管理；管理员不能读取 Secret | Skill 详情配置区 | 正式产品能力的受控子功能 |
| 关系与图谱 | Engine Capability | 关系两端对象的原有归属 | Product Facade 按两端对象权限控制 | 作为 Search/Session/Context 的内部增强 | v0.1 不设菜单，不开放原始写接口 |
| 异步任务 | System Operations | 系统任务记录，引用 User/Account 目标 | Worker 执行；查看/取消继承任务与目标对象权限 | `/app/activity`、受控管理视图 | 正式产品能力的状态子系统 |
| Resource Watch | Account/User 按目标归属 | Watch 控制记录引用目标 Resource | Resource 管理者控制，Worker 调度 | Resource 详情页，不设独立顶级菜单 | 正式产品能力的受控子功能 |
| Snapshot、Pack、Backup、Import、Restore | Engine/System Operations | 可能覆盖整个 User/Account/实例 | 平台运维 | 私网 Studio/运维工具 | v0.1 私网运维能力 |
| 产品健康摘要 | Platform/Account | 聚合状态，不含底层路径和 Secret | Platform 运维；Account Admin 只看当前 Account 摘要 | `/admin/monitoring`、`/platform/monitoring` | 正式产品能力 |
| 原始观测、指标与系统修复 | System Operations | Queue、锁、模型、VectorDB、文件系统和实例状态 | 平台运维 | 监控系统、私网 `/studio` | v0.1 私网运维能力 |
| 低层文件系统与 Content API | Engine Capability | 由目标 URI 决定 | Product Facade / URI Policy；不由原始 Router 自行决定 | 内部 Service；必要时由产品 Facade 暴露 | 非独立产品能力 |
| MCP、SDK、CLI、插件和 MCP OAuth | Delivery Channel | 继承授权 User 和目标对象归属 | Platform 统一认证授权 | 集成入口、`/oauth/consent`、`/oauth/verify` | v0.1 正式集成能力 |
| Bot/Agent Chat | 外部 Delivery Channel | User 私有 Session | 各客户端以 User 凭证接入 | Codex、其他 Agent、插件/MCP、可选 VikingBot | v0.1 不提供网页 Chat；VikingBot 非必选 |
| WebDAV | Delivery Channel | 当前源码直接映射 Account 共享 Resource | 无合规的产品控制面 | 不挂载 | v0.1 禁用 |
| Studio | System Operations | 可能触达实例级底层状态 | 运维/开发 | `/studio`，仅开发或私网 | 可选维护入口，不是正式产品能力 |

## 28. OpenViking v0.4.12 源码能力映射

### 28.1 正式产品能力

| 源码模块/Router | 源码能力 | 产品化归属 | 产品化处理 |
| --- | --- | --- | --- |
| `server/routers/admin.py` | Account、User、Role、API Key 管理；旧迁移与 Agent Evolution | Platform/Account Control Plane | IAM 管理迁移为新的 Platform API；Provisioning 复用受控 Service；旧 Key 生成、`migrate`、Agent Evolution 不进入产品 UI/API |
| `server/routers/resources.py` | Resource/Skill 导入、临时上传、等待处理、Watch 参数 | Account/User Content Plane | 经 Content Registry 和 Target Policy；未指定目标的 Resource 强制进入 User 私有区；v0.1 产品页只开放文件、公开 HTTPS 页面和公开 HTTPS Git |
| `server/routers/skills.py` | Skill 列表、查找、校验、读取、更新、删除 | User/Account Collaboration Plane | 按 `user_private` 与 `account_shared` 分流；共享 Skill 普通 User 只能读取和使用 |
| `server/routers/sessions.py` | 创建 Session、消息、Tool Result、Context、Commit、Extract | User Experience Plane | 作为用户对话、归档、上下文和 Memory 提取能力；Session 归 User，不等同于登录 Session；不由该 Router 生成 AI 回复 |
| `server/routers/search.py` | `find`、`search`、`recall`、`grep`、`glob` | Engine + Product Facade | `/app/search` 只提供 `find/search` 两种模式及类型、标签、时间筛选，并固定检索根与执行参数；`recall` 供受控调用链；`grep/glob` 只留私网 Studio |
| `server/routers/relations.py` | 关系查询、链接、解除链接、构建图 | Engine + Product Facade | 关系两端的可见性和写权限都要分别检查；v0.1 不直接暴露任意 URI 图操作 |
| `server/routers/privacy_configs.py` | 隐私配置版本、激活和读取 | User Experience | 作为用户敏感配置的受控子能力；原始类别和 target key 不直接开放给前端 |
| `server/routers/stats.py` | Memory、Session 统计 | User/Account/Platform 视图 | 产品 API 根据 Actor/Subject 提供个人、Account 或平台聚合视图，不直接复用无范围统计接口 |

### 28.2 引擎和运维能力

| 源码模块/Router | 源码能力 | 能力归属 | v0.1 边界 |
| --- | --- | --- | --- |
| `server/routers/filesystem.py` | `ls`、`tree`、`stat`、`mkdir`、`rm`、`mv`、标签 | Engine Capability | 作为底层实现；产品 API 不接受任意 URI，所有写操作经过 Target Policy |
| `server/routers/content.py` | read、abstract、overview、download、write、batch-write、reindex | Engine Capability | 读写由产品对象和 URI 分类器包装；`reindex` 为系统/运维动作 |
| `server/routers/snapshot.py` | commit、log、restore、diff、ignore | Engine/System Operations | v0.1 只留私网运维；不进入 `/app`、`/admin`、`/platform` 或普通集成凭证 |
| `server/routers/pack.py` | export、backup、import、restore | System Operations | v0.1 只留私网运维；产品中的单对象下载/导出另由 Product Facade 实现，不复用整包 Restore |
| `server/routers/tasks.py` | 查询和取消处理任务 | System Operations | 经 `platform_operation_refs` 暴露受控状态；取消同时检查 Task Permission、可取消状态和目标对象写权限 |
| `server/routers/watches.py` | Watch 创建、修改、触发、删除 | System Operations | 封装为 Resource 详情子功能；读继承 Resource read，管理/触发继承 Resource write，不开放任意 task ID |
| `server/routers/user_settings.py` | Resource 默认添加位置 | User/System Configuration | v0.1 不给用户修改默认根；Product Facade 固定普通 User 默认私有、共享管理入口固定 Account 共享 |
| `server/routers/observer.py`、`server/routers/metrics.py`、`server/routers/debug.py`、`server/routers/system.py` | 队列、VectorDB、模型、锁、健康、同步和内部指标 | Platform/System Operations | 仅平台运维或私网维护；不进入普通产品 API |
| `server/routers/openviking_assets.py` | 模型/资源解析和预检 | Platform/System Operations | 部署和运维能力，不作为 User/Account 内容能力 |
| `server/routers/console.py` | Dashboard、Token、Context Commit、Audit 视图 | Platform/System Operations | 业务摘要重建为产品 Dashboard/Monitoring；原始请求 Audit 和底层明细留私网，不替代 Platform Audit Event |

### 28.3 集成与兼容入口

| 源码入口 | 归属判断 | 处理规则 |
| --- | --- | --- |
| `server/mcp_endpoint.py`、`server/oauth/` | 用户委托型集成 | MCP OAuth/Token 最终解析为 User Principal；授权页迁出 Studio，使用产品登录 Session；与 REST 共用 Principal Resolver、RBAC 和 URI Policy |
| `examples/*-plugin/`、SDK、CLI | 用户委托型集成 | 使用谁的 User API Key，就代表谁；不生成新的业务身份 |
| `server/routers/webdav.py` | 外部兼容渠道 | 当前固定映射到 `viking://resources` 且支持写/删/移动；v0.1 生产不挂载，后续如启用必须先另做读写和客户端身份设计 |
| `server/routers/bot.py` | 可选 Bot/Agent 集成渠道 | 不进入产品网页；VikingBot 若部署，按普通接入客户端解析当前 User，Compile/Health 为内部能力，不得获得 Root 特权 |
| `web-studio/` 与 `/studio` | 运维维护渠道 | 不进入正式产品导航；生产公网默认不挂载，开发/私网可用于底层排障 |

### 28.4 Studio 当前页面如何拆分

现有 Studio 不是单纯的“管理后台”，而是把用户工作台、集成连接、实验工具和实例运维混在一个 SPA 中。v0.1 不复制它的信息架构，而按能力归属拆分：

| Studio 当前路由 | 当前主要能力 | v0.1 正式去向 | 只留私网 Studio 的部分 |
| --- | --- | --- | --- |
| `/studio/home` | Dashboard、Token、Context Commit 摘要 | `/app` 个人摘要；`/admin/monitoring` Account 摘要；`/platform/monitoring` 平台摘要 | 原始 Token/Commit 调试明细 |
| `/studio/playground` | VikingFS 浏览、内容编辑、Resource 导入、终端、Agent Chat | Resource 详情/导入与 `/app/sessions` Chat | 任意 URI 浏览、原始命令终端、底层 Session 命令 |
| `/studio/retrieval` | find/search/grep/glob、结果列表/抽屉与原始范围过滤 | `/app/search` 复用结果列表/抽屉及快速检索、结合会话检索 | grep/glob、任意根 URI、分数/层级/检索计划/来源追踪和原始结果结构 |
| `/studio/skills` | 私有/共享 Skill 浏览 | `/app/skills`、`/admin/shared-skills`、平台只读页 | 原始文件结构和引擎调试信息 |
| `/studio/sessions` | Session 列表、Chat、删除、Context/Archive | `/app/sessions`；管理员按 Subject 只读查看 | extract、tool-result 原始调试和任意底层操作 |
| `/studio/tasks` | 所有 QueueFS Task | `/app/activity` 及受控管理摘要 | 系统任务、迁移、恢复、清理和原始错误堆栈 |
| `/studio/request-logs` | HTTP Request Audit | Platform Audit 页面使用新的业务审计模型 | 原始请求日志 |
| `/studio/monitoring` | Queue、VectorDB、模型、锁、检索、文件系统、系统 | 产品只提供脱敏业务健康摘要 | 全部组件级调试和修复入口 |
| `/studio/settings` | Base URL、Root/User API Key 连接 | `/app/profile/api-keys`、`/app/profile/connections` | Studio 自身连接设置和 Root Key |
| `/studio/users` | 旧 Account/User/Role/Key 管理及身份切换 | `/admin/users`、`/platform/accounts`，全部改用新 IAM | 旧 Key 生成、Root/Account Switcher 和旧门禁操作 |
| `/studio/oauth/consent` | MCP 同设备授权（Studio 内路由为 `/oauth/consent`，挂载 `/studio` 后为 `/studio/oauth/consent`） | `/oauth/consent`，使用产品登录 Session | 无 |
| `/studio/oauth/verify` | MCP 跨设备验证码授权（Studio 内路由为 `/oauth/verify`，挂载 `/studio` 后为 `/studio/oauth/verify`） | `/oauth/verify`，使用产品登录 Session | 无 |

这里的“拆分”不是把 Studio 页面换一个 URL，而是重新设计产品 DTO、Permission 和交互。比如 `/studio/playground` 的终端能执行原始 `write/mv/delete/session extract`，因此不能整体搬到 `/app`。

### 28.5 MCP 13 个 Tool 的 v0.1 映射

v0.4.12 的 MCP 暴露 13 个 Tool。Tool 名属于协议入口，最终权限仍由动作和目标对象决定：

| MCP Tool | v0.1 产品语义 | 数据范围/权限规则 |
| --- | --- | --- |
| `find` | 目录式快速发现 | 只返回当前 User 私有区与当前 Account 可读共享区；逐项过滤 |
| `search` | 语义检索 | 同上，客户端不能扩大检索根 |
| `recall` | 面向对话的记忆召回 | 只召回当前 User Memory 与有权读取/使用的上下文 |
| `read` | 读取已知对象 | 每个 URI 规范化后检查对象 read Permission |
| `list` | 列出受控目录 | 只允许产品白名单根；不列出 internal/temp/queue/privacy 原始目录 |
| `remember` | 写入个人长期 Memory | 固定当前 User 私有 Memory，不接受 Account/User 切换 |
| `add_resource` | 导入 Resource | 未指定目标时固定 User 私有；Account 共享目标只允许 Account Admin/Platform 对应权限 |
| `list_watches` | 查看 Resource 自动同步 | 只返回调用者有权读取的目标 Resource Watch |
| `cancel_watch` | 停止 Resource 自动同步 | 目标 Resource 必须可写；停止同步不删除 Resource |
| `grep` | 源码已有的全文/模式检索 | v0.1 不向产品 MCP 凭证发布，只留私网 Studio |
| `glob` | 源码已有的路径匹配 | v0.1 不向产品 MCP 凭证发布，只留私网 Studio |
| `forget` | 删除对象 | 名称保留，行为改为 30 天软删除；需要目标 delete Permission，不能直接物理删除 |
| `health` | MCP 连接健康 | 不返回 Queue、模型、路径或租户数据；调用仍需有效集成凭证 |

MCP 当前没有 Account/User/Role/API Key 管理 Tool，也没有 `add_skill` Tool。不能因为 REST/SDK 有相应能力，就在产品设计中宣称 MCP 已支持这些动作。

### 28.6 SDK/CLI 与原生 REST 的边界

- SDK/CLI 可以继续覆盖 Resource、Skill、Session、Search 和 Task 等已纳入能力，但必须使用 User API Key/OAuth 并经过同一 Product Registry 与 Target Policy。
- 原生 REST 的 `fs/content/relations/snapshot/pack/system/debug/observer` 是低层或运维接口，不因 SDK 有方法就自动成为正式集成契约。
- v0.1 对外契约允许“按能力选接口”，不要求公开全部 `/api/v1/*`。未列入正式产品/集成能力的 Router 在公网反向代理和应用挂载层都默认关闭。
- `content.download` 可作为有权限对象的受控下载实现；任意 `content.write/batch-write/reindex` 不直接开放。
- Relations 的读取结果可以被 Search/Session/Context 使用；`link/unlink/build_graph` 只供内部引擎，v0.1 不对普通凭证开放。

## 29. 正式产品入口的能力边界

### 29.1 `/app`：User Experience Plane

`/app` 面向 User，承载：

- 我的 Memory、Peer、对话 Session、检索和上下文使用。
- 我的 User 私有 Resource 与 Skill 的创建、更新、删除和使用。
- 当前 Account 共享 Resource 的查看、检索和引用。
- 当前 Account 共享 Skill 的查看和使用。
- 个人资料、密码、登录会话和 User API Key。
- 已授权 MCP 客户端、与个人对象相关的任务状态、回收站和恢复操作。
- Resource 详情中的自动同步设置与执行状态。

`/app` 不允许用户通过输入 URI、Account ID、User ID、`visibility` 或 `target_uri` 来任意切换数据归属。共享内容的创建和维护入口应明确显示“Account 共享”，并只对拥有相应 Permission 的角色显示。

### 29.2 `/admin`：Account Collaboration Plane

`/admin` 面向 Account Admin，承载：

- 本 Account 普通 User 的直接创建、禁用、删除、恢复和严格低级别密码重置。
- 本 Account 共享 Resource/Skill 的发布、更新、删除和恢复。
- 本 Account 用户和共享对象的数据视图，以及受控审计视图。
- Account 共享对象的处理任务、自动同步和业务健康摘要。
- 三个内置角色和权限的只读说明。

Account Admin 负责本 Account 的日常协作内容，但不是其他 Account 的管理员，也不因为能读取用户数据就自动获得修改、导出或删除他人私有数据的权限。

### 29.3 `/platform`：Platform Control Plane

`/platform` 仅面向 Platform Super Admin，承载：

- Account 创建、暂停、恢复、删除和 Provisioning 重试。
- 首位 Account Admin 创建、Account Admin 生命周期管理和全局用户管理。
- 跨 Account 的受控数据查看、平台审计和平台运维状态。
- Platform 级权限、Provisioning、回收和异步清理状态；实例级修复仍由私网运维面执行。

Platform Super Admin 在选择目标 Account 时只是指定 Subject，不改变自己的 Actor，也不提供普通用户意义上的 Account 切换。平台角色可以代管 Account 数据，但不因此成为 Account 共享内容的业务创建者。

### 29.4 `/api/v1`、MCP、SDK/CLI 和插件

这些入口的定位是集成交付，不是另一套产品界面。它们必须满足：

1. 解析出的 Principal 与网页登录的 User 相同。
2. 调用动作先经过统一授权，再调用 OpenViking Service。
3. Product API、低层 REST、MCP、WebDAV 和 Bot 的目标 URI 都经过同一个归属分类器。
4. 未指定 Resource 目标时，默认写入当前 User 私有区；不能沿用源码默认的 Account 共享 Resource 根。
5. 任何入口都不能通过 `mv`、`write`、`mkdir`、`set_tags`、批量写入或导入动作绕过共享区只读策略。
6. v0.1 生产不挂载 WebDAV、Snapshot、Pack、Debug、Observer 和原始系统修复 Router。

### 29.5 无导航但属于产品闭环的页面

`/oauth/consent`、`/oauth/verify` 属于 MCP OAuth 的用户授权表面。它们不是 `/studio`、不是 OIDC 登录、也不是日常菜单，但必须随 `web-platform` 对产品用户可达：

- 同设备授权用 `pending_id` 定位待批准请求；跨设备授权由用户输入短期 display code。
- 未登录先进入本地邮箱密码登录，再安全返回授权页。
- 批准身份来自 HttpOnly 登录 Session，提交使用 CSRF；页面不要求或保存 User API Key。
- 授权页展示 Client、回调域名、Scope、当前 Account 及有效数据范围，用户可以允许或拒绝。
- `/app/profile/connections` 提供授权后的查看和撤销闭环。

这类页面专业上叫 **transient product surface（瞬时产品界面）**：只在某个流程中出现，不占导航，但仍是正式产品的一部分。大白话说，它不是平时点菜单进去的页面，却不能少，否则 MCP 授权流程走不通。

## 30. 数据归属不变量

以下规则是能力归属设计与 01、02、03、04、05 号文档之间的连接点：

1. `viking://user/{ov_user_id}/memories/**`、`sessions/**`、`peers/**`、`privacy/**`、User 私有 Resource 和 User 私有 Skill 都归 User。
2. `viking://resources/**` 的 Account 共享 Resource 归当前 Account，不归创建者；`created_by_actor_user_id` 只做审计。
3. `viking://agent/skills/**` 的 Account 共享 Skill 归当前 Account，不归创建者。
4. Account 共享 Resource 的管理权来自 Account/Platform Permission；Account 共享 Skill 只由 Account Admin 管理，Platform Super Admin 只读。普通 User 的读取和使用权不改变数据归属。
5. `temp`、`upload`、`queue`、内部锁、任务状态、VectorDB 索引和 Worker 中间产物属于 System Operations Plane，不通过业务 API 作为 User/Account 内容返回。
6. API Key 的 secret 归 User 使用，但密钥生命周期归 Platform IAM 控制；审计记录必须同时保留 Actor 和 Subject。
7. Watch、异步 Task 和索引 Job 由 System Worker 执行，任务的业务控制范围继承被监控或被处理对象的归属。
8. Resource 从 User 私有区发布到 Account 共享区是显式复制新建，产生新的产品对象；Skill 发布是例外，由 Account Admin 把本 Account 任意 User 私有 Skill 原地转换为共享，保持 ID/名称，不保留副本。两者都必须独立授权和审计。
9. “共享”只表示当前 Account 内共享，不表示互联网公开、跨 Account 共享或没有权限控制。
10. Platform Super Admin 可以按平台权限访问 Account 数据，但平台控制权不等于业务数据所有权；对 Skill 明确只有跨 Account 读取权限，不得创建、修改、发布、删除、恢复或使用。
11. Skill 私密配置始终归配置它的 User；共享 Skill 不会让该 User 的 Secret 变成 Account 共享数据，Account Admin/Platform Super Admin 的内容读取权也不能读取 Secret 明文。
12. MCP OAuth Grant 归授权 User；OAuth Client 只是软件标识，不是 Principal。授权页使用登录 Session 识别人，Access Token 调用时仍以该 User 为 Actor。

## 31. 模块责任边界

产品化后的推荐依赖关系如下：

| 模块 | 应负责 | 不应负责 |
| --- | --- | --- |
| `web-platform` | 页面展示、路由守卫、用户操作确认、按 Permission 隐藏不适用入口 | 最终鉴权、数据归属判断、直接拼接任意 Viking URI |
| `Platform API / IAM` | 登录、Principal、RBAC、Account/User 生命周期、审计、产品 DTO | 直接把底层文件系统当作前端数据模型 |
| `ProductFacadeService` | 产品 ID 到 URI 的映射、目标归属分类、Actor/Subject、软删除和业务编排 | 自己保存 OpenViking 正文副本、绕过 OpenViking Service |
| `TargetPolicy / AuthorizationService` | 动作、可见性、Scope 和低层入口统一授权 | 依赖前端传入的 `visibility` 或 `account_id` |
| `OpenVikingService` | Memory、Resource、Skill、Session、Search 等引擎能力执行 | 决定产品角色、网页入口或管理员业务流程 |
| `VikingFS / VectorDB / QueueFS` | 文件、向量、队列和异步处理的运行时实现 | 作为产品 IAM 的身份事实来源 |
| `System Worker` | Provisioning、索引、清理、Watch、备份等异步任务 | 冒充 User、持有对外 API Key 或伪造 Actor |
| `/studio` | 开发、排障、底层维护 | 承载正式产品功能、替代产品 RBAC |

## 32. v0.1 纳入与排除

### 32.1 v0.1 必须有正式产品承载的能力

- 邮箱密码登录、登录 Session、退出和当前用户信息。
- Account、User、角色和 Permission 的产品化管理。
- User 私有 Memory、Session、Resource、Skill、检索和个人设置。
- Account 共享 Resource/Skill 的读取、引用和管理员维护。
- User API Key 的创建、一次性展示、元数据查看和撤销。
- MCP OAuth 同设备/跨设备授权、已授权客户端查看与撤销；授权流程不依赖 Studio 或浏览器内 API Key。
- Resource 详情中的 Watch/自动同步，以及 `/app/activity` 中受控的 Task 状态和取消。
- Account/User/数据的审计、软删除、恢复和 Provisioning 状态。
- REST、MCP、SDK/CLI 和插件统一走同一 Principal 与授权链。

### 32.2 v0.1 不作为普通用户独立产品菜单的能力

- 任意 URI 的文件系统浏览、移动、批量写入和删除。
- 任意根目录的 Content `write`、`batch-write` 和 `reindex`。
- Snapshot、Pack、Backup、Import、Restore 等实例级高风险操作。
- Observer、Metrics、Debug、System、VectorDB 和锁状态。
- 直接操作任务存储、Watch 控制文件或内部队列；产品只操作与目标 Resource 绑定的受控 Facade。
- Root、Service Account、Account 级共享机器身份和旧凭证门禁。
- WebDAV 共享根读写入口。

这些能力不是“永远不存在”，而是必须先经过独立的产品封装、影响范围确认、审计和数据范围设计，不能因为源码已经有接口就默认成为产品能力。

## 33. 设计验收标准

满足以下条件，才认为“产品能力归属”落地正确：

1. 每项对外产品能力都有明确的能力归属层、数据归属、控制责任和产品入口。
2. Product API、低层 REST、MCP、SDK/CLI、WebDAV 和 Bot 没有各自独立的归属规则。
3. 普通 User 新增 Resource 默认落入 User 私有区；共享写入必须从明确的 Account 共享动作进入。
4. Account 共享 Resource/Skill 不因创建者删除、禁用或离开而改变归属。
5. 管理员访问他人数据时，Actor、Subject、Scope 和目标对象在审计中同时存在。
6. Search、Relations、Watch、Task 等跨对象能力，均按目标对象的归属和权限继承规则处理。
7. `/app`、`/admin`、`/platform` 的导航和 API 边界与能力归属矩阵一致；`/studio` 不承载正式业务闭环。
8. 任意低层写操作都不能绕过 Product Facade、Target Policy 和统一授权门。
9. 文档中不使用“公共”代指 Account 共享，不使用“切换 Account”代指 Platform Super Admin 选择 Subject。
10. 对尚未完成产品封装的引擎/运维能力，明确标为“内部、管理端或后续设计”，不能模糊标记为“已支持”。
11. `/studio` 不挂载时，MCP OAuth 的同设备和跨设备授权仍能由 `/oauth/consent`、`/oauth/verify` 完整完成，且浏览器不需要 User API Key。
12. MCP `forget`、原生删除接口和产品删除接口都遵守 30 天软删除；WebDAV、Snapshot、Pack 和系统修复入口不能成为物理删除旁路。

## 34. 与现有设计文档的关系

| 文档 | 本文的关系 |
| --- | --- |
| `01-product-positioning-and-baseline.md` | 定义产品定位、正式入口、源码基线和 URI 数据可见性；本文补充能力域及源码 Router 的归属映射 |
| `02-architecture-and-identity.md` | 定义 Actor、Subject 和 RequestContext；本文把这些身份上下文应用到能力入口和数据归属 |
| `03-authentication-and-authorization.md` | 定义 Permission 和角色矩阵；本文不重复 Permission，只声明控制责任与授权入口 |
| `04-data-model.md` | 保存 IAM、审计、Content Registry 和删除任务；本文说明哪些产品对象需要这些数据模型承载 |
| `05-backend-and-api.md` | 定义 ProductFacade、TargetPolicy 和 API；本文确定哪些源码 Router 只能作为底层实现 |
| `06-frontend-security-and-operations.md` | 定义 `/app`、`/admin`、`/platform` 页面和安全规则；本文提供页面能力目录的来源和边界 |
| `07-quality-rollout-and-decisions.md` | 定义测试、实施和验收；本文的验收标准应进入 Phase 0 能力目录冻结和后续权限测试 |
| `09-resource-product-contract.md` | 将本文确定的 Resource、Watch、Task 能力边界细化为页面字段、状态机、动作、API 和异常流程 |
| `10-skill-product-contract.md` | 细化 Skill 创建上传、发布、使用、权限和恢复规则 |
| `11-memory-search-session-product-contract.md` | 细化 Memory、Search、Session 接入、Commit、Memory Impact 和删除恢复规则 |

## 35. 本轮收敛结论

基于 v0.4.12 的 Studio 路由、REST Router、13 个 MCP Tool 和 SDK/CLI 能力盘点，v0.1 收敛为：

1. Watch/自动同步进入正式产品，但只作为 Resource 详情的子功能；Task 统一进入 Activity 状态视图，不新建 Watch 顶级菜单。
2. Snapshot、Pack、Backup、Import、Restore 全部保留为私网运维能力，不分配给 User、Account Admin 或 Platform Super Admin 的普通产品页面。产品若需要单对象下载/导出，单独走受控 Facade，不复用实例恢复接口。
3. Relations/Graph 作为 Search、Session 和 Context 的内部增强，不建立独立页面，也不向普通凭证开放 `link/unlink/build_graph`。
4. MCP OAuth 是正式集成能力；同意页和跨设备验证页从 Studio 迁入 `web-platform`，使用本地邮箱密码产生的登录 Session 授权，不引入 OIDC，也不要求浏览器保存 API Key。
5. WebDAV 因当前实现直接映射 Account 共享根且支持写、删、移动，v0.1 生产禁用。
6. Studio 继续是可选私网维护入口；它现有的业务能力必须拆入正式产品页面，底层调试能力不得借 Studio 身份进入产品权限模型。

以上是能力边界，不是开发计划。Resource、Skill、Memory、Search 和 Session 已分别在 09、10、11 号文档中细化；VikingBot 是可选外部接入方。后续页面继续采用“先复用源码，只有真实冲突再决策”的方式收敛字段、状态、动作和异常流程，而不是按源码 Router 增加菜单。
