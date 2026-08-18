# 14 Development Plan（开发计划）

> Design v0.1 · [返回版本索引](README.md) ｜ 状态：Draft（设计已冻结，开发未启动）
> 上游基线：OpenViking v0.4.12（`c1d38eb4`）｜ 设计冻结：`design-v0.1.0`
> 本文把已冻结的 Design v0.1 拆为 Phase 1–5 的 Epic 级任务清单；开发时每个 Epic 再细拆为 story。所有验收标准均可验证，并可映射回设计文档章节与 07 §21 验收清单。

## 94. 与设计文档的关系

- 本文是**执行计划**，不是设计来源：页面、API、权限、状态、错误码的契约一律以 01–13 号文档为准（Design Frozen）。
- Phase 0（契约冻结）已完成：Permission Catalog、集成契约、术语与 API Key 格式已随 `design-v0.1.0` 固定。
- 技术验证（Spike）已交付：`spikes/platform-v0.1/`（后端 57/57 断言、同进程集成 14/14、web-platform 脚手架）。Spike 为可丢弃代码，正式实现按设计文档重写；其断言升级为正式测试（见 §98.2）。
- 所有 Epic 的 Plane 字段（优先级/状态）与后续 Plane 项目导入字段对齐：Epic = Phase，Work Item = Epic。

## 95. 里程碑总表

| Phase | 名称 | 核心交付物 | 退出标准（Go 信号） |
| --- | --- | --- | --- |
| 1 | IAM 基础 | PostgreSQL schema/migration、RBAC 内核、登录 Session、API Key、IAM 管理 API、审计基础 | `tests/platform/` 认证类集成测试全绿；verify.py 除 `==12` 外全部断言复跑通过 |
| 2 | Provisioning 与 Product API | outbox/worker/reconciler、Principal→RequestContext、Product Facade、Content Registry、Resource/Skill/Session/Search 产品 API、低层入口统一守卫、MCP OAuth 存储迁 PG | 18.2/18.3 全量通过；verify.py `==12` 通过；Spike 风险 10 闭合 |
| 3 | web-platform 产品前端 | 登录/首页/检索/Resource/Skill/Session/Activity/回收站/个人设置/OAuth 授权页 | 18.4 app 域 E2E 全绿；07 §21 条目 1、14、15、20、25、26 满足 |
| 4 | 管理后台 | /admin 用户/共享内容/审计/回收站、/platform 平台管理 | 18.4 admin/platform 域 E2E 全绿；07 §21 条目 2、4、10、11、13、17、18、19、22、24 满足 |
| 5 | 初始部署与生产加固 | 部署单元与路由、初始化、备份回滚演练、安全加固、26 条验收门禁 | 07 §21 全部 26 条有证据；18.5 安全测试全绿；Go/No-Go 报告完成 |

建议执行顺序与并行度：P1-E1→E2→E3→（E4‖E5）｜P2-E1‖E2→（E3‖E4‖E5）→E6｜P3-E1→E2→（E3‖E4‖E5）｜P4-E1→（E2‖E3）→E4｜P5-E1→（E2‖E3）→E4。多 SubAgent 并行时按 Epic 隔离 worktree，验收标准作为合并门禁。

## 96. Phase 1：IAM 基础（Epic P1-E1 ～ P1-E5）

### 96.1 P1-E1：IAM PostgreSQL 数据层（Schema + Alembic Migration + Repository）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：SQLAlchemy 2 async + asyncpg 接入现有 FastAPI 进程；Alembic async 模板（复用 spike `migrations/` 示范）；9 张 `iam_*` 表（accounts/users/api_credentials/roles/permissions/role_permissions/user_roles/sessions/audit_events，字段与约束按 04 §10.1–10.8）；**`iam_roles.rank`（3/2/1，注明与 OpenViking `Role` 内置 rank 独立、禁止混用，04 §10.4）**；唯一约束：`normalized_email` 全局唯一（仅 Unicode/大小写/首尾空白规范化）、`(account_id, ov_user_id)`、`(account_id, normalized_username)`、`public_id/key_hash`、`token_hash`；`account_id IS NULL` 仅允许 `platform_super_admin`；repository（05 §11 `iam/repository.py`）含乐观锁；确认 `code` 与 `ov_account_id` 双唯一语义（Spike §4.2 #8 待决）。
- **范围 Out**：`iam_outbox` 消费与 Provisioning Worker、`platform_content_refs/operation_refs/uploads/deletion_jobs/oauth_*` 表、角色/权限种子（P1-E2）。
- **依赖**：PostgreSQL 16；`pyproject.toml` 增加 SQLAlchemy 2/asyncpg/Alembic；Platform 配置模型。
- **验收标准**：① 全新库执行迁移后 9 表创建成功，upgrade/downgrade 可重复；② `normalized_email`、`(account_id, ov_user_id)`、`(account_id, normalized_username)` 唯一生效；③ `iam_roles.rank` 支撑 verify.py "ranks 3/2/1" 断言；④ `account_id IS NULL` 仅 PSA；⑤ 外键循环（`accounts.deleted_by↔users.account_id`）按 Spike §4.2 #5 人工编排迁移，无手工修复残留；⑥ repository 提供事务性 CRUD 与乐观锁。
- **对应章节**：04 §10.1–10.8；05 §11；Spike README §4.2 #5/#8。

### 96.2 P1-E2：RBAC 内核（权限目录、内置角色种子、有效权限计算）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：完整 Permission 目录种子（03 §9.1 全部 code，含 domain/action/risk_level）；三内置角色全局单行（`platform_super_admin` rank=3/`ov_base_role=NULL`、`account_admin` rank=2/`admin`、`user` rank=1/`user`）；**PSA 权限集合显式剔除全部 Skill 写/用权限（`skill.user_private.manage.self`、`skill.user_private.publish.account`、`skill.account_shared.use.account`、`skill.account_shared.manage.account`），不得经继承隐式获得（03 §9.3 回填发现）**；有效权限 = `union(active role permissions) − disabled`（03 §9.4）；双版本缓存：用户级 `permission_version` + 全局 `permission_schema_version`（进程内短 TTL，06 §16.4 单实例许可）；角色授予约束（Account 一致性、单角色、PSA 仅平台初始化路径）。
- **范围 Out**：自定义角色 CRUD、多角色叠加、Redis 共享缓存、`ov_base_role→RequestContext` 转换（P2）。
- **依赖**：P1-E1。
- **验收标准**：① 种子后恰三内置角色且 rank/ov_base_role 正确（verify.py `==3` 复跑）；② PSA `auth/me` 无任何 `skill.*.manage/publish/use`（verify.py `==1` 复跑）；③ 有效权限=并集，禁用后为空；④ 提升后免重登生效（verify.py `==11` 复跑）；⑤ 种子/migration 变更递增 `permission_schema_version` 参与全部缓存键；⑥ 跨 Account 授予 `account_admin/user` 被拒。
- **对应章节**：03 §9.1–9.4；04 §10.4–10.6；Spike README §4.1 #4、§4.2 #7。

