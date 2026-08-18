# 04 PostgreSQL 数据模型

> Design v0.1 · [返回版本索引](README.md)

## 10. PostgreSQL 数据模型

### 10.1 `iam_accounts`

| 字段 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | UUID | 主键，内部不可变 ID |
| `ov_account_id` | VARCHAR(64) | 唯一，映射 OpenViking `account_id` |
| `display_name` | VARCHAR(128) | 展示名称 |
| `status` | VARCHAR(24) | `provisioning/active/suspended/failed/pending_deletion/deleted` |
| `provisioning_error` | TEXT | 最近一次同步错误 |
| `deleted_at` | TIMESTAMPTZ | 进入回收期的时间，可空 |
| `purge_after` | TIMESTAMPTZ | 默认 `deleted_at + 30 days`，可空 |
| `deleted_by` | UUID | 执行软删除的 Actor，可空 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |
| `version` | BIGINT | 乐观锁版本 |

### 10.2 `iam_users`

| 字段 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `account_id` | UUID | 外键 `iam_accounts.id`；Platform Super Admin 可空，其他用户必须且只能属于一个 Account |
| `ov_user_id` | VARCHAR(64) | 映射 OpenViking `user_id`；Platform Super Admin 可空 |
| `username` | VARCHAR(128) | Account 内唯一 |
| `email` | VARCHAR(320) | 必填，规范化后全局唯一，作为登录标识 |
| `display_name` | VARCHAR(128) | 可空 |
| `password_hash` | TEXT | v0.1 必填，Argon2id；不保存或恢复明文 |
| `password_changed_at` | TIMESTAMPTZ | 可空 |
| `status` | VARCHAR(24) | `provisioning/active/disabled/failed/pending_deletion/deleted` |
| `permission_version` | BIGINT | 权限缓存失效版本 |
| `last_login_at` | TIMESTAMPTZ | 可空 |
| `deleted_at/purge_after/deleted_by` | TIMESTAMPTZ/TIMESTAMPTZ/UUID | 30 天回收期与删除 Actor |
| `created_at/updated_at` | TIMESTAMPTZ | 审计时间 |

唯一约束：

- `(account_id, ov_user_id)` 唯一。
- `(account_id, normalized_username)` 唯一。
- `normalized_email` 建全局唯一索引；规范化只做 Unicode/大小写和首尾空白处理，不使用邮箱服务商特有的点号或 `+tag` 折叠规则。
- Account Admin 和 User 创建后固定归属一个 Account，不建立多 Account membership，也不提供 Account 切换。
- `account_id IS NULL` 只允许 `platform_super_admin`，并由数据库约束或 service invariant 强制。

### 10.3 `iam_api_credentials`

保存 Account User 为 SDK、CLI、插件和 MCP 创建的个人 API Key。v0.1 的每条记录必须归属于一个 `account_id` 非空的用户，不支持 Platform Super Admin Key、`service_account_id` 或其他机器主体。

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键，同时作为审计中的 `credential_id` |
| `account_id` | Account 外键，必须与所属用户一致 |
| `user_id` | `iam_users.id` 外键，不可空 |
| `name` | 用户填写的用途名称，例如“Codex 笔记本” |
| `public_id` | Key 内非敏感随机定位 ID，唯一索引；不包含 Account/User 信息 |
| `key_hash` | `SHA-256(secret)`，唯一；secret 至少 256 bit 且不保存明文 |
| `key_last_four` | 列表展示用末四位，不参与认证 |
| `status` | `active/revoked` |
| `expires_at` | 可空；到期后立即拒绝 |
| `last_used_at` | 最近成功使用时间，可空，可异步更新 |
| `created_by` | 创建该 Key 的用户；v0.1 只能为自己创建 |
| `created_at` | 创建时间 |
| `revoked_at/revoked_by` | 撤销时间和 Actor，可空 |

规则：

