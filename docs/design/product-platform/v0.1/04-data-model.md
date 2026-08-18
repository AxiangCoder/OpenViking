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
| `email` | VARCHAR(320) | 可空，规范化后按策略唯一 |
| `display_name` | VARCHAR(128) | 可空 |
| `password_hash` | TEXT | 可空，纯 OIDC 用户可无密码 |
| `password_changed_at` | TIMESTAMPTZ | 可空 |
| `status` | VARCHAR(24) | `invited/provisioning/active/disabled/failed/pending_deletion/deleted` |
| `permission_version` | BIGINT | 权限缓存失效版本 |
| `last_login_at` | TIMESTAMPTZ | 可空 |
| `deleted_at/purge_after/deleted_by` | TIMESTAMPTZ/TIMESTAMPTZ/UUID | 30 天回收期与删除 Actor |
| `created_at/updated_at` | TIMESTAMPTZ | 审计时间 |

唯一约束：

- `(account_id, ov_user_id)` 唯一。
- `(account_id, normalized_username)` 唯一。
- 邮箱唯一性采用全局唯一还是 Account 内唯一仍待产品确认；冻结前不能据此生成数据库唯一索引。
- Account Admin 和 User 创建后固定归属一个 Account，不建立多 Account membership，也不提供 Account 切换。
- `account_id IS NULL` 只允许 `platform_super_admin`，并由数据库约束或 service invariant 强制。

### 10.3 `iam_identities`

用于密码之外的外部登录身份：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键 |
| `user_id` | 内部用户外键 |
| `provider` | `oidc/wechat_work/...` |
| `issuer` | Provider issuer |
| `subject` | Provider 内稳定 subject |
| `profile` | JSONB，非敏感展示信息 |
| `created_at/last_used_at` | 时间 |

唯一约束：`(provider, issuer, subject)`。

### 10.4 `iam_roles`

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

### 10.7 `iam_sessions`

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
| `actor_user_id` | 操作者，可空表示系统 |
| `actor_account_id` | Actor 所属 Account；Platform Super Admin/系统可空 |
| `actor_session_id` | 会话，可空 |
| `subject_account_id` | 被访问数据所属 Account，可空 |
| `subject_user_id` | 被访问数据所属 User，可空 |
| `action` | 权限或业务动作 |
| `target_type/target_id` | 操作对象 |
| `scope` | `self/account/platform` |
| `result` | `success/denied/failed` |
| `reason` | 拒绝/失败原因 |
| `metadata` | JSONB，必须脱敏 |

审计日志与应用日志分开。管理员访问他人数据时，`actor_user_id` 与 `subject_user_id` 必须同时存在；系统不能只记录 Subject。审计日志不记录密码、Session Token、API Key 或完整敏感正文。

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

### 10.10 `iam_deletion_jobs`

统一跟踪 Account、User、Memory、Session 和 Resource 的软删除、恢复和期满清理：

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

- 软删除后对象从正常查询隐藏；Account/User 同时禁止登录并撤销相关 Session。
- 回收期内按数据范围授权恢复；恢复动作必须写审计。
- 到达 `purge_after` 后由后台 Worker 幂等清理 OpenViking 数据，再将状态置为 `purged`。
- 审计事件不随业务数据物理清理。
