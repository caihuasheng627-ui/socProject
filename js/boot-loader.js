// ============================================
// CSVest boot splash — prioritize first-screen visuals
// Shows a real progress bar while critical landing images load
// ============================================

(function (global) {
  const CRITICAL = [
    'assets/landing/scene-ak-fireserpent.png',
  ];

  const WARM = [
    'assets/landing/scene-karambit-doppler.png',
    'assets/landing/scene-gloves-pandora.png',
  ];

  const MIN_MS = 720;
  const MAX_MS = 8000;

  const STATUS = {
    start: 'Warming up the arsenal…',
    critical: 'Loading hero visuals…',
    warm: 'Sharpening the next scenes…',
    almost: 'Almost ready…',
    done: 'Welcome to CSVest',
  };

  const startedAt = Date.now();
  let settled = false;
  let progress = 0;
  let resolveReady;
  const readyPromise = new Promise((resolve) => {
    resolveReady = resolve;
  });

  const el = {
    root: null,
    fill: null,
    text: null,
    pct: null,
  };

  function bindDom() {
    el.root = document.getElementById('app-loader');
    el.fill = document.getElementById('boot-progress-fill');
    el.text = document.getElementById('boot-status');
    el.pct = document.getElementById('boot-pct');
  }

  function setStatus(msg) {
    if (el.text) el.text.textContent = msg;
  }

  function setProgress(p) {
    progress = Math.max(progress, Math.min(100, Math.round(p)));
    if (el.fill) {
      el.fill.style.width = progress + '%';
      el.fill.classList.add('is-driven');
    }
    if (el.pct) el.pct.textContent = progress + '%';
    if (el.root) el.root.setAttribute('aria-valuenow', String(progress));
  }

  function loadImage(src) {
    return new Promise((resolve) => {
      const img = new Image();
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        resolve(src);
      };
      img.onload = finish;
      img.onerror = finish;
      img.decoding = 'async';
      img.src = src;
      if (img.complete && img.naturalWidth > 0) finish();
    });
  }

  async function loadGroup(urls, weightStart, weightEnd, status) {
    if (!urls.length) {
      setProgress(weightEnd);
      return;
    }
    setStatus(status);
    let completed = 0;
    const span = weightEnd - weightStart;
    await Promise.all(
      urls.map(async (src) => {
        await loadImage(src);
        completed += 1;
        setProgress(weightStart + (span * completed) / urls.length);
      })
    );
  }

  function dismiss() {
    if (settled) return;
    settled = true;
    setProgress(100);
    setStatus(STATUS.done);
    const root = el.root || document.getElementById('app-loader');
    if (!root) {
      resolveReady();
      return;
    }
    root.classList.add('is-leaving');
    window.setTimeout(() => {
      root.classList.add('hidden');
      root.setAttribute('aria-busy', 'false');
      root.setAttribute('aria-hidden', 'true');
      resolveReady();
    }, 420);
  }

  function finishWhenAllowed() {
    const elapsed = Date.now() - startedAt;
    const wait = Math.max(0, MIN_MS - elapsed);
    window.setTimeout(dismiss, wait);
  }

  function applySectionBg(node) {
    if (!node || node.dataset.loaded === '1') return;
    const src = node.getAttribute('data-bg');
    if (!src) return;
    node.dataset.loaded = '1';
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      node.style.backgroundImage = `url('${src}')`;
      node.classList.add('is-ready');
    };
    img.onerror = () => {
      node.classList.add('is-ready');
    };
    img.src = src;
  }

  function lazyLoadSections(root) {
    const scope = root || document;
    const nodes = scope.querySelectorAll('.landing-section-bg[data-bg]:not([data-loaded="1"])');
    if (!nodes.length) return;

    if (typeof IntersectionObserver !== 'function') {
      nodes.forEach(applySectionBg);
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          applySectionBg(entry.target);
          io.unobserve(entry.target);
        });
      },
      { rootMargin: '240px 0px', threshold: 0.01 }
    );

    nodes.forEach((node) => io.observe(node));
  }

  async function run() {
    bindDom();
    if (el.root) {
      el.root.setAttribute('role', 'progressbar');
      el.root.setAttribute('aria-busy', 'true');
      el.root.setAttribute('aria-valuemin', '0');
      el.root.setAttribute('aria-valuemax', '100');
      el.root.setAttribute('aria-valuenow', '0');
      el.root.setAttribute('aria-label', 'Loading CSVest');
    }

    setStatus(STATUS.start);
    setProgress(4);

    const maxTimer = window.setTimeout(() => {
      setStatus(STATUS.almost);
      finishWhenAllowed();
    }, MAX_MS);

    try {
      await loadGroup(CRITICAL, 8, 72, STATUS.critical);
      // Hero is usable — dismiss soon; keep warming the next scenes.
      finishWhenAllowed();
      window.clearTimeout(maxTimer);
      loadGroup(WARM, 72, 96, STATUS.warm).then(() => {
        setProgress(100);
        lazyLoadSections();
      });
    } catch (_) {
      window.clearTimeout(maxTimer);
      finishWhenAllowed();
    }
  }

  const api = {
    ready: readyPromise,
    waitUntilReady() {
      return readyPromise;
    },
    lazyLoadSections,
    getProgress() {
      return progress;
    },
    isReady() {
      return settled;
    },
  };

  global.CSVestBoot = api;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run, { once: true });
  } else {
    run();
  }
})(typeof window !== 'undefined' ? window : globalThis);
