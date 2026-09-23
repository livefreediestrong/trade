/* Tomahawk - vanilla JS */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  let state = null;
  let activeTab = "pending";
  let toastTimer = null;
  let pendingApproveId = null;
  let prevQualifySet = new Set();
  let oppPrevTickers = new Set();
  let oppRankMap = {};
  let focusTicker = null;
  let sseLive = false;
  let sseEs = null;
  let sseRetryMs = 2000;
  let statePollMs = 8000;
  let statePollTimer = null;
  let loopPollTimer = null;
  let sparkTimer = null;
  let alertSeen = new Map(); // key -> ts (event-set or minute-bucket)
  let deskAlertSeenIds = new Set(); // state.alerts item ids (session memory)
  let deskAlertsBaselineReady = false;
  let prevBuzzHitTickers = new Set();
  let prevTargetHit = false;
  let prevKillSkip = null;
  let alertBaselineReady = false;
  let lastOppEventSet = ""; // sorted ticker:verdict ids
  let pollErrToasts = 0;
  const POLL_ERR_TOAST_CAP = 2;
  let sseReconnectTimer = null;
  let fullStateSeen = false;
  let lastHeroPnl = null;
  let lastBigActionKey = null;
  let lastFeedNewestSeq = null;
  let sessionWasActive = false;
  let toastHideTimer = null;
  let prevHeatTickers = new Set();
  let lastSparkKey = "";
  let lastOppSig = "";
  let lastFeedSig = "";
  let lastPosSig = "";
  let lastPendingCount = 0;
  let lastChurnSig = "";
  let oppEmptyTimer = null; // hysteresis: delay empty paint on N→0
  let lastHeatSig = "";
  let stickyResizeTimer = null;
  let prevBuzzScores = new Map(); // ticker -> score for client spike gate
  let buzzSpikeCooldown = new Map(); // ticker -> last alert ts
  const BUZZ_SPIKE_ABS = 3;
  const BUZZ_SPIKE_REL = 0.5;
  const BUZZ_SPIKE_COOLDOWN_MS = 180000; // 3 min


  /** Simple-mode butler gloss for latest-call kinds (Hold/Buy/Sell/flat/none). */
  function callGloss(kind) {
    const k = String(kind == null ? "" : kind).toLowerCase().trim();
    if (k === "buy" || k === "long" || k === "buying") return "Paper buy for you";
    if (k === "sell" || k === "short" || k === "selling") return "Paper sell for you";
    if (k === "none" || k === "nocall" || k === "no_call" || k === "no-call") {
      return "No call yet — Start when you wish";
    }
    if (k === "hold" || k === "flat" || k === "holding" || !k) return "Holding — no trade";
    return "Holding — no trade";
  }

  /** Simple-mode butler gloss for horizon outcomes (helped/hurt/flat). */
  function outcomeGloss(label, ticker) {
    const L = String(label == null ? "" : label).toLowerCase();
    const t = ticker ? String(ticker).toUpperCase() + " " : "";
    if (L === "helped") return t + "call helped over the horizon — paper only";
    if (L === "hurt") return t + "call hurt over the horizon — noted, no drama";
    if (L === "flat") return t + "call was flat-ish — nothing loud";
    return "";
  }

  /** Simple-mode butler gloss for research verdicts (PASS/WATCH/AVOID). */
  function verdictGloss(v) {
    const u = String(v == null ? "" : v).toUpperCase().trim();
    if (u === "PASS") return "Worth a look";
    if (u === "WATCH") return "Not ready yet";
    if (u === "AVOID") return "Skip this one";
    return "";
  }

  function timingGloss(v) {
    const l = String(v == null ? "" : v).toLowerCase().trim();
    if (l === "late") return "Late — the price already moved";
    if (l === "chasing") return "Chasing — the price already jumped";
    if (l === "early") return "Early";
    if (l === "fair" || l === "good" || l === "ok") return "On time";
    return l ? l[0].toUpperCase() + l.slice(1) : "";
  }

  /** "How sure" in words + a count people can picture (0.75 → "fairly sure (about 3 in 4)"). */
  function surePhrase(conf) {
    const c = Number(conf);
    if (!Number.isFinite(c) || c <= 0) return "";
    const word = c < 0.4 ? "not very sure" : c < 0.7 ? "somewhat sure" : "fairly sure";
    const tenths = Math.max(1, Math.min(10, Math.round(c * 10)));
    const frac = tenths === 5 ? "about 1 in 2" : tenths === 10 ? "nearly certain" : `about ${tenths} in 10`;
    return `${word} (${frac})`;
  }

  /** Replace trading jargon in AI-written text shown in Simple mode. */
  function plainify(text) {
    let t = String(text || "");
    const map = [
      [/\bmulti-timeframe\b/gi, "short- and long-term"],
      [/\babove all major SMAs\b/gi, "above its recent average prices"],
      [/\bbelow all major SMAs\b/gi, "below its recent average prices"],
      [/\bSMA\s?(\d+)\b/gi, "$1-day average price"],
      [/\bSMAs?\b/gi, "average prices"],
      [/\bVWAP\b/g, "the day's average trade price"],
      [/\brelative volume \(([\d.]+)x\)/gi, "trading volume ($1× a normal day)"],
      [/\brel(?:ative)?[ _]vol(?:ume)?\b/gi, "trading volume vs. normal"],
      [/\bRSI\b/g, "momentum score"],
      [/\bCMF\b/g, "money-flow score"],
      [/\bsector tailwinds? from ([A-Z]{2,4}) \(([-+]?[\d.]+%)\)/g, "its industry is up today ($2)"],
      [/\bthe 100% mark of its 30-day range\b/gi, "the top of its 30-day price range"],
      [/\bRTH\b/g, "market hours"],
      [/\bbps\b/g, "hundredths of a percent"],
    ];
    for (const [re, rep] of map) t = t.replace(re, rep);
    return t;
  }

  function getUiMode() {
    try {
      return localStorage.getItem("ui_mode") === "advanced" ? "advanced" : "simple";
    } catch {
      return "simple";
    }
  }
  function syncStickyOffsets() {
    const chrome = $("#paper-chrome");
    if (!chrome) return;
    const h = Math.ceil(chrome.getBoundingClientRect().height) || 36;
    document.documentElement.style.setProperty("--masthead-h", `${h}px`);
    // Dual sticky: stage sits below sticky session toolbar in Simple.
    // Measure toolbar so One Job stage top clears it (toolbar stays z-55 hero controls).
    const toolbar = $("#session-toolbar");
    let th = 0;
    const simple = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
    if (simple && toolbar) {
      th = Math.ceil(toolbar.getBoundingClientRect().height) || 0;
    }
    document.documentElement.style.setProperty("--session-toolbar-h", `${th}px`);
  }


  function syncSimpleAmbient(data) {
    const body = document.body;
    if (!body) return;
    const cfg = (data && data.config) || {};
    const loop = (data && data.loop) || {};
    const outside = isOutsideRth(loop, cfg);
    const active = !!cfg.session_active;
    const simple = getUiMode() === "simple" || body.classList.contains("ui-simple");
    // Moonlit after-hours wash (Simple); Advanced keeps default chrome
    body.classList.toggle("ah-moonlit", simple && outside);
    // Teal breath while session on during RTH
    body.classList.toggle("session-live", simple && active && !outside);
    // Start button mirror class for CSS hooks
    const startBtn = $("#btn-start-session");
    if (startBtn) {
      startBtn.classList.toggle("session-live", simple && active && !outside);
    }
    try {
      if (window.DeskPulse && typeof window.DeskPulse.setSessionLive === "function") {
        window.DeskPulse.setSessionLive(simple && active && !outside);
      }
    } catch (_) {}
    try {
      if (window.TradeGlow && typeof window.TradeGlow.setSimpleIdle === "function") {
        window.TradeGlow.setSimpleIdle(simple);
      }
    } catch (_) {}
    try {
      if (window.DeskMotion && typeof window.DeskMotion.setSessionMood === "function") {
        window.DeskMotion.setSessionMood({
          sessionLive: active && !outside,
          afterHours: outside,
        });
      }
    } catch (_) {}
    syncButlerStory(data);
    try { syncStageGoalRace(data); } catch (_) {}
  }

  let _butlerStoryKey = "";
  let _butlerFillUntil = 0;
  let _lastFillStoryKey = "";
  let _luckyLine = "";
  let _luckyUntil = 0;
  let _luckyTicker = "";

  /** One calm rotating caption — Simple presence, not research chrome.
   *  Text-cap: gloss XOR butler-story (call stage shows at most one of them). */
  function syncButlerStory(data) {
    const el = $("#butler-story");
    if (!el) return;
    const simple = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
    if (!simple) {
      el.textContent = "";
      el.hidden = true;
      return;
    }
    // Prefer call gloss when a decision is on stage; butler only for idle/session presence
    const glossEl = $("#loop-action-gloss");
    const glossOn = !!(glossEl && !glossEl.hidden && glossEl.textContent && !glossEl.classList.contains("is-idle"));
    if (glossOn) {
      el.textContent = "";
      el.hidden = true;
      // Header butler still speaks blockers / session status
      try {
        const hdr = $("#butler-header-status");
        if (hdr) {
          const cfg2 = (data && data.config) || {};
          const loop2 = (data && data.loop) || {};
          const outside2 = isOutsideRth(loop2, cfg2);
          const active2 = !!cfg2.session_active;
          let hline = "Session on — watching watchlist headlines for you";
          if (_luckyLine && Date.now() < _luckyUntil) hline = _luckyLine;
          else if (!active2) hline = "Not checking — press Start checking when ready";
          else if (outside2) hline = (loop2.rth_only !== false) ? "Market closed — checks pause until the next open (US hours)" : "After hours — soft moonlight";
          hdr.textContent = hline;
        }
      } catch (_) {}
      return;
    }
    el.hidden = false;
    const cfg = (data && data.config) || {};
    const loop = (data && data.loop) || {};
    const outside = isOutsideRth(loop, cfg);
    const active = !!cfg.session_active;
    const pending = Number(
      data && data.pending_count != null
        ? data.pending_count
        : (data && data.opportunities
          ? data.opportunities.filter((o) => o && o.source === "pending").length
          : 0)
    );
    const radar = (data && data.radar) || {};
    const radarOn = !!(data && (data.radar_enabled || cfg.radar_enabled || radar.enabled));
    const radarCount = radarOn
      ? (radar.count != null ? Number(radar.count) : (radar.movers || []).length)
      : 0;
    const last = loop.last_decision || (data.decisions_preview && data.decisions_preview[0]) || null;
    const now = Date.now();

    // Brief fill toast line — sticky a few seconds without flicker
    try {
      const fills = (data && data.ledger && data.ledger.fills) || [];
      const f0 = fills[0];
      if (f0) {
        const fk = String(f0.id || f0.seq || "") + "|" + String(f0.ts || "") + "|" + String(f0.ticker || "");
        if (fk && fk !== _lastFillStoryKey) {
          _lastFillStoryKey = fk;
          _butlerFillUntil = now + 4200;
        }
      }
    } catch (_) {}

    let line = "Standing by";
    let key = "idle";
    const skip = loop.last_skip || "";
    const rthOnly = loop.rth_only !== false;
    if (now < _butlerFillUntil) {
      line = "A fill just kissed the ledger";
      key = "fill";
    } else if (!active || skip === "session_inactive") {
      line = "Not checking — press Start checking when ready";
      key = "off";
    } else if (outside || skip === "outside_rth") {
      line = rthOnly
        ? "Market closed — checks pause until the next open (US hours)"
        : (active
          ? "After hours — soft moonlight on the desk"
          : "Market closed — moonlight keeps watch");
      key = "ah|" + (rthOnly ? "rth" : "1");
    } else if (skip === "max_loss") {
      line = "Paused — max loss reached for today";
      key = "maxloss";
    } else if (skip === "target_hit") {
      line = "Goal hit — session paused for you";
      key = "goal";
    } else if (skip === "empty_watchlist") {
      line = "No tickers on the watchlist yet";
      key = "emptywl";
    } else if (pending > 0) {
      line = pending === 1
        ? "1 idea waiting for you"
        : pending + " ideas waiting for you";
      key = "pend|" + pending;
    } else if (active) {
      const scanning = !!(loop.running || loop.scanning || loop.last_tick_ts || last);
      if (scanning) {
        line = "Session on — watching watchlist headlines for you";
        key = "scan";
      } else {
        line = "Session live — standing ready";
        key = "live";
      }
    } else if (radarOn && radarCount > 0) {
      line = "Radar whispers " + radarCount + " movers";
      key = "radar|" + radarCount;
    }

    // Feeling Lucky sticky line (brief)
    if (_luckyLine && now < _luckyUntil) {
      line = _luckyLine;
      key = "lucky|" + (_luckyTicker || "");
    }

    if (key === _butlerStoryKey && el.textContent === line) {
      const hdr = $("#butler-header-status");
      if (hdr && hdr.textContent !== line) hdr.textContent = line;
      return;
    }
    _butlerStoryKey = key;
    el.textContent = line;
    const hdr = $("#butler-header-status");
    if (hdr) hdr.textContent = line;
  }

  function setUiMode(mode) {
    mode = mode === "advanced" ? "advanced" : "simple";
    const prev = getUiMode();
    try { localStorage.setItem("ui_mode", mode); } catch (_) {}
    document.body.classList.toggle("ui-simple", mode === "simple");
    document.body.classList.toggle("ui-advanced", mode === "advanced");
    // Mode only changes IA density (themes deferred).
    try {
      if (window.EquityScoreboard) {
        if (typeof window.EquityScoreboard.forceRedraw === "function") {
          window.EquityScoreboard.forceRedraw();
        } else if (typeof window.EquityScoreboard.refreshPalette === "function") {
          window.EquityScoreboard.refreshPalette();
        }
      }
    } catch (_) {}
    $$(".mode-btn").forEach((b) => {
      const on = b.dataset.uiMode === mode;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    // Leave expired tab when switching to Simple (expired is adv-only)
    if (mode === "simple" && activeTab === "expired") {
      activeTab = "pending";
      $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === activeTab));
    }
    // One hide path via CSS (ui-simple/ui-advanced); only invalidate mode-sensitive lists
    // Avoid wiping every sig (pos/heat/churn) — cuts mode-switch flash; SSE untouched
    if (prev !== mode) {
      lastOppSig = "";
      lastFeedSig = "";
    }
    if (state) {
      renderAll(state);
    }
    requestAnimationFrame(syncStickyOffsets);
    if (state) syncSimpleAmbient(state);
    if (mode === "advanced") {
      try { closeSimpleSettings(); } catch (_) {}
    }
    try { portalPrefsToSimpleSheet(mode); } catch (_) {}
    try {
      if (window.TradeGlow && typeof window.TradeGlow.resize === "function") {
        window.TradeGlow.resize();
      }
    } catch (_) {}
    try {
      if (window.DeskPulse && typeof window.DeskPulse.onModeMaybe === "function") {
        window.DeskPulse.onModeMaybe();
      } else if (window.DeskPulse && typeof window.DeskPulse.resize === "function") {
        window.DeskPulse.resize();
      }
    } catch (_) {}
  }

  // Only http(s) links from feeds — blocks javascript:/data: URLs in headlines/threads.
  function safeUrl(u) {
    const s = String(u || "").trim();
    return /^https?:\/\//i.test(s) ? s : "";
  }

  let corruptWarned = "";
  function warnCorruptFiles(files) {
    const names = Object.keys(files || {}).map((p) => p.split(/[\\/]/).pop()).sort().join(", ");
    if (!names || names === corruptWarned) return;
    corruptWarned = names;
    toast("Data file unreadable (" + names + ") — trading is paused and saves are blocked. Repair it and restart.", true);
  }

  function toast(msg, isError) {
    const el = $("#toast");
    if (!el) {
      console.warn(isError ? "error:" : "info:", msg);
      return;
    }
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    el.classList.remove("hidden", "toast-out");
    // retrigger enter animation
    el.classList.remove("toast-in");
    void el.offsetWidth;
    el.classList.add("toast-in");
    clearTimeout(toastTimer);
    clearTimeout(toastHideTimer);
    toastTimer = setTimeout(() => {
      el.classList.add("toast-out");
      el.classList.remove("toast-in");
      toastHideTimer = setTimeout(() => {
        el.classList.add("hidden");
        el.classList.remove("toast-out");
      }, 280);
    }, isError ? 9000 : 4200);
  }

  // ---- Money / mode truth helpers (one place decides what the screen may claim)
  function brokerMode(cfg) {
    const b = window.__brokerStatus || {};
    return (cfg || state?.config || {}).mode === "auto_live" && !!b.configured;
  }
  function realMoney(cfg) {
    const b = window.__brokerStatus || {};
    return brokerMode(cfg) && b.paper_mode === false;
  }
  function moneyNoun(cfg) {
    if (realMoney(cfg)) return "REAL money";
    if (brokerMode(cfg)) return "Alpaca paper money";
    return "paper money";
  }

  // ---- Freshness: never show old numbers as if they were current
  let lastDataAt = 0;
  function noteFresh() {
    lastDataAt = Date.now();
    document.body.classList.remove("is-stale");
    const b = $("#stale-banner");
    if (b) b.hidden = true;
  }
  function tickFreshness() {
    const el = $("#live-ind");
    const age = lastDataAt ? Math.round((Date.now() - lastDataAt) / 1000) : null;
    const stale = age != null && age > 25;
    if (el) {
      if (age == null) {
        el.textContent = "Connecting…";
        el.dataset.health = "connecting";
      } else if (stale) {
        el.textContent = `Not updating · ${age < 120 ? age + "s" : Math.round(age / 60) + " min"}`;
        el.dataset.health = "stale";
      } else {
        el.textContent = sseLive ? `Live · ${age}s` : `Updating · ${age}s`;
        el.dataset.health = "live";
      }
    }
    document.body.classList.toggle("is-stale", stale);
    const b = $("#stale-banner");
    if (b) {
      b.hidden = !stale;
      if (stale) {
        const t = new Date(lastDataAt);
        const hh = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        const txt = b.querySelector("[data-stale-text]");
        if (txt) txt.textContent = `Numbers haven't updated since ${hh}. They may be out of date.`;
      }
    }
  }
  setInterval(tickFreshness, 1000);

  function watchlistCount(value) {
    const raw = Array.isArray(value) ? value.join(" ") : String(value || "");
    const seen = new Set();
    raw.split(/[\s,;]+/).forEach((part) => {
      let token = part.trim().replace(/^\$+/, "").toUpperCase();
      token = token.replace(/^(NASDAQ|NYSE|NYSEARCA):/, "");
      if (token) seen.add(token);
    });
    return seen.size;
  }

  function renderWatchlistCount(value) {
    const el = $("#watchlist-count");
    const count = watchlistCount(value);
    if (el) el.textContent = `${count} ticker${count === 1 ? "" : "s"}`;
  }

  function fmtMoney(n) {
    if (n == null || Number.isNaN(n)) return "-";
    return Number(n).toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    });
  }

  function fmtPct(n) {
    return `${Number(n).toFixed(2)}%`;
  }

  function shortTs(iso) {
    if (!iso) return "-";
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  async function api(path, opts) {
    const timeoutMs = opts?.timeoutMs ?? 12000;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const { timeoutMs: _tm, signal: _sig, ...fetchOpts } = opts || {};
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(fetchOpts.headers || {}) },
        ...fetchOpts,
        signal: ctrl.signal,
      });
      let data = null;
      try {
        data = await res.json();
      } catch {
        data = { ok: false, error: "Bad JSON" };
      }
      if (!res.ok) {
        const err = new Error(data?.error || res.statusText);
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    } catch (e) {
      if (e && (e.name === "AbortError" || e.code === 20)) {
        const url = typeof path === "string" ? path : "";
        if (url.includes("evaluate")) throw new Error("Score timed out");
        const te = new Error("state slow/timeout");
        te.timeout = true;
        throw te;
      }
      if (e instanceof TypeError && !e.status) {
        const ne = new Error("Can't reach the desk. Is Tomahawk still running?");
        ne.offline = true;
        throw ne;
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  function renderPresetDetail(preset, name) {
    const el = $("#preset-detail");
    if (!el) return;
    if (!preset) {
      el.textContent = "";
      return;
    }
    el.textContent =
      `${name}: size ${fmtPct(preset.max_position_pct)} | ` +
      `${preset.max_trades_per_day} trades/day | ` +
      `max loss ${fmtPct(preset.max_daily_loss_pct)} | ` +
      `stop ${preset.stop_r}R / target ${preset.target_r}R`;
  }


  function parsePositiveMoney(raw) {
    if (raw == null || String(raw).trim() === "") return null;
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return null;
    return n;
  }

  function updateStartEnabled() {
    const btn = $("#btn-start-session");
    if (!btn) return;
    const make = parsePositiveMoney($("#daily-target")?.value);
    const bank = parsePositiveMoney($("#beginning-bank")?.value);
    const ready = !!(make && bank);
    btn.disabled = !ready;
    if (!ready) {
      const missing = [];
      if (!make) missing.push("goal");
      if (!bank) missing.push("starting cash");
      btn.title = `Turn Auto paper on — enter ${missing.join(" and ")} first`;
    } else {
      btn.title = "Turn Auto paper on";
    }
  }

  function renderSessionChip(cfg, ledger, loop) {
    const chip = $("#session-chip");
    const stopBtn = $("#btn-stop-session");
    if (!chip) return;
    // Session chip follows session_active only (mode alone insufficient)
    const active = !!cfg.session_active;
    const goal = cfg.daily_profit_target_usd;
    const rth = loop?.rth_only !== false;
    const outside = isOutsideRth(loop, cfg);
    // Short chill status — never long pipe strings that ellipsis mid-token
    const mode = cfg.mode || "manual";
    const parts = ["Checking"];
    parts.push(mode === "auto_paper" ? "Auto fill" : mode === "auto_live" ? "Auto + Alpaca" : "Ask me first");
    if (goal != null && Number(goal) > 0) {
      const g = Number(goal);
      const goalTxt = Number.isInteger(g) ? `$${g}` : fmtMoney(g);
      parts.push(`Goal ${goalTxt}`);
    }
    if (active) {
      const afterHoursPaused = outside && rth;
      if (afterHoursPaused) {
        chip.innerHTML =
          '<span class="session-ah-dot" aria-hidden="true"></span>' +
          '<span class="session-ah-label">Paused · after hours</span>';
        chip.title = "Session on — paused outside market hours (no new fills until open)";
        chip.classList.add("after-hours");
      } else {
        const skip = String((loop && loop.last_skip) || "");
        const paused = {
          max_loss: "Paused · loss limit reached",
          bleed_pause: "Paused · losing after costs",
          target_hit: "Paused · goal reached",
          empty_watchlist: "Paused · watchlist is empty",
          loop_disabled: "Paused · checks switched off",
        }[skip];
        chip.textContent = paused || parts.join(" · ");
        chip.classList.toggle("is-paused", !!paused);
        chip.title = paused
          ? paused.replace("Paused · ", "No new trades: ") + ". Open positions still have their stop-loss."
          : "Checking your watchlist every few minutes" +
            (rth ? " during market hours" : "") +
            (goal != null && Number(goal) > 0 ? ` · goal ${fmtMoney(goal)}` : "");
        chip.classList.remove("after-hours");
      }
      const justStarted = !sessionWasActive;
      chip.classList.remove("hidden");
      chip.classList.add("loop-on");
      if (justStarted) {
        chip.classList.remove("session-appear");
        void chip.offsetWidth;
        chip.classList.add("session-appear");
      }
      if (stopBtn) {
        stopBtn.classList.remove("hidden");
        if (justStarted) {
          stopBtn.classList.remove("session-appear");
          void stopBtn.offsetWidth;
          stopBtn.classList.add("session-appear");
        }
      }
      sessionWasActive = true;
    } else {
      chip.classList.add("hidden");
      chip.classList.remove("loop-on", "session-appear", "after-hours");
      chip.textContent = "";
      if (stopBtn) {
        stopBtn.classList.add("hidden");
        stopBtn.classList.remove("session-appear");
      }
      sessionWasActive = false;
    }
    // Ambient class hooks for Simple visuals (no DOM rebuild)
    try { syncSimpleAmbient({ config: cfg, loop: loop }); } catch (_) {}
  }

  // ---- Money display: sign + arrow (never colour alone), smooth count to new value
  function fmtSigned(v) {
    const n = Number(v || 0);
    if (!Number.isFinite(n)) return "—";
    if (Math.abs(n) < 0.005) return fmtMoney(0);
    return (n > 0 ? "+" : "−") + fmtMoney(Math.abs(n)) + (n > 0 ? " ▲" : " ▼");
  }
  const RM_Q = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  function tweenMoney(el, to, opts) {
    if (!el) return;
    const signed = !!(opts && opts.signed);
    const fmt = signed ? fmtSigned : fmtMoney;
    const target = Number(to);
    if (!Number.isFinite(target)) { el.textContent = "—"; return; }
    const from = el.dataset.v != null ? Number(el.dataset.v) : target;
    el.dataset.v = String(target);
    if (el._raf) cancelAnimationFrame(el._raf);
    if ((RM_Q && RM_Q.matches) || Math.abs(target - from) < 0.01 || document.hidden) {
      el.textContent = fmt(target);
      return;
    }
    el.dataset.dir = target > from ? "up" : "down";
    const t0 = performance.now();
    const ms = 450;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / ms);
      const e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(from + (target - from) * e);
      if (k < 1) el._raf = requestAnimationFrame(step);
      else { el._raf = 0; delete el.dataset.dir; el.textContent = fmt(target); }
    };
    el._raf = requestAnimationFrame(step);
  }

  function renderOpenPnl(data) {
    const el = $("#stat-pnl-open");
    if (!el) return;
    const open = Number(data && data.open_pnl_usd);
    const hasPos = ((data && data.ledger && data.ledger.positions) || []).some((p) => Number(p.shares) > 0)
      || (data && data.open_position);
    if (!Number.isFinite(open) || (!hasPos && Math.abs(open) < 0.005)) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.textContent = `open ${fmtSigned(open)}`;
    el.classList.toggle("pos", open > 0);
    el.classList.toggle("neg", open < 0);
  }

  function renderBrokerBook(data) {
    const panel = $("#broker-book");
    if (!panel) return;
    const book = data && data.broker_book;
    if (!book) { panel.classList.add("hidden"); return; }
    panel.classList.remove("hidden");
    const kind = $("#broker-book-kind");
    if (kind) kind.textContent = book.paper_mode === false ? "(Alpaca — REAL money)" : "(Alpaca paper)";
    panel.classList.toggle("is-live", book.paper_mode === false);
    const day = $("#broker-book-day");
    if (day) {
      const n = Number(data.broker_trades_today || 0);
      day.textContent = book.day_pnl_usd != null
        ? `Today ${fmtSigned(book.day_pnl_usd)} · ${n} order${n === 1 ? "" : "s"}`
        : `${n} order${n === 1 ? "" : "s"} today`;
    }
    const list = $("#broker-book-list");
    if (!list) return;
    if (!book.ok) {
      list.innerHTML = `<div class="empty">${escapeHtml(book.error || "Couldn't reach Alpaca")} — the numbers here may be missing.</div>`;
      return;
    }
    const rows = book.positions || [];
    list.innerHTML = rows.length
      ? rows.map((p) => `
          <div class="bb-row">
            <strong>${escapeHtml(p.ticker)}</strong>
            <span>${escapeHtml(String(p.shares))} shares${p.side === "short" ? " (short)" : ""} @ ${fmtMoney(p.avg_price)}</span>
            <span class="money ${Number(p.open_pnl_usd) >= 0 ? "pos" : "neg"}">${fmtSigned(p.open_pnl_usd)}</span>
          </div>`).join("")
      : `<div class="empty">No positions at the broker.</div>`;
  }

  // ---- Light / dark theme (saved choice wins; otherwise follow the computer)
  function applyTheme(t, save) {
    document.documentElement.setAttribute("data-theme", t);
    if (save) { try { localStorage.setItem("desk_theme_v2", t); } catch (_) {} }
    const b = $("#btn-theme");
    if (b) {
      b.textContent = t === "light" ? "☾" : "☀";
      b.setAttribute("aria-label", t === "light" ? "Switch to dark mode" : "Switch to light mode");
      b.title = t === "light" ? "Switch to dark mode" : "Switch to light mode";
    }
    try {
      if (window.EquityScoreboard && typeof window.EquityScoreboard.forceRedraw === "function") {
        window.EquityScoreboard.forceRedraw();
      }
    } catch (_) {}
  }
  applyTheme(document.documentElement.getAttribute("data-theme") || "dark", false);
  $("#btn-theme")?.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    applyTheme(cur === "light" ? "dark" : "light", true);
  });
  try {
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", (e) => {
      let saved = null;
      try { saved = localStorage.getItem("desk_theme_v2"); } catch (_) {}
      if (saved !== "light" && saved !== "dark") applyTheme(e.matches ? "light" : "dark", false);
    });
  } catch (_) {}

  // ---- Stage of the day: one job at a time (Simple). setup → watching ⇄ decide → recap
  function syncPhase(cfg, pendingCount, data) {
    const on = !!cfg.session_active;
    const mode = cfg.mode || "manual";
    const d = (data && data.daily) || state?.daily || {};
    let phase;
    if (on) {
      phase = pendingCount > 0 && mode !== "auto_paper" ? "decide" : "watching";
    } else {
      const started = String(cfg.session_started_at || "");
      const ranToday = Number(d.trades || 0) > 0 || (started && d.date && started.slice(0, 10) >= String(d.date).slice(0, 10));
      phase = ranToday ? "recap" : "setup";
    }
    document.body.dataset.phase = phase;
    const tag = $("#stage-phase-tag");
    if (tag) {
      const txt = phase === "setup"
        ? "From your last session — press Start checking to begin"
        : phase === "recap"
          ? "Checking is stopped — this was the last call"
          : "";
      tag.textContent = txt;
      tag.hidden = !txt;
    }
    renderDecideCard(phase === "decide");
  }

  function firstPending() {
    const opps = (state?.opportunities || []).filter((o) => isOppActionable(o));
    if (opps.length) return opps[0];
    const bag = state?.signals || {};
    return (bag.pending || [])[0] || null;
  }

  function renderDecideCard(show) {
    const box = $("#stage-decide");
    if (!box) return;
    const s = show ? firstPending() : null;
    if (!s || pendingSkips.has(s.id)) {
      box.hidden = true;
      box.innerHTML = "";
      box.dataset.sig = "";
      return;
    }
    const n = Math.max(0, lastPendingCount - 1);
    const sh = Number(s.suggested_shares || 0);
    const px = Number(s.signal_price || 0);
    const verb = String(s.side || "").toLowerCase() === "sell" ? "Sell" : "Buy";
    const sig = [s.id, sh, px, n, realMoney()].join("|");
    if (box.dataset.sig === sig) { box.hidden = false; return; }
    box.dataset.sig = sig;
    const why = plainify(s.why_plain || s.llm_thesis || s.thesis || s.reason || "");
    box.innerHTML = `
      <div class="decide-head">An idea needs you${n ? ` <span class="muted">(+${n} more waiting)</span>` : ""}</div>
      <div class="decide-main">
        <strong class="decide-ticker">${verb} ${escapeHtml(s.ticker || "")}</strong>
        <span class="decide-maths">${sh ? `${sh} shares × ${fmtMoney(px)} ≈ ${fmtMoney(sh * px)} of ${escapeHtml(moneyNoun())}` : ""}</span>
      </div>
      ${why ? `<p class="decide-why">${escapeHtml(String(why).slice(0, 180))}</p>` : ""}
      <div class="decide-actions">
        <button type="button" class="btn good" data-approve="${escapeHtml(s.id)}" title="Review and place it (key: A)">Review &amp; ${verb.toLowerCase()}</button>
        <button type="button" class="btn ghost" data-reject="${escapeHtml(s.id)}" title="Pass on this idea — you can undo for 5 seconds (key: S)">Skip</button>
        <span class="decide-keys muted">Keys: <kbd>A</kbd> review · <kbd>S</kbd> skip</span>
      </div>`;
    box.hidden = false;
    box.classList.remove("arrive");
    void box.offsetWidth;
    box.classList.add("arrive");
  }

  $("#stage-decide")?.addEventListener("click", (ev) => {
    const approve = ev.target.closest("[data-approve]");
    const reject = ev.target.closest("[data-reject]");
    if (approve) openApprovePreview(approve.dataset.approve);
    if (reject) skipWithUndo(reject.dataset.reject, "Skipped by you", null);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const tag = (ev.target && ev.target.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT/.test(tag) || (ev.target && ev.target.isContentEditable)) return;
    if (document.querySelector(".modal:not(.hidden)")) return;
    const box = $("#stage-decide");
    if (!box || box.hidden) return;
    const k = ev.key.toLowerCase();
    if (k === "a") {
      const b = box.querySelector("[data-approve]");
      if (b) { ev.preventDefault(); openApprovePreview(b.dataset.approve); }
    } else if (k === "s") {
      const b = box.querySelector("[data-reject]");
      if (b) { ev.preventDefault(); skipWithUndo(b.dataset.reject, "Skipped by you", null); }
    }
  });

  // ---- "The desk is thinking" — only while a decision is really in flight
  function syncThinking(loop) {
    const on = !!(loop && loop.decision_in_flight);
    document.body.classList.toggle("is-deciding", on);
    const el = $("#stage-thinking");
    if (!el) return;
    el.hidden = !on;
    const t = el.querySelector("[data-thinking-text]");
    if (t) t.textContent = on ? `Checking ${loop.in_flight_ticker || "a ticker"}…` : "";
    $("#one-job-stage")?.setAttribute("aria-busy", on ? "true" : "false");
  }

  // ---- Slow-bleed guard + "you vs. just holding SPY"
  function fmtPctSigned(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(2) + "%";
  }
  function benchSentence(b) {
    if (!b || !b.ok) return "";
    const ahead = Number(b.ahead_usd || 0);
    const verdict = Math.abs(ahead) < 0.5
      ? "about even with just holding SPY"
      : ahead > 0
        ? `${fmtMoney(ahead)} ahead of just holding SPY`
        : `${fmtMoney(Math.abs(ahead))} behind just holding SPY`;
    return `You ${fmtPctSigned(b.desk_return_pct)} (${fmtSigned(b.desk_usd)}) · SPY ${fmtPctSigned(b.spy_return_pct)} (${fmtSigned(b.spy_usd)}) — ${verdict}.`;
  }
  function renderBenchmark(data) {
    const el = $("#bench-line");
    if (!el) return;
    const txt = benchSentence(data && data.benchmark);
    el.hidden = !txt;
    el.textContent = txt;
    const ahead = Number(data?.benchmark?.ahead_usd || 0);
    el.classList.toggle("pos", ahead >= 0.5);
    el.classList.toggle("neg", ahead <= -0.5);
  }
  function renderBleed(data) {
    const box = $("#stage-bleed");
    if (!box) return;
    const b = data && data.bleed;
    const on = !!(b && b.paused);
    box.hidden = !on;
    document.body.classList.toggle("is-bleed-paused", on);
    if (!on) return;
    const t = box.querySelector("[data-bleed-text]");
    if (t) {
      t.textContent =
        `Your last ${b.closed_trades} closed trades lost ${fmtMoney(Math.abs(b.net_usd))} in total after costs ` +
        `(trading results ${fmtSigned(b.realized_usd)}, fees −${fmtMoney(b.fees_usd)}). ` +
        `Small losses like this add up quietly, so no new trades will be placed until you choose to resume. ` +
        `Stop-loss and take-profit on open positions keep working.`;
    }
  }
  $("#btn-bleed-resume")?.addEventListener("click", async () => {
    if (!confirm("Resume trading? The losing-streak count starts over from now.")) return;
    try {
      await api("/api/bleed/resume", { method: "POST", body: "{}" });
      toast("Resumed — the desk will watch the next trades");
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  });

  // ---- Claude head-to-head toggle
  $("#claude-shadow")?.addEventListener("change", async (ev) => {
    const on = !!ev.target.checked;
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify({ claude_shadow: on }) });
      toast(on ? "Claude now answers silently next to your main AI (costs a little per check)" : "Claude head-to-head off");
    } catch (e) {
      ev.target.checked = !on;
      toast(e.message, true);
    }
  });

  // ---- Weekly report card
  async function openReport() {
    const modal = $("#report-modal");
    const body = $("#report-body");
    if (!modal || !body) return;
    body.innerHTML = '<p class="muted">Loading…</p>';
    modal.classList.remove("hidden");
    $("#btn-report-close")?.focus();
    let r;
    try {
      r = await api("/api/report/weekly?days=7", { timeoutMs: 20000 });
    } catch (e) {
      body.innerHTML = `<p>Couldn't load the report: ${escapeHtml(e.message)}</p>`;
      return;
    }
    const hit = r.hit_rate != null ? `${Math.round(r.hit_rate * 100)}%` : "—";
    const setups = (r.worst_setups || []).map((s) => {
      const [v, lt] = String(s.setup || "?|?").split("|");
      const call = { buy: "Buy", sell: "Sell", flat: "Hold" }[s.side] || s.side;
      return `<li>${escapeHtml(call)} calls on <strong>${escapeHtml(verdictGloss(v) || v)}</strong> setups with ${escapeHtml(timingGloss(lt) || lt)} timing — ${s.hurt} hurt, ${s.helped} helped</li>`;
    }).join("");
    body.innerHTML = `
      <div class="report-grade grade-${escapeHtml(String(r.grade).replace("—", "none"))}">${escapeHtml(r.grade)}</div>
      <p class="report-why">${escapeHtml(r.grade_reason)}</p>
      <ul class="report-list">
        <li>Profit after costs: <strong class="money ${r.net_usd > 0 ? "pos" : r.net_usd < 0 ? "neg" : ""}">${fmtSigned(r.net_usd)}</strong>
          <span class="muted">(trades ${fmtSigned(r.realized_usd)}, costs −${fmtMoney(r.fees_usd)})</span></li>
        <li><strong>${r.closed_trades}</strong> closed trade${r.closed_trades === 1 ? "" : "s"}, <strong>${r.wins}</strong> winner${r.wins === 1 ? "" : "s"}</li>
        <li>AI calls right: <strong>${hit}</strong> <span class="muted">(${r.calls_helped} helped, ${r.calls_hurt} hurt)</span></li>
        ${r.best ? `<li>Best trade: ${escapeHtml(r.best.ticker || "")} ${fmtSigned(r.best.pnl)}</li>` : ""}
        ${r.worst ? `<li>Worst trade: ${escapeHtml(r.worst.ticker || "")} ${fmtSigned(r.worst.pnl)}</li>` : ""}
        ${r.benchmark && r.benchmark.ok ? `<li>${escapeHtml(benchSentence(r.benchmark))}</li>` : ""}
      </ul>
      ${setups ? `<p class="report-sub">Setups that kept losing — the AI now sees this history before deciding:</p><ul class="report-list">${setups}</ul>` : ""}
      <p class="muted report-foot">Practice money only. A good grade over a few weeks matters more than one good week.</p>`;
    appendBrainScoreboard(body);
  }
  async function appendBrainScoreboard(body) {
    try {
      const s = await api("/api/brains/scoreboard", { timeoutMs: 15000 });
      if (!s || !s.compared_calls) {
        if (s && s.claude_configured) {
          body.insertAdjacentHTML("beforeend", '<p class="muted">Claude head-to-head: no scored calls yet.</p>');
        }
        return;
      }
      const pct = (d) => (d.hit_rate != null ? Math.round(d.hit_rate * 100) + "%" : "—");
      const main = escapeHtml(String(s.main_brain || "Main AI"));
      body.insertAdjacentHTML("beforeend", `
        <p class="report-sub">Who called it better? (same ${s.compared_calls} decisions, last ${s.days} days)</p>
        <table class="brain-table">
          <tr><th></th><th>Right</th><th>Wrong</th><th>Hit rate</th></tr>
          <tr><td>${main}</td><td>${s.main.helped}</td><td>${s.main.hurt}</td><td>${pct(s.main)}</td></tr>
          <tr><td>Claude</td><td>${s.claude.helped}</td><td>${s.claude.hurt}</td><td>${pct(s.claude)}</td></tr>
        </table>`);
    } catch (_) { /* optional section */ }
  }
  // ---- Backtest: does the screener rule have an edge on past data?
  function btBlock(title, s) {
    if (!s || !s.trades) return `<li>${escapeHtml(title)}: no trades</li>`;
    return `<li>${escapeHtml(title)}: <strong>${s.trades}</strong> trades, won ${Math.round(s.win_rate * 100)}%,
      average ${fmtSigned(s.avg_per_trade_usd)} per trade after costs (total ${fmtSigned(s.total_usd)}; worst dip −${fmtMoney(s.max_drawdown_usd)})</li>`;
  }
  function renderBacktest(res, box) {
    if (!res || !res.ok) {
      box.innerHTML = `<p>${escapeHtml((res && res.error) || "No result yet.")}</p>`;
      return;
    }
    const label = { edge: "Promising", overfit: "Probably luck", weak: "Better than random, still losing", no_edge: "No proven edge", not_enough: "Not enough data" }[res.verdict] || res.verdict;
    box.innerHTML = `
      <p class="report-sub">Past-data test: <span class="bt-verdict bt-${escapeHtml(res.verdict)}">${escapeHtml(label)}</span></p>
      <p>${escapeHtml(res.explanation)}</p>
      ${(res.warnings || []).map((w) => `<p class="approve-live-warn">${escapeHtml(w)}</p>`).join("")}
      <p class="muted">Learning period ${escapeHtml(res.period.start)} → ${escapeHtml(res.period.split)}:</p>
      <ul class="report-list">${btBlock("Screener's buy rule", res.learn.rule)}${btBlock("Buying with no filter", res.learn.baseline)}</ul>
      <p class="muted">Check period ${escapeHtml(res.period.split)} → ${escapeHtml(res.period.end)} (never used to tune):</p>
      <ul class="report-list">${btBlock("Screener's buy rule", res.check.rule)}${btBlock("Buying with no filter", res.check.baseline)}</ul>
      <p class="muted report-foot">${(res.tickers || []).length} stocks tested, ${fmtMoney(res.params.position_usd)} per trade, buy at 10:30 am, exit at stop / target / close.
        Not included: ${escapeHtml((res.not_included || []).join(", "))}.</p>`;
  }
  async function runBacktest() {
    const body = $("#report-body");
    if (!body) return;
    let box = $("#bt-box");
    if (!box) {
      body.insertAdjacentHTML("beforeend", '<div id="bt-box" class="bt-box"></div>');
      box = $("#bt-box");
    }
    try {
      const cur = await api("/api/backtest");
      if (!cur.status.running) {
        if (cur.result && !confirm("Run a new test? (Downloads about 2 years of hourly prices — takes a minute or two.)\n\nCancel shows the last result.")) {
          renderBacktest(cur.result, box);
          return;
        }
        await api("/api/backtest", { method: "POST", body: "{}" });
      }
    } catch (e) {
      box.innerHTML = `<p>${escapeHtml(e.message)}</p>`;
      return;
    }
    const poll = async () => {
      try {
        const s = await api("/api/backtest");
        if (s.status.running) {
          box.innerHTML = `<p class="muted">${escapeHtml(s.status.message || "Working…")}</p>`;
          setTimeout(poll, 1500);
        } else {
          renderBacktest(s.result, box);
        }
      } catch (e) {
        box.innerHTML = `<p>${escapeHtml(e.message)}</p>`;
      }
    };
    poll();
  }
  $("#btn-backtest")?.addEventListener("click", runBacktest);
  $("#btn-report")?.addEventListener("click", openReport);
  $("#btn-recap-report")?.addEventListener("click", () => {
    $("#recap-modal")?.classList.add("hidden");
    openReport();
  });
  $("#btn-report-close")?.addEventListener("click", () => $("#report-modal")?.classList.add("hidden"));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") $("#report-modal")?.classList.add("hidden");
  });

  // ---- End-of-day recap (shown after Stop)
  function showRecap(before, after) {
    const modal = $("#recap-modal");
    const body = $("#recap-body");
    if (!modal || !body || !after) return;
    const d = after.daily || {};
    const pnl = Number(d.pnl || 0);
    const goal = d.target_usd != null ? Number(d.target_usd) : null;
    const trades = Number(d.trades || 0);
    const positions = ((after.ledger && after.ledger.positions) || []).filter((p) => Number(p.shares) > 0);
    const open = Number(after.open_pnl_usd || 0);
    const goalLine = goal
      ? (pnl >= goal
          ? `You reached your ${fmtMoney(goal)} goal.`
          : `${fmtMoney(Math.max(0, goal - pnl))} short of your ${fmtMoney(goal)} goal.`)
      : "No goal was set.";
    body.innerHTML = `
      <div class="recap-big money ${pnl > 0 ? "pos" : pnl < 0 ? "neg" : ""}">${fmtSigned(pnl)}</div>
      <p class="recap-line">${escapeHtml(goalLine)}</p>
      ${after.benchmark && after.benchmark.ok ? `<p class="recap-line">${escapeHtml(benchSentence(after.benchmark))}</p>` : ""}
      <ul class="recap-list">
        <li><strong>${trades}</strong> paper trade${trades === 1 ? "" : "s"} today${Number(d.fees || 0) > 0 ? ` · pretend costs ${fmtMoney(d.fees)} (already taken out of the number above)` : ""}</li>
        <li><strong>${positions.length}</strong> position${positions.length === 1 ? "" : "s"} still open${positions.length ? ` (worth ${fmtSigned(open)} vs. what you paid)` : ""}</li>
      </ul>
      ${positions.length ? `<p class="muted">Open positions keep their stop-loss and take-profit while the market is open. You can also close them all now.</p>` : ""}`;
    const flat = $("#btn-recap-flatten");
    if (flat) flat.classList.toggle("hidden", !positions.length);
    const ok = $("#btn-recap-ok");
    if (ok) ok.textContent = positions.length ? "Keep them & close" : "Close";
    modal.classList.remove("hidden");
    $("#btn-recap-ok")?.focus();
  }
  $("#btn-recap-ok")?.addEventListener("click", () => $("#recap-modal")?.classList.add("hidden"));
  $("#btn-recap-flatten")?.addEventListener("click", async () => {
    if (!confirm("Close ALL open paper positions at the current price?")) return;
    try {
      await api("/api/ledger/flatten", { method: "POST", body: "{}", timeoutMs: 30000 });
      toast("All paper positions closed");
      $("#recap-modal")?.classList.add("hidden");
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  });
  $("#btn-stale-retry")?.addEventListener("click", () => {
    refresh();
    try { connectSSE(); } catch (_) {}
  });

  function renderAll(data) {
    if (!data) return;
    renderTop(data);
    renderOpenPnl(data);
    renderBrokerBook(data);
    renderBleed(data);
    renderBenchmark(data);
    const cs = $("#claude-shadow");
    if (cs && data.config && document.activeElement !== cs) cs.checked = !!data.config.claude_shadow;
    renderLoopPanel(data);
    try { renderNews(data); } catch (_) {}
    renderLoopFeed();
    renderPaperChrome(data);
    renderBuzz(data);
    renderHeat(data);
    renderRadar(data);
    renderPace(data);
    renderOpportunities(data);
    renderSignals(data);
    renderPositions(data);
    updateDeskGuide(data);
    if (typeof scheduleSparks === "function") scheduleSparks(data);
  }

  function renderTop(data) {
    const cfg = data.config || {};
    const ledger = data.ledger || {};
    const daily = data.daily || {};
    const modeLabel = { manual: "I approve", auto_paper: "auto paper", auto_live: "auto + Alpaca" };
    const setText = (sel, text) => {
      const el = $(sel);
      if (el) el.textContent = text;
    };
    setText("#stat-mode", modeLabel[cfg.mode] || cfg.mode || "-");
    setText("#stat-preset", cfg.risk_preset || "-");
    setText("#stat-equity", fmtMoney(ledger.equity));
    setText("#stat-cash", fmtMoney(ledger.cash));
    setText("#stat-trades", String(daily.trades ?? 0));
    const pnl = Number(daily.pnl || 0);
    const pnlEl = $("#stat-pnl");
    if (pnlEl) {
      tweenMoney(pnlEl, pnl, { signed: true });
      pnlEl.classList.toggle("pos", pnl > 0);
      pnlEl.classList.toggle("neg", pnl < 0);
      if (lastHeroPnl != null && Number.isFinite(pnl) && pnl !== lastHeroPnl) {
        const dir = pnl > lastHeroPnl ? "up" : "down";
        pnlEl.removeAttribute("data-flash");
        void pnlEl.offsetWidth;
        pnlEl.setAttribute("data-flash", dir);
        clearTimeout(pnlEl._flashTimer);
        pnlEl._flashTimer = setTimeout(() => pnlEl.removeAttribute("data-flash"), 750);
      }
      lastHeroPnl = pnl;
    }

    const targetUsd = daily.target_usd;
    const tgtEl = $("#stat-target");
    const remEl = $("#stat-target-rem");
    const fillEl = $("#target-fill");
    if (targetUsd == null) {
      if (tgtEl) tgtEl.textContent = "not set";
      if (remEl) remEl.textContent = "";
      if (fillEl) {
        fillEl.style.width = "0%";
        fillEl.classList.remove("hit");
        const bar0 = fillEl.closest && fillEl.closest(".target-bar, #race-meter");
        if (bar0) {
          bar0.classList.remove("is-progressing", "is-near-goal", "is-hit", "is-celebrate");
        }
      }
      renderTop._wasHit = false;
    } else {
      if (tgtEl) tgtEl.textContent = fmtMoney(targetUsd);
      const pct = Number(daily.progress_pct || 0);
      if (fillEl) {
        fillEl.style.width = `${pct}%`;
        fillEl.classList.toggle("hit", !!daily.target_hit);
        const bar = fillEl.closest && fillEl.closest(".target-bar, #race-meter");
        if (bar) {
          const progressing = pct > 0 && pct < 100 && !daily.target_hit;
          const near = !!daily.target_usd && pct >= 85 && pct < 100 && !daily.target_hit;
          bar.classList.toggle("is-progressing", progressing);
          bar.classList.toggle("is-near-goal", near);
          bar.classList.toggle("is-hit", !!daily.target_hit);
          // Brief celebrate when goal newly hit (Simple)
          const hitNow = !!daily.target_hit;
          if (hitNow && !renderTop._wasHit) {
            bar.classList.remove("is-celebrate");
            void bar.offsetWidth;
            bar.classList.add("is-celebrate");
            clearTimeout(bar._celeTimer);
            bar._celeTimer = setTimeout(() => bar.classList.remove("is-celebrate"), 2200);
          }
          renderTop._wasHit = hitNow;
        } else {
          renderTop._wasHit = !!daily.target_hit;
        }
      }
      if (remEl) {
        remEl.textContent = daily.target_hit
          ? "Goal hit - paused"
          : `${fmtMoney(daily.remaining_usd)} to go`;
        remEl.classList.toggle("hit", !!daily.target_hit);
      }
    }
    const dailyTargetEl = $("#daily-target");
    if (dailyTargetEl && document.activeElement !== dailyTargetEl) {
      dailyTargetEl.value =
        cfg.daily_profit_target_usd != null ? cfg.daily_profit_target_usd : "";
    }
    const bankEl = $("#beginning-bank");
    if (bankEl && document.activeElement !== bankEl) {
      const bank =
        cfg.paper_equity != null
          ? cfg.paper_equity
          : ledger.equity != null
            ? ledger.equity
            : "";
      if (bank !== "" && bankEl.value === "") {
        bankEl.value = bank;
      } else if (bank !== "" && !bankEl.dataset.touched) {
        bankEl.value = bank;
      }
    }
    updateStartEnabled();
    renderSessionChip(cfg, ledger, data.loop);

    const modeSelect = $("#mode-select");
    const liveBox = $("#live-box");
    if (modeSelect) modeSelect.value = cfg.mode || "manual";
    syncFillModeToggle(cfg);
    if (liveBox) {
      liveBox.classList.toggle(
        "hidden",
        cfg.mode !== "auto_live" && (!modeSelect || modeSelect.value !== "auto_live")
      );
      if (modeSelect && modeSelect.value === "auto_live") {
        liveBox.classList.remove("hidden");
      }
    }

    $$(".preset-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.preset === cfg.risk_preset);
    });
    renderPresetDetail(data.preset, cfg.risk_preset);

    // Simple-mode watchlist editor: never overwrite while the user has unsaved edits
    const swl = $("#simple-wl");
    if (swl && Array.isArray(cfg.watchlist) && swl.dataset.dirty !== "1" && document.activeElement !== swl) {
      swl.value = cfg.watchlist.join("\n");
      const c = $("#simple-wl-count");
      if (c) c.textContent = `(${cfg.watchlist.length})`;
    }
    // Never write #watchlist unless Array.isArray(cfg.watchlist) - lite must not blank it
    const wlEl = $("#watchlist");
    if (Array.isArray(cfg.watchlist)) {
      const wl = cfg.watchlist;
      if (wlEl && document.activeElement !== wlEl) {
        wlEl.value = wl.join("\n");
      }
      renderWatchlistCount(wl);
    } else if (wlEl) {
      renderWatchlistCount(wlEl.value);
    }

    const focus = cfg.watchlist_focus || "liquid";
    const focusStatus = $("#watchlist-focus-status");
    if (focusStatus) {
      focusStatus.textContent =
        focus === "liquid"
          ? `Focus: liquid (scan/loop use liquid names from your list)`
          : `Focus: full list (scan/loop use all equity tickers)`;
    }
    const btnFocusLiquid = $("#btn-focus-liquid");
    const btnFocusAll = $("#btn-focus-all");
    if (btnFocusLiquid) btnFocusLiquid.classList.toggle("active", focus === "liquid");
    if (btnFocusAll) btnFocusAll.classList.toggle("active", focus === "all");

    if (cfg.kill_switch) {
      const ks = cfg.kill_switch;
      const ksLoss = $("#ks-loss");
      const ksTrades = $("#ks-trades");
      const ksPos = $("#ks-pos");
      if (ksLoss && document.activeElement !== ksLoss && ks.max_daily_loss_usd != null) {
        ksLoss.value = ks.max_daily_loss_usd;
      }
      if (ksTrades && document.activeElement !== ksTrades && ks.max_trades_per_day != null) {
        ksTrades.value = ks.max_trades_per_day;
      }
      if (ksPos && document.activeElement !== ksPos && ks.max_position_size_usd != null) {
        ksPos.value = ks.max_position_size_usd;
      }
    }

    if (data.banner) {
      const banner = $("#banner");
      if (banner) banner.textContent = data.banner;
    }
    renderBrokerStatus(data.broker);

    const llm = data.llm || {};
    const llmEl = $("#stat-llm");
    if (llmEl) {
      const brain = (llm.brain_mode || data.brain_mode || "gemini").toLowerCase();
      const simpleUi = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
      // Always keep quiet class — chill vibe; ok/warn are soft hints not neon
      llmEl.classList.add("quiet", "soft");
      if (brain === "mock") {
        llmEl.textContent = simpleUi ? "Mock" : "Mock brain";
        llmEl.classList.remove("warn-pill");
        llmEl.classList.add("ok-pill");
        llmEl.title = simpleUi ? "Brain (mock) — picks Hold, Buy, or Sell" : "Mock brain";
      } else if (brain === "jev") {
        if (llm.configured || llm.jev_key_present) {
          llmEl.textContent = simpleUi ? "Jev" : "Jev on";
          llmEl.classList.remove("warn-pill");
          llmEl.classList.add("ok-pill");
          llmEl.title = simpleUi ? "Brain (Jev) — picks Hold, Buy, or Sell" : "Jev brain on — picks Hold, Buy, or Sell (paper research)";
        } else {
          llmEl.textContent = simpleUi ? "Jev?" : "Jev needs API key";
          llmEl.classList.add("warn-pill");
          llmEl.classList.remove("ok-pill");
          llmEl.title = simpleUi ? "Brain needs a key — using mock for now" : "Jev needs an API key — using mock brain until set";
        }
      } else if (llm.configured) {
        const model = llm.model || "flash";
        llmEl.textContent = simpleUi ? "Gemini" : `Gemini · ${model}`;
        llmEl.classList.remove("warn-pill");
        llmEl.classList.add("ok-pill");
        llmEl.title = simpleUi ? "Brain (Gemini) — picks Hold, Buy, or Sell" : `Gemini brain on (${model}) — picks Hold, Buy, or Sell (paper research)`;
      } else {
        llmEl.textContent = simpleUi ? "Gemini?" : "Gemini needs an API key";
        llmEl.classList.add("warn-pill");
        llmEl.classList.remove("ok-pill");
        llmEl.title = simpleUi ? "Brain needs a key — set it in Advanced or .env" : "Gemini needs an API key — set GEMINI_API_KEY in .env";
      }
    }
    if (typeof renderBrainLedger === "function") renderBrainLedger(data);
  }

  function signalBadges(s) {
    const bits = [];
    const v = (s.verdict || "").toUpperCase();
    if (v === "PASS") bits.push(`<span class="badge badge-pass" title="Pass — research says worth a look (not a fill)">PASS</span>`);
    else if (v === "WATCH") bits.push(`<span class="badge badge-watch" title="Watch — not ready to act">WATCH</span>`);
    else if (v === "AVOID") bits.push(`<span class="badge badge-avoid" title="Avoid — research says skip">AVOID</span>`);
    const late = (s.lateness_label || s.entry_quality?.label || "").toLowerCase();
    if (late) {
      const lateClass = late === "chasing" || late === "late" ? "badge-late" : "badge-fair";
      bits.push(`<span class="badge ${lateClass}">${escapeHtml(late)}</span>`);
    }
    const earn = s.earnings;
    if (earn && earn.is_soon) {
      const d = earn.days_away != null ? earn.days_away : "?";
      bits.push(`<span class="badge badge-earn">Earnings in ${escapeHtml(String(d))}d</span>`);
    }
    if (s.rel_vol != null) {
      bits.push(`<span class="badge badge-vol">${Number(s.rel_vol).toFixed(1)}× volume</span>`);
    }
    const flags = s.research_flags || [];
    if (flags.length) {
      bits.push(`<span class="badge badge-research" title="Research notes only — not a trade signal: ${escapeHtml(flags.join(', '))}">research</span>`);
    }
    if (s.llm_side) {
      const ls = String(s.llm_side).toLowerCase();
      bits.push(`<span class="badge badge-llm" title="Brain lean (Buy/Sell/Hold) — research only">Brain ${escapeHtml(ls)}</span>`);
    }
    if (s.llm_error) {
      bits.push(`<span class="badge badge-avoid" title="${escapeHtml(s.llm_error)}">Gemini error</span>`);
    }
    const rflags = s.research_flags || (s.research_flag ? [s.research_flag] : []);
    if (rflags.includes("halt_detected")) {
      bits.push(`<span class="badge badge-halt">HALT</span>`);
    }
    if (rflags.includes("gap_gt_8pct") || (s.gap_pct != null && Math.abs(Number(s.gap_pct)) > 8)) {
      const g = s.gap_pct != null ? Number(s.gap_pct).toFixed(1) : "?";
      bits.push(`<span class="badge badge-gap">GAP ${g}%</span>`);
    }
    if (s.buzz_mentions != null && Number(s.buzz_mentions) > 0) {
      bits.push(`<span class="badge badge-buzz" title="Reddit mention count (research only)">buzz ${escapeHtml(String(s.buzz_mentions))}</span>`);
    }
    return bits.length ? `<div class="sig-badges">${bits.join("")}</div>` : "";
  }

  function signalCard(s) {
    const confPct = Math.round((s.confidence || 0) * 100);
    const actions =
      s.status === "pending"
        ? `<div class="sig-actions">
            <button type="button" class="btn good sm" data-approve="${s.id}" title="Do this paper trade">Approve</button>
            <button type="button" class="btn bad sm" data-reject="${s.id}" title="Pass on this idea">Skip</button>
          </div>`
        : s.reject_reason
          ? `<div class="sig-meta">Reason: ${escapeHtml(s.reject_reason)}</div>`
          : s.fill
            ? `<div class="sig-meta">Filled ${s.fill.shares}@${s.fill.price} (${escapeHtml(s.fill.source || "")})</div>`
            : "";

    return `<article class="signal-card ${s.side}">
      <div class="sig-head">
        <span class="sig-ticker">${escapeHtml(s.ticker)}</span>
        <span class="sig-side ${s.side}">${escapeHtml(s.side)}</span>
      </div>
      ${signalBadges(s)}
      <div class="conf-bar"><div class="conf-fill" style="width:${confPct}%"></div></div>
      <div class="sig-meta">
        <span>${Math.round((s.confidence || 0) * 100)}% sure</span>
        <span>${fmtMoney(s.signal_price)}</span>
        <span>${s.suggested_shares} sh</span>
      </div>
      <p class="sig-reason">${escapeHtml(s.reason || "")}</p>
      ${s.llm_thesis ? `<p class="sig-llm"><span class="llm-label">Gemini</span> ${escapeHtml(s.llm_thesis)}</p>${citationsHtml(s.citations || s.screener_citations)}` : ""}
      ${actions}
    </article>`;
  }

  function citationsHtml(cites) {
    if (!cites || !cites.length) return "";
    return `<div class="citations">${cites.map((c) =>
      `<span class="cite">${escapeHtml(c.label || c.key)}: ${escapeHtml(c.value)}</span>`
    ).join("")}</div>`;
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderSignals(data) {
    const bag = data.signals || {};
    const list = bag[activeTab] || [];
    const el = $("#signal-list");
    if (!el) return;
    const counts = {
      pending: (bag.pending || []).length,
      approved: (bag.approved || []).length,
      rejected: (bag.rejected || []).length,
      expired: (bag.expired || []).length,
    };
    $$(".tab").forEach((t) => {
      const key = t.dataset.tab;
      const n = counts[key];
      const label = t.dataset.label || t.textContent.replace(/\s*\d+$/, "").trim();
      if (!t.dataset.label) t.dataset.label = label.split("\n")[0].trim();
      const base = t.dataset.label;
      t.innerHTML = n != null
        ? `${base}<span class="tab-count">${n}</span>`
        : base;
    });
    if (!list.length) {
      const hints = {
        pending: "Nothing waiting — start Auto paper or wait for the next scan.",
        approved: "No paper fills yet.",
        rejected: "Skipped ideas show up here.",
        expired: "Expired signals show up here.",
      };
      const title = {
        pending: "No pending ideas",
        approved: "No done items",
        rejected: "No skipped items",
        expired: "No expired items",
      }[activeTab] || `No ${activeTab} items`;
      el.innerHTML = `<div class="empty">${title}<span class="empty-hint">${hints[activeTab] || ""}</span></div>`;
      return;
    }
    el.innerHTML = list.map(signalCard).join("");
  }

  function posSignature(positions) {
    return (positions || [])
      .map((p) => `${p.ticker}|${p.side}|${p.shares}|${p.avg_price}`)
      .join(";");
  }

  function renderPositions(data) {
    const positions = data.ledger?.positions || [];
    const el = $("#positions");
    const emptyEl = $("#pos-empty");
    const chip = $("#pos-chip");
    if (chip) {
      const n = positions.length;
      chip.textContent = n === 1 ? "1 open" : n + " open";
    }
    if (!el) return;
    const sig = posSignature(positions);
    if (sig === lastPosSig && el.childElementCount) return;
    lastPosSig = sig;
    if (!positions.length) {
      el.innerHTML = "";
      if (emptyEl) emptyEl.classList.remove("hidden");
      else el.innerHTML = `<div class="empty">No open positions<span class="empty-hint">Nothing held on paper yet.</span></div>`;
      return;
    }
    if (emptyEl) emptyEl.classList.add("hidden");
    el.innerHTML = `<table>
      <thead><tr><th title="Ticker symbol">Ticker</th><th title="Buy (long) or Sell (short)">Side</th><th title="Shares held">Sh</th><th title="Average fill price">Avg</th></tr></thead>
      <tbody>
        ${positions
          .map((p) => {
            const hints = [];
            if (p.stop_price != null) hints.push(`SL ${p.stop_price}`);
            if (p.take_profit_price != null) hints.push(`TP ${p.take_profit_price}`);
            const hint = hints.length
              ? `<div class="pos-exit-hint">${escapeHtml(hints.join(" · "))}</div>`
              : "";
            return `<tr><td>${escapeHtml(p.ticker)}${hint}</td><td>${escapeHtml(p.side)}</td><td>${p.shares}</td><td>${p.avg_price}</td></tr>`;
          })
          .join("")}
      </tbody>
    </table>`;
  }

  function renderFills(data) {
    const fills = (data.ledger?.fills || []).slice(0, 12);
    const el = $("#fills");
    if (!el) return;
    if (!fills.length) {
      el.innerHTML = `<div class="empty">No fills yet<span class="empty-hint">Paper fills appear after Approve or Auto fill — not live brokerage fills.</span></div>`;
      return;
    }
    el.innerHTML = `<table>
      <thead><tr><th>Time</th><th>T</th><th>Side</th><th>Fill</th></tr></thead>
      <tbody>
        ${fills
          .map(
            (f) =>
              `<tr><td>${shortTs(f.ts)}</td><td>${escapeHtml(f.ticker)}</td><td>${escapeHtml(f.side)}</td><td>${f.shares}@${f.price}</td></tr>`
          )
          .join("")}
      </tbody>
    </table>`;
  }

  function renderJournal(data) {
    const journal = data.journal || [];
    const el = $("#journal");
    if (!el) return;
    if (!journal.length) {
      el.innerHTML = `<div class="empty">No journal entries yet<span class="empty-hint">Short desk notes land here after activity.</span></div>`;
      return;
    }
    el.innerHTML = journal
      .slice(0, 30)
      .map((j) => {
        const detail = j.detail?.ticker || j.detail?.mode || j.detail?.error || j.detail?.status || "";
        return `<div class="j-entry"><strong>${escapeHtml(j.action)}</strong> ${escapeHtml(String(detail))} <span>${shortTs(j.ts)}</span></div>`;
      })
      .join("");
  }

  function render(data) {
    state = data;
    fullStateSeen = true;
    _liteSig = "";
    window.__oppEmptyStreak = 0;
    if (oppEmptyTimer) { clearTimeout(oppEmptyTimer); oppEmptyTimer = null; }
    renderTop(data);
    renderLoopPanel(data);
    renderSignals(data);
    renderPositions(data);
    renderFills(data);
    syncTradeGlowLedger(data);
    renderJournal(data);
    renderPaperChrome(data);
    renderBuzz(data);
    renderHeat(data);
    renderPace(data);
    renderOpportunities(data);
    renderEdgeSample(data);
    updateDeskGuide(data);
    maybeAlertFromState(data);
    consumeDeskAlerts(data && data.alerts);
    scheduleSparks(data);
  }


  function appendChatBubble(role, text) {
    const log = $("#chat-log");
    if (!log) return;
    const div = document.createElement("div");
    div.className = `chat-bubble ${role}`;
    div.innerHTML = `<strong>${role === "user" ? "You" : "Gemini"}</strong> ${escapeHtml(text)}`;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  async function sendChat() {
    const input = $("#chat-input");
    const tickerEl = $("#chat-ticker");
    const message = (input?.value || "").trim();
    if (!message) {
      toast("Type a message first", true);
      return;
    }
    const ticker = (tickerEl?.value || "").trim().toUpperCase() || undefined;
    if (sendChat._busy) return; // each send is a paid Gemini call
    sendChat._busy = true;
    const sendBtn = $("#btn-chat-send");
    if (sendBtn) sendBtn.disabled = true;
    appendChatBubble("user", ticker ? `[${ticker}] ${message}` : message);
    input.value = "";
    try {
      const body = { message };
      if (ticker) body.ticker = ticker;
      const data = await api("/api/llm/chat", {
        method: "POST",
        body: JSON.stringify(body),
      });
      appendChatBubble("assistant", data.reply || "(empty reply)");
      if (Array.isArray(data.citations) && data.citations.length) {
        const line = data.citations.map((c) => `${c.label || c.key}: ${c.value}`).join(" | ");
        appendChatBubble("assistant", "Screener facts - " + line);
      }
      if (data.llm && !data.llm.configured) {
        toast("Add GEMINI_API_KEY to the project .env file", true);
      }
    } catch (e) {
      appendChatBubble("assistant", `Couldn't get an answer: ${e.message || e}`);
      toast("Chat didn't answer — check the AI status in Settings, then try again.", true);
    } finally {
      sendChat._busy = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  }


  function renderBrokerStatus(broker) {
    const b = broker || {};
    window.__brokerStatus = b;
    const chip = document.getElementById("broker-chip");
    const chrome = document.getElementById("chrome-broker");
    const line = document.getElementById("broker-status-line");
    const paperBadge = document.getElementById("chrome-paper");
    const pm = b.paper_mode !== false; // default true when unknown
    const label = b.connected_label || (
      b.configured
        ? (pm ? "Alpaca paper" : "Alpaca LIVE endpoint (real money)")
        : "Broker not configured"
    );
    if (chip) {
      chip.textContent = "Broker: " + label;
      chip.title = b.configured
        ? (pm
            ? "Alpaca paper API connected — fake money"
            : "Alpaca LIVE endpoint — real money; use with care")
        : "No Alpaca keys — local paper sim only";
      chip.dataset.status = b.status || (b.configured ? (pm ? "paper" : "live") : "not_configured");
      chip.dataset.paperMode = pm ? "1" : "0";
      chip.classList.toggle("ok", !!b.configured && pm);
      chip.classList.toggle("live", !!b.configured && !pm);
      chip.classList.toggle("warn", !b.configured);
    }
    if (chrome) {
      chrome.textContent = b.ui_badge || (
        b.configured ? (pm ? "ALPACA PAPER" : "LIVE ENDPOINT") : "NO BROKER"
      );
      chrome.classList.toggle("hidden", false);
      chrome.classList.toggle("ok", !!b.configured && pm);
      chrome.classList.toggle("live", !!b.configured && !pm);
      chrome.classList.toggle("off", !b.configured);
      chrome.title = label;
    }
    if (line) {
      if (b.configured && pm) {
        line.textContent =
          "Alpaca paper-api connected (ALPACA_PAPER=true). Auto+Alpaca submits to broker only on success — no dual local paper book.";
      } else if (b.configured && !pm) {
        line.textContent =
          "LIVE ENDPOINT active (ALPACA_PAPER=false → api.alpaca.markets). Real money. Orders journaled as live_submitted.";
      } else {
        line.textContent =
          "No Alpaca keys — desk stays on local paper sim. Set ALPACA_API_KEY / ALPACA_API_SECRET in .env (ALPACA_PAPER defaults true).";
      }
    }
    const note = document.getElementById("chrome-note");
    if (note) {
      const live = !!b.configured && !pm && (state?.config?.mode === "auto_live");
      note.textContent = live ? "REAL MONEY — orders go to your Alpaca account" : "Practice mode — fake money";
      note.classList.toggle("is-live", live);
    }
    document.body.classList.toggle("money-live", !!b.configured && !pm && (state?.config?.mode === "auto_live"));
    // Masthead: PAPER ONLY unless live endpoint is active
    if (paperBadge) {
      if (b.configured && !pm) {
        paperBadge.textContent = b.masthead || "LIVE ENDPOINT";
        paperBadge.classList.add("live-endpoint");
        paperBadge.title = b.masthead_title || "LIVE broker endpoint — real money";
      } else {
        paperBadge.textContent = b.masthead || "PAPER ONLY";
        paperBadge.classList.remove("live-endpoint");
        paperBadge.title =
          b.masthead_title ||
          (b.configured
            ? "Fake money — Alpaca paper API and/or local sim"
            : "Fake money — local paper sim only");
      }
    }
  }

  function brokerToastHint(broker, book, paperFallback, fill) {
    const b = broker || window.__brokerStatus || {};
    const pm = b.paper_mode !== false;
    if (book === "broker_only") {
      const where = pm ? "Alpaca paper account" : "your REAL Alpaca account";
      if (fill && fill.confirmed === false) {
        return `order sent to ${where}, but the fill isn't confirmed yet (price shown is an estimate)`;
      }
      const sh = fill && fill.shares;
      const px = fill && fill.price;
      return sh && px
        ? `filled ${sh} ${fill.ticker || ""} at ${fmtMoney(px)} in ${where}`
        : `filled in ${where}`;
    }
    if (paperFallback || book === "local_paper_fallback") {
      return "Broker failed — fell back to local paper sim";
    }
    if (book === "local_paper") {
      return pm || !b.configured
        ? "Local paper fill"
        : "Local paper fill (broker not used)";
    }
    if (b.configured && !pm) return "LIVE ENDPOINT";
    if (b.configured && pm) return "Alpaca paper";
    return "Local paper";
  }

  let refreshSeq = 0;
  let refreshInFlight = null;
  async function refresh() {
    // Coalesce: overlapping polls used to let an older /api/state land after a newer one.
    if (refreshInFlight) return refreshInFlight;
    const mySeq = ++refreshSeq;
    refreshInFlight = (async () => {
      try {
        const data = await api("/api/state", { timeoutMs: fullStateSeen ? 12000 : 30000 });
        if (mySeq !== refreshSeq) return;
        pollErrToasts = 0;
        noteFresh();
        render(data);
        warnCorruptFiles(data && data.corrupt_files);
      } finally {
        refreshInFlight = null;
      }
    })();
    try {
      await refreshInFlight;
    } catch (e) {
      console.warn("refresh failed:", e);
      // First full load failed (cold start) — try again soon instead of waiting a full poll.
      if (!fullStateSeen) setTimeout(refresh, 3000);
      const msg = e && e.message ? e.message : "Refresh failed";
      // Suppress routine poll/SSE-fallback error spam after a small threshold
      if (pollErrToasts < POLL_ERR_TOAST_CAP) {
        toast(
          msg === "state slow/timeout"
            ? "The desk is slow to answer. Your trades are safe — it will keep retrying."
            : msg,
          true
        );
        pollErrToasts += 1;
      }
    }
  }

  async function refreshCuratedMeta() {
    try {
      const data = await api("/api/watchlist/curated");
      const btn = $("#btn-restore-gf");
      if (btn) btn.classList.toggle("hidden", !data.google_finance_available);
      const note = $("#watchlist-focus-note");
      if (note && data.focused_count != null) {
        note.textContent =
          `Scan & Auto paper focus uses ${data.focused_count} ticker(s) ` +
          `(saved ${data.saved_count}, curated ${data.curated_count}). ` +
          `Full list stays saved unless you replace it.`;
      }
    } catch (_) {
      /* optional meta */
    }
  }

  // Events
  $("#mode-select")?.addEventListener("change", () => {
    const _lb = $("#live-box"); const _ms = $("#mode-select"); if (_lb && _ms) _lb.classList.toggle("hidden", _ms.value !== "auto_live");
  });

  $("#btn-apply-mode")?.addEventListener("click", async () => {
    const modeSel = $("#mode-select");
    if (!modeSel) return;
    const mode = modeSel.value;
    const body = { mode };
    if (mode === "auto_live") {
      const b0 = window.__brokerStatus || {};
      if (b0.configured && b0.paper_mode === false) {
        const typed = prompt(
          "This connects the desk to your REAL-MONEY Alpaca account.\n" +
          "Approvals and automatic trades will place real orders.\n\n" +
          "Type REAL to continue:"
        );
        if (String(typed || "").trim().toUpperCase() !== "REAL") {
          toast("Not switched — still on paper");
          return;
        }
      } else if (!confirm("Switch to Auto + Alpaca? Trades will be sent to your Alpaca paper account.")) {
        return;
      }
      // optional note only — no ENABLE LIVE AUTO unlock ceremony
      const note = ($("#live-confirm") && $("#live-confirm").value || "").trim();
      if (note) body.note = note;
      const ksLoss = Number($("#ks-loss").value) || null;
      const ksTrades = Number($("#ks-trades").value) || null;
      const ksPos = Number($("#ks-pos").value) || null;
      body.kill_switch = {
        max_daily_loss_usd: ksLoss,
        max_trades_per_day: ksTrades,
        max_position_size_usd: ksPos,
        armed: !!(ksLoss || ksTrades || ksPos),
      };
    }
    try {
      const data = await api("/api/config", { method: "POST", body: JSON.stringify(body) });
      const b = data.broker || window.__brokerStatus || {};
      let msg = `Mode set to ${data.config?.mode || mode}`;
      if (mode === "auto_live") {
        if (b.configured && b.paper_mode === false) {
          msg = "Auto + Alpaca — LIVE ENDPOINT (ALPACA_PAPER=false, real money)";
        } else if (b.configured) {
          msg = "Auto + Alpaca — paper-api (broker-only on success; no dual local book)";
        } else {
          msg = "Auto + Alpaca — no keys; will use local paper until Alpaca is configured";
        }
      }
      toast(msg, !!(mode === "auto_live" && b.configured && b.paper_mode === false));
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  });

  $$(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/config", {
          method: "POST",
          body: JSON.stringify({ risk_preset: btn.dataset.preset }),
        });
        toast(`Risk set to ${btn.dataset.preset}`);
        await refresh();
      } catch (e) {
        toast(e.message, true);
      }
    });
  });

  const dailyTargetEl = $("#daily-target");
  const beginningBankEl = $("#beginning-bank");
  if (dailyTargetEl) {
    dailyTargetEl.addEventListener("input", updateStartEnabled);
    dailyTargetEl.addEventListener("change", updateStartEnabled);
  }
  if (beginningBankEl) {
    beginningBankEl.addEventListener("input", () => {
      beginningBankEl.dataset.touched = "1";
      updateStartEnabled();
    });
    beginningBankEl.addEventListener("change", updateStartEnabled);
  }
  updateStartEnabled();

  $("#btn-start-session")?.addEventListener("click", async () => {
    const make = parsePositiveMoney($("#daily-target")?.value);
    const bank = parsePositiveMoney($("#beginning-bank")?.value);
    if (!make || !bank) {
      toast("Enter a goal and starting cash (both over 0)", true);
      updateStartEnabled();
      return;
    }
    const startBtn = $("#btn-start-session");
    if (startBtn?.dataset.busy === "1") return;
    const curMode = state?.config?.mode;
    const body = {
      make_today_usd: make,
      beginning_bank_usd: bank,
    };
    // Keep the user's fill choice (Ask me first stays Ask me first).
    if (curMode === "manual" || curMode === "auto_paper") body.mode = curMode;
    const send = () => api("/api/session/start", {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: 30000,
    });
    if (startBtn) { startBtn.dataset.busy = "1"; startBtn.disabled = true; }
    try {
      let data;
      try {
        data = await send();
      } catch (e) {
        if (e.status === 409 && e.data?.needs_confirm) {
          const d = e.data;
          const lines = [];
          if (d.open_positions) lines.push(`• ${d.open_positions} open paper position(s) will be closed out`);
          if (d.fills_today) lines.push(`• ${d.fills_today} trade(s) move to history`);
          if (!confirm(`Start a fresh paper day?\n\n${lines.join("\n")}\n\nYour new starting cash will be ${fmtMoney(bank)}.`)) {
            toast("Start cancelled — nothing changed");
            return;
          }
          body.confirm_reset = true;
          data = await send();
        } else {
          throw e;
        }
      }
      const m = data?.config?.mode || curMode;
      if ($("#mode-select") && m) $("#mode-select").value = m;
      if (window.EquityScoreboard && typeof window.EquityScoreboard.reset === "function") {
        window.EquityScoreboard.reset();
      }
      const how = m === "auto_paper" ? "Auto fill" : m === "auto_live" ? "Auto + Alpaca" : "Ask me first";
      let msg = `Checking started (${how}) — goal ${fmtMoney(make)}, starting with ${fmtMoney(bank)} of ${moneyNoun(data?.config)}`;
      if (data.signal?.ticker) {
        msg += ` · first look: ${data.signal.ticker}`;
      }
      toast(msg);
      await refresh();
    } catch (e) {
      toast(e.message, true);
    } finally {
      if (startBtn) { delete startBtn.dataset.busy; startBtn.disabled = false; updateStartEnabled(); }
    }
  });

  async function stopSessionHandler() {
    try {
      await stopSession();
    } catch (e) {
      toast(e.message, true);
    }
  }
  const stopBtn = $("#btn-stop-session");
  if (stopBtn) stopBtn.addEventListener("click", stopSessionHandler);

  // --- P1.7 NL watchlist find ---
  let wlFindInFlight = false;
  let wlFindLastSuggest = [];

  function highlightWatchlistMatches(matched) {
    const ta = $("#watchlist");
    if (!ta) return;
    const set = new Set((matched || []).map((t) => String(t).toUpperCase()));
    if (!set.size) {
      ta.classList.remove("wl-find-highlight");
      return;
    }
    ta.classList.add("wl-find-highlight");
    // Scroll textarea to first matched line when possible
    const lines = String(ta.value || "").split(/\n/);
    let pos = 0;
    let foundAt = -1;
    for (const line of lines) {
      const toks = line.toUpperCase().split(/[\s,;]+/).filter(Boolean);
      if (toks.some((t) => set.has(t.replace(/^(NASDAQ|NYSE|NYSEARCA|AMEX|OTC):/, "")))) {
        foundAt = pos;
        break;
      }
      pos += line.length + 1;
    }
    if (foundAt >= 0) {
      try {
        ta.focus();
        ta.setSelectionRange(foundAt, foundAt);
      } catch (_) { /* ignore */ }
    }
    clearTimeout(highlightWatchlistMatches._t);
    highlightWatchlistMatches._t = setTimeout(() => ta.classList.remove("wl-find-highlight"), 4200);
  }

  function renderWlFindResults(data) {
    const box = $("#wl-find-results");
    if (!box) return;
    const matched = data.matched || [];
    const suggest = data.suggest || [];
    wlFindLastSuggest = suggest.slice();
    const source = data.source || "local";
    const note = data.note || "";
    if (!matched.length && !suggest.length) {
      box.classList.remove("hidden");
      box.innerHTML = `<p class="wl-find-note">${escapeHtml(note || "No matches.")} <span class="muted">(${escapeHtml(source)})</span></p>`;
      return;
    }
    const matchChips = matched
      .map((t) => `<span class="wl-find-chip matched" title="On watchlist">${escapeHtml(t)}</span>`)
      .join("");
    const suggestChips = suggest
      .map((t) => `<button type="button" class="wl-find-chip suggest" data-add-ticker="${escapeHtml(t)}" title="Add to list">${escapeHtml(t)}</button>`)
      .join("");
    let html = `<p class="wl-find-note">${escapeHtml(note)} <span class="muted">· ${escapeHtml(source)}</span></p>`;
    if (matched.length) {
      html += `<div class="wl-find-row"><span class="wl-find-label">Matched (${matched.length})</span>${matchChips}</div>`;
    }
    if (suggest.length) {
      html += `<div class="wl-find-row"><span class="wl-find-label">Suggest</span>${suggestChips}`;
      html += `<button type="button" class="btn ghost sm wl-find-add-all" id="btn-wl-find-add-all">Add to list</button></div>`;
    }
    box.classList.remove("hidden");
    box.innerHTML = html;
    highlightWatchlistMatches(matched);
  }

  async function mergeSuggestedTickers(tickers) {
    const list = (tickers || []).map((t) => String(t).toUpperCase()).filter(Boolean);
    if (!list.length) {
      toast("Nothing to add");
      return;
    }
    try {
      const data = await api("/api/watchlist/import", {
        method: "POST",
        body: JSON.stringify({ text: list.join(" "), mode: "merge", source: "paste" }),
      });
      $("#watchlist").value = (data.watchlist || []).join("\n");
      renderWatchlistCount(data.watchlist || []);
      toast(`Added ${data.added} ticker${data.added === 1 ? "" : "s"} (merged)`);
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function runWatchlistFind() {
    if (wlFindInFlight) return;
    const input = $("#wl-find-q");
    const btn = $("#btn-wl-find");
    const q = (input?.value || "").trim();
    if (!q) {
      toast("Enter a find query", true);
      return;
    }
    wlFindInFlight = true;
    if (btn) btn.disabled = true;
    if (input) input.disabled = true;
    try {
      const data = await api("/api/watchlist/find", {
        method: "POST",
        body: JSON.stringify({ q }),
        timeoutMs: 20000,
      });
      renderWlFindResults(data || {});
    } catch (e) {
      toast(e.message || "Find failed", true);
    } finally {
      wlFindInFlight = false;
      if (btn) btn.disabled = false;
      if (input) input.disabled = false;
    }
  }

  $("#btn-wl-find")?.addEventListener("click", () => runWatchlistFind());
  $("#wl-find-q")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      runWatchlistFind();
    }
  });
  $("#wl-find-results")?.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t instanceof Element)) return;
    if (t.id === "btn-wl-find-add-all") {
      mergeSuggestedTickers(wlFindLastSuggest);
      return;
    }
    const chip = t.closest("[data-add-ticker]");
    if (chip) {
      const sym = chip.getAttribute("data-add-ticker");
      if (sym) mergeSuggestedTickers([sym]);
    }
  });

  $("#btn-save-watchlist")?.addEventListener("click", async () => {
    try {
      await api("/api/config", {
        method: "POST",
        body: JSON.stringify({ watchlist: $("#watchlist").value }),
      });
      toast("Watchlist saved");
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  });

  async function setWatchlistFocus(focus) {
    try {
      await api("/api/config", {
        method: "POST",
        body: JSON.stringify({ watchlist_focus: focus }),
      });
      toast(focus === "liquid" ? "Focus: liquid US" : "Focus: full list");
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  }

  const btnUseLiquid = $("#btn-use-liquid");
  if (btnUseLiquid) {
    btnUseLiquid.addEventListener("click", async () => {
      try {
        const data = await api("/api/watchlist/use-liquid", { method: "POST", body: "{}" });
        $("#watchlist").value = (data.watchlist || []).join("\n");
        renderWatchlistCount(data.watchlist || []);
        toast(`Liquid US list loaded (${data.total || 0})`);
        await refresh();
        await refreshCuratedMeta();
      } catch (e) {
        toast(e.message, true);
      }
    });
  }
  const btnFocusLiquid = $("#btn-focus-liquid");
  if (btnFocusLiquid) btnFocusLiquid.addEventListener("click", () => setWatchlistFocus("liquid"));
  const btnFocusAll = $("#btn-focus-all");
  if (btnFocusAll) btnFocusAll.addEventListener("click", () => setWatchlistFocus("all"));
  const btnRestoreGf = $("#btn-restore-gf");
  if (btnRestoreGf) {
    btnRestoreGf.addEventListener("click", async () => {
      try {
        const data = await api("/api/watchlist/restore-google-finance", { method: "POST", body: "{}" });
        $("#watchlist").value = (data.watchlist || []).join("\n");
        renderWatchlistCount(data.watchlist || []);
        toast(`Restored Google Finance list (${data.total || 0})`);
        await refresh();
        await refreshCuratedMeta();
      } catch (e) {
        toast(e.message, true);
      }
    });
  }

  async function importWatchlist(mode) {
    try {
      const data = await api("/api/watchlist/import", {
        method: "POST",
        body: JSON.stringify({ text: $("#watchlist").value, mode, source: "paste" }),
      });
      $("#watchlist").value = (data.watchlist || []).join("\n");
      renderWatchlistCount(data.watchlist || []);
      toast(`${mode === "merge" ? "Merged" : "Replaced"} ${data.added} ticker${data.added === 1 ? "" : "s"}`);
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function updateChurn(results) {
    const qualifying = new Set(
      (results || [])
        .filter((r) => ["PASS", "WATCH"].includes(String(r.verdict || "").toUpperCase()))
        .map((r) => String(r.ticker || "").toUpperCase())
        .filter(Boolean)
    );
    const prev = prevQualifySet;
    const added = [...qualifying].filter((t) => !prev.has(t));
    const kept = [...qualifying].filter((t) => prev.has(t));
    const fell = [...prev].filter((t) => !qualifying.has(t));
    prevQualifySet = qualifying;
    const strip = $("#churn-strip");
    if (strip) {
      const chips = [
        ...added.map((t) => `<span class="churn-chip new">new ${escapeHtml(t)}</span>`),
        ...kept.slice(0, 12).map((t) => `<span class="churn-chip still">still ${escapeHtml(t)}</span>`),
        ...fell.map((t) => `<span class="churn-chip fell">off ${escapeHtml(t)}</span>`),
      ];
      if (chips.length) {
        strip.innerHTML = chips.join("");
        strip.classList.remove("hidden");
      }
    }
    return { added: new Set(added), kept: new Set(kept), fell: new Set(fell) };
  }

  function renderWatchlistResults(results) {
    const el = $("#watchlist-results");
    if (!el) return;
    if (!results?.length) {
      el.innerHTML = `<div class="empty">No tickers to evaluate</div>`;
      return;
    }
    const churn = updateChurn(results);
    el.innerHTML = results.map((item) => {
      const verdict = item.verdict || "-";
      const side = item.llm_side || "-";
      const confidence = item.llm_confidence != null ? `${Math.round(Number(item.llm_confidence) * 100)}%` : "-";
      const detail = item.error
        ? `<span class="watchlist-error">${escapeHtml(item.error)}</span>`
        : `${escapeHtml(side)} | ${escapeHtml(confidence)}${item.llm_thesis ? ` | ${escapeHtml(item.llm_thesis)}` : ""}`;
      const verdictClass = ["PASS", "WATCH", "AVOID"].includes(String(verdict).toUpperCase())
        ? String(verdict).toLowerCase()
        : "watch";
      const t = String(item.ticker || "").toUpperCase();
      let churnCls = "";
      if (churn.added.has(t)) churnCls = "churn-new";
      else if (churn.kept.has(t)) churnCls = "churn-still";
      const gapBadge = (item.research_flags || []).includes("gap_gt_8pct") || (item.gap_pct != null && Math.abs(Number(item.gap_pct)) > 8)
        ? `<span class="badge badge-gap">GAP</span>` : "";
      const haltBadge = (item.research_flags || []).includes("halt_detected")
        ? `<span class="badge badge-halt">HALT</span>` : "";
      return `<div class="watchlist-result ${churnCls}"><strong>${escapeHtml(item.ticker)}</strong><span class="badge badge-${verdictClass}">${escapeHtml(verdict)}</span>${gapBadge}${haltBadge}<span>${detail}</span>${citationsHtml(item.citations || [])}</div>`;
    }).join("");
    if (churn.fell.size) {
      el.innerHTML += [...churn.fell].map((t) =>
        `<div class="watchlist-result churn-fell"><strong>${escapeHtml(t)}</strong><span class="badge">fell off</span></div>`
      ).join("");
    }
  }

  $("#btn-merge-watchlist")?.addEventListener("click", () => importWatchlist("merge"));
  $("#btn-replace-watchlist")?.addEventListener("click", () => importWatchlist("replace"));

  $("#btn-evaluate-watchlist")?.addEventListener("click", async () => {
    const text = $("#watchlist").value.trim();
    try {
      const body = text ? { tickers: text } : {};
      const nTickers = text
        ? text.split(/[\s,;]+/).filter(Boolean).length
        : (state?.config?.watchlist || []).length;
      toast(`Scoring up to 25 of ${nTickers}...`);
      const data = await api("/api/watchlist/evaluate", {
        method: "POST",
        body: JSON.stringify(body),
        timeoutMs: 180000,
      });
      renderWatchlistResults(data.results || []);
      const scored = data.scored ?? (data.results || []).length;
      const total = data.total ?? scored;
      let msg = `Scored ${scored} ticker${scored === 1 ? "" : "s"}`;
      if (data.capped) msg += ` (capped; ${total} total)`;
      toast(msg);
    } catch (e) {
      toast(e.message, true);
    }
  });

  $("#watchlist")?.addEventListener("input", (ev) => renderWatchlistCount(ev.target.value));

  $("#btn-gen")?.addEventListener("click", async () => {
    try {
      const data = await api("/api/signals/generate", { method: "POST", body: "{}" });
      toast(`Signal ${data.signal?.ticker} ${data.signal?.side} (${data.signal?.status})`);
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  });

  $("#btn-reset-ledger")?.addEventListener("click", async () => {
    if (!confirm("Reset the paper account to your starting cash?")) return;
    try {
      await api("/api/ledger/reset", { method: "POST", body: "{}" });
      toast("Paper account reset");
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  });

  $("#btn-refresh")?.addEventListener("click", () => refresh());

  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      activeTab = tab.dataset.tab;
      $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      if (state) renderSignals(state);
    });
  });

  function findSignalById(id) {
    const bag = state?.signals || {};
    for (const key of Object.keys(bag)) {
      const hit = (bag[key] || []).find((s) => s.id === id);
      if (hit) return hit;
    }
    return (state?.opportunities || []).find((o) => o.id === id) || null;
  }

  function openApprovePreview(id) {
    if (!id || String(id).startsWith("loop-")) {
      toast("That idea is research-only — not ready to Approve", true);
      return;
    }
    const s = findSignalById(id);
    if (!s || (s.source && s.source !== "pending") || String(s.id || "").startsWith("loop-")) {
      toast("Signal not found", true);
      return;
    }
    pendingApproveId = id;
    const cfg = state?.config || {};
    const live = realMoney(cfg);
    const broker = brokerMode(cfg);
    const el = $("#approve-preview");
    if (el) {
      const thesis = plainify(s.why_plain || s.llm_thesis || s.thesis || s.reason || "Idea waiting on you");
      const sh = Number(s.suggested_shares || 0);
      const px = Number(s.signal_price || 0);
      const total = sh && px ? sh * px : null;
      const cash = Number(state?.ledger?.cash);
      const verb = String(s.side || "").toLowerCase() === "sell" ? "Sell" : "Buy";
      const maths = total != null
        ? `${verb} ${sh} share${sh === 1 ? "" : "s"} × ${fmtMoney(px)} ≈ <strong>${fmtMoney(total)}</strong> of ${escapeHtml(moneyNoun(cfg))}`
        : `${verb} ${escapeHtml(String(s.suggested_shares ?? "-"))} shares`;
      const left = !broker && verb === "Buy" && total != null && Number.isFinite(cash)
        ? `<div class="row"><span class="k">After</span><span>Leaves about ${fmtMoney(Math.max(0, cash - total))} of your ${fmtMoney(cash)} paper cash</span></div>`
        : "";
      const warn = live
        ? `<div class="approve-live-warn" role="alert"><strong>REAL MONEY.</strong> This sends a real market order to your Alpaca account. The fill price can differ from ${fmtMoney(px)}.</div>`
        : broker
          ? `<div class="approve-broker-note">Sends an order to your <strong>Alpaca paper</strong> account (fake money at the broker, not this desk's paper book).</div>`
          : "";
      el.innerHTML = `
        ${warn}
        <div class="row"><span class="k">Stock</span><span>${escapeHtml(s.ticker)}</span></div>
        <div class="row"><span class="k">Trade</span><span>${maths}</span></div>
        ${left}
        <div class="row"><span class="k">Why</span><span>${escapeHtml(String(thesis).slice(0, 160))}</span></div>
      `;
    }
    const titleEl = $("#approve-modal-title");
    if (titleEl) {
      titleEl.textContent = live ? "Send a REAL order?" : broker ? "Send order to Alpaca paper?" : "Place this paper trade?";
    }
    const cbtn = $("#btn-approve-confirm");
    if (cbtn) {
      const verb = String(s.side || "").toLowerCase() === "sell" ? "Sell" : "Buy";
      cbtn.textContent = live ? `${verb} with real money` : broker ? `${verb} on Alpaca paper` : `${verb} on paper`;
      cbtn.classList.toggle("danger", live);
      cbtn.title = live ? "Sends a real-money market order" : "Fake money — practice trade";
      cbtn.disabled = false;
    }
    $("#approve-modal")?.classList.toggle("is-live", live);
    // P0.4 quiet stop / TP — blank → fill_px + 0.8%×stop_r geometry
    const stopIn = $("#approve-stop");
    const tpIn = $("#approve-tp");
    const noBr = $("#approve-no-bracket");
    const gloss = $("#approve-bracket-gloss");
    if (stopIn) stopIn.value = "";
    if (tpIn) tpIn.value = "";
    const trIn = $("#approve-trail");
    if (trIn) trIn.value = "";
    if (noBr) noBr.checked = false;
    const preset = (state && state.preset) || {};
    const stopR = preset.stop_r != null ? Number(preset.stop_r) : 1;
    const tgtR = preset.target_r != null ? Number(preset.target_r) : 2.5;
    const stopPct = (0.8 * stopR);
    const tgtPct = stopPct * tgtR;
    if (gloss) {
      gloss.textContent = `Optional paper stop & take-profit — blank uses ~${stopPct.toFixed(1)}% / ~${tgtPct.toFixed(1)}% from fill. Use % suffix for percents (e.g. 1%).`;
    }
    const modal = $("#approve-modal");
    if (modal) {
      modal.classList.remove("hidden");
      modal.dataset.returnFocus = document.activeElement && document.activeElement.id
        ? document.activeElement.id
        : "";
      const confirmBtn = $("#btn-approve-confirm");
      if (confirmBtn) confirmBtn.focus();
    }
  }

  function closeApproveModal() {
    const modal = $("#approve-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    const rid = modal.dataset.returnFocus;
    if (rid) {
      const prev = document.getElementById(rid);
      if (prev && typeof prev.focus === "function") prev.focus();
    }
    modal.dataset.returnFocus = "";
  }

  async function confirmApprove() {
    if (!pendingApproveId) return;
    const id = pendingApproveId;
    pendingApproveId = null;
    const stopVal = ($("#approve-stop") && $("#approve-stop").value || "").trim();
    const tpVal = ($("#approve-tp") && $("#approve-tp").value || "").trim();
    const noBracket = !!( $("#approve-no-bracket") && $("#approve-no-bracket").checked );
    const cbtn = $("#btn-approve-confirm");
    if (cbtn) cbtn.disabled = true; // no double submit
    closeApproveModal();
    const body = {};
    if (noBracket) {
      body.bracket_off = true;
      body.no_bracket = true;
    } else {
      if (stopVal) body.stop_loss = stopVal;
      if (tpVal) body.take_profit = tpVal;
      const trVal = ($("#approve-trail") && $("#approve-trail").value || "").trim();
      if (trVal) body.trail_pct = trVal;
      // blank both → server uses risk-preset defaults
    }
    try {
      // Broker path can take ~20 s (submit + fill confirmation) — don't give up at 12 s.
      const appr = await api(`/api/signals/${id}/approve`, {
        method: "POST",
        body: JSON.stringify(body),
        timeoutMs: 35000,
      });
      const hint = brokerToastHint(appr.broker || window.__brokerStatus, appr.book, appr.paper_fallback, appr.fill);
      const warns = (appr.fill && appr.fill.exit_warnings) || [];
      toast(
        (noBracket ? `Approved — ${hint} (no auto-exit)` : `Approved — ${hint}`) +
          (warns.length ? ` · Note: ${warns[0]}` : ""),
        !!(appr.fill && appr.fill.confirmed === false)
      );
      await refresh();
    } catch (e) {
      if (e.timeout) {
        toast("No answer yet — the order MAY have gone through. Check Positions before trying again.", true);
        setTimeout(refresh, 4000);
      } else {
        toast(e.message, true);
      }
    } finally {
      if (cbtn) cbtn.disabled = false;
    }
  }

  $("#btn-approve-confirm")?.addEventListener("click", () => confirmApprove());
  $("#btn-approve-cancel")?.addEventListener("click", () => {
    pendingApproveId = null;
    closeApproveModal();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const howto = $("#howto-modal");
    if (howto && !howto.classList.contains("hidden")) {
      closeHowto();
      return;
    }
    const modal = $("#approve-modal");
    if (!modal || modal.classList.contains("hidden")) return;
    pendingApproveId = null;
    closeApproveModal();
  });

  $("#signal-list")?.addEventListener("click", async (ev) => {
    const approve = ev.target.closest("[data-approve]");
    const reject = ev.target.closest("[data-reject]");
    if (approve) {
      openApprovePreview(approve.dataset.approve);
    }
    if (reject) skipWithUndo(reject.dataset.reject, "Skipped by you", reject.closest(".opp-card, .signal-card, article, li"));
  });

  // Skip waits 5 s before telling the server, so a mis-tap can be undone.
  const pendingSkips = new Map();
  function skipWithUndo(id, reason, cardEl) {
    if (!id || pendingSkips.has(id)) return;
    if (cardEl) cardEl.classList.add("is-skipping");
    const bar = $("#undo-bar");
    const finish = async () => {
      pendingSkips.delete(id);
      if (bar) bar.hidden = true;
      try {
        await api(`/api/signals/${id}/reject`, { method: "POST", body: JSON.stringify({ reason }) });
      } catch (e) {
        if (cardEl) cardEl.classList.remove("is-skipping");
        toast(e.message, true);
      }
      await refresh();
    };
    const t = setTimeout(finish, 5000);
    pendingSkips.set(id, t);
    if (bar) {
      const txt = bar.querySelector("[data-undo-text]");
      const s = findSignalById(id);
      if (txt) txt.textContent = `Skipped ${s?.ticker || "idea"}.`;
      bar.hidden = false;
      const btn = bar.querySelector("[data-undo-btn]");
      if (btn) {
        btn.onclick = () => {
          clearTimeout(t);
          pendingSkips.delete(id);
          bar.hidden = true;
          if (cardEl) cardEl.classList.remove("is-skipping");
          toast("Undone — the idea is still waiting");
        };
        btn.focus();
      }
    } else {
      toast("Skipped — skipping is fine");
    }
  }

  const chatSend = $("#btn-chat-send");
  const chatInput = $("#chat-input");
  if (chatSend) chatSend.addEventListener("click", () => sendChat());
  if (chatInput) {
    chatInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        sendChat();
      }
    });
  }


  let loopFeedSeq = 0;
  let loopEvents = [];

  function noteTradeGlowFromEvent(e) {
    try {
      if (e && (e.event === "fill" || e.filled) && window.DeskMotion && typeof window.DeskMotion.pulseFill === "function") {
        window.DeskMotion.pulseFill(e.side || e.decision || "buy");
      }
    } catch (_) {}

    try {
      if (window.TradeGlow && typeof window.TradeGlow.noteLoopEvent === "function") {
        window.TradeGlow.noteLoopEvent(e);
      }
    } catch (_) {}
    try {
      if (window.DeskPulse && typeof window.DeskPulse.noteLoopEvent === "function") {
        window.DeskPulse.noteLoopEvent(e);
      }
    } catch (_) {}
  }

  function syncTradeGlowLedger(data) {
    try {
      const fills = (data && data.ledger && data.ledger.fills) || [];
      if (window.TradeGlow && typeof window.TradeGlow.syncLedger === "function") {
        window.TradeGlow.syncLedger(fills);
      }
    } catch (_) {}
    try {
      if (window.DeskPulse && typeof window.DeskPulse.syncFromState === "function") {
        window.DeskPulse.syncFromState(data);
      }
      if (window.EquityScoreboard && typeof window.EquityScoreboard.syncFromState === "function") {
        window.EquityScoreboard.syncFromState(data);
      }
    } catch (_) {}
  }

  function isOutsideRth(loop, cfg) {
    const L = loop || {};
    // Match chrome: closed when rth_ok === false or explicit outside_rth
    return !!L.outside_rth || L.rth_ok === false;
  }

    function syncTradeGlowAfterHours(data) {
    try {
      const loop = (data && data.loop) || {};
      const cfg = (data && data.config) || {};
      const outside = isOutsideRth(loop, cfg);
      if (window.TradeGlow && typeof window.TradeGlow.setAfterHours === "function") {
        window.TradeGlow.setAfterHours(outside);
      }
      if (window.DeskPulse && typeof window.DeskPulse.setAfterHours === "function") {
        window.DeskPulse.setAfterHours(outside);
      }
      // DeskPulse + EquityScoreboard sync owned by syncTradeGlowLedger (deduped)
      syncSimpleAmbient(data);
    } catch (_) {}
  }


  function fmtTimeShort(iso) {
    if (!iso) return "-";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return "-";
    }
  }

  function setProbs(probs) {
    const buy = Math.round(((probs && probs.buy) || 0) * 100);
    const sell = Math.round(((probs && probs.sell) || 0) * 100);
    const flat = Math.round(((probs && probs.flat) || 0) * 100);
    const eb = $("#prob-buy"), es = $("#prob-sell"), ef = $("#prob-flat");
    if (eb) eb.style.width = `${buy}%`;
    if (es) es.style.width = `${sell}%`;
    if (ef) ef.style.width = `${flat}%`;
    const pb = $("#prob-buy-pct"), ps = $("#prob-sell-pct"), pf = $("#prob-flat-pct");
    if (pb) pb.textContent = `${buy}%`;
    if (ps) ps.textContent = `${sell}%`;
    if (pf) pf.textContent = `${flat}%`;
  }


  function confPlain(conf) {
    const c = Number(conf);
    if (!Number.isFinite(c) || c <= 0) return "—";
    if (c < 0.4) return "low";
    if (c < 0.7) return "medium";
    return "high";
  }

  function gateWhyPlain(last, loop, cfg) {
    if (!last && (!cfg || !cfg.session_active)) {
      return "Not checking right now — press Start checking when you're ready.";
    }
    if (!last) return "";
    const err = String(last.error || last.reason || "").toLowerCase();
    const thesis = String(last.thesis || last.llm_thesis || "").trim();
    const late = !!(last.late || (last.lateness_label || "").toLowerCase().match(/late|chasing/));
    const low = !!(last.low_confidence || err.includes("low_confidence"));
    const gated = err.includes("gated") || thesis.toLowerCase().startsWith("gated before");
    const bits = [];
    if (low) {
      const minC = last.min_decision_confidence != null ? Number(last.min_decision_confidence) : null;
      const c = Number(last.confidence || 0);
      bits.push(
        minC != null && Number.isFinite(minC)
          ? `The AI wasn't sure enough (${Math.round(c * 100)}% — it needs ${Math.round(minC * 100)}%), so the desk waited instead of guessing.`
          : "The AI wasn't sure enough, so the desk waited instead of guessing."
      );
    }
    if (/side_horizon_conflict|bad_confidence|parse_error/.test(err)) {
      bits.push("The AI's answer didn't add up, so the desk held rather than guess.");
    }
    if (late) bits.push("The price already moved a lot — buying now would mean chasing it.");
    if (gated) {
      const g = thesis || err;
      if (/rel.?vol|relative.?vol|rvol|volume/i.test(g)) {
        bits.push("Trading is quiet today — not enough activity for a clean entry.");
      } else if (/verdict_avoid|avoid/i.test(g)) {
        bits.push("The screener says skip this one for now.");
      } else if (/lateness/i.test(g)) {
        bits.push("The price already moved — skipped before asking the AI (saves money).");
      } else if (/llm_on_scan_disabled/i.test(g)) {
        bits.push("AI checks are switched off in Settings, so the desk is holding.");
      } else {
        bits.push("A safety check said wait, so the desk didn't ask the AI.");
      }
    }
    if (!bits.length && thesis) {
      const pt = plainify(thesis);
      bits.push(pt.length > 180 ? pt.slice(0, 178) + "…" : pt);
    }
    if (!bits.length && last.abstain) {
      bits.push("Nothing clear yet — waiting is fine.");
    }
    const skip = (loop && loop.last_skip) || "";
    if (!bits.length && skip === "outside_rth") {
      bits.push("The market is closed. Checks pause until it opens (9:30 am – 4 pm Eastern, weekdays).");
    }
    return bits[0] || "";
  }

  function syncStageGoalRace(data) {
    const daily = (data && data.daily) || {};
    const pace = (data && data.pace) || daily.pace || {};
    const pct = Number(daily.progress_pct != null ? daily.progress_pct : 0);
    const rem = daily.remaining_usd;
    const remEl = $("#stage-race-rem");
    if (remEl) {
      if (daily.target_usd == null) remEl.textContent = "no goal set";
      else if (daily.target_hit) remEl.textContent = `goal of ${fmtMoney(daily.target_usd)} reached`;
      else {
        const made = Math.max(0, Number(daily.target_usd) - Number(rem ?? daily.target_usd));
        remEl.textContent = `${fmtMoney(made)} of ${fmtMoney(daily.target_usd)} goal · ${rem != null ? fmtMoney(rem) : "—"} to go`;
      }
    }
    const paceEl = $("#stage-race-pace");
    if (paceEl && pace && pace.expected_pct != null) {
      paceEl.style.left = Math.max(0, Math.min(100, Number(pace.expected_pct))) + "%";
    }
    try {
      if (window.DeskMotion && typeof window.DeskMotion.setGoalProgress === "function") {
        window.DeskMotion.setGoalProgress(pct, pct >= 80 || !!daily.target_hit);
      } else {
        const fill = $("#stage-race-fill");
        if (fill) fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
      }
    } catch (_) {}
    // Also mirror header race if present
    const tf = $("#target-fill");
    if (tf) tf.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }

  function renderSpokenCall(data, last, decision, conf, ticker, isAvoid) {
    const simpleUi = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
    const lineEl = $("#spoken-line");
    const whyEl = $("#spoken-why");
    const confLabel = $("#loop-action-conf-label");
    const howBody = $("#how-call-body");
    const howEmpty = $("#how-call-empty");
    const newsMatch = $("#spoken-news-match");
    const label = decision === "buy" ? "Buy" : decision === "sell" ? "Sell" : isAvoid ? "Skip" : "Hold";
    const confWord = confPlain(conf);
    if (confLabel) confLabel.textContent = confWord === "—" ? "—" : confWord;
    const loop = (data && data.loop) || {};
    const cfg = (data && data.config) || {};
    const why = gateWhyPlain(last, loop, cfg);
    if (simpleUi && lineEl) {
      if (!last) {
        lineEl.innerHTML = "Standing by — press <em>Start</em> when you wish";
      } else {
        const sym = ticker ? escapeHtml(String(ticker).toUpperCase()) : "this stock";
        const sure = surePhrase(conf);
        // Say what the call means. "High confidence" on a Hold is about doing NOTHING.
        if (decision === "buy" || decision === "sell") {
          lineEl.innerHTML = "<em>" + escapeHtml(label) + " " + sym + "</em> looks worth it" +
            (sure ? " — the AI is " + escapeHtml(sure) + "." : ".");
        } else {
          lineEl.innerHTML = "<em>Skip " + sym + " for now.</em>" +
            (sure ? " The AI is " + escapeHtml(sure) + " that doing nothing is best." : " No clear chance right now.");
        }
      }
    }
    if (whyEl) {
      if (simpleUi && why) {
        whyEl.textContent = why;
        whyEl.hidden = false;
      } else {
        whyEl.textContent = "";
        whyEl.hidden = true;
      }
    }
    // How this call was made
    if (howBody) {
      if (!last) {
        howBody.innerHTML = '<p class="muted" id="how-call-empty">No decision yet — details appear after the first check.</p>';
      } else {
        const brain = last.brain_mode || (data.brain_mode) || (cfg.brain_mode) || "—";
        const horizon = last.horizon || (last.horizon_min != null ? last.horizon_min + " min" : null);
        const minC = last.min_decision_confidence;
        const probs = last.probs || {};
        const rows = [];
        const brainName = { gemini: "Gemini (Google's AI)", mock: "Practice brain (no AI)", jev: "Jev AI" }[String(brain).toLowerCase()] || String(brain);
        rows.push('<div class="how-row"><span class="how-k">Which AI decided</span><span>' + escapeHtml(brainName) + "</span></div>");
        if (horizon) rows.push('<div class="how-row"><span class="how-k">Look-ahead window</span><span>' + escapeHtml(String(horizon)) + "</span></div>");
        // P1 dual confidence honesty: research (loop) vs fill-gate (blended) when both present
        const researchConf = Number(
          last.llm_confidence != null ? last.llm_confidence
            : (last.confidence != null ? last.confidence : conf)
        );
        const gateConf = last.gate_confidence != null ? Number(last.gate_confidence)
          : (last.signal_confidence != null ? Number(last.signal_confidence) : null);
        const minTxt = minC != null ? " vs min " + Math.round(Number(minC) * 100) + "%" : "";
        if (gateConf != null && Number.isFinite(gateConf) && Math.abs(gateConf - researchConf) >= 0.005) {
          rows.push('<div class="how-row"><span class="how-k">Research confidence</span><span>' + Math.round((researchConf || 0) * 100) + "%" + minTxt + "</span></div>");
          rows.push('<div class="how-row"><span class="how-k">Fill gate confidence</span><span>' + Math.round(gateConf * 100) + "% (blended with playbook)</span></div>");
        } else {
          rows.push('<div class="how-row"><span class="how-k">How sure</span><span>' + Math.round((researchConf || 0) * 100) + "%" + minTxt + ' <span class="muted">— not a chance of profit</span></span></div>');
        }
        const pb = Number(probs.buy), ps = Number(probs.sell), pf = Number(probs.flat);
        if ([pb, ps, pf].some((x) => Number.isFinite(x) && x > 0)) {
          rows.push('<div class="how-row"><span class="how-k">Out of 100 tries</span><span>Buy ' + Math.round((pb || 0) * 100) + " · Sell " + Math.round((ps || 0) * 100) + " · Do nothing " + Math.round((pf || 0) * 100) + "</span></div>");
        }
        if (last.lateness_label) rows.push('<div class="how-row"><span class="how-k">Timing</span><span>' + escapeHtml(timingGloss(last.lateness_label)) + "</span></div>");
        if (last.verdict) rows.push('<div class="how-row"><span class="how-k">Screener says</span><span>' + escapeHtml(verdictGloss(last.verdict) || String(last.verdict)) + ' <span class="muted">(' + escapeHtml(String(last.verdict)) + ")</span></span></div>");
        if (last.macro_force_ask_first || last.macro_forced_pending) {
          rows.push('<div class="how-row"><span class="how-k">Big news day</span><span>Fed / jobs / inflation / earnings today — so this waits for your OK' + (last.macro_butler_note ? " — " + escapeHtml(String(last.macro_butler_note).slice(0, 80)) : "") + "</span></div>");
        } else if (last.macro_size_mult != null && Number(last.macro_size_mult) < 1) {
          rows.push('<div class="how-row"><span class="how-k">Big news day</span><span>Trading smaller: ' + Math.round(Number(last.macro_size_mult) * 100) + "% of the usual size" + (last.macro_butler_note ? " — " + escapeHtml(String(last.macro_butler_note).slice(0, 80)) : "") + "</span></div>");
        } else if (last.macro_butler_note) {
          rows.push('<div class="how-row"><span class="how-k">Macro</span><span>' + escapeHtml(String(last.macro_butler_note).slice(0, 100)) + "</span></div>");
        }
        howBody.innerHTML = rows.join("");
      }
    }
    // Optional matching headline under Why
    if (newsMatch) {
      let hit = null;
      try {
        const items = ((data && data.watchlist_news) || {}).items || [];
        const tU = String(ticker || "").toUpperCase();
        const th = String((last && (last.thesis || last.llm_thesis)) || "").toLowerCase();
        if (tU && /news|catalyst|headline|earnings|fed|cpi/i.test(th)) {
          hit = items.find((n) => String(n.ticker || "").toUpperCase() === tU) || null;
        }
        if (!hit && tU) {
          // soft: show top headline for focus if thesis present
          if (last && th) hit = items.find((n) => String(n.ticker || "").toUpperCase() === tU) || null;
        }
      } catch (_) {}
      if (simpleUi && hit && hit.title) {
        const href = safeUrl(hit.link || hit.url);
        const title = escapeHtml(String(hit.title).slice(0, 100));
        newsMatch.innerHTML = href
          ? 'Headline for this ticker (display only — not used in the call): <a href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">' + title + "</a>"
          : "Headline for this ticker (display only — not used in the call): " + title;
        newsMatch.hidden = false;
      } else {
        newsMatch.innerHTML = "";
        newsMatch.hidden = true;
      }
    }
    try {
      if (window.DeskMotion && typeof window.DeskMotion.setConfidence === "function") {
        const side = decision === "buy" || decision === "sell" ? decision : isAvoid ? "avoid" : "hold";
        window.DeskMotion.setConfidence(conf || 0, side);
      }
    } catch (_) {}
  }

  function renderLoopPanel(data) {
    const loop = data.loop || {};
    const cfg = data.config || {};
    const daily = data.daily || {};
    const ledger = data.ledger || {};
    const last = loop.last_decision || (data.decisions_preview && data.decisions_preview[0]) || null;

    const rthBanner = $("#loop-rth-banner");
    if (rthBanner) {
      const outside = !!loop.outside_rth || (!!loop.rth_only && !loop.rth_ok);
      rthBanner.classList.toggle("hidden", !outside || !cfg.session_active);
      rthBanner.classList.toggle("ah-calm", outside && !!cfg.session_active);
    }
    syncTradeGlowAfterHours(data);

    const gateRth = $("#gate-rth");
    if (gateRth) {
      gateRth.textContent = loop.rth_ok ? "Market open" : "Market closed";
      gateRth.className = "gate " + (loop.rth_ok ? "ok" : cfg.session_active && loop.rth_only ? "warn" : "");
    }
    const gateLoss = $("#gate-loss");
    if (gateLoss) {
      const maxLoss = cfg.max_session_loss_usd ?? loop.max_session_loss_usd;
      const skipped = loop.last_skip === "max_loss";
      gateLoss.textContent = maxLoss != null ? `Max loss ${fmtMoney(maxLoss)}` : "Max loss -";
      gateLoss.className = "gate " + (skipped ? "bad" : "ok");
    }
    const gateLoop = $("#gate-loop");
    if (gateLoop) {
      const running = !!loop.running;
      const skip = loop.last_skip;
      const skipLabel = {
        session_inactive: "not started",
        outside_rth: "market closed",
        max_loss: "max loss hit",
        target_hit: "goal hit",
        loop_disabled: "turned off",
        empty_watchlist: "no tickers",
        mode_not_auto_paper: "wrong mode",
      };
      gateLoop.textContent = running
        ? `Every ${loop.interval_sec || 60}s`
        : skip
          ? `Paused | ${skipLabel[skip] || skip}`
          : "Idle";
      gateLoop.className = "gate " + (running ? "ok" : skip ? "warn" : "");
    }

    const lat = loop.latency || {};
    const latEl = $("#loop-lat");
    if (latEl) {
      latEl.textContent =
        lat.last_ms != null
          ? `${lat.last_ms} ms last / ${lat.avg_ms ?? "-"} ms avg`
          : "-";
      latEl.title = lat.last_ms != null
        ? "How long recent checks took (latency in milliseconds)"
        : "Latency unknown until a check runs";
    }
    const healthEl = $("#loop-health");
    if (healthEl) {
      const skip = loop.last_skip;
      const outside =
        !!loop.outside_rth ||
        skip === "outside_rth" ||
        (!!loop.rth_only && !loop.rth_ok && !!cfg.session_active);
      if (!cfg.session_active || skip === "session_inactive") {
        healthEl.textContent = "Off";
      } else if (loop.running && !outside) {
        healthEl.textContent = "On";
      } else if (outside) {
        healthEl.textContent = "Paused (market closed)";
      } else if (skip === "max_loss") {
        healthEl.textContent = "Paused (max loss)";
      } else if (skip === "target_hit") {
        healthEl.textContent = "Paused (goal hit)";
      } else if (skip === "loop_disabled") {
        healthEl.textContent = "Off";
      } else if (skip === "empty_watchlist") {
        healthEl.textContent = "Paused (no tickers)";
      } else if (skip === "mode_not_auto_paper") {
        healthEl.textContent = "Off";
      } else if (loop.running) {
        healthEl.textContent = "On";
      } else {
        healthEl.textContent = "Off";
      }
    }
    const posEl = $("#loop-pos");
    if (posEl) {
      const positions = ledger.positions || [];
      if (!positions.length) posEl.textContent = "None";
      else {
        posEl.textContent = positions
          .slice(0, 3)
          .map((p) => `${p.ticker} ${p.side} ${p.shares}`)
          .join(", ");
      }
    }
    const pnlEl = $("#loop-pnl");
    if (pnlEl) {
      const pnl = Number(daily.pnl || 0);
      pnlEl.textContent = fmtMoney(pnl);
      pnlEl.classList.toggle("pos", pnl > 0);
      pnlEl.classList.toggle("neg", pnl < 0);
    }
    const goalEl = $("#loop-goal");
    if (goalEl) {
      if (daily.target_usd == null) goalEl.textContent = "not set";
      else if (daily.target_hit) goalEl.textContent = "Hit - paused";
      else goalEl.textContent = `${daily.progress_pct ?? 0}% | ${fmtMoney(daily.remaining_usd)} to go`;
    }

    // Big action + probs
    const big = $("#loop-big-action");
    const word = $("#loop-action-word");
    const meta = $("#loop-action-meta");
    let decision = "hold";
    let conf = 0;
    let latency = null;
    let ticker = "";
    let probs = { buy: 0, sell: 0, flat: 0.5 };
    if (last && (last.event === "decision" || last.event === "intent" || last.event === "fill" || last.event === "cancel")) {
      const evk = String(last.event || "").toLowerCase();
      // Intent/cancel/decision are never fills - big action stays Hold unless this is a fill event
      if (evk === "fill" && last.filled) {
        decision = (last.decision || last.side || "hold").toLowerCase();
      } else if (last.filled && evk !== "intent") {
        decision = (last.decision || last.side || "hold").toLowerCase();
      } else {
        decision = "hold";
      }
      conf = Number(last.confidence || 0);
      latency = last.latency_ms != null ? last.latency_ms : last.latencyMs;
      ticker = last.ticker || "";
      probs = last.probs || {
        buy: last.buy_prob,
        sell: last.sell_prob,
        flat: last.flat_prob,
      };
      // Prefer model probs from intended side when holding
      const modelSide = (last.model_side || last.intended_side || "").toLowerCase();
      if (probs.buy == null && conf > 0) {
        const sideForProb = (decision === "buy" || decision === "sell") ? decision : (modelSide || "flat");
        if (sideForProb === "buy") probs = { buy: conf, sell: (1 - conf) * 0.35, flat: (1 - conf) * 0.65 };
        else if (sideForProb === "sell") probs = { sell: conf, buy: (1 - conf) * 0.35, flat: (1 - conf) * 0.65 };
        else probs = { flat: Math.max(conf, 0.5), buy: (1 - conf) * 0.5, sell: (1 - conf) * 0.5 };
      }
    }
    if (big && word) {
      const simpleUi = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
      const pol = String((last && (last.policy_label || last.verdict || last.advisory_label)) || "").toUpperCase();
      // Research lean for hero word / spoken (Why/How agree). Fill-biased `decision`
      // stays Hold on intent; model_side/intended_side carry the brain lean.
      let lean = decision;
      if (last) {
        const ms = String(last.model_side || last.intended_side || "").toLowerCase();
        if (ms === "buy" || ms === "sell") lean = ms;
        else if (ms === "flat" || ms === "hold" || ms === "abstain") lean = "hold";
        else if (decision === "buy" || decision === "sell") lean = decision;
        else lean = "hold";
      }
      // Observe-only advisory "KILL" did not block anything — don't paint it as Skip.
      const isAvoid = lean === "avoid" || decision === "avoid" || pol === "AVOID";
      const label = lean === "buy" ? "Buy" : lean === "sell" ? "Sell" : isAvoid ? "Skip" : "Hold";
      word.textContent = label;
      try {
        renderSpokenCall(data, last, lean, conf, ticker, isAvoid);
      } catch (_) {}
      const glossEl = $("#loop-action-gloss");
      const storyEl = $("#butler-story");
      const idleCall = !last;
      if (glossEl) {
        if (simpleUi) {
          // Text cap line 1: gloss XOR butler-story. Idle → butler owns the line.
          if (idleCall) {
            glossEl.textContent = "";
            glossEl.hidden = true;
            glossEl.classList.add("is-idle");
          } else if (isAvoid) {
            glossEl.textContent = "Skip this one for now";
            glossEl.hidden = false;
            glossEl.classList.remove("is-idle");
          } else {
            glossEl.textContent = callGloss(decision);
            glossEl.hidden = false;
            glossEl.classList.remove("is-idle");
          }
        } else {
          glossEl.textContent = "";
          glossEl.hidden = true;
          glossEl.classList.add("is-idle");
        }
      }
      // Hide conf bar in Simple text-cap (keeps under-word to 3 lines max)
      const confBar = $("#loop-action-conf");
      const confFill = $("#loop-action-conf-fill");
      if (confBar && confFill) {
        confFill.style.width = "0%";
        confBar.setAttribute("aria-hidden", "true");
        confBar.classList.add("is-empty");
        if (simpleUi) confBar.hidden = true;
        else confBar.hidden = false;
      }
      const outEl = $("#loop-action-outcome");
      if (outEl) {
        if (simpleUi) {
          let note = "";
          try {
            const outEv = (loopEvents || []).find(
              (e) => e && (e.event === "outcome" || e.outcome || e.outcome_label)
            );
            if (outEv) {
              note =
                outEv.butler_note ||
                outcomeGloss(outEv.outcome || outEv.outcome_label, outEv.ticker) ||
                "";
            }
          } catch (_) {}
          if (note) {
            outEl.textContent = note;
            outEl.hidden = false;
          } else {
            outEl.textContent = "";
            outEl.hidden = true;
          }
        } else {
          outEl.textContent = "";
          outEl.hidden = true;
        }
      }
      const sideClass = lean === "buy" || lean === "sell"
        ? lean
        : isAvoid
          ? "avoid"
          : "hold";
      const actionKey = [
        sideClass,
        last && (last.seq != null ? last.seq : last.id != null ? last.id : last.ts) || "",
        ticker,
      ].join("|");
      // Preserve One Job classes (call-spoken); only swap side modifiers
      const sideMods = ["buy", "sell", "hold", "avoid"];
      sideMods.forEach((c) => big.classList.remove(c));
      big.classList.add("big-action");
      big.classList.add("call-spoken");
      big.classList.add(sideClass);
      const reducedMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      if (!reducedMotion && lastBigActionKey != null && actionKey !== lastBigActionKey) {
        big.classList.remove("is-pulse");
        void big.offsetWidth;
        big.classList.add("is-pulse");
        clearTimeout(big._pulseTimer);
        big._pulseTimer = setTimeout(() => big.classList.remove("is-pulse"), 720);
      }
      lastBigActionKey = actionKey;
      if (meta) {
        // Text cap line 2: compact meta (ticker · time)
        if (!last) {
          meta.textContent = "";
          meta.hidden = !!simpleUi;
        } else {
          const bits = [];
          if (ticker) bits.push(ticker);
          if (last.ts) bits.push(fmtTimeShort(last.ts));
          else if (conf > 0) bits.push(`${Math.round(conf * 100)}%`);
          if (last?.late) bits.push("late");
          meta.textContent = bits.join(" · ") || (simpleUi ? "" : "Latest call");
          meta.hidden = false;
        }
      }
      // Re-sync butler after gloss state so XOR sticks
      if (simpleUi) {
        try { syncButlerStory(data); } catch (_) {}
      } else if (storyEl) {
        storyEl.hidden = true;
      }
    }
    setProbs(probs);

    // Seed feed from preview if empty
    if ((!loopEvents.length) && Array.isArray(data.decisions_preview)) {
      loopEvents = data.decisions_preview.slice();
      const maxSeq = loopEvents.reduce((m, e) => Math.max(m, Number(e.seq || 0)), 0);
      loopFeedSeq = Math.max(loopFeedSeq, maxSeq);
      renderLoopFeed();
      loopEvents.forEach((ev) => noteTradeGlowFromEvent(ev));
    }
  }


  function renderBrainLedger(data) {
    const lb = data.ledger_brain || {};
    const totals = data.session_totals || (data.loop && data.loop.session_totals) || {};
    const modelUsd = Number(lb.model_usd != null ? lb.model_usd : totals.model_usd || 0);
    const paperPnl = Number(lb.paper_pnl != null ? lb.paper_pnl : (data.daily && data.daily.pnl) || 0);
    const friction = Number(lb.friction_usd != null ? lb.friction_usd : totals.friction_usd || 0);
    const late = Number(lb.late_blocks != null ? lb.late_blocks : totals.late_blocks || 0);
    const elBrain = $("#ledger-brain");
    const elPnl = $("#ledger-pnl");
    const elFric = $("#ledger-friction");
    const elLate = $("#ledger-late");
    if (elBrain) {
      elBrain.textContent = `Brain $${modelUsd.toFixed(4)}`;
      elBrain.title = "Estimated model API spend this session (research accounting)";
    }
    if (elPnl) {
      elPnl.textContent = `Paper P&L $${paperPnl.toFixed(2)}`;
      elPnl.title = "Today's paper profit or loss";
      elPnl.classList.toggle("pos", paperPnl > 0);
      elPnl.classList.toggle("neg", paperPnl < 0);
    }
    if (elFric) {
      elFric.textContent = `Friction $${friction.toFixed(4)}`;
      elFric.title = "Paper slip and fee drag this session";
    }
    if (elLate) {
      elLate.textContent = `Late ${late}`;
      elLate.title = "Checks skipped because the move looked late";
    }
    const hint = $("#friction-session-hint");
    if (hint) {
      hint.textContent = `So far ${fmtMoney(friction)}`;
      hint.title = "Slip + fee so far this session";
    }
    const cfg = (data && data.config) || {};
    syncFrictionInputs(cfg);
    const sel = $("#brain-mode-select");
    const mode = (data.brain_mode || (data.config && data.config.brain_mode) || (data.llm && data.llm.brain_mode) || "gemini");
    if (sel && !sel.dataset.userEditing) {
      sel.value = mode;
    }
  }

  function syncFrictionInputs(cfg) {
    if (!cfg || typeof cfg !== "object") return;
    const slipEl = $("#friction-slip");
    const feeEl = $("#friction-fee");
    const slip =
      cfg.slip_bps != null
        ? Number(cfg.slip_bps)
        : null;
    const fee =
      cfg.fee_bps != null
        ? Number(cfg.fee_bps)
        : null;
    if (slipEl && document.activeElement !== slipEl && !slipEl.dataset.userEditing) {
      if (slip != null && Number.isFinite(slip)) slipEl.value = String(slip);
    }
    if (feeEl && document.activeElement !== feeEl && !feeEl.dataset.userEditing) {
      if (fee != null && Number.isFinite(fee)) feeEl.value = String(fee);
    }
    markFrictionPresetActive(slip, fee);
  }

  function markFrictionPresetActive(slip, fee) {
    const s = Number(slip);
    const f = Number(fee);
    $$(".friction-preset").forEach((btn) => {
      const bs = Number(btn.dataset.slip);
      const bf = Number(btn.dataset.fee);
      const on =
        Number.isFinite(s) &&
        Number.isFinite(f) &&
        Math.abs(bs - s) < 1e-9 &&
        Math.abs(bf - f) < 1e-9;
      btn.classList.toggle("active", on);
    });
  }

  async function postFrictionConfig(slipBps, feeBps, label) {
    const body = {
      slip_bps: Math.max(0, Number(slipBps)),
      fee_bps: Math.max(0, Number(feeBps)),
    };
    if (!Number.isFinite(body.slip_bps) || !Number.isFinite(body.fee_bps)) {
      toast("Slip and fee must be numbers", true);
      return;
    }
    const slipEl = $("#friction-slip");
    const feeEl = $("#friction-fee");
    if (slipEl) slipEl.dataset.userEditing = "1";
    if (feeEl) feeEl.dataset.userEditing = "1";
    try {
      const data = await api("/api/config", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (data && data.config && state) {
        state.config = Object.assign({}, state.config || {}, data.config);
      }
      if (slipEl) slipEl.value = String(body.slip_bps);
      if (feeEl) feeEl.value = String(body.fee_bps);
      markFrictionPresetActive(body.slip_bps, body.fee_bps);
      toast(label || `Friction: slip ${body.slip_bps} / fee ${body.fee_bps} bps`);
      await refresh();
    } catch (err) {
      toast(String(err.message || err), true);
    } finally {
      if (slipEl) delete slipEl.dataset.userEditing;
      if (feeEl) delete feeEl.dataset.userEditing;
    }
  }

  function feedSignature(rows) {
    return (rows || [])
      .map((e) => `${e.seq ?? ""}|${e.event || ""}|${e.decision || e.side || ""}|${e.filled ? 1 : 0}|${e.ticker || ""}|${e.outcome || e.outcome_label || ""}|${e.exit_reason || ""}`)
      .join(";");
  }

  function renderLoopFeed() {
    const el = $("#loop-feed");
    const emptyEl = $("#loop-empty");
    if (!el) return;
    // Simple hides the decision log list (Latest call tile only) — skip DOM paint.
    if (getUiMode() === "simple" || document.body.classList.contains("ui-simple")) {
      return;
    }
    if (!loopEvents.length) {
      if (lastFeedSig !== "__empty__") {
        el.innerHTML = "";
        lastFeedSig = "__empty__";
      }
      if (emptyEl) emptyEl.classList.remove("hidden");
      return;
    }
    if (emptyEl) emptyEl.classList.add("hidden");
    const rows = loopEvents.slice(0, 80);
    const sig = feedSignature(rows);
    const newestSeq = rows.length ? Number(rows[0].seq ?? rows[0].id ?? NaN) : null;
    const markNew =
      newestSeq != null &&
      Number.isFinite(newestSeq) &&
      lastFeedNewestSeq != null &&
      newestSeq !== lastFeedNewestSeq;
    if (sig === lastFeedSig && !markNew) return;
    lastFeedSig = sig;
    if (newestSeq != null && Number.isFinite(newestSeq)) {
      lastFeedNewestSeq = newestSeq;
    }
    el.innerHTML = rows
      .map((e, idx) => {
        const evKind = String(e.event || "intent").toLowerCase();
        const isFill = evKind === "fill" || (!!e.filled && evKind !== "intent");
        const isIntent = evKind === "intent" || evKind === "decision" || evKind === "cancel";
        const dec = (e.decision || e.side || "hold").toLowerCase();
        const modelSide = (e.model_side || e.intended_side || dec).toLowerCase();
        let act, actClass;
        if (evKind === "cancel") {
          act = "CANCEL";
          actClass = "act-hold";
        } else if (isFill) {
          act = dec === "buy" ? "FILL BUY" : dec === "sell" ? "FILL SELL" : "FILL";
          actClass = dec === "buy" ? "act-buy" : dec === "sell" ? "act-sell" : "act-hold";
        } else {
          // Intent / decision - never show as a filled trade
          const shown = e.filled ? "hold" : dec; // intent.filled always false
          act =
            shown === "buy"
              ? "BUY?"
              : shown === "sell"
                ? "SELL?"
                : shown === "flat"
                  ? "FLAT"
                  : "HOLD";
          if (e.status === "pending" || evKind === "intent") {
            if (modelSide === "buy") act = "INTENT BUY";
            else if (modelSide === "sell") act = "INTENT SELL";
            else act = "INTENT";
            if (e.hold || e.abstain || e.late || e.capped) {
              act = modelSide === "buy" || modelSide === "sell"
                ? `HOLD (${modelSide})`
                : "HOLD";
            }
          }
          actClass =
            act.includes("BUY") && !act.includes("HOLD")
              ? "act-buy"
              : act.includes("SELL") && !act.includes("HOLD")
                ? "act-sell"
                : "act-hold";
        }
        const isHoldAct = act.startsWith("HOLD") || act === "FLAT" || act === "CANCEL" || act === "INTENT";
        const hasErr = !!(e.error || e.data_error);
        const lateOnly = !!e.late && !hasErr;
        const simpleUi = getUiMode() === "simple";
        const badges = [];
        const pol = String(e.policy_label || "").toUpperCase();
        const polTip = e.advisory && e.advisory.scores
          ? Object.entries(e.advisory.scores).map(([k, v]) => `${k}=${v}`).join(" | ")
          : "Advisory research note - not a trade signal";
        // Simple: action + conf + at most one research policy chip. Advanced: full badge set.
        if (simpleUi) {
          if (pol === "SHIP" || pol === "FIX" || pol === "KILL") {
            badges.push(`<span class="badge-policy badge-policy-research badge-policy-${pol.toLowerCase()}" title="${escapeHtml(polTip)}">note ${pol}</span>`);
          }
        } else {
          if (isIntent && !isFill) {
            badges.push(`<span class="badge-intent" title="Decision only — not a paper fill">intent</span>`);
          }
          if (isFill) {
            badges.push(`<span class="badge-fill" title="Paper simulated fill (not a live brokerage fill)">fill</span>`);
          }
          const outL = String(e.outcome || e.outcome_label || "").toLowerCase();
          if (outL === "helped" || outL === "hurt" || outL === "flat") {
            badges.push(`<span class="badge-outcome-${outL}" title="Later mark vs intended side on paper — sample only, not proof of edge">outcome ${outL}</span>`);
          }
          if (e.exit_reason === "paper_stop" || (evKind === "exit" && String(e.action || "").toLowerCase().includes("stop"))) {
            badges.push(`<span class="badge-exit-stop" title="Paper stop — exited because the trade went against you">stop</span>`);
          } else if (e.exit_reason === "paper_take_profit" || (evKind === "exit" && String(e.action || "").toLowerCase().includes("profit"))) {
            badges.push(`<span class="badge-exit-tp" title="Paper take-profit — exited because the trade went your way">TP</span>`);
          }
          if (hasErr) {
            const tip = escapeHtml(String(e.error || "data error").slice(0, 120));
            badges.push(`<span class="badge-err" title="${tip}">err</span>`);
          } else if (lateOnly) {
            badges.push(`<span class="badge-late" title="Skipped — the move looked late to chase">late</span>`);
          }
          if (e.capped) {
            badges.push(`<span class="badge-capped" title="Risk or size limit blocked the preferred side">capped</span>`);
          }
          if (e.coherence_label === "coherent" || e.coherent === true) {
            badges.push(`<span class="badge-coherent" title="Thesis pieces agreed (research note)">coherent</span>`);
          } else if (e.coherence_label === "incoherent" || e.coherent === false) {
            badges.push(`<span class="badge-incoherent" title="Thesis pieces disagreed (research note)">incoherent</span>`);
          }
          if (e.low_confidence || e.error === "low_confidence") {
            badges.push(`<span class="badge-lowconf" title="Confidence too low — desk held instead of trading">low conf</span>`);
          }
          if (pol === "SHIP" || pol === "FIX" || pol === "KILL") {
            badges.push(`<span class="badge-policy badge-policy-research badge-policy-${pol.toLowerCase()}" title="${escapeHtml(polTip)}">note ${pol}</span>`);
          }
          if (e.routed === "mock_cheap") {
            badges.push(`<span class="badge-routed" title="Used a cheaper brain path: ${escapeHtml(String(e.router_reason || "cheap router"))}">routed</span>`);
          }
          if ((e.hold || e.abstain) && !isHoldAct && !isFill) {
            badges.push(`<span class="badge-hold" title="Held flat — no paper fill">hold</span>`);
          }
        }
        const conf = e.confidence != null ? `${Math.round(Number(e.confidence) * 100)}%` : "-";
        const lat = e.latency_ms != null ? `${e.latency_ms}` : e.latencyMs != null ? `${e.latencyMs}` : "-";
        const size = isFill
          ? e.size_at_price ||
            (e.fill ? `${e.fill.shares}@${e.fill.price}` : "-")
          : e.mid != null
            ? `@${Number(e.mid).toFixed(2)}`
            : "-";
        const skip = e.event && String(e.event).startsWith("loop_skip") ? e.event.replace("loop_skip_", "") : "";
        // Advanced micro-metrics
        const microBits = [];
        if (e.latency_ms != null || e.latencyMs != null) microBits.push(`${lat}ms`);
        if (e.mid != null) microBits.push(`mid ${Number(e.mid).toFixed(2)}`);
        if (e.spread_bps != null) microBits.push(`spr ${e.spread_bps}bps`);
        else if (e.bid != null && e.ask != null) {
          microBits.push(`b${Number(e.bid).toFixed(2)}/a${Number(e.ask).toFixed(2)}`);
        }
        let advMicro = "";
        if (e.advisory && e.advisory.scores && typeof e.advisory.scores === "object") {
          const sc = e.advisory.scores;
          const bits = ["thesis_coherent", "regime_ok", "risk_ok", "tradeable_now"]
            .filter((k) => sc[k] != null)
            .map((k) => `${k.replace(/_/g, " ")} ${Number(sc[k]).toFixed(2)}`);
          if (bits.length) {
            advMicro = `<span class="feed-advisory adv-only" title="Advisory research scores — observe only, not a signal">${escapeHtml(bits.join(" | "))}</span>`;
          }
        }
        const microCore = microBits.length
          ? `<span class="feed-micro adv-only">${escapeHtml(microBits.join(" | "))}</span>`
          : "";
        const micro = microCore + advMicro;
        const modeLabel = isFill
          ? `<span class="feed-sim">paper fill</span>`
          : `<span class="feed-sim">paper intent</span>`;
        const newCls = markNew && idx === 0 ? " is-new" : "";
        const sideAttr =
          actClass === "act-buy" ? "buy" : actClass === "act-sell" ? "sell" : "hold";
        const kindAttr = isFill ? "fill" : isHoldAct ? "hold" : "intent";
        const pillKind = isFill ? "act-fill" : isHoldAct ? "act-ghost" : "act-intent";
        const confNum =
          e.confidence != null && Number.isFinite(Number(e.confidence))
            ? Math.max(0, Math.min(100, Math.round(Number(e.confidence) * 100)))
            : null;
        const confBar =
          confNum != null
            ? `<span class="conf-meter" aria-hidden="true"><i style="width:${confNum}%"></i></span><span class="conf-pct">${confNum}%</span>`
            : `<span class="conf-pct conf-none">-</span>`;
        const tickerLabel = escapeHtml(e.ticker || skip || "-");
        return `<div class="loop-feed-row${newCls}" data-event="${escapeHtml(evKind)}" data-side="${sideAttr}" data-kind="${kindAttr}" data-seq="${escapeHtml(String(e.seq ?? ""))}">
          <span class="feed-seq">${e.seq ?? "-"}</span>
          <span class="feed-time">${escapeHtml(fmtTimeShort(e.ts))}</span>
          <span class="feed-ticker"><span class="ticker-badge">${tickerLabel}</span></span>
          <span class="feed-act"><span class="act-pill ${pillKind} ${actClass}">${act}</span>${badges.join("")}</span>
          <span class="feed-conf" title="${confNum != null ? "Confidence " + confNum + "%" : "Confidence unknown"}">${confBar}</span>
          <span class="feed-ms adv-only" title="${lat !== "-" ? "Latency " + lat + " ms" : "Latency unknown"}">${lat}${micro}</span>
          <span class="feed-size">${escapeHtml(String(size))}</span>
          <span class="feed-mode adv-only">${modeLabel}</span>
        </div>`;
      })
      .join("");
  }

  async function pollLoopFeed() {
    try {
      const q = loopFeedSeq ? `?since_seq=${loopFeedSeq}&limit=40` : "?limit=40";
      const data = await api(`/api/loop/feed${q}`);
      const events = data.events || [];
      if (events.length) {
        // merge newest-first
        const seen = new Set(loopEvents.map((e) => e.id || e.seq));
        const fresh = events.filter((e) => !seen.has(e.id) && !seen.has(e.seq));
        if (fresh.length) {
          loopEvents = fresh.concat(loopEvents).slice(0, 200);
          renderLoopFeed();
          fresh.forEach((ev) => noteTradeGlowFromEvent(ev));
        }
        loopFeedSeq = Math.max(
          loopFeedSeq,
          ...events.map((e) => Number(e.seq || 0)),
          data.seq || 0
        );
      }
      if (data.loop && state) {
        state.loop = data.loop;
        renderLoopPanel(state);
      }
    } catch (_) {
      /* ignore poll errors */
    }
  }

  async function stopSession() {
    const before = state ? JSON.parse(JSON.stringify(state)) : null;
    await api("/api/session/stop", { method: "POST", body: "{}" });
    toast("Checking stopped. Open positions keep their stop-loss protection.");
    await refresh();
    try { showRecap(before, state); } catch (_) {}
  }



  const MEGATHREAD_LABELS = {
    wsb_daily: "Daily Discussion",
    wsb_waymt: "WAYMT / Moves",
    wsb_post_day: "Post-day / AH",
    wsb_weekend: "Weekend / Futures",
    wsb_live_chat: "WSB Live Chat",
  };

  function renderBuzzSimplePill(data) {
    const pill = $("#buzz-simple-pill");
    if (!pill) return;
    const heat = (data.heat || (data.buzz && data.buzz.heat) || []);
    const spikes = heat.filter((h) => h.is_spike).slice(0, 2);
    const hits = (data.buzz && data.buzz.watchlist_hits) || [];
    const simple = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
    // Simple masthead stays calm — buzz is Advanced-only (no data-simple-cue leak)
    if (simple) {
      pill.classList.add("hidden");
      pill.removeAttribute("data-simple-cue");
      pill.classList.remove("is-spike");
      pill.textContent = "Buzz";
      return;
    }
    if (!hits.length && !spikes.length) {
      pill.classList.add("hidden");
      pill.classList.remove("is-spike");
      pill.removeAttribute("data-simple-cue");
      pill.textContent = "Buzz";
      return;
    }
    const names = (spikes.length ? spikes : hits).slice(0, 3).map((h) => h.ticker).join(" · ");
    pill.textContent = spikes.length ? `Buzz ↑ ${names}` : `Buzz: ${names}`;
    pill.classList.toggle("is-spike", !!spikes.length);
    pill.classList.remove("hidden");
    pill.removeAttribute("data-simple-cue");
  }

  function renderBuzzPanel(buzz) {
    const list = $("#buzz-list");
    const meta = $("#buzz-meta");
    const errEl = $("#buzz-errors");
    const megaEl = $("#buzz-megathreads");
    if (!list) return;
    buzz = buzz || {};
    const cachedAt = buzz.cached_at;
    let when = "-";
    if (cachedAt) {
      try {
        const d = new Date(cachedAt);
        when = d.toLocaleString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });
      } catch (_) {
        when = String(cachedAt);
      }
    }
    const stale = buzz.stale ? " · may be outdated" : "";
    const fromCache = buzz.from_cache ? " · from cache" : "";
    const authMode = buzz.auth_mode || (buzz.reddit_auth && buzz.reddit_auth.mode) || "";
    let authBit = "";
    if (authMode) {
      const am = String(authMode).toLowerCase();
      if (am.includes("oauth")) authBit = " · Reddit login (OAuth)";
      else if (am.includes("public")) authBit = " · public feed (may be blocked)";
      else authBit = ` · ${authMode}`;
    }
    if (meta) {
      meta.textContent = `Last refresh: ${when}${stale}${fromCache}${authBit}`;
      meta.title = "When Reddit mention data was last refreshed (research only)";
    }
    if (errEl) {
      const errs = buzz.errors || [];
      errEl.textContent = errs.length ? errs.slice(0, 4).join(" | ") : "";
    }
    const liveNote = $("#buzz-live-chat-note");
    const liveTicks = $("#buzz-live-chat-tickers");
    const live = buzz.live_chat || {};
    if (liveNote) {
      liveNote.textContent = live.note || "Live Chat needs Reddit login - connect later.";
    }
    if (liveTicks) {
      const pasteRows = live.paste_tickers || [];
      if (!pasteRows.length) {
        liveTicks.innerHTML = live.has_paste ? "" : "";
      } else {
        liveTicks.innerHTML = pasteRows.slice(0, 12).map((t) =>
          `<span class="churn-chip still">${escapeHtml(t.ticker)} ${escapeHtml(String(t.weighted_mentions ?? t.mentions ?? ""))}</span>`
        ).join("");
      }
    }
    if (megaEl) {
      const threads = buzz.megathreads || [];
      if (!threads.length) {
        megaEl.innerHTML = "";
      } else {
        megaEl.innerHTML = threads.map((th) => {
          const tag = th.tag || "wsb";
          const label = MEGATHREAD_LABELS[tag] || tag;
          const tops = (th.top_tickers || []).slice(0, 8).map((t) =>
            `<span class="churn-chip still">${escapeHtml(t.ticker)} ${escapeHtml(String(t.weighted_mentions ?? ""))}</span>`
          ).join("");
          const err = th.error ? `<span class="muted"> | ${escapeHtml(th.error)}</span>` : "";
          return `<div class="buzz-thread">
            <span class="thread-tag ${escapeHtml(tag)}">${escapeHtml(label)}</span>
            <a class="thread-title" href="${escapeHtml(safeUrl(th.url) || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(th.title || "(thread)")}</a>
            ${err}
            <div class="thread-tickers">${tops || '<span class="muted">no tickers parsed</span>'}</div>
          </div>`;
        }).join("");
      }
    }
    const rows = buzz.tickers || buzz.top || [];
    if (!rows.length) {
      list.innerHTML = `<div class="empty">${buzz.stale ? "Buzz is warming up — check back shortly." : "No ticker mentions yet. Reddit may be blocking public feeds; a login helps when configured."}</div>`;
      return;
    }
    const hotCut = rows[0] && (rows[0].weighted_mentions || rows[0].mentions || 0);
    list.innerHTML = rows.slice(0, 25).map((r, i) => {
      const ment = r.weighted_mentions != null ? r.weighted_mentions : r.mentions;
      const isHot = i < 5 && ment >= Math.max(3, (hotCut || 0) * 0.45);
      const samples = (r.samples || []).slice(0, 2).map((s) =>
        `<a href="${escapeHtml(safeUrl(s.url) || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.title || "post")}</a>`
      ).join(" | ");
      const src = (r.sources || []).join(", ");
      return `<div class="buzz-row ${isHot ? "hot" : ""}">
        <span class="bz-ticker">${escapeHtml(r.ticker)}</span>
        <span class="bz-count">${escapeHtml(String(ment))}</span>
        <div class="bz-samples">${samples || "-"}<div class="bz-src">${escapeHtml(src)}</div></div>
      </div>`;
    }).join("");
  }

  function renderBuzz(data) {
    renderBuzzSimplePill(data);
    // Advanced panel: prefer full buzz on data.buzz; refresh may hydrate more via /api/buzz
    if (getUiMode() === "advanced") {
      renderBuzzPanel(data.buzz || {});
    }
  }

  function renderPaperChrome(data) {
    const rth = $("#chrome-rth");
    if (!rth) return;
    const loop = data.loop || {};
    const on = loop.rth_ok !== false;
    rth.textContent = on ? "Market hours" : "Outside hours";
    rth.title = on
      ? "US regular trading session is open"
      : "Outside US regular trading hours — Auto paper pauses until the market opens";
    rth.classList.toggle("off", !on);
    rth.classList.toggle("ah-calm", !on);
    syncTradeGlowAfterHours(data);
  }

  function isOppActionable(o) {
    return !!(o && o.source === "pending" && o.id && !String(o.id).startsWith("loop-"));
  }

  function sidePlain(side) {
    const s = String(side || "").toLowerCase();
    if (s === "buy" || s === "long") return "Buy";
    if (s === "sell" || s === "short") return "Sell";
    if (!s || s === "-" || s === "hold" || s === "flat") return "Hold";
    return String(side);
  }

  function syncFillModeToggle(cfg) {
    const mode = (cfg && cfg.mode) || "manual";
    // Simple only exposes auto_paper | manual. Never treat auto_live as Ask-me-first.
    $$(".fill-mode-btn").forEach((b) => {
      const want = b.dataset.fillMode;
      const on = (want === "auto_paper" && mode === "auto_paper")
        || (want === "manual" && mode === "manual");
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const wrap = $("#fill-mode-wrap") || document.querySelector(".fill-mode-wrap");
    if (wrap) {
      wrap.classList.toggle("mode-auto-live", mode === "auto_live");
      wrap.classList.toggle("mode-unmapped", mode === "auto_live");
    }
  }

  function updateOppHeader(data, pendingCount) {
    const cfg = data.config || {};
    const countEl = $("#opp-waiting-count");
    if (countEl) {
      if (pendingCount > 0) {
        countEl.textContent = `${pendingCount} waiting`;
        countEl.classList.remove("hidden", "is-zero");
        if (pendingCount > lastPendingCount) {
          countEl.classList.remove("is-pulse");
          void countEl.offsetWidth;
          countEl.classList.add("is-pulse");
          clearTimeout(countEl._pulseTimer);
          countEl._pulseTimer = setTimeout(() => countEl.classList.remove("is-pulse"), 700);
        }
      } else {
        countEl.textContent = "";
        countEl.classList.add("is-zero");
        countEl.classList.remove("is-pulse");
      }
    }
    lastPendingCount = pendingCount;
    try { syncPhase(cfg.session_active !== undefined ? cfg : (state?.config || {}), pendingCount, data); } catch (_) {}
    // Background tab: show the waiting count in the browser tab itself.
    document.title = pendingCount > 0 ? `(${pendingCount}) waiting · Tomahawk` : "Tomahawk";
    const hint = $("#opp-hint-simple");
    if (hint) {
      const m = cfg.mode || "manual";
      if (m === "auto_paper") {
        hint.textContent = "Auto fill On — paper fills can happen without Approve. Waiting stays quiet unless something still needs you.";
      } else if (m === "auto_live") {
        hint.textContent = "Auto + Alpaca is on (Advanced mode). Approve or auto may hit the broker — not the same as Ask me first. Switch to Ask me first or Auto fill for paper-only.";
      } else {
        hint.textContent = "Approve places the paper trade; Skip passes. Ideas wait here only in Ask me first.";
      }
    }
    const gloss = $("#fill-mode-gloss");
    if (gloss) {
      const m = cfg.mode || "manual";
      if (m === "auto_paper") {
        gloss.textContent = "Auto fill: I place paper fills for you — no Approve needed (still fake money).";
      } else if (m === "auto_live") {
        gloss.textContent = "Auto + Alpaca (set in Advanced): fills can go to the broker. Pick Ask me first or Auto fill here for paper-only.";
      } else {
        gloss.textContent = "Ask me first: ideas wait in Waiting until you Approve or Skip.";
      }
    }
    const hintAdv = $("#opp-hint-adv");
    if (hintAdv) {
      const m = cfg.mode || "manual";
      if (m === "auto_paper") {
        hintAdv.textContent = "Auto fill On — paper fills happen without Approve. Ranked research may still show for review; Approve/Skip only applies to pending Ask-me-first ideas.";
      } else if (m === "auto_live") {
        hintAdv.textContent = "Auto + Alpaca — Approve and auto paths can submit to the broker (live if ALPACA_PAPER=false). This is not Ask me first / paper Waiting.";
      } else {
        hintAdv.textContent = "Ranked research ideas (Pass = worth a look, Watch = not ready). Approve or Skip only applies to pending ideas — not a live order ticket.";
      }
    }
    syncFillModeToggle(cfg);
    const panel = $("#opp-panel");
    if (panel) {
      const nowPending = pendingCount > 0;
      panel.classList.toggle("has-pending", nowPending);
      panel.classList.toggle("is-empty-quiet", !nowPending && (getUiMode() === "simple" || document.body.classList.contains("ui-simple")));
      // One-shot attention flash when count rises — not infinite pulse
      if (nowPending && pendingCount > (updateOppHeader._prevPending || 0)) {
        panel.classList.remove("pending-flash");
        void panel.offsetWidth;
        panel.classList.add("pending-flash");
        clearTimeout(panel._flashTimer);
        panel._flashTimer = setTimeout(() => panel.classList.remove("pending-flash"), 2600);
      }
      if (!nowPending) panel.classList.remove("pending-flash");
      updateOppHeader._prevPending = pendingCount;
    }
    try {
      if (state) syncButlerStory(Object.assign({}, state, data, { pending_count: pendingCount }));
      else syncButlerStory(Object.assign({}, data, { pending_count: pendingCount }));
    } catch (_) {}
  }

  function updateDeskGuide(data) {
    const guide = $("#desk-guide");
    if (!guide) return;
    // Quiet Simple: desk guide hidden in all modes this pass (DOM kept, null-safe).
    guide.classList.add("hidden");
    guide.setAttribute("hidden", "");
  }

  function oppSignature(rows, modeKey) {
    // Stable sig: omit signal_price (jitters every tick → full rewrite flash)
    return modeKey + "|" + (rows || [])
      .map((o) => `${o.id || ""}|${o.source || ""}|${o.verdict || ""}|${o.side || ""}|${o.ticker || ""}|${o.suggested_shares ?? ""}`)
      .join(";");
  }

  function setOppEmpty(simple, cfg) {
    const emptyEl = $("#opp-empty");
    const feed = $("#opp-feed");
    if (!emptyEl) return;
    const main = $("#opp-empty-main");
    const sub = $("#opp-empty-sub");
    const autoOn = cfg.mode === "auto_paper" && !!cfg.session_active;
    if (simple) {
      if (autoOn) {
        if (main) main.textContent = "Auto fill On — fills happen on their own.";
        if (sub) sub.textContent = "Switch to Ask me first if you want ideas to wait here.";
      } else {
        if (main) main.textContent = "Nothing waiting.";
        if (sub) sub.textContent = "When an idea needs you, it lands here.";
      }
    } else {
      if (main) main.textContent = "No ideas waiting.";
      if (sub) sub.textContent = "When research marks Pass or Watch, pending ideas land here for Approve or Skip.";
    }
    emptyEl.classList.remove("hidden");
    if (feed) feed.innerHTML = "";
  }

  function clearOppEmptyTimer() {
    if (oppEmptyTimer) {
      clearTimeout(oppEmptyTimer);
      oppEmptyTimer = null;
    }
  }

  function renderOpportunities(data) {
    const el = $("#opp-feed");
    const churnEl = $("#opp-churn");
    const emptyEl = $("#opp-empty");
    if (!el) return;
    const simple = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
    const cfg = data.config || {};
    let rows = (data.opportunities || []).slice();
    rows = rows.filter((o) => {
      const v = String(o.verdict || "WATCH").toUpperCase();
      return v === "PASS" || v === "WATCH" || o.source === "pending";
    });
    if (simple) {
      rows = rows.filter(isOppActionable);
    }
    // Auto fill (Simple): hide Waiting only when no pending ghosts remain
    const serverPending = Number(data.pending_count != null ? data.pending_count : rows.filter(isOppActionable).length);
    const autoHidePending = simple && cfg.mode === "auto_paper" && serverPending === 0;
    if (autoHidePending) {
      rows = [];
    }
    rows = rows.slice(0, 16);
    const pendingCount = autoHidePending ? 0 : rows.filter(isOppActionable).length;
    const modeKey = `${simple ? "s" : "a"}|${cfg.mode || ""}|${cfg.session_active ? 1 : 0}`;
    const sig = oppSignature(rows, modeKey);
    const skipRewrite = sig === lastOppSig;

    const nowSet = new Set(rows.map((o) => String(o.ticker || "").toUpperCase()).filter(Boolean));
    const added = [...nowSet].filter((t) => !oppPrevTickers.has(t));
    const kept = [...nowSet].filter((t) => oppPrevTickers.has(t));
    const fell = [...oppPrevTickers].filter((t) => !nowSet.has(t));
    const churnSig = `${added.join(",")}|${fell.join(",")}|${kept.slice(0, 8).join(",")}`;
    if (churnEl && churnSig !== lastChurnSig) {
      lastChurnSig = churnSig;
      const chips = [
        ...added.map((t) => `<span class="churn-chip new">new ${escapeHtml(t)}</span>`),
        ...kept.slice(0, 8).map((t) => `<span class="churn-chip still">still ${escapeHtml(t)}</span>`),
        ...fell.slice(0, 6).map((t) => `<span class="churn-chip fell">off ${escapeHtml(t)}</span>`),
      ];
      churnEl.innerHTML = chips.length
        ? chips.join("")
        : `<span class="muted">Watching for ticker changes…</span>`;
    }

    // Empty path (incl. Auto fill forced-empty)
    if (!rows.length) {
      if (autoHidePending) {
        updateOppHeader(data, 0);
        clearOppEmptyTimer();
        if (!skipRewrite) lastOppSig = sig;
        setOppEmpty(simple, cfg);
        oppPrevTickers = nowSet;
        oppRankMap = {};
        return;
      }
      // Ask me first: hysteresis — delay empty paint when list was visible (~700ms)
      const feedHasChildren = el.children.length > 0;
      const emptyHidden = emptyEl && emptyEl.classList.contains("hidden");
      if (feedHasChildren || (oppPrevTickers.size > 0 && emptyHidden)) {
        // Keep count chip until empty actually paints (timer callback updates header)
        if (!oppEmptyTimer) {
          const snapSimple = simple;
          const snapCfg = Object.assign({}, cfg);
          oppEmptyTimer = setTimeout(() => {
            oppEmptyTimer = null;
            // Re-check: if actionable rows returned, abort empty
            const cur = (state && state.opportunities) || [];
            const stillSimple = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
            const stillCfg = (state && state.config) || snapCfg;
            if (stillSimple && stillCfg.mode === "auto_paper") {
              // Only force-empty Auto fill when no pending ghosts remain
              const pc = Number((state && state.pending_count) || 0);
              if (pc === 0) {
                setOppEmpty(stillSimple, stillCfg);
                lastOppSig = oppSignature([], `${stillSimple ? "s" : "a"}|${stillCfg.mode || ""}|${stillCfg.session_active ? 1 : 0}`);
                oppPrevTickers = new Set();
                oppRankMap = {};
                updateOppHeader(state || { config: stillCfg }, 0);
                return;
              }
            }
            let live = cur.slice().filter((o) => {
              const v = String(o.verdict || "WATCH").toUpperCase();
              return v === "PASS" || v === "WATCH" || o.source === "pending";
            });
            if (stillSimple) live = live.filter(isOppActionable);
            if (live.length > 0) return; // rows came back — keep list
            setOppEmpty(stillSimple, stillCfg);
            lastOppSig = oppSignature([], `${stillSimple ? "s" : "a"}|${stillCfg.mode || ""}|${stillCfg.session_active ? 1 : 0}`);
            oppPrevTickers = new Set();
            oppRankMap = {};
            updateOppHeader(state || { config: stillCfg }, 0);
          }, 700);
        }
        // Keep previous list painted; do not flip lastOppSig to empty yet
        return;
      }
      clearOppEmptyTimer();
      updateOppHeader(data, 0);
      if (!skipRewrite) lastOppSig = sig;
      setOppEmpty(simple, cfg);
      oppPrevTickers = nowSet;
      oppRankMap = {};
      return;
    }

    // 0→N or N→M: show list immediately; cancel pending empty
    clearOppEmptyTimer();
    updateOppHeader(data, simple ? pendingCount : rows.filter(isOppActionable).length);

    if (skipRewrite) {
      oppPrevTickers = nowSet;
      return;
    }
    lastOppSig = sig;

    if (emptyEl) emptyEl.classList.add("hidden");

    const newRank = {};
    el.innerHTML = rows.map((o, idx) => {
      const t = String(o.ticker || "").toUpperCase();
      newRank[t] = idx;
      const prevIdx = oppRankMap[t];
      let moveCls = "";
      let churnCls = "";
      if (simple) {
        // Soft arrive only — no rank thrash / aggressive pulse
        if (oppPrevTickers && oppPrevTickers.size && !oppPrevTickers.has(t)) {
          moveCls = "opp-arrive";
        }
      } else {
        if (prevIdx == null) moveCls = "rank-new";
        else if (idx < prevIdx) moveCls = "rank-up";
        else if (idx > prevIdx) moveCls = "rank-down";
        if (added.includes(t)) churnCls = "churn-new";
        else if (kept.includes(t)) churnCls = "churn-still";
      }
      const v = (o.verdict || "WATCH").toUpperCase();
      const vc = ["PASS", "WATCH", "AVOID"].includes(v) ? v.toLowerCase() : "watch";
      const canAct = isOppActionable(o);
      const actions = canAct
        ? `<div class="opp-actions">
             <button type="button" class="btn good sm" data-approve="${escapeHtml(o.id)}" title="Approve places this paper trade (fake money)">Approve</button>
             <button type="button" class="btn bad sm" data-reject="${escapeHtml(o.id)}" title="Skip passes — nothing is filled">Skip</button>
           </div>`
        : `<div class="opp-actions"><span class="muted">research only</span></div>`;
      const buzzBadge = (!simple && o.buzz_mentions != null && Number(o.buzz_mentions) > 0)
        ? `<span class="badge badge-buzz">buzz ${escapeHtml(String(o.buzz_mentions))}</span>` : "";
      const focusCls = focusTicker && focusTicker === t ? "focus" : "";
      const muteCls = canAct ? "" : "research-only";
      const sideRaw = String(o.side || "").toLowerCase();
      const sideCls = sideRaw === "long" ? "buy" : sideRaw === "short" ? "sell" : sideRaw;
      const sideLabel = simple ? sidePlain(o.side) : (o.side || "-");
      let sizeHtml = "";
      if (simple) {
        const sh = o.suggested_shares;
        const px = o.signal_price;
        if (sh != null && sh !== "" && px != null && px !== "") {
          sizeHtml = `<span class="opp-size">${escapeHtml(String(sh))} share${Number(sh) === 1 ? "" : "s"} × ${fmtMoney(px)} ≈ ${fmtMoney(Number(sh) * Number(px))}</span>`;
        } else if (sh != null && sh !== "") {
          sizeHtml = `<span class="opp-size">${escapeHtml(String(sh))} sh</span>`;
        } else if (px != null && px !== "") {
          sizeHtml = `<span class="opp-size">${fmtMoney(px)}</span>`;
        } else {
          sizeHtml = `<span class="opp-size muted">—</span>`;
        }
      }
      const vg = simple ? verdictGloss(v) : "";
      const thesisFull = String(o.thesis || "");
      let thesisShown = thesisFull;
      let thesisTitle = "";
      if (simple) {
        // Prefer short butler verdict label in mid col; thesis truncates (or butler if empty thesis)
        if (!thesisFull && vg) {
          thesisShown = vg;
          thesisTitle = v;
        } else if (thesisFull.length > 48) {
          thesisShown = thesisFull.slice(0, 46) + "…";
          thesisTitle = thesisFull;
        } else if (thesisFull) {
          thesisTitle = thesisFull;
        }
      }
      const thesisAttr = thesisTitle ? ` title="${escapeHtml(thesisTitle)}"` : "";
      const verdictHtml = simple
        ? `<span class="opp-simple-mid">${sizeHtml}${
            vg
              ? `<span class="opp-verdict-plain" title="${escapeHtml(v)}">${escapeHtml(vg)}</span>`
              : ""
          }</span>`
        : `<span class="opp-verdicts"><span class="badge badge-${vc}">${escapeHtml(v)}</span>${buzzBadge}</span>`;
      const whyLine = simple && canAct
        ? `<span class="opp-why-line muted">Approve places it with ${escapeHtml(moneyNoun())} · Skip passes (5 s to undo)</span>`
        : "";
      return `<div class="opp-row ${simple ? "opp-simple" : ""} ${churnCls} ${moveCls} ${focusCls} ${muteCls}" data-ticker="${escapeHtml(t)}">
        <span class="ticker">${escapeHtml(o.ticker)}</span>
        <span class="sig-side ${escapeHtml(sideCls || "")}">${escapeHtml(sideLabel)}</span>
        ${verdictHtml}
        <span class="thesis"${thesisAttr}>${escapeHtml(thesisShown)}</span>
        ${whyLine}
        ${actions}
      </div>`;
    }).join("");
    oppPrevTickers = nowSet;
    oppRankMap = newRank;
  }

  function renderEdgeSample(data) {
    const e = data.edge_sample || {};
    const set = (id, v) => { const n = $(id); if (n) n.textContent = v; };
    set("#edge-n", e.n != null ? String(e.n) : "-");
    set("#edge-hold", e.abstain_pct != null ? `${e.abstain_pct}%` : (e.hold_rate != null ? `${Math.round(e.hold_rate * 100)}%` : "-"));
    set("#edge-pass", e.pass_rate != null ? `${Math.round(e.pass_rate * 100)}%` : "-");
    set("#edge-win", e.win_rate != null ? `${Math.round(e.win_rate * 100)}%` : "-");
  }

  $("#btn-force-flatten")?.addEventListener("click", async () => {
    const b = window.__brokerStatus || {};
    const warn =
      b.configured
        ? "Force flatten local paper AND cancel/close Alpaca positions?"
        : "Force flatten ALL paper positions at last price?";
    if (!confirm(warn)) return;
    try {
      const data = await api("/api/ledger/flatten", { method: "POST", body: "{}" });
      const n = data.count ?? (data.closed || []).length;
      const bf = data.broker_flatten || {};
      let msg = `Flattened ${n} local paper position(s)`;
      if (bf.attempted) {
        msg += bf.ok
          ? " + Alpaca cancel/close OK"
          : " — Alpaca flatten FAILED (not complete for broker book)";
      }
      toast(msg, !!(data.refuse_flatten_as_complete || (bf.attempted && !bf.ok)));
      await refresh();
    } catch (e) {
      toast(e.message, true);
    }
  });

  $("#opp-feed")?.addEventListener("click", (ev) => {
    const approve = ev.target.closest("[data-approve]");
    const reject = ev.target.closest("[data-reject]");
    if (approve) openApprovePreview(approve.dataset.approve);
    if (reject) skipWithUndo(reject.dataset.reject, "Skipped from ideas feed", reject.closest(".opp-card, .signal-card, article, li"));
  });


  
  $("#btn-buzz-live-chat-ingest")?.addEventListener("click", async () => {
    const ta = $("#buzz-live-chat-paste");
    const text = (ta?.value || "").trim();
    if (!text) {
      toast("Paste Live Chat text first", true);
      return;
    }
    try {
      const data = await api("/api/buzz/live-chat", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      if (state) {
        state.buzz = data.buzz || state.buzz || {};
        if (data.live_chat) {
          state.buzz.live_chat = data.live_chat;
        }
      }
      renderBuzzPanel(data.buzz || { live_chat: data.live_chat });
      renderBuzzSimplePill({ buzz: data.buzz || state?.buzz });
      toast(`Live Chat paste: ${(data.live_chat?.tickers || []).length} tickers`);
    } catch (e) {
      toast(e.message || "Live Chat paste failed", true);
    }
  });

$("#btn-buzz-refresh")?.addEventListener("click", async () => {
    try {
      const data = await api("/api/buzz?force=1");
      if (state) state.buzz = data;
      renderBuzzPanel(data);
      renderBuzzSimplePill({ buzz: data });
      toast("Buzz refreshed");
    } catch (e) {
      toast(e.message || "Buzz refresh failed", true);
    }
  });

  $$(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => setUiMode(btn.dataset.uiMode));
  });
  setUiMode(getUiMode());
  syncStickyOffsets();
  window.addEventListener("resize", () => {
    if (stickyResizeTimer) clearTimeout(stickyResizeTimer);
    stickyResizeTimer = setTimeout(syncStickyOffsets, 80);
  });

  $$(".fill-mode-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mode = btn.dataset.fillMode;
      if (!mode || mode === (state?.config?.mode)) return;
      if (mode === "auto_paper" && state?.config?.session_active &&
          !confirm("Switch to Auto fill? The desk will place paper trades on its own, without asking you.")) {
        return;
      }
      try {
        const data = await api("/api/config", {
          method: "POST",
          body: JSON.stringify({ mode }),
        });
        toast(mode === "auto_paper"
          ? "Auto fill On — paper fills on their own"
          : mode === "auto_live"
            ? "Auto + Alpaca — broker path (check Advanced)"
            : "Ask me first — ideas wait for Approve");
        if (data?.config && state) state.config = { ...state.config, ...data.config };
        syncFillModeToggle(data?.config || { mode });
        await refresh();
      } catch (e) {
        toast(e.message, true);
      }
    });
  });


  // --- Market radar (Advanced; Simple gets calm butler line only) ---
  async function postRadarConfig(body) {
    try {
      const data = await api("/api/config", { method: "POST", body: JSON.stringify(body) });
      if (data && data.config) {
        state = state || {};
        state.config = Object.assign({}, state.config || {}, data.config);
      }
      // Force refresh payload
      const r = await api("/api/radar", { method: "POST", body: JSON.stringify({ force: !!body.force }) });
      if (r && r.radar) {
        state.radar = r.radar;
        state.radar_enabled = !!(r.config && r.config.radar_enabled);
        if (state.config) {
          state.config.radar_enabled = state.radar_enabled;
          if (r.config) {
            if (r.config.radar_top_n != null) state.config.radar_top_n = r.config.radar_top_n;
            if (r.config.radar_refresh_sec != null) state.config.radar_refresh_sec = r.config.radar_refresh_sec;
          }
        }
        lastRadarSig = "";
        renderRadar(state);
      }
      return data;
    } catch (err) {
      toast(String(err && err.message ? err.message : err));
    }
  }
  const chkRadar = $("#chk-radar-enabled");
  if (chkRadar) {
    chkRadar.addEventListener("change", () => {
      postRadarConfig({ radar_enabled: !!chkRadar.checked, force: !!chkRadar.checked });
    });
  }
  const radarTopN = $("#radar-top-n");
  if (radarTopN) {
    radarTopN.addEventListener("change", () => {
      const n = Number(radarTopN.value) || 20;
      postRadarConfig({ radar_top_n: n });
    });
  }
  $("#btn-radar-refresh")?.addEventListener("click", () => {
    postRadarConfig({ force: true });
    toast("Refreshing market radar…");
  });

  // --- Desk alerts (state.alerts) + sound / desktop prefs ---
  function alertSoundOn() {
    try { return localStorage.getItem("alert_sound") === "1"; } catch { return false; }
  }
  function setAlertSound(on) {
    try { localStorage.setItem("alert_sound", on ? "1" : "0"); } catch (_) {}
  }
  function browserAlertsOn() {
    try { return localStorage.getItem("desk_browser_alerts") === "1"; } catch { return false; }
  }
  function setBrowserAlerts(on) {
    try { localStorage.setItem("desk_browser_alerts", on ? "1" : "0"); } catch (_) {}
  }
  const chkSound = $("#chk-alert-sound");
  if (chkSound) {
    chkSound.checked = alertSoundOn();
    chkSound.addEventListener("change", () => setAlertSound(!!chkSound.checked));
  }
  const chkBrowserAlerts = $("#chk-browser-alerts");
  if (chkBrowserAlerts) {
    const prefOn = browserAlertsOn();
    const granted = typeof Notification !== "undefined" && Notification.permission === "granted";
    chkBrowserAlerts.checked = !!(prefOn && granted);
    if (prefOn && !granted) setBrowserAlerts(false);
    chkBrowserAlerts.addEventListener("change", async () => {
      if (!chkBrowserAlerts.checked) {
        setBrowserAlerts(false);
        return;
      }
      if (typeof Notification === "undefined") {
        toast("Desktop alerts are not available in this browser", true);
        chkBrowserAlerts.checked = false;
        setBrowserAlerts(false);
        return;
      }
      let perm = Notification.permission;
      if (perm === "default") {
        try {
          perm = await Notification.requestPermission();
        } catch (_) {
          perm = "denied";
        }
      }
      if (perm !== "granted") {
        toast("Desktop alerts need browser permission — you can turn them on later", true);
        chkBrowserAlerts.checked = false;
        setBrowserAlerts(false);
        return;
      }
      setBrowserAlerts(true);
      toast("Desktop alerts on — Waiting, goal, and max-loss may pop up quietly");
    });
  }

  function playAlertBeep() {
    if (!alertSoundOn()) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = playAlertBeep._ctx || (playAlertBeep._ctx = new Ctx());
      if (ctx.state === "suspended") {
        try { ctx.resume(); } catch (_) {}
      }
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = 660;
      g.gain.value = 0.03;
      o.connect(g);
      g.connect(ctx.destination);
      o.start();
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.16);
      o.stop(ctx.currentTime + 0.18);
    } catch (_) { /* fail soft */ }
  }

  function deskAlertKindLabel(kind) {
    const k = String(kind || "").toLowerCase();
    if (k === "waiting_enqueue") return "Waiting";
    if (k === "goal_hit") return "Goal";
    if (k === "kill") return "Kill";
    if (k === "session_loss" || k === "max_loss") return "Max loss";
    return kind ? String(kind) : "Note";
  }

  function relAlertTime(iso) {
    if (!iso) return "";
    try {
      const t = new Date(iso).getTime();
      if (!Number.isFinite(t)) return "";
      const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
      if (sec < 45) return "just now";
      if (sec < 90) return "1m ago";
      if (sec < 3600) return Math.floor(sec / 60) + "m ago";
      if (sec < 86400) return Math.floor(sec / 3600) + "h ago";
      return shortTs(iso);
    } catch {
      return "";
    }
  }

  function renderDeskAlertsLog(items) {
    const log = $("#desk-alerts-log");
    const empty = $("#desk-alerts-empty");
    if (!log) return;
    const rows = Array.isArray(items) ? items.slice(0, 8) : [];
    if (!rows.length) {
      log.innerHTML = "";
      if (empty) empty.classList.remove("hidden");
      return;
    }
    if (empty) empty.classList.add("hidden");
    log.innerHTML = rows
      .map((it) => {
        const kind = deskAlertKindLabel(it && it.kind);
        const msg = escapeHtml(String((it && it.message) || ""));
        const when = escapeHtml(relAlertTime(it && it.ts));
        const lvl = String((it && it.level) || "info").toLowerCase();
        const lvlCls = lvl === "error" ? " is-error" : lvl === "success" ? " is-success" : "";
        return `<div class="desk-alert-row${lvlCls}"><span class="desk-alert-kind">${escapeHtml(kind)}</span><span class="desk-alert-msg">${msg}</span><span class="desk-alert-when">${when}</span></div>`;
      })
      .join("");
  }

  function maybeBrowserNotify(item) {
    if (!item || !item.browser_notification) return;
    if (!browserAlertsOn()) return;
    if (typeof Notification === "undefined") return;
    if (Notification.permission !== "granted") return;
    const id = String(item.id || "");
    const body = String(item.message || item.kind || "Desk note").slice(0, 180);
    try {
      const n = new Notification("Tomahawk", { body, tag: id || undefined });
      n.onclick = () => { try { window.focus(); n.close(); } catch (_) {} };
    } catch (_) { /* fail soft */ }
  }

  function consumeDeskAlerts(alerts) {
    if (!alerts || !Array.isArray(alerts.items)) {
      return; // graceful no-op when absent
    }
    const items = alerts.items;
    renderDeskAlertsLog(items);
    // Newest-first from backend; process oldest→newest so toasts feel natural
    const chrono = items.slice().reverse();
    if (!deskAlertsBaselineReady) {
      for (const it of chrono) {
        if (it && it.id != null) deskAlertSeenIds.add(String(it.id));
      }
      deskAlertsBaselineReady = true;
      return;
    }
    const fresh = [];
    for (const it of chrono) {
      if (!it || it.id == null) continue;
      const id = String(it.id);
      if (deskAlertSeenIds.has(id)) continue;
      deskAlertSeenIds.add(id);
      fresh.push(it);
    }
    // Bound memory
    if (deskAlertSeenIds.size > 200) {
      const keep = new Set(items.map((it) => it && it.id != null ? String(it.id) : "").filter(Boolean));
      for (const id of fresh) keep.add(String(id.id));
      deskAlertSeenIds = keep;
    }
    for (const it of fresh) {
      const msg = String(it.message || it.kind || "Desk note");
      const isError = String(it.level || "").toLowerCase() === "error";
      toast(msg, isError);
      maybeBrowserNotify(it);
      if (alertSoundOn()) playAlertBeep();
    }
  }

  function alertDedupe(key, opts) {
    const now = Date.now();
    const ttl = (opts && opts.eventSet) ? 180000 : 120000;
    for (const [k, ts] of alertSeen) {
      if (now - ts > ttl) alertSeen.delete(k);
    }
    // Event-set / high-pri: exact key, no minute bucket
    if (opts && (opts.eventSet || opts.high)) {
      if (alertSeen.has(key)) return false;
      alertSeen.set(key, now);
      return true;
    }
    const minuteKey = key + "|" + Math.floor(now / 60000);
    if (alertSeen.has(minuteKey)) return false;
    alertSeen.set(minuteKey, now);
    return true;
  }

  function fireAlert(key, msg, isError, opts) {
    if (!alertDedupe(key, opts)) return;
    toast(msg, !!isError);
    playAlertBeep();
  }

  function collectOppEvents(opps) {
    const out = [];
    for (const o of (opps || []).slice(0, 8)) {
      const v = String(o.verdict || "").toUpperCase();
      const conf = Number(o.confidence || 0);
      const ticker = String(o.ticker || "").toUpperCase();
      if (!ticker) continue;
      if (v === "PASS" || (v === "WATCH" && conf >= 0.65)) {
        out.push({ ticker, v, conf, id: ticker + ":" + v });
      }
    }
    return out;
  }

  function maybeAlertFromState(data) {
    try {
      const daily = data.daily || {};
      const loop = data.loop || {};
      const buzz = data.buzz || {};
      const oppEvents = collectOppEvents(data.opportunities || []);
      const setKey = oppEvents.map((e) => e.id).sort().join("|");

      // Seed comparators until first full-state baseline (avoid boot spam)
      if (!fullStateSeen || !alertBaselineReady) {
        prevTargetHit = !!daily.target_hit;
        prevKillSkip = loop.last_skip || null;
        prevBuzzHitTickers = new Set(
          (buzz.watchlist_hits || []).map((h) => String(h.ticker || "").toUpperCase()).filter(Boolean)
        );
        prevBuzzScores = new Map();
        for (const h of (data.heat || buzz.heat || [])) {
          const t = String(h.ticker || "").toUpperCase();
          if (t) prevBuzzScores.set(t, Number(h.score) || 0);
        }
        for (const h of (buzz.watchlist_hits || [])) {
          const t = String(h.ticker || "").toUpperCase();
          if (t && !prevBuzzScores.has(t)) {
            prevBuzzScores.set(t, Number(h.weighted_mentions || h.mentions || 0));
          }
        }
        lastOppEventSet = setKey;
        if (fullStateSeen) alertBaselineReady = true;
        return;
      }

      // Goal / max-loss / kill: prefer state.alerts (desk_alerts) when present
      const hasDeskAlerts = !!(data.alerts && Array.isArray(data.alerts.items));
      if (!hasDeskAlerts) {
        if (daily.target_hit && !prevTargetHit) {
          fireAlert("goal_hit", "Goal hit — Auto paper paused", false, { high: true });
        }
        const skip = loop.last_skip || null;
        if ((skip === "max_loss" || skip === "kill") && skip !== prevKillSkip) {
          fireAlert(
            "kill_" + skip,
            skip === "max_loss" ? "Max loss hit - loop paused" : "Kill switch - loop paused",
            true,
            { high: true }
          );
        }
        prevKillSkip = skip;
      } else {
        prevKillSkip = loop.last_skip || null;
      }
      prevTargetHit = !!daily.target_hit;

      // Opportunities - batch into one summary toast; dedupe by event set
      if (setKey !== lastOppEventSet) {
        const prevIds = new Set(lastOppEventSet ? lastOppEventSet.split("|").filter(Boolean) : []);
        const newly = oppEvents.filter((e) => !prevIds.has(e.id));
        lastOppEventSet = setKey;
        if (newly.length) {
          const batchKey = "opp_set|" + newly.map((e) => e.id).sort().join(",");
          if (alertDedupe(batchKey, { eventSet: true })) {
            let msg;
            if (newly.length === 1) {
              const e = newly[0];
              msg = `Idea: ${e.ticker} ${e.v} (${Math.round(e.conf * 100)}%)`;
            } else {
              const names = newly.map((e) => e.ticker);
              const shown = names.slice(0, 5).join(", ");
              const more = names.length > 5 ? ", ..." : "";
              msg = `${newly.length} new paper ideas: ${shown}${more}`;
            }
            toast(msg, false);
            playAlertBeep();
          }
        }
      }

      // Buzz spike: meaningful mention jump (delta + cooldown), not every new hit
      const heatRows = data.heat || buzz.heat || [];
      const hitRows = buzz.watchlist_hits || [];
      const now = Date.now();
      const candidates = [];
      const seenT = new Set();
      for (const h of heatRows) {
        const t = String(h.ticker || "").toUpperCase();
        if (!t || seenT.has(t)) continue;
        seenT.add(t);
        candidates.push({
          ticker: t,
          score: Number(h.score) || 0,
          mentions: Number(h.mentions != null ? h.mentions : h.score) || 0,
          is_spike: !!h.is_spike,
          in_watchlist: !!h.in_watchlist,
          source: h.source_label || ((h.sources || [])[0]) || "Reddit",
        });
      }
      for (const h of hitRows) {
        const t = String(h.ticker || "").toUpperCase();
        if (!t || seenT.has(t)) continue;
        seenT.add(t);
        candidates.push({
          ticker: t,
          score: Number(h.weighted_mentions || h.mentions || 0),
          mentions: Number(h.mentions || h.raw_mentions || 0),
          is_spike: false,
          in_watchlist: true,
          source: "Reddit",
        });
      }
      const hitSet = new Set(candidates.filter((c) => c.in_watchlist).map((c) => c.ticker));
      for (const c of candidates) {
        const prev = prevBuzzScores.has(c.ticker) ? prevBuzzScores.get(c.ticker) : null;
        const delta = prev == null ? c.score : c.score - prev;
        let rising = !!c.is_spike;
        if (!rising && prev != null && prev > 0) {
          rising = delta >= BUZZ_SPIKE_ABS || (delta > 0 && delta / prev >= BUZZ_SPIKE_REL);
        } else if (!rising && (prev == null || prev <= 0) && c.score >= BUZZ_SPIKE_ABS && c.in_watchlist) {
          rising = true;
        }
        prevBuzzScores.set(c.ticker, c.score);
        if (!rising) continue;
        if (!c.in_watchlist && !c.is_spike) continue;
        const lastFire = buzzSpikeCooldown.get(c.ticker) || 0;
        if (now - lastFire < BUZZ_SPIKE_COOLDOWN_MS) continue;
        buzzSpikeCooldown.set(c.ticker, now);
        const n = Math.max(1, Math.round(c.mentions || c.score));
        fireAlert(
          "buzz_rise_" + c.ticker,
          `Buzz rising: ${c.ticker} (${n} mentions)`,
          false,
          { high: false }
        );
      }
      prevBuzzHitTickers = hitSet;
    } catch (_) { /* fail soft */ }
  }

  function setLiveInd(mode) {
    const el = $("#live-ind");
    const live = mode === "live";
    sseLive = live;
    if (!el) return;
    el.classList.toggle("live", live);
    el.classList.toggle("polling", !live);
    tickFreshness();
  }

  let _liteSig = "";
  function liteSignature(lite) {
    if (!lite) return "";
    const loop = lite.loop || {};
    const daily = lite.daily || {};
    const last = loop.last_decision || lite.last_decision || {};
    const opp = Array.isArray(lite.opportunities) ? lite.opportunities : [];
    const oppKey = opp.map((o) => (o && (o.id || o.ticker || "")) + ":" + (o && o.status || "")).join(",");
    const pos = (lite.open_position && lite.open_position.items) || [];
    const posKey = pos.map((p) => String((p && p.ticker) || "") + ":" + String((p && p.shares) || "")).join(",");
    const sb = lite.scoreboard || {};
    const curve = Array.isArray(lite.equity_curve) ? lite.equity_curve : [];
    const cLast = curve.length ? curve[curve.length - 1] : null;
    return [
      !!lite.session_active,
      daily.pnl, daily.progress_pct, daily.target_hit, daily.trades,
      loop.rth_ok, loop.outside_rth, loop.seq, loop.updated_at,
      last.ticker, last.action, last.confidence, last.seq,
      lite.pending_count, oppKey, posKey,
      lite.open_position && lite.open_position.count,
      sb.pnl, sb.max_drawdown, sb.fills_count, sb.win_rate, sb.butler,
      curve.length, cLast && (cLast.equity != null ? cLast.equity : cLast.pnl),
      lite.radar_enabled, lite.heat_enabled,
      lite.radar && lite.radar.count,
      lite.buzz && lite.buzz.updated_at,
      lite.brain_mode, lite.pace && lite.pace.status,
      lite.open_pnl_usd, lite.broker_trades_today,
      lite.bleed && lite.bleed.paused, lite.bleed && lite.bleed.net_usd,
      lite.benchmark && lite.benchmark.ahead_usd,
      lite.broker_book && lite.broker_book.day_pnl_usd,
      lite.broker_book && (lite.broker_book.positions || []).length,
      loop.decision_in_flight, loop.last_skip,
    ].join("|");
  }

  function applyStateLite(lite) {
    if (!lite || lite.ok === false) return;
    // Alerts: always attempt (id Set dedupes); do not depend on lite signature
    if (lite.alerts) consumeDeskAlerts(lite.alerts);
    const sig = liteSignature(lite);
    if (sig && sig === _liteSig) return; // skip no-op DOM thrash
    _liteSig = sig;
    if (!state) {
      // Seed lite WITHOUT implying empty watchlist (omit watchlist key)
      state = {
        config: { session_active: !!lite.session_active },
        daily: lite.daily || {},
        loop: lite.loop || {},
        ledger: { positions: (lite.open_position && lite.open_position.items) || [] },
        buzz: lite.buzz || {},
        heat: lite.heat || [],
        heat_enabled: lite.heat_enabled,
        opportunities: Array.isArray(lite.opportunities) ? lite.opportunities : [],
        pace: lite.pace || {},
      };
      window.__oppEmptyStreak = 0;
    }
    if (lite.loop) state.loop = lite.loop;
    if (lite.daily) state.daily = lite.daily;
    if (lite.pace) state.pace = lite.pace;
    if (lite.open_pnl_usd !== undefined) state.open_pnl_usd = lite.open_pnl_usd;
    if (lite.broker_trades_today !== undefined) state.broker_trades_today = lite.broker_trades_today;
    state.broker_book = lite.broker_book || null;
    if (lite.bleed !== undefined) state.bleed = lite.bleed;
    if (lite.benchmark !== undefined) state.benchmark = lite.benchmark;
    try {
      renderOpenPnl(state); renderBrokerBook(state); syncThinking(state.loop);
      renderBleed(state); renderBenchmark(state);
    } catch (_) {}
    if (lite.buzz) state.buzz = Object.assign({}, state.buzz || {}, lite.buzz);
    if (lite.heat) {
      state.heat = lite.heat;
      if (state.buzz) state.buzz.heat = lite.heat;
    }
    if (lite.heat_enabled !== undefined) {
      state.heat_enabled = lite.heat_enabled;
      state.config = state.config || {};
      // Soft-update chrome - do not wipe other config fields
      state.config.heat_enabled = lite.heat_enabled;
    }
    if (lite.radar) {
      state.radar = lite.radar;
      state.radar_enabled = lite.radar_enabled;
      state.config = state.config || {};
      if (lite.radar_enabled !== undefined) state.config.radar_enabled = lite.radar_enabled;
    } else if (lite.radar_enabled !== undefined) {
      state.radar_enabled = lite.radar_enabled;
      state.config = state.config || {};
      state.config.radar_enabled = lite.radar_enabled;
    }
    // Opportunities: [] is truthy in JS — do not wipe on brief empty lite.
    // Prefer pending_count===0 with streak>=2 before clearing.
    if (Array.isArray(lite.opportunities)) {
      const incoming = lite.opportunities;
      const pendingCount = lite.pending_count;
      if (incoming.length > 0) {
        state.opportunities = incoming;
        window.__oppEmptyStreak = 0;
      } else if (pendingCount === 0 || pendingCount === undefined) {
        // hysteresis: require 2 consecutive empty lites (~10s) OR pending_count explicitly 0 twice
        window.__oppEmptyStreak = (window.__oppEmptyStreak || 0) + 1;
        if (window.__oppEmptyStreak >= 2) {
          state.opportunities = incoming;
        }
        // else keep previous state.opportunities
      } else {
        window.__oppEmptyStreak = 0;
      }
    }
    // Positions: skip full replace when lite truncates ([:5]) to avoid list flicker
    if (lite.open_position && typeof lite.open_position === "object") {
      state.ledger = state.ledger || {};
      const items = Array.isArray(lite.open_position.items) ? lite.open_position.items : [];
      const count = lite.open_position.count;
      const truncated =
        !!lite.open_position.truncated ||
        (count != null && Number(count) > items.length);
      const prev = Array.isArray(state.ledger.positions) ? state.ledger.positions : [];
      if (truncated && prev.length > items.length) {
        const byT = new Map(items.map((p) => [String(p.ticker || "").toUpperCase(), p]));
        state.ledger.positions = prev.map((p) => {
          const t = String(p.ticker || "").toUpperCase();
          return byT.has(t) ? Object.assign({}, p, byT.get(t)) : p;
        });
      } else if (lite.open_position.items !== undefined) {
        state.ledger.positions = items;
      }
    }
    if (lite.scoreboard != null) state.scoreboard = lite.scoreboard;
    // Empty equity_curve array must not wipe a good curve
    if (Array.isArray(lite.equity_curve) && lite.equity_curve.length > 0) {
      state.equity_curve = lite.equity_curve;
    }
    // EquityScoreboard synced once via syncTradeGlowLedger (deduped)
    if (state.config) state.config.session_active = lite.session_active;
    if (lite.ledger_brain) state.ledger_brain = lite.ledger_brain;
    if (lite.session_totals) state.session_totals = lite.session_totals;
    if (lite.brain_mode) {
      state.brain_mode = lite.brain_mode;
      state.config = state.config || {};
      state.config.brain_mode = lite.brain_mode;
    }
    if (lite.slip_bps != null || lite.fee_bps != null) {
      state.config = state.config || {};
      if (lite.slip_bps != null) state.config.slip_bps = lite.slip_bps;
      if (lite.fee_bps != null) state.config.fee_bps = lite.fee_bps;
    }
    if (lite.model_cost) state.model_cost = lite.model_cost;
    // Soft updates - never touch #watchlist from lite (no cfg.watchlist array)
    renderSessionChip(state.config || {}, state.ledger || {}, state.loop);
    renderLoopPanel(state);
    renderBrainLedger(state);
    renderPaperChrome(state);
    renderBuzz(state);
    renderHeat(state);
    renderRadar(state);
    renderPace(state);
    renderOpportunities(state);
    renderPositions(state);
    syncTradeGlowLedger(state);
    updateDeskGuide(state);
    if (fullStateSeen) maybeAlertFromState(state);
    scheduleSparks(state);
    if (lite.last_decision && lite.last_decision.ticker) {
      const ev = Object.assign({ event: "decision" }, lite.last_decision);
      const seen = new Set(loopEvents.map((e) => e.seq));
      if (ev.seq != null && !seen.has(ev.seq)) {
        loopEvents = [ev].concat(loopEvents).slice(0, 200);
        loopFeedSeq = Math.max(loopFeedSeq, Number(ev.seq || 0));
        renderLoopFeed();
        noteTradeGlowFromEvent(ev);
      }
    }
  }

  function renderPace(data) {
    const daily = data.daily || {};
    const pace = data.pace || daily.pace || {};
    const label = $("#pace-label");
    const mark = $("#pace-mark");
    const fill = $("#target-fill");
    if (label) {
      const st = pace.status || "n/a";
      if (!daily.target_usd || st === "n/a") {
        label.textContent = "";
        label.className = "pace-label adv-only";
      } else {
        const map = {
          ahead: "Ahead of pace (informational)",
          on_pace: "On pace (informational)",
          behind: "Behind pace (informational)",
          outside: "Market closed (for information only)",
        };
        label.textContent = map[st] || st;
        label.className = "pace-label adv-only pace-" + st;
      }
    }
    if (mark) {
      const frac = pace.rth_frac;
      if (frac == null || daily.target_usd == null) {
        mark.style.display = "none";
      } else {
        mark.style.display = "block";
        mark.style.left = `${Math.max(0, Math.min(100, Number(frac) * 100))}%`;
      }
    }
    // Race meter fill already set in renderTop via progress_pct
    if (fill && daily.progress_pct != null) {
      fill.style.width = `${Number(daily.progress_pct || 0)}%`;
    }
    const bar = fill && fill.closest ? fill.closest(".target-bar") : null;
    if (bar) {
      bar.classList.remove("pace-glow-ahead", "pace-glow-behind", "pace-glow-on_pace", "pace-glow-outside");
      const st = pace.status;
      if (daily.target_usd && st && st !== "n/a") {
        bar.classList.add("pace-glow-" + st);
      }
      const pct = Number(daily.progress_pct || 0);
      const progressing = !!daily.target_usd && pct > 0 && pct < 100 && !daily.target_hit;
      const near = !!daily.target_usd && pct >= 85 && pct < 100 && !daily.target_hit;
      bar.classList.toggle("is-progressing", progressing);
      bar.classList.toggle("is-near-goal", near);
      bar.classList.toggle("is-hit", !!daily.target_hit);
    }
  }


  let lastRadarSig = "";

  function renderRadar(data) {
    const cfg = (data && data.config) || {};
    const radar = (data && data.radar) || {};
    const enabled = !!(data && (data.radar_enabled || cfg.radar_enabled || radar.enabled));
    const movers = enabled ? (radar.movers || []) : [];
    const count = enabled ? (radar.count != null ? Number(radar.count) : movers.length) : 0;

    const chk = $("#chk-radar-enabled");
    if (chk && document.activeElement !== chk) chk.checked = enabled;
    const topN = $("#radar-top-n");
    if (topN && document.activeElement !== topN) {
      const n = cfg.radar_top_n != null ? cfg.radar_top_n : (radar.top_n || 20);
      topN.value = String(n);
    }
    const meta = $("#radar-meta");
    if (meta) {
      if (!enabled) {
        meta.textContent = "Off — not scanning movers";
        meta.title = "Market radar is off";
      } else {
        const src = radar.source ? String(radar.source) : "—";
        const age = radar.age_sec != null ? Math.round(Number(radar.age_sec)) + "s ago" : "";
        meta.textContent = count
          ? `${count} movers · ${src}${age ? " · " + age : ""} · paper research`
          : `Scanning… · ${src} · paper research`;
        meta.title = "Whole-market movers for paper research — not a signal";
      }
    }
    const panel = $("#radar-panel");
    if (panel) panel.classList.toggle("dimmed", !enabled);

    const line = $("#radar-butler-line");
    const whisper = $("#radar-whisper");
    const simpleUi = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
    // Radar butler line = Simple calm caption only (Advanced uses research radar panel)
    if (line) {
      if (!simpleUi || !enabled) {
        line.textContent = "";
        line.classList.add("hidden");
        line.hidden = true;
      } else {
        const msg = radar.butler || (count ? `Watching ${count} movers for you` : "Radar listening…");
        line.textContent = msg;
        line.classList.remove("hidden");
        line.hidden = false;
      }
    }
    if (whisper) {
      if (!simpleUi || !enabled) {
        whisper.innerHTML = "";
        whisper.hidden = true;
        whisper.setAttribute("hidden", "");
      } else {
        const tops = (movers || []).slice(0, 5);
        const wSig = tops.map((m) => String(m.ticker || "").toUpperCase()).join(",");
        if (wSig !== (renderRadar._whisperSig || "")) {
          renderRadar._whisperSig = wSig;
          if (!tops.length) {
            whisper.innerHTML = "";
            whisper.hidden = true;
            whisper.setAttribute("hidden", "");
          } else {
            whisper.hidden = false;
            whisper.removeAttribute("hidden");
            whisper.innerHTML = tops.map((m) => {
              const t = String(m.ticker || "").toUpperCase();
              const pct = Number(m.pct_change);
              const up = Number.isFinite(pct) && pct >= 0;
              const tip = Number.isFinite(pct)
                ? t + " " + (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%"
                : t;
              return `<span class="radar-pill ${up ? "up" : "down"}" title="${escapeHtml(tip)}">${escapeHtml(t)}</span>`;
            }).join("");
          }
        }
      }
    }

    try { syncButlerStory(data); } catch (_) {}

    const lane = $("#radar-lane");
    if (!lane) return;
    if (!enabled) {
      if (lastRadarSig !== "__off__") {
        lane.innerHTML = `<span class="muted">Radar off — turn on to scan whole-market movers. The brain does not decide on a name until it enters Auto paper.</span>`;
        lastRadarSig = "__off__";
      }
      return;
    }
    if (!movers.length) {
      if (lastRadarSig !== "__empty__") {
        lane.innerHTML = `<span class="muted">No movers yet — refresh or wait for the next scan.</span>`;
        lastRadarSig = "__empty__";
      }
      return;
    }
    const sig = movers.map((m) => `${m.ticker}|${m.pct_change}|${m.score}`).join(";");
    if (sig === lastRadarSig) return;
    lastRadarSig = sig;
    lane.innerHTML = movers.slice(0, 24).map((m) => {
      const t = String(m.ticker || "").toUpperCase();
      const pct = Number(m.pct_change);
      const up = Number.isFinite(pct) && pct >= 0;
      const pctStr = Number.isFinite(pct) ? ((pct >= 0 ? "+" : "") + pct.toFixed(1) + "%") : "";
      const score = m.score != null ? Number(m.score).toFixed(0) : "";
      const tip = [t, pctStr, m.rel_volume != null ? "relative volume " + m.rel_volume : "", "paper research — not a signal"].filter(Boolean).join(" · ");
      return `<span class="radar-chip ${up ? "up" : "down"}" title="${escapeHtml(tip)}"><strong>${escapeHtml(t)}</strong><span class="radar-pct">${escapeHtml(pctStr)}</span>${score ? `<span class="radar-score">${escapeHtml(score)}</span>` : ""}</span>`;
    }).join("");
  }

  function renderHeat(data) {
    const lane = $("#heat-lane");
    if (!lane) return;
    const heatOn = data.heat_enabled !== false && (data.config ? data.config.heat_enabled !== false : true);
    let heat = heatOn ? (data.heat || (data.buzz && data.buzz.heat) || []).slice() : [];
    const panel = $("#heat-panel");
    if (panel) panel.classList.toggle("dimmed", !heatOn);
    // Spikes front (server usually already sorts); fade stale
    heat.sort((a, b) => {
      const as = a.is_spike ? 0 : 1;
      const bs = b.is_spike ? 0 : 1;
      if (as !== bs) return as - bs;
      return (Number(b.score) || 0) - (Number(a.score) || 0);
    });
    const sig = (focusTicker || "") + "#" + heat.map((h) => `${h.ticker}|${h.score}|${h.is_spike ? 1 : 0}|${h.mentions || ""}`).join(";");
    if (!heat.length) {
      if (lastHeatSig !== "__empty__") {
        lane.innerHTML = `<span class="muted">No buzz heat yet — names appear when Reddit mentions tickers (research only).</span>`;
        lastHeatSig = "__empty__";
      }
      prevHeatTickers = new Set();
      return;
    }
    if (sig === lastHeatSig) return;
    lastHeatSig = sig;
    const simple = getUiMode() === "simple";
    const show = simple ? heat.slice(0, 6) : heat.slice(0, 12);
    const scores = show.map((h) => Number(h.score) || 0);
    const maxScore = Math.max(1, ...scores);
    const nowHeat = new Set();
    lane.innerHTML = show.map((h) => {
      const t = String(h.ticker || "").toUpperCase();
      nowHeat.add(t);
      const scoreNum = Number(h.score) || 0;
      const mentions = h.mentions != null ? Number(h.mentions) : Math.round(scoreNum);
      const wl = h.in_watchlist ? " in-wl" : "";
      const foc = focusTicker === t ? " focus" : "";
      const spike = h.is_spike ? " is-spike" : "";
      const stale = h.is_stale ? " is-stale" : "";
      const appear = prevHeatTickers.size && !prevHeatTickers.has(t) ? " is-appear" : "";
      const level = Math.max(1, Math.min(5, Math.ceil((scoreNum / maxScore) * 5)));
      const src = h.source_label || ((h.sources || [])[0]) || "";
      const srcShort = src ? String(src).replace(/wallstreetbets/i, "WSB").slice(0, 10) : "";
      const meta = simple
        ? ""
        : `<span class="heat-meta"><span class="heat-score">${escapeHtml(String(mentions))}</span>${srcShort ? `<span class="heat-src">${escapeHtml(srcShort)}</span>` : ""}</span>`;
      const tipBits = [t];
      if (mentions) tipBits.push(mentions + " mentions");
      if (srcShort) tipBits.push(srcShort);
      if (h.is_spike) tipBits.push("rising mentions");
      tipBits.push("paper research — does not change your watchlist");
      const tip = escapeHtml(tipBits.join(" · "));
      return `<button type="button" class="heat-chip${wl}${foc}${spike}${stale}${appear}" data-heat="${escapeHtml(t)}" data-heat-level="${level}" title="${tip}">${escapeHtml(t)}${meta}</button>`;
    }).join("");
    prevHeatTickers = nowHeat;
  }

  $("#heat-lane")?.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-heat]");
    if (!btn) return;
    focusTicker = String(btn.dataset.heat || "").toUpperCase();
    const chatTicker = $("#chat-ticker");
    if (chatTicker) chatTicker.value = focusTicker;
    toast(`Looking at ${focusTicker}`);
    if (state) {
      lastHeatSig = "";
      lastOppSig = "";
      renderHeat(state);
      renderOpportunities(state);
      scheduleSparks(state);
    }
  });

  function svgSpark(closes, w, h) {
    if (!closes || closes.length < 2) return "";
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const span = max - min || 1;
    const pts = closes.map((c, i) => {
      const x = (i / (closes.length - 1)) * (w - 2) + 1;
      const y = h - 1 - ((c - min) / span) * (h - 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const up = closes[closes.length - 1] >= closes[0];
    const stroke = up ? "#8dffb0" : "#ffb0b0";
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><polyline fill="none" stroke="${stroke}" stroke-width="1.5" points="${pts}"/></svg>`;
  }

  let sparkCache = {};
  let sparkInflight = false;

  async function scheduleSparks(data) {
    if (sparkInflight) return;
    const tickers = [];
    const push = (t) => {
      const u = String(t || "").toUpperCase();
      if (u && !tickers.includes(u)) tickers.push(u);
    };
    const last = (data.loop && data.loop.last_decision) || (data.decisions_preview && data.decisions_preview[0]) || data.last_decision;
    if (last && last.ticker) push(last.ticker);
    if (focusTicker) push(focusTicker);
    const heat = data.heat || (data.buzz && data.buzz.heat) || [];
    heat.slice(0, 3).forEach((h) => push(h.ticker));
    (data.opportunities || []).slice(0, 3).forEach((o) => push(o.ticker));
    const simple = getUiMode() === "simple";
    const want = simple ? tickers.slice(0, 1) : tickers.slice(0, 4);
    if (!want.length) {
      const strip = $("#spark-strip");
      if (strip) strip.innerHTML = `<span class="muted spark-empty" title="Short price lines appear for focus tickers">No price lines yet — they appear for focus tickers</span>`;
      return;
    }
    const missing = want.filter((t) => !sparkCache[t] || Date.now() - sparkCache[t].at > 90000);
    if (missing.length) {
      sparkInflight = true;
      try {
        const data2 = await api(`/api/sparks?tickers=${encodeURIComponent(missing.join(","))}`, { timeoutMs: 10000 });
        const sparks = (data2 && data2.sparks) || {};
        for (const t of Object.keys(sparks)) {
          sparkCache[t] = { at: Date.now(), closes: sparks[t] || [] };
        }
      } catch (_) {
        /* fail soft */
      } finally {
        sparkInflight = false;
      }
    }
    const strip = $("#spark-strip");
    if (!strip) return;
    const sparkKey = want.join(",");
    const draw = sparkKey !== lastSparkKey;
    lastSparkKey = sparkKey;
    strip.innerHTML = want.map((t) => {
      const closes = (sparkCache[t] && sparkCache[t].closes) || [];
      const svg = svgSpark(closes, 72, 22);
      const appear = draw ? " is-appear" : "";
      const drawCls = draw ? " spark-draw" : "";
      return `<div class="spark-cell${appear}${drawCls}" data-spark="${escapeHtml(t)}" title="${escapeHtml(t)} short price line — research only"><span class="spark-t">${escapeHtml(t)}</span>${svg || '<span class="muted">-</span>'}</div>`;
    }).join("");
  }

  function connectSSE() {
    if (typeof EventSource === "undefined") {
      setLiveInd("polling");
      return;
    }
    try {
      if (sseEs) {
        try { sseEs.close(); } catch (_) {}
        sseEs = null;
      }
      if (sseReconnectTimer) {
        clearTimeout(sseReconnectTimer);
        sseReconnectTimer = null;
      }
      const q = loopFeedSeq ? `?since_seq=${loopFeedSeq}` : "";
      const es = new EventSource("/api/loop/stream" + q);
      sseEs = es;
      es.addEventListener("hello", () => {
        setLiveInd("live");
        sseRetryMs = 2000;
        statePollMs = 45000; // stream carries state_lite every ~5 s
        restartStatePoll();
        // Do NOT restartLoopPoll on every event - only on live/polling transitions below
      });
      es.addEventListener("decision", (ev) => {
        if (!sseLive) setLiveInd("live");
        try {
          const data = JSON.parse(ev.data);
          const seen = new Set(loopEvents.map((e) => e.id || e.seq));
          if (!seen.has(data.id) && !seen.has(data.seq)) {
            loopEvents = [data].concat(loopEvents).slice(0, 200);
            renderLoopFeed();
            noteTradeGlowFromEvent(data);
          }
          loopFeedSeq = Math.max(loopFeedSeq, Number(data.seq || 0));
          if (state) {
            state.loop = state.loop || {};
            // Prefer intent/decision for last_decision; fills update via their own fields
            const ek = String(data.event || "decision").toLowerCase();
            if (ek === "fill") {
              state.loop.last_fill = data;
              // Keep last_decision pointing at matching intent when possible
              if (!state.loop.last_decision || state.loop.last_decision.intent_id !== data.intent_id) {
                state.loop.last_decision = data;
              }
            } else if (ek !== "cancel") {
              state.loop.last_decision = data;
            }
            renderLoopPanel(state);
            if (fullStateSeen) maybeAlertFromState(state);
            scheduleSparks(state);
          }
        } catch (_) {}
      });
      // Native intent/fill/cancel listeners (SSE also dual-emits as decision)
      ["intent", "fill", "cancel"].forEach((name) => {
        es.addEventListener(name, (ev) => {
          if (!sseLive) setLiveInd("live");
          try {
            const data = JSON.parse(ev.data);
            const seen = new Set(loopEvents.map((e) => e.id || e.seq));
            if (!seen.has(data.id) && !seen.has(data.seq)) {
              loopEvents = [data].concat(loopEvents).slice(0, 200);
              renderLoopFeed();
              noteTradeGlowFromEvent(data);
            }
            loopFeedSeq = Math.max(loopFeedSeq, Number(data.seq || 0));
          } catch (_) {}
        });
      });
      es.addEventListener("loop", (ev) => {
        if (!sseLive) setLiveInd("live");
        try {
          const st = JSON.parse(ev.data);
          noteFresh();
          syncThinking(st);
          if (state) {
            state.loop = st;
            const ld = st.last_decision || {};
            const loopSig = [st.running, st.rth_ok, st.outside_rth, st.last_skip, st.decision_in_flight,
              st.last_decision_ts, ld.seq, ld.id, st.loop_enabled, st.pending_intents,
              JSON.stringify(st.session_totals || {})].join("|");
            if (loopSig !== connectSSE._loopSig) {
              connectSSE._loopSig = loopSig;
              renderLoopPanel(state);
              renderPaperChrome(state);
              if (fullStateSeen) maybeAlertFromState(state);
            }
          }
        } catch (_) {}
      });
      es.addEventListener("state_lite", (ev) => {
        if (!sseLive) setLiveInd("live");
        try {
          const lite = JSON.parse(ev.data);
          if (lite && lite.ok !== false) noteFresh();
          applyStateLite(lite);
        } catch (_) {}
      });
      es.onerror = () => {
        setLiveInd("polling");
        try { es.close(); } catch (_) {}
        sseEs = null;
        statePollMs = 8000;
        restartStatePoll();
        // Single reconnect timer
        if (sseReconnectTimer) clearTimeout(sseReconnectTimer);
        sseReconnectTimer = setTimeout(() => {
          sseReconnectTimer = null;
          connectSSE();
        }, sseRetryMs);
        sseRetryMs = Math.min(15000, sseRetryMs * 1.5);
      };
    } catch (_) {
      setLiveInd("polling");
    }
  }

  function restartStatePoll() {
    if (statePollTimer) clearInterval(statePollTimer);
    statePollTimer = setInterval(refresh, statePollMs);
  }

  function restartLoopPoll() {
    if (loopPollTimer) clearInterval(loopPollTimer);
    loopPollTimer = setInterval(pollLoopFeed, sseLive ? 60000 : 2500);
  }

  // Only restart loop poll when live/polling mode actually changes
  let _lastLiveMode = null;
  const _setLiveInd = setLiveInd;
  setLiveInd = function(mode) {
    _setLiveInd(mode);
    if (mode !== _lastLiveMode) {
      _lastLiveMode = mode;
      restartLoopPoll();
    }
  };

  window.addEventListener("pagehide", () => {
    if (sseEs) { try { sseEs.close(); } catch (_) {} sseEs = null; }
    if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  });

  
  const brainSel = $("#brain-mode-select");
  if (brainSel) {
    brainSel.addEventListener("change", async () => {
      const mode = brainSel.value;
      brainSel.dataset.userEditing = "1";
      try {
        await api("/api/config", {
          method: "POST",
          body: JSON.stringify({ brain_mode: mode }),
        });
        toast(`Brain set to ${mode} (paper only)`);
        await refresh();
      } catch (err) {
        toast(String(err.message || err), true);
      } finally {
        delete brainSel.dataset.userEditing;
      }
    });
  }

  // Paper friction knobs (slip_bps / fee_bps)
  function readFrictionInputs() {
    const slipEl = $("#friction-slip");
    const feeEl = $("#friction-fee");
    const slip = slipEl ? Number(slipEl.value) : 5;
    const fee = feeEl ? Number(feeEl.value) : 1;
    return { slip, fee };
  }
  const frictionSlip = $("#friction-slip");
  const frictionFee = $("#friction-fee");
  const onFrictionChange = () => {
    const { slip, fee } = readFrictionInputs();
    postFrictionConfig(slip, fee);
  };
  if (frictionSlip) {
    frictionSlip.addEventListener("change", onFrictionChange);
  }
  if (frictionFee) {
    frictionFee.addEventListener("change", onFrictionChange);
  }
  $$(".friction-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slip = Number(btn.dataset.slip);
      const fee = Number(btn.dataset.fee);
      const slipEl = $("#friction-slip");
      const feeEl = $("#friction-fee");
      if (slipEl) slipEl.value = String(slip);
      if (feeEl) feeEl.value = String(fee);
      const name = (btn.textContent || "preset").trim();
      postFrictionConfig(slip, fee, `Friction: ${name} (${slip}/${fee} bps)`);
    });
  });



  // --- Simple A+B: settings sheet, curve/pos expand, Feeling Lucky, News ---
  $("#simple-wl")?.addEventListener("input", (ev) => {
    ev.target.dataset.dirty = "1";
    const st = $("#simple-wl-status");
    if (st) st.textContent = "Unsaved changes";
  });
  $("#btn-simple-wl-save")?.addEventListener("click", async () => {
    const ta = $("#simple-wl");
    const st = $("#simple-wl-status");
    if (!ta) return;
    const before = (state?.config?.watchlist || []).slice();
    try {
      const data = await api("/api/config", { method: "POST", body: JSON.stringify({ watchlist: ta.value }) });
      delete ta.dataset.dirty;
      const wl = data?.config?.watchlist || [];
      ta.value = wl.join("\n");
      if (st) st.textContent = `Saved · ${wl.length} stocks`;
      const c = $("#simple-wl-count");
      if (c) c.textContent = `(${wl.length})`;
      toast(`Watchlist saved (${wl.length} stocks)`);
      // One-step undo for a mistaken replace
      const bar = $("#undo-bar");
      if (bar && before.length) {
        const txt = bar.querySelector("[data-undo-text]");
        if (txt) txt.textContent = "Watchlist saved.";
        bar.hidden = false;
        const btn = bar.querySelector("[data-undo-btn]");
        const hide = setTimeout(() => { bar.hidden = true; }, 8000);
        if (btn) btn.onclick = async () => {
          clearTimeout(hide);
          bar.hidden = true;
          await api("/api/config", { method: "POST", body: JSON.stringify({ watchlist: before.join("\n") }) });
          delete ta.dataset.dirty;
          ta.value = before.join("\n");
          if (st) st.textContent = `Restored · ${before.length} stocks`;
          toast("Watchlist restored");
          await refresh();
        };
      }
      await refresh();
    } catch (e) {
      if (st) st.textContent = "Not saved";
      toast(e.message, true);
    }
  });

  function portalPrefsToSimpleSheet(mode) {
    const alerts = $("#desk-alert-prefs");
    const friction = $("#paper-friction");
    const sheetBody = $("#simple-settings-body");
    const home = $("#loop-prefs-home");
    if (!alerts || !friction) return;
    if (mode === "simple" && sheetBody) {
      if (alerts.parentElement !== sheetBody) sheetBody.appendChild(alerts);
      if (friction.parentElement !== sheetBody) sheetBody.appendChild(friction);
    } else if (home) {
      if (alerts.parentElement !== home) home.appendChild(alerts);
      if (friction.parentElement !== home) home.appendChild(friction);
    }
  }

  const _origSetUiMode = setUiMode;
  // wrap setUiMode to portal prefs (setUiMode already defined — patch via after-call)
  $$(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      setTimeout(() => portalPrefsToSimpleSheet(getUiMode()), 0);
    });
  });

  let sheetPrevFocus = null;
  let sheetFocusables = [];

  function sheetIsOpen() {
    const sheet = $("#simple-settings-sheet");
    return !!(sheet && sheet.classList.contains("is-open") && !sheet.hidden);
  }

  function collectSheetFocusables() {
    const sheet = $("#simple-settings-sheet");
    if (!sheet) return [];
    return Array.from(
      sheet.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
    ).filter((el) => !el.disabled && el.offsetParent !== null);
  }

  function openSimpleSettings() {
    const sheet = $("#simple-settings-sheet");
    const backdrop = $("#simple-sheet-backdrop");
    if (!sheet || !backdrop) return;
    portalPrefsToSimpleSheet("simple");
    sheetPrevFocus = document.activeElement;
    sheet.hidden = false;
    backdrop.hidden = false;
    requestAnimationFrame(() => {
      sheet.classList.add("is-open");
      backdrop.classList.add("is-open");
      sheetFocusables = collectSheetFocusables();
      const closeBtn = $("#btn-simple-sheet-close");
      if (closeBtn) closeBtn.focus();
      else if (sheetFocusables[0]) sheetFocusables[0].focus();
    });
  }
  function closeSimpleSettings() {
    const sheet = $("#simple-settings-sheet");
    const backdrop = $("#simple-sheet-backdrop");
    if (!sheet) return;
    const wasOpen = sheet.classList.contains("is-open") || !sheet.hidden;
    sheet.classList.remove("is-open");
    if (backdrop) backdrop.classList.remove("is-open");
    sheetFocusables = [];
    const prev = sheetPrevFocus;
    sheetPrevFocus = null;
    setTimeout(() => {
      sheet.hidden = true;
      if (backdrop) backdrop.hidden = true;
      const restore = prev || $("#btn-simple-settings");
      if (wasOpen && restore && typeof restore.focus === "function") {
        try { restore.focus(); } catch (_) {}
      }
    }, 280);
  }
  $("#btn-simple-settings")?.addEventListener("click", () => openSimpleSettings());
  $("#btn-simple-sheet-close")?.addEventListener("click", () => closeSimpleSettings());
  $("#simple-sheet-backdrop")?.addEventListener("click", () => closeSimpleSettings());
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (sheetIsOpen()) {
        ev.preventDefault();
        closeSimpleSettings();
        return;
      }
    }
    if (!sheetIsOpen() || ev.key !== "Tab" || !sheetFocusables.length) return;
    // Refresh in case portal moved nodes
    sheetFocusables = collectSheetFocusables();
    if (!sheetFocusables.length) return;
    const first = sheetFocusables[0];
    const last = sheetFocusables[sheetFocusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  });

  $("#btn-curve-toggle")?.addEventListener("click", () => {
    const panel = $("#equity-score-panel");
    const btn = $("#btn-curve-toggle");
    if (!panel || !btn) return;
    const open = panel.classList.toggle("is-open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    try {
      if (open && window.EquityScoreboard) {
        if (typeof window.EquityScoreboard.forceRedraw === "function") window.EquityScoreboard.forceRedraw();
        else if (typeof window.EquityScoreboard.resize === "function") window.EquityScoreboard.resize();
      }
    } catch (_) {}
  });

  $("#btn-pos-toggle")?.addEventListener("click", () => {
    const panel = $("#positions-panel");
    const btn = $("#btn-pos-toggle");
    const hint = $("#pos-hint");
    if (!panel || !btn) return;
    const open = panel.classList.toggle("is-open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (hint) hint.textContent = open ? "tap to collapse" : "tap to expand";
  });

  function luckyPool(data) {
    const cfg = (data && data.config) || {};
    let wl = Array.isArray(cfg.watchlist) ? cfg.watchlist.slice() : [];
    wl = wl.map((t) => String(t || "").toUpperCase()).filter(Boolean);
    const focus = String(cfg.watchlist_focus || "liquid").toLowerCase();
    // Prefer liquid intersection when focus is liquid and curated list known from state
    let pool = wl.slice();
    try {
      const curated = (data && data.curated_liquid) || (data && data.liquid_universe) || null;
      if (focus === "liquid" && Array.isArray(curated) && curated.length) {
        const set = new Set(curated.map((t) => String(t).toUpperCase()));
        const inter = wl.filter((t) => set.has(t));
        if (inter.length) pool = inter;
      }
    } catch (_) {}
    // Blend radar whisper chips (optional)
    try {
      const movers = ((data && data.radar) || {}).movers || [];
      const tops = movers.slice(0, 5).map((m) => String(m.ticker || "").toUpperCase()).filter(Boolean);
      tops.forEach((t) => {
        if (!pool.includes(t)) pool.push(t);
      });
    } catch (_) {}
    return pool;
  }

  async function feelingLucky() {
    const btn = $("#btn-feeling-lucky");
    const data = state || {};
    const pool = luckyPool(data);
    if (!pool.length) {
      toast("Watchlist is empty — add tickers first", true);
      if (btn) btn.disabled = true;
      return;
    }
    if (btn) btn.disabled = false;
    const pick = pool[Math.floor(Math.random() * pool.length)];
    focusTicker = pick;
    const chatTicker = $("#chat-ticker");
    if (chatTicker) chatTicker.value = pick;
    _luckyTicker = pick;
    _luckyLine = "Feeling lucky — looking at " + pick + " for you (random focus only — no fill, not a tip)";
    _luckyUntil = Date.now() + 6000;
    try {
      if (window.DeskMotion && typeof window.DeskMotion.sparkle === "function") {
        window.DeskMotion.sparkle(btn);
      } else if (btn) {
        btn.classList.add("lucky-sparkle");
        setTimeout(() => btn.classList.remove("lucky-sparkle"), 900);
      }
    } catch (_) {}
    toast("Feeling lucky — " + pick);
    try { syncButlerStory(Object.assign({}, data, { pending_count: data.pending_count })); } catch (_) {}
    if (state) {
      lastHeatSig = "";
      lastOppSig = "";
      try { renderHeat(state); } catch (_) {}
      try { renderOpportunities(state); } catch (_) {}
      try { scheduleSparks(state); } catch (_) {}
      try { renderNews(state); } catch (_) {}
    }
    // Focus-only: never call /api/signals/generate — that path ingest_signal can
    // paper-fill (auto_paper) or broker-submit (auto_live). Lucky is research focus only.
  }

  function syncLuckyButton(data) {
    const btn = $("#btn-feeling-lucky");
    if (!btn) return;
    const pool = luckyPool(data || state || {});
    btn.disabled = pool.length === 0;
    btn.title = pool.length
      ? "Pick a random watchlist ticker and focus the desk (no fill, no Approve)"
      : "Watchlist empty — add tickers first";
  }

  $("#btn-feeling-lucky")?.addEventListener("click", () => {
    feelingLucky().catch(() => {});
  });

  function newsAge(ts) {
    if (ts == null) return "";
    let ms = Number(ts);
    if (!Number.isFinite(ms)) return "";
    if (ms < 1e12) ms *= 1000;
    const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    if (sec < 60) return sec + "s";
    if (sec < 3600) return Math.floor(sec / 60) + "m";
    if (sec < 86400) return Math.floor(sec / 3600) + "h";
    return Math.floor(sec / 86400) + "d";
  }

  let lastNewsSig = "";
  function renderNews(data) {
    const feed = $("#news-feed");
    const empty = $("#news-empty");
    const butler = $("#news-butler");
    const filter = $("#news-filter");
    const macroStrip = $("#macro-strip");
    const macroItems = $("#macro-strip-items");
    if (!feed) return;
    const wn = (data && data.watchlist_news) || {};
    let items = Array.isArray(wn.items) ? wn.items.slice() : [];
    const providers = wn.providers || {};
    const focus = String((data && data.focus_ticker) || focusTicker || "").toUpperCase();
    const positions = ((data && data.ledger) || {}).positions || [];
    const posSet = new Set(positions.map((p) => String(p.ticker || "").toUpperCase()).filter(Boolean));
    const radar = ((data && data.radar) || {}).movers || [];
    const hot = new Set(radar.slice(0, 8).map((m) => String(m.ticker || "").toUpperCase()).filter(Boolean));

    // Prefer focus, positions, radar hot
    items.sort((a, b) => {
      const at = String(a.ticker || "").toUpperCase();
      const bt = String(b.ticker || "").toUpperCase();
      const score = (t) => (t === focus ? 3 : 0) + (posSet.has(t) ? 2 : 0) + (hot.has(t) ? 1 : 0);
      return score(bt) - score(at);
    });

    const filt = filter && filter.value ? String(filter.value).toUpperCase() : "";
    if (filt) items = items.filter((n) => String(n.ticker || "").toUpperCase() === filt);

    // Populate filter options (Advanced)
    if (filter && !filter.dataset.bound) {
      filter.dataset.bound = "1";
      filter.addEventListener("change", () => {
        lastNewsSig = "";
        if (state) renderNews(state);
      });
    }
    if (filter) {
      const syms = Array.from(new Set((wn.items || []).map((n) => String(n.ticker || "").toUpperCase()).filter(Boolean))).sort();
      const cur = filter.value;
      const opts = ['<option value="">All symbols</option>'].concat(
        syms.map((s) => '<option value="' + escapeHtml(s) + '"' + (cur === s ? " selected" : "") + ">" + escapeHtml(s) + "</option>")
      );
      if (filter.options.length !== opts.length || filter.dataset.symSig !== syms.join(",")) {
        filter.dataset.symSig = syms.join(",");
        filter.innerHTML = opts.join("");
        if (cur) filter.value = cur;
      }
    }

    // Macro / calendar strip — headlines are display-only; macro CAN force Ask-first / size cut
    try {
      const macro = (data && data.macro) || {};
      const cal = macro.calendar || {};
      const risk = macro.risk || {};
      const chips = [];
      const events = cal.upcoming || cal.events || cal.items || [];
      if (Array.isArray(events)) {
        events.slice(0, 4).forEach((ev) => {
          const label = ev.title || ev.name || ev.event || ev.label || "";
          if (label) chips.push(String(label).slice(0, 48));
        });
      }
      const forceAsk = !!(risk && (risk.force_ask_first || risk.macro_force_ask_first));
      let sizeMult = null;
      try { if (risk && risk.size_mult != null) sizeMult = Number(risk.size_mult); } catch (_) {}
      if (forceAsk) chips.unshift("Ask-me-first on (macro day)");
      else if (sizeMult != null && Number.isFinite(sizeMult) && sizeMult < 1) {
        chips.unshift("Size cut to " + Math.round(sizeMult * 100) + "% (macro)");
      }
      if (risk && risk.butler_note) chips.push(String(risk.butler_note).slice(0, 64));
      if (risk && Array.isArray(risk.reasons)) {
        risk.reasons.slice(0, 2).forEach((r) => chips.push(String(r).slice(0, 40)));
      }
      if (macroStrip && macroItems) {
        if (chips.length) {
          macroStrip.hidden = false;
          macroItems.innerHTML = chips.map((c) => '<span class="macro-chip">' + escapeHtml(c) + "</span>").join("");
        } else {
          macroStrip.hidden = true;
          macroItems.innerHTML = "";
        }
      }
    } catch (_) {
      if (macroStrip) macroStrip.hidden = true;
    }

    const simple = getUiMode() === "simple" || document.body.classList.contains("ui-simple");
    const maxN = simple ? 5 : 18;
    const shown = items.slice(0, maxN);
    const sig = shown.map((n) => (n.ticker || "") + "|" + (n.title || "") + "|" + (n.ts || "")).join(";");
    const ok = wn.ok !== false;
    const err = wn.error || "";

    const newsPanel = $("#news-panel");
    if (butler) {
      if (!shown.length) {
        butler.textContent = simple
          ? "No headlines right now — display only (do not gate fills)"
          : (!ok || err)
            ? ("News quiet — " + (err || "providers not returning"))
            : "News quiet for now — watchlist headlines are display-only";
      } else {
        butler.textContent = simple
          ? "Watchlist headlines — display only, not a tip, do not gate fills"
          : "Headlines are display-only — they do not gate fills (macro calendar can)";
      }
    }

    if (!shown.length) {
      feed.innerHTML = "";
      if (newsPanel) newsPanel.classList.add("is-empty-quiet");
      if (empty) {
        empty.classList.remove("hidden");
        const main = $("#news-empty-main");
        const sub = $("#news-empty-sub");
        if (main) main.textContent = "No headlines right now";
        if (sub) {
          sub.textContent = err
            ? String(err).slice(0, 120)
            : (simple
              ? "Sources may be offline, or the watchlist is empty. Headlines are display-only — they do not gate fills."
              : "Finnhub / Yahoo may be offline or the watchlist is empty.");
        }
      }
      lastNewsSig = "empty";
      return;
    }
    if (newsPanel) newsPanel.classList.remove("is-empty-quiet");
    if (empty) empty.classList.add("hidden");
    if (sig === lastNewsSig) return;
    const prev = lastNewsSig;
    lastNewsSig = sig;
    feed.innerHTML = shown.map((n) => {
      const t = escapeHtml(String(n.ticker || "").toUpperCase() || "—");
      const title = escapeHtml(String(n.title || n.headline || "").slice(0, 140));
      const src = escapeHtml(String(n.source || n.publisher || "news"));
      const age = escapeHtml(newsAge(n.ts));
      const href = safeUrl(n.link || n.url);
      const tag = href ? "a" : "div";
      const hrefAttr = href ? ' href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer"' : "";
      return "<" + tag + ' class="news-row"' + hrefAttr + ">" +
        '<span class="news-sym">' + t + "</span>" +
        '<span class="news-title">' + title + "</span>" +
        '<span class="news-meta"><span class="news-src">' + src + "</span><span>" + age + "</span></span>" +
        "</" + tag + ">";
    }).join("");
    if (prev && prev !== "empty") {
      try {
        if (window.DeskMotion && typeof window.DeskMotion.markWaitingArrive === "function") {
          window.DeskMotion.markWaitingArrive(Array.from(feed.querySelectorAll(".news-row")).slice(0, 3));
        } else {
          feed.querySelectorAll(".news-row").forEach((el, i) => {
            if (i < 3) el.classList.add("motion-arrive");
          });
        }
      } catch (_) {}
    }
  }

  $("#btn-news-refresh")?.addEventListener("click", async () => {
    try {
      const q = focusTicker ? ("?symbols=" + encodeURIComponent(focusTicker)) : "";
      const res = await api("/api/research/news" + q, { timeoutMs: 20000 });
      if (state && res) {
        state.watchlist_news = res;
        lastNewsSig = "";
        renderNews(state);
      }
    } catch (err) {
      console.warn("news refresh soft-fail", err);
      const butler = $("#news-butler");
      if (butler) butler.textContent = "News refresh quiet — try again in a moment";
    }
  });

  // Curve strip meta from daily pnl
  function syncCurveStripMeta(data) {
    const el = $("#curve-strip-meta");
    if (!el) return;
    const pnl = Number((data && data.daily && data.daily.pnl) || 0);
    el.textContent = (pnl > 0 ? "+" : "") + (Number.isFinite(pnl) ? fmtMoney(pnl) : "—");
  }

  // Portal prefs on boot + wrap render paths
  portalPrefsToSimpleSheet(getUiMode());

  // Augment existing renderAll-driven ambient with news/lucky/curve meta via monkey-patch on syncSimpleAmbient end — call from refresh path
  const _syncAmbient = syncSimpleAmbient;
  syncSimpleAmbient = function (data) {
    _syncAmbient(data);
    try { syncLuckyButton(data); } catch (_) {}
    try { syncCurveStripMeta(data); } catch (_) {}
  };

  // DeskMotion fill pulse from loop events
  const _noteGlow = typeof noteTradeGlowFromEvent === "function" ? noteTradeGlowFromEvent : null;
  if (_noteGlow) {
    // can't easily wrap if const — soft listen via Mutation optional skip
  }


  // --- How to tutorial ---
  const HOWTO_SEEN_KEY = "tomahawk_howto_seen";
  let howtoPrevFocus = null;
  let howtoFocusables = [];

  function howtoIsOpen() {
    const m = $("#howto-modal");
    return !!(m && !m.classList.contains("hidden"));
  }

  function markHowtoSeen() {
    try { localStorage.setItem(HOWTO_SEEN_KEY, "1"); } catch (_) {}
  }

  function hasSeenHowto() {
    try { return localStorage.getItem(HOWTO_SEEN_KEY) === "1"; } catch { return false; }
  }

  function openHowto() {
    const modal = $("#howto-modal");
    if (!modal) return;
    howtoPrevFocus = document.activeElement;
    modal.classList.remove("hidden");
    const card = modal.querySelector(".howto-card");
    howtoFocusables = card
      ? Array.from(card.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
          .filter((el) => !el.disabled && el.offsetParent !== null)
      : [];
    const closeBtn = $("#btn-howto-close");
    if (closeBtn) closeBtn.focus();
    else if (howtoFocusables[0]) howtoFocusables[0].focus();
  }

  function closeHowto() {
    const modal = $("#howto-modal");
    if (!modal || modal.classList.contains("hidden")) return;
    const showOnce = $("#howto-show-once");
    // Prefer set seen on any close so first-visit is not naggy; uncheck = remind next visit.
    if (!showOnce || showOnce.checked) markHowtoSeen();
    modal.classList.add("hidden");
    howtoFocusables = [];
    const prev = howtoPrevFocus;
    howtoPrevFocus = null;
    if (prev && typeof prev.focus === "function") {
      try { prev.focus(); } catch (_) {}
    }
  }

  $("#btn-howto")?.addEventListener("click", () => openHowto());
  $("#btn-simple-mock")?.addEventListener("click", () => {
    window.location.href = "/static/simple_one_job_mock.html";
  });
  $("#btn-howto-close")?.addEventListener("click", () => closeHowto());
  $("#btn-howto-dismiss")?.addEventListener("click", () => closeHowto());
  $("#howto-modal")?.addEventListener("click", (ev) => {
    if (ev.target === $("#howto-modal")) closeHowto();
  });
  document.addEventListener("keydown", (ev) => {
    if (!howtoIsOpen() || ev.key !== "Tab" || !howtoFocusables.length) return;
    const first = howtoFocusables[0];
    const last = howtoFocusables[howtoFocusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  });

  // First visit: open once after a short delay (does not block Start / trading)
  if (!hasSeenHowto()) {
    setTimeout(() => {
      if (!hasSeenHowto()) openHowto();
    }, 400);
  }

  refresh();
  refreshCuratedMeta();
  restartLoopPoll();
  restartStatePoll();
  connectSSE();
  sparkTimer = setInterval(() => {
    if (state) scheduleSparks(state);
  }, 95000);
})();
