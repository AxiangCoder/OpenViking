# 10 Skill 页面与产品契约

> Design v0.1 · [返回版本索引](README.md)

## 50. 文档目标

本文固定 Skill 在正式产品中的页面、创建方式、名称、权限、发布、编辑、删除恢复和调用边界。本文只覆盖已经确认的 v0.1 规则，不把 `/studio/skills`、Viking URI 或底层文件系统直接包装成产品页面。

专业上，这份设计是 **Skill Authoring, Ownership and Invocation Contract（Skill 创作、归属与调用契约）**。大白话说，它回答“谁能创建哪种 Skill、怎么安装、名字怎么管、谁能把它变成团队共享，以及从哪里使用”。

## 51. 源码基线

OpenViking v0.4.12 当前具备以下能力：

- `POST /api/v1/skills` 接受完整 `SKILL.md` 字符串、结构化 Skill 数据或临时上传文件。
- 结构化输入若符合 MCP Tool 数据格式，`SkillProcessor` 可以把它转换为 Skill；这只是输入格式转换，不表示 MCP 已有 `add_skill` Tool。
- 上传内容可以是单个 `SKILL.md` 或 ZIP；ZIP 根目录或唯一一级子目录中必须存在 `SKILL.md`。
- Skill 包中的其他文件作为辅助文件写入 Skill 目录。
- 默认目标是当前 User 私有根 `viking://user/{ov_user_id}/skills/**`；显式目标可使用 Account 共享根 `viking://agent/skills/**`。
- `GET /api/v1/skills` 会合并返回私有 Skill 与共享 Skill，当前源码允许两者同名且不去重。
- `PUT /api/v1/skills/{skill_name}` 是整体替换，且新内容中的 `name` 必须与路径名称一致。
- `DELETE /api/v1/skills/{skill_name}` 当前执行底层删除；产品层必须接入统一 30 天软删除。
- `/studio/skills` 当前主要提供列表和详情查看，不是完整的新建、上传、编辑和权限产品。
- 当前没有 Skill Git URL 导入、网页导入、Watch 或通用 `POST /skills/{id}/run` 执行器。

产品 v0.1 在复用解析、ZIP 安全解压、Skill 校验、隐私占位符和索引能力的同时，用 Product Facade 覆盖源码的同名合并、任意 `target_uri` 和直接物理删除行为。

## 52. 已确认的 v0.1 规则

1. 正式页面支持在线创建、上传单个 `SKILL.md` 和上传 ZIP Skill 包。
2. v0.1 页面不提供 Git URL 导入和 MCP Tool JSON 导入；SDK/CLI/API 仍可使用已经纳入契约的结构化输入。
3. 在线创建的简单 Skill 可以在线编辑；ZIP Skill 展示文件，但更新时整体重新上传，不提供逐文件在线编辑。
4. 普通 User 只能创建、上传和管理自己的 User 私有 Skill。
5. 只有 Account Admin 可以创建、上传和管理本 Account 的共享 Skill。
6. Account Admin 可以读取本 Account 任意 User 的私有 Skill，并可将其直接发布为 Account 共享 Skill；不需要所属 User 审批。
7. 除发布外，Account Admin 不能编辑、删除或恢复其他 User 的私有 Skill。
8. 发布是归属转换：Skill ID 和名称不变，URI 从 User 私有根迁入 Account 共享根，原私有区不保留副本。
9. 发布是单向操作；v0.1 不提供取消发布或共享转私有。
10. Platform Super Admin 对所有 Account 的 Skill 只有读取权限，不能创建、上传、编辑、发布、删除、恢复或使用 Skill。
11. 同一 Account 内所有未删除 Skill 的名称全局唯一，包含所有 User 私有 Skill 和 Account 共享 Skill。
12. Skill 名称创建后不可修改；可以修改描述、标签、正文和 `allowed-tools`。
13. Skill 软删除后名称立即释放，可以创建、上传或发布同名 Skill。
14. 恢复旧 Skill 时若名称已被占用，v0.1 返回名称冲突，不改名、不覆盖新 Skill；更复杂的冲突处理留到后续版本。
15. Skill 页面不提供“在新 Session 中使用”或独立“运行/测试 Skill”执行器；实际使用发生在 Codex、其他 Agent、插件或 MCP 客户端。
16. 发布只处理 Skill 归属和 URI，不迁移、共享或删除任何 User 私密配置。

