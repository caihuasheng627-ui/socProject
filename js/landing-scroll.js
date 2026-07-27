// ============================================
// Landing scroll reveal + soft section pause
// ============================================

(function (global) {
  function clamp(n, a, b) {
    return Math.max(a, Math.min(b, n));
  }

  function initLandingScroll(root, options = {}) {
    if (!root) {
      return { destroy() {}, refresh() {} };
    }

    const reduceMotion = !!(
      options.reduceMotion ||
      (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches)
    );

    const selector = options.selector || '.landing-section--stage, .landing-tile--stage, .landing-hero';
    let panels = [];
    let raf = 0;
    let destroyed = false;
    let snapLock = false;
    let snapTimer = 0;
    let wheelAcc = 0;

    const onScroll = () => {
      if (destroyed) return;
      if (raf) return;
      raf = requestAnimationFrame(paint);
    };

    function collect() {
      panels = Array.from(root.querySelectorAll(selector));
    }

    function paint() {
      raf = 0;
      if (destroyed) return;
      const vh = window.innerHeight || 1;
      const focusY = vh * 0.42;

      panels.forEach((el) => {
        const rect = el.getBoundingClientRect();
        if (rect.height < 8) return;

        const mid = rect.top + rect.height * 0.38;
        const dist = Math.abs(mid - focusY);
        const raw = 1 - dist / (vh * 0.62);
        const reveal = reduceMotion ? 1 : clamp(raw, 0, 1);
        // Stronger contrast — linger dim, punch when focused
        const punch = Math.pow(reveal, 1.35);
        const eased = punch * punch * (3 - 2 * punch);

        el.style.setProperty('--reveal', eased.toFixed(4));
        el.style.setProperty('--reveal-y', ((mid - focusY) / vh).toFixed(4));
        el.classList.toggle('is-inview', eased > 0.18);
        el.classList.toggle('is-focus', eased > 0.52);
      });
    }

    function activeIndex() {
      const vh = window.innerHeight || 1;
      let best = 0;
      let bestScore = -Infinity;
      panels.forEach((el, i) => {
        const rect = el.getBoundingClientRect();
        const mid = rect.top + rect.height * 0.4;
        const score = 1 - Math.abs(mid - vh * 0.42) / vh;
        if (score > bestScore) {
          bestScore = score;
          best = i;
        }
      });
      return best;
    }

    function navOffset() {
      const raw = getComputedStyle(root).getPropertyValue('--lp-nav-h');
      const n = parseFloat(raw);
      return Number.isFinite(n) ? n : 48;
    }

    function scrollToPanel(index) {
      const el = panels[clamp(index, 0, panels.length - 1)];
      if (!el) return;
      const top = el.offsetTop - navOffset();
      root.scrollTo({ top: Math.max(0, top), behavior: reduceMotion ? 'auto' : 'smooth' });
    }

    function onWheel(e) {
      if (destroyed || reduceMotion) return;
      // Let hero scene scrub handle first screen
      const hero = root.querySelector('.landing-hero');
      if (hero) {
        const hr = hero.getBoundingClientRect();
        const inHero = hr.top < window.innerHeight * 0.35 && hr.bottom > window.innerHeight * 0.45;
        if (inHero) return;
      }

      let dy = e.deltaY;
      if (e.deltaMode === 1) dy *= 16;
      if (e.deltaMode === 2) dy *= window.innerHeight;
      if (!dy) return;

      const idx = activeIndex();
      const panel = panels[idx];
      if (panel) {
        const rect = panel.getBoundingClientRect();
        const nav = navOffset();
        // Tall panels (e.g. feature shots): allow native scroll until the edge, then snap.
        if (dy > 0 && rect.bottom > window.innerHeight + 12) {
          wheelAcc = 0;
          return;
        }
        if (dy < 0 && rect.top < nav - 12) {
          wheelAcc = 0;
          return;
        }
      }

      const atLast = idx >= panels.length - 1 && dy > 0;
      const atFirst = idx <= 0 && dy < 0;
      if (atLast || atFirst) {
        wheelAcc = 0;
        return;
      }

      // Soft section stepping with pause
      e.preventDefault();
      if (snapLock) return;

      wheelAcc += dy;
      const threshold = 70;
      if (Math.abs(wheelAcc) < threshold) return;

      const dir = wheelAcc > 0 ? 1 : -1;
      wheelAcc = 0;
      const next = clamp(idx + dir, 0, panels.length - 1);
      if (next === idx) return;

      snapLock = true;
      scrollToPanel(next);
      if (snapTimer) clearTimeout(snapTimer);
      snapTimer = setTimeout(() => {
        snapLock = false;
        snapTimer = 0;
      }, 620);
    }

    collect();
    paint();
    root.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    if (!reduceMotion) {
      root.addEventListener('wheel', onWheel, { passive: false });
    }

    return {
      destroy() {
        destroyed = true;
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
        if (snapTimer) clearTimeout(snapTimer);
        snapTimer = 0;
        root.removeEventListener('scroll', onScroll);
        window.removeEventListener('resize', onScroll);
        root.removeEventListener('wheel', onWheel);
        panels.forEach((el) => {
          el.style.removeProperty('--reveal');
          el.style.removeProperty('--reveal-y');
          el.classList.remove('is-inview', 'is-focus');
        });
        panels = [];
      },
      refresh() {
        collect();
        paint();
      },
    };
  }

  global.initLandingScroll = initLandingScroll;
})(typeof window !== 'undefined' ? window : globalThis);
