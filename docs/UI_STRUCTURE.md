# Single-page UI map

Updated 2026-09-24. The desk uses Flask/Jinja and vanilla JavaScript, with visible modules on one page. The current desert scene, logo and sidebar companions are layered on the existing trading workflow; no frontend build step is required.


## Phase 1 information architecture (2026-09-24)

Main page spine is attention → overview → live desk → live automation (broker agent as one unit) → options → paper → companion (with agent_summary parked beside it) → research shelf (scanner lives here, not above live) → settings. Inside `#desk-live`, children follow status/risk hub → holdings → ticket (workflow guide collapsed in `<details>`; kill/risk stay visible) → working orders → ideas (opp-panel + signal queue, unmerged) → research loop → demoted live-test/cost planner. Sidebar TRADE jumps match real IDs: Overview, Live desk, Holdings, Ticket, Orders, Ideas, Broker agent, Options, Paper.
## Where to edit

| Change | File |
| --- | --- |
| Page order, navigation, stylesheet/script imports | `templates/index.html` |
| Header, account/mode/connection status | `templates/desk/header.html` |
| Actual broker balances, orders, research and reviews | `templates/desk/live.html` |
| Local simulated account, paper approvals, fills and results | `templates/desk/paper.html` |
| News, market radar, Reddit information and research chat | `templates/desk/research.html` |
| Watchlist, research model, live mode and journal | `templates/desk/settings.html` |
| Approval, tutorial and recap dialogs | `templates/desk/dialogs.html` |
| All page styling and both themes | `static/app.css` |
| Existing data rendering and event handlers | `static/app.js` |
| Actual paper equity chart | `static/equity_scoreboard.js` |
| Linked chart, evaluation, paper experiments and journal UI | `static/workbench.js`, `templates/desk/workbench_*.html` |
| Explicit broker cancellation dialog | `static/order_controls.js`, `broker_controls.py` |
| Immutable order terms and capability matrix | `order_terms.py` |
| Research workspace and captured experiments API | `desk_workbench.py` |
| Moss avatar, routine and access summary | `templates/desk/companion.html` |
| Fox / optional portrait assets and cosmetic animation | `static/moss_avatar.js`, `static/moss_avatar.css`, `docs/assets/moss-fox-atlas.png`, `docs/assets/moss-portrait-atlas.png` |
| Cost calculator and saved automation rehearsal | `templates/desk/trade_planner.html`, `trade_planner.py` |
| Notebook, learned parameters and glossary | `templates/desk/notebook.html`, `static/companion.js` |
| Daily research and fitted research-ranking parameters | `research_companion.py`, `research_learning.py` |
| Options contract preview and separate paper book | `templates/desk/options.html`, `static/options.js`, `options_desk.py` |
| Hover/focus secondary explanations | `static/context_help.js` |
| Broad US stock and ETF directory | `market_universe.py`, `templates/desk/moss_workday.html` |
| Shared scanner and Stocks on Sale | `templates/desk/scanner_workspace.html`, `static/sale_market.js`, `sale_scanner.py` |
| Live automation summary and agent brief | `templates/desk/live_automation.html`, `templates/desk/agent_summary.html`, `static/agent_desk.js` |
| Changing Woman's headlines and source health | `templates/desk/news_sources.html`, `static/news_desk.js`, `static/news_quips.js`, `companion_news.py` |
| Sidebar habitat and scene motion | `templates/desk/sidebar_companions.html`, `static/nadzeel_motion.js`, `static/nadzeel_motion.css` |
| Current compact layout overrides | `static/desk_layout.css` |

`app.css` holds base tokens, layout, data components, dialogs, responsive rules and workbench styles. `nadzeel.css` supplies the current visual theme; guidance, motion and layout have focused stylesheets loaded afterward in `index.html`. Inspect that order before changing an override. There is no generated stylesheet, CSS generator, frontend dependency installation, or build step.

`desk_motion.js`, `trade_glow.js`, `desk_pulse.js` and the standalone old mock are archived under `docs/legacy-ui/`, outside the publicly served assets. The current animated scene and companions use the separate files above. There is no Simple-mode control. New workflows use focused modules instead of expanding the large core renderer. See `WORKBENCH_IMPLEMENTATION.md` for behavior and qualification boundaries.

## How to change the layout

Moss's open-to-close configuration is `templates/desk/moss_workday.html`, included in the companion panel. Its read-only actual execution review is `templates/desk/real_trade_review.html` in the research section. Both use `static/companion.js` and existing form/table styles; no build step. Paper workday status is also reflected in the existing paper-loop strip.

- Reorder includes in `index.html` to reorder sections. Keep navigation targets and section headings in agreement.
- Use `.panel` for a content block and `.wide-panel` when it should span both desktop columns. Below 850px, sections use one column.
- Keep tables inside `.table-wrap`. Wide tables scroll inside their panel; they should not widen the document.
- Use actual buttons and labels. Keep primary modules visible rather than putting them in dropdown containers. Secondary explanations belong in hover/focus help; critical order and risk information stays visible.
- Keep `.hidden` and `[hidden]` authoritative. In particular, CSS must not expose paper bracket fields during a live review, or display an approval modal that JavaScript has closed.
- Preserve element IDs and data attributes used by `app.js`, especially review/confirmation controls and `data-workspace-only`. Moving a control across live/paper sections changes its meaning and requires a behavior test.
- Keep account identity, real/simulated funds, quote age, broker response status, actual order status, and explicit confirmation visible where relevant. Color is supplementary to the text labels.
- The appearance can change without changing saved configuration, approval tokens, risk gates, broker adapters, or P&L subscription handling.

## Validate an edit

From the project directory:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_workspace_ui.py tests/test_execution_repairs.py -q
node --check static/app.js
```

The layout test renders the actual Jinja includes, rejects duplicate IDs, and checks critical live/paper ownership. Other tests exercise approval and execution boundaries with isolated fixtures. They do not send broker orders.

Then use the browser at desktop and narrow widths. Verify account/status text, paper controls, local table scrolling, light/dark themes, and modal open/close/focus behavior. Approval behavior needs isolated tests; do not submit a real order as a styling test. A passing syntax check alone is insufficient.

Reload the browser after CSS or template changes. Jinja template auto-reload checks file modification times, so future template edits do not require restarting the app or broker session. This does not enable the Flask debugger or process reloader. Python changes still require a controlled app restart through the existing launcher and a broker/P&L check afterward. A temporary read-only preview was used for this redesign to inspect the new templates before the one-time deployment restart.

## State updates and boundaries

The browser opts into `/api/loop/stream?compact=1`. Every connection begins with a complete signal history. Later frames may omit unchanged approved, rejected or expired groups and carry `signal_history_delta: true`; merge these groups by key. An explicit empty list clears a group. Pending signals, approvals, broker orders, balances and risk controls still arrive on each state frame. Legacy stream clients retain full snapshots. Keep `desk:state` dispatch after all incoming state has been merged.

Preserve separate paper funds/auto-approval/sizing, exact-order review and current P&L validity handling when editing the UI. Missing P&L is not zero. The workbench behavior is documented in `WORKBENCH_IMPLEMENTATION.md`; the original platform research remains in `TRADING_PLATFORM_REVIEW.md`.
