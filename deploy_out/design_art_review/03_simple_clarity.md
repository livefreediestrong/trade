# Design/Art Agent 3 — Simple Mode Clarity & Butler UX

**Desk:** Tomahawk Simple @ `/workspace/daytrade-signal-desk/`  
**Lens:** Can a non-trader understand the latest call and gates? Cognitive load? Misleading copy? Button placement? Paper-only clarity?  
**Sources:** `templates/index.html`, `static/app.js` (Simple UI), `static/app.css`, `deploy_out/notes_simple_ab_live.md` (+ howto / overhaul notes for alignment)  
**Reviewed:** 2026-09-22 ~19:27 EDT  
**Code changes:** none (review only)

---

## Verdict

**Conditional pass — structure is right; two live bugs break the One Job story.**

Simple A+B delivers the right IA: sticky stage, spoken Why + expandable How, quiet Waiting empty, Settings sheet, collapsed curve/positions, paper disclaimer, howto steps that match the new chrome. A non-trader *can* follow Goal → Start → Ask me first / Auto fill → Waiting Approve/Skip, and paper-only honesty is strong.

But after the first state paint, **JS wipes `call-spoken`**, so the spoken-card layout and the CSS that hides competing gloss/story fail. Separately, the stage word tracks **fill side** (`decision`), while the loop stores research lean as `model_side` / `intended_side` with `decision: "hold"` — so the hero usually reads perpetual **Hold** even when How/Odds imply Buy. Until those two are fixed, “understand the latest call and gates” fails for the primary job.

**Score (clarity):** 6.5 / 10 structure · **4 / 10 live call truth** until P0s land.

---

## Ranked findings

### P0 — Fix before calling Simple “done”

1. **`call-spoken` class wiped on every loop paint → One Job spoken CSS dies**  
   - **Evidence:** HTML `#loop-big-action` starts as `class="call-spoken big-action hold"` (`templates/index.html` ~212). JS sets `big.className = "big-action " + sideClass` (`static/app.js` ~2256), dropping `call-spoken`.  
   - One Job rules that depend on `.call-spoken` then stop applying: spoken grid (`.call-spoken` ~4804), teal/gold word colors (~4858–4863), and **`display: none !important` on `.action-gloss` / `.butler-story`** (~4897–4898).  
   - Older Simple rules still say `body.ui-simple .big-action .action-gloss { display: block }` (~3490–3491), so after first refresh the stage can show **spoken-line + gloss + meta + outcome** again — the overcrowding One Job was meant to kill.  
   - **Fix (precise):** Preserve classes, e.g. `big.className = "call-spoken big-action " + sideClass` (or `classList` add/remove side only). Re-test that gloss/story stay hidden and the grid holds.

2. **Stage word ≠ research lean → “latest call” lies for non-traders**  
   - **Evidence:** Loop saves research as `decision: "hold"` with `model_side` / `intended_side` = raw lean (`paper_loop.py` ~1115–1118). UI picks Buy/Sell only when `event === "fill"` / `filled` (`app.js` ~2147–2156); otherwise forces `decision = "hold"`. `renderSpokenCall` then speaks “I would **Hold** …” (`~1956`) while How-body can still show Buy/Sell/Flat odds (`~1981–1983`).  
   - Howto promises: sticky stage shows latest Hold/Buy/Sell with Why — research, not an order (`howto` step 4). Live behavior trains users that the brain almost always Holds.  
   - **Fix (precise):** For Simple spoken word + Why label, prefer `model_side || intended_side || decision`, and map flat → Hold. Keep fill flash / ledger language separate (“Paper fill: Buy …”) so Buy on stage does not mean an order already placed. Mirror in `#loop-action-word` and conf-ring side.

### P1 — High cognitive load / misleading / placement

3. **“I would Buy/Sell …” reads like advice or an order**  
   - **Evidence:** Spoken template `I would <em>Buy TICK</em> — confidence medium…` (`app.js` ~1956). Fill-mode and Waiting use “places the paper trade”; masthead says PAPER ONLY; footer disclaimer is good — but the hero verb still sounds directive.  
   - **Fix:** Soften to research voice, e.g. `Research leans **Buy AAPL** (paper) — confidence medium` or `I'd Hold AAPL on paper — …`. Keep “Approve places the paper trade” only on Waiting cards.

