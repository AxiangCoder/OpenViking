# P5-E4 上线 Go/No-Go 报告（26 条生产验收门禁 + 18.5 安全测试）

- **Epic**：P5-E4（14 号计划 §99.4，Plane：urgent——上线门禁，唯一证据收口点）
- **分支**：`feature/platform-v0.1-p5-e4`（基 = origin/dev `e56db24f`，24 个 Epic 已合并）
- **日期**：2026-08-19
- **测试命令**：`PYTHONPATH=. .venv/bin/python -m pytest tests/platform/ -p no:cacheprovider --no-cov`
  （PG：`OV_PLATFORM_TEST_ADMIN_URL=postgresql://ov_platform:ov_platform_dev@127.0.0.1:55456/postgres`）
- **结论**：**GO**（26/26 PASS；18.5 全 17 项 PASS；无 P0/P1 遗留；遗留 P1 项为既有接线限制，见下）

---

## 26 条生产验收门禁证据表

> 证据收口约定：P5-E1/E2/E3 已收集的 §21 引用条目（6/7/12 及各自条目）为本
> 生产复验输入，不重复收集，直接引用其测试/交付物；P5-E4 新增证据为本报告
> 新增安全测试文件。每条证据格式：测试用例编号、结论、证据链接（file::test）。

| 用例编号 | 结论 | 证据链接（tests/platform/，除注明外） |
| --- | --- | --- |
| G1 | PASS | 产品登录进 `/app`、浏览器不依赖 API Key、明文仅一次：`test_api_keys.py::test_create_returns_full_key_once_format_valid`、`test_api_keys.py::test_list_returns_metadata_only_no_plaintext`、`test_admin_api.py::test_admin_creates_user_role_fixed`（密码仅一次不可再取）；13 §82.4 一次性明文见 `test_api_keys.py::test_create_returns_full_key_once_format_valid` |
| G2 | PASS | `/admin` 管理本 Account User、三内置角色只读、不能建/编/删角色或提升 Admin：`test_admin_api.py::test_admin_lists_users_with_roles`、`test_admin_api.py::test_admin_roles_three_builtin`、`test_admin_api.py::test_reset_same_rank_403`、`test_admin_api.py::test_psa_admin_path_has_no_account_context` |
| G3 | PASS | 三角色 Permission 与数据范围后端真实生效：`test_permission_catalog.py::test_catalog_covers_design_91`、`test_permission_catalog.py::test_three_builtin_roles_defined`、`test_admin_api.py::test_plain_user_cannot_access_admin_api`、`test_tenant_isolation.py::test_cross_account_idor_denied` |
| G4 | PASS | 管理员读成员数据、审计 Actor+Subject：`test_audit_production.py::test_cross_user_events_have_actor_and_subject`、`test_admin_api.py::test_admin_audit_events_account_scoped`、`test_sessions_api.py::test_member_readonly_sessions_admin`、`test_sessions_api.py::test_member_readonly_sessions_platform` |
| G5 | PASS | 身份仅服务端解析、客户端身份字段无效：`test_principal_resolver.py::test_session_and_api_key_resolve_to_same_identity`、`test_principal_resolver.py::test_key_does_not_encode_identity`、`test_security_acceptance.py::test_header_spoofing_ignored_scope`（P5-E4 新增）、`test_tenant_isolation.py::test_header_spoofing_ignored` |
| G6 | PASS | 跨 Account/User/Header spoofing/IDOR 全通过：`test_tenant_isolation.py::test_cross_account_idor_denied`、`test_tenant_isolation.py::test_cross_user_idor_denied`、`test_tenant_isolation.py::test_header_spoofing_ignored`、`test_admin_api.py::test_reset_cross_account_404`、`test_security_acceptance.py::test_header_spoofing_ignored_scope`（P5-E4 新增） |
| G7 | PASS | 生产公网不挂 `/studio`；SDK/CLI/插件/MCP 用新 Key/OAuth：`test_mount_production.py::test_platform_mode_no_studio_route`、`test_mount_production.py::test_platform_mode_studio_404_not_redirect`、`test_mount_production.py::test_route_table_16_2`、`test_oauth_without_studio.py::test_oauth_routes_mounted_without_studio`；客户端清单见本报告「18.3 公网断言客户端清单」 |
| G8 | PASS | 三凭证同一套实时 RBAC、渠道不能切换身份：`test_api_keys.py::test_session_and_api_key_have_same_identity_and_permissions`、`test_oauth_principal.py::test_three_credentials_resolve_same_semantics`、`test_oauth_principal.py::test_oauth_principal_reflects_permission_changes_immediately`、`test_bootstrap_cmd.py::test_verify_three_credentials_consistent`、`test_security_acceptance.py::test_login_rotation_no_session_fixation`（P5-E4 新增） |
| G9 | PASS | 禁用/改密/角色变更即时生效：`test_admin_api.py::test_disable_kills_sessions_and_keys`、`test_api_keys.py::test_disabled_user_keys_and_session_rejected`、`test_auth_api.py::test_password_change_rotates_cookie_old_cookie_dead`、`test_oauth_principal.py::test_oauth_principal_reflects_permission_changes_immediately`、`test_principal_resolver.py::test_disabled_user_blocks_session_and_all_keys` |
| G10 | PASS | 管理与高风险操作全审计：`test_audit_production.py::test_17_1_event_categories_produce_masked_audit`、`test_security_acceptance.py::test_confirmation_dialog_is_not_a_security_boundary`（P5-E4 新增）、`test_high_risk_confirm.py`（P5-E4 新增，14.5 全量） |
| G11 | PASS | Provisioning 失败可见、可重试、不重复：`test_provisioning.py`（active 重试 409 守卫）、`test_reconciler.py`、`test_audit_production.py::test_17_1_event_categories_produce_masked_audit`（provisioning.retry 审计） |
| G12 | PASS | 备份恢复与版本回滚演练：`test_backup_restore.py::test_drill_verify_passes_without_fault`、`test_backup_restore.py::test_encrypted_backup_roundtrip`、`test_rollback_drill.py::test_single_step_downgrade_upgrade_keeps_data`、`test_rollback_drill.py::test_pg_credentials_authorize_after_downgrade_upgrade_cycle`；演练记录 `docs/ovp/v0.1/p5-e3-drill-record.md` |
| G13 | PASS | 软删 30 天恢复、期满物理清理、审计保留、Skill 冲突失败：`test_deletion_jobs.py::test_purge_after_defaults_to_30_days`、`test_deletion_jobs.py::test_purge_worker_idempotent_and_preserves_audit`、`test_sessions_api.py::test_soft_delete_hides_and_blocks_writes`、`test_resource_deletion.py::test_purge_worker_physical_cleanup_after_30_days`、`test_skill_api.py`（Skill 恢复冲突失败） |
| G14 | PASS | 用户与 Account Admin 不能切换 Account：`test_auth_api.py::test_password_change_rotates_cookie_old_cookie_dead`（无 Account 切换机制）、`test_api_keys.py::test_cookie_wins_over_bearer_at_http_level`、13 §92 页面级验收（web-platform 无 Account 切换入口，`docs/ovp/v0.1/13-admin-profile-product-contract.md` §91） |
| G15 | PASS | 多具名 Key 分别创建/撤销、明文一次、禁用全阻断：`test_api_keys.py::test_create_returns_full_key_once_format_valid`、`test_api_keys.py::test_revoke_is_per_key_and_idempotent`、`test_api_keys.py::test_revoke_other_users_key_not_found`、`test_api_keys.py::test_disabled_user_keys_and_session_rejected`、`test_api_keys.py::test_password_change_and_reset_keep_key_valid` |
| G16 | PASS | v0.1 无 Service Account/Key/机器 Principal：`test_permission_catalog.py::test_reserved_high_risk_codes_ungranted`（service account 权限未授予）、`test_api_keys.py::test_psa_cannot_create_platform_api_key`、`test_bootstrap_cmd.py::test_keys_issued_by_iam_are_ovk_u_prefixed`（仅 IAM 签发 User Key，无机器 Principal 路径） |
| G17 | PASS | PSA 建 Account+首位 Admin、Admin 直建 User、无注册/邀请：`test_bootstrap_cmd.py::test_init_creates_psa_password_printed_once`、`test_platform_api.py`（PSA 建 Account）、`test_admin_api.py::test_admin_creates_user_role_fixed`（角色固定 user、无邀请流程） |
| G18 | PASS | 初始/重置密码可复制长期用、不存明文、不可再取：`test_admin_api.py::test_admin_creates_user_role_fixed`（密码仅一次不可再取、DB 仅 Argon2id hash）、`test_bootstrap_cmd.py::test_init_creates_psa_password_printed_once`、`test_bootstrap_cmd.py::test_init_password_env_injected_not_printed` |
| G19 | PASS | 重置仅严格上级对下级、同级拒绝、只撤销登录 Session：`test_admin_api.py::test_reset_same_rank_403`、`test_admin_api.py::test_admin_resets_user_revokes_sessions`、`test_api_keys.py::test_password_change_and_reset_keep_key_valid`（不自动撤销 Key）、`test_sessions_api.py`（不删对话数据） |
| G20 | PASS | 私有/共享语义全入口一致、无「公共」含糊表述：`test_resource_api.py`（user_private/account_shared 全入口）、`test_lowlevel_guard.py::test_default_target_forces_actor_private_root`、`test_skill_publish.py::test_publish_two_stage_converts_and_migrates`（原地转共享、无副本） |
| G21 | PASS | 默认新增私有区、共享只读、任何渠道不能写/删共享区：`test_lowlevel_guard.py::test_default_target_forces_actor_private_root`、`test_lowlevel_guard.py::test_plain_user_explicit_shared_target_denied`、`test_lowlevel_guard.py::test_mv_cross_visibility_denied`、`test_tenant_isolation.py::test_default_target_escape_blocked`、`test_security_acceptance.py::test_visibility_tamper_cannot_bypass_shared_write`（P5-E4 新增）、`test_security_acceptance.py::test_shared_watch_manage_scope`（P5-E4 新增） |
| G22 | PASS | Admin 管本 Account 共享、PSA 代管目标 Account 共享 Resource、Skill 始终只读：`test_resource_publish.py::test_admin_cannot_publish_member_private_resource`、`test_skill_publish.py::test_publish_permissions_roles`（PSA 只读 Skill）、`test_permission_catalog.py::test_psa_skill_fixed_to_platform_read_only`、`test_aggregate_endpoints.py::test_platform_account_deletion_preview_and_delete`（平台代管目标 Account） |
| G23 | PASS | Skill 名称全局唯一、不可修改、删除释放、恢复冲突失败：`test_skill_api.py`（唯一性/不可改名/删除释放）、`test_skill_upload.py::test_create_package_name_mismatch_releases_name` |
| G24 | PASS | 仅 Account Admin 原地发布 Skill（ID/名称不变、无副本、不可取消）：`test_skill_publish.py::test_publish_two_stage_converts_and_migrates`、`test_skill_publish.py::test_publish_audit_actor_subject_ownership`、`test_skill_publish.py::test_publish_source_missing_idempotent`、`test_target_policy.py::test_skill_publish_only_account_admin` |
| G25 | PASS | Skill 在线创建/SKILL.md/ZIP 上传/整体替换、网页无执行器：`test_skill_upload.py::test_end_to_end_skill_md_upload_via_http`、`test_skill_upload.py::test_end_to_end_zip_create_and_replace_via_http`、`test_skill_upload.py::test_zip_safety_validation_matrix`（替换语义）、`test_mcp_tools_productized.py`（Skill 由 MCP/Agent 按权限读取，产品 API 无执行器端点） |
| G26 | PASS | 仅本地邮箱密码登录、无 OIDC/企业登录/占位：`test_oauth_without_studio.py::test_oauth_routes_mounted_without_studio`（MCP OAuth 仅客户端授权）、`test_security_acceptance.py::test_low_level_ops_blocked_public`（P5-E4 新增，配置无 OIDC provider）、`test_auth_api.py`（登录仅邮箱+密码）；`deploy/product/config/ov.conf.template` 无 OIDC/企业登录配置段 |

