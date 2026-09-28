/* Opening research never activates trading. Only the reviewed, typed confirmation can start Fox. */
(() => {
 'use strict';
 const $=id=>document.getElementById('research-live-'+id);
 if(!document.getElementById('research-live'))return;
 let draft=null,busy=false;
 const message=text=>{$('message').textContent=text;};
 const money=value=>value===null||value===undefined?'Not set':Number(value).toLocaleString('en-US',{style:'currency',currency:'USD'});
 const yes=value=>value?'On':'Off';
 function controls(){
  const fresh=draft&&Date.now()<Date.parse(draft.expires_at);
  $('review').disabled=busy;$('cancel').disabled=busy;$('ack').disabled=busy;$('typed').disabled=busy;
  $('start').disabled=busy||!fresh||!$('ack').checked||$('typed').value!== (draft.identity.paper_mode?'PAPER':'REAL');
  if(draft&&!fresh)message('This review expired. Read the account and limits again before arming.');
 }
 async function call(path,body){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  try{
   const response=await fetch('/api/live-agent/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
   const data=await response.json();if(!response.ok||data.ok!==true)throw Error(data.error||'Account review unavailable');return data;
  }finally{clearTimeout(timer);}
 }
 function rows(id,values){
  const list=$(id);list.replaceChildren();
  for(const [name,value] of values){
   const term=document.createElement('dt'),detail=document.createElement('dd');term.textContent=name;detail.textContent=value;
   list.appendChild(term);list.appendChild(detail);
  }
 }
 function render(data){
  const p=data.policy,ks=data.desk_kill_switch||{},limits=data.desk_limits||{},options=data.auto_options;
  const word=data.identity.paper_mode?'PAPER':'REAL';
  $('account').textContent=`${word==='REAL'?'REAL MONEY':'BROKER SIMULATOR'} · IBKR account …${String(data.identity.account_id).slice(-4)}`;
  $('effect').textContent=data.message;
  rows('limits',[
   ['Current account value',money(data.equity)],['Today’s account profit / loss',money(data.daily_pnl)],
   ['Fox maximum order amount',money(p.max_order_usd)],['Fox daily loss stop',money(p.max_daily_loss_usd)],
   ['Fox order attempts per day',p.max_orders_per_day],['Fox recorded AI budget per day',money(p.model_budget_usd)]
  ]);
  const open=[];if(p.max_order_usd>=1e8)open.push('order amount');if(p.max_daily_loss_usd>=1e8)open.push('daily loss');if(p.max_orders_per_day>=1e5)open.push('order attempts');
  $('open-limits').textContent=open.length?`Effectively open Fox limits: ${open.join(', ')}. These large saved values do not provide practical caps for this account. Other account and desk checks still apply. Change limits in Fox’s live settings if needed.`:'';
  $('universe').textContent=`Fox currently evaluates ${data.universe_count} eligible symbols across the watchlist and radar: ${data.universe_sample.join(', ')}${data.universe_count>data.universe_sample.length?', …':''}. This list can change as radar candidates and quote availability change. The saved symbol list is only a hint, not a fixed trading restriction.`;
  $('options').textContent=options.enabled?`Automatic options entries: ON. Strategies: ${options.strategies.map(s=>s.replaceAll('_',' ')).join(', ')}. Maximum contracts per order: ${options.max_contracts}. Uncovered short options: ${yes(options.allow_naked_short)}. Automatic options exits: ${options.automatic_exits?'broker-paper test only':'OFF; separate position management is needed'}.`:'Automatic options entries: OFF.';
  rows('details',[
   ['Order type',p.order_type],['Research interval',p.interval_sec+' seconds'],['Maximum research attempts per day',p.max_research_per_day],
   ['Minimum model confidence',p.min_confidence],['Maximum quote age',p.max_quote_age_sec+' seconds'],
   ['Stock exits managed by the app',yes(p.protective_exits)+'; app and data must remain available'],['Broker-held stock stops',p.broker_stops?'Broker-paper test':'Off'],
   ['Desk risk preset',data.desk_risk_preset],['Desk daily loss limit',limits.max_daily_loss_pct+'% of account value'],
   ['Desk maximum position',limits.max_position_pct+'% of account value'],['Desk trade attempts per day',limits.max_trades_per_day],
   ['Additional account limits',ks.armed?'Enabled':'Disabled'],['Additional daily loss limit',money(ks.max_daily_loss_usd)],
   ['Additional position limit',money(ks.max_position_size_usd)],['Additional daily attempt limit',ks.max_trades_per_day??'Not set']
  ]);
  $('expiry').textContent='Review expires in 90 seconds. Changes to the account or any saved settings require a new review.';
  $('word').textContent=`Type ${word} to confirm this account`;$('start').textContent=word==='REAL'?'Arm real-money trading':'Arm broker-paper trading';
 }
 $('review').addEventListener('click',async()=>{
  if(busy)return;busy=true;draft=null;$('confirm').hidden=true;$('typed').value='';$('ack').checked=false;controls();message('Checking the connected account and saved limits…');
  try{const data=await call('start-review',{});render(data);draft=data;$('confirm').hidden=false;$('account').focus();message('Review ready. Trading controls have not changed.');}
  catch(e){message('Could not prepare activation. '+e.message);}
  finally{busy=false;controls();}
 });
 $('confirm').addEventListener('submit',async event=>{
  event.preventDefault();controls();if($('start').disabled)return;
  const body={review_token:draft.review_token,identity:draft.identity,revision:draft.revision,confirm:$('typed').value};
  busy=true;draft=null;controls();message('Requesting activation for the reviewed account…');
  try{
   const result=await call('start',body);
   if(result.enabled!==true||result.session_active!==true)throw Error('The desk did not confirm activation.');
   message('Fox is armed for '+(body.identity.paper_mode?'broker-paper trading':'REAL-MONEY trading')+'. Eligible orders may now follow automatically. Open Fox’s live settings and status to monitor or pause.');
  }catch(e){message('Activation was not confirmed. Open Fox’s live settings and status to check whether he is armed before trying again. '+e.message);}
  finally{busy=false;$('confirm').hidden=true;$('typed').value='';$('ack').checked=false;controls();}
 });
 $('cancel').addEventListener('click',()=>{if(busy)return;draft=null;$('confirm').hidden=true;$('typed').value='';$('ack').checked=false;message('Review closed. Trading controls have not changed.');controls();$('review').focus();});
 $('ack').addEventListener('change',controls);$('typed').addEventListener('input',controls);
 setInterval(controls,1000);controls();
})();
