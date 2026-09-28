# Maintenance plan — September 26, 2026

The plan keeps recovery evidence, separates broker maintenance from app faults, and avoids restarting a desk that may be supervising trades. Settings → Connections & automatic routines now includes a read-only Maintenance & recovery section with dated watchdog, daily-upkeep and backup receipts.

September 27 owner instruction: **“always implement maintenance packages.”** Treat tested maintenance updates as authorized for installation during ongoing work; do not leave them staged solely to request approval again. Verify source drift and a fresh backup, use the existing orderly restart, preserve trading controls/limits, and verify the installed version and affected workflow. Active work, unresolved submissions, failed cleanup or a failed backup must be resolved before proceeding; report the specific obstruction. This instruction does not authorize risk-limit changes, trades, or replacement of the unattended-restart safeguards.

The owner confirmed that the roughly 02:12 ET Gateway exit was intentional; leave Gateway closed. The [broker-read load repair](BROKER_READ_LOAD_2026_09_27.md) and overnight research feature are the next combined package. With the desk idle and Gateway already closed, the owner-authorized explicit restart can use `-NoBroker`; broker exposure remains unknown. The read repair prevents a slow account refresh from filling every web-server worker. It does not fix the separate missing-P&L callback issue. See the subsequent release receipt for installation evidence.

## Existing schedule and recommended owner checks

| When | Work | What it must not do |
|---|---|---|
| Every five minutes | Existing `Tomahawk-Desk-Watchdog` checks the expected checkout and data directory, recovers missing workers and uses guarded update recovery. | No independent Gateway launcher, blind order retry or restart to conceal a provider error. |
| Daily, 16:40 computer-local time | Existing `Tomahawk-Desk-Upkeep` verifies a new backup first, checks SQLite integrity and checkpoints, rotates oversized logs and records outcomes. | Failed backups defer housekeeping. Source files, scratch directories and archives are not automatically moved/deleted merely because of their names or ages. |
| Changed app build, after the close | The daily job runs isolated regression tests if the app is healthy/current, regular trading is closed, no decision is in flight and there are no unresolved submission intents. A passed build is not retested every day. | A missed scheduled job catching up during the session must defer its heavy test work. A passing test suite does not establish broker readiness. |
| 30 minutes after each stock-session close | Existing daily review compares dated research records, costs and prior sessions. | No automatic promotion of an AI idea into a trading rule. |
| 90 minutes after the week's final stock-session close | Existing weekly review compares the cumulative week with earlier weeks, noting missing days. | No claim of profitability from an incomplete or negative sample. |
| Before the next trading session — owner check | Confirm Gateway sign-in, selected account, fresh P&L, all holdings, working orders, unresolved submissions and practical dollar limits. | Missing data is unknown, never zero. Connection alone is not activation. |
| Weekly — owner review | Review failures and research, check free disk space, select an attended update window and refresh provider-status assumptions. | No unattended dependency upgrades, broker upgrades or changes to risk limits. |
| Monthly and after a storage-schema change — owner/agent exercise | Restore a verified backup into a new local folder, block network before app import, disable workers and exercise real readers. | Never restore over production or replay old saved orders without reconciliation. |

The two Windows tasks were inspected on September 26. Both pointed to this repository's virtual environment and script, used IgnoreNew for overlapping instances, and had a 30-minute execution limit. Their last recorded task results were success. The schedule was not re-registered or supplemented during this work. The pre-session, weekly owner and monthly restoration rows are a plan, not newly installed jobs or reminders.

## Broker downtime

