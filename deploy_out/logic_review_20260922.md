# Tomahawk educational paper desk — logic review

**Date:** 2026-09-22 (ET)  
**Scope:** Decision brain, paper-loop gates, Waiting vs Auto fill, Lucky, UI Why fidelity, news/macro  
**Method:** Source read of `paper_loop.py`, `llm_trader.py`, `app.py` (execute/ingest/approve), `macro_calendar.py`, `news_stream.py`, `static/app.js`  
**Code changes:** none (report only)

---

## 1. Conclusion (first)

**Overall soundness: mixed**

The educational paper path is largely coherent: Hold/Buy/Sell is gated before and after the brain; Gemini/Jev failures hard-hold (no silent mock fills); intent events are never treated as fills in the UI; RTH / session / max-loss / target / late / confidence / macro Ask-first behave as designed for `manual` + `auto_paper`.

Concerns that keep this from “sound”: (a) Lucky’s “never auto-Approves” claim is false when mode is `auto_paper`/`auto_live` because `/api/signals/generate` → `ingest_signal` can fill or hit the broker; (b) Simple fill-mode UI labels any non-`auto_paper` mode as “Ask me first,” including `auto_live` (Approve → broker); (c) dual confidence metrics (raw thesis vs blended signal) and observe-only advisory KILL painted as “Avoid” can mis-explain holds.

No P0 found that opens a *hidden* live path through the paper loop itself (loop only runs for `manual` | `auto_paper`). Live risk is opt-in via Advanced `auto_live` + Alpaca keys, but UX can under-warn.

---

## 2. Decision flowchart

```mermaid
flowchart TD
  A[paper_loop._tick] --> B{session_active?}
  B -->|no| Z1[skip session_inactive]
  B -->|yes| C{loop_enabled?}
  C -->|no| Z2[skip loop_disabled]
  C -->|yes| D{mode in auto_paper|manual?}
  D -->|no| Z3[skip mode_not_auto_paper]
  D -->|yes| E{rth_only and not RTH?}
  E -->|yes| Z4[skip outside_rth]
  E -->|no| F{target_hit / max_loss?}
  F -->|yes| Z5[skip target/loss]
  F -->|no| G[RR next symbol ± radar merge]
  G --> H{decision_in_flight?}
  H -->|yes| Z6[Hold intent late decision_in_flight]
  H -->|no| I[analyze_ticker]
  I --> J{entry_quality present?}
  J -->|no| Z7[Hold missing_entry_quality no LLM]
  J -->|yes| K{verdict PASS/WATCH AND not late/chasing unless allow_late AND llm_on?}
  K -->|fail| Z8[Gated before LLM flat]
  K -->|ok| L[decide_trade_thesis gemini|mock|jev]
  L --> L2{brain_router junk/AVOID?}
  L2 -->|yes| M[mock_cheap]
  L2 -->|no| N[Gemini/Jev or hard hold on error]
  M --> O[shadow observe + advisory observe]
  N --> O
  O --> P[macro risk_adjustment size / force_ask]
  P --> Q{decision_late wall?}
  Q -->|yes| Z9[Hold cancel decision_late]
  Q -->|no| R{low_confidence / soft_kill / shadow_gate?}
  R -->|yes| Z10[Hold]
  R -->|no| S{side buy/sell?}
  S -->|flat| Z11[Hold]
  S -->|yes| T[execute_loop_decision]
  T --> U{manual OR macro_force_ask_first?}
  U -->|yes| V[enqueue Waiting pending]
  U -->|no auto_paper| W[paper_fill_maker]
  W --> X[intent event + optional fill event]
  V --> X
```

### Concrete field path (one tick)

1. `PaperLoop._tick` (`paper_loop.py`) loads `cfg` via deps.  
2. Gates set `_last_skip` / emit skip events with `event` like `loop_skip_rth`.  
3. `analyze_ticker` → `analysis.verdict`, `analysis.entry_quality.label`, `analysis.price`.  
4. Pre-LLM: if verdict ∉ {PASS,WATCH} or lateness ∈ {late,chasing} (unless `allow_late`) or LLM off → `thesis.error = gate_reason`, `side=flat`.  
5. Else `trade_thesis` → `llm_trader.decide_trade_thesis` → `side`, `confidence`, `thesis`, `brain_mode`, optional `routed`/`router_reason`, `error`.  
6. Shadow → `shadow.coherent`; advisory → `policy_label`, `size_mult`.  
7. Macro → `thesis.macro_force_ask_first`, `macro_size_mult`, `macro_butler_note`.  
8. Confidence: `low_confidence` if `confidence` / probs below `min_decision_confidence(cfg)` (default **0.55**).  
9. `execute_loop_decision` may re-check verdict/late/`can_take_trade`, blend confidence on signal, then either `pending`/`queued` or `fill`.  
10. Journal: always an `event=intent` with `filled:false`, `intended_side`/`model_side`; separate `event=fill` only when filled.

