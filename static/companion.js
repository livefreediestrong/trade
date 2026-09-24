/* Moss and planning tools. No broker submit/cancel endpoint is used here. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = n => n == null ? 'Unknown' : new Intl.NumberFormat('en-US', {style:'currency',currency:'USD',maximumFractionDigits:4}).format(n);
  const percent = n => typeof n==='number'&&Number.isFinite(n)?(n*100).toFixed(1)+'%':'Unknown';
  const txt = (id, value) => { if($(id)) $(id).textContent = value; };
  const val = id => $(id).value;
  const checked = id => $(id).checked;
  const instrument = r => r.asset_type==='OPT' ? `${r.local_symbol||r.ticker} · ${r.expiry||''} ${r.right||''} ${r.strike??''} · multiplier ${r.multiplier||'unknown'}` : r.ticker;
  let snapshot, initialized = false, paperInitialized = false, workdayInitialized = false, pollTimer, fetching = false, pageActive = true, busyBefore = false, costRevision = 0;
  async function api(url, body) {
    const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), 25000);
    try {
      const response = await fetch(url, {method:body === undefined ? 'GET':'POST', signal:ctrl.signal,
        headers:body === undefined ? {} : {'Content-Type':'application/json'}, body:body === undefined ? undefined:JSON.stringify(body)});
      const result = await response.json();
      if(!response.ok || result.ok === false) throw Error(result.error || 'The request could not be completed.');
      return result;
    } finally { clearTimeout(timer); }
  }
  async function task(button, target, work) {
    if(button) button.disabled = true;
    try { await work(); } catch(e) { txt(target,e.name === 'AbortError' ? 'This took too long. No automatic retry was sent; refresh the status before trying again.' : e.message); }
    finally { if(button) button.disabled = false; }
  }
  function safeURL(raw) { try { const u=new URL(raw); return ['https:','http:'].includes(u.protocol) ? u.href : ''; } catch (_) { return ''; } }
  function showEntry() {
    const brief = snapshot?.briefs.find(b=>b.id===val('moss-history'));
    if(!brief) return;
    let obs = brief.observations.map(o=>`<article class="notebook-observation"><h3>${esc(o.symbol)}</h3><p>${esc(o.note)}</p>${o.available ? `<p class="muted">${money(o.close)} close · ${esc(o.source)} · ${esc(o.as_of)} · ${esc(o.sample_days)} daily bars<br>20-session change: ${o.twenty_day_pct == null ? 'Unknown' : esc(o.twenty_day_pct)+'%'} · Typical daily move: ${esc(o.mean_abs_daily_move_pct)}%</p>`:''}</article>`).join('');
    obs += (brief.global_observations||[]).map(o=>`<article class="notebook-observation"><h3>${esc(o.instrument?.symbol||o.instrument_id)}</h3><p>${esc(o.error||o.quality)}</p><p>${esc(o.instrument?.quote_unit||'')} · latest completed ${esc(o.latest_completed?.date||'unavailable')} · close ${esc(o.latest_completed?.close??'unavailable')}</p>${o.evidence_id?`<button class="btn ghost sm" data-evidence="${esc(o.evidence_id)}">Inspect source evidence</button>`:''}</article>`).join('');
    const model=brief.model;
    const news=(brief.headlines||[]).map(n=>{const url=safeURL(n.url || n.link);return url ? `<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(n.title||n.headline)}</a> <span class="muted">${esc(n.source||'Source')}</span></li>`:'';}).join('');
    const account=brief.account_context||{};
    $('moss-notebook-entry').innerHTML=`<p class="muted">Saved ${esc(new Date(brief.created_at).toLocaleString())} · research goal ${money(brief.target)} · ${brief.scheduled ? 'Daily routine' : 'Requested review'}</p><p>${esc(brief.summary)}</p><div class="notebook-grid">${obs}</div>${model ? `<h3>Configured AI review · ${esc(model.llm_model||model.brain_mode||'unavailable')}</h3><p>${esc(model.error||model.thesis||'No model narrative returned.')}</p>`:'<p class="muted">No new AI call: market closed, model disabled, or no fresh intraday data.</p>'}<p>${esc(brief.next_step)}</p>${brief.short_note?`<p class="hint-line">${esc(brief.short_note)}</p>`:''}<section class="desk-module"><h4 class="module-title">Account context at the time</h4><p>Live equity ${money(account.equity)} · daily profit/loss ${money(account.daily_pnl)} · paper equity ${money(account.paper_equity)}. Account data ${account.risk_ready?'available':'not ready'}.</p></section>${news?`<section class="desk-module"><h4 class="module-title">Headlines considered</h4><ul>${news}</ul></section>`:''}`;
  }
  function render(s, startedAt) {
    snapshot=s;
    window.dispatchEvent(new CustomEvent('moss:review',{detail:s}));
    window.dispatchEvent(new CustomEvent('moss:state',{detail:{busy:!!(s.busy||s.paper_workday?.busy),error:!!s.error,marketOpen:!!s.market_open}}));
    const w=s.paper_workday, j=s.actual_trades;
    if(w){
      window.dispatchEvent(new CustomEvent('moss:workday',{detail:{workday:w,startedAt}}));
      txt('moss-workday-status',w.error || `${w.active?'Enabled':'Paused'} · ${w.busy?'researching':w.phase.replaceAll('_',' ')} · ${w.settings.personality==='empirical_bayes'?'Empirical Bayes':'Stoic evidence collector'}`);
      txt('moss-workday-progress',`${w.today.cycles||0} cycles · ${w.today.candidates_checked||0} US checks / ${(w.today.symbols_checked||[]).length} unique symbols · ${w.today.model_calls||0}/${w.settings.max_model_calls} AI attempts · ${w.today.qualified_candidates||0} eligible candidates · ${w.today.paper_fills||0} paper fills. ${w.today.last_result||''}`);
      txt('moss-paper-data-limits',`Maximum quote age and execution delay: ${w.settings.max_quote_age_sec}s / ${w.settings.max_order_latency_sec}s. Minimum daily dollar volume: ${money(w.settings.min_daily_dollar_volume)}; completed-bar volatility: ${w.settings.min_realized_volatility_pct}%. Existing paper preset limits also apply. Reducing exits may exceed the daily fill cap.`);
      const ev=w.evaluation;
      const u=w.universe;
      if(u)txt('moss-universe-status',`${u.count.toLocaleString()} US stock/ETF directory entries cached · ${u.updated_at?new Date(u.updated_at).toLocaleString():'not downloaded yet'}. ${u.error||u.cadence}`);
      $('moss-workday-evaluation').innerHTML=ev?`<p>${ev.qualified_samples} qualified outcomes · ${esc(ev.note)}</p><p>Excluded: ${esc(Object.entries(ev.excluded).map(([k,v])=>`${k.replaceAll('_',' ')} ${v}`).join(' · ')||'none')}</p><div class="table-wrap"><table><thead><tr><th>Stock / setup</th><th>Samples</th><th>Sample weight</th><th>Win rate · 95% range</th><th>Net expectancy</th><th>Scaling</th></tr></thead><tbody>${ev.weights.map(r=>`<tr><td>${esc(r.ticker)} · ${esc(r.setup)} · ${esc(r.side)}</td><td>${r.samples}</td><td>${percent(r.alpha_fraction)}</td><td>${percent(r.win_rate)}<br>${percent(r.win_rate_lower_95)}–${percent(r.win_rate_upper_95)}</td><td>${Number(r.expectancy_bps).toFixed(2)} bps</td><td>${r.stable_for_paper_scaling?'Within paper cap':'Base amount only'}</td></tr>`).join('')}</tbody></table></div>`:'Collecting evidence. No qualified outcome is invented.';
      if(!workdayInitialized){
        const p=w.settings;
        $('moss-paper-enabled').checked=p.enabled;$('moss-personality').value=p.personality;$('moss-paper-symbols').value=p.symbols.join(', ');
        if($('moss-universe'))$('moss-universe').value=p.universe||'focus';
        for(const [id,key] of Object.entries({'moss-paper-batch':'candidates_per_cycle','moss-paper-interval':'interval_sec','moss-paper-horizon':'horizon_min','moss-paper-base':'base_order_usd','moss-paper-max':'max_order_usd','moss-paper-calls':'max_model_calls','moss-paper-cost':'model_budget_usd'}))$(id).value=p[key];
        $('moss-paper-close').checked=p.flatten_before_close;workdayInitialized=true;
        for(const [id,key] of Object.entries({'moss-paper-positions':'max_positions','moss-paper-exposure':'max_total_exposure_pct','moss-paper-trades':'max_trades_per_day','moss-paper-loss':'max_daily_loss_pct'}))$(id).value=p[key];
      }
    }
    if(j){
      window.dispatchEvent(new CustomEvent('moss:journal',{detail:j}));
      txt('moss-trades-insights',(j.real.insights||[]).join(' '));
      txt('moss-trades-status',j.error || `Last broker sync: ${j.last_sync?new Date(j.last_sync).toLocaleString():'not yet'} · ${j.real.executions} retained real executions · account ending ${j.account_suffix||'unknown'}`);
      $('moss-trades-summary').innerHTML=Object.entries(j.real.by_currency).map(([currency,r])=>`<p>${esc(currency)} · broker-reported realized P&L ${Number(r.broker_realized_pnl).toFixed(2)} (${r.pnl_missing} missing) · reported fees ${Number(r.fees_reported).toFixed(2)} (${r.fees_missing} missing)</p>`).join('')+`<p class="hint-line">${esc(j.real.note)}</p>`;
      $('moss-trades-rows').innerHTML=j.executions.length?`<table><thead><tr><th>Time / instrument</th><th>Side / quantity</th><th>Price</th><th>Fee</th><th>Broker P&L</th><th>Record</th></tr></thead><tbody>${j.executions.map(r=>`<tr><td>${esc(new Date(r.ts).toLocaleString())}<br>${esc(instrument(r))}</td><td>${esc(r.side)} · ${esc(r.shares)} ${r.asset_type==='OPT'?'contracts':'shares'}</td><td>${esc(r.price)} ${esc(r.currency)}</td><td>${r.commission==null?'Unknown':esc(r.commission)+' '+esc(r.commission_currency)}</td><td>${r.broker_realized_pnl==null?'Unknown':esc(r.broker_realized_pnl)}</td><td>${r.superseded_by?'Corrected':esc(r.source)}<br>${r.reference_slippage_bps==null?'Slippage unavailable':esc(r.reference_slippage_bps)+' bps vs recorded quote'}</td></tr>`).join('')}</tbody></table>`:'<p>No real fills have been exposed to this journal yet.</p>';
    }
    txt('moss-title', `${s.settings.name} · your research companion`);
    txt('moss-mode-note',`Desk execution mode: ${s.desk_execution_mode}. Moss test plan: not armed.`);
    txt('moss-status',w?.active ? `Paper workday · ${w.busy?'researching':w.phase.replaceAll('_',' ')}` : s.busy ? 'Writing research notebook' : !s.settings.enabled ? 'Daily notebook paused' : s.market_open ? 'Market open · observing' : 'Market closed · resting');
    const last=s.briefs[0];
    txt('moss-insight', s.error || last?.summary || 'We can control our preparation and our risk. There is no need to force a trade to meet a target.');
    txt('moss-next',last?.next_step || 'Start with the cost calculator, or ask me to review completed market sessions.');
    txt('moss-greeting',`${s.notebook_count} notebook entries · ${s.memory.reviewed_outcomes} reviewed outcomes`);
    txt('moss-learning',s.memory.note+' '+s.learning);
    txt('moss-memory-count',`${s.memory.observations} observations · ${s.memory.reviewed_outcomes} scored`);
    txt('moss-schedule',s.schedule + (s.next_session ? ` Next session opportunity: ${new Date(s.next_session).toLocaleString()}.` : ''));
    $('moss-research').disabled=s.busy;
    const selected=val('moss-history');
    $('moss-history').innerHTML=s.briefs.length ? s.briefs.map(b=>`<option value="${esc(b.id)}">${esc(new Date(b.created_at).toLocaleString())} · ${b.scheduled?'daily':'requested'}</option>`).join('') : '<option value="">No entries yet</option>';
    if(s.briefs.some(b=>b.id===selected)) $('moss-history').value=selected;
    showEntry();
    const trained=s.learned_model;
    $('moss-weights').innerHTML=trained?.weights?.length ? `<p>${trained.qualified_samples} qualified outcomes · version ${esc(trained.version)}</p><div class="table-wrap"><table><thead><tr><th>Stock / model / setup</th><th>Horizon</th><th>Samples</th><th>Weight</th><th>Mean net move</th></tr></thead><tbody>${trained.weights.map(w=>`<tr><td>${esc(w.ticker)} / ${esc(w.model)} / ${esc(w.setup)} / ${esc(w.side)}<br>${esc(w.workspace)}</td><td>${esc(w.horizon_min)} min</td><td>${w.samples}</td><td>${w.weight}${w.active_for_ranking?'':' · neutral until 20 samples'}</td><td>${esc(w.mean_net_bps)} basis points</td></tr>`).join('')}</tbody></table></div>`:'No qualified outcomes yet. No strategy weights have been learned; no skill level is invented.';
    if(!initialized) {
      $('moss-name').value=s.settings.name; $('moss-goal').value=s.settings.daily_target;
      $('moss-enabled').checked=s.settings.enabled; $('moss-model').checked=s.settings.use_model; $('moss-shorts').checked=s.settings.research_short_selling;
      $('auto-budget').value=s.plan.budget; $('auto-loss').value=s.plan.daily_loss_limit; $('auto-count').value=s.plan.max_orders;
      $('auto-symbols').value=s.plan.symbols.join(', '); $('auto-type').value=s.plan.order_type;
      $('auto-fractional').checked=s.plan.fractional; $('auto-shorts').checked=s.plan.short_selling;
      initialized=true;
    }
    busyBefore=s.busy || w?.busy;
  }
  async function refresh() {
    if(!pageActive || document.hidden || fetching) return;
    fetching=true;const startedAt=Date.now();
    try { render(await api('/api/companion'),startedAt); } catch(e) { txt('moss-status','Notebook unavailable'); txt('moss-insight',e.message); window.dispatchEvent(new CustomEvent('moss:state',{detail:{error:true}}));window.dispatchEvent(new CustomEvent('moss:workday',{detail:{error:true,startedAt}})); }
    finally { fetching=false; clearTimeout(pollTimer); if(pageActive&&!document.hidden) pollTimer=setTimeout(refresh,busyBefore?5000:15000); }
  }
  $('moss-research').addEventListener('click',e=>task(e.currentTarget,'moss-insight',async()=>{await api('/api/companion/research',{}); await refresh();}));
  $('moss-history').addEventListener('change',showEntry);
  $('moss-paper-form').addEventListener('submit',e=>{e.preventDefault();task(e.submitter,'moss-paper-save',async()=>{
    const p={...snapshot.paper_workday.settings,enabled:checked('moss-paper-enabled'),personality:val('moss-personality'),symbols:val('moss-paper-symbols').toUpperCase().split(/[\s,]+/).filter(Boolean),interval_sec:val('moss-paper-interval'),horizon_min:val('moss-paper-horizon'),base_order_usd:val('moss-paper-base'),max_order_usd:val('moss-paper-max'),max_model_calls:val('moss-paper-calls'),model_budget_usd:val('moss-paper-cost'),flatten_before_close:checked('moss-paper-close')};
    if($('moss-universe'))p.universe=val('moss-universe');
    if($('moss-paper-batch'))p.candidates_per_cycle=val('moss-paper-batch');
    for(const [id,key] of Object.entries({'moss-paper-positions':'max_positions','moss-paper-exposure':'max_total_exposure_pct','moss-paper-trades':'max_trades_per_day','moss-paper-loss':'max_daily_loss_pct'}))p[key]=val(id);
    await api('/api/companion/paper',p);txt('moss-paper-save','Paper workday saved. Live execution mode was not changed.');await refresh();window.dispatchEvent(new Event('desk:refresh'));
  });});
  $('moss-sync-trades').addEventListener('click',e=>task(e.currentTarget,'moss-trades-status',async()=>{await api('/api/companion/trades/sync',{});await refresh();}));
  $('moss-universe-refresh')?.addEventListener('click',e=>task(e.currentTarget,'moss-universe-status',async()=>{await api('/api/companion/universe/refresh',{});await refresh();}));
  $('moss-settings-form').addEventListener('submit',e=>{e.preventDefault();task(e.submitter,'moss-settings-status',async()=>{await api('/api/companion/settings',{name:val('moss-name').trim(),daily_target:val('moss-goal'),enabled:checked('moss-enabled'),use_model:checked('moss-model'),research_short_selling:checked('moss-shorts')});txt('moss-settings-status','Routine saved. Live trading settings did not change.');await refresh();});});
  $('automation-form').addEventListener('submit',e=>{e.preventDefault();task(e.submitter,'auto-result',async()=>{const result=await api('/api/companion/plan',{budget:val('auto-budget'),daily_loss_limit:val('auto-loss'),max_orders:val('auto-count'),symbols:val('auto-symbols').toUpperCase().split(/[\s,]+/).filter(Boolean),order_type:val('auto-type'),fractional:checked('auto-fractional'),short_selling:checked('auto-shorts')});txt('auto-result',result.note);});});
  $('auto-rehearse').addEventListener('click',e=>task(e.currentTarget,'auto-result',async()=>{const r=await api('/api/companion/rehearse',{});$('auto-result').innerHTML=`<p><strong>No orders submitted.</strong> ${r.candidates.length} pending ideas matched the saved symbol list.</p><ul>${r.blockers.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>${r.candidates.map(c=>`<p>${esc(c.ticker)}: ${esc(c.reason)}</p>`).join('')}`;}));
  $('cost-form').addEventListener('submit',e=>{e.preventDefault();task(e.submitter,'cost-result',async()=>{
    const revision=costRevision;
    const r=await api('/api/cost-estimate',{budget:val('cost-budget'),price:val('cost-price'),exit_price:val('cost-exit'),fractional:val('cost-fractional')==='true',direction:val('cost-direction'),order_type:val('cost-type'),fee_mode:val('cost-fee-mode'),entry_fee:val('cost-entry-fee'),exit_fee:val('cost-exit-fee'),borrow_fee:val('cost-borrow-fee'),slippage_bps:Number(val('cost-slippage'))*100});
    if(revision!==costRevision) return;
    $('cost-result').innerHTML=`<div class="estimate-grid"><div><span>Shares in this scenario</span><strong>${esc(r.shares)}</strong></div><div><span>Entry value · before fees</span><strong>${money(r.entry_value)}</strong></div><div><span>Estimated entry cash</span><strong>${r.direction==='short'?'Margin unverified':money(r.cash_needed_estimate)}</strong></div><div><span>Fees assumed · both sides</span><strong>${money(r.fees_assumed)}</strong></div><div><span>Estimated net result</span><strong>${money(r.net_pnl_estimate)}</strong></div><div><span>Break-even exit price</span><strong>${r.break_even_possible===false?'Not attainable':money(r.break_even_price)}</strong></div></div><p>${esc(r.note)}</p>${r.short_note?`<p>${esc(r.short_note)}</p>`:''}<p class="hint-line">${r.order_type==='limit'?'The calculation assumes the limit fills; it may never fill.':'The modeled price movement is an assumption, not a maximum.'}</p>`;
  });});
  $('cost-quote').addEventListener('click',e=>task(e.currentTarget,'cost-quote-status',async()=>{
    const revision=costRevision, symbol=val('cost-symbol').trim().toUpperCase(), result=await api(`/api/cost-estimate/quote/${encodeURIComponent(symbol)}`), q=result.quote;
    if(revision!==costRevision){txt('cost-quote-status','Inputs changed while the price loaded. Get the price again.');return;}
    const price=Number(q.price);
    costRevision++;
    if(!Number.isFinite(price)||price<=0){
      txt('cost-result','No new price was obtained. Retained price inputs are prior/manual assumptions; calculate again only for that scenario.');
      txt('cost-quote-status',`${symbol}: price unavailable · ${q.source || 'source unavailable'} · retained inputs are unverified assumptions.`);
      return;
    }
    $('cost-price').value=price.toFixed(4);$('cost-exit').value=price.toFixed(4);
    txt('cost-result','Price updated. Calculate again for the current inputs.');
    txt('cost-quote-status',`${symbol}: ${money(q.price)} · ${q.source} · ${q.market_time?new Date(q.market_time).toLocaleString():'time unknown'} · ${q.fresh?'recent observation':'old/unverified; scenario only'}`);
  }));
  $('cost-price').addEventListener('input',()=>txt('cost-quote-status','Price edited manually · hypothetical assumption, not a verified quote.'));
  $('cost-form').addEventListener('input',()=>{costRevision++;txt('cost-result','Inputs changed. Calculate again for the current scenario.');});
  $('cost-symbol').addEventListener('input',()=>{txt('cost-quote-status','Symbol changed. Get a new price or enter a hypothetical price.');$('cost-price').value='';$('cost-exit').value='';});
  $('cost-fee-mode').addEventListener('change',()=>{for(const id of ['cost-entry-fee','cost-exit-fee','cost-borrow-fee'])$(id).disabled=val('cost-fee-mode')!=='custom';});
  for(const id of ['cost-entry-fee','cost-exit-fee','cost-borrow-fee']) $(id).disabled=true;
  $('paper-sizing-form').addEventListener('submit',e=>{e.preventDefault();task(e.submitter,'paper-sizing-status',async()=>{await api('/api/paper-research',{fractional_enabled:checked('paper-fractional'),order_budget:val('paper-order-budget')});txt('paper-sizing-status','Saved for paper only. Existing holdings remain intact.');window.dispatchEvent(new Event('desk:refresh'));});});
  window.addEventListener('desk:state',e=>{
    const c=e.detail?.config;
    if(c && !paperInitialized && 'paper_equity' in c){$('paper-fractional').checked=!!c.paper_fractional_enabled;$('paper-order-budget').value=c.paper_order_budget||0;paperInitialized=true;}
  });
  document.addEventListener('click',e=>{const link=e.target.closest('a[href^="#"]');if(!link)return;const target=document.getElementById(link.getAttribute('href').slice(1));if(target?.tagName==='DETAILS')target.open=true;});
  document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(!document.hidden)refresh();});
  window.addEventListener('moss:refresh',refresh);
  window.addEventListener('pagehide',()=>{pageActive=false;clearTimeout(pollTimer);});
  window.addEventListener('pageshow',()=>{pageActive=true;clearTimeout(pollTimer);refresh();});
  refresh();
})();
