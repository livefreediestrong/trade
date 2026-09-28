# WSB summaries and Massive access repair — September 28, 2026

Research → Market watch now includes **WSB attention & outlook**. ApeWisdom
shows discussion counts and its prior-day comparison; Tradestie shows the
provider's outlook and comment count. Each card links to the provider.

These are separate outside samples. Counts are not added to one another or to
the desk's own dated post sample. Neither feed represents verified holdings.
Ticker recognition, underlying coverage and the services' independence are
unverified. Discussion volume is not a positive investment outlook.

Both feeds omit source-update timestamps. The page shows the actual fetch time
and says **source update time unavailable**. A successful HTTP response does not
establish fresh market information. Failed refreshes remove earlier rows from
the current display rather than displaying zero activity or neutral sentiment.

The existing Social research setting controls these reads. An optional
`COMMUNITY_FEEDS_ENABLED=0` environment setting disables the new feeds. No saved
setting is changed by installation. Requests use the documented public APIs,
with no credentials, account information or watchlist transmitted. There is no
site scraping, Discord access, Reddit access bypass, model training or automatic
trade action. These feeds are currently research-page context, not new live
decision inputs or additions to the nightly AI prompt.

Per-provider locks share overlapping requests. Successful reads cache in process
memory for 15 minutes; the Refresh button respects that period. Failure backoff,
HTTP Retry-After, denied-access cooldowns, response-size limits, numeric checks
and malformed-response handling are included. No long-term provider archive is
created. The page refreshes every five minutes while open; each provider is read
only when its cache/cooldown allows it. Provider documentation:

- https://tradestie.com/apps/reddit/api/
- https://apewisdom.io/api/

The Massive adapter keeps its existing Python module and internal provider ID
(`polygon`) for compatibility. It accepts `MASSIVE_API_KEY` first, then the
existing `POLYGON_API_KEY`, and uses the official Massive HTTPS API with the key
in an authorization header. No credentials are included in public errors.

Basic is end-of-day and excludes snapshots; Starter/Developer are delayed;
Advanced requires the corresponding live entitlement. A manually declared
`MASSIVE_STOCKS_PLAN` is explicitly **not verified**. The default shared request
allowance is five/minute, with a one-minute response cache. The optional
`MASSIVE_MAX_REQUESTS_PER_MINUTE` is bounded at 120 and Basic at five. An error
does not trigger immediate retries or a fan-out across dozens of symbols.
Snapshot denial remains visible even if a later historical-bars request works.

Real-source qualification before installation: both community adapters returned
50/100 valid records. The existing Massive key returned five historical daily
bars for AAPL and denied the snapshot endpoint. This proves that historical
access worked for that request, not the exact subscription name or live-data
entitlement. No subscription or account permission was changed.

Validation: 108 focused Python checks, two actual JavaScript-controller suites,
and the real template/controller preview at 1341 requested desktop width
(browser reports 1342), 768 and 390. Fixtures exercised denied access, rate
limits, malformed/oversized responses, concurrent readers, failed refreshes,
unknown timestamps, zero baselines, and unsafe text/links. Browser previews used
saved public responses without broker connectivity. Installed verification is
recorded separately in the release receipt.

Still separate: local sentiment-model evaluation; the Ekonix-inspired company
comparison; expanded weekly outcome analysis; direct Reddit approval; verified
WSB portfolio data; broker-paper lifecycle tests and market-session endurance.
This maintenance package does not change trading activation or risk limits.
