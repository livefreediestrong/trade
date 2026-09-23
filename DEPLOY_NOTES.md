# Tomahawk deploy notes — deployment and safety notes

Research desk with local paper default + optional Alpaca (`ALPACA_PAPER` defaults true). No secrets in this file. No git push from this change set.

> **Current contract:** this file contains historical implementation notes. For current
> broker behavior, use `README.md` and the code. In particular, broker submission
> failure is not converted into a local paper fill.
> Risk preset policy is maintained in `risk_policy.py`.
Folder name unchanged (`daytrade-signal-desk`). Parent deploys (do not CopyFromBox from this agent).

## Design themes deferred; reverted to chill slate (2026-09-22)

Pendleton / Wool craft / yarn felt theme system stripped. Desk restored to original chill slate/teal look. Simple vs Advanced IA, desk pulse, equity curve, trade glow, radar, and butler copy kept — without wool materials, theme picker, vibe-craft, felt badges, or yarn FX.

### What changed
- Body: `ui-simple vibe-chill` (no `theme-craft` / `vibe-craft` / `data-desk-theme`).
- Theme picker removed from topbar; `localStorage.desk_theme` / `wool_vibe` cleared on boot.
- `static/wool_vibes.js` deleted; WoolVibes calls stripped from `app.js`.
- `app.css`: craft-family / felt nap / stitch / wool-vibe-picker / punch yarn blocks removed; `:root` chill palette is sole look for both modes. Call stage = clean slate card; toolbar not beige felt.
- Canvas FX (`desk_pulse`, `trade_glow`, `equity_scoreboard`): teal/sage/coral/gold only; no `--wool-*` / craft-family dependency.
- API pack files (`polygon_*`, etc.) untouched.

### Files
`static/app.css`, `static/app.js`, `static/desk_pulse.js`, `static/trade_glow.js`, `static/equity_scoreboard.js`, `templates/index.html`, `DEPLOY_NOTES.md` (deleted `static/wool_vibes.js`)

### Manifest
`deploy_out/copy_manifest_chill_revert.json`

### Verify
```
node --check static/desk_pulse.js static/trade_glow.js static/equity_scoreboard.js static/app.js
```
Hard-refresh after deploy (static cache max-age=120).

---

## Changed files

- `app.py` — session_active gate on fills; dead-code sweep (`_session_loss_limit_usd` removed, size-cut helpers merged, citations thin-wrap); open-MTM max-loss/kill; short cover + equity MTM liability; naked-short ban; flatten `bypass_gates`; approve atomic claim; evaluate pool 3–5 / 18s / 90s deadline + `equity_loop_symbols` sanitize + `llm_on_scan`; GF normalize (BTC/USD pairs, AMEX/OTCMKTS, BRK.B→Yahoo); corrupt JSON backup+fail-closed; lock discipline (short snapshot; expire/DecisionRing outside `_lock`); state_lite cache publish (no watchlist field); `_today_str` America/New_York; pace outside after 16:00/weekends; reverse_paper_fill for STOP honesty; refuse no-real-quote fills.
- `paper_loop.py` — STOP/fill honesty (check generation before fill; reverse or keep `filled=True`; never remapped-abstain after commit); gate-then-LLM; missing `entry_quality` abstain; session MTM loss; DecisionRing corrupt backup.
- `screener_logic.py` — halt/gap recompute after Finnhub quote.
- `data_sources.py` — Yahoo v7 quote fallback via v8 chart; `yahoo_symbol` BRK.B→BRK-B in chart/quote; META L denylist fix; UA env.
- `buzz_sources.py` — `wsb_daily` before `wsb_post_day`; OAuth fail→public fallback + `reddit_degraded` demotes WSB heat; paste TTL 30m; ticker slang len≥2.
- `llm_trader.py` — shared Gemini RPM/daily budget (`GEMINI_RPM` / `GEMINI_DAILY`).
- `static/app.js` — prior ship fixes + batch opp toasts / event-set dedupe / poll-error toast cap / goal·max-loss high-pri; alert baseline uses `alertBaselineReady`.
- `templates/index.html` — Heat/Opportunities research-only copy; Streaming title; duplicate STOP hidden; pace informational.
- `.env.example` — UA + Gemini budget placeholders.
- `static/app.css` — simple loop-feed 6-col alignment (hide ms/Mode cells).
- `DEPLOY_NOTES.md` — this file (deferred leftovers closed).

