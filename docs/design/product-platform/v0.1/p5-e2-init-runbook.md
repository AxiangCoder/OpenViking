# P5-E2 一次性初始化与生产运维 runbook（06 §15.2 八步初始化）

- **Epic**：P5-E2（14 号计划 §99.2）｜ 文档化可重复（验收①）
- **配套**：`ov platform init` / `ov platform status` / `ov platform verify`
  （openviking/server/platform/bootstrap_cli.py，经 `openviking_cli/rust_cli.py`
  Python-native 子命令接入 `ov`）
- **环境**：deploy/product 三单元（reverse-proxy / product-server / postgresql）
- **原则**（06 §15.1）：PostgreSQL 是 Account/User/Role/登录 Session/API Key 的
  唯一身份事实来源；不存在旧凭证导入或并行期；Root API Key 不参与产品初始化。

## 八步顺序（06 §15.2）

### 步骤 1：全新 Platform schema migration

```bash
# PG 启动后（compose 已含 healthcheck），执行迁移（`init` 内部自动完成，
# 也支持单独执行）：
python -m openviking.server.platform.bootstrap_cli init --skip-migrations=false
```

`init` 先执行 `alembic upgrade head`（8 个迁移：a1b2c3d4e5f6 → e5f6a7b8c9d2），
再进入步骤 2。全新 schema = 无旧 IAM 数据（15.1：不导入旧 registry、不设并行期）。

### 步骤 2：一次性命令创建首位 Platform Super Admin

```bash
export OV_PLATFORM_DATABASE_URL='postgresql://ov_platform:CHANGE_ME@postgresql:5432/ov_platform'
export OV_PLATFORM_INIT_PSA_EMAIL='psa@example.com'
export OV_PLATFORM_INIT_PSA_USERNAME='psa'
export OV_PLATFORM_INIT_PSA_PASSWORD='<Secret Manager 注入，>=12 字符>'  # 推荐注入
ov platform init
```

- **幂等拒绝**：已存在 PSA 时退出码 1，提示"拒绝重复初始化"，不覆盖密码
  （验收②）。
- **密码策略**（03 §8.3）：环境注入时只校验长度 ≥12、不打印；未注入时服务端
  生成 16 字符随机密码并**仅展示一次**（07 §21 条目 18：可复制、长期使用、
  不强制修改）。库中只存 Argon2id hash（验收②）。
- 不用 Root API Key 代替人类管理员（15.2 注）。

### 步骤 3：PSA 创建 Account 与首位 Account Admin

登录 `/platform/accounts` →「创建 Account」：Account 名称、`code`、首位
Admin 邮箱与显示名；成功后一次性展示首位 Admin 初始密码（13 §89.2，05 §12.6）。

API：`POST /api/platform/v1/platform/accounts`（PSA）。

### 步骤 4：Provisioning Worker 初始化 OpenViking namespace

Account/User 创建时同事务写入 `iam_outbox`（account.provision + user.provision），
ProvisioningWorker 用内部 SystemPrincipal 初始化 namespace 后置 active
（05 §11.3）。部署层按周期调度 Worker；失败可见可重试
（`POST .../provisioning/retry`，幂等 409 守卫）。

状态巡检：`ov platform status`（provisioning backlog/failed 数量，阈值
>100 条即非 ready，06 §16.3）。

### 步骤 5：Account Admin 直接创建普通 User

登录 `/admin` → 创建 User（邮箱 + 显示名），复制系统生成初始密码线下交接；
**无注册/邀请/激活流程**（07 §21 条目 17）。

### 步骤 6：用户创建具名 API Key

用户登录 `/app/profile/api-keys` 创建具名 Key（如 Codex / OpenCode / 插件名）。
完整明文 `ovk_u.<public_id>.<secret>` 只返回一次；PG 只存 SHA-256 hash +
前缀 + 末四位（04 §10.3，06 §14.4）。多具名 Key 分别创建/撤销（07 §21 条目 15）。

### 步骤 7：配置客户端

把一次性 Key 配置到 Codex / OpenClaw / OpenCode / SDK / CLI / MCP 客户端；
MCP 客户端可用 OAuth 授权（`/oauth/consent`），浏览器不持久化 API Key。

