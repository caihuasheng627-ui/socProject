# 全量 SQLite 种子库（云端 Docker 首次启动灌入 volume）
#
# - `skinvision.db`：本地 BUFF+CSV 灌好的库（约 681 件有 `price_history`）
# - 运行时库仍在 `backend/data/`（gitignore，Docker volume `sqlite-data`）
# - 服务器若已是旧的 154 件库，需重置 volume 或设环境变量：
#     RESET_DB_FROM_SEED=1 docker compose up -d --force-recreate api
#
# 更新种子（本机有新库时）：
#   cp backend/data/skinvision.db backend/seed/skinvision.db
