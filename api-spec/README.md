# CSVest - 前后端对接指南

> 面向:**后端开发同学**(数据工程 + LLM 工程师)
> 目的:让前端能在 D8 (7/20) 起顺利联调

## 📋 目录

1. [快速开始](#1-快速开始)
2. [数据契约](#2-数据契约)
3. [API 端点](#3-api-端点)
4. [WebSocket 实时](#4-websocket-实时)
5. [认证](#5-认证)
6. [错误处理](#6-错误处理)
7. [Mock 模式](#7-mock-模式)
8. [联调 Checklist](#8-联调-checklist)

---

## 1. 快速开始

### 1.1 文档清单

| 文件 | 用途 |
|------|------|
| `api-spec/openapi.yaml` | OpenAPI 3.0 规范(可导入 Postman / Swagger UI / 代码生成) |
| `api-spec/websocket.md` | WebSocket 实时通信协议 |
| `api-spec/README.md`(本文件) | 对接指南 |
| `js/api.js` | 前端 API 客户端(可作集成参考) |

### 1.2 后端技术栈(推荐)

| 层级 | 技术 | 说明 |
|------|------|------|
| 框架 | **FastAPI** | Python 3.10+,自动生成 OpenAPI |
| 数据获取 | httpx + BUFF Session | httpx 异步请求 |
| 实时推送 | **WebSocket** | FastAPI 原生支持 |
| 缓存 | Redis | 价格缓存 60s |
| 任务调度 | APScheduler / Celery | 定时拉取 + 预警检查 |
| 数据库 | PostgreSQL + TimescaleDB | 时序数据 |
| ORM | SQLAlchemy 2.0 | 异步 |

### 1.3 启动本地后端

```bash
# 1. 克隆前端项目
git clone https://github.com/caihuasheng627-ui/socProject.git

# 2. 后端同学另建 backend/ 目录
mkdir backend && cd backend

# 3. 安装依赖
pip install fastapi uvicorn httpx sqlalchemy redis apscheduler

# 4. 启动服务
uvicorn main:app --reload --port 8000

# 5. 测试连通
curl http://localhost:8000/api/health
# 期望: {"status": "ok", "dataSources": {...}, "models": {...}}
```

### 1.4 前端切换到真实后端

```javascript
// 浏览器控制台执行
localStorage.setItem('sv_api_url', 'http://localhost:8000');
localStorage.setItem('sv_use_mock', 'false');
location.reload();
```

切换后,所有 API 调用会真实打到后端。后端不可用时,自动 fallback 到 mock。

---

## 2. 数据契约

### 2.1 核心数据模型

| 模型 | 来源 | 备注 |
|------|------|------|
| `Skin` | BUFF + Skinport | 合并去重,价格以 BUFF 为主 |
| `KLinePoint` | BUFF `/api/market/goods/price_history` | USD 转 CNY |
| `NewsItem` | 爬虫 (Valve/Steam/HLTV/Reddit) | 每日 09:00 抓取 |
| `Alert` / `PortfolioItem` | 用户本地 | 当前用 localStorage,后期需后端 |
| `ModelResult` | 模型推理输出 | 来自 training pipeline |

### 2.2 数据时区

- **所有时间戳**: ISO 8601 + UTC(如 `2026-07-15T10:23:45Z`)
- **日期**: 单独字段用 `YYYY-MM-DD`
- **K 线日期**: 前端展示用 `M/D` 短格式,后端可返回 ISO 字符串

### 2.3 数值精度

| 字段 | 类型 | 精度 |
|------|------|------|
| `price` | float | 2 位小数 |
| `change*` | float | 2 位小数(%) |
| `volume24h` | int | 整数 |
| `confidence` | float | 整数 0-100 |

### 2.4 中文 vs 英文

**目前**:
- `category` 字段:中文(`步枪` / `狙击枪` / ...)
- `wear` 字段:英文缩写(`FT` / `MW` / `FN` / `BS` / `WW`)
- 错误信息:中文(面向用户)

**预留**:后期可加 `?lang=en` 查询参数。

---

## 3. API 端点

完整规范见 [`openapi.yaml`](./openapi.yaml)。这里列关键点:

### 3.1 必须实现的端点(联调阻塞)

| 优先级 | 端点 | 说明 |
|:------:|------|------|
| 🔴 P0 | `GET /api/health` | 联调第一关 |
| 🔴 P0 | `GET /api/skins` | 行情看板 |
| 🔴 P0 | `GET /api/skins/{id}/kline?days=90` | K 线图 |
| 🔴 P0 | `POST /api/predict` | AI 预测(模型推理) |
| 🔴 P0 | `POST /api/chat` | AI 对话(流式) |
| 🟡 P1 | `GET /api/news` | 资讯流 |
| 🟡 P1 | `POST /api/explain/{id}` | RAG 解释 |
| 🟢 P2 | `WS /ws` | WebSocket 实时推送 |
| 🟢 P2 | `POST /api/debate/{id}` | 双 Agent 辩论 |

### 3.2 Mock 数据参考

前端 mock 数据在 `js/data.js`,后端同学可以参考字段结构:
- `SKINS_POOL` - 20 个饰品
- `NEWS_FEED` - 6 条新闻
- `MODEL_COMPARISON` - 6 回归 + 4 分类
- `DEBATE_SAMPLE` - 双 Agent 辩论示例

### 3.3 CORS 配置

FastAPI 启动时:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # 本地前端
        "https://caihuasheng627-ui.github.io",  # GitHub Pages
        "https://c6baedd765c64ac0be36a263177a5469.app.codebuddy.work",  # CloudStudio
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 4. WebSocket 实时

详见 [`websocket.md`](./websocket.md)。

**关键事件**:
- `price.alerts` - 用户预警触发
- `market.tickers` - 全部饰品最新价(5/分钟)
- `chat.session.{id}` - AI 对话流式 chunk
- `debate.{skinId}` - 双 Agent 辩论进度

**前端使用示例**:

```javascript
import { CSVestAPI } from './js/api.js';

const ws = new CSVestWS('wss://api.csvest.local', token);
ws.connect();
ws.on('price.alerts', (msg) => {
  if (msg.event === 'alert.triggered') {
    showNotification(msg.data);
  }
});
```

(完整实现见 `js/api.js` 的 `CSVestWS` class)

---

## 5. 认证

### 5.1 当前状态

- ✅ 已实现:无认证(MVP 阶段)
- 🟡 计划中:简单 JWT 认证
- 🟢 未来:OAuth (GitHub / Steam)

### 5.2 JWT 流程(待后端实现)

```
1. 用户登录 → POST /api/auth/login { email, password }
2. 后端验证 → 返回 { accessToken, refreshToken, expiresIn }
3. 前端存储 accessToken 到 localStorage:
   localStorage.setItem('sv_token', accessToken)
4. 后续请求 Header 加:
   Authorization: Bearer <accessToken>
5. 401 响应时,自动 refresh
```

### 5.3 WebSocket 鉴权

通过 query 参数:
```
wss://api.csvest.local/ws?token=<jwt>
```

---

## 6. 错误处理

### 6.1 标准错误响应

```json
{
  "code": 400,
  "message": "请求参数错误",
  "details": "skinId 不能为空"
}
```

### 6.2 错误码约定

| HTTP | code | 含义 |
|------|------|------|
| 400 | `INVALID_PARAMS` | 参数错误 |
| 401 | `UNAUTHORIZED` | 未登录/token 失效 |
| 403 | `FORBIDDEN` | 权限不足 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 408 | `TIMEOUT` | 请求超时 |
| 429 | `RATE_LIMIT` | 频率限制 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |
| 503 | `SERVICE_UNAVAILABLE` | 依赖服务不可用(如 BUFF 挂了) |

### 6.3 前端错误处理

`js/api.js` 中的 `_safeCall` 会自动:
- 优先调用真实 API
- 失败时 fallback 到 mock
- 通过 `console.warn` 记录失败原因

前端 UI 会显示友好的 Toast 提示。

---

## 7. Mock 模式

### 7.1 默认状态

前端**默认**走 mock 数据(`sv_use_mock=true`),不依赖后端,演示和开发都不受影响。

### 7.2 切换方式

**方式 1:localStorage(推荐)**
```javascript
localStorage.setItem('sv_api_url', 'http://localhost:8000');
localStorage.setItem('sv_use_mock', 'false');
location.reload();
```

**方式 2:控制台运行时切换**
```javascript
CSVestAPI.setUseMock(false);
CSVestAPI.setBaseURL('http://localhost:8000');
```

### 7.3 混合模式(可调试)

如果想**只让特定接口走真实后端**,可以:

```javascript
// 在 app.js 启动时
CSVestAPI.setUseMock(true);  // 全局 mock

// 临时切换单个调用
const data = await fetch('http://localhost:8000/api/skins').then(r => r.json());
// 不经过 CSVestAPI,直接用原生 fetch
```

---

## 8. 联调 Checklist

### D7 (7/17 周五) - 数据+基础
- [ ] 后端:实现 `GET /api/skins` (返回 mock 数据即可)
- [ ] 后端:实现 `GET /api/health`
- [ ] 前端:确认 `localStorage.setItem('sv_api_url', 'http://localhost:8000')` 后能调通
- [ ] 联调:共享 CORS 配置,确认跨域 OK

### D8 (7/20 周一) - 基线模型
- [ ] 后端:实现 `GET /api/skins/{id}/kline?days=90`
- [ ] 后端:训练 ARIMA / XGBoost / RandomForest
- [ ] 前端:K 线图 + 价格卡片改为调用真实 API
- [ ] 联调:用真实 K 线数据替换 mock

### D9 (7/21 周二) - 高级模型
- [ ] 后端:实现 `POST /api/predict` (6 模型)
- [ ] 后端:训练 LSTM / GRU(慢,提前开始)
- [ ] 前端:AI 预测页改为调用 `/api/predict`
- [ ] 联调:模型推理延迟 < 2s

### D10 (7/22 周三) - 进展汇报
- [ ] 演示:CloudStudio 走真实后端(部署 staging 后端)
- [ ] 演示:展示行情 + K 线 + AI 预测 + 5 个模型
- [ ] 备用:GitHub Pages 走 mock(防止后端崩)

### D11 (7/23 周四) - RAG + 集成
- [ ] 后端:实现 `POST /api/chat` (流式 SSE)
- [ ] 后端:接入 DeepSeek API
- [ ] 后端:搭建 RAG 知识库(Qdrant / pgvector)
- [ ] 前端:AI 对话改为流式渲染

### D12 (7/24 周五) - Agent + 评估
- [ ] 后端:实现 `POST /api/debate/{id}` (流式)
- [ ] 后端:实现双 Agent 辩论逻辑
- [ ] 后端:实现回测引擎 + SHAP
- [ ] 前端:模型实验室对接

### D13 (7/27 周一) - 打磨
- [ ] 后端:WebSocket `/ws` 实时推送
- [ ] 前端:实时价格预警
- [ ] 联调:完整流程跑通

### D14 (7/28 周二) - 最终展示
- [ ] 后端:生产部署(阿里云 PAIDSW?)
- [ ] 前端:CloudStudio + GitHub Pages 双部署
- [ ] 备份:mock 模式作为兜底

---

## 9. 沟通约定

- **接口变更**:先更新 `openapi.yaml` 提交到 `api-spec/`,通知前端后端改
- **数据问题**:在 GitHub Issue 提,标签 `data` / `api`
- **联调时间**:每天 21:00 同步进度
- **紧急联系**:微信群(已建)

---

## 10. 版本

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.1 | 2026-07-15 | 初版,与前端 V1.1 对齐 |
