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
6. [前端、安全、迁移与运维](06-frontend-security-and-operations.md)
7. [测试、实施边界与架构决策](07-quality-rollout-and-decisions.md)

## 已确认设计决策

- OpenViking 作为上下文与记忆引擎，产品平台采用模块化单体。
- 新建产品前端与管理界面；现有 `/studio` 保留为运维控制台。
- 浏览器使用服务端 Session，不保存 OpenViking Root/User API Key。
- 权限层级包含 Platform Super Admin、Account Admin 和 User。
- 管理员跨用户读取必须同时保留 Actor（操作者）与 Subject（数据归属者）。
- 高风险操作使用展示影响范围的确认弹窗，不要求重输密码、输入 Account 名称或双人审批。
- Account、用户及其数据使用 30 天软删除与恢复窗口，期满后异步物理清理。
- 现有 API Key、OAuth、SDK、CLI、MCP 和 `/api/v1/*` 保持兼容。

## 变更记录

| 日期 | 修订 | 说明 |
| --- | --- | --- |
| 2026-08-18 | Design v0.1 初稿 | 基于 OpenViking v0.4.12 建立产品化、IAM 与 RBAC 总体设计。 |
| 2026-08-18 | Design v0.1 补充 | 明确三级数据范围、Actor/Subject 审计、弹窗确认与 30 天软删除。 |

## 待继续讨论

- 邮箱登录标识及邮箱唯一性范围。
- 是否在 v0.1 开放自定义角色，或仅提供三个内置角色。
- Account 和首位 Account Admin 的创建入口。
- 共享资源的普通用户写权限。
- OIDC/企业登录进入哪个设计版本。
- Studio 的生产访问边界。
