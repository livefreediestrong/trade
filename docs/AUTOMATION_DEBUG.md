# Automation debugging — September 24–25, 2026

## September 25 reliability pass

Failure injection reproduced stalled work flags, scheduler starvation and incorrect retry accounting. Repairs retain the existing configuration, budgets and order authorization path:

- Live-agent research runs on one guarded worker. Reconciliation can continue while research is waiting on a provider. The cycle lock is claimed before launching, and no replacement is launched while that worker is alive. A pause or unresolved order arriving during research still blocks submission.
- Only completed WATCH/AVOID screens receive the free 30-second rotation. Provider failures and unexplained empty responses retain their reserved research attempt and full configured interval, including across restarts.
- Notebook, paper research, headlines, buzz, backtest, directory scanner, paper loop and optional dashboard context release their busy/running claims if worker launch fails. Existing notebook/headline/paper cooldowns remain in force. User-triggered backtests/scans can be retried explicitly.
- Optional news or review failures no longer skip the daily notebook. Per-job scheduler errors are visible and clear after a healthy check.
- Paper outcome, due-exit and report checks each get a chance to run. Any maintenance failure holds new research cycles until recovery; already-held paper positions can still be checked for due exits.
- A failed journal write cannot terminate the main scheduler or strand the live-agent cycle lock.
- Paper status distinguishes actual research from waiting for its next eligible cycle, displays the next time, and exposes maintenance errors. Companion activity reflects those errors as well.
- The shared companion controller updates only panels present on the current page. Missing notebook, workday, journal or plan panels no longer abort rendering or make a healthy paper status appear stale. Events still reach both autonomous companions on every page.

`tests/test_automation_reliability.py` exercises actual scanner-to-agent failure accounting, thread-launch failures/recovery, a deliberately blocked live-research worker with continued reconciliation, pause/pending-order arrival during research, and a real local-paper ledger exit despite a failed outcome checker. All use isolated storage and intercepted external I/O. These are software checks, not live broker execution or profitability evidence.

## September 24 scheduler pass

Eight reproduced defects were repaired in the existing schedulers. Nine initial regression cases failed before the changes. No live order, cancellation, arming action or trading configuration change was used to reproduce them.

| Defect | Result after repair |
| --- | --- |
| A direct stock ticket lacks the scanner's expected `ts`, causing repeated background errors. | New tickets include both timestamps. Scanning also tolerates older missing, malformed, timezone-less and future timestamps. Manual drafts do not reset research cadence. |
| A scan returning no candidate or raising an exception retries every five seconds, ignoring its configured interval. | Every attempt reserves its interval before network work, including empty and failed attempts. |
| With the live session stopped, an enabled Moss paper workday still wakes the separate live scanner. | Moss owns its paper collection; a stopped live session no longer borrows its enablement. |
| Independent paper research with the live session stopped creates a live-workspace signal before making a paper copy. | The background scanner selects paper settings and labels the result paper from the outset. |
| A scan begun in manual mode can be ingested after the operator changes execution mode. | Scheduled work checks its captured settings before further collection, before model enrichment and again at ingestion. Changes discard the old result. Existing submission gates remain in place. |
| Calling the companion's start method twice creates duplicate scheduler threads. | Each scheduler has one tracked thread; repeated starts reuse live workers and can replace a dead one. |
| One ticker/provider exception aborts the entire Moss candidate batch. | That candidate is recorded as unavailable and excluded; the remaining candidates can be evaluated. Pause/settings/session checks run between candidates. |
| Automatic conclusions forget qualified outcomes after they leave the display's 500-row history. | Conclusions merge the existing bounded Moss archive with current revisions, matching the retained history used by the paper researcher. The archive reader remains bounded to its latest 30 files and 10,000 Moss records. |

`tests/test_automation_debug.py` exercises the actual background loop with a controlled clock, provider failures, configuration changes and fake worker starts. It also exercises Moss collection and automatic evidence review against isolated files. Broker writes and network connections are forbidden by the isolated fixture. Existing session-clock, holiday, early-close, paper/live separation, approval and reconciliation tests remain relevant.

Current operational meaning:

- An enabled paper workday waits outside market hours and resumes during an eligible exchange session while the app and PC are running. These repairs do not create an OS wake/start task.
- Live automatic execution still requires the user's existing verified activation flow. Manual mode remains manual.
- Missing quotes, daily P&L or account/risk evidence remain blockers where required. No synthetic value is substituted to make automation run.
- A request already in progress may finish after Pause; these guards stop further collection or ingestion under obsolete settings. They cannot retract an external model request already sent.
- Tests with fake transport establish behavior for those cases, not live broker acceptance, current feed quality or profitable strategies.
