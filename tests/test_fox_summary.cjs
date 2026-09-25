// Readiness presentation only; no broker, credentials, models or orders.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {tradingSummary}=require('../static/fox_workspace.js');
const now=Date.parse('2026-09-25T21:40:00Z');
const snapshot=()=>({ok:true,as_of:new Date(now).toISOString(),agent:{ok:true,configured:true,enabled:true,session_active:true,mode:'auto_live',market_open:true,phase:'enabled',identity:{broker:'ibkr',account_id:'TEST1234',paper_mode:false},policy:{max_order_usd:50,max_daily_loss_usd:10,max_orders_per_day:3}},broker:{age_sec:1,connected:true,risk_ready:true,day_pnl:0},brain:{provider:'gemini',brain_mode:'gemini',configured:true}});
let d=snapshot(),s=tradingSummary(d,now);
assert.equal(s.title,'On · monitoring for a setup');assert.equal(s.pnl,'$0.00');
assert.match(s.account,/ending 1234/);assert.doesNotMatch(s.account,/TEST1234/);
assert.match(s.explanation,/already on/);assert.doesNotMatch(JSON.stringify(s),/Ready to trade/);
d.agent.market_open=false;d.broker.day_pnl=null;d.broker.risk_ready=false;d.brain={provider:'jev',configured:false};s=tradingSummary(d,now);
assert.equal(s.title,'On · new trades blocked');
for(const word of ['profit/loss','JEV','market is closed'])assert.ok(s.checks.some(x=>x.includes(word)),word);
for(const value of [null,undefined,'0',NaN,Infinity]){d=snapshot();d.broker.day_pnl=value;assert.match(tradingSummary(d,now).pnl,/Missing/);}
for(const input of [null,{}, {...snapshot(),agent:{}}, {...snapshot(),as_of:'bad'}, {...snapshot(),as_of:new Date(now-31000).toISOString()}, {...snapshot(),as_of:new Date(now+6000).toISOString()}])assert.equal(tradingSummary(input,now).switch,'Unknown');
d=snapshot();d.broker.age_sec=25;assert.match(tradingSummary(d,now+6000).connection,/fresh/);
d=snapshot();d.broker.connected=false;assert.match(tradingSummary(d,now).title,/blocked/);
d=snapshot();d.agent.identity=null;assert.match(tradingSummary(d,now).title,/blocked/);
d=snapshot();d.agent.enabled=false;assert.equal(tradingSummary(d,now).switch,'Off');
d=snapshot();d.agent.session_active=false;assert.equal(tradingSummary(d,now).title,'On · session stopped');
d=snapshot();d.agent.identity.paper_mode=true;assert.match(tradingSummary(d,now).account,/Practice/);
d=snapshot();d.agent.market_open=false;assert.equal(tradingSummary(d,now).title,'On · waiting for market');
d=snapshot();d.brain.provider='mock';assert.match(tradingSummary(d,now).title,/blocked/);
d=snapshot();d.brain.health={state:'unavailable'};assert.match(tradingSummary(d,now).brain,/failed/);
d=snapshot();d.agent.policy.max_order_usd=1e9;assert.match(tradingSummary(d,now).limits,/1,000,000,000/);assert.ok(tradingSummary(d,now).checks.some(x=>x.includes('$1 billion')));
d=snapshot();d.agent.policy.max_daily_loss_usd=null;assert.match(tradingSummary(d,now).limits,/incomplete/);
// Actual controller: render, expiry, a failed read, and no trading writes.
const nodes={},requests=[],timers=[],listeners={};
const node=id=>nodes[id]??={textContent:'',dataset:{},addEventListener(){},replaceChildren(...children){this.children=children;}};
vm.runInNewContext(fs.readFileSync('static/fox_workspace.js','utf8'),{
 document:{hidden:false,getElementById:node,createElement:()=>({append(){}}),addEventListener(n,f){listeners[n]=f;}},
 window:{addEventListener(n,f){listeners[n]=f;}},Date:class extends Date{static now(){return now;}},AbortController,
 setTimeout:(f,ms)=>{timers.push({f,ms});return timers.length;},clearTimeout(){},
 fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))
});
async function settle(){for(let i=0;i<12;i++)await Promise.resolve();}
(async()=>{
 assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/fox-workspace');assert.equal(requests[0].options.method,undefined);
 requests[0].resolve({ok:true,json:async()=>snapshot()});await settle();assert.match(node('fox-start-title').textContent,/On/);
 timers.find(t=>t.ms===30000).f();assert.equal(node('fox-start-switch').textContent,'Unknown');
 timers.find(t=>t.ms===20000).f();assert.equal(requests.length,2);
 requests[1].reject(Error('network unavailable'));await settle();assert.equal(node('fox-start-switch').textContent,'Unknown');
 assert.ok(requests.every(r=>r.url==='/api/fox-workspace'&&!r.options.method));
 console.log('Fox summary passed: activation versus readiness, simultaneous blockers, zero versus missing P&L, stale/future data, account labels, saved limits, expiry, failed reads and GET-only polling.');
})().catch(e=>{console.error(e);process.exitCode=1;});
