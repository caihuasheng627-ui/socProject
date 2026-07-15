// ============================================
// SkinVision AI - Mock 数据层
// 基于 Project_Proposal_SkinVest.md 数据方案
// ============================================

// CS2 饰品池（来自策划书 3.4 节：20-30 个高流动性饰品）
const SKINS_POOL = [
  { id: 'ak47-redline-ft', name: 'AK-47 | Redline (FT)', category: '步枪', wear: 'Field-Tested', price: 429.50, change24h: 2.34, change7d: -1.23, volume24h: 1630, liquidity: 98, rarity: 4, image: '🔫' },
  { id: 'ak47-fireserpent-fn', name: 'AK-47 | Fire Serpent (FN)', category: '步枪', wear: 'Factory New', price: 15850.00, change24h: 0.85, change7d: 5.67, volume24h: 55, liquidity: 72, rarity: 7, image: '🐍' },
  { id: 'awp-dragonlore-ft', name: 'AWP | Dragon Lore (FT)', category: '狙击枪', wear: 'Field-Tested', price: 42788.00, change24h: 1.23, change7d: 8.45, volume24h: 16, liquidity: 65, rarity: 7, image: '🐉' },
  { id: 'awp-asiimov-ft', name: 'AWP | Asiimov (FT)', category: '狙击枪', wear: 'Field-Tested', price: 1280.00, change24h: -0.45, change7d: 3.21, volume24h: 240, liquidity: 90, rarity: 5, image: '🎯' },
  { id: 'm4a1s-printstream-ft', name: 'M4A1-S | Printstream (FT)', category: '步枪', wear: 'Field-Tested', price: 980.00, change24h: 3.12, change7d: 6.78, volume24h: 312, liquidity: 88, rarity: 5, image: '🖨️' },
  { id: 'm4a4-howl-mw', name: 'M4A4 | Howl (MW)', category: '步枪', wear: 'Minimal Wear', price: 26800.00, change24h: -0.23, change7d: -2.15, volume24h: 8, liquidity: 60, rarity: 7, image: '🐺' },
  { id: 'deagle-printstream-ft', name: 'Desert Eagle | Printstream (FT)', category: '手枪', wear: 'Field-Tested', price: 320.50, change24h: 1.56, change7d: 4.32, volume24h: 480, liquidity: 92, rarity: 4, image: '🦅' },
  { id: 'deagle-crimsonweb-mw', name: 'Desert Eagle | Crimson Web (MW)', category: '手枪', wear: 'Minimal Wear', price: 280.00, change24h: -1.20, change7d: 2.10, volume24h: 350, liquidity: 85, rarity: 4, image: '🕸️' },
  { id: 'butterfly-northern-fn', name: '★ Butterfly Knife | Northern Forest (FN)', category: '刀具', wear: 'Factory New', price: 3110.00, change24h: 0.92, change7d: 3.45, volume24h: 26, liquidity: 70, rarity: 6, image: '🦋' },
  { id: 'm9-doppler-fn', name: '★ M9 Bayonet | Doppler (FN)', category: '刀具', wear: 'Factory New', price: 12800.00, change24h: 2.15, change7d: 7.89, volume24h: 18, liquidity: 68, rarity: 6, image: '🔪' },
  { id: 'karambit-doppler-fn', name: '★ Karambit | Doppler (FN)', category: '刀具', wear: 'Factory New', price: 18500.00, change24h: 1.85, change7d: 5.23, volume24h: 12, liquidity: 65, rarity: 6, image: '🌀' },
  { id: 'gloves-pandora-ft', name: '★ Sport Gloves | Pandora\'s Box (FT)', category: '手套', wear: 'Field-Tested', price: 22600.00, change24h: 0.45, change7d: 2.67, volume24h: 6, liquidity: 58, rarity: 7, image: '🎁' },
  { id: 'gloves-crimson-ft', name: '★ Specialist Gloves | Crimson Web (FT)', category: '手套', wear: 'Field-Tested', price: 9800.00, change24h: -0.65, change7d: -1.45, volume24h: 14, liquidity: 62, rarity: 6, image: '🧤' },
  { id: 'case-dreams', name: 'Dreams & Nightmares Case', category: '箱子', wear: 'N/A', price: 9.15, change24h: 0.00, change7d: 0.00, volume24h: 90471, liquidity: 99, rarity: 1, image: '📦' },
  { id: 'case-dangerzone', name: 'Danger Zone Case', category: '箱子', wear: 'N/A', price: 18.50, change24h: 1.20, change7d: 2.45, volume24h: 12500, liquidity: 95, rarity: 1, image: '⚠️' },
  { id: 'case-prisma', name: 'Prisma Case', category: '箱子', wear: 'N/A', price: 6.80, change24h: -0.30, change7d: 1.20, volume24h: 18900, liquidity: 96, rarity: 1, image: '🌈' },
  { id: 'usps-killconfirmed-ft', name: 'USP-S | Kill Confirmed (FT)', category: '手枪', wear: 'Field-Tested', price: 145.00, change24h: 0.85, change7d: 2.15, volume24h: 580, liquidity: 90, rarity: 3, image: '✅' },
  { id: 'glock-fade-ft', name: 'Glock-18 | Fade (FT)', category: '手枪', wear: 'Factory New', price: 680.00, change24h: 1.45, change7d: 3.78, volume24h: 120, liquidity: 80, rarity: 4, image: '🌅' },
  { id: 'awp-hyperbeast-ft', name: 'AWP | Hyper Beast (FT)', category: '狙击枪', wear: 'Field-Tested', price: 195.00, change24h: -0.55, change7d: 1.85, volume24h: 410, liquidity: 88, rarity: 3, image: '👹' },
  { id: 'famas-roll-cage-ft', name: 'FAMAS | Roll Cage (FT)', category: '步枪', wear: 'Field-Tested', price: 38.50, change24h: 0.25, change7d: 0.85, volume24h: 1200, liquidity: 92, rarity: 2, image: '🏎️' },
];

