/* Nightly evidence presentation; no trading endpoints and no additional poller. */
(() => {
 'use strict';
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const number=n=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(2):'Unavailable';
 const stamp=s=>s?new Date(s).toLocaleString():'Not yet';
 const url=value=>{try{const u=new URL(value);return /^https?:$/.test(u.protocol)&&!u.username&&!u.password?u.href:'';}catch(_){return '';}};
 function sourceRows(e){return [e.outcomes,e.executions,e.coverage,...(e.market||[]),...(e.headlines||[]),...(e.memories||[])].filter(Boolean);}
 function narrative(model,name,prefix){
  const r=model?.result;
  if(!r)return `<article class="nightly-voice"><h3>${name}</h3><p>${esc(model?.note||'Waiting for this review.')}</p></article>`;
  const items=(key,label)=>r[key]?.length?`<h4>${label}</h4><ul>${r[key].map(x=>`<li><p>${esc(x.text)}</p>${x.test?`<p class="nightly-test"><strong>Prospective test:</strong> ${esc(x.test)}</p>`:''}<small>${(x.evidence_ids||[]).map(id=>`<a href="#${prefix}-${encodeURIComponent(id)}">${esc(id)}</a>`).join(' · ')}</small></li>`).join('')}</ul>`:'';
  return `<article class="nightly-voice"><h3>${name}</h3><small>${esc(model.model)} · AI interpretation</small><p class="nightly-summary">${esc(r.summary)}</p>${items('findings','Interpretation of the evidence')}${items('hypotheses','Ideas to test · unproven')}${items('uncertainties','What remains uncertain')}</article>`;
 }
 function reportHTML(r,prefix){
  if(!r)return '<p>No session review has been saved yet.</p>';
  const e=r.evidence;if(!e)return `<p>${esc(r.error||'Collecting dated evidence for '+r.day+'…')}</p>`;
  const o=e.outcomes,t=o.today,p=o.prior_20_observed_sessions;
  return `<p><strong>${esc(r.day)}</strong> · ${esc(r.status)} · saved ${esc(stamp(r.completed_at||r.attempted_at))}</p>
   <div class="nightly-metrics"><article><h3>Today · simulated observations</h3><strong>${t.outcomes} qualified</strong><p>${number(t.mean_net_bps)} bps mean after recorded costs</p><small>${o.today_recorded} recorded observations; unscored or disqualified records excluded.</small></article><article><h3>Prior observed sessions</h3><strong>${p.outcomes} qualified · ${p.session_days} days</strong><p>${number(p.mean_net_bps)} bps mean after recorded costs</p><small>Up to 20 earlier observed sessions. Today's data excluded.</small></article></div>
   <p class="nightly-verdict"><strong>${esc(o.verdict.title)}</strong> ${esc(o.verdict.text)}</p>
   <div class="nightly-voices">${narrative(r.models?.changing_woman,'Changing Woman · evidence & context',prefix)}${narrative(r.models?.fox,'Fox · challenge & next tests',prefix)}</div>
   <details class="nightly-evidence"><summary>Inspect sources, calculations & memory (${sourceRows(e).length} records)</summary><p>Snapshot ${esc(stamp(e.collected_at))}. Outcomes available through ${esc(stamp(e.outcome_cutoff))}. ${esc(e.coverage.news)}</p>
   ${sourceRows(e).map(s=>`<article id="${prefix}-${encodeURIComponent(s.id)}"><h4>${esc(s.label)}</h4>${s.url&&url(s.url)?`<p><a href="${esc(url(s.url))}" target="_blank" rel="noopener noreferrer">Read source</a> · ${esc(s.source)} · ${esc(stamp(s.published_at))}</p><p>${esc(s.context)}</p>`:`<p>${esc(s.note||s.learning||'Saved evidence')}</p>`}<details><summary>Recorded details</summary><pre>${esc(JSON.stringify(s,null,2))}</pre></details></article>`).join('')}
   <h4>Feed coverage</h4><ul>${(e.source_health||[]).map(s=>`<li>${esc(s.name)}: ${esc(s.status)} · ${s.recent||0} recent items</li>`).join('')}</ul><p>Snapshot fingerprint: <code>${esc(e.hash)}</code></p></details><p class="hint-line keep-visible">${esc(r.memory_note||'Evidence is saved before model interpretation.')}</p>`;
 }
 if(typeof module==='object'&&module.exports){module.exports={reportHTML,url};return;}
 const panels=[...document.querySelectorAll('[data-nightly-review]')];
 panels.forEach((panel,i)=>{
  const q=k=>panel.querySelector(`[data-nightly="${k}"]`);let latest=null,selected='',pinned=false,revision=0,lastRender='';
  panel.id=i?'nightly-review-'+i:'nightly-review';
  panel.addEventListener('click',event=>{const link=event.target.closest('a');if(!link?.getAttribute('href')?.startsWith('#'+panel.id+'-'))return;const target=document.getElementById(link.getAttribute('href').slice(1));if(!target)return;let parent=target.parentElement;while(parent&&parent!==panel){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}target.scrollIntoView({block:'start'});});
  function draw(r){const key=JSON.stringify(r);if(key===lastRender)return;lastRender=key;q('body').innerHTML=reportHTML(r,panel.id);}
  q('history').addEventListener('change',async()=>{
   selected=q('history').value;pinned=true;const request=++revision;
   if(selected===latest?.day){draw(latest);return;}
   const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),15000);
   try{const res=await fetch('/api/companion/after-close/'+encodeURIComponent(selected),{signal:ctrl.signal}),data=await res.json();if(!res.ok)throw Error(data.error||'Report unavailable');if(request===revision)draw(data.report);}
   catch(e){if(request===revision)q('state').textContent='Saved review unavailable: '+e.message;}finally{clearTimeout(timer);}
  });
  window.addEventListener('moss:review',event=>{
   const s=event.detail?.after_close;if(!s)return;latest=s.latest;
   q('state').textContent=s.error||(s.busy?'Working · '+s.phase.replaceAll('_',' '):latest?'Latest review: '+latest.status:'Waiting for the closing bell');
   q('schedule').textContent=(s.schedule||'')+(s.next_at?' Next: '+stamp(s.next_at)+'.':'');
   const history=s.history||[],key=history.map(r=>r.day+':'+r.status).join('|');
   if(q('history').dataset.key!==key){q('history').dataset.key=key;q('history').innerHTML=history.length?history.map(r=>`<option value="${esc(r.day)}">${esc(r.day)} · ${esc(r.status)}</option>`).join(''):'<option>No saved review yet</option>';if(pinned&&history.some(r=>r.day===selected))q('history').value=selected;else {selected=latest?.day||'';pinned=false;}}
   if(!selected||selected===latest?.day)draw(latest);
  });
  window.addEventListener('moss:state',event=>{if(event.detail?.unavailable)q('state').textContent='Current status unavailable; saved review may be stale.';});
 });
})();
