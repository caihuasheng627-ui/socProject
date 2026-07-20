# CSVest — CS2 饰品 AI 智能分析平台

> **课程:** SWS3022 — AI/ML for Financial Services  
> **对外品牌:** **CSVest**（历史名 SkinVision AI / SkinVest）  
> **版本:** V1.2 · 2026-07-20（预测规范 v4 · 分支 `post-delivery/forecast-contract-v4`）  
> **仓库:** https://github.com/caihuasheng627-ui/socproject

全栈实现：前端看板 + FastAPI 后端 + Hybrid LSTM / 树模型预测 + DeepSeek RAG / 双 Agent 辩论 + Docker 一键部署。

ML 侧最新预测契约与公平评测见 [`ml/FORECAST_CONTRACT.md`](ml/FORECAST_CONTRACT.md) · [`ml/README.md`](ml/README.md)。

---

## 在线访问

| 入口 | 地址 |
|------|------|
| GitHub | https://github.com/caihuasheng627-ui/socproject |
| GitHub Pages | https://caihuasheng627-ui.github.io/socProject |
> Pages / 静态演示默认走 Mock。完整预测、对话、辩论、持仓诊断需启动后端（见下方部署）。

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | Vue 3 + Tailwind + ECharts（CDN） | 无构建步骤，中英 i18n |
| API | FastAPI + Uvicorn + SQLite | OpenAPI v1.2.0，约 15+ 端点 |
| LLM | DeepSeek-V3 | Chat SSE / RAG / 辩论 / 组合诊断 |
| ML | LSTM-C/D Hybrid + GRU + ARIMA/XGB/LGBM/RF | 训练产物在 `ml/`，推理在 `backend/model_loader.py` |
| 部署 | Docker Compose | API `:8000` + Nginx 前端 `:8080` |

---

## 项目结构

```
socproject/
├── index.html / app.js / style.css / data.js / i18n.js   # 前端入口（品牌 CSVest）
├── js/api.js                                            # API 客户端（Mock 可回退）
├── api-spec/openapi.yaml                                # 前后端契约 v1.2.0
├── backend/                                             # FastAPI 后端（组员 3）
│   ├── main.py / database.py / model_loader.py
│   ├── llm.py / rag.py / agent_debate.py
│   ├── portfolio_diagnose.py / scheduler.py
│   └── .env.example
├── ml/                                                  # 训练 / 模型 / 预测 CSV（组员 1+2）
│   ├── forecast_contract.py / models/ / preds/ / tests/
│   └── FORECAST_CONTRACT.md
├── docs/expo/                                           # Expo 种子（预录辩论 / 日报 / 持仓）
├── docs/post-delivery/                                  # 预测规范 v4 维护文档
├── Dockerfile                                           # 后端镜像
├── docker-compose.yml                                   # 一键起 API + Web
└── README.md
```

---

## 功能概览

0. **品牌首屏** — 平台亮点介绍 +「进入分析面板」入口  
1. **行情看板** — 饰品列表、分类筛选、涨跌与流动性  
2. **物品详情 + AI 预测** — K 线 / MA、多模型 7 日预测、共识度、入场区间、RAG 解释、🐂🐻 双 Agent 辩论  
3. **AI 对话** — DeepSeek 流式 SSE（无 Key 时 Mock）  
4. **市场日报 / 资讯** — 指标卡片 + RAG 资讯流  
5. **价格预警** — 涨破 / 跌破 CRUD  
6. **模拟持仓** — real/sim 持仓、`value_history` 市值曲线、`diagnose` 组合诊断  
7. **模型实验室** — 对比表 / 回测曲线 / SHAP  

**ML 部署口径（Hybrid）：** low → LSTM-C；mid/high → LSTM-D（val 冻结，见 `ml/models/lstm_hybrid_route.json`）；公平 test 指标见 `ml/FORECAST_CONTRACT.md`。

---

## 部署方式（推荐：Docker 一键起）

### 前置

- Docker Desktop / Docker Engine + Compose 插件  
- （可选）DeepSeek API Key；无 Key 时 Chat / 现场辩论 / RAG 汇总自动降级  

### 步骤

```bash
git clone https://github.com/caihuasheng627-ui/socproject.git
cd socproject

# 环境变量（可先空着跑通；填 Key 后启用真实 LLM）
cp backend/.env.example backend/.env
# 编辑 backend/.env，按需填写 DEEPSEEK_API_KEY=sk-...

docker compose up --build -d
```

| 服务 | 地址 | 说明 |
|------|------|------|
| API | http://localhost:8000 | FastAPI；文档 http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/api/health | 数据源 + 模型状态 |
| 前端 | http://localhost:8080 | Nginx 静态托管 |