### 96.3 P1-E3：认证与登录 Session（密码登录、CSRF、auth/me、密码修改与分级重置）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：Argon2id（锁定 argon2-cffi 版本，异常用 `VerifyMismatchError/VerificationError/InvalidHashError`）；`POST /auth/login` 仅 email+password、统一 `LOGIN_FAILED` 防枚举、IP+标识限流递增冷却；不透明登录 Session（`__Host-ov_session`，HttpOnly/Secure/SameSite=Lax/Path=/，token≥256bit，DB 仅存 SHA-256，空闲 24h/绝对 30 天可配置，登录/提权/改密轮换，登出/禁用/重置撤销）；CSRF（SameSite=Lax + Origin/Referer 校验 + `X-CSRF-Token`）；`auth/me`、`logout`、`logout-all`、`password/change`（必填旧密码，**轮换必须同步 Set-Cookie，05 §12.3 回填发现**）；分级密码重置（`actor_role_rank > target_role_rank`，**只用 `iam_roles.rank` 3/2/1，与 OpenViking `Role` 内置 rank 0/1/2 无映射，03 §8.3 回填发现**；重置撤销目标全部登录 Session、不删对话数据、不撤销 API Key）；首次 PSA 初始化/bootstrap 路径；同进程集成基线（Spike 14/14 已验，`create_app()` 真实挂载留 P5-E1）。
- **范围 Out**：注册/邀请/激活/找回密码、OIDC/企业登录、MCP OAuth 页面迁移（P2）、Break-glass（Phase 6）、登录 UI（P3）。
- **依赖**：P1-E1、P1-E2。
- **验收标准**：① 登录下发 Cookie（≥40 字符）且 DB 仅存 SHA-256；② 错误密码/未知邮箱同码 401；③ 改密错旧密码→`LOGIN_FAILED`，成功响应含新 Set-Cookie 且旧 Cookie 失效（verify.py `==6` 复跑）；④ 重置撤销全部登录 Session 且 API Key 不受影响（`==7` 复跑）；⑤ 跨 Account 重置 404、同级 403（`==8`/`==11` 复跑，仅用平台 rank）；⑥ 无 CSRF Token 的写请求被拒、API Key 不能做 CSRF 写（`==4` 复跑）；⑦ 连续失败触发限流冷却；⑧ `auth/me` 权限摘要与 P1-E2 一致、logout 后立即失效。
- **对应章节**：03 §8.1–8.3；04 §10.7；05 §12.3；Spike README §4.1 #1/#3、§4.2 #6、§4.3 风险 9。

### 96.4 P1-E4：用户 API Key 与统一 Principal Resolver

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：`GET/POST/DELETE /me/api-keys`（Session+CSRF，`credential.*.self`）；Key 格式 `ovk_u.<public_id>.<secret>`（secret≥256bit base64url，DB 存 public_id+SHA-256(secret)+末四位，明文仅创建响应一次）；常量时间校验、`public_id` 定位→加载用户/权限→`AuthenticatedUserPrincipal(authentication_method="api_key")`；`Bearer` 与 `X-Api-Key` 同一解析器；统一 Principal Resolver（Session/API Key 产出同一 Principal，REST 与 MCP 复用）；**凭据优先级：Cookie 优先、失效不自动回退 Bearer（03 §8.4 回填发现）**；撤销幂等按名独立；禁用/删除期全部失效；到期立即拒绝；Key 无 Scope、不存权限快照；PSA 不签发平台级 Key。
- **范围 Out**：`/api/v1`、`/mcp`、SDK/CLI 入口接入（P2）；OAuth Token→Principal（P2）；Key 限制性 Scope（Phase 6）；Service Account（v0.1 不做）。
- **依赖**：P1-E1、P1-E2、P1-E3。
- **验收标准**：① 创建返回 `ovk_u.<public>.<secret>`，列表无明文（`==4` 复跑）；② Session 与 API Key 同 user_id 同权限集仅认证方式不同（`==4` 复跑）；③ Bearer 与 X-Api-Key 解析一致；④ Cookie+Bearer 并存时以 Cookie 为准，Cookie 失效不回退（新增断言）；⑤ 按名撤销互不影响、重复撤销幂等（`==9` 复跑）；⑥ 禁用后全部 Key 立即失效（`==10` 复跑）；⑦ 重置不撤销 Key（`==7` 复跑）；⑧ Key 不能通过 CSRF 写接口。
- **对应章节**：03 §8.4–8.5；04 §10.3；05 §11.4、§12.4；Spike README §4.1 #2。

### 96.5 P1-E5：IAM 管理 API 与审计基础

- **Plane**：优先级 medium ｜ 状态 backlog
- **范围 In**：PSA 创建 Account+首位 Admin（一次返回初始密码）、`GET /platform/accounts[/{id}/users]`；Account Admin 直建 User（角色固定 `user`，一次返回初始密码）；用户管理（列表/PATCH 启用禁用/disable/分级密码重置/roles 只读）；平台级 `PUT .../users/{id}/role`（仅 `user→account_admin`）、平台级重置（禁目标 PSA）；管理员查看/撤销用户 API Key 元数据；守卫（不能建/提/重置 Account Admin 同级、`LAST_ACCOUNT_ADMIN_REQUIRED`、密码仅一次返回）；审计基础：`iam_audit_events` 写入（Actor/Subject 分离、脱敏 metadata）、`GET /admin/audit-events`、平台审计读取；全部管理动作写审计。
- **范围 Out**：Provisioning outbox/worker 与 `provisioning/retry`（P2）；Product Facade；软删/回收站/purge（P2/P4）；成员数据查看（P2/P4）；管理后台 UI（P4）。
- **依赖**：P1-E1～E4。
- **验收标准**：① PSA 建 Account+首位 Admin 返回 ov 映射与一次性密码（`==3` 复跑）；② 建 User 角色固定 user、密码仅一次（`==3` 复跑）；③ 重置遵循平台 rank：跨 Account 404、同级 403、成功撤销目标全部 Session（`==7/8/11` 复跑）；④ 提升即时生效、产品 API 不能建/提/重置 PSA 或建 Account Admin；⑤ 禁用即时杀 Session+全部 Key（`==10` 复跑）；⑥ 每项管理动作一条审计，Actor/Subject 分离，无密码/Token/Key 明文（04 §10.8）；⑦ 最后一名 Account Admin 删除/禁用被拒；⑧ verify.py 除 `==12` 外全部复跑通过（Phase 1 门禁）。
- **对应章节**：03 §8.3、§9.2–9.3；04 §10.8；05 §12.2、§12.6；07 §19 Phase 1、§21 条目 1–10。

## 97. Phase 2：Provisioning 与 Product API（Epic P2-E1 ～ P2-E6）

### 97.1 P2-E1：Provisioning Outbox/Worker/Reconciler 与控制面同步

- **Plane**：优先级 urgent（Spike 遗留 + 全部账号可用性前置）｜ 状态 backlog
- **范围 In**：`iam_outbox` 落地（04 §10.9）与事务写入；ProvisioningService（05 §11.3：PG 事务建 account/user(provisioning)+outbox→commit→Worker 用 `SystemPrincipal` 初始化 OpenViking namespace→active/failed）；`SystemPrincipal`（02 §7.1：仅受控代码路径、无对外凭证、审计 `actor_type=system`+组件名）；Worker 重试（attempts/指数退避/脱敏错误）；Reconciler（卡死事件恢复、对账）；控制面同步（ov 映射一致）；`POST /platform/accounts/{id}/provisioning/retry`（仅 provisioning/failed、幂等）；**create_app() 真实 ServerConfig 挂载验证（Spike 风险 9）**。
- **范围 Out**：登录/密码/Session（P1）；30 天软删与 Purge（P2-E2+各对象 Epic）；多实例 Worker 协调（Phase 6）。
- **依赖**：Phase 1；无 Phase 2 内部依赖，可最先启动。
- **验收标准**：① Account+首位 Admin 创建与 outbox 同一 PG 事务，Worker 成功→active+completed、失败→failed+attempts 递增；② SystemPrincipal 不可从 HTTP/MCP 声明、审计含 system 组件；③ Worker 幂等不产生重复 namespace；④ `provisioning/pending|failed` 用户产品请求返回 `PROVISIONING_PENDING/FAILED`；⑤ retry 仅 failed 生效且幂等；⑥ create_app 真实挂载后 `/api/platform/v1` 与 `/api/v1`、`/mcp` 并存不冲突（Spike 风险 9 闭合）；⑦ Reconciler 恢复卡死事件且不重复执行；⑧ 控制面同步与 PG ov 映射一致。
- **对应章节**：02 §6.1/§7.1；04 §10.1/§10.2/§10.8/§10.9；05 §11.3/§12.2/§12.5；Spike README §4.1/§4.3。

