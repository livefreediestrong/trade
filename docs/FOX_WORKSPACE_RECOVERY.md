# Fox workspace and recovery

The opening trading summary distinguishes the saved On/Off switch from current operating readiness. Connection, Daily P&L and the selected AI configuration remain visible when the market is closed. The summary links to existing controls without activating or pausing anything. It shows actual saved agent limits and flags billion-dollar values for review; it never changes them. Every order still uses the existing execution checks.

The summary reads the existing workspace endpoint, rejects stale/future timestamps, accounts for broker-cache age and clears its current-state claims after 30 seconds or a failed read. The update is frontend-only and uses Flask's existing template auto-reload. Validation: `node tests/test_fox_summary.cjs`, existing agent UI tests, and a real browser read-only check; no real orders are test cases.

`/desk/fox` is Fox's workspace; `/desk/auto` remains a compatible address. Fox is the sole automatic broker decision owner. An enabled saved policy with a current revision, activation ID, account identity and active session is required. Changing Woman supplies context and monitors desk chores. Account checks, current quotes, pending-order reconciliation, model abstention and existing risk limits still apply to every order. Manual tickets retain their existing explicit review.

The top-ten list contains actual completed agent assessments, one per symbol for the current account and Eastern trading day. Entries expire after one hour. PASS sorts before WATCH, then other assessments, with newest first in each group. Fewer than ten results is normal, especially after installation or while paused. This is research history, not ten buy recommendations; no extra model calls are made to fill the list.

## Brain failures

Stale/unverified quotes are market-data holds, including historical records using the old `llm_error` field. They do not call the brain. Provider overload, throttling and transient server errors receive at most two retries with increasing delay and jitter. Retry-After is honored only within the remaining request allowance; otherwise the error is returned. All attempts count against the persistent provider budget. Authentication/client errors, timeouts, invalid output and exhausted budgets remain explicit holds. Model or provider selection is unchanged.

Gemini thinking-setting rejection now shares the same retry allowance rather than starting a new full timeout recursively. The workspace reports the latest observed provider transport result, expires that health after 15 minutes and says unobserved after restart. A successful transport is not evidence that a proposed trade is acceptable. Guidance: [Google's Gemini troubleshooting documentation](https://ai.google.dev/gemini-api/docs/troubleshooting).

## Calendar coverage

Seven calendar days are grouped in America/New_York time, with source failures displayed beside the calendar. Failed feeds retry every 15 minutes; successful feeds keep the three-hour cadence. Malformed optional cache records are ignored. Explicit unknown ICS timezones are not guessed.

BLS was returning HTTP 403 on this machine on September 25, 2026. A dated, partial official schedule snapshot supplies selected releases through October 31 when no usable BLS cache exists. Each event is labeled saved, with its verification date and source. The live feed remains marked unavailable. After October the snapshot expires and must be refreshed if the feed is still unavailable. Source: [BLS 2026 release schedule](https://www.bls.gov/schedule/2026/), checked September 25, 2026. The presidential feed can also be unavailable; no speech dates are invented. Existing static FOMC dates and owner-added events remain available. Calendar sources can revise dates.

## Backups

The workspace's **Back up now** action and the existing daily upkeep job create local `data/backups/state-*.zip` snapshots. Only top-level JSON and SQLite stores are included. Credentials (`.env`), images, logs, nested research exports and caches are excluded. Archives contain private account and research records and remain local. No automatic deletion is performed.

JSON files are captured under the desk lock. SQLite uses its online backup API, including committed WAL transactions; each store has its own capture time. This is not a distributed transaction across all stores. Every archive is reopened, all SHA-256 digests checked, all JSON decoded and every database quick-checked before atomic publication. A failure retains previous snapshots and is surfaced by upkeep and the workspace. The UI receipt means verified at creation, not continuous checksum monitoring. To verify later, call `desk_backups.verify(path)` from the local Python environment.

There is deliberately no state-restore endpoint: any recovery requires stopping the application, reviewing the manifest and reconciling the current broker account. Restoring old order/counter files must never trigger a replay. Source-only recovery remains separate in `release_tools.py`; it now hashes the exact bytes archived, verifies them before publication, and never overwrites an existing archive. It excludes all trading state and credentials.

## Visual assets

`static/fox-canyon-workspace-v1.png` was generated with the built-in image-generation tool on September 25, 2026. Art direction: contemporary illustration of eastern Arizona canyon/high-desert country; terracotta canyon walls, mesas, agave, yucca, desert grasses and mesquite, one watchful fox on the right, warm amber light and navy shadows, quiet left space for text. No text, logos, people, ceremonial objects or invented tribal symbols. It is landscape-inspired contemporary artwork, not a claimed authentic cultural artifact. Existing fox/Changing Woman portrait art and the documented basketry-inspired border remain. Greek constellation charts are no longer loaded into the interface.

## Broker recovery

After an account/P&L resubscription the adapter reads the replacement P&L object and still requires its real current-day callback. A recovered transport or account balance cannot substitute for Daily P&L. Mocked transport tests exercise this replacement path; a real missing Gateway callback still blocks new exposure.

The September 25 Gateway export showed a real zero Daily P&L callback at 17:08:22 ET, upstream error 1100 at 17:14:15, client cancellation/disconnection before the 1102 recovery at 17:14:17, and no further Daily P&L responses through 17:47. Later 2100 warnings immediately followed the desk's own STOP_UPDATE account requests. This proves the warning was self-generated in those cases; it does not establish why Gateway did not emit P&L for later subscriptions.

Initial account refresh now requests a new download without first stopping updates. Explicit refresh is held while the upstream server is unavailable but the API socket remains open. The scheduled refresh timer resets on genuine P&L callbacks, waits through an upstream outage, and shares the latest reconnect time with automatic recovery. The original actual-callback, account, current-day and transport checks remain required. Regression tests exercise the exported event ordering without connecting to a broker: `tests/test_pnl_initial_recovery.py`, `tests/test_pnl_stream_safety.py`, `tests/test_ibkr_pnl.py`, and `tests/test_ibkr_connection.py`. These backend changes require a desk restart. Reference: [IBKR message codes](https://interactivebrokers.github.io/tws-api/message_codes.html).
