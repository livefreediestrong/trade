# Tomahawk — Paper Trading Desk (Gemini)

Flask signal desk with **Gemini LLM thesis + chat**, **risk presets**, **human approve**, and optional Auto modes.  
Companion-style app for a Windows PC beside `holdings-options-monitor`.

**Port:** `5056`

## Research draft (UI banner + code)

> Tomahawk — Gemini research desk. Local paper by default; Alpaca optional (`ALPACA_PAPER=true` → paper-api; `ALPACA_PAPER=false` → **LIVE money** endpoint).

- **Draft research tool** — no practical-use capability filters; AVOID / late / chasing / low confidence are **annotations** (`research_flags`), not hard rejects.
- **Alpaca optional** via `broker_alpaca.py` (`ALPACA_API_KEY` / `ALPACA_API_SECRET`). Default `ALPACA_PAPER=true` → `paper-api.alpaca.markets`.
- Raw `/api/broker/*`, `/api/orders`, `/api/alpaca/*`, `/api/ibkr/*`, `/api/tos/*` stay **403** (use desk approve / auto_live).
- **auto_live / approve (when mode=auto_live):** `can_take_trade` + size/loss caps **first**, then broker submit. Successful broker submit is **broker-only** (no dual local `paper_fill`). Broker fail → **no trade** (never booked as paper).
- UI masthead: **PAPER ONLY** unless `ALPACA_PAPER=false` and keys set → **LIVE ENDPOINT**.
- Switching to real money requires typing REAL in the confirm prompt.

Kill-switch / daily profit target remain **optional** research controls; they do not block mode entry by default.


## Gemini LLM (thesis + chat)

Set your Google AI Studio key in project `.env` (gitignored):

```env
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.6-flash
```

(`GOOGLE_API_KEY` is also accepted.) Restart the app after editing `.env`.

**What the LLM does**

- On each watchlist scan / **New signal**, after `screener_logic.analyze_ticker`, Gemini returns a structured trade thesis (`side`, `confidence`, `thesis`, entry/stop/target ideas, risks). Fields land on the signal as `llm_thesis`, `llm_side`, `llm_confidence`, `llm_model`, `llm_raw` (truncated).
- Side from LLM (`buy`/`sell`) replaces the playbook default when present; `flat` keeps the playbook buy research default. Confidence is blended 50/50 with the playbook score.
- **Chat** (`POST /api/llm/chat`) answers free-form day-trade research questions (optional ticker + fresh screener context). Replies are journaled.
- Without a key the desk still runs in **screener-only** mode (`configured: false`); thesis/chat return a graceful `missing_gemini_api_key` error — no crash.

Config toggles (also via `POST /api/config`): `llm_enabled` (default true), `llm_on_scan` (default true), `llm_model` (optional override).

## Modes

| Mode | Behavior |
|------|----------|
| **manual** (default) | Signals land in a **pending** queue. User must **Approve** or **Reject**. Approve → paper fill into ledger. |
| **auto_paper** | Signals are approved automatically into the **paper** ledger (simulated fills at signal/last price ± slip). No broker. |
| **auto_live** | Gate `can_take_trade` first; if Alpaca keys set, submit to Alpaca (paper-api unless `ALPACA_PAPER=false`). Success = broker-only book. Fail = no trade. Switching to real money asks you to type REAL. |

## Risk presets

Stored in config / applied from `RISK_PRESETS` in `app.py`:

| Preset | Max size % equity | Max trades/day | Max daily loss % | Min confidence | Stop / target R |
|--------|-------------------|----------------|------------------|----------------|-----------------|
| **low** | 1% | 3 | 1% | 0.0 (no filter) | 1R / 2R |
| **mid** | 2% | 6 | 2% | 0.0 (no filter) | 1R / 2.5R |
| **high** | 4% | 12 | 4% | 0.0 (no filter) | 1R / 3R |

Suggested size cuts for late/chasing appear in reason text / `size_mult_suggested` as FYI only; research uses full size (`size_mult=1.0`).

## Signals

- Signals come from a **volume-screener playbook** scan of the editable **watchlist** (relative volume, SMA10/20/50, sector CMF → PASS / WATCH / AVOID, plus entry_quality / lateness).
- **Buy-side** research candidates are queued for PASS / WATCH / **AVOID** (AVOID tagged for false-positive study; no shorts invented).
- Hard rejects for AVOID / late / chasing / min_confidence are **removed** — flags only.
- Cards show verdict, lateness, earnings-soon (≤7d), and rel_vol badges.
- Quotes: yfinance → Yahoo chart/NASDAQ daily fallback (`data_sources`) → optional Finnhub if `FINNHUB_API_KEY` in `.env`.
- Queue statuses: **pending** / **approved** / **rejected** / **expired** (TTL from config).
- Background **scan loop** default interval **120s** (`scan_interval_sec`).
- Daily profit target (if set) still pauses new fills when hit; kill-switch only when armed.
- **Journal** of actions in `./data/journal.json`.

