/* Explicit policy / activation controls. No timed mutation, no automatic retry. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),form=$('live-agent-form');if(!form)return;
 const fields=['interval_sec','max_order_usd','max_daily_loss_usd','max_orders_per_day','max_research_per_day','model_budget_usd','limit_offset_bps','min_confidence','max_quote_age_sec'];
 let snapshot=null,dirty=false,editRevision=null,busy=false,received=0,epoch=0,timer=null,loading=false;
 const set=(id,text)=>{$(id).textContent=text;};
 const identityKey=s=>JSON.stringify([s?.identity,s?.revision]);
 async function requestJson(url,options){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{const r=await fetch(url,{...options,signal:controller.signal}),s=await r.json();if(!r.ok||!s.ok)throw Error(s.error||'Agent request failed');return s;}
  finally{clearTimeout(timeout);}
 }
 function controls(){
  const s=snapshot,kind=s?.identity?.paper_mode===false?'REAL':s?.identity?.paper_mode===true?'PAPER':null;
  $('live-agent-start').disabled=busy||dirty||!s?.configured||!kind||s.identity.broker!=='ibkr'||Date.now()-received>25000||$('live-agent-confirm').value.trim()!==kind||s.enabled;
  $('live-agent-save').disabled=busy;
  // Pausing stays available even when status is old or the form has edits.
  $('live-agent-pause').disabled=busy;
 }
 function render(s){
  if(snapshot&&identityKey(snapshot)!==identityKey(s))$('live-agent-confirm').value='';
  snapshot=s;received=Date.now();
  const identity=s.identity,kind=identity?.paper_mode===false?'REAL MONEY':identity?.paper_mode===true?'BROKER PAPER':'UNVERIFIED';
  set('live-agent-account',identity?`${kind} · ${identity.broker} · account …${String(identity.account_id||'').slice(-4)}`:'Account not yet confirmed for execution · verify IBKR in broker settings');
  set('live-agent-state',!s.configured?'Not configured':!s.enabled?'Paused':s.session_active&&s.mode==='auto_live'?'Enabled':'Session stopped');
  set('live-agent-status',s.message||'Waiting for agent status');
  set('live-agent-today',`${s.today?.research||0} research attempts · ${s.today?.orders||0} broker attempts today${s.next_at?' · next cycle no earlier than '+new Date(s.next_at).toLocaleTimeString():''}`);
  if(!dirty){
   $('la-symbols').value=s.policy.symbols.join(' ');
   for(const key of fields)$('la-'+key).value=s.policy[key];
   form.querySelector(`[name="la-order-type"][value="${s.policy.order_type}"]`).checked=true;
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
 function edited(){if(!dirty)editRevision=snapshot?.revision;dirty=true;form.dataset.edits=String(Number(form.dataset.edits||0)+1);$('live-agent-confirm').value='';controls();}
 form.addEventListener('input',edited);form.addEventListener('change',edited);
 form.addEventListener('submit',event=>{event.preventDefault();if(!snapshot||busy)return;const policy={symbols:$('la-symbols').value.toUpperCase().split(/[\s,]+/).filter(Boolean),order_type:form.querySelector('[name="la-order-type"]:checked').value};for(const key of fields)policy[key]=Number($('la-'+key).value);mutate('policy',{policy,revision:dirty?editRevision:snapshot.revision});});
 $('live-agent-reload').addEventListener('click',()=>{if(busy)return;dirty=false;$('live-agent-confirm').value='';++epoch;received=0;read();controls();});
 $('live-agent-confirm').addEventListener('input',controls);
 $('live-agent-start').addEventListener('click',()=>{controls();if($('live-agent-start').disabled)return;mutate('start',{revision:snapshot.revision,identity:snapshot.identity,confirm:$('live-agent-confirm').value.trim()});});
 $('live-agent-pause').addEventListener('click',()=>mutate('pause',{}));
 function watch(){clearTimeout(timer);controls();if(!document.hidden){read();timer=setTimeout(watch,10000);}}
 document.addEventListener('visibilitychange',watch);window.addEventListener('pagehide',()=>{clearTimeout(timer);++epoch;});watch();
})();
