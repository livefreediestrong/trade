/* Today at the desk: Fox (broker agent) and Changing Woman (chores and reasoning). Read-only. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);
 if(!$('desk-day')&&!$('sidebar-companions'))return;
 const POLL_MS=20000;
 let timer=null,busy=false,active=true;
 const STATE={off:'Off duty',resting:'Resting',waiting:'Waiting for the open',holding:'Holding off',researching:'Researching',
  reconciling:'Reconciling',done:'Done for today',blocked:'Stuck',watching:'On watch'};
 const CHORE={done:'✓',working:'…',attention:'!',off:'–'};
 let followedHash='';
 function sourceRow(node,kind,key){if(key){node.id='dd-'+kind+'-'+encodeURIComponent(String(key));node.tabIndex=-1;}return node;}
 function followSource(){
  const hash=typeof location==='undefined'?'':location.hash;
  if(!hash||hash===followedHash)return;
  let id;try{id=decodeURIComponent(hash.slice(1));}catch(_){return;}
  if(!/^dd-(note-|chore-|move-|fox-headline$|woman-headline$)/.test(id))return;
  const target=$(id);if(!target)return;
  followedHash=hash;target.tabIndex=-1;target.scrollIntoView?.({block:'center',behavior:'instant'});target.focus?.({preventScroll:true});
 }
 function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n;}
 function link(text,url){if(!/^https:\/\//i.test(String(url||'')))return el('span',text);const a=el('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
 const money=v=>Number.isFinite(Number(v))?'$'+Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
 const clock=v=>{const d=new Date(v);return isNaN(d)?'':d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});};
 function set(id,text){const n=$(id);if(n)n.textContent=text;}
 function fill(id,nodes,empty){const n=$(id);if(!n)return;n.replaceChildren(...(nodes.length?nodes:[el('li',empty,'dd-empty')]));}
 function render(d){
  const chip=$('bx-agent-chip');
  if(chip){chip.textContent=STATE[d.fox?.state]||'Unknown';chip.className='bx-agent-chip '+(['blocked','off'].includes(d.fox?.state)?'is-blocked':['resting','done'].includes(d.fox?.state)?'is-paused':d.fox?.state==='researching'?'is-scanning':'is-waiting');}
  set('bx-agent-detail',d.fox?.headline||'Agent status unavailable');
  if(!$('desk-day'))return;
  const fox=d.fox||{},woman=d.woman||{};
  set('dd-state',STATE[fox.state]||'Unknown');
  $('dd-state').dataset.state=fox.state||'';
  set('dd-fox-headline',fox.headline||'');
  const managed=fox.managed||[];
  $('dd-positions-table').hidden=!managed.length;
  $('dd-positions').replaceChildren(...managed.map(m=>{const tr=el('tr');tr.append(el('td',m.ticker),el('td',m.shares),el('td',money(m.entry)),el('td',money(m.stop)+(m.breakeven?' (entry)':'')),el('td',money(m.target)));return tr;}));
  fill('dd-fox-recent',(fox.recent||[]).map(r=>{const li=sourceRow(el('li',null,'dd-'+r.kind),'move',r.id);li.append(el('time',clock(r.at)),document.createTextNode(' '+r.text));return li;}),'Nothing yet today.');
  set('dd-woman-headline',woman.headline||'');
  fill('dd-notes',(woman.reasoning||[]).map(n=>sourceRow(el('li',n.text,'dd-note is-'+n.level),'note',n.key)),'Nothing to flag right now.');
  fill('dd-chores',(woman.chores||[]).map(c=>{const li=sourceRow(el('li',null,'dd-chore is-'+c.state),'chore',c.key);li.append(el('span',CHORE[c.state]||'·','dd-mark'),el('strong',c.label),el('small',c.detail));return li;}),'');
  const ev=d.events||{};
  fill('dd-events',(ev.upcoming||[]).map(e=>{const li=el('li',null,'dd-event is-'+e.impact);li.append(el('span',e.impact,'dd-impact'),' ',link(e.when||e.title,e.url));if(e.guard)li.append(el('small',' · Fox pauses new entries '+e.guard));return li;}),'No medium or high impact events in the next 36 hours.');
  const bad=Object.entries((ev.status||{}).sources||{}).filter(([,s])=>!s.ok).map(([k,s])=>k+' ('+(s.error||'unavailable')+')');
  set('dd-events-note',(ev.guard_enabled?'Event pause is on.':'Event pause is off (Settings).')+(bad.length?' Unavailable: '+bad.join(', ')+'.':''));
  const w=d.wsb||{};
  const rows=(w.top||[]).slice(0,6).map(r=>{const tr=el('tr');const lean=r.bull_share==null?'—':r.bull_share>=.5?Math.round(r.bull_share*100)+'% bull':Math.round((1-r.bull_share)*100)+'% bear';
   tr.append(el('td','$'+r.ticker),el('td',r.mentions_60m+(r.live_chat_pasted?' +'+r.live_chat_pasted+' chat':'')),el('td',r.velocity==null?'building':r.velocity+'×'),el('td',lean));return tr;});
  $('dd-wsb').replaceChildren(...(rows.length?rows:[(()=>{const tr=el('tr'),td=el('td',w.configured?'No ticker mentions in the last hour yet.':'Reddit is not connected.');td.colSpan=4;tr.append(td);return tr;})()]));
  const threads=(w.threads||[]).map(t=>t.label).join(', ');
  set('dd-wsb-note',w.configured?((threads?'Reading '+threads+'. ':'')+(w.error?w.error+'. ':'')+(w.live_chat_note||'')):'Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to read the WSB threads. '+(w.live_chat_note||''));
  followSource();
 }
 async function poll(){
  clearTimeout(timer);if(!active||document.hidden||busy)return;busy=true;
  const controller=new AbortController(),stop=setTimeout(()=>controller.abort(),15000);
  try{
   const r=await fetch('/api/desk-day',{credentials:'same-origin',signal:controller.signal}),d=await r.json();
   if(!r.ok||!d.ok)throw Error(d.error||'Desk day unavailable');
   render(d);window.dispatchEvent(new CustomEvent('desk:day',{detail:d}));
  }catch(e){set('dd-state','Unavailable');set('dd-fox-headline',String((e&&e.message)||'Desk day unavailable'));set('bx-agent-chip','Status unavailable');set('bx-agent-detail','Current agent status could not be read.');if($('bx-agent-chip'))$('bx-agent-chip').className='bx-agent-chip is-blocked';window.dispatchEvent(new CustomEvent('desk:day-unavailable'));}
  finally{clearTimeout(stop);busy=false;if(active&&!document.hidden)timer=setTimeout(poll,POLL_MS);}
 }
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)poll();});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);});
 window.addEventListener('pageshow',e=>{active=true;if(e.persisted)poll();}); // a normal load already polled
 window.addEventListener('hashchange',()=>{followedHash='';followSource();});
 poll();
})();
