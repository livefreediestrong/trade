# Live options v1 (long single-leg)

SAFE path for manual live options on the IBKR desk. **No auto_live / live_agent options. No naked STO/BTC. No multi-leg/BAG.**

## Enabled
- Manual `live_manual` + IBKR only
- Intents: **BTO** (buy to open) and **STC** (sell to close)
- Single-leg call/put, standard 100-share US equity options
- Market or DAY limit; quantity = whole contracts
- Notional gate: `contracts × premium × 100` via `order_terms.option_notional`
- Review → type **OCC / localSymbol** → approve (same review/ack/risk gates as stock)
- STC verifies OPT holding by `con_id` (not underlying stock shares)

## Still paper-only
- STO / BTC (naked short / cover short)
- Verticals and other multi-leg / BAG
- Options from Moss paper workday / auto agents
- Exercise / assignment simulation

## API
- `POST /api/live/ticket/option/review` — preview only; never places
- Approve remains `POST /api/signals/<id>/approve` with `review_token` + `ack_ticker` = OCC/local symbol

## UI
- Live Order ticket → **Option** tab: working BTO/STC form
- TRADE nav: **Live desk** + **Options (paper)** (`#desk-options` unchanged)

## Restart
Restart the desk process on :5056 after deploying so Flask loads the new blueprint routes.
