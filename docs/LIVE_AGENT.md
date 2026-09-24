# Moss broker agent

The broker agent connects the existing market research/brain pipeline to the existing IBKR order gateway. It is separate from the local paper workday and the notebook rehearsal. The new module is visible under **Live automation**.

## Operator workflow

1. Connect IB Gateway and verify the intended account in the desk's existing broker settings.
2. Enter the permitted symbols and policy in **Moss · broker agent**. Choose Market or DAY limit, price-based maximum dollars per order, account daily loss stop, broker attempts/day, research attempts/day, recorded AI budget, confidence threshold, interval, and quote age.
3. **Check symbol budget** reads current quotes for up to the first eight symbols in your draft, two requests at a time. It shows price-only whole-share capacity and highlights symbols your amount cannot cover. Quotes must meet your draft age limit; expired results are marked unavailable. Fees, limit offsets, account cash and other risk limits may reduce capacity. Unchecked symbols are stated explicitly. This check neither saves nor submits anything. Then **Save policy**: saving selects the agent as the scheduled broker owner and leaves it paused. It does not change the current broker mode or submit an order. Any previous agent activation becomes invalid. Unsaved drafts survive status refreshes; concurrent policy edits produce a revision conflict.
4. After qualification on the broker paper account, the operator can type **PAPER** for an IBKR paper account or **REAL** for the actual account and click **Start broker agent**. This explicitly enables `auto_live` and the main session for the exact confirmed account. The route verifies identity, saved revision, genuine equity/daily P&L, local unresolved orders and readable state. Every order has additional checks.
5. **Pause agent** stops new decisions and submissions that have not committed. A broker submission already in progress may finish. Existing orders and positions remain at the broker; use the broker order review or Gateway to manage them. Main session Stop and mode changes also pause the agent. Resuming requires a new explicit agent start.

An explicitly enabled policy persists across app restarts and waits for the next regular US stock-market session. The PC, app, Gateway and data providers must be available. The app does not wake a sleeping PC or bypass Gateway authentication. No Codex or Windows scheduled task is created.

## Execution behavior

- One rotating policy symbol is researched per interval, using the existing configured brain and screener. Empty/error checks consume a cycle. Research count and next eligible time are written before the work starts; restarting cannot repeat that interval or reset daily counters.
- Buy/sell/hold is a research decision. The agent requires PASS, configured minimum confidence, fresh attributable in-session quotes, and no late/chasing entry or existing research/model block. Mock, synthetic, future-dated and stale data cannot authorize orders. Confidence is not a calibrated probability of profit.
- Only listed US stocks/ETFs, whole shares, and the IBKR adapter are supported. Buy can open/add a long; sell can reduce held shares; buy against an existing short can cover it. The selected symbols may include holdings acquired elsewhere. It never intentionally opens a new short position.
- Server policy computes quantity and price terms. DAY limits use the decision price plus the configured offset for buys or minus it for sells, rounded conservatively. Sizing uses the larger of the fresh mark and limit. Existing ATR, preset, position, loss and working-order limits can reduce quantity or block the order. Market orders may fill beyond the estimated amount; fees are not a guaranteed upper bound.
- **Protective exits** (on by default; policy fields `protective_exits`, `breakeven_after_r`, `max_hold_min`, `flatten_before_close_min`). When an agent stock buy fills, the agent records the position with the research plan's stop and target distances, re-anchored to the actual fill price (0.8% of price and twice that when the plan has none). Each cycle, before new research, it reads broker holdings and a fresh, non-delayed IBKR quote. It sells only the shares it bought and still holds, when:
  - the bid reaches the stop (`stop_loss`);
  - the bid reaches the target (`take_profit`);
  - the price has moved `breakeven_after_r` times the planned risk in favor, so the stop moves to the entry price, and the bid then falls back to entry (`breakeven_stop`);
  - `max_hold_min` has passed;
  - fewer than `flatten_before_close_min` minutes remain before the regular close (`end_of_day`).

  Exit limits sit 25 bps below the bid, or 50 bps for stop and end-of-day exits (at least the policy offset). They go through the same gated order path as entries, as reducing orders. Exits are allowed for symbols outside the evaluated universe and count as broker attempts, but the daily attempt cap does not block them. One exit is sent per cycle, and no exit is sent while any broker order is unresolved or when the verified account differs from the policy's account.

  Exits are checks the desk makes on each cycle. They are not resting broker orders or brackets, so they act only while the desk and Gateway run, and a gap can fill below the stop. Positions opened outside the agent are never managed. Policies saved before this version get the defaults.
