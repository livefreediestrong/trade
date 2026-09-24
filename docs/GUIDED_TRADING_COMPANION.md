# Purposeful companion and guided trading interface

Implemented September 23, 2026. Frontend/template changes use the existing Flask/Jinja and vanilla JS style. No broker process restart, live submission, cancellation, arming, or trading configuration change is required.

## Companion behavior

The current controller keeps Fox near the top of the left sidebar and Changing Woman near the bottom on cozy desert perches, with occasional hair/fur breeze frames. Both are shown by default on migration; later user choices persist. See [SIDEBAR_HABITAT.md](SIDEBAR_HABITAT.md) for the current design, motion and source details. Earlier bounded walking and cliff-climbing layouts have been replaced.

Speech bubbles name the destination and explain the section or focused field. Dismissal persists for that message; a checkbox can turn bubbles off entirely. Appearance settings remain local (`moss_appearance_v1`). Hidden pages stop the controller, and pageshow restores it. No new model or network call is used for guidance.

## Live stock workflow

`templates/desk/live_guide.html` and `static/desk_guidance.js` provide Buy shares / Sell shares I own paths. The status line reads actual existing desk state and treats stale data, an unverified market session/account, unavailable daily P&L and a non-manual mode as incomplete. It never labels a connection as permission to trade.

Buying: account checks → whole-share cost/fee calculator → existing research ticket review → recorded broker orders. Navigation opens the relevant disclosure but never clicks a trade action. The calculator initially uses whole shares and a limit-price assumption; fractional paper estimates remain selectable. Ticket buttons now say Review trade to distinguish opening a review from final submission. Existing account-bound acknowledgement and submission gates remain unchanged.

Selling an existing holding: check the exact position/quantity → use IBKR's own close/sell workflow → reconcile the fill. The desk has no direct position-close ticket; the guide says this explicitly instead of presenting a research sell signal as a close action. Selling beyond a long holding can create short exposure. No new live execution capability was added.

## Calls and puts

`templates/desk/options_guide.html`, `templates/desk/options.html` and `static/options.js` explain buying calls/puts, closing owned contracts, protected credit spreads and debit spreads. Opening contract fields, paper sizing, preview and confirmation have numbered stages. Expiration, strike, premium, the standard 100-share multiplier, bid/ask and expiry break-even are distinguished.

Choosing Sell an option I already bought hides the opening ticket and invalidates any previous review. Existing paper positions provide the exact-contract close preview. Closing labels now reverse the actual held legs: sell to close the long leg and buy to close the short leg. The review emphasizes dollars, modeled loss including assumed round-trip fees, expiry break-even, and blockers; Greeks are secondary. Stale async responses, input changes and expired reviews cannot retain a confirmable ticket. Duplicate clicks do not reuse a consumed review.

Options remain an isolated paper book, using verified broker quotes when available. Long calls/puts and the existing vertical spreads are supported. Naked short options, live options orders and exercise/assignment simulation are unavailable. Actual American-style short options can be assigned early; modeled spread loss does not simulate the resulting stock obligations.

Explanations checked against [OIC option basics](https://www.optionseducation.org/optionsoverview/what-is-an-option), [OIC assignment](https://www.optionseducation.org/referencelibrary/faq/options-assignment), and [SEC order types](https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders).

## Checks

- `node tests/test_moss_avatar.cjs`: stable perches, finite breeze frames, paused typing, dialogs, dismissal, hidden/narrow/reduced-motion states, both avatars and page recovery.
- `node tests/test_desk_guidance.cjs`: stale/unknown/closed/non-manual/broker-paper state handling and option intent distinctions.
- `node tests/test_options_ui.cjs`: real frontend controller with isolated paper responses; stale preview rejection, invalidation, correct closing action/cost labels, expiry, missing quotes, and duplicate-click prevention. All non-options URLs rejected in the harness.
- Existing options, workspace UI and companion Python suite: 84 passed; one existing event-loop deprecation warning from eventkit.
- Existing design graphics controller checks continue to pass.

Browser verification covers the buy/sell and call/put/credit-spread/close selectors, paper-only confirmation state, navigational disclosures, speech, responsive layout and paused motion. Filled option review and close behavior are tested offline; no real-money transaction is used as validation.
