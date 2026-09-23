/**
 * P0.2 Equity curve + day scoreboard (Simple calm teal/gold; Advanced richer OK).
 * Server-persisted equity_curve preferred; never invents wins.
 */
(function (global) {
  "use strict";

  let TEAL = "rgba(106, 173, 200, 0.95)";
  let TEAL_SOFT = "rgba(106, 173, 200, 0.18)";
  let GOLD = "rgba(201, 168, 108, 0.9)";
  let SAGE = "rgba(111, 173, 138, 0.85)";
  let CORAL = "rgba(201, 138, 138, 0.85)";

  const RESEARCH = {
    TEAL: "rgba(106, 173, 200, 0.95)",
    TEAL_SOFT: "rgba(106, 173, 200, 0.18)",
    GOLD: "rgba(201, 168, 108, 0.9)",
    SAGE: "rgba(111, 173, 138, 0.85)",
    CORAL: "rgba(201, 138, 138, 0.85)",
  };

  const LIGHT_PALETTE = {
    TEAL: "rgba(31, 111, 147, 0.95)",
    TEAL_SOFT: "rgba(31, 111, 147, 0.14)",
    GOLD: "rgba(138, 90, 0, 0.9)",
    SAGE: "rgba(27, 122, 75, 0.9)",
    CORAL: "rgba(179, 54, 58, 0.9)",
  };

  function refreshPalette() {
    const pal = document.documentElement.getAttribute("data-theme") === "light" ? LIGHT_PALETTE : RESEARCH;
    TEAL = pal.TEAL;
    TEAL_SOFT = pal.TEAL_SOFT;
    GOLD = pal.GOLD;
    SAGE = pal.SAGE;
    CORAL = pal.CORAL;
  }

  let canvas = null;
  let ctx = null;
  let shellEl = null;
  let lastSig = "";
  /** @type {Array} */
  let lastCurve = [];
  let dpr = 1;
  let w = 0;
  let h = 0;

  function $(sel) {
    try {
      return document.querySelector(sel);
    } catch (_) {
      return null;
    }
  }

  function isSimple() {
    return !!(document.body && document.body.classList.contains("ui-simple"));
  }

  function fmtMoney(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return "—";
    const abs = Math.abs(v);
    const s = abs >= 1000 ? abs.toLocaleString(undefined, { maximumFractionDigits: 0 }) : abs.toFixed(0);
    return (v < 0 ? "−$" : "$") + s;
  }

  function fmtPct(r) {
    if (r == null || !Number.isFinite(Number(r))) return "—";
    return Math.round(Number(r) * 100) + "%";
  }

  const FIXED_H = 72;

  function resize() {
    if (!canvas || !shellEl) return;
    dpr = Math.min(2, global.devicePixelRatio || 1);
    const rect = shellEl.getBoundingClientRect();
    // Width from shell; height FIXED — never grow shell from canvas (ResizeObserver loop)
    w = Math.max(120, Math.floor(rect.width || 320));
    h = FIXED_H;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = "100%";
    canvas.style.height = FIXED_H + "px";
    ctx = canvas.getContext("2d");
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawCurve(points) {
    refreshPalette();
    if (!ctx || !w || !h) return;
    ctx.clearRect(0, 0, w, h);
    const pts = Array.isArray(points) ? points : [];
    if (pts.length < 2) {
      // calm baseline whisper
      ctx.strokeStyle = TEAL_SOFT;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(8, h * 0.55);
      ctx.quadraticCurveTo(w * 0.5, h * 0.48, w - 8, h * 0.55);
      ctx.stroke();
      return;
    }
    const vals = pts.map((p) => Number(p.equity != null ? p.equity : p.pnl));
    let lo = Math.min.apply(null, vals);
    let hi = Math.max.apply(null, vals);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
    if (Math.abs(hi - lo) < 0.5) {
      lo -= 1;
      hi += 1;
    }
    const padX = 6;
    const padY = 8;
    const span = hi - lo || 1;
    const n = vals.length;
    const xy = vals.map((v, i) => {
      const x = padX + ((w - padX * 2) * i) / Math.max(1, n - 1);
      const y = padY + (h - padY * 2) * (1 - (v - lo) / span);
      return { x, y };
    });

    // soft fill
    const last = xy[xy.length - 1];
    const first = xy[0];
    const up = last.y <= first.y;
    const stroke = up ? TEAL : CORAL;
    const fillGrad = ctx.createLinearGradient(0, 0, 0, h);
    fillGrad.addColorStop(0, up ? TEAL_SOFT : CORAL.replace("0.85","0.18").replace("0.9","0.18"));
    fillGrad.addColorStop(1, "rgba(14,20,28,0)");
    ctx.beginPath();
    ctx.moveTo(first.x, h - 2);
    ctx.lineTo(first.x, first.y);
    for (let i = 1; i < xy.length; i++) ctx.lineTo(xy[i].x, xy[i].y);
    ctx.lineTo(last.x, h - 2);
    ctx.closePath();
    ctx.fillStyle = fillGrad;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(first.x, first.y);
    for (let i = 1; i < xy.length; i++) ctx.lineTo(xy[i].x, xy[i].y);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1.75;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    // gold tip
    ctx.beginPath();
    ctx.arc(last.x, last.y, 2.6, 0, Math.PI * 2);
    ctx.fillStyle = GOLD;
    ctx.fill();
  }

  function setText(sel, text) {
    const el = $(sel);
    if (el) el.textContent = text == null ? "" : String(text);
  }

  function renderScoreboard(board) {
    const panel = $("#equity-score-panel");
    if (!panel) return;
    board = board || {};
    let curve = Array.isArray(board.equity_curve) ? board.equity_curve : [];
    // Never wipe a good curve with an empty array from a partial/exception payload
    if (curve.length >= 2) {
      lastCurve = curve.slice();
    } else if (lastCurve.length >= 2) {
      curve = lastCurve;
    }
    const sig = [
      curve.length,
      board.pnl,
      board.max_drawdown,
      board.fills_count,
      board.win_rate,
      board.butler,
      curve.length ? curve[curve.length - 1].equity : "",
    ].join("|");
    if (sig === lastSig && canvas && ctx) {
      return; // lastSig early-return — skip no-op redraw
    }
    lastSig = sig;
    setText("#eq-dd", board.max_drawdown != null ? fmtMoney(board.max_drawdown) : "—");
    setText("#eq-fills", board.fills_count != null ? String(board.fills_count) : "0");
    const wr = board.win_rate != null ? fmtPct(board.win_rate) : board.flatish_rate != null ? fmtPct(board.flatish_rate) + " calm" : "—";
    setText("#eq-win", wr);
    setText("#eq-butler", board.butler || "Quiet so far — watching the tape for you");
    if (!ctx) resize();
    drawCurve(curve);
  }

  let lastDayKey = "";
  let lastSessionKey = "";

  function reset() {
    lastCurve = [];
    lastSig = "";
    if (ctx) drawCurve([]);
  }

  function syncFromState(data) {
    if (!data) return;
    // A new trading day or a new session must not keep drawing yesterday's line.
    const dayKey = String((data.daily && data.daily.date) || "");
    const sessKey = String((data.config && data.config.session_started_at) || lastSessionKey);
    if ((dayKey && lastDayKey && dayKey !== lastDayKey) || (sessKey && lastSessionKey && sessKey !== lastSessionKey)) {
      reset();
    }
    if (dayKey) lastDayKey = dayKey;
    if (sessKey) lastSessionKey = sessKey;
    const board = data.scoreboard || {
      equity_curve: data.equity_curve || (data.ledger && data.ledger.equity_curve) || [],
      max_drawdown: 0,
      fills_count: (data.daily && data.daily.trades) || 0,
      butler: "",
      pnl: data.daily && data.daily.pnl,
    };
    const incoming = board.equity_curve;
    const fromData = data.equity_curve || (data.ledger && data.ledger.equity_curve);
    if ((!Array.isArray(incoming) || incoming.length === 0) && Array.isArray(fromData) && fromData.length) {
      board.equity_curve = fromData;
    } else if (Array.isArray(incoming) && incoming.length === 0 && lastCurve.length) {
      board.equity_curve = lastCurve;
    }
    renderScoreboard(board);
  }

  function init() {
    canvas = document.getElementById("equity-curve-canvas");
    shellEl = document.querySelector(".equity-curve-shell");
    if (!canvas || !shellEl) return;
    resize();
    drawCurve([]);
    if (global.ResizeObserver) {
      let roTimer = null;
      const ro = new ResizeObserver(() => {
        // Debounce + only react to width (height is fixed)
        if (roTimer) clearTimeout(roTimer);
        roTimer = setTimeout(() => {
          const prevW = w;
          resize();
          if (w !== prevW) {
            lastSig = ""; // force next sync to recompute sig if needed
            drawCurve(lastCurve.length ? lastCurve : []);
          }
        }, 50);
      });
      ro.observe(shellEl);
    }
  }

  function forceRedraw() {
    refreshPalette();
    lastSig = "";
    if (ctx) drawCurve(lastCurve.length ? lastCurve : []);
  }

  global.EquityScoreboard = {
    reset,
    refreshPalette: refreshPalette,
    forceRedraw: forceRedraw,
    init,
    syncFromState,
    renderScoreboard,
    resize,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : globalThis);