### 97.2 P2-E2：身份上下文转换、Product Facade 骨架与注册表基础设施

- **Plane**：优先级 urgent（E3–E6 全部依赖）｜ 状态 backlog
- **范围 In**：`DataAccessContext` 与 `authorize_data_access`（02 §7.2）；`to_ov_context`/`to_ov_account_context`（02 §7.3：最小 `Role.USER`、`platform-gateway` 占位、平台 rank 与 OpenViking rank 隔离）；URI Canonicalizer + Target Classifier（user_private/account_shared/internal）+ AuthorizationService 统一授权门（02 §7.5）；TargetPolicy 基础（05 §11.5 动作→Permission 映射、默认目标规则）；ProductFacadeService 骨架（05 §11.2）；Content Registry（04 §10.10：`platform_content_refs`+Skill 部分唯一索引、`platform_operation_refs`(generation)、`platform_uploads`(15 分钟/原子消费)、Idempotency-Key 事务、tags 校验与 `search_tags` Outbox 同步）；`iam_deletion_jobs`+Purge Worker 公共基础设施（04 §10.11）。
- **范围 Out**：业务对象完整端点（E3–E5）；低层 `/api/v1` 与 MCP 写守卫（E6）；前端（P3）。
- **依赖**：Phase 1；P2-E1（active 语义、SystemPrincipal）。
- **验收标准**：① 三凭证解析同一 Principal 语义、授权一致；② `to_ov_context` 仅 user_private 且 subject 非空，`to_ov_account_context` 仅 account_shared 且 subject_user 为空、跨 Account 执行载体固定 `platform-gateway`；③ URI 分类覆盖 user/resources/agent/skills 与 internal 根，internal 默认拒绝；④ 客户端提交的 visibility/ID 不能单独作为授权依据，canonical URI 一致性校验失败即拒；⑤ 对外创建先 `platform_content_refs(provisioning)` 再调 OpenViking，列表只返回 active；⑥ Skill 部分唯一约束生效且冲突响应不泄露占用者；⑦ Idempotency-Key 重复提交不产生重复对象；⑧ 删除任务与 Purge 幂等、`purge_after` 默认 30 天、审计不随物理清理。
- **对应章节**：02 §7；04 §10.10–10.12/§10.14；05 §11.1/§11.2/§11.5/§12.2。

### 97.3 P2-E3：Resource 产品 API（me/account/platform 全生命周期）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：Capabilities 与三入口 Upload；导入（文件/公开 HTTPS 网页/公开 HTTPS Git，安全校验 09 §40.6，异步导入事务 09 §40.7）；列表/详情/PATCH（名称/说明/标签+乐观锁）；replace/refresh/retry；nodes 只读树+Node ID 防越权；Watch 全量（09 §43.2，持久化只存 Resource ID 不存明文来源）；私有→共享发布（复制新建新 ID，09 §44）；删除六步事务与恢复（09 §45.2/§45.3）；generation 防旧任务覆盖；Activity 聚合与取消；稳定错误码（09 §46.5）与审计（09 §47.2）。
- **范围 Out**：Skill 生命周期（E4）；低层 fs/content 写守卫（E6）；`content.write/batch-write/reindex` 不进产品 API；前端（P3）；内容版本浏览/回滚、WebDAV。
- **依赖**：P2-E2；P2-E1（目标用户 active）。
- **验收标准**：① 未指定目标固定私有根，共享导入必须经 `/account/*` 或 `/platform/accounts/{id}/*` 并持对应写权限；② 删除按 09 §45.2 六步事务（pending_deletion+deletion_jobs+Watch 暂停+协作取消+列表/Search 排除），物理清理仅 30 天后由 Purge Worker 执行；③ 旧 Operation generation 过期只能记录终态、不能切换 active_generation 或复活删除中对象；④ Refresh/Watch 期间旧版本可读、失败保持 active 显示「最近同步失败」、首次失败保持 failed 仅管理者可见；⑤ 私有发布生成新 ID 独立 URI、原对象保留、Watch/私有关系/审计历史/Query 不复制；⑥ Watch 记录不含明文 URL，触发时 Facade 实时校验；⑦ Upload ID 绑定主体，跨 Scope 消费/过期/重放拒绝，原子 ready→consumed；⑧ 可见无写权限 403、不可见 404、错误码按 09 §46.5。
- **对应章节**：09 §36–§48；04 §10.10–10.12/§10.14；05 §12.5。

### 97.4 P2-E4：Skill 产品 API（名称唯一、两段式发布、恢复冲突）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：私有/共享列表详情、在线创建、SKILL.md/ZIP 上传（ZIP 安全校验）；整体更新 PUT（name 不可变，ZIP 新包 name 必须一致）；Account 范围名称唯一（覆盖全部私有+共享未删除，冲突不泄露占用者）；软删立即释放名称、恢复唯一性复查冲突 `SKILL_NAME_CONFLICT`；成员 Skill 只读+`POST /admin/users/{id}/skills/{id}/publish`；**发布两段式（10 §58.4：PG 归属转换+`platform_operation_refs(skill_publish)`+outbox → Worker 受控 `fs.mv` 迁移、向量 URI 重写保留原向量、`owner_user_id` 残留清洗、失败幂等重试，禁止「已转换未迁移/已迁移未转换」）**；发布确认与审计（10 §58.3）；`SKILL_*` 错误码。
- **范围 Out**：Skill 私密配置（P3）；取消发布/共享转私有/多版本/远程 Skill/Watch（暂缓）；前端（P3）。
- **依赖**：P2-E2。
- **验收标准**：① 同一 Account 任意两个未删除 Skill 不得同名（跨 User/可见性）；② name 不可修改（JSON 与 ZIP 均拒，`SKILL_NAME_IMMUTABLE`）；③ 冲突响应只说明「该名称在当前 Account 不可用」；④ 软删立即释放名称、恢复同名冲突保持删除状态不改名不覆盖；⑤ 发布按 10 §58.4 两段式、任一步失败由 Operation 状态机保护、重试幂等；⑥ 发布后目录完整迁移、向量不重新 embedding、残留 owner 被清洗；⑦ 普通 User 不能发布、Account Admin 可发布任意成员私有 Skill 但不能编辑/删除/恢复他人私有 Skill、PSA 全 Skill 只读；⑧ 发布审计含 Actor/Subject/前后归属，不可取消。
- **对应章节**：10 §52–§64；04 §10.10；05 §12.5。

