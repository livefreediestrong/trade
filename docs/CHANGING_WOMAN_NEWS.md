# Changing Woman's headline commentary

Implemented September 24, 2026. This is a curated ten-source mix for the US desk,
not a measured global ranking of news quality. No subscription or paid model call
is created. Headlines, original links and dates are read from public publisher feeds;
full articles remain at the publisher and may require a subscription.

| Source | Purpose | Reference |
| --- | --- | --- |
| CNBC | US business and market reporting | https://www.cnbc.com/rss-feeds/ |
| MarketWatch | Market context and investing coverage | https://www.marketwatch.com/rss/ |
| The Wall Street Journal | Market and corporate reporting | https://www.wsj.com/news/markets |
| Financial Times | Global context for US markets | https://www.ft.com/ |
| Yahoo Finance | Broad coverage and syndicated headlines | https://finance.yahoo.com/ |
| Federal Reserve | Official policy and banking announcements | https://www.federalreserve.gov/feeds/feeds.htm |
| SEC | Securities regulation and enforcement releases | https://www.sec.gov/about/rss-feeds |
| Bureau of Labor Statistics | Employment and CPI releases | https://www.bls.gov/feed/ |
| Energy Information Administration | Official energy analysis | https://www.eia.gov/tools/rssfeeds/ |
| Federal Trade Commission | Competition and consumer-protection releases | https://www.ftc.gov/stay-connected/rss |

Source feed URLs and allowed article domains live in `companion_news.SOURCES`.
MarketWatch and WSJ share the Dow Jones ownership label. Yahoo's syndication is
explicit. This panel does not calculate independent confirmation or sentiment.

## Behavior

- Refresh the eleven feeds representing ten sources every five minutes while the
  app is running, with at most four concurrent requests. The GET endpoint returns
  immediately while the refresh runs in the background.
- Parse RSS and Atom dates. Exclude missing/naive/future dates, unsupported XML,
  unexpected article domains, credentials in links and excessive feed bodies.
- Use only headlines no older than 36 hours whose source check is no older than
  15 minutes for new quips. Older official releases can leave a connected feed
  with zero recent headlines; that is different from an outage.
- Keep publication and retrieval timestamps separate. Failed refreshes retain
  cached rows with an unavailable label, never renewed freshness. A bad optional
  news-cache file is discarded without marking trading state corrupt.
- Use deterministic topic-aware, app-written observations in Changing Woman's
  dry, composed voice. No full-article summary or invented price prediction.
  Serious-story keywords select a measured line without a punchline. This is a
  conservative keyword rule, not comprehensive semantic understanding.
- Show the actual headline, named publisher, publication time and direct link
  beside her comment. External links open with noopener/noreferrer. Render all
  fetched text as text, never HTML.
- Speak for at most 32 seconds, at most once per five minutes. Session deduplication
  remembers delivered story IDs. Suppressed delivery can retry later. News belongs
  only to Changing Woman; Fox retains social-buzz cues.
- Respect Quiet desk, speech-off, hidden avatars, small screens, typing, dialogs,
  user-requested guidance and reduced motion. The news module remains readable
  on phones even when sidebar characters are hidden.
- Reserve sidebar space for the taller cited bubble so it cannot overlap menu links.

The feed service and quip controller do not call broker execution, alter risk
settings, or supply news-derived trading eligibility. `/api/companion/news` is a
read-only view with a bounded feed refresh. The small cached news file is excluded
from source recovery archives.

## Validation

All ten source endpoints returned parsed items during live verification; the
deployed endpoint reported ten connected sources and 80 recent deduplicated
headlines. Connectivity and publication cadence can change. RSS is not a
guaranteed real-time institutional newswire.

78 focused Python tests passed, including feed parsing, timestamp/domain/size
rejection, stale-on-failure handling, optional cache isolation, single-flight refresh,
and prior companion/workspace tests. Four JavaScript suites passed for quip tone,
read-only polling, delivery/deduplication, avatar behavior and Fox's existing cues.
Browser validation observed a genuine current headline with Changing Woman's
attributed quip, all ten source statuses, and Quiet desk hiding the bubble.
