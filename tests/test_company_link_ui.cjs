const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const nodes=new Map(),listeners={},pending=[],events=[];
class Element {
 constructor(id=''){this.id=id;this.children=[];this.value='';this.hidden=false;this.dataset={};this.handlers={};}
 append(...x){this.children.push(...x);}
 before(n){nodes.set(n.id,n);}
 replaceChildren(...x){this.children=x;}
 addEventListener(name,fn){this.handlers[name]=fn;}
}
for(const id of ['company-check','company-check-form','company-check-ticker','company-check-result','company-check-refresh','company-check-go','company-check-status'])nodes.set(id,new Element(id));
const context={document:{getElementById:id=>nodes.get(id),createElement:()=>new Element(),createTextNode:text=>text},Node:Element,
 window:{addEventListener:(name,fn)=>listeners[name]=fn,dispatchEvent:e=>events.push(e)},
 location:{search:''},URLSearchParams,AbortController,CustomEvent:class{constructor(type,opts){this.type=type;Object.assign(this,opts);}},
 fetch:(url,opts)=>new Promise((resolve,reject)=>pending.push({url,opts,resolve,reject})),setTimeout:()=>1,clearTimeout:()=>{}};
const settle=()=>new Promise(r=>setImmediate(r)),reply=ticker=>({ok:true,json:async()=>({ok:true,ticker,name:ticker+' company',checked_at:'2026-09-25T20:00:00Z',sections:{}})});
(async()=>{
 vm.runInNewContext(fs.readFileSync('static/company_check.js','utf8'),context);
 listeners['desk:research-symbol']({detail:{ticker:'AAPL',source:'chart'}});
 listeners['desk:research-symbol']({detail:{ticker:'MSFT',source:'chart'}});
 assert.equal(pending.length,2);assert(pending[0].opts.signal.aborted);assert.equal(nodes.get('company-check-result').hidden,true);
 pending[1].resolve(reply('MSFT'));await settle();
 assert.match(nodes.get('company-check-status').textContent,/MSFT/);assert.equal(nodes.get('company-check-result').hidden,false);
 pending[0].resolve(reply('AAPL'));await settle();
 assert.match(nodes.get('company-check-status').textContent,/MSFT/,'late old-company success cannot overwrite the selected company');
 nodes.get('company-talk').handlers.click();assert.equal(events.at(-1).detail.ticker,'MSFT');
 listeners['desk:research-symbol']({detail:{ticker:'AAPL',source:'chart'}});
 listeners['desk:research-symbol']({detail:{ticker:'NVDA',source:'chart'}});
 pending[2].reject(Error('old request failed'));await settle();
 assert.match(nodes.get('company-check-status').textContent,/Checking NVDA/);assert.equal(nodes.get('company-check-go').disabled,true);
 pending[3].resolve(reply('NVDA'));await settle();assert.equal(nodes.get('company-check-go').disabled,false);
 assert.equal(events.filter(e=>e.type==='desk:research-symbol').length,0,'linked selections do not loop back');
 console.log('Company linking: rapid changes, late success/error, busy controls and matching chat context passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