### 97.5 P2-E5：Session 与 Search 产品 API（集成写入链路）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：Session 集成写入链路（POST /sessions 幂等创建、幂等追加消息、commit，11 §70.6/§70.8）；读取（列表/详情/messages 组装/memory-impact 脱敏 Diff，11 §70.4/§71.3）；Commit 服务端统一 Turn-aware Retention（3 Turn/12000 Token/至少 1 个最新 Assistant Step，客户端不能调低）；Session 软删/恢复（30 天、`SESSION_DELETED`、恢复不回滚 Memory、删除期间拒绝写入，11 §72）；Search find/search（固定检索根、字段白名单 `query/context_type/tags/since/until`、`session_id` 仅 search 且属当前 User、严格 DTO 拒绝调试字段，11 §69.2）；Search 结果 DTO 脱敏（删 uri/score/level/query_plan/provenance/relations/category，visibility 服务端分类）；admin/platform 成员只读 Search/Session（Actor/Subject 同时记录）。
- **范围 Out**：Memory 独立 CRUD/列表/回收站；recall 网页动作；网页 Chat/手工 Commit 按钮（P3）；Tool Result/Extract/原始 Archive 完整返回。
- **依赖**：P2-E2。
- **验收标准**：① find 不加载 Session、search 的 session_id 必须属当前 User 且未删除；② 白名单外字段严格拒绝不静默透传；③ 结果不含 URI/score/level/Query Plan/Provenance/Relations/category，Memory 结果只含类型/摘要/匹配原因；④ Session 创建/追加/Commit 幂等、消息顺序稳定；⑤ Retention 由服务端统一、客户端更低预算被拒；⑥ memory-impact 不返回 Archive/Memory URI、区分 pending/running/completed(有/无操作)/failed；⑦ 软删立即隐藏、写入返回 `SESSION_DELETED`、恢复不回滚 Memory、30 天后 Worker 幂等物理删除；⑧ 跨 User/Account 访问统一不可见语义（`SESSION_NOT_FOUND`/404）。
- **对应章节**：11 §66–§77；05 §12.5；02 §7.3。

### 97.6 P2-E6：低层入口统一守卫、MCP OAuth 迁移与跨租户隔离测试

- **Plane**：优先级 high（含 Spike 风险 10）｜ 状态 backlog
- **范围 In**：低层 `/api/v1` Router 与 MCP Tool 统一 TargetPolicy 写守卫（05 §11.5：add_resource/write/mkdir/mv(源与目标)/set_tags/归档/导入/恢复/批量全映射 Permission；未显式目标强制 User 私有根、显式共享目标对普通 User 403、跨可见性移动不走普通 mv）；MCP 13 Tool 产品化接入（08 §28.5：`forget` 改 30 天软删、`grep/glob` 不向产品凭证发布、`cancel_watch` 需目标写权限、health 脱敏）；**MCP OAuth 存储 SQLite→PostgreSQL（04 §10.13，Spike 风险 10：iam_oauth_clients/grants/pending_authorizations/tokens；协议端点复用 `mcp.server.auth` SDK 替换存储层；Refresh 轮换+family 重放撤销；同意仅接受 `authentication_method=session`；禁用/删除期撤销全部 Grant/Token）**；SDK/CLI/插件接入验证；跨 Account/User 隔离测试（IDOR、Header spoofing、默认目标逃逸、编码/别名 URI、Cookie+Bearer 优先级）。
- **范围 Out**：WebDAV 挂载、Snapshot/Pack/Backup/Import/Restore/Debug/Observer/系统修复入口（公网 404）；`/studio` 挂载与旧 Key 迁移（P5）；Service Account（v0.1 不做）。
- **依赖**：P2-E2；P2-E3/E4/E5（forget 与写入口经 Content Registry）。
- **验收标准**：① 低层写动作未显式目标强制私有根、显式共享目标对普通 User 403；② 所有「会改变」动作（含 mv 源与目标、mkdir、set_tags、归档、恢复、批量）经 Target Policy，跨可见性移动被拒；③ MCP `forget` 与产品删除一致进 30 天回收期、`grep/glob` 对产品凭证不可用、WebDAV/Snapshot/Pack/系统修复公网 404；④ OAuth 数据全部存 PG（SQLite 不再为事实来源）、Refresh 强制轮换、重放撤销 family；⑤ OAuth 同意只接受登录 Session、API Key 不能代替浏览器批准；⑥ 同一用户三凭证调用同一低层动作授权一致、插件以 Key 归属者身份读写审计；⑦ 跨 Account/User/Header spoofing/编码别名 URI/默认目标逃逸/IDOR 测试全通过；⑧ Cookie+Bearer 并存 Cookie 优先、失效不回退。
- **对应章节**：05 §11.5/§12.4；08 §28.5/§33；04 §10.13；02 §7.4–7.5；Spike README §4.1/§4.3。

## 98. Phase 3 与 Phase 4：前端与管理后台（Epic P3-E1 ～ P4-E4）

> 贯穿约束（所有 Epic）：前端路由 Guard 与按钮只做体验控制、不是安全边界（06 §13.4）；本地存储禁存 Key/Token/密码/权限快照/业务正文（06 §13.5）；错误 UI 展示 Request ID 不泄露底层异常（05 §12.2）。技术栈沿用 Spike 脚手架（Vite+React19+TS+TanStack Router/Query）。

### 98.1 P3-E1：web-platform 基础框架、路由守卫与权限控制层

- **Plane**：优先级 urgent ｜ 状态 backlog
- **范围 In**：Spike 脚手架固化为正式 `web-platform` 基线；路由树按 06 §13.2 全量展开（业务页可占位）；`lib/platform-client`（Cookie+CSRF、`{status,result,error}` envelope、稳定错误码、Request ID、401/403 处理）；启动调 `/auth/me` 的 auth 状态层；按 Permission 的路由 Guard 与按钮控制（`lib/permissions.ts`）；`/app`、`/admin`、`/platform` 三布局与侧边栏；本地存储规则落地。
- **范围 Out**：登录表单与业务页内容（后续 Epic）；权限快照持久化与前端安全锁；`/studio` 改造（06 §13.6）。
- **依赖**：Spike web-platform；后端 P1（auth/me、Session Cookie+CSRF、权限种子）。
- **验收标准**：① 启动必调 `/auth/me`，未登录访问受保护路由跳 `/login` 并回跳（OAuth 仅回跳同源授权路由）；② Guard 与按钮依 permissions 隐藏/禁用；③ 401/403 刷新 `/auth/me` 并更新界面，会话过期回登录页；④ DevTools 验证本地存储仅非敏感偏好；⑤ 绕过 Guard 直接请求后端仍 403/404，错误页展示 Request ID；⑥ 路由树覆盖 06 §13.2 全部正式路由，URL 无 Viking URI/Account/User ID。
- **对应章节**：06 §13.1–13.6；05 §12.2–12.3。

### 98.2 P3-E2：登录、个人设置与 MCP OAuth 授权页（含 API Key 一次性明文）

- **Plane**：优先级 urgent ｜ 状态 backlog
- **范围 In**：`/login`（邮箱+密码、统一错误文案、限流/停用/开通中状态、按角色进入默认入口）；`/app/profile`（基本信息只读、改密含旧密码、退出所有设备）；`/app/profile/api-keys`（列表掩码、创建、撤销、一次性明文展示页）；`/app/profile/connections`（OAuth Grant 列表与单独撤销）；`/oauth/consent` 与 `/oauth/verify` 完整流程。
- **范围 Out**：注册/邀请/激活/找回密码；单登录会话列表（仅 logout-all）；OAuth 协议端点（后端）；PSA 平台级个人 Key；`/studio` 旧 OAuth 页。
- **依赖**：P3-E1；后端 P1（auth 全组、me/api-keys）；后端 P1/P2（oauth-grants、oauth pending/authorize）。
- **验收标准**：① `/login` 仅邮箱+密码、错误统一、状态文案符合 13 §80.5；② 登录由服务端签发 HttpOnly Cookie、已登录访问 `/login` 跳默认入口；③ API Key 明文只在一次性结果页展示一次、刷新/离开不可再取、列表永不显示完整 Key；④ Key 明文仅内存态、不入任何存储/URL/埋点/剪贴板历史；⑤ consent/verify 使用登录 Session、不要求输入任何 Key/密码，展示服务端登记的 Client ID 与回调 host；⑥ OAuth 页未登录先跳 `/login` 且只允许回跳同源授权路由，pending/display code/authorization code 不进 URL/埋点/日志正文；⑦ connections 可单独撤销且不影响其他 Key/客户端；⑧ 改密必填旧密码、成功后提示重新登录；「退出所有设备」不影响 API Key 与 OAuth Grant。
- **对应章节**：13 §80–§83；06 §13.5–13.6/§13.8；05 §12.3–12.4。

