# Insider-disclosure platform comparison and implemented repairs

Reviewed September 26, 2026. Scope: software analyzing public insider disclosures. These are selected established benchmarks, not an independently measured market-share or investment-performance ranking. Comparison uses public product pages, not paid-account acceptance testing or vendor accuracy/latency audits.

## Useful benchmarks

| Platform | Documented features relevant to this app | What to take from it | Current desk position |
| --- | --- | --- | --- |
| [OpenInsider](https://openinsider.com/latest-cluster-buys) | Cluster screens, filing/trade dates, filing delay, officer roles, transaction codes and changes in holdings. | Make the evidence/filtering understandable before interpreting it. | New staged view adds filtering, role context, dates/delay, cautious holding-change estimates and a documented multi-owner purchase flag. Still one company per request. |
| [SECForm4.com](https://www.secform4.com/) | Insider reports, screeners, graph views, buy/sell ratios, institutional holdings and 13D/13G coverage. | Let users move from a company to an ownership timeline and original disclosures. | Original filing links and richer ownership detail added. A linked timeline and the wider ownership datasets are still missing. Vendor real-time claims were not independently tested. |
| [Fintel](https://fintel.io/screenerTerms) | Officer-specific purchase counts/values, weighted purchase prices, float-relative measurements and proprietary insider scores. | Distinguish officers from other reporting owners and normalize historical activity carefully. | Officer filtering added. No equivalent cross-company score, float-normalized history or long-term insider baseline yet. |
| [Quiver Quantitative](https://www.quiverquant.com/insiders/) | Insider dashboard with trade/disclosure dates, executive-buy views, alerts/API access, alongside Congress, contracts and lobbying datasets. | Connect related evidence while keeping dataset dates and coverage explicit. | Existing company check covers several of these categories; insider date/coverage detail improved. No market-wide insider alerts or comparable historical dataset. |
| [VerityData / InsiderScore](https://verityplatform.com/solution/veritydata/insiderscore/) | Cleaned historical data, behavioral flags, watchlists/alerts, analyst research, exports and data feeds. [Its plan views](https://verityplatform.com/resources/veritydata-new-10b5-1-views-2025/) describe plan timing and lifecycle context. | Prioritize transaction interpretation, amendment handling and historical context. | Filing plan flags and explanatory notes retained; ambiguous amendments withhold totals. No analyst-reviewed dataset, complete plan reconstruction or commercial-scale history. |

The strongest design direction for this desk is OpenInsider-style clarity, Quiver-style linked company context and Verity-style care with the underlying filings. This is a design judgment from the comparison, not evidence that copying vendor features produces profitable trades.

## Confirmed issues repaired

The old reader labeled every P/S row as an open-market transaction, described purchases as insiders using their own money, kept only the first reporting owner, omitted disclosure timing, ignored Form 4/A, converted absent plan checkboxes to false, and presented unpriced aggregates as known amounts. The old panel also hid grants/tax/gifts from its transaction rows.

The SEC's [current Form 4 instructions](https://www.sec.gov/files/form4.pdf) explain that P/S codes also cover private transactions, provide a filing-level 10b5-1 indication, and permit amendments that change or add selected rows. Those distinctions drove the repairs; amendments are not treated as complete replacement filings or simply added to originals.

Implemented in the held candidate:

- Parse all reporting owners/IDs and preserve one transaction row for joint ownership. Joint, indirect, unidentified and malformed rows cannot create a multi-owner purchase flag.
- Show transaction date, filing date, the reported SEC acceptance timestamp and calendar-day delay. A delay is not labeled a legal violation. Retain the time the app obtained the sample; any later backtest must use public availability rather than pretending the trade date was the disclosure date.
- Preserve security class, direct/indirect holdings, reported shares after a transaction and a holding-change estimate only where a single direct holding row permits the arithmetic. No estimate for joint or multi-row holdings.
- Keep plan flags as checked, unchecked or unavailable; none establishes an individual row's motive. Preserve row references and filing notes, with explicit shortening/missing-text notices.
- Read Form 4/A, flag amendments, and withhold purchase/sale totals and cluster assessment until originals can be reconciled. Missing prices stay unknown with separately labeled priced subtotals.
- Deduplicate accession requests, bound the display to 100 rows, retain all transaction types, mark incomplete filing coverage, reject unsafe paths/future transaction dates, and recognize unvisited older submission pages. Invalidate the old cache schema.
- Add purchase/sale/other filters, officer-only filtering and newest-filing/value ordering. Original-source links allow only the expected HTTPS SEC/USAspending hosts. Text and footnotes are rendered as text.

The descriptive purchase flag is explicitly defined as at least two distinct SEC owner IDs with direct, single-owner P rows in the latest 30 calendar days. It includes private purchases and plan-related filings. It does not assert coordination, independence of economic interests, an unusual historical pattern, a price forecast or verified investment skill. Coverage problems/amendments suppress the assessment.

## Important remaining gaps and implementation order

1. **Correction-aware filing archive:** store immutable raw documents, acquisition times and versions; reconcile added/corrected rows; retain provenance and explicit coverage gaps. This is needed before reliable history or automatic interpretation of amendments.
2. **Watchlist disclosure feed:** use filing availability time, a shared SEC request governor/backoff, durable duplicate suppression and visible source health. Add in-app alerts after ingestion is dependable. The current reader is on demand, capped at 20 documents/90 days and may use a healthy cached check for six hours.
3. **Insider/company timelines:** separate direct/indirect and derivative positions; show first/repeat purchases and deviations from that same insider's baseline. Do not infer “unusual” from a 20-document sample.
4. **Proper outcome evaluation:** preserve what was knowable at the decision time, corporate actions and delisted names, realistic costs/liquidity and later untouched test data. Do not borrow vendor returns or publish an unvalidated ranking.
5. **Optional licensed breadth:** compare feed contracts, latency, historical corrections and total cost before adding a paid provider. No new subscription, account or API purchase was made.

Company check remains display-only and connected to the existing companion discussion action. This batch does not feed raw insider flags into order scoring, alter the separately staged reviewed activation path, change live controls or raise risk limits.

## Evidence and release status

- **75 Python checks passed in 8.99 seconds**, four related suites: insider review, company check, companion research view and research activation. Actual parsing, metadata acquisition and Flask routes exercised using isolated fixtures; no orders/model calls.
- New insider UI and existing company-linking controller suites passed; JavaScript syntax and `company_check.py` pyflakes passed. Cases cover filtering/sorting, missing totals/amendments, text/link safety, stale responses and read-only requests.
- Bounded live source check with the final reader succeeded: Apple had eight listed filings in the window, two were read, yielding four displayed rows. The result correctly remained partial; cluster assessment required review. This verifies one actual retrieval/parsing path, not full-market coverage. Receipt: task `outputs/insider-live-source-20260926.json` with reader checksum.
- Actual browser sample check/filter/sort/notes/amendment flow tested at requested1341px (rendered1342px) and390px. No horizontal overflow or captured console warnings/errors. Expected favicon403 in the isolated preview. Screenshots `outputs/insider-review-1341.png`, `insider-review-390.png`, `insider-review-amendment-390.png`. Sample transport/workers/orders disabled; preview5066 stopped, tab closed, viewport reset.
- Combined maintenance candidate remains **not installed**. At23:29ET production PID38140/source81b4c2f8877820b8/config checksum were unchanged; Gateway and P&L available, no pending intents/decision. Pre-existing manual/session-paused/Fox-off controls preserved. No restart, dependency install, trade or cancellation.

This cumulative candidate supersedes research-live and all older held patches for installation. Earlier test totals belong to different source versions and are not a current full-suite claim.