## P0 checklist

| # | Finding | Status |
|---|---------|--------|
| 1 | STOP/fill honesty | **Fixed** — pre-fill generation check; reverse or honest `filled=True` |
| 2 | session_active gate | **Fixed** — can_take_trade / paper_fill / auto_paper; UI chip |
| 3 | Short equity | **Fixed** — cover short on buy; equity = cash+long−short; naked short banned |
| 4 | Flatten bypass | **Fixed** — `bypass_gates=True` (still paper-only) |
| 5 | Locks | **Fixed** — short snapshot; expire/DecisionRing off hot lock; state_lite publish |
| 6 | state_lite watchlist blank | **Fixed** — no watchlist on lite; JS Array.isArray guard |
| 7 | Evaluate parallelize | **Fixed** — pool 4, ~18s, 90s deadline, llm_on_scan, sanitize |
| 8 | GF normalize | **Fixed** — BTC/USD pair, BRK.B→BRK-B, AMEX/OTC strip, drop .DJI/2353/29M |

## P1 checklist

| Finding | Status |
|---------|--------|
| Max loss / kill consider open MTM | **Fixed** |
| Refuse demo/hash prices for fills | **Fixed** (`no_real_quote` abstain) |
| Gate-then-LLM; shared RPM/daily; llm_on_scan | **Fixed** |
| Halt/gap after Finnhub; conf==persisted; missing entry_quality; size_mult allow_late | **Fixed** |
| SSE reconnect / pagehide / since_seq / alerts / heat / positions | **Fixed** |
| Pace outside after 16:00/weekends; `_today_str` ET | **Fixed** |
| Simple UI: feed align (pre-existing grid); heat research copy; one STOP; setUiMode | **Fixed** |
| Approve atomic claim | **Fixed** |
| Corrupt JSON backup + fail closed | **Fixed** |
| Yahoo v7 + BRK.B chart remap | **Fixed** |
| Buzz megathread / OAuth degraded / paste TTL / denylist | **Fixed** |

## P2 checklist

| Finding | Status |
|---------|--------|
| Live→Streaming updates | **Fixed** |
| Heat/Opportunities research-only copy | **Fixed** |
| Pace informational | **Fixed** |
| Batch toasts | **Fixed** — opp alerts batched; event-set dedupe; poll error toast cap; goal/max-loss high-pri |
| Dead code removal | **Fixed** — dropped `_session_loss_limit_usd`, merged `_size_multiplier`→`_suggested_size_cut`, slimmed `_screener_citations` |
| DEPLOY_NOTES honesty | **Fixed** (this file) |
| .env.example UA | **Fixed** |
| META L denylist | **Fixed** → `METAL` |

## Residuals

- Heat / spark / opportunities stay visible in Simple (lite: fewer heat chips / 1 spark) — intentional, not adv-only.
- Simple loop-feed columns aligned (6-col; ms/Mode hidden to match head).
- Yahoo/Finnhub still best-effort; no exchange halt feed.
- Force flatten uses last price; if quote missing and avg used, tagged allow_demo for flatten only.
- Naked shorts banned (sell closes longs only) — intentional paper honesty.
- `reddit_degraded` demotes WSB weights when OAuth fails; Stocktwits still best-effort.
- Evaluate daemon threads may finish after client timeout (results discarded once deadline fires).
- Manual approve also requires `session_active` (START) — mode alone insufficient.
- Parent must CopyFromBox + restart desk process for changes to load.

