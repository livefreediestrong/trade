/* Watchlist alert settings: notification only, never an order or a risk input. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('watch-alerts'))return;
 let busy=false;
 function render(d){
  const s=d.settings||{};
  $('wa-enabled').checked=!!s.enabled;$('wa-news').checked=!!s.news;
  $('wa-move_pct').value=s.move_pct;$('wa-earnings_days').value=s.earnings_days;
  $('wa-status').textContent=(s.enabled?'Alerts on':'Alerts off')+(d.last_run?' · last checked '+new Date(d.last_run).toLocaleTimeString()+(Number.isFinite(d.symbols)?' across '+d.symbols+' stocks':''):' · not checked yet this session')+(d.last_error?' · '+d.last_error:'')+'.';
  const items=(d.history||[]).map(h=>{const li=document.createElement('li');li.textContent=new Date(h.at).toLocaleString()+' · '+h.message;if(h.url){const a=document.createElement('a');a.href=h.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=' source ↗';li.append(a);}return li;});
  if(!items.length){const li=document.createElement('li');li.className='cc-off';li.textContent='None yet.';items.push(li);}
  $('wa-history').replaceChildren(...items);
 }
 async function call(url,options){
  const c=new AbortController(),t=setTimeout(()=>c.abort(),60000);
  try{const r=await fetch(url,{...options,signal:c.signal,credentials:'same-origin'}),d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Alert settings unavailable');return d;}
  finally{clearTimeout(t);}
 }
 async function act(url,options,message){
  if(busy)return;busy=true;$('wa-status').textContent=message;
  try{render(await call(url,options));}catch(e){$('wa-status').textContent=e.name==='AbortError'?'The check took too long; it will keep running in the background.':e.message;}
  finally{busy=false;}
 }
 $('watch-alerts-form').addEventListener('submit',e=>{
  e.preventDefault();
  act('/api/watch-alerts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:$('wa-enabled').checked,news:$('wa-news').checked,move_pct:Number($('wa-move_pct').value),earnings_days:Number($('wa-earnings_days').value)})},'Saving…');
 });
 $('wa-check').addEventListener('click',()=>act('/api/watch-alerts/check',{method:'POST'},'Checking prices, earnings dates and headlines…'));
 act('/api/watch-alerts',{},'Reading alert settings…');
})();
