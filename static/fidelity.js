/* Fidelity portfolio panel: suggestions only; the desk never connects to or orders at Fidelity. */
(() => {
 'use strict';
 const usd=v=>typeof v==='number'&&Number.isFinite(v)?(v<0?'-':'')+'$'+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
 const pct=v=>typeof v==='number'&&Number.isFinite(v)?(v>0?'+':'')+v.toFixed(2)+'%':'—';
 const shares=v=>typeof v==='number'&&Number.isFinite(v)?(+v.toFixed(3)).toLocaleString(undefined,{maximumFractionDigits:3}):'—';
 const TAGS={add:'Buy setup',review:'Check this',hold:'Hold',unknown:'No reading'};
 if(typeof module==='object'&&module.exports){module.exports={usd,pct,shares,TAGS};return;}
 const $=id=>document.getElementById(id);
 if(!$('fidelity'))return;
 const status=$('fid-status'),runBtn=$('fid-run');
 let busy=false;
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 async function api(url,body){
  const r=await fetch(url,body===undefined?{credentials:'same-origin'}:{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  let d={};try{d=await r.json();}catch{}
  if(!r.ok||!d.ok)throw Error(d.error||'The desk could not read that');
  return d;
 }
 function fillSettings(s){for(const k of ['risk_pct','max_position_pct','stop_atr'])if($('fid-'+k)&&s)$('fid-'+k).value=s[k];}
 function sizeBlock(size){
  const box=el('div',null,'fid-size');
  if(!size||!(size.shares>0)){box.append(el('p',size&&size.why?'No size: '+size.why:'No size: not enough cash or room under your position limit.'));return box;}
  box.append(el('p',`Suggested size: ${shares(size.shares)} shares ≈ ${usd(size.dollars)} (about ${usd(size.risk_usd)} lost if the stop is hit)`));
  box.append(el('small',size.why));
  return box;
 }
 function holdingCard(h){
  const c=el('article',null,'fid-card');c.dataset.action=h.action||'unknown';
  const head=el('header');head.append(el('h4',h.ticker),el('span',h.headline||TAGS[h.action]||'','fid-tag'));c.append(head);
  const nums=el('p',null,'fid-nums');
  nums.append(document.createTextNode(`${shares(h.qty)} sh · ${usd(h.price)} · ${usd(h.value)}`+(h.weight_pct!=null?` · ${h.weight_pct}% of account`:'')));
  if(typeof h.gain_usd==='number'){const g=el('span',` · ${usd(h.gain_usd)} (${pct(h.gain_pct)}) since you bought`,h.gain_usd>=0?'fid-up':'fid-down');nums.append(g);}
  c.append(nums);
  c.append(el('p',h.detail||''));
  if(h.flags&&h.flags.length){const ul=el('ul');h.flags.forEach(f=>ul.append(el('li',f)));c.append(ul);}
  if(h.stop)c.append(el('p',`Protective stop idea: ${usd(h.stop)}`,'fid-nums'));
  if(h.action==='add')c.append(sizeBlock(h.size));
  return c;
 }
 function ideaCard(i){
  const c=el('article',null,'fid-card');c.dataset.action='add';
  const head=el('header');head.append(el('h4',i.ticker),el('span','Desk setup today','fid-tag'));c.append(head);
  c.append(el('p',`Signal price ${usd(i.price)} · stop ${usd(i.stop)}`+(i.target?` · target ${usd(i.target)}`:''),'fid-nums'));
  if(i.warning){const ul=el('ul');ul.append(el('li','Caution: '+i.warning));c.append(ul);}
  c.append(el('p','A same-day setup from the desk scanner. Prices move quickly; check the current quote in Fidelity first.'));
  c.append(sizeBlock(i.size));
  return c;
 }
 function renderEvidence(e){
  const box=$('fid-evidence');if(!e){box.hidden=true;return;}
  box.hidden=false;box.dataset.passed=String(!!e.passed);
  box.textContent=e.passed
   ?`The desk's buy rule has passed its evidence test (${e.calls} scored calls over ${e.sessions} days, average ${e.mean_bps} bps after costs). It is still a model, not a guarantee.`
   :`Treat buy setups with caution: the desk's buy rule has not yet shown it makes money. So far: ${e.calls??0} scored calls`+(e.sessions!=null?` over ${e.sessions} day(s)`:'')+(typeof e.mean_bps==='number'?`, average ${e.mean_bps} bps`:'')+`. It needs 60 calls over 10 days that beat costs.`;
 }
 function render(d){
  renderEvidence(d.evidence);
  const sum=$('fid-summary');sum.hidden=false;sum.replaceChildren();
  for(const [k,v] of [['Account',usd(d.account_value)],['Stocks',usd(d.holdings_value)],['Cash',usd(d.cash)]]){const s=el('span');s.append(el('b',k),document.createTextNode(v));sum.append(s);}
  $('fid-holdings').replaceChildren(...(d.holdings||[]).map(holdingCard));
  const ideas=d.ideas||[];$('fid-ideas-title').hidden=!ideas.length;$('fid-ideas').replaceChildren(...ideas.map(ideaCard));
  const when=new Date(d.checked_at);
  status.textContent=`Read ${d.priced} of ${(d.holdings||[]).length} holdings at ${Number.isFinite(when.getTime())?when.toLocaleTimeString():'just now'}.`+(d.unpriced&&d.unpriced.length?` No price for ${d.unpriced.join(', ')}.`:'')+(ideas.length?'':' No new desk buy setups today.');
 }
 async function run(force){
  if(busy)return;busy=true;runBtn.disabled=true;
  status.textContent='Reading prices, trend and earnings for each holding (about 3 seconds a stock)…';
  try{render(await api('/api/fidelity/suggest',{force}));}
  catch(e){status.textContent=e.message;}
  finally{busy=false;runBtn.disabled=false;}
 }
 async function load(){
  try{
   const d=await api('/api/fidelity');fillSettings(d.portfolio.settings);
   if(!d.has_holdings){status.textContent='No holdings yet. Open "Add or update holdings" above.';$('fid-import').open=true;runBtn.disabled=true;return;}
   const p=d.portfolio;status.textContent=`${p.positions.length} holding(s) and ${usd(p.cash)} cash saved`+(p.imported_at?` on ${new Date(p.imported_at).toLocaleDateString()}`:'')+'.';
   runBtn.disabled=false;run(false);
  }catch(e){status.textContent=e.message;}
 }
 $('fid-file').addEventListener('change',async ev=>{const f=ev.target.files&&ev.target.files[0];if(f)$('fid-text').value=await f.text();});
 $('fid-import-form').addEventListener('submit',async ev=>{
  ev.preventDefault();
  try{const d=await api('/api/fidelity/import',{text:$('fid-text').value});
   $('fid-text').value='';$('fid-import').open=false;
   status.textContent=`Saved ${d.portfolio.positions.length} holding(s) and ${usd(d.portfolio.cash)} cash.`+(d.skipped&&d.skipped.length?` Skipped: ${d.skipped.join(', ')}.`:'');
   runBtn.disabled=false;run(true);
  }catch(e){status.textContent=e.message;}
 });
 $('fid-clear').addEventListener('click',async()=>{
  if(!confirm('Remove all saved Fidelity holdings from the desk? (Nothing changes at Fidelity.)'))return;
  try{await api('/api/fidelity/clear',{});$('fid-holdings').replaceChildren();$('fid-ideas').replaceChildren();$('fid-summary').hidden=true;$('fid-evidence').hidden=true;$('fid-ideas-title').hidden=true;status.textContent='Removed.';runBtn.disabled=true;}
  catch(e){status.textContent=e.message;}
 });
 $('fid-settings').addEventListener('submit',async ev=>{
  ev.preventDefault();
  const body={};for(const k of ['risk_pct','max_position_pct','stop_atr'])body[k]=Number($('fid-'+k).value);
  try{const d=await api('/api/fidelity/settings',body);fillSettings(d.portfolio.settings);run(true);}
  catch(e){status.textContent=e.message;}
 });
 runBtn.addEventListener('click',()=>run(true));
 load();
})();
