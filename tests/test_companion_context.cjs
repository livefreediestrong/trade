const assert=require('node:assert/strict'),C=require('../static/companion_context.js');
const d={as_of:new Date().toISOString(),context:{}},c={event_id:'a',as_of:d.as_of,what:'Waiting',why:'P&L missing',next:'Verify account'};
assert(C.fresh(d));assert(!C.fresh({...d,as_of:new Date(Date.now()-91000).toISOString()}));assert(!C.fresh({...d,as_of:new Date(Date.now()+6000).toISOString()}));
let h=C.record([],c);assert.equal(C.record(h,c),h,'unchanged polls retain the same history');
h=JSON.parse(JSON.stringify(h));assert.equal(C.record(h,c).length,1,'reload deduplicates');
assert.equal(C.record(h,{...c,event_id:'b',why:'Connection restored'}).length,2);
for(let i=0;i<60;i++)h=C.record(h,{...c,event_id:String(i)});assert.equal(h.length,40);
console.log('Companion timestamps, changed evidence, reload deduplication and bounded history passed.');