---

## 3. Gate table

| Gate | Effect | Source |
|------|--------|--------|
| `session_active` | No new loop decisions / no paper fills (START required) | `paper_loop.py` ~560; `can_take_trade` `app.py` ~1733; `execute_loop_decision` ~2449 |
| `loop_enabled` | Loop decisions paused (housekeeping may still run if session on) | `paper_loop.py` ~563 |
| `mode` ∉ {`auto_paper`,`manual`} | Loop skip `mode_not_auto_paper` (incl. `auto_live`) | `paper_loop.py` ~567 |
| `rth_only` + outside RTH | Loop pause; fills blocked | `paper_loop.py` ~571–581; `can_take_trade` ~1757 |
| Daily profit target hit | Loop skip; new fills blocked (realized) | `paper_loop.py` ~585; `can_take_trade` ~1786 |
| `max_session_loss_usd` / preset loss / kill-switch loss | Loop skip + fill block (MTM where wired) | `paper_loop.py` ~601–632; `can_take_trade` ~1764–1780 |
| Empty / pruned watchlist | Skip | `paper_loop.py` ~634–660 |
| `radar_enabled` | Hot symbols merged into RR focus (no LLM on radar alone) | `paper_loop.py` ~638–657; `market_radar` |
| `decision_in_flight` | Hold/skip; bump `late_blocks` | `paper_loop.py` ~666–701 |
| Missing `entry_quality` | Hold, no LLM | `paper_loop.py` ~723–738 |
| Verdict not PASS/WATCH | Gate before LLM + abstain in execute | `paper_loop.py` ~749; `app.py` ~2390 |
| Lateness late/chasing unless `allow_late` | Gate before LLM + abstain in execute | `paper_loop.py` ~751; `app.py` ~2402 |
| `llm_enabled` / `llm_on_scan` | Gate or `_loop_trade_thesis` abstain | `paper_loop.py` ~747; `app.py` ~2563 |
| Wall-clock `decision_late` (elapsed > 0.85×interval) | Hold, cancel pending, **after** LLM may already have run | `paper_loop.py` ~704, ~767–768, ~925–933 |
| `min_decision_confidence` | Hold / no fill (`low_confidence`) | `paper_loop.py` ~892–958; `llm_trader.py` ~1027; execute also ~2439 |
| Shadow incoherent + `SHADOW_GATE=1` | Hold | `paper_loop.py` ~967–976; default gate **off** |
| Advisory soft size KILL (`ADVISORY_SOFT_SIZE=1`) | Hold (`advisory_kill`) | `paper_loop.py` ~860–871, ~960 |
| Macro hard impact / earnings today | Size cut; optional `macro_force_ask_first` → Waiting even in auto_paper | `macro_calendar.risk_adjustment`; `paper_loop.py` ~873–888; `app.py` ~2488–2533 |
| Friction (`slip_bps`/`fee_bps`) | Applied on fill P&amp;L only — **not** a skip gate | `app.py` `paper_fill` ~1977–1982 |
| Caps (cash, max position, naked short, max trades) | Abstain / capped | `can_take_trade` / `paper_fill` |
| Session stop mid-fill | Attempt `reverse_paper_fill`; else `filled_before_stop` | `paper_loop.py` ~1001–1034 |

---

## 4. UI Why fidelity table

