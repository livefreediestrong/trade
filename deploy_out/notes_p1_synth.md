# P1 strong follow-ups — Tomahawk synthesis

**Date:** 2026-09-22 (ET)  
**Sources:** `design_art_review/SYNTHESIS.md` + art 01–04 + `logic_review_20260922.md`  
**Already done (not regressed):** P0 A–D · Lucky focus-only · Ask-me-first only when `manual` · auto_live gloss · spoken “Research leans … (paper)” · fill-flash Simple-only / Advanced glow-scoped  

**Constraints honored:** no git · config/watchlist/brain_mode untouched · no new fill paths · Lucky generate not reintroduced  

---

## Checklist

| # | P1 item | Status | Notes |
|---|---------|--------|-------|
| 1 | Type scale / `action-meta` competing with spoken Why | **Done** | Simple `.call-spoken .action-meta` + `.action-outcome` → `display: none !important` (mirror gloss). Primary remains word + spoken + Why + How. |
| 2 | Triple ambient (chrome-wave + body::after + stage-ambient) | **Done** | Chrome-wave → static 2px hairline, opacity 0.45, no animation. `body.ui-simple::after` → faint 28% foot wash. **Stage-ambient kept as hero.** Advanced pulse/glow unchanged. |
| 3 | Hold color = gold collides with meta | **Done** | `.call-spoken.hold .action-word` → cool `#a8b4c4`. Gold reserved for butler/meta/Waiting/Lucky. |
| 4 | News vs Waiting empty asymmetry | **Done** | Empty Simple news adds `is-empty-quiet` (dashed calm empty, no teal panel chrome). Advanced tools kept. |
| 5 | Soften “I would Buy” | **Skipped** | Already shipped in P0 (“Research leans … (paper)”). |
| 6 | Jargon: tape / RTH / bps / Master / (dream) | **Done** | See copy table below. Master removed from defaults (no Settings toggle — not worth the surface). |
| 7 | Topbar Lucky + Old mock crowding | **Done** | Old mock demoted to quiet footer link (`#btn-simple-mock` / `.footer-mock-link`). Lucky stays on Simple topbar. |
| 8 | Fill-flash wash vs Advanced glow | **Verified** | Still Simple-only full wash; Advanced retargeted to `#trade-glow-panel` border glow. Not changed. |
| 9 | `loop-feed-row.motion-arrive` in reduced-motion kill list | **Done** | Added `.loop-feed-row.motion-arrive` + Advanced scoped selector to PRM block. |
| 10 | Dual confidence (logic) | **Done (UI honesty)** | See confidence section. |
| 11 | News display-only vs macro Ask-first | **Done** | News butler/empty/title + spoken headline labeled display-only. Macro strip chips force Ask-first / size cut in plain language when `risk.force_ask_first` / `size_mult < 1`. How-details show macro rows from intent fields. |

---

## Copy changes (jargon)

| Surface | From | To |
|---------|------|----|
| News h2 (Simple) | What’s moving the tape | What’s moving (+ display-only title-ctx) |
| News empty | Finnhub/Yahoo… | No headlines… Sources may be offline… display-only |
| Masthead badge | RTH / OUTSIDE RTH | Market hours / Outside hours |
| Butler AH | RTH-only is on | Market closed — checks pause until the next open (US hours) |
| Butler default | …, Master | Standing by / waiting for you (no Master) |
| Equity butler | watching the tape | watching watchlist headlines |
| Friction (Simple) | Slip (bps) / Fee (bps) | Worse than mid / Per-fill drag (bps in title) |
| Howto step 1 | this PC (dream) | this computer |

---

## Dual confidence — choice

**Chose: UI honesty first; persist fields for How; do not unify gate math.**

- Loop still gates on raw thesis `confidence` (`paper_loop.py` ~905–913).
- Execute still gates on blended playbook+LLM (`app.py` `build_loop_signal` / ~2439–2446).
- **UI:** How-details label **Research confidence** (stage/Why number = `llm_confidence` \|\| `confidence`).
- When execute returns a signal, intent/fill events now also store **`gate_confidence`** (blended). If it differs ≥0.5pp from research, How shows a second line **Fill gate confidence (blended with playbook)**.
- Unifying the two gates into one formula was deferred — would change which paper fills pass vs hold; out of scope for this P1 UI batch.

Macro fields also persisted on intent for How: `macro_force_ask_first`, `macro_butler_note`, `macro_size_mult`, `macro_forced_pending`.

---

## Verify

```bash
node --check static/app.js   # clean
python3 -m py_compile paper_loop.py   # clean
```

Manual (dream deploy):
1. Simple cold load — stage: no meta/outcome under spoken; Hold word cool slate not gold.
2. Ambient: thin static wave; stage wash is the mood hero.
3. Empty news → quiet dashed empty; with headlines → panel chrome returns; butler says display-only.
4. Macro day chip / How row when force-ask or size cut.
5. Settings: Worse than mid / Per-fill drag; bps in titles.
6. Footer “Old mock (comparison)”; Lucky still topbar.
7. `prefers-reduced-motion`: feed arrive animations off.
8. Lucky still focus-only; Ask-me-first only for `manual`; fill-flash still Simple-only.

---

## Files touched

- `static/app.css`
- `static/app.js`
- `templates/index.html`
- `paper_loop.py` (intent/fill display fields only — no fill-path change)
- `deploy_out/notes_p1_synth.md` (this file)
