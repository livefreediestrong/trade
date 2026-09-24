# Repairs from the 20-agent review

Date: 2026-09-24. Reviewed baseline: `d67e81c9bdd4e61f8be7df6811cdc473fa5d97f6`.

All 38 consolidated findings (39 original variants) have implementation fixes and targeted regression coverage. The changes preserve the existing Flask/JavaScript design. They do not enable live automation, change trading settings, or place/cancel real orders.

## Evidence and limits

- Final full Python suite: **809 passed**, 10 existing `datetime.utcnow()` deprecation warnings, 65.33 seconds.
- **15 Node regression suites** passed; all **20 production JavaScript files** passed syntax checks.
- The regression suite exercises fake broker callbacks, installed IBKR library objects, temporary databases/state, intercepted launcher/process checks, and controlled asynchronous UI transports. Tests cover adverse interleavings, not just successful builds.
- These tests do not establish profitability, live execution readiness, or exchange/broker behavior during a real trade. No real-money order or cancellation was used for verification.
- Terminal IBKR correction refresh is bounded to the 50 most recent retained matching orders per pass. It cannot recover execution history that the broker API no longer supplies. Missing or incomplete correction evidence remains pending and unverified; an older report cannot clear that condition.
- A full-state browser response overlapping a newer stream update is discarded. Full-only secondary data may wait until the next uncontested poll.
- Interrupted paid notebook attempts are retained to avoid duplicate charges. An attempt with no saved response stays incomplete rather than silently retrying a paid request.

## Finding closure

| Finding | Corrected behavior | Main regression coverage |
|---|---|---|
| R01 | Manual-mode saves apply the explicit custom-limit switch; booleans are strictly validated; saved limits reach review and approval. | `test_review20_app.py` |
| R02 | Approval returns and preserves the latest reconciled signal/fill instead of replacing it with an older partial fill. | `test_review20_app.py` |
| R03 | After contract qualification, entry submission rechecks cached connection, account identity, equity, P&L and authorization without another yielding broker call. Both account-value and account-summary updates invalidate stale equity. | `test_review20_ibkr_boundaries.py`, lifecycle/order-limit tests |
| R04a | Simulated fills require a market tick strictly after order creation and before expiration. | `test_review20_quant.py` |
| R04b | Replay checks a feed gap before a new tick refreshes the watchdog heartbeat. | `test_review20_quant.py` |
| R05a | Primary automatic paper fills recheck current mode, session and risk configuration after slow quote collection. | `test_review20_app.py` |
| R05b | Macro ask-first paper candidates retain their paper workspace. | `test_review20_app.py` |
| R06a | A late paper-options completion/error cannot clear a newer open or close review. | `test_options_ui.cjs` |
| R06b / R07a | Fee-adjusted options and short-stock break-even values that cannot be reached are explicitly marked unavailable, with explanations. | `test_review20_misc.py`, options/cost UI tests |
| R07b | Missing/invalid prices label retained assumptions instead of announcing a successful refresh. | `test_cost_quote_ui.cjs` |
| R08a | Daily and sector indicator histories independently require valid completed-session dates, even with a fresh quote. | `test_indicator_freshness.py` |
| R08b | Previous close follows the quote's trading date rather than assuming the last daily bar is forming. | `test_indicator_freshness.py` |
| R08c | Radar preserves and validates provider market timestamps; acceptable previous-session prices show their date directly in the chip. | `test_radar_observations.py`, `test_radar_ui.cjs` |
| R09a | Shared discovery reserves capacity before directory advancement, so displaced symbols are not skipped. | `test_market_universe.py` |
| R09b | Liquidity uses the mean of matched daily price-times-volume observations. | `test_research_studio.py` |
| R09c | Temporarily stale history can be retried on Resume and is not retained in the history cache. | `test_sale_scanner.py` |
| R10a | Nonfinite, boolean, string or out-of-range model confidence produces abstention, not maximum confidence. | `test_review20_jev.py` |
| R10b | Shared persistent Jev request budgets reserve each attempt before network work, including failed requests. | `test_review20_jev.py` |
| R11 | Replay supports current `intended_side` records and counts unsupported directions as explicit exclusions. | `test_workbench.py` |
| R12a | Notebook history is validated before paid work; persistent session attempts/results prevent duplicate calls after save failure/restart. | `test_companion.py` |
| R12b | Notebook rechecks current model settings/configuration after slow analysis and before reservation. | `test_companion.py` |
| R13a | Execution revisions supersede prior quantity/price, including zero busts. Permanent-ID refresh, incomplete corrections and stale follow-up reports preserve durable reconciliation state and order counts. | `test_review20_ibkr_boundaries.py`, `test_review20_app.py` |
| R13b | Real-trade totals are scoped to the selected synchronized account/mode, with other account history separated. | `test_review20_trade_journal.py` |
| R13c | Raw commission-report provenance distinguishes unavailable realized P&L from a genuine zero. | `test_review20_ibkr_boundaries.py`, journal tests |
| R14a | Restart recovery recognizes a committed paper fill in its own ledger. | `test_review20_app.py` |
| R14b | Research-stage writes compare the expected prior stage atomically and reject stale concurrent transitions. | `test_review20_misc.py` |
| R14c | Invalid UTF-8 receives the same state-corruption quarantine and write block as invalid JSON. | `test_review20_app.py` |
| R16a | Cancellation review generations bind acknowledgement to the actual current ticket; older responses/errors are ignored. | `test_frontend_state.cjs` |
| R16b | Streaming renders and successful saves preserve newer unsaved watchlist/risk drafts, including after blur. | `test_frontend_state.cjs` |
| R16c | Newer stream state wins over a delayed full poll; coalescing, retries and cleanup remain functional. | `test_frontend_state.cjs` |
| R17a | Long help remains open during internal scrolling and supports focus/Escape navigation. | `test_context_help.cjs` |
| R17b | Closing order review restores its opener, a replacement button for the same signal, or the signal queue. | `test_frontend_state.cjs` |
| R18a | A fresh duplicate from a healthy news source takes priority over a stale cached headline. | `test_companion_news.py` |
| R18b | Short sidebars apply speech visibility and do not consume news/buzz they cannot display. | `test_moss_avatar.cjs` |
| R19a | Launcher reuse verifies both checkout and data-root identity. | `test_launcher.py` |
| R19b | Launch/app settings handle inline comments, quoted hashes and process-environment precedence consistently; example comments are separate lines. | `test_launcher.py`, `test_recovery_settings.py` |
| R19c | Source recovery checks the configured endpoint and instance PID, rejecting running or unverifiable targets before changes. | `test_recovery_settings.py`, market operations tests |
| R20 | Actual evaluator workers hold bounded slots until completion. Expired results cannot start thesis work; duplicate UI requests are coalesced. | `test_review20_app.py`, `test_frontend_state.cjs` |

The original review's R15 security checks produced no confirmed defect; they remain part of the existing test suite rather than a fabricated repair item.

## Operational handoff

Keep existing credentials, configuration and trading records in their current locations. Source recovery archives exclude them and never rewind trades. The launcher is still `Start-Tomahawk.ps1` / the existing desktop shortcut. Jev request limits are documented in `.env.example` (`JEV_RPM`, `JEV_DAILY`) and use defaults unless manually overridden.

Use source-only backups before deployment and verify `/api/health` reports the intended source/data roots after restart. App restarts may require IBKR to provide a new genuine P&L baseline; unavailable data must remain unavailable. Do not substitute zero or disable risk checks to obtain readiness.
