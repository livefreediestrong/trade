/* Strategy scorecard: display only; reads /api/strategy-scorecard. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('strategy-scorecard'))return;
 const pct=v=>typeof v==='number'&&Number.isFinite(v)?Math.round(v*100)+'%':'—';
 const bps=v=>typeof v==='number'&&Number.isFinite(v)?(v>0?'+':'')+v.toFixed(1)+' bps':'—';
 let busy=false;
 function cell(text,title){const td=document.createElement('td');td.textContent=text;if(title)td.title=title;return td;}
 function render(d){
  $('scorecard-status').textContent=d.summary;
  $('scorecard-note').textContent=d.note||'';
  const rows=(d.rows||[]).map(r=>{
   const tr=document.createElement('tr');tr.dataset.status=r.status;
   tr.append(cell(r.label),cell(String(r.scored)),cell(pct(r.win_rate)),cell(bps(r.avg_net_bps),r.example||''),cell(r.plain));
   return tr;
  });
  $('scorecard-rows').replaceChildren(...rows);$('scorecard-table').hidden=!rows.length;
 }
 async function load(){
  if(busy)return;busy=true;
  const c=new AbortController(),t=setTimeout(()=>c.abort(),15000);
  try{const r=await fetch('/api/strategy-scorecard?days='+encodeURIComponent($('scorecard-days').value),{signal:c.signal,credentials:'same-origin'}),d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Scorecard unavailable');render(d);}
  catch(e){$('scorecard-status').textContent='Scorecard unavailable: '+(e.name==='AbortError'?'timed out':e.message);$('scorecard-table').hidden=true;}
  finally{clearTimeout(t);busy=false;}
 }
 $('scorecard-days').addEventListener('change',load);
 load();
})();
