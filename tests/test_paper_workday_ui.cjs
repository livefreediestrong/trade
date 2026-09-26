// Exercise the actual controller with isolated HTTP responses, never the running desk.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {view}=require('../static/paper_workday_ui.js');
const settings={enabled:false,base_order_usd:5,max_order_usd:20,interval_sec:120,horizon_min:10,max_positions:4,max_total_exposure_pct:10,max_daily_loss_pct:2,max_trades_per_day:40,max_model_calls:10,model_budget_usd:1,flatten_before_close:true,personality:'stoic',symbols:['AAA'],universe:'focus'};
function harness(){
 let now=100000,status={settings:{...settings},today:{},active:false,busy:false,phase:'paused',error:null},deferred=null,failPost=false;
 const nodes={},listeners={},calls=[],timers=new Map();let serial=0;
 for(const id of ['moss-automation','paper-auto-state','paper-auto-next','paper-auto-amount','paper-auto-activity','paper-auto-limits','paper-auto-last','paper-auto-stamp','paper-auto-pause','paper-auto-review','paper-auto-refresh','paper-auto-start','paper-auto-confirm','paper-auto-feedback','paper-auto-review-text','paper-auto-dismiss','moss-paper-enabled']){
  nodes[id]={textContent:'',hidden:false,disabled:false,checked:false,addEventListener(n,f){this[n]=f;}};
 }
 const document={hidden:false,getElementById:id=>nodes[id],addEventListener(n,f){listeners[n]=f;}};
 class Clock extends Date{constructor(...a){super(...(a.length?a:[now]));}static now(){return now;}}
 const context={document,Date:Clock,Intl,AbortController,Event:class{constructor(type){this.type=type;}},window:{addEventListener(n,f){listeners[n]=f;},dispatchEvent(e){listeners[e.type]?.(e);}},
  setTimeout(f,ms){timers.set(++serial,{f,ms});return serial;},clearTimeout(id){timers.delete(id);},
  fetch:async(path,opts)=>{
   calls.push({path,method:opts.method,body:opts.body&&JSON.parse(opts.body)});
   if(deferred){const p=deferred;deferred=null;await p;}
   if(opts.method==='POST'){if(failPost){const e=Error('timeout');e.name='AbortError';throw e;}status.settings=JSON.parse(opts.body);status.active=status.settings.enabled;}
   return {ok:true,json:async()=>path==='/api/companion'?{paper_workday:JSON.parse(JSON.stringify(status))}:{ok:true}};
  }};
 vm.runInNewContext(fs.readFileSync('static/paper_workday_ui.js','utf8'),context);
 const flush=()=>new Promise(r=>setImmediate(r));
 async function click(id){nodes[id].click();await flush();}
 function emit(startedAt=now){listeners['moss:workday']({detail:{workday:JSON.parse(JSON.stringify(status)),startedAt}});}
 return{nodes,listeners,calls,timers,click,flush,emit,status,advance(ms){now+=ms;},hold(p){deferred=p;},failPost(){failPost=true;},posts(){return calls.filter(c=>c.method==='POST');}};
}
(async()=>{
 const paused=view({settings,today:{},active:false});assert.equal(paused.state,'New entries paused');
 assert.match(view({settings,today:{},active:true,phase:'waiting_for_market'}).next,/Holidays/);
 assert.equal(view({settings,today:{},active:true,error:'bad data'}).state,'Needs attention');
 const waiting=view({settings,today:{},active:true,phase:'waiting_for_next_cycle',next_at:'2026-09-25T15:05:00Z'});
 assert.equal(waiting.state,'Waiting for next cycle');assert.match(waiting.next,/no earlier than.*Due exits/);
 assert.equal(view({settings,today:{},active:true,busy:true,phase:'waiting_for_next_cycle',next_at:'2026-09-25T15:05:00Z'}).state,'Reviewing candidates');
 assert.doesNotMatch(view({settings,today:{},active:true,phase:'waiting_for_next_cycle',next_at:'broken'}).next,/Invalid Date/);
 let h=harness();h.emit();assert.equal(h.nodes['paper-auto-review'].disabled,false);
 await h.click('paper-auto-review');assert.equal(h.posts().length,0);assert.equal(h.nodes['paper-auto-confirm'].hidden,false);assert.match(h.nodes['paper-auto-review-text'].textContent,/\$5.00/);
 h.advance(600000);await h.click('paper-auto-start');assert.equal(h.posts().length,1,'no rushed review deadline; fresh settings rechecked');
 assert.equal(h.posts()[0].body.enabled,true);assert.deepEqual(h.posts()[0].body.symbols,['AAA']);assert.equal(h.nodes['moss-paper-enabled'].checked,true);
 assert.ok(h.calls.every(c=>['/api/companion','/api/companion/paper'].includes(c.path)));
 h.status.settings.max_order_usd=15;h.status.settings.symbols=['BBB'];await h.click('paper-auto-pause');
 assert.equal(h.posts().at(-1).body.enabled,false);assert.equal(h.posts().at(-1).body.max_order_usd,15);assert.deepEqual(h.posts().at(-1).body.symbols,['BBB']);assert.match(h.nodes['paper-auto-feedback'].textContent,/Due exits/);
 h=harness();h.emit();await h.click('paper-auto-review');h.status.settings.max_order_usd=99;await h.click('paper-auto-start');assert.equal(h.posts().length,0);assert.match(h.nodes['paper-auto-feedback'].textContent,/changed/);
 h=harness();h.emit();await h.click('paper-auto-review');let release;h.hold(new Promise(r=>{release=r;}));h.nodes['paper-auto-start'].click();h.nodes['paper-auto-start'].click();release();await h.flush();assert.equal(h.posts().length,1,'duplicate clicks cannot create a second save');
 h=harness();h.emit();await h.click('paper-auto-review');h.failPost();await h.click('paper-auto-start');assert.match(h.nodes['paper-auto-feedback'].textContent,/may have completed/);assert.equal(h.nodes['paper-auto-review'].disabled,true);assert.equal(h.posts().length,1);
 h=harness();h.emit();h.advance(46000);for(const t of [...h.timers.values()])if(t.ms===1000)t.f();assert.match(h.nodes['paper-auto-next'].textContent,/stale screen/);assert.equal(h.nodes['paper-auto-review'].disabled,true);await h.click('paper-auto-refresh');assert.equal(h.nodes['paper-auto-review'].disabled,false);
 h.status.settings.enabled=true;h.status.active=true;await h.click('paper-auto-pause');const good=h.nodes['paper-auto-state'].textContent;h.status.active=true;h.status.settings.enabled=true;h.emit(0);assert.equal(h.nodes['paper-auto-state'].textContent,good,'old background responses ignored after saves');
 h.listeners.pagehide();assert.equal(h.timers.size,0);
 console.log('Paper UI: saved-limit review, fresh recheck, no rushed deadline, pause preservation, changed limits, duplicate click, uncertain timeout, stale state and old response guards passed; no broker routes.');
})().catch(e=>{console.error(e);process.exit(1);});
