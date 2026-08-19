# P5-E2 验收自检报告（一次性初始化与生产运维可观测性）

- **Epic**：P5-E2（14 号计划 §99.2，Plane：high）
- **分支**：`feature/platform-v0.1-p5-e2`（基 = origin/dev `6d7235bc`）
- **日期**：2026-08-19
- **提交**：见文末 commit 列表；证据均为自动化测试，命令：
  `PYTHONPATH=. .venv/bin/python -m pytest tests/platform/ -p no:cacheprovider --no-cov`
  （需要 PG：`OV_PLATFORM_TEST_ADMIN_URL=postgresql://ov_platform:ov_platform_dev@127.0.0.1:55454/postgres`）
- **基线回归**：`tests/platform/` 全量 **560 通过**（既有 529 + 本 Epic 新增 31，零破坏）

## 验收标准逐条自检

| # | 验收标准 | 结论 | 证据（测试用例/交付物） |
| --- | --- | --- | --- |
| ① | 全新环境按 15.2 八步完整走通且文档化可重复 | ✅ | `docs/design/product-platform/v0.1/p5-e2-init-runbook.md`（八步 runbook + 验证清单）；`ov platform init/status/verify` CLI 端到端冒烟通过（迁移→建 PSA→幂等拒绝→巡检，见 bootstrap_cli.py）；步骤 3–8 复用例：test_bootstrap_cmd.py::test_verify_three_credentials_consistent（步骤 8）、test_admin_service/test_provisioning 既有用例（步骤 3–5）、test_api_keys 既有用例（步骤 6） |
| ② | 初始化命令幂等、密码仅一次展示、库中仅 Argon2id hash | ✅ | test_bootstrap_cmd.py::test_init_creates_psa_password_printed_once（生成密码仅 `>>> ... <<<` 一次展示、`$argon2id$` 可校验）、test_init_password_env_injected_not_printed（env 注入不打印明文、库中 Argon2id）、test_init_idempotent_rejection（重复执行退出码 1、不覆盖密码、PSA 唯一）；CLI 冒烟 exit=1 验证 |
| ③ | 无旧 Key 导入路径、新 Key 均为 `ovk_u.*` 由 IAM 签发 | ✅ | test_bootstrap_cmd.py::test_keys_issued_by_iam_are_ovk_u_prefixed（`ovk_u.<public_id>.<secret>` 三段式、库中仅 SHA-256+末四位）；无任何旧 registry 导入/双写路径（06 §15.1：PG 为唯一事实来源，`resolve_api_key_principal` 只接受 `ovk_u.` 前缀，见 principals.py:181） |
| ④ | `/health`、`/ready` 在 PG 故障/migration 落后/backlog 超阈值/Purge 停滞时非 ready 且含可读诊断（阈值可精确复测） | ✅ | tests/platform/test_health_ready.py：test_ready_not_ready_when_pg_down（503+`unreachable`）、test_migration_lag_not_ready（落后 1 版 error+lag 诊断）、test_provisioning_backlog_over_threshold_not_ready（101>100 error；自定义阈值 150 复测 ok）、test_purge_pending_over_max_not_ready（501>500；max_pending=1000 复测 ok）、test_purge_stall_by_earliest_purge_after（落后 5d>3d；max_stall_days=7 复测 ok）、test_purge_pending_without_worker_not_ready（Worker 未运行）、test_ready_not_ready_when_backlog_over_threshold（HTTP 503+provisioning 明细）、test_health_non_ready_in_platform_mode（/health 503 not_ready）、test_health_ready_in_platform_mode / test_ready_platform_ok_when_checks_pass（正向 200）；阈值入 `PlatformConfig`（06 §16.3 一致：100/1/500/3 天） |
| ⑤ | 17.1 每类事件有脱敏审计、跨用户事件含 Actor+Subject | ✅ | test_audit_production.py::test_17_1_event_categories_produce_masked_audit（登录成功/失败、登出、Key 创建/撤销、User 创建/禁用/启用、密码重置、角色提升、Account 创建、Provisioning、Purge 全部有审计；全库审计不含口令/Key 明文/完整 hash/Token）、test_cross_user_events_have_actor_and_subject（重置密码事件 actor=PSA、subject=alice、scope/platform、authentication_method 完整、metadata 无新密码） |
| ⑥ | 日志/审计/埋点无密码/Cookie/Key 明文/完整 hash/Token | ✅ | 同上脱敏断言（扫描审计全字段）；06 §14.4 统一脱敏既有实现（auth/log 层）随本 Epic 回归；备份加密脚本不留明文（backup-encrypt.sh）；P5-E1 AC⑥ 扫描测试继续通过 |
| ⑦ | 三角色经 Session、API Key 与 OAuth 三凭证获得一致权限与数据范围 | ✅ | test_bootstrap_cmd.py::test_verify_three_credentials_consistent（同一用户 Session/API Key/OAuth 解析一致，`ov platform verify` 退出码 0）、test_verify_mismatch_fails（目标用户不一致退出码 1）；三角色（PSA/Account Admin/User）权限与数据范围由既有 test_permission_catalog/test_rbac/test_tenant_isolation 回归 |

