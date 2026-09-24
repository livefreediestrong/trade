/* Changing Woman's editorial voice. Headlines remain separately attributed facts. */
(() => {
 'use strict';
 const MAX_AGE=36*3600000;
 const lines=[
  [/\b(fed|rates?|inflation|cpi|interest|fomc)\b/i,['A new number for the economy. Composure remains refreshingly unlisted.','The market may debate every syllable. I shall keep my breathing schedule.']],
  [/\b(earnings|revenue|profits?|quarter|guidance|results)\b/i,['A company brings its report card. I prefer reading the footnotes to applauding the cover.','Another earnings headline. Confidence has arrived; now we wait for its supporting documents.']],
  [/\b(tariffs?|trade|china|talks|agreement|truce)\b/i,['Diplomacy has entered the chat. Patience has, wisely, kept its seat.','A headline about negotiations. Even my eyebrow is waiting for the terms.']],
  [/\b(merger|acquisition|acquire|deal|takeover|antitrust|ftc)\b/i,['A deal has a headline. The fine print would also like a moment on stage.','Corporate courtship. Let us read the terms before choosing the flowers.']],
  [/\b(ai|artificial intelligence|chips?|semiconductor|technology)\b/i,['Another chapter for technology. My skepticism has installed no updates.','The future has excellent publicity. I still ask it to show its work.']],
  [/\b(oil|energy|gas|crude|opec|electricity)\b/i,['Energy in the headlines. I shall conserve mine until the details arrive.','The energy story deserves a closer look. Drama is not a unit of measurement.']],
  [/\b(treasury|bonds?|yields?|debt)\b/i,['Even bonds get dramatic headlines. I prefer my suspense in fiction.','A story about debt and yields. My composure, at least, is not on loan.']],
  [/\b(record|rally|surge|soar|jump)\b/i,['Enthusiasm is a lively guest. It still needs to leave room for evidence.','A grand headline. I shall remain elegantly unimpressed until I read the details.']]
 ];
 const serious=/\b(dead|deaths?|killed|fatal|war|attacks?|shooting|suicide|abuse|assault|victims?|disaster|earthquake|hurricane|layoffs?|laying off|job cuts|fraud|scam|bankrupt|threats?|threatens?|destroy|bombs?|bombing|missiles?|refugees|military)\b/i;
 function newsQuip(row,now=Date.now()){
  if(!row||row.fresh!==true||typeof row.title!=='string'||!row.title.trim()||typeof row.source!=='string'||!row.source.trim())return null;
  const stamp=Number(row.published_ts)*1000,checked=Number(row.checked_at)*1000;
  if(!Number.isFinite(stamp)||!Number.isFinite(checked)||stamp>now||checked>now||now-stamp>MAX_AGE||now-checked>900000||/mock|demo|synthetic/i.test(row.source))return null;
  let url;try{url=new URL(row.url);if(!['https:','http:'].includes(url.protocol)||url.username||url.password)return null;}catch(_){return null;}
  const title=row.title.trim().slice(0,280),seed=Array.from(String(row.id||url.href)).reduce((n,c)=>(n+c.charCodeAt(0))%65536,0);
  const choices=serious.test(title)?['This deserves care, not a punchline. Let us read the source before drawing conclusions.']:
   lines.find(([pattern])=>pattern.test(title))?.[1]||['A fresh headline. Let us give the facts a moment to catch up with the adjectives.','The news has arrived with confidence. I have a comfortable chair and a few questions.','A story worth reading. Certainty, as usual, has arrived before the footnotes.'];
  return {id:String(row.id||url.href),title,text:choices[seed%choices.length],url:url.href,source:row.source,publishedAt:new Date(stamp).toISOString(),publishedTs:stamp,checkedAt:checked,label:'App-written commentary · not a trade signal'};
 }
 if(typeof module==='object'&&module.exports){module.exports={newsQuip};return;}
 window.NadzeelNews={newsQuip};
})();
