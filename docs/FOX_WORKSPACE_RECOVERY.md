# Fox workspace and recovery

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
