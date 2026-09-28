/* Design preferences and read-only state projection; never triggers a trading action. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const money=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{style:'currency',currency:'USD'}):'Unavailable';
  window.addEventListener('desk:state',e=>{const d=e.detail||{},b=d.broker_book||{},cfg=d.config||{};
    if(!$('overview-account-label'))return; // the overview card is only on the Overview page
    $('overview-account-label').textContent=b.paper_mode===true?'Broker paper account value':'Live account value';
    $('overview-equity').textContent=b.ok?money(b.equity):'Not connected';
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
        // One plain sentence when the number is missing, instead of "Unavailable" twice.
        const note=pnl==null?(b.ok?"Today's profit/loss is not confirmed yet, so new live trades stay blocked.":"Today's profit/loss appears once the broker connects. Until then new live trades stay blocked."):"Today's profit / loss";
        pnlEl.innerHTML=`<div class="overview-pnl-block">${pnl==null?'':`<span class="overview-pnl-signed ${cls}">${signed}</span>`}<span class="muted">${note}</span>${meter}</div>`;
      }
    }
    $('overview-paper').textContent=money(d.ledger?.equity);
    $('overview-session').textContent=d.loop?.rth_ok===true?'Market open':d.loop?.rth_ok===false?'Market closed':'Hours unknown';
    const riskKnown=b.ok===true&&b.risk_ready===true&&typeof b.day_pnl_usd==='number'&&Number.isFinite(b.day_pnl_usd);
    const live=cfg.mode==='live_manual'||cfg.mode==='auto_live',confirmed=Boolean(cfg.broker_identity);
    const next=!b.ok?['Connect the broker','Broker account data is unavailable. Review the connection diagnostics.','desk-health','Account checks']
      :!riskKnown?['Check account data','Daily P&L or account checks are unavailable. New live risk stays blocked.','desk-health','Account checks']
      :!live?['Research while live trading is off','Review evidence or practice on Paper. Your broker account is not being traded by this desk.','company-check','Browse research']
      :!confirmed?['Confirm execution account','Review the intended broker account in execution settings.','live-execution-settings','Confirm account']
      :d.loop?.rth_ok===false?['Review the session','Regular trading is closed. Review the day’s evidence and after-close findings.','nightly-review','After-close review']
      :['Review an idea','Account data is available. Each proposed order still needs current quote, account and risk checks.','opp-panel','Review ideas'];
    $('overview-next').textContent=next[0];$('overview-evidence').textContent=next[1];
    const action=$('overview-action'),target=next[2];
    if(action){action.href=($(target)?'':target==='live-execution-settings'?'/desk/settings':target==='company-check'?'/desk/research':'/desk/fox')+'#'+target;action.dataset.guideJump=target;action.textContent=next[3]+' ↗';}
  });
  const links=[...document.querySelectorAll('.page-nav a[href^="#"]')];
  const observer=new IntersectionObserver(entries=>{const visible=entries.filter(x=>x.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);if(!visible.length)return;for(const link of links){if(link.hash==='#'+visible[0].target.id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}},{rootMargin:'-5% 0px -65% 0px'});
  for(const link of links){const target=document.getElementById(link.hash.slice(1));if(target)observer.observe(target);}
})();
