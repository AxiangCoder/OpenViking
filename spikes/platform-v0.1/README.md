# OpenViking 产品平台 v0.1 技术验证（Spike）结论

> 日期：2026-08-18 ｜ 基线：OpenViking v0.4.12 ｜ 设计：`docs/design/product-platform/v0.1`（Design Frozen，`design-v0.1.0`）
> 本 Spike 是**可丢弃验证代码**（disposable），正式开发按设计文档重新实现，不直接演进本目录。

## 1. 验证目标与范围

对应 Phase 1（07 号文档 §19）的关键技术假设：

| 验证项 | 结论 |
| --- | --- |
| SQLAlchemy 2 async + asyncpg + Alembic 接入 FastAPI | ✅ 可行 |
| Argon2id 密码哈希（argon2-cffi） | ✅ 可行（主仓库已有依赖） |
| 不透明登录 Session（SHA-256 hash、轮换、撤销、CSRF） | ✅ 可行 |
| 用户 API Key（`ovk_u.<public>.<secret>`，Bearer 解析为同一 Principal） | ✅ 可行 |
| Permission 实时计算 + 双版本缓存键（permission_version / schema_version） | ✅ 可行 |
| 严格角色等级密码重置（actor_rank > target_rank）+ 撤销目标登录 Session | ✅ 可行 |
| web-platform 脚手架（Vite + React 19 + TS + TanStack Router/Query） | ✅ 可行 |

**未验证（不在 Spike 范围）**：MCP OAuth 协议端点与 SQLite→PostgreSQL 迁移、Provisioning Worker（outbox）、内容注册表（platform_content_refs）、低层 `/api/v1` 与 MCP 的 Target Policy 接入、`create_app()` 完整挂载（需真实 ServerConfig，见 §4.3 风险 9）。

## 2. 环境与运行

```bash
# PostgreSQL（spike 专用容器，用完可删）
docker run -d --name ov-spike-pg -e POSTGRES_USER=ov_platform -e POSTGRES_PASSWORD=ov_platform_dev \
  -e POSTGRES_DB=ov_platform -p 55432:5432 postgres:16-alpine

cd spikes/platform-v0.1
uv sync                                  # 安装依赖
.venv/bin/python scripts/reset_db.py     # 重建 schema + 种子 + 创建首位 PSA
cd backend && ../.venv/bin/uvicorn app:app --port 18080   # 启动后端
cd web-platform && npm install && npm run dev              # 启动前端（默认 18082）

# 端到端验证（57 项断言）
.venv/bin/python scripts/verify.py
```

PSA 默认密码：`Spike-PSA-Pass-2026-Dev`（可用 `OV_PSA_PASSWORD` 覆盖）。本地 Spike `OV_COOKIE_SECURE=0`；**生产必须 `1`**（`__Host-` 前缀强制 Secure）。

## 3. 验证结果（57/57 通过）

覆盖设计断言：统一 `LOGIN_FAILED` 防枚举；PSA 建 Account+首位 Admin、Account Admin 直建 User（一次展示初始密码）；Session/API Key 解析出**同一 Principal**（`authentication_method` 不同、权限一致）；Actor/Subject 分离；改密必须旧密码且轮换 Session；重置密码撤销全部登录 Session **但不撤销 API Key**；同级/跨 Account 重置拒绝（403/404）；API Key 按名撤销互不影响；禁用即时杀死 Session+全部 Key；角色提升经 `permission_version` 缓存失效**免重登生效**；PSA 对 Skill 只读（权限集合减法）；Provisioning 重试守卫。

## 4. 发现与决策点（正式开发前需要确认）

### 4.1 必须回填设计文档的发现

