# Account holdings and price coverage — September 27, 2026

The Research account review previously treated the stock-book subset as the portfolio and could value a holding with its purchase-price fallback. That can make an incomplete or unpriced account look fully valued. The new review lists every position returned by the existing IBKR account reader and shows which positions have supported prices. Unavailable amounts remain unknown. Refreshing, disconnecting or receiving conflicting records clears previous totals.

## Scope

- A requested IBKR review reads all returned asset types and currencies through the existing serialized broker owner. It adds an optional read of the existing portfolio cache; ordinary protection reads do not depend on that cache. There is no new connection or subscription.
- Values require matching account, contract and position quantity, a positive reported market price and an explicit supported multiplier. Average cost is not used as a market price. Conflicting records or stale inventory withhold results.
- Only USD stocks and USD option premium values are calculated. Foreign-currency instruments, futures, missing prices and unsupported multipliers remain visible but unvalued. No currency conversion is inferred.
- Gross adds the absolute included values; net offsets their signs. Neither is account equity, buying power, margin, maximum loss or proof that positions hedge each other. Short-option premium does not measure assignment obligations.
- Sector groups and uniform price-move examples cover priced USD stocks only. Options are excluded from these linear examples. Historical correlations also use only the known USD stock subset. Funds are not decomposed into their underlying holdings.
- The alternative broker-book route is explicitly labeled a stock subset. Unfinished orders and other holdings remain unverified there.

## Price timing

IBKR account portfolio updates report market price and average cost as different fields. Account updates may arrive periodically; reading cached data now does not establish the exchange timestamp of the price. The page therefore labels the account-read time separately and does not describe these values as live execution quotes. See [IBKR account updates](https://interactivebrokers.github.io/tws-api/account_updates.html) and [IBKR account and portfolio lesson](https://www.interactivebrokers.com/campus/trading-lessons/python-account-portfolio/).

## Validation and limits

The initial purchase-cost regression reproduced an incorrect $1,000 value. The final focused run passed 211 checks covering portfolio coverage, account commitments, funds, broker safety, lifecycle, reconnect and stock-book contention. An actual ib_async framed-message decoder test distinguishes market price from average cost; its broker transport is isolated. The actual JavaScript controller passed explicit-request, stale-result clearing, unknown-value, escaping and error checks.

The actual controller/template were exercised in a browser with clearly labeled sample data at requested 1341px (reported 1342px) and 390px widths. Six sample holdings produced three supported values and three unvalued rows; an intentional failed refresh removed previous amounts. There was no page-level horizontal overflow; tables retain their existing inner scrolling on mobile.

Gateway is intentionally closed by the owner. No live broker behavior, paper order acceptance, assignment, settlement or profitability is qualified by these tests. This view is local and descriptive: it does not add account data to model prompts, change risk limits, activate/pause trading or send/cancel orders. Full account-risk and AI-context integration remain pending. Installed-version verification is recorded separately after deployment.
