/* Design preferences and read-only state projection; never triggers a trading action. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const money=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{style:'currency',currency:'USD'}):'Unavailable';
  window.addEventListener('desk:state',e=>{const d=e.detail||{},b=d.broker_book||{},cfg=d.config||{};
    $('overview-account-label').textContent=b.paper_mode===true?'Broker paper account value':'Live account value';
    $('overview-equity').textContent=b.ok?money(b.equity):'Unavailable';
    $('overview-pnl').textContent=b.ok&&typeof b.day_pnl_usd==='number'?`Daily P&L ${money(b.day_pnl_usd)}`:'Daily P&L unavailable · new live risk blocked';
    $('overview-paper').textContent=money(d.ledger?.equity);
    $('overview-session').textContent=String($('chrome-rth')?.textContent||'US market session');
    $('overview-next').textContent=!b.ok?'Connect the broker':!b.risk_ready?'Check account data':'Review an idea';
    $('overview-evidence').textContent=!b.risk_ready?'Live entries blocked until account risk data is verified.':`Mode: ${String(cfg.mode||'unknown').replaceAll('_',' ')}. Order checks still apply.`;
    const action=$('overview-action'),target=b.ok&&b.risk_ready?'opp-panel':'desk-health';
    if(action){action.href='#'+target;action.dataset.guideJump=target;action.textContent=target==='opp-panel'?'Review ideas ↗':'Account checks ↗';}
  });
  const links=[...document.querySelectorAll('.page-nav a[href^="#"]')];
  const observer=new IntersectionObserver(entries=>{const visible=entries.filter(x=>x.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);if(!visible.length)return;for(const link of links){if(link.hash==='#'+visible[0].target.id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}},{rootMargin:'-5% 0px -65% 0px'});
  for(const link of links){const target=document.getElementById(link.hash.slice(1));if(target)observer.observe(target);}
})();
