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
 let busy=false,current='',revision=0,controller=null;
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
 const stamp=value=>{if(!value)return 'timestamp unavailable';const d=new Date(value);return Number.isFinite(d.getTime())&&/(?:Z|[+-]\d\d:\d\d)$/i.test(value)?d.toLocaleString(undefined,{timeZoneName:'short'}):String(value);};
 const link=(href,text)=>{try{const u=new URL(href);if(u.protocol!=='https:'||u.username||u.password||u.port||!['www.sec.gov','www.usaspending.gov','lda.gov'].includes(u.hostname))return el('span',text);}catch{return el('span',text);}const a=el('a',text);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;};
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
 function insiderDetails(a,x){
  a.className='cc-insiders';
  const cluster=x.cluster||{};
  const clusterText=cluster.status==='observed'?`${cluster.people} distinct reporting owners bought within the latest ${cluster.window_days} days.`:
   cluster.status==='needs_review'?'Cluster assessment unavailable: coverage or amendments need review.':'No qualifying cluster observed in the filings read.';
  a.append(el('p',clusterText,'cc-insider-signal'));
  if(cluster.method)a.append(el('p',cluster.method,'cc-note'));
  const sums=el('div',null,'cc-insider-totals');
  for(const [label,value] of [['Purchases',x.buys],['Sales',x.sells]]){
   const amount=x.amendments?'withheld for amendment review':value?.value==null?'unknown total; '+money(value?.known_value)+' priced subtotal':money(value.value);
   sums.append(el('p',`${label}: ${amount}. Rows without a usable price: ${value?.unpriced_rows||0}. Rows with joint or unidentified owners: ${value?.ambiguous_owner_rows||0}.`));
  }
  a.append(sums);
  a.append(el('p',`Rows in filings marking a trading plan: ${x.flagged_plan_rows||0}. Rows with no checkbox data: ${x.unknown_plan_rows||0}. Rows needing data review: ${x.issue_rows||0}. A filing-level checkbox may not apply to every row.`,'cc-note'));
  const controls=el('div',null,'cc-insider-controls');
  const typeLabel=el('label','Show transactions '),type=el('select');type.id='cc-insider-type';
  type.setAttribute('aria-label','Show transactions');
  for(const [value,label] of [['all','All types'],['P','Purchases'],['S','Sales'],['other','Grants, tax, gifts and other']]){const o=el('option',label);o.value=value;type.append(o);}
  typeLabel.append(type);
  const sortLabel=el('label','Order by '),sort=el('select');sort.id='cc-insider-sort';
  sort.setAttribute('aria-label','Order by');
  for(const [value,label] of [['filed','Newest filing'],['value','Largest reported value']]){const o=el('option',label);o.value=value;sort.append(o);}sortLabel.append(sort);
  const officerLabel=el('label'),officer=el('input');officer.type='checkbox';officer.id='cc-insider-officers';officerLabel.append(officer,document.createTextNode(' Only company officers'));
  controls.append(typeLabel,sortLabel,officerLabel);a.append(controls);
  const count=el('p',null,'cc-note'),list=el('div',null,'cc-insider-list');count.setAttribute('role','status');a.append(count,list);
  const renderRows=()=>{
   const selected=(x.rows||[]).filter(r=>(type.value==='all'||(type.value==='other'?!['P','S'].includes(r.code):r.code===type.value))&&(!officer.checked||r.officer));
   selected.sort((a,b)=>sort.value==='value'?(b.value??-1)-(a.value??-1):String(b.filed||'').localeCompare(String(a.filed||''))||String(b.date||'').localeCompare(String(a.date||'')));
   count.textContent=`Showing ${selected.length} of ${(x.rows||[]).length} loaded rows${x.rows_truncated?`; only the newest 100 of ${x.row_count} rows were loaded`:''}. Filters apply to this sample.`;
   list.replaceChildren();
   for(const r of selected){
    const row=el('section',null,'cc-insider-row');row.append(el('h4',`${r.name} · ${r.role}`));
    row.append(el('p',`${r.amended?'AMENDED · ':''}${r.code||'?'} · ${r.action} · Estimated value: ${money(r.value)}`));
    row.append(el('p',`Traded ${r.date||'unknown'} → filed ${r.filed||'unknown'}${Number.isFinite(r.delay_days)?` · ${r.delay_days} calendar days later`:''}. SEC accepted: ${stamp(r.accepted_at)}.`));
    row.append(el('p',`${r.security||'Security title unavailable'} · ${r.shares==null?'unknown shares':r.shares.toLocaleString()+' shares'} · ${r.ownership==='D'?'direct':r.ownership==='I'?'indirect':'unknown'} ownership${r.joint?' · joint filing; not counted as independent buyers':''}.`));
    row.append(el('p',`Holdings after: ${r.shares_after==null?'not supplied':r.shares_after.toLocaleString()+' shares'}. Holding change: ${r.holding_change_pct==null?'not calculated':r.holding_change_pct+'% (single-row estimate)'}. ${r.plan_label||'Plan status unavailable.'}`,'cc-note'));
    if(r.issues?.length)row.append(el('p','Data needs review: '+r.issues.join('; '),'cc-error'));
    if(r.url)row.append(link(r.url,'Read original SEC filing'));
    const notes=[...(r.footnotes||[]),...(r.filing_notes||[])].filter((n,i,all)=>all.findIndex(x=>x.id===n.id)===i);
    if(notes.length){const detail=el('details');detail.append(el('summary','Filing notes and plan context'));for(const n of notes)detail.append(el('p',`${n.id}: ${n.text}`));if(r.notes_truncated)detail.append(el('p','Some notes were shortened; open the original filing.'));row.append(detail);}
    list.append(row);
   }
  };
  type.value='all';sort.value='filed';type.addEventListener('change',renderRows);sort.addEventListener('change',renderRows);officer.addEventListener('change',renderRows);renderRows();
 }
 function lobbyingDetails(a,x){
  a.className='cc-lobbying';
  a.append(el('p','Combined company spending: not calculated. Firm income can overlap with organization expenses.','cc-note'));
  a.append(el('p',`Latest reports in this sample: ${x.latest_reports||0}. Filing rows needing review: ${x.review_rows||0}. Duplicate copies removed: ${x.duplicate_rows||0}.`,'cc-note'));
  if(x.top_issues?.length)a.append(el('p','Issues in the latest readable sample: '+x.top_issues.join(', '),'cc-note'));
  for(const warning of x.warnings||[])a.append(el('p',warning,'cc-error'));
  for(const c of x.coverage||[])a.append(el('p',`${c.year}: ${c.rows_read} source rows read across ${c.pages_read} page(s); ${c.source_count??'unknown'} reported by the source. ${c.all_search_pages_read?'All returned search pages read; issuer links remain unverified.':'Partial coverage — more records or revisions may be missing.'}`,'cc-note'));
  if(x.excluded_names?.length){const d=el('details');d.append(el('summary','Different client names excluded'));for(const name of x.excluded_names)d.append(el('p',name));a.append(d);}
  if(x.records?.length){
   const d=el('details'),list=el('div',null,'cc-filing-list');d.append(el('summary',`Read ${x.records.length} filing record(s), amounts and revisions`),list);
   for(const r of x.records){
    const row=el('section',null,'cc-insider-row');row.append(el('h4',`${r.registrant||'Registrant unknown'} · ${r.year} ${String(r.period||'period unknown').replaceAll('_',' ')}`));
    const status=r.status==='latest_in_sample'?'Latest in this sample':r.status==='older_in_sample'?'Older version in this sample — do not add again':'Versions or data need review';
    row.append(el('p',`${status}. ${r.filing_type_label||'Type unknown'}${r.no_activity?' · marked no activity':''}. Posted: ${stamp(r.posted_at)}.`));
    row.append(el('p',`Client: ${r.client||'unknown'} · public client ID: ${r.client_id??'not supplied'} · registrant ID: ${r.registrant_id??'not supplied'}. Company link unverified.`,'cc-note'));
    const kind={firm_income:'Firm income',organization_expenses:'Organization expenses',not_given:'Amount',needs_review:'Amount needing review'}[r.amount_kind]||'Amount';
    row.append(el('p',`${kind}: ${money(r.amount)}${r.amount===0?' (check the original for a below-threshold amount)':''}.${r.expense_method?' Expense reporting method: '+r.expense_method.toUpperCase()+'.':''}`));
    if(r.amount_kind==='needs_review')row.append(el('p',`Separate API fields: income ${money(r.income)}; expenses ${money(r.expenses)}. No combined amount.`,'cc-note'));
    if(r.issue_areas?.length)row.append(el('p','Issues: '+r.issue_areas.join(', '),'cc-note'));
    for(const issue of r.issues||[])row.append(el('p',issue,'cc-error'));
    if(r.url)row.append(link(r.url,'Read original lobbying filing'));
    list.append(row);
   }
   a.append(d);
  }
  if(x.retrieved_at)a.append(el('p','Source retrieved: '+stamp(x.retrieved_at),'cc-note'));
 }
 function render(d){
  const s=d.sections||{};
  out.replaceChildren(
   card('Bottom line',s.bottom_line,(a,x)=>{(x.items||[]).forEach(i=>{const row=el('div',null,'cc-item');row.dataset.tone=i.tone;row.append(el('b',i.label),el('span',i.value),el('p',i.plain));a.append(row);});}),
   card('Insider disclosures (last '+((s.insiders||{}).lookback_days||90)+' days)',s.insiders,insiderDetails),
   card('Federal contracts — identity check',s.contracts,(a,x)=>{
    a.append(el('p','Verified company total: unknown.','cc-note'));
    if(x.matched_names?.length)a.append(el('p','Possible same-name recipients: '+x.matched_names.join(', '),'cc-note'));
    if(x.candidate_obligations_12m!=null)a.append(el('p','Reported obligations for possible matches in this sample: '+money(x.candidate_obligations_12m)+'. Company link unverified.'));
    if(x.new_awards?.length){a.append(el('p','Possible same-name awards — verify each recipient:'));a.append(table(['When','Recipient / public ID','Agency','For','Reported amount'],x.new_awards.map(r=>[r.date,(r.recipient||'Name unavailable')+' · UEI: '+(r.recipient_uei||'not supplied'),r.agency,r.what,r.url?link(r.url,money(r.amount)):money(r.amount)])));}
    if(x.excluded_names?.length){const details=el('details');details.append(el('summary','Different names excluded from the company results'));for(const name of x.excluded_names)details.append(el('p',name));a.append(details);}
    const c=x.coverage;if(c)a.append(el('p',`Records read: ${c.recipient_rows_read} recipient groups and ${c.award_rows_read} awards. ${c.recipient_has_more||c.award_has_more?'More source results were not read.':'This bounded name search does not establish complete company coverage.'}`,'cc-note'));
   }),
   card('Lobbying — filing review',s.lobbying,lobbyingDetails),
   card('Congress trades',s.congress,(a,x)=>{if(x.rows?.length)a.append(table(['Member','Did','Size','Traded → reported'],x.rows.map(r=>[r.who+(r.party?' ('+r.party[0]+')':''),r.action,r.range||'',r.traded+' → '+r.reported+' ('+r.delay_days+' days)'])));})
  );
  let talk=$('company-talk');if(!talk){talk=el('button','Discuss this company with the companions');talk.id='company-talk';talk.type='button';out.before(talk);talk.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('desk:chat',{detail:{ticker:current}})));}talk.textContent='Discuss '+d.ticker+' with the companions';
  out.hidden=false;$('company-check-refresh').hidden=false;
  $('company-check-status').textContent=(d.name||d.ticker)+' · checked '+new Date(d.checked_at).toLocaleString()+(d.cached?' (saved copy)':'')+'. '+(d.note||'');
 }
 async function run(ticker,refresh,announce=true){
  ticker=String(ticker||'').trim().toUpperCase();if(!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker))return;busy=true;current=ticker;input.value=ticker;
  controller?.abort();const sequence=++revision;
  out.hidden=true;if($('company-talk'))$('company-talk').hidden=true;
  if(announce)window.dispatchEvent(new CustomEvent('desk:research-symbol',{detail:{ticker,source:'company'}}));
  $('company-check-go').disabled=$('company-check-refresh').disabled=true;
  $('company-check-status').textContent='Checking '+ticker+'… reading five public sources, this can take about 20 seconds.';
  const c=new AbortController();controller=c;const timer=setTimeout(()=>c.abort(),90000);
  try{
   const r=await fetch('/api/research/company?ticker='+encodeURIComponent(ticker)+(refresh?'&refresh=1':''),{signal:c.signal,credentials:'same-origin'}),d=await r.json();
   if(!r.ok||!d.ok)throw Error(d.error||'Company check failed');
   if(sequence===revision){render(d);if($('company-talk'))$('company-talk').hidden=false;}
  }catch(e){if(sequence===revision)$('company-check-status').textContent=e.name==='AbortError'?'The sources took too long. Try again in a minute.':e.message;}
  finally{clearTimeout(timer);if(sequence===revision){busy=false;$('company-check-go').disabled=$('company-check-refresh').disabled=false;}}
 }
 form.addEventListener('submit',e=>{e.preventDefault();run(input.value,false);});
 $('company-check-refresh').addEventListener('click',()=>run(current||input.value,true));
 window.addEventListener('desk:research-symbol',e=>{if(e.detail?.source!=='company')run(e.detail?.ticker,false,false);});
 const asked=new URLSearchParams(location.search).get('company');
 if(asked){input.value=asked.toUpperCase();run(asked,false);}
})();
