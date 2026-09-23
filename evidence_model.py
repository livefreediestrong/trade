"""Canonical research evidence envelope model.

Every research signal shown in the desk — news, Reddit/Stocktwits chatter, the
authorized X (Twitter) adapter, SEC/EDGAR filings, macro releases, market
context, and fundamental context — is normalized into one shared
``EvidenceEnvelope`` shape before it reaches analysis or the UI. That keeps
provenance, freshness, and confidence auditable across heterogeneous sources
and lets ``research_fidelity_summary`` report how trustworthy today's
research surface is.

This module is read-only research plumbing. Nothing here creates, approves,
sizes, or gates a trade — see ``risk_policy.py`` / ``app.py`` execution gates
for that. Evidence envelopes are for display and provenance only.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Ticker validation
# ---------------------------------------------------------------------------
# Common US equity/ETF tickers: 1-5 letters, optionally with a single
# class-share suffix like BRK.B or BF-B. Reject anything else so malformed or
# hostile input never becomes a "matched" ticker in an evidence envelope.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")


def validate_ticker(ticker: Any) -> str | None:
    """Return a normalized ticker string, or None if it doesn't look valid."""
    sym = str(ticker or "").strip().upper().lstrip("$")
    if not sym or not _TICKER_RE.match(sym):
        return None
    return sym


def validate_tickers(tickers: Iterable[Any] | None) -> list[str]:
    """Validate + de-duplicate a list of ticker candidates, preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tickers or []:
        sym = validate_ticker(raw)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


# ---------------------------------------------------------------------------
# Source quality weights
# ---------------------------------------------------------------------------
# Baseline source-quality priors (0..1). These are deliberately conservative:
# regulatory/primary-source filings and macro releases outrank unauthenticated
# public social chatter. Used as one input into overall `confidence`, never a
# hard gate.
SOURCE_QUALITY: dict[str, float] = {
    "sec_edgar": 0.97,
    "fred": 0.95,
    "fomc_calendar": 0.95,
    "market_data": 0.9,
    "fundamental_data": 0.85,
    "finnhub": 0.75,
    "benzinga": 0.7,
    "google_news": 0.6,
    "gdelt": 0.55,
    "public_rss": 0.6,
    "reddit_public": 0.35,
    "stocktwits": 0.3,
    "x_public": 0.3,
}

EVIDENCE_TYPES = {
    "news",
    "sec_filing",
    "social_post",
    "macro_release",
    "market_context",
    "fundamental_context",
}

_FRESHNESS_HALF_LIFE_SEC = 172800.0  # 48h: matches news_intelligence horizon


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _to_ts(value: Any) -> float | None:
    """Best-effort conversion of a reported time (epoch, ISO string, or
    RFC-2822-ish string) to a UTC epoch seconds float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def freshness_score(reported_ts: float | None, *, now: float | None = None) -> float:
    if reported_ts is None:
        return 0.0
    now_value = float(now if now is not None else time.time())
    age = max(0.0, now_value - float(reported_ts))
    return round(max(0.0, 1.0 - min(age, _FRESHNESS_HALF_LIFE_SEC) / _FRESHNESS_HALF_LIFE_SEC), 4)


def source_quality(source: str) -> float:
    return SOURCE_QUALITY.get(str(source or "").lower(), 0.5)


