/**
 * Trade glow — calm abstract paper-fill visualization (canvas, no deps).
 * Buy = sage bloom drifting up/right; sell = coral bloom drifting down/left.
 * Outside RTH: slower moonlit dust, gentle breathing pulse, desk-resting caption.
 * Caps ~20 particles; respects prefers-reduced-motion.
 */
(function (global) {
  "use strict";

  const MAX_PARTICLES = 20;
  const SAGE_DEF = { r: 111, g: 173, b: 138 };   // --green
  const CORAL_DEF = { r: 201, g: 138, b: 138 }; // --red
  const MOON = { r: 120, g: 155, b: 210 };  // after-hours ambient
  let SAGE = SAGE_DEF;
  let CORAL = CORAL_DEF;
  const AMBIENT_TEAL_DEF = { r: 106, g: 173, b: 200 };
  let AMBIENT_TEAL = AMBIENT_TEAL_DEF;

  function refreshPalette() {
    // Chill slate only — design themes deferred
    SAGE = SAGE_DEF;
    CORAL = CORAL_DEF;
    AMBIENT_TEAL = AMBIENT_TEAL_DEF;
  }

  const AMBIENT_N = 5;
  const AMBIENT_N_AH = 7;

  const seen = new Set();
  /** @type {Array<object>} */
  let particles = [];
  /** @type {Array<object>} */
  let ambient = [];
  let canvas = null;
  let ctx = null;
  let emptyEl = null;
  let captionEl = null;
  let shellEl = null;
  let rafId = 0;
  let running = false;
  let reduced = false;
  let pageVisible = true;
  let afterHours = false;
  let simpleIdle = false;
  let lastTs = 0;
  let seededLedger = false;
  let dpr = 1;
  let w = 0;
  let h = 0;
  let breathPhase = 0;

  function prefersReduced() {
    try {
      return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (_) {
      return false;
    }
  }

  function fillKey(src) {
    if (!src || typeof src !== "object") return "";
    if (src.seq != null && src.seq !== "") return "seq:" + String(src.seq);
    if (src.id) return "id:" + String(src.id);
    if (src.fill && src.fill.id) return "id:" + String(src.fill.id);
    const side = String(src.side || src.decision || "").toLowerCase();
    const t = src.ticker || "";
    const sh = (src.fill && src.fill.shares) != null ? src.fill.shares : src.shares;
    const px = (src.fill && src.fill.price) != null ? src.fill.price : src.price;
    const ts = src.ts || (src.fill && src.fill.ts) || "";
    return ["k", t, side, sh, px, ts].join("|");
  }

  function isPaperFillEvent(e) {
    if (!e || typeof e !== "object") return false;
    const k = String(e.event || "").toLowerCase();
    if (k === "fill") return true;
    if (k === "intent" || k === "cancel") return false;
    return !!(e.filled && e.fill);
  }

  function normalizeFill(src) {
    const fill = src.fill && typeof src.fill === "object" ? src.fill : null;
    let side = String(src.side || src.decision || (fill && fill.side) || "").toLowerCase();
    if (side !== "buy" && side !== "sell") {
      const act = String(src.action || "").toLowerCase();
      if (act.includes("buy")) side = "buy";
      else if (act.includes("sell")) side = "sell";
      else return null;
    }
    const shares = Number(
      (fill && fill.shares != null ? fill.shares : src.shares) != null
        ? fill && fill.shares != null
          ? fill.shares
          : src.shares
        : 0
    );
    const price = Number(
      fill && fill.price != null ? fill.price : src.price != null ? src.price : 0
    );
    const notional = Number(
      fill && fill.notional != null
        ? fill.notional
        : src.notional != null
          ? src.notional
          : shares * price
    );
    return {
      side,
      shares: Number.isFinite(shares) ? shares : 0,
      notional: Number.isFinite(notional) ? notional : 0,
      ticker: String(src.ticker || (fill && fill.ticker) || ""),
      key: fillKey(src),
    };
  }

  function sizeScale(shares, notional) {
    const s = Math.max(0, shares || 0);
    const n = Math.max(0, notional || 0);
    // Mild scale: base 1, up to ~1.65
    const fromShares = Math.min(1.4, 0.85 + Math.log10(s + 1) * 0.35);
    const fromNotional = Math.min(1.5, 0.9 + Math.log10(n / 500 + 1) * 0.28);
    return Math.max(0.85, Math.min(1.65, (fromShares + fromNotional) / 2));
  }

  function spawnBloom(norm, opts) {
    refreshPalette();
    if (!canvas || !norm) return;
    opts = opts || {};
    const soft = !!opts.soft;
    const buy = norm.side === "buy";
    const scale = sizeScale(norm.shares, norm.notional);
    const color = buy ? SAGE : CORAL;
    const cx = w * (0.18 + Math.random() * 0.64);
    const cy = h * (buy ? 0.55 + Math.random() * 0.28 : 0.18 + Math.random() * 0.28);
    const simple = document.body && document.body.classList.contains("ui-simple");
    const punch = simple && !soft ? 1.48 : 1;
    const life = reduced
      ? (soft ? 2.8 : 3.6)
      : soft
        ? 5.5 + Math.random() * 2
        : (simple ? 5.6 : 4.2) + Math.random() * (simple ? 3.0 : 2.4);
    const baseR = (reduced ? 10 : 14) * scale * (soft ? 0.75 : 1) * punch;

    while (particles.length >= MAX_PARTICLES) {
      particles.shift();
    }

    particles.push({
      x: cx,
      y: cy,
      vx: reduced ? 0 : buy ? 6 + Math.random() * 10 : -(6 + Math.random() * 10),
      vy: reduced ? 0 : buy ? -(8 + Math.random() * 12) : 8 + Math.random() * 12,
      r: baseR,
      rPeak: baseR * (1.15 + Math.random() * 0.35),
      life,
      age: 0,
      color,
      soft,
      ticker: norm.ticker,
      ripples: reduced
        ? []
        : [
            { delay: 0.15, life: 2.2 },
            { delay: 0.55, life: 2.6 },
          ],
    });
    updateEmpty();
    ensureLoop();
  }

  function noteFill(src, opts) {
    const norm = normalizeFill(src);
    if (!norm || !norm.key) return false;
    if (seen.has(norm.key)) return false;
    seen.add(norm.key);
    if (seen.size > 400) {
      // trim oldest-ish by recreating from recent particle keys only — keep set bounded
      const keep = Array.from(seen).slice(-200);
      seen.clear();
      keep.forEach((k) => seen.add(k));
      seen.add(norm.key);
    }
    spawnBloom(norm, opts);
    return true;
  }

  function noteLoopEvent(e) {
    if (!isPaperFillEvent(e)) return false;
    return noteFill(e, { soft: false });
  }

  function syncLedger(fills) {
    if (!Array.isArray(fills) || !fills.length) return;
    // First hydrate: soft-seed a few recent fills (no casino burst). Later: only new ones.
    const list = fills.slice(0, 12);
    if (!seededLedger) {
      seededLedger = true;
      const recent = list.slice(0, 5).reverse();
      recent.forEach((f) => noteFill(f, { soft: true }));
      return;
    }
    // Newest-first ledger: note unseen from the front
    for (let i = 0; i < list.length; i++) {
      noteFill(list[i], { soft: false });
    }
  }

  function updateEmpty() {
    if (!emptyEl) return;
    const quiet = particles.length === 0;
    // During after-hours, caption owns the quiet message; hide default empty copy
    const hideEmpty = !quiet || afterHours;
    emptyEl.classList.toggle("is-hidden", hideEmpty);
    emptyEl.setAttribute("aria-hidden", hideEmpty ? "true" : "false");
    // Batch B: no idle shimmer; hide shell when empty (panel keeps quiet empty copy)
    if (shellEl) {
      shellEl.classList.remove("simple-idle-shimmer");
      shellEl.classList.toggle("is-empty-shell", quiet && !afterHours);
      shellEl.setAttribute("aria-hidden", quiet && !afterHours ? "true" : "false");
    }
    const panel = document.getElementById("trade-glow-panel");
    if (panel) {
      panel.classList.toggle("is-empty", quiet && !afterHours);
    }
    if (!afterHours && quiet) {
      const simple = document.body && document.body.classList.contains("ui-simple");
      emptyEl.textContent = simple ? "Fills land here." : "Fills will glow here.";
    }
  }

  function updateCaption() {
    if (!captionEl) return;
    captionEl.classList.toggle("is-on", afterHours);
    captionEl.setAttribute("aria-hidden", afterHours ? "false" : "true");
    if (afterHours) {
      const simple = document.body && document.body.classList.contains("ui-simple");
      captionEl.textContent = simple ? "After hours · soft moonlight" : "After hours · desk resting";
    }
  }

  function syncShellClass() {
    if (!shellEl) return;
    shellEl.classList.toggle("after-hours", afterHours);
    shellEl.classList.toggle("ah-reduced", afterHours && reduced);
  }

  function buildAmbient() {
    ambient = [];
    if (reduced || !w) return;
    const simple = document.body && document.body.classList.contains("ui-simple");
    // Quieter idle ambient dust
    const n = afterHours ? AMBIENT_N_AH : simple ? Math.min(AMBIENT_N + 3, 9) : AMBIENT_N;
    for (let i = 0; i < n; i++) {
      if (afterHours) {
        ambient.push({
          x: Math.random() * w,
          y: Math.random() * h,
          r: 1.4 + Math.random() * 2.8,
          phase: Math.random() * Math.PI * 2,
          speed: 0.035 + Math.random() * 0.055,
          driftX: (Math.random() - 0.5) * 1.6,
          driftY: (Math.random() - 0.5) * 1.1,
          a: 0.055 + Math.random() * 0.07,
          moon: true,
        });
      } else {
        ambient.push({
          x: Math.random() * w,
          y: Math.random() * h,
          r: (simple ? 1.9 : 1.2) + Math.random() * (simple ? 3.2 : 2.2),
          phase: Math.random() * Math.PI * 2,
          speed: (simple ? 0.04 : 0.08) + Math.random() * (simple ? 0.07 : 0.12),
          driftX: (Math.random() - 0.5) * (simple ? 2.0 : 4),
          driftY: (Math.random() - 0.5) * (simple ? 1.4 : 3),
          a: (simple ? 0.09 : 0.04) + Math.random() * (simple ? 0.1 : 0.05),
          moon: false,
        });
      }
    }
  }

  /** One Job hides #trade-glow-panel in Simple — stop rAF when not shown. */
  function panelVisible() {
    try {
      if (document.body && document.body.classList.contains("ui-simple")) return false;
    } catch (_) { return false; }
    const el = document.getElementById("trade-glow-panel");
    if (!el) return false;
    try {
      const st = global.getComputedStyle(el);
      if (st.display === "none" || st.visibility === "hidden") return false;
    } catch (_) {}
    try {
      if (typeof el.checkVisibility === "function") return el.checkVisibility();
    } catch (_) {}
    return el.getClientRects().length > 0;
  }

    function setSimpleIdle(on) {
    const next = !!on;
    if (next === simpleIdle) {
      updateEmpty();
      if (!panelVisible()) stopLoop();
      return;
    }
    simpleIdle = next;
    // No idle shimmer — keep shell quiet when empty
    updateEmpty();
    buildAmbient();
    if (panelVisible()) ensureLoop();
    else stopLoop();
  }

  function setAfterHours(on) {
    const next = !!on;
    if (next === afterHours) {
      syncShellClass();
      updateCaption();
      return;
    }
    afterHours = next;
    breathPhase = 0;
    syncShellClass();
    updateCaption();
    updateEmpty();
    buildAmbient();
    ensureLoop();
  }

  function resize() {
    if (!canvas || !shellEl) return;
    const rect = shellEl.getBoundingClientRect();
    const cssW = Math.max(120, Math.floor(rect.width) || canvas.clientWidth || 320);
    const simple =
      document.body && document.body.classList.contains("ui-simple");
    const cssH = simple ? 96 : 108;
    dpr = Math.min(2, global.devicePixelRatio || 1);
    w = cssW;
    h = cssH;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildAmbient();
  }

  function drawRipple(p, rip, t) {
    const local = t - rip.delay;
    if (local < 0 || local > rip.life) return;
    const u = local / rip.life;
    const ease = 1 - Math.pow(1 - u, 2);
    const simpleRip = document.body && document.body.classList.contains("ui-simple");
    const rr = p.r * (0.6 + ease * (simpleRip ? 2.8 : 2.4));
    const alpha = (1 - u) * (simpleRip ? 0.3 : 0.22);
    const c = p.color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, rr, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(" + c.r + "," + c.g + "," + c.b + "," + alpha + ")";
    ctx.lineWidth = simpleRip ? 1.55 : 1.25;
    ctx.stroke();
  }

  function drawParticle(p) {
    const u = Math.min(1, p.age / p.life);
    // Soft bloom envelope: ease in, hold, fade
    let env;
    if (u < 0.12) env = u / 0.12;
    else if (u < 0.55) env = 1;
    else env = Math.max(0, 1 - (u - 0.55) / 0.45);
    env = env * env * (3 - 2 * env); // smoothstep-ish
    const c = p.color;
    const radius = p.r + (p.rPeak - p.r) * Math.min(1, u * 1.4);
    const simpleDraw = document.body && document.body.classList.contains("ui-simple");
    const alpha = (p.soft ? 0.3 : simpleDraw ? 0.7 : 0.42) * env;

    const grd = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius * 1.95);
    grd.addColorStop(0, "rgba(" + c.r + "," + c.g + "," + c.b + "," + (alpha * 0.98) + ")");
    grd.addColorStop(0.32, "rgba(" + c.r + "," + c.g + "," + c.b + "," + (alpha * 0.4) + ")");
    grd.addColorStop(1, "rgba(" + c.r + "," + c.g + "," + c.b + ",0)");
    ctx.beginPath();
    ctx.fillStyle = grd;
    ctx.arc(p.x, p.y, radius * 1.95, 0, Math.PI * 2);
    ctx.fill();

    // Soft core node
    ctx.beginPath();
    ctx.fillStyle = "rgba(" + c.r + "," + c.g + "," + c.b + "," + (alpha * 0.62) + ")";
    ctx.arc(p.x, p.y, Math.max(1.6, radius * 0.24), 0, Math.PI * 2);
    ctx.fill();


    if (p.ripples && p.ripples.length) {
      p.ripples.forEach((rip) => drawRipple(p, rip, p.age));
    }
  }

  function drawBreath() {
    if (!afterHours || !w || !h) return;
    // Reduced: static dusk wash only (no pulse)
    const strength = reduced
      ? 0.07
      : 0.045 + 0.035 * (0.5 + 0.5 * Math.sin(breathPhase));
    const cx = w * 0.5;
    const cy = h * 0.55;
    const rad = Math.max(w, h) * 0.72;
    const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
    grd.addColorStop(0, "rgba(" + MOON.r + "," + MOON.g + "," + MOON.b + "," + (strength * 0.55) + ")");
    grd.addColorStop(0.55, "rgba(40, 55, 90," + (strength * 0.35) + ")");
    grd.addColorStop(1, "rgba(14, 20, 36,0)");
    ctx.fillStyle = grd;
    ctx.fillRect(0, 0, w, h);
  }

  function frame(ts) {
    refreshPalette();
    if (!ctx || !canvas) {
      running = false;
      return;
    }
    if (!pageVisible || !panelVisible()) {
      stopLoop();
      return;
    }
    const dt = lastTs ? Math.min(0.05, (ts - lastTs) / 1000) : 0.016;
    lastTs = ts;

    ctx.clearRect(0, 0, w, h);

    // After-hours breathing wash (under dust)
    if (afterHours) {
      if (!reduced) breathPhase += dt * 0.55;
      drawBreath();
    }

    // Very slow ambient drift (constellation dust / moonlit after hours)
    if (!reduced && ambient.length) {
      for (let i = 0; i < ambient.length; i++) {
        const a = ambient[i];
        a.phase += a.speed * dt;
        a.x += a.driftX * dt;
        a.y += a.driftY * dt;
        if (a.x < -4) a.x = w + 4;
        if (a.x > w + 4) a.x = -4;
        if (a.y < -4) a.y = h + 4;
        if (a.y > h + 4) a.y = -4;
        const tw = a.a * (0.65 + 0.35 * Math.sin(a.phase));
        const col = a.moon ? MOON : AMBIENT_TEAL;
        ctx.beginPath();
        ctx.fillStyle = "rgba(" + col.r + "," + col.g + "," + col.b + "," + tw + ")";
        ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    const next = [];
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      p.age += dt;
      if (!reduced) {
        // Slow easing drift + gentle damp (slower when after hours)
        const damp = afterHours ? 0.988 : 0.992;
        const slow = afterHours ? 0.55 : 1;
        p.vx *= damp;
        p.vy *= damp;
        p.x += p.vx * dt * slow;
        p.y += p.vy * dt * slow;
      }
      if (p.age < p.life) {
        drawParticle(p);
        next.push(p);
      }
    }
    particles = next;
    updateEmpty();

    // Idle stop: only keep rAF while panel visible and particles/AH ambient alive.
    // Simple One Job hides this panel — never keep ambient rAF for display:none.
    const need =
      pageVisible &&
      panelVisible() &&
      !reduced &&
      (particles.length > 0 || (afterHours && ambient.length > 0));
    if (need) {
      rafId = global.requestAnimationFrame(frame);
    } else {
      running = false;
      lastTs = 0;
      rafId = 0;
    }
  }

  function ensureLoop() {
    if (running) return;
    if (reduced || !pageVisible || !panelVisible()) {
      if (panelVisible()) paintStatic();
      else stopLoop();
      return;
    }
    // Nothing to animate → static paint, no perpetual rAF
    if (!particles.length && !(afterHours && ambient.length)) {
      paintStatic();
      return;
    }
    running = true;
    lastTs = 0;
    rafId = global.requestAnimationFrame(frame);
  }

  function paintStatic() {
    if (!ctx || !canvas) return;
    ctx.clearRect(0, 0, w, h);
    if (afterHours) drawBreath();
    // Static ambient dust snapshot (Simple idle shimmer / reduced motion)
    if (ambient.length) {
      for (let i = 0; i < ambient.length; i++) {
        const a = ambient[i];
        const col = a.moon ? MOON : AMBIENT_TEAL;
        ctx.beginPath();
        ctx.fillStyle = "rgba(" + col.r + "," + col.g + "," + col.b + "," + (a.a * 0.7) + ")";
        ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    for (let i = 0; i < particles.length; i++) drawParticle(particles[i]);
  }

  function stopLoop() {
    running = false;
    lastTs = 0;
    if (rafId) {
      try { global.cancelAnimationFrame(rafId); } catch (_) {}
      rafId = 0;
    }
  }

  function onMotionChange() {
    reduced = prefersReduced();
    if (reduced) {
      ambient = [];
      particles.forEach((p) => {
        p.vx = 0;
        p.vy = 0;
        p.ripples = [];
      });
      syncShellClass();
      paintStatic();
      stopLoop();
      return;
    }
    buildAmbient();
    syncShellClass();
    ensureLoop();
  }

  function init() {
    canvas = document.getElementById("trade-glow-canvas");
    emptyEl = document.getElementById("trade-glow-empty");
    captionEl = document.getElementById("trade-glow-ah-caption");
    shellEl = document.querySelector(".trade-glow-shell");
    if (!canvas || !shellEl) return;
    ctx = canvas.getContext("2d");
    if (!ctx) return;
    reduced = prefersReduced();
    resize();
    updateEmpty();
    updateCaption();
    syncShellClass();
    if (reduced) paintStatic();
    else ensureLoop();

    if (global.ResizeObserver) {
      const ro = new ResizeObserver(() => resize());
      ro.observe(shellEl);
    } else {
      global.addEventListener("resize", resize);
    }

    try {
      const mq = global.matchMedia("(prefers-reduced-motion: reduce)");
      if (mq) {
        const handler = () => onMotionChange();
        if (mq.addEventListener) mq.addEventListener("change", handler);
        else if (mq.addListener) mq.addListener(handler);
      }
    } catch (_) {}

    try {
      document.addEventListener("visibilitychange", () => {
        pageVisible = document.visibilityState !== "hidden";
        if (pageVisible) ensureLoop();
        else stopLoop();
      });
    } catch (_) {}
  }

  global.TradeGlow = {
    refreshPalette: refreshPalette,
    init,
    noteFill,
    noteLoopEvent,
    syncLedger,
    isPaperFillEvent,
    setAfterHours,
    setSimpleIdle,
    resize,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : globalThis);
