/* Secondary explanations live behind one hover/focus helper per local heading. */
(() => {
  'use strict';
  const groups = new Map();
  let active, timer, serial = 0;
  function hide(){ if(active){active.tip.hidden=true;active.button.setAttribute('aria-expanded','false');active=null;} }
  function scheduleHide(){clearTimeout(timer);timer=setTimeout(()=>{if(active&&(active.button.contains(document.activeElement)||active.tip.contains(document.activeElement)))return;hide();},140);}
  function show(group){
    clearTimeout(timer);if(active!==group)hide();active=group;group.tip.hidden=false;
    group.button.setAttribute('aria-expanded','true');
    const width=document.documentElement.clientWidth;
    group.tip.style.maxWidth=`${width-16}px`;
    const r=group.button.getBoundingClientRect(), t=group.tip.getBoundingClientRect();
    group.tip.style.left=`${Math.max(8,Math.min(r.left,width-t.width-8))}px`;
    group.tip.style.top=`${Math.max(8,r.bottom+t.height+10<innerHeight?r.bottom+7:r.top-t.height-7)}px`;
  }
  const candidates=document.querySelectorAll('.hint-line:not([id]):not([role]):not(.keep-visible), .title-ctx:not([id]), [data-context-help]');
  for(const node of candidates){
    if(node.closest('[role="dialog"],dialog,#execution-summary') || node.querySelector('input,button,select,form,a') || !node.textContent.trim())continue;
    if(node.classList.contains('simple-only'))continue;
    const section=node.closest('.desk-module,details,.panel,.desk-section');
    if(!section)continue;
    const heading=node.closest('h2,h3,h4') || section.querySelector(':scope > summary,:scope > .panel-head > h2,:scope > .panel-head > h3,:scope > h2,:scope > h3,:scope > .section-heading > h2');
    if(!heading)continue;
    let group=groups.get(heading);
    if(!group){
      const caption=heading.cloneNode(true);caption.querySelectorAll('.title-ctx,.simple-only,.panel-ico').forEach(n=>n.remove());
      const label=caption.textContent.replace(/\s+/g,' ').trim().slice(0,85);
      const button=document.createElement('button');button.type='button';button.className='context-help-button';button.textContent='?';
      const tip=document.createElement('div');tip.id=`context-help-${++serial}`;tip.className='context-help-popup';tip.hidden=true;tip.tabIndex=0;tip.setAttribute('role','region');tip.setAttribute('aria-label',`About ${label}`);
      button.setAttribute('aria-label',`About ${label}`);button.setAttribute('aria-describedby',tip.id);button.setAttribute('aria-expanded','false');
      button.setAttribute('aria-controls',tip.id);
      heading.append(button);document.body.append(tip);group={button,tip};groups.set(heading,group);
      button.addEventListener('pointerenter',()=>show(group));button.addEventListener('pointerleave',scheduleHide);
      tip.addEventListener('pointerenter',()=>clearTimeout(timer));tip.addEventListener('pointerleave',scheduleHide);
      button.addEventListener('focus',()=>show(group));button.addEventListener('blur',e=>{if(!tip.contains(e.relatedTarget))scheduleHide();});
      tip.addEventListener('focus',()=>clearTimeout(timer));tip.addEventListener('blur',e=>{if(e.relatedTarget!==button&&!tip.contains(e.relatedTarget))scheduleHide();});
      button.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();show(group);tip.focus();});
    }
    node.classList.add('context-help-copy');group.tip.append(node);
  }
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&active){const button=active.button;if(active.tip.contains(document.activeElement))button.focus();hide();}});
  document.addEventListener('pointerdown',e=>{if(active&&!active.button.contains(e.target)&&!active.tip.contains(e.target))hide();});
  window.addEventListener('resize',hide);window.addEventListener('scroll',e=>{if(active&&!active.tip.contains(e.target))hide();},{passive:true,capture:true});
})();
