# Options exits, changed holdings and practical limit drafts

**Current status, September 27:** installed in the September 26 combined maintenance
release. Actual broker-paper execution and genuine settlement evidence remain open;
the separate paper login is not confirmed available. The [qualification runbook](BROKER_PAPER_RUNBOOK_2026_09_27.md)
distinguishes local fixtures, broker simulation and actual statements. The dated
preparation notes below are historical; their not-installed statements are superseded.

Later staged continuation: [local option activity statement review](OPTION_ACTIVITY_2026_09_26.md) adds a limited CSV evidence importer, duplicate/conflict handling and account/contract lookup. It does not yet complete assignment settlement accounting or qualify actual broker behavior.

September 26, 2026. Implemented in the separate maintenance candidate, **not installed**. The owner's latest instruction is **do not turn live trading off**. No running settings, Fox activation, session controls or dollar limits were changed. The earlier answer selecting live-off was superseded by that correction. Existing live controls remain available; this change adds an optional IBKR paper-account test for the new options lifecycle.

## What the new option test does

The optional **automatic options exits** setting defaults off. With a verified IBKR paper account, an active Fox session and this setting enabled, the agent records the exact contracts it owns before sending an entry. It supports one managed option position at a time: a standard 100-share single option or a two-leg vertical spread. It does not adopt older or manually opened options. Existing options or working orders on the same underlying prevent adoption of a new position.

Confirmed fills determine the remaining contracts, including partial fills. Entry and exit evidence survives restarts and normal signal-history trimming. A lost submission response remains unresolved until the existing broker reconciliation path resolves it. The agent does not blindly submit the same exit again.

The default exit test seeks to close at a 25% loss or 50% gain in the option premium, or 30 minutes before the close of the trading session one session before expiry. Those percentages are **premium changes before fees**, not maximum account losses. Short-premium strategies reverse the gain/loss calculation. The saved policy for each position remains attached to that position. Closing uses all remaining tracked contracts and a DAY limit order. Limits intended to prevent new entries do not reduce the close quantity or block an otherwise verified protective exit merely because the daily entry budget was reached.

The app requires current executable bid/ask receipts for every leg, exact contracts, live rather than delayed data, an explicit non-halted status, acceptable spread and sufficient displayed size. Both quote sides must have current timestamps. It rechecks account, policy, holdings and working orders before submission. Stale or incomplete evidence produces an explanation instead of a trade. A limit order can remain unfilled; these checks cannot guarantee an exit before expiry.

This is app-managed exit logic. It needs the app, PC and Gateway available and follows the existing active-session and market-hours gates. It does not create broker-held option stops. Stopping Fox stops its automatic exit submissions too. A previously enabled policy can persist through restart under the existing rules; no setting here starts it automatically for the owner.

## What happens when holdings change

While a tracked position exists, the app checks options and stock on that underlying, including while Fox is paused or the market is closed. A missing leg, partial disappearance, adjusted contract, unexpected stock change or corrected fill creates a persistent review hold. New risk cannot pass that hold; matching protected actions also require consistent evidence. The hold does not rewrite the enabled/session switches.

The message says **possible exercise, assignment, expiry or external trade**. Holdings alone cannot prove which event occurred. Assignment can affect part of a short position and can occur before expiry; the broker can also take its own expiry-risk actions. See [OIC assignment guidance](https://www.optionseducation.org/referencelibrary/faq/options-assignment) and [IBKR delivery, exercise and expiration actions](https://brokerage.ibkr.com/en/trading/delivery-exercise-actions.php).

**Options being watched** shows the position, check time and reason for review. To record an external closure in this paper test:

1. The owner pauses Fox and ends the session. The review action does not do this on their behalf.
2. Resolve any remaining options, stock and working orders for that underlying through the broker, then inspect its activity statement.
3. Request current broker evidence in the desk. It must report no remaining holdings or orders for that underlying.
4. Add the broker activity date/reference, acknowledge the evidence and save within 90 seconds.

Saving rereads broker evidence and compares the exact account, settings, fills and review state. A changed, expired or reused review is rejected. Successful review clears only that discrepancy, retains original contract/fill evidence and records a dated owner acknowledgement. It does not fabricate fills, realized profit/loss, assignment costs or an independently verified event cause. Later changed fill evidence requires review again. Mixed holdings and assigned stock still held at the broker are unsupported by this closure acknowledgement.

## Practical limits remain drafts

**Prepare practical limits** provides two starting drafts:

| Draft | New-order amount | Daily loss stop | Broker attempts/day | Options |
| --- | --- | --- | --- | --- |
| Paper option test | $100 | $200 | 10 | One contract; long call/put; new exit test enabled in the draft |
| Small stock test | $10 | $2 | 3 | New automatic options and their exit test disabled in the draft |

Clicking either draft only fills the unsaved form. It makes no save, start or pause request. The ordinary **Save policy** action retains its existing behavior, including pausing Fox; it is not invoked by a draft button. No dollar choice has been accepted or applied during this work. The installed order/day-loss/attempt caps remain unchanged.

These are test examples, not a recommendation to raise the owner's risk. A small budget may buy no whole share or option contract, and the app must report that rather than raise the budget. Fees, slippage, gaps, liquidity and assignment exposure can exceed a quoted premium or daily loss threshold. Protective exits count as attempts but may bypass the new-entry attempt cap under the existing reducing-order rules.

## Validation and remaining work

- **470 Python tests passed in 54.01 seconds** across 14 related suites. The new lifecycle suite exercises entry/exit through the desk's submission code with isolated files and intercepted broker transport. Cases cover partial fills, pending submissions, account/settings races, stale or mismatched prices, spread/short closes, holiday deadlines, contract changes, corrected receipts, corrupted records and review expiration/reuse.
- The option-review, live-agent and stock-stop-review JavaScript controller suites passed. They verify explicit acknowledgement, unsaved draft preservation, absence of activation requests, and no automatic retry after an uncertain save response. New modules passed pyflakes; changed scripts passed syntax checks.
- A real browser exercised sample-data review, acknowledgement, save and reload, plus both risk drafts. The requested 1341-pixel viewport rendered at 1342 CSS pixels; a 390-pixel phone viewport was also checked. No page overflow or captured console warnings/errors appeared. The preview deliberately rejected unrelated routes, including the favicon. Screenshots are in task outputs. The preview's outgoing network connections and broker submissions were blocked.
- No real or broker-paper orders, cancellations, assignments or exercise instructions were sent. No production process or dependency was changed. The preview server was stopped and its browser size override removed.

Still required: controlled IBKR paper-account execution and restart/reconnect acceptance; real assignment/expiry activity import and full settlement accounting; any later live qualification; the owner's practical dollar-limit choice. Passing software tests and a connected P&L feed do not establish those outcomes. Earlier P&L and stock-stop candidates remain included in the combined maintenance patch, with their original qualification boundaries.
