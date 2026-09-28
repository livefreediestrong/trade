# Community research release - September 28, 2026

Installed and verified source `44cf0c4ffc88eec1`, PID 32720. All 15
package files match their tested hashes; startup, serving and disk agree.
[Feature and access details](COMMUNITY_RESEARCH_2026_09_28.md).

61 focused Python checks and the actual browser controller suite passed. Real
desktop (1341 requested, 1342 reported) and 390px mobile checks covered source
evidence, stale-price presentation, explicit quote requests and unavailable data.
Installed browser had no captured console errors/warnings or page overflow.
The live-source check returned Finnhub AAPL 341.07 dated September 25 at 16:00 ET;
the page correctly called it old. It is a provider observation, not a trade.
Stocktwits/RSS supplied records; Reddit lacks approved credentials and X reports
402 billing access. Stocktwits had partial per-symbol coverage. The sampled pulse
excludes 155 old, undated, future or duplicate records in this read.

Saved config and environment hashes are unchanged. The normal restart used
`-NoBroker`, preserving the owner's closed Gateway. No order, cancellation,
activation, limit change, subscription, Discord join or message was performed.
Both maintenance tasks are Ready/enabled. No missing workers or corrupt stores.

Verified state backup: `state-20260928T044041-fa7bb432.zip`, 31 stores,
SHA-256 `b2644086a9a962e6b4bbc4540e55a6b38ab5a080c5f9831cbc356c0f82eb5ac4`. Verified source recovery:
`maintenance-source-rollback-20260928T044127Z.zip`, SHA-256 `82bd7fb76cc34bb817903c9f49df6539fe7d70c44b3d54778983b283c16748da`.
Candidate ZIP SHA-256 `4aa81ac6dd9e17e33bdf7a5ed092990123be043c4d03afe831f60c22e9489acf`.
Neither recovery archive was restored over the running desk.

Evidence lives in task outputs: `social-research-20260928.json`, focused test
receipts, `social-research-browser-20260928.json`, installed browser screenshots,
and `social-research-final-runtime-20260928.json`. G06 remains partial: no WSB
portfolio export/API or Discord channel access was obtained. This package does
not close P&L recovery, broker-paper lifecycle/settlement tests, risk-policy
decisions, prospective strategy qualification or market-session endurance.
