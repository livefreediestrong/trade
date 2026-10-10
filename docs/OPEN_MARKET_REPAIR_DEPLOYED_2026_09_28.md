# Live-market repair deployment

Checked 2026-09-28T15:26:51.465664+00:00. Source `7d1aac87710c18d9`, PID 20512.

All 35 package files match the tested candidate. Config and credentials hashes unchanged: True / True. Live Fox enabled: True; mode: auto_live; active session: True. No missing workers or corrupt stores were reported. Both maintenance tasks were restored enabled/Ready after installation.

Broker connected: True; equity: $110.0; Daily P&L: None; risk-ready: False. Latest read: 0 holdings, 0 working orders. No test orders/cancellations were submitted. A working connection is not proof of order execution or stop protection.

Automatic real-money options entries are held; stock automation's saved controls remain on. New stock entries still require all existing checks, including Daily P&L and live bid/ask data. At $110 equity, the existing high preset implies $4.40 per stock position / option-order maximum loss and a $4.40 daily-loss entry stop, with at most 12 agent attempts. The UI shows dollar limits as unavailable while the required broker-risk data is unverified. Whole-share requirements mean many stocks cannot fit this budget.

Massive historical bars responded; snapshot access was denied. No entitlement or purchase was invented. Direct Reddit approval and X billing remain external blockers; independent WSB summaries remain available. Unsupported symbols now have a durable one-hour cooldown; that recovery behavior was exercised in unit tests, not asserted as an observed live missing-symbol cycle today.

Validation: 572 Python checks, six controller suites, actual read-only preview and installed Fox views at requested 1341px and 390px widths, no page overflow or captured JavaScript errors in these checks. The installed Research page displayed the feed distinctions. Narrow visual checks do not certify every app page. Broker-paper protective stop/options lifecycle and assignment qualification remain pending.

The verified 31-store state archive is state-20260928T152053-e39a1acc.zip. Source rollback: C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs\maintenance-source-rollback-20260928T152206Z.zip. Saved runtime evidence and source manifest are in the chat output directory. The post-restart P&L timeline distinguishes an API-responsive connection from a missing broker callback; old P&L is never carried forward as current evidence. Owner assistance was requested for a normal Gateway restart because native Gateway controls are unavailable in this session.

Implementation details: [open-market repairs](OPEN_MARKET_REPAIR_2026_09_28.md). Broader pending work remains in TASKS.md.
