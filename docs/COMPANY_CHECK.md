# Company check

Research page → **Company check** (also linked from the Ticket page's symbol box). Type a symbol and the desk reads five public sources about that one company. It shows each result in plain words.

**Research only (owner decision).** Nothing in `company_check.py` is an execution input. It never places, sizes or blocks a trade, and Fox's gates do not read it.

| Section | What it tells you | Source | Needs |
| --- | --- | --- | --- |
| Bottom line | Price vs profit (P/E), next year's P/E, sales growth, profit per $1 of sales, debt vs equity, analyst target range. Each with a one-line explanation. | Yahoo Finance company data (`yfinance`) | nothing |
| Insiders | Who bought or sold with their own money in the last 90 days (SEC Form 4, open-market codes P and S), their role, value, and whether the trade was pre-planned (10b5-1). Grants, option exercises and tax withholding are counted but not treated as buys or sales. | [SEC EDGAR](https://www.sec.gov/edgar) | `EDGAR_USER_AGENT` with a contact email |
| Federal contracts | Contract money obligated in the last 12 months and the biggest new awards. | [USAspending.gov](https://api.usaspending.gov/) | nothing |
| Lobbying | Lobbying reports naming the company as client this year (or last), the amount reported, top issues and firms hired. | [Senate Lobbying Disclosure](https://lda.senate.gov/api/) | nothing |
| Congress trades | Disclosed trades by members of Congress in the last 12 months, with the delay between trade and report. | [Quiver Quantitative API](https://api.quiverquant.com/) | `QUIVER_API_KEY` (paid, from about $30/month) |

Notes:
- Contracts and lobbying are matched by company name ("LOCKHEED MARTIN CORP" → "LOCKHEED MARTIN"), so a similarly named firm can appear. The card lists the matched names. Funds skip these sections.
- Congress members may report trades up to 45 days late. Studies disagree on whether copying them beats the market. The card shows the delay for each trade.
- One slow or failing source shows its own message; the other sections still load.
- Results are cached for 6 hours per symbol (memory plus `data/company_checks.json`, newest 80 symbols). **Read again** ignores the cache. SEC requests are spaced to stay under SEC's 10-per-second limit.
- Route: `GET /api/research/company?ticker=LMT[&refresh=1]`.
