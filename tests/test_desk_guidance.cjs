const assert=require('node:assert/strict');
const {descriptions,liveStatus}=require('../static/desk_guidance.js');
const state={config:{mode:'live_manual'},broker:{connected:true},broker_book:{ok:true,paper_mode:false,risk_ready:true,day_pnl_usd:0},loop:{rth_ok:true,outside_rth:false}};
assert.match(liveStatus(state,1000),/not permission/);
assert.match(liveStatus(state,30000),/stale/);
assert.match(liveStatus({...state,loop:{}},1000),/closed or unverified/);
assert.match(liveStatus({...state,broker:{connected:false}},1000),/not been verified/);
assert.match(liveStatus({...state,broker_book:{...state.broker_book,paper_mode:true}},1000),/not been verified/);
for(const day_pnl_usd of [null,undefined,NaN,Infinity,'0'])assert.match(liveStatus({...state,broker_book:{...state.broker_book,day_pnl_usd}},1000),/incomplete/);
assert.match(liveStatus({...state,config:{mode:'auto_live'}},1000),/not live manual/);
assert.match(descriptions.close.join(' '),/does not open a new short/);
assert.match(descriptions.long_put.join(' '),/different from short-selling/);
assert.match(descriptions.call_credit.join(' '),/lower-strike call.*higher-strike call/);
assert.match(descriptions.put_credit.join(' '),/higher-strike put.*lower-strike put/);
console.log('Guidance: missing/stale/closed/non-manual/broker-paper status and call/put/open/close distinctions passed.');
const fs=require('node:fs'),vm=require('node:vm'),handlers={};let prevented=0,focused=0;
const target={tagName:'SECTION',parentElement:null,scrollIntoView(){},setAttribute(){},focus(){focused++;},closest(){return null;}};
vm.runInNewContext(fs.readFileSync('static/desk_guidance.js','utf8'),{
 document:{hidden:true,getElementById:id=>id==='local'?target:null,querySelectorAll:()=>[],addEventListener:(k,f)=>handlers[k]=f},
 window:{addEventListener(){},dispatchEvent(){}},matchMedia:()=>({matches:true}),clearTimeout(){},CustomEvent:class{},
});
const click=id=>handlers.click({target:{closest:()=>({dataset:{guideJump:id}})},preventDefault(){prevented++;}});
click('desk-health');assert.equal(prevented,0,'cross-page links retain normal browser navigation');
click('local');assert.equal(prevented,1);assert.equal(focused,1,'same-page guidance still focuses its section');
