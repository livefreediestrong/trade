# Broker account refresh under load — September 27, 2026

Status: **tested maintenance candidate, not installed**. This correction is combined with the [overnight research candidate](OVERNIGHT_STUDY_2026_09_27.md). It does not change trading controls, limits, broker connection ownership or order submission.

## Problem and correction

When several browser requests arrived after the account cache expired, every request could start the same slow account/holdings read. Eight requests could occupy all eight web-server workers, leaving even the health endpoint waiting. The cache also returned a shared mutable object, and a completed old read could repopulate the cache after an account change or explicit P&L refresh.

Only one request now refreshes the account. Other requests return promptly with account values explicitly unavailable while it finishes. An expired successful result is not presented as current evidence. Fresh cache results are copied before being returned, so one caller cannot alter another caller's holdings or readiness result. The cache lifetime starts before the provider read, preventing a slow read from receiving a new full lifetime when it finishes.

Before publishing a result, the desk checks the selected broker/account identity and an explicit-refresh generation. Superseded results are discarded and do not update the daily P&L peak. A failed account response skips the unnecessary holdings request. The refresh lock is released after failures so subsequent reads can recover. This is a UI-read correction; order execution retains its separate fresh checks.

## Behavior actually exercised

Seven focused cache tests exercise concurrent requests, mutation isolation, expired data, an account change during a read, explicit invalidation during a read, recovery after failure, and avoiding a holdings read after an account failure. The expanded API-worker test fills all 32 ordinary read slots and verifies that cancellation and reconciliation operations are still admitted and run first after the active operation completes, using the same owner thread. These are simulated operations in the real dispatcher, not broker-accepted orders or cancellations.

The combined run passed **174 Python checks** across broker-book load, broker queue load, broker restart, self recovery, account funds, live audit, IBKR connection, Fox recovery, overnight research, workspace UI and plain-language suites. The overnight controller's two checks, account-funds controller suite and live-agent controller suite also passed. Test receipt: task `outputs/pending-maintenance-tests-20260927.txt`.

Before installation, a separate **30-check orderly-restart and launcher run passed**. The first launcher run failed because the isolated source copy omitted the unchanged root PowerShell launcher; after copying and checksum-checking that file, the complete run passed. Production launcher code was not changed. Receipt: `outputs/pending-maintenance-restart-tests-20260927.txt`.

The HTTP experiment used real Waitress with eight workers and the real `/api/state`, `/api/health` and `/api/loop/feed` handlers in an isolated source copy. Its account provider was deliberately held, then released or failed. Broker transports and all non-test network connections were denied. Three waves of eight account-reading requests were followed by 360 mixed requests.

| Measurement during a stalled account provider | Before | Candidate |
|---|---:|---:|
| Account reads per eight-request wave | 8 | 1 |
| Health response, three waves | 623.0 / 614.8 / 613.2 ms | 2.74 / 2.10 / 2.19 ms |
| Median state response, three waves | 783.28 / 775.77 / 774.23 ms | 16.24 / 15.56 / 24.65 ms |
| Account-reading requests still waiting on the provider | 8 | 1 |

The seven other requests report unavailable account data; the owner request still takes roughly 766–777 ms in this experiment. All tested HTTP responses succeeded, including the explicit unavailable-account responses. Ordinary mixed-request timings were not faster: state p95 was 99.33 ms before and 119.78 ms afterward. The demonstrated benefit is avoiding web-worker starvation during a slow broker read, not a general speed claim.

Threads stayed at 18 during each bounded mixed run. Candidate resident memory rose about 0.71 MiB over its six samples; the measurement includes the harness's retained responses and instrumentation. This is not a sustained memory-leak qualification. Receipts: task `outputs/overnight-study-stage-http-load-20260927.json` and `outputs/pending-maintenance-stage-http-load-20260927.json`; reproduction harness: `outputs/benchmark-read-load.py`.

## Release and remaining evidence

The owner confirmed that the roughly 02:12 ET Gateway exit was intentional. Leave Gateway closed. Missing Daily P&L before that shutdown remains separate from this repair; this test neither diagnoses IBKR's missing callbacks nor proves recovery under a real outage.

The owner subsequently instructed: **“always implement maintenance packages.”** Installation is authorized through the existing explicit-restart flow, with a verified backup, unchanged controls, no active session/decision and no unresolved app submissions. Gateway is already closed; restart the desk with the launcher's `-NoBroker` option. This does not establish that the broker account is flat, and does not change the stricter unattended-restart checks. Record installed source and actual browser evidence separately after the reload. Real broker-paper protective-stop/options tests, a real market-session endurance run and practical account-specific risk limits remain open. An IBKR paper account is not yet confirmed available.
