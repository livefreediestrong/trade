# Design + logic synthesis — Tomahawk One Job pack

**Date:** 2026-09-22 (ET)  
**Sources:** logic_review_20260922.md · art 01 hierarchy · 02 motion · 03 Simple clarity · 04 Advanced a11y  
**Already shipped:** Lucky focus-only (no generate/fill) · Simple Ask-me-first only when `mode === manual` · auto_live honest gloss (`notes_p0_lucky_mode.md`)

---

## Overall verdict: **ship-with-fixes**

One Job IA and butler chill slate are directionally right. Remaining work is cascade/bugs (spoken call class wiped), sticky stack, motion ghost work, and a11y dialog/focus — not a redesign.

---

## P0 — fix next (ranked)

| # | Issue | Lens | Status |
|---|--------|------|--------|
| 1 | Lucky → generate could fill / broker | Logic | **DONE** |
| 2 | Simple toggle lit Ask-me-first for `auto_live` | Logic | **DONE** |
| 3 | `big.className = "big-action …"` drops `call-spoken` after first paint → spoken layout/Why hide breaks | Clarity 03 | Open |
| 4 | Stage word uses fill-biased `decision` (often Hold) while lean is `model_side`/`intended_side` → Why disagrees with hero | Clarity 03 | Open |
| 5 | Dual sticky: session toolbar z-55 over stage z-35 | Hierarchy 01 | Open |
| 6 | Legacy poster-bloom (`max-width: 22rem`, aurora) fights full-width `.call-spoken` | Hierarchy 01 | Open |
| 7 | Hidden pulse/glow canvas rAF still runs in Simple | Motion 02 | Open |
| 8 | DeskMotion perpetual rAF / unused `--ambient-breath`; Advanced pays for hidden stage | Motion 02 | Open |
| 9 | No global `:focus-visible`; settings sheet dialog incomplete (no trap/restore) | Adv a11y 04 | Open |

---

## P1 — strong follow-ups

- Type scale / `action-meta` still competing with spoken Why (01, 03)
- Triple ambient (chrome-wave + body::after + stage-ambient) (01)
- Hold color = gold collides with meta gold (01)
- News always-on vs Waiting quiet-empty asymmetry (01)
- “I would Buy” sounds like an order; soften to research lean (03)
- Jargon: tape / RTH / bps / Master / (dream) in howto (03)
- Topbar Lucky + Old mock crowding (03)
- Fill-flash wash duplicates trade-glow on Advanced; gate to Simple or glow only (04)
- `loop-feed-row.motion-arrive` missing from reduced-motion kill list (04)
- Dual confidence math loop vs execute (logic)
- News display-only vs macro that *does* force Ask-first — label UI honestly (logic)

---

## Keep

Sticky One Job stage · gate Why + How `<details>` · quiet Waiting · Settings portal · collapsed curve/pos · pulse/glow hidden in Simple · PAPER ONLY · prefs portal back to Advanced · chill slate teal/gold · restrained Lucky sparkle (now focus-only)

---

## Recommended next batch

**Batch A (UI truth — ~1 session):** P0 #3–4 call-spoken + model_side hero; soften “I would Buy” copy.  
**Batch B (layout/art):** P0 #5–6 sticky + kill poster-bloom under Simple call-spoken.  
**Batch C (motion/perf):** P0 #7–8 pause hidden canvases; DeskMotion start only when Simple ambient visible.  
**Batch D (a11y):** P0 #9 focus-visible + settings sheet trap like howto.

Full per-lens write-ups live beside this file.
