/* Explicit user tickets. No activation, background submission or automatic retry. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), form=$('live-ticket-form');
  if(!form)return;
  let review=null, generation=0, previewing=false, submitting=false, snapshot=null, received=0;
  const selected=name=>form.querySelector(`input[name="${name}"]:checked`)?.value;
  const money=value=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString(undefined,{style:'currency',currency:'USD'}):'Unknown';
  const message=text=>{$('live-ticket-result').textContent=text;};
  function clear(){generation++;review=null;$('live-ticket-review').hidden=true;$('live-ticket-ack').value='';$('live-ticket-send').disabled=true;}
  function update(){
    const limit=selected('live-ticket-type')==='limit';
    $('live-ticket-limit').disabled=!limit;$('live-ticket-limit').required=limit;
    $('live-ticket-price-help').textContent=limit?'DAY limit: buy at this price or less; sell at this price or more. Unfilled orders can remain working until the session ends.':'Market: the execution price can change. There is no maximum purchase price or minimum sale price.';
    const fresh=snapshot&&Date.now()-received<25000;
    const mode=fresh&&snapshot.config?.mode==='live_manual'&&snapshot.broker?.broker==='ibkr';
    $('live-ticket-preview').disabled=previewing||submitting||!mode;
    $('live-ticket-status').textContent=!fresh?'Waiting for a fresh desk status. Composing a ticket sends no order.':!mode?'Direct tickets require IBKR and Approve each — live broker mode. This form does not change your mode.':snapshot.broker_book?.paper_mode===false?'Real-money account. Review prepares the ticket; only your final Submit sends it.':'Broker paper or unverified account. The review verifies the exact account before submission.';
    const identity=snapshot?.config?.broker_identity;
    const sameAccount=review&&identity?.account_id===review.identity.account_id&&identity?.paper_mode===review.identity.paper_mode;
    const valid=review&&Date.parse(review.expires_at)>Date.now()&&mode&&sameAccount;
    $('live-ticket-send').disabled=!valid||submitting||$('live-ticket-ack').value.trim().toUpperCase()!==review?.ticker;
    if(review&&!valid){clear();message('Review expired, status became stale, or the account/mode changed. Request a fresh review.');}
  }
  async function post(path,body){
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),45000);
    try{
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
      const data=await response.json();
      if((!response.ok||!data.ok)&&!data.pending)throw Error(data.error||'Request could not complete');
      return data;
    }finally{clearTimeout(timeout);}
  }
  function show(data){
    if(!data.signal_id||!data.review_token||!data.identity||!data.order||!data.quote)throw Error('Incomplete order review; request a fresh review');
    review=data;$('live-ticket-review').hidden=false;
    $('live-ticket-account').textContent=`${data.identity.paper_mode?'BROKER PAPER':'REAL MONEY'} · account …${String(data.identity.account_id).slice(-4)}`;
    $('live-ticket-terms').textContent=`${data.intent==='cover'?'Buy to cover':data.side==='sell'?'Sell held':'Buy'} ${data.order.shares} ${data.ticker} · ${data.order.type==='limit'?`DAY limit ${money(data.order.limit)}`:'DAY market'} · ${data.costs?.contract?.primary_exchange||'exchange unverified'}`;
    $('live-ticket-position').textContent=`Held: ${data.held_shares} shares · available to close: ${data.available_whole_shares} whole shares after working orders`;
    $('live-ticket-quote').textContent=`${money(data.quote.price)} · ${data.quote.source} · ${new Date(data.quote.market_time).toLocaleString()}`;
    $('live-ticket-notional').textContent=`${money(data.estimated_notional)} ${data.order.type==='limit'?(data.side==='sell'?'minimum sale value before fees if filled':'maximum purchase value before fees'):'at the quoted price; actual value can differ'}`;
    const cost=data.costs||{};
    $('live-ticket-fees').textContent=cost.currency==='USD'?`${money(cost.commission_estimate)} · ${cost.source||'broker estimate'}`:'Unknown in USD';
    $('live-ticket-cash').textContent=data.estimated_cash_change==null?'Unknown until fees are available':`${money(data.estimated_cash_change)} · estimate only`;
    $('live-ticket-expiry').textContent=new Date(data.expires_at).toLocaleTimeString();
    $('live-ticket-warning').textContent=cost.warning||cost.error||'Submission is not a fill. Track remaining quantity before sending another order.';
    $('live-ticket-ack').value='';update();if(!review)return;$('live-ticket-review-title').focus();
    message('Review ready. Verify the account, order and fees, then type the exact stock symbol.');
  }
  form.addEventListener('input',()=>{clear();update();message('Ticket changed. Review the new terms before submitting.');});
  form.addEventListener('change',()=>{clear();update();});
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(previewing||submitting||$('live-ticket-preview').disabled)return;
    clear();const mine=generation;previewing=true;update();message('Checking price, holdings, risk and estimated fees…');
    const body={ticker:$('live-ticket-symbol').value.trim().toUpperCase(),intent:selected('live-ticket-intent'),shares:$('live-ticket-shares').value,order:{type:selected('live-ticket-type')}};
    if(body.order.type==='limit')body.order.limit_price=$('live-ticket-limit').value;
    try{const data=await post('/api/live/ticket/review',body);if(mine===generation)show(data);}
    catch(error){if(mine===generation)message(error.message+' No order was sent.');}
    finally{previewing=false;update();}
  });
  $('live-ticket-ack').addEventListener('input',update);
  $('live-ticket-discard').addEventListener('click',async()=>{
    const ticket=review;clear();const mine=generation;$('live-ticket-preview').focus();
    if(!ticket)return;
    try{await post(`/api/signals/${encodeURIComponent(ticket.signal_id)}/reject`,{});if(mine===generation)message('Ticket discarded. No order was sent.');}
    catch(error){if(mine===generation)message('Review closed locally. The saved ticket will expire; refresh the queue to check its status.');}
  });
  $('live-ticket-send').addEventListener('click',async()=>{
    update();if(!review||submitting||$('live-ticket-send').disabled)return;
    const ticket=review,ack=$('live-ticket-ack').value.trim().toUpperCase();clear();const mine=generation;submitting=true;update();message('Submitting once. Waiting for broker evidence…');
    try{
      const result=await post(`/api/signals/${encodeURIComponent(ticket.signal_id)}/approve`,{review_token:ticket.review_token,ack_ticker:ack});
      if(mine===generation)message(result.pending?'Order is still working or awaiting reconciliation. Follow it in Broker orders before trying again.':result.fill?`Broker reports ${result.fill.shares} shares at ${money(result.fill.price)}. Check the order record for final status.`:'Request processed. Check Broker orders for the confirmed outcome.');
    }catch(error){if(mine===generation)message(`${error.message}. Check Broker orders and your broker before trying again; this ticket will not retry automatically.`);}
    finally{submitting=false;update();window.dispatchEvent(new Event('desk:refresh'));}
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-live-position]');if(!button||submitting)return;
    clear();$('live-ticket-symbol').value=button.dataset.livePosition;$('live-ticket-shares').value=String(Math.floor(Number(button.dataset.shares)));
    const intent=button.dataset.side==='short'?'cover':'sell';
    form.querySelector(`input[name="live-ticket-intent"][value="${intent}"]`).checked=true;
    form.querySelector('input[name="live-ticket-type"][value="limit"]').checked=true;
    $('live-ticket-limit').value='';update();message('Holding copied. Enter a limit price, then request a fresh position and fee review.');
    $('live-stock-ticket').scrollIntoView({block:'start'});$('live-ticket-symbol').focus();
  });
  window.addEventListener('desk:state',event=>{snapshot=event.detail;received=Date.now();update();});
  document.addEventListener('visibilitychange',update);
  let timer=setInterval(update,1000);
  window.addEventListener('pagehide',()=>{clearInterval(timer);clear();});
  window.addEventListener('pageshow',()=>{clearInterval(timer);timer=setInterval(update,1000);update();});
  update();
})();
