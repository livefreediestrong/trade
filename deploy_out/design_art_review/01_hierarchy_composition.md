# Design/Art Review 01 — Visual Hierarchy & Composition

**Lens:** Focal points · sticky stage weight · type scale · breathing room · teal/gold accent discipline · Simple vs Advanced separation · competing heroes  
**Surfaces:** `templates/index.html`, `static/app.css` (One Job / stage / news / sheet + legacy Simple packs), `deploy_out/notes_simple_ab_live.md`, `static/simple_one_job_mock.html`  
**Date:** 2026-09-22 (ET)  
**Scope:** Art direction only — no product code changes.

---

## Verdict: **mixed**

The One Job spine is real: sticky stage, confidence ring, spoken Why, quiet Waiting empty, collapsed curve/pos chips, Settings sheet, and deliberate kill of desk-pulse / trade-glow as Simple heroes. Teal/gold on chill slate matches the mock’s art direction and Advanced stays denser without inheriting the stage theater.

What keeps it from **strong** is cascade residue and competing stickies. Legacy “poster bloom” rules still clamp and bloom the same `#loop-big-action` that the new `.call-spoken` grid owns; session toolbar sticks *above* the stage; chrome-wave + `body::after` + `.stage-ambient` are three ambients; Hold paints the hero in **gold**, the same token used for meta (butler label, race pace, macro, Waiting chrome). Live type scale undershoots the mock’s ring word and overcooks nested card chrome. Fix the stack conflicts and accent roles and this becomes a clean strong pass.

---

## Findings (ranked)

### P0 — Dual sticky: session toolbar outranks the One Job stage

**Evidence**

- `body.ui-simple #session-toolbar` — `position: sticky; top: var(--masthead-h); z-index: 55` (`app.css` ~1745–1748)
- `body.ui-simple .stage-wrap` — `position: sticky; top: calc(var(--masthead-h) + 0.25rem); z-index: 35` (`app.css` ~4718–4722)
- Stage top offset ignores toolbar height; toolbar z beats stage.

**Why it hurts**  
On scroll, Start / fill-mode / cash chrome sits on top of the call. The product story (“one job = latest call”) loses the sticky hero to a control strip. Notes already flag `--masthead-h` sensitivity; the live bug is **two stickies fighting**, not just masthead height.

**Art direction**  
Pick one sticky hero for Simple:

1. **Preferred:** Keep `#one-job-stage` sticky; make `#session-toolbar` *static* (or collapse into a thin non-sticky strip once session is live). Stage `top` = masthead only.  
2. **Alt:** Keep toolbar sticky; stage sticky with `top: calc(var(--masthead-h) + var(--session-toolbar-h))` and `z-index` above content but below toolbar — measure toolbar height in the existing `syncStickyOffsets` path.  
Never leave stage at z-35 under a z-55 bar with a top that assumes no toolbar.

---

### P0 — Legacy poster-bloom CSS fights the new call grid (nested hero)

**Evidence**

- HTML: `#loop-big-action` is both `.call-spoken` and `.big-action` (`index.html` ~212)
- Legacy Simple poster pack still applies to `.big-action`:
  - `max-width: 22rem; width: 100%; margin: 0.15rem auto; min-height: 7.75rem` + radial bloom + aurora (`app.css` ~3423–3451)
  - Older clamps: `max-width: 18rem` (~1736–1738)
- New stage intends a full-width spoken card inside `.stage-wrap` (`app.css` ~4804–4814: grid `auto 1fr`, blur panel)
- Mock’s `.call-spoken` spans the stage; ring at ~5.6rem; **no** 22rem centered poster (`simple_one_job_mock.html` ~207–250)

**Why it hurts**  
Ambient stage frame + centered 22rem bloom card + conf-ring + spoken column = **hero inside hero**. Breathing room collapses into a narrow column; edges of the sticky stage feel empty while the card feels cramped. Aurora + stage-ambient double the wash.

**Art direction**

Under `body.ui-simple .call-spoken` (or `.stage-wrap .big-action`):

