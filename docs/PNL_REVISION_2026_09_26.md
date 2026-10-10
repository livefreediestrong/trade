# P&L revision: maintenance candidate, September 26, 2026

**Implemented in an isolated source copy; not installed in the running desk.** The owner previously deferred installation to the next maintenance window. This revision preserves that choice and the current Gateway connection. It improves maintainability and provides a controlled way to investigate reconnect failure; it is not proof that Gateway's missing first P&L response is fixed.

## What changed

- The broker adapter and its library-specific tests use `ib_async==2.1.0` instead of archived `ib_insync`. All stock, option, combination, order and execution-filter imports use the same replacement library. There is no silent fallback to a different library.
- Existing account validation, loss limits, missing-P&L blocking, commission-value protection, callback invalidation, single API owner and retry cooldowns remain in place.
- The Windows launcher verifies the tested adapter version and installs declared requirements when dependencies are missing or incompatible. It still uses the normal update/restart controls.
- The previously deferred browser-update classification repair is included: browser/template/style changes can be served without reconnecting the broker after this Python update is loaded. Python changes still require a guarded restart. Test receipts still cover changed browser files.
- `tools/pnl_probe.py` provides a P&L-only diagnostic with the official API, new wrapper or legacy wrapper. It defaults to printing a plan, makes no connection on import, and executes only with `--run-paper`.
- Changes to the probe invalidate the test fingerprint. No test may authorize trading based on a prior version of that tool's checks.

The diagnostic uses a nonzero reserved client ID, loopback and paper ports. It refuses a responding/uncertain desk endpoint, verifies an advertised paper account before requesting P&L, uses bounded observation cycles, and stops if cleanup fails. Each cycle gets a separate receipt. It cancels only its own P&L subscription; it contains no order-submission/cancellation call. Reports retain timestamps, callback counts, validity, numeric error codes, library/server versions and independent time-response counts. Account identifiers, amounts and raw error messages are excluded. A valid receipt is explicitly not a trading-readiness decision.

Exit codes: `0` means every completed cycle received a usable callback and finished connected without an upstream-loss indication; `3` means at least one observed cycle lacked that evidence; `2` means the diagnostic stopped/refused or failed. Plan mode also returns `0` with `connected: false`. Even a successful three-cycle result is bounded evidence, not market-open, rollover or overnight qualification.

## Dependencies and differences that matter

The local adapter test environment pins `ib_async 2.1.0`, `aeventkit 2.1.0` and `tzdata 2025.3`. This library release requires `tzdata>=2025.2,<2026`; production currently has `2026.4`. Installing this candidate would therefore change the timezone-data package. Calendar and weekly-review tests are included in qualification, but they do not cover every jurisdiction's future timezone changes. Do not remove that constraint or claim the newer version was tested with this candidate.

The new wrapper negotiates a maximum client protocol of 178, versus 176 in the old wrapper. It still implements its own protocol. The official comparison uses **IBKR API 10.50.2** and its pinned **protobuf 5.29.5**, installed separately from the desk dependencies. The probe checks that official SDK version because its error callback signature differs from older releases. The official client is not added to the desk's normal requirements.

The current official SDK archive was downloaded from [IBKR's published archive](https://interactivebrokers.github.io/downloads/twsapi_macunix.1050.02.zip). SHA-256: `673129e5cba58c4d77bc40647265f84ea42f605eccf88fa4c1221d62d12454f3`. SDK files and downloaded dependencies are local test artifacts and are not included in the application patch. [IBKR library guidance](https://www.interactivebrokers.com/docs/tws-api/doc/third-party-api-platforms/non-standard-tws-api-languages-and-packages/ib-insync-and-ib-async) supports moving away from the legacy library; it does not promise a P&L fix.

The official diagnostic uses a minimal account handshake. The wrappers perform their own initial account/position downloads. The report labels this difference; a difference in results is a lead for a matched-startup experiment, not proof that only protocol version caused it.

## How to inspect and run the diagnostic

The prepared source is `C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs\pnl-revision-stage`. It contains no production `.env` or trading data. Do not run that copied desk alongside production.

This command only shows the plan and does not require an SDK or Gateway:

```powershell
python tools/pnl_probe.py
```

The locally prepared dependency folders are siblings of the staged source. In a terminal opened in that source folder, the maintenance-only official-client command is:

```powershell
$env:PYTHONPATH = '..\pnl-official-deps'
& 'C:\Users\maher\Projects\daytrade-signal-desk\.venv\Scripts\python.exe' tools/pnl_probe.py --run-paper --library official --port 4002 --client-id 937 --seconds 180 --cycles 3 --output ..\pnl-official-paper-result.json
```

Run only after the normal production desk has been stopped for maintenance and a paper Gateway session is signed in. The `DU` account check happens after the handshake: a misconfigured paper port that advertises a live account is rejected before the P&L subscription. Multiple advertised accounts require `PNL_PROBE_ACCOUNT` to identify the intended paper account locally. The tool never prints that value. Use a new output name for every run; it will not overwrite an existing receipt.

For the new wrapper comparison, use `PYTHONPATH=..\pnl-revision-deps`, `--library ib_async` and a different output filename. For the old wrapper, use an isolated environment containing `ib_insync 0.9.86` and `--library ib_insync`; this PC's unchanged production virtual environment still contains it. Keep client ID, account, Gateway build, duration and session conditions consistent, and change one variable at a time. Do not change the production client's ID or use client ID zero. No broker diagnostic was executed as part of preparing this candidate.

## Validation and installation handoff

The validation receipt is recorded in the task's `outputs/pnl-revision-20260926.json`. The selected checks exercise real library objects and message decoders, fragmented P&L frames, cancellation/replacement, old-callback rejection, unusable values, disconnect invalidation, account-download completion, commission handling, stock/options/combination risk paths, protective exits, normal launcher branches, dependency-version refusal, calendar/weekly boundaries, and deferred-update behavior. Broker I/O and launcher process effects are intercepted. The official decoder test exercises both legacy and protobuf P&L payloads.

A first launcher-probe test accidentally selected an unrelated PowerShell variable; the test was corrected to execute the actual Python dependency check. The production launcher was not executed. Final counts and fingerprints belong to the final receipt, not the earlier intermediate runs.

At maintenance, verify every production file against the patch manifest before applying. Resolve any drift instead of overwriting newer work. Apply the candidate patch, let the normal launcher install its pinned dependencies and perform the permitted update flow, then verify actual running version and P&L receipt. Earlier automatic approval review rejected a direct stop/relaunch command; this work does not retry it or trigger the watchdog as a workaround. The earlier eight-file patch remains an untouched historical artifact; this combined candidate supersedes it for installation, so do not apply both.

If a real paper comparison fails, retain the receipt and the prepared support draft; keep new live risk blocked when P&L is unavailable. Gateway build changes, official-client broker results, market-open changing P&L and durable recovery remain unqualified. G01 stays open.
