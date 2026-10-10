const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function harness(){
 const nodes={},requests=[],timers=[];let clock=Date.now();
 const make=()=>({textContent:'',hidden:false,disabled:false,checked:false,children:[],handlers:{},addEventListener(n,f){this.handlers[n]=f;},replaceChildren(...rows){this.children=rows;},focus(){this.focused=true;},querySelectorAll(){return this.children.flatMap(li=>li.children.filter(n=>n.type==='button'));}});
 const node=id=>nodes[id]??=make(),window={};
 vm.runInNewContext(fs.readFileSync('static/stop_review.js','utf8'),{document:{getElementById:node,createElement:make},window,
  Date:class extends Date{static now(){return clock}},AbortController,setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout(){},
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
 const state=extra=>({identity:{broker:'ibkr',paper_mode:true,account_id:'DU123'},revision:'a',enabled:false,session_active:false,stop_corrections:[{ref:'ref',ticker:'TEST',key:'v2',message:'A correction needs review'}],...extra});
 const review=extra=>({ref:'ref',ticker:'TEST',account:'IBKR paper …U123',before:{shares:1,price:99.2,fees:.2},after:{shares:1,price:98,fees:.3},remaining:1,held:1,stop_status:'Submitted',issues:[],can_accept:true,token:'exact-token',expires_at:new Date(clock+90000).toISOString(),...extra});
 const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
 const reply=async(index,body,ok=true)=>{requests[index].resolve({ok,json:async()=>body});await flush();};
 return {node,requests,timers,state,review,flush,reply,render:s=>window.TomahawkStopReview.render(s),advance:ms=>clock+=ms,
  check:()=>node('stop-correction-list').children[0].children[1].handlers.click(),
  ack:()=>{node('stop-correction-ack').checked=true;node('stop-correction-ack').handlers.change();},
  accept:()=>node('stop-correction-accept').handlers.click(),close:()=>node('stop-correction-close').handlers.click()};
}
(async()=>{
 const h=harness();h.render(h.state());assert.equal(h.requests.length,0);
 h.check();h.check();assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/api/live-agent/corrections/review');
 await h.reply(0,{ok:true,review:h.review()});assert.equal(h.node('stop-correction-accept').disabled,true);
 assert.equal(h.node('stop-correction-comparison').children[1].children[2].textContent,'$98.00');
 h.accept();assert.equal(h.requests.length,1);h.ack();h.accept();h.accept();assert.equal(h.requests.length,2);
 assert.deepEqual(JSON.parse(h.requests[1].options.body),{token:'exact-token',acknowledge:true});
 await h.reply(1,{ok:true,message:'Correction reviewed; trading stays paused.'});
 assert.match(h.node('stop-correction-result').textContent,/stays paused/);assert.equal(h.node('stop-correction-detail').hidden,true);
 assert.ok(h.requests.every(r=>/corrections\/(review|accept)$/.test(r.url)));
 for(const change of [{enabled:true},{session_active:true},{identity:{broker:'ibkr',paper_mode:false,account_id:'U123'}},{revision:'new'}]){
  const x=harness();x.render(x.state());x.check();await x.reply(0,{ok:true,review:x.review()});x.ack();x.render(x.state(change));x.accept();
  assert.equal(x.requests.length,1);assert.equal(x.node('stop-correction-accept').disabled,true);
 }
 const stale=harness();stale.render(stale.state());stale.check();await stale.reply(0,{ok:true,review:stale.review()});stale.advance(90001);stale.ack();stale.accept();assert.equal(stale.requests.length,1);
 assert.match(stale.node('stop-correction-expiry').textContent,/expired/);
 const changed=harness();changed.render(changed.state());changed.check();await changed.reply(0,{ok:true,review:changed.review()});changed.ack();changed.render(changed.state({stop_corrections:[{ref:'ref',key:'v3'}]}));changed.accept();assert.equal(changed.requests.length,1);
 const unsafe=harness();unsafe.render(unsafe.state());unsafe.check();await unsafe.reply(0,{ok:true,review:unsafe.review({can_accept:false,token:undefined,issues:['Fees are missing']})});unsafe.ack();unsafe.accept();assert.equal(unsafe.requests.length,1);assert.equal(unsafe.node('stop-correction-issues').children[0].textContent,'Fees are missing');
 const closed=harness();closed.render(closed.state());closed.check();closed.close();await closed.reply(0,{ok:true,review:closed.review()});assert.equal(closed.node('stop-correction-detail').hidden,true);
 const failure=harness();failure.render(failure.state());failure.check();await failure.reply(0,{ok:true,review:failure.review()});failure.ack();failure.accept();failure.requests[1].reject(Object.assign(Error('timeout'),{name:'AbortError'}));await failure.flush();failure.accept();assert.equal(failure.requests.length,2);assert.match(failure.node('stop-correction-result').textContent,/not be retried automatically/);
 const malicious=harness();malicious.render(malicious.state({stop_corrections:[{ref:'ref',key:'v2',ticker:'<img onerror=bad>',message:'<script>bad</script>'}]}));
 assert.match(malicious.node('stop-correction-list').children[0].children[0].textContent,/<script>/);assert.equal(malicious.node('stop-correction-list').children[0].children[0].innerHTML,undefined);
 console.log('Correction review UI passed: explicit acknowledgement, exact token, no activation, duplicate/stale/changed-account guards, blocked evidence, late responses, timeout without retry, escaped text.');
})().catch(e=>{console.error(e);process.exit(1);});
