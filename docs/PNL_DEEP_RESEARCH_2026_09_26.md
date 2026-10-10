# Daily P&L: deep research, September 26, 2026

**Finding:** The recurring failure is most consistent with Gateway not supplying the first account P&L value to a replacement API subscriber. The desk requests it, Gateway accepts the request, and other API traffic continues, but the captured Gateway log contains no outgoing P&L response. A Gateway subscription/cache issue is the leading hypothesis; compatibility with the legacy Python client is another credible cause. Neither is a confirmed root cause or a proven fix.

**Current status at 19:06 ET:** Production process 38140 runs source `81b4c2f8877820b8`, equal to disk. The desk reports broker connected, account download complete, P&L available, one numeric callback, independent API response age zero, and no subscription retries or soft reconnects in this connection. That callback was about 24 minutes old. One quiet Saturday callback does not qualify changing market data or recovery after reconnect. Mode is manual, session paused, Fox OFF, zero pending intents and no decision in flight. This check used the existing app's read-only HTTP endpoints; no extra broker client was connected.

The owner-deferred browser-update classification patch remains separate and uninstalled. This research changed documentation and local analysis artifacts only. G01 remains open.

## Evidence from this account

The Desktop export contains 41,653 lines, ends at September 26 18:28:19.708 ET, and has SHA-256 `3d2f1ba7094d51114350d1ce9f027847d962bdc67689e1365546f92c095669f2`. The raw file remains local. Analysis excludes account identifiers, balances, credentials and raw log records.

Across the export there are **46 account P&L request records and three outgoing P&L response records**, all for one account and one API client identity, with no model filter. These are event counts, not 46 independent experiments or a reliability percentage: request windows have different lengths and some contain reconnect/startup boundaries.

| Request time, ET | First outgoing P&L | Delay | Export lines: request / response |
| --- | --- | --- | --- |
| 00:49:46.648 | 00:49:48.845 | 2.197 seconds | 2436 / 2533 |
| 15:22:49.709 | 15:22:51.892 | 2.183 seconds | 5879 / 5945 |
| 17:31:24.344 | 17:31:26.563 | 2.219 seconds | 32795 / 32860 |

Each successful example is associated with fresh Gateway startup in the reviewed timeline. Later app reconnects repeatedly fail to receive the initial value while Gateway remains running. The [earlier export review](GATEWAY_EXPORT_REVIEW_2026_09_26.md) and [retry continuation](PNL_RETRY_MAINTENANCE_2026_09_26.md) preserve the broader lifecycle evidence.

Two particularly useful failure intervals:

- **15:42–17:08, earlier active-profile API export:** 27 accepted P&L requests, no outgoing P&L response, 12 account-download completions and 141 current-time replies. The independent replies make a completely frozen socket or event loop a poor explanation for this interval.
- **18:00–18:28, fresh Desktop Gateway export:** eight P&L requests, eight P&L cancellations including the old subscription's cancellation, five API session closures, five account-download end messages and 67 current-time **requests**, but no outgoing P&L response. Do not confuse this request count with the earlier response count. Error-channel records contain only 2104, 2107 and 2158; no 1100/1101/1102/2100/2110 or duplicate-client-ID error explains this occurrence.

Successful subscription records show the internal manager adding a new subscriber, an upstream `FixPnlRequest` with type `ORIGINAL`, incoming daily-P&L and frozen-price data, availability flags becoming true, then an outgoing API response. The sustained post-18:00 failure interval has accepted subscriber requests but no new `FixPnlRequest` or outgoing P&L message.

**A useful correction:** `forceSubscribe=false` and `acctSpecs=[]` occur in both successful and failing examples. Neither flag independently identifies the fault. Internal log names suggest a subscription/cache path, but their complete semantics are not public and cannot prove a cache defect.

All three examples in this export are from Saturday. Nearby runtime snapshots report an empty account. Lack of changing positions may expose the initial-value problem, but it cannot alone explain why a fresh Gateway supplies P&L and a later subscriber does not. Earlier connection-loss episodes have other circumstances and should not all be assigned this same cause. Gateway unexpectedly closing is also a separate unresolved lifecycle issue.

## What the installed code does

Local inspection found `ib_insync 0.9.86` with supported client-protocol range 157–176 and Gateway build `10.51.1a` in the export. These are package/protocol/build versions, not interchangeable numbers.

The relevant code is in [broker_ibkr.py](../broker_ibkr.py):

