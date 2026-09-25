# Calm, expressive companions

September 25, 2026. This supersedes the sprite-animation and preference descriptions in SIDEBAR_HABITAT.md; the stationary top/bottom positions and Apache-country art direction remain.

## Presentation

Both characters use one aligned four-expression portrait atlas, `static/companion-portraits-v2.png`. Faces are larger than the former full-body sprites and remain at a fixed scale and baseline. Attentive, thoughtful, cautious and speaking portraits correspond to the current action. The existing action controller still selects research, reconciliation, waiting, news, chores and shared listening from recorded desk state. Props are kept below the face, and a listening scene does not carry a research chart.

Two transparent layers crossfade in opposite directions over 1.8 seconds; switching an activity no longer resets both layers or moves the torso vertically. Gentle breathing moves one pixel over 12 seconds, and prop motion is slower. Speaking is a finite expression beat, not a looping mouth. Blocked, reconciling and unavailable states use the cautious face. Quiet desk, reduced motion, typing, dialogs, hovering and hidden-page handling remain supported. The narrow-screen layout continues to hide sidebar companions rather than overlay trading controls.

Routine speech remains readable for at least 18 seconds, with longer text receiving up to 32 seconds. The ordinary speaker cadence is 45 seconds. New order updates, blockers and lost data remain immediate. The speech card has stable geometry across topics, so news no longer moves the navigation. Speech separates a principle, the actual observation, a next research question, and provenance. News headlines retain their source and publication date. Outages are described explicitly instead of falling back to reassuring generic guidance.

Scenery preferences live inside the collapsed **Appearance** disclosure. The longer companion explanation and speech setting are also collapsed on the Paper page. Quiet desk remains directly accessible; no manual action or expression selector was added.

## Roles and autonomous information

Fox is selective, accountable and concise: protect capital, require a reason for the trade, account for costs, define invalidation, wait for broker reconciliation and treat passing as a decision. Changing Woman is observant, curious and gently skeptical: verify provenance and timing, separate facts from implications, seek contrary evidence, track uncertainty and keep useful notes. These are app-written personalities, not claims about a real person's beliefs or cultural teachings.

The existing background research service checks its scheduler every 30 seconds and refreshes the ten publisher feeds on their five-minute cache interval while the app runs. This release exposes the current verified source count, headline count, refresh interval and busy state through the existing read-only desk-day response. It does not launch a fetch or paid model call from avatar rendering. Stale, future-dated and malformed cached entries cannot be called current in this view.

Headline commentary now adds a concrete research lens and next check: earnings guidance/cash flow, macro release revisions/expectations, effective versus proposed policy, deal conditions, technology demand, energy exposures, duration and yield changes, or the downside/cost of chasing a move. It is rule-based commentary on a headline, not a claim to have read a full article, an independent corroboration, a model forecast or an execution signal. The configured broker agent continues its existing autonomous evaluations with its saved policy and budgets. More screen movement or more model requests would not establish an edge.

## Research used and follow-on experiments

- [W3C Pause, Stop, Hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html) and [Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html) inform restrained motion, stable reading time and retained pause controls. The particular timings above are design choices, not timings prescribed by WCAG. This is not a complete accessibility-conformance claim.
- [CME's trading-plan guidance](https://www.cmegroup.com/education/courses/building-a-trade-plan/trading-strategies-in-your-trade-plan) supports explicit entry/exit criteria, defined risk and decisions prepared before emotional price movement. No example position size or strategy from that page was copied into live policy.
- [FINRA's frequent-trading discussion](https://www.finra.org/investors/insights/frequent-intraday-trading) identifies trading costs and the risk of frequent short-term activity. The implementation therefore emphasizes selectivity and after-cost evaluation rather than portraying activity as profitability.

The next useful profitability research is empirical: freeze a candidate rule and data cutoff; compare it with the existing baseline on an untouched later period; include spread, fees and slippage; report drawdown, sample size and uncertainty as well as average returns. Record rejected ideas and losing setups, not only wins. Compare whether a source or filter adds incremental value after costs, and retain negative results. These are proposed experiments, not backtests performed by this UI change. No risk caps, trading permissions, order rules, model budgets or strategy thresholds changed.

## Asset provenance

Built-in image-generation tool, identity-preserving reference generation, September 25. References were the existing `static/moss-fox-actions-v1.png` and `static/changing-woman-expressions-v1.png`. Original output remains in the Codex generated-images directory; a versioned copy is tracked in the project. No existing reference asset was overwritten.

Prompt: Create a 2:1 atlas with exactly four equal columns and two equal rows. Top row: four chest-up Fox portraits preserving gray/orange fur, amber eyes, green field jacket and turquoise pendant. Bottom row: four chest-up Changing Woman portraits preserving warm brown skin, adult face, purple-black hair and bangs, turquoise forehead pendant, cream clothing with blue/red trim and blue-white chestpiece. Refined painterly game illustration with smooth edges and readable eyes. Identical scale, face center and eye baseline within each row. Expressions left to right: calm attentive; focused thoughtful; cautious questioning; speaking warmly. Faces occupy about 45 percent of each tile, with heads fully inside. Transparent background; no hands over faces, props, tile borders, text, watermarks or added sacred/ceremonial/tribal symbols. Readable at 120 pixels per tile.

## Validation scope

Focused Python tests cover cached-source freshness, malformed/unavailable data, busy status, existing desk-day behavior and page routing. JavaScript tests execute the real controller with an isolated clock, covering expression mapping, crossfade state, repeated snapshot stability, message reading holds, urgent interruption, attribution, source links, stale-state handling, reduced motion, lifecycle and the prohibition on avatar network calls. Browser checks inspect actual faces, both themes, a changed caution state, the Appearance disclosure and a 390-pixel viewport. No broker action is used as a test; these changes do not establish live execution readiness or profitability.
