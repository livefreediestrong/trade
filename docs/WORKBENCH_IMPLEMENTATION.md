# Workbench implementation and qualification boundaries

Updated 2026-09-23. Builds on the ten-platform review. The application remains Flask/Jinja plus plain JavaScript, with no new frontend dependencies or build process.

## Delivered workflows

| Area | Implemented behavior | Evidence / remaining boundary |
| --- | --- | --- |
| Order terms | Manual reviews default to whole-share DAY limits. Market remains an explicit choice. The server binds type, price and quantity to the account-bound review token. Changing terms invalidates approval; a risk-driven quantity change requires another review. | Real adapter objects/request bodies and endpoint gates exercised with broker I/O replaced. This does not establish exchange acceptance. |
| Costs | IBKR what-if commission estimate in an explicit review; missing estimates stay unknown. The entry notional bound and fee uncertainty are separate. | What-if is an estimate, not a guaranteed fee ceiling. The $10 all-in test remains blocked. |
| Working orders | DAY limits stay working after the initial wait; existing durable intent/restart reconciliation continues. Cancellation has its own short-lived account/order-bound review and ticker acknowledgement. Partial fills remain recorded even when the remainder is canceled. | Cancel-and-replace is a deliberate sequence: verify terminal cancellation, then obtain a new idea and new review. No automatic replacement or bulk cancellation. |
| Health | Compact account, P&L feed, last value age, Gateway response age, quote timestamp/age, and model/block reason. Paper status widgets live in Paper research. | Account readiness is only one order prerequisite. A missing daily-P&L callback is never replaced by zero. |
| Linked research | Watchlist dropdown, typed symbol and spark selection link daily candles, volume, headlines and retained theses. Keyboard bar inspection and 30/60/120-bar windows. Sequence guard/abort discards superseded symbol responses. | Historical daily bars, not a streaming intraday chart. Data source and possible incomplete daily bar are labeled. No synthetic market bars. |
| Paper experiments | Immutable capture of recorded decisions/inputs/outcomes, policy settings, model/strategy version, capital, horizon, spread, fees, adverse-delay bps and deterministic partial-fill percentage. Hash-verified replay and JSON export. | Independent fixed-capital scenarios; summed scenario P&L is not a portfolio return. Recorded policy outputs are replayed, not a historical rerun of today's model. Missing, routed, mock, errored or wrong-horizon outcomes are excluded. |
| Journal | Separate order/fill rows, workspace/symbol/status/strategy/time filters, matching CSV export and formula-injection protection. Broker fills are cumulative per order; missing fees remain blank. | Retained desk history, not a complete broker statement. Current positions remain in the separate broker/paper books. |
| Evaluation | Cohorts by actual model, prompt version, workspace and horizon; sample count, coverage, abstention, after-cost positive rate and Brier score. | Descriptive retained observations. Self-reported confidence is not a calibrated probability; small samples are labeled. Existing screener split/walk-forward tests remain separate. |
| Convenience | Saved watchlists with explicit Apply, settings search, navigation-only hotkeys, collapsible health/details. | No hotkey submits, skips or cancels an order. |

## Deliberately unavailable capabilities

The capability matrix in `order_terms.py` explicitly disables fractional shares, attached broker brackets and a guaranteed all-in budget. They are not enabled by the presence of similar features in TWS or another broker's native app. A future bracket path must prove parent/child acknowledgement, rejected or missing children, partial parent/child fills, cancel races, restart discovery and account isolation on a broker paper account before being offered for live use. A stop also does not guarantee its execution price.

IBKR implementation references: [Order fields, what-if and transmission](https://www.interactivebrokers.com/docs/tws-api/ref/order), [bracket transmission sequencing](https://interactivebrokers.github.io/tws-api/bracket_order.html). Alpaca's [order documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca) distinguishes order acceptance, fills, bracket activation and cancellation races. These sources support the capability boundary; this implementation does not claim native-platform parity.

## Cleanup and performance

- Background browser tabs close their stream and pause state/decision polling; returning forces a refresh before approval can look fresh.
- Decision polling coalesces concurrent requests, including slow fallback connections.
- Large JSON snapshots negotiate gzip at level 1. The decoded payload stays byte-identical; gzip opt-out and streaming/SSE exclusion are tested.
- Informational provider cache tracks the identity of its completed result. A new symbol never receives the previous symbol's news/filing context while refresh is running.
- Unloaded decorative scripts and the old standalone mock moved from `static/` to `docs/legacy-ui/`. The working equity chart remains.
- New functionality stays in `desk_workbench.py`, `order_terms.py`, `broker_controls.py`, `static/workbench.js`, `static/order_controls.js` and small Jinja partials. Existing broker/P&L logic was not replaced with a framework.

## Validation

Focused tests: `tests/test_workbench.py`, `tests/test_order_limits.py`, existing execution, P&L and workspace tests. Full-suite and browser results are recorded in the task handoff. The browser test server uses temporary data and forbids outbound sockets/broker calls; its chart is explicitly labeled as a fixture. Production verification uses read-only requests and no real-money submission.

Final result: 409 tests passed, followed by 15 passing workspace checks after the final template edits. Isolated browser capture/replay and watchlist workflows passed; the production daily chart/news and journal loaded without browser console errors. A production JSON snapshot used 59,112 compressed bytes versus 495,228 decoded bytes (88.1% smaller). This is a payload measurement, not proof of lower overall latency or CPU use.

At 18:09 ET after the user restarted Gateway, IBKR had supplied one genuine daily-P&L callback of $0.00, retained for 293 seconds with a responsive Gateway and account risk data ready. Live remained manual and paper auto-approval stayed enabled. Other execution gates still apply, and no real order was submitted or canceled. No broker-paper execution qualification was performed in this pass.

OpenAI tooling used: Product Design **audit** for workflow/accessibility review, and the browser skill for actual interaction checks. No OpenAI API/provider dependency, external upload, plugin installation or model subscription was added. OpenAI Docs remains applicable if a future change adds an OpenAI research provider.
