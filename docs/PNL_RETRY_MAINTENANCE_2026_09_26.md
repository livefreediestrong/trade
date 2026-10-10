# P&L retry and maintenance-panel continuation — September 26, 2026

## Changes and evidence

Three defects were reproduced before repair:

- An active but unusable Daily P&L callback (missing, NaN, infinity or IBKR's unset sentinel) reset the accumulated soft-reconnect counter, cleared pending recovery and marked the scheduled refresh as successful. The callback remains counted as received, but only a usable number now completes recovery. Missing P&L still blocks new risk; zero and negative valid P&L are accepted normally.
- The silent-subscription path could cancel and replace the current request during the existing soft-reconnect cooldown. That path now honors the same deadline. It leaves the request available for a delayed callback, continues independent API-response checks and resumes permitted retries when the cooldown expires. This does not introduce a finite retry budget or claim to repair Gateway's missing emission.
- Settings' maintenance card replaced an open details element on each periodic or visibility refresh. It now preserves the open/closed state and restores keyboard focus only if the replaced summary had focus. A refresh does not take focus from an unrelated control. Failed reads still clear stale status and report that current evidence is unknown.

Eight new broker cases exercise the first two changes. Before repair, five of the initial seven broker cases failed; the two valid-callback controls passed. The frontend regression failed because a refresh closed the details. After repair, the final focused matrix passed **126 tests, one existing event-loop deprecation warning**:

`python -m pytest tests/test_pnl_stream_safety.py tests/test_pnl_initial_recovery.py tests/test_broker_restart.py tests/test_ibkr_connection.py tests/test_broker_lifecycle.py tests/test_maintenance_plan.py -q`

The real maintenance renderer's Node regression and JavaScript syntax check also passed. Broker tests use temporary data and mocked transport. They do not establish recovery on the real Gateway or broker-paper order qualification. The previous 1,565-test full-suite receipt is historical; no full-suite rerun is claimed for this batch.

## Actual browser verification

The existing production Settings page loaded `desk_maintenance.js?v=maintenance-20260926-4` without a server restart. A real `/api/maintenance` HTTP 200 refresh was observed while the details stayed open and their summary retained keyboard focus. Keyboard close/reopen also worked. Desktop width requested 1341 (browser reports 1342) and mobile 390 showed no horizontal overflow; no captured browser warnings/errors were reported. Screenshots are saved in the task outputs folder. Mouse clicks through the browser automation interface did not consistently toggle the disclosure; do not claim mouse-toggle qualification from the keyboard/refresh evidence.

## Deployment and current broker state

At **18:24 ET**, the server remained PID 3212, running source `ea2b993407099235`; the saved source is `81b4c2f8877820b8`. The frontend repair is loaded, but the two Python retry repairs are **pending a permitted server reload**. The existing automatic restart endpoint returned `Broker state unavailable; automatic restart deferred`. The guard was respected. Neither the desk process nor Gateway was forcibly stopped.

A new 29-store backup, `state-20260926T222048-d0cd72f3.zip`, passed verification at 18:21 ET. SHA-256: `ae442877fb07f1df1d09e29ad5b1ae295b71b088b01acc730ddb66ba84a81662`. This is backup creation/verification evidence, not a new restore drill.

The actual account download and positions request completed; independent API responses were current. Daily P&L still had **zero callbacks** after six subscription retries and three soft reconnects in the existing process. Risk readiness remained false. A fresh all-instrument snapshot reported zero holdings and zero working orders. Manual mode, session paused, Fox OFF, zero unresolved submissions and no decision in flight were confirmed. Gateway's listener remained PID 23768. A separate existing preview on port 5057 was disconnected from the broker; no competing desk API connection was found. It was left untouched.

IBKR documents account Daily P&L as a separate `reqPnL`/`pnl` subscription and requires the portfolio-P&L setting the owner already confirmed enabled: [official account P&L documentation](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/profit-loss-pn-l/request-p-l-for-accounts). Account-download success cannot establish delivery of that subscription. No new root cause is proven, and no further Gateway restart or sign-in was requested.

## Handoff

- G01 remains open: find the cause of missing Gateway P&L emission and qualify durable recovery. Load the tested retry repairs through an allowed desk restart when conditions permit; do not bypass the current guard just to report deployment.
- G10 maintenance disclosure refresh is repaired; mouse interaction remains an explicitly limited browser check. Market-open endurance remains unqualified.
- The running connection panel showed a Federal Reserve feed HTTP 404 at 18:23 ET, after an earlier successful read. Recheck the exact source and distinguish a transient response from a changed URL before claiming another feed repair.
- G02/G03 native protective-order and options lifecycle, G04 practical risk limits/activation, and G08 prospective after-cost evidence remain in TASKS.md.

Task artifacts: `outputs/pnl-retry-maintenance-20260926.json`, `outputs/maintenance-refresh-desktop-20260926.png`, `outputs/maintenance-refresh-mobile-20260926.png`. No order was submitted or canceled, no trading settings were changed, and no logs were transmitted externally.

## Applied and fully tested — September 26, 18:37 ET

The owner supplied a new Gateway export through 18:28:19.708 and then reopened the desk through its normal shortcut. This supersedes the pending-reload status above. The assistant's earlier combined stop/relaunch command was rejected by automatic approval review with only “blocked by policy”; it did not run and was not retried by another mechanism. The owner performed the normal reload.

The installed desk is now PID 38140 on `81b4c2f8877820b8`, equal to disk. The redacted trace confirms orderly closure at 18:32:47, cancellation of one P&L data subscription, and a new account-download completion at 18:32:59. Gateway stayed PID 33968 during that reload. A new Gateway process had already replaced PID 23768 before the owner's desk reload; an actual P&L callback arrived at 18:29:27. The assistant did not restart Gateway and does not know what initiated that earlier process change.

The new desk subscription received no P&L callback, including after its first automatic replacement at 18:36:02. At 18:37:25 the independent API reply was current, account download was complete and risk readiness remained false. This qualifies deployment of the retry repairs, **not recovery of the missing P&L feed**. Manual mode, session paused, Fox OFF, no decision/pending submission and a fresh empty all-instrument holdings/orders snapshot were confirmed.

Actual daily upkeep completed in 116.2 seconds with zero reported problems. Its full installed-source suite passed **1,573 tests, five existing deprecation warnings, in 95.52 seconds**. Source fingerprint `81b4c2f8877820b8`, test fingerprint `0618d6dc62a48790`; saved details: `data/upkeep/test-results/20260926T183700858741.txt`. The real Settings page now correctly reports that the current version passed; expanded details and the current version label were inspected and captured. No horizontal overflow was observed at the requested desktop width (1342 reported).

The configuration stores in the pre-reload and post-reload backups are byte-identical. Both the pre-reload backup `state-20260926T223123-a4276241.zip` and the upkeep backup `state-20260926T223504-d06850a4.zip` were verified, each with 29 stores. The latter's SHA-256 is `4a4a1175b46682b4ddd29a874139f7c4f29f49e31b2c21c599927a9079d23f8f`. The Federal Reserve RSS endpoint returned HTTP 200 with 20 items, and the app's own source status subsequently reported connected with no error. Its prior transient 404 does not currently justify changing the source URL.

Additional evidence and the unsent support draft are described in the [new Gateway export review](GATEWAY_EXPORT_REVIEW_2026_09_26.md#new-owner-export-and-reload--1837-et). Artifacts: `gateway-new-export-evidence-20260926.json`, `gateway-new-export-runtime-20260926.json`, `gateway-new-export-installed-20260926.png`, and `ibkr-pnl-support-draft-20260926.md` in the task outputs folder.
