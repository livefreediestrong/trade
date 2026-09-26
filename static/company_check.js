/* Company check panel: research only, never an order or a risk input. */
(() => {
 'use strict';
 const money=v=>{if(typeof v!=='number'||!Number.isFinite(v))return 'amount not given';const a=Math.abs(v);return (v<0?'-':'')+'$'+(a>=1e9?(a/1e9).toFixed(1)+'B':a>=1e6?(a/1e6).toFixed(1)+'M':a>=1e3?(a/1e3).toFixed(1)+'K':a.toFixed(0));};
 if(typeof module==='object'&&module.exports){module.exports={money};return;}
 const $=id=>document.getElementById(id);
 // Ticket page: link the typed symbol to its company check (opens Research; never trades).
 const ticketSymbol=$('live-ticket-symbol'),ticketLink=$('live-ticket-company');
 if(ticketSymbol&&ticketLink){const sync=()=>{const t=ticketSymbol.value.trim().toUpperCase();ticketLink.hidden=!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(t);ticketLink.href='/desk/research?company='+encodeURIComponent(t)+'#company-check';};ticketSymbol.addEventListener('input',sync);ticketSymbol.addEventListener('change',sync);sync();}
 if(!$('company-check'))return;
 const form=$('company-check-form'),input=$('company-check-ticker'),out=$('company-check-result');
 let busy=false,current='';
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 const link=(href,text)=>{const a=el('a',text);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;};
 function card(title,section,body){
  const a=el('article');a.append(el('h3',title));
  if(!section){a.append(el('p','Not checked.','cc-off'));return a;}
  if(!section.ok){a.append(el('p',section.error||'This source is unavailable right now.','cc-error'));return a;}
  a.append(el('p',section.summary||'',section.applies===false?'cc-summary cc-off':'cc-summary'));
  if(section.applies!==false&&body)body(a,section);
  if(section.note)a.append(el('p',section.note,'cc-note'));
  if(section.source&&section.applies!==false)a.append(el('p','Source: '+section.source,'cc-source'));
  return a;
 }
 function table(head,rows){
  const wrap=el('div',null,'cc-table-wrap'),t=el('table'),h=el('tr');head.forEach(x=>h.append(el('th',x)));t.append(h);
  rows.forEach(r=>{const tr=el('tr');r.forEach(c=>{const td=el('td');td.append(c instanceof Node?c:document.createTextNode(c??''));tr.append(td);});t.append(tr);});
  wrap.append(t);return wrap;
 }
 function render(d){
  const s=d.sections||{};
  out.replaceChildren(
   card('Bottom line',s.bottom_line,(a,x)=>{(x.items||[]).forEach(i=>{const row=el('div',null,'cc-item');row.dataset.tone=i.tone;row.append(el('b',i.label),el('span',i.value),el('p',i.plain));a.append(row);});}),
   card('Insiders (last '+((s.insiders||{}).lookback_days||90)+' days)',s.insiders,(a,x)=>{if(x.rows?.length)a.append(table(['Who','What','Shares','Value'],x.rows.map(r=>[r.name+' · '+r.role+' · '+r.date,r.action+(r.planned?' (pre-planned)':''),Math.round(r.shares).toLocaleString(),r.url?link(r.url,money(r.value)):money(r.value)])));}),
   card('Federal contracts (12 months)',s.contracts,(a,x)=>{if(x.new_awards?.length){a.append(el('p','Biggest new awards:'));a.append(table(['When','Agency','For','Amount'],x.new_awards.map(r=>[r.date,r.agency,r.what,r.url?link(r.url,money(r.amount)):money(r.amount)])));}if(x.matched_names?.length)a.append(el('p','Matched recipients: '+x.matched_names.join(', '),'cc-note'));}),
   card('Lobbying',s.lobbying,(a,x)=>{if(x.firms?.length)a.append(el('p','Firms hired: '+x.firms.join(', '),'cc-note'));}),
   card('Congress trades',s.congress,(a,x)=>{if(x.rows?.length)a.append(table(['Member','Did','Size','Traded → reported'],x.rows.map(r=>[r.who+(r.party?' ('+r.party[0]+')':''),r.action,r.range||'',r.traded+' → '+r.reported+' ('+r.delay_days+' days)'])));})
  );
  out.hidden=false;$('company-check-refresh').hidden=false;
  $('company-check-status').textContent=(d.name||d.ticker)+' · checked '+new Date(d.checked_at).toLocaleString()+(d.cached?' (saved copy)':'')+'. '+(d.note||'');
 }
 async function run(ticker,refresh){
  ticker=String(ticker||'').trim().toUpperCase();if(!ticker||busy)return;busy=true;current=ticker;
  $('company-check-go').disabled=$('company-check-refresh').disabled=true;
  $('company-check-status').textContent='Checking '+ticker+'… reading five public sources, this can take about 20 seconds.';
  const c=new AbortController(),timer=setTimeout(()=>c.abort(),90000);
  try{
   const r=await fetch('/api/research/company?ticker='+encodeURIComponent(ticker)+(refresh?'&refresh=1':''),{signal:c.signal,credentials:'same-origin'}),d=await r.json();
   if(!r.ok||!d.ok)throw Error(d.error||'Company check failed');
   if(current===ticker)render(d);
  }catch(e){$('company-check-status').textContent=e.name==='AbortError'?'The sources took too long. Try again in a minute.':e.message;}
  finally{clearTimeout(timer);busy=false;$('company-check-go').disabled=$('company-check-refresh').disabled=false;}
 }
 form.addEventListener('submit',e=>{e.preventDefault();run(input.value,false);});
 $('company-check-refresh').addEventListener('click',()=>run(current||input.value,true));
 const asked=new URLSearchParams(location.search).get('company');
 if(asked){input.value=asked.toUpperCase();run(asked,false);}
})();