// K线图生成器（基于策划书 3.3 节：BUFF API 历史价格 ~2次/天 × 90天）
function generateKLineData(basePrice, days = 90, volatility = 0.03, trend = 0.001) {
  const data = [];
  const volumes = [];
  let currentPrice = basePrice * 0.85; // 90天前较低
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;

  for (let i = days; i >= 0; i--) {
    const date = new Date(now - i * dayMs);
    const dateStr = `${date.getMonth() + 1}/${date.getDate()}`;

    // 模拟价格波动 + 趋势
    const change = (Math.random() - 0.5) * volatility * currentPrice + currentPrice * trend;
    const open = currentPrice;
    const close = currentPrice + change;
    const high = Math.max(open, close) * (1 + Math.random() * 0.015);
    const low = Math.min(open, close) * (1 - Math.random() * 0.015);
    const volume = Math.floor(Math.random() * 5000 + 1000) * (basePrice > 1000 ? 0.1 : 1);

    data.push([dateStr, open.toFixed(2), close.toFixed(2), low.toFixed(2), high.toFixed(2)]);
    volumes.push([i, volume, close > open ? 1 : -1]);
    currentPrice = close;
  }

  // 最后一天设为当前价格
  data[data.length - 1][2] = basePrice.toFixed(2);
  return { kline: data, volumes };
}

// MA 指标计算
function calculateMA(data, period) {
  const result = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push('-');
    } else {
      let sum = 0;
      for (let j = 0; j < period; j++) {
        sum += parseFloat(data[i - j][1]); // 使用 close 价格
      }
      result.push((sum / period).toFixed(2));
    }
  }
  return result;
}

