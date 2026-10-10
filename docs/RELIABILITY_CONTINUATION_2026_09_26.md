# Reliability continuation — September 26, 2026

This batch repairs concrete execution and recovery faults, adds a read-only view of broker protection, and preserves the broader unfinished work in `TASKS.md`. It does not qualify the app for unattended live trading.

## Current operating state

At 01:14 ET the production desk on port 5056 loaded build `1846f3737287fbfa`, matching the on-disk fingerprint. Execution mode was manual, the live agent was OFF, and the app had zero unresolved submission intents. Gateway was disconnected, so current holdings and Daily P&L were unknown. No broker orders, cancellations, account switches, risk increases or data purchases were performed for this batch.

The existing saved billion-dollar caps remain effectively open. Replacement daily-loss, position and total-exposure dollar limits were requested from the owner and have not been silently chosen.

## Gateway findings

The old Windows `tasklist /V` scan could hang on an unrelated window and stop the launcher from deciding whether Gateway existed. The replacement obtains process names/titles and Java command lines as a structured, bounded Windows query. Incomplete or unreadable results still mean “unknown,” never “absent.” Existing cross-process launch locking, cooldowns and attempt budgets remain.

The repaired scan completed in about 0.43 seconds on this computer, and one Gateway launch succeeded. After the owner signed in, the real account reader returned connected=true, risk_ready=true and numeric Daily P&L. Gateway disappeared again by about 01:02 ET, before the desk was stopped or reloaded. The owner confirmed the program closed on its own.

The launcher log reports daily auto-restart enabled. The latest matching JVM crash files are from September 24; no matching new Windows Application crash/hang record or CrashDumps file was found. Current Gateway settings and detailed logs have an encrypted IBGZENC header. The readable API export is dated September 23 and cannot explain this occurrence. A fresh Gateway/API export was requested; native Gateway controls are unavailable in this session.

[IBKR's status page](https://www.interactivebrokers.com/en/software/systemStatus.php), read September 26, reported active scheduled maintenance starting at midnight and a Saturday 00:00–02:00 ET window. This overlaps the observed loss of connection. It is a plausible explanation for connectivity trouble, not proof of why the local process exited. [IBKR's Gateway documentation](https://www.interactivebrokers.com/docs/tws-api/doc/architecture/the-trader-workstation/the-ib-gateway) distinguishes daily auto-restart from required weekly reauthentication. Repeated blind sign-ins during maintenance are not a durability test.

Next evidence: fresh exported Gateway and API logs, normal sign-in after maintenance, then a sustained read-only connection/P&L check. Preserve account identifiers and credentials locally; no logs were uploaded.

## Repairs delivered

- **Uncertain option submissions:** a socket exception after `placeOrder` for a single option or spread now marks submission as attempted. The app retains its durable unresolved intent and prevents a second send until reconciliation. Preflight rejections still remain definite non-submissions. Tests exercise the selected IBKR helper and application ledger path with an injected lost acknowledgement.
- **Conflicting option orders:** single-option actions check for working spreads containing that contract. Spread closes reject overlapping single-option/spread orders; qualified legs must be distinct and match supplied contract identities. These guards do not implement autonomous assignment recovery.
- **Stalled broker reads:** read requests have a 30-second caller deadline, a bounded queue and cancellation only while still queued. The running API owner is retained. Writes are not automatically retried or treated as unsent because a caller stops waiting. Tests block the real owner queue, verify bounded accumulation and recover it after releasing the blocked operation.
- **Restart exposure:** automatic restart checks include all held IBKR asset types and currencies, so an apparently empty stock panel cannot hide options, futures or foreign-currency holdings. Unknown broker data prevents an automatic restart.
- **Protection and expiry display:** Fox's agent controls now contain “What protection is at the broker?” The GET-only monitor reads account-matched holdings and working orders. Only one active, transmitted, exact-quantity standalone stop gets the “broker reports a working stop” wording. Partial, duplicate, contingent, held or linked orders remain for review. Option expiry and short-assignment concerns remain visible. Evidence older than 45 seconds is discarded; a disconnect never becomes zero holdings. This display does not create, replace or cancel any order.
- **JEV response parsing:** the current [Typesafe API](https://docs.typesafe.ai/api) defines `choice` responses with their own confidence and probability distribution. The adapter now uses that field, rejects inconsistent/invalid distributions and removes the separate `noul` question incorrectly used as confidence. JEV remains locked: neither supported API-key variable is configured, and no real provider qualification was possible.
- **Scanner work:** per-symbol result and progress updates now share transactions instead of repeatedly reopening the database. Saved progress still commits per symbol and locks are released before network work. This addressed a real five-second test deadline failure without merely increasing the deadline.

The monitor is read-only evidence. App-managed exits still need the app and Gateway to be running. Broker-native protective order creation, rejection/replacement/restart recovery, and autonomous option exercise/assignment handling remain G02/G03.

## Validation and its limits

- Final complete Python suite: **1,455 passed**, four existing datetime deprecation warnings, 89.67 seconds. The initial run had 1,446 passed and one scanner deadline failure; that failure is retained in the evidence. After the transaction change, the 106-symbol isolated case passed in 2.03 seconds and the complete suite passed.
- Frontend: **30 passed**. Focused broker reliability matrix: **177 passed**. JEV/brain matrix: **74 passed**. Those are scoped mock/isolated results, not broker-paper trading receipts.
- Real browser, production app: new panel displayed disconnected/unknown correctly at requested width 1341 (browser reported 1342), 670 and 390. No horizontal document overflow or browser console errors appeared. Viewport override was reset. No account/activation controls were changed. This is not a complete WCAG or 200% zoom audit.
- Read-only HTTP burst: 180 requests, four concurrent, all valid HTTP 200 JSON responses in 3.4 seconds. Endpoint p95: health 96.61 ms, lite state 176.43 ms, broker safety 37.01 ms. Gateway was disconnected and the stock market was closed; this is not broker throughput, sustained memory or market-open endurance evidence.
- Pre-reload verified backup: `state-20260926T050244-8d033d08.zip`, 29 stores; SHA-256 `2b1b2d76b444eaeecce6335dfc3856e65c3b29b7dfdb9f3a70793561fa1ee8fb`. Production state was not restored. Earlier isolated real-reader restoration of the same 29-store schema is recorded in `EXPERIENCE_AND_WEEKLY_REVIEW_2026_09_26.md`.

Evidence directory: `C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs`.

- `reliability-python-20260926.txt` — initial scanner failure.
- `reliability-python-final-20260926.txt` — final complete Python result.
- `reliability-frontend-20260926.txt` — frontend results.
- `reliability-runtime-20260926.json` — redacted operating-state and HTTP sample receipt.
- `broker-protection-desktop-20260926.png` and `broker-protection-mobile-20260926.png` — real production screenshots.

## Remaining work

The primary queue still includes durable Gateway recovery; native protective exits and full options lifecycle; meaningful owner-selected risk limits; a prospective full-policy sample after costs; market-open endurance; and conditional Reddit/X/licensed data access. Source history, additional themes/workspace arrangements and broader exposure breakdowns remain as separately tracked work. No unavailable data, negative research result or missing provider access was converted into a completed checkbox.
