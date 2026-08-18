# OpenViking 产品化平台 Design v0.1

> 状态：讨论中<br>
> 上游源码基线：OpenViking v0.4.12<br>
> 基线提交：`c1d38eb47ff2ebf9ff4cee46756728893fd8caf3`<br>
> 目标读者：产品负责人、前端工程师、后端工程师、运维与安全负责人

## 文档导航

1. [产品定位、术语与源码基线](01-product-positioning-and-baseline.md)
2. [目标架构与身份上下文](02-architecture-and-identity.md)
3. [认证与授权](03-authentication-and-authorization.md)
4. [PostgreSQL 数据模型](04-data-model.md)
5. [后端模块与 API](05-backend-and-api.md)
6. [前端、安全、初始部署与运维](06-frontend-security-and-operations.md)
7. [测试、实施边界与架构决策](07-quality-rollout-and-decisions.md)

## 已确认设计决策

- OpenViking 作为上下文与记忆引擎，产品平台采用模块化单体。
- 新建产品前端与管理界面；现有 `/studio` 保留为运维控制台。
- 浏览器登录使用服务端登录 Session，不使用或持久化 Root/User API Key；用户主动创建 API Key 时只展示一次明文，随后由用户保存到集成客户端。
- 权限层级包含 Platform Super Admin、Account Admin 和 User。
- 邮箱是必填且全局唯一的登录标识，一个 User 只属于一个 Account。
- Platform Super Admin 创建 Account 和首位 Account Admin；Account Admin 直接创建本 Account 的普通 User，不提供注册、邀请或激活流程。
- 创建用户或由上级重置密码时，系统生成可复制的初始密码并只展示一次；该密码可长期使用，用户不被强制修改，由管理员在线下自行交接。
- 管理员只能重置严格低级别用户的密码，不能重置同级；重置后撤销目标用户全部登录 Session，但不删除 OpenViking 对话 Session，也不自动撤销 API Key。
- 管理员跨用户读取必须同时保留 Actor（操作者）与 Subject（数据归属者）。
- 高风险操作使用展示影响范围的确认弹窗，不要求重输密码、输入 Account 名称或双人审批。
- Account、用户及其数据使用 30 天软删除与恢复窗口，期满后异步物理清理。
- SDK、CLI、插件和 MCP 使用用户 API Key 或用户 OAuth Token，均代表授权用户本人，并实时继承同一套 RBAC 与数据范围。
- 业务数据不使用含糊的“公共/私有”表述，统一分为 **Account 共享数据** 与 **User 私有数据**；共享只表示同一 Account 内可见，不表示互联网公开或跨 Account 可见。
- `viking://resources/**` 是 Account 共享 Resource；`viking://user/{ov_user_id}/resources/**` 是 User 私有 Resource。普通 User 默认新增到自己的私有区，只读 Account 共享 Resource；Account Admin 管理本 Account 共享 Resource。
- `viking://agent/skills/**` 是 Account 共享 Skill；`viking://user/{ov_user_id}/skills/**` 是 User 私有 Skill。普通 User 可读取和使用共享 Skill，但只能管理自己的私有 Skill；Account Admin 管理本 Account 共享 Skill。
- `/api/platform/v1`、`/api/v1`、MCP、SDK、CLI 和插件必须经过同一个后端授权门；换一种调用渠道不能扩大权限，也不能绕过上述共享/私有规则。
- v0.1 不提供 Service Account 或 Service Account Key；内部 Worker 使用不对外签发凭证的系统身份。
- v0.1 是产品初版，不导入或兼容旧 Account/User/API Key 门禁；现有协议入口可被选为首版契约，但鉴权统一接入新 IAM。

## 变更记录

| 日期 | 修订 | 说明 |
| --- | --- | --- |
| 2026-08-18 | Design v0.1 初稿 | 基于 OpenViking v0.4.12 建立产品化、IAM 与 RBAC 总体设计。 |
| 2026-08-18 | Design v0.1 补充 | 明确三级数据范围、Actor/Subject 审计、弹窗确认与 30 天软删除。 |
| 2026-08-18 | Design v0.1 凭证修订 | 明确插件/MCP 为用户委托型集成，用户 API Key 统一继承 RBAC；Service Account 和旧门禁兼容均不进入 v0.1。 |
| 2026-08-18 | Design v0.1 用户创建与密码修订 | 取消注册和邀请流程；明确管理员直建用户、长期初始密码手工交接、禁止同级重置及登录 Session 撤销。 |
| 2026-08-18 | Design v0.1 数据可见性修订 | 根据 v0.4.12 源码核对 Resource/Skill 命名空间；明确 Account 共享与 User 私有边界、默认写入位置及全入口统一授权。 |

## 待继续讨论

- OIDC/企业登录进入哪个设计版本。
- Studio 的生产访问边界。
