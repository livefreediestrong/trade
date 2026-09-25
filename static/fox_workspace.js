/* Current, read-only research view. The sole write action creates a local backup. */
(() => {
 'use strict';
 const money=value=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}):'Unknown';
 function tradingSummary(d,now=Date.now()){
  const age=(now-Date.parse(d?.as_of))/1000,a=d?.agent,b=d?.broker||{},brain=d?.brain||{};
  if(!a||a.ok!==true||!Number.isFinite(age)||age< -5||age>30)return {title:'Trading status unavailable',explanation:'Waiting for a fresh update. Do not rely on the previous reading.',account:'Account not currently verified',switch:'Unknown',connection:'Unknown',pnl:'Unknown',brain:'Unknown',checks:['Current trading status could not be confirmed.'],limits:'Saved limits are not currently verified.',tone:'attention'};
  const checks=[],on=a.enabled===true,active=on&&a.session_active===true&&a.mode==='auto_live';
  const identity=a.identity,verified=identity?.broker==='ibkr'&&typeof identity.account_id==='string'&&identity.account_id.length>0&&typeof identity.paper_mode==='boolean';
  const brokerFresh=Number.isFinite(b.age_sec)&&b.age_sec>=0&&b.age_sec+Math.max(0,age)<=30;
  const connected=brokerFresh&&b.connected===true,pnl=connected&&b.risk_ready===true&&typeof b.day_pnl==='number'&&Number.isFinite(b.day_pnl);
  const provider=String(brain.provider||brain.brain_mode||'AI').toUpperCase();
  if(!verified)checks.push('The broker account is not verified. Open broker connection checks.');
  if(!brokerFresh)checks.push('The broker reading is out of date. Waiting for a fresh account check.');
  else if(!connected)checks.push('Gateway is disconnected or the account could not be read. Open broker connection checks.');
  else if(!pnl)checks.push("Today's profit/loss is missing. Fox cannot check the daily loss limit, so new trades are blocked.");
  if(brain.selection_error)checks.push(brain.selection_error);
  else if(brain.configured!==true)checks.push(`${provider} is selected but is not set up. Choose a configured researcher in AI settings.`);
  else if(brain.brain_mode==='mock'||brain.provider==='mock')checks.push('Mock research cannot authorize live orders. Review the AI settings.');
  else if(brain.health?.state==='unavailable')checks.push(`${provider}'s latest request failed. Review the AI status below.`);
  if(!a.configured)checks.push('No saved agent settings. Review and save the limits in Start / pause controls.');
  if(on&&!active)checks.push('The agent switch is on, but the trading session or automatic mode is stopped. Review the controls.');
  if(active&&['blocked','error','budget','daily_limit','event_window','reconciling','discarded'].includes(a.phase))checks.push(a.message||'Fox is waiting for an operating check.');
  const attention=checks.length>0;
  if(a.market_open!==true)checks.push(a.market_open===false?'The market is closed. Fox waits for regular US stock-market hours.':'Market hours have not been verified.');
  if(!on&&a.configured)checks.push('Automatic trading is off. Review the account and saved limits in Start / pause controls.');
  const policy=a.policy||{},limitKeys=['max_order_usd','max_daily_loss_usd','max_orders_per_day'];
  const limitsValid=limitKeys.every(k=>typeof policy[k]==='number'&&Number.isFinite(policy[k])&&policy[k]>0);
  const limits=limitsValid?`Saved agent limits: ${money(policy.max_order_usd)} per order · ${money(policy.max_daily_loss_usd)} daily loss · ${policy.max_orders_per_day.toLocaleString('en-US')} order attempts per day.`:'Saved agent limits are incomplete or unavailable. Review the controls.';
  const largeLimits=limitsValid&&(policy.max_order_usd>=1e9||policy.max_daily_loss_usd>=1e9);
  if(largeLimits)checks.push('One or more agent money limits is $1 billion. Review these saved values before relying on the limits.');
  if(!limitsValid)checks.push('The saved agent limits could not be verified.');
  return {
   title:!a.configured?'Live trading needs setup':!on?'Automatic trading is off':!active?'On · session stopped':attention?'On · new trades blocked':a.market_open!==true?'On · waiting for market':largeLimits||!limitsValid?'On · review saved limits':'On · monitoring for a setup',
   explanation:active?'The switch is already on. Fox can send orders when the required checks pass; no second Start is needed.':'Use the controls below to review the account, limits and trading switch.',
   account:verified?`${identity.paper_mode?'Practice broker account':'Real-money broker account'} · IBKR · ending ${identity.account_id.slice(-4)}`:'Broker account not verified',
   switch:on?'On':'Off',connection:connected?'Connected':brokerFresh?'Not connected':'Needs fresh check',pnl:pnl?money(b.day_pnl):'Missing — new trades blocked',
   brain:brain.selection_error?`${provider} · unavailable`:brain.configured!==true?`${provider} · needs setup`:brain.brain_mode==='mock'||brain.provider==='mock'?'Mock · research only':brain.health?.state==='unavailable'?`${provider} · request failed`:`${provider} · configured`,
   checks:checks.length?checks:['No blocker reported in this summary. Quotes, available money, pending orders and all risk checks are checked again for every order.'],
   limits:limits+' Broker rules and other desk limits also apply.',tone:attention||largeLimits||!limitsValid?'attention':active?'waiting':'off'
  };
 }
 if(typeof module==='object'&&module.exports){module.exports={tradingSummary};return;}
 const $=id=>document.getElementById(id);if(!$('fox-workspace'))return;
 const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const set=(id,text)=>{$(id).textContent=text;};
 const stamp=v=>{const d=new Date(v);return Number.isNaN(d.getTime())?'Unknown time':d.toLocaleString([],{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET';};
 let timer,staleTimer,busy=false,active=true;
 function renderSummary(d){
  if(!$('fox-start-title'))return;
  const s=tradingSummary(d);
  for(const key of ['title','explanation','account','switch','connection','pnl','brain','limits'])set('fox-start-'+key,s[key]);
  $('fox-start-title').dataset.tone=s.tone;
  $('fox-start-checks').replaceChildren(...s.checks.map(text=>node('li',text)));
 }
 async function json(url,options={}){const c=new AbortController(),t=setTimeout(()=>c.abort(),options.method?180000:15000);try{const r=await fetch(url,{credentials:'same-origin',...options,signal:c.signal});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Status unavailable');return d;}finally{clearTimeout(t);}}
 function render(d){
  clearTimeout(staleTimer);renderSummary(d);staleTimer=setTimeout(()=>renderSummary(null),30000);
  set('fox-update','Updated '+stamp(d.as_of));
  const a=d.agent||{};set('fox-current',(a.enabled?'Authorized · ':'Paused · ')+(a.message||'Waiting for agent state'));
  const b=d.brain||{},h=b.health||{};
  set('fox-brain',`${b.provider||'Brain'} · ${b.model||'No model'} · `+(b.selection_error||(!b.configured?'Not configured':h.state==='healthy'?'Latest provider call succeeded':h.state==='unavailable'?'Latest provider call failed: '+h.error:h.state==='stale'?'Last provider result is older than 15 minutes':'No provider call observed in this process'))+(h.at?' · '+stamp(h.at*1000):''));
  const broker=d.broker||{};set('fox-broker',broker.risk_ready?'Account data and daily P&L verified at the latest broker check. Every order is checked again.':broker.error||'Current daily P&L/account evidence unavailable. New risk stays blocked.');
  const backup=d.backups||{};set('fox-backup',backup.last_error?backup.last_error+' · Previous snapshot retained from '+stamp(backup.verified_at):backup.ok?(backup.stale?'Backup overdue · ':'Verified when created · ')+stamp(backup.verified_at)+` · ${backup.files} stores`:(backup.error||'No verified state snapshot yet'));
  const rows=a.reviews||[];
  try{
   const saved=JSON.parse(sessionStorage.getItem('fox-review-seen-v1')||'null');
   const prev=saved?.scope===d.review_scope&&Array.isArray(saved.rows)?saved.rows:null;
   if(prev){const entered=rows.filter(r=>!prev.some(p=>p.ticker===r.ticker)).map(r=>r.ticker),left=prev.filter(p=>!rows.some(r=>r.ticker===p.ticker)).map(r=>r.ticker),changed=rows.filter(r=>prev.some(p=>p.ticker===r.ticker&&p.verdict!==r.verdict)).map(r=>r.ticker+' → '+r.verdict);const messages=[entered.length?'Entered: '+entered.join(', '):'',left.length?'Left this one-hour top-ten view: '+left.join(', ')+' (expiry or ranking; not a trade decision)':'',changed.length?'Assessment changed: '+changed.join(', '):''].filter(Boolean);if(messages.length)set('fox-review-changes',stamp(d.as_of)+' · '+messages.join(' · '));}
   sessionStorage.setItem('fox-review-seen-v1',JSON.stringify({scope:d.review_scope,rows:rows.map(r=>({ticker:r.ticker,verdict:r.verdict}))}));
  }catch(_){}
  $('fox-reviews').replaceChildren(...(rows.length?rows.map(r=>{const li=node('li','');li.append(node('strong',r.ticker+' · '+r.verdict),node('p',r.reason||'No reason supplied'),node('small',stamp(r.at)+' · '+Math.floor(r.age_sec/60)+' min ago'+(r.error?' · '+r.error:'')));const detail=r.detail;if(detail){for(const text of detail.missing||[])li.append(node('p',text));li.append(node('p','Reconsider when: '+detail.next_condition),node('small',detail.confidence_note));const levels=detail.invalidation||{};if(levels.stop!=null)li.append(node('p','Planned invalidation '+levels.stop+' · target '+(levels.target??'unavailable')+' · not a broker stop confirmation'));}else li.append(node('p','Detailed decision receipt was not retained for this older screen.'));return li;}):[node('li','No completed screens from this account in the last hour. Fox populates this list as authorized research runs.')]));
  const cal=d.calendar||{},sources=Object.entries(cal.status?.sources||{});
  set('fox-calendar-status',sources.length?sources.map(([name,s])=>name.toUpperCase()+': '+(s.ok?'connected':(s.fallback?s.fallback+' · ':'unavailable · ')+(s.error||'feed unavailable'))).join(' · '):'Calendar coverage has not been verified yet.');
  $('fox-calendar-days').replaceChildren(...(cal.days||[]).map(day=>{const section=node('section',''),ul=node('ul','');section.append(node('h4',day.label),ul);for(const e of day.events||[]){const li=node('li','');li.className='is-'+e.impact;li.append(node('time',e.clock+' · '+e.impact));if(/^https:\/\//i.test(e.url||'')){const a=node('a',e.title);a.href=e.url;a.target='_blank';a.rel='noopener noreferrer';li.append(a);}else li.append(node('span',e.title));if(e.schedule_snapshot){const small=node('small','Saved '+e.verified_on);small.className='saved';li.append(small);}ul.append(li);}if(!ul.children.length)ul.append(node('li','No events in available sources'));return section;}));
 }
 async function poll(){clearTimeout(timer);if(busy||document.hidden||!active)return;busy=true;try{render(await json('/api/fox-workspace'));}catch(e){clearTimeout(staleTimer);renderSummary(null);set('fox-update','Workspace unavailable; displayed details may be stale. '+e.message);set('fox-current','Current state unavailable');set('fox-broker','Current readiness unavailable; refresh required.');}finally{busy=false;if(active&&!document.hidden)timer=setTimeout(poll,20000);}}
 $('fox-backup-create').addEventListener('click',async()=>{const button=$('fox-backup-create');button.disabled=true;set('fox-backup','Capturing and verifying local state…');try{const d=await json('/api/backups',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});set('fox-backup','Verified '+stamp(d.verified_at)+' · '+d.files+' stores');}catch(e){set('fox-backup',e.message);}finally{button.disabled=false;}});
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)poll();else renderSummary(null);});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);clearTimeout(staleTimer);renderSummary(null);});window.addEventListener('pageshow',e=>{active=true;if(e.persisted)poll();});poll();
})();
