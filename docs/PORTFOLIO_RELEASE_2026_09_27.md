# Portfolio coverage maintenance release — September 27, 2026

Installed and verified source `557445386517a202`, PID 35352. The [account coverage repair](PORTFOLIO_COVERAGE_2026_09_27.md) is running. All 17 installed files match the tested package; startup, serving and disk fingerprints agree. Saved config and environment files retain their exact hashes. The owner instruction to always implement tested maintenance packages is recorded in the maintenance plan.

Validation: 211 focused Python checks, the actual JavaScript controller suite, syntax check, sample desktop/mobile browser behavior and the installed disconnected-account workflow. The production browser loaded the new asset version, showed unknown holdings without dollar totals, had no page overflow at desktop/mobile sizes and captured no console warnings/errors. This is not connected-broker or order-execution qualification.

Fresh state backup: `state-20260927T070443-4a394461.zip` (31 stores), SHA-256 `cd7401b3b3bfdc9ec750889d7644f5161bcf7cdc095ea290b6232f1783dae4c0`. Source recovery archive: `maintenance-source-rollback-20260927T070520Z.zip`, SHA-256 `5c2bfcfc13a079e798de775d81fa4fa1b6f9df9994be4d316b1317809a32f626`. Candidate archive SHA-256: `8272cd501df4f88bedfd83e1c38684399dce67a430a02a87a93abea9f512ae74`. These files were verified, not restored over production.

The normal explicit restart was used with `-NoBroker`. Gateway remains intentionally closed; no account exposure is inferred. The app reports no missing workers or corrupt stores. Both existing maintenance tasks are Ready and enabled. No trade, cancellation, activation, pause or limit change was made.

G01 P&L recovery, G02/G03 actual broker-paper stops/options and settlement, G04 practical risk choices, prospective strategy qualification and sustained market-session testing remain open. The new account view is descriptive and local; full settlement/margin/correlated-risk context and integration into model decisions remain pending.

Evidence is in the task workspace `outputs/portfolio-review-20260927.json`, `portfolio-review-final-runtime-20260927.json`, the test receipts and `portfolio-installed-1341.png` / `portfolio-installed-390.png`.
