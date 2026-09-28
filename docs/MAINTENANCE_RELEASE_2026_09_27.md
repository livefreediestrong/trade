# September 27 maintenance — installed and verified

Installed at 02:38 ET after the owner instructed **“always implement maintenance packages.”** This release supersedes the preparation-only status of the overnight research and broker-read load candidates. The running source is `ad19c6f8e282c67d`, PID 21100; startup, serving and on-disk versions agree. All 17 packaged files match their recorded hashes.

## What changed

- [Overnight/daytime research comparison](OVERNIGHT_STUDY_2026_09_27.md): compare up to three exact US stock/fund symbols, explicit historical price adjustments, missing-session checks, costs, fixed versus reinvested amounts, a buy-and-hold baseline and frozen export/replay. Opening the page does not run a study or activate trading.
- [Broker-read load repair](BROKER_READ_LOAD_2026_09_27.md): one account refresh at a time, prompt unavailable-data responses for other callers, copies of cached data, late-result rejection after account changes or explicit refreshes, and fewer redundant reads on failure.
- [Maintenance policy](MAINTENANCE_PLAN_2026_09_26.md): install tested packages during ongoing work without another routine approval request, preserving controls and the normal restart protections.

## Installation and recovery evidence

The owner had intentionally closed Gateway. Before installation the desk was manual, session inactive and Fox disabled, with no decision in progress or unresolved app submissions. Those saved settings remain byte-for-byte unchanged. This release did not turn real-money trading off or on, change risk limits, or place/cancel an order. Broker exposure is unknown while Gateway is closed; no flat-account claim was used.

The owner-authorized explicit shutdown endpoint stopped PID 14536 and completed broker-owner cleanup. The normal launcher used `-NoBrowser -NoDialogs -NoBroker` to start the updated desk. The existing unattended-restart checks were not changed. Watchdog and upkeep were briefly held and restored to their original enabled state; both are Ready. Gateway remains closed. No missing workers or corrupt stores were reported afterward.

Pre-update verified backup: `data/backups/state-20260927T063708-e70ab3e7.zip`; 30 stores; SHA-256 `317f4d8888f244c9effb78dfd55607c118e18357a0756478f1ce249ae65c6cfd`. Original source recovery copy: task `maintenance-source-rollback-20260927T063835Z.zip`; SHA-256 `3e9495a8bee2aba8532738987b88cac6141054c91eeef29a670d89317646abfc`. Credentials and saved controls retain their checksums. No dependency versions changed.

Rollback, if needed, uses another orderly stop and only the source files in that archive. Files marked new in its manifest must be handled individually after verifying their paths and current contents. Preserve later user edits and all current trading state; do not restore an old account ledger or replay orders. No rollback occurred.

## Validation and limits

The candidate passed 174 focused Python checks plus a separate 30-check restart/launcher run. Three controller suites passed. The first launcher check exposed an omitted launcher file in the isolated test copy; adding the unchanged, checksum-matched launcher resolved that harness error.

The real local HTTP experiment held a simulated broker provider while eight account requests arrived. Account reads fell from eight to one per wave, and health responses improved from 613–623 ms to 2.1–2.74 ms. Other callers correctly received unavailable account values until refresh completion. Ordinary mixed-request speed did not improve; the measured repair concerns slow-provider contention. The 360-request run is not market-open endurance or memory-leak proof.

The installed page actually fetched public SPY history for January 10–April 30, 2025, calculated 76 completed intervals, saved the comparison, retained it after reload and downloaded an identical frozen replay. Browser checks at requested desktop width 1341 (reported 1342) and phone width 390 found no page overflow or captured console warnings/errors. The result table scrolls within its container on a phone. Method disclosure opened correctly. Screenshots are task `outputs/maintenance-installed-1341.png` and `outputs/maintenance-installed-390.png`; export is `overnight-03b3aad2d55d.json` in Downloads. This research retained negative results; it does not establish strategy profitability.

The installed app reports the broker disconnected. The earlier missing P&L callback problem remains G01; actual paper protective stops/options and settlement remain G02/G03; practical risk values and prospective strategy evidence remain open. The owner has not confirmed a separate IBKR paper account. These limitations were not converted into successful qualification by the maintenance update.

Exact receipts: task `outputs/pending-maintenance-20260927.json`, `outputs/pending-maintenance-install-receipt.json`, `outputs/pending-maintenance-startup.json` and `outputs/pending-maintenance-final-runtime-20260927.json`.