### 98.3 P3-E3：首页、统一检索、Session 查看管理与活动/回收站

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：`/app` 首页（内容数量、最近 Session/Resource/Skill、失败摘要）；`/app/search`（快速/结合会话两模式、类型/标签 `key=value`/更新时间范围筛选、结果列表+详情抽屉）；`/app/sessions`（双栏只读、完整消息历史、Memory Impact、软删除）；`/app/activity`（聚合任务与取消）；`/app/recycle-bin`（当前 User 可恢复对象，按类型恢复）。
- **范围 Out**：网页 Chat/Composer/SSE/停止生成；`/app/memories` 与 Memory CRUD；recall 页面模式、grep/glob；Search 调试参数透传；Session 手工 Commit/Extract/Tool Result 原始查看；管理员只读视图（P4）。
- **依赖**：P3-E1、P3-E2；后端 P2（dashboard、search find/search、sessions 组、activity、recycle-bin）。
- **验收标准**：① 首页不请求/展示 Queue/锁/模型/VectorDB 等底层状态；② 快速检索不加载 Session、结合会话检索只列自己的未删除 Session、默认范围=我的私有+Account 共享；③ Search 请求体仅含白名单字段、前端无法构造/透传调试字段；④ 结果列表+抽屉：Resource/Skill 跳详情、Memory 只读抽屉无编辑/删除/下载、不展示 URI/Score/层级/Query Plan/Provenance/Relations；⑤ `/app/sessions` 无消息输入框、标题=客户端名+Session ID 短标识、Tool 卡片脱敏；⑥ Memory Impact 区分 pending/running/completed(有/无变更)/failed；⑦ Session 软删弹窗展示影响与恢复截止、属主可自助恢复；⑧ 回收站仅展示可恢复对象、恢复按类型携带权限码、`RESTORE_WINDOW_EXPIRED` 正确展示。
- **对应章节**：11 §66–§72/§74；06 §13.7；05 §12.5。

### 98.4 P3-E4：Resource 产品页面（私有/共享、导入、详情、Watch、发布、删除恢复）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：`/app/resources/private|shared` 两分区列表（筛选/排序/Cursor）；新增（文件/公开 HTTPS 网页/公开 Git，入口固定归属）；详情（概览/内容树预览/自动同步/活动四标签）；元数据编辑（乐观锁）；Refresh/替换；Watch 配置；删除预览与 30 天软删；自己的私有恢复；Account Admin 发布**自己的**私有 Resource 为共享副本；Account Admin 在 `/app` 共享页的管理能力（与 `/admin/shared-resources` 同组件）。
- **范围 Out**：visibility/URI/父目录/processing_mode 表单字段；批量删除/移动/直接改共享；解析内容编辑；grep/glob、任意 URI 浏览；成员私有 Resource 管理/发布（P4）；手工新建文本。
- **依赖**：P3-E1、P3-E2；后端 P2 Resource API 全组；权限种子。
- **验收标准**：① 新增入口决定归属、弹窗无归属下拉；普通 User 共享页无管理按钮并提示「共享内容由 Account 管理员维护」；② 上传限制取自 `resources/capabilities` 不硬编码，批量按文件独立成败；③ 内容树根固定当前 Resource、隐藏控制文件、预览沙箱化、下载安全文件名；④ 元数据冲突 `RESOURCE_VERSION_CONFLICT` 重新加载、标签严格 key=value；⑤ Refresh/Watch 期间旧版本可读、上传文件不能 Watch、`RESOURCE_BUSY` 禁用按钮；⑥ 删除预览弹窗完整、恢复后 Watch 保持 paused；⑦ 发布仅 Account Admin 自己的私有 Resource、生成新 ID、原对象保留、不复制 Watch/私有关系；⑧ 页面与网络请求无 URI/原始 Task ID/宿主机路径/来源 Query，404/403 语义正确。
- **对应章节**：09 §36–§45；06 §13.3/§13.7；05 §12.5。

### 98.5 P3-E5：Skill 产品页面（私有/共享、创建上传、详情编辑、删除恢复）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：`/app/skills/private|shared` 两分区列表；在线创建与 SKILL.md/ZIP 上传；详情（概览/使用说明渲染/文件清单/工具范围/私密配置入口）；在线编辑（name 不可变）与 ZIP 整体替换；软删与 30 天恢复；普通 User 共享页只读；Account Admin 在 `/app` 共享页管理共享 Skill（与 `/admin/shared-skills` 同组件）。
- **范围 Out**：Skill 发布入口（统一在 P4 `/admin/users/{id}/skills/{id}/publish`）；「在新 Session 中使用」/执行器；Git/网页导入、Watch；MCP Tool JSON 导入表单；取消发布/共享转私有；成员私有 Skill 编辑/删除/恢复。
- **依赖**：P3-E1、P3-E2；后端 P2（me/skills、account/skills、me/skill-configs）。
- **验收标准**：① 列表分区展示名称/描述/标签/归属/更新时间/辅助文件标识，无 URI 与控制文件；② 在线创建名称按 `validate_skill_name` 规则、表单不要求手写 YAML、不提交 target_uri；③ name 创建后不可编辑、同名冲突不泄露占用者；④ ZIP 更新只能整体重传、新包 name 必须一致（`SKILL_NAME_IMMUTABLE`）；⑤ 详情页无执行入口、私密配置仅当前 User 管理自己的；⑥ 软删立即释放名称、恢复同名冲突保持删除状态；⑦ 普通 User 共享页无管理按钮、Account Admin 共享新增固定进共享区。
- **对应章节**：10 §52–§59/§61/§63；06 §13.3；05 §12.5。

### 98.6 P4-E1：管理后台框架与用户生命周期管理

- **Plane**：优先级 urgent ｜ 状态 backlog
- **范围 In**：`/admin` 布局与守卫（仅 Account Admin，固定当前登录 Account）；`/admin/users` 列表与状态筛选；直接创建用户（邮箱全局唯一、code 唯一不可改、仅 user 角色、一次性初始密码弹窗）；禁用/启用；分级密码重置（仅低级别目标显示按钮、一次性新密码）；删除预览与 30 天软删；`/admin/settings` 占位页。
- **范围 Out**：邀请/激活/邀请邮件；单登录会话列表；创建/提升 account_admin（仅 P4-E4）；PSA 同级重置与网页紧急恢复；成员数据只读视图（P4-E2）。
- **依赖**：P3-E1、P3-E2（守卫、一次性凭证展示模式）；后端 P1（admin/users CRUD+deletion-preview+初始密码一次返回）；后端 P2（Provisioning 状态机）；05 §12.2 错误码。
- **验收标准**：① 普通 User 访问 `/admin/*` 被守卫阻止、直接请求 403/404、Account 固定来自登录 Session；② 创建成功一次性展示初始密码（可复制）且关闭后不可再取；③ 创建表单无角色选择（固定 user）、不能创建/提升 account_admin；④ 重置按钮只对严格低级别显示、确认弹窗提示设备退出/对话记忆保留/Key 不撤销、成功后一次性展示新密码；⑤ 禁用弹窗提示会话与 Key 立即失效、对话记忆不受影响；⑥ 删除前展示影响范围、只确认/取消不重输密码；⑦ `/admin/settings` 仅展示 Account 基本信息占位。
- **对应章节**：13 §79/§84/§85；06 §13.2/§13.9/§14.5；05 §12.6。

