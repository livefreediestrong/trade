/* Paper controls only. Use the existing paper-settings API; never broker routes. */
(() => {
 'use strict';
 const usd=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(n);
 function view(w){
  const p=w.settings,t=w.today||{};
  const waiting=w.phase==='waiting_for_next_cycle'&&Number.isFinite(Date.parse(w.next_at));
  const state=w.error?'Needs attention':!w.active?'New entries paused':w.busy?'Reviewing candidates':w.phase==='waiting_for_market'?'Waiting for market':waiting?'Waiting for next cycle':'Paper workday enabled';
  const next=w.error?'Read the latest issue below. Failed background checks retry automatically; new research waits for recovery.':!w.active?'No new automatic entries. Review saved limits when you are ready to start.':w.busy?'Moss is checking evidence. You can leave this section; no action is needed.':w.phase==='waiting_for_market'?'Waiting for the next regular US market session. Holidays and early closes are respected.':waiting?'Next candidate check no earlier than '+new Date(w.next_at).toLocaleTimeString()+'. Due exits and outcome checks continue.':'Moss checks candidates roughly every '+Math.round(p.interval_sec/60*10)/10+' minutes. No eligible setup means no trade; you do not need to intervene.';
  return {state,next,amount:`${usd(p.base_order_usd)} base · ${usd(p.max_order_usd)} maximum`,activity:`${t.cycles||0} cycles · ${t.paper_fills||0} paper fills`,limits:`${p.max_positions} positions · ${p.max_daily_loss_pct}% loss limit · ${p.max_trades_per_day} entry/fill cap`,last:w.error||t.last_result||'No completed candidate result recorded today.',review:`${p.universe==='broad_us'?'Broad US stocks & ETFs plus ':''}Focus list: ${p.symbols.join(', ')}. ${usd(p.base_order_usd)} base / ${usd(p.max_order_usd)} maximum per entry; ${p.max_total_exposure_pct}% total paper exposure; ${p.max_positions} positions; ${p.max_daily_loss_pct}% daily loss limit. Up to ${p.max_model_calls} AI attempts and ${usd(p.model_budget_usd)} recorded desk AI costs per day (unreported charges are not included). Check every ${p.interval_sec}s; evaluate after ${p.horizon_min} minutes. ${p.flatten_before_close?'Attempt fresh-quote exits before close.':'No optional pre-close flattening.'} Personality: ${p.personality==='empirical_bayes'?'adaptive empirical Bayes':'fixed-size Stoic collector'}.`};
 }
 if(typeof module==='object'&&module.exports){module.exports={view};return;}
 const $=id=>document.getElementById(id);if(!$('moss-automation'))return;
 let workday=null,received=0,pending=false,review=null,ignoreBefore=0,active=true,timer=null;
 const put=(id,text)=>{if($(id).textContent!==text)$(id).textContent=text;};
 const key=w=>JSON.stringify(w.settings);
 function buttons(){
  const stale=!received||Date.now()-received>45000;
  $('paper-auto-pause').disabled=pending||stale||!workday?.settings.enabled;
  $('paper-auto-review').disabled=pending||stale||!!workday?.active;
  $('paper-auto-refresh').disabled=pending;
  $('paper-auto-start').disabled=pending||!review;
  $('paper-auto-review').hidden=!!workday?.active;
  $('paper-auto-pause').hidden=!!workday&&!workday.settings.enabled;
  if(stale){put('paper-auto-state','Status needs refresh');put('paper-auto-next','Check status to see whether the workday is running. A stale screen does not pause automation.');}
 }
 function accept(w){
  if(!w?.settings||!w.today)return;
  workday=w;received=Date.now();const v=view(w);
  put('paper-auto-state',v.state);put('paper-auto-next',v.next);put('paper-auto-amount',v.amount);put('paper-auto-activity',v.activity);put('paper-auto-limits',v.limits);put('paper-auto-last','Latest result: '+v.last);
  put('paper-auto-stamp','Checked '+new Date(received).toLocaleTimeString()+'. Due exits can exceed the daily fill cap; all existing paper risk checks still apply.');
  if(review&&key(w)!==review.key){review=null;$('paper-auto-confirm').hidden=true;put('paper-auto-feedback','Saved limits changed. Review them again before starting.');}
  buttons();
 }
 async function api(path,body){
  const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),25000);
  try{const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:abort.signal,cache:'no-store'});const r=await response.json();if(!response.ok||r.ok===false)throw Error(r.error||'Request failed.');return r;}finally{clearTimeout(timeout);}
 }
 async function current(){const s=await api('/api/companion');if(!s.paper_workday?.settings)throw Error('Paper status unavailable.');accept(s.paper_workday);return s.paper_workday;}
 async function action(fn){
  if(pending)return;pending=true;ignoreBefore=Date.now();buttons();put('paper-auto-feedback','Checking the saved paper configuration…');
  try{await fn();}catch(e){received=0;review=null;$('paper-auto-confirm').hidden=true;put('paper-auto-feedback',e.name==='AbortError'?'The response timed out; the save may have completed. Check status before trying again. No automatic retry was sent.':e.message+' Check status before trying again.');}
  finally{pending=false;ignoreBefore=Date.now();buttons();}
 }
 async function save(w,enabled){
  await api('/api/companion/paper',{...w.settings,enabled});
  review=null;$('paper-auto-confirm').hidden=true;
  // Reflect just the switch in the editor; retain any unsaved limit edits.
  if($('moss-paper-enabled'))$('moss-paper-enabled').checked=enabled;
  await current();window.dispatchEvent(new Event('moss:refresh'));window.dispatchEvent(new Event('desk:refresh'));
  put('paper-auto-feedback',enabled?'Paper workday saved. It starts when market hours and data/risk checks permit.':'New paper entries paused. Due exits and outcome checks continue.');
 }
 $('paper-auto-refresh').addEventListener('click',()=>action(async()=>{await current();put('paper-auto-feedback','Status checked.');}));
 $('paper-auto-pause').addEventListener('click',()=>action(async()=>save(await current(),false)));
 $('paper-auto-review').addEventListener('click',()=>action(async()=>{
  const w=await current();if(w.active){put('paper-auto-feedback','Paper workday is already enabled.');return;}
  review={key:key(w),at:Date.now()};put('paper-auto-review-text',view(w).review);$('paper-auto-confirm').hidden=false;put('paper-auto-feedback','Review the saved limits below. Nothing has started.');
 }));
 $('paper-auto-start').addEventListener('click',()=>action(async()=>{
  const approved=review;if(!approved)throw Error('Review the saved limits again.');
  const w=await current();if(key(w)!==approved.key)throw Error('Saved limits changed. Review them again.');
  if(w.active){review=null;$('paper-auto-confirm').hidden=true;put('paper-auto-feedback','Paper workday is already enabled.');return;}
  await save(w,true);
 }));
 $('paper-auto-dismiss').addEventListener('click',()=>{review=null;$('paper-auto-confirm').hidden=true;buttons();put('paper-auto-feedback','Review dismissed. No change sent.');});
 // Reuse the companion's single status poll; ignore reads begun before a save.
 window.addEventListener('moss:workday',e=>{
  const d=e.detail||{};if(pending||!active||d.startedAt<ignoreBefore)return;
  if(d.error){received=0;buttons();}else accept(d.workday);
 });
 function age(){clearTimeout(timer);if(!active||document.hidden)return;buttons();timer=setTimeout(age,1000);}
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)age();});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);});
 window.addEventListener('pageshow',e=>{active=true;age();if(e.persisted)window.dispatchEvent(new Event('moss:refresh'));});age();
})();
