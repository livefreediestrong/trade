// Real frontend controller with isolated fake paper responses; all broker URLs forbidden.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/options.js','utf8');
const ids=[...source.matchAll(/\$\('([^']+)'\)/g)].map(m=>m[1]);
const nodes=Object.fromEntries(ids.map(id=>[id,{id,value:'',disabled:false,hidden:false,innerHTML:'',textContent:'',listeners:{},addEventListener(k,f){(this.listeners[k]??=[]).push(f);},insertAdjacentHTML(_,s){this.innerHTML+=s;},scrollIntoView(){this.scrolled=true;},replaceChildren(){this.innerHTML='';}}]));
// Match the DOM's mutually replacing textContent/innerHTML behavior.
let reviewHtml='',reviewText='';Object.defineProperties(nodes['option-review'],{
 innerHTML:{get:()=>reviewHtml,set:value=>{reviewHtml=value;reviewText='';}},
 textContent:{get:()=>reviewText,set:value=>{reviewText=value;reviewHtml='';}}
});
const defaults={'option-symbol':'SPY','option-expiry':'2026-09-25','option-strategy':'long_put','option-long':'100','option-short':'','option-count':'1','option-budget':'200','option-fee':'0.65'};
for(const [id,value] of Object.entries(defaults))nodes[id].value=value;nodes['option-short'].disabled=true;
const listeners={};
const calls=[],events=[],timers=new Map();let serial=0,nextPreview=null,releasePreview=null;
let deferFill=false,releaseFill,rejectFill;
const plan={symbol:'SPY',expiry:'2026-09-25',strategy:'long_put',right:'P',long_strike:100,short_strike:null,contracts:1};
function response(action='open',review_id='review-1') {return {review_id,action,plan,expires_in_seconds:30,blockers:[],estimate:{entry_net_usd:120,maximum_loss_usd:121.3,maximum_gain_usd:9878.7,break_even_at_expiry:98.787,fee_per_side_usd:.65,close_value_usd:110},close_pnl_estimate:-11.3,quote:{legs:[{action:'BUY',strike:100,right:'P',bid:1.1,ask:1.2,market_data_type:1}]}};}
const context={document:{getElementById:id=>nodes[id]},window:{dispatchEvent:e=>events.push(e),addEventListener:(name,fn)=>listeners[name]=fn},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},matchMedia:()=>({matches:true}),AbortController,Date,Intl,
 setTimeout(fn,ms){timers.set(++serial,{fn,ms});return serial;},clearTimeout(id){timers.delete(id);},
 fetch:async(url,options)=>{assert.ok(url.startsWith('/api/options'),url);calls.push({url,body:options.body&&JSON.parse(options.body)});let result;
  if(url==='/api/options')result={book:{free_cash:1000,positions:[],fills:[],realized_pnl:0},paper_enabled:true};
  else if(url==='/api/options/preview')result=await new Promise(resolve=>{releasePreview=resolve;});
  else if(url==='/api/options/close-preview')result=nextPreview;
  else if(url==='/api/options/paper-fill')result=deferFill?await new Promise((resolve,reject)=>{releaseFill=resolve;rejectFill=reject;}):{fill:{action:'close'},duplicate:false};
  else throw Error('Unexpected request '+url);
  return {ok:true,json:async()=>result};}
};
vm.runInNewContext(source,context);
const settle=async()=>{for(let i=0;i<10;i++)await new Promise(r=>setImmediate(r));};
function fire(id,type,e={}){for(const fn of nodes[id].listeners[type]||[])fn(e);}
const submit={id:'submit',disabled:false};
const preview=()=>fire('options-form','submit',{preventDefault(){},submitter:submit});
(async()=>{
 await settle();assert.match(nodes['option-positions'].innerHTML,/Nothing to sell to close/);
 preview();await settle();fire('options-form','input');releasePreview(response());await settle();assert.equal(nodes['option-confirm'].disabled,true);assert.doesNotMatch(nodes['option-review'].innerHTML,/Open new/);
 preview();await settle();releasePreview(response());await settle();assert.equal(nodes['option-confirm'].disabled,false);assert.match(nodes['option-review'].innerHTML,/buy to open 100/);assert.match(nodes['option-review'].innerHTML,/\$121.30/);assert.equal(nodes['option-confirm'].textContent,'Confirm paper opening');
 fire('options-form','change');assert.equal(nodes['option-confirm'].disabled,true);
 nextPreview=response('close');const close={disabled:false,dataset:{optionClose:'position-1'}};
 fire('option-positions','click',{target:{closest:()=>close}});await settle();assert.equal(nodes['option-confirm'].textContent,'Confirm paper close');assert.match(nodes['option-review'].innerHTML,/sell to close 100/);assert.match(nodes['option-review'].innerHTML,/Sell to close/);assert.doesNotMatch(nodes['option-review'].innerHTML,/buy to open/);
 const expiry=[...timers.values()].find(t=>t.ms===30000);assert.ok(expiry);expiry.fn();assert.equal(nodes['option-confirm'].disabled,true);assert.match(nodes['option-review'].innerHTML,/Review expired/);
 nextPreview={...response('close',null),estimate:null,quote:null,blockers:['Quotes unavailable']};fire('option-positions','click',{target:{closest:()=>close}});await settle();assert.equal(nodes['option-confirm'].disabled,true);assert.match(nodes['option-review'].innerHTML,/Quotes unavailable/);
 nextPreview=response('close');fire('option-positions','click',{target:{closest:()=>close}});await settle();fire('option-confirm','click',{currentTarget:nodes['option-confirm']});fire('option-confirm','click',{currentTarget:nodes['option-confirm']});await settle();assert.equal(calls.filter(c=>c.url==='/api/options/paper-fill').length,1);assert.match(nodes['option-review'].textContent,/No broker order was sent/);
 preview();await settle();const beforeDiscovery=calls.length;
 listeners['scanner:options']({detail:{symbol:'AAPL'}});
 assert.equal(nodes['option-symbol'].value,'AAPL');assert.equal(nodes['option-expiry'].value,'');assert.equal(nodes['option-long'].value,'');
 assert.equal(nodes['option-confirm'].disabled,true);assert.equal(calls.length,beforeDiscovery,'Choosing discovery never requests a fill or chain');
 releasePreview(response());await settle();assert.equal(nodes['option-confirm'].disabled,true,'Old-symbol review must stay invalid');
 assert.match(nodes['option-chain-status'].textContent,/availability and prices are unverified/);
 for(const newerAction of ['open','close']){
  nextPreview=response('close','A');fire('option-positions','click',{target:{closest:()=>close}});await settle();
  deferFill=true;fire('option-confirm','click',{currentTarget:nodes['option-confirm']});await settle();
  if(newerAction==='open'){preview();await settle();releasePreview(response('open','B'));}
  else{nextPreview=response('close','B');fire('option-positions','click',{target:{closest:()=>close}});}
  await settle();const visible=nodes['option-review'].innerHTML;assert.ok(visible);assert.equal(nodes['option-confirm'].disabled,false);
  releaseFill({fill:{action:'close'},duplicate:false});await settle();
  assert.equal(nodes['option-review'].innerHTML,visible,'late A fill must preserve exact B review');assert.equal(nodes['option-confirm'].disabled,false);
  deferFill=false;fire('option-confirm','click',{currentTarget:nodes['option-confirm']});await settle();assert.equal(calls.filter(c=>c.url==='/api/options/paper-fill').at(-1).body.review_id,'B');
 }
 nextPreview=response('close','A-error');fire('option-positions','click',{target:{closest:()=>close}});await settle();deferFill=true;
 fire('option-confirm','click',{currentTarget:nodes['option-confirm']});await settle();nextPreview=response('close','B-error');fire('option-positions','click',{target:{closest:()=>close}});await settle();
 const visible=nodes['option-review'].innerHTML;rejectFill(Error('Old fill unavailable'));await settle();assert.equal(nodes['option-review'].innerHTML,visible);assert.equal(nodes['option-confirm'].disabled,false);
 nextPreview=response('open','all-loss');Object.assign(nextPreview.estimate,{break_even_at_expiry:null,break_even_possible:false,break_even_note:'No attainable break-even after the assumed round-trip fees.'});
 fire('option-positions','click',{target:{closest:()=>close}});await settle();assert.match(nodes['option-review'].innerHTML,/Not attainable/);assert.match(nodes['option-review'].innerHTML,/No attainable break-even/);
 assert.ok(events.some(e=>e.type==='options:guide'&&/expired/.test(e.detail.text)));
 console.log('Options UI: stale response/error rejection, delayed fill versus newer open/close review, exact token, impossible break-even, input invalidation, labels/costs, expiry and duplicate-click prevention passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
