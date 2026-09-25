/* Cozy sidebar perches. Local presentation only; no account or order actions. */
(() => {
 'use strict';
 function perchLayout(width,height,scale='comfortable'){
  if(width<150||height<350)return null;
  const size=height<650?({compact:68,comfortable:84,large:92}[scale]||84):({compact:104,comfortable:128,large:140}[scale]||112);
  return {width,height,size,fox:{x:18,y:height<650?194:224},woman:{x:width-size-18,y:height-12}};
 }
 function activityForSky(sky){return ({dawn:{row:0,key:'water',label:'Preparing water'},day:{row:1,key:'weave',label:'Working plant fibers'},dusk:{row:2,key:'gathered-food',label:'Sorting gathered food'},night:{row:3,key:'rest',label:'Resting'}})[sky]||{row:3,key:'rest',label:'Resting'};}
 // Poses describe observed work, never infer a fill, profit or a new trading decision.
 function companionAction(actor,{day,sky,cue='',speaking=false,unavailable=false}={}){
  const action=(key,label,sheet,row,mark='',sequence=[0,1,2,3,2,1])=>({key,label,sheet,row,mark,sequence,speaking});
  if(unavailable)return action('unavailable','Status unavailable',actor?'woman':'fox',0,'?',[0]);
  if(cue==='nightly'&&!day?.after_close?.busy&&!['blocked','reconciling'].includes(day?.fox?.state))
   return action('explaining','Sharing the nightly review',actor?'gesture':'fox',actor?0:2,'…');
  if(day?.after_close?.busy&&cue!=='trade'&&!['blocked','reconciling'].includes(day?.fox?.state))
   return actor?action('reading','Reviewing the session','woman',2,'…'):action('researching','Challenging the nightly evidence','fox',2,'…');
  if(!actor){
   if(cue==='trade')return action('order-update','Order update','fox',2,'…');
   if(cue==='buzz')return action('listening','Listening','fox',0,'!');
   if(cue==='guide')return action('explaining','Reading along','fox',2,'…');
   const state=day?.fox?.state;
   if(state==='researching')return action('researching','Researching'+(day.fox.ticker?' '+String(day.fox.ticker).slice(0,12):''),'fox',2,'…');
   if(state==='reconciling')return action('reconciling','Awaiting broker','fox',2,'…',[0,1,3,1]);
   if(state==='blocked')return action('blocked','Blocked','fox',2,'?',[1,3,1,0]);
   if(state==='holding')return action('holding','Holding off','fox',0,'Ⅱ',[0,2,1,0]);
   if(['off','resting','done'].includes(state))return action('resting',state==='done'?'Done for today':state==='off'?'Off duty':'Resting','fox',3,'z',[0,1,2,3,3,2]);
   if(state==='watching')return action('watching','On watch','fox',0,'',[0,1,0,2,3,1]);
   return action('waiting',state==='waiting'?'Waiting for open':'At the desk','breeze',0,'',[0,1,2,3,2,1]);
  }
  if(cue==='sources')return day?.research?.busy||day?.research?.fresh_sources>0?
   action('sourcing',day.research.busy?'Refreshing source context':'Evaluating source updates','woman',2,'…'):
   action('sources-unavailable','Waiting for source data','woman',2,'?',[0]);
  if(cue==='news')return action('reading-news','Reading the news','woman',2,'…');
  if(cue==='guide')return action('explaining','Explaining','gesture',0,'',[0,1,2,2,3,0]);
  const reasoning=(day?.woman?.reasoning||[]).find(n=>n.level==='block'||n.level==='caution');
  if(cue==='reasoning'||reasoning)return action('thinking','Thinking it through','woman',2,reasoning?.level==='block'?'!':'…',[3,2,3,1,0,1]);
  const chores=day?.woman?.chores||[],chore=chores.find(c=>c.state==='attention')||chores.find(c=>c.state==='working');
  if(chore)return action('checking',String(chore.label||'Checking the desk').slice(0,48)+(chore.state==='attention'?' · needs attention':''),'woman',2,chore.state==='attention'?'?':'…',[0,1,3,2,1,0]);
  if(day?.fox?.state==='researching')return action('reading','Reading along','woman',2,'…');
  const routine=activityForSky(sky);return action(routine.key,routine.label,'routine',routine.row,routine.key==='rest'?'z':'');
 }
 function actionPose(action,elapsed,still){
  // Read -> hand to chin -> open palm when sharing the reason. Paused poses retain meaning.
  if(action.key==='thinking'&&action.speaking&&!still&&elapsed%14400>=7200)
   return {sheet:'gesture',row:0,frame:[0,1,2,2,3,0][Math.floor((elapsed%14400-7200)/1200)]};
  const sequence=action.sequence,frame=still?sequence[0]:sequence[Math.floor(elapsed/1200)%sequence.length];
  return {sheet:action.sheet,row:action.row,frame};
 }
 function buzzNote(detail,now=Date.now()){
  const d=detail||{},stamp=Date.parse(d.cachedAt),ticker=String(d.ticker||'').toUpperCase();
  if(d.stale!==false||!Number.isFinite(stamp)||stamp>now||now-stamp>7*60000||! /^[A-Z][A-Z0-9.^=-]{0,14}$/.test(ticker))return '';
  const source=typeof d.source==='string'?d.source.trim().slice(0,24):'Social feed';
  if(/mock|demo|simulat/i.test(source))return '';
  return ticker+' · '+(source||'Social feed')+' activity rose. Attention, not a buy signal.';
 }
 if(typeof module==='object'&&module.exports){module.exports={perchLayout,activityForSky,buzzNote,companionAction,actionPose};return;}
 const $=id=>document.getElementById(id),stage=$('moss-sidebar-stage'),panel=$('sidebar-companions');
 // Both companions work autonomously. Retired character/motion choices cannot hide them.
 const stand=(value,checked)=>({value,checked,addEventListener(){}});
 const control=$('moss-motion')||stand('cozy'),choice=$('moss-avatar')||stand('both'),speech=$('moss-speech-enabled')||stand('',true),bubble=$('moss-speech');
 if(!stage||!bubble)return;
 const actors=[$('moss-fox'),$('moss-woman')],reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const scenes=window.DeskCompanionScenes;
 const frames=[null,null],actions=[null,null];
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
 choice.value=$('moss-avatar')&&saved.habitatVersion>=2&&['fox','woman','both'].includes(saved.avatar)?saved.avatar:'both';
 // Old roaming preferences migrate to the user's requested stationary perches.
 control.value=$('moss-motion')&&['dock','hide'].includes(saved.motion)?saved.motion:'cozy';speech.checked=saved.speech!==false;
 let characterSize=['compact','comfortable','large'].includes(saved.size)?saved.size:'comfortable';
 let frequency=['gentle','balanced','lively'].includes(saved.frequency)?saved.frequency:'balanced',scene=null,sceneSince=0;
 let timer=null,active=true,hovered=false,quiet=document.documentElement.dataset.deskQuiet==='true',lastScroll=-Infinity;
 let stop=stops[0],custom='',customUntil=0,messageKey='',dismissed='',messageSince=-Infinity,messageState='';
 let buzz='',buzzUntil=0,lastBuzzAt=-Infinity;
 let news=null,newsUntil=0,lastNewsAt=-Infinity;
 // Fox is the broker agent; Changing Woman keeps the chores and reasons with him (desk:day).
 let day=null,dayAt=-Infinity,dayUnavailable=false,foxEvent=null,foxEventUntil=0,note=null,noteUntil=0,lastNoteAt=-Infinity;
 const remembered=key=>{try{return new Set(JSON.parse(sessionStorage.getItem(key)||'[]'));}catch(_){return new Set();}};
 const remember=(key,set)=>{try{sessionStorage.setItem(key,JSON.stringify([...set].slice(-60)));}catch(_){}};
 const seenTrades=remembered('desk_day_trades_v1'),seenNotes=remembered('desk_day_notes_v1');
 const showingBuzz=()=>!!buzz&&performance.now()<buzzUntil&&!custom&&!actors[0].hidden;
 const showingFoxEvent=()=>!!foxEvent&&performance.now()<foxEventUntil&&!custom&&!actors[0].hidden;
 const showingNote=()=>!!note&&performance.now()<noteUntil&&!custom&&!quiet&&!actors[1].hidden;
 const dayMode=()=>!custom&&!!day&&!!day.fox&&(day.fox.state!=='off'||!!day.research)&&performance.now()-lastScroll>30000;
 function womanLine(){const top=((day&&day.woman&&day.woman.reasoning)||[]).find(n=>n.level==='block'||n.level==='caution');return top?top.text:(day?.research?.summary||day?.woman?.headline||'');}
 const showingNews=()=>!!news&&performance.now()<newsUntil&&!custom&&!quiet&&!actors[1].hidden&&Date.now()-news.publishedTs<=36*3600000&&Date.now()-news.checkedAt<=900000;
 const dialog=()=>document.querySelector('dialog[open],.modal:not(.hidden):not([hidden])');
 const interacting=()=>hovered||document.activeElement?.matches('input,select,textarea,[contenteditable="true"]')||bubble.contains(document.activeElement);
 function persist(){try{localStorage.setItem('moss_appearance_v1',JSON.stringify({motion:control.value,avatar:choice.value,speech:speech.checked,size:characterSize,frequency,habitatVersion:3}));}catch(_){}}
 function selectStop(now){
  if(now<customUntil)return;custom='';
  const visible=stops.map(s=>({s,r:$(s[0])?.getBoundingClientRect()})).filter(x=>x.r&&x.r.bottom>80&&x.r.top<innerHeight*.65);
  const found=visible.find(x=>x.r.top<=innerHeight*.35&&x.r.bottom>innerHeight*.35)||visible[0];if(found)stop=found.s;
 }
 function message(){
  const nightly=day?.after_close,finished=Date.parse(nightly?.completed_at),recentNightly=Number.isFinite(finished)&&Date.now()>=finished&&Date.now()-finished<900000;
  const isTrade=showingFoxEvent(),isNightly=!isTrade&&!custom&&!dayUnavailable&&!['blocked','reconciling'].includes(day?.fox?.state)&&(nightly?.busy||recentNightly);
  const isBuzz=!isTrade&&!isNightly&&showingBuzz(),isNote=!isTrade&&!isNightly&&!isBuzz&&showingNote(),isNews=!isTrade&&!isNightly&&!isBuzz&&!isNote&&showingNews();
  const isDay=!isTrade&&!isBuzz&&!isNote&&!isNews&&dayMode();
  const alternate=actors[1].hidden?0:actors[0].hidden?1:Math.floor(performance.now()/45000)%2;
  const speaker=isTrade||isBuzz?0:isNote||isNews?1:isNightly||isDay?alternate:!actors[1].hidden&&(actors[0].hidden||stops.indexOf(stop)%2===0)?1:0;
  const dayText=isDay?(speaker?womanLine():day.fox.headline):'';
  const nightlyText=nightly?.busy?(speaker?'I am gathering dated evidence and comparing the session with our earlier observations.':'I am reviewing the evidence with Changing Woman. Any new idea still needs a prospective test.'):String(nightly?.[speaker?'changing_woman':'fox']||'The local session evidence is saved. Inspect the report for AI availability and remaining gaps.').slice(0,340);
  const text=dayUnavailable?'Current desk information is unavailable. I will wait for a fresh update before describing activity.':isTrade?foxEvent.text:isNightly?nightlyText:isBuzz?buzz:isNote?note.text:isNews?news.text:(isDay&&dayText)?dayText:custom||stop[speaker?3:2];
  const isSources=!isNightly&&isDay&&speaker===1&&!!day?.research&&!((day.woman?.reasoning||[]).some(n=>n.level==='block'||n.level==='caution'));
  const toDay=isTrade||isNote||(isDay&&!!dayText),target=isNightly?'nightly-review':isBuzz?'buzz-panel':isNews?news.url:isSources?'companion-news':toDay?'desk-day':stop[0],key=speaker+'|'+target+'|'+text;
  const now=performance.now(),state=dayUnavailable?'unavailable':day?.fox?.state||'',critical=dayUnavailable||['blocked','reconciling'].includes(state);
  const readingMs=Math.min(32000,Math.max(18000,String($('moss-speech-text').textContent||'').length*65));
  // Keep a complete thought readable. New order/risk/status changes remain immediate.
  if(key!==messageKey&&messageKey&&!custom&&!isTrade&&!isBuzz&&!isNote&&!isNews&&!critical&&state===messageState&&
     (bubble.dataset.topic!=='news'||(news&&Date.now()-news.checkedAt<=900000&&Date.now()-news.publishedTs<=36*3600000))&&
     (interacting()||now-messageSince<readingMs)&&!bubble.hidden)return;
  if(key!==messageKey){
   messageSince=now;messageState=state;messageKey=key;$('moss-speech-text').textContent=text;
   const brief=scenes?.briefFor(speaker,day)||{};
   if($('moss-speech-principle'))$('moss-speech-principle').textContent=isNews?'Read the facts. Test the implication.':isTrade?'Record first. Reconcile next.':brief.principle||'Evidence before conviction.';
   if($('moss-speech-next')){$('moss-speech-next').textContent=isNews?(news.next||'Check the original source and what would disprove the thesis.'):isTrade?'This is the recorded broker update; the ledger determines what actually filled.':isBuzz?'Attention is a research lead. It does not establish an edge.':brief.next||'';}
   if($('moss-speech-meta'))$('moss-speech-meta').textContent=isNightly?('Nightly review · '+nightly.day+' · '+(nightly.busy?'in progress':'AI interpretation / saved evidence')):isNews?'App-written research lens · source below':day?.as_of?'Desk update · '+new Date(day.as_of).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZone:'America/New_York'})+' ET':'App-written desk guidance';
   const page={'desk-overview':'overview','desk-day':'overview','moss-desk':'paper','desk-paper':'paper','desk-options':'paper','desk-research':'research','research-studio':'research','buzz-panel':'research','companion-news':'research','desk-settings':'settings'}[target]||'auto';
   const a=$('moss-speech-link');a.href=isNews?news.url:($(target)?'':'/desk/'+page)+'#'+target;a.target=isNews?'_blank':'_self';a.rel=isNews?'noopener noreferrer':'';
   a.textContent=isNightly?'Read the nightly review':isBuzz?'Inspect buzz':isNews?news.source+' · '+new Date(news.publishedAt).toLocaleString()+' ↗':isSources?'Inspect source coverage':toDay?'Today at the desk':'Go to this section';
   $('moss-destination').textContent=isTrade?'Fox · broker agent':isBuzz?'Fox · Market buzz':isNote?'Changing Woman · thinking it through':isNews?'Changing Woman · News commentary':
    toDay?(speaker?'Changing Woman · evidence & context':'Fox · broker agent'):(speaker?'Changing Woman':'Fox')+' · '+stop[1];
   if($('moss-news-headline')){$('moss-news-headline').hidden=!isNews;$('moss-news-headline').textContent=isNews?news.title:'';}
  }
  bubble.dataset.topic=dayUnavailable?'unavailable':isTrade?'trade':isNightly?'nightly':isBuzz?'buzz':isNote?'reasoning':isNews?'news':isSources?'sources':isDay?'day':'guide';
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
  if(!active||document.hidden||control.value==='hide'){actors.forEach(el=>{el.dataset.still='true';});return;}
  const r=stage.getBoundingClientRect(),g=perchLayout(r.width,r.height,characterSize);if(!g){bubble.hidden=true;actors.forEach(el=>{el.dataset.still='true';});return;}
  const now=performance.now();
  if(day&&now-dayAt>90000){day=null;dayUnavailable=true;foxEvent=null;note=null;scene=null;}
  if(now-lastScroll>250)selectStop(now);message();
  const still=quiet||reduced.matches||control.value==='dock'||!!dialog()||interacting()||now-lastScroll<250;
  for(let i=0;i<actors.length;i++){
   const el=actors[i],p=i?g.woman:g.fox;
   el.style.width=g.size+'px';el.style.height=g.size+'px';el.style.transform='translate3d('+p.x+'px,'+(p.y-g.size)+'px,0)';
   el.style.setProperty('--caption-width',Math.max(90,g.width-g.size-44)+'px');
   el.dataset.destination=i?'bottom-perch':'top-perch';
   el.dataset.buzz=String(i===0&&!still&&showingBuzz()&&dismissed!==messageKey);
   // A slow 12.8-second breeze every two minutes. CSS blends registered frames.
   const period=scenes?.PERIOD[frequency]||60000,wind=(now-i*300)%(period*2)-18000,step=Math.floor(wind/1600);
   const sky=document.documentElement.dataset.sky,activity=activityForSky(sky),workStep=Math.floor(now/3200)%20;
   const speaking=!bubble.hidden&&bubble.dataset.speaker===(i?'woman':'fox');
   const cue=speaking&&(bubble.dataset.topic!=='guide'||custom)?bubble.dataset.topic:'';
   const action=companionAction(i,{day,sky,cue,speaking,unavailable:dayUnavailable});
   const actionKey=action.key+'|'+action.label;if(actions[i]?.key!==actionKey)actions[i]={key:actionKey,since:now};
   const elapsed=now-actions[i].since,restBeat=elapsed%period>16000&&!['reasoning','news','sources','trade','buzz','guide'].includes(cue);
   let pose=actionPose(action,elapsed,still||el.hidden||restBeat);
   const priority=!bubble.hidden&&['trade','buzz','news','sources'].includes(bubble.dataset.topic)||!!custom;
   const partnered=!actors[0].hidden&&!actors[1].hidden&&!dayUnavailable&&!priority;
   const sceneFrame=partnered?scenes?.scenePose(scene,i,now-sceneSince,frequency,still):null;
   if(sceneFrame)pose=sceneFrame;
   // Unoccupied companions keep their original slow breeze / daily chores.
   if(pose.sheet==='routine')pose.frame=still?0:workStep<6?[0,1,2,3,2,1][workStep]:0;
   if(pose.sheet==='breeze')pose.frame=still?0:step>=0&&step<8?[0,1,2,3,2,3,1,0][step]:0;
   const {sheet,row,frame}=pose;
   el.dataset.action=action.key;el.dataset.sheet=sheet;
   el.dataset.pose=sheet==='breeze'||sheet==='routine'||(sheet==='fox'&&row===3)?'sit':'stand';
   const caption=$(i?'moss-woman-action':'moss-fox-action'),thought=$(i?'moss-woman-thought':'moss-fox-thought');
   if(caption)caption.textContent=sceneFrame?.label||action.label;
   el.dataset.speaking=String(speaking);el.dataset.expression=sceneFrame?.expression||(i?'composed':action.key==='researching'?'curious':'attentive');
   const prop=(sceneFrame?.expression==='listening'?'':scenes?.propFor(i,action,day))||'',propNode=$(i?'moss-woman-prop':'moss-fox-prop');
   if(propNode&&prop&&el.dataset.prop!==prop)propNode.setAttribute('href','/static/companion-props.svg#'+prop);el.dataset.prop=prop;
   if(thought){
    const info=scenes?.thoughtFor(i,{day,action,news:showingNews()?news:null,trade:foxEvent,unavailable:dayUnavailable});
    thought.textContent=info?.text||action.mark;thought.hidden=quiet||(!info&&!action.mark);
    if(info){thought.href=info.href;thought.title=String(info.detail||'');thought.setAttribute('aria-label',(i?'Changing Woman':'Fox')+': '+info.text+'. '+(info.detail||''));thought.target=/^https:\/\//.test(info.href)?'_blank':'_self';thought.rel=thought.target==='_blank'?'noopener noreferrer':'';}
   }
   el.dataset.breeze=String(!still&&step>=0&&step<8);
   if(i===1)el.dataset.activity=activity.key;
   const detail=i?(day?.woman?.headline||''):(day?.fox?.headline||'');
   el.setAttribute('aria-label',(i?'Changing Woman':'Fox, your broker agent')+' · '+(sceneFrame?.label||action.label)+(detail?' · '+detail:''));
   el.dataset.still=String(still||el.hidden||restBeat&&!sceneFrame);el.dataset.frame=String(frame);
   const portrait=scenes?.portraitFor(i,action,sceneFrame,speaking?now-messageSince:elapsed,still)||{row:i,frame:0,expression:'attentive'};
   const faceKey=portrait.row+'|'+portrait.frame,facePosition=(portrait.frame*100/3)+'% '+(portrait.row*100)+'%';
   // Crossfade both transparent layers; never replace both at a sheet boundary.
   el.dataset.expression=portrait.expression;
   if(!frames[i]){
    el.style.setProperty('--frame-a',facePosition);el.style.setProperty('--frame-b',facePosition);el.style.setProperty('--frame-blend','0');frames[i]={key:faceKey,upper:false};
   }else if(frames[i].key!==faceKey){
    el.style.setProperty(frames[i].upper?'--frame-a':'--frame-b',facePosition);frames[i].upper=!frames[i].upper;frames[i].key=faceKey;
    el.style.setProperty('--frame-blend',frames[i].upper?'1':'0');
   }
  }
  landscape(g);
  timer=setTimeout(tick,still?1000:400);
 }
 function apply(){
  actors[0].hidden=control.value==='hide'||choice.value==='woman';actors[1].hidden=control.value==='hide'||choice.value==='fox';panel.hidden=control.value==='hide';
  const motionNote=$('moss-motion-note');
  if(motionNote)motionNote.textContent=quiet?'Quiet desk: companions hold still; trading continues.':reduced.matches?'Reduced motion: still poses show what each companion is doing.':'Fox and Changing Woman act out shared scenes with charts, calendars and notebooks. Click a thought to inspect its recorded source. Their actions follow current information automatically.';
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
  const d=e.detail||{},now=performance.now(),stamp=d.as_of==null?null:Date.parse(d.as_of);
  if(d.ok===false||!d.fox||!d.woman||(stamp!==null&&(!Number.isFinite(stamp)||Date.now()-stamp>90000||stamp>Date.now()+5000))){day=null;dayUnavailable=true;foxEvent=null;note=null;scene=null;tick();return;}
  day=d;dayAt=now;dayUnavailable=false;
  const nextScene=scenes?.sceneFor(d);if(nextScene?.key!==scene?.key){scene=nextScene;sceneSince=now;}
  const trade=d.fox&&d.fox.latest_trade;
  if(trade&&trade.id&&!seenTrades.has(trade.id)&&canSpeak(0)){seenTrades.add(trade.id);remember('desk_day_trades_v1',seenTrades);foxEvent={id:trade.id,text:String(trade.text||'').slice(0,230)};foxEventUntil=now+20000;dismissed='';}
  const next=((d.woman&&d.woman.reasoning)||[]).find(n=>(n.level==='block'||n.level==='caution')&&n.key&&!seenNotes.has(n.key));
  if(next&&!showingFoxEvent()&&now-lastNoteAt>=180000&&canSpeak(1)){seenNotes.add(next.key);remember('desk_day_notes_v1',seenNotes);note={text:String(next.text||'').slice(0,230)};noteUntil=now+30000;lastNoteAt=now;dismissed='';}
  if(d.fox&&d.fox.headline)actors[0].setAttribute('aria-label','Fox, your broker agent · '+d.fox.headline);
  tick();
 });
 window.addEventListener('desk:day-unavailable',()=>{day=null;dayUnavailable=true;foxEvent=null;note=null;scene=null;tick();});
 window.addEventListener('moss:appearance',e=>{const d=e.detail||{};if(['compact','comfortable','large'].includes(d.size))characterSize=d.size;if(['gentle','balanced','lively'].includes(d.frequency))frequency=d.frequency;persist();apply();});
 window.addEventListener('desk:attention',e=>{quiet=!!e.detail?.quiet;apply();});
 window.addEventListener('scroll',()=>{lastScroll=performance.now();customUntil=0;tick();},{passive:true});
 document.addEventListener('focusin',tick);document.addEventListener('focusout',tick);window.addEventListener('resize',tick);reduced.addEventListener('change',apply);
 document.addEventListener('visibilitychange',tick);
 window.addEventListener('pagehide',()=>{active=false;tick();});window.addEventListener('pageshow',()=>{active=true;tick();});
 persist();apply();
})();
