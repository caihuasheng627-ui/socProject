// ============================================
// CSVest - 闁告挸绉堕?API 閻庡箍鍨洪崺娑氱博?
// 閻忓繋娴囬ˉ?fetch + 闂佹寧鐟ㄩ銈嗗緞閸曨厽鍊?+ Mock 闁搞儳鍋ら埀顑藉亾
// ============================================

function isLocalHostname(hostname) {
  return !hostname || hostname === 'localhost' || hostname === '127.0.0.1';
}

function isRemotePage() {
  return typeof location !== 'undefined' && !isLocalHostname(location.hostname);
}

/** GitHub/GitLab Pages 缂佹稑顦遍崙浠嬫濞嗘劏鍋撴担鐟邦潹缂佺姭妲勭槐鏉库柦閳╁啯绠?nginx /api 闁告瑥绉抽崬顒勫Υ?*/
function isStaticPagesHost(hostname) {
  const h = (hostname || (typeof location !== 'undefined' ? location.hostname : '') || '').toLowerCase();
  return h.endsWith('github.io') || h.endsWith('gitlab.io') || h.endsWith('pages.dev');
}

/** 鐎规瓕寮撶换姘扁偓娑欘焽濞堟垿宕楅鍌滅Ч API闁挎稑鐗撳?localhost闁挎稑顦埀顑垮緞TTPS Pages 闁告瑯浜ｉ崗妯绘交?HTTPS 闁告艾娴烽顒勫Υ?*/
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

/** 閻熸瑱绲鹃悗?API 闁哄秶鎳撳﹢鎾锤閳ь剟濡?
 * - 闁哄牜鍓欏﹢鎾晬濮樺墎甯涢悹?http://localhost:8000
 * - 闂傚牊鐟﹂埀?Pages闁挎稒鐭划搴ㄦ偨閵婏附鈻旂€殿喖绻橀崢銈囩磾椤旂偓鐣遍柛蹇ｅ墰缂?API闁挎稒绋戦幆渚€宕氬▎鎴旀晞闁挎稑鐗愰摂?Mock闁挎稑鑻€ｄ線骞嶉幘铏€辨繝?/api闁?
 * - 闁稿繗娉涢悾鐘诲礂椤掑倻绉瑰銈囶暜缁辨ocker/nginx闁挎稑顧€缁辩増顪€濡鍚囬柛姘湰缁喚绮氭潪鎷岊洬闁挎稑鐭侀摂?/api 闁告瑥绉抽崬?
 */
function defaultApiBaseURL() {
  const saved = localStorage.getItem('sv_api_url');
  if (isRemotePage()) {
    const publicApi = savedPublicApiBase();
    if (publicApi) return publicApi;
    if (isStaticPagesHost()) return '';
    // 缂佸苯鎼悺褏绮敂鑳洬 = 鐟滅増鎸告晶鐘炽亜閻㈠憡妗ㄩ柛姘湰缁噣鏁嶅畝鍐巢 /api 闁?闁告艾娴烽顒勬晬閸ь柇inx 闁告瑥绉抽崬顒勬晬?
    return '';
  }
  return (saved || 'http://localhost:8000').replace(/\/$/, '');
}

class CSVestAPI {
  constructor() {
    this.baseURL = defaultApiBaseURL();
    this.token = localStorage.getItem('sv_token') || null;
    this.timeout = 30000; // 30s
    // 闁哄牜鍓欏﹢?/ 闂傚牊鐟﹂埀?Pages 濮掓稒顭堥?Mock闁挎稒绋掑﹢渚€宕楅鍌滅Ч API 闁?nginx 闁告艾鏈花顕€鏌堥妸褑顔夐柡鍐╁劶閾斿鎯囬悢椋庢澖闁告艾娴烽?
    // AI advice must never be silently substituted with browser-generated
    // content. Earlier builds persisted `sv_use_mock=true`, which made a
    // real Agent Debate look successful while it never reached the backend.
    localStorage.removeItem('sv_use_mock');
    this.useMock = false;
    this.online = false;
    this._alerts = null;
    this._inventory = null;
    this._portfolio = null;
    this._agentSessions = new Map();
  }

