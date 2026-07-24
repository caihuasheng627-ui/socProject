// ============================================
// CSVest - 前端 API 客户端
// 封装 fetch + 错误处理 + Mock 回退
// ============================================

function isLocalHostname(hostname) {
  return !hostname || hostname === 'localhost' || hostname === '127.0.0.1';
}

function isRemotePage() {
  return typeof location !== 'undefined' && !isLocalHostname(location.hostname);
}

/** GitHub/GitLab Pages 等纯静态托管：没有 nginx /api 反代。 */
function isStaticPagesHost(hostname) {
  const h = (hostname || (typeof location !== 'undefined' ? location.hostname : '') || '').toLowerCase();
  return h.endsWith('github.io') || h.endsWith('gitlab.io') || h.endsWith('pages.dev');
}

/** 已保存的公网 API（非 localhost）。HTTPS Pages 只能连 HTTPS 后端。 */
function savedPublicApiBase() {
  const saved = localStorage.getItem('sv_api_url');
  if (!saved) return '';
  try {
    const u = new URL(saved, typeof location !== 'undefined' ? location.href : undefined);
    if (isLocalHostname(u.hostname)) {
      localStorage.removeItem('sv_api_url');
      return '';
    }
    return saved.replace(/\/$/, '');
  } catch (_) {
    localStorage.removeItem('sv_api_url');
    return '';
  }
}

/** 解析 API 根地址。
 * - 本地：默认 http://localhost:8000
 * - 静态 Pages：仅用显式配置的公网 API；否则空（走 Mock，勿打同源 /api）
 * - 其它公网页（Docker/nginx）：默认同源空串，走 /api 反代
 */
function defaultApiBaseURL() {
  const saved = localStorage.getItem('sv_api_url');
  if (isRemotePage()) {
    const publicApi = savedPublicApiBase();
    if (publicApi) return publicApi;
    if (isStaticPagesHost()) return '';
    // 空字符串 = 当前页面同源，走 /api → 后端（nginx 反代）
    return '';
  }
  return (saved || 'http://localhost:8000').replace(/\/$/, '');
}

class CSVestAPI {
  constructor() {
    this.baseURL = defaultApiBaseURL();
    this.token = localStorage.getItem('sv_token') || null;
    this.timeout = 30000; // 30s
    // 本地 / 静态 Pages 默认 Mock；有公网 API 或 nginx 同源部署时走真实后端
    const mockFlag = localStorage.getItem('sv_use_mock');
    if (isRemotePage()) {
      const hasPublicApi = !!savedPublicApiBase();
      if (isStaticPagesHost() && !hasPublicApi) {
        this.useMock = mockFlag === null ? true : mockFlag === 'true';
      } else {
        if (mockFlag === 'true') localStorage.removeItem('sv_use_mock');
        this.useMock = false;
      }
    } else {
      this.useMock = mockFlag === null ? true : mockFlag === 'true';
    }
    this.online = false;
    this._alerts = null;
    this._inventory = null;
    this._portfolio = null;
  }

  setBaseURL(url) {
    this.baseURL = url || '';
    if (this.baseURL) localStorage.setItem('sv_api_url', this.baseURL);
    else localStorage.removeItem('sv_api_url');
  }

  setToken(token) {
    this.token = token;
    localStorage.setItem('sv_token', token);
  }

  setUseMock(useMock) {
    this.useMock = !!useMock;
    localStorage.setItem('sv_use_mock', String(this.useMock));
  }