- `max-width: none; width: 100%; margin: 0; min-height: 0`
- Kill or zero `.action-aurora` opacity when `.conf-ring` is present
- Let `.call-spoken` own one surface (current rgba + teal border); do not stack the old poster radial/box-shadow recipes on the same node
- Leave Advanced’s denser `.big-action` path alone (`body.ui-advanced .call-spoken` already resets display/border)

---

### P0 — Type scale cascade: three sizes for one word; meta still speaks

**Evidence**

| Rule | Size / weight | Location |
|------|---------------|----------|
| `body.ui-simple .big-action .action-word` | **1.75rem** | ~1740 |
| `body.ui-simple .big-action .action-word` | **2.15rem / 800** + text-shadow | ~3465–3470 |
| `body.ui-simple .call-spoken .action-word` | **1.28rem / 750** (wins by source order) | ~4858–4859 |
| Mock `.action-word` | **1.35rem / 750** | mock ~275–276 |
| Mock `.spoken-line` | **1.05rem** | mock ~291–292 |
| Live `.spoken-line` | **1.02rem** | ~4869–4870 |

Also:

- `.action-gloss` correctly killed on `.call-spoken` (`display: none !important` ~4897)
- **`#loop-action-meta` has no Simple hide** — still styled at ~3472–3475 while `#spoken-line` / `#spoken-why` / `#spoken-news-match` are the intended voice (`index.html` ~224–228)

**Why it hurts**  
Ring word is smaller than the mock and smaller than the abandoned 2.15rem poster hero — confidence ring looks like a badge, not a call. `action-meta` duplicates spoken copy under Why → noise in the focal column. Dead 1.75 / 2.15 rules are landmines for the next CSS edit.

**Art direction**

1. Single Simple type ladder (align to mock, then live polish):
   - Ring word: **1.35–1.4rem / 750** (mock)
   - Spoken line: **1.05–1.08rem / 500**
   - Why: **0.84rem** muted (live 0.82 is slightly tight — bump to mock 0.84)
   - Eyebrow / conf-label: **0.65–0.68rem** uppercase gold/muted
2. `body.ui-simple .call-spoken .action-meta { display: none !important; }` (mirror gloss)
3. Delete or gate the 1.75 / 2.15 poster word rules so they cannot override `.call-spoken`

---

### P1 — Triple ambient (chrome-wave + page wash + stage ambient)

**Evidence**

- Notes: “One ambient behind stage”; Simple hides pulse/glow (`notes_simple_ab_live.md` §A.4; `app.css` ~4937–4939)
- Still live:
  - `.chrome-wave.simple-only` — 4px drifting teal/sage/gold bar + glow (~3391–3416, enriched ~4452–4465)
  - `body.ui-simple::after` — fixed bottom 42% radial wash (~4467–4478)
  - `.stage-ambient` — stage radial + grid drift (`--ambient-drift`) (~4734–4760)

**Why it hurts**  
Three soft heroes compete for “mood.” Wave under masthead + page foot wash pull the eye away from the sticky call; gold/teal appear in all three so accent discipline blurs.

**Art direction**

- **Keep** `.stage-ambient` only (idle/live/AH classes already earn their keep)
- Demote chrome-wave to a **static 2px** hairline, no animation, opacity ≤0.5 — or remove in Simple
- Remove or opacity-gate `body.ui-simple::after` while One Job ships (Advanced can keep denser mood later if needed)
- Reduced-motion already stops stage drift — extend that to chrome-wave if wave stays

---

### P1 — Gold role collision: Hold hero = meta gold

**Evidence**

- Tokens: `--motion-teal: #6aadc8; --motion-gold: #c9a86c` (~4664–4665)
- Hold word: `body.ui-simple .call-spoken.hold .action-word { color: var(--motion-gold); }` (~4861)
- Same gold on: butler label (~4689–4691), race pace mark (~4791–4793), spoken eyebrow (~4865–4867), Waiting tray border (~4948–4951), macro strip / `.news-src` (~5096–5101, ~5135–5137), Lucky spark (~5062–5065)
- Teal used well for: ring, spoken `em`, how-call summary, news symbols, pos chip (~4872, ~4885, ~5021–5024, ~5124–5128)
- PAPER badge still uses older sage (`~1783–1786`), not gold — mock uses gold for paper (~mock 67)

