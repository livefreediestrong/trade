# Remaining broker qualification — September 27, 2026

The stock-stop lifecycle, options exits and local statement review are installed.
The owner reports that the separate IBKR paper account is not set up or its
availability is unknown. This runbook records remaining tests, not test completion,
live-account approval or an instruction to send an order now.

## Connection prerequisite

The owner checks Client Portal > Settings > Account Configuration > Paper Trading
Account and completes any separate credential setup. Do not put credentials into
chat or use the real account as a fallback. Confirm the returned account identity
and paper mode from the broker, not from a port number or a UI label alone. Keep
paper-test data and broker client identity separate from the real desk; do not
switch the running real-account connection to execute a test. A separate setup
must pass the app's account, quote, permissions and P&L checks before sending a
reviewed, bounded paper ticket. No new practical dollar policy is selected here.

## Evidence required for each test

Retain source version, paper account scope, API client/order/permanent IDs,
submitted quantity/prices, submission and callback times, actual open-order and
execution observations, position snapshots, fees/corrections, and the app's
before/after durable state. Redact account IDs from shared reports. A returned
method call or screenshot of a button is insufficient. Unavailable simulator
behavior is recorded as not exercised rather than passed.

| Area | Broker-paper evidence still needed | Separate evidence still required |
| --- | --- | --- |
| Stop acceptance | After a confirmed entry, independently observe accepted SELL STP, remaining quantity, price step, account/client IDs and no duplicate protection | No atomic entry/stop guarantee; gaps remain possible |
| Partial entry/exit | Position and remaining stop agree after partial fills; fee events counted once | Deterministic recorded-event replay for rare sequences simulator cannot produce |
| Change/cancel | Tightening or quantity change preserves identity; cancel acknowledgement and fresh holdings precede any replacement exit | Cancel/fill races and broker corrections cannot be assumed from a simple cancel test |
| Reconnect/restart | Broker orders/history and app ledger reconcile without duplicated orders or client rebinding | Repeated market-open and overnight endurance |
| Rejection/outage | Explicit rejection or unavailable account/quote evidence leaves a visible hold and no blind retry | Simulated rejection does not cover every production rejection |
| Option closing | Exact owned contract/leg quantities close through the reviewed paper path; receipts and residual positions agree | Live fills, spreads, assignment and liquidity differ from simulation |
| Changed holdings | Unexplained option/stock changes create a durable review hold, with no invented assignment or P&L | Genuine broker activity/statement evidence to identify the event and cash/stock consequences |
| Activity import | Real detailed CSV preview, account/contract match, duplicate/conflict handling and reload | Settlement/cost-basis reconciliation against an independent broker statement |

## Simulator boundary

IBKR's current documentation says paper fills use top-of-book data, stops and
complex orders are simulated, and combo support is limited. Exchange-directed
market-order partial-fill remainder handling can differ from a real exchange.
The simulator has no real execution or clearing ability. Passing paper tests can
qualify the app's broker interaction; it cannot prove live liquidity, stop prices,
assignment behavior, clearing or profitability. This review did not establish
whether the owner's particular paper setup simulates exercise/assignment events.
Do not invent those events or label sample CSVs as genuine statements.

## Current practical-risk decision

Existing saved settings remain unchanged. The previously prepared $10/$2/3 stock
draft and $100/$200/10 paper-options draft are examples, not approved replacements.
The current $1B order/$1B daily-loss/100k-attempt caps are not meaningful small-account
limits. The owner's correction remains **do not turn live trading off**; no control
was changed to resolve this pending decision. Available data, qualified execution
and explicit activation are separate facts.

Sources checked September 27:
- [IBKR paper setup](https://www.interactivebrokers.com/campus/trading-lessons/how-to-open-an-ibkr-paper-trading-account/)
- [IBKR paper simulator limitations](https://www.interactivebrokers.com/campus/glossary-terms/paper-trading-account/)
- [IBKR account guide](https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm)
- [Exercise/assignment activity fields](https://www.ibkrguides.com/reportingreference/reportguide/options_exercises_expirations_fq.htm) (previously reviewed; actual owner export still required)
