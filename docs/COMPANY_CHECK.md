# Company check

Research page → **Company check** (also linked from the Ticket page's symbol box). Type a symbol and the desk reads five public sources about that one company. It shows each result in plain words.

**Research only (owner decision).** Nothing in `company_check.py` is an execution input. It never places, sizes or blocks a trade, and Fox's gates do not read it.

| Section | What it tells you | Source | Needs |
| --- | --- | --- | --- |
| Bottom line | Price vs profit (P/E), next year's P/E, sales growth, profit per $1 of sales, debt vs equity, analyst target range. Each with a one-line explanation. | Yahoo Finance company data (`yfinance`) | nothing |
| Insiders | Public Form 4 / 4/A purchases, sales, grants, tax transactions and other non-derivative activity over 90 days. Roles, joint/direct/indirect ownership, trade/filing dates, plan checkbox and notes. P/S includes private transactions; motive remains unknown. | [SEC EDGAR](https://www.sec.gov/edgar) | `EDGAR_USER_AGENT` with a contact email |
| Federal contracts | Same-name recipient candidates and reported award amounts, with public IDs and excluded-name/coverage details. The verified company total remains unknown. | [USAspending.gov](https://api.usaspending.gov/) | nothing |
| Lobbying | Full-name client candidates, separate firm-income and organization-expense fields, original documents, quarter/date/IDs, amendments, partial coverage and review warnings. No combined spending total. | [Senate Lobbying Disclosure](https://lda.gov/api/) | public anonymous access; shared rate limit |
| Congress trades | Disclosed trades by members of Congress in the last 12 months, with the delay between trade and report. | [Quiver Quantitative API](https://api.quiverquant.com/) | `QUIVER_API_KEY`; confirm current provider access/pricing |

Notes:
- Contract searches compare full legal names after broad retrieval and keep possible matches separate from verified company identity. Different names and unverified subsidiaries are excluded from the displayed company candidates. Recipient IDs alone do not link an entity to the stock; no verified company total is claimed. Searches read at most ten recipient groups and five awards. [Identity correction and remaining work](CONTRACT_IDENTITY_2026_09_27.md).
- Lobbying retrieves broad search results, then compares full legal names. Different names, intermediaries and unverified subsidiaries are excluded from company candidates. Up to two 25-row pages per year are read; the prior year is tried only after a complete readable current-year search has no matching quarterly filings. Partial retrieval never establishes no activity. Registration forms are excluded. Exact duplicate filing IDs are removed; conflicting IDs, dates, amounts or versions remain for review. Only a clearly later amendment can be labeled the latest version within the downloaded group. All original rows remain visible and amounts are never summed across filings. Public IDs do not prove linkage to a stock. [Filing correction and evidence](LOBBYING_RECORDS_2026_09_27.md). Funds skip both sections.
- LDA requests share a process-wide 4.1-second interval and a 65-second retrieval budget per check. HTTP 429 respects Retry-After and does not trigger an immediate retry. Partial source failures retain readable records; incomplete or unresolved results are cached for only one minute. The app does not create an LDA account or require an API key.
- Congress members may report trades up to 45 days late. Studies disagree on whether copying them beats the market. The card shows the delay for each trade.
- One slow or failing source shows its own message; the other sections still load.
- Healthy results may be cached for 6 hours per symbol (memory plus `data/company_checks.json`, newest 80 symbols); incomplete sources are retried sooner. **Read again** ignores the cache. Insider document requests are spaced per check; a shared app-wide SEC request governor is still needed before market-wide monitoring.
- Route: `GET /api/research/company?ticker=LMT[&refresh=1]`.

## Reliability follow-up

A missing company lookup or unreadable Form 4 is an unknown result, not proof of no insider activity. The card reports filings found versus filings read, labels partial coverage, rejects HTML masquerading as XML, and limits transactions to the requested dates. Filing review is capped at 20 documents with a bounded retrieval window. Form 4/A is visible and unresolved amendments withhold totals. Derivative transactions and older submission pages are not read. Incomplete or failed source results are rechecked after one minute instead of being cached as healthy for six hours. Old cache versions, future timestamps and invalid timestamps are ignored.

The [insider-platform comparison and repairs](INSIDER_PLATFORM_REVIEW_2026_09_26.md) add richer evidence, filters and a precisely defined multi-owner purchase flag. Missing prices remain unknown; joint/indirect owners cannot create independent-buyer counts. The flag is descriptive, not an execution input or tested strategy. Those insider repairs were installed in the combined September 26 maintenance release. The later recipient-identity correction has its own installation status and evidence record.
