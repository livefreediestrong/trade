/* Three dated priorities plus descriptive concentration; display only. */
(() => {
 'use strict';
 const num=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
 function accountFunds(book,now=Date.now()){
  const observation=book?.funds_observation,stamp=observation?.read_at;
  const dated=typeof stamp==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(stamp),age=dated?now-Date.parse(stamp):NaN;
  const available=book?.ok===true&&book.currency==='USD'&&observation?.currency==='USD'&&Number.isFinite(age)&&age>=0&&age<=45000;
  const labels={cash:'Total cash',settled_cash:'Settled cash',buying_power:'Buying power',available_funds:'Available funds',excess_liquidity:'Margin cushion (excess liquidity)'};
  const states={missing:'Not reported',unavailable:'Value unavailable',conflicting:'Conflicting values',currency_unverified:'USD value unverified',unreadable:'Unreadable balance'};
  return {available,readAt:available?stamp:null,rows:Object.entries(labels).map(([key,label])=>{
   const state=observation?.field_status?.[key],value=num(book?.[key]);
   return {label,text:available&&state==='reported'&&value!==null?value.toLocaleString('en-US',{style:'currency',currency:'USD'}):available?(states[state]||'Not verified'):'Waiting for an updated account read'};
  })};
 }
 function concentration(book){
  if(book?.ok!==true||num(book.equity)===null||book.equity<=0||book.currency!=='USD')return {available:false,rows:[],missing:null};
  let missing=0;const grouped=new Map();
  for(const p of book.positions||[]){if(p.mark_is_cost_basis||num(p.last)===null||p.last<=0||num(p.shares)===null){missing++;continue;}const value=Math.abs(p.shares*p.last);if(!Number.isFinite(value)){missing++;continue;}const k=String(p.ticker||'Unknown');grouped.set(k,(grouped.get(k)||0)+value);}
  return {available:true,missing,rows:[...grouped].map(([ticker,value])=>({ticker,value,percent:value/book.equity*100})).sort((a,b)=>b.value-a.value)};
 }
 function relevantNews(rows,symbols,now=Date.now()){
  const seen=new Set(),out=[];
  for(const row of rows||[]){const ts=Number(row.published_ts)*1000;if(!row.fresh||!Number.isFinite(ts)||ts>now||now-ts>36*3600000)continue;const title=String(row.title||''),key=title.toLowerCase().replace(/[^a-z0-9]/g,'');if(!key||seen.has(key))continue;seen.add(key);
   const matches=[...symbols].filter(s=>new RegExp('(?:\\$|\\b)'+s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b','i').test(title)&&(!['A','I','ON','IT','ALL','FOR','NOW'].includes(s)||title.includes('$'+s)));
   if(matches.length)out.push({...row,matches});
  }return out.sort((a,b)=>b.published_ts-a.published_ts);
 }
 if(typeof module==='object'&&module.exports){module.exports={concentration,relevantNews,accountFunds};return;}
 const $=id=>document.getElementById(id),node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text??'';return n;};let desk=null,day=null,news=[],last=0;
 const time=t=>Number.isFinite(Date.parse(t))?new Date(t).toLocaleString():'Date unavailable';
 function draw(){const box=$('daily-brief-items');if(box){const current=Date.now()-last<45000,focus=new Set([...(desk?.config?.watchlist||[]),...(desk?.broker_book?.positions||[]).map(p=>p.ticker)]),story=relevantNews(news,focus)[0],event=day?.events?.upcoming?.[0];box.replaceChildren();
   const rows=[['Fox’s latest recorded action',current?(day?.fox?.recent?.[0]?.text||day?.fox?.headline||'No recorded action available.'):'Current desk activity unavailable.',day?.fox?.recent?.[0]?.at||day?.as_of,null],
    ['News mentioning your watchlist or holdings',story?story.title:'No fresh headline with an exact symbol match. This does not mean there is no relevant news.',story?.published_at,story?.url],
    ['Next event in available calendars',current?(event?.when||event?.title||'No dated event available. Check calendar source coverage before assuming a quiet day.'):'Calendar status needs a refresh.',day?.as_of,event?.url]];
   rows.forEach(([title,text,at,url])=>{const a=node('article');a.append(node('h3',title),node('p',text),node('small',(title.startsWith('News')?'Published · ':'Desk checked · ')+time(at)));try{const u=new URL(url);if(u.protocol==='https:'&&!u.username&&!u.password){const l=node('a','Read source');l.href=u.href;l.target='_blank';l.rel='noopener noreferrer';a.append(l);}}catch(_){}box.append(a);});
  }
  const positions=$('concentration-rows');if(positions){const c=concentration(desk?.broker_book),recent=Date.now()-last<45000,limit=Number($('concentration-limit').value);positions.replaceChildren();if(!recent||!c.available)positions.append(node('p','Current account values unavailable. Concentration cannot be calculated.'));else if(!c.rows.length)positions.append(node('p',c.missing?'Price marks are missing; weights are unavailable.':'No US stock holdings in this snapshot.'));else for(const r of c.rows){const line=node('p',r.ticker+': '+r.percent.toFixed(1)+'% of account value'+(Number.isFinite(limit)&&limit>0&&r.percent>limit?' · above your display threshold':''));positions.append(line);}if(c.missing)positions.append(node('p',c.missing+' holdings omitted because a current broker price mark is missing.'));}
  const funds=$('concentration-funds');if(funds){const report=accountFunds(Date.now()-last<45000?desk?.broker_book:null);funds.replaceChildren();for(const row of report.rows)funds.append(node('p',row.label+': '+row.text));funds.append(node('small',report.available?'Account cache read · '+time(report.readAt)+'. IBKR did not attach a value-update time to these balances.':'Current balance details are unavailable. This does not mean the balances are zero.'));}
 }
 window.addEventListener('desk:state',e=>{desk=e.detail;last=Date.now();draw();});window.addEventListener('desk:day',e=>{day=e.detail;draw();});window.addEventListener('desk:headlines',e=>{news=e.detail?.items||[];draw();});window.addEventListener('desk:day-unavailable',()=>{day=null;draw();});
 const limit=$('concentration-limit');if(limit){try{const saved=Number(localStorage.getItem('desk_concentration_display_v1'));if(saved>=1&&saved<=100)limit.value=saved;}catch(_){}limit.addEventListener('input',()=>{if(limit.checkValidity()){try{localStorage.setItem('desk_concentration_display_v1',limit.value);}catch(_){}draw();}});}setInterval(()=>{if(!document.hidden)draw();},15000);
})();
