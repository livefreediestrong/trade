# Options paper desk, research universe and compact help

Implemented 2026-09-23 with Flask/Jinja and vanilla JavaScript. Options broker execution does not exist in this module. No live execution flag or permission is changed by these features.

## Broader stock and ETF discovery

Moss's **Paper workday → Research universe → Broad US stocks & ETFs + focus list** reads Nasdaq's public [Nasdaq-traded symbol directory](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt). This includes eligible securities on multiple US listing exchanges, not just Nasdaq listings. The parser retains supported stock, ADR and ETF symbols, including dotted class shares. Test issues, NextShares, unsupported symbol formats, warrants, rights, units, preferred securities and non-ETF notes/debentures are excluded. It is not a worldwide, OTC, futures, FX or cryptocurrency directory. Current membership is discovery data, not a survivorship-free historical universe.

Directory membership does not certify liquidity, borrow availability, broker permission or working market data. Existing freshness, session, liquidity, volatility, provenance, model-budget and risk gates remain. Symbols with insufficient data do not become executable just because they are listed.

The original focus list remains available (now 1–500 symbols). Broad mode evaluates one rotating focus name and one broad name per cycle. The cursor persists across sessions/restarts. At the default 120 seconds, a normal full session offers roughly 195 cycles before processing delays: coverage of the whole directory takes many sessions. This does not claim a simultaneous full-market scan. The worker researches at most one qualified candidate per cycle; most cycles use measured liquidity/volatility ranking, with periodic exploration. The main desk's saved watchlist remains separate.

Refresh occurs on the first broad-mode work cycle if the cache is older than a day; manual refresh is available. Failed/incomplete downloads preserve the prior list and automatically retry after an hour. Failure text and the cache timestamp remain visible. Only public directory data is downloaded; no user account data is sent to that source.

## Options workflow

Supported strategies: long call, long put, call debit vertical, put debit vertical, call credit vertical, put credit vertical. Each vertical has one bought and one sold contract at different strikes, with identical underlying, expiry and call/put type. Only standard 100-share USD equity/ETF option classes qualified by IBKR are accepted. No naked short options, adjusted/nonstandard contracts, index options or legging out.

1. Enter any supported US stock or ETF symbol. Load listed expirations and candidate strikes, or enter an exact contract. Not every strike/expiry combination in the returned parameters necessarily exists; each leg is qualified again.
2. Select the strategy, whole contract count, maximum paper loss budget and assumed fee per contract per leg per transaction.
3. Preview displays contract identity, live bid/ask, available model Greeks, opening debit/credit, fee assumptions, modeled expiry payoff limits and fee-adjusted break-even.
4. Confirm within 30 seconds. The server requests fresh data, verifies the exact contract IDs and rejects a price worse than the reviewed net premium. Changed settings, cash/position limits and review expiration are rechecked after I/O.
5. Use **Preview paper close** to review an entire held strategy. Closing cash flow and estimated realized P&L include entry and exit fees. Closing remains available while new paper entries are paused, subject to the same market-data/session requirements.

The local book starts at $1,000 and is stored in `data/options_paper.json`, independently of stock paper and actual broker funds. It reserves modeled expiry maximum loss plus assumed round-trip fees. Limits: five open positions, $250 modeled risk per trade, 1–10 whole contracts. Entry uses ask for buys and bid for sells; exit reverses the sides. Simultaneous spread execution is hypothetical. This does not model queue priority or guarantee that a broker can execute the combination at those prices.

Quote requirements: IBKR live data type 1, confirmed unhalted status 0, positive uncrossed bid/ask, at most a 25% quoted spread, sufficient displayed size, and receipt of both price sides within ten seconds. Delayed/frozen, missing, future or stale receipts cannot produce a paper fill. IBKR can omit halt status; unknown status remains a blocker. The receipt times are local callback times, **not exchange timestamps**. [IBKR documents the feed types and halt status](https://interactivebrokers.github.io/tws-api/tick_types.html).

The displayed $0.65 fee default is an editable simulation assumption, not a verified broker commission. Modeled payoff limits assume both spread legs remain intact through expiry; closing spreads, fees and early assignment can change actual results. Standard contracts usually represent 100 shares, but adjusted contracts can differ; this version rejects nonstandard multipliers. [OIC options basics](https://www.optionseducation.org/optionsoverview/options-basics).

Fills are restricted to the stock market's regular session and early-close calendar, conservatively ending at 4 p.m. Eastern even where an instrument may trade later. New expiry-day positions stop 30 minutes before close. Exercise, assignment, automatic settlement and stock delivery are not simulated. Expired positions remain visibly unresolved and their reserves are not silently released. Close them before expiration when valid data is available.

## Automation inventory

| Capability | Current behavior |
| --- | --- |
| Stock/ETF paper workday | Optional automatic session schedule, quality filtering, bounded rotation, research and eligible simulated entries |
| Broad ticker directory | Automatic daily refresh during active work; manual refresh; retained cache on failure |
| Paper exits and evaluations | Horizon exits, optional pre-close exit attempts, grading and retained daily reports |
| Empirical Bayes personality | Optional ranking and bounded paper sizing using qualified outcomes; no guaranteed profitability |
| Actual trade journal | Read-only execution/commission sync about once per minute while running; options contract identifiers retained |
| Options discovery and estimates | Read-only requests on demand |
| Options paper entry and close | Exact review plus a manual click; no automatic options strategy worker |
| Real-money execution | Existing manual configuration and approval boundaries; no arming by this change |

Potential next automations are options screening, expiry reminders, Greeks/exposure reports and controlled paper strategy experiments. They require implementation and validation; they are not silently active. Research evidence and fitted ranking parameters are retained app data, not changes to a foundation model's training weights.

## Editing the presentation

`static/context_help.js` groups secondary `.hint-line` and `.title-ctx` explanations into one small `?` per nearby heading. Hover, keyboard focus or tap opens the explanation; Escape, outside click and scroll close it. Identified status fields, dialogs, forms, links, errors, amounts and `.keep-visible` content remain in place. Use `data-context-help` for an explicit helper and `.keep-visible` for critical text. The current enhancement runs when the document loads; dynamically rendered status/error explanations remain visible.

Edit `templates/desk/options.html`, `static/options.js` and the existing CSS tokens for options presentation. Edit `options_desk.py` for the local simulator, `broker_ibkr.py` for read-only data requests, `market_universe.py` for directory discovery, and `templates/desk/moss_workday.html` for automation controls. No new frontend framework or build step is required.

## Validation scope

Tests use isolated temporary books and deny broker execution. They exercise all six payoff formulas, whole-contract validation, price/identity/session/freshness blockers, review expiration, settings revocation, cash limits, duplicate submission, paper closing, read-only subscription cleanup, directory filtering and failure retention. The layout test validates separate ownership of live, stock paper and options controls. Browser integration uses a temporary book and labeled fixture prices. These checks are not evidence of real broker execution or market-hour options liquidity.