### 步骤 8：三凭证一致验证

```bash
export OV_PLATFORM_VERIFY_EMAIL='user@example.com'
export OV_PLATFORM_VERIFY_SESSION_TOKEN='<浏览器 Session Cookie 值>'
export OV_PLATFORM_VERIFY_API_KEY='ovk_u.<public_id>.<secret>'
export OV_PLATFORM_VERIFY_OAUTH_TOKEN='<MCP OAuth access token>'
ov platform verify
```

对同一用户，Session/API Key/OAuth 解析出的 Principal 权限集合、角色集合与
数据范围（Actor Account/User）必须一致；任一不一致退出码 1 并指出失败凭证
（07 §21 条目 8；每次请求实时 RBAC，无渠道差异）。

## 可观测性（06 §17）

- **审计**（17.1）：登录/登出/Key/用户管理/密码重置/角色/Provisioning/Purge/
  权限拒绝全部脱敏审计；跨用户事件含 Actor+Subject（17.2 字段完整）。
- **日志关联**（17.2）：X-Request-ID 贯通 HTTP 日志、审计事件、OTel trace 与
  后台 Provisioning Task（outbox payload 回填）。
- **健康检查**（17.3）：`/health`、`/ready` 在平台模式下附加 PG 连通 / migration
  版本 / Provisioning backlog / Session cleanup / Purge 状态；非 ready 时
  503 + 可读诊断。阈值（06 §16.3）：backlog > 100、migration 落后 ≥ 1 版本、
  Purge 停滞（待清理 > 500 或最早 purge_after 落后 > 3 天或 Worker 未运行）。
  环境变量：`OV_HEALTH_PROVISIONING_BACKLOG_THRESHOLD`、
  `OV_HEALTH_MIGRATION_LAG_VERSIONS`、`OV_HEALTH_PURGE_STALL_MAX_PENDING`、
  `OV_HEALTH_PURGE_STALL_MAX_DAYS`。

## 生产化（06 §14.4）

- 全站 HTTPS（deploy/product/nginx 443 server 块 + 证书挂载）。
- CORS 仅正式产品源（ov.conf.template `cors_origins`，同源部署优先）。
- 日志/审计/埋点统一脱敏 Authorization/Cookie/API Key/密码/Token（06 §14.4；
  自动化断言见 tests/platform/test_audit_production.py）。
- 数据库备份加密 + 恢复审计：`deploy/product/scripts/backup-encrypt.sh`
  （pg_dump → gzip → GPG AES256；恢复需人工确认并留审计）；工具选型与完整
  演练归 P5-E3（§99.3 首个交付项）。

## Worker 调度（运维部署）

本 Epic 交付 Worker 状态可观测（last_result/last_run/running），周期调度由
部署层负责（与既有 ProvisioningWorker 同模式）。生产建议：
- ProvisioningWorker / PurgeWorker / SessionCleanupWorker 随 product-server
  进程周期运行（`run_once`/`run_periodically`），健康检查据 worker 状态
  报告运行与否。

## 验证清单（验收①）

| 步骤 | 验证方式 | 证据 |
| --- | --- | --- |
| 1 | `alembic upgrade head` 成功；`ov platform status` 显示 head 一致 | status 输出 |
| 2 | `ov platform init` 成功；重复执行退出码 1；库中 `$argon2id$` | 自动化测试 test_bootstrap_cmd.py |
| 3 | PSA 登录 → 建 Account；首位 Admin 初始密码一次展示 | 页面 + 审计 account.create |
| 4 | Worker 处理后 Account active；`ov platform status` backlog=0 | 审计 provision.account |
| 5 | Admin 直建 User 一次密码展示；无邀请路径 | 页面 + 审计 user.create |
| 6 | `/app/profile/api-keys` 建 Key，明文一次；库中仅 hash | 页面 + 审计 credential.create |
| 7 | SDK/CLI/MCP 用新 Key 走通 | 18.3 公网断言（P5-E4 收口） |
| 8 | `ov platform verify` 三凭证一致退出码 0 | verify 输出（自动化测试） |
