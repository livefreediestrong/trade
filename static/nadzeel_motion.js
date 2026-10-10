/* Motion Options + Desktop App v3 from the supplied kit. Cosmetic and read-only.
   No network requests, order actions, trading preferences, or generated market data. */
(() => {
  'use strict';
  const scenes = {
    dawn: {bands:['#2B2D55','#454A7C','#7F84B2','#C995A4','#E6A391','#F2C77A'], layers:['#9A7896','#5E4664','#2E2234'], cloud:'#F3C2A8'},
    day: {bands:['#2A64A0','#3F7DB8','#6E9FCB','#9EC0DB','#CBDCE2','#EBDCC0'], layers:['#D09274','#A9472F','#6E2A1C'], cloud:'#F6F0E6'},
    dusk: {bands:['#26264F','#43406F','#7A5B84','#B86F7C','#E39A86','#E8B55A'], layers:['#95596C','#553246','#24161F'], cloud:'#E9A48C'},
    night: {bands:['#0E0F22','#131533','#181B42','#1E224E','#252957','#2C2F5C'], layers:['#2E3266','#1C1C40','#0B0A16'], cloud:'#2A2D58'}
  };
  const stops = [[0,'night'],[5,'night'],[7.5,'dawn'],[10.5,'day'],[15.5,'day'],[18.5,'dusk'],[20.5,'night'],[24,'night']];
  const clamp = n => Math.max(0,Math.min(1,n));
  const mix = (a,b,t) => '#' + [1,3,5].map(i => Math.round(parseInt(a.slice(i,i+2),16)*(1-t)+parseInt(b.slice(i,i+2),16)*t).toString(16).padStart(2,'0')).join('');
  const etClock = new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hourCycle:'h23',hour:'2-digit',minute:'2-digit'});
  const etDay = new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
  function easternHour(date) {
    const parts = Object.fromEntries(etClock.formatToParts(date).map(p=>[p.type,p.value]));
    return Number(parts.hour)+Number(parts.minute)/60;
  }
  function sampleScene(hour, choice='auto') {
    const h = choice in scenes ? ({dawn:7,day:13,dusk:19,night:23})[choice] : ((hour%24)+24)%24;
    let k=0; while(k<stops.length-2 && h>=stops[k+1][0])k++;
    const [start,a]=stops[k], [end,b]=stops[k+1], t=clamp((h-start)/(end-start)), e=t*t*(3-2*t);
    const from=choice in scenes?scenes[choice]:scenes[a], to=choice in scenes?from:scenes[b];
    const dayT=(h-6)/13.5, moonT=((h<12?h+24:h)-19.5)/11;
    const arc=v=>({x:8+v*84,y:62-Math.sin(clamp(v)*Math.PI)*50,visible:v>=0&&v<=1});
    return {key:choice in scenes?choice:e<.5?a:b,hour:h,
      bands:from.bands.map((c,i)=>mix(c,to.bands[i],e)), layers:from.layers.map((c,i)=>mix(c,to.layers[i],e)), cloud:mix(from.cloud,to.cloud,e),
      sun:arc(dayT),moon:arc(moonT),night:clamp(h>=12?(h-19)/2:(6-h)/1.5),
      glow:clamp(1-Math.min(Math.abs(h-6.8),Math.abs(h-19))/1.6)};
  }
  function qualifiedFills(journal, now=new Date()) {
    const seen=new Set(), day=etDay.format(now);
    return (Array.isArray(journal?.executions)?journal.executions:[]).filter(r=>{
      if(!r || r.paper_mode!==false || r.verified!==true || r.superseded_by || !r.account_id || !r.execution_id || !r.ticker)return false;
      const stamp=Date.parse(r.ts);
      if(!Number.isFinite(stamp)||stamp>now.getTime()||etDay.format(new Date(stamp))!==day)return false;
      if(typeof r.shares!=='number'||!Number.isFinite(r.shares)||r.shares<=0||typeof r.price!=='number'||!Number.isFinite(r.price)||r.price<=0)return false;
      const id=JSON.stringify([r.account_id,r.execution_id]); if(seen.has(id))return false;seen.add(id);return true;
    }).sort((a,b)=>Date.parse(a.ts)-Date.parse(b.ts)||String(a.execution_id).localeCompare(String(b.execution_id)));
  }
  // Export only the pure presentation helpers for deterministic checks in Node.
  if(typeof module==='object' && module.exports){module.exports={sampleScene,easternHour,qualifiedFills};return;}
  const $=id=>document.getElementById(id), root=document.documentElement;
  if(!$('desk-sky') || !$('scene-stars'))return;
  const svgNS='http://www.w3.org/2000/svg';
  function svg(tag,attrs) {const el=document.createElementNS(svgNS,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));return el;}
  function div(cls,style={}) {const el=document.createElement('div');el.className=cls;Object.assign(el.style,style);return el;}
  function read(key,fallback,allowed) {let v;try{v=localStorage.getItem(key);}catch(_){}return allowed.includes(v)?v:fallback;}
  function save(key,v){try{localStorage.setItem(key,v);}catch(_){}}
  const sky=$('desk-sky'), treatment=$('desk-landscape'), motion=$('desk-scene-motion'), preview=$('desk-sky-preview'), rotate=$('desk-scene-rotate');
  sky.value=read('nadzeel_sky_v1','auto',['auto',...Object.keys(scenes)]);
  treatment.value=read('nadzeel_landscape_v1','banded',['banded','clouds','earth']);
  motion.checked=read('nadzeel_scene_motion_v1','on',['on','off'])==='on';
  rotate.checked=read('nadzeel_scene_rotate_v1','on',['on','off'])==='on';
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let timer=null, previewStart=null, journal=null, journalKey='', busy=false, busyAt=0, pageActive=true;
  let quiet=root.dataset.deskQuiet==='true';
  const running=()=>pageActive&&!document.hidden&&motion.checked&&!reduced.matches&&!quiet;
  let rotationElapsed=0,lastPaint=performance.now(),wasRotating=false;
  const rotationOrigin=easternHour(new Date()),landscapes=['banded','clouds','earth'];
  let seed=7;
  const rand=()=>{seed=(seed*9301+49297)%233280;return seed/233280;};
  window.DeskConstellations?.render($('scene-stars'));
  for(let i=0;i<7;i++){
    const cloud=div('scene-cloud',{top:(8+rand()*42)+'%',width:(180+rand()*320)+'px',height:(14+rand()*23)+'px'});
    cloud.style.setProperty('--duration',(180+rand()*160)+'s');cloud.style.setProperty('--delay',(-rand()*300)+'s');
    cloud.append(div('cloud-band'));
    const puffs=div('cloud-puffs');
    for(let j=0;j<6;j++)puffs.append(div('cloud-puff',{left:j*14+'%',bottom:(Math.sin(j/5*Math.PI)*25)+'px',width:(55+rand()*45)+'%',height:(50+rand()*55)+'px'}));
    cloud.append(puffs);$('scene-clouds').append(cloud);
  }
  for(let i=0;i<5;i++){
    const w=div('scene-gust',{top:(12+rand()*62)+'%'});
    w.style.setProperty('--duration',(30+rand()*20)+'s');w.style.setProperty('--delay',(-rand()*45)+'s');
    const line=svg('svg',{viewBox:'0 0 600 40',width:600,height:40});
    line.append(svg('path',{d:'M0 20 C100 6 200 34 300 20 S500 6 600 20 M40 28 C140 20 240 36 340 28 S520 20 580 28',fill:'none',stroke:'currentColor','stroke-width':1}));w.append(line);$('scene-wind').append(w);
  }
  // The kit's inline mark and accent animate in CSS; scene pause applies to both.
  function stopTimer(){if(timer!==null)clearTimeout(timer);timer=null;}
  function paint(){
    stopTimer();const now=new Date(),stamp=performance.now();
    if(wasRotating)rotationElapsed+=Math.max(0,stamp-lastPaint);
    lastPaint=stamp;
    if(previewStart!==null&&(!running()||performance.now()-previewStart>=12000))previewStart=null;
    const previewing=previewStart!==null,rotating=rotate.checked;
    wasRotating=rotating&&running()&&!previewing;
    const h=previewing?(5+(stamp-previewStart)/12000*24)%24:rotating?(rotationOrigin+rotationElapsed/360000*24)%24:easternHour(now);
    const landscape=rotating?landscapes[Math.floor(rotationElapsed/120000)%3]:treatment.value;
    const s=sampleScene(h,previewing||rotating?'auto':sky.value);
    root.dataset.sky=s.key;root.dataset.landscape=landscape;root.dataset.sceneRotate=String(wasRotating);
    root.dataset.sceneMotion=running()?'on':'off';root.dataset.scenePreview=String(previewing);
    root.dataset.sceneBusy=String(busy&&Date.now()-busyAt<90000); // A lost status feed must not spin forever.
    s.bands.forEach((c,i)=>root.style.setProperty('--sky'+(i+1),landscape==='earth'?mix(c,'#B89A7A',.28):c));
    ['--mesa-distant','--mesa-far','--mesa-near'].forEach((key,i)=>root.style.setProperty(key,s.layers[i]));
    root.style.setProperty('--scene-cloud',s.cloud);root.style.setProperty('--scene-night',s.night);root.style.setProperty('--scene-glow',s.glow);
    for(const [id,pos] of [['scene-sun',s.sun],['scene-moon',s.moon]]){const el=$(id);el.hidden=!pos.visible;el.style.left=pos.x+'%';el.style.top=pos.y+'%';}
    $('desk-clock').textContent=new Intl.DateTimeFormat('en-US',{weekday:'short',hour:'numeric',minute:'2-digit',timeZone:'America/New_York',timeZoneName:'short'}).format(now);
    preview.disabled=!motion.checked||reduced.matches||quiet;preview.setAttribute('aria-pressed',String(previewing));preview.textContent=previewing?'Stop sky preview':'Preview a day · 12s';
    $('scene-motion-note').textContent=quiet?'Motion paused · Quiet desk':previewing?'Previewing scenery only · returns to your sky':reduced.matches?'Motion paused · system reduced-motion setting':!motion.checked?'Scene motion paused':rotating?'Decorative rotation · 6-minute sky, 2-minute landscapes':sky.value==='auto'?'Decorative sky · Eastern time':'Decorative sky · '+sky.options[sky.selectedIndex].text;
    renderJournal(now);
    if(pageActive&&!document.hidden)timer=setTimeout(paint,previewing?100:wasRotating?1000:15000);
  }
  function changed(){previewStart=null;save('nadzeel_sky_v1',sky.value);save('nadzeel_landscape_v1',treatment.value);save('nadzeel_scene_motion_v1',motion.checked?'on':'off');save('nadzeel_scene_rotate_v1',rotate.checked?'on':'off');paint();}
  for(const el of [sky,treatment])el.addEventListener('change',()=>{rotate.checked=false;changed();});
  for(const el of [motion,rotate])el.addEventListener('change',changed);
  preview.addEventListener('click',()=>{if(!running())return;previewStart=previewStart===null?performance.now():null;paint();});
  reduced.addEventListener('change',paint);
  window.addEventListener('desk:attention',e=>{quiet=!!e.detail?.quiet;paint();});
  document.addEventListener('visibilitychange',paint);
  window.addEventListener('pagehide',()=>{pageActive=false;previewStart=null;paint();});
  window.addEventListener('pageshow',()=>{pageActive=true;paint();});
  window.addEventListener('moss:state',e=>{busy=e.detail?.busy===true&&e.detail?.error!==true;busyAt=Date.now();paint();});
  window.addEventListener('moss:journal',e=>{journal=e.detail;renderJournal(new Date());});

  function inspectFill(row,button){
    for(const child of $('trade-sky-stars').children)child.setAttribute('aria-pressed',String(child===button));
    const instrument=row.asset_type==='OPT'?(row.local_symbol||`${row.ticker} ${row.expiry||''} ${row.right||''} ${row.strike??''}`):row.ticker;
    const fee=typeof row.commission==='number'&&Number.isFinite(row.commission)?`${row.commission} ${row.commission_currency||'currency unknown'}`:'not reported';
    $('trade-sky-detail').textContent=`${etClock.format(new Date(row.ts))} ET · ${instrument} · ${row.side||'side unknown'} ${row.shares} ${row.asset_type==='OPT'?'contracts':'units'} @ ${row.price} ${row.currency||'currency unknown'} · fee ${fee} · account …${String(row.account_id).slice(-4)} · execution ${row.execution_id}`;
  }
  function renderJournal(now){
    if(!journal||!$('trade-sky-stars'))return; // the trade sky is not on every page
    const all=qualifiedFills(journal,now), rows=all.slice(-24), key=JSON.stringify([rows,journal.last_sync,journal.error,etDay.format(now)]);
    if(key===journalKey)return;journalKey=key;
    // Rebuild only when records change; preserve keyboard selection when possible.
    const old=$('trade-sky-stars').querySelector('[aria-pressed="true"]')?.dataset.fill;
    const wasFocused=$('trade-sky-stars').contains(document.activeElement);
    $('trade-sky-count').textContent=`${all.length} real ${all.length===1?'fill':'fills'} today`;
    $('trade-sky-empty').hidden=rows.length>0;$('trade-sky-empty').textContent='No recorded real fills today. Markers appear when genuine executions arrive.';
    $('trade-sky-source').textContent=`Retained journal · ${rows.length} shown${all.length>24?' (most recent 24)':''} · last sync ${journal.last_sync&&Number.isFinite(Date.parse(journal.last_sync))?new Date(journal.last_sync).toLocaleString():'not available'}${journal.error?' · sync unavailable; retained records only':''}. Coverage may be incomplete.`;
    $('trade-sky-lines').replaceChildren();$('trade-sky-stars').replaceChildren();
    const cols=Math.min(rows.length,8), bands=Math.ceil(rows.length/8);
    const positions=rows.map((r,i)=>({x:cols===1?50:7+(i%8)/(cols-1)*86,y:(bands===1?48:23+Math.floor(i/8)*25)+Math.sin(i*1.65)*6}));
    if(rows.length>1)$('trade-sky-lines').append(svg('polyline',{points:positions.map(p=>`${p.x*8},${p.y*1.6}`).join(' '),fill:'none',pathLength:100,'class':'constellation-path'}));
    let selected=null;
    rows.forEach((r,i)=>{
      const button=document.createElement('button'), p=positions[i], id=JSON.stringify([r.account_id,r.execution_id]);
      button.type='button';button.className='trade-star';button.dataset.fill=id;
      button.style.left=p.x+'%';button.style.top=p.y+'%';button.style.setProperty('--delay',i*.06+'s');
      button.setAttribute('aria-label',`${r.ticker} ${r.side||''} ${r.shares} at ${r.price} ${r.currency||''}, ${etClock.format(new Date(r.ts))} ET, account ending ${String(r.account_id).slice(-4)}`);button.setAttribute('aria-pressed','false');
      button.append(div('trade-star-diamond'));button.addEventListener('click',()=>inspectFill(r,button));$('trade-sky-stars').append(button);
      if(id===old)selected={r,button};
    });
    if(selected){inspectFill(selected.r,selected.button);if(wasFocused)selected.button.focus();}
    else { $('trade-sky-detail').textContent='Recorded executions only; partial fills count separately.';if(wasFocused)$('trade-sky').querySelector('.module-title,summary').focus(); }
  }
  paint();
})();