- 一个 User 可以有多个具名 Key，但所有 Key 共享该 User 当前角色、Permission 和数据范围。
- Key 不保存 Role、Permission 快照或独立 Scope；用户禁用、删除、角色变化后无需轮换 Key 即刻生效。
- 创建接口只返回一次完整明文；列表和审计仅返回 `name/public_id/key_last_four/status/expires_at/last_used_at`。
- 删除用户进入回收期时立即撤销其全部 Key；恢复用户不自动恢复已撤销 Key。
- v0.1 不建 `iam_service_accounts`、`service_account_roles` 或 Service Account Credential 表。

### 10.4 `iam_roles`

v0.1 只种子化 `platform_super_admin/account_admin/user` 三个内置角色，不开放自定义 Role CRUD。

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键 |
| `account_id` | Account 外键；仅平台级 System Role 可为空 |
| `code` | Account 内稳定唯一代码 |
| `name` | 展示名称 |
| `description` | 描述 |
| `ov_base_role` | `user` 或 `admin`；Platform Super Admin 为 `NULL` |
| `is_system` | 是否内置角色 |
| `status` | `active/disabled` |
| `created_at/updated_at` | 时间 |

### 10.5 `iam_permissions`

| 字段 | 说明 |
| --- | --- |
| `code` | 主键，例如 `memory.read.self` |
| `domain` | `memory/user/role/...` |
| `action` | `read/write/delete/...` |
| `description` | 权限解释 |
| `risk_level` | `low/medium/high/critical` |

Permission 由代码和 migration 注册，不允许普通管理员任意创建未知 Permission Code。

### 10.6 关联表

`iam_role_permissions`：

- `role_id`
- `permission_code`
- 复合主键 `(role_id, permission_code)`

`iam_user_roles`：

- `user_id`
- `role_id`
- `assigned_by`
- `assigned_at`
- 复合主键 `(user_id, role_id)`

v0.1 对每个 User 强制只有一个有效内置角色；表结构保留关联形式只是为了权限查询和未来扩展，不在首版开放多角色叠加。

### 10.7 `iam_sessions`

本表只保存网页登录的登录 Session/认证会话，不保存 OpenViking 对话 Session。管理员重置密码时批量填写目标用户未撤销记录的 `revoked_at/revoked_reason`，业务对话数据不受影响。

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键，写入审计引用 |
| `token_hash` | SHA-256，唯一 |
| `account_id/user_id` | 会话归属；Platform Super Admin 的 `account_id` 可空 |
| `csrf_secret_hash` | CSRF 绑定信息 |
| `created_at` | 创建时间 |
| `last_seen_at` | 最近活动时间 |
| `idle_expires_at` | 空闲到期时间 |
| `absolute_expires_at` | 绝对到期时间 |
| `revoked_at/revoked_reason` | 撤销信息 |
| `ip_hash` | 可选，隐私化后保存 |
| `user_agent` | 限长保存 |

索引：`token_hash`、`(user_id, revoked_at)`、`absolute_expires_at`。

### 10.8 `iam_audit_events`

| 字段 | 说明 |
| --- | --- |
| `id` | UUID/ULID 主键 |
| `occurred_at` | 事件时间 |
| `request_id` | 关联 HTTP Request ID |
| `account_id` | 租户范围 |
| `actor_type` | `user/system`；v0.1 不存在 `service_account` |
| `actor_user_id` | 操作者，可空表示系统 |
| `actor_account_id` | Actor 所属 Account；Platform Super Admin/系统可空 |
| `actor_system_component` | 系统任务组件名，可空；`actor_type=system` 时必填 |
| `actor_session_id` | 会话，可空 |
| `authentication_method` | `session/api_key/oauth/system` |
| `actor_credential_id` | API Key/OAuth 凭证 ID，可空；不记录 secret |
| `subject_account_id` | 被访问数据所属 Account，可空 |
| `subject_user_id` | 被访问数据所属 User，可空 |
| `action` | 权限或业务动作 |
| `target_type/target_id` | 操作对象 |
| `target_visibility` | `user_private/account_shared/internal`，非数据操作可空 |
| `scope` | `self/account/platform` |
| `result` | `success/denied/failed` |
| `reason` | 拒绝/失败原因 |
| `metadata` | JSONB，必须脱敏 |

