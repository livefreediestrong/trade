# Live options (Moss LIVE auto + manual)

## Armed capabilities
- **Long calls / puts**: BTO + STC (×100 notional)
- **Short calls / puts**: STO + BTC
  - Covered short **calls** when long underlying shares ≥ contracts×100
  - Naked STO requires explicit `allow_naked` / policy `allow_naked_short` plus buying-power floor check (not full IBKR margin)
- **Vertical spreads / BAG**: call/put debit & credit via IBKR combo path (`_place_bag_from_desk`)
- **auto_live**: Moss broker agent can convert PASS stock setups into OPT/BAG when **Enable auto options** is checked on the agent policy

## UI label
**Moss LIVE options · calls/puts · shorts gated · BAG verticals**

## Honest risk_ready
New auto options risk requires verified broker day P&L (`risk_ready`). Missing PnL blocks conversion.

## Notional
`contracts × premium × 100` via `order_terms.option_notional`.

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
