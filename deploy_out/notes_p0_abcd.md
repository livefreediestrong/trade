# P0 Batches A–D — Tomahawk One Job pack

**Date:** 2026-09-22 (ET)  
**Scope:** Open SYNTHESIS P0 rows #3–9 (+ soften spoken copy; gate fill-flash)  
**Already done (not regressed):** Lucky focus-only · Ask-me-first only when `mode === "manual"` · `auto_live` honest gloss  
**Constraints honored:** no git commit · config/watchlist/brain_mode untouched · paper loop / broker fill logic untouched (UI only)

---

## Summary

| Batch | SYNTHESIS P0 | Status |
|-------|--------------|--------|
| A | #3 `call-spoken` wiped · #4 hero vs lean · soften “I would Buy” | **Done** |
| B | #5 dual sticky · #6 poster-bloom | **Done** |
| C | #7 hidden canvas rAF · #8 DeskMotion perpetual / unused breath · fill-flash gate | **Done** |
| D | #9 `:focus-visible` + settings sheet trap | **Done** |

---

## Batch A — Call stage truth

### A1. Preserve `call-spoken`
- **Was:** `big.className = "big-action " + sideClass` wiped One Job classes after first paint.
- **Now:** `classList` remove side mods (`buy|sell|hold|avoid`), then `add("big-action","call-spoken", sideClass)`.
- **File:** `static/app.js` (~loop big-action paint).

### A2. Hero word = research lean
- **Was:** fill-biased `decision` forced Hold on intent/cancel → perpetual Hold while How odds showed Buy/Sell.
- **Now:** `lean = model_side || intended_side` when buy/sell; flat/hold/abstain → Hold; else filled `decision`. Word, spoken line, conf-ring side, and sideClass all use `lean`.
- Hold kept when truly flat/abstain.

### A3. Soften spoken copy
- **Was:** `I would <em>Buy TICK</em> — confidence …`
- **Now:** `Research leans <em>Buy TICK</em> (paper) — confidence …`

---

## Batch B — Sticky + poster-bloom

### B1. Dual sticky (chosen approach)
**Preferred option used:** keep both sticky; stage `top` includes toolbar height.

- `syncStickyOffsets()` now measures `#session-toolbar` in Simple → `--session-toolbar-h`.
- `body.ui-simple .stage-wrap { top: calc(var(--masthead-h) + var(--session-toolbar-h) + 0.25rem); }`
- Toolbar stays `z-index: 55` (controls); stage `z-index: 35` (One Job hero under bar, not covered).
- **Documented in CSS comment** next to `.stage-wrap`.

### B2. Kill poster-bloom under `.call-spoken`
Under `body.ui-simple .call-spoken` / `.stage-wrap .big-action.call-spoken`:
- `max-width: none; width: 100%; margin: 0; min-height: 0`
- Single spoken surface (no stacked poster radial/box-shadow)
- `.action-aurora { display: none }` when spoken card present
- Advanced `.big-action` path untouched (`body.ui-advanced .call-spoken` reset remains).

---

## Batch C — Motion ghosts

### C1. `desk_pulse.js` / `trade_glow.js`
- Added `panelVisible()`: returns **false** when `body.ui-simple` (One Job `display:none` on both panels) or computed style/rects say hidden.
- `frame` / `ensureLoop` / mode / visibility handlers stop rAF when panel not visible.
- Pulse now animates when Advanced panel is shown (was historically Simple-gated while Simple now hides it — ghost loop killed; Advanced panel can paint).

### C2. `desk_motion.js`
- `tick`: if `!visible()` / reduced / `!targetsVisible()` → **`stop()` without re-queue**.
- Removed unused `--ambient-breath` writes (kept `--ambient-drift` only).
- `targetsVisible()`: rAF only when **Simple** + ambient/conf targets actually visible.
- Advanced keeps class toggles (`setSessionMood` / `pulseDecision`) without useless rAF.
- Boot uses `syncVisibility()` instead of unconditional `start()`.

### C3. Fill-flash wash
- CSS: full-viewport `::after` wash scoped to `body.ui-simple.motion-fill-flash`.
- Advanced: `body.ui-advanced.motion-fill-flash #trade-glow-panel` soft border glow only (no desk-wide wash duplicate).
- Reduced-motion covers both.

---

## Batch D — A11y

### D1. Global `:focus-visible`
```css
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```
Plus explicit rings for `.wl-find-input` / friction number inputs that set `outline: none` on `:focus`.

### D2. `#simple-settings-sheet` dialog behavior
Mirrors howto modal:
- On open: store focus, move focus to Close (or first focusable), build Tab list.
- Tab trap (Shift+Tab wrap).
- Escape closes (already); restore focus to gear (or prior).
- `setUiMode("advanced")` force-closes sheet before prefs portal home.

---

## Verify steps

1. **node --check** (already run clean):
   ```bash
   node --check static/app.js static/desk_motion.js static/desk_pulse.js static/trade_glow.js
   ```
2. **Batch A:** Cold load Simple → paint a research intent with `model_side=buy` → stage word **Buy**, spoken “Research leans Buy … (paper)”, Why/How agree; `#loop-big-action` still has `call-spoken`; gloss/story stay hidden via CSS.
3. **Batch B:** Scroll Simple — session toolbar sticky under masthead; stage sticks below toolbar (not covered). Spoken card full-width; no 22rem clamp / no aurora wash.
4. **Batch C:** Simple mode — DevTools Performance: no perpetual rAF from pulse/glow canvases; DeskMotion rAF only while Simple stage ambient visible; switch Advanced → pulse/glow may run, DeskMotion rAF stops.
5. **Batch D:** Tab through desk — accent focus ring visible (incl. friction / find inputs). Open Settings gear → focus in sheet; Tab cycles; Escape restores gear; switch Advanced while open → sheet closes.
6. **No regress:** Lucky still focus-only (no generate). Ask-me-first active only for `manual`. `auto_live` still shows broker warning gloss. `data/config.json` unchanged by this pass.

---

## Files touched

- `static/app.js`
- `static/app.css`
- `static/desk_motion.js`
- `static/desk_pulse.js`
- `static/trade_glow.js`
- `deploy_out/notes_p0_abcd.md` (this file)

