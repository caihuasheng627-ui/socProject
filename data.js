// ============================================
// CSVest - Mock 数据层
// 回归指标对齐 ml/outputs/compare_results_test.json（公平 test）
// Hybrid 路由对齐 ml/models/lstm_hybrid_route.json：low→C, mid/high→D
// ============================================

// CS2 饰品池（来自策划书 3.4 节：20-30 个高流动性饰品）
const SKINS_POOL = [
  { id: 'ak47-redline-ft', name: 'AK-47 | Redline (FT)', category: '步枪', wear: 'Field-Tested', price: 59.65, change24h: 2.34, change7d: -1.23, volume24h: 1630, liquidity: 98, rarity: 4, image: '🔫' },
  { id: 'ak47-fireserpent-fn', name: 'AK-47 | Fire Serpent (FN)', category: '步枪', wear: 'Factory New', price: 2201.39, change24h: 0.85, change7d: 5.67, volume24h: 55, liquidity: 72, rarity: 7, image: '🐍' },
  { id: 'awp-dragonlore-ft', name: 'AWP | Dragon Lore (FT)', category: '狙击枪', wear: 'Field-Tested', price: 5942.78, change24h: 1.23, change7d: 8.45, volume24h: 16, liquidity: 65, rarity: 7, image: '🐉' },
  { id: 'awp-asiimov-ft', name: 'AWP | Asiimov (FT)', category: '狙击枪', wear: 'Field-Tested', price: 177.78, change24h: -0.45, change7d: 3.21, volume24h: 240, liquidity: 90, rarity: 5, image: '🎯' },
  { id: 'm4a1s-printstream-ft', name: 'M4A1-S | Printstream (FT)', category: '步枪', wear: 'Field-Tested', price: 136.11, change24h: 3.12, change7d: 6.78, volume24h: 312, liquidity: 88, rarity: 5, image: '🖨️' },
  { id: 'm4a4-howl-mw', name: 'M4A4 | Howl (MW)', category: '步枪', wear: 'Minimal Wear', price: 3722.22, change24h: -0.23, change7d: -2.15, volume24h: 8, liquidity: 60, rarity: 7, image: '🐺' },
  { id: 'deagle-printstream-ft', name: 'Desert Eagle | Printstream (FT)', category: '手枪', wear: 'Field-Tested', price: 44.51, change24h: 1.56, change7d: 4.32, volume24h: 480, liquidity: 92, rarity: 4, image: '🦅' },
  { id: 'deagle-crimsonweb-mw', name: 'Desert Eagle | Crimson Web (MW)', category: '手枪', wear: 'Minimal Wear', price: 38.89, change24h: -1.20, change7d: 2.10, volume24h: 350, liquidity: 85, rarity: 4, image: '🕸️' },
  { id: 'butterfly-northern-fn', name: '★ Butterfly Knife | Northern Forest (FN)', category: '刀具', wear: 'Factory New', price: 431.94, change24h: 0.92, change7d: 3.45, volume24h: 26, liquidity: 70, rarity: 6, image: '🦋' },
  { id: 'm9-doppler-fn', name: '★ M9 Bayonet | Doppler (FN)', category: '刀具', wear: 'Factory New', price: 1777.78, change24h: 2.15, change7d: 7.89, volume24h: 18, liquidity: 68, rarity: 6, image: '🔪' },
  { id: 'karambit-doppler-fn', name: '★ Karambit | Doppler (FN)', category: '刀具', wear: 'Factory New', price: 2569.44, change24h: 1.85, change7d: 5.23, volume24h: 12, liquidity: 65, rarity: 6, image: '🌀' },
  { id: 'gloves-pandora-ft', name: '★ Sport Gloves | Pandora\'s Box (FT)', category: '手套', wear: 'Field-Tested', price: 3138.89, change24h: 0.45, change7d: 2.67, volume24h: 6, liquidity: 58, rarity: 7, image: '🎁' },
  { id: 'gloves-crimson-ft', name: '★ Specialist Gloves | Crimson Web (FT)', category: '手套', wear: 'Field-Tested', price: 1361.11, change24h: -0.65, change7d: -1.45, volume24h: 14, liquidity: 62, rarity: 6, image: '🧤' },
  { id: 'case-dreams', name: 'Dreams & Nightmares Case', category: '箱子', wear: 'N/A', price: 1.27, change24h: 0.00, change7d: 0.00, volume24h: 90471, liquidity: 99, rarity: 1, image: '📦' },
  { id: 'case-dangerzone', name: 'Danger Zone Case', category: '箱子', wear: 'N/A', price: 2.57, change24h: 1.20, change7d: 2.45, volume24h: 12500, liquidity: 95, rarity: 1, image: '⚠️' },
  { id: 'case-prisma', name: 'Prisma Case', category: '箱子', wear: 'N/A', price: 0.94, change24h: -0.30, change7d: 1.20, volume24h: 18900, liquidity: 96, rarity: 1, image: '🌈' },
  { id: 'usps-killconfirmed-ft', name: 'USP-S | Kill Confirmed (FT)', category: '手枪', wear: 'Field-Tested', price: 20.14, change24h: 0.85, change7d: 2.15, volume24h: 580, liquidity: 90, rarity: 3, image: '✅' },
  { id: 'glock-fade-ft', name: 'Glock-18 | Fade (FT)', category: '手枪', wear: 'Factory New', price: 94.44, change24h: 1.45, change7d: 3.78, volume24h: 120, liquidity: 80, rarity: 4, image: '🌅' },
  { id: 'awp-hyperbeast-ft', name: 'AWP | Hyper Beast (FT)', category: '狙击枪', wear: 'Field-Tested', price: 27.08, change24h: -0.55, change7d: 1.85, volume24h: 410, liquidity: 88, rarity: 3, image: '👹' },
  { id: 'famas-roll-cage-ft', name: 'FAMAS | Roll Cage (FT)', category: '步枪', wear: 'Field-Tested', price: 5.35, change24h: 0.25, change7d: 0.85, volume24h: 1200, liquidity: 92, rarity: 2, image: '🏎️' },
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
    // 与后端契约一致:volume 为整数(高价饰品成交量按 1/10 缩放)
    const volume = Math.round((Math.random() * 5000 + 1000) * (basePrice > 1000 ? 0.1 : 1));

    data.push([dateStr, open, close, low, high]);
    // 索引须按时间顺序（旧→新），与 K 线类目轴对齐
    volumes.push([data.length - 1, volume, close > open ? 1 : -1]);
    currentPrice = close;
  }

  // 整体缩放使最后收盘价 = 当前价格（避免最后一天出现跳变异常 K 线）
  const ratio = basePrice / currentPrice;
  for (const d of data) {
    for (let j = 1; j <= 4; j++) d[j] = (d[j] * ratio).toFixed(2);
  }
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
        sum += parseFloat(data[i - j][2]); // close 在索引 2（[date, open, close, low, high]）
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

