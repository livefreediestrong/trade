"""Provider-qualified global discovery and history, isolated from execution.

Yahoo symbols are opaque provider identifiers: VOD.L is not the US class share
VOD-L. No US-only price fallback, dollar conversion or exchange calendar is used.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import re
import threading
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from flask import Blueprint, jsonify, request
from desk_workbench import finite, fingerprint

TYPES = {"EQUITY": "equity", "ETF": "etf", "INDEX": "index", "CURRENCY": "fx",
         "CRYPTOCURRENCY": "crypto", "FUTURE": "future", "MUTUALFUND": "mutual_fund"}
US_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "BATS", "PNK", "OQB", "OQX", "NAS", "SNP", "DJI", "CBO"}
GROUPS = {
    "US & sectors": "SPY QQQ IWM DIA RSP VTI XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC SMH",
    "US indices": "^GSPC ^DJI ^IXIC ^RUT ^VIX",
    "World equities": "VEA VWO ACWI EFA EEM EWJ EWZ INDA EWC EWA EWY EWT EWW EZA VGK FXI",
    "World indices": "^GSPC ^DJI ^IXIC ^RUT ^VIX ^FTSE ^GDAXI ^FCHI ^STOXX50E ^N225 ^HSI 000001.SS ^NSEI ^BSESN ^AXJO ^GSPTSE ^BVSP ^MXX",
    "Global listings": "7203.T 6758.T 9984.T 0700.HK 9988.HK 600519.SS 000001.SZ RELIANCE.NS TCS.NS SAP.DE ASML.AS MC.PA NOVO-B.CO NESN.SW VOD.L BHP.AX RY.TO VALE3.SA NPN.JO",
    "Currencies": "EURUSD=X GBPUSD=X USDJPY=X USDCHF=X USDCAD=X AUDUSD=X NZDUSD=X EURGBP=X USDCNY=X USDINR=X USDMXN=X USDBRL=X USDZAR=X",
    "Crypto": "BTC-USD ETH-USD SOL-USD XRP-USD ADA-USD DOGE-USD AVAX-USD LINK-USD LTC-USD",
    "Futures references": "ES=F NQ=F YM=F RTY=F CL=F BZ=F NG=F GC=F SI=F HG=F ZC=F ZW=F ZS=F ZB=F ZN=F ZT=F KC=F CT=F SB=F",
    "Rates & fixed income": "^IRX ^FVX ^TNX ^TYX SHY IEF TLT TIP LQD HYG BND EMB MUB BIL SGOV",
    "Funds & real assets": "VTSAX VFIAX VTWAX GLD SLV USO UNG DBA VNQ VNQI IYR",
}
_CACHE, _LOCK = {}, threading.RLock()


def symbol(value):
    value = str(value or "").strip().upper()
    if value.startswith("YF:"):
        value = value[3:]
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]{0,31}", value):
        raise ValueError("Use an exact provider symbol, for example 7203.T, EURUSD=X or BTC-USD")
    return value


def fetch(path, params):
    response = requests.get("https://query1.finance.yahoo.com/" + path, params=params,
                            headers={"User-Agent": "Mozilla/5.0 (Tomahawk research)"}, timeout=10)
    response.raise_for_status()
    if len(response.content) > 4_000_000:
        raise ValueError("Provider response exceeded the research limit")
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Provider returned an invalid research response")
    return result


def cached(key, seconds, fn):
    with _LOCK:
        item = _CACHE.get(key)
        if item and 0 <= time.monotonic() - item[0] < seconds:
            return copy.deepcopy(item[1])
    result = fn()
    with _LOCK:
        if len(_CACHE) >= 200:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result


def search(query, directory, scope="us"):
    if scope not in ("us", "global"):
        raise ValueError("Choose us or global market scope")
    query = str(query or "").strip()
    if not 1 <= len(query) <= 80:
        raise ValueError("Search by a company, fund, index or exact symbol (1–80 characters)")
    matches, seen, error = [], set(), None
    for row in directory:
        if query.upper() == row["symbol"] or query.lower() in str(row.get("name", "")).lower():
            sym = row["symbol"].replace(".", "-")
            matches.append({"id": "YF:"+sym, "symbol": sym, "name": row.get("name"),
                            "asset_type": "etf" if row.get("etf") else "equity", "exchange": row.get("exchange"),
                            "source": "Nasdaq directory", "verified": False})
            seen.add(sym)
            if len(matches) >= 20:
                break
    try:
        data = cached(("search", query.casefold()), 300,
                      lambda: fetch("v1/finance/search", {"q": query, "quotesCount": 20, "newsCount": 0}))
        for row in data.get("quotes", []):
            if row.get("quoteType") not in TYPES:
                continue
            if scope == "us" and (row.get("exchange") not in US_EXCHANGES or row.get("quoteType") in ("CURRENCY", "CRYPTOCURRENCY", "FUTURE")):
                continue
            try:
                sym = symbol(row.get("symbol"))
            except ValueError:
                continue
            if sym in seen:
                continue
            seen.add(sym)
            matches.append({"id": "YF:"+sym, "symbol": sym, "name": row.get("longname") or row.get("shortname"),
                            "asset_type": TYPES[row["quoteType"]], "exchange": row.get("exchDisp") or row.get("exchange"),
                            "source": "Yahoo search", "verified": False})
    except (requests.RequestException, ValueError, KeyError, TypeError):
        error = "Global search unavailable; retained directory results remain available. Exact-symbol lookup still works."
    return {"ok": True, "results": matches[:40], "error": error,
            "note": "Search matches are candidates. Open one to verify identity, currency and historical data."}


def parse_history(expected, payload, now=None):
    now = now or datetime.now(timezone.utc)
    rows = (payload.get("chart") or {}).get("result") or []
    if not rows:
        raise ValueError("No verified history returned for that provider symbol")
    result = rows[0]
    meta = result.get("meta") or {}
    if str(meta.get("symbol") or "").upper() != expected:
        raise ValueError("Provider returned a different instrument; refusing to substitute it")
    asset = TYPES.get(meta.get("instrumentType"))
    currency, tz = meta.get("currency"), meta.get("exchangeTimezoneName")
    if not asset or not currency or not tz or not meta.get("exchangeName"):
        raise ValueError("Instrument type, currency, exchange or timezone is unverified")
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Provider exchange timezone is unrecognized") from exc
    prices = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    candles, rejected, seen = [], 0, set()
    for i, raw_time in enumerate(result.get("timestamp") or []):
        stamp = finite(raw_time)
        values = {key: finite((prices.get(key) or [])[i]) if i < len(prices.get(key) or []) else None
                  for key in ("open", "high", "low", "close", "volume")}
        o, h, l, c = (values[k] for k in ("open", "high", "low", "close"))
        if (stamp is None or stamp <= 0 or stamp > now.timestamp() or stamp in seen or
                any(v is None for v in (o, h, l, c)) or l > min(o, c) or h < max(o, c) or l > h or
                (asset != "future" and min(o, h, l, c) <= 0) or
                (values["volume"] is not None and values["volume"] < 0)):
            rejected += 1
            continue
        seen.add(stamp)
        market = datetime.fromtimestamp(stamp, timezone.utc)
        candles.append({"date": market.isoformat(), **values,
                        "complete": market.astimezone(zone).date() < now.astimezone(zone).date()})
    candles.sort(key=lambda row: row["date"])
    if not candles:
        raise ValueError("No valid historical candles; no replacement or synthetic prices were generated")
    tick_time, price = finite(meta.get("regularMarketTime")), finite(meta.get("regularMarketPrice"))
    if tick_time is None or not 0 < tick_time <= now.timestamp() or price is None or (price <= 0 and asset != "future"):
        tick_time, price = None, None
    unit = "index points / provider scale" if asset == "index" else currency
    if asset == "future":
        unit += " quoted units; contract multiplier unverified"
    if currency == "GBp":
        unit = "GBp (pence, not pounds)"
    instrument = {"id": "YF:"+expected, "symbol": expected, "provider": "Yahoo Finance",
                  "name": meta.get("longName") or meta.get("shortName") or expected,
                  "asset_type": asset, "currency": currency, "quote_unit": unit,
                  "exchange": meta.get("fullExchangeName") or meta["exchangeName"],
                  "timezone": tz, "verified_at": now.isoformat(), "verified": True,
                  "execution": False, "paper_execution": False,
                  "capability": "Historical research only; broker instrument qualification is separate"}
    return {"ok": True, "symbol": expected, "instrument": instrument, "candles": candles[-120:],
            "chart_available": True, "interval": "1d", "source": "Yahoo Chart API",
            "retrieved_at": now.isoformat(), "market_time": datetime.fromtimestamp(tick_time, timezone.utc).isoformat() if tick_time else None,
            "quote_age_sec": round(now.timestamp()-tick_time) if tick_time else None,
            "price": price, "quote_unit": unit, "quality": "Historical / potentially delayed; not an executable quote",
            "rejected_bars": rejected, "session": f"{tz}; current local date excluded from completed-bar analysis",
            "news": [], "theses": [], "display_only": True, "error": None,
            "notes": ["Raw OHLC; splits, dividends and futures rolls can break price comparability. Not a total-return backtest.",
                      "No executable bid/ask, venue permissions, full historical membership or licensed full-market stream is implied."]}


def history(value):
    sym = symbol(value)
    return cached(("history", sym), 60,
                  lambda: parse_history(sym, fetch("v8/finance/chart/"+quote(sym, safe=""), {"range": "6mo", "interval": "1d"})))


def load_saved(desk):
    return desk._load_json(desk.DATA_DIR/"global_research.json", {"instruments": [], "watchlist": [], "cursor": 0})


def save_observation(desk, result, follow=False):
    from desk_operations import EvidenceStore
    record = EvidenceStore(desk.DATA_DIR).put("market_observation", "research", result["instrument"]["id"], result)
    result = dict(result, evidence_id=record)
    with desk._lock:
        raw = load_saved(desk)
        records = {r["id"]: r for r in raw["instruments"]}
        records[result["instrument"]["id"]] = dict(result["instrument"], evidence_id=record)
        raw["instruments"] = list(records.values())[-2000:]
        if follow and result["instrument"]["id"] not in raw["watchlist"]:
            if len(raw["watchlist"]) >= 200:
                raise ValueError("At most 200 global instruments in the research watchlist")
            raw["watchlist"].append(result["instrument"]["id"])
        path = desk.DATA_DIR/"global_research.json"
        if str(path.resolve()) in desk._CORRUPT_PATHS:
            raise ValueError("Global research storage needs recovery before saving")
        desk._save_json(path, raw)
    return result


def notebook_observations(desk):
    """Rotate saved global interests during the existing daily notebook worker."""
    with desk._lock:
        raw = load_saved(desk)
        values, cursor = raw["watchlist"], raw.get("cursor", 0)
        selected = [values[(cursor+i) % len(values)] for i in range(min(4, len(values)))] if values else []
        raw["cursor"] = cursor + len(selected)
        path = desk.DATA_DIR/"global_research.json"
        if str(path.resolve()) in desk._CORRUPT_PATHS:
            raise ValueError("Global research storage needs recovery")
        desk._save_json(path, raw)
    observations = []
    for value in selected:
        try:
            data = save_observation(desk, history(value))
            completed = [b for b in data["candles"] if b["complete"]]
            observations.append({"instrument": data["instrument"], "evidence_id": data["evidence_id"],
                                 "latest_completed": completed[-1] if completed else None,
                                 "quality": data["quality"], "retrieved_at": data["retrieved_at"]})
        except (ValueError, requests.RequestException) as exc:
            observations.append({"instrument_id": value, "error": str(exc)[:160]})
    return observations


def register(app, desk):
    bp = Blueprint("markets", __name__)

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(ok=False, error=str(exc)), 400

    @bp.errorhandler(requests.RequestException)
    def unavailable(exc):
        return jsonify(ok=False, error="Market provider unavailable or rate limited; retry later. Saved observations remain intact."), 503

    @bp.get("/api/markets")
    def status():
        import market_universe
        raw = load_saved(desk)
        return jsonify(ok=True, directory=market_universe.status(desk), groups=GROUPS,
                       instruments=raw["instruments"], watchlist=raw["watchlist"],
                       scope="US-first discovery and verified on-demand history. Global research is optional; coverage is not exhaustive.",
                       unsupported="Individual bonds, OTC derivatives, private markets and unsupported option classes need qualified contracts and licensed data.")

    @bp.get("/api/markets/search")
    def lookup():
        import market_universe
        return jsonify(search(request.args.get("q"), market_universe.load(desk).get("symbols", []), request.args.get("scope", "us")))

    @bp.get("/api/markets/instrument/<path:value>")
    def inspect(value):
        return jsonify(save_observation(desk, history(value)))

    @bp.post("/api/markets/watchlist")
    def watchlist():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {"symbol", "remove"}:
            raise ValueError("Use symbol and optional remove fields")
        if "remove" in body and not isinstance(body["remove"], bool):
            raise ValueError("remove must be true or false")
        sym = "YF:"+symbol(body.get("symbol"))
        if body.get("remove") is True:
            with desk._lock:
                raw = load_saved(desk)
                raw["watchlist"] = [s for s in raw["watchlist"] if s != sym]
                path = desk.DATA_DIR/"global_research.json"
                if str(path.resolve()) in desk._CORRUPT_PATHS:
                    raise ValueError("Global research storage needs recovery")
                desk._save_json(path, raw)
        else:
            save_observation(desk, history(sym), follow=True)
        return jsonify(ok=True, watchlist=load_saved(desk)["watchlist"])

    app.register_blueprint(bp)
    app.config["MARKETS_AVAILABLE"] = True