// 新闻数据（RAG 知识库）
const NEWS_FEED = [
  { id: 1, time: '2小时前', source: 'Valve 官方', title: 'CS2 7月更新:武器平衡性调整', summary: 'Valve 发布最新 CS2 更新,AK-47 后坐力微调,无削弱操作。', impact: 'AK 系列利好', sentiment: 'positive', relatedSkins: ['ak47-redline-ft', 'ak47-fireserpent-fn'] },
  { id: 2, time: '5小时前', source: 'HLTV', title: 'IEM Cologne 2026 即将开赛', summary: 'IEM Cologne 即将于 7月25日开赛,Major 级别赛事带动饰品经济。', impact: '整体利好', sentiment: 'positive', relatedSkins: ['awp-dragonlore-ft', 'ak47-fireserpent-fn'] },
  { id: 3, time: '1天前', source: 'Reddit r/csgomarketforum', title: '手套市场流通性下降讨论', summary: '社区热议近期高端手套成交稀疏,价格短期承压。', impact: '手套类利空', sentiment: 'negative', relatedSkins: ['gloves-pandora-ft', 'gloves-crimson-ft'] },
  { id: 4, time: '2天前', source: 'Steam 公告', title: 'Dreams & Nightmares Case 掉宝率提升', summary: 'Valve 临时提升 D&N Case 掉宝率,箱子价格短期波动。', impact: '箱子价格波动', sentiment: 'neutral', relatedSkins: ['case-dreams'] },
  { id: 5, time: '3天前', source: 'HLTV', title: 'Top 战队偏爱 AWP Dragon Lore', summary: '近期多场重要比赛中,职业选手频繁使用 AWP Dragon Lore。', impact: 'AWP 龙狙利好', sentiment: 'positive', relatedSkins: ['awp-dragonlore-ft'] },
  { id: 6, time: '4天前', source: 'BUFF 公告', title: '交易手续费限时减免活动', summary: 'BUFF 推出限时交易手续费减免,提高市场活跃度。', impact: '整体利好', sentiment: 'positive', relatedSkins: [] },
];

// 模型对比数据（来自策划书 5.1-5.4）
const MODEL_COMPARISON = {
  regression: [
    { name: 'ARIMA', rmse: 142.35, mae: 98.23, mape: 4.23, r2: 0.72, accuracy: null, auc: null, returnPct: 8.5, speed: '快', interpretability: 3, type: '统计基线' },
    { name: 'XGBoost', rmse: 89.12, mae: 62.45, mape: 2.87, r2: 0.89, accuracy: null, auc: null, returnPct: 18.7, speed: '快', interpretability: 2, type: 'ML 主力' },
    { name: 'LightGBM', rmse: 92.78, mae: 64.12, mape: 2.95, r2: 0.88, accuracy: null, auc: null, returnPct: 17.3, speed: '极快', interpretability: 2, type: 'ML 对比' },
    { name: 'Random Forest', rmse: 105.34, mae: 75.67, mape: 3.42, r2: 0.84, accuracy: null, auc: null, returnPct: 12.4, speed: '快', interpretability: 2, type: '集成基线' },
    { name: 'LSTM', rmse: 76.45, mae: 52.34, mape: 2.31, r2: 0.92, accuracy: null, auc: null, returnPct: 23.5, speed: '慢', interpretability: 1, type: 'DL 主力' },
    { name: 'GRU', rmse: 81.23, mae: 56.78, mape: 2.56, r2: 0.91, accuracy: null, auc: null, returnPct: 21.8, speed: '慢', interpretability: 1, type: 'DL 对比' },
  ],
  classification: [
    { name: 'Logistic Regression', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.58, auc: 0.61, returnPct: 6.2, speed: '快', interpretability: 3, type: '线性基线' },
    { name: 'Random Forest', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.65, auc: 0.69, returnPct: 12.4, speed: '快', interpretability: 2, type: '集成基线' },
    { name: 'XGBoost', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.71, auc: 0.76, returnPct: 18.7, speed: '快', interpretability: 2, type: 'ML 主力' },
    { name: 'LightGBM', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.70, auc: 0.75, returnPct: 17.3, speed: '极快', interpretability: 2, type: 'ML 对比' },
  ],
  buyAndHold: { name: '买入持有', returnPct: 9.8 }
};

