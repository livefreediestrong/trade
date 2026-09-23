# P1.5 Alerts UI — notes

## Files changed
- `static/app.js` — consume `state.alerts`, seen-id Set, toast / Notification / soft beep, Desktop alerts preference, Advanced log render; skip client goal/kill toasts when `state.alerts` present
- `templates/index.html` — Soft alert sound + Desktop alerts toggles under Auto paper; Advanced “Desk alerts” timeline; ops console points at shared toggles
- `static/app.css` — minimal chill styles for prefs row + alert timeline (slate, decision-log aesthetic)

No Python touched.

## Behaviour
1. **Seen-id dedupe**: session `Set` (`deskAlertSeenIds`). First payload with `alerts.items` seeds all ids (no toast spam on refresh). Later items with new ids fire once across poll + SSE.
2. **On apply** (`render` full poll + `applyStateLite` when `lite.alerts` present): chronological process → toast(message, level==='error') → optional `Notification('Tomahawk', { body, tag: id })` if `browser_notification` and preference+permission → soft Web Audio beep if `alert_sound=1`.
3. **Desktop alerts**: checkbox requests `Notification.requestPermission()` only when user enables; stores `desk_browser_alerts=1`. No request on load.
4. **Sound**: existing `alert_sound` localStorage wired to visible “Soft alert sound” checkbox (Simple + Advanced).
5. **Advanced log**: last ~8 rows — kind label, message, relative time.
6. Missing `state.alerts` → no-op.

## How to test
1. Open desk Simple mode: confirm Soft alert sound + Desktop alerts under Auto paper.
2. Enable Soft alert sound; enqueue a Waiting idea (or wait for Auto paper) → toast + quiet beep once; refresh/poll should not re-toast same id.
3. Enable Desktop alerts → browser permission prompt once; grant → Waiting / goal / max-loss can show OS notification tagged by id.
4. Switch Advanced: Desk alerts list under decision log updates; same toggles still work.
5. With backend quiet / no alerts key: UI stays calm, no errors.

## Trace
`waiting_enqueue` / `goal_hit` / kill hooks → `desk_alerts.emit` → `drain_for_state` on `/api/state` + `state_lite` → frontend `consumeDeskAlerts` → toast (+ optional Notification + beep).
