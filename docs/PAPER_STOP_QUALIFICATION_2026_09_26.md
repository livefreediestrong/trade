# Broker-held stock stops: paper qualification stage

**Current status, September 27:** installed in the September 26 combined maintenance
release, still off by default and restricted to IBKR paper qualification. Actual
broker acceptance remains untested. The owner reports that a separate paper account
is not set up or its availability is unknown. See the [qualification runbook](BROKER_PAPER_RUNBOOK_2026_09_27.md).
The dated implementation/package notes below preserve preparation history; their
earlier staged/not-installed statements are superseded by this status.

Follow-up: [stop execution/fee reconciliation](STOP_FILL_RECONCILIATION_2026_09_26.md) now implements the accounting work described below as outstanding. That update supersedes this package for installation. This document preserves the earlier lifecycle test receipt; actual broker-paper acceptance remains open.

## Implemented behavior

Fox can prepare a SELL STP, good-until-cancelled stop for confirmed whole shares his strategy bought. It operates during regular trading hours. The adapter requires the exact verified IBKR paper account and original positive API client ID. There is no live override or public order endpoint for this feature. Saving the checkbox pauses the agent and sends no order.

- A confirmed stock entry, including a partial entry, gets a protection check immediately after tracking. Subsequent checks precede research. A quote outage does not prevent protection using the saved stop; price-based app exits still require fresh quotes.
- Intent is saved before broker I/O under the ordinary submission lock. Timeouts, disk failures, restarts and missing broker evidence never authorize duplicate submissions. Protective operations count as attempts while bypassing the entry attempt cap.
- Acceptance requires a subsequent broker observation. The adapter captures actual openOrder replies because the maintained library retains some old order fields in existing Trade objects. A test demonstrates this with the real library wrapper.
- Changes preserve the order ID, include confirmed partial fills in total quantity, obey the broker price step and never loosen a stop. Other sells, short calls and pending call sales reserve shares. Ambiguous combinations require review.
- A profit, timed or end-of-day exit waits for cancellation confirmation and updated holdings before a separate sell. A fill during cancellation blocks that sell. An externally cancelled stop is not silently recreated.
- Uncertain stops block other desk orders for the same account, including manual tickets. Pausing does not cancel or forget accepted orders. Disabling the checkbox requires reconciliation before normal app exits can resume.
- Plain-language policy text distinguishes default app exits from this optional paper test. The accessible checkbox defaults off and preserves unsaved edits during polling.

## Still required before live qualification

The original lifecycle candidate paused every stop fill for review. The follow-up now records executions and fees, keeps corrections under review, and clears ordinary holds only after holdings and remaining protection agree. Broker-paper acceptance is still required. Do not delete durable records to resume trading.

This is not an atomic bracket: shares may be unprotected between entry and stop acceptance. Rejection, outages and changed holdings can leave gaps. GTC is not a perpetual-availability guarantee; regular-hours stops do not cover overnight trading, and execution can occur beyond the trigger price. Uncertain requests, other-client orders and unavailable broker history require review. No automatic client-ID rebinding is attempted.

Automatic option exits and expiry/assignment handling remain G03. Live-account activation of this stop policy is rejected. Existing risk caps and activation choices are unchanged.

## Validation

- **672 passed in 38.40 seconds** across 26 selected Python suites, including 71 new stop tests and related execution, ticket, option, P&L, lifecycle, queue, launcher, upkeep and weekly checks. Broker I/O is intercepted and storage is isolated. New module pyflakes check passed.
- Four JavaScript suites passed: live agent, live ticket, broker orders and broker safety. Opt-in checks cover the off default, preserved draft and save without activation.
- Real browser component preview used temporary data, no workers, disabled outgoing network and unavailable broker actions. Opt-in, save while paused and restoring off worked. Requested desktop width 1341 reported 1342 CSS pixels due browser rounding; narrow width was 390. Neither had horizontal overflow; no captured warnings/errors. This was not a whole-app visual audit. Screenshots are task `outputs/paper-stop-preview-1341.png` and `outputs/paper-stop-preview-390.png`.
- An interim broad run had 671 passes and one launcher subprocess failure caused by relative isolated dependency paths. Using absolute paths fixed that test harness issue; the final run above passed all 672 without installing production dependencies.
- Read-only production check at **20:34 ET**: PID 38140, running/disk source `81b4c2f8877820b8`, Gateway connected, Daily P&L available and independent API response current. Manual mode, session paused, Fox off and no stored pending intents. Quiet P&L does not qualify changing-market updates or the next reconnect; G01 remains open.

## Package and next steps

The combined candidate includes the earlier P&L/library revision and supersedes `pnl-revision-20260926.patch` for installation; do not apply both. Production source, dependencies, settings and broker orders were not changed by this pass. The isolated preview server was stopped.

Install the latest combined candidate only during the chosen maintenance window, verify source/dependency receipts and retained settings, then verify Gateway identity and P&L. Paper-test acceptance, partial fills, fees, corrections, resizing/tightening, rejection, cancel/fill races, app/Gateway reconnects, client changes and overnight behavior before considering live enablement. The dedicated P&L probe remains paper-only and must not run alongside a responding desk. Do not use a watchdog as a workaround for the earlier rejected direct restart action.

## References

[IBKR modifying orders](https://www.interactivebrokers.com/docs/tws-api/doc/orders/modifying-orders) describes order/client identity constraints. [Individual cancellation](https://interactivebrokers.github.io/tws-api/cancel_order.html) differs from global cancellation. [Order placement callbacks](https://www.interactivebrokers.com/docs/tws-api/doc/orders/place-order/introduction) describe broker reports after submission. Software checks and broker acceptance do not establish profitability or successful live execution.
