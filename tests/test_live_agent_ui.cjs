// Run the actual browser controller with deferred in-memory HTTP responses.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function harness(){
 const nodes={},listeners={},requests=[],timers=[];let clock=Date.now(),kind='limit';
 const node=id=>nodes[id]??={value:'',textContent:'',dataset:{},disabled:false,handlers:{},addEventListener(n,f){this.handlers[n]=f;},replaceChildren(...children){this.children=children;}};
 node('live-agent-form').querySelector=selector=>{
  const value=selector.match(/value="([^"]+)"/)?.[1];
  return value?{set checked(on){if(on)kind=value;}}:{value:kind};
 };
 vm.runInNewContext(fs.readFileSync('static/live_agent.js','utf8'),{
  document:{hidden:false,getElementById:node,createElement:()=>({replaceChildren(...children){this.children=children;}}),addEventListener(n,f){listeners[n]=f;}},
  window:{addEventListener(n,f){listeners[n]=f;}},Date:class extends Date{static now(){return clock}},
  AbortController,setTimeout:(f,ms)=>{if(ms===10000)timers.push(f);return timers.length;},clearTimeout(){},
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))
 });
 const policy={symbols:['TEST'],interval_sec:120,order_type:'limit',max_order_usd:100,max_daily_loss_usd:20,max_orders_per_day:3,max_research_per_day:100,model_budget_usd:5,limit_offset_bps:5,min_confidence:.6,max_quote_age_sec:30,protective_exits:true,breakeven_after_r:1,max_hold_min:0,flatten_before_close_min:10};
 const state=(extra={})=>({ok:true,configured:true,enabled:false,policy,revision:'rev1',identity:{broker:'ibkr',account_id:'TEST1234',paper_mode:false},message:'Paused',today:{research:1,orders:0},events:[],...extra});
 const reply=async(i,s,ok=true)=>{requests[i].resolve({ok,json:async()=>s});for(let n=0;n<8;n++)await Promise.resolve();};
 const ack=value=>{node('live-agent-confirm').value=value;node('live-agent-confirm').handlers.input();};
 const poll=async()=>{timers.at(-1)();await Promise.resolve();};
 const click=id=>node(id).handlers.click();
 return {node,requests,state,reply,ack,poll,click,desk:s=>listeners['desk:state']({detail:s}),advance:ms=>{clock+=ms},edit:()=>node('live-agent-form').handlers.input(),save:()=>node('live-agent-form').handlers.submit({preventDefault(){}})};
}
(async()=>{
 const h=harness();assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/api/live-agent');
 await h.reply(0,h.state());assert.equal(h.node('live-agent-start').disabled,true);
 h.ack('PAPER');assert.equal(h.node('live-agent-start').disabled,true);h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,false);
 h.click('live-agent-start');h.click('live-agent-start');assert.equal(h.requests.length,2);
 const start=JSON.parse(h.requests[1].options.body);assert.equal(start.identity.account_id,'TEST1234');assert.equal(start.confirm,'REAL');assert.equal(start.revision,'rev1');
 await h.reply(1,h.state({enabled:true}));assert.equal(h.node('live-agent-start').disabled,true);
 h.click('live-agent-pause');await h.reply(2,h.state());assert.match(h.node('live-agent-result').textContent,/paused/);
 h.node('la-max_order_usd').value='321';h.edit();await h.poll();await h.reply(3,h.state());
 assert.equal(h.node('la-max_order_usd').value,'321');h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,true);
 h.save();assert.equal(h.requests[4].url,'/api/live-agent/policy');assert.equal(JSON.parse(h.requests[4].options.body).policy.max_order_usd,321);
 // Editing during save must not overwrite the newer draft or enable start.
 h.node('la-max_order_usd').value='456';h.edit();await h.reply(4,h.state({revision:'rev2'}));
 assert.equal(h.node('la-max_order_usd').value,'456');assert.equal(h.node('live-agent-start').disabled,true);
 h.save();await h.reply(5,h.state({revision:'rev3',policy:{...h.state().policy,max_order_usd:456}}));
 h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,false);
 h.advance(26000);h.click('live-agent-start');assert.equal(h.requests.length,6);assert.equal(h.node('live-agent-start').disabled,true);
 await h.poll();await h.reply(6,h.state({revision:'rev3',identity:{broker:'ibkr',account_id:'OTHER',paper_mode:true}}));
 assert.equal(h.node('live-agent-confirm').value,'');h.ack('REAL');assert.equal(h.node('live-agent-start').disabled,true);h.ack('PAPER');
 h.click('live-agent-start');h.requests[7].reject(Error('Lost acknowledgement'));for(let n=0;n<8;n++)await Promise.resolve();
 assert.match(h.node('live-agent-result').textContent,/will not retry automatically/);assert.equal(h.requests.length,8);
 assert.equal(h.node('live-agent-start').disabled,true);assert.equal(h.node('live-agent-pause').disabled,false);
 const conflict=harness();await conflict.reply(0,conflict.state());conflict.edit();await conflict.poll();await conflict.reply(1,conflict.state({revision:'other-window'}));conflict.save();
 assert.equal(JSON.parse(conflict.requests[2].options.body).revision,'rev1');
 await conflict.reply(2,{ok:false,error:'Policy changed in another window'},false);
 assert.match(conflict.node('live-agent-result').textContent,/another window/);
 const quote=(price=125,extra={})=>({ok:true,quote:{price,fresh:true,source:'Finnhub',market_time:new Date(Date.now()-1000).toISOString(),...extra}});
 const flush=async()=>{for(let n=0;n<20;n++)await Promise.resolve();};
 const budget=harness();await budget.reply(0,budget.state({identity:null}));
 assert.match(budget.node('live-agent-next').textContent,/verify the account/);
 budget.click('live-agent-budget-check');budget.click('live-agent-budget-check');assert.equal(budget.requests.length,2);
 assert.equal(budget.requests[1].url,'/api/cost-estimate/quote/TEST');assert.equal(budget.requests[1].options.method,undefined);
 await budget.reply(1,quote());await flush();
 assert.match(budget.node('live-agent-budget-list').children[0].textContent,/0 whole shares.*one share needs/);
 assert.match(budget.node('live-agent-budget-status').textContent,/fees.*risk limits can reduce/);
 assert.equal(budget.node('live-agent-start').disabled,true);
 budget.advance(31000);await budget.poll();
 assert.match(budget.node('live-agent-budget-list').children[0].textContent,/older than your limit/);
 assert.equal(budget.requests.at(-1).url,'/api/live-agent'); // Expiry never fetches or trades on its own.
 const race=harness();await race.reply(0,race.state());race.click('live-agent-budget-check');
 race.node('la-max_order_usd').value='500';race.edit();await race.reply(1,quote());await flush();
 assert.equal(race.node('live-agent-budget-list').children.length,0); // Old draft quote cannot restore a result.
 race.click('live-agent-budget-check');await race.reply(2,quote(125));await flush();
 assert.match(race.node('live-agent-budget-list').children[0].textContent,/4 whole shares/);
 await race.poll();await race.reply(3,race.state({revision:'changed',policy:{...race.state().policy,max_order_usd:99}}));
 assert.equal(race.node('la-max_order_usd').value,'500'); // Unsaved draft still owns the estimate.
 for(const extra of [{fresh:false},{source:'mock_feed'},{price:NaN},{market_time:new Date(Date.now()+60000).toISOString()}]){
  const bad=harness();await bad.reply(0,bad.state());bad.click('live-agent-budget-check');await bad.reply(1,quote(50,extra));await flush();
  assert.match(bad.node('live-agent-budget-list').children[0].textContent,/unavailable/);
 }
 const capped=harness();await capped.reply(0,capped.state());capped.node('la-symbols').value='A B C D E F G H I';capped.edit();capped.click('live-agent-budget-check');
 assert.equal(capped.requests.length,3); // At most two provider requests at once.
 for(let i=1;i<=8;i++){assert.ok(capped.requests[i]);await capped.reply(i,quote(50));await flush();}
 assert.equal(capped.requests.length,9);assert.equal(capped.node('live-agent-budget-list').children.length,8);
 assert.match(capped.node('live-agent-budget-status').textContent,/Checked 8 of 9.*Other symbols have not been checked/);
 const invalid=harness();await invalid.reply(0,invalid.state());invalid.node('la-symbols').value='SPY/<script>';invalid.click('live-agent-budget-check');
 assert.equal(invalid.requests.length,1);assert.match(invalid.node('live-agent-budget-status').textContent,/valid US symbols/);
 const failure=harness();await failure.reply(0,failure.state());failure.click('live-agent-budget-check');failure.requests[1].reject(Error('Provider unavailable'));await flush();
 assert.match(failure.node('live-agent-budget-list').children[0].textContent,/Provider unavailable/);
 assert.equal(failure.node('live-agent-budget-check').disabled,false);
 const options=harness(),ao={enabled:true,strategies:['put_credit','short_put'],max_contracts:3,allow_naked_short:false,dte_min:21,dte_max:70,max_quote_age_sec:8,prefer_otm_pct:2,spread_width_pct:4,every_n_stock_cycles:3};
 await options.reply(0,options.state({auto_options:ao}));options.node('la-interval_sec').value='180';options.edit();
 await options.poll();await options.reply(1,options.state({auto_options:{...ao,dte_min:7},revision:'other-window'}));
 options.save();const saved=JSON.parse(options.requests[2].options.body);
 assert.deepEqual(saved.auto_options,ao);assert.equal(saved.revision,'rev1'); // Polls never replace the draft's hidden settings.
 const empty=harness();await empty.reply(0,empty.state());
 for(const strategy of ['long_call','long_put'])empty.node('la-ao-'+strategy).checked=false;
 empty.edit();empty.save();assert.equal(empty.requests.length,1);assert.match(empty.node('live-agent-result').textContent,/at least one/);
 const monitor=harness(),scope={broker:'ibkr',account_id:'TEST1234',paper_mode:false};
 await monitor.reply(0,monitor.state({enabled:true,session_active:true,mode:'auto_live',market_open:false,account_scope:scope,managed:{TEST:{shares:2,entry:100,stop:99,target:102,account_scope:{account_id:'TEST1234',paper_mode:false,broker:'ibkr'}}}}));
 monitor.desk({broker:{connected:false},broker_book:{ok:false,risk_ready:false}});
 assert.equal(monitor.node('bx-agent-chip').textContent,'Connection needed');
 assert.doesNotMatch(monitor.node('live-agent-managed').textContent,/Protecting/);
 assert.match(monitor.node('live-agent-position-list').children[0].children[2].textContent,/Connection required/);
 monitor.advance(26000);await monitor.poll();assert.equal(monitor.node('bx-agent-chip').textContent,'Status unavailable');
 console.log('Live agent UI: no automatic activation, exact account confirmation, duplicate suppression, dirty drafts, stale status, account changes and uncertain-request handling passed.');
 console.log('Budget check: real quotes, zero/positive whole-share sizing, stale/future/mock rejection, draft races, errors and bounded read-only requests passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
