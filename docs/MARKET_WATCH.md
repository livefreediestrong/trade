# Market watch, X watcher and trend scanner

Research section → **Market watch & trend scanner** (`GET /api/market-watch`, `?force=1` to refresh).
Everything here is display-only: it never creates, sizes or approves a trade.

## What it shows

| Block | Sources | Needs |
| --- | --- | --- |
| Trending across the internet | Reddit (buzz cache), Stocktwits trending, Yahoo Finance trending, Google Trends daily searches, Google News market headlines, X | Nothing (X and Reddit optional) |
| Market headlines | Google News RSS: Business topic + "stock market" (last day); your `MARKET_WATCH_RSS_FEEDS` | Nothing |
| X stock watcher | X API v2 recent search: watchlist `$cashtags` + `X_WATCH_ACCOUNTS` | `X_BEARER_TOKEN` |
| Social pulse | Reddit, Stocktwits, public RSS, X | Social research on in Settings; Reddit needs `REDDIT_CLIENT_ID/SECRET` |
| Watchlist on Google Finance | Links to Google Finance quote pages | Nothing |

## Trend scanner

Each source gives a ranked list. A ticker scores by its rank on each list, and
tickers on more independent lists rank first ("broad" = 3+ sources). Tickers are
checked against the Nasdaq US listing directory the desk already downloads, so
crypto (`BTC.X`), indices (`^GSPC`) and futures are dropped. Google Trends
reports search terms, not tickers: a term is matched to a company only by its
exact listed name ("nvidia", "nvidia stock", "nvda stock"); everyday-word names
such as Target or Match are never matched from free text. Unmatched
finance-related searches appear as topics.

Trending means attention, not quality or direction. Several sources often
echo one story, and pumps and leveraged/inverse funds trend too (they are flagged).

## X watcher

- Off until `X_BEARER_TOKEN` is set. X's recent search is a paid API.
- Budget: `X_MONTHLY_POST_CAP` (default 3000) is spread evenly over the month's
  days (`X_DAILY_POST_CAP` overrides), so a busy morning cannot spend the month.
  Usage is kept in `data/x_watch_usage.json`.
- Every `X_POLL_MINUTES` (default 15) it asks only for posts newer than the last
  poll (`since_id`), `X_MAX_RESULTS` (default 10) per query, at most three
  queries per poll; long watchlists rotate through their query chunks.
- 429 waits for X's reset time; 401/403 back off for 1/6 hours with the reason
  shown. If the plan rejects the `$cashtag` operator it switches to ticker
  keywords (word-like tickers such as AI or ON are skipped in that mode).
- Posts tagging more than five cashtags are dropped as spam lists.

## Edge cases handled in the existing feeds

- Google News `pubDate` (RFC 2822) and GDELT `YYYYMMDDHHMMSS` dates now parse;
  GDELT dates were previously read as year-2612 timestamps and always ranked freshest.
- Future-dated headlines are not treated as fresh. Feeds over 2 MB are refused.
- Google News rows name the real publisher and drop the " - Publisher" title
  suffix, so the same story from Yahoo/Finnhub deduplicates.
- Word-like tickers (F, AI, ON, NOW...) are searched on Google News by
  exchange-qualified mentions and counted in social text only as `$cashtags`.
- Social rows sort by parsed time; mixed date formats previously let stale RSS
  items crowd out fresh posts. Stocktwits links are real message URLs.
- Reddit in the social pulse uses the same OAuth client as the buzz scanner;
  public JSON stays opt-in (`REDDIT_PUBLIC_JSON=1`) per Reddit's API terms.
- Sources are fetched in parallel with per-source status; one failure never
  empties the panel. Custom feeds may be RSS or Atom.

## Not included

- No Google Finance scraping (no public API; links open Google's page).
- No unofficial X mirrors or RSS bridges (unreliable and against X's terms).
- Live verification of Google, X and Stocktwits endpoints was not possible from
  the build environment; parsers are tested against recorded feed formats.
