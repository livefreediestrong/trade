# Moss, paper fractions and trade planning

**Follow-up:** The [paper workday and actual-trade journal](MOSS_WORKDAY.md) now extend this initial implementation. The combined backend was deployed on 2026-09-23 and local paper automation enabled; the restart notice described below records the earlier implementation stage. The newer document supersedes the once-daily-only operation and bounded-memory descriptions for workday evidence. Live mode and non-paper configuration were preserved.

Implemented 2026-09-23 in the existing Flask/Jinja and vanilla JavaScript application. No frontend framework, model provider, dependency installation, broker permission or production execution setting was added.

## What works

- **Fractional local paper trades:** optional six-decimal share sizing and a per-entry dollar cap including modeled entry fees. Fractional partial exits, stop exits and full liquidation retain the remaining quantity. Disabling new fractional entries does not strand existing fractions. These controls affect the simulated paper book only.
- **Cost calculator:** hypothetical whole/fractional, long/short and market/limit scenarios; explicit price movement, entry/exit fee and borrow-cost assumptions; estimated entry cash, after-cost result and break-even. Unknown fees remain unknown. An unaffordable share size produces no modeled trade. Changing inputs invalidates the displayed estimate; late quote responses cannot overwrite changed inputs.
- **Moss:** original owl avatar, Stoic-inspired language, an editable routine and aspirational daily target. The target cannot change risk limits or force an order. Market-session calendar skips weekends, exchange holidays and times after early closes. Research runs once per open session while the app and computer are running; it does not fabricate missed days or wake a sleeping PC.
- **Historical research:** up to four watchlist symbols per review, completed daily bars, source/as-of labels, bounded headlines and one configured AI thesis with existing audits when fresh intraday data is available. Closed markets or unavailable intraday data skip that AI call. Stale completed-session history cannot complete a scheduled day.
- **Notebook and learned parameters:** `data/research_companion.json` stores up to 90 briefs, with the newest 30 selectable in the UI. Each brief records observations, account context, target, model provenance and a fitted research-ranking snapshot. Training uses the most recent 500 retained decision records; this is bounded memory, not unlimited retention.
- **Rehearsal plan:** saves proposed symbol, size, count, loss, order-type, fractional and short-selling test parameters. Rehearse inspects pending ideas and explains blockers. It does not submit orders, arm Moss or configure the existing `auto_live` path.

Moss can read desk research, market history, the paper book and cached live account state. Its write access covers its notebook, research decisions and research-ranking weights. It has no broker submission or cancellation calls. Account context is retained locally and is not inserted into its model prompt; existing market/research context goes to the configured provider when model use is enabled. Model calls and audits can incur normal provider charges.

## What learning means here

`research_learning.py` fits an actual small statistical research ranker, version `research-ranker-beta-v1`. Each cohort separates ticker, actual model, prompt version, workspace, horizon, direction and setup. It updates a Beta(2,2) prior from qualified positive versus nonpositive modeled after-cost outcomes, retains the fitted alpha/beta parameters and evidence hash, and bounds research weights to 0.5–1.5. Fewer than 20 samples keep a neutral weight; a nonpositive average outcome cannot boost a cohort above neutral.

Duplicate IDs, mock/routed results, errors, missing provenance, future outcomes and mismatched horizons are excluded. Eligible weights change research attention, not order sizing or execution. Tests exercise that weights apply to the actual default model as well as explicit model choices.

These are trained statistical parameters, not changes to the foundation language model's weights. They are descriptive training-sample evidence; no held-out trading edge, guaranteed profit or ability to earn a requested daily amount is claimed.

## Execution boundaries and manual activation

See [Manual live enablement](MANUAL_LIVE_ENABLEMENT.md) for exact environment variables, endpoint request bodies, mode confirmation and session lifecycle. No arming or real order command was executed in this implementation pass. Existing manual reviews support whole-share market and DAY limit orders. Existing automatic execution uses whole-share market orders. The current Gateway adapter does not implement fractional stock or borrowed-share entries; local paper fractions and calculator scenarios do not change that capability.

The running production Python process was left intact to preserve its broker/P&L session. Updated backend features load after a deliberate app restart. Templates detect whether the backend registered the new routes; the existing process shows a restart notice and omits the new forms/scripts, so refreshing the current desk does not produce broken new controls. `COMPANION_AVAILABLE` is an internal route-availability marker, not a user arming flag. The launcher reuses an already-running app, so opening the shortcut alone does not reload Python code. During a maintenance window, stop the existing desk process you have identified, then open **Daytrade Signal Desk** to start the updated application. Check account identity, mode, P&L and outstanding orders before resuming work. Do not terminate an unidentified process or restart with unresolved broker orders.

## UI and maintenance

The one-page layout remains. New content uses small template includes and native collapsible details. Sage/teal dark colors and warm ivory light colors live in existing CSS variables, with no animation or frontend build step. Account and real/simulated labels remain explicit. The cost calculator, notebook and glossary explain technical terms near their controls. The notebook refreshes once a minute when visible, every five seconds while researching, and pauses while hidden.

Edit `templates/desk/companion.html`, `trade_planner.html`, `notebook.html`, `static/companion.js`, or the short companion section in `static/app.css`. Backend modules are `research_companion.py`, `research_learning.py` and `trade_planner.py`. Existing paper fills remain in `app.py`. The [UI map](UI_STRUCTURE.md) lists the surrounding files.

OpenAI tooling used: Product Design audit for workflow/presentation, Browser for interaction checks, and Imagegen for the original Moss avatar. No OpenAI API integration was added. Avatar asset: `static/moss-researcher.png`. Generation prompt: square standalone avatar for a calm market research companion named Moss; small sage-green owl, thoughtful curious expression, ivory face, tiny round amber glasses, closed research notebook; matte editorial style and subtle paper texture; sage, cream and slate palette; centered and readable at 88 pixels; deep teal background #142724; no text, charts or profit symbols.

## Validation and remaining qualification

Full isolated suite: **430 passed in 52.13 seconds**. JavaScript syntax passed. Relevant cases include fee-inclusive paper budgets, fractional partial/full exits, changed sizing during a quote fetch, invalid numbers, unknown fees, zero-share estimates, training provenance/horizon filtering, actual-model ranking, holidays/early closes, restart deduplication, missing/stale history and rehearsal isolation. These tests replace broker I/O and do not qualify live execution.

After the final template/backend compatibility guard and zero-format adjustment, **37 focused companion/workspace checks passed in 2.64 seconds**. The guard was also verified against the still-running production process: it renders the restart notice and omits unavailable new controls.

Browser checks used a separate port, temporary data and blocked outbound sockets: paper sizing and routine persistence, plan save/rehearsal, fixture research/notebook, fee scenarios, stale estimate clearing, both themes and a 390-pixel layout without document overflow. No browser console errors were observed. Screenshots contain fixture data and are not market evidence.

The read-only production check retained `live_manual`, genuine daily P&L 0, ready account data, zero broker positions/pending orders/recorded fills, and unchanged configuration SHA-256 `4419247D3E339C44C563436FDAD651421A5963A2B0F4444E2DE451E8A828A41F`. The existing research session was already active; it was not started or stopped in this pass. End-to-end broker-paper order/recovery qualification and a new scheduled run during an actual market session remain unverified.
