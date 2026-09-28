const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function harness(){
 const nodes={},requests=[],intervals=[];let now=Date.now();
 const node=id=>nodes[id]??={value:'',textContent:'',hidden:false,disabled:false,checked:false,handlers:{},addEventListener(n,f){this.handlers[n]=f;}};
 const window={};
 vm.runInNewContext(fs.readFileSync('static/option_review.js','utf8'),{document:{getElementById:node},window,
  Date:class extends Date{static now(){return now;}},AbortController,setTimeout:()=>1,clearTimeout(){},setInterval:f=>intervals.push(f),
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
 const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
 const reply=async(i,body)=>{requests[i].resolve({ok:true,json:async()=>body});await flush();};
 return {node,requests,flush,reply,prepare:()=>window.TomahawkOptionReview.prepare('position'),
  preview:()=>({ok:true,token:'single-use',ticker:'<img onerror=bad>',message:'Current broker evidence',expires_at:new Date(now+90000).toISOString()}),
  ack:()=>{node('option-review-reference').value='Activity checked';node('option-review-ack').checked=true;node('option-review-ack').handlers.change();},
  save:()=>node('option-review-save').handlers.click(),expire:()=>{now+=90001;intervals[0]();}};
}
(async()=>{
 const h=harness();assert.equal(h.requests.length,0);
 h.prepare();h.prepare();assert.equal(h.requests.length,1);
 await h.reply(0,h.preview());assert.equal(h.node('option-review-save').disabled,true);
 assert.match(h.node('option-review-evidence').textContent,/<img onerror=bad>/);
 assert.equal(h.node('option-review-evidence').innerHTML,undefined);
 h.save();assert.equal(h.requests.length,1);h.ack();h.save();h.save();assert.equal(h.requests.length,2);
 assert.deepEqual(JSON.parse(h.requests[1].options.body),{token:'single-use',reference:'Activity checked',acknowledge:true});
 await h.reply(1,{ok:true,message:'Review recorded. Fox stays paused.'});assert.equal(h.node('option-review-details').hidden,true);
 assert.ok(h.requests.every(r=>/options\/(review-closed|accept-closed)$/.test(r.url)));
 const old=harness();old.prepare();await old.reply(0,old.preview());old.ack();old.expire();old.save();assert.equal(old.requests.length,1);
 const timeout=harness();timeout.prepare();await timeout.reply(0,timeout.preview());timeout.ack();timeout.save();
 timeout.requests[1].reject(Object.assign(Error('timeout'),{name:'AbortError'}));await timeout.flush();timeout.save();
 assert.equal(timeout.requests.length,2);assert.match(timeout.node('option-review-result').textContent,/Do not retry/);
 console.log('Option review UI passed: explicit acknowledgement, single use, no control mutation, expired evidence, escaped text, no retry after uncertain response.');
})().catch(e=>{console.error(e);process.exit(1);});