| UI claim / surface | Actual backend field(s) | Accurate? |
|--------------------|-------------------------|-----------|
| Big word Hold on intent (not fill) | `last.event==="intent"` → decision forced Hold; fill only if `event==="fill" && filled` | **Yes** (`app.js` ~2147–2156) |
| Spoken “I would Hold/Buy/Sell” | Same `decision` as big word | **Yes** for fill vs hold; does **not** show `intended_side` when held |
| Why: confidence below floor | `low_confidence`, `confidence`, `min_decision_confidence` | **Mostly yes** (`gateWhyPlain` ~1869–1878); loop may have used raw thesis conf while execute uses blended |
| Why: late / chasing | `late`, `lateness_label`, gate thesis | **Yes** |
| Why: gated before brain | `thesis` starts with “Gated before LLM” / `error` gate codes | **Yes** |
| Why: fallback thesis text | `last.thesis` | **Yes**, but may describe a Buy the desk did not fill |
| How: Brain / Horizon / Confidence / Odds / Timing / Verdict | `brain_mode`, `horizon`/`horizon_min`, `confidence`, `min_decision_confidence`, `probs`, `lateness_label`, `verdict` | **Yes** for fields shown; does not surface `routed`/`router_reason`/`macro_force`/`shadow` |
| Gloss “I'd skip this” / Avoid | `policy_label` KILL **or** `verdict` AVOID even when advisory is observe-only | **Misleading** when SHIP/FIX path held for other reasons but `policy_label==KILL` (`app.js` ~2176–2194) |
| “Related headline” under Why | Soft match from `watchlist_news` by ticker if thesis mentions news-ish words **or** any thesis | **Display-only**; implies news drove the call — **misleading** |
| Butler blockers (session/RTH/lucky) | `session_active`, `outside_rth`, `_luckyLine` | **Yes** for session/RTH; Lucky line is UX-only |
| Simple Auto fill / Ask me first | `mode===auto_paper` vs else | **Partial**: `auto_live` painted as Ask me first (`syncFillModeToggle` ~2816–2817) |
| Lucky “paper research — not a tip” / never auto-approve | Focus + optional `POST /api/signals/generate` → `ingest_signal` | **False** under Auto fill / auto_live (see findings) |

---

## 5. Ranked findings

### P0 — live-risk / safety representation

1. **Lucky can fill (paper) or broker-submit without Approve UI**  
   - Claim: `app.js` ~4465 “never auto-approve”.  
   - Actual: `feelingLucky` → `/api/signals/generate` → `ingest_signal` (`app.py` ~4352–4368, ~2085+).  
   - If `mode==auto_paper` + `session_active` → `paper_fill` (~2144–2157).  
   - If `mode==auto_live` + session → `execute_gated_broker_or_paper` (~2171–2190).  
   - Evidence: Lucky only skips generate when session off; label still says research-only.

2. **Simple fill toggle mislabels `auto_live` as Ask me first**  
   - `syncFillModeToggle`: Ask-me-first active when `mode !== "auto_paper"` (`app.js` ~2816–2817).  
   - Approve while `auto_live` uses broker path (`app.py` ~4416–4419).  
   - User can believe Waiting/Approve is paper-only while Advanced left `auto_live` on.

### P1 — logic / fidelity bugs (edu/paper still important)

3. **Dual confidence gates disagree**  
   - Loop pre-fill: raw `thesis.confidence` (+ probs) vs `min_decision_confidence(cfg)` (`paper_loop.py` ~905–913).  
   - Execute: `build_loop_signal` blends 50% playbook + 50% LLM (`app.py` ~2312–2316), then `max(env_floor, preset.min_confidence)` (~2439–2446).  
   - Presets often have `min_confidence: 0.0`, so floor is env 0.55 — but blended vs raw can pass one gate and fail the other → confusing Why / `low_confidence` reasons.

4. **Wall-clock late still spends LLM**  
   - Deadline checked **after** `trade_thesis` (`paper_loop.py` ~765–768, ~925).  
   - Entry lateness correctly gates **before** LLM (~745–763).  
   - Slow Gemini → bill then Hold `decision_late`.

5. **Advisory KILL / AVOID painted as Avoid while often observe-only**  
   - Default `ADVISORY_SOFT_SIZE=0` → KILL does not block fills.  
   - UI still uses `policy_label===KILL` → Avoid gloss (`app.js` ~2176–2194).  
   - Users may think the desk “avoided” when the model wanted Buy and nothing blocked.

6. **“Related headline” implies news causality**  
   - News never enters `decide_trade_thesis` / loop gates (`news_stream.py` is state/UI; screener/LLM prompt forbid inventing news).  
   - Soft attach under Why (`app.js` ~1991–2003).

