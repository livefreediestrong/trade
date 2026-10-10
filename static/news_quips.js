/* Changing Woman's editorial voice. Headlines remain separately attributed facts. */
(() => {
 'use strict';
 const MAX_AGE=36*3600000;
 const lines=[
  [/\b(fed|rates?|inflation|cpi|interest|fomc)\b/i,['A macro headline can change the rate story. I want the actual release, its time and any revisions.','Markets react to surprises, not just large numbers. I would compare this release with the prior reading and expectations.'],'Verify the release and event window. A headline alone cannot tell us the price reaction.'],
  [/\b(earnings|revenue|profits?|quarter|guidance|results)\b/i,['This earnings report needs the guidance and cash-flow details as well as the headline.','An earnings report card deserves its footnotes. I want to know what changed, not just whether the cover looks good.'],'Check the company release, forward guidance and report timing; then compare expectations with price and volume.'],
  [/\b(tariffs?|trade|china|talks|agreement|truce)\b/i,['Diplomacy is a developing catalyst. I separate a proposal from a signed, effective policy.','These negotiations need dates, scope and confirmation. An optimistic headline is not the final agreement.'],'Verify the official terms and affected businesses. Record what would reverse the thesis.'],
  [/\b(merger|acquisition|acquire|deal|takeover|antitrust|ftc)\b/i,['A deal headline starts a checklist: terms, financing, approvals and timing.','Corporate courtship still has paperwork. I want the price, conditions and a credible path to completion.'],'Read the filing or announcement. A deal spread includes the possibility of failure.'],
  [/\b(ai|artificial intelligence|chips?|semiconductor|technology)\b/i,['Technology has excellent publicity. I am looking for revenue, margins and evidence of demand.','A compelling product story still needs measurable adoption and costs.'],'Compare the claim with filings and customer evidence; distinguish a theme from a tested trade.'],
  [/\b(oil|energy|gas|crude|opec|electricity)\b/i,['Energy news needs a supply, demand and timing check. A dramatic headline is not a position-size model.','The energy story may matter differently to producers and consumers. I want the transmission path clear.'],'Verify the underlying data and affected exposures; do not assume every energy name reacts alike.'],
  [/\b(treasury|bonds?|yields?|debt)\b/i,['A yield move needs its maturity and catalyst. One number is not the entire curve.','Debt news deserves a check on funding costs, timing and who carries the exposure.'],'Compare the actual rate change and duration exposure before drawing a sector conclusion.'],
  [/\b(record|rally|surge|soar|jump)\b/i,['Enthusiasm is welcome; chasing is optional. I want participation, liquidity and a clear invalidation level.','A record price describes the past. It does not establish the next trade’s reward after costs.'],'Check volume, spread and downside. Passing is reasonable when the entry has moved away.']
 ];
 const serious=/\b(dead|deaths?|killed|fatal|war|attacks?|shooting|suicide|abuse|assault|victims?|disaster|earthquake|hurricane|layoffs?|laying off|job cuts|fraud|scam|bankrupt|threats?|threatens?|destroy|bombs?|bombing|missiles?|refugees|military)\b/i;
 function newsQuip(row,now=Date.now()){
  if(!row||row.fresh!==true||typeof row.title!=='string'||!row.title.trim()||typeof row.source!=='string'||!row.source.trim())return null;
  // Keep advice-column stories in the source list, but do not turn them into
  // unsolicited trading commentary. This is relevance, not an execution filter.
  if(/\b(my (husband|wife|boyfriend|girlfriend)|how (do|can|should) (i|we)|dear (abby|moneyist)|our (marriage|wedding)|my (in-laws|mother-in-law|father-in-law))\b/i.test(row.title))return null;
  const stamp=Number(row.published_ts)*1000,checked=Number(row.checked_at)*1000;
  if(!Number.isFinite(stamp)||!Number.isFinite(checked)||stamp>now||checked>now||now-stamp>MAX_AGE||now-checked>900000||/mock|demo|synthetic/i.test(row.source))return null;
  let url;try{url=new URL(row.url);if(!['https:','http:'].includes(url.protocol)||url.username||url.password)return null;}catch(_){return null;}
  const title=row.title.trim().slice(0,280),seed=Array.from(String(row.id||url.href)).reduce((n,c)=>(n+c.charCodeAt(0))%65536,0);
  const topic=lines.find(([pattern])=>pattern.test(title));
  const choices=serious.test(title)?['This deserves care, not a punchline. I will read the source, distinguish confirmed facts from speculation and avoid rushing a market conclusion.']:
   topic?.[1]||['A fresh report is a research lead. I want the original source, timing and a testable implication.','The useful question is what changed, who is exposed and what evidence could prove the idea wrong.'];
  return {id:String(row.id||url.href),title,text:choices[seed%choices.length],next:serious.test(title)?'Use verified reporting. Human consequences deserve care; commentary is not an execution signal.':topic?.[2]||'Compare an independent source, record uncertainty and test any idea after costs.',url:url.href,source:row.source,publishedAt:new Date(stamp).toISOString(),publishedTs:stamp,checkedAt:checked,label:'App-written research lens · not a trade signal'};
 }
 if(typeof module==='object'&&module.exports){module.exports={newsQuip};return;}
 window.NadzeelNews={newsQuip};
})();
