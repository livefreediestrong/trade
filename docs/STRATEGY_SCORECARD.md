# Strategy scorecard

Research page and Fox page → **Strategy scorecard**. It shows, in one table, how each kind of call has done. The idea is borrowed from copy-trading apps, which show a track record for every strategy.

- **Kind of call** = the screener verdict (PASS, Watch, Avoid) × entry timing (early, fair, late, chasing) × side (buy or sell).
- **Won after costs** = share of scored calls whose price move over the look-ahead window beat the estimated trading costs.
- **Average after costs** = mean of `outcome_executable_move_bps` (100 bps = 1%). Hover it for a dollar example: −12 bps is about −$1.20 per $1,000 traded.
- Fewer than 30 scored calls → "Too few calls to judge". A handful of wins proves nothing.
- "Stay out" calls are counted separately: how often the price did not move enough to pay for a trade.
- Mock, routed, pending and out-of-period rows are excluded.

It measures the desk's calls, not account profit, and has no SPY comparison. It is **display only**. Fox already stops setups with a losing record through `_evidence_block` in `app.py`. Route: `GET /api/strategy-scorecard?days=30` (1–365).
