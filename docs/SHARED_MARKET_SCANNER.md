# Shared US market discovery — September 24, 2026

The desk uses one persistent market scan for completed daily history and separate feature-specific checks for quotes, options, execution and research outcomes. Discovery is not execution eligibility. Existing user watchlists, trading modes, AI budgets and risk limits are not rewritten.

## What is connected

| Feature | Shared input | Independent checks retained |
| --- | --- | --- |
| Stocks on Sale | Full supported directory and adjusted daily-price observations | Sixty completed sessions, corporate-action adjustment, exact identity and latest completed session |
| Momentum / volume activity | Same rows, alternate rankings | Twenty-session return / completed-day relative volume; no intraday claim |
| Moss Broad US workday | One bounded scanner-candidate slot, alongside focus and rotating directory candidates | Existing current intraday data, liquidity, volatility, model/cost budgets, paper sizing and risk gates |
| Moss notebook | Up to two scanner candidates within its four-symbol attention budget | Fresh historical observations; existing research-model conditions; scanner provenance retained in the brief |
| Radar | Cached directory rotation plus scanner names if the quote-provider fallback is reached | Fetches its own current quotes and applies the existing radar filters; daily prices are never passed off as quotes |
| Charts and news | Selected underlying symbol | Destination loads its own chart, headlines and saved theses |
| Options research | Selected underlying, clearing previous expiration, strikes, chain and approval | Broker chain and current option quote checks; selecting a symbol submits nothing |
| Cost calculator / research chat | Selected symbol only | Price assumptions are cleared in the calculator; chat is not automatically sent |
| Live and paper interfaces | Shared coverage/access link | Order reviews, holdings, fills and P&L remain tied to their original verified sources |

Research evaluation and backtests continue to use their captured evidence. A present-day scanner result is not inserted retroactively into historical tests. The current listing directory has survivorship limitations for backtesting.

## Scan and coverage

The Nasdaq-traded directory supplies supported US exchange-listed common equities, ETFs and ADRs. OTC, preferred shares, warrants, rights, units, debt securities and non-US listings are excluded. Directory refresh and history-provider coverage are reported separately. The first real run loaded 11,645 listings; this is a dated directory count, not a guaranteed permanent total.

`sale_scanner.py` runs one daemon worker with at least one second between history requests, reusing the existing Yahoo history validation. Progress is stored transactionally in `data/sale_scan.sqlite3`. Closing the browser leaves the worker running while the app and PC stay on. App restart recovers a paused checkpoint; Resume explicitly continues it. A new scan refreshes and freezes its directory, then replaces the previous pass only after obtaining a usable directory. No additional scheduler or paid model call is created.

The pass freezes its latest completed exchange session. A new completed session stops further scanning and labels the retained results dated. Small/new/stale/malformed histories are exclusions, not successful observations. Network failures remain provider gaps. HTTP 401/403/429 or three consecutive provider errors pause the pass with a five-minute cooldown. Failed rows can be retried by Resume, without repeating successful rows. Rate limits or data availability may prevent full coverage.

Results paginate at 40 rows. Filters are applied to all retained rows, not only the visible page. All-checked ranks by dollar turnover; momentum by positive 20-session return; activity by completed-day relative volume; pullbacks by distance below the adjusted 60-session median. An adjusted historical price drop is not fair value. Annualized volatility uses sample standard deviation of 20 daily log returns times sqrt(252), expressed in percent.

GET `/api/markets/scanner` reads status and results. Query keys: `lens` (pullbacks/momentum/active/all), `kind` (all/stock/etf), `q`, `minimum`, `liquid=1`, `issues=1`, `page`. POST the same path with JSON `action` of `start`, `resume` or `pause`. `/api/research/sale/market` is a compatibility alias. The existing up-to-12-symbol quick check is independent of the full-directory pass.

`market_discovery.py` provides read-only, current-session, liquid candidate context to consumers, returning no candidates for missing, corrupt, future or older-session data. It never contacts a broker, fetches prices or writes trading configuration.

## Presentation

Main module accordions were replaced with permanent sections and headings. Compact borders and a restrained left edge distinguish tools. Research forms use two columns where space permits; results and source lists have bounded scrolling areas. Secondary prose still uses the existing hover/focus help. Existing select inputs that choose a value are separate from module visibility. Source loading now follows visibility rather than a removed accordion toggle. Journal keyboard focus returns to its section heading.

## Verification

195 unique Python tests passed across scanner, research studio, market universe, Moss workday, companion, workspace, market operations and options suites. Existing eventkit event-loop deprecation warning remains. Four JavaScript controller suites passed: scanner, options, scene motion and trading guidance. Scanner tests cover a 106-listing pass, class-share mapping, pagination/filters, duplicate starts, pause during an in-flight request, persisted recovery, provider cooldown, session rollover and future/mismatched data. Shared-consumer tests check bounded discovery and fresh-quote radar handoff. Options tests exercise stale review invalidation after selecting a different underlying.

The running browser exercised the real full-directory scan, real historical rows, pause and resume, all-checked view and options selection without submitting a chain request or trade. All 44 main modules are visible and no main `details` elements remain. Desktop 1500 px and phone 390 px checks found no page-wide overflow. Book-library visibility loaded all 12 sources and populated the hypothesis selector. These checks do not establish a completed full-market pass, working broker connectivity or a profitable strategy.

The app backend was restarted once to deploy the scanner. Gateway was not restarted. Production config SHA-256 remained `25ACFF6CF6863C28224FC6FE0F591D6BD735C8733A92942025F5725927408155` across deployment. Mode remained `live_manual`; the broker was disconnected/unverified during the check. No real-money order, cancellation, arming action or trading configuration write was performed.
