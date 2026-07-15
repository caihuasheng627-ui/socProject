# SkinVision AI — CS2 饰品 AI 智能分析平台

> **课程:** SWS3022 — AI/ML for Financial Services
> **前端角色:** 蔡华升 (前端开发)
> **版本:** V1.1 · 2026-07-15

融合 AI 预测模型、RAG 知识库和 AI Agent 的 CS2 饰品市场智能分析平台前端实现。

---

## 🚀 在线访问

- **CloudStudio 演示:** 
- **GitHub:** https://github.com/caihuasheng627-ui/socProject
- **GitHub Pages:** https://caihuasheng627-ui.github.io/socProject/ (启用 Pages 后)

> 在线版本使用模拟数据(MVP),完整功能需配合 FastAPI 后端 + 真实 BUFF/Skinport API。

---

## 📦 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 框架 | Vue 3 (CDN) | Composition API + 响应式数据 |
| 样式 | Tailwind CSS (CDN) + 自定义 CSS | 深色 CS2 风格主题 |
| 图表 | ECharts 5.4 | K线图、雷达图、回测曲线、SHAP |
| Markdown | marked.js | AI 回复内容渲染 |
| 部署 | CloudStudio | 静态资源一键部署 |

**无构建步骤:** 所有资源通过 CDN 加载,双击 `index.html` 即可在本地运行。

---

## 📁 项目结构

```
skinvision-ai/
├── index.html            # 入口 HTML (含全部页面模板)
├── css/
│   └── style.css         # 深色主题 + 自定义组件样式
├── js/
│   ├── data.js           # Mock 数据层 (20饰品 + 新闻 + 模型)
│   └── app.js            # Vue 主应用 + 图表渲染
└── README.md             # 本文档
```

---

## 🎯 功能模块

按策划书 4. 节功能清单实现:

### 1️⃣ 📊 行情看板
- 20 个高流动性 CS2 饰品(来自策划书 3.4)
- 7 日涨跌榜 Top 5
- 分类筛选(步枪/狙击枪/手枪/刀具/手套/箱子)
- 实时价格、涨跌幅、成交量、流动性指标

### 2️⃣ 🔍 物品详情 + AI 预测 ⭐ 核心
- **K线图**: ECharts 实现,90/180 天历史 + 30 天 AI 预测虚线
- **技术指标**: MA7 / MA30 叠加,Volume 副图
- **多模型预测**: 6 个回归模型对比 (ARIMA/XGBoost/LightGBM/RF/LSTM/GRU)
- **共识度评分**: 76% 横向条
- **入场区间**: 动态计算 + 目标价 + 止损位
- **RAG 解释**: 关联新闻/赛事/公告,4 条相关资讯卡片
- **🐂🐻 双 Agent 辩论**: 3 轮迭代(独立分析→互相质疑→达成共识)

### 3️⃣ 💬 AI 对话顾问 ⭐ 亮点
- 自然语言对话界面
- 6 个推荐问题快捷入口
- 流式输出 + Loading 动画
- 智能回复:分析饰品 / 推荐组合 / 解读新闻
- 支持 Markdown 渲染(加粗/列表/emoji)

### 4️⃣ 📰 AI 市场日报
- 关键指标卡片(监控数/上涨/下跌)
- 成交量 Top 5
- AI 市场总结(DeepSeek 风格)
- RAG 资讯流(6 条新闻 + 情感分析)

### 5️⃣ 🔔 价格预警
- 3 个关键指标(活跃/今日触发/本周触发)
- 预警列表(状态徽章可视化)
- 新建预警弹窗(饰品/条件/目标价/备注)
- 支持涨破/跌破两种条件

### 6️⃣ 📋 模拟持仓
- 6 个风险指标(总成本/市值/盈亏/Sharpe/最大回撤/波动率)
- 持仓明细表(盈亏、收益率自动计算)
- 添加持仓弹窗(自动填充当前价)
- 一键平仓

### 7️⃣ 🤖 模型实验室
- **回归模型对比表**: 6 模型 × 7 指标(对应策划书 5.4)
- **分类模型对比表**: 4 模型(对应 5.2)
- **雷达图**: 6 维度多模型对比
- **回测曲线**: 60 天 5 模型收益对比 + 买入持有基准
- **SHAP 特征重要性**: 8 个核心特征(对应 5.3)
- **训练策略说明**: TimeSeriesSplit / Walk-Forward / SMOTE 等

---

## 🎨 设计亮点

- **CS2 主题色**: 橙色 (#ff6b00) 品牌色 + 深色背景
- **中国股市配色**: 涨红跌绿 (与策划书一致)
- **响应式布局**: 4 栏 → 3 栏 → 2 栏自适应
- **微动画**: 状态点脉冲 / 消息淡入 / Loading 打字效果
- **数据可视化**: K线红涨绿跌符合中国习惯

---

## 🚀 本地运行

```bash
# 方式 1: 直接打开
双击 index.html

# 方式 2: 启动本地服务器 (推荐,避免某些资源跨域问题)
cd skinvision-ai
python -m http.server 3000
# 访问 http://localhost:3000

# 方式 3: Node 服务器
npx serve .
```

---

## 🔌 与后端对接

当前前端使用 Mock 数据。生产环境需对接:

| 前端调用 | 后端 API (FastAPI) |
|----------|---------------------|
| 行情数据 | `GET /api/skins?category={cat}` |
| K线数据 | `GET /api/skins/{id}/kline?days={n}` |
| AI 预测 | `POST /api/predict` body: `{skin_id, horizon}` |
| RAG 解释 | `GET /api/explain/{skin_id}` |
| AI 对话 | `POST /api/chat` (流式 SSE) |
| 双 Agent 辩论 | `POST /api/debate/{skin_id}` |
| 新闻 | `GET /api/news?limit={n}` |
| 预警/持仓 | `GET/POST/DELETE /api/alerts` `/api/portfolio` |

数据源(策划书 3.1):
- **BUFF API** (主力,Session Cookie)
- **Skinport API** (辅助,免费)
- **Kaggle CS:GO Price Dataset** (历史训练)

---

## 📊 评分对齐

| Canvas 维度 | 占比 | 前端实现 |
|-------------|:---:|----------|
| 仪表板展示 | 10% | ✅ 7 个完整模块 + 现代化 UI |
| 金融相关性 | 20% | ✅ 风险指标(Sharpe/Drawdown/Volatility) + 模拟回测 |
| 数据+特征展示 | 20% | ✅ 20 饰品 + K线 + 跨平台比价字段 |
| 模型可视化 | 20% | ✅ 对比表 + 雷达图 + SHAP + 回测曲线 |
| 评估+基准 | 20% | ✅ 10 模型对比 + 买入持有基准 |
| 演讲/演示 | 10% | ✅ 完整交互式 Demo |

---

## 📝 后续优化

- [ ] 接入真实 BUFF/Skinport API
- [ ] FastAPI 后端 + WebSocket 实时推送
- [ ] 用户系统(登录/收藏/历史记录)
- [ ] 移动端适配(< 768px)
- [ ] 国际化(中/英)
- [ ] 暗色/亮色主题切换
- [ ] 数据导出(Excel/PDF 报告)

---

## 📄 许可

仅供 SWS3022 课程项目使用 · © 2026 SkinVision AI Team
