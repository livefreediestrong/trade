# Gateway export review and account-download repair — September 26, 2026

## What was found on this PC

The three Desktop files initially supplied contain September 22–23 API traffic and September 25 Gateway traffic. A targeted search found newer readable `api-exported-logs.txt` and `gateway-exported-logs.txt` inside the active profile under `C:\Jts\ibgateway\1051`. These cover September 26 through 17:07:52 and 17:07:44 ET respectively. No new export, decryption, credential entry or Gateway restart was needed.

The September 26 API export is 818,353 bytes, SHA-256 `26835c9a6cbc6f4ec7c841efffc3e8bdc4ac57ec9d035c6be332a0a8898d7372`. The Gateway export is 3,170,024 bytes, SHA-256 `ae425c83067bbbe807cc2c3a868cc5923dba18df4645632f147f175e28ef228b`. Raw files remain local. The task artifact `outputs/gateway-exports-evidence-20260926.json` contains only selected event names, times, request IDs, counts and source line numbers; account identifiers, money, credentials and raw messages are excluded.

## Confirmed timeline

| Time (ET) | Evidence | Meaning |
| --- | --- | --- |
| 00:49:48 | API line 639, Gateway line 2534: account P&L response 94, request 4 | The first overnight connection did receive Daily P&L. |
| 01:02:04 | Gateway lines 3266 and 3337: upstream socket closed by peer; API lines 789–790: 1100 | The broker-side connection dropped. This does not establish why the local Gateway process later disappeared. |
| 15:22:49–51 | API line 1374: P&L request 4; line 1430: response 94; Gateway line 5946 corroborates | A fresh Gateway session again supplied a numeric account P&L callback. |
| 15:42:57–58 | API line 1867: account download ended; line 2259: new P&L request 5; Gateway lines 8102–8109: request accepted and internal subscription invoked | The desk reconnected and Gateway accepted the account-wide P&L request. |
| 15:42–17:08 | 27 P&L requests, 25 P&L cancellations, 12 account-download completions and 141 current-time replies; zero P&L responses | Daily P&L is absent in Gateway's outgoing API evidence, before the app's decoder or display can process it. The ordinary socket/account path works. |

The afternoon export contains no 1100, 1101, 1102, 2100 or 2110 notification. Both Gateway and API exports agree on the missing P&L responses. This is different from the earlier September 25 outage/2100 sequence. It supports a Gateway-side emission/subscription problem for this occurrence, but does **not** prove an internal IBKR cache defect, a particular build regression, or a guaranteed workaround.

Gateway's internal P&L manager receives upstream data for the initial subscription; later client subscriptions invoke the existing manager with `forceSubscribe=false`. There is no corresponding outgoing P&L callback in the captured period. The empty trading-summary list appears in both working and failing subscriptions and is not, by itself, a failure explanation. Ordinary account-window realized/unrealized values remain a different basis; they are not substituted for Daily P&L.

## Confirmed desk repair

Five repeated `reqAccountUpdates(True, account)` requests occur after the initial download already completed on their socket. Gateway does not send another accountDownloadEnd for those repeats. The desk waited for one anyway and recorded a five-second TimeoutError as an account-download failure. The installed ib_insync method explicitly describes this as a startup subscription that does not need repeating. [IBKR documents initial account values followed by changes on that subscription](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/requesting-account-updates).

The adapter now observes the original library's real accountDownloadEnd callback before connection startup. It preserves completion of the library's waiting future and records a receipt for that connection and account. Missing-P&L recovery skips an account download already confirmed complete. Disconnects, account resets, upstream loss/data loss and account unsubscription invalidate the receipt. A receipt for another account cannot satisfy this check. This avoids a needless timeout; it never supplies a P&L value or permits risk. The existing panel can now say: “Account download completed; Daily P&L is still missing.”

