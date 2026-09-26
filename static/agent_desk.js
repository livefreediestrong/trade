/* Evidence summaries and truthful live status. This controller never sends mutations. */
(() => {
 'use strict';
 const finite=n=>typeof n==='number'&&Number.isFinite(n);
 const money=n=>finite(n)?n.toLocaleString('en-US',{style:'currency',currency:'USD'}):'Unknown';
 function liveView(state,settings,age){
  const cfg=settings||{},broker=state?.broker||{},book=state?.broker_book||{},mode=state?.config?.mode||cfg.mode;
  const session=state?.config?.session_active??cfg.session_active,automatic=mode==='auto_live',blocks=[];
  if(!state||age>25000)blocks.push('Status is stale or unavailable. Refresh before relying on these readings.');
  if(broker.connected!==true||book.ok!==true)blocks.push('Gateway connection and account are not verified.');
  if(broker.paper_mode!==false||book.paper_mode!==false)blocks.push('A real-money account has not been verified.');
  if(book.risk_ready!==true||!finite(book.day_pnl_usd))blocks.push('Daily P&L or account risk data is missing. New live risk is blocked.');
  if(state?.loop?.rth_ok!==true||state?.loop?.outside_rth===true)blocks.push('Regular-market trading is closed or unverified.');
  const agent=cfg.live_agent,ks=agent?{armed:true,max_daily_loss_usd:agent.policy?.max_daily_loss_usd,max_trades_per_day:agent.policy?.max_orders_per_day,max_position_size_usd:agent.policy?.max_order_usd}:cfg.kill_switch||{},limitsOn=ks.armed===true;
  if(!settings)blocks.push('Configured broker limits have not been loaded.');
  else if(!limitsOn||![ks.max_daily_loss_usd,ks.max_trades_per_day,ks.max_position_size_usd].every(n=>finite(n)&&n>0))blocks.push('All three explicit broker loss, count and size limits are not enabled.');
  if(agent&&!agent.enabled)blocks.push('Fox (the broker agent) is paused. Its saved policy does not authorize orders.');
  const title=agent?agent.enabled&&automatic&&session?'Fox agent enabled':'Fox agent paused':automatic?(session?'Automatic mode · session active':'Automatic mode · session stopped'):'Manual activation required';
  return {title,mode:mode==='live_manual'?'Approve each live order':automatic?'Automatic broker orders':mode||'Unknown',session:session?'Session active':'Session stopped',
   broker:broker.connected===true&&book.ok===true?(book.paper_mode===false?'Live account connected':'Broker paper / unverified'):'Disconnected / unverified',
   pnl:'Daily P&L '+money(book.day_pnl_usd),limits:settings?`${limitsOn?'Enabled':'Disabled · saved'}: ${money(ks.max_position_size_usd)} / position · ${money(ks.max_daily_loss_usd)} daily loss · ${ks.max_trades_per_day??'?'} trades`:'Configured limits unavailable',
   blocks,next:blocks.length?'Resolve the displayed checks before considering activation.':automatic?'Selected mode can submit eligible orders while its session is active. Per-order checks still apply.':'Current account/session checks passed. Broker-paper qualification and your manual activation are separate.'};
 }
 if(typeof module==='object'&&module.exports){module.exports={liveView};return;}
 const $=id=>document.getElementById(id);if(!$('live-automation'))return;
 const set=(id,text)=>{if($(id)&&$(id).textContent!==text)$(id).textContent=text;};
 let state=null,settings=null,received=0,settingsReceived=0,timer;
 function live(){
  const v=liveView(state,Date.now()-settingsReceived<45000?settings:null,Date.now()-received);
  for(const key of ['mode','session','broker','pnl','limits','next'])set('live-auto-'+key,v[key]);set('live-auto-state',v.title);
  $('live-auto-blockers').replaceChildren(...v.blocks.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
 }
 window.addEventListener('desk:state',e=>{state=e.detail;received=Date.now();live();});
 window.addEventListener('moss:review',e=>{
  const s=e.detail;settings=s.execution_settings;settingsReceived=Date.now();live();
  const review=s.agent_review||{},r=review.latest||{},p=r.paper||{},c=r.conclusion||{},w=s.paper_workday||{};
  set('agent-review-state',review.error?'Review needs attention':c.state?.replaceAll('_',' ')||'Awaiting first review');
  set('agent-review-activity',w.busy?'Reviewing candidates':w.active?(s.market_open?'Paper research running':'Waiting for market open'):s.settings.enabled?'Daily notebook enabled':'Daily notebook paused');
  set('agent-review-routine',`${w.today?.candidates_checked||0} candidates checked today · ${w.today?.paper_fills||0} paper fills. Evidence review every five minutes.`);
  set('agent-review-title',c.title||'No saved conclusion yet');set('agent-review-conclusion',c.text||'The next automatic review will read the retained evidence.');set('agent-review-next',c.next||'');
  set('agent-review-sample',`${p.outcomes||0} qualified outcomes · ${p.session_days||0} days`);
  const groups=r.execution_quality?.groups||[],actual=groups.filter(g=>g.scope==='live');
  set('agent-review-executions',r.created_at?`${actual.reduce((n,g)=>n+g.executions,0)} verified live fills analyzed · ${actual.reduce((n,g)=>n+g.benchmark_coverage,0)} with arrival-price benchmarks.`:'No saved execution review yet.');
  set('agent-review-stamp',r.created_at?`Conclusion saved ${new Date(r.created_at).toLocaleString()} · evidence ${r.evidence_id?.slice(0,12)||'unknown'}`:'No saved review yet.');
  set('agent-review-error',review.error||'');
 });
 function watch(){clearTimeout(timer);if(!document.hidden){live();timer=setTimeout(watch,5000);}}
 document.addEventListener('visibilitychange',watch);window.addEventListener('pagehide',()=>clearTimeout(timer));watch();
})();
