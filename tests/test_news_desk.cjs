const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {newsQuip}=require('../static/news_quips.js');
const nodes={},events={},timers=new Map(),spoken=[],requests=[],storage=new Map();let next=0,fail=false,accept=true,stall=false;
function node(id){return nodes[id]??={id,children:[],textContent:'',hidden:false,append(...x){this.children.push(...x);},replaceChildren(...x){this.children=x;}};}
const now=Date.now(),row={id:'story',title:'<script>earnings</script>',url:'https://www.cnbc.com/story',source:'CNBC',fresh:true,published_ts:(now-50000)/1000,checked_at:(now-1000)/1000};
const payload={busy:false,sources:[{name:'CNBC',home:'https://www.cnbc.com/',kind:'Reporting',status:'connected',focus:'US markets',recent:1,checked_at:now/1000}],items:[row]};
const doc={hidden:false,getElementById:node,createElement:tag=>({...node('new-'+(++next)),tag}),addEventListener:(k,f)=>events[k]=f};
const context={document:doc,window:{NadzeelNews:{newsQuip},addEventListener:(k,f)=>events[k]=f,dispatchEvent(e){spoken.push(e);if(accept)e.preventDefault();return !e.defaultPrevented;}},
 sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
 CustomEvent:class{constructor(type,opts){this.type=type;Object.assign(this,opts);this.defaultPrevented=false;}preventDefault(){this.defaultPrevented=true;}},
 AbortController,
 fetch:async(url,options)=>{assert.equal(url,'/api/companion/news');assert.ok(options.signal);requests.push(url);if(stall)return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))));return {ok:!fail,json:async()=>payload};},
 setTimeout(fn){timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)};
const settle=()=>new Promise(r=>setImmediate(r));
(async()=>{
 vm.runInNewContext(fs.readFileSync('static/news_desk.js','utf8'),context);await settle();
 assert.equal(spoken.length,1);assert.equal(spoken[0].type,'moss:news');assert.equal(node('headline-story').textContent,row.title);assert.equal(node('headline-story').href,row.url);assert.equal(node('headline-health').textContent,'1 / 1 feeds available');
 assert.match(storage.get('changing_woman_news_v1'),/story/);
 events.pageshow({persisted:true});await settle();assert.equal(spoken.length,1,'consumed story is not repeated');
 payload.items=[{...row,id:'second'}];accept=false;events.pageshow({persisted:true});await settle();assert.equal(spoken.length,2);assert(!storage.get('changing_woman_news_v1').includes('second'),'suppressed speech remains eligible later');
 accept=true;events.pageshow({persisted:true});await settle();assert.equal(spoken.length,3);assert(storage.get('changing_woman_news_v1').includes('second'));
 payload.items=[{...row,id:'stale',fresh:false}];events.pageshow({persisted:true});await settle();assert.equal(spoken.length,3);assert.equal(node('headline-story').hidden,true);
 fail=true;events.pageshow({persisted:true});await settle();assert.match(node('headline-status').textContent,/unavailable/);assert.match(node('headline-health').textContent,/unavailable/);assert.equal(spoken.length,3);
 stall=true;events.pageshow({persisted:true});await settle();[...timers.values()][0]();await settle();assert.match(node('headline-health').textContent,/unavailable/);stall=false;
 const count=requests.length;doc.hidden=true;events.visibilitychange();assert.equal(timers.size,0);events.pageshow({persisted:true});await settle();assert.equal(requests.length,count);
 doc.hidden=false;events.visibilitychange();await settle();assert.equal(requests.length,count+1);
 events.pagehide();assert.equal(timers.size,0);
 console.log('News desk: read-only polling, text-safe headlines, one-time delivery, suppressed-story retry, stale/failure behavior and hidden-page lifecycle passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