### 98.7 P4-E2：成员数据只读视图与共享内容管理

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：Subject 数据视图组件与「以 {Actor} 身份查看 {Subject} 的数据」横幅；`/admin/users/{id}/data` 成员只读检索；`/admin/users/{id}/sessions|resources|skills` 只读；成员 Skill「发布为共享」；`/admin/users/{id}/api-keys` 元数据查看与撤销；`/admin/shared-resources` 与 `/admin/shared-skills` 管理页（与 `/app` 共享页同组件）。
- **范围 Out**：成员数据下载/导出/修改/删除/Watch/发布私有 Resource；成员私有 Skill 编辑/删除/恢复；代创建 Key 或取明文；成员 Session 删除/恢复。
- **依赖**：P4-E1；后端 P2（admin 成员只读 API、publish、account 共享管理 API）。
- **验收标准**：① 成员数据页固定 Actor/Subject 横幅、不允许无痕替换身份；② 隐藏全部修改/导出/下载/Watch/发布/删除按钮；③ 成员 Key 页只显示元数据与掩码、无明文无代创建；④ `/admin/shared-resources` 显示 provisioning/failed 占位行、导入弹窗显示「保存到：{Account} 共享 Resource」无归属切换；⑤ `/admin/shared-skills` 支持创建/上传/整体替换/删除/恢复、名称不可编辑；⑥ 成员 Skill 发布确认弹窗展示全部影响与不可取消提示；⑦ 越权动作 UI 无入口、直接请求后端 403 并审计。
- **对应章节**：13 §84.2/§86；06 §13.3；09 §38/§44；10 §53/§58；05 §12.6。

### 98.8 P4-E3：角色、审计、Activity、监控与回收站

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：`/admin/roles`（只读三内置角色与权限矩阵）；`/admin/audit`（时间/Actor/Subject/动作/Scope/结果/Request ID 多条件筛选）；`/admin/activity`（当前 Account 共享任务与可取消任务）；`/admin/monitoring`（业务健康摘要）；`/admin/recycle-bin`（按对象类型分组展示与恢复、权限按类型分别校验）。
- **范围 Out**：角色创建/编辑/删除/权限分配；底层组件监控；成员私有对象任务日志；他人私有 Skill/Session 恢复入口。
- **依赖**：P4-E1；后端 P1（审计基础、audit-events）；后端 P2（activity、monitoring、recycle-bin 与类型化恢复权限）。
- **验收标准**：① roles 只读、无管理入口；② audit 支持筛选、详情不展示任何敏感字段；③ activity 仅当前 Account 共享任务、取消需 `task.cancel.account_shared`+目标写权限、不展示原始 Task ID/堆栈/Worker 路径；④ monitoring 仅共享失败任务/Provisioning 摘要/待清理数量；⑤ recycle-bin 按类型分组展示并可恢复性标注、不可恢复类型不显示；⑥ 恢复按对象类型携带对应权限码、`SKILL_NAME_CONFLICT`/`RESTORE_WINDOW_EXPIRED` 正确展示、恢复写审计。
- **对应章节**：13 §87–§88；06 §13.7/§14.6；05 §12.6 注。

### 98.9 P4-E4：/platform 平台管理（Account、Provisioning 重试、平台级 Subject 视图）

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：`/platform` 布局与守卫（仅 PSA）；`/platform/accounts`（列表/状态筛选/创建含首位 Admin 与一次性初始密码/删除预览与删除/Provisioning 重试）；`/platform/accounts/{id}/users`（列表、提升 `user→account_admin`、平台级分级重置、Key 元数据查看与撤销）；平台级 Subject 数据视图（检索/Session/Resource/Skill 只读）；`/platform/audit|activity|monitoring|recycle-bin`（Skill 始终只读）。
- **范围 Out**：创建/重置 PSA；平台级个人 Key；Account `suspended` 操作端点；Account 切换（目标 Account 仅 Subject）；平台代用户发布/写入 Skill。
- **依赖**：P3-E1、P3-E2、P4-E1；后端 P1/P2 平台级 API 全组（含 provisioning/retry）。
- **验收标准**：① 非 PSA 访问 `/platform/*` 被守卫阻止、直接请求 403/404；② 选择目标 Account 是管理浏览不改变 Actor、页面明确 Account 上下文；③ accounts 列表含名称/code/状态/成员数/创建时间、`suspended` 只展示不操作；④ 创建 Account 表单含首位 Admin 邮箱与显示名、成功一次性展示初始密码、无创建/重置 PSA 入口；⑤ Provisioning 状态正确展示、仅 failed 显示「重试开通」并幂等调用；⑥ 提升仅 `user→account_admin`、平台级重置禁止目标 PSA、确认弹窗遵循 06 §13.9；⑦ 平台级成员数据页为只读 Subject 视图、Skill 路由全部只读；⑧ recycle-bin 按类型恢复且对 Skill 只读、audit 可按目标 Account 筛选并同时展示 Actor/Subject。
- **对应章节**：13 §89–§90/§79.3；06 §13.2；05 §12.6；09 §38/10 §53/11 §73 平台路由。

## 99. Phase 5：初始部署与生产加固（Epic P5-E1 ～ P5-E4）

### 99.1 P5-E1：生产部署单元、路由边界与 create_app 真实挂载验证

- **Plane**：优先级 urgent（首个执行，解除部署阻塞）｜ 状态 backlog
- **范围 In**：三单元部署（reverse-proxy / openviking-product-server / postgresql，沿用 VectorDB/模型/数据卷配置）；按 16.2 路由表落地（`/login`、`/app/*`、`/admin/*`、`/platform/*`、`/oauth/*`→web-platform；`/api/platform/v1/*`→Platform Router；`/api/v1/*`、`/mcp`、OAuth 协议端点→OpenViking；同源）；`/studio` 公网不挂载（外部 404、bundle 保留仅私网可挂载）；低层 Admin API 网络边界（`/api/v1/admin/*` 仅内网/VPN、trusted 不暴露公网、WebDAV/Snapshot/Pack/Debug/Observer/系统修复公网 404）；**create_app() 真实 ServerConfig 挂载验证（Spike 风险 9：07 §20 少量修改 app.py/routers/config，staging 配置模板）**；生产配置注入（环境变量/Secret Manager、CORS 同源）；Dockerfile/Compose/Helm 增加 web-platform 构建与 PG 配置。
- **范围 Out**：业务功能开发；Redis（Phase 6）；私网 Studio 启用方式选型；MCP OAuth 协议实现（P2 已闭合，本 Epic 只验证公网无 Studio 时 OAuth 走通）。
- **依赖**：Phase 1–4；Spike 57/57 与 14/14；07 §20 修改合入；部署配置模板。
- **验收标准**：① 公网路由表无 `/studio`、请求 `/studio/*` 404 且不重定向登录页；② 同源下 SPA 深链返回 200；③ 16.2 路由表逐条验证路径与处理器对应；④ create_app 真实配置完整启动、Platform Routers 注册、web-platform 静态可访问、既有路由回归无破坏；⑤ `/api/v1/admin/*` 与 trusted 公网不可达、低层运维入口 404/拒绝；⑥ 镜像/前端环境变量/仓库无 Root Key/数据库口令/签名密钥明文；⑦ 无 Studio 时同设备与跨设备 MCP OAuth 完整走通、浏览器无 User API Key。
- **对应章节**：06 §13.6/§14.3–14.4/§16.1–16.3；07 §18.5/§19/§20/§21 条目 6–7；Spike README §4.3 风险 9。

