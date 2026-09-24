# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-local Flask trading desk (names in code: nadzeeɫ, Tomahawk, Daytrade Signal Desk) that trades a **real brokerage account** through IB Gateway (`broker_ibkr.py`) or Alpaca, alongside a separate simulated paper book. Changes to order, sizing, risk or broker code move real money: read the relevant `docs/*.md` before changing them, and keep every gate fail-closed.

## Commands

```bash
# Python tests (conftest sets TOMAHAWK_NO_BG=1, a temp TOMAHAWK_DATA_DIR, and mocks broker creds)
python -m pytest -q
python -m pytest -q tests/test_options_risk_fixes.py -k bag_close   # one test
# Front-end tests: standalone Node scripts (vm + stub DOM), no framework
node tests/test_live_ticket_ui.cjs
for f in tests/*.cjs; do node "$f" >/dev/null || echo "FAIL $f"; done
# Lint (no project config; pyflakes on files you touched)
python -m pyflakes market_watch.py
# Run locally without scanners/broker threads (Windows uses Launch.vbs -> Start-Tomahawk.ps1)
TOMAHAWK_NO_BG=1 TOMAHAWK_DEV_SERVER=1 TOMAHAWK_DATA_DIR=/tmp/deskdata python app.py   # http://127.0.0.1:5056/
```

In Claude Code on the web, `.claude/hooks/session-start.sh` creates `.venv` with requirements plus pytest/pyflakes and puts it on `PATH`. `tests/test_launcher.py` runs PowerShell and is skipped off Windows.

## Architecture

**`app.py` is the core (~9k lines):** config/ledger/signals/journal persistence (JSON files under `DATA_DIR`, written atomically via `_save_json`; unreadable files land in `_CORRUPT_PATHS` and block broker execution), the scan/decision loop, all execution gates, and most routes. Feature modules attach with `module.register(app, sys.modules[__name__])` near the end of `app.py` (live_ticket, live_agent, options_desk, market_catalog, market_watch, …) and call back into `app` for gates and storage.

**Two workspaces, never mixed.** Every signal is `live` or `paper` (`signal_workspace`). Paper state (local ledger, paper loop, options paper book) must never authorize or block broker orders, and broker data never fills the paper book. Config `mode` is `manual | live_manual | auto_live` (plus paper automation settings).

**Order path to a real broker:** signal or ticket → `execute_gated_broker_or_paper()` (`_BROKER_SUBMIT_LOCK`; verified broker identity must equal `cfg["broker_identity"]`; `_broker_session_gate` for every order and `_broker_risk_gate` (daily loss, trade count) for non-reducing ones; quantity/terms from `order_terms.canonical_*`) → `broker_router` (dispatches to `broker_ibkr` or `broker_alpaca` via `BROKER_PROVIDER`) → adapter re-checks positions, working orders and `_submission_risk_error` right before `placeOrder`. Unresolved submissions are recorded in `ledger["pending_broker_orders"]` first and reconciled; `recover_interrupted_approvals()` restores them at startup. Server-issued review tokens and `valid_until` deadlines bind a reviewed order to its exact terms.

- `order_terms.py`: canonical equity/option/BAG terms, limit precision, `option_notional` (premium) vs `option_max_loss` (worst case, used for sizing and size caps).
- `broker_ibkr.py`: one `ib_insync` connection on a dedicated API thread (`@_on_api_thread`); daily-P&L subscription drives `risk_ready`; Gateway launch policy lives in `ensure_gateway` (unattended callers pass `automatic=True`).
- `live_agent.py` (Moss broker agent, `auto_live`) and `auto_live_options.py` (stock PASS → OPT/BAG conversion) feed the same gated path; `live_ticket.py` is the user-directed ticket.

**Research/information modules are display-only** and must not become execution inputs: `news_stream`, `social_intelligence`, `buzz_sources` (Reddit OAuth; public JSON only via `REDDIT_PUBLIC_JSON=1`), `x_watcher` (X API, budgeted), `market_watch` (headlines + trend scan), `companion_news`, `market_radar`, `edgar_client`, `macro_calendar`. `quant_engine.py`/`quant_risk.py` are an isolated paper-only kernel (`docs/QUANT_KERNEL.md`).

**Front end:** Flask/Jinja + vanilla JS, no build step. Routes render split pages `templates/pages/{overview,auto,paper,research,ticket,settings}.html` (`/` = overview); `/desk/all` renders the legacy `index_all.html`; `templates/index.html` is not routed (tests read it). Panels are partials in `templates/desk/`. Each page loads most scripts, so **scripts must tolerate missing elements** (return early or use `?.`); one unguarded `getElementById(...).addEventListener` aborts the whole script on other pages. Script/style tags carry `?v=` cache-busting versions in all six page templates plus `index.html`/`index_all.html`; bump them when a file changes. Live state reaches scripts via `desk:state` window events from `app.js` (SSE with polling fallback).

**Local-only access guard** (`_local_only_guard` in `app.py`): requests must use an allowed Host, come from loopback (or carry `TOMAHAWK_AUTH_TOKEN` over HTTPS), and unsafe methods reject cross-site `Origin`/`Sec-Fetch-Site`. Tests must use `base_url="http://127.0.0.1:5056"`.

**Launcher and upkeep (Windows):** `Launch.vbs`/`Launch.bat` → `Start-Tomahawk.ps1` (creates `.venv`, verifies the running desk by `/api/health` identity, offers restart when `code_version` reports stale code, opens Gateway only on user launch). `tools/desk_upkeep.py` runs from scheduled tasks and never changes trading state.

## Conventions

- Comments marked **"owner decision"** record deliberate product choices (e.g. delayed stock quotes never block live orders; upkeep never touches trading). Don't reverse them without being asked.
- Fail closed: missing/unknown data (P&L, quotes, identity, timestamps) blocks new risk; reducing orders have narrow, explicit exceptions.
- Money math uses `Decimal`; option limits are whole cents; timestamps are timezone-aware and future dates are rejected.
- Tests that touch `app` isolate storage by monkeypatching `desk.CONFIG_PATH`/`LEDGER_PATH`/`SIGNALS_PATH`/`JOURNAL_PATH` to `tmp_path`, and mock `broker_router`/adapter functions; no test may reach a real broker.
- Feature docs live in `docs/` (linked from `README.md`); update the matching doc when behavior changes. `.env.example` documents every setting; `data/`, `.env`, `deploy_out/` and debug scripts are gitignored.