## 53. 产品信息架构

### 53.1 路由

```text
/app/skills/private
/app/skills/private/new
/app/skills/private/$skillId

/app/skills/shared
/app/skills/shared/$skillId

/admin/shared-skills
/admin/shared-skills/new
/admin/shared-skills/$skillId
/admin/users/$userId/skills/$skillId

/platform/accounts/$accountId/skills
/platform/accounts/$accountId/skills/$skillId
/platform/accounts/$accountId/users/$userId/skills/$skillId
```

- `/app/skills/private` 是当前 Actor 自己的私有 Skill。
- `/app/skills/shared` 是当前 Account 成员可读取和使用的共享 Skill。
- `/admin/shared-skills` 是 Account Admin 的共享 Skill 管理入口。
- `/admin/users/$userId/skills/$skillId` 是 Account Admin 读取成员私有 Skill 并发起发布的入口；不提供编辑、删除或恢复按钮。
- `/platform/**/skills/**` 全部只读。

### 53.2 列表

“我的 Skill”和“Account 共享 Skill”使用独立列表，不把两种归属混成一个没有范围标识的列表。列表至少展示：

- Skill 名称。
- 描述。
- 标签。
- User 私有或 Account 共享标识。
- 私有 Skill 的所属 User；仅在有权查看该字段的管理视图展示。
- 最近更新时间。
- 是否包含辅助文件。

页面不展示 Viking URI、OpenViking User ID、`.abstract.md`、`.overview.md`、`.source.json` 或索引控制字段。

### 53.3 详情

详情页包含：

- 概览：名称、描述、标签和归属。
- 使用说明：渲染后的 `SKILL.md` 正文。
- 文件：脚本、参考资料和其他辅助文件清单。
- 工具范围：`allowed-tools` 声明。
- 私密配置入口：仅当前 User 管理自己的配置；不在发布流程中处理迁移。
- 使用提示：说明可由已连接的 Codex、其他 Agent、插件或 MCP 客户端按权限检索和读取。

`allowed-tools` 是 Skill 声明希望使用的工具范围，不是 Platform Permission，也不能替代 Agent/客户端运行时的工具授权。

## 54. 创建与上传

### 54.1 在线创建

在线表单包含：

- `name`：必填，沿用当前 `validate_skill_name` 规则，最多 64 个字符，只允许 ASCII 字母、数字、下划线和连字符。
- `description`：必填。
- `tags`：可选；最多 20 个，每个最多 40 字符，严格使用 `key=value`，key/value 均非空，整体转小写并去重。
- `allowed_tools`：可选。
- `content`：Markdown 指令正文。

产品服务将表单序列化为合法 `SKILL.md`。前端不要求普通用户手写 YAML Frontmatter，也不允许提交 Viking URI 或 `target_uri`。

普通 User 的创建入口固定进入自己的私有区；Account Admin 从 `/admin/shared-skills/new` 创建时固定进入当前 Account 共享区。Account Admin 在个人页面创建时仍创建自己的私有 Skill，角色本身不会改变页面目标。

### 54.2 文件上传

支持：

- 单个 `SKILL.md`。
- 包含 `SKILL.md`、脚本和参考资料的 ZIP。

上传后先调用共享校验能力解析名称、描述、标签、`allowed-tools` 和文件清单。上传 Skill 的 Frontmatter 标签同样只接受结构化 `key=value`；Product Facade 将其同步为 OpenViking `search_tags`，使 Skill 与 Resource 使用同一筛选语义。目标归属由入口决定，上传请求不能自行携带 `visibility`、`owner_user_id` 或任意 URI。

ZIP 的路径穿越、绝对路径、符号链接、文件数量和大小限制由服务端校验；这些属于上传安全约束，不在页面上暴露为可调底层参数。

### 54.3 不进入 v0.1 页面

- Git 仓库或网页 URL 导入。
- Skill Watch 或远程自动同步。
- MCP Tool JSON 导入表单。
- 服务端本地目录或文件路径。
- 任意 Skill 根目录选择器。

