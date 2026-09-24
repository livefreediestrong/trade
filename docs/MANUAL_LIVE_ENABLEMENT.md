# Manual live enablement reference — not executed

This file documents the actual code as of 2026-09-23. It is not an arming script. The agent did not change `.env`, select `auto_live`, start a live session, submit an order or cancel a broker order.

September 24 addition: the [direct stock ticket](LIVE_STOCK_TICKETS.md) uses the same verified `live_manual` configuration below. It adds no environment or arming flag. Buy, sell-owned and cover tickets require their own exact order review and explicit user submission.

September 24 addition: [Moss broker agent](LIVE_AGENT.md) now connects a separate saved policy to broker execution. Its visible Save / Start / Pause controls are the preferred agent workflow. Saving leaves it paused; starting requires the exact account, current revision and REAL/PAPER confirmation. An installed agent policy replaces legacy scheduled broker scans, including while paused. The legacy `auto_live` JSON below alone does not enable that agent. No new environment flag is required.

The older features remain separate from that agent:

1. **Existing desk execution**: `live_manual` requires a fresh account-bound review and ticker confirmation for each order. The existing `auto_live` path can submit eligible whole-share market orders when the live session is active. It does not use Moss's saved rehearsal plan.
2. **Moss rehearsal**: `data/research_companion.json` stores a test plan. Saving it or using Rehearse sends no broker order. Its fractional, short, limit-order and dollar-budget preferences do not configure the existing auto-live path. There is no Moss arming flag in this implementation.

## Broker connection variables

The current adapter reads these variables from the existing project `.env` at app startup. Substitute the exact account identifier shown by your Gateway; do not paste the example placeholder as an account.

```dotenv
BROKER_PROVIDER=ibkr
IB_GATEWAY_HOST=127.0.0.1
IB_GATEWAY_PORT=4001
IB_CLIENT_ID=37
IBKR_ACCOUNT=U_REPLACE_WITH_YOUR_ACCOUNT
IBKR_LIVE=true
```

Gateway paper testing instead uses the paper account identifier (starting `DU`), `IB_GATEWAY_PORT=4002`, and `IBKR_LIVE=false`. Do not mix live and paper identifiers/ports. If another client uses client ID 37, choose a unique ID consistently. Authentication remains in Gateway; no username, password or MFA value goes in this file. The API setting “Prepare DailyPnL when downloading positions” must remain enabled. Gateway's read-only API setting must permit order submission for a human-operated execution test.

No broker source-code change is required to select the existing modes. Do not remove account checks, P&L gates, intent persistence, quote freshness, expiry handling, or reconciliation. A new connection must supply an actual daily-P&L callback; unknown is never zero.

## Exact existing API configuration, for you to apply manually

These are request bodies and endpoint names, not commands run by the agent. Prefer the existing Settings execution-mode controls, which use these same account-verification paths. Keep the local server bound to `127.0.0.1`.

**First stop the main session**, before changing mode: `POST /api/session/stop` with `{}`. Read `/api/state` and verify `config.session_active` is false. Stopping new research does not cancel working orders or close positions. Resolve those separately at the broker before switching environments.

For manual live orders, `POST /api/config`:

```json
{
  "mode": "live_manual",
  "live_confirm": "REAL",
  "rth_only": true
}
```

For the **existing automatic whole-share market-order path only**, after its broker-paper qualification is complete, the actual mode request is `POST /api/config` with the following example values. Replace the loss/size/count values with your tested limits; they are illustrative, not recommendations:

```json
{
  "mode": "auto_live",
  "live_confirm": "REAL",
  "rth_only": true,
  "kill_switch": {
    "armed": true,
    "max_daily_loss_usd": 2,
    "max_trades_per_day": 1,
    "max_position_size_usd": 10
  }
}
```

For a verified **broker paper** connection, that confirmation string is `AUTO_LIVE` instead of `REAL`. The server verifies the actual connected account and persists `broker_identity`; never hand-edit or invent that identity.

`kill_switch.armed=true` turns the configured **risk limits** on; it does not mean “disable the stop.” The automatic order capability comes from `mode=auto_live` together with an active session. `max_position_size_usd` is a price-based exposure check, **not a guaranteed $10 total including fees**, especially with market orders. The risk preset and other gates can reduce size further. A whole share more expensive than the cap cannot be bought under that cap.

The separate manual action that starts the selected broker session is `POST /api/session/start` with `{}` (the **Start checking** button). If the selected mode is `auto_live`, this can permit automatic real-money submissions as soon as an eligible idea arrives. The agent has not performed this action.

To stop new automatic entries, use `POST /api/session/stop` with `{}`, then select `live_manual` through the verified configuration flow if desired. Confirm status and all outstanding orders/positions at the broker. Neither action is a cancel-all or flatten command.

## Qualification still required

Unit tests and local paper fills do not qualify broker execution. Exercise order acceptance/rejection, partial fills, fees, cancellation races, disconnect/restart recovery, unknown submissions, account switches, market close, missing P&L and stale quotes on the broker paper account. Verify both the desk records and broker records. No such execution was performed in this implementation pass.

Moss's notebook rehearsal cannot be promoted by setting an environment variable. Use the separate broker-agent policy for supported whole-share Market/DAY limit orders; its quantities and limits are enforced independently. Fractional and borrowed-share entries remain unsupported. Broker-paper qualification remains required. Do not copy rehearsal fields into `config.json` and assume they take effect.

## Source pointers

- `broker_ibkr.py`: `_settings`, `_identity` — environment and account/port validation.
- `app.py`: `_api_config_post`, `api_session_start`, `ingest_signal`, `execute_gated_broker_or_paper`, `_broker_session_gate`, `_broker_risk_gate` — actual existing mode and execution paths.
- `research_companion.py`: `plan`, `rehearse` routes — store/inspect only; no broker writes.
- `order_terms.py`: supported whole-share order types and explicit unsupported capabilities.

IBKR references: [TWS error codes, including fractional-share restrictions](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes), [Web API March 2026 stock cash-quantity change](https://www.interactivebrokers.com/docs/web-api/changelog). The Web API is a separate integration; its support does not automatically extend this Gateway adapter.
