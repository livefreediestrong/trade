# Research context and reviewed live activation

Implemented in the staged September 26 maintenance candidate. The owner clarified that research should both inform real-money decisions and provide an explicit activation path. This implements both. It does not install the candidate or activate, pause, restart or reconfigure the running desk.

## What changes

- The existing shared decision pipeline now includes up to three curated reference summaries for Fox's scanner and other research paths, alongside the existing news and past-setup evidence. These summaries reach the model input and the immutable decision receipt. They remain marked as reference knowledge, not current prices, verified profitability or model training weights. Missing/locked reference storage produces explicit unavailability without invented sources.
- Practice and Research each show **Use research with real money** and **Review and arm live trading**. The review reads the connected IBKR account, current equity and daily P&L, saved Fox policy, desk preset, additional account limits, options policy and current watchlist/radar scope. It explains that the universe can change and the saved symbols are only a hint. Effectively open Fox limits are stated plainly.
- The owner checks an acknowledgement, types **REAL** or **PAPER** for the displayed account, and presses the corresponding final arm button. That calls the existing activation route; eligible orders can follow through the existing scheduler and risk checks. Opening/closing the review or saving a paper workday cannot arm trading.
- A one-use review expires after 90 seconds and binds the exact account, agent revision and entire saved configuration. The server rechecks it after the current broker checks, under the execution/configuration locks. Any settings change, expiry or replay requires another review. Missing P&L, unresolved intents and unreadable execution state still block activation. Existing direct activation remains compatible with the live workspace.
- A lost response is presented as unconfirmed activation. The browser does not retry or assume Fox stayed off; it directs the owner to the live status view. An already-enabled agent cannot be activated again through the research review.
- The new bridge rejects real-account activation of the staged broker-paper protection tests. It does not remove that qualification boundary, save new risk values, promote paper adaptive sizing into real capital, or raise model budgets.

## Validation

The current source passed **165 Python tests in 8.50 seconds** across `test_live_research`, `test_live_agent`, `test_brain_repairs`, `test_model_reservations`, `test_policy_evidence`, `test_workspace_ui` and `test_paper_workspaces`. The tests exercise the actual Flask routes and shared model-input construction with isolated storage and intercepted broker/model I/O. Coverage includes both account kinds, stale/changed configuration, changes during broker checks, expired/consumed tokens, new broker/P&L/order blockers, request origin, bounded review storage and unavailable reference storage.

The new research controller and existing live-agent controller suites passed; the new script passed syntax checking and `live_research.py` passed pyflakes. Browser evidence used the actual template/controller in an isolated sample-account server, with all outgoing socket connections, broker orders and background workers disabled. Review, typed confirmation, sample activation, reload and already-enabled rejection were exercised. Requested 1341px rendered 1342px; 390px was also checked, with no horizontal overflow. No unexpected console warnings/errors were captured during the successful flow; favicon requests were deliberately blocked. The preview process/tab were closed and viewport reset.

Task artifacts: `outputs/research-live-tests-20260926.txt`, `research-live-{1341,390}.png`, `research-live-sample-result.png`, and `preview-research-live.py`. These checks establish software behavior in isolation. They do not exercise real broker orders, paid live model calls or Gateway reconnect recovery.

## Maintenance and remaining work

The cumulative `research-live-20260926.patch` supersedes earlier held candidates for installation. It remains uninstalled under the owner's maintenance-window choice. At 23:10 ET, production source `81b4c2f8877820b8`, PID38140 and the saved configuration checksum were unchanged. Gateway and daily P&L were available; no pending intents or decision were in flight. The pre-existing manual/session-paused/Fox-off controls were preserved; this work did not turn trading off or on.

Real Gateway recovery, broker-paper protective order/options qualification, complete assignment accounting and owner-approved practical dollar limits remain open. The new reference context informs research; it does not itself establish a trading edge or perform model-weight training.
