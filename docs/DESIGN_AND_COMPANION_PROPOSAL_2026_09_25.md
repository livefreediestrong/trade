# A clearer trading desk, changeable themes and companion chat

**Approved September 26, 2026.** Original proposal retained below. Current delivery and remaining scope: [implementation record](EXPERIENCE_AND_WEEKLY_REVIEW_2026_09_26.md).

This is a proposed next set of changes. The interactive preview uses invented example data and prepared replies. It does not connect to the trading app, a broker or an AI service. This review changes no trading settings.

My recommendation is to keep the working app and its six main pages, make the language easier to understand, separate its appearance from its operation, and add a conversation panel for Fox and Changing Woman. Build on the recent account-first layout. Improve the longer Fox and Research pages in smaller steps.

I reviewed the current project, its open-work list and the public product/help pages for all 17 services you named. This is a review of documented features and public interface examples, not a hands-on test of 17 signed-in accounts, their execution quality or their profitability. Features vary by country, account and device. I interpreted “well fargo” as Wells Fargo/WellsTrade and “TradeVision” as tradevision.io.

## 1. Keep the interface; change the theme

Yes. Think of the interface as the arrangement of the rooms, and the theme as the paint and furnishings. Changing the theme should leave the pages, buttons, charts, chat, saved research and trading rules in the same places.

| Keep as part of the working interface | Make independently changeable |
| --- | --- |
| Page navigation and the account overview | Colours, decorative backgrounds and borders |
| Tables, charts, labels and readable spacing | Decorative artwork and display fonts |
| The real-money/practice distinction | Desert scenery or a plain background |
| Fox and Changing Woman's roles, conversation and memory | Whether their portraits are shown |
| Order review, account checks and trading limits | Light or dark appearance |
| Clear warnings, keyboard controls and mobile layout | Gentle movement or still images |

The first release should offer **Plain** and **Current desert** themes, each with light/dark appearance. Keep the current theme selected for existing users. A separate “Show portraits” setting can hide artwork while keeping the companions available by name. A future theme can add a different look without rewriting the app or changing how either companion thinks.

This needs some careful work. `static/nadzeel.css` and `static/moss_avatar.css` currently contain both artwork and functional layout rules. Simply removing them would also remove some spacing and navigation behaviour. The header template also contains scenery directly. We should move the shared layout into the existing layout styles, then keep each theme's colours and artwork separate. Keep Flask/Jinja and the current JavaScript; this does not require a new framework.

Themes must not change the meaning of warning colours, hide real-money status, change button actions or clear a partly written message. Unknown or broken themes should fall back to the readable plain appearance. Installable theme packs can come later, using reviewed colours and assets rather than arbitrary scripts.

## 2. An interface that answers ordinary questions

The main screen should answer these questions immediately: **How is my account doing? Is real-money trading on? Does anything need my attention? What are the companions working on?**

| Page | Plain-language purpose | Proposed organisation |
| --- | --- | --- |
| Overview | “What needs my attention?” | Account value, today's profit/loss, operating status, next useful step, then a short market summary. Preserve the recent layout. |
| Fox | “What is Fox considering or doing?” | Summary, ideas under review, open investments and exits, decisions/history. Put his top ten ideas here with a reason and next check for each. |
| Practice | “What happens with pretend money?” | Rename the visible Paper tab to Practice; retain “paper trading” in its explanation. Separate practice results, goals and tests from the real account. |
| Research | “What do we know about this stock?” | One selected company across chart, news, company check, evidence and chat. Move broad market discovery, the calendar and learning history into clearly labelled subviews. |
| Review a trade | “What exactly would this order do?” | Rename the visible Ticket tab. Show the exact account, buy/sell direction, amount, estimated costs and exit coverage before the existing review step. |
| Settings | “How is the app set up?” | Connections, trading rules, AI spending, saved information, backups and appearance. Keep detailed logs expandable. |

Use a stable layout first. Offer a few saved arrangements later, such as Overview, Research and Review. A freely draggable wall of widgets would add complexity before the simpler arrangement is proven useful.

