/* Explicit paper discrepancy review. No automatic mutation or retry. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),panel=$('option-review-details');
 if(!panel)return;
 let draft=null,busy=false,epoch=0;
 const message=text=>{$('option-review-result').textContent=text;};
 function controls(){
  const fresh=draft&&Date.now()<Date.parse(draft.expires_at);
  $('option-review-save').disabled=busy||!fresh||!$('option-review-ack').checked||$('option-review-reference').value.trim().length<3;
  if(draft&&!fresh)message('Review expired. Read the broker evidence again.');
 }
 async function post(path,body){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  try{const r=await fetch('/api/live-agent/options/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
   const data=await r.json();if(!r.ok||data.ok!==true)throw Error(data.error||'Review failed');return data;
  }finally{clearTimeout(timer);}
 }
 async function prepare(id){
  if(busy)return;
  const run=++epoch;busy=true;draft=null;panel.hidden=true;controls();message('Reading current broker holdings and orders…');
  try{const result=await post('review-closed',{id});if(run!==epoch)return;draft=result;$('option-review-ack').checked=false;$('option-review-reference').value='';
   $('option-review-evidence').textContent=`${result.ticker}: ${result.message}. Review expires at ${new Date(result.expires_at).toLocaleTimeString()}.`;
   panel.hidden=false;message('Read the evidence and add the activity reference before recording your review.');
  }catch(e){message(e.name==='AbortError'?'Broker check timed out. Nothing was saved.':e.message);}
  finally{busy=false;controls();}
 }
 $('option-review-save').addEventListener('click',async()=>{
  controls();if($('option-review-save').disabled)return;
  const body={token:draft.token,reference:$('option-review-reference').value.trim(),acknowledge:true};
  busy=true;draft=null;controls();message('Rechecking the broker before saving the review…');
  try{const result=await post('accept-closed',body);panel.hidden=true;message(result.message);}
  catch(e){panel.hidden=true;message(e.name==='AbortError'?'Save response timed out. Do not retry this token. Refresh to check whether the review was saved.':e.message);}
  finally{busy=false;controls();}
 });
 $('option-review-ack').addEventListener('change',controls);
 $('option-review-reference').addEventListener('input',controls);
 setInterval(controls,1000);
 window.TomahawkOptionReview={prepare};
})();
