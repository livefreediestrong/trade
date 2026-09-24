# Live options (Moss LIVE auto + manual)

## Armed capabilities
- **Long calls / puts**: BTO + STC (×100 notional)
- **Short calls / puts**: STO + BTC
  - Covered short **calls** when *unpledged* long underlying shares ≥ contracts×100 (shares already covering held or working short calls do not count)
  - Naked STO requires explicit `allow_naked` / policy `allow_naked_short` plus a buying-power check against worst-case loss: (strike − premium)×100 for puts, strike×100 for calls (not full IBKR margin)
- **Vertical spreads / BAG**: call/put debit & credit via IBKR combo path (`_place_bag_from_desk`). CLOSE trades the same combo in the opposite direction and is refused unless the account holds that spread
- **auto_live**: Moss broker agent can convert PASS stock setups into OPT/BAG when **Enable auto options** is checked on the agent policy

## UI label
**Moss LIVE options · calls/puts · shorts gated · BAG verticals**

## Honest risk_ready
New auto options risk requires verified broker day P&L (`risk_ready`). Missing PnL blocks conversion.

Auto conversion also requires live option market data (IBKR type 1). Delayed or frozen option quotes block conversion; stock tickets keep their existing delayed-data behavior.

## Notional and risk
`contracts × premium × 100` via `order_terms.option_notional` is the premium exchanged.

Sizing and the max-position safety stop use worst-case loss via `order_terms.option_max_loss`: premium for long options and debit spreads, (width − credit) for credit spreads, (strike − premium) for short puts, and strike for naked short calls. The auto agent's `max_order_usd` budget buys contracts against that loss, starting from `max_contracts`.

## Prices
Option and combo limits must be whole cents. Auto limits round onto $0.01 below $3 and $0.05 at $3 and above (combos: $0.01), rounding buys down and sells up. The broker's contract rules remain authoritative.

## Residual risks
- **Assignment / early exercise** on short legs is not simulated
- Naked short margin is a **buying-power floor**, not IBKR portfolio margin
- Cash-secured short puts are treated as naked unless `allow_naked_short` is on
- BAG simultaneous fill is broker-dependent; legs can still break after fill

## Verify (no live fire required for unit tests)
1. `pytest tests/test_auto_live_options.py tests/test_live_options_v1.py -q`
2. Flask restart only (`_restart_flask_only.ps1`) — do **not** restart Gateway
3. In Live automation → Moss broker agent: enable auto options checkboxes, save policy (agent stays paused)
4. Manual ticket Option tab: BTO/STC work; STO needs Covered or Allow naked; BTC needs short holding
5. Confirm status shows `auto_options_armed` only when agent enabled **and** auto_options.enabled

## Restart
Flask / desk process on :5056 only. Gateway stays up.
