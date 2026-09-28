# Layout continuation — September 25, 2026

## Recommendation

Use a task-first desk with six stable destinations, a persistent execution-status strip, a central working area and a quiet companion rail. This is a recommended fit for the current app and the owner's workflow, not a claim of a universally optimal trading interface. Keep Flask/Jinja, the existing CSS tokens and vanilla JavaScript.

| Destination | First question it should answer | Main content |
| --- | --- | --- |
| Overview | What is happening, and what needs my attention? | Broker and paper balances, next useful action, live switch, market context, current Fox activity |
| Fox | What is the agent doing with the broker account? | Operating blockers, actual positions/orders, exit coverage, latest decision and its evidence |
| Research | What supports or contradicts this idea? | Linked symbol/chart/news/thesis, source dates, costs, evaluation and journal |
| Paper | What are we testing, and what happened? | Separate simulated funds, experiment rules, fills/costs, goals and evidence |
| Ticket | What exact order am I reviewing? | Immutable account/symbol/quantity/price/fees, reviewed terms and explicit confirmation |
| Settings | What needs configuration or repair? | Connections, source health, model budget, risk policy, recovery and appearance |

The design borrows linked context from [TradingView's documented chart synchronization](https://www.tradingview.com/support/solutions/43000629992-how-to-sync-the-charts-of-my-layout/), explicit portfolio/order monitoring from [IBKR's Mosaic overview](https://www.interactivebrokers.com/en/?f=%2Fen%2Fgeneral%2Feducation%2Fpdfnotes%2FWN-TWSMosaic.php), and secondary controls on request from [Nielsen Norman Group's progressive disclosure guidance](https://www.nngroup.com/articles/progressive-disclosure/). These sources were checked in this continuation. Applying those principles to this desk is our design judgment; none establishes profitability or execution readiness.

## Implemented now

- Shared execution status stays at the top while scrolling. One page navigation bar replaces the repeated visible brand/tool row; help and theme controls remain accessible in Desk details & appearance.
- Desktop companions and local page navigation now occupy the right rail. Existing portraits, expressions, factual action mapping, speech and motion preferences are preserved. The rail remains 270–278 pixels wide so expressions remain readable.
- Overview starts with actual broker value, separately labelled paper equity and the next useful action. Live OFF recommends research; it no longer leads immediately toward activation. Missing risk data takes precedence. A closed regular session directs an enabled, account-confirmed desk toward after-close review.
- The live switch is compact with its existing warning and confirmation workflow intact. Market context and a short Fox summary precede the long activity/calendar log. The duplicate overview readiness/balance panel was removed; the detailed account panel remains on Fox.
- Mobile uses six explicit navigation links in a two-row grid, two balance cards side by side, and secondary appearance controls after the main content.
- Navigation links retain native link semantics and mark the current page. Skip to workspace transfers keyboard focus to the main area. Scenery & motion opens the existing appearance disclosure.
- Account data availability is labelled Data available instead of an ambiguous Ready. The primary risk/exposure text uses the same current state as broker balances, and expires when the state is stale. Slower companion explanation polls no longer replace it. OFF, enabled-but-paused supervision, unknown exposure and active app-managed exit requirements remain distinct.

## Current provider evidence

The redacted Gateway trace contains a numeric Daily P&L callback at **2026-09-26 02:26:44 UTC / September 25 22:26:44 ET**, after an API disconnect/reconnect. Current read-only checks report `risk_ready=true`, a real reported zero P&L, zero broker positions, live mode manual/OFF and Fox disabled. No Gateway or app restart was performed in this continuation. This is a recovered current feed; reconnect/overnight durability still needs evidence. Do not close G01 solely from this snapshot.

This pass changed frontend assets/templates and tests only, plus documentation. Jinja auto-reload and a browser reload apply the UI without interrupting the recovered broker session. The server's started fingerprint remains `5df690bf038ac96c`; health may report the edited source bundle as newer. Updated frontend imports carry `workspace-20260926-1`. A fresh backend restart is not claimed.

## Verification and limits

- **92 focused Python tests passed**: workspace rendering/state, execution repairs, initial P&L recovery and P&L stream safety. The later wording/focus edit reran the 17 workspace tests successfully.
- **All 27 frontend Node suites passed**, including exact-ticket behavior, stale/racing requests, autonomous avatars, budget/option UI and the new current-state/explanation separation regression.
- **18 real-browser page/width checks**: Overview, Fox, Paper, Research, Ticket and Settings at requested 1341 (browser reports 1342), 1024 and 390 pixels. No horizontal overflow or console errors observed. Phone local anchors and duplicate-ID checks passed. Keyboard details, current-page navigation, skip-link focus and light/dark switching were exercised; dark mode restored.
- At the desktop test width, the Overview account section moved from approximately **597 to 204 pixels** below the viewport top. On a 390×844 viewport it begins around **235 pixels**, with both account values visible before scrolling.
- Screenshot and browser receipts are under the Codex task's `outputs/layout-20260925/`. The final provider snapshot is `runtime.json`. Existing broker-paper lifecycle, sustained market-open reliability, strategy cohort and external source-access limitations remain in [TASKS](../TASKS.md).

No orders, cancellations, live activation, limit changes, paid model requests or new credentials were used for validation. A browser layout check is not broker-paper qualification.

## Next design work

The overview and shared shell are substantially clearer. Fox and Research still contain long, detailed pages. A subsequent focused pass should group Fox into current supervision, positions/orders, and decisions/history; Research should emphasize the existing linked-symbol workspace and move large catalogues/evaluation history behind labelled secondary views. Keep critical blockers and current exposure visible. Do not add a draggable window system or new framework merely to imitate a trading terminal.
