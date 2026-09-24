# Moss paper workday and actual-trade review

Implemented on 2026-09-23 in the existing Flask/Jinja application. This extends the [original companion](MOSS_IMPLEMENTATION.md). It adds no dependencies or broker execution permissions.

## Daily operation

Moss's independent paper scheduler checks the exchange session calendar every five seconds. While enabled, it rotates through two research symbols per cycle, measures fresh completed-bar volatility and liquidity, and selects one candidate for the configured research model. Every fourth cycle favors the least-sampled eligible candidate in that pair. No catch-up trades are fabricated after downtime. The cycle reservation, symbol cursor and daily attempt count survive a restart.

Initial configuration is the **Stoic evidence collector**: SPY, QQQ, AAPL, MSFT, NVDA, AMD, META and TSLA; 120 seconds between cycles; 10-minute outcome horizon; $5 base paper entries, $20 maximum; four positions; 8% total marked exposure; 40 fills before new entries stop; 2% paper daily loss limit. Existing paper preset and loss guards also apply. Reducing exits can continue after entry limits or a pause. All dollar amounts here refer to the simulated book, currently $1,000, not the broker account.

The existing configured/preset minimum-confidence floor is checked again at the final paper-ledger boundary. Advisory reductions apply to the dollar budget as well as the preset quantity. A low-confidence decision stays in the audit history but cannot produce an automatic Moss paper entry.

Up to 200 research attempts per exchange day and a $5 recorded desk-wide AI-spend threshold are configured. An attempt can include model audits, so this is not a limit of 200 individual provider calls. Costs are existing estimated response accounting, not an absolute provider billing ceiling; unreported failures and concurrent desk activity can exceed the estimate. Market observations continue after the research budget is exhausted. Lower these settings in **Moss → Paper workday** if desired.

Research starts at the regular session open. Eligible decisions require enough fresh completed bars, so an immediate opening trade is not expected. New entries stop early enough to observe the whole horizon. Moss attempts to close its own holdings at the horizon or two minutes before the exchange close. Fresh in-session prices are required at the actual ledger write. Unpriced or missed exits remain visible as holdings and in the daily report; no closing price is invented. Borrowed-share entries are unavailable; bearish predictions can be retained as research or close Moss's own long paper holding.

The app and PC must remain running and connected. This is the app's scheduler, not a cloud service, Windows wake timer or a recurring Codex conversation. The existing desktop launcher starts the app. The exchange calendar handles weekends, US exchange holidays, daylight-saving offsets and early closes. Compare unusual dates with the [NYSE calendar](https://www.nyse.com/trade/hours-calendars).

## Optional empirical-Bayes personality

Select **Empirical Bayes · adaptive paper size** in the same panel. Cohorts separate ticker, actual model, prompt version, horizon, setup relative to VWAP, direction, personality and fee/slippage assumptions. Switching personality does not silently combine their results.

The prior is estimated from other matched cohorts when at least two peer cohorts supply ten eligible observations. Its mean win rate and mean net move are empirical; prior concentration uses a bounded method-of-moments estimate corrected for within-cohort binomial noise. Otherwise the explicitly labeled fallback is a neutral 50% win rate, zero net expectation and concentration 10. The inspected cohort does not supply its own prior.