  setBaseURL(url) {
    this.baseURL = url || '';
    if (this.baseURL) localStorage.setItem('sv_api_url', this.baseURL);
    else localStorage.removeItem('sv_api_url');
  }

  setToken(token) {
    this.token = token || null;
    if (token) localStorage.setItem('sv_token', token);
    else localStorage.removeItem('sv_token');
  }

  clearToken() {
    this.token = null;
    localStorage.removeItem('sv_token');
  }

  setUseMock(useMock) {
    // Compatibility for old settings panels: decisions either use the live
    // backend or visibly fail; browser-generated analysis is not permitted.
    if (useMock) console.warn('[API] Mock mode is disabled for this build.');
    this.useMock = false;
    localStorage.removeItem('sv_use_mock');
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
        let detail = error.detail ?? error.message ?? res.statusText;
        if (Array.isArray(detail)) {
          detail = detail.map((d) => d?.msg || d?.message || String(d)).filter(Boolean).join('; ');
        } else if (detail && typeof detail === 'object') {
          detail = detail.message || detail.msg || JSON.stringify(detail);
        }
        throw new APIError(detail || res.statusText, res.status, error.code);
      }

      // 204 / 缂?body闁挎稒顑廍LETE 缂佹稑顦板Λ銈夊礃閸涱収鍟囬柛婵嗙Т缁?
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
        /* Corrupted legacy localized literal kept below for source-history
           compatibility; do not execute it. */
        throw new APIError('Request timed out.', 408, 'TIMEOUT');
        /*
        throw new APIError('閻犲洭鏀遍惇鎵惥閸涱喗顦?, 408, 'TIMEOUT');
        */
      }
      throw err;
    }
  }

  async _safeCall(apiCall, mockCall, opts = {}) {
    try {
      const result = await apiCall();
      this.online = true;
      return result;
    } catch (err) {
      console.warn(`[API] ${apiCall.name || 'request'} failed:`, err.message);
      this.online = false;
      // Never replace unavailable market/AI data with a browser-generated
      // value. Callers must render their explicit unavailable/empty states.
      throw err;
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

  async register(username, password) {
    return this._fetch('/api/register', {
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
        if (!skin) throw new APIError('Skin not found.', 404, 'NOT_FOUND');
        /* Legacy malformed localized literal:
        if (!skin) throw new APIError('濡ゆ婢橀幖褎绋夊鍛憼闁?, 404, 'NOT_FOUND');
        */
        return skin;
      }
    );
  }

  async getKLine(skinId, days = 90) {
    return this._safeCall(
      () => this._fetch(`/api/skins/${skinId}/kline?days=${days}`),
      () => {
        const skin = window.CSVestData.SKINS_POOL.find(s => s.id === skinId);
        if (!skin) throw new APIError('Skin not found.', 404, 'NOT_FOUND');
        /* Legacy malformed localized literal:
        if (!skin) throw new APIError('濡ゆ婢橀幖褎绋夊鍛憼闁?, 404, 'NOT_FOUND');
        // Mock 缂備焦鎸婚悗顖溾偓闈涚秺缂嶅牓宕ユ惔锝庝紓 GET /api/skins/{id}/kline(openapi.yaml KLineResponse)
        */
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
    return this._fetch('/api/predict', {
      method: 'POST',
      body: JSON.stringify({ skinId, horizon, models }),
    });
  }

  async getEntryRange(skinId, riskLevel = 'moderate') {
    return this._fetch('/api/predict/entry-range', {
      method: 'POST',
      body: JSON.stringify({ skinId, riskLevel }),
    });
  }

  async getExplanation(skinId, days = 7) {
    return this._fetch(`/api/explain/${encodeURIComponent(skinId)}?days=${days}`);
  }

  /* Browser-side mock implementations are deliberately disabled. They used
     invented market data whenever a network request failed. Keep them only as
     commented source history until the next cleanup commit. */
  /* Disabled mock code (never used by the application):
  _mockPredict(skinId, horizon) {
    const skin = window.CSVestData.SKINS_POOL.find(s => s.id === skinId);
    if (!skin) throw new APIError('Skin not found.', 404, 'NOT_FOUND');
    /* Legacy malformed localized literal:
    if (!skin) throw new APIError('濡ゆ婢橀幖褎绋夊鍛憼闁?, 404, 'NOT_FOUND');
    // 濞戞挸楠搁幃妤冪博?v5 濠靛倹鍨圭€瑰磭鈧潧缍婄紞? LSTM/GRU 缂侇垵顕ч崹顏嗘暜?dailyPrices(7 濠㈠灈鏅犻埀顒佸姈濡晝鍒掗崜褉鈧ɑ锛愰崟顒傘偞)
    * /
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
      status: 'demo',
      reason: null,
      currentPrice: skin.price,
      predictions: [
        { model: 'ARIMA', type: '缂備胶鍠曢?, price: skin.price * 1.012, change: 1.2, confidence: 65 },
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
    if (!skin) throw new APIError('Skin not found.', 404, 'NOT_FOUND');
    /* Legacy malformed localized literal:
    if (!skin) throw new APIError('濡ゆ婢橀幖褎绋夊鍛憼闁?, 404, 'NOT_FOUND');
    * /
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
        summary: '閻犲洢鍎甸妶浼村传娴ｇ晫绠柡鍫㈠枍閻滎垶寮介悡搴＄秮闁告柣鍔嬬€靛瞼鎲版担绋跨秬 Valve 闁哄洤鐡ㄩ弻濠囧椽瀹€鍐洷濞存粌顑囩划鈥趁规惔鈥愁殯闁告繂绉查埀?,
        relatedNews: window.CSVestData.NEWS_FEED.slice(0, 3),
        sources: ['Valve 閻庤蓱閺?, 'HLTV', 'BUFF 闁稿浚鍓欓幉?],
      })
    );
  }

  */
  async chat(message, sessionId, onChunk) {
    if (this.useMock) {
      this.setUseMock(false);
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
      if (!response.ok) throw new APIError('Chat request failed.', response.status);
      /* Legacy malformed localized literals:

      if (!response.ok) throw new APIError('閻庣數顢婇惁鐣屾嫚闁垮婀村鎯扮簿鐟?, response.status);
      if (!response.body) throw new APIError('婵炵繝绀佺槐锟犲传瀹ュ懐瀹夊☉鎾崇Т瑜版煡鎮?, 500);

      */
      if (!response.body) throw new APIError('Chat response has no body.', 500);
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
      console.warn('[API] chat stream failed:', err.message);
      this.online = false;
      throw err;
    }
  }

  async orchestrateAI(payload) {
    if (this.useMock) {
      // Recover an older cached bundle without ever calling a mock endpoint.
      this.setUseMock(false);
    }
    try {
      const result = await this._fetch('/api/ai/orchestrate', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      this.online = true;
      return result;
    } catch (err) {
      console.error('[API] orchestrate request failed:', err.message);
      this.online = false;
      throw err;
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

  async runAgentRound(sessionId, message, options = {}) {
    return this._fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/round`, {
      method: 'POST',
      body: JSON.stringify({
        message,
        locale: localStorage.getItem('sv_lang') || 'zh-CN',
      }),
    });
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

  /* Disabled browser mock implementation:
  async _mockChatStream(message, onChunk) {
    const response = window.CSVestData.AI_PRESET_RESPONSES['default']
      || '闁规儼椴搁悺鎴︽晬鐏炵晫绉奸柛鎾崇С鐠愮喓绮嬮懡銈呮疇婵犳洘姊婚妵姘熼垾宕囩闁靛棗鍊介顒勫触椤栨艾袟闁告艾娴烽顒勭嵁鐠哄搫褰犻梻?Mock 闁告艾閰ｉ崳鍝ユ嫚閺囨ǚ鍋?;
    const chunks = response.split(/(?<=[闁靛棗鍋婄槐鎺楁晬??\n])/);
    for (const chunk of chunks) {
      if (onChunk) onChunk(chunk);
      await new Promise(r => setTimeout(r, 24));
    }
  }

  }

  async translateAIContent(content, targetLocale) {
    return this._fetch('/api/ai/translate', {
      method: 'POST',
      body: JSON.stringify({ content, targetLocale }),
    });
  }
  */
  async debate(skinId, options = {}) {
    const settings = typeof options === 'string' ? { mode: options } : options;
    const query = new URLSearchParams({
      mode: settings.mode || 'bull_bear',
      locale: settings.locale || localStorage.getItem('sv_lang') || 'zh-CN',
      live: String(!!settings.live),
      horizon: String(settings.horizon || 7),
      riskLevel: settings.riskLevel || 'medium',
    });
    if (settings.budget) query.set('budget', String(settings.budget));
    if (settings.rounds) query.set('rounds', String(settings.rounds));
    return this._safeCall(
      () => this._fetch(`/api/debate/${encodeURIComponent(skinId)}?${query.toString()}`, { method: 'POST' }),
      () => null
    );
  }

  async getNews(params = {}) {
    return this._safeCall(
      () => this._fetch(`/api/news?${new URLSearchParams(params)}`),
      () => window.CSVestData.NEWS_FEED
    );
  }

  async fetchNews({ aggressive = true } = {}) {
    const qs = new URLSearchParams({ aggressive: aggressive ? '1' : '0' });
    return this._fetch(`/api/news/fetch?${qs}`, { method: 'POST', body: '{}' });
  }

  async getDailyReport(date, { refresh = false } = {}) {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    if (refresh) params.set('refresh', '1');
    const qs = params.toString();
    return this._safeCall(
      () => this._fetch(`/api/daily-report${qs ? `?${qs}` : ''}`),
      () => ({
        date: date || new Date().toISOString().slice(0, 10),
        generatedAt: new Date().toISOString(),
        metrics: { monitored: 20, gainers: 14, losers: 6 },
        hotVolume: window.CSVestData.HOT_VOLUME,
        aiSummary: 'CS2 skin market brief 闁?monitored demo universe is mostly advancing, with breadth skewed constructive [1]. Event calendars and sticker liquidity remain the main near-term drivers; watch post-Major mean reversion [2].\n\nPrefer liquid rifles/SMGs over thin knife/glove books when scaling risk. Skin markets are highly volatile 闁?this is not investment advice.',
        sources: this._mockRagSources(),
        news: window.CSVestData.NEWS_FEED,
      })
    );
  }

  /* Disabled browser mock implementation:
  _mockRagSources() {
    const news = (window.CSVestData.NEWS_FEED || []).slice(0, 4);
    const kb = [
      { type: 'kb', title: 'CS2 閻㈩垰鍊稿┃鈧柣顓滃劥閻︽垶鎯?, source: '闁告劕鎳愰悿鍡涙儗閵夈劎妲曢幖?, snippet: 'Major 閻犙勭◥缁ㄣ劑宕滃鍛€?7-14 濠?闁烩晝顭堥崣褏鎷圭€靛摜鍓ㄥ☉鎾抽叄閵堜即宕担鐟扮亣濞存嚎鍊濋崳娲焻濮橆剛鍩楀☉鎾筹工瀹?15-30%,濞达絽妫滅粋灞剧鐎ｎ剛娉㈤柡澶屽枎閹寮垫径濠冪閻犲鍟敮鍥礉濞戞牑鍋?, score: 3, relevance: 1 },
      { type: 'kb', title: 'CS2 閻㈩垰鍊稿┃鈧柣顓滃劥閻︽垶鎯?, source: '闁告劕鎳愰悿鍡涙儗閵夈劎妲曢幖?, snippet: '濡ゅ倹眉閻滎垶宕愰梻瀵哥У婵炵繝绀佹慨鈺呭箑瑜旈妶浼村传?闁告巻鍋?闁归潧顑呴〃?闁哄啨鍎遍崬鏉戔枖閵忕姴袟濠?濞戞梹婢樺畷鐘崇瀹勭増鈻曢悗?濞戞挸绉归埀顒€鍊搁幃搴㈠緞瑜旈·鍌炴儗椤撶姴娈犻柕?, score: 2, relevance: 0.67 },
    ];
    const newsSrc = news.map((n, i) => ({
      type: 'news',
      title: n.title,
      snippet: n.summary || n.title,
      source: n.source || 'RAG 闁活厹鍎撮惁鎴炴償?,
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
          answer: `(婵犳洘姊婚妵姘熼垾宕囩) 闁哄秷顫夊畵渚€宕ラ幋锕€娅ゆ俊顐熷亾缂佷究鍨归崺宀勬儍閸曨厾鍙€閻犲洤妫楃花杈ㄧ▔鎼淬倗銈悹?闂佽棄鐗嗛顕€濡?{query}闁靛棗绉跺▓鎴﹀礆閸℃鈧?闁烩晝顭堥崣褎顨滈弶鎸庢儌閺夆晜鍨跺﹢锟犲矗?Major 閻犙勭〒閳诲吋绋?Valve 闁哄洤鐡ㄩ弻濠冿紣閸曨剚鍩傜憸鏉垮船閹?闁瑰瓨鍔掑锕傛煂韫囧海鐟㈠ù鐘绘敱閻楃鈻旈姀鐘残楅柛鏃傚Т閵?[1][2];鐎点倝缂氶鍛磼閹捐櫕鍊ら柟瀛樺姃濮橈箓鏌岃箛搴ｇ憿缁句勘鍔嶅畷顖滅驳婢跺矂鐛撻柛鎺嬪€栭弻鍥礂閵夈儲绨氶柡鍐煐濠р偓闁靛棗鍊靛▓?濡ゆ婢橀幖褏鏁崒姘皻濡ゅ倹蓱鐏忔繈宕?濞寸姰鍎扮粭鍌涚▔瀹ュ棛鈧垶骞嬮幇顓烆潓閻犙冨缂傛挾鎷嬮琛″亾娣?
          sources,
          retrieval: { mode: 'vector', provider: 'dashscope', model: 'text-embedding-v3' },
        };
      }
    );
  }

  */
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
      holdingType: data.holdingType || 'sim',
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
      () => ({ dates: [], values: [], predictedDates: [], predictedValues: [], total: 0 })
    );
  }

  async diagnosePortfolio() {
    return this._safeCall(
      () => this._fetch('/api/portfolio/diagnose', { method: 'POST' }),
      () => ({
        empty: true,
        summary: 'Offline demo: connect the backend to run portfolio diagnosis.',
        aiSummary: 'Offline demo: connect the backend to run portfolio diagnosis.',
        valueRange: null,
        adjustments: [],
        riskTopN: [],
      })
    );
  }

  // ============ 闁瑰瓨鍨瑰▓鎴炴償閹惧磭鎽犻柨娑樼墢濠€锛勨偓鍦仜缁ㄨ京鈧稒锕槐閬嶅触鎼达綆浼傜€垫澘鎳庨顕€骞掗妷顖滅闁稿繐鐗忛弳鈧柟鎭掑劚瑜?+ mock闁?===========
  _mockInventory() {
    if (this._inventory) return this._inventory;
    this._inventory = (window.CSVestData.DEFAULT_INVENTORY || []).map(p => ({ ...p }));
    return this._inventory;
  }

  /** GET /api/inventory 闁?闁兼儳鍢茶ぐ鍥儑閻旈鏉介幖瀛樻尭閻°劑宕氬Δ鍕┾偓?*/
  async getInventory() {
    return this._safeCall(
      () => this._fetch('/api/inventory'),
      () => ({ total: this._mockInventory().length, items: this._mockInventory() })
    );
  }

  /** POST /api/inventory 闁?闁归潧顑呮慨鈺伹庣拠鎻掝潱閹煎瓨鎸搁悺銊︻殰閺夋寧鎯?*/
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

  /** DELETE /api/inventory/{id} 闁?缂佸顭峰▍搴㈡償閹惧磭鎽犲Δ妤佹緲閹?*/
  async deleteInventoryItem(id) {
    return this._safeCall(
      () => this._fetch(`/api/inventory/${id}`, { method: 'DELETE' }),
      () => {
        this._inventory = this._mockInventory().filter(p => p.id !== id);
        return { success: true };
      }
    );
  }

  /** GET /api/inventory/value_history 闁?閹煎瓨鎸搁悺銊╁箑鐠佸磭骞嗛柛濠呭閾斿宕?*/
  async getInventoryValueHistory(days = 90) {
    return this._safeCall(
      () => this._fetch(`/api/inventory/value_history?days=${days}`),
      () => {
        const gen = window.CSVestData.generateInventoryValueHistory;
        const inv = this._mockInventory ? this._mockInventory() : [];
        return gen
          ? gen(inv, days)
          : { dates: [], values: [], predictedDates: [], predictedValues: [], total: 0 };
      }
    );
  }

  /**
   * POST /api/inventory/steam/import 闁?Steam 閹煎瓨鎸搁悺銊р偓鐢靛帶閸欏棝鏁嶉崼婵堢鐎殿喒鍋撻柛娆愬煀缁?
   * 闁告艾娴烽顒傗偓浣冾潐鐢挳宕ユ惔顖滅獥闁瑰嘲顦ぐ?Steam inventory 闁?闁哄嫮濮撮惃?market_hash_name 闁?闁告劖鐟ラ崣?inventory
   */
  async importSteamInventory(payload = {}) {
    return this._safeCall(
      () => this._fetch('/api/inventory/steam/import', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
      () => {
        throw new Error('Steam inventory import is unavailable.');
        /* Legacy malformed localized fallback:
        const err = new Error('Steam 閻庣數鍘ч崣鍡涙閳ь剚娼婚悙鏉戝闁告艾娴烽?鐟滅増鎸告晶鐘崇▔閻戞宸濈紒鈧悜妯愪礁顕?);
        err.code = 'STEAM_IMPORT_PENDING';
        throw err;
        */
      },
      { fallback: false }   // 闁活亞鍠庨悿鍕煥濞嗘帩鍤?403缂佸鐒﹀﹢?429闂傚嫭鍔栫粊?404缂?闊洤鎳橀妴蹇旂▔婵犲啫顫?濞戞挸绉烽崗妯兼偖?mock 闁稿繑绮岀花鎶藉箳閳哄啯纾?
    );
  }

  /** 濞寸姴楠搁崳锝夊磹閸忓吋閿ょ紒鐐鐢湱绮?returnPct闁挎稑鐗婂Λ顐﹀触鎼达綆浼?online track 闁告瑯鍨甸崗妯肩磽妤︽鍤夐悗娑欘殕椤斿矂鏁?*/
        /*
      },
      { fallback: false }
        * /
    /*
    );
    */

  _returnPctFromSeries(arr) {
    if (!Array.isArray(arr) || arr.length < 2) return null;
    let first = null;
    let last = null;
    for (const v of arr) {
      if (v == null || Number.isNaN(Number(v))) continue;
      if (first == null) first = Number(v);
      last = Number(v);
    }
    if (first == null || last == null || first === 0) return null;
    return +(((last / first) - 1) * 100).toFixed(2);
  }

  async _enrichOnlineReturnPct(cmp) {
    const online = cmp?.tracks?.online;
    const rows = online?.regression;
    if (!Array.isArray(rows) || !rows.length) return cmp;
    if (rows.every((r) => r?.returnPct != null && !Number.isNaN(Number(r.returnPct)))) {
      return cmp;
    }
    try {
      // days=0闁挎稒鐭粭澶愬箣椤忓懏鐒介柨娑樼灱閺併倝宕楅妸锔诲斀闁告垟鍋撻柛濠傚悑鐢綊寮ㄩ崜浣规妱
      const raw = await this._fetch('/api/models/backtest?days=0&track=online');
      const bt = this._normalizeBacktest(raw, 0);
      const series = bt?.series || {};
      online.regression = rows.map((r) => {
        if (r?.returnPct != null && !Number.isNaN(Number(r.returnPct))) return r;
        const rp = this._returnPctFromSeries(series[r.name]);
        return rp == null ? r : { ...r, returnPct: rp };
      });
    } catch (err) {
      console.warn('[API] enrich online returnPct failed:', err?.message || err);
    }
    return cmp;
  }

  async getModelComparison() {
    return this._safeCall(
      async () => {
        const cmp = await this._fetch('/api/models/comparison');
        return this._enrichOnlineReturnPct(cmp);
      },
      () => window.CSVestData.MODEL_COMPARISON
    );
  }

  /** 閻忓繐妫滅粊顐︽煂閹寸偞閿ょ紒鎹愭硶缁儤绋夐埀顒佺▔閹巻鍋撶仦鐐婵炲牅绲婚幑锝夋倷?100闁靛棗绉跺▓鎴﹀礄閳ь剟宕愰崗鐓庣樄闁?*/
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

  /** 閻忓繐妫欓弻濠囧籍瑜嶅ú鏍?JSON 缂備胶鍠嶇粩鎾箣?{ dates, series: { name: number[] } } */
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
        // 闁告艾娴烽顒€顔忛崣澶婄樆闁稿繈鍔庨埢鑲╂導妞嬪骸浠憸鐗堝笂缁?indexBase=full_start)闁哄啳娉涚€ｄ線宕樺鍥╃礆闁衡偓閹惧懐骞?
        // 闁哄唲鍕闁告瑱缍€鐎氥垺绂?indexed闁靛棔绀佸畵鍫ュ及椤栨ǚ鍋撳畝鈧悰銉╁矗閿濆娴曢柣?100闁靛棗绋勭槐婵嬪礃瀹ュ棗鐦?days 闁规惌浜滆ぐ鍥础閸愭彃璁查柕?
        let series = raw.series;
        if (!raw.indexed) {
          series = this._reindexSeries(raw.series);
        } else if (raw.indexBase !== 'full_start' && !raw._windowReindexed) {
          // 闁稿繒鍘ч鎰板籍瑜嶉幃妤冪博椤栥倗绐楃€圭寮跺Σ鍝ョ玻濡も偓瑜版稖銇愰幒宥囶伇闁挎稑濂旂换姘跺箰娴ｇ鏂ч柡?
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
    // 闁哄倻澧楅悧绋款嚕? fee_0.0000.{model}: [{date, capital}, ...]
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
      // 闁稿繐鐗婄€垫粓寮€涙﹩鍞介悹褔顥撻崑锝堛亹閹哄秶顏遍柨娑樿嫰閸熲偓闁规惌浜濆〒鑸垫交?N 濠?
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
        note: 'Values are indexed to 100 at the full backtest start.',
        /* Legacy malformed localized note:
        note: '闁告垟鍋撻柛濠囨？娴滄帡寮€涙﹩鍞介柛銉у仦缁佸鎸ф搴′化=100闁挎稒绋撻悺銉╂偩閵夈儲鍎撻柣婊€鍗抽崳鐐閹捐尙绉撮柨娑樻湰鐏忔繈宕濋妸鈹惧亾濮橆剛鍩楅悘蹇撶箣缁剙顭ㄩ垾鑼尝 Buy&Hold闁?,
        */
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

  async getBacktest(days = 60, track = 'historical') {
    return this._safeCall(
      async () => {
        const raw = await this._fetch(`/api/models/backtest?days=${days}&track=${encodeURIComponent(track)}`);
        return this._normalizeBacktest(raw, days);
      },
      () => this._normalizeBacktest(null, days)
    );
  }

  async getShap(model = 'xgboost') {
    return this._safeCall(
      () => this._fetch(`/api/models/shap?model=${encodeURIComponent(model)}`),
      () => (window.CSVestData.SHAP_FEATURES || []).map(d => ({
        feature: d.name || d.feature,
        importance: d.value ?? d.importance ?? d.mean_abs_shap ?? 0,
        meanAbsShap: d.value ?? d.importance ?? d.mean_abs_shap ?? 0,
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
