# Design/Art Review 04 — Advanced Density, Research Chrome & A11y

**Agent:** Design/Art Agent 4 — Advanced Density, Research Chrome & A11y  
**Product:** Tomahawk (`/workspace/daytrade-signal-desk/`)  
**Date:** 2026-09-22 (ET)  
**Surfaces:** `templates/index.html`, `static/app.css`, `static/desk_motion.js`, `static/app.js` (portal / sheet / mode only), `deploy_out/notes_simple_ab_live.md`, prior `deploy_out/design_review/*`, `deploy_out/design_batch_a_notes.md`, art reviews 01–02  
**Constraint:** Review only — no product code changes, no git.

---

## Verdict: **conditional pass (ship-with-fixes)**

Advanced still *is* the research desk: desk pulse, trade glow, day equity, heat/radar, news filter + refresh, edge, buzz, decision log, brain ledger, and **friction + alert prefs under Auto paper** when not portaled to the Simple settings sheet. Mode isolation via `body.ui-simple` / `body.ui-advanced` + `.simple-only` / `.adv-only` is real and Batch A’s wool-bleed / butler-gate fixes hold on the chill slate lock.

What blocks a clean **strong** pass is not missing Advanced chrome — it is **a11y unfinished for a dense desk** (global focus rings, sheet dialog completeness, live-region stack) and **desk-wide motion that rediscovers competition** next to pulse + glow (viewport fill wash + AH filter + feed “arrive” that reduced-motion CSS does not fully name). Density is intentional; clutter is the fill-wash duplicating trade glow and three viz panels still reading as peers.

**Mood scorecard (Advanced)**

| Lens | Score | Notes |
|------|-------|-------|
| Research chrome present | Strong | Pulse / glow / equity / news tools / radar / buzz intact |
| Mode isolation | Strong | CSS `!important` gates + prefs portal; Simple kills pulse/glow by ID |
| Friction home under Auto paper | Strong | Portal restores to `#loop-prefs-home` on Advanced |
| Hierarchy inside Advanced | Mixed | Equity tagged primary; pulse/glow still full-width peers |
| Desk-wide motion help vs clutter | Mixed → clutter risk | Border tint OK; body fill-flash + AH `filter` compete with canvases |
| Focus / dialogs | Weak | Sheet: Escape only; inputs strip outline; howto trap exists |
| Reduced motion | Mixed | `desk_motion.js` respects PRM at boot; Advanced `loop-feed-row.motion-arrive` miss in latest reduce block |
| Contrast (chill slate) | OK | `--muted` `#8494a8` on `--panel` `#141c26` ≈ **5.5:1** (AA for small UI text) |

---

## Ranked findings

### P0 — Fix before calling Advanced “a11y ready”

#### P0.1 No global `:focus-visible` — keyboard focus dies in the densest UI

**Evidence**

- `app.css` `.wl-find-input:focus` and `.friction-field input[type="number"]:focus` set `outline: none` (≈214, ≈1291) and only swap border color.
- Sole `:focus-visible` rule: `.modal-card .btn:focus-visible` (≈1515–1517).
- Advanced surfaces that need a ring: friction slip/fee, friction presets, news `#news-filter`, mode toggle, brain-mode select, radar toggles, queue tabs, watchlist actions.

**Why it hurts**  
Advanced is a long, control-heavy page. Removing outlines without a desk-wide focus token makes Tab navigation untrustworthy exactly where density is highest. Border-color-only focus fails for low-vision and for controls whose border already matches accent.

**Recommendation**

```css
:focus { outline: none; } /* optional reset */
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
/* keep stronger inset rings for ghost buttons on busy panels if needed */
```

Do **not** rely on modal-only rings. Apply before shipping “a11y done.”

---

#### P0.2 Settings sheet is `role="dialog"` without dialog behavior

**Evidence**

- Markup: `#simple-settings-sheet` — `role="dialog" aria-modal="true" aria-labelledby="simple-settings-title"` (`index.html` ≈668).
- `openSimpleSettings` / `closeSimpleSettings` (`app.js` ≈4347–4377): toggle `hidden` + `.is-open`; **Escape** closes; backdrop click closes.
- Missing: move focus into sheet on open, focus trap, restore focus to `#btn-simple-settings`, `inert` / `aria-hidden` on the rest of the desk.
- Howto modal **does** implement return-focus + Tab cycle (`app.js` ≈4700+); approve modal returns focus — sheet is the incomplete sibling.

