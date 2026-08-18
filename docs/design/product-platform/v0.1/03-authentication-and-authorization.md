# 03 认证与授权

> Design v0.1 · [返回版本索引](README.md)

## 8. 认证设计

### 8.1 浏览器认证决策

第一阶段使用“不透明服务端会话”，不使用浏览器长期 JWT：

- Cookie 名：`__Host-ov_session`
- 属性：`HttpOnly; Secure; SameSite=Lax; Path=/`
- Cookie 值：至少 256 bit 的随机 token。
- 数据库仅保存 `SHA-256(token)`，不保存明文 token。
- 默认空闲有效期：24 小时，可配置。
- 默认绝对有效期：30 天，可配置。
- 登录、提权、密码修改后轮换 Session。
- 登出、禁用用户、修改密码后撤销相关 Session。

选择不透明 Session 的理由：

- 可立即撤销。
- 不把角色和权限快照固化进长期 Token。
- 权限变更下一次请求立即生效。
- 与当前 OAuth 存储“明文只给客户端、服务端存 hash”的安全习惯一致。

### 8.2 CSRF 防护

所有修改请求同时满足：

1. Cookie `SameSite=Lax`。
2. 校验 `Origin` 或 `Referer` 属于允许的产品源。
3. 非 `GET/HEAD/OPTIONS` 请求要求 `X-CSRF-Token`。
4. CSRF Token 与 Session 绑定，前端只能读取 CSRF Token，不能读取 Session Cookie。

### 8.3 密码认证

- 密码使用 Argon2id 哈希。
- 数据库保存 hash、算法版本和最近修改时间，不保存明文或可逆密文。
- 登录错误使用统一响应，避免枚举账号。
- 按 IP、Account 和登录标识限流。
- 连续失败进入递增冷却，不直接永久锁死账号。
- 密码重置使用一次性、短期、哈希存储的 reset token。

### 8.4 OIDC/企业登录

第二阶段增加外部身份绑定：

- `password`、`oidc`、`wechat_work` 等 Provider 写入 `iam_identities`。
- 外部 Provider 返回的 subject 映射到内部 `(account_id, user_id)`。
- 外部登录不能直接指定 OpenViking Account/User。
- 首次登录创建用户时走受控邀请或自动 Provisioning 策略。

当前 `openviking/server/oauth` 是 MCP 客户端授权服务，不应直接当成产品用户登录系统复用。

### 8.5 API Key 与 OAuth 兼容

- 现有 `api_key` auth mode 保持不变。
- 现有 User API Key 继续用于 SDK、CLI、MCP 上游和自动化集成。
- 现有 OAuth 2.1 继续叠加在 API Key 模式上。
- 产品网页登录不把 User API Key 写入 `sessionStorage` 或 `localStorage`。
- 若未来需要个人访问令牌 PAT，应单独支持多 Key、scope、到期和逐 Key 撤销，不复用单 Key 模型硬扩展。

## 9. RBAC 权限设计

### 9.1 权限命名规则

数据类 Permission Code 采用 `<domain>.<action>.<scope>`；管理类 Permission 可采用 `<domain>.<action>`：

```text
account.read
account.update
account.delete
account.read.platform
account.manage.platform

user.read
user.invite
user.create
user.update
user.disable
user.delete
user.credential.rotate
user.read.account
user.read.platform

role.read
role.create
role.update
role.delete
role.assign

memory.read.self
memory.write.self
memory.delete.self
memory.export.self
memory.read.account
memory.read.platform
memory.write.account
memory.write.platform
memory.export.account
memory.export.platform
memory.delete.account
memory.delete.platform

resource.read.shared
resource.write.shared
resource.delete.shared

session.read.self
session.write.self
session.delete.self
session.commit.self
session.read.account
session.read.platform

skill.read
skill.use
skill.manage

audit.read
monitoring.read
system.task.read
```

不使用 `admin=true` 之类布尔值代替 Permission。角色只是 Permission 的集合。

### 9.2 内置角色与数据范围

| 角色 | OpenViking Base Role | 数据范围 | 用途 |
| --- | --- | --- | --- |
| `platform_super_admin` | 不直接映射为 `root` | 全平台 | 管理并查看所有 Account、用户和数据 |
| `account_admin` | `admin` | 当前 Account | 管理当前 Account 用户，并查看当前 Account 全部用户数据 |
| `user` | `user` | 仅自己 | 使用和管理自己的记忆、Session 与允许的共享资源，不能切换 Account |

`platform_super_admin` 是可登录的人类平台角色；`root` 是 OpenViking 机器控制身份。两者权限范围可以相近，但凭据、请求上下文和审计身份不能混用。产品登录不会签发 Root API Key，也不会生成 `Role.ROOT`。

是否在 v0.1 开放 Account 自定义角色仍待产品确认；无论是否开放，以上三个内置角色及其数据范围不可删除或改 code。

### 9.3 默认角色权限矩阵

| 权限组 | Platform Super Admin | Account Admin | User |
| --- | ---: | ---: | ---: |
| 查看 Account | 全部 | 当前 Account |  |
| 创建、停用、恢复 Account | ✓ |  |  |
| 管理用户 | 全部 Account | 当前 Account |  |
| 查看自己的记忆与 Session | ✓ | ✓ | ✓ |
| 查看其他用户的记忆与 Session | 全部 Account | 当前 Account |  |
| 修改其他用户数据 | 独立高风险 Permission | 默认无 |  |
| 导出、删除其他用户数据 | 独立高风险 Permission | 默认无 |  |
| 共享资源读取 | ✓ | ✓ | ✓ |
| 共享资源写入 | ✓ | ✓ | 待确认 |
| 查看审计 | 全平台 | 当前 Account |  |
| 系统监控 | ✓ | 当前 Account 视图 |  |

管理员查看他人数据属于授权的数据范围访问，不称为“冒充登录”。每次访问都必须记录 Actor、Subject、Action、Scope、Request ID 和结果；查看权限不能自动推导出写入、导出或删除权限。

Platform Super Admin 的内置角色包含平台级修改、导出和删除 Permission；之所以仍拆成独立 Permission，是为了逐项校验、确认弹窗和审计，而不是降低其最高权限。Account Admin 默认不包含修改、导出或删除他人数据的 Permission。

### 9.4 有效权限计算

```text
effective_permissions
  = union(all active role permissions)
  - disabled permissions
```

规则：

- Account Admin/User 与 Role 必须属于同一 Account；Platform Super Admin 使用平台级 System Role。
- System Role 不允许删除或改 code，可调整显示名和描述。
- 若后续开放自定义 Role，必须指定 `ov_base_role=user|admin`，且不能获得平台级 Scope。
- `ov_base_role=admin` 只影响 OpenViking 控制面兼容，不自动授予任何 Platform Permission。
- 用户禁用后，有效权限为空且所有登录会话失效。
- 权限结果可按 `(account_id, user_id, permission_version)` 短期缓存；角色变更递增 `permission_version`。
