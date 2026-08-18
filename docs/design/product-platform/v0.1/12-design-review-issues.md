# 12 Design v0.1 设计审查问题清单

> 审查日期：2026-08-18
> 审查对象：Design v0.1 全部 11 篇设计文档
> 基线：OpenViking v0.4.12（`c1d38eb47ff2ebf9ff4cee46756728893fd8caf3`）
> 方法：文档内部一致性 + 文档与源码契约交叉核对（逐项给出源码证据）

本清单记录设计审查发现的问题，按严重度分为四类。修订设计时逐项消化，处置结果在「处置」列标注。

## 一、实质问题（建议修订）

### 1. Session 标题 display_name 与「不新增标题字段」矛盾

- **位置**：11 号文档 §70.2（原 §64.2）
- **问题**：文档同时声称「v0.1 不新增标题字段」和「列表优先显示接入客户端提交且经过脱敏校验的 `display_name`」，但源码 `SessionMeta`（`openviking/session/session.py:416-458`）**没有 display_name 字段**。Studio 的标题是前端从首条消息截取前 20 字符写入 localStorage（`web-studio/src/lib/sessions/use-chat.ts:536-543`），不是服务端字段。
- **影响**：接入客户端的 display_name 存在哪里未定义——新增 OpenViking 字段违反「不新增标题字段」，存 PostgreSQL 违反「不复制业务正文」哲学（04 §10.10）。
- **建议**：明确方案后再落地：a) 在 Session Meta 增加可空 `display_name`（产品化对源码的最小字段扩展）；或 b) 产品侧仅使用客户端传入的展示名且不持久化；或 c) 明确不展示标题，仅显示客户端名 + Session ID 短标识。

### 2. Skill 发布的 OpenViking 执行细节缺失

- **位置**：04 §10.10、10 §58.2
- **问题**：设计只定义了 PostgreSQL 侧归属转换（`visibility/owner_user_id/ov_uri` 一并改写），但发布时 `viking://user/{U1}/skills/foo → viking://agent/skills/foo` 的**物理迁移动作**未定义：文件移动、索引重建、search_tags 同步、旧目录清理、失败补偿。
- **影响**：若执行中途失败，PG 引用与 OpenViking 实际数据不一致；恢复和重试语义缺失。
- **建议**：补一段「发布执行与失败补偿」流程：事务中写 PG 转换 → 调度 OpenViking mv（幂等）→ 成功确认 → 失败回滚 PG 或标记 failed 可重试；明确旧 URI 物理清理归属（Purge Worker 还是发布事务内）。

### 3. `POST /auth/password/change` 不要求旧密码

- **位置**：05 §12.3
- **问题**：密码修改仅凭登录 Session（权限未定义）。若设备失窃或会话被接管，攻击者可立即改密并永久锁定目标用户（v0.1 无网页 Break-glass，管理员重置也只能由上级执行）。
- **影响**：账号接管后不可恢复性增强；与「高风险操作」清单（06 §14.5）的防护意图不符。
- **建议**：要求请求体携带旧密码（`old_password`），服务端校验后再改密并轮换当前会话；不通过时返回统一凭证错误。

### 4. `permission_version` 缓存失效缺口

- **位置**：03 §9.4
- **问题**：权限缓存键为 `(account_id, user_id, permission_version)`，是用户级版本号。角色权限表本身（`iam_role_permissions`）的变更——例如 migration 更新内置角色、种子变更——无法触发任何用户的 `permission_version` 递增。
- **影响**：全局权限变更后缓存可能继续返回旧权限，最长存活一个缓存 TTL。
- **建议**：增加全局/角色级权限版本号（如 `iam_roles.permission_version` 或全局 `permission_schema_version`）参与缓存键；或角色权限变更时批量递增受影响用户版本。

## 二、产品决策确认点（需要用户确认）

### 5. recycle-bin 恢复权限语义含糊