On desktop, keep the main work on the left and a compact companion area on the right. Open chat by expanding that area. At narrower widths, use a clearly labelled Chat button and a panel with its own close control. On a phone, chat should use a full-height panel with a reachable composer and a visible account/mode label. It must return you to the same place when closed.

## 3. Talk with Fox and Changing Woman

There is already an “Ask Gemini” panel. The model helper can accept a conversation, but `app.py:api_llm_chat` currently passes only the latest message. That means the visible chat history does not give Gemini the earlier turns. It also supplies optional stock analysis, rather than a complete, clearly scoped view of the current page and companion role.

The proposed chat builds on that existing path:

- **Fox:** concise and disciplined. Explains a decision, expected costs, trade size, exit coverage and what would change his view. His automated decisions remain within the app's trading rules.
- **Changing Woman:** thoughtful and curious. Explains news, checks the source, compares earlier observations and identifies missing or contradictory evidence. Avoid repetitive catchphrases.
- **Ask both:** request two views of the same question. Show disagreements honestly. Clearly label two perspectives generated by the same model; they are not independent confirmation. Use this only when requested so every question does not automatically cost twice as much.

Choosing who receives a message does not select an avatar's mood or activity. Their visible behaviour should still follow actual work in the app.

Useful starter questions include “Why did you skip this stock?”, “Explain this number”, “What changed since yesterday?”, “What could go wrong?”, and “What did we learn today?” A short answer comes first, with evidence and technical detail available underneath.

Each answer should show:

1. **What I can see:** recorded facts, their time and whether they come from the real account or practice.
2. **What it means:** a clearly labelled interpretation, including uncertainty.
3. **What I would check next:** one useful next step.

Source links should come from records actually supplied to the model. The app should reject invented source IDs and show when it cannot verify a claim. “No recent information” must remain different from “nothing happened.” A source's publication time, the app's fetch time and the current account snapshot time should not be confused.

Start with explanations and research. A request to buy or sell can lead to the existing **reviewable trade draft**; a chat answer must not submit an order, change risk limits or claim that an order filled. Show “Draft prepared” only after an actual draft exists. A conversational request to remember a risk preference is not permission to change a trading limit.

### Memory and cost

Retain a bounded conversation history so follow-up questions work. Keep “this conversation” separate from “saved facts and preferences.” Let you inspect, correct, forget and export saved information. Corrections need to update later recall, not just hide text on screen. A summary must retain source dates and important uncertainty, and must not turn a repeated opinion into an established fact.

Use local code for account arithmetic, status and known definitions. Continue using the configured Gemini service for open-ended explanations within the existing spending limits. Show the provider, when a request uses AI, the estimated cost and remaining allowance. A second companion's model call must reserve its own cost. No silent switch to another paid service when one fails.

Local calculations are not a local language model. Fully offline, free-form conversation would require a separately installed local model and testing on this PC. That is a later option, not something the existing local calculations already provide. A helpful offline fallback can still explain recorded facts and show saved answers, with their original dates.

### Example wording — illustrative, not a current account claim

**You:** “Fox, why aren't you trading?”

**Fox:** “Real-money trading is switched off. I can still review ideas. Before it is enabled, we also need to finish testing how exit orders behave when the app or broker connection stops.”

**You:** “Changing Woman, what did we learn today?”

**Changing Woman:** “In this example, three practice ideas were reviewed. That is too little evidence to call the method reliable. I would compare them with earlier days and include the estimated trading costs. Open the saved review to see each example.”

## 4. What the 17 app reviews suggest

The last column is my proposed application of the source, not a claim that the vendor recommends it. These are design references, not a proposal to move your account or connect all 17 services.

