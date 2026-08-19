# P5-E3 备份恢复与回滚 runbook（06 §14.4/§15.4，14 号计划 §99.3）

- **Epic**：P5-E3（备份恢复与回滚演练）
- **配套**：`deploy/product/scripts/backup-encrypt.sh`（PG 加密备份/恢复/校验 +
  数据卷备份/恢复 + 双路审计）；`deploy/product/VERSION`（部署版本标记）；
  `deploy/product/docker-compose.yml`（`OVP_PRODUCT_IMAGE` 镜像 tag 回退）；
  `deploy/product/scripts/drill-backup-restore.sh` + `drill_lib.py`（演练剧本与校验）
- **原则**（06 §15.1/§15.4）：PostgreSQL 是唯一身份事实来源；回滚以同一套 PG IAM
  为边界——**回滚应用版本后仍使用 PG 用户和凭证，不回退旧 JSON registry 门禁**；
  发布前必须备份 PG 与数据卷；schema 变更必须向下兼容发布顺序或可验证 down；
  凭证 schema 若发生不可逆变化，先恢复备份到独立实例验证再切流量。

## 1. 备份（发布前强制，06 §15.4 步骤 1）

```bash
export OV_PLATFORM_DATABASE_URL='postgresql://ov_platform:CHANGE_ME@postgresql:5432/ov_platform'
export OV_PLATFORM_BACKUP_PASSPHRASE='<Secret Manager 注入>'   # GPG 对称密钥
export OV_PLATFORM_BACKUP_CONFIRM=yes                           # 恢复非交互（演练/自动化）
export OV_PLATFORM_BACKUP_AUDIT_LOG=/var/log/ovp/ops-audit.log
export OV_PLATFORM_BACKUP_AUDIT_DB_URL="$OV_PLATFORM_DATABASE_URL"  # 审计同步写 iam_audit_events
# 本地无 pg 工具时（工具在 PG 容器内）：
export OV_PLATFORM_PG_TOOLS='docker exec -i ovp-postgresql'
# Docker Desktop 下容器内访问宿主已发布端口用 host.docker.internal

./backup-encrypt.sh backup /var/backups/ovp            # PG 全量（pg_dump -Fc → gzip → GPG）
./backup-encrypt.sh backup-volume ./data /var/backups/ovp   # 数据卷（tar → gzip → GPG）
./backup-encrypt.sh verify /var/backups/ovp/ov_platform_<ts>.dump.gz.gpg   # 可解密 + 可列出
```

- 密文权限 600；口令只经环境变量注入（06 §14.4），明文不留盘。
- 每次 backup/restore 自动写审计：`iam_audit_events`（action=backup.created /
  backup.restore，actor=system/backup-ops）+ shell 侧 600 审计日志（append-only）。

## 2. 恢复（覆盖式，用于故障/误操作恢复）

```bash
./backup-encrypt.sh restore /var/backups/ovp/ov_platform_<ts>.dump.gz.gpg
./backup-encrypt.sh restore-volume /var/backups/ovp/ov_volume_<ts>.tar.gz.gpg <数据卷父目录>
```

- `restore` 先写 `restore started` 审计再执行；`--clean --if-exists --no-owner` 幂等覆盖。
- `restore-volume` 校验归档条目无绝对路径/`..` 逃逸后解包到父目录。
- 恢复后必须做一致性校验（§6），并在 `iam_audit_events` 留 `restore completed`。

## 3. 发布顺序（06 §15.4 步骤 2：向下兼容或可验证 down）

原则：**先迁移后应用，回滚先应用后 down**。当前全部 7 个迁移均有 `downgrade`
（可验证 down：`test_rollback_drill.py::test_single_step_downgrade_upgrade_keeps_data`）。