1. **`iam_roles.rank` 字段缺失**：设计 04 §10.4 角色表无等级字段，但 03 §8.3 的 `actor_role_rank > target_role_rank` 需要它。Spike 用 `iam_roles.rank`（3/2/1）实现。→ 建议回填 04 号文档。
2. **凭证优先级语义**：请求同时携带 Session Cookie 与 Bearer 时，Cookie 优先；Cookie 已失效**不自动回退** Bearer（防凭据混淆攻击）。插件/MCP 客户端不带 Cookie，不受影响。→ 建议写入 03 号文档信任边界。
3. **改密轮换必须同步 Set-Cookie**：`rotate_session` 若只撤销旧 Session 而不下发新 Cookie，客户端立即掉线（Spike 中实际踩坑并修复）。→ 写入 05 §12.3 实现约束。
4. **Platform 角色不能直接继承 Account Admin**：权限集合必须显式减去 Skill 写/用权限（`skill.*.manage/publish/use`），否则 PSA 会获得 `skill.account_shared.manage.account`。→ 写入 03 §9.2/9.3 种子实现说明。

### 4.2 技术选型确认

5. **Alembic autogenerate 外键循环**：`iam_accounts.deleted_by → iam_users` 与 `iam_users.account_id → iam_accounts` 成环，autogenerate 生成的迁移会因建表顺序失败；需手工调整（Spike 已示范：先建 users、再 `create_foreign_key`）。→ 正式 migration 需人工编排或拆两版。
6. **argon2-cffi 异常**：用 `VerifyMismatchError/VerificationError/InvalidHashError`，旧版本异常名不同（`VerifyMemoryError` 不存在于当前版本），需锁定版本。
7. **权限缓存**：Spike 用进程内 dict + 双版本键。设计 06 §16.4 允许单实例短 TTL；多实例前必须换共享后端（Redis）。
8. **`IAMAccount.code` 与 `ov_account_id` 双唯一**：Spike 的 Account 创建接口同时校验 code 唯一（PG 约束）。设计 04 §10.1 只有 `ov_account_id` 唯一——`code`（展示/路径标识）是否也要唯一未在设计明确。→ ✅ **已确认（2026-08-19）**：双唯一，`code` 为展示/路径短标识、创建后不可修改；已回填设计 04 §10.1 与 14 号开发计划。

### 4.3 未验证风险（正式开发首周验证）

9. **与现有 OpenViking 进程同进程集成**：✅ **已做类型与模块层本地验证**（`scripts/verify_integration.py`，14/14 通过）——同一解释器同时导入真实 `openviking.server.identity`（RequestContext/Role/UserIdentifier）、`openviking.core.namespace`（is_accessible 兜底）与 spike 全部 IAM 模块无冲突；`AuthenticatedUserPrincipal → RequestContext` 转换与设计 02 §7.3 语义一致（含 `platform-gateway` 执行占位、最小 `Role.USER`）；`Role.register()` 扩展点可用。**剩余部分**：`create_app()` 挂载 Platform Router 需要真实 `ServerConfig`（本地无 `ov.conf`，且不应使用生产配置），留正式开发首周用部署配置验证。**发现**：源码 `Role` 内置 rank 为 USER=0/ADMIN=1/ROOT=2，与平台 `iam_roles.rank`（3/2/1）是两套独立体系，不得混用（03 §8.3 的等级比较只用平台 rank）。
10. **MCP OAuth 数据迁 PostgreSQL**：设计 04 §10.13 要求 Client/Grant/Token 从工作目录 SQLite 迁出；源码 `oauth/storage.py` 是 SQLite。协议端点复用 `mcp.server.auth` SDK，替换存储层是正式开发的独立工作项。
11. **Provisioning outbox/worker**：Spike 只模拟了 `status=provisioning/failed` 与 retry 守卫，未实现 `iam_outbox` + Worker + OpenViking 控制面同步。

## 5. 目录结构

```text
spikes/platform-v0.1/
  backend/            # 独立 FastAPI 应用（可丢弃）
    models.py         # 8 张 IAM 表（ORM）
    permissions.py    # 权限目录 + 3 内置角色种子 + rank
    security.py       # Argon2id / token / SHA-256
    principals.py     # Session/API Key -> AuthenticatedUserPrincipal
    services/         # session_service / api_credential_service / user_service
    routers/          # auth / me / admin / platform
    bootstrap.py      # 建种子 + 首位 PSA（一次性）
  migrations/         # Alembic（async 模板，1 个初始迁移）
  scripts/            # reset_db.py（重建库）、verify.py（57 断言）
  web-platform/       # Vite + React 19 + TS + TanStack Router/Query 脚手架
```
