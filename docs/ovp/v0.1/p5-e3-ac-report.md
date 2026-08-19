# P5-E3 验收自检报告（备份恢复与回滚演练）

- **Epic**：P5-E3（14 号计划 §99.3，Plane：high）
- **分支**：`feature/platform-v0.1-p5-e3`（基 = origin/dev `50d14d5a`）
- **日期**：2026-08-19
- **提交**：见文末 commit 列表
- **测试命令**：`PYTHONPATH=. .venv/bin/python -m pytest tests/platform/ -p no:cacheprovider --no-cov`
  （需要 PG：`OV_PLATFORM_TEST_ADMIN_URL=postgresql://ov_platform:ov_platform_dev@127.0.0.1:55455/postgres`；
  加密备份冒烟另需 `OV_PLATFORM_TEST_PG_CONTAINER=ovp-pg16-p5e3`）
- **基线回归**：`tests/platform/` 全量 **567 通过（既有 560 + 本 Epic 新增 7）**，零破坏

## 验收标准逐条自检

| # | 验收标准 | 结论 | 证据 |
| --- | --- | --- | --- |
| ① | 至少一次完整备份-恢复演练并留存记录 | ✅ | `docs/ovp/v0.1/p5-e3-drill-record.md` 演练一（Drill A，2026-08-19T09:12:51Z，真实 PG 16.15 容器 55455）：备份（pg_dump -Fc→gzip→GPG AES256 + 数据卷 tar→gzip→GPG）→故障注入→恢复→18 项校验全绿；产物 `drill-results/full/`（report.json/verify-report.json/backups/*.enc.gz） |
| ② | 恢复后登录/Key/审计正常、PG 与数据卷一致（抽查 Resource/Skill/Session 无重复） | ✅ | verify-report.json：login_password ✅、session_resolve ✅、api_key_resolve ✅、audit_continuity ✅（9≥7）；count_* 与快照一致；no_dup_content_refs/no_dup_session_refs duplicates=0；数据卷恢复后 memory.md 内容比对一致（Drill A 步骤 5）；自动化：test_drill_verify_passes_without_fault / _detects_fault（校验逻辑正/负向） |
| ③ | 完成一次应用版本回滚演练、回滚后仍以 PG IAM 鉴权 | ✅ | 演练二（Drill B，09:17:02Z）：deploy-list 版本回退 v0.4.13→v0.4.12（deploy/VERSION + OVP_PRODUCT_IMAGE 机制）+ migration down/up（iam_users 3→3 数据保留）+ 登录/Session/API Key 全从 PG 解析成功（不回退旧 registry）；自动化：test_single_step_downgrade_upgrade_keeps_data、test_pg_credentials_authorize_after_downgrade_upgrade_cycle |
| ④ | 不可逆 migration 具备独立实例验证步骤且演练通过 | ✅ | 演练三（Drill C，09:17:54Z）：备份恢复到独立实例 `ov_platform_drill_verify` → 数据逐表一致 → 完整校验通过 → 主实例未受影响（iam_users=3/audit=10 不变）后才允许切流量（06 §15.4 步骤 4）；步骤固化于 p5-e3-runbook.md §5；当前 7 个迁移均含 down（可逆），流程以占位不可逆操作真实演练 |
| ⑤ | 备份加密生效、恢复操作有审计 | ✅ | 密文 GPG AES256（verify 子命令解密可读 + pg_restore --list 可解析，密文权限 600）；恢复操作双路审计：`iam_audit_events`（action=backup.restore，actor=system/backup-ops，`restore_audited` 校验通过）+ shell 侧 600 审计日志（ops-audit.log 完整留痕）；自动化：test_restore_audit_required（无恢复审计必须失败）、test_encrypted_backup_roundtrip（backup→verify→list 冒烟） |
| ⑥ | 恢复后 outbox/reconciler 一致、failed 可经重试接口幂等恢复 | ✅ | verify-report.json：no_dup_outbox duplicates=0、retry_failed_account（retry→Worker completed，failed_left=0）、retry_idempotent_no_dup（二次 retry 409、事件数不变）；自动化：test_retry_recovers_failed_and_keeps_single_event（409 守卫 + 无重复事件）；reconciler 既有用例回归（test_reconciler.py） |

## 交付物清单

- **选型文档（首个交付项）**：`docs/ovp/v0.1/p5-e3-backup-tooling.md`
  —— 备份/恢复工具与加密方案结论：pg_dump -Fc → gzip → GPG AES256；数据卷 tar；
  无需第三方备份软件，生产可平滑换托管 RDS。
- **生产脚本固化**：`deploy/product/scripts/backup-encrypt.sh` —— P5-E2 骨架按选型
  固化为生产可执行：新增 `backup-volume`/`restore-volume`/`verify`/`list` 子命令、
  非交互恢复（`OV_PLATFORM_BACKUP_CONFIRM=yes`）、双路审计
  （iam_audit_events + shell 600 审计日志）、`OV_PLATFORM_PG_TOOLS` docker exec 模式。
- **runbook**：`docs/ovp/v0.1/p5-e3-runbook.md` —— 发布前备份、
  向下兼容发布顺序/可验证 down、应用版本回滚（PG IAM 边界）、不可逆 schema 独立
  实例验证流程、恢复后一致性校验清单（06 §14.4/§15.4）。
- **版本回滚机制**：`deploy/product/VERSION` + docker-compose `OVP_PRODUCT_IMAGE`
  镜像 tag 回退。
- **演练剧本**：`deploy/product/scripts/drill-backup-restore.sh`（full/rollback/
  standalone 三种剧本）+ `drill_lib.py`（数据准备/故障注入/一致性校验，与自动化
  测试共用同一套逻辑）。
- **演练记录**：`docs/ovp/v0.1/p5-e3-drill-record.md`
  （07 §21 条目 12 证据；Drill A/B/C 步骤、校验明细、审计留痕、复现方法）。
- **测试**：`tests/platform/test_backup_restore.py`（4 例）、`test_rollback_drill.py`
  （3 例）。

## 对既有文件的改动说明

- `deploy/product/scripts/backup-encrypt.sh`（P5-E2 交付，本 Epic 增强）：
  - 修复 P5-E2 遗留缺陷：DSN 解析原实现（`pg_host`/`pg_port` 用 `${PG_DSN%@*}`
    + GNU sed `t` 分支）在带密码 DSN 与 BSD/macOS sed 下失效（会解析出错误
    host/端口甚至直接报错），改为纯 bash 参数展开解析；
  - custom 格式 pg_restore 需要 seekable 文件：docker 模式经 `docker cp` 进容器，
    `list`/`verify`/`restore` 不再走管道；
  - 恢复审计 INSERT 补 `id`（gen_random_uuid()）——原实现缺 id 会触发
    NOT NULL 违反并被静默吞掉（审计失效）；
  - 新增子命令与审计/确认/PG_TOOLS 机制（见上）。
- `deploy/product/docker-compose.yml`：product-server 增加 `image: ${OVP_PRODUCT_IMAGE:-...}`
  （版本回滚镜像 tag 机制，行为兼容，未改其他配置）。
- `deploy/product/VERSION`（新增）：部署版本标记，发布/回滚依据。
- 无产品代码（openviking/ 服务端）改动——本 Epic 为运维/演练交付，业务代码零变更。

## 遗留项（P0/P1/P2）

- **P2**：真实容器镜像级别的应用回滚演练（本地无镜像仓库，演练以 VERSION 标记 +
  migration down/up + PG 凭证校验执行；镜像 tag 回退机制已接线，生产执行时按
  runbook §4 复跑）。
- **P2**：告警通道（06 §14.6）v0.1 不部署（继承 P5-E2 遗留，Phase 6）。
- **P2（环境限制）**：备份恢复自动化定时调度未部署（脚本与 runbook 已就绪，
  调度归部署层，同 P5-E2 遗留说明）。
- **P2**：不可逆迁移当前不存在，Drill C 以占位流程演练；首个真实不可逆迁移落地时
  需按 runbook §5 执行并回填记录。

## Commit 列表（按 story 分批）

- `feat(platform): P5-E3 备份工具选型固化（首个交付项，§99.3）+ backup-encrypt.sh 生产化增强`
- `feat(platform): P5-E3 回滚 runbook + 部署版本标记 + 回滚演练测试（AC③，06 §15.4）`
- `feat(platform): P5-E3 演练剧本脚本（备份→故障注入→恢复→校验，AC①②④⑤⑥）`
- `fix(platform): P5-E3 演练脚本真实执行修复（bash 3.2 多字节解析、psql -c、
  DSN 双视角、非恢复演练审计开关）`
- `docs(platform): P5-E3 演练记录 + 验收自检报告（07 §21 条目 12 证据）`
