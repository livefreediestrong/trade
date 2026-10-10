# Reddit connection, memory and unattended recovery

September 25, 2026. These changes preserve the saved trading policy, account selection and model budgets.

## Reddit

The connection panel reports missing credentials, ready, connected, error or cooldown separately. A token is not proof of a successful source read. This installation had no REDDIT_CLIENT_ID or REDDIT_CLIENT_SECRET at review; a Reddit browser login and another authorized app cannot provide this app's credentials.

Obtain approved external Data API access and register your own client through Reddit's current process. Put REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in the existing local .env, plus an accurate REDDIT_USERNAME/User-Agent contact. REDDIT_REFRESH_TOKEN is supported for an existing read-only OAuth grant to that client. Restart using the normal launcher after changing credentials. Do not paste credentials into a chat or reuse a different application's tokens.

The client automatically renews expiring access tokens, renews once after a 401, applies one shared cooldown across Reddit consumers, respects Retry-After and rate-window reset headers, and backs off on temporary errors. A denial never triggers anonymous fallback. An explicitly configured legacy public reader remains supported. Successful identical requests share a 60-second cache (bounded to 32 responses); returned objects are independent copies. Cached evidence and its age remain separate from connection status. Provider errors omit raw exception bodies and tokens.

The buzz API returns immediately with cached evidence and starts one background refresh. A forced refresh does not bypass Reddit's cooldown. Fresh disk caches retain their watchlist key after a process restart.

Sources: [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), [Data API terms](https://redditinc.com/policies/data-api-terms).

## Devvit alternative

[Devvit](https://developers.reddit.com/docs/) builds apps hosted inside Reddit and handles authentication for their Reddit capability. The [FAQ](https://developers.reddit.com/docs/guides/faq) explicitly distinguishes external scripts/sites from this flow. It is an option for a separate community-installed companion, subject to installation and review, not a credential source for the local Flask app. No Devvit app was uploaded, installed or granted account permissions during this repair. Community Live Chat is not connected by this existing post/comment reader.

## Memory and speed

The decision ring and lessons now retain up to 5,000 records (unresolved decision horizons retain the existing extra protection). The companion uses the newest records, not the tail of a newest-first list. Notebook status reuses a summary for 30 seconds when the file is unchanged; a correction, file replacement or corruption marker invalidates the summary. Capacity increases apply to future records; already expired records are not recreated.

A corrected lesson replaces the old vote for the same decision ID and keeps up to three prior numeric revisions. Repeating the same result is idempotent. Raw and modeled after-cost outcomes, model/prompt/horizon/workspace provenance and exclusions remain distinct. The ranker uses the newest duplicate, including a withdrawn outcome, and never revives an older qualified score. A 100% confidence input is included in the 90–100 band; it is not evidence of a certain return.

Unchanged lesson files use a bounded cache that returns copies and invalidates on file changes. Invalid JSON/structures and non-finite numbers cannot silently replace memory. The decision ring preserves an unreadable original plus a recovery copy and refuses writes. Compact JSON reduces retained decision serialization and disk size. No foundation-model weights, live policy thresholds or risk limits are changed by this learning.

## Recovery

The existing Windows watchdog runs every five minutes. Health now reports the scan and companion schedulers. Its local-only recovery endpoint requires the current process ID and restarts only missing workers; healthy workers and explicit stop flags are preserved. Stored-evidence corruption stops automatic recovery rather than resetting counters or trading history.

For updated source code only, the watchdog may request an automatic restart. The running app refuses while the trading session is active, an agent cycle is running, an order is unresolved, broker holdings/orders are present or unverified, or evidence needs recovery. It rechecks conditions under the submission lock before exit. The watchdog observes process departure before invoking the hidden normal launcher, which maintains its own single-instance checks. Attempts have a ten-minute cooldown. A Reddit outage, missing Daily P&L or an arbitrary provider error does not cause a restart loop. Hung but still-running workers are not duplicated or forcibly killed.

Recovery retries known operations; it does not rewrite source code, install updates, change permissions, reset the ledger, clear financial guardrails or replay order submission. Broker login/2FA and external provider approval remain external dependencies.

## Validation boundaries

Regression tests exercise token renewal, cooldown and resumption, denied-access isolation, credential redaction, shared caching, immediate buzz responses, correction/reload behavior, corrupt-file preservation, stale evidence exclusion, dead-worker repair, and restart refusal/rechecks. All provider and broker mutations in these tests are intercepted. Actual Reddit OAuth success requires approved credentials and remains unverified on this installation. No broker order is submitted as a test.