- There is no broker bracket, fractional execution or live options exit management. A low budget may be unable to buy one whole share. An unfilled DAY limit remains monitored and can block subsequent submissions until resolved.
- Every broker attempt, including sells/covers, rejected attempts and uncertain outcomes, consumes the agent's daily attempt budget before submission. This conservative count is separate from the desk's existing broker-fill count. Raising a limit changes the cap, not the count. Client-ID changes do not reset account counters.
- Daily loss is measured on the broker account. Missing P&L blocks new risk; the existing narrowly verified reducing-order exception remains. The attempt cap can still block a reducing order, so it is not an exit guarantee. Reaching a stop blocks new risk; it does not flatten positions or cancel working orders.
- Identity, mode, active session, policy revision and per-activation run ID are checked again before durable submission. Changing settings during research discards the result. Pausing/restarting cannot authorize an old activation's decision. The adapter verifies the qualified US instrument and cached position/working quantities immediately before submission. External broker activity can still race.
- Unknown submissions and partial fills use the existing durable intent/reconciliation path. They never become simulated fills or trigger a new automatic retry. Historical execution corrections remain part of that path.

The AI cost limit checks recorded whole-desk estimated cost before an agent research attempt. One attempt can call multiple models; this is not a guaranteed provider billing cap and does not govern other desk research features. Research-memory ranking and execution-quality review continue through the existing research and actual-trade journals; neither changes live capital limits automatically.

## Storage and API

`config.json.live_agent` contains `{policy, revision, enabled, run_id}`. No new `.env` flag is needed; the existing verified IBKR settings still apply. The server alone creates policy revisions and activation IDs.

`data/live_agent.json` retains per-account/day research and broker-attempt counters, consumed signal IDs, next-cycle time, rotation cursor, the latest 200 activity entries and `managed` agent positions (shares, entry, stop, target, planned risk per share, breakeven flag, optional hold deadline). Corrupt state blocks execution and is not silently replaced. Source recovery archives exclude both files and never rewind order state.

- `GET /api/live-agent`: local read-only status, saved policy and latest 30 activity records.
- `POST /api/live-agent/policy`: exact `{policy, revision}`; leaves the agent paused.
- `POST /api/live-agent/start`: exact `{revision, identity, confirm}`; explicit operator activation.
- `POST /api/live-agent/pause`: stop future agent work; does not cancel/flatten.

The existing background worker reconciles broker orders before running the agent. When a policy exists, it owns scheduled live research even while paused; the legacy scanner cannot silently take over. Local Moss paper research remains independent. Manual stock tickets still use their own explicit order review in `live_manual` mode.

## Validation boundary

Tests exercise the actual agent, risk gateway, durable reservations and order lifecycle using isolated files and intercepted broker transport; network connections are denied in the fixture. Scenarios include exact activation/account/revision, protective exits (stop, breakeven, target, hold limit, close, stale quotes, changed account, positions sold elsewhere), Market and DAY limit terms, sizing/holdings, pauses during slow work, restart cadence/counters, failed and empty cycles, uncertain submissions, partial fills, daily budgets, missing P&L, corrupt state and final IBKR position checks. Browser-controller tests exercise account confirmation, duplicate clicks, stale status, unsaved edits and uncertain activation responses.

These checks are software evidence, not completed broker-paper qualification, live execution proof or strategy profitability. The implementation pass does not activate the installed agent or send/cancel real orders.

Broker reference: [IBKR TWS API documentation](https://www.interactivebrokers.com/docs/tws-api/doc/introduction). Order acceptance, execution reports and commissions are distinct evidence; the existing reconciliation path remains authoritative for fills.
