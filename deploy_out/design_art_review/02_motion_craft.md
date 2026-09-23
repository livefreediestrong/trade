# Design/Art Review 02 — Motion, Craft & Atmosphere

**Agent:** Design/Art Agent 2 — Motion, Craft & Atmosphere  
**Product:** Tomahawk (`/workspace/daytrade-signal-desk/`)  
**Date:** 2026-09-22 (ET)  
**Scope:** `static/desk_motion.js`, motion CSS in `static/app.css` (ambient, conf ring, goal race, flashes, arrive), Simple stage ambient, Advanced accents, reduced-motion, Lucky / news / waiting entrances, after-hours mood. Notes: `deploy_out/notes_simple_ab_live.md`.  
**Constraint:** Review only — no product code changes, no git.

---

## Verdict

**Ship-with-fixes: motion language is coherently butler-calm on the One Job stage, but Simple still runs hidden canvas rAF (pulse/glow) and a perpetual DeskMotion loop that thrash-writes unused CSS vars — performance and “calm vs busy” are not yet aligned.**

Shared DeskMotion + chill teal/gold tokens successfully pull Simple and Advanced toward one educational-butler dialect (no casino particle spam on Lucky, soft tray/news arrive, moonlit AH wash). The craft gap is not taste — it is **ghost work** (animating what the user cannot see), **idle perpetual motion** (breath/wave/hold pulse while standing by), and a few incomplete hooks (`--ambient-breath`, `data-fill-side`, Advanced `motion-arrive`) that leave the motion API looking fuller than the pixels.

Mood scorecard (intent vs current):

| Lens | Simple | Advanced | Notes |
|------|--------|----------|-------|
| Educational butler vs casino | Mostly butler | Border accents only | Lucky sparkle OK; fill wash + dual flashes risk slot-machine if conf jitters |
| Calm vs busy | Stage calm; chrome still breathes | Dense panels; AH filter soft | Infinite CSS breaths + hidden canvas contradict “one ambient” |
| Shared language | Strong tokens / ease | Weak application | Conf ring / stage ambient hidden; accents are border/filter only |
| Reduced motion | Mostly covered | Partial | DeskMotion freezes reduce at boot; CSS media queries catch most keyframes |
| After-hours | Strong (`is-ah` + `ah-moonlit`) | Soft saturate filter | Moonlit story is coherent |

---

## Ranked findings

### P0 — Fix before calling motion “done”

#### P0.1 Hidden canvas rAF still runs in Simple after One Job hide
**Evidence**
- `app.css` ~4938–4939: `body.ui-simple #desk-pulse-panel, #trade-glow-panel { display: none !important; }` (notes: “Simple hides desk-pulse + trade-glow”).
- `desk_pulse.js` ~516–538: loop continues while `isSimple() && pageVisible && !reduced` — **no check that the panel is visible**.
- `trade_glow.js` ~454–463: keeps rAF when `simpleNow && ambient.length > 0` even if shell is `display: none`.

**Why it matters:** Simple promised *one* ambient behind the stage. Users still pay for two canvas loops they cannot see, on top of DeskMotion’s rAF → laptop fan / battery / jank risk, and it undercuts the calm mood when the compositor still composites hidden canvases.

**Rec:** Gate pulse/glow `ensureLoop` / `frame` on panel visibility (e.g. `offsetParent` / `checkVisibility()` / explicit `DeskPulse.pause()` when Simple A+B hides heroes). Prefer stop + one static paint, same pattern pulse already uses for Advanced.

#### P0.2 DeskMotion rAF is perpetual style thrash (including Advanced + unused breath)
**Evidence**
- `desk_motion.js` ~37–61: every frame sets `--ambient-drift` and `--ambient-breath` on `#stage-ambient`.
- `app.css`: `--ambient-drift` used on `.stage-ambient::after` transform (~4749); **`--ambient-breath` never consumed**.
- Advanced: `.stage-ambient { display: none }` (~4732) and `.conf-ring { display: none }` (~4833), yet boot still `start()` (~205) for both modes.
- Hidden-tab path (~39–41) re-schedules rAF when `!visible()` instead of stopping — relies entirely on `visibilitychange` → `stop()`; empty frames are a footgun if start races.

**Why it matters:** Continuous `style.setProperty` + `filter: drop-shadow` opacity driven by `--ring-glow` (~4844–4846) forces layout/paint work for a ~48px grid drift the eye barely notices. Advanced pays for a motion helper whose hero surfaces are CSS-hidden.

