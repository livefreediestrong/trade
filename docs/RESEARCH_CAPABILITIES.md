# Research capabilities review

Reviewed September 24, 2026 on the `claude/trader-repo-review-1j60gj` branch. This covers what the desk can research, what actually feeds a trading decision, what only informs you, and the gaps worth closing next.

## What decides a trade

A broker agent (Fox) buy passes through these steps, in order:

1. **Screener** (`screener_logic.py`). Price, first-hour relative volume, 10/20/50-day averages, sector ETF, intraday ATR and spread, entry lateness (early, fair, late, chasing) and the next earnings date. Data comes from yfinance, then Stooq, Yahoo and Finnhub as fallbacks, with IBKR quotes for execution.
2. **Deterministic gates** in `generate_scan_signal`:
   - broad-market regime (SPY/QQQ from the market radar);
   - execution quality (spread over 8% of ATR);
   - lateness;
   - **reward to risk after costs** (new);
   - **the setup's own scored record** (new);
   - **WSB crowding caution** (new).
3. **Brain** (`llm_trader.py` Gemini, `claude_brain.py` Claude, optional head-to-head). It answers a horizon question from the screener facts, the desk's own track record for similar setups (`lessons.py`, built from numbers only) and research evidence. The optional advisory panel and shadow auditor can shrink or veto a decision. Confidence is now deterministic per setup.
4. **Agent gates** (`live_agent.py`):
   - policy and account identity;
   - fresh quote;
   - minimum confidence;
   - daily attempt and budget caps;
   - **scheduled-event pause and earnings** (new).
5. **Desk broker gates** (`app.py`): session, daily loss, trade count, the **give-back guard** (new), and worst-case sizing for options.
6. **After the fill:** **protective exits** (new). The planned stop and target, a breakeven stop, an optional time limit, and a sell before the close.

Everything else below informs you (and Changing Woman's notes) but cannot start or enlarge a trade.

## Inventory

| Area | Modules | Sources | Needs a key? | Role |
| --- | --- | --- | --- | --- |
| Prices and bars | `data_sources`, `polygon_client`, `broker_ibkr` | yfinance, Stooq, Yahoo quote, Finnhub, Polygon, IBKR | Finnhub, Polygon optional; IBKR market data subscription for real-time | Decision input |
| Discovery | `market_radar`, `market_universe`, `sale_scanner`, `market_discovery` | Alpaca/Polygon snapshots or Yahoo; Nasdaq listing directory | Optional | Chooses what to research |
| Company news | `news_stream`, `news_intelligence` | Finnhub company news, Yahoo, GDELT, Benzinga (stub) | Finnhub recommended | Display, timeline, reaction study |
| Market news | `companion_news`, `market_watch` | 10 publisher/agency feeds; Google News; your RSS feeds | No | Display; Changing Woman's commentary |
| Filings | `edgar_client` | SEC EDGAR | `EDGAR_USER_AGENT` with email | Display |
| Social attention | `buzz_sources`, `social_intelligence`, `x_watcher`, **`wsb_monitor`** | Reddit (OAuth), Stocktwits, RSS, X API | Reddit and X keys | Display; **WSB crowding caution** |
| Trends | `market_watch` trend scan | Reddit, **WSB live**, Stocktwits, Yahoo trending, Google Trends, Google News, X | Reddit/X optional | Display |
| Calendar | `macro_calendar`, **`market_events`** | FOMC dates, FRED, Finnhub/Yahoo earnings, **Fed calendar, BLS calendar, presidential schedule, your events** | FRED optional | Paper macro size cut; **agent event pause and earnings guard** |
| Options | `options_desk`, `options_flow`, `auto_live_options` | IBKR chains; Quiver, Unusual Whales, Finnhub flow | Flow sources paid | Option research, paper book, gated live options |
| Learning | `lessons`, `research_learning`, `session_track`, `agent_review`, `moss_policy` | The desk's own scored decisions | No | Track record in prompts; evidence gate; paper sizing |
| Testing | `backtest`, `quant_engine`, `research_studio`, `research_metrics` | Historical hourly bars; paper kernel | No | Offline evaluation |

## Strengths

- **Clean separation.** Paper and broker books never mix, and research modules are display-only unless a gate says otherwise. The new gates only reduce risk.
- **Honest data handling.** Timestamps are checked for age and future dates. Mock or synthetic data is refused for execution. Delayed quotes are labeled.
- **Self-scoring.** Every research decision is scored after costs at a fixed horizon (`session_track`). Those outcomes feed the track record the brain sees, and now a gate that stops a losing setup.
- **Breadth of attention sources**, each with per-source status, so a missing source is visible rather than silent.

## Gaps and recommendations

Ranked by expected effect on real-money results.

1. **Real-time quotes.** Without an IBKR real-time market-data subscription, quotes are delayed. The agent then falls back to other real-time sources, and protective exits wait for a fresh quote. Subscribe to US equity real-time data for the account.
2. **Correlated exposure.** The agent can hold several positions in the same sector or theme. Add a per-sector or per-correlation cap (the sector ETF is already on each signal).
3. **Breaking news on held positions.** Company headlines (`news_stream`) are shown but not tied to Fox's positions. A next step: Changing Woman flags a new headline on a held ticker, and optionally a halt or LULD pause detected from IBKR tick data.
4. **Historical test of the full decision.** `backtest.py` tests only the screener's PASS rule. The brain's decisions are scored only going forward. Replaying recorded research decisions against later bars, including the new cost and evidence gates, would show whether the gates help before relying on them.
5. **Short interest and float for crowded names.** WSB crowding is now a caution. Adding short interest and float (via Finnhub or Polygon) would separate squeeze risk from ordinary attention.
6. **Pre-market context.** The screener starts at the open. Pre-market gaps and overnight news for watchlist names are not summarized. A pre-open brief (gap %, overnight headlines, today's events) would suit Changing Woman's morning chores.
7. **Sentiment quality.** Social and WSB lean use keywords. Keep them as attention measures, not direction signals, unless a scored study shows otherwise. The evidence gate framework could measure that.
8. **Calendar coverage.**
   - Census (retail sales) and BEA (GDP, PCE) releases are not in the calendar yet; both publish release schedules.
   - Unscheduled announcements can only be added by hand under Settings.

## Done in this branch

- Cost-aware reward to risk, the evidence gate, deterministic confidence and the give-back guard.
- Protective exits, the scheduled-event pause and the earnings guard for the agent.
- The live WSB thread monitor with a crowding caution, and WSB in the trend scan.
- Fox as the agent's voice and Changing Woman's chores and reasoning, in the Today at the desk panel.
- Market watch, the X watcher and the internet-wide trend scan.