For n outcomes and prior concentration k, the empirical fraction starts at n/(n+k). Below 10 it keeps strong shrinkage toward the prior. From 10 through 19 the discount tapers linearly; at 20 or more the empirical fraction is 1. This graduation is the requested policy, not a claim that standard empirical Bayes establishes certainty after 20 trades. The [Efron primary research](https://pmc.ncbi.nlm.nih.gov/articles/PMC4196219/) explains empirical Bayes and its assumptions; the 10/20 schedule is this application's explicit heuristic.

Learned research weights are bounded from 0.5 to 1.5 and apply only to matching configured-model cohorts. Nonpositive expected net moves cannot boost priority. Paper size can increase from the base to the configured maximum only after at least 20 qualified outcomes, positive mean net expectancy, first-half versus second-half win-rate drift no greater than 15 percentage points, and a Wilson 95% win-rate lower bound above 50%. All paper risk limits remain active. The current rule makes a bounded step to the maximum when those tests pass; it is not an uncapped exposure ramp.

This is descriptive, adaptive research using recent evidence. It is not held-out proof, a guaranteed profit strategy, or foundation-model fine-tuning. Horizon predictions and actual realized paper-trade P&L are separate measures. A stop can exit before the evaluation horizon. Model costs and simulation fee assumptions are not a substitute for actual broker costs.

## Evidence and retention

- Candidate observations: `data/moss_observations/YYYY-MM-DD.jsonl`, including rejected candidates and reasons, measured liquidity/volatility, selection weight and execution attempts.
- Versioned decision revisions: `data/research_history/YYYY-MM-DD.jsonl`, including model/prompt/input hash, quote timestamps, policy settings, prior training hash, fill timing, fees and subsequent horizon outcome.
- Workday state and fitted parameters: `data/moss_workday.json`.
- End-of-session report: `data/moss_reports/YYYY-MM-DD.json`; also readable through `/api/companion/reports/YYYY-MM-DD` after the outcome window has closed.

The fitter reads up to the newest 30 archive files and 10,000 decision IDs, plus the current decision ring; archives themselves are retained. This is a rolling research window, not unlimited all-history training. Identical decision IDs are deduplicated and overlapping horizons for the same ticker cannot count as independent samples. All training is as-of the decision time: only already completed outcomes are usable.

Strict filters reject mock/routed research, synthetic/fixture sources, failed or rejected executions, missing provenance, nonpositive/nonfinite prices, future timestamps, out-of-session observations, missing or stale quotes, late horizon observations, unfilled attempted executions and execution delay beyond the configured threshold. A later price cannot retroactively fill a missed outcome window. Quote and execution-delay defaults are 30 seconds. Eligible candidate defaults require at least $50 million estimated daily dollar volume and 0.05% completed-bar realized volatility. These are research eligibility assumptions, not a claim of execution liquidity or profitability.

## Actual broker trades: read-only

`real_trade_journal.py` refreshes verified IBKR execution reports about once a minute through the existing serialized adapter. **Review actual trades** opens the one-page journal. It records execution identity, account type, ticker/contract, direction, quantity, time, price, exchange, order IDs, and any matching commission/realized-P&L report. Trades placed elsewhere appear only if this Gateway API client exposes them.

The durable journal is `data/broker_execution_journal.json`. Repeated reports do not duplicate fills. Late fee reports enrich the original execution; temporary missing values do not erase known fees. IBKR correction IDs supersede the earlier record in totals while retaining its audit history. Real and broker-paper accounts are summarized separately. Neither is mixed into local-paper personality training. No live submission, cancellation, arming or mode change is performed by this collector.

Analysis shows reported P&L and fees by reported currency, missing-data counts, orders with multiple executions, regular-session coverage, and price difference against a matched, fresh, pre-trade desk quote when one exists. Positive quote-relative slippage is adverse. Fees are not subtracted a second time from broker-reported realized P&L. No opening cost basis or round-trip win rate is guessed for incomplete history. Unknown values remain unknown.

IBKR's API history is limited by its session, client visibility and Trade Log configuration. An empty response means no executions were exposed to this collector, not proof that the account has never traded. See [execution and commission callbacks](https://interactivebrokers.github.io/tws-api/executions_commissions.html) and [execution-report reference](https://www.interactivebrokers.com/docs/tws-api/ref/execution). Complete multi-day account reconciliation would require an additional statement/Flex import.

## Implementation and validation

Backend modules: `moss_policy.py`, `moss_paper.py`, `real_trade_journal.py`; scheduler/API wiring in `research_companion.py`; final local-paper checks in `app.py`; read-only executions in `broker_ibkr.py`. Templates: `moss_workday.html` and `real_trade_review.html`; renderer: `static/companion.js`. Existing live mode, session state, risk configuration and broker identity are preserved by the paper-settings endpoint.

The isolated suite passed 459 tests before final presentation refinements. After those refinements, 93 focused tests passed, including a new test proving that matched empirical weights change candidate selection. Tests use temporary data, block outbound sockets and reject any broker-write route. They exercise the real local paper ledger, revoked approval during quote retrieval, stale exits, risk limits, evidence filtering, correction/late-fee handling, restart deduplication and missing-close reporting. These checks do not qualify live execution or establish a trading edge.

Browser checks exercise settings persistence, optional personality selection, pause/resume status, empty broker-history state, and a narrow 390-pixel layout without document overflow. A new full market-session run and nonempty real Gateway executions remain separate operational validation steps. See the task's deployment snapshot for the verified running state.
