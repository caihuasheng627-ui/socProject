// ============================================
// Landing wheel scenes: silky continuous scrub
// Real CS skins from data.js (stylized frames)
// ============================================

(function (global) {
  const DEFAULT_SCENES = [
    {
      skinId: 'ak47-fireserpent-fn',
      src: 'assets/landing/scene-ak-fireserpent.png',
      mapZh: 'Dust II · 炸弹点 B',
      mapEn: 'Dust II · B site',
      fallback: {
        name: 'AK-47 | Fire Serpent (FN)',
        image: '🐍',
        price: 2201.39,
      },
    },
    {
      skinId: 'karambit-doppler-fn',
      src: 'assets/landing/scene-karambit-doppler.png',
      mapZh: 'Mirage · 中路展台',
      mapEn: 'Mirage · mid vault',
      fallback: {
        name: '★ Karambit | Doppler (FN)',
        image: '🌀',
        price: 2569.44,
      },
    },
    {
      skinId: 'gloves-pandora-ft',
      src: 'assets/landing/scene-gloves-pandora.png',
      mapZh: 'Inferno · 香蕉道',
      mapEn: 'Inferno · banana',
      fallback: {
        name: "★ Sport Gloves | Pandora's Box (FT)",
        image: '🎁',
        price: 3138.89,
      },
    },
    {
      skinId: 'awp-dragonlore-ft',
      src: 'assets/landing/scene-awp-dragonlore.png',
      mapZh: 'Ancient · 遗迹大厅',
      mapEn: 'Ancient · ruins',
      fallback: {
        name: 'AWP | Dragon Lore (FT)',
        image: '🐉',
        price: 5942.78,
      },
    },
  ];

  function clamp(n, a, b) {
    return Math.max(a, Math.min(b, n));
  }

  function preferZh() {
    try {
      const lang = (document.documentElement.lang || '').toLowerCase();
      return lang.startsWith('zh');
    } catch (_) {
      return false;
    }
  }

  function findSkin(id) {
    const pool =
      (global.CSVestData && (global.CSVestData.SKINS_POOL || global.CSVestData.SKINS)) ||
      [];
    return pool.find((s) => s && s.id === id) || null;
  }

  function enrichScenes(raw) {
    return raw.map((scene) => {
      const skin = findSkin(scene.skinId) || scene.fallback || {};
      return {
        ...scene,
        name: skin.name || scene.fallback?.name || scene.skinId,
        image: skin.image || scene.fallback?.image || '🔫',
        price: typeof skin.price === 'number' ? skin.price : scene.fallback?.price,
      };
    });
  }

  function formatPrice(n) {
    if (typeof n !== 'number' || Number.isNaN(n)) return '';
    return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  function initLandingScenes(root, options = {}) {
    if (!root) {
      return { destroy() {}, setProgress() {}, getProgress() { return 0; } };
    }

    const scenes = enrichScenes(
      Array.isArray(options.scenes) && options.scenes.length ? options.scenes : DEFAULT_SCENES
    );
    const maxIndex = Math.max(0, scenes.length - 1);

    const reduceMotion = !!(
      options.reduceMotion ||
      (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches)
    );

    let layersHost = root.querySelector('.landing-hero-scenes');
    if (!layersHost) {
      layersHost = document.createElement('div');
      layersHost.className = 'landing-hero-scenes';
      root.insertBefore(layersHost, root.firstChild);
    }
    layersHost.innerHTML = '';

    const layers = scenes.map((scene, i) => {
      const el = document.createElement('div');
      el.className = 'landing-scene' + (i === 0 ? ' is-on' : '');
      el.style.backgroundImage = `url('${scene.src}')`;
      el.setAttribute('aria-hidden', 'true');
      layersHost.appendChild(el);
      return el;
    });

    scenes.forEach((s) => {
      const img = new Image();
      img.decoding = 'async';
      img.src = s.src;
    });

    const captionEl = options.captionEl || document.getElementById('landing-scene-caption');
    const nameEl = options.nameEl || document.getElementById('landing-scene-name');
    const mapEl = options.mapEl || document.getElementById('landing-scene-map');
    const priceEl = options.priceEl || document.getElementById('landing-scene-price');
    const dotsEl = options.dotsEl || document.getElementById('landing-scene-dots');

    if (dotsEl) {
      dotsEl.innerHTML = '';
      scenes.forEach((_, i) => {
        const dot = document.createElement('span');
        dot.className = 'landing-scene-dot' + (i === 0 ? ' is-active' : '');
        dotsEl.appendChild(dot);
      });
    }

    let progress = 0;
    let target = 0;
    let velocity = 0;
    let raf = 0;
    let lastTs = 0;
    let destroyed = false;
    let lastCaptionIdx = -1;
    const listeners = [];

    function activeScene(i) {
      return scenes[clamp(Math.round(i), 0, maxIndex)];
    }

    function updateCaption(idx) {
      if (idx === lastCaptionIdx) return;
      lastCaptionIdx = idx;
      const scene = scenes[idx];
      if (!scene) return;
      const zh = preferZh();
      if (nameEl) nameEl.textContent = scene.name;
      if (mapEl) mapEl.textContent = zh ? scene.mapZh : scene.mapEn;
      if (priceEl) priceEl.textContent = formatPrice(scene.price);
      if (captionEl) {
        captionEl.classList.remove('is-pulse');
        // force reflow for soft pulse
        void captionEl.offsetWidth;
        captionEl.classList.add('is-pulse');
      }
      if (dotsEl) {
        dotsEl.querySelectorAll('.landing-scene-dot').forEach((dot, i) => {
          dot.classList.toggle('is-active', i === idx);
        });
      }
    }

    function paint(p) {
      progress = clamp(p, 0, maxIndex);
      const i0 = Math.floor(progress);
      const i1 = Math.min(maxIndex, i0 + 1);
      const t = progress - i0;
      // Smoothstep for silkier crossfade
      const u = t * t * (3 - 2 * t);

      layers.forEach((el, i) => {
        let op = 0;
        if (i === i0) op = 1 - u;
        if (i === i1 && i1 !== i0) op = u;
        if (i0 === i1 && i === i0) op = 1;
        el.style.opacity = String(op);
        el.classList.toggle('is-on', op > 0.02);
        const y = (progress - i) * 28;
        const scale = 1.04 + Math.abs(progress - i) * 0.02;
        el.style.transform = `translate3d(0, ${y}px, 0) scale(${scale})`;
      });

      updateCaption(Math.round(progress));
    }

    function tick(ts) {
      if (destroyed) return;
      if (!lastTs) lastTs = ts;
      const dt = Math.min(40, ts - lastTs);
      lastTs = ts;

      // Critically-damped feel toward target
      const ease = 1 - Math.exp(-dt / 140);
      progress += (target - progress) * ease;
      // Bleed residual velocity into target for inertia
      if (Math.abs(velocity) > 0.0002) {
        target = clamp(target + velocity * (dt / 16), 0, maxIndex);
        velocity *= Math.exp(-dt / 90);
      }

      paint(progress);

      const still =
        Math.abs(target - progress) < 0.0008 &&
        Math.abs(velocity) < 0.0002;

      if (!still) {
        raf = requestAnimationFrame(tick);
      } else {
        progress = target;
        paint(progress);
        raf = 0;
        lastTs = 0;
      }
    }

    function kick() {
      if (!raf) {
        lastTs = 0;
        raf = requestAnimationFrame(tick);
      }
    }

    function heroInView() {
      const hero = root.closest('.landing-hero') || root;
      const rect = hero.getBoundingClientRect();
      return rect.top < window.innerHeight * 0.4 && rect.bottom > window.innerHeight * 0.28;
    }

    let sceneLock = false;
    let sceneLockTimer = 0;
    let wheelAcc = 0;
    let touchStartY = 0;
    let touchStartX = 0;
    let touchActive = false;

    function stepScene(dir) {
      const current = Math.round(target);
      const next = clamp(current + dir, 0, maxIndex);
      if (next === current || sceneLock) return false;
      sceneLock = true;
      velocity = 0;
      target = next;
      kick();
      if (sceneLockTimer) clearTimeout(sceneLockTimer);
      sceneLockTimer = setTimeout(() => {
        sceneLock = false;
        sceneLockTimer = 0;
      }, 480);
      return true;
    }

    function onWheel(e) {
      if (destroyed) return;
      if (!heroInView()) return;

      let dy = e.deltaY;
      if (e.deltaMode === 1) dy *= 16;
      if (e.deltaMode === 2) dy *= window.innerHeight;
      if (!dy) return;

      const current = Math.round(target);
      const atEnd = current >= maxIndex && dy > 0;
      const atStart = current <= 0 && dy < 0;
      const landing = root.closest('.landing');
      const scrollTop = landing
        ? landing.scrollTop
        : (window.scrollY || document.documentElement.scrollTop || 0);

      if (atEnd) {
        velocity = 0;
        wheelAcc = 0;
        return;
      }
      if (atStart && scrollTop > 4) {
        velocity = 0;
        wheelAcc = 0;
        return;
      }

      e.preventDefault();
      if (sceneLock) return;

      wheelAcc += dy;
      const threshold = e.deltaMode === 1 ? 1.5 : 55;
      if (Math.abs(wheelAcc) < threshold) return;

      const dir = wheelAcc > 0 ? 1 : -1;
      wheelAcc = 0;
      stepScene(dir);
    }

    function onTouchStart(e) {
      if (destroyed || !heroInView() || !e.touches || !e.touches[0]) return;
      touchActive = true;
      touchStartY = e.touches[0].clientY;
      touchStartX = e.touches[0].clientX;
    }

    function onTouchMove(e) {
      if (!touchActive || destroyed || !heroInView()) return;
      if (!e.touches || !e.touches[0]) return;
      const dy = e.touches[0].clientY - touchStartY;
      const dx = e.touches[0].clientX - touchStartX;
      if (Math.abs(dy) < 12 || Math.abs(dy) < Math.abs(dx) * 1.2) return;

      const current = Math.round(target);
      const atEnd = current >= maxIndex && dy < 0; // finger up => next
      const atStart = current <= 0 && dy > 0;
      const landing = root.closest('.landing');
      const scrollTop = landing ? landing.scrollTop : 0;

      if (atEnd || (atStart && scrollTop > 4)) return;

      // Keep vertical swipe on hero for scene change
      if (Math.abs(dy) > 18) e.preventDefault();
    }

    function onTouchEnd(e) {
      if (!touchActive || destroyed) {
        touchActive = false;
        return;
      }
      touchActive = false;
      if (!heroInView()) return;
      const t = (e.changedTouches && e.changedTouches[0]) || null;
      if (!t) return;
      const dy = t.clientY - touchStartY;
      const dx = t.clientX - touchStartX;
      if (Math.abs(dy) < 42 || Math.abs(dy) < Math.abs(dx) * 1.15) return;

      const current = Math.round(target);
      const dir = dy < 0 ? 1 : -1; // swipe up => next scene
      const atEnd = current >= maxIndex && dir > 0;
      const atStart = current <= 0 && dir < 0;
      const landing = root.closest('.landing');
      const scrollTop = landing ? landing.scrollTop : 0;
      if (atEnd || (atStart && scrollTop > 4)) return;

      stepScene(dir);
    }

    function bind(el, type, fn, opts) {
      if (!el) return;
      el.addEventListener(type, fn, opts);
      listeners.push([el, type, fn, opts]);
    }

    const wheelHost = options.wheelHost || root.closest('.landing') || root.closest('.landing-hero') || window;
    const heroEl = root.closest('.landing-hero') || root;
    bind(wheelHost, 'wheel', onWheel, { passive: false });
    bind(heroEl, 'touchstart', onTouchStart, { passive: true });
    bind(heroEl, 'touchmove', onTouchMove, { passive: false });
    bind(heroEl, 'touchend', onTouchEnd, { passive: true });
    bind(heroEl, 'touchcancel', onTouchEnd, { passive: true });

    paint(0);

    return {
      destroy() {
        destroyed = true;
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
        if (sceneLockTimer) clearTimeout(sceneLockTimer);
        sceneLockTimer = 0;
        sceneLock = false;
        listeners.forEach(([el, type, fn, opts]) => {
          try { el.removeEventListener(type, fn, opts); } catch (_) { /* ignore */ }
        });
        listeners.length = 0;
      },
      setProgress(p, animate) {
        const next = clamp(p, 0, maxIndex);
        target = next;
        if (animate && !reduceMotion) kick();
        else {
          progress = next;
          paint(progress);
        }
      },
      getProgress() { return progress; },
      animateTo(index) {
        target = clamp(index, 0, maxIndex);
        velocity = 0;
        kick();
      },
      refreshLang() {
        lastCaptionIdx = -1;
        paint(progress);
      },
    };
  }

  global.initLandingScenes = initLandingScenes;
  global.LANDING_SCENES = DEFAULT_SCENES;
})(typeof window !== 'undefined' ? window : globalThis);
