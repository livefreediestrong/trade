(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('trading-goals'))return;
 const money=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{style:'currency',currency:'USD'}):'Unavailable';
 let revision=null,busy=false,timer,staleTimer,active=true;
 const dirty=new Set();
 const draftRevision={};
 function unavailable(message){clearTimeout(staleTimer);$('goals-status').textContent=message;for(const s of ['live','paper']){$('goal-'+s+'-progress').textContent='Current progress unavailable';$('goal-'+s+'-meter').hidden=true;$('goal-'+s+'-milestones').replaceChildren();}}
 function render(d){
  clearTimeout(staleTimer);revision=d.revision;$('goals-status').textContent='Trading day '+d.day+' · updated '+new Date(d.as_of).toLocaleTimeString();
  for(const scope of ['live','paper']){
   const data=d[scope],p=data.progress,base='goal-'+scope+'-';
   $(base+'progress').textContent=p.target==null?'No profit target · '+money(p.pnl)+' today':money(p.pnl)+' / '+money(p.target)+(p.pnl==null?' · waiting for verified P&L':p.hit?' · target reached': ' · '+money(p.remaining)+' remaining');
   $(base+'source').textContent=p.source;
   $(base+'meter').hidden=p.percent==null;$(base+'meter').value=p.percent??0;
   $(base+'milestones').replaceChildren(...data.milestones.map(m=>{const li=document.createElement('li');li.textContent=(m.complete===true?'✓ ':m.complete===false?'○ ':'? ')+m.label+(m.detail?' — '+m.detail:'');return li;}));
   if(!dirty.has(scope)){
    const select=$(base+'preset');select.replaceChildren(...[...data.presets,{id:'custom',label:'Custom profit target + milestones'}].map(p=>new Option(p.label,p.id)));
    select.value=data.selected||'observe';$(base+'custom').value=p.target??0;showCustom(scope);
   }
  }
  staleTimer=setTimeout(()=>unavailable('Goal progress is stale. Refreshing is required before relying on it.'),45000);
 }
 function showCustom(s){const custom=$('goal-'+s+'-preset').value==='custom';$('goal-'+s+'-custom').hidden=!custom;$('goal-'+s+'-custom-label').hidden=!custom;$('goal-'+s+'-custom').required=custom;}
 async function json(options){const c=new AbortController(),timeout=setTimeout(()=>c.abort(),10000);try{const r=await fetch('/api/trading-goals',{...options,signal:c.signal,credentials:'same-origin'}),d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Could not read goals');return d;}finally{clearTimeout(timeout);}}
 async function poll(){clearTimeout(timer);if(busy||!active||document.hidden)return;busy=true;try{render(await json());}catch(e){unavailable(e.message);}finally{busy=false;if(active&&!document.hidden)timer=setTimeout(poll,25000);}}
 for(const s of ['live','paper']){
  $('goal-'+s+'-form').addEventListener('input',()=>{if(!dirty.has(s))draftRevision[s]=revision;dirty.add(s);showCustom(s);});
  $('goal-'+s+'-form').addEventListener('submit',async e=>{e.preventDefault();if(busy||!revision)return;busy=true;const button=e.currentTarget.querySelector('button');button.disabled=true;try{const d=await json({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:s,preset:$('goal-'+s+'-preset').value,target:$('goal-'+s+'-custom').value,revision:dirty.has(s)?draftRevision[s]:revision})});dirty.delete(s);render(d);$('goals-status').textContent='Saved '+s+' goal. Trading activation and risk limits are unchanged.';}catch(err){$('goals-status').textContent=err.message;if(/changed elsewhere/.test(err.message)){revision=null;}}finally{button.disabled=false;busy=false;timer=setTimeout(poll,25000);}});
 }
 document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);unavailable('Refresh goals when returning to the desk.');}else poll();});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);clearTimeout(staleTimer);});window.addEventListener('pageshow',e=>{active=true;if(e.persisted)poll();});poll();
})();
