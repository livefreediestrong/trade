const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const S=require('../static/companion_scenes.js'),A=require('../static/moss_avatar.js'),P=require('../static/companion_preview.js');
const research=P.example('research'),caution=P.example('thinking'),now=Date.now();
assert.equal(S.sceneFor(research.day).kind,'research');assert.equal(S.sceneFor(caution.day).kind,'caution');
const snapshot=JSON.stringify(caution);
assert.equal(S.scenePose(caution.scene,1,4500,'balanced',false).expression,'raised-brow');
assert.equal(S.scenePose(caution.scene,1,8500,'balanced',false).frame,2);
assert.equal(S.scenePose(caution.scene,0,8500,'balanced',false).expression,'listening');
assert.equal(S.scenePose(caution.scene,1,8500,'balanced',true),null,'quiet and reduced motion disable choreography');
assert.equal(S.scenePose(caution.scene,1,61000,'gentle',false),null);
assert.equal(S.scenePose(caution.scene,1,61000,'balanced',false).sheet,'woman');
assert.equal(S.scenePose(caution.scene,1,31000,'lively',false).sheet,'woman');
const pass=P.example('pass',now);assert.equal(S.sceneFor(pass.day,now).kind,'pass');assert.equal(S.sceneFor(pass.day,now+120001),null);
pass.day.fox.recent[0].at=new Date(now+1).toISOString();assert.equal(S.sceneFor(pass.day,now),null,'future records do not enact a pass');
assert.equal(S.scenePose({kind:'pass'},0,60000,'lively',false),null,'a pass is not replayed by the repeat interval');
assert.equal(JSON.stringify(caution),snapshot,'choreography never changes source data');
for(const [mode,actor,prop]of [['research',0,'chart'],['waiting',0,'clock'],['order',0,'receipt'],['news',1,'news'],['journal',1,'journal'],['thinking',1,'calendar']]){
 const m=P.example(mode),action=A.companionAction(actor,{day:m.day,cue:m.cues[actor]});assert.equal(S.propFor(actor,action,m.day),prop);
}
const thought=S.thoughtFor(1,{day:caution.day,action:{key:'thinking'}});
assert.equal(decodeURIComponent(thought.href.split('#')[1]),S.anchor('note','event:example'));
assert.equal(thought.detail,caution.day.woman.reasoning[0].text);
const unsafe=S.thoughtFor(1,{day:research.day,action:{key:'reading-news'},news:{url:'javascript:bad()',title:'Untrusted headline'}});assert.ok(unsafe.href.startsWith('/desk/'));
assert.equal(S.thoughtFor(0,{unavailable:true}).href,'/desk/settings#desk-health');
for(const width of[270,300])for(const height of[350,600,720,1000])for(const scale of['compact','comfortable','large']){
 const g=A.perchLayout(width,height,scale);for(const p of[g.fox,g.woman])assert.ok(p.x>=0&&p.x+g.size<=width&&p.y-g.size>=0&&p.y<=height);
}
// Run the real preview controller with deterministic time and no network or broker facilities.
function harness(){
 let now=100,timerId=0;const timers=new Map(),events={},nodes={},outbound=[];
 const prefs={size:'comfortable',frequency:'balanced',quiet:false,reduced:false,motion:'cozy'};
 for(const id of['moss-preview-dialog','desk-companions-open','moss-preview-open','moss-preview-close','moss-preview-scene','moss-preview-replay','moss-preview-pause','moss-preview-fox','moss-preview-woman','moss-preview-fox-prop','moss-preview-woman-prop','moss-preview-fox-caption','moss-preview-woman-caption','moss-preview-story','moss-preview-motion-note','moss-character-size','moss-frequency'])nodes[id]={id,style:{setProperty(k,v){this[k]=v;}},dataset:{},attrs:{},open:false,value:'',textContent:'',setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];},addEventListener(k,fn){this[k]=fn;},focus(){doc.activeElement=this;}};
 const modal=nodes['moss-preview-dialog'];modal.showModal=()=>{modal.open=true;};modal.close=()=>{modal.open=false;modal.onclose();};modal.addEventListener=(k,f)=>{modal['on'+k]=f;};
 nodes['moss-preview-scene'].value='research';const doc={hidden:false,activeElement:null,getElementById:id=>nodes[id],addEventListener(k,fn){events[k]=fn;}};
 const media={addEventListener(k,fn){events.media=fn;}};
 const context={document:doc,window:{MossAvatars:{...A,appearance:()=>prefs,refresh(){}},DeskCompanionScenes:S,addEventListener(k,fn){events[k]=fn;},dispatchEvent(e){outbound.push(e);assert.equal(e.type,'moss:appearance','preview must never publish desk or trading events');Object.assign(prefs,e.detail);}},performance:{now:()=>now},matchMedia:()=>media,CustomEvent:function(type,init){this.type=type;this.detail=init.detail;},setTimeout(fn,ms){timers.set(++timerId,{fn,at:now+ms});return timerId;},clearTimeout:id=>timers.delete(id),fetch(){throw Error('No network allowed for preview');}};
 vm.runInNewContext(fs.readFileSync('static/companion_preview.js','utf8'),context);
 function advance(ms){const end=now+ms;while(now<end){now=Math.min(end,now+100);for(const[id,t]of [...timers])if(t.at<=now){timers.delete(id);t.fn();}}}
 function open(){nodes['desk-companions-open'].click({currentTarget:nodes['desk-companions-open'],preventDefault(){}});}
 return {nodes,prefs,events,doc,timers,outbound,advance,open,modal};
}
const h=harness();h.open();assert.equal(h.modal.open,true);assert.equal(h.nodes['moss-preview-fox'].dataset.prop,'chart');
let propWrites=0;const propNode=h.nodes['moss-preview-fox-prop'],setProp=propNode.setAttribute;propNode.setAttribute=function(k,v){propWrites++;setProp.call(this,k,v);};
h.advance(5000);assert.match(h.nodes['moss-preview-story'].textContent,/checks the evidence/);assert.equal(propWrites,0,'frame ticks do not reassign the SVG reference and refetch its asset');
h.nodes['moss-preview-pause'].click();const frozen=h.nodes['moss-preview-fox'].dataset.frame;h.advance(8000);assert.equal(h.nodes['moss-preview-fox'].dataset.frame,frozen);assert.equal(h.timers.size,0);
h.nodes['moss-preview-pause'].click();h.advance(13000);assert.match(h.nodes['moss-preview-story'].textContent,/Scene finished/);assert.equal(h.timers.size,0);
for(const mode of P.MODES){h.nodes['moss-preview-scene'].value=mode;h.nodes['moss-preview-scene'].change();assert.ok(h.nodes['moss-preview-story'].textContent);h.advance(1000);}
h.nodes['moss-character-size'].value='large';h.nodes['moss-frequency'].value='gentle';h.nodes['moss-character-size'].change();assert.equal(h.prefs.size,'large');assert.equal(h.prefs.frequency,'gentle');assert.equal(h.nodes['moss-preview-fox'].style.width,'168px');
h.prefs.reduced=true;h.events.media();assert.equal(h.timers.size,0);h.advance(20000);h.prefs.reduced=false;h.events.media();assert.doesNotMatch(h.nodes['moss-preview-story'].textContent,/Scene finished/,'time spent in reduced motion does not advance the scene');
h.doc.hidden=true;h.events.visibilitychange();assert.equal(h.timers.size,0);assert.equal(h.nodes['moss-preview-woman'].dataset.still,'true');h.doc.hidden=false;h.events.visibilitychange();assert.equal(h.timers.size,1);
h.prefs.quiet=true;h.events['desk:attention']();assert.equal(h.timers.size,0);assert.match(h.nodes['moss-preview-motion-note'].textContent,/Quiet desk/);
h.modal.close();assert.equal(h.timers.size,0);assert.equal(h.doc.activeElement,h.nodes['desk-companions-open'],'focus returns to the opener');
assert.equal(h.outbound.length,1,'only the explicit appearance change leaves preview state');
console.log('Companion scenes: coordinated beats, truthful sources, props, timing, all examples, appearance preferences, pause/replay, motion and lifecycle gates, focus return and zero trading/network events passed.');
