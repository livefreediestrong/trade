# Debugging and performance cleanup — 2026-09-24

This pass preserved the current one-page layout, sidebar companions, scene,
execution configuration and durable trading records. It repaired reproduced
issues without changing trading strategy thresholds or broker permissions.

## Changes

- Scheduled watchlist ideas now respect the existing regular-market setting,
  weekends, holidays and early closes. A slow scan rechecks the current session
  settings before publishing its result. Reconciliation and expiry still run.
  Explicit historical research and the broad daily-history scanner remain usable
  outside the trading session.
- The browser requests compact SSE state. The first frame carries all signal
  history; subsequent frames omit only unchanged terminal groups. Corrections,
  deletions and reconnects are handled explicitly. Pending ideas, approvals,
  broker orders, balances and risk controls remain in every state frame. Legacy
  stream consumers still receive full snapshots.
- Evidence synchronization uses one SQLite transaction for a batch instead of
  opening a connection and committing each record. Fingerprints and immutable
  revisions are preserved. Invalid batches do not leave partial writes.
- Headline and scanner requests have bounded timeouts and release their busy
  flags after failures. A failed headline refresh displays unavailable status.
  Companion/scanner polling respects hidden and departed pages; headline delivery
  keeps a bounded in-memory deduplication list.
- UI state events are dispatched after broker and decision state are merged.
  Help closes when nested research panels scroll. Shared-brain notifications
  describe both research workspaces. Historical price-extension notes no longer
  recommend full-size entries.
- The launcher verifies Python and Pydantic versions, includes Pydantic in its
  dependency check, and checks again after installing requirements.
- Source packaging includes setup templates and excludes root debug screenshots,
  probes and smoke scripts. Files resolving outside the source tree are excluded.
  Credentials and runtime state remain excluded. Older archives containing the
  now-forbidden debug paths will be rejected by the stricter verifier; they have
  not been deleted or modified.
- Removed an unused state snapshot cache and refreshed the UI editing map.

## Measured results

| Check | Before | After |
| --- | ---: | ---: |
| 500 evidence records, local synthetic benchmark | 5,165 ms | 40 ms |
| Unchanged terminal history, identical saved stream frame | 297,858 bytes | 42,984 bytes |
| Deployed compact stream: initial / subsequent frame | 298,225 bytes | 43,278 bytes |

The evidence benchmark verified identical fingerprints. Stream measurements are
uncompressed JSON payload sizes, not total application bandwidth. Normal JSON
HTTP responses already used gzip. Active state was checked for preservation.

## Verification

- Baseline: 659 Python tests passed. Final: 674 passed, with 10 warnings.
- All 11 JavaScript suites and syntax checks for all 20 served JS files passed.
- The installed launcher dependency check passed without installing packages.
- The deployed app served the new assets and requested `compact=1`; two actual
  SSE frames confirmed full initial history and smaller subsequent state.
- Browser checks found no captured console errors or failed images, and no
  document overflow at 1440px, 594px or 390px. Nested-panel scrolling dismissed
  an open help popup. Responsive overrides were reset after testing.
- The app restarted successfully through the existing launcher. Configuration
  SHA-256 was unchanged. The daily-history scanner resumed saved progress.
- Four minutes after the pre-deployment check, the last scheduled signal had not
  changed while the regular market was closed. Mode remained `live_manual` with
  zero pending broker orders.

Gateway refused its API connection before and after deployment. Account daily
P&L remained unknown and new live risk remained blocked. This pass did not arm,
submit, cancel or otherwise test a real-money order. Passing tests and the
observed UI checks do not establish broker readiness or strategy profitability.
