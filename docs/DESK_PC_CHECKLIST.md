# Desk PC verification checklist

Items from the September 2026 review that the build sandbox could not check. Run them on the Windows desk PC, with IB Gateway on the **paper** account, before trusting them with the real account.

## Launcher and Gateway

- [ ] Desktop shortcut starts the desk under the default Windows script policy (`Launch.vbs` / `Launch.bat` pass `-ExecutionPolicy Bypass`).
- [ ] After a `git pull` with the desk running, the page shows the restart notice and the shortcut offers to restart. The restart is refused while a broker order is unresolved.
- [ ] Closing the Gateway login window before signing in keeps it closed (no relaunch every few minutes). **Ensure Gateway** reopens it.
- [ ] A signed-in Gateway that exits is relaunched once after about 90 seconds, only while a live session is active.
- [ ] Gateway started by IBC (`java.exe` / `javaw.exe`) is recognized as running; no second Gateway starts.

## Live agent protective exits (paper account first)

- [ ] An agent buy fill appears under **Protecting:** with entry, stop and target.
- [ ] A move below the stop sends one sell limit below the bid; the row disappears after the fill.
- [ ] After a 1R gain the stop shows "(at entry)".
- [ ] With `flatten_before_close_min` = 10, agent positions are sold in the last 10 minutes of the session.
- [ ] A position sold by hand in TWS stops being managed on the next cycle.
- [ ] Delayed IBKR market data (no real-time subscription) holds exits instead of selling on stale prices. If exits never fire, check the market data subscription.

## Entry guards

- [ ] **Settings → Entry & profit guards** saves and reloads the three values.
- [ ] A thin setup shows WATCH with "Entry blocked: after round-trip costs…" in its text.
- [ ] After a profitable morning, giving back half of the peak pauses new broker risk with "Protecting today's gains", while sells still go through.

## Market watch and X

- [ ] Research → Market watch lists Google News headlines, Google search trends, Yahoo and Stocktwits trending.
- [ ] With `X_BEARER_TOKEN` set, X posts load and the status line shows posts read today and this month. If the plan lacks the `$` operator, the status says so and switches to keyword search.
- [ ] With Reddit OAuth keys set, each subreddit shows "connected" in Source status.

## Optional integrations

- [ ] `ALERT_WEBHOOK_URL` pointed at a Zapier Catch Hook receives an alert (for example a daily goal) with `event.kind` and `event.message`.
- [ ] With `SENTRY_DSN` set and `sentry-sdk[flask]` installed, a test error reaches Sentry without account numbers, request bodies or local variables.
