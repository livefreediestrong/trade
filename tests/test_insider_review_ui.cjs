const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const nodes=new Map(),pending=[];
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.hidden=false;this.dataset={};this.handlers={};this.checked=false;}
 set id(id){this._id=id;nodes.set(id,this);}get id(){return this._id;}
 append(...x){this.children.push(...x);}before(n){nodes.set(n.id,n);}replaceChildren(...x){this.children=x;}
 addEventListener(name,fn){this.handlers[name]=fn;}setAttribute(name,value){this[name]=value;}
}
for(const id of ['company-check','company-check-form','company-check-ticker','company-check-result','company-check-refresh','company-check-go','company-check-status']){const n=new Element();n.id=id;}
const context={document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),createTextNode:text=>text},Node:Element,
 window:{addEventListener(){},dispatchEvent(){}},location:{search:''},URL,URLSearchParams,AbortController,
 CustomEvent:class{},fetch:(url,opts)=>new Promise(resolve=>pending.push({url,opts,resolve})),setTimeout:()=>1,clearTimeout(){}};
const all=(n=nodes.get('company-check-result'))=>[n,...(n.children||[]).flatMap(c=>typeof c==='object'?all(c):[])];
const text=n=>[n.textContent||'',...(n.children||[]).map(c=>typeof c==='object'?text(c):c)].join(' ');
const list=()=>all().find(n=>n.className==='cc-insider-list');
const result=amended=>({ok:true,ticker:'TEST',checked_at:'2026-09-26T20:00:00Z',sections:{insiders:{ok:true,summary:'Public disclosures only',
 amendments:amended?1:0,cluster:{status:amended?'needs_review':'observed',people:2,window_days:30,method:'Two distinct owners'},
 buys:{value:amended?null:5000,known_value:amended?null:5000,unpriced_rows:0},sells:{value:null,known_value:0,unpriced_rows:1},
 rows:[{name:'<img onerror=bad>',role:'Chief Financial Officer',officer:true,code:'P',action:'purchase (market or private)',date:'2026-09-10',filed:'2026-09-12',delay_days:2,value:5000,shares:100,security:'Common stock',ownership:'D',shares_after:500,holding_change_pct:25,
  plan_label:'Plan status unknown',footnotes:[{id:'F1',text:'<script>bad()</script>'}],url:'https://www.sec.gov/Archives/test.xml'},
 {name:'Director',role:'Director',code:'S',action:'sale',date:'2026-09-12',filed:'2026-09-15',value:null,shares:10,url:'javascript:bad()'},
 {name:'Officer',role:'Officer',officer:true,code:'F',action:'tax or exercise',date:'2026-09-15',filed:'2026-09-16',value:0,shares:4}],row_count:3},
 lobbying:{ok:true,summary:'Same-name filings, company link unverified',latest_reports:1,review_rows:1,duplicate_rows:1,
 coverage:[{year:2026,rows_read:25,pages_read:1,source_count:30,all_search_pages_read:false}],
 excluded_names:['<script>bad()</script> Apple Services'],warnings:['Second page unavailable'],
 records:[{registrant:'Example firm',year:2026,period:'first_quarter',filing_type_label:'Amendment',status:'latest_in_sample',
 client:'APPLE INC',client_id:2,registrant_id:3,amount_kind:'firm_income',amount:20000,issues:[],url:'https://lda.gov/filings/public/filing/example/print/'},
 {registrant:'<img onerror=bad>',year:2026,period:'first_quarter',filing_type_label:'Report',status:'older_in_sample',
 amount_kind:'organization_expenses',amount:null,issues:['Missing amount'],url:'https://lda.gov:888/invalid'}]},
 contracts:{ok:true,summary:'Company identity needs verification',total_12m:null,candidate_obligations_12m:12.5,
 matched_names:['APPLE INC'],excluded_names:['<img onerror=bad> APPLE JUICE'],
 new_awards:[{date:'2026-09-01',recipient:'APPLE INC',recipient_uei:'PUBLICID1234',amount:null,agency:'Public agency',what:'A reported award'}],
 coverage:{recipient_rows_read:10,award_rows_read:5,recipient_has_more:true,award_has_more:true}}}});
async function respond(i,data){pending[i].resolve({ok:true,json:async()=>data});for(let n=0;n<20;n++)await Promise.resolve();}
(async()=>{
 vm.runInNewContext(fs.readFileSync('static/company_check.js','utf8'),context);
 assert.equal(pending.length,0);nodes.get('company-check-ticker').value='TEST';nodes.get('company-check-form').handlers.submit({preventDefault(){}});
 await respond(0,result(false));assert.equal(list().children.length,3);
 assert.match(text(nodes.get('company-check-result')),/unknown total/);
 assert.match(text(list()),/<img onerror=bad>/);assert.match(text(list()),/<script>bad\(\)<\/script>/);
 assert.equal(all().filter(n=>n.href).length,2);assert.equal(all().find(n=>n.href).href,'https://www.sec.gov/Archives/test.xml');
 assert.match(text(nodes.get('company-check-result')),/Combined company spending: not calculated/);
 assert.match(text(nodes.get('company-check-result')),/Firm income: \$20\.0K/);
 assert.match(text(nodes.get('company-check-result')),/Organization expenses: amount not given/);
 assert.match(text(nodes.get('company-check-result')),/Older version in this sample/);
 assert.match(text(nodes.get('company-check-result')),/Partial coverage/);
 assert.match(text(nodes.get('company-check-result')),/Second page unavailable/);
 assert.equal(all().filter(n=>n.href?.includes(':888')).length,0);
 assert.match(text(nodes.get('company-check-result')),/Verified company total: unknown/);
 assert.match(text(nodes.get('company-check-result')),/PUBLICID1234/);
 assert.match(text(nodes.get('company-check-result')),/Company link unverified/);
 assert.match(text(nodes.get('company-check-result')),/More source results were not read/);
 assert.match(text(nodes.get('company-check-result')),/<img onerror=bad> APPLE JUICE/);
 assert.equal(all().filter(n=>n.tag==='img').length,0);
 const type=nodes.get('cc-insider-type'),officers=nodes.get('cc-insider-officers'),sort=nodes.get('cc-insider-sort');
 type.value='P';type.handlers.change();assert.equal(list().children.length,1);assert.match(text(list()),/purchase \(market or private\)/);
 type.value='S';type.handlers.change();officers.checked=true;officers.handlers.change();assert.equal(list().children.length,0);
 type.value='other';type.handlers.change();assert.equal(list().children.length,1);assert.match(text(list()),/tax or exercise/);
 type.value='all';officers.checked=false;sort.value='value';sort.handlers.change();assert.match(text(list().children[0]),/5\.0K/);
 nodes.get('company-check-refresh').handlers.click();await respond(1,result(true));assert.match(text(nodes.get('company-check-result')),/withheld for amendment review/);
 assert.equal(pending.length,2);assert(pending.every(r=>r.url.startsWith('/api/research/company?')&&!r.opts.method),'No activation, settings or order writes');
 console.log('Insider UI: filters, officer scope, ordering, amendment/missing amounts, source safety and read-only requests passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
