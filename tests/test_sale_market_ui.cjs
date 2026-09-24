// Actual scanner controller with isolated responses; no execution URLs allowed.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/sale_market.js','utf8');
const nodes={},events={},windowEvents={},dispatched=[],calls=[],intervals=new Map();let next=0;
function node(id){return nodes[id]??=( {id,value:'',hidden:false,disabled:false,innerHTML:'',textContent:'',listeners:{},
 addEventListener(k,fn){this.listeners[k]=fn;},setAttribute(k,v){this[k]=v;},querySelectorAll(){return[];},
 scrollIntoView(){this.scrolled=true;},focus(){this.focused=true;},dispatchEvent(e){this.event=e.type;}});}
for(const m of source.matchAll(/\$\('([^']+)'\)/g))node(m[1]);for(const a of ['start','resume','pause'])node('sale-market-'+a);
let fields=[['lens','pullbacks'],['kind','all'],['minimum','0']],state='running';
const row={symbol:'TEST',name:'<b>Company</b>',close:50,below_typical_pct:10,below_observed_high_pct:20,observed_sessions:250,return_20_sessions_pct:5,
 completed_session_relative_volume:2,avg_daily_dollar_volume:2e7,as_of:'2026-09-23',retrieved_at:'2026-09-24T04:00:00Z'};
const context={document:{hidden:false,getElementById:node,querySelectorAll:()=>[],addEventListener:(k,fn)=>events[k]=fn},
 window:{addEventListener:(k,fn)=>windowEvents[k]=fn,dispatchEvent:e=>dispatched.push(e)},URLSearchParams,Date,Number,JSON,
 FormData:class{constructor(){return fields;}},Event:class{constructor(type){this.type=type;}},CustomEvent:class{constructor(type,opts){this.type=type;this.detail=opts.detail;}},
 AbortController,setTimeout,clearTimeout,
 setInterval(fn){intervals.set(++next,fn);return next;},clearInterval:id=>intervals.delete(id),
 fetch:async(url,opts)=>{assert.ok(url.startsWith('/api/markets/scanner'),url);calls.push({url,opts});if(opts.method==='POST')state=JSON.parse(opts.body).action==='pause'?'paused':'running';
  return {ok:true,json:async()=>({ok:true,state,message:'Checking',results:[row],page:1,page_size:40,matched:1,total:11000,checked:10,valid:9,excluded:1,failed:0,pending:10990,
   as_of:'2026-09-23',directory_at:'2026-09-24T04:00:00Z',coverage_pct:.08,lens:new URL(url,'http://local').searchParams.get('lens')||'pullbacks'})};}
};
vm.runInNewContext(source,context);
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 await settle();assert.equal(nodes['sale-market-start'].disabled,true);assert.match(nodes['sale-market-warning'].textContent,/Partial coverage/);
 assert.match(nodes['sale-market-results'].innerHTML,/&lt;b&gt;Company&lt;\/b&gt;/);assert.match(nodes['sale-market-results'].innerHTML,/Below typical/);
 fields=[['lens','momentum'],['kind','all']];nodes['sale-market-filters'].listeners.change({target:{type:'radio'}});await settle();assert.equal(nodes['sale-discount-filter'].hidden,true);assert.match(nodes['sale-market-results'].innerHTML,/20-session return/);
 nodes['sale-market-results'].listeners.click({target:{closest:()=>({dataset:{saleRow:'0'}})}});
 const before=calls.length;nodes['sale-market-detail'].listeners.click({target:{closest:()=>({dataset:{scanUse:'options'}})}});
 assert.equal(dispatched.at(-1).type,'scanner:options');assert.equal(dispatched.at(-1).detail.symbol,'TEST');assert.equal(calls.length,before);
 nodes['sale-market-detail'].listeners.click({target:{closest:()=>({dataset:{scanUse:'cost'}})}});assert.equal(nodes['cost-symbol'].value,'TEST');assert.equal(nodes['cost-symbol'].event,'input');assert.equal(calls.length,before);
 await nodes['sale-market-pause'].listeners.click({currentTarget:nodes['sale-market-pause']});await settle();assert.equal(nodes['sale-market-resume'].hidden,false);
 context.document.hidden=true;events.visibilitychange();assert.equal(intervals.size,0);context.document.hidden=false;events.visibilitychange();await settle();assert.equal(intervals.size,1);
 windowEvents.pagehide();assert.equal(intervals.size,0);
 console.log('Shared scanner UI: partial coverage, escaping, lens filters, pause controls, safe cross-feature selection and hidden-page polling passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
