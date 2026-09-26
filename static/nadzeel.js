/* Design preferences and read-only state projection; never triggers a trading action. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const money=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{style:'currency',currency:'USD'}):'Unavailable';
  window.addEventListener('desk:state',e=>{const d=e.detail||{},b=d.broker_book||{},cfg=d.config||{};
    if(!$('overview-account-label'))return; // the overview card is only on the Overview page
    $('overview-account-label').textContent=b.paper_mode===true?'Broker paper account value':'Live account value';
    $('overview-equity').textContent=b.ok?money(b.equity):'Unavailable';
    {
      const pnlEl=$('overview-pnl');
      if(pnlEl){
        const pnl=b.ok&&typeof b.day_pnl_usd==='number'&&Number.isFinite(b.day_pnl_usd)?b.day_pnl_usd:null;
        const ks=cfg.kill_switch||cfg.live_agent?.policy||{};
        const lossCap=Number(ks.max_daily_loss_usd??ks.max_daily_loss);
        const lossKnown=Number.isFinite(lossCap)&&lossCap>0;
        let signed='Unavailable';
        let cls='is-unknown';
        if(pnl!=null){
          const abs=Math.abs(pnl).toLocaleString('en-US',{style:'currency',currency:'USD'});
          signed=(pnl>0?'+':pnl<0?'−':'')+abs;
          cls=pnl>0?'pos':pnl<0?'neg':'';
        }
        let meter='';
        if(lossKnown){
          const used=pnl==null?0:Math.max(0,Math.min(100,Math.round((Math.max(0,-pnl)/lossCap)*100)));
          const fillCls=used>=85?'is-danger':used>=60?'is-warn':'';
          const label=pnl==null
            ?`Loss budget ${money(lossCap)} · day P&L unavailable`
            :`Loss budget ${used}% of ${money(lossCap)} used`;
          meter=`<div class="overview-loss-meter"><div class="overview-loss-track" aria-hidden="true"><div class="overview-loss-fill ${fillCls}" style="width:${used}%"></div></div><span class="overview-loss-label">${label}</span></div>`;
        }
        const note=pnl==null?'Daily P&L unavailable · new live risk blocked':'Daily P&L';
        pnlEl.innerHTML=`<div class="overview-pnl-block"><span class="overview-pnl-signed ${cls}">${signed}</span><span class="muted">${note}</span>${meter}</div>`;
      }
    }
    $('overview-paper').textContent=money(d.ledger?.equity);
    $('overview-session').textContent=d.loop?.rth_ok===true?'Market open':d.loop?.rth_ok===false?'Market closed':'Hours unknown';
    const confirmed=Boolean(cfg.broker_identity);
    $('overview-next').textContent=!b.ok?'Connect the broker':!b.risk_ready?'Check account data':!confirmed?'Confirm execution account':'Review an idea';
    $('overview-evidence').textContent=!b.risk_ready?'Live entries blocked until account risk data is verified.':!confirmed?'Broker data is connected. Confirm the intended account in execution settings.':`Mode: ${String(cfg.mode||'unknown').replaceAll('_',' ')}. Order checks still apply.`;
    const action=$('overview-action'),target=!b.ok||!b.risk_ready?'desk-health':!confirmed?'live-execution-settings':'opp-panel';
    if(action){action.href=($(target)?'':target==='live-execution-settings'?'/desk/settings':'/desk/auto')+'#'+target;action.dataset.guideJump=target;action.textContent=target==='opp-panel'?'Review ideas ↗':target==='live-execution-settings'?'Confirm account ↗':'Account checks ↗';}
  });
  const links=[...document.querySelectorAll('.page-nav a[href^="#"]')];
  const observer=new IntersectionObserver(entries=>{const visible=entries.filter(x=>x.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);if(!visible.length)return;for(const link of links){if(link.hash==='#'+visible[0].target.id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}},{rootMargin:'-5% 0px -65% 0px'});
  for(const link of links){const target=document.getElementById(link.hash.slice(1));if(target)observer.observe(target);}
})();
