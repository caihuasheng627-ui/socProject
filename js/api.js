// ============================================
// CSVest - 前端 API 客户端
// 封装 fetch + 错误处理 + Mock 回退
// ============================================

class CSVestAPI {
  constructor() {
    const queryAPI = new URLSearchParams(window.location.search).get('api_url');
    this.baseURL = queryAPI || localStorage.getItem('sv_api_url') || 'http://localhost:8000';
    this.token = localStorage.getItem('sv_token') || null;
    this.timeout = 30000;
    // 未设置时默认 mock；显式 'false' 才走真实后端
    const mockFlag = localStorage.getItem('sv_use_mock');
    this.useMock = mockFlag === null ? true : mockFlag === 'true';
    this.online = false;
    this._alerts = null;
    this._portfolio = null;
  }

  setBaseURL(url) {
    this.baseURL = url;
    localStorage.setItem('sv_api_url', url);
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

  async _safeCall(apiCall, mockCall) {
    if (this.useMock) {
      return mockCall();
    }
    try {
      const result = await apiCall();
      this.online = true;
      return result;
    } catch (err) {
      console.warn(`[API] ${apiCall.name || 'request'} failed, fallback to mock:`, err.message);
      this.online = false;
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

  async getSkins(params = {}) {
    return this._safeCall(
      () => this._fetch(`/api/skins?${new URLSearchParams(params)}`),
      () => Promise.resolve({
        total: window.CSVestData.SKINS_POOL.length,
        items: window.CSVestData.SKINS_POOL,
      })
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
    return {
      skinId,
      horizon,
      currentPrice: skin.price,
      predictions: [
        { model: 'ARIMA', type: '统计', price: skin.price * 1.012, change: 1.2, confidence: 65 },
        { model: 'XGBoost', type: 'ML', price: skin.price * 1.018, change: 1.8, confidence: 78 },
        { model: 'LightGBM', type: 'ML', price: skin.price * 1.016, change: 1.6, confidence: 76 },
        { model: 'RandomForest', type: 'ML', price: skin.price * 1.014, change: 1.4, confidence: 72 },
        { model: 'LSTM', type: 'DL', price: skin.price * 1.025, change: 2.5, confidence: 82 },
        { model: 'GRU', type: 'DL', price: skin.price * 1.022, change: 2.2, confidence: 80 },
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

  async chat(message, sessionId, onChunk, locale = null) {
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
        body: JSON.stringify({
          message,
          sessionId,
          locale: locale || localStorage.getItem('sv_lang') || 'zh-CN',
        }),
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

  async orchestrateAI(payload) {
    if (this.useMock) {
      return this._mockOrchestrate(payload, { mode: 'mock', reason: 'manual_mock' });
    }
    try {
      const result = await this._fetch('/api/ai/orchestrate', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      this.online = true;
      return result;
    } catch (err) {
      console.warn('[API] orchestrate request failed, using explicit fallback:', err.message);
      this.online = false;
      return this._mockOrchestrate(payload, {
        mode: 'degraded',
        reason: 'request_failed',
        errorType: err?.name || 'APIError',
      });
    }
  }

  async createAgentSession(payload) {
    return this._fetch('/api/agent/sessions', {
      method: 'POST',
      body: JSON.stringify({
        ...payload,
        locale: payload.locale || localStorage.getItem('sv_lang') || 'zh-CN',
      }),
    });
  }

  async getAgentSession(sessionId) {
    return this._fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}`);
  }

  async sendAgentMessage(sessionId, message, targetAgent) {
    return this._fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/message`, {
      method: 'POST',
      body: JSON.stringify({
        message,
        targetAgent,
        locale: localStorage.getItem('sv_lang') || 'zh-CN',
      }),
    });
  }

  async runAgentRound(sessionId, message) {
    return this._fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/round`, {
      method: 'POST',
      body: JSON.stringify({
        message,
        locale: localStorage.getItem('sv_lang') || 'zh-CN',
      }),
    });
  }

  _mockOrchestrate(payload, fallback = {}) {
    const message = String(payload.message || '');
    const action = payload.action || 'auto';
    const english = payload.locale === 'en-US';
    const fallbackMode = fallback.mode || 'mock';
    const runtime = {
      llm: {
        mode: fallbackMode,
        configured: false,
        provider: 'Local',
        model: 'browser-fallback',
        lastError: fallback.errorType || null,
      },
      agents: {
        mode: fallbackMode,
        bullModel: 'local-fallback',
        bearModel: 'local-fallback',
        judgeModel: 'local-fallback',
      },
      hybrid: { mode: 'mock', model: 'browser-trend-fallback' },
    };
    const pool = window.CSVestData.SKINS_POOL || [];
    const skin = pool.find(item => item.id === payload.skinId);
    const wantsRecommendation = action === 'recommend'
      || /\u63a8\u8350|recommend|suggest/i.test(message);

    if (wantsRecommendation) {
      const budget = Number(payload.budget) || Infinity;
      const recommendations = pool
        .filter(item => Number(item.price) <= budget)
        .sort((a, b) => (b.liquidity || 0) - (a.liquidity || 0))
        .slice(0, 5)
        .map(item => ({
          skinId: item.id,
          name: item.name,
          category: item.category,
          price: item.price,
          change7d: item.change7d || 0,
          liquidity: item.liquidity || 0,
          risk: 'medium',
          score: Math.min(99, 60 + (item.liquidity || 0) * 0.3),
          reasons: english
            ? ['Offline demo ranking', 'Start the backend for evidence-based results']
            : ['离线演示排序', '启动后端后将使用真实市场证据'],
        }));
      return Promise.resolve({
        type: 'recommendation',
        message: english
          ? 'This is an offline demo ranking. Start the backend for evidence-based recommendations.'
          : '\u5f53\u524d\u662f\u79bb\u7ebf\u6f14\u793a\u63a8\u8350\uff1b\u542f\u52a8\u540e\u7aef\u540e\u5c06\u4f7f\u7528\u771f\u5b9e\u5e02\u573a\u8bc1\u636e\u6392\u5e8f\u3002',
        recommendations,
        runtime,
        fallbackReason: fallback.reason || 'manual_mock',
      });
    }

    if (action === 'predict' && skin) {
      return Promise.resolve({
        type: 'prediction',
        message: english
          ? 'An offline demo forecast has been generated.'
          : '\u5df2\u751f\u6210\u79bb\u7ebf\u6f14\u793a\u9884\u6d4b\u3002',
        skin: { skinId: skin.id, name: skin.name, price: skin.price },
        prediction: this._mockPredict(skin.id, payload.horizonDays || 7),
        runtime,
        fallbackReason: fallback.reason || 'manual_mock',
      });
    }

    return Promise.resolve({
      type: 'chat',
      message: english
        ? (fallback.reason === 'request_failed'
          ? 'The live backend request failed, so this reply used the browser fallback. Retry after checking the backend log; no DeepSeek result was produced for this message.'
          : 'The app is using the offline demo assistant. Start the backend for live AI output.')
        : (fallback.reason === 'request_failed'
          ? '本次后端实时请求失败，因此明确使用了浏览器降级回复。请检查后端日志后重试；这条消息没有产生 DeepSeek 结果。'
          : (window.CSVestData.AI_PRESET_RESPONSES.default
            || '\u5f53\u524d\u4e3a\u79bb\u7ebf\u6f14\u793a\u6a21\u5f0f\u3002')),
      runtime,
      fallbackReason: fallback.reason || 'manual_mock',
    });
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
    const locale = localStorage.getItem('sv_lang') || 'zh-CN';
    return this._safeCall(
      () => this._fetch(`/api/debate/${skinId}?mode=${mode}&locale=${encodeURIComponent(locale)}`, { method: 'POST' }),
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
        metrics: { monitored: 20, gainers: 14, losers: 6 },
        hotVolume: window.CSVestData.HOT_VOLUME,
        aiSummary: '今日 CS2 饰品市场整体偏强震荡...',
        news: window.CSVestData.NEWS_FEED,
      })
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

  async getModelComparison() {
    return this._safeCall(
      () => this._fetch('/api/models/comparison'),
      () => window.CSVestData.MODEL_COMPARISON
    );
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
    if (raw.dates && raw.series && !Array.isArray(Object.values(raw.series)[0]?.[0])) {
      // 已是前端格式，或 series 值为数字数组
      const first = Object.values(raw.series)[0];
      if (Array.isArray(first) && (typeof first[0] === 'number' || first[0] == null)) {
        return raw;
      }
    }
    // 新格式: fee_0.0000.{model}: [{date, capital}, ...]
    const feeKey = Object.keys(raw).find(k => k.startsWith('fee_')) || null;
    const block = feeKey ? raw[feeKey] : null;
    if (block && typeof block === 'object') {
      const modelNames = Object.keys(block);
      const anchor = block[modelNames[0]] || [];
      const dates = anchor.map(p => {
        const d = String(p.date || '');
        if (/^\d{4}-\d{2}-\d{2}/.test(d)) {
          const [, m, day] = d.split(/[-T]/);
          return `${+m}/${+day}`;
        }
        return d;
      });
      const series = {};
      for (const [name, pts] of Object.entries(block)) {
        const base = pts[0]?.capital || 10000;
        series[name] = pts.map(p => +((p.capital / base) * 100).toFixed(2));
      }
      if (Array.isArray(raw.buy_hold) && raw.buy_hold.length) {
        const bh = raw.buy_hold;
        const base = bh[0]?.capital || 10000;
        series['买入持有'] = bh.map(p => +((p.capital / base) * 100).toFixed(2));
      }
      return { dates, series, fee: feeKey };
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
