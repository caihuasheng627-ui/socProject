# SkinVision AI 后端(组员 3)

FastAPI + SQLite + DeepSeek + RAG + 双 Agent 辩论 + 组合诊断 + 定时任务。

## 快速启动

```bash
cd backend
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
cp .env.example .env          # 填入 DEEPSEEK_API_KEY(可选,无 Key 走 Mock)
python main.py                # 或:uvicorn main:app --reload --port 8000
```

启动时会自动:
1. 建表(7 表 + portfolio 转正含 holding_type)
2. 从 `ml/data/{train,val,test}.csv` 导入 154 件 CSV skins + 从 `docs/catalog_800_buff_target.csv` 导入 769 件 BUFF 目录；共 923 件 skins + 10.7 万行 price_history
3. 写入 6 件 Expo 种子持仓 + 4 条资讯 + 8 模型 registry
4. 加载 LSTM-C/D×3/GRU + scaler(TensorFlow 缺失则 Hybrid 降级趋势外推)
5. 开 APScheduler(RSS 6h / 日报 09:00 / 增量训练默认禁用)

健康检查:`GET http://localhost:8000/api/health`

## 前端联调

前端 `js/api.js` 默认 Mock。切到后端:

```js
localStorage.setItem('sv_api_url', 'http://localhost:8000');
localStorage.setItem('sv_use_mock', 'false');
location.reload();
```

联调顺序(策划书):health → skins → kline → predict → chat → **portfolio** → news → debate。

## 端点清单

| 优先级 | 端点 | 说明 |
|:--:|------|------|
| P0 | `GET /api/health` | 数据源 + 模型状态 |
| P0 | `GET /api/skins` | 列表(category/sort/limit) |
| P0 | `GET /api/skins/{id}/kline` | K 线 + MA7/MA30 + 成交量 |
| P0 | `POST /api/predict` | 6 模型预测(走 predictions 缓存) |
| P0 | `POST /api/chat` | DeepSeek 流式 SSE |
| P0 | `GET/POST/DELETE /api/portfolio` | 持仓 CRUD(real/sim) |
| 🆕 P1 | `GET /api/portfolio/value_history` | 总市值曲线(SQL 聚合) |
| 🆕 P1 | `POST /api/portfolio/diagnose` | 组合诊断三块输出 |
| P1 | `GET /api/news` · `GET /api/explain/{id}` · `GET /api/daily-report` | 资讯/RAG/日报 |
| P2 | `POST /api/debate/{id}?live=0` | 双模式:预录回放(默认)/ 现场重跑 |
| — | `GET /api/models/{comparison,backtest,shap}` | 模型实验室 |
| — | `GET/POST/DELETE /api/alerts` | 价格预警 |

## Expo 种子数据

```bash
python seed_data.py            # 生成预录辩论/日报/持仓快照到 docs/expo/
python seed_data.py --live     # 有 DeepSeek Key 时现场生成辩论预录
```

## Docker 一键起（推荐）

仓库根目录：

```bash
cp backend/.env.example backend/.env   # 可选填 DEEPSEEK_API_KEY
docker compose up --build -d           # API:8000  Web:8080
# 健康检查: curl http://localhost:8000/api/health
```

SQLite 落在 `backend/data/skinvision.db`，Compose 挂载 volume `sqlite-data` 持久化。
详见根目录 `README.md`「部署方式」。

## 降级口径(策划书 §13.2)

- BUFF 实时爬虫默认关 → 用已落库历史价 + 训练 CSV 兜底
- 无 DeepSeek Key → chat/辩论/RAG/诊断汇总走 Mock 或预录回放
- 无 TensorFlow → Hybrid LSTM 降级为近 7 日趋势外推
- 无 xgboost → XGBoost 端点走 `ml/preds/pred_xgboost.csv`

## 文件结构

```
backend/
├── main.py                 FastAPI 全端点
├── database.py             SQLite 建表 + 导入 + 种子
├── model_loader.py         Hybrid LSTM-C/D + GRU + 树(预录 CSV)
├── llm.py                  DeepSeek 同步/SSE 流式 + Mock
├── rag.py                  DashScope 向量 RAG + 关键词降级 + LLM 解释
├── agent_debate.py         双 Agent 辩论(预录/现场双模式)
├── portfolio_diagnose.py   组合诊断三块输出
├── scheduler.py            APScheduler(RSS/日报/增量训练)
├── seed_data.py            Expo 种子生成
├── config.py               路径/Key/开关
├── requirements.txt
└── .env.example
```