## Verify (box)

```text
python -m py_compile app.py paper_loop.py buzz_sources.py data_sources.py llm_trader.py screener_logic.py
node --check static/app.js
python -c "from app import parse_watchlist, yahoo_symbol, sanitize_watchlist; from buzz_sources import _classify_megathread; assert parse_watchlist('BTC / USD')==['BTC / USD']; assert yahoo_symbol('BRK.B')=='BRK-B'; assert _classify_megathread('Daily Discussion Thread for September 22, 2026')=='wsb_daily'"
```


## Jev steals ship (P0–P2) — paper only

### Features
1. **Late / hold** — if brain misses loop deadline or a tick arrives while a decision is in flight → `hold` + `late: true`, no fill; `late_blocks` in session totals; cancels pending intent for that symbol.
2. **Intent vs fill** — feed/SSE `intent` (status pending/held/late) separate from `fill` (`simulated: true`). UI badges: intent / fill; decision rows are never treated as fills.
3. **Model-cost vs P&L** — Advanced ledger: Brain $ · Paper P&L $ · Friction $ · Late N. Persisted on `state` / `state_lite` as `ledger_brain` + `session_totals`.
4. **Shadow auditor** — `SHADOW_AUDITOR=1` (default): coherent/incoherent badge + journal. `SHADOW_GATE=0` (default) does not block fills.
5. **One decision in flight** — trade brain serial; overlapping tick → late/hold skip.
6. **Capped-side honesty** — risk/size blocks set `capped: true`, keep model probs, hold (never pretend blocked side filled).
7. **Horizon question** — LLM/Jev ask higher/lower/flat over `DECISION_HORIZON_MIN` (default 20).
8. **Brain toggle** — Advanced select: gemini | mock | jev. Persist `brain_mode` in config. Mock = momentum heuristic, no API.
9. **Per-tick micro-metrics** — Advanced feed: latencyMs, mid, spread bps / bid-ask.
10. **Friction** — per-fill slip + `fee_bps` → `friction_usd` session total.
11. **Cancel-on-error / stale intent** — late/error clears pending intent + expires pending queue signals for that ticker.
12. **Optional Jev** — `TYPESAFE_AI_API_KEY` + `brain_mode=jev` → POST `https://api.typesafe.ai/v1/systemone` model `jev-latest`. Fallback mock/gemini on error. Key not required to run.

### Env
See `.env.example`: `BRAIN_MODE`, `TYPESAFE_AI_API_KEY`, `SHADOW_AUDITOR`, `SHADOW_GATE`, `DECISION_HORIZON_MIN`, cost estimate knobs.

### Files
`paper_loop.py`, `llm_trader.py`, `app.py`, `static/app.js`, `static/app.css`, `templates/index.html`, `.env.example`, `DEPLOY_NOTES.md`

## Jev steals review fix (harsh pass)

### P0 fixed
1. **Horizon/side conflict** — `_normalize_thesis` now prefers `horizon` when JSON conflicts (higher→buy, lower→sell, flat→flat).
2. **Brain $ not resetting on START** — `ledger_brain.model_usd` is session_totals only (day rollup stays on `model_cost`).
3. **Advanced ledger stale on SSE** — `applyStateLite` merges ledger_brain/session_totals/brain_mode and calls `renderBrainLedger`.
4. **Intent row looked like a fill** — intent events always `filled:false` / `hold:true`; only `event:fill` is a fill. Big action Hold unless last event is a real fill.

