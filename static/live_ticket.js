/* Explicit user tickets. No activation, background submission, or automatic retry. */
(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const form = $('live-ticket-form');
  if (!form) return;

  let review = null;
  let generation = 0;
  let previewing = false;
  let submitting = false;
  let snapshot = null;
  let received = 0;
  let instrument = 'stock'; // stock | option
  let unit = 'dollars'; // dollars | shares
  let lastPrice = null;
  let lastPriceMeta = 'Last known';

  const selected = name => form.querySelector(`input[name="${name}"]:checked`)?.value;
  const money = value =>
    typeof value === 'number' && Number.isFinite(value)
      ? value.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
      : 'Unknown';
  const finite = n => typeof n === 'number' && Number.isFinite(n);
  const message = text => { $('live-ticket-result').textContent = text; };

  function setPressed(btn, on) {
    if (!btn) return;
    btn.classList.toggle('is-active', !!on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }

  function clearReview() {
    generation += 1;
    review = null;
    $('live-ticket-review').hidden = true;
    $('live-ticket-ack').value = '';
    $('live-ticket-send').disabled = true;
  }

  /** Treat validator-ceiling live_agent numbers as open (null display). */
  function practicalCap(value, kind) {
    const n = Number(value);
    if (!finite(n)) return null;
    if (kind === 'trades') return n >= 10000 ? null : n;
    return n >= 1e8 ? null : n; // order / daily-loss USD
  }

  function capsFromSnapshot() {
    const cfg = snapshot?.config || {};
    const agent = cfg.live_agent;
    const ks = agent
      ? {
          armed: true,
          max_daily_loss_usd: agent.policy?.max_daily_loss_usd,
          max_trades_per_day: agent.policy?.max_orders_per_day,
          max_position_size_usd: agent.policy?.max_order_usd
        }
      : (cfg.kill_switch || {});
    const maxOrder = practicalCap(ks.max_position_size_usd, 'usd');
    const dailyLoss = practicalCap(ks.max_daily_loss_usd, 'usd');
    const maxTrades = practicalCap(ks.max_trades_per_day, 'trades');
    const used = Number(snapshot?.loop?.session_totals?.intents || snapshot?.daily?.trades || 0);
    const left = maxTrades != null ? Math.max(0, maxTrades - (finite(used) ? used : 0)) : null;
    const armed = ks.armed === true && (maxOrder != null || dailyLoss != null || maxTrades != null);
    return { maxOrder, dailyLoss, left, armed };
  }

  function quoteForSymbol(symbol) {
    const sym = (symbol || '').trim().toUpperCase();
    if (!sym) return null;
    if (review?.ticker === sym && finite(Number(review?.quote?.price))) return Number(review.quote.price);
    if (finite(lastPrice) && ($('live-ticket-symbol').value.trim().toUpperCase() === sym)) return lastPrice;
    const rows = snapshot?.broker_book?.positions || [];
    const hit = rows.find(p => String(p.ticker || '').toUpperCase() === sym);
    if (hit && finite(Number(hit.last))) return Number(hit.last);
    return null;
  }

  function refreshLastDisplay() {
    const sym = $('live-ticket-symbol').value.trim().toUpperCase();
    const px = quoteForSymbol(sym);
    if (px != null) {
      lastPrice = px;
      $('live-ticket-last').textContent = money(px);
      const fromBook = (snapshot?.broker_book?.positions || []).some(
        p => String(p.ticker || '').toUpperCase() === sym && finite(Number(p.last))
      );
      lastPriceMeta =
        review?.ticker === sym && review?.quote?.source
          ? String(review.quote.source)
          : fromBook
            ? 'broker book'
            : 'from desk';
      $('live-ticket-last-meta').textContent = lastPriceMeta;
    } else {
      $('live-ticket-last').textContent = '—';
      $('live-ticket-last-meta').textContent = 'No quote in snapshot';
    }
  }

  function ensureCapMeter(capEl) {
    if (!capEl || typeof capEl.closest !== 'function') return null;
    const tile = capEl.closest('.lt-cap');
    if (!tile) return null;
    let meter = tile.querySelector('.lt-cap-meter');
    if (!meter) {
      meter = document.createElement('div');
      meter.className = 'lt-cap-meter';
      meter.setAttribute('aria-hidden', 'true');
      const fill = document.createElement('div');
      fill.className = 'lt-cap-fill';
      meter.appendChild(fill);
      tile.appendChild(meter);
    }
    return { tile, fill: meter.querySelector('.lt-cap-fill') };
  }


  function refreshChecklist() {
    const list = $('live-ticket-check-list');
    if (!list) return;
    const cfg = snapshot?.config || {};
    const book = snapshot?.broker_book || {};
    const goal = snapshot?.live_goal || {};
    const strat = snapshot?.strategy_status || {};
    const { maxOrder, dailyLoss, left } = capsFromSnapshot();
    const dayPnl = goal.pnl_usd != null ? goal.pnl_usd : book.day_pnl_usd;
    const target = goal.target_usd != null ? goal.target_usd : cfg.daily_profit_target_usd;
    const riskReady = book.risk_ready === true || strat.risk_ready === true;
    const sym = ($('live-ticket-symbol')?.value || '').trim().toUpperCase();
    const pol = cfg.live_agent?.policy || {};
    const allowed = Array.isArray(strat.symbols) && strat.symbols.length
      ? strat.symbols.map(s => String(s).toUpperCase())
      : (Array.isArray(pol.symbols) ? pol.symbols.map(s => String(s).toUpperCase()) : null);
    const items = [];

    // Edge / live ORDER allow-list (evaluation may cover the full watchlist)
    const evalCount = strat.eval_symbols_count != null ? Number(strat.eval_symbols_count) : null;
    if (allowed && allowed.length) {
      const ok = !sym || allowed.includes(sym);
      const n = Number.isFinite(evalCount) && evalCount > 0 ? evalCount : allowed.length;
      const orderLabel = n > 1
        ? `Live orders: full equity universe (${n}) · uncapped (broker rules)`
        : `Live orders: ${allowed[0]} · uncapped (broker rules)`;
      const evalNote = Number.isFinite(evalCount) ? ` · eval: full watchlist (${evalCount})` : ' · eval: full watchlist';
      items.push({ ok, text: ok
        ? `${orderLabel}${evalNote}`
        : `Symbol ${sym} outside live-order allow-list — eval may still rank it; do not force auto-order` });
    } else {
      items.push({ ok: true, text: 'Live orders: full universe · uncapped (broker rules) · review/ack/risk_ready stay' });
    }

    // Risk ready
    items.push({
      ok: riskReady && typeof dayPnl === 'number' && Number.isFinite(dayPnl),
      text: riskReady && typeof dayPnl === 'number' && Number.isFinite(dayPnl)
        ? `risk_ready · day P&L ${money(dayPnl)}`
        : `risk_ready false / day P&L unknown — new risk blocked`
    });

    // Orders left
    if (left != null) {
      items.push({
        ok: left > 0,
        text: left > 0
          ? `${left} order slot${left === 1 ? '' : 's'} left today — treat as scarce`
          : '0 orders left today — skip new entries; manage / journal only'
      });
    }

    // Size vs cap
    const shares = wholeShares();
    const px = pricingPx();
    const notional = (shares > 0 && finite(px)) ? shares * px : null;
    if (maxOrder != null && notional != null) {
      items.push({
        ok: notional <= maxOrder + 0.01,
        text: notional <= maxOrder + 0.01
          ? `Size ${money(notional)} within max order ${money(maxOrder)}`
          : `Size ${money(notional)} exceeds max order ${money(maxOrder)}`
      });
    } else if (maxOrder != null) {
      items.push({ ok: true, text: `Max order ${money(maxOrder)} · enter size to check` });
    }

    // Loss headroom
    if (dailyLoss != null && typeof dayPnl === 'number' && Number.isFinite(dayPnl)) {
      const used = Math.max(0, -dayPnl);
      const head = dailyLoss - used;
      items.push({
        ok: head > 0,
        text: head > 0
          ? `Loss headroom ~${money(head)} of ${money(dailyLoss)}`
          : `Daily loss cap reached (${money(dailyLoss)})`
      });
    }

    // Goal progress — advisory
    if (target != null && Number(target) > 0 && typeof dayPnl === 'number' && Number.isFinite(dayPnl)) {
      const hit = dayPnl >= Number(target);
      items.push({
        ok: !hit,
        text: hit
          ? `Soft goal ${money(Number(target))} already hit — new risk should stay paused`
          : `Goal progress ${money(dayPnl)} / ${money(Number(target))} · one good trade beats forcing size`
      });
    } else {
      items.push({ ok: true, text: 'No soft goal set · caps still apply' });
    }

    // Late / chasing is not known on bare ticket — remind
    items.push({
      ok: true,
      text: 'Prefer PASS + fresh quote; skip late/chasing entries (see idea board rank reasons)'
    });

    list.innerHTML = items.map(i =>
      `<li class="${i.ok ? 'is-ok' : 'is-block'}">${i.text}</li>`
    ).join('');
  }

  function refreshCaps() {
    const { maxOrder, dailyLoss, left, armed } = capsFromSnapshot();
    const used = Number(snapshot?.loop?.session_totals?.intents || snapshot?.daily?.trades || 0);
    const maxTrades = left != null && Number.isFinite(used) ? used + left : null;
    const dayPnl = snapshot?.broker_book?.day_pnl_usd;
    const lossUsedPct = (dailyLoss != null && typeof dayPnl === 'number' && Number.isFinite(dayPnl) && dailyLoss > 0)
      ? Math.max(0, Math.min(100, Math.round((Math.max(0, -dayPnl) / dailyLoss) * 100)))
      : 0;
    const tradeUsedPct = (maxTrades != null && maxTrades > 0)
      ? Math.max(0, Math.min(100, Math.round((Math.max(0, used) / maxTrades) * 100)))
      : 0;

    const maxEl = $('live-ticket-cap-max');
    const lossEl = $('live-ticket-cap-loss');
    const ordersEl = $('live-ticket-cap-orders');
    if (maxEl) maxEl.textContent = maxOrder != null ? money(maxOrder) : 'open';
    if (lossEl) lossEl.textContent = dailyLoss != null ? money(dailyLoss) : 'open';
    if (ordersEl) {
      ordersEl.textContent = left != null
        ? (maxTrades != null ? `${left} left (${used}/${maxTrades})` : String(left))
        : 'open';
    }

    const maxMeter = ensureCapMeter(maxEl);
    if (maxMeter) {
      // Max order is a hard cap on ticket size — show remaining headroom as full unless armed unknown.
      maxMeter.fill.style.width = maxOrder != null ? '100%' : '0%';
      maxMeter.tile.classList.toggle('is-warn', false);
      maxMeter.tile.classList.toggle('is-near', false);
    }
    const lossMeter = ensureCapMeter(lossEl);
    if (lossMeter) {
      lossMeter.fill.style.width = `${lossUsedPct}%`;
      const near = lossUsedPct >= 70;
      lossMeter.tile.classList.toggle('is-warn', near);
      lossMeter.tile.classList.toggle('is-near', near);
      lossMeter.tile.title = dailyLoss != null
        ? (typeof dayPnl === 'number' && Number.isFinite(dayPnl)
          ? `Day P&L vs loss cap · ${lossUsedPct}% of budget used`
          : 'Daily loss cap · day P&L unavailable')
        : 'Daily loss cap not set';
    }
    const ordMeter = ensureCapMeter(ordersEl);
    if (ordMeter) {
      ordMeter.fill.style.width = `${tradeUsedPct}%`;
      const near = tradeUsedPct >= 70;
      ordMeter.tile.classList.toggle('is-warn', near);
      ordMeter.tile.classList.toggle('is-near', near);
      ordMeter.tile.title = left != null
        ? `Orders remaining ${left}${armed ? '' : ''}`
        : 'Order cap not set';
    }
    try { refreshChecklist(); } catch (_) {}
  }

  function pricingPx() {
    if (selected('live-ticket-type') === 'limit') {
      const lim = Number($('live-ticket-limit').value);
      if (finite(lim) && lim > 0) return lim;
    }
    return quoteForSymbol($('live-ticket-symbol').value);
  }

  function wholeShares() {
    const raw = Number($('live-ticket-size').value);
    if (!finite(raw) || raw <= 0) return 0;
    if (unit === 'shares') return Math.floor(raw);
    const px = pricingPx();
    if (!finite(px) || px <= 0) return 0;
    return Math.floor(raw / px);
  }

  function syncSharesField() {
    const shares = wholeShares();
    $('live-ticket-shares').value = shares > 0 ? String(shares) : '';
    return shares;
  }

  function refreshEstimate() {
    const shares = syncSharesField();
    const px = pricingPx();
    const notional = shares > 0 && finite(px) ? shares * px : null;
    const intent = selected('live-ticket-intent');
    const { maxOrder } = capsFromSnapshot();

    $('live-ticket-est-shares').textContent = shares > 0 ? String(shares) : '—';
    $('live-ticket-est-notional').textContent = notional != null ? money(notional) : '—';

    const cash = snapshot?.broker_book?.cash;
    if (notional != null && finite(Number(cash))) {
      const next = intent === 'sell' ? Number(cash) + notional : Number(cash) - notional;
      $('live-ticket-est-cash').textContent = `${money(next)} (rough)`;
    } else if (finite(Number(cash))) {
      $('live-ticket-est-cash').textContent = `${money(Number(cash))} on book`;
    } else {
      $('live-ticket-est-cash').textContent = '—';
    }

    const warn = $('live-ticket-est-warn');
    let reason = '';
    if (unit === 'dollars' && !finite(px)) {
      reason = 'No price yet for a dollar→share conversion. Switch to Shares, or wait for a quote (Review also fetches price server-side when Shares are set).';
    } else if (shares < 1) {
      reason = unit === 'dollars'
        ? 'Size is below 1 whole share at this price. Raise dollars or switch to Shares.'
        : 'Enter at least 1 whole share.';
    } else if (maxOrder != null && notional != null && notional - maxOrder > 0.0001) {
      reason = `Over the ${money(maxOrder)} max-order cap from desk config. Trim size before review.`;
    }
    if (reason) {
      warn.hidden = false;
      warn.textContent = reason;
    } else {
      warn.hidden = true;
      warn.textContent = '';
    }

    const sym = $('live-ticket-symbol').value.trim().toUpperCase() || '—';
    const type = selected('live-ticket-type');
    const verb = intent === 'cover' ? 'COVER' : intent === 'sell' ? 'SELL' : 'BUY';
    const lim = Number($('live-ticket-limit').value);
    const limitBit = type === 'limit' ? (finite(lim) ? `limit ${money(lim)}` : 'limit') : 'market';
    const estBit = notional != null ? ` · est ${money(notional)}` : '';
    $('live-ticket-summary').textContent =
      shares > 0 ? `${verb} ${shares} ${sym} · DAY ${limitBit}${estBit}` : `${verb} · enter size to review`;

    const preview = $('live-ticket-preview');
    preview.classList.toggle('lt-buy-tone', intent !== 'sell');
    preview.classList.toggle('lt-sell-tone', intent === 'sell');
    preview.textContent =
      intent === 'cover' ? `Review COVER · ${sym}` :
      intent === 'sell' ? `Review SELL · ${sym}` :
      `Review BUY · ${sym}`;
  }

  function setIntent(intent) {
    const radio = form.querySelector(`input[name="live-ticket-intent"][value="${intent}"]`);
    if (radio) radio.checked = true;
    setPressed($('live-ticket-btn-buy'), intent === 'buy' || intent === 'cover');
    setPressed($('live-ticket-btn-sell'), intent === 'sell');
    setPressed($('live-ticket-btn-cover'), intent === 'cover');
  }

  function setOrderType(type) {
    const radio = form.querySelector(`input[name="live-ticket-type"][value="${type}"]`);
    if (radio) radio.checked = true;
    setPressed($('live-ticket-btn-limit'), type === 'limit');
    setPressed($('live-ticket-btn-market'), type === 'market');
    $('live-ticket-limit-box').hidden = type !== 'limit';
    $('live-ticket-limit').disabled = type !== 'limit';
    $('live-ticket-limit').required = type === 'limit';
  }

  function setUnit(next) {
    unit = next;
    setPressed($('live-ticket-unit-dollars'), unit === 'dollars');
    setPressed($('live-ticket-unit-shares'), unit === 'shares');
    $('live-ticket-size-prefix').textContent = unit === 'dollars' ? '$' : '#';
  }

  function setInstrument(next) {
    instrument = next;
    const stockBtn = $('live-ticket-mode-stock');
    const optBtn = $('live-ticket-mode-option');
    setPressed(stockBtn, instrument === 'stock');
    setPressed(optBtn, instrument === 'option');
    stockBtn.setAttribute('aria-selected', instrument === 'stock' ? 'true' : 'false');
    optBtn.setAttribute('aria-selected', instrument === 'option' ? 'true' : 'false');
    $('live-ticket-stock-panel').hidden = instrument !== 'stock';
    $('live-ticket-option-panel').hidden = instrument !== 'option';
    if (instrument === 'option') {
      clearReview();
      message('Live options BTO/STC/STO/BTC. Naked STO needs Allow naked; covered needs long shares.');
    }
  }

  function modeOk() {
    const fresh = snapshot && Date.now() - received < 25000;
    return !!(fresh && snapshot.config?.mode === 'live_manual' && snapshot.broker?.broker === 'ibkr');
  }

  function statusText() {
    if (instrument === 'option') {
      return 'Live options (BTO/STC/STO/BTC). Confirm the OCC symbol on review before anything sends.';
    }
    const fresh = snapshot && Date.now() - received < 25000;
    if (!fresh) return 'Waiting for a fresh desk status. Composing a ticket sends no order.';
    if (!(snapshot.config?.mode === 'live_manual' && snapshot.broker?.broker === 'ibkr')) {
      return 'Direct tickets require IBKR and Approve each — live broker mode. This form does not change your mode.';
    }
    const shares = syncSharesField();
    if (unit === 'dollars' && !finite(pricingPx())) {
      return 'Dollar size needs a price to convert to whole shares. Switch to Shares, or wait for a quote.';
    }
    if (shares < 1) return 'Enter a size that yields at least 1 whole share before review.';
    if (selected('live-ticket-type') === 'limit') {
      const lim = Number($('live-ticket-limit').value);
      if (!finite(lim) || lim <= 0) return 'Enter a limit price, or switch to Market.';
    }
    if (!$('live-ticket-symbol').value.trim()) return 'Enter a stock symbol.';
    if (snapshot.broker_book?.paper_mode === false) {
      return 'Real-money account. Review prepares the ticket; only your final Submit sends it.';
    }
    return 'Broker paper or unverified account. The review verifies the exact account before submission.';
  }

  function update() {
    const limit = selected('live-ticket-type') === 'limit';
    $('live-ticket-limit').disabled = !limit;
    $('live-ticket-limit').required = limit;
    $('live-ticket-price-help').textContent = limit
      ? 'DAY limit: buy at this price or less; sell at this price or more. Unfilled orders can remain working until the session ends.'
      : 'Market: the execution price can change. There is no maximum purchase price or minimum sale price.';

    refreshLastDisplay();
    refreshCaps();
    refreshEstimate();
    try { refreshChecklist(); } catch (_) {}

    const mode = modeOk();
    const shares = syncSharesField();
    const canReview =
      instrument === 'stock' &&
      mode &&
      !previewing &&
      !submitting &&
      shares >= 1 &&
      !!$('live-ticket-symbol').value.trim() &&
      (selected('live-ticket-type') !== 'limit' ||
        (finite(Number($('live-ticket-limit').value)) && Number($('live-ticket-limit').value) > 0));

    // When dollars mode has no price yet, disable review (never send fractional / unknown shares)
    const dollarsBlocked = unit === 'dollars' && !finite(pricingPx());
    $('live-ticket-preview').disabled = !canReview || dollarsBlocked;
    $('live-ticket-status').textContent = statusText();

    const identity = snapshot?.config?.broker_identity;
    const sameAccount =
      review &&
      identity?.account_id === review.identity.account_id &&
      identity?.paper_mode === review.identity.paper_mode;
    const valid = review && Date.parse(review.expires_at) > Date.now() && mode && sameAccount;
    $('live-ticket-send').disabled =
      !valid || submitting || $('live-ticket-ack').value.trim().toUpperCase() !== review?.ticker;
    if (review && !valid) {
      clearReview();
      message('Review expired, status became stale, or the account/mode changed. Request a fresh review.');
    }
  }

  async function post(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal
      });
      const data = await response.json();
      if ((!response.ok || !data.ok) && !data.pending) throw Error(data.error || 'Request could not complete');
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  function show(data) {
    if (!data.signal_id || !data.review_token || !data.identity || !data.order || !data.quote) {
      throw Error('Incomplete order review; request a fresh review');
    }
    review = data;
    if (finite(Number(data.quote.price))) {
      lastPrice = Number(data.quote.price);
      lastPriceMeta = data.quote.source || 'review quote';
    }
    $('live-ticket-review').hidden = false;
    $('live-ticket-account').textContent =
      `${data.identity.paper_mode ? 'BROKER PAPER' : 'REAL MONEY'} · account …${String(data.identity.account_id).slice(-4)}`;
    $('live-ticket-terms').textContent =
      `${data.intent === 'cover' ? 'Buy to cover' : data.side === 'sell' ? 'Sell held' : 'Buy'} ${data.order.shares} ${data.ticker} · ${
        data.order.type === 'limit' ? `DAY limit ${money(data.order.limit)}` : 'DAY market'
      } · ${data.costs?.contract?.primary_exchange || 'exchange unverified'}`;
    $('live-ticket-position').textContent =
      `Held: ${data.held_shares} shares · available to close: ${data.available_whole_shares} whole shares after working orders`;
    $('live-ticket-quote').textContent =
      `${money(data.quote.price)} · ${data.quote.source} · ${new Date(data.quote.market_time).toLocaleString()}`;
    $('live-ticket-notional').textContent =
      `${money(data.estimated_notional)} ${
        data.order.type === 'limit'
          ? (data.side === 'sell' ? 'minimum sale value before fees if filled' : 'maximum purchase value before fees')
          : 'at the quoted price; actual value can differ'
      }`;
    const cost = data.costs || {};
    $('live-ticket-fees').textContent =
      cost.currency === 'USD'
        ? `${money(cost.commission_estimate)} · ${cost.source || 'broker estimate'}`
        : 'Unknown in USD';
    $('live-ticket-cash').textContent =
      data.estimated_cash_change == null
        ? 'Unknown until fees are available'
        : `${money(data.estimated_cash_change)} · estimate only`;
    $('live-ticket-expiry').textContent = new Date(data.expires_at).toLocaleTimeString();
    $('live-ticket-warning').textContent =
      cost.warning || cost.error || 'Submission is not a fill. Track remaining quantity before sending another order.';
    $('live-ticket-ack').value = '';
    update();
    if (!review) return;
    $('live-ticket-review-title').focus();
    message('Review ready. Verify the account, order and fees, then type the exact stock symbol.');
  }

  // Mode toggle
  $('live-ticket-mode-stock').addEventListener('click', () => {
    setInstrument('stock');
    update();
  });
  $('live-ticket-mode-option').addEventListener('click', () => {
    setInstrument('option');
    update();
  });

  // Side
  $('live-ticket-btn-buy').addEventListener('click', () => {
    setIntent('buy');
    clearReview();
    update();
    message('Ticket changed. Review the new terms before submitting.');
  });
  $('live-ticket-btn-sell').addEventListener('click', () => {
    setIntent('sell');
    clearReview();
    update();
    message('Ticket changed. Review the new terms before submitting.');
  });
  $('live-ticket-btn-cover').addEventListener('click', () => {
    setIntent('cover');
    clearReview();
    update();
    message('Cover selected. Review buys shares to close a short.');
  });

  // Unit
  $('live-ticket-unit-dollars').addEventListener('click', () => {
    setUnit('dollars');
    if (!$('live-ticket-size').value) $('live-ticket-size').value = '10';
    clearReview();
    update();
  });
  $('live-ticket-unit-shares').addEventListener('click', () => {
    const shares = wholeShares() || 1;
    setUnit('shares');
    $('live-ticket-size').value = String(shares);
    clearReview();
    update();
  });

  // Dollar presets
  form.querySelectorAll('[data-lt-preset]').forEach(btn => {
    btn.addEventListener('click', () => {
      setUnit('dollars');
      const { maxOrder } = capsFromSnapshot();
      const key = btn.getAttribute('data-lt-preset');
      $('live-ticket-size').value =
        key === 'max' ? String(maxOrder != null ? maxOrder : '') : key;
      clearReview();
      update();
    });
  });

  // Order type
  $('live-ticket-btn-limit').addEventListener('click', () => {
    setOrderType('limit');
    clearReview();
    update();
  });
  $('live-ticket-btn-market').addEventListener('click', () => {
    setOrderType('market');
    clearReview();
    update();
  });

  
  form.addEventListener('input', () => {
    clearReview();
    update();
    message('Ticket changed. Review the new terms before submitting.');
  });
  form.addEventListener('change', () => {
    clearReview();
    update();
  });


  // Live option long single-leg (BTO/STC)
  let optIntent = 'BTO';
  let optRight = 'C';
  let optType = 'limit';

  function optNotionalPreview() {
    const contracts = Number($('live-ticket-opt-contracts')?.value || 0);
    const premium = optType === 'limit' ? Number($('live-ticket-opt-limit')?.value || 0) : null;
    const el = $('live-ticket-opt-est-notional');
    if (!el) return;
    if (!(contracts >= 1) || !(premium > 0)) { el.textContent = '—'; return; }
    el.textContent = money(contracts * premium * 100);
  }

  function optSummary() {
    const sym = ($('live-ticket-opt-symbol')?.value || '').trim().toUpperCase();
    const right = optRight === 'C' ? 'Call' : 'Put';
    const strike = $('live-ticket-opt-strike')?.value || '—';
    const expiry = $('live-ticket-opt-expiry')?.value || '—';
    const contracts = $('live-ticket-opt-contracts')?.value || '1';
    $('live-ticket-opt-summary').textContent =
      `${optIntent} ${contracts} ${sym || '—'} ${strike}${right[0]} ${expiry} · ${optType.toUpperCase()} · ×100 notional`;
    optNotionalPreview();
  }

  $('live-ticket-opt-call')?.addEventListener('click', () => {
    optRight = 'C';
    setPressed($('live-ticket-opt-call'), true);
    setPressed($('live-ticket-opt-put'), false);
    optSummary();
  });
  $('live-ticket-opt-put')?.addEventListener('click', () => {
    optRight = 'P';
    setPressed($('live-ticket-opt-put'), true);
    setPressed($('live-ticket-opt-call'), false);
    optSummary();
  });
  document.querySelectorAll('#live-ticket-option-panel [data-lt-opt]').forEach(btn => {
    btn.addEventListener('click', () => {
      const act = btn.getAttribute('data-lt-opt');
      document.querySelectorAll('#live-ticket-option-panel [data-lt-opt]').forEach(b => setPressed(b, false));
      setPressed(btn, true);
      optIntent = act;
      if (act === 'STO') message('STO selected — check Covered (long shares) or Allow naked short before review.');
      else if (act === 'BTC') message('BTC selected — requires an existing short option holding.');
      optSummary();
    });
  });
  $('live-ticket-opt-btn-limit')?.addEventListener('click', () => {
    optType = 'limit';
    setPressed($('live-ticket-opt-btn-limit'), true);
    setPressed($('live-ticket-opt-btn-market'), false);
    $('live-ticket-opt-limit-box').hidden = false;
    $('live-ticket-opt-limit').required = true;
    optSummary();
  });
  $('live-ticket-opt-btn-market')?.addEventListener('click', () => {
    optType = 'market';
    setPressed($('live-ticket-opt-btn-market'), true);
    setPressed($('live-ticket-opt-btn-limit'), false);
    $('live-ticket-opt-limit-box').hidden = true;
    $('live-ticket-opt-limit').required = false;
    optSummary();
  });
  ['live-ticket-opt-symbol','live-ticket-opt-contracts','live-ticket-opt-expiry','live-ticket-opt-strike','live-ticket-opt-limit']
    .forEach(id => $(id)?.addEventListener('input', optSummary));

  $('live-ticket-option-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    if (previewing || submitting) return;
    if (!modeOk()) {
      message('Direct tickets require IBKR and Approve each — live broker mode.');
      return;
    }
    if (optIntent === 'STO') {
      const covered = !!$('live-ticket-opt-covered')?.checked;
      const naked = !!$('live-ticket-opt-naked')?.checked;
      if (!covered && !naked) {
        message('STO needs Covered (long shares) or Allow naked short.');
        return;
      }
    }
    clearReview();
    const mine = generation;
    previewing = true;
    update();
    message('Qualifying option contract and checking risk — no order sent yet…');
    const body = {
      ticker: $('live-ticket-opt-symbol').value.trim().toUpperCase(),
      intent: optIntent,
          covered: !!$('live-ticket-opt-covered')?.checked,
          allow_naked: !!$('live-ticket-opt-naked')?.checked,
      contracts: Number($('live-ticket-opt-contracts').value),
      right: optRight,
      expiry: $('live-ticket-opt-expiry').value,
      strike: Number($('live-ticket-opt-strike').value),
      order: { type: optType }
    };
    if (optType === 'limit') body.order.limit_price = Number($('live-ticket-opt-limit').value);
    try {
      const data = await post('/api/live/ticket/option/review', body);
      if (mine !== generation) return;
      // Reuse stock review UI; ack must be OCC/local symbol.
      show({
        ...data,
        ticker: data.ack_symbol || data.local_symbol || data.ticker,
        intent: (data.intent === 'STC' || data.intent === 'STO') ? 'sell' : 'buy',
        held_shares: data.held_contracts,
        available_whole_shares: data.held_contracts,
        order: {
          ...(data.order || {}),
          shares: data.order?.contracts || data.order?.shares,
          type: data.order?.type,
          limit: data.order?.limit
        },
        estimated_notional: data.estimated_notional,
        estimated_cash_change: data.estimated_cash_change
      });
      $('live-ticket-ack-label') && ($('live-ticket-ack-label').textContent = 'Type the OCC / local option symbol to confirm');
      message(`Review ready for ${data.local_symbol || data.ack_symbol}. Notional ${money(data.estimated_notional)}. Type that OCC symbol to submit.`);
    } catch (error) {
      if (mine === generation) message(error.message + ' No order was sent.');
    } finally {
      previewing = false;
      update();
    }
  });


  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (instrument !== 'stock') {
      message('Live options BTO/STC/STO/BTC. Naked STO needs Allow naked; covered needs long shares.');
      return;
    }
    if (previewing || submitting || $('live-ticket-preview').disabled) return;
    const shares = syncSharesField();
    if (shares < 1) {
      message('Need at least 1 whole share before review. No order was sent.');
      return;
    }
    clearReview();
    const mine = generation;
    previewing = true;
    update();
    message('Checking price, holdings, risk and estimated fees…');
    const body = {
      ticker: $('live-ticket-symbol').value.trim().toUpperCase(),
      intent: selected('live-ticket-intent'),
      shares: String(shares),
      order: { type: selected('live-ticket-type') }
    };
    if (body.order.type === 'limit') body.order.limit_price = $('live-ticket-limit').value;
    try {
      const data = await post('/api/live/ticket/review', body);
      if (mine === generation) show(data);
    } catch (error) {
      if (mine === generation) message(error.message + ' No order was sent.');
    } finally {
      previewing = false;
      update();
    }
  });

  $('live-ticket-ack').addEventListener('input', update);

  $('live-ticket-discard').addEventListener('click', async () => {
    const ticket = review;
    clearReview();
    const mine = generation;
    $('live-ticket-preview').focus();
    if (!ticket) return;
    try {
      await post(`/api/signals/${encodeURIComponent(ticket.signal_id)}/reject`, {});
      if (mine === generation) message('Ticket discarded. No order was sent.');
    } catch (error) {
      if (mine === generation) {
        message('Review closed locally. The saved ticket will expire; refresh the queue to check its status.');
      }
    }
  });

  $('live-ticket-send').addEventListener('click', async () => {
    update();
    if (!review || submitting || $('live-ticket-send').disabled) return;
    const ticket = review;
    const ack = $('live-ticket-ack').value.trim().toUpperCase();
    clearReview();
    const mine = generation;
    submitting = true;
    update();
    message('Submitting once. Waiting for broker evidence…');
    try {
      const result = await post(`/api/signals/${encodeURIComponent(ticket.signal_id)}/approve`, {
        review_token: ticket.review_token,
        ack_ticker: ack
      });
      if (mine === generation) {
        message(
          result.pending
            ? 'Order is still working or awaiting reconciliation. Follow it in Broker orders before trying again.'
            : result.fill
              ? `Broker reports ${result.fill.shares} shares at ${money(result.fill.price)}. Check the order record for final status.`
              : 'Request processed. Check Broker orders for the confirmed outcome.'
        );
      }
    } catch (error) {
      if (mine === generation) {
        message(
          `${error.message}. Check Broker orders and your broker before trying again; this ticket will not retry automatically.`
        );
      }
    } finally {
      submitting = false;
      update();
      window.dispatchEvent(new Event('desk:refresh'));
    }
  });

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-live-position]');
    if (!button || submitting) return;
    setInstrument('stock');
    clearReview();
    $('live-ticket-symbol').value = button.dataset.livePosition;
    const shares = Math.floor(Number(button.dataset.shares));
    setUnit('shares');
    $('live-ticket-size').value = String(shares > 0 ? shares : 1);
    $('live-ticket-shares').value = String(shares > 0 ? shares : 1);
    setIntent(button.dataset.side === 'short' ? 'cover' : 'sell');
    setOrderType('limit');
    $('live-ticket-limit').value = '';
    update();
    message('Holding copied. Enter a limit price, then request a fresh position and fee review.');
    $('live-stock-ticket').scrollIntoView({ block: 'start' });
    $('live-ticket-symbol').focus();
  });

  window.addEventListener('desk:state', event => {
    snapshot = event.detail;
    received = Date.now();
    update();
  });
  document.addEventListener('visibilitychange', update);
  let timer = setInterval(update, 1000);
  window.addEventListener('pagehide', () => {
    clearInterval(timer);
    clearReview();
  });
  window.addEventListener('pageshow', () => {
    clearInterval(timer);
    timer = setInterval(update, 1000);
    update();
  });

  setInstrument('stock');
  setIntent('buy');
  setUnit('dollars');
  setOrderType('limit');
  update();
})();
