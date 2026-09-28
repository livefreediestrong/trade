const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(require('node:path').join(__dirname, '../static/live_switch.js'), 'utf8');
const nodes = {}, listeners = {}, requests = [];
for (const id of ['live-switch', 'ls-state', 'ls-plain', 'ls-on', 'ls-off', 'ls-confirm', 'ls-confirm-text',
                  'ls-status', 'ls-phrase', 'ls-cancel']) {
 nodes[id] = {textContent:'', hidden:false, dataset:{}, value:'GO LIVE', events:{},
  addEventListener(name, fn){this.events[name]=fn;}, focus(){}};
}
let mode = 'manual', failMutation = false, failReads = false;
const window = {addEventListener(name, fn){listeners[name]=fn;}, dispatchEvent(e){listeners[e.type]?.(e);}};
const context = {window, document:{hidden:false,getElementById:id=>nodes[id],addEventListener(){}},
 AbortController, setTimeout, clearTimeout, CustomEvent:class {constructor(type){this.type=type;}},
 fetch:async (url, options)=>{
  requests.push({url, options});
  if(options.method === 'POST') {
   mode = 'live_manual'; // Server committed the change before the connection failed.
   if(failMutation)throw Object.assign(Error('connection lost'),{name:'AbortError'});
  } else if(failReads)throw Error('offline');
  return {ok:true,json:async()=>({ok:true,mode,live:mode!=='manual',real_money:true,broker_configured:true,plain:mode})};
 }};
const flush = ()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 vm.runInNewContext(code,context);await flush();
 assert.equal(nodes['ls-state'].textContent,'OFF');
 mode='auto_live';listeners['desk:state']({detail:{config:{mode}}});await flush();
 assert.equal(nodes['ls-state'].textContent,'ON · Fox automatic','another control changing mode updates the switch');
 mode='manual';listeners['desk:state']({detail:{config:{mode}}});await flush();
 failMutation=true;failReads=true;
 nodes['ls-confirm'].events.submit({preventDefault(){}});await flush();
 assert.equal(nodes['ls-state'].textContent,'Status unknown');
 assert.ok(nodes['ls-on'].hidden && nodes['ls-off'].hidden);
 assert.doesNotMatch(nodes['ls-status'].textContent,/nothing was changed/i);
 assert.equal(requests.filter(r=>r.options.method==='POST').length,1,'ambiguous mutation is never blindly resubmitted');
 failReads=false;listeners['desk:state']({detail:{config:{mode:'live_manual'}}});await flush();
 assert.equal(nodes['ls-state'].textContent,'ON · you approve each order');
 assert.equal(nodes['ls-off'].hidden,false);
 assert.ok(requests.every(r=>r.url==='/api/live/master'));
 vm.runInNewContext(code,{document:{getElementById:()=>null}});
 console.log('Live switch: external mode changes, ambiguous committed request, read recovery and missing-panel checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