**Why it hurts**  
Even though the gear is Simple-primary, the same nodes (`#paper-friction`, `#desk-alert-prefs`) are Advanced’s under-Auto-paper controls. Incomplete dialog chrome trains bad patterns and can leave focus behind the backdrop if a user opens Settings then switches mode (portal moves nodes; sheet may stay `.is-open`).

**Recommendation**

1. On open: store `document.activeElement`, focus Close (or first focusable in `.sheet-body`).
2. Tab-trap like howto; Escape already OK.
3. On close: restore focus to the gear.
4. On `setUiMode("advanced")`: force `closeSimpleSettings()` before/after portal home.
5. Optional: `document.getElementById("app-root")?.inert = true` while open (or aria-hide main landmarks).

---

#### P0.3 Desk-wide fill wash + three viz heroes — motion clutters Advanced

**Evidence**

- Notes (`notes_simple_ab_live.md`): Simple hides desk-pulse + trade-glow; Advanced keeps them; motion “only adds subtle border/mood accents.”
- Reality: `body.motion-fill-flash::after` is a **fixed full-viewport** radial wash (`app.css` ≈4926–4934) hooked from trade-glow fill events via `DeskMotion.pulseFill`.
- Advanced still mounts, in order: `#equity-score-panel` (viz-primary) → `#desk-pulse-panel` (viz-secondary **adv-only**) → `#trade-glow-panel` (viz-tertiary, **no** `adv-only` class — hidden in Simple only by ID rule ≈4938–4939).
- Plus `body.motion-after-hours.ui-advanced` applies `filter: saturate(0.85) brightness(0.95)` on pulse + glow panels (≈5158–5160).

**Why it hurts**  
Trade glow’s job is “fills flash here.” A second, desk-wide wash re-introduces the old “three equal heroes + ambient spam” problem Agent 01/02 flagged for Simple — now on Advanced, where canvases already speak. AH CSS `filter` on whole panels is a heavier mood hammer than a caption.

**Recommendation**

1. **Gate `pulseFill` body wash to Simple** (`if (body.classList.contains("ui-simple"))`), *or* retarget the wash to `#trade-glow-panel` only in Advanced.
2. Keep Advanced motion accents to **border-color** on `#live-loop-panel` / equity only (already good at ≈5148–5156).
3. Prefer AH caption + token shift over panel-wide `filter`.
4. Markup: add `adv-only` to `#trade-glow-panel` so isolation matches desk-pulse (one hide path, not ID + class divergence).

---

### P1 — Density, isolation edges, a11y polish

#### P1.1 Advanced `loop-feed-row.motion-arrive` not listed in the latest reduced-motion kill list

**Evidence**

- `body.ui-advanced .loop-feed-row.motion-arrive { animation: tray-arrive … }` (≈5162–5164).
- Nearest `@media (prefers-reduced-motion: reduce)` block (≈5166–5179) kills `.opp-row.motion-arrive`, `.news-row.motion-arrive`, fill-wash, stage shimmer — **not** `.loop-feed-row.motion-arrive`.
- `desk_motion.js` correctly no-ops `markWaitingArrive` / flashes when `REDUCED` is true at boot — but CSS-only class additions or older paths can still animate; PRM is also **snapshotted once** (no `change` listener).

**Recommendation**  
Add `.loop-feed-row.motion-arrive` (and `body.ui-advanced …`) to the reduce block; optionally `matchMedia(...).addEventListener("change", …)` to flip `DeskMotion` reduced at runtime.

---

#### P1.2 Aria-live stack is loud on Advanced

**Evidence**  
≈16 `aria-live="polite"` regions in `index.html`, including several that stay visible in Advanced: paper chrome, session chip, `#loop-big-action`, `#loop-feed`, `#desk-alerts-log`, `#opp-waiting-count`, `#opp-churn`, `#opp-feed`, `#news-butler`, `#news-feed`, `#buzz-list`, `#churn-strip`, watchlist results, etc.

**Why it hurts**  
During a live session, decision log + alerts + opportunities + news can all announce. Screen-reader users get a news ticker, not a desk.

