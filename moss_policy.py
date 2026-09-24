"""Paper-only workday policy and auditable, as-of empirical Bayes estimates."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import math

import paper_loop
from desk_workbench import finite, fingerprint, timestamp
from trade_planner import number

VERSION = "moss-intraday-v1"
DEFAULTS = {
    "enabled": False, "personality": "stoic", "interval_sec": 120, "universe": "focus",
    "horizon_min": 10, "max_model_calls": 200, "model_budget_usd": 5.0, "candidates_per_cycle": 6,
    "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "META", "TSLA"],
    "base_order_usd": 5.0, "max_order_usd": 20.0, "max_total_exposure_pct": 8.0,
    "max_positions": 4, "max_trades_per_day": 40, "max_daily_loss_pct": 2.0,
    "max_quote_age_sec": 30, "max_order_latency_sec": 30,
    "min_daily_dollar_volume": 50_000_000.0, "min_realized_volatility_pct": .05,
    "flatten_before_close": True,
}


def settings(cfg):
    return dict(DEFAULTS, **(cfg.get("moss_paper") or {}))


def validate(body):
    if not isinstance(body, dict) or set(body) - set(DEFAULTS):
        raise ValueError("Use only the displayed Moss paper settings")
    value = dict(DEFAULTS, **body)
    for key in ("enabled", "flatten_before_close"):
        if not isinstance(value[key], bool):
            raise ValueError(f"{key} must be true or false")
    if value["personality"] not in ("stoic", "empirical_bayes"):
        raise ValueError("Choose stoic or empirical_bayes")
    if value["universe"] not in ("focus", "broad_us"):
        raise ValueError("Choose focus or broad_us for the research universe")
    limits = {"interval_sec": (60, 1800), "horizon_min": (5, 60), "candidates_per_cycle": (2, 8),
              "max_model_calls": (1, 400), "model_budget_usd": (.01, 100),
              "base_order_usd": (.01, 5000), "max_order_usd": (.01, 5000),
              "max_total_exposure_pct": (1, 25), "max_positions": (1, 10),
              "max_trades_per_day": (1, 200), "max_daily_loss_pct": (.1, 5),
              "max_quote_age_sec": (1, 60), "max_order_latency_sec": (1, 60),
              "min_daily_dollar_volume": (1_000_000, 1e12),
              "min_realized_volatility_pct": (0, 5)}
    integers = {"interval_sec", "horizon_min", "max_model_calls", "max_positions", "max_trades_per_day", "candidates_per_cycle"}
    for key, bounds in limits.items():
        n = number(value[key], key, *bounds)
        if key in integers and n != int(n):
            raise ValueError(f"{key} must be a whole number")
        value[key] = int(n) if key in integers else float(n)
    if value["base_order_usd"] > value["max_order_usd"]:
        raise ValueError("Base paper amount cannot exceed the maximum")
    if not isinstance(value["symbols"], list) or not 1 <= len(value["symbols"]) <= 500 or not all(isinstance(s,str) for s in value["symbols"]):
        raise ValueError("Choose 1–500 stock/ETF focus symbols")
    cleaned = paper_loop.equity_loop_symbols(value["symbols"])
    if len(cleaned) != len(set(value["symbols"])):
        raise ValueError("Use uppercase US stock symbols")
    value["symbols"] = cleaned
    return value


def aware(value):
    stamp = timestamp(value)
    return datetime.fromtimestamp(stamp, timezone.utc) if stamp is not None else None


def session(day):
    close = paper_loop.session_close_time(day)
    if close is None:
        return None, None
    return (datetime.combine(day, paper_loop.RTH_OPEN, paper_loop.NY_TZ),
            datetime.combine(day, close, paper_loop.NY_TZ))


def quote_error(quote, now, max_age):
    q = quote or {}
    price, observed = finite(q.get("price")), aware(q.get("market_time"))
    if not price or price <= 0 or not observed or not q.get("fresh"):
        return "missing_or_unverified_quote"
    if not q.get("source") or any(word in str(q.get("source", "")).lower() for word in ("mock", "demo", "synthetic", "fixture")):
        return "synthetic_quote"
    age = (now - observed).total_seconds()
    if age < 0:
        return "future_quote"
    if age > max_age:
        return "stale_quote"
    if not paper_loop.is_rth(observed) or not paper_loop.is_rth(now):
        return "out_of_session_quote"
    return None


def assess(analysis, cfg, now):
    """Validate measurements, then rank liquid completed-bar volatility."""
    p = settings(cfg)
    error = analysis.get("error") or quote_error(analysis.get("quote"), now, p["max_quote_age_sec"])
    intraday = analysis.get("intraday") or {}
    as_of = aware(intraday.get("completed_bar_at"))
    if not error and (not intraday.get("fresh") or not as_of or as_of > now or not paper_loop.is_rth(as_of)):
        error = "unverified_intraday_bars"
    if not error and (now - as_of).total_seconds() > 600:
        error = "stale_intraday_bars"
    vol = finite(intraday.get("realized_volatility_pct"))
    dollar_volume = finite((analysis.get("volume") or {}).get("avg_30d"))
    price = finite((analysis.get("quote") or {}).get("price"))
    dollar_volume = dollar_volume * price if dollar_volume and price else None
    if not error and (dollar_volume is None or dollar_volume < p["min_daily_dollar_volume"]):
        error = "insufficient_verified_liquidity"
    if not error and (vol is None or vol < p["min_realized_volatility_pct"]):
        error = "insufficient_completed_bar_volatility"
    if not error and ((analysis.get("checks") or {}).get("halt_or_gap") or analysis.get("verdict") == "AVOID"):
        error = "halt_gap_or_avoid"
    spread = finite(intraday.get("spread_bps"))
    if not error and spread is not None and (spread < 0 or spread > 30):
        error = "wide_or_invalid_spread"
    score = min(4, math.log10(max(1, (dollar_volume or 0) / 1e6))) * min(3, vol or 0)
    vwap = finite(intraday.get("vwap"))
    setup = "above_vwap" if vwap and price and price >= vwap else "below_vwap" if vwap else "vwap_unknown"
    return {"ok": not bool(error), "reason": str(error) if error else None,
            "score": round(score, 6), "setup": setup, "daily_dollar_volume": dollar_volume,
            "realized_volatility_pct": vol, "spread_bps": spread,
            "as_of": now.isoformat(), "policy_version": VERSION}


def cohort(event):
    return (event.get("ticker"), event.get("llm_model"), event.get("prompt_version"),
            event.get("horizon_min"), event.get("moss_setup"), event.get("intended_side"),
            event.get("moss_personality"), event.get("slip_bps"), event.get("fee_bps"))


def qualified(events, now):
    """Return chronologically available independent outcomes and exclusion reasons."""
    reasons, accepted, seen, occupied = Counter(), [], set(), {}
    for e in sorted(events, key=lambda row: timestamp(row.get("ts")) or 0):
        why = None
        start, end = aware(e.get("ts")), aware(e.get("outcome_ts"))
        entry_tick, exit_tick = aware((e.get("quote") or {}).get("market_time")), aware(e.get("outcome_market_time"))
        net, horizon = finite(e.get("outcome_executable_move_bps")), finite(e.get("horizon_min"))
        prices = [finite((e.get("quote") or {}).get("price")), finite(e.get("mid")), finite(e.get("outcome_mid"))]
        fill = e.get("moss_fill") or {}
        fill_at, completed = aware(fill.get("ts")), aware(e.get("decision_completed_at"))
        quote_age = finite(e.get("moss_max_quote_age_sec", 30))
        latency_limit = finite(e.get("moss_max_order_latency_sec", 30))
        key = e.get("id")
        if not key or key in seen:
            why = "duplicate_or_missing_id"
        elif e.get("moss_policy_version") != VERSION or e.get("source") != "moss_paper":
            why = "different_policy_or_source"
        elif e.get("mock") or e.get("brain_mode") == "mock" or str(e.get("llm_model", "")).startswith("mock") or e.get("routed"):
            why = "mock_or_routed"
        elif e.get("error") or e.get("execution_block") or e.get("moss_execution_error"):
            why = "errored_or_rejected"
        elif not e.get("input_hash") or not e.get("llm_model") or not e.get("prompt_version") or not (e.get("moss_quality") or {}).get("ok"):
            why = "missing_provenance"
        elif any(v is None or v <= 0 for v in prices) or quote_age is None or latency_limit is None or not 0 < quote_age <= 60 or not 0 < latency_limit <= 60:
            why = "invalid_prices_or_thresholds"
        elif not (e.get("quote") or {}).get("fresh") or quote_error(e.get("quote"), start or now, quote_age):
            why = "unverified_entry_quote"
        elif e.get("intended_side") not in ("buy", "sell") or e.get("outcome_status") != "scored" or e.get("scoring_version") != "horizon-net-v2" or net is None:
            why = "unscored_or_abstention"
        elif not all((start, end, entry_tick, exit_tick, horizon)) or not start < end <= now or entry_tick > start or exit_tick > end:
            why = "future_or_invalid_timestamps"
        elif not all(paper_loop.is_rth(t) for t in (start, end, entry_tick, exit_tick)):
            why = "out_of_session"
        elif quote_error(e.get("outcome_quote"), end, 30):
            why = "unverified_outcome_quote"
        elif not 0 <= (start - entry_tick).total_seconds() <= quote_age:
            why = "stale_entry_tick"
        elif not 0 <= (exit_tick - start).total_seconds() - horizon * 60 <= 120 or (end - exit_tick).total_seconds() > 30:
            why = "wrong_horizon_or_stale_outcome"
        elif e.get("moss_execution_attempted") and not e.get("moss_fill"):
            why = "unfilled_execution"
        elif (fill and (finite(fill.get("latency_sec")) is None or
              not 0 <= finite(fill.get("latency_sec")) <= latency_limit or
              not fill_at or not completed or not start <= completed <= fill_at <= end or
              not paper_loop.is_rth(fill_at) or not 0 <= (fill_at-completed).total_seconds() <= latency_limit or
              (finite(fill.get("price")) or 0) <= 0 or (finite(fill.get("shares")) or 0) <= 0)):
            why = "stale_execution"
        # A second prediction for the same ticker before the first horizon ended
        # is not another independent observation, even across strategy cohorts.
        elif start < occupied.get(e.get("ticker"), datetime.min.replace(tzinfo=timezone.utc)):
            why = "overlapping_ticker_horizon"
        if why:
            reasons[why] += 1
            continue
        seen.add(key)
        occupied[e.get("ticker")] = exit_tick
        accepted.append(e)
    return accepted, dict(reasons)


def fit(events, now=None):
    now = now or datetime.now(timezone.utc)
    accepted, excluded = qualified(events, now)
    groups = defaultdict(list)
    for e in accepted:
        groups[cohort(e)].append(e)
    rows = []
    for key, samples in groups.items():
        # Leave this ticker/setup out of its own empirical prior. Match actual
        # model, prompt, horizon, direction, personality and cost assumptions.
        peer_groups = [values for other, values in groups.items() if other != key and
                       tuple(other[i] for i in (1, 2, 3, 5, 6, 7, 8)) == tuple(key[i] for i in (1, 2, 3, 5, 6, 7, 8))]
        peers = [e for group in peer_groups for e in group]
        empirical = len(peer_groups) >= 2 and len(peers) >= 10
        baseline = sum(e["outcome_executable_move_bps"] > 0 for e in peers) / len(peers) if empirical else .5
        baseline = max(.01, min(.99, baseline))
        prior_mean = sum(e["outcome_executable_move_bps"] for e in peers) / len(peers) if empirical else 0.
        strength = 10.
        if empirical:
            rates = [sum(e["outcome_executable_move_bps"] > 0 for e in g) / len(g) for g in peer_groups]
            between = sum((r - baseline) ** 2 for r in rates) / len(rates)
            noise = sum(baseline * (1 - baseline) / len(g) for g in peer_groups) / len(peer_groups)
            strength = max(2., min(40., baseline * (1 - baseline) / max(.0001, between - noise) - 1))
        values = [e["outcome_executable_move_bps"] for e in samples]
        n, wins = len(values), sum(v > 0 for v in values)
        raw_rate, mean = wins / n, sum(values) / n
        eb_fraction = n / (n + strength)
        alpha_fraction = 1. if n >= 20 else eb_fraction if n < 10 else eb_fraction + ((n - 10) / 10) * (1 - eb_fraction)
        estimate = baseline + alpha_fraction * (raw_rate - baseline)
        expectancy = prior_mean + alpha_fraction * (mean - prior_mean)
        z = 1.96
        center = (raw_rate + z*z / (2*n)) / (1 + z*z/n)
        radius = z * math.sqrt(raw_rate*(1-raw_rate)/n + z*z/(4*n*n)) / (1+z*z/n)
        half = n // 2
        drift = abs(sum(v > 0 for v in values[:half])/half - sum(v > 0 for v in values[half:])/len(values[half:])) if half else 1.
        stable = n >= 20 and drift <= .15 and center-radius > .5 and mean > 0
        rows.append({"key": list(key), "ticker": key[0], "setup": key[4], "side": key[5],
                     "samples": n, "win_rate": raw_rate, "baseline_win_rate": baseline,
                     "posterior_win_rate": estimate, "expectancy_bps": expectancy,
                     "raw_expectancy_bps": mean, "alpha_fraction": alpha_fraction,
                     "prior_alpha": baseline*strength, "prior_beta": (1-baseline)*strength,
                     "prior_source": "empirical_peers" if empirical else "neutral_fallback",
                     "win_rate_lower_95": center-radius, "win_rate_upper_95": center+radius,
                     "half_sample_win_rate_drift": drift, "stable_for_paper_scaling": stable,
                     "weight": max(.5, min(1.5 if expectancy > 0 else 1., estimate / baseline)),
                     "paper_growth_fraction": alpha_fraction if stable else 0.})
    return {"version": VERSION, "trained_at": now.isoformat(), "qualified_samples": len(accepted),
            "excluded": excluded, "weights": rows,
            "evidence_hash": fingerprint([(e["id"], e["outcome_ts"], e["outcome_executable_move_bps"]) for e in accepted]),
            "note": "As-of descriptive estimates; no proven edge. Twenty samples remove the shrinkage discount, not the paper risk limits."}
