# P0 fix — Lucky focus-only + Simple mode labeling

**Date:** 2026-09-22 (ET)

## Fixes
1. **I'm Feeling Lucky** — removed `POST /api/signals/generate` from `feelingLucky()`. Focus + butler line + sparkle only. Generate/ingest could paper-fill (`auto_paper`) or broker-submit (`auto_live`).
2. **Simple fill toggle** — `Ask me first` active only when `mode === "manual"` (was `mode !== "auto_paper"`, which lit Ask-me-first during `auto_live`).
3. **Hints/gloss** — explicit copy when `mode === "auto_live"` so Simple never implies paper Waiting.

## Files
- `static/app.js`
- `templates/index.html` (Lucky button title)

## Verify
- Lucky with session on + auto_paper: focus changes, no new auto fill from Lucky alone.
- Set Advanced mode to auto_live: Simple Ask me first not active; gloss warns broker path.
- Click Ask me first → mode becomes `manual`.
