const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {accountFunds}=require('../static/desk_brief.js');
const now=Date.now(),book={ok:true,currency:'USD',cash:110,settled_cash:0,buying_power:null,available_funds:-2,
 funds_observation:{currency:'USD',read_at:new Date(now).toISOString(),broker_value_time:null,
 field_status:{cash:'reported',settled_cash:'reported',buying_power:'missing',available_funds:'reported',excess_liquidity:'conflicting'}}};
let r=accountFunds(book,now);
assert.equal(r.available,true);assert.equal(r.rows[0].text,'$110.00');assert.equal(r.rows[1].text,'$0.00');
assert.equal(r.rows[2].text,'Not reported');assert.equal(r.rows[3].text,'-$2.00');assert.equal(r.rows[4].text,'Conflicting values');
for(const change of [{ok:false},{currency:'EUR'},{funds_observation:null},
 {funds_observation:{...book.funds_observation,currency:'BASE'}},
 {funds_observation:{...book.funds_observation,read_at:new Date(now-45001).toISOString()}},
 {funds_observation:{...book.funds_observation,read_at:new Date(now+1).toISOString()}},
 {funds_observation:{...book.funds_observation,read_at:'2026-09-27T00:00:00'}}]){
 r=accountFunds({...book,...change},now);assert.equal(r.available,false);assert.ok(r.rows.every(x=>!x.text.includes('$')));
}
for(const value of [true,'100',NaN,Infinity,null,undefined])assert.equal(accountFunds({...book,cash:value},now).rows[0].text,'Not verified');
assert.equal(accountFunds({...book,funds_observation:{...book.funds_observation,field_status:{cash:'<img onerror=bad>'}}},now).rows[0].text,'Not verified');

const nodes={},events={},intervals=[];let clock=now;
function node(id){return nodes[id]??=( {textContent:'',children:[],value:'25',handlers:{},
 append(...x){this.children.push(...x);},replaceChildren(){this.children=[];},addEventListener(n,f){this.handlers[n]=f;}});}
const visible=new Set(['concentration-funds']);
vm.runInNewContext(fs.readFileSync('static/desk_brief.js','utf8'),{
 document:{getElementById:id=>visible.has(id)?node(id):null,createElement:()=>node('generated-'+Object.keys(nodes).length)},
 window:{addEventListener:(name,fn)=>events[name]=fn},Date:class extends Date{static now(){return clock;}},
 setInterval:fn=>intervals.push(fn),URL,Map,Set
});
events['desk:state']({detail:{broker_book:book}});
assert.match(node('concentration-funds').children.map(x=>x.textContent).join('\n'),/Settled cash: \$0.00/);
assert.match(node('concentration-funds').children.at(-1).textContent,/did not attach a value-update time/);
clock+=45001;intervals[0]();
assert.doesNotMatch(node('concentration-funds').children.map(x=>x.textContent).join('\n'),/\$110/);
assert.match(node('concentration-funds').children.at(-1).textContent,/does not mean.*zero/);
console.log('Account funds UI: zero vs unknown, negative balances, source age, malformed values, safe text and timer expiry passed.');
