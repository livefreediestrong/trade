# P&L revision prepared for maintenance — September 26, 2026

**Implemented and tested in an isolated copy; not installed.** This preserves the owner's earlier decision to wait for the next maintenance window. The candidate includes the maintained IBKR library, a paper-only diagnostic, and the previously deferred browser-update classification repair. It supersedes the old eight-file patch for installation; do not apply both.

## Delivered candidate

- The adapter, stock/options/combination order types, execution filters and library-specific tests now use `ib_async 2.1.0`, with no silent legacy fallback.
- Existing risk gates, callback identity/invalidation, bounded reconnect cooldowns and commission-P&L availability protection remain intact.
- The launcher checks the exact tested dependency versions. `aeventkit 2.1.0` and `tzdata 2025.3` are pinned with the adapter; the latter is required by this library release's `<2026` constraint. Production still has its existing dependencies. Installing the candidate would change its current timezone-data release, so calendar and weekly-review checks were included.
- The diagnostic defaults to a no-connection plan. Explicit paper runs use only P&L and time/account-read operations, reject live-account selection, retain separate reconnect-cycle receipts and exclude account identifiers, amounts and raw error text. Cleanup cancels only its own data subscription. Failure to receive P&L is reported, never replaced by zero.
- The diagnostic supports the separately prepared official IBKR SDK 10.50.2, the new wrapper and the legacy wrapper for controlled comparison. The official SDK/protobuf packages are isolated and are not added to production requirements. Startup differences between clients are recorded rather than hidden.
- The deferred update-classification repair prevents browser/template/style edits from causing broker reconnects once this Python update is installed. Python updates retain restart checks; all changes still invalidate the appropriate test evidence.

## Validation

**437 passed in 29.87 seconds**, across 23 selected suites. The checks cover actual replacement-library framing/decoding and objects; missing/invalid/late P&L; disconnect and account-download behavior; commissions; stock/options/combination and protective-exit paths; official legacy/protobuf decoders; diagnostic redaction/cleanup; Windows launcher and dependency-version rejection; calendar/weekly review; and deferred-update behavior. Broker I/O and process effects were intercepted. A first new launcher-probe test selected the wrong PowerShell variable; its selector was corrected before the final passing run.

New Python files pass pyflakes. Declared dependency constraints were checked. Running the actual default diagnostic command printed `connected: false` and made no broker connection. The combined 25-file patch passed `git apply --check` against the current dirty production tree. No production source or dependencies were modified by this batch.

These checks do not establish real Gateway reconnect recovery, changing market P&L, day rollover, overnight stability or broker-paper order qualification. No real broker diagnostic was run and no order was placed or canceled. G01 remains open.

## Package and provenance

All artifacts are in `C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs`:

- `pnl-revision-stage`: prepared source, without production settings/data. Its `docs/PNL_REVISION_2026_09_26.md` contains the maintenance and diagnostic commands.
- `pnl-revision-20260926.patch`: combined application, launcher, tests, diagnostic and documentation revision.
- `pnl-revision-20260926.json`: original/staged file checksums, exact dependency versions and validation scope.
- `pnl-revision-tests-20260926.txt`: final selected-suite output.
- `pnl-revision-runtime-20260926.json`: read-only production snapshot.

Staged application source: `1428235269d15e73`. Staged test/runner/dependency fingerprint: `abb21bf5c13ca3ee`. Patch SHA-256: `412f8d59db2558937b17be07e2621561cdfe65c715b1d2b1098151a68081ac0a`.

At 19:46 ET the production desk remained PID 38140, running and on-disk source both `81b4c2f8877820b8`, manual / session paused / Fox OFF, zero pending intents and no decision in flight. Gateway was no longer running and broker P&L was unavailable. **The owner confirmed they closed Gateway.** This is a normal owner shutdown, not evidence of another spontaneous crash or a regression from the staged revision.

## Short handoff

Owner reconnect follow-up, 19:50 ET: Gateway PID 40812 is running again. The unchanged desk received one numeric P&L callback; account download completed and the independent API response was current, with zero P&L retries/soft reconnects. Manual / session paused / Fox OFF. Receipt: `outputs/pnl-owner-reconnected-20260926.json`. This is existing-version recovery after Gateway startup, not a deployed-candidate result or durable reconnect qualification.

At the owner-selected maintenance window, verify manifest original-file checksums, reconcile any newer work, apply only the combined patch and use the normal permitted update flow. Do not change Gateway builds at the same time as the first client comparison. Qualify actual paper reconnect behavior, then verify production startup and a real P&L receipt. Keep the support case open if the initial-response failure recurs. The current patch remains uninstalled; no new maintenance schedule was created.