- **位置**：05 §12.6 `/admin/recycle-bin/*`、`/platform/recycle-bin/*`
- **问题**：恢复接口使用笼统的「Account 范围恢复权限」「平台范围恢复权限」，但各对象恢复策略不同：Session「管理员不能恢复他人」（11 §72）、私有 Skill「不能恢复他人」（10 §59）、Account 共享 Resource/Skill 可恢复（09/10）、User 恢复走回收站（05 §12.6 无专用接口）。
- **影响**：单一入口跨对象类型的权限矩阵未枚举，实现时容易放错或漏掉。
- **处置**：

### 6. Skill 发布「不需属主审批 + 不可取消」与 Resource「只能发布自己的」不对称

- **位置**：10 §52.6/§58 vs 09 §44.1
- **问题**：Account Admin 可把本 Account 任意 User 的私有 Skill 原地转为共享且无需审批、不可取消；但 Resource 发布只允许发布自己的私有 Resource。两者不对称且设计未给理由。
- **影响**：属主失去内容控制权（无法阻止、无法撤回），属于权限失衡点。
- **处置**：

### 7. Resource 与 Skill 软删除后的占用策略不对称

- **位置**：04 §10.10 vs 10 §55.3
- **问题**：Resource 的 `ov_uri` 唯一约束占用到物理清理（30 天）；Skill 名称软删除立即释放（恢复时同名冲突才失败）。
- **影响**：两套回收语义并存，前端文案和回收站展示需要区分；恢复截止时间展示要按对象类型差异化。
- **处置**：

### 8. 旧生产数据的可见性

- **位置**：01 §15.3（仅含糊一句）
- **问题**：新 IAM Account 使用全新 `ov_account_id`，现有生产实例的 memory/resource/session 在新产品中不可达（仅私网 Studio 用旧 Key 可见）。设计只在开发数据一节带过，未作为正式产品决策记录。
- **影响**：若用户预期旧记忆在新产品可见，上线后会产生「数据丢失」观感。
- **处置**：

### 9. 内置角色的 Account 归属未定义

- **位置**：04 §10.4
- **问题**：只定义了平台级 System Role `account_id` 可空，未说明 `account_admin/user` 是每 Account 复制一份角色行，还是全局单行 + `iam_user_roles` 校验 Account 匹配（03 §9.4「Account Admin/User 与 Role 必须属于同一 Account」的实现方式未定）。
- **影响**：影响 schema、migration 和权限种子设计。
- **处置**：

## 三、与源码的差异点（实现时需新增约束）

### 10. 标签 20 项 / 40 字符限制源码未实现

- **证据**：`openviking/utils/tags.py` 仅校验严格 `key=value`（恰好一个 `=`、key/value 非空、strip 小写、去重），无数量/长度上限。设计（04 §10.10、09 §40.3）规定「每对象最多 20 项、每项最多 40 字符」。
- **影响**：这是产品新增校验，需在 Product Facade/Content Registry 落地，并明确产品限制与 OpenViking 引擎宽松度的分界（低层 API 是否同限）。

### 11. Turn-aware Retention 默认值来源需明确

- **证据**：设计（11 §71.1，原 §65.1）写「默认保留最近 3 个逻辑 Turn、6000 Token 预算」；源码中 3/6000 是 VikingBot 插件默认值（`bot/vikingbot/hooks/builtins/openviking_hooks.py:198-201`），引擎兜底为 3 Turn / 12000 Token（`openviking/session/session.py:1724-1730`）。
- **影响**：产品默认值取哪个未定义，涉及接入客户端（Codex 等）行为一致性。

### 12. `platform-gateway` 方案可行但双层审计不一致

- **证据**：`viking://resources/**` 在源码中无条件放行（`viking_fs.py:2959-2960`、`namespace.py:271-272`），且整条访问链不校验 user 是否注册，因此 `platform-gateway` 执行占位可行。
- **影响**：OpenViking 层日志/审计记录的是 `platform-gateway` 或 Subject 身份，只有产品审计保留 Actor/Subject（02 §7.3）。双层审计不一致需确认可接受，并在排障时说明。

