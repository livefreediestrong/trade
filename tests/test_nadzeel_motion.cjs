// Exercise the actual presentation controller offline. No broker, network or orders.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {sampleScene,easternHour,qualifiedFills}=require('../static/nadzeel_motion.js');
assert.equal(easternHour(new Date('2026-09-23T13:30:00Z')),9.5);
assert.equal(easternHour(new Date('2026-01-23T14:30:00Z')),9.5);
assert.equal(sampleScene(13).key,'day');assert.equal(sampleScene(23).night,1);
assert.equal(sampleScene(23).moon.visible,true);assert.equal(sampleScene(13).sun.visible,true);
assert.equal(sampleScene(23,'day').hour,13);assert.equal(sampleScene(24).key,sampleScene(0).key);
assert.equal(sampleScene(7.5).bands[0],'#2b2d55');assert.notEqual(sampleScene(6).bands[0],sampleScene(5).bands[0]);
const now=new Date('2026-09-23T18:00:00Z');
const fill={paper_mode:false,verified:true,execution_id:'abc.01',account_id:'U1234',ticker:'TEST',ts:'2026-09-23T16:10:00Z',price:20,shares:.5,side:'buy',currency:'USD'};
const rejected=[{paper_mode:true},{paper_mode:undefined},{verified:false},{superseded_by:'abc.02'},{ts:'2026-09-23T19:00:00Z'},
  {ts:'2026-09-23T01:00:00Z'},{ts:'invalid'},{price:Infinity},{shares:NaN},{shares:'1'},{price:null},{execution_id:''},{account_id:''},{ticker:''}];
for(const patch of rejected)assert.equal(qualifiedFills({executions:[{...fill,...patch}]},now).length,0,JSON.stringify(patch));
const input={executions:[fill,fill,{...fill,account_id:'U5678'},{...fill,execution_id:'def.01',ts:'2026-09-23T16:00:00Z'}]};
const before=JSON.stringify(input), filtered=qualifiedFills(input,now);
assert.equal(filtered.length,3);assert.equal(filtered[0].execution_id,'def.01');assert.equal(JSON.stringify(input),before);
assert.deepEqual(qualifiedFills({},now),[]);