## 55. Account 范围名称唯一性

### 55.1 唯一范围

名称约束覆盖同一 `account_id` 下所有未删除 Skill：

```text
Account A
├── User 1 private skills
├── User 2 private skills
├── ...
└── account shared skills

以上集合中的有效 name 不得重复。
```

创建、在线保存首次记录、上传、结构化 API 新增和发布都必须在 Product Registry 中执行同一名称占用检查。不能只依赖 `viking://user/**` 与 `viking://agent/**` 各自目录内不重名，因为那无法阻止跨 User 和跨可见性同名。

普通 User 因名称冲突被拒绝时，响应只说明“该名称在当前 Account 不可用”，不返回占用者、所属 User、可见性或 URI，避免通过名称探测他人私有 Skill。

### 55.2 名称不可修改

Skill 创建后，Product API 的更新 DTO 不接受 `name`。上传替换时，新包 `SKILL.md` 的 `name` 必须等于当前名称，否则拒绝。

这与 Resource 的可修改展示名称不同：Skill `name` 同时是格式字段、目录名和调用标识，因此 v0.1 不另设可改变 canonical name 的重命名流程。

### 55.3 删除与恢复冲突

软删除后，该记录不再占用名称；新 Skill 可以立即使用相同名称。恢复时重新执行 Account 范围唯一检查：

- 名称未被占用：恢复原 Skill ID 和名称。
- 名称已被占用：返回 `SKILL_NAME_CONFLICT`，旧 Skill 保持在回收站。
- v0.1 不允许恢复时改名，也不覆盖当前 Skill。

产品确认维持与 Resource 的不对称占用策略（04 §10.10）：Resource 的 `ov_uri` 占用到物理清理（恢复回原位），Skill 名称是调用标识、软删除立即释放（恢复同名冲突时失败）；两种回收语义分别在前端文案与错误码中体现，不统一。

## 56. 编辑与整体替换

### 56.1 在线 Skill

Skill 所属 User 可以编辑自己的私有 Skill；Account Admin 可以编辑共享 Skill。可编辑字段为：

- `description`
- `tags`
- `allowed_tools`
- `content`

`name` 不可编辑。

标签更新使用与创建相同的结构化校验。多个 Search 标签采用 AND 关系；产品不提供独立的自由标签字段，也不在标签与 `search_tags` 之间增加编码转换层。

### 56.2 ZIP Skill

ZIP 导入的 Skill 可以浏览其文件清单和允许读取的文件内容，但更新采用完整 ZIP 重新上传。v0.1 不提供网页文件树中的新建、重命名、移动、删除或逐文件保存。

底层仍使用 OpenViking 现有整体替换能力；产品页面不暴露更新备份目录、原始 Task、索引或文件系统写入过程。

### 56.3 暂缓的处理语义

本次设计不新增 Skill 版本切换、发布代数、处理期间可见性或失败回退产品规则。v0.1 沿用 OpenViking 当前新增与整体替换处理方式；若后续需要稳定的多版本发布能力，另行设计，不从 Resource 的 generation 模型自动类推。

## 57. Skill 使用边界

Skill 详情页只负责查看和管理，不执行 Skill，也不创建网页 Session。Codex、其他 Agent、插件或 MCP 客户端在运行时通过 Search/Read 等受控接口发现并加载 Skill：

1. 客户端使用当前 User API Key 或用户委托型 OAuth Token。
2. 服务端按稳定产品对象映射解析 Skill，不接受任意 URI 越权读取。
3. 每次读取或使用前重新校验 Skill 仍存在、未删除且当前 User 仍有 read/use Permission。
4. Agent Runtime 自己决定如何把 Skill 放入对话上下文；OpenViking 产品网页不代理模型执行。

本设计不增加 `/skills/{id}/execute`、服务器通用 Skill Runner 或“在新 Session 中使用”按钮。

## 58. 发布为 Account 共享 Skill

### 58.1 权限与范围

- 只有 Account Admin 拥有 `skill.user_private.publish.account`。
- Account Admin 可以发布本 Account 任意 User 的私有 Skill，包括自己的私有 Skill。
- 普通 User 不能发布，即使目标是自己的 Skill。
- Platform Super Admin 只有读取权限，不能代替 Account Admin 发布。
- Account Admin 的发布权不包含编辑、删除或恢复其他 User 私有 Skill。