4. **Topbar tool pile: Lucky + Old mock + Settings + How to**  
   - **Evidence:** `#btn-howto`, `#btn-feeling-lucky`, `#btn-simple-settings`, `#btn-simple-mock` sit in one chrome cluster (`index.html` ~74–77). Notes call Old mock a comparison escape hatch; for a non-trader it looks like a product action. Lucky can `POST /api/signals/generate` when session is on (`app.js` ~4468–4477) with toast “Lucky check queued” — easy to read as “it traded for me.”  
   - **Fix:** Demote **Old mock** (footer link, Advanced-only, or remove from live Simple). Keep Lucky, but label/toast: `Paper research focus: AAPL (not a trade)`. Disable or no-op generate unless user opts in; or only set focus + butler line (already the soft-fail path).

5. **News title “What’s moving the tape” + empty copy name providers**  
   - **Evidence:** Simple h2 “What’s moving the tape” (`index.html` ~436); empty sub “Finnhub / Yahoo may be offline…” (`~456`, `app.js` ~4624–4627); butler “watching the tape” (`app.js` ~172, ~248). “Tape” and vendor names are trader/ops jargon.  
   - **Fix:** Title → `What’s moving (watchlist news)` or `Headlines for your list`. Empty → `No headlines right now — sources may be offline, or the watchlist is empty.` Drop Finnhub/Yahoo from Simple empty; keep in Advanced title tooltip if needed.

6. **Settings sheet portals friction with bps jargon, weak Simple gloss**  
   - **Evidence:** Sheet intro is good (`index.html` ~674). Portaled `#paper-friction` still leads with Slip/Fee (bps) and “Presets: Cheap / Typical / Harsh” (`~270–284`). Non-traders hit gear for “alerts” and land on basis points.  
   - **Fix:** In Simple sheet only: lead with alerts; rename friction head to `Paper fill honesty (how harsh fake fills are)`; replace bps labels with `A little worse than mid` / `Cash drag per fill` and keep numbers in `title` tooltips. Or collapse friction under `<details>` default closed.

7. **Gate language still leaks RTH / Brain / Horizon / Verdict**  
   - **Evidence:** Masthead badge `RTH` (`index.html` ~30); butler “Outside regular hours — RTH-only is on” (`app.js` ~175, ~225–226); How rows Brain / Horizon / Odds / Verdict (`~1978–1986`); Why can say “gated before spending the brain” (`~1889–1893`).  
   - Non-trader path: see Hold + expand How → acronym soup.  
   - **Fix:** Simple masthead: `RTH` → `Market hours` (title: US regular session open/closed). Butler: `Market closed — checks pause until the next open (US hours).` How labels: `Brain` → `Which research brain`; `Horizon` → `Look-ahead window`; `Verdict` → `Research label` + plain PASS/WATCH/AVOID via existing `verdictGloss`. Why: avoid “gated/brain”; prefer already-good lines like “Relative volume looks quiet…”.

8. **Waiting `is-empty-quiet` hides the tray title — discoverability dip**  
   - **Evidence:** Empty Simple toggles `is-empty-quiet` (`app.js` ~2870); CSS hides `.waiting-head` and `.hint-line` (`app.css` ~4962–4963). Calm dashed empty remains — good — but howto/step language says “Waiting tray”; first-run users may not map the quiet box to “where Approve will appear.”  
   - **Fix:** Keep chrome light, but leave a one-line label inside empty: `Waiting for you — empty` (no heavy panel border). Or show title at `opacity: 0.55` without full tray chrome.

9. **Howto step 1 leaks internal host nickname “(dream)”**  
   - **Evidence:** `Open http://127.0.0.1:5056 on this PC (dream).` (`index.html` ~521). Notes elsewhere also say “dream.” Confuses first-run; looks like a typo.  
   - **Fix:** `Open http://127.0.0.1:5056 on this computer.` Align `notes_howto.md` / mock notes the same way.

