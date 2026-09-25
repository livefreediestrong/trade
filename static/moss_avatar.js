/* Cozy sidebar perches. Local presentation only; no account or order actions. */
(() => {
 'use strict';
 function perchLayout(width,height){
  if(width<150||height<350)return null;
  const size=height<650?68:88;
  return {width,height,size,fox:{x:28,y:height<650?188:210},woman:{x:width-size-28,y:height-16}};
 }
 function activityForSky(sky){return ({dawn:{row:0,key:'water',label:'Preparing water'},day:{row:1,key:'weave',label:'Working plant fibers'},dusk:{row:2,key:'gathered-food',label:'Sorting gathered food'},night:{row:3,key:'rest',label:'Resting'}})[sky]||{row:3,key:'rest',label:'Resting'};}
 function buzzNote(detail,now=Date.now()){
  const d=detail||{},stamp=Date.parse(d.cachedAt),ticker=String(d.ticker||'').toUpperCase();
  if(d.stale!==false||!Number.isFinite(stamp)||stamp>now||now-stamp>7*60000||! /^[A-Z][A-Z0-9.^=-]{0,14}$/.test(ticker))return '';
  const source=typeof d.source==='string'?d.source.trim().slice(0,24):'Social feed';
  if(/mock|demo|simulat/i.test(source))return '';
  return ticker+' · '+(source||'Social feed')+' activity rose. Attention, not a buy signal.';
 }
 if(typeof module==='object'&&module.exports){module.exports={perchLayout,activityForSky,buzzNote};return;}
 const $=id=>document.getElementById(id),stage=$('moss-sidebar-stage'),panel=$('sidebar-companions');
 // The appearance controls live only on the Paper page; elsewhere the saved choices apply.
 const stand=(value,checked)=>({value,checked,addEventListener(){}});
 const control=$('moss-motion')||stand('cozy'),choice=$('moss-avatar')||stand('both'),speech=$('moss-speech-enabled')||stand('',true),bubble=$('moss-speech');
 if(!stage||!bubble)return;
 const actors=[$('moss-fox'),$('moss-woman')],reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const frames=[null,null];
 const stops=[
  ['desk-overview','Overview','Start with the account and data status. The next step should be clear before we act.','First, the numbers. My eyebrow is not a risk model.'],
  ['moss-desk','Notebook','Research lives here. An honest notebook is more useful than a confident guess.','A good note outlives a confident guess. How inconvenient for the guess.'],
  ['desk-live','Live trading','Check the account and costs before reviewing an order.','Composure first. The Buy button can wait.'],
  ['desk-paper','Paper research','This book uses simulated money, separate from your real account.','Practice is cheaper than bravado. This book uses simulated money.'],
  ['desk-options','Calls & puts','Buying opens a right. Selling to open creates an obligation.','Rights and obligations make very different dinner guests. Check which one you invited.'],
  ['desk-research','Market research','Check the source and age of a quote before considering it.','A falling price is not a personal invitation. Let us inspect the evidence.'],
  ['research-studio','Research studio','Keep the hypothesis and evidence together. A useful idea still needs testing.','Elegant theories are welcome. They still need to show their work.'],
  ['desk-settings','Settings & journal','Change one assumption at a time so results stay understandable.','One change at a time. Future us has enough mysteries.']
 ];
 let saved={};try{saved=JSON.parse(localStorage.getItem('moss_appearance_v1')||'{}')||{};}catch(_){}
 choice.value=saved.habitatVersion>=2&&['fox','woman','both'].includes(saved.avatar)?saved.avatar:'both';
 // Old roaming preferences migrate to the user's requested stationary perches.
 control.value=['dock','hide'].includes(saved.motion)?saved.motion:'cozy';speech.checked=saved.speech!==false;
 let timer=null,active=true,hovered=false,quiet=document.documentElement.dataset.deskQuiet==='true',lastScroll=-Infinity;
 let stop=stops[0],custom='',customUntil=0,messageKey='',dismissed='';
 let buzz='',buzzUntil=0,lastBuzzAt=-Infinity;
 let news=null,newsUntil=0,lastNewsAt=-Infinity;
 // Fox is the broker agent; Changing Woman keeps the chores and reasons with him (desk:day).
 let day=null,foxEvent=null,foxEventUntil=0,note=null,noteUntil=0,lastNoteAt=-Infinity;
 const remembered=key=>{try{return new Set(JSON.parse(sessionStorage.getItem(key)||'[]'));}catch(_){return new Set();}};
 const remember=(key,set)=>{try{sessionStorage.setItem(key,JSON.stringify([...set].slice(-60)));}catch(_){}};
 const seenTrades=remembered('desk_day_trades_v1'),seenNotes=remembered('desk_day_notes_v1');
 const showingBuzz=()=>!!buzz&&performance.now()<buzzUntil&&!custom&&!actors[0].hidden;
 const showingFoxEvent=()=>!!foxEvent&&performance.now()<foxEventUntil&&!custom&&!actors[0].hidden;
 const showingNote=()=>!!note&&performance.now()<noteUntil&&!custom&&!quiet&&!actors[1].hidden;
 const dayMode=()=>!custom&&!!day&&!!day.fox&&day.fox.state!=='off'&&performance.now()-lastScroll>30000;
 function womanLine(){const top=((day&&day.woman&&day.woman.reasoning)||[]).find(n=>n.level==='block'||n.level==='caution');return top?top.text:((day&&day.woman&&day.woman.headline)||'');}
 const showingNews=()=>!!news&&performance.now()<newsUntil&&!custom&&!quiet&&!actors[1].hidden&&Date.now()-news.publishedTs<=36*3600000&&Date.now()-news.checkedAt<=900000;
 const dialog=()=>document.querySelector('dialog[open],.modal:not(.hidden):not([hidden])');
 const interacting=()=>hovered||document.activeElement?.matches('input,select,textarea,[contenteditable="true"]')||bubble.contains(document.activeElement);
 function persist(){try{localStorage.setItem('moss_appearance_v1',JSON.stringify({motion:control.value,avatar:choice.value,speech:speech.checked,habitatVersion:3}));}catch(_){}}
 function selectStop(now){
  if(now<customUntil)return;custom='';
  const visible=stops.map(s=>({s,r:$(s[0])?.getBoundingClientRect()})).filter(x=>x.r&&x.r.bottom>80&&x.r.top<innerHeight*.65);
  const found=visible.find(x=>x.r.top<=innerHeight*.35&&x.r.bottom>innerHeight*.35)||visible[0];if(found)stop=found.s;
 }
 function message(){
  const isTrade=showingFoxEvent(),isBuzz=!isTrade&&showingBuzz(),isNote=!isTrade&&!isBuzz&&showingNote(),isNews=!isTrade&&!isBuzz&&!isNote&&showingNews();
  const isDay=!isTrade&&!isBuzz&&!isNote&&!isNews&&dayMode();
  const alternate=actors[1].hidden?0:actors[0].hidden?1:Math.floor(performance.now()/20000)%2;
  const speaker=isTrade||isBuzz?0:isNote||isNews?1:isDay?alternate:!actors[1].hidden&&(actors[0].hidden||stops.indexOf(stop)%2===0)?1:0;
  const dayText=isDay?(speaker?womanLine():day.fox.headline):'';
  const text=isTrade?foxEvent.text:isBuzz?buzz:isNote?note.text:isNews?news.text:(isDay&&dayText)?dayText:custom||stop[speaker?3:2];
  const toDay=isTrade||isNote||(isDay&&!!dayText),target=isBuzz?'buzz-panel':isNews?news.url:toDay?'desk-day':stop[0],key=speaker+'|'+target+'|'+text;
  if(key!==messageKey){
   messageKey=key;$('moss-speech-text').textContent=text;
   const a=$('moss-speech-link');a.href=isNews?news.url:toDay&&!$('desk-day')?'/desk/overview#desk-day':'#'+target;a.target=isNews?'_blank':'_self';a.rel=isNews?'noopener noreferrer':'';
   a.textContent=isBuzz?'Inspect buzz':isNews?news.source+' · '+new Date(news.publishedAt).toLocaleString()+' ↗':toDay?'Today at the desk':'Go to this section';
   $('moss-destination').textContent=isTrade?'Fox · broker agent':isBuzz?'Fox · Market buzz':isNote?'Changing Woman · thinking it through':isNews?'Changing Woman · News commentary':
    toDay?(speaker?'Changing Woman · chores & reasoning':'Fox · broker agent'):(speaker?'Changing Woman':'Fox')+' · '+stop[1];
   if($('moss-news-headline')){$('moss-news-headline').hidden=!isNews;$('moss-news-headline').textContent=isNews?news.title:'';}
  }
  bubble.dataset.topic=isTrade?'trade':isBuzz?'buzz':isNote?'reasoning':isNews?'news':isDay?'day':'guide';
  bubble.dataset.speaker=speaker?'woman':'fox';bubble.hidden=!speech.checked||control.value==='hide'||dismissed===key||(quiet&&!custom)||!!dialog();
 }
 function landscape(g){
  $('moss-trails').setAttribute('viewBox','0 0 '+g.width+' '+g.height);
  const seat=(p,offset)=>({x:p.x+g.size*.48,y:p.y-g.size*offset});
  const f=seat(g.fox,.34),w=seat(g.woman,.36);
  // Two small rounded sandstone shelves, with open space for the menu between them.
  const shelf=(p,left)=>'M '+(left?0:g.width)+' '+(p.y+4)+' Q '+p.x+' '+(p.y-5)+' '+(p.x+(left?46:-46))+' '+(p.y+3)+' L '+(p.x+(left?22:-22))+' '+(p.y+15)+' Q '+p.x+' '+(p.y+10)+' '+(left?0:g.width)+' '+(p.y+24)+' Z';
  $('moss-canyon-near').setAttribute('d',(actors[0].hidden?'':shelf(f,true))+' '+(actors[1].hidden?'':shelf(w,false)));
  $('moss-canyon-far').setAttribute('d','M 0 '+(g.height-25)+' Q '+g.width*.24+' '+(g.height-64)+' '+g.width*.51+' '+(g.height-26)+' T '+g.width+' '+(g.height-30)+' V '+g.height+' H 0 Z');
  $('moss-sand').setAttribute('d','M 0 '+(g.height-9)+' Q '+g.width*.4+' '+(g.height-30)+' '+g.width+' '+(g.height-5)+' V '+g.height+' H 0 Z');
  const x=60,y=g.height-18;
  $('moss-cacti').setAttribute('d','M '+x+' '+y+' v -31 q 4 -6 8 0 v 11 h 6 v -10 q 4 -5 7 0 v 14 q 0 4 -5 4 h -8 V '+y+' Z M '+x+' '+(y-12)+' h -8 q -5 0 -5 -5 v -10 q 4 -5 7 0 v 7 h 6 Z');
 }
 function tick(){
  clearTimeout(timer);timer=null;
  bubble.hidden=!speech.checked||control.value==='hide'||dismissed===messageKey||(quiet&&!custom)||!!dialog()||!active||document.hidden;
  if(!active||document.hidden||control.value==='hide')return;
  const r=stage.getBoundingClientRect(),g=perchLayout(r.width,r.height);if(!g){bubble.hidden=true;return;}
  const now=performance.now();if(now-lastScroll>250)selectStop(now);message();landscape(g);
  const still=quiet||reduced.matches||control.value==='dock'||!!dialog()||interacting()||now-lastScroll<250;
  for(let i=0;i<actors.length;i++){
   const el=actors[i],p=i?g.woman:g.fox;
   el.style.width=g.size+'px';el.style.height=g.size+'px';el.style.transform='translate3d('+p.x+'px,'+(p.y-g.size)+'px,0)';
   el.dataset.pose='sit';el.dataset.destination=i?'bottom-perch':'top-perch';
   el.dataset.buzz=String(i===0&&!still&&showingBuzz()&&dismissed!==messageKey);
   // A slow 12.8-second breeze every two minutes. CSS blends registered frames.
   const wind=(now-i*300)%120000-18000,step=Math.floor(wind/1600);
   const activity=activityForSky(document.documentElement.dataset.sky),workStep=Math.floor(now/3200)%20;
   const frame=still?0:i===1?(workStep<6?[0,1,2,3,2,1][workStep]:0):step>=0&&step<8?[0,1,2,3,2,3,1,0][step]:0;
   el.dataset.breeze=String(!still&&step>=0&&step<8);
   const row=i===1?activity.row:0,position=(frame*100/3)+'% '+(i===1?row*100/3:0)+'%',key=row+'|'+frame;
   if(i===1){el.dataset.activity=activity.key;el.setAttribute('aria-label','Changing Woman · '+activity.label+' · '+(document.documentElement.dataset.sky||'night'));}
   el.dataset.still=String(still);el.dataset.frame=String(frame);
   if(still||!frames[i]){
    el.style.setProperty('--frame-a',position);el.style.setProperty('--frame-b',position);el.style.setProperty('--frame-blend','0');frames[i]={frame,key,upper:false};
   }else if(frames[i].key!==key){
    // Replace the covered layer, then reveal it over 1.35s; the torso stays aligned.
    el.style.setProperty(frames[i].upper?'--frame-a':'--frame-b',position);frames[i].upper=!frames[i].upper;frames[i].frame=frame;frames[i].key=key;
    el.style.setProperty('--frame-blend',frames[i].upper?'1':'0');
   }
  }
  timer=setTimeout(tick,still?1000:400);
 }
 function apply(){
  actors[0].hidden=control.value==='hide'||choice.value==='woman';actors[1].hidden=control.value==='hide'||choice.value==='fox';panel.hidden=control.value==='hide';
  const motionNote=$('moss-motion-note');
  if(motionNote)motionNote.textContent=quiet?'Quiet desk: companions rest; trading continues.':reduced.matches?'Reduced motion: still poses.':'Fox is your broker agent and reports his trades. Changing Woman keeps the chores and reasons with him; she follows the sky: dawn water, daytime fiber work, dusk food sorting, night rest. Typing and dialogs pause motion.';
  tick();
 }
 function save(){persist();dismissed='';apply();}
 control.addEventListener('change',save);choice.addEventListener('change',save);speech.addEventListener('change',save);
 $('moss-speech-dismiss').addEventListener('click',()=>{dismissed=messageKey;bubble.hidden=true;});
 bubble.addEventListener('pointerenter',()=>{hovered=true;tick();});bubble.addEventListener('pointerleave',()=>{hovered=false;tick();});
 window.addEventListener('moss:guide',e=>{const d=e.detail||{},s=stops.find(s=>s[0]===d.target);if(!s||typeof d.text!=='string')return;stop=s;custom=d.text.slice(0,230);customUntil=performance.now()+18000;dismissed='';tick();});
 window.addEventListener('moss:buzz',e=>{
  const note=buzzNote(e.detail),now=performance.now(),r=stage.getBoundingClientRect();
  if(!note||quiet||document.hidden||!active||actors[0].hidden||!speech.checked||dialog()||interacting()||!perchLayout(r.width,r.height)||now<customUntil)return;
  e.preventDefault(); // Handled here; do not duplicate with a toast or a beep.
  if(now-lastBuzzAt<180000)return;
  buzz=note;buzzUntil=now+18000;lastBuzzAt=now;dismissed='';tick();
 });
 window.addEventListener('moss:news',e=>{
  const d=e.detail||{},now=performance.now(),r=stage.getBoundingClientRect();
  const note=window.NadzeelNews?.newsQuip({...d,published_ts:d.publishedTs/1000,checked_at:d.checkedAt/1000,fresh:true});
  if(!note||quiet||document.hidden||!active||actors[1].hidden||!speech.checked||dialog()||interacting()||!perchLayout(r.width,r.height)||now<customUntil||showingBuzz()||now-lastNewsAt<300000)return;
  news=note;newsUntil=now+32000;lastNewsAt=now;dismissed='';e.preventDefault();tick();
 });
 function canSpeak(i){const r=stage.getBoundingClientRect();return !quiet&&!document.hidden&&active&&!actors[i].hidden&&speech.checked&&!dialog()&&!interacting()&&!!perchLayout(r.width,r.height)&&performance.now()>=customUntil;}
 window.addEventListener('desk:day',e=>{
  const d=e.detail||{},now=performance.now();day=d;
  const trade=d.fox&&d.fox.latest_trade;
  if(trade&&trade.id&&!seenTrades.has(trade.id)&&canSpeak(0)){seenTrades.add(trade.id);remember('desk_day_trades_v1',seenTrades);foxEvent={text:String(trade.text||'').slice(0,230)};foxEventUntil=now+20000;dismissed='';}
  const next=((d.woman&&d.woman.reasoning)||[]).find(n=>(n.level==='block'||n.level==='caution')&&n.key&&!seenNotes.has(n.key));
  if(next&&!showingFoxEvent()&&now-lastNoteAt>=180000&&canSpeak(1)){seenNotes.add(next.key);remember('desk_day_notes_v1',seenNotes);note={text:String(next.text||'').slice(0,230)};noteUntil=now+30000;lastNoteAt=now;dismissed='';}
  if(d.fox&&d.fox.headline)actors[0].setAttribute('aria-label','Fox, your broker agent · '+d.fox.headline);
  tick();
 });
 window.addEventListener('desk:attention',e=>{quiet=!!e.detail?.quiet;apply();});
 window.addEventListener('scroll',()=>{lastScroll=performance.now();customUntil=0;tick();},{passive:true});
 document.addEventListener('focusin',tick);document.addEventListener('focusout',tick);window.addEventListener('resize',tick);reduced.addEventListener('change',apply);
 document.addEventListener('visibilitychange',tick);
 window.addEventListener('pagehide',()=>{active=false;tick();});window.addEventListener('pageshow',()=>{active=true;tick();});
 persist();apply();
})();
