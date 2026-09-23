# Simple A+B mock — One Job + Butler polish

**Date:** 2026-09-22 (ET)  
**Status:** Visual mock only — does **not** replace live Simple. Not wired to `/api` or the paper loop.

## Open it

| Path | URL (on dream when desk is up) |
|------|--------------------------------|
| `/workspace/daytrade-signal-desk/static/simple_one_job_mock.html` | http://127.0.0.1:5056/static/simple_one_job_mock.html |

From live **Simple** topbar: ghost button **Preview One Job** (`#btn-simple-mock`, Simple-visible only) opens the mock in the same tab. Advanced hides it via `.simple-only`.

Back link on the mock returns to `/` (live desk).

## What to click (demo controls)

1. **Waiting: 1 card / empty (0)** — toggles tray vs empty-state CSS (A2: panel chrome only when count > 0).
2. **Day curve** strip — tap to expand sparkline → full curve; tap again to collapse (A3, default collapsed).
3. **Positions** chip (“2 open”) — tap to expand holdings; tap again to collapse (A5).
4. **⚙ Settings** (header) or **Open Settings** (demo strip) — friction/alerts sheet, not main scroll (A6). Esc / backdrop / Close dismisses.
5. **Cycle Hold / Buy / Sell** — swaps spoken call + soft confidence ring (B polish).
6. **Auto fill / Ask me first** — visual mode chips only (mock).

Banner always reads: `MOCK — Simple A+B preview · not wired to live loop`.

## Map to overhaul A — One Job

| Spec | Mock element |
|------|----------------|
| Sticky stage: thin goal race + big Hold/Buy/Sell + confidence soft ring + one butler line | `.stage-wrap` (sticky) + `.goal-race` + `.conf-ring` + spoken line |
| Waiting only when count > 0 | `#waiting-tray` hidden when empty; `#waiting-empty` shown |
| Day curve tap-to-expand (default collapsed) | `#curve-strip` |
| Desk pulse + Trade glow → one ambient behind stage | `.ambient` CSS gradient / grid wash (no dual canvases) |
| Positions = chip row expanding on tap | `#pos-row` / `#pos-chip` |
| Friction/alerts → gear Settings sheet | `#settings-sheet` via `#btn-settings` |

## Map to overhaul B — Butler polish (layered on A)

| Spec | Mock element |
|------|----------------|
| Teal/gold on chill slate (not Pendleton/wool) | `--accent` teal + `--gold`; slate `--bg` / `--panel` |
| Butler card in header | `.butler-card` short status sentence |
| Call as spoken card | “I would Hold AAPL — confidence medium…” |
| Waiting as calm tray + Approve / Skip | `.waiting-tray` / `.tray-card` |
| Deferential educational copy + paper-only disclaimer | spoken why + `.disclaimer` |

## Files touched

- **New:** `static/simple_one_job_mock.html` (self-contained CSS + JS)
- **New:** `deploy_out/notes_simple_ab_mock.md` (this file)
- **Touch (minimal):** `templates/index.html` — `#btn-simple-mock`
- **Touch (minimal):** `static/app.js` — navigate to mock on click

## Explicitly not done

- Live Simple remains default (unchanged layout)
- Advanced layout untouched
- No Pendleton/wool
- No `/api` or loop changes
- No git commit