## Persist

All under `./data/*.json`:

- `config.json` — mode, preset, watchlist, kill-switch, paper equity, slip
- `signals.json` — signal queue
- `ledger.json` — paper cash, positions, fills, daily stats
- `journal.json` — action log

## Stack

Flask + Jinja + vanilla JS + yfinance/pandas/requests. Dark desk UI (`--bg #0b0f17`, etc.).

## Windows quick start

Double-click the **Tomahawk** desktop shortcut (runs `Start-Tomahawk.ps1`: starts the desk if needed, then opens the browser). First-time setup: `python -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`.

(`Launch.bat` is legacy — it builds a separate `venv` folder.)

Or after first setup:

```bat
run.bat
```

After a deploy from the box:

```powershell
powershell -NoProfile -File install_and_restart.ps1
```

## Linux / this box

```bash
cd /workspace/daytrade-signal-desk
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5056`

## API sketch

- `GET /api/state` — config, signals by status, ledger, journal
- `POST /api/config` — mode / preset / watchlist / optional kill-switch
- `POST /api/signals/generate` — force one watchlist scan (optional `{"ticker": "AAPL"}`); the idea always waits for Approve
- `POST /api/signals/<id>/approve` — paper fill; if mode=auto_live → gate then broker-or-paper (no dual-book)
- `POST /api/signals/<id>/reject`
- `POST /api/ledger/reset`
- `GET /api/health` — includes `llm: {configured, model, enabled}`
- `GET /api/llm/status` — `{configured, model, provider:"gemini"}` (never returns the key)
- `POST /api/llm/chat` — `{message, ticker?}` → `{ok, reply, ticker, analysis_snippet?}`
- `POST /api/llm/thesis` — `{ticker}` → analyze + structured thesis

## Broker adapter summary (Alpaca optional)

1. Default mode is **manual**; fills are local paper unless auto_live + keys.
2. **auto_live** on a real-money endpoint asks you to type REAL; on Alpaca paper a confirm dialog.
3. `live_broker_place_order` posts to Alpaca when keys are set; journals every attempt. Missing keys → `live_not_configured`.
4. Desk **never** dual-books: broker success skips local `paper_fill`; broker fail is reported as a failure (no paper fallback).
5. `ALPACA_PAPER` defaults **true**. `false` → live money endpoint; UI shows **LIVE ENDPOINT**.
6. Force flatten closes local paper **and** cancel/close Alpaca when configured (else clearly not broker-complete).
7. Raw broker HTTP routes return 403; banner always visible.

## Safety guards (2026-09-22 review fixes)

- **Local-only by default.** Binds `127.0.0.1:5056`. Requests with a foreign `Host`, a foreign `Origin`, or `Sec-Fetch-Site: cross-site` get 403, so a web page you visit can't switch modes or approve trades. To expose on a LAN on purpose: `TOMAHAWK_HOST=0.0.0.0` plus `TOMAHAWK_ALLOWED_HOSTS=192.168.x.y:5056`.
- **Corrupt data fails closed.** A BOM is tolerated. An unreadable `data/*.json` is backed up as `*.corrupt.<timestamp>.bak`, never overwritten, and trading is gated until it's repaired and the app restarted (`corrupt_files` in `/api/health` and `/api/state`; UI toast).
- **Broker honesty.** A broker order that fails or is rejected is **not** booked as a local paper trade. Fills use Alpaca's `filled_avg_price`/`filled_qty` (polled up to `BROKER_FILL_WAIT_SEC`, default 6s); otherwise the fill is flagged `confirmed:false`, `price_estimated:true`. Broker orders count toward max trades/day, and broker day P&L (equity − last_equity) is checked against the loss caps. Gate+submit is serialized.
- A crashed approve marks the signal `rejected` (never stuck in `approving`, never silently re-pending). Signals stuck in `approving` are released at startup.
- `signals.json` keeps the newest 300 resolved signals plus all pending ones.
- Tests: `.venv\Scripts\python -m pip install pytest` then `.venv\Scripts\python -m pytest tests -q` (uses a temp data dir; no network).

## 10-agent review fixes (2026-09-22, later the same day)

**Safety / money**
- Gemini key goes in the `x-goog-api-key` header (never the URL); error text is scrubbed of keys.
- Every number from the UI/API is validated (NaN/Infinity/strings → 400); NaN on disk is reset to defaults; JSON never contains NaN.
- Expiring old ideas can no longer undo an in-progress Approve (double fill) or drop new ideas.
- Broker path: never opens real shorts; sells are clamped to shares held and treated as exits; order size uses the broker account's equity; broker account/positions unreachable = no order.
- Start keeps your fill mode (Ask me first stays Ask me first) and asks before archiving open positions; Stop no longer changes the mode and stop-loss/take-profit keep running after Stop.
- "Find an idea" (was New signal) and Lucky never trade — ideas always wait for Approve.
- Model confidence: 85 → 0.85; NaN/true/huge → hold; contradictory side/horizon → hold.
- Market hours know NYSE holidays and 1 pm early closes. FOMC days come from `FOMC_DECISION_DAYS` in `macro_calendar.py` — **extend it each year**.
- Fed/CPI/jobs-day check now actually fires (FRED queried for the day itself); a FRED outage is reported, not treated as a calm day.

