# Agent research and live-first workspace — September 24, 2026

The opening panel reports the actual desk execution mode, session, broker identity,
daily P&L and configured broker risk limits. It reads state only. A separate Moss
rehearsal plan cannot set these limits or arm the desk. The manual reference is
available at `/manual-live-enablement` and in `docs/MANUAL_LIVE_ENABLEMENT.md`.

Moss's compact brief shows current research activity, the latest saved conclusion,
qualified sample size and verified actual-fill coverage. Detailed research records
and optional investigation tools remain visible in one bounded, keyboard-focusable
region; navigation links scroll directly to the relevant module. The shared market
scanner remains outside that region. Companion artwork and movement are unchanged.

## Automatic analysis

`AgentReview` runs in the existing companion worker, at most once every five minutes
while the app is running. It does not call a model, place or cancel an order, alter
configuration, or promote a strategy into live execution.

- Read retained decisions (the companion's existing last-500 window), real execution
  journal and signal metadata. Preserve live versus broker-paper scope.
- Apply the existing Moss provenance, mock-data, timestamp, session, latency and
  overlapping-horizon exclusions. Require a valid confidence score for evaluation.
- Run the existing chronological walk-forward analysis. Fewer than 30 outcomes or
  five independent session days means more evidence is needed. A nonpositive sample
  is labeled no observed edge. A positive sample remains descriptive and unproven;
  these thresholds are reporting categories, not execution qualification.
- Analyze only verified, unsuperseded executions with valid nonfuture timestamps.
  Preserve missing fees and arrival benchmarks as missing. Retain late fee updates.
- Save immutable captured inputs and calculations in the evidence store, plus a
  compact current review file. Identical inputs do not create duplicate reviews.
  Paper evaluation is compatible with the existing replay endpoint. Execution
  review inputs can reproduce `research_metrics.execution_costs` exactly.
- Refuse a new successful conclusion if the review, decision, signal or execution
  journal is corrupt. Preserve the last valid conclusion when another error occurs.

During regular-market hours, enabled daily notebook research starts an idle shared
scan or refreshes a completed stale scan. Explicitly paused scans stay paused,
including provider pauses. Existing market-day scheduling, paper approval, model
budgets, execution guards and end-of-day reporting remain in their own workers.
No missed sessions or absent executions are fabricated.

## Verification boundary

Isolated tests forbid network calls and broker execution. They exercise evidence
replay, delayed commissions, future execution exclusion, storage corruption,
deduplication, daily scanner scheduling and pause preservation. UI checks cover
missing/stale state, unknown versus zero P&L, broker-paper identity, market closure,
missing risk limits and distinguishing current mode from older settings snapshots.
These tests do not qualify real broker execution or establish profitability.
