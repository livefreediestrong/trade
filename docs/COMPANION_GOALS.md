# Companion evidence and separate trading goals

Implemented September 25, 2026. The companions remain automatic; there is no mood or activity selector.

## Evidence, speech and memory

- Nightly reports are checked on read, including historical reports. Quantitative headlines come from frozen local evidence. Known unsupported account-return, qualified-sample and profitability statements are held for review; original model text remains intact and inspectable. This is a bounded claim checker, not a general factual verifier.
- A shared investigation identifies the report, frozen evidence version, each reviewer's completion state, findings, Fox's challenge, uncertainties and unproven tests. Animation timing cannot manufacture a completed handoff.
- The evidence strip shows source freshness, research evidence and execution readiness separately. Closed markets and missing P&L appear together. “What changed / why / next condition” history deduplicates polling and reloads, retains 40 events per account/day scope and at most seven browser scopes. History is local presentation data, never execution input.
- Speech and explanations can be pinned. The current status strip remains separate; unavailable status and new critical updates interrupt pinned speech. Quiet and reduced motion preferences remain supported. The text strip remains accessible below the sidebar's 800px breakpoint.
- New decision records retain a recall receipt with lesson IDs/revisions, setup, model, horizon, cost/outcome evidence and recall time. Current corrections, withdrawal and missing records are labeled. Historical decisions without receipts are not retroactively described as having used current memory. The model gets aggregates and no more than 12 detailed examples; the full receipt remains local.
- New top-ten review rows retain signal/evidence IDs, recorded blockers, modeled reward/risk, planned invalidation and the next condition for a new review. Entry, expiry/ranking departure and verdict changes are reported separately from actual trade decisions.
- Existing portraits retain their position and identity. Blink graphics are restricted to the eye region. Expressions crossfade; research poses describe current work rather than a timer-driven conversation. No new model requests are made by the UI.

## Goals

`GET/POST /api/trading-goals` presents live and local-paper plans. Presets include observation/review only, $1/$5/$10 live targets and $10/$25/$50 simulated targets, plus a custom amount. These are examples, not financial recommendations or return forecasts.

Live targets use the existing `daily_profit_target_usd` and verified broker daily P&L. Paper targets use `paper_profit_target_usd` and the local simulation's daily realized P&L after recorded costs. The existing risk gates stop new entries when the appropriate target is reached; verified reducing exits keep their exception. Saving affects only the selected scope's target/receipt. It cannot activate trading, change account, choose a model, increase an order/loss cap or change the other scope. An edit is bound to the revision at which the user began the draft; conflicting saves fail without overwriting.

Milestones show current account/P&L readiness, unresolved broker submissions, 20 qualified paper observations, after-cost evaluation and the day's two-part review. Unknown evidence remains unknown. A sample milestone is never a trade quota or proof of profitability. No target was selected on the production account during implementation.

## Execution hardening and boundaries

Stock quote freshness now requires observed price ticks; previous close and unrelated ticks cannot establish freshness. Market data types 2 and 4 are frozen, not live. `market_time` retains the compatibility field name but `time_kind=local_receipt` and `exchange_time=null` explicitly identify its provenance. Temporary update-event handlers preserve ticks that ib_insync clears between packets. Quote subscriptions and handlers are removed on completion.

The automatic agent respects the configured quote-age limit rather than silently allowing 120 seconds for IBKR. Before new stock risk, the adapter checks a current matching account/contract quote, both bid/ask receipt ages (15 seconds), a noncrossed market, a 50 bps spread ceiling and, for market orders, sufficient displayed size. Authorization and review expiry are checked again after waiting for that quote. The owner-directed delayed-stock policy remains; a delayed quote's receipt is not evidence of a current exchange price or live depth. Limits bound execution price; Level 1 size is not a depth guarantee.

The API owner thread prioritizes cancellation and order/reconciliation tasks over queued research reads. It cannot interrupt a broker call already in progress or bypass IBKR pacing. Reconnects use bounded exponential backoff with jitter. A broker-time round trip exposes clock-skew diagnostics; a detected lower bound above five seconds blocks new risk. Unavailable clock measurements are labeled unavailable, not estimated as zero.

Existing durable intent, partial fills, execution corrections, account identity, unknown-order reconciliation and reducing-order exceptions remain authoritative. No live or broker-paper orders or cancellations were used for validation.

**Remaining capabilities and external checks:** Stock exits remain app-managed regular-session checks, not resting broker brackets; they require the PC, desk, Gateway and eligible data. Autonomous options exit management and broker-native attached protective orders are not introduced by this avatar/goals change. A local watchdog cannot survive PC or network failure. Native protective-order lifecycle work and broker-paper qualification are still necessary before describing the desk as protected while offline. Missing broker daily P&L continues to block new risk. Reddit developer approval remains an external requirement; no access restriction is bypassed.

Primary references checked during implementation: [IBKR market data types](https://interactivebrokers.github.io/tws-api/market_data_type.html), [IBKR bracket transmission](https://interactivebrokers.github.io/tws-api/bracket_order.html), [ib_insync wrapper source](https://ib-insync.readthedocs.io/_modules/ib_insync/wrapper.html).

## Validation

Focused tests exercise factual contradiction handling, original preservation, memory revisions, prompt bounds, stale/unknown progress, exact save isolation, concurrent edits, quote types and callbacks, spread/size rejection, final-preflight races, clock drift, existing order lifecycle and automation gates. Browser qualification uses an isolated data directory with outbound sockets blocked; save/reload tests cannot contact a broker. Production verification is read-only after the guarded desk restart. Passing local tests is not broker acceptance, live execution proof or evidence of profitability.

## Image provenance

Built-in image generation edited `static/companion-portraits-v2.png` into `static/companion-blink-v1.png`. The alpha channel is retained; code clips the changed sheet to the eyes so the torso does not jump. Existing artwork represents fictional app companions; no new claim of cultural authenticity is made.

Prompt: “Edit target: the supplied transparent 4-column by 2-row portrait sprite atlas. Produce an exact same-size same-layout transparent sprite atlas for blink frames. Preserve every pixel's apparent position, silhouette, color, clothing, hair, head size, ears, nose, brows and mouth expressions as closely as possible. The ONLY intentional change: all eight portraits have their eyes gently fully closed for a natural brief blink. First row the gray fox, second row the purple-haired woman. Keep each face exactly registered within the same cell. Do not add any background, shadow, grid lines, text, labels, borders or extra elements. Real transparent alpha. Do not change style, clothing, cultural details, light or camera. This is a momentary overlay for the existing illustrated app avatars.”
