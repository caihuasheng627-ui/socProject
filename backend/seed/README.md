# 全量 SQLite 种子库（云端 Docker 首次启动灌入 volume）
#
# - `skinvision.db`：本地 BUFF+CSV 灌好的库（约 681 件有 `price_history`，含 Steam `image_url`）
# - 运行时库仍在 `backend/data/`（gitignore，Docker volume `sqlite-data`）
# - 服务器若已是旧库（volume 会挡住新 seed），需重置：
#     bash scripts/update-deploy.sh --reset-seed
#   或：
#     RESET_DB_FROM_SEED=1 docker compose up -d --build --force-recreate api
# - `update-deploy.sh` 在检测到 `backend/seed/skinvision.db` 变更时会自动带 RESET_DB_FROM_SEED=1
# - 刷新近期价后若短序列目录件回潮，可清理：
#     py backend/prune_short_history.py --db backend/seed/skinvision.db
#   `refresh_recent_days.py` 只会给已有 ≥61 天历史的件补窗，避免再次写入空目录。
# - 增量采集中断导致最新日不一致时，可用前向持价对齐到统一截止日期：
#     py backend/fill_gaps.py --db backend/seed/skinvision.db --extend-to 2026-07-28
#   有 BUFF_COOKIE 时优先重跑 `scrape_incremental.py` 拉真实价，再用 fill_gaps 收尾。
# 更新种子（本机有新库时）：
#   cp backend/data/skinvision.db backend/seed/skinvision.db
# 仅同步饰品图 URL：
#   py backend/_sync_seed_images.py
# 运行库缺图时会读 seed/skin_image_urls.json 幂等回填（不必整库 RESET）
