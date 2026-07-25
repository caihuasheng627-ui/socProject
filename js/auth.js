// ============================================
// CSVest - 用户认证
// 优先走后端 /api/login · /api/register（JWT → sv_token）
// 后端不可用时回落 localStorage Mock，便于静态 Pages 演示
// ============================================

(function (global) {
  const USERS_KEY = 'sv_users';
  const SESSION_KEY = 'sv_session';
  const NAMES_KEY = 'sv_display_names';
  const DEMO_EMAIL = 'demo@csvest.app';
  const DEMO_PASSWORD = 'demo123';
  /** 与 backend DEMO_USERNAME 对齐 */
  const DEMO_USERNAME = 'demo';

  function _now() {
    return Date.now();
  }

  function _uid(prefix) {
    return `${prefix}_${_now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  }

  function _api() {
    return global.CSVestAPI || null;
  }

  /** 是否尝试真实 /api/login·register（与数据层 useMock 解耦） */
  function _shouldTryApiAuth() {
    const api = _api();
    if (!api || typeof api.login !== 'function') return false;
    if (typeof location !== 'undefined') {
      const h = (location.hostname || '').toLowerCase();
      const isStatic = h.endsWith('github.io') || h.endsWith('gitlab.io') || h.endsWith('pages.dev');
      // 纯静态托管且未配置公网 API：只能本地 Mock
      if (isStatic && !api.baseURL) return false;
    }
    return true;
  }

  function _loadUsers() {
    try {
      const raw = localStorage.getItem(USERS_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch {
      return [];
    }
  }

  function _saveUsers(users) {
    localStorage.setItem(USERS_KEY, JSON.stringify(users));
  }

  function _loadNames() {
    try {
      const raw = localStorage.getItem(NAMES_KEY);
      const map = raw ? JSON.parse(raw) : {};
      return map && typeof map === 'object' ? map : {};
    } catch {
      return {};
    }
  }

  function _saveDisplayName(userId, name) {
    if (userId == null || !name) return;
    const map = _loadNames();
    map[String(userId)] = String(name).trim();
    localStorage.setItem(NAMES_KEY, JSON.stringify(map));
  }

  function _displayNameFor(userId, fallback) {
    const map = _loadNames();
    return map[String(userId)] || fallback || '';
  }

  function _ensureDemoUser() {
    const users = _loadUsers();
    if (!users.some((u) => u.email === DEMO_EMAIL)) {
      users.push({
        id: 'user_demo',
        email: DEMO_EMAIL,
        password: DEMO_PASSWORD,
        name: 'Demo',
        createdAt: _now(),
      });
      _saveUsers(users);
    }
  }

  /** 登录标识 → 后端 username（demo 邮箱映射到内置 demo 用户） */
  function toUsername(input) {
    const s = String(input || '').trim().toLowerCase();
    if (!s) return '';
    if (s === DEMO_EMAIL || s === DEMO_USERNAME) return DEMO_USERNAME;
    return s;
  }

  function _publicUser(user) {
    if (!user) return null;
    return {
      id: user.id,
      email: user.email,
      name: user.name,
      username: user.username || user.email,
      isDemo: !!user.isDemo,
      isAdmin: !!user.isAdmin,
      createdAt: user.createdAt,
      source: user.source || 'local',
    };
  }

  function _fromApiUser(apiUser, displayName) {
    if (!apiUser) return null;
    const username = apiUser.username || '';
    const id = apiUser.id;
    const name = displayName || _displayNameFor(id, username) || username;
    const email = username.includes('@') ? username : (username === DEMO_USERNAME ? DEMO_EMAIL : username);
    return _publicUser({
      id,
      email,
      name,
      username,
      isDemo: !!apiUser.is_demo,
      isAdmin: !!apiUser.is_admin,
      createdAt: apiUser.created_at || null,
      source: 'api',
    });
  }

  function _avatarChar(user) {
    if (!user) return '?';
    const src = (user.name || user.email || user.username || '?').trim();
    return src.charAt(0).toUpperCase();
  }

  function getSession() {
    try {
      const raw = localStorage.getItem(SESSION_KEY);
      if (!raw) return null;
      const session = JSON.parse(raw);
      if (!session || !session.user || !session.token) return null;
      if (session.expiresAt && session.expiresAt < _now()) {
        clearSession();
        return null;
      }
      return session;
    } catch {
      return null;
    }
  }

  function getCurrentUser() {
    return getSession()?.user || null;
  }

  function isLoggedIn() {
    return !!getCurrentUser();
  }

  function _applyToken(token) {
    localStorage.setItem('sv_token', token);
    const api = _api();
    if (api && typeof api.setToken === 'function') {
      api.setToken(token);
    }
  }

  function _writeSession(user, token, expiresInSec) {
    const session = {
      token,
      user: _publicUser(user),
      expiresAt: _now() + (Number(expiresInSec) > 0 ? Number(expiresInSec) * 1000 : 7 * 24 * 60 * 60 * 1000),
    };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    // mock token 不写入 Authorization，避免真实后端 401 / 串到 demo
    if (token && !String(token).startsWith('mock.')) {
      _applyToken(token);
    } else {
      localStorage.removeItem('sv_token');
      const api = _api();
      if (api) {
        if (typeof api.clearToken === 'function') api.clearToken();
        else api.token = null;
      }
    }
    return session;
  }

  function clearSession() {
    localStorage.removeItem(SESSION_KEY);
    localStorage.removeItem('sv_token');
    const api = _api();
    if (api) {
      if (typeof api.clearToken === 'function') api.clearToken();
      else api.token = null;
    }
  }

  function _mapApiAuthError(err) {
    const status = err?.status;
    const msg = String(err?.message || '');
    if (status === 401) return { ok: false, code: 'INVALID', message: 'invalid_credentials' };
    if (status === 409 || /已存在|exists/i.test(msg)) {
      return { ok: false, code: 'EXISTS', message: 'email_exists' };
    }
    if (status === 400 && /密码|password|4/i.test(msg)) {
      return { ok: false, code: 'WEAK', message: 'password_too_short' };
    }
    return { ok: false, code: 'GENERIC', message: msg || 'auth_failed' };
  }

  async function login(email, password) {
    const e = String(email || '').trim().toLowerCase();
    const p = String(password || '');
    if (!e || !p) {
      return { ok: false, code: 'EMPTY', message: 'email_password_required' };
    }

    const username = toUsername(e);
    const api = _api();

    if (api && typeof api.login === 'function' && _shouldTryApiAuth()) {
      try {
        const data = await api.login(username, p);
        if (data?.token && data?.user) {
          const user = _fromApiUser(data.user, _displayNameFor(data.user.id, data.user.username));
          const session = _writeSession(user, data.token, data.expires_in);
          return { ok: true, session, user: session.user, source: 'api' };
        }
      } catch (err) {
        // 明确鉴权失败不回落 Mock，避免误以为登录了本地账号
        if (err?.status === 401 || err?.status === 400 || err?.status === 409) {
          return _mapApiAuthError(err);
        }
        // 网络/离线：继续本地 Mock
        console.warn('[Auth] API login failed, trying local mock:', err?.message || err);
      }
    }

    return _loginLocal(e, p);
  }

  function _loginLocal(email, password) {
    _ensureDemoUser();
    const user = _loadUsers().find((u) => u.email === email);
    if (!user || user.password !== password) {
      return { ok: false, code: 'INVALID', message: 'invalid_credentials' };
    }
    const token = `mock.${btoa(unescape(encodeURIComponent(user.id + ':' + _now()))).replace(/=+$/, '')}`;
    const session = _writeSession(
      { ...user, source: 'local' },
      token,
      7 * 24 * 60 * 60
    );
    return { ok: true, session, user: session.user, source: 'local' };
  }

  async function register(name, email, password) {
    const n = String(name || '').trim();
    const e = String(email || '').trim().toLowerCase();
    const p = String(password || '');
    if (!n || !e || !p) {
      return { ok: false, code: 'EMPTY', message: 'fields_required' };
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) {
      return { ok: false, code: 'EMAIL', message: 'invalid_email' };
    }
    if (p.length < 6) {
      return { ok: false, code: 'WEAK', message: 'password_too_short' };
    }

    const username = toUsername(e);
    const api = _api();

    if (api && typeof api.register === 'function' && _shouldTryApiAuth()) {
      try {
        const data = await api.register(username, p);
        if (data?.token && data?.user) {
          _saveDisplayName(data.user.id, n);
          const user = _fromApiUser(data.user, n);
          const session = _writeSession(user, data.token, data.expires_in);
          return { ok: true, session, user: session.user, source: 'api' };
        }
      } catch (err) {
        if (err?.status === 401 || err?.status === 400 || err?.status === 409) {
          return _mapApiAuthError(err);
        }
        console.warn('[Auth] API register failed, trying local mock:', err?.message || err);
      }
    }

    return _registerLocal(n, e, p);
  }

  function _registerLocal(name, email, password) {
    _ensureDemoUser();
    const users = _loadUsers();
    if (users.some((u) => u.email === email)) {
      return { ok: false, code: 'EXISTS', message: 'email_exists' };
    }
    const user = {
      id: _uid('user'),
      email,
      password,
      name,
      createdAt: _now(),
      source: 'local',
    };
    users.push(user);
    _saveUsers(users);
    const token = `mock.${btoa(unescape(encodeURIComponent(user.id + ':' + _now()))).replace(/=+$/, '')}`;
    const session = _writeSession(user, token, 7 * 24 * 60 * 60);
    return { ok: true, session, user: session.user, source: 'local' };
  }

  function logout() {
    clearSession();
    return { ok: true };
  }

  function updateProfile({ name } = {}) {
    const session = getSession();
    if (!session) return { ok: false, code: 'UNAUTHORIZED', message: 'not_logged_in' };
    const n = String(name || '').trim();
    if (!n) return { ok: false, code: 'EMPTY', message: 'name_required' };

    _saveDisplayName(session.user.id, n);

    if (session.user.source === 'api' || !String(session.token || '').startsWith('mock.')) {
      const nextUser = { ...session.user, name: n };
      const next = _writeSession(nextUser, session.token, Math.max(0, (session.expiresAt - _now()) / 1000));
      return { ok: true, user: next.user };
    }

    const users = _loadUsers();
    const idx = users.findIndex((u) => u.id === session.user.id);
    if (idx < 0) return { ok: false, code: 'NOT_FOUND', message: 'user_not_found' };
    users[idx].name = n;
    _saveUsers(users);
    const next = _writeSession({ ...users[idx], source: 'local' }, session.token, 7 * 24 * 60 * 60);
    return { ok: true, user: next.user };
  }

  /** 用 /api/me 校验 JWT，成功则刷新会话；失败清会话。mock 会话仅作离线 UI。 */
  async function restoreFromApi() {
    const api = _api();
    const session = getSession();
    if (!session?.token) return null;
    if (String(session.token).startsWith('mock.')) {
      // 确保不带假 Bearer
      localStorage.removeItem('sv_token');
      if (api) {
        if (typeof api.clearToken === 'function') api.clearToken();
        else api.token = null;
      }
      return session.user;
    }
    if (!api || typeof api.me !== 'function') return session.user;
    _applyToken(session.token);
    try {
      const data = await api.me();
      const apiUser = data?.user || data;
      if (!apiUser?.id) {
        clearSession();
        return null;
      }
      const user = _fromApiUser(apiUser, session.user?.name);
      const next = _writeSession(user, session.token, Math.max(60, (session.expiresAt - _now()) / 1000));
      return next.user;
    } catch (err) {
      if (err?.status === 401) clearSession();
      return null;
    }
  }

  // 启动时：历史 mock.* 不得作为 Bearer（会 401）；会话可保留作离线 UI 登录态
  (function _bootPurge() {
    try {
      const token = localStorage.getItem('sv_token') || '';
      if (token.startsWith('mock.')) {
        localStorage.removeItem('sv_token');
        const api = _api();
        if (api) {
          if (typeof api.clearToken === 'function') api.clearToken();
          else api.token = null;
        }
      }
    } catch {
      /* ignore */
    }
  })();

  _ensureDemoUser();

  global.CSVestAuth = {
    DEMO_EMAIL,
    DEMO_PASSWORD,
    DEMO_USERNAME,
    getSession,
    getCurrentUser,
    isLoggedIn,
    login,
    register,
    logout,
    updateProfile,
    restoreFromApi,
    toUsername,
    avatarChar: _avatarChar,
    clearSession,
  };
})(typeof window !== 'undefined' ? window : globalThis);
