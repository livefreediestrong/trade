/* Explicit policy / activation controls. No timed mutation, no automatic retry. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),form=$('live-agent-form');if(!form)return;
 const fields=['interval_sec','max_order_usd','max_daily_loss_usd','max_orders_per_day','max_research_per_day','model_budget_usd','limit_offset_bps','min_confidence','max_quote_age_sec'];
 let snapshot=null,dirty=false,editRevision=null,busy=false,received=0,epoch=0,timer=null,loading=false;
 let quoteRows=[],quoteRun=0,quoteBusy=false,quoteDraft=null;
 const set=(id,text)=>{$(id).textContent=text;};
 const identityKey=s=>JSON.stringify([s?.identity,s?.revision]);
 const budgetKey=()=>JSON.stringify([$('la-symbols').value,$('la-max_order_usd').value,$('la-max_quote_age_sec').value]);
 const money=value=>value.toLocaleString(undefined,{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:4});
 function clearBudget(){++quoteRun;quoteRows=[];quoteDraft=null;$('live-agent-budget-list').replaceChildren();set('live-agent-budget-status','Check whole-share capacity using current quotes. No policy is saved and no order is sent.');}
 function renderBudget(){
  if(!quoteDraft)return;
  if(quoteDraft!==budgetKey()){clearBudget();return;}
  const budget=Number($('la-max_order_usd').value),maxAge=Number($('la-max_quote_age_sec').value);
  $('live-agent-budget-list').replaceChildren(...quoteRows.map(row=>{
   const li=document.createElement('li'),q=row.quote||{},age=(Date.now()-Date.parse(q.market_time))/1000;
   const valid=typeof q.price==='number'&&Number.isFinite(q.price)&&q.price>0&&q.fresh===true&&Number.isFinite(age)&&age>=0&&age<=maxAge&&typeof q.source==='string'&&q.source.trim()&&!/mock|demo|synthetic|fixture|unknown/i.test(q.source);
   if(row.error||!valid){li.textContent=`${row.symbol} · ${row.error||'Quote unavailable or older than your limit; check again.'}`;return li;}
   const shares=Math.floor(budget/q.price);
   li.textContent=`${row.symbol} · ${money(q.price)} · ${shares.toLocaleString()} whole shares at this quote${shares===0?` · one share needs at least ${money(q.price)} before fees`:''} · ${q.source}, ${Math.floor(age)}s old`;
   return li;
  }));
 }
 async function checkBudget(){
  if(busy||quoteBusy)return;
  const symbols=[...new Set($('la-symbols').value.toUpperCase().split(/[\s,]+/).filter(Boolean))],budget=Number($('la-max_order_usd').value),age=Number($('la-max_quote_age_sec').value);
  clearBudget();
  if(!symbols.length||symbols.length>500||symbols.some(s=>!/^[A-Z]{1,5}(?:\.[A-Z])?$/.test(s))||!Number.isFinite(budget)||budget<.01||budget>1e9||!Number.isInteger(age)||age<1||age>60){set('live-agent-budget-status','Enter valid US symbols, a positive order amount and a quote age of 1–60 seconds.');return;}
  quoteBusy=true;const run=quoteRun,key=budgetKey(),selected=symbols.slice(0,8),rows=[];let next=0;
  controls();set('live-agent-budget-status',`Checking ${selected.length} symbol${selected.length===1?'':'s'}${symbols.length>8?` of ${symbols.length}; only the first 8 are checked`:''}…`);
  async function worker(){while(next<selected.length&&run===quoteRun){const index=next++,symbol=selected[index];try{const s=await requestJson('/api/cost-estimate/quote/'+encodeURIComponent(symbol),{cache:'no-store'});rows[index]={symbol,quote:s.quote};}catch(e){rows[index]={symbol,error:e.name==='AbortError'?'Quote request timed out; check again.':e.message};}}}
  try{await Promise.all([worker(),worker()]);if(run===quoteRun&&key===budgetKey()){quoteDraft=key;quoteRows=rows;set('live-agent-budget-status',`Checked ${selected.length} of ${symbols.length} symbols. Price-only capacity; fees, limit offsets, available cash and desk risk limits can reduce it.${symbols.length>8?' Other symbols have not been checked.':''}`);renderBudget();}}
  finally{quoteBusy=false;controls();}
 }
 async function requestJson(url,options){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{const r=await fetch(url,{...options,signal:controller.signal}),s=await r.json();if(!r.ok||!s.ok)throw Error(s.error||'Agent request failed');return s;}
  finally{clearTimeout(timeout);}
 }
 function controls(){
  const s=snapshot,kind=s?.identity?.paper_mode===false?'REAL':s?.identity?.paper_mode===true?'PAPER':null;
  $('live-agent-start').disabled=busy||dirty||!s?.configured||!kind||s.identity.broker!=='ibkr'||Date.now()-received>25000||$('live-agent-confirm').value.trim()!==kind||s.enabled;
  $('live-agent-save').disabled=busy;
  $('live-agent-budget-check').disabled=busy||quoteBusy;
  renderBudget();
  // Pausing stays available even when status is old or the form has edits.
  $('live-agent-pause').disabled=busy;
 }
 function render(s){
  if(snapshot&&identityKey(snapshot)!==identityKey(s))$('live-agent-confirm').value='';
  snapshot=s;received=Date.now();
  const identity=s.identity,kind=identity?.paper_mode===false?'REAL MONEY':identity?.paper_mode===true?'BROKER PAPER':'UNVERIFIED';
  set('live-agent-account',identity?`${kind} · ${identity.broker} · account …${String(identity.account_id||'').slice(-4)}`:'Account not yet confirmed for execution · verify IBKR in broker settings');
  set('live-agent-state',!s.configured?'Not configured':!s.enabled?'Paused':s.session_active&&s.mode==='auto_live'?'Enabled':'Session stopped');
  (function(){
    const chip=document.getElementById('bx-agent-chip');
    const detail=document.getElementById('bx-agent-detail');
    if(!chip)return;
    let label='Paused', cls='is-paused';
    if(!s.configured){label='Not set up'; cls='is-blocked';}
    else if(!s.enabled){label='Paused'; cls='is-paused';}
    else if(s.blocked||s.block_reason){label='Blocked'; cls='is-blocked';}
    else if(s.waiting_for_quote||s.quote_wait){label='Waiting for quote'; cls='is-waiting';}
    else if(s.session_active&&s.mode==='auto_live'){label='Ready to trade'; cls='is-ready';}
    else if(s.running||s.scanning||s.researching){label='Scanning'; cls='is-scanning';}
    else if(s.session_active){label='Session on'; cls='is-scanning';}
    else {label='Session stopped'; cls='is-paused';}
    chip.className='bx-agent-chip '+cls;
    chip.textContent=label;
    if(detail) detail.textContent=s.message||s.next||'Moss agent status';
  })();
  set('live-agent-status',s.message||'Waiting for agent status');
  set('live-agent-next',!identity?'Next: verify the account in broker settings. You can check your draft budget below.':!s.configured?'Next: check your symbols and budget, then save a policy.':s.enabled?'Agent enabled. Pause stops new work; working orders and positions stay at the broker.':dirty?'Next: save your edited policy. This leaves the agent paused.':'Policy saved and paused. After broker-paper testing, confirm the displayed account to start.');
  set('live-agent-today',`${s.today?.research||0} research attempts · ${s.today?.orders||0} broker attempts today${s.next_at?' · next cycle no earlier than '+new Date(s.next_at).toLocaleTimeString():''}`);
  if(!dirty){
   $('la-symbols').value=s.policy.symbols.join(' ');
   for(const key of fields)$('la-'+key).value=s.policy[key];
   form.querySelector(`[name="la-order-type"][value="${s.policy.order_type}"]`).checked=true;
   const ao=s.auto_options||{};
   const strats=new Set(ao.strategies||[]);
   if($('la-ao-enabled'))$('la-ao-enabled').checked=!!ao.enabled;
   if($('la-ao-long-call'))$('la-ao-long-call').checked=strats.has('long_call');
   if($('la-ao-long-put'))$('la-ao-long-put').checked=strats.has('long_put');
   if($('la-ao-short-call'))$('la-ao-short-call').checked=strats.has('short_call');
   if($('la-ao-spreads'))$('la-ao-spreads').checked=['call_debit','put_debit','call_credit','put_credit'].some(x=>strats.has(x));
   if($('la-ao-naked'))$('la-ao-naked').checked=!!ao.allow_naked_short;
   if($('la-ao-max-contracts') && ao.max_contracts!=null)$('la-ao-max-contracts').value=ao.max_contracts;
   if($('la-auto-options-label') && s.auto_options_label)$('la-auto-options-label').textContent=s.auto_options_label+(s.auto_options_armed?' · ARMED':' · not armed');
  }
  $('live-agent-history').replaceChildren(...(s.events||[]).slice(0,8).map(e=>{const li=document.createElement('li');li.textContent=`${new Date(e.at).toLocaleTimeString()} · ${e.ticker?e.ticker+' · ':''}${e.message}`;return li;}));
  controls();
 }
 async function read(){
  if(document.hidden||busy||loading)return;
  loading=true;const requestEpoch=epoch;
  try{const s=await requestJson('/api/live-agent',{cache:'no-store'});if(requestEpoch===epoch&&!busy)render(s);}
  catch(e){if(requestEpoch===epoch){received=0;set('live-agent-status',e.message);controls();}}
  finally{loading=false;}
 }
 async function mutate(path,body){
  if(busy)return;busy=true;++epoch;controls();
  const editVersion=form.dataset.edits||'0';
  try{
   const s=await requestJson('/api/live-agent/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   if(path==='policy'){
    if((form.dataset.edits||'0')===editVersion)dirty=false;
    else editRevision=s.revision; // Remaining edits are based on our own completed save.
   }
   $('live-agent-confirm').value='';render(s);
   set('live-agent-result',path==='start'?'Agent enabled for the confirmed account. Orders still depend on current session, data and risk checks.':path==='pause'?'Agent paused. Check working orders and positions separately.':'Policy saved. Agent paused.');
  }catch(e){received=0;$('live-agent-confirm').value='';set('live-agent-result',e.message+' Status will refresh; this request will not retry automatically.');}
  finally{busy=false;controls();}
 }
 function edited(){if(!dirty)editRevision=snapshot?.revision;dirty=true;form.dataset.edits=String(Number(form.dataset.edits||0)+1);$('live-agent-confirm').value='';clearBudget();controls();}
 form.addEventListener('input',edited);form.addEventListener('change',edited);
 form.addEventListener('submit',event=>{event.preventDefault();if(!snapshot||busy)return;const policy={symbols:$('la-symbols').value.toUpperCase().split(/[\s,]+/).filter(Boolean),order_type:form.querySelector('[name="la-order-type"]:checked').value};for(const key of fields)policy[key]=Number($('la-'+key).value);const strategies=[];if($('la-ao-long-call')?.checked)strategies.push('long_call');if($('la-ao-long-put')?.checked)strategies.push('long_put');if($('la-ao-short-call')?.checked)strategies.push('short_call');if($('la-ao-spreads')?.checked)strategies.push('call_debit','put_debit','call_credit','put_credit');if(!strategies.length)strategies.push('long_call','long_put');const auto_options={enabled:!!$('la-ao-enabled')?.checked,strategies,allow_naked_short:!!$('la-ao-naked')?.checked,max_contracts:Number($('la-ao-max-contracts')?.value||2),dte_min:5,dte_max:45,max_quote_age_sec:15,prefer_otm_pct:0.5,spread_width_pct:1.0,every_n_stock_cycles:1};mutate('policy',{policy,auto_options,revision:dirty?editRevision:snapshot.revision});});
 $('live-agent-reload').addEventListener('click',()=>{if(busy)return;dirty=false;clearBudget();$('live-agent-confirm').value='';++epoch;received=0;read();controls();});
 $('live-agent-budget-check').addEventListener('click',checkBudget);
 $('live-agent-confirm').addEventListener('input',controls);
 $('live-agent-start').addEventListener('click',()=>{controls();if($('live-agent-start').disabled)return;mutate('start',{revision:snapshot.revision,identity:snapshot.identity,confirm:$('live-agent-confirm').value.trim()});});
 $('live-agent-pause').addEventListener('click',()=>mutate('pause',{}));
 function watch(){clearTimeout(timer);controls();if(!document.hidden){read();timer=setTimeout(watch,10000);}}
 document.addEventListener('visibilitychange',watch);window.addEventListener('pagehide',()=>{clearTimeout(timer);++epoch;});watch();
})();
