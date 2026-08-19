# P5-E1 验收自检报告（生产部署单元、路由边界与 create_app 真实挂载验证）

- **Epic**：P5-E1（14 号计划 §99.1，Plane：urgent）
- **分支**：`feature/platform-v0.1-p5-e1`（基 = origin/dev `b01a39d5`）
- **日期**：2026-08-19
- **提交**：见文末 commit 列表；证据均为自动化测试，命令：
  `PYTHONPATH=. .venv/bin/python -m pytest tests/platform/ -p no:cacheprovider --no-cov`
  （需要 PG：`OV_PLATFORM_TEST_ADMIN_URL=postgresql://ov_platform:ov_platform_dev@127.0.0.1:55453/postgres`）

## 验收标准逐条自检

| # | 验收标准 | 结论 | 证据（测试用例/交付物） |
| --- | --- | --- | --- |
| ① | 公网路由表无 `/studio`、请求 `/studio/*` 404 且不重定向登录页；裸根 `/` 返回平台入口页 | ✅ | `tests/platform/test_mount_production.py::test_platform_mode_no_studio_route`、`test_platform_mode_studio_404_not_redirect`（/studio、/studio/、/studio/index.html、/studio/app 全部 404，/ 返回 web-platform 入口 200）；代理层无 /studio 路由见 `deploy/product/nginx/nginx.conf`（`location ~ ^/studio(/|$) { return 404; }`）与 Helm ingress server-snippet |
| ② | 同源下 SPA 深链返回 200 | ✅ | `test_platform_mode_spa_deep_links_200`（/app/*、/admin/*、/platform/* 深链与 /login、/oauth/consent、/oauth/verify 精确页 200；静态资源按真实文件返回） |
| ③ | 16.2 路由表逐条验证路径与处理器对应 | ✅ | `test_route_table_16_2`（/、/login、/app/*、/admin/*、/platform/*→web-platform；/oauth/consent、/oauth/verify→web-platform 授权页；/studio 不注册；/api/platform/v1/*→Platform Router；/api/v1/*→OpenViking；/mcp 注册）+ `test_oauth_routes_mounted_without_studio`（/authorize、/token、/register、/revoke、/.well-known/*、/oauth/authorize/page、/oauth/authorize/page/status→OpenViking） |
| ④ | create_app 真实配置完整启动、Platform Routers 注册、web-platform 静态可访问、既有路由回归无破坏；真实 bundle 冒烟 | ✅（真实 E2E 以集成测试模拟，环境无真实运行时的限制见说明） | `test_production_config_mount_full`（platform+real 配置完整启动、app.state 装配、真实适配器注入、web-platform 可访问）；`test_mount.py` 既有 4 用例回归全绿（/api/platform/v1 与 /api/v1、/mcp 并存）；真实 bundle 冒烟（登录→首页→检索→admin 一页）需 P5-E2 初始化命令与真实 MCP 客户端，本 Epic 以集成测试+配置断言覆盖（注明：环境不允许真实 E2E，见 test_oauth_without_studio.py docstring） |
| ⑤ | `/api/v1/admin/*` 与 trusted 公网不可达、低层运维入口应用层未挂载且代理层拒绝（两级断言） | ✅ | 应用层：`test_low_level_ops_not_mounted_in_production`（WebDAV/Snapshot/Pack/Debug/Observer/Console 未挂载，admin 保留）＋`test_create_app_platform_mode_unmounts_ops_routers`（既有）；代理层：`deploy/product/nginx/nginx.conf` deny 规则（/api/v1/admin、webdav、console/snapshot/pack/debug/observer、/studio、系统修复 → 404）+ Helm ingress server-snippet；trusted 不暴露公网：平台模式低层认证为 `platform_iam`（PG IAM，`auth_mode=platform_iam`，见 deploy/product/config/ov.conf.template），trusted 模式仅受控内部网关使用 |
| ⑥ | 镜像/前端环境变量/仓库无 Root Key/数据库口令/签名密钥明文 | ✅ | `test_no_secret_plaintext_in_repo`（扫描 deploy/ 的 yml/yaml/json/conf/template：口令类字段必须为空或 ${ENV} 引用）；staging 模板 `ov.conf.template` 口令全为 `${...}` 占位；compose/Helm 经环境变量与 Secret 注入（`deploy/product/docker-compose.yml`、Helm `platform.secretName`）；镜像无内置口令（Dockerfile.product） |
| ⑦ | 无 Studio 时同设备与跨设备 MCP OAuth 完整走通、浏览器无 User API Key | ✅（集成测试+配置断言，真实 OAuth E2E 需 MCP 客户端，留 P5-E4 生产复验） | `test_oauth_routes_mounted_without_studio`（无 /studio 时协议端点+授权页完整挂载、PG 存储）、`test_oauth_pg_store_in_platform_mode`（PostgresOAuthStore，非 SQLite）、`test_consent_verify_pages_from_web_platform`（/oauth/consent、/oauth/verify 由 web-platform 提供）、`test_browser_no_user_api_key_surface`（授权页无 User API Key 输入） |

## 真实适配器接线清单

| 适配器（原 Fake） | 真实实现 | 状态 | 说明 |
| --- | --- | --- | --- |
| FakeControlPlane | `adapters/control_plane.py::RealControlPlaneAdapter` | ✅ 已接线 | namespace 初始化（`initialize_account_directories`/`initialize_user_directories`，幂等）；`list_provisioned_accounts/users` 为存在性检查最佳努力（v0.4.12 无账号枚举接口），对账漂移由 Provisioning 重放兜底 |
| FakeSkillConfigsAdapter | `adapters/skill_configs.py::RealSkillConfigsAdapter` | ✅ 已接线 | `UserPrivacyConfigService`（category=`skills`），脱敏快照语义对齐；账号经 `get_account_id_for_ov_user`（新增 IamRepository 方法）反查 |
| FakeSearchEngine | `adapters/search.py::RealSearchEngine` | ✅ 已接线（需运行时） | `SearchService.find/search` → RawHit（内部字段 DTO 层脱敏）；检索根由产品服务端固定 |
| FakeSessionBackend | `adapters/session_backend.py::RealSessionBackend` | ✅ 已接线 | `SessionService` create/append/commit/messages/meta/delete；Commit Phase 2 状态以 pending 兜底（Memory Impact 由产品 DTO 从 Session Commit 记录组装），真实 Archive 合并语义由 Session 加载提供 |
| FakeSkillContentAdapter | `adapters/skills.py::RealSkillContentAdapter` | ⚠️ 部分接线 | **私有根已接线**（VikingFS 写/读/删）；**共享根（viking://agent/skills）未接线**：v0.4.12 文件层按 account 隔离，内容写入协议不携带 Actor Account，无法确定共享根物理归属 → 显式报错避免写错位置（供 P5-E4 门禁评审） |
| FakeSkillMigrationAdapter | `adapters/skills.py::RealSkillMigrationAdapter` | ✅ 已接线 | `VikingFS.mv`（整目录复制+向量重写+失败回滚+孤儿清理幂等语义）；clean_owner_user_id 由 mv 向量重写覆盖（v0.4.12 无单条向量字段变更 API，空操作并记录） |
| FakeResourceExecutionPlane | — | ❌ 未接线 | 真实摄取管线（上传/网页/Git → ResourceProcessor）依赖完整运行时（VectorDB/Embedder/AGFS 抓取）与任务编排，产品脱敏语义与底层 Task 解耦，保留 Fake（供 P5-E4 评审） |
| NoopPurgeHandler / ResourcePurgeHandler | `adapters/purge.py::RealResourcePurgeHandler` | ✅ 已接线 | 期满清理经 `VikingFS.rm`（文件+向量索引一体，幂等），URI 取 `IamDeletionJob.ov_uri` |

接线配置开关：`ServerConfig.platform_adapter_mode`（`fake` 默认——保持 Phase 1–4 开发/测试行为；`real`——真实接线）。staging/生产模板（`deploy/product/config/ov.conf.template`、Helm `platform.adapterMode`）显式设 `real`。

## 部署配置交付物

- `deploy/product/docker-compose.yml`：三单元部署（reverse-proxy nginx / openviking-product-server / postgresql 16），口令只经环境变量注入（`:?` 强制）
- `deploy/product/nginx/nginx.conf`：16.2 路由表落地 + 低层入口 deny（第二道防线）+ HTTPS 骨架
- `deploy/product/config/ov.conf.template`：staging 生产配置模板（platform_enabled=true、platform_adapter_mode=real、low_level_routers_enabled=["admin"]、studio_enabled=false、CORS 同源、auth_mode=platform_iam）
- `deploy/product/Dockerfile.product`：产品化镜像入口
- `deploy/helm/openviking`：`platform.*` values（ov.conf 注入平台配置、Secret 注入口令、ingress server-snippet 低层 deny）、`postgresql` 依赖说明
- `Dockerfile` / `setup.py` / `pyproject.toml`：web-platform SPA 构建并入镜像与 Python 包（`web_platform/dist`，`OV_SKIP_PLATFORM_BUILD` 可关）
- 数据卷：沿用既有 VectorDB/模型/数据卷配置（`/app/.openviking`，workspace/vectordb/agfs）

## 对既有文件的改动说明

- `openviking/server/app.py`：低层 router 无条件 include → 白名单条件挂载（新增 `LOW_LEVEL_ROUTER_NAMES`/`_resolve_low_level_routers_enabled`/`_resolve_studio_enabled` 辅助函数；admin 由常挂组移入白名单组，非平台缺省白名单=全量，行为零变化）；Studio 挂载增加 `studio_enabled` 开关。平台模式既有语义（ops 不挂载）由缺省白名单 ["admin"] 保持。
- `openviking/server/config.py`：ServerConfig 新增 `low_level_routers_enabled`、`studio_enabled`、`platform_adapter_mode`（extra=forbid 需显式建模）。
- `openviking/server/platform/mount.py`：生产挂载（web-platform SPA、适配器模式选择、real Purge 处理器）；`_mount_resource_services` 增加 config 参数。
- `openviking/server/platform/iam/repository.py` / `postgres_repository.py`：新增 `get_account_id_for_ov_user`（真实适配器 ctx 构建）。
- `setup.py` / `pyproject.toml`：web-platform 构建与 package-data（镜像/包分发）。
- `Dockerfile` / `docker-compose.yml` / `deploy/helm/openviking/*`：web-platform 构建、PG 配置、低层 deny。

## 遗留项（P0/P1/P2）

- **P1（未接线，供 P5-E4 门禁评审）**：SkillContentAdapter 共享根写入——需要产品服务层为内容写入传递 Actor Account 上下文（协议扩展）后接线；ResourceExecutionPlane 真实摄取——需要完整运行时环境与摄取编排确认。
- **P2**：RealSessionBackend Commit Phase 2 状态（get_commit_status 以 pending 兜底，Memory Diff 组装随产品 DTO）；RealControlPlaneAdapter 账号枚举（list_* 存在性检查最佳努力）。
- **P2（环境限制）**：真实 OAuth E2E 与真实 bundle 冒烟需 P5-E2 初始化命令 + MCP 客户端 + 生产运行环境，本 Epic 以集成测试+配置断言覆盖；`tests/server` 中依赖原生 RAGFS 构建/本机 ov.conf 的用例在本环境不可运行（基线如此，非本 Epic 引入）。

## Commit 列表

```
8f8cf492 feat(platform): P5-E1 低层 router 条件挂载开关 + Studio 启用开关（ServerConfig/app.py，06 §16.2/§16.3，AC①⑤）
0b995337 feat(platform): P5-E1 真实适配器接线 + web-platform SPA 生产挂载（14 号计划 §99.1，Spike 风险 9 闭合，AC③④）
b7671295 feat(platform): P5-E1 三单元部署配置（web-platform 构建 + PG + 低层 deny 第二道防线，06 §16.1–16.3，AC⑤⑥）
<末次提交>  feat(platform): P5-E1 验收⑦集成断言与自检报告（test_oauth_without_studio.py，AC⑦）
```

## 测试统计（2026-08-19，PG ovp-pg16-p5e1:55453）

- `tests/platform/`：**529 passed**（基线 492 + 新增 37：test_mount_production 16 项、test_real_adapters 17 项、test_oauth_without_studio 4 项）
- `tests/server/`：受环境限制（原生 RAGFS 构建/ov.conf）不可运行的用例为基线既有情况；无本 Epic 引入的回归
- `ruff check`：全绿