审计日志与应用日志分开。管理员访问他人数据时，`actor_user_id` 与 `subject_user_id` 必须同时存在；系统不能只记录 Subject。内部 Worker 使用 `actor_type=system` 和 `actor_system_component`，不能伪装成用户。审计日志不记录密码、登录 Session Token、API Key 明文、完整凭证 hash 或敏感正文。

由于 v0.1 允许管理员复制并长期知道新建/重置后的用户密码，密码交接完成前后的 Actor 只表示“使用了哪个用户凭证”，不能提供自然人不可抵赖证明；这是已接受的产品限制。审计必须记录创建者/重置者、目标用户和时间，但绝不记录生成的密码。

### 10.9 `iam_outbox`

用于 PostgreSQL 与 OpenViking Provisioning 的可靠同步：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID/ULID |
| `event_type` | `account.provision/user.provision/user.disable/...` |
| `aggregate_id` | Account 或 User ID |
| `payload` | JSONB，仅含必要 ID 和操作参数 |
| `status` | `pending/processing/completed/failed` |
| `attempts` | 重试次数 |
| `next_attempt_at` | 下次重试时间 |
| `last_error` | 脱敏错误 |
| `created_at/completed_at` | 时间 |

### 10.10 `platform_content_refs`

该表保存产品稳定 ID、授权元数据与 OpenViking URI 的映射，不复制业务内容正文。OpenViking 仍是内容事实来源，PostgreSQL 是可见性、归属与审计关联的事实来源。

| 字段 | 说明 |
| --- | --- |
| `id` | UUID/ULID 主键，作为产品 API 的对象 ID |
| `account_id` | 对象所在 Account，不可空 |
| `object_type` | `resource/skill`；Memory 与 Session 继续使用 OpenViking 自身稳定 ID，不建立影子目录 |
| `visibility` | `user_private/account_shared` |
| `owner_user_id` | User 私有对象的所属 User；Account 共享对象必须为空 |
| `ov_uri` | 规范化后的 OpenViking URI，在 Account 内唯一；不直接由前端指定 |
| `canonical_name` | Skill 必填的稳定名称，Resource 可空；Skill 按当前 `validate_skill_name` 规范化后写入，创建后不可修改 |
| `display_name` | Resource 的产品展示名称；Skill v0.1 与 `canonical_name` 一致且不可单独修改 |
| `description` | 产品说明，可空 |
| `tags` | JSONB 产品标签；Resource 最多 20 个，需同步到 OpenViking Search Tags |
| `source_type` | Resource 使用 `upload/web/git`；Skill 可使用自己的来源枚举 |
| `source_display` | 已脱敏的文件名或远程来源，不包含 URL Query/Userinfo/Secret |
| `source_locator_ciphertext/source_locator_key_version` | 应用层加密的远程来源与密钥版本；上传来源为空，普通查询和 DTO 永不返回 |
| `source_fingerprint` | 来源规范化 fingerprint，用于审计、幂等和变更检测，不替代访问控制 |
| `mime_type/size_bytes` | 单文件来源元数据，可空；集合型 Resource 的统计由详情聚合 |
| `latest_operation_id` | 最近一次导入/Refresh Operation，可空 |
| `last_processed_at` | 最近成功处理时间，可空 |
| `active_generation` | 当前对外可读的最近成功内容代数；首次成功前为 0 |
| `created_by_actor_user_id` | 实际创建者，仅用于审计和展示，不授予 Account 共享对象所有权 |
| `updated_by_actor_user_id` | 最近更新 Actor，可空 |
| `status` | `provisioning/active/failed/pending_deletion/deleted` |
| `version` | 产品元数据乐观锁版本；与内容 `active_generation` 分开 |
| `created_at/updated_at/deleted_at` | 时间 |

一致性约束：