### P1 fixed
5. **Fee honesty** — `fee_bps` deducted from paper cash (slip already in fill price); friction still slip+fee.
6. **No resting pending→fill race** — removed parking held/capped as pending intents (fills are sync); cancel-on-error still expires queue signals.
7. **Shadow Gemini cost** — returned as `model_cost_usd` and bumped into session `model_usd`.
8. **Cost day roll** — America/New_York aligned with session PnL day.
9. **Atomic one-in-flight claim** — check+set under one lock; skip path no longer cancels unrelated symbol intents.
10. **Brain resolve** — loop uses `resolve_brain_mode`; desk cfg does not let Gemini model id override brain_mode.
11. **SSE cancel** — dual-emitted as `decision` for UI; native intent/fill/cancel listeners added (deduped by id/seq).
12. **Event names** — missing_entry / tick errors emit `intent` (not legacy `decision`).

### Deferred P2
- One-in-flight skip rarely fires (single-threaded loop) — keep as guard.
- Shadow keyword heuristic false positives (`bid` substring etc.).
- Dual SSE emit redundant if all clients listen to native event names.

### Verify (box)
```
python -m py_compile app.py paper_loop.py llm_trader.py
node --check static/app.js
# Flask test_client: /api/health, /api/state, brain_mode mock|jev|bad
# Unit: late → no fill; capped → no fill + capped badge; horizon conflict → side from horizon
```

Manifest: `deploy_out/copy_manifest_jev_review_fix.json` (CopyFromBox not available to this agent — parent deploys).

## Jev roundup #20 ship (Prism / Router / confidence) — paper only

Patterns from “I reviewed 287 open-source Jev projects…” (esp. Prism, Codex Router, jev-trader, Canny, killmyidea). Days-old ecosystem — patterns not endorsements.

### Shipped (ranked)

1. **P0 Prism advisory panel** — multi-q scores `thesis_coherent` / `regime_ok` / `risk_ok` / `tradeable_now`. Default observe-only (`ADVISORY_PANEL=1`). Soft size opt-in (`ADVISORY_SOFT_SIZE=0`). Never flips buy↔sell. Costs roll into session `model_usd` when Gemini/Jev advisory used.
2. **P0 Confidence gate** — `MIN_DECISION_CONFIDENCE` (default 0.55). Below → `event=intent`, `error=low_confidence`, no fill. Badge on feed.
3. **P1 Cheap brain router** — `BRAIN_ROUTER=1` (default): gemini + (weak rel_vol ∧ range extreme) ∨ AVOID → `routed: mock_cheap`. Does not override `brain_mode=mock|jev`. `BRAIN_ROUTER=0` forces Gemini-only.
4. **P1 Shadow keyword FP** — word-boundary match (`bid` ∉ `forbid`); structured audit still preferred.
5. **P1 KILL/FIX/SHIP** — `policy_label` from advisory scores in code only. Simple chip; Advanced full score panel.
6. **Review** — honesty greps; py_compile + node --check.

### Env
`ADVISORY_PANEL`, `ADVISORY_SOFT_SIZE`, `ADVISORY_USE_GEMINI`, `ADVISORY_USE_JEV`, `MIN_DECISION_CONFIDENCE`, `BRAIN_ROUTER`

### Files
`llm_trader.py`, `paper_loop.py`, `app.py`, `static/app.js`, `static/app.css`, `.env.example`, `DEPLOY_NOTES.md`

Manifest: `deploy_out/copy_manifest_jev20_fix.json`

## Waiting-for-you flash fix (2026-09-22)

Root causes: lite `[]` truthy wipe; `signal_price` in opp signature; Auto fill briefly showing Approve rows.

Shipped in `static/app.js` (+ tiny HTML/CSS):
- `applyStateLite` opportunities merge with `__oppEmptyStreak` + `pending_count` hysteresis (2 empty lites before clear); full `/api/state` `render()` resets streak immediately.
- Simple + `auto_paper`: force empty Approve list; calm empty copy; count chip 0.
- `oppSignature` drops `signal_price`.
- Ask-me-first N→0 empty paint delayed ~700ms (cancel if rows return).
- `#opp-empty` / `#opp-feed` mutual exclusion + `opp-feed-wrap` min-height; Simple disables rank/churn flash anims.

