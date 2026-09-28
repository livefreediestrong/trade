/* Recording a reviewed correction is separate from enabling or sending trades. */
(()=>{
 'use strict';
 const $=id=>document.getElementById(id),root=$('stop-correction-review');if(!root)return;
 let state=null,received=0,current=null,busy=false,epoch=0,listKey='',returnButton=null,timer=null;
 const text=(id,value)=>{$(id).textContent=value;};
 const money=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString(undefined,{style:'currency',currency:'USD',maximumFractionDigits:4}):'Not reported';
 const scope=s=>JSON.stringify([s?.identity,s?.revision,s?.enabled,s?.session_active]);
 const ready=()=>state?.identity?.broker==='ibkr'&&state.identity.paper_mode===true&&state.enabled===false&&state.session_active===false&&Date.now()-received<25000;
 function controls(){
  const expired=!current?.token||Date.now()>=Date.parse(current.expires_at);
  $('stop-correction-accept').disabled=busy||!ready()||!current?.can_accept||expired||!$('stop-correction-ack').checked;
  $('stop-correction-ack').disabled=busy||!ready()||expired||!current?.can_accept;
  if(current?.token&&expired)text('stop-correction-expiry','This review expired. Close it and check the correction again.');
  for(const button of $('stop-correction-list').querySelectorAll('button'))button.disabled=busy||!ready();
 }
 function discard(message=''){
  ++epoch;current=null;clearTimeout(timer);$('stop-correction-detail').hidden=true;$('stop-correction-ack').checked=false;
  if(message)text('stop-correction-result',message);controls();
 }
 async function post(path,body){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
  try{const response=await fetch('/api/live-agent/corrections/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
   const value=await response.json();if(!response.ok||!value.ok)throw Error(value.error||'Correction review unavailable');return value;
  }finally{clearTimeout(timeout);}
 }
 function show(review){
  current=review;$('stop-correction-detail').hidden=false;$('stop-correction-ack').checked=false;
  text('stop-correction-heading',review.ticker+' · check the corrected trade');text('stop-correction-account',review.account);
  const rows=[['Shares sold',review.before.shares??'Not reported',review.after.shares??'Not reported'],['Average sale price',money(review.before.price),money(review.after.price)],['Fees',money(review.before.fees),money(review.after.fees)]];
  $('stop-correction-comparison').replaceChildren(...rows.map(values=>{const tr=document.createElement('tr');tr.replaceChildren(...values.map((value,i)=>{const cell=document.createElement(i?'td':'th');if(!i)cell.scope='row';cell.textContent=String(value);return cell;}));return tr;}));
  const status={Submitted:'accepted and working',PreSubmitted:'accepted; waiting to trigger',Filled:'finished selling',Cancelled:'cancelled',ApiCancelled:'cancelled',Inactive:'rejected or inactive',PendingCancel:'cancellation not confirmed',PendingSubmit:'acceptance not confirmed'}[review.stop_status]||'not verified';
  text('stop-correction-position',`Fox tracks ${review.remaining??'unknown'} remaining ${review.remaining===1?'share':'shares'}. Broker reports ${review.held??'unknown'} ${review.held===1?'share':'shares'}. Broker stop: ${status}.`);
  $('stop-correction-issues').replaceChildren(...review.issues.map(message=>{const li=document.createElement('li');li.textContent=message;return li;}));
  text('stop-correction-expiry',review.can_accept?'Checked just now. This review expires in 90 seconds; the server checks the evidence again when you confirm.':'This correction cannot be cleared yet. Resolve the items above, then check again.');
  text('stop-correction-result',review.can_accept?'Compare the values before recording your review. Trading will remain paused.':'The correction remains blocked.');
  $('stop-correction-heading').focus();clearTimeout(timer);if(review.token)timer=setTimeout(controls,Math.max(0,Date.parse(review.expires_at)-Date.now())+1);controls();
 }
 async function check(ref,button){
  if(busy||!ready())return;discard();returnButton=button;busy=true;const run=epoch,identity=scope(state);controls();text('stop-correction-result','Checking corrected executions, fees, holdings and the remaining stop…');
  try{const result=await post('review',{ref});if(run===epoch&&identity===scope(state)&&ready())show(result.review);}
  catch(error){if(run===epoch)text('stop-correction-result',error.name==='AbortError'?'The check timed out. Check again when the broker responds.':error.message);}
  finally{busy=false;controls();}
 }
 function render(next){
  const oldScope=scope(state),items=next.stop_corrections||[];
  if(state&&oldScope!==scope(next))discard('Account, policy or trading state changed. Check the correction again.');
  if(current&&state){const old=(state.stop_corrections||[]).find(r=>r.ref===current.ref),fresh=items.find(r=>r.ref===current.ref);if(!fresh||old?.key!==fresh.key)discard('The correction changed. Check the latest values before recording a review.');}
  state=next;received=Date.now();root.hidden=!items.length&&!current&&!$('stop-correction-result').textContent;
  const key=JSON.stringify(items);
  if(key!==listKey){listKey=key;$('stop-correction-list').replaceChildren(...items.map(item=>{const li=document.createElement('li'),label=document.createElement('p'),button=document.createElement('button');label.textContent=item.ticker+' · '+item.message;button.type='button';button.className='btn';button.textContent='Check '+item.ticker+' correction';button.addEventListener('click',()=>check(item.ref,button));li.replaceChildren(label,button);return li;}));}
  controls();
 }
 $('stop-correction-ack').addEventListener('change',controls);
 $('stop-correction-close').addEventListener('click',()=>{discard();returnButton?.focus();});
 $('stop-correction-accept').addEventListener('click',async()=>{
  controls();if($('stop-correction-accept').disabled)return;
  const token=current.token,run=epoch;busy=true;controls();
  try{const result=await post('accept',{token,acknowledge:true});if(run===epoch){discard();text('stop-correction-result',result.message);}}
  catch(error){if(run===epoch){discard();text('stop-correction-result',error.name==='AbortError'?'The save response was not received. It will not be retried automatically. Refresh the list before checking again.':error.message);}}
  finally{busy=false;controls();}
 });
 window.TomahawkStopReview={render};
})();