// 模型对比：公平 test（25,702 行 · 113 件 · horizon=7；剔除同名异价脏行）
// 来源 ml/outputs/compare_results_test.json · 2026-07-23 树模型重训后
// returnPct 来自同契约回测 fee=0（子集演示曲线，非主结论）
const MODEL_COMPARISON = {
  split: 'test',
  horizonSteps: 7,
  nItems: 113,
  nRows: 25702,
  hybridRoute: { low: 'LSTM-C', mid: 'LSTM-D', high: 'LSTM-D' },
  note: 'Fair test after 2026-07-23 tree retrain. Rows with mismatched truth vs LSTM panel dropped. LSTM-C best RMSE/MAE/R²; RF best MAPE.',
  regression: [
    { name: 'LSTM-C', rmse: 46.43, mae: 7.87, mape: 9.66, r2: 0.9488, accuracy: null, auc: null, returnPct: 47.7, speed: '慢', interpretability: 1, typeKey: 'dl_panel', type: 'DL · 共享面板', course: 'DL · panel Embedding' },
    { name: 'LSTM-D', rmse: 60.95, mae: 9.59, mape: 9.40, r2: 0.9118, accuracy: null, auc: null, returnPct: 52.4, speed: '慢', interpretability: 1, typeKey: 'dl_tier', type: 'DL · 分组', course: 'DL · price tiers' },
    { name: 'Hybrid', rmse: 60.95, mae: 9.58, mape: 9.63, r2: 0.9118, accuracy: null, auc: null, returnPct: 36.5, speed: '慢', interpretability: 1, typeKey: 'route', type: '部署路由', course: 'Route: low→C, mid/high→D' },
    { name: 'Random Forest', rmse: 62.42, mae: 9.92, mape: 7.84, r2: 0.9075, accuracy: null, auc: null, returnPct: 133.8, speed: '快', interpretability: 2, typeKey: 'tree_mape', type: '树 · MAPE最优', course: 'Best MAPE on fair test' },
    { name: 'LightGBM', rmse: 70.70, mae: 11.78, mape: 7.93, r2: 0.8814, accuracy: null, auc: null, returnPct: 32.7, speed: '极快', interpretability: 2, typeKey: 'tree', type: '树模型', course: 'Tree ensemble' },
    { name: 'XGBoost', rmse: 71.61, mae: 12.19, mape: 8.01, r2: 0.8783, accuracy: null, auc: null, returnPct: 26.1, speed: '快', interpretability: 2, typeKey: 'tree', type: '树模型', course: 'Tree ensemble' },
  ],
  classification: [
    { name: 'Logistic Regression', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.58, auc: 0.61, returnPct: 6.2, speed: '快', interpretability: 3, typeKey: 'linear', type: '线性基线' },
    { name: 'Random Forest', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.6837, auc: 0.8551, returnPct: 12.4, speed: '快', interpretability: 2, typeKey: 'ensemble', type: '集成基线' },
    { name: 'XGBoost', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.6798, auc: 0.8536, returnPct: 26.1, speed: '快', interpretability: 2, typeKey: 'direction', type: '方向分类' },
    { name: 'LightGBM', rmse: null, mae: null, mape: null, r2: null, accuracy: 0.6805, auc: 0.8537, returnPct: 32.7, speed: '极快', interpretability: 2, typeKey: 'direction', type: '方向分类' },
  ],
  buyAndHold: { name: '买入持有', returnPct: 194.65 }
};

