# Simple A+B live + desk-wide motion + News

**Date:** 2026-09-22 (ET)  
**Status:** Live UI wiring — Simple One Job + butler polish; desk-wide dynamics; News; I’m Feeling Lucky.  
**Not changed:** paper loop / brain / broker logic, `data/config.json` watchlist, `brain_mode`.

## Open it

- Live desk: `http://127.0.0.1:5056/` → **Simple**
- Standalone mock (comparison): topbar **Old mock** → `/static/simple_one_job_mock.html`

## What changed (summary)

### A — One Job (Simple only layout)
1. **Sticky call stage** (`#one-job-stage`): ambient wash + thin goal race + Hold/Buy/Sell in a confidence ring + spoken butler line + Why.
2. **Waiting tray**: `#opp-panel` gets tray chrome when count > 0; `is-empty-quiet` calm empty when zero (no heavy panel chrome).
3. **Day curve**: collapsed strip by default (`#btn-curve-toggle`); expands full equity canvas/stats.
4. **One ambient** behind stage (CSS `.stage-ambient`); Simple hides desk-pulse + trade-glow panels as competing heroes. Advanced keeps pulse/glow.
5. **Positions** chip (`#btn-pos-toggle`) expands on tap.
6. **Friction + alerts** portal into Settings gear sheet (`#simple-settings-sheet`) in Simple; return under Auto paper in Advanced (`#loop-prefs-home`). Same IDs / `/api/config` wiring.

### B — Butler polish
- Teal/gold on chill slate (no Pendleton/wool).
- Header butler card (`#butler-header-status`).
- Call as spoken card (`#spoken-line` + `#spoken-why`).
- Quiet paper disclaimer (`#simple-disclaimer`).
- Fill mode gloss near Start (`#fill-mode-gloss`).

### Explanatory logic (from existing state — no new APIs)
| UI bit | Source |
|--------|--------|
| Why Hold/Buy/Sell | `loop.last_decision.thesis` / `llm_thesis`, plus plain-language gates from `error` / `reason` / `low_confidence` / `lateness_label` / `abstain` |
| Session blockers | `config.session_active`, `loop.last_skip`, `loop.rth_only` / `outside_rth` |
| How this call was made | `brain_mode`, `horizon` / `horizon_min`, `confidence` vs `min_decision_confidence`, `probs`, `verdict` |
| Waiting Approve/Skip | One-line gloss on cards + hint copy |
| Auto fill vs Ask me first | `#fill-mode-gloss` from `config.mode` |

Missing optional fields → hide / soft empty (no crash).

### I’m Feeling Lucky
- Button `#btn-feeling-lucky` (Simple only).
- Pool: watchlist, prefer liquid focus when curated intersection exists; may blend radar movers.
- Sets `focusTicker` (same path as heat chips), butler line for ~6s, sparkle via DeskMotion.
- If session active → soft `POST /api/signals/generate` with `{ticker}` (no auto-Approve).
- Disabled when watchlist empty.

### News — what’s influencing the market
- Panel `#news-panel` (Simple + Advanced).
- Data: `state.watchlist_news` (`news_stream.watchlist_news`) + optional `GET /api/research/news`.
- Macro strip from `state.macro.calendar` / `macro.risk` when present.
- Ranking prefers focus ticker, open positions, radar hot symbols.
- Simple: compact top ~5 rows; Advanced: fuller list + symbol filter + Refresh.
- Soft degrade + butler empty copy; no 500 toast spam.
- Optional related headline under Why when thesis looks news/catalyst-ish (`#spoken-news-match`).

## Desk-wide motion (`static/desk_motion.js`)

Shared rAF/CSS language for **Simple and Advanced**:

| Effect | Behavior |
|--------|----------|
| Ambient wash | CSS vars `--ambient-drift` / breath; cooler when after-hours; live vs idle classes |
| Confidence ring | Conic arc tracks conf; Hold teal / Buy green / Sell coral; pulse on change |
| Goal race | Width + shimmer near goal (≥80%) |
| Call flash | Brief tile accent on Hold/Buy/Sell flip |
| Waiting / news entrances | `motion-arrive` |
| Fill flash | Soft wash via `pulseFill` (hooked from trade-glow events) |
| Lucky sparkle | Button shimmer on click |

**Reduced motion:** `prefers-reduced-motion: reduce` → no rAF animation / no flash/shimmer classes.  
**Hidden tab:** rAF pauses on `visibilitychange`.  
Advanced keeps denser panels; motion only adds subtle border/mood accents — does not apply Simple One Job layout to Advanced.

## Click map (Simple)

1. Start / Stop — session  
2. Auto fill / Ask me first — fill mode + gloss  
3. Sticky stage — read call + Why; expand “How this call was made”  
4. I’m Feeling Lucky — random focus (+ optional one-shot generate)  
5. ⚙ Settings — friction presets + alert toggles  
6. Waiting Approve / Skip — unchanged APIs  
7. Day curve strip — expand/collapse  
8. Positions chip — expand/collapse  
9. News row — opens URL when present  
10. Old mock — standalone mock  

## How to verify

1. `node --check static/app.js` and `node --check static/desk_motion.js`
2. Open `/` in Simple: sticky stage, no desk-pulse/trade-glow panels, curve collapsed, positions chip, settings gear, Lucky, News card.
3. Start session (paper): butler status updates; goal race moves; ambient live class.
4. Switch Advanced: pulse/glow/equity/research intact; prefs back under Auto paper; News fuller.
5. Toggle reduced-motion in OS/browser — flashes/ambient drift stop.
6. Empty watchlist → Lucky disabled.
7. Confirm `data/config.json` watchlist / brain_mode untouched.

## Files touched

- `templates/index.html` — One Job stage, butler card, settings sheet, Lucky, News, howto, curve/pos toggles  
- `static/app.css` — One Job + desk-wide motion + News  
- `static/app.js` — wiring, why copy, Lucky, News, prefs portal, DeskMotion hooks  
- `static/desk_motion.js` — **new** shared motion helper  
- `deploy_out/notes_simple_ab_live.md` — this file  

Unchanged for comparison: `static/simple_one_job_mock.html`, Advanced feature set, loop/brain/broker.

## Risks / follow-ups

- News depends on Yahoo/Finnhub availability; empty state is expected without keys.  
- Lucky → `signals/generate` can be slow; soft-fail keeps focus-only behavior.  
- Sticky stage + masthead offset uses `--masthead-h`; unusual chrome heights may need a tweak.  
- Advanced decision-log row “arrive” animation is light-touch only — further viz polish optional.  
- No git commit (per request).
