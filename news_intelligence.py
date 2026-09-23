"""Research-only intelligence over heterogeneous news and event records."""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

_POSITIVE = re.compile(r"\b(beat|raises|raised|upgrade|approved|growth|surge|record|wins)\b", re.I)
_NEGATIVE = re.compile(r"\b(miss|cuts|cut|downgrade|rejected|decline|loss|lawsuit|probe|recall)\b", re.I)
_STATS: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"requests": 0, "successes": 0, "items": 0, "last_error": None, "last_at": None}
)


def record_provider(name: str, *, ok: bool, items: int = 0, error: str | None = None) -> None:
    row = _STATS[str(name or "unknown")]
    row["requests"] += 1
    row["successes"] += int(bool(ok))
    row["items"] += max(0, int(items or 0))
    row["last_error"] = str(error)[:160] if error else None
    row["last_at"] = time.time()


def provider_reliability() -> dict[str, dict[str, Any]]:
    out = {}
    for name, row in _STATS.items():
        requests = int(row["requests"] or 0)
        out[name] = {
            **row,
            "success_rate": round(row["successes"] / requests, 4) if requests else None,
            "avg_items": round(row["items"] / requests, 2) if requests else 0,
        }
    return out


def _cluster_key(item: dict[str, Any]) -> str:
    tags = tuple(sorted(str(tag) for tag in (item.get("event_tags") or []) if tag))
    if tags and item.get("ticker"):
        return f"{str(item.get('ticker')).upper()}|{tags[0]}"
    key = str(item.get("headline_key") or item.get("title") or "").lower()
    return re.sub(r"\b(the|a|an)\b", " ", key)


def _event_polarity(title: str) -> str:
    pos = bool(_POSITIVE.search(title or ""))
    neg = bool(_NEGATIVE.search(title or ""))
    return "conflicted" if pos and neg else "positive" if pos else "negative" if neg else "neutral"


def analyze_items(items: list[dict[str, Any]] | None, *, now: float | None = None) -> dict[str, Any]:
    """Build agreement, freshness, contradiction, entity, and digest metadata."""
    now_value = float(now if now is not None else time.time())
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    enriched = []
    for raw in items or []:
        item = dict(raw)
        title = str(item.get("title") or "").strip()
        ticker = str(item.get("ticker") or "").upper()
        exact_entity = bool(ticker and re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", title.upper()))
        published = item.get("published_ts")
        age = max(0.0, now_value - float(published)) if published is not None else None
        freshness = round(max(0.0, 1.0 - min(age or 172800.0, 172800.0) / 172800.0), 4)
        item["entity_match"] = "exact" if exact_entity else "query_only"
        item["entity_confidence"] = 1.0 if exact_entity else 0.55
        item["freshness_score"] = freshness
        item["polarity"] = _event_polarity(title)
        groups[_cluster_key(item)].append(item)
    contradictions = []
    for key, rows in groups.items():
        sources = sorted({str(row.get("source") or "unknown") for row in rows})
        polarities = {row["polarity"] for row in rows if row["polarity"] != "neutral"}
        agreement = len(sources)
        contradiction = len(polarities) > 1
        for row in rows:
            row["source_agreement"] = agreement
            row["independent_sources"] = sources
            row["contradiction"] = contradiction
            row["cluster_key"] = key
            if contradiction:
                contradictions.append(key)
        enriched.extend(rows)
    enriched.sort(
        key=lambda row: (
            1 if row.get("contradiction") else 0,
            int(row.get("source_agreement") or 0),
            float(row.get("intelligence_score") or 0),
            float(row.get("published_ts") or 0),
        ),
        reverse=True,
    )
    alerts = [
        row for row in enriched
        if row.get("materiality") == "high"
        and int(row.get("source_agreement") or 0) >= 2
        and not row.get("contradiction")
    ]
    return {
        "items": enriched,
        "source_agreement": [
            {
                "cluster_key": key,
                "headline": rows[0].get("title"),
                "sources": sorted({str(row.get("source") or "unknown") for row in rows}),
                "count": len({str(row.get("source") or "unknown") for row in rows}),
                "contradiction": len({_event_polarity(str(row.get("title") or "")) for row in rows} - {"neutral"}) > 1,
            }
            for key, rows in groups.items()
        ],
        "contradictions": contradictions,
        "alerts": alerts[:10],
        "digest": enriched[:8],
        "freshness": {
            "latest_ts": max((float(row["published_ts"]) for row in enriched if row.get("published_ts") is not None), default=None),
            "stale_count": sum(1 for row in enriched if float(row.get("freshness_score") or 0) < 0.25),
        },
        "provider_reliability": provider_reliability(),
        "display_only": True,
    }


def build_timeline(
    items: list[dict[str, Any]] | None = None,
    filings: list[dict[str, Any]] | None = None,
    macro: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    timeline = []
    for item in items or []:
        timeline.append({**item, "event_type": "news", "event_ts": item.get("published_ts")})
    for filing in filings or []:
        timeline.append(
            {
                **filing,
                "event_type": "sec_filing",
                "event_ts": _date_ts(filing.get("filed")),
                "title": filing.get("description") or filing.get("form") or "SEC filing",
                "source": "sec_edgar",
            }
        )
    for event in macro or []:
        timeline.append({**event, "event_type": "macro", "event_ts": event.get("timestamp") or event.get("ts")})
    return sorted(timeline, key=lambda row: float(row.get("event_ts") or 0), reverse=True)


def _date_ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def reaction_study(symbol: str, items: list[dict[str, Any]], bars: Any) -> dict[str, Any]:
    """Descriptive event-date reaction study; no prediction or execution output."""
    if bars is None or not hasattr(bars, "iterrows"):
        return {"ticker": symbol, "observations": [], "status": "no_bars", "display_only": True}
    closes = {}
    for index, row in bars.iterrows():
        try:
            closes[str(index)[:10]] = float(row["Close"])
        except (KeyError, TypeError, ValueError):
            continue
    observations = []
    for item in items or []:
        ts = item.get("published_ts")
        if ts is None:
            continue
        day = datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")
        close = closes.get(day)
        next_close = next((closes[key] for key in sorted(closes) if key > day), None)
        if close and next_close:
            observations.append(
                {
                    "title": item.get("title"),
                    "event_tags": item.get("event_tags") or [],
                    "day": day,
                    "next_day_return": round((next_close / close - 1) * 100, 4),
                }
            )
    return {"ticker": symbol, "observations": observations, "count": len(observations), "display_only": True}
