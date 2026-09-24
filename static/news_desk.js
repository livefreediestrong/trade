/* Read-only headline polling; no model calls or execution endpoints. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('companion-news')||!window.NadzeelNews)return;
 let timer=null,busy=false,active=true,seen=new Set(),rows=[];
 try{seen=new Set(JSON.parse(sessionStorage.getItem('changing_woman_news_v1')||'[]'));}catch(_){}
 function el(tag,text){const n=document.createElement(tag);if(text)n.textContent=text;return n;}
 function link(text,url){if(!/^https?:\/\//i.test(String(url||'')))return el('span',text);const a=el('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
 const date=t=>t?new Date(t*1000).toLocaleString():'not checked';
 function present(){
  if(document.hidden||!active)return;
  const candidates=rows.map(r=>window.NadzeelNews.newsQuip(r)).filter(Boolean);
  const next=candidates.find(q=>!seen.has(q.id));
  if(next){
   const event=new CustomEvent('moss:news',{detail:next,cancelable:true});
   window.dispatchEvent(event);
   if(event.defaultPrevented){seen.add(next.id);seen=new Set([...seen].slice(-200));try{sessionStorage.setItem('changing_woman_news_v1',JSON.stringify([...seen]));}catch(_){}}
  }
  const preview=candidates[0];
  $('headline-story').hidden=!preview;
  $('headline-quip').textContent=preview?preview.text:'No fresh, verified headline is available for a quip. She will wait for one.';
  $('headline-credit').textContent=preview?`${preview.source} · published ${new Date(preview.publishedAt).toLocaleString()}`:'';
  if(preview){$('headline-story').textContent=preview.title;$('headline-story').href=preview.url;}
 }
 function render(d){
  rows=d.items||[];
  const connected=d.sources.filter(s=>['connected','partial'].includes(s.status)).length;
  $('headline-health').textContent=`${connected} / ${d.sources.length} feeds available`;
  $('headline-status').textContent=d.error|| (d.busy?'Updating sources; saved headlines retain their original publication times.':`${rows.length} recent headlines · checks every five minutes · no automatic trades.`);
  $('headline-sources').replaceChildren(...d.sources.map(s=>{
   const tr=el('tr'),name=el('td'),focus=el('td'),status=el('td');
   name.append(link(s.name,s.home),el('small',s.kind));focus.textContent=s.focus;
   status.append(el('strong',s.status+(s.error?' · '+s.error:'')),el('small',`${s.recent} recent · checked ${date(s.checked_at)}`));
   tr.append(name,focus,status);return tr;
  }));
  $('headline-list').replaceChildren(...rows.slice(0,12).map(r=>{
   const article=el('article');article.append(link(r.title,r.url),el('small',`${r.source} · ${date(r.published_ts)}${r.fresh?'':' · cached / source unavailable'}`));return article;
  }));present();
 }
 async function poll(){
  clearTimeout(timer);if(!active||document.hidden||busy)return;busy=true;let loading=false;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{const r=await fetch('/api/companion/news',{signal:controller.signal});if(!r.ok)throw Error('Headline service unavailable');const d=await r.json();loading=d.busy;if(active&&!document.hidden)render(d);}
  catch(_){$('headline-health').textContent='Feed status unavailable';$('headline-status').textContent='Headline service unavailable. Cached headlines keep their dates.';rows=[];present();}
  finally{clearTimeout(timeout);busy=false;if(active&&!document.hidden)timer=setTimeout(poll,loading?10000:60000);}
 }
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)poll();});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);});window.addEventListener('pageshow',e=>{active=true;if(e&&e.persisted)poll();});poll();
})();
