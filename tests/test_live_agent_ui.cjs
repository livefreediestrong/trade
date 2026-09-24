// Run the actual browser controller with deferred in-memory HTTP responses.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function harness(){
 const nodes={},listeners={},requests=[],timers=[];let clock=Date.now(),kind='limit';
 const node=id=>nodes[id]??={value:'',textContent:'',dataset:{},disabled:false,handlers:{},addEventListener(n,f){this.handlers[n]=f;},replaceChildren(...children){this.children=children;}};
 node('live-agent-form').querySelector=selector=>{
  const value=selector.match(/value="([^"]+)"/)?.[1];
  return value?{set checked(on){if(on)kind=value;}}:{value:kind};
 };
 vm.runInNewContext(fs.readFileSync('static/live_agent.js','utf8'),{
  document:{hidden:false,getElementById:node,createElement:()=>({}),addEventListener(n,f){listeners[n]=f;}},
  window:{addEventListener(){}},Date:class extends Date{static now(){return clock}},
  AbortController,setTimeout:(f,ms)=>{if(ms===10000)timers.push(f);return timers.length;},clearTimeout(){},
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))
 });
 const policy={symbols:['TEST'],interval_sec:120,order_type:'limit',max_order_usd:100,max_daily_loss_usd:20,max_orders_per_day:3,max_research_per_day:100,model_budget_usd:5,limit_offset_bps:5,min_confidence:.6,max_quote_age_sec:30};
 const state=(extra={})=>({ok:true,configured:true,enabled:false,policy,revision:'rev1',identity:{broker:'ibkr',account_id:'TEST1234',paper_mode:false},message:'Paused',today:{research:1,orders:0},events:[],...extra});
 const reply=async(i,s,ok=true)=>{requests[i].resolve({ok,json:async()=>s});for(let n=0;n<8;n++)await Promise.resolve();};
 const ack=value=>{node('live-agent-confirm').value=value;node('live-agent-confirm').handlers.input();};
 const poll=async()=>{timers.at(-1)();await Promise.resolve();};
 const click=id=>node(id).handlers.click();
 return {node,requests,state,reply,ack,poll,click,advance:ms=>{clock+=ms},edit:()=>node('live-agent-form').handlers.input(),save:()=>node('live-agent-form').handlers.submit({preventDefault(){}})};
}
(async()=>{
 const h=harness();assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/api/live-agent');
 await h.reply(0,h.state());assert.equal(h.node('live-agent-start').disabled,true);
 h.ack('PAPER');assert.equal(h.node('live-agent-start').disabled,true);h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,false);
 h.click('live-agent-start');h.click('live-agent-start');assert.equal(h.requests.length,2);
 const start=JSON.parse(h.requests[1].options.body);assert.equal(start.identity.account_id,'TEST1234');assert.equal(start.confirm,'REAL');assert.equal(start.revision,'rev1');
 await h.reply(1,h.state({enabled:true}));assert.equal(h.node('live-agent-start').disabled,true);
 h.click('live-agent-pause');await h.reply(2,h.state());assert.match(h.node('live-agent-result').textContent,/paused/);
 h.node('la-max_order_usd').value='321';h.edit();await h.poll();await h.reply(3,h.state());
 assert.equal(h.node('la-max_order_usd').value,'321');h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,true);
 h.save();assert.equal(h.requests[4].url,'/api/live-agent/policy');assert.equal(JSON.parse(h.requests[4].options.body).policy.max_order_usd,321);
 // Editing during save must not overwrite the newer draft or enable start.
 h.node('la-max_order_usd').value='456';h.edit();await h.reply(4,h.state({revision:'rev2'}));
 assert.equal(h.node('la-max_order_usd').value,'456');assert.equal(h.node('live-agent-start').disabled,true);
 h.save();await h.reply(5,h.state({revision:'rev3',policy:{...h.state().policy,max_order_usd:456}}));
 h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,false);
 h.advance(26000);h.click('live-agent-start');assert.equal(h.requests.length,6);assert.equal(h.node('live-agent-start').disabled,true);
 await h.poll();await h.reply(6,h.state({revision:'rev3',identity:{broker:'ibkr',account_id:'OTHER',paper_mode:true}}));
 assert.equal(h.node('live-agent-confirm').value,'');h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,true);h.ack('PAPER');
 h.click('live-agent-start');h.requests[7].reject(Error('Lost acknowledgement'));for(let n=0;n<8;n++)await Promise.resolve();
 assert.match(h.node('live-agent-result').textContent,/will not retry automatically/);assert.equal(h.requests.length,8);
 assert.equal(h.node('live-agent-start').disabled,true);assert.equal(h.node('live-agent-pause').disabled,false);
 const conflict=harness();await conflict.reply(0,conflict.state());conflict.edit();await conflict.poll();await conflict.reply(1,conflict.state({revision:'other-window'}));conflict.save();
 assert.equal(JSON.parse(conflict.requests[2].options.body).revision,'rev1');
 await conflict.reply(2,{ok:false,error:'Policy changed in another window'},false);
 assert.match(conflict.node('live-agent-result').textContent,/another window/);
 console.log('Live agent UI: no automatic activation, exact account confirmation, duplicate suppression, dirty drafts, stale status, account changes and uncertain-request handling passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
