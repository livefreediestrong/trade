const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const nodes=new Map(),pending=[],requests=[];
class Element{
 constructor(tag='div'){this.tag=tag;this.handlers={};this.children=[];this.value='';this.disabled=false;this.textContent='';}
 append(...x){this.children.push(...x);}
 replaceChildren(...x){this.children=x;this.textContent='';}
 addEventListener(e,fn){this.handlers[e]=fn;}
 setAttribute(k,v){this[k]=v;}
}
const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const text=n=>n.textContent+' '+n.children.map(text).join(' ');
const context={document:{getElementById:get,createElement:t=>new Element(t),hidden:false,addEventListener(){}},
 window:{addEventListener(){}},AbortController,Date,console,setTimeout:()=>1,clearTimeout(){},
 fetch:url=>{requests.push(url);return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const reply=data=>({ok:true,json:async()=>data});
const fixture={ok:true,community:{enabled:true,note:'Overlapping WSB samples; counts are not added.',providers:{
 apewisdom:{name:'ApeWisdom',ok:true,docs:'https://apewisdom.io/api/',fetched_at:new Date().toISOString(),rows:[{symbol:'MU',count:93,previous_mentions:0,rank:1,previous_rank:2}]},
 tradestie:{name:'Tradestie',ok:true,docs:'javascript:bad()',rows:[{symbol:'<script>bad()</script>',count:18,tone:'bearish'}]}}}};
(async()=>{
 vm.runInNewContext(fs.readFileSync('static/market_watch.js','utf8'),context);
 pending.shift().resolve(reply(fixture));await flush();
 assert(text(get('mw-apewisdom')).includes('0 mentions'),'Known zero is not unknown');
 assert(text(get('mw-tradestie')).includes('Negative'));
 assert(text(get('mw-tradestie')).includes('<script>bad()</script>'),'Provider text is text only');
 assert.equal(get('mw-tradestie-status').children[0].tag,'span','Unsafe URL rejected');
 assert(text(get('mw-apewisdom-status')).includes('source update time unavailable'));
 assert(!text(get('mw-sources')).includes('connected'),'HTTP success is not verified freshness');
 get('mw-refresh').handlers.click();pending.shift().resolve(reply({ok:true,community:{enabled:true,providers:{
  apewisdom:{name:'ApeWisdom',ok:false,error:'Source rate limit',rows:[{symbol:'OLD',count:999}]}
 }}}));await flush();
 assert(!text(get('mw-apewisdom')).includes('OLD')&&!text(get('mw-apewisdom')).includes('MU'),'Error clears prior rows');
 assert(text(get('mw-apewisdom-status')).includes('Source rate limit'));
 get('mw-refresh').handlers.click();pending.shift().resolve(reply(fixture));await flush();
 get('mw-refresh').handlers.click();pending.shift().reject(Error('Network unavailable'));await flush();
 assert(!text(get('mw-apewisdom')).includes('MU'),'Whole snapshot failure clears summaries');
 assert(text(get('mw-community-note')).includes('Refresh failed'));
 assert(requests.every(url=>url.startsWith('/api/market-watch')),'Only research reads');
 console.log('PASS: aggregate controller, unknown freshness, zero baseline, literal text, unsafe links, source failure, whole-refresh failure, no trading actions');
})().catch(e=>{console.error(e);process.exitCode=1;});