## 18.5 安全测试 17 项清单

> 执行环境：生产类配置（`deploy/product/config/ov.conf.template`：platform_enabled、
> adapter_mode=real、low_level_routers_enabled=["admin"]、studio_enabled=false）+ 测试库
> 集成断言；真实公网不可用，公网断言以生产配置下的集成/配置断言覆盖（18.3 注明证据形态）。
> 全部 17 项 **PASS**；证据格式：条目、结论、证据链接。

| # | 18.5 条目 | 结论 | 证据链接（tests/platform/，除注明外） |
| --- | --- | --- | --- |
| #1 | IDOR/篡改（URL/请求体 Account/User ID） | PASS | `test_tenant_isolation.py::test_cross_account_idor_denied`、`test_tenant_isolation.py::test_cross_user_idor_denied`、`test_lowlevel_guard.py::test_idor_other_user_private_uri_denied`、`test_admin_api.py::test_reset_cross_account_404`、`test_api_keys.py::test_revoke_other_users_key_not_found` |
| #2 | Header spoofing 无效 | PASS | `test_tenant_isolation.py::test_header_spoofing_ignored`、`test_security_acceptance.py::test_header_spoofing_ignored_scope`（P5-E4 新增） |
| #3 | Cookie Secure/HttpOnly/SameSite 生效 | PASS | `test_csrf.py`（apply_session_cookie 属性）、`test_security_acceptance.py::test_cookie_attributes_secure_httponly_samesite`（P5-E4 新增） |
| #4 | CSRF、爆破、fixation、replay | PASS | `test_csrf.py`、`test_rate_limit.py`、`test_security_acceptance.py::test_login_rotation_no_session_fixation`（P5-E4 新增）、`test_resource_api.py::test_import_idempotency_key_replay`、`test_oauth_refresh_rotation.py`（Token 重放撤销） |
| #5 | 日志/审计无密码/Cookie/Key 明文/完整 hash/Token | PASS | `test_audit_production.py::test_17_1_event_categories_produce_masked_audit`（全库审计脱敏扫描） |
| #6 | 创建/重置密码响应不入访问日志/埋点/错误上报/审计 metadata | PASS | `test_audit_production.py::test_cross_user_events_have_actor_and_subject`（metadata 无新密码）、`test_security_acceptance.py::test_reset_password_response_not_in_audit_metadata`（P5-E4 新增） |
| #7 | 删除/禁用最后一个 Account Admin 被拒 | PASS | `test_deletion_jobs.py::test_delete_last_account_admin_rejected`、`test_admin_api.py::test_disable_last_account_admin_409`、`test_aggregate_endpoints.py::test_admin_user_delete_last_admin_conflict`、`test_security_acceptance.py::test_last_account_admin_delete_and_disable_protected`（P5-E4 新增） |
| #8 | 篡改 Subject/伪造角色/绕过确认弹窗不能绕过后端授权 | PASS | `test_lowlevel_guard.py`（Subject/默认目标）、`test_admin_api.py::test_admin_creates_user_role_fixed`（role 字段忽略）、`test_security_acceptance.py::test_confirmation_dialog_is_not_a_security_boundary`（P5-E4 新增） |
| #9 | 篡改 visibility/直接提交共享根/默认目标/编码别名 URI/跨可见性移动不能绕过共享写 | PASS | `test_lowlevel_guard.py::test_plain_user_explicit_shared_target_denied`、`test_lowlevel_guard.py::test_encoded_and_alias_uris_normalize_before_authorization`、`test_lowlevel_guard.py::test_mv_cross_visibility_denied`、`test_tenant_isolation.py::test_default_target_escape_blocked`、`test_security_acceptance.py::test_visibility_tamper_cannot_bypass_shared_write`（P5-E4 新增） |
| #10 | /studio 不挂载时同/跨设备 OAuth 走通、浏览器无 User API Key | PASS | `test_oauth_without_studio.py::test_oauth_routes_mounted_without_studio`、`test_oauth_without_studio.py::test_consent_verify_pages_from_web_platform`、`test_oauth_without_studio.py::test_browser_no_user_api_key_surface`、`test_mount_production.py::test_platform_mode_studio_404_not_redirect` |
| #11 | forget 与公开删除入口只进 30 天回收期、不能直接物理删除 | PASS | `test_mcp_tools_productized.py::test_product_forget_soft_deletes_into_30_day_window`、`test_deletion_jobs.py::test_purge_after_defaults_to_30_days`、`test_deletion_jobs.py::test_restore_after_purge_window_expired` |
| #12 | 普通 User 不能查看/触发/取消他人私有 Watch/Task；Admin 只能管理共享 Watch | PASS | `test_security_acceptance.py::test_watch_cross_user_invisible`、`test_security_acceptance.py::test_shared_watch_manage_scope`、`test_security_acceptance.py::test_cross_user_operation_invisible`（P5-E4 新增）、`test_resource_watch.py`、`test_aggregate_endpoints.py::test_admin_activity_and_monitoring`（共享 Task 可见、成员私有任务不暴露） |
| #13 | 远程来源拒绝 localhost/私网/云元数据/DNS Rebinding/危险 Redirect/本地路径/Userinfo/私有 Git 凭证 | PASS | `test_resource_api.py::test_remote_source_security`（基础面）、`test_security_acceptance.py::test_dns_rebinding_blocked`、`test_security_acceptance.py::test_redirect_revalidation_and_depth_limit`、`test_security_acceptance.py::test_remote_source_blocks_private_git_credentials`（P5-E4 新增） |
| #14 | 完整 URL 仅密文保存；Outbox/QueueFS/Watch JSON/日志/审计/DTO 无含 Query 明文 | PASS | `test_resource_watch.py::test_watch_persists_resource_id_only_not_plaintext_url`（Watch 行/密文列）、`test_security_acceptance.py::test_one_time_url_masked_in_dto_and_audit`（P5-E4 新增，DTO/审计无 Query 明文） |
| #15 | Upload ID 过期/重放/跨 User/Account/Visibility 消费拒绝；大小/MIME 服务端重校验 | PASS | `test_resource_api.py::test_upload_binds_actor_and_scope`（跨 Scope/重放/原子消费）、`test_resource_api.py::test_upload_expired_rejected`（过期）、`test_security_acceptance.py::test_upload_consumption_guards`（P5-E4 新增，重放+拒绝审计） |
| #16 | Node ID 篡改不能跳出父 Resource；HTML/SVG 预览不执行脚本；下载文件名不注入响应头 | PASS | `test_resource_api.py::test_nodes_tree_preview_download_and_node_id_authorization`、`test_security_acceptance.py::test_node_id_tamper_cannot_escape_parent`（P5-E4 新增）；`openviking/server/platform/resource/security.py::is_executable_extension`（可执行类型不内联） |
| #17 | 生产公网 WebDAV/Snapshot/Pack/Debug/Observer/系统修复 404/拒绝、不能借 Router 绕过 Product Facade | PASS | `test_mount_production.py::test_low_level_ops_not_mounted_in_production`（应用层）、`test_lowlevel_guard.py`（低层守卫）、`test_security_acceptance.py::test_low_level_ops_blocked_public`（P5-E4 新增，配置+代理 deny 双断言） |

