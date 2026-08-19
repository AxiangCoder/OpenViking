"""Content Registry（04 §10.10/§10.12/§10.14）：产品对象注册表基础设施。

- `tags`：规范化标签校验（04 §10.10：≤20 项、每项 ≤40 字符、严格 `key=value`、
  key/value 均非空、整体小写并去重；05 §12.5 统一强制）；
- `repository`：`platform_content_refs`/`platform_operation_refs`/
  `platform_uploads`/`iam_deletion_jobs` 数据访问；
- `service`：对外创建（provisioning→active/failed）、Idempotency-Key 事务、
  Upload 原子消费、Operation generation。
"""
