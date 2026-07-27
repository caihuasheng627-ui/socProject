# 全量 SQLite 种子库（云端 Docker 首次启动灌入 volume）
#
# - `skinvision.db`：本地 BUFF+CSV 灌好的库（约 681 件有 `price_history`，含 Steam `image_url`）
# - 运行时库仍在 `backend/data/`（gitignore，Docker volume `sqlite-data`）
# - 服务器若已是旧库（volume 会挡住新 seed），需重置：
#     bash scripts/update-deploy.sh --reset-seed
#   或：
#     RESET_DB_FROM_SEED=1 docker compose up -d --build --force-recreate api
# - `update-deploy.sh` 在检测到 `backend/seed/skinvision.db` 变更时会自动带 RESET_DB_FROM_SEED=1
# 更新种子（本机有新库时）：
#   cp backend/data/skinvision.db backend/seed/skinvision.db
# 仅同步饰品图 URL：
#   py backend/_sync_seed_images.py
# 运行库缺图时会读 seed/skin_image_urls.json 幂等回填（不必整库 RESET）
