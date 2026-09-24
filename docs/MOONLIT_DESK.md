# Moonlit desk presentation

September 24, 2026. Changes use the existing Flask/Jinja templates and vanilla CSS/JavaScript. No application or Gateway restart is needed.

## Logo

The mark retains all 24 blocks, the central disc, outer ring and accent block from the user's `Apache Trading App Logo Kit (4).zip` (`Mark.dc.html`). `templates/desk/brand_mark.html` renders that geometry inline. It is 80 px on desktop and 50 px on narrow screens. The fox perch sits below the enlarged mark.

The final direction supersedes the earlier quick startup and the kit's accelerating Mb demonstration. The user requested slow, relaxing, satisfying variations, with light moving up and down the swirls and continuous clockwise/counterclockwise rotation. `static/desk_layout.css` controls the motion:

| Time | Motion |
| --- | --- |
| 0–90 s | One full, slow clockwise turn. |
| 90–180 s | One full, slow counterclockwise turn back to the original orientation, then repeat. |
| Every 28 s | Soft gradient light travels from the inner swirl blocks to the tips and back. |
| Every 30 s | A slight breathing pulse: 1.5%, then 2.5%, then 1.5%, in a repeating 90-second sequence. |

The gradient is masked to the actual swirl blocks, with eased reversals and feathered edges. Its intensity moves slowly between brighter flow, softer orbit and warm breathing variations over three minutes. The turning circle never enters a stationary chapter; it eases into each reversal, and the cycle ends at its starting orientation without a snap. The wordmark and surrounding layout do not move. No strobe, startup rush, fake trade signal, completion flash or order-status animation is present. The motif is decorative.

Quiet desk and Gentle scene motion pause both the outer motion and inner light at their current phase. The existing controller also pauses when hidden. System reduced motion keeps a static highlighted mark. The entire cycle uses CSS, without an additional timer or rendering loop.

## Working layout

Trade sections come first: overview, live, options, paper, then Moss/research. Navigation groups those destinations under Trade, Research and Desk. The overview reports real data availability and offers an account-check link. The stock workflow guide is expandable. Existing order/risk controls and their IDs are retained. The trade journal map and scene sources are under Settings & journal. Long decorative headings and repeated explanations were reduced.

## Time-of-day environment

The background adds faint drifting haze, a moon halo and occasional wildlife. The selected or rotating decorative sky controls both wildlife and Changing Woman's activity:

| Sky | Companion activity | Background wildlife |
| --- | --- | --- |
| Dawn | Preparing water | Ground-foraging quail |
| Day | Working plant fibers | A slowly circling hawk |
| Dusk | Sorting gathered food | A foraging jackrabbit |
| Night | Resting in a plain wrap | A small bat |

These are stylized, historically inspired everyday activities, not a verified reconstruction of a particular Aravaipa community in the 1500s. Source distinctions remain visible in the scene disclosure. Sources for the broad activities include [NPS on Nde daily life and basketry](https://www.nps.gov/articles/apache.htm) and the [Mescalero Apache Tribe's own culture account](https://mescaleroapachetribe.com/our-culture/); the latter is not an Aravaipa-specific source.

Wildlife inspiration uses [NPS Saguaro birds](https://home.nps.gov/sagu/learn/nature/birds.htm), [NPS jackrabbit natural history](https://www.nps.gov/whsa/learn/nature/black-tailed-jackrabbit.htm), and [NPS animal activity patterns](https://www.nps.gov/arch/learn/nature/animals.htm). The broader canyon landscape is informed by [BLM's Aravaipa description](https://www.blm.gov/visit/aravaipa-canyon-wilderness). The animated scene is not a wildlife observation or a live sky calculation.

Animals are faint, intermittent, decorative and noninteractive. Only the matching sky layer animates. Quiet desk, scene pause, hidden pages and reduced-motion preferences are respected. Both companions remain on their sidebar perches; small frame changes crossfade slowly.

## Editing locations

- `static/desk_layout.css`: compact spacing, logo timings, haze and wildlife motion.
- `templates/desk/brand_mark.html`: original logo geometry and its masked gradient.
- `templates/desk/fauna.html`: original vector wildlife silhouettes.
- `static/moss_avatar.js` and `.css`: time-of-day activity mapping, pause behavior and sprite frames.
- `templates/desk/scene_notes.html`: source disclosure and retained journal map.
- `docs/CHANGING_WOMAN_DAY_ROUTINE_ASSET.md`: asset generation provenance and exact prompts.

The presentation uses existing data events. It does not arm live trading, send/cancel orders, change broker settings or manufacture account values.

## Fox buzz cues

Fresh buzz alerts from the existing spike/deduplication logic dispatch a cancelable `moss:buzz` presentation event. When Fox is visible and available, he presents an 18-second source-labeled bubble beside his upper perch and a small 12-second listening tilt. Changing Woman does not deliver these cues. An accepted cue suppresses the duplicate generic toast and sound. A global three-minute companion cooldown prevents a burst of reactions; the normal buzz panels remain available throughout.

The companion rejects missing, invalid, future or older-than-seven-minute cache timestamps, stale snapshots and mock/demo sources. The source's normal cache lifetime is seven minutes. Quiet desk, typing, a dialog, requested guidance, a hidden/narrow sidebar, or a woman-only appearance leaves the existing generic alert path in charge. Reduced motion and Still perches allow the information bubble without movement. No additional feed polling or trade decision is introduced.

Offline tests exercise this event boundary and the companion's suppression/expiry rules. A real fresh spike is not fabricated on the running desk to demonstrate the effect.

## Final presentation checks

Four focused JavaScript suites passed: scene, avatar, Fox buzz integration and workflow guidance. Existing companion/workspace Python suites passed 37 tests. The later Wolfram review added eleven reference cases; the math-reference and paper-workday suites passed 40 tests, with one existing event-loop deprecation warning. Details are in WOLFRAM_MATH_AUDIT.md.

Running-browser checks verified exact frozen logo transforms under scene pause and Quiet desk, resume, all four sky/activity mappings, matching wildlife layers, account-check navigation, expanded buy/sell guidance, no duplicate IDs and no horizontal page overflow at 390 px. The enlarged logo and fox have separate space at desktop and 600 px viewport height. The Wolfram disclosure opens and remains usable at narrow width. Fox's buzz trigger is exercised offline; no current feed spike was fabricated as a browser demonstration.

At final inspection Moss reported waiting for market, with no qualified workday evaluation available. Broker connection status was unverified. Those states remain visible; the mathematical checks do not certify a working live feed or a profitable strategy. Scene rotation and gentle motion are on; Quiet desk is off. No broker process restart or trading-setting change was made by this work.
