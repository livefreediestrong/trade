# Account balances and the short-option funding check

This revision distinguishes cash, settled cash, buying power, available funds and
excess liquidity. These are different broker values, not amounts to add together.
It repairs an existing short-option buying-power check; it does not add permission
to trade options, change limits, activate Fox or certify a margin calculation.

## Confirmed problems

Eleven new regressions failed against the installed source before correction.
The short-option check selected values only by tag, ignoring account and currency.
Nonfinite/unset values could pass the comparison. A numeric zero could fall back
to available funds, and an absent buying-power field could use that other balance.
Conflicting duplicates used the last row. The account view converted absent
buying power to zero, omitted settled cash, and accepted conflicting equity rows.
The submission boundary in these reproductions was intercepted; no broker order
was submitted. Not every failing check represents an observed live-account event.

## Revised behavior

- Select the exact account and USD currency. Other accounts and currencies cannot
  supply a missing amount. Net liquidation must establish an unambiguous positive
  USD base-currency equity, including after reconnect.
- Keep zero and negative reported values. Missing, conflicting, malformed,
  Boolean, nonfinite and IBKR unset-sentinel values remain unknown with a reason.
  Identical duplicate amounts are not added together.
- Missing optional cash fields do not discard otherwise valid equity or Daily
  P&L. P&L validity remains a separate existing requirement.
- The naked-short-option floor check requires actual selected-account USD buying
  power. It never substitutes available funds or another balance. The resulting
  order receipt retains the observed buying power and compared risk floor; all
  existing final identity/P&L/authorization checks still run before dispatch.
- A naked call's strike-based floor is not its maximum loss. Passing this check
  does not establish settled cash, reserve outstanding orders/fees, verify trading
  permission, or qualify full portfolio margin.
- Fox's live-workspace concentration section adds a collapsed plain-language balance panel.
  Zero, missing, negative and conflicting values remain distinguishable. Late or
  undated app observations are hidden rather than presented as current.
- The timestamp is explicitly the local account-cache read time. The broker's
  economic value-update time is unavailable; a new read does not invent it.
  IBKR base-currency summary figures do not establish per-currency settled funds.

## Validation and remaining evidence

190 focused Python checks across 11 broker/options/P&L/account suites passed.
Two frontend suites passed, including missing source metadata, stale/future reads,
safe text, negative/unknown/zero values and timer expiry. The actual installed
ib_async socket decoder replayed split account-summary frames without opening a
socket. Existing P&L retained-subscription and outage checks remain passing.

An initial combined run exposed a test-only router monkeypatch leaking across
test cases; the new test now patches the selected adapter instead, and the combined
run passes. The isolated browser preview exercised the real new parser and panel
with labeled fixtures at requested width 1341 (reported 1342) and 390, with no
horizontal overflow or console errors. Keyboard disclosure worked and stayed open
through periodic refresh. It does not prove that a broker has reported these
sample balances or that a paper/live order is qualified.

Actual broker-paper order qualification, genuine settlement reconciliation,
per-currency cash commitments, account-wide correlated exposure and practical
dollar-policy choices remain on TASKS.md. Account summary cache freshness and
broker margin/permission checks require further work; this revision does not
silently promote summary reads to those guarantees.

## Primary documentation checked September 27

- [IBKR account-value definitions](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/account-value-keys)
- [IBKR account configuration and cash requirements](https://www.interactivebrokers.com/en/accounts/configuring-your-account.php)
- [IBKR account-summary callback reference](https://interactivebrokers.github.io/tws-api/interfaceIBApi_1_1EWrapper.html) (legacy reference for the SettledCash callback definition)

Rollback should restore source only from the verified pre-install source archive
through normal stopped-desk maintenance, preserving current ledger and controls.
Do not restore trading state or replay orders to roll back this source change.

## Installation evidence

Installed September 27 at 01:21 ET, startup source `0f06310f805657b2`, PID 31896.
A subsequent template-only caption correction changed the served version to
`e500a07239951dd4` without another backend reload. Buying power is now described
as broker-calculated purchasing capacity instead of cash available to trade.
All nine original files and that separate template addendum matched their receipts.
The fresh 30-store state backup and source rollback were verified; saved controls
and limits retained their exact checksum. Watchdog/upkeep schedules were restored.

The installed API and actual desktop/mobile page showed IBKR-reported total cash,
settled cash, buying power, available funds and excess liquidity, each $110 at
this observation. These are overlapping balance measures, not $550 in money.
No broker value-update time was supplied. No order was sent or canceled.
At 01:25:23 ET Gateway/API responded, all workers were present and no corrupt
store was reported, but Daily P&L remained unknown with zero callbacks after one
retry on this connection. G01 is still open. Detailed receipts and screenshots
are under the task workspace's `outputs/account-funds-*` files.