// 回测数据
function generateBacktestData(days = 60) {
  const data = {};
  const models = ['ARIMA', 'XGBoost', 'LightGBM', 'LSTM', '买入持有'];
  models.forEach(model => {
    const series = [];
    let value = 100;
    const drift = model === '买入持有' ? 0.0015 :
                  model === 'LSTM' ? 0.0035 :
                  model === 'XGBoost' ? 0.0028 :
                  model === 'LightGBM' ? 0.0025 : 0.0012;
    const vol = model === '买入持有' ? 0.015 : 0.022;
    for (let i = 0; i < days; i++) {
      value = value * (1 + (Math.random() - 0.5) * vol + drift);
      series.push(value.toFixed(2));
    }
    data[model] = series;
  });
  return data;
}

// SHAP 特征重要性（来自策划书 5.3 节）
const SHAP_FEATURES = [
  { name: 'MA_30 偏离度', value: 0.245, importance: 0.245 },
  { name: 'RSI_14', value: 0.198, importance: 0.198 },
  { name: 'Volume_Change_Ratio', value: 0.167, importance: 0.167 },
  { name: 'Return_7d', value: 0.142, importance: 0.142 },
  { name: 'MACD', value: 0.098, importance: 0.098 },
  { name: 'BB_position', value: 0.076, importance: 0.076 },
  { name: 'Days_To_Major', value: 0.045, importance: 0.045 },
  { name: 'Steam_CCU', value: 0.029, importance: 0.029 },
];

// 涨跌榜数据
const TOP_GAINERS = [...SKINS_POOL].sort((a, b) => b.change7d - a.change7d).slice(0, 8);
const TOP_LOSERS = [...SKINS_POOL].sort((a, b) => a.change7d - b.change7d).slice(0, 8);
const HOT_VOLUME = [...SKINS_POOL].sort((a, b) => b.volume24h - a.volume24h).slice(0, 8);

// 双 Agent 辩论示例
const DEBATE_SAMPLE = {
  skin: 'AK-47 | Fire Serpent (FN)',
  currentPrice: 15850,
  rounds: [
    {
      round: 1,
      bull: '🟢 多头 Agent:技术面显示 MA_30 上穿 MA_90 形成金叉,ML 模型预测 7 天内上涨 5.67% (+¥898),RSI 处于 58 健康区间。叠加 IEM Cologne 赛事临近,AK 系列历史表现强势。',
      bear: '🔴 空头 Agent:当前价格已突破历史阻力位 ¥15,500,估值偏高。MACD 红柱缩短,动能减弱。社区情绪调查显示看多比例已超 75%,反向指标。'
    },
    {
      round: 2,
      bull: '🟢 多头 Agent:你说的阻力位其实在 7 月 8 日已被突破并回踩确认,目前是支撑位。历史类似金叉后 30 天平均涨幅 4.2%,你的反向情绪指标有滞后性。',
      bear: '🔴 空头 Agent:接受技术面信号,但 Kaggle 训练数据多为 CS:GO 时期,CS2 后市场结构变化(玩家基数+30%)是否已充分建模?流动性指标最近 3 天下降 15%。'
    },
    {
      round: 3,
      bull: '🟢 多头 Agent:承认结构变化是潜在风险,下调短期预期。但 ML 模型已加入 Days_Since_Release 和 Steam_CCU 等外部特征,应已捕捉结构性差异。建议入场区间 ¥15,400-15,600 而非追高。',
      bear: '🔴 空头 Agent:认可保守入场策略。补充一点:LSTM 与 XGBoost 共识度 76%,中等偏高,但 GRU 给出反向信号。建议设置 ¥14,800 止损。'
    }
  ],
  consensus: {
    recommendation: '谨慎看多',
    entryRange: '¥15,400 - 15,600',
    stopLoss: '¥14,800',
    targetPrice: '¥16,800 (30天)',
    consensusScore: 76,
    confidence: '中等偏高',
    risks: ['短期回调风险', 'CS2 后市场结构变化', '职业比赛结果不确定性']
  }
};

