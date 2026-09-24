// Exercise the actual app-to-companion event boundary without feeds or orders.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('  function presentBuzzAlert('),end=source.indexOf('\n  }',start)+4;
assert.ok(start>=0&&end>start);
const calls=[];let accepted=true,handled=true;
const context={alertDedupe:()=>accepted,toast:(...args)=>calls.push(['toast',...args]),playAlertBeep:()=>calls.push(['sound']),
 CustomEvent:class{constructor(type,options){this.type=type;Object.assign(this,options);}},window:{dispatchEvent(event){calls.push(['event',event]);return !handled;}}};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const candidate={ticker:'SPY',source:'Stocktwits',mentions:7,score:7},buzz={stale:false,cached_at:new Date().toISOString()};
context.presentBuzzAlert(candidate,buzz);assert.equal(calls.length,1);assert.equal(calls[0][1].type,'moss:buzz');assert.equal(calls[0][1].cancelable,true);assert.equal(calls[0][1].detail.cachedAt,buzz.cached_at);assert.equal(calls[0][1].detail.stale,false);
calls.length=0;handled=false;context.presentBuzzAlert(candidate,buzz);assert.equal(calls.length,3);assert.equal(calls[1][0],'toast');assert.equal(calls[2][0],'sound');
calls.length=0;accepted=false;context.presentBuzzAlert(candidate,buzz);assert.equal(calls.length,0);
calls.length=0;accepted=true;context.presentBuzzAlert(candidate,{});assert.equal(calls[0][1].detail.stale,true,'missing freshness must not animate Fox');
console.log('Buzz event integration: source provenance, cancelable delivery, no duplicate toast/sound when Fox handles it, unavailable-companion fallback and deduplication passed.');
