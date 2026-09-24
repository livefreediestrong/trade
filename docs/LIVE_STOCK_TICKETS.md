# Direct stock tickets

Implemented September 24, 2026. This adds user-directed tickets to the existing IBKR manual execution path. It does not activate a session, change the broker account, select automatic mode, or submit anything when the page opens.

## User workflow

1. Open **Live trading → Stock order ticket**.
2. Choose **Buy shares**, **Sell shares I own**, or **Buy to cover a short**. Holdings also offer **Prepare sell ticket** / **Prepare cover ticket**; these only copy the symbol and whole-share quantity into the form.
3. Enter the symbol and whole-share quantity, then choose **Limit** or **Market**. Limit is the initial selection and requires an explicit price.
4. **Review order & fees** checks the current account, quote, holdings, working orders, session and risk limits. It verifies the listed US stock/ETF contract and asks IBKR for a what-if commission estimate. It does not place an order.
5. Inspect the account, action, exact quantity, price rule, fee estimate and estimated cash change. Type the exact stock symbol, then use **Submit reviewed order** yourself.
6. Follow **Broker orders**. Submission and partial execution do not mean the entire order has filled. The existing cancellation review remains available for working orders.

The existing verified `live_manual` account and manual session controls are required. [Manual enablement reference](MANUAL_LIVE_ENABLEMENT.md) contains the actual environment variables and configuration requests for the user to apply after broker-paper testing. This feature adds no activation flag. Direct tickets remain unavailable in `auto_live`.

## Boundaries enforced in code

- New `/api/live/ticket/review` accepts only ticker, intent, whole shares and explicit market/limit terms. It creates a server-owned draft and uses the existing account-bound, short-lived, single-use review token. An unsuccessful preview rejects the draft.
- Changing price, quantity or action requires a new ticket. Limits that would shrink a requested order instead reject it for editing; requested quantity is not silently reduced. Existing research ideas retain their separate sizing behavior.
- A sell may only reduce an existing long holding; a cover may only reduce an existing short. Working quantities are reserved, and fractional remainders are never rounded up. Buying a currently short symbol requires the explicit cover action.
- The final IBKR adapter compares the qualified contract with the exact reviewed contract and checks its latest cached holdings and working orders immediately before submission. Other clients or external broker activity can still race after that check; this is not a broker-native reduce-only guarantee.
- Fresh quotes, current account/mode, review expiry, session and risk gates are rechecked by the existing execution path. Missing daily P&L continues to block new risk; independently verified reducing closes use the existing close exception.
- Durable submission intent, uncertain-order reconciliation and confirmed execution accounting are shared with research orders. The browser consumes its review before submission, never retries an uncertain submission automatically, and invalidates stale responses/account changes.
- The broker-order table joins working orders with confirmed cumulative fills, avoiding duplicate rows. Unknown fills remain unknown; corrections and reversals are labeled.

## Supported scope and limitations

IBKR, USD-denominated stocks/ETFs on the explicitly verified US exchange list, whole shares, DAY market and DAY limit orders. No new borrowed-share entry, fractional execution, live options, multi-leg orders or attached stop/take-profit orders are implemented here. Paper options remain separate.

Commission and cash-change figures are estimates. Missing fees or non-USD commission estimates leave the USD cash total unknown. A market order has no price guarantee. A limit bounds its execution price, not fees or later exit costs. No guaranteed all-in dollar cap is claimed. IBKR describes what-if requests as [pre-trade margin/commission estimates](https://interactivebrokers.github.io/tws-api/margin.html).

## Validation

- `tests/test_live_ticket.py` exercises actual Flask review/approval routes and the real IBKR adapter using intercepted broker transport: input rejection, mode/account/position/contract changes, working reservations, limit sizing, missing P&L, unknown fees, market covers, preview failures, acknowledgement and duplicate/uncertain submission.
- `tests/test_live_ticket_ui.cjs` runs the actual browser controller with deferred in-memory responses, covering exact payloads, edits, expiry/account/status invalidation, duplicate clicks, position prefilling, discard and no retry.
- `tests/test_broker_orders_ui.cjs` exercises the real renderer for partial quantities, deduplication, unverified/corrected evidence and escaping.

These checks do not establish broker-paper acceptance, real-money execution readiness or strategy profitability. No real broker order, cancellation or arming action was used for implementation validation.
