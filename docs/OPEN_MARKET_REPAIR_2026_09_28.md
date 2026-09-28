# Open-market repair package — September 28, 2026

Candidate source: `7d1aac87710c18d9`. Installation and running-state verification are recorded separately; this document alone does not establish deployment or broker qualification.

## Repairs

- Effective limits use the tightest existing preset, agent and armed kill-switch limits. At $110 equity with the existing high preset, that means $4.40 per stock position or option order maximum loss, $4.40 daily-loss entry stop and 12 agent attempts per day. Unknown equity blocks dollar sizing. These are entry controls, not guaranteed exit prices.
- Option opening orders now pass the same account-based risk budget, including a fresh check under the dispatch lock. Buy-limit offsets are included when sizing stocks. Reducing exits retain their existing separate checks.
- Unqualified automatic real-money option entries are held in conversion and final dispatch. A saved options checkbox no longer means armed. Existing IBKR paper lifecycle testing requires automatic exits; real assignment/exercise recovery and broker stop acceptance still need controlled qualification. Stock automation remains available under its own existing checks.
- Delayed or frozen stock quotes cannot authorize orders. The final broker adapter independently requires live type-1 bid/ask data. Display-only delayed fallback remains available. No data subscription is purchased by this update.
- Definite missing-history symbols receive a persistent one-hour research cooldown. Rotation advances to another eligible symbol; broad provider errors retain normal backoff. Exits/reconciliation run before entry cooldown and attempt limits.
- X billing failures show unavailable. Direct Reddit access is distinct from independent WSB summaries and still requires approved access. Massive checks historical bars and snapshot entitlement separately in bounded background reads, without buying access or fetching paid regulatory snapshots.

## Evidence and limits

572 focused Python checks passed across 18 suites. Six JavaScript controller suites passed. The actual revised templates/controllers were reviewed in a read-only sample-data browser at requested 1341px desktop and 390px mobile widths, without horizontal overflow or captured JavaScript errors. Samples are explicitly labeled and cannot contact the broker. The Python order-path checks use fakes; no real order, fill, stop or assignment is qualified by them.

Source rollback archive, candidate archive, baseline/file hashes, state backup, exact-byte installation receipt and post-install API/browser checks accompany the package. Saved account configuration and credentials must remain unchanged. No real order or cancellation is part of validation.

## Still external or unqualified

IBKR paper-account protective stops/options exit and assignment recovery qualification; full account exposure/margin/settlement integration; long-running recovery evidence; Reddit developer approval; X credits; applicable market-data entitlements. No profitability claim is made. Broader request tracking remains in TASKS.md.

IBKR documents that a delayed-data request returns live data when entitled, so execution checks use the returned market-data type: https://www.interactivebrokers.com/docs/tws-api/doc/market-data-delayed/introduction . Paper and live execution differ: https://ibkrcampus.com/campus/trading-lessons/paper-trading-vs-live-trading-whats-the-difference/ .