```bash
# 发布：
#   1) 发布前备份（§1）
#   2) alembic upgrade head（迁移向前兼容：新列有默认值、新表不破坏旧查询）
#   3) 更新 deploy/product/VERSION，构建并推送 OVP_PRODUCT_IMAGE=<tag>，docker compose up
#   4) /health /ready 绿 + ov platform status 巡检
# 回滚（若新版本异常）：
#   1) docker compose 以 OVP_PRODUCT_IMAGE=<上版 tag> up（应用先回退）
#   2) 若需撤 schema：alembic downgrade -1（可验证 down；先验证无数据依赖）
#   3) 校验 PG 凭证仍生效（§5），/ready 绿
```

## 4. 应用版本回滚演练（06 §15.4 步骤 3，验收③）

以同一套 PG IAM 为边界：回滚前后登录 Session / API Key 全部继续由 PG
`iam_users`/`iam_sessions`/`iam_api_credentials` 解析（05 §11.3 单一事实来源；
平台 Principal Resolver 无旧 JSON registry 读取路径）。回滚只切换应用镜像
（`OVP_PRODUCT_IMAGE` 上一 tag）+ 可选 `alembic downgrade`；**不回退旧 registry，
不重放旧门禁**。验证命令：

```bash
# 回滚后用 PG 内同一凭证解析（三凭证一致，P5-E2 verify）：
export OV_PLATFORM_VERIFY_EMAIL=... OV_PLATFORM_VERIFY_SESSION=... \
       OV_PLATFORM_VERIFY_API_KEY=...
python -m openviking.server.platform.bootstrap_cli verify   # 退出码 0 = PG IAM 生效
```

自动化：`test_rollback_drill.py::test_pg_credentials_authorize_after_downgrade_upgrade_cycle`。

## 5. 不可逆 schema 变更流程（06 §15.4 步骤 4，验收④）

凭证表（`iam_users.password_hash` / `iam_api_credentials.key_hash` 等）一旦
不可逆（如 hash 算法迁移、列约束收紧），流程为：

1. 发布前备份（§1）。
2. **恢复备份到独立实例**：在独立 PG 实例/库（同版本 PG 16）执行
   `backup-encrypt.sh restore <备份>`（演练用 `ov_platform_drill_verify` 库）。
3. 在独立实例上执行不可逆变更并验证（数据、鉴权、审计样本）。
4. 验证通过后才在主实例执行变更并切流量；主实例仅在独立实例验证通过后触碰。
   **演练证明**：`p5-e3-drill-record.md` Drill C —— 主实例全程只读，独立实例
   恢复+变更+校验通过后主实例才允许执行同样操作（当前无不可逆迁移，演练以
   占位不可逆操作模拟，流程真实可重复）。

## 6. 恢复后一致性校验（验收②⑥，drill_lib.py 提供 `verify_restore`）

- 登录/Key 可解析：PSA/User 密码 Argon2id 可校验、API Key SHA-256 可解析；
- 审计连续性：备份时刻前的审计事件数一致，且恢复后新增 restore 审计；
- 业务数据一致：Resource/Skill/Session 相关表（`platform_content_refs`/
  `platform_session_refs` 等）计数与备份清单一致、抽查无重复；
- outbox 一致：无重复 `(event_type, aggregate_id, status)` 组合、无孤儿
  processing/failed 悬置；failed 事件经 `provisioning/retry` 幂等恢复
  （重复重试不产生重复事件）。

## 7. 演练（真实执行记录见 p5-e3-drill-record.md）

```bash
export OV_PLATFORM_TEST_ADMIN_URL=... # 本 Epic 演练用 ovp-pg16-p5e3 (55455)
./deploy/product/scripts/drill-backup-restore.sh --db ov_platform_drill --workdir <dir>
```

剧本：①准备业务数据（PSA/Account/User/Key/Session/Resource/Skill/审计/outbox）
→ ②备份（PG+卷）→ ③故障注入（删用户/清审计/清数据卷）→ ④恢复（PG+卷）
→ ⑤校验（§6）→ ⑥报告 PASS/FAIL 落盘。另含独立实例验证（Drill C）与
版本回滚（Drill B，VERSION + migration down/up + 凭证校验）。
