/* Read retained reference data. Never initiates provider refreshes or orders. */
(()=>{'use strict';
 const $=id=>document.getElementById(id);if(!$('public-context'))return;
 const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;return e;};
 const link=(text,url)=>{const e=node('a',text);if(/^https:\/\//.test(url||'')){e.href=url;e.target='_blank';e.rel='noopener noreferrer';}return e;};
 let timer,busy=false;
 async function update(){if(busy||document.hidden)return;busy=true;const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),10000);
  try{const response=await fetch('/api/public-context',{signal:controller.signal}),d=await response.json();if(!response.ok||!d.ok)throw Error(d.error||'Source receipts unavailable');
   $('public-context-status').textContent=d.sources.filter(s=>s.fresh).length+' of '+d.sources.length+' source reads current. '+d.note;
   $('public-context-values').replaceChildren();$('public-context-sources').replaceChildren();$('public-context-halts').replaceChildren();$('public-context-held').replaceChildren();
   for(const s of d.sources){const source=node('article','');source.append(node('h3',s.name+' · '+(s.fresh?'read current':'read unavailable / stale')),node('p','Publisher: '+s.owner+' · Last successful read: '+(s.last_ok_at||'not yet observed')));if(s.error)source.append(node('p',s.error));source.append(link('Original source',s.url));$('public-context-sources').append(source);
    if(s.id==='halts'){const rows=s.rows.filter(r=>(d.relevant_symbols||[]).includes(r.symbol));for(const r of rows)$('public-context-halts').append(node('p',r.symbol+' · '+r.reason+' · halted '+r.halt_at+' · '+(r.resumption_at?'resumption published '+r.resumption_at:'no trade-resumption time published')+(s.fresh?'':' · stale source')));if(!rows.length)$('public-context-halts').append(node('p',s.fresh?'No matching halt in this retained feed. This is not an exchange tradability check.':'Halt coverage unavailable. Absence cannot clear a symbol.'));continue;}
    for(const r of s.rows){const card=node('article','');card.append(node('h3',r.series),node('p',r.value+' '+r.unit+' · observation '+r.observation_date+' ('+r.observation_age_days+' calendar days ago)'),node('small','First seen '+r.first_seen_at+' · '+r.revisions.length+' retained revisions'+(s.fresh?'':' · stale source')));$('public-context-values').append(card);source.append(node('p',r.series+' · vintage '+r.vintage_id+' · publication time '+(r.published_at||'not supplied')));}
   }
   $('public-context-held').append(node('p',d.held_context?.note||'Position coverage unverified.'));
   const brief=d.session_brief||{};$('public-context-brief').replaceChildren(node('p','Next regular session: '+(brief.next_session||'unavailable')),node('p',brief.note||'Brief unavailable.'));
   for(const e of brief.events||[]){const p=node('p',e.start+' · '+e.impact+' · ');p.append(link(e.title,e.url));$('public-context-brief').append(p);}
   for(const r of d.held_context?.items||[]){const p=node('p',r.symbol+' · ');p.append(link(r.title,r.url),node('small',' · published '+(r.published_at||'unknown')));$('public-context-held').append(p);}
  }catch(e){$('public-context-status').textContent='Reference read unavailable; any values below are historical. '+e.message;}finally{clearTimeout(deadline);busy=false;clearTimeout(timer);if(!document.hidden)timer=setTimeout(update,60000);}
 }
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)update();});window.addEventListener('pagehide',()=>clearTimeout(timer));update();
})();
