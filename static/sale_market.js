/* Durable broad-market research, separate from execution and Moss's paper loop. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('sale-market-filters'))return;
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const number=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{maximumFractionDigits:2}):'Unavailable';
 let page=1,rows=[],timer,loading=false,active=true,sequence=0,selected=null,filters=new URLSearchParams(new FormData($('sale-market-filters')));
 async function request(data){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
   const response=await fetch('/api/markets/scanner'+(data?'':'?'+new URLSearchParams([...filters,['page',String(page)]])),{signal:controller.signal,...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
   const value=await response.json();if(!response.ok||!value.ok)throw Error(value.error||'Market scan unavailable');return value;
  }catch(e){if(e.name==='AbortError')throw Error('Scanner response timed out. Refresh status before repeating a control action');throw e;}
  finally{clearTimeout(timeout);}
 }
 function inspect(row){
  selected=row.symbol;
  $('sale-market-detail').innerHTML=`<h4>${esc(row.symbol)} · ${esc(row.name||row.listing_name)}</h4><div class="sale-context-grid"><p>Close <strong>$${number(row.close)}</strong><br>60-session median $${number(row.typical_60_session_close)}<br>${number(row.below_observed_high_pct)}% below observed high over ${number(row.observed_sessions)} sessions</p><p>20-session return <strong>${number(row.return_20_sessions_pct)}%</strong><br>Prior 20-session average turnover $${number(row.avg_daily_dollar_volume)}<br>Completed-day relative volume ${number(row.completed_session_relative_volume)}×</p><p>${esc(row.buyers_returning)}<br>Recent adjusted closing low $${number(row.invalidation_reference)}</p></div><p class="source-meta">${esc(row.source)} · session ${esc(row.as_of)} · retrieved ${esc(row.retrieved_at)}</p><p class="source-meta">Annualized realized volatility (20 sessions): ${number(row.realized_volatility_20_sessions_pct)}% · Last session move: ${number(row.return_1_session_pct)}%</p><div class="sale-toolbar"><button type="button" class="btn sm" data-scan-use="chart">Charts &amp; news</button><button type="button" class="btn sm" data-scan-use="options">Options research</button><button type="button" class="btn sm" data-scan-use="cost">Estimate costs</button><button type="button" class="btn sm" data-scan-use="chat">Ask about this stock</button></div><p>Company health and the reason for the decline still need dated filings and news. This historical pullback does not establish fair value.</p>`;
 }
 function render(d){
  rows=d.results;page=d.page;
  for(const node of document.querySelectorAll('[data-scanner-context]'))node.textContent=`${number(d.valid)} / ${number(d.total)} valid histories · ${d.as_of||'not scanned'} · ${d.stale?'dated':d.state}`;
  $('sale-discount-filter').hidden=d.lens!=='pullbacks';
  const running=['running','initializing','pausing'].includes(d.state), resumable=['paused','partial'].includes(d.state)&&d.total>0&&!d.stale;
  $('sale-market-state').textContent=d.stale?'Dated results':d.state.replaceAll('_',' ');
  $('sale-market-message').textContent=d.message+(d.current_symbol?' · '+d.current_symbol:'');
  $('sale-market-start').disabled=running;$('sale-market-start').textContent=d.total?'Start new scan':'Scan US market';
  $('sale-market-pause').hidden=!running;$('sale-market-pause').disabled=d.state==='pausing';$('sale-market-resume').hidden=!resumable;
  $('sale-market-progress').value=d.total?100*d.checked/d.total:0;
  $('sale-market-coverage').innerHTML=[['Checked',`${number(d.checked)} / ${number(d.total)}`],['Valid history',number(d.valid)],['Excluded',number(d.excluded)],['Provider gaps',number(d.failed)]].map(([label,value])=>`<span>${esc(label)}<strong>${esc(value)}</strong></span>`).join('');
  $('sale-market-date').textContent=`Completed session: ${d.as_of||'not scanned'} · Directory: ${d.directory_at?new Date(d.directory_at).toLocaleString():'not loaded'} · Valid-history coverage: ${number(d.coverage_pct)}%${d.directory_error?' · Directory refresh failed; retained listing snapshot':''}`;
  $('sale-market-warning').textContent=(d.stale?`A newer session (${d.latest_completed_session}) is available. `:'')+(d.pending||d.failed?'Partial coverage. Rankings include only successfully checked listings.':'Directory pass complete; excluded listings have no usable ranking.')+' Lower than typical is not verified fair value.';
  if(!d.total)$('sale-market-warning').textContent='Scan the full supported US directory. Results appear as each listing is checked.';
  const issues=filters.get('issues')==='1';
  const metric={pullbacks:['Below typical','below_typical_pct','%'],momentum:['20-session return','return_20_sessions_pct','%'],active:['Relative volume','completed_session_relative_volume','×'],all:['20-session return','return_20_sessions_pct','%']}[d.lens];
  const heads=issues?['Listing','Coverage gap','Reason']:['Listing','Close',metric[0],'Below high','Avg. daily $ volume','Type'];
  $('sale-market-results').innerHTML=rows.length?`<table><thead><tr>${heads.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map((r,i)=>issues?`<tr><td>${esc(r.symbol)}</td><td>${r.state==='provider_error'?'Provider failure':'Excluded'}</td><td>${esc(r.error)}</td></tr>`:`<tr><td><button class="sale-symbol" type="button" data-sale-row="${i}" aria-pressed="${r.symbol===selected}">${esc(r.symbol)}</button><small>${esc(r.name||r.listing_name)}</small></td><td>$${number(r.close)}</td><td>${number(r[metric[1]])}${metric[2]}</td><td>${number(r.below_observed_high_pct)}%</td><td>$${number(r.avg_daily_dollar_volume)}</td><td>${r.etf?'ETF':'Stock'}</td></tr>`).join('')}</tbody></table>`:`<p class="muted">${d.valid?'No listings match these filters in the checked results.':'No matching results yet. Coverage counts above show what has been checked.'}</p>`;
  $('sale-market-page').textContent=`${number(d.matched)} matches · page ${page} of ${Math.max(1,Math.ceil(d.matched/d.page_size))}`;
  $('sale-market-prev').disabled=page<=1;$('sale-market-next').disabled=page*d.page_size>=d.matched;
  const current=rows.find(r=>r.symbol===selected);if(current&&!issues)inspect(current);
 }
 async function refresh(){
  if(loading||!active||document.hidden)return;loading=true;const version=sequence;
  try{const d=await request();if(active&&!document.hidden&&version===sequence)render(d);}catch(e){$('sale-market-message').textContent=e.message+' · Saved results may be out of date.';}finally{loading=false;if(version!==sequence&&active&&!document.hidden)refresh();}
 }
 async function control(action,button){
  button.disabled=true;sequence++;
  try{await request({action});page=1;button.disabled=false;await refresh();}catch(e){button.disabled=false;$('sale-market-message').textContent=e.message;}
 }
 for(const action of ['start','resume','pause'])$('sale-market-'+action).addEventListener('click',e=>control(action,e.currentTarget));
 function applyFilters(){filters=new URLSearchParams(new FormData($('sale-market-filters')));page=1;sequence++;refresh();}
 $('sale-market-filters').addEventListener('submit',e=>{e.preventDefault();applyFilters();});
 $('sale-market-filters').addEventListener('change',e=>{if(e.target.type==='radio'||e.target.type==='checkbox')applyFilters();});
 $('sale-market-prev').addEventListener('click',()=>{page--;sequence++;refresh();});
 $('sale-market-next').addEventListener('click',()=>{page++;sequence++;refresh();});
 $('sale-market-results').addEventListener('click',e=>{const button=e.target.closest('[data-sale-row]');if(!button)return;const row=rows[Number(button.dataset.saleRow)];if(row){inspect(row);for(const b of $('sale-market-results').querySelectorAll('[data-sale-row]'))b.setAttribute('aria-pressed',String(b===button));}});
 function jump(id){const node=$(id);if(node){node.scrollIntoView({block:'start'});node.setAttribute('tabindex','-1');node.focus({preventScroll:true});}}
 $('sale-market-detail').addEventListener('click',e=>{
  const action=e.target.closest('[data-scan-use]')?.dataset.scanUse;if(!action||!selected)return;
  if(action==='chart'){window.dispatchEvent(new CustomEvent('desk:instrument',{detail:{id:selected}}));jump('symbol-workspace');}
  if(action==='options'&&$('option-symbol')){window.dispatchEvent(new CustomEvent('scanner:options',{detail:{symbol:selected}}));jump('desk-options');}
  if(action==='cost'&&$('cost-symbol')){$('cost-symbol').value=selected;$('cost-symbol').dispatchEvent(new Event('input',{bubbles:true}));jump('cost-planner');}
  if(action==='chat'&&$('chat-ticker')){$('chat-ticker').value=selected;jump('chat-panel');}
 });
 function schedule(){clearInterval(timer);if(active&&!document.hidden){refresh();timer=setInterval(refresh,8000);}}
 document.addEventListener('visibilitychange',schedule);window.addEventListener('pagehide',()=>{active=false;clearInterval(timer);});window.addEventListener('pageshow',()=>{active=true;schedule();});schedule();
})();
