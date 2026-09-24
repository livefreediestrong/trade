// Source-extracted controllers with deferred in-memory requests. No live HTTP.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('static/app.js','utf8');
const slice=(a,b)=>{const start=src.indexOf(a),end=src.indexOf(b,start);assert(start>=0&&end>start);return src.slice(start,end);};
function element(id=''){return {id,isConnected:true,dataset:{},value:'',disabled:false,textContent:'',handlers:{},classList:{s:new Set(['hidden']),add(x){this.s.add(x)},remove(x){this.s.delete(x)},contains(x){return this.s.has(x)},toggle(x,on){on?this.add(x):this.remove(x)}},focus(){this.focused=true},setAttribute(k,v){this[k]=v},addEventListener(n,f){this.handlers[n]=f},closest(){return this}};}
async function cancellation(){
 const ids=['cancel-order-dialog','cancel-order-message','cancel-order-submit','cancel-order-ack','cancel-order-close'];
 const nodes=Object.fromEntries(ids.map(id=>[id,element(id)])),handlers={},requests=[];
 const context={document:{getElementById:id=>nodes[id],addEventListener:(n,f)=>handlers[n]=f},window:{dispatchEvent(){}},Event:class{},fetch:(path,opts)=>new Promise((resolve,reject)=>requests.push({path,opts,resolve,reject}))};
 vm.runInNewContext(fs.readFileSync('static/order_controls.js','utf8'),context);
 const button=id=>Object.assign(element(id),{dataset:{cancelReview:'signal-'+id}});
 const response=id=>({ok:true,json:async()=>({ok:true,identity:{paper_mode:false,account_id:'acct1234'},ticker:'AAPL',order_id:'order-'+id,review_token:'token-'+id,note:'Review remaining quantity.'})});
 const a=handlers.click({target:button('A')});nodes['cancel-order-close'].handlers.click();
 const b=handlers.click({target:button('B')});requests[1].resolve(response('B'));await b;
 nodes['cancel-order-ack'].value='AAPL';nodes['cancel-order-ack'].handlers.input({target:nodes['cancel-order-ack']});
 requests[0].resolve(response('A'));await a;
 assert.match(nodes['cancel-order-message'].textContent,/order-B/);assert.equal(nodes['cancel-order-submit'].disabled,false);
 const submitted=nodes['cancel-order-submit'].handlers.click({currentTarget:nodes['cancel-order-submit']});
 assert.equal(requests[2].path,'/api/signals/signal-B/cancel');assert.equal(JSON.parse(requests[2].opts.body).review_token,'token-B');
 nodes['cancel-order-close'].handlers.click();const c=handlers.click({target:button('C')});requests[3].resolve(response('C'));await c;
 requests[2].resolve({ok:true,json:async()=>({ok:true,message:'B cancelled',status:{}})});await submitted;
 assert.match(nodes['cancel-order-message'].textContent,/order-C/);assert.equal(nodes['cancel-order-submit'].disabled,true);
 nodes['cancel-order-close'].handlers.click();const d=handlers.click({target:button('D')});nodes['cancel-order-close'].handlers.click();
 const e=handlers.click({target:button('E')});requests[5].resolve(response('E'));await e;requests[4].reject(Error('old failure'));await d;
 assert.match(nodes['cancel-order-message'].textContent,/order-E/);
}
async function dirtyForms(){
 const ids=['#watchlist','#ks-loss','#ks-trades','#ks-pos','#mode-select','#live-box','#btn-save-watchlist','#btn-apply-mode'];
 const nodes=Object.fromEntries(ids.map(id=>[id,element(id)]));
 const cfg={mode:'live_manual',watchlist:['AAPL'],kill_switch:{max_daily_loss_usd:1000,max_trades_per_day:20,max_position_size_usd:5000}};
 let release;
 const c={cfg,$:id=>nodes[id]||null,document:{activeElement:nodes['#watchlist']},liveSettingsFields:['#mode-select','#ks-loss','#ks-trades','#ks-pos'],renderWatchlistCount(){},toast(){},refresh:async()=>{},api:()=>new Promise(resolve=>release=resolve)};
 vm.createContext(c);
 vm.runInContext(slice('  // Events','  $("#btn-apply-mode")'),c);
 vm.runInContext(slice('  $("#watchlist")?.addEventListener("input"','  async function generateWorkspaceIdea'),c);
 nodes['#watchlist'].value='MSFT';nodes['#watchlist'].handlers.input({target:nodes['#watchlist']});
 nodes['#ks-loss'].value='100';nodes['#ks-loss'].handlers.input();nodes['#ks-trades'].value='3';c.document.activeElement=nodes['#ks-trades'];
 nodes['#mode-select'].value='auto_live';nodes['#mode-select'].handlers.change();
 const renderDraft=()=>vm.runInContext('{'+slice('    // Never write #watchlist unless','    const focus = cfg.watchlist_focus')+slice('    if (cfg.kill_switch) {','    if (data.banner) {')+'}',c);
 renderDraft();assert.equal(nodes['#watchlist'].value,'MSFT');assert.equal(nodes['#ks-loss'].value,'100');assert.equal(nodes['#ks-trades'].value,'3');
 vm.runInContext(slice('    const modeSelect = $("#mode-select");','    syncFillModeToggle(cfg);'),c);assert.equal(nodes['#mode-select'].value,'auto_live');
 vm.runInContext(slice('  $("#btn-save-watchlist")','  async function setWatchlistFocus'),c);
 const save=nodes['#btn-save-watchlist'].handlers.click();nodes['#watchlist'].value='NVDA';release({});await save;renderDraft();assert.equal(nodes['#watchlist'].value,'NVDA');assert.equal(nodes['#watchlist'].dataset.dirty,'1');
 const saved=nodes['#btn-save-watchlist'].handlers.click();release({});await saved;assert.equal(nodes['#watchlist'].dataset.dirty,undefined);
 cfg.watchlist=['NVDA'];renderDraft();assert.equal(nodes['#watchlist'].value,'NVDA');
 const savedBodies=[];Object.assign(c,{activeWorkspace:'live',window:{__brokerStatus:{}},confirm:()=>true,renderBrokerStatus(){},api:async(path,options)=>{
  if(path==='/api/broker-identity')return {broker:{paper_mode:true,broker:'fixture'}};
  savedBodies.push(JSON.parse(options.body));return new Promise(resolve=>release=resolve);
 }});
 vm.runInContext(slice('  $("#btn-apply-mode")','  $$(".preset-btn")'),c);
 const riskSave=nodes['#btn-apply-mode'].handlers.click();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(savedBodies[0].kill_switch.max_daily_loss_usd,100);assert.equal(savedBodies[0].kill_switch.max_trades_per_day,3);
 nodes['#ks-loss'].value='50';nodes['#ks-loss'].handlers.input();release({config:{mode:'auto_live'}});await riskSave;
 renderDraft();assert.equal(nodes['#ks-loss'].value,'50');assert.equal(nodes['#ks-loss'].dataset.dirty,'1','edits made during save stay dirty');
 const finalSave=nodes['#btn-apply-mode'].handlers.click();await new Promise(resolve=>setImmediate(resolve));release({config:{mode:'auto_live'}});await finalSave;
 for(const selector of c.liveSettingsFields)assert.equal(nodes[selector].dataset.dirty,undefined,'successful unchanged save clears the form draft');
}
async function streamOrder(){
 const noop=()=>{};const calls=[];
 const c={state:{config:{},signals:{pending:[],approved:[]}},window:{dispatchEvent:noop},CustomEvent:class{},console:{warn:noop},setTimeout,clearTimeout,
 fullStateSeen:true,oppEmptyTimer:null,pollErrToasts:0,POLL_ERR_TOAST_CAP:3,loopEvents:[],loopFeedSeq:0,
 api:()=>new Promise((resolve,reject)=>calls.push({resolve,reject})),noteFresh:noop,warnCorruptFiles:noop,toast:noop,workspaceView:x=>x};
 const renderCode=slice('  function render(data) {','  function appendChatBubble');
 const liteCode=slice('  let _liteSig = "";','  function renderPace');
 for(const name of [...new Set((renderCode+liteCode).match(/\b(?:render\w+|sync\w+|consumeDeskAlerts|updateDeskGuide|maybeAlertFromState|scheduleSparks|noteTradeGlowFromEvent)\b/g))])c[name]=noop;
 vm.createContext(c);vm.runInContext(renderCode+liteCode+slice('  let refreshSeq = 0;','  async function refreshCuratedMeta'),c);
 const first=vm.runInContext('refresh()',c),coalesced=vm.runInContext('refresh()',c);assert.equal(calls.length,1);
 c.incoming={ok:true,session_active:false,config:{mode:'live_manual'},signals:{pending:[],approved:[{id:'new-fill'}]},signal_history_delta:true};
 vm.runInContext('applyStateLite(incoming)',c);
 calls[0].resolve({config:{mode:'live_manual'},signals:{pending:[{id:'new-fill'}],approved:[]}});await Promise.all([first,coalesced]);
 assert.equal(c.state.signals.approved[0].id,'new-fill');
 c.incoming={...c.incoming,signals:{pending:[]},loop:{seq:22}};vm.runInContext('applyStateLite(incoming)',c);assert.equal(c.state.signals.approved[0].id,'new-fill');
 const next=vm.runInContext('refresh()',c);assert.equal(calls.length,2);calls[1].resolve({config:{mode:'manual'},signals:{pending:[],approved:[{id:'newer-fill'}]}});await next;
 assert.equal(c.state.signals.approved[0].id,'newer-fill');
 const failed=vm.runInContext('refresh()',c);calls[2].reject(Error('offline'));await failed;
 const retry=vm.runInContext('refresh()',c);calls[3].resolve({config:{},signals:{pending:[],approved:[]}});await retry;assert.equal(c.state.signals.approved.length,0);
}
async function focusReturn(){
 const selectors=['#approve-modal','#broker-order-type','#broker-order-limit','#broker-order-terms-status','#btn-approve-confirm','#signal-list'];
 const nodes=Object.fromEntries(selectors.map(id=>[id,element(id)])),opener=element(),replacement=element();
 const signal={id:'idea',ticker:'AAPL',source:'pending',actionable:true,side:'buy',suggested_shares:1};replacement.dataset.approve='idea';
 let release;const c={$:id=>nodes[id]||null,$$:selector=>selector==='[data-approve]'?[replacement]:[],document:{activeElement:opener},window:{__brokerStatus:{paper_mode:true}},state:{config:{mode:'live_manual'}},approvalContext:null,pendingApproveId:null,approveReturnFocus:null,approveReturnSignalId:null,findSignalById:()=>signal,signalWorkspace:()=> 'live',brokerMode:()=>true,realMoney:()=>false,renderBrokerStatus(){},updateApproveEligibility(){},toast(){},api:()=>new Promise(r=>release=r)};
 vm.createContext(c);vm.runInContext(slice('  async function openApprovePreview(','  function invalidateBrokerOrderReview'),c);
 const first=vm.runInContext('openApprovePreview("idea")',c);c.document.activeElement=element('another-control');release({order:{type:'limit',limit:100},review_token:'token'});await first;
 assert.equal(c.approveReturnFocus,opener,'opener captured before the request');
 c.document.activeElement=element('updated-terms');const updated=vm.runInContext('openApprovePreview("idea", {type:"limit"})',c);release({order:{type:'limit',limit:101},review_token:'updated'});await updated;
 assert.equal(c.approveReturnFocus,opener,'updated terms do not replace the opener');vm.runInContext('closeApproveModal()',c);assert.equal(opener.focused,true);
 c.approveReturnFocus=Object.assign(element(),{isConnected:false});c.approveReturnSignalId='idea';vm.runInContext('closeApproveModal()',c);assert.equal(replacement.focused,true);
 c.approveReturnFocus=null;c.approveReturnSignalId='removed';vm.runInContext('closeApproveModal()',c);assert.equal(nodes['#signal-list'].focused,true);
}
async function evaluation(){
 const button=element(),watch=element();watch.value='AAPL';const calls=[];
 const c={$:id=>id==='#btn-evaluate-watchlist'?button:watch,toast(){},renderWatchlistResults(){},state:{},api:()=>new Promise((resolve,reject)=>calls.push({resolve,reject}))};
 vm.runInNewContext(slice('  $("#btn-evaluate-watchlist")','  $("#watchlist")?.addEventListener("input"'),c);
 const first=button.handlers.click({currentTarget:button});await button.handlers.click({currentTarget:button});assert.equal(calls.length,1);assert.equal(button.disabled,true);
 calls[0].resolve({results:[]});await first;assert.equal(button.disabled,false);
 const failed=button.handlers.click({currentTarget:button});calls[1].reject(Error('bounded timeout'));await failed;assert.equal(button.disabled,false);
}
(async()=>{await cancellation();await dirtyForms();await streamOrder();await focusReturn();await evaluation();console.log('Frontend state: exact cancellation ticket, late error/completion isolation, dirty drafts/save races, stream/full ordering and retry, approval focus, and Evaluate coalescing passed.');})().catch(e=>{console.error(e);process.exitCode=1});
