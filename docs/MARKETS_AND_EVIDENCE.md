# US-first markets and evidence workbench

The primary workflow remains US stocks and ETFs, with the existing separately reviewed US stock/ETF options simulator. The fox, single page, Flask/Jinja and vanilla JavaScript remain. No real-money automation is enabled by these changes.

## Market coverage

- Search defaults to **US markets**. The retained Nasdaq-traded directory is searchable by ticker or company/fund name, including beyond the liquid starter list. Directory membership is discovery, not a permission to trade.
- Moss's broad-US mode examines one focus symbol and up to seven rotating directory symbols per cycle. Default batch: six. One qualified candidate can proceed to the existing model/risk path. AI, loss, exposure and fill limits remain separate. A cycle can take longer than its interval when providers are slow; this is not simultaneous real-time surveillance of the entire market.
- US sector ETFs, indices, rates/fixed-income funds and real-asset funds have research shortcuts. Shortcut symbols are verified against provider metadata when opened, not assumed valid forever.
- Optional global search/history covers provider-supported equities, ETFs, indices, FX, crypto, futures references and mutual funds. Provider-qualified IDs use `YF:`. Foreign ticker suffixes are never stripped; currency, quote units, timezone, source, market timestamp and retrieval time stay attached. London GBp is pence. Indices use provider points/scales; futures multipliers and delivery contracts are unverified and not orderable.
- The new provider history path has no US-only fallback. A mismatched symbol, unknown identity/unit/timezone, future timestamp, invalid OHLC or invalid volume is rejected. Current local date is conservatively excluded from completed-bar analysis. No full holiday/session calendar is claimed for optional global research.
- The global research watchlist is separate from trading configuration. Up to four saved instruments rotate into the daily notebook. Opening or following one cannot change an approval, live mode, or the US automated paper universe.
- History is potentially delayed and raw OHLC. Splits/dividends/futures rolls can affect comparability. No synthetic substitute, price guarantee, total-return claim, complete historical universe, or whole-market real-time feed is supplied. Individual bonds, OTC derivatives, private assets and unsupported options still need explicit adapters and licensed data.

Primary provider references: [Yahoo exchange suffixes and delays](https://help.yahoo.com/kb/SLN2310.html), [IBKR contract identity](https://interactivebrokers.github.io/tws-api/contracts.html). Yahoo's public research endpoint is not an exchange-grade feed or an SLA-backed trading service.

## Palantir-inspired recommendations implemented in this local version

1. **Decision dossier.** `evidence.sqlite3` indexes immutable observed revisions of retained signals, decisions, executions, fills, notebooks and research observations. IDs include kind, workspace/account scope, logical ID and content hash. An explicit order/signal reference is required to link a fill; ticker equality is insufficient. Unverified account scopes do not join. Options/futures need a matching contract ID. Historical revisions cannot be reconstructed if they were never retained; collection starts with evidence actually available.
2. **Attention inbox.** Stable incident IDs retain first/last observation, resolution and review time. A review mark never dismisses a risk gate. Reopening an incident resets its review flag. Connected broker data and valid daily P&L are separate states.
3. **Evaluation suites.** A capture freezes qualified local-paper outcomes and evaluates four prespecified confidence filters on chronological train/validation/test splits. Cross-boundary overlapping horizons are purged. Failed, insufficient and reused-test attempts remain recorded. Results are descriptive re-filtering of recorded decisions, not a new portfolio simulation. A fresh prospective paper sample is still required to assess a proposed change.
4. **Observable runs.** Daily notebooks and paper cycles persist input evidence IDs, data/model results, actual model/prompt metadata when available, duration, result and error. They do not record private chain-of-thought. Notebook observations and fitted research weights are not LLM weight training.
5. **Automation view.** The existing single worker's trigger, conditions, permitted paper effect, estimated next eligibility, last result, retry/cycle reservation and budget are visible. There is no second scheduler and no broker-write action in this module.
6. **Dependency health.** Broker account → daily P&L → risk gate, directory → discovery, and worker/trace errors explain which workflow is affected. A missing value stays missing.
7. **Scenario comparison.** Existing hash-verified workbench captures can be replayed beside altered capital, spread, fees and delay. Original captures and runtime settings remain untouched. Sum of independent scenarios is labeled separately from portfolio returns.
8. **Bounded proposals.** Each suite retains its hypothesis and all four variants, with zero model-call cost for the deterministic comparison. Stages progress from proposal/offline evaluation through paper observation to reviewed; review notes and transitions are stored. Stages change research records only, never live permissions or risk policy.
9. **Relationship inspection.** The dossier shows verified recorded-input and explicit order/signal links. Issuer, supplier, correlation and ETF membership edges are not invented in the absence of sourced, dated relationship data.
10. **Release/recovery.** Source manifests, checksummed archives, validation and dry-run restoration wrap the existing launcher. They exclude `.env`, `data`, databases, journals, credentials and installed environments. Syntax checks are not live readiness; the evidence/UI/test results remain separate.

## Source recovery commands

Run from the application directory with the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe release_tools.py manifest
.\.venv\Scripts\python.exe release_tools.py pack --archive releases\known-good.zip
.\.venv\Scripts\python.exe release_tools.py verify --archive releases\known-good.zip
.\.venv\Scripts\python.exe release_tools.py restore --archive releases\known-good.zip
```

Restore defaults to a dry run. After the desk is stopped, add `--apply` to replace archived source files. The stopped-instance check reads `TOMAHAWK_HOST`, `TOMAHAWK_PORT`, `TOMAHAWK_DATA_DIR`, and `TOMAHAWK_INSTANCE_LOCK` from the target checkout's `.env`; process environment values take precedence. Relative paths resolve against that checkout. `--host` and `--port` explicitly override the endpoint check. Recovery refuses a running or unverifiable instance PID, a reachable port, or an inconclusive endpoint check. It saves the current source first and never stops Gateway or a process. Additional unarchived source files remain. No database schema rollback is attempted. Restart with `Start-Tomahawk.ps1`; verify health and reconcile actual broker state before further trading. Keep archives on trusted local storage: hashes detect changes, not the identity of an archive's author.

## Files and boundaries

- `market_catalog.py`: discovery, metadata, history and separate research watchlist.
- `market_universe.py` / `moss_policy.py` / `moss_paper.py`: bounded US batch rotation and existing risk-filtered paper worker.
- `desk_operations.py`: SQLite evidence, incidents, trace records, evaluation attempts, scenario comparison.
- `static/market_operations.js` and the matching template/CSS: replaceable presentation, collapsed detail sections.
- `release_tools.py`: source-only recovery CLI; contains no broker execution command.

Validation includes isolated tests with network/broker writes denied, external read-only data probes, browser interactions, and an independently checked production configuration. A passing test suite does not establish a profitable strategy or live execution readiness.