### 58.2 发布语义

发布不是复制，而是同一产品对象的归属转换：

```text
发布前
  id = S1
  visibility = user_private
  owner_user_id = U1
  ov_uri = viking://user/{U1}/skills/example

发布后
  id = S1
  visibility = account_shared
  owner_user_id = null
  ov_uri = viking://agent/skills/example
```

名称、Skill ID、正文和辅助文件保持不变。原 User 私有区不再保留一份副本，产品不提供取消发布或反向转换。

发布只处理 Skill 对象归属和 URI。User 私密配置不迁移、不共享、不删除，继续保留在各 User 私有空间；v0.1 不建立发布时的私密配置处理流程。

### 58.3 确认与审计

发布属于改变他人数据归属的高风险操作。确认弹窗展示：

- Skill 名称。
- 当前所属 User。
- 目标 Account。
- 发布后所有 Account 成员可读取和使用。
- 原 User 私有区不再保留。
- 操作不可取消发布。

不要求所属 User 审批，不要求重输密码或 Account 名称。审计记录 Actor Account Admin、Subject User、Skill ID、发布前后归属和结果。

### 58.4 发布执行与失败补偿

发布采用「PG 记录转换 + 受控迁移任务」两段式执行，不把物理迁移放在 HTTP 请求内：

1. 前置检查：发布事务内确认 Skill 为 `active`、未删除，且目标共享根 `viking://agent/skills/{name}` 在 PostgreSQL 与磁盘上均无同名占用；名称唯一性以 PG 部分唯一约束为准，磁盘侧复查防事务外变更。
2. 发布事务（同一 PG 事务）：`platform_content_refs` 写入归属转换（`visibility=account_shared`、`owner_user_id=NULL`、`ov_uri` 更新为目标共享根），同时创建 `platform_operation_refs(kind=task, type=skill_publish)` 与 `iam_outbox` 发布事件。
3. 发布 Worker（SystemPrincipal）执行：
   - 再次校验目标共享根无同名；
   - 复用 `viking_fs.mv` 完成整目录迁移：文件树复制（含 `SKILL.md`、`.abstract.md`、`.overview.md`、辅助文件与 `.source.json`）、向量 URI/ID 重写（保留原向量，不重新 embedding）、源目录删除，一条调用完成；
   - 清洗向量记录残留的旧 `owner_user_id`（源码 `update_uri_mapping` 复制记录时保留原 owner 字段；发布后必须清除，避免共享 Skill 残留属主语义）。
4. 成功：Operation 置 `succeeded`，Content Ref 保持 `active`；失败：Operation 置 `failed` 并可重试——`fs.mv` 在源缺失时只清理孤儿索引且幂等，重试不会产生重复对象或重复移动。
5. 发布不可取消、不可反向转换（§52.9）。迁移期间的意外失败由 Operation 状态机保护，不允许出现「已转换但未迁移」或「已迁移但未转换」的持久不一致。
6. 兜底：孤儿向量与残留文件由 Purge Worker 复用现有 `prune_orphans` 机制清理；User 私密配置按 §52.16 不迁移、不共享、不删除。
7. 审计：记录 Actor（Account Admin）、Subject User、Skill ID、发布前后归属（`user_private -> account_shared`）与结果，不记录 Skill 正文。

## 59. 删除与恢复

- User 可以软删除和恢复自己的私有 Skill。
- Account Admin 可以软删除和恢复 Account 共享 Skill。
- Account Admin 不能删除或恢复其他 User 私有 Skill。
- Platform Super Admin 只能读取，不能删除或恢复任何 Skill。
- Skill 删除进入统一 30 天回收站；产品公开入口不得调用底层直接物理删除。
- 共享 Skill 发布后已归 Account，后续由 Account Admin 按共享 Skill 权限删除和恢复。
- 软删除立即释放名称；恢复时按 55.3 处理同名冲突。

## 60. 角色能力矩阵