[IBKR publishes](https://www.interactivebrokers.com/en/software/systemStatus.php) a North American reset window of 00:15–01:45 ET Sunday–Friday and a Saturday maintenance window of 00:00–02:00 ET. The app shows those windows as a **planning assumption**, not a live outage feed: this account's assigned server region has not been independently verified. Daylight-saving offsets are included; on the autumn repeated hour the planning window uses its later end. The saved schedule is dated and asks for review after 90 days.

During a broker outage, preserve unresolved order evidence, continue bounded reconnection attempts, and show holdings/P&L as unknown until refreshed. Do not repeatedly log out, open multiple Gateway windows or reset the ledger. After service returns, verify the account, P&L, holdings, working orders and fills before considering new risk. An outage overlapping a published window does not prove the cause of a local process exit.

Gateway's native auto-restart handles its daily session. [IBKR documents weekly reauthentication](https://www.interactivebrokers.com/docs/tws-api/doc/architecture/the-trader-workstation/the-ib-gateway); normal sign-in remains the owner's task when required. This batch does not change Gateway's time, weaken its security settings, or create an extra Gateway task. The legacy `Ensure-IBGateway.ps1` now delegates to the running desk's session/hours/budget/process policy; it cannot independently reopen the program.

If the desk is active, holdings/orders are present, or broker evidence is unknown, automatic code-update restart is deferred. App-managed exits need the app and Gateway running. Broker-held stop creation and the complete option assignment/exit lifecycle remain unfinished G02/G03 work.

## Recovery copies and disk space

Keep the verified state ZIPs and their receipts local. They exclude credentials, logs and media. This batch retains archives rather than deciding that old user files are disposable. Recommended retention for a future explicit cleanup: preserve at least 14 daily snapshots, eight weekly snapshots and the last known-good pre-change snapshot; only remove older redundant copies after verifying the retained set and a restore drill. That retention cleanup has **not** been enabled.

The C: drive had approximately 41.5 GiB free during this review. Check weekly; investigate below 10 GiB before large downloads or upgrades. This observation is not continuous disk monitoring. A backup verified at creation is not a guarantee that every archive byte remains intact later.

## Repairs and evidence

- Upkeep now parses `.env` with the same library as the app, so duplicate settings, quoted `#` characters and environment overrides agree.
- Upkeep validates app ID, port, source directory and data directory before accepting health; its backup entry point independently verifies the expected instance.
- Backup-first housekeeping, retained archives and deferred market-hours tests were exercised with failures injected.
- The actual PowerShell compatibility helper was executed with process effects intercepted; unknown desk identity and a held automatic-launch request do not start Gateway.
- Maintenance-window tests cover exact boundaries, Eastern daylight-saving offsets, repeated-hour behavior and invalid/stale/future receipts.

Validation: 101 focused maintenance/recovery/launcher/workspace tests passed; the final two added daylight-saving boundary cases brought the maintenance-only suite to 22 passing tests. All 30 existing frontend tests passed and the new JavaScript passed syntax checking. The complete 1,455-test Python run belongs to the preceding reliability batch, not this narrower change.

Actual pre-reload backup: `state-20260926T053034-cdbbd6c7.zip`, 29 stores, SHA-256 `833cbe60d5f0d3ebcc0e016027af9f14ff9f7d49968aa10a7c3463308f0c815a`. No production restoration, cleanup deletion, order submission, cancellation or account/risk change was performed.

Evidence: task `outputs/maintenance-tests-20260926.txt` and `outputs/maintenance-frontend-20260926.txt`. The first new boundary test incorrectly expected Friday's end for the upcoming Saturday window; that test expectation was corrected. The runtime schedule calculation was already returning Saturday's 02:00 end.

Final browser checks exercised the production panel at requested width 1341 (browser reported 1342), 670 and 390, including its expandable plan. There was no horizontal document overflow or browser console error. Source context sits below the three status cards. Screenshots: task `outputs/maintenance-desktop-20260926.png` and `outputs/maintenance-mobile-20260926.png`. The temporary viewport override was reset.

At 01:40 ET build `3a909e111b0e378a` was loaded and matched disk, with the app healthy, manual mode, live automation OFF and zero unresolved submission intents. Gateway remained disconnected; current holdings/P&L were unknown. The configuration checksum was unchanged across the final reload. Runtime receipt: `outputs/maintenance-runtime-20260926.json`; final 22-case result: `outputs/maintenance-final-tests-20260926.txt`. Prior full-suite results remain historical, not a fresh run for this maintenance batch.

## Later scanner and test-evidence continuation

See [the scanner and maintenance report](SCANNER_MAINTENANCE_CONTINUATION_2026_09_26.md). Scheduled checks now include the test/runner/dependency-pin fingerprint when deciding whether a previous pass can be reused. Bounded dated tracebacks stay local under `data/upkeep/test-results`; no traceback is sent to the maintenance API. The new App checks card distinguishes current, older and unknown results. Final source passed 1,565 tests with five existing deprecation warnings. The owner subsequently authorized pausing the session; the guarded restart loaded this exact tested version. The subsequent real daily upkeep reported zero problems and reused the matching test receipt. Missing broker P&L remains a separate unresolved readiness issue.
