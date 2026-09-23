from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evidence_model as em


def test_validate_ticker_accepts_and_rejects():
    assert em.validate_ticker("aapl") == "AAPL"
    assert em.validate_ticker("$MSFT") == "MSFT"
    assert em.validate_ticker("BRK.B") == "BRK.B"
    assert em.validate_ticker("") is None
    assert em.validate_ticker("TOOLONGTICKER") is None
    assert em.validate_ticker("123") is None
    assert em.validate_ticker("<script>") is None


def test_validate_tickers_dedupes_preserving_order():
    out = em.validate_tickers(["aapl", "AAPL", "$msft", "???", "NVDA"])
    assert out == ["AAPL", "MSFT", "NVDA"]


def test_build_envelope_basic_fields():
    now = time.time()
    env = em.build_envelope(
        evidence_type="news",
        source="benzinga",
        source_id="abc123",
        ticker="AAPL",
        title="AAPL beats on earnings",
        summary="Record quarter",
        url="https://example.com/a",
        reported_ts=now - 60,
        now=now,
    )
    assert env.ticker == "AAPL"
    assert env.entity_match == "exact"
    assert env.entity_confidence == 1.0
    assert env.evidence_type == "news"
    assert env.display_only is True
    assert 0.9 < env.freshness <= 1.0
    assert 0.0 <= env.confidence <= 1.0
    assert env.envelope_id  # stable non-empty hash


def test_build_envelope_query_only_match():
    env = em.build_envelope(
        evidence_type="social_post",
        source="reddit_public",
        source_id="t1",
        ticker="TSLA",
        title="Big news today",
        summary="no ticker mentioned in text",
    )
    assert env.entity_match == "query_only"
    assert env.entity_confidence < 1.0


def test_envelope_ids_are_stable_and_hash_based():
    kwargs = dict(evidence_type="news", source="benzinga", source_id="x1", ticker="MSFT", title="Headline")
    a = em.build_envelope(**kwargs)
    b = em.build_envelope(**kwargs)
    assert a.envelope_id == b.envelope_id
    diff = em.build_envelope(**{**kwargs, "source_id": "x2"})
    assert diff.envelope_id != a.envelope_id


def test_dedupe_keeps_freshest():
    now = time.time()
    stale = em.build_envelope(
        evidence_type="news", source="benzinga", source_id="dup", ticker="AAPL",
        title="Dup", reported_ts=now - 100000, now=now,
    )
    fresh = em.build_envelope(
        evidence_type="news", source="benzinga", source_id="dup", ticker="AAPL",
        title="Dup", reported_ts=now - 10, now=now,
    )
    out = em.dedupe([stale, fresh])
    assert len(out) == 1
    assert out[0].freshness == fresh.freshness


def test_link_support_contradiction_flags_disagreement():
    bull = em.build_envelope(
        evidence_type="social_post", source="reddit_public", source_id="1",
        ticker="AAPL", title="bullish take", tags=["bullish"],
    )
    bear = em.build_envelope(
        evidence_type="social_post", source="reddit_public", source_id="2",
        ticker="AAPL", title="bearish take", tags=["bearish"],
    )
    also_bull = em.build_envelope(
        evidence_type="social_post", source="reddit_public", source_id="3",
        ticker="AAPL", title="also bullish", tags=["bullish"],
    )
    linked = em.link_support_contradiction([bull, bear, also_bull])
    by_id = {e.envelope_id: e for e in linked}
    assert by_id[bull.envelope_id].contradiction_links
    assert by_id[bull.envelope_id].support_links
    assert also_bull.envelope_id in by_id[bull.envelope_id].support_links
    assert bear.envelope_id in by_id[bull.envelope_id].contradiction_links


def test_research_fidelity_summary_empty():
    summary = em.research_fidelity_summary([])
    assert summary["envelope_count"] == 0
    assert summary["coverage_score"] == 0.0
    assert summary["display_only"] is True


def test_research_fidelity_summary_coverage_and_confidence():
    now = time.time()
    envelopes = [
        em.build_envelope(evidence_type="news", source="benzinga", source_id="1", ticker="AAPL", title="A", reported_ts=now, now=now),
        em.build_envelope(evidence_type="sec_filing", source="sec_edgar", source_id="2", ticker="AAPL", title="10-Q", reported_ts=now, now=now),
        em.build_envelope(evidence_type="social_post", source="reddit_public", source_id="3", ticker="AAPL", title="chatter", reported_ts=now - 500000, now=now),
    ]
    summary = em.research_fidelity_summary(envelopes)
    assert summary["envelope_count"] == 3
    assert summary["source_types"]["news"] == 1
    assert summary["source_types"]["sec_filing"] == 1
    assert "AAPL" in summary["tickers_covered"]
    assert summary["stale_count"] == 1
    assert 0.0 < summary["coverage_score"] <= 1.0


def test_research_fidelity_summary_from_dicts_matches_object_form():
    now = time.time()
    envelopes = [
        em.build_envelope(evidence_type="news", source="benzinga", source_id="1", ticker="AAPL", title="A", reported_ts=now, now=now),
        em.build_envelope(evidence_type="macro_release", source="fred", source_id="2", title="CPI", reported_ts=now, now=now),
    ]
    from_objects = em.research_fidelity_summary(envelopes)
    from_dicts = em.research_fidelity_summary_from_dicts(em.envelopes_to_dicts(envelopes))
    assert from_objects["envelope_count"] == from_dicts["envelope_count"]
    assert from_objects["source_types"] == from_dicts["source_types"]


def test_from_news_item_and_from_social_row_shapes():
    news_item = {
        "source": "benzinga", "headline_key": "k1", "ticker": "AAPL",
        "title": "AAPL rallies", "published_ts": time.time(), "event_tags": ["earnings"],
        "polarity": "positive",
    }
    env = em.from_news_item(news_item)
    assert env.evidence_type == "news"
    assert "positive" in env.tags

    social_row = {
        "source": "reddit_wallstreetbets", "source_id": "abc", "title": "$AAPL to the moon",
        "excerpt": "bullish", "ticker_candidates": ["AAPL"], "sentiment": "bullish",
        "permalink": "https://reddit.com/x", "created_at": time.time(),
    }
    env2 = em.from_social_row(social_row)
    assert env2.evidence_type == "social_post"
    assert env2.ticker == "AAPL"
    assert "bullish" in env2.tags


def test_source_quality_ranks_regulatory_above_social():
    assert em.source_quality("sec_edgar") > em.source_quality("reddit_public")
    assert em.source_quality("fred") > em.source_quality("x_public")
    assert em.source_quality("unknown_provider") == 0.5
