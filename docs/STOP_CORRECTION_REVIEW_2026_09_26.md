# Reviewing a broker correction

Implemented September 26, 2026 in the staged maintenance candidate. **Not installed.** This completes the supported acknowledgement workflow for a verified correction in the IBKR paper-stop test. Real broker-paper acceptance remains open, and the owner's maintenance-window choice is preserved.

## Owner workflow

When a tracked stop execution is corrected, Fox's panel shows **Broker correction review**. Pause Fox and end the trading session, then choose **Check [stock] correction**. The desk reads current broker evidence and shows the original and corrected shares sold, average sale price and fees, followed by remaining holdings and stop status.

If all required evidence agrees, tick **I checked the changed shares, price and fees**, then choose **Record review · keep paused**. The review expires after 90 seconds. Confirmation reads the broker again and refuses a changed account, policy, execution, fee, holding or stop. It never starts Fox, changes the mode, submits/cancels an order or alters the risk limits.

Missing evidence appears as a specific blocker. No confirmation is offered for unknown/non-USD fees, broker counts that have not caught up, a different/new position in the same stock, another tracking error, weak/unaccepted/cancelled remaining protection, corrupt data or outstanding broker submissions. A correction that restores shares after a completed order can still require a separate broker/protection repair. Acknowledgement cannot invent a working stop or combine positions.

## Implementation and recovery

- `stop_review.py` uses the existing broker submission/configuration locks and the stop execution journal. Review and confirmation are POST-only routes under the local access guard. GET status exposes a read-only list scoped to the selected account.
- Each server-issued, single-use token binds the full review evidence and expires after 90 seconds. At most 20 current tokens are retained per process; reopening one correction replaces its earlier token. Restart loses pending tokens and requires a new review.
- Confirmation writes the accepted accounting key, an audit entry and removal of only that stop's own tracking error together in one saved record. Original/corrected values, time, position counts and evidence fingerprint are retained. Failed saves retain the correction hold; lost responses are not retried automatically.
- Later reconciliation accepts the same reviewed economic facts. New economic facts, including a new correction or changed fee, require another review. This is deliberately conservative even if a later partial fill adds information to an order that was previously corrected.
- The original before-correction receipt is retained when available; missing earlier evidence is labelled **Not reported**. Another position's tracking error is preserved. Damaged review receipts require recovery instead of silently clearing a hold.
- This resolves only the selected correction. Other unresolved orders, stops, session/risk checks and paper-only restrictions continue to apply.

## Evidence

- **817 passed in 73.63 seconds** across 31 selected Python suites. This broad run covers order gates, existing stock-stop lifecycle/accounting, P&L, launcher/recovery, options boundaries, journal and weekly review, including the first 28 review cases.
- Final focused run: **29 passed in 13.55 seconds**, including one subsequently added cross-site/GET safety case. This overlaps the broad run; do not add the counts together. Broker I/O was intercepted and data directories were temporary. No actual broker orders were used.
- Five browser-controller suites passed: correction review, live agent, live ticket, broker orders and broker safety. New controller checks cover explicit acknowledgement, exact tokens, duplicate clicks, stale/changed state, unsafe evidence, late responses, timeout without retry and text escaping. The three stop/review modules passed pyflakes.
- Real browser preview exercised actual review routes against sample broker reads with outgoing network and order actions disabled. Checked requested desktop width 1341 (actual 1342 CSS pixels) and mobile width 390. Fixed the comparison table's inherited minimum width; both Before and Corrected columns now fit on mobile with 14px text and no page or table overflow. Captured browser warnings/errors were empty; the preview deliberately blocked its favicon request.
- Browser confirmation saved a sample review; reload showed the pending correction removed, its tracking warning cleared, a dated audit event present, and Fox still paused. Temporary preview process stopped, tab closed and viewport reset. This is component acceptance, not whole-app or real-Gateway qualification.

Receipts are in the task outputs: `correction-review-tests-20260926.txt`, `correction-review-focused-20260926.txt`, `correction-review-{1341,390}.png`, and `correction-review-saved.png`.

## Package and remaining work

The combined `correction-review-20260926.patch` and `.json` supersede the stop-ledger/protection/P&L/update-classification packages for installation; apply only the latest combined candidate after verifying the production baseline. It remains held for the chosen maintenance window.

Read-only production check at **21:46 ET**: PID 38140 and source `81b4c2f8877820b8` unchanged; saved configuration checksum unchanged; Gateway connected, Daily P&L available, current independent API response, manual / session paused / Fox off, zero stored pending intents. A single quiet-session P&L callback does not prove reconnect or market-open recovery. No production code, dependency, setting or broker order was changed.

G01 real P&L reconnect recovery and G02 broker-paper lifecycle acceptance remain open. G03 option exits/expiry/assignment, G04 practical risk choices, G06 external source access, G08 strategy evidence and G10 endurance remain separate work. The staged software is not a live-readiness or profitability claim.
