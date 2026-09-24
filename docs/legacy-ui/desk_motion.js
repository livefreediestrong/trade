/**
 * Desk-wide motion helper (Simple + Advanced).
 * Chill slate teal/gold — calm accents, not casino spam.
 * Single rAF loop; pauses when tab hidden or prefers-reduced-motion.
 */
(function (global) {
  "use strict";

  const REDUCED =
    !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches);

  let rafId = 0;
  let running = false;
  let t0 = 0;
  let ambientEl = null;
  let confRing = null;
  let lastConf = 0;
  let lastSide = "hold";
  let sessionLive = false;
  let afterHours = false;
  let goalPct = 0;
  let goalNear = false;

  function $(sel) {
    try { return document.querySelector(sel); } catch (_) { return null; }
  }

  function visible() {
    return !(document.hidden || document.visibilityState === "hidden");
  }

  function ensureEls() {
    if (!ambientEl) ambientEl = $("#stage-ambient") || $(".stage-ambient");
    if (!confRing) confRing = $("#conf-ring");
  }

  function targetsVisible() {
    // rAF only when Simple ambient / conf targets are on screen
    try {
      if (!document.body || !document.body.classList.contains("ui-simple")) return false;
    } catch (_) { return false; }
    ensureEls();
    const el = ambientEl || confRing;
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

  function tick(now) {
    if (!running) return;
    if (!visible() || REDUCED || !targetsVisible()) {
      // Do not re-queue when hidden / Advanced — stop cleanly
      stop();
      return;
    }
    ensureEls();
    if (!t0) t0 = now;
    const t = (now - t0) / 1000;

    // Soft ambient drift via CSS vars (no canvas particles).
    // --ambient-breath unused in CSS — do not perpetual-write it.
    if (ambientEl) {
      const drift = (t * 6) % 48;
      ambientEl.style.setProperty("--ambient-drift", drift.toFixed(2) + "px");
    }

    // Gentle conf ring glow intensity
    if (confRing && lastConf > 0) {
      const glow = 0.55 + 0.2 * Math.sin(t * 1.1);
      confRing.style.setProperty("--ring-glow", glow.toFixed(3));
    }

    rafId = global.requestAnimationFrame(tick);
  }

  function start() {
    if (running || REDUCED) return;
    if (!visible() || !targetsVisible()) return;
    running = true;
    t0 = 0;
    rafId = global.requestAnimationFrame(tick);
  }

  function stop() {
    running = false;
    if (rafId) {
      try { global.cancelAnimationFrame(rafId); } catch (_) {}
      rafId = 0;
    }
  }

  function syncVisibility() {
    if (visible() && !REDUCED && targetsVisible()) start();
    else stop();
  }

  document.addEventListener("visibilitychange", syncVisibility);

  function setSessionMood(opts) {
    opts = opts || {};
    sessionLive = !!opts.sessionLive;
    afterHours = !!opts.afterHours;
    const body = document.body;
    if (!body) return;
    body.classList.toggle("motion-session-live", sessionLive && !afterHours);
    body.classList.toggle("motion-after-hours", afterHours);
    ensureEls();
    if (ambientEl) {
      ambientEl.classList.toggle("is-live", sessionLive && !afterHours);
      ambientEl.classList.toggle("is-ah", afterHours);
      ambientEl.classList.toggle("is-idle", !sessionLive);
    }
    // Class toggles work on Advanced without rAF; start only if Simple targets visible
    if (targetsVisible()) start();
    else stop();
  }

  /** conf 0..1, side hold|buy|sell|avoid */
  function setConfidence(conf, side) {
    const c = Math.max(0, Math.min(1, Number(conf) || 0));
    const s = String(side || "hold").toLowerCase();
    const changed = Math.abs(c - lastConf) > 0.02 || s !== lastSide;
    lastConf = c;
    lastSide = s;
    ensureEls();
    const deg = Math.round(c * 360);
    const color =
      s === "buy" ? "var(--motion-buy, #6fad8a)" :
      s === "sell" ? "var(--motion-sell, #c98a8a)" :
      s === "avoid" ? "var(--motion-gold, #c9a86c)" :
      "var(--motion-teal, #6aadc8)";
    if (confRing) {
      confRing.style.background =
        "conic-gradient(" + color + " 0deg " + deg + "deg, rgba(42,54,72,0.85) " + deg + "deg 360deg)";
      confRing.dataset.side = s;
    }
    const wrap = $("#conf-ring-wrap");
    if (wrap) wrap.dataset.side = s;
    if (changed && !REDUCED) pulseDecision(s);
  }

  function setGoalProgress(pct, near) {
    goalPct = Math.max(0, Math.min(100, Number(pct) || 0));
    goalNear = !!near || goalPct >= 80;
    const fill = $("#stage-race-fill") || $("#target-fill");
    if (fill) {
      fill.style.width = goalPct + "%";
      fill.classList.toggle("is-near", goalNear);
      fill.classList.toggle("is-shimmer", goalNear && !REDUCED);
    }
    const track = fill && fill.parentElement;
    if (track) track.classList.toggle("goal-near", goalNear);
  }

  function pulseDecision(side) {
    if (REDUCED) return;
    const big = $("#loop-big-action");
    if (big) {
      big.classList.remove("motion-flash");
      void big.offsetWidth;
      big.classList.add("motion-flash");
      clearTimeout(big._motionFlash);
      big._motionFlash = setTimeout(function () {
        big.classList.remove("motion-flash");
      }, 720);
    }
    const body = document.body;
    if (body) {
      body.classList.remove("motion-decision-buy", "motion-decision-sell", "motion-decision-hold");
      const cls =
        side === "buy" ? "motion-decision-buy" :
        side === "sell" ? "motion-decision-sell" :
        "motion-decision-hold";
      body.classList.add(cls);
      clearTimeout(body._motionDec);
      body._motionDec = setTimeout(function () {
        body.classList.remove("motion-decision-buy", "motion-decision-sell", "motion-decision-hold");
      }, 900);
    }
  }

  function pulseFill(side) {
    if (REDUCED) return;
    const body = document.body;
    if (!body) return;
    // Simple: full-viewport wash. Advanced: class drives #trade-glow-panel accent only (CSS).
    body.classList.remove("motion-fill-flash");
    void body.offsetWidth;
    body.classList.add("motion-fill-flash");
    body.dataset.fillSide = String(side || "buy").toLowerCase();
    clearTimeout(body._motionFill);
    body._motionFill = setTimeout(function () {
      body.classList.remove("motion-fill-flash");
    }, 1100);
  }

  function sparkle(el) {
    if (REDUCED || !el) return;
    el.classList.remove("lucky-sparkle");
    void el.offsetWidth;
    el.classList.add("lucky-sparkle");
    clearTimeout(el._sparkle);
    el._sparkle = setTimeout(function () {
      el.classList.remove("lucky-sparkle");
    }, 900);
  }

  function markWaitingArrive(nodes) {
    if (REDUCED || !nodes) return;
    const list = Array.isArray(nodes) ? nodes : [nodes];
    list.forEach(function (n) {
      if (!n || !n.classList) return;
      n.classList.add("motion-arrive");
      clearTimeout(n._arrive);
      n._arrive = setTimeout(function () {
        n.classList.remove("motion-arrive");
      }, 800);
    });
  }

  // Boot — rAF only when Simple ambient/conf targets are visible
  if (!REDUCED) {
    try { syncVisibility(); } catch (_) {}
  }

  global.DeskMotion = {
    setSessionMood: setSessionMood,
    setConfidence: setConfidence,
    setGoalProgress: setGoalProgress,
    pulseDecision: pulseDecision,
    pulseFill: pulseFill,
    sparkle: sparkle,
    markWaitingArrive: markWaitingArrive,
    start: start,
    stop: stop,
    reduced: REDUCED,
  };
})(typeof window !== "undefined" ? window : globalThis);
