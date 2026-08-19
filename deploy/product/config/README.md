# OpenViking 产品化平台 staging 配置说明（14 号计划 §99.1 P5-E1）
#
# 三单元部署（reverse-proxy / openviking-product-server / postgresql）见
# 本目录 docker-compose.yml；nginx 路由与低层 deny 见 nginx/nginx.conf。
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
#
# ## 生产默认（本模板已固化）
#   platform_enabled=true、platform_adapter_mode=real（真实 OpenViking 接线）
#   low_level_routers_enabled=["admin"]（WebDAV/Snapshot/Pack/Debug/Observer/
#   Console 应用层不挂载；nginx deny 为第二道防线）
#   studio_enabled=false（公网无 /studio、无 /→/studio/ 重定向，06 §13.6）
#   cors_origins=同源（06 §14.4 优先同源部署）
#   OV_COOKIE_SECURE=1（Cookie Secure/HttpOnly/SameSite=Lax，03 §8.1）
#
# ## 数据卷
#   ./data 挂载 /app/.openviking（workspace/vectordb/agfs 持久化，沿用既有
#   VectorDB/模型/数据卷配置）；pgdata 卷持久化 PostgreSQL。
#
# ## 首次初始化（P5-E2 一次性初始化命令：建 PSA → Account → 首位 Admin；
#   本 Epic 不实现，见 14 号计划 §99.2）。
