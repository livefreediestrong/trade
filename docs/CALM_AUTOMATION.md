# Sidebar companions and calm paper controls

September 23, 2026. Vanilla JS/Jinja additions; no dependencies, broker restart, strategy change, live arming or live execution.

## Movement and attention

Avatars and speech belong to the left navigation. The current accepted design uses stationary top/bottom cozy perches with an occasional sprite breeze; it replaces the bounded walking/climbing experiment. See [SIDEBAR_HABITAT.md](SIDEBAR_HABITAT.md) for current motion, pause, cultural-source and astronomy details.

`desk_attention.js/.css` adds a persistent local Quiet desk preference and a saved next-task destination. The button opens ancestor disclosures and focuses the selected section. Quiet desk pauses scenery and avatars, softens the background, and suppresses unsolicited speech without changing saved character/scene preferences. Explicit guidance still appears. Broker/market/risk information and automation remain active and visible. This is an attention preference within the existing full desk, not the deferred Simple mode.

Design references: [W3C Limit Interruptions](https://www.w3.org/WAI/WCAG2/supplemental/patterns/o5p01-minimal-interruptions/) and [Let Users Control When the Content Moves or Changes](https://www.w3.org/WAI/WCAG2/supplemental/patterns/o8p01-motion/). These inform user control and reduced distraction; this release does not claim a clinical ADHD benefit or a complete accessibility audit.

## Paper automation

`paper_workday_ui.js` and `paper_automation.html` show current operational state, a next step, saved entry amounts, daily guardrails, today's activity, last result and a freshness stamp. The existing companion status poll is reused at 15 seconds (5 seconds when busy); no second polling feed was added. Older background responses cannot overwrite state after a control action. Status older than 45 seconds is visibly unknown; stale screens never imply automation has stopped.

Pause immediately requests a freshly read policy with enabled=false through the existing paper-only settings endpoint. It preserves current saved limits. It stops new automated paper entries, including revocation during in-flight checks via existing backend guards. Due exits and outcome grading continue. It neither liquidates positions nor cancels broker orders, and other notebook/journal routines remain separate.

Starting requires a review of the currently saved policy, including symbol scope, entry size, exposure, positions, losses, recorded API budget and schedule. There is no review countdown: a fresh read is compared with the reviewed settings at the final click. Changed limits require another review. Duplicate clicks are blocked, requests time out without automatic retries, and an uncertain save requires checking status. Existing backend paper risk limits, latency checks, data-quality gates, holidays and early closes remain authoritative. Existing options paper tickets are still manually reviewed; live automation is not armed.

This uses the existing full-policy save endpoint. Concurrent configuration edits from another browser in the narrow interval between the fresh read and save are not an atomic compare-and-set; avoid simultaneous policy editing. No backend protocol or restart was introduced in this presentation/controls release.

## Validation

- `node tests/test_moss_avatar.cjs`: actual controller with an isolated clock: stable perches, finite breeze and crossfade layers, typing/hover/dialog/quiet/reduced-motion pauses, speech, both characters and hidden-page recovery.
- `node tests/test_paper_workday_ui.cjs`: actual controller with isolated HTTP responses; saved-limit review, fresh comparisons, no rushed deadline, preserved pause limits, changed settings, duplicate clicks, uncertain timeout, stale state and old response rejection. It asserts only companion/paper routes are used.
- Existing guidance, options UI and scene-motion Node suites passed.
- `python -m pytest tests/test_moss_workday.py tests/test_companion.py tests/test_workspace_ui.py -q`: focused worker, API and UI checks. These prohibit network/broker writes where the isolation fixtures apply.
- Running browser: both sprites are absolute descendants of the sidebar and fit its bounds; none exist under main. Quiet desk pauses scene and speech but leaves Waiting for market visible; next-task navigation focuses its target; 390px layout has no horizontal overflow and no floating avatar.

Browser checks do not submit a paper configuration or a live order. The source/API tests verify control behavior in isolation; this is not proof of future strategy profitability or live execution readiness.
