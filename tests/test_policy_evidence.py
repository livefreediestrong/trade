from datetime import datetime, timedelta, timezone
import copy
import policy_evidence as p


def event(index=0, move=-20):
    at = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)+timedelta(hours=index)
    facts = {"research_evidence": {"items": [{"published_at": (at-timedelta(minutes=5)).isoformat()}]}}
    return {"id": str(index), "ts": at.isoformat(), "inputs": facts,
            "policy_receipt": p.capture({"risk": 1, "api_key": "SECRET", "broker_identity": {"account": "HIDDEN"}}, facts, at.isoformat(), "fixture"),
            "intended_side": "buy", "verdict": "PASS", "llm_model": "fixture", "prompt_version": "v1", "horizon_min": 20,
            "outcome_move_bps": move, "outcome_cost_bps": 5,
            "outcome_market_time": (at+timedelta(minutes=20)).isoformat(), "outcome_due_ts": (at+timedelta(minutes=20)).isoformat(),
            "outcome_status": "scored", "scoring_version": "horizon-net-v2"}


def test_receipt_is_immutable_and_omits_secrets():
    e = event()
    assert "api_key" not in e["policy_receipt"]["policy"]["config"]
    assert "broker_identity" not in e["policy_receipt"]["policy"]["config"]
    e["policy_receipt"]["policy"]["config"]["risk"] = 10
    assert p.evaluate([e])["exclusions"] == {"missing_or_changed_prospective_receipt": 1}


def test_later_losses_stay_no_edge_with_drawdown_and_baselines():
    r = p.evaluate([event(i) for i in range(60)])
    group = r["groups"][0]
    assert group["state"] == "no_edge"
    assert group["earlier"]["samples"] == 36 and group["later"]["samples"] == 24
    assert group["later"]["mean_net_bps"] == -25
    assert group["later"]["sequence_drawdown_bps"] == 600


def test_no_future_news_or_backfilled_old_policy():
    e = event()
    e["inputs"]["research_evidence"]["items"][0]["published_at"] = e["outcome_market_time"]
    e["policy_receipt"]["inputs_hash"] = p.digest(e["inputs"])
    older = event(1)
    older.pop("policy_receipt")
    assert p.evaluate([e, older])["exclusions"] == {"undated_or_future_source": 1, "missing_or_changed_prospective_receipt": 1}


def test_overlapping_windows_and_duplicate_ids_do_not_inflate_samples():
    e = event()
    overlap = copy.deepcopy(e)
    overlap["id"] = "overlap"
    r = p.evaluate([e, e, overlap])
    assert r["exclusions"] == {"duplicate_or_missing_id": 1, "overlapping_horizon": 1}
    assert r["groups"][0]["state"] == "insufficient_evidence"


def test_changed_policy_separates_cohorts():
    rows = [event(i) for i in range(60)]
    for e in rows[30:]:
        e["policy_receipt"] = p.capture({"risk": 2}, e["inputs"], e["ts"], "fixture")
    assert len(p.evaluate(rows)["groups"]) == 2
    assert all(g["state"] == "insufficient_evidence" for g in p.evaluate(rows)["groups"])