**Limits / data**
- Loss limits count open positions (recent prices); size limits are per position, not per order; fees count in today's P&L; fee 0 means 0.
- Exit levels must be on the right side of entry; partial sells keep your levels.
- Screener: no PASS from yesterday's volume; first-hour volume is time-adjusted; NaN last bars dropped; stale-trade halt proxy; yesterday's close is never labeled live.
- Finnhub quota: earnings cached per ticker/day, option chains cached; radar fixed (right Finnhub fields, no rescan storm, IEX volume scaled + labeled, leveraged ETFs excluded).
- Reddit public scraping is off unless `REDDIT_CLIENT_ID/SECRET` are set (or `REDDIT_PUBLIC_JSON=1`).
- Gemini cost recorded for every billed call from real token counts; loop totals reset at NY midnight. `tzdata` added to requirements.

**Screen**
- Simple mode follows the stage of the day: set up → watching → an idea needs you (it appears on the main card; keys **A** review / **S** skip, 5 s Undo) → recap after Stop.
- Plain language throughout ("Skip AAPL for now. The AI is fairly sure (about 8 in 10)…"), money shown with +/− and ▲/▼, paper = violet, real money = orange with a warning bar and a typed confirmation.
- Approve window shows the maths and whose money moves; broker positions shown separately; unconfirmed broker fills are labeled; "Today" shows closed and open P&L.
- "Live · 2s / Not updating" freshness in both modes with a banner when data goes stale; the "(2) waiting" count shows in the browser tab.
- Pop-ups (Approve, tutorial, Settings, toasts) sit on top again in Simple; watchlist editable in Simple Settings; 44 px touch targets; focus rings; reduced-motion respected.

Light mode: the ☀ / ☾ button in the header (defaults to your computer's setting). `static/theme_light.css` is **generated** from app.css — after editing app.css run `.venv\Scripts\python tools\gen_light_theme.py`. Money/paper/live colours are hand-tuned at the bottom of that script.

## Slow-bleed guard & SPY benchmark
- **Slow-bleed guard:** if your last 20 closed paper trades (at least 10) are net negative **after fees**, the desk stops opening new trades and shows "Paused to protect your money" with the numbers. Exits keep working. **Resume anyway** restarts the count (`POST /api/bleed/resume`). Tuning: `BLEED_WINDOW` / `BLEED_MIN_TRADES` in app.py; turn off with `bleed_guard_enabled: false` in config.
- **You vs. SPY:** at Start checking the desk records SPY's price; the stage and the end-of-day recap show your return next to simply holding SPY with the same cash.

## Trading-bot ideas (from YouTube review, 2026-09-22)
- **Trailing stop:** "Trailing stop %" in the Approve window — the stop follows the price up (never down).
- **Lessons memory (`lessons.py`, `data/lessons.json`):** every scored call becomes a one-line lesson; before deciding, the AI sees this desk's own record for the same kind of setup and ticker. `GET /api/lessons`.
- **Midday check:** once a day after 12:00 ET, positions down 7%+ are closed and positions up 3%+ get their stop raised to the price paid (`MIDDAY_CUT_PCT`, `MIDDAY_BREAKEVEN_PCT`; off with `midday_check_enabled: false`).
- **Report card:** header button — last 7 days, grade A–F, profit after costs, % of AI calls right, worst setups. `GET /api/report/weekly`.
- **Scanner signals:** 5-minute relative volume (vs. the same time on prior days), VWAP and its slope, distance from VWAP / day's high in ATRs, bid-ask spread vs. ATR (market hours only). They can downgrade PASS → WATCH and are shown to the AI.
- **Claude (`claude_brain.py`):** choose "Claude" as the brain, or tick "Claude head-to-head" to have Claude answer silently next to your main AI; the report card shows who was right more often on the same decisions. Needs `ANTHROPIC_API_KEY` in `.env`. Model `CLAUDE_MODEL` (default `claude-opus-5`, ~$5/$25 per million tokens), `CLAUDE_EFFORT` (default `low`). Uses server-side refusal fallbacks (`fallbacks: "default"`).
- **Past-data test (`backtest.py`):** "Test the rules on past data" in the report card replays the screener's PASS rule on ~2 years of hourly prices with a learning/check split and a no-filter baseline. `GET/POST /api/backtest`.