**Why it hurts**  
Hold is the default idle call. Painting it gold makes the desk feel “celebratory meta” all day and flattens Waiting / macro / butler labels (gold meant *secondary accent*). Teal then has to carry both brand and “pay attention.”

**Art direction**

- **Hold** → cool slate/silver (`#a8b4c4` or `--muted` +1 step), ring stays teal — Hold = calm, not precious
- **Gold** reserved for: butler label, goal race pace, Waiting *when pending*, Lucky, PAPER honesty, sheet section labels
- **Buy/Sell** keep `--motion-buy` / `--motion-sell` (already correct)
- Optional: align PAPER badge to gold (mock) *or* keep sage — pick one honesty color, don’t invent a third

---

### P1 — Competing below-stage heroes: News always “on,” Waiting only when needed

**Evidence**

- Waiting: gold tray when active; `is-empty-quiet` strips chrome (~4948–4969) — excellent
- News: always `border-radius: 14px; border: 1px solid rgba(106,173,200,0.16); background: rgba(20,28,38,0.55)` (~5077–5080)
- DOM order: stage → curve strip → Waiting → News (`index.html` ~310–459)
- Macro strip inside News adds a **gold** band (~5093–5097)
- Simple caps rows at 5 (`nth-child(n+6)` ~5146) — good density control, not hierarchy

**Why it hurts**  
Empty Waiting correctly whispers; News still presents as a full panel. With macro gold + teal news rows, “What’s moving the tape” can out-rank Waiting when both matter, or feel like a second stage when Waiting is quiet.

**Art direction**

- When news feed empty: mirror Waiting — dashed quiet empty, no teal panel border (butler line only)
- When Waiting `has-pending`: slightly raise Waiting (border 0.28–0.32 gold, optional soft inset); keep News one step quieter (border alpha ≤0.12, no macro gold fill unless calendar is hot)
- Cap Simple news visual weight: title row + ≤5 rows; no second butler voice competing with header/stage (prefer empty `#news-butler` when stage already carries catalyst via `#spoken-news-match`)

---

### P2 — Topbar density vs butler card focal

**Evidence**

- Brand cluster + butler card (`index.html` ~54–67; card ~4672–4695)
- Tools: Simple/Advanced, How to, Feeling Lucky, ⚙, Old mock (`~69–78`)
- Hero stats correctly hidden in Simple (`~4945`) — good

**Why it hurts**  
Butler card is the right secondary focal, but five chrome controls on the right flatten the topbar into a button farm. “Old mock” especially reads as prod chrome.

**Art direction**

- Keep: mode toggle, ⚙, Lucky (gold outline on hover only — mock’s `.btn.gold-outline`)
- Demote How to + Old mock to ghost text / footer or Settings sheet “Debug” section
- Cap butler card `max-width` (~26rem is fine); don’t let tool wrap push butler below the fold on mid widths — prefer tools wrapping under brand, butler sticky to brand row

---

### P2 — Stage padding / breathing room vs mock

**Evidence**

- Live `.stage` padding `0.75rem 0.9rem`, gap `0.65rem` (~4761–4764)
- Mock `--stage-pad: 1rem 1.15rem` (mock ~24, ~207–209)
- Live `.call-spoken` padding `0.75rem 0.9rem` (~4809) inside already-padded stage → double inset when poster clamp removed
- Goal race thin track (5px) is correctly subordinate (~4776–4778)

**Art direction**

- Stage pad → **1rem 1.1rem** (mock)
- After killing poster max-width: reduce inner `.call-spoken` pad slightly (**0.65rem 0.75rem**) so ring + spoken breathe against stage ambient, not against nested padding
- Keep goal race first, one thin row — do not enlarge into a second hero meter (topbar heroes already removed)

---

### P2 — Simple vs Advanced separation is mostly clean (keep; one cleanup)

**Evidence (good)**

- Pulse/glow `display: none` in Simple (~4937–4939)
- Loop head / strip / prefs home hidden in Simple (~4940–4944); prefs portal to sheet
- Advanced: stage-wrap transparent, ambient off, spoken/how-call hidden (~4725–4732, ~4899–4903)
- Curve/pos toggles Simple-only; Advanced forces open bodies (~5006–5007, ~5031–5032)
- Advanced keeps denser panels + subtle live border accents (~5148–5157)

