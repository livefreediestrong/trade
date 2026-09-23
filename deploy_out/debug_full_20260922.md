# Tomahawk educational paper desk — full debug audit

- **Date:** 2026-09-22 (EDT)
- **Auditor:** executor subagent (box + attempted dream)
- **Box mirror:** `/workspace/daytrade-signal-desk/`
- **Live PC (dream):** machineId `5f5810ed-3b62-424c-81ec-3edccf70f0f5` @ `C:\Users\maher\Projects\daytrade-signal-desk\`
- **URL:** http://127.0.0.1:5056

## Overall status: **degraded**

Box mirror code compiles and UI markers for How-to / Paper friction / Desk alerts are present. Config had a clear P0 brain mismatch (`brain_mode=jev` with no TypeSafe/Jev key while Gemini is the intended brain). **LIVE dream process/API smoke was NOT verified** from this subagent (Shell/`Read` with `machineId` did not route to dream; box cannot reach dream's localhost:5056). Treat runtime health, broker status, `thread_alive`, and live watchlist as **unverified on dream** until parent re-runs smoke on PC.

---

## Access limitation (blocks checklist A/B/E live)

| Attempt | Result |
|--------|--------|
| Shell without machineId | Runs on box (Linux). `curl http://127.0.0.1:5056` → connection refused |
| Shell with `machineId=5f5810ed-…` | Parameter ignored by this executor tool surface; still box bash |
| Read `C:\Users\maher\Projects\daytrade-signal-desk\…` | File not found (no machine routing) |
| `host.docker.internal:5056` / egress proxy `8791` | No path to user localhost |
| UI browser to dream 5056 | **Not used** (box cannot reach user PC localhost) |

**UI note:** UI visual not verified from box; HTML markers checked via templates/static on box mirror (not HTTP on dream).

---

## A. Process & smoke (LIVE — blocked)

| Check | Status | Evidence |
|------|--------|----------|
| Desk responds on 5056 | **UNVERIFIED (dream)** / box refused | `curl -m 5 http://127.0.0.1:5056/api/health` on box → `Failed to connect` |
| `smoke_desk.ps1` on dream | **NOT RUN** | No machineId Shell; script present in mirror (`smoke_desk.ps1` health+state, 3s state budget) |
| brain_mode / llm.configured / broker / watchlist_count | **Box mirror only** (see C) | LIVE values unknown |

**Box mirror snapshot (data/config.json after P0 fix):**

| Field | Value |
|-------|-------|
| brain_mode | `gemini` (was `jev` — fixed on box) |
| llm_enabled | true |
| GEMINI_API_KEY | SET (len 53) → llm.configured expected true if process loads .env |
| broker keys | ABSENT on box `.env` → broker not configured |
| watchlist_count | 4 (`AAPL`,`MSFT`,`NVDA`,`SPY`) |
| loop_enabled | false |
| session_active | false |
| radar_enabled | false |

---

## B. API surface (LIVE — blocked; code-reviewed on box)

Expected routes exist in `app.py`. LIVE HTTP status **not captured**.

| Endpoint | Code presence | Notes |
|----------|---------------|-------|
| GET `/api/health` | yes (~L5075) | Returns `ok`, `llm`, `broker`, `loop`, `providers` |
| GET `/api/state` | yes (~L3268) | Large payload; fail-soft comment for missing keys |
| GET `/api/providers` | yes (~L5020) | `api_providers.public_pack_status()`; outer except → **500** |
| GET `/api/loop/feed?limit=5` | yes (~L4650) | |
| POST `/api/watchlist/find` `{"q":"AAPL"}` | yes (~L3983) | |
| GET `/` HTML | `templates/index.html` | Markers: `#howto-modal`, `#btn-howto`, Paper friction (`#ledger-friction` / `.paper-friction`), Desk alerts (`aria-label="Desk alerts"`, prefs) — **present** |
| GET `/api/research/edgar` | yes | try/except → JSON error **HTTP 500** (not soft 200) |
| GET `/api/research/news` | yes | same pattern → **500** on exception |
| GET `/api/research/macro` | yes | same; `macro_calendar` documents degrade when keys missing |
| GET `/api/research/options-flow` | yes | same → **500** on exception |

