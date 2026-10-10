/* Local dated maintenance evidence only. No operational controls. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('maintenance-plan'))return;
 const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 const when=t=>{const d=new Date(t);return t&&Number.isFinite(d.getTime())?d.toLocaleString():'No dated record';};
 let timer=null,busy=false,controller=null,epoch=0;
 function draw(d){
  const evidence=$('maintenance-evidence'),previous=evidence.querySelector('details');
  const expanded=previous?.open===true,summaryFocused=!!previous&&document.activeElement===previous.querySelector('summary');
  const w=d.broker_window||{},suffix=w.schedule_needs_review?' The saved schedule needs a fresh review.':'';
  $('maintenance-current').textContent=(w.within_published_window?'Within IBKR’s published maintenance window. ':'Next published IBKR window: ')+when(w.starts_at)+' to '+when(w.ends_at)+'. This is a schedule, not a confirmed outage.'+suffix;
  const tests=d.tests||{},testMessages={
   passed:'The current app version passed the full local test suite.',
   failed:'The current app version has failing local tests. Review the saved details.',
   older_passed:'An earlier version passed. The current app or test suite still needs a full check.',
   older_failed:'The last full check found failures in an earlier version. The current app or test suite has not yet been fully checked.',
   unknown:'No dated full-test result is available for this app.'
  };
  const wd=d.watchdog||{},daily=d.daily||{},b=d.backup||{},rows=[
   ['Automatic app check',wd.recent?'An automatic check was recorded within the last 15 minutes.':'No recent automatic check. Check whether the PC and scheduled task are running.','Last check: '+when(wd.at)],
   ['Daily upkeep',daily.recent&&daily.problem_count===0?'Latest recorded upkeep completed without reported problems.':daily.problem_count>0?`${daily.problem_count} upkeep problem(s) need review.`:'Daily upkeep is overdue or its result is unavailable.','Last run: '+when(daily.at)],
   ['Recovery backup',b.ok===true&&b.recent===true&&!b.stale&&!b.last_error?'Latest backup was verified when created. This view does not recheck every archived byte.':'A backup is missing, old or has a later failure. Review before changing the app.','Last verification: '+when(b.verified_at)],
   ['App checks',(testMessages[tests.status]||testMessages.unknown)+(tests.update_waiting?' An app update is waiting to load.':''),'Last full check: '+when(tests.at)]
  ];
  const cards=rows.map(([title,text,detail])=>{const a=node('article','');a.append(node('h4',title),node('p',text),node('small',detail));return a;});
  const details=node('details',''),summary=node('summary','Test result and its limits');details.open=expanded;details.append(summary);
  for(const text of [tests.summary,tests.last_run_note,tests.details_file?'Local details: data/'+tests.details_file:'',tests.scope])if(text)details.append(node('p',text));
  cards[cards.length-1].append(details);
  const source=$('maintenance-source'),link=node('a','Check IBKR’s current system status');link.href='https://www.interactivebrokers.com/en/software/systemStatus.php';link.target='_blank';link.rel='noopener noreferrer';source.textContent=w.scope+' Schedule last reviewed '+w.verified_on+'. ';source.append(link);
  evidence.replaceChildren(...cards);
  if(summaryFocused)summary.focus({preventScroll:true});
 }
 async function refresh(){
  if(busy||document.hidden)return;busy=true;const current=epoch,abort=new AbortController();controller=abort;const deadline=setTimeout(()=>abort.abort(),10000);
  try{const r=await fetch('/api/maintenance',{cache:'no-store',signal:abort.signal});if(!r.ok)throw Error('unavailable');const d=await r.json();if(current===epoch)draw(d);}
  catch(e){if(current===epoch){$('maintenance-current').textContent='Maintenance records could not be read. Current backup and upkeep status are unknown.';$('maintenance-evidence').replaceChildren();$('maintenance-source').replaceChildren();}}
  finally{clearTimeout(deadline);busy=false;controller=null;}
 }
 function start(){clearInterval(timer);timer=setInterval(refresh,60000);refresh();}
 window.addEventListener('pagehide',()=>{epoch++;controller?.abort();clearInterval(timer);});
 window.addEventListener('pageshow',e=>{if(e.persisted)start();});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});start();
})();
