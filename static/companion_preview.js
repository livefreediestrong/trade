/* Local animation examples. No requests, trading events or changes to desk snapshots. */
(() => {
 'use strict';
 const MODES=['research','thinking','explaining','waiting','resting','pass','order','news','journal'];
 function example(mode,now=Date.now()){
  const day={fox:{state:'watching',ticker:'DEMO',headline:'Example only.',recent:[]},woman:{headline:'Example only.',reasoning:[],chores:[]}};
  const cues=['',''];let scene=null;
  if(mode==='research'){day.fox.state='researching';scene={kind:'research'};}
  if(mode==='thinking'){day.woman.reasoning=[{key:'event:example',level:'caution',text:'Example: an announcement is approaching.'}];scene={kind:'caution'};}
  if(mode==='explaining')cues[1]='guide';
  if(mode==='waiting')day.fox.state='waiting';
  if(mode==='resting')day.fox.state='resting';
  if(mode==='pass'){day.fox.recent=[{id:'example-pass',kind:'skip',at:new Date(now).toISOString(),text:'Example: the recorded decision was to pass.'}];scene={kind:'pass'};}
  if(mode==='order')cues[0]='trade';
  if(mode==='news')cues[1]='news';
  if(mode==='journal')day.woman.chores=[{key:'journal',label:'Write in the journal',state:'working',detail:'Example journal entry'}];
  return {day,cues,scene};
 }
 function story(mode,beat){
  const lines={research:['Fox opens the chart. Changing Woman listens.','She checks the evidence while he studies.','She thinks it through. Fox looks up to listen.','They return to the research together.'],
   thinking:['Changing Woman reads the caution.','A raised eyebrow: the timing needs attention.','She explains it with an open palm. Fox listens.','They settle back; the desk retains its actual decision.'],
   pass:['Fox reads the recorded pass.','He takes a final look at the notebook.','He puts it away. Changing Woman gives a quiet nod.','Back on watch. Passing is a recorded decision, not a failed order.']};
  return lines[mode]?.[Math.min(3,beat)]||({explaining:'An open palm and a composed expression accompany her explanation.',waiting:'Fox keeps a clock nearby while Changing Woman rests.',resting:'An off-duty moment: relaxed ears, a curled tail and quiet rest.',order:'Fox opens the order record. This does not imply a fill or profit.',news:'Changing Woman reads a newspaper and considers the headline.',journal:'Changing Woman works through the journal with a moving pencil.'})[mode]||'';
 }
 if(typeof module==='object'&&module.exports){module.exports={MODES,example,story};return;}
 const $=id=>document.getElementById(id),dialog=$('moss-preview-dialog'),avatars=window.MossAvatars,scenes=window.DeskCompanionScenes;
 if(!dialog||!avatars||!scenes)return;
 const actors=[$('moss-preview-fox'),$('moss-preview-woman')],frames=[null,null];
 let timer=null,elapsed=0,lastAt=0,paused=false,opener=null;
 function draw(){
  clearTimeout(timer);timer=null;
  if(!dialog.open||document.hidden){lastAt=0;actors.forEach(el=>{el.dataset.still='true';});return;}
  const now=performance.now(),prefs=avatars.appearance(),stopped=paused||prefs.quiet||prefs.reduced||prefs.motion==='dock'||prefs.motion==='hide';
  if(lastAt&&!stopped)elapsed=Math.min(16000,elapsed+now-lastAt);lastAt=stopped?0:now;
  const still=stopped||elapsed>=16000,mode=$('moss-preview-scene').value,model=example(mode),beat=Math.min(3,Math.floor(elapsed/4000));
  actors.forEach((el,i)=>{
   const action=avatars.companionAction(i,{day:model.day,sky:'night',cue:model.cues[i],speaking:i===1});
   const performancePose=scenes.scenePose(model.scene,i,elapsed,'balanced',still),pose=performancePose||avatars.actionPose(action,elapsed,still);
   frames[i]=scenes.paintPose(el,pose,still,frames[i]);
   el.dataset.action=action.key;el.dataset.expression=performancePose?.expression||(i?'composed':mode==='research'?'curious':'attentive');el.dataset.speaking=String(i===(mode==='thinking'||mode==='explaining'?1:0));
   el.style.width=el.style.height=({compact:112,comfortable:144,large:168})[prefs.size]+'px';
   const prop=scenes.propFor(i,action,model.day);el.dataset.prop=prop;$(i?'moss-preview-woman-prop':'moss-preview-fox-prop').setAttribute('href','/static/companion-props.svg#'+prop);
   $(i?'moss-preview-woman-caption':'moss-preview-fox-caption').textContent=performancePose?.label||action.label;
   el.setAttribute('aria-label',(i?'Changing Woman':'Fox')+' · example · '+(performancePose?.label||action.label));
  });
  $('moss-preview-story').textContent=(elapsed>=16000?'Scene finished. ':'')+story(mode,beat);
  $('moss-preview-motion-note').textContent=prefs.reduced?'Reduced motion is on: meaningful still poses.':prefs.quiet?'Quiet desk is on: the preview holds still.':['dock','hide'].includes(prefs.motion)?'Your companion movement preference keeps this preview still.':paused?'Preview paused.':elapsed>=16000?'Replay this scene or choose another.':'Playing an animation example. Live activity continues separately.';
  if(!still)timer=setTimeout(draw,400);
 }
 function replay(){elapsed=0;lastAt=0;paused=false;$('moss-preview-pause').textContent='Pause preview';$('moss-preview-pause').setAttribute('aria-pressed','false');draw();}
 function open(e){e?.preventDefault();opener=e?.currentTarget||document.activeElement;const prefs=avatars.appearance();$('moss-character-size').value=prefs.size;$('moss-frequency').value=prefs.frequency;dialog.showModal();replay();avatars.refresh();}
 for(const id of ['desk-companions-open','moss-preview-open'])$(id)?.addEventListener('click',open);
 for(const id of ['moss-fox-thought','moss-woman-thought'])$(id)?.addEventListener('click',e=>{if($(id).getAttribute('href')==='#moss-preview-dialog')open(e);});
 $('moss-preview-close').addEventListener('click',()=>dialog.close());
 dialog.addEventListener('close',()=>{clearTimeout(timer);timer=null;actors.forEach(el=>{el.dataset.still='true';});opener?.focus?.();avatars.refresh();});
 $('moss-preview-scene').addEventListener('change',replay);$('moss-preview-replay').addEventListener('click',replay);
 $('moss-preview-pause').addEventListener('click',()=>{paused=!paused;lastAt=0;$('moss-preview-pause').textContent=paused?'Resume preview':'Pause preview';$('moss-preview-pause').setAttribute('aria-pressed',String(paused));draw();});
 for(const id of ['moss-character-size','moss-frequency'])$(id).addEventListener('change',()=>{window.dispatchEvent(new CustomEvent('moss:appearance',{detail:{size:$('moss-character-size').value,frequency:$('moss-frequency').value}}));draw();});
 window.addEventListener('desk:attention',draw);matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',draw);
 document.addEventListener('visibilitychange',draw);window.addEventListener('pagehide',()=>{clearTimeout(timer);timer=null;});window.addEventListener('pageshow',draw);
})();