  async _fetch(path, options = {}) {
    const url = `${this.baseURL}${path}`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    const headers = {
      'Content-Type': 'application/json',
      ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
      ...options.headers,
    };

    try {
      const res = await fetch(url, {
        ...options,
        headers,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      if (!res.ok) {
        const error = await res.json().catch(() => ({ message: res.statusText }));
        throw new APIError(error.message || res.statusText, res.status, error.code);
      }

      // 204 / 空 body：DELETE 等无内容响应
      if (res.status === 204 || res.headers.get('content-length') === '0') {
        return { success: true };
      }
      const text = await res.text();
      if (!text) return { success: true };
      try {
        return JSON.parse(text);
      } catch {
        return { success: true, raw: text };
      }
    } catch (err) {
      clearTimeout(timeoutId);
      if (err.name === 'AbortError') {
        throw new APIError('请求超时', 408, 'TIMEOUT');
      }
      throw err;
    }
  }

  async _safeCall(apiCall, mockCall, opts = {}) {
    const fallback = opts.fallback !== false;
    if (this.useMock) {
      return mockCall();
    }
    try {
      const result = await apiCall();
      this.online = true;
      return result;
    } catch (err) {
      console.warn(`[API] ${apiCall.name || 'request'} failed${fallback ? ', fallback to mock' : ''}:`, err.message);
      this.online = false;
      if (!fallback) throw err;
      return mockCall();
    }
  }

  async health() {
    try {
      const data = await this._fetch('/api/health', { method: 'GET' });
      this.online = true;
      return data;
    } catch (err) {
      this.online = false;
      throw err;
    }
  }

  async login(username, password) {
    return this._fetch('/api/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
  }

  async me() {
    return this._fetch('/api/me');
  }

  async adminUsers() {
    return this._fetch('/api/admin/users');
  }

  async adminGetConfig() {
    return this._fetch('/api/admin/config');
  }

  async adminPutConfig(body) {
    return this._fetch('/api/admin/config', {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  async adminStatus() {
    return this._fetch('/api/admin/status');
  }

  async adminProbeLlm() {
    return this._fetch('/api/admin/probe/llm', { method: 'POST', body: '{}' });
  }

  async adminProbeEmbed() {
    return this._fetch('/api/admin/probe/embed', { method: 'POST', body: '{}' });
  }

  async getSkins(params = {}, opts = {}) {
    return this._safeCall(
      () => this._fetch(`/api/skins?${new URLSearchParams(params)}`),
      () => Promise.resolve({
        total: window.CSVestData.SKINS_POOL.length,
        items: window.CSVestData.SKINS_POOL,
      }),
      opts
    );
  }

  async getSkin(skinId) {
    return this._safeCall(
      () => this._fetch(`/api/skins/${skinId}`),
      () => {
        const skin = window.CSVestData.SKINS_POOL.find(s => s.id === skinId);
        if (!skin) throw new APIError('饰品不存在', 404, 'NOT_FOUND');
        return skin;
      }
    );
  }

  async getKLine(skinId, days = 90) {
    return this._safeCall(
      () => this._fetch(`/api/skins/${skinId}/kline?days=${days}`),
      () => {
        const skin = window.CSVestData.SKINS_POOL.find(s => s.id === skinId);
        if (!skin) throw new APIError('饰品不存在', 404, 'NOT_FOUND');
        // Mock 结构对齐后端 GET /api/skins/{id}/kline(openapi.yaml KLineResponse)
        const mock = window.CSVestData.generateKLineData(skin.price, days);
        const data = mock.kline.map(([date, open, close, low, high]) => ({
          date, open: +open, close: +close, low: +low, high: +high,
        }));
        const toMa = (arr) => arr.map(v => (v === '-' ? null : +v));
        return {
          skinId,
          interval: '1d',
          data,
          ma7: toMa(window.CSVestData.calculateMA(mock.kline, 7)),
          ma30: toMa(window.CSVestData.calculateMA(mock.kline, 30)),
          volumes: mock.volumes.map(([, volume, direction], i) => ({
            date: data[i].date, volume, direction,
          })),
        };
      }
    );
  }

  async getPlatformQuotes(skinId, opts = {}) {
    const { platforms = null, live = false } = opts;
    const qs = new URLSearchParams();
    if (platforms) qs.set('platforms', platforms);
    if (live) qs.set('live', '1');
    const suffix = qs.toString() ? `?${qs}` : '';
    return this._safeCall(
      () => this._fetch(`/api/skins/${skinId}/quotes${suffix}`),
      () => this._mockPlatformQuotes(skinId)
    );
  }

  _mockPlatformQuotes(skinId) {
    const skin = window.CSVestData.SKINS_POOL.find(s => s.id === skinId)
      || { id: skinId, name: skinId, price: 0 };
    const base = Number(skin.price) || 0;
    const factors = {
      skinport: 0.97,
      waxpeer: 0.99,
      marketcsgo: 0.98,
      csgotrader: 1.02,
      lootfarm: 1.18,
    };
    const labels = {
      skinport: 'Skinport',
      waxpeer: 'Waxpeer',
      marketcsgo: 'Market.CSGO',
      csgotrader: 'CSGOTrader',
      lootfarm: 'Loot.farm',
    };
    const quotes = Object.entries(factors).map(([platform, factor]) => {
      const price = base > 0 ? +(base * factor).toFixed(2) : null;
      return {
        platform,
        label: labels[platform],
        currency: 'USD',
        price,
        priceNative: price,
        buyPrice: price != null ? +(price * 0.97).toFixed(2) : null,
        sellPrice: price,
        volume: null,
        ok: price != null,
        error: price != null ? null : 'NO_BASE_PRICE',
        live: false,
      };
    });
    const ok = quotes.filter(q => q.ok);
    const prices = ok.map(q => q.price);
    const spread = prices.length >= 2
      ? {
          min: Math.min(...prices),
          max: Math.max(...prices),
          minPlatform: ok.find(q => q.price === Math.min(...prices)).platform,
          maxPlatform: ok.find(q => q.price === Math.max(...prices)).platform,
          spreadPct: +(((Math.max(...prices) - Math.min(...prices)) / Math.min(...prices)) * 100).toFixed(2),
        }
      : null;
    return {
      skinId,
      marketHashName: skin.name || skinId,
      basePrice: base || null,
      mode: 'mock',
      fetchedAt: new Date().toISOString(),
      quotes,
      spread,
    };
  }

  async predict(skinId, horizon = 7, models) {
    return this._safeCall(
      () => this._fetch('/api/predict', {
        method: 'POST',
        body: JSON.stringify({ skinId, horizon, models }),
      }),
      () => this._mockPredict(skinId, horizon)
    );
  }

  async getEntryRange(skinId, riskLevel = 'moderate') {
    return this._safeCall(
      () => this._fetch('/api/predict/entry-range', {
        method: 'POST',
        body: JSON.stringify({ skinId, riskLevel }),
      }),
      () => this._mockEntryRange(skinId)
    );
  }

  _mockPredict(skinId, horizon) {
    const skin = window.CSVestData.SKINS_POOL.find(s => s.id === skinId);
    if (!skin) throw new APIError('饰品不存在', 404, 'NOT_FOUND');
    // 与后端 v5 契约对齐: LSTM/GRU 系列带 dailyPrices(7 天逐日精确预测)
    const dailyPath = (totalChangePct) => {
      const out = [];
      for (let i = 1; i <= 7; i++) {
        const eased = 1 - Math.pow(1 - i / 7, 2);
        out.push(+(skin.price * (1 + (totalChangePct / 100) * eased)).toFixed(4));
      }
      return out;
    };
    return {
      skinId,
      horizon,
      currentPrice: skin.price,
      predictions: [
        { model: 'ARIMA', type: '统计', price: skin.price * 1.012, change: 1.2, confidence: 65 },
        { model: 'XGBoost', type: 'ML', price: skin.price * 1.018, change: 1.8, confidence: 78 },
        { model: 'LightGBM', type: 'ML', price: skin.price * 1.016, change: 1.6, confidence: 76 },
        { model: 'RandomForest', type: 'ML', price: skin.price * 1.014, change: 1.4, confidence: 72 },
        { model: 'LSTM', type: 'DL', price: skin.price * 1.025, change: 2.5, confidence: 82, dailyPrices: dailyPath(2.5) },
        { model: 'GRU', type: 'DL', price: skin.price * 1.022, change: 2.2, confidence: 80, dailyPrices: dailyPath(2.2) },
      ],
      consensus: { score: 76, level: 'high' },
      entryRange: { low: skin.price * 0.97, high: skin.price * 0.99 },
      targetPrice: skin.price * 1.05,
      generatedAt: new Date().toISOString(),
    };
  }

  _mockEntryRange(skinId) {
    const skin = window.CSVestData.SKINS_POOL.find(s => s.id === skinId);
    if (!skin) throw new APIError('饰品不存在', 404, 'NOT_FOUND');
    return {
      entryLow: skin.price * 0.97,
      entryHigh: skin.price * 0.99,
      stopLoss: skin.price * 0.92,
      target7d: skin.price * 1.05,
      target30d: skin.price * 1.12,
    };
  }

  async getExplanation(skinId, days = 7) {
    return this._safeCall(
      () => this._fetch(`/api/explain/${skinId}?days=${days}`),
      () => ({
        skinId,
        summary: '该饰品近期价格变动主要受 Valve 更新和赛事经济影响。',
        relatedNews: window.CSVestData.NEWS_FEED.slice(0, 3),
        sources: ['Valve 官方', 'HLTV', 'BUFF 公告'],
      })
    );
  }

  async chat(message, sessionId, onChunk) {
    if (this.useMock) {
      return this._mockChatStream(message, onChunk);
    }
    try {
      const response = await fetch(`${this.baseURL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        },
        body: JSON.stringify({ message, sessionId }),
      });

      if (!response.ok) throw new APIError('对话请求失败', response.status);
      if (!response.body) throw new APIError('流式响应不可用', 500);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n');
        buffer = parts.pop() || '';
        for (const line of parts) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.chunk && onChunk) onChunk(data.chunk);
          } catch (_) { /* ignore partial */ }
        }
      }
      this.online = true;
    } catch (err) {
      console.warn('[API] chat stream failed, fallback to mock:', err.message);
      this.online = false;
      return this._mockChatStream(message, onChunk);
    }
  }

  async _mockChatStream(message, onChunk) {
    const response = window.CSVestData.AI_PRESET_RESPONSES['default']
      || '抱歉，当前为离线演示模式。请启动后端并关闭 Mock 后重试。';
    const chunks = response.split(/(?<=[。！？!?\n])/);
    for (const chunk of chunks) {
      if (onChunk) onChunk(chunk);
      await new Promise(r => setTimeout(r, 24));
    }
  }

  async debate(skinId, mode = 'bull_bear') {
    return this._safeCall(
      () => this._fetch(`/api/debate/${skinId}?mode=${mode}`, { method: 'POST' }),
      () => null
    );
  }

  async getNews(params = {}) {
    return this._safeCall(
      () => this._fetch(`/api/news?${new URLSearchParams(params)}`),
      () => window.CSVestData.NEWS_FEED
    );
  }

  async getDailyReport(date) {
    return this._safeCall(
      () => this._fetch(`/api/daily-report?date=${date || ''}`),
      () => ({
        date: date || new Date().toISOString().slice(0, 10),
        generatedAt: new Date().toISOString(),
        metrics: { monitored: 20, gainers: 14, losers: 6 },
        hotVolume: window.CSVestData.HOT_VOLUME,
        aiSummary: '今日 CS2 饰品市场整体偏强震荡,大盘上涨面占优 [1];临近 Major 赛程,热门贴纸与皮肤成交活跃 [2]。建议关注成交量放大标的,注意赛事结束后的回调风险。',
        sources: this._mockRagSources(),
        news: window.CSVestData.NEWS_FEED,
      })
    );
  }

  _mockRagSources() {
    const news = (window.CSVestData.NEWS_FEED || []).slice(0, 4);
    const kb = [
      { type: 'kb', title: 'CS2 市场知识库', source: '内置知识库', snippet: 'Major 赛事前后 7-14 天,相关贴纸与饰品成交量通常上升 15-30%,但赛事结束后有回调压力。', score: 3, relevance: 1 },
      { type: 'kb', title: 'CS2 市场知识库', source: '内置知识库', snippet: '高价值低流动性饰品(刀/手套)日内波动大,买卖价差宽,不适合大额短线。', score: 2, relevance: 0.67 },
    ];
    const newsSrc = news.map((n, i) => ({
      type: 'news',
      title: n.title,
      snippet: n.summary || n.title,
      source: n.source || 'RAG 知识库',
      date: n.time || n.published_at || null,
      sentiment: n.sentiment,
      url: n.url || null,
      score: Math.max(1, 2 - i * 0.5),
      relevance: Math.max(0.2, 0.9 - i * 0.2),
    }));
    return [...kb, ...newsSrc].map((s, i) => ({ ...s, id: i + 1 }));
  }

  async ragAsk(query, topK = 5) {
    return this._safeCall(
      () => this._fetch('/api/rag/ask', {
        method: 'POST',
        body: JSON.stringify({ query, topK }),
      }),
      () => {
        const sources = this._mockRagSources().slice(0, topK);
        return {
          query,
          answer: `(演示模式) 根据向量检索到的知识库与资讯,针对「${query}」的分析:相关饰品近期受 Major 赛程与 Valve 更新预期影响,成交量与价格波动加大 [1][2];建议结合成交量与磨损等级判断入场时机。⚠ 饰品市场高波动,以上不构成投资建议。`,
          sources,
          retrieval: { mode: 'vector', provider: 'dashscope', model: 'text-embedding-v3' },
        };
      }
    );
  }

  _mockAlerts() {
    if (this._alerts) return this._alerts;
    this._alerts = [
      { id: 1, skinId: 'ak47-fireserpent-fn', skinName: 'AK-47 | Fire Serpent (FN)', type: 'above', targetPrice: 2291.67, currentPrice: 2201.39, active: true, triggered: false, createdAt: '2026-07-10 14:23' },
      { id: 2, skinId: 'awp-dragonlore-ft', skinName: 'AWP | Dragon Lore (FT)', type: 'above', targetPrice: 6250.00, currentPrice: 5942.78, active: true, triggered: true, createdAt: '2026-07-08 09:15' },
    ];
    return this._alerts;
  }

  async getAlerts() {
    return this._safeCall(
      () => this._fetch('/api/alerts'),
      () => this._mockAlerts()
    );
  }

  async createAlert(data) {
    return this._safeCall(
      () => this._fetch('/api/alerts', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
      () => {
        const skin = window.CSVestData.SKINS_POOL.find(s => s.id === data.skinId);
        const newAlert = {
          id: Date.now(),
          ...data,
          skinName: skin?.name || '',
          currentPrice: skin?.price || 0,
          active: true,
          triggered: false,
          createdAt: new Date().toISOString(),
        };
        this._alerts = [...this._mockAlerts(), newAlert];
        return newAlert;
      }
    );
  }

  async deleteAlert(id) {
    return this._safeCall(
      () => this._fetch(`/api/alerts/${id}`, { method: 'DELETE' }),
      () => {
        this._alerts = this._mockAlerts().filter(a => a.id !== id);
        return { success: true };
      }
    );
  }

  _mockPortfolio() {
    if (this._portfolio) return this._portfolio;
    this._portfolio = (window.CSVestData.DEFAULT_PORTFOLIO || []).map(p => ({ ...p }));
    return this._portfolio;
  }

  async getPortfolio() {
    return this._safeCall(
      () => this._fetch('/api/portfolio'),
      () => ({ total: 0, items: this._mockPortfolio() })
    );
  }

  async addPortfolioItem(data) {
    const payload = {
      skinId: data.skinId,
      buyPrice: data.buyPrice,
      buyDate: data.buyDate,
      quantity: data.quantity || 1,
      holdingType: data.holdingType || 'real',
    };
    return this._safeCall(
      () => this._fetch('/api/portfolio', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
      () => {
        const skin = window.CSVestData.SKINS_POOL.find(s => s.id === data.skinId);
        const newItem = {
          id: Date.now(),
          ...payload,
          name: skin?.name || '',
        };
        this._portfolio = [...this._mockPortfolio(), newItem];
        return newItem;
      }
    );
  }

  async deletePortfolioItem(id) {
    return this._safeCall(
      () => this._fetch(`/api/portfolio/${id}`, { method: 'DELETE' }),
      () => {
        this._portfolio = this._mockPortfolio().filter(p => p.id !== id);
        return { success: true };
      }
    );
  }

  async getPortfolioValueHistory(days = 90) {
    return this._safeCall(
      () => this._fetch(`/api/portfolio/value_history?days=${days}`),
      () => ({ dates: [], values: [], total: 0 })
    );
  }

  async diagnosePortfolio() {
    return this._safeCall(
      () => this._fetch('/api/portfolio/diagnose', { method: 'POST' }),
      () => ({
        summary: '离线演示：请连接后端以获取组合诊断。',
        valueForecast: null,
        actions: [],
        riskTop: [],
      })
    );
  }

  // ============ 我的库存（真实库存；后端待对接，先留接口 + mock）============
  _mockInventory() {
    if (this._inventory) return this._inventory;
    this._inventory = (window.CSVestData.DEFAULT_INVENTORY || []).map(p => ({ ...p }));
    return this._inventory;
  }

  /** GET /api/inventory — 获取真实库存列表 */
  async getInventory() {
    return this._safeCall(
      () => this._fetch('/api/inventory'),
      () => ({ total: this._mockInventory().length, items: this._mockInventory() })
    );
  }

  /** POST /api/inventory — 手动添加库存饰品 */
  async addInventoryItem(data) {
    const payload = {
      skinId: data.skinId,
      acquirePrice: data.acquirePrice,
      acquireDate: data.acquireDate,
      quantity: data.quantity || 1,
      source: data.source || 'manual',
    };
    return this._safeCall(
      () => this._fetch('/api/inventory', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
      () => {
        const skin = window.CSVestData.SKINS_POOL.find(s => s.id === data.skinId);
        const newItem = {
          id: Date.now(),
          ...payload,
          name: skin?.name || '',
        };
        this._inventory = [...this._mockInventory(), newItem];
        return newItem;
      }
    );
  }

  /** DELETE /api/inventory/{id} — 移除库存饰品 */
  async deleteInventoryItem(id) {
    return this._safeCall(
      () => this._fetch(`/api/inventory/${id}`, { method: 'DELETE' }),
      () => {
        this._inventory = this._mockInventory().filter(p => p.id !== id);
        return { success: true };
      }
    );
  }

  /** GET /api/inventory/value_history — 库存总价值走势 */
  async getInventoryValueHistory(days = 90) {
    return this._safeCall(
      () => this._fetch(`/api/inventory/value_history?days=${days}`),
      () => {
        const gen = window.CSVestData.generateInventoryValueHistory;
        return gen
          ? gen(this._mockInventory(), days)
          : { dates: [], values: [], total: 0 };
      }
    );
  }

  /**
   * POST /api/inventory/steam/import — Steam 库存导入（待开发）
   * 后端对接后：拉取 Steam inventory → 映射 market_hash_name → 写入 inventory
   */
  async importSteamInventory(payload = {}) {
    return this._safeCall(
      () => this._fetch('/api/inventory/steam/import', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
      () => {
        const err = new Error('Steam 导入需连接后端,当前为演示模式');
        err.code = 'STEAM_IMPORT_PENDING';
        throw err;
      },
      { fallback: false }   // 真实错误(403私有/429限流/404空)必须上抛,不能被 mock 兜底掩盖
    );
  }

  async getModelComparison() {
    return this._safeCall(
      () => this._fetch('/api/models/comparison'),
      () => window.CSVestData.MODEL_COMPARISON
    );
  }

  /** 将资金曲线统一为「整段起点=100」的净值指数 */
  _reindexSeries(series) {
    const out = {};
    for (const [name, arr] of Object.entries(series || {})) {
      if (!Array.isArray(arr) || !arr.length) {
        out[name] = arr;
        continue;
      }
      const base = arr.find((v) => v != null && Number(v) !== 0);
      const b = (base == null || Number(base) === 0) ? 1 : Number(base);
      out[name] = arr.map((v) => (v == null ? null : +((Number(v) / b) * 100).toFixed(2)));
    }
    return out;
  }

  /** 将新旧回测 JSON 统一成 { dates, series: { name: number[] } } */
  _normalizeBacktest(raw, days = 60) {
    if (!raw || typeof raw !== 'object') {
      return {
        dates: Array.from({ length: days }, (_, i) => {
          const d = new Date(Date.now() - (days - i) * 86400000);
          return `${d.getMonth() + 1}/${d.getDate()}`;
        }),
        series: window.CSVestData.generateBacktestData(days),
      };
    }
    if (raw.dates && raw.series) {
      const first = Object.values(raw.series)[0];
      if (Array.isArray(first) && (typeof first[0] === 'number' || first[0] == null)) {
        // 后端已按全程起点归一(indexBase=full_start)时勿再缩放；
        // 旧接口若仅 indexed、却是「窗口首点=100」，再按 days 截取即可。
        let series = raw.series;
        if (!raw.indexed) {
          series = this._reindexSeries(raw.series);
        } else if (raw.indexBase !== 'full_start' && !raw._windowReindexed) {
          // 兼容旧后端：已是窗口归一，保持原样
          series = raw.series;
        }
        let dates = raw.dates;
        let seriesOut = series;
        if (days > 0 && dates.length > days) {
          dates = dates.slice(-days);
          seriesOut = Object.fromEntries(
            Object.entries(series).map(([k, v]) => [k, Array.isArray(v) ? v.slice(-days) : v])
          );
        }
        return {
          dates,
          series: seriesOut,
          indexed: true,
          indexBase: raw.indexBase || 'full_start',
          note: raw.note,
        };
      }
    }
    // 新格式: fee_0.0000.{model}: [{date, capital}, ...]
    const feeKey = Object.keys(raw).find(k => k.startsWith('fee_')) || null;
    const block = feeKey ? raw[feeKey] : null;
    if (block && typeof block === 'object') {
      const modelNames = Object.keys(block);
      const anchor = block[modelNames[0]] || [];
      const datesAll = anchor.map(p => {
        const d = String(p.date || '');
        if (/^\d{4}-\d{2}-\d{2}/.test(d)) {
          const [, m, day] = d.split(/[-T]/);
          return `${+m}/${+day}`;
        }
        return d;
      });
      // 先按整段起点归一，再截最近 N 天
      const seriesFull = {};
      for (const [name, pts] of Object.entries(block)) {
        const caps = pts.map(p => Number(p.capital) || 0);
        const base = caps.find((v) => v) || 1;
        seriesFull[name] = caps.map(v => +((v / base) * 100).toFixed(2));
      }
      if (Array.isArray(raw.buy_hold) && raw.buy_hold.length) {
        const caps = raw.buy_hold.map(p => Number(p.capital) || 0);
        const base = caps.find((v) => v) || 1;
        seriesFull['Buy&Hold'] = caps.map(v => +((v / base) * 100).toFixed(2));
      }
      let datesOut = datesAll;
      let seriesOut = seriesFull;
      if (days > 0 && datesOut.length > days) {
        datesOut = datesOut.slice(-days);
        seriesOut = Object.fromEntries(
          Object.entries(seriesFull).map(([k, v]) => [k, v.slice(-days)])
        );
      }
      return {
        dates: datesOut,
        series: seriesOut,
        fee: feeKey,
        indexed: true,
        indexBase: 'full_start',
        note: '净值以整段回测起点=100；策略含现金仓位，波动通常小于满仓 Buy&Hold。',
      };
    }
    return {
      dates: Array.from({ length: days }, (_, i) => {
        const d = new Date(Date.now() - (days - i) * 86400000);
        return `${d.getMonth() + 1}/${d.getDate()}`;
      }),
      series: window.CSVestData.generateBacktestData(days),
    };
  }

  async getBacktest(days = 60) {
    return this._safeCall(
      async () => {
        const raw = await this._fetch(`/api/models/backtest?days=${days}`);
        return this._normalizeBacktest(raw, days);
      },
      () => this._normalizeBacktest(null, days)
    );
  }

  async getShap(model = 'xgboost') {
    return this._safeCall(
      () => this._fetch(`/api/models/shap?model=${encodeURIComponent(model)}`),
      () => (window.CSVestData.SHAP_FEATURES || []).map(d => ({
        feature: d.name,
        importance: d.value,
      }))
    );
  }
}

class APIError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'APIError';
    this.status = status;
    this.code = code;
  }
}

window.CSVestAPI = new CSVestAPI();
window.SkinVisionAPI = window.CSVestAPI;
