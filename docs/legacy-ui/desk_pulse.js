/**
 * Desk pulse — Simple-only live ambient line/area chart (canvas, no deps).
 * Soft teal/sage stroke + gold accent; continuous calm motion.
 * Reacts to paper pnl / mid / fills when available; ambient waveform when quiet.
 * After hours: slower cool-blue moonlit motion. Caps blooms; prefers-reduced-motion.
 */
(function (global) {
  "use strict";

  const MAX_POINTS = 96;
  const TEAL_DEF = { r: 106, g: 173, b: 200 };   // --accent (advanced / fallback)
  const SAGE_DEF = { r: 111, g: 173, b: 138 };   // --green
  const GOLD_DEF = { r: 201, g: 168, b: 108 };   // --amber
  const CORAL_DEF = { r: 201, g: 138, b: 138 };  // --red
  const MOON = { r: 120, g: 155, b: 210 };
  let TEAL = TEAL_DEF;
  let SAGE = SAGE_DEF;
  let GOLD = GOLD_DEF;
  let CORAL = CORAL_DEF;

  function refreshPalette() {
    // Chill slate only — design themes deferred
    TEAL = TEAL_DEF;
    SAGE = SAGE_DEF;
    GOLD = GOLD_DEF;
    CORAL = CORAL_DEF;
  }

  /** @type {number[]} normalized series ~0.15–0.85 */
  let series = [];
  /** @type {Array<object>} */
  let blooms = [];
  let canvas = null;
  let ctx = null;
  let shellEl = null;
  let panelEl = null;
  let captionEl = null;
  let rafId = 0;
  let running = false;
  let reduced = false;
  let pageVisible = true;
  let afterHours = false;
  let sessionLive = false;
  let lastTs = 0;
  let dpr = 1;
  let w = 0;
  let h = 0;
  let phase = 0;
  let lastPushTs = 0;
  let lastPnl = null;
  let lastMid = null;
  let lastDecisionKey = "";
  let seenFills = new Set();
  let goldSpark = 0;
  let seeded = false;
  let _lastSyncAt = 0;

  function prefersReduced() {
    try {
      return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (_) {
      return false;
    }
  }

  function isSimple() {
    return !!(document.body && document.body.classList.contains("ui-simple"));
  }

  /** One Job hides #desk-pulse-panel in Simple — never burn rAF on a display:none canvas. */
  function panelVisible() {
    if (isSimple()) return false;
    const el = panelEl || document.getElementById("desk-pulse-panel");
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

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function seedAmbient() {
    series = [];
    const n = MAX_POINTS;
    let y = 0.48;
    for (let i = 0; i < n; i++) {
      const t = i / n;
      y = clamp(
        0.48 +
          Math.sin(t * Math.PI * 2.2) * 0.06 +
          Math.sin(t * Math.PI * 5.1 + 0.4) * 0.03 +
          (Math.random() - 0.5) * 0.01,
        0.22,
        0.78
      );
      series.push(y);
    }
    seeded = true;
    lastPushTs = performance.now();
  }

  function pushPoint(y, opts) {
    opts = opts || {};
    if (!series.length) seedAmbient();
    const v = clamp(Number(y), 0.08, 0.92);
    series.push(v);
    while (series.length > MAX_POINTS) series.shift();
    if (!opts.ambient) lastPushTs = performance.now();
    if (opts.gold) goldSpark = 1;
    ensureLoop();
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

  function spawnBloom(side) {
    if (!w || !h) return;
    const buy = side === "buy";
    const sell = side === "sell";
    const color = buy ? SAGE : sell ? CORAL : TEAL;
    while (blooms.length >= (sessionLive && !afterHours ? 6 : 3)) blooms.shift();
    blooms.push({
      x: w * (0.72 + Math.random() * 0.18),
      y: h * (buy ? 0.55 : sell ? 0.42 : 0.5),
      r: reduced ? 20 : 34 + Math.random() * 18,
      life: reduced ? 1.6 : 2.6 + Math.random() * 1.3,
      age: 0,
      color,
      soft: side === "hold",
    });
    ensureLoop();
  }

  function noteFill(src) {
    if (!panelVisible()) return false;
    const key = fillKey(src);
    if (!key || seenFills.has(key)) return false;
    seenFills.add(key);
    if (seenFills.size > 300) {
      const keep = Array.from(seenFills).slice(-150);
      seenFills.clear();
      keep.forEach((k) => seenFills.add(k));
      seenFills.add(key);
    }
    let side = String(src.side || src.decision || "").toLowerCase();
    if (side !== "buy" && side !== "sell") {
      const act = String(src.action || "").toLowerCase();
      if (act.includes("buy")) side = "buy";
      else if (act.includes("sell")) side = "sell";
      else side = "hold";
    }
    const last = series.length ? series[series.length - 1] : 0.5;
    const bump = side === "buy" ? 0.045 : side === "sell" ? -0.045 : 0.008;
    pushPoint(last + bump + (Math.random() - 0.5) * 0.012, { gold: side !== "hold" });
    spawnBloom(side);
    return true;
  }

  function noteLoopEvent(e) {
    if (!panelVisible() || !e) return false;
    if (isPaperFillEvent(e)) return noteFill(e);
    const ek = String(e.event || "").toLowerCase();
    if (ek === "intent" || ek === "cancel") return false;
    // Decision / hold — calm drift nudge
    const dec = String(e.decision || e.side || e.model_side || "hold").toLowerCase();
    const key =
      (e.seq != null ? "d:" + e.seq : "") ||
      ["d", e.ticker || "", e.ts || "", dec].join("|");
    if (key && key === lastDecisionKey) return false;
    lastDecisionKey = key;
    const last = series.length ? series[series.length - 1] : 0.5;
    let mid = e.mid != null ? Number(e.mid) : e.quote && e.quote.mid != null ? Number(e.quote.mid) : null;
    let target = last;
    if (mid != null && Number.isFinite(mid) && lastMid != null && lastMid > 0) {
      const pct = (mid - lastMid) / lastMid;
      target = last + clamp(pct * 4, -0.08, 0.08);
    } else if (e.pnl_delta != null && Number.isFinite(Number(e.pnl_delta))) {
      target = last + clamp(Number(e.pnl_delta) / 80, -0.07, 0.07);
    } else if (dec === "buy") {
      target = last + 0.02;
    } else if (dec === "sell") {
      target = last - 0.02;
    } else {
      target = last + (Math.random() - 0.5) * 0.01;
    }
    if (mid != null && Number.isFinite(mid)) lastMid = mid;
    pushPoint(target, { gold: dec === "buy" || dec === "sell" });
    if (dec === "buy" || dec === "sell") spawnBloom(dec);
    else spawnBloom("hold");
    return true;
  }

  function syncFromState(data) {
    if (!panelVisible() || !data) return;
    const now = (global.performance && performance.now) ? performance.now() : Date.now();
    if (now - _lastSyncAt < 48) return;
    _lastSyncAt = now;
    if (!series.length) seedAmbient();
    const daily = data.daily || {};
    const loop = data.loop || {};
    const last = loop.last_decision || (data.decisions_preview && data.decisions_preview[0]) || null;
    const pnl = daily.pnl != null ? Number(daily.pnl) : null;

    if (pnl != null && Number.isFinite(pnl)) {
      if (lastPnl == null) {
        lastPnl = pnl;
        // Soft seed from current pnl level (map small $ to band)
        const base = 0.48 + clamp(pnl / 400, -0.18, 0.18);
        pushPoint(base, {});
      } else if (pnl !== lastPnl) {
        const delta = pnl - lastPnl;
        lastPnl = pnl;
        const prev = series[series.length - 1];
        pushPoint(prev + clamp(delta / 60, -0.09, 0.09), { gold: Math.abs(delta) > 0.5 });
      }
    }

    if (last) {
      const mid =
        last.mid != null
          ? Number(last.mid)
          : last.quote && last.quote.mid != null
            ? Number(last.quote.mid)
            : null;
      if (mid != null && Number.isFinite(mid)) {
        if (lastMid == null) {
          lastMid = mid;
        } else if (mid !== lastMid) {
          const pct = (mid - lastMid) / Math.max(1e-6, lastMid);
          lastMid = mid;
          const prev = series[series.length - 1];
          pushPoint(prev + clamp(pct * 5, -0.07, 0.07), {});
        }
      }
      // One soft hold bloom on first hydrate only
      if (!seeded) {
        /* seedAmbient already set seeded */
      }
    }

    // Ledger fills — soft note new ones
    const fills = (data.ledger && data.ledger.fills) || [];
    if (Array.isArray(fills) && fills.length) {
      fills.slice(0, 8).forEach((f) => noteFill(f));
    }
  }

  function setAfterHours(on) {
    const next = !!on;
    if (next === afterHours) {
      syncShellClass();
      updateCaption();
      return;
    }
    afterHours = next;
    syncShellClass();
    updateCaption();
    ensureLoop();
  }

  function syncShellClass() {
    if (!shellEl) return;
    shellEl.classList.toggle("after-hours", afterHours);
    shellEl.classList.toggle("ah-reduced", afterHours && reduced);
    if (panelEl) {
      panelEl.classList.toggle("after-hours", afterHours);
    }
  }

  function updateCaption() {
    if (!captionEl) return;
    captionEl.classList.toggle("is-on", afterHours);
    captionEl.setAttribute("aria-hidden", afterHours ? "false" : "true");
    if (afterHours) {
      captionEl.textContent = "After hours · soft moonlight";
    }
  }

  function setSessionLive(on) {
    const next = !!on;
    if (next === sessionLive) {
      if (shellEl) shellEl.classList.toggle("session-live", sessionLive);
      return;
    }
    sessionLive = next;
    if (shellEl) shellEl.classList.toggle("session-live", sessionLive);
    if (panelEl) panelEl.classList.toggle("session-live", sessionLive);
    resize();
    ensureLoop();
  }

  function resize() {
    if (!canvas || !shellEl) return;
    if (!isSimple()) return;
    const rect = shellEl.getBoundingClientRect();
    const cssW = Math.max(160, Math.floor(rect.width) || canvas.clientWidth || 360);
    // Batch B hierarchy: demote pulse — thinner than Day curve
    const cssH = sessionLive && !afterHours ? 72 : 64;
    dpr = Math.min(2, global.devicePixelRatio || 1);
    w = cssW;
    h = cssH;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function ambientTick(dt) {
    if (!series.length) seedAmbient();
    const now = performance.now();
    const quiet = now - lastPushTs > 900;
    const liveBoost = sessionLive && !afterHours;
    const speed = afterHours ? 0.35 : liveBoost ? 1.05 : 0.7;
    phase += dt * speed;

    if (reduced) {
      // Gentle opacity-only path handled in draw; keep series static
      return;
    }

    // Continuous subtle drift — append ambient point at calm cadence
    const last = series[series.length - 1];
    const wave =
      Math.sin(phase * 1.15) * (afterHours ? 0.018 : liveBoost ? 0.042 : 0.028) +
      Math.sin(phase * 2.7 + 1.1) * (afterHours ? 0.008 : liveBoost ? 0.018 : 0.012);
    const target = clamp(0.48 + wave + (last - 0.48) * 0.92, 0.18, 0.82);
    const interval = afterHours ? 280 : liveBoost ? 90 : 160;
    if (!ambientTick._acc) ambientTick._acc = 0;
    ambientTick._acc += dt * 1000;
    if (ambientTick._acc >= interval) {
      ambientTick._acc = 0;
      // Stronger ambient blend when quiet; preserve real spikes briefly after data
      const blend = quiet ? 0.5 : 0.14;
      pushPoint(last * (1 - blend) + target * blend, { ambient: true });
    }
    if (goldSpark > 0) goldSpark = Math.max(0, goldSpark - dt * 0.55);
  }

  function drawBlooms(dt) {
    const next = [];
    for (let i = 0; i < blooms.length; i++) {
      const b = blooms[i];
      b.age += dt;
      if (b.age >= b.life) continue;
      const u = b.age / b.life;
      let env;
      if (u < 0.15) env = u / 0.15;
      else if (u < 0.45) env = 1;
      else env = Math.max(0, 1 - (u - 0.45) / 0.55);
      env = env * env * (3 - 2 * env);
      const c = b.color;
      const alpha = (b.soft ? 0.22 : 0.42) * env;
      const rad = b.r * (0.85 + u * 0.55);
      // Soft bloom under the line
      const cy = Math.min(h * 0.78, b.y + h * 0.08);
      const grd = ctx.createRadialGradient(b.x, cy, 0, b.x, cy, rad * 1.75);
      grd.addColorStop(0, "rgba(" + c.r + "," + c.g + "," + c.b + "," + alpha + ")");
      grd.addColorStop(0.4, "rgba(" + c.r + "," + c.g + "," + c.b + "," + alpha * 0.4 + ")");
      grd.addColorStop(1, "rgba(" + c.r + "," + c.g + "," + c.b + ",0)");
      ctx.beginPath();
      ctx.fillStyle = grd;
      ctx.arc(b.x, cy, rad * 1.75, 0, Math.PI * 2);
      ctx.fill();
      next.push(b);
    }
    blooms = next;
  }

  function drawChart() {
    refreshPalette();
    if (!ctx || !w || !h || !series.length) return;
    const padX = 6;
    const padY = 10;
    const n = series.length;
    const spanX = Math.max(1, w - padX * 2);
    const spanY = Math.max(1, h - padY * 2);

    // After-hours cool wash
    if (afterHours) {
      const strength = reduced ? 0.06 : 0.04 + 0.03 * (0.5 + 0.5 * Math.sin(phase * 0.8));
      const grd = ctx.createRadialGradient(w * 0.5, h * 0.55, 0, w * 0.5, h * 0.55, Math.max(w, h) * 0.7);
      grd.addColorStop(0, "rgba(" + MOON.r + "," + MOON.g + "," + MOON.b + "," + strength * 0.5 + ")");
      grd.addColorStop(0.6, "rgba(40,55,90," + strength * 0.3 + ")");
      grd.addColorStop(1, "rgba(14,20,36,0)");
      ctx.fillStyle = grd;
      ctx.fillRect(0, 0, w, h);
    }

    function pt(i) {
      const x = padX + (i / Math.max(1, n - 1)) * spanX;
      const y = padY + (1 - series[i]) * spanY;
      return { x, y };
    }

    // Area fill
    ctx.beginPath();
    const p0 = pt(0);
    ctx.moveTo(p0.x, h - 2);
    ctx.lineTo(p0.x, p0.y);
    for (let i = 1; i < n; i++) {
      const p = pt(i);
      ctx.lineTo(p.x, p.y);
    }
    const pN = pt(n - 1);
    ctx.lineTo(pN.x, h - 2);
    ctx.closePath();

    const lineCol = afterHours ? MOON : TEAL;
    const areaTop = afterHours
      ? "rgba(" + MOON.r + "," + MOON.g + "," + MOON.b + ",0.24)"
      : "rgba(" + TEAL.r + "," + TEAL.g + "," + TEAL.b + ",0.38)";
    const areaBot = afterHours ? "rgba(40,55,90,0.02)" : "rgba(14,28,36,0.02)";
    const ag = ctx.createLinearGradient(0, padY, 0, h);
    ag.addColorStop(0, areaTop);
    ag.addColorStop(1, areaBot);
    ctx.fillStyle = ag;
    ctx.fill();

    // Soft gold accent near recent crest (when data reacted)
    if (goldSpark > 0.05 && !afterHours) {
      const gx = pN.x;
      const gy = pN.y;
      const gg = ctx.createRadialGradient(gx, gy, 0, gx, gy, 36);
      gg.addColorStop(0, "rgba(" + GOLD.r + "," + GOLD.g + "," + GOLD.b + "," + 0.28 * goldSpark + ")");
      gg.addColorStop(1, "rgba(" + GOLD.r + "," + GOLD.g + "," + GOLD.b + ",0)");
      ctx.fillStyle = gg;
      ctx.beginPath();
      ctx.arc(gx, gy, 36, 0, Math.PI * 2);
      ctx.fill();
    }

    // Primary stroke
    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    for (let i = 1; i < n; i++) {
      const p = pt(i);
      ctx.lineTo(p.x, p.y);
    }
    ctx.strokeStyle =
      "rgba(" + lineCol.r + "," + lineCol.g + "," + lineCol.b + "," + (reduced ? 0.78 : 0.94) + ")";
    ctx.lineWidth = 2.35;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
    // Soft twin accent stroke
    if (!afterHours && !reduced) {
      ctx.beginPath();
      ctx.moveTo(p0.x, p0.y + 1.1);
      for (let i = 1; i < n; i++) {
        const p = pt(i);
        ctx.lineTo(p.x, p.y + 1.1);
      }
      ctx.strokeStyle =
        "rgba(" + GOLD.r + "," + GOLD.g + "," + GOLD.b + "," + (0.28 + goldSpark * 0.25) + ")";
      ctx.lineWidth = 1.05;
      ctx.stroke();
    }

    // Gold hairline highlight on trailing third
    if (!afterHours) {
      ctx.beginPath();
      const start = Math.floor(n * 0.62);
      const ps = pt(start);
      ctx.moveTo(ps.x, ps.y);
      for (let i = start + 1; i < n; i++) {
        const p = pt(i);
        ctx.lineTo(p.x, p.y);
      }
      ctx.strokeStyle =
        "rgba(" + GOLD.r + "," + GOLD.g + "," + GOLD.b + "," + (0.32 + goldSpark * 0.4) + ")";
      ctx.lineWidth = 1.35;
      ctx.stroke();
    }


    // Soft head node
    ctx.beginPath();
    ctx.fillStyle =
      "rgba(" + lineCol.r + "," + lineCol.g + "," + lineCol.b + "," + (0.62 + goldSpark * 0.3) + ")";
    ctx.arc(pN.x, pN.y, reduced ? 2.4 : 3.5, 0, Math.PI * 2);
    ctx.fill();
  }

  function stopLoop() {
    running = false;
    lastTs = 0;
    if (rafId) {
      try { global.cancelAnimationFrame(rafId); } catch (_) {}
      rafId = 0;
    }
  }

  function paintStatic() {
    if (!ctx || !w || !h) return;
    if (!series.length) seedAmbient();
    ctx.clearRect(0, 0, w, h);
    drawChart();
  }

  function frame(ts) {
    if (!ctx || !canvas) {
      stopLoop();
      return;
    }
    if (!pageVisible || !panelVisible()) {
      // Pause when tab hidden or panel hidden (Simple One Job display:none)
      stopLoop();
      return;
    }
    if (reduced) {
      // prefers-reduced-motion: one static paint, no perpetual rAF
      paintStatic();
      stopLoop();
      return;
    }
    const dt = lastTs ? Math.min(0.05, (ts - lastTs) / 1000) : 0.016;
    lastTs = ts;

    ambientTick(dt);

    ctx.clearRect(0, 0, w, h);
    drawBlooms(dt);
    drawChart();

    // Idle stop when blooms/spark done and ambient cadence quiet? Keep ambient while Simple+visible.
    running = true;
    rafId = global.requestAnimationFrame(frame);
  }

  function ensureLoop() {
    if (running) return;
    if (!pageVisible || !panelVisible()) return;
    if (reduced) {
      paintStatic();
      return;
    }
    running = true;
    lastTs = 0;
    rafId = global.requestAnimationFrame(frame);
  }

  function onMotionChange() {
    reduced = prefersReduced();
    syncShellClass();
    if (reduced) {
      paintStatic();
      stopLoop();
    } else {
      ensureLoop();
    }
  }

  function onModeMaybe() {
    // Called after ui mode toggle — stop when Simple hides panel; run when Advanced shows it
    refreshPalette();
    if (panelVisible()) {
      resize();
      if (!series.length) seedAmbient();
      ensureLoop();
    } else {
      stopLoop();
    }
  }

  function init() {
    canvas = document.getElementById("desk-pulse-canvas");
    shellEl = document.querySelector(".desk-pulse-shell");
    panelEl = document.getElementById("desk-pulse-panel");
    captionEl = document.getElementById("desk-pulse-ah-caption");
    if (!canvas || !shellEl) return;
    ctx = canvas.getContext("2d");
    if (!ctx) return;
    reduced = prefersReduced();
    seedAmbient();
    resize();
    syncShellClass();
    updateCaption();
    if (panelVisible()) {
      if (reduced) paintStatic();
      else ensureLoop();
    } else {
      stopLoop();
    }

    if (global.ResizeObserver) {
      const ro = new ResizeObserver(() => {
        resize();
      });
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
        if (pageVisible && panelVisible() && !reduced) ensureLoop();
        else stopLoop();
      });
    } catch (_) {}
  }

  global.DeskPulse = {
    refreshPalette: refreshPalette,
    init,
    syncFromState,
    noteLoopEvent,
    noteFill,
    setAfterHours,
    setSessionLive,
    resize,
    onModeMaybe,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : globalThis);
