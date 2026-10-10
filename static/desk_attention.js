/* Local presentation preferences only. Never pauses or enables trading. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id), quiet=$('desk-quiet'), task=$('desk-task');
 if(!quiet||!task)return;
 let saved={};try{saved=JSON.parse(localStorage.getItem('desk_attention_v1')||'{}')||{};}catch(_){}
 quiet.checked=saved.quiet===true;
 if([...task.options].some(o=>o.value===saved.task))task.value=saved.task;
 function apply(){
  document.documentElement.dataset.deskQuiet=String(quiet.checked);
  $('desk-attention-note').textContent=quiet.checked?'Quiet desk on. Motion and unsolicited speech paused. Trading and risk alerts continue.':'Quiet desk pauses decorative motion and unprompted speech. Trading and risk alerts continue.';
  window.dispatchEvent(new CustomEvent('desk:attention',{detail:{quiet:quiet.checked}}));
  try{localStorage.setItem('desk_attention_v1',JSON.stringify({quiet:quiet.checked,task:task.value}));}catch(_){}
 }
 quiet.addEventListener('change',apply);task.addEventListener('change',apply);
 $('desk-task-go').addEventListener('click',()=>{
  const target=$(task.value);
  if(!target){ // split pages: the section lives on another page
   const page={'desk-options':'paper','desk-paper':'paper','moss-desk':'paper','moss-notebook':'research','agent-research':'research','desk-settings':'settings'}[task.value]||'auto';
   location.href='/desk/'+page+'#'+encodeURIComponent(task.value);return;
  }
  for(let p=target;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;
  target.setAttribute('tabindex','-1');target.focus({preventScroll:true});target.scrollIntoView({behavior:'instant',block:'start'});
 });apply();
})();
