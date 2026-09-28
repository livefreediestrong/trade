# What Fox knows at decision time

The combined maintenance release adds a local evidence summary to each shared
stock-research model call, including scanner/Fox calls and Changing Woman's
research calls. The summary is captured before the model call, retained in the
decision input receipt, and shown among the recorded shared-investigation
uncertainties. Older decisions keep their original inputs.

The summary reports the research quote's age, delayed/frozen status, available
two-sided spread, count of dated cached company headlines, upcoming earnings
date, matching historical sample size and requested time horizon. Missing or
stale data is explicitly unknown. Repeated links count once. A recently received
delayed quote is not described as a current live price. Research freshness is
not an execution quote qualification.

It adds no new feed requests, broker requests or model calls. It includes no
account identifier, credential or account balance. The existing configured
model receives this extra bounded context under the existing cost accounting.
Prompt version `thesis-v3-context` separates later results from the older prompt
cohort; the old history is retained rather than relabeled.

## Existing decision evidence

- Screen, price/volume/trend, entry quality, earnings and cost checks.
- Dated cached headlines and curated reference material.
- Matching past observations, with source/policy/model cohort boundaries.
- Separate deterministic account identity, P&L, pending-order, sector-count,
  market/event, risk-budget and order checks.

## Gaps still requiring dedicated work

- Complete account-wide exposure, including manually held positions, ETFs,
  options, currency/factor overlap and correlated holdings. Fox's sector-count
  cap is not an account-wide correlation model.
- Settled cash, exact buying power and per-instrument permissions in a dated
  decision receipt. Broker acceptance is not evidence of economic suitability.
- Timestamped market depth, halt status and short-borrow availability; live
  entitlements and exchange timestamps matter more than receipt time.
- Dividend/ex-dividend, split, tender and other corporate-action calendars;
  option exercise/assignment risk across the intended holding window.
- Regime-specific later-sample calibration and measured fill/slippage results.
  Model confidence and a handful of favorable observations are not a win rate.
- SEC amendment reconciliation, complete insider history and point-in-time
  fundamental evidence. Company Check remains a research view.

These gaps are disclosed to the AI; this release does not invent these data or
replace the existing execution checks with an AI judgment.

## Connections and skills

The chat currently exposes Exa search/fetch tools. They can help research public
sources during development. They are not automatically callable from the
Windows app's background workers. App integration would need an explicit API
adapter, credentials, provider terms, cache, dated provenance, rate limits and
cost accounting. No new provider account or subscription is created here.

The installed Bigdata company-risk skill supplies a useful review structure:
regulatory/legal, competition, operations, finances, macro markets, and
management/governance. Its provider tools were not exposed in this session, so
this release does not claim that Bigdata is connected. The deployment checklist
skill is used for the maintenance workflow.

Prefer improving the desk's existing SEC, Finnhub, broker and public-calendar
coverage before adding another overlapping headline feed. Financial filings and
company investor-relations releases provide primary evidence; social sources
are leads that need corroboration.

References checked for this revision:

- [IBKR market data types and subscriptions](https://www.interactivebrokers.com/campus/trading-lessons/python-receiving-market-data/)
- [IBKR order-type and execution limitations](https://portal.interactivebrokers.com/en/trading/ordertypes.php)
- [SEC investor bulletin on Forms 3, 4 and 5](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-69)

Code tests exercise delayed/frozen/stale/future data, incomplete news and history,
duplicate links, privacy, immutability, actual model-input assembly and retained
decision presentation. They do not establish strategy performance or live-order
readiness.
