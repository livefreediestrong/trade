/* Fox's buy-rule history test: reads and starts the test; never places orders. */
(() => {
 'use strict';
 const bps=v=>typeof v==='number'&&Number.isFinite(v)?(v>0?'+':'')+v.toFixed(1)+' bps':'—';
 const LABELS={before_costs:'Before any costs',zero_commission:'With $0 commission (slippage only)',your_costs:'At your costs today',random_entry_before_costs:'Buying at random, before costs (for comparison)'};
 const dollars=(v,usd)=>typeof v==='number'&&Number.isFinite(v)?` ≈ ${v<0?'-':''}$${Math.abs(v*usd/1e4).toFixed(2)} per $${usd} trade`:'';
 if(typeof module==='object'&&module.exports){module.exports={bps,LABELS,dollars};return;}
 const $=id=>document.getElementById(id);if(!$('rule-history'))return;
 let timer=null;
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 function render(d){
  const g=d.gate||{},r=d.result,job=d.job||{};
  $('rh-verdict').textContent=g.plain||'No history test yet.';
  $('rh-verdict').dataset.state=g.state||'missing';
  const box=$('rh-lines');
  if(r&&r.summary){
   box.hidden=false;box.replaceChildren();
   for(const [k,label] of Object.entries(LABELS)){
    const l=(r.summary.lines||{})[k]||{};const row=el('div',null,'rh-line');
    row.append(el('span',label),el('strong',l.unknown?'unknown':bps(l.avg_bps)+' a trade'),
     el('small',l.unknown?'':`${(l.trades||0).toLocaleString()} trades · won ${Math.round((l.win_rate||0)*100)}%`+(l.avg_bps!=null?dollars(l.avg_bps,100):'')));
    row.dataset.good=String((l.avg_bps||0)>0);box.append(row);
   }
  }else box.hidden=true;
  const busy=!!job.running;$('rh-run').disabled=busy;
  $('rh-status').textContent=busy?(job.message||'Running…')+(job.total?` ${job.progress||0}/${job.total}`:''):(r?`Last run ${new Date(r.finished_at).toLocaleString()}. Takes about 3 minutes.`:'Takes about 3 minutes.');
  clearTimeout(timer);if(busy)timer=setTimeout(load,3000);
 }
 async function call(method){
  const r=await fetch('/api/live-agent/history-test',{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:method==='POST'?'{}':undefined});
  const d=await r.json().catch(()=>({}));if(!r.ok||!d.ok)throw Error(d.error||'History test unavailable');return d;
 }
 async function load(){try{render(await call('GET'));}catch(e){$('rh-status').textContent=e.message;}}
 async function live(){
  try{const s=await fetch('/api/live-agent',{credentials:'same-origin'}).then(r=>r.json());const e=s.entry_evidence||{};
   $('rh-live').textContent=`Live record so far: ${e.calls??0} of 60 scored buy calls, ${e.sessions??0} of 10 trading days`+(typeof e.mean_bps==='number'?`, averaging ${bps(e.mean_bps)}`:'')+'.';}catch{}
 }
 $('rh-run').addEventListener('click',async()=>{$('rh-run').disabled=true;try{render(await call('POST'));}catch(e){$('rh-status').textContent=e.message;$('rh-run').disabled=false;}});
 load();live();
})();
