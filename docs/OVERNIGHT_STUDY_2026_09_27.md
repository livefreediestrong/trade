# Overnight and daytime research comparison — September 27, 2026

**Preparation status: tested candidate.** The owner subsequently authorized installation of completed maintenance packages. This feature is combined with the [broker-read load repair](BROKER_READ_LOAD_2026_09_27.md); its release receipt records installation separately. Gateway was closed by the owner before that maintenance, so no current broker exposure or P&L is implied by the research validation below.

Research now has **Overnight or daytime: what changed?** It compares previous-close to next-open, open-to-close and buy-and-hold for one to three exact US-listed USD stock/fund symbols. Opening the page only reads saved comparisons. **Run and save comparison** explicitly retrieves public historical prices and retains the assumptions, observations and results. It cannot call a broker, enable a session, change a risk limit or promote an AI rule.

## Method and evidence

- Date ranges are bounded to 5–730 calendar days, starting January 10, 2025 or later. The existing US equity holiday/early-close calendar is used, with the January 9, 2025 national day of mourning also excluded when finding the prior session. Sessions must have closed at least 30 minutes before the source was retrieved; cached pre-close data cannot become a final bar merely because time passed.
- Yahoo chart data must identify the exact symbol, supported US venue, USD currency, stock/ETF asset type and New York time zone. Each expected session, including the prior close, needs one valid positive OHLC and adjusted close. Missing, invalid or duplicate dates do not become zero returns or get bridged. A symbol without full coverage has no full-period result.
- Synthetic adjusted open = provider open × adjusted close / provider close. Both sides therefore use the same vendor adjustment basis. Dividend/split/capital-gain event records are retained, with no second dividend addition. This is a mathematical decomposition of adjusted prices, not actual auction execution or a cash-dividend accounting engine. Vendor event completeness is not independently established.
- Gross overnight factor × intraday factor must equal the close-to-close factor, both per session and compounded across the entire range. Buy-and-hold uses those same endpoint closes.
- With cost `c` on each side and invested fraction `f`, each repeated interval contributes `f × (price factor × (1-c)/(1+c) - 1)`. Reinvested returns multiply those net growth factors. Fixed-dollar results add interval returns times the original hypothetical amount. Buy-and-hold pays entry/exit costs once; its drawdown uses the intermediate adjusted-close path.
- Zero, chosen and doubled cost assumptions are displayed. Earlier 70% / later 30% blocks are retrospective. The five strongest overnight days and the result without them show sensitivity. Worst overnight and endpoint drawdowns are shown. Removing days is an after-the-fact diagnostic, not a tradable strategy.
- Every attempt is retained, including unavailable source results. The local archive stops at 40 studies rather than silently deleting poor variants. Identical requests within five minutes reuse their record. Provider reads use the existing bounded request/cache facility; at most three symbols are fetched concurrently, and only one study runs at once. No paid model call is added.
- A study checksum binds inputs and outputs. Download recomputes results from frozen observations, without fetching revised history, and requires identical results. The checksum detects accidental change; it is not a signature authenticating a third-party data provider.

## Storage and boundaries

`data/overnight_studies.json` is optional research state and is included automatically in the existing top-level JSON backup coverage. An unreadable archive is preserved and the study endpoint refuses replacement; it does not mark the trading ledger corrupt. A corrupt JSON file can still prevent a full verified backup until repaired. Exports include source links, retrieval times, payload hashes, dated adjusted/raw provider bars, event records, assumptions, all components and results.

This does **not** model random missed fills, auction queue position, cash settlement, whole-share affordability, margin, financing, interest or taxes. The invested fraction is constant for each interval. There is no dated earnings-event attribution. Current-vintage historical adjustments can reflect information published later. These selected surviving symbols and retrospective blocks do not establish an untouched, survivorship-free forward test. Negative results are retained and are not rewritten as a profitable edge.

## Validation

56 focused Python checks passed across overnight research, research-page integration and existing workspace/plain-language checks. Two Node checks verified cost-scenario selection, negative amounts and unknown-value presentation. Tests exercised mathematical reconciliation, both-side costs, fixed/reinvested sizing, partial investment, dividend basis, price rescaling, missing/duplicate/bad observations, identity rejection, holiday/early-close handling, cache timing, drawdown, archive limits, corruption, failed persistence, no-fetch replay, deduplication and unchanged saved controls.

Actual Yahoo public responses for MU, NVDA and SPY over January 10–April 30, 2025 each supplied 77 prices (76 intervals plus the prior close) and one adjustment event. All three reconciled. In the prespecified example with $1,000, 100% invested and 0.05% cost on each side, MU's overnight result was **-24.14%**, compared with **-22.57%** buy-and-hold. This is one illustrative historical range, not reproduction of the Reddit image's unknown full period or a recommendation.

The actual controller was exercised in an isolated real browser with these frozen public observations: run, saved records, keyboard disclosure, download/replay and reload persistence. Requested widths 1341 (actual 1342) and 390 had no page overflow; wide tables scroll within their own region. No captured browser errors were reported. Screenshots and the sealed candidate manifest are kept in the task workspace. Installed-page/browser integration is pending deployment. No broker-paper stop, option, assignment or settlement test is implied by this work; the separate paper account remains unconfirmed.

## Sources

- [Original Reddit discussion](https://www.reddit.com/r/insiderData/comments/1w05iwl/this_seems_so_weird_anyone_knows_why_it_could_be/) and the [prior review/protocol](REDDIT_OVERNIGHT_REVIEW_2026_09_26.md).
- [Yahoo adjusted-close explanation](https://help.yahoo.com/kb/SLN28256.html); source history links are retained per symbol.
- [NYSE market calendar](https://www.nyse.com/markets/hours-calendars) and [Nasdaq's January 9, 2025 closure notice](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2024-87).

G12's bounded historical comparison is implemented. Dated earnings attribution, independent data validation, full execution/cash constraints and a prospective test remain open; G08 strategy qualification is unchanged.
