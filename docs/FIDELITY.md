# Fidelity portfolio (suggestions only)

Home page → **Fidelity portfolio**. Owner decision 2026-09-28: the desk never connects to,
reads from or orders at Fidelity (Fidelity has no retail trading API). You import holdings and
place any trade yourself in Fidelity. Data lives in `data/fidelity_portfolio.json`, separate from
the IBKR desk, Fox, its limits and the paper ledger.

## Import
- Fidelity web: Positions → Download (CSV). Trader+ position exports also work.
- Or typed lines: `META 1.39 718.99` (symbol, shares, average cost) and `CASH 4225.70`.
- Money-market rows (SPAXX, FDRXX, FCASH, "Cash (…)") and Pending Activity count as cash. The same
  stock in several accounts is merged with a weighted average cost.

## What each holding shows
- **Trend** from the 20- and 50-day averages (up / mixed / down).
- **Buy setup today** only when the desk's screener verdict is PASS (volume, trend and sector checks).
  A screener AVOID for low volume alone is a same-day entry filter, not a reason to sell.
- **Protective stop idea**: price − (stop multiple × 14-day average true range).
- Notes: above your position limit, earnings within 7 days.

## Sizing (fractional shares, $0 commission)
shares = min(account × risk % ÷ (price − stop), room under the position limit ÷ price, cash ÷ price),
rounded down to 0.001. Defaults: 1% risk, 20% largest position, 2× daily-move stop.

## Ideas
Today's desk PASS buy signals for stocks you don't hold, with the desk's stop and target, sized the
same way. The panel always shows the strategy's evidence test (60 scored calls over 10 days that beat
costs); until it passes, treat setups with caution.
