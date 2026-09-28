const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/desk_maintenance.js','utf8');
const nodes=new Map(),events={},timers=new Map();let serial=0,fail=false;
function node(id,tag){if(!nodes.has(id))nodes.set(id,{tagName:tag,textContent:'',children:[],open:false,append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},querySelector(tag){for(const child of this.children){if(child.tagName===tag)return child;const found=child.querySelector(tag);if(found)return found;}return null;},focus(options){assert.equal(options.preventScroll,true);context.document.activeElement=this;}});return nodes.get(id);}
function text(n){return [n.textContent,...n.children.map(text)].join(' ');}
const data={broker_window:{},watchdog:{},daily:{},backup:{},tests:{status:'older_failed',summary:'2 failed in an earlier version',scope:'Local checks do not qualify live trading.',details_file:'upkeep/test-results/result.txt'}};
const context={document:{hidden:false,activeElement:null,getElementById:node,createElement:tag=>node('new'+(++serial),tag),addEventListener:(k,f)=>events[k]=f},
 window:{addEventListener:(k,f)=>events[k]=f},AbortController,
 fetch:async(url,options)=>{assert.equal(url,'/api/maintenance');assert.ok(options.signal);return {ok:!fail,json:async()=>data}},
 setInterval:fn=>{timers.set(++serial,fn);return serial},clearInterval:id=>timers.delete(id),
 setTimeout:()=>++serial,clearTimeout:()=>{}};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 vm.runInNewContext(source,context);await settle();
 let visible=text(node('maintenance-evidence'));
 assert.match(visible,/failures in an earlier version/);assert.doesNotMatch(visible,/current app version has failing/);
 assert.match(visible,/do not qualify live trading/);assert.match(visible,/test-results\/result.txt/);
 let details=node('maintenance-evidence').querySelector('details');details.open=true;
 context.document.activeElement=details.querySelector('summary');
 for(const refresh of [()=>[...timers.values()][0](),()=>events.visibilitychange()]){
  refresh();await settle();details=node('maintenance-evidence').querySelector('details');
  assert.equal(details.open,true,'Refresh closed the reader’s details');
  assert.equal(context.document.activeElement,details.querySelector('summary'),'Refresh lost keyboard focus');
 }
 const elsewhere=node('unrelated-input');context.document.activeElement=elsewhere;
 events.visibilitychange();await settle();assert.equal(context.document.activeElement,elsewhere,'Refresh stole focus');
 details=node('maintenance-evidence').querySelector('details');details.open=false;
 events.visibilitychange();await settle();assert.equal(node('maintenance-evidence').querySelector('details').open,false);
 for(const [status,expected] of [['passed',/current app version passed/],['failed',/current app version has failing/],['older_passed',/earlier version passed/],['unknown',/No dated full-test result/]]){
  data.tests.status=status;events.pageshow({persisted:true});await settle();assert.match(text(node('maintenance-evidence')),expected);
 }
 data.tests.summary='<img src=x onerror=alert(1)>';data.tests.update_waiting=true;events.pageshow({persisted:true});await settle();
 visible=text(node('maintenance-evidence'));assert.match(visible,/waiting to load/);assert.match(visible,/<img src=x/);
 fail=true;events.pageshow({persisted:true});await settle();assert.equal(node('maintenance-evidence').children.length,0);
 assert.match(node('maintenance-current').textContent,/status are unknown/);
 events.pagehide();assert.equal(timers.size,0);
 console.log('Maintenance renderer: refresh preserves details/focus, old/current/unknown checks, local details, text safety and failed refresh passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
