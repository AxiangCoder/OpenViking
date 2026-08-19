# OpenViking 产品化平台 staging 配置说明（14 号计划 §99.1 P5-E1 / §99.2 P5-E2）
#
# 三单元部署（reverse-proxy / openviking-product-server / postgresql）见
# 本目录 docker-compose.yml；nginx 路由与低层 deny 见 nginx/nginx.conf；
# PG 加密备份/恢复与恢复审计见 scripts/backup-encrypt.sh（06 §14.4，演练归
# P5-E3 §99.3）。
#
# ## ov.conf 注入
# 容器入口（docker/openviking-entrypoint.sh）支持 OPENVIKING_CONF_CONTENT 全量
# 注入；或把本模板 envsubst 后挂载为 /app/.openviking/ov.conf：
#
#   envsubst < deploy/product/config/ov.conf.template > /tmp/ov.conf
#   docker compose -f deploy/product/docker-compose.yml up -d --build
#
# ## 必需环境变量（口令/密钥只经环境注入，不写镜像/仓库，06 §14.4）
#   OPENVIKING_EMBEDDING_API_KEY   模型网关密钥（embedding）
#   OPENVIKING_VLM_API_KEY         模型网关密钥（VLM）
#   OV_PLATFORM_DATABASE_URL       PostgreSQL DSN（含口令）
#   OV_PLATFORM_DB_PASSWORD        PostgreSQL 口令（compose 侧）
#   OV_NODE_ID_SECRET              资源 Node ID HMAC 密钥（09 §42.3）
#   OV_SOURCE_CIPHER_KEY           远程来源加密密钥（09 §47.3，≥32 字节）
#   OPENVIKING_PUBLIC_BASE_URL     公网基址（OAuth issuer / MCP 上传指令）
#   OV_PLATFORM_INIT_PSA_*         首次初始化输入（`ov platform init`，见下）
#
# ## 生产默认（本模板已固化）
#   platform_enabled=true、platform_adapter_mode=real（真实 OpenViking 接线）
#   low_level_routers_enabled=["admin"]（WebDAV/Snapshot/Pack/Debug/Observer/
#   Console 应用层不挂载；nginx deny 为第二道防线）
#   studio_enabled=false（公网无 /studio、无 /→/studio/ 重定向，06 §13.6）
#   cors_origins=同源（06 §14.4 优先同源部署）
#   OV_COOKIE_SECURE=1（Cookie Secure/HttpOnly/SameSite=Lax，03 §8.1）
#
# ## 首次初始化（P5-E2，06 §15.2 八步顺序，runbook 见
#   docs/design/product-platform/v0.1/p5-e2-init-runbook.md）
#   1) 启动三单元后执行一次：
#        OV_PLATFORM_INIT_PSA_EMAIL=psa@example.com \
#         OV_PLATFORM_INIT_PSA_USERNAME=psa \
#         OV_PLATFORM_INIT_PSA_PASSWORD='<Secret Manager 注入>' \
#         docker compose exec openviking-product-server \
#           python -m openviking.server.platform.bootstrap_cli init
#      （重复执行幂等拒绝；缺省 OV_PLATFORM_INIT_PSA_PASSWORD 时密码仅展示一次）
#   2) `ov platform status` 巡检；`ov platform verify` 完成步骤 8 三凭证验证。
#
# ## 健康检查阈值（06 §16.3，14 号计划 §99.2 /ready 判定）
#   OV_HEALTH_PROVISIONING_BACKLOG_THRESHOLD   backlog > N 非 ready（默认 100）
#   OV_HEALTH_MIGRATION_LAG_VERSIONS           migration 落后 >= N 非 ready（默认 1）
#   OV_HEALTH_PURGE_STALL_MAX_PENDING          待清理 > N 非 ready（默认 500）
#   OV_HEALTH_PURGE_STALL_MAX_DAYS             最早 purge_after 落后 > N 天
#                                               非 ready（默认 3）
#   /health、/ready 在平台模式下返回 platform 检查明细；非 ready 时为 503 +
#   可读诊断（06 §17.3）。
#
# ## 数据卷
#   ./data 挂载 /app/.openviking（workspace/vectordb/agfs 持久化，沿用既有
#   VectorDB/模型/数据卷配置）；pgdata 卷持久化 PostgreSQL。
