# All returned holdings and unfinished orders

Fox's broker-protection panel now has a collapsed **All holdings and unfinished
orders** review. It uses the same selected-account broker read as the existing
protection check. It adds no connections, subscriptions, model calls or order
actions. No raw account state is added to Gemini or Claude inputs.

The earlier concentration panel remains explicitly US-stock-only. This review
includes every asset type and currency returned by the positions read, including
holdings opened outside Fox. It groups counts by asset and currency, retains
short-option obligations and shows unfinished orders individually.

For supported USD stock and option limit buys, the estimate is remaining quantity
times the limit price times the reported contract multiplier. Partial fills are
removed. Missing multipliers, unavailable numbers, market orders and unsupported
assets remain unknown. Non-USD costs are not estimated: the contract currency
alone cannot establish quote subunits or a conversion rate. Pending cancellations
retain their remaining quantity. Linked, conditional and held orders are excluded
from the standalone subtotal. Unfilled sales never become available cash.

Duplicate broker identifiers do not double-count. Conflicting duplicates withhold
the commitments view. Missing, old, future or undated snapshots cannot retain
amounts in the browser. The adapter rejects Boolean position/order quantities,
and optional unset/nonfinite price fields remain unknown rather than breaking
otherwise usable protection evidence. Existing protective-exit decisions are
unchanged.

This is a descriptive inventory and partial cost estimate. It is not an account
valuation, sector/factor correlation model, margin requirement, settlement check,
instrument permission, maximum loss or spendable-cash calculation. Buying power
may already reflect orders; the display must not subtract estimates from it.
Option premium costs do not quantify exercise/assignment or combined-position risk.
Only API-visible orders are included. Visibility of every manually entered order
has not been established, and this read never binds or takes over those orders.
Complete account-aware sizing and the wider decision-context gap remain open.

## Validation

- 274 focused Python checks passed across nine suites, covering the new estimates,
  the selected-account adapter, funds, connection lifecycle, option limits,
  stop fills, stop review and exit safety. External order I/O is replaced in tests.
- Two Node suites passed, including four protection/commitment controller checks.
- The actual controller and assessment ran in an isolated browser preview with
  clearly labeled sample holdings and orders. Keyboard disclosure, refresh
  persistence and layouts at 1341 requested pixels (1342 reported) and 390 pixels
  were checked. No document overflow or captured console warning/error.
- No genuine broker paper order, cancel/fill race, exercise, assignment or
  settlement was exercised. Paper-account availability is still unconfirmed.

## Installation and rollback

The sealed package, exact-file manifest, validation receipts, screenshots and
source rollback archive live under the Codex task's `outputs` folder. Before
installation: require the unchanged saved control checksum, current source,
fresh flat/no-orders broker read, no running decision, and a verified state backup.
Use the normal orderly maintenance and launcher path; restore maintenance
schedules afterward. Verify the running source and real read-only route after
restart. A new unknown P&L reading is not evidence that the P&L defect is repaired.
Roll back the listed source files if the route or existing protection flow fails;
do not overwrite the owner's saved controls.

Primary references reviewed September 27:

- [IBKR active-order retrieval](https://interactivebrokers.github.io/tws-api/open_orders.html):
  API-client/all-API reads and the separate client-zero manual-order binding path.
- [IBKR order modification](https://www.interactivebrokers.com/docs/tws-api/doc/orders/modifying-orders):
  `reqAllOpenOrders` does not bind orders for modification.
- [IBKR contract details](https://www.interactivebrokers.com/docs/tws-api/ref/contract-details):
  price conventions need instrument details; a currency label is insufficient
  to infer every market's quote units.
