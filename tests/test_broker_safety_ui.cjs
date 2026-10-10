const test=require('node:test'),assert=require('node:assert/strict');
const {view,commitmentView}=require('../static/broker_safety.js');
const now=Date.parse('2026-09-26T15:00:00Z');
test('only fresh successful records expose holdings',()=>{
 const report={ok:true,checked_at:new Date(now).toISOString(),summary:'Checked',rows:[{symbol:'TEST'}]};
 assert.equal(view(report,now).rows.length,1);
 for(const change of [{ok:false},{checked_at:null},{checked_at:new Date(now-46000).toISOString()},
                      {checked_at:new Date(now+1000).toISOString()}]){
  const v=view({...report,...change},now);assert.equal(v.fresh,false);assert.deepEqual(v.rows,[]);
 }
 assert.equal(view(null,now).fresh,false);
});

test('missing or old commitments cannot retain account amounts',()=>{
 const report={ok:true,checked_at:new Date(now).toISOString(),commitments:{ok:true,summary:'One buy',groups:[],orders:[],buy_limits:[{currency:'USD',amount:830,order_count:1}]}};
 assert.match(commitmentView(report,now).totals[0],/USD 830.00/);
 for(const change of [{ok:false},{checked_at:new Date(now-46000).toISOString()},{checked_at:'2026-09-26T15:00:00'},{commitments:{ok:false,summary:'Conflicting identifiers'}}]){
  const v=commitmentView({...report,...change},now);assert.equal(v.available,false);assert.deepEqual(v.totals,[]);
 }
 assert.equal(commitmentView(null,now).available,false);
});

test('foreign currency, excluded costs and sales remain visible separately',()=>{
 const report={ok:true,checked_at:new Date(now).toISOString(),commitments:{ok:true,summary:'Checked',short_option_count:1,unknown_buy_count:1,contingent_buy_count:2,sell_order_count:1,
  groups:[{currency:'EUR',asset_type:'OPT',long_count:0,short_count:1}],buy_limits:[{currency:'USD',amount:83.1,order_count:1},{currency:'EUR',amount:50,order_count:1}],
  orders:[{instrument:'<img src=x>',side:'buy',remaining_qty:83,asset_type:'STK',currency:'USD',limit_amount:83.1,note:'Before fees.'}]}};
 const result=commitmentView(report,now);
 assert.match(result.totals[0],/^USD 83.10/);assert.match(result.totals[1],/^EUR 50.00/);
 assert.ok(result.totals.some(t=>t.includes('unknown cost')));assert.ok(result.totals.some(t=>t.includes('not counted')));
 assert.ok(result.groups.some(t=>t.includes('assignment')));assert.match(result.orders[0].title,/<img src=x>/);
 assert.equal(report.commitments.buy_limits[0].amount,83.1);
});

test('unknown total is not a zero estimate',()=>{
 const report={ok:true,checked_at:new Date(now).toISOString(),commitments:{ok:true,orders:[{instrument:'TEST',remaining_qty:1,limit_amount:null,note:'Cost unknown'}],buy_limits:[]}};
 const result=commitmentView(report,now);assert.match(result.totals[0],/No standalone buy subtotal/);assert.doesNotMatch(result.orders[0].text,/0.00/);
});
