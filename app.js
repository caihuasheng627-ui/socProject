// ============================================
// CSVest - 主应用
// 基于策划书功能清单实现
// ============================================

const { createApp, ref, computed, onMounted, onUpdated, nextTick, watch } = Vue;

// 安全地获取/创建 ECharts 实例。
// 图表所在的整块 DOM 是被 v-if 控制的(切换页面时会被整个销毁重建),
// 之前的代码用「实例变量存在就跳过 init」的写法,离开页面再回来时
// 拿到的其实是绑定在“已被移除的旧 DOM 节点”上的旧实例——图表会变成空白/不更新。
// 这里改成:如果旧实例绑定的 DOM 已经不是当前容器了,就先 dispose 掉旧实例再重新 init。
function getOrCreateChart(existingInstance, dom) {
  if (existingInstance) {
    if (existingInstance.getDom() === dom) return existingInstance;
    existingInstance.dispose();
  }
  return echarts.init(dom);
}

/** 让图表不抢走手机纵向滑动 */
function allowPageScrollOverChart(chart) {
  try {
    const dom = chart?.getDom?.();
    if (!dom) return;
    dom.style.touchAction = 'pan-y';
    dom.querySelectorAll('canvas').forEach((c) => {
      c.style.touchAction = 'pan-y';
    });
  } catch (_) { /* ignore */ }
}

// ============ i18n 国际化 ============
const SUPPORTED_LANGS = ['zh-CN', 'en-US'];
const detectBrowserLang = () => {
  const browser = (navigator.language || 'zh-CN').toLowerCase();
  if (browser.startsWith('en')) return 'en-US';
  return 'zh-CN';
};
const currentLang = ref(localStorage.getItem('sv_lang') || detectBrowserLang());
const t = (key, params = {}) => {
  const dict = window.I18N[currentLang.value] || window.I18N['zh-CN'];
  let str = dict[key] || key;
  // 简单参数替换: {name} → params.name
  Object.keys(params).forEach(k => {
    str = str.replace(new RegExp(`\\{${k}\\}`, 'g'), params[k]);
  });
  return str;
};
const setLang = (lang) => {
  if (!SUPPORTED_LANGS.includes(lang)) return;
  currentLang.value = lang;
  localStorage.setItem('sv_lang', lang);
  document.documentElement.lang = lang === 'zh-CN' ? 'zh-CN' : 'en';
};
const toggleLang = () => {
  setLang(currentLang.value === 'zh-CN' ? 'en-US' : 'zh-CN');
};
setLang(currentLang.value);