**Recommendation**

- Keep **one** primary live region for the latest call (`#loop-big-action` / status).
- Demote feeds to non-live DOM updates; expose a compact “N new decisions” polite status, or `aria-live="off"` until user focuses the panel.
- News butler line: update text only when the empty/error state changes, not every refresh.

---

#### P1.3 Friction block under Auto paper is correct — but visually heavy before research

**Evidence**

- Portal: Simple → `#simple-settings-body`; Advanced → `#loop-prefs-home` under Auto paper (`app.js` `portalPrefsToSimpleSheet`, notes A+B §A.6).
- Advanced title-ctx on slip/fee; full gloss paragraph always visible (`index.html` ≈264–285).
- Sits above decision log / desk alerts — good discoverability, bad scroll budget before equity/pulse/glow/news.

**Recommendation**  
Keep home under Auto paper (do not re-hide in Advanced). Collapse gloss behind `<details>` or a single-line hint; presets stay visible. Optional: sticky subhead “Paper honesty” so the knobs don’t feel like a second settings page.

---

#### P1.4 Research news chrome — good Advanced delta, minor density notes

**Evidence**

- Shared `#news-panel`; Advanced tools `.news-tools.adv-only` with `sr-only` label on `#news-filter` + Refresh (`index.html` ≈441–447) — **keep**.
- Simple caps rows via CSS (`nth-child` / compact rules); Advanced fuller list — **keep**.
- Macro strip + news butler both polite; butler can duplicate empty copy.

**Recommendation**  
Keep filter + refresh Advanced-only. Ensure empty state is one voice (either empty block *or* butler, not both announcing). Consider `max-height` + scroll on Advanced news-feed so it doesn’t outgrow pulse/glow.

---

#### P1.5 Mode isolation is solid — two fragile edges

**Evidence (keep / watch)**

| Mechanism | Status |
|-----------|--------|
| `body.ui-simple .adv-only` / `body.ui-advanced .simple-only` | Strong (`app.css` ≈860–861) |
| Simple ID kill `#desk-pulse-panel`, `#trade-glow-panel`, `#loop-prefs-home` | Works; trade-glow lacks `adv-only` class |
| Butler / eq-butler / radar whisper `simple-only` | Batch A intact |
| Prefs portal round-trip | Works if `home` exists |
| Boot FOUC guard for `ui_mode=advanced` | Inline script on `<body class="ui-simple">` |

**Recommendation**  
Unify trade-glow with `adv-only`. On Advanced enter, close settings sheet. Avoid adding new Simple theater inside shared nodes without an Advanced reset (stage ambient already `display:none` / stripped in Advanced — **keep**).

---

### P2 — Later / craft debt

#### P2.1 Confidence ring hidden in Advanced with no text alternative on the tile  
`body.ui-advanced .conf-ring { display: none }` while ring stays `aria-hidden="true"`. Prob bars (`.prob-bars.adv-only`) compensate — ensure conf % remains in the visible call chrome for AT (not only canvas odds).

#### P2.2 Approve modal: return-focus yes, Tab trap no  
Howto traps; approve does not. Dense Advanced users approve from Waiting — add the same trap helper.

#### P2.3 Panel `h2` still `color: var(--muted)` at ≈0.72rem  
≈5.5:1 on panel passes AA numerically; if any light vibe / future theme returns, re-apply Batch A charcoal `@0.92` ink. Chill slate: **watch**, not block.

#### P2.4 `DeskMotion` rAF still boots on Advanced  
Ambient targets are Simple-stage-heavy; Advanced pays a quiet rAF when not reduced (Agent 02). Prefer `start()` only when Simple stage ambient is visible, or when a registered Advanced accent needs it.

#### P2.5 Decision-log “arrive” is light-touch — optional viz polish only  
Notes already mark this as optional; do not add more motion language until P0.3 is scoped.

---

## Keep list (do not regress)

