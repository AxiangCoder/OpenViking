"""删除回收与 Purge 公共基础设施（04 §10.11，05 §12.6 注，14 号计划 §97.2）。

- `service`：User/Account deletion-preview/DELETE（进入回收期 + deletion job
  ID）、回收站列表与类型化恢复权限映射（05 §12.6 注）；
- `worker`：Purge Worker（幂等物理清理，审计不随业务数据物理清理）。
"""