**Rec:**
1. Drive ambient drift with a CSS `@keyframes` (18–24s linear) and kill the rAF ambient path — or run rAF only when Simple + ambient visible + `lastConf > 0` for ring glow.
2. Delete `--ambient-breath` writes until CSS uses them (e.g. `opacity: calc(0.75 + 0.15 * var(--ambient-breath))` on `::after`), or wire breath once.
3. In `tick`, if `!visible()` → `stop(); return;` (do not re-queue).
4. `start()` only when `body.ui-simple` (or when a registered target is connected + visible); Advanced can keep class toggles without rAF.

---

### P1 — Coherence, calm, a11y

#### P1.1 Idle perpetual motion fights “standing by” butler mood
**Evidence**
- Infinite CSS while Simple idle/session: `chrome-wave-drift` (~3410–3416, ~4452–4464), `simple-teal-breath` on Start/chip (~3364–3370), `simple-hold-pulse` on Hold aurora (~3563–3575), `soft-icon-breathe` on empty Waiting (~3811–3826).
- DeskMotion ambient drift runs even when `is-idle` (only opacity 0.85, ~4759).

**Why it matters:** Educational butler should feel *present*, not *twitchy*. Multiple soft loops stack into “dashboard screensaver,” especially Hold (most common state) + empty Waiting + masthead wave.

**Rec:** Cap infinite loops to **one** ambient voice in Simple: stage wash *or* chrome-wave, not both. Hold aurora: static soft glow until decision *changes*. Empty Waiting: breathe only once on empty transition, then static dashed calm (`is-empty-quiet` already good). Session teal-breath: only while `session-live`, OK if wave is off.

#### P1.2 Decision flash can stack / jitter into casino-adjacent pulses
**Evidence**
- `setConfidence` (~102–123): pulses when `|Δconf| > 0.02` or side change → `pulseDecision` → `#loop-big-action.motion-flash` + body `motion-decision-*` (~139–163).
- Called from `renderSpokenCall` / loop render on every state refresh (`app.js` ~2018–2020).
- Legacy `is-pulse` / `actionPulse` still in CSS (~3608–3610, ~2051+); call tile also has aurora blooms.

**Why it matters:** Confidence noise across polls can re-trigger 0.7s ring flashes while Hold word + gold/teal accents already shift — reads as slot reel, not butler nod.

**Rec:** Pulse only on **side** change (or conf crossing bands: low/mid/high), not ±0.02. Debounce ≥1.2s. Prefer one channel: either DeskMotion flash *or* `is-pulse`, not both. Consider flashing conf-ring opacity instead of whole tile box-shadow.

#### P1.3 Reduced-motion: DeskMotion freezes preference at boot; Advanced arrive omitted
**Evidence**
- `desk_motion.js` ~9–10: `const REDUCED = matchMedia(...).matches` once; no `change` listener (unlike `desk_pulse.js` ~611+).
- CSS `@media (prefers-reduced-motion: reduce)` at ~5166–5178 covers stage/goal/flash/arrive/lucky/fill — good.
- Does **not** list `body.ui-advanced .loop-feed-row.motion-arrive` (~5162–5163); earlier blocks cover `.loop-feed-row.is-new` (~3142+) which is what Advanced actually uses (~2583).

**Why it matters:** OS toggle mid-session leaves rAF running; notes claim “flashes/ambient drift stop” on toggle (~notes 93) — only true after reload for JS path.

**Rec:** Mirror pulse: `matchMedia(...).addEventListener("change", …)` → `stop()` / clear classes. Add Advanced `motion-arrive` (if kept) to the reduce block. Document reload vs live toggle in notes.

#### P1.4 After-hours mood is strong on stage; Advanced accents too thin / dual systems
**Evidence**
- Simple: `.stage-ambient.is-ah` desat cool wash (~4752–4757) + `body.ui-simple.ah-moonlit` (~3343–3348) + older AH badge/banner pulses (~3188–3248).
- Advanced: `body.motion-after-hours` only `filter: saturate(0.85) brightness(0.95)` on glow/pulse panels (~5158–5160).
- `setSessionMood` toggles both body classes and ambient classes (~86–99) — good API.

**Why it matters:** AH story (“moonlit desk”) lands in Simple; Advanced still feels like daytime chrome with a slight dim — shared language weakens on mode switch.

**Rec:** Reuse the same cool radial tokens (or a shared `--ah-wash`) on Advanced panel shells; slow AH calm pulse already exists — prefer that over global `filter` (filter blurs text). Ensure Simple AH does not also run `simple-teal-breath` (session-live already excludes outside — verify).

#### P1.5 Waiting vs News arrive API mismatch
**Evidence**
- Waiting: `opp-arrive` class baked in HTML string (`app.js` ~3060–3061); CSS shares `tray-arrive` with `.motion-arrive` (~4971–4977).
- News: `DeskMotion.markWaitingArrive` on first 3 rows after sig change (`app.js` ~4651–4654) — named for Waiting, used for News.
- Advanced `loop-feed-row.motion-arrive` CSS (~5162) never applied (uses `is-new`).

