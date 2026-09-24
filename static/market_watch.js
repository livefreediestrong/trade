/* Market watch: read-only headlines, X posts, social pulse and trend scan. No execution endpoints. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('market-watch'))return;
 let timer=null,busy=false,active=true;
 const POLL_MS=5*60*1000;
 function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n;}
 function link(text,url){
  const safe=/^https?:\/\//i.test(String(url||''));
  if(!safe)return el('span',text);
  const a=el('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;
 }
 const when=v=>{if(v==null||v==='')return 'time unknown';const d=typeof v==='number'?new Date(v*1000):new Date(v);return isNaN(d)?'time unknown':d.toLocaleString();};
 const SOURCE_LABEL={reddit:'Reddit',stocktwits:'Stocktwits',yahoo:'Yahoo trending',google_trends:'Google search trends',google_news:'Google News',x:'X'};
 function headline(r){const a=el('article');a.append(link(r.title,r.link),el('small',`${r.publisher||r.source||'source'} · ${when(r.published_ts)}${r.feed?' · '+r.feed:''}`));return a;}
 function post(r){
  const a=el('article');
  a.append(link(r.excerpt||r.title,r.permalink));
  const who=r.account?'@'+r.account:'X post';
  const tickers=(r.ticker_candidates||[]).map(t=>'$'+t).join(' ');
  a.append(el('small',`${who} · ${when(r.created_at)}${tickers?' · '+tickers:''} · ${r.score||0} likes/reposts`));
  return a;
 }
 function renderTrends(t){
  const rows=(t&&t.tickers)||[];
  if(!rows.length){const tr=el('tr'),td=el('td','No trending tickers found yet. Sources may still be loading.');td.colSpan=3;tr.append(td);$('mw-trends').replaceChildren(tr);}
  else $('mw-trends').replaceChildren(...rows.map(r=>{
   const tr=el('tr'),tick=el('td'),breadth=el('td'),where=el('td');
   tick.append(link(r.symbol,r.google_finance_url));
   if(r.name)tick.append(el('small',r.name));
   const flags=[];if(r.in_watchlist)flags.push('on your watchlist');if(r.leveraged_or_inverse)flags.push('leveraged/inverse fund');if(r.etf)flags.push('ETF');
   if(flags.length)tick.append(el('small',flags.join(' · ')));
   breadth.append(el('strong',`${r.breadth} source${r.breadth===1?'':'s'}`),el('small',r.label));
   Object.entries(r.sources||{}).forEach(([src,info])=>where.append(el('small',`${SOURCE_LABEL[src]||src} #${info.rank}/${info.of}${info.detail?' · '+info.detail:''}`)));
   tr.append(tick,breadth,where);return tr;
  }));
  const topics=(t&&t.topics)||[];
  $('mw-topics').replaceChildren(...(topics.length?[el('h4','Market topics trending on Google search','module-title')]:[]),...topics.map(x=>{
   const p=el('p');p.append(el('strong',x.term),el('small',` ${x.traffic?x.traffic.toLocaleString()+'+ searches':''}${x.news&&x.news[0]?' · '+x.news[0]:''}`));return p;
  }));
 }
 function renderX(x){
  const s=x.status||{},u=s.usage||{};
  const posts=[...(x.accounts||[]),...(x.cashtags||[])];
  let line=s.configured?`Posts read today ${u.day_posts||0}/${u.daily_cap||0} · this month ${u.posts||0}/${u.monthly_cap||0} · polls every ${s.poll_minutes} min · ${s.query_mode} search`:'';
  if(s.accounts&&s.accounts.length)line+=` · watching @${s.accounts.join(', @')}`;
  if(s.error)line=(line?line+' · ':'')+s.error;
  $('mw-x-status').textContent=line||'X watcher idle.';
  $('mw-x').replaceChildren(...posts.slice(0,16).map(post));
 }
 function renderSocial(s){
  $('mw-social-status').textContent=s.error||(s.active_sources&&s.active_sources.length?`Active: ${s.active_sources.join(', ')} · scanned ${when(s.scanned_at)}`:'No social rows yet.');
  $('mw-social').replaceChildren(...((s.pulse||[]).slice(0,10).map(p=>{
   const tr=el('tr');tr.append(el('td','$'+p.ticker),el('td',p.mentions),el('td',(p.sources||[]).join(', ')),el('td',`${p.bullish} / ${p.bearish}`));return tr;
  })));
 }
 function statusRows(d){
  const out=[];
  const add=(name,st)=>{if(st)out.push([name,st]);};
  Object.entries((d.sources&&d.sources.google_news)||{}).forEach(([k,v])=>add('Google News · '+k,v));
  Object.entries((d.sources&&d.sources.custom_rss)||{}).forEach(([k,v])=>add('RSS · '+k,v));
  Object.entries((d.trends&&d.trends.sources)||{}).forEach(([k,v])=>add('Trend · '+(SOURCE_LABEL[k]||k),v));
  Object.entries((d.social&&d.social.source_status)||{}).forEach(([k,v])=>add('Social · '+k,v));
  return out;
 }
 function render(d){
  const trends=(d.trends&&d.trends.tickers)||[];
  $('mw-health').textContent=`${(d.headlines||[]).length} headlines · ${trends.length} trending${d.x&&d.x.status&&d.x.status.configured?' · X on':' · X off'}`;
  $('mw-status').textContent=`Updated ${when(d.as_of)}${d.from_cache?' (cached)':''}${d.refreshing?' · refreshing in the background':''}. Refreshes every five minutes while this page is open.`;
  renderTrends(d.trends);
  const heads=d.headlines||[];
  $('mw-headlines').replaceChildren(...(heads.length?heads.slice(0,16).map(headline):[el('p','No market headlines available right now. See Source status below; missing headlines do not mean no news.','hint-line')]));
  const custom=d.custom_headlines||[];$('mw-custom').hidden=!custom.length;$('mw-custom').replaceChildren(...custom.slice(0,12).map(headline));
  renderX(d.x||{});renderSocial(d.social||{});
  $('mw-watchlist').replaceChildren(...(d.watchlist||[]).map(w=>link(w.symbol,w.google_finance_url)));
  $('mw-sources').replaceChildren(...statusRows(d).map(([name,st])=>{const tr=el('tr');tr.append(el('td',name),el('td',st.ok?'connected':('unavailable'+(st.error?' · '+st.error:''))));return tr;}));
 }
 async function poll(force){
  clearTimeout(timer);if(!active||document.hidden||busy)return;busy=true;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),45000);
  try{
   const r=await fetch('/api/market-watch'+(force?'?force=1':''),{signal:controller.signal,credentials:'same-origin'});
   const d=await r.json();
   if(!r.ok||!d.ok)throw Error(d.error||'Market watch unavailable');
   if(active&&!document.hidden)render(d);
  }catch(e){$('mw-health').textContent='Sources unavailable';$('mw-status').textContent=(e&&e.name==='AbortError')?'Market watch timed out; it will retry.':String((e&&e.message)||'Market watch unavailable');}
  finally{clearTimeout(timeout);busy=false;if(active&&!document.hidden)timer=setTimeout(()=>poll(false),POLL_MS);}
 }
 $('mw-refresh').addEventListener('click',()=>poll(true));
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)poll(false);});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);});window.addEventListener('pageshow',()=>{active=true;poll(false);});
 poll(false);
})();
