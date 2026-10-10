/* Read-only explanations and local navigation. No trading or broker requests. */
(() => {
  'use strict';
  const descriptions={
    long_call:['Buy to open a call','Pay for the right to buy the stock at the strike price. A rising stock generally helps; time decay and implied volatility also affect its price.','To exit, sell the same call to close. The premium and fees are at risk.'],
    long_put:['Buy to open a put','Pay for the right to sell the stock at the strike price. A falling stock generally helps. Buying a put is different from short-selling shares.','To exit, sell the same put to close. The premium and fees are at risk.'],
    close:['Sell to close an option you own','Choose the existing position in the paper book. Its stock, date, call/put and strike must match exactly.','Previewing the close reverses the held position. It does not open a new short option.'],
    call_credit:['Sell a call spread to open','Sell the lower-strike call and buy a higher-strike call with the same expiration. You collect a net premium; a rising stock can hurt this position.','Closing buys back the short call and sells the protective call. The modeled maximum loss assumes both legs; real assignment can create stock exposure.'],
    put_credit:['Sell a put spread to open','Sell the higher-strike put and buy a lower-strike put with the same expiration. You collect a net premium; a falling stock can hurt this position.','Closing buys back the short put and sells the protective put. The modeled maximum loss assumes both legs; real assignment can create stock exposure.'],
    call_debit:['Buy a call spread to open','Buy a lower-strike call and sell a higher-strike call with the same expiration. Pay a net premium for a bullish position with capped modeled gain.','Close both legs together from the paper book. Check the total loss budget and fees.'],
    put_debit:['Buy a put spread to open','Buy a higher-strike put and sell a lower-strike put with the same expiration. Pay a net premium for a bearish position with capped modeled gain.','Close both legs together from the paper book. Check the total loss budget and fees.']
  };
  function liveStatus(d,age){
    const b=d?.broker_book||{};
    if(!d||age>25000)return 'Status is waiting or stale. Refresh the desk before reviewing a real order.';
    if(d.config?.mode!=='live_manual')return 'The execution mode is not live manual review. Check the mode above; this guide does not change it.';
    if(b.ok!==true||d.broker?.connected!==true||b.paper_mode!==false)return 'A connected real account has not been verified. Check account and data health.';
    if(b.risk_ready!==true||typeof b.day_pnl_usd!=='number'||!Number.isFinite(b.day_pnl_usd))return 'Account risk data is incomplete. New live risk is blocked; check account and data health.';
    if(d.loop?.rth_ok!==true||d.loop?.outside_rth===true)return 'Regular-market trading is closed or unverified. You can learn and estimate now; any order still needs current checks.';
    return 'Account data is available. Review the exact order, quote, available funds and fees; this status is not permission to trade.';
  }
  if(typeof module==='object'&&module.exports){module.exports={descriptions,liveStatus};return;}
  const $=id=>document.getElementById(id),reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const say=(target,text)=>window.dispatchEvent(new CustomEvent('moss:guide',{detail:{target,text}}));
  let snapshot=null,received=0,timer=null;
  function status(){if($('live-guide-status'))$('live-guide-status').textContent=liveStatus(snapshot,Date.now()-received);}
  window.addEventListener('desk:state',e=>{snapshot=e.detail;received=Date.now();status();});
  function watch(){clearTimeout(timer);if(!document.hidden){status();timer=setTimeout(watch,10000);}}
  document.addEventListener('visibilitychange',watch);window.addEventListener('pagehide',()=>clearTimeout(timer));window.addEventListener('pageshow',watch);watch();
  function jump(id){
    const target=$(id);if(!target)return;
    for(let parent=target;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
    target.scrollIntoView({behavior:reduced.matches?'auto':'smooth',block:'start'});target.setAttribute('tabindex','-1');target.focus({preventScroll:true});
  }
  document.addEventListener('click',e=>{
    const link=e.target.closest('[data-guide-jump]');if(!link||!$(link.dataset.guideJump))return;e.preventDefault();jump(link.dataset.guideJump);
    const section=$(link.dataset.guideJump)?.closest('#desk-options,#desk-live,#moss-desk,#desk-paper,#desk-settings,#research-studio,#desk-research')?.id||'desk-live';
    say(section,section==='desk-options'?'Start with the action: opening a contract and closing one you own are different workflows.':'This opens the relevant step. It does not submit, cancel or approve an order.');
  });
  const buySteps=$('live-guide-steps')?.innerHTML;
  for(const button of document.querySelectorAll('[data-live-intent]'))button.addEventListener('click',()=>{
    for(const other of document.querySelectorAll('[data-live-intent]'))other.setAttribute('aria-pressed',String(other===button));
    const selling=button.dataset.liveIntent==='sell';
    $('live-guide-steps').innerHTML=selling?'<li><strong>Find the holding</strong><p>Check the exact stock and owned quantity at the broker. Selling more than you own can open a short position.</p><a href="#broker-book" data-guide-jump="broker-book">Inspect broker holdings</a></li><li><strong>Prepare a sell ticket</strong><p>Use Prepare sell ticket beside the holding, or choose Sell shares I own in the stock ticket. Review whole shares, price and fees; current holdings and working orders are checked again before submission.</p><a href="#live-stock-ticket" data-guide-jump="live-stock-ticket">Open stock ticket</a></li><li><strong>Confirm the actual fill</strong><p>Check filled and remaining quantities at the broker. Broker activity may not appear immediately in this desk; do not send a duplicate because a row is missing.</p><a href="/desk/research#moss-real-trades" data-guide-jump="moss-real-trades">Review retained executions</a></li>':buySteps;
    $('live-guide-limit').textContent=selling?'Sell and cover tickets are checked against your current holding and working orders. They cannot intentionally open a new short or long position. The broker remains the source of truth.':'Use the stock ticket for your own order, or review a research idea separately. Whole shares only; attached broker stop-loss and take-profit orders are unavailable.';
    say('desk-live',selling?'Selling shares you own reduces that holding. Prepare its sell ticket, inspect the exact quantity and fees, then confirm only after review.':'First check the account, then estimate dollars and fees, then review the exact stock idea before any real order.');
  });
  const intent=$('option-intent'),strategy=$('option-strategy');
  if(!intent||!strategy)return;
  function explain(key,announce=false){
    const copy=descriptions[key]||descriptions.long_call;
    $('option-intent-title').textContent=copy[0];$('option-intent-copy').textContent=copy[1];$('option-intent-exit').textContent=copy[2];
    $('options-ticket').hidden=key==='close';
    const next=$('option-guide-continue'),target=key==='close'?'options-book':'options-ticket';
    next.href='#'+target;next.dataset.guideJump=target;next.textContent=key==='close'?'Choose a held position ↓':'Choose contract & size ↓';
    $('option-guide-next').textContent=key==='close'?'Next: find your held option below and choose Preview paper close. If the book is empty, there is nothing to close.':'Next: choose the stock, expiration and strike; then preview the total cost and modeled loss.';
    if(announce)say('desk-options',copy[0]+'. '+copy[2]);
  }
  intent.addEventListener('change',()=>{
    if(intent.value==='close'){
      // A previously reviewed opening must not remain confirmable in the close workflow.
      $('options-form').dispatchEvent(new Event('input',{bubbles:true}));explain('close',true);return;
    }
    strategy.value=intent.value==='spread'?'call_debit':intent.value;
    strategy.dispatchEvent(new Event('change',{bubbles:true}));explain(strategy.value,true);
  });
  strategy.addEventListener('change',()=>{intent.value=strategy.value.endsWith('_debit')?'spread':strategy.value;explain(strategy.value,true);});
  for(const id of ['option-symbol','option-expiry','option-long','option-short','option-count','option-budget','option-fee'])$(id)?.addEventListener('focus',()=>{
    const hints={'option-symbol':'Use the stock or ETF symbol. Load its listed expirations before choosing a contract.','option-expiry':'Expiration is when the contract ends. This simulator does not exercise or assign contracts.','option-long':'Strike is the exercise price, not the premium you pay. Match the exact contract in the review.','option-short':'This is the protective spread’s other strike. Both legs must share the same date and call/put type.','option-count':'Options use whole contracts here. Each standard contract has a 100-share multiplier.','option-budget':'This caps the modeled paper loss plus assumed round-trip fees, not the price per share.','option-fee':'This is your fee assumption per contract per leg. The review includes entry and exit fees in modeled loss.'};say('desk-options',hints[id]);
  });
  window.addEventListener('options:guide',e=>{const d=e.detail||{};if(typeof d.text==='string'){$('option-guide-next').textContent=d.text;say('desk-options',d.text);}});
  explain(strategy.value);
})();