- `_api_worker` owns the broker connection and pumps its event loop. `_ib` attaches P&L/error/account observers before connecting.
- The installed library registers its request-to-P&L-object mapping before sending `reqPnL`. Its response decoder maps message 94 to the P&L callback. This inspection found no obvious lost-listener race in that path.
- `_pnl_update` rejects callbacks for the wrong object/account/model or an invalidated connection. Numeric validity and session/date checks matter before risk is enabled.
- Cancellation passes the account to the high-level `ib.cancelPnL(account)` method. It does not pass the P&L object, a mistake found in an online workaround.
- Recovery already tries account/position preparation, subscription replacement and bounded soft reconnects. Earlier cooldown defects have been repaired and deployed; the captured failure persisted after those repairs.
- Account-window P&L remains a display-only fallback. The risk gate still needs the appropriate account-wide daily-P&L receipt and other account/API readiness checks.

This is code inspection, not independent qualification of every broker path. Because the failed intervals lack an outgoing Gateway P&L record, changing only the browser, renderer or Python callback handler is unlikely to fix them. Client request timing or protocol compatibility could still influence Gateway's behavior, which is why an official-client comparison matters.

## What the external research establishes

**IBKR documents the required setting and request.** Account `reqPnL` delivers updates through `EWrapper.pnl`; the portfolio-P&L preparation setting is required. The owner already confirmed it is enabled, and successful callbacks support that finding. The reviewed request/response documentation does not specify a maximum first-response time or clearly guarantee an unchanged empty account's initial value after reconnect. That is an explicit question for support. [IBKR account P&L request documentation](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/profit-loss-pn-l/request-p-l-for-accounts), [response documentation](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/profit-loss-pn-l/receive-p-l-for-accounts).

