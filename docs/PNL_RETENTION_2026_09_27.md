# Retained P&L subscription recovery

Installed September 27 at 00:23 ET as source `1463bca1157f8ffb`, PID 18160.
Saved controls remained byte-for-byte unchanged. A verified 30-store state backup
and fresh zero-holdings/zero-working-orders broker snapshot preceded the normal
orderly stop/launch. Gateway stayed running; watchdog and upkeep were restored.
At 00:32:26 a real numeric P&L callback arrived, and at 00:32:54 the desk reported
`risk_ready=true`. No new upstream-loss/restoration cycle occurred after this
installation, so this recovery does not itself validate the new retention window.

## Reproduced defect and repair

The installed September 26 adapter measured a silent-subscription retry from the
original request time. After a long-lived connection received 1100/2110 followed
by 1102, the next account read could immediately cancel the retained subscription
or run an already-pending soft reconnect. Production traces show a replacement
four seconds after the 23:58 restoration and a soft reconnect 37 seconds after
the 00:15 restoration. Both traces also contain a P&L callback immediately before
the restored notice; those callbacks were correctly excluded during recovery.

The repair gives only the same active connection's retained subscriptions a full
180-second callback window after actual upstream restoration. It does not rewrite
request age, callback age, risk day or the P&L amount. Duplicate 1102 messages do
not extend the window. 1101 resynchronization, 2100 account-feed loss, disconnected
clients and unrelated connections take precedence. Existing backoff still applies.
When the window expires without a usable callback, bounded retries resume.

An update received during the outage is never promoted into a post-recovery value.
New risk remains blocked until a usable callback arrives after restoration and
the independent API/account checks pass. Account-window unrealized/realized P&L
is not substituted for portfolio Daily P&L.

The API now reports `recovery_wait_seconds` separately from actual subscription
age and gives a plain-language explanation while the window is active.

## Evidence and limits

Before the fix, the new tests reproduced immediate cancellation after both 1100
and 2110 restoration, plus premature execution of a pending soft reconnect.
After the fix, 189 relevant checks pass. Five official-SDK diagnostic tests skip
because the normal runtime does not include the separate `ibapi` probe dependency.
The actual installed `ib_async` decoder is exercised using framed messages in the
observed order; it emits no cancellation during the window, rejects the outage
value, and accepts a later valid callback on the same subscription. Tests do not
connect to Gateway or submit broker orders.

This fixes a reproducible early-retry defect, not the entire missing-P&L problem.
Real retained-subscription recovery and repeated reconnect endurance remain G01.
If Gateway emits no later P&L callback, the app must continue to report unavailable.

Official sources checked September 27, 2026:
- https://interactivebrokers.github.io/tws-api/message_codes.html — 1101 means data
  lost; 1102 means data maintained. It does not establish freshness of a callback.
- https://interactivebrokers.github.io/tws-api/pnl.html — account-window and
  portfolio P&L differ in source, update/reset schedules; required Gateway setting.

## Installation and rollback

Install only the validated three-file manifest `pnl-retention-20260927.json`, using
the normal orderly shutdown/launcher after a verified backup and fresh flat/no
working-order broker check. Preserve saved controls and schedules. The manifest
records exact before/after hashes and its installer saves a source rollback ZIP.
Revert the changed adapter only through another orderly maintenance stop if new
API exceptions or retry starvation appear. Never restore live state or replay
orders to undo this source change. Runtime observation is recorded separately.