- `visibility=user_private` 时 `owner_user_id IS NOT NULL`，URI 必须位于该 User 的 `viking://user/{ov_user_id}/...` canonical root。
- `visibility=account_shared` 时 `owner_user_id IS NULL`，Resource URI 必须位于 `viking://resources/**`，Skill URI 必须位于 `viking://agent/skills/**`。
- 客户端提交的可见性不能直接落库；服务端根据操作入口、已授权目标与 canonical URI 共同判定，并在事务中校验。
- Account 共享对象属于 Account，不属于创建它的 User。删除或禁用创建者不自动删除共享对象，也不会改变其他成员的读取权限。
- Resource 不支持在 `user_private` 与 `account_shared` 间直接改字段转换；发布 Resource 是显式复制新建，生成新产品 ID。Skill 是明确例外：Account Admin 发布本 Account 任意 User 私有 Skill 时保持同一产品 ID 和 `canonical_name`，把 `visibility/owner_user_id/ov_uri` 一并转换为 Account 共享值并审计；v0.1 不支持反向转换。
- 产品 API、对外开放的低层 API 与 MCP 创建 Resource/Skill 时都必须通过同一个 Content Registry Service 写入该表，不能产生只存在于 OpenViking、没有产品引用记录的外部内容。
- Resource 的 `ov_uri` 在对象首次进入 `active` 后不可因展示名称变化而修改；Resource 重命名只更新 `display_name`。Skill 名称不可修改，只有已授权发布动作可以在名称不变、ID 不变时把 `ov_uri` 从 User 私有根迁入 Account 共享根。
- 同一 Account 下未删除 Skill 的 `canonical_name` 全局唯一，范围同时覆盖所有 User 私有 Skill 与 Account 共享 Skill。软删除记录不参与唯一约束，因此删除后名称可以立即复用。
- Resource 的稳定远程来源使用 Secret Manager 中的应用密钥做 Envelope Encryption，只在抓取 Worker 内存中短暂解密。包含 Query 的一次性 URL 可在导入 Operation 期间加密保存，任务终态后清除，且不能创建 Watch。
- Outbox、OpenViking Task Meta、Watch JSON、审计和日志只保存 Resource ID、脱敏 `source_display` 与 `source_fingerprint`，不能复制完整来源。Watch Scheduler 通过 Resource ID 向 Product Facade 解析来源，而不是持久化明文 `path`。
- `latest_operation_id` 只做快速关联，Operation 的真实状态仍以 `platform_operation_refs` 为准。Refresh 期间 Resource 保持 `active`，继续指向上一次成功版本。
- Operation 成功提交时只有其 `generation` 仍是目标最新待处理代数，且对象不在删除中，才能原子更新 `active_generation`；旧任务晚到只能记录终态，不能切换内容。
- 产品标签与 OpenViking Search Tags 的同步使用 Outbox/Reconciler；前端不能直接调用底层 `set_tags` 形成双写分叉。

索引至少包括 `(account_id, object_type, visibility, status)`、`(owner_user_id, object_type, status)` 和 `(account_id, ov_uri)` 唯一索引。Skill 另建等价于 `UNIQUE (account_id, canonical_name) WHERE object_type='skill' AND deleted_at IS NULL` 的部分唯一约束；名称冲突响应不得泄露占用者。

### 10.11 `iam_deletion_jobs`

统一跟踪 Account、User、Memory、OpenViking 对话 Session、Resource 和 Skill 的软删除、恢复和期满清理：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID/ULID 主键 |
| `account_id` | 数据所属 Account |
| `resource_type/resource_id` | 被删除对象及产品 ID |
| `ov_uri` | 对应 OpenViking URI；可空且不返回前端 |
| `deleted_by` | 执行软删除的 Actor |
| `deleted_at` | 进入回收期时间 |
| `purge_after` | 默认 30 天后的物理清理时间 |
| `restored_by/restored_at` | 恢复 Actor 与时间，可空 |
| `status` | `pending/restored/purging/purged/failed` |
| `last_error` | 脱敏清理错误，可空 |

规则：