Verify: `node --check static/app.js`
Manifest: `deploy_out/copy_manifest_waiting_flash.json` (parent CopyFromBox + restart).

### Brainstorm only — market status bubble (do NOT implement yet)

Product ideas for a calm “sector/industry feel” bubble that updates every N minutes (parent presents to user):

1. **One-line regime chip** near Waiting/session: “Tech soft · Energy firm · VIX calm” — 3–5 sector adjectives from breadth/rel-vol aggregates, refresh 5–15 min.
2. **Heat-linked industry whisper** — bubble text pulled from top heat tickers’ GICS/Yahoo industry, not a second heat lane (avoid duplicate chrome).
3. **Session-scoped snapshot** — freeze first reading after START; soft-update only on meaningful flip (e.g. risk-on→risk-off) so it never flickers like Waiting.
4. **Paper-honest disclaimer** — always “research feel, not a signal”; never Approve/Skip or size suggestions from the bubble.
5. **Simple vs Advanced** — Simple: one muted sentence; Advanced: expandable sector table + last-update ET clock.
6. **Update cadence** — default 10 min; pause outside RTH; show “as of HH:MM ET” to set expectations.
7. **Optional stub later** — static placeholder string in Simple header only; no new API until product sign-off.

## P0.2–P0.4 ship — equity curve · marked outcomes · paper stop/TP (2026-09-22)

Paper only. Did **not** touch `live_broker_place_order` / Alpaca hooks (parallel broker work).

### Shipped
1. **P0.2 Equity curve + day scoreboard** — server `ledger.equity_curve` appended on fill + throttled loop/SSE; Simple/Advanced panel with curve, max DD, fills, helped rate; butler one-liner under curve (teal/gold, no flicker invent).
2. **P0.3 Marked outcomes** — after intent/decision with mid, schedule horizon check (`decision_horizon_min`); stamp `helped` / `hurt` / `flat` vs intended side; Advanced feed badge; Simple butler note on latest call when outcome lands.
3. **P0.4 Paper stop / TP** — approve modal quiet fields (price or %; blank → risk preset `stop_r`/`target_r` as %); `bracket_off` checkbox; auto_paper fills get preset defaults; exit intents on position; loop tick closes on hit (`paper_stop` / `paper_take_profit`); reducing exits still allowed; flatten untouched.

### Files
`session_track.py` (new), `app.py`, `paper_loop.py`, `static/equity_scoreboard.js` (new), `static/app.js`, `static/app.css`, `templates/index.html`, `DEPLOY_NOTES.md`

### Manifest
`deploy_out/copy_manifest_p0_equity_outcomes_bracket.json` (parent CopyFromBox + restart).

### Verify (box)
```
python -m py_compile app.py paper_loop.py session_track.py
node --check static/app.js static/equity_scoreboard.js
# Unit: resolve_exit_prices defaults; classify_horizon_outcome helped/hurt/flat; DecisionRing.patch
```

### Smoke for parent
1. Start session → Day curve panel shows calm baseline + butler “Quiet so far…”.
2. Approve a pending idea with blank stop/TP → position shows SL/TP from preset; fill has exit_bracket.
3. Approve with “No stop / take-profit” → no SL/TP on position.
4. After horizon_min, Advanced log shows `outcome helped|hurt|flat`; Simple gloss may update.
5. Force flatten still closes all; max-loss gates unchanged for new risk.

### Left for P1
Alerts polish, friction UX, NL find, chart strip (not in this ship).

## P0 Alpaca honesty (2026-09-22)

