/* Read-only broker evidence. No activation, replacement or cancellation controls. */
(() => {
 'use strict';
 function view(report, now=Date.now()){
  const age=now-Date.parse(report?.checked_at);
  if(report?.ok!==true)return {fresh:false,summary:report?.summary||'Waiting for a broker protection check.',rows:[]};
  if(typeof report.checked_at!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(report.checked_at)||!Number.isFinite(age)||age<0||age>45000)return {fresh:false,summary:'Protection records are old. Waiting for a fresh broker check.',rows:[]};
  return {fresh:true,summary:report.summary,rows:Array.isArray(report.rows)?report.rows:[]};
 }
 function commitmentView(report,now=Date.now()){
  const checked=view(report,now),c=report?.commitments;
  if(!checked.fresh||c?.ok!==true)return {available:false,summary:checked.fresh?(c?.summary||'Account commitments are not available yet.'):'Waiting for a fresh broker read. Missing information does not mean there are no commitments.',groups:[],totals:[],orders:[]};
  const money=(v,currency)=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<1e100&&/^[A-Z]{3}$/.test(currency)?currency+' '+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'Amount unknown';
  const asset=v=>({STK:'Stocks and funds',OPT:'Options',FOP:'Futures options',FUT:'Futures',CASH:'Currency positions',BOND:'Bonds',CFD:'Contracts for difference',BAG:'Combined orders'}[v]||v||'Unknown asset');
  const groups=(Array.isArray(c.groups)?c.groups:[]).map(g=>`${g.currency} · ${asset(g.asset_type)}: ${g.long_count} owned; ${g.short_count} sold short.`);
  const totals=(Array.isArray(c.buy_limits)?c.buy_limits:[]).map(t=>money(t.amount,t.currency)+` across ${t.order_count} standalone limit ${t.order_count===1?'buy':'buys'}, before fees`);
  if(!totals.length)totals.push(c.orders?.length?'No standalone buy subtotal can be shown from these orders.':'No unfinished orders were returned by this broker read.');
  if(c.unknown_buy_count)totals.push(`${c.unknown_buy_count} ${c.unknown_buy_count===1?'buy has':'buys have'} unknown cost and ${c.unknown_buy_count===1?'is':'are'} excluded.`);
  if(c.contingent_buy_count)totals.push(`${c.contingent_buy_count} linked, conditional or held ${c.contingent_buy_count===1?'buy is':'buys are'} excluded; review the related orders together.`);
  if(c.sell_order_count)totals.push(`${c.sell_order_count} unfinished ${c.sell_order_count===1?'sale is':'sales are'} listed below. Expected proceeds are not counted as available cash.`);
  if(c.short_option_count)groups.push(`${c.short_option_count} short option ${c.short_option_count===1?'holding can':'holdings can'} create an exercise or assignment obligation. Premium value does not measure that obligation.`);
  const orders=(Array.isArray(c.orders)?c.orders:[]).map(o=>({title:`${o.instrument} · ${o.side} · ${o.remaining_qty} remaining · ${asset(o.asset_type)} · ${o.currency}`,
    text:(o.limit_amount!==null?money(o.limit_amount,o.currency)+' estimated limit cost. ':'')+(o.note||'Cost is unverified.')}));
  return {available:true,summary:c.summary,groups,totals,orders,note:c.note};
 }
 if(typeof module==='object'&&module.exports){module.exports={view,commitmentView};return;}
 const $=id=>document.getElementById(id),panel=$('broker-safety');if(!panel)return;
 let report=null,busy=false,controller=null,timer=null,epoch=0,scope=null;
 const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 function draw(){
  const v=view(report);$('broker-safety-summary').textContent=v.summary;
  $('broker-safety-time').textContent=v.fresh?'Checked '+new Date(report.checked_at).toLocaleString():'';
  $('broker-safety-rows').replaceChildren(...v.rows.map(row=>{
   const li=node('li','');li.append(node('strong',`${row.instrument||row.symbol} · ${row.quantity} ${row.asset_type==='OPT'?'contracts':'units'}`));
   for(const text of row.notes||[])li.append(node('p',text));return li;
  }));
  const details=$('broker-commitment-body');if(details){
   const c=commitmentView(report);details.replaceChildren(node('p',c.summary));
   if(c.available){
    details.append(node('h5','Holdings returned by the broker'));
    for(const line of c.groups)details.append(node('p',line));
    if(!c.groups.length)details.append(node('p','No held instruments were returned.'));
    details.append(node('small','Long means owned; short means sold with an obligation to buy back or deliver. A holding count is not its dollar risk.'));
    details.append(node('h5','Estimated cost of unfinished buys'));
    for(const line of c.totals)details.append(node('p',line));
    const list=node('ul','');for(const row of c.orders){const li=node('li','');li.append(node('strong',row.title),node('p',row.text));list.append(li);}details.append(list,node('small',c.note||''));
   }
  }
 }
 async function refresh(){
  if(document.hidden||busy)return;
  busy=true;const version=epoch;controller=new AbortController();const timeout=setTimeout(()=>controller?.abort(),35000);
  try{const r=await fetch('/api/broker-safety',{cache:'no-store',signal:controller.signal}),value=await r.json();if(version===epoch)report=value;}
  catch(e){if(version===epoch)report={ok:false,summary:'The broker check did not complete. Holdings and protection are unknown.'};}
  finally{clearTimeout(timeout);busy=false;controller=null;draw();}
 }
 function start(){clearInterval(timer);timer=setInterval(()=>{draw();refresh();},30000);refresh();}
 window.addEventListener('desk:state',e=>{
  const s=e.detail||{},key=JSON.stringify([s.broker?.broker,s.broker?.endpoint,s.broker_book?.account_id,s.broker?.connected]);
  if(key!==scope){scope=key;epoch++;controller?.abort();report=null;draw();}
  if(s.broker?.connected===false){report={ok:false,summary:'Gateway is disconnected. Holdings and protection are unknown.'};draw();}
 });
 window.addEventListener('pagehide',()=>{epoch++;controller?.abort();clearInterval(timer);});
 window.addEventListener('pageshow',e=>{if(e.persisted)start();});
 document.addEventListener('visibilitychange',()=>{draw();if(!document.hidden)refresh();});
 draw();start();
})();
