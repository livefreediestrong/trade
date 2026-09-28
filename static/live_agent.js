/* Explicit policy / activation controls. No timed mutation, no automatic retry. */
(() => {
 'use strict';
 function agentView(s,state,age,agentAge){
  const fresh=!!state&&age<=25000,book=state?.broker_book||{},broker=state?.broker||{},blocks=[];
  const connected=fresh&&broker.connected===true&&book.ok===true;
  if(!fresh)blocks.push('Account status is stale or unavailable. Waiting for a fresh desk update.');
  else if(!connected)blocks.push('Gateway is disconnected or the account is unverified. Open broker settings to resolve it.');
  if(fresh&&(book.risk_ready!==true||typeof book.day_pnl_usd!=='number'||!Number.isFinite(book.day_pnl_usd)))blocks.push('Daily P&L or risk data is unavailable. New entries are blocked; verified reducing orders have separate checks.');
  if(s?.market_open!==true)blocks.push('Regular market is closed or unverified. Agent research and stock exit checks wait for market hours.');
  const pending=state?.broker_ledger?.pending_broker_orders?.length||0;
  if(pending)blocks.push(`${pending} broker order${pending===1?' is':'s are'} unresolved. Reconciliation must finish before another submission.`);
  let label='Monitoring',cls='is-scanning';
  if(!s||agentAge>25000){label='Status unavailable';cls='is-blocked';}
  else if(!s.configured){label='Not set up';cls='is-blocked';}
  else if(!s.enabled||!s.session_active||s.mode!=='auto_live'){label='Paused';cls='is-paused';}
  else if(fresh&&!connected){label='Connection needed';cls='is-blocked';}
  else if(s.market_open!==true){label='Waiting for market';cls='is-waiting';}
  else if(!fresh){label='Checking account';cls='is-waiting';}
  else if(pending){label='Reconciling order';cls='is-waiting';}
  else if(book.risk_ready!==true||!Number.isFinite(book.day_pnl_usd)){label='New entries blocked';cls='is-blocked';}
  else if(['blocked','error','budget','daily_limit','event_window','coverage_wait','option_review','protection_review'].includes(s.phase)){label=['blocked','error','option_review','protection_review'].includes(s.phase)?'Needs attention':'Research paused';cls='is-blocked';blocks.push(s.message||'Read the latest agent status.');}
  else if(s.phase==='entry_window'){label='Outside buy window (10:30-11:00)';cls='is-waiting';blocks.push(s.message||'Automatic buys only happen from 10:30 to 11:00 a.m. Eastern.');}
  else if(s.phase==='researching'){label='Researching';cls='is-scanning';}
  return {label,cls,blocks,connected};
 }
 if(typeof module==='object'&&module.exports){module.exports={agentView};return;}
 const $=id=>document.getElementById(id),form=$('live-agent-form');if(!form)return;
 const fields=['interval_sec','max_order_usd','max_daily_loss_usd','max_orders_per_day','max_research_per_day','model_budget_usd','limit_offset_bps','min_confidence','max_quote_age_sec','breakeven_after_r','max_hold_min','flatten_before_close_min','max_positions_per_sector'];
 let snapshot=null,dirty=false,editRevision=null,busy=false,received=0,epoch=0,timer=null,loading=false;
 let deskState=null,deskReceived=0,draftOptions=null;
 const strategies=['long_call','long_put','short_call','short_put','call_debit','put_debit','call_credit','put_credit'];
 let quoteRows=[],quoteRun=0,quoteBusy=false,quoteDraft=null;
 const set=(id,text)=>{if($(id))$(id).textContent=text;};
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
  renderOperations();
 }
 function renderOperations(){
  const s=snapshot,v=agentView(s,deskState,Date.now()-deskReceived,Date.now()-received);
  set('live-agent-state',v.label);
  const chip=$('bx-agent-chip');if(chip){chip.textContent=v.label;chip.className='bx-agent-chip '+v.cls;}
  set('bx-agent-detail',v.blocks[0]||s?.message||'Reading agent status');
  if($('live-agent-checks'))$('live-agent-checks').replaceChildren(...v.blocks.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
  set('live-agent-draft',dirty?'Unsaved policy edits. The agent continues using its saved policy until you save; saving pauses it.':'Showing saved policy. Editing these fields does not change the running agent.');
  const p=s?.policy||{},equity=deskState?.broker_book?.equity;
  const caps=p.max_order_usd>=1e8||p.max_daily_loss_usd>=1e8||p.max_orders_per_day>=100000;
  const effective=deskState?.live_risk_limits,limitsFresh=effective?.verified===true&&Date.now()-deskReceived<=25000;
  set('risk-draft-summary',(limitsFresh?`Effective limit: ${money(effective.max_order_usd)} per stock position or option maximum loss, ${money(effective.max_daily_loss_usd)} daily-loss entry stop, ${effective.max_orders_per_day} agent attempts. `:'Effective limits await a current verified account reading. ')+(caps?'The large saved values below are extra ceilings; they cannot override the account preset. ':'')+(typeof equity==='number'&&Number.isFinite(equity)?`Last reported account equity: ${money(equity)}. `:'')+'Drafts do not change the running policy until saved.');
  if($('la-auto-options-label'))set('la-auto-options-label',s?.auto_options_armed?'Automatic options · IBKR paper exit test armed':s?.auto_options?.enabled?(s.auto_options_entry_block||'Automatic options are not armed'):'Automatic options are off');
  const owner=s?.account_scope,entries=Object.entries(s?.managed||{});
  set('live-agent-managed',entries.length?`${entries.length} tracked stock position${entries.length===1?'':'s'}. Exit checks require the matching account, an active agent, market hours and current quotes.`:'No agent stock positions are currently tracked.');
  if($('live-agent-position-list'))$('live-agent-position-list').replaceChildren(...entries.map(([ticker,row])=>{
   const item=document.createElement('li'),title=document.createElement('strong'),detail=document.createElement('p'),status=document.createElement('p');
   title.textContent=`${ticker} · ${row.shares} shares`;
   detail.textContent=`Entry ${money(Number(row.entry))} · stop ${money(Number(row.stop))}${row.breakeven?' (raised)':''} · target ${money(Number(row.target))}${row.exit_at?' · hold deadline '+new Date(row.exit_at).toLocaleString():''}`;
   const check=s?.exit_checks?.[ticker],current=check&&Date.now()-Date.parse(check.checked_at)<=25000;
   const note=!row.account_scope||!owner||!['broker','account_id','paper_mode'].every(k=>row.account_scope[k]===owner[k])?'Account review required; no automatic exit for this record.':row.tracking_error?row.tracking_error:!s.policy.protective_exits?'Exit monitoring is disabled in the saved policy.':!s.enabled||!s.session_active||s.mode!=='auto_live'?'Agent paused; exit monitoring is paused.':!v.connected?'Connection required before exit monitoring can be verified.':!s.market_open?'Market closed; exit monitoring waits for market hours.':current?check.message:'Waiting for a current exit check.';
   status.textContent=note+(check?.checked_at?' Last check '+new Date(check.checked_at).toLocaleString()+'.':'');
   item.replaceChildren(title,detail,status);return item;
  }));
 }
 function render(s){
  if(snapshot&&identityKey(snapshot)!==identityKey(s))$('live-agent-confirm').value='';
  snapshot=s;received=Date.now();
  window.TomahawkStopReview?.render(s);
  const optionRows=s.option_positions||[];
  set('option-lifecycle-status',s.option_check_error|| (optionRows.length?'Tracked option fills are compared with broker holdings. Pausing stops automatic exits; exposure checks continue.':'No option positions are tracked. The automatic lifecycle is an optional IBKR paper test.'));
  if($('option-lifecycle-rows'))$('option-lifecycle-rows').replaceChildren(...optionRows.map(row=>{
   const li=document.createElement('li'),title=document.createElement('strong'),detail=document.createElement('p');
   title.textContent=`${row.ticker} · ${String(row.strategy).replaceAll('_',' ')} · ${row.remaining} remaining`;
   detail.textContent=(row.externally_resolved?'External resolution reviewed; broker was verified flat. Original fill accounting remains incomplete.':row.hold|| (row.awaiting_entry?'Waiting for confirmed entry details.':row.remaining?'Exit checks use the saved rules.':'Recorded fills have closed this position.'))+` Expiry ${row.expiry}. Planned latest exit ${new Date(row.deadline).toLocaleString()}.`;
   if(row.externally_resolved)title.textContent=`${row.ticker} · reviewed external closure`;
   li.append(title,detail);
   if(row.hold){const button=document.createElement('button');button.type='button';button.textContent='Review after resolving at broker';button.addEventListener('click',()=>window.TomahawkOptionReview?.prepare(row.id));li.append(button);}
   return li;
  }));
  const identity=s.identity,kind=identity?.paper_mode===false?'REAL MONEY':identity?.paper_mode===true?'BROKER PAPER':'UNVERIFIED';
  set('live-agent-account',identity?`Saved account: ${kind} · ${identity.broker} · …${String(identity.account_id||'').slice(-4)}. Connection is checked separately above.`:'Account not yet confirmed for execution · verify IBKR in broker settings');
  set('live-agent-state',!s.configured?'Not configured':!s.enabled?'Paused':s.session_active&&s.mode==='auto_live'?'Enabled':'Session stopped');
  set('live-agent-status',s.message||'Waiting for agent status');
  set('live-agent-next',!identity?'Next: verify the account in broker settings. You can check your draft budget below.':!s.configured?'Next: check your symbols and budget, then save a policy.':s.enabled?'Agent enabled. Pause stops new work; working orders and positions stay at the broker.':dirty?'Next: save your edited policy. This leaves the agent paused.':'Policy saved and paused. After broker-paper testing, confirm the displayed account to start.');
  set('live-agent-today',`${s.today?.research||0} / ${s.policy.max_research_per_day} research attempts · ${s.today?.orders||0} broker attempts today${s.next_at&&Date.parse(s.next_at)>Date.now()&&s.market_open?' · next research no earlier than '+new Date(s.next_at).toLocaleTimeString():''}`);
  set('live-agent-universe',`${s.eval_symbols_count??'Unknown'} symbols in the evaluated watchlist. Orders follow this full watchlist and discovery results. The symbol field below is for saved preferences and budget checks; it does not restrict live orders.`);
  if(!dirty){
   draftOptions=null;
   $('la-symbols').value=s.policy.symbols.join(' ');
   for(const key of fields)$('la-'+key).value=s.policy[key];
   if($('la-protective_exits'))$('la-protective_exits').checked=s.policy.protective_exits!==false;
   if($('la-broker_stops'))$('la-broker_stops').checked=s.policy.broker_stops===true;
   form.querySelector(`[name="la-order-type"][value="${s.policy.order_type}"]`).checked=true;
   const ao=s.auto_options||{};
   const strats=new Set(ao.strategies||['long_call','long_put']);
   if($('la-ao-enabled'))$('la-ao-enabled').checked=!!ao.enabled;
   if($('la-ao-automatic_exits'))$('la-ao-automatic_exits').checked=!!ao.automatic_exits;
   for(const key of ['exit_loss_pct','exit_profit_pct','exit_before_expiry_sessions','exit_max_spread_pct'])if($('la-ao-'+key))$('la-ao-'+key).value=ao[key]??({exit_loss_pct:25,exit_profit_pct:50,exit_before_expiry_sessions:1,exit_max_spread_pct:20})[key];
   for(const strategy of strategies)if($('la-ao-'+strategy))$('la-ao-'+strategy).checked=strats.has(strategy);
   if($('la-ao-naked'))$('la-ao-naked').checked=!!ao.allow_naked_short;
   if($('la-ao-max-contracts') && ao.max_contracts!=null)$('la-ao-max-contracts').value=ao.max_contracts;
   // Effective eligibility is rendered from current status, separately from the saved checkbox.
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
 function edited(){if(!dirty){editRevision=snapshot?.revision;draftOptions=JSON.parse(JSON.stringify(snapshot?.auto_options||{}));}dirty=true;form.dataset.edits=String(Number(form.dataset.edits||0)+1);$('live-agent-confirm').value='';clearBudget();controls();}
 form.addEventListener('input',edited);form.addEventListener('change',edited);
 function fillRiskDraft(kind){
  if(!snapshot||busy)return;
  edited();
  const paper=kind==='paper';
  $('la-max_order_usd').value=paper?100:10;
  $('la-max_daily_loss_usd').value=paper?200:2;
  $('la-max_orders_per_day').value=paper?10:3;
  form.querySelector('[name="la-order-type"][value="limit"]').checked=true;
  if($('la-ao-enabled'))$('la-ao-enabled').checked=paper;
  if($('la-ao-automatic_exits'))$('la-ao-automatic_exits').checked=paper;
  if($('la-ao-max-contracts'))$('la-ao-max-contracts').value=1;
  if($('la-ao-naked'))$('la-ao-naked').checked=false;
  for(const strategy of strategies)if($('la-ao-'+strategy))$('la-ao-'+strategy).checked=['long_call','long_put'].includes(strategy);
  for(const [key,value] of Object.entries({exit_loss_pct:25,exit_profit_pct:50,exit_before_expiry_sessions:1,exit_max_spread_pct:20}))if($('la-ao-'+key))$('la-ao-'+key).value=value;
  set('live-agent-result','Draft filled. Nothing saved, started or paused. Review the fields before saving. Other desk limits can be stricter.');
  controls();
 }
 $('risk-draft-paper')?.addEventListener('click',()=>fillRiskDraft('paper'));
 $('risk-draft-small')?.addEventListener('click',()=>fillRiskDraft('small'));
 form.addEventListener('submit',event=>{
  event.preventDefault();if(!snapshot||busy)return;
  const policy={symbols:$('la-symbols').value.toUpperCase().split(/[\s,]+/).filter(Boolean),order_type:form.querySelector('[name="la-order-type"]:checked').value};
  for(const key of fields)policy[key]=Number($('la-'+key).value);
  policy.protective_exits=!!$('la-protective_exits')?.checked;
  policy.broker_stops=!!$('la-broker_stops')?.checked;
  const optionBase=draftOptions||snapshot.auto_options||{};
  const strategyOrder=[...new Set([...(optionBase.strategies||[]),...strategies])];
  const selected=strategyOrder.filter(key=>$('la-ao-'+key)?.checked);
  if(!selected.length){set('live-agent-result','Select at least one options strategy. No settings have been saved.');return;}
  const auto_options={...optionBase,enabled:!!$('la-ao-enabled')?.checked,strategies:selected,allow_naked_short:!!$('la-ao-naked')?.checked,max_contracts:Number($('la-ao-max-contracts')?.value||2)};
  auto_options.automatic_exits=!!$('la-ao-automatic_exits')?.checked;
  for(const key of ['exit_loss_pct','exit_profit_pct','exit_before_expiry_sessions','exit_max_spread_pct'])if($('la-ao-'+key))auto_options[key]=Number($('la-ao-'+key).value);
  mutate('policy',{policy,auto_options,revision:dirty?editRevision:snapshot.revision});
 });
 $('live-agent-reload').addEventListener('click',()=>{if(busy)return;dirty=false;clearBudget();$('live-agent-confirm').value='';++epoch;received=0;read();controls();});
 $('live-agent-budget-check').addEventListener('click',checkBudget);
 $('live-agent-confirm').addEventListener('input',controls);
 $('live-agent-start').addEventListener('click',()=>{controls();if($('live-agent-start').disabled)return;mutate('start',{revision:snapshot.revision,identity:snapshot.identity,confirm:$('live-agent-confirm').value.trim()});});
 $('live-agent-pause').addEventListener('click',()=>mutate('pause',{}));
 function watch(){clearTimeout(timer);controls();if(!document.hidden){read();timer=setTimeout(watch,10000);}}
 document.addEventListener('visibilitychange',watch);window.addEventListener('pagehide',()=>{clearTimeout(timer);++epoch;});watch();
 window.addEventListener('desk:state',event=>{deskState=event.detail;deskReceived=Date.now();renderOperations();});
})();