### Behavior
1. **Gate before broker** — `can_take_trade` + size/loss caps run **before** any Alpaca submit (never broker-before-gate).
2. **No dual-book** — if Alpaca configured and submit succeeds → **broker-only** fill record (no local `paper_fill`). Broker failure is recorded as a failure and is not converted into a local paper fill. (The older fallback wording in this historical entry is superseded.)
3. **`ALPACA_PAPER` defaults true** (paper-api). `ALPACA_PAPER=false` → live money endpoint; UI masthead/chip/toast show **LIVE ENDPOINT** (not PAPER ONLY / Fake money).
4. **Same size/loss caps** applied to broker qty path (`_cap_shares_for_broker`).
5. **Flatten** — local paper close + Alpaca `cancel_all_orders` / `close_all_positions` when configured; if broker flatten fails → `refuse_flatten_as_complete` (not claimed complete).
6. **Removed dead `ENABLE LIVE AUTO` / `live_confirm_ok` ceremony** — optional note field only; not documented as required.
7. Toast/chip driven by `broker.paper_mode`.

### Files
`broker_alpaca.py`, `app.py` (broker/flatten/approve/auto_live), `static/app.js` (chrome/toast), `templates/index.html` (broker copy), `README.md`, `.env.example`, `DEPLOY_NOTES.md`

### Verify
```
python -m py_compile app.py broker_alpaca.py
node --check static/app.js
python smoke_alpaca_broker.py
```

### EDU note
No unlock/kill ceremony added. Do **not** reintroduce `ENABLE LIVE AUTO` unlock.

## P0 Waiting / Approve / bracket / outcome slice (2026-09-22)

Paper only. Did **not** touch `live_broker_place_order` / Alpaca paths (parallel broker work).

### Fixed
1. **Ask me first** — loop runs on `manual` + session; `execute_loop_decision` enqueues pending Waiting (Approve/Skip). `auto_paper` still auto-fills.
2. **Auto fill mode switch** — drains pending (paper approve or expire) so Waiting is not hidden over ghosts; UI only auto-hides when `pending_count === 0`.
3. **Brackets** — blank stop/TP from `fill_px` + `0.8%×stop_r` / `×target_r` (screener-aligned); ignore stale signal absolute stop/target unless user typed; `%` required for pct (bare `$24` is price).
4. **Exits + outcomes** — `check_paper_exit_intents` + `check_decision_outcomes` run whenever `session_active` (not auto_paper-only).
5. **Approve modal** — butler-plain (no Lateness/PASS/citations); stop/TP fields cleared.
6. **Latest-call gloss** — callGloss kept; horizon outcome shown separately (`#loop-action-outcome`).
7. **Waiting pulse** — one-shot `pending-flash` instead of infinite `has-pending` animation.

### Files
`paper_loop.py`, `app.py` (`execute_loop_decision` / approve / session mode drain), `session_track.py`, `static/app.js`, `static/app.css`, `templates/index.html`


## Market radar — whole-market movers (2026-09-22)

Paper research only. **No LLM on every name** — radar ranks movers; existing brain path runs only when a top-N ticker enters the loop / Waiting.

### Turn on
1. Advanced → **Market radar** → check **Watch top movers (paper)** (or `POST /api/config` `{"radar_enabled": true}`).
2. Optional: Top N (default 20), refresh ~300s.
3. Start session + Auto paper or Ask me first — hot names merge into loop focus; Waiting gets ideas via the normal loop path.

### Behavior
- Sources: Alpaca snapshots (if keys) → Yahoo day_gainers/losers/most_actives → Finnhub/Yahoo batch fallback.
- Filters: price ≥ $2, dollar volume / volume floors, junk symbols dropped.
- Simple: calm line `Market radar: watching top movers (N)`. Advanced: chip lane + controls.
- Does **not** overwrite saved watchlist. Edu / paper-only chrome; no unlock ceremony.

### Files
`market_radar.py` (new), `app.py`, `paper_loop.py`, `templates/index.html`, `static/app.js`, `static/app.css`, `.env.example`, `DEPLOY_NOTES.md`

### Manifest
`deploy_out/copy_manifest_market_radar.json`