The observer is confined to the existing client instance. There is no global installed-library patch, new broker connection or account/client-ID change. Existing bounded P&L retries and the requirement for an actual current-day callback remain intact. [IBKR's P&L documentation](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/profit-loss-pn-l/request-p-l-for-accounts) confirms that P&L uses its own request and callback; account-download completion is separate evidence.

## Validation

**168 focused checks passed**, including eight new receipt/invalidation cases, the real ib_insync wrapper's future completion without connecting transport, existing P&L/outage/restart cases, the actual JavaScript renderer, protective exits during an event window, and scanner behavior. Evidence: `outputs/gateway-export-recovery-tests-20260926.txt`. The initial receipt tests failed before implementation. No actual order was submitted or canceled.

The earlier automatic upkeep run reported two failures. The event-window exit fixture omitted the holding's required long/short side; it now supplies a complete long holding and account identity, with production exit guards unchanged. The scanner's five-second worker-finish check passed in the isolated recheck and final focused run; its full-suite timing failure is not claimed fixed. G10 retains that intermittent failure. No new full-suite result is claimed, and the previous upkeep failure receipt is preserved.

## Running-app verification

A fresh 29-store backup was verified before loading: `state-20260926T212107-d8058106.zip`, SHA-256 `c4f95169f0783e1c1e800ba7e100313f8ee1a026cdb954f90b114f8628ea0b43`. The app was manual with Fox disabled, no unresolved submissions, no holdings and no working orders. The normal orderly shutdown closed PID 7172, and the existing hidden launcher started PID 7556 with `-NoBrowser -NoBroker -NoDialogs`. Gateway's listener stayed PID 37296.

At 17:25 ET the app was healthy on build `3b24331827e2cc11`, equal to disk. The real account-download callback had been observed; recovery reported `account_download: already_complete` with no TimeoutError. Gateway answered its time probe. Daily P&L still had zero callbacks and risk readiness remained false. A fresh all-asset safety snapshot again showed zero holdings and zero working orders. Configuration SHA-256 remained `7053a7e80c9baac9edc122bb1bafc6b63edf9077eb93b26378c4abbbda0a2b28`; live automation remained disabled and pending submissions remained zero.

The real Fox page was reloaded and displayed the new message and version, with live OFF. The active 579 × 956 CSS-pixel viewport had no horizontal overflow or captured console errors. Evidence: task `outputs/gateway-export-recovery-runtime-20260926.json` and `outputs/gateway-export-recovery-browser-20260926.png`. This is real confirmation of the timeout repair, not a claim that missing P&L recovered or that an empty account qualifies order lifecycles.

## Next evidence and remaining limits

G01 remains open. A broker support case can use the two September 26 exports and the exact request/response timeline above. Suggested subject: **Gateway 10.51.1a accepts account reqPnL after API reconnect but sends no pnl callback while current-time/account downloads work**. Ask IBKR whether this is expected for an unchanged empty account after client reconnection and whether a supported build/configuration fix exists. No support message or raw log was sent externally.

For the earlier self-closure, the fresh log confirms upstream disconnects and reconnect attempts around 01:02, but does not contain a conclusive local process-exit reason. Do not equate a maintenance overlap with a proven crash cause. Native protective orders, options exits, practical risk limits and broker-paper/endurance qualification remain separate open tasks.

Before requesting more exports in future, check both the owner's chosen destination and Gateway's current profile directory, compare embedded session dates, and use only already-readable exports. Do not attempt to decode encrypted IBGZENC files.

## New owner export and reload — 18:37 ET

The Desktop `gateway-exported-logs.txt` was updated at 18:28:20 and now contains 41,653 lines through September 26, 18:28:19.708. Size: 5,260,274 bytes; SHA-256 `3d2f1ba7094d51114350d1ce9f027847d962bdc67689e1365546f92c095669f2`. Its sessions identify Gateway build 10.51.1a. Only allowlisted event metadata was copied into `outputs/gateway-new-export-evidence-20260926.json`; the raw export remains local.

The latest successful P&L response in this export is at **17:31:26.563**, line **32860**. At 18:00:19–20 the previous desk cancels its data subscription and closes its API session normally. The replacement subscription is accepted at **18:00:56.319**, line **36089**; the internal account/Core requests follow on lines 36147–36148 with `forceSubscribe=false`.

Between 18:00 and the export's end, the Gateway log records **eight P&L requests, eight P&L cancellations, five API-session closures, five account-download end messages and 67 current-time requests, but zero outgoing P&L responses**. The cancellation count includes the pre-reload cancellation. This extends the earlier missing-emission evidence; it does not prove why the manager is silent.

The API error-channel records in this interval contain only **2104, 2107 and 2158**, five of each. IBKR describes those as healthy market-data connectivity, an unused historical-data connection waiting for demand, and healthy contract-definition connectivity. They are not evidence of a general network failure. No 1100/1101/1102/2100/2110/326 is present in those wire records. [Official IBKR message descriptions](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes).

Runtime evidence after the export ends: a new Gateway process (33968) supplied a real P&L callback at 18:29:27; the assistant did not start it. The owner then reloaded the desk normally. Desk PID 38140 loaded source `81b4c2f8877820b8` and closed/reopened its API connection while Gateway's PID stayed unchanged. At 18:37:25 account downloads and independent responses succeeded, but P&L again had zero callbacks. The all-instrument broker snapshot was empty; live automation stayed OFF and the session paused.

The app fixes have now passed the full 1,573-test installed-source suite and daily upkeep reported no problems. They are not a fix for Gateway's observed lack of an outgoing callback. Details are in [the applied repair record](PNL_RETRY_MAINTENANCE_2026_09_26.md#applied-and-fully-tested--september-26-1837-et).

A concise **unsent** draft is saved at task `outputs/ibkr-pnl-support-draft-20260926.md`. It asks whether an unchanged empty account should receive an initial callback after reconnect, whether 10.51.1a has a known issue, and what supported recovery exists without restarting Gateway. It separates log evidence from the later runtime observations and contains no account IDs, balances or credentials. No support request, raw log or draft was transmitted.
