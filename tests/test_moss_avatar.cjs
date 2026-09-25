const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {perchLayout,activityForSky,buzzNote,companionAction,actionPose}=require('../static/moss_avatar.js');
const {newsQuip}=require('../static/news_quips.js');
assert.deepEqual(['dawn','day','dusk','night'].map(s=>activityForSky(s).key),['water','weave','gathered-food','rest']);
assert.equal(activityForSky('unknown').key,'rest');
for(const width of [270,300])for(const height of [600,650,1000]){
 const g=perchLayout(width,height);
 for(const p of [g.fox,g.woman])assert.ok(p.x>=0&&p.x+g.size<=width&&p.y-g.size>=0&&p.y<=height);
 assert.ok(g.fox.y<g.woman.y-g.size,'top and bottom remain distinct');
}
function harness({width=300,height=1000,reduced=false,saved={},without=[]}={}){
 let now=0,serial=0,dialog=null;const nodes={},events={},timers=new Map(),storage=new Map([['moss_appearance_v1',JSON.stringify(saved)]]);
 for(const id of ['moss-fox','moss-woman','moss-fox-action','moss-woman-action','moss-fox-thought','moss-woman-thought','moss-motion','moss-avatar','moss-motion-note','moss-speech','moss-speech-enabled','moss-speech-text','moss-speech-link','moss-destination','moss-speech-dismiss','sidebar-companions','moss-sidebar-stage','moss-trails','moss-canyon-far','moss-canyon-near','moss-sand','moss-cacti'])
  nodes[id]={id,style:{setProperty(k,v){this[k]=v;}},dataset:{},hidden:false,value:'',checked:false,attrs:{},setAttribute(k,v){this.attrs[k]=v;},contains:()=>false,addEventListener(k,f){this[k]=f;}};
 for(const id of without)delete nodes[id];
 const sections={'desk-overview':{top:100,bottom:800},'desk-live':{top:2000,bottom:2800},'desk-options':{top:3000,bottom:3800}};
 nodes['moss-sidebar-stage'].getBoundingClientRect=()=>({width,height});
 const doc={documentElement:{dataset:{}},hidden:false,activeElement:null,getElementById:id=>sections[id]?{getBoundingClientRect:()=>sections[id]}:nodes[id],querySelector:()=>dialog,addEventListener(k,f){events[k]=f;}};
 const preference={matches:reduced,addEventListener(k,f){this[k]=f;}};
 const context={document:doc,window:{NadzeelNews:{newsQuip},DeskCompanionScenes:require('../static/companion_scenes.js'),addEventListener(k,f){events[k]=f;}},matchMedia:()=>preference,performance:{now:()=>now},innerHeight:height,
 localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},setTimeout(fn,ms){timers.set(++serial,{fn,at:now+ms});return serial;},clearTimeout:id=>timers.delete(id),
 requestAnimationFrame(){throw Error('Cozy perches must not schedule animation frames');},fetch(){throw Error('Avatar must not contact an API');}};
 vm.runInNewContext(fs.readFileSync('static/moss_avatar.js','utf8'),context);
 const positions=()=>[nodes['moss-fox'].style.transform,nodes['moss-woman'].style.transform];
 const frames=()=>[nodes['moss-fox'].dataset.frame,nodes['moss-woman'].dataset.frame];
 function advance(ms){const end=now+ms;while(now<end){now=Math.min(end,now+100);for(const [id,t]of [...timers])if(t.at<=now){timers.delete(id);t.fn();}}}
 return{nodes,events,timers,doc,preference,sections,storage,advance,positions,frames,setDialog:v=>{dialog=v;},resize:(w,h)=>{width=w;height=h;events.resize();}};
}
let h=harness({saved:{motion:'roam',avatar:'both',habitatVersion:2}});
assert.equal(h.nodes['moss-motion'].value,'cozy');assert.equal(h.nodes['moss-avatar'].value,'both');
assert.equal(JSON.parse(h.storage.get('moss_appearance_v1')).habitatVersion,3);
const home=h.positions(),initial=h.frames();h.advance(18000);assert.equal(h.frames()[0],initial[0],'long still period before fox breeze');h.advance(1600);assert.notEqual(h.frames()[0],initial[0],'fur frames change in a breeze');assert.deepEqual(h.positions(),home);
assert.equal(h.nodes['moss-fox'].style['--frame-blend'],'1','frame transition uses the second image layer');
h.advance(20000);assert.equal(h.frames()[0],initial[0],'breeze settles back to rest');assert.equal(h.nodes['moss-fox'].dataset.breeze,'false');
for(const sky of ['dawn','day','dusk','night']){h.doc.documentElement.dataset.sky=sky;h.advance(1000);assert.equal(h.nodes['moss-woman'].dataset.activity,activityForSky(sky).key);assert.deepEqual(h.positions(),home);}
h.sections['desk-overview']={top:-2000,bottom:-100};h.sections['desk-live']={top:180,bottom:1200};h.events.scroll();h.advance(1000);
assert.match(h.nodes['moss-destination'].textContent,/Live trading/);assert.deepEqual(h.positions(),home,'section changes never make them climb');
h.doc.activeElement={matches:()=>true};h.events.focusin();const frozen=h.frames();h.advance(30000);assert.deepEqual(h.frames(),frozen);
h.doc.activeElement=null;h.events.focusout();h.nodes['moss-speech'].pointerenter();const hover=h.frames();h.advance(30000);assert.deepEqual(h.frames(),hover);h.nodes['moss-speech'].pointerleave();
h.setDialog({});h.advance(1000);assert.equal(h.nodes['moss-speech'].hidden,true);h.setDialog(null);
h.events['desk:attention']({detail:{quiet:true}});h.advance(1000);assert.equal(h.nodes['moss-speech'].hidden,true);const quiet=h.frames();h.advance(30000);assert.deepEqual(h.frames(),quiet);
h.events['moss:guide']({detail:{target:'desk-options',text:'Review the exact contract.'}});assert.equal(h.nodes['moss-speech-text'].textContent,'Review the exact contract.');assert.equal(h.nodes['moss-speech'].hidden,false);
h.nodes['moss-speech-dismiss'].click();h.advance(1000);assert.equal(h.nodes['moss-speech'].hidden,true);
h.events['desk:attention']({detail:{quiet:false}});h.nodes['moss-speech-enabled'].checked=false;h.nodes['moss-speech-enabled'].change();assert.equal(h.nodes['moss-speech'].hidden,true);
h.nodes['moss-motion'].value='dock';h.nodes['moss-motion'].change();const dock=h.frames();h.advance(30000);assert.deepEqual(h.frames(),dock);
h.nodes['moss-motion'].value='hide';h.nodes['moss-motion'].change();assert.equal(h.timers.size,0);assert.equal(h.nodes['sidebar-companions'].hidden,true);
h.nodes['moss-motion'].value='cozy';h.nodes['moss-motion'].change();assert.equal(h.timers.size,1);
h.doc.hidden=true;h.events.visibilitychange();assert.equal(h.timers.size,0);h.doc.hidden=false;h.events.visibilitychange();assert.equal(h.timers.size,1);
h.events.pagehide();assert.equal(h.timers.size,0);h.events.pageshow();assert.equal(h.timers.size,1);
h=harness({reduced:true});const still=h.frames();h.advance(30000);assert.deepEqual(h.frames(),still);
h=harness({width:0});assert.equal(h.timers.size,0);
h=harness({saved:{habitatVersion:3,avatar:'woman',motion:'dock'}});assert.equal(h.nodes['moss-fox'].hidden,true);assert.equal(h.nodes['moss-woman'].hidden,false);
const observed=Date.now(),buzz={ticker:'SPY',source:'Stocktwits',cachedAt:new Date(observed-1000).toISOString(),stale:false};
assert.match(buzzNote(buzz,observed),/SPY.*Stocktwits.*not a buy signal/);
for(const patch of [{stale:true},{cachedAt:null},{cachedAt:'bad'},{cachedAt:new Date(observed+1).toISOString()},{cachedAt:new Date(observed-420001).toISOString()},{ticker:'<img onerror=bad>'},{source:'mock feed'}])assert.equal(buzzNote({...buzz,...patch},observed),'');
h=harness();let consumed=0;const cue={detail:buzz,preventDefault(){consumed++;}};
const beforeBuzz=h.positions();h.events['moss:buzz'](cue);
assert.equal(consumed,1);assert.equal(h.nodes['moss-speech'].dataset.speaker,'fox');assert.equal(h.nodes['moss-speech'].dataset.topic,'buzz');assert.equal(h.nodes['moss-fox'].dataset.buzz,'true');assert.equal(h.nodes['moss-woman'].dataset.buzz,'false');assert.equal(h.nodes['moss-speech-link'].href,'/desk/research#buzz-panel');assert.deepEqual(h.positions(),beforeBuzz);
h.advance(19000);assert.equal(h.nodes['moss-fox'].dataset.buzz,'false');assert.equal(h.nodes['moss-speech'].dataset.topic,'guide');
h.events['moss:buzz'](cue);assert.equal(consumed,2);assert.equal(h.nodes['moss-fox'].dataset.buzz,'false','suppress repeat cues during three-minute cooldown');
h.events['desk:attention']({detail:{quiet:true}});h.events['moss:buzz'](cue);assert.equal(consumed,2,'quiet mode does not claim the normal alert');
h.events['desk:attention']({detail:{quiet:false}});h.advance(180000);h.events['moss:guide']({detail:{target:'desk-options',text:'User requested contract guidance.'}});h.events['moss:buzz'](cue);assert.equal(consumed,2);assert.equal(h.nodes['moss-speech-text'].textContent,'User requested contract guidance.');
h=harness({saved:{habitatVersion:3,avatar:'woman'}});h.events['moss:buzz'](cue);assert.equal(consumed,2,'never assign Fox cues to Changing Woman');
h=harness({width:0});h.events['moss:buzz'](cue);assert.equal(consumed,2,'narrow or hidden perches retain normal alert fallback');
h=harness({reduced:true});h.events['moss:buzz'](cue);assert.equal(h.nodes['moss-fox'].dataset.buzz,'false');assert.equal(h.nodes['moss-speech'].dataset.topic,'buzz','reduced motion keeps the information without moving');
console.log('Fox buzz: fresh source timestamps, invalid/mock/future rejection, finite cue, cooldown, Fox-only attribution, reduced motion and normal-alert fallback passed.');
const newsNow=Date.now(),story=newsQuip({id:'story-one',title:'Quarterly earnings announced',url:'https://www.cnbc.com/story',source:'CNBC',fresh:true,published_ts:(newsNow-60000)/1000,checked_at:(newsNow-1000)/1000});
let heard=0;const newsCue={detail:story,preventDefault(){heard++;}};
h=harness();h.events['moss:news'](newsCue);
assert.equal(heard,1);assert.equal(h.nodes['moss-speech'].dataset.speaker,'woman');assert.equal(h.nodes['moss-speech'].dataset.topic,'news');assert.equal(h.nodes['moss-speech-link'].href,story.url);assert.match(h.nodes['moss-speech-link'].textContent,/CNBC/);assert.equal(h.nodes['moss-speech-link'].target,'_blank');
h.advance(33000);assert.equal(h.nodes['moss-speech'].dataset.topic,'guide');assert.equal(h.nodes['moss-speech-link'].target,'_self');
h.events['moss:news'](newsCue);assert.equal(heard,1,'five-minute cooldown');
h=harness({saved:{habitatVersion:3,avatar:'fox'}});h.events['moss:news'](newsCue);assert.equal(heard,1,'never assign Changing Woman news to Fox');
h=harness();h.events['desk:attention']({detail:{quiet:true}});h.events['moss:news'](newsCue);assert.equal(heard,1);
h=harness();h.nodes['moss-speech-enabled'].checked=false;h.events['moss:news'](newsCue);assert.equal(heard,1);
h=harness();h.setDialog({});h.events['moss:news'](newsCue);assert.equal(heard,1);
h=harness();h.doc.activeElement={matches:()=>true};h.events['moss:news'](newsCue);assert.equal(heard,1);
h=harness();h.events['moss:guide']({detail:{target:'desk-options',text:'Requested guidance'}});h.events['moss:news'](newsCue);assert.equal(heard,1);
h=harness({reduced:true});h.events['moss:news'](newsCue);assert.equal(heard,2);assert.equal(h.nodes['moss-speech'].dataset.topic,'news');assert.equal(h.nodes['moss-woman'].dataset.still,'true');
h=harness({width:300,height:340});assert.equal(h.timers.size,0);assert.equal(h.nodes['moss-speech'].hidden,true);
const beforeShortNews=heard,beforeShortBuzz=consumed;h.events['moss:news'](newsCue);h.events['moss:buzz'](cue);
assert.equal(heard,beforeShortNews,'short stage never consumes undisplayed news');assert.equal(consumed,beforeShortBuzz,'short stage retains buzz fallback');
h.nodes['moss-speech-enabled'].checked=false;h.nodes['moss-speech-enabled'].change();assert.equal(h.nodes['moss-speech'].hidden,true);
h.resize(300,1000);assert.equal(h.nodes['moss-speech'].hidden,true,'speech-off survives resized geometry');assert.equal(h.timers.size,1);
h.nodes['moss-speech-enabled'].checked=true;h.nodes['moss-speech-enabled'].change();h.events['moss:news'](newsCue);assert.equal(heard,beforeShortNews+1,'unconsumed news can display after resize');assert.equal(h.nodes['moss-speech'].dataset.topic,'news');
h.resize(300,340);h.events['desk:attention']({detail:{quiet:true}});assert.equal(h.nodes['moss-speech'].hidden,true);assert.equal(h.timers.size,0);
h.events.pagehide();assert.equal(h.nodes['moss-speech'].hidden,true);h.resize(300,1000);assert.equal(h.timers.size,0);h.events.pageshow();assert.equal(h.timers.size,1);assert.equal(h.nodes['moss-speech'].hidden,true,'quiet setting survives page lifecycle');
console.log('Changing Woman news: attribution, external source link, expiry, cooldown, woman-only ownership, quiet/speech-off/typing/dialog/guidance suppression and reduced motion passed.');
const daySnap={fox:{state:'watching',headline:'Fox is on watch.',latest_trade:{id:'t1',text:'Bought 2 NVDA at $100.00.'}},
 woman:{headline:'Changing Woman has the chores done.',reasoning:[{key:'event_soon:x',level:'caution',text:'FOMC Press Conference (2:30 PM ET) is in 40 min.'}]}};