| App and official source | Useful documented feature | What to adopt here |
| --- | --- | --- |
| [Betterment](https://www.betterment.com/mobile-app) | Account overview, linked-account picture, goal progress and performance. | Calm account-first summary and understandable goal progress. Keep deposits separate from investment gains. |
| [moomoo](https://www.moomoo.com/us/learn/detail-how-to-use-moomoo-ai-116922-250925021) | Stock briefs before/after the market session and summaries of announcements and reports. | “What changed?” and earnings summaries, with original sources and dates. Existing nightly reviews are a foundation; predictive marketing is not performance evidence. |
| [Webull](https://www.webull.com/trading-platforms) | Desktop, web and mobile tools with charting and practice capabilities that differ by platform. | A focused research workspace, saved views and obvious practice mode. Test the actual phone experience instead of assuming desktop parity. |
| [Vanguard](https://investor.vanguard.com/client-benefits/account-conveniences) | Straightforward account management, investment tracking and recurring transactions. | Clear account labels, easy history access and less distraction on Overview. This is a useful simplicity reference rather than an active-trading template. |
| [Fidelity Trader+](https://www.fidelity.com/learning-center/trading-investing/trading-platforms/how-to-use-trader-plus-web) | Selecting a position or watchlist symbol updates related chart, quote and news tools. | Carry one selected stock through research and companion questions; label when a chart or source still belongs to the previous stock. |
| [IBKR Desktop](https://www.interactivebrokers.com/campus/traders-insight/ibkr-toolbox/ibkr-toolbox-creating-custom-layouts-in-the-ibkr-desktop/) | Preset/custom layouts and linked groups of tools. | A few task-based saved arrangements and clear order/account visibility. Keep the existing broker integration; this does not add another one. |
| [SoFi](https://support.sofi.com/hc/en-us/articles/how-do-i-set-up-edit-or-cancel-a-recurring-deposit-on-my-invest-account) | Scheduled deposits with visible activity and edit/cancel details. | Show the next run, last result and pause state for our existing automated jobs. No bank transfers or new recurring purchases are proposed. |
| [Chase / J.P. Morgan](https://www.chase.com/personal/investments/online-investing/self-directed/investing-features) | Pin/filter holdings, customise columns and compare performance over selected periods. | Fewer visible columns, pinned items, balanced positive/negative research and clearly chosen comparison periods. |
| [Robinhood Legend](https://robinhood.com/us/en/support/articles/layouts-on-legend/) | Layout templates with movable/resizable panels. | Presets before unrestricted rearranging. Keep the current order review step even if the surrounding research becomes faster. |
| [Schwab / thinkorswim](https://www.schwab.com/learn/story/practice-trading-risk-free-with-papermoney) | Practice trading, detailed activity records, profit/loss review and export. | Make the existing practice tools and journal easier to find; attach the actual decision and cost assumptions to each result. Simulation is not proof of real fills. |
| [Lightyear](https://lightyear.com/en-gb/help/trading-and-investments/plans) | Named investment groups with optional automation and a review step. | Give existing goals and research groups clear names. Saving a group or goal must remain separate from enabling trading. Product availability differs by region. |
| [MEXEM](https://www.mexem.com/trading-platforms) | A simpler client portal alongside more detailed TWS tools. | Keep a simple everyday view with technical detail one step away. A different broker-branded screen would not itself fix Gateway problems. |
| [Wells Fargo / WellsTrade](https://www.wellsfargo.com/investing/wellstrade-online-brokerage/) | Portfolio tools, screeners, watchlists and research from identified providers. | A clean account/holdings view and visible research attribution. Distinguish account facts from outside commentary. |
| [Trade Republic](https://support.traderepublic.com/en-lu/768-Wie-kann-ich-einen-Sparplan-erstellen) | Savings plans with an asset, amount, schedule, next execution and management controls. | Give each existing routine a plain summary of what it does and when. Keep “save settings” and “turn on automation” separate. |
| [TradeVision](https://www.tradevision.io/) | Charts, news, watchlists, alerts and a trade tracker organised by strategy. | Link the journal to the stock, reason and later outcome. Extra market indicators need verified data and a useful purpose before adding more panels. |
| [M1](https://m1.com/invest/what-is-a-pie/) | Visual portfolio weights and target allocations. | Add an understandable concentration view: how much is in each company/sector versus a chosen limit. This is a display proposal; automatic rebalancing is a separate trading feature. |
| [Public.com](https://public.com/ai-agents/research) | Plain-English questions about stocks, markets and existing holdings. | The strongest direct reference for companion chat: a visible question, relevant account/stock context and an answer you can inspect. Licensed data availability is not inherited by our app. |

An additional technical idea from [moomoo's official tool catalogue](https://open.moomoo.com/mcp-docs/available-tools) is its separation of read permissions and trading permissions. Apply that principle to our companion chat: reading a position and submitting an order should be separate capabilities. No moomoo installation or account connection is included in this draft.

## 5. The Reddit thread

In the [thread you linked](https://www.reddit.com/r/Daytrading/comments/1t51hf0/which_ai_trading_tools_are_truly_useful_or_are/), visible comments describe AI helping with summaries, contextual second opinions and filtering/scoring ideas. One participant mentions moomoo; others promote their own products. These are anecdotes and promotional claims, not independently verified trading results. My takeaway is to prioritise research help, explainable decisions and review of past mistakes. The thread does not establish a reliably profitable bot or justify giving a chatbot unrestricted order access.

Reading this public thread for research does not connect the app's WSB feed. That feed still has its separate approved-access requirement.

## 6. Approval list

**Recommended order: A, then B, then C. Continue the existing reliability work in D, with broker-paper qualification before any real-money activation. E is optional later.** These are work batches, not promises that everything can be completed in one pass.

| ID | Proposed change | What you get | How we will check it |
| --- | --- | --- | --- |
| **A1** | Separate core layout from themes | Plain/current desert themes; light/dark independent; existing look preserved by default. | All six pages with scenery removed; same controls, values and layout; switch/reload without losing drafts or changing trading state. |
| **A2** | Plain language throughout | Clear buttons, explanations and actionable error messages. Remove inconsistent visible names such as Moss where Fox or Changing Woman is intended. | Walk through common tasks without unexplained abbreviations; verify the new wording matches actual behaviour. |
| **A3** | Finish page organisation | Focused Fox/Research subviews; visible top-ten reviews; fewer repeated panels. | Existing links, selected stocks, forms, filters and browser back still work; no missing controls. |
| **A4** | Readability and motion pass | Larger readable text, clearer focus, calmer expressions, compact appearance settings, same content on a phone. | Browser checks at 1341, 1024 and 390 px; zoom, keyboard, light/dark, contrast and reduced motion. |
| **B1** | Real companion conversation | Chat from any page; Fox, Changing Woman or both; meaningful follow-up questions. | Multi-turn questions, changed stock/account context, two browser tabs, duplicate-send handling and restart behaviour. |
| **B2** | Context and source receipts | Show the account type, stock, facts, dates and sources used in an answer. | Missing/stale facts, invented citations, contradictory sources and malicious instructions embedded in outside text. |
| **B3** | Understandable memory controls | Separate current chat from saved preferences/evidence; inspect, correct, forget and export. | Corrected/deleted information stops influencing later recall; expired facts do not silently return as current facts. |
| **B4** | Cost, failure and privacy handling | Local factual answers where possible; Gemini explanations within existing budgets; clear failure messages. | Budget exhaustion, timeouts, concurrent sends, cancelled requests and provider errors; no hidden paid fallback or leaked credentials/account IDs. |
| **C1** | One-company research view | Selecting a stock links its chart, news, company check and chat context. | Quick switches and slow responses cannot put one company's evidence under another company's name. |
| **C2** | Useful daily brief | Three things that changed, held/watched-company news and upcoming events; source/date visible. | Duplicated stories, unrelated headlines, revised announcements, timezone/holiday handling and unavailable feeds. |
| **C3** | Better decision journal and learning view | Why an idea was taken or skipped, what happened later, estimated/measured costs and what to test next. | Separate real fills, practice fills and price-only observations; preserve negative findings and “not enough evidence.” Explain differing sample-count thresholds without implying that reaching a count proves reliability. |
| **C4** | Clearer goals and concentration | Preserve existing real/practice profit targets and milestones; make progress and portfolio concentration understandable. | Deposits are not profits; targets do not increase position sizes; unknown values stay unknown; dates and account scope stay separate. |
| **C5** | One connection and routine summary | What is connected, missing or delayed; last good read; next attempt; what needs your action. | Distinguish server outage, missing credentials, access denial, exhausted budget and one-symbol gaps. Confirm settings changes do not start routines. |
| **D1** | Reliable account data (existing G01/G10) | Evidence that account updates recover through reconnects and long sessions. | Actual account read checks, controlled failure cases and sustained tests. A recovered value once is not overnight proof. |
| **D2** | Broker-held protective exits (existing G02) | Correctly tracked exit orders that remain at the broker when the app closes, where supported. | Broker-paper partial fills, child-order rejection, cancel/replace races, restart, disconnect and duplicate-event recovery. Stops still do not guarantee an execution price. |
| **D3** | Complete options lifecycle (existing G03) | Correct handling of automatic closes, expiry and assignment, with clear limitations. | Paper scenarios per contract and leg, partial fills, early assignment/exercise, expiry and recovery. Do not label the feature fully automatic beforehand. |
| **D4** | Risk and strategy qualification (existing G04/G08) | Reviewable actual limits and honest evidence of the whole strategy after costs. | Your risk-policy decision plus later-data, portfolio and execution checks; no automatic loosening of caps or promotion based on a nice-looking chart. |
| **E1** | More theme packs and saved arrangements | Additional looks using the same interface; a small set of saved workspaces. | Theme package validation, accessibility checks and safe fallback. Revisit free dragging only if presets prove insufficient. |
| **E2** | Optional local language model | Private offline free-form conversation if this PC can run it well. | Hardware assessment, real-answer quality/latency tests, clear model status and no silent cloud fallback. |
| **E3** | Additional paid/social data | Specific gaps filled when access, cost and usefulness justify it. | Approved provider access, actual current responses and dated coverage; no assumption that public app marketing includes a usable API licence. |

**Existing repairs are not reset by this proposal.** G09 backup restoration, G14 shared AI budget controls, the recent overview/status improvements, JEV's temporary lock, autonomous avatar actions and the separate real/practice goals must be preserved. G06 external access and G11/G12 data gaps remain tracked in `TASKS.md`.

## 7. Visual details to approve

- **Plain theme:** a soft neutral background, strong text contrast and one restrained accent. Use a normal readable interface font; reserve decorative type for the optional themed title.
- **Current desert theme:** keep the existing identity and artwork as an option. Place scenery behind the companion area and decorative edges; keep numbers, forms and charts on clear surfaces. A colour study in the preview is not replacement artwork.
- **Companions:** readable portraits with subtle expression changes. Keep the face in the same place during a change. The actual action label should explain “checking news” or “waiting”; do not imply continuous thought or successful trades without recorded events.
- **Chat:** one response per clear bubble, the speaker's name, sources underneath, and an expandable longer explanation. Keep typed text when the panel closes. New replies should not move the page while you are reading an older message.
- **Status:** real-money trading, practice trading, broker connection and quote freshness need distinct labels. Good connection status does not mean trading is approved. A warning must use text as well as colour.
- **Charts and tables:** keep axis labels and dates visible, sensible decimal precision and readable empty states. Let a small table scroll inside its own area if necessary, not push the entire page sideways.
- **Settings:** subtle access to appearance and detailed logs. Trading controls and urgent account problems remain easy to find.
- **No celebrations tied to profit or order submission.** Celebrate completed learning milestones only if wanted; avoid visual pressure to trade more or meet a daily profit target.

Accessibility acceptance targets: WCAG AA contrast, visible keyboard focus, practical 44 px touch targets for main controls, 200% zoom checks, reduced-motion support and informative labels independent of colour. These are acceptance targets for implementation, not a claim that the current app already passes every criterion. [WCAG 2.2](https://www.w3.org/TR/WCAG22/)

## 8. Replace jargon without losing the meaning

| Current/technical wording | Proposed visible wording |
| --- | --- |
| P&L | Profit or loss |
| Equity | Account value |
| Unrealized P&L | Current gain or loss on investments still open |
| Buying power | Broker's available trading capacity — may include borrowing |
| Positions | Open investments |
| Exposure | Total value of open investments; explain separately how much could be lost |
| Reconciliation | Check our records against the broker |
| Stale snapshot | Account information has not updated recently |
| Spread | Gap between the quoted buying and selling prices |
| Slippage | Difference between the expected and actual trade price |
| Basis points | Show percent or dollars in the main view; offer the technical unit in details |
| Calibration | How well past confidence estimates matched results |
| Walk-forward evaluation | Test on later data the method has not seen |
| Kill switch | Name the actual action, for example “Stop new trades”; say explicitly whether existing investments stay open |
| No edge / insufficient cohort | No reliable advantage shown yet / not enough comparable examples |

An error should answer **what happened, what it affects and what to do next**. Example: “Reddit is not connected. WSB discussions are missing from this view. Approved Reddit access is needed; the other news sources can still be used.” Technical details can expand beneath that sentence. Avoid raw configuration keys in the main flow.

## 9. Implementation notes for the next coding pass

Preserve current user/Claude changes. Work within `templates/pages/`, shared `templates/desk/` partials and the existing `static/` files. Keep the recent account-first layout in `page_split.css`. Extract shared layout rules incrementally from `nadzeel.css` and `moss_avatar.css`; migrate scenery markup into an optional shared partial. Keep semantic status styles in the core. Version the appearance preference and migrate the existing light/dark selection without resetting it.

Chat work starts with `app.py:api_llm_chat`, `llm_trader.py:chat`, `static/app.js:sendChat` and `templates/desk/research.html`. Add a shared companion panel rather than duplicate it on six pages. Use bounded server-owned conversation IDs and explicit role/context fields; validate message types/lengths, protect session isolation and render model text safely. Preserve the local Host/Origin guard and existing atomic model-cost reservations. Treat news and retrieved text as evidence, never as instructions to execute tools. Keep model-facing context small and scrubbed. Add sources from allowlisted record references, not arbitrary URLs supplied by generated text.

Every script must tolerate pages without its panel. Preserve element/event contracts where possible, update cache versions on all relevant templates, and retain link/keyboard behaviour. Loading chat or switching a theme must not send orders, start scans, change symbols in an execution ticket or make a paid model call on its own.

Test in layers: narrow unit tests for changed state/cost/history logic; browser interactions for the actual theme/chat controls and mobile layout; explicit real-model evaluation before calling model answers verified; broker-paper exercises for the remaining execution lifecycle. A build or attractive screenshot does not replace those checks.

## Approval choices

The recommended first approval is **A + B: clearer language, removable themes, focused page layout and companion chat**. The next product batch is **C: research, journal, goals and connection summaries**. D remains the existing work needed before calling live automation ready. E can stay on the later list.

You can approve a whole batch or individual IDs, for example “A and B, keep the desert look as my default.” Approval of this design is not an instruction to activate real-money trading, increase risk, purchase data or change broker accounts.

## What was checked for this draft

The standalone preview's JavaScript syntax and local assets were checked. In the actual browser, the Overview preview passed 12 combinations: two themes, two light/dark settings and three widths (requested 1341 px, browser reported 1342; 1024; 390). No horizontal overflow or console warnings/errors were observed. Theme changes retained the example balance and a typed draft. Hiding portraits kept chat available. Prepared replies from both companions, company selection flowing into chat, separate Practice context, the phone composer and closing/reopening a phone chat draft were exercised.

These checks establish that the preview controls work. They do not test a live language-model response, production theme migration, account-feed recovery, order execution or profitability. No production app code was edited in this draft; only this proposal and its tracker entry were added to the repo. A full production test suite was therefore not run.

Preview and evidence folder: `C:\Users\maher\Documents\Codex\2026-09-24\new-chat\outputs\design-proposal-20260925`. Open `preview.html`; the companion portrait file must remain beside it. `browser-checks.json` records the browser results.
