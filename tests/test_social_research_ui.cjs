const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const nodes=new Map(),pending=[],requests=[];
class Element{
 constructor(tag='div'){this.tag=tag;this.handlers={};this.children=[];this.value='';this.disabled=false;this.textContent='';}
 append(...x){this.children.push(...x);}
 replaceChildren(...x){this.children=x;}
 addEventListener(e,fn){this.handlers[e]=fn;}
 setAttribute(k,v){this[k]=v;}
}
const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const context={document:{getElementById:get,createElement:t=>new Element(t),hidden:false,addEventListener(){}},
 window:{addEventListener(){}},AbortController,Date,console,
 setTimeout:()=>1,clearTimeout(){},fetch:(url,options)=>{requests.push(url);return new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}));}};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const reply=(data,status=200)=>({ok:status<400,json:async()=>data});
const text=node=>node.textContent+' '+node.children.map(text).join(' ');
const fixture={ok:true,as_of:new Date().toISOString(),social:{window_ends_at:new Date().toISOString(),pulse:[{ticker:'AAPL',sources:['reddit'],post_mentions:1,headline_mentions:1,unique_authors:1,bullish:1,bearish:0,uncertain:0,items:[{excerpt:'<script>bad()</script>',permalink:'javascript:bad()'}]}]}};
(async()=>{
 const source=fs.readFileSync('static/market_watch.js','utf8');assert(!source.includes('Â·'));
 vm.runInNewContext(source,context);
 assert.deepEqual(requests,['/api/market-watch']);pending.shift().resolve(reply(fixture));await flush();
 assert.equal(pending.length,0,'Rendering social posts must not fetch prices automatically');
 assert(text(get('mw-social')).includes('1 posts / 1 headlines'));
 const first=get('mw-social').children[0].children[0].children[0];
 assert.equal(first.children[2].children[0].tag,'span','Unsafe source URLs must never become links');
 get('mw-price-symbol').value='AAPL';get('mw-price-form').handlers.submit({preventDefault(){}});
 assert.equal(pending.length,1);assert(get('mw-price-check').disabled);
 assert(get('mw-price-result').textContent.startsWith('Checking'));
 pending.shift().resolve(reply({price:123.45,source:'Fixture',market_time:new Date(Date.now()-3600000).toISOString()}));await flush();
 assert(get('mw-price-result').textContent.includes('old or unverified time'));
 assert(!get('mw-price-check').disabled);
 get('mw-price-form').handlers.submit({preventDefault(){}});
 assert(!get('mw-price-result').textContent.includes('123.45'),'Old price cleared during a new check');
 pending.shift().resolve(reply({error:'Provider cooling down'},429));await flush();
 assert.equal(get('mw-price-result').textContent,'Provider cooling down');assert(!get('mw-price-check').disabled);
 get('mw-price-symbol').value='../private';get('mw-price-form').handlers.submit({preventDefault(){}});
 assert.equal(pending.length,0);
 assert(requests.every(url=>url.startsWith('/api/market-watch')),'Research controls must never call trading routes');
 console.log('PASS: real controller render, no automatic price reads, unsafe links, stale price, failed refresh, invalid ticker, no trading routes');
})().catch(e=>{console.error(e);process.exitCode=1;});