Inner clients often return `{ok:false,...}` at 200 when keys/symbols missing; **unhandled exceptions still become 500** at the Flask layer. Checklist wanted “degrade not 500” — partially met (happy-path degrade), not for unexpected throws.

---

## C. Config & secrets (box mirror `.env` / `data/config.json`)

**No secret values below — lengths / presence only.**

### `.env` (box)

| Key group | Status | Evidence |
|-----------|--------|----------|
| GEMINI (`GEMINI_API_KEY`) | **SET** | len=53; also `GEMINI_MODEL` present |
| POLYGON | **ABSENT** | |
| FRED | **ABSENT** | |
| FINNHUB | **ABSENT** | |
| ALPACA_* | **ABSENT** | |
| EDGAR / SEC user-agent | **ABSENT** | |
| TYPESAFE / JEV | **ABSENT** | |
| REDDIT_* | **ABSENT** | Known: OAuth pending |

Only keys present on box `.env`: `GEMINI_API_KEY`, `GEMINI_MODEL`.

### `data/config.json` (box)

| Field | Value |
|-------|-------|
| brain_mode | **was `jev` → fixed to `gemini`** |
| watchlist length | 4 |
| fee_bps | 1.0 |
| slip_bps | 5 |
| radar_enabled | false |
| session_active | false |
| loop_enabled | false |
| mode | manual |
| llm_enabled | true |

**Flag:** `brain_mode=jev` without TypeSafe key → `jev_trade_thesis` returns `missing_typesafe_api_key`; `decide_trade_thesis` docstring/path: **“Jev/gemini errors → hard hold (no silent mock fills)”**. Matches known context (Jev signups paused; Gemini should be brain).

---

## D. Code health (box)

| Check | Result |
|-------|--------|
| `python -m py_compile` on `app.py`, `paper_loop.py`, `llm_trader.py`, `desk_alerts.py`, `market_radar.py`, `buzz_sources.py`, `api_providers.py`, `edgar_client.py`, `news_stream.py`, `options_flow.py`, `macro_calendar.py`, `polygon_client.py`, `broker_alpaca.py`, `data_sources.py`, `screener_logic.py`, `session_track.py` | **EXIT 0** (all OK) |
| `node --check static/app.js` | **NODE_OK** |
| api_pack modules | No separate `api_pack/` package; pack = `api_providers.py` + research clients |
| TODO/FIXME/XXX / stub raises | No actionable broken stubs found in a Python scan of project sources (venv excluded). `raise RuntimeError("missing_gemini_api_key")` etc. are intentional guard paths |

---

## E. Runtime logs / journal (box `data/` — may lag LIVE)

| Artifact | Finding |
|----------|---------|
| `data/journal.json` | 46 entries; recent = filter tests + `app_start` port 5056 + `scan_no_setups` on 4-symbol WL + `live_intent_blocked` (`live_not_wired`) — **no** missing-key / hard-hold / HTTP 500 strings in last entries |
| `data/decisions.json` | `{seq:0, events:[]}` empty |
| `data/ledger.json` | trivial/default |
| loop `thread_alive` | **UNVERIFIED live**; config has `loop_enabled=false`, `session_active=false` → loop not expected active |

Timestamps in journal are ~2026-09-22T16:03–16:15Z (12:03–12:15 PM EDT) — older than this audit evening; mirror may not reflect current dream process.

---

## F. UI

UI visual **not verified from box**. HTML markers checked in box mirror:

- `#howto-modal`, `#btn-howto` — present (howto just shipped; `deploy_out/notes_howto.md`)
- Paper friction controls / `#ledger-friction` — present (`notes_p1_friction.md`)
- Desk alerts prefs + Advanced timeline — present (`notes_p1_alerts.md`)

---

## G. Known context cross-check

| Context | Audit |
|---------|-------|
| Reddit OAuth pending | Confirmed: no REDDIT_* keys on box `.env` |
| Jev/TypeSafe paused; Gemini should be brain | **Violated on box config** (`brain_mode=jev`); **fixed → gemini** on box mirror only |
| How-to tutorial shipped | Confirmed in templates + `static/app.js` + notes |

