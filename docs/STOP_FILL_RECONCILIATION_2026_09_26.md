# Stop fills, fees and restart recovery

Implemented in a separate staging copy on September 26, 2026. **Not installed.** The owner's maintenance-window choice remains in force. This extends the paper-only broker-held stock stops; the option defaults off and cannot be activated for a real-money account.

Follow-up: [explicit correction review](STOP_CORRECTION_REVIEW_2026_09_26.md) now supplies the acknowledgement workflow described below as outstanding. Its combined candidate supersedes this package for installation. The validation here remains the earlier accounting batch's receipt.

## What changes for the owner

- If a stop sells part of a position, the desk records the sold shares once and reduces Fox's tracked remainder once. Later partial fills update the same order's record.
- Fees can arrive later. The desk leaves missing fees unknown, retains reported amounts by currency, and waits for complete USD fees before releasing the accounting hold. It never invents a currency conversion or treats an absent fee as zero.
- A normal hold clears only when verified executions, fees and holdings agree. Remaining protection must also be working with sufficient shares and the required stop price. Pending, rejected or externally cancelled stops keep other orders blocked.
- A broker correction updates the existing trade record. Quantity corrections also adjust the original tracked position, but never a newer position in the same ticker. Corrections keep a review hold; there is no automatic acknowledgement or restart of trading.
- Interrupted saves recover by repeating the same ledger update. A new process must read broker execution history before trusting saved stop status. The same gate covers manual broker tickets.
- Reconciliation runs while Fox is paused, but it does not enable him, change the mode, submit an order or cancel one. The practice ledger remains separate.

## How the evidence is joined

`broker_ibkr.execution_history` supplies account identity, execution IDs, cumulative shares, provisional-price flags and matching commissions. `stop_fills.py` reuses `real_trade_journal.TradeJournal`, retaining original and corrected reports. A larger final execution-ID revision replaces the older member of that execution family; the two are never added together. Account, paper mode, ticker, contract, permanent order ID, order reference, side, times and quantities must agree before the ledger changes.

The saved stop record contains a settlement receipt before the broker ledger is updated. The ledger uses one account-scoped order key. The final save commits Fox's new share count with the cumulative accounted quantity. Repeating any interrupted boundary cannot apply the same share reduction twice. The saved receipt hash and field checks detect damaged accounting records and require recovery rather than resetting counters.

Archived stop records and their native-stop ledger rows are retained for later corrections. They are not dropped by the ordinary 100/200-row display-history limits. A future compaction policy must preserve these accounting keys and revision evidence; long-running storage growth has not been endurance-qualified.

## Validation and limits

- **789 passed in 55.21 seconds** across 30 selected Python suites, including **43 new stop-accounting cases** and the prior 71 stop lifecycle cases. These exercise the real local persistence paths with temporary data and intercepted broker I/O. Failure cases include partial fills, late/non-USD fees, price/quantity corrections, old reports after corrections, position-feed lag, three interrupted-save boundaries, wrong contracts/accounts/orders, provisional prices, external cancellation, stale stop terms, damaged active/archived records, restart gates and cancellation races.
- Four JavaScript controller suites passed: live agent, live ticket, broker orders and broker safety. `pyflakes` passed for the two stop modules. Running it directly on the pytest fixture-importing test file reports fixture imports/redefinitions; this is not included as a clean test-file lint claim.
- Isolated browser copy check at requested 1341 (actual 1342 CSS pixels) and 390 widths: revised explanation visible, checkbox off, no horizontal overflow or captured warning/error. Preview data was temporary, workers and outgoing network disabled, and broker actions unavailable. An intentionally blocked favicon request returned 403. Screenshots: task outputs `stop-ledger-preview-1341.png` and `stop-ledger-preview-390.png`. This was a component check, not another whole-app visual audit. Preview server stopped afterward.
- Test receipt: task `outputs/stop-ledger-tests-20260926.txt`. Combined patch and manifest: `outputs/stop-ledger-20260926.patch` and `.json`. This package supersedes the protection/P&L/update-classification candidates for installation; do not apply them together.

No actual broker stop has been exercised by this pass. **G02 remains open** until a controlled IBKR paper session confirms acceptance, partial fills, fees, corrections, cancel/fill races and app/Gateway reconnects. Missing historical execution or order evidence requires review; Gateway history is not a complete statement. The follow-up implements correction acknowledgement when current evidence agrees; unsupported position/protection discrepancies remain blocked.

G01 P&L recovery across reconnects, G03 automatic option exits/expiry/assignment, G04 risk settings and G08/G10 strategy/endurance qualification remain separate work. The candidate preserves the existing risk limits and paused/manual state. Software test results do not establish profitable or safe real-money trading.

## Primary references

IBKR documents separate executions for partial fills and a changed final execution-ID segment for corrections in [the execution object](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/the-execution-object). [Commission and fees reports](https://www.interactivebrokers.com/docs/tws-api/ref/commission-and-fees-report) attach to an execution ID. [Execution and commission requests](https://interactivebrokers.github.io/tws-api/executions_commissions.html) describe the available history and callback flow. These broker rules inform the implementation; the tests here do not replace broker acceptance.