前端切到真实后端（浏览器控制台执行一次）：

```js
localStorage.setItem('sv_api_url', 'http://localhost:8000');
localStorage.setItem('sv_use_mock', 'false');
location.reload();
```

停止 / 查看日志：

```bash
docker compose logs -f api
docker compose down
```

SQLite（持仓 / 预警 / 预测缓存）挂在 Docker volume `sqlite-data`，重启不丢。

### 仅起后端镜像

```bash
cp backend/.env.example backend/.env
docker build -t skinvision-api .
docker run --rm -p 8000:8000 --env-file backend/.env \
  -v skinvision-db:/app/backend/data skinvision-api
```

---

## 本地开发（不用 Docker）

### 后端

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
cp .env.example .env   # 按需填 DEEPSEEK_API_KEY

python main.py
# 或: uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

启动时会：建表 → 从 `ml/data/{train,val,test}.csv` 导入饰品与行情 → 写入 Expo 种子 → 加载 LSTM/GRU（无 TensorFlow 则趋势外推降级）→ 启动定时任务。

### 前端

```bash
# 仓库根目录
python -m http.server 8080
# 访问 http://localhost:8080 ，按上文 localStorage 切 API
```

也可直接双击 `index.html`（部分浏览器对跨域 / 模块有限制，建议本地 server）。

### 联调顺序

`health` → `skins` → `kline` → `predict` → `chat` → `portfolio` → `news` → `debate`

---

## 主要 API（节选）

契约详见 [`api-spec/openapi.yaml`](api-spec/openapi.yaml)。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康 / 模型状态 |
| GET | `/api/skins` | 饰品列表 |
| GET | `/api/skins/{id}/kline` | K 线 + MA7/MA30 |
| POST | `/api/predict` | 多模型预测（默认 Hybrid LSTM + 树模型 CSV） |
| POST | `/api/chat` | DeepSeek SSE |
| POST | `/api/debate/{id}?live=0` | 预录回放；`live=1` 现场 DeepSeek |
| GET/POST/DELETE | `/api/portfolio` | 持仓 CRUD（`holdingType`: real/sim） |
| GET | `/api/portfolio/value_history` | 总市值曲线 |
| POST | `/api/portfolio/diagnose` | 组合诊断 |
| GET | `/api/models/comparison` · `/backtest` · `/shap` | 模型实验室 |
| GET/POST/DELETE | `/api/alerts` | 价格预警 |

请求体字段为 camelCase（如 `skinId`、`horizon`），与 OpenAPI / 前端一致。

---

## 降级口径

| 条件 | 行为 |
|------|------|
| 无 `DEEPSEEK_API_KEY` | Chat / 辩论现场 / RAG·诊断汇总 → Mock 或预录 |
| 无 TensorFlow / 模型文件 | Hybrid → 近 7 日趋势外推 |
| 树模型包缺失 | 读 `ml/preds/pred_*.csv` |
| BUFF 实时爬虫默认关 | 使用库内历史价 + 训练 CSV |

---

## 团队协作（Git）

```bash
git clone https://github.com/caihuasheng627-ui/socproject.git
cd socproject
git pull origin main
# 修改 → 提交 → 推送（需 Collaborator Write，或 Fork + PR）
git add .
git commit -m "描述修改"
git push origin main
```

Push 使用 GitHub Personal Access Token（勾选 `repo`），不是账户密码。

---

## 评分对齐（摘要）

| 维度 | 实现 |
|------|------|
| 仪表板 | 7 大模块 + 深色 CS2 UI + 移动端适配 |
| 金融相关 | 持仓风险、回测、组合诊断 |
| 数据 / 特征 | 154 件面板数据 + K 线 + 特征工程 |
| 模型可视化 | 对比表 / 雷达 / SHAP / 回测曲线 |
| 评估 + 基准 | 多模型指标 + Buy & Hold |
| 演示 | Docker 一键起 + Expo 预录辩论 |

---

## 后续优化

- [x] FastAPI 后端 + OpenAPI 联调
- [x] Hybrid LSTM 推理 + 树模型 pred CSV
- [x] Docker Compose 一键部署
- [x] 移动端适配 / 中英 i18n
- [ ] 接入真实 BUFF / Skinport 实时行情
- [ ] 用户系统（登录 / 收藏）
- [ ] 分类模型输出接入 `/api/predict`
- [ ] 真·30 日预测（当前 30 日由 7 日外推）

---

## 许可

仅供 SWS3022 课程项目使用 · © 2026 CSVest Team
