# Orderly restart and independent P&L diagnostics — September 26, 2026

## Confirmed findings

The previous update endpoint called `os._exit` after journal and lock handling. That bypasses registered Python cleanup, so the broker owner did not close its P&L subscription and socket through its normal lifecycle. The existing worker's ordinary exit also disconnected without canceling the P&L request first. This is a confirmed lifecycle gap; it is not proof of the recurring missing-P&L root cause.

Transport responsiveness was checked only after a numeric P&L value arrived. As a result, a connected account missing P&L also reported no verified ordinary API response, even when the API could answer. On a failed probe, every subsequent read could immediately repeat the synchronous timeout.

## Repairs

- An explicit bounded shutdown fence uses the same API owner. Its current operation finishes before cleanup. Incoming work is refused, queued work is rejected as not started, P&L data subscriptions are canceled, the socket is flushed briefly and disconnected, then the owner stops. The app releases its instance lock and exits only after cleanup confirms completion.
- A queued shutdown that exceeds its wait is canceled and cannot run later. A cleanup already underway keeps new broker work blocked until it finishes. An operation failure clears the fence for recovery, invalidates canceled P&L values and records only a safe exception type. An unavailable owner with a residual client refuses shutdown instead of claiming success or manipulating that connection from another thread.
- The cleanup does not cancel orders, stop Gateway, change the account/client ID, activate trading or alter risk settings. Existing unresolved-submission and automatic-restart gates remain in front of it.
- Current-time probes now run independently of P&L availability. A response never authorizes risk by itself. Both successful and failed probes have a short 15-second reuse interval; a failed probe never reuses the last success as readiness.

## Validation

The initial test run failed the two actual process-exit ordering/refusal checks and lacked the new lifecycle entry point (eight failures, one pass). The completed focused matrix passed **256 tests**, covering restart owner/queue behavior, active and queued operations, timeouts during waiting and during cleanup, failed disconnects, unresolved orders, P&L callback/outage handling, current-time and clock guards, current UI copy, maintenance and the earlier exit safeguards. Tests intercept broker transport and process exit; none places or cancels an actual order.

Expanded validation found two older fixture assumptions from the preceding exit batch: an exact holdings dictionary excluded the newly returned identity/timestamp, and the fake Gateway lacked its real API's `openTrades` method. The fixtures now assert the new provenance and provide realistic working trades. The reducing-order test supplies a held share. No safety gate was weakened to make these pass.

Evidence: task workspace `outputs/broker-restart-before-20260926.txt` and `outputs/broker-restart-matrix-20260926.txt`. A complete repository-suite rerun is not claimed. The existing eventkit event-loop deprecation warning remains.

## Running-app verification

A fresh 29-store backup was verified before loading the changes: `state-20260926T200458-051ce05a.zip`, SHA-256 `25a44ad54b40fd7e4414462c17f0ef72547685b91fec583b39a4c8ee01d3d625`. The previous build was stopped normally to load this repair, then the new shutdown itself was exercised once with the app manual, Fox disabled, no pending submissions/decisions and a fresh empty actual broker snapshot.

At 16:06:34 ET the local redacted lifecycle trace recorded `restart_connection_closed`, one P&L data subscription canceled and no queued requests remaining. The old desk process stopped. The existing launcher, with `-NoBroker -NoBrowser -NoDialogs`, started the updated desk; Gateway's listener process remained PID 37296 throughout. No order was placed/canceled and no Gateway restart occurred.

At 16:07 ET the running desk was healthy, PID 7172, build `701f61d0eb265a4f` equal to disk. Gateway connected and answered current-time probes, but Daily P&L remained missing with zero callbacks. Fresh actual broker evidence reported zero instruments and zero working orders. Manual mode, disabled live agent and configuration SHA-256 `7053A7E80C9BAAC9EDC122BB1BAFC6B63EDF9077EB93B26378C4ABBBDA0A2B28` stayed unchanged.

The real browser displayed the new build and the distinction “waiting for first Gateway value” alongside “Gateway responded …s ago.” At its active 579 × 956 CSS-pixel viewport there was no horizontal overflow or captured console error after reload. No UI layout change or full responsive matrix is claimed. Evidence: task `outputs/broker-restart-before-runtime-20260926.json`, `broker-restart-runtime-20260926.json` and `broker-restart-browser-20260926.png`.

Final check at 16:10 ET remained healthy and connected with a successful current-time reply, zero holdings/orders and unchanged configuration. Daily P&L was still missing after the first automatic subscription retry; account-download recovery timed out while position download completed. G01 remains open. The runtime receipt retains both the initial and final checks.

## References and limits

[Python process-exit documentation](https://docs.python.org/3/library/os.html#os._exit) states that the hard exit bypasses cleanup handlers. [IBKR account P&L documentation](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/profit-loss-pn-l/receive-p-l-for-accounts) identifies P&L as its own callback, separate from other requests. Canceling a data subscription and flushing a socket are not an acknowledgement that Gateway or its upstream P&L source has reset. G01 remains open until actual recovery is verified; native protective-stop and automatic options-close qualification remain separate G02/G03 work.