**Residue**

- Large blocks of `body.ui-simple .trade-glow-*` / `.desk-pulse-*` (~3658–3701, ~4486–4510) still style panels that Simple never shows — not a user-visible bug, but invites future “turn it back on” hierarchy regressions

**Art direction**  
Leave Advanced denser. Optionally quarantine dead Simple pulse/glow rules behind a comment block or delete once One Job is canonical — don’t re-enable those panels in Simple.

---

## What to keep

1. **Sticky One Job stage** concept + shadow weight (`box-shadow: 0 8px 28px…`) — correct hero treatment once sticky stack is fixed  
2. **Confidence ring + spoken column** grid — strongest composition idea vs old centered poster word  
3. **Waiting `is-empty-quiet`** — best breathing-room pattern in the desk; model News after it  
4. **Collapsed Day curve / Positions chips** — correct demotion of secondary viz  
5. **Teal/gold token pair** on chill slate; no Pendleton/wool — matches mock and notes  
6. **Settings sheet** (fixed right, backdrop) — keeps friction/alerts out of the stage  
7. **Simple kill of desk-pulse + trade-glow + hero-stats** — stops competing canvas heroes  
8. **Advanced path** that strips ambient/spoken and keeps probs / feed / pulse / glow  
9. **DeskMotion reduced-motion + AH ambient desat** — craft without casino spam  
10. **`#spoken-news-match` under Why** — catalyst belongs with the call, not only in the News panel  

---

## Concrete art direction changes (checklist)

| # | Change | Target |
|---|--------|--------|
| 1 | Resolve dual sticky: one hero (stage preferred); sync `top`/`z-index`/`--session-toolbar-h` | `#session-toolbar`, `#one-job-stage` |
| 2 | Neutral Simple `.call-spoken` / `.big-action`: no `max-width: 22rem`, no auto margin, no aurora when ring exists | `.call-spoken`, `.action-aurora` |
| 3 | One type ladder: ring **1.35–1.4rem**, spoken **1.05rem**, why **0.84rem**; hide `.action-meta` in Simple | `.action-word`, `.spoken-*`, `.action-meta` |
| 4 | Retire or gate poster word rules (1.75 / 2.15) so they cannot override One Job | legacy Simple packs ~1740, ~3465 |
| 5 | Single ambient = `.stage-ambient`; demote/remove chrome-wave animation + `body::after` wash | `.chrome-wave`, `body.ui-simple::after` |
| 6 | Hold ≠ gold; gold = meta only (butler, pace, Waiting pending, Lucky, honesty) | `.call-spoken.hold .action-word` |
| 7 | News empty = Waiting quiet pattern; when Waiting pending, News one step quieter | `#news-panel`, `#opp-panel` |
| 8 | Stage pad → mock-like `1rem 1.1rem`; tighten inner spoken pad after full-width fix | `.stage`, `.call-spoken` |
| 9 | Thin topbar: Lucky/⚙/mode stay; How to + Old mock demoted | `.topbar-chrome-tools` |
| 10 | Do not resurrect pulse/glow in Simple; prune dead CSS when convenient | `#desk-pulse-panel`, `#trade-glow-panel` |

---

## Focal map (intended after fixes)

```
[ PAPER masthead — honesty, not hero ]
[ Brand + butler card ]     [ mode · Lucky · ⚙ ]
[ Session controls — not sticky, or sticky only pre-Start ]
━━━━━━━━ sticky stage (sole ambient) ━━━━━━━━
  thin goal race
  [ conf ring | spoken line + Why ]
━━━━━━━━ end stage ━━━━━━━━
[ Waiting tray — loud only when count > 0 ]
[ Day curve chip ] [ Positions chip ]
[ News — quiet empty / compact when filled ]
[ disclaimer ]
```

Advanced keeps multi-panel density; motion accents only — no Simple stage theater.

---

*Agent 1 — Visual Hierarchy & Composition. No product files modified.*
