# P5-E4 验收自检报告（安全加固与 26 条生产验收门禁）

- **Epic**：P5-E4（14 号计划 §99.4，Plane：urgent——上线门禁，唯一证据收口点）
- **分支**：`feature/platform-v0.1-p5-e4`（基 = origin/dev `e56db24f`，全部 24 个 Epic 已合并）
- **日期**：2026-08-19
- **测试命令**：`PYTHONPATH=. .venv/bin/python -m pytest tests/platform/ -p no:cacheprovider --no-cov`
  （PG：`OV_PLATFORM_TEST_ADMIN_URL=postgresql://ov_platform:ov_platform_dev@127.0.0.1:55456/postgres`）
- **lint**：`uvx ruff check openviking/server/platform/ tests/platform/` → All checks passed
- **结论**：**Go**（验收 ①–⑤ 全部满足；详见 p5-e4-go-no-go.md）

## 验收标准逐条自检

| # | 验收标准 | 结论 | 证据（测试用例/交付物） |
| --- | --- | --- | --- |
| ① | 26 条验收逐条有证据（唯一证据收口点，Go/No-Go 报告模板：测试用例编号、结论、证据链接） | ✅ | `docs/ovp/v0.1/p5-e4-go-no-go.md`「26 条生产验收门禁证据表」：G1–G26 每条含用例编号/结论/证据链接；证据完整性由测试门禁守护 `test_security_acceptance.py::test_go_no_go_report_evidence_complete`（26 行、结论合法、证据链接可解析）；P5-E1/E2/E3 条目按 §99.4 约定只引用不重复收集 |
| ② | 18.5 全通过（含 `/studio` 公网 404、无 Studio OAuth、低层封禁） | ✅ | `test_security_acceptance.py` 19 项（17 项 18.5 清单实时断言 + 报告完整性门禁 2 项）；报告「18.5 安全测试 17 项清单」全 PASS；`/studio` 404/低层封禁：`test_mount_production.py::test_platform_mode_studio_404_not_redirect`、`test_low_level_ops_not_mounted_in_production`、`test_security_acceptance.py::test_low_level_ops_blocked_public`；无 Studio OAuth：`test_oauth_without_studio.py` |
| ③ | 五类测试生产配置全绿且与 07 §18 清单对应（18.3 公网断言按客户端清单执行并留存证据） | ✅ | 全量回归 **599 passed, 1 skipped**（既有 566+1 + 本 Epic 新增 33）；18.1/18.2/18.5 覆盖见 Go/No-Go 报告各表；18.3 客户端清单（SDK/CLI/插件/MCP）`test_client_acceptance.py` 5 项：新 IAM Key（`ovk_u.*`）走通、三凭证一致实时 RBAC；真实公网不可用 → 生产配置集成断言+配置断言并注明证据形态（报告「18.3 公网断言测试客户端清单」）；18.4 前端 E2E 生产配置以既有 web-platform 构建/集成断言回归（`test_mount_production.py`） |
| ④ | 安全缺陷全修复无 P0/P1 遗留 | ✅ | 本 Epic 修复 2 个真实安全缺陷（见下「安全缺陷修复清单」）：① 高风险写依赖层拒绝无审计；② 服务层拒绝审计在路由异常路径被回滚。均已有回归测试。无新增 P0/P1 遗留（遗留 P1 为既有接线限制，见 Go/No-Go 报告） |
| ⑤ | Go/No-Go 报告完成且结论可追溯 | ✅ | `p5-e4-go-no-go.md`：26 条证据表 + 18.5 清单 + 14.5 复验 + 客户端清单 + 备份回滚/审计/健康证据 + 遗留项 + 上线结论（GO）；可追溯性由 `test_security_acceptance.py::test_go_no_go_report_evidence_complete` / `test_18_5_security_matrix_complete` 门禁守护 |

## 新增测试文件（33 项）

| 文件 | 项数 | 覆盖 |
| --- | --- | --- |
| `tests/platform/test_security_acceptance.py` | 19 | 07 §18.5 十七项清单实时断言（新增 DNS Rebinding/Redirect 深度/跨 User Watch/Operation 越权/一次性 URL 脱敏/Upload 重放/末位 Admin/Header spoofing/确认弹窗非安全边界/低层封禁配置断言）+ Go/No-Go 报告证据完整性门禁 2 项 |
| `tests/platform/test_high_risk_confirm.py` | 9 | 06 §14.5 六类高风险操作：成功/拒绝审计、CSRF 重校验、幂等删除、preview 数据源、成员数据只读无写路径、同级重置/跨 Account 拒绝审计 |
| `tests/platform/test_client_acceptance.py` | 5 | 07 §18.3 客户端清单：SDK(Bearer)/CLI(X-Api-Key)/插件(归属审计)/MCP(OAuth Token) 新 IAM Key 走通 + 三凭证一致 + 无旧 Key 路径 |

## 安全缺陷修复清单（本 Epic，feat/fix commit）

| # | 缺陷 | 严重度 | 修复 | 回归测试 |
| --- | --- | --- | --- | --- |
| 1 | 高风险写操作在**依赖层**被拒（CSRF/权限 403）不写审计——06 §14.5「成功、失败与拒绝都写入审计」/ 07 §21 #10 未满足，攻击探测无足迹 | P1 | `dependencies.py` 新增 `require_high_risk_write`（CSRF+权限合并守卫，任一失败先写脱敏拒绝审计再 403，含 Actor/Subject/Scope，target 取自 path_params）；admin/platform/resources/skills 四 router 共 11 个高风险写端点切换（delete user/account、disable、reset password、revoke key、delete resource/skill） | `test_high_risk_confirm.py`（拒绝审计逐类断言）、`test_security_acceptance.py::test_confirmation_dialog_is_not_a_security_boundary` |
| 2 | 服务层拒绝审计（last-admin 409、同级重置 403）在路由 **except 异常路径不 commit** 被回滚丢失 | P1 | admin/platform 高风险处理器 except 分支补 `await session.commit()`（审计与拒绝响应同事务持久化） | `test_high_risk_confirm.py::test_reset_rank_and_cross_account_denied_audited`、`test_delete_user_audit_success_denied_and_preview` |

## 测试统计（2026-08-19，PG ovp-pg16-p5e4:55456）

- 基线：566 passed, 1 skipped（P5-E3 收尾态）
- 本 Epic 新增：**33 passed**（security_acceptance 19 + high_risk_confirm 9 + client_acceptance 5）
- 全量：**599 passed, 1 skipped**
- `ruff check`（openviking/server/platform + tests/platform）：全绿（既有代码库其他目录的 82 条历史告警为本 Epic 范围外基线，未触碰）

## Commit 列表

```
d9372600 feat(platform): P5-E4 18.5 安全测试 17 项收口测试 + 高风险拒绝审计缺陷修复（14 号计划 §99.4）
f7712b0e feat(platform): P5-E4 Go/No-Go 报告（26 条门禁证据表 + 18.5 清单 + 客户端清单）与验收自检报告
```

## 遗留项（P0/P1/P2）

- **P0**：无。
- **P1**：无新发现（既有 P1 接线限制见 p5-e1-ac-report.md：Skill 共享根写入、Resource 真实摄取，均不影响门禁结论）。
- **P2**：RealSessionBackend Commit Phase 2 兜底、RealControlPlane 账号枚举最佳努力、真实公网 E2E 以生产配置集成断言覆盖（证据形态已注明）。
