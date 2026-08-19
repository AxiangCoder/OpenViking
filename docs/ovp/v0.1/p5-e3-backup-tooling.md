# P5-E3 备份工具选型（14 号计划 §99.3 首个交付项）

- **Epic**：P5-E3（备份恢复与回滚演练，Plane：high）
- **日期**：2026-08-19
- **范围**：备份/恢复工具与加密方案确认（06 §14.4「数据库备份加密，恢复操作审计」、
  06 §15.4「发布前备份 PostgreSQL 与 OpenViking 数据卷」）
- **结论先行**：**pg_dump/pg_restore（PG 16 自带）+ tar + GPG 对称 AES256**；
  无需引入第三方备份软件，生产可平滑换托管 RDS（快照/导出兼容）。

## 1. 数据库备份/恢复工具

| 维度 | 结论 | 理由 |
| --- | --- | --- |
| 备份工具 | `pg_dump -Fc`（custom 格式） | PG 16 官方自带、无额外依赖；custom 格式压缩、支持 `pg_restore --list` 校验与部分恢复；`--no-owner --no-privileges` 保证跨实例恢复不依赖角色名 |
| 恢复工具 | `pg_restore --clean --if-exists` | 幂等覆盖恢复（先删后建）；`--clean` 要求目标库仅含备份内对象，演练已验证 |
| 一致性 | 单库 `pg_dump -Fc` 快照一致 | 事务级快照（`--single-transaction` 语义由 pg_dump 默认保证），IAM/审计/outbox/业务表同点一致 |
| 定时 | 不内置调度器 | 由部署层 cron / 备份服务调用（v0.1 范围 Out：快照产品化仅私网运维，07 §21 条目 12 演练为本 Epic 目标） |
| 选型替代 | 托管 RDS 快照 | 生产若用托管 RDS，`pg_dump -Fc` 备份仍然适用；快照仅作补充，不改变恢复演练流程 |

### 恢复审计与校验（06 §14.4）

- 恢复操作必须审计：恢复前写 `backup.restore` 审计（`iam_audit_events`，
  action=`backup.restore`，result=`started`/`completed`/`failed`），同时写
  shell 侧 append-only 审计日志（`OV_PLATFORM_BACKUP_AUDIT_LOG`，权限 600）；
  DB 不可用时 shell 审计日志兜底。
- 备份本身留审计：action=`backup.created`（含文件名/大小/时间）。
- 完整性校验：`verify` 子命令 = GPG 解密 + `pg_restore --list` 解析 + 抽样
  表行数断言（`iam_accounts`/`iam_users`/`iam_audit_events` 等关键表行数
  记录于清单，恢复后对照）。

## 2. 数据卷备份/恢复

| 维度 | 结论 | 理由 |
| --- | --- | --- |
| 备份工具 | `tar`（GNU）+ `gzip` + GPG AES256 | 数据卷为 OpenViking workspace/vectordb/agfs（deploy/product compose `./data:/app/.openviking`）；tar 保持目录结构/权限/符号链接 |
| 一致性 | 先停写再 tar（短停写窗口）或接受弱一致 | 数据卷为 Embedding 缓存/工作目录，非事务事实来源（事实来源是 PG）；发布窗口内停机备份即一致（06 §15.4 步骤 1 为发布前备份） |
| 恢复 | 同 tar 解包到原挂载点 | `restore-volume` 校验备份内路径前缀，防止路径逃逸 |
| 保留策略 | 每轮演练/发布前保留 1 份 PG + 1 份卷备份（滚动 7 份） | 密文 gzip（PG dump 一般可压 5–8 倍）；过期由部署层清理 |

## 3. 加密方案

- **GPG 对称加密 AES256**（复用 P5-E2 `deploy/product/scripts/backup-encrypt.sh`）：
  - 口令经环境变量 `OV_PLATFORM_BACKUP_PASSPHRASE` 注入（Secret Manager 推荐），
    不落盘、不写脚本、不进镜像；
  - `--pinentry-mode loopback` 适合无交互运维；
  - 密文文件权限 `600`，明文只在临时路径存在且用完即删。
- 与 P5-E2 脚本关系：E2 交付骨架（backup/restore 子命令），本 Epic 依据选型
  结论固化为生产脚本：新增 `backup-volume`/`restore-volume`/`verify` 子命令、
  非交互确认（`OV_PLATFORM_BACKUP_CONFIRM=yes`，供演练与自动化）、审计日志
  与审计表写入（06 §14.4）。

## 4. 演练剧本（与本文件配套）

真实执行与结果见 `p5-e3-drill-record.md`；剧本脚本：
`deploy/product/scripts/drill-backup-restore.sh`（备份→故障注入→恢复→校验）、
`deploy/product/scripts/drill_lib.py`（演练数据准备与一致性校验，供脚本与
自动化测试复用）。
