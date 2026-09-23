# P1.6 Paper friction knobs

## Goal
Expose calm Simple + Advanced controls so users can tune paper `slip_bps` / `fee_bps` without editing `config.json`. Fill math unchanged.

## Files changed
- `templates/index.html` — Paper friction group under Auto paper (after Desk alert prefs)
- `static/app.js` — sync from `state.config`, POST `/api/config`, Cheap/Typical/Harsh presets, lite soft-merge
- `static/app.css` — chill slate styles only (`.paper-friction`, fields, presets)
- `app.py` — coerce/clamp `slip_bps` on POST (like `fee_bps`); expose `slip_bps` / `fee_bps` on `state_lite`

## Defaults
- Slip: **5 bps** (`config.slip_bps`)
- Fee: **1.0 bps** (`config.fee_bps`)
- Presets: Cheap `(0, 0)` · Typical `(5, 1)` · Harsh `(15, 3)`

## Control placement
Auto paper panel → after Soft alert sound / Desktop alerts → **Paper friction** row (shared chrome; visible in Simple and Advanced). Quiet session friction hint reuses ledger `friction_usd`.

## State / sync
- Full `/api/state` already returns entire `config` (includes `slip_bps`, `fee_bps`).
- `state_lite` now also carries top-level `slip_bps` / `fee_bps`; UI soft-merges into `state.config`.
- On apply: `syncFrictionInputs` writes inputs only when not focused / not mid-edit (`document.activeElement` + `dataset.userEditing`).
- On change or preset click: `POST /api/config` with `{ slip_bps, fee_bps }` → toast → `refresh()`.

## POST round-trip
1. UI posts numbers (clamped ≥ 0).
2. `api_config`: `slip_bps` → `max(0.0, float(...))`; `fee_bps` → same existing path.
3. `save_config(cfg)`; response includes updated `config`.
4. Next paper fill still uses existing slip-into-price + fee-from-cash math; session `friction_usd` = slip_usd + fee_usd.

## Verify
- `python3 -m py_compile app.py` — OK
- No fill-math semantic changes; no new themes; no git commit

## Deploy
Parent deploys to PC (restart signal desk / refresh static).
