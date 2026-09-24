# Dynamic graphics from the supplied design kit

Implemented September 23, 2026 from `Apache Trading App Logo Kit (4).zip`, using `Desktop App v3.dc.html` and `Motion Options.dc.html` as references. The package runtime, demonstration portfolio, simulated placing-order timer, and example trade stars are not part of the application.

## Effect mapping

| Package element | Application implementation |
| --- | --- |
| Dawn/day/dusk/night scene | Original six-color palettes, smoothly blended against Eastern time, three mesa depths, warm horizon glow. Manual scene choices persist locally. Sun/moon arcs are decorative, not astronomical calculations or market-session indicators. |
| Moving sky | Slow drifting clouds, faint wind ribbons, sun breathing glow, moon halo, haze and three documented star patterns containing 19 bright stars. CSS handles animation; no canvas render loop or added market polling. |
| Three painter treatments | Banded sky, Sculpted clouds, and Earth & grain. These adapt the package's banded, cumulus, and earthy/grained options without changing financial colors or data. |
| Original mark and motion | The original 24-block Whirl Tray is enlarged. The final user direction replaces the kit's fast compounding spin and entry build with continuous slow clockwise/counterclockwise turning, masked gradient flow and small breathing variations. A three-minute rotation loop stays decorative; it does not represent Moss or order status. Fox handles fresh buzz cues separately. See MOONLIT_DESK.md for exact timings. |
| Mc — Quill signs | Quill line draws and its terminal dot lands; wordmark fades in. The quill appears beside Trade constellation and signs again when opened. |
| Md — Day in the sky | User-triggered 12-second preview in Scene & motion. Returns to the saved sky, and stops on pause, reduced motion, or a hidden page. Preview never changes the real clock, session, or execution settings. |
| Me — Constellation | Expandable Settings & journal graphic from the existing read-only retained broker journal. One selectable star per verified real execution from today in Eastern time. Partial fills are separate. Dotted path follows record order, not equity, price, or P&L. Last 24 fills shown; journal timestamp and limited coverage are explicit. |
| Dynamic companions | Existing Fox, Changing Woman, and Both options, poses and movement controls remain available. Their movement control is separate from scenery. |

The constellation excludes paper/unknown-mode, unverified, superseded, duplicate, future-dated, previous-Eastern-day, and malformed records. Fees remain unknown when unreported. No fills means no trade stars; decorative background stars have no financial meaning. Every displayed record is inserted as text, never HTML.

## Editing and operation

- `static/nadzeel_motion.js`: palette/time blending, deterministic scenery, appearance controls and read-only journal projection. Pure helpers export in Node for tests; the browser uses existing `moss:state` / `moss:journal` events. No fetch, order endpoint, trading configuration, or extra dependency.
- `static/nadzeel_motion.css`: animation speeds, layer opacity, landscape treatments and responsive behavior. Keep the celestial elements faint and mobile navigation backed by a panel for legibility.
- `templates/desk/header.html`: decorative layers (`aria-hidden`, no pointer events).
- `templates/index.html`: compact Scene & motion controls and animated mark.
- `templates/desk/scene_notes.html`: keyboard-accessible constellation disclosure and selectable fill buttons.
- `static/desk_layout.css` and `templates/desk/brand_mark.html`: calm logo choreography, masked swirl light, compact working layout and time-matched wildlife.
- `static/companion.js`: emits the already-fetched retained journal to the graphic; no extra broker call.

Scene preferences use only the `nadzeel_*` local appearance keys. Motion follows the system reduced-motion preference, can be paused manually, suspends in hidden pages, and resumes on visibility/bfcache restore. Mobile uses fewer cloud/star/wind elements. Fox remains the default avatar. The application and Gateway do not need a restart for these template/static changes.

## Validation

`node tests/test_nadzeel_motion.cjs` exercises Eastern time and DST, palette blending, real-fill eligibility, corrected/duplicate records, empty histories, safe record text, selection/focus persistence, preview expiry, reduced motion, hidden-page suspension and bfcache recovery. `node tests/test_moss_avatar.cjs` checks the existing companion behavior.

Focused existing Python UI/companion/research tests passed (65 tests). Browser interactions on the running desk verified scene/treatment choices, visibly changing cloud transforms, exact frozen transforms while paused, preview return, honest empty constellation, and a 390-pixel layout with no horizontal overflow. Populated trade interaction is tested offline; no real order was placed to manufacture a populated visual.

Source recovery archives include `.cjs` tests as well as the graphics. Runtime credentials, trading configuration, account state and journal data remain excluded.