def canonical_hash(*parts: Any) -> str:
    """Stable content hash used for envelope identity and dedupe."""
    joined = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def entity_match(ticker: str | None, title: str, body: str = "") -> tuple[str, float]:
    """Whether `ticker` is exactly present (word-boundary) in the text, vs. it
    only being present because it was the query/watchlist symbol."""
    if not ticker:
        return "none", 0.0
    text = f"{title} {body}".upper()
    exact = bool(re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", text))
    return ("exact", 1.0) if exact else ("query_only", 0.55)


@dataclass
class EvidenceEnvelope:
    """Canonical, source-agnostic research evidence record."""

    envelope_id: str
    evidence_type: str
    source: str
    source_id: str
    url: str | None
    ticker: str | None
    entity_match: str
    entity_confidence: float
    title: str
    summary: str
    reported_at: str | None
    observed_at: str
    ingested_at: str
    freshness: float
    source_quality: float
    confidence: float
    support_links: list[str] = field(default_factory=list)
    contradiction_links: list[str] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    display_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_envelope(
    *,
    evidence_type: str,
    source: str,
    source_id: str,
    ticker: Any = None,
    title: str = "",
    summary: str = "",
    url: str | None = None,
    reported_ts: Any = None,
    observed_ts: float | None = None,
    tags: Iterable[str] | None = None,
    raw: dict[str, Any] | None = None,
    now: float | None = None,
) -> EvidenceEnvelope:
    """Construct one normalized evidence envelope.

    `reported_ts` accepts an epoch float, ISO string, or RFC-2822 string (the
    "reported" time the *source* claims, e.g. published/filed/created time).
    `observed_ts` is when *this app* fetched the record (defaults to now).
    `ingested_at` is always "now" — when the envelope itself was built.
    """
    now_value = float(now if now is not None else time.time())
    observed_value = float(observed_ts if observed_ts is not None else now_value)
    reported_value = _to_ts(reported_ts)
    sym = validate_ticker(ticker)
    match_kind, match_conf = entity_match(sym, title, summary)
    fresh = freshness_score(reported_value, now=now_value)
    quality = source_quality(source)
    confidence = round(min(1.0, max(0.0, 0.45 * quality + 0.35 * fresh + 0.20 * match_conf)), 4)
    source_id_str = str(source_id or "").strip() or canonical_hash(source, title, url or "")
    envelope_id = canonical_hash(evidence_type, source, source_id_str, sym or "", title)
    return EvidenceEnvelope(
        envelope_id=envelope_id,
        evidence_type=evidence_type,
        source=str(source or "unknown"),
        source_id=source_id_str,
        url=url or None,
        ticker=sym,
        entity_match=match_kind,
        entity_confidence=match_conf,
        title=str(title or "")[:300],
        summary=str(summary or "")[:600],
        reported_at=_iso(reported_value),
        observed_at=_iso(observed_value) or _iso(now_value),
        ingested_at=_iso(now_value),
        freshness=fresh,
        source_quality=quality,
        confidence=confidence,
        tags=[str(t) for t in (tags or []) if t],
        raw=dict(raw or {}),
    )


# ---------------------------------------------------------------------------
# Per-source adapters
# ---------------------------------------------------------------------------

def from_news_item(item: dict[str, Any], *, now: float | None = None) -> EvidenceEnvelope:
    return build_envelope(
        evidence_type="news",
        source=str(item.get("source") or "news"),
        source_id=str(item.get("headline_key") or item.get("link") or item.get("title") or ""),
        ticker=item.get("ticker"),
        title=str(item.get("title") or ""),
        summary=str(item.get("summary") or item.get("excerpt") or ""),
        url=item.get("link") or item.get("url"),
        reported_ts=item.get("published_ts"),
        tags=[*(item.get("event_tags") or []), *([item["polarity"]] if item.get("polarity") else [])],
        raw={k: item[k] for k in ("materiality", "intelligence_score") if k in item},
        now=now,
    )


def from_social_row(row: dict[str, Any], *, ticker: Any = None, now: float | None = None) -> EvidenceEnvelope:
    candidates = row.get("ticker_candidates") or ([ticker] if ticker else [])
    sym = ticker or (candidates[0] if candidates else None)
    return build_envelope(
        evidence_type="social_post",
        source=str(row.get("source") or "social"),
        source_id=str(row.get("source_id") or row.get("thread_id") or ""),
        ticker=sym,
        title=str(row.get("title") or ""),
        summary=str(row.get("excerpt") or ""),
        url=row.get("permalink"),
        reported_ts=row.get("created_at"),
        tags=[str(row.get("sentiment"))] if row.get("sentiment") else [],
        raw={
            "author_hash": row.get("author_hash"),
            "score": row.get("score"),
            "comments": row.get("comments"),
        },
        now=now,
    )


def from_edgar_filing(filing: dict[str, Any], *, ticker: Any, now: float | None = None) -> EvidenceEnvelope:
    return build_envelope(
        evidence_type="sec_filing",
        source="sec_edgar",
        source_id=str(filing.get("accession") or ""),
        ticker=ticker,
        title=str(filing.get("description") or filing.get("form") or "SEC filing"),
        summary=str(filing.get("form") or ""),
        url=filing.get("url"),
        reported_ts=filing.get("filed"),
        tags=[str(filing.get("form"))] if filing.get("form") else [],
        raw={"form": filing.get("form")},
        now=now,
    )


def from_macro_event(event: dict[str, Any], *, source: str = "fred", now: float | None = None) -> EvidenceEnvelope:
    label = str(event.get("label") or event.get("kind") or "macro event")
    return build_envelope(
        evidence_type="macro_release",
        source=str(event.get("source") or source),
        source_id=str(event.get("release_id") or event.get("kind") or label),
        ticker=None,
        title=label,
        summary=str(event.get("kind") or ""),
        url=None,
        reported_ts=event.get("date"),
        tags=[str(event.get("kind"))] if event.get("kind") else [],
        raw={"heuristic": bool(event.get("research_only"))},
        now=now,
    )


def from_market_context(ticker: Any, quote: dict[str, Any], *, source: str = "market_data", now: float | None = None) -> EvidenceEnvelope:
    price = quote.get("price") or quote.get("last") or quote.get("c")
    return build_envelope(
        evidence_type="market_context",
        source=source,
        source_id=f"{validate_ticker(ticker) or ticker}:quote",
        ticker=ticker,
        title=f"{validate_ticker(ticker) or ticker} market snapshot",
        summary=f"last={price}" if price is not None else "",
        url=None,
        reported_ts=quote.get("ts") or quote.get("t") or now,
        raw={k: quote[k] for k in quote if k in {"price", "last", "volume", "change_pct", "c", "v"}},
        now=now,
    )


def from_fundamental_context(
    ticker: Any, fundamentals: dict[str, Any], *, source: str = "fundamental_data", now: float | None = None
) -> EvidenceEnvelope:
    metric = fundamentals.get("metric") or "fundamentals"
    return build_envelope(
        evidence_type="fundamental_context",
        source=source,
        source_id=f"{validate_ticker(ticker) or ticker}:{metric}",
        ticker=ticker,
        title=f"{validate_ticker(ticker) or ticker} {metric}",
        summary=str(fundamentals.get("summary") or ""),
        url=fundamentals.get("url"),
        reported_ts=fundamentals.get("as_of") or now,
        raw={k: v for k, v in fundamentals.items() if k not in {"summary", "url", "as_of"}},
        now=now,
    )


# ---------------------------------------------------------------------------
# Dedupe / linking / summary
# ---------------------------------------------------------------------------

def dedupe(envelopes: Iterable[EvidenceEnvelope]) -> list[EvidenceEnvelope]:
    """Drop duplicate envelopes sharing a canonical hash, keeping the freshest."""
    best: dict[str, EvidenceEnvelope] = {}
    for env in envelopes:
        existing = best.get(env.envelope_id)
        if existing is None or env.freshness > existing.freshness:
            best[env.envelope_id] = env
    return list(best.values())


_POSITIVE_TAGS = {"bullish", "positive"}
_NEGATIVE_TAGS = {"bearish", "negative"}


def link_support_contradiction(envelopes: list[EvidenceEnvelope]) -> list[EvidenceEnvelope]:
    """Populate support_links / contradiction_links between envelopes that
    share a ticker + evidence_type cluster but agree/disagree in polarity."""
    groups: dict[tuple[str, str], list[EvidenceEnvelope]] = {}
    for env in envelopes:
        if not env.ticker:
            continue
        groups.setdefault((env.ticker, env.evidence_type), []).append(env)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        for env in rows:
            polarity = "pos" if _POSITIVE_TAGS & set(env.tags) else "neg" if _NEGATIVE_TAGS & set(env.tags) else None
            for other in rows:
                if other.envelope_id == env.envelope_id:
                    continue
                other_polarity = (
                    "pos" if _POSITIVE_TAGS & set(other.tags) else "neg" if _NEGATIVE_TAGS & set(other.tags) else None
                )
                if polarity is None or other_polarity is None:
                    continue
                if polarity == other_polarity:
                    if other.envelope_id not in env.support_links:
                        env.support_links.append(other.envelope_id)
                else:
                    if other.envelope_id not in env.contradiction_links:
                        env.contradiction_links.append(other.envelope_id)
    return envelopes


def research_fidelity_summary(envelopes: list[EvidenceEnvelope]) -> dict[str, Any]:
    """Roll up envelope coverage/freshness/confidence into one auditable
    research_fidelity summary, safe to surface via API/UI."""
    if not envelopes:
        return {
            "envelope_count": 0,
            "source_types": {},
            "tickers_covered": [],
            "avg_freshness": 0.0,
            "avg_confidence": 0.0,
            "avg_source_quality": 0.0,
            "contradiction_count": 0,
            "stale_count": 0,
            "coverage_score": 0.0,
            "display_only": True,
        }
    by_type: dict[str, int] = {}
    tickers: set[str] = set()
    contradictions = 0
    stale = 0
    for env in envelopes:
        by_type[env.evidence_type] = by_type.get(env.evidence_type, 0) + 1
        if env.ticker:
            tickers.add(env.ticker)
        if env.contradiction_links:
            contradictions += 1
        if env.freshness < 0.25:
            stale += 1
    n = len(envelopes)
    avg_fresh = round(sum(e.freshness for e in envelopes) / n, 4)
    avg_conf = round(sum(e.confidence for e in envelopes) / n, 4)
    avg_quality = round(sum(e.source_quality for e in envelopes) / n, 4)
    coverage = round(min(1.0, len(by_type) / len(EVIDENCE_TYPES)), 4)
    return {
        "envelope_count": n,
        "source_types": by_type,
        "tickers_covered": sorted(tickers),
        "avg_freshness": avg_fresh,
        "avg_confidence": avg_conf,
        "avg_source_quality": avg_quality,
        "contradiction_count": contradictions,
        "stale_count": stale,
        "coverage_score": coverage,
        "display_only": True,
    }


def envelopes_to_dicts(envelopes: Iterable[EvidenceEnvelope]) -> list[dict[str, Any]]:
    return [e.to_dict() for e in envelopes]


def research_fidelity_summary_from_dicts(envelope_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Same rollup as `research_fidelity_summary`, but accepts the plain dict
    form already emitted by per-source adapters (news/social/edgar/macro),
    for callers combining envelopes across multiple modules."""
    if not envelope_dicts:
        return research_fidelity_summary([])
    by_id: dict[str, dict[str, Any]] = {}
    for row in envelope_dicts:
        eid = row.get("envelope_id")
        if not eid:
            continue
        existing = by_id.get(eid)
        if existing is None or float(row.get("freshness") or 0) > float(existing.get("freshness") or 0):
            by_id[eid] = row
    rows = list(by_id.values())
    if not rows:
        return research_fidelity_summary([])
    by_type: dict[str, int] = {}
    tickers: set[str] = set()
    contradictions = 0
    stale = 0
    for row in rows:
        evidence_type = str(row.get("evidence_type") or "unknown")
        by_type[evidence_type] = by_type.get(evidence_type, 0) + 1
        if row.get("ticker"):
            tickers.add(str(row["ticker"]))
        if row.get("contradiction_links"):
            contradictions += 1
        if float(row.get("freshness") or 0) < 0.25:
            stale += 1
    n = len(rows)
    return {
        "envelope_count": n,
        "source_types": by_type,
        "tickers_covered": sorted(tickers),
        "avg_freshness": round(sum(float(r.get("freshness") or 0) for r in rows) / n, 4),
        "avg_confidence": round(sum(float(r.get("confidence") or 0) for r in rows) / n, 4),
        "avg_source_quality": round(sum(float(r.get("source_quality") or 0) for r in rows) / n, 4),
        "contradiction_count": contradictions,
        "stale_count": stale,
        "coverage_score": round(min(1.0, len(by_type) / len(EVIDENCE_TYPES)), 4),
        "display_only": True,
    }
