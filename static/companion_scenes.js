/* Shared presentation for the real companions and the explicitly simulated preview. */
(() => {
 'use strict';
 const PERIOD={gentle:120000,balanced:60000,lively:30000};
 const anchor=(kind,key)=>'dd-'+kind+'-'+encodeURIComponent(String(key||''));
 const source=(kind,key)=>'/desk/overview#'+encodeURIComponent(anchor(kind,key));
 const caution=d=>(d?.woman?.reasoning||[]).find(n=>n.level==='block'||n.level==='caution');
 const chore=d=>(d?.woman?.chores||[]).find(c=>c.state==='attention')||(d?.woman?.chores||[]).find(c=>c.state==='working');
 function sceneFor(day,now=Date.now()){
  if(!day)return null;
  const note=caution(day);
  if(note)return {key:'caution:'+note.key,kind:'caution'};
  if(day.fox?.state==='researching')return {key:'research:'+day.fox.ticker,kind:'research'};
  const last=day.fox?.recent?.[0],stamp=Date.parse(last?.at);
  if(day.fox?.state==='watching'&&last?.kind==='skip'&&Number.isFinite(stamp)&&stamp<=now&&now-stamp<120000)
   return {key:'pass:'+last.id,kind:'pass'};
  return null;
 }
 function scenePose(scene,actor,elapsed,frequency,still){
  if(!scene||still)return null;
  const t=scene.kind==='pass'?elapsed:elapsed%(PERIOD[frequency]||PERIOD.balanced);
  if(t>=16000)return null;
  const beat=Math.floor(t/4000),frame=Math.floor(t/1200)%4;
  if(scene.kind==='research')return actor?
   [{sheet:'gesture',row:0,frame:0,label:'Listening to Fox',expression:'attentive'},
    {sheet:'woman',row:2,frame,label:'Checking the evidence',expression:'thoughtful'},
    {sheet:'woman',row:2,frame:3,label:'Thinking it through',expression:'thoughtful'},
    {sheet:'woman',row:2,frame,label:'Reading along',expression:'composed'}][beat]:
   {sheet:'fox',row:beat===2?0:2,frame:beat===2?1:frame,label:beat===2?'Listening to Changing Woman':null,expression:beat===2?'listening':'curious'};
  if(scene.kind==='caution')return actor?
   [{sheet:'woman',row:2,frame:3,expression:'thoughtful'},
    {sheet:'gesture',row:0,frame:1,expression:'raised-brow'},
    {sheet:'gesture',row:0,frame:2,expression:'explaining'},
    {sheet:'gesture',row:0,frame:3,expression:'composed'}][beat]:
   {sheet:'fox',row:0,frame:[0,1,3,0][beat],label:'Listening to Changing Woman',expression:'listening'};
  if(scene.kind==='pass')return actor?{sheet:'gesture',row:0,frame:[0,3,0,3][beat],expression:'composed'}:
   {sheet:'fox',row:beat<2?2:0,frame:beat<2?[1,0][beat]:0,label:beat<2?'Reviewing the recorded pass':'Putting the notebook away',expression:'settling'};
  return null;
 }
 function propFor(actor,action,day){
  if(!actor)return ({researching:'chart',reconciling:'clock',waiting:'clock','order-update':'receipt',blocked:'lens',explaining:'journal',listening:'lens'})[action.key]||'';
  if(action.key==='reading-news')return 'news';
  if(action.key==='checking')return ({calendar:'calendar',news:'news',journal:'journal',reconcile:'receipt',wsb:'news'})[chore(day)?.key]||'journal';
  if(action.key==='thinking')return /^(event|earnings)/.test(caution(day)?.key||'')?'calendar':'journal';
  return ['reading','explaining'].includes(action.key)?'journal':'';
 }
 function thoughtFor(actor,{day,action,news,trade,unavailable}={}){
  if(unavailable)return {text:'Status unavailable',detail:'The latest desk status could not be read.',href:'/desk/settings#desk-health'};
  if(!day)return {text:'Meet the companions',detail:'See how we work together.',href:'#moss-preview-dialog'};
  if(!actor){
   if(action.key==='order-update'&&trade?.id)return {text:'Read the order update',detail:trade.text,href:source('move',trade.id)};
   const last=day.fox?.recent?.[0];
   if(last?.kind==='skip'&&day.fox.state==='watching')return {text:'Why did I pass?',detail:last.text,href:source('move',last.id)};
   return {text:({researching:'What am I studying?',reconciling:'What is still pending?',blocked:'What needs attention?',waiting:'Why am I waiting?',holding:'Why hold off?',resting:'Why am I resting?'})[action.key]||'See what I am doing',
    detail:day.fox?.headline||'Read the current agent status.',href:'/desk/overview#dd-fox-headline'};
  }
  if(action.key==='reading-news'&&news)return {text:'Read this headline',detail:news.title,href:/^https:\/\//i.test(news.url||'')?news.url:'/desk/paper#companion-news'};
  const note=caution(day),task=chore(day);
  if(note)return {text:/^(event|earnings)/.test(note.key)?'Check the timing with me':'Read my caution',detail:note.text,href:source('note',note.key)};
  if(task)return {text:task.state==='attention'?'This task needs attention':'See the task I am checking',detail:task.label+': '+task.detail,href:source('chore',task.key)};
  return {text:'See my desk notes',detail:day.woman?.headline||'Read the current chores and reasoning.',href:'/desk/overview#dd-woman-headline'};
 }
 function paintPose(el,pose,still,previous){
  const {sheet,row,frame}=pose,position=(frame*100/3)+'% '+(sheet==='gesture'?row*100:row*100/3)+'%',key=sheet+'|'+row+'|'+frame;
  el.dataset.sheet=sheet;el.dataset.frame=String(frame);el.dataset.still=String(still);
  el.dataset.pose=sheet==='breeze'||sheet==='routine'||(sheet==='fox'&&row===3)?'sit':'stand';
  if(still||!previous||previous.sheet!==sheet){
   el.style.setProperty('--frame-a',position);el.style.setProperty('--frame-b',position);el.style.setProperty('--frame-blend','0');return {sheet,key,upper:false};
  }
  if(previous.key!==key){el.style.setProperty(previous.upper?'--frame-a':'--frame-b',position);previous.upper=!previous.upper;previous.key=key;el.style.setProperty('--frame-blend',previous.upper?'1':'0');}
  return previous;
 }
 const api={PERIOD,anchor,source,sceneFor,scenePose,propFor,thoughtFor,paintPose};
 if(typeof module==='object'&&module.exports)module.exports=api;else window.DeskCompanionScenes=api;
})();
