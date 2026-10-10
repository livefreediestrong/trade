# Combined maintenance release — installed

Installed September 26, 2026 at 23:51 ET under the owner's explicit request.
Running source: `64a16ba503306688`. Previous process 38140 was stopped through
the normal guarded shutdown endpoint; the normal launcher started process 7616.
All 89 packaged files matched the candidate's recorded SHA-256 hashes.

**Follow-up September 27 at 00:04 ET:** the desk and Gateway remain connected,
but Daily P&L is unavailable again after an IBKR upstream-loss/restoration event.
The installation is complete; reliable P&L recovery is not. The app leaves new
risk blocked instead of substituting the earlier $0 value. No user trading
switch or limit was changed.

This release supersedes the earlier held P&L, protection, reconciliation,
correction-review, option-lifecycle, option-activity, research-live and insider
candidates. Their dated “not installed” statements are historical preparation
receipts, not the current installation state. Do not apply those old patches.

## Included

- Maintained IBKR adapter and P&L diagnostics, orderly shutdown and more precise
  detection of browser-only versus backend updates.
- Optional IBKR-paper stock stops, fill/fee reconciliation and explicit review
  of corrected execution evidence.
- Optional IBKR-paper option exits and changed-holdings review; local activity
  CSV evidence import and backup coverage. Real-account qualification remains
  incomplete, and these new test flags were not enabled.
- Shared curated research references and explicit account/limits/typed-confirmation
  review before live activation from Practice or Research.
- Insider-disclosure parsing, plan/ownership/footnote detail, amendment handling,
  bounded descriptive cluster flags and filters.
- [Dated decision context and explicit unknowns](DECISION_CONTEXT_2026_09_26.md)
  passed to the shared model pipeline and retained with decision evidence.

## Runtime evidence

The original Gateway process/port became unavailable before source or dependency
installation. The launcher requested Gateway startup; the owner completed normal
sign-in. At 23:53:36 ET the installed adapter reported a completed account download,
one P&L callback, responsive API and risk-ready status: $0 Daily P&L, $110 equity
and cash. No reconnect retry was needed on this startup. A quiet on-change P&L
stream is not proof of repeated reconnect recovery or market-open endurance.

The retained trace later showed this sequence (Eastern time):

1. 23:53:28: a real numeric P&L callback was accepted.
2. 23:58:29.871: IBKR reported upstream connection loss, code 1100.
3. 23:58:30.476: account download completed.
4. 23:58:32.392: a P&L callback arrived while recovery was still pending and was
   rejected by the existing connection-recovery guard.
5. 23:58:32.564: IBKR reported restored connectivity with data maintained, 1102.
6. 23:58:36 and 00:01:41: bounded replacement subscriptions received no callback.
7. 00:04:41: the existing automatic API reconnect ran; Gateway itself was not
   restarted. The replacement P&L subscription remained unverified afterward.

This is concrete callback-ordering evidence for G01. A callback before restored
connectivity is not silently relabeled as a post-recovery callback. Whether a
strictly quarantined receipt can safely be accepted after restoration needs a
separate matched protocol test; this maintenance does not weaken that rule.
The official [IBKR status page](https://www.interactivebrokers.com/en/software/systemStatus.php)
describes reset windows, but its published schedule does not establish the cause
of this particular loss. The adapter lifecycle trace is retained locally at
`data/diagnostics/ibkr-pnl.jsonl`, with a copy in the task's
`outputs/pnl-maintenance-observation.jsonl`. It contains no account identifiers
or P&L values. Repeated Gateway sign-in requests are not a demonstrated repair.

The saved configuration SHA-256 remained
`9f0d643811e66a93edbab5fd366317cf13bcdf1038483c9000e041838ee55003`.
The pre-existing controls were manual mode, session inactive and Fox disabled.
The update did not turn live trading off or on, alter dollar limits, or submit or
cancel broker orders. No pending app submissions, missing workers, unreadable
stores or automation error streak were reported after startup.

The UI still requires an explicitly verified execution account before activation;
a connected Gateway and available P&L do not themselves authorize trading.

## Validation and recovery

- Final isolated candidate: 754 Python checks across the changed paths and related
  broker, calendar, weekly, model and receipt boundaries; 8 Node controller files.
- Newly installed dependency environment: 111 focused checks; `pip check` passed.
- Normal dependencies: `ib_async 2.1.0`, `aeventkit 2.1.0`, `tzdata 2025.3`.
  The official IBKR diagnostic SDK was not installed into the normal runtime.
- Six primary pages returned HTTP 200. Browser inspection confirmed the installed
  Fox explanation, restored P&L, research activation entry and real AAPL source
  rendering. Desktop/mobile context layout had no horizontal overflow or captured
  console warnings/errors. Reading company evidence did not arm trading.
- The real AAPL reader read 8 of 8 listed Form 4/4A filings and displayed 9 rows.
  This is the bounded current submission sample, not a complete insider audit.
- Verified pre-install 29-store backup:
  `data/backups/state-20260927T035017-c48b8c2d.zip`.
  Source rollback archive and exact before/after hashes are in the task outputs:
  `maintenance-source-rollback-20260927T035130Z.zip` and
  `combined-maintenance-20260926.json`.

Rollback requires an orderly stop, restoring only the exact changed source files
from that archive and the previous timezone dependency (`tzdata 2026.4` with the
old adapter). Do not restore live state blindly or replay saved orders. Inspect
any later edits and reconcile broker state before recovery. No rollback occurred.

## Continuing maintenance and priorities

The existing Windows watchdog remains enabled every five minutes. Daily upkeep
remains enabled for 16:40 local time. Both tasks were briefly held during the
controlled installation and restored to their original enabled state. No duplicate
schedule, new credentials, external messages or subscriptions were created.

Daily upkeep retains its backup-first behavior, bounded log/database care and
after-close changed-build checks. Use the Settings maintenance panel for dated
results. A provider outage needs source evidence and backoff; a broker restart
must retain unresolved-order and exposure checks.

The next useful decision inputs are complete correlated account exposure, settled
cash/permissions, corporate-action/dividend/assignment events, dated market-depth
and halt evidence, and qualified later-session outcomes. Current $1 billion
order/daily-loss ceilings are preserved but are not practical caps for a $110
account. The owner has not approved replacement dollar values.

Entity matching also needs improvement: the actual AAPL federal-contract query
returned unrelated firms and apple-juice awards under the broad word “Apple”.
The UI already labels name matching; those values must not be treated as verified
Apple Inc. contracts or revenue. Resolve recipient/company identifiers before
using such records in automated investment reasoning.

Exa search/fetch is available in this chat and Firecrawl offers source extraction.
Neither is automatically connected to the app's background workers. An app
integration needs an explicit licensed API, private credentials, provenance,
cache and cost controls. The installed Bigdata risk-review skill is useful as a
research structure, but its provider was not connected by this maintenance.