**Why it matters:** Entrances feel right (6px rise, 0.55–0.65s, limited to new items) but the helper API is misleading; Advanced “shared language” claim is half-wired.

**Rec:** Rename `markWaitingArrive` → `markArrive` (or add aliases). Optionally route Waiting through the helper for one timeout/cleanup path. Either wire Advanced feed to `motion-arrive` *or* delete the unused rule to avoid false confidence.

---

### P2 — Polish / craft debt

#### P2.1 Lucky sparkle taste is good; keep restraint
**Evidence:** `#btn-feeling-lucky.lucky-sparkle` → gold box-shadow pulse 0.85s (~5062–5070); JS ~180–188 / `app.js` ~4448. No particle spray, no screen shake.

**Rec:** Keep as-is. Do not add emoji/star sprites. Optional: honor `data-fill-side`-style side tint only if Lucky ever becomes tied to a call (today it should stay gold = curiosity, not Buy).

#### P2.2 `data-fill-side` set but fill wash always teal
**Evidence:** `pulseFill` sets `body.dataset.fillSide` (~173); CSS `body.motion-fill-flash::after` fixed teal radial (~4926–4930).

**Rec:** Either color wash by `[data-fill-side="buy"|"sell"]` with muted sage/coral at ≤12% opacity, or drop the dataset to avoid dead API surface. Prefer side tint — educational, not casino, if opacity stays low.

#### P2.3 Hold semantics: spoken gold vs ring teal
**Evidence:** `.call-spoken.hold .action-word { color: var(--motion-gold) }` (~4861); conf ring hold/default uses `--motion-teal` (~114–115, ~4841).

**Rec:** Hold ring track → gold at low saturation, or spoken Hold → teal. Pick one “patience” hue so ring + word agree.

#### P2.4 Conic ring `transition: background` does not interpolate
**Evidence:** `.conf-ring { transition: background 0.45s …}` (~4845) while JS replaces full `conic-gradient(...)` string (~117–118).

**Rec:** Drive angle via `--conf-deg` / `@property` or mask rotate; keep color swaps instant. Avoid fighting the browser on gradient string transitions.

#### P2.5 Dual goal-race shimmer systems
**Evidence:** Stage `.race-fill.is-shimmer` + `goal-shimmer` (~4786–4800) via DeskMotion; legacy `#race-meter.is-progressing .target-fill::after` + `race-shimmer` (~3642–3654). Hero stats hidden in Simple (~4945) but CSS + `#target-fill` width mirror remain (`app.js` ~1932–1934).

**Rec:** One race metaphor. Retire or gate legacy shimmer when `#stage-goal-race` is present. Shimmer only ≥80% / target hit — already the JS intent (~128–133); keep.

#### P2.6 Conf ring glow uses `filter: drop-shadow` driven every frame
**Evidence:** opacity + drop-shadow both depend on `--ring-glow` (~4844–4846); JS sine wave ~1.1 Hz (~56–58).

**Rec:** Prefer box-shadow on `.conf-ring-wrap` updated at 4–8 Hz max, or CSS-only `opacity` keyframes when `lastConf > 0`. Drop per-frame filter.

---

## Keep list (do not regress)

1. **Single shared helper surface** — `DeskMotion` API (`setSessionMood`, `setConfidence`, `setGoalProgress`, `pulseDecision`, `pulseFill`, `sparkle`, `markWaitingArrive`) is the right seam for Simple + Advanced.
2. **Chill slate teal/gold tokens** — `--motion-teal/gold/buy/sell` + `--ease-desk` (~4663–4668); no Pendleton/wool revival in motion.
3. **One Job stage ambient wash** — radial teal/gold/sage stack (~4734–4740); AH desat variant (~4752–4757); live/idle opacity (~4759–4760).
4. **Simple hides competing viz heroes** — pulse/glow `display: none` (~4938–4939) — correct product call; finish by stopping their loops (P0.1).
5. **Confidence ring as calm meter** — conic arc + soft mask (~4838–4846); side colors mapped buy/sell/avoid/hold.
6. **Goal race near-goal shimmer gate** — `is-shimmer` only when near/≥80% (`desk_motion.js` ~126–133).
7. **Tray / news arrive** — short translateY(6px), ~0.55–0.65s, only new/changed rows — educational, not carnival.
8. **Lucky sparkle** — single gold glow pulse; paper-disclaimer copy in Lucky line; no auto-Approve.
9. **Fill wash as brief top radial** — full-viewport but low opacity + 1.1s fade — butler acknowledgment if not stacked with flashes.
10. **Visibility pause intent** — `visibilitychange` → `stop()` / `start()` (~79–84); keep and harden (P0.2).
11. **CSS reduce media blocks** — layered reduce coverage across AH, Simple visual pack, and DeskMotion section; keep expanding rather than replacing with JS-only.
12. **Advanced session border accent** — `motion-session-live` teal border on live-loop (~5155–5156) — subtle, on-brand.
13. **Empty Waiting quiet chrome** — `is-empty-quiet` (~4955–4969) is the right calm empty; protect it from attention glow.

