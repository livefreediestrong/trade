/* Autonomous presentation of current desk information. No trading actions. */
(() => {
 'use strict';
 const PERIOD={gentle:120000,balanced:60000,lively:30000};
 const anchor=(kind,key)=>'dd-'+kind+'-'+encodeURIComponent(String(key||''));
 const source=(kind,key)=>'/desk/overview#'+encodeURIComponent(anchor(kind,key));
 const caution=d=>(d?.woman?.reasoning||[]).find(n=>n.level==='block'||n.level==='caution');
 const chore=d=>(d?.woman?.chores||[]).find(c=>c.state==='attention')||(d?.woman?.chores||[]).find(c=>c.state==='working');
 function sceneFor(day,now=Date.now()){
  if(!day)return null;
  if(day.after_close?.busy&&!['blocked','reconciling'].includes(day.fox?.state))return {key:'nightly:'+day.after_close.day,kind:'research'};
  const note=caution(day);
  if(note)return {key:'caution:'+note.key+'|'+day.fox?.state,kind:'caution',partnered:['watching','researching','holding'].includes(day.fox?.state)};
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
   scene.partnered===false?null:{sheet:'fox',row:0,frame:[0,1,3,0][beat],label:'Listening to Changing Woman',expression:'listening'};
  if(scene.kind==='pass')return actor?{sheet:'gesture',row:0,frame:[0,3,0,3][beat],expression:'composed'}:
   {sheet:'fox',row:beat<2?2:0,frame:beat<2?[1,0][beat]:0,label:beat<2?'Reviewing the recorded pass':'Putting the notebook away',expression:'settling'};
  return null;
 }
 function propFor(actor,action,day){
  if(!actor)return ({researching:'chart',reconciling:'clock',waiting:'clock','order-update':'receipt',blocked:'lens',explaining:'journal',listening:'lens'})[action.key]||'';
  if(['reading-news','sourcing','sources-unavailable'].includes(action.key))return 'news';
  if(action.key==='checking')return ({calendar:'calendar',news:'news',journal:'journal',reconcile:'receipt',wsb:'news'})[chore(day)?.key]||'journal';
  if(action.key==='thinking')return /^(event|earnings)/.test(caution(day)?.key||'')?'calendar':'journal';
  return ['reading','explaining'].includes(action.key)?'journal':'';
 }
 function thoughtFor(actor,{day,action,news,trade,unavailable}={}){
  if(unavailable)return {text:'Status unavailable',detail:'The latest desk status could not be read.',href:'/desk/auto#desk-health'};
  if(!day)return {text:'Reading desk status',detail:'Waiting for current desk information.',href:'/desk/overview#desk-day'};
  if((day.after_close?.busy||action?.label==='Sharing the nightly review')&&action?.key!=='order-update'&&!['blocked','reconciling'].includes(day.fox?.state))return {text:'Review the session with us',detail:day.after_close?.busy?'Comparing dated observations, prior sessions and public source context.':'Read the saved evidence, AI interpretations and unproven research tests.',href:'/desk/fox#nightly-review'};
  if(!actor){
   if(action.key==='order-update'&&trade?.id)return {text:'Read the order update',detail:trade.text,href:source('move',trade.id)};
   const last=day.fox?.recent?.[0];
   if(last?.kind==='skip'&&day.fox.state==='watching')return {text:'Why did I pass?',detail:last.text,href:source('move',last.id)};
   return {text:({researching:'What am I studying?',reconciling:'What is still pending?',blocked:'What needs attention?',waiting:'Why am I waiting?',holding:'Why hold off?',resting:'Why am I resting?'})[action.key]||'See what I am doing',
    detail:day.fox?.headline||'Read the current agent status.',href:'/desk/overview#dd-fox-headline'};
  }
  if(action.key==='reading-news'&&news)return {text:'Read this headline',detail:news.title,href:/^https:\/\//i.test(news.url||'')?news.url:'/desk/paper#companion-news'};
  if(['sourcing','sources-unavailable'].includes(action.key))return {text:'Inspect source coverage',detail:day.research?.summary||'Current source status',href:'/desk/research#companion-news'};
  const note=caution(day),task=chore(day);
  if(note)return {text:/^(event|earnings)/.test(note.key)?'Check the timing with me':'Read my caution',detail:note.text,href:source('note',note.key)};
  if(task)return {text:task.state==='attention'?'This task needs attention':'See the task I am checking',detail:task.label+': '+task.detail,href:source('chore',task.key)};
  return {text:'See my desk notes',detail:day.woman?.headline||'Read the current chores and reasoning.',href:'/desk/overview#dd-woman-headline'};
 }
 // One expression atlas keeps the face in the same place between every activity.
 // A speaking expression is a brief beat, not a perpetual open-mouth loop.
 function portraitFor(actor,action,scene,elapsed,still){
  const cautionKeys=['blocked','unavailable','sources-unavailable','holding','reconciling'];
  const cautious=cautionKeys.includes(action.key)||scene?.expression==='raised-brow';
  const focused=['researching','reading','reading-news','sourcing','thinking','checking'].includes(action.key);
  const speaking=action.speaking&&!still&&elapsed>=2400&&elapsed<7200&&!cautious;
  return {row:actor?1:0,frame:cautious?2:speaking?3:focused?1:0,
   expression:cautious?'cautious':speaking?'explaining':scene?.expression==='listening'?'listening':focused?'thoughtful':'attentive'};
 }
 function briefFor(actor,day){
  if(!day)return {principle:'Current evidence first.',next:'Waiting for a fresh desk update.'};
  if(!actor){
   const state=day.fox?.state;
   const rules={
    researching:['Earn the trade.','I am checking the setup; the decision still needs costs, risk and an invalidation level.'],
    blocked:['Protect the account first.','I will reconsider when the recorded blocker clears. No forced trade.'],
    reconciling:['Confirm before acting again.','I need the broker outcome before another decision. An acknowledgement is not a fill.'],
    holding:['Patience is a position.','I am holding off until the recorded conditions allow another review.'],
    watching:['Selectivity over activity.','A new idea must justify its downside and costs. Passing is a valid decision.'],
    waiting:['Prepare, then act.','I will follow the saved session and policy when the market window opens.'],
    resting:['Protect attention as well as capital.','The desk is resting; I will not describe it as active research.'],
    off:['Authority starts with your policy.','Automatic trading is off. Research commentary does not authorize orders.'],
    done:['Judge the process, then the result.','Review recorded outcomes after costs before changing a rule.']
   };
   const [principle,next]=rules[state]||['Evidence before conviction.','Waiting for the next recorded agent update.'];
   return {principle,next};
  }
  const n=caution(day),task=chore(day),r=day.research;
  if(n)return {principle:'Challenge the thesis kindly.',next:'I want the timing and contrary evidence clear before Fox commits.'};
  if(r?.fresh_sources>0)return {principle:'Curiosity with receipts.',next:`Feeds refresh about every ${Math.round((r.refresh_seconds||300)/60)} minutes while the app runs. I separate reported facts from a hypothesis; Fox still checks the setup.`};
  if(task)return {principle:'Uncertainty belongs in the open.',next:task.state==='attention'?'This source or task needs attention; I will keep its limitation visible.':'I am following the recorded task, then checking what changed.'};

  return {principle:'A strong idea can survive a question.',next:'I compare sources, look for disconfirming evidence and keep the notebook honest.'};
 }
 const api={PERIOD,anchor,source,sceneFor,scenePose,propFor,thoughtFor,portraitFor,briefFor};
 if(typeof module==='object'&&module.exports)module.exports=api;else window.DeskCompanionScenes=api;
})();