---

## Ranked findings

### P0 — blockers

1. **`brain_mode=jev` without TypeSafe/Jev API key (box mirror)**  
   - **Evidence:** `data/config.json` had `"brain_mode": "jev"`; `.env` has no `TYPESAFE_*` / Jev key; `llm_trader.jev_trade_thesis` → `missing_typesafe_api_key`; `decide_trade_thesis` hard-holds on jev errors.  
   - **Known intent:** Gemini brain.  
   - **Fix applied (box only):** set `brain_mode` → `gemini` via Python UTF-8 `json.dumps`; watchlist unchanged (still 4 symbols).  
   - **Action for parent:** Confirm/apply same on **dream** live `config.json` (do not use PowerShell `ConvertTo-Json`). Re-smoke `/api/health` → `llm.configured` / brain.

2. **LIVE dream unreachable from this subagent**  
   - **Evidence:** machineId Shell/Read did not route; box curl 5056 refused.  
   - **Impact:** Cannot certify process up, smoke pass, broker, loop thread, or API HTTP codes on production PC.  
   - **Fix:** Parent runs `smoke_desk.ps1` + API checklist on dream with machineId Shell.

### P1 — broken / impaired UX

1. **API pack largely unconfigured on box `.env`** (POLYGON, FRED, FINNHUB, ALPACA_*, EDGAR, REDDIT absent)  
   - Research/radar/broker features will show empty/degraded; Alpaca paper broker unavailable until keys exist on the machine that runs the desk.  
   - Reddit absence expected (pending OAuth).

2. **Research Flask handlers return HTTP 500 on exception**  
   - **Evidence:** `app.py` `/api/research/{edgar,news,macro,options-flow}` `except` → `jsonify(...), 500`.  
   - Checklist expected degrade-not-500; soft `{ok:false}` bodies at **200** would match UX better (inner clients already often return ok:false).

3. **Idle loop/session on box config** (`loop_enabled=false`, `session_active=false`)  
   - Auto paper will not run until user Starts session + enables loop — may be intentional; flag if LIVE was expected hot.

4. **Watchlist only 4 symbols** on box config (liquid quartet) while `google_finance_watchlist.*` exists separately — confirm LIVE was not wiped (historical ConvertTo-Json risk).

### P2 — polish

1. How-to / friction / alerts shipped on box; need hard-refresh + restart on dream to pick up static if not already deployed (`notes_howto.md` says parent deploys + restart).
2. `radar_enabled=false` — radar UI quiet by default.
3. Journal/decisions on box look like test/synthetic activity; not a live trading day log.
4. No separate `api_pack` package name — docs referring to “api_pack modules” map to `api_providers.py` + clients.

---

## Fixes applied

| Fix | Scope | Detail |
|-----|-------|--------|
| `brain_mode` jev → gemini | **Box mirror only** `data/config.json` | Python UTF-8 JSON write; verified watchlist length/contents unchanged |
| None on dream | — | No machine access |

---

## Suggested parent follow-up (dream, ~2 min)

```powershell
# on dream, from repo root
powershell -NoProfile -File .\smoke_desk.ps1
Invoke-RestMethod http://127.0.0.1:5056/api/health
# confirm brain_mode gemini + llm.configured
(Invoke-RestMethod http://127.0.0.1:5056/api/state -TimeoutSec 60).config.brain_mode
Invoke-RestMethod http://127.0.0.1:5056/api/providers
Invoke-RestMethod 'http://127.0.0.1:5056/api/loop/feed?limit=5'
Invoke-RestMethod http://127.0.0.1:5056/api/watchlist/find -Method POST -ContentType 'application/json' -Body '{"q":"AAPL"}'
# research should not 500 on missing keys (note current code may still 500 on throws)
```

If dream `config.json` still has `brain_mode: jev`, patch with Python UTF-8 (not ConvertTo-Json).

---

## Report path

`/workspace/daytrade-signal-desk/deploy_out/debug_full_20260922.md`