---

## Specific recommendations (motion / CSS / rAF)

### rAF / JS (`desk_motion.js`)
| # | Change |
|---|--------|
| 1 | Prefer CSS keyframes for ambient drift; remove perpetual rAF unless ring glow needs it. |
| 2 | If rAF kept: `if (!visible()) { stop(); return; }` — never re-queue while hidden. |
| 3 | `start()` only when Simple stage ambient (or conf ring) is visible; Advanced = class toggles only. |
| 4 | Listen for `prefers-reduced-motion` **changes**; set `DeskMotion.reduced` live. |
| 5 | `pulseDecision`: side-change only + ≥1.2s debounce; ignore 0.02 conf jitter. |
| 6 | Drop or wire `--ambient-breath`; stop writing dead vars every frame. |
| 7 | Ring glow: CSS animation class `.conf-ring.is-lit` instead of 60fps `--ring-glow`. |
| 8 | Rename `markWaitingArrive` → `markArrive`; use for news + waiting. |

### CSS (`app.css`)
| # | Change |
|---|--------|
| 9 | Pick **one** infinite Simple ambient (stage `::after` *or* `.chrome-wave`); disable the other in A+B. |
| 10 | Hold: static aurora (no `simple-hold-pulse` infinite); pulse only on class enter. |
| 11 | Empty Waiting: remove infinite `soft-icon-breathe` or run once. |
| 12 | Align Hold color: ring track ↔ `.action-word` (gold *or* teal). |
| 13 | `conf-ring`: `--conf-deg` + custom property / rotation; drop `transition: background`. |
| 14 | Fill wash: `[data-fill-side=buy|sell]` muted tints ≤0.12 opacity. |
| 15 | Reduce block (~5166): add `.loop-feed-row.motion-arrive`, `body.motion-decision-*` if any anim, sheet transform optional keep. |
| 16 | Retire dual `race-shimmer` / `goal-shimmer` — one keyframe name. |
| 17 | Avoid `filter` on whole Advanced panels for AH; tint backgrounds instead (text sharpness). |

### app.js wiring
| # | Change |
|---|--------|
| 18 | On Simple enter: `DeskPulse.stop` / `TradeGlow` idle-stop; on Advanced exit Simple: resume as today. |
| 19 | Call `markArrive` for Waiting new rows (optional) for unified cleanup timeouts. |
| 20 | Do not double-apply `lucky-sparkle` fallback if DeskMotion already did (harmless today). |

### Performance budget (target)
- Simple visible tab: **≤1** rAF owner (ideally **0** — CSS only).
- Advanced: pulse/glow canvases as today; DeskMotion **0** rAF.
- Hidden tab: **0** rAF (all helpers).
- Reduced motion: **0** perpetual animation; static AH dusk OK.

---

## Atmosphere notes (qualitative)

- **News entrances:** Correct — only after prior non-empty signature; top 3 only. Feels like a quiet tray update, not a ticker tape. Keep.
- **Waiting:** Soft `opp-arrive` for new tickers in Simple is butler-grade; Advanced rank-up/churn classes are busier (acceptable for research density) but sit outside DeskMotion dialect.
- **Lucky:** Tasteful curiosity cue; gold matches spoken eyebrow / macro chips — “interest,” not “jackpot.”
- **After-hours:** Simple stage `is-ah` is the best mood beat in the pack; propagate that cool hush to Advanced shells.
- **Casino risk residual:** Stacked flashes (decision + fill + Hold aurora + near-goal shimmer) if a fill lands near a conf flip while ≥80% goal — add a simple mutex: one DeskMotion “accent token” at a time (fill > decision > shimmer).

---

## Summary

Motion craft intent is right: chill teal/gold, one stage ambient, shared helper, reduced-motion CSS, restrained Lucky/news. **Do not ship as “desk-wide motion complete” until P0 ghost canvas/rAF and unused per-frame writes are fixed** — otherwise Simple’s calm UI is a costume over busy machinery. Then trim idle infinite loops (P1) so standing-by feels like a butler in the room, not a screensaver.
