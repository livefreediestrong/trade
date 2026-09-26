# Live trading ON/OFF

Overview page and Fox page → **Live trading**. This is one plain switch on top of the modes in Settings. Rebuilt from the unmerged `livefreediestrong-integrate-redesign-foundations` branch (commit d455856) for the split pages.

| State | Mode | What it means |
| --- | --- | --- |
| OFF | `manual` | This desk sends no orders to your broker: no Fox trades, no tickets and no automatic exits. Fox is switched off. Anything already open stays open at your broker. |
| ON | `live_manual` | You approve every order. Fox only trades if you start him on the Fox page. |
| ON · Fox automatic | `auto_live` | Shown when Settings or the Fox page armed automation. The switch never arms it. Turning OFF stops it. |

- **OFF** works at once, with no confirmation, because it only removes risk. Behind the scenes it is the same `mode: manual` change as Settings.
- **ON** requires a connected broker and typing `GO LIVE`. It then goes through the same `_api_config_post` gate as the Settings mode menu, with the verified broker identity and the REAL (real money) or AUTO_LIVE (paper broker) token. If that gate refuses, nothing changes. Only `live` and `confirm` are accepted; a request that tries to pass a mode is rejected.
- Pressing ON while already live changes nothing. It never upgrades or downgrades Fox.
- Every change is journaled (`live_master_on` / `live_master_off`, plus the usual `mode_change`).
- Routes: `GET /api/live/master`, `POST /api/live/master` with `{"live": false}` or `{"live": true, "confirm": "GO LIVE"}`.
