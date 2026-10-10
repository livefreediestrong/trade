# Pre-registered strategy search (2026-09-28)

**Question:** is there a well-known, daily-bar rule worth automating for a small account?
**Answer:** no. Nothing survived once the biased stock list was accounted for.

## Method
- Data: ~10 years of split/dividend-adjusted daily bars for Fox's 243-symbol list plus SPY/QQQ
  (220 usable). Rebuild with `python fetch_daily.py daily.pkl` (reads the symbol list from the
  running desk at 127.0.0.1:5056).
- Five designer agents proposed 15 published rules with parameters **fixed from the source,
  before any data was looked at**. A selector kept 10 distinct ones (M = 10, Bonferroni).
- `harness.py` fixes the timing (decide on day t, fill at the next open), the costs
  (Fidelity: $0 commission + 5 bps slippage a side; IBKR $110 account: +1% a side) and the
  pass rules before any strategy ran. `run_one.py <module> <name> <M>` scores one strategy.
- Every strategy was implemented, then **independently re-implemented from the written spec**
  (`verify/`) and audited for look-ahead. All 10 pairs produced identical trade lists.
- One verifier found a harness bug: a slot freed by a losing trade skipped the next entry.
  Fixed (allocate `min(equal share, free cash)`), and every strategy was re-scored (`reports/v2_*`).
- `excess.py` adds the survivorship check: each stock trade minus the average return of the
  whole list over the same dates. The list is today's watchlist, full of later winners
  (QUBT, RGTI, IONQ, OKLO, GME), which flatters anything that buys these stocks.

## Results (Fidelity costs, after the harness fix)

| Strategy (source) | Trades | Mean/trade | Lower bound | 5-slot Sharpe (SPY) | Max DD | vs same list (lower bound) | Pre-registered pass |
|---|---|---|---|---|---|---|---|
| Dual momentum on stocks (Antonacci) | 1,745 | +606 bps | +88 | **1.07** (0.84) | −70% | +396 bps (**−2**) | yes, but fragile |
| Connors RSI(2) pullbacks | 5,626 | +78 | +36 | 0.61 (0.84) | −39% | +32 (−18) | no |
| Short-term reversal, monthly | 6,523 | +235 | +23 | 0.48 (0.90) | −89% | +31 (−73) | no |
| 12-month time-series momentum | 12,613 | +201 | +56 | 0.34 (0.84) | −51% | +7 (−58) | no |
| Clenow stocks-on-the-move | 241 | +477 | −191 | 0.74 (0.84) | −54% | +99 (−485) | no |
| Heston-Sadka seasonality | 1,986 | +257 | −50 | 0.50 (0.84) | −85% | +62 (−141) | no |
| Turtle System 2 breakout | 5,562 | +354 | −104 | 0.06 (0.87) | −72% | −1 (−349) | no |
| Vol-targeted SPY trend (Faber) | 344 | +57 | −30 | 0.44 (0.85) | −21% | SPY only | no |
| Halloween (SPY, Nov-Apr) | 60 | +117 | −47 | 0.51 (0.90) | −7% | SPY only | no |
| Turn of the month (SPY) | 120 | +19 | −27 | 0.31 (0.89) | −2% | SPY only | no |

- Only dual momentum met the pre-registered rules. Its robustness check says **fragile**: both
  halves (before and after 2021) fail on their own, removing 2024 breaks it, since 2021 it trails
  SPY on Sharpe (0.81 vs 0.92), it had a −70% drawdown, and QUBT alone supplies much of the gain.
  Against the same list over the same months its edge is not significant (lower bound −2 bps).
- The SPY timing rules cut drawdowns but earned far less than simply holding SPY.
- At the IBKR $110 account's costs (1% a side) most rules turn negative.

**Bottom line:** buying and holding SPY had a Sharpe ratio of about 0.84–0.90 over these periods,
better risk-adjusted than every rule tested except one fragile, survivorship-flattered result.
Nothing here is worth automating. Any future rule should pass this harness plus the
same-list check before real money is involved.
