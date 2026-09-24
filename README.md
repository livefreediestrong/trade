# nadzeeɫ — Live Trading and Paper Research

Windows-local trading and research desk, also called **Tomahawk** / **Daytrade Signal Desk** in the launcher and code. Includes separate live and paper workspaces, a shared US-market scanner, options research, Moss research memory, and the Fox / Changing Woman sidebar companions.

## Install from GitHub

1. Install Python **3.11 or later** on Windows, with the Python launcher available.
2. Clone this repository, or download its ZIP from GitHub and extract it to a writable folder.
3. Double-click **`Launch.vbs`** in that folder. The first launch creates `.venv`, installs the Python dependencies, and opens **http://127.0.0.1:5056/**. `Launch.bat` is the visible diagnostic alternative.
4. For optional AI or broker integrations, copy **`.env.example`** to **`.env`**, enter your own settings locally, and restart the desk. Complete broker sign-in and 2FA in IB Gateway when needed.

This repository contains the application source and artwork. Your API keys, broker account settings, saved trading mode, orders, fills, research memory, imported documents, databases, and local logs are **not included**. A fresh copy starts with a stopped session and paper automation disabled; it does not inherit the original PC's live configuration. Keep `.env` and everything under `data/` private.

The GitHub upload is a clean source snapshot, preserving the destination repository's history without importing older local diagnostic commits. The original local installation and its data remain separate. Do not run both checkouts against the same broker account or shared data folder.

Useful feature references:

- [Shared market scanner](docs/SHARED_MARKET_SCANNER.md)
- [Agent research workspace](docs/AGENT_RESEARCH_WORKSPACE.md)
- [Options and automation](docs/OPTIONS_AND_AUTOMATION.md)
- [Changing Woman news and source feeds](docs/CHANGING_WOMAN_NEWS.md)
- [UI structure](docs/UI_STRUCTURE.md)
- [Manual live enablement reference](docs/MANUAL_LIVE_ENABLEMENT.md)
- [Direct stock tickets and position closing](docs/LIVE_STOCK_TICKETS.md)
- [Automation debugging and scheduler boundaries](docs/AUTOMATION_DEBUG.md)
- [Moss broker agent: policy, activation, execution and limits](docs/LIVE_AGENT.md)
- [Market watch, X watcher and internet-wide trend scanner](docs/MARKET_WATCH.md)

The detailed implementation notes below use the legacy Tomahawk name.

Flask signal desk with **Gemini LLM thesis + chat**, **risk presets**, **human approve**, and optional Auto modes.
Companion-style app for a Windows PC beside `holdings-options-monitor`.

**Port:** `5056`

## Open everything with one shortcut

Double-click **Daytrade Signal Desk** on the desktop. The shortcut runs `Launch.vbs`, which calls the single `Start-Tomahawk.ps1` startup flow without leaving a terminal open.

The launcher creates a missing Python environment, repairs missing dependencies, starts the desk, waits for its health check, opens its browser page, and opens the installed IB Gateway when IBKR is selected and the configured API port is unavailable. It reuses existing components on repeat clicks. Previous server logs are retained as `.previous` files; setup failures show an explanation and keep details in `data/setup.log` or `data/server.stderr.log`.

Complete **IB Gateway sign-in / 2FA** when prompted. The launcher does not store credentials, choose an account, change execution mode, or submit orders. The desk displays remaining startup steps at the top of the page; account verification and mode confirmation still happen before broker execution. A reachable Gateway port alone is not account verification.

The IBKR adapter keeps one API connection on an owner thread and retains its client ID across requests. Disconnects clear identity verification; the next request reconnects. Account balances and holdings can remain readable when the separate daily P&L subscription is unavailable. In that case the desk shows the P&L limitation and blocks new risk. Verified closes can still reduce holdings after accounting for working orders.

