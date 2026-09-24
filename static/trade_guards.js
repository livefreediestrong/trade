/* Entry and profit guards: desk-wide settings for new entries. Never places orders. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),form=$('trade-guards-form');if(!form)return;
 const nums=['min_net_reward_risk','evidence_min_samples','giveback_stop_pct'];
 let busy=false;
 const status=text=>{$('tg-status').textContent=text;};
 function fill(cfg){
  if(!cfg||typeof cfg!=='object')return;
  for(const key of nums){const el=$('tg-'+key);if(el&&document.activeElement!==el&&cfg[key]!=null)el.value=cfg[key];}
  $('tg-evidence_gate_enabled').checked=cfg.evidence_gate_enabled!==false;
 }
 async function load(){
  try{const r=await fetch('/api/config',{credentials:'same-origin'}),d=await r.json();if(!r.ok)throw Error(d.error||'unavailable');fill(d.config);}
  catch(e){status('Could not read the saved guards; reload the page to try again.');}
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;
  const body={evidence_gate_enabled:$('tg-evidence_gate_enabled').checked};
  for(const key of nums)body[key]=Number($('tg-'+key).value);
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