7. **`build_loop_signal` defaults invalid side to `buy`**  
   - `app.py` ~2305–2307. Currently shielded by execute’s early `side not in buy/sell` return, but footgun if reused.

### P2 — dead code / polish / assumptions

8. **`_pending_intents` never written with a live intent** — only cleared (`paper_loop.py` ~1186, ~1234). Cancel-intent journal path is mostly unused for resting limits (fills are sync).  

9. **`stop()` does not set `_stop`** — thread keeps sleeping/`_tick` with `_running_flag=False` (`paper_loop.py` ~413–416, ~496–510). Reused by `start()`; not a silent fill bug.  

10. **Pre-work generation check is a no-op** (`gen_pre = self._generation` then immediate compare, ~710–714).  

11. **Shadow auditor fail-open** on exception → `coherent: True` (~818–819).  

12. **Cheap router** can show `brain_mode=mock` with `routed=mock_cheap` while UI “Brain” row may not explain routing (~1443–1450 vs How panel).  

13. **Intent `status: pending` with `filled: False`** when Waiting — UI handles this; journal readers must not treat status alone as fill.

---

## 6. What news / macro actually do vs UI

### News (`news_stream.py`)

| Reality | UI implication |
|---------|----------------|
| Fetches Finnhub/Yahoo/(optional Benzinga) for watchlist; cached into `state.watchlist_news` | News panel + butler “watching headlines” |
| **Does not** gate fills, size, or brain side | “Related headline” under Why can look causal |
| LLM system: do not invent news; screener has no news features | Thesis may *mention* catalysts from model imagination — still not fed live headlines |

**Truth:** News is **display / research chrome only** for fills. Macro earnings calendar is separate (Finnhub/Yahoo earnings date), not the headline stream.

### Macro (`macro_calendar.py`)

| Reality | UI implication |
|---------|----------------|
| FRED release/obs + heuristics; earnings-today per ticker | Macro strip chips + `butler_note` |
| On hard high-impact or earnings today (cfg on): `size_mult` cut (default 0.5) and `force_ask_first` if `macro_force_ask_first` (default True) | “Macro day — size cut / Ask-me-first” |
| Soft heuristics only if `macro_use_heuristics` (default False): size cut, **no** force-ask | Soft chip copy still possible when heuristics enabled |
| Applied in loop before execute; execute enqueues Waiting when `macro_force_ask_first` even in `auto_paper` | Auto fill can still land ideas in Waiting on macro days — correct, but easy to miss in Simple copy |

**Truth:** Macro **does** influence size and Waiting vs auto-fill. News headlines **do not**.

---

## 7. Recommended fixes (short)

1. **Lucky:** Only focus (+ optional analysis/scan that always ends `pending` / never calls `ingest_signal` auto path); or pass a `research_only: true` flag that forces manual pending. Update copy.  
2. **Simple fill toggle:** Treat `auto_live` as its own state (warn / force Advanced); never show Ask-me-first as active for `auto_live`.  
3. **Unify confidence:** One metric (prefer blended signal conf) for both loop gate and execute; persist that value on intent for Why.  
4. **Why / Avoid:** Map Avoid gloss to research `verdict===AVOID` or actual hold `error`, not observe-only `policy_label`. Surface `intended_side` when Hold.  
5. **News under Why:** Label “Headline for this ticker (not used in the call)” or only show when thesis cites a matched title.  
6. **Late wall:** Check elapsed / soft deadline before paid brain, or budget a shorter timeout when near deadline.  
7. **How panel:** Add `routed`/`router_reason`, `macro_force_ask_first`, shadow coherent badge.  
8. **Footgun:** In `build_loop_signal`, refuse non buy/sell instead of defaulting to buy.

---

## Appendix — Brain path summary

| Mode | Behavior |
|------|----------|
| `gemini` | Optional `BRAIN_ROUTER` → `mock_cheap` on AVOID or weak vol+extreme range; else Gemini JSON; error → hard hold `abstain` |
| `mock` | Heuristic momentum; no API |
| `jev` | Typesafe; error → hard hold |
| Shadow | Default on, observe; `SHADOW_GATE` default off |
| Advisory | Default on, observe; soft size opt-in |

**Abstain/AVOID:** Playbook AVOID gates before LLM in the loop. Brain may still return flat/hold. UI “Avoid” should mean research skip, not necessarily filled-side.

