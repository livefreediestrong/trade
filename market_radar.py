"""
Whole-market mover radar (paper research) — no LLM.

Scans a broad US equity universe and ranks by |% change|, relative volume,
and dollar volume. Filters penny / low-liquidity junk.

Source preference (first that yields usable rows):
  1. Alpaca assets + snapshots when ALPACA_API_KEY/SECRET present
  2. Polygon snapshots when POLYGON_API_KEY present
  3. Yahoo predefined screeners (day_gainers / day_losers / most_actives)
  4. Finnhub quotes (FINNHUB_API_KEY) or Yahoo batch quotes on a fallback universe

Only top-N names should ever enter the desk brain path (loop / Waiting).
This module never calls Gemini/Jev.
"""
from __future__ import annotations

import math
import logging
import os
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Defaults / filters
# ---------------------------------------------------------------------------

class RadarTuning:
    """Centralized, auditable thresholds for whole-market research scans."""

    DEFAULT_TOP_N = 20
    DEFAULT_REFRESH_SEC = 300
    MIN_REFRESH_SEC = 60
    MAX_REFRESH_SEC = 3600
    MIN_PRICE = 5.0
    MIN_DOLLAR_VOLUME = 5_000_000.0
    MIN_VOLUME = 200_000
    MIN_ABS_PCT = 0.15
    DISTRIBUTION_PCT = -2.0
    DISTRIBUTION_REL_VOLUME = 3.0
    PARABOLIC_DAY_PCT = 50.0
    MAX_ABS_PCT = 80.0


DEFAULT_TOP_N = RadarTuning.DEFAULT_TOP_N
DEFAULT_REFRESH_SEC = RadarTuning.DEFAULT_REFRESH_SEC
MIN_REFRESH_SEC = RadarTuning.MIN_REFRESH_SEC
MAX_REFRESH_SEC = RadarTuning.MAX_REFRESH_SEC
MIN_PRICE = RadarTuning.MIN_PRICE
MIN_DOLLAR_VOLUME = RadarTuning.MIN_DOLLAR_VOLUME
# Alpaca's free IEX feed sees roughly 2-3% of consolidated US volume; scale so the
# consolidated-volume thresholds below still mean something. Labeled "_iex_est".
IEX_VOLUME_SHARE = 0.025
EMPTY_SCAN_BACKOFF_SEC = 120.0
MIN_VOLUME = RadarTuning.MIN_VOLUME
MAX_ABS_PCT = RadarTuning.MAX_ABS_PCT

# Broader than CURATED_LIQUID_US — used when Alpaca/batch path needs a universe
# without listing every US equity. ~120 liquid names.
FALLBACK_UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AMD", "AVGO",
    "NFLX", "CRM", "ORCL", "ADBE", "INTC", "QCOM", "TXN", "MU", "AMAT", "CSCO",
    "COST", "WMT", "HD", "MCD", "NKE", "SBUX", "DIS", "BA", "CAT", "GE",
    "JPM", "BAC", "GS", "V", "MA", "XOM", "CVX", "UNH", "JNJ", "PG",
    "KO", "PEP", "ABBV", "MRK", "LLY", "TMO", "ISRG", "NOW", "SHOP", "UBER",
    "SQ", "PYPL", "COIN", "HOOD", "PLTR", "SNOW", "CRWD", "PANW", "NET", "DDOG",
    "SMCI", "ARM", "MRVL", "LRCX", "KLAC", "SNPS", "CDNS", "ANET", "DELL", "IBM",
    "F", "GM", "RIVN", "LCID", "NIO", "SPOT", "ROKU", "SNAP", "PINS", "ZM",
    "DKNG", "PENN", "MGM", "MAR", "BKNG", "ABNB", "DAL", "UAL", "AAL", "LUV",
    "BAX", "CVS", "WBA", "T", "VZ", "TMUS", "CMCSA",
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH", "XBI", "ARKK",
    "SOXL", "TQQQ", "SQQQ", "UVXY", "GLD", "SLV", "USO", "TLT",
]

YAHOO_SCREENERS = ("day_gainers", "day_losers", "most_actives")