## 14.5 高风险操作确认弹窗 + 审计复验

> 06 §14.5：确认弹窗只用于防误触，不是安全边界；后端必须重校验 Session/CSRF/
> Permission/Actor-Subject Scope/目标状态，成功、失败、拒绝都写审计。前端弹窗
> 数据源（deletion-preview 的影响范围/可恢复/恢复截止）与后端审计复验由
> `test_high_risk_confirm.py`（P5-E4 新增）逐类覆盖：删除 Account、删除用户全部
> 数据、重置他人密码、撤销他人 Key、删除/批量覆盖共享 Resource/Skill、成员数据
> 只读（无写路径）。本 Epic 修复「高风险写拒绝缺审计」缺陷（见安全缺陷清单）。

## 18.3 公网断言测试客户端清单

> 证据形态：真实公网/真实 MCP 客户端在本环境不可用（18.3 注），以**生产配置下
> 的集成断言 + 配置断言**留存证据：每个客户端以新签发 IAM Key（`ovk_u.*`）走通
> 产品 API，并断言三凭证（Session/API Key/OAuth）同一套实时 RBAC；公网路由/低层
> 封禁以配置断言（deploy/product）覆盖。客户端接入步骤对应 06 §15.2 第 6–8 步。

| 客户端 | 凭证形态 | 走通断言 | 证据（tests/platform/） |
| --- | --- | --- | --- |
| SDK（httpx 产品 API 客户端） | `Authorization: Bearer <ovk_u.*>` | me/资源/Key 全链路、三凭证一致 | `test_client_acceptance.py::test_sdk_client_bearer_new_key_flow`（P5-E4）、`test_api_keys.py::test_session_and_api_key_have_same_identity_and_permissions` |
| CLI | `X-Api-Key` 环境变量注入 | Bearer 与 X-Api-Key 等价 | `test_client_acceptance.py::test_cli_client_x_api_key_env`（P5-E4）、`test_api_keys.py::test_x_api_key_header_equivalent_to_bearer` |
| 插件（低层 API/工具面） | `X-Api-Key`（Key 归属者审计） | 插件写动作以 Key 归属者审计、共享区只读 | `test_tenant_isolation.py::test_plugin_audits_as_key_owner`、`test_client_acceptance.py::test_plugin_client_audits_as_key_owner`（P5-E4） |
| MCP 客户端 | MCP OAuth Access Token（无 Studio） | 协议端点+授权页+PG 存储走通、浏览器无 User Key | `test_oauth_without_studio.py`、`test_oauth_principal.py::test_three_credentials_resolve_same_semantics`、`test_client_acceptance.py::test_oauth_token_client_same_rbac`（P5-E4） |

