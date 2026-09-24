const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const timers=new Map();let serial=0;const events={},docEvents={};
function node(){return {hidden:false,style:{},attrs:{},handlers:{},classList:{contains:()=>false,add(){}},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,f){this.handlers[k]=f},append(){},contains(n){return n===this},focus(){document.activeElement=this;this.handlers.focus?.()},getBoundingClientRect(){return {left:20,bottom:30,top:10,width:350,height:180}}};}
const button=node(),tip=node(),heading=node();heading.cloneNode=()=>({textContent:'Account, data & research health',querySelectorAll:()=>[]});
const section={querySelector:()=>heading},candidate={textContent:'Risk and capability guidance',classList:{contains:()=>false,add(){}},querySelector:()=>null,closest(s){return s==='[role="dialog"],dialog,#execution-summary'?null:s==='.desk-module,details,.panel,.desk-section'?section:null}};
let created=0;const document={activeElement:null,documentElement:{clientWidth:594},querySelectorAll:()=>[candidate],createElement:()=>created++===0?button:tip,body:{append(){}},addEventListener:(name,fn)=>docEvents[name]=fn};
vm.runInNewContext(fs.readFileSync('static/context_help.js','utf8'),{document,window:{addEventListener:(name,fn)=>events[name]=fn},innerHeight:240,setTimeout(fn){timers.set(++serial,fn);return serial},clearTimeout:id=>timers.delete(id)});
button.focus();assert.equal(tip.hidden,false);events.scroll({target:tip});assert.equal(tip.hidden,false,'internal scrolling keeps guidance readable');
button.handlers.click({preventDefault(){},stopPropagation(){}});assert.equal(document.activeElement,tip);assert.equal(tip.tabIndex,0);assert.equal(tip.attrs.role,'region');
button.handlers.blur({relatedTarget:tip});assert.equal(timers.size,0,'focus inside disclosure does not dismiss it');
docEvents.keydown({key:'Escape'});assert.equal(document.activeElement,button);assert.equal(tip.hidden,true);
button.focus();events.scroll({target:document});assert.equal(tip.hidden,true,'page scrolling still dismisses displaced help');
button.focus();button.handlers.click({preventDefault(){},stopPropagation(){}});tip.handlers.pointerleave();for(const fn of timers.values())fn();assert.equal(tip.hidden,false,'keyboard focus retains readable guidance even when pointer leaves');
document.activeElement=null;tip.handlers.blur({relatedTarget:document});for(const fn of timers.values())fn();assert.equal(tip.hidden,true);
console.log('Context help: short-viewport internal scrolling, focusable disclosure, focus transfer, Escape restoration, and page dismissal passed.');
