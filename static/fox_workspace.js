/* Current, read-only research view. The sole write action creates a local backup. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('fox-workspace'))return;
 const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const set=(id,text)=>{$(id).textContent=text;};
 const stamp=v=>{const d=new Date(v);return Number.isNaN(d.getTime())?'Unknown time':d.toLocaleString([],{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET';};
 let timer,busy=false,active=true;
 async function json(url,options={}){const c=new AbortController(),t=setTimeout(()=>c.abort(),options.method?180000:15000);try{const r=await fetch(url,{credentials:'same-origin',...options,signal:c.signal});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Status unavailable');return d;}finally{clearTimeout(t);}}
 function render(d){
  set('fox-update','Updated '+stamp(d.as_of));
  const a=d.agent||{};set('fox-current',(a.enabled?'Authorized · ':'Paused · ')+(a.message||'Waiting for agent state'));
  const b=d.brain||{},h=b.health||{};
  set('fox-brain',`${b.provider||'Brain'} · ${b.model||'No model'} · `+(!b.configured?'Not configured':h.state==='healthy'?'Latest provider call succeeded':h.state==='unavailable'?'Latest provider call failed: '+h.error:h.state==='stale'?'Last provider result is older than 15 minutes':'No provider call observed in this process')+(h.at?' · '+stamp(h.at*1000):''));
  const broker=d.broker||{};set('fox-broker',broker.risk_ready?'Account data and daily P&L verified at the latest broker check. Every order is checked again.':broker.error||'Current daily P&L/account evidence unavailable. New risk stays blocked.');
  const backup=d.backups||{};set('fox-backup',backup.last_error?backup.last_error+' · Previous snapshot retained from '+stamp(backup.verified_at):backup.ok?(backup.stale?'Backup overdue · ':'Verified when created · ')+stamp(backup.verified_at)+` · ${backup.files} stores`:(backup.error||'No verified state snapshot yet'));
  const rows=a.reviews||[];
  $('fox-reviews').replaceChildren(...(rows.length?rows.map(r=>{const li=node('li','');li.append(node('strong',r.ticker+' · '+r.verdict),node('p',r.reason||'No reason supplied'),node('small',stamp(r.at)+' · '+Math.floor(r.age_sec/60)+' min ago'+(r.error?' · '+r.error:'')));return li;}):[node('li','No completed screens from this account in the last hour. Fox populates this list as authorized research runs.')]));
  const cal=d.calendar||{},sources=Object.entries(cal.status?.sources||{});
  set('fox-calendar-status',sources.length?sources.map(([name,s])=>name.toUpperCase()+': '+(s.ok?'connected':(s.fallback?s.fallback+' · ':'unavailable · ')+(s.error||'feed unavailable'))).join(' · '):'Calendar coverage has not been verified yet.');
  $('fox-calendar-days').replaceChildren(...(cal.days||[]).map(day=>{const section=node('section',''),ul=node('ul','');section.append(node('h4',day.label),ul);for(const e of day.events||[]){const li=node('li','');li.className='is-'+e.impact;li.append(node('time',e.clock+' · '+e.impact));if(/^https:\/\//i.test(e.url||'')){const a=node('a',e.title);a.href=e.url;a.target='_blank';a.rel='noopener noreferrer';li.append(a);}else li.append(node('span',e.title));if(e.schedule_snapshot){const small=node('small','Saved '+e.verified_on);small.className='saved';li.append(small);}ul.append(li);}if(!ul.children.length)ul.append(node('li','No events in available sources'));return section;}));
 }
 async function poll(){clearTimeout(timer);if(busy||document.hidden||!active)return;busy=true;try{render(await json('/api/fox-workspace'));}catch(e){set('fox-update','Workspace unavailable; displayed details may be stale. '+e.message);set('fox-current','Current state unavailable');set('fox-broker','Current readiness unavailable; refresh required.');}finally{busy=false;if(active&&!document.hidden)timer=setTimeout(poll,20000);}}
 $('fox-backup-create').addEventListener('click',async()=>{const button=$('fox-backup-create');button.disabled=true;set('fox-backup','Capturing and verifying local state…');try{const d=await json('/api/backups',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});set('fox-backup','Verified '+stamp(d.verified_at)+' · '+d.files+' stores');}catch(e){set('fox-backup',e.message);}finally{button.disabled=false;}});
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)poll();});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);});window.addEventListener('pageshow',e=>{active=true;if(e.persisted)poll();});poll();
})();
