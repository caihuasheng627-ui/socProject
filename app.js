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
};
const toggleLang = () => {
  setLang(currentLang.value === 'zh-CN' ? 'en-US' : 'zh-CN');
};

const app = createApp({
  setup() {
    // ============ 菜单 ============
    // 7 大菜单 - Lucide Icons (专业开源图标库)
    const menu = computed(() => [
      {
        id: 'dashboard',
        label: t('menu.dashboard'),
        iconName: 'chart-line',
      },
      {
        id: 'prediction',
        label: t('menu.prediction'),
        badge: t('menu.badge.core'),
        iconName: 'target',
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
        id: 'alerts',
        label: t('menu.alerts'),
        iconName: 'bell',
      },
      {
        id: 'portfolio',
        label: t('menu.portfolio'),
        iconName: 'clipboard-list',
      },
      {
        id: 'models',
        label: t('menu.models'),
        iconName: 'cpu',
      },
    ]);

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

    const currentPage = ref('dashboard');
    const currentMenu = computed(() => menu.value.find(m => m.id === currentPage.value));

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
      const typeIcon = { success: '✅', error: '❌', info: 'ℹ️' }[type] || 'ℹ️';
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
    const skins = ref(window.CSVestData.SKINS_POOL);
    const topGainers = ref(window.CSVestData.TOP_GAINERS);
    const topLosers = ref(window.CSVestData.TOP_LOSERS);
    const hotVolume = ref(window.CSVestData.HOT_VOLUME);
    const newsFeed = ref(window.CSVestData.NEWS_FEED);
    const debateData = window.CSVestData.DEBATE_SAMPLE;
    const modelComparison = window.CSVestData.MODEL_COMPARISON;
    const regressionModels = ref([
      { ...modelComparison.regression[0], course: 'DL · panel Embedding' },
      { ...modelComparison.regression[1], course: 'DL · price tiers' },
      { ...modelComparison.regression[2], course: 'Route: low→C, mid/high→D' },
      { ...modelComparison.regression[3], course: 'Best MAPE on fair test' },
      { ...modelComparison.regression[4], course: 'Tree ensemble' },
      { ...modelComparison.regression[5], course: 'Tree ensemble' },
    ]);
    const hybridRoute = modelComparison.hybridRoute;
    const classificationModels = ref(modelComparison.classification);
    const suggestedQuestions = window.CSVestData.SUGGESTED_QUESTIONS;

    // ============ 行情看板 ============
    const filterCategory = ref('all');
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
    const filteredSkins = computed(() => {
      if (filterCategory.value === 'all') return skins.value;
      // 反向查找:从 i18n key 找到中文标签
      const zhLabel = Object.keys(categoryMap).find(k => categoryMap[k] === filterCategory.value);
      return skins.value.filter(s => s.category === zhLabel);
    });

    const refreshData = () => {
      // 模拟数据波动
      skins.value = skins.value.map(s => ({
        ...s,
        change24h: s.change24h + (Math.random() - 0.5) * 0.5,
        change7d: s.change7d + (Math.random() - 0.5) * 0.3,
        price: s.price * (1 + (Math.random() - 0.5) * 0.01),
      }));
      // 重新计算涨跌榜
      topGainers.value = [...skins.value].sort((a, b) => b.change7d - a.change7d).slice(0, 8);
      topLosers.value = [...skins.value].sort((a, b) => a.change7d - b.change7d).slice(0, 8);
    };

    // ============ AI 预测详情 ============
    const selectedSkin = ref(skins.value[1]); // 默认 AK-47 Fire Serpent
    const klineChart = ref(null);
    const timeframe = ref('90D');
    const klineLoading = ref(false);
    let klineChartInstance = null;

    const viewSkin = (skinId) => {
      const skin = skins.value.find(s => s.id === skinId);
      if (skin) {
        selectedSkin.value = skin;
        currentPage.value = 'prediction';
      }
    };

    const roundTitle = (idx) => {
      return ['独立分析', '互相质疑', '达成共识'][idx];
    };

    // 模型预测结果
    const modelPredictions = computed(() => {
      if (!selectedSkin.value) return [];
      const base = selectedSkin.value.price;
      return [
        { name: 'ARIMA', type: '统计', price: base * 1.012, change: 1.2, confidence: 65 },
        { name: 'XGBoost', type: 'ML', price: base * 1.018, change: 1.8, confidence: 78 },
        { name: 'LightGBM', type: 'ML', price: base * 1.016, change: 1.6, confidence: 76 },
        { name: 'Random Forest', type: 'ML', price: base * 1.014, change: 1.4, confidence: 72 },
        { name: 'LSTM', type: 'DL ⭐', price: base * 1.025, change: 2.5, confidence: 82 },
        { name: 'GRU', type: 'DL', price: base * 1.022, change: 2.2, confidence: 80 },
      ];
    });

    // 相关新闻
    const relatedNews = computed(() => {
      if (!selectedSkin.value) return [];
      return newsFeed.value.filter(n =>
        !n.relatedSkins.length || n.relatedSkins.includes(selectedSkin.value.id)
      ).slice(0, 4);
    });

    const newsIcon = (sentiment) => {
      return sentiment === 'positive' ? '📈' : sentiment === 'negative' ? '📉' : '📰';
    };

    // K线图渲染
    const renderKline = async () => {
      if (!klineChart.value || !selectedSkin.value) return;

      klineLoading.value = true;
      // 模拟短暂加载(让用户感知到数据刷新)
      await new Promise(resolve => setTimeout(resolve, 200));

      klineChartInstance = getOrCreateChart(klineChartInstance, klineChart.value);

      const days = { '7D': 7, '30D': 30, '90D': 90, '180D': 180 }[timeframe.value];
      const { kline, volumes } = window.CSVestData.generateKLineData(
        selectedSkin.value.price,
        days,
        selectedSkin.value.category === '箱子' ? 0.02 : 0.035
      );

      const ma7 = window.CSVestData.calculateMA(kline, 7);
      const ma30 = window.CSVestData.calculateMA(kline, 30);

      // 预测数据(虚线显示)
      const lastClose = parseFloat(kline[kline.length - 1][2]);
      const predictedDates = [];
      const predictedValues = [];
      for (let i = 1; i <= 30; i++) {
        const d = new Date(Date.now() + i * 24 * 60 * 60 * 1000);
        predictedDates.push(`${d.getMonth() + 1}/${d.getDate()}`);
        const predicted = lastClose * (1 + 0.0008 * i + (Math.random() - 0.5) * 0.01);
        predictedValues.push(predicted.toFixed(2));
      }

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
          { left: 60, right: 30, top: 40, height: '60%' },
          { left: 60, right: 30, top: '75%', height: '15%' },
        ],
        xAxis: [
          {
            type: 'category',
            data: kline.map(d => d[0]).concat(predictedDates),
            boundaryGap: false,
            axisLine: { lineStyle: { color: '#374151' } },
            axisLabel: { color: '#9ca3af', fontSize: 10 },
            splitLine: { show: false },
          },
          {
            type: 'category',
            gridIndex: 1,
            data: kline.map(d => d[0]).concat(predictedDates),
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
            data: kline.map(d => [d[1], d[3], d[4], d[2]]),
            itemStyle: {
              color: '#ef4444',  // 涨红色(中国市场)
              color0: '#10b981', // 跌绿色
              borderColor: '#ef4444',
              borderColor0: '#10b981',
            },
          },
          {
            name: 'MA7',
            type: 'line',
            data: ma7,
            smooth: true,
            showSymbol: false,
            lineStyle: { color: '#fbbf24', width: 1 },
          },
          {
            name: 'MA30',
            type: 'line',
            data: ma30,
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
            data: volumes,
            itemStyle: {
              color: (params) => params.data[2] > 0 ? '#ef4444' : '#10b981',
              opacity: 0.6,
            },
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

    const sendMessage = async (overrideText) => {
      // 防御:如果从 @click 调用,Vue 会传 MouseEvent,这里过滤掉
      const text = (typeof overrideText === 'string' ? overrideText : chatInput.value).trim();
      if (!text || chatLoading.value) return;

      chatMessages.value.push({
        role: 'user',
        content: text,
        time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      });
      chatInput.value = '';
      chatLoading.value = true;
      await scrollChatBottom();

      // 模拟 AI 回复延迟
      setTimeout(async () => {
        const response = generateAIResponse(text);
        chatMessages.value.push({
          role: 'assistant',
          content: response,
          time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
          model: 'DeepSeek-V3',
        });
        chatLoading.value = false;
        await scrollChatBottom();
      }, 1200 + Math.random() * 800);
    };

    // 监听聊天输入框的键盘事件
    const onChatKeydown = (e) => {
      // 输入框为空时,支持上下方向键选择建议问题
      if (!chatInput.value && chatMessages.value.length <= 1) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          chatSuggestedIndex.value = Math.min(chatSuggestedIndex.value + 1, suggestedQuestions.length - 1);
          if (chatSuggestedIndex.value < 0) chatSuggestedIndex.value = 0;
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          chatSuggestedIndex.value = Math.max(chatSuggestedIndex.value - 1, 0);
        } else if (e.key === 'Enter' && chatSuggestedIndex.value >= 0) {
          e.preventDefault();
          sendMessage(suggestedQuestions[chatSuggestedIndex.value]);
          chatSuggestedIndex.value = -1;
          return;
        }
      }
      // 默认 Enter 发送
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
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
      if (q.includes('5000') || q.includes('预算') || q.includes('推荐')) {
        return `根据您的 **5000 预算 + 中等风险** 偏好,推荐以下组合:

**🥇 首选组合 (稳健型):**
1. **AK-47 | Redline (FT)** × 5 件 = ¥2,148
   - 高流动性 (98分)、价格稳定、社区共识度高
2. **AWP | Asiimov (FT)** × 2 件 = ¥2,560
   - 中等价位、波动率适中、模型预测 +3.21% (7天)
3. **Dreams & Nightmares Case** × 30 件 = ¥275
   - 极致流动性、开箱期望值高

**总投入: ¥4,983** | **预期 30 天收益: +5%~+8%**

**⚠️ 风险提示:**
- 历史回测仅供参考,实际收益受市场波动影响
- 建议分批建仓,避免追高
- 设置止损位 -8%

是否需要我深入分析某个具体饰品?`;
      }
      if (q.includes('涨') || q.includes('今天')) {
        return `今日涨幅榜 Top 3:

1. **AWP | Dragon Lore (FT)** +8.45% (¥42,788)
   - 驱动: 职业选手偏好 + IEM Cologne 预期

2. **M9 Bayonet | Doppler (FN)** +7.89% (¥12,800)
   - 驱动: 刀具市场整体回暖

3. **AK-47 | Fire Serpent (FN)** +5.67% (¥15,850)
   - 驱动: Valve 更新未削弱 + 赛事需求

**📊 整体市场情绪:** 贪婪(指数 68)
**🔥 热点板块:** 高端饰品 (¥10k+) 持续走强

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
2. 🎯 目标价格 (如 ¥15,000)
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
      { id: 1, skinId: 'ak47-fireserpent-fn', skinName: 'AK-47 | Fire Serpent (FN)', type: 'above', targetPrice: 16500, currentPrice: 15850, active: true, triggered: false, createdAt: '2026-07-10 14:23' },
      { id: 2, skinId: 'awp-dragonlore-ft', skinName: 'AWP | Dragon Lore (FT)', type: 'above', targetPrice: 45000, currentPrice: 42788, active: true, triggered: true, createdAt: '2026-07-08 09:15' },
      { id: 3, skinId: 'm4a1s-printstream-ft', skinName: 'M4A1-S | Printstream (FT)', type: 'below', targetPrice: 900, currentPrice: 980, active: true, triggered: false, createdAt: '2026-07-12 16:40' },
      { id: 4, skinId: 'gloves-pandora-ft', skinName: '★ Sport Gloves | Pandora\'s Box (FT)', type: 'below', targetPrice: 20000, currentPrice: 22600, active: false, triggered: false, createdAt: '2026-07-05 11:20' },
    ]);

    const showAlertModal = ref(false);
    const newAlert = ref({ skinId: '', type: 'above', targetPrice: null, note: '' });

    const addAlert = () => {
      if (!newAlert.value.skinId || !newAlert.value.targetPrice) return;
      const skin = skins.value.find(s => s.id === newAlert.value.skinId);
      alerts.value.push({
        id: Date.now(),
        skinId: newAlert.value.skinId,
        skinName: skin.name,
        type: newAlert.value.type,
        targetPrice: newAlert.value.targetPrice,
        currentPrice: skin.price,
        active: true,
        triggered: false,
        createdAt: new Date().toLocaleString('zh-CN'),
      });
      showAlertModal.value = false;
      newAlert.value = { skinId: '', type: 'above', targetPrice: null, note: '' };
    };

    // ============ 持仓 ============
    const portfolio = ref([...window.CSVestData.DEFAULT_PORTFOLIO]);
    const showPortfolioModal = ref(false);
    const newPortfolio = ref({ skinId: '', buyPrice: null, quantity: 1, buyDate: '2026-07-15' });

    const getCurrentPrice = (skinId) => {
      return skins.value.find(s => s.id === skinId)?.price || 0;
    };

    const getItemPnl = (item) => {
      const current = getCurrentPrice(item.skinId);
      return (current - item.buyPrice) * item.quantity;
    };

    const getItemPnlPct = (item) => {
      return ((getCurrentPrice(item.skinId) - item.buyPrice) / item.buyPrice) * 100;
    };

    const portfolioMetrics = computed(() => {
      const prices = {};
      portfolio.value.forEach(p => prices[p.skinId] = getCurrentPrice(p.skinId));
      return window.CSVestData.calculateRiskMetrics(portfolio.value, prices);
    });

    const addPortfolio = () => {
      if (!newPortfolio.value.skinId || !newPortfolio.value.buyPrice) return;
      const skin = skins.value.find(s => s.id === newPortfolio.value.skinId);
      portfolio.value.push({
        id: Date.now(),
        skinId: newPortfolio.value.skinId,
        name: skin.name,
        buyPrice: newPortfolio.value.buyPrice,
        quantity: newPortfolio.value.quantity,
        buyDate: newPortfolio.value.buyDate,
      });
      showPortfolioModal.value = false;
      newPortfolio.value = { skinId: '', buyPrice: null, quantity: 1, buyDate: '2026-07-15' };
    };

    const removePortfolio = (id) => {
      portfolio.value = portfolio.value.filter(p => p.id !== id);
    };

    // ============ 模型实验室图表 ============
    const radarChart = ref(null);
    const backtestChart = ref(null);
    const shapChart = ref(null);
    let radarInstance = null, backtestInstance = null, shapInstance = null;

    const renderRadar = () => {
      if (!radarChart.value) return;
      radarInstance = getOrCreateChart(radarInstance, radarChart.value);

      const option = {
        backgroundColor: 'transparent',
        tooltip: { backgroundColor: '#1f2937', borderColor: '#374151', textStyle: { color: '#f3f4f6' } },
        legend: {
          data: ['LSTM', 'XGBoost', 'ARIMA', 'Random Forest'],
          textStyle: { color: '#9ca3af', fontSize: 11 },
          top: 0,
        },
        radar: {
          indicator: [
            { name: 'RMSE 精度', max: 100 },
            { name: '训练速度', max: 100 },
            { name: '可解释性', max: 100 },
            { name: '回测收益', max: 100 },
            { name: 'R²', max: 100 },
            { name: '泛化能力', max: 100 },
          ],
          center: ['50%', '55%'],
          radius: '60%',
          axisName: { color: '#9ca3af', fontSize: 11 },
          splitLine: { lineStyle: { color: '#2a3447' } },
          splitArea: { areaStyle: { color: ['rgba(255,107,0,0.02)', 'rgba(255,107,0,0.05)'] } },
          axisLine: { lineStyle: { color: '#374151' } },
        },
        series: [{
          type: 'radar',
          data: [
            { value: [88, 30, 35, 95, 92, 90], name: 'LSTM',
              areaStyle: { color: 'rgba(255, 107, 0, 0.2)' },
              lineStyle: { color: '#ff6b00', width: 2 },
              itemStyle: { color: '#ff6b00' } },
            { value: [80, 85, 70, 78, 89, 85], name: 'XGBoost',
              areaStyle: { color: 'rgba(59, 130, 246, 0.2)' },
              lineStyle: { color: '#3b82f6', width: 2 },
              itemStyle: { color: '#3b82f6' } },
            { value: [55, 95, 95, 35, 72, 60], name: 'ARIMA',
              areaStyle: { color: 'rgba(16, 185, 129, 0.2)' },
              lineStyle: { color: '#10b981', width: 2 },
              itemStyle: { color: '#10b981' } },
            { value: [70, 75, 65, 50, 84, 80], name: 'Random Forest',
              areaStyle: { color: 'rgba(139, 92, 246, 0.2)' },
              lineStyle: { color: '#8b5cf6', width: 2 },
              itemStyle: { color: '#8b5cf6' } },
          ],
        }],
      };
      radarInstance.setOption(option);
    };

    const renderBacktest = () => {
      if (!backtestChart.value) return;
      backtestInstance = getOrCreateChart(backtestInstance, backtestChart.value);

      const backtestData = window.CSVestData.generateBacktestData(60);
      const dates = Array.from({ length: 60 }, (_, i) => {
        const d = new Date(Date.now() - (60 - i) * 24 * 60 * 60 * 1000);
        return `${d.getMonth() + 1}/${d.getDate()}`;
      });

      const colors = {
        'LSTM': '#ff6b00',
        'XGBoost': '#3b82f6',
        'LightGBM': '#06b6d4',
        'ARIMA': '#10b981',
        '买入持有': '#6b7280',
      };

      const series = Object.entries(backtestData).map(([name, values]) => ({
        name,
        type: 'line',
        data: values,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: colors[name], width: name === 'LSTM' ? 3 : 2 },
        emphasis: { focus: 'series' },
      }));

      const option = {
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: '#1f2937', borderColor: '#374151', textStyle: { color: '#f3f4f6' } },
        legend: {
          data: Object.keys(backtestData),
          textStyle: { color: '#9ca3af', fontSize: 11 },
          top: 0,
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
          axisLine: { lineStyle: { color: '#374151' } },
          axisLabel: { color: '#9ca3af', fontSize: 10, formatter: '{value}' },
          splitLine: { lineStyle: { color: '#2a3447', type: 'dashed' } },
        },
        series,
      };
      backtestInstance.setOption(option);
    };

    const renderShap = () => {
      if (!shapChart.value) return;
      shapInstance = getOrCreateChart(shapInstance, shapChart.value);

      const data = window.CSVestData.SHAP_FEATURES.slice().reverse();

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
            formatter: (p) => p.value.toFixed(3),
          },
        }],
      };
      shapInstance.setOption(option);
    };

    // ============ 工具函数 ============
    const formatPrice = (num) => {
      if (num === null || num === undefined) return '0';
      return Number(num).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
          action: () => { currentPage.value = m.id; },
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
          subtitle: `${s.category} · ¥${formatPrice(s.price)} · 7d ${s.change7d >= 0 ? '+' : ''}${s.change7d.toFixed(2)}%`,
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
        if (num >= 1 && num <= 7) {
          e.preventDefault();
          const target = menu.value[num - 1];
          currentPage.value = target.id;
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

      renderKline();
      window.addEventListener('keydown', handleGlobalKeydown);
      window.addEventListener('resize', () => {
        klineChartInstance?.resize();
        radarInstance?.resize();
        backtestInstance?.resize();
        shapInstance?.resize();
      });

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

      // 不再弹欢迎 Toast (用户反馈: 弹窗太多令人困惑)
    });

    // Vue 重渲染后再次处理 (新插入的 ph-* 元素)
    onUpdated(() => {
      window.processPhIcons && window.processPhIcons();
    });

    // 监听页面切换
    watch(currentPage, async (newPage) => {
      await nextTick();
      // 切换页面后再次处理 (v-if 内的新图标)
      window.processPhIcons && window.processPhIcons();
      if (newPage === 'prediction') {
        renderKline();
      } else if (newPage === 'models') {
        setTimeout(() => {
          renderRadar();
          renderBacktest();
          renderShap();
        }, 100);
      } else if (newPage === 'chat') {
        setTimeout(scrollChatBottom, 100);
      }
    });

    // 监听选中饰品变化
    watch(selectedSkin, () => {
      if (currentPage.value === 'prediction') {
        renderKline();
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
      menu, currentPage, currentMenu, renderMenuIcon,
      // 行情
      skins, topGainers, topLosers, hotVolume, refreshData,
      filterCategory, categoryKeys, categoryMap, filteredSkins,
      // 预测
      selectedSkin, viewSkin, klineChart, klineLoading, timeframe, renderKline,
      modelPredictions, relatedNews, newsIcon, roundTitle, debateData,
      // 对话
      chatMessages, chatInput, chatLoading, chatSuggestedIndex, sendMessage, askQuestion, onChatKeydown, renderMarkdown, suggestedQuestions,
      // 资讯
      newsFeed,
      // 预警
      alerts, showAlertModal, newAlert, addAlert,
      // 持仓
      portfolio, showPortfolioModal, newPortfolio, addPortfolio, removePortfolio,
      portfolioMetrics, getCurrentPrice, getItemPnl, getItemPnlPct,
      // 模型
      regressionModels, classificationModels, modelComparison, hybridRoute,
      radarChart, backtestChart, shapChart,
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