// 模拟持仓（默认数据）
const DEFAULT_PORTFOLIO = [
  { id: 1, skinId: 'ak47-redline-ft', name: 'AK-47 | Redline (FT)', buyPrice: 380, quantity: 5, buyDate: '2026-06-15' },
  { id: 2, skinId: 'm4a1s-printstream-ft', name: 'M4A1-S | Printstream (FT)', buyPrice: 850, quantity: 2, buyDate: '2026-06-28' },
  { id: 3, skinId: 'awp-asiimov-ft', name: 'AWP | Asiimov (FT)', buyPrice: 1200, quantity: 1, buyDate: '2026-07-05' },
];

// 风险指标生成
function calculateRiskMetrics(portfolio, currentPrices) {
  let totalCost = 0, totalValue = 0;
  portfolio.forEach(item => {
    totalCost += item.buyPrice * item.quantity;
    totalValue += (currentPrices[item.skinId] || item.buyPrice) * item.quantity;
  });
  const pnl = totalValue - totalCost;
  const pnlPct = (pnl / totalCost) * 100;
  return {
    totalCost: totalCost.toFixed(2),
    totalValue: totalValue.toFixed(2),
    pnl: pnl.toFixed(2),
    pnlPct: pnlPct.toFixed(2),
    sharpeRatio: (1.45 + Math.random() * 0.5).toFixed(2),
    maxDrawdown: (-(8.5 + Math.random() * 4)).toFixed(2),
    volatility: (15.2 + Math.random() * 5).toFixed(2),
  };
}

// AI 对话预设回复
const AI_PRESET_RESPONSES = {
  'ak47-fireserpent-fn': `基于最新数据,AK-47 | Fire Serpent (FN) 当前价格 ¥15,850。

**多模型分析:**
- LSTM 预测 7 天: ¥16,120 (+1.7%) ⭐
- XGBoost 预测 7 天: ¥16,015 (+1.0%)
- 模型共识度: 76% (中等偏高)

**RAG 关联事件:**
1. 7月12日 Valve 更新中 AK-47 未受削弱,市场信心增强
2. 距离 IEM Cologne 还有 10 天,历史同期 AK 皮肤平均上涨 3.2%

**建议:** 谨慎看多,入场区间 ¥15,400-15,600,目标价 ¥16,800。⚠️ 仅供参考,不构成投资建议。`,
  'awp-dragonlore-ft': `AWP | Dragon Lore (FT) 当前价格 ¥42,788,近 7 天 +8.45%。

**市场热点:**
- Top 战队近期频繁使用,职业选手偏好上升
- MA_30 强势突破,技术面看多

**模型预测:**
- 30 天目标价: ¥48,500 (+13.4%)
- 共识度: 82% (高)

**风险提示:** 高端饰品流动性差,实际成交价可能低于挂价。建议小仓位参与,设置 ¥40,000 止损。`,
  'default': `已为您分析该饰品。基于 6 个回归模型 + 4 个分类模型的综合判断,模型共识度 68%。

**核心建议:**
- 入场区间已根据波动率和技术指标动态计算
- 短期(7天)目标价基于 LSTM/XGBoost 加权
- 长期(30天)参考 ML 趋势 + 赛事周期

如需更详细的分析,请告诉我具体的饰品名称,或选择下方推荐问题。`
};

// 推荐问题
const SUGGESTED_QUESTIONS = [
  '🔍 AK-47 火蛇现在该买吗?',
  '💰 5000 预算,中等风险,推荐什么?',
  '📈 今天哪些饰品在涨?',
  '🎯 哪个饰品最值得长期持有?',
  '⚠️ 帮我设置价格预警',
  '📊 模型对比结果怎么样?',
];

// 导出到全局
window.SkinVisionData = {
  SKINS_POOL,
  generateKLineData,
  calculateMA,
  NEWS_FEED,
  MODEL_COMPARISON,
  generateBacktestData,
  SHAP_FEATURES,
  TOP_GAINERS,
  TOP_LOSERS,
  HOT_VOLUME,
  DEBATE_SAMPLE,
  DEFAULT_PORTFOLIO,
  calculateRiskMetrics,
  AI_PRESET_RESPONSES,
  SUGGESTED_QUESTIONS,
};