- 软删除后对象从正常查询隐藏；Account/User 同时禁止登录并撤销相关登录 Session。
- 回收期内按数据范围授权恢复；恢复动作必须写审计。
- Skill 软删除立即释放 Account 名称占用；恢复前重新检查同 Account 未删除 Skill 名称。若已被占用，恢复返回名称冲突并保持删除状态，不改名、不覆盖现对象。
- 到达 `purge_after` 后由后台 Worker 幂等清理 OpenViking 数据，再将状态置为 `purged`。
- 审计事件不随业务数据物理清理。
- `resource_id` 引用 `platform_content_refs.id`；删除任务中的 `ov_uri` 只供受控 Worker 使用，不能替代创建任务时的可见性与 Permission 校验。

### 10.12 `platform_operation_refs`

该表把 OpenViking 的异步 Task、Resource Watch 和产品对象关联起来，负责授权索引和产品状态展示，不复制 QueueFS 的运行日志或任务正文。

| 字段 | 说明 |
| --- | --- |
| `id` | UUID/ULID 产品操作 ID |
| `account_id` | 操作所属 Account，不可空 |
| `operation_kind` | `task/watch` |
| `operation_type` | `resource_import/resource_watch/skill_import/session_commit/...` 稳定产品枚举 |
| `ov_operation_id` | OpenViking Task/Watch ID，与 `account_id + operation_kind` 组成唯一约束 |
| `batch_id` | 批量上传/导入的产品 Batch ID，可空 |
| `target_type/target_id` | 目标对象类型及产品 ID；可空表示平台内部任务 |
| `target_visibility` | `user_private/account_shared/internal` |
| `owner_user_id` | User 私有操作的 Subject User；共享或内部操作为空 |
| `initiated_by_actor_user_id` | 发起操作的 Actor；系统发起时可空 |
| `status` | `pending/running/active/succeeded/failed/cancelled/paused`；`active/paused` 主要用于 Watch |
| `stage` | 产品稳定阶段，例如 `queued/fetching/parsing/indexing/finalizing`；不直接保存前端依赖的源码阶段 |
| `cancellable` | 当前任务类型是否允许用户取消；最终仍需实时校验 |
| `generation` | 目标对象操作代数；旧任务完成时不得覆盖更新一代或删除中的对象 |
| `error_code/error_summary/retryable` | 脱敏产品错误，可空；不保存堆栈和远程响应正文 |
| `created_at/updated_at/completed_at` | 时间 |

规则：

- `/app/activity` 只查询 `owner_user_id=actor_user_id` 的私有对象任务，以及 Actor 有权读取的当前 Account 共享对象任务。
- Account Admin 的管理视图只显示 Account 共享对象任务，不因管理员可读取成员数据而默认暴露所有成员的私有任务日志。
- Platform Super Admin 可按平台 Permission 查询全部范围，但每次查询仍记录 Actor、Subject Account/User 和 Scope。
- 查看 Watch 继承目标 Resource 的读取权限；新增、修改、手动触发和取消继承目标 Resource 的写权限。
- 取消 Task 需要对应 `task.cancel.*`、任务处于可取消状态，并再次校验目标对象的写权限；平台内部清理、Provisioning、备份恢复和迁移任务不允许通过普通产品接口取消。
- `ov_operation_id` 不返回为可枚举的主 ID；产品 API 使用本表 `id`，再由服务端解析到底层操作。

### 10.13 MCP OAuth 数据表

MCP OAuth 的 Client、Grant、Pending Authorization 和 Token 数据迁入 PostgreSQL，不继续以工作目录 SQLite 作为产品生产环境的授权事实来源。这里的 OAuth 只用于 MCP 客户端授权，不用于产品网页登录。

`iam_oauth_clients`：

| 字段 | 说明 |
| --- | --- |
| `client_id` | OAuth Public Client ID，主键 |
| `client_name` | 同意页展示名称，可空 |
| `redirect_uris` | 允许的精确回调 URI 列表 |
| `grant_types/response_types` | 允许的协议类型 |
| `token_endpoint_auth_method` | v0.1 固定为 Public Client 的 `none`，必须使用 PKCE |
| `status` | `active/disabled` |
| `created_at/updated_at` | 时间 |

