# Reddit overnight-return claim — reviewed September 26, 2026

User source: [r/insiderData thread](https://www.reddit.com/r/insiderData/comments/1w05iwl/this_seems_so_weird_anyone_knows_why_it_could_be/), a cross-post of a Micron discussion. The original image was inspected in the real browser, not inferred only from comments.

The image claims extraordinary compounded MU returns from buying each close and selling the next open, versus large losses during regular hours. It proposes a trading agent placing $1,000 each day. These are different possible sizing rules: reinvesting an entire growing balance is not the same experiment as using a fixed $1,000 per trade. The chart alone does not establish executable fills, data adjustments, costs or a forward-looking edge.

The comments disagree and use different dates and instruments. One commenter reports a shorter MU period in which buy-and-hold beat overnight-only; another reports a different outcome for NVDA. These are unverified community calculations, not independently reproduced results. The Korea/news explanations are hypotheses, not demonstrated causal evidence. No account trades or app rules were changed from this thread.

## What independent research adds

The [New York Fed's original overnight-drift research](https://www.newyorkfed.org/research/staff_reports/sr917) examined U.S. equity returns around European market opening hours. A [July 2026 update](https://libertystreeteconomics.newyorkfed.org/2026/07/the-disappearing-overnight-drift/) found that the specific 02:00–03:00 ET futures return pattern was approximately flat in the 2021–2025 sample, following stronger earlier results. This is not a direct replication or rejection of MU close-to-open returns. It does show why a historical pattern needs a later, untouched sample.

[SEC investor guidance](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-42) describes lower liquidity, wider spreads, price uncertainty and differing broker/order arrangements outside normal sessions. Even if the proposed transactions are at regular-session auctions, historical open/close prices do not prove a retail order would receive those prices in the required quantity. Overnight exposure also crosses periods when the desk or broker may be unavailable.

## A useful app research experiment

Keep this in the research workspace and existing G08/G12 queue. Before any strategy conclusion:

1. Freeze symbols and date ranges before inspecting the result: MU, NVDA and a broad-market comparison, with several market periods and a separate later test period. Record every variant tried.
2. Preserve dated open/close bars, provider and retrieval time. Use consistent split/dividend treatment for both sides; never divide an unadjusted open by an adjusted close. Confirm session dates, holidays and early closes. Reject missing sessions rather than treating a multi-day interval as one normal overnight return.
3. Separate three measurements: previous close to next open; open to close; and buy-and-hold over the same overall dates. Check that the compounded return components reconcile to the full-period result before costs. Display fixed-dollar and reinvested-capital experiments separately.
4. Add both entry and exit costs, spreads, conservative auction slippage and missed/partial fills. Show a range of assumptions rather than a single assumed perfect-fill curve. Include cash constraints and do not invent tax results.
5. Report worst overnight loss, drawdown, number of observations, concentration in the largest gap days and the share associated with earnings. Removing a few favorable days can expose a fragile result. Earnings attribution requires dated event evidence.
6. Have Changing Woman document the evidence and alternatives; Fox should challenge it, compare buy-and-hold and require a later paper sample after costs. Preserve a negative/no-edge conclusion. Do not let either AI promote the idea into live rules.

September 27 continuation: the [bounded historical comparison is implemented and tested](OVERNIGHT_STUDY_2026_09_27.md), including frozen source observations, consistent adjustment basis, missing-session rejection, geometric reconciliation, cost/investment sensitivity, fixed/reinvested sizing, retrospective blocks and download/replay. A real three-symbol example returned complete data and retained negative results. The posted chart and its unknown full-period numerical returns have **not** been independently reproduced. A new execution strategy, overnight permission, broker order type or maintenance exception has **not** been enabled. Dated earnings attribution, cash/execution constraints and independent source validation remain G12 work; full-policy prospective qualification remains G08.
