# Community research and dated price checks

Open Research -> Market watch -> Community attention & price checks.
Expand a ticker for dated source examples and a provider price check. A separate
ticker field works when community sources are unavailable. Small screens show
the counts as labeled cards. No price request runs just because the panel opens.

The sample covers 24 hours ending at the displayed snapshot time. It excludes
old, missing/future-dated and duplicate records. Message IDs are shared across
Stocktwits symbol results. Unknown/deleted authors and RSS feeds are not people.
Account counts are scoped by platform; they do not prove independent people.
News/feed headlines are separate from community tone. A post mentioning several
tickers has unclear direction. Tone uses word cues/provider labels, so sarcasm,
negation and missing posts remain limitations; this is not a representative poll.

Prices reuse the existing Finnhub/Yahoo reader. One explicit request runs at a
time, results/failures cache for 60 seconds, and uncached requests have a shared
three-second spacing. Cached observations retain their market and receipt times.
Price age is recomputed when returned. Currency is unavailable from this reader
and is stated as such. Yahoo's one-minute result is a bar close. None of these
observations is an executable bid/ask or proof of real-time exchange entitlement.
This release adds no paid provider, subscription, broker access, risk change or
trading activation. Research context is not an order approval.

## Access facts checked September 28, 2026

- Reddit's current Data API guidance requires a registered OAuth token and an
  honest descriptive user agent. Appending `.json` is not an access workaround.
  https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
- The existing legacy REDDIT_PUBLIC_JSON opt-in remains unchanged. It now shares
  cache, response checks and cooldown handling with the authenticated reader.
  OAuth rejection never causes an anonymous fallback. Denial stops repeated reads.
- Reddit ordinary post listings do not establish access to the embedded WSB
  Verified Trader portfolio dataset. The screenshot is a dated visual reference,
  not a connected portfolio feed or verified return history.
- The supplied Discord invite resolved to TRADING ANALYTICS, with a premium-chat
  target. Affiliation, channel content and permission to collect messages were
  not verified. No server was joined and no message sent.
- Polygon renamed itself Massive. A provider addition still needs explicit data
  entitlement/configuration; this release uses existing readers.
  https://www.massive.com/blog/polygon-is-now-massive

## Validation scope

Focused Python tests exercise Reddit 401/403/429 recovery, legacy cache/backoff,
freshness/deduplication, multi-symbol posts, source separation and the actual
price endpoint (cache, errors, invalid tickers and concurrency limits). The actual
JavaScript controller is exercised for no automatic price reads, safe links,
stale prices and failed requests. Browser preview uses labeled fixtures and denies
outgoing sockets, with desktop/mobile captures. The installed runtime and one real
provider check are recorded separately in the release receipt. These checks do
not establish Reddit approval, a working WSB/Discord feed or live execution safety.