| 动作 | 普通 User | Account Admin | Platform Super Admin |
| --- | --- | --- | --- |
| 创建/上传自己的私有 Skill | 允许 | 允许 | 不允许 |
| 编辑/删除/恢复自己的私有 Skill | 允许 | 允许 | 不允许 |
| 查看其他 User 私有 Skill | 不允许 | 当前 Account | 全平台只读 |
| 编辑/删除/恢复其他 User 私有 Skill | 不允许 | 不允许 | 不允许 |
| 发布其他 User 私有 Skill | 不允许 | 当前 Account | 不允许 |
| 读取 Account 共享 Skill | 当前 Account | 当前 Account | 全平台只读 |
| 在 Session 中使用共享 Skill | 允许 | 允许 | 不允许 |
| 创建/上传/编辑/删除/恢复共享 Skill | 不允许 | 当前 Account | 不允许 |
| 取消发布 | 不支持 | 不支持 | 不支持 |

## 61. Product API 契约

### 61.1 当前 User 私有 Skill

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/platform/v1/me/skills` | 私有 Skill 列表 |
| POST | `/api/platform/v1/me/skills` | 在线创建或消费已上传的 `upload_id`；目标固定为 Actor 私有区 |
| GET | `/api/platform/v1/me/skills/{id}` | 私有 Skill 详情 |
| PUT | `/api/platform/v1/me/skills/{id}` | 整体更新自己的 Skill；名称不可变 |
| DELETE | `/api/platform/v1/me/skills/{id}` | 软删除自己的 Skill |
| POST | `/api/platform/v1/me/skills/{id}/restore` | 30 天内恢复；同名占用时失败 |

注（PUT 请求体）：在线 Skill 提交 `description/tags/allowed_tools/content` JSON（与创建一致，`name` 不接受）；ZIP Skill 提交新的 `upload_id` 整体替换，新包 `SKILL.md` 的 `name` 必须等于当前名称，否则返回 `SKILL_NAME_IMMUTABLE`。

### 61.2 当前 Account 共享 Skill

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/platform/v1/account/skills` | 当前 Account 共享 Skill 列表 |
| GET | `/api/platform/v1/account/skills/{id}` | 共享 Skill 详情 |
| POST | `/api/platform/v1/account/skills` | 仅 Account Admin 在线创建或上传共享 Skill |
| PUT | `/api/platform/v1/account/skills/{id}` | 仅 Account Admin 整体更新，名称不可变 |
| DELETE | `/api/platform/v1/account/skills/{id}` | 仅 Account Admin 软删除 |
| POST | `/api/platform/v1/account/skills/{id}/restore` | 仅 Account Admin 恢复；同名占用时失败 |

### 61.3 Account Admin 成员 Skill

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/platform/v1/admin/users/{user_id}/skills` | 读取成员私有 Skill 列表 |
| GET | `/api/platform/v1/admin/users/{user_id}/skills/{id}` | 读取成员私有 Skill 详情 |
| POST | `/api/platform/v1/admin/users/{user_id}/skills/{id}/publish` | 将该 Skill 原地转换为 Account 共享 Skill |

这里不提供成员私有 Skill 的 `PUT`、`DELETE` 或 `restore` 管理接口。

### 61.4 Platform 只读 Skill

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/platform/v1/platform/accounts/{account_id}/skills` | 读取目标 Account 共享 Skill |
| GET | `/api/platform/v1/platform/accounts/{account_id}/skills/{id}` | 读取共享 Skill 详情 |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/skills` | 读取目标 User 私有 Skill |
| GET | `/api/platform/v1/platform/accounts/{account_id}/users/{user_id}/skills/{id}` | 读取目标 User 私有 Skill 详情 |

Platform Skill 路由不提供 POST、PUT、DELETE、restore、publish 或 use。

### 61.5 上传与校验

Skill 上传使用绑定 Actor、Account 和目标入口的一次性 `upload_id`。产品服务可以复用受控临时上传基础设施，但不能让普通 User 把私有入口生成的上传 ID 消费到共享区，也不能接受客户端用 `target_uri` 改变目标。

产品 API 可以提供统一校验端点返回格式错误和文件清单；校验成功不代表已经创建 Skill，也不占用名称。正式创建时必须在数据库事务中再次检查名称唯一性。

## 62. 低层 API、SDK、CLI 与 MCP

- `/api/v1/skills`、`/api/v1/resources` 中的 `add_skill`、SDK 和 CLI 最终必须经过同一 Skill Registry、Account 范围名称唯一检查与 Target Policy。
- 普通 User 未指定目标时进入自己的私有区；显式提交 `viking://agent/skills` 也必须返回 403。
- Account Admin 只有通过授权的共享创建或发布动作才能写共享根；不能用任意文件系统写入绕过名称注册。
- 当前 MCP 没有 `add_skill` Tool，不在 v0.1 文档中宣称 MCP 可以创建或发布 Skill。
- 所有调用渠道使用稳定产品 ID 解析对象；旧名称接口在 Product Facade 外不得作为跨私有/共享目标选择规则。

