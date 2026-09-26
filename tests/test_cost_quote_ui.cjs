const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/companion.js','utf8'),nodes={};
function $(id){return nodes[id]??={value:'',textContent:'',innerHTML:'',listeners:{},addEventListener(k,f){this.listeners[k]=f}};}
let quote,estimate;
const context={$: $,val:id=>$(id).value,txt:(id,text)=>$(id).textContent=text,costRevision:0,money:n=>n==null?'Unknown':`$${n}`,esc:String,task:(_button,_target,fn)=>fn(),api:async path=>path.includes('/quote/')?{quote}:estimate};
const start=source.indexOf("  $('cost-form')?.addEventListener('submit'"),end=source.indexOf("  $('cost-price')?.addEventListener('input'",start);assert(start>=0&&end>start);
vm.runInNewContext(source.slice(start,end),context);
(async()=>{
 $('cost-symbol').value='SPY';$('cost-price').value='600';$('cost-exit').value='610';
 for(const price of [null,undefined,0,-1,NaN,Infinity,'invalid']){
  quote={price,source:'unavailable',fresh:false,market_time:null};await $('cost-quote').listeners.click({currentTarget:{}});
  assert.equal($('cost-price').value,'600');assert.equal($('cost-exit').value,'610');assert.match($('cost-result').textContent,/No new price/);assert.match($('cost-quote-status').textContent,/unverified assumptions/);
 }
 for(const fresh of [false,true]){
  quote={price:123.4567,source:'fixture',fresh,market_time:'2026-09-24T12:00:00Z'};await $('cost-quote').listeners.click({currentTarget:{}});
  assert.equal($('cost-price').value,'123.4567');assert.equal($('cost-exit').value,'123.4567');assert.match($('cost-result').textContent,/Price updated/);
  assert.match($('cost-quote-status').textContent,fresh?/recent observation/:/old\/unverified/);
 }
 estimate={shares:.1,direction:'short',break_even_price:null,break_even_possible:false,note:'No attainable nonnegative break-even under these costs.'};
 $('cost-form').listeners.submit({preventDefault(){},submitter:{}});await new Promise(resolve=>setImmediate(resolve));assert.match($('cost-result').innerHTML,/Not attainable/);assert.match($('cost-result').innerHTML,/No attainable/);
 console.log('Cost UI: unavailable/invalid/zero/negative prices retain explicitly labeled assumptions, finite stale/fresh quotes update, and impossible break-even is explicit.');
})().catch(e=>{console.error(e);process.exitCode=1});
