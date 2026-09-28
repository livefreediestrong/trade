/* One set of components and forms; view/theme preferences are presentation only. */
(() => {
 'use strict';
 const defaults={skin:'desert',detail:'full',portraits:true};
 const normalize=p=>({skin:['desert','plain'].includes(p?.skin)?p.skin:p?.skin==null?defaults.skin:'plain',detail:['focused','full'].includes(p?.detail)?p.detail:defaults.detail,portraits:p?.portraits!==false});
 if(typeof module==='object'&&module.exports){module.exports={normalize};return;}
 const root=document.documentElement,$=id=>document.getElementById(id);let prefs=normalize({skin:root.dataset.skin,detail:root.dataset.detail,portraits:root.dataset.portraits!=='off'});
 const folds=[];
 // Moving an existing element preserves event handlers, input values and IDs.
 const secondary={auto:['strategy-scorecard','desk-day','live-automation'],research:['public-context','agent-research','strategy-scorecard','desk-research','research-studio'],paper:['desk-options'],overview:[],ticket:[],settings:[]};
 for(const id of secondary[document.body.dataset.deskPage]||[]){const panel=$(id);if(!panel||panel.closest('.desk-tool-fold'))continue;const wrap=document.createElement('details'),summary=document.createElement('summary');wrap.className='desk-tool-fold';summary.textContent=panel.querySelector('h2,h3')?.textContent||'More tools';panel.before(wrap);wrap.append(summary,panel);folds.push(wrap);}
 function reveal(){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch(_){return;}const target=$(id);if(!target)return;let opened=false;for(let p=target.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS'&&!p.open){p.open=true;opened=true;}if(opened)requestAnimationFrame(()=>target.scrollIntoView({block:'start',behavior:'instant'}));}
 function apply(save=false){root.dataset.skin=prefs.skin;root.dataset.detail=prefs.detail;root.dataset.portraits=prefs.portraits?'on':'off';folds.forEach(f=>f.open=prefs.detail==='full');if($('desk-view'))$('desk-view').value=prefs.detail;if($('desk-skin'))$('desk-skin').value=prefs.skin;if($('desk-portraits'))$('desk-portraits').checked=prefs.portraits;if($('desk-view-note'))$('desk-view-note').textContent=prefs.detail==='full'?'Full workspace · every tool and research section is available.':'Focused workspace · extra tools are folded below; open them whenever needed.';if(save)try{localStorage.setItem('desk_appearance_v1',JSON.stringify(prefs));}catch(_){$('desk-view-note').textContent+=' Preferences could not be saved in this browser.';}reveal();window.dispatchEvent(new Event('resize'));}
 $('desk-view')?.addEventListener('change',e=>{prefs.detail=e.target.value;apply(true);});$('desk-skin')?.addEventListener('change',e=>{prefs.skin=e.target.value;apply(true);});$('desk-portraits')?.addEventListener('change',e=>{prefs.portraits=e.target.checked;apply(true);});
 // Light/dark continues to use the existing, independently persisted theme control.
 window.addEventListener('hashchange',reveal);window.addEventListener('storage',e=>{if(e.key==='desk_appearance_v1')try{prefs=normalize(JSON.parse(e.newValue||'{}'));apply();}catch(_){}});
 document.addEventListener('click',e=>{const a=e.target.closest('a[href^="#"]');if(a)setTimeout(reveal,0);});apply();root.dataset.experienceReady='true';
})();
