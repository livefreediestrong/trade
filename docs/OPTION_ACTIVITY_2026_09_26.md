# Local option activity review

**Current status, September 27:** installed in the September 26 combined maintenance
release. A genuine owner-provided activity export has not been accepted yet; fixture
tests do not establish settlement correctness. The [qualification runbook](BROKER_PAPER_RUNBOOK_2026_09_27.md)
records the remaining evidence. The earlier not-installed statement below is a
dated preparation note and is superseded by this status.

September 26, 2026. This addition is implemented in the separate maintenance candidate and **not installed**. It extends [the options lifecycle work](OPTIONS_LIFECYCLE_2026_09_26.md). The owner's instruction remains **do not turn live trading off**. No running setting, risk limit, order, cancellation, process or dependency was changed.

## What it adds

Fox's page now has a collapsed **Review an option activity statement** section. Choose a local IBKR CSV, click **Read file for review**, check the account and rows, acknowledge the limitations, then **Save evidence locally**. File selection and preview do not save. The saved evidence survives reload and is kept separately from the order ledger and live P&L.

IBKR's [Options, Exercises, Assignments and Expirations report](https://www.ibkrguides.com/reportingreference/reportguide/options_exercises_expirations_fq.htm) includes event types, quantities, contract identifiers and reported financial fields. IBKR's [Activity Flex Query setup](https://www.ibkrguides.com/orgportal/performanceandstatements/activityflex.htm) allows selecting sections, fields, accounts and CSV output. Use that specific section: a generic trades export can contain unrelated buys and sells that do not establish delivery from an option event.

Supported input is a single-section detailed CSV with one header row. Required fields are Account ID, Asset Class, Currency, Conid, Symbol, Date, Transaction Type, Quantity and Trade ID. Dates must be YYYY-MM-DD or YYYYMMDD. Supported instruments are OPT and STK; option events are Assignment, Exercise and Expiration; related stock rows are Buy or Sell. Common compact header spellings such as ClientAccountID are recognized. Include the optional financial fields listed in the in-app export help when available. Limits are 1 MB and 3,000 rows per file.

This is a deliberately limited parser, tested with generated examples. A real owner-exported Flex CSV has not yet been supplied for acceptance testing. Multi-section statements, XML/PDF, model allocations, summaries, lot rows, pending exercise instructions, zero-quantity/cancelled rows, unknown labels and other instruments are rejected. The importer does not guess missing identifiers or silently drop malformed rows. One wrong-account row rejects the whole file. It requires the actual account ID rather than an account alias.

## How to read the results

- **Already saved:** the same normalized event is not counted twice, even across overlapping uploads.
- **Conflict:** the same account, Trade ID, instrument and contract has different values across imports. Both versions remain visible; both are excluded from the reported-P&L summary. There is no automatic choice of a winning version. Corrections issued under a different Trade ID are not automatically linked.
- **Contract match:** the contract ID matches a tracked position in the same account, and the event date is not before that position's opening date. Multiple possible position records are marked ambiguous. This is a lookup aid, not proof that the event resolves that position. Stock delivery rows are displayed without inventing a pairing to a specific option event.
- **Not supplied:** the export did not contain an amount. Missing P&L stays unknown, including when every imported row lacks it. Currencies remain separate. Reported P&L is not reduced by fees a second time. These figures cover only unambiguous imported rows and are not a complete account return, tax calculation or settlement ledger.

Imported files are owner-supplied evidence, not independently authenticated broker responses. Saving never creates an execution, changes recorded holdings, changes live P&L/risk inputs, clears an assignment hold or switches Fox/session settings. The existing external-closure review still requires its own fresh broker evidence. The view supports the selected real or broker-paper account without activating or pausing either.

## Storage, privacy and recovery

Only normalized supported fields, report hashes and local import metadata are retained in `data/option_activity_evidence.json`. Other CSV columns and the original file contents are not stored. The data directory is already excluded from source sharing. There is no cloud model request, broker query, Flex token or network fetch in this feature.

Preview tokens expire after two minutes, are single-use, are limited to five, and bind the selected account plus the existing evidence store. They retain only the bounded upload, not copies of the whole store. A changed account, changed store or expired preview requires a new review. A failed or missing save response is not automatically retried. The store stops accepting new data at its stated capacity rather than deleting older evidence.

An unreadable optional evidence file blocks its import/view and remains untouched. It does not add a failure to the separate execution-state corruption gate. Existing atomic JSON writes are used, and success requires rereading the saved contents. Regular backups include the file. The restore-drill reader now checks it; restoring evidence does not replay orders.

## Evidence and remaining limits

**196 Python checks passed in 34.61 seconds**, across the statement, option-lifecycle, live-agent, stop-review and trade-journal suites. They cover duplicate/conflicting reports, wrong accounts, missing amounts, malformed rows, active real-account controls unchanged, expiration/single use, changed stores, unreadable files, failed saves, contract matching, and an actual backup/extract/read round trip in isolated storage. Broker transport was intercepted and no external orders were sent.

Three browser-controller suites passed: option activity, option review and live agent. The new script passed syntax checking and the new Python module passed pyflakes. A real browser selected a generated two-row sample, previewed it, acknowledged/saved it, reloaded and reopened the evidence. The original assignment warning remained. The requested 1341-pixel viewport rendered at 1342 CSS pixels; 390 pixels was also checked. Neither overflowed horizontally; no captured warnings/errors occurred. The unrelated favicon request was deliberately blocked. Screenshots and test receipts are in task outputs. The preview server was stopped and the size override removed.

Still open: acceptance with a genuine export from this account; independent retrieval/verification of broker statements; corrected/cancelled event resolution; linking deliveries to exact option events; final cash/basis/settlement reconciliation; actual broker-paper order/assignment/reconnect qualification; live use of the new option exit engine; approved practical dollar limits. This addition provides useful local evidence without declaring those tasks complete.
