const assert=require('node:assert/strict');
const {liveView}=require('../static/agent_desk.js');
const cfg={mode:'live_manual',session_active:true,kill_switch:{armed:true,max_position_size_usd:10,max_daily_loss_usd:2,max_trades_per_day:1}};
const state={config:{...cfg},live_risk_limits:{verified:true,max_order_usd:10,max_daily_loss_usd:2,max_orders_per_day:1},broker:{connected:true,paper_mode:false},broker_book:{ok:true,paper_mode:false,risk_ready:true,day_pnl_usd:0},loop:{rth_ok:true}};
const before=JSON.stringify(state);
assert.equal(liveView(state,cfg,0).blocks.length,0);
assert.match(liveView(state,cfg,0).title,/Manual activation/);
assert.match(liveView(state,cfg,0).pnl,/\$0.00/);
assert.match(liveView(state,cfg,0).next,/qualification.*separate/);
assert(liveView(state,cfg,26000).blocks.some(s=>s.includes('stale')));
assert(liveView(state,null,0).blocks.some(s=>s.includes('not been loaded')));
assert(liveView({...state,broker_book:{...state.broker_book,day_pnl_usd:null}},cfg,0).blocks.some(s=>s.includes('P&L')));
assert(liveView({...state,broker:{connected:false}},cfg,0).blocks.some(s=>s.includes('Gateway')));
assert(liveView({...state,loop:{rth_ok:true,outside_rth:true}},cfg,0).blocks.some(s=>s.includes('closed')));
assert(liveView({...state,broker_book:{...state.broker_book,paper_mode:true}},cfg,0).blocks.some(s=>s.includes('real-money')));
assert(liveView({...state,live_risk_limits:{verified:false}},cfg,0).blocks.some(s=>s.includes('limits')));
assert(liveView({...state,live_risk_limits:null},cfg,0).blocks.some(s=>s.includes('limits')));
assert.match(liveView(state,{...cfg,kill_switch:{armed:false}},0).limits,/Effective.*10.00/); // Current verified rails do not depend on a stale settings response.
const auto={...state,config:{mode:'auto_live',session_active:true}};
assert.match(liveView(auto,cfg,0).title,/Automatic mode · session active/); // latest state overrides older settings
assert.match(liveView({...auto,config:{mode:'auto_live',session_active:false}},cfg,0).title,/session stopped/);
assert.equal(JSON.stringify(state),before);
console.log('Live status boundary checks passed');
