/* Evidence-backed explanations. Browser history is presentation, never an execution input. */
(() => {
 'use strict';
 function fresh(d,now=Date.now()){const t=Date.parse(d?.as_of);return d?.ok!==false&&!!d?.context&&Number.isFinite(t)&&t<=now+5000&&now-t<=90000;}
 function record(history,c){
  if(history.some(r=>r.id===c.event_id))return history;
  const prior=history[0],data=c.quality?.data?.detail,roles=JSON.stringify(c.case?.roles||{});
  let what=c.what;
  if(prior&&prior.headline===c.what){
   if(prior.version!==c.case?.version)what='Shared investigation received a new evidence version. '+(c.case?.summary||c.what);
   else if(prior.roles!==roles)what='Shared review updated: '+Object.entries(c.case?.roles||{}).map(([k,r])=>(k==='fox'?'Fox':'Changing Woman')+' '+r.status).join(' · ');
   else if(prior.data!==data)what='Source coverage changed: '+(data||'Current coverage unavailable');
   else if(prior.why!==c.why)what='Operating checks changed. '+c.why;
  }
  return [{id:c.event_id,at:c.as_of,what,headline:c.what,why:c.why,next:c.next,case_id:c.case?.id,version:c.case?.version,data,roles},...history].slice(0,40);
 }
 function changedReviews(previous,rows){const old=new Map(previous.map(r=>[r.ticker,r]));return rows.map(r=>({...r,change:!old.has(r.ticker)?'New to this view':old.get(r.ticker).verdict!==r.verdict?'Changed from '+old.get(r.ticker).verdict:'Updated assessment'}));}
 if(typeof module==='object'&&module.exports){module.exports={fresh,record,changedReviews};return;}
 const $=id=>document.getElementById(id);if(!$('companion-context'))return;
 const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=text??'';return e;};
 let current=null,pinned=null,history=[],scope=null,timer;
 const stamp=v=>{const t=Date.parse(v);return Number.isFinite(t)?new Date(t).toLocaleString():'Time unavailable';};
 function drawExplanation(c){if(!c)return;$('companion-what').textContent='What changed: '+c.what;$('companion-why').textContent='Why: '+c.why;$('companion-next').textContent='Next condition: '+c.next;$('companion-receipt').textContent=(pinned?'Pinned · observed ':'Observed ')+stamp(c.as_of||c.at)+(c.case?' · '+c.case.id+' · evidence '+c.case.version:'');$('companion-pin').textContent=pinned?'Unpin and follow current':'Pin explanation';$('companion-pin').setAttribute('aria-pressed',String(!!pinned));}
 function unavailable(){$('companion-critical').textContent='Current trading status is unavailable. The explanation below is historical; new risk still requires fresh account checks.';$('companion-critical').dataset.blocked='true';$('companion-quality').replaceChildren(node('dt','Current checks'),node('dd','Unavailable. Refresh required.'));}
 function draw(c){
  current=c;clearTimeout(timer);timer=setTimeout(unavailable,90000);
  if(scope!==c.scope){scope=c.scope;pinned=null;try{const saved=JSON.parse(localStorage.getItem('companion-history-v1-'+scope)||'[]');history=Array.isArray(saved)?saved.filter(r=>r&&typeof r.id==='string'&&typeof r.what==='string').slice(0,40):[];}catch(_){history=[];}}
  history=record(history,c);try{localStorage.setItem('companion-history-v1-'+scope,JSON.stringify(history));const keys=[];for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(key.startsWith('companion-history-v1-'))keys.push(key);}for(const key of keys.filter(k=>k!=='companion-history-v1-'+scope).slice(0,Math.max(0,keys.length-7)))localStorage.removeItem(key);}catch(_){}
  $('companion-critical').textContent=c.quality.execution.detail;$('companion-critical').dataset.blocked=String(c.blockers.length>0);
  $('companion-quality').replaceChildren(...Object.entries(c.quality).flatMap(([k,q])=>[node('dt',({data:'Source freshness',research:'Research evidence',execution:'Execution readiness'})[k]),node('dd',q.state+' · '+q.detail)]));
  drawExplanation(pinned||c);
  const caseRoot=$('companion-case');caseRoot.replaceChildren();if(c.case){const x=c.case;caseRoot.append(node('p',x.title+' · '+x.id+' · version '+x.version+' · completed '+stamp(x.at)),node('p',x.summary),node('p',x.note));for(const [role,r] of Object.entries(x.roles)){caseRoot.append(node('h4',(role==='fox'?'Fox · decision and challenge':'Changing Woman · evidence and counterevidence')+' · '+r.status));for(const key of ['findings','challenge','uncertainties','hypotheses']){if(!r[key]?.length)continue;caseRoot.append(node('h5',key==='hypotheses'?'Unproven paper tests':key));for(const item of r[key])caseRoot.append(node('p',item.text+' · evidence: '+(item.evidence_ids||[]).join(', ')));}if(r.flagged_count)caseRoot.append(node('p',r.flagged_count+' statement(s) withheld by local factual checks. Originals remain in the nightly report.'));}const a=node('a','Inspect the complete evidence and original interpretations');a.href=x.href;caseRoot.append(a);}else caseRoot.append(node('p','No shared investigation receipt yet. Activity animations do not imply that both reviews have completed.'));
  const recall=$('companion-recall'),r=c.recall;recall.replaceChildren(node('p',r.note));if(r.available){recall.append(node('p',(r.sample_count??'?')+' matching setup observations · '+r.setup+' · recalled '+stamp(r.recalled_at)));for(const e of (r.evidence_rows||[]).slice(0,20)){recall.append(node('p',[e.id,'revision '+(e.revision||1),stamp(e.ts),e.ticker,(e.recalled_for||[]).join(' + '),e.llm_model,e.horizon_min+' min',e.net_outcome,'net '+(e.outcome_executable_move_bps??'?')+' bps','cost '+(e.outcome_cost_bps??'?')+' bps',e.current_status].filter(Boolean).join(' · ')));}if((r.evidence_rows||[]).length>20)recall.append(node('p','Showing 20 of '+r.evidence_rows.length+' retained evidence receipts.'));}
  $('companion-history').replaceChildren(...history.map(r=>{const li=node('li','');li.append(node('time',stamp(r.at)),node('p',r.what),node('p','Why: '+r.why),node('p','Next: '+r.next));if(r.case_id)li.append(node('small',r.case_id+' · '+r.version));const b=node('button','Pin this explanation');b.type='button';b.className='btn ghost sm';b.addEventListener('click',()=>{pinned=r;drawExplanation(r);});li.append(b);return li;}));
 }
 $('companion-pin').addEventListener('click',()=>{pinned=pinned?null:current;drawExplanation(pinned||current);});
 window.addEventListener('desk:day',e=>{if(fresh(e.detail))draw(e.detail.context);else unavailable();});
 window.addEventListener('desk:day-unavailable',unavailable);document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);unavailable();}});window.addEventListener('pagehide',()=>clearTimeout(timer));
})();