## 备份回滚 / 审计可观测性 / 健康检查证据（P5-E1/E2/E3 输入）

- 备份加密与恢复审计：`test_backup_restore.py`、`deploy/product/scripts/backup-encrypt.sh`
- 回滚演练：`test_rollback_drill.py`、`docs/ovp/v0.1/p5-e3-drill-record.md`
- 审计 17.1 清单 + 17.2 Request ID 贯通：`test_audit_production.py`
- 健康检查（PG/migration/backlog/Purge 阈值，06 §16.3）：`test_health_ready.py`
- 部署/镜像无明文密钥：`test_mount_production.py::test_no_secret_plaintext_in_repo`

## 遗留项（P0/P1/P2）

- **P0**：无。
- **P1**：无新发现。既有 P1 延续（非本 Epic 引入，见 p5-e1-ac-report.md）：
  SkillContentAdapter 共享根写入未接线（需服务层传 Actor Account 上下文后接线，
  当前显式报错避免写错位置）；ResourceExecutionPlane 真实摄取需完整运行时。
  两处均不影响 26 条验收结论（对应路径以受控 fake + 显式失败语义覆盖）。
- **P2**：RealSessionBackend Commit Phase 2 兜底；RealControlPlaneAdapter 账号枚举
  最佳努力；真实公网 E2E 以生产配置集成/配置断言覆盖（证据形态已注明）。