### Verify
```
python -m py_compile market_radar.py app.py paper_loop.py
node --check static/app.js
python -c "import market_radar as m; r=m.scan_movers(top_n=5); assert r['ok'] and r['movers']"
```

## API pack (2026-09-22)

Edu paper desk — **all optional**. Missing keys degrade cleanly (no crashes, honest `not_configured`). Did **not** buy paid plans or invent secrets. Design/theme files left alone (parallel craft revert).

### Modules (new)
| Module | Env | Role |
|--------|-----|------|
| `polygon_client.py` | `POLYGON_API_KEY` | Snapshots + aggregates; radar source after Alpaca, before Yahoo |
| `macro_calendar.py` | `FRED_API_KEY` (+ Finnhub earnings) | Fed/CPI/NFP flags; earnings-day size cut / force Ask-me-first |
| `news_stream.py` | `FINNHUB_API_KEY`, optional `BENZINGA_API_KEY` | Deepen company-news + Yahoo → `watchlist_news` |
| `edgar_client.py` | `EDGAR_USER_AGENT` (email required) | Free data.sec.gov recent filings chip |
| `options_flow.py` | `QUIVER_API_KEY` / `UNUSUAL_WHALES_API_KEY` / Finnhub | Advanced-only flow; **never** auto-trade |
| `desk_alerts.py` | `ALERT_WEBHOOK_URL`, optional `TWILIO_*` + `ALERT_TWILIO=1` | Waiting / goal / kill → queue + webhook (+ Twilio stub) |
| `api_providers.py` | — | Aggregates configured map for health/state |

### Wiring
- `market_radar.py` — Alpaca → **Polygon** → Yahoo screener → Finnhub/Yahoo batch
- `paper_loop.py` — macro size into thesis; alerts on Waiting / goal / max_loss
- `app.py` — health `providers`/`api_pack`; state `watchlist_news`, `edgar`, `options_flow`, `macro`, `alerts`; routes `/api/providers`, `/api/research/*`
- `buzz_sources.py` — Stocktwits trending soft-retry + empty/403 honesty (no key)
- `.env.example` — all keys commented with signup/docs URLs
- Config flags: `macro_gates_enabled`, `macro_size_mult`, `macro_force_ask_first`, `macro_use_heuristics`

### Honesty
- Polygon free/Basic ≈ delayed + rate limits; paid for real-time
- FRED free key; release calendar soft + optional weekday heuristics (off by default)
- EDGAR free; User-Agent must include contact email
- Options flow is research context only (`auto_trade: false`)
- Twilio never sends unless `ALERT_TWILIO=1` **and** all `TWILIO_*` present
- Stocktwits public JSON — may 403 from datacenter IPs

### Live vs needs keys
| Capability | Live without keys | Needs key |
|------------|-------------------|-----------|
| Yahoo news / radar fallback / Stocktwits attempt | Yes | — |
| Polygon radar | No | `POLYGON_API_KEY` |
| FRED macro series/releases | Soft heuristics only if enabled | `FRED_API_KEY` |
| Finnhub news/earnings/options chain | No | `FINNHUB_API_KEY` |
| Benzinga news | No | `BENZINGA_API_KEY` |
| SEC EDGAR filings | No | `EDGAR_USER_AGENT` |
| Quiver / UW flow | `not_configured` | respective keys |
| Webhook alerts | Queue only | `ALERT_WEBHOOK_URL` |
| Twilio SMS | Off | `ALERT_TWILIO=1` + `TWILIO_*` |

### Verify
```
python -m py_compile polygon_client.py macro_calendar.py news_stream.py edgar_client.py options_flow.py desk_alerts.py api_providers.py market_radar.py paper_loop.py app.py buzz_sources.py
python -c "import api_providers; print(api_providers.configured_map())"
```

### Manifest
`deploy_out/copy_manifest_api_pack.json`
