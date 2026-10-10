/* Historical research only. Requests are scoped to the local study archive. */
(() => {
 'use strict';
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const pct=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:2,minimumFractionDigits:2})+'%':'Unavailable';
 const money=v=>finite(v)?v.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}):'Unavailable';
 const labels={overnight:'Close to next open',intraday:'Open to close',buy_and_hold:'Buy and hold'};
 function rows(result,cost){
  if(result?.available!==true)return [];
  const scenario=result.scenarios?.find(s=>s.cost_bps_per_side===cost);
  if(!scenario)return [];
  return Object.entries(labels).map(([key,label])=>{const m=scenario[key]||{};return [label,pct(m.compounded_pct),money(m.reinvested_pnl),money(m.fixed_dollar_pnl),pct(m.drawdown_pct)];});
 }
 if(typeof module==='object'&&module.exports){module.exports={rows,pct,money};return;}
 const $=id=>document.getElementById(id),panel=$('overnight-study');if(!panel)return;
 const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const form=$('overnight-form'),status=$('overnight-status'),saved=$('overnight-saved'),results=$('overnight-results');
 let studies=[],busy=false,controller=null;
 const today=new Date().toLocaleDateString('en-CA',{timeZone:'America/New_York'});
 const end=new Date(today+'T12:00:00Z');end.setUTCDate(end.getUTCDate()-1);
 const start=new Date(end);start.setUTCDate(start.getUTCDate()-90);
 $('overnight-start').value=start.toISOString().slice(0,10)<'2025-01-10'?'2025-01-10':start.toISOString().slice(0,10);
 $('overnight-end').value=end.toISOString().slice(0,10);
 $('overnight-end').max=today;$('overnight-start').max=today;
 function table(caption,headers,data){
  const wrap=node('div','');wrap.className='overnight-scroll';wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',caption);
  const t=node('table','');t.append(node('caption',caption));const head=node('thead',''),tr=node('tr','');
  for(const text of headers){const th=node('th',text);th.scope='col';tr.append(th);}head.append(tr);t.append(head);
  const body=node('tbody','');for(const row of data){const r=node('tr','');row.forEach((text,i)=>{const c=node(i===0?'th':'td',text);if(i===0)c.scope='row';r.append(c);});body.append(r);}t.append(body);wrap.append(t);return wrap;
 }
 function draw(){
  results.replaceChildren();const study=studies.find(s=>s.id===saved.value);if(!study)return;
  const p=study.parameters;
  const info=node('p',`Saved ${new Date(study.created_at).toLocaleString()} · ${p.start} through ${p.end} · hypothetical ${money(p.capital)} · ${pct(p.fill_pct)} invested each time · ${pct(p.cost_bps/100)} cost on each side.`);info.className='overnight-note';results.append(info);
  const download=node('a','Download frozen prices, assumptions and checked results');download.href='/api/research/overnight/'+encodeURIComponent(study.id);results.append(download);
  for(const result of study.results||[]){
   const card=node('article','');card.className='overnight-card';card.append(node('h4',result.symbol));
   if(result.source)card.append(node('p',`${result.source} · retrieved ${new Date(result.retrieved_at).toLocaleString()} · ${result.adjustment_events} provider-reported adjustment ${result.adjustment_events===1?'event':'events'} retained.`));
   if(!result.available){card.append(node('p',result.reason||'Source data are unavailable. No return is estimated.'));if(result.missing_dates?.length)card.append(node('p','Missing dates: '+result.missing_dates.join(', ')));results.append(card);continue;}
   card.append(node('p',`${result.intervals} completed sessions · previous close ${result.start} to final close ${result.end}. The two price components reconcile before costs.`));
   card.append(table('Your cost assumption',['Holding period','Reinvested return','Reinvested gain/loss','Same amount each trade','Largest drop from peak'],rows(result,p.cost_bps)));
   card.append(node('p','Drawdown is the largest drop from a prior peak at interval endpoints. It can miss losses between those measurements. Dollar gains and losses use hypothetical money.'));
   const base=result.scenarios.find(s=>s.cost_bps_per_side===p.cost_bps);
   card.append(node('p','Worst single overnight: '+pct(base?.overnight?.worst_interval_pct)+'. Buy-and-hold includes only one purchase and one sale.'));
   const details=node('details','');details.append(node('summary','Costs, earlier/later periods and unusually strong days'));
   details.append(table('Reinvested return with different costs',['Cost on each side','Close to next open','Open to close','Buy and hold'],result.scenarios.map(s=>[pct(s.cost_bps_per_side/100),pct(s.overnight?.compounded_pct),pct(s.intraday?.compounded_pct),pct(s.buy_and_hold?.compounded_pct)])));
   for(const sample of result.retrospective_samples||[])details.append(node('p',`${sample.label}: ${sample.overnight.samples} sessions, ${sample.start||'—'} to ${sample.end||'—'}; overnight return ${sample.overnight.samples?pct(sample.overnight.compounded_pct):'unavailable'}.`));
   details.append(node('p',result.without_best_five_overnights?`If the five best overnight returns are removed, the remaining overnight return is ${pct(result.without_best_five_overnights.compounded_pct)}. Removed dates: ${result.best_five_dates.join(', ')}. This is a sensitivity check, not a tradable rule.`:'More than five sessions are needed for the best-days sensitivity check.'));
   details.append(node('p',result.earnings_attribution));card.append(details);results.append(card);
  }
 }
 function setStudies(items,selected){
  studies=items;saved.replaceChildren(...items.map(s=>{const o=node('option',`${new Date(s.created_at).toLocaleString()} · ${s.parameters.symbols.join(', ')} · ${s.parameters.start}–${s.parameters.end}`);o.value=s.id;return o;}));
  if(selected&&items.some(s=>s.id===selected))saved.value=selected;
  $('overnight-saved-label').hidden=!items.length;draw();
 }
 async function request(url,options={}){
  controller=new AbortController();const current=controller,timer=setTimeout(()=>current.abort(),45000);
  try {const response=await fetch(url,{cache:'no-store',...options,signal:current.signal}),data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||'The comparison could not be read.');return data;}
  finally {clearTimeout(timer);if(controller===current)controller=null;}
 }
 async function load(){
  busy=true;form.querySelector('button').disabled=true;
  try{const data=await request('/api/research/overnight');setStudies(data.studies||[]);status.textContent=studies.length?`${studies.length} saved comparisons. Choose one to inspect its original assumptions.`:'Choose your assumptions, then run a comparison. Nothing runs automatically.';}
  catch(e){status.textContent='Saved comparisons unavailable: '+e.message;}
  finally{busy=false;form.querySelector('button').disabled=false;}
 }
 form.addEventListener('submit',async e=>{
  e.preventDefault();if(busy)return;busy=true;
  const controls=[...form.elements];controls.forEach(c=>c.disabled=true);status.textContent='Reading dated prices and saving the comparison…';
  try{
   const data=await request('/api/research/overnight',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols:$('overnight-symbols').value.split(/[\s,]+/).filter(Boolean),start:$('overnight-start').value,end:$('overnight-end').value,capital:Number($('overnight-capital').value),cost_bps:Number($('overnight-cost').value)*100,fill_pct:Number($('overnight-fill').value)})});
   setStudies([data.study,...studies.filter(s=>s.id!==data.study.id)],data.study.id);
   const available=data.study.results.filter(r=>r.available).length;
   status.textContent=`${data.reused?'Opened the recent identical comparison':'Comparison saved'}: ${available} of ${data.study.results.length} symbols have complete data. Research only; trading settings are unchanged.`;
  }catch(e){status.textContent='The request did not complete: '+e.message+' Previously saved results below keep their original dates. Reload to check whether this request was saved before retrying.';}
  finally{busy=false;controls.forEach(c=>c.disabled=false);}
 });
 saved.addEventListener('change',draw);
 window.addEventListener('pagehide',()=>controller?.abort());
 window.addEventListener('pageshow',e=>{if(e.persisted&&!busy)load();});
 load();
})();
