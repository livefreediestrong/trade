// Render the real overview controller against current state and stale header text.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},listeners={},missing=new Set();
const node=id=>nodes[id]??={textContent:'',dataset:{}};
node('chrome-rth').textContent='Market closed';
vm.runInNewContext(fs.readFileSync('static/nadzeel.js','utf8'),{
 document:{getElementById:id=>missing.has(id)?null:node(id),querySelectorAll:()=>[]},
 window:{addEventListener:(name,fn)=>listeners[name]=fn},
 IntersectionObserver:class{observe(){}},
});
const state={broker_book:{ok:true,risk_ready:true,equity:110,day_pnl_usd:0,paper_mode:false},config:{mode:'live_manual'},loop:{rth_ok:true},ledger:{equity:1000}};
listeners['desk:state']({detail:state});
assert.equal(node('overview-session').textContent,'Market open');
assert.equal(node('overview-next').textContent,'Confirm execution account');
assert.equal(node('overview-action').href,'#live-execution-settings');
state.config.broker_identity={broker:'ibkr',account_id:'TEST'};
listeners['desk:state']({detail:state});
assert.equal(node('overview-next').textContent,'Review an idea');
assert.equal(node('overview-action').href,'#opp-panel');
state.loop.rth_ok=false;listeners['desk:state']({detail:state});assert.equal(node('overview-session').textContent,'Market closed');
delete state.loop;listeners['desk:state']({detail:state});assert.equal(node('overview-session').textContent,'Hours unknown');
state.broker_book.risk_ready=false;listeners['desk:state']({detail:state});assert.equal(node('overview-action').href,'#desk-health');
for(const id of ['desk-health','live-execution-settings','opp-panel'])missing.add(id);
listeners['desk:state']({detail:state});assert.equal(node('overview-action').href,'/desk/auto#desk-health');
state.broker_book.risk_ready=true;delete state.config.broker_identity;
listeners['desk:state']({detail:state});assert.equal(node('overview-action').href,'/desk/settings#live-execution-settings');
state.config.broker_identity={broker:'ibkr',account_id:'TEST'};
listeners['desk:state']({detail:state});assert.equal(node('overview-action').href,'/desk/auto#opp-panel');
console.log('Overview: current session data wins over stale chrome; missing account confirmation and unavailable risk data show the right next step.');
