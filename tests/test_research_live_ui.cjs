const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function harness(){
 const nodes={},requests=[],intervals=[];let now=Date.now();
 const node=id=>nodes[id]??={value:'',textContent:'',hidden:false,disabled:false,checked:false,children:[],handlers:{},
  addEventListener(n,f){this.handlers[n]=f;},replaceChildren(){this.children=[];},appendChild(x){this.children.push(x);},focus(){this.focused=true;}};
 vm.runInNewContext(fs.readFileSync('static/research_live.js','utf8'),{document:{getElementById:node,createElement:()=>({textContent:''})},
  Date:class extends Date{static now(){return now;}},AbortController,setTimeout:()=>1,clearTimeout(){},setInterval:f=>intervals.push(f),
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
 const n=id=>node('research-live-'+id),flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
 const reply=async(i,data,ok=true)=>{requests[i].resolve({ok,json:async()=>data});await flush();};
 const result=(paper=false)=>({ok:true,review_token:'one-use',expires_at:new Date(now+90000).toISOString(),revision:'saved-policy',
  identity:{broker:'ibkr',account_id:'TEST1234',paper_mode:paper},equity:110,daily_pnl:0,universe_count:2,universe_sample:['TEST','<img>'],
  policy:{max_order_usd:1e9,max_daily_loss_usd:1e9,max_orders_per_day:1e5,model_budget_usd:5,order_type:'limit',interval_sec:120,max_research_per_day:100,min_confidence:.6,max_quote_age_sec:30,protective_exits:true,broker_stops:false},
  auto_options:{enabled:false},desk_limits:{max_daily_loss_pct:2,max_position_pct:2,max_trades_per_day:6},desk_kill_switch:{armed:false},desk_risk_preset:'mid',message:'Eligible orders may follow automatically.'});
 const confirm=word=>{n('typed').value=word;n('typed').handlers.input();n('ack').checked=true;n('ack').handlers.change();};
 return {n,requests,flush,reply,result,confirm,review:()=>n('review').handlers.click(),cancel:()=>n('cancel').handlers.click(),
  submit:()=>n('confirm').handlers.submit({preventDefault(){}}),expire:()=>{now+=90001;intervals[0]();}};
}
(async()=>{
 const h=harness();assert.equal(h.requests.length,0);assert.equal(h.n('start').disabled,true);
 h.review();h.review();assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/api/live-agent/start-review');
 assert.deepEqual(JSON.parse(h.requests[0].options.body),{});await h.reply(0,h.result());
 assert.equal(h.n('confirm').hidden,false);assert.equal(h.n('start').disabled,true);assert.match(h.n('account').textContent,/REAL MONEY.*1234/);
 assert.match(h.n('open-limits').textContent,/Effectively open/);assert.match(h.n('universe').textContent,/<img>/);
 h.confirm('PAPER');assert.equal(h.n('start').disabled,true);h.confirm('REAL');assert.equal(h.n('start').disabled,false);
 h.submit();h.submit();assert.equal(h.requests.length,2);assert.equal(h.requests[1].url,'/api/live-agent/start');
 assert.deepEqual(JSON.parse(h.requests[1].options.body),{review_token:'one-use',identity:h.result().identity,revision:'saved-policy',confirm:'REAL'});
 assert.equal(h.n('cancel').disabled,true);await h.reply(1,{ok:true,enabled:true,session_active:true});
 assert.match(h.n('message').textContent,/armed for REAL-MONEY/);assert.equal(h.n('confirm').hidden,true);
 assert.equal(h.n('typed').value,'');assert.equal(h.requests.length,2);

 const paper=harness();paper.review();await paper.reply(0,paper.result(true));paper.confirm('REAL');assert.equal(paper.n('start').disabled,true);
 paper.confirm('PAPER');assert.equal(paper.n('start').disabled,false);paper.cancel();assert.equal(paper.requests.length,1);
 assert.equal(paper.n('confirm').hidden,true);assert.equal(paper.n('start').disabled,true);

 const stale=harness();stale.review();await stale.reply(0,stale.result());stale.confirm('REAL');stale.expire();stale.submit();
 assert.equal(stale.requests.length,1);assert.match(stale.n('message').textContent,/expired/);

 const failed=harness();failed.review();await failed.reply(0,failed.result());failed.confirm('REAL');failed.submit();
 failed.requests[1].reject(Error('Timed out'));await failed.flush();assert.match(failed.n('message').textContent,/not confirmed.*check whether he is armed/);
 assert.equal(failed.n('confirm').hidden,true);failed.submit();assert.equal(failed.requests.length,2);
 failed.review();await failed.reply(2,{ok:false,error:'Already enabled'},false);assert.match(failed.n('message').textContent,/Already enabled/);
 assert.equal(failed.n('start').disabled,true);
 console.log('research live controller: explicit account/typed review, duplicates, cancel, expiry, text safety and uncertain activation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