### 99.2 P5-E2：一次性初始化与生产运维可观测性

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：按 15.2 八步初始化顺序落地（全新 schema→一次性命令建首位 PSA→PSA 建 Account+首位 Admin→Worker 初始化 namespace→Admin 建 User→用户建 Key→配置客户端→三凭证一致验证）；bootstrap 升级为正式部署命令（种子+建 PSA，环境变量注入密码、重复执行幂等拒绝）；新 IAM 签发 Key 全链路、无旧 Key 导入/双写路径；17.1 审计事件清单生产验证；17.2 日志关联（Request ID 贯通 HTTP/审计/trace/任务，管理员跨用户事件字段完整）；17.3 健康检查扩展（PG 连通、migration 版本、Provisioning backlog、Session cleanup、待清理数量与 Purge 状态）；14.4 生产化（HTTPS、脱敏、备份加密、CORS）。
- **范围 Out**：功能开发；PSA 网页 Break-glass（Phase 6）；多实例 Redis（Phase 6）；旧数据迁移。
- **依赖**：P5-E1；Phase 1–4；Alembic 外键循环手工编排（Spike §4.2 #5）、argon2-cffi 版本锁定（#6）、`IAMAccount.code` 唯一性确认（#8）。
- **验收标准**：① 全新环境按 15.2 八步完整走通且文档化可重复；② 初始化命令幂等、密码仅一次展示、库中仅 Argon2id hash；③ 无旧 Key 导入路径、新 Key 均为 `ovk_u.*` 由 IAM 签发；④ `/health`、`/ready` 在 PG 故障/migration 落后/backlog 超阈值/Purge 停滞时非 ready 且含可读诊断；⑤ 17.1 每类事件有脱敏审计、跨用户事件含 Actor+Subject；⑥ 日志/审计/埋点无密码/Cookie/Key 明文/完整 hash/Token；⑦ 三角色经 Session 与 Key 获得一致权限与数据范围。
- **对应章节**：06 §14.4/§15.1–15.3/§17.1–17.3；13 §89.2；07 §21 条目 1/4/10/17/18/26；Spike README §4.2 #5–8。

### 99.3 P5-E3：备份恢复与回滚演练

- **Plane**：优先级 high ｜ 状态 backlog
- **范围 In**：按 15.4 落地并演练（发布前备份 PG 与数据卷、migration 向下兼容发布顺序或可验证 down、回滚后仍用 PG 凭证不回退旧 registry、不可逆 schema 先恢复备份到独立实例验证再切流量）；备份加密与恢复审计；演练剧本（备份→故障注入→恢复→检查登录/凭证/业务数据/审计连续性→outbox 一致性无重复对象）；演练记录留存（07 §21 条目 12 证据）。
- **范围 Out**：Snapshot/Pack/Backup/Import/Restore 产品化（仅私网运维）；生产真实故障处置。
- **依赖**：P5-E2；Phase 1 migration 基线；备份工具选型。
- **验收标准**：① 至少一次完整备份-恢复演练并留存记录；② 恢复后登录/Key/审计正常、PG 与数据卷一致（抽查 Resource/Skill/Session 无重复）；③ 完成一次应用版本回滚演练、回滚后仍以 PG IAM 鉴权；④ 不可逆 migration 具备独立实例验证步骤且演练通过；⑤ 备份加密生效、恢复操作有审计；⑥ 恢复后 outbox/reconciler 一致、failed 可经重试接口幂等恢复。
- **对应章节**：06 §14.4/§15.4；07 §21 条目 11–12；05 §11.3/§12.6；Spike README §4.3 风险 11。

### 99.4 P5-E4：安全加固与 26 条生产验收门禁

- **Plane**：优先级 urgent（上线门禁）｜ 状态 backlog
- **范围 In**：07 §21 全部 26 条在生产类环境逐条执行并留存证据；18.5 安全测试全量执行（IDOR/篡改、Header spoofing、Cookie 属性、CSRF/爆破/fixation/replay、日志脱敏、末位 Admin 保护、授权不可绕过、共享写不可绕过、/studio 缺省 OAuth、软删物理边界、Watch/Task 越权、远程来源防护、来源 URL 密文、Upload ID、Node ID、低层入口封禁）；14.5 高风险操作确认弹窗+审计复验；18.1–18.5 五类测试生产配置下回归全绿；上线 Go/No-Go 报告（26 条+安全+备份回滚+审计可观测性+健康检查证据齐备，无 P0/P1 遗留）。
- **范围 Out**：外部渗透测试采购（可选）；Phase 6 可选项；私网 Studio 启用验证。
- **依赖**：P5-E1～E3；Phase 1–4 测试基线。
- **验收标准**：① 26 条验收逐条有证据；② 18.5 全通过（含 `/studio` 公网 404、无 Studio OAuth、低层封禁）；③ 五类测试生产配置全绿且与 07 §18 清单对应；④ 安全缺陷全修复无 P0/P1 遗留；⑤ Go/No-Go 报告完成且结论可追溯。
- **对应章节**：06 §14/§17；07 §18.5/§21/§22；13 §92。

## 100. 测试策略与验收映射

### 100.1 07 §18 五类测试 → Phase 归属

| 07 §18 类别 | 编写 Phase | 执行/收口 Phase | 说明 |
| --- | --- | --- | --- |
| 18.1 单元测试（19 项） | Phase 1（主体）+ Phase 2（内容类：URI 分类器、默认私有目标、Lifecycle/Stage 分离、Watch Eligibility） | Phase 1–2 全绿，Phase 5 随生产配置回归 | Spike `security.py`/`principals.py`/`services/*` 为参考实现；正式单测在 Phase 1 重建 |
| 18.2 API 集成测试 | Phase 1（认证类）+ Phase 2（内容/Provisioning 类）+ Phase 4（管理类） | Phase 2 主体收口，Phase 4 补齐，Phase 5 全量回归 | Spike verify.py 12 组断言全部升级为 `tests/platform/` 种子（§100.2） |
| 18.3 凭证与集成测试 | Phase 1 + Phase 2 | Phase 2 主体收口，Phase 5 完成公网断言 | OAuth 渠道测试依赖 P2-E6（MCP OAuth 存储迁移）闭合 |
| 18.4 前端 E2E（23 项） | Phase 3（app 域）+ Phase 4（admin/platform 域） | Phase 3–4 收口，Phase 5 真实 bundle 冒烟 | 对应 13 §92 页面级验收 10 条 |
| 18.5 安全测试（17 项） | Phase 1–2（逻辑类先行）+ Phase 5（生产类全量） | **Phase 5 全量收口**（P5-E4 门禁） | 与 07 §21 条目 6/7/13/16 直接对应 |

### 100.2 Spike verify.py 断言 → 正式集成测试升级关系

