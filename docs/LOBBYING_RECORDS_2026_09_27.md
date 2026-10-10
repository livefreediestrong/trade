# Lobbying filing correction — September 27, 2026

This package includes the earlier federal-contract identity correction. Installation
is recorded separately; source and browser evidence do not establish deployment.

**Installed September 27 at 00:56:50 ET:** source `56993167f89fdffc`, PID 39784.
All nine files matched the sealed manifest; saved controls were unchanged. The
normal launcher completed, watchdog/upkeep were restored, and the installed
Research workflow returned the corrected public-source records. Desktop/mobile
expansion and layout were verified without browser warnings/errors. At 01:01 ET,
Gateway/API respond, but P&L has not supplied an initial callback after this reload.
This is the existing G01 issue, not a claim of live-trading readiness. The package
did not change the broker adapter. Receipt: task outputs/company-records-install-receipt.json.

## Defects reproduced and corrected

The earlier reader matched broad names, added firm income and organization expenses
into one amount, read only the first page despite requesting 100 rows, and stopped
before later amendments. Three behavior tests reproduced those errors before repair.
The LDA API actually caps pages at 25. Its old Senate address redirects to lda.gov.

The reader now keeps complete legal-name candidates, reported client/registrant IDs,
filing UUID, quarter, posted date, original document, amendment/no-activity/termination
flags, amount type, expense method and issues. A same-name result is not a verified
link to a stock. Different names, intermediaries and unverified subsidiaries remain
excluded and visible for review. Registration forms do not count as quarterly reports.

Firm income and organization expenses are separate fields, never added. Missing or
invalid numbers stay unknown. Conflicting amount fields, negative amounts and API
zero values require original-filing review. No-activity flags do not invent zero
income or expenses. The existing spend field now returns null, including empty results.

Exact duplicate copies are removed. Groups use registrant ID, client ID, year and
period. Clearly later amendments are latest *within the sample*, and previous rows
remain visible. Missing IDs/dates, unexpected types, tied timestamps, conflicting
copies or multiple ordinary reports keep a review flag. No complete amendment history
or issuer linkage is claimed.

The reader uses the fixed official API URL, increments its own page number and never
follows a provider-supplied next URL. It reads at most two pages per year. Prior-year
fallback requires a complete, readable current-year search with no matching quarterly
filings. It reports partial pages, errors, count changes and duplicate/missing IDs.
One failed later page does not hide earlier readable records. A shared 4.1-second
request interval and HTTP 429 Retry-After cooldown bound anonymous traffic; the per-check
budget is 65 seconds. No credentials or new service connection are required.

## Validation

- 99 focused Python checks passed, including identity, mixed amount types, amendments,
  duplicate/conflicting IDs, malformed dates/types/amounts, prior-year fallback,
  incomplete pages, page limits, rate spacing and Retry-After.
- Two controller suites passed. They exercise literal external text, safe links,
  revisions, missing amounts, partial coverage and read-only requests. An initial
  date-formatter scope error was caught and corrected before browser verification.
- Real Apple read: 30 source rows across two pages; 20 same-name quarterly filings,
  one matching registration excluded, no company total. Five other client names were
  excluded, including intermediary and similarly named organizations. No amendments
  were present in this actual sample; amendment behavior is fixture-tested.
- Two original public documents were checked against the API: Federal Street
  Strategies reported $60,000 firm income; the Apple self-filed report showed
  $2,740,000 organization expenses. They remain separate estimates, not additive.
- Actual isolated browser rendering of the saved public receipts was checked at
  requested 1341px (browser reports 1342) and 390px. Expanded filing details and
  original links are readable; document width stays contained. Browser errors/warnings
  were empty. The records now use a full-width section and responsive filing cards.
- Old cached broad totals are invalidated with cache version 5.

## Sources and remaining work

Checked September 27, 2026:
- https://lda.gov/api/openapi/v1/ — official schema and 25-row page limit.
- https://lda.gov/api/v1/constants/filing/filingtypes/ — official filing type codes.
- https://lda.gov/api/tos/ — anonymous rate limit, attribution and retrieval date.
- https://www.senate.gov/legislative/resources/pdf/S1guidance.pdf — income versus
  organizational expenses and outside-firm overlap (older guidance; not used for
  current thresholds).
- https://lda.gov/filings/public/filing/ef1bac6d-c7cb-48a4-885b-0097e33df9ef/print/
- https://lda.gov/filings/public/filing/e9fec06f-33ff-4583-8687-23cd9c67a774/print/

The verified issuer/recipient/client crosswalk, reviewed subsidiaries/intermediaries,
complete historical pagination, point-in-time snapshots and full amendment reconciliation
remain open. This company card remains display-only and does not place, size, block or
authorize trades. These repairs do not qualify the live trading system.

## Installation / rollback

Use the exact combined company-records manifest after a fresh verified state backup,
flat/no-working-orders broker snapshot, unchanged saved controls and normal orderly
maintenance stop. The installer keeps a source rollback copy, restores schedules,
uses the normal launcher and checks loaded source identity. No risk settings change.
Roll back through an orderly maintenance stop if the company route fails or worker
health degrades. Do not restore trading state to roll back a research reader.
