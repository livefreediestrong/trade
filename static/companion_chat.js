/* Source-attributed shared chat. Sends no trading/configuration requests. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),dialog=$('companion-chat');if(!dialog)return;
 const workspace=document.body.dataset.deskPage==='paper'?'paper':'live',base='/api/companion/chat',query='?workspace='+workspace;
 const node=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=text??'';if(cls)n.className=cls;return n;};
 const stamp=t=>Number.isFinite(Date.parse(t))?new Date(t).toLocaleString():'Date unavailable';
 let state=null,cid='',timer=null,loading=false,sending=false,edit=null,lastDraw='',requestDraft=null;
 let preferences={provider:'gemini',role:'both',local_model:''};
 try{const saved=JSON.parse(localStorage.getItem('desk_chat_preferences_'+workspace)||'null');if(saved){preferences={provider:['gemini','local','ollama'].includes(saved.provider)?saved.provider:'local',role:['both','fox','changing_woman'].includes(saved.role)?saved.role:'both',local_model:typeof saved.local_model==='string'?saved.local_model:''};}}catch(_){preferences.provider='local';}
 $('chat-provider').value=preferences.provider;$('chat-role').value=preferences.role;$('chat-local-label').hidden=preferences.provider!=='ollama';
 function savePreferences(){preferences.provider=$('chat-provider').value;preferences.role=$('chat-role').value;preferences.local_model=$('chat-local-model').value||preferences.local_model;try{localStorage.setItem('desk_chat_preferences_'+workspace,JSON.stringify(preferences));}catch(_){status('Reply preference could not be saved in this browser. Check the provider after reloading.');}}
 try{cid=sessionStorage.getItem('desk_chat_'+workspace)||'';requestDraft=JSON.parse(sessionStorage.getItem('desk_chat_pending_'+workspace)||'null');}catch(_){}
 const status=text=>$('chat-status').textContent=text;
 try{$('chat-message').value=sessionStorage.getItem('desk_chat_draft_'+workspace)||'';}catch(_){}
 function saveDraft(){try{sessionStorage.setItem('desk_chat_draft_'+workspace,$('chat-message').value);}catch(_){}}
 $('chat-message').addEventListener('input',saveDraft);
 const remember=()=>{try{sessionStorage.setItem('desk_chat_'+workspace,cid);if(requestDraft)sessionStorage.setItem('desk_chat_pending_'+workspace,JSON.stringify(requestDraft));else sessionStorage.removeItem('desk_chat_pending_'+workspace);}catch(_){}};
 async function api(path,body){const c=new AbortController(),timeout=setTimeout(()=>c.abort(),15000);try{const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,signal:c.signal,credentials:'same-origin'});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Request unavailable');return d;}finally{clearTimeout(timeout);}}
 function active(){return state?.conversations.find(c=>c.id===cid);}
 function draw(){
  const chats=state.conversations||[];
  if(cid&&!chats.some(c=>c.id===cid))cid='';
  if(requestDraft){const found=chats.find(c=>c.turns.some(t=>t.id===requestDraft.request_id));if(found){cid=found.id;requestDraft=null;remember();}}
  const h=$('chat-history');h.replaceChildren(new Option('New conversation',''),...chats.map(c=>new Option(c.title,c.id)));h.value=cid;
  const c=active(),key=JSON.stringify(c?.turns||[]),box=$('chat-turns');
  if(key!==lastDraw){lastDraw=key;const nearBottom=box.scrollHeight-box.clientHeight-box.scrollTop<80;box.replaceChildren();
   if(!c)box.append(node('p','Start a conversation. The companions will explain what they know and what is missing.'));
   for(const t of c?.turns||[]){const article=node('article',null,'chat-turn');article.append(node('p',t.message,'chat-user'),node('h3',t.role==='both'?'Changing Woman & Fox':t.role==='fox'?'Fox':'Changing Woman'));
    article.append(node('p',t.reply||t.error||(t.status==='working'?'Preparing a reply…':t.status)),node('small',[stamp(t.at),t.provider_used,t.estimated_cost_usd!=null?'Estimated model cost $'+Number(t.estimated_cost_usd).toFixed(5):null].filter(Boolean).join(' · ')));
    if(t.sources){const details=node('details'),summary=node('summary','What this reply used');details.append(summary,node('p',t.context_note));for(const s of t.sources)details.append(node('h4',s.label+' ['+s.id+']'),node('small',stamp(s.as_of)),node('pre',JSON.stringify(s.facts||{},null,2)),node('p',s.limits));details.append(node('p','Saved-note versions: '+(t.memory_receipts?.map(m=>m.id+' v'+m.version).join(', ')||'none')));article.append(details);}if(t.note)article.append(node('p',t.note));box.append(article);
   }if(nearBottom)box.scrollTop=box.scrollHeight;
  }
  const working=!!c?.turns.some(t=>t.status==='working');$('chat-send').disabled=working||sending;$('chat-stop').disabled=!working;$('chat-forget').disabled=!c;
  $('chat-retention').textContent=state.policy;$('chat-export').href=base+'/export'+query;
  const memories=$('chat-memories');memories.replaceChildren(node('h3','Saved notes · your statements, not proven trading rules'));
  if(!state.memories.length)memories.append(node('p','No long-term conversation notes saved.'));
  for(const m of state.memories){const a=node('article'),editButton=node('button','Edit'),forget=node('button','Forget');editButton.type=forget.type='button';a.append(node('p',m.text),node('small','Version '+m.version+' · expires '+stamp(m.expires_at)),editButton,forget);editButton.addEventListener('click',()=>{edit=m;$('chat-memory-text').value=m.text;$('chat-memory-save').textContent='Save correction';$('chat-memory-text').focus();});forget.addEventListener('click',async()=>{try{await api(base+'/memory'+query,{action:'forget',id:m.id});if(edit?.id===m.id)resetEdit();await refresh();status('Note forgotten. Future replies will not recall it or answers based on its old version.');}catch(e){status(e.message);}});memories.append(a);}
  if(working)status(c.turns.find(t=>t.status==='working')?.provider==='ollama'?'Local AI is preparing a reply on this PC. Stopping hides its result; local generation may continue until it finishes.':'Reply is running. Stopping hides its result; an already sent provider request may still be billed.');
  $('chat-context').textContent='Context: '+document.body.dataset.deskPage+' page · '+(workspace==='paper'?'practice':'real-account')+' workspace. Saved research is dated; chat does not fetch a fresh stock quote.';
  remember();
 }
 async function refresh(){if(loading)return;loading=true;try{state=await api(base+query);draw();}catch(e){status('Notebook unavailable: '+e.message);}finally{loading=false;clearTimeout(timer);if(dialog.open)timer=setTimeout(refresh,active()?.turns.some(t=>t.status==='working')?2000:15000);}}
 function resetEdit(){edit=null;$('chat-memory-text').value='';$('chat-memory-save').textContent='Save note';}
 let researchSymbol='';window.addEventListener('desk:research-symbol',e=>{researchSymbol=e.detail?.ticker||'';});
 async function open(ticker='') {if(!dialog.open)dialog.showModal();const symbol=ticker||researchSymbol||$('company-check-ticker')?.value;if(symbol)$('chat-stock').value=symbol;if($('chat-provider').value==='ollama')loadLocalModels();await refresh();$('chat-message').focus();}
 document.querySelectorAll('[data-open-companion-chat]').forEach(b=>b.addEventListener('click',()=>open()));
 window.addEventListener('desk:chat',e=>{open(e.detail?.ticker||'');if(e.detail?.message){$('chat-message').value=e.detail.message;saveDraft();}});
 $('chat-close').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>clearTimeout(timer));
 $('chat-history').addEventListener('change',e=>{cid=e.target.value;lastDraw='';draw();});$('chat-new').addEventListener('click',()=>{cid='';lastDraw='';draw();$('chat-message').focus();});
 $('chat-forget').addEventListener('click',async()=>{if(!cid)return;try{await api(base+'/'+encodeURIComponent(cid)+'/forget'+query,{});cid='';lastDraw='';await refresh();status('Conversation forgotten. Saved notes are separate and can be forgotten above.');}catch(e){status(e.message);}});
 $('chat-stop').addEventListener('click',async()=>{if(!cid)return;try{await api(base+'/'+encodeURIComponent(cid)+'/cancel'+query,{});await refresh();status('Reply stopped. A transmitted request may still be billed.');}catch(e){status(e.message);}});
 $('chat-memory-reset').addEventListener('click',resetEdit);$('chat-memory-form').addEventListener('submit',async e=>{e.preventDefault();try{await api(base+'/memory'+query,{action:'save',text:$('chat-memory-text').value,...(edit?{id:edit.id,version:edit.version}:{})});resetEdit();await refresh();status('Note saved for future replies. It will not change any trading rule.');}catch(error){status(error.message);}});
 document.querySelectorAll('[data-chat-question]').forEach(b=>b.addEventListener('click',()=>{$('chat-message').value=b.dataset.chatQuestion;saveDraft();$('chat-message').focus();}));
 $('chat-form').addEventListener('submit',async e=>{e.preventDefault();if(sending)return;const message=$('chat-message').value.trim();if(!message)return;sending=true;$('chat-send').disabled=true;
  const body={message,role:$('chat-role').value,provider:$('chat-provider').value,local_model:$('chat-local-model').value,ticker:$('chat-stock').value.trim().toUpperCase(),page:document.body.dataset.deskPage||'overview',workspace,conversation_id:cid};
  const same=requestDraft&&JSON.stringify({...requestDraft,request_id:null})===JSON.stringify({...body,request_id:null});if(!same)requestDraft={...body,request_id:crypto.randomUUID()};remember();status('Sending message…');
  try{const d=await api(base,requestDraft);cid=d.conversation_id;requestDraft=null;remember();$('chat-message').value='';saveDraft();status('Message saved.');await refresh();}
  catch(error){status('Could not confirm the reply: '+error.message+'. Check saved conversations before retrying. An unchanged retry uses the same receipt.');}
  finally{sending=false;$('chat-send').disabled=!!active()?.turns.some(t=>t.status==='working');}
 });
 async function loadLocalModels(){try{const d=await api(base+'/local-models');$('chat-local-model').replaceChildren(new Option('Choose a local model',''),...d.models.map(m=>new Option(m.name+' · '+m.size_gb+' GB',m.name)));if(d.models.some(m=>m.name===preferences.local_model))$('chat-local-model').value=preferences.local_model;$('chat-local-status').textContent=d.note;}catch(e){$('chat-local-status').textContent='Local models unavailable: '+e.message;}}
 $('chat-provider').addEventListener('change',()=>{savePreferences();const local=$('chat-provider').value==='ollama';$('chat-local-label').hidden=!local;if(local)loadLocalModels();});$('chat-local-model').addEventListener('change',savePreferences);$('chat-role').addEventListener('change',savePreferences);
})();
