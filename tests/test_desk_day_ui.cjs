// Today-at-the-desk panel: renders the snapshot as text and shares it with the companions.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(tag){return{tag,children:[],textContent:'',dataset:{},hidden:false,className:'',attrs:{},
 append(...xs){for(const x of xs)this.children.push(typeof x==='string'?{tag:'#text',textContent:x}:x);},
 replaceChildren(...xs){this.children=[];this.append(...xs);},setAttribute(k,v){this.attrs[k]=v;}};}
const ids=['desk-day','dd-state','dd-fox-headline','dd-positions-table','dd-positions','dd-fox-recent','dd-woman-headline','dd-notes','dd-chores','dd-events','dd-events-note','dd-wsb','dd-wsb-note'];
const nodes=Object.fromEntries(ids.map(id=>[id,node('div')]));
const snap={ok:true,fox:{state:'holding',headline:'Fox is holding off on new trades: FOMC Press Conference (2:30 PM ET).',
 managed:[{ticker:'NVDA',shares:2,entry:100,stop:99.2,target:101.6,breakeven:true}],
 recent:[{at:'2026-09-24T14:00:00+00:00',kind:'trade',text:'Bought 2 NVDA at $100.00.'}]},
 woman:{headline:'Changing Woman has the chores done.',reasoning:[{key:'x',level:'block',text:'<b>Scheduled</b> event'}],
 chores:[{key:'wsb',label:'Read the WSB threads',state:'done',detail:'Daily Discussion'}]},
 events:{guard_enabled:true,status:{sources:{bls:{ok:false,error:'HTTP 503'}}},upcoming:[{title:'FOMC Press Conference',when:'FOMC Press Conference (2:30 PM ET)',impact:'high',url:'javascript:alert(1)',guard:'2:15 PM ET–2:45 PM ET'}]},
 wsb:{configured:true,threads:[{label:'Daily Discussion'}],top:[{ticker:'NVDA',mentions_60m:40,velocity:null,bull_share:.25,live_chat_pasted:0}],live_chat_note:'Paste chat.'}};
let dispatched=null,unavailable=false;
const context={document:{getElementById:id=>nodes[id],createElement:tag=>node(tag),createTextNode:t=>({tag:'#text',textContent:t}),addEventListener(){},hidden:false},
 window:{addEventListener(){},dispatchEvent(e){dispatched=e;}},CustomEvent:function(type,init){this.type=type;this.detail=init?.detail;},
 AbortController:function(){this.signal={};this.abort=()=>{};},setTimeout:()=>1,clearTimeout(){},
 fetch:async url=>{assert.equal(url,'/api/desk-day');if(unavailable)throw Error('Disconnected');return{ok:true,json:async()=>snap};}};
vm.runInNewContext(fs.readFileSync('static/desk_day.js','utf8'),context);
setImmediate(()=>{
 assert.equal(nodes['dd-state'].textContent,'Holding off');assert.equal(nodes['dd-state'].dataset.state,'holding');
 assert.equal(nodes['dd-positions-table'].hidden,false);
 const cells=nodes['dd-positions'].children[0].children.map(c=>c.textContent);assert.deepEqual(cells.slice(0,2),['NVDA','2']);assert.match(cells[3],/\(entry\)/);
 assert.equal(nodes['dd-notes'].children[0].textContent,'<b>Scheduled</b> event','fetched text renders as text');
 assert.equal(nodes['dd-notes'].children[0].className,'dd-note is-block');
 const event=nodes['dd-events'].children[0];assert.ok(!event.children.some(c=>c.tag==='a'),'unsafe links are not links');
 assert.match(nodes['dd-events-note'].textContent,/bls \(HTTP 503\)/);
 assert.equal(nodes['dd-wsb'].children[0].children[3].textContent,'75% bear');assert.equal(nodes['dd-wsb'].children[0].children[2].textContent,'building');
 assert.equal(dispatched.type,'desk:day');assert.equal(dispatched.detail,snap);
 console.log('Desk day panel: text-only rendering, positions, notes, events with safe links, WSB lean and companion event passed.');
 unavailable=true;vm.runInNewContext(fs.readFileSync('static/desk_day.js','utf8'),context);
 setImmediate(()=>{assert.equal(nodes['dd-state'].textContent,'Unavailable');assert.equal(dispatched.type,'desk:day-unavailable');console.log('Failed desk-day fetch explicitly clears companion activity.');});
});
