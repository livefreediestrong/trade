# After-close review

The companion scheduler checks every 30 seconds for the latest US equity session
whose scheduled close was at least 30 minutes ago. It follows the existing market
calendar, including early closes and DST. It catches up the latest missed session
when the app returns, rather than fabricating a backlog. The computer must be awake
and the app running. Existing companion enable/use-model controls still apply.

`after_close_reviews.json` retains 90 session reports and is included by the existing
verified top-level state backup. It contains dated local calculations, source-health
snapshots, completed market prices, headlines, model interpretations and hashes.
Qualified observation inputs are retained so the numerical comparisons can be
recomputed. The broker journal stays account-scoped and separate from simulations.
Current account P&L is never assigned to a historical session. Portfolio drawdown
requires an equity curve and is not invented from individual observation returns.

Changing Woman interprets the evidence; Fox sees her interpretation, challenges it,
and proposes falsifiable paper tests. Their model is the existing configured Gemini.
The payload excludes account IDs, credentials and raw broker execution records.
Role calls are durably reserved before transmission. A crash after transmission
does not repeat that role's call. Local collection has at most three attempts per
session; provider failure leaves a partial report. Normal Gemini transport retries
still consume the existing persistent request allowance. Each call has a 90-second
deadline and a 4,096-token output bound.

Before each call, the review checks recorded desk-wide spend against the saved
`live_agent.policy.model_budget_usd`, plus a $0.25 nightly estimated ceiling. Unknown
budgets/usage fail closed. This is a conservative pre-call estimate, not a provider
billing limit or a cross-worker atomic spending reservation. Concurrent use and
unknown failed-call charges can differ from estimates. No risk/account policy is
written by this feature.

Online context is limited to up to 24 fresh headlines from the existing publisher
feeds, plus 120 daily bars for at most six symbols (SPY, QQQ, and active observed or
watchlist names). It does not retrieve full articles, perform general web search,
or include blocked Reddit data. Stale/future headlines are excluded; publication
after the reviewed close is labeled. Price series stop at the reviewed session.

The next review receives the previous five reports' Fox hypotheses as unproven
memory. This is retrieval and reflection, not model-weight training or automatic
promotion of rules. Existing qualified-outcome research ranking remains separate.
The nightly report is a frozen as-of snapshot: later corrections are reflected in
later reviews, not silently rewritten into an old report.

Read the report in Fox's workspace or Research. Source links expand the evidence
section. Model text is escaped; returned citation IDs must exist in the supplied
snapshot. Valid citations do not independently verify AI prose. Broker blockers
and reconciliation cues retain precedence over nightly avatar activity.

Validation: `python -m pytest tests/test_after_close_review.py tests/test_companion.py
tests/test_companion_research_view.py tests/test_brain_repairs.py
tests/test_fox_recovery.py tests/test_desk_day.py -q`, then
`node tests/test_after_close.cjs` and the existing companion page/avatar/scene tests.