`iam_oauth_grants`：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID/ULID 主键，也是 `/app/profile/connections` 的连接 ID |
| `account_id/user_id` | 完成授权的 Account User |
| `client_id` | `iam_oauth_clients.client_id` |
| `scope` | 协议 Scope；v0.1 为 `mcp` |
| `status` | `active/revoked` |
| `granted_at/last_used_at` | 授权与最近使用时间 |
| `revoked_at/revoked_by` | 撤销信息，可空 |

`iam_oauth_pending_authorizations` 保存短期 `pending_id`、display code hash、Client、精确 redirect URI、PKCE challenge、state、过期时间和批准状态；只能由 OAuth 协议端点与产品授权端点访问，前端不能提交或修改 redirect URI。

`iam_oauth_tokens` 只保存 Access Token、Refresh Token 和 Authorization Code 的 hash、类型、Grant、Token family、父子轮换关系、过期/消费/撤销状态，不保存可再次读取的明文。Refresh Token 必须轮换；检测到已消费 Token 重放时撤销整个 Token family。

授权约束：

- 一个 Grant 绑定一个 `user_id + client_id + scope`，不绑定登录 Session，也不绑定某个 User API Key。
- 同意动作只接受 `authentication_method=session` 的当前网页登录身份；API Key 可以直接调用 MCP，但不能代替浏览器批准 OAuth Client。
- 每次 OAuth Token 调用都重新加载 User、Account、角色、Permission、Grant 和 Token 状态，生成与 API Key 相同语义的 User Principal。
- 用户禁用或进入删除期时撤销其全部 OAuth Grant/Token；用户恢复后不自动恢复。角色变化无需重签 Token，但下一次请求立即按新权限计算。
- OAuth Client/Grant/Token 的创建、批准、拒绝、轮换、重放拒绝和撤销均写审计，不记录 Code、Token、PKCE verifier 或 display code 明文。

### 10.14 `platform_uploads`

该表保存 Product Upload ID 的授权元数据；上传字节存临时对象存储或受控 Temp Upload Store，不写入 PostgreSQL。

| 字段 | 说明 |
| --- | --- |
| `id` | UUID/ULID，对外作为不可猜测 Upload ID |
| `account_id` | 目标 Account，不可空 |
| `actor_user_id` | 创建 Upload 的 Actor |
| `target_visibility` | `user_private/account_shared`，创建后不可修改 |
| `owner_user_id` | 私有 Upload 的目标 User；共享 Upload 为空 |
| `object_type` | v0.1 为 `resource`，后续 Skill 可复用但必须独立校验 |
| `storage_ref` | 内部临时对象引用，不返回前端 |
| `original_filename/mime_type/size_bytes` | 服务端检测后的文件元数据 |
| `content_hash` | 文件内容 hash，用于完整性与幂等；不在普通 DTO/审计中返回全值 |
| `status` | `uploading/ready/consumed/expired/failed` |
| `expires_at` | 默认创建后 15 分钟，可由 Capabilities 配置 |
| `consumed_by_operation_id` | 成功消费它的 Product Operation，可空 |
| `created_at/consumed_at` | 时间 |

规则：

- 私有 Upload 只能由相同 Actor 的 `/me/resources/imports` 消费；共享 Upload 只能由相同 Account、仍有共享写权限的管理入口消费。
- Upload ID 不允许跨 User、Account、Visibility 或 Object Type 使用；即使知道 ID 也必须返回 404/拒绝。
- 消费使用数据库原子状态转换 `ready -> consumed`，重复请求通过 `Idempotency-Key` 返回第一次结果，不能再次创建对象。
- 到期、失败、取消和已消费文件由 Cleanup Worker 删除临时字节；清理失败告警重试，但文件保持不可消费。
- 文件名、MIME、大小和 hash 由服务端计算；不能信任浏览器上传声明。
