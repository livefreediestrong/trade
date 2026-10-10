const test=require('node:test'),assert=require('node:assert/strict');
const {rows,pct,money}=require('../static/overnight_study.js');
test('unknown results never appear as zero dollars or a profitable result',()=>{
 for(const value of [null,undefined,NaN,Infinity,'4',true]){assert.equal(pct(value),'Unavailable');assert.equal(money(value),'Unavailable');}
 assert.deepEqual(rows({available:false},5),[]);assert.deepEqual(rows({available:true,scenarios:[]},5),[]);
});
test('display selects the actual cost scenario and preserves losses',()=>{
 const r={available:true,scenarios:[{cost_bps_per_side:0,overnight:{compounded_pct:10}},{cost_bps_per_side:5,overnight:{compounded_pct:-1.2,reinvested_pnl:-12,fixed_dollar_pnl:-11,drawdown_pct:-5}}]};
 const row=rows(r,5)[0];assert.equal(row[1],'-1.20%');assert.equal(row[2],'-$12.00');assert.equal(row[3],'-$11.00');assert.equal(row[4],'-5.00%');
 assert.equal(rows(r,5)[1][1],'Unavailable');
});
