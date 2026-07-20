// ============================================
// CSVest - 前端 API 客户端
// 封装 fetch + 错误处理 + Mock 回退
// ============================================

class CSVestAPI {
  constructor() {
    // 优先使用 localStorage 配置的 baseURL,默认本地
    this.baseURL = localStorage.getItem('sv_api_url') || 'http://localhost:8000';
    this.token = localStorage.getItem('sv_token') || null;
    this.timeout = 30000; // 30s
    // 未设置时默认 mock；显式设为 'false' 才走真实后端
    // 旧写法 `=== 'true' || true` 恒为 true，导致永远连不上后端
    const mockFlag = localStorage.getItem('sv_use_mock');
    this.useMock = mockFlag === null ? true : mockFlag === 'true';
    this.online = false;
  }

  // ============ 配置 ============
  setBaseURL(url) {
    this.baseURL = url;
    localStorage.setItem('sv_api_url', url);
  }

  setToken(token) {
    this.token = token;
    localStorage.setItem('sv_token', token);
  }

  setUseMock(useMock) {
    this.useMock = useMock;
    localStorage.setItem('sv_use_mock', String(useMock));
  }

  // ============ 核心 fetch 封装 ============
  async _fetch(path, options = {}) {
    const url = `${this.baseURL}${path}`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    const headers = {
      'Content-Type': 'application/json',
      ...(this.token ? { 'Authorization': `Bearer ${this.token}` } : {}),
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

      return await res.json();
    } catch (err) {
      clearTimeout(timeoutId);
      if (err.name === 'AbortError') {
        throw new APIError('请求超时', 408, 'TIMEOUT');
      }
      throw err;
    }
  }

  async _safeCall(apiCall, mockCall) {
    // 强制 mock 时直接使用 mock
    if (this.useMock) {
      return mockCall();
    }
    try {
      const result = await apiCall();
      this.online = true;
      return result;
    } catch (err) {
      console.warn(`[API] ${apiCall.name} failed, fallback to mock:`, err.message);
      this.online = false;
      return mockCall();
    }
  }

  // ============ 健康检查 ============
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

  // ============ 行情数据 ============
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
        return window.CSVestData.generateKLineData(skin.price, days);
      }
    );
  }

  // ============ AI 预测 ============
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

  // ============ RAG 解释 ============
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

  // ============ AI 对话(流式) ============
  async chat(message, sessionId, onChunk) {
    if (this.useMock) {
      // Mock 流式输出
      return this._mockChatStream(message, onChunk);
    }
    try {
      const response = await fetch(`${this.baseURL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(this.token ? { 'Authorization': `Bearer ${this.token}` } : {}),
        },
        body: JSON.stringify({ message, sessionId }),
      });

      if (!response.ok) throw new APIError('对话请求失败', response.status);
      if (!response.body) throw new APIError('流式响应不可用', 500);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n').filter(l => l.startsWith('data: '));
        for (const line of lines) {
          const data = JSON.parse(line.slice(6));
          if (data.chunk && onChunk) onChunk(data.chunk);
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
    const response = window.CSVestData.AI_PRESET_RESPONSES['default'];
    const chunks = response.split(/(?<=[。!?\n])/);
    for (const chunk of chunks) {
      if (onChunk) onChunk(chunk);
      await new Promise(r => setTimeout(r, 30));
    }
  }

  // ============ 双 Agent 辩论 ============
  async debate(skinId, mode = 'bull_bear', onProgress) {
    return this._safeCall(
      async () => this._fetch(`/api/debate/${skinId}?mode=${mode}`, { method: 'POST' }),
      () => null
    );
  }

  // ============ 资讯流 ============
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
        date: date || '2026-07-15',
        metrics: { monitored: 20, gainers: 14, losers: 6 },
        hotVolume: window.CSVestData.HOT_VOLUME,
        aiSummary: '今日 CS2 饰品市场整体偏强震荡...',
        news: window.CSVestData.NEWS_FEED,
      })
    );
  }

  // ============ 预警 ============
  async getAlerts() {
    return this._safeCall(
      () => this._fetch('/api/alerts'),
      () => this._alerts
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
        this._alerts = [...(this._alerts || []), newAlert];
        return newAlert;
      }
    );
  }

  async deleteAlert(id) {
    return this._safeCall(
      () => this._fetch(`/api/alerts/${id}`, { method: 'DELETE' }),
      () => {
        this._alerts = (this._alerts || []).filter(a => a.id !== id);
        return { success: true };
      }
    );
  }

  // ============ 持仓 ============
  async getPortfolio() {
    return this._safeCall(
      () => this._fetch('/api/portfolio'),
      () => this._portfolio
    );
  }

  async addPortfolioItem(data) {
    return this._safeCall(
      () => this._fetch('/api/portfolio', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
      () => {
        const skin = window.CSVestData.SKINS_POOL.find(s => s.id === data.skinId);
        const newItem = {
          id: Date.now(),
          ...data,
          name: skin?.name || '',
        };
        this._portfolio = [...(this._portfolio || []), newItem];
        return newItem;
      }
    );
  }

  async deletePortfolioItem(id) {
    return this._safeCall(
      () => this._fetch(`/api/portfolio/${id}`, { method: 'DELETE' }),
      () => {
        this._portfolio = (this._portfolio || []).filter(p => p.id !== id);
        return { success: true };
      }
    );
  }

  // ============ 模型对比 ============
  async getModelComparison() {
    return this._safeCall(
      () => this._fetch('/api/models/comparison'),
      () => window.CSVestData.MODEL_COMPARISON
    );
  }

  async getBacktest(days = 60) {
    return this._safeCall(
      () => this._fetch(`/api/models/backtest?days=${days}`),
      () => ({
        dates: Array.from({ length: days }, (_, i) => {
          const d = new Date(Date.now() - (days - i) * 24 * 60 * 60 * 1000);
          return `${d.getMonth() + 1}/${d.getDate()}`;
        }),
        series: window.CSVestData.generateBacktestData(days),
      })
    );
  }
}

// ============ 错误类 ============
class APIError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'APIError';
    this.status = status;
    this.code = code;
  }
}

// ============ 全局单例 ============
window.CSVestAPI = new CSVestAPI();
window.SkinVisionAPI = window.CSVestAPI; // legacy alias
