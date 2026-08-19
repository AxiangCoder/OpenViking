# P5-E3 备份恢复与回滚演练记录（14 号计划 §99.3，07 §21 条目 12）

- **Epic**：P5-E3（备份恢复与回滚演练，Plane：high）
- **日期**：2026-08-19（UTC）
- **环境**：
  - 演练库容器 `ovp-pg16-p5e3`（PostgreSQL 16.15，alpine），宿主端口 55455；
  - PG 工具经 `OV_PLATFORM_PG_TOOLS='docker exec -i ovp-pg16-p5e3'` 执行
    （宿主机无 pg 二进制，容器内工具经 host.docker.internal 回访宿主发布端口）；
  - 演练库：`ov_platform_drill`（主实例）、`ov_platform_drill_verify`（独立实例）；
  - 加密：GPG AES256，`OV_PLATFORM_BACKUP_PASSPHRASE` 环境注入；
  - 剧本：`deploy/product/scripts/drill-backup-restore.sh` + `drill_lib.py`
    （真实演练与自动化测试共用同一套数据准备/校验逻辑）。
- **证据产物**：`drill-results/full/`（seed.json / snapshot.json / verify-report.json /
  standalone-verify.json / backups/*.enc.gz / audit/ops-audit.log / report.json）。

## 演练一（Drill A）完整备份-恢复演练 —— 通过

时间：2026-08-19T09:12:51Z（report.json result=PASS）

| 步骤 | 动作 | 结果 |
| --- | --- | --- |
| 0 | 重建 `ov_platform_drill`：迁移 head（8 版 a1b2c3d4e5f6→e5f6a7b8c9d2）+ `ov platform init`（PSA） | PASS |
| 1 | 业务数据准备（drill_lib prepare）：acme Account + 首位 Admin（真实 AdminService 路径，同事务写 outbox）、alice User + 登录 Session + 具名 API Key（ovk_u.*）、Resource/Skill/Session 引用、审计事件、failed outbox + failed Account | PASS（seed.json） |
| 2 | 基准快照（9 张表计数：accounts=2/users=3/roles=3/credentials=1/sessions=1/audit=7/outbox=4/content_refs=2/session_refs=1） | PASS |
| 3 | 备份：`backup-encrypt.sh backup`（pg_dump -Fc→gzip→GPG）+ `backup-volume`（tar→gzip→GPG）+ `verify`（解密可读、pg_restore --list 可解析）；双路审计（iam_audit_events + ops-audit.log 600） | PASS（备份 12,334B 密文） |
| 4 | 故障注入：删除 alice/admin（级联清凭证/Session/角色/审计/引用）+ 删除 broken outbox 事件 + 删除数据卷文件 | PASS |
| 5 | 恢复：`restore`（非交互，解密→pg_restore --clean）+ `restore-volume`（路径前缀逃逸校验后解包） | PASS |
| 6 | 一致性校验（drill_lib verify，18 项全绿）： | PASS |

校验明细（verify-report.json）：

| 校验项 | 结果 | 说明 |
| --- | --- | --- |
| login_password | ✅ | alice 密码登录成功（Argon2id 校验，PG 侧） |
| session_resolve | ✅ | 备份前 Session token 恢复后仍解析（PG iam_sessions） |
| api_key_resolve | ✅ | 备份前 `ovk_u.*` Key 恢复后仍解析（SHA-256 hash 校验） |
| audit_continuity | ✅ | 恢复后 audit=9 ≥ 备份时 7（含恢复事件） |
| restore_audited | ✅ | iam_audit_events 含 backup.restore（恢复操作有审计，06 §14.4） |
| count_*（8 表） | ✅ | 与快照一致（sessions 2==1+1：校验登录新增 1 条） |
| no_dup_content_refs / no_dup_session_refs / no_dup_outbox | ✅ | Resource/Skill/Session/outbox 无重复对象（AC②） |
| retry_failed_account | ✅ | failed outbox 经 retry → Worker 跑通 completed（failed_left=0，AC⑥） |
| retry_idempotent_no_dup | ✅ | 二次 retry → 409，事件数不变（幂等无重复，05 §12.6） |

## 演练二（Drill B）应用版本回滚演练 —— 通过

时间：2026-08-19T09:17:02Z（report.json result=PASS）

| 步骤 | 动作 | 结果 |
| --- | --- | --- |
| 0 | 重建演练库 + 业务数据（同 Drill A） | PASS |
| 1 | 记录发布版本（模拟 v0.4.13+p5e3，deploy-list.json） | PASS |
| 2 | 可验证 down：`alembic downgrade e5f6a7b8c9d1`（head 前驱）→ `upgrade head`，iam_users 计数 3→3（数据保留，06 §15.4 步骤 2） | PASS |
| 3 | 回滚版本标记：镜像 tag 切回 v0.4.12（deploy/VERSION + OVP_PRODUCT_IMAGE 机制） | PASS |
| 4 | 回滚后仍以 PG IAM 鉴权：登录/Session/API Key 全部从 PG 解析成功（不回退旧 JSON registry，05 §11.3 单一事实来源） | PASS |

> 注：`downgrade -1` 相对修订在 alembic CLI 下会被解析为 base（全量回退），
> 演练与自动化测试均使用显式前驱 revision（`_previous_revision()`）。

## 演练三（Drill C）不可逆 schema 独立实例验证演练 —— 通过

时间：2026-08-19T09:17:54Z（report.json result=PASS）

| 步骤 | 动作 | 结果 |
| --- | --- | --- |
| 0 | 独立实例 `ov_platform_drill_verify`：迁移 + 恢复 Drill A 的加密备份（主实例未触碰） | PASS |
| 1 | 独立实例数据与备份快照逐表一致（audit 因恢复事件新增除外） | PASS |
| 2 | 不可逆变更占位演练：在独立实例执行完整校验（登录/凭证/审计/无重复/retry），验证通过 | PASS（standalone-verify.json） |
| 3 | 主实例 `ov_platform_drill` 未受影响（iam_users=3/audit=10 与演练前一致）——验证通过后才允许切流量/执行不可逆变更（06 §15.4 步骤 4） | PASS |

> 当前 7 个迁移全部具备 down（可逆）；本演练固化「不可逆 schema → 备份恢复
> 独立实例 → 验证 → 切流量」流程，未来出现不可逆迁移时按此执行（流程真实可重复）。

## 审计留痕（ops-audit.log，权限 600）

```text
[2026-08-19T09:12:53Z] backup-encrypt.sh kind=backup result=created operator=rentianxiang detail="ov_platform_20260819-091253Z.dump.gz.gpg (12334 bytes)"
[2026-08-19T09:12:54Z] backup-encrypt.sh kind=backup-volume result=created operator=rentianxiang detail="ov_volume_20260819-091254Z.tar.gz.gpg src=vol"
[2026-08-19T09:13:00Z] backup-encrypt.sh kind=restore result=started operator=rentianxiang detail="ov_platform_20260819-091253Z.dump.gz.gpg"
[2026-08-19T09:13:00Z] backup-encrypt.sh kind=restore result=completed operator=rentianxiang detail="ov_platform_20260819-091253Z.dump.gz.gpg"
[2026-08-19T09:13:01Z] backup-encrypt.sh kind=restore-volume result=started operator=rentianxiang detail="ov_volume_20260819-091254Z.tar.gz.gpg"
[2026-08-19T09:13:01Z] backup-encrypt.sh kind=restore-volume result=completed operator=rentianxiang detail="ov_volume_20260819-091254Z.tar.gz.gpg"
```

同一批动作同步写入 `iam_audit_events`（action=backup.created / backup.restore，
actor=system/backup-ops），恢复后校验 `restore_audited` 通过（AC⑤ 证据）。

## 复现方法

```bash
export OV_PLATFORM_TEST_ADMIN_URL='postgresql://ov_platform:ov_platform_dev@127.0.0.1:55455/postgres'
export OV_PLATFORM_BACKUP_PASSPHRASE='<口令>' OV_PLATFORM_PG_TOOLS='docker exec -i ovp-pg16-p5e3'
./deploy/product/scripts/drill-backup-restore.sh full       --workdir drill-results/full
./deploy/product/scripts/drill-backup-restore.sh rollback    --workdir drill-results/rollback
./deploy/product/scripts/drill-backup-restore.sh standalone  --backup drill-results/full/backups/ov_platform_*.dump.gz.gpg --workdir drill-results/full
```
