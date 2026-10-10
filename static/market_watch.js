/* Market watch: read-only headlines, X posts, social pulse and trend scan. No execution endpoints. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('market-watch'))return;
 let timer=null,busy=false,active=true;
 let quoteBusy=false;
 const quotes=new Map();
 const POLL_MS=5*60*1000;
 function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n;}
 function link(text,url){
  const safe=/^https?:\/\//i.test(String(url||''));
  if(!safe)return el('span',text);
  const a=el('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;
 }
 const when=v=>{if(v==null||v==='')return 'time unknown';const d=typeof v==='number'?new Date(v*1000):new Date(v);return isNaN(d)?'time unknown':d.toLocaleString();};
 const SOURCE_LABEL={wsb:'WSB threads (live)',reddit:'Reddit',stocktwits:'Stocktwits',yahoo:'Yahoo trending',google_trends:'Google search trends',google_news:'Google News',x:'X'};
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
 function showQuote(price,q){
    if(!q||q.price==null){price.textContent='No provider price available. You can retry after the one-minute cache expires.';return;}
    // Recompute on render as the snapshot may outlive the quote's freshness.
    const ts=Date.parse(q.market_time),age=Number.isFinite(ts)?(Date.now()-ts)/1000:null;
    const recent=age!==null&&age>=-15&&age<=120;
    price.textContent=`${Number(q.price).toLocaleString(undefined,{maximumFractionDigits:4})} · ${q.source||'provider'} · market time ${when(q.market_time)} · ${recent?'within 2 min when checked':'old or unverified time'}${q.from_cache?' · cached':''}. Currency not supplied. Research price only; timing does not guarantee a real-time feed.`;
 }
 async function checkQuote(symbol,button,price){
    if(quoteBusy){price.textContent='Another price check is running. Try again shortly.';return;}
    quoteBusy=true;button.disabled=true;price.textContent='Checking the provider price…';
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),35000);
    try{
     const r=await fetch('/api/market-watch/quote?symbol='+encodeURIComponent(symbol),{credentials:'same-origin',signal:controller.signal});const q=await r.json();
     if(!r.ok)throw Error(q.error||'Price check unavailable.');
     quotes.set(symbol,q);showQuote(price,q);
    }catch(e){price.textContent=e&&e.name==='AbortError'?'Price check timed out. Try again shortly.':String(e.message||'Price check unavailable.');}
    finally{clearTimeout(timeout);quoteBusy=false;button.disabled=false;}
 }
 function renderSocial(s){
  const excluded=s.excluded||{}, skipped=Object.values(excluded).reduce((sum,n)=>sum+(Number(n)||0),0);
  $('mw-social-status').textContent=(s.error||`24-hour sample ending ${when(s.window_ends_at||s.scanned_at)} · available sources: ${(s.active_sources||[]).join(', ')||'none'}`)+(skipped?` · ${skipped} old, undated, future or duplicate records excluded`:'');
  $('mw-social-method').textContent=s.method_note||'Missing posts do not mean no activity. Word cues can misread sarcasm and negation. Headlines do not vote.';
  const rows=(s.pulse||[]).slice(0,10).map(p=>{
   const tr=el('tr'),ticker=el('td'),counts=el('td'),tone=el('td'),detail=el('details'),summary=el('summary',`$${p.ticker} · evidence & price`);
   detail.append(summary,el('small',`Latest record ${when(p.newest_at)} · ${(p.sources||[]).join(', ')}`));
   (p.items||[]).forEach(item=>{
    const article=el('p');article.append(link(item.excerpt||item.title||'Open source',item.permalink),el('small',`${item.source||'source'} · ${when(item.created_at)}`));detail.append(article);
   });
   const button=el('button','Check provider price','btn ghost'),price=el('p','No price requested.','hint-line');button.type='button';price.setAttribute('role','status');
   if(quotes.has(p.ticker))showQuote(price,quotes.get(p.ticker));
   button.addEventListener('click',()=>checkQuote(p.ticker,button,price));
   detail.append(button,price);ticker.append(detail);
   counts.append(el('span',`${p.post_mentions||0} posts / ${p.headline_mentions||0} headlines`));
   tone.append(el('span',`${p.bullish||0} bullish / ${p.bearish||0} bearish`),el('small',`${p.uncertain||0} unclear · approximate text labels`));
   const accounts=el('td',`${p.unique_authors||0} identified accounts`);
   counts.setAttribute('data-label','Posts / headlines');accounts.setAttribute('data-label','Accounts seen');tone.setAttribute('data-label','Post tone');
   tr.append(ticker,counts,accounts,tone);return tr;
  });
  if(!rows.length){const tr=el('tr'),td=el('td','No current community sample to compare. Check Source status below.');td.colSpan=4;tr.append(td);rows.push(tr);}
  $('mw-social').replaceChildren(...rows);
 }
 function renderWsb(w){
  if(!$('mw-wsb'))return;
  const threads=w.threads||[];
  $('mw-wsb-status').textContent=!w.configured?'Direct Reddit threads need approved access and credentials. Independent WSB summaries are shown separately below.':
   `${(w.comments_read||0).toLocaleString()} comments read${w.fresh?'':' · not read in the last 15 minutes'}${w.error?' · '+w.error:''}`;
  $('mw-wsb-threads').replaceChildren(...threads.map(t=>link(`${t.label}: ${t.title||''}`,t.url)));
  const rows=(w.top||[]).map(r=>{const tr=el('tr');const lean=r.bull_share==null?'—':r.bull_share>=.5?Math.round(r.bull_share*100)+'% bullish':Math.round((1-r.bull_share)*100)+'% bearish';
   tr.append(el('td','$'+r.ticker),el('td',r.mentions_15m),el('td',r.mentions_60m+(r.live_chat_pasted?' (+'+r.live_chat_pasted+' pasted chat)':'')),el('td',r.velocity==null?'building history':r.velocity+'×'),el('td',r.authors_60m),el('td',lean));return tr;});
  $('mw-wsb').replaceChildren(...(rows.length?rows:[(()=>{const tr=el('tr'),td=el('td',w.configured?'No ticker mentions in the last hour.':'Not connected.');td.colSpan=6;tr.append(td);return tr;})()]));
  $('mw-wsb-note').textContent=[w.stats_note,w.live_chat_note].filter(Boolean).join(' ');
 }
 function renderCommunity(data){
  if(!$('mw-community-note'))return;
  const providers=data.providers||{};
  $('mw-community-note').textContent=data.note||'Source update times and coverage are unverified.';
  ['apewisdom','tradestie'].forEach(name=>{
   const p=providers[name]||{},body=$('mw-'+name),status=$('mw-'+name+'-status');
   if(!body||!status)return;
   status.replaceChildren();
   status.append(link(p.name||(name==='apewisdom'?'ApeWisdom':'Tradestie'),p.docs));
   let line=data.enabled===false?' · Off':!p.ok?' · '+(p.error||'Unavailable'):
    ` · fetched ${when(p.fetched_at)}${p.cached?' (cached)':''} · source update time unavailable. Showing up to 10 of ${(p.rows||[]).length} records.`;
   if(p.rejected_rows)line+=` ${p.rejected_rows} unusable records excluded.`;
   if(p.next_check_at)line+=` Next check after ${when(p.next_check_at)}.`;
   status.append(el('span',line));
   const rows=(p.ok?p.rows||[]:[]).slice(0,10).map(r=>{
    const tr=el('tr'),stock=el('td',r.symbol),second=el('td'),third=el('td');
    stock.setAttribute('data-label','Stock');
    if(name==='apewisdom'){
     second.textContent=String(r.count);second.setAttribute('data-label','Mentions');
     third.textContent=r.previous_mentions==null?'Unknown':`${r.previous_mentions} mention${r.previous_mentions===1?'':'s'}`;
     if(r.rank&&r.previous_rank)third.append(el('small',`Rank ${r.previous_rank} → ${r.rank}`));
     third.setAttribute('data-label','Previous day');
    }else{
     second.textContent=({bullish:'Positive',bearish:'Negative',neutral:'Neutral',unclear:'Unclear'})[r.tone]||'Unclear';
     second.setAttribute('data-label','Provider outlook');
     third.textContent=String(r.count);third.setAttribute('data-label','Comments');
    }
    tr.append(stock,second,third);return tr;
   });
   if(!rows.length){const tr=el('tr'),td=el('td',p.ok?'Provider returned no records. This does not establish no activity.':'No current records available.');td.colSpan=3;tr.append(td);rows.push(tr);}
   body.replaceChildren(...rows);
  });
 }
 function statusRows(d){
  const out=[];
  const add=(name,st)=>{if(st)out.push([name,st]);};
  Object.entries((d.sources&&d.sources.google_news)||{}).forEach(([k,v])=>add('Google News · '+k,v));
  Object.entries((d.sources&&d.sources.custom_rss)||{}).forEach(([k,v])=>add('RSS · '+k,v));
  Object.entries((d.trends&&d.trends.sources)||{}).forEach(([k,v])=>add('Trend · '+(SOURCE_LABEL[k]||k),v));
  Object.entries((d.social&&d.social.source_status)||{}).forEach(([k,v])=>add('Social · '+k,v));
  Object.values((d.community&&d.community.providers)||{}).forEach(v=>add('WSB summary · '+v.name,{ok:false,error:v.ok?'response received; source update time unavailable':v.error,aggregate:true,received:v.ok}));
  return out;
 }
 function render(d){
  const trends=(d.trends&&d.trends.tickers)||[];
  const xs=d.x?.status||{};
  $('mw-health').textContent=`${(d.headlines||[]).length} headlines · ${trends.length} trending · X ${!xs.configured?'not configured':xs.error?'unavailable':xs.last_poll_at?'checked':'awaiting check'}`;
  $('mw-status').textContent=`Updated ${when(d.as_of)}${d.from_cache?' (cached)':''}${d.refreshing?' · refreshing in the background':''}. Refreshes every five minutes while this page is open.`;
  renderTrends(d.trends);
  const heads=d.headlines||[];
  $('mw-headlines').replaceChildren(...(heads.length?heads.slice(0,16).map(headline):[el('p','No market headlines available right now. See Source status below; missing headlines do not mean no news.','hint-line')]));
  const custom=d.custom_headlines||[];$('mw-custom').hidden=!custom.length;$('mw-custom').replaceChildren(...custom.slice(0,12).map(headline));
  renderX(d.x||{});renderSocial(d.social||{});renderWsb(d.wsb||{});renderCommunity(d.community||{});
  const market=d.market_data||{};
  if($('mw-massive-status'))$('mw-massive-status').textContent=`Massive (formerly Polygon): ${market.configured?'key configured; access '+({available:'responded',cached:'cached',not_checked:'not checked',access_denied:'denied',authentication_required:'needs authentication',rate_limited:'waiting for request allowance',plan_excluded:'excluded by declared plan',unavailable:'unavailable'}[market.state]||'not checked'):'no key configured'}. ${market.error||market.tier_note||''}`;
  if($('mw-massive-status'))Object.entries(market.endpoints||{}).forEach(([kind,info])=>{
   $('mw-massive-status').append(el('small',`${kind==='snapshot'?'Price snapshots':'Historical bars'}: ${info.error||(['available','cached'].includes(info.state)?'response received; live entitlement not established':info.state||'not checked')}`));
  });
  $('mw-watchlist').replaceChildren(...(d.watchlist||[]).map(w=>link(w.symbol,w.google_finance_url)));
  $('mw-sources').replaceChildren(...statusRows(d).map(([name,st])=>{const tr=el('tr');tr.append(el('td',name),el('td',st.aggregate&&st.received?st.error:st.ok?'connected':('unavailable'+(st.error?' · '+st.error:''))));return tr;}));
 }
 async function poll(force){
  clearTimeout(timer);if(!active||document.hidden||busy)return;busy=true;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),45000);
  try{
   const r=await fetch('/api/market-watch'+(force?'?force=1':''),{signal:controller.signal,credentials:'same-origin'});
   const d=await r.json();
   if(!r.ok||!d.ok)throw Error(d.error||'Market watch unavailable');
   if(active&&!document.hidden)render(d);
  }catch(e){$('mw-health').textContent='Sources unavailable';$('mw-status').textContent=(e&&e.name==='AbortError')?'Market watch timed out; it will retry.':String((e&&e.message)||'Market watch unavailable');renderCommunity({note:'Refresh failed. Current community summaries are unavailable.'});}
  finally{clearTimeout(timeout);busy=false;if(active&&!document.hidden)timer=setTimeout(()=>poll(false),POLL_MS);}
 }
 $('mw-price-form').addEventListener('submit',e=>{
  e.preventDefault();
  const symbol=$('mw-price-symbol').value.trim().toUpperCase();
  if(!/^[A-Z]{1,5}(?:\.[A-Z])?$/.test(symbol)){$('mw-price-result').textContent='Enter a US stock ticker, such as AAPL.';return;}
  checkQuote(symbol,$('mw-price-check'),$('mw-price-result'));
 });
 $('mw-refresh').addEventListener('click',()=>poll(true));
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)poll(false);});
 window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);});window.addEventListener('pageshow',e=>{active=true;if(e.persisted)poll(false);});
 poll(false);
})();
