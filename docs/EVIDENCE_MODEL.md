# Canonical research evidence model

`evidence_model.py` defines one shared **evidence envelope** shape that every
research source in the desk is normalized into: news, Reddit/Stocktwits
public chatter, the authorized X (Twitter) API adapter, SEC/EDGAR filings,
macro (FRED/FOMC) releases, market context (quotes), and fundamental context
(analyst data). This keeps provenance, freshness, and confidence auditable
across heterogeneous providers, and lets the desk report one
`research_fidelity` summary for "how trustworthy is today's research surface".

**This is read-only research plumbing.** Nothing in this model creates,
approves, sizes, or gates a trade. Execution gates (`risk_policy.py`,
`can_take_trade`, macro/promotion gates in `app.py`) are unaffected by
evidence envelopes or `research_fidelity` — see "Never affects execution"
below.

## The envelope shape

Every envelope (`evidence_model.EvidenceEnvelope`) carries:

| Field | Meaning |
| --- | --- |
| `envelope_id` | Canonical content hash (`evidence_type + source + source_id + ticker + title`); stable identity for dedupe/linking. |
| `evidence_type` | One of `news`, `sec_filing`, `social_post`, `macro_release`, `market_context`, `fundamental_context`. |
| `source` / `source_id` | Provider name (e.g. `reddit_wallstreetbets`, `sec_edgar`, `fred`, `x_public`) and its native record id. |
| `url` | Source URL when the provider exposes one (permalink, filing URL, ...). |
| `ticker` | Validated ticker (see below), or `None`. |
| `entity_match` / `entity_confidence` | Whether the ticker was found exactly in the text (`exact`, confidence 1.0) vs. only inferred from the query/watchlist (`query_only`, confidence 0.55). |
| `reported_at` | UTC ISO time the *source* claims (published/filed/created time). |
| `observed_at` | UTC ISO time *this app* fetched the record. |
| `ingested_at` | UTC ISO time the envelope itself was built. |
| `freshness` | 0..1 decay score vs. a 48h half-life on `reported_at`. |
| `source_quality` | 0..1 prior per source (regulatory/primary sources rank above unauthenticated social chatter — see `SOURCE_QUALITY`). |
| `confidence` | Blend of `source_quality`, `freshness`, and `entity_confidence`. |
| `support_links` / `contradiction_links` | Envelope ids of same-ticker/same-type records that agree/disagree (via `link_support_contradiction`). |
| `derived_from` | Provenance chain for any higher-level conclusion built from this envelope. |
| `raw` | Small bag of source-specific extras (kept out of the canonical fields). |
| `display_only` | Always `True` — a structural reminder that this is not an execution signal. |

## Ticker validation & dedupe

* `evidence_model.validate_ticker` / `validate_tickers` accept only
  1–5 letter symbols (with an optional single class-share suffix like
  `BRK.B`), reject junk input, and de-duplicate while preserving order. All
  adapters run tickers through this before building an envelope.
* `evidence_model.dedupe(envelopes)` collapses envelopes sharing an
  `envelope_id`, keeping the freshest.
* `social_intelligence._dedupe_rows` similarly dedupes raw social rows by
  `(source, source_id/permalink/title)` before they become envelopes, so the
  same Reddit thread or tweet surfaced twice never double-counts.

## `research_fidelity` summary

`evidence_model.research_fidelity_summary(envelopes)` (and its dict-based
twin `research_fidelity_summary_from_dicts`, for combining envelopes already
serialized by different modules) rolls up:

* `envelope_count`, `source_types` (counts per `evidence_type`)
* `tickers_covered`
* `avg_freshness`, `avg_confidence`, `avg_source_quality`
* `contradiction_count`, `stale_count`
* `coverage_score` — fraction of the 6 evidence types actually represented

It is exposed via:

* `GET /api/research/fidelity?symbols=...` — dedicated endpoint returning
  `research_fidelity` + the underlying `envelopes` for the requested tickers.
* `GET /api/research/intelligence` — includes `research_fidelity` /
  `envelopes` alongside the existing news timeline/digest.
* `GET /api/state` (`research_context.research_fidelity`, top-level
  `research_fidelity`) — used by the "Evidence N · conf X%" chip in the
  Advanced research/news-intelligence panel (`static/app.js` →
  `renderIntelligence`).
* `social_intelligence.snapshot(...)`, `edgar_client.recent_filings(...)`,
  and `macro_calendar.calendar_snapshot(...)` each also embed their own
  `research_fidelity` + `envelopes` for that source alone.

## Per-source adapters

| Module | Builder | Notes |
| --- | --- | --- |
| `news_intelligence.py` | `from_news_item` / `build_envelopes` | Adds `polarity` as a tag so `link_support_contradiction` can flag conflicting headlines. |
| `social_intelligence.py` | `from_social_row` | Covers Reddit, Stocktwits, public RSS, and (when enabled) the X adapter — all normalized into the same row shape first. |
| `edgar_client.py` | `from_edgar_filing` | One envelope per filing (`accession` as `source_id`). |
| `macro_calendar.py` | `from_macro_event` | FRED/FOMC release flags and latest observations. |
| `app.py` (`_refresh_state_aux`) | `from_market_context` / `from_fundamental_context` | Built from `data_sources.finnhub_quote` / `finnhub_recommendation` for the focus ticker. |

## Authorized X (Twitter) adapter

`x_adapter.py` adds an **authorized, compliant** X API v2 adapter:

* Uses **only** the official `GET /2/tweets/search/recent` endpoint with an
  operator-provided bearer token (`X_BEARER_TOKEN` / `TWITTER_BEARER_TOKEN`).
  No scraping, no browser automation, no private/paywalled endpoints.
* **Off by default** and gated behind *two* config flags:
  `social_enabled=true` AND `x_enabled=true` (see `DEFAULT_CONFIG` in
  `app.py`, settable via `POST /api/config`), plus the bearer token being
  present. `x_adapter.is_enabled(cfg)` encodes this gate.
  Since social research itself is research-only/off-by-default, the X
  adapter inherits that: it can never be reached in a default install.
* **Local rate limiting**: `x_adapter._try_reserve_request_slot()` enforces
  a conservative token-bucket (`X_RATE_LIMIT_PER_15MIN`, default 30 per 15
  minutes) well under typical X API recent-search tier caps, so the adapter
  can't exceed its authorized quota even under bursty polling. Status is
  visible via `x_adapter.rate_limit_status()` / `public_status()`
  (surfaced under `details.x` in `/api/health` and `/api/providers`).
  Query text always keeps `X_SEARCH_QUERY_SUFFIX` (`-is:retweet -is:reply
  lang:en` by default) to reduce noise and stay within intended use.
* Rows are normalized into the exact same shape as Reddit/Stocktwits rows
  (`source`, `source_id`, `author_hash` — a SHA-256 hash, never a raw
  author id/handle — `permalink`, `ticker_candidates`, ...) before becoming
  evidence envelopes, so they get the same `display_only` / `attention_only`
  treatment everywhere downstream.

## Never affects execution

Evidence envelopes and `research_fidelity` are additive, read-only research
context. They do not participate in `can_take_trade`, `risk_policy`,
`execute_gated_broker_or_paper`, or any other execution gate — those remain
governed solely by the desk's existing risk/promotion/macro gates. Social
and X sources also carry `research_only` / `display_only` / `attention_only`
markers throughout so downstream consumers never mistake them for a trade
signal.
