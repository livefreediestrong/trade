/* Entry and profit guards: desk-wide settings for new entries. Never places orders. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),form=$('trade-guards-form');if(!form)return;
 const nums=['min_net_reward_risk','evidence_min_samples','giveback_stop_pct','event_guard_before_min','event_guard_after_min','wsb_crowd_min_mentions'];
 const checks=['evidence_gate_enabled','event_guard_enabled'];
 const eventsText=rows=>(rows||[]).map(r=>[r.start,r.title,r.impact].join(' | ')).join('\n');
 function parseEvents(text){
  return String(text||'').split('\n').map(l=>l.trim()).filter(Boolean).map((line,i)=>{
   const [start,title,impact]=line.split('|').map(x=>(x||'').trim());
   if(!/^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}$/.test(start||'')||!title)throw Error('Event line '+(i+1)+' needs "YYYY-MM-DD HH:MM | title | impact"');
   return {start,title,impact:(impact||'high').toLowerCase()};
  });
 }
 let busy=false;
 const status=text=>{$('tg-status').textContent=text;};
 function fill(cfg){
  if(!cfg||typeof cfg!=='object')return;
  for(const key of nums){const el=$('tg-'+key);if(el&&document.activeElement!==el&&cfg[key]!=null)el.value=cfg[key];}
  for(const key of checks){const el=$('tg-'+key);if(el)el.checked=cfg[key]!==false;}
  const action=$('tg-wsb_crowding_action');if(action&&document.activeElement!==action)action.value=cfg.wsb_crowding_action||'half';
  const custom=$('tg-custom_market_events');if(custom&&document.activeElement!==custom)custom.value=eventsText(cfg.custom_market_events);
 }
 async function load(){
  try{const r=await fetch('/api/config',{credentials:'same-origin'}),d=await r.json();if(!r.ok)throw Error(d.error||'unavailable');fill(d.config);}
  catch(e){status('Could not read the saved guards; reload the page to try again.');}
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;
  const body={};
  for(const key of checks)if($('tg-'+key))body[key]=$('tg-'+key).checked;
  for(const key of nums)if($('tg-'+key))body[key]=Number($('tg-'+key).value);
  if($('tg-wsb_crowding_action'))body.wsb_crowding_action=$('tg-wsb_crowding_action').value;
  try{if($('tg-custom_market_events'))body.custom_market_events=parseEvents($('tg-custom_market_events').value);}
  catch(e){status(e.message);busy=false;return;}
  status('Saving…');
  try{
   const r=await fetch('/api/config',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const d=await r.json().catch(()=>({}));
   if(!r.ok||d.ok===false)throw Error(d.error||'Save failed');
   fill(d.config);status('Saved. The next scan uses these guards.');
  }catch(e){status(String((e&&e.message)||e));}
  finally{busy=false;}
 });
 load();
})();