const app = createApp({
  setup() {
    // ============ 菜单 ============
    // 5 页信息架构(策划书 §5):行情中心 / 我的库存 / AI 对话 / AI 日报 / 模型实验室
    // 物品详情(prediction)与价格预警(alerts)为二级视图,不入侧边栏
    const menu = computed(() => {
      // 管理端不进侧边栏 / 命令面板 / 数字快捷键,仅通过 #admin 隐藏入口进入
      return [
        {
          id: 'dashboard',
          label: t('menu.dashboard'),
          badge: t('menu.badge.core'),
          iconName: 'chart-line',
        },
        {
          id: 'portfolio',
          label: t('menu.portfolio'),
          iconName: 'clipboard-list',
        },
        {
          id: 'chat',
          label: t('menu.chat'),
          badge: t('menu.badge.highlight'),
          iconName: 'message-circle',
        },
        {
          id: 'daily',
          label: t('menu.daily'),
          iconName: 'newspaper',
        },
        {
          id: 'models',
          label: t('menu.models'),
          iconName: 'cpu',
        },
      ];
    });

    // 二级视图 → 所属一级页面(侧边栏高亮 + 面包屑)
    const PARENT_PAGE = { prediction: 'dashboard', alerts: 'portfolio' };

    // 渲染菜单图标 SVG
    const renderMenuIcon = (name) => {
      try {
        if (typeof window === 'undefined' || !window.renderLucide) return '';
        return window.renderLucide(name, { size: 20, strokeWidth: 2 });
      } catch (e) {
        console.warn('renderMenuIcon error:', name, e);
        return '';
      }
    };

    const renderLucideIcon = (name, size = 18) => {
      try {
        if (typeof window === 'undefined' || !window.renderLucide) return '';
        return window.renderLucide(name, { size, strokeWidth: 2 });
      } catch (e) {
        return '';
      }
    };

    const currentPage = ref((() => {
      try {
        // 隐藏入口: URL hash #admin
        if (typeof location !== 'undefined' && (location.hash || '').replace(/^#/, '') === 'admin') {
          return 'admin';
        }
        const saved = sessionStorage.getItem('sv_page');
        // 管理端不从 session 恢复,避免普通用户刷新后停在管理页
        if (saved && saved !== 'admin' && typeof saved === 'string') return saved;
      } catch (_) { /* ignore */ }
      return 'dashboard';
    })());
    watch(currentPage, (pageId) => {
      try {
        if (pageId === 'admin') {
          sessionStorage.removeItem('sv_page');
          if (typeof location !== 'undefined' && location.hash !== '#admin') {
            history.replaceState(null, '', '#admin');
          }
        } else {
          sessionStorage.setItem('sv_page', pageId);
          if (typeof location !== 'undefined' && (location.hash || '').replace(/^#/, '') === 'admin') {
            history.replaceState(null, '', location.pathname + location.search);
          }
        }
      } catch (_) { /* ignore */ }
    });
    const showAdmin = computed(() => currentPage.value === 'admin');
    const activeNavId = computed(() => PARENT_PAGE[currentPage.value] || currentPage.value);
    const currentMenu = computed(() => menu.value.find(m => m.id === activeNavId.value));
    // 二级视图在面包屑中的子标题
    const subPageLabel = computed(() => {
      if (currentPage.value === 'prediction') return t('menu.prediction');
      if (currentPage.value === 'alerts') return t('menu.alerts');
      return '';
    });

    // ============ 用户认证（前端本地，后端未接入） ============
    // 启动页提供「登录进入」与「游客体验」两个入口
    const Auth = window.CSVestAuth;
    const currentUser = ref(Auth?.getCurrentUser?.() || null);
    const isGuest = ref(!currentUser.value && sessionStorage.getItem('sv_guest') === '1');
    const showAuthPanel = ref(false);
    const authMode = ref('login');
    const authForm = ref({ name: '', email: '', password: '' });
    const authError = ref('');
    const authSubmitting = ref(false);
    const userMenuOpen = ref(false);
    const showProfileModal = ref(false);
    const profileNameDraft = ref('');
    const userAvatarChar = computed(() => {
      if (currentUser.value) return Auth.avatarChar(currentUser.value);
      return Auth.avatarChar({ name: t('auth.guest') });
    });

    const canEnter = () => !!(currentUser.value || isGuest.value);
    const showLanding = ref(
      currentPage.value === 'admin'
        ? false
        : (!canEnter() || sessionStorage.getItem('sv_entered') !== '1')
    );
    const landingExiting = ref(false);

    // 无登录且非游客时，强制停留在启动页（管理端除外）
    if (!canEnter() && currentPage.value !== 'admin') {
      sessionStorage.removeItem('sv_entered');
      sessionStorage.removeItem('sv_guest');
      isGuest.value = false;
      showLanding.value = true;
    }

    // 进入管理端时隐藏 Landing（独立全页）
    watch(showAdmin, (on) => {
      if (on) showLanding.value = false;
    });

    const enterSystem = (asGuest = false) => {
      if (landingExiting.value || !showLanding.value) return;
      if (asGuest) {
        isGuest.value = true;
        sessionStorage.setItem('sv_guest', '1');
      }
      if (!canEnter()) {
        authError.value = t('auth.err.required');
        showAuthPanel.value = true;
        return;
      }
      if (currentUser.value) {
        isGuest.value = false;
        sessionStorage.removeItem('sv_guest');
      }
      landingExiting.value = true;
      sessionStorage.setItem('sv_entered', '1');
      userMenuOpen.value = false;
      showAuthPanel.value = false;
      const done = () => {
        showLanding.value = false;
        landingExiting.value = false;
        nextTick(() => {
          renderKline();
          setTimeout(() => {
            klineChartInstance?.resize();
            radarInstance?.resize();
            backtestInstance?.resize();
            shapInstance?.resize();
            perDayInstance?.resize();
            inventoryValueChartInstance?.resize();
          }, 80);
        });
      };
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        done();
        return;
      }
      setTimeout(done, 520);
    };

    const openAuthPanel = (mode = 'login') => {
      authMode.value = mode;
      authError.value = '';
      showAuthPanel.value = true;
    };

    const closeAuthPanel = () => {
      showAuthPanel.value = false;
      authError.value = '';
    };

    const authErrorMessage = (code) => {
      const map = {
        EMPTY: 'auth.err.empty',
        INVALID: 'auth.err.invalid',
        EMAIL: 'auth.err.email',
        WEAK: 'auth.err.weak',
        EXISTS: 'auth.err.exists',
      };
      return t(map[code] || 'auth.err.generic');
    };

    const submitLogin = () => {
      if (!Auth || authSubmitting.value) return;
      authSubmitting.value = true;
      authError.value = '';
      const result = Auth.login(authForm.value.email, authForm.value.password);
      authSubmitting.value = false;
      if (!result.ok) {
        authError.value = authErrorMessage(result.code);
        return;
      }
      currentUser.value = result.user;
      isGuest.value = false;
      sessionStorage.removeItem('sv_guest');
      authForm.value.password = '';
      showToast({ title: t('auth.toast.loginOk'), subtitle: result.user.name, type: 'success' });
      enterSystem();
    };

    const submitRegister = () => {
      if (!Auth || authSubmitting.value) return;
      authSubmitting.value = true;
      authError.value = '';
      const result = Auth.register(authForm.value.name, authForm.value.email, authForm.value.password);
      authSubmitting.value = false;
      if (!result.ok) {
        authError.value = authErrorMessage(result.code);
        return;
      }
      currentUser.value = result.user;
      isGuest.value = false;
      sessionStorage.removeItem('sv_guest');
      authForm.value.password = '';
      showToast({ title: t('auth.toast.registerOk'), subtitle: result.user.name, type: 'success' });
      enterSystem();
    };

    const enterAsGuest = () => {
      showToast({ title: t('auth.toast.guest'), type: 'info' });
      enterSystem(true);
    };

    const logoutUser = () => {
      Auth?.logout?.();
      currentUser.value = null;
      isGuest.value = false;
      userMenuOpen.value = false;
      showProfileModal.value = false;
      showPortfolioModal.value = false;
      showAuthPanel.value = false;
      authMode.value = 'login';
      authError.value = '';
      showToast({ title: t('auth.toast.logoutOk'), type: 'success' });
      sessionStorage.removeItem('sv_entered');
      sessionStorage.removeItem('sv_guest');
      showLanding.value = true;
      landingExiting.value = false;
    };

    const returnToLandingForLogin = () => {
      userMenuOpen.value = false;
      showAuthPanel.value = true;
      authMode.value = 'login';
      authError.value = '';
      sessionStorage.removeItem('sv_entered');
      // 保留游客标记，取消进入后仍可再选游客
      showLanding.value = true;
      landingExiting.value = false;
    };

    // 我的库存仅登录用户可用；模拟持仓保持原有体验
    const requirePortfolioLogin = () => {
      if (currentUser.value) return true;
      showToast({
        title: t('portfolio.loginRequired.title'),
        subtitle: t('portfolio.loginRequired.toast'),
        type: 'info',
      });
      return false;
    };

    const requireInventoryLogin = () => {
      if (currentUser.value) return true;
      showToast({
        title: t('inventory.loginRequired.title'),
        subtitle: t('inventory.loginRequired.toast'),
        type: 'info',
      });
      return false;
    };

    const goToPage = (pageId) => {
      // 侧边栏/快捷键不应切到管理端; 管理端仅 #admin 进入
      if (pageId === 'admin' && (location.hash || '').replace(/^#/, '') !== 'admin') {
        try { history.replaceState(null, '', '#admin'); } catch (_) { /* ignore */ }
      }
      currentPage.value = pageId;
      sidebarOpen.value = false;
    };

    const openProfileEditor = () => {
      if (!currentUser.value) return;
      profileNameDraft.value = currentUser.value.name || '';
      showProfileModal.value = true;
      userMenuOpen.value = false;
    };

    const saveProfile = () => {
      const result = Auth?.updateProfile?.({ name: profileNameDraft.value });
      if (!result?.ok) {
        showToast({ title: authErrorMessage(result?.code), type: 'error' });
        return;
      }
      currentUser.value = result.user;
      showProfileModal.value = false;
      showToast({ title: t('auth.toast.profileOk'), type: 'success' });
    };

    // 点击页面其他区域关闭用户菜单
    if (typeof document !== 'undefined') {
      document.addEventListener('click', (e) => {
        if (!userMenuOpen.value) return;
        const menu = document.querySelector('.user-menu');
        if (menu && !menu.contains(e.target)) {
          userMenuOpen.value = false;
        }
      });
    }

    // ============ 主题切换 ============
    const theme = ref(localStorage.getItem('sv_theme') || 'dark');
    const applyTheme = (t) => {
      document.documentElement.setAttribute('data-theme', t);
      localStorage.setItem('sv_theme', t);
    };
    applyTheme(theme.value);
    const toggleTheme = () => {
      theme.value = theme.value === 'dark' ? 'light' : 'dark';
      applyTheme(theme.value);
      showToast({ title: t('theme.switched'), subtitle: theme.value === 'dark' ? t('theme.dark') : t('theme.light'), type: 'success' });
      // 重新渲染图表以适配主题
      setTimeout(() => {
        klineChartInstance?.resize();
        radarInstance?.resize();
        backtestInstance?.resize();
        shapInstance?.resize();
        perDayInstance?.resize();
        inventoryValueChartInstance?.resize();
      }, 250);
    };

    // ============ 移动端侧边栏 ============
    const sidebarOpen = ref(false);
    watch(sidebarOpen, (open) => {
      document.body.classList.toggle('sidebar-open', open);
      // 侧边栏打开/关闭会改变主内容宽度,触发 ECharts resize
      setTimeout(() => {
        klineChartInstance?.resize();
        radarInstance?.resize();
        backtestInstance?.resize();
        shapInstance?.resize();
        perDayInstance?.resize();
        inventoryValueChartInstance?.resize();
      }, 300);
    });
    watch(currentPage, () => {
      // 切换页面时自动关闭侧边栏(移动端)
      sidebarOpen.value = false;
    });

    // ============ Toast 通知 ============
    const toasts = ref([]);
    const showToast = ({ title, subtitle = '', type = 'info', icon = null, duration = 3000 }) => {
      const id = Date.now() + Math.random();
      const typeIcon = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' }[type] || 'ℹ️';
      toasts.value.push({ id, title, subtitle, type, icon: icon || typeIcon });
      setTimeout(() => {
        toasts.value = toasts.value.filter(t => t.id !== id);
      }, duration);
    };

    // ============ 数据导出 ============
    const exportData = (type, format) => {
      let data, filename;
      if (type === 'skins') {
        data = skins.value;
        filename = `CSVest_skins_${new Date().toISOString().slice(0,10)}`;
      } else if (type === 'alerts') {
        data = alerts.value;
        filename = `CSVest_alerts_${new Date().toISOString().slice(0,10)}`;
      } else if (type === 'portfolio') {
        data = portfolio.value.map(p => ({
          ...p,
          currentPrice: getCurrentPrice(p.skinId),
          pnl: getItemPnl(p),
          pnlPct: getItemPnlPct(p).toFixed(2) + '%',
        }));
        filename = `CSVest_portfolio_${new Date().toISOString().slice(0,10)}`;
      } else if (type === 'inventory') {
        if (!requireInventoryLogin()) return;
        data = myInventory.value.map(p => ({
          ...p,
          currentPrice: getCurrentPrice(p.skinId),
          marketValue: getCurrentPrice(p.skinId) * (p.quantity || 1),
        }));
        filename = `CSVest_inventory_${new Date().toISOString().slice(0,10)}`;
      } else if (type === 'models') {
        data = [...regressionModels.value, ...classificationModels.value.map(m => ({...m, course: '分类模型'}))];
        filename = `CSVest_models_${new Date().toISOString().slice(0,10)}`;
      }

      if (format === 'csv') {
        const headers = Object.keys(data[0]);
        const csv = [
          headers.join(','),
          ...data.map(row => headers.map(h => `"${(row[h] ?? '').toString().replace(/"/g, '""')}"`).join(','))
        ].join('\n');
        downloadFile(csv, filename + '.csv', 'text/csv;charset=utf-8;');
        showToast({ title: t('export.success'), subtitle: filename + '.csv', type: 'success' });
      } else if (format === 'json') {
        downloadFile(JSON.stringify(data, null, 2), filename + '.json', 'application/json');
        showToast({ title: t('export.success'), subtitle: filename + '.json', type: 'success' });
      }
    };

    const downloadFile = (content, filename, mimeType) => {
      const BOM = '\uFEFF'; // 解决中文乱码
      const blob = new Blob([BOM + content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    };

    // ============ 数据 ============
    const apiOnline = ref(false);
    const skins = ref(window.CSVestData.SKINS_POOL);
    const topGainers = ref(window.CSVestData.TOP_GAINERS);
    const topLosers = ref(window.CSVestData.TOP_LOSERS);
    const hotVolume = ref(window.CSVestData.HOT_VOLUME);
    const newsFeed = ref(window.CSVestData.NEWS_FEED);
    const debateData = ref(window.CSVestData.DEBATE_SAMPLE);

    const buildDebateFromSkin = (skin, meta = {}) => {
      const price = +(skin?.price || 0);
      const target = +(meta.targetPrice ?? price * 1.05);
      const chg = price ? +(((target - price) / price) * 100).toFixed(2) : 0;
      const entryLow = +(meta.entryLow ?? price * 0.97).toFixed(2);
      const entryHigh = +(meta.entryHigh ?? price * 0.99).toFixed(2);
      const stop = +(price * 0.93).toFixed(2);
      const bullTarget = +(target * 1.03).toFixed(2);
      return {
        skin: skin?.name || '',
        currentPrice: price,
        rounds: [
          {
            round: 1,
            bull: `🟢 多头 Agent:模型预测 7 天涨幅 ${chg}%,趋势偏多,目标价 $${bullTarget}。成交量与 Major 节奏支撑短期上行。`,
            bear: `🔴 空头 Agent:近 30 日波动较大,若跌破 $${stop} 则趋势破坏。当前价 $${price.toFixed(2)},需警惕流动性与回撤。`,
          },
          {
            round: 2,
            bull: `🟢 多头 Agent:止损 $${stop} 可控,风险收益比尚可,维持看多。建议入场 $${entryLow}-$${entryHigh}。`,
            bear: `🔴 空头 Agent:模型共识有限,预测涨幅不稳定,建议轻仓试探。`,
          },
          {
            round: 3,
            bull: '🟢 多头 Agent:综合看,温和看多,建议小仓位持有。',
            bear: '🔴 空头 Agent:同意观望偏多,严守止损。',
          },
        ],
        consensus: {
          recommendation: chg >= 0 ? '观望偏多(轻仓)' : '谨慎观望',
          entryRange: `$${entryLow} - ${entryHigh}`,
          stopLoss: `$${stop}`,
          targetPrice: `$${target.toFixed(2)} (7天)`,
          consensusScore: meta.consensusScore || 60,
          confidence: '中等',
          risks: ['饰品市场高波动,模型预测存在误差', '流动性不足时滑点放大'],
        },
      };
    };

    const loadDebate = async (skinId) => {
      const skin = skins.value.find(s => s.id === skinId) || selectedSkin.value;
      const fallback = () => {
        debateData.value = buildDebateFromSkin(skin, predictionMeta.value);
      };
      const client = api();
      if (!client || !skinId) {
        fallback();
        return;
      }
      try {
        const res = await client.debate(skinId);
        if (res?.error || !res?.rounds?.length) {
          fallback();
          return;
        }
        // 若预录辩论挂的是别的饰品价,仍以当前皮肤价重建
        const seedCur = res.prediction?.current_price;
        const liveCur = skin?.price;
        if (liveCur && seedCur && Math.abs(seedCur - liveCur) / Math.max(liveCur, 0.01) > 0.5) {
          fallback();
          return;
        }
        debateData.value = {
          skin: skin?.name || res.name || '',
          currentPrice: liveCur ?? seedCur,
          rounds: res.rounds,
          consensus: {
            recommendation: res.consensus?.recommendation || '观望',
            entryRange: res.consensus?.entryRange || '',
            stopLoss: res.consensus?.stopLoss || '',
            targetPrice: res.consensus?.targetPrice || '',
            consensusScore: res.consensus?.consensusScore || 60,
            confidence: res.consensus?.confidence || 'medium',
            risks: res.consensus?.risks || [],
          },
        };
      } catch (e) {
        fallback();
      }
    };
    const modelComparison = window.CSVestData.MODEL_COMPARISON;

    const api = () => window.CSVestAPI || window.SkinVisionAPI;

    const reconnectLeaders = () => {
      // 后端已接入 800+ 件 BUFF 实时数据；涨跌/热度榜只统计实时更新的饰品，
      // 避免与训练 CSV 的历史静态价(已停更)混排产生失真榜单
      const liveOnly = skins.value.filter((s) => s.isLive !== false);
      const pool = liveOnly.length >= 10 ? liveOnly : skins.value;
      topGainers.value = [...pool]
        .filter((s) => (s.change7d || 0) > 0)
        .sort((a, b) => (b.change7d || 0) - (a.change7d || 0))
        .slice(0, 8);
      topLosers.value = [...pool]
        .filter((s) => (s.change7d || 0) < 0)
        .sort((a, b) => (a.change7d || 0) - (b.change7d || 0))
        .slice(0, 8);
      hotVolume.value = [...pool].sort((a, b) => (b.volume24h || 0) - (a.volume24h || 0)).slice(0, 8);
    };

    const loadSkinsFromApi = async () => {
      const client = api();
      if (!client) return false;
      // 后端全集 681 件(132 csv + 549 buff); limit 调大到 1000 以全量展示
      // 连真实后端时禁止静默退回 Mock，避免行情中心“看起来在线实则演示”
      const res = await client.getSkins(
        { limit: 1000, sort: 'volume_desc' },
        { fallback: false }
      );
      const items = res?.items || [];
      if (!items.length) return false;
      skins.value = items.map(s => ({
        ...s,
        image: s.image || '🎮',
        // 后端/训练同口径 USD
        price: s.price ?? s.priceUsd ?? 0,
        priceUsd: s.priceUsd ?? s.price ?? 0,
        category: s.category || inferCategory(s),
        // 后端历史不足时 change 可能为 null；wear 可能是字符串 "nan"
        change24h: s.change24h ?? 0,
        change7d: s.change7d ?? 0,
        volume24h: s.volume24h ?? 0,
        liquidity: s.liquidity ?? 0,
        wear: (s.wear && String(s.wear).toLowerCase() !== 'nan') ? s.wear : '—',
        // 数据新鲜度: BUFF 爬取(滚动实时) vs 训练 CSV(历史静态)
        source: s.source || 'BUFF',
        priceDate: s.priceDate || null,
        isLive: s.isLive != null ? !!s.isLive : inferIsLive(s.priceDate),
      }));
      reconnectLeaders();
      const prefer = skins.value.find(s => /ak-47|ak47/i.test(s.id || s.name || ''))
        || skins.value.find(s => /awp/i.test(s.id || s.name || ''))
        || skins.value[0];
      if (prefer) selectedSkin.value = prefer;
      return true;
    };

    // priceDate 距今 ≤7 天视为实时数据(与后端 isLive 口径一致;旧后端无该字段时前端兜底)
    const inferIsLive = (priceDate) => {
      if (!priceDate) return true;
      const d = new Date(priceDate);
      if (Number.isNaN(d.getTime())) return true;
      return (Date.now() - d.getTime()) <= 7 * 24 * 60 * 60 * 1000;
    };

    const inferCategory = (s) => {
      const text = `${s.weaponType || ''} ${s.name || ''} ${s.id || ''}`.toLowerCase();
      if (/knife|bayonet|karambit|butterfly|talon|stiletto|navaja|skeleton|falchion|bowie|★/.test(text)) return '刀具';
      if (/glove|hand wraps|wraps/.test(text)) return '手套';
      if (/case|container/.test(text)) return '箱子';
      if (/^awp|ssg|scar|g3sg1/.test(text) || /\bawp\b|\bssg\b/.test(text)) return '狙击枪';
      if (/ak-47|ak47|m4a1|m4a4|famas|galil|aug|sg 553|sg553/.test(text)) return '步枪';
      return s.category || '手枪';
    };

    const loadPredictions = async (skinId) => {
      const client = api();
      if (!client || !skinId) return;
      try {
        const res = await client.predict(skinId, 7);
        const curUsd = res.currentPrice
          ?? res.currentPriceUsd
          ?? selectedSkin.value?.price
          ?? 0;
        // 后端返回 USD 预测价;LSTM 系列(v5 契约)带 dailyPrices = 7 天逐日精确预测
        modelPredictions.value = (res.predictions || []).map(p => {
          const change = +(p.change || 0);
          const price = (p.price != null && p.price > 0)
            ? +(+p.price).toFixed(2)
            : +(curUsd * (1 + change / 100)).toFixed(2);
          const daily = (Array.isArray(p.dailyPrices) && p.dailyPrices.length)
            ? p.dailyPrices.map(v => +(+v).toFixed(4))
            : null;
          return {
            name: p.model,
            type: p.type || 'ML',
            price,
            change: +change.toFixed(2),
            confidence: Math.round(p.confidence || 0),
            daily,
          };
        });
        const levelMap = {
          very_high: '很高', high: '偏高', medium: '中等', low: '偏低',
        };
        predictionMeta.value = {
          consensusScore: Math.round(res.consensus?.score ?? 0),
          consensusLevel: levelMap[res.consensus?.level] || res.consensus?.level || '',
          entryLow: res.entryRange?.low ?? +(curUsd * 0.97).toFixed(2),
          entryHigh: res.entryRange?.high ?? +(curUsd * 0.99).toFixed(2),
          targetPrice: res.targetPrice ?? +(curUsd * 1.05).toFixed(2),
        };
        // 逐日预测主路径: 优先 LSTM(部署主力),否则任一带 daily 的模型
        const withDaily = modelPredictions.value.filter(p => p.daily && p.daily.length);
        const primary = withDaily.find(p => /lstm/i.test(p.name)) || withDaily[0] || null;
        predictionDaily.value = primary
          ? { model: primary.name, base: curUsd, prices: primary.daily }
          : null;
        return res;
      } catch (err) {
        console.warn('[CSVest] predict failed', err);
        predictionDaily.value = null;
        return null;
      }
    };

    const connectBackend = async () => {
      const client = api();
      if (!client) {
        console.warn('[CSVest] js/api.js not loaded');
        return false;
      }
      const isLocalHost = (h) => !h || h === 'localhost' || h === '127.0.0.1';
      const isStaticPages = (() => {
        const h = (location.hostname || '').toLowerCase();
        return h.endsWith('github.io') || h.endsWith('gitlab.io') || h.endsWith('pages.dev');
      })();
      try {
        // 与 js/api.js 一致：公网同源 /api（nginx 反代）；Pages 无反代；忽略误存的 localhost
        const remote = !isLocalHost(location.hostname);
        let apiBase = localStorage.getItem('sv_api_url') || '';
        if (remote) {
          try {
            if (apiBase) {
              const u = new URL(apiBase, location.href);
              if (isLocalHost(u.hostname)) apiBase = '';
            }
          } catch (_) {
            apiBase = '';
          }
          // GitHub Pages 等静态站：没有 /api 反代，未配置公网 API 时保持 Mock
          if (isStaticPages && !apiBase) {
            client.setUseMock(true);
            apiOnline.value = false;
            console.info('[CSVest] static Pages: no public API configured, using mock');
            return false;
          }
          // 其它公网页默认同源；仍兼容显式配置的公网 API
          if (!apiBase) apiBase = '';
        } else if (!apiBase) {
          apiBase = 'http://localhost:8000';
        }
        client.setBaseURL(apiBase);
        client.setUseMock(false);
        await client.health();
        const skinsOk = await loadSkinsFromApi();
        if (!skinsOk) throw new Error('skins empty');
        apiOnline.value = true;
        await Promise.all([
          loadNewsFromApi(),
          loadAlertsFromApi(),
          currentUser.value ? loadPortfolioFromApi() : Promise.resolve(),
          loadModelsFromApi(),
        ]);
        const shown = client.baseURL || location.origin;
        showToast({ title: t('dashboard.connected'), subtitle: `${shown} · ${skins.value.length}`, type: 'success' });
        return true;
      } catch (err) {
        apiOnline.value = false;
        try { client.setUseMock(true); } catch (_) { /* ignore */ }
        console.warn('[CSVest] backend offline, mock data:', err?.message || err);
        return false;
      }
    };

    const reconnectBackend = async () => {
      const ok = await connectBackend();
      if (!ok) {
        showToast({
          title: t('topbar.dataSource.offline'),
          subtitle: t('dashboard.backendHint'),
          type: 'warning',
        });
      }
      return ok;
    };

    const dataSourceLabel = computed(() => {
      if (apiOnline.value) {
        return t('topbar.dataSource.online', { count: skins.value.length || 0 });
      }
      return t('topbar.dataSource.mock');
    });

    const loadNewsFromApi = async () => {
      const client = api();
      if (!client) return;
      try {
        const news = await client.getNews({ limit: 20 });
        const items = Array.isArray(news) ? news : (news?.items || []);
        if (items.length) newsFeed.value = items;
      } catch (_) { /* keep mock */ }
    };

    const loadAlertsFromApi = async () => {
      const client = api();
      if (!client) return;
      try {
        const list = await client.getAlerts();
        const items = Array.isArray(list) ? list : (list?.items || []);
        if (items.length || apiOnline.value) alerts.value = items;
      } catch (_) { /* keep mock */ }
    };

    const loadPortfolioFromApi = async () => {
      const client = api();
      if (!client) return;
      try {
        const res = await client.getPortfolio();
        const items = Array.isArray(res) ? res : (res?.items || []);
        if (items.length || apiOnline.value) {
          // 模拟持仓页：优先展示 holdingType=sim；无标记时兼容旧数据
          const mapped = items.map(p => ({
            id: p.id,
            skinId: p.skinId,
            name: p.name,
            buyPrice: p.buyPrice,
            quantity: p.quantity || 1,
            buyDate: p.buyDate,
            holdingType: p.holdingType || 'sim',
            currentPrice: p.currentPrice,
            pnl: p.pnl,
            pnlPct: p.pnlPct,
          }));
          const simOnly = mapped.filter(p => p.holdingType === 'sim');
          portfolio.value = simOnly.length ? simOnly : mapped;
        }
      } catch (_) { /* keep mock */ }
    };

    const loadInventoryFromApi = async () => {
      const client = api();
      if (!client) return;
      try {
        const res = await client.getInventory();
        const items = Array.isArray(res) ? res : (res?.items || []);
        if (items.length || apiOnline.value) {
          myInventory.value = items.map(p => ({
            id: p.id,
            skinId: p.skinId,
            name: p.name,
            acquirePrice: p.acquirePrice ?? p.buyPrice ?? null,
            quantity: p.quantity || 1,
            acquireDate: p.acquireDate || p.buyDate || '',
            source: p.source || 'manual',
            currentPrice: p.currentPrice,
          }));
        }
      } catch (_) { /* keep mock */ }
    };

    const loadModelsFromApi = async () => {
      const client = api();
      if (!client) return;
      try {
        const cmp = await client.getModelComparison();
        const courseByName = Object.fromEntries(
          (modelComparison.regression || []).map((r) => [r.name, r.course || ''])
        );
        if (cmp?.regression?.length) {
          regressionModels.value = cmp.regression.map((r) => {
            const course = r.course && r.course !== r.type
              ? r.course
              : (courseByName[r.name] || r.course || '');
            return { ...r, course };
          });
        }
        if (cmp?.classification?.length) {
          classificationModels.value = cmp.classification;
        }
        if (cmp?.buyAndHold && typeof cmp.buyAndHold === 'object') {
          modelComparison.buyAndHold = {
            ...modelComparison.buyAndHold,
            ...cmp.buyAndHold,
          };
        }
        // v5 契约: Seq2Seq 多步模型带 perDay 逐日指标(D1..D7)
        modelsPerDay.value = (cmp?.regression || [])
          .filter((r) => Array.isArray(r.perDay) && r.perDay.length)
          .map((r) => ({ name: r.name, perDay: r.perDay }));
      } catch (_) { /* keep mock */ }
    };

    const dailyReport = ref({
      date: '',
      generatedAt: '',
      metrics: { monitored: 20, gainers: 14, losers: 6 },
      aiSummary: '',
      sources: [],
    });
    const explainSummary = ref('');
    const portfolioDiagnose = ref(null);
    const portfolioValueHistory = ref({ dates: [], values: [] });

    const loadDailyReport = async () => {
      const client = api();
      if (!client) return;
      try {
        const rep = await client.getDailyReport();
        if (!rep) return;
        dailyReport.value = {
          date: rep.date || '',
          generatedAt: rep.generatedAt || '',
          metrics: {
            monitored: rep.metrics?.monitored ?? skins.value.length,
            gainers: rep.metrics?.gainers ?? topGainers.value.length,
            losers: rep.metrics?.losers ?? topLosers.value.length,
          },
          aiSummary: rep.aiSummary || rep.summary || '',
          sources: Array.isArray(rep.sources) ? rep.sources : [],
        };
        if (Array.isArray(rep.hotVolume) && rep.hotVolume.length) {
          hotVolume.value = rep.hotVolume;
        } else {
          reconnectLeaders();
        }
        const news = Array.isArray(rep.news) ? rep.news : [];
        if (news.length) newsFeed.value = news;
      } catch (e) {
        console.warn('[CSVest] daily-report failed', e);
      }
    };

    const loadExplanation = async (skinId) => {
      const client = api();
      if (!client || !skinId) return;
      try {
        const exp = await client.getExplanation(skinId, 7);
        explainSummary.value = exp?.summary || '';
        if (Array.isArray(exp?.relatedNews) && exp.relatedNews.length) {
          // 合并到 newsFeed 供 relatedNews computed 使用；同时写临时列表
          relatedNewsOverride.value = exp.relatedNews;
        }
      } catch (_) {
        explainSummary.value = '';
      }
    };
    const relatedNewsOverride = ref(null);

    // ============ RAG 智能问答(市场日报内) ============
    const ragQuery = ref('');
    const ragAnswer = ref('');
    const ragAnswerSources = ref([]);
    const ragLoading = ref(false);
    const ragAsked = ref(false);
    const ragRetrieval = ref({ mode: '', model: null });
    const ragSuggestions = [
      'Major 赛事对饰品价格有什么影响?',
      'StatTrak 版本为什么更贵?',
      '磨损等级怎么影响价格和流动性?',
      '最近有哪些利好/利空消息?',
    ];

    const askRag = async (q) => {
      const query = (q ?? ragQuery.value ?? '').trim();
      if (!query || ragLoading.value) return;
      ragQuery.value = query;
      ragLoading.value = true;
      ragAsked.value = true;
      ragAnswer.value = '';
      ragAnswerSources.value = [];
      try {
        const client = api();
        if (!client) throw new Error('api client missing');
        const res = await client.ragAsk(query, 5);
        ragAnswer.value = res?.answer || '';
        ragAnswerSources.value = Array.isArray(res?.sources) ? res.sources : [];
        ragRetrieval.value = res?.retrieval || { mode: '', model: null };
      } catch (err) {
        console.warn('[rag]', err);
        ragAnswer.value = t('daily.rag.error');
      } finally {
        ragLoading.value = false;
      }
    };

    // 把答案里的 [n] 引用高亮为角标(返回可 v-html 的安全片段)
    const renderCitations = (text) => {
      const esc = String(text || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      return esc.replace(/\[(\d+)\]/g, '<sup class="rag-cite">[$1]</sup>');
    };

    // ============ 管理员端 ============
    const ADMIN_SESSION_KEY = 'sv_admin_session';
    const loadAdminSession = () => {
      try {
        const raw = localStorage.getItem(ADMIN_SESSION_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch { return null; }
    };
    const adminSession = ref(loadAdminSession());
    const adminIsAuthed = computed(() => !!(adminSession.value?.token && adminSession.value?.user?.is_admin));
    const adminLoginForm = ref({ username: 'admin', password: '' });
    const adminLoginError = ref('');
    const adminLoginLoading = ref(false);
    const adminUsers = ref([]);
    const adminConfig = ref(null);
    const adminStatus = ref(null);
    const adminProbeLlm = ref(null);
    const adminProbeEmbed = ref(null);
    const adminSaving = ref(false);
    const adminLoading = ref(false);
    const adminConfigForm = ref({
      deepseekApiKey: '',
      deepseekBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      deepseekModel: 'deepseek-v3',
      dashscopeApiKey: '',
      dashscopeBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      ragEmbedModel: 'text-embedding-v3',
      ragEmbedDim: 1024,
      ragUseVector: true,
    });

    const persistAdminSession = (payload) => {
      adminSession.value = payload;
      if (payload?.token) {
        localStorage.setItem(ADMIN_SESSION_KEY, JSON.stringify(payload));
        localStorage.setItem('sv_token', payload.token);
        try { api()?.setToken?.(payload.token); } catch (_) { /* ignore */ }
      } else {
        localStorage.removeItem(ADMIN_SESSION_KEY);
      }
    };

    const adminLogout = () => {
      persistAdminSession(null);
      adminUsers.value = [];
      adminConfig.value = null;
      adminStatus.value = null;
      adminProbeLlm.value = null;
      adminProbeEmbed.value = null;
    };

    /** 离开独立管理页：有会话则回用户端，否则回 Landing */
    const leaveAdmin = () => {
      const entered = canEnter() && sessionStorage.getItem('sv_entered') === '1';
      currentPage.value = 'dashboard';
      showLanding.value = !entered;
      try {
        if ((location.hash || '').replace(/^#/, '') === 'admin') {
          history.replaceState(null, '', location.pathname + location.search);
        }
      } catch (_) { /* ignore */ }
    };

    const adminLogin = async () => {
      adminLoginLoading.value = true;
      adminLoginError.value = '';
      try {
        const client = api();
        if (!client) throw new Error('api offline');
        const res = await client.login(
          (adminLoginForm.value.username || '').trim(),
          adminLoginForm.value.password || ''
        );
        if (!res?.user?.is_admin) {
          adminLoginError.value = t('admin.err.notAdmin');
          return;
        }
        persistAdminSession({ token: res.token, user: res.user, expiresIn: res.expires_in });
        adminLoginForm.value.password = '';
        showToast({ title: t('admin.toast.loginOk'), type: 'success' });
        await loadAdminPanel();
      } catch (err) {
        adminLoginError.value = err?.message || t('admin.err.login');
      } finally {
        adminLoginLoading.value = false;
      }
    };

    const fillAdminConfigForm = (cfg) => {
      if (!cfg) return;
      adminConfigForm.value = {
        deepseekApiKey: '',
        deepseekBaseUrl: cfg.deepseek?.baseUrl || 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        deepseekModel: cfg.deepseek?.model || 'deepseek-v3',
        dashscopeApiKey: '',
        dashscopeBaseUrl: cfg.dashscope?.baseUrl || 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        ragEmbedModel: cfg.dashscope?.embedModel || 'text-embedding-v3',
        ragEmbedDim: cfg.dashscope?.embedDim || 1024,
        ragUseVector: cfg.dashscope?.useVector !== false,
      };
    };

    const loadAdminPanel = async () => {
      if (!adminIsAuthed.value) return;
      adminLoading.value = true;
      try {
        const client = api();
        if (!client) throw new Error('api offline');
        // 确保 Bearer 用管理员 token
        if (adminSession.value?.token) client.setToken(adminSession.value.token);
        const [usersRes, cfg, status] = await Promise.all([
          client.adminUsers(),
          client.adminGetConfig(),
          client.adminStatus(),
        ]);
        adminUsers.value = Array.isArray(usersRes?.items) ? usersRes.items : [];
        adminConfig.value = cfg;
        adminStatus.value = status;
        fillAdminConfigForm(cfg);
      } catch (err) {
        console.warn('[admin]', err);
        if (String(err?.message || '').includes('403') || err?.status === 403) {
          adminLogout();
          adminLoginError.value = t('admin.err.notAdmin');
        }
        showToast({ title: t('admin.err.load'), subtitle: err?.message || '', type: 'warning' });
      } finally {
        adminLoading.value = false;
      }
    };

    const saveAdminConfig = async () => {
      if (!adminIsAuthed.value) return;
      adminSaving.value = true;
      try {
        const client = api();
        if (!client) throw new Error('api offline');
        if (adminSession.value?.token) client.setToken(adminSession.value.token);
        const f = adminConfigForm.value;
        const body = {
          deepseekBaseUrl: f.deepseekBaseUrl,
          deepseekModel: f.deepseekModel,
          dashscopeBaseUrl: f.dashscopeBaseUrl,
          ragEmbedModel: f.ragEmbedModel,
          ragEmbedDim: Number(f.ragEmbedDim) || 1024,
          ragUseVector: !!f.ragUseVector,
        };
        // 仅当输入了新 Key 才覆盖(空=不改)
        if ((f.deepseekApiKey || '').trim()) body.deepseekApiKey = f.deepseekApiKey.trim();
        if ((f.dashscopeApiKey || '').trim()) body.dashscopeApiKey = f.dashscopeApiKey.trim();
        const res = await client.adminPutConfig(body);
        adminConfig.value = res?.config || res;
        fillAdminConfigForm(adminConfig.value);
        showToast({ title: t('admin.toast.saved'), type: 'success' });
        await refreshAdminStatus();
      } catch (err) {
        showToast({ title: t('admin.err.save'), subtitle: err?.message || '', type: 'warning' });
      } finally {
        adminSaving.value = false;
      }
    };

    const refreshAdminStatus = async () => {
      try {
        const client = api();
        if (!client || !adminIsAuthed.value) return;
        if (adminSession.value?.token) client.setToken(adminSession.value.token);
        adminStatus.value = await client.adminStatus();
      } catch (err) {
        console.warn('[admin-status]', err);
      }
    };

    const runProbeLlm = async () => {
      adminProbeLlm.value = { loading: true };
      try {
        const client = api();
        if (adminSession.value?.token) client.setToken(adminSession.value.token);
        adminProbeLlm.value = await client.adminProbeLlm();
      } catch (err) {
        adminProbeLlm.value = { ok: false, error: err?.message || String(err) };
      }
    };

    const runProbeEmbed = async () => {
      adminProbeEmbed.value = { loading: true };
      try {
        const client = api();
        if (adminSession.value?.token) client.setToken(adminSession.value.token);
        adminProbeEmbed.value = await client.adminProbeEmbed();
      } catch (err) {
        adminProbeEmbed.value = { ok: false, error: err?.message || String(err) };
      }
    };
    const regressionModels = ref(
      (modelComparison.regression || []).map((r) => ({ ...r }))
    );
    const hybridRoute = modelComparison.hybridRoute;
    const classificationModels = ref(modelComparison.classification);
    const modelTypeLabel = (m) => {
      if (!m) return '—';
      if (m.typeKey) {
        const key = 'models.type.' + m.typeKey;
        const label = t(key);
        if (label && label !== key) return label;
      }
      return m.type || '—';
    };
    const suggestedQuestions = window.CSVestData.SUGGESTED_QUESTIONS;
    const debateSuggestedQuestions = window.CSVestData.DEBATE_SUGGESTED_QUESTIONS || [];
    const chatMode = ref('qa'); // 'qa' | 'debate'

    const activeSuggestedQuestions = computed(() => (
      chatMode.value === 'debate' ? debateSuggestedQuestions : suggestedQuestions
    ));

    // ============ 行情看板 ============
    const filterCategory = ref('all');
    const skinSearch = ref('');
    const skinSort = ref('change7d');
    const categoryKeys = ['all', 'rifle', 'sniper', 'pistol', 'knife', 'gloves', 'case'];
    // 中文类别 → i18n key 映射
    const categoryMap = {
      '步枪': 'rifle',
      '狙击枪': 'sniper',
      '手枪': 'pistol',
      '刀具': 'knife',
      '手套': 'gloves',
      '箱子': 'case',
    };

    const categoryLabel = (cat) => {
      if (!cat) return '';
      const key = categoryMap[cat] || (categoryKeys.includes(cat) ? cat : null);
      if (!key) return cat;
      return t('dashboard.category.' + key);
    };

    const formatChange = (num) => {
      const v = Number(num);
      if (!Number.isFinite(v)) return '0.00%';
      const sign = v > 0 ? '+' : '';
      return `${sign}${v.toFixed(2)}%`;
    };

    const formatVolume = (num) => {
      const v = Number(num);
      if (!Number.isFinite(v) || v <= 0) return '0';
      if (v >= 1000000) return `${(v / 1000000).toFixed(1).replace(/\.0$/, '')}M`;
      if (v >= 10000) return `${(v / 1000).toFixed(1).replace(/\.0$/, '')}k`;
      return Math.round(v).toLocaleString('en-US');
    };

    const marketPulse = computed(() => {
      const list = skins.value || [];
      let up = 0;
      let down = 0;
      let sum = 0;
      let live = 0;
      for (const s of list) {
        const ch = Number(s.change7d) || 0;
        if (ch > 0) up += 1;
        else if (ch < 0) down += 1;
        sum += ch;
        if (s.isLive !== false) live += 1;
      }
      return {
        total: list.length,
        live,
        up,
        down,
        avg: list.length ? sum / list.length : 0,
      };
    });

    // 800+ 件全量渲染会卡顿：默认渲染前 SKIN_PAGE_SIZE 条，点“加载更多”翻页
    const SKIN_PAGE_SIZE = 60;
    const skinDisplayLimit = ref(SKIN_PAGE_SIZE);

    const filteredSkins = computed(() => {
      let list = skins.value || [];
      if (filterCategory.value !== 'all') {
        const zhLabel = Object.keys(categoryMap).find(k => categoryMap[k] === filterCategory.value);
        list = list.filter(s => {
          const cat = s.category || inferCategory(s);
          return cat === zhLabel || categoryMap[cat] === filterCategory.value;
        });
      }
      const q = skinSearch.value.trim().toLowerCase();
      if (q) {
        list = list.filter(s => {
          const hay = `${s.name || ''} ${s.wear || ''} ${s.category || ''} ${categoryLabel(s.category)}`.toLowerCase();
          return hay.includes(q);
        });
      }
      const sorted = [...list];
      const sort = skinSort.value;
      sorted.sort((a, b) => {
        if (sort === 'name') return (a.name || '').localeCompare(b.name || '', 'en');
        if (sort === 'price') return (Number(b.price) || 0) - (Number(a.price) || 0);
        if (sort === 'volume') return (Number(b.volume24h) || 0) - (Number(a.volume24h) || 0);
        if (sort === 'liquidity') return (Number(b.liquidity) || 0) - (Number(a.liquidity) || 0);
        return (Number(b.change7d) || 0) - (Number(a.change7d) || 0);
      });
      return sorted;
    });

    // 实际渲染的分页切片(全量数据仍保留在 filteredSkins 供计数/导出)
    const visibleSkins = computed(() => filteredSkins.value.slice(0, skinDisplayLimit.value));
    const hasMoreSkins = computed(() => filteredSkins.value.length > skinDisplayLimit.value);
    const remainingSkins = computed(() => Math.max(filteredSkins.value.length - skinDisplayLimit.value, 0));
    const showMoreSkins = () => {
      skinDisplayLimit.value += SKIN_PAGE_SIZE * 2;
    };
    // 筛选/搜索/排序变化时回到第一页
    watch([filterCategory, skinSearch, skinSort], () => {
      skinDisplayLimit.value = SKIN_PAGE_SIZE;
    });

    const refreshData = async () => {
      if (!apiOnline.value) {
        const ok = await reconnectBackend();
        if (ok) return;
      }
      if (apiOnline.value) {
        try {
          await loadSkinsFromApi();
          if (selectedSkin.value?.id) await loadPredictions(selectedSkin.value.id);
          // 看板刷新顺带对前 N 个饰品抓真实市场价(行内“实时”标签展示)
          refreshMarketLive();
          showToast({ title: t('dashboard.connected'), type: 'success' });
          return;
        } catch (err) {
          console.warn('[CSVest] refresh failed', err);
          apiOnline.value = false;
          try { api()?.setUseMock(true); } catch (_) { /* ignore */ }
          showToast({
            title: t('topbar.dataSource.offline'),
            subtitle: err?.message || '',
            type: 'warning',
          });
        }
      }
      // Mock 波动兜底
      skins.value = skins.value.map(s => ({
        ...s,
        change24h: s.change24h + (Math.random() - 0.5) * 0.5,
        change7d: s.change7d + (Math.random() - 0.5) * 0.3,
        price: s.price * (1 + (Math.random() - 0.5) * 0.01),
      }));
      reconnectLeaders();
    };

    // ============ AI 预测详情 ============
    const selectedSkin = ref(skins.value[0]);
    const klineChart = ref(null);
    const timeframe = ref('90D');
    const klineLoading = ref(false);
    let klineChartInstance = null;
    const modelPredictions = ref([]);
    // v5 契约: LSTM 系列返回 7 天逐日精确预测 { model, base(决策日价), prices[7] }
    const predictionDaily = ref(null);
    const predictionMeta = ref({
      consensusScore: 76,
      consensusLevel: '',
      entryLow: 0,
      entryHigh: 0,
      targetPrice: 0,
    });
    const platformQuotes = ref([]);
    const platformQuotesLoading = ref(false);
    const platformQuotesMeta = ref({ mode: '', spread: null, fetchedAt: '' });

    const PLATFORM_LABELS = {
      buff: 'BUFF',
      skinport: 'Skinport',
      steam: 'Steam',
      waxpeer: 'Waxpeer',
      marketcsgo: 'Market.CSGO',
      lootfarm: 'Loot.farm',
      csgotrader: 'CSGOTrader',
      csfloat: 'CSFloat',
    };
    const platformLabel = (key) => PLATFORM_LABELS[key] || key;

    const platformQuotesSorted = computed(() => {
      return [...platformQuotes.value].sort((a, b) => {
        if (a.ok && b.ok) return (a.price ?? 0) - (b.price ?? 0);
        if (a.ok) return -1;
        if (b.ok) return 1;
        return String(a.platform).localeCompare(String(b.platform));
      });
    });

    // 报价基准 = 当前展示报价的中位数(自洽), 避免用合成量纲的库内价导致离谱百分比
    const platformQuotesRef = computed(() => {
      const prices = platformQuotes.value
        .filter(q => q.ok && q.price != null)
        .map(q => Number(q.price))
        .sort((a, b) => a - b);
      if (!prices.length) return null;
      const mid = Math.floor(prices.length / 2);
      return prices.length % 2 ? prices[mid] : (prices[mid - 1] + prices[mid]) / 2;
    });

    // 是否已切到真实市场价(点“刷新”后)
    const platformQuotesLive = computed(() => platformQuotesMeta.value.mode === 'live');

    // 实时均价 = 各平台真实报价的算术平均(仅 live 模式有效)
    const livePriceAvg = computed(() => {
      if (platformQuotesMeta.value.mode !== 'live') return null;
      const prices = platformQuotes.value
        .filter(q => q.ok && q.price != null)
        .map(q => Number(q.price));
      if (!prices.length) return null;
      return prices.reduce((a, b) => a + b, 0) / prices.length;
    });

    const loadPlatformQuotes = async (skinId, { live = false } = {}) => {
      if (!skinId) return;
      platformQuotesLoading.value = true;
      try {
        const client = api();
        if (!client) throw new Error('api client missing');
        // 默认演示价(与 App 价同量纲, 秒开); 点“刷新”才抓真实市场价(免登录平台)
        const data = await client.getPlatformQuotes(skinId, { live });
        platformQuotes.value = Array.isArray(data?.quotes) ? data.quotes : [];
        platformQuotesMeta.value = {
          mode: data?.mode || '',
          spread: data?.spread || null,
          fetchedAt: data?.fetchedAt || '',
        };
      } catch (err) {
        console.warn('[quotes]', err);
        platformQuotes.value = [];
        platformQuotesMeta.value = { mode: '', spread: null, fetchedAt: '' };
      } finally {
        platformQuotesLoading.value = false;
      }
    };

    const refreshPlatformQuotes = () => {
      if (selectedSkin.value?.id) loadPlatformQuotes(selectedSkin.value.id, { live: true });
    };

    // 看板行内“实时价”缓存: { [skinId]: { price, at } }
    const marketLiveQuotes = ref({});
    const marketLiveLoading = ref(false);

    const _quotesMedian = (quotes) => {
      const prices = (quotes || [])
        .filter(q => q.ok && q.price != null)
        .map(q => Number(q.price))
        .sort((a, b) => a - b);
      if (!prices.length) return null;
      const mid = Math.floor(prices.length / 2);
      return prices.length % 2 ? prices[mid] : (prices[mid - 1] + prices[mid]) / 2;
    };

    // 行情中心刷新: 对看板靠前的前 N 个饰品抓真实市场价(并发有限, 90s 缓存兜底)
    const refreshMarketLive = async (limit = 12) => {
      const client = api();
      if (!client || !apiOnline.value) return;
      const targets = (filteredSkins.value || []).slice(0, limit).filter(s => s?.id);
      if (!targets.length) return;
      marketLiveLoading.value = true;
      try {
        // 有限并发(4), 避免同时打爆各平台全表接口(Skinport 限流很严)
        const map = { ...marketLiveQuotes.value };
        const now = Date.now();
        let hits = 0;
        const pool = 4;
        const queue = [...targets];
        const worker = async () => {
          while (queue.length) {
            const s = queue.shift();
            if (!s) break;
            try {
              const d = await client.getPlatformQuotes(s.id, { live: true });
              const median = _quotesMedian(d?.quotes);
              if (median != null) {
                map[s.id] = { price: median, at: now };
                hits += 1;
              }
            } catch (_) { /* 单个失败不影响其余 */ }
          }
        };
        await Promise.all(Array.from({ length: Math.min(pool, targets.length) }, worker));
        marketLiveQuotes.value = { ...map };
        showToast({
          title: t('dashboard.liveDone', { count: hits }),
          type: hits ? 'success' : 'warning',
        });
      } catch (err) {
        console.warn('[market-live]', err);
      } finally {
        marketLiveLoading.value = false;
      }
    };

    // 7 天逐日预测明细行(供预测页逐日面板渲染)
    const predictionDailyRows = computed(() => {
      const dp = predictionDaily.value;
      if (!dp?.prices?.length) return [];
      const baseDateRaw = selectedSkin.value?.priceDate;
      const baseDate = baseDateRaw ? new Date(baseDateRaw) : new Date();
      const anchor = Number.isNaN(baseDate.getTime()) ? new Date() : baseDate;
      return dp.prices.map((price, i) => {
        const d = new Date(anchor.getTime() + (i + 1) * 24 * 60 * 60 * 1000);
        const change = dp.base > 0 ? ((price - dp.base) / dp.base) * 100 : 0;
        return {
          day: i + 1,
          date: `${d.getMonth() + 1}/${d.getDate()}`,
          price: +(+price).toFixed(2),
          change: +change.toFixed(2),
        };
      });
    });

    const syncPredictionMetaFromSkin = (skin) => {
      const price = skin?.price || 0;
      predictionMeta.value = {
        consensusScore: predictionMeta.value.consensusScore || 76,
        consensusLevel: predictionMeta.value.consensusLevel || '',
        entryLow: +(price * 0.97).toFixed(2),
        entryHigh: +(price * 0.99).toFixed(2),
        targetPrice: +(price * 1.05).toFixed(2),
      };
    };

    const viewSkin = (skinId) => {
      const skin = skins.value.find(s => s.id === skinId);
      if (skin) {
        selectedSkin.value = skin;
        currentPage.value = 'prediction';
        loadPlatformQuotes(skinId, { live: true });
      }
    };

    const roundTitle = (idx) => {
      return ['独立分析', '互相质疑', '达成共识'][idx];
    };

    // 相关新闻
    const relatedNews = computed(() => {
      if (relatedNewsOverride.value?.length) {
        return relatedNewsOverride.value.slice(0, 4);
      }
      if (!selectedSkin.value) return [];
      return newsFeed.value.filter(n =>
        !n.relatedSkins?.length || n.relatedSkins.includes(selectedSkin.value.id)
      ).slice(0, 4);
    });

    const newsIcon = (sentiment) => {
      if (sentiment === 'positive') return '📈';
      if (sentiment === 'negative') return '📉';
      return '📰';
    };

    const openExternalUrl = (url) => {
      const u = (url || '').trim();
      if (!u) return;
      try {
        const parsed = new URL(u, location.href);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return;
        const w = window.open(parsed.href, '_blank', 'noopener,noreferrer');
        // 部分环境拦截 window.open:退化为同页跳转提示
        if (!w) {
          const a = document.createElement('a');
          a.href = parsed.href;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
      } catch (_) { /* ignore bad urls */ }
    };

    // K线图渲染（优先后端真实 K 线 + 模型预测）
    const renderKline = async () => {
      if (!klineChart.value || !selectedSkin.value) return;

      klineLoading.value = true;
      klineChartInstance = getOrCreateChart(klineChartInstance, klineChart.value);

      const days = { '7D': 7, '30D': 30, '90D': 90, '180D': 180 }[timeframe.value] || 90;
      let kline = [];
      let volumes = [];
      let ma7 = [];
      let ma30 = [];
      let predChange = 0.02;

      try {
        const client = api();
        if (client && apiOnline.value) {
          const [kl, pred] = await Promise.all([
            client.getKLine(selectedSkin.value.id, days),
            loadPredictions(selectedSkin.value.id),
          ]);
          // K 线与列表价统一为 USD
          kline = (kl.data || []).map(d => [
            d.date,
            +(+d.open).toFixed(2),
            +(+d.close).toFixed(2),
            +(+d.low).toFixed(2),
            +(+d.high).toFixed(2),
          ]);
          volumes = (kl.volumes || []).map((v, i) => [i, v.volume, v.direction]);
          ma7 = (kl.ma7 || []).map(v => v == null ? '-' : +(+v).toFixed(2));
          ma30 = (kl.ma30 || []).map(v => v == null ? '-' : +(+v).toFixed(2));
          // 用全模型涨跌幅中位数，抗单模型（如 LSTM）离群值
          const changes = (pred?.predictions || [])
            .map(p => p.change)
            .filter(c => c != null && isFinite(c))
            .sort((a, b) => a - b);
          if (changes.length) {
            const mid = Math.floor(changes.length / 2);
            const median = changes.length % 2
              ? changes[mid]
              : (changes[mid - 1] + changes[mid]) / 2;
            predChange = median / 100;
          }
        }
      } catch (err) {
        console.warn('[CSVest] kline api failed, mock fallback', err);
      }

      if (!kline.length) {
        const mock = window.CSVestData.generateKLineData(
          selectedSkin.value.price,
          days,
          selectedSkin.value.category === '箱子' ? 0.02 : 0.035
        );
        kline = mock.kline;
        volumes = mock.volumes;
        ma7 = window.CSVestData.calculateMA(kline, 7);
        ma30 = window.CSVestData.calculateMA(kline, 30);
        if (!modelPredictions.value.length) {
          const base = selectedSkin.value.price;
          modelPredictions.value = [
            { name: 'ARIMA', type: '统计', price: +(base * 1.012).toFixed(2), change: 1.2, confidence: 65 },
            { name: 'XGBoost', type: 'ML', price: +(base * 1.018).toFixed(2), change: 1.8, confidence: 78 },
            { name: 'LightGBM', type: 'ML', price: +(base * 1.016).toFixed(2), change: 1.6, confidence: 76 },
            { name: 'Random Forest', type: 'ML', price: +(base * 1.014).toFixed(2), change: 1.4, confidence: 72 },
            { name: 'LSTM', type: 'DL ⭐', price: +(base * 1.025).toFixed(2), change: 2.5, confidence: 82 },
            { name: 'GRU', type: 'DL', price: +(base * 1.022).toFixed(2), change: 2.2, confidence: 80 },
          ];
          syncPredictionMetaFromSkin(selectedSkin.value);
        }
      }

      const lastClose = parseFloat(kline[kline.length - 1][2]);
      const predictedDates = [];
      const predictedValues = [];
      const dailyPath = predictionDaily.value;
      const horizon = (dailyPath?.prices?.length) || 7;
      // 预测日期从最后一根 K 线的日期顺延，而不是从今天开始（历史数据可能止于更早日期）
      const lastLabel = String(kline[kline.length - 1][0]);
      const [lm, ld] = lastLabel.split('/').map(Number);
      const baseDate = (lm >= 1 && lm <= 12 && ld >= 1 && ld <= 31)
        ? new Date(new Date().getFullYear(), lm - 1, ld)
        : new Date();
      for (let i = 1; i <= horizon; i++) {
        const d = new Date(baseDate.getTime() + i * 24 * 60 * 60 * 1000);
        predictedDates.push(`${d.getMonth() + 1}/${d.getDate()}`);
      }
      if (dailyPath?.prices?.length && dailyPath.base > 0) {
        // v5 契约: LSTM 逐日精确预测。按决策日价 → 最后收盘价的比例锚定,
        // 保持模型给出的逐日相对涨跌形状(决策日与最新 K 线可能相差数日)
        for (const p of dailyPath.prices) {
          predictedValues.push((lastClose * (p / dailyPath.base)).toFixed(2));
        }
      } else {
        // 无逐日数据(旧模型/树模型)时退回合成路径:
        // 简单确定性伪随机（按饰品 id 播种），避免每次渲染曲线抖动
        let seed = 0;
        for (const ch of String(selectedSkin.value.id || '')) seed = (seed * 31 + ch.charCodeAt(0)) % 997;
        const rand = () => {
          seed = (seed * 137 + 71) % 997;
          return seed / 997 - 0.5;
        };
        // 缓动逼近目标价 + 小幅波动，模拟逐日预测路径而非直线
        const dailyVol = Math.min(0.012, Math.abs(predChange) * 0.35 + 0.003);
        for (let i = 1; i <= horizon; i++) {
          const t = i / horizon;
          const eased = 1 - Math.pow(1 - t, 2); // ease-out：前快后缓
          const wiggle = i === horizon ? 0 : rand() * dailyVol;
          predictedValues.push((lastClose * (1 + predChange * eased + wiggle)).toFixed(2));
        }
      }

      // 成交量按类目轴对齐：历史有值，预测区间留空，避免 [index,vol,dir] 与日期类目错位
      const forecastPad = predictedDates.map(() => '-');
      const volumeBars = volumes.map((v) => {
        const vol = Array.isArray(v) ? v[1] : v;
        const dir = Array.isArray(v) ? v[2] : 0;
        return {
          value: vol,
          itemStyle: {
            color: dir > 0 ? '#ef4444' : '#10b981',
            opacity: 0.6,
          },
        };
      }).concat(forecastPad);

      const option = {
        backgroundColor: 'transparent',
        animation: false,
        legend: {
          data: ['K线', 'MA7', 'MA30', 'AI 预测'],
          textStyle: { color: '#9ca3af', fontSize: 11 },
          top: 0,
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross' },
          backgroundColor: '#1f2937',
          borderColor: '#374151',
          textStyle: { color: '#f3f4f6' },
        },
        axisPointer: {
          link: [{ xAxisIndex: 'all' }],
          label: { backgroundColor: '#ff6b00' },
        },
        grid: [
          { left: 52, right: 16, top: 40, height: '58%' },
          { left: 52, right: 16, top: '76%', height: '14%' },
        ],
        xAxis: [
          {
            type: 'category',
            data: kline.map(d => d[0]).concat(predictedDates),
            // 与成交量轴保持同一 boundaryGap，否则 K 线与 Volume 会左右错位
            boundaryGap: true,
            axisLine: { lineStyle: { color: '#374151' } },
            axisLabel: { color: '#9ca3af', fontSize: 10 },
            splitLine: { show: false },
          },
          {
            type: 'category',
            gridIndex: 1,
            data: kline.map(d => d[0]).concat(predictedDates),
            boundaryGap: true,
            axisLine: { lineStyle: { color: '#374151' } },
            axisLabel: { show: false },
            splitLine: { show: false },
          },
        ],
        yAxis: [
          {
            scale: true,
            splitArea: { show: false },
            axisLine: { lineStyle: { color: '#374151' } },
            axisLabel: { color: '#9ca3af', fontSize: 10 },
            splitLine: { lineStyle: { color: '#2a3447', type: 'dashed' } },
          },
          {
            scale: true,
            gridIndex: 1,
            splitNumber: 2,
            axisLine: { lineStyle: { color: '#374151' } },
            axisLabel: { color: '#9ca3af', fontSize: 10 },
            splitLine: { show: false },
          },
        ],
        dataZoom: [
          { type: 'inside', xAxisIndex: [0, 1], start: 50, end: 100 },
        ],
        series: [
          {
            name: 'K线',
            type: 'candlestick',
            // ECharts candlestick: [open, close, low, high]
            data: kline.map(d => [d[1], d[2], d[3], d[4]]),
            itemStyle: {
              color: '#ef4444',
              color0: '#10b981',
              borderColor: '#ef4444',
              borderColor0: '#10b981',
            },
          },
          {
            name: 'MA7',
            type: 'line',
            data: ma7.concat(forecastPad),
            smooth: true,
            showSymbol: false,
            lineStyle: { color: '#fbbf24', width: 1 },
          },
          {
            name: 'MA30',
            type: 'line',
            data: ma30.concat(forecastPad),
            smooth: true,
            showSymbol: false,
            lineStyle: { color: '#8b5cf6', width: 1 },
          },
          {
            name: 'AI 预测',
            type: 'line',
            data: new Array(kline.length - 1).fill('-').concat([kline[kline.length - 1][2]]).concat(predictedValues),
            smooth: true,
            showSymbol: false,
            lineStyle: { color: '#ff6b00', width: 2, type: 'dashed' },
            areaStyle: {
              color: {
                type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(255, 107, 0, 0.3)' },
                  { offset: 1, color: 'rgba(255, 107, 0, 0)' },
                ],
              },
            },
            markArea: {
              itemStyle: { color: 'rgba(255, 107, 0, 0.05)' },
              data: [[
                { xAxis: kline[kline.length - 1][0] },
                { xAxis: predictedDates[predictedDates.length - 1] },
              ]],
            },
          },
          {
            name: 'Volume',
            type: 'bar',
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumeBars,
            barMaxWidth: 10,
          },
        ],
      };
      klineChartInstance.setOption(option, true);
      klineLoading.value = false;
    };

    // ============ AI 对话 ============
    const chatMessages = ref([
      {
        role: 'assistant',
        content: '__WELCOME__',
        time: '刚刚',
        model: 'DeepSeek-V3',
      }
    ]);
    const chatInput = ref('');
    const chatLoading = ref(false);
    const chatMessagesEl = ref(null);
    const chatSuggestedIndex = ref(-1);

    const setChatMode = (mode) => {
      chatMode.value = mode === 'debate' ? 'debate' : 'qa';
      chatSuggestedIndex.value = -1;
    };

    const chatNow = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

    const SKIN_ALIASES = [
      { keys: ['火蛇', 'fireserpent', 'fire serpent', 'ak47-fireserpent'], idHint: 'fireserpent' },
      { keys: ['龙狙', 'dragonlore', 'dragon lore', 'awp-dragonlore'], idHint: 'dragonlore' },
      { keys: ['红线', 'redline', 'ak47-redline'], idHint: 'redline' },
      { keys: ['asiimov', '二西莫夫', 'awp-asiimov'], idHint: 'asiimov' },
      { keys: ['蝴蝶刀', 'butterfly'], idHint: 'butterfly' },
      { keys: ['多普勒', 'doppler'], idHint: 'doppler' },
    ];

    const resolveSkinFromQuery = (query) => {
      const q = String(query || '').trim().toLowerCase();
      const list = skins.value || [];
      if (!q) return selectedSkin.value || null;

      const byId = list.find(s => String(s.id || '').toLowerCase() === q);
      if (byId) return byId;

      for (const alias of SKIN_ALIASES) {
        if (alias.keys.some(k => q.includes(k))) {
          const hit = list.find(s => String(s.id || '').toLowerCase().includes(alias.idHint)
            || String(s.name || '').toLowerCase().includes(alias.idHint));
          if (hit) return hit;
        }
      }

      // 名称子串匹配：优先更长命中
      let best = null;
      let bestScore = 0;
      for (const s of list) {
        const name = String(s.name || '').toLowerCase();
        const id = String(s.id || '').toLowerCase();
        if (!name && !id) continue;
        if (q.includes(name) || name.includes(q) || q.includes(id) || id.includes(q.replace(/\s+/g, '-'))) {
          const score = Math.max(name.length, id.length);
          if (score > bestScore) {
            best = s;
            bestScore = score;
          }
        }
      }
      if (best) return best;

      // 泛化请求 + 已选饰品
      if (selectedSkin.value && /(辩论|debate|多空|牛熊|这个|当前|开始)/i.test(q)) {
        return selectedSkin.value;
      }
      return selectedSkin.value || null;
    };

    const canSendChat = computed(() => {
      if (chatLoading.value) return false;
      const text = chatInput.value.trim();
      if (chatMode.value === 'debate') {
        return !!(text || selectedSkin.value);
      }
      return !!text;
    });

    const ensureDebateLoaded = async (skin) => {
      if (!skin?.id) return null;
      await loadDebate(skin.id);
      return debateData.value;
    };

    const sendDebateMessage = async (overrideText) => {
      const raw = (typeof overrideText === 'string' ? overrideText : chatInput.value).trim();
      const skin = resolveSkinFromQuery(raw);
      const displayText = raw || (skin ? t('chat.debateHintSkin', { name: skin.name }).replace(/；.*/, '') : '');

      if (!skin) {
        chatMessages.value.push({
          role: 'user',
          content: raw || t('chat.startDebate'),
          time: chatNow(),
        });
        chatInput.value = '';
        chatMessages.value.push({
          role: 'assistant',
          content: t('chat.debateNeedSkin'),
          time: chatNow(),
          model: 'CSVest',
        });
        await scrollChatBottom();
        return;
      }

      chatMessages.value.push({
        role: 'user',
        content: displayText || t('chat.debateHintSkin', { name: skin.name }),
        time: chatNow(),
      });
      chatInput.value = '';
      chatLoading.value = true;
      await scrollChatBottom();

      try {
        const data = await ensureDebateLoaded(skin);
        chatMessages.value.push({
          role: 'assistant',
          type: 'debate',
          content: `${t('prediction.debateTitle')} · ${skin.name}`,
          debate: {
            skin: data?.skin || skin.name,
            currentPrice: data?.currentPrice ?? skin.price,
            rounds: data?.rounds || [],
            consensus: data?.consensus || {
              recommendation: '观望',
              entryRange: '—',
              stopLoss: '—',
              targetPrice: '—',
              risks: [],
            },
          },
          time: chatNow(),
          model: apiOnline.value ? 'Bull/Bear Agents' : 'Mock Debate',
        });
      } catch (e) {
        chatMessages.value.push({
          role: 'assistant',
          content: t('chat.debateNeedSkin'),
          time: chatNow(),
          model: 'Mock',
        });
      }
      chatLoading.value = false;
      await scrollChatBottom();
    };

    const sendMessage = async (overrideText) => {
      if (chatMode.value === 'debate') {
        await sendDebateMessage(overrideText);
        return;
      }

      const text = (typeof overrideText === 'string' ? overrideText : chatInput.value).trim();
      if (!text || chatLoading.value) return;

      chatMessages.value.push({
        role: 'user',
        content: text,
        time: chatNow(),
      });
      chatInput.value = '';
      chatLoading.value = true;
      await scrollChatBottom();

      const assistantMsg = {
        role: 'assistant',
        content: '',
        time: chatNow(),
        model: apiOnline.value ? 'DeepSeek-V3' : 'Mock',
      };
      chatMessages.value.push(assistantMsg);

      try {
        const client = api();
        if (client && apiOnline.value) {
          await client.chat(text, null, (chunk) => {
            assistantMsg.content += chunk;
            scrollChatBottom();
          });
          if (!assistantMsg.content.trim()) {
            assistantMsg.content = generateAIResponse(text);
          }
        } else {
          // 离线：模拟延迟后本地回复
          await new Promise(r => setTimeout(r, 600));
          assistantMsg.content = generateAIResponse(text);
          assistantMsg.model = 'Mock';
        }
      } catch (e) {
        assistantMsg.content = generateAIResponse(text);
        assistantMsg.model = 'Mock';
      }
      chatLoading.value = false;
      await scrollChatBottom();
    };

    // 监听聊天输入框的键盘事件
    const onChatKeydown = (e) => {
      const suggestions = activeSuggestedQuestions.value;
      // 输入框为空时,支持上下方向键选择建议问题
      if (!chatInput.value && chatMessages.value.length <= 1) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          chatSuggestedIndex.value = Math.min(chatSuggestedIndex.value + 1, suggestions.length - 1);
          if (chatSuggestedIndex.value < 0) chatSuggestedIndex.value = 0;
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          chatSuggestedIndex.value = Math.max(chatSuggestedIndex.value - 1, 0);
        } else if (e.key === 'Enter' && chatSuggestedIndex.value >= 0) {
          e.preventDefault();
          sendMessage(suggestions[chatSuggestedIndex.value]);
          chatSuggestedIndex.value = -1;
          return;
        }
      }
      // 默认 Enter 发送
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (canSendChat.value) sendMessage();
        chatSuggestedIndex.value = -1;
      }
    };

    const askQuestion = (q) => {
      sendMessage(q);
      chatSuggestedIndex.value = -1;
    };

    const generateAIResponse = (query) => {
      const q = query.toLowerCase();
      if (q.includes('火蛇') || q.includes('ak47-fireserpent') || q.includes('ak-47') && q.includes('火')) {
        return window.CSVestData.AI_PRESET_RESPONSES['ak47-fireserpent-fn'];
      }
      if (q.includes('龙狙') || q.includes('dragonlore') || q.includes('awp')) {
        return window.CSVestData.AI_PRESET_RESPONSES['awp-dragonlore-ft'];
      }
      if (q.includes('5000') || q.includes('700') || q.includes('预算') || q.includes('推荐')) {
        return `根据您的 **$700 预算 + 中等风险** 偏好,推荐以下组合:

**🥇 首选组合 (稳健型):**
1. **AK-47 | Redline (FT)** × 5 件 = $298
   - 高流动性 (98分)、价格稳定、社区共识度高
2. **AWP | Asiimov (FT)** × 2 件 = $356
   - 中等价位、波动率适中、模型预测 +3.21% (7天)
3. **Dreams & Nightmares Case** × 30 件 = $38
   - 极致流动性、开箱期望值高

**总投入: $692** | **预期 30 天收益: +5%~+8%**

**⚠️ 风险提示:**
- 历史回测仅供参考,实际收益受市场波动影响
- 建议分批建仓,避免追高
- 设置止损位 -8%

是否需要我深入分析某个具体饰品?`;
      }
      if (q.includes('涨') || q.includes('今天')) {
        return `今日涨幅榜 Top 3:

1. **AWP | Dragon Lore (FT)** +8.45% ($5,943)
   - 驱动: 职业选手偏好 + IEM Cologne 预期

2. **M9 Bayonet | Doppler (FN)** +7.89% ($1,778)
   - 驱动: 刀具市场整体回暖

3. **AK-47 | Fire Serpent (FN)** +5.67% ($2,201)
   - 驱动: Valve 更新未削弱 + 赛事需求

**📊 整体市场情绪:** 贪婪(指数 68)
**🔥 热点板块:** 高端饰品 ($1.5k+) 持续走强

需要我分析某个具体饰品吗?`;
      }
      if (q.includes('长期') || q.includes('值得')) {
        return `长期持有价值分析 (基于 LSTM 30 天预测):

**⭐ 强烈推荐:**
1. **AWP | Dragon Lore (FT)** - 30天预期 +13.4%
   - 不可再生产(已停售)、职业比赛使用率高
2. **AK-47 | Fire Serpent (FN)** - 30天预期 +7.2%
   - IEM 赛事临近 + 历史表现强势

**👍 中等推荐:**
3. **M4A1-S | Printstream (FT)** - 30天预期 +5.8%
   - Printstream 系列持续受社区追捧

**⚠️ 谨慎:**
- **手套类** - 流动性下降,短期承压
- **箱子类** - 受掉宝率影响波动大

需要我给出具体入场策略吗?`;
      }
      if (q.includes('预警') || q.includes('设置')) {
        return `我可以帮您设置价格预警!请告诉我:

1. 📦 关注的饰品 (如 "AK-47 火蛇")
2. 🎯 目标价格 (如 $2,200)
3. 📈 触发条件 (涨破 / 跌破)

您也可以前往左侧菜单 **🔔 价格预警** 页面直接创建。`;
      }
      if (q.includes('模型') || q.includes('对比')) {
        return `模型对比结果 (来自 5.1-5.3 节):

**📊 回归模型 (RMSE 越低越好):**
| 模型 | RMSE | R² | 收益 |
|------|------|-----|------|
| **LSTM** ⭐ | 76.45 | 0.92 | +23.5% |
| GRU | 81.23 | 0.91 | +21.8% |
| XGBoost | 89.12 | 0.89 | +18.7% |
| 买入持有基准 | — | — | +9.8% |

**🎯 分类模型:**
- XGBoost 准确率 71%, AUC 0.76 (最佳)
- LightGBM 紧随其后,训练速度极快

**结论:** LSTM 在回归任务中表现最优,XGBoost 在分类中胜出。建议生产环境使用 XGBoost (速度+可解释性) + LSTM (高精度预测) 的 Stacking 集成。`;
      }
      return window.CSVestData.AI_PRESET_RESPONSES['default'];
    };

    // Markdown 缓存,避免重复解析
    const markdownCache = new Map();

    const renderMarkdown = (content) => {
      if (!content || content === '__WELCOME__') return '';
      // 命中缓存
      if (markdownCache.has(content)) return markdownCache.get(content);
      // 未加载 marked 时降级显示原文
      if (typeof marked === 'undefined' || typeof marked.parse !== 'function') {
        return escapeHtml(content).replace(/\n/g, '<br>');
      }
      try {
        const html = marked.parse(content);
        markdownCache.set(content, html);
        return html;
      } catch (e) {
        console.error('Markdown 解析失败:', e);
        return escapeHtml(content).replace(/\n/g, '<br>');
      }
    };

    const escapeHtml = (str) => {
      if (str === null || str === undefined) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    };

    // 包装 t 函数,避免 undefined
    const tSafe = (key, params) => {
      try {
        return t(key, params);
      } catch (e) {
        console.error('i18n error:', key, e);
        return key;
      }
    };

    const scrollChatBottom = async () => {
      await nextTick();
      if (chatMessagesEl.value) {
        chatMessagesEl.value.scrollTop = chatMessagesEl.value.scrollHeight;
      }
    };

    // ============ 预警 ============
    const alerts = ref([
      { id: 1, skinId: 'ak47-fireserpent-fn', skinName: 'AK-47 | Fire Serpent (FN)', type: 'above', targetPrice: 2291.67, currentPrice: 2201.39, active: true, triggered: false, createdAt: '2026-07-10 14:23' },
      { id: 2, skinId: 'awp-dragonlore-ft', skinName: 'AWP | Dragon Lore (FT)', type: 'above', targetPrice: 6250.00, currentPrice: 5942.78, active: true, triggered: true, createdAt: '2026-07-08 09:15' },
      { id: 3, skinId: 'm4a1s-printstream-ft', skinName: 'M4A1-S | Printstream (FT)', type: 'below', targetPrice: 125.00, currentPrice: 136.11, active: true, triggered: false, createdAt: '2026-07-12 16:40' },
      { id: 4, skinId: 'gloves-pandora-ft', skinName: '★ Sport Gloves | Pandora\'s Box (FT)', type: 'below', targetPrice: 2777.78, currentPrice: 3138.89, active: false, triggered: false, createdAt: '2026-07-05 11:20' },
    ]);

    const showAlertModal = ref(false);
    const newAlert = ref({ skinId: '', type: 'above', targetPrice: null, note: '' });

    const addAlert = async () => {
      if (!newAlert.value.skinId || !newAlert.value.targetPrice) return;
      const skin = skins.value.find(s => s.id === newAlert.value.skinId);
      const payload = {
        skinId: newAlert.value.skinId,
        type: newAlert.value.type,
        targetPrice: +newAlert.value.targetPrice,
        note: newAlert.value.note || '',
      };
      try {
        const client = api();
        if (client && apiOnline.value) {
          const created = await client.createAlert(payload);
          await loadAlertsFromApi();
          if (!alerts.value.find(a => a.id === created.id)) {
            alerts.value = [{
              id: created.id,
              skinId: created.skinId || payload.skinId,
              skinName: skin?.name || '',
              type: created.type || payload.type,
              targetPrice: created.targetPrice || payload.targetPrice,
              currentPrice: skin?.price || 0,
              active: true,
              triggered: false,
              createdAt: new Date().toLocaleString('zh-CN'),
            }, ...alerts.value];
          }
        } else {
          alerts.value.push({
            id: Date.now(),
            skinId: payload.skinId,
            skinName: skin?.name || '',
            type: payload.type,
            targetPrice: payload.targetPrice,
            currentPrice: skin?.price || 0,
            active: true,
            triggered: false,
            createdAt: new Date().toLocaleString('zh-CN'),
          });
        }
        showToast({ title: t('common.confirm'), subtitle: skin?.name || '', type: 'success' });
      } catch (e) {
        showToast({ title: '创建预警失败', subtitle: e.message || '', type: 'error' });
      }
      showAlertModal.value = false;
      newAlert.value = { skinId: '', type: 'above', targetPrice: null, note: '' };
    };

    const deleteAlert = async (id) => {
      try {
        const client = api();
        if (client && apiOnline.value) {
          await client.deleteAlert(id);
        }
        alerts.value = alerts.value.filter(a => a.id !== id);
        showToast({ title: t('common.delete'), type: 'success' });
      } catch (e) {
        showToast({ title: '删除失败', subtitle: e.message || '', type: 'error' });
      }
    };

    // ============ 持仓 / 库存 ============
    const portfolioTab = ref('inventory'); // inventory | sim
    const portfolio = ref([...window.CSVestData.DEFAULT_PORTFOLIO]);
    const showPortfolioModal = ref(false);
    const newPortfolio = ref({ skinId: '', buyPrice: null, quantity: 1, buyDate: new Date().toISOString().slice(0, 10), holdingType: 'sim' });

    const myInventory = ref([...(window.CSVestData.DEFAULT_INVENTORY || [])]);
    const showInventoryModal = ref(false);
    const newInventory = ref({
      skinId: '',
      acquirePrice: 0,
      quantity: 1,
      acquireDate: new Date().toISOString().slice(0, 10),
      source: 'manual',
    });
    const selectedInventoryItem = ref(null);
    const inventoryMenuId = ref(null);
    const showInventoryEditModal = ref(false);
    const editingInventory = ref({ id: null, name: '', acquirePrice: 0 });
    // Steam 库存导入
    const showSteamImportModal = ref(false);
    const steamImportLoading = ref(false);
    const steamImportForm = ref({ steamUrl: '', cookie: '' });
    const steamImportResult = ref(null);
    const inventoryValueHistory = ref({ dates: [], values: [], predictedDates: [], predictedValues: [], total: 0 });
    const inventoryValueChart = ref(null);
    let inventoryValueChartInstance = null;

    const getSkinMeta = (skinId) => skins.value.find(s => s.id === skinId) || null;

    const getSkinImage = (skinId) => getSkinMeta(skinId)?.image || '🎯';

    const getSkinChange24h = (skinId) => {
      const ch = Number(getSkinMeta(skinId)?.change24h);
      return Number.isFinite(ch) ? ch : 0;
    };

    const getCurrentPrice = (skinId) => {
      const fromPortfolio = portfolio.value.find(p => p.skinId === skinId);
      if (fromPortfolio?.currentPrice != null) return fromPortfolio.currentPrice;
      const fromInv = myInventory.value.find(p => p.skinId === skinId);
      if (fromInv?.currentPrice != null) return fromInv.currentPrice;
      return skins.value.find(s => s.id === skinId)?.price || 0;
    };

    const getItemPnl = (item) => {
      if (item.pnl != null) return item.pnl;
      const current = getCurrentPrice(item.skinId);
      return (current - item.buyPrice) * item.quantity;
    };

    const getItemPnlPct = (item) => {
      if (item.pnlPct != null) return item.pnlPct;
      return ((getCurrentPrice(item.skinId) - item.buyPrice) / item.buyPrice) * 100;
    };

    const portfolioMetrics = computed(() => {
      const prices = {};
      portfolio.value.forEach(p => prices[p.skinId] = getCurrentPrice(p.skinId));
      if (window.CSVestData?.calculateRiskMetrics) {
        return window.CSVestData.calculateRiskMetrics(portfolio.value, prices);
      }
      const totalCost = portfolio.value.reduce((s, p) => s + p.buyPrice * p.quantity, 0);
      const totalValue = portfolio.value.reduce((s, p) => s + getCurrentPrice(p.skinId) * p.quantity, 0);
      const pnl = totalValue - totalCost;
      return {
        totalCost: +totalCost.toFixed(2),
        totalValue: +totalValue.toFixed(2),
        pnl: +pnl.toFixed(2),
        pnlPct: totalCost ? +((pnl / totalCost) * 100).toFixed(2) : 0,
        sharpeRatio: '—',
        maxDrawdown: '—',
        volatility: '—',
      };
    });

    const inventoryItemCount = computed(() =>
      myInventory.value.reduce((s, p) => s + (p.quantity || 1), 0)
    );

    const inventoryTotalValue = computed(() =>
      myInventory.value.reduce((s, p) => s + getCurrentPrice(p.skinId) * (p.quantity || 1), 0)
    );

    /** 库存总价值较昨日涨跌（按市值加权） */
    const inventoryTotalChange24h = computed(() => {
      let weight = 0;
      let weighted = 0;
      myInventory.value.forEach((item) => {
        const value = getCurrentPrice(item.skinId) * (item.quantity || 1);
        if (!value) return;
        weight += value;
        weighted += value * getSkinChange24h(item.skinId);
      });
      if (!weight) return 0;
      return +(weighted / weight).toFixed(2);
    });

    const inventorySourceLabel = (source, short = false) => {
      if (source === 'steam') return short ? 'Steam' : t('inventory.source.steam');
      return short ? t('inventory.source.manual.short') : t('inventory.source.manual');
    };

    const renderInventoryValueChart = () => {
      if (!inventoryValueChart.value) return;
      inventoryValueChartInstance = getOrCreateChart(inventoryValueChartInstance, inventoryValueChart.value);
      const hist = inventoryValueHistory.value || {};
      const dates = hist.dates || [];
      const values = hist.values || [];
      const predictedDates = hist.predictedDates || [];
      const predictedValues = hist.predictedValues || [];
      const lastValue = values.length ? values[values.length - 1] : null;
      const forecastSeries = lastValue == null
        ? []
        : new Array(Math.max(dates.length - 1, 0)).fill('-').concat([lastValue], predictedValues);

      inventoryValueChartInstance.setOption({
        backgroundColor: 'transparent',
        animation: true,
        legend: {
          data: [t('inventory.valueTrend'), 'AI 预测'],
          textStyle: { color: '#9ca3af', fontSize: 11 },
          top: 0,
        },
        grid: { left: 52, right: 16, top: 36, bottom: 32 },
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#1f2937',
          borderColor: '#374151',
          textStyle: { color: '#f3f4f6' },
          valueFormatter: (v) => (v == null || v === '-' ? '-' : `$${Number(v).toFixed(2)}`),
        },
        xAxis: {
          type: 'category',
          data: dates.concat(predictedDates),
          boundaryGap: false,
          axisLabel: { color: '#9ca3af', fontSize: 10 },
          axisLine: { lineStyle: { color: '#374151' } },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { color: '#9ca3af', fontSize: 10, formatter: (v) => `$${v}` },
          splitLine: { lineStyle: { color: '#2a3447' } },
        },
        series: [
          {
            name: t('inventory.valueTrend'),
            type: 'line',
            data: values.concat(predictedDates.map(() => '-')),
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 2.5, color: '#ff6b00' },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(255,107,0,0.28)' },
                { offset: 1, color: 'rgba(255,107,0,0.02)' },
              ]),
            },
          },
          {
            name: 'AI 预测',
            type: 'line',
            data: forecastSeries,
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 2.5, color: '#22c792', type: 'dashed' },
            markLine: predictedDates.length ? {
              symbol: 'none',
              label: { show: true, formatter: '预测', color: '#9ca3af', fontSize: 10 },
              lineStyle: { color: '#6b7280', type: 'dashed' },
              data: [{ xAxis: dates[dates.length - 1] }],
            } : undefined,
          },
        ],
      }, true);
      allowPageScrollOverChart(inventoryValueChartInstance);
    };

    const refreshInventoryCharts = async () => {
      if (!currentUser.value) return;
      const client = api();
      try {
        if (client) {
          const hist = await client.getInventoryValueHistory(90);
          inventoryValueHistory.value = hist || { dates: [], values: [], predictedDates: [], predictedValues: [], total: 0 };
        } else if (window.CSVestData.generateInventoryValueHistory) {
          inventoryValueHistory.value = window.CSVestData.generateInventoryValueHistory(myInventory.value, 90);
        }
      } catch (_) {
        if (window.CSVestData.generateInventoryValueHistory) {
          inventoryValueHistory.value = window.CSVestData.generateInventoryValueHistory(myInventory.value, 90);
        }
      }
      nextTick(() => {
        renderInventoryValueChart();
        inventoryValueChartInstance?.resize();
      });
    };

    const setPortfolioTab = (tab) => {
      portfolioTab.value = tab;
      inventoryMenuId.value = null;
      if (tab === 'inventory' && currentUser.value) {
        loadInventoryFromApi().then(() => refreshInventoryCharts());
      } else if (tab === 'sim') {
        loadPortfolioFromApi();
        loadPortfolioExtras();
      }
    };

    const openInventoryItem = (item) => {
      inventoryMenuId.value = null;
      if (item?.skinId) viewSkin(item.skinId);
    };

    const toggleInventoryMenu = (id) => {
      inventoryMenuId.value = inventoryMenuId.value === id ? null : id;
    };

    const closeInventoryMenu = () => {
      inventoryMenuId.value = null;
    };

    const openEditInventoryPrice = (item) => {
      if (!item) return;
      inventoryMenuId.value = null;
      editingInventory.value = {
        id: item.id,
        name: item.name,
        acquirePrice: item.acquirePrice != null ? +item.acquirePrice : 0,
      };
      showInventoryEditModal.value = true;
    };

    const saveInventoryPrice = async () => {
      if (!requireInventoryLogin()) return;
      const id = editingInventory.value.id;
      if (id == null) return;
      const price = Number(editingInventory.value.acquirePrice);
      const nextPrice = Number.isFinite(price) ? price : 0;
      const idx = myInventory.value.findIndex(p => p.id === id);
      if (idx >= 0) {
        myInventory.value[idx] = {
          ...myInventory.value[idx],
          acquirePrice: nextPrice,
        };
        // 预留：后端对接后在此调用 PATCH /api/inventory/{id}
        showToast({ title: t('inventory.updated'), subtitle: editingInventory.value.name || '', type: 'success' });
        await refreshInventoryCharts();
      }
      showInventoryEditModal.value = false;
      editingInventory.value = { id: null, name: '', acquirePrice: 0 };
    };

    const addPortfolio = async () => {
      if (!newPortfolio.value.skinId || !newPortfolio.value.buyPrice) return;
      const skin = skins.value.find(s => s.id === newPortfolio.value.skinId);
      const payload = {
        skinId: newPortfolio.value.skinId,
        buyPrice: +newPortfolio.value.buyPrice,
        quantity: +newPortfolio.value.quantity || 1,
        buyDate: newPortfolio.value.buyDate,
        holdingType: 'sim',
      };
      try {
        const client = api();
        if (client && apiOnline.value) {
          await client.addPortfolioItem(payload);
          await loadPortfolioFromApi();
        } else {
          portfolio.value.push({
            id: Date.now(),
            ...payload,
            name: skin?.name || '',
          });
        }
        showToast({ title: t('portfolio.addHolding'), subtitle: skin?.name || '', type: 'success' });
      } catch (e) {
        showToast({ title: '添加持仓失败', subtitle: e.message || '', type: 'error' });
      }
      showPortfolioModal.value = false;
      newPortfolio.value = { skinId: '', buyPrice: null, quantity: 1, buyDate: new Date().toISOString().slice(0, 10), holdingType: 'sim' };
    };

    const removePortfolio = async (id) => {
      try {
        const client = api();
        if (client && apiOnline.value) {
          await client.deletePortfolioItem(id);
        }
        portfolio.value = portfolio.value.filter(p => p.id !== id);
        showToast({ title: t('portfolio.close'), type: 'success' });
      } catch (e) {
        showToast({ title: '平仓失败', subtitle: e.message || '', type: 'error' });
      }
    };

    const addInventoryItem = async () => {
      if (!requireInventoryLogin()) return;
      if (!newInventory.value.skinId) return;
      const skin = skins.value.find(s => s.id === newInventory.value.skinId);
      const rawPrice = newInventory.value.acquirePrice;
      const payload = {
        skinId: newInventory.value.skinId,
        acquirePrice: rawPrice != null && rawPrice !== '' && Number.isFinite(+rawPrice) ? +rawPrice : 0,
        quantity: +newInventory.value.quantity || 1,
        acquireDate: newInventory.value.acquireDate,
        source: 'manual',
      };
      try {
        const client = api();
        if (client && apiOnline.value) {
          await client.addInventoryItem(payload);
          await loadInventoryFromApi();
        } else {
          myInventory.value.push({
            id: Date.now(),
            ...payload,
            name: skin?.name || '',
          });
        }
        showToast({ title: t('inventory.added'), subtitle: skin?.name || '', type: 'success' });
        await refreshInventoryCharts();
      } catch (e) {
        showToast({ title: t('common.error'), subtitle: e.message || '', type: 'error' });
      }
      showInventoryModal.value = false;
      newInventory.value = {
        skinId: '',
        acquirePrice: 0,
        quantity: 1,
        acquireDate: new Date().toISOString().slice(0, 10),
        source: 'manual',
      };
    };

    const removeInventoryItem = async (id) => {
      if (!requireInventoryLogin()) return;
      inventoryMenuId.value = null;
      try {
        const client = api();
        if (client && apiOnline.value) {
          await client.deleteInventoryItem(id);
        }
        myInventory.value = myInventory.value.filter(p => p.id !== id);
        if (selectedInventoryItem.value?.id === id) selectedInventoryItem.value = null;
        showToast({ title: t('inventory.removed'), type: 'success' });
        await refreshInventoryCharts();
      } catch (e) {
        showToast({ title: t('common.error'), subtitle: e.message || '', type: 'error' });
      }
    };

    const importSteamInventory = () => {
      if (!requireInventoryLogin()) return;
      inventoryMenuId.value = null;
      steamImportForm.value = { steamUrl: '', cookie: '' };
      steamImportResult.value = null;
      showSteamImportModal.value = true;
    };

    const submitSteamImport = async () => {
      if (!steamImportForm.value.steamUrl.trim()) {
        showToast({ title: t('inventory.steamModal.failed'), subtitle: t('inventory.steamModal.urlRequired'), type: 'error' });
        return;
      }
      const client = api();
      if (!client || !apiOnline.value) {
        showToast({ title: t('inventory.steamModal.failed'), subtitle: t('network.offline') || '后端未连接', type: 'error' });
        return;
      }
      steamImportLoading.value = true;
      steamImportResult.value = null;
      try {
        const res = await client.importSteamInventory({
          steamUrl: steamImportForm.value.steamUrl.trim(),
          cookie: steamImportForm.value.cookie.trim(),
        });
        steamImportResult.value = res;
        await loadInventoryFromApi();
        await refreshInventoryCharts();
        const imported = res?.imported ?? 0;
        const skipped = res?.skipped ?? 0;
        const unmatched = res?.unmatched?.length ?? 0;
        showToast({
          title: t('inventory.steamModal.success'),
          subtitle: t('inventory.steamModal.summary', { imported, skipped, unmatched }),
          type: imported > 0 ? 'success' : 'info',
        });
      } catch (e) {
        const msg = e?.message || '';
        let title = t('inventory.steamModal.failed');
        if (/403|private|私有|cookie/i.test(msg)) title = t('inventory.steamModal.private');
        else if (/429|限流|rate/i.test(msg)) title = t('inventory.steamModal.rateLimited');
        else if (/400|格式|链接/i.test(msg)) title = t('inventory.steamModal.badUrl');
        showToast({ title, subtitle: msg, type: 'error' });
      } finally {
        steamImportLoading.value = false;
      }
    };

    const loadPortfolioExtras = async () => {
      const client = api();
      if (!client || !apiOnline.value) return;
      try {
        const [hist, diag] = await Promise.all([
          client.getPortfolioValueHistory(90),
          client.diagnosePortfolio(),
        ]);
        portfolioValueHistory.value = hist || { dates: [], values: [] };
        portfolioDiagnose.value = diag;
      } catch (e) {
        console.warn('[CSVest] portfolio extras failed', e);
      }
    };

    // ============ 模型实验室图表 ============
    const radarChart = ref(null);
    const backtestChart = ref(null);
    const shapChart = ref(null);
    const perDayChart = ref(null);
    // v5 契约: LSTM 系列 Seq2Seq Dense(7) 的逐日(D1-D7)误差指标
    const modelsPerDay = ref([]);
    const perDayMetric = ref('rmse'); // rmse | mae | mape
    let radarInstance = null, backtestInstance = null, shapInstance = null, perDayInstance = null;

    const renderRadar = () => {
      if (!radarChart.value) return;
      radarInstance = getOrCreateChart(radarInstance, radarChart.value);
      const narrow = typeof window !== 'undefined' && window.innerWidth <= 768;

      // 从当前 ML 输出动态计算雷达分数，避免硬编码指标与重训结果脱节
      const wanted = ['LSTM-C', 'Hybrid', 'Random Forest', 'XGBoost'];
      const rows = wanted
        .map(name => regressionModels.value.find(r => r.name === name))
        .filter(Boolean);
      const maxRmse = Math.max(...rows.map(r => Number(r.rmse) || 0), 1);
      const maxReturn = Math.max(...rows.map(r => Math.abs(Number(r.returnPct) || 0)), 1);
      const speedScore = (speed) => {
        const s = String(speed || '').toLowerCase();
        if (/极快|very fast/.test(s)) return 100;
        if (/快|fast/.test(s)) return 82;
        if (/中|medium/.test(s)) return 58;
        return 30;
      };
      const colors = [
        ['#ff6b00', 'rgba(255, 107, 0, 0.18)'],
        ['#06b6d4', 'rgba(6, 182, 212, 0.15)'],
        ['#8b5cf6', 'rgba(139, 92, 246, 0.15)'],
        ['#3b82f6', 'rgba(59, 130, 246, 0.15)'],
      ];
      const radarData = rows.map((r, i) => {
        const rmseScore = Math.max(0, 100 - (Number(r.rmse) || maxRmse) / maxRmse * 55);
        const r2Score = Math.max(0, Math.min(100, (Number(r.r2) || 0) * 100));
        const explainScore = Math.max(20, Math.min(100, (Number(r.interpretability) || 1) / 3 * 100));
        const returnScore = Math.max(0, Math.min(100, Math.abs(Number(r.returnPct) || 0) / maxReturn * 100));
        const generalize = (rmseScore + r2Score) / 2;
        return {
          value: [
            +rmseScore.toFixed(1),
            speedScore(r.speed),
            +explainScore.toFixed(1),
            +returnScore.toFixed(1),
            +r2Score.toFixed(1),
            +generalize.toFixed(1),
          ],
          name: r.name,
          areaStyle: { color: colors[i][1] },
          lineStyle: { color: colors[i][0], width: 2 },
          itemStyle: { color: colors[i][0] },
        };
      });

      const option = {
        backgroundColor: 'transparent',
        tooltip: { backgroundColor: '#1f2937', borderColor: '#374151', textStyle: { color: '#f3f4f6' } },
        legend: {
          data: rows.map(r => r.name),
          textStyle: { color: '#9ca3af', fontSize: narrow ? 10 : 11 },
          top: narrow ? undefined : 0,
          bottom: narrow ? 0 : undefined,
          type: 'scroll',
          width: narrow ? '90%' : undefined,
        },
        radar: {
          indicator: [
            { name: t('models.radar.rmse'), max: 100 },
            { name: t('models.radar.speed'), max: 100 },
            { name: t('models.radar.explain'), max: 100 },
            { name: t('models.radar.return'), max: 100 },
            { name: t('models.radar.r2'), max: 100 },
            { name: t('models.radar.generalize'), max: 100 },
          ],
          center: ['50%', narrow ? '52%' : '58%'],
          radius: narrow ? '52%' : '58%',
          axisName: { color: '#9ca3af', fontSize: narrow ? 10 : 11 },
          splitLine: { lineStyle: { color: '#2a3447' } },
          splitArea: { areaStyle: { color: ['rgba(255,107,0,0.02)', 'rgba(255,107,0,0.05)'] } },
          axisLine: { lineStyle: { color: '#374151' } },
        },
        series: [{
          type: 'radar',
          data: radarData,
        }],
      };
      radarInstance.setOption(option, true);
      allowPageScrollOverChart(radarInstance);
    };

    const renderBacktest = async () => {
      if (!backtestChart.value) return;
      backtestInstance = getOrCreateChart(backtestInstance, backtestChart.value);

      let dates = [];
      let seriesMap = {};
      try {
        const client = api();
        if (client && apiOnline.value) {
          const bt = await client.getBacktest(90);
          dates = bt.dates || [];
          seriesMap = bt.series || {};
        }
      } catch (_) { /* mock below */ }
      if (!dates.length || !Object.keys(seriesMap).length) {
        seriesMap = window.CSVestData.generateBacktestData(60);
        dates = Array.from({ length: 60 }, (_, i) => {
          const d = new Date(Date.now() - (60 - i) * 24 * 60 * 60 * 1000);
          return `${d.getMonth() + 1}/${d.getDate()}`;
        });
      }

      const palette = ['#ff6b00', '#3b82f6', '#06b6d4', '#8b5cf6', '#f59e0b', '#ec4899', '#10b981'];
      const names = Object.keys(seriesMap);
      const series = names.map((name, i) => {
        const isBench = /buy|hold|持有/i.test(name);
        return {
          name,
          type: 'line',
          data: seriesMap[name],
          smooth: true,
          showSymbol: false,
          lineStyle: {
            color: isBench ? '#9ca3af' : palette[i % palette.length],
            width: isBench ? 2 : (/hybrid|lstm/i.test(name) ? 3 : 2),
            type: isBench ? 'dashed' : 'solid',
          },
          itemStyle: { color: isBench ? '#9ca3af' : palette[i % palette.length] },
          emphasis: { focus: 'series' },
        };
      });

      const option = {
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: '#1f2937', borderColor: '#374151', textStyle: { color: '#f3f4f6' } },
        legend: {
          data: names,
          textStyle: { color: '#9ca3af', fontSize: 11 },
          top: 0,
          type: 'scroll',
        },
        grid: { left: 60, right: 30, top: 40, bottom: 30 },
        xAxis: {
          type: 'category',
          data: dates,
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: 10 },
        },
        yAxis: {
          type: 'value',
          name: '净值',
          nameTextStyle: { color: '#6b7280', fontSize: 10 },
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: 10, formatter: '{value}' },
          splitLine: { lineStyle: { color: '#2a3447', type: 'dashed' } },
        },
        series,
      };
      backtestInstance.setOption(option, true);
      allowPageScrollOverChart(backtestInstance);
    };

    // Seq2Seq Dense(7) 逐日误差曲线: 展示 LSTM 系列 D1→D7 的误差递增趋势
    const renderPerDay = () => {
      if (!perDayChart.value) return;
      if (!modelsPerDay.value.length) {
        try { perDayInstance?.dispose(); } catch (_) { /* ignore */ }
        perDayInstance = null;
        return;
      }
      perDayInstance = getOrCreateChart(perDayInstance, perDayChart.value);
      const metric = perDayMetric.value;
      const days = modelsPerDay.value[0].perDay.map(d => `D${d.day}`);
      const palette = ['#ff6b00', '#06b6d4', '#8b5cf6', '#3b82f6', '#10b981'];
      const series = modelsPerDay.value.map((m, i) => ({
        name: m.name,
        type: 'line',
        data: m.perDay.map(d => (d[metric] != null ? +Number(d[metric]).toFixed(4) : null)),
        smooth: true,
        symbolSize: 6,
        lineStyle: { color: palette[i % palette.length], width: 2 },
        itemStyle: { color: palette[i % palette.length] },
        emphasis: { focus: 'series' },
      }));
      perDayInstance.setOption({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: '#1f2937', borderColor: '#374151', textStyle: { color: '#f3f4f6' } },
        legend: {
          data: modelsPerDay.value.map(m => m.name),
          textStyle: { color: '#9ca3af', fontSize: 11 },
          top: 0,
          type: 'scroll',
        },
        grid: { left: 56, right: 24, top: 40, bottom: 30 },
        xAxis: {
          type: 'category',
          data: days,
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: 10 },
        },
        yAxis: {
          type: 'value',
          scale: true,
          name: metric.toUpperCase(),
          nameTextStyle: { color: '#6b7280', fontSize: 10 },
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: 10 },
          splitLine: { lineStyle: { color: '#2a3447', type: 'dashed' } },
        },
        series,
      }, true);
      allowPageScrollOverChart(perDayInstance);
    };

    const setPerDayMetric = (metric) => {
      perDayMetric.value = metric;
      renderPerDay();
    };

    const renderShap = async () => {
      if (!shapChart.value) return;
      shapInstance = getOrCreateChart(shapInstance, shapChart.value);

      let rows = [];
      try {
        const client = api();
        if (client && apiOnline.value) {
          const shap = await client.getShap('xgboost');
          rows = (Array.isArray(shap) ? shap : []).map(d => ({
            name: d.feature || d.name,
            value: d.importance ?? d.value ?? 0,
          }));
        }
      } catch (_) { /* mock */ }
      if (!rows.length) {
        rows = (window.CSVestData.SHAP_FEATURES || []).map(d => ({ name: d.name, value: d.value }));
      }
      const data = rows.slice().sort((a, b) => a.value - b.value);

      const option = {
        backgroundColor: 'transparent',
        tooltip: { backgroundColor: '#1f2937', borderColor: '#374151', textStyle: { color: '#f3f4f6' } },
        grid: { left: 130, right: 30, top: 20, bottom: 30 },
        xAxis: {
          type: 'value',
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: 10 },
          splitLine: { lineStyle: { color: '#2a3447', type: 'dashed' } },
        },
        yAxis: {
          type: 'category',
          data: data.map(d => d.name),
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: 11 },
        },
        series: [{
          type: 'bar',
          data: data.map((d, i) => ({
            value: d.value,
            itemStyle: {
              color: i % 2 === 0 ? '#ff6b00' : '#ff8c3a',
              borderRadius: [0, 4, 4, 0],
            },
          })),
          barWidth: 18,
          label: {
            show: true,
            position: 'right',
            color: '#9ca3af',
            fontSize: 10,
            formatter: (p) => Number(p.value).toFixed(3),
          },
        }],
      };
      shapInstance.setOption(option, true);
      allowPageScrollOverChart(shapInstance);
    };

    // ============ 工具函数 ============
    const formatPrice = (num) => {
      if (num === null || num === undefined) return '0.00';
      return Number(num).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    };

    // 渲染 Phosphor SVG 图标 (内嵌,不依赖字体)
    const renderIcon = (name, options = {}) => {
      try {
        if (typeof window === 'undefined' || !window.renderPhosphorIcon) return '';
        return window.renderPhosphorIcon(name, options);
      } catch (e) {
        console.warn('renderIcon error:', name, e);
        return '';
      }
    };

    // ============ 命令面板 (Ctrl+K) ============
    const showCommandPalette = ref(false);
    const commandQuery = ref('');
    const commandSelected = ref(0);
    const commandInput = ref(null);

    const commands = computed(() => {
      const q = commandQuery.value.toLowerCase().trim();
      const match = (text) => !q || text.toLowerCase().includes(q);

      const groups = [];

      // 页面命令
      const pageCmds = menu.value
        .filter(m => match(m.label))
        .map((m, i) => ({
          id: `page-${m.id}`,
          icon: m.icon,
          title: m.label,
          subtitle: t('cmd.pageDesc', { name: m.label }),
          kbd: String(i + 1),
          action: () => { goToPage(m.id); },
        }));
      if (pageCmds.length) groups.push({ title: t('cmd.group.pages'), items: pageCmds });

      // 饰品命令
      const skinCmds = skins.value
        .filter(s => match(s.name) || match(s.category))
        .slice(0, 8)
        .map(s => ({
          id: `skin-${s.id}`,
          icon: s.image,
          title: s.name,
          subtitle: `${s.category} · $${formatPrice(s.price)} · 7d ${s.change7d >= 0 ? '+' : ''}${s.change7d.toFixed(2)}%`,
          kbd: '',
          action: () => { viewSkin(s.id); },
        }));
      if (skinCmds.length) groups.push({ title: t('cmd.group.skins'), items: skinCmds });

      // 操作命令
      const actionCmds = [
        { id: 'act-theme', icon: theme.value === 'dark' ? 'sun' : 'moon', iconStyle: 'duotone', title: t('cmd.action.theme'), subtitle: `${t('cmd.action.themeCurrent')}: ${theme.value === 'dark' ? t('theme.dark') : t('theme.light')}`, kbd: 'Ctrl+Shift+L', action: toggleTheme },
        { id: 'act-help', icon: 'keyboard', iconStyle: 'duotone', title: t('cmd.action.help'), subtitle: t('shortcut.title'), kbd: '?', action: () => { showShortcutHelp.value = true; } },
        { id: 'act-export-skins', icon: 'download-simple', iconStyle: 'duotone', title: t('cmd.action.export'), subtitle: 'CSV / JSON', kbd: '', action: () => exportData('skins', 'csv') },
        { id: 'act-refresh', icon: 'arrows-clockwise', iconStyle: 'duotone', title: t('cmd.action.refresh'), subtitle: '', kbd: '', action: refreshData },
        { id: 'act-alert', icon: 'bell-ringing', iconStyle: 'duotone', title: t('cmd.action.alert'), subtitle: t('menu.alerts'), kbd: '', action: () => { currentPage.value = 'alerts'; setTimeout(() => showAlertModal.value = true, 100); } },
      ].filter(c => match(c.title) || match(c.subtitle));
      if (actionCmds.length) groups.push({ title: t('cmd.group.actions'), items: actionCmds });

      return groups;
    });

    const commandResults = computed(() => commands.value);

    const executeCommand = (item) => {
      item.action();
      showCommandPalette.value = false;
      commandQuery.value = '';
      commandSelected.value = 0;
    };

    const onCommandKeydown = (e) => {
      const allItems = commandResults.value.flatMap(g => g.items);
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        commandSelected.value = (commandSelected.value + 1) % Math.max(allItems.length, 1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        commandSelected.value = (commandSelected.value - 1 + allItems.length) % Math.max(allItems.length, 1);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const item = allItems.find(i => i.id === `cmd-${commandSelected.value}`) || allItems[commandSelected.value];
        if (item) executeCommand(item);
      } else if (e.key === 'Escape') {
        showCommandPalette.value = false;
      }
    };

    watch(commandQuery, () => { commandSelected.value = 0; });
    watch(showCommandPalette, (show) => {
      if (show) {
        nextTick(() => commandInput.value?.focus());
      }
    });

    // ============ 快捷键系统 ============
    const showShortcutHelp = ref(false);
    const handleGlobalKeydown = (e) => {
      if (showLanding.value) return;
      // 忽略在输入框中的按键
      const tag = e.target.tagName;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || e.target.isContentEditable) {
        if (e.key === 'Escape') e.target.blur();
        return;
      }

      // Ctrl+K 命令面板
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        showCommandPalette.value = !showCommandPalette.value;
        return;
      }
      // Ctrl+Shift+L 切换主题
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'l') {
        e.preventDefault();
        toggleTheme();
        return;
      }
      // ? 显示快捷键
      if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        showShortcutHelp.value = !showShortcutHelp.value;
        return;
      }
      // Esc 关闭弹窗
      if (e.key === 'Escape') {
        if (showCommandPalette.value) { showCommandPalette.value = false; return; }
        if (showShortcutHelp.value) { showShortcutHelp.value = false; return; }
        if (showAlertModal.value) { showAlertModal.value = false; return; }
        if (showPortfolioModal.value) { showPortfolioModal.value = false; return; }
      }
      // 数字键切换页面 (无 Toast,直接跳转)
      if (!e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
        const num = parseInt(e.key);
        if (num >= 1 && num <= menu.value.length) {
          e.preventDefault();
          const target = menu.value[num - 1];
          goToPage(target.id);
          // 不弹 Toast,避免用户困惑
        }
      }
    };

    // ============ 生命周期 ============
    onMounted(async () => {
      await nextTick();
      // 移除首次加载遮罩 (CSS 动画 0.6s 后自动隐藏,这里做兜底)
      setTimeout(() => {
        const loader = document.getElementById('app-loader');
        if (loader) loader.classList.add('hidden');
      }, 300);

      // 自动探测后端；通了就切真实 API
      await connectBackend();

      // 首屏展示时图表容器尚未挂载,进入系统后再渲染
      if (!showLanding.value) {
        renderKline();
      }
      window.addEventListener('keydown', handleGlobalKeydown);
      window.addEventListener('resize', () => {
        klineChartInstance?.resize();
        radarInstance?.resize();
        backtestInstance?.resize();
        shapInstance?.resize();
        perDayInstance?.resize();
        inventoryValueChartInstance?.resize();
      });

      // 隐藏入口: #admin 进出独立管理端(不进侧边栏)
      const syncAdminHash = () => {
        const isAdminHash = (location.hash || '').replace(/^#/, '') === 'admin';
        if (isAdminHash && currentPage.value !== 'admin') {
          currentPage.value = 'admin';
          showLanding.value = false;
          if (adminIsAuthed.value) loadAdminPanel();
        } else if (!isAdminHash && currentPage.value === 'admin') {
          leaveAdmin();
        }
      };
      window.addEventListener('hashchange', syncAdminHash);
      if ((location.hash || '').replace(/^#/, '') === 'admin') {
        showLanding.value = false;
        syncAdminHash();
      }

      // 网络状态监听
      const updateOnlineStatus = () => {
        if (!navigator.onLine) {
          showErrorToast(t('network.offline'), t('network.offlineDesc'));
        }
      };
      window.addEventListener('online', () => showToast({ title: t('network.online'), type: 'success' }));
      window.addEventListener('offline', updateOnlineStatus);

      // 把 <i class="ph-..."> 自动替换为 Lucide 内嵌 SVG
      // (Phosphor 字体 404,这些图标原本不可见)
      window.processPhIcons && window.processPhIcons();

      // 刷新后若仍停在模型页，需主动渲染图表（watch 不会在初始值触发）
      if (!showLanding.value && currentPage.value === 'models') {
        await loadModelsFromApi();
        setTimeout(() => {
          renderRadar();
          renderBacktest();
          renderShap();
          renderPerDay();
          radarInstance?.resize();
          backtestInstance?.resize();
          shapInstance?.resize();
          perDayInstance?.resize();
        }, 120);
      }

      // 不再弹欢迎 Toast (用户反馈: 弹窗太多令人困惑)
    });

    // Vue 重渲染后再次处理 (新插入的 ph-* 元素)
    onUpdated(() => {
      window.processPhIcons && window.processPhIcons();
    });

    // 监听页面切换
    watch(currentPage, async (newPage, oldPage) => {
      await nextTick();
      window.processPhIcons && window.processPhIcons();

      // 离开模型页时释放图表，避免触摸事件残留
      if (oldPage === 'models' && newPage !== 'models') {
        try {
          radarInstance?.dispose();
          backtestInstance?.dispose();
          shapInstance?.dispose();
          perDayInstance?.dispose();
        } catch (_) { /* ignore */ }
        radarInstance = null;
        backtestInstance = null;
        shapInstance = null;
        perDayInstance = null;
      }

      if (newPage === 'prediction') {
        renderKline();
        if (selectedSkin.value?.id) {
          loadPlatformQuotes(selectedSkin.value.id, { live: true });
        }
      } else if (newPage === 'models') {
        await loadModelsFromApi();
        setTimeout(() => {
          renderRadar();
          renderBacktest();
          renderShap();
          renderPerDay();
          radarInstance?.resize();
          backtestInstance?.resize();
          shapInstance?.resize();
          perDayInstance?.resize();
        }, 100);
      } else if (newPage === 'daily') {
        await loadDailyReport();
      } else if (newPage === 'admin') {
        if (adminIsAuthed.value) await loadAdminPanel();
      } else if (newPage === 'alerts') {
        await loadAlertsFromApi();
      } else if (newPage === 'portfolio') {
        if (portfolioTab.value === 'inventory') {
          if (currentUser.value) {
            await loadInventoryFromApi();
            await refreshInventoryCharts();
          }
        } else {
          await loadPortfolioFromApi();
          await loadPortfolioExtras();
        }
      } else if (newPage === 'chat') {
        setTimeout(scrollChatBottom, 100);
      }
    });

    // 登录后若正停留在「我的库存」，自动加载数据与图表
    watch(currentUser, async (user) => {
      if (user && currentPage.value === 'portfolio' && portfolioTab.value === 'inventory') {
        await loadInventoryFromApi();
        await refreshInventoryCharts();
      }
    });

    // 监听选中饰品变化
    watch(selectedSkin, (skin) => {
      relatedNewsOverride.value = null;
      explainSummary.value = '';
      predictionDaily.value = null;
      if (currentPage.value === 'prediction') {
        renderKline();
        if (skin?.id) {
          loadPlatformQuotes(skin.id, { live: true });
        }
      }
    });

    return {
      // 主题
      theme, toggleTheme,
      // 侧边栏
      sidebarOpen,
      // i18n
      currentLang, toggleLang, t,
      // Toast
      toasts, showToast,
      // 菜单
      menu, currentPage, currentMenu, activeNavId, subPageLabel, renderMenuIcon, renderLucideIcon, goToPage,
      // 首屏
      showLanding, landingExiting, enterSystem, showAdmin, leaveAdmin,
      // 用户认证
      currentUser, isGuest, showAuthPanel, authMode, authForm, authError, authSubmitting,
      submitLogin, submitRegister, enterAsGuest, logoutUser,
      openAuthPanel, closeAuthPanel, returnToLandingForLogin,
      userMenuOpen, userAvatarChar,
      showProfileModal, profileNameDraft, openProfileEditor, saveProfile,
      // 行情
      skins, topGainers, topLosers, hotVolume, refreshData,
      filterCategory, categoryKeys, categoryMap, categoryLabel, filteredSkins,
      visibleSkins, hasMoreSkins, remainingSkins, showMoreSkins,
      marketLiveQuotes, marketLiveLoading, refreshMarketLive,
      skinSearch, skinSort, marketPulse, formatChange, formatVolume,
      apiOnline, connectBackend, reconnectBackend, dataSourceLabel,
      // 预测
      selectedSkin, viewSkin, klineChart, klineLoading, timeframe, renderKline,
      modelPredictions, predictionMeta, predictionDaily, predictionDailyRows,
      relatedNews, newsIcon, openExternalUrl, roundTitle, debateData,
      explainSummary, loadExplanation,
      platformQuotes, platformQuotesLoading, platformQuotesMeta, platformQuotesSorted,
      loadPlatformQuotes, refreshPlatformQuotes, platformLabel, platformQuotesRef, platformQuotesLive, livePriceAvg,
      // 对话
      chatMessages, chatInput, chatLoading, chatSuggestedIndex, sendMessage, askQuestion, onChatKeydown, renderMarkdown,
      suggestedQuestions, debateSuggestedQuestions, activeSuggestedQuestions,
      chatMode, setChatMode, canSendChat,
      // 资讯 / 日报
      newsFeed, dailyReport, loadDailyReport,
      ragQuery, ragAnswer, ragAnswerSources, ragLoading, ragAsked, ragSuggestions, askRag, renderCitations, ragRetrieval,
      adminSession, adminIsAuthed, adminLoginForm, adminLoginError, adminLoginLoading,
      adminUsers, adminConfig, adminStatus, adminProbeLlm, adminProbeEmbed,
      adminSaving, adminLoading, adminConfigForm,
      adminLogin, adminLogout, loadAdminPanel, saveAdminConfig, refreshAdminStatus, runProbeLlm, runProbeEmbed,
      // 预警
      alerts, showAlertModal, newAlert, addAlert, deleteAlert,
      // 持仓 / 库存
      portfolioTab, setPortfolioTab,
      portfolio, showPortfolioModal, newPortfolio, addPortfolio, removePortfolio,
      portfolioMetrics, getCurrentPrice, getItemPnl, getItemPnlPct,
      portfolioDiagnose, portfolioValueHistory, loadPortfolioExtras,
      myInventory, showInventoryModal, newInventory, addInventoryItem, removeInventoryItem,
      importSteamInventory, openInventoryItem,
      showSteamImportModal, steamImportLoading, steamImportForm, steamImportResult, submitSteamImport,
      inventoryMenuId, toggleInventoryMenu, closeInventoryMenu,
      showInventoryEditModal, editingInventory, openEditInventoryPrice, saveInventoryPrice,
      inventoryItemCount, inventoryTotalValue, inventoryTotalChange24h, inventorySourceLabel,
      getSkinImage, getSkinChange24h, getSkinMeta,
      inventoryValueChart, inventoryValueHistory,
      refreshInventoryCharts,
      // 模型
      regressionModels, classificationModels, modelTypeLabel, modelComparison, hybridRoute,
      radarChart, backtestChart, shapChart,
      perDayChart, modelsPerDay, perDayMetric, setPerDayMetric,
      // 工具
      formatPrice, exportData, renderIcon,
      // 命令面板
      showCommandPalette, commandQuery, commandResults, commandSelected,
      executeCommand, onCommandKeydown, commandInput,
      // 快捷键
      showShortcutHelp,
    };
  },
});

