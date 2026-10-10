/* Local statement evidence only. No broker requests, activation or inferred fills. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),panel=$('option-activity');
 if(!panel)return;
 let draft=null,busy=false,epoch=0,loaded=false;
 const message=text=>{$('option-activity-message').textContent=text;};
 function controls(){
  const fresh=draft&&Date.now()<Date.parse(draft.expires_at);
  $('option-activity-save').disabled=busy||!fresh||!$('option-activity-ack').checked;
  $('option-activity-preview').disabled=busy||!$('option-activity-file').files?.length;
  $('option-activity-refresh').disabled=busy;
  $('option-activity-file').disabled=busy;
  if(draft&&!fresh)message('Preview expired. Read the file again before saving.');
 }
 async function call(path,body){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  try{
   const r=await fetch('/api/live-agent/options/activity'+path,body===undefined?{signal:controller.signal}:
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
   const result=await r.json();if(!r.ok||result.ok!==true)throw Error(result.error||'Statement review unavailable');return result;
  }finally{clearTimeout(timer);}
 }
 function render(result,preview){
  $('option-activity-summary').textContent=`${preview?'Unsaved preview':'Saved evidence'} · ${result.paper_mode?'Broker paper':'Real account'} …${result.account_suffix} · ${result.total_rows} rows · ${result.conflicting_events} conflicting events. ${result.truncated?'Showing the latest 100 rows. ':''}${result.note}`;
  const list=$('option-activity-rows');list.replaceChildren();
  for(const row of result.rows||[]){
   const item=document.createElement('li');
   const amounts=`Reported P&L: ${row.realized_pnl===null?'not supplied':row.realized_pnl+' '+row.currency}; fees/tax: ${row.commission_tax===null?'not supplied':row.commission_tax+' '+row.currency}`;
   item.textContent=`${row.date} · ${row.symbol} · ${row.type} · quantity ${row.quantity} · contract ${row.con_id} · trade ${row.trade_id}. ${row.conflict?'CONFLICT: values differ between imports; excluded from totals. ':''}${row.match}. ${amounts}.`;
   list.appendChild(item);
  }
  $('option-activity-totals').textContent=Object.entries(result.by_currency||{}).map(([currency,values])=>
   `${currency}: reported P&L ${values.realized_pnl===null?'not supplied':values.realized_pnl}; ${values.pnl_missing} of ${values.rows} rows lack P&L`).join(' · ')||'No unambiguous reported amounts available.';
 }
 async function refresh(){
  if(busy)return;const run=++epoch;busy=true;draft=null;$('option-activity-confirm').hidden=true;controls();
  try{const result=await call('');if(run!==epoch)return;render(result,false);loaded=true;message('Saved evidence loaded. Trading controls are unchanged.');}
  catch(e){message('Saved evidence is unavailable. '+e.message);$('option-activity-summary').textContent='Evidence unavailable; previous results are not current.';$('option-activity-rows').replaceChildren();$('option-activity-totals').textContent='';}
  finally{busy=false;controls();}
 }
 $('option-activity-file').addEventListener('change',()=>{++epoch;draft=null;$('option-activity-confirm').hidden=true;message('File selected. Read it for review; nothing has been saved.');controls();});
 $('option-activity-preview').addEventListener('click',async()=>{
  if(busy)return;const file=$('option-activity-file').files?.[0];if(!file)return;
  const run=++epoch;busy=true;draft=null;$('option-activity-confirm').hidden=true;controls();message('Reading the selected local report…');
  try{
   if(file.size>1000000||!file.name.toLowerCase().endsWith('.csv'))throw Error('Choose a CSV file no larger than 1 MB.');
   const csv=await file.text();if(run!==epoch)return;
   const result=await call('/preview',{csv,filename:file.name});if(run!==epoch)return;
   draft=result;render(result,true);$('option-activity-ack').checked=false;$('option-activity-confirm').hidden=false;
   message(`${result.added} new versions; ${result.already_stored} already saved. Review below before saving. Nothing changed at the broker.`);
  }catch(e){if(run===epoch){message(e.message);$('option-activity-summary').textContent='This file could not be reviewed; nothing was saved.';$('option-activity-rows').replaceChildren();$('option-activity-totals').textContent='';}}
  finally{busy=false;controls();}
 });
 $('option-activity-save').addEventListener('click',async()=>{
  controls();if($('option-activity-save').disabled)return;
  const token=draft.token,run=++epoch;busy=true;draft=null;controls();message('Saving statement evidence locally…');
  try{const result=await call('/save',{token,acknowledge:true});if(run!==epoch)return;render(result,false);loaded=true;message(result.message);}
  catch(e){message('Save was not confirmed. Refresh saved evidence before trying another import. '+e.message);}
  finally{busy=false;$('option-activity-confirm').hidden=true;controls();}
 });
 $('option-activity-ack').addEventListener('change',controls);
 $('option-activity-refresh').addEventListener('click',refresh);
 panel.addEventListener('toggle',()=>{if(panel.open&&!loaded&&!busy)refresh();});
 setInterval(controls,1000);controls();
})();