function harness({reduce=false,saved={}}={}) {
  const events={},nodes={},callbacks=new Map(),stores=new Map(Object.entries(saved));let serial=0,elapsed=0;
  class Node {
    constructor(id=''){this.id=id;this.children=[];this.dataset={};this.style={setProperty(k,v){this[k]=v;}};this.attrs={};this.listeners={};this.value='';this.checked=false;this.hidden=false;this.textContent='';this.options=[{text:'Night'}];this.selectedIndex=0;}
    append(...items){for(const n of items)this.children.push(...(n.fragment?n.children:[n]));}
    replaceChildren(...items){this.children=[];this.append(...items);}
    addEventListener(k,fn){this.listeners[k]=fn;}
    setAttribute(k,v){this.attrs[k]=v;}
    querySelector(q){return this.children.find(n=>q==='.module-title,summary'?n.id==='module-title':n.attrs['aria-pressed']==='true');}
    contains(n){return this.children.includes(n);}
    focus(){document.activeElement=this;}
  }
  for(const id of ['desk-sky','desk-landscape','desk-scene-rotate','desk-scene-motion','desk-sky-preview','scene-stars','scene-clouds','scene-wind','scene-mark','scene-sun','scene-moon','desk-clock','scene-motion-note','trade-sky','trade-sky-stars','trade-sky-lines','trade-sky-detail','trade-sky-count','trade-sky-empty','trade-sky-source'])nodes[id]=new Node(id);
  nodes['trade-sky'].append(new Node('module-title'));
  const root=new Node('root'),preference={matches:reduce,addEventListener(k,fn){this[k]=fn;}};
  const document={documentElement:root,hidden:false,activeElement:null,getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node(),
    createDocumentFragment:()=>Object.assign(new Node(),{fragment:true}),addEventListener(k,fn){events[k]=fn;}};
  const context={document,window:{addEventListener(k,fn){events[k]=fn;}},Date,Intl,matchMedia:()=>preference,performance:{now:()=>elapsed},
    localStorage:{getItem:k=>stores.get(k),setItem:(k,v)=>stores.set(k,v)},setTimeout(fn,delay){callbacks.set(++serial,{fn,delay});return serial;},clearTimeout(id){callbacks.delete(id);}};
  vm.runInNewContext(fs.readFileSync('static/constellations.js','utf8'),context);
  vm.runInNewContext(fs.readFileSync('static/nadzeel_motion.js','utf8'),context);
  return {nodes,root,events,document,preference,callbacks,stores,change(id){nodes[id].listeners.change();},click(id){nodes[id].listeners.click();},step(ms){elapsed+=ms;const first=callbacks.entries().next().value;assert.ok(first);callbacks.delete(first[0]);first[1].fn();}};
}
let h=harness();
assert.equal(h.root.dataset.sceneMotion,'on');assert.equal(h.nodes['scene-stars'].children.length,3);assert.equal(h.nodes['scene-clouds'].children.length,7);assert.equal(h.callbacks.size,1);
assert.equal(h.nodes['desk-scene-rotate'].checked,true);assert.equal(h.root.dataset.sceneRotate,'true');
h.step(120000);assert.equal(h.root.dataset.landscape,'clouds');
const rotatingColor=h.root.style['--sky1'];h.step(120000);assert.equal(h.root.dataset.landscape,'earth');assert.notEqual(h.root.style['--sky1'],rotatingColor);
h.events['desk:attention']({detail:{quiet:true}});const pausedColor=h.root.style['--sky1'];h.step(240000);assert.equal(h.root.style['--sky1'],pausedColor);assert.equal(h.root.dataset.sceneRotate,'false');
h.events['desk:attention']({detail:{quiet:false}});assert.equal(h.root.style['--sky1'],pausedColor,'resume has no elapsed-time jump');h.step(1000);assert.equal(h.root.dataset.sceneRotate,'true');
h.click('desk-sky-preview');assert.equal(h.root.dataset.scenePreview,'true');assert.equal([...h.callbacks.values()][0].delay,100);
h.step(12001);assert.equal(h.root.dataset.scenePreview,'false');assert.equal([...h.callbacks.values()][0].delay,1000);
h.nodes['desk-sky'].value='day';h.change('desk-sky');assert.equal(h.root.dataset.sky,'day');assert.equal(h.nodes['scene-moon'].hidden,true);
assert.equal(h.nodes['desk-scene-rotate'].checked,false);assert.equal(h.stores.get('nadzeel_scene_rotate_v1'),'off');
h.nodes['desk-scene-motion'].checked=false;h.change('desk-scene-motion');assert.equal(h.root.dataset.sceneMotion,'off');assert.equal(h.nodes['desk-sky-preview'].disabled,true);h.click('desk-sky-preview');assert.equal(h.root.dataset.scenePreview,'false');
h.document.hidden=true;h.events.visibilitychange();assert.equal(h.callbacks.size,0);assert.equal(h.root.dataset.sceneMotion,'off');
h.document.hidden=false;h.events.visibilitychange();assert.equal(h.callbacks.size,1);
h.events.pagehide();assert.equal(h.callbacks.size,0);h.events.pageshow();assert.equal(h.callbacks.size,1);
h=harness({saved:{nadzeel_scene_rotate_v1:'off',nadzeel_scene_motion_v1:'off',nadzeel_sky_v1:'dusk'}});assert.equal(h.root.dataset.sceneRotate,'false');assert.equal(h.root.dataset.sceneMotion,'off');assert.equal(h.root.dataset.sky,'dusk');
h=harness({reduce:true,saved:{nadzeel_sky_v1:'bogus',nadzeel_landscape_v1:'bogus'}});assert.equal(h.root.dataset.sceneMotion,'off');assert.equal(h.nodes['desk-sky-preview'].disabled,true);assert.equal(h.nodes['desk-sky'].value,'auto');assert.equal(h.root.dataset.landscape,'banded');
h.preference.matches=false;h.preference.change();assert.equal(h.root.dataset.sceneMotion,'on');
h.events['moss:state']({detail:{busy:true}});assert.equal(h.root.dataset.sceneBusy,'true');h.events['moss:state']({detail:{busy:true,error:true}});assert.equal(h.root.dataset.sceneBusy,'false');
h.events['moss:journal']({detail:{executions:[],last_sync:null}});assert.equal(h.nodes['trade-sky-count'].textContent,'0 real fills today');assert.equal(h.nodes['trade-sky-empty'].hidden,false);assert.equal(h.nodes['trade-sky-stars'].children.length,0);
const currentFill={...fill,ts:new Date(Date.now()-1000).toISOString(),ticker:'<img onerror=bad>'};
h.events['moss:journal']({detail:{executions:[currentFill],last_sync:new Date().toISOString()}});
const star=h.nodes['trade-sky-stars'].children[0];assert.ok(star);star.listeners.click();assert.match(h.nodes['trade-sky-detail'].textContent,/<img onerror=bad>/);assert.match(h.nodes['trade-sky-detail'].textContent,/fee not reported/);assert.equal(star.attrs['aria-pressed'],'true');
star.focus();h.events['moss:journal']({detail:{executions:[{...currentFill,commission:0,commission_currency:'USD'}]}});assert.match(h.nodes['trade-sky-detail'].textContent,/fee 0 USD/);assert.equal(h.document.activeElement,h.nodes['trade-sky-stars'].children[0]);
h.events['moss:journal']({detail:{executions:[]}});assert.equal(h.document.activeElement.id,'module-title');assert.equal(h.nodes['trade-sky-stars'].children.length,0);
console.log('Dynamic graphics: Eastern time/DST, blended palettes, real-fill filtering, correction/deduplication, empty states, safe text, selection, reduced motion, preview expiry, hidden-page/bfcache and error-state checks passed.');
