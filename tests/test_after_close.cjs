const assert=require('node:assert/strict');
const R=require('../static/after_close.js'),A=require('../static/moss_avatar.js'),S=require('../static/companion_scenes.js');
const day={fox:{state:'waiting'},woman:{},after_close:{busy:true,day:'2026-09-25'}};
assert.equal(A.companionAction(0,{day}).key,'researching');
assert.equal(A.companionAction(1,{day}).key,'reading');
assert.equal(S.sceneFor(day).kind,'research');
assert.equal(S.thoughtFor(0,{day,action:{key:'researching'}}).href,'/desk/fox#nightly-review');
for(const state of ['blocked','reconciling']){
 const guarded={...day,fox:{state}};
 assert.equal(A.companionAction(0,{day:guarded}).key,state);
 assert.notEqual(S.sceneFor(guarded)?.kind,'research');
}
assert.equal(A.companionAction(0,{day:{...day,after_close:{busy:false}},cue:'nightly',speaking:true}).key,'explaining');
assert.equal(A.companionAction(0,{day,unavailable:true}).key,'unavailable');
assert.equal(A.companionAction(0,{day,cue:'trade'}).key,'order-update');
const stats={outcomes:0,session_days:0,mean_net_bps:null};
const report={day:'2026-09-25',status:'partial',models:{fox:{model:'<script>evil()</script>',result:{summary:'<img src=x onerror=bad()>',findings:[{text:'Unproven',evidence_ids:['coverage']}],hypotheses:[],uncertainties:[]}}},evidence:{outcomes:{id:'outcomes',today:stats,prior_20_observed_sessions:stats,today_recorded:5,verdict:{title:'More evidence needed',text:'No measured edge'}},market:[],headlines:[{id:'a',label:'Unsafe link',url:'javascript:alert(1)'}],coverage:{id:'coverage',news:'Headlines only'},source_health:[]}};
const html=R.reportHTML(report,'nightly-review');
assert(!html.includes('<script>')&&!html.includes('<img'));
assert(html.includes('&lt;img')&&html.includes('Unavailable bps'));
assert(!html.includes('href="javascript:'));
assert(html.includes('href="#nightly-review-coverage"'));
assert.equal(R.url('https://name:secret@example.org'), '');
assert.equal(R.url('https://example.org/source'), 'https://example.org/source');
console.log('Nightly review: truthful autonomous poses, blocker precedence, citation links, escaped model text, unsafe URLs and missing metrics passed.');
