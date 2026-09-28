/* One set of components and forms; view/theme preferences are presentation only. */
(() => {
 'use strict';
 // "focused" is the Simple view (the default); "full" shows every section in place.
 const defaults={skin:'desert',detail:'focused',portraits:true};
 const normalize=p=>({skin:['desert','plain'].includes(p?.skin)?p.skin:p?.skin==null?defaults.skin:'plain',detail:['focused','full'].includes(p?.detail)?p.detail:defaults.detail,portraits:p?.portraits!==false});
 // Simple view keeps only these top-level panels; everything else goes to "More tools".
 const essentials={
  overview:['#desk-overview','#fidelity','#live-switch','#daily-brief'],
  auto:['.desk-page-banner','#live-switch','#fox-workspace','#desk-live'],
  paper:['.desk-page-banner','#desk-paper'],
  research:['.desk-page-banner','#company-check','.research-shelf'],
  ticket:['.desk-page-banner','.desk-section'],
  settings:['.desk-page-banner','#connection-summary','#watch-alerts','#desk-settings'],
 };
 // Long blocks inside kept panels: a one-line header in Simple view, open in Everything.
 // "selector", "selector | Header text", or "heading:Title" (matches a panel by its heading).
 const collapse={
  auto:['#nightly-review','.fox-reviews','.fox-calendar','#beginner-moss | What Fox is doing','#live-loop-panel'],
  paper:['#paper-friction | Pretend trading costs','#equity-score-panel','#daily-recap-panel','#edge-panel','heading:Paper fills',
         'heading:Paper decision history','#paper-experiments'],
  research:['#desk-research','#research-studio'],
  ticket:['#beginner-moss | What Fox is doing','#live-loop-panel'],
  settings:['#maintenance-plan','#wa-history | Recent alerts','#trade-guards','heading:Watchlist, brains & live settings',
            '#trade-sky | Trade journal map','#sky-guide'],
 };
 // Heading text without help marks ("?") or decorative symbols (▸ ⌒ ◷ ❚❚).
 const panelTitle=el=>{const h=el.querySelector('h1,h2,h3,summary');const t=(h?.textContent||'').replace(/\s+/g,' ').replace(/^[^\p{L}\p{N}]+/u,'').replace(/[\s?]+$/,'').trim();return t.slice(0,60)||'Section';};
 if(typeof module==='object'&&module.exports){module.exports={normalize,essentials,collapse,panelTitle};return;}
 const STORE='desk_appearance_v2';
 const root=document.documentElement,$=id=>document.getElementById(id);let prefs=normalize({skin:root.dataset.skin,detail:root.dataset.detail,portraits:root.dataset.portraits!=='off'});
 // Moving an existing element preserves event handlers, input values and IDs.
 // A placeholder remembers each panel's original spot for the Everything view.
 const main=document.querySelector('main.desk-page-main'),keep=essentials[document.body.dataset.deskPage];
 const moved=[];let more=null,moreBody=null;
 if(main&&keep){
  for(const panel of [...main.children]){
   if(keep.some(sel=>panel.matches(sel)))continue;
   const spot=document.createComment('desk panel');panel.before(spot);moved.push({panel,spot});
  }
  if(moved.length){
   more=document.createElement('details');more.className='desk-more-tools';more.id='desk-more-tools';
   const summary=document.createElement('summary'),label=document.createElement('strong'),list=document.createElement('span');
   label.textContent=`More tools (${moved.length})`;
   list.textContent=moved.map(m=>panelTitle(m.panel)).join(' · ');
   summary.append(label,list);moreBody=document.createElement('div');moreBody.className='desk-more-body';
   more.append(summary,moreBody);main.append(more);
   more.addEventListener('toggle',()=>window.dispatchEvent(new Event('resize')));
  }
 }
 const folds=[];
 const matches=rule=>{
  if(rule.startsWith('heading:')){const title=rule.slice(8);return [...document.querySelectorAll('main section, main article')].filter(e=>panelTitle(e).startsWith(title));}
  return [...document.querySelectorAll(rule)];
 };
 for(const entry of collapse[document.body.dataset.deskPage]||[]){
  const [rule,label]=entry.split(' | ');
  for(const block of matches(rule)){
   if(block.closest('.desk-inline-fold'))continue;
   const wrap=document.createElement('details'),summary=document.createElement('summary');
   wrap.className='desk-inline-fold';summary.textContent=label||panelTitle(block);
   block.before(wrap);wrap.append(summary,block);folds.push(wrap);
   wrap.addEventListener('toggle',()=>window.dispatchEvent(new Event('resize')));
  }
 }
 function place(){
  const simple=prefs.detail==='focused';
  for(const f of folds)f.open=!simple;
  for(const {panel,spot} of moved){if(simple)moreBody.append(panel);else spot.after(panel);}
  if(more)more.hidden=!simple;
 }
 function reveal(){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch(_){return;}const target=id&&$(id);if(!target)return;let opened=false;for(let p=target.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS'&&!p.open){p.open=true;opened=true;}if(opened)requestAnimationFrame(()=>target.scrollIntoView({block:'start',behavior:'instant'}));}
 function note(){
  if(prefs.detail==='full')return 'Everything: every tool and research section is shown.';
  return moved.length?`Simple: just the essentials. ${moved.length} more section${moved.length===1?' is':'s are'} under "More tools" at the bottom of this page.`:'Simple: just the essentials.';
 }
 function apply(save=false){
  root.dataset.skin=prefs.skin;root.dataset.detail=prefs.detail;root.dataset.portraits=prefs.portraits?'on':'off';place();
  if($('desk-view'))$('desk-view').value=prefs.detail;
  document.querySelectorAll('[data-desk-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.deskView===prefs.detail)));
  if($('desk-skin'))$('desk-skin').value=prefs.skin;if($('desk-portraits'))$('desk-portraits').checked=prefs.portraits;
  if($('desk-view-note'))$('desk-view-note').textContent=note();
  if(save)try{localStorage.setItem(STORE,JSON.stringify(prefs));}catch(_){if($('desk-view-note'))$('desk-view-note').textContent+=' Preferences could not be saved in this browser.';}
  reveal();window.dispatchEvent(new Event('resize'));
 }
 const setView=v=>{prefs.detail=v;apply(true);};
 $('desk-view')?.addEventListener('change',e=>setView(e.target.value));
 document.querySelectorAll('[data-desk-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.deskView)));
 $('desk-skin')?.addEventListener('change',e=>{prefs.skin=e.target.value;apply(true);});$('desk-portraits')?.addEventListener('change',e=>{prefs.portraits=e.target.checked;apply(true);});
 // Light/dark continues to use the existing, independently persisted theme control.
 window.addEventListener('hashchange',reveal);window.addEventListener('storage',e=>{if(e.key===STORE)try{prefs=normalize(JSON.parse(e.newValue||'{}'));apply();}catch(_){}});
 document.addEventListener('click',e=>{const a=e.target.closest('a[href^="#"]');if(a)setTimeout(reveal,0);});apply();root.dataset.experienceReady='true';
})();
