// Real ticket controller; deferred fetches stay in memory and never reach a broker.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function harness(){
 const nodes={},handlers={},windows={},requests=[],intervals=[];let clock=Date.now();
 function node(id){return nodes[id]??=( {id,value:'',disabled:false,hidden:false,textContent:'',handlers:{},dataset:{},attrs:{},style:{},
  classList:{toggle(){},add(){},remove(){},contains:()=>false},setAttribute(k,v){this.attrs[k]=String(v)},getAttribute(k){return this.attrs[k]??null},
  focus(){this.focused=true},scrollIntoView(){},addEventListener(n,f){this.handlers[n]=f}} );}
 const radio={};
 for(const [name,values] of [['live-ticket-intent',['buy','sell','cover']],['live-ticket-type',['limit','market']]]){
  radio[name]={selected:values[0],nodes:{}};
  for(const value of values){radio[name].nodes[value]={value};Object.defineProperty(radio[name].nodes[value],'checked',{get:()=>radio[name].selected===value,set:on=>{if(on)radio[name].selected=value}});}
 }
 node('live-ticket-form').querySelectorAll=()=>[];
 node('live-ticket-form').querySelector=selector=>{const name=selector.match(/name="([^"]+)"/)[1],value=selector.match(/value="([^"]+)"/)?.[1];return radio[name].nodes[value||radio[name].selected];};
 node('live-ticket-symbol').value='TEST';node('live-ticket-shares').value='2';node('live-ticket-limit').value='100';
 const context={document:{getElementById:node,querySelectorAll:()=>[],addEventListener:(n,f)=>handlers[n]=f},window:{addEventListener:(n,f)=>windows[n]=f,dispatchEvent(){}},
  Date:class extends Date{static now(){return clock}},AbortController,Event:class{},setInterval:f=>{intervals.push(f);return 1},clearInterval(){},setTimeout(){return 1},clearTimeout(){},
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,body:JSON.parse(options.body),resolve,reject}))};
 vm.runInNewContext(fs.readFileSync('static/live_ticket.js','utf8'),context);
 // The ticket sizes in dollars by default; this harness sizes in whole shares.
 node('live-ticket-unit-shares').handlers.click();node('live-ticket-size').value='2';
 const identity={account_id:'fixture1234',paper_mode:false,broker:'ibkr'};
 const state=(patch={})=>windows['desk:state']({detail:{config:{mode:'live_manual',broker_identity:identity},broker:{broker:'ibkr'},broker_book:{paper_mode:false},...patch}});
 state();
 const response=(signal='one')=>({ok:true,signal_id:signal,review_token:'token-'+signal,ticker:'TEST',side:'buy',intent:'buy',identity,
  order:{shares:2,type:'limit',limit:100},held_shares:0,available_whole_shares:0,quote:{price:100,source:'fixture',market_time:new Date(clock).toISOString()},
  costs:{commission_estimate:1,currency:'USD',contract:{primary_exchange:'NASDAQ'}},estimated_notional:200,estimated_cash_change:-201,expires_at:new Date(clock+90000).toISOString()});
 const resolve=(index,data,status=true)=>requests[index].resolve({ok:status,json:async()=>data});
 const preview=()=>node('live-ticket-form').handlers.submit({preventDefault(){}});
 const ack=value=>{node('live-ticket-ack').value=value;node('live-ticket-ack').handlers.input();};
 return {nodes,node,requests,radio,state,response,resolve,preview,ack,windows,handlers,intervals,advance:ms=>{clock+=ms}};
}
(async()=>{
 const h=harness();assert.equal(h.requests.length,0);
 const first=h.preview();await h.preview();assert.equal(h.requests.length,1);
 assert.equal(h.requests[0].url,'/api/live/ticket/review');assert.equal(h.requests[0].body.order.type,'limit');
 h.resolve(0,h.response());await first;
 assert.equal(h.node('live-ticket-send').disabled,true);assert.match(h.node('live-ticket-account').textContent,/REAL MONEY/);
 h.ack('WRONG');assert.equal(h.node('live-ticket-send').disabled,true);
 h.ack('TEST');assert.equal(h.node('live-ticket-send').disabled,false);
 const send=h.node('live-ticket-send').handlers.click();await h.node('live-ticket-send').handlers.click();
 assert.equal(h.requests.length,2);assert.equal(h.requests[1].body.review_token,'token-one');assert.equal(h.requests[1].body.ack_ticker,'TEST');
 h.resolve(1,{ok:false,pending:true,error:'Awaiting reconciliation'},false);await send;
 assert.match(h.node('live-ticket-result').textContent,/still working/);assert.equal(h.node('live-ticket-send').disabled,true);
 // A late preview cannot restore confirmation after the user edited quantity.
 const late=h.preview();h.node('live-ticket-size').value='3';h.node('live-ticket-form').handlers.input();h.resolve(2,h.response('late'));await late;
 assert.equal(h.node('live-ticket-review').hidden,true);assert.equal(h.node('live-ticket-send').disabled,true);
 // Mode/account/age changes invalidate a reviewed ticket.
 const second=h.preview();h.resolve(3,h.response('second'));await second;h.ack('TEST');
 h.state({config:{mode:'live_manual',broker_identity:{account_id:'other',paper_mode:false}}});
 assert.equal(h.node('live-ticket-review').hidden,true);assert.equal(h.node('live-ticket-send').disabled,true);h.state();
 const third=h.preview();h.resolve(4,h.response('third'));await third;h.advance(26000);h.intervals[0]();
 assert.equal(h.node('live-ticket-review').hidden,true);h.state();
 // Market selection omits the retained limit value. Discard rejects the saved draft only.
 h.radio['live-ticket-type'].selected='market';h.node('live-ticket-form').handlers.change();
 const market=h.preview();assert.equal(h.requests[5].body.order.type,'market');assert.equal(h.requests[5].body.order.limit_price,undefined);
 h.resolve(5,h.response('market'));await market;
 const discard=h.node('live-ticket-discard').handlers.click();assert.equal(h.requests[6].url,'/api/signals/market/reject');
 h.resolve(6,{ok:true});await discard;assert.match(h.node('live-ticket-result').textContent,/Ticket discarded/);
 // Position buttons only populate a form; no automatic review or order.
 const count=h.requests.length;h.handlers.click({target:{closest:()=>({dataset:{livePosition:'MSFT',side:'short',shares:'3.75'}})}});
 assert.equal(h.radio['live-ticket-intent'].selected,'cover');assert.equal(h.node('live-ticket-shares').value,'3');assert.equal(h.node('live-ticket-size').value,'3');assert.equal(h.requests.length,count);
 // A copied holding needs a limit price before any review can be requested.
 h.node('live-ticket-symbol').value='TEST';await h.preview();assert.equal(h.requests.length,count);
 h.node('live-ticket-limit').value='100';h.node('live-ticket-form').handlers.input();
 const failed=h.preview();h.resolve(7,h.response('failed'));await failed;h.ack('TEST');
 const uncertain=h.node('live-ticket-send').handlers.click();h.requests[8].reject(Error('Connection interrupted'));await uncertain;
 assert.match(h.node('live-ticket-result').textContent,/will not retry automatically/);assert.equal(h.requests.length,9);
 console.log('Live ticket: explicit review and acknowledgement, exact payload, duplicate suppression, stale response/account/status invalidation, market terms, discard, position prefilling, uncertain submission and no automatic retry passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