## 上线结论

**GO** —— 26 条生产验收门禁全部 PASS、18.5 安全测试 17 项全部 PASS、14.5 高风险
操作审计复验通过（含本 Epic 修复的拒绝审计缺陷）、五类测试生产配置回归全绿、
无 P0/P1 遗留。遗留 P1 为既有接线限制且不影响门禁结论，建议上线后随
Skill 内容协议扩展（Phase 6 前）闭合。

## 测试统计（2026-08-19，PG ovp-pg16-p5e4:55456）

- 基线：566 passed, 1 skipped（P5-E3 收尾态）
- 本 Epic 新增：**33 passed**（test_security_acceptance 19 + test_high_risk_confirm 9 + test_client_acceptance 5）
- 全量：**599 passed, 1 skipped**（`PYTHONPATH=. .venv/bin/python -m pytest tests/platform/ -p no:cacheprovider --no-cov`）
- lint：`uvx ruff check openviking/server/platform/ tests/platform/` 全绿
- 本 Epic 安全缺陷修复 2 项（P1）：高风险写拒绝审计缺失（dependencies.py `require_high_risk_write`）、
  服务层拒绝审计异常路径回滚（router except 分支补 commit）；回归测试见
  `test_high_risk_confirm.py` / `test_security_acceptance.py::test_confirmation_dialog_is_not_a_security_boundary`