If daily P&L stays unavailable, check **Configure → Settings → API → Settings → Prepare portfolio PnL data when downloading positions** in IB Gateway. Prefer the desk **Refresh broker P&L** control (or `POST /api/broker-pnl-refresh`) first: it soft-reconnects only the API client and re-requests `reqPnL` without restarting the Gateway process. The desk also auto-retries silent subscriptions, handles IB error **2100** (competitor account-data request) by soft reconnect + re-subscribe with backoff, and while `auto_live` is on with `risk_ready` false can schedule soft refreshes every `IBKR_PNL_SOFT_REFRESH_MINUTES` (default 5; set `0` to disable). Account-window realized/unrealized figures may still display while Daily P&L is missing; they never satisfy `risk_ready`. The UI shows **Recovering Daily P&L...

**2FA / IB Key cannot be eliminated** and must never be disabled. Soft reconnect does not log Gateway out, so it usually avoids 2FA. Human 2FA is required when Gateway fully logs out (weekly reauthentication, explicit logout, cold start after token expiry). To make full logouts rare: in IB Gateway set **Configure → Lock and Exit → Never lock** and **Auto restart** at a time you are around (IB’s recommended API setup). Optional later: [IBC](https://github.com/IbcAlpha/IBC) can drive Auto-Restart; this desk documents it only — it is not bundled. A Windows Task Scheduler proposal lives in `tools/Register-GatewayDailyRestart.ps1` (relaunches `ibgateway.exe`; login may auto if the session is cached, otherwise you still approve IB Key).

Gateway is discovered under `C:\Jts\ibgateway` or your user `Jts\ibgateway` directory. For another location, set `IB_GATEWAY_EXE` in `.env`. `Launch.bat` and the older start/restart scripts delegate to the same launcher; none blindly terminates a port owner. For diagnostics, run `Launch.bat -NoBrowser -NoDialogs`.

The launcher reuses a running desk only when its health response identifies this checkout and the configured data directory. If the code in this folder changed after that desk started (for example after `git pull`), the launcher asks whether to restart it; the desk refuses to stop while a broker order is unresolved, and headless runs (`-NoDialogs`, the watchdog) never restart it. The page also shows a notice until the desk is restarted. `Launch.vbs` and `Launch.bat` run the launcher with a process-scoped `-ExecutionPolicy Bypass`, so Windows' default script policy does not block the shortcut. Another checkout on the same port is reported as a conflict. Process environment settings override `.env`; inline comments are supported, and quote values that contain a literal `#` after whitespace. Jev request budgets use `JEV_RPM=30` and `JEV_DAILY=500` by default and are reserved before provider requests.

## Market-hours data and decision handling

- Flat/hold and failed-brain decisions stay research-only; confidence in staying flat is not blended into buy confidence.
- Mock results carry their actual model/router label. Mock and AVOID research cannot be sent to a broker; directional mock ideas remain available for local paper practice.
- New execution and normal paper fills require a positive quote with a provider timestamp no more than 120 seconds old (and no more than 15 seconds in the future). Unknown/stale times fail closed. The UI preserves source, market time, receipt time and freshness separately.
- The latest-call tile follows the active scanner/paper path and dates old calls explicitly. Old decision records are retained; incomplete fill rows are labeled as incomplete legacy records.
- Risk Cockpit labels its scope and never grants general broker permission from local paper risk. Broker exposure is unknown until checked against the broker; the exact order is checked on submission.
- Current quotes are applied before SMA/gap/verdict evaluation. Five-minute indicators have their own current-session, timezone, and age checks; unavailable indicators are omitted and cannot support PASS.
- Stale and nonactionable pending research cannot monopolize the scan queue. Replacement ideas retain the previous record as superseded history.
- Waitress reserves capacity for ordinary requests by admitting at most four SSE streams; extra tabs use polling.

## One page, separate live and paper accounts

The desk shows **Live trading**, **Paper research**, **Market research**, and **Settings** together on one page, with fixed links between sections. The full interface is always shown; Simple mode is deferred. The weekly report is inline and can be refreshed without leaving the desk.

**Live trading** is the first, primary section. It shows the verified broker account, holdings, broker orders and live ideas. The existing selected account and execution mode are retained. In `live_manual`, an idea needs an explicit order review and matching ticker acknowledgement before submission. A broker market order has **no attached stop-loss, take-profit or trailing stop**; paper exit controls are hidden and rejected on broker submissions.

The **Stock order ticket** supports user-directed whole-share buys, selling owned shares and covering existing shorts through the verified IBKR `live_manual` account. Choose market or DAY limit, review the exact account/quantity/price/estimated fees, then type the stock symbol before submitting. Holding buttons prepare a ticket without sending it. See [direct stock tickets](docs/LIVE_STOCK_TICKETS.md) for supported scope and validation.

The **$10 live test readiness** panel is read-only. It reports account, P&L, market-session, freshness and unresolved-order blockers. Neither market nor limit tickets guarantee a $10 total including fees, and paper fee settings are not a live broker fee estimate. A connected account or passing unit tests do not establish readiness for that real-money test.

Opening a broker review obtains a single-use server token tied to the verified account, endpoint, mode and displayed signal terms. The review expires after at most 90 seconds, or sooner when the signal expires. Changes to those terms require a new review. The server and broker adapter also enforce an absolute submission deadline after slow prechecks, so a quote or signal that expires while waiting cannot be submitted. Current account limits can reduce the suggested quantity or block the order.

**Paper research** has its own Start/Stop, risk preset and **Auto-approve paper ideas** checkbox. To test automatic simulation, scroll to that section, enable Auto-approve, then Start paper research. It uses simulated funds and separate idea IDs, fills and positions. It can run while live scanning is stopped. Its totals, idea-status filter and latest-call history stay independent of the live section. The option affects subsequent scheduled ideas; **Find paper idea** always opens research for manual review. Hold, failed-brain, synthetic and stale-price decisions remain blocked, while fresh directional mock research may simulate fills.

The simulator's controls use `POST /api/paper-research` with `enabled`, `auto_approve`, and `risk_preset`. They never change the main mode or account. Paper positions, fills, loss limits and counts do not authorize or block live orders. Paper reset and Close all paper positions affect only simulated funds and preserve broker records. Section links only scroll the page. New paper automation is off until explicitly enabled.

Paper ideas are sized independently using the paper balance and paper preset. Changing paper auto-approval or its risk settings while a quote is loading revokes the pending automated fill; the idea remains available for review under the current settings.

Live submission writes a durable intent before contacting the broker. Uncertain responses remain pending and prevent another order until reconciliation resolves them; an absent response is never treated as permission to resubmit. Closing quantities reserve existing working sells/covers. The server checks current mode, account, session and limits again at placement.

IBKR reconciliation combines retained trades, completed-order status and execution reports using the permanent broker order ID. A completed status without the required fill quantity and price stays unresolved. Recovery verifies the account before polling, preserves partial fills, and restores interrupted approvals from the durable order/fill records before workers start.

## Research draft (UI banner + code)

> Tomahawk — live trading and separate paper research. Check the verified broker account before approving an order.

- **Draft research tool** — research candidates remain visible. Hold/error/synthetic decisions cannot execute; mock and AVOID candidates cannot enter broker orders. Other quality notes are annotations, not evidence of a trading edge.
- **Alpaca optional** via `broker_alpaca.py` (`ALPACA_API_KEY` / `ALPACA_API_SECRET`). Default `ALPACA_PAPER=true` → `paper-api.alpaca.markets`.
- Raw `/api/broker/*`, `/api/orders`, `/api/alpaca/*`, `/api/ibkr/*`, `/api/tos/*` stay **403** (use desk approve / auto_live).
- **auto_live / live_manual approval:** broker-specific session, size, working-order and loss gates run before submission. Confirmed executions enter only the broker book. Missing broker configuration or failed/unknown submission never falls back to a paper fill.
- **live_manual:** real-money-capable approve-first mode. Scanning can create ideas, but only an explicit Approve action submits the broker order; it never auto-submits.
- **Interactive Brokers:** optional IB Gateway adapter via `BROKER_PROVIDER=ibkr`; default port `4002` is paper. Live requires `IBKR_LIVE=true`, Gateway port `4001`, and the existing explicit `REAL` confirmation.
- The workspace execution bar shows the verified broker venue or local simulation; unverified account modes cannot be approved.
- Enabling `auto_live` requires a matching server-side confirmation; real-money endpoints specifically require typing `REAL` in the UI prompt.
- The desk is unauthenticated on loopback only. If exposed beyond loopback with `TOMAHAWK_HOST` / `TOMAHAWK_ALLOWED_HOSTS`, set a long random `TOMAHAWK_AUTH_TOKEN` and terminate HTTPS at the deployment boundary; remote requests must send `X-Tomahawk-Token` or `Authorization: Bearer`.
- Production startup uses Waitress; set `TOMAHAWK_TRUSTED_PROXY_HOPS=1` only when one trusted reverse proxy terminates HTTPS. Use `TOMAHAWK_DEV_SERVER=1` only for local development.
- Only one server instance is allowed by default. The process lock prevents concurrent JSON read-modify-write corruption.
- Broker position caps include current holdings and remaining buy orders at the current quote or a higher limit price. New broker submissions require a fresh quote and a complete open-order snapshot.
- Partial executions remain tracked until the broker reports a terminal order state. Paper resets and fresh sessions preserve broker orders, fills, and counters; unresolved orders block further submissions until reconciled.
- IBKR account verification selects one individual `U` (live) or `DU` (paper) account, requires USD base currency, and checks `IBKR_LIVE` and standard ports against that account. Set `IBKR_ACCOUNT` if multiple accounts are exposed. Unsupported accounts block execution; unavailable daily P&L blocks new risk. The gate uses IBKR's daily P&L subscription, not an invented previous-equity value.
- Applying an IBKR mode verifies and binds confirmation to the account, endpoint, client ID, and paper/live mode. An account change requires selecting the mode again. Until verification succeeds the desk displays **BROKER UNVERIFIED**. Existing IBKR mode configurations need one new mode confirmation after this update.
- Fund scans (including SPY and QQQ) skip company earnings calendars; missing ETF earnings are not a delisting signal.
- Risk presets are defined in `risk_policy.py` and consumed by both paper and broker gate paths.
- Backtests are screening evidence only; they do not provide statistical proof of edge and should be checked across symbols, periods, and cost assumptions.
- Market-radar results apply centralized liquidity gates and label distribution/parabolic moves as research warnings; these warnings can downgrade a candidate to `WATCH` but never authorize an order.
- Radar provider failures are retained in the response and written to `data/market_radar.log` with rotation.

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
- Side from LLM (`buy`/`sell`) replaces the playbook default when present; `flat` becomes Hold. Directional confidence is blended 50/50 with the playbook score; hold confidence stays attached to abstention.
- **Chat** (`POST /api/llm/chat`) answers free-form day-trade research questions (optional ticker + fresh screener context). Replies are journaled.
- Without a key the desk still runs in **screener-only** mode (`configured: false`); thesis/chat return a graceful `missing_gemini_api_key` error — no crash.

Config toggles (also via `POST /api/config`): `llm_enabled` (default true), `llm_on_scan` (default true), `llm_model` (optional override).

## Modes

| Mode | Behavior |
|------|----------|
| **live_manual** | Main broker workspace. Each actionable idea requires order review. Account and current limits are checked at submission. |
| **auto_live** | Explicitly enabled broker automation with current account and risk gates. Real-money activation requires REAL confirmation. |
| **Paper research controls** | Independent simulation alongside the main broker mode; manual review or optional auto approval of scheduled paper ideas. |
| **manual / auto_paper** | Legacy local-only modes remain supported for existing installations. They cannot convert an explicitly live idea into a paper order. |

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
- **Alert cooldown** deduplicates repeated pending ticker/verdict alerts (`alert_cooldown_sec`, default 300).
- **Risk cockpit** exposes data health, exposure, slippage/fee friction, and a conservative paper-evidence promotion checklist.
- **News intelligence** merges Finnhub, Yahoo, public Google News RSS and GDELT discovery, plus optional Benzinga headlines, removes near-duplicates,
  normalizes publication times, and ranks explainable event tags (earnings, guidance, corporate actions,
  regulatory, capital, analyst, executive, and macro). Headlines are display-only and never gate fills.

## Persist

All under `./data/*.json`:

- `config.json` — mode, preset, watchlist, kill-switch, paper equity, slip
- `signals.json` — signal queue
- `ledger.json` — paper cash, positions, fills, daily stats
- `journal.json` — action log

## Stack

Flask + Jinja + vanilla JS + yfinance/pandas/requests. Dark desk UI (`--bg #0b0f17`, etc.).

## Windows quick start

Double-click the **Daytrade Signal Desk** desktop shortcut. It runs `Launch.vbs` and the shared `Start-Tomahawk.ps1` startup flow, including environment setup when needed, then opens the browser.

(`Launch.bat` uses the same `.venv` folder.)

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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5056`

## API sketch

- `GET /api/state` — config, signals by status, ledger, journal
- `POST /api/config` — mode / preset / watchlist / optional kill-switch
- `POST /api/signals/generate` — force one watchlist scan (optional `{"ticker": "AAPL"}`); the idea always waits for Approve
- `POST /api/signals/<id>/review` — read-only broker verification; returns an expiring review token and the verified identity
- `POST /api/signals/<id>/approve` — workspace-specific approval; broker orders require a valid `review_token`, plus `ack_ticker` for a real-money account, followed by current execution gates; no paper fallback
- `GET /api/data-quality` — provider readiness, data-file health, and fallback policy
- `GET /api/risk/cockpit` — current exposure and permission-to-trade checks
- `GET /api/execution/realism` — sampled slippage, fees, and paper/broker execution counts
- `GET /api/strategy/evidence` — evidence/promotion gate; never enables live trading
- `POST /api/signals/<id>/reject`
- `POST /api/ledger/reset`
- `GET /api/health` — includes `llm: {configured, model, enabled}`
- `GET /api/llm/status` — `{configured, model, provider:"gemini"}` (never returns the key)
- `POST /api/llm/chat` — `{message, ticker?}` → `{ok, reply, ticker, analysis_snippet?}`
- `POST /api/llm/thesis` — `{ticker}` → analyze + structured thesis

## Broker adapter summary (IBKR or Alpaca)

1. The primary workspace is live trading; the saved execution mode is preserved. Broker modes require verified account configuration; local paper simulation has separate controls.
2. **auto_live** on a real-money endpoint asks you to type REAL; the server rejects the transition without that confirmation. On Alpaca paper, a confirm dialog is shown.
3. `live_broker_place_order` routes to the selected broker, checks the reviewed identity and submission deadline, and journals the result. Missing configuration blocks execution.
4. Desk **never** dual-books: broker success skips local `paper_fill`; broker fail is reported as a failure (no paper fallback).
5. `ALPACA_PAPER` defaults **true**. `false` → live money endpoint; UI shows **LIVE ENDPOINT**.
6. Close all paper positions affects simulated positions only. It does not cancel or close broker orders or holdings.
7. Raw broker HTTP routes return 403; banner always visible.

## Safety guards (2026-09-22 review fixes)

- **Local-only by default.** Binds `127.0.0.1:5056`. Requests with a foreign `Host`, a foreign `Origin`, or `Sec-Fetch-Site: cross-site` get 403, so a web page you visit can't switch modes or approve trades. To expose on a LAN on purpose: `TOMAHAWK_HOST=0.0.0.0` plus `TOMAHAWK_ALLOWED_HOSTS=192.168.x.y:5056`.
- **Corrupt data fails closed.** A BOM is tolerated. An unreadable `data/*.json` is backed up as `*.corrupt.<timestamp>.bak`, never overwritten, and trading is gated until it's repaired and the app restarted (`corrupt_files` in `/api/health` and `/api/state`; UI toast).
- **Broker honesty.** Failed broker orders are never booked as local paper trades. Only verified broker execution quantities and prices are recorded, and partial fills remain tracked until terminal status is established. Orders still pending after `BROKER_FILL_WAIT_SEC` go through supported cancellation/reconciliation. Broker fills and counters remain separate from paper. Daily loss gates use IBKR's P&L subscription or Alpaca's account equity change, as appropriate. Gate+submit is serialized.
- Startup resolves interrupted `approving` signals against durable pending orders and recorded fills before starting workers. An approval with neither is rejected and requires a fresh idea; an uncertain broker submission remains pending. Starting a fresh paper session requires confirmation before archiving open positions; archived positions are marked still open rather than realized exits.
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
- Social intelligence is off by default. Set `social_enabled: true` to poll public WSB JSON; it is read-only, rate-limited, and research-only.
- Gemini cost recorded for every billed call from real token counts; loop totals reset at NY midnight. `tzdata` added to requirements.

**Screen**
- The full desk is shown on one page. Simple mode is deferred; the existing implementation remains dormant.
- Plain language throughout ("Skip AAPL for now. The AI is fairly sure (about 8 in 10)…"), money shown with +/− and ▲/▼, paper = violet, real money = orange with a warning bar and a typed confirmation.
- Approve window shows the maths and whose money moves; broker positions shown separately; unconfirmed broker fills are labeled; "Today" shows closed and open P&L.
- "Live · 2s / Not updating" freshness in both modes with a banner when data goes stale; the "(2) waiting" count shows in the browser tab.
- Order confirmation and the tutorial remain dialogs. Settings and the weekly report are inline; focus rings and reduced-motion preferences are respected.

The desk uses a plain single-page layout. Light and dark colors live in the tokens at the top of `static/app.css`; the header theme button keeps your choice. No generated stylesheet or frontend build step. Edit the four section templates under `templates/desk/`. See [UI structure](docs/UI_STRUCTURE.md) and the [trading platform review](docs/TRADING_PLATFORM_REVIEW.md).

The subsequent [workbench implementation](docs/WORKBENCH_IMPLEMENTATION.md) adds reviewed DAY limit orders, explicit cancellation review, linked daily candles/news, saved watchlists, filtered CSV journals, recorded-policy evaluation and reproducible paper stress experiments. It documents the remaining broker qualification and order-capability limits; simulated tests do not establish live execution readiness.

The [Moss implementation](docs/MOSS_IMPLEMENTATION.md) adds a Stoic-inspired research companion, retained notebook and fitted research-ranking parameters, fractional local paper sizing, a fee-aware scenario calculator, glossary and saved automation rehearsals. See [manual live enablement](docs/MANUAL_LIVE_ENABLEMENT.md) for exact existing flags and activation steps. Moss's test plan does not arm or configure the separate automatic broker mode.

The [Moss paper workday](docs/MOSS_WORKDAY.md) runs local paper research throughout each exchange session, with persistent evidence, daily reports, configurable limits and an optional empirical-Bayes personality. A separate read-only IBKR execution journal records and analyzes actual trades, partial fills, corrections and reported fees/P&L. Configure it under **Moss → Paper workday**; use **Review actual trades** for real execution history. Keep the app and PC running for scheduled work.

## Slow-bleed guard & SPY benchmark
- **Slow-bleed guard:** if your last 20 closed paper trades (at least 10) are net negative **after fees**, the desk stops opening new trades and shows "Paused to protect your money" with the numbers. Exits keep working. **Resume anyway** restarts the count (`POST /api/bleed/resume`). Tuning: `BLEED_WINDOW` / `BLEED_MIN_TRADES` in app.py; turn off with `bleed_guard_enabled: false` in config.
- **You vs. SPY:** at Start checking the desk records SPY's price; the stage and the end-of-day recap show your return next to simply holding SPY with the same cash.
- **Daily after-hours recap:** `/api/state` includes `daily_recap`, and `/api/report/daily` returns a descriptive close-of-day summary with net P&L after fees, fills, wins/losses, decisions, blocked calls, open positions, and tickers observed. It never authorizes overnight or live orders.
- **Automation health:** automatic paper-loop status includes the last completed cycle, last error, consecutive error count, and bounded retry delay. Unexpected failures back off up to 60 seconds instead of retrying in a tight loop.
- **Opportunity priorities:** pending and recent opportunities use one auditable score based on verdict, confidence, lateness, execution/regime flags, attention context, and review status. Each row exposes `priority_score`, `priority_tier` (`act_now`, `review`, or `research`), and reasons.
- **Readiness summary:** `/api/state` exposes `readiness` with explicit research, paper, and live capabilities, blockers, cautions, and the next priority. It never claims live readiness or changes broker mode.

## Trading-bot ideas (from YouTube review, 2026-09-22)
- **Trailing stop:** "Trailing stop %" in the Approve window — the stop follows the price up (never down).
- **Lessons memory (`lessons.py`, `data/lessons.json`):** every scored call becomes a one-line lesson; before deciding, the AI sees this desk's own record for the same kind of setup and ticker. `GET /api/lessons`.
- **Memory health:** setup retrieval includes bounded recent results (last 12 matching lessons), ticker history, and total memory depth so stale aggregate history is not treated as current evidence.
- **Market-capture funnel:** `/api/state` exposes `market_capture` with actionable-call, fill, horizon-outcome, capture-rate, blocked-call, and missed-favorable-call counts. These are descriptive research measures only; intraday MFE/MAE path capture is intentionally reported as unavailable until high/low path data is persisted.
- **Midday check:** once a day after 12:00 ET, positions down 7%+ are closed and positions up 3%+ get their stop raised to the price paid (`MIDDAY_CUT_PCT`, `MIDDAY_BREAKEVEN_PCT`; off with `midday_check_enabled: false`).
- **Report card:** header button — last 7 days, grade A–F, profit after costs, % of AI calls right, worst setups. `GET /api/report/weekly`.
- **Scanner signals:** 5-minute relative volume (vs. the same time on prior days), VWAP and its slope, distance from VWAP / day's high in ATRs, bid-ask spread vs. ATR (market hours only). They can downgrade PASS → WATCH and are shown to the AI.
- **Social/news pulse:** when enabled, `/api/state` and `/api/research/social` combine bounded public Reddit communities (`wallstreetbets`, `stocks`, `investing`, `options`, `Daytrading`, `Shortsqueeze`), Stocktwits symbol streams for the watchlist, and configurable public RSS feeds. Defaults include CNBC Markets, MarketWatch Top Stories, NYT Business, and BBC Business; override with `SOCIAL_RSS_FEEDS`. They expose ticker attention, unique-author count, sentiment split, source counts, links, and quality warnings. Social/news chatter never creates a PASS, changes sizing, or bypasses execution gates.
- **Claude (`claude_brain.py`):** choose "Claude" as the brain, or tick "Claude head-to-head" to have Claude answer silently next to your main AI; the report card shows who was right more often on the same decisions. Needs `ANTHROPIC_API_KEY` in `.env`. Model `CLAUDE_MODEL` (default `claude-opus-5`, ~$5/$25 per million tokens; costs for other current models, including cache writes/reads, are in `claude_brain.PRICES`), `CLAUDE_EFFORT` (default `low`; ignored for Haiku 4.5, which takes no effort or adaptive thinking). On Claude Opus 5 and Fable 5/5.1 a safety decline is re-run server-side on Anthropic's recommended fallback (`fallbacks: "default"`); `CLAUDE_FALLBACKS=0` turns that off, and a final refusal stays a hold.
- **Past-data test (`backtest.py`):** "Test the rules on past data" in the report card replays the screener's PASS rule on ~2 years of hourly prices with a learning/check split and a no-filter baseline. `GET/POST /api/backtest`.

## Auto upkeep (2026-09-24)

`tools/desk_upkeep.py` runs from two Windows scheduled tasks registered by
`tools/Register-AutoUpkeep.ps1 -Register` (remove with `-Unregister`). Register them only on the PC
that runs the live desk.

- **Tomahawk-Desk-Watchdog** (every 5 min): if `/api/health` is unreachable twice 20s apart, runs the
  normal launcher headless for the desk only (`-NoBrowser -NoDialogs -NoBroker`, 10-minute cooldown).
  If the desk is up but the broker socket is down, it asks the desk (`POST /api/broker-ensure-gateway`
  with `automatic: true`) instead of opening Gateway itself. Never stops or restarts a healthy desk.

### When the desk reopens IB Gateway

Unattended callers (the watchdog, the Moss agent's tick, the daily `Ensure-IBGateway.ps1` task) reopen
Gateway only when it had signed in and served the API since its last launch and has then been gone for
90 seconds (so Gateway's own auto-restart is not raced). A login window you close, or one that exits
without signing in, stays closed: the desk shows "closed before it signed in" and waits for you to use
**Ensure Gateway** (Live trading section) or the desktop shortcut. Nothing ever starts a second Gateway while one is running
(`ibgateway.exe`, `tws.exe`, or a Gateway/TWS `java.exe`/`javaw.exe` such as IBC), and launches share a
180-second cooldown. Launch and sign-in times are kept in `data/gateway_launch.json` (`at`, `source`,
`api_seen_at`).
- **Tomahawk-Desk-Upkeep** (daily 16:40): moves root `_*` backups/scratch and `data/_*` probe dumps into
  `_archive/`, deletes `_archive` items untouched for `UPKEEP_BACKUP_DAYS` (default 30), rolls
  `data/*.log` over `UPKEEP_LOG_MAX_MB` (default 25) when not in use, clears `__pycache__`, runs SQLite
  `quick_check` + WAL checkpoint + `optimize`, runs the test suite in an isolated temp data dir, and
  writes `data/upkeep/last_daily.json`. Problems are flagged on one line in `TASKS.md` and cleared
  automatically once healthy.

Upkeep never edits config, orders, positions, sessions, policies or broker settings, and never logs
Gateway out. Check status with `.venv\Scripts\python.exe tools\desk_upkeep.py status`.

`tools/Install-DeskShortcut.ps1` creates the Desktop and Start menu shortcuts with the wheel icon
(`static/desk.ico`, rendered from `static/nadzeel-mark.svg`).

### Review fixes (same day)

- IBKR delayed quotes (market data type 3/4) are labelled as delayed. When IBKR is delayed, the desk
  prefers a real-time quote under 20s old (the live agent requires under 10s so the order window is not
  shortened) and otherwise keeps the IBKR quote, so live ordering is never blocked.
- Auto live option/BAG limits and risk use the option premium, not the underlying stock price.
- `place_from_desk_order` runs on the IBKR API owner thread again, like every other broker call.
- The live agent AI budget counts only live-agent research (`model_usage.json` now records a per-scope
  total), so paper Moss spending cannot pause live research.
