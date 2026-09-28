/* Live trading ON/OFF. OFF works at once; ON needs the typed phrase and the server's own checks. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);if(!$('live-switch'))return;
 let busy=false,state=null,refreshPending=false;
 function unknown(message){
  state=null;$('ls-state').dataset.live='unknown';$('ls-state').textContent='Status unknown';
  $('ls-plain').textContent=message;$('ls-on').hidden=$('ls-off').hidden=true;$('ls-confirm').hidden=true;
 }
 function render(d){
  state=d;const pill=$('ls-state');
  pill.dataset.live=String(!!d.live);pill.textContent=d.live?(d.mode==='auto_live'?'ON · Fox automatic':'ON · you approve each order'):'OFF';
  $('ls-plain').textContent=d.plain;
  $('ls-off').hidden=!d.live;$('ls-on').hidden=!!d.live;
  $('ls-on').disabled=!d.broker_configured;$('ls-on').title=d.broker_configured?'':'Connect a broker in Settings first';
  if(d.live)$('ls-confirm').hidden=true;
  $('ls-confirm-text').textContent='Turning ON lets this desk send orders to '+(d.real_money?'your REAL-MONEY account':'your broker account')+'. You still approve every order, and Fox stays off unless you start him on the Fox page.';
 }
 async function send(body,message){
  if(busy)return;busy=true;$('ls-status').textContent=message;
  const c=new AbortController(),t=setTimeout(()=>c.abort(),30000);
  try{
   const r=await fetch('/api/live/master',body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:c.signal,credentials:'same-origin'}:{signal:c.signal,credentials:'same-origin'});
   const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Live switch unavailable');
   render(d);$('ls-status').textContent=body?(d.live?'Live trading is ON.':'Live trading is OFF.'):'';
   if(body)window.dispatchEvent(new CustomEvent('desk:config-changed'));
  }catch(e){
   const message=body?'The request result is unknown. The setting may have changed; checking the saved mode before another action.':
    'Cannot verify the live trading mode. Reconnecting to read its current status.';
   unknown(message);$('ls-status').textContent=message;
   if(body)refreshPending=true;
  }
  finally{clearTimeout(t);busy=false;if(refreshPending){refreshPending=false;send(null,'Checking the saved mode…');}}
 }
 $('ls-off').addEventListener('click',()=>send({live:false},'Turning live trading off…'));
 $('ls-on').addEventListener('click',()=>{$('ls-confirm').hidden=false;$('ls-phrase').value='';$('ls-phrase').focus();});
 $('ls-cancel').addEventListener('click',()=>{$('ls-confirm').hidden=true;$('ls-status').textContent='Nothing changed.';});
 $('ls-confirm').addEventListener('submit',e=>{e.preventDefault();send({live:true,confirm:$('ls-phrase').value},'Checking the broker account…');});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)send(null,'');});
 window.addEventListener('desk:config-changed',()=>{if(busy)refreshPending=true;else send(null,'');});
 window.addEventListener('desk:state',event=>{
  const mode=event.detail?.config?.mode;
  if(mode&&mode!==state?.mode){if(busy)refreshPending=true;else send(null,'');}
 });
 send(null,'');
})();