### 13. `agent/endpoints|tools|payments` 源码默认全局可读

- **证据**：`viking_fs.py:2971-2974` 对新格式 `agent/skills|endpoints|tools|payments` 子路径全局可读（account scope）。
- **影响**：设计（02 §7.5）将 `agent/endpoints|tools|payments` 列为 `internal` 拒绝，产品层必须通过 Target Policy 强制覆盖源码的放行行为——这是硬依赖，低层 API 一旦漏接即形成旁路。

## 四、表述与编号小问题

| # | 位置 | 问题 |
| --- | --- | --- |
| 14 | 06 §13.1 | 前端目录树残留 `routes/app/memories/`，与「不设置 /app/memories 顶级页面」（06 §13.7、11 文档）矛盾 |
| 15 | 10/11 文档编号 | 10 号文档占 §50-65，11 号文档原从 §60 开始重叠；已重编号为 §66-78，文档内无交叉引用断链 |
| 16 | 05 §12.3 | `logout`、`logout-all` 未标 CSRF，与 03 §8.2「所有修改请求同时满足 CSRF 校验」不一致 |
| 17 | 04 §10.2 vs 05 §12.3 | `username`（Account 内唯一）与 `auth/me` 返回的 `code` 的关系、是否必填、是否可改未定义；登录只用 email，username 用途不明 |
| 18 | 01/06/08 | Studio OAuth 路由写作 `/studio/oauth/consent`、`/studio/oauth/verify`；源码路由实际为 `/oauth/consent`、`/oauth/verify`（web-studio 独立 SPA，前缀来自挂载路径），需统一表述 |
| 19 | 05 §12.5、10 §61 | `PUT /api/platform/v1/me/skills/{id}` 与 `POST /api/platform/v1/sessions` 的请求体契约（SKILL.md 字符串 / upload_id / JSON、客户端标识与幂等字段）未定义 |

## 处置记录

| # | 处置 | 修订人 | 日期 |
| --- | --- | --- | --- |
| 1 | 保持现状：不新增标题字段、不展示标题；11 §70.2 已修订 | 助手 | 2026-08-18 |
| 2 | 采用「PG 转换 + 受控 mv 迁移任务」方案；10 文档新增 §58.4 | 助手 | 2026-08-18 |
| 3 | 改密要求旧密码；03 §8.3、05 §12.3 已修订，07 测试已补用例 | 助手 | 2026-08-18 |
| 4 | 增加全局权限版本；03 §9.4、04 §10.5 已修订 | 助手 | 2026-08-18 |
| 5 | 回收站按对象类型分别校验恢复权限；05 §12.6 注 + 06 §14.6 已修订 | 助手 | 2026-08-18 |
| 6 | 维持不对称（用户确认） | 用户 | 2026-08-18 |
| 7 | 维持不对称（用户确认）：Skill 名称即调用标识、删除立即释放；Resource URI 位置语义、占用到物理清理；10 §55.3 已补说明 | 用户 | 2026-08-18 |
| 8 | 关闭：v0.1 无旧数据，全新起步（用户确认） | 用户 | 2026-08-18 |
| 9 | 内置角色全局单行；04 §10.4/§10.6 已修订 | 助手 | 2026-08-18 |
| 10 | 标签 20/40 限制由 Product Facade 强制；04 §10.10 已补注 | 助手 | 2026-08-18 |
| 11 | 产品默认 12000 Token；11 §71.1 已修订 | 助手 | 2026-08-18 |
| 12 | 底层日志不用于产品审计结论；06 §17.2 已补写 | 助手 | 2026-08-18 |
| 13 | 设计已强制 Target Policy 覆盖，无需修订 | 助手 | 2026-08-18 |
| 14-19 | 一次修掉：06 §13.1 删 memories 残留；11 文档重编号 §66-78；05 §12.3 logout 补 CSRF；04 §10.2 username=code；OAuth 路由表述统一（03/06/08）；10 §61.1、05 §12.5 契约补全 | 助手 | 2026-08-18 |