### P2 — Polish / debt

10. **CSS cascade debt on gloss** — Pre–One Job `display: block` on `.action-gloss` (~3490) vs One Job hide on `.call-spoken` (~4897). Even after P0 class fix, delete or gate the old block rule so one source of truth remains.  

11. **`action-meta` + `action-outcome` still paint under spoken body** — Meta ticker·time (`app.js` ~2266–2278) is not hidden by One Job CSS; outcome butler notes can appear (`~2218–2239`). Cap stage to: word + spoken-line + Why (+ optional news match) + How `<details>`. Hide meta in Simple or fold time into How.  

12. **Butler “Master” voice** — Repeated (“Standing by, Master”, “awaits your word, Master”, “fill just kissed the ledger, Master”). Fun for the persona; exclusionary / opaque for a non-trader who never opted into butler roleplay. Soften default to “Standing by” / “1 idea waiting for you”; keep Master behind a Settings toggle if desired.  

13. **Lucky sparkle + toast stacking** — Button sparkle + toast “Feeling lucky — TICK” + 6s butler line + optional second toast “Lucky check queued” (`app.js` ~4445–4476). One channel is enough (butler line XOR toast).  

14. **Positions chip below the fold in `<aside>`** — Chip pattern is good (`#btn-pos-toggle`); in Simple layout it still lives in `.desk` aside after News/Waiting. Consider docking the chip under Day curve so “0 open” stays in the One Job scroll path.  

15. **Confidence ring `title="Confidence"` only** — Ring shows low/medium/high in-core (`#loop-action-conf-label`) but no plain gloss that high ≠ “will make money.” Add `title="How sure the research brain was — not a promise of profit"`.

---

## Keep list (do not regress)

| Keep | Why |
|------|-----|
| Sticky `#one-job-stage` + thin `#stage-goal-race` | One Job hero; header `.hero-stats` correctly hidden in Simple (`app.css` ~4945) |
| Spoken Why via `gateWhyPlain` (volume / late / low conf / abstain) | Best non-trader gate explanations already in the codebase |
| `#how-call-made` `<details>` progressive disclosure | Right pattern; only needs plainer labels |
| Waiting tray chrome only when `has-pending`; calm empty when quiet | Matches overhaul A; empty Auto-fill copy is clear |
| Fill mode beside Start + `#fill-mode-gloss` | Correct placement; paper language is honest |
| Settings gear portal for friction + alerts | Main scroll stays one job |
| Curve / Positions collapse toggles | Secondary instruments default tucked away |
| Hide `#desk-pulse-panel` + `#trade-glow-panel` in Simple | Ambient stays behind stage; no dual viz heroes |
| Masthead `PAPER ONLY` + `#simple-disclaimer` | Paper-only clarity backbone |
| Howto steps 2–7 (Simple stage, Waiting, Lucky, Settings, curve/pos) | Aligns with live A+B; fix only step 1 “(dream)” |
| Lucky pool disabled when watchlist empty | Honest empty affordance |
| News soft-degrade (no 500 toast spam) | Calm failure mode |
| `prefers-reduced-motion` + tab visibility pause (DeskMotion) | A11y / load hygiene |
| Approve modal “Confirm paper fill” + bracket gloss | Ceremony stays paper-framed |

---

## Copy / layout fixes (precise)

### Call stage
| Where | From | To |
|-------|------|----|
| `#spoken-line` (has decision) | `I would Buy AAPL — confidence medium…` | `Research leans Buy AAPL (paper) — confidence medium` |
| `#spoken-line` (idle) | keep | `Standing by — press Start when you wish` ✓ |
| Word source | `decision` (fill-biased) | Simple: `model_side \|\| intended_side \|\| decision` |
| `#loop-big-action` class | `big-action ${side}` | `call-spoken big-action ${side}` |
| Gloss / story | hide via `.call-spoken` only | Also `body.ui-simple #loop-action-gloss, #butler-story { display:none !important }` |
| `#spoken-why` prefix (optional) | bare thesis | `Why: …` when showing thesis (gates already plain) |