## 63. 错误语义

| 错误码 | 场景 |
| --- | --- |
| `SKILL_NAME_CONFLICT` | 当前 Account 已有未删除同名 Skill，或恢复时名称已被重新占用 |
| `SKILL_NAME_IMMUTABLE` | 更新包或请求试图修改 Skill 名称 |
| `SKILL_INVALID_FORMAT` | `SKILL.md`、Frontmatter 或 ZIP 结构不合法 |
| `SKILL_SHARED_WRITE_FORBIDDEN` | 普通 User 或 Platform Super Admin 尝试写共享 Skill |
| `SKILL_PRIVATE_MANAGE_FORBIDDEN` | Account Admin/Platform 尝试编辑、删除或恢复他人私有 Skill |
| `SKILL_PUBLISH_FORBIDDEN` | 非 Account Admin、跨 Account 或 Platform 发起发布 |
| `SKILL_UNPUBLISH_UNSUPPORTED` | 请求共享转私有或取消发布 |
| `SKILL_RESTORE_NAME_CONFLICT` | 恢复对象时名称已被占用；可映射统一 `SKILL_NAME_CONFLICT` 作为稳定外部码 |

403 响应不能泄露同名私有 Skill 的所属 User 或 URI；404 与 403 的选择遵循统一防 IDOR 策略。

## 64. 验收标准

1. 普通 User 可以在线创建、上传 `SKILL.md`/ZIP、编辑和删除自己的私有 Skill。
2. 普通 User 不能通过页面、Product API、低层 REST、SDK 或 CLI 把 Skill 写入共享根。
3. Account Admin 可以创建和管理共享 Skill，并能读取和发布本 Account 任意 User 的私有 Skill。
4. Account Admin 不能编辑、删除或恢复其他 User 私有 Skill。
5. Platform Super Admin 能读取任意 Account 的私有/共享 Skill，但所有 Skill 写入、发布、恢复和使用操作被拒绝。
6. Account 内任意两个未删除 Skill 不能同名，即使属于不同 User 或不同可见性。
7. Skill 创建后不能改名；在线更新和 ZIP 替换均拒绝名称变化。
8. 发布保持 Skill ID 和名称不变，将 URI 与归属从 User 私有转换为 Account 共享，不保留副本。
9. 发布不需要所属 User 审批，但展示影响范围并完整审计；v0.1 不支持取消发布。
10. 删除立即释放名称；恢复遇到同名时失败，不改名也不覆盖当前 Skill。
11. ZIP Skill 只能整体重新上传，产品页面不能逐文件写入底层 Skill 目录。
12. Skill 页面不提供“在新 Session 中使用”、独立 Skill `execute` API 或通用运行器；接入客户端按权限检索和读取 Skill。
13. 发布流程不迁移、共享或删除 User 私密配置。
14. 页面与 DTO 不泄露 Viking URI、内部控制文件、绝对路径或其他 User 的私密配置。

## 65. 本次明确暂缓

以下内容不在本次 v0.1 Skill 契约中继续展开：

- Skill 处理期间的产品可见性状态。
- 多版本 Skill、版本切换和失败回退模型。
- 发布时的私密配置迁移或清理策略。
- 恢复同名冲突时的改名、覆盖或人工合并。
- Git/网页远程 Skill、Watch 和自动同步。
- 独立 Skill Runner、测试沙箱和服务器执行环境。
- 取消发布或共享转私有。

这些暂缓项不能由实现自行补成 v0.1 功能；若要加入，必须先更新本设计版本。
