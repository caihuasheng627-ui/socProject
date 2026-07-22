// ============================================
// CSVest - 前端本地用户认证（后端未接入前的 Mock）
// 数据存 localStorage；后续可无缝切到 /api/auth/*
// ============================================

(function (global) {
  const USERS_KEY = 'sv_users';
  const SESSION_KEY = 'sv_session';
  const DEMO_EMAIL = 'demo@csvest.app';
  const DEMO_PASSWORD = 'demo123';

  function _now() {
    return Date.now();
  }

  function _uid(prefix) {
    return `${prefix}_${_now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
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

  function _publicUser(user) {
    if (!user) return null;
    return {
      id: user.id,
      email: user.email,
      name: user.name,
      createdAt: user.createdAt,
    };
  }

  function _avatarChar(user) {
    if (!user) return '?';
    const src = (user.name || user.email || '?').trim();
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

  function _writeSession(user) {
    const token = `mock.${btoa(unescape(encodeURIComponent(user.id + ':' + _now()))).replace(/=+$/, '')}`;
    const session = {
      token,
      user: _publicUser(user),
      expiresAt: _now() + 7 * 24 * 60 * 60 * 1000,
    };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    // 与 api.js 约定：后续真实鉴权可复用 sv_token
    localStorage.setItem('sv_token', token);
    if (global.CSVestAPI && typeof global.CSVestAPI.setToken === 'function') {
      global.CSVestAPI.setToken(token);
    }
    return session;
  }

  function clearSession() {
    localStorage.removeItem(SESSION_KEY);
    localStorage.removeItem('sv_token');
    if (global.CSVestAPI) {
      global.CSVestAPI.token = null;
    }
  }

  function login(email, password) {
    _ensureDemoUser();
    const e = String(email || '').trim().toLowerCase();
    const p = String(password || '');
    if (!e || !p) {
      return { ok: false, code: 'EMPTY', message: 'email_password_required' };
    }
    const user = _loadUsers().find((u) => u.email === e);
    if (!user || user.password !== p) {
      return { ok: false, code: 'INVALID', message: 'invalid_credentials' };
    }
    const session = _writeSession(user);
    return { ok: true, session, user: session.user };
  }

  function register(name, email, password) {
    _ensureDemoUser();
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
    const users = _loadUsers();
    if (users.some((u) => u.email === e)) {
      return { ok: false, code: 'EXISTS', message: 'email_exists' };
    }
    const user = {
      id: _uid('user'),
      email: e,
      password: p,
      name: n,
      createdAt: _now(),
    };
    users.push(user);
    _saveUsers(users);
    const session = _writeSession(user);
    return { ok: true, session, user: session.user };
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

    const users = _loadUsers();
    const idx = users.findIndex((u) => u.id === session.user.id);
    if (idx < 0) return { ok: false, code: 'NOT_FOUND', message: 'user_not_found' };

    users[idx].name = n;
    _saveUsers(users);
    const next = _writeSession(users[idx]);
    return { ok: true, user: next.user };
  }

  _ensureDemoUser();

  global.CSVestAuth = {
    DEMO_EMAIL,
    DEMO_PASSWORD,
    getSession,
    getCurrentUser,
    isLoggedIn,
    login,
    register,
    logout,
    updateProfile,
    avatarChar: _avatarChar,
    clearSession,
  };
})(typeof window !== 'undefined' ? window : globalThis);