// 回测曲线 Mock（演示用；真实曲线见 ml/outputs/backtest/backtest_curves.json）
function generateBacktestData(days = 60) {
  const data = {};
  const models = ['LSTM-C', 'Hybrid', 'Random Forest', 'LightGBM', 'XGBoost', '买入持有'];
  const driftMap = {
    '买入持有': 0.0012,
    'LSTM-C': 0.0032,
    'Hybrid': 0.0026,
    'Random Forest': 0.0040,
    'LightGBM': 0.0024,
    'XGBoost': 0.0022,
  };
  models.forEach(model => {
    const series = [];
    let value = 100;
    const drift = driftMap[model] || 0.0015;
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
const TOP_GAINERS = [...SKINS_POOL].filter((a) => a.change7d > 0).sort((a, b) => b.change7d - a.change7d).slice(0, 8);
const TOP_LOSERS = [...SKINS_POOL].filter((a) => a.change7d < 0).sort((a, b) => a.change7d - b.change7d).slice(0, 8);
const HOT_VOLUME = [...SKINS_POOL].sort((a, b) => b.volume24h - a.volume24h).slice(0, 8);

// 双 Agent 辩论示例
const DEBATE_SAMPLE = {
  skin: 'AK-47 | Fire Serpent (FN)',
  currentPrice: 2201.39,
  rounds: [
    {
      round: 1,
      bull: '🟢 多头 Agent:技术面显示 MA_30 上穿 MA_90 形成金叉,ML 模型预测 7 天内上涨 5.67% (+$125),RSI 处于 58 健康区间。叠加 IEM Cologne 赛事临近,AK 系列历史表现强势。',
      bear: '🔴 空头 Agent:当前价格已突破历史阻力位 $2,150,估值偏高。MACD 红柱缩短,动能减弱。社区情绪调查显示看多比例已超 75%,反向指标。'
    },
    {
      round: 2,
      bull: '🟢 多头 Agent:你说的阻力位其实在 7 月 8 日已被突破并回踩确认,目前是支撑位。历史类似金叉后 30 天平均涨幅 4.2%,你的反向情绪指标有滞后性。',
      bear: '🔴 空头 Agent:接受技术面信号,但 Kaggle 训练数据多为 CS:GO 时期,CS2 后市场结构变化(玩家基数+30%)是否已充分建模?流动性指标最近 3 天下降 15%。'
    },
    {
      round: 3,
      bull: '🟢 多头 Agent:承认结构变化是潜在风险,下调短期预期。但 ML 模型已加入 Days_Since_Release 和 Steam_CCU 等外部特征,应已捕捉结构性差异。建议入场区间 $2,140-2,170 而非追高。',
      bear: '🔴 空头 Agent:认可保守入场策略。补充一点:LSTM 与 XGBoost 共识度 76%,中等偏高,但 GRU 给出反向信号。建议设置 $2,055 止损。'
    }
  ],
  consensus: {
    recommendation: '谨慎看多',
    entryRange: '$2,140 - 2,170',
    stopLoss: '$2,055',
    targetPrice: '$2,333 (30天)',
    consensusScore: 76,
    confidence: '中等偏高',
    risks: ['短期回调风险', 'CS2 后市场结构变化', '职业比赛结果不确定性']
  }
};

// 模拟持仓（默认数据）
const DEFAULT_PORTFOLIO = [
  { id: 1, skinId: 'ak47-redline-ft', name: 'AK-47 | Redline (FT)', buyPrice: 52.78, quantity: 5, buyDate: '2026-06-15', holdingType: 'sim' },
  { id: 2, skinId: 'm4a1s-printstream-ft', name: 'M4A1-S | Printstream (FT)', buyPrice: 118.06, quantity: 2, buyDate: '2026-06-28', holdingType: 'sim' },
  { id: 3, skinId: 'awp-asiimov-ft', name: 'AWP | Asiimov (FT)', buyPrice: 166.67, quantity: 1, buyDate: '2026-07-05', holdingType: 'sim' },
];

// 我的库存（真实库存：手动添加 / Steam 导入；Steam 待对接）
const DEFAULT_INVENTORY = [
  { id: 101, skinId: 'ak47-redline-ft', name: 'AK-47 | Redline (FT)', acquirePrice: 48.50, quantity: 3, acquireDate: '2026-05-20', source: 'manual' },
  { id: 102, skinId: 'deagle-printstream-ft', name: 'Desert Eagle | Printstream (FT)', acquirePrice: 41.20, quantity: 4, acquireDate: '2026-06-02', source: 'manual' },
  { id: 103, skinId: 'awp-asiimov-ft', name: 'AWP | Asiimov (FT)', acquirePrice: 170.00, quantity: 1, acquireDate: '2026-06-18', source: 'manual' },
  { id: 104, skinId: 'usps-killconfirmed-ft', name: 'USP-S | Kill Confirmed (FT)', acquirePrice: 18.90, quantity: 6, acquireDate: '2026-07-01', source: 'manual' },
];

/** 根据库存与饰品现价，生成库存总价值历史 + 预测曲线（前端 mock，后端对接后走 API） */
function generateInventoryValueHistory(inventory, days = 90, forecastDays = 7) {
  const dates = [];
  const values = [];
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;
  const items = inventory || [];
  const baseTotal = items.reduce((sum, item) => {
    const skin = SKINS_POOL.find(s => s.id === item.skinId);
    const price = skin?.price ?? item.acquirePrice ?? 0;
    return sum + price * (item.quantity || 1);
  }, 0) || 1000;

  // 按市值加权的 24h 涨跌，用于预测段斜率（日均变化率）
  let weightSum = 0;
  let weightedChange = 0;
  items.forEach((item) => {
    const skin = SKINS_POOL.find(s => s.id === item.skinId);
    const price = skin?.price ?? item.acquirePrice ?? 0;
    const w = price * (item.quantity || 1);
    weightSum += w;
    weightedChange += w * ((skin?.change24h ?? 0) / 100);
  });
  // 将 24h 涨跌摊到预测区间，并限制幅度，避免曲线失真
  const avgChange = weightSum > 0 ? weightedChange / weightSum : 0.002;
  const horizonMove = Math.max(-0.08, Math.min(0.08, avgChange * 0.85));

  let cursor = baseTotal * 0.88;
  for (let i = days; i >= 0; i--) {
    const d = new Date(now - i * dayMs);
    dates.push(`${d.getMonth() + 1}/${d.getDate()}`);
    const drift = (Math.random() - 0.48) * 0.012 * cursor;
    cursor = Math.max(cursor + drift, baseTotal * 0.6);
    values.push(+cursor.toFixed(2));
  }
  // 末日对齐当前总市值
  if (values.length) values[values.length - 1] = +baseTotal.toFixed(2);

  const predictedDates = [];
  const predictedValues = [];
  let seed = Math.round(baseTotal) % 997;
  const rand = () => {
    seed = (seed * 137 + 71) % 997;
    return seed / 997 - 0.5;
  };
  for (let i = 1; i <= forecastDays; i++) {
    const d = new Date(now + i * dayMs);
    predictedDates.push(`${d.getMonth() + 1}/${d.getDate()}`);
    const t = i / forecastDays;
    const eased = 1 - Math.pow(1 - t, 2);
    const wiggle = i === forecastDays ? 0 : rand() * 0.004;
    const predCursor = baseTotal * (1 + horizonMove * eased + wiggle);
    predictedValues.push(+predCursor.toFixed(2));
  }

  return {
    dates,
    values,
    predictedDates,
    predictedValues,
    total: +baseTotal.toFixed(2),
  };
}

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
  'ak47-fireserpent-fn': `基于最新数据,AK-47 | Fire Serpent (FN) 当前价格 $2,201。

**多模型分析:**
- LSTM 预测 7 天: $2,239 (+1.7%) ⭐
- XGBoost 预测 7 天: $2,223 (+1.0%)
- 模型共识度: 76% (中等偏高)

**RAG 关联事件:**
1. 7月12日 Valve 更新中 AK-47 未受削弱,市场信心增强
2. 距离 IEM Cologne 还有 10 天,历史同期 AK 皮肤平均上涨 3.2%

**建议:** 谨慎看多,入场区间 $2,140-2,170,目标价 $2,333。⚠️ 仅供参考,不构成投资建议。`,
  'awp-dragonlore-ft': `AWP | Dragon Lore (FT) 当前价格 $5,943,近 7 天 +8.45%。

**市场热点:**
- Top 战队近期频繁使用,职业选手偏好上升
- MA_30 强势突破,技术面看多

**模型预测:**
- 30 天目标价: $6,736 (+13.4%)
- 共识度: 82% (高)

**风险提示:** 高端饰品流动性差,实际成交价可能低于挂价。建议小仓位参与,设置 $5,556 止损。`,
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
  '💰 $700 预算,中等风险,推荐什么?',
  '📈 今天哪些饰品在涨?',
  '🎯 哪个饰品最值得长期持有?',
  '⚠️ 帮我设置价格预警',
  '📊 模型对比结果怎么样?',
];

const DEBATE_SUGGESTED_QUESTIONS = [
  '🐂🐻 对 AK-47 火蛇发起牛熊辩论',
  '🐂🐻 龙狙现在多空怎么看?',
  '🐂🐻 红线该不该买?开辩论',
  '🐂🐻 Asiimov 三轮多空对决',
];

// 导出到全局
window.CSVestData = {
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
  DEFAULT_INVENTORY,
  generateInventoryValueHistory,
  calculateRiskMetrics,
  AI_PRESET_RESPONSES,
  SUGGESTED_QUESTIONS,
  DEBATE_SUGGESTED_QUESTIONS,
  HYBRID_ROUTE: MODEL_COMPARISON.hybridRoute,
};
// Legacy alias (pre-rebrand)
window.SkinVisionData = window.CSVestData;