1. **Advanced research stack present:** desk pulse, trade glow, equity scoreboard, heat, radar, news (filter + refresh), edge, buzz, decision log, brain ledger, spark strip, ops/chat/queue.
2. **Friction + desk alerts under Auto paper** when Advanced (`#loop-prefs-home`); same IDs / `/api/config` as Simple sheet portal.
3. **`.simple-only` / `.adv-only` + body mode classes** as the single hide path (Batch A content-visibility removal — keep).
4. **Simple kills competing viz heroes** (`#desk-pulse-panel` / `#trade-glow-panel` display none) while Advanced retains them.
5. **News Advanced tools** with `sr-only` on the symbol filter; Simple compact row cap.
6. **Butler / spoken Why / Feeling Lucky / settings gear** gated Simple-only — no Advanced butler triplication.
7. **Equity always expanded in Advanced** (curve-toggle / pos-toggle forced open via CSS) — correct research posture.
8. **`desk_motion.js` PRM + `visibilitychange` pause** — keep; extend coverage rather than remove.
9. **Howto focus trap + approve return-focus** — patterns to copy onto the settings sheet.
10. **Chill slate teal/gold tokens** (no Pendleton bleed on Advanced) — Batch A `clearVars` posture; don’t reintroduce weave chrome without Advanced reset.
11. **Adv intro + research-only title-ctx** on odds, sparks, news, buzz — honesty copy stays.
12. **Macro strip + soft news empty** — soft degrade, no toast spam (per live notes).

---

## Precise recommendations (Advanced + a11y)

### A. Advanced density (visual IA)

1. **Promote day equity; demote pulse/glow** — already `viz-primary|secondary|tertiary` in markup; enforce in CSS: equity full rhythm; pulse/glow half-height or 2-col row under equity so three full-width canvases don’t stack as equal heroes.
2. **One fill-feedback channel in Advanced** — trade-glow canvas *or* body wash, never both.
3. **Friction:** stay under Auto paper; shorten gloss; presets primary.
4. **News:** keep filter/refresh; scroll-cap the feed; single empty voice.
5. **Motion accents:** border tint on Auto paper + equity only; drop AH panel `filter`; keep AH captions.

### B. Mode isolation

1. Add `adv-only` to `#trade-glow-panel`.
2. `setUiMode("advanced")` → `closeSimpleSettings()` then portal home.
3. Keep portal as the only prefs transport (no duplicate friction markup).

### C. A11y checklist (ordered)

| # | Item | Target |
|---|------|--------|
| 1 | Global `:focus-visible` | All interactive controls |
| 2 | Sheet focus move + trap + restore | Parity with howto |
| 3 | Close sheet on mode switch | No orphan dialog |
| 4 | Reduce live regions | Call status primary; feeds quiet |
| 5 | PRM CSS includes `.loop-feed-row.motion-arrive` | Advanced feed |
| 6 | Optional PRM `change` listener in `DeskMotion` | Runtime OS toggle |
| 7 | Approve Tab trap | Same helper as howto |
| 8 | Conf % visible/announced without ring | Advanced call tile |

### D. Desk-wide motion — help vs clutter (Advanced answer)

| Effect | Advanced verdict |
|--------|------------------|
| Session border tint on Auto paper | **Helps** — quiet “live” cue |
| AH panel `filter` | **Clutters** — prefer caption/tokens |
| Body `motion-fill-flash` wash | **Clutters** — duplicates trade glow |
| `motion-arrive` on waiting/news | **Helps** if PRM-safe and rare |
| `motion-arrive` on decision log | **Optional / light only** — wire PRM |
| Conf ring / stage ambient | **N/A** — correctly stripped in Advanced |
| Lucky sparkle | **N/A** — Simple-only control |

**Bottom line:** Shared `DeskMotion` is fine as a helper; Advanced should consume **class toggles and border accents**, not viewport washes or hidden-stage rAF work.

---

## Out of scope / non-goals (this lens)

- Paper loop / brain / broker logic, watchlist config, `brain_mode`.
- Re-opening Pendleton / wool themes on Advanced.
- Rewriting Simple One Job layout into Advanced.
- Product code edits (this file is review-only).

---

## Suggested fix batch (for a later implementer)

**Batch D — Advanced a11y + motion scope**

1. Global focus-visible + friction/wl-find outline fix.  
2. Settings sheet dialog completeness + close-on-Advanced.  
3. `pulseFill` wash gated / retargeted; `adv-only` on trade-glow.  
4. PRM CSS gap for loop-feed arrive; trim aria-live on feeds.  
5. Optional: equity / pulse+glow CSS hierarchy tighten.

No git commit per request.
