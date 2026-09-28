const assert=require('node:assert/strict'),C=require('../static/companion_context.js');
const d={as_of:new Date().toISOString(),context:{}},c={event_id:'a',as_of:d.as_of,what:'Waiting',why:'P&L missing',next:'Verify account'};
assert(C.fresh(d));assert(!C.fresh({...d,as_of:new Date(Date.now()-91000).toISOString()}));assert(!C.fresh({...d,as_of:new Date(Date.now()+6000).toISOString()}));
let h=C.record([],c);assert.equal(C.record(h,c),h,'unchanged polls retain the same history');
h=JSON.parse(JSON.stringify(h));assert.equal(C.record(h,c).length,1,'reload deduplicates');
assert.equal(C.record(h,{...c,event_id:'b',why:'Connection restored'}).length,2);
for(let i=0;i<60;i++)h=C.record(h,{...c,event_id:String(i)});assert.equal(h.length,40);
console.log('Companion timestamps, changed evidence, reload deduplication and bounded history passed.');

// A slower explanation response must never overwrite current broker status.
const vm=require('node:vm'),fs=require('node:fs'),nodes={},events={};
const element=()=>({textContent:'',dataset:{},replaceChildren(){},append(){},setAttribute(){},addEventListener(){}});
const node=id=>nodes[id]??=element();
for(const id of ['operating-risk','operating-exposure'])Object.defineProperty(node(id),'textContent',{get:()=> 'Fresh state',set:()=>{throw Error('Explanation overwrote current account state');}});
vm.runInNewContext(fs.readFileSync('static/companion_context.js','utf8'),{
 document:{getElementById:node,createElement:element,addEventListener(){}},
 window:{addEventListener:(name,fn)=>events[name]=fn},localStorage:{getItem(){return null;},setItem(){},length:0},
 setTimeout(){return 1;},clearTimeout(){},Date,
});
events['desk:day']({detail:{as_of:new Date().toISOString(),context:{...c,scope:'fixture',as_of:new Date().toISOString(),
 valid_for_seconds:15,operational:{summary:'Old missing P&L',exposure:'Old exposure'},
 quality:{execution:{detail:'Prior account checks'},data:{state:'unavailable'}},blockers:[],recall:{available:false,note:'No receipt'}}}});
events['desk:day-unavailable']();
assert.equal(node('operating-risk').textContent,'Fresh state');
console.log('Companion poll and failure cannot replace the authoritative account header.');
