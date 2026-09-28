# Scanner and maintenance repairs — September 26, 2026

This continuation addresses the intermittent directory-scan test failure, malformed ranking data and incomplete maintenance evidence. It does not activate trading, change money limits, implement broker-held protection or qualify a strategy.

## Reproduced problems and repairs

- A scan opened and closed SQLite twice per symbol. Most time in the 106-symbol local profile was database commit/close work. The worker now keeps one thread-owned connection for a pass, commits every checkpoint, and closes it on completion or pause. Provider requests do not hold the scanner lock or an open write transaction. Other threads keep independent connections.
- Missing `below_typical_pct` stopped the entire pass with the symbol still pending. Strings, booleans and objects could also be accepted as valid ranking metrics. Ten new regression cases initially failed. Results now require the right symbol/session and finite numeric ranking values. Numeric strings are normalized; missing/invalid values and impossible negative volumes are excluded, allowing the pass to continue. Optional absent measurements remain absent.
- A transient worker database-open failure now records a paused state and preserves completed results. Pause/resume, external visibility of committed rows and connection cleanup are exercised.
- The 106-symbol functional test used a five-second disk-speed deadline. It now permits 30 seconds while still requiring the actual worker to finish and all 106 unique results to be present. This is not a performance acceptance threshold.
- Scheduled tests previously discarded tracebacks and reused a passing application fingerprint even when the tests changed. Upkeep now saves dated local details (bounded to 200,000 output characters), preserves partial timeout output, records the exit code and checks a separate fingerprint of tests, runner and dependency pins. A concurrent source/test edit invalidates the receipt.
- Settings' maintenance section now distinguishes current pass/failure, older results, missing evidence and an update waiting to load. Details are collapsible and use text-safe rendering. Traceback contents are not returned by this panel's API. A local test pass is explicitly separate from live-trading qualification.

## Measured evidence

An isolated comparison used 106 deterministic histories, no network and the same validation/durable commits. Per-transaction connections took 1.903, 1.838 and 1.881 seconds; the worker-owned connection took 0.345, 0.374 and 0.394 seconds. Medians were **1.881 vs 0.374 seconds**, an **80.1% reduction in this local processing test**. Provider latency and pacing were excluded; this is not a claim that live scans or the whole app are 80% faster.

Focused research/scanner/maintenance/exit checks: 130 passed before the final startup guard. The scanner/maintenance/watchdog matrix after that guard: 71 passed. The final complete scheduled suite: **1,565 passed, five warnings in 90.67 seconds**. The warnings concern existing event-loop and UTC deprecations; they are not claimed fixed.

Full-suite source fingerprint: `ea2b993407099235`; test fingerprint: `34764c27ff1116a6`. Local details: `data/upkeep/test-results/20260926T174810356891.txt`. Both failures in the earlier 1,534-test upkeep run are absent from this final run.

Browser verification uses the real maintenance module, template fragment, styles and script in an isolated read-only preview without importing the trading app. The requested 1341-pixel viewport is reported as 1342 by the browser; 390-pixel mobile was also checked. Four cards render, result details expand, no horizontal overflow and no captured console errors/warnings. This preview is component evidence, not a reload of the active app.

## Runtime and deployment boundary

At 17:44 ET the existing desk (PID 7556, build `3b24331827e2cc11`) was manual, Fox OFF, no decision in flight, no pending submissions and no stock holdings. Gateway was responding and Daily P&L had one numeric callback. Its process was now 23768, different from the previous turn's 37296; this continuation did not restart Gateway and does not claim to know why the process changed. G01's recurring-loss cause remains open.

Verified backup before requesting a reload: `state-20260926T214528-50f66a4c.zip`, 29 stores, SHA-256 `6923f9fbe7fb9cbdd36b339413cc7276a0a122c2ce0cd39d31c73bc83041df6a`.

The app's guarded restart refused with **Active trading session; update restart deferred**. `config.session_active` is true despite manual mode/Fox OFF. No guard was bypassed and no session, risk or broker settings were changed. The source update and tests are ready; permission to pause that session for the reload was requested. Runtime reload and full Settings-page verification remain pending that decision.

Primary remaining work is tracked in TASKS.md: durable Gateway/P&L recovery, native protective-order lifecycle, automatic options exits/assignment, practical risk limits, prospective after-cost strategy evidence and market-open endurance.

## Applied after approval — September 26, 18:05 ET

The owner's next "continue" authorized the previously proposed session pause and desk-only restart. Created and verified another 29-store backup, `state-20260926T220004-7353c91e.zip`, then used `/api/session/stop`. A before/after comparison confirms configuration outside the approved pause fields was unchanged. The existing guarded restart accepted the request; PID 7556 exited normally. The hidden launcher with `-NoBrowser -NoBroker -NoDialogs` started PID 3212 with source `ea2b993407099235`, matching disk and the 1,565-test receipt. Gateway PID 23768 remained running throughout. No orders were placed or canceled, and live trading remains OFF.

The real daily upkeep completed in 14.6 seconds with a verified backup, successful database checks and zero recorded problems. It recognized the existing exact-source/test-fingerprint pass and correctly skipped an unnecessary repeat suite. The prior failed-upkeep marker was cleared. The installed Settings page was checked at desktop (1342 reported pixels) and 390-pixel widths with no horizontal overflow or captured console errors. Test details were opened by keyboard and screenshot evidence saved. A minor follow-up remains: preserve an open details panel across periodic/visibility refreshes; rebuilding the card currently discards that state.

Daily P&L **did not return after the desk's new API connection**. At 18:05 ET account download and positions had completed, independent Gateway responses were fresh and one automatic subscription retry had run, but callback count remained zero. The all-instrument safety snapshot reported zero holdings and zero working orders; no submissions were pending. `risk_ready=false` correctly blocks new risk. This reproduces G01 without a Gateway process restart and does not establish a new root cause. The session stays paused; Gateway is left open.

Artifacts in the task's `outputs` folder: `scanner-upkeep-deploy-20260926.json`, `scanner-maintenance-installed-20260926.png`, and `scanner-maintenance-installed-mobile-20260926.png`. No source changed during this application step; the saved full-suite pass remains applicable. Runtime verification is separate from that test evidence.
