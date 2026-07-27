// ============================================
// Landing interactive skin constellation
// Pointer-driven nodes from CSVestData skins
// ============================================

(function (global) {
  const RARITY_COLORS = {
    1: '#b0b3bd',
    2: '#4d8cf7',
    3: '#4d8cf7',
    4: '#a855f7',
    5: '#eb4b4b',
    6: '#ff6b00',
    7: '#ffd700',
  };

  function shortName(name) {
    if (!name) return 'Skin';
    const base = String(name).replace(/\s*\([^)]*\)\s*$/, '');
    const parts = base.split('|');
    const right = (parts[1] || parts[0] || '').trim();
    return right.length > 16 ? right.slice(0, 15) + '…' : right;
  }

  function pickSkins(skins, limit) {
    const list = Array.isArray(skins) ? skins.slice() : [];
    list.sort((a, b) => {
      const ra = (b.rarity || 0) - (a.rarity || 0);
      if (ra) return ra;
      return (b.liquidity || 0) - (a.liquidity || 0);
    });
    return list.slice(0, limit);
  }

  function initLandingCanvas(canvas, skins, options = {}) {
    if (!canvas || typeof canvas.getContext !== 'function') {
      return { destroy() {}, tip: null };
    }

    const tipEl = options.tipEl || null;
    const reduceMotion = !!(
      options.reduceMotion ||
      (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches)
    );

    const ctx = canvas.getContext('2d');
    let raf = 0;
    let running = true;
    let w = 0;
    let h = 0;
    let dpr = 1;
    const pointer = { x: -9999, y: -9999, active: false };
    let nodes = [];
    let linkDist = 140;
    let maxNodes = 32;

    function computeBudget() {
      const mobile = w < 720;
      maxNodes = mobile ? 18 : (w < 1100 ? 26 : 34);
      linkDist = mobile ? 0 : (w < 1100 ? 118 : 150);
    }

    function resize() {
      const parent = canvas.parentElement || canvas;
      const rect = parent.getBoundingClientRect();
      w = Math.max(1, Math.floor(rect.width));
      h = Math.max(1, Math.floor(rect.height));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = w + 'px';
      canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      computeBudget();
      seedNodes(true);
      if (reduceMotion) drawFrame(true);
    }

    function seedNodes(keepLayout) {
      const picked = pickSkins(skins, maxNodes);
      const prev = keepLayout ? nodes : [];
      nodes = picked.map((s, i) => {
        const old = prev[i];
        const cx = old ? old.x : (0.12 + Math.random() * 0.76) * w;
        const cy = old ? old.y : (0.14 + Math.random() * 0.72) * h;
        return {
          id: s.id || String(i),
          label: shortName(s.name),
          fullName: s.name || shortName(s.name),
          price: Number(s.price) || 0,
          emoji: s.image || '◆',
          color: RARITY_COLORS[s.rarity] || RARITY_COLORS[4],
          x: cx,
          y: cy,
          vx: 0,
          vy: 0,
          ox: cx,
          oy: cy,
          r: 10 + Math.min(8, (s.rarity || 1) * 1.1),
          phase: Math.random() * Math.PI * 2,
        };
      });
      // fill ambient dust if few skins
      while (nodes.length < Math.min(maxNodes, 12)) {
        const i = nodes.length;
        const cx = (0.1 + Math.random() * 0.8) * w;
        const cy = (0.1 + Math.random() * 0.8) * h;
        nodes.push({
          id: 'dust-' + i,
          label: '',
          fullName: '',
          price: 0,
          emoji: '·',
          color: 'rgba(255,107,0,0.55)',
          x: cx,
          y: cy,
          vx: 0,
          vy: 0,
          ox: cx,
          oy: cy,
          r: 2.5,
          phase: Math.random() * Math.PI * 2,
          dust: true,
        });
      }
    }

    function showTip(node, clientX, clientY) {
      if (!tipEl || !node || node.dust || !node.fullName) {
        hideTip();
        return;
      }
      tipEl.hidden = false;
      tipEl.innerHTML =
        `<strong>${escapeHtml(node.fullName)}</strong>` +
        (node.price ? `<span>$${node.price.toFixed(2)}</span>` : '');
      const pad = 14;
      const tw = tipEl.offsetWidth || 160;
      const th = tipEl.offsetHeight || 48;
      let left = clientX + 14;
      let top = clientY + 14;
      if (left + tw > window.innerWidth - pad) left = clientX - tw - 10;
      if (top + th > window.innerHeight - pad) top = clientY - th - 10;
      tipEl.style.left = left + 'px';
      tipEl.style.top = top + 'px';
    }

    function hideTip() {
      if (!tipEl) return;
      tipEl.hidden = true;
    }

    function escapeHtml(str) {
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function hitTest(x, y) {
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        if (n.dust) continue;
        const dx = n.x - x;
        const dy = n.y - y;
        if (dx * dx + dy * dy <= (n.r + 10) * (n.r + 10)) return n;
      }
      return null;
    }

    function step(dt) {
      const t = performance.now() * 0.001;
      for (const n of nodes) {
        const driftX = Math.sin(t * 0.45 + n.phase) * 10;
        const driftY = Math.cos(t * 0.38 + n.phase * 1.3) * 8;
        let tx = n.ox + driftX;
        let ty = n.oy + driftY;

        if (pointer.active) {
          const dx = n.x - pointer.x;
          const dy = n.y - pointer.y;
          const dist = Math.hypot(dx, dy) || 1;
          const radius = 160;
          if (dist < radius) {
            const force = (1 - dist / radius) * 38;
            tx += (dx / dist) * force;
            ty += (dy / dist) * force;
          }
        }

        n.vx += (tx - n.x) * 0.045;
        n.vy += (ty - n.y) * 0.045;
        n.vx *= 0.86;
        n.vy *= 0.86;
        n.x += n.vx * (dt / 16);
        n.y += n.vy * (dt / 16);
        n.x = Math.max(18, Math.min(w - 18, n.x));
        n.y = Math.max(18, Math.min(h - 18, n.y));
      }
    }

    function drawFrame(staticOnly) {
      ctx.clearRect(0, 0, w, h);

      // soft vignette wash
      const g = ctx.createRadialGradient(w * 0.7, h * 0.35, 40, w * 0.55, h * 0.45, Math.max(w, h) * 0.75);
      g.addColorStop(0, 'rgba(255,107,0,0.07)');
      g.addColorStop(0.45, 'rgba(77,140,247,0.04)');
      g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      if (linkDist > 0) {
        ctx.lineWidth = 1;
        for (let i = 0; i < nodes.length; i++) {
          for (let j = i + 1; j < nodes.length; j++) {
            const a = nodes[i];
            const b = nodes[j];
            const dx = a.x - b.x;
            const dy = a.y - b.y;
            const d = Math.hypot(dx, dy);
            if (d > linkDist) continue;
            let nearPointer = false;
            if (pointer.active) {
              const midX = (a.x + b.x) / 2;
              const midY = (a.y + b.y) / 2;
              nearPointer = Math.hypot(midX - pointer.x, midY - pointer.y) < 120;
            }
            const alpha = (1 - d / linkDist) * (nearPointer ? 0.55 : 0.18);
            ctx.strokeStyle = nearPointer
              ? `rgba(255,107,0,${alpha})`
              : `rgba(120,140,180,${alpha})`;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }

      for (const n of nodes) {
        if (n.dust) {
          ctx.beginPath();
          ctx.fillStyle = n.color;
          ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
          ctx.fill();
          continue;
        }

        const highlighted = pointer.active && Math.hypot(n.x - pointer.x, n.y - pointer.y) < 90;
        const rr = n.r + (highlighted ? 3 : 0);

        ctx.beginPath();
        ctx.fillStyle = highlighted ? 'rgba(255,107,0,0.28)' : 'rgba(16,16,24,0.72)';
        ctx.strokeStyle = n.color;
        ctx.lineWidth = highlighted ? 2 : 1.25;
        ctx.arc(n.x, n.y, rr + 6, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();

        ctx.font = `${Math.round(rr + 4)}px "Segoe UI Emoji","Apple Color Emoji",sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#fff';
        ctx.fillText(n.emoji, n.x, n.y + 1);

        if (highlighted && n.label) {
          ctx.font = '600 11px Outfit, system-ui, sans-serif';
          ctx.fillStyle = 'rgba(242,239,233,0.92)';
          ctx.fillText(n.label, n.x, n.y + rr + 18);
        }
      }

      if (staticOnly) return;
    }

    let last = performance.now();
    function loop(now) {
      if (!running) return;
      const dt = Math.min(32, now - last);
      last = now;
      if (!reduceMotion) step(dt);
      drawFrame(false);
      raf = requestAnimationFrame(loop);
    }

    function onPointerMove(e) {
      const rect = canvas.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
      pointer.active = true;
      const hit = hitTest(pointer.x, pointer.y);
      if (hit) canvas.style.cursor = 'pointer';
      else canvas.style.cursor = 'default';
    }

    function onPointerLeave() {
      pointer.active = false;
      pointer.x = -9999;
      pointer.y = -9999;
      hideTip();
      canvas.style.cursor = 'default';
    }

    function onClick(e) {
      if (canvas.dataset.sceneScrub === '1') return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const hit = hitTest(x, y);
      if (hit) showTip(hit, e.clientX, e.clientY);
      else hideTip();
    }

    function onDocClick(e) {
      if (!tipEl || tipEl.hidden) return;
      if (e.target === canvas || tipEl.contains(e.target)) return;
      hideTip();
    }

    resize();
    window.addEventListener('resize', resize);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerleave', onPointerLeave);
    canvas.addEventListener('click', onClick);
    document.addEventListener('click', onDocClick);

    if (!reduceMotion) {
      raf = requestAnimationFrame(loop);
    } else {
      drawFrame(true);
    }

    return {
      destroy() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        window.removeEventListener('resize', resize);
        canvas.removeEventListener('pointermove', onPointerMove);
        canvas.removeEventListener('pointerleave', onPointerLeave);
        canvas.removeEventListener('click', onClick);
        document.removeEventListener('click', onDocClick);
        hideTip();
      },
      resize,
    };
  }

  global.initLandingCanvas = initLandingCanvas;
})(typeof window !== 'undefined' ? window : globalThis);