**The client library is a maintenance concern.** IBKR says original `ib_insync` uses a legacy API and is no longer updated. For users retaining that style it points to `ib_async`, while preferring its direct API for support. This supports a qualified migration or official-client test; it does not promise that migration cures this P&L failure. [IBKR library guidance](https://www.interactivebrokers.com/docs/tws-api/doc/third-party-api-platforms/non-standard-tws-api-languages-and-packages/ib-insync-and-ib-async).

**The Gateway build family deserves comparison.** At research time, IBKR's TWS release index lists 10.51 under Beta and 10.50 under Production. The captured Gateway is 10.51.1a. This is a version-family lead, not independent confirmation of the installed Gateway's channel. Verify its exact channel and a supported comparison build before changing anything. Reviewed 10.51, 10.50 and 10.48 notes do not identify a fix for this exact symptom. [Release index](https://www.ibkrguides.com/releasenotes/tws.htm), [10.51](https://www.ibkrguides.com/releasenotes/version-10.51.htm), [10.50](https://www.ibkrguides.com/releasenotes/version-10.50.htm), [10.48](https://www.ibkrguides.com/releasenotes/version-10.48.htm). IBKR offers separate Latest and Stable Gateway downloads; the download description is not a specific fix recommendation. [Gateway downloads](https://www.interactivebrokers.com/en/trading/ibgateway-latest.php).

**Reddit contains a similar symptom, but no verified fix for ours.** A developer describes P&L subscriptions silently dying after reconnect while using `ib_async`. That anecdote also mislabels error 1102 as data lost. IBKR defines **1101 as restored with data lost** and **1102 as restored with data maintained**. The cited comment is a research lead, not a recovery specification, and shows why a library swap alone cannot be assumed sufficient. [Original Reddit discussion](https://www.reddit.com/r/interactivebrokers/comments/1rzt7bn/built_an_automated_trading_system_on_ibkrs_api/), [IBKR message-code definitions](https://interactivebrokers.github.io/tws-api/message_codes.html).

**Not all P&L fields are equivalent.** IBKR distinguishes account-window P&L from portfolio-window P&L, including source and reset differences. Its roughly once-per-second statement is for individual-position `reqPnLSingle`, not a guaranteed account-wide heartbeat. An unset number is not zero. These distinctions rule out substituting arbitrary balances or declaring a quiet account broken solely because its last value is old. [IBKR P&L definitions](https://interactivebrokers.github.io/tws-api/pnl.html).

**Other reconnect reports are weaker matches.** Historical `ib_insync` issues discuss delayed socket closure and client-ID conflicts. Those are worth considering generally, but the current captured subscriptions were accepted and no client-ID collision appears. Repeatedly changing production client IDs would not be an evidence-based repair. [Maintainer issue 376](https://github.com/erdewit/ib_insync/issues/376), [issue 76](https://github.com/erdewit/ib_insync/issues/76). Another library's maintainer documents pre-market stale daily P&L and conditional resubscription, but that is an observation in a different implementation, not an IBKR guarantee or a demonstrated solution after our repeated failed requests. [Maintainer API documentation](https://pkg.go.dev/github.com/osauer/ibkr/pkg/ibkr).

## Ranked explanations

| Explanation | Fit to current evidence | What would distinguish it |
| --- | --- | --- |
| Gateway fails to supply/replay the initial account value to a later subscriber | Strongest: fresh start works, later accepted requests are silent, other traffic continues | Same failure with IBKR's official minimal client, plus support confirmation |
| Gateway build and legacy client-protocol interaction | Plausible: current stack combines 10.51.1a and old `ib_insync` | Compare clients on one Gateway build, then builds with one client |
| Empty/unchanged account or weekend data acts as a trigger | Plausible condition, not sufficient explanation by itself | Compare initial receipt and reconnect during a weekday using naturally available data |
| App listener, mapping or request-timing defect | Lower for captured silent intervals; not fully excluded | Official client consistently succeeds under matched conditions while the wrapper fails |
| General network failure, missing setting or duplicate client ID | Weaker for these intervals | A contrary fresh log, rejected request or failed independent response would raise its priority |

These rankings express investigative priority, not measured probabilities.

## Next maintenance-window test plan

No test below ran during this research. The current working connection and deferred update were preserved.

1. **Prepare a minimal official-API reproduction.** Use an isolated environment and IBKR's supported API, with no order methods. Prefer a paper account. Wait for the connection handshake and account identification, then request account P&L with no model filter. Record only client/build/protocol versions, request IDs, receipt timestamps, value validity, account-download completion and independent time responses. Keep account identifiers and values local.
2. **Compare initial connection and replacement connection.** Use one diagnostic client at a time, a reserved nonzero ID and the same account/configuration. Observe one subscription for a fixed diagnostic window, such as 180 seconds; that is a test window, not an IBKR service guarantee. Cancel only its own data subscription, close cleanly, then reconnect two or three times. Do not run beside the production broker owner to manufacture a result. A production-account reproduction, if paper cannot reproduce it, needs an attended maintenance window.
3. **Change one variable at a time.** Compare the official API with the installed wrapper on the same Gateway build; then compare a verified supported production/Stable Gateway build using the same official client. Preserve rollback installers/settings first. A failure in both clients points toward Gateway/upstream behavior; a repeatable client-specific difference points toward timing/protocol/wrapper behavior. A build-specific difference supports a Gateway regression but still needs repeated validation.
4. **Check the market-state condition.** Observe a weekday and naturally changing paper data; do not place a live trade merely to make P&L move. A successful initial receipt is distinct from proof of changing values, same-session resubscription, reconnect, day rollover and nightly reset. Record each separately.
5. **Escalate a focused case to IBKR if needed.** The local unsent draft asks about empty-account initial callbacks, 10.51.1a compatibility and a supported refresh mechanism. Add the official-client result before presenting it as a reproducible supported-API defect. The existing exports already cover the failure; another blind restart/export request is unnecessary.

Plan any eventual `ib_async` migration as a broker integration change: verify P&L, existing commission handling, positions, order/fill/cancel reconciliation and disconnect behavior in isolation and broker paper. Do not install it into production merely because imports look similar. Broader separation of the broker connection from browser/server reloads may reduce exposure to this trigger later, but it is not the first diagnostic step and does not repair Gateway itself.

**Workarounds not justified here:** repeated rapid resets; clearing the risk gate by treating missing P&L as zero; carrying an old callback across a disconnected session; adding realized and unrealized account-window values as a daily-loss substitute; changing production client IDs; or taking a position just to provoke an update. The staged browser-update classification patch can reduce avoidable future reconnects after installation, but is not a demonstrated fix for Gateway's P&L behavior.

## Reproducibility and handoff

Local artifacts are in `C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs`:

- `analyze-pnl-subscriptions-20260926.py` and `pnl-subscription-analysis-20260926.json`: allowlisted metadata extraction. Chronological request windows can cross session boundaries; do not interpret every internal event within a window as caused by that request.
- `verify-pnl-subscription-analysis-20260926.py`: passed checks of the exact export hash, independent event counts, all three response timestamps/latencies, post-18:00 silence, successful/failed flag comparisons and exclusion of account identifiers.
- `pnl-deep-research-runtime-20260926.json`: read-only 19:06 production status receipt.
- `ibkr-pnl-support-draft-20260926.md`: revised local draft; not sent and no logs uploaded.

No production-code test suite was rerun because this batch changed no product code. Log-analysis checks and the real read-only runtime check support the findings; neither is broker-paper recovery qualification. Keep G01 open, preserve the working session, and keep the owner-deferred update staged until the next maintenance window.
