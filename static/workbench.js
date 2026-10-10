/* Plain optional desk tools. No order submission and no background polling. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = (x, digits = 2) => x == null || !Number.isFinite(Number(x)) ? "Unavailable" : Number(x).toLocaleString(undefined, {maximumFractionDigits: digits});
  const pct = (x) => x == null ? "Unmeasured" : `${fmt(x * 100, 1)}%`;
  const text = (id, value) => { if ($(id)) $(id).textContent = value; };
  async function api(path, body, signal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, {once:true});
    const timer = setTimeout(abort, 25000);
    try {
      const response = await fetch(path, {signal: controller.signal,
        ...(body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)})});
      const data = await response.json();
      if (!response.ok || data.ok === false) throw Error(data.error || "Request failed");
      return data;
    } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }
  function task(button, status, action) {
    if (button?.disabled) return;
    if (button) button.disabled = true;
    return Promise.resolve().then(action).catch(e => text(status, e.name === "AbortError" ? "Request timed out or was superseded. Try again." : e.message))
      .finally(() => { if (button) button.disabled = false; });
  }
  function table(headers, rows) {
    return `<table><thead><tr>${headers.map(h => `<th scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c=>`<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  }
  function safeUrl(url) { try { const u = new URL(url); return ["https:", "http:"].includes(u.protocol) ? u.href : null; } catch (_) { return null; } }

  // Shared state arrives from the existing stream; this module adds no poller.
  window.addEventListener("desk:state", ({detail:s}) => {
    const symbols = (s.config?.watchlist || []).filter(t => /^[A-Z][A-Z0-9.\-]{0,9}$/.test(t));
    const selector = $("linked-watchlist-symbol"), signature = symbols.join(",");
    if (selector && symbols.length && selector.dataset.symbols !== signature) {
      const selected = selector.value;
      selector.innerHTML = '<option value="">Choose from your watchlist</option>' + symbols.map(t=>`<option>${esc(t)}</option>`).join("");
      selector.value = symbols.includes(selected) ? selected : ""; selector.dataset.symbols = signature;
    }
    const book = s.broker_book || {}, diag = book.pnl_diagnostics || {};
    const selected = $("linked-symbol")?.value.trim().toUpperCase();
    const signals = Object.values(s.signals || {}).flat().filter(x => x && typeof x === "object");
    signals.sort((a,b) => (Date.parse(b.created_at || b.ts) || 0) - (Date.parse(a.created_at || a.ts) || 0));
    const latest = signals.find(x => x.ticker === selected) || signals[0] || {};
    const quote = latest.quote || {};
    const account = String(book.account_id || "");
    const quoteAge = quote.market_time ? Math.round((Date.now()-Date.parse(quote.market_time))/1000) : null;
    const quoteFresh = !!(quote.fresh && quoteAge != null && quoteAge <= 120 && quoteAge >= -15);
    const rows = [
      ["Account", account ? `…${account.slice(-4)} · ${book.paper_mode ? "BROKER PAPER" : "LIVE"}` : "Unverified", account ? (book.paper_mode ? "ok" : "warn") : "bad"],
      ["Broker data", book.ok ? "Account/positions available" : book.error || "Unavailable", book.ok ? "ok" : "bad"],
      ["Daily P&L", book.day_pnl_usd == null ? "Unavailable; new risk blocked" : `$${fmt(book.day_pnl_usd)}`, book.day_pnl_usd == null ? "bad" : "ok"],
      ["P&L feed", diag.status || "Unavailable", diag.status === "ready" ? "ok" : diag.status ? "warn" : "unknown"],
      ["Value update age", diag.last_update_age_seconds == null ? "Unavailable" : `${fmt(diag.last_update_age_seconds,0)}s`, diag.last_update_age_seconds == null ? "unknown" : Number(diag.last_update_age_seconds) > 60 ? "warn" : "ok"],
      ["Gateway response age", diag.api_response_age_seconds == null ? "Unavailable" : `${fmt(diag.api_response_age_seconds,0)}s`, diag.api_response_age_seconds == null ? "unknown" : Number(diag.api_response_age_seconds) > 60 ? "warn" : "ok"],
      ["Research quote", quote.source ? `${latest.ticker} · ${quote.source} · ${quote.market_time || "unknown time"} · age ${fmt(quoteAge,0)}s · ${quoteFresh ? "fresh" : "stale"}` : "No timestamped quote", quote.source ? (quoteFresh ? "ok" : "warn") : "unknown"],
      ["Research/model block", latest.execution_block || latest.error || latest.brain_error || "No block recorded on the latest item", latest.execution_block || latest.error || latest.brain_error ? "warn" : "ok"],
      ["New risk check", book.risk_error || (book.risk_ready ? "Account check passed; order checks still apply" : "Not ready"), book.risk_error ? "bad" : book.risk_ready ? "ok" : "warn"]
    ];
    const ico = {ok: "✓", warn: "!", bad: "✕", unknown: "·"};
    const ageSec = diag.last_update_age_seconds != null ? Number(diag.last_update_age_seconds)
      : (quoteAge != null ? Number(quoteAge) : null);
    const freshnessPct = ageSec == null ? 0 : Math.max(0, Math.min(100, Math.round(100 - Math.min(ageSec, 120) / 120 * 100)));
    const freshLabel = ageSec == null ? "Freshness unknown" : ageSec <= 25 ? `Current · ${Math.round(ageSec)}s ago` : `Stale · ${Math.round(ageSec)}s ago`;
    const html = `<div class="health-grid">${rows.map(([k,v,tone]) =>
      `<div class="health-cell"><span class="health-ico ${tone}" aria-hidden="true">${ico[tone]||"·"}</span><div><span class="health-k">${esc(k)}</span><span class="health-v">${esc(v)}</span></div></div>`
    ).join("")}</div>
    <div class="health-fresh-wrap" aria-label="Data freshness">
      <div class="health-fresh-dial ${ageSec != null && ageSec > 25 ? "is-stale" : ""}" style="--fresh:${freshnessPct}" aria-hidden="true"></div>
      <div class="health-fresh-copy"><strong>${esc(freshLabel)}</strong>Desk snapshot and feed ages for account checks.</div>
    </div>`;
    text("health-overview", `${book.ok ? "Broker data available" : "Broker unavailable"} · daily P&L ${book.day_pnl_usd == null ? "unavailable" : "$"+fmt(book.day_pnl_usd)} · ${quoteFresh ? "research quote fresh" : "research quote stale/unavailable"}`);
    if ($("health-summary") && $("health-summary").innerHTML !== html) $("health-summary").innerHTML = html;
  });

  let symbolSequence = 0, symbolAbort, currentChart = null;
  async function selectSymbol(symbol) {
    symbol = String(symbol || "").trim().toUpperCase();
    const global = symbol.startsWith('YF:') || /[=^]/.test(symbol) || /^\d/.test(symbol) || /\.(?![AB]$)/.test(symbol) || /-USD$/.test(symbol);
    if (!/^(?:YF:)?[A-Z0-9^][A-Z0-9.^=\-]{0,31}$/.test(symbol)) { text("symbol-status", "Enter a stock symbol or an exact provider identity such as YF:7203.T."); return; }
    $("linked-symbol").value = symbol;
    symbolAbort?.abort(); symbolAbort = new AbortController();
    const sequence = ++symbolSequence;
    currentChart = null;
    ["linked-chart", "linked-news", "linked-theses", "chart-detail"].forEach(id => text(id, ""));
    $("chart-inspector-label").hidden = true;
    if ($('follow-global')) $('follow-global').hidden = true;
    text('instrument-capability','');
    text("symbol-status", `Loading ${symbol}…`);
    try {
      const path = global ? '/api/markets/instrument/' : '/api/workbench/symbol/';
      const result = await api(path+encodeURIComponent(symbol), undefined, symbolAbort.signal);
      if (sequence !== symbolSequence) return;
      currentChart = result;
      if (result.instrument) {
        text('instrument-capability', `${result.instrument.name} · ${result.instrument.asset_type} · ${result.instrument.exchange} · ${result.quote_unit} · ${result.quality}. ${result.instrument.capability}. ${result.notes.join(' ')}`);
        if ($('follow-global')) { $('follow-global').hidden = false; $('follow-global').dataset.instrument = result.instrument.id; }
      }
      text("symbol-status", `${symbol} · ${result.interval} · ${result.source || "source unavailable"} · ${result.session} · retrieved ${new Date(result.retrieved_at).toLocaleString()}${result.error ? ` · ${result.error}` : ""}`);
      drawChart();
      $("linked-news").innerHTML = result.news.length ? result.news.map(n => {
        const url = safeUrl(n.url || n.link);
        const headline = esc(n.title || n.headline);
        return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${headline}</a>` : headline} <small>${esc(n.source)}</small></li>`;
      }).join("") : "<li>No headlines returned.</li>";
      $("linked-theses").innerHTML = result.theses.length ? result.theses.map(s => `<p><strong>${esc(s.workspace)} · ${esc(s.side)} · ${esc(s.status)}</strong><br>${esc(s.why_plain || s.llm_thesis || s.thesis || "No thesis recorded")}</p>`).join("") : "<p>No saved thesis for this symbol.</p>";
    } catch (e) { if (sequence === symbolSequence) text("symbol-status", e.name === "AbortError" ? "Research request timed out; retry." : e.message); }
  }
  function drawChart() {
    if (!currentChart) return;
    const bars = currentChart.candles.slice(-Number($("chart-window").value));
    if (!bars.length) return;
    const low = Math.min(...bars.map(b=>b.low)), high = Math.max(...bars.map(b=>b.high));
    const span = Math.max(high-low, high * .001), volume = Math.max(1,...bars.map(b=>b.volume || 0));
    const w = 760 / bars.length, y = p => 220 - (p-low) / span * 190;
    const candles = bars.map((b,i) => {
      const x = 30 + (i+.5)*w, rise = b.close >= b.open;
      return `<g class="${rise ? "candle-up" : "candle-down"}"><title>${esc(b.date.slice(0,10))}: O ${b.open} H ${b.high} L ${b.low} C ${b.close}, volume ${b.volume ?? "unavailable"}</title><line x1="${x}" x2="${x}" y1="${y(b.high)}" y2="${y(b.low)}"/><rect x="${x-w*.3}" y="${Math.min(y(b.open),y(b.close))}" width="${w*.6}" height="${Math.max(1,Math.abs(y(b.open)-y(b.close)))}"/><rect opacity=".45" x="${x-w*.3}" y="${280-(b.volume||0)/volume*45}" width="${w*.6}" height="${(b.volume||0)/volume*45}"/></g>`;
    }).join("");
    $("linked-chart").innerHTML = `<svg viewBox="0 0 820 305" role="img" aria-label="${esc(currentChart.symbol)} daily candles and volume. Use the bar inspector for exact values."><text x="30" y="18">${esc(currentChart.symbol)} · ${fmt(high)} high / ${fmt(low)} low</text>${candles}<text x="30" y="300">${esc(bars[0].date.slice(0,10))}</text><text x="690" y="300">${esc(bars.at(-1).date.slice(0,10))}</text></svg>`;
    $("chart-inspector").max = bars.length-1;
    $("chart-inspector").value = bars.length-1;
    $("chart-inspector-label").hidden = false;
    inspectBar();
  }
  function inspectBar() {
    const bars = currentChart?.candles.slice(-Number($("chart-window").value));
    const b = bars?.[Number($("chart-inspector").value)];
    if (b) text("chart-detail", `${b.date.slice(0,10)} · ${currentChart.quote_unit || 'USD'} · Open ${fmt(b.open)} · High ${fmt(b.high)} · Low ${fmt(b.low)} · Close ${fmt(b.close)} · Volume ${fmt(b.volume,0)}${b.complete === false ? ' · potentially incomplete' : ''}`);
  }
  $("symbol-form")?.addEventListener("submit", e => { e.preventDefault(); selectSymbol($("linked-symbol").value); });
  window.addEventListener('desk:instrument', e => selectSymbol(e.detail.id));
  $("chart-window")?.addEventListener("change", drawChart);
  $("chart-inspector")?.addEventListener("input", inspectBar);
  $("linked-watchlist-symbol")?.addEventListener("change", e => { if (e.target.value) selectSymbol(e.target.value); });
  document.addEventListener("click", e => { const node = e.target.closest("[data-spark]"); if (node) { selectSymbol(node.dataset.spark); $("symbol-workspace").scrollIntoView({block:"start"}); } });

  function journalQuery() {
    const values = new FormData($("journal-filter")), query = new URLSearchParams();
    for (const [key, value] of values) if (value) query.set(key, ["from","to"].includes(key) ? new Date(value).toISOString() : value.trim());
    return query;
  }
  $("journal-filter")?.addEventListener("submit", e => {
    e.preventDefault(); task(e.submitter,"journal-summary",async () => {
      const query = journalQuery(), result = await api(`/api/workbench/journal?${query}`);
      query.set("format","csv"); $("journal-export").href = `/api/workbench/journal?${query}`;
      text("journal-summary", `${result.total} matching records${result.total > result.rows.length ? `; showing ${result.rows.length}, CSV includes all matches` : ""}. ${result.scope}`);
      $("journal-table").innerHTML = table(["Time","Workspace","Record","Symbol","Side","Status","Shares","Price","Fees","Strategy"],result.rows.map(r=>[r.ts,r.workspace,r.kind,r.ticker,r.side,r.status,r.shares,r.price,r.fee_usd ?? "Unknown",r.strategy]));
    });
  });
  $("journal-filter")?.addEventListener("input", () => { try { const q = journalQuery(); q.set("format","csv"); $("journal-export").href = `/api/workbench/journal?${q}`; } catch (_) {} });
  $("evaluation-refresh")?.addEventListener("click", e => task(e.currentTarget,"evaluation-results",async()=>{
    const result = await api("/api/workbench/evaluation");
    $("evaluation-results").innerHTML = result.groups.length ? table(["Model / prompt","Workspace","Horizon s","Calls","Scored","Coverage","Abstained","After-cost positive","Brier","Evidence"], result.groups.map(g=>[`${g.model} / ${g.prompt_version}`,g.workspace,g.horizon_sec,g.decisions,g.scored,pct(g.coverage),pct(g.abstention_rate),pct(g.win_rate),fmt(g.brier,4),g.evidence])) : "No captured decisions yet.";
  }));
  function showExperiments(result) {
    $("experiment-results").innerHTML = result.experiments.length ? result.experiments.map(e=>`<article><h4>${esc(e.parameters.name)}</h4><p>${esc(e.parameters.strategy_version)} · $${fmt(e.parameters.capital)} capital · ${e.result.eligible} eligible / ${e.result.excluded} excluded · ${e.result.filled_scenarios} filled scenarios · scenario P&amp;L sum $${fmt(e.result.scenario_pnl_sum)}</p><p>Capture ${esc(e.id.slice(0,16))} · ${esc(e.created_at)}</p><button class="btn ghost" type="button" data-replay="${esc(e.id)}">Verify replay</button> <a href="/api/workbench/experiments/${esc(e.id)}" download="experiment-${esc(e.id.slice(0,16))}.json">Export capture + results</a><p data-replay-result="${esc(e.id)}" role="status"></p></article>`).join("") : "No saved experiments.";
  }
  $("experiment-form")?.addEventListener("submit", e=>{
    e.preventDefault();
    task(e.submitter, "experiment-results", async () => {
      showExperiments(await api("/api/workbench/experiments", Object.fromEntries(new FormData(e.target))));
    });
  });
  $("experiments-refresh")?.addEventListener("click", e=>task(e.currentTarget,"experiment-results",async()=>showExperiments(await api("/api/workbench/experiments"))));
  $("experiment-results")?.addEventListener("click", e=>{const b=e.target.closest("[data-replay]"); if(!b)return; task(b,"experiment-results",async()=>{const r=await api(`/api/workbench/experiments/${b.dataset.replay}`); const target=document.querySelector(`[data-replay-result="${b.dataset.replay}"]`); target.textContent=r.identical ? "Capture hash verified; replay exactly matches the saved result." : "Replay differs from the saved result.";});});
  let savedLists = {};
  function showLists(result) { savedLists=result.watchlists; $("saved-watchlist-select").innerHTML='<option value="">Choose a saved list</option>'+Object.keys(savedLists).map(n=>`<option>${esc(n)}</option>`).join(""); }
  $("saved-watchlist-form")?.addEventListener("submit",e=>{e.preventDefault();task(e.submitter,"saved-watchlist-status",async()=>{showLists(await api("/api/workbench/watchlists",Object.fromEntries(new FormData(e.target))));text("saved-watchlist-status","Watchlist saved. Apply it explicitly when ready.");});});
  $("saved-watchlist-load")?.addEventListener("click",e=>task(e.currentTarget,"saved-watchlist-status",async()=>showLists(await api("/api/workbench/watchlists"))));
  $("saved-watchlist-apply")?.addEventListener("click",e=>task(e.currentTarget,"saved-watchlist-status",async()=>{const list=savedLists[$("saved-watchlist-select").value];if(!list)throw Error("Choose a saved list first");await api("/api/config",{watchlist:list});text("saved-watchlist-status",`Applied research watchlist: ${list.join(", ")}`);window.dispatchEvent(new Event("desk:refresh"));}));

  $("settings-search")?.addEventListener("input",e=>{
    const q=e.target.value.trim().toLowerCase();
    const panels=[...document.querySelectorAll("#desk-settings .panel")];
    panels.forEach(p=>{p.hidden=!!q&&!p.textContent.toLowerCase().includes(q);});
    text("settings-search-status",q?`${panels.filter(p=>!p.hidden).length} matching sections`:"All settings sections shown");
  });
  // Navigation only: never submit, cancel, skip or approve an order via shortcut.
  document.addEventListener("keydown",e=>{
    if(e.defaultPrevented||e.ctrlKey||e.metaKey||e.altKey||e.target.closest("input,textarea,select,[contenteditable=true]")||document.querySelector(".modal:not(.hidden)"))return;
    const destinations={"1":"desk-live","2":"desk-paper","3":"desk-research","4":"desk-settings"};
    if(e.key==="/"){e.preventDefault();$("linked-symbol")?.focus();}
    else if(destinations[e.key]){e.preventDefault();$(destinations[e.key])?.scrollIntoView({block:"start"});}
  });
})();