## 交付物清单

- **初始化命令**：`openviking/server/platform/bootstrap_cli.py`（`init`/`status`/`verify`），
  经 `openviking_cli/rust_cli.py` 接入 `ov platform`；`AuthService.bootstrap_platform_super_admin`
  新增 env 密码注入（`OV_PLATFORM_INIT_PSA_PASSWORD`）。
- **runbook**：`docs/design/product-platform/v0.1/p5-e2-init-runbook.md`（15.2 八步 + 可观测性 +
  生产化 + 验证清单）。
- **健康检查**：`openviking/server/platform/health.py`（17.3 五维检查 + 阈值判定）；
  system.py `/health`、`/ready` 平台模式扩展（503+可读诊断）；`PlatformConfig` 新增
  4 个阈值配置项（06 §16.3）；PurgeWorker 可观测状态（running/last_run_at/last_processed）。
- **审计/日志关联**：Provisioning outbox payload 携带 request_id → Worker 审计回填
  （17.2 HTTP→审计→后台任务贯通）；跨用户重置密码审计补 authentication_method/
  actor_credential_id（17.2 字段完整）。
- **部署配置**：`deploy/product/config/README.md`（init 命令、健康阈值 env、备份说明）；
  `deploy/product/scripts/backup-encrypt.sh`（pg_dump→gzip→GPG AES256 加密备份 +
  恢复审计，14.4；选型/演练归 P5-E3）。
- **测试**：`tests/platform/test_bootstrap_cmd.py`（7 例）、`test_health_ready.py`（20 例）、
  `test_audit_production.py`（4 例）。

## 对既有文件的改动说明

- `openviking/server/platform/auth/service.py`：`bootstrap_platform_super_admin` 新增
  `password` 参数（env 注入，缺省仍随机仅一次）；`reset_user_password` 新增
  `actor_authentication_method`/`actor_credential_id`（审计字段完整，append-only）。
- `openviking/server/platform/provisioning/service.py`：`create_provisioning_events` 新增
  `request_id` 参数并写入 outbox payload；`process_event`/`_append_system_audit` 回填
  request_id（17.2）。
- `openviking/server/platform/admin/service.py`：`create_account_with_first_admin` 透传
  request_id 到 provisioning；`reset_user_password` 透传认证方式/凭证 ID。
- `openviking/server/platform/deletion/worker.py`：PurgeWorker 增加 `running`/
  `last_run_at`/`last_processed` 可观测状态（run_once 刷新）。
- `openviking/server/platform/mount.py`：装配 `SessionCleanupWorker` +
  `PlatformHealthChecks` 到 app.state（worker 周期调度仍由部署层负责，同
  ProvisioningWorker 模式，runbook 已注明）。
- `openviking/server/routers/system.py`：`/health`、`/ready` 消费
  `app.state.platform_health_checks`；非平台模式行为零变化（tests/server 回归）。
- `openviking_cli/rust_cli.py`：Python-native 子命令 `ov platform init|status|verify`。
- `openviking/server/platform/config.py`：`OV_HEALTH_*` 四项阈值（仅 append）。
- `deploy/product/config/README.md`、`deploy/product/scripts/backup-encrypt.sh`（新增）。

## 遗留项（P0/P1/P2）

- **P1**：告警通道（06 §14.6 清理失败必须告警）v0.1 不部署（§99.2 范围 Out），以
  `/ready` Purge 停滞判定 + 审计为准，告警随 Phase 6。
- **P2**：Session cleanup / Provisioning / Purge Worker 的周期调度在部署层接线
  （本 Epic 交付状态可观测；runbook 注明运维职责）。
- **P2**：真实备份-恢复与回滚演练归 P5-E3（§99.3）；本 Epic 交付加密备份脚本骨架。
- **P2（环境限制）**：真实 SDK/CLI/MCP 客户端三凭证 E2E 归 P5-E4 公网断言
  （18.3 客户端清单），本 Epic 以服务级解析一致性测试覆盖。

## Commit 列表（按 story 分批）

- `feat(platform): P5-E2 bootstrap 正式部署命令（ov platform init/status/verify，06 §15.2 八步初始化，AC①②③⑦）`
- `feat(platform): P5-E2 /health /ready 扩展 + 06 §16.3 阈值（17.3，AC④）`
- `feat(platform): P5-E2 17.1 审计清单生产验证 + 17.2 Request ID 贯通（AC⑤⑥）`
- `feat(platform): P5-E2 14.4 生产化配置（备份加密脚本）+ 初始化 runbook + 验收自检报告`
