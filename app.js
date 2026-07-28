// ============================================
// CSVest - 主应用
// 基于策划书功能清单实现
// ============================================

const { createApp, ref, computed, onMounted, onUnmounted, onUpdated, nextTick, watch } = Vue;

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
  const browser = (navigator.language || 'en-US').toLowerCase();
  if (browser.startsWith('zh')) return 'zh-CN';
  return 'en-US';
};
const currentLang = ref(localStorage.getItem('sv_lang') || 'en-US');
const t = (key, params = {}) => {
  const primary = window.I18N[currentLang.value] || {};
  const fallback = window.I18N[currentLang.value === 'zh-CN' ? 'en-US' : 'zh-CN'] || {};
  let str = primary[key] || fallback[key] || key;
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
    // Apple-style frosted glass sliding indicator for sidebar nav
    const navMenuRef = ref(null);
    const navPillReady = ref(false);
    const navPillStyle = ref({
      transform: 'translateY(0px)',
      height: '0px',
      width: '0px',
      left: '0px',
    });
    const updateNavPill = () => {
      const menuEl = navMenuRef.value;
      if (!menuEl) return;
      const active = menuEl.querySelector('.nav-item.active');
      if (!active) {
        navPillReady.value = false;
        return;
      }
      // Measure after layout so the glass pill can slide between items.
      const apply = () => {
        navPillStyle.value = {
          transform: `translate3d(0, ${active.offsetTop}px, 0)`,
          height: `${active.offsetHeight}px`,
          width: `${active.offsetWidth}px`,
          left: `${active.offsetLeft}px`,
        };
        navPillReady.value = true;
      };
      requestAnimationFrame(apply);
    };
    watch([activeNavId, currentLang, menu], async () => {
      await nextTick();
      updateNavPill();
    });
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
    const landingHeroIndex = ref(0);
    const landingHeroSlides = computed(() => {
      // touch currentLang so language toggle refreshes slide copy
      void currentLang.value;
      return [0, 1, 2, 3].map((i) => ({
        kicker: t(`landing.hero.s${i}.kicker`),
        title: t(`landing.hero.s${i}.title`),
        lead: t(`landing.hero.s${i}.lead`),
      }));
    });
    const landingCoarsePointer =
      typeof matchMedia === 'function' &&
      (matchMedia('(pointer: coarse)').matches || matchMedia('(max-width: 720px)').matches);
    const landingSceneHint = computed(() => {
      void currentLang.value;
      return t(landingCoarsePointer ? 'landing.sceneHintTouch' : 'landing.sceneHint');
    });
    let landingScenesApi = null;
    let landingScrollApi = null;

    const destroyLandingCanvas = () => {
      try {
        landingScenesApi?.destroy?.();
      } catch (_) { /* ignore */ }
      landingScenesApi = null;
      try {
        landingScrollApi?.destroy?.();
      } catch (_) { /* ignore */ }
      landingScrollApi = null;
    };

    const mountLandingCanvas = async () => {
      await nextTick();
      destroyLandingCanvas();
      if (!showLanding.value) return;
      const sceneRoot = document.getElementById('landing-scene-root');
      if (typeof window.initLandingScenes === 'function' && sceneRoot) {
        const landingRoot = document.querySelector('.landing');
        const hero = document.querySelector('.landing-hero');
        landingScenesApi = window.initLandingScenes(sceneRoot, {
          wheelHost: landingRoot || hero || window,
          captionEl: document.getElementById('landing-scene-caption'),
          nameEl: document.getElementById('landing-scene-name'),
          mapEl: document.getElementById('landing-scene-map'),
          priceEl: document.getElementById('landing-scene-price'),
          dotsEl: document.getElementById('landing-scene-dots'),
          copyViewportEl: document.getElementById('landing-hero-copy-viewport'),
          copyTrackEl: document.getElementById('landing-hero-copy-track'),
          onSceneChange: (idx) => {
            landingHeroIndex.value = idx;
          },
        });
      }
      if (typeof window.initLandingScroll === 'function') {
        const landingRoot = document.querySelector('.landing');
        if (landingRoot) {
          landingScrollApi = window.initLandingScroll(landingRoot);
        }
      }
    };

    watch(showLanding, (on) => {
      if (on) mountLandingCanvas();
      else destroyLandingCanvas();
    });

    watch(currentLang, async () => {
      await nextTick();
      try {
        landingScenesApi?.refreshLang?.();
      } catch (_) { /* ignore */ }
    });

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
      destroyLandingCanvas();
      const done = () => {
        showLanding.value = false;
        landingExiting.value = false;
        nextTick(async () => {
          updateNavPill();
          renderKline();
          await hydrateCurrentPage(currentPage.value);
          setTimeout(() => {
            klineChartInstance?.resize();
            radarInstance?.resize();
            backtestInstance?.resize();
            shapInstance?.resize();
            perDayInstance?.resize();
            inventoryValueChartInstance?.resize();
            updateNavPill();
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
        GENERIC: 'auth.err.generic',
      };
      return t(map[code] || 'auth.err.generic');
    };

    const refreshPersonalDataAfterAuth = async () => {
      if (!currentUser.value || !apiOnline.value) return;
      try {
        await Promise.all([
          loadAlertsFromApi(),
          loadPortfolioFromApi(),
          loadInventoryFromApi().catch(() => null),
        ]);
      } catch (_) { /* ignore */ }
    };

    const submitLogin = async () => {
      if (!Auth || authSubmitting.value) return;
      authSubmitting.value = true;
      authError.value = '';
      try {
        const result = await Auth.login(authForm.value.email, authForm.value.password);
        if (!result.ok) {
          authError.value = authErrorMessage(result.code);
          return;
        }
        currentUser.value = result.user;
        isGuest.value = false;
        sessionStorage.removeItem('sv_guest');
        authForm.value.password = '';
        showToast({ title: t('auth.toast.loginOk'), subtitle: result.user.name, type: 'success' });
        await refreshPersonalDataAfterAuth();
        enterSystem();
      } catch (err) {
        authError.value = authErrorMessage('GENERIC');
        console.warn('[Auth] login error:', err);
      } finally {
        authSubmitting.value = false;
      }
    };

    const submitRegister = async () => {
      if (!Auth || authSubmitting.value) return;
      authSubmitting.value = true;
      authError.value = '';
      try {
        const result = await Auth.register(
          authForm.value.name,
          authForm.value.email,
          authForm.value.password
        );
        if (!result.ok) {
          authError.value = authErrorMessage(result.code);
          return;
        }
        currentUser.value = result.user;
        isGuest.value = false;
        sessionStorage.removeItem('sv_guest');
        authForm.value.password = '';
        showToast({ title: t('auth.toast.registerOk'), subtitle: result.user.name, type: 'success' });
        await refreshPersonalDataAfterAuth();
        enterSystem();
      } catch (err) {
        authError.value = authErrorMessage('GENERIC');
        console.warn('[Auth] register error:', err);
      } finally {
        authSubmitting.value = false;
      }
    };

    const enterAsGuest = () => {
      showToast({ title: t('auth.toast.guest'), type: 'info' });
      enterSystem(true);
    };

    const logoutUser = () => {
      Auth?.logout?.();
      try { api()?.clearToken?.(); } catch (_) { /* ignore */ }
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
      // Re-render charts so axis/legend colors match the active theme.
      setTimeout(() => {
        if (currentPage.value === 'prediction' && selectedSkin.value) {
          renderKline();
        }
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
        .filter((s) => (s.change7d || 0) > 0 && (s.priceUsd || s.price || 0) >= 4)
        .sort((a, b) => (b.change7d || 0) - (a.change7d || 0))
        .slice(0, 8);
      topLosers.value = [...pool]
        .filter((s) => (s.change7d || 0) < 0 && (s.priceUsd || s.price || 0) >= 4)
        .sort((a, b) => (a.change7d || 0) - (b.change7d || 0))
        .slice(0, 8);
      hotVolume.value = [...pool]
        .sort((a, b) => Math.abs(Number(b.change24h) || 0) - Math.abs(Number(a.change24h) || 0))
        .slice(0, 8);
      loadLeaderSparks();
    };

    // Mini 7–30d price sparkline for leaderboard rows (real kline closes)
    const rowSparkCache = ref({});
    const rowSparkPending = new Set();
    const buildRowSparkPath = (closes) => {
      const vals = (closes || []).map(Number).filter((v) => Number.isFinite(v) && v > 0).slice(-30);
      if (vals.length < 2) return '';
      const w = 72;
      const h = 28;
      const pad = 2;
      const min = Math.min(...vals);
      const max = Math.max(...vals);
      const range = max - min || 1;
      const coords = vals.map((v, i) => {
        const x = pad + (i / (vals.length - 1)) * (w - pad * 2);
        const y = pad + (1 - (v - min) / range) * (h - pad * 2);
        return [x, y];
      });
      let path = `M${coords[0][0].toFixed(1)},${coords[0][1].toFixed(1)}`;
      for (let i = 1; i < coords.length - 1; i += 1) {
        const midX = (coords[i][0] + coords[i + 1][0]) / 2;
        const midY = (coords[i][1] + coords[i + 1][1]) / 2;
        path += ` Q${coords[i][0].toFixed(1)},${coords[i][1].toFixed(1)} ${midX.toFixed(1)},${midY.toFixed(1)}`;
      }
      const last = coords[coords.length - 1];
      return `${path} T${last[0].toFixed(1)},${last[1].toFixed(1)}`;
    };
    const rowSparkPath = (skinId) => rowSparkCache.value[skinId] || '';
    // Fetch one skin's ~30d closes and cache its sparkline path (deduped).
    const ensureSkinSpark = async (skinId) => {
      if (!skinId) return;
      if (rowSparkCache.value[skinId] != null || rowSparkPending.has(skinId)) return;
      rowSparkPending.add(skinId);
      let closes = [];
      try {
        const client = api();
        if (client) {
          const kl = await client.getKLine(skinId, 30);
          closes = (kl?.data || []).map((d) => Number(d.close)).filter((v) => Number.isFinite(v) && v > 0);
        }
      } catch (_) { /* fall through to mock */ }
      if (closes.length < 2) {
        const skin = skins.value.find((s) => s.id === skinId)
          || topGainers.value.find((s) => s.id === skinId)
          || topLosers.value.find((s) => s.id === skinId);
        if (skin && window.CSVestData?.generateKLineData) {
          const mock = window.CSVestData.generateKLineData(skin.price || 1, 30);
          closes = (mock.kline || []).map((d) => Number(d[2])).filter((v) => Number.isFinite(v) && v > 0);
        }
      }
      rowSparkCache.value = { ...rowSparkCache.value, [skinId]: buildRowSparkPath(closes) };
      rowSparkPending.delete(skinId);
    };
    const loadLeaderSparks = () => {
      [
        ...topGainers.value.slice(0, 5),
        ...topLosers.value.slice(0, 5),
      ].forEach((s) => { if (s?.id) ensureSkinSpark(s.id); });
    };

    const loadSkinsFromApi = async () => {
      const client = api();
      if (!client) return false;
      // 后端全集 681 件(132 csv + 549 buff); limit 调大到 1000 以全量展示
      // 连真实后端时禁止静默退回 Mock，避免行情中心“看起来在线实则演示”
      const res = await client.getSkins(
        { limit: 1000, sort: 'change7d_desc' },
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
        liquidity: s.liquidity ?? null,
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
        predictionStatus.value = res.status || 'demo';
        predictionReason.value = res.reason || '';
        predictionCalibration.value = res.calibration || null;
        if (predictionStatus.value === 'unavailable') {
          modelPredictions.value = [];
          predictionDaily.value = null;
          predictionTrend30d.value = null;
          predictionCalibration.value = null;
          predictionMeta.value = {
            consensusScore: 0,
            consensusLevel: '',
            entryLow: null,
            entryHigh: null,
            targetPrice: null,
          };
          return res;
        }
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
            name: p.routeModel || p.model,
            type: p.type || 'ML',
            price,
            change: +change.toFixed(2),
            confidence: Math.round(p.confidence || 0),
            daily,
            rawDaily: Array.isArray(p.rawDailyPrices) ? p.rawDailyPrices : null,
          };
        });
        const levelKey = res.consensus?.level
          ? `prediction.level.${res.consensus.level}`
          : '';
        const predsMapped = modelPredictions.value;
        let score = Math.round(Number(res.consensus?.score));
        // Never surface 0% when models are available: recompute from agreement/confidence.
        if (!Number.isFinite(score) || score <= 0) {
          const changes = predsMapped.map((p) => Number(p.change)).filter((c) => Number.isFinite(c));
          const confs = predsMapped.map((p) => Number(p.confidence)).filter((c) => c > 0);
          if (confs.length) {
            score = Math.round(confs.reduce((a, b) => a + b, 0) / confs.length);
          } else if (changes.length >= 2) {
            const mean = changes.reduce((a, b) => a + b, 0) / changes.length;
            const variance = changes.reduce((a, c) => a + (c - mean) ** 2, 0) / changes.length;
            const std = Math.sqrt(variance);
            score = Math.round(88 - Math.min(std, 18) * 2.2);
          } else if (changes.length === 1) {
            score = Math.round(58 + Math.min(Math.abs(changes[0]), 10) * 2.4);
          } else {
            score = 60;
          }
        }
        score = Math.max(42, Math.min(96, score));
        let resolvedLevel = levelKey;
        if (!resolvedLevel || !(window.I18N['zh-CN']?.[resolvedLevel] || window.I18N['en-US']?.[resolvedLevel])) {
          const lvl = score >= 80 ? 'very_high' : score >= 65 ? 'high' : score >= 45 ? 'medium' : 'low';
          resolvedLevel = `prediction.level.${lvl}`;
        }
        predictionMeta.value = {
          consensusScore: score,
          consensusLevel: resolvedLevel,
          entryLow: res.entryRange?.low ?? +(curUsd * 0.97).toFixed(2),
          entryHigh: res.entryRange?.high ?? +(curUsd * 0.99).toFixed(2),
          targetPrice: res.targetPrice ?? +(curUsd * 1.05).toFixed(2),
        };
        // 逐日预测主路径: 优先 LSTM(部署主力),否则任一带 daily 的模型
        const withDaily = modelPredictions.value.filter(p => p.daily && p.daily.length);
        const primary = withDaily.find(p => /lstm/i.test(p.name)) || withDaily[0] || null;
        const forecastAnchor = Number(res.forecastAnchorPrice || curUsd);
        predictionDaily.value = primary
          ? {
            model: primary.name,
            base: Number.isFinite(forecastAnchor) && forecastAnchor > 0 ? forecastAnchor : curUsd,
            prices: primary.daily,
            anchorApplied: Boolean(
              Array.isArray(res.calibration?.reasonCodes)
              && res.calibration.reasonCodes.includes('UNCONFIRMED_PRICE_SHOCK')
            ),
          }
          : null;
        predictionTrend30d.value = null;
        if (
          res.trend30d
          && res.trend30d.horizon === 30
          && ['p10', 'p50', 'p90'].every(
            key => Array.isArray(res.trend30d[key]) && res.trend30d[key].length === 30
          )
        ) {
          predictionTrend30d.value = res.trend30d;
        }
        return res;
      } catch (err) {
        console.warn('[CSVest] predict failed', err);
        predictionStatus.value = 'error';
        predictionReason.value = 'REQUEST_FAILED';
        predictionCalibration.value = null;
        modelPredictions.value = [];
        predictionDaily.value = null;
        predictionTrend30d.value = null;
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
        const news = await client.getNews({ limit: 40 });
        const items = Array.isArray(news) ? news : (news?.items || []);
        if (items.length) {
          newsFeed.value = items;
          await localizeDailyHeadlines(currentLang.value);
        }
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
        // 在线时以服务端为准(含空列表);模拟持仓页只展示 sim,绝不回退成真实库存
        if (items.length || apiOnline.value) {
          const mapped = items.map(p => ({
            id: p.id,
            skinId: p.skinId,
            name: p.name,
            buyPrice: p.buyPrice,
            quantity: p.quantity || 1,
            buyDate: p.buyDate,
            // 无标记视为 real,避免真实库存误入模拟持仓页
            holdingType: p.holdingType || 'real',
            currentPrice: p.currentPrice,
            pnl: p.pnl,
            pnlPct: p.pnlPct,
          }));
          portfolio.value = mapped.filter(p => p.holdingType === 'sim');
        }
      } catch (_) { /* keep local */ }
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
      modelsLoading.value = true;
      try {
        if (!client) {
          modelsDataSource.value = 'demo';
          return;
        }
        const cmp = await client.getModelComparison();
        const courseByName = Object.fromEntries(
          (modelComparison.regression || []).map((r) => [r.name, r.course || ''])
        );
        let usedLive = false;
        if (cmp?.tracks) {
          modelTracks.value = cmp.tracks;
          applyModelTrack(modelTrack.value);
          await enrichOnlineReturnsFromBacktest();
          usedLive = true;
        } else if (cmp?.regression?.length) {
          regressionModels.value = cmp.regression.map((r) => {
            const course = r.course && r.course !== r.type
              ? r.course
              : (courseByName[r.name] || r.course || '');
            return { ...r, course };
          });
          usedLive = true;
        }
        if (!cmp?.tracks && cmp?.classification?.length) {
          classificationModels.value = cmp.classification;
          usedLive = true;
        }
        if (cmp?.buyAndHold && typeof cmp.buyAndHold === 'object') {
          modelComparison.buyAndHold = {
            ...modelComparison.buyAndHold,
            ...cmp.buyAndHold,
          };
        }
        if (cmp?.nItems != null) {
          modelsNItems.value = Number(cmp.nItems) || modelsNItems.value;
        }
        // v5 契约: Seq2Seq 多步模型带 perDay 逐日指标(D1..D7)
        if (!cmp?.tracks) modelsPerDay.value = (cmp?.regression || [])
          .filter((r) => Array.isArray(r.perDay) && r.perDay.length)
          .map((r) => ({ name: r.name, perDay: r.perDay }));
        modelsDataSource.value = (usedLive && apiOnline.value) ? 'live' : 'demo';
      } catch (_) {
        modelsDataSource.value = 'demo';
      } finally {
        modelsLoading.value = false;
      }
    };

    const dailyReport = ref({
      date: '',
      generatedAt: '',
      summaryProvider: '',
      metrics: { monitored: 0, gainers: 0, losers: 0 },
      aiSummary: '',
      sources: [],
    });
    const dailyReportLoading = ref(false);
    const dailyTab = ref('report'); // report | rag
    const dailySourcesOpen = ref(true);
    const dailyReportLocaleVersions = ref({});
    let dailyReportTranslationRequest = 0;
    const setDailyTab = (tab) => {
      dailyTab.value = tab === 'rag' ? 'rag' : 'report';
    };
    const dailySummaryLocale = (summary) => /[\u3400-\u9fff]/.test(String(summary || ''))
      ? 'zh-CN'
      : 'en-US';
    // 英文 RSS / 种子标题 → 中文显示缓存（key = 原文 title）
    const headlineZhCache = ref({});
    let headlineTranslationRequest = 0;
    const HEADLINE_ZH_FALLBACK = {
      'CS2-Bot-Improver v1.4.3: Stickers, Charms & Bot Inspects':
        'CS2-Bot-Improver v1.4.3：贴纸、挂件与 Bot 检视',
      'SmokePractice got a big update: Nuke, Cache, better physics and more':
        'SmokePractice 大更新：Nuke、Cache、更佳物理效果等',
      "Donk broke his personal Faceit CS2 elo record today. He's now 48 elo away from reclaiming the all-time record from Mail09.":
        'donk 刷新个人 Faceit CS2 Elo 纪录，距 Mail09 历史纪录仅差 48 分',
      '2.84-rated donk Dust2 decider fires Spirit to 13-0 win against 100 Thieves':
        'donk Dust2 决胜图 2.84 评分，Spirit 13-0 横扫 100 Thieves',
      'shiro 7.88 rating with 1 ct round vs 100T':
        'shiro 对阵 100T 仅 1 个 CT 回合打出 7.88 评分',
      'In the 13-0 win vs 100 Thieves on Dust2, donk has posted a 2.84 rating - his highest-rated map of his entire career':
        'Spirit 在 Dust2 以 13-0 击败 100 Thieves，donk 打出生涯最高单图评分 2.84',
      '100 Thieves vs Spirit / BLAST Bounty 2026 Season 2 - Stage 2 Round Of 16 / Post-Match Discussion':
        '100 Thieves vs Spirit｜BLAST Bounty 2026 第二季 十六强赛后讨论',
      'Aurora and magic qualify for StarSeries Fall':
        'Aurora 与 magic 晋级 StarSeries Fall',
    };
    const headlineNeedsZh = (text) => {
      const s = String(text || '').trim();
      if (!s) return false;
      if (/[\u3400-\u9fff]/.test(s)) return false;
      return /[A-Za-z]/.test(s);
    };
    const localizedHeadline = (title) => {
      const raw = String(title || '');
      if (currentLang.value !== 'zh-CN' || !headlineNeedsZh(raw)) return raw;
      return headlineZhCache.value[raw] || HEADLINE_ZH_FALLBACK[raw] || raw;
    };
    const localizedNewsImpact = (impact) => {
      const raw = String(impact || '').trim();
      if (!raw) return '';
      const key = `daily.impact.${raw.toLowerCase()}`;
      const mapped = t(key);
      return mapped === key ? raw : mapped;
    };
    const collectDailyHeadlines = () => {
      const titles = [];
      const push = (title) => {
        const s = String(title || '').trim();
        if (s && headlineNeedsZh(s)) titles.push(s);
      };
      (newsFeed.value || []).forEach((n) => push(n?.title));
      (dailyReport.value?.sources || []).forEach((s) => push(s?.title));
      (dailyReport.value?.news || []).forEach((n) => push(n?.title));
      (ragAnswerSources.value || []).forEach((s) => push(s?.title));
      return [...new Set(titles)];
    };
    const localizeDailyHeadlines = async (locale = currentLang.value) => {
      if (locale !== 'zh-CN') return;
      const pending = collectDailyHeadlines().filter(
        (title) => !headlineZhCache.value[title] && !HEADLINE_ZH_FALLBACK[title]
      );
      // 静态兜底先写入缓存，立刻可显示
      const fallbackHits = collectDailyHeadlines().filter((title) => HEADLINE_ZH_FALLBACK[title]);
      if (fallbackHits.length) {
        const next = { ...headlineZhCache.value };
        fallbackHits.forEach((title) => {
          if (!next[title]) next[title] = HEADLINE_ZH_FALLBACK[title];
        });
        headlineZhCache.value = next;
      }
      if (!pending.length) return;
      const client = api();
      if (!client || typeof client.translateAIContent !== 'function') return;
      const requestId = ++headlineTranslationRequest;
      try {
        const response = await client.translateAIContent(
          { items: pending.map((title, i) => ({ id: String(i), title })) },
          'zh-CN'
        );
        if (requestId !== headlineTranslationRequest) return;
        const items = response?.content?.items;
        if (!Array.isArray(items)) return;
        const next = { ...headlineZhCache.value };
        items.forEach((item, i) => {
          const src = pending[Number(item?.id)] ?? pending[i];
          const zh = typeof item?.title === 'string' ? item.title.trim() : '';
          if (src && zh && zh !== src) next[src] = zh;
        });
        headlineZhCache.value = next;
      } catch (error) {
        console.warn('[daily-headlines] translation unavailable:', error?.message || error);
      }
    };
    const dailyBreadth = computed(() => {
      const g = Number(dailyReport.value?.metrics?.gainers) || 0;
      const l = Number(dailyReport.value?.metrics?.losers) || 0;
      const total = g + l;
      if (!total) return { upPct: 0, downPct: 0 };
      return {
        upPct: Math.round((g / total) * 100),
        downPct: Math.round((l / total) * 100),
      };
    });
    /** Client-side KPI fallback from skins list (same ≥$4 rule as backend). */
    const buildClientDailyMetrics = () => {
      const all = skins.value || [];
      const priced = all.filter((s) => Number(s?.price) > 0);
      const pool = priced.length ? priced : all;
      let gainers = 0;
      let losers = 0;
      for (const s of pool) {
        const px = Number(s?.price);
        const ch = Number(s?.change7d);
        if (!(px >= 4) || !Number.isFinite(ch) || ch === 0) continue;
        if (ch > 0) gainers += 1;
        else losers += 1;
      }
      return { monitored: pool.length, gainers, losers };
    };
    const ensureDailyMetricsPlaceholder = () => {
      const cur = Number(dailyReport.value?.metrics?.monitored) || 0;
      if (cur > 0) return;
      const next = buildClientDailyMetrics();
      if (!next.monitored) return;
      dailyReport.value = {
        ...dailyReport.value,
        metrics: next,
      };
    };
    const dailySummaryBadge = computed(() => {
      const provider = String(dailyReport.value?.summaryProvider || '').toLowerCase();
      if (provider === 'deepseek') return t('daily.aiSummaryModel.deepseek');
      if (provider === 'rule_based' || provider === 'rule') return t('daily.aiSummaryModel.rule');
      if (provider === 'seed') return t('daily.aiSummaryModel.seed');
      // Missing provider on old payloads → do not imply DeepSeek
      return t('daily.aiSummaryModel.rule');
    });
    const explainSummary = ref('');
    const portfolioDiagnose = ref(null);
    const portfolioDiagnoseLoading = ref(false);
    const portfolioDiagnoseLocaleVersions = ref({});
    let portfolioDiagnoseTranslationRequest = 0;
    const portfolioValueHistory = ref({ dates: [], values: [] });

    const applyDailyReport = (rep, { resetLocaleCache = false } = {}) => {
      if (!rep) return;
      const summary = rep.aiSummary || rep.summary || '';
      const localeKey = rep.locale || dailySummaryLocale(summary);
      const fallback = buildClientDailyMetrics();
      const prev = dailyReport.value?.metrics || {};
      const apiMon = Number(rep.metrics?.monitored);
      // Backend metrics are authoritative when monitored > 0. Zero/missing usually
      // means empty runtime DB or a hung regenerate left the initial placeholder —
      // keep previous/client fallback so KPIs don't stick at 0 while LLM times out.
      const metrics = apiMon > 0
        ? {
            monitored: apiMon,
            gainers: Number(rep.metrics?.gainers) || 0,
            losers: Number(rep.metrics?.losers) || 0,
          }
        : {
            monitored: Number(prev.monitored) > 0 ? Number(prev.monitored) : (fallback.monitored || skins.value.length || 0),
            gainers: Number(prev.gainers) || fallback.gainers || topGainers.value.length || 0,
            losers: Number(prev.losers) || fallback.losers || topLosers.value.length || 0,
          };
      dailyReport.value = {
        date: rep.date || dailyReport.value?.date || '',
        generatedAt: rep.generatedAt || dailyReport.value?.generatedAt || '',
        locale: localeKey,
        summaryProvider: rep.summaryProvider || dailyReport.value?.summaryProvider || '',
        metrics,
        aiSummary: summary || dailyReport.value?.aiSummary || '',
        sources: Array.isArray(rep.sources) ? rep.sources : (dailyReport.value?.sources || []),
        news: Array.isArray(rep.news) ? rep.news : (dailyReport.value?.news || []),
        portfolio: Array.isArray(rep.portfolio) ? rep.portfolio : (dailyReport.value?.portfolio || []),
      };
      if (resetLocaleCache) {
        dailyReportLocaleVersions.value = summary ? { [localeKey]: summary } : {};
      } else if (summary) {
        dailyReportLocaleVersions.value = {
          ...dailyReportLocaleVersions.value,
          [localeKey]: summary,
        };
      }
      if (Array.isArray(rep.hotVolume) && rep.hotVolume.length) {
        hotVolume.value = rep.hotVolume;
      } else {
        reconnectLeaders();
      }
      const news = Array.isArray(rep.news) ? rep.news : [];
      // 日报种子新闻常无 url；资讯流优先保留 API 抓取结果（可点开原文）
      if (news.length && !(newsFeed.value || []).some((n) => n?.url)) {
        newsFeed.value = news;
      }
    };

    const localizeDailyReport = async (locale) => {
      const summary = dailyReport.value?.aiSummary;
      if (!summary) return;
      const cached = dailyReportLocaleVersions.value?.[locale];
      if (cached) {
        dailyReport.value = { ...dailyReport.value, aiSummary: cached, locale };
        return;
      }
      // Already in target language — cache and skip translate
      if (dailySummaryLocale(summary) === locale) {
        dailyReportLocaleVersions.value = {
          ...dailyReportLocaleVersions.value,
          [locale]: summary,
        };
        dailyReport.value = { ...dailyReport.value, locale };
        return;
      }
      // Primary path: ask backend to regenerate summary in the requested locale
      const client = api();
      if (client) {
        try {
          const rep = await client.getDailyReport(undefined, { locale });
          const next = rep?.aiSummary || rep?.summary || '';
          if (typeof next === 'string' && next.trim()) {
            dailyReportLocaleVersions.value = {
              ...dailyReportLocaleVersions.value,
              [rep.locale || locale]: next,
            };
            applyDailyReport({ ...dailyReport.value, ...rep, aiSummary: next });
            if (dailySummaryLocale(next) === locale) return;
          }
        } catch (error) {
          console.warn('[daily-report] locale reload failed:', error?.message || error);
        }
      }
      // Fallback: DeepSeek translate of existing summary
      if (!client || typeof client.translateAIContent !== 'function') return;
      const source = Object.values(dailyReportLocaleVersions.value || {})[0] || summary;
      const requestId = ++dailyReportTranslationRequest;
      try {
        const response = await client.translateAIContent(source, locale);
        if (requestId !== dailyReportTranslationRequest) return;
        const translated = response?.content;
        if (typeof translated !== 'string' || !translated.trim()) return;
        dailyReportLocaleVersions.value = {
          ...dailyReportLocaleVersions.value,
          [locale]: translated,
        };
        dailyReport.value = { ...dailyReport.value, aiSummary: translated, locale };
      } catch (error) {
        console.warn('[daily-report] translation unavailable:', error?.message || error);
      }
    };

    const loadDailyReport = async ({ refresh = false } = {}) => {
      const client = api();
      if (!client) return;
      // Show skins-derived KPIs immediately so regenerate/LLM wait doesn't leave 0s.
      ensureDailyMetricsPlaceholder();
      try {
        const rep = await client.getDailyReport(undefined, {
          refresh,
          locale: currentLang.value,
        });
        applyDailyReport(rep, { resetLocaleCache: refresh });
        await localizeDailyReport(currentLang.value);
        await localizeDailyHeadlines(currentLang.value);
      } catch (e) {
        console.warn('[CSVest] daily-report failed', e);
        ensureDailyMetricsPlaceholder();
      }
    };

    const regenerateDailyReport = async () => {
      if (dailyReportLoading.value) return;
      dailyReportLoading.value = true;
      showToast({ title: t('daily.regenerating'), type: 'info' });
      try {
        await loadDailyReport({ refresh: true });
        showToast({ title: t('daily.regenerateDone'), type: 'success' });
      } catch (e) {
        showToast({
          title: t('daily.regenerateFail'),
          subtitle: e?.message || String(e),
          type: 'error',
        });
      } finally {
        dailyReportLoading.value = false;
      }
    };

    watch(currentLang, async (locale) => {
      if (currentPage.value !== 'daily') return;
      await localizeDailyReport(locale);
      await localizeDailyHeadlines(locale);
    });

    const exportDailyReport = () => {
      const r = dailyReport.value || {};
      const m = r.metrics || {};
      const lines = [
        `# CSVest ${t('daily.titleClean')}`,
        `${t('daily.export.date')}: ${r.date || '-'}`,
        `${t('daily.export.generated')}: ${r.generatedAt || '-'}`,
        '',
        `## ${t('daily.export.metrics')}`,
        `- ${t('daily.metricMonitor')}: ${m.monitored ?? '-'}`,
        `- ${t('daily.metricGainers')}: ${m.gainers ?? '-'}`,
        `- ${t('daily.metricLosers')}: ${m.losers ?? '-'}`,
        '',
        `## ${t('daily.aiSummary')}`,
        r.aiSummary || t('daily.export.empty'),
        '',
        `## ${t('daily.rag.sourcesBadge')}`,
        ...((r.sources || []).map((s, i) =>
          `${i + 1}. [${s.source || s.type || 'source'}] ${localizedHeadline(s.title) || s.snippet || ''}`
        )),
        '',
        `## ${t('daily.newsStream')}`,
        ...((r.news || newsFeed.value || []).slice(0, 8).map((n) =>
          `- ${localizedHeadline(n.title)}${n.source ? ` (${n.source})` : ''}`
        )),
      ];
      const filename = `CSVest_daily_${(r.date || new Date().toISOString().slice(0, 10))}.md`;
      downloadFile(lines.join('\n'), filename, 'text/markdown;charset=utf-8;');
      showToast({ title: t('export.success'), subtitle: filename, type: 'success' });
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
    const ragSuggestions = computed(() => [
      t('daily.rag.sug1'),
      t('daily.rag.sug2'),
      t('daily.rag.sug3'),
      t('daily.rag.sug4'),
    ]);

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
        await localizeDailyHeadlines(currentLang.value);
      } catch (err) {
        console.warn('[rag]', err);
        ragAnswer.value = t('daily.rag.error');
        if (err?.message) {
          showToast({
            title: t('daily.rag.error'),
            subtitle: String(err.message).slice(0, 120),
            type: 'error',
          });
        }
      } finally {
        ragLoading.value = false;
      }
    };

    const newsFetchLoading = ref(false);
    const fetchNewsNow = async () => {
      if (newsFetchLoading.value) return;
      const client = api();
      if (!client || !apiOnline.value) {
        showToast({ title: t('daily.rag.fetchOffline'), type: 'warning' });
        return;
      }
      newsFetchLoading.value = true;
      showToast({ title: t('daily.rag.fetching'), type: 'info' });
      try {
        const res = await client.fetchNews({ aggressive: true });
        await loadNewsFromApi();
        const inserted = res?.inserted ?? 0;
        showToast({
          title: t('daily.rag.fetchDone'),
          subtitle: t('daily.rag.fetchDoneSub', {
            n: inserted,
            scanned: res?.scanned ?? 0,
            feeds: res?.feeds ?? 0,
          }),
          type: 'success',
        });
      } catch (e) {
        showToast({
          title: t('daily.rag.fetchFail'),
          subtitle: e?.message || String(e),
          type: 'error',
        });
      } finally {
        newsFetchLoading.value = false;
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
    const defaultAdminApiBase = () => {
      try {
        const client = api();
        if (client?.baseURL) return client.baseURL;
      } catch (_) { /* ignore */ }
      const saved = localStorage.getItem('sv_api_url');
      if (saved) return saved.replace(/\/$/, '');
      const host = (typeof location !== 'undefined' ? location.hostname : '') || '';
      const isLocal = !host || host === 'localhost' || host === '127.0.0.1';
      const isStatic = /\.(github|gitlab)\.io$/i.test(host) || /\.pages\.dev$/i.test(host);
      if (isStatic) return '';
      if (isLocal) return 'http://localhost:8000';
      return ''; // 公网 Docker/nginx：同源 /api
    };
    const adminSession = ref(loadAdminSession());
    const adminIsAuthed = computed(() => !!(adminSession.value?.token && adminSession.value?.user?.is_admin));
    const adminLoginForm = ref({ username: 'admin', password: '' });
    const adminApiBase = ref(defaultAdminApiBase());
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

    /** 管理端必须连真实后端；关闭 Mock，并应用 API Base URL */
    const ensureAdminApiClient = () => {
      const client = api();
      if (!client) throw new Error(t('admin.err.apiOffline'));
      const host = (typeof location !== 'undefined' ? location.hostname : '') || '';
      const isLocal = !host || host === 'localhost' || host === '127.0.0.1';
      const isStatic = /\.(github|gitlab)\.io$/i.test(host) || /\.pages\.dev$/i.test(host);
      let base = String(adminApiBase.value || '').trim().replace(/\/$/, '');
      if (!base) {
        if (isStatic) throw new Error(t('admin.err.needApiUrl'));
        base = isLocal ? 'http://localhost:8000' : '';
      }
      if (isStatic && /localhost|127\.0\.0\.1/i.test(base)) {
        throw new Error(t('admin.err.pagesNeedHttpsApi'));
      }
      client.ensureLiveBackend(base);
      adminApiBase.value = client.baseURL || base;
      if (adminSession.value?.token) client.setToken(adminSession.value.token);
      return client;
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
        const client = ensureAdminApiClient();
        // 先探活，避免登录失败只显示含糊网络错误
        try {
          await client.health();
        } catch (healthErr) {
          throw new Error(healthErr?.message || t('admin.err.backendDown'));
        }
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
        const client = ensureAdminApiClient();
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
        const client = ensureAdminApiClient();
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
        if (!adminIsAuthed.value) return;
        const client = ensureAdminApiClient();
        adminStatus.value = await client.adminStatus();
      } catch (err) {
        console.warn('[admin-status]', err);
      }
    };

    const runProbeLlm = async () => {
      adminProbeLlm.value = { loading: true };
      try {
        const client = ensureAdminApiClient();
        adminProbeLlm.value = await client.adminProbeLlm();
      } catch (err) {
        adminProbeLlm.value = { ok: false, error: err?.message || String(err) };
      }
    };

    const runProbeEmbed = async () => {
      adminProbeEmbed.value = { loading: true };
      try {
        const client = ensureAdminApiClient();
        adminProbeEmbed.value = await client.adminProbeEmbed();
      } catch (err) {
        adminProbeEmbed.value = { ok: false, error: err?.message || String(err) };
      }
    };
    const regressionModels = ref(
      (modelComparison.regression || []).map((r) => ({ ...r }))
    );
    const modelTrack = ref('historical');
    const modelTracks = ref(null);
    const modelTrackMetadata = ref({});
    const trend30Metrics = ref(null);
    const hybridRoute = modelComparison.hybridRoute;
    const classificationModels = ref(modelComparison.classification);
    const modelsLoading = ref(false);
    const modelsDataSource = ref('demo');
    const modelsNItems = ref(Number(modelComparison.nItems) || 155);
    const selectedRadarModel = ref('LSTM-C');
    const shapModel = ref('xgboost');
    const shapEmpty = ref(false);
    const shapFeatureRows = ref([]);
    const modelsInfoOpen = ref(null); // 'reg' | 'clf' | 'shap' | null
    const backtestEmpty = ref(false);
    const shapModelOptions = [
      { id: 'xgboost', label: 'XGBoost' },
      { id: 'lightgbm', label: 'LightGBM' },
      { id: 'average', label: 'Avg' },
    ];

    const toggleModelsInfo = (id) => {
      modelsInfoOpen.value = modelsInfoOpen.value === id ? null : id;
    };

    const regressionGuideItems = computed(() => {
      void currentLang.value;
      return [
        { key: 'task', label: t('models.guide.reg.taskLabel'), desc: t('models.guide.reg.task') },
        { key: 'rmse', label: 'RMSE', desc: t('models.guide.reg.rmse') },
        { key: 'mae', label: 'MAE', desc: t('models.guide.reg.mae') },
        { key: 'mape', label: 'MAPE', desc: t('models.guide.reg.mape') },
        { key: 'r2', label: 'R²', desc: t('models.guide.reg.r2') },
        { key: 'ret', label: t('models.col.returnPct'), desc: t('models.guide.reg.return') },
        { key: 'route', label: t('models.kpi.route'), desc: t('models.guide.reg.route') },
        { key: 'hybrid', label: 'Hybrid', desc: t('models.guide.reg.hybrid') },
      ];
    });

    const classificationGuideItems = computed(() => {
      void currentLang.value;
      return [
        { key: 'task', label: t('models.guide.clf.taskLabel'), desc: t('models.guide.clf.task') },
        { key: 'acc', label: 'ACC', desc: t('models.guide.clf.acc') },
        { key: 'auc', label: 'AUC', desc: t('models.guide.clf.auc') },
        { key: 'ret', label: t('models.col.returnPct'), desc: t('models.guide.clf.return') },
        { key: 'bh', label: t('models.buyHold'), desc: t('models.guide.clf.buyHold') },
      ];
    });

    const shapFeatureDesc = (name) => {
      const key = `models.shap.feat.${String(name || '').trim()}`;
      const label = t(key);
      if (label && label !== key) return label;
      return t('models.shap.feat.fallback');
    };

    const shapFeatureGuide = computed(() => {
      void currentLang.value;
      return (shapFeatureRows.value || []).map((row) => ({
        name: row.name,
        desc: shapFeatureDesc(row.name),
      }));
    });

    if (typeof document !== 'undefined') {
      document.addEventListener('click', (e) => {
        if (!modelsInfoOpen.value) return;
        const pop = e.target?.closest?.('.models-lab__info-pop');
        if (!pop) modelsInfoOpen.value = null;
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modelsInfoOpen.value) {
          modelsInfoOpen.value = null;
        }
      });
    }

    watch(currentPage, (page) => {
      if (page !== 'models') modelsInfoOpen.value = null;
    });
    watch(modelTrack, () => {
      modelsInfoOpen.value = null;
    });

    const applyModelTrack = (track) => {
      const selected = modelTracks.value?.[track];
      if (!selected) return;
      regressionModels.value = (selected.regression || []).map((row) => ({ ...row }));
      classificationModels.value = (selected.classification || []).map((row) => ({ ...row }));
      modelsPerDay.value = regressionModels.value
        .filter((row) => Array.isArray(row.perDay) && row.perDay.length)
        .map((row) => ({ name: row.name, perDay: row.perDay }));
      modelTrackMetadata.value = selected.metadata || {};
      trend30Metrics.value = selected.trend30 || null;
    };

    /** 旧后端 online 缺 returnPct 时，用回测净值曲线补齐 */
    const enrichOnlineReturnsFromBacktest = async () => {
      if (modelTrack.value !== 'online') return;
      const rows = regressionModels.value || [];
      if (!rows.length || rows.every((r) => r.returnPct != null)) return;
      const client = api();
      if (!client?.getBacktest) return;
      try {
        const bt = await client.getBacktest(0, 'online');
        const series = bt?.series || {};
        let changed = false;
        const next = rows.map((r) => {
          if (r.returnPct != null) return r;
          const arr = series[r.name];
          if (!Array.isArray(arr) || arr.length < 2) return r;
          const first = Number(arr.find((v) => v != null));
          const last = Number([...arr].reverse().find((v) => v != null));
          if (!first || last == null || Number.isNaN(first) || Number.isNaN(last)) return r;
          changed = true;
          return { ...r, returnPct: +(((last / first) - 1) * 100).toFixed(2) };
        });
        if (!changed) return;
        regressionModels.value = next;
        if (modelTracks.value?.online) {
          modelTracks.value = {
            ...modelTracks.value,
            online: { ...modelTracks.value.online, regression: next.map((r) => ({ ...r })) },
          };
        }
      } catch (err) {
        console.warn('[Models] enrich online returns failed:', err?.message || err);
      }
    };

    const setModelTrack = async (track) => {
      if (!['historical', 'online'].includes(track) || modelTrack.value === track) return;
      modelTrack.value = track;
      applyModelTrack(track);
      await enrichOnlineReturnsFromBacktest();
      await nextTick();
      renderRadar();
      renderPerDay();
      renderBacktest();
    };

    const modelsBest = computed(() => {
      const rows = regressionModels.value || [];
      let bestRmse = null;
      let bestMape = null;
      let bestReturn = null;
      for (const r of rows) {
        if (r.rmse != null && (bestRmse == null || r.rmse < bestRmse.rmse)) bestRmse = r;
        if (r.mape != null && (bestMape == null || r.mape < bestMape.mape)) bestMape = r;
        if (r.returnPct != null && (bestReturn == null || r.returnPct > bestReturn.returnPct)) bestReturn = r;
      }
      return {
        rmse: bestRmse?.name || '',
        mape: bestMape?.name || '',
        returnName: bestReturn?.name || '',
        returnPct: bestReturn?.returnPct ?? null,
        rmseVal: bestRmse?.rmse ?? null,
        mapeVal: bestMape?.mape ?? null,
      };
    });

    // 相对 RMSE 差距低于此值视为近并列（如 Hybrid-V2 Raw≈Calibrated），跳过以免显示 0.0%
    const MODELS_FINDINGS_MIN_REL_GAP = 0.005;

    const modelsFindingsPct = computed(() => {
      const rows = (regressionModels.value || [])
        .filter((r) => r.rmse != null && Number(r.rmse) > 0)
        .slice()
        .sort((a, b) => Number(a.rmse) - Number(b.rmse));
      if (rows.length < 2) return '—';
      const bestRmse = Number(rows[0].rmse);
      const runner = rows.slice(1).find((r) => {
        const rel = (Number(r.rmse) - bestRmse) / bestRmse;
        return rel >= MODELS_FINDINGS_MIN_REL_GAP;
      });
      if (!runner) return '—';
      const lead = ((Number(runner.rmse) - bestRmse) / bestRmse) * 100;
      if (lead < 1) return `${lead.toFixed(2)}%`;
      return `${lead.toFixed(1)}%`;
    });

    const modelsFindingsSentence = computed(() => {
      void currentLang.value;
      const pct = modelsFindingsPct.value;
      if (!pct || pct === '—') return t('models.findingsTextTied');
      return t('models.findingsText', { pct });
    });

    const formatModelReturn = (value) => {
      if (value == null || Number.isNaN(Number(value))) return '—';
      const n = Number(value);
      return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
    };

    const modelsKpis = computed(() => {
      void currentLang.value;
      const b = modelsBest.value;
      const route = hybridRoute || {};
      const routeText = `low→${route.low || 'C'} · mid/high→${route.mid || route.high || 'D'}`;
      const meta = modelTrackMetadata.value || {};
      const onlineMeta = meta.items != null
        ? `${meta.items} items`
        : (meta.modelVersion || '');
      return [
        {
          id: 'rmse',
          label: t('models.kpi.bestRmse'),
          value: b.rmseVal != null ? b.rmseVal.toFixed(2) : '—',
          meta: b.rmse || '',
        },
        {
          id: 'mape',
          label: t('models.kpi.bestMape'),
          value: b.mapeVal != null ? `${b.mapeVal.toFixed(2)}%` : '—',
          meta: b.mape || '',
        },
        {
          id: 'ret',
          label: t('models.kpi.bestReturn'),
          value: formatModelReturn(b.returnPct),
          meta: b.returnName || '',
        },
        modelTrack.value === 'online'
          ? {
              id: 'online',
              label: t('models.kpi.onlineCoverage'),
              value: meta.decisions != null ? String(meta.decisions) : '—',
              meta: onlineMeta,
            }
          : {
              id: 'route',
              label: t('models.kpi.route'),
              value: routeText,
              meta: '',
            },
      ];
    });

    const selectRadarModel = (name) => {
      selectedRadarModel.value = name;
      try { renderRadar(); } catch (_) { /* charts may not be ready */ }
    };

    const setShapModel = async (id) => {
      shapModel.value = id;
      try { await renderShap(); } catch (_) { /* charts may not be ready */ }
    };
    const modelTypeLabel = (m) => {
      if (!m) return '—';
      if (m.typeKey) {
        const key = 'models.type.' + m.typeKey;
        const label = t(key);
        if (label && label !== key) return label;
      }
      return m.type || '—';
    };
    const suggestedQuestions = computed(() => {
      if (currentLang.value === 'en-US') {
        return [
          'How has AWP | Asiimov (Field-Tested) trended recently?',
          'Recommend skins for a $700 budget with medium risk.',
          'Which skins are rising today?',
          'Which skin is worth holding long term?',
          'Help me set a price alert.',
          'How do the model-comparison results look?',
        ];
      }
      return [
        'AWP | 二西莫夫 (久经沙场) 最近的走势如何？',
        '预算 700 美元、中等风险，推荐什么饰品？',
        '今天哪些饰品正在上涨？',
        '哪款饰品更适合长期持有？',
        '帮我设置一个价格预警。',
        '当前各个预测模型的表现如何？',
      ];
    });

    const debateSuggestedQuestions = computed(() => {
      if (currentLang.value === 'en-US') {
        return [
          'Run a Bull / Bear / Judge debate on AWP | Asiimov (Field-Tested)',
          'What is the multi-agent view on ★ Karambit | Doppler (Factory New)?',
          'Should I buy M4A1-S | Golden Coil (Factory New)? Start a debate',
          'Debate Glock-18 | Neo-Noir (Factory New) across three rounds',
        ];
      }
      return [
        '请 Bull / Bear / Judge 分析 AWP | 二西莫夫 (久经沙场)',
        '多 Agent 怎么看 爪子刀（★） | 多普勒 (崭新出厂)？',
        'M4A1消音版 | 金蛇缠绕 (崭新出厂) 现在适合买入吗？开一场辩论',
        '围绕 格洛克18型 | 黑色魅影 (崭新出厂) 做三轮辩论',
      ];
    });

    const responseModelLabel = (response) => {
      const runtime = response?.runtime || {};
      const type = response?.type || 'chat';

      if (type === 'debate' || type === 'debate_round' || type === 'agent_followup') {
        if (runtime.agents?.mode === 'live') {
          return `Bull / Bear / Judge · Live (${runtime.agents.judgeModel || 'LLM'})`;
        }
        if (!runtime.agents) return 'Bull / Bear / Judge';
        return runtime.agents.mode === 'degraded'
          ? 'Bull / Bear / Judge · Live service unavailable'
          : 'Bull / Bear / Judge';
      }
      if (type === 'debate_answer') {
        if (response?.answerMode === 'llm_grounded' && runtime.llm?.mode === 'live') {
          return `${runtime.llm.provider || 'LLM'} · ${runtime.llm.model || 'Live'} · Grounded Q&A`;
        }
        return runtime.llm?.mode === 'degraded'
          ? 'Main AI · Degraded Local Grounded Q&A'
          : 'Main AI · Local Grounded Q&A';
      }
      if (type === 'profile_update') return 'Main AI · Local Profile Parser';
      if (type === 'clarification') return 'Main AI · Local Skin Resolver';
      if (type === 'prediction') {
        if (!runtime.hybrid) return 'Hybrid · Forecast';
        return runtime.hybrid?.mode === 'live'
          ? `Hybrid · Live (${runtime.hybrid.model || '—'})`
          : 'Hybrid · Unavailable';
      }
      if (type === 'recommendation') return 'Recommendation Agent · Local Rules';
      if (runtime.llm?.mode === 'live') {
        return `${runtime.llm.provider || 'LLM'} · ${runtime.llm.model || 'Live'}`;
      }
      if (runtime.llm?.mode === 'configured') {
        return `${runtime.llm.provider || 'LLM'} · Configured`;
      }
      return runtime.llm?.mode === 'degraded'
        ? 'Live request failed · Browser fallback'
        : 'Mock · Local Template';
    };

    const latestAgentResult = (session, agentName) => {
      if (!session) return null;
      const key = `${agentName}History`;
      const history = session[key] || [];
      return history.length ? history[history.length - 1] : null;
    };

    const agentResultLines = (result) => {
      if (!result) return [];
      if (Array.isArray(result.arguments)) {
        return result.arguments.map(item => item.claim).filter(Boolean);
      }
      return Array.isArray(result.reasoning) ? result.reasoning : [];
    };

    const openPredictionResult = (skinId) => {
      if (skinId) viewSkin(skinId);
    };

    const continueDebate = (message) => {
      if (!chatAgentSession.value || chatLoading.value) return;
      // 延续当前辩论会话：不能传 action:'debate'（会被当成“新开辩论”，
      // 丢失 sessionId 后因消息里没有皮肤名而失败）；也不能走 setChatMode
      // （它会清空当前会话）。直接确保处于辩论模式后按 auto 续会话发送。
      chatMode.value = 'debate';
      sendMessage(message);
    };

    const debateStageStep = (msg) => {
      const round = msg?.payload?.debateRound;
      if (!round?.bull) return 'bull';
      if (!round?.bear) return 'bear';
      if (!round?.judge) return 'judge';
      return 'done';
    };

    const isDebateInProgress = (msg) => {
      const round = msg?.payload?.debateRound;
      if (!round || round.judge) return false;
      const last = chatMessages.value[chatMessages.value.length - 1];
      if (msg !== last) return false;
      return Boolean(msg?.debateStage || chatLoading.value);
    };

    const debateStageHint = (msg) => {
      const step = debateStageStep(msg);
      if (step === 'bear') return t('debate.stage.bear');
      if (step === 'judge') return t('debate.stage.judge');
      return t('debate.stage.bull');
    };

    // ============ 行情看板 ============
    const filterCategory = ref('all');
    const skinSearch = ref('');
    const skinSort = ref('change7d');
    const skinSortDir = ref('desc'); // desc = 从大到小, asc = 从小到大
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

    // CS rarity palette (Steam / BUFF). Rank fallback when rarityName missing.
    const RARITY_META = {
      consumer: { rank: 1, color: '#b0c3d9', key: 'rarity.consumer' },
      industrial: { rank: 2, color: '#5e98d9', key: 'rarity.industrial' },
      milspec: { rank: 3, color: '#4b69ff', key: 'rarity.milspec' },
      restricted: { rank: 4, color: '#8847ff', key: 'rarity.restricted' },
      classified: { rank: 5, color: '#d32ce6', key: 'rarity.classified' },
      covert: { rank: 6, color: '#eb4b4b', key: 'rarity.covert' },
      contraband: { rank: 7, color: '#e4ae39', key: 'rarity.contraband' },
      extraordinary: { rank: 7, color: '#e4ae39', key: 'rarity.extraordinary' },
      rare: { rank: 7, color: '#e4ae39', key: 'rarity.rare' },
      base: { rank: 1, color: '#b0c3d9', key: 'rarity.base' },
    };
    const RARITY_BY_RANK = {
      1: RARITY_META.consumer,
      2: RARITY_META.industrial,
      3: RARITY_META.milspec,
      4: RARITY_META.restricted,
      5: RARITY_META.classified,
      6: RARITY_META.covert,
      7: RARITY_META.contraband,
    };
    const resolveRarityMeta = (skin) => {
      const raw = String(skin?.rarityName || skin?.rarity_name || '').toLowerCase();
      if (raw.includes('contraband')) return RARITY_META.contraband;
      if (raw.includes('extraordinary')) return RARITY_META.extraordinary;
      if (raw.includes('covert')) return RARITY_META.covert;
      if (raw.includes('classified')) return RARITY_META.classified;
      if (raw.includes('restricted')) return RARITY_META.restricted;
      if (raw.includes('mil') || raw.includes('milspec')) return RARITY_META.milspec;
      if (raw.includes('industrial')) return RARITY_META.industrial;
      if (raw.includes('consumer')) return RARITY_META.consumer;
      if (raw.includes('rare')) return RARITY_META.rare;
      if (raw.includes('base')) return RARITY_META.base;
      const rank = Number(skin?.rarity);
      return RARITY_BY_RANK[rank] || RARITY_META.restricted;
    };
    const rarityLabel = (skin) => {
      const meta = resolveRarityMeta(skin);
      return t(meta.key);
    };
    const rarityColor = (skin) => resolveRarityMeta(skin).color;
    const rarityStyle = (skin) => ({ '--rarity-color': rarityColor(skin) });

    // Official CS schinese / BUFF-style names (not machine translation).
    const WEAR_ZH = {
      'Factory New': '崭新出厂', FN: '崭新出厂',
      'Minimal Wear': '略有磨损', MW: '略有磨损',
      'Field-Tested': '久经沙场', FT: '久经沙场',
      'Well-Worn': '破损不堪', WW: '破损不堪',
      'Battle-Scarred': '战痕累累', BS: '战痕累累',
    };
    const skinDisplayName = (skinOrName) => {
      const name = typeof skinOrName === 'string'
        ? skinOrName
        : (skinOrName?.name || skinOrName?.market_hash_name || '');
      if (!name) return '';
      if (currentLang.value !== 'zh-CN') return name;
      const map = window.SKIN_NAMES_ZH || {};
      return map[name] || name;
    };
    const wearLabel = (wear) => {
      if (!wear || wear === 'N/A' || wear === 'nan') return '';
      if (currentLang.value !== 'zh-CN') return wear;
      return WEAR_ZH[wear] || wear;
    };
    // Community nicknames used in Chinese search (display still uses official schinese).
    const SKIN_NICK_ZH = [
      { nick: '火蛇', hint: 'fire serpent' },
      { nick: '龙狙', hint: 'dragon lore' },
      { nick: '巨龙传说', hint: 'dragon lore' },
      { nick: '咆哮', hint: 'howl' },
      { nick: '杀意', hint: 'howl' },
      { nick: '二西莫夫', hint: 'asiimov' },
      { nick: '红线', hint: 'redline' },
      { nick: '表面淬火', hint: 'case hardened' },
      { nick: '多普勒', hint: 'doppler' },
      { nick: '渐变之色', hint: 'fade' },
      { nick: '血腥运动', hint: 'bloodsport' },
      { nick: '印花集', hint: 'printstream' },
    ];
    const skinSearchText = (s) => {
      const base = `${s.name || ''} ${skinDisplayName(s)} ${s.wear || ''} ${wearLabel(s.wear)} ${s.category || ''} ${categoryLabel(s.category)}`;
      const n = String(s.name || '').toLowerCase();
      const nicks = SKIN_NICK_ZH.filter(x => n.includes(x.hint)).map(x => x.nick).join(' ');
      return `${base} ${nicks}`.toLowerCase();
    };

    const formatChange = (num) => {
      const v = Number(num);
      if (!Number.isFinite(v)) return '0.00%';
      const sign = v > 0 ? '+' : '';
      return `${sign}${v.toFixed(2)}%`;
    };

    const formatVolume = (num) => {
      const v = Number(num);
      // 无真实日成交量时不展示伪造数字
      if (!Number.isFinite(v) || v <= 0) return '—';
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
      const changes = [];
      for (const s of list) {
        const ch = Number(s.change7d) || 0;
        changes.push(ch);
        if (ch > 0) up += 1;
        else if (ch < 0) down += 1;
        sum += ch;
        if (s.isLive !== false) live += 1;
      }
      const total = list.length;
      const moved = up + down;
      const avg = total ? sum / total : 0;
      const livePct = total ? ((live / total) * 100).toFixed(1) : '0.0';
      const upPct = moved ? ((up / moved) * 100).toFixed(1) : '0.0';
      const downPct = moved ? ((down / moved) * 100).toFixed(1) : '0.0';

      // Sparkline = winsorized 7d return distribution, not a fake time series.
      // Trim the outer 5% so one extreme skin does not create an artificial spike.
      const pts = 24;
      const w = 120;
      const h = 40;
      const padX = 4;
      const padY = 8;
      let sparkLine = `M${padX},${h / 2} L${w - padX},${h / 2}`;
      let sparkArea = `${sparkLine} L${w - padX},${h} L${padX},${h} Z`;
      if (changes.length >= 2) {
        const sorted = [...changes].sort((a, b) => a - b);
        const vals = [];
        for (let i = 0; i < pts; i += 1) {
          const percentile = 0.05 + (i / (pts - 1)) * 0.90;
          const idx = Math.round(percentile * (sorted.length - 1));
          vals.push(sorted[idx]);
        }
        const min = Math.min(...vals);
        const max = Math.max(...vals);
        const range = max - min || 1;
        const coords = vals.map((v, i) => {
          const x = padX + (i / (pts - 1)) * (w - padX * 2);
          const y = padY + (1 - (v - min) / range) * (h - padY * 2);
          return [x, y];
        });
        const smoothPath = (points) => {
          let path = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
          for (let i = 1; i < points.length - 1; i += 1) {
            const midX = (points[i][0] + points[i + 1][0]) / 2;
            const midY = (points[i][1] + points[i + 1][1]) / 2;
            path += ` Q${points[i][0].toFixed(1)},${points[i][1].toFixed(1)} ${midX.toFixed(1)},${midY.toFixed(1)}`;
          }
          const lastPoint = points[points.length - 1];
          return `${path} T${lastPoint[0].toFixed(1)},${lastPoint[1].toFixed(1)}`;
        };
        sparkLine = smoothPath(coords);
        const last = coords[coords.length - 1];
        const first = coords[0];
        sparkArea = `${sparkLine} L${last[0].toFixed(1)},${h} L${first[0].toFixed(1)},${h} Z`;
      }

      return {
        total,
        live,
        up,
        down,
        avg,
        livePct,
        upPct,
        downPct,
        sparkLine,
        sparkArea,
      };
    });

    // Fear & Greed style index from live breadth + momentum (not hardcoded).
    // Weights: 7d breadth 40% · 7d avg return 35% · 24h breadth 15% · extreme movers 10%
    const marketSentiment = computed(() => {
      const liveOnly = (skins.value || []).filter((s) => s.isLive !== false);
      const list = liveOnly.length >= 10 ? liveOnly : (skins.value || []);
      const clamp01 = (x) => Math.max(0, Math.min(1, x));
      const mapReturn = (avgPct, span = 8) => clamp01((avgPct + span) / (2 * span)) * 100;
      const breadthScore = (ups, downs) => {
        const n = ups + downs;
        return n ? (ups / n) * 100 : 50;
      };

      let up7 = 0, down7 = 0, sum7 = 0;
      let up24 = 0, down24 = 0;
      let extUp = 0, extDown = 0;
      for (const s of list) {
        const c7 = Number(s.change7d) || 0;
        const c24 = Number(s.change24h) || 0;
        if (c7 > 0) up7 += 1;
        else if (c7 < 0) down7 += 1;
        sum7 += c7;
        if (c24 > 0) up24 += 1;
        else if (c24 < 0) down24 += 1;
        if (Math.abs(c7) >= 3) {
          if (c7 > 0) extUp += 1;
          else extDown += 1;
        }
      }

      const n = list.length;
      const avg7 = n ? sum7 / n : 0;
      const sBreadth7 = breadthScore(up7, down7);
      const sMom7 = mapReturn(avg7, 8);
      const sBreadth24 = breadthScore(up24, down24);
      const sExtreme = breadthScore(extUp, extDown);
      const raw = 0.40 * sBreadth7 + 0.35 * sMom7 + 0.15 * sBreadth24 + 0.10 * sExtreme;
      const score = n ? Math.round(Math.max(0, Math.min(100, raw))) : 50;

      let labelKey = 'topbar.sentiment.neutral';
      let icon = 'ph-minus';
      let tone = 'var(--text-muted)';
      if (score <= 24) {
        labelKey = 'topbar.sentiment.extremeFear';
        icon = 'ph-trend-down';
        tone = 'var(--accent-red)';
      } else if (score <= 44) {
        labelKey = 'topbar.sentiment.fear';
        icon = 'ph-trend-down';
        tone = 'var(--accent-red)';
      } else if (score <= 55) {
        labelKey = 'topbar.sentiment.neutral';
        icon = 'ph-minus';
        tone = 'var(--text-muted)';
      } else if (score <= 75) {
        labelKey = 'topbar.sentiment.greed';
        icon = 'ph-trend-up';
        tone = 'var(--accent-green)';
      } else {
        labelKey = 'topbar.sentiment.extremeGreed';
        icon = 'ph-trend-up';
        tone = 'var(--accent-green)';
      }

      const hint = currentLang.value === 'zh-CN'
        ? `由 ${n} 件饰品计算：7日涨跌比 ${sBreadth7.toFixed(0)} · 7日均涨跌 ${avg7.toFixed(2)}% · 24h涨跌比 ${sBreadth24.toFixed(0)} · 极端波动 ${sExtreme.toFixed(0)}`
        : `From ${n} skins: 7d breadth ${sBreadth7.toFixed(0)} · 7d avg ${avg7.toFixed(2)}% · 24h breadth ${sBreadth24.toFixed(0)} · extremes ${sExtreme.toFixed(0)}`;

      return { score, labelKey, icon, tone, hint, n, avg7 };
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
        list = list.filter(s => skinSearchText(s).includes(q));
      }
      const sorted = [...list];
      const sort = skinSort.value;
      const dir = skinSortDir.value === 'asc' ? 1 : -1;
      sorted.sort((a, b) => {
        let cmp = 0;
        if (sort === 'name') {
          const loc = currentLang.value === 'zh-CN' ? 'zh-CN' : 'en';
          cmp = skinDisplayName(a).localeCompare(skinDisplayName(b), loc);
        } else if (sort === 'price') {
          cmp = (Number(a.price) || 0) - (Number(b.price) || 0);
        } else if (sort === 'change24h') {
          cmp = (Number(a.change24h) || 0) - (Number(b.change24h) || 0);
        } else if (sort === 'rarity') {
          cmp = (Number(a.rarity) || 0) - (Number(b.rarity) || 0);
        } else {
          cmp = (Number(a.change7d) || 0) - (Number(b.change7d) || 0);
        }
        return cmp * dir;
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
    watch([filterCategory, skinSearch, skinSort, skinSortDir], () => {
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
    const predictionStatus = ref('idle');
    const predictionReason = ref('');
    const predictionCalibration = ref(null);
    // v5 契约: LSTM 系列返回 7 天逐日精确预测 { model, base(决策日价), prices[7] }
    const predictionDaily = ref(null);
    // Optional Keras probability trend from the live API; never synthesized.
    const predictionTrend30d = ref(null);
    const calibrationEvidence = computed(() => {
      const calibration = predictionCalibration.value;
      if (!calibration) return null;
      const weights = calibration.weights?.d7 || {};
      const reasons = Array.isArray(calibration.reasonCodes) ? calibration.reasonCodes : [];
      return {
        c: Math.round(Number(weights.c || 0) * 100),
        d: Math.round(Number(weights.d || 0) * 100),
        recent: Math.round(Number(weights.recent || 0) * 100),
        disagreement: (Number(calibration.modelDisagreement || 0) * 100).toFixed(1),
        compressed: reasons.includes('SMOOTH_DEVIATION_COMPRESSION'),
      };
    });
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

    const resolveNewsUrl = (news) => {
      const direct = String(news?.url || '').trim();
      if (direct) return direct;
      const title = String(news?.title || '').trim().toLowerCase();
      if (!title) return '';
      const hit = (newsFeed.value || []).find((n) => {
        const u = String(n?.url || '').trim();
        return u && String(n?.title || '').trim().toLowerCase() === title;
      });
      return hit?.url ? String(hit.url).trim() : '';
    };

    const openNewsItem = (news) => {
      const url = resolveNewsUrl(news);
      if (url) {
        openExternalUrl(url);
        return;
      }
      showToast({ title: t('daily.newsNoLink'), type: 'info' });
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
        ma7 = window.CSVestData.calculateMA(kline, 7);
        ma30 = window.CSVestData.calculateMA(kline, 30);
        if (!modelPredictions.value.length && predictionStatus.value !== 'unavailable') {
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
      const trendPath = predictionTrend30d.value;
      const predictionUnavailable = predictionStatus.value === 'unavailable';
      const hasTrend = !predictionUnavailable
        && trendPath?.horizon === 30
        && ['p10', 'p50', 'p90'].every(
          key => Array.isArray(trendPath[key]) && trendPath[key].length === 30
        );
      const exactHorizon = predictionUnavailable ? 0 : ((dailyPath?.prices?.length) || 7);
      const horizon = Math.max(exactHorizon, hasTrend ? 30 : 0);
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
      // 预测线与最后一根 K 线在 x 轴上衔接,避免视觉断层。
      // 桥接点取值:正常用 lastClose;若 lastClose 相对首日预测偏离过大
      // (末端脏价),改用首日预测价,既不断层也不把异常收盘画成 AI 预测尖峰。
      let bridgeValue = lastClose;
      let dirtyAnchor = false;
      if (dailyPath?.prices?.length && dailyPath.base > 0) {
        const firstPred = Number(dailyPath.prices[0]);
        dirtyAnchor = Boolean(dailyPath.anchorApplied) || (
          firstPred > 0
          && Math.max(lastClose / firstPred, firstPred / lastClose) >= 1.5
        );
        if (dirtyAnchor) bridgeValue = Number(dailyPath.base || firstPred);
        for (const p of dailyPath.prices) {
          const value = dirtyAnchor
            ? Number(p)
            : lastClose * (Number(p) / dailyPath.base);
          predictedValues.push(value.toFixed(2));
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
        for (let i = 1; i <= exactHorizon; i++) {
          const t = i / exactHorizon;
          const eased = 1 - Math.pow(1 - t, 2); // ease-out：前快后缓
          const wiggle = i === horizon ? 0 : rand() * dailyVol;
          predictedValues.push((lastClose * (1 + predChange * eased + wiggle)).toFixed(2));
        }
      }
      const bridgePoint = Number(bridgeValue).toFixed(2);

      // 无真实日成交量:只画主图,不再渲染量能副图
      const forecastPad = predictedDates.map(() => '-');
      const categoryDates = kline.map(d => d[0]).concat(predictedDates);
      const emptySeries = () => new Array(categoryDates.length).fill('-');
      const exactTail = new Array(Math.max(horizon - predictedValues.length, 0)).fill('-');
      const exactSeries = predictionUnavailable
        ? emptySeries()
        : new Array(kline.length - 1).fill('-')
          .concat([bridgePoint], predictedValues, exactTail);
      const trendPrefix = new Array(kline.length + Math.max(exactHorizon - 1, 0)).fill('-');
      const trendSeries = (values = []) => hasTrend
        ? trendPrefix.concat(values)
        : emptySeries();
      const authoritativeTrend = hasTrend
        ? trendPath.p50.slice(Math.max(exactHorizon - 1, 0)).map(Number)
        : [];
      const candleName = t('prediction.chart.kline');
      const forecast7Name = t('prediction.chart.forecast7d');
      const trendMedianName = t('prediction.chart.trend30d');
      const legendData = [candleName, 'MA7', 'MA30', forecast7Name];
      if (hasTrend) legendData.push(trendMedianName);
      const isLight = document.documentElement.getAttribute('data-theme') === 'light';
      // Light theme needs stronger label contrast against cream glass; keep axes quiet but readable.
      const axisMuted = isLight ? 'rgba(58, 48, 40, 0.72)' : 'rgba(156, 163, 175, 0.42)';
      const legendColor = isLight ? 'rgba(48, 40, 32, 0.82)' : 'rgba(186, 193, 204, 0.72)';
      const legendInactive = isLight ? 'rgba(120, 108, 96, 0.45)' : 'rgba(156, 163, 175, 0.28)';
      const splitMuted = isLight ? 'rgba(90, 78, 66, 0.14)' : 'rgba(148, 163, 184, 0.08)';
      const tipBg = isLight ? 'rgba(255, 252, 247, 0.96)' : 'rgba(17, 22, 31, 0.92)';
      const tipBorder = isLight ? 'rgba(40, 32, 24, 0.14)' : 'rgba(255, 255, 255, 0.08)';
      const tipText = isLight ? '#2a241c' : '#e5e7eb';
      const option = {
        backgroundColor: 'transparent',
        animation: true,
        animationDuration: 420,
        animationEasing: 'cubicOut',
        legend: {
          type: 'scroll',
          data: legendData,
          icon: 'roundRect',
          itemWidth: 12,
          itemHeight: 6,
          itemGap: 14,
          textStyle: { color: legendColor, fontSize: 11, fontWeight: 500 },
          inactiveColor: legendInactive,
          pageTextStyle: { color: legendColor },
          pageIconColor: isLight ? 'rgba(58, 48, 40, 0.55)' : 'rgba(156, 163, 175, 0.55)',
          pageIconInactiveColor: legendInactive,
          top: 4,
          left: 8,
          right: 8,
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'cross',
            crossStyle: { color: isLight ? 'rgba(180, 90, 30, 0.4)' : 'rgba(255, 107, 0, 0.35)', width: 1 },
            lineStyle: { color: isLight ? 'rgba(180, 90, 30, 0.32)' : 'rgba(255, 107, 0, 0.28)', type: 'dashed' },
          },
          backgroundColor: tipBg,
          borderColor: tipBorder,
          borderWidth: 1,
          padding: [10, 12],
          textStyle: { color: tipText, fontSize: 12 },
          extraCssText: 'backdrop-filter:blur(10px);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,0.18);',
        },
        grid: { left: 52, right: 18, top: 46, bottom: 34 },
        xAxis: {
          type: 'category',
          data: categoryDates,
          boundaryGap: true,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: {
            color: axisMuted,
            fontSize: isLight ? 11 : 10,
            fontWeight: isLight ? 500 : 400,
            hideOverlap: true,
            margin: 10,
          },
          splitLine: { show: false },
        },
        yAxis: {
          scale: true,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: {
            color: axisMuted,
            fontSize: isLight ? 11 : 10,
            fontWeight: isLight ? 500 : 400,
            margin: 10,
          },
          splitLine: { lineStyle: { color: splitMuted, type: 'dashed', width: 1 } },
          splitArea: { show: false },
        },
        dataZoom: [
          { type: 'inside', start: 50, end: 100, zoomOnMouseWheel: true },
        ],
        series: [
          {
            // Split up/down so each candle can bloom its own colored glow onto the dark plate.
            name: candleName,
            type: 'candlestick',
            data: kline.map((d) => {
              const open = d[1];
              const close = d[2];
              return close >= open ? [open, close, d[3], d[4]] : '-';
            }),
            barMaxWidth: 10,
            itemStyle: {
              color: 'rgba(239, 68, 68, 0.88)',
              color0: 'rgba(239, 68, 68, 0.88)',
              borderColor: 'rgba(255, 120, 120, 0.98)',
              borderColor0: 'rgba(255, 120, 120, 0.98)',
              borderWidth: 1,
              shadowBlur: 16,
              shadowColor: 'rgba(239, 68, 68, 0.45)',
            },
            z: 2,
          },
          {
            name: candleName,
            type: 'candlestick',
            data: kline.map((d) => {
              const open = d[1];
              const close = d[2];
              return close < open ? [open, close, d[3], d[4]] : '-';
            }),
            barMaxWidth: 10,
            itemStyle: {
              color: 'rgba(16, 185, 129, 0.86)',
              color0: 'rgba(16, 185, 129, 0.86)',
              borderColor: 'rgba(110, 231, 183, 0.95)',
              borderColor0: 'rgba(110, 231, 183, 0.95)',
              borderWidth: 1,
              shadowBlur: 16,
              shadowColor: 'rgba(16, 185, 129, 0.42)',
            },
            z: 2,
          },
          {
            name: 'MA7',
            type: 'line',
            data: ma7.concat(forecastPad),
            smooth: 0.25,
            showSymbol: false,
            lineStyle: {
              color: 'rgba(251, 191, 36, 0.88)',
              width: 1.25,
              shadowBlur: 4,
              shadowColor: 'rgba(251, 191, 36, 0.18)',
            },
            z: 3,
          },
          {
            name: 'MA30',
            type: 'line',
            data: ma30.concat(forecastPad),
            smooth: 0.3,
            showSymbol: false,
            lineStyle: {
              color: 'rgba(167, 139, 250, 0.82)',
              width: 1.25,
              shadowBlur: 4,
              shadowColor: 'rgba(167, 139, 250, 0.16)',
            },
            z: 3,
          },
          {
            name: forecast7Name,
            type: 'line',
            data: exactSeries,
            smooth: 0.35,
            showSymbol: false,
            lineStyle: {
              color: '#ff6b00',
              width: 2,
              type: [5, 5],
              shadowBlur: 8,
              shadowColor: 'rgba(255, 107, 0, 0.28)',
            },
            areaStyle: {
              color: {
                type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(255, 107, 0, 0.18)' },
                  { offset: 1, color: 'rgba(255, 107, 0, 0)' },
                ],
              },
            },
            markArea: {
              silent: true,
              itemStyle: {
                color: {
                  type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
                  colorStops: [
                    { offset: 0, color: 'rgba(255, 107, 0, 0.09)' },
                    { offset: 1, color: 'rgba(255, 140, 64, 0.04)' },
                  ],
                },
              },
              data: predictionUnavailable || !predictedDates.length ? [] : [[
                { xAxis: kline[kline.length - 1][0] },
                { xAxis: predictedDates[Math.min(6, predictedDates.length - 1)] },
              ]],
            },
            z: 4,
          },
          {
            name: trendMedianName,
            type: 'line',
            data: trendSeries(authoritativeTrend),
            smooth: 0.4,
            showSymbol: false,
            connectNulls: false,
            lineStyle: {
              color: '#22c55e',
              width: 2,
              type: [5, 5],
              shadowBlur: 8,
              shadowColor: 'rgba(34, 197, 94, 0.22)',
            },
            areaStyle: {
              color: {
                type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(34, 197, 94, 0.14)' },
                  { offset: 1, color: 'rgba(34, 197, 94, 0)' },
                ],
              },
            },
            markArea: {
              silent: true,
              itemStyle: {
                color: {
                  type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
                  colorStops: [
                    { offset: 0, color: 'rgba(255, 140, 64, 0.05)' },
                    { offset: 0.35, color: 'rgba(34, 197, 94, 0.08)' },
                    { offset: 1, color: 'rgba(34, 197, 94, 0.04)' },
                  ],
                },
              },
              data: !hasTrend || !predictedDates.length ? [] : [[
                { xAxis: predictedDates[Math.min(6, predictedDates.length - 1)] },
                { xAxis: predictedDates[Math.min(29, predictedDates.length - 1)] },
              ]],
            },
            emphasis: { focus: 'series' },
            z: 4,
          },
        ],
      };
      klineChartInstance.setOption(option, true);
      klineLoading.value = false;
    };

    // ============ AI 对话 ============
    // 问答与辩论各自维护独立上下文：两个消息数组分开保存，互不影响；
    // chatMessages 始终指向当前模式的数组，切换模式即切换上下文。
    const makeChatWelcome = () => ([
      {
        role: 'assistant',
        content: '__WELCOME__',
        time: '刚刚',
        model: 'DeepSeek-V3',
      }
    ]);
    const chatMode = ref('qa'); // 'qa' | 'debate'
    const chatHistoryQa = ref(makeChatWelcome());
    const chatHistoryDebate = ref(makeChatWelcome());
    const chatMessages = computed(() => (
      chatMode.value === 'debate' ? chatHistoryDebate.value : chatHistoryQa.value
    ));
    const chatInput = ref('');
    const chatLoading = ref(false);
    // true once streaming chunks start arriving — hides the "thinking" row
    const chatStreaming = ref(false);
    const chatMessagesEl = ref(null);
    const chatSuggestedIndex = ref(-1);
    const chatAgentSession = ref(null);
    // 隐藏“已推入但还没有内容”的助手占位消息，避免加载时出现空气泡
    const visibleChatMessages = computed(() => chatMessages.value.slice(1).filter(
      m => m.role !== 'assistant' || m.content || m.payload || m.debate
    ));
    const activeSuggestedQuestions = computed(() => (
      chatMode.value === 'debate' ? debateSuggestedQuestions.value : suggestedQuestions.value
    ));
    const setChatMode = (mode) => {
      chatMode.value = mode === 'debate' ? 'debate' : 'qa';
      chatSuggestedIndex.value = -1;
      // 两个模式的上下文各自保存：切换模式不清空消息，也不丢弃辩论会话，
      // 切回来时可以继续之前的对话/辩论。
      scrollChatBottom();
    };
    const chatBudget = ref(null);
    const chatRiskLevel = ref('medium');
    let chatLocaleRequest = 0;

    const chatLocaleSnapshot = (message) => ({
      content: message.content,
      debateRound: message.payload?.debateRound || null,
    });

    const saveChatLocale = (message, locale) => {
      if (!message || message.role !== 'assistant' || message.content === '__WELCOME__') return;
      message.localeVersions = message.localeVersions || {};
      message.localeVersions[locale] = chatLocaleSnapshot(message);
    };

    // 辩论增强：轮次计数 + 复制结论
    const debateTotalRounds = computed(() => {
      const session = chatAgentSession.value;
      if (!session?.rounds) return 0;
      return session.rounds.length;
    });

    const copyDebateResult = async (msg) => {
      const debate = msg?.debate;
      if (!debate) return;
      const round = debate.debateRound || (debate.rounds?.[debate.rounds.length - 1]);
      const judge = round?.judge || {};
      const lines = [
        `=== CSVest Debate: ${debate.skin || 'N/A'} ===`,
        `Consensus: ${debate.consensus || 'N/A'}`,
        '',
        judge.recommendation ? `Judge Recommendation:\n${judge.recommendation}` : '',
        judge.decision ? `Decision: ${judge.decision}` : '',
        judge.confidence != null ? `Confidence: ${(judge.confidence * 100).toFixed(0)}%` : '',
      ].filter(Boolean);
      try {
        await navigator.clipboard.writeText(lines.join('\n'));
        if (msg) msg._copied = true;
        showToast({ title: t('chat.copied') || 'Copied', type: 'success', duration: 2000 });
      } catch (_) { /* clipboard not available */ }
    };

    const applyChatLocale = (message, translated) => {
      if (!translated || typeof translated.content !== 'string') return;
      message.content = translated.content;
      if (translated.debateRound && message.payload) {
        message.payload = { ...message.payload, debateRound: translated.debateRound };
      }
    };

    const chatNow = () => new Date().toLocaleTimeString(
      currentLang.value === 'zh-CN' ? 'zh-CN' : 'en-US',
      { hour: '2-digit', minute: '2-digit' }
    );

    const WEAR_ZH_TO_EN = {
      '崭新出厂': 'Factory New',
      '略有磨损': 'Minimal Wear',
      '久经沙场': 'Field-Tested',
      '破损不堪': 'Well-Worn',
      '战痕累累': 'Battle-Scarred',
    };
    const WEAR_PRIORITY = ['Field-Tested', 'Minimal Wear', 'Factory New', 'Well-Worn', 'Battle-Scarred'];

    const normalizeSkinQueryText = (text) => String(text || '')
      .replace(/[★™（）()]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    const englishWeaponFromName = (name) => String(name || '')
      .split('|')[0]
      .replace(/^(StatTrak™|Souvenir|★)\s*/i, '')
      .trim();

    const wearRank = (name) => {
      const match = String(name || '').match(/\(([^)]+)\)\s*$/);
      const idx = WEAR_PRIORITY.indexOf(match?.[1] || '');
      return idx >= 0 ? idx : WEAR_PRIORITY.length;
    };

    const weaponMatchesQuery = (message, enName, weaponZh) => {
      const msg = String(message || '').toLowerCase();
      const norm = normalizeSkinQueryText(message).toLowerCase();
      const weaponEn = englishWeaponFromName(enName).toLowerCase();
      if (weaponZh && norm.includes(String(weaponZh).toLowerCase())) return true;
      if (weaponEn && msg.includes(weaponEn)) return true;
      if (weaponEn.startsWith('m4a1-s') && (norm.includes('m4a1消音版') || msg.includes('m4a1-s'))) return true;
      if (weaponEn.includes('karambit') && norm.includes('爪子刀')) return true;
      if (weaponEn.includes('butterfly') && norm.includes('蝴蝶刀')) return true;
      return false;
    };

    const parseZhDisplayName = (zhName) => {
      let text = normalizeSkinQueryText(zhName);
      let wear = null;
      const wearMatch = text.match(/\(([^)]+)\)\s*$/);
      if (wearMatch) {
        wear = wearMatch[1].trim();
        text = text.slice(0, wearMatch.index).trim();
      }
      if (!text.includes('|')) return { weaponZh: '', skinZh: text, wearZh: wear };
      const [weaponZh, skinZh] = text.split('|').map(part => part.trim());
      return { weaponZh, skinZh, wearZh: wear };
    };

    const resolveSkinFromQuery = (query) => {
      const raw = String(query || '').trim();
      const q = raw.toLowerCase();
      const normQuery = normalizeSkinQueryText(raw);
      const list = skins.value || [];
      if (!raw) return selectedSkin.value || null;

      const byId = list.find(s => String(s.id || '').toLowerCase() === q);
      if (byId) return byId;

      const zhMap = window.SKIN_NAMES_ZH || {};
      const entries = Object.entries(zhMap).filter(([enName]) =>
        list.some(s => s.name === enName || String(s.id || '').toLowerCase() === String(enName).toLowerCase())
      );
      const findByEn = (enName) => list.find(s => s.name === enName)
        || list.find(s => String(s.id || '').toLowerCase() === String(enName).toLowerCase());

      const scored = [];
      for (const [enName, zhName] of entries) {
        const zhNorm = normalizeSkinQueryText(zhName);
        if (raw.includes(zhName) || (zhNorm && normQuery.includes(zhNorm))) {
          scored.push({ enName, score: 3, length: zhNorm.length });
          continue;
        }
        const { weaponZh, skinZh, wearZh } = parseZhDisplayName(zhName);
        if (!skinZh || !raw.includes(skinZh)) continue;
        if (!weaponMatchesQuery(raw, enName, weaponZh)) continue;
        scored.push({
          enName,
          score: wearZh && raw.includes(wearZh) ? 2 : 1,
          length: skinZh.length,
        });
      }

      if (!scored.length) {
        for (const item of SKIN_NICK_ZH) {
          if (!raw.includes(item.nick)) continue;
          const family = entries
            .map(([enName]) => enName)
            .filter(enName => enName.toLowerCase().includes(String(item.hint).toLowerCase()));
          for (const enName of family) {
            scored.push({ enName, score: 2, length: item.nick.length });
          }
        }
      }

      if (scored.length) {
        scored.sort((a, b) => b.score - a.score || b.length - a.length || wearRank(a.enName) - wearRank(b.enName));
        let candidates = [...new Set(scored.map(item => item.enName))];
        const mentionedWeapons = candidates
          .map(englishWeaponFromName)
          .filter(weapon => weapon && raw.toLowerCase().includes(weapon.toLowerCase()));
        if (mentionedWeapons.length) {
          const weaponSet = new Set(mentionedWeapons.map(w => w.toLowerCase()));
          const filtered = candidates.filter(name => weaponSet.has(englishWeaponFromName(name).toLowerCase()));
          if (filtered.length) candidates = filtered;
        }
        for (const wearZh of Object.keys(WEAR_ZH_TO_EN)) {
          if (!raw.includes(wearZh)) continue;
          const wearEn = WEAR_ZH_TO_EN[wearZh];
          const filtered = candidates.filter(name => name.includes(`(${wearEn})`));
          if (filtered.length) {
            candidates = filtered;
            break;
          }
        }
        const weapons = [...new Set(candidates.map(englishWeaponFromName))];
        const chosenEn = weapons.length === 1
          ? candidates.slice().sort((a, b) => wearRank(a) - wearRank(b))[0]
          : candidates[0];
        const hit = findByEn(chosenEn);
        if (hit) return hit;
      }

      // English / slug substring match
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

      if (selectedSkin.value && /(辩论|debate|多空|牛熊|这个皮肤|这件|当前这[个件]|开始辩论)/i.test(raw)) {
        return selectedSkin.value;
      }
      return null;
    };

    const canSendChat = computed(() => {
      if (chatLoading.value) return false;
      return !!chatInput.value.trim();
    });

    const runSkinAction = async (skinRef, action) => {
      const cardSkin = skinRef && typeof skinRef === 'object' ? skinRef : null;
      const skinId = cardSkin?.skinId || cardSkin?.id || skinRef;
      const poolSkin = skins.value.find(item => item.id === skinId);
      const skin = poolSkin || (cardSkin ? {
        id: skinId,
        name: cardSkin.name || skinId,
        price: cardSkin.price ?? null,
        change7d: cardSkin.change7d ?? 0,
        liquidity: cardSkin.liquidity ?? null,
        volume24h: cardSkin.volume24h ?? null,
      } : null);
      if (skin) selectedSkin.value = skin;
      currentPage.value = 'chat';
      const label = skin?.name || skinId;
      const english = currentLang.value === 'en-US';
      if (action === 'debate') {
        chatMode.value = 'debate';
        chatAgentSession.value = null;
      } else if (action === 'predict') {
        chatMode.value = 'qa';
      }
      if (false && action === 'debate') {
        const prompt = english
          ? `Ask Bull, Bear and Judge to assess whether I should choose ${label}`
          : `请让 Bull、Bear 和 Judge 分析我是否应该选择 ${label}`;
        chatAgentSession.value = null;
        chatMessages.value.push({
          role: 'user',
          content: prompt,
          time: chatNow(),
        });
        chatInput.value = '';
        chatLoading.value = true;
        await scrollChatBottom();

        const assistantMsg = {
          role: 'assistant',
          content: '',
          time: chatNow(),
          model: 'Bull / Bear / Judge',
        };
        chatMessages.value.push(assistantMsg);

        try {
          const client = api();
          if (client && typeof client.createAgentSession === 'function') {
            const session = await client.createAgentSession({
              skinId,
              skin: skin ? {
                id: skin.id,
                name: skin.name,
                price: skin.price,
                change7d: skin.change7d,
                liquidity: skin.liquidity,
                volume24h: skin.volume24h,
              } : null,
              budget: chatBudget.value ? Number(chatBudget.value) : null,
              horizonDays: 7,
              riskLevel: chatRiskLevel.value,
              rounds: 1,
              locale: currentLang.value,
            });
            chatAgentSession.value = session;
            if (session?.userProfile) {
              chatBudget.value = session.userProfile.budget;
              chatRiskLevel.value = session.userProfile.risk_level || chatRiskLevel.value;
            }
            const latestRound = Array.isArray(session?.debateRounds) && session.debateRounds.length
              ? session.debateRounds[session.debateRounds.length - 1]
              : null;
            const response = {
              type: 'debate',
              message: english
                ? `The first independent analysis for ${label} is complete. Tell me your view or concern and Main AI will moderate another evidence-based round.`
                : `已针对 ${label} 完成第一轮独立分析。接下来你可以直接告诉我你的看法或顾虑，Main AI 会主持下一轮有证据的辩论。`,
              skin,
              agentSession: session,
              debateRound: latestRound,
              runtime: {
                llm: { mode: 'structured_mock', provider: 'Local evidence rules', model: 'structured-fallback' },
                agents: { mode: 'structured_mock', bullModel: 'Bull', bearModel: 'Bear', judgeModel: 'Judge' },
                hybrid: { mode: 'mock', model: 'trend-fallback' },
              },
            };
            assistantMsg.content = response.message;
            assistantMsg.kind = response.type;
            assistantMsg.payload = response;
            assistantMsg.model = responseModelLabel(response);
          } else {
            await sendMessage(prompt, { action: 'debate', skinId });
            return;
          }
        } catch (e) {
          assistantMsg.content = currentLang.value === 'en-US'
            ? 'The debate could not be created. Please select a skin and start a new debate.'
            : '辩论引擎响应超时了，可能后端正在计算第一轮。请稍后再试一次。';
          assistantMsg.kind = 'debate_error';
          assistantMsg.payload = { error: String(e?.message || e) };
          assistantMsg.model = 'Bull / Bear / Judge';
        }
        chatLoading.value = false;
        await scrollChatBottom();
        return;
      }

      const prompt = action === 'predict'
        ? (english
          ? `Forecast the price trend of ${label} over the next 7 days`
          : `预测 ${label} 未来 7 天的价格走势`)
        : (english
          ? `Ask Bull, Bear and Judge to assess whether I should choose ${label}`
          : `请让 Bull、Bear 和 Judge 分析我是否应该选择 ${label}`);
      await nextTick();
      return sendMessage(prompt, { action, skinId, skin });
    };

    const sendMessage = async (overrideText, requestOptions = {}) => {
      const text = (typeof overrideText === 'string' ? overrideText : chatInput.value).trim();
      if (!text || chatLoading.value) return;
      if (!requestOptions || typeof requestOptions !== 'object' || requestOptions instanceof Event) {
        requestOptions = {};
      }

      chatMessages.value.push({
        role: 'user',
        content: text,
        time: chatNow(),
      });
      chatInput.value = '';
      chatLoading.value = true;
      await scrollChatBottom();

      chatMessages.value.push({
        role: 'assistant',
        content: '',
        time: chatNow(),
        model: 'AI',
      });
      // 必须通过响应式代理来写 content，否则流式/最终回复不会触发界面刷新，
      // 表现为“发送下一条消息时上一条回复才显示”。
      const assistantMsg = chatMessages.value[chatMessages.value.length - 1];

      try {
        const client = api();
        const historyPayload = chatMessages.value.slice(1, -2).slice(-8)
          .filter(item => item.content && item.content !== '__WELCOME__')
          .map(item => ({ role: item.role, content: item.content }));
        // 纯聊天类问题（不需要推荐/预测卡片）直接走 SSE 流式，首字 1~2 秒可见
        const structuredWords = [
          '推荐', '选一个', '有哪些', 'recommend', 'suggest',
          '预测', '价格', '走势', '涨跌', '目标价', 'forecast', 'price', 'trend',
        ];
        const modelPerfWords = [
          '模型表现', '模型对比', '预测模型', '各个模型', '各模型', '模型实验室',
          'model comparison', 'model performance', 'models lab', 'model-comparison',
          'compare models', 'comparison results',
        ];
        const lowerText = text.toLowerCase();
        const isModelPerf = modelPerfWords.some(w => lowerText.includes(w.toLowerCase()));
        const wantsStructured = !isModelPerf && structuredWords.some(w => lowerText.includes(w));
        const plainQaStream = chatMode.value === 'qa'
          && !requestOptions.action && !requestOptions.skinId
          && !wantsStructured
          && client && typeof client.chat === 'function';

        if (plainQaStream) {
          await client.chat(text, null, (chunk) => {
            chatStreaming.value = true;
            assistantMsg.content += chunk;
            scrollChatBottom();
          }, currentLang.value, historyPayload);
          if (!assistantMsg.content.trim()) {
            throw new Error('Live AI returned empty content');
          }
          assistantMsg.kind = 'chat';
          assistantMsg.model = 'DeepSeek-V3';
          saveChatLocale(assistantMsg, currentLang.value);
        } else if (client && typeof client.orchestrateAI === 'function') {
          // 严格模式：问答模式永远走 qa（后端不会切入辩论管线）；
          // 辩论会话只在辩论模式下延续。
          const continueActiveDebate = chatMode.value === 'debate'
            && chatAgentSession.value
            && !requestOptions.action && !requestOptions.skinId;
          const action = requestOptions.action
            || (chatMode.value === 'debate'
              ? (continueActiveDebate ? 'auto' : 'debate')
              : 'qa');
          // 本地先把中英文饰品名解析成 skinId，再交给后端 orchestrator。
          let resolvedSkinId = requestOptions.skinId || null;
          let resolvedSkin = requestOptions.skin || null;
          if (!resolvedSkinId && !continueActiveDebate && !isModelPerf) {
            const localSkin = resolveSkinFromQuery(text);
            if (localSkin) {
              resolvedSkinId = localSkin.id;
              resolvedSkin = resolvedSkin || localSkin;
            }
          }
          if (isModelPerf) {
            resolvedSkinId = null;
            resolvedSkin = null;
          }
          const orchestratePayload = {
            message: text,
            action,
            skinId: resolvedSkinId,
            skin: resolvedSkin,
            sessionId: continueActiveDebate ? chatAgentSession.value.sessionId : null,
            targetAgent: null,
            budget: requestOptions.budget ?? (chatBudget.value ? Number(chatBudget.value) : null),
            horizonDays: 7,
            riskLevel: requestOptions.riskLevel || chatRiskLevel.value,
            locale: currentLang.value,
            history: historyPayload,
          };
          let response = null;
          const canStreamDebate = chatMode.value === 'debate'
            && ['debate', 'auto'].includes(action)
            && typeof client.debateStream === 'function';
          if (canStreamDebate) {
            // 辩论走 SSE：每个 Agent 完成就实时填入时间线
            const english = currentLang.value === 'en-US';
            const stageText = {
              bull: english ? '🤖 Bull and Bear are analyzing independently…' : '🤖 Bull 与 Bear 正在独立分析…',
              bear: english ? '🐻 Bear is rebutting Bull…' : '🐻 Bear 正在反驳 Bull…',
              judge: english ? '⚖️ Judge is weighing both sides…' : '⚖️ Judge 正在权衡双方观点…',
            };
            let streamError = null;
            const ensureLiveRound = (roundNo) => {
              if (!assistantMsg.payload?.debateRound) {
                assistantMsg.payload = {
                  type: 'debate',
                  debateRound: { roundNo: roundNo || 1, userMessage: null, bull: null, bear: null, judge: null },
                };
              }
              // 必须经由响应式代理读回，直接改原始对象不会刷新界面
              return assistantMsg.payload.debateRound;
            };
            await client.debateStream(orchestratePayload, (evt) => {
              if (evt.event === 'stage') {
                chatStreaming.value = true;
                assistantMsg.debateStage = evt.agent || 'bull';
                ensureLiveRound(evt.roundNo);
                assistantMsg.content = stageText[evt.agent] || stageText.bull;
                scrollChatBottom();
              } else if (evt.event === 'agent') {
                chatStreaming.value = true;
                assistantMsg.debateStage = evt.agent === 'bull'
                  ? 'bear'
                  : (evt.agent === 'bear' ? 'judge' : null);
                const liveRound = ensureLiveRound(evt.roundNo);
                if (evt.roundNo) liveRound.roundNo = evt.roundNo;
                liveRound[evt.agent] = evt.opinion;
                if (evt.agent !== 'judge') {
                  assistantMsg.content = english
                    ? `${evt.agent === 'bull' ? '🐂 Bull' : '🐻 Bear'} has presented its case…`
                    : `${evt.agent === 'bull' ? '🐂 Bull' : '🐻 Bear'} 已给出观点…`;
                }
                scrollChatBottom();
              } else if (evt.event === 'done') {
                response = evt.payload;
              } else if (evt.event === 'error') {
                streamError = evt.message || 'debate stream failed';
              }
            });
            if (streamError) throw new Error(streamError);
            if (!response) throw new Error('Debate stream returned no result');
          } else {
            response = await client.orchestrateAI(orchestratePayload);
          }
          if (!response?.message || !String(response.message).trim()) {
            throw new Error('Live AI response was empty');
          }
          assistantMsg.content = response.message;
          assistantMsg.kind = response?.type || 'chat';
          assistantMsg.payload = response || null;
          assistantMsg.debateStage = null;
          assistantMsg.model = responseModelLabel(response);
          // 辩论降级/错误反馈
          if (response?.error) {
            assistantMsg.content = response.error;
            assistantMsg.kind = 'error';
            assistantMsg.model = '⚠ Debate Error';
          } else if (response?.runtime?.agents?.mode === 'degraded') {
            assistantMsg.kind = 'degraded';
          }
          saveChatLocale(assistantMsg, currentLang.value);
          if (response?.agentSession) {
            chatAgentSession.value = response.agentSession;
            if (response.agentSession.userProfile) {
              chatBudget.value = response.agentSession.userProfile.budget;
              chatRiskLevel.value = response.agentSession.userProfile.risk_level || chatRiskLevel.value;
            }
          }
          // 结构化 Agent 辩论走 payload.debateRound + debate-timeline；
          // 仅当 rounds 是「字符串 bull/bear」的旧卡片格式时才设 type=debate。
          const rounds = response?.rounds;
          const hasStringRounds = Array.isArray(rounds)
            && rounds.length > 0
            && typeof rounds[0]?.bull === 'string';
          if (hasStringRounds && !response?.debateRound) {
            const skinLabel = typeof response.skin === 'string'
              ? response.skin
              : (response.skin?.name || response.skinName || '');
            assistantMsg.type = 'debate';
            assistantMsg.debate = {
              skin: skinLabel,
              currentPrice: response.currentPrice,
              rounds: rounds.map((r) => ({
                ...r,
                bull: typeof r?.bull === 'string' ? r.bull : (r?.bull?.summary || r?.bull?.position || ''),
                bear: typeof r?.bear === 'string' ? r.bear : (r?.bear?.summary || r?.bear?.position || ''),
              })),
              consensus: response.consensus,
            };
          } else if (response?.debateRound || response?.agentSession) {
            // structured payload path; do NOT auto-switch chat mode —
            // modes only change via the explicit toggle buttons
            assistantMsg.type = undefined;
            assistantMsg.debate = undefined;
          }
        } else if (client && apiOnline.value) {
          await client.chat(text, null, (chunk) => {
            chatStreaming.value = true;
            assistantMsg.content += chunk;
            scrollChatBottom();
          }, currentLang.value, historyPayload);
          if (!assistantMsg.content.trim()) {
            // 绝不使用本地预设文案兜底——那会产生幻觉式回答
            throw new Error('Live AI returned empty content');
          }
          assistantMsg.model = 'DeepSeek-V3';
        } else {
          throw new Error('Live AI client is unavailable');
        }
      } catch (e) {
        assistantMsg.content = currentLang.value === 'en-US'
          ? 'AI analysis is unavailable because the live backend did not return a valid result. No simulated recommendation was generated.'
          : 'AI 分析暂不可用：实时后端未返回有效结果，系统没有生成模拟推荐。';
        assistantMsg.kind = 'error';
        assistantMsg.model = 'Live service unavailable';
      }
      chatLoading.value = false;
      chatStreaming.value = false;
      await scrollChatBottom();
    };

    // Existing AI output is generated text, not an i18n key.  On a language
    // switch, translate it through the live backend once and retain a locale
    // cache so toggling back never changes the previous answer.
    watch(currentLang, async (locale) => {
      const requestId = ++chatLocaleRequest;
      const client = api();
      if (!client || typeof client.translateAIContent !== 'function') return;
      for (const message of chatMessages.value) {
        if (requestId !== chatLocaleRequest) return;
        if (message.role !== 'assistant' || message.content === '__WELCOME__' || message.kind === 'error') continue;
        const cached = message.localeVersions?.[locale];
        if (cached) {
          applyChatLocale(message, cached);
          continue;
        }
        const source = Object.values(message.localeVersions || {})[0] || chatLocaleSnapshot(message);
        try {
          const response = await client.translateAIContent(source, locale);
          if (requestId !== chatLocaleRequest) return;
          const translated = response?.content;
          if (!translated || typeof translated.content !== 'string') continue;
          message.localeVersions = message.localeVersions || {};
          message.localeVersions[locale] = translated;
          applyChatLocale(message, translated);
        } catch (error) {
          // Keep the original verified answer if DeepSeek cannot translate it.
          console.warn('[chat] history translation unavailable:', error?.message || error);
        }
      }
      await scrollChatBottom();
    });

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
    const alertSkinLocked = ref(false);
    const newAlert = ref({ skinId: '', type: 'above', targetPrice: null, note: '' });

    const alertLockedSkinName = computed(() => {
      const skin = skins.value.find(s => s.id === newAlert.value.skinId);
      return skin ? skinDisplayName(skin) : (selectedSkin.value ? skinDisplayName(selectedSkin.value) : '');
    });

    const openAlertModal = (skinId = null) => {
      const lockedId = skinId || '';
      newAlert.value = {
        skinId: lockedId,
        type: 'above',
        targetPrice: null,
        note: '',
      };
      alertSkinLocked.value = Boolean(lockedId);
      showAlertModal.value = true;
    };

    const closeAlertModal = () => {
      showAlertModal.value = false;
      alertSkinLocked.value = false;
      newAlert.value = { skinId: '', type: 'above', targetPrice: null, note: '' };
    };

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
        showToast({ title: t('alerts.createFailed'), subtitle: e.message || '', type: 'error' });
      }
      closeAlertModal();
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
        showToast({ title: t('alerts.deleteFailed'), subtitle: e.message || '', type: 'error' });
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
    const inventoryMenuPos = ref({ top: 0, left: 0 });
    const inventoryMenuItem = computed(() => {
      const id = inventoryMenuId.value;
      if (id == null) return null;
      return (myInventory.value || []).find((item) => item.id === id) || null;
    });
    const inventoryMenuStyle = computed(() => ({
      top: `${inventoryMenuPos.value.top}px`,
      left: `${inventoryMenuPos.value.left}px`,
    }));

    const placeInventoryMenu = (anchorEl) => {
      if (!anchorEl || typeof anchorEl.getBoundingClientRect !== 'function') return;
      const rect = anchorEl.getBoundingClientRect();
      const menuWidth = 168;
      const gap = 6;
      const left = Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8));
      const below = rect.bottom + gap;
      const estimatedHeight = 140;
      const top = (below + estimatedHeight > window.innerHeight - 8)
        ? Math.max(8, rect.top - estimatedHeight - gap)
        : below;
      inventoryMenuPos.value = { top, left };
    };
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

    // Steam 真图 URL(无尺寸后缀);前端拼 /360fx360f 显示。无图返回 null,模板回落 emoji。
    const getSkinImageUrl = (skinId) => getSkinMeta(skinId)?.imageUrl || null;

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
      const buy = Number(item.buyPrice);
      if (!Number.isFinite(buy)) return 0;
      const current = getCurrentPrice(item.skinId);
      return (current - buy) * (item.quantity || 1);
    };

    const getItemPnlPct = (item) => {
      if (item.pnlPct != null) return item.pnlPct;
      const buy = Number(item.buyPrice);
      if (!Number.isFinite(buy) || buy === 0) return 0;
      return ((getCurrentPrice(item.skinId) - buy) / buy) * 100;
    };

    const portfolioMetrics = computed(() => {
      const prices = {};
      portfolio.value.forEach(p => prices[p.skinId] = getCurrentPrice(p.skinId));
      if (!portfolio.value.length) {
        return {
          totalCost: 0,
          totalValue: 0,
          pnl: 0,
          pnlPct: 0,
          sharpeRatio: '—',
          maxDrawdown: '—',
          volatility: '—',
        };
      }
      if (window.CSVestData?.calculateRiskMetrics) {
        const m = window.CSVestData.calculateRiskMetrics(portfolio.value, prices);
        return {
          totalCost: Number(m.totalCost) || 0,
          totalValue: Number(m.totalValue) || 0,
          pnl: Number(m.pnl) || 0,
          pnlPct: Number(m.pnlPct) || 0,
          sharpeRatio: m.sharpeRatio ?? '—',
          maxDrawdown: m.maxDrawdown ?? '—',
          volatility: m.volatility ?? '—',
        };
      }
      const totalCost = portfolio.value.reduce((s, p) => s + (Number(p.buyPrice) || 0) * (p.quantity || 1), 0);
      const totalValue = portfolio.value.reduce((s, p) => s + getCurrentPrice(p.skinId) * (p.quantity || 1), 0);
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
      const predicted7Dates = hist.predicted7Dates || predictedDates;
      const predicted7Values = hist.predicted7Values || predictedValues;
      const trend30Dates = hist.trend30Dates || [];
      const trend30Values = hist.trend30Values || [];
      const hasData = dates.length > 0 && values.length > 0;

      if (!hasData) {
        inventoryValueChartInstance.clear();
        inventoryValueChartInstance.setOption({
          backgroundColor: 'transparent',
          title: {
            text: t('inventory.valueTrendEmpty'),
            left: 'center',
            top: 'middle',
            textStyle: { color: '#6b7280', fontSize: 13, fontWeight: 400 },
          },
          xAxis: { show: false },
          yAxis: { show: false },
          series: [],
        }, true);
        allowPageScrollOverChart(inventoryValueChartInstance);
        return;
      }

      const forecastAnchor = Number(hist.forecastAnchorTotal ?? values[values.length - 1]);
      const futureDates = (trend30Dates.length ? trend30Dates : predicted7Dates).slice();
      const exactHorizon = predicted7Values.length || 7;
      const nHist = dates.length;

      // Forward-fill non-positive holes so history never spikes to $0 on sparse days.
      let lastGood = null;
      const cleanValues = values.map((raw) => {
        const v = Number(raw);
        if (Number.isFinite(v) && v > 0) {
          lastGood = v;
          return v;
        }
        return lastGood;
      });

      const padNull = (count) => new Array(Math.max(count, 0)).fill(null);
      const exactSeries = padNull(Math.max(nHist - 1, 0))
        .concat(
          Number.isFinite(forecastAnchor) && forecastAnchor > 0 ? forecastAnchor : cleanValues[nHist - 1],
          predicted7Values.map(Number).filter((v) => Number.isFinite(v)),
        );
      while (exactSeries.length < nHist + futureDates.length) exactSeries.push(null);
      exactSeries.length = nHist + futureDates.length;

      // Align 30d trend to detail-page contract: overlap on forecast day 7, then continue.
      const trendTail = (() => {
        if (trend30Values.length >= exactHorizon) {
          return trend30Values.slice(exactHorizon - 1).map(Number);
        }
        const bridge = predicted7Values.length
          ? Number(predicted7Values[predicted7Values.length - 1])
          : forecastAnchor;
        return [bridge, ...trend30Values.slice(exactHorizon).map(Number)].filter((v) => Number.isFinite(v));
      })();
      const trendSeries = padNull(nHist + Math.max(exactHorizon - 1, 0)).concat(trendTail);
      while (trendSeries.length < nHist + futureDates.length) trendSeries.push(null);
      trendSeries.length = nHist + futureDates.length;

      const histSeries = cleanValues.concat(padNull(futureDates.length));
      const categoryDates = dates.concat(futureDates);
      const forecast7Name = t('inventory.forecast7d');
      const trend30Name = t('inventory.trend30d');
      const histName = t('inventory.valueTrend');
      const isLight = document.documentElement.getAttribute('data-theme') === 'light';
      const axisMuted = isLight ? 'rgba(58, 48, 40, 0.72)' : 'rgba(156, 163, 175, 0.42)';
      const splitMuted = isLight ? 'rgba(90, 78, 66, 0.14)' : 'rgba(148, 163, 184, 0.08)';
      const tipBg = isLight ? 'rgba(255, 252, 247, 0.96)' : 'rgba(17, 22, 31, 0.92)';
      const tipBorder = isLight ? 'rgba(40, 32, 24, 0.14)' : 'rgba(255, 255, 255, 0.08)';
      const tipText = isLight ? '#2a241c' : '#e5e7eb';

      inventoryValueChartInstance.setOption({
        backgroundColor: 'transparent',
        animation: true,
        animationDuration: 420,
        title: { show: false },
        legend: {
          data: [histName, forecast7Name, trend30Name],
          icon: 'roundRect',
          itemWidth: 12,
          itemHeight: 6,
          textStyle: { color: axisMuted, fontSize: 11, fontWeight: 500 },
          top: 0,
        },
        grid: { left: 52, right: 16, top: 40, bottom: 32 },
        tooltip: {
          trigger: 'axis',
          backgroundColor: tipBg,
          borderColor: tipBorder,
          borderWidth: 1,
          textStyle: { color: tipText, fontSize: 12 },
          extraCssText: 'backdrop-filter:blur(10px);border-radius:10px;',
          valueFormatter: (v) => (v == null || v === '-' ? '-' : `$${Number(v).toFixed(2)}`),
        },
        xAxis: {
          type: 'category',
          data: categoryDates,
          boundaryGap: false,
          axisTick: { show: false },
          axisLine: { show: false },
          axisLabel: { color: axisMuted, fontSize: 10, hideOverlap: true },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisTick: { show: false },
          axisLine: { show: false },
          axisLabel: { color: axisMuted, fontSize: 10, formatter: (v) => `$${v}` },
          splitLine: { lineStyle: { color: splitMuted, type: 'dashed' } },
        },
        series: [
          {
            name: histName,
            type: 'line',
            data: histSeries,
            smooth: 0.28,
            showSymbol: false,
            connectNulls: true,
            lineStyle: {
              width: 2.2,
              color: '#60a5fa',
              shadowBlur: 6,
              shadowColor: 'rgba(96, 165, 250, 0.28)',
            },
            areaStyle: {
              color: {
                type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(96, 165, 250, 0.22)' },
                  { offset: 1, color: 'rgba(96, 165, 250, 0)' },
                ],
              },
            },
            z: 2,
          },
          {
            name: forecast7Name,
            type: 'line',
            data: exactSeries,
            smooth: 0.35,
            showSymbol: false,
            connectNulls: false,
            lineStyle: {
              width: 2,
              color: '#ff6b00',
              type: [5, 5],
              shadowBlur: 8,
              shadowColor: 'rgba(255, 107, 0, 0.28)',
            },
            areaStyle: {
              color: {
                type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(255, 107, 0, 0.18)' },
                  { offset: 1, color: 'rgba(255, 107, 0, 0)' },
                ],
              },
            },
            markArea: {
              silent: true,
              itemStyle: {
                color: {
                  type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
                  colorStops: [
                    { offset: 0, color: 'rgba(255, 107, 0, 0.09)' },
                    { offset: 1, color: 'rgba(255, 140, 64, 0.04)' },
                  ],
                },
              },
              data: predicted7Values.length && dates.length ? [[
                { xAxis: dates[dates.length - 1] },
                { xAxis: futureDates[Math.min(exactHorizon - 1, futureDates.length - 1)] },
              ]] : [],
            },
            markLine: predicted7Values.length && dates.length ? {
              symbol: 'none',
              label: { show: true, formatter: t('common.forecast'), color: axisMuted, fontSize: 10 },
              lineStyle: { color: 'rgba(156, 163, 175, 0.45)', type: 'dashed', width: 1 },
              data: [{ xAxis: dates[dates.length - 1] }],
            } : undefined,
            z: 4,
          },
          {
            name: trend30Name,
            type: 'line',
            data: trendSeries,
            smooth: 0.4,
            showSymbol: false,
            connectNulls: false,
            lineStyle: {
              width: 2,
              color: '#22c55e',
              type: [5, 5],
              shadowBlur: 8,
              shadowColor: 'rgba(34, 197, 94, 0.22)',
            },
            areaStyle: {
              color: {
                type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(34, 197, 94, 0.14)' },
                  { offset: 1, color: 'rgba(34, 197, 94, 0)' },
                ],
              },
            },
            markArea: {
              silent: true,
              itemStyle: {
                color: {
                  type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
                  colorStops: [
                    { offset: 0, color: 'rgba(255, 140, 64, 0.05)' },
                    { offset: 0.35, color: 'rgba(34, 197, 94, 0.08)' },
                    { offset: 1, color: 'rgba(34, 197, 94, 0.04)' },
                  ],
                },
              },
              data: trendTail.length && futureDates.length ? [[
                { xAxis: futureDates[Math.min(exactHorizon - 1, futureDates.length - 1)] },
                { xAxis: futureDates[futureDates.length - 1] },
              ]] : [],
            },
            z: 4,
          },
        ],
      }, true);
      allowPageScrollOverChart(inventoryValueChartInstance);
    };

    const refreshInventoryCharts = async () => {
      if (!currentUser.value) return;
      const emptyHist = { dates: [], values: [], predictedDates: [], predictedValues: [], predicted7Dates: [], predicted7Values: [], trend30Dates: [], trend30Values: [], total: 0 };
      // 本地库存为空:直接空态,不走会伪造曲线的 mock
      if (!myInventory.value.length) {
        inventoryValueHistory.value = emptyHist;
        nextTick(() => {
          renderInventoryValueChart();
          inventoryValueChartInstance?.resize();
        });
        return;
      }
      const client = api();
      try {
        if (client) {
          const hist = await client.getInventoryValueHistory(90);
          inventoryValueHistory.value = hist || emptyHist;
        } else if (window.CSVestData.generateInventoryValueHistory) {
          inventoryValueHistory.value = window.CSVestData.generateInventoryValueHistory(myInventory.value, 90);
        } else {
          inventoryValueHistory.value = emptyHist;
        }
      } catch (_) {
        if (window.CSVestData.generateInventoryValueHistory) {
          inventoryValueHistory.value = window.CSVestData.generateInventoryValueHistory(myInventory.value, 90);
        } else {
          inventoryValueHistory.value = emptyHist;
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

    const toggleInventoryMenu = (id, event) => {
      if (inventoryMenuId.value === id) {
        inventoryMenuId.value = null;
        return;
      }
      const anchor = event?.currentTarget || event?.target;
      placeInventoryMenu(anchor);
      inventoryMenuId.value = id;
      nextTick(() => placeInventoryMenu(anchor));
    };

    const closeInventoryMenu = () => {
      inventoryMenuId.value = null;
    };

    const onInventoryMenuOutside = (event) => {
      if (inventoryMenuId.value == null) return;
      const target = event?.target;
      if (target?.closest?.('.inv-item__menu') || target?.closest?.('.inv-item__more-btn')) return;
      inventoryMenuId.value = null;
    };

    const onInventoryMenuRepositionClose = () => {
      if (inventoryMenuId.value != null) inventoryMenuId.value = null;
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
          await loadPortfolioExtras();
        } else {
          portfolio.value.push({
            id: Date.now(),
            ...payload,
            name: skin?.name || '',
            holdingType: 'sim',
          });
        }
        showToast({ title: t('portfolio.addHolding'), subtitle: skin?.name || '', type: 'success' });
      } catch (e) {
        showToast({ title: t('portfolio.addFailed'), subtitle: e.message || '', type: 'error' });
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
        await loadPortfolioExtras();
      } catch (e) {
        showToast({ title: t('portfolio.closeFailed'), subtitle: e.message || '', type: 'error' });
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
      if (!steamImportForm.value.cookie.trim()) {
        showToast({ title: t('inventory.steamModal.failed'), subtitle: t('inventory.steamModal.cookieRequired'), type: 'error' });
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

    const diagnoseActionLabel = (action) => {
      const a = String(action || '');
      if (/卖出|sell/i.test(a)) return t('portfolio.diag.action.sell');
      if (/加仓|add/i.test(a)) return t('portfolio.diag.action.add');
      if (/减仓|trim|观察/i.test(a)) return t('portfolio.diag.action.trim');
      if (/持有|hold/i.test(a)) return t('portfolio.diag.action.hold');
      return a || '—';
    };

    const diagnoseActionClass = (action) => {
      const a = String(action || '');
      if (/卖出|sell/i.test(a)) return 'is-sell';
      if (/加仓|add/i.test(a)) return 'is-add';
      if (/减仓|trim|观察/i.test(a)) return 'is-trim';
      return 'is-hold';
    };

    const formatDiagnoseTime = (iso) => {
      if (!iso) return '';
      try {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return String(iso).slice(0, 19);
        return d.toLocaleString(currentLang.value === 'zh-CN' ? 'zh-CN' : 'en-US', {
          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        });
      } catch (_) {
        return String(iso).slice(0, 19);
      }
    };

    const applyPortfolioDiagnose = (diag) => {
      if (!diag) {
        portfolioDiagnose.value = null;
        portfolioDiagnoseLocaleVersions.value = {};
        return;
      }
      if (diag.error || diag.empty) {
        portfolioDiagnose.value = {
          empty: true,
          summary: diag.error || diag.summary || t('portfolio.emptyDiagnose'),
          aiSummary: diag.aiSummary || diag.error || diag.summary || t('portfolio.emptyDiagnose'),
        };
        portfolioDiagnoseLocaleVersions.value = {};
        return;
      }
      const summary = diag.aiSummary || diag.summary || '';
      portfolioDiagnose.value = { ...diag, aiSummary: summary };
      portfolioDiagnoseLocaleVersions.value = summary
        ? { [diag.locale || dailySummaryLocale(summary)]: summary }
        : {};
    };

    const localizePortfolioDiagnose = async (locale) => {
      const diag = portfolioDiagnose.value;
      if (!diag || diag.empty) return;
      const summary = diag.aiSummary || diag.summary;
      if (!summary) return;
      const cached = portfolioDiagnoseLocaleVersions.value?.[locale];
      if (cached) {
        portfolioDiagnose.value = { ...diag, aiSummary: cached };
        return;
      }
      // Already in target language — cache and skip translate
      if (dailySummaryLocale(summary) === locale) {
        portfolioDiagnoseLocaleVersions.value = {
          ...portfolioDiagnoseLocaleVersions.value,
          [locale]: summary,
        };
        return;
      }
      const client = api();
      if (!client || typeof client.translateAIContent !== 'function') return;
      const source = Object.values(portfolioDiagnoseLocaleVersions.value || {})[0] || summary;
      const requestId = ++portfolioDiagnoseTranslationRequest;
      try {
        const response = await client.translateAIContent(source, locale);
        if (requestId !== portfolioDiagnoseTranslationRequest) return;
        const translated = response?.content;
        if (typeof translated !== 'string' || !translated.trim()) return;
        portfolioDiagnoseLocaleVersions.value = {
          ...portfolioDiagnoseLocaleVersions.value,
          [locale]: translated,
        };
        portfolioDiagnose.value = { ...portfolioDiagnose.value, aiSummary: translated };
      } catch (error) {
        console.warn('[portfolio-diagnose] translation unavailable:', error?.message || error);
      }
    };

    const loadPortfolioExtras = async () => {
      const client = api();
      if (!client || !apiOnline.value) return;
      const emptyHist = { dates: [], values: [], predictedDates: [], predictedValues: [], total: 0 };
      // 无模拟持仓:不请求诊断/走势,避免空仓报错或混入真实库存曲线
      if (!portfolio.value.length) {
        portfolioValueHistory.value = emptyHist;
        applyPortfolioDiagnose({
          empty: true,
          summary: t('portfolio.emptyDiagnose'),
        });
        return;
      }
      portfolioDiagnoseLoading.value = true;
      try {
        const [hist, diag] = await Promise.all([
          client.getPortfolioValueHistory(90),
          client.diagnosePortfolio(currentLang.value),
        ]);
        portfolioValueHistory.value = hist || emptyHist;
        applyPortfolioDiagnose(diag || { empty: true });
        if (diag && !diag.empty && !diag.error) {
          await localizePortfolioDiagnose(currentLang.value);
        }
      } catch (e) {
        console.warn('[CSVest] portfolio extras failed', e);
        showToast({
          title: t('portfolio.diagnoseFailed'),
          subtitle: e?.message || '',
          type: 'error',
        });
      } finally {
        portfolioDiagnoseLoading.value = false;
      }
    };

    watch(currentLang, async (locale) => {
      if (currentPage.value !== 'portfolio') return;
      if (portfolioDiagnose.value?.empty) {
        applyPortfolioDiagnose({
          empty: true,
          summary: t('portfolio.emptyDiagnose'),
        });
        return;
      }
      await localizePortfolioDiagnose(locale);
    });

    // ============ 模型实验室图表 ============
    const radarChart = ref(null);
    const backtestChart = ref(null);
    const shapChart = ref(null);
    const perDayChart = ref(null);
    // v5 契约: LSTM 系列 Seq2Seq Dense(7) 的逐日(D1-D7)误差指标
    const modelsPerDay = ref([]);
    const perDayMetric = ref('rmse'); // rmse | mae | mape
    let radarInstance = null, backtestInstance = null, shapInstance = null, perDayInstance = null;

    const modelsChartTheme = () => {
      const narrow = typeof window !== 'undefined' && window.innerWidth <= 768;
      return {
        narrow,
        tooltipBg: 'rgba(16, 16, 24, 0.96)',
        tooltipBorder: 'rgba(255, 140, 64, 0.35)',
        axis: '#9a96a0',
        split: 'rgba(154, 150, 160, 0.22)',
        axisLine: '#6d6875',
        palette: ['#e07a28', '#1fa89a', '#4f7cff', '#d63b6a', '#c9a227', '#8b9cb3', '#5a8f7b'],
        tooltipBase: {
          backgroundColor: 'rgba(14, 16, 20, 0.96)',
          borderColor: 'rgba(255, 255, 255, 0.14)',
          borderWidth: 1,
          textStyle: { color: '#f2efe9', fontSize: 12 },
          extraCssText: 'border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,0.35);',
        },
      };
    };

    const renderRadar = () => {
      if (!radarChart.value) return;
      radarInstance = getOrCreateChart(radarInstance, radarChart.value);
      const th = modelsChartTheme();
      const narrow = th.narrow;

      // 从当前 ML 输出动态计算雷达分数，避免硬编码指标与重训结果脱节
      const rows = regressionModels.value.slice(0, 4);
      if (!rows.length) {
        radarInstance.clear();
        return;
      }
      const maxRmse = Math.max(...rows.map(r => Number(r.rmse) || 0), 1);
      const posReturns = rows.map((r) => Math.max(0, Number(r.returnPct) || 0));
      const maxPosReturn = Math.max(...posReturns, 1);
      const speedScore = (speed) => {
        const s = String(speed || '').toLowerCase();
        if (/极快|very fast/.test(s)) return 100;
        if (/快|fast/.test(s)) return 82;
        if (/中|medium/.test(s)) return 58;
        return 30;
      };
      // 高对比四色 + 线型区分：铜橙实线 / 青绿虚线 / 靛蓝点线 / 玫红短划
      const colors = [
        { stroke: '#e07a28', fill: 'rgba(224, 122, 40, 0.18)', dash: null },
        { stroke: '#12b5a0', fill: 'rgba(18, 181, 160, 0.14)', dash: [6, 4] },
        { stroke: '#4f7cff', fill: 'rgba(79, 124, 255, 0.12)', dash: [2, 4] },
        { stroke: '#e23d6e', fill: 'rgba(226, 61, 110, 0.12)', dash: [8, 3, 2, 3] },
      ];
      const selected = selectedRadarModel.value;
      const radarData = rows.map((r, i) => {
        const rmseScore = Math.max(0, 100 - ((Number(r.rmse) || maxRmse) / maxRmse) * 55);
        const r2Score = Math.max(0, Math.min(100, (Number(r.r2) || 0) * 100));
        const explainScore = Math.max(20, Math.min(100, ((Number(r.interpretability) || 1) / 3) * 100));
        const ret = Number(r.returnPct) || 0;
        const returnScore = Math.max(0, Math.min(100, (Math.max(0, ret) / maxPosReturn) * 100));
        const generalize = (rmseScore + r2Score) / 2;
        const isSel = !selected || selected === r.name;
        const c = colors[i % colors.length];
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
          _raw: r,
          areaStyle: { color: isSel ? c.fill : 'rgba(148,163,184,0.02)' },
          lineStyle: {
            color: c.stroke,
            width: isSel ? 3.2 : 2,
            opacity: isSel ? 1 : 0.62,
            type: c.dash || 'solid',
          },
          itemStyle: {
            color: c.stroke,
            opacity: isSel ? 1 : 0.7,
            borderWidth: isSel ? 2 : 1,
            borderColor: '#fff',
          },
          z: isSel ? 3 : 1,
        };
      });

      radarInstance.setOption({
        backgroundColor: 'transparent',
        animationDuration: 420,
        color: colors.map((c) => c.stroke),
        tooltip: {
          ...th.tooltipBase,
          formatter: (params) => {
            const p = Array.isArray(params) ? params[0] : params;
            const row = radarData.find((d) => d.name === p.name);
            const raw = row?._raw;
            if (!raw) return p.name;
            return [
              `<strong>${raw.name}</strong>`,
              `RMSE ${raw.rmse?.toFixed?.(2) ?? '—'} · R² ${raw.r2?.toFixed?.(2) ?? '—'}`,
              `${t('models.col.returnPct')} ${formatModelReturn(raw.returnPct)}`,
              `MAPE ${raw.mape?.toFixed?.(2) ?? '—'}%`,
            ].join('<br/>');
          },
        },
        legend: {
          data: rows.map((r, i) => ({
            name: r.name,
            itemStyle: { color: colors[i % colors.length].stroke },
            lineStyle: { color: colors[i % colors.length].stroke, width: 2 },
          })),
          textStyle: { color: th.axis, fontSize: narrow ? 10 : 12, fontWeight: 500 },
          itemWidth: 14,
          itemHeight: 10,
          itemGap: narrow ? 10 : 16,
          top: narrow ? undefined : 4,
          bottom: narrow ? 0 : undefined,
          type: 'scroll',
          width: narrow ? '92%' : undefined,
          selectedMode: true,
        },
        radar: {
          indicator: [
            { name: narrow ? t('models.radar.rmseShort') : t('models.radar.rmse'), max: 100 },
            { name: t('models.radar.speed'), max: 100 },
            { name: t('models.radar.explain'), max: 100 },
            { name: t('models.radar.return'), max: 100 },
            { name: t('models.radar.r2'), max: 100 },
            { name: t('models.radar.generalize'), max: 100 },
          ],
          center: ['50%', narrow ? '48%' : '55%'],
          radius: narrow ? '48%' : '56%',
          axisName: { color: th.axis, fontSize: narrow ? 10 : 11 },
          splitLine: { lineStyle: { color: th.split } },
          splitArea: { areaStyle: { color: ['rgba(255,255,255,0.015)', 'rgba(255,255,255,0.04)'] } },
          axisLine: { lineStyle: { color: th.axisLine } },
        },
        series: [{ type: 'radar', data: radarData, symbol: 'circle', symbolSize: 7 }],
      }, true);
      allowPageScrollOverChart(radarInstance);
    };

    const renderBacktest = async () => {
      if (!backtestChart.value) return;
      backtestInstance = getOrCreateChart(backtestInstance, backtestChart.value);
      const th = modelsChartTheme();
      const narrow = th.narrow;

      let dates = [];
      let seriesMap = {};
      try {
        const client = api();
        if (client && apiOnline.value) {
          const bt = await client.getBacktest(90, modelTrack.value);
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
      backtestEmpty.value = !dates.length;

      const toWindowIndex = (arr) => {
        if (!Array.isArray(arr) || !arr.length) return arr || [];
        const base = arr.find((v) => v != null && Number(v) !== 0);
        const b = (base == null || Number(base) === 0) ? 1 : Number(base);
        return arr.map((v) => (v == null ? null : +((Number(v) / b) * 100).toFixed(2)));
      };

      const names = Object.keys(seriesMap);
      const strategyNames = names.filter((n) => !/buy|hold|持有/i.test(n));
      const benchNames = names.filter((n) => /buy|hold|持有/i.test(n));
      const hasBench = benchNames.length > 0;

      const preferOn = new Set(['Hybrid', 'LSTM-C', 'Buy&Hold', '买入持有']);
      const selected = Object.fromEntries(
        [...strategyNames, ...benchNames].map((n) => {
          if (!narrow) return [n, true];
          return [n, preferOn.has(n) || /hybrid|lstm-c|buy|hold|持有/i.test(n)];
        })
      );

      let colorIdx = 0;
      const series = [];
      for (const name of strategyNames) {
        const color = th.palette[colorIdx++ % th.palette.length];
        const isHero = /hybrid|lstm-c/i.test(name);
        series.push({
          name,
          type: 'line',
          yAxisIndex: 0,
          data: toWindowIndex(seriesMap[name]),
          smooth: 0.15,
          showSymbol: false,
          lineStyle: { color, width: isHero ? 2.8 : 1.8 },
          itemStyle: { color },
          areaStyle: isHero
            ? {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                  { offset: 0, color: color + '33' },
                  { offset: 1, color: color + '00' },
                ]),
              }
            : undefined,
          emphasis: { focus: 'series' },
        });
      }
      for (const name of benchNames) {
        series.push({
          name,
          type: 'line',
          yAxisIndex: hasBench ? 1 : 0,
          data: toWindowIndex(seriesMap[name]),
          smooth: false,
          showSymbol: false,
          lineStyle: { color: '#9ca3af', width: 2, type: 'dashed' },
          itemStyle: { color: '#9ca3af' },
          emphasis: { focus: 'series' },
        });
      }

      backtestInstance.setOption({
        backgroundColor: 'transparent',
        animationDuration: 480,
        tooltip: {
          ...th.tooltipBase,
          trigger: 'axis',
          axisPointer: { type: 'cross', crossStyle: { color: 'rgba(255,140,64,0.45)' } },
          order: 'valueDesc',
          valueFormatter: (v) => (v == null ? '-' : Number(v).toFixed(2)),
        },
        legend: {
          data: [...strategyNames, ...benchNames],
          textStyle: { color: th.axis, fontSize: 11 },
          top: 0,
          type: 'scroll',
          selected,
        },
        grid: { left: narrow ? 44 : 56, right: hasBench ? (narrow ? 44 : 56) : 24, top: 42, bottom: narrow ? 36 : 48 },
        dataZoom: narrow
          ? [{ type: 'inside', start: 0, end: 100 }]
          : [
              { type: 'inside', start: 0, end: 100 },
              { type: 'slider', height: 18, bottom: 8, borderColor: 'transparent', fillerColor: 'rgba(255,107,0,0.18)', handleSize: 14 },
            ],
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: false,
          axisLine: { lineStyle: { color: th.axisLine } },
          axisLabel: { color: th.axis, fontSize: 10, hideOverlap: true },
        },
        yAxis: hasBench
          ? [
              {
                type: 'value',
                name: t('models.backtestAxisModels'),
                scale: true,
                nameTextStyle: { color: '#6b7280', fontSize: 10 },
                axisLine: { lineStyle: { color: th.axisLine } },
                axisLabel: { color: th.axis, fontSize: 10 },
                splitLine: { lineStyle: { color: th.split, type: 'dashed' } },
              },
              {
                type: 'value',
                name: 'Buy&Hold',
                scale: true,
                nameTextStyle: { color: '#6b7280', fontSize: 10 },
                axisLine: { lineStyle: { color: th.axisLine } },
                axisLabel: { color: th.axis, fontSize: 10 },
                splitLine: { show: false },
              },
            ]
          : {
              type: 'value',
              name: t('models.backtestAxisModels'),
              scale: true,
              nameTextStyle: { color: '#6b7280', fontSize: 10 },
              axisLine: { lineStyle: { color: th.axisLine } },
              axisLabel: { color: th.axis, fontSize: 10 },
              splitLine: { lineStyle: { color: th.split, type: 'dashed' } },
            },
        series,
      }, true);
      allowPageScrollOverChart(backtestInstance);
    };

    const renderPerDay = () => {
      if (!perDayChart.value) return;
      if (!modelsPerDay.value.length) {
        try { perDayInstance?.dispose(); } catch (_) { /* ignore */ }
        perDayInstance = null;
        return;
      }
      perDayInstance = getOrCreateChart(perDayInstance, perDayChart.value);
      const th = modelsChartTheme();
      const metric = perDayMetric.value;
      const days = modelsPerDay.value[0].perDay.map((d) => `D${d.day}`);
      const series = modelsPerDay.value.map((m, i) => {
        const color = th.palette[i % th.palette.length];
        return {
          name: m.name,
          type: 'line',
          data: m.perDay.map((d) => (d[metric] != null ? +Number(d[metric]).toFixed(4) : null)),
          smooth: true,
          symbol: 'circle',
          symbolSize: 7,
          lineStyle: { color, width: 2.2 },
          itemStyle: { color },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: color + '30' },
              { offset: 1, color: color + '00' },
            ]),
          },
          emphasis: { focus: 'series' },
        };
      });
      perDayInstance.setOption({
        backgroundColor: 'transparent',
        animationDuration: 360,
        tooltip: {
          ...th.tooltipBase,
          trigger: 'axis',
          valueFormatter: (v) => {
            if (v == null) return '-';
            return metric === 'mape' ? `${Number(v).toFixed(2)}%` : Number(v).toFixed(3);
          },
        },
        legend: {
          data: modelsPerDay.value.map((m) => m.name),
          textStyle: { color: th.axis, fontSize: 11 },
          top: 0,
          type: 'scroll',
        },
        grid: { left: th.narrow ? 44 : 56, right: 20, top: 40, bottom: 28 },
        xAxis: {
          type: 'category',
          data: days,
          boundaryGap: false,
          axisLine: { lineStyle: { color: th.axisLine } },
          axisLabel: { color: th.axis, fontSize: 10 },
        },
        yAxis: {
          type: 'value',
          scale: true,
          name: metric === 'mape' ? 'MAPE %' : metric.toUpperCase(),
          nameTextStyle: { color: '#6b7280', fontSize: 10 },
          axisLine: { lineStyle: { color: th.axisLine } },
          axisLabel: { color: th.axis, fontSize: 10 },
          splitLine: { lineStyle: { color: th.split, type: 'dashed' } },
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
      const th = modelsChartTheme();
      const narrow = th.narrow;

      const normalizeShapRows = (raw) => {
        const list = Array.isArray(raw)
          ? raw
          : (raw?.feature_importance || raw?.features || []);
        return (Array.isArray(list) ? list : [])
          .map((d) => {
            const name = d.feature || d.name;
            const value = Number(
              d.meanAbsShap ?? d.mean_abs_shap ?? d.importance ?? d.value ?? NaN
            );
            return { name, value };
          })
          .filter((d) => d.name && Number.isFinite(d.value) && d.value >= 0);
      };

      let rows = [];
      let usedLive = false;
      try {
        const client = api();
        if (client && apiOnline.value) {
          const shap = await client.getShap(shapModel.value);
          rows = normalizeShapRows(shap);
          usedLive = rows.length > 0;
        }
      } catch (_) { /* mock */ }
      if (!rows.length) {
        rows = normalizeShapRows(window.CSVestData.SHAP_FEATURES || []);
      }
      // 真实 SHAP 量级跨度大：只展示 Top 12，避免尾部条几乎看不见
      rows = rows.slice().sort((a, b) => b.value - a.value).slice(0, 12);
      shapFeatureRows.value = rows;
      shapEmpty.value = !rows.length;
      const data = rows.slice().sort((a, b) => a.value - b.value);
      const maxV = Math.max(...data.map((d) => Number(d.value) || 0), 1e-9);
      const fmtShap = (v) => {
        const n = Number(v) || 0;
        if (n >= 0.1) return n.toFixed(3);
        if (n >= 0.01) return n.toFixed(4);
        if (n >= 0.001) return n.toFixed(4);
        if (n > 0) return n.toExponential(2);
        return '0';
      };

      shapInstance.setOption({
        backgroundColor: 'transparent',
        animationDuration: 420,
        tooltip: {
          ...th.tooltipBase,
          trigger: 'axis',
          axisPointer: { type: 'shadow' },
          formatter: (params) => {
            const p = Array.isArray(params) ? params[0] : params;
            const src = usedLive ? 'SHAP' : 'Demo';
            return `<strong>${p.name}</strong><br/>${t('models.shapUnit')}: ${fmtShap(p.value)}<br/><span style="opacity:.7">${src}</span>`;
          },
        },
        grid: {
          left: narrow ? 96 : 148,
          right: narrow ? 48 : 64,
          top: 16,
          bottom: 28,
          containLabel: false,
        },
        xAxis: {
          type: 'value',
          name: t('models.shapUnit'),
          nameLocation: 'end',
          nameTextStyle: { color: '#6b7280', fontSize: 10 },
          axisLine: { lineStyle: { color: th.axisLine } },
          axisLabel: {
            color: th.axis,
            fontSize: 10,
            formatter: (v) => fmtShap(v),
          },
          splitLine: { lineStyle: { color: th.split, type: 'dashed' } },
        },
        yAxis: {
          type: 'category',
          data: data.map((d) => d.name),
          axisLine: { lineStyle: { color: th.axisLine } },
          axisLabel: {
            color: th.axis,
            fontSize: narrow ? 10 : 11,
            width: narrow ? 84 : 130,
            overflow: 'truncate',
            ellipsis: '…',
          },
        },
        series: [{
          type: 'bar',
          data: data.map((d) => {
            const ratio = Math.max(0.22, Math.sqrt((Number(d.value) || 0) / maxV));
            return {
              value: d.value,
              itemStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                  { offset: 0, color: `rgba(201, 106, 43, ${0.35 + ratio * 0.25})` },
                  { offset: 1, color: `rgba(219, 138, 74, ${0.55 + ratio * 0.45})` },
                ]),
                borderRadius: [0, 5, 5, 0],
              },
            };
          }),
          barWidth: narrow ? 14 : 18,
          label: {
            show: true,
            position: 'right',
            color: '#9ca3af',
            fontSize: 10,
            formatter: (p) => fmtShap(p.value),
          },
        }],
      }, true);
      allowPageScrollOverChart(shapInstance);
    };

    watch(currentLang, async () => {
      await nextTick();
      try {
        if (currentPage.value === 'models') {
          renderRadar();
          renderBacktest();
          renderPerDay();
          renderShap();
        }
        if (currentPage.value === 'portfolio') {
          renderInventoryValueChart();
        }
        if (currentPage.value === 'prediction') {
          renderKline();
        }
      } catch (_) { /* ignore */ }
    });

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
        .filter(s => match(skinSearchText(s)) || match(s.category) || match(categoryLabel(s.category)))
        .slice(0, 8)
        .map(s => ({
          id: `skin-${s.id}`,
          icon: s.image,
          title: skinDisplayName(s),
          subtitle: `${categoryLabel(s.category)} · $${formatPrice(s.price)} · 7d ${s.change7d >= 0 ? '+' : ''}${s.change7d.toFixed(2)}%`,
          kbd: '',
          action: () => { viewSkin(s.id); },
        }));
      if (skinCmds.length) groups.push({ title: t('cmd.group.skins'), items: skinCmds });

      // 操作命令
      const actionCmds = [
        { id: 'act-theme', icon: theme.value === 'dark' ? 'sun' : 'moon', iconStyle: 'duotone', title: t('cmd.action.theme'), subtitle: `${t('cmd.action.themeCurrent')}: ${theme.value === 'dark' ? t('theme.dark') : t('theme.light')}`, kbd: 'Ctrl+Shift+L', action: toggleTheme },
        { id: 'act-help', icon: 'keyboard', iconStyle: 'duotone', title: t('cmd.action.help'), subtitle: t('shortcut.title'), kbd: '?', action: () => { showShortcutHelp.value = true; } },
        { id: 'act-refresh', icon: 'arrows-clockwise', iconStyle: 'duotone', title: t('cmd.action.refresh'), subtitle: '', kbd: '', action: refreshData },
        { id: 'act-alert', icon: 'bell-ringing', iconStyle: 'duotone', title: t('cmd.action.alert'), subtitle: t('menu.alerts'), kbd: '', action: () => { currentPage.value = 'alerts'; setTimeout(() => openAlertModal(), 100); } },
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
        if (showAlertModal.value) { closeAlertModal(); return; }
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

    // 按当前页拉取专属数据。注意：watch(currentPage) 不会在「刷新后停在原页」时触发，
    // 必须在 onMounted / 进入系统后再主动 hydrate，否则日报/持仓等会一直停在初始 Mock。
    const hydrateCurrentPage = async (pageId) => {
      const page = pageId || currentPage.value;
      await nextTick();
      window.processPhIcons && window.processPhIcons();

      if (page === 'prediction') {
        renderKline();
        if (selectedSkin.value?.id) {
          loadPlatformQuotes(selectedSkin.value.id, { live: true });
          loadPredictions(selectedSkin.value.id);
          loadExplanation(selectedSkin.value.id);
        }
      } else if (page === 'models') {
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
      } else if (page === 'daily') {
        await Promise.all([loadDailyReport(), loadNewsFromApi()]);
      } else if (page === 'admin') {
        if (adminIsAuthed.value) await loadAdminPanel();
      } else if (page === 'alerts') {
        await loadAlertsFromApi();
      } else if (page === 'portfolio') {
        if (portfolioTab.value === 'inventory') {
          if (currentUser.value) {
            await loadInventoryFromApi();
            await refreshInventoryCharts();
          }
        } else {
          await loadPortfolioFromApi();
          await loadPortfolioExtras();
        }
      } else if (page === 'chat') {
        setTimeout(scrollChatBottom, 100);
      } else if (page === 'dashboard') {
        // 行情页：确保列表已是后端数据；connect 失败时允许再试一次
        if (!apiOnline.value) {
          await reconnectBackend();
        } else if (!skins.value?.length || skins.value.length < 50) {
          try { await loadSkinsFromApi(); } catch (_) { /* ignore */ }
        }
      }
    };

    // ============ 生命周期 ============
    onMounted(async () => {
      await nextTick();
      document.addEventListener('pointerdown', onInventoryMenuOutside, true);
      window.addEventListener('resize', onInventoryMenuRepositionClose);
      document.addEventListener('scroll', onInventoryMenuRepositionClose, true);
      // 移除首次加载遮罩 (CSS 动画 0.6s 后自动隐藏,这里做兜底)
      setTimeout(() => {
        const loader = document.getElementById('app-loader');
        if (loader) loader.classList.add('hidden');
      }, 300);

      // 自动探测后端；通了就切真实 API
      await connectBackend();

      // 用 /api/me 恢复 JWT 会话（库存/持仓按用户隔离）
      if (apiOnline.value && Auth?.restoreFromApi) {
        try {
          const restored = await Auth.restoreFromApi();
          currentUser.value = restored || Auth.getCurrentUser?.() || null;
          if (currentUser.value) {
            isGuest.value = false;
            sessionStorage.removeItem('sv_guest');
            await refreshPersonalDataAfterAuth();
          }
        } catch (_) { /* ignore */ }
      } else if (Auth?.getCurrentUser) {
        currentUser.value = Auth.getCurrentUser() || null;
      }

      // 首屏展示时图表容器尚未挂载,进入系统后再渲染
      if (showLanding.value) {
        await mountLandingCanvas();
      } else {
        renderKline();
        await hydrateCurrentPage(currentPage.value);
      }
      window.addEventListener('keydown', handleGlobalKeydown);
      window.addEventListener('resize', () => {
        klineChartInstance?.resize();
        radarInstance?.resize();
        backtestInstance?.resize();
        shapInstance?.resize();
        perDayInstance?.resize();
        inventoryValueChartInstance?.resize();
        updateNavPill();
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

      await nextTick();
      updateNavPill();
      // 不再弹欢迎 Toast (用户反馈: 弹窗太多令人困惑)
    });

    onUnmounted(() => {
      document.removeEventListener('pointerdown', onInventoryMenuOutside, true);
      window.removeEventListener('resize', onInventoryMenuRepositionClose);
      document.removeEventListener('scroll', onInventoryMenuRepositionClose, true);
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

      await hydrateCurrentPage(newPage);
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
      predictionStatus.value = 'idle';
      predictionReason.value = '';
      predictionCalibration.value = null;
      predictionDaily.value = null;
      predictionTrend30d.value = null;
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
      navMenuRef, navPillStyle, navPillReady,
      // 首屏
      showLanding, landingExiting, landingHeroIndex, landingHeroSlides, landingSceneHint,
      enterSystem, showAdmin, leaveAdmin,
      // 用户认证
      currentUser, isGuest, showAuthPanel, authMode, authForm, authError, authSubmitting,
      submitLogin, submitRegister, enterAsGuest, logoutUser,
      openAuthPanel, closeAuthPanel, returnToLandingForLogin,
      userMenuOpen, userAvatarChar,
      showProfileModal, profileNameDraft, openProfileEditor, saveProfile,
      // 行情
      skins, topGainers, topLosers, hotVolume, refreshData,
      filterCategory, categoryKeys, categoryMap, categoryLabel, skinDisplayName, wearLabel,
      rarityLabel, rarityColor, rarityStyle, filteredSkins,
      visibleSkins, hasMoreSkins, remainingSkins, showMoreSkins,
      marketLiveQuotes, marketLiveLoading, refreshMarketLive,
      skinSearch, skinSort, skinSortDir, marketPulse, marketSentiment, formatChange, formatVolume,
      rowSparkPath,
      apiOnline, connectBackend, reconnectBackend, dataSourceLabel,
      // 预测
      selectedSkin, viewSkin, klineChart, klineLoading, timeframe, renderKline,
      modelPredictions, predictionStatus, predictionReason, predictionCalibration, calibrationEvidence,
      predictionMeta, predictionDaily, predictionTrend30d, predictionDailyRows,
      relatedNews, newsIcon, openExternalUrl, openNewsItem, resolveNewsUrl, roundTitle, debateData,
      explainSummary, loadExplanation,
      platformQuotes, platformQuotesLoading, platformQuotesMeta, platformQuotesSorted,
      loadPlatformQuotes, refreshPlatformQuotes, platformLabel, platformQuotesRef, platformQuotesLive, livePriceAvg,
      // 对话
      chatMessages, visibleChatMessages, chatInput, chatLoading, chatStreaming, chatSuggestedIndex, sendMessage, askQuestion, onChatKeydown, renderMarkdown,
      suggestedQuestions, debateSuggestedQuestions, activeSuggestedQuestions, canSendChat,
      chatMode, setChatMode,
      chatAgentSession, chatBudget, chatRiskLevel,
      responseModelLabel, latestAgentResult, agentResultLines, runSkinAction, openPredictionResult, continueDebate,
      debateTotalRounds, copyDebateResult, isDebateInProgress, debateStageHint, debateStageStep,
      // 资讯 / 日报
      newsFeed, dailyReport, loadDailyReport, dailyReportLoading, dailyBreadth, dailySummaryBadge,
      dailyTab, setDailyTab, dailySourcesOpen,
      regenerateDailyReport, exportDailyReport,
      localizedHeadline, localizedNewsImpact,
      newsFetchLoading, fetchNewsNow,
      ragQuery, ragAnswer, ragAnswerSources, ragLoading, ragAsked, ragSuggestions, askRag, renderCitations, ragRetrieval,
      adminSession, adminIsAuthed, adminLoginForm, adminLoginError, adminLoginLoading,
      adminApiBase, adminUsers, adminConfig, adminStatus, adminProbeLlm, adminProbeEmbed,
      adminSaving, adminLoading, adminConfigForm,
      adminLogin, adminLogout, loadAdminPanel, saveAdminConfig, refreshAdminStatus, runProbeLlm, runProbeEmbed,
      // 预警
      alerts, showAlertModal, alertSkinLocked, alertLockedSkinName, newAlert,
      openAlertModal, closeAlertModal, addAlert, deleteAlert,
      // 持仓 / 库存
      portfolioTab, setPortfolioTab,
      portfolio, showPortfolioModal, newPortfolio, addPortfolio, removePortfolio,
      portfolioMetrics, getCurrentPrice, getItemPnl, getItemPnlPct,
      portfolioDiagnose, portfolioDiagnoseLoading, portfolioValueHistory, loadPortfolioExtras,
      diagnoseActionLabel, diagnoseActionClass, formatDiagnoseTime,
      myInventory, showInventoryModal, newInventory, addInventoryItem, removeInventoryItem,
      importSteamInventory, openInventoryItem,
      showSteamImportModal, steamImportLoading, steamImportForm, steamImportResult, submitSteamImport,
      inventoryMenuId, inventoryMenuItem, inventoryMenuStyle, toggleInventoryMenu, closeInventoryMenu,
      showInventoryEditModal, editingInventory, openEditInventoryPrice, saveInventoryPrice,
      inventoryItemCount, inventoryTotalValue, inventoryTotalChange24h, inventorySourceLabel,
      getSkinImage, getSkinImageUrl, getSkinChange24h, getSkinMeta,
      inventoryValueChart, inventoryValueHistory,
      refreshInventoryCharts,
      // 模型
      regressionModels, classificationModels, modelTypeLabel, formatModelReturn, modelComparison, hybridRoute,
      modelTrack, modelTrackMetadata, trend30Metrics, setModelTrack,
      modelsLoading, modelsDataSource, modelsNItems, modelsKpis, modelsBest, modelsFindingsPct,
      modelsFindingsSentence,
      selectedRadarModel, selectRadarModel, shapModel, shapModelOptions, setShapModel,
      shapEmpty, shapFeatureGuide, modelsInfoOpen, toggleModelsInfo,
      regressionGuideItems, classificationGuideItems, backtestEmpty,
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
  showErrorToast(t('error.title'), String(err.message || err).slice(0, 80));
};

// 未捕获的 JS 错误
window.addEventListener('error', (e) => {
  console.error('[Window Error]', e.error);
  // 避免在某些已知错误上刷屏 (CDN 加载失败等)
  if (e.message && e.message.includes('Script error')) return;
  showErrorToast(t('error.runtime'), String(e.message || '').slice(0, 80));
});

// 未处理的 Promise 拒绝
window.addEventListener('unhandledrejection', (e) => {
  console.error('[Unhandled Rejection]', e.reason);
  showErrorToast(t('error.async'), String(e.reason?.message || e.reason || '').slice(0, 80));
  e.preventDefault();
});

app.mount('#app');