h=harness();h.events['desk:day']({detail:daySnap});
assert.equal(h.nodes['moss-speech-text'].textContent,'Bought 2 NVDA at $100.00.');assert.equal(h.nodes['moss-destination'].textContent,'Fox · broker agent');
assert.equal(h.nodes['moss-speech'].dataset.topic,'trade');assert.equal(h.nodes['moss-speech'].dataset.speaker,'fox');
h.events['desk:day']({detail:daySnap});assert.equal(h.nodes['moss-speech'].dataset.topic,'trade','her note waits for his trade bubble');
h.advance(21000);h.events['desk:day']({detail:daySnap});
assert.equal(h.nodes['moss-speech'].dataset.topic,'reasoning');assert.equal(h.nodes['moss-speech'].dataset.speaker,'woman');assert.match(h.nodes['moss-speech-text'].textContent,/FOMC/);
h.advance(31000);assert.equal(h.nodes['moss-speech'].dataset.topic,'day','between announcements they narrate the day');
assert.ok(['Fox is on watch.',daySnap.woman.reasoning[0].text].includes(h.nodes['moss-speech-text'].textContent));
assert.equal(h.nodes['moss-speech-link'].href,'/desk/overview#desk-day');assert.equal(h.nodes['moss-speech-link'].textContent,'Today at the desk');
h.events['desk:day']({detail:daySnap});assert.equal(h.nodes['moss-speech'].dataset.topic,'day','a trade is announced once');
assert.equal(h.nodes['moss-fox'].attrs['aria-label'],'Fox, your broker agent · On watch · Fox is on watch.');
h=harness();h.events['desk:day']({detail:{fox:{state:'off',headline:'Fox is off duty.'},woman:{reasoning:[]}}});h.advance(1000);assert.equal(h.nodes['moss-speech'].dataset.topic,'guide','off duty keeps section guidance');
h=harness();h.events['desk:attention']({detail:{quiet:true}});h.events['desk:day']({detail:daySnap});assert.notEqual(h.nodes['moss-speech'].dataset.topic,'trade','quiet desk stays quiet');
h=harness({saved:{habitatVersion:3,avatar:'woman'}});h.events['desk:day']({detail:daySnap});assert.notEqual(h.nodes['moss-speech'].dataset.topic,'trade','trades belong to Fox');
// Pages without the Paper page's appearance controls still seat both companions.
h=harness({without:['moss-motion','moss-avatar','moss-speech-enabled','moss-motion-note']});h.advance(1000);
assert.ok(h.positions().every(t=>/translate3d/.test(t||'')),'both companions placed without the settings controls');
assert.notEqual(h.positions()[0],h.positions()[1],'Fox and Changing Woman sit apart');
h=harness({saved:{habitatVersion:3,avatar:'fox'},without:['moss-motion','moss-avatar','moss-speech-enabled','moss-motion-note']});h.advance(1000);
assert.equal(h.nodes['moss-woman'].hidden,false,'retired character choices cannot hide an autonomous companion');
h=harness({saved:{habitatVersion:3,avatar:'woman',motion:'hide'},without:['moss-motion','moss-avatar']});
assert.equal(h.nodes['moss-fox'].hidden,false);assert.equal(h.nodes['sidebar-companions'].hidden,false);
assert.equal(JSON.parse(h.storage.get('moss_appearance_v1')).motion,'cozy');
console.log('Desk day: Fox announces each trade once, Changing Woman raises cautions after him, day narration alternates, quiet and hidden companions stay silent passed.');
// Character hands/faces/props follow the observed state, independently of which bubble is speaking.
for(const [state,key,row] of [['researching','researching',2],['reconciling','reconciling',2],['blocked','blocked',2],['holding','holding',0],['watching','watching',0],['off','resting',3],['resting','resting',3],['done','resting',3]]){
 const action=companionAction(0,{day:{fox:{state}}});assert.equal(action.key,key);assert.equal(action.sheet,'fox');assert.equal(action.row,row);
}
assert.equal(companionAction(0,{day:{fox:{state:'reconciling'}},cue:'trade'}).label,'Order update','an update does not claim a fill or profit');
assert.equal(companionAction(0,{day:{fox:{state:'waiting'}}}).sheet,'breeze');
assert.equal(companionAction(1,{sky:'day'}).key,'weave');
assert.equal(companionAction(1,{cue:'news'}).key,'reading-news');
const think=companionAction(1,{day:daySnap,speaking:true});
assert.equal(actionPose(think,0,false).sheet,'woman');assert.equal(actionPose(think,9600,false).sheet,'gesture');
assert.equal(actionPose(think,9600,false).frame,2,'open hand explains the caution');
assert.deepEqual(actionPose(think,9600,true),{sheet:'woman',row:2,frame:3},'reduced motion keeps the hand-to-chin pose without gestures');
const research={fox:{state:'researching',ticker:'NVDA',headline:'Fox is researching NVDA.'},woman:{headline:'Reading along.',reasoning:[],chores:[]}};
h=harness();h.events['desk:day']({detail:research});const researchHome=h.positions();
assert.equal(h.nodes['moss-fox'].dataset.action,'researching');assert.equal(h.nodes['moss-woman'].dataset.action,'reading');
assert.equal(h.nodes['moss-fox'].dataset.sheet,'fox');assert.match(h.nodes['moss-fox-action'].textContent,/NVDA/);
const readingFrame=h.frames();h.advance(1600);assert.notDeepEqual(h.frames(),readingFrame,'both actors actually change reading frames');assert.deepEqual(h.positions(),researchHome);
h.doc.activeElement={matches:()=>true};h.events.focusin();const typingFrame=h.frames();h.advance(6000);assert.deepEqual(h.frames(),typingFrame);assert.equal(h.nodes['moss-fox'].dataset.still,'true');
h.doc.activeElement=null;h.events.focusout();h.setDialog({});h.advance(1000);assert.equal(h.nodes['moss-woman'].dataset.still,'true');h.setDialog(null);
h.doc.hidden=true;h.events.visibilitychange();assert.equal(h.nodes['moss-fox'].dataset.still,'true');assert.equal(h.timers.size,0);
h.doc.hidden=false;h.events.visibilitychange();assert.equal(h.nodes['moss-fox'].dataset.still,'false');
h.events['desk:day-unavailable']();assert.equal(h.nodes['moss-fox'].dataset.action,'unavailable');assert.equal(h.nodes['moss-woman'].dataset.action,'unavailable');
h.events['desk:day']({detail:research});assert.equal(h.nodes['moss-fox'].dataset.action,'researching');h.advance(91000);assert.equal(h.nodes['moss-fox'].dataset.action,'unavailable','missed polls expire activity');
for(const as_of of ['bad',new Date(Date.now()-120000).toISOString(),new Date(Date.now()+60000).toISOString()]){h.events['desk:day']({detail:{...research,as_of}});assert.equal(h.nodes['moss-fox'].dataset.action,'unavailable','invalid, old and future snapshots do not animate work');}
h.events['desk:day']({detail:{...research,as_of:new Date().toISOString()}});assert.equal(h.nodes['moss-fox'].dataset.action,'researching');
h=harness({reduced:true});h.events['desk:day']({detail:research});const reducedRead=h.frames();h.advance(10000);assert.deepEqual(h.frames(),reducedRead);assert.equal(h.nodes['moss-fox-action'].textContent,'Researching NVDA');
h=harness();h.events['desk:day']({detail:daySnap});assert.equal(h.nodes['moss-fox'].dataset.action,'order-update');assert.equal(h.nodes['moss-woman'].dataset.action,'thinking');
h.advance(21000);h.events['desk:day']({detail:daySnap});assert.equal(h.nodes['moss-fox'].dataset.action,'watching');
h.advance(3000);assert.equal(h.nodes['moss-woman'].dataset.sheet,'gesture');
h.advance(31000);h.events['desk:day']({detail:daySnap});assert.equal(h.nodes['moss-fox'].dataset.action,'watching','repeat polls do not replay an order gesture');
assert.ok(JSON.parse(h.storage.get('desk_day_trades_v1')).includes('t1'),'remember event IDs across page visits');
h=harness();h.events['desk:day']({detail:{fox:{state:'watching'},woman:{reasoning:[],chores:[{key:'calendar',label:'Check the calendar',state:'attention'}]}}});
assert.equal(h.nodes['moss-woman'].dataset.action,'checking');assert.match(h.nodes['moss-woman-action'].textContent,/needs attention/);
assert.match(h.nodes['moss-woman-thought'].href,/#dd-chore-calendar$/,'thought opens the exact chore');
const paired={fox:{state:'watching',headline:'On watch'},woman:{reasoning:[{key:'event:test',level:'caution',text:'Recorded timing caution'}],chores:[]}};
h=harness();h.events['desk:day']({detail:paired});h.advance(8500);assert.equal(h.nodes['moss-woman'].dataset.sheet,'gesture');assert.equal(h.nodes['moss-woman'].dataset.frame,'2');assert.equal(h.nodes['moss-fox'].dataset.expression,'listening');
h.events['desk:day']({detail:paired});assert.equal(h.nodes['moss-woman'].dataset.frame,'2','repeated snapshots do not restart a scene');
h.events['moss:appearance']({detail:{size:'large',frequency:'gentle'}});assert.equal(h.nodes['moss-fox'].style.width,'128px');assert.equal(JSON.parse(h.storage.get('moss_appearance_v1')).frequency,'gentle');
h.events['moss:appearance']({detail:{size:'bad',frequency:'bad'}});assert.equal(h.nodes['moss-fox'].style.width,'128px');assert.equal(JSON.parse(h.storage.get('moss_appearance_v1')).frequency,'gentle','unknown appearance values are ignored');
h.events['desk:day-unavailable']();assert.equal(h.nodes['moss-fox'].dataset.action,'unavailable');assert.equal(h.nodes['moss-woman'].dataset.expression,'composed');
console.log('Action poses: reading, waiting, reconciling, thought-to-gesture sequence, chores, one-shot order updates, stale-feed recovery and motion pauses passed.');
console.log('Cozy perches: stationary top/bottom through task changes, occasional expressions, pause/dock/reduced motion, dismissal, preferences migration, hidden/narrow lifecycle, and no animation loop or network actions passed.');
