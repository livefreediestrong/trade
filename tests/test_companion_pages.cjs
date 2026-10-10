// Run the actual companion controller with the IDs of each split-page template.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function markup(name){
 const text=fs.readFileSync('templates/'+name,'utf8');
 return text.replace(/{%\s*include\s+['"]([^'"]+)['"]\s*%}/g,(_,file)=>markup(file));
}
const status={ok:true,settings:{name:'Moss',daily_target:10,enabled:true,use_model:true,research_short_selling:false},
 plan:{budget:10,daily_loss_limit:2,max_orders:1,symbols:['SPY'],order_type:'limit'},busy:false,error:null,market_open:true,
 briefs:[{id:'one',created_at:'2026-09-25T14:00:00Z',summary:'Recorded research',observations:[]}],notebook_count:1,
 memory:{observations:2,reviewed_outcomes:1,note:'Evidence'},learning:'Local',schedule:'Daily',
 paper_workday:{settings:{enabled:true,personality:'stoic',symbols:['SPY']},active:true,busy:false,
  phase:'waiting_for_next_cycle',next_at:'2026-09-25T15:05:00Z',today:{},evaluation:null,universe:{count:2}},
 actual_trades:{real:{insights:[],by_currency:{},executions:0},executions:[]}};
async function run(page,change={}){
 const nodes={},events=[],calls=[];
 for(const match of markup(page).matchAll(/\bid="([^"]+)"/g))nodes[match[1]]={value:'',checked:false,disabled:false,textContent:'',innerHTML:'',addEventListener(){}};
 const context={Intl,Date,URL,AbortController,Event,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
  document:{hidden:false,getElementById:id=>nodes[id]||null,addEventListener(){}},
  window:{addEventListener(){},dispatchEvent(e){events.push(e);}},setTimeout(){return 1;},clearTimeout(){},
  fetch:async(url,opts)=>{calls.push([url,opts.method]);return{ok:true,json:async()=>({...status,...change})}}};
 vm.runInNewContext(fs.readFileSync('static/companion.js','utf8'),context);
 await new Promise(r=>setImmediate(r));
 assert.ok(!events.some(e=>e.type==='moss:workday'&&e.detail.error),'Split-page rendering failed on '+page+': '+nodes['moss-insight']?.textContent);
 assert.ok(events.some(e=>e.type==='moss:journal'),'Trade events still reach companions on '+page);
 assert.ok(events.some(e=>e.type==='moss:workday'&&e.detail.workday),'Workday events still reach controls on '+page);
 assert.deepEqual(calls,[['/api/companion','GET']]);
 if(nodes['moss-history'])assert.match(nodes['moss-history'].innerHTML,/one/);
 return{nodes,events};
}
(async()=>{
 for(const page of ['overview','auto','paper','research','ticket','settings'])await run('pages/'+page+'.html');
 await run('index_all.html');
 const h=await run('pages/paper.html',{scheduler_errors:{news:'News unavailable; retrying.'}});
 assert.equal(h.nodes['moss-status'].textContent,'Research needs attention');
 assert.equal(h.nodes['moss-insight'].textContent,'News unavailable; retrying.');
 assert.equal(h.events.find(e=>e.type==='moss:state').detail.error,true);
 const p=await run('pages/paper.html',{paper_workday:{...status.paper_workday,error:'Paper maintenance unavailable'}});
 assert.equal(p.nodes['moss-insight'].textContent,'Paper maintenance unavailable');
 console.log('Companion pages: six split layouts and legacy page render without missing-panel errors; scheduler and maintenance errors visible; GET only.');
})().catch(e=>{console.error(e);process.exit(1);});
