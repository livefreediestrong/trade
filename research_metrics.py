"""Descriptive research calculations; no broker or trading-policy dependencies."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
import random
import statistics

from desk_workbench import finite, timestamp
import paper_loop

VERSION = "research-metrics-v1"


def number(value, name: str, low: float, high: float) -> float:
    out = finite(value)
    if isinstance(value, bool) or out is None or not low <= out <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return out


def expectancy(rows: list[dict], friction: float = 0) -> dict:
    """Deterministic cluster bootstrap: resample whole session days, not individual trades."""
    groups = defaultdict(list)
    for row in rows:
        val, ts = finite(row.get("outcome_executable_move_bps")), timestamp(row.get("ts"))
        if val is None or ts is None:
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).astimezone(paper_loop.NY_TZ).date().isoformat()
        groups[day].append(val - friction)
    values = [x for group in groups.values() for x in group]
    interval = None
    if len(groups) >= 5:
        rng = random.Random(314159)
        clusters = [(sum(v), len(v)) for v in groups.values()]
        draws = []
        for _ in range(1000):
            sample = rng.choices(clusters, k=len(clusters))
            draws.append(sum(s for s, n in sample)/sum(n for s, n in sample))
        draws.sort()
        interval = [draws[24], draws[974]]
    return {"outcomes": len(values), "session_days": len(groups),
            "mean_net_bps": statistics.mean(values) if values else None,
            "day_cluster_95_interval_bps": interval,
            "status": "descriptive_only" if interval else "insufficient_independent_days",
            "note": "Resamples whole days; at least five days needed to display an exploratory interval. Cross-day regime dependence remains. Twenty outcomes alone do not establish an edge."}


def walk_forward(rows: list[dict], friction: float = 0) -> dict:
    excluded_confidence = sum(finite(r.get("confidence")) is None or not 0 <= finite(r.get("confidence")) <= 1 for r in rows)
    rows = [dict(r, confidence=finite(r["confidence"])) for r in rows if finite(r.get("confidence")) is not None and 0 <= finite(r["confidence"]) <= 1]
    rows = sorted(rows, key=lambda r: (timestamp(r["ts"]), str(r["id"])))
    windows = []
    # Four prespecified expanding windows. Only prior outcomes choose each threshold.
    step = len(rows) // 6
    for i in range(2, 6):
        start, stop = i * step, (i + 1) * step if i < 5 else len(rows)
        test = rows[start:stop]
        if not test:
            continue
        boundary = timestamp(test[0]["ts"])
        train = [r for r in rows[:start] if timestamp(r["outcome_ts"]) < boundary]
        trials = []
        for threshold in (0., .6, .7, .8):
            selected = [r for r in train if (finite(r.get("confidence")) or 0) >= threshold]
            trials.append({"threshold": threshold, **expectancy(selected, friction)})
        eligible = [t for t in trials if t["outcomes"] >= 30 and t["session_days"] >= 5]
        chosen = max(eligible, key=lambda t: (t["mean_net_bps"], -t["threshold"])) if eligible else None
        selected = [r for r in test if chosen and (finite(r.get("confidence")) or 0) >= chosen["threshold"]]
        windows.append({"train_ids": [r["id"] for r in train], "test_ids": [r["id"] for r in test],
                        "purged": start-len(train), "trials": trials,
                        "chosen_threshold": chosen["threshold"] if chosen else None, "test": expectancy(selected, friction),
                        "no_filter_baseline": expectancy(test, friction), "cash_baseline_bps": 0})
    bins = []
    for lo, hi in ((0, .5), (.5, .7), (.7, .9), (.9, 1.000001)):
        sample = [r for r in rows if finite(r.get("confidence")) is not None and lo <= r["confidence"] < hi]
        bins.append({"confidence_range": [lo, min(hi, 1)], "count": len(sample),
                     "observed_positive_net_fraction": statistics.mean(r["outcome_executable_move_bps"] > friction for r in sample) if sample else None})
    return {"version": VERSION, "windows": windows, "overall": expectancy(rows, friction), "calibration_bins": bins, "excluded_confidence": excluded_confidence,
            "note": "All training variants retained; purged chronological expanding windows. Confidence is a model score, not a calibrated probability. Cash means zero interest over the short horizon. Trade observations are not portfolio returns; matched market/sector baselines require recorded benchmark prices."}


def quote_benchmark(quote: dict, captured_at: str) -> dict:
    """Keep only evidence actually present when captured, including quote provenance."""
    market = timestamp(quote.get("market_time"))
    capture = timestamp(captured_at)
    bid, ask = finite(quote.get("bid")), finite(quote.get("ask"))
    valid = market is not None and capture is not None and 0 <= capture-market <= 30 and quote.get("fresh") is True and not quote.get("mock") and bool(quote.get("source"))
    spread = valid and bid is not None and ask is not None and 0 < bid <= ask
    return {"captured_at": captured_at, "market_time": quote.get("market_time"), "source": quote.get("source"),
            "bid": bid if spread else None, "ask": ask if spread else None,
            "mid": (bid+ask)/2 if spread else None, "last": finite(quote.get("price")) if valid else None,
            "fresh_at_capture": valid, "spread_bps": (ask-bid)/((ask+bid)/2)*10000 if spread else None}


def execution_costs(rows: list[dict], signals: list[dict]) -> dict:
    by_id = {str(s.get("id")): s for s in signals}
    groups, details, orders = defaultdict(list), [], defaultdict(list)
    for row in rows:
        if not row.get("verified") or row.get("superseded_by") or not isinstance(row.get("paper_mode"), bool):
            continue
        signal = by_id.get(str(row.get("order_ref"))) or {}
        identity = signal.get("review_identity") or {}
        matched = (identity.get("account_id") == row.get("account_id") and identity.get("paper_mode") is row["paper_mode"]
                   and signal.get("ticker") == row.get("ticker") and row.get("asset_type") == "STK")
        benchmarks = signal.get("execution_benchmarks", {}) if matched else row.get("execution_benchmarks", {})
        ts, px, qty = timestamp(row.get("ts")), finite(row.get("price")), finite(row.get("shares"))
        if ts is None or not px or not qty or qty <= 0 or row.get("side") not in ("buy", "sell"):
            continue
        side = 1 if row["side"] == "buy" else -1
        item = {"execution_id": row.get("execution_id"), "ticker": row.get("ticker"), "side": row["side"],
                "shares": qty, "price": px, "currency": row.get("currency"), "commission": finite(row.get("commission")),
                "commission_currency": row.get("commission_currency"), "decision_slippage_bps": None, "arrival_slippage_bps": None,
                "decision_last_difference_bps": None, "arrival_last_difference_bps": None,
                "latency_sec": None, "spread_bps": None, "strategy": signal.get("llm_model") if matched else row.get("strategy"),
                "notional": px*qty, "order_type": (signal.get("review_order") or {}).get("type") if matched else row.get("order_type")}
        for name in ("decision", "arrival"):
            q = benchmarks.get(name) or {}
            t, mid = timestamp(q.get("captured_at")), finite(q.get("mid"))
            last_price = finite(q.get("last"))
            if q.get("fresh_at_capture") and t is not None and 0 <= ts-t and last_price and last_price > 0:
                item[name+"_last_difference_bps"] = (px-last_price)/last_price*10000*side
                if name == "arrival":
                    item["latency_sec"] = ts-t
            if q.get("fresh_at_capture") and t is not None and 0 <= ts-t and mid and mid > 0:
                item[name+"_slippage_bps"] = (px-mid)/mid*10000*side
                if name == "arrival":
                    item.update(latency_sec=ts-t, spread_bps=q.get("spread_bps"))
        local = datetime.fromtimestamp(ts, timezone.utc).astimezone(paper_loop.NY_TZ)
        segment = "outside_session" if not paper_loop.is_rth(local) else "opening_hour" if local.hour < 10 or local.hour == 10 and local.minute < 30 else "closing_hour" if local.hour >= 15 else "midday"
        scope = "broker_paper" if row["paper_mode"] else "live"
        item["size_bucket"] = "under_$1k" if px*qty < 1000 else "$1k_to_$10k" if px*qty < 10000 else "$10k_plus"
        item["spread_bucket"] = "unknown" if item["spread_bps"] is None else "under_10_bps" if item["spread_bps"] < 10 else "10_bps_plus"
        group = (scope, str(row.get("account_id")), item["currency"], item["strategy"] or "unlinked", segment)
        groups[group].append(item)
        if matched:
            orders[(scope, row.get("account_id"), row.get("order_ref"))].append(item)
        details.append(dict(item, scope=scope, session_segment=segment))
    summary = []
    for (scope, account, currency, strategy, segment), items in groups.items():
        measured = [i for i in items if i["arrival_slippage_bps"] is not None]
        summary.append({"scope": scope, "account_suffix": account[-4:], "currency": currency, "strategy": strategy,
                        "session_segment": segment, "executions": len(items), "benchmark_coverage": len(measured),
                        "notional_weighted_arrival_bps": sum(i["arrival_slippage_bps"]*i["price"]*i["shares"] for i in measured)/sum(i["price"]*i["shares"] for i in measured) if measured else None,
                        "fees_reported": sum(i["commission"] for i in items if i["commission"] is not None and i["commission_currency"] == currency),
                        "fees_missing_or_other_currency": sum(i["commission"] is None or i["commission_currency"] != currency for i in items)})
    fill_rates = []
    for (_, _, reference), items in orders.items():
        signal = by_id[str(reference)]
        intended = finite((signal.get("review_order") or {}).get("shares"))
        if intended and intended > 0:
            filled = sum(i["shares"] for i in items)
            fill_rates.append({"signal_id": reference, "intended": intended, "observed_filled": filled,
                               "observed_fill_fraction": filled/intended, "status": signal.get("status"), "partial": filled < intended})
    return {"groups": summary, "executions": details[-200:], "linked_fill_rates": fill_rates,
            "note": "Positive differences are adverse for either side. Midpoint slippage and last-trade price differences are separate measures. Actual and broker-paper records stay separate. Missing quotes are never reconstructed. Liquidity and missed-opportunity costs require contemporaneous evidence; partial broker history cannot establish account-wide fill rate."}


def option_value(spot: float, strike: float, days: float, vol: float, rate: float, dividend: float, right: str) -> float:
    t = days/365
    if t <= 0:
        return max(0, spot-strike if right == "call" else strike-spot)
    sd, kd = spot*math.exp(-dividend*t), strike*math.exp(-rate*t)
    if vol <= 0:
        return max(0, sd-kd if right == "call" else kd-sd)
    d1 = (math.log(spot/strike)+(rate-dividend+vol*vol/2)*t)/(vol*math.sqrt(t))
    d2 = d1-vol*math.sqrt(t)
    cdf = lambda x: (1+math.erf(x/math.sqrt(2)))/2
    return sd*cdf(d1)-kd*cdf(d2) if right == "call" else kd*cdf(-d2)-sd*cdf(-d1)


def option_scenarios(body: dict) -> dict:
    spot = number(body.get("spot"), "Underlying price", .01, 1e6)
    days = number(body.get("days"), "Days remaining", 0, 3650)
    vol = number(body.get("iv_pct"), "Volatility percent", .01, 500)/100
    rate = number(body.get("rate_pct", 0), "Interest rate percent", -10, 50)/100
    dividend = number(body.get("dividend_pct", 0), "Dividend yield percent", 0, 50)/100
    legs = body.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= 4:
        raise ValueError("Use one to four option legs")
    parsed = []
    for leg in legs:
        if not isinstance(leg, dict) or leg.get("right") not in ("call", "put") or leg.get("side") not in ("buy", "sell"):
            raise ValueError("Each leg needs call/put and buy/sell")
        strike = number(leg.get("strike"), "Strike", .01, 1e6)
        count = number(leg.get("contracts", 1), "Whole contracts", 1, 1000)
        if count != int(count):
            raise ValueError("Contracts must be whole numbers")
        parsed.append((strike, count*(1 if leg["side"] == "buy" else -1), leg["right"]))
    def value(s, d, v):
        return sum(100*n*option_value(s, k, d, v, rate, dividend, right) for k, n, right in parsed)
    base = value(spot, days, vol)
    scenarios = [{"underlying_change_pct": move, "elapsed_days": elapsed, "iv_change_points": dv,
                  "model_value_usd": value(spot*(1+move/100), max(0, days-elapsed), max(.0001, vol+dv/100)),
                  "change_from_model_usd": value(spot*(1+move/100), max(0, days-elapsed), max(.0001, vol+dv/100))-base}
                 for elapsed in sorted({0, min(7, days), days}) for dv in (-10, 0, 10) for move in (-10, -5, 0, 5, 10)]
    return {"baseline_model_value_usd": base, "scenarios": scenarios, "assumptions": body,
            "note": "European Black-Scholes with continuous dividend yield and assumed standard 100-share contracts; user-entered assumptions. These are theoretical value changes, not executable prices or trade P&L. Fees, discrete dividends, early exercise, assignment, borrow, and adjusted contracts are not modeled."}
