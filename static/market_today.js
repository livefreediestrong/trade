/* Market today visualizer — polls /api/market-today every ~20s. Fail soft. */
(function () {
  const REFRESH_MS = 20000;
  const rootSel = "#market-today";
  let timer = null;
  let lastSig = "";

  function $(sel, el) { return (el || document).querySelector(sel); }

  function fmtPct(n) {
    if (typeof n !== "number" || !Number.isFinite(n)) return "—";
    const sign = n > 0 ? "+" : "";
    return sign + n.toFixed(2) + "%";
  }

  function fmtPx(n) {
    if (typeof n !== "number" || !Number.isFinite(n)) return "—";
    return n >= 100 ? n.toFixed(2) : n.toFixed(n >= 10 ? 2 : 3);
  }

  function sparkPath(closes) {
    const pts = (closes || []).filter((x) => typeof x === "number" && Number.isFinite(x));
    if (pts.length < 2) return "";
    const w = 100, h = 22, pad = 1;
    let min = Math.min(...pts), max = Math.max(...pts);
    if (max === min) { max += 1; min -= 1; }
    const span = max - min;
    return pts.map((v, i) => {
      const x = pad + (i / (pts.length - 1)) * (w - pad * 2);
      const y = pad + (1 - (v - min) / span) * (h - pad * 2);
      return (i ? "L" : "M") + x.toFixed(2) + " " + y.toFixed(2);
    }).join(" ");
  }

  function tone(chg) {
    if (typeof chg !== "number" || !Number.isFinite(chg)) return "flat";
    if (chg > 0.01) return "up";
    if (chg < -0.01) return "dn";
    return "flat";
  }

  function render(data) {
    const root = $(rootSel);
    if (!root) return;
    const grid = $("#mt-grid", root);
    const ts = $("#mt-ts", root);
    const summary = $("#mt-summary", root);
    const err = $("#mt-err", root);
    if (!grid) return;

    const items = (data && data.items) || [];
    const when = (data && (data.as_of_local || data.as_of)) || "";
    if (ts) ts.textContent = when ? ("Updated " + when) : "Waiting…";

    let up = 0, dn = 0, flat = 0;
    items.forEach((it) => {
      const t = tone(it.change_pct);
      if (t === "up") up += 1;
      else if (t === "dn") dn += 1;
      else flat += 1;
    });
    if (summary) {
      if (!items.length) {
        summary.innerHTML = '<span class="flat">Loading market pulse…</span>';
      } else {
        const lean = up > dn + 1 ? "Mostly higher" : dn > up + 1 ? "Mostly lower" : "Mixed";
        summary.innerHTML =
          '<span class="' + (lean.indexOf("higher") >= 0 ? "up" : lean.indexOf("lower") >= 0 ? "dn" : "flat") + '">' + lean + "</span>" +
          '<span class="up">' + up + " up</span>" +
          '<span class="dn">' + dn + " down</span>" +
          (flat ? '<span class="flat">' + flat + " flat</span>" : "");
      }
    }

    if (err) {
      if (data && data.ok === false && data.error) {
        err.hidden = false;
        err.textContent = "Market pulse unavailable: " + String(data.error).slice(0, 120);
      } else {
        err.hidden = true;
        err.textContent = "";
      }
    }

    const sig = items.map((it) => it.symbol + ":" + (it.change_pct ?? "")).join("|");
    const maxAbs = Math.max(0.5, ...items.map((it) => Math.abs(Number(it.change_pct) || 0)));

    grid.innerHTML = items.map((it) => {
      const chg = Number(it.change_pct);
      const t = tone(chg);
      const barPct = Math.max(4, Math.min(100, Math.round((Math.abs(chg) / maxAbs) * 100)));
      const path = sparkPath(it.spark);
      const flash = lastSig && lastSig.indexOf(it.symbol + ":") >= 0 && lastSig !== sig ? " is-flash" : "";
      return (
        '<article class="mt-tile is-' + t + flash + '" data-symbol="' + String(it.symbol || "") + '">' +
          '<span class="mt-sym">' + String(it.symbol || "") + "</span>" +
          '<span class="mt-name">' + String(it.name || "") + "</span>" +
          '<span class="mt-chg">' + fmtPct(chg) + "</span>" +
          '<span class="mt-px">' + fmtPx(Number(it.price)) + "</span>" +
          '<div class="mt-bar" aria-hidden="true"><div class="mt-bar-fill" style="width:' + barPct + '%"></div></div>' +
          (path
            ? '<svg class="mt-spark" viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true"><path d="' + path + '"/></svg>'
            : "") +
        "</article>"
      );
    }).join("") || '<p class="mt-err">No market quotes yet.</p>';

    lastSig = sig;
  }

  async function refresh() {
    try {
      const res = await fetch("/api/market-today", { credentials: "same-origin" });
      const data = await res.json();
      render(data);
    } catch (e) {
      render({ ok: false, error: (e && e.message) || "network", items: [] });
    }
  }

  function start() {
    if (!$(rootSel)) return;
    refresh();
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, REFRESH_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refresh();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
