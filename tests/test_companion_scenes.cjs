const assert=require('node:assert/strict');
const S=require('../static/companion_scenes.js'),A=require('../static/moss_avatar.js');
const day={fox:{state:'researching',ticker:'TEST',recent:[]},woman:{reasoning:[],chores:[]}},now=Date.now();
assert.equal(S.sceneFor(day).kind,'research');
day.woman.reasoning=[{key:'event:actual',level:'caution',text:'A scheduled announcement is approaching.'}];
const scene=S.sceneFor(day),snapshot=JSON.stringify(day);
assert.equal(scene.kind,'caution');
assert.equal(S.scenePose(scene,1,4500,'balanced',false).expression,'raised-brow');
assert.equal(S.scenePose(scene,1,8500,'balanced',false).frame,2);
assert.equal(S.scenePose(scene,0,8500,'balanced',false).expression,'listening');
assert.equal(S.scenePose(scene,1,8500,'balanced',true),null);
assert.equal(S.scenePose(scene,1,61000,'gentle',false),null);
assert.equal(S.scenePose(scene,1,61000,'balanced',false).sheet,'woman');
assert.equal(JSON.stringify(day),snapshot,'presentation never changes source data');
for(const state of ['off','resting','waiting','blocked','reconciling','done']){
 const latest={...day,fox:{state}},sc=S.sceneFor(latest);
 assert.equal(S.scenePose(sc,0,8500,'balanced',false),null,`${state} remains visible while she thinks`);
 assert.equal(S.scenePose(sc,1,8500,'balanced',false).frame,2);
 assert.notEqual(sc.key,scene.key,'state changes update the scene');
}
const pass={fox:{state:'watching',recent:[{kind:'skip',id:'recorded-pass',at:new Date(now).toISOString()}]},woman:{}};
assert.equal(S.sceneFor(pass,now).kind,'pass');assert.equal(S.sceneFor(pass,now+120001),null);
pass.fox.recent[0].at=new Date(now+1).toISOString();assert.equal(S.sceneFor(pass,now),null);
assert.equal(S.scenePose({kind:'pass'},0,60000,'lively',false),null);
for(const [actor,key,prop] of [[0,'researching','chart'],[0,'waiting','clock'],[0,'order-update','receipt'],[1,'reading-news','news'],[1,'explaining','journal'],[1,'thinking','calendar']])
 assert.equal(S.propFor(actor,{key},day),prop);
const thought=S.thoughtFor(1,{day,action:{key:'thinking'}});
assert.equal(decodeURIComponent(thought.href.split('#')[1]),S.anchor('note','event:actual'));
assert.equal(thought.detail,day.woman.reasoning[0].text);
assert.ok(S.thoughtFor(1,{day,action:{key:'reading-news'},news:{url:'javascript:bad()',title:'Untrusted'}}).href.startsWith('/desk/'));
assert.equal(S.thoughtFor(0,{unavailable:true}).href,'/desk/auto#desk-health');
assert.equal(S.thoughtFor(0,{}).href,'/desk/overview#desk-day','loading links to actual status');
for(const width of[270,300])for(const height of[350,600,720,1000])for(const scale of['compact','comfortable','large']){
 const g=A.perchLayout(width,height,scale);for(const p of[g.fox,g.woman])assert.ok(p.x>=0&&p.x+g.size<=width&&p.y-g.size>=0&&p.y<=height);
}
console.log('Autonomous scenes: actual state precedence, finite passes, current evidence links, props, reduced motion and geometry passed.');
for(const actor of [0,1]){
 for(const key of ['blocked','unavailable','reconciling'])assert.equal(S.portraitFor(actor,{key,speaking:true},null,4000,false).frame,2,'a blocker does not turn into a cheerful speaking expression');
 const action={key:'researching',speaking:true};
 assert.equal(S.portraitFor(actor,action,null,0,false).frame,1);
 assert.equal(S.portraitFor(actor,action,null,4000,false).frame,3);
 assert.equal(S.portraitFor(actor,action,null,9000,false).frame,1,'finite speaking beat settles');
 assert.equal(S.portraitFor(actor,action,null,4000,true).frame,1,'still expression retains current meaning');
 assert.equal(S.portraitFor(actor,action,null,4000,true).row,actor);
}
assert.match(S.briefFor(0,{fox:{state:'watching'}}).next,/costs/);
assert.match(S.briefFor(0,{fox:{state:'off'}}).next,/off/);
assert.match(S.briefFor(1,{woman:{},research:{fresh_sources:2,total_sources:10,fresh_headlines:3,refresh_seconds:300}}).next,/5 minutes/);
assert.equal(S.thoughtFor(1,{day:{research:{summary:'2 current sources'}},action:{key:'sourcing'}}).href,'/desk/research#companion-news');
console.log('Expression mapping and distinct evidence-oriented trader values passed.');
