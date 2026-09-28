# Browser updates and connection preservation — September 26, 2026

## Status: tested and saved, not installed

The owner chose **save for the next maintenance window**. Production source and the running desk remain `81b4c2f8877820b8`; no application source, restart task, trading setting or broker connection was changed for this batch. Only documentation was updated in the production folder. No maintenance date or new scheduled job was created.

The tested copy is in `C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs\update-classification-stage`. Its full source fingerprint is `0d62d826c3bb5fa3`; test/runner fingerprint is `5c1cc05bd7cd50f4`.

## Problem and prepared repair

The current update detector combines Python, templates, JavaScript and CSS into one restart signal. Because the watchdog can restart an idle, verified-empty desk for a code update, a visual edit can unnecessarily disconnect a working P&L subscription. Repeated P&L loss after desk reconnects makes that distinction useful even though it does not explain Gateway's missing callbacks.

The prepared repair separates the Python restart check from the full source fingerprint. Python edits, additions and deletions still require the existing guarded restart. Templates and browser scripts/styles can be fetched again without a desk-process restart. The immutable `started` field remains available for startup and policy evidence; a separate `serving` field describes current files only while Python still matches the startup version. It does not claim that an older open tab has already fetched new assets.

Maintenance and daily upkeep compare test receipts with the full current version, including browser files. A visual edit therefore invalidates an older passing result. The small version label can describe the current files after refresh, while still identifying pending Python updates. No order, risk, P&L-retry or automatic-restart safety gate is changed.

The digest cache also distinguishes absolute source paths. This avoids reusing a same-name, same-size, same-timestamp file digest from a different checkout during comparison or testing.

## Validation

From the isolated copy, using the existing virtual environment:

```text
python -m pytest -q tests/test_code_updates.py tests/test_desk_restart.py tests/test_maintenance_plan.py tests/test_self_recovery.py tests/test_launcher.py --tb=short
93 passed in 23.87s
```

The checks cover:

- Template, JavaScript and CSS edits change the full fingerprint without requesting a restart.
- Python edits, additions, deletions and mixed Python/browser changes still request a restart.
- The real Flask page route, Jinja template reload and static-file conditional response serve changed content in the same process.
- The Windows launcher does not offer a restart for a browser-only update, and the watchdog neither restarts the desk nor touches a healthy Gateway for it. Process effects are intercepted in tests.
- A browser change forces a new test run; old receipts remain visibly old and the new receipt retains the full source version.
- Existing stale-code, restart-refusal, active-session, unresolved-order and broker-exposure guards still pass.

`git apply --check` succeeded against the current production working tree. No patch was applied. This is isolated application/launcher validation, not a live restart, real-browser reload check or qualification of broker reconnection.

## Current broker evidence

At **18:55 ET**, production desk PID 38140 and Gateway listener PID 35552 remained connected. The desk had a numeric P&L callback and reported risk-ready; the independent API response age was zero, with no subscription retries or soft reconnects on this connection. The P&L update mode is on-change, so absence of repeated unchanged callbacks alone is not a stale-feed finding.

Gateway's process had changed before the first check in this continuation, and a callback arrived at 18:42:24. The assistant did not restart it and has not established what caused the process change. P&L recovery after future reconnects remains unqualified; G01 stays open.

The desk remained manual, session paused, Fox disabled, zero unresolved app submissions and no decision in flight. No orders were submitted or canceled. The current source still matches its startup fingerprint. This preserves the owner's choice to hold the prepared backend update.

## Installation handoff

The task's `outputs` folder contains:

- `update-classification-20260926.patch`: eight-file patch, 195 insertions and 19 deletions, based on the existing dirty working tree rather than a clean checkout.
- `update-classification-20260926.json`: original and staged file checksums, source/test fingerprints and validation scope.
- `update-classification-runtime-20260926.json`: redacted runtime receipt; no account identifier, balance or credentials.
- `update-classification-stage`: tested source copy, separate from production and the existing preview on port 5057.

At the owner-selected maintenance window, first verify the manifest's original file checksums. If any source file has changed, reconcile the patch with those edits instead of overwriting them. Recheck patch applicability, apply the reviewed files, and use the normal allowed update flow. This classification repair itself changes Python and therefore needs one backend reload before future visual edits can avoid it. Verify the resulting running fingerprint and actual P&L state afterwards; a passing test cannot establish delivery of broker callbacks.

An earlier direct stop/relaunch command was rejected by automatic approval review without a more specific reason. It was not retried, and the watchdog was not used to work around that rejection. The owner subsequently chose to defer this separate update. Do not apply it early merely because the current connection is healthy.