// ============ 全局错误处理 ============
const showErrorToast = (title, subtitle = '') => {
  // 通过 DOM 操作触发 toast (避免在 setup 外无法访问响应式数据)
  const container = document.querySelector('.toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast error';
  toast.innerHTML = `
    <span class="toast-icon" aria-hidden="true">⚠️</span>
    <div class="toast-content">
      <div class="toast-title">${title}</div>
      ${subtitle ? `<div class="toast-subtitle">${subtitle}</div>` : ''}
    </div>
  `;
  toast.style.animation = 'slideInRight 0.2s';
  toast.setAttribute('role', 'alert');
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 5000);

  // 屏幕阅读器公告
  const live = document.getElementById('aria-live-assertive');
  if (live) live.textContent = `${title}${subtitle ? ' ' + subtitle : ''}`;
};

// Vue 组件错误处理
app.config.errorHandler = (err, instance, info) => {
  console.error('[Vue Error]', err, info);
  showErrorToast('组件渲染出错', String(err.message || err).slice(0, 80));
};

// 未捕获的 JS 错误
window.addEventListener('error', (e) => {
  console.error('[Window Error]', e.error);
  // 避免在某些已知错误上刷屏 (CDN 加载失败等)
  if (e.message && e.message.includes('Script error')) return;
  showErrorToast('运行时错误', String(e.message || '').slice(0, 80));
});

// 未处理的 Promise 拒绝
window.addEventListener('unhandledrejection', (e) => {
  console.error('[Unhandled Rejection]', e.reason);
  showErrorToast('异步操作失败', String(e.reason?.message || e.reason || '').slice(0, 80));
  e.preventDefault();
});

app.mount('#app');