// Existing chip renderer with pure fixtures; no quote/provider requests.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('  function renderRadar('),end=source.indexOf('  function renderHeat(',start);
assert(start>=0&&end>start);
const lane={innerHTML:''};
const context={$:id=>id==='#radar-lane'?lane:null,document:{activeElement:null,body:{classList:{contains:()=>false}}},getUiMode:()=> 'advanced',syncButlerStory(){},escapeHtml:value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),lastRadarSig:''};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const row={ticker:'AAPL',pct_change:2.4,score:70,market_time:'2026-09-23T20:00:00Z',fresh:false,observation_status:'prior_session',research_flags:['prior_session_quote']};
function render(mover){context.data={config:{radar_enabled:true},radar:{movers:[mover],count:1}};vm.runInContext('renderRadar(data)',context);}
render(row);
assert.match(lane.innerHTML,/<span class="muted">Prior session · /,'label is visible, not only a tooltip');
assert(lane.innerHTML.includes(new Date(row.market_time).toLocaleString()));assert.match(lane.innerHTML,/AAPL/);assert.match(lane.innerHTML,/\+2.4%/);
const previous=lane.innerHTML,newTime='2026-09-22T20:00:00Z';render({...row,market_time:newTime});assert.notEqual(lane.innerHTML,previous);assert(lane.innerHTML.includes(new Date(newTime).toLocaleString()),'unchanged price/score must not hide timestamp corrections');
render({...row,fresh:true,observation_status:'recent',research_flags:[]});assert.doesNotMatch(lane.innerHTML,/Prior session/,'freshness changes invalidate the chip cache');
render({...row,observation_status:undefined});assert.match(lane.innerHTML,/Prior session/,'explicit prior-session research flag remains visible');
render({...row,market_time:null});assert.match(lane.innerHTML,/Prior session · market time unknown/);assert.doesNotMatch(lane.innerHTML,/Invalid Date/);
render({ticker:'TEST',pct_change:1,score:50});assert.doesNotMatch(lane.innerHTML,/Prior session|Invalid Date/,'missing fields do not invent an observation status');
console.log('Radar chips: visible prior-session label and market timestamp, timestamp/freshness cache invalidation, research-flag fallback and missing-data handling passed.');