| verify.py 组 | 断言摘要 | 升级为（07 §18） | 归属 Phase |
| --- | --- | --- | --- |
| 1 登录/Session/CSRF/me | Cookie≥256bit、PSA 无 Skill 写权限 | 18.1+18.2+18.4 | P1+P3 |
| 2 统一 LOGIN_FAILED | 错误密码与未知邮箱同码 | 18.1+18.5 | P1+P5 |
| 3 PSA 建 Account+首位 Admin | ov 映射、角色固定、初始密码 | 18.2（正式版含 outbox/Worker 状态流，spike 为同步模拟需升级） | P1+P2 |
| 4 Session vs API Key 同 Principal | 同 user_id、仅认证方式不同、Key 不能做 CSRF 写 | 18.1+18.3 | P1+P2 |
| 5 Actor/Subject 预览 | actor≠subject、user_private | 18.2+18.4 | P2+P4 |
| 6 改密旧密码+轮换 | LOGIN_FAILED、Set-Cookie 同步 | 18.1+18.5 | P1 |
| 7 重置撤销 Session 保留 Key | sessions_revoked、旧 Session 401、Key 可用 | 18.2 | P1+P2 |
| 8 严格 rank+跨 Account 404 | 同级 403、跨 Account 404 | 18.1+18.2 | P1 |
| 9 按名撤销互不影响 | key1 死 key2 活、幂等 | 18.3 | P1 |
| 10 禁用即时生效 | Session 与全部 Key 立即拒绝 | 18.2+18.3 | P1+P2 |
| 11 提升免重登生效 | permission_version 缓存失效 | 18.1+18.2 | P1+P2 |
| 12 Provisioning 重试守卫 | active 重试 409 | 18.2+13 §89.2 页面动作 | P2+P4+P5 |

`verify_integration.py`（14 项同进程断言）升级：同进程导入→`tests/platform/test_same_process_integration.py`（P1）；UserIdentifier 映射→18.1（P1）；Principal→RequestContext（含 platform-gateway）→18.1（P1）；rank 两套体系→18.1（P1）；namespace ACL 兜底→18.1+18.2（P2）；create_app 真实挂载→P5-E1 正式集成测试（P5）。

### 100.3 07 §21 验收清单 26 条 → Phase 归属

| 条目 | 摘要 | 满足 Phase | 关键测试/页面 |
| --- | --- | --- | --- |
| 1 | 产品登录进 `/app`，浏览器不依赖 API Key，明文仅一次 | 3 | 13 §82.4 E2E |
| 2 | /admin 管理本 Account 用户、查看内置角色、不能建/编/删角色或提升 Admin | 4 | 13 §85/§87.1 |
| 3 | 三角色权限与数据范围后端真实生效 | 2 | 18.2 隔离测试 |
| 4 | 管理员读成员数据、审计记录 Actor+Subject | 4 | 13 §87.2/§90 |
| 5 | 身份仅服务端解析、客户端身份字段无效 | 2 | 18.3+18.5 |
| 6 | 跨 Account/User/Header spoofing/IDOR 全通过 | 5 | 18.5（P2 先行基线） |
| 7 | 公网不挂 /studio；SDK/CLI/插件/MCP 用新 Key/OAuth | 5 | P5-E1 |
| 8 | 三凭证同一套实时 RBAC、渠道不能切换身份 | 2 | 18.3（依赖 P2-E6 闭合） |
| 9 | 禁用/改密/角色变更即时生效 | 2 | 18.1+18.2 |
| 10 | 管理与高风险操作全审计 | 4 | 17.1+14.5（P5 复验） |
| 11 | Provisioning 失败可见可重试不重复 | 4 | 05 §12.6 + 13 §89.2 |
| 12 | 备份恢复与版本回滚演练 | 5 | P5-E3 |
| 13 | 软删 30 天恢复、期满物理清理、审计保留、Skill 冲突失败 | 4 | 13 §88 |
| 14 | 用户与 Account Admin 不能切换 Account | 3 | 18.4 |
| 15 | 多具名 Key 分别创建/撤销、明文一次、禁用全阻断 | 3 | 13 §82 |
| 16 | v0.1 无 Service Account/Key/机器 Principal | 2 | 18.3 |
| 17 | PSA 建 Account+首位 Admin、Admin 直建 User、无注册/邀请 | 4 | 13 §85.2/§89.2 |
| 18 | 初始/重置密码可复制长期用、不存明文、不可再取 | 4 | 13 §85 |
| 19 | 重置仅严格上级对下级、同级拒绝、只撤销登录 Session | 4 | 18.1+18.2+页面 |
| 20 | 私有/共享语义全入口一致、无「公共」含糊表述 | 3 | 18.3+18.4 |
| 21 | 默认新增私有区、共享只读、任何渠道不能写/删共享区 | 2 | 18.2+18.3 |
| 22 | Admin 管本 Account 共享、PSA 管目标 Account Resource/Skill 只读 | 4 | 13 §86/§89 |
| 23 | Skill 名称全局唯一、不可修改、删除释放、恢复冲突失败 | 2 | 18.2 |
| 24 | 仅 Account Admin 原地发布 Skill（ID/名称不变、无副本、不可取消） | 4 | 10 §58.4 + 13 §86.2 |
| 25 | Skill 在线创建/SKILL.md/ZIP 上传/整体替换、网页无执行器 | 3 | 18.4 |
| 26 | 仅本地邮箱密码登录、无 OIDC/企业登录/占位 | 3（5 复验） | 13 §80 + P5-E1/E4 |

Phase 汇总：P2 满足 #3/5/8/9/16/21/23 ｜ P3 满足 #1/14/15/20/25/26 ｜ P4 满足 #2/4/10/11/13/17/18/19/22/24 ｜ P5 满足 #6/7/12。

## 101. 风险与未决项

| 风险/未决项 | 说明 | 处置 |
| --- | --- | --- |
| create_app() 真实挂载（Spike 风险 9） | 类型/模块层已 14/14 验证；完整挂载需真实 ServerConfig | P2-E1 + P5-E1 两处验证点 |
| MCP OAuth SQLite→PG（Spike 风险 10） | 独立工作项，阻塞 07 §21 条目 8 与 18.5 OAuth 断言 | P2-E6 闭合，Phase 2 收口时明确标记 |
| Provisioning outbox/worker（Spike 风险 11） | Spike 仅验证状态机与重试守卫 | P2-E1 实现，P5-E3 恢复侧验证 |
| `IAMAccount.code` 唯一性未定（Spike §4.2 #8） | 设计 04 §10.1 只有 `ov_account_id` 唯一 | P1-E1 首周确认后回填设计 |
| Skill 名称占用与 Resource URI 占用不对称 | 已确认的刻意设计（12 号清单 #7） | 前端文案与错误码差异化实现（P2-E4/P3-E5/P4-E3） |
| 多 SubAgent 并行开发冲突 | Epic 间共享后端模块（Facade/Registry） | 按 Epic 隔离 worktree；共享模块任务串行指派（P2-E2 先行） |

## 102. Plane 项目导入说明

- 项目：`OpenViking Platform v0.1`（建议 identifier `OVP`）。
- Cycles：Phase 1–5 各一个，含目标与范围（见 §95 里程碑表）。
- Epic：本文 P1-E1～P5-E4 共 24 个 Epic，标题与编号原样导入；每个 Epic 的验收标准复制到描述。
- Work Items：开发时按 Epic 细拆 story，挂到对应 Epic，标注优先级与依赖。
- 状态流：backlog → todo → in_progress → completed；验收标准作为 completed 门禁。

## 103. 变更记录

| 日期 | 修订 | 说明 |
| --- | --- | --- |
| 2026-08-18 | 初稿 | 基于 Design v0.1（design-v0.1.0）与 Spike 结论（57/57+14/14）产出 Phase 1–5 共 24 个 Epic；含测试策略映射与 26 条验收归属。 |
