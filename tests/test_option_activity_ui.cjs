const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function harness(){
 const nodes={},requests=[],intervals=[];let now=Date.now();
 const node=id=>nodes[id]??={value:'',textContent:'',hidden:false,disabled:false,checked:false,files:[],open:false,children:[],handlers:{},
  addEventListener(n,f){this.handlers[n]=f;},replaceChildren(){this.children=[];},appendChild(x){this.children.push(x);}};
 vm.runInNewContext(fs.readFileSync('static/option_activity.js','utf8'),{document:{getElementById:node,createElement:()=>({textContent:''})},
  Date:class extends Date{static now(){return now;}},AbortController,setTimeout:()=>1,clearTimeout(){},setInterval:f=>intervals.push(f),
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
 const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
 const reply=async(i,data)=>{requests[i].resolve({ok:true,json:async()=>data});await flush();};
 const result=()=>({ok:true,token:'single-use',expires_at:new Date(now+120000).toISOString(),added:1,already_stored:0,total_rows:1,
  account_suffix:'ONLY',paper_mode:true,conflicting_events:0,by_currency:{USD:{realized_pnl:null,pnl_missing:1,rows:1}},note:'Statement evidence only',
  rows:[{date:'2026-09-26',symbol:'<img onerror=bad>',type:'exercise',quantity:'-1',con_id:100,trade_id:123,match:'Matched contract',realized_pnl:null,commission_tax:'-1',currency:'USD'}]});
 const choose=(file={name:'example.csv',size:50,text:async()=> 'SAMPLE'})=>{node('option-activity-file').files=[file];node('option-activity-file').handlers.change();};
 const preview=()=>node('option-activity-preview').handlers.click();
 const ack=()=>{node('option-activity-ack').checked=true;node('option-activity-ack').handlers.change();};
 return {node,requests,flush,reply,result,choose,preview,ack,save:()=>node('option-activity-save').handlers.click(),
  expire:()=>{now+=120001;intervals[0]();}};
}
(async()=>{
 const h=harness();assert.equal(h.requests.length,0);h.choose();h.preview();h.preview();await h.flush();assert.equal(h.requests.length,1);
 assert.equal(h.node('option-activity-file').disabled,true);assert.equal(JSON.parse(h.requests[0].options.body).csv,'SAMPLE');
 await h.reply(0,h.result());assert.equal(h.node('option-activity-save').disabled,true);assert.match(h.node('option-activity-rows').children[0].textContent,/<img onerror=bad>/);
 assert.equal(h.node('option-activity-rows').children[0].innerHTML,undefined);assert.match(h.node('option-activity-totals').textContent,/not supplied/);
 h.save();assert.equal(h.requests.length,1);h.ack();h.save();h.save();assert.equal(h.requests.length,2);
 assert.deepEqual(JSON.parse(h.requests[1].options.body),{token:'single-use',acknowledge:true});
 await h.reply(1,{...h.result(),message:'Saved locally'});assert.match(h.node('option-activity-summary').textContent,/Saved evidence/);
 assert.ok(h.requests.every(r=>r.url.startsWith('/api/live-agent/options/activity')));
 const expired=harness();expired.choose();expired.preview();await expired.flush();await expired.reply(0,expired.result());expired.ack();expired.expire();expired.save();assert.equal(expired.requests.length,1);
 const change=harness();change.choose();change.preview();await change.flush();await change.reply(0,change.result());change.ack();change.choose();change.save();assert.equal(change.requests.length,1);
 const late=harness();late.choose();late.preview();await late.flush();late.choose();await late.reply(0,late.result());assert.equal(late.node('option-activity-confirm').hidden,true);
 const large=harness();large.choose({name:'huge.csv',size:1000001,text:async()=>{throw Error('must not read')}});await large.preview();assert.equal(large.requests.length,0);
 const bad=harness();bad.choose();bad.preview();await bad.flush();bad.requests[0].reject(Error('Wrong account'));await bad.flush();assert.match(bad.node('option-activity-message').textContent,/Wrong account/);assert.equal(bad.node('option-activity-save').disabled,true);
 const unknown=harness();unknown.choose();unknown.preview();await unknown.flush();await unknown.reply(0,unknown.result());unknown.ack();unknown.save();unknown.requests[1].reject(Error('network lost'));await unknown.flush();unknown.save();assert.equal(unknown.requests.length,2);assert.match(unknown.node('option-activity-message').textContent,/Refresh saved evidence/);
 const conflict=harness();conflict.choose();conflict.preview();await conflict.flush();const c=conflict.result();c.rows[0].conflict=true;c.conflicting_events=1;await conflict.reply(0,c);assert.match(conflict.node('option-activity-rows').children[0].textContent,/excluded from totals/);
 console.log('Option activity UI passed: lazy reads, duplicate guards, explicit acknowledgement, expired/changed files, safe text, conflicts, unknown-save handling and no activation requests.');
})().catch(e=>{console.error(e);process.exit(1);});