_lock = threading.RLock()
_cache: dict[str, Any] = {
    "at": 0.0,
    "movers": [],
    "source": None,
    "error": None,
    "top_n": DEFAULT_TOP_N,
}

_log = logging.getLogger("tomahawk.market_radar")
_log.setLevel(logging.INFO)
_log.propagate = False
if not _log.handlers:
    try:
        _log_dir = Path(os.environ.get("TOMAHAWK_DATA_DIR", "data"))
        _log_dir.mkdir(parents=True, exist_ok=True)
        _log_handler = RotatingFileHandler(
            _log_dir / "market_radar.log",
            maxBytes=512 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        _log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(_log_handler)
    except OSError:
        pass


def clamp_refresh_sec(sec: Any) -> int:
    try:
        v = int(sec)
    except (TypeError, ValueError):
        v = DEFAULT_REFRESH_SEC
    return max(MIN_REFRESH_SEC, min(MAX_REFRESH_SEC, v))


def clamp_top_n(n: Any) -> int:
    try:
        v = int(n)
    except (TypeError, ValueError):
        v = DEFAULT_TOP_N
    return max(5, min(50, v))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        value = float(v)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _today_et_str() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d")


def _is_tradeable_symbol(sym: str) -> bool:
    s = (sym or "").strip().upper()
    if not s or s.startswith("."):
        return False
    try:
        import data_sources as _ds

        if _ds.is_leveraged_or_inverse(s):  # 3x / inverse ETFs decay; not for this desk
            return False
    except Exception:
        pass
    if any(ch in s for ch in (" ", "/", ":", "^")):
        return False
    if s[0].isdigit() or s.isdigit():
        return False
    if s.count(".") > 1:
        return False
    core = s.replace(".", "")
    if not core.isalnum() or not any(c.isalpha() for c in core):
        return False
    if len(core) > 5:  # allow BRK.B (5) / most US tickers
        # BRK.B → core BRKB len 4; allow up to 5 letters without dot
        if "." not in s and len(core) > 5:
            return False
        if len(core) > 6:
            return False
    return True


# ---------------------------------------------------------------------------
# Scoring / filters
# ---------------------------------------------------------------------------

def _rel_vol(volume: float, avg_vol: float) -> float:
    if avg_vol and avg_vol > 0:
        return volume / avg_vol
    return 1.0 if volume > 0 else 0.0


def _composite_score(pct: float, rvol: float, dollar_vol: float) -> float:
    """0–100-ish score. Higher = hotter mover (liquidity-aware)."""
    abs_pct = abs(pct)
    pct_part = min(abs_pct / 8.0, 1.5) * 40.0  # |8%| → 40
    rvol_part = min(max(rvol, 0.0) / 4.0, 1.5) * 35.0  # 4× → 35
    # log10($5M)≈6.7, log10($500M)≈8.7
    if dollar_vol > 0:
        dvol_part = min(max(math.log10(dollar_vol) - 6.0, 0.0) / 3.0, 1.0) * 25.0
    else:
        dvol_part = 0.0
    return round(pct_part + rvol_part + dvol_part, 2)


def _passes_filters(
    *,
    price: float,
    volume: float,
    dollar_vol: float,
    pct: float,
) -> bool:
    if price < MIN_PRICE:
        return False
    if volume < MIN_VOLUME and dollar_vol < MIN_DOLLAR_VOLUME:
        return False
    if dollar_vol < MIN_DOLLAR_VOLUME * 0.4:  # soft floor for very thin names
        return False
    if abs(pct) > MAX_ABS_PCT:
        return False
    if abs(pct) < 0.15 and dollar_vol < MIN_DOLLAR_VOLUME * 2:
        # ignore flat quiet names unless huge dollar volume (most_actives)
        return False
    return True


def _row_from_quote(
    symbol: str,
    *,
    price: float,
    pct: float,
    volume: float,
    avg_vol: float = 0.0,
    source: str = "unknown",
) -> Optional[dict[str, Any]]:
    sym = (symbol or "").strip().upper()
    if not _is_tradeable_symbol(sym):
        return None
    price = _safe_float(price)
    pct = _safe_float(pct)
    volume = _safe_float(volume)
    avg_vol = _safe_float(avg_vol)
    if price <= 0:
        return None
    dollar_vol = price * volume
    if not _passes_filters(price=price, volume=volume, dollar_vol=dollar_vol, pct=pct):
        return None
    rvol = _rel_vol(volume, avg_vol)
    score = _composite_score(pct, rvol, dollar_vol)
    flags: list[str] = []
    if pct <= RadarTuning.DISTRIBUTION_PCT and rvol >= RadarTuning.DISTRIBUTION_REL_VOLUME:
        flags.append("distribution_day")
    if abs(pct) >= RadarTuning.PARABOLIC_DAY_PCT:
        flags.append("parabolic_move")
    return {
        "ticker": sym,
        "price": round(price, 4),
        "pct_change": round(pct, 3),
        "volume": int(volume),
        "avg_volume": int(avg_vol) if avg_vol else None,
        "rel_volume": round(rvol, 2),
        "dollar_volume": round(dollar_vol, 0),
        "score": score,
        "source": source,
        "research_flags": flags,
    }


def _dedupe_rank(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        prev = best.get(t)
        if prev is None or float(r.get("score") or 0) > float(prev.get("score") or 0):
            best[t] = r
    ranked = sorted(best.values(), key=lambda x: float(x.get("score") or 0), reverse=True)
    return ranked[:top_n]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _yahoo_headers() -> dict[str, str]:
    try:
        import data_sources as ds

        return dict(getattr(ds, "HEADERS", {}) or {})
    except Exception:
        return {
            "User-Agent": "Mozilla/5.0 (compatible; TomahawkDesk/1.0)",
            "Accept": "application/json",
        }


def fetch_yahoo_screener_movers(count_per: int = 25) -> list[dict[str, Any]]:
    """Yahoo predefined day_gainers / day_losers / most_actives. No LLM."""
    import requests

    out: list[dict[str, Any]] = []
    headers = _yahoo_headers()
    for scr in YAHOO_SCREENERS:
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        try:
            r = requests.get(
                url,
                params={"count": min(50, max(5, count_per)), "scrIds": scr},
                headers=headers,
                timeout=12,
            )
            if r.status_code != 200:
                continue
            result = (r.json().get("finance") or {}).get("result") or []
            quotes = (result[0] or {}).get("quotes") or [] if result else []
            for q in quotes:
                sym = q.get("symbol") or ""
                price = _safe_float(q.get("regularMarketPrice"))
                pct = _safe_float(q.get("regularMarketChangePercent"))
                vol = _safe_float(q.get("regularMarketVolume"))
                avg = _safe_float(
                    q.get("averageDailyVolume3Month")
                    or q.get("averageDailyVolume10Day")
                    or 0
                )
                row = _row_from_quote(
                    sym,
                    price=price,
                    pct=pct,
                    volume=vol,
                    avg_vol=avg,
                    source=f"yahoo_{scr}",
                )
                if row:
                    out.append(row)
        except Exception:
            continue
    return out


def fetch_alpaca_snapshot_movers(universe: list[str] | None = None) -> list[dict[str, Any]]:
    """Alpaca market-data snapshots when broker keys present. Fail soft → []."""
    try:
        import broker_alpaca as ba
    except Exception:
        return []
    if not ba.is_configured():
        return []

    import requests

    symbols = [s for s in (universe or FALLBACK_UNIVERSE) if _is_tradeable_symbol(s)]
    if not symbols:
        return []

    key, secret = ba.credentials()
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }
    # data.alpaca.markets — same keys as trading API
    data_base = "https://data.alpaca.markets"
    out: list[dict[str, Any]] = []
    # Batch ≤100 symbols per request
    for i in range(0, len(symbols), 100):
        chunk = symbols[i : i + 100]
        try:
            feed = "iex"
            r = requests.get(
                f"{data_base}/v2/stocks/snapshots",
                params={"symbols": ",".join(chunk), "feed": "iex"},
                headers=headers,
                timeout=15,
            )
            if r.status_code != 200:
                feed = "default"
                # Retry without feed hint
                r = requests.get(
                    f"{data_base}/v2/stocks/snapshots",
                    params={"symbols": ",".join(chunk)},
                    headers=headers,
                    timeout=15,
                )
            if r.status_code != 200:
                continue
            data = r.json() or {}
            # Response may be {SYM: {...}} or {"snapshots": {SYM: {...}}}
            snaps = data.get("snapshots") if isinstance(data.get("snapshots"), dict) else data
            if not isinstance(snaps, dict):
                continue
            for sym, snap in snaps.items():
                if not isinstance(snap, dict):
                    continue
                daily = snap.get("dailyBar") or snap.get("prevDailyBar") or {}
                prev = snap.get("prevDailyBar") or {}
                latest = snap.get("latestTrade") or {}
                price = _safe_float(
                    latest.get("p")
                    or daily.get("c")
                    or snap.get("minuteBar", {}).get("c")
                )
                # Pre-market, dailyBar is still the LAST session: compare to its close,
                # not to the session before it (that doubled the % move).
                daily_is_today = str(daily.get("t") or "")[:10] == _today_et_str()
                if daily_is_today:
                    prev_close = _safe_float(prev.get("c") or daily.get("o"))
                    vol = _safe_float(daily.get("v"))
                else:
                    prev_close = _safe_float(daily.get("c"))
                    vol = 0.0
                if feed == "iex" and vol:
                    # IEX prints are only a small slice of consolidated volume.
                    vol = vol / IEX_VOLUME_SHARE
                pct = 0.0
                if prev_close > 0 and price > 0:
                    pct = ((price - prev_close) / prev_close) * 100.0
                row = _row_from_quote(
                    str(sym),
                    price=price,
                    pct=pct,
                    volume=vol,
                    avg_vol=0.0,
                    source="alpaca_snapshot_iex_est" if feed == "iex" else "alpaca_snapshot",
                )
                if row:
                    out.append(row)
        except Exception:
            continue
    return out


def fetch_polygon_snapshot_movers(universe: list[str] | None = None) -> list[dict[str, Any]]:
    """Polygon snapshots when POLYGON_API_KEY present. Fail soft → []."""
    try:
        import polygon_client as pc
    except Exception:
        return []
    if not pc.is_configured():
        return []
    out: list[dict[str, Any]] = []
    try:
        snaps = pc.fetch_radar_movers(universe or FALLBACK_UNIVERSE)
        for s in snaps or []:
            if not isinstance(s, dict):
                continue
            row = _row_from_quote(
                str(s.get("ticker") or ""),
                price=_safe_float(s.get("price")),
                pct=_safe_float(s.get("pct")),
                volume=_safe_float(s.get("volume")),
                avg_vol=0.0,
                source="polygon_snapshot",
            )
            if row:
                out.append(row)
    except Exception:
        return []
    return out



def fetch_batch_quote_movers(universe: list[str] | None = None) -> list[dict[str, Any]]:
    """Finnhub (if keyed) then Yahoo chart batch on fallback universe."""
    symbols = [s for s in (universe or FALLBACK_UNIVERSE) if _is_tradeable_symbol(s)]
    out: list[dict[str, Any]] = []

    # Finnhub — rate-limited; sample a subset to stay under free tier
    try:
        import data_sources as ds

        if getattr(ds, "FINNHUB_KEY", ""):
            for sym in symbols[:40]:
                q = ds.finnhub_quote(sym)
                if not q:
                    continue
                # ds.finnhub_quote normalizes to current / change_pct
                price = _safe_float(q.get("current") or q.get("price") or q.get("c"))
                pct = _safe_float(q.get("change_pct") or q.get("percent") or q.get("dp"))
                # Finnhub quote has no volume in basic — skip thin filter via dollar soft
                row = _row_from_quote(
                    sym,
                    price=price,
                    pct=pct,
                    volume=max(MIN_VOLUME, 1),  # unknown vol — will need dollar soft fail
                    avg_vol=0,
                    source="finnhub",
                )
                # Without volume Finnhub rows rarely pass filters; keep only strong % moves
                if row is None and price >= MIN_PRICE and abs(pct) >= 2.0:
                    row = {
                        "ticker": sym,
                        "price": round(price, 4),
                        "pct_change": round(pct, 3),
                        "volume": None,
                        "avg_volume": None,
                        "rel_volume": None,
                        "dollar_volume": None,
                        "score": round(min(abs(pct) / 8.0, 1.5) * 40.0, 2),
                        "source": "finnhub_pct_only",
                    }
                if row:
                    out.append(row)
    except Exception:
        pass

    if out:
        return out

    # Yahoo batch / chart meta — slow; cap universe
    try:
        import data_sources as ds

        batch = ds.yahoo_quote_batch(symbols[:60], timeout=12)
        seen_sym: set[str] = set()
        for sym, q in (batch or {}).items():
            if not isinstance(q, dict):
                continue
            sym_u = str(sym or "").upper()
            # yahoo_quote_batch may key both BRK.B and BRK-B — keep one
            if "-" in sym_u and sym_u.replace("-", ".") in (batch or {}):
                continue
            if sym_u in seen_sym:
                continue
            seen_sym.add(sym_u)
            price = _safe_float(
                q.get("regularMarketPrice") or q.get("price")
            )
            prev = _safe_float(q.get("regularMarketPreviousClose") or q.get("previousClose"))
            pct = _safe_float(q.get("regularMarketChangePercent"))
            if pct == 0.0 and prev > 0 and price > 0:
                pct = ((price - prev) / prev) * 100.0
            vol = _safe_float(q.get("regularMarketVolume") or q.get("volume"))
            avg = _safe_float(
                q.get("averageDailyVolume3Month") or q.get("averageDailyVolume10Day") or 0
            )
            row = _row_from_quote(
                sym,
                price=price,
                pct=pct,
                volume=vol if vol else MIN_VOLUME,
                avg_vol=avg,
                source="yahoo_batch",
            )
            if row:
                out.append(row)
    except Exception:
        pass
    return out

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_movers(
    *,
    top_n: int = DEFAULT_TOP_N,
    prefer_alpaca: bool = True,
) -> dict[str, Any]:
    """Scan market movers. Never raises. Never calls LLM."""
    top_n = clamp_top_n(top_n)
    rows: list[dict[str, Any]] = []
    source = None
    error = None

    if prefer_alpaca:
        try:
            rows = fetch_alpaca_snapshot_movers()
            if rows:
                source = "alpaca"
        except Exception as exc:  # noqa: BLE001
            error = f"alpaca: {exc}"
            _log.warning("alpaca mover scan failed: %s", exc)

    # API pack: Polygon after Alpaca, before Yahoo
    if not rows:
        try:
            rows = fetch_polygon_snapshot_movers()
            if rows:
                source = "polygon"
        except Exception as exc:  # noqa: BLE001
            error = (error + "; " if error else "") + f"polygon: {exc}"
            _log.warning("polygon mover scan failed: %s", exc)

    if not rows:
        try:
            rows = fetch_yahoo_screener_movers(count_per=30)
            if rows:
                source = "yahoo_screener"
        except Exception as exc:  # noqa: BLE001
            error = (error + "; " if error else "") + f"yahoo: {exc}"
            _log.warning("yahoo screener scan failed: %s", exc)

    if not rows:
        try:
            rows = fetch_batch_quote_movers()
            if rows:
                source = "batch_quotes"
        except Exception as exc:  # noqa: BLE001
            error = (error + "; " if error else "") + f"batch: {exc}"
            _log.warning("batch quote scan failed: %s", exc)

    movers = _dedupe_rank(rows, top_n)
    return {
        "ok": bool(movers),
        "movers": movers,
        "count": len(movers),
        "top_n": top_n,
        "source": source,
        "error": error if not movers else None,
        "scanned_at": _now_iso(),
        "sim": True,
        "paper_research": True,
        "llm": False,
    }


def get_cached() -> dict[str, Any]:
    with _lock:
        movers = list(_cache.get("movers") or [])
        return {
            "ok": bool(movers),
            "movers": movers,
            "count": len(movers),
            "top_n": _cache.get("top_n") or DEFAULT_TOP_N,
            "source": _cache.get("source"),
            "error": _cache.get("error"),
            "scanned_at": (
                datetime.fromtimestamp(_cache["at"], tz=timezone.utc).isoformat()
                if _cache.get("at")
                else None
            ),
            "age_sec": round(time.time() - float(_cache.get("at") or 0), 1)
            if _cache.get("at")
            else None,
            "sim": True,
            "paper_research": True,
            "llm": False,
        }


def maybe_refresh(cfg: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any]:
    """Refresh cache if stale / forced. Respects radar_enabled when cfg given."""
    cfg = cfg or {}
    if cfg and not bool(cfg.get("radar_enabled", False)) and not force:
        return get_cached()

    top_n = clamp_top_n(cfg.get("radar_top_n", DEFAULT_TOP_N))
    refresh = clamp_refresh_sec(cfg.get("radar_refresh_sec", DEFAULT_REFRESH_SEC))

    with _lock:
        age = time.time() - float(_cache.get("at") or 0)
        if _cache.get("at"):
            # A failed/empty scan still counts as a scan (short backoff) — before,
            # every /api/state poll re-ran the whole scan after one failure.
            fresh_for = refresh if _cache.get("movers") else min(refresh, EMPTY_SCAN_BACKOFF_SEC)
            if age < fresh_for and not force:
                return get_cached()
            if force and age < 10:
                return get_cached()  # debounce repeated force clicks
        if _cache.get("scanning"):
            return get_cached()  # one scan at a time
        _cache["scanning"] = True

    try:
        result = scan_movers(top_n=top_n)
    except Exception as exc:  # noqa: BLE001
        result = {"movers": [], "source": None, "error": str(exc)[:200]}
        _log.exception("mover refresh failed")
    finally:
        with _lock:
            _cache["scanning"] = False
    with _lock:
        _cache["at"] = time.time()
        _cache["movers"] = list(result.get("movers") or [])
        _cache["source"] = result.get("source")
        _cache["error"] = result.get("error")
        _cache["top_n"] = top_n
    return get_cached()


def hot_symbols(cfg: dict[str, Any] | None = None, *, limit: int | None = None) -> list[str]:
    """Ticker list for loop merge. Empty when radar disabled or cache cold."""
    cfg = cfg or {}
    if not bool(cfg.get("radar_enabled", False)):
        return []
    cached = get_cached()
    movers = cached.get("movers") or []
    if not movers:
        # Opportunistic soft refresh (non-blocking prefer — caller may force)
        cached = maybe_refresh(cfg)
        movers = cached.get("movers") or []
    n = clamp_top_n(limit if limit is not None else cfg.get("radar_top_n", DEFAULT_TOP_N))
    out: list[str] = []
    for m in movers[:n]:
        t = str(m.get("ticker") or "").upper()
        if t and t not in out:
            out.append(t)
    return out


def merge_into_focus(
    base: list[str],
    radar: list[str],
    *,
    cap_extra: int = 12,
) -> list[str]:
    """Prepend hot radar names not already in base. Does not mutate inputs."""
    base = list(base or [])
    seen = {s.upper() for s in base}
    extra: list[str] = []
    for raw in radar or []:
        s = str(raw or "").strip().upper()
        if not s or s in seen:
            continue
        if not _is_tradeable_symbol(s):
            continue
        seen.add(s)
        extra.append(s)
        if len(extra) >= cap_extra:
            break
    return extra + base


def public_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Safe UI payload — no secrets, no LLM."""
    cfg = cfg or {}
    enabled = bool(cfg.get("radar_enabled", False))
    cached = get_cached()
    count = int(cached.get("count") or 0) if enabled else 0
    butler = None
    if enabled:
        if count:
            butler = f"Market radar: watching top movers ({count})"
        else:
            butler = "Market radar: scanning for movers…"
    return {
        "enabled": enabled,
        "top_n": clamp_top_n(cfg.get("radar_top_n", DEFAULT_TOP_N)),
        "refresh_sec": clamp_refresh_sec(cfg.get("radar_refresh_sec", DEFAULT_REFRESH_SEC)),
        "count": count,
        "movers": (cached.get("movers") or []) if enabled else [],
        "source": cached.get("source") if enabled else None,
        "scanned_at": cached.get("scanned_at") if enabled else None,
        "age_sec": cached.get("age_sec") if enabled else None,
        "error": cached.get("error") if enabled else None,
        "butler": butler,
        "sim": True,
        "paper_research": True,
        "llm": False,
    }


def simple_butler_line(cfg: dict[str, Any] | None = None) -> str | None:
    st = public_status(cfg)
    return st.get("butler")