### Waiting
| Where | From | To |
|-------|------|----|
| Empty quiet label | head hidden entirely | Inside `#opp-empty-main` when quiet: `Waiting for you — nothing yet` |
| Auto-fill empty | keep | `Auto fill On — fills happen on their own.` ✓ |
| Card gloss | keep Approve/Skip paper framing | ✓ |

### I’m Feeling Lucky
| Where | From | To |
|-------|------|----|
| Button title | keep paper research | ✓ |
| Toast | `Feeling lucky — AAPL` / `Lucky check queued for AAPL` | `Focus → AAPL (paper research, not a trade)` only |
| Topbar | Lucky beside Old mock | Move/remove Old mock from Simple topbar |

### News
| Where | From | To |
|-------|------|----|
| Simple h2 | `What’s moving the tape` | `What’s moving` + title-ctx `(watchlist headlines)` |
| Empty sub | Finnhub / Yahoo… | `No headlines right now — sources may be offline, or the watchlist is empty.` |
| Butler when rows | keep “research only, not a tip” | ✓ |

### Settings
| Where | From | To |
|-------|------|----|
| Sheet body order | alerts then friction (DOM append order) | Append alerts first; wrap friction in `<details open>` only if user expanded last time |
| Friction title (Simple) | `Paper friction` | `Paper fill honesty` |
| Slip/Fee labels (Simple) | `Slip (bps)` / `Fee (bps)` | `Worse than mid` / `Per-fill drag` + bps in `title` |

### Howto / chrome
| Where | From | To |
|-------|------|----|
| Howto li1 | `…on this PC (dream).` | `…on this computer.` |
| `#chrome-rth` text | `RTH` | `Hours` or `Market hours` |
| Butler AH | `RTH-only is on` | `Checks pause outside US market hours` |
| Butler salutation | `…, Master` | Drop Master by default (optional Settings) |

### Layout (no product logic)
1. Restore `call-spoken` on class updates (P0).  
2. Cap visible stage text layers to ≤4 (eyebrow, spoken-line, why, how summary).  
3. Dock `#positions-panel` chip under `#equity-score-panel` in Simple CSS order if aside burial persists.  
4. Delete obsolete Simple gloss `display:block` rule once One Job hide is global.

---

## Paper-only clarity (summary)

**Strong:** masthead badge, chrome note, fill-mode gloss, Waiting Approve = paper trade, approve modal title, footer disclaimer, Lucky title attribute, News “not a tip.”  

**Weak spots:** spoken “I would Buy,” Lucky “check queued,” perpetual Hold vs Buy odds (truth), RTH/tape/bps/Master jargon. Fix those and Simple stays educational without sounding like a live ticket.

---

## Alignment check vs `notes_simple_ab_live.md` / howto

| Spec claim | Live | Notes |
|------------|------|-------|
| Sticky call stage + Why + How | Present | Broken by class wipe after paint |
| Waiting tray empty quiet | Present | Title fully removed — slightly under-labeled |
| Day curve / Positions collapse | Present | Keep |
| Pulse/glow hidden; one ambient | Present (`display:none` ~4938–4939) | Keep |
| Settings gear sheet | Present | Friction jargon P1 |
| I’m Feeling Lucky | Present | Placement + generate toast P1 |
| News “what’s moving the tape” | Present | Rename for non-traders |
| Howto mirrors A+B | Mostly | Fix “(dream)” |

---

## Suggested fix order

1. P0 className preserve `call-spoken`  
2. P0 Simple word = `model_side` / `intended_side`  
3. Soften spoken + Lucky toasts; rename News/RTH; howto dream  
4. Settings friction Soft labels; Waiting empty micro-label  
5. P2 cascade cleanup + Master toggle + chip dock  

**Re-test:** cold load Simple → Start → one research decision with Buy lean → stage says Buy (research) + Why gate OR thesis + How odds agree; gloss/story absent; Waiting empty still calm; Lucky toast paper-framed; howto has no “dream.”
