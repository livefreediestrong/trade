# Advanced explainability (plain purpose copy)

## Goal
Bring Advanced closer to Simple’s explain-everything bar: `title-ctx` parentheticals, full `hint-line` sentences, longer `title` tooltips — without removing Advanced power features. Chill slate only.

## Files changed
- `templates/index.html` — Advanced intro, title-ctx on panels, expanded hints, clearer chrome/toolbar titles
- `static/app.js` — plain tooltips / empty states / buzz·radar·heat·spark·edge-adjacent copy; no toast spam
- `static/app.css` — Advanced `title-ctx` / `adv-intro` / prob caption / ledger caption visibility (muted, wrap OK)
- `deploy_out/notes_adv_explain.md` — this note

## Advanced intro
Under toolbar: *“Advanced shows research detail. Nothing here is a live brokerage recommendation.”* (`#adv-intro`)

## Panels touched

| Panel / chrome | Change |
|---|---|
| Mode toggle / toolbar | Adv intro; Risk title-ctx; New signal / Refresh titles; longer toolbar hint |
| Masthead badges | Clearer titles (Broker, RTH, Buzz, live channel) |
| Live / kill box | Plain broker status copy; kill-switch titles |
| Auto paper | Replaced `research` tag with title-ctx; full hint; gate/speed titles |
| Brain ledger | Caption + pill titles (cost vs paper P&L — research accounting) |
| Prob bars | Caption “(model odds for this check — research only)” |
| Sparklines | aria/title; empty + cell titles in JS |
| Paper friction | Adv-only title-ctx on Slip/Fee; gloss explains bps |
| Decision log | Caption + column `title`s (Conf/ms/Mode expanded in hover); clearer empty |
| Desk alerts | Caption title-ctx; clearer empty |
| Day curve | Tag → title-ctx (P&L, biggest drop, fills) |
| Trade glow | Tag → title-ctx (paper fill flashes) |
| Heat | title-ctx + full hint (does not change watchlist) |
| Market radar | title-ctx + full hints; control titles; JS meta/empty/tips |
| Idea board | Kept label; title-ctx “(ideas waiting for Approve or Skip)”; full hint + JS sync |
| Edge check | title-ctx “not proof of edge”; Sample size label; cell titles; hint |
| Ticker buzz | title-ctx; OAuth explained in plain words; JS meta/empty |
| Operator console | title-ctx; Force flatten title + full hint |
| Ask Gemini | summary title-ctx |
| Signal queue | heading + tab titles + hint |
| Positions / Fills | Adv title-ctx; fill empty copy |
| More / Watchlist / Find / Journal | titles + quiet hints; wl-find preserved |
| Feed badges / brain pills / broker chip | Longer `title` attrs in JS |

## Before → after (examples)
- Auto paper tag `research` → `(scheduled paper checks while Start is on)`
- Hint `Scheduled decisions + log…` → full sentence: timer checks, log ≠ to-do, odds/sparks = research
- Day curve `equity · drawdown · fills` → `(today's paper P&L, biggest drop, and fills)`
- Heat `Buzz names - does not change…` → “Shows names getting buzz attention. Research only — …”
- Radar `no LLM until…` → “The brain does not decide on a name until it enters Auto paper.”
- Idea board (no ctx) → `(ideas waiting for Approve or Skip)`
- Edge (tag only) → title-ctx + “Small-sample stats… do not prove a trading edge.”
- Buzz `via OAuth…` → “Uses a Reddit login when configured; otherwise public feeds may fail.”
- Ops (bare h2) → `(emergency paper controls)` + when to use Force flatten
- Feed `Conf` / `ms` kept short; titles now say Confidence / Latency
- Badge `LLM buy` → visible `Brain buy` + research title
- Opp empty `Ranked PASS/WATCH…` → “When research marks Pass or Watch…”

## CSS note
`.title-ctx` was already global; Advanced previously leaned on uppercase `panel-tag`s. New rules under `body.ui-advanced` keep title-ctx muted/normal-case, allow wrap, style `#adv-intro`, `.prob-caption`, and `.ledger-caption`. Session label title-ctx remains hidden in Advanced (unchanged). No theme redesign.

## Verify
- Mentally: every Advanced panel head has purpose text a non-trader can parse
- Simple explainers kept (`simple-only` title-ctx / hints intact)
- `wl-find*` preserved
- No trading/loop logic changes; no Pendleton/wool; no git commit
- `node --check static/app.js` OK

## Deploy
Parent deploys to PC (restart signal desk / hard-refresh static).
