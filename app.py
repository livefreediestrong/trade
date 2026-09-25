"""
Day-trade SIGNAL DESK — DRAFT RESEARCH TOOL
-------------------------------------------
Modes: manual (default) | auto_paper | auto_live | live_manual

Research draft: full playbook visible.
Alpaca broker optional via broker_alpaca (ALPACA_API_KEY/SECRET).
ALPACA_PAPER defaults true (paper-api). ALPACA_PAPER=false → live money endpoint.
When Alpaca is configured, auto_live / approve submit to broker only on success
(no dual local paper_fill). On broker fail, no local paper fill is recorded.
Broker routing supports Alpaca and Interactive Brokers Gateway.
"""

from __future__ import annotations

import csv
import copy
import atexit
import hashlib
import hmac
import io
import ipaddress
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request

import llm_trader
import lessons
import claude_brain
import backtest
import paper_loop as paper_loop_mod
import session_track
import buzz_sources
import market_radar
import api_providers
import news_stream
import news_intelligence
import social_intelligence
import market_capture
import desk_alerts
import macro_calendar
import market_events
import wsb_monitor
import edgar_client
import options_flow
import order_terms
import code_version
from risk_policy import RISK_PRESETS

# Fingerprint the source this process loaded, before anything can change on disk.
code_version.mark_started()

APP_DIR = Path(__file__).resolve().parent
# TOMAHAWK_DATA_DIR overrides (tests use a temp dir so they never touch real data)
DATA_DIR = Path(os.environ.get("TOMAHAWK_DATA_DIR") or (APP_DIR / "data"))
DATA_DIR.mkdir(exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
SIGNALS_PATH = DATA_DIR / "signals.json"
LEDGER_PATH = DATA_DIR / "ledger.json"
JOURNAL_PATH = DATA_DIR / "journal.json"
DECISIONS_PATH = DATA_DIR / "decisions.json"
LESSONS_PATH = DATA_DIR / "lessons.json"
BACKTEST_PATH = DATA_DIR / "backtest.json"

BANNER = (
    "Tomahawk — live trading and separate paper research. "
    "Check the verified broker account before approving an order."
)

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "SPY", "QQQ"]

def yahoo_symbol(symbol: str) -> str:
    """Map desk symbols to Yahoo-friendly form (BRK.B → BRK-B)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return sym
    # Class shares: BRK.B / BRK/B → BRK-B
    if re.match(r"^[A-Z]{1,5}[./][AB]$", sym):
        return sym.replace(".", "-").replace("/", "-")
    return sym


def _normalize_watchlist_token(value: Any) -> str:
    """Normalize one watchlist token without exposing provider-specific symbols."""
    if value is None:
        return ""
    token = str(value).strip().lstrip("$").strip().upper()
    if not token or token in {"/", "-", "|", "."}:
        return ""
    # Google Finance / exchange prefixes for US equities.
    token = re.sub(
        r"^(?:NASDAQ|NYSE|NYSEARCA|AMEX|OTCMKTS|OTC|BATS|ARCA):",
        "",
        token,
    )
    token = token.strip()
    # Lone FX/crypto fragments from already-split "BTC / USD" imports.
    try:
        from paper_loop import WATCHLIST_NON_EQUITY
        deny = WATCHLIST_NON_EQUITY
    except Exception:
        deny = {"USD", "USDT", "USDC", "EUR", "GBP", "JPY", "BTC", "ETH", "FX", "CRYPTO"}
    if token in deny:
        return ""
    return token


def parse_watchlist(value: str | list[Any]) -> list[str]:
    """Parse watchlist text or list values into ordered, unique symbols.

    Delimiters are commas, whitespace, and semicolons. Slash/space pairs such
    as ``BTC / USD`` are kept as a single token (not split into BTC+USD), then
    typically dropped by equity_loop_symbols. A list is flattened by parsing
    each item so API callers can mix already-split and bulk values.
    """
    if isinstance(value, list):
        # Preserve per-item integrity (e.g. ["BTC / USD", "AAPL"]) then join
        # with newlines so slash-pairs survive the pair-protector below.
        raw_text = "\n".join(str(item) for item in value if item is not None)
    elif isinstance(value, str):
        raw_text = value
    else:
        raise TypeError("watchlist must be list or string")

    # Protect "BTC / USD" / "EUR / USD" style pairs from whitespace splitting.
    protected: list[str] = []
    def _protect(m: re.Match) -> str:
        protected.append(m.group(0).strip().upper())
        return f" __PAIR{len(protected)-1}__ "

    raw_text = re.sub(
        r"\b([A-Z]{2,10})\s*/\s*([A-Z]{2,10})\b",
        _protect,
        raw_text,
        flags=re.I,
    )

    tokens = re.split(r"[,;\s]+", raw_text)
    result: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        if not raw:
            continue
        m = re.fullmatch(r"__PAIR(\d+)__", raw)
        if m:
            token = protected[int(m.group(1))]
        else:
            token = _normalize_watchlist_token(raw)
        if not token or token in seen:
            continue
        # Drop pure punctuation
        if token in {"/", "-", "|", "."}:
            continue
        seen.add(token)
        result.append(token)
    return result


def sanitize_watchlist(symbols: list[str] | None) -> list[str]:
    """Normalize + drop non-equity junk for evaluate/loop budgets.

    Keeps class shares (BRK.B). Drops index/meta (.DJI), pure numeric (2353),
    digit-leading (29M), and slash pairs (BTC / USD).
    """
    parsed = parse_watchlist(list(symbols or []))
    try:
        from paper_loop import equity_loop_symbols
        return equity_loop_symbols(parsed)
    except Exception:
        return []


def parse_watchlist_import(value: Any) -> list[str]:
    """Parse pasted text, including a CSV Symbol/Ticker column when present."""
    if isinstance(value, list):
        return parse_watchlist(value)
    if not isinstance(value, str):
        raise TypeError("text must be a string")

    text = value.strip()
    if not text:
        return []

    # If the first CSV row has a Symbol/Ticker-style header, use that column
    # rather than treating headers and adjacent CSV fields as symbols.
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    try:
        rows = list(csv.reader(io.StringIO(text), dialect=dialect))
    except csv.Error:
        rows = []
    if rows:
        header = rows[0]
        header_keys = [re.sub(r"[^a-z0-9]+", "", cell.strip().lower()) for cell in header]
        symbol_idx = next(
            (idx for idx, key in enumerate(header_keys) if key in {"symbol", "ticker", "tickersymbol", "symbols"}),
            None,
        )
        if symbol_idx is not None:
            values = [row[symbol_idx] for row in rows[1:] if len(row) > symbol_idx]
            return parse_watchlist(values)
    return parse_watchlist(text)


# Backward-friendly private alias for callers that prefer helper-style names.
_parse_watchlist = parse_watchlist


DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "manual",  # manual | auto_paper | auto_live
    "paper_research_enabled": False,
    "paper_auto_approve": False,
    "paper_risk_preset": "mid",
    "risk_preset": "mid",
    "watchlist": list(DEFAULT_WATCHLIST),
    "paper_equity": 100_000.0,
    "paper_cash": 100_000.0,
    "slip_bps": 5,  # ±0.05% slip on paper fills
    "signal_ttl_sec": 900,  # 15 min
    "kill_switch": {
        "max_daily_loss_usd": None,
        "max_trades_per_day": None,
        "max_position_size_usd": None,
        "armed": False,
    },
    "demo_signal_interval_sec": 120,  # legacy key; scan loop prefers scan_interval_sec
    "scan_interval_sec": 120,  # volume-screener watchlist scan interval
    "alert_cooldown_sec": 300,
    "promotion_min_samples": 30,
    "promotion_min_independent_tickers": 3,
    "promotion_min_win_rate": 0.52,
    "promotion_min_expectancy_usd": 0.0,
    "promotion_min_profit_factor": 1.05,
    "promotion_max_drawdown_pct": 5.0,
    "promotion_window_days": 30,
    # Session goal (USD). Not a guarantee — stops new auto/manual fills when realized day PnL hits it.
    "daily_profit_target_usd": None,
    "session_active": False,
    "session_started_at": None,
    # Gemini LLM research (thesis on scan + chat). Desk still runs without API key.
    "llm_enabled": True,
    "llm_model": None,  # None → use GEMINI_MODEL / llm_trader default
    "llm_on_scan": True,
    # Jev-style paper decision loop (equities, RTH-gated)
    "loop_interval_sec": 60,  # clamped >= 30
    "loop_enabled": False,  # set True on START
    "rth_only": True,  # refuse overnight unsupervised loops
    "max_session_loss_usd": None,  # default = bank * preset max_daily_loss_pct/100
    # Watchlist focus for scan + paper loop: liquid intersects CURATED; all uses equity filter only.
    "watchlist_focus": "liquid",  # liquid | all
    # Heat lane: buzz → liquid/watchlist-relevant strip (does not overwrite watchlist)
    "heat_enabled": True,
    # Whole-market mover radar (no LLM on every name; top N enter loop/Waiting)
    "radar_enabled": False,
    "radar_top_n": 20,
    "radar_refresh_sec": 300,
    # Optional broad-market filter. Disabled by default until live data coverage is proven.
    "market_regime_gate_enabled": False,
    "social_enabled": False,
    # API pack — macro calendar gates (Fed/CPI/earnings); degrade when keys missing
    "macro_gates_enabled": True,
    "macro_size_mult": 0.5,
    "macro_force_ask_first": True,
    "macro_use_heuristics": False,
    # Brain: gemini | mock | jev (one selected; jev needs TYPESAFE_AI_API_KEY)
    "brain_mode": "gemini",
    # Horizon for LLM/Jev directional question (minutes)
    "decision_horizon_min": 20,
    # Paper friction analogs (bps)
    "fee_bps": 1.0,
    "risk_per_trade_pct": 0.25,
    "atr_stop_multiple": 1.0,
    # Trade intelligence gates (all trades). A PASS must keep this reward:risk after
    # round-trip fees, slippage and spread; 0 disables.
    "min_net_reward_risk": 1.2,
    # Block execution of a setup whose own scored after-cost record is below breakeven.
    "evidence_gate_enabled": True,
    "evidence_min_samples": 12,
    # Pause new broker risk after giving back this % of the day's peak gain; 0 disables.
    "giveback_stop_pct": 50.0,
    # Scheduled high-impact events (FOMC, Fed Chair, CPI/jobs, presidential addresses, your own):
    # the broker agent opens nothing new this many minutes before/after; exits continue.
    "event_guard_enabled": True,
    "event_guard_before_min": 15,
    "event_guard_after_min": 15,
    "custom_market_events": [],
    # WSB crowding on a new buy idea: "half" size, "skip", "note" only, or "off". Never adds risk.
    "wsb_crowding_action": "half",
    "wsb_crowd_min_mentions": 25,
}

_lock = threading.RLock()
app = Flask(__name__)
# The local desk's templates can be redesigned without restarting the broker session.
# Jinja checks template mtimes; this does not enable Flask's debugger or process reloader.
app.config["TEMPLATES_AUTO_RELOAD"] = True
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    _proxy_hops = int(os.environ.get("TOMAHAWK_TRUSTED_PROXY_HOPS", "0") or 0)
    if _proxy_hops > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_proxy_hops, x_host=_proxy_hops, x_proto=_proxy_hops)
except (ImportError, ValueError):
    pass
logging.basicConfig(level=os.environ.get("TOMAHAWK_LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("tomahawk")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB is plenty for any desk request

try:
    from flask.json.provider import DefaultJSONProvider as _DefaultJSONProvider

    class _SafeJSONProvider(_DefaultJSONProvider):
        """Never emit NaN/Infinity — the browser's JSON.parse rejects them."""

        def dumps(self, obj, **kwargs):  # type: ignore[override]
            return super().dumps(_json_safe(obj), **kwargs)

    app.json = _SafeJSONProvider(app)
except Exception:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Local-only access guard (anti-CSRF / anti-DNS-rebinding)
# ---------------------------------------------------------------------------
# The desk can switch modes, approve signals and submit broker orders with no login,
# so it must only accept requests from itself. Bind is 127.0.0.1 by default; set
# TOMAHAWK_HOST=0.0.0.0 AND list extra host:port values in TOMAHAWK_ALLOWED_HOSTS
# (comma-separated) to expose it on a LAN deliberately.
DESK_PORT = int(os.environ.get("TOMAHAWK_PORT", "5056"))
DESK_HOST = os.environ.get("TOMAHAWK_HOST", "127.0.0.1").strip() or "127.0.0.1"
_ALLOWED_HOSTS = {
    f"127.0.0.1:{DESK_PORT}",
    f"localhost:{DESK_PORT}",
    f"[::1]:{DESK_PORT}",
} | {
    h.strip().lower()
    for h in (os.environ.get("TOMAHAWK_ALLOWED_HOSTS") or "").split(",")
    if h.strip()
}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_RATE_LIMITS: dict[str, list[float]] = {}
_RATE_LIMIT_LOCK = threading.Lock()


def _rate_limited(bucket: str, limit: int, window: float = 60.0) -> bool:
    now = time.monotonic()
    key = f"{bucket}:{request.remote_addr or 'local'}"
    with _RATE_LIMIT_LOCK:
        values = [t for t in _RATE_LIMITS.get(key, []) if now - t < window]
        limited = len(values) >= limit
        if not limited:
            values.append(now)
        _RATE_LIMITS[key] = values
        return limited


def _is_loopback_request() -> bool:
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _remote_request_authorized() -> bool:
    """Require a shared token for every non-loopback client."""
    configured = (os.environ.get("TOMAHAWK_AUTH_TOKEN") or "").strip()
    if not configured:
        return False
    supplied = (request.headers.get("X-Tomahawk-Token") or "").strip()
    if not supplied:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, configured)


@app.before_request
def _local_only_guard():
    host = (request.host or "").strip().lower()
    if host not in _ALLOWED_HOSTS:
        # Blocks DNS-rebinding pages that resolve an attacker hostname to 127.0.0.1
        return jsonify({"ok": False, "error": "forbidden_host"}), 403
    if not _is_loopback_request() and not _remote_request_authorized():
        return jsonify({"ok": False, "error": "remote_auth_required"}), 403
    if not _is_loopback_request() and not request.is_secure:
        return jsonify({"ok": False, "error": "https_required"}), 403
    if request.method in _UNSAFE_METHODS:
        origin = (request.headers.get("Origin") or "").strip().lower()
        if origin and origin != "null":
            origin_host = origin.split("://", 1)[-1].rstrip("/")
            if origin_host not in _ALLOWED_HOSTS:
                return jsonify({"ok": False, "error": "forbidden_origin"}), 403
        elif origin == "null":
            return jsonify({"ok": False, "error": "forbidden_origin"}), 403
        # Browsers always send Sec-Fetch-Site; scripts (PowerShell/curl) send neither header.
        if (request.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return jsonify({"ok": False, "error": "forbidden_cross_site"}), 403
    return None


@app.after_request
def _static_cache_headers(resp):
    """Cache fingerprinted-ish static assets briefly; never cache SSE/API."""
    try:
        p = request.path or ""
        if p.startswith("/static/") and resp.status_code == 200:
            # Short private cache — deploy replaces files in place (no hash in URL)
            resp.headers.setdefault("Cache-Control", "public, max-age=120, must-revalidate")
        elif p.startswith("/api/") or p.startswith("/events"):
            resp.headers.setdefault("Cache-Control", "no-store")
    except Exception:
        pass
    return resp


@app.after_request
def _compress_json_snapshot(resp):
    """Compress large snapshots, never SSE or a streamed response."""
    if (request.method == "GET" and request.accept_encodings["gzip"] > 0
            and resp.mimetype == "application/json" and not resp.is_streamed
            and not resp.direct_passthrough and not resp.headers.get("Content-Encoding")):
        raw = resp.get_data()
        if len(raw) >= 4096:
            import gzip
            packed = gzip.compress(raw, compresslevel=1, mtime=0)
            if len(packed) < len(raw):
                resp.set_data(packed)
                resp.headers["Content-Encoding"] = "gzip"
                resp.vary.add("Accept-Encoding")
    return resp



# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    """Calendar day in America/New_York (session PnL day boundary)."""
    try:
        if paper_loop_mod.NY_TZ is not None:
            return datetime.now(paper_loop_mod.NY_TZ).strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now().strftime("%Y-%m-%d")


# Files found corrupt this process. Saves to these paths are refused (fail closed) so a
# default/empty object can never overwrite the operator's real data. Repair the file
# (a timestamped .corrupt.bak copy is kept) and restart to clear.
_CORRUPT_PATHS: dict[str, str] = {}


def corrupt_files_status() -> dict[str, str]:
    return dict(_CORRUPT_PATHS)


def _mark_corrupt(path: Path, reason: str) -> None:
    key = str(path.resolve())
    if key in _CORRUPT_PATHS:
        return
    _CORRUPT_PATHS[key] = str(reason)[:200]
    logger.error("state file marked corrupt: %s (%s)", path, reason)
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = path.with_suffix(path.suffix + f".corrupt.{stamp}.bak")
        bak.write_bytes(path.read_bytes())
        note = path.with_suffix(path.suffix + ".corrupt.txt")
        note.write_text(
            f"unreadable or corrupt JSON: {reason}\nbackup: {bak.name}\n"
            "Saves to this file are blocked until it is repaired and the app restarted.\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    logger.error("saves blocked and trading gated for %s", path.name)


def _load_json(path: Path, default: Any) -> Any:
    """Load JSON; on corrupt file backup aside and fail closed (return default, never overwrite)."""
    if not path.exists():
        return default
    try:
        # utf-8-sig: tolerate a BOM (PowerShell 5 Set-Content/Out-File adds one)
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _mark_corrupt(path, str(exc))
        return default
    except OSError:
        _mark_corrupt(path, "file could not be read")
        return default


def _json_safe(obj: Any) -> Any:
    """NaN/Infinity → None so files stay valid JSON (browsers can't parse NaN)."""
    import math

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _save_json(path: Path, data: Any) -> None:
    if str(path.resolve()) in _CORRUPT_PATHS:
        # Fail closed: never overwrite a file we could not read.
        return
    import time as _time

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(_json_safe(data), f, indent=2, allow_nan=False)
    # On Windows, replace() fails (WinError 5/32) while another thread has the file
    # open for reading. Retry briefly instead of failing the whole request.
    delay = 0.02
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            _time.sleep(delay)
            delay = min(delay * 2, 0.5)


class BadNumber(ValueError):
    """User-supplied number failed validation (message is user-facing)."""


def _num(
    raw: Any,
    name: str,
    *,
    lo: float | None = None,
    hi: float | None = None,
    integer: bool = False,
) -> float:
    """Parse a finite number within [lo, hi]; raise BadNumber with a plain message.

    Rejects NaN/Infinity/booleans — NaN silently disables every `<=` risk gate.
    """
    import math

    if isinstance(raw, bool) or raw is None or (isinstance(raw, str) and not raw.strip()):
        raise BadNumber(f"{name} must be a number")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise BadNumber(f"{name} must be a number") from None
    if not math.isfinite(v):
        raise BadNumber(f"{name} must be a real number (not NaN/Infinity)")
    if lo is not None and v < lo:
        raise BadNumber(f"{name} must be at least {lo:g}")
    if hi is not None and v > hi:
        raise BadNumber(f"{name} must be at most {hi:g}")
    if integer:
        v = float(int(v))
    return v


def _finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if __import__("math").isfinite(number) else default


_NUMERIC_CFG_LIMITS: dict[str, tuple[float, float]] = {
    "paper_equity": (1.0, 1e9),
    "paper_cash": (0.0, 1e9),
    "daily_profit_target_usd": (0.0, 1e9),
    "max_session_loss_usd": (0.0, 1e9),
    "slip_bps": (0.0, 500.0),
    "fee_bps": (0.0, 500.0),
    "risk_per_trade_pct": (0.0, 5.0),
    "atr_stop_multiple": (0.25, 5.0),
    "signal_ttl_sec": (10.0, 86400.0),
    "demo_signal_interval_sec": (5.0, 86400.0),
    "scan_interval_sec": (15.0, 86400.0),
    "alert_cooldown_sec": (0.0, 86400.0),
    "promotion_min_samples": (1.0, 100000.0),
    "promotion_min_independent_tickers": (1.0, 1000.0),
    "promotion_min_win_rate": (0.0, 1.0),
    "promotion_min_expectancy_usd": (-1e6, 1e6),
    "promotion_min_profit_factor": (0.0, 100.0),
    "promotion_max_drawdown_pct": (0.0, 100.0),
    "promotion_window_days": (1.0, 3650.0),
    "macro_size_mult": (0.1, 1.0),
    "min_net_reward_risk": (0.0, 10.0),
    "evidence_min_samples": (3.0, 10000.0),
    "giveback_stop_pct": (0.0, 100.0),
    "event_guard_before_min": (0.0, 240.0),
    "event_guard_after_min": (0.0, 240.0),
    "wsb_crowd_min_mentions": (5.0, 10000.0),
}


def _sanitize_numeric_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Drop non-finite / out-of-range numbers already on disk back to defaults."""
    for k, (lo, hi) in _NUMERIC_CFG_LIMITS.items():
        if k not in cfg or cfg[k] is None:
            continue
        try:
            _num(cfg[k], k, lo=lo, hi=hi)
        except BadNumber:
            cfg[k] = DEFAULT_CONFIG.get(k)
    ks = cfg.get("kill_switch")
    if isinstance(ks, dict):
        for k in ("max_daily_loss_usd", "max_trades_per_day", "max_position_size_usd"):
            if ks.get(k) in (None, ""):
                continue
            try:
                _num(ks[k], k, lo=0, hi=1e9)
            except BadNumber:
                ks[k] = None
    return cfg


def _clean_kill_switch_in(ks_in: Any) -> dict[str, Any]:
    """Validate kill-switch numbers from a request body (raises BadNumber)."""
    out: dict[str, Any] = {}
    if not isinstance(ks_in, dict):
        return out
    for k in ("max_daily_loss_usd", "max_trades_per_day", "max_position_size_usd"):
        if k not in ks_in:
            continue
        raw = ks_in[k]
        if raw in (None, ""):
            out[k] = None
        else:
            out[k] = _num(raw, k, lo=0, hi=1e9, integer=(k == "max_trades_per_day"))
            if k == "max_trades_per_day":
                out[k] = int(out[k])
    if "armed" in ks_in:
        if not isinstance(ks_in["armed"], bool):
            raise BadNumber("armed must be true or false")
        out["armed"] = ks_in["armed"]
    return out


def _cfg_fee_bps(cfg: dict[str, Any]) -> float:
    """fee_bps with a default only when missing (0 is a valid 'no fees' setting)."""
    raw = cfg.get("fee_bps")
    if raw is None or raw == "":
        return 1.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return v if v >= 0 else 1.0


def load_config() -> dict[str, Any]:
    cfg = _load_json(CONFIG_PATH, None)
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)
        cfg["kill_switch"] = dict(DEFAULT_CONFIG["kill_switch"])
        cfg["watchlist"] = list(DEFAULT_WATCHLIST)
        _save_json(CONFIG_PATH, cfg)
        return cfg
    if not isinstance(cfg, dict):
        _mark_corrupt(CONFIG_PATH, "top-level JSON value must be an object")
        return dict(DEFAULT_CONFIG)
    # merge defaults for new keys
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    if "kill_switch" not in merged or not isinstance(merged["kill_switch"], dict):
        merged["kill_switch"] = dict(DEFAULT_CONFIG["kill_switch"])
    else:
        ks = dict(DEFAULT_CONFIG["kill_switch"])
        ks.update(merged["kill_switch"])
        merged["kill_switch"] = ks
    merged = _sanitize_numeric_cfg(merged)
    # Scrub FX/crypto pair fragments so the UI watchlist matches evaluate rails.
    if isinstance(merged.get("watchlist"), list):
        cleaned = parse_watchlist(merged["watchlist"])
        try:
            from paper_loop import equity_loop_symbols
            equityish = equity_loop_symbols(cleaned)
        except Exception:
            equityish = [t for t in cleaned if t not in {"USD", "BTC", "ETH", "/", "EUR", "GBP"}]
        if cleaned != list(merged.get("watchlist") or []) or equityish != cleaned:
            # Prefer equity-shaped symbols for storage; drop deny-list leftovers.
            if equityish != list(merged.get("watchlist") or []):
                merged["watchlist"] = equityish
                try:
                    _save_json(CONFIG_PATH, merged)
                except Exception:
                    pass
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    _save_json(CONFIG_PATH, cfg)


def load_signals() -> list[dict[str, Any]]:
    signals = _load_json(SIGNALS_PATH, [])
    if not isinstance(signals, list) or any(not isinstance(s, dict) for s in signals):
        _mark_corrupt(SIGNALS_PATH, "top-level JSON value must be a list of objects")
        return []
    return signals


SIGNALS_KEEP = 300  # newest non-pending signals kept on disk (list is newest-first)


def save_signals(signals: list[dict[str, Any]]) -> None:
    """Persist signals; prune old resolved ones so the file doesn't grow forever.

    Pending/approving signals are always kept regardless of age.
    """
    kept = [
        s
        for i, s in enumerate(signals)
        if i < SIGNALS_KEEP or (isinstance(s, dict) and s.get("status") in ("pending", "approving", "broker_pending"))
    ]
    _save_json(SIGNALS_PATH, kept)


def signal_workspace(signal: dict[str, Any]) -> str:
    if signal.get("workspace") in ("live", "paper"):
        return signal["workspace"]
    return "paper" if signal.get("mode_at_create") in ("manual", "auto_paper") else "live"


def paper_research_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Local simulation settings; never changes the main broker mode/session."""
    result = dict(cfg, mode="auto_paper" if cfg.get("paper_auto_approve") else "manual",
                session_active=bool(cfg.get("paper_research_enabled")),
                risk_preset=cfg.get("paper_risk_preset", "mid"),
                daily_profit_target_usd=None, max_session_loss_usd=None,
                kill_switch=dict(DEFAULT_CONFIG["kill_switch"]), _paper_research=True)
    import moss_policy
    p = moss_policy.settings(cfg)
    if p["enabled"]:
        result.update(rth_only=True, paper_fractional_enabled=True,
                      kill_switch={"armed":True,"max_trades_per_day":p["max_trades_per_day"],
                                   "max_daily_loss_usd":float(cfg.get("paper_equity") or 0)*p["max_daily_loss_pct"]/100,
                                   "max_position_size_usd":p["max_order_usd"]})
    return result


def _size_paper_research(sig, cfg, ledger):
    """Share the thesis, never the live workspace's suggested position size."""
    preset = get_preset(cfg.get("risk_preset"))
    px = _finite_float(sig.get("signal_price"), 0) or 0
    from trade_planner import paper_quantity
    fractional = bool(cfg.get("paper_fractional_enabled", False))
    held = sum(float(p.get("shares") or 0) for p in ledger.get("positions", [])
               if p.get("ticker") == sig.get("ticker") and p.get("side") == "long")
    if sig.get("side") == "sell":
        shares = held
    elif px > 0:
        equity = max(0, float(ledger.get("equity") or 0))
        budget = max(0, equity * preset["max_position_pct"] / 100 - held * px)
        dollar_cap = _finite_float(cfg.get("paper_order_budget"), 0) or 0
        entry_px = px * (1 + float(cfg.get("slip_bps", 5)) / 10000)
        fee_factor = 1 + _cfg_fee_bps(cfg) / 10000
        quantity_cap = min(budget / entry_px,
                           max(0, float(ledger.get("cash") or 0)) / (entry_px * fee_factor))
        if dollar_cap > 0:
            quantity_cap = min(quantity_cap, dollar_cap / (entry_px * fee_factor))
        shares = paper_quantity(quantity_cap, fractional)
    else:
        shares = 0
    multiplier = _finite_float(sig.get("advisory_size_mult"), 1.0)
    shares = paper_quantity(shares * max(0, min(1, multiplier)), fractional or sig.get("side") == "sell")
    sig.update(suggested_shares=shares, suggested_notional=round(shares * px, 2),
               preset=cfg.get("risk_preset"), stop_r=preset["stop_r"], target_r=preset["target_r"])
    # Paper protection must also use the paper preset when geometry is resolved.
    sig.pop("stop", None)
    sig.pop("target", None)


def _pending_scan_blocks(signal: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if signal.get("status") in ("approving", "broker_pending"):
        return True
    if signal.get("status") != "pending":
        return False
    expected = "live" if cfg.get("mode") in ("live_manual", "auto_live") else "paper"
    if signal_workspace(signal) != expected:
        return False
    quote = signal.get("quote")
    if not isinstance(quote, dict):
        return False
    return not bool(signal_execution_block(signal, broker=expected == "live"))


def signal_execution_block(signal: dict[str, Any], *, broker: bool = False) -> str | None:
    """Research may be retained without becoming an executable instruction."""
    if signal.get("data_error") or signal.get("llm_error") == "stale_or_unverified_market_data":
        return "Market data unavailable: wait for a fresh quote and decision"
    if signal.get("llm_error"):
        return "Brain error: wait for a new decision"
    if signal.get("execution_block"):
        return "Research gate: " + str(signal["execution_block"])
    if signal.get("abstain") or str(signal.get("llm_side") or "").lower() in ("flat", "hold"):
        return "Hold / flat decision: no order"
    if str(signal.get("side") or "").lower() not in ("buy", "sell"):
        return "No executable buy or sell decision"
    if "demo" in (signal.get("sources") or []) or signal.get("synthetic"):
        return "Synthetic research cannot be traded"
    if isinstance(signal.get("quote"), dict):
        import data_sources as ds
        q = signal["quote"]
        if not ds.quote_snapshot(q.get("price"), q.get("market_time"), q.get("source", "unknown"))["fresh"]:
            return "Research price is stale or unverified; request a fresh scan"
    if broker:
        if str(signal.get("llm_model") or "").startswith("mock") or signal.get("brain_mode") == "mock" or "mock_brain" in (signal.get("llm_risks") or []):
            return "Mock research is for paper practice only"
        if str(signal.get("verdict") or "").upper() == "AVOID":
            return "AVOID research cannot be sent to the broker"
    return None


def _signal_ui_projection(signal: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Keep state polls small without changing persisted signal fidelity."""
    fields = (
        "id", "ticker", "side", "status", "ts", "created_at", "expires_at",
        "confidence", "signal_price", "suggested_shares", "reason", "verdict",
        "lateness_label", "entry_quality", "earnings", "rel_vol", "research_flags",
        "research_flag", "llm_side", "llm_error", "data_error", "llm_thesis", "citations",
        "screener_citations", "gap_pct", "reject_reason", "size_mult_suggested",
        "llm_model", "llm_confidence", "llm_risks", "brain_mode", "routed", "router_reason", "quote",
        "workspace", "source_signal_id", "mode_at_create", "decision_record_id", "execution_block", "manual_order", "origin",
    )
    out = {key: signal[key] for key in fields if key in signal}
    cfg = cfg or load_config()
    out["workspace"] = signal_workspace(signal)
    blocked = signal_execution_block(signal, broker=out["workspace"] == "live" and cfg.get("mode") in ("live_manual", "auto_live"))
    out.update(actionable=not bool(blocked), execution_block=blocked)
    if signal.get("llm_error") or signal.get("abstain") or signal.get("llm_side") in ("flat", "hold"):
        out["side"] = "hold"
        out["confidence"] = 0 if signal.get("llm_error") else signal.get("llm_confidence", signal.get("confidence", 0))
    if isinstance(out.get("quote"), dict):
        import data_sources as ds
        q = out["quote"]
        out["quote"] = ds.quote_snapshot(q.get("price"), q.get("market_time"), q.get("source", "unknown"))
        out["quote"]["received_at"] = q.get("received_at")
    for key, limit in (("reason", 320), ("llm_thesis", 600), ("reject_reason", 240)):
        if key in out and out[key] is not None:
            out[key] = str(out[key])[:limit]
    for key in ("citations", "screener_citations"):
        if isinstance(out.get(key), list):
            out[key] = [
                {k: item[k] for k in ("label", "key", "value") if k in item}
                for item in out[key][:8]
                if isinstance(item, dict)
            ]
            if not signal.get("quote"):
                for item in out[key]:
                    if item.get("key") == "as_of" or item.get("label") == "As of":
                        item["label"] = "Legacy scan time (price time unknown)"
    fill = signal.get("fill")
    if isinstance(fill, dict):
        out["fill"] = {
            key: fill[key]
            for key in ("shares", "price", "source", "ts", "confirmed", "broker")
            if key in fill
        }
    return out


def load_ledger() -> dict[str, Any]:
    default = {
        "equity": 100_000.0,
        "cash": 100_000.0,
        "positions": [],
        "fills": [],
        "daily": {},  # date -> {trades, pnl, realized}
        "equity_curve": [],
        "equity_curve_day": None,
    }
    data = _load_json(LEDGER_PATH, None)
    if data is None:
        cfg = load_config()
        default["equity"] = float(cfg.get("paper_equity", 100_000))
        default["cash"] = float(cfg.get("paper_cash", default["equity"]))
        _save_json(LEDGER_PATH, default)
        return default
    if not isinstance(data, dict):
        _mark_corrupt(LEDGER_PATH, "top-level JSON value must be an object")
        return default
    for key, kind in (("positions", list), ("fills", list), ("daily", dict)):
        if not isinstance(data.get(key, default[key]), kind):
            _mark_corrupt(LEDGER_PATH, f"ledger.{key} must be {kind.__name__}")
            return default
    data.setdefault("positions", [])
    data.setdefault("fills", [])
    data.setdefault("daily", {})
    return data


def save_ledger(ledger: dict[str, Any]) -> None:
    """Persist ledger; callers should hold `_lock` for RMW, but we re-enter safely."""
    with _lock:
        _save_json(LEDGER_PATH, ledger)


def load_journal() -> list[dict[str, Any]]:
    journal = _load_json(JOURNAL_PATH, [])
    if not isinstance(journal, list) or any(not isinstance(entry, dict) for entry in journal):
        _mark_corrupt(JOURNAL_PATH, "top-level JSON value must be a list of objects")
        return []
    return journal


def append_journal(action: str, detail: dict[str, Any] | None = None) -> None:
    """Append journal entry under `_lock` (RMW-safe)."""
    with _lock:
        journal = load_journal()
        entry = {
            "id": str(uuid.uuid4()),
            "ts": _now_iso(),
            "action": action,
            "detail": detail or {},
        }
        journal.insert(0, entry)
        # keep last 500
        save_journal(journal[:500])


def save_journal(journal: list[dict[str, Any]]) -> None:
    _save_json(JOURNAL_PATH, journal)


def get_preset(name: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    key = name or cfg.get("risk_preset", "mid")
    return dict(RISK_PRESETS.get(key, RISK_PRESETS["mid"]))


# ---------------------------------------------------------------------------
# Broker adapter (Alpaca paper-first via broker_alpaca)
# ---------------------------------------------------------------------------

def _broker_public_status() -> dict[str, Any]:
    """Safe broker chip for health/state — never includes secrets."""
    try:
        import broker_router as alpaca
    except ImportError:
        return {
            "broker": "alpaca",
            "configured": False,
            "paper_mode": True,
            "endpoint": None,
            "status": "live_not_wired",
            "connected_label": "Broker module missing",
        }
    try:
        return alpaca.public_status()
    except Exception as exc:  # noqa: BLE001
        return {
            "broker": "alpaca",
            "configured": False,
            "paper_mode": True,
            "endpoint": None,
            "status": "error",
            "connected_label": "Broker status error",
            "error": str(exc)[:200],
        }


def live_broker_place_order(order: dict[str, Any]) -> dict[str, Any]:
    """
    Place equity order via Alpaca when keys are configured.
    Default: paper-api.alpaca.markets (ALPACA_PAPER=true).
    Journals every attempt. Never fakes a successful broker fill on failure.
    Statuses: paper_submitted | live_submitted | live_not_configured | live_not_wired | error
    Caller MUST run can_take_trade / size caps BEFORE calling this.
    """
    try:
        import broker_router as alpaca
    except ImportError:
        append_journal(
            "broker_attempt",
            {
                "status": "live_not_wired",
                "order": order,
                "note": "broker_alpaca module missing",
            },
        )
        return {
            "status": "live_not_wired",
            "ok": False,
            "message": "broker_alpaca module missing — no broker API call.",
            "order": order,
            "paper_mode": True,
        }

    try:
        result = alpaca.place_from_desk_order(order if isinstance(order, dict) else {})
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "status": "error",
            "error": str(exc)[:300],
            "order": order,
            "broker": "alpaca",
            "paper_mode": True,
        }
        try:
            result["paper_mode"] = alpaca.paper_mode()
        except Exception:
            pass

    detail = {
        "status": result.get("status"),
        "ok": bool(result.get("ok")),
        "paper_mode": result.get("paper_mode"),
        "endpoint": result.get("endpoint"),
        "order_id": result.get("order_id"),
        "broker": result.get("broker") or "alpaca",
        "order": result.get("order") or order,
        "error": result.get("error") or result.get("message"),
        "http_status": result.get("http_status"),
    }
    append_journal("broker_attempt", detail)
    return result


def _broker_is_configured() -> bool:
    try:
        import broker_router as alpaca
        return bool(alpaca.is_configured())
    except Exception:
        return False


def _cap_shares_for_broker(
    sig: dict[str, Any],
    cfg: dict[str, Any],
    ledger: dict[str, Any],
    *,
    equity_override: float | None = None,
    allow_fetch: bool = True,
    existing_notional: float = 0.0,
) -> tuple[int, float, str | None]:
    """Apply same size / loss caps as paper path before broker qty submit.

    Returns (shares, notional, error_or_None). shares may be reduced by max_position_pct.
    `equity_override` = the broker account's equity (size real orders off the real
    account, not the paper ledger). `allow_fetch=False` when called under `_lock`.
    """
    preset = get_preset(cfg.get("risk_preset"))
    if equity_override is not None and equity_override > 0:
        equity = float(equity_override)
    else:
        equity = float(ledger.get("equity", cfg.get("paper_equity", 100_000)) or 100_000)
    px = float(sig.get("signal_price") or 0)
    if (not px or px <= 0) and allow_fetch:
        live = fetch_last_price(str(sig.get("ticker") or ""))
        if live:
            px = float(live)
            sig["signal_price"] = px
    if not px or px <= 0:
        return 0, 0.0, "no_real_quote"
    shares = max(0, int(sig.get("suggested_shares") or 0))
    if shares <= 0:
        return 0, 0.0, "zero_shares"
    atr = _finite_float(sig.get("atr_usd"))
    risk_pct = _finite_float(cfg.get("risk_per_trade_pct"), 0.0) or 0.0
    atr_multiple = _finite_float(cfg.get("atr_stop_multiple"), 1.0) or 1.0
    if atr and atr > 0 and risk_pct > 0 and atr_multiple > 0:
        risk_budget = equity * risk_pct / 100.0
        risk_per_share = atr * atr_multiple
        risk_shares = int(risk_budget / risk_per_share)
        if risk_shares <= 0:
            return 0, 0.0, "risk_budget_below_one_share"
        if shares > risk_shares:
            shares = risk_shares
            sig["suggested_shares"] = shares
            sig["size_capped_for_atr_risk"] = True
            sig["atr_risk_budget_usd"] = round(risk_budget, 2)
            sig["atr_stop_distance_usd"] = round(risk_per_share, 4)
    max_pos = max(0.0, equity * (float(preset["max_position_pct"]) / 100.0) - existing_notional)
    max_shares = int(max_pos / px)
    if shares > max_shares:
        shares = max_shares
        sig["suggested_shares"] = shares
        sig["size_capped_for_broker"] = True
    # Optional kill-switch max position USD
    ks = cfg.get("kill_switch") or {}
    if ks.get("armed") and ks.get("max_position_size_usd") is not None:
        try:
            ks_max = float(ks["max_position_size_usd"])
            ks_shares = int(max(0, ks_max - existing_notional) / px)
            if shares > ks_shares:
                shares = ks_shares
                sig["suggested_shares"] = shares
                sig["size_capped_for_broker"] = True
        except (TypeError, ValueError):
            pass
    notional = round(shares * px, 2)
    if shares <= 0:
        return 0, 0.0, "Position limit reached (including holdings and working orders)"
    return shares, notional, None


_BROKER_SUBMIT_LOCK = threading.Lock()
# Mode changes take this lock before _lock; slow account reads and fill polling
# stay outside it so switching off broker execution can revoke an in-flight check.
_BROKER_EXEC_LOCK = threading.RLock()
BROKER_FILL_WAIT_SEC = float(os.environ.get("BROKER_FILL_WAIT_SEC", "6") or 6)
_BROKER_REVIEWS: dict[str, dict] = {}


def _review_terms(sig, cfg):
    return {"signal": {key: sig.get(key) for key in
                       ("id", "ticker", "side", "suggested_shares", "workspace", "expires_at", "quote", "signal_price", "manual_order")},
            "mode": cfg.get("mode"), "identity": cfg.get("broker_identity")}


def _execution_deadline(sig):
    """An absolute deadline survives waits in the app and the broker owner queue."""
    import data_sources as ds
    quote = sig.get("execution_quote") or {}
    if not ds.quote_snapshot(quote.get("price"), quote.get("market_time"), quote.get("source", "unknown"))["fresh"]:
        raise ValueError("Execution quote is stale or unverified; request a fresh review")
    deadlines = [datetime.fromisoformat(quote["market_time"].replace("Z", "+00:00")) + timedelta(seconds=120)]
    for key in ("expires_at", "review_expires_at"):
        if sig.get(key):
            stamp = datetime.fromisoformat(str(sig[key]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Signal expiry must include a timezone")
            deadlines.append(stamp)
    deadline = min(deadlines)
    if deadline <= datetime.now(timezone.utc):
        raise ValueError("Order review or signal expired; request a fresh review")
    return deadline.isoformat()


def _broker_fill_record(
    sig: dict[str, Any],
    broker_resp: dict[str, Any],
    source: str,
    confirm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Honest journal/UI fill marker for broker-only success (not a local ledger write).

    Uses Alpaca's filled_avg_price / filled_qty when the order confirmed. If the order
    was still open when we stopped polling, the price is the signal price and the
    record is flagged confirmed=False / price_estimated=True.
    """
    confirm = confirm or {}
    shares: float = float(sig.get("suggested_shares") or 0)
    try:
        shares = float(broker_resp.get("qty") or shares)
    except (TypeError, ValueError):
        pass
    est_px = float(sig.get("signal_price") or 0)
    state = confirm.get("state") or "unknown"
    filled_px = confirm.get("filled_avg_price")
    filled_qty = float(confirm.get("filled_qty") or 0)
    if state in ("filled", "partially_filled") and filled_px and filled_qty > 0:
        px = float(filled_px)
        shares = filled_qty
        price_source = "broker_filled"
        confirmed = True
    else:
        px = est_px
        price_source = "signal_estimate_unconfirmed"
        confirmed = False
    shares_out: float | int = int(shares) if float(shares).is_integer() else shares
    return {
        "id": str(uuid.uuid4()),
        "ts": _now_iso(),
        "signal_id": sig.get("id"),
        "ticker": str(sig.get("ticker") or "").upper(),
        "side": sig.get("side"),
        "shares": shares_out,
        "price": px,
        "notional": round(float(shares) * px, 2) if px else None,
        "source": source,
        "simulated": False,
        "broker": True,
        "broker_status": broker_resp.get("status"),
        "broker_fill_state": state,
        "alpaca_status": confirm.get("alpaca_status") or broker_resp.get("alpaca_status"),
        "confirmed": confirmed,
        "price_estimated": not confirmed,
        "order_id": broker_resp.get("order_id"),
        "broker_identity": copy.deepcopy((broker_resp.get("order") or {}).get("broker_identity") or sig.get("review_identity")),
        "execution_versions": copy.deepcopy(confirm.get("execution_versions") or {}),
        "execution_correction": confirm.get("execution_correction") is True,
        "paper_mode": broker_resp.get("paper_mode"),
        "endpoint": broker_resp.get("endpoint"),
        "price_source": price_source,
        "slip_bps": (
            round(abs(px - float(sig.get("signal_price") or px)) / px * 10000, 3)
            if px and sig.get("signal_price") else None
        ),
        "slip_usd": (
            round(abs(px - float(sig.get("signal_price") or px)) * shares, 4)
            if px and sig.get("signal_price") else 0.0
        ),
        "fee_usd": 0.0,
    }


def _broker_position_qty(ticker: str) -> tuple[float, str | None]:
    """Signed share count held at the broker for `ticker` (short < 0), or an error."""
    try:
        import broker_router as alpaca

        res = alpaca.get_positions()
    except Exception as exc:  # noqa: BLE001
        return 0.0, f"Couldn't read broker positions ({str(exc)[:80]}) — order not sent"
    if not res.get("ok"):
        return 0.0, "Couldn't read broker positions — order not sent"
    sym = str(ticker or "").upper().replace("/", "")
    for p in res.get("positions") or []:
        if str(p.get("symbol") or "").upper() != sym:
            continue
        q = _finite_float(p.get("qty"))
        if q is None:
            return 0.0, "Invalid broker position quantity; cannot verify exposure"
        if str(p.get("side") or "").lower() == "short":
            q = -abs(q)
        return q, None
    return 0.0, None


_BROKER_BOOK_CACHE: dict[str, Any] = {"at": 0.0, "val": None}
_BROKER_BOOK_TTL = 15.0


def _broker_book_cached() -> dict[str, Any] | None:
    """Configured-broker positions + today's P&L for the UI (15 s cache).

    Broker fills never touch the paper ledger, so without this the screen showed
    'no positions' right after a real order.
    """
    import time as _t

    if not _broker_is_configured():
        return None
    now = _t.monotonic()
    import broker_router as broker
    status = broker.public_status()
    cache_key = (status.get("broker"), status.get("endpoint"), status.get("connected"),
                 status.get("paper_mode"), json.dumps(load_config().get("broker_identity"), sort_keys=True))
    if _BROKER_BOOK_CACHE.get("key") == cache_key and _BROKER_BOOK_CACHE["val"] is not None and now - _BROKER_BOOK_CACHE["at"] < _BROKER_BOOK_TTL:
        return _BROKER_BOOK_CACHE["val"]
    try:
        import broker_router as alpaca

        acct = alpaca.get_account()
        pos = alpaca.get_positions()
    except Exception as exc:  # noqa: BLE001
        val = {"ok": False, "error": str(exc)[:120], "account_id": None}
    else:
        a = (acct or {}).get("account") or {}
        rows = []
        for p in (pos or {}).get("positions") or []:
            try:
                avg = float(p.get("avg_entry_price") or 0)
                last = _safe_money(p.get("current_price"))
                if last is None and avg > 0:
                    last = avg  # cost-basis fallback when broker mark not streamed yet
                rows.append({
                    "ticker": str(p.get("symbol") or "").upper(),
                    "side": str(p.get("side") or "long").lower(),
                    "shares": abs(float(p.get("qty") or 0)),
                    "avg_price": avg,
                    "last": last,
                    "open_pnl_usd": _safe_money(p.get("unrealized_pl")),
                    "mark_is_cost_basis": _safe_money(p.get("current_price")) is None and avg > 0,
                })
            except (TypeError, ValueError):
                continue
        try:
            day_pnl = round(float(a["day_pnl"]), 2) if a.get("day_pnl") is not None else round(float(a["equity"]) - float(a["last_equity"]), 2)
        except (KeyError, TypeError, ValueError):
            day_pnl = None
        val = {
            "ok": bool(acct.get("ok")) and bool(pos.get("ok")),
            "paper_mode": alpaca.paper_mode(),
            "equity": _safe_money(a.get("equity")),
            "cash": _safe_money(a.get("cash")),
            "buying_power": _safe_money(a.get("buying_power")),
            "day_pnl_usd": day_pnl,
            "risk_ready": acct.get("risk_ready", day_pnl is not None),
            "risk_error": acct.get("risk_error"),
            "account_window_pnl": a.get("account_window_pnl"),
            "pnl_diagnostics": acct.get("pnl_diagnostics"),
            "positions": rows,
            "account_id": acct.get("account_id") or a.get("id"),
            "error": None if (acct.get("ok") and pos.get("ok")) else acct.get("error") or pos.get("error") or "Couldn't reach configured broker",
        }
        if acct.get("ok") and acct.get("risk_ready") is True and a.get("day_pnl") is not None and day_pnl is not None:
            _note_day_pnl_peak(day_pnl)  # the give-back guard also sees peaks between orders
    _BROKER_BOOK_CACHE.update(at=now, val=val, key=cache_key)
    return val


def _safe_money(v: Any) -> float | None:
    try:
        value = _finite_float(v)
        return round(value, 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def _money_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    """Extra money fields for the UI: open (unrealized) P&L and the broker book."""
    with _lock:
        ledger = load_ledger()
    out: dict[str, Any] = {
        "bleed": bleed_status(ledger, cfg),
        "benchmark": benchmark_status(ledger, cfg),
        "open_pnl_usd": round(_unrealized_mtm(ledger, recent_marks()), 2),
        "fees_today_usd": round(float((ledger.get("daily") or {}).get(_today_str(), {}).get("fees") or 0), 2),
        "broker_trades_today": _broker_trades_today(ledger),
    }
    if cfg.get("mode") in ("auto_live", "live_manual") or _broker_trades_today(ledger):
        out["broker_book"] = _broker_book_cached()
    out["live_goal"] = _live_goal_view(cfg, out.get("broker_book"))
    # Strategy rails snapshot for display strip (never authorizes orders).
    la = cfg.get("live_agent") if isinstance(cfg.get("live_agent"), dict) else None
    pol = (la or {}).get("policy") if isinstance((la or {}).get("policy"), dict) else {}
    ks = cfg.get("kill_switch") if isinstance(cfg.get("kill_switch"), dict) else {}
    book = out.get("broker_book") if isinstance(out.get("broker_book"), dict) else {}
    trades_today = int(out.get("broker_trades_today") or 0)
    max_trades = None
    if la and pol.get("max_orders_per_day") is not None:
        try:
            max_trades = int(pol["max_orders_per_day"])
        except (TypeError, ValueError):
            max_trades = None
    elif ks.get("armed") and ks.get("max_trades_per_day") is not None:
        try:
            max_trades = int(ks["max_trades_per_day"])
        except (TypeError, ValueError):
            max_trades = None
    symbols = list(pol.get("symbols") or []) if la else []
    eval_symbols: list[str] = []
    eval_count = 0
    if la:
        try:
            import live_agent as _la_mod
            eval_symbols = _la_mod.order_universe(cfg, pol)
            eval_count = len(eval_symbols)
        except Exception:
            try:
                eval_symbols = paper_loop_mod.equity_loop_symbols(list(cfg.get("watchlist") or []))
                eval_count = len(eval_symbols)
            except Exception:
                eval_symbols, eval_count = [], 0
    # Live auto-orders track the full evaluated equity universe (not SPY-only).
    order_symbols = list(eval_symbols) if la else []
    spy_only_orders = False
    out["strategy_status"] = {
        "mode": cfg.get("mode"),
        "live_agent_enabled": bool(la and la.get("enabled")),
        "symbols": order_symbols or symbols,
        "order_symbols": order_symbols,
        "spy_only": spy_only_orders,
        "eval_symbols_count": eval_count,
        "eval_symbols_sample": eval_symbols[:12],
        "eval_full_universe": bool(la and eval_count > 1),
        "universe_label": (
            (
                f"Live auto + eval: full equity watchlist ({eval_count})"
                + (f" · sample {', '.join(eval_symbols[:6])}" if eval_symbols else "")
                + (f" (+{eval_count-6} more)" if eval_count > 6 else "")
            )
            if la
            else ("Universe: not set")
        ),
        "max_order_usd": _finite_float(pol.get("max_order_usd") if la else ks.get("max_position_size_usd")),
        "max_daily_loss_usd": _finite_float(pol.get("max_daily_loss_usd") if la else ks.get("max_daily_loss_usd")),
        "max_orders_per_day": max_trades,
        "orders_used": trades_today,
        "orders_left": (max(0, max_trades - trades_today) if max_trades is not None else None),
        "min_confidence": _finite_float(pol.get("min_confidence")) if la else None,
        "risk_ready": bool(book.get("risk_ready")) if book else False,
        "risk_error": book.get("risk_error") if book else None,
        "kill_switch_armed": bool(ks.get("armed")),
        "note": (
            "Status strip only — does not place orders. "
            "Live Moss auto-orders use the full evaluated equity universe under broker rules."
        ),
    }
    return out


def market_regime_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify broad-market tone from fresh SPY/QQQ radar snapshots.

    Missing or stale benchmark data is explicitly unknown, never bullish or bearish.
    The optional gate can therefore fail closed when enabled without fabricating a
    regime from an unrelated mover.
    """
    cfg = cfg or {}
    cached = market_radar.get_cached()
    age = _finite_float(cached.get("age_sec"))
    rows = {
        str(row.get("ticker") or "").upper(): row
        for row in cached.get("movers") or []
        if isinstance(row, dict)
    }
    benchmarks = {}
    for ticker in ("SPY", "QQQ"):
        row = rows.get(ticker)
        pct = _finite_float(row.get("pct_change")) if row else None
        if pct is not None:
            benchmarks[ticker] = round(pct, 3)
    fresh = age is not None and age <= 900
    if not fresh or len(benchmarks) < 2:
        regime = "unknown"
    elif all(value <= -0.5 for value in benchmarks.values()) or any(
        value <= -1.0 for value in benchmarks.values()
    ):
        regime = "weak"
    elif all(value >= 0.5 for value in benchmarks.values()):
        regime = "strong"
    else:
        regime = "mixed"
    return {
        "enabled": bool(cfg.get("market_regime_gate_enabled", False)),
        "regime": regime,
        "benchmarks": benchmarks,
        "fresh": fresh,
        "age_sec": age,
        "action": "downgrade_long_pass" if regime == "weak" else "none",
    }


# ---------------------------------------------------------------------------
# Slow-bleed guard: many small losing trades + fees can drain an account while
# nobody is watching. Pause new trades when the recent closed trades are net
# negative AFTER costs; the user resumes deliberately (bleed_ack_at).
# ---------------------------------------------------------------------------
BLEED_WINDOW = 20      # look at this many most-recent closed trades
BLEED_MIN_TRADES = 10  # need at least this many before judging


def bleed_status(ledger: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    enabled = cfg.get("bleed_guard_enabled", True) is not False
    ack = str(cfg.get("bleed_ack_at") or "")
    fills = [
        f for f in list(ledger.get("fills") or []) + list(ledger.get("fills_archive") or [])
        if isinstance(f, dict) and not f.get("broker")
    ]
    fills.sort(key=lambda f: str(f.get("ts") or ""), reverse=True)
    closed = 0
    realized = 0.0
    fees = 0.0
    seen: set[str] = set()
    for f in fills:
        fid = str(f.get("id") or "")
        if fid and fid in seen:
            continue
        seen.add(fid)
        if ack and str(f.get("ts") or "") <= ack:
            break
        try:
            fees += float(f.get("fee_usd") or 0)
        except (TypeError, ValueError):
            pass
        if str(f.get("side") or "").lower() == "sell" or "realized_pnl" in f:
            closed += 1
            try:
                realized += float(f.get("realized_pnl") or 0)
            except (TypeError, ValueError):
                pass
        if closed >= BLEED_WINDOW:
            break
    net = round(realized - fees, 2)
    paused = enabled and closed >= BLEED_MIN_TRADES and net < 0
    return {
        "enabled": enabled,
        "paused": paused,
        "closed_trades": closed,
        "window": BLEED_WINDOW,
        "min_trades": BLEED_MIN_TRADES,
        "realized_usd": round(realized, 2),
        "fees_usd": round(fees, 2),
        "net_usd": net,
        "since": ack or None,
    }


# ---------------------------------------------------------------------------
# Benchmark: would simply holding SPY have done better since the session began?
# ---------------------------------------------------------------------------
_SPY_CACHE: dict[str, Any] = {"at": 0.0, "px": None}


def _spy_now() -> float | None:
    import time as _t

    now = _t.monotonic()
    if _SPY_CACHE["px"] is not None and now - _SPY_CACHE["at"] < 60:
        return _SPY_CACHE["px"]
    px = fetch_last_price("SPY")
    if px:
        _SPY_CACHE.update(at=now, px=float(px))
    return _SPY_CACHE["px"]


def benchmark_status(ledger: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    try:
        spy0 = float(cfg.get("session_spy_start") or 0)
        bank = float(cfg.get("session_bank_start") or 0)
    except (TypeError, ValueError):
        return None
    if spy0 <= 0 or bank <= 0:
        return None
    spy1 = _spy_now()
    if not spy1:
        return {"ok": False, "error": "SPY price unavailable"}
    equity = _ledger_equity_mtm(ledger, recent_marks())
    desk_ret = equity / bank - 1.0
    spy_ret = spy1 / spy0 - 1.0
    return {
        "ok": True,
        "since": cfg.get("session_started_at"),
        "bank_usd": round(bank, 2),
        "desk_return_pct": round(desk_ret * 100, 3),
        "desk_usd": round(equity - bank, 2),
        "spy_return_pct": round(spy_ret * 100, 3),
        "spy_usd": round(bank * spy_ret, 2),
        "ahead_usd": round((equity - bank) - bank * spy_ret, 2),
        "spy_start": round(spy0, 2),
        "spy_now": round(spy1, 2),
    }


def _broker_trades_today(ledger: dict[str, Any]) -> int:
    day = (ledger.get("broker_daily") or {}).get(_today_str()) or {}
    try:
        return int(day.get("trades") or 0)
    except (TypeError, ValueError):
        return 0


def _record_broker_trade() -> None:
    """Count a broker order toward today's trade cap (broker fills never touch the paper book)."""
    with _lock:
        ledger = load_ledger()
        bd = ledger.setdefault("broker_daily", {})
        day = bd.setdefault(_today_str(), {"trades": 0})
        day["trades"] = int(day.get("trades") or 0) + 1
        for old in sorted(bd)[:-10]:  # keep ~2 weeks
            bd.pop(old, None)
        save_ledger(ledger)


def _broker_order_terminal(confirm: dict[str, Any]) -> bool:
    if "terminal" in confirm:
        return confirm["terminal"] is True
    status = str(confirm.get("alpaca_status") or confirm.get("broker_status") or "").lower()
    return confirm.get("state") == "filled" or status in {
        "filled", "canceled", "cancelled", "expired", "rejected", "suspended", "stopped", "inactive", "apicancelled"
    }


def _preserve_broker_state(previous: dict[str, Any], replacement: dict[str, Any]) -> None:
    """Paper lifecycle operations must never reset broker lifecycle records."""
    for key, value in previous.items():
        if key.startswith("broker_") or key == "pending_broker_orders":
            replacement[key] = value


def _apply_broker_update(sig, broker, confirm, source):
    """Persist cumulative execution, pending remainder and count together.

    Caller holds _BROKER_SUBMIT_LOCK. Updating an existing fill never counts it
    as a new trade. Unknown/nonterminal states always remain in durable tracking.
    """
    order_id = str(broker["order_id"])
    terminal = _broker_order_terminal(confirm)
    quantity = _finite_float(confirm.get("filled_qty"), 0) or 0
    price = _finite_float(confirm.get("filled_avg_price"), 0) or 0
    with _lock:
        ledger = load_ledger()
        fills = ledger.setdefault("broker_fills", [])
        prior = next((f for f in fills if str(f.get("order_id")) == order_id), None)
        fill = prior
        versions = confirm.get("execution_versions") or {}
        old_versions = (prior or {}).get("execution_versions") or {}
        pending_versions = (prior or {}).get("pending_execution_versions") or {}
        versioned = isinstance(versions, dict) and bool(versions) and all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
            for k, v in versions.items())
        if pending_versions and (terminal or quantity > 0 or confirm.get("execution_correction")) and (
                not versioned or any(versions.get(k, 0) < v for k, v in pending_versions.items())):
            # An incomplete newer correction remains unresolved even if an older
            # terminal snapshot arrives later (including cancellation/no fill).
            return prior, False
        if prior and old_versions and (quantity > 0 or confirm.get("state") == "filled") and (
                not versioned or any(versions.get(k, 0) < v for k, v in old_versions.items())):
            # A late report cannot resurrect a superseded execution revision.
            return prior, bool(prior.get("broker_reconciled"))
        corrected = (versioned and confirm.get("execution_correction") is True
                     and confirm.get("execution_details_verified") is True
                     and any(v > old_versions.get(k, 0) for k, v in versions.items()))
        if ((quantity > 0 and price > 0 and (prior is None or quantity >= float(prior.get("shares") or 0) or corrected))
                or (prior is not None and corrected and quantity == 0)):
            fill = _broker_fill_record(sig, broker, source, confirm)
            if corrected and quantity == 0:
                fill.update(shares=0, price=0, notional=0, confirmed=True, price_estimated=False,
                            price_source="broker_execution_bust", broker_fill_state="voided", fee_usd=0)
            if prior:
                fill["id"], fill["ts"] = prior["id"], prior["ts"]
                fills[fills.index(prior)] = fill
            else:
                fills.insert(0, fill)
            counted = ledger.setdefault("broker_counted_orders", {})
            if order_id not in counted and prior is None:
                day = _today_str()
                counted[order_id] = day
                stats = ledger.setdefault("broker_daily", {}).setdefault(day, {"trades": 0})
                stats["trades"] = int(stats.get("trades") or 0) + 1
        # Missing execution details cannot resolve an order known to have filled.
        if (confirm.get("state") == "filled" and quantity <= 0 and not corrected) or (quantity > 0 and (fill is None or float(fill.get("shares") or 0) != quantity)):
            terminal = False
        if fill:
            fill["broker_reconciled"] = terminal
            fill["alpaca_status"] = confirm.get("alpaca_status") or fill.get("alpaca_status")
            if confirm.get("execution_correction") and not confirm.get("execution_details_verified"):
                terminal = False
                fill["broker_reconciled"] = False
                fill["confirmed"] = False
                fill["reconciliation_error"] = confirm.get("error") or "Execution correction is awaiting complete broker evidence"
                if versioned:
                    fill["pending_execution_versions"] = {
                        k: max(old_versions.get(k, 0), pending_versions.get(k, 0), versions.get(k, 0))
                        for k in old_versions.keys() | pending_versions.keys() | versions.keys()
                    }
        pending = ledger.setdefault("pending_broker_orders", [])
        intent_id = broker.get("intent_id")
        item = next((x for x in pending if str(x.get("order_id")) == order_id
                     or (intent_id and x.get("intent_id") == intent_id)), None)
        if terminal:
            ledger["pending_broker_orders"] = [x for x in pending if x is not item]
        else:
            if item is None:
                item = {"order_id": order_id, "created_at": _now_iso()}
                pending.append(item)
            item.update(order_id=order_id, signal=dict(sig), broker=dict(broker), last_checked_at=_now_iso())
            if intent_id:
                item["intent_id"] = intent_id
        # Keep unresolved fill records even when trimming completed history.
        active = {str(x.get("order_id")) for x in ledger["pending_broker_orders"]}
        ledger["broker_fills"] = [f for i, f in enumerate(fills) if i < 200 or str(f.get("order_id")) in active]
        save_ledger(ledger)
        sig["live_response"] = copy.deepcopy(broker)
        sig["status"] = "approved" if fill else ("rejected" if terminal else "broker_pending")
        if fill:
            sig["fill"] = fill
            sig["reject_reason"] = None
        elif terminal:
            sig["reject_reason"] = "Broker order ended without a fill"
        signals = load_signals()
        for i, current in enumerate(signals):
            if current.get("id") == sig.get("id"):
                signals[i] = dict(sig)
                break
        save_signals(signals)
    return fill, terminal


_LAST_EXECUTION_CORRECTIONS_AT = 0.0


def _reconcile_execution_corrections() -> None:
    """Retained terminal executions can still receive broker corrections."""
    global _LAST_EXECUTION_CORRECTIONS_AT
    import broker_router as broker
    if broker.public_status().get("broker") != "ibkr":
        return
    with _BROKER_SUBMIT_LOCK:
        if time.monotonic() - _LAST_EXECUTION_CORRECTIONS_AT < 60:
            return
        _LAST_EXECUTION_CORRECTIONS_AT = time.monotonic()
        with _lock:
            identity = load_config().get("broker_identity")
            if not identity or identity.get("broker") != "ibkr":
                return
            signals = {s.get("id"): s for s in load_signals()}
            retained = {}
            for fill in load_ledger().get("broker_fills") or []:
                sig = signals.get(fill.get("signal_id"))
                response = (sig or {}).get("live_response") or {}
                if (not sig or (response.get("order") or {}).get("broker_identity") != identity
                        or str(response.get("order_id")) != str(fill.get("order_id"))):
                    continue
                poll_id = str(response.get("permanent_order_id") or fill["order_id"])
                retained[poll_id] = (sig, response, fill)
                if len(retained) >= 50:
                    break
        if not retained:
            return
        import broker_ibkr
        refreshed = broker_ibkr.refresh_execution_corrections(list(retained), identity)
        if not refreshed.get("ok") or refreshed.get("identity") != identity:
            return
        with _lock:
            if load_config().get("broker_identity") != identity:
                return
            for update in refreshed.get("orders") or []:
                item = retained.get(str(update.get("order_id")))
                versions = update.get("execution_versions") or {}
                prior_versions = (item[2].get("execution_versions") or {}) if item else {}
                if (not item or update.get("identity") != identity or not update.get("execution_correction")
                        or not any(isinstance(v, int) and not isinstance(v, bool) and v > prior_versions.get(k, 0)
                                   for k, v in versions.items())):
                    continue
                _apply_broker_update(dict(item[0]), dict(item[1]), update, "broker_execution_correction")


def _reconcile_pending_broker_orders() -> None:
    import broker_router as broker
    with _BROKER_SUBMIT_LOCK:
        with _lock:
            pending = list(load_ledger().get("pending_broker_orders") or [])
        pending.sort(key=lambda item: str(item.get("last_checked_at") or ""))
        for item in pending[:20]:
            if item.get("broker", {}).get("broker") not in (None, broker.public_status().get("broker")):
                continue  # Never poll an order ID on a different provider.
            try:
                age = time.time() - datetime.fromisoformat(str(item.get("created_at") or "").replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError, OSError):
                age = 0
            if age > 3600:
                desk_alerts.emit("broker_order_stale", f"Broker order {item['order_id']} remains unresolved after {int(age // 60)} minutes.",
                                 detail={"order_id": item["order_id"]}, level="warning", dedupe_key=f"broker_order_stale:{item['order_id']}")
            try:
                response = dict(item["broker"])
                expected = response.get("order", {}).get("broker_identity")
                context = broker.verify_execution_context()
                if not expected or not context.get("ok") or context.get("identity") != expected:
                    continue  # Never recover or resolve another account's order.
                if str(item["order_id"]).startswith("intent:"):
                    found = broker.find_order_by_signal(
                        str((item.get("signal") or {}).get("id") or ""),
                        expected_identity=response.get("order", {}).get("broker_identity"))
                    if not found.get("ok") or not found.get("order_id"):
                        # An absent order is not proof that a timed-out submission failed.
                        result = {"state": "unknown", "terminal": False,
                                  "error": found.get("error") or "Submission outcome remains unverified"}
                    else:
                        response.update(found)
                        result = broker.wait_for_fill(str(response["order_id"]), timeout=0.25, interval=0.1)
                else:
                    poll_id = str(response.get("permanent_order_id") or item["order_id"])
                    result = broker.wait_for_fill(poll_id, timeout=0.25, interval=0.1)
                    if result.get("state") == "unknown" and response.get("broker") == "ibkr":
                        found = broker.find_order_by_signal(str((item.get("signal") or {}).get("id") or ""), expected_identity=expected)
                        if found.get("ok") and found.get("order_id"):
                            # Keep the ledger's existing ID/count while recovering the
                            # permanent ID of an order interrupted before acknowledgement.
                            response["permanent_order_id"] = found["order_id"]
                            result = broker.wait_for_fill(str(found["order_id"]), timeout=0.25, interval=0.1)
            except Exception as exc:
                result = {"state": "unknown", "terminal": False, "error": str(exc)[:200]}
            fill, terminal = _apply_broker_update(dict(item.get("signal") or {}),
                                                response, result, "reconciled_broker")
            if terminal:
                append_journal("broker_order_reconciled", {"order_id": item["order_id"], "fill": fill})
    _reconcile_execution_corrections()


def _broker_day_pnl() -> tuple[float | None, float | None, str | None]:
    """Return verified equity even if only daily P&L is unavailable.

    A positive equity with an error means identity/account checks passed and only
    P&L is missing; a strictly reducing order may still close verified holdings.
    """
    try:
        import broker_router as alpaca

        acct = alpaca.get_account()
    except Exception as exc:  # noqa: BLE001
        return None, None, f"Broker account check failed: {str(exc)[:120]}"
    if not acct.get("ok"):
        return None, None, "Broker account unavailable — cannot check loss limits"
    a = acct.get("account") or {}
    if a.get("trading_blocked") or a.get("account_blocked"):
        return None, None, "Broker account is blocked from trading"
    try:
        equity = _finite_float(a.get("equity"))
        if equity is None or equity <= 0:
            raise ValueError("invalid equity")
    except (TypeError, ValueError):
        return None, None, "Broker account returned no valid equity — cannot check loss limits"
    day_pnl = _finite_float(a.get("day_pnl"))
    if day_pnl is None:
        last = _finite_float(a.get("last_equity"))
        if last is None or last <= 0:
            return None, equity, "Broker daily P&L unavailable — new risk is blocked"
        day_pnl = equity - last
    _note_day_pnl_peak(day_pnl)
    return day_pnl, equity, None


def _broker_session_gate(cfg: dict[str, Any], notional: float, *, reducing: bool = False) -> tuple[bool, str]:
    """Broker session/order checks never inspect the local paper book."""
    if _CORRUPT_PATHS:
        names = ", ".join(sorted(Path(k).name for k in _CORRUPT_PATHS))
        return False, f"Data file corrupt ({names}) — repair before broker execution"
    if not reducing and not (cfg.get("session_active") and load_config().get("session_active")):
        return False, "Broker session not active — start checking before opening new risk"
    if cfg.get("rth_only", True) and not paper_loop_mod.is_rth():
        return False, "The market is closed. Broker orders wait for regular market hours."
    if _finite_float(notional) is None or notional <= 0:
        return False, "Invalid broker order notional"
    ks = cfg.get("kill_switch") or {}
    if not reducing and ks.get("armed") and ks.get("max_position_size_usd") is not None:
        if notional > float(ks["max_position_size_usd"]):
            return False, "Safety stop: this broker order exceeds your max position size"
    return True, "ok"


def _broker_risk_gate(
    cfg: dict[str, Any], ledger: dict[str, Any], day_pnl: float, equity: float
) -> tuple[bool, str]:
    """Trade-count + loss caps measured on the broker account (local paper can't see these)."""
    preset = get_preset(cfg.get("risk_preset"))
    max_trades = int(preset["max_trades_per_day"])
    ks = cfg.get("kill_switch") or {}
    if ks.get("armed") and ks.get("max_trades_per_day") is not None:
        max_trades = int(ks["max_trades_per_day"])
    if _finite_float(day_pnl) is None or _finite_float(equity) is None or equity <= 0:
        return False, "Broker equity or daily P&L unavailable — new risk is blocked"
    total = _broker_trades_today(ledger)
    if total >= max_trades:
        return False, f"You've hit today's limit of {max_trades} broker trades."
    max_loss = equity * (float(preset["max_daily_loss_pct"]) / 100.0)
    if day_pnl <= -max_loss:
        return False, f"Your broker account hit today's loss limit ({preset['max_daily_loss_pct']}%)."
    if ks.get("armed") and ks.get("max_daily_loss_usd") is not None:
        if day_pnl <= -abs(float(ks["max_daily_loss_usd"])):
            return False, "Safety stop: your broker account hit today's max loss."
    max_sess = cfg.get("max_session_loss_usd")
    if max_sess is not None and str(max_sess).strip() != "":
        try:
            if day_pnl <= -abs(float(max_sess)):
                return False, "Your broker account hit today's max loss."
        except (TypeError, ValueError):
            pass
    target = _finite_float(cfg.get("daily_profit_target_usd"))
    if target is not None and target > 0 and day_pnl >= target:
        return False, f"Your broker account reached today's profit target (${target:,.2f}); new risk is paused"
    giveback = _finite_float(cfg.get("giveback_stop_pct"), 0.0) or 0.0
    peak = _finite_float(((ledger.get("broker_daily") or {}).get(_today_str()) or {}).get("peak_pnl"))
    # Only a meaningful peak (a quarter of the daily loss limit) arms the guard.
    meaningful = equity * float(preset["max_daily_loss_pct"]) / 100.0 * 0.25
    if giveback > 0 and peak is not None and peak > 0 and peak >= meaningful and day_pnl <= peak * (1 - giveback / 100.0):
        return False, (f"Protecting today's gains: P&L fell from a ${peak:,.2f} peak to ${day_pnl:,.2f} "
                       f"(more than {giveback:g}% given back); new risk is paused")
    return True, "ok"


def _note_day_pnl_peak(day_pnl: float) -> None:
    """Remember today's best broker P&L for the give-back guard (writes only on a new high)."""
    try:
        with _lock:
            if _CORRUPT_PATHS:
                return
            ledger = load_ledger()
            day = ledger.setdefault("broker_daily", {}).setdefault(_today_str(), {"trades": 0})
            peak = _finite_float(day.get("peak_pnl"))
            if peak is None or day_pnl > peak:
                day["peak_pnl"] = round(float(day_pnl), 2)
                save_ledger(ledger)
    except Exception:  # noqa: BLE001 - bookkeeping never blocks an account read
        pass


def _broker_working_reservations(ticker: str, px: float) -> tuple[dict[str, float], str | None]:
    """Reserve both opening notional and shares already committed to closing."""
    reserved = {"buy_exposure": 0.0, "buy_qty": 0.0, "sell_qty": 0.0}
    try:
        import broker_router as broker
        result = broker.get_open_orders()
        if not result.get("ok"):
            raise ValueError("Open broker orders unavailable; cannot check exposure")
        for order in result["orders"]:
            if str(order.get("symbol") or "").upper() != ticker.upper():
                continue
            # OPT open orders must not reserve stock shares/notional for the underlying.
            asset = str(order.get("asset_type") or order.get("sec_type") or "STK").upper()
            if asset and asset != "STK":
                continue
            side = str(order.get("side") or "").lower()
            if side not in ("buy", "sell"):
                raise ValueError("Invalid broker open-order side")
            qty = _finite_float(order.get("qty"))
            filled = _finite_float(order.get("filled_qty"), 0)
            if qty is None or filled is None or qty < 0 or filled < 0 or filled > qty:
                raise ValueError("Invalid broker open-order quantity")
            remaining = qty - filled
            reserved[side + "_qty"] += remaining
            if side == "buy":
                limit = _finite_float(order.get("limit_price"), px) or px
                reserved["buy_exposure"] += remaining * max(px, limit)
        return reserved, None
    except Exception as exc:
        return reserved, str(exc)[:200]


def _broker_working_exposure(ticker: str, px: float) -> tuple[float, str | None]:
    reserved, error = _broker_working_reservations(ticker, px)
    return reserved["buy_exposure"], error


def _broker_authorization_error(cfg, identity, current) -> str | None:
    if cfg.get("mode") not in ("live_manual", "auto_live") or current.get("mode") != cfg.get("mode"):
        return "Broker execution mode changed; review the order again"
    if identity is not None and current.get("broker_identity") != identity:
        return "Broker account authorization changed; review the account again"
    return None


def _persist_broker_intent(sig, order, provider):
    """Durable unresolved intent exists before any request can reach the broker."""
    from research_metrics import quote_benchmark
    sig.setdefault("execution_benchmarks", {})["arrival"] = quote_benchmark(sig.get("execution_quote") or {}, _now_iso())
    intent_id = str(uuid.uuid4())
    response = {"order_id": "intent:" + intent_id, "intent_id": intent_id,
                "broker": provider, "status": "submission_unverified", "order": dict(order)}
    _apply_broker_update(sig, response, {"state": "unknown", "terminal": False}, "broker_submit_intent")
    return response


def execute_gated_broker_or_paper(sig, cfg, *, source: str, via: str) -> dict[str, Any]:
    """Serialize account gates, submit, and durable order reconciliation."""
    if signal_workspace(sig) == "paper":
        return {"ok": False, "error": "Paper research cannot submit broker orders", "gated": True}
    restriction = signal_execution_block(sig, broker=True)
    if restriction:
        return {"ok": False, "error": restriction, "abstain": True, "gated": True}
    if not _broker_is_configured():
        return {"ok": False, "error": "Broker unavailable; a live-account idea cannot fill the local paper book",
                "abstain": True, "gated": True, "book": None, "broker": None, "paper_fallback": False}
    import broker_router as broker

    def blocked(error):
        return {"ok": False, "error": error, "abstain": True, "book": None, "broker": None, "gated": True}

    manual = sig.get("manual_order")
    agent_order = sig.get("source") == "live_agent" or cfg.get("mode") == "auto_live"
    if agent_order:
        error = live_agent.authorization_error(sig, cfg)
        if error:
            return blocked(error)
    if manual and (cfg.get("mode") != "live_manual" or not sig.get("review_identity")
                   or sig.get("review_order") != manual.get("order")):
        return blocked("Direct tickets require their exact manual order review")
    with _BROKER_SUBMIT_LOCK:
        identity = None
        provider = broker.public_status().get("broker")
        if provider in ("ibkr", "alpaca"):
            context = broker.verify_execution_context()
            if not context.get("ok"):
                return blocked(context.get("error") or "Broker account could not be verified")
            identity = context["identity"]
            if cfg.get("broker_identity") != identity:
                return blocked("Broker account changed or was not confirmed; select the broker mode again")
            if sig.get("review_identity") is not None and sig["review_identity"] != identity:
                return blocked("Broker account differs from the reviewed account; open a fresh review")
        # Unknown prior outcomes must be reconciled before another order is sent.
        with _lock:
            if load_ledger().get("pending_broker_orders"):
                return blocked("A broker order is still unresolved; wait for reconciliation before another order")
        px = fetch_last_price(str(sig.get("ticker") or ""))
        if not px or not _finite_float(px) or px <= 0:
            return blocked("no_real_quote")
        with _MARKS_LOCK:
            execution_quote = dict(_QUOTE_SNAPSHOTS.get(str(sig.get("ticker") or "").upper()) or {})
        if agent_order:
            if provider != "ibkr":
                return blocked("Live agent requires the IBKR Gateway adapter")
            try:
                if sig.get("asset_type") in ("OPT", "BAG"):
                    import auto_live_options
                    policy = ((cfg.get("live_agent") or {}).get("policy") or {})
                    # execution_quote/px describe the UNDERLYING stock. The option
                    # or spread premium was quoted at conversion (signal quote).
                    prem = (_finite_float((sig.get("quote") or {}).get("price"))
                            or _finite_float(sig.get("signal_price")))
                    if not prem or prem <= 0:
                        raise ValueError("Option premium missing from the converted signal")
                    sig["review_order"] = auto_live_options.execution_terms_option(
                        sig, cfg, prem, execution_quote, float(policy.get("max_order_usd") or 0),
                    )
                else:
                    sig["review_order"] = live_agent.execution_terms(sig, cfg, px, execution_quote)
                sig["suggested_shares"] = sig["review_order"]["shares"]
            except ValueError as exc:
                return blocked(str(exc))
        reviewed_order = sig.get("review_order")
        # Risk checks use the worst of the fresh mark and the reviewed entry
        # limit. A limit below market must not understate existing holdings.
        if sig.get("asset_type") in ("OPT", "BAG") or (reviewed_order or {}).get("asset_type") in ("OPT", "BAG"):
            # Option risk is premium-based; the underlying price must not leak in.
            opt_px = (_finite_float((reviewed_order or {}).get("limit"))
                      or _finite_float((sig.get("quote") or {}).get("price"))
                      or _finite_float(sig.get("signal_price")) or float(px))
            risk_px = abs(opt_px)
        else:
            risk_px = max(float(px), float(reviewed_order.get("limit") or px)) if reviewed_order else float(px)
        sig["signal_price"] = risk_px
        sig["execution_quote"] = execution_quote
        day_pnl, equity, acct_error = _broker_day_pnl()
        manual = sig.get("manual_order")
        reviewed_asset = (sig.get("review_order") or {}).get("asset_type")
        is_opt = bool(
            sig.get("asset_type") in ("OPT", "BAG")
            or reviewed_asset in ("OPT", "BAG")
            or (manual and (manual.get("order") or {}).get("asset_type") in ("OPT", "BAG"))
        )
        side = str(sig.get("side") or "").lower()
        if side not in ("buy", "sell"):
            return blocked("Invalid order side")
        if is_opt:
            # Options: never use underlying stock qty or premium-as-stock-price caps.
            held = 0.0  # stock qty unused for OPT sizing
            opt_intent = str(
                (manual or {}).get("intent")
                or (sig.get("review_order") or {}).get("option_intent")
                or sig.get("option_intent")
                or ""
            ).upper()
            reducing = opt_intent in ("STC", "BTC", "CLOSE") or (
                opt_intent == "" and side == "sell"
            )
            if side == "sell":
                # Holding check is enforced again in broker_ibkr by con_id.
                pass
            reserved, open_error = {"buy_qty": 0.0, "sell_qty": 0.0, "buy_exposure": 0.0}, None
            # Filter working OPT reservations by con_id later in the adapter.
            day_pnl, equity, acct_error = day_pnl, equity, acct_error
            if acct_error and not (reducing and day_pnl is None and equity is not None and equity > 0):
                return blocked(acct_error)
            reviewed_order = sig.get("review_order")
            contracts = int((reviewed_order or {}).get("contracts") or (reviewed_order or {}).get("shares") or sig.get("suggested_shares") or 0)
            premium = float((reviewed_order or {}).get("limit") or sig.get("signal_price") or 0)
            from order_terms import option_max_loss, option_notional
            try:
                notional = option_notional(contracts, premium, 100)
                # Opening shorts and credit spreads risk far more than the
                # premium; size caps must see the worst-case loss.
                terms = dict((manual or {}).get("order") or {})
                terms.update(reviewed_order or {})
                for key in ("option_strategy", "right", "strike", "long_strike", "short_strike", "covered", "asset_type"):
                    if terms.get(key) is None and sig.get(key) is not None:
                        terms[key] = sig.get(key)
                terms.update(contracts=contracts, option_intent=opt_intent or terms.get("option_intent"))
                risk_notional = notional if reducing else option_max_loss(terms, premium)
            except ValueError as exc:
                return blocked(str(exc))
            shares = contracts  # quantity field carried as contracts
            with _lock:
                ledger = load_ledger()
                if reviewed_order and shares != int(reviewed_order.get("contracts") or reviewed_order.get("shares") or 0):
                    return blocked("Risk limits changed the reviewed quantity; open a fresh review")
                sig["suggested_shares"] = shares
                ok, reason = _broker_session_gate(cfg, risk_notional, reducing=reducing)
                if ok and not reducing:
                    ok, reason = _broker_risk_gate(cfg, ledger, day_pnl, equity)
                if not ok:
                    return blocked(reason)
            asset_type = (reviewed_order or {}).get("asset_type") or sig.get("asset_type") or "OPT"
            opt_intent = (
                (manual or {}).get("intent")
                or (reviewed_order or {}).get("option_intent")
                or sig.get("option_intent")
            )
            order = {"ticker": sig.get("ticker"), "side": side, "shares": shares, "contracts": shares,
                     "limit": (reviewed_order or {}).get("limit"), "signal_id": sig.get("id"), "via": via,
                     "type": (reviewed_order or {}).get("type") or "market", "broker_identity": identity,
                     "asset_type": asset_type, "option_intent": opt_intent,
                     "right": (reviewed_order or {}).get("right") or sig.get("right"),
                     "expiry": (reviewed_order or {}).get("expiry") or sig.get("expiry"),
                     "strike": (reviewed_order or {}).get("strike") or sig.get("strike"),
                     "long_strike": (reviewed_order or {}).get("long_strike") or sig.get("long_strike"),
                     "short_strike": (reviewed_order or {}).get("short_strike") or sig.get("short_strike"),
                     "option_strategy": (reviewed_order or {}).get("option_strategy") or sig.get("option_strategy"),
                     "legs": (reviewed_order or {}).get("legs"),
                     "allow_naked": (reviewed_order or {}).get("allow_naked") or sig.get("allow_naked_short"),
                     "covered": (reviewed_order or {}).get("covered") or sig.get("covered"),
                     "contract_identity": copy.deepcopy(sig.get("review_contract")),
                     "position_intent": opt_intent,
                     "option_notional": notional, "option_max_loss": risk_notional}
            if reviewed_order:
                order.update({k: v for k, v in reviewed_order.items() if k not in order})
            # Jump to submission by replacing the stock path with OPT order already built.
            # Fall through: we set a marker and skip stock sizing below.
            _opt_order_ready = order
        else:
            _opt_order_ready = None
            held, pos_error = _broker_position_qty(str(sig.get("ticker") or ""))
            if pos_error or _finite_float(held) is None:
                return blocked(pos_error or "Invalid broker position quantity")
            if side == "sell" and held <= 0:
                return blocked("Nothing to sell at the broker; this desk never opens short sales")
            reducing = (side == "sell" and held > 0) or (side == "buy" and held < 0)
        if not is_opt:
          if acct_error and not (reducing and day_pnl is None and equity is not None and equity > 0):
            return blocked(acct_error)
          if agent_order:
            error = _live_agent.trade_error(sig, cfg, day_pnl, reducing)
            if error:
                return blocked(error)
          reserved, open_error = _broker_working_reservations(str(sig.get("ticker") or ""), px)
          if open_error:
            return blocked(open_error)
          if manual:
            from live_ticket import position_error
            intent_error = position_error(manual["intent"], held, reviewed_order["shares"], reserved)
            if intent_error:
                return blocked(intent_error)
        if not is_opt:
          with _lock:
            ledger = load_ledger()
            if reducing:
                want = int(float(sig.get("suggested_shares") or 0)) or int(abs(held))
                available = max(0.0, abs(held) - reserved[side + "_qty"])
                shares = min(want, int(available))
                if shares <= 0:
                    return blocked("No whole shares available to close after working orders")
                notional, cap_error = shares * px, None
            else:
                shares, notional, cap_error = _cap_shares_for_broker(
                    sig, cfg, ledger, equity_override=equity, allow_fetch=False,
                    existing_notional=max(0, held) * px + reserved["buy_exposure"])
            if cap_error:
                return blocked(cap_error)
            if agent_order:
                reviewed_order["shares"] = shares
                if reviewed_order.get("type") == "limit":
                    reviewed_order["notional_bound"] = shares * reviewed_order["limit"]
            if reviewed_order and shares != reviewed_order["shares"]:
                return blocked("Risk limits changed the reviewed quantity; open a fresh review")
            sig["suggested_shares"] = shares
            ok, reason = _broker_session_gate(cfg, notional, reducing=reducing)
            if ok and not reducing:
                ok, reason = _broker_risk_gate(cfg, ledger, day_pnl, equity)
            if not ok:
                return blocked(reason)
        if _opt_order_ready is not None:
            order = _opt_order_ready
        else:
            order = {"ticker": sig.get("ticker"), "side": side, "shares": shares, "limit": px,
                     "signal_id": sig.get("id"), "via": via, "type": "market", "broker_identity": identity}
            if reviewed_order:
                order.update(reviewed_order)
            if manual:
                order["position_intent"] = manual["intent"]
                order["contract_identity"] = copy.deepcopy(sig.get("review_contract"))
            elif agent_order:
                order["position_intent"] = "sell" if side == "sell" else "cover" if held < 0 else "buy"
                order["agent_policy"] = {"revision": sig["agent_revision"], "run_id": sig["agent_run_id"]}
        with _BROKER_EXEC_LOCK:
            with _lock:
                current = load_config()
                authorization_error = _broker_authorization_error(cfg, identity, current)
                if authorization_error:
                    return blocked(authorization_error)
                if agent_order:
                    error = _live_agent.trade_error(sig, current, day_pnl, reducing)
                    if error:
                        return blocked(error)
                if not reducing and not is_opt:
                    shares, notional, cap_error = _cap_shares_for_broker(
                        sig, current, ledger, equity_override=equity, allow_fetch=False,
                        existing_notional=max(0, held) * px + reserved["buy_exposure"])
                    if cap_error:
                        return blocked(cap_error)
                    if reviewed_order and shares != reviewed_order["shares"]:
                        return blocked("Risk limits changed the reviewed quantity; open a fresh review")
                    sig["suggested_shares"] = shares
                    order["shares"] = shares
                # Re-run broker-only gates against the latest limits/session immediately
                # before persisting intent; config POST uses this same outer lock.
                # Options are capped on worst-case loss, not premium (same as the first gate).
                gate_notional = risk_notional if is_opt else notional
                ok, reason = _broker_session_gate(current, gate_notional, reducing=reducing)
                if ok and not reducing:
                    ok, reason = _broker_risk_gate(current, ledger, day_pnl, equity)
                if not ok:
                    return blocked(reason)
                try:
                    order["valid_until"] = _execution_deadline(sig)
                except (ValueError, TypeError, KeyError) as exc:
                    return blocked(str(exc))
                order["risk_authorization"] = {"day_pnl": day_pnl, "equity": equity, "reducing": reducing}
                if agent_order:
                    _live_agent.reserve(sig, current)
                intent = _persist_broker_intent(sig, order, provider)
            try:
                response = live_broker_place_order(order)
            except Exception as exc:
                response = {"ok": False, "error": str(exc)[:200], "submission_attempted": True}
        response = dict(response or {}, intent_id=intent["intent_id"])
        response.setdefault("broker", provider)
        response["order"] = dict(order)  # Keep the verified account for restart recovery.
        sig["live_response"] = response
        if not response.get("order_id"):
            response["order_id"] = intent["order_id"]
            definitive = response.get("submission_attempted") is False or response.get("definitive_rejection") is True
            _apply_broker_update(sig, response, {"state": "failed" if definitive else "unknown", "terminal": definitive}, source)
            return {"ok": False, "error": f"Broker order not filled: {response.get('error') or response.get('status')}",
                    "broker": response, "book": None, "paper_fallback": False, "pending": not definitive}
        # A known order identity remains tracked even if its submit response is an error.
        _apply_broker_update(sig, response, {"state": "unknown", "terminal": False}, source)
        try:
            confirm = broker.wait_for_fill(str(response["order_id"]), timeout=BROKER_FILL_WAIT_SEC)
            _apply_broker_update(sig, response, confirm, f"{source}_broker")
            if not _broker_order_terminal(confirm) and order["type"] == "market":
                confirm = broker.reconcile_after_timeout(str(response["order_id"]), timeout=2.0)
        except Exception as exc:
            confirm = {"state": "unknown", "terminal": False, "error": str(exc)[:200]}
        response["fill_confirm"] = confirm
        fill, terminal = _apply_broker_update(sig, response, confirm, f"{source}_broker")
        append_journal("broker_fill" if fill else "broker_order_pending" if not terminal else "broker_order_failed",
                       {"signal_id": sig.get("id"), "broker": response, "fill": fill})
        if not fill:
            return {"ok": False, "error": "Broker order has no reconciled fill; tracking continues" if not terminal else "Broker order ended without a reconciled fill",
                    "broker": response, "book": None, "paper_fallback": False, "pending": not terminal}
        return {"ok": True, "fill": fill, "broker": response, "book": "broker_only",
                "paper_fallback": False, "pending": not terminal}


def broker_flatten_if_configured() -> dict[str, Any]:
    """Cancel/close Alpaca positions when configured; honest refuse when not."""
    try:
        import broker_router as alpaca
    except ImportError:
        return {
            "ok": False,
            "attempted": False,
            "status": "live_not_wired",
            "message": "broker_alpaca missing — local flatten only",
        }
    if not alpaca.is_configured():
        return {
            "ok": True,
            "attempted": False,
            "status": "not_configured",
            "message": "No Alpaca keys — local paper flatten only",
            "paper_mode": alpaca.paper_mode(),
        }
    try:
        result = alpaca.flatten_broker()
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "attempted": True,
            "status": "error",
            "error": str(exc)[:300],
            "paper_mode": alpaca.paper_mode(),
        }
    append_journal("broker_flatten", result)
    return result


def _reject_broker_api_attempt(endpoint: str) -> tuple[dict, int]:
    """Public /api/broker/* stays closed; desk uses live_broker_place_order internally."""
    append_journal(
        "broker_api_rejected",
        {"endpoint": endpoint, "note": "Use desk approve/auto paths; raw broker HTTP not exposed."},
    )
    return {
        "ok": False,
        "error": "Raw broker HTTP routes are not exposed. Use desk approve / auto_live.",
        "banner": BANNER,
        "broker": _broker_public_status(),
    }, 403


# ---------------------------------------------------------------------------
# Pricing (optional yfinance)
# ---------------------------------------------------------------------------

_MARKS: dict[str, tuple[float, float]] = {}  # ticker -> (price, monotonic time)
_MARKS_LOCK = threading.Lock()
MARK_MAX_AGE_SEC = 600.0


def recent_marks(max_age: float = MARK_MAX_AGE_SEC) -> dict[str, float]:
    """Last prices seen in the past `max_age` seconds (used to value open positions)."""
    import time as _t

    now = _t.monotonic()
    with _MARKS_LOCK:
        return {t: px for t, (px, at) in _MARKS.items() if now - at <= max_age}


def fetch_last_price(ticker: str) -> float | None:
    """Best-effort last price; also remembered for mark-to-market risk gates."""
    import math as _m
    import time as _t

    px = _fetch_last_price_raw(ticker)
    if px is not None:
        try:
            v = float(px)
        except (TypeError, ValueError):
            return None
        if not _m.isfinite(v) or v <= 0:
            return None
        with _MARKS_LOCK:
            symbol = (ticker or "").strip().upper()
            quote = _QUOTE_SNAPSHOTS.get(symbol) or {}
            age = max(0, float(quote.get("age_sec") or 0))
            _MARKS[symbol] = (v, _t.monotonic() - age)
        return v
    return None


_QUOTE_SNAPSHOTS: dict[str, dict] = {}


REALTIME_PREFER_MAX_AGE_SEC = 20.0


def _realtime_quote_or_none(ds, ticker: str) -> dict | None:
    """A fresh, attributable non-IBKR quote young enough for every live gate.

    The 20s ceiling sits under the live agent's 30s quote limit, so preferring
    this quote can never turn an executable IBKR-delayed quote into a stale hold.
    """
    try:
        rt = ds.latest_quote(ticker)
    except Exception:
        return None
    if not isinstance(rt, dict) or not rt.get("fresh") or rt.get("error"):
        return None
    source = str(rt.get("source") or "").lower()
    if not source or "ibkr" in source or any(w in source for w in ("mock", "demo", "synthetic", "fixture", "unknown")):
        return None
    price, age = _finite_float(rt.get("price")), _finite_float(rt.get("age_sec"))
    if price is None or price <= 0 or age is None or not 0 <= age <= REALTIME_PREFER_MAX_AGE_SEC:
        return None
    return rt


def _fetch_last_price_raw(ticker: str) -> float | None:
    """Only timestamp-verified quotes can update marks or execution prices.

    Prefer IBKR live/delayed mkt data when the live broker is connected; fall
    back to Finnhub/Yahoo. Never invents a fresh stamp.
    """
    import data_sources as ds
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None
    quote = None
    try:
        import broker_router
        import os
        if (os.environ.get("BROKER_PROVIDER", "") or "").strip().lower() == "ibkr":
            ibq = broker_router.stock_quote(ticker)
            if ibq.get("ok") and ibq.get("fresh") and ibq.get("price"):
                quote = {
                    "price": ibq["price"], "source": ibq.get("source") or "IBKR mkt data",
                    "market_time": ibq.get("market_time"), "received_at": ibq.get("received_at"),
                    "age_sec": ibq.get("age_sec"), "fresh": True,
                    "bid": ibq.get("bid"), "ask": ibq.get("ask"), "error": None,
                    "max_age_sec": 120, "delayed": bool(ibq.get("delayed")),
                    "market_data_type": ibq.get("market_data_type"),
                }
                if quote["delayed"]:
                    # IBKR delayed prices are ~15 min old despite a receipt-time
                    # stamp. Use a verified real-time quote when one is available;
                    # otherwise keep the IBKR quote so execution is never blocked.
                    realtime = _realtime_quote_or_none(ds, ticker)
                    if realtime:
                        quote = realtime
    except Exception:
        quote = None
    if not quote:
        quote = ds.latest_quote(ticker)
    with _MARKS_LOCK:
        _QUOTE_SNAPSHOTS[ticker] = quote
    return quote.get("price") if quote.get("fresh") else None


def demo_price(ticker: str) -> float:
    """Deterministic-ish demo price from ticker hash + noise."""
    base = 50 + (sum(ord(c) for c in ticker.upper()) % 400)
    noise = random.uniform(-2.0, 2.0)
    return round(base + noise, 2)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def _confidence_for_verdict(verdict: str, lateness_label: str | None) -> float:
    """Map screener verdict + entry lateness → confidence."""
    label = (lateness_label or "fair").lower()
    v = (verdict or "").upper()
    ranges = {
        ("PASS", "early"): (0.82, 0.92),
        ("PASS", "fair"): (0.82, 0.92),
        ("PASS", "late"): (0.65, 0.75),
        ("PASS", "chasing"): (0.45, 0.55),
        ("WATCH", "early"): (0.55, 0.68),
        ("WATCH", "fair"): (0.55, 0.68),
        ("WATCH", "late"): (0.40, 0.55),
        ("WATCH", "chasing"): (0.35, 0.45),
        ("AVOID", "early"): (0.20, 0.30),
        ("AVOID", "fair"): (0.20, 0.30),
        ("AVOID", "late"): (0.15, 0.25),
        ("AVOID", "chasing"): (0.10, 0.20),
    }
    lo, hi = ranges.get((v, label), (0.40, 0.55))
    # Deterministic: confidence gates real orders (live agent min_confidence), so the
    # same setup must always score the same. It was random within the band before.
    return round((lo + hi) / 2, 2)


def _stop_target_distances(price: float, intraday: dict | None, cfg: dict[str, Any],
                           preset: dict[str, Any]) -> tuple[float, float]:
    """Stop and target distances per share: ATR-based when known, else 0.8% of price."""
    atr_usd = _finite_float((intraday or {}).get("atr_usd"))
    atr_multiple = _finite_float(cfg.get("atr_stop_multiple"), 1.0) or 1.0
    base = atr_usd * atr_multiple if atr_usd and atr_usd > 0 else price * 0.008
    stop_dist = round(base * float(preset["stop_r"]), 2)
    return stop_dist, round(stop_dist * float(preset["target_r"]), 2)


def _edge_after_costs(price: float, stop_dist: float, target_dist: float, cfg: dict[str, Any],
                      intraday: dict | None = None, quote: dict | None = None) -> dict[str, Any]:
    """Round-trip friction per share and the reward:risk that survives it.

    Costs: entry+exit fees and slippage (fee_bps, slip_bps each way) plus one full
    bid/ask spread (quote when present, else the screener's spread/ATR estimate).
    """
    bps = 2 * ((_finite_float(cfg.get("fee_bps"), 0.0) or 0.0) + (_finite_float(cfg.get("slip_bps"), 0.0) or 0.0))
    q = quote if isinstance(quote, dict) else {}
    bid, ask = _finite_float(q.get("bid")), _finite_float(q.get("ask"))
    spread = None
    if bid and ask and ask >= bid > 0:
        spread = ask - bid
    else:
        spread_atr = _finite_float((intraday or {}).get("spread_atr"))
        atr_usd = _finite_float((intraday or {}).get("atr_usd"))
        if spread_atr is not None and atr_usd:
            spread = max(0.0, spread_atr * atr_usd)
    cost = price * bps / 1e4 + (spread or 0.0)
    net_risk = stop_dist + cost
    if stop_dist <= 0 or net_risk <= 0:
        return {"round_trip_cost_usd": round(cost, 4), "net_reward_risk": None, "breakeven_win_rate": None}
    net_rr = (target_dist - cost) / net_risk
    return {
        "round_trip_cost_usd": round(cost, 4),
        "net_reward_risk": round(net_rr, 3),
        # Win rate at which a stop-or-target trade breaks even after costs.
        "breakeven_win_rate": round(1 / (1 + net_rr), 3) if net_rr > 0 else 1.0,
    }


def _evidence_block(sig: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Refuse execution when this setup's own scored after-cost record is losing."""
    if not cfg.get("evidence_gate_enabled", True):
        return None
    side = str(sig.get("side") or "").lower()
    if side not in ("buy", "sell"):
        return None
    try:
        rec = lessons.track_record(LESSONS_PATH, ticker=str(sig.get("ticker") or ""), verdict=sig.get("verdict"),
                                   lateness=sig.get("lateness_label"), scope=_research_scope(cfg))
    except Exception:  # noqa: BLE001 - missing history never blocks
        return None
    stats = (rec.get("side_stats") or {}).get(side) or {}
    samples = int(stats.get("samples") or 0)
    rate, move = stats.get("helped_rate"), stats.get("avg_move_bps")
    need = int(_finite_float(cfg.get("evidence_min_samples"), 12.0) or 12)
    breakeven = _finite_float(sig.get("breakeven_win_rate")) or 0.5
    if samples >= need and rate is not None and move is not None and rate < breakeven and move <= 0:
        return (f"setup_losing_record: this {rec.get('setup')} {side} setup won {rate:.0%} of {samples} "
                f"scored trades after costs (breakeven {breakeven:.0%}), average {move:+.1f} bps")
    return None


def _apply_wsb_caution(sig: dict[str, Any], cfg: dict[str, Any]) -> None:
    """A buy idea on a ticker crowded on WSB gets the configured caution. Only ever reduces risk."""
    action = cfg.get("wsb_crowding_action", "half")
    if action == "off" or str(sig.get("side") or "").lower() != "buy":
        return
    try:
        crowd = wsb_monitor.crowding(str(sig.get("ticker") or ""), cfg)
    except Exception:  # noqa: BLE001 - WSB problems never block or size a trade
        return
    if not crowd.get("crowded"):
        return
    sig["wsb_crowding"] = crowd
    sig["research_flags"] = list(sig.get("research_flags") or []) + ["wsb_crowded"]
    note = crowd.get("reason") or "Crowded on WSB"
    if action == "skip":
        sig["execution_block"] = sig.get("execution_block") or f"wsb_crowded: {note}; skipped by your WSB setting"
    elif action == "half":
        shares = _finite_float(sig.get("suggested_shares"), 0.0) or 0.0
        sig["suggested_shares"] = int(shares // 2) if shares >= 1 else shares / 2
        sig["advisory_size_mult"] = min(_finite_float(sig.get("advisory_size_mult"), 1.0) or 1.0, 0.5)
        if sig["suggested_shares"] < 1 and shares >= 1:
            sig["execution_block"] = sig.get("execution_block") or f"wsb_crowded: {note}; half size is under one share"
    sig["reason"] = (str(sig.get("reason") or "") + f" · {note} ({action}).").strip(" ·")


def _suggested_size_cut(lateness_label: str | None) -> float:
    """Suggested size multiplier for late/chasing (FYI + allow_late sizing)."""
    label = (lateness_label or "").lower()
    if label == "late":
        return 0.50
    if label == "chasing":
        return 0.25
    return 1.0


def _analysis_to_signal(
    analysis: dict,
    cfg: dict,
    preset: dict,
    *,
    force: bool = False,
) -> dict | None:
    """Build a buy-side research signal from analyze_ticker output.

    Research desk: AVOID / sector_dying still queued as buy research
    candidates (low confidence) so false positives can be studied.
    `force` is unused for skip gates (kept for call-site compat).
    """
    from datetime import timedelta

    _ = force  # research path does not skip on force

    verdict = (analysis.get("verdict") or "").upper()
    checks = analysis.get("checks") or {}
    entry = analysis.get("entry_quality") or {}
    lateness_label = entry.get("label") if isinstance(entry, dict) else None
    vol = analysis.get("volume") or {}

    research_flags: list[str] = list(analysis.get("research_flags") or [])

    # Tag sector dying as AVOID research candidate (still queue buy-side).
    if checks.get("sector_dying") and verdict != "AVOID":
        verdict = "AVOID"
        analysis = dict(analysis)
        analysis["verdict"] = "AVOID"
        analysis["verdict_text"] = (
            analysis.get("verdict_text") or ""
        ) + " Sector dying — research long candidate (false-positive study)."
        research_flags.append("sector_dying")

    if verdict == "AVOID":
        research_flags.append("verdict_avoid")
    if (lateness_label or "").lower() in ("late", "chasing"):
        research_flags.append(f"lateness_{(lateness_label or '').lower()}")

    # Prefer PASS/WATCH/AVOID; unknown verdicts still emit as WATCH research.
    if verdict not in ("PASS", "WATCH", "AVOID"):
        verdict = "WATCH"
        research_flags.append("verdict_normalized")

    price = _finite_float(analysis.get("price"))
    if not price or price <= 0:
        return None
    conf = _confidence_for_verdict(verdict, lateness_label)
    # AVOID / sector_dying: keep confidence low for study, never filter out.
    if verdict == "AVOID":
        conf = min(conf, 0.25)
    preset_name = cfg.get("risk_preset", "mid")
    # Research: full size (mult=1.0); suggested cut is FYI metadata only.
    suggested_mult = _suggested_size_cut(lateness_label)
    mult = 1.0

    equity = float(load_ledger().get("equity", cfg.get("paper_equity", 100_000)))
    size_pct = preset["max_position_pct"] * mult
    notional = equity * (size_pct / 100.0)
    shares = max(1, int(notional / max(price, 0.01)))

    intraday = analysis.get("intraday") or {}
    atr_usd = _finite_float(intraday.get("atr_usd"))
    stop_dist, target_dist = _stop_target_distances(price, intraday, cfg, preset)
    edge = _edge_after_costs(price, stop_dist, target_dist, cfg, intraday, analysis.get("quote"))
    stop = round(price - stop_dist, 2)
    target = round(price + target_dist, 2)

    reason_parts = [analysis.get("verdict_text") or f"{verdict} setup"]
    if isinstance(entry, dict) and entry.get("suggestion"):
        reason_parts.append(entry["suggestion"])
    if suggested_mult < 1.0:
        cut_pct = int(round((1.0 - suggested_mult) * 100))
        reason_parts.append(
            f"FYI only: playbook would cut size {cut_pct}% for {lateness_label} "
            f"(preset={preset_name}); research uses full size."
        )
    if verdict == "AVOID":
        reason_parts.append(
            "Research candidate (verdict=AVOID) — queued to study false positives."
        )
    reason = " ".join(reason_parts)

    earnings = analysis.get("earnings")
    ttl = int(cfg.get("signal_ttl_sec", 900))
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    return {
        "id": str(uuid.uuid4()),
        "ts": _now_iso(),
        "ticker": analysis["ticker"].upper(),
        "side": "buy",
        "confidence": conf,
        "reason": reason,
        "signal_price": price,
        "analysis_price": analysis.get("price"),
        "quote": analysis.get("quote"),
        "suggested_shares": shares,
        "suggested_notional": round(shares * price, 2),
        "size_mult": mult,
        "size_mult_suggested": suggested_mult,
        "stop": stop,
        "target": target,
        "stop_r": preset["stop_r"],
        "target_r": preset["target_r"],
        **edge,
        "atr_usd": atr_usd,
        "spread_atr": _finite_float(intraday.get("spread_atr")),
        "vwap_dist_atr": _finite_float(intraday.get("vwap_dist_atr")),
        "vwap_slope_pct": _finite_float(intraday.get("vwap_slope_pct")),
        "preset": preset_name,
        "status": "pending",
        "expires_at": exp.isoformat(),
        "fill": None,
        "mode_at_create": cfg.get("mode", "manual"),
        "verdict": verdict,
        "verdict_text": analysis.get("verdict_text"),
        "entry_quality": entry if isinstance(entry, dict) else None,
        "lateness_label": lateness_label,
        "earnings": earnings,
        "rel_vol": (vol or {}).get("rel_vol"),
        "checks": checks,
        "sources": analysis.get("sources") or [],
        "session_is_today": (vol or {}).get("session_is_today"),
        "research_flags": research_flags,
        "reject_reason": None,
    }


def _demo_fallback_signal(cfg: dict, preset: dict, watchlist: list) -> dict:
    """Last-resort demo signal when every scan fails and force=True."""
    from datetime import timedelta

    ticker = random.choice(watchlist) if watchlist else "SPY"
    price = fetch_last_price(ticker) or demo_price(ticker)
    conf = round(random.uniform(0.40, 0.70), 2)
    equity = float(load_ledger().get("equity", cfg.get("paper_equity", 100_000)))
    notional = equity * (preset["max_position_pct"] / 100.0)
    shares = max(1, int(notional / max(price, 0.01)))
    stop_dist = round(price * 0.008 * preset["stop_r"], 2)
    target_dist = round(stop_dist * preset["target_r"], 2)
    ttl = int(cfg.get("signal_ttl_sec", 900))
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    return {
        "id": str(uuid.uuid4()),
        "ts": _now_iso(),
        "ticker": ticker.upper(),
        "side": "buy",
        "confidence": conf,
        "reason": "[fallback demo] Scan failed for all watchlist symbols — synthetic placeholder.",
        "signal_price": price,
        "analysis_price": price,
        "suggested_shares": shares,
        "suggested_notional": round(shares * price, 2),
        "stop": round(price - stop_dist, 2),
        "target": round(price + target_dist, 2),
        "stop_r": preset["stop_r"],
        "target_r": preset["target_r"],
        "preset": cfg.get("risk_preset", "mid"),
        "status": "pending",
        "expires_at": exp.isoformat(),
        "fill": None,
        "mode_at_create": cfg.get("mode", "manual"),
        "verdict": "WATCH",
        "verdict_text": "[fallback demo] No live screener data",
        "entry_quality": None,
        "lateness_label": "fair",
        "earnings": None,
        "rel_vol": None,
        "checks": {},
        "sources": ["demo"],
        "session_is_today": None,
    }



def _llm_public_status(cfg: dict | None = None) -> dict[str, Any]:
    """Safe LLM status for health/state (never includes API key)."""
    cfg = cfg or load_config()
    try:
        st = llm_trader.status_public_extended(cfg)
    except Exception:
        st = llm_trader.status_public()
    model_override = cfg.get("llm_model")
    brain = str(cfg.get("brain_mode") or st.get("brain_mode") or "gemini").lower()
    if model_override and brain == "gemini":
        st = dict(st)
        st["model"] = model_override
    out = {
        "configured": bool(st.get("configured")),
        "model": st.get("model"),
        "provider": st.get("provider", "gemini"),
        "enabled": bool(cfg.get("llm_enabled", True)),
        "on_scan": bool(cfg.get("llm_on_scan", True)),
        "brain_mode": brain,
        "jev_key_present": bool(st.get("jev_key_present")),
        "shadow_auditor": bool(st.get("shadow_auditor", True)),
        "shadow_gate": bool(st.get("shadow_gate", False)),
        "advisory_panel": bool(st.get("advisory_panel", True)),
        "brain_router": bool(st.get("brain_router", True)),
        "min_decision_confidence": st.get("min_decision_confidence", 0.55),
        "horizon_min": st.get("horizon_min") or cfg.get("decision_horizon_min") or 20,
        "model_cost": st.get("model_cost") or llm_trader.model_cost_today(),
    }
    return out


def _llm_cfg_for_calls(cfg: dict | None = None) -> dict[str, Any]:
    """Build llm_trader config, applying optional model override from desk config."""
    base = llm_trader.load_llm_config()
    cfg = cfg or load_config()
    override = cfg.get("llm_model")
    if override:
        base = dict(base)
        base["model"] = str(override).strip() or base.get("model")
    return base


def _enrich_signal_with_llm(sig: dict[str, Any], analysis: dict, cfg: dict) -> dict[str, Any]:
    """Attach Gemini thesis fields; blend side/confidence/reason with playbook."""
    if isinstance(analysis.get("quote"), dict) and not analysis["quote"].get("fresh"):
        sig.update(side="hold", confidence=0, abstain=True, llm_side="hold",
                   data_error="stale_or_unverified_market_data", llm_error=None,
                   llm_thesis="Waiting for a fresh market quote; the brain has not been called.")
        sig["citations"] = _screener_citations(analysis)
        return sig
    if not cfg.get("llm_enabled", True) or not cfg.get("llm_on_scan", True):
        sig.setdefault("llm_thesis", None)
        sig.setdefault("llm_side", None)
        sig.setdefault("llm_confidence", None)
        sig.setdefault("llm_model", None)
        sig.setdefault("llm_raw", None)
        return sig

    thesis = _research_thesis(analysis, cfg, source="scanner")

    playbook_conf = float(sig.get("confidence") or 0)
    llm_side = str(thesis.get("side") or "flat").lower()
    llm_conf = float(thesis.get("confidence") or 0)
    llm_thesis_text = thesis.get("thesis") or ""
    err = thesis.get("error")

    sig["llm_thesis"] = llm_thesis_text or None
    sig["citations"] = _screener_citations(analysis)
    sig["llm_side"] = llm_side
    sig["llm_confidence"] = llm_conf
    sig["llm_model"] = thesis.get("llm_model")
    raw = thesis.get("llm_raw") or ""
    sig["llm_raw"] = (raw[:800] if raw else None)
    sig["llm_entry_plan"] = thesis.get("entry_plan") or None
    sig["llm_stop_idea"] = thesis.get("stop_idea") or None
    sig["llm_target_idea"] = thesis.get("target_idea") or None
    sig["llm_risks"] = thesis.get("risks") or []
    sig["llm_playbook_agree"] = thesis.get("playbook_agree")
    sig["llm_error"] = err
    sig["brain_mode"] = thesis.get("brain_mode") or llm_trader.resolve_brain_mode(cfg)
    sig["routed"] = thesis.get("routed")
    sig["router_reason"] = thesis.get("router_reason")
    for key in ("decision_record_id", "shadow", "advisory", "execution_block", "advisory_size_mult"):
        sig[key] = thesis.get(key)
    multiplier = thesis.get("advisory_size_mult", 1.0)
    if multiplier is not None and multiplier < 1:
        sig["suggested_shares"] = max(0, int((sig.get("suggested_shares") or 0) * multiplier))
        sig["suggested_notional"] = round(sig["suggested_shares"] * float(sig.get("signal_price") or 0), 2)
    sig["abstain"] = bool(thesis.get("abstain") or llm_side in ("flat", "hold") or err)

    if err:
        sig["side"] = "hold"
        sig["confidence"] = 0
        # Screener-only mode continues; annotate reason
        if err == "missing_gemini_api_key":
            sig["reason"] = (sig.get("reason") or "") + " [LLM: missing API key — screener-only]"
        else:
            sig["reason"] = (sig.get("reason") or "") + f" [LLM error: {err}]"
        append_journal(
            "llm_thesis_error",
            {"ticker": sig.get("ticker"), "error": err, "notes": (thesis.get("notes") or "")[:200]},
        )
        return sig

    # A flat thesis is confidence in abstaining, never confidence in a buy.
    if sig["abstain"]:
        sig["side"] = "hold"
        sig["confidence"] = llm_conf
    # Directional theses may replace the playbook direction.
    if not sig["abstain"] and llm_side in ("buy", "sell"):
        sig["side"] = llm_side
        # Recalculate stop/target direction if sell
        price = float(sig.get("signal_price") or 0)
        stop_r = float(sig.get("stop_r") or 1)
        target_r = float(sig.get("target_r") or 2)
        # Keep the screener's (ATR-based) stop distance; sizing already used it.
        prior_stop = _finite_float(sig.get("stop"))
        stop_dist = round(abs(price - prior_stop), 2) if prior_stop is not None and price else 0.0
        if stop_dist <= 0:
            stop_dist = round(price * 0.008 * stop_r, 2)
        target_dist = round(stop_dist * target_r, 2)
        if llm_side == "sell":
            sig["stop"] = round(price + stop_dist, 2)
            sig["target"] = round(price - target_dist, 2)
        else:
            sig["stop"] = round(price - stop_dist, 2)
            sig["target"] = round(price + target_dist, 2)

    # Blend confidence: prefer LLM when present
    if llm_conf > 0 and not sig["abstain"]:
        blended = round(0.5 * playbook_conf + 0.5 * llm_conf, 3)
        sig["confidence"] = blended
        sig["confidence_playbook"] = playbook_conf
        sig["confidence_llm"] = llm_conf

    # Prepend thesis to reason
    if llm_thesis_text:
        agree = thesis.get("playbook_agree")
        agree_bit = "agrees" if agree else "disagrees"
        prefix = f"[LLM {llm_side} {int(round(llm_conf * 100))}% · {agree_bit} playbook] {llm_thesis_text}"
        sig["reason"] = prefix + " | " + (sig.get("reason") or "")

    append_journal(
        "llm_thesis",
        {
            "ticker": sig.get("ticker"),
            "side": llm_side,
            "confidence": llm_conf,
            "playbook_agree": thesis.get("playbook_agree"),
            "model": thesis.get("llm_model"),
        },
    )
    return sig


def generate_scan_signal(
    cfg: dict | None = None,
    *,
    force: bool = False,
    ticker: str | None = None,
    still_authorized: Callable[[], bool] | None = None,
    pass_only: bool = False,
    notes: dict | None = None,
) -> dict | None:
    """Scan watchlist with volume-screener rules; emit first eligible buy signal.

    ``pass_only`` (the live agent): return only PASS setups, so no model call is
    spent on a WATCH/AVOID idea the caller can never trade. ``notes`` receives the
    best non-PASS verdict and its text for the caller's status line.
    """
    from screener_logic import analyze_ticker

    cfg = cfg or load_config()
    preset = get_preset(cfg.get("risk_preset"))
    if ticker:
        watchlist = [ticker.strip().upper()]
    else:
        watchlist = paper_loop_mod.prune_watchlist_for_trading(
            list(cfg.get("watchlist") or DEFAULT_WATCHLIST),
            focus=str(cfg.get("watchlist_focus") or "liquid"),
        )
    if not watchlist:
        return None

    signals = load_signals()
    pending_tickers = {
        s.get("ticker", "").upper()
        for s in signals
        if _pending_scan_blocks(s, cfg)
    }

    # Rotate start so background scans don't always hit the same names first
    if not force and len(watchlist) > 1:
        offset = int(datetime.now().timestamp() // max(1, int(cfg.get("scan_interval_sec") or cfg.get("demo_signal_interval_sec") or 120))) % len(watchlist)
        watchlist = watchlist[offset:] + watchlist[:offset]

    errors: list[str] = []
    best_watch: dict | None = None
    best_rank = -1  # prefer PASS over WATCH; within same, prefer early

    rank_lateness = {"early": 3, "fair": 2, "late": 1, "chasing": 0}

    for sym in watchlist:
        if still_authorized is not None and not still_authorized():
            return None
        sym = sym.strip().upper()
        if not sym:
            continue
        if sym in pending_tickers and not (force and ticker):
            continue
        try:
            analysis = analyze_ticker(sym)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sym}: {exc}")
            continue
        if analysis.get("error"):
            errors.append(f"{sym}: {analysis['error']}")
            continue

        if still_authorized is not None and not still_authorized():
            return None
        verdict = (analysis.get("verdict") or "").upper()
        checks = analysis.get("checks") or {}
        if checks.get("sector_dying"):
            # Research: still queue as AVOID buy candidate (do not skip)
            verdict = "AVOID"
            analysis = dict(analysis)
            analysis["verdict"] = "AVOID"
            analysis["checks"] = dict(checks)

        entry = analysis.get("entry_quality") or {}
        lateness = (entry.get("label") if isinstance(entry, dict) else None) or "fair"
        intraday = analysis.get("intraday") or {}
        spread_atr = _finite_float(intraday.get("spread_atr"))
        execution_blocked = (
            (spread_atr is not None and spread_atr > 0.08)
            or lateness.lower() in ("late", "chasing")
        )
        if execution_blocked and verdict == "PASS":
            analysis = dict(analysis)
            analysis["verdict"] = "WATCH"
            analysis["verdict_text"] = (
                analysis.get("verdict_text") or "Setup found."
            ) + " Entry blocked: execution quality is poor (wide spread or late/chasing price)."
            analysis["research_flags"] = list(analysis.get("research_flags") or []) + [
                "execution_quality_block"
            ]
            verdict = "WATCH"

        regime = market_regime_status(cfg)
        if (
            bool(cfg.get("market_regime_gate_enabled"))
            and regime.get("regime") == "weak"
            and verdict == "PASS"
        ):
            analysis = dict(analysis)
            analysis["verdict"] = "WATCH"
            analysis["verdict_text"] = (
                analysis.get("verdict_text") or "Setup found."
            ) + " Entry blocked: broad-market regime is weak."
            analysis["research_flags"] = list(analysis.get("research_flags") or []) + [
                "weak_market_regime"
            ]
            verdict = "WATCH"

        min_rr = _finite_float(cfg.get("min_net_reward_risk"), 0.0) or 0.0
        price_now = _finite_float(analysis.get("price"))
        if verdict == "PASS" and min_rr > 0 and price_now and price_now > 0:
            sd, td = _stop_target_distances(price_now, intraday, cfg, preset)
            edge = _edge_after_costs(price_now, sd, td, cfg, intraday, analysis.get("quote"))
            if edge["net_reward_risk"] is not None and edge["net_reward_risk"] < min_rr:
                analysis = dict(analysis)
                analysis["verdict"] = "WATCH"
                analysis["verdict_text"] = (analysis.get("verdict_text") or "Setup found.") + (
                    f" Entry blocked: after round-trip costs (${edge['round_trip_cost_usd']:.2f}/share) the reward is only "
                    f"{edge['net_reward_risk']:.2f}x the risk (minimum {min_rr:g}x).")
                analysis["research_flags"] = list(analysis.get("research_flags") or []) + ["thin_edge_after_costs"]
                verdict = "WATCH"

        if verdict == "PASS":
            sig = _analysis_to_signal(analysis, cfg, preset, force=force)
            if sig:
                if still_authorized is not None and not still_authorized():
                    return None
                sig = _enrich_signal_with_llm(sig, analysis, cfg)
                block = _evidence_block(sig, cfg)
                if block and not sig.get("execution_block"):
                    sig["execution_block"] = block
                    sig["research_flags"] = list(sig.get("research_flags") or []) + ["setup_losing_record"]
                _apply_wsb_caution(sig, cfg)
                append_journal(
                    "scan_hit",
                    {"ticker": sym, "verdict": verdict, "lateness": lateness, "force": force},
                )
                return sig
        elif verdict == "WATCH":
            # Prefer PASS; rank WATCH above AVOID
            rank = 20 + rank_lateness.get(lateness, 0)
            if rank > best_rank:
                best_rank = rank
                best_watch = analysis
        elif verdict == "AVOID":
            # Prefer buy-side PASS/WATCH, but keep AVOID as research fallback
            rank = 5 + rank_lateness.get(lateness, 0)
            if rank > best_rank:
                best_rank = rank
                best_watch = analysis

    if best_watch is not None and pass_only:
        if notes is not None:
            notes.update(ticker=best_watch.get("ticker"), verdict=best_watch.get("verdict"),
                         text=best_watch.get("verdict_text"))
        return None
    if best_watch is not None:
        if still_authorized is not None and not still_authorized():
            return None
        # Queue best WATCH or AVOID research candidate when no PASS found
        sig = _analysis_to_signal(best_watch, cfg, preset, force=force)
        if sig:
            sig = _enrich_signal_with_llm(sig, best_watch, cfg)
            append_journal(
                "scan_hit",
                {
                    "ticker": best_watch.get("ticker"),
                    "verdict": best_watch.get("verdict"),
                    "lateness": (best_watch.get("entry_quality") or {}).get("label"),
                    "force": force,
                    "from_best_watch": True,
                    "research": True,
                },
            )
            return sig

    if force:
        append_journal(
            "scan_no_setups",
            {"watchlist": watchlist, "errors": errors[:10]},
        )
        if errors and len(errors) >= len(watchlist):
            # ALL scans failed — last-resort demo
            append_journal("scan_fallback_demo", {"errors": errors[:10]})
            return _demo_fallback_signal(cfg, preset, watchlist)
        return None

    if errors:
        append_journal("scan_errors", {"errors": errors[:10]})
        if notes is not None:
            notes["error"] = "Market data was unavailable; no completed setup assessment"
    return None


# Back-compat alias (old name)
def generate_demo_signal(
    cfg: dict | None = None, *, force: bool = False
) -> dict | None:
    return generate_scan_signal(cfg, force=force)



def _is_past_expiry(s: dict[str, Any], now: datetime) -> bool:
    exp = s.get("expires_at")
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
    except ValueError:
        return False
    return now >= exp_dt


def expire_stale_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark pending signals past their TTL as expired.

    `signals` may be a stale snapshot, so the write re-loads the file under `_lock`
    and only flips signals that are STILL pending there. Saving the snapshot itself
    would resurrect an in-flight Approve ("approving" → "pending", double fill) and
    drop signals added since the snapshot.
    """
    now = datetime.now(timezone.utc)
    candidates = {
        s.get("id")
        for s in signals
        if s.get("status") == "pending" and _is_past_expiry(s, now)
    }
    if not candidates:
        return signals
    expired: list[dict[str, Any]] = []
    with _lock:
        fresh = load_signals()
        for s in fresh:
            if s.get("id") in candidates and s.get("status") == "pending" and _is_past_expiry(s, now):
                s["status"] = "expired"
                expired.append(s)
        if expired:
            save_signals(fresh)
        for s in expired:
            append_journal("signal_expired", {"signal_id": s["id"], "ticker": s.get("ticker")})
    return fresh


def daily_stats(ledger: dict[str, Any]) -> dict[str, Any]:
    day = _today_str()
    d = ledger.get("daily", {}).get(day, {"trades": 0, "pnl": 0.0, "realized": 0.0})
    return {"date": day, **d}


def daily_recap(cfg: dict[str, Any], ledger: dict[str, Any], *, day: str | None = None) -> dict[str, Any]:
    """Auditable after-hours summary; never presents a closed market as actionable."""
    day = day or _today_str()
    def in_day(value):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return False  # Unknown timezone cannot establish a trading day.
            return stamp.astimezone(paper_loop_mod.NY_TZ).date().isoformat() == day
        except (ValueError, TypeError):
            return False
    stats = dict(ledger.get("daily", {}).get(day) or {})
    fills = []
    for fill in list(ledger.get("fills") or []) + list(ledger.get("fills_archive") or []):
        if in_day(fill.get("ts")):
            fills.append(fill)
    realized = sum(_finite_float(f.get("realized_pnl"), 0.0) or 0.0 for f in fills)
    fees = sum(_finite_float(f.get("fee_usd"), 0.0) or 0.0 for f in fills)
    wins = sum(1 for f in fills if (_finite_float(f.get("realized_pnl"), 0.0) or 0.0) > 0)
    losses = sum(1 for f in fills if (_finite_float(f.get("realized_pnl"), 0.0) or 0.0) < 0)
    decisions = [
        d for d in _decision_ring.latest(500)
        if in_day(d.get("ts") or d.get("timestamp"))
    ]
    if cfg.get("_paper_research"):
        decisions.extend(s for s in load_signals()
                         if s.get("paper_research") and signal_workspace(s) == "paper"
                         and in_day(s.get("ts")))
    actionable = [
        d for d in decisions
        if str(d.get("side") or d.get("decision") or "").lower() not in ("", "hold", "flat")
    ]
    blocked = sum(
        1 for d in decisions
        if d.get("abstain") or d.get("gated") or d.get("reject_reason")
        or "block" in str(d.get("reason") or "").lower()
    )
    close = paper_loop_mod.session_close_time(
        datetime.now(getattr(paper_loop_mod, "NY_TZ", timezone.utc)).date()
    )
    now_et = datetime.now(getattr(paper_loop_mod, "NY_TZ", timezone.utc))
    closed = close is None or now_et.time() >= close
    net = round(realized - fees, 2)
    return {
        "date": day,
        "market_closed": closed,
        "status": "after_hours" if closed else "intraday",
        "headline": (
            f"{day}: {('up' if net > 0 else 'down' if net < 0 else 'flat')} "
            f"${abs(net):,.2f} net after fees"
        ),
        "pnl_usd": round(float(stats.get("pnl") or net), 2),
        "realized_usd": round(realized, 2),
        "fees_usd": round(fees, 2),
        "fills": len(fills),
        "wins": wins,
        "losses": losses,
        "decisions": len(decisions),
        "actionable_decisions": len(actionable),
        "blocked_decisions": blocked,
        "open_positions": len([
            p for p in ledger.get("positions") or []
            if (_finite_float(p.get("shares"), 0.0) or 0.0) > 0
        ]),
        "top_tickers": sorted({
            str(x.get("ticker") or "").upper() for x in fills + decisions if x.get("ticker")
        })[:12],
        "note": "After-hours recap only. It is descriptive and does not authorize overnight or live orders.",
    }


def rth_pace_progress(
    pnl: float,
    target_usd: float | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Linear RTH pace vs make-today goal (9:30–16:00 ET). Fail soft."""
    out: dict[str, Any] = {
        "expected_usd": None,
        "actual_usd": round(float(pnl or 0), 2),
        "status": "n/a",
        "rth_frac": None,
    }
    try:
        tgt = float(target_usd) if target_usd not in (None, "") else None
    except (TypeError, ValueError):
        tgt = None
    if tgt is None or tgt <= 0:
        return out
    try:
        if paper_loop_mod.NY_TZ is not None:
            now = now or datetime.now(paper_loop_mod.NY_TZ)
            if now.tzinfo is None:
                now = now.replace(tzinfo=paper_loop_mod.NY_TZ)
            else:
                now = now.astimezone(paper_loop_mod.NY_TZ)
        else:
            now = now or datetime.now()
        # Weekend / NYSE holiday: treat as outside RTH
        close_t = paper_loop_mod.session_close_time(now.date())
        if close_t is None:
            out["rth_frac"] = 0.0
            out["expected_usd"] = 0.0
            out["status"] = "outside"
            return out
        open_m = 9 * 60 + 30
        close_m = close_t.hour * 60 + close_t.minute
        cur_m = now.hour * 60 + now.minute
        if cur_m < open_m:
            frac = 0.0
            status_outside = True
        elif cur_m >= close_m:
            frac = 1.0
            status_outside = True  # after 16:00 ET = outside (informational)
        else:
            frac = (cur_m - open_m) / float(close_m - open_m)
            status_outside = False
        frac = max(0.0, min(1.0, frac))
        expected = round(tgt * frac, 2)
        actual = round(float(pnl or 0), 2)
        out["rth_frac"] = round(frac, 4)
        out["expected_usd"] = expected
        out["actual_usd"] = actual
        if status_outside:
            out["status"] = "outside"
        elif actual >= tgt:
            out["status"] = "ahead"  # goal already hit
        else:
            # band: within 8% of expected → on pace
            band = max(5.0, abs(expected) * 0.08)
            if actual >= expected + band:
                out["status"] = "ahead"
            elif actual <= expected - band:
                out["status"] = "behind"
            else:
                out["status"] = "on_pace"
    except Exception:
        out["status"] = "n/a"
    return out


def daily_target_progress(cfg: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    stats = daily_stats(ledger)
    target = cfg.get("daily_profit_target_usd")
    pnl = float(stats.get("pnl", 0) or 0)
    out = {
        **stats,
        "target_usd": float(target) if target not in (None, "") else None,
        "pnl": pnl,
        "remaining_usd": None,
        "progress_pct": None,
        "target_hit": False,
        "pace": rth_pace_progress(pnl, None),
    }
    if out["target_usd"] is not None and out["target_usd"] > 0:
        rem = out["target_usd"] - pnl
        out["remaining_usd"] = round(rem, 2)
        out["progress_pct"] = round(max(0.0, min(100.0, (pnl / out["target_usd"]) * 100.0)), 1)
        out["target_hit"] = pnl >= out["target_usd"]
        out["pace"] = rth_pace_progress(pnl, out["target_usd"])
    return out




def _live_goal_view(cfg: dict[str, Any], broker_book: dict[str, Any] | None = None) -> dict[str, Any]:
    """Display-only broker day P&L vs soft daily goal. Does not change caps or place orders.

    Note: when day P&L reaches daily_profit_target_usd, _broker_risk_gate already pauses
    new risk. This payload only exposes that progress for the UI meter.
    """
    target = _finite_float(cfg.get("daily_profit_target_usd"))
    book = broker_book if isinstance(broker_book, dict) else None
    pnl = _finite_float((book or {}).get("day_pnl_usd")) if book else None
    risk_ready = bool((book or {}).get("risk_ready")) if book else False
    out: dict[str, Any] = {
        "target_usd": target if target is not None and target > 0 else None,
        "pnl_usd": pnl,
        "remaining_usd": None,
        "progress_pct": None,
        "target_hit": False,
        "risk_ready": risk_ready,
        "source": "broker_day_pnl" if pnl is not None else "unavailable",
        "note": "Display only. Soft goal also pauses new broker risk when hit; it never raises order/loss/trade caps.",
    }
    if out["target_usd"] is not None and pnl is not None:
        rem = out["target_usd"] - pnl
        out["remaining_usd"] = round(rem, 2)
        out["progress_pct"] = round(max(0.0, min(100.0, (pnl / out["target_usd"]) * 100.0)), 1)
        out["target_hit"] = pnl >= out["target_usd"]
        out["pace"] = rth_pace_progress(pnl, out["target_usd"])
    else:
        out["pace"] = rth_pace_progress(pnl or 0.0, None)
    return out


def _unrealized_mtm(ledger: dict[str, Any], marks: dict[str, float] | None = None) -> float:
    """Open MTM vs avg using actual marks; unknown positions are excluded."""
    marks = marks or {}
    total = 0.0
    for p in ledger.get("positions") or []:
        sh = float(p.get("shares") or 0)
        avg = float(p.get("avg_price") or 0)
        if sh <= 0 or avg <= 0:
            continue
        t = str(p.get("ticker") or "").upper()
        if t not in marks:
            continue
        mark = float(marks[t])
        side = (p.get("side") or "long").lower()
        if side in ("long", "buy"):
            total += (mark - avg) * sh
        else:
            total += (avg - mark) * sh
    return round(total, 2)


def _session_pnl_with_mtm(ledger: dict[str, Any], marks: dict[str, float] | None = None) -> float:
    """Realized day PnL + open unrealized MTM.

    With no explicit marks, uses prices seen in the last 10 minutes. Positions without
    a current mark are excluded from MTM and block new risk in the trade gate.
    """
    if marks is None:
        marks = _marks_for_ledger(ledger)
    stats = daily_stats(ledger)
    return round(float(stats.get("pnl", 0) or 0) + _unrealized_mtm(ledger, marks), 2)


def _missing_marks(ledger: dict[str, Any], marks: dict[str, float]) -> list[str]:
    return sorted(
        {
            str(p.get("ticker") or "").upper()
            for p in (ledger.get("positions") or [])
            if float(p.get("shares") or 0) > 0
            and p.get("ticker")
            and str(p.get("ticker") or "").upper() not in marks
        }
    )


def _day_scoreboard(ledger: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """P0.2 day equity curve + scoreboard. Calls helped = horizon outcomes (not fill W/L)."""
    board = session_track.build_day_scoreboard(
        ledger,
        cfg,
        today_str=_today_str(),
        pnl_fn=_session_pnl_with_mtm,
        daily_stats_fn=daily_stats,
        ny_tz=getattr(paper_loop_mod, "NY_TZ", None),
    )
    try:
        helped = hurt = flat = 0
        rows = _decision_ring.latest(200)
        for ev in rows or []:
            label = ev.get("outcome") or ev.get("outcome_label")
            if label == "helped":
                helped += 1
            elif label == "hurt":
                hurt += 1
            elif label == "flat":
                flat += 1
        decided = helped + hurt + flat
        if decided:
            board = dict(board or {})
            board["wins"] = helped
            board["losses"] = hurt
            board["flats"] = flat
            board["win_rate"] = round(helped / decided, 3)
            board["win_rate_source"] = "horizon"
    except Exception:
        pass
    # Calm Simple radar line (dedicated #radar-butler-line — does not invent P&L)
    try:
        if bool((cfg or {}).get("radar_enabled")):
            rb = market_radar.simple_butler_line(cfg)
            if rb:
                board = dict(board or {})
                board["radar_butler"] = rb
    except Exception:
        pass
    return board


def _append_equity_point(ledger: dict[str, Any], *, force: bool = False, marks=None) -> dict[str, Any] | None:
    return session_track.append_equity_curve_point(
        ledger,
        today_str=_today_str(),
        now_iso=_now_iso(),
        equity_fn=_ledger_equity_mtm,
        pnl_fn=_session_pnl_with_mtm,
        force=force,
        marks=marks,
    )


def check_paper_exit_intents(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """P0.4 — paper close when stop / take-profit hit (reducing only)."""
    cfg = cfg or load_config()
    with _lock:
        ledger = load_ledger()
        positions = [dict(p) for p in (ledger.get("positions") or [])]
    hits: list[dict[str, Any]] = []
    for pos in positions:
        stop = pos.get("stop_price")
        tp = pos.get("take_profit_price")
        if stop is None and tp is None:
            continue
        ticker = str(pos.get("ticker") or "").upper()
        shares = float(pos.get("shares") or 0)
        if not ticker or shares <= 0:
            continue
        mark = fetch_last_price(ticker)
        if mark is None or mark <= 0:
            continue
        # Trailing stop: ratchet toward the price (never away), persist, then check.
        if pos.get("trail_pct"):
            new_stop = session_track.ratchet_trailing_stop(pos, mark)
            if new_stop is not None:
                with _lock:
                    led = load_ledger()
                    for p in led.get("positions") or []:
                        if str(p.get("ticker") or "").upper() == ticker and (p.get("side") or "long") == (pos.get("side") or "long"):
                            p["stop_price"] = pos["stop_price"]
                            p["trail_high"] = pos.get("trail_high")
                            break
                    save_ledger(led)
                stop = pos.get("stop_price")
        side = (pos.get("side") or "long").lower()
        is_long = side in ("long", "buy")
        try:
            stop_f = float(stop) if stop is not None else None
            tp_f = float(tp) if tp is not None else None
        except (TypeError, ValueError):
            continue
        reason = None
        if is_long:
            if stop_f is not None and mark <= stop_f:
                reason = "paper_stop"
            elif tp_f is not None and mark >= tp_f:
                reason = "paper_take_profit"
        else:
            if stop_f is not None and mark >= stop_f:
                reason = "paper_stop"
            elif tp_f is not None and mark <= tp_f:
                reason = "paper_take_profit"
        if not reason:
            continue
        close_side = "sell" if is_long else "buy"
        sig = {
            "id": str(uuid.uuid4()),
            "ticker": ticker,
            "side": close_side,
            "suggested_shares": shares,
            "signal_price": mark,
            "mid": mark,
            "analysis_price": mark,
            "status": "pending",
            "reason": reason,
            "bracket_off": True,
            "no_bracket": True,
        }
        result = paper_fill(sig, cfg, source=reason)
        if result.get("ok"):
            hits.append(
                {
                    "ticker": ticker,
                    "reason": reason,
                    "mark": mark,
                    "stop": stop_f,
                    "take_profit": tp_f,
                    "fill": result.get("fill"),
                }
            )
            append_journal(
                reason,
                {"ticker": ticker, "mark": mark, "stop": stop_f, "take_profit": tp_f},
            )
            try:
                _decision_ring.append(
                    {
                        "event": "exit",
                        "ticker": ticker,
                        "side": close_side,
                        "decision": close_side,
                        "action": "Stop" if reason == "paper_stop" else "Take profit",
                        "filled": True,
                        "fill": result.get("fill"),
                        "exit_reason": reason,
                        "mid": mark,
                        "hold": False,
                        "sim": True,
                        "dry_run": True,
                        "simulated": True,
                        "paper_only": True,
                    }
                )
            except Exception:
                pass
        else:
            append_journal(
                "paper_exit_blocked",
                {"ticker": ticker, "reason": reason, "error": result.get("error")},
            )
    return hits


_outcome_check_lock = threading.Lock()


def check_decision_outcomes(limit: int = 40) -> list[dict[str, Any]]:
    """Grade only quotes observed within the recorded horizon window."""
    if not _outcome_check_lock.acquire(blocking=False):
        return []
    try:
        return _check_decision_outcomes(limit)
    finally:
        _outcome_check_lock.release()


def _check_decision_outcomes(limit: int) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stamped = []
    for ev in _decision_ring.pending_due(now, limit):
        due = datetime.fromisoformat(str(ev["outcome_due_ts"]).replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        tolerance = min(120, max(0, int(ev.get("outcome_tolerance_sec", 120))))
        missed = None
        if (now - due).total_seconds() > tolerance:
            missed = "missed_horizon_window"
        elif ev.get("scoring_version") != "horizon-net-v2" or any(ev.get(k) is None for k in ("slip_bps", "fee_bps")):
            missed = "legacy_missing_cost_snapshot"
        if missed:
            patched = _decision_ring.patch(ev["id"], {"outcome_pending": False,
                "outcome_status": missed, "outcome_ts": _now_iso()})
            if patched:
                stamped.append(patched)
            continue
        ticker = str(ev.get("ticker") or "").upper()
        if not ticker:
            continue
        mid_now = fetch_last_price(ticker)
        quote = _QUOTE_SNAPSHOTS.get(ticker) or {}
        try:
            market_time = datetime.fromisoformat(str(quote.get("market_time") or "").replace("Z", "+00:00"))
            if market_time.tzinfo is None:
                market_time = market_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        # Timestamp, not just retrieval time, must belong to this horizon.
        if not quote.get("fresh") or not due <= market_time <= min(now + timedelta(seconds=15), due + timedelta(seconds=tolerance)):
            continue
        mid_at = _finite_float(ev.get("mid_at_decision"), 0) or 0
        mid_now = _finite_float(mid_now, 0) or 0
        if mid_at <= 0 or mid_now <= 0:
            continue
        intended = str(ev.get("outcome_intended_side") or "flat")
        cost_args = {"slip_bps": ev["slip_bps"], "fee_bps": ev["fee_bps"]}
        result = session_track.classify_horizon_outcome(
            intended_side=intended, mid_at=mid_at, mid_now=mid_now,
            path_prices=ev.get("outcome_path_prices"), **cost_args)
        if not result.get("outcome"):
            continue
        shadow_result = {}
        shadow = ev.get("shadow_claude") or {}
        if shadow.get("side") and not shadow.get("error"):
            shadow_result = session_track.classify_horizon_outcome(
                intended_side=shadow["side"], mid_at=mid_at, mid_now=mid_now, **cost_args)
        updates = {
            "scoring_version": "horizon-net-v2",
            "outcome": result["outcome"], "outcome_pending": False, "outcome_status": "scored",
            "shadow_claude_outcome": shadow_result.get("outcome"),
            "shadow_claude_net_outcome": shadow_result.get("net_outcome"),
            "shadow_claude_executable_move_bps": shadow_result.get("executable_move_bps"),
            "outcome_mid": result.get("mid_now"), "outcome_move_bps": result.get("move_bps"),
            "outcome_ts": _now_iso(), "outcome_market_time": market_time.isoformat(),
            "outcome_quote": copy.deepcopy(quote),
            "outcome_delay_sec": round((market_time - due).total_seconds(), 3),
            "outcome_label": result["outcome"], "net_outcome": result.get("net_outcome"),
            "outcome_mfe_bps": result.get("mfe_bps"), "outcome_mae_bps": result.get("mae_bps"),
            "outcome_path_available": result.get("path_available"),
            "outcome_executable_move_bps": result.get("executable_move_bps"),
            "outcome_cost_bps": result.get("round_trip_cost_bps"), "paper_only": True,
        }
        patched = _decision_ring.patch(ev["id"], updates)
        if not patched:
            continue
        stamped.append(patched)
        lessons.record_outcome(LESSONS_PATH, patched)
        _decision_ring.append({"event": "outcome", "ticker": ticker, "ref_id": ev["id"],
            "ref_seq": ev.get("seq"), "decision": "hold", "side": "hold", "hold": True,
            "filled": False, "simulated": True, "paper_only": True, **updates,
            "action": "Horizon price observation", "horizon_min": ev.get("horizon_min"),
            "butler_note": "Price direction and modeled costs; this is not a broker fill."})
        append_journal("decision_outcome", {"ticker": ticker, "ref_id": ev["id"],
            "outcome": result["outcome"], "net_outcome": result.get("net_outcome")})
    return stamped


def maybe_refresh_equity_curve(cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Throttle-append equity point for SSE / loop ticks (no fake PnL).

    Fetches live marks for open positions when possible so MTM samples stay honest.
    """
    with _lock:
        ledger = load_ledger()
        tickers = [
            str(p.get("ticker") or "").upper()
            for p in (ledger.get("positions") or [])
            if p.get("ticker")
        ]
    marks: dict[str, float] = {}
    for t in tickers:
        try:
            px = fetch_last_price(t)
            if px is not None and float(px) > 0:
                marks[t] = float(px)
        except Exception:
            continue
    with _lock:
        ledger = load_ledger()
        pt = _append_equity_point(ledger, force=False, marks=marks or None)
        if pt:
            save_ledger(ledger)
        return pt


def can_take_trade(
    cfg: dict[str, Any],
    ledger: dict[str, Any],
    notional: float,
    *,
    bypass_gates: bool = False,
    reducing: bool = False,
    marks: dict[str, float] | None = None,
) -> tuple[bool, str]:
    """Risk gates for new paper fills. Mode alone is insufficient — session_active required.

    When bypass_gates=True (force flatten), skip max-loss/target/kill/session/RTH/trade caps.
    When reducing=True (pure close/cover), skip size/loss/trade/target caps so paper can exit.
    session_active + RTH still apply unless bypass_gates.
    """
    if bypass_gates:
        return True, "ok"

    # Fail closed while any data file is unreadable (ledger/config may be defaults)
    if _CORRUPT_PATHS:
        names = ", ".join(sorted(Path(k).name for k in _CORRUPT_PATHS))
        return False, f"Data file corrupt ({names}) — repair and restart before trading"

    # START session required for NEW risk. Pure exits (stop/target/close) are always
    # allowed in market hours — pressing STOP must not disable stop-loss protection.
    # Check the file too: `cfg` may be a snapshot taken before STOP was pressed.
    active_key = "paper_research_enabled" if cfg.get("_paper_research") else "session_active"
    if not reducing and not (cfg.get("session_active") and load_config().get(active_key)):
        return False, "Session not active — checking is off. Press Start checking first."

    preset = get_preset(cfg.get("risk_preset"))
    stats = daily_stats(ledger)
    pnl_mtm = _session_pnl_with_mtm(ledger, marks)
    if not reducing:
        marks_for_risk = marks if marks is not None else _marks_for_ledger(ledger)
        missing = _missing_marks(ledger, marks_for_risk)
        if missing:
            return False, f"Risk valuation unavailable for open position(s): {', '.join(missing)}"
    max_trades = preset["max_trades_per_day"]
    # kill-switch overrides when armed (optional research control; not required)
    ks = cfg.get("kill_switch") or {}
    if ks.get("armed"):
        if ks.get("max_trades_per_day") is not None:
            max_trades = int(ks["max_trades_per_day"])
        # Position-size kill applies to new risk only — never block a pure exit/cover
        if (
            not reducing
            and ks.get("max_position_size_usd") is not None
            and notional > float(ks["max_position_size_usd"])
        ):
            return False, "Safety stop: this trade is bigger than your max position size."
        if not reducing and ks.get("max_daily_loss_usd") is not None:
            if pnl_mtm <= -abs(float(ks["max_daily_loss_usd"])):
                return False, "Safety stop: today's max loss is reached (counting open positions). No new trades today."

    if not reducing:
        bs = bleed_status(ledger, load_config())
        if bs["paused"]:
            return False, (
                f"Paused: your last {bs['closed_trades']} closed trades lost "
                f"${abs(bs['net_usd']):,.2f} after costs (${bs['fees_usd']:,.2f} of that was fees). "
                "Review them, then press Resume on the desk."
            )

    # Session RTH gate (import is_rth from paper_loop)
    if cfg.get("rth_only", True) and not paper_loop_mod.is_rth():
        return False, "The market is closed. Trades wait for market hours (9:30 am – 4 pm Eastern, weekdays)."

    # Reducing closes may exit after loss/target/trade caps (stop the bleeding).
    if reducing:
        return True, "ok"

    # Session max loss (explicit config) — includes open MTM
    max_sess = cfg.get("max_session_loss_usd")
    if max_sess is not None and str(max_sess).strip() != "":
        try:
            if pnl_mtm <= -abs(float(max_sess)):
                return False, "Today's max loss is reached (counting open positions). No new trades today."
        except (TypeError, ValueError):
            pass

    if stats.get("trades", 0) >= max_trades:
        return False, f"You've hit today's limit of {max_trades} trades."

    equity = float(ledger.get("equity", 100_000))
    max_loss_pct = preset["max_daily_loss_pct"]
    max_loss = equity * (max_loss_pct / 100.0)
    if pnl_mtm <= -max_loss:
        return False, f"Today's loss limit ({max_loss_pct}% of your cash) is reached, counting open positions."

    max_pos = equity * (preset["max_position_pct"] / 100.0)
    if notional > max_pos * 1.05:  # small tolerance
        return False, f"Too big: one stock can be at most {preset['max_position_pct']}% of your cash on this risk setting."

    target = cfg.get("daily_profit_target_usd")
    if target is not None and float(target) > 0:
        # Target uses realized-only (goal bar); open MTM does not auto-stop on target
        if float(stats.get("pnl", 0)) >= float(target):
            return False, f"You reached today's goal of ${float(target):,.2f}. No new trades today — nice work."

    return True, "ok"


def _ledger_equity_mtm(ledger: dict[str, Any], mark_by_ticker: dict[str, float] | None = None) -> float:
    """Cash + long MTM − short MTM liability (prevents naked-short equity inflation)."""
    marks = mark_by_ticker or {}
    cash = float(ledger.get("cash") or 0)
    long_val = 0.0
    short_liab = 0.0
    for p in ledger.get("positions") or []:
        sh = float(p.get("shares") or 0)
        if sh <= 0:
            continue
        t = str(p.get("ticker") or "").upper()
        if t not in marks:
            continue
        mark = float(marks[t])
        side = (p.get("side") or "long").lower()
        if side in ("long", "buy"):
            long_val += sh * mark
        else:
            short_liab += sh * mark
    return round(cash + long_val - short_liab, 2)


def _marks_for_ledger(ledger: dict[str, Any], seed: dict[str, float] | None = None) -> dict[str, float]:
    """Return all usable recent marks, filling gaps before risk/equity checks."""
    marks = dict(seed or {})
    marks.update(recent_marks())
    tickers = {
        str(p.get("ticker") or "").upper()
        for p in (ledger.get("positions") or [])
        if p.get("ticker")
    }
    for ticker in sorted(tickers - set(marks)):
        try:
            px = fetch_last_price(ticker)
        except Exception:
            px = None
        if px is not None and float(px) > 0:
            marks[ticker] = float(px)
    return marks


def paper_fill(
    signal: dict[str, Any],
    cfg: dict[str, Any],
    source: str,
    *,
    bypass_gates: bool = False,
    allow_demo_price: bool = False,
) -> dict[str, Any]:
    """Simulated fill at signal price ± slip. Network fetch outside lock; ledger RMW under `_lock`.

    Buy covers short before opening long. Equity includes short MTM liability.
    Refuses demo/hash prices when a real quote is missing (abstain) unless allow_demo_price.
    bypass_gates=True for force flatten (skips max-loss/target/kill/session/RTH).
    Naked shorts are banned (sell only closes longs).
    """
    if signal.get("workspace") == "live":
        return {"ok": False, "error": "Live ideas cannot be filled in the paper workspace"}
    restriction = signal_execution_block(signal)
    if restriction:
        return {"ok": False, "error": restriction, "abstain": True}
    slip_bps = float(cfg.get("slip_bps", 5))
    side = signal["side"]
    ticker = str(signal.get("ticker") or "").upper()
    px = float(signal.get("signal_price") or 0)
    live = fetch_last_price(ticker)
    price_source = "live" if live else "signal"
    if live:
        px = float(live)
    elif not allow_demo_price and not bypass_gates:
        return {"ok": False, "error": "no_real_quote", "abstain": True}
    if not px or px <= 0:
        return {"ok": False, "error": "no_real_quote", "abstain": True}

    slip = px * (slip_bps / 10_000.0)
    fill_px = round(px + slip, 4) if side == "buy" else round(px - slip, 4)
    from trade_planner import paper_quantity
    fractional = bool(cfg.get("paper_fractional_enabled", False)) or side == "sell" or bypass_gates
    try:
        shares_req = paper_quantity(signal.get("suggested_shares", 1), fractional)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    atr = _finite_float(signal.get("atr_usd"))
    risk_pct = _finite_float(cfg.get("risk_per_trade_pct"), 0.0) or 0.0
    atr_multiple = _finite_float(cfg.get("atr_stop_multiple"), 1.0) or 1.0
    atr_risk_budget = None
    atr_stop_distance = None
    if atr and atr > 0 and risk_pct > 0:
        atr_risk_budget = float(cfg.get("paper_equity") or 0) * risk_pct / 100.0
        atr_stop_distance = atr * atr_multiple
        risk_shares = paper_quantity(atr_risk_budget / atr_stop_distance, fractional) if atr_stop_distance > 0 else 0
        if risk_shares <= 0 and not bypass_gates:
            return {"ok": False, "error": "risk_budget_below_one_share", "abstain": True}
        if risk_shares > 0:
            shares_req = min(shares_req, risk_shares)
    if shares_req <= 0:
        return {"ok": False, "error": "zero_shares", "abstain": True}
    notional = round(shares_req * fill_px, 2)
    risk_marks = _marks_for_ledger(load_ledger(), {ticker: fill_px})

    with _lock:
        ledger = load_ledger()
        if source == "auto_paper" and not cfg.get("_paper_research") and not bypass_gates:
            current_paper = load_config()
            if current_paper.get("mode") != "auto_paper":
                return {"ok": False, "error": "Paper automation was turned off; review manually", "requires_review": True}
            if any(current_paper.get(key) != cfg.get(key) for key in
                   ("risk_preset", "risk_per_trade_pct", "atr_stop_multiple", "slip_bps", "fee_bps",
                    "paper_fractional_enabled", "paper_order_budget", "moss_paper", "paper_equity")):
                return {"ok": False, "error": "Paper risk settings changed; request fresh research", "requires_review": True}
            cfg = current_paper
        if cfg.get("_paper_research") and not bypass_gates:
            current_paper = paper_research_config(load_config())
            if source == "auto_paper" and not current_paper.get("paper_auto_approve"):
                return {"ok": False, "error": "Paper auto approval was turned off; review manually", "requires_review": True}
            if any(current_paper.get(key) != cfg.get(key) for key in
                   ("risk_preset", "risk_per_trade_pct", "atr_stop_multiple", "slip_bps", "fee_bps", "paper_fractional_enabled", "paper_order_budget", "moss_paper")):
                return {"ok": False, "error": "Paper risk settings changed; request fresh research", "requires_review": True}
            cfg = current_paper
        if signal.get("moss_exit") or (side == "sell" and any(p.get("ticker")==ticker and p.get("moss_owned_shares") for p in ledger.get("positions",[]))):
            import moss_policy
            reason = moss_policy.quote_error(_QUOTE_SNAPSHOTS.get(ticker), datetime.now(timezone.utc), moss_policy.settings(cfg)["max_quote_age_sec"])
            if reason:
                return {"ok":False,"error":reason}
            if signal.get("moss_exit"):
                owned = sum(float(pos.get("moss_owned_shares") or 0) for pos in ledger.get("positions",[]) if pos.get("ticker")==ticker)
                shares_req = min(shares_req, owned)
                if side != "sell" or shares_req <= 0:
                    return {"ok":False,"error":"No Moss paper holding to close"}
                notional = round(shares_req * fill_px, 2)
        if (cfg.get("moss_paper") or {}).get("enabled") and source == "auto_paper" and side == "buy" and not signal.get("moss_policy_version"):
            return {"ok":False,"error":"Moss owns automatic paper entries; request a qualified Moss decision"}
        if signal.get("moss_policy_version") and side == "buy":
            import moss_paper, moss_policy
            confidence = _finite_float(signal.get("confidence"), 0) or 0
            minimum = max(float(llm_trader.min_decision_confidence(cfg)), float(get_preset(cfg.get("risk_preset")).get("min_confidence") or 0))
            if confidence < minimum:
                return {"ok":False,"error":"Moss paper confidence is below the configured minimum"}
            reason = moss_paper.entry_error(signal, cfg, ledger, _QUOTE_SNAPSHOTS.get(ticker), datetime.now(timezone.utc))
            if reason:
                return {"ok":False,"error":reason}
            p = moss_policy.settings(cfg)
            total_exposure = notional + sum(float(pos.get("shares") or 0)*float(risk_marks.get(pos.get("ticker")) or 0) for pos in ledger.get("positions",[]))
            if total_exposure > max(0, _ledger_equity_mtm(ledger, risk_marks))*p["max_total_exposure_pct"]/100:
                return {"ok":False,"error":"Moss total paper exposure limit"}
            if notional*(1+_cfg_fee_bps(cfg)/10000) > min(p["max_order_usd"],float(signal.get("moss_budget") or 0))+.001:
                return {"ok":False,"error":"Price moved beyond the approved paper budget; collect a new decision"}
        positions_snap = ledger.get("positions") or []
        # Pure reduce/cover: sell <= long shares or buy <= short shares for this ticker.
        # Mixed (close + reverse) keeps full gates so new risk still respects size caps.
        open_long = sum(
            float(p.get("shares") or 0)
            for p in positions_snap
            if p.get("ticker") == ticker and (p.get("side") or "").lower() == "long"
        )
        open_short = sum(
            float(p.get("shares") or 0)
            for p in positions_snap
            if p.get("ticker") == ticker and (p.get("side") or "").lower() == "short"
        )
        # Paper never opens shorts, so a sell against a long is always a close:
        # clamp it to the shares held instead of treating the overshoot as new risk.
        if side == "sell" and open_long > 0 and shares_req > open_long:
            shares_req = open_long
            notional = round(shares_req * fill_px, 2)
        reducing = (
            (side == "sell" and open_long > 0 and shares_req <= open_long)
            or (side == "buy" and open_short > 0 and shares_req <= open_short)
        )
        # Cap the simulated entry at the current paper-only dollar setting,
        # including modeled entry fees. Never cap a reducing exit.
        dollar_cap = _finite_float(cfg.get("paper_order_budget"), 0) or 0
        if dollar_cap > 0 and not reducing:
            shares_req = min(shares_req, paper_quantity(dollar_cap /
                (fill_px * (1 + _cfg_fee_bps(cfg) / 10000)), fractional))
            notional = round(shares_req * fill_px, 2)
            if shares_req <= 0:
                return {"ok": False, "error": "Paper budget is too small for the selected share size"}
        # Size caps apply to the whole position, not just this order.
        exposure = notional
        if side == "buy" and not reducing:
            exposure = notional + sum(
                float(p.get("shares") or 0) * float(p.get("avg_price") or fill_px)
                for p in positions_snap
                if p.get("ticker") == ticker and (p.get("side") or "").lower() == "long"
            )
        ok, reason = can_take_trade(
            cfg, ledger, exposure, bypass_gates=bypass_gates, reducing=reducing, marks=risk_marks
        )
        if not ok:
            return {"ok": False, "error": reason, "abstain": True}

        cash = float(ledger.get("cash", 0))
        positions = ledger.setdefault("positions", [])
        realized_pnl = 0.0
        position_id: str | None = None
        filled = 0
        day = _today_str()
        daily = ledger.setdefault("daily", {})
        d = daily.setdefault(day, {"trades": 0, "pnl": 0.0, "realized": 0.0})

        if side == "buy":
            remaining = shares_req
            # 1) Cover shorts first
            for p in list(positions):
                if remaining <= 0:
                    break
                if p.get("ticker") == ticker and (p.get("side") or "").lower() == "short":
                    close_sh = min(remaining, float(p["shares"]))
                    pnl = round((float(p["avg_price"]) - fill_px) * close_sh, 2)
                    realized_pnl += pnl
                    cash = round(cash - close_sh * fill_px, 2)
                    d["pnl"] = round(float(d["pnl"]) + pnl, 2)
                    d["realized"] = round(float(d["realized"]) + pnl, 2)
                    p["shares"] -= close_sh
                    remaining -= close_sh
                    filled += close_sh
                    if p["shares"] <= 0:
                        positions.remove(p)
            # 2) Open/add long with remainder (cash-limited)
            if remaining > 0:
                # Leave room for the fee so cash can't go negative
                fee_mult = 1.0 + _cfg_fee_bps(cfg) / 10_000.0
                can_buy = paper_quantity(max(0, cash) / max(fill_px * fee_mult, 0.01), fractional)
                long_sh = min(remaining, max(0, can_buy))
                if long_sh <= 0 and filled <= 0:
                    return {"ok": False, "error": "Not enough practice cash for this trade."}
                if long_sh > 0:
                    cost = round(long_sh * fill_px, 2)
                    cash = round(cash - cost, 2)
                    found = False
                    for p in positions:
                        if p["ticker"] == ticker and (p.get("side") or "").lower() == "long":
                            total_sh = p["shares"] + long_sh
                            p["avg_price"] = round(
                                (p["avg_price"] * p["shares"] + fill_px * long_sh) / total_sh, 4
                            )
                            p["shares"] = total_sh
                            found = True
                            break
                    if not found:
                        positions.append(
                            {
                                "position_id": str(uuid.uuid4()),
                                "ticker": ticker,
                                "side": "long",
                                "shares": long_sh,
                                "avg_price": fill_px,
                                "opened_ts": _now_iso(),
                            }
                        )
                    filled += long_sh
        else:
            # sell: close longs only — naked short banned
            remaining = shares_req
            for p in list(positions):
                if remaining <= 0:
                    break
                if p.get("ticker") == ticker and (p.get("side") or "").lower() == "long":
                    position_id = str(p.get("position_id") or position_id or "")
                    close_sh = min(remaining, float(p["shares"]))
                    pnl = round((fill_px - float(p["avg_price"])) * close_sh, 2)
                    realized_pnl += pnl
                    cash = round(cash + close_sh * fill_px, 2)
                    d["pnl"] = round(float(d["pnl"]) + pnl, 2)
                    d["realized"] = round(float(d["realized"]) + pnl, 2)
                    p["shares"] -= close_sh
                    remaining -= close_sh
                    filled += close_sh
                    if p["shares"] <= 0:
                        positions.remove(p)
            if remaining > 0 and filled <= 0:
                return {
                    "ok": False,
                    "error": "naked_short_banned" if not bypass_gates else "nothing_to_flatten",
                    "abstain": True,
                    "detail": f"{remaining} shares would open naked short",
                }
            # Partial close is OK (flatten / size)

        if filled <= 0:
            return {"ok": False, "error": "zero_fill", "abstain": True}

        notional = round(filled * fill_px, 2)
        fee_bps = _cfg_fee_bps(cfg)
        slip_usd = round(abs(fill_px - px) * filled, 4)
        fee_usd = round(notional * (fee_bps / 10_000.0), 4)
        friction_usd = round(slip_usd + fee_usd, 4)
        # Apply fee to paper cash (slip already in fill_px); keeps friction honest vs P&L
        cash = round(cash - fee_usd, 2)
        ledger["cash"] = cash
        # Fees are a real cost: today's P&L (goal + loss limits) is net of them.
        if fee_usd:
            d["pnl"] = round(float(d.get("pnl") or 0) - fee_usd, 2)
            d["fees"] = round(float(d.get("fees") or 0) + fee_usd, 4)
        fill = {
            "id": str(uuid.uuid4()),
            "ts": _now_iso(),
            "signal_id": signal.get("id"),
            "ticker": ticker,
            "side": side,
            "shares": filled,
            "price": fill_px,
            "notional": notional,
            "source": source,
            "slip_bps": slip_bps,
            "fee_bps": fee_bps,
            "slip_usd": slip_usd,
            "fee_usd": fee_usd,
            "friction_usd": friction_usd,
            "fill_style": "mid_or_taker",
            "maker_bias": False,
            "mid": px,
            "price_source": price_source,
            "simulated": True,
            "position_id": position_id,
            "atr_usd": atr,
            "atr_stop_distance_usd": round(atr_stop_distance, 4) if atr_stop_distance else None,
            "atr_risk_budget_usd": round(atr_risk_budget, 2) if atr_risk_budget else None,
        }
        if signal.get("moss_policy_version"):
            fill.update(decision_record_id=signal.get("decision_record_id"), moss_policy_version=signal["moss_policy_version"],
                        reference_quote=copy.deepcopy(_QUOTE_SNAPSHOTS.get(ticker)), moss_weight_snapshot=signal.get("moss_weight_snapshot"))
            if side == "buy":
                import moss_policy
                horizon=moss_policy.settings(cfg)["horizon_min"]
                for pos in positions:
                    if pos.get("ticker")==ticker:
                        pos.update(moss_owned_shares=filled,moss_exit_at=(datetime.now(timezone.utc)+timedelta(minutes=horizon)).isoformat())
        for pos in positions:
            if pos.get("ticker")==ticker and pos.get("moss_owned_shares"):
                pos["moss_owned_shares"]=min(float(pos["moss_owned_shares"]),float(pos["shares"]))
        if realized_pnl:
            fill["realized_pnl"] = round(realized_pnl, 2)

        d["trades"] = int(d.get("trades", 0)) + 1
        ledger["equity"] = _ledger_equity_mtm(ledger, _marks_for_ledger(ledger, {ticker: fill_px}))
        # P0.4 — stamp stop / take-profit on remaining open position (not on pure closes)
        # Only buys (open/add) set exit levels. Partial sells keep the levels already
        # on the position — they used to be re-derived from the sell price.
        if side == "buy" and not signal.get("bracket_off") and not signal.get("no_bracket") and source not in (
            "force_flatten",
            "paper_stop",
            "paper_take_profit",
            "paper_fill_reverse",
        ):
            try:
                # After buy: attach to long; after sell cover short rarely remains — attach to leftover long only
                open_side = None
                entry_ref = fill_px
                for p in positions:
                    if p.get("ticker") == ticker and float(p.get("shares") or 0) > 0:
                        open_side = (p.get("side") or "long").lower()
                        try:
                            entry_ref = float(p.get("avg_price") or fill_px)  # add-on → new average
                        except (TypeError, ValueError):
                            entry_ref = fill_px
                        break
                if open_side:
                    bracket_body = {
                        k: signal[k]
                        for k in (
                            "stop_loss",
                            "take_profit",
                            "stop_loss_pct",
                            "take_profit_pct",
                            "stop_pct",
                            "target_pct",
                            "bracket",
                            "bracket_off",
                            "no_bracket",
                            "use_bracket_defaults",
                            "trail_pct",
                        )
                        if k in signal
                    }
                    # Absolute stop/target only if approve stamped them (not screener)
                    if signal.get("_user_bracket_abs"):
                        if "stop" in signal:
                            bracket_body["stop"] = signal["stop"]
                        if "target" in signal:
                            bracket_body["target"] = signal["target"]
                    exits = session_track.resolve_exit_prices(
                        entry_px=entry_ref,
                        side="buy" if open_side == "long" else "sell",
                        preset=get_preset(cfg.get("risk_preset")),
                        signal=signal,
                        body=bracket_body,
                    )
                    session_track.attach_exit_intents(
                        positions,
                        ticker=ticker,
                        pos_side=open_side,
                        exits=exits,
                        now_iso=_now_iso(),
                    )
                    if exits.get("rejected"):
                        fill["exit_warnings"] = exits["rejected"]
                    if exits.get("trail_pct"):
                        fill["trail_pct"] = exits["trail_pct"]
                    if exits.get("bracket"):
                        fill["stop_price"] = exits.get("stop_price")
                        fill["take_profit_price"] = exits.get("take_profit_price")
                        fill["exit_bracket"] = True
            except Exception:
                pass
        ledger.setdefault("fills", []).insert(0, fill)
        ledger["fills"] = ledger["fills"][:200]
        try:
            _append_equity_point(ledger, force=True, marks={ticker: fill_px})
        except Exception:
            pass
        save_ledger(ledger)

        signal["status"] = "approved"
        signal["fill"] = fill
        append_journal(
            "paper_fill",
            {"fill_id": fill["id"], "signal_id": signal.get("id"), "source": source, "fill": fill},
        )
        return {"ok": True, "fill": fill, "ledger": ledger}


def ingest_signal(sig: dict[str, Any], *, research_only: bool = False,
                  expected_config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Keep live ideas and independent paper experiments separately identified."""
    from research_metrics import quote_benchmark
    sig.setdefault("execution_benchmarks", {}).setdefault("decision", quote_benchmark(sig.get("quote") or {}, _now_iso()))
    with _lock:
        cfg = copy.deepcopy(load_config())
        if expected_config is not None and cfg != expected_config:
            return None
    primary = "live" if cfg.get("mode") in ("live_manual", "auto_live") else "paper"
    workspace = sig.get("workspace") or primary
    sig["workspace"] = workspace
    sig.setdefault("mode_at_create", cfg.get("mode"))
    if workspace == "paper" and primary == "live":
        sig["paper_research"] = True
        return _ingest_one_signal(sig, research_only=research_only,
                                  cfg_override=paper_research_config(cfg))
    # Copy before live execution can mutate status/order identity. A paper experiment
    # has its own ID and never reuses a broker approval or broker result.
    paper = None
    if primary == "live" and cfg.get("paper_research_enabled") and not (cfg.get("moss_paper") or {}).get("enabled"):
        paper = copy.deepcopy(sig)
        paper.update(id=str(uuid.uuid4()), workspace="paper", paper_research=True,
                     source_signal_id=sig["id"], mode_at_create="auto_paper" if cfg.get("paper_auto_approve") else "manual")
        for key in ("live_response", "broker_order_id", "broker_order", "fill"):
            paper.pop(key, None)
        with _lock:
            _size_paper_research(paper, paper_research_config(cfg), load_ledger())
    result = _ingest_one_signal(sig, research_only=research_only, cfg_override=cfg)
    if paper:
        paper["source_signal_id"] = result["id"]
        _ingest_one_signal(paper, research_only=research_only,
                           cfg_override=paper_research_config(cfg))
    return result


def _ingest_one_signal(sig: dict[str, Any], *, research_only: bool = False,
                       cfg_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add signal to queue; auto-handle based on mode.

    Research desk: AVOID / late / chasing / low confidence are annotations
    only (`research_flags`). `reject_reason` stays empty while pending.
    TTL expiry still applies via expire_stale_signals.

    Lock discipline: brief lock for config/signals annotate+save; release;
    paper_fill / live stub outside lock; brief lock to persist signal updates.
    """
    with _lock:
        cfg = dict(cfg_override if cfg_override is not None else load_config())
        preset = get_preset(cfg.get("risk_preset"))
        risk = cfg.get("risk_preset", "mid")
        verdict = (sig.get("verdict") or "").upper()
        lateness = (sig.get("lateness_label") or "").lower()
        flags = list(sig.get("research_flags") or [])

        try:
            _conf = float(sig.get("confidence") or 0)
        except (TypeError, ValueError):
            _conf = 0.0
        if _conf < float(preset.get("min_confidence") or 0):
            flags.append("below_min_confidence")
        if verdict == "AVOID":
            if "verdict_avoid" not in flags:
                flags.append("verdict_avoid")
        if risk == "low" and lateness in ("late", "chasing"):
            flags.append(f"low_preset_would_block_{lateness}")
        elif risk == "mid" and lateness == "chasing":
            flags.append("mid_preset_would_block_chasing")

        sig["research_flags"] = flags
        if sig.get("status") not in ("approved", "rejected", "expired"):
            sig["status"] = "pending"
            sig["reject_reason"] = None

        cooldown = max(0, int(_finite_float(cfg.get("alert_cooldown_sec"), 0) or 0))
        now = datetime.now(timezone.utc)
        fingerprint = hashlib.sha256(json.dumps({
            "workspace": signal_workspace(sig),
            "ticker": str(sig.get("ticker") or "").upper(),
            "side": str(sig.get("side") or "").lower(),
            "verdict": verdict,
            "lateness": lateness,
            "source": sig.get("source") or sig.get("via") or "scanner",
            "price": round(_finite_float(sig.get("signal_price"), 0) or 0, 2),
        }, sort_keys=True).encode()).hexdigest()[:24]
        sig["alert_fingerprint"] = fingerprint
        if cooldown:
            for prior in load_signals():
                if (
                    prior.get("status") == "pending"
                    and prior.get("alert_fingerprint") == fingerprint
                    and isinstance(prior.get("quote"), dict)
                    and not signal_execution_block(prior, broker=signal_workspace(prior) == "live")
                ):
                    try:
                        age = (now - datetime.fromisoformat(str(prior.get("created_at") or prior.get("ts")).replace("Z", "+00:00"))).total_seconds()
                    except (TypeError, ValueError):
                        age = cooldown + 1
                    if age < cooldown:
                        append_journal("signal_deduplicated", {
                            "ticker": sig.get("ticker"), "duplicate_of": prior.get("id"),
                            "cooldown_sec": cooldown,
                        })
                        return prior

        if flags:
            append_journal(
                "signal_annotated",
                {
                    "signal_id": sig["id"],
                    "research_flags": flags,
                    "confidence": sig.get("confidence"),
                    "verdict": verdict,
                    "lateness": lateness,
                    "preset": risk,
                    "min_confidence": preset.get("min_confidence"),
                },
            )

        mode = cfg.get("mode", "manual")
        signals = load_signals()
        for prior in signals:
            if (prior.get("status") == "pending"
                    and signal_workspace(prior) == signal_workspace(sig)
                    and prior.get("ticker") == sig.get("ticker")):
                prior.update(status="expired", reject_reason="Superseded by fresh research",
                             superseded_by=sig["id"])
        signals.insert(0, sig)
        save_signals(signals)  # save_signals prunes old resolved ones, keeps pending
        append_journal(
            "signal_created",
            {"signal_id": sig["id"], "ticker": sig["ticker"], "side": sig["side"], "mode": mode,
             "research_only": research_only},
        )
        # research_only (New signal / Lucky buttons): always wait for Approve, never fill.
        if mode == "manual" or research_only or signal_execution_block(sig, broker=mode in ("auto_live", "live_manual")):
            return sig

    # --- fills / live stub OUTSIDE lock ---
    if mode == "auto_paper":
        if not cfg.get("session_active"):
            with _lock:
                sig["status"] = "rejected"
                sig["reject_reason"] = "session_not_active"
                signals = load_signals()
                for i, s in enumerate(signals):
                    if s["id"] == sig["id"]:
                        signals[i] = sig
                        break
                save_signals(signals)
                append_journal("auto_paper_blocked", {"signal_id": sig["id"], "error": "session_not_active"})
            return sig
        result = paper_fill(sig, cfg, source="auto_paper")
        with _lock:
            if not result.get("ok"):
                sig["status"] = "pending" if result.get("requires_review") else "rejected"
                sig["reject_reason"] = result.get("error", "auto_paper blocked")
                append_journal("auto_paper_blocked", {"signal_id": sig["id"], "error": result.get("error")})
            signals = load_signals()
            for i, s in enumerate(signals):
                if s["id"] == sig["id"]:
                    signals[i] = sig
                    break
            save_signals(signals)
        return sig

    if mode == "auto_live":
        # Gate first; broker-only on success; broker fail = rejected (no paper fallback).
        # Never dual-book (broker submit + paper_fill) for the same fill.
        if not cfg.get("session_active"):
            with _lock:
                sig["status"] = "rejected"
                sig["reject_reason"] = "session_not_active"
                signals = load_signals()
                for i, s in enumerate(signals):
                    if s["id"] == sig["id"]:
                        signals[i] = sig
                        break
                save_signals(signals)
                append_journal(
                    "auto_live_blocked",
                    {"signal_id": sig["id"], "error": "session_not_active", "gated": True},
                )
            return sig
        result = execute_gated_broker_or_paper(
            sig, cfg, source="auto_live", via="auto_live"
        )
        with _lock:
            live_note = result.get("broker")
            if live_note:
                sig["live_response"] = live_note
            if result.get("ok"):
                sig["status"] = "approved"
                sig["reject_reason"] = None
                append_journal(
                    "auto_live_fill",
                    {
                        "signal_id": sig["id"],
                        "broker": live_note,
                        "fill": result.get("fill"),
                        "book": result.get("book"),
                        "paper_fallback": bool(result.get("paper_fallback")),
                    },
                )
            else:
                sig["status"] = "broker_pending" if result.get("pending") else "rejected"
                sig["reject_reason"] = result.get("error", "auto_live blocked")
                append_journal(
                    "auto_live_blocked",
                    {
                        "signal_id": sig["id"],
                        "error": result.get("error"),
                        "broker": live_note,
                        "gated": bool(result.get("gated")),
                    },
                )
            signals = load_signals()
            for i, s in enumerate(signals):
                if s["id"] == sig["id"]:
                    signals[i] = sig
                    break
            save_signals(signals)
        return sig

    return sig





# ---------------------------------------------------------------------------
# Jev-style paper decision loop
# ---------------------------------------------------------------------------

_decision_ring = paper_loop_mod.DecisionRing(DECISIONS_PATH)
_paper_loop: paper_loop_mod.PaperLoop | None = None


def reverse_paper_fill(fill: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Undo a just-committed paper fill (STOP after fill). Opposite side, bypass gates."""
    if not fill or not fill.get("ticker"):
        return {"ok": False, "error": "no_fill"}
    side = (fill.get("side") or "").lower()
    opp = "sell" if side == "buy" else "buy"
    sig = {
        "id": str(uuid.uuid4()),
        "ticker": fill["ticker"],
        "side": opp,
        "signal_price": float(fill.get("price") or 0),
        "suggested_shares": float(fill.get("shares") or 0),
        "status": "pending",
        "confidence": 1.0,
        "analysis_price": float(fill.get("price") or 0),
        "mid": float(fill.get("mid") or fill.get("price") or 0),
        "reason": "reverse_after_stop",
    }
    cfg2 = dict(cfg or load_config())
    cfg2["rth_only"] = False
    result = paper_fill(
        sig, cfg2, source="reverse_after_stop", bypass_gates=True, allow_demo_price=True
    )
    append_journal(
        "paper_fill_reversed",
        {"original_fill_id": fill.get("id"), "ok": result.get("ok"), "error": result.get("error")},
    )
    return result


def paper_fill_maker(signal: dict[str, Any], cfg: dict[str, Any], source: str = "loop_paper") -> dict[str, Any]:
    """Honest paper fill for loop: same taker direction as paper_fill (buy mid+slip, sell mid−slip).

    Removed edge-favorable maker bias. Tags fill as mid_or_taker; maker_bias=False.
    Network fetch happens inside paper_fill (outside lock).
    """
    result = paper_fill(signal, cfg, source=source)
    if result.get("ok") and result.get("fill"):
        result["fill"]["maker_bias"] = False
        result["fill"]["fill_style"] = "mid_or_taker"
        # keep mid if paper_fill set it
        result["fill"].setdefault("mid_or_taker", True)
    return result



def build_loop_signal(
    analysis: dict,
    thesis: dict,
    cfg: dict,
    *,
    mid: float | None = None,
) -> dict[str, Any]:
    """Build a signal dict from screener analysis + LLM thesis for loop fills."""
    from datetime import timedelta

    preset = get_preset(cfg.get("risk_preset"))
    ticker = str(analysis.get("ticker") or "").upper()
    price = float(mid or analysis.get("price") or 0)
    if not price:
        # No demo/hash fallback for loop fills — caller must abstain
        price = 0.0
    side = (thesis.get("side") or "buy").lower()
    if side not in ("buy", "sell"):
        side = "buy"
    try:
        llm_conf = float(thesis.get("confidence") or 0)
    except (TypeError, ValueError):
        llm_conf = 0.0
    playbook_conf = _confidence_for_verdict(
        (analysis.get("verdict") or "WATCH"),
        ((analysis.get("entry_quality") or {}) or {}).get("label"),
    )
    conf = round(0.5 * playbook_conf + 0.5 * llm_conf, 3) if llm_conf > 0 else playbook_conf

    equity = float(load_ledger().get("equity", cfg.get("paper_equity", 100_000)))
    size_pct = preset["max_position_pct"]
    notional = equity * (size_pct / 100.0)
    shares = max(1, int(notional / max(price, 0.01)))
    stop_dist = round(price * 0.008 * preset["stop_r"], 2)
    target_dist = round(stop_dist * preset["target_r"], 2)
    if side == "sell":
        stop = round(price + stop_dist, 2)
        target = round(price - target_dist, 2)
    else:
        stop = round(price - stop_dist, 2)
        target = round(price + target_dist, 2)
    ttl = int(cfg.get("signal_ttl_sec", 900))
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    entry = analysis.get("entry_quality") or {}
    lateness = entry.get("label") if isinstance(entry, dict) else None
    thesis_text = thesis.get("thesis") or ""
    return {
        "id": str(uuid.uuid4()),
        "ts": _now_iso(),
        "ticker": ticker,
        "side": side,
        "confidence": conf,
        "reason": f"[LOOP {side} {int(round(conf * 100))}%] {thesis_text}"[:500],
        "signal_price": price,
        "analysis_price": analysis.get("price"),
        "suggested_shares": shares,
        "suggested_notional": round(shares * price, 2),
        "size_mult": 1.0,
        "stop": stop,
        "target": target,
        "stop_r": preset["stop_r"],
        "target_r": preset["target_r"],
        "preset": cfg.get("risk_preset", "mid"),
        "status": "pending",
        "expires_at": exp.isoformat(),
        "fill": None,
        "mode_at_create": cfg.get("mode", "auto_paper"),
        "verdict": analysis.get("verdict"),
        "verdict_text": analysis.get("verdict_text"),
        "entry_quality": entry if isinstance(entry, dict) else None,
        "lateness_label": lateness,
        "llm_thesis": thesis_text or None,
        "llm_side": thesis.get("side"),
        "llm_confidence": llm_conf,
        "llm_model": thesis.get("llm_model"),
        "sources": analysis.get("sources") or [],
        "research_flags": ["paper_loop"],
        "reject_reason": None,
        "loop": True,
    }


def execute_loop_decision(
    *,
    analysis: dict,
    thesis: dict,
    cfg: dict,
    mid: float | None = None,
) -> dict[str, Any]:
    """Execute one loop decision: abstain gates or honest paper fill.

    Always may return abstain=True (hold) — never forces a trade.
    Requires analysis verdict in (PASS, WATCH). AVOID or missing → abstain.
    Late/chasing → abstain unless cfg allow_late (default False for loop).
    Effective min_confidence floor 0.55 for loop path (override 0.0 presets).
    Confidence gate uses the same blended conf persisted on the signal.
    """
    if thesis.get("error") or thesis.get("abstain") or thesis.get("execution_block"):
        return {"ok": False, "abstain": True, "reason": thesis.get("execution_block") or thesis.get("error") or "brain_abstain"}
    side = (thesis.get("side") or "flat").lower()
    if side not in ("buy", "sell"):
        return {"ok": False, "abstain": True, "reason": "flat_abstain"}

    verdict = (analysis.get("verdict") or "").upper()
    if verdict not in ("PASS", "WATCH"):
        return {
            "ok": False,
            "abstain": True,
            "reason": f"verdict_{verdict or 'missing'}",
        }

    entry = analysis.get("entry_quality") or {}
    if not isinstance(entry, dict) or not entry:
        return {"ok": False, "abstain": True, "reason": "missing_entry_quality"}
    lateness = (entry.get("label") if isinstance(entry, dict) else None) or ""
    allow_late = bool(cfg.get("allow_late", False))
    if lateness.lower() in ("late", "chasing") and not allow_late:
        return {
            "ok": False,
            "abstain": True,
            "reason": f"lateness_{lateness.lower()}",
        }

    # Build signal first so conf gate == persisted confidence
    sig = build_loop_signal(analysis, thesis, cfg, mid=mid)
    # size_mult when allow_late and late/chasing
    if allow_late and lateness.lower() in ("late", "chasing"):
        mult = _suggested_size_cut(lateness)
        sig["size_mult"] = mult
        shares = max(1, int(int(sig.get("suggested_shares") or 1) * mult))
        sig["suggested_shares"] = shares
        px = float(sig.get("signal_price") or 0)
        sig["suggested_notional"] = round(shares * px, 2)

    # Prism soft size (ADVISORY_SOFT_SIZE=1 only) — half size; never flips side
    try:
        adv_mult = _finite_float(thesis.get("advisory_size_mult", 1.0), 0)
    except (TypeError, ValueError):
        adv_mult = 0.0
    if not 0 < adv_mult <= 1:
        return {"ok": False, "abstain": True, "reason": "advisory_zero_or_invalid_size"}
    if 0 < adv_mult < 1.0:
        cur_m = float(sig.get("size_mult") or 1.0)
        sig["size_mult"] = round(cur_m * adv_mult, 4)
        shares = max(0, int(int(sig.get("suggested_shares") or 0) * adv_mult))
        sig["suggested_shares"] = shares
        px = float(sig.get("signal_price") or 0)
        sig["suggested_notional"] = round(shares * px, 2)
        sig["advisory_size_mult"] = adv_mult

    try:
        conf = float(sig.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    preset = get_preset(cfg.get("risk_preset"))
    env_floor = float(llm_trader.min_decision_confidence(cfg))
    min_conf = max(env_floor, float(preset.get("min_confidence") or 0))
    if conf < min_conf:
        return {
            "ok": False,
            "abstain": True,
            "reason": f"low_confidence ({conf:.2f}<{min_conf})",
        }

    if not cfg.get("session_active"):
        return {"ok": False, "abstain": True, "reason": "session_not_active"}

    ledger = load_ledger()
    if cfg.get("paper_fractional_enabled") or cfg.get("paper_order_budget"):
        _size_paper_research(sig, cfg, ledger)
    ticker = str(sig.get("ticker") or "").upper()
    shares_req = max(0, float(sig.get("suggested_shares") or 0))
    positions_snap = ledger.get("positions") or []
    open_long = sum(
        float(p.get("shares") or 0)
        for p in positions_snap
        if p.get("ticker") == ticker and (p.get("side") or "").lower() == "long"
    )
    open_short = sum(
        float(p.get("shares") or 0)
        for p in positions_snap
        if p.get("ticker") == ticker and (p.get("side") or "").lower() == "short"
    )
    if side == "sell" and open_long > 0 and shares_req > open_long:
        shares_req = open_long
        sig["suggested_shares"] = shares_req
        try:
            sig["suggested_notional"] = round(shares_req * float(sig.get("signal_price") or 0), 2)
        except (TypeError, ValueError):
            pass
    reducing = (
        (side == "sell" and open_long > 0 and shares_req <= open_long)
        or (side == "buy" and open_short > 0 and shares_req <= open_short)
    )
    ok, reason = can_take_trade(
        cfg,
        ledger,
        float(sig.get("suggested_notional") or 0),
        reducing=reducing,
    )
    if not ok:
        return {"ok": False, "abstain": True, "reason": reason}

    # Tag quote provenance for paper_fill honesty
    if mid is not None:
        sig["mid"] = mid
    if analysis.get("price") is not None:
        sig["analysis_price"] = analysis.get("price")
    sig["quote_ok"] = bool(mid or analysis.get("price"))

    mode = (cfg.get("mode") or "manual").lower()

    # API pack: macro gate may force Ask-me-first even in auto_paper
    macro_force = bool(thesis.get("macro_force_ask_first"))
    if thesis.get("macro_butler_note"):
        sig["macro_butler_note"] = thesis.get("macro_butler_note")
        sig["macro_reasons"] = thesis.get("macro_reasons") or []
        sig["research_note"] = thesis.get("macro_butler_note")
    if thesis.get("macro_size_mult") is not None:
        sig["macro_size_mult"] = thesis.get("macro_size_mult")

    # Ask me first (manual + session): enqueue Waiting — do not auto-execute
    # Also when macro_force_ask_first on Fed/CPI/earnings day
    if mode == "manual" or macro_force:
        sig["status"] = "pending"
        sig["workspace"] = "paper" if cfg.get("_paper_research") or mode in ("manual", "auto_paper") else "live"
        sig["mode_at_create"] = "manual" if mode == "manual" else "macro_ask_first"
        sig["fill"] = None
        if macro_force and mode != "manual":
            sig["macro_forced_pending"] = True
        # Drop absolute screener stop/target so Approve blank → fill_px + preset %
        sig.pop("stop", None)
        sig.pop("target", None)
        with _lock:
            signals = load_signals()
            signals.insert(0, sig)
            save_signals(signals[:300])
            append_journal(
                "loop_pending",
                {
                    "signal_id": sig["id"],
                    "ticker": sig["ticker"],
                    "side": sig["side"],
                    "mode": sig["mode_at_create"],
                    "macro_force": macro_force,
                },
            )
        try:
            desk_alerts.alert_waiting_enqueue(sig.get("ticker") or "", sig.get("side"))
        except Exception:
            pass
        return {
            "ok": True,
            "abstain": False,
            "pending": True,
            "queued": True,
            "signal": sig,
            "macro_force_ask_first": macro_force,
        }

    # auto_paper: honest paper fill
    result = paper_fill_maker(sig, cfg, source="loop_paper")
    if not result.get("ok"):
        return {"ok": False, "abstain": True, "reason": result.get("error", "fill_blocked")}

    sig["status"] = "approved"
    sig["fill"] = result.get("fill")
    with _lock:
        signals = load_signals()
        signals.insert(0, sig)
        save_signals(signals[:300])
        append_journal(
            "loop_fill",
            {
                "signal_id": sig["id"],
                "ticker": sig["ticker"],
                "side": sig["side"],
                "fill": result.get("fill"),
            },
        )
    return {"ok": True, "abstain": False, "fill": result.get("fill"), "signal": sig}



# ---------------------------------------------------------------------------
# Midday risk check (once per trading day, after 12:00 ET, market open):
#  - cut positions down more than MIDDAY_CUT_PCT
#  - protect winners up MIDDAY_BREAKEVEN_PCT or more by raising the stop to the
#    price paid (a winner can no longer turn into a loss)
# ---------------------------------------------------------------------------
MIDDAY_CUT_PCT = 7.0
MIDDAY_BREAKEVEN_PCT = 3.0


def midday_risk_check(cfg: dict[str, Any] | None = None, *, force: bool = False, now: datetime | None = None) -> dict[str, Any] | None:
    cfg = cfg or load_config()
    if cfg.get("midday_check_enabled", True) is False:
        return None
    tz = paper_loop_mod.NY_TZ
    now = now or (datetime.now(tz) if tz else datetime.now())
    day = now.strftime("%Y-%m-%d")
    if not force:
        if now.hour < 12 or not paper_loop_mod.is_rth(now):
            return None
    with _lock:
        ledger = load_ledger()
        if ledger.get("midday_done_day") == day and not force:
            return None
        positions = [dict(p) for p in (ledger.get("positions") or []) if float(p.get("shares") or 0) > 0]
        ledger["midday_done_day"] = day
        save_ledger(ledger)
    cut, protected = [], []
    for pos in positions:
        t = str(pos.get("ticker") or "").upper()
        if (pos.get("side") or "long").lower() not in ("long", "buy"):
            continue
        try:
            avg = float(pos.get("avg_price") or 0)
        except (TypeError, ValueError):
            continue
        mark = fetch_last_price(t)
        if not mark or avg <= 0:
            continue
        chg = (mark / avg - 1) * 100
        if chg <= -MIDDAY_CUT_PCT:
            sig = {
                "id": str(uuid.uuid4()), "ticker": t, "side": "sell",
                "suggested_shares": float(pos.get("shares") or 0),
                "signal_price": mark, "mid": mark, "analysis_price": mark,
                "status": "pending", "reason": "midday_cut", "bracket_off": True, "no_bracket": True,
            }
            res = paper_fill(sig, cfg, source="midday_cut")
            if res.get("ok"):
                cut.append({"ticker": t, "change_pct": round(chg, 2)})
        elif chg >= MIDDAY_BREAKEVEN_PCT:
            with _lock:
                led = load_ledger()
                for p in led.get("positions") or []:
                    if str(p.get("ticker") or "").upper() == t and (p.get("side") or "long") == (pos.get("side") or "long"):
                        cur = p.get("stop_price")
                        try:
                            cur_f = float(cur) if cur is not None else None
                        except (TypeError, ValueError):
                            cur_f = None
                        if cur_f is None or cur_f < avg:
                            p["stop_price"] = round(avg, 4)
                            protected.append({"ticker": t, "change_pct": round(chg, 2), "new_stop": round(avg, 4)})
                        break
                save_ledger(led)
    summary = {"day": day, "cut": cut, "protected": protected, "checked": len(positions)}
    append_journal("midday_check", summary)
    if cut or protected:
        bits = []
        if cut:
            bits.append("closed " + ", ".join(f"{c['ticker']} ({c['change_pct']:+.1f}%)" for c in cut))
        if protected:
            bits.append("protected " + ", ".join(f"{p['ticker']} (stop raised to what you paid)" for p in protected))
        try:
            desk_alerts.emit("midday", "Midday check: " + "; ".join(bits), detail=summary, level="info",
                             dedupe_key=f"midday|{day}")
        except Exception:
            pass
    return summary


# ---------------------------------------------------------------------------
# Weekly report card
# ---------------------------------------------------------------------------
def weekly_report(days: int = 7) -> dict[str, Any]:
    from datetime import timedelta as _td

    cutoff = (datetime.now(timezone.utc) - _td(days=days)).isoformat()
    with _lock:
        ledger = load_ledger()
        cfg = load_config()
    fills = {}
    for f in list(ledger.get("fills") or []) + list(ledger.get("fills_archive") or []):
        if isinstance(f, dict) and str(f.get("ts") or "") >= cutoff and not f.get("broker"):
            fills[str(f.get("id") or id(f))] = f
    fills_l = list(fills.values())
    closed = [f for f in fills_l if str(f.get("side") or "").lower() == "sell" or "realized_pnl" in f]
    realized = round(sum(float(f.get("realized_pnl") or 0) for f in closed), 2)
    fees = round(sum(float(f.get("fee_usd") or 0) for f in fills_l), 2)
    net = round(realized - fees, 2)
    wins = [f for f in closed if float(f.get("realized_pnl") or 0) > 0]
    best = max(closed, key=lambda f: float(f.get("realized_pnl") or 0), default=None)
    worst = min(closed, key=lambda f: float(f.get("realized_pnl") or 0), default=None)
    ls = [r for r in lessons.recent(LESSONS_PATH, 500) if str(r.get("ts") or "") >= cutoff]
    helped = sum(1 for r in ls if r.get("outcome") == "helped")
    hurt = sum(1 for r in ls if r.get("outcome") == "hurt")
    hit = helped / (helped + hurt) if (helped + hurt) else None
    # Setups that hurt most this week
    by_setup: dict[str, list[int]] = {}
    for r in ls:
        k = f"{r.get('side')}|{r.get('setup')}"
        h = by_setup.setdefault(k, [0, 0])
        if r.get("outcome") == "helped":
            h[0] += 1
        elif r.get("outcome") == "hurt":
            h[1] += 1
    worst_setups = sorted(
        ([k, v[0], v[1]] for k, v in by_setup.items() if v[1] >= 2 and v[1] > v[0]),
        key=lambda x: -x[2],
    )[:3]
    enough = len(closed) >= 5 or (helped + hurt) >= 10
    if not enough:
        grade, why = "—", "Not enough trades or scored calls yet to grade fairly."
    elif net > 0 and (hit is None or hit >= 0.6):
        grade, why = "A", "Made money after costs and most calls were right."
    elif net > 0:
        grade, why = "B", "Made money after costs, but the calls were hit-and-miss."
    elif hit is not None and hit >= 0.5:
        grade, why = "C", "Calls were right about half the time, but costs or sizing ate the gains."
    elif hit is not None and hit >= 0.4:
        grade, why = "D", "Lost money after costs and fewer than half the calls were right."
    else:
        grade, why = "F", "Lost money after costs and most calls were wrong. Stay on paper and review the setups below."
    bench = None
    try:
        bench = benchmark_status(ledger, cfg)
    except Exception:
        pass

    def _brief(f):
        if not f:
            return None
        return {"ticker": f.get("ticker"), "pnl": round(float(f.get("realized_pnl") or 0), 2), "ts": f.get("ts")}

    return {
        "ok": True,
        "days": days,
        "grade": grade,
        "grade_reason": why,
        "closed_trades": len(closed),
        "wins": len(wins),
        "realized_usd": realized,
        "fees_usd": fees,
        "net_usd": net,
        "calls_helped": helped,
        "calls_hurt": hurt,
        "hit_rate": round(hit, 3) if hit is not None else None,
        "best": _brief(best),
        "worst": _brief(worst),
        "worst_setups": [
            {"setup": s.split("|", 1)[1], "side": s.split("|", 1)[0], "helped": h, "hurt": u}
            for s, h, u in worst_setups
        ],
        "benchmark": bench,
    }


@app.route("/api/report/weekly")
def api_report_weekly():
    try:
        days = max(1, min(90, int(request.args.get("days") or 7)))
    except ValueError:
        days = 7
    return jsonify(weekly_report(days))


@app.route("/api/report/daily")
def api_report_daily():
    cfg = load_config()
    ledger = load_ledger()
    day = (request.args.get("date") or "").strip() or None
    return jsonify({"ok": True, "recap": daily_recap(cfg, ledger, day=day)})


def _with_lessons(analysis: dict, cfg: dict | None = None) -> dict:
    """Attach this desk's own track record for the setup (numbers only)."""
    if not isinstance(analysis, dict):
        return analysis
    try:
        eq = analysis.get("entry_quality") or {}
        rec = lessons.track_record(
            LESSONS_PATH,
            ticker=str(analysis.get("ticker") or ""),
            verdict=analysis.get("verdict"),
            lateness=(eq.get("label") if isinstance(eq, dict) else None) or analysis.get("lateness_label"),
            scope=_research_scope(cfg) if cfg is not None else None,
        )
        note = lessons.prompt_note(rec)
    except Exception:
        note = None
    if not note:
        return analysis
    out = dict(analysis)
    out["past_results_for_similar_setups"] = note
    # Keep the numeric evidence available to the decision engine; prose alone
    # can be misunderstood and cannot be audited after a decision.
    out["learning_context"] = {
        "scope": rec.get("scope"),
        "setup": rec.get("setup"),
        "sample_count": rec.get("setup_count", 0),
        "side_stats": rec.get("side_stats", {}),
        "instruction": "Use this as prior evidence, not as a guarantee. Abstain when the matching side has persistent negative results.",
    }
    return out


@app.route("/api/lessons")
def api_lessons():
    try:
        limit = int(request.args.get("limit") or 50)
    except ValueError:
        limit = 50
    return jsonify({"ok": True, "lessons": lessons.recent(LESSONS_PATH, limit)})


class _ClaudeShadow:
    """Run Claude on the same facts in a background thread (head-to-head, no trading)."""

    def __init__(self, analysis: dict, horizon_min: int):
        self._out: dict[str, Any] | None = None
        self._t = threading.Thread(target=self._run, args=(analysis, horizon_min), daemon=True, name="claude-shadow")
        self._t.start()

    def _run(self, analysis: dict, horizon_min: int) -> None:
        try:
            self._out = claude_brain.decide(analysis, horizon_min=horizon_min)
        except Exception as exc:  # noqa: BLE001
            self._out = {"side": "flat", "error": f"claude_shadow:{type(exc).__name__}"}

    def result(self, timeout: float) -> dict[str, Any] | None:
        self._t.join(timeout)
        return self._out


def brain_scoreboard(days: int = 30) -> dict[str, Any]:
    """Head-to-head on the SAME decisions: main brain vs. Claude shadow."""
    from datetime import timedelta as _td

    cutoff = (datetime.now(timezone.utc) - _td(days=days)).isoformat()
    candidates = [r for r in lessons.recent(LESSONS_PATH, 1000)
                  if r.get("claude_outcome") and str(r.get("ts") or "") >= cutoff]
    rows = [r for r in candidates if r.get("scoring_version") == "horizon-net-v2"
            and r.get("outcome_status") == "scored" and not r.get("mock") and not r.get("routed")
            and all(r.get(k) is not None for k in ("llm_model", "shadow_claude_model", "prompt_version", "horizon_min", "input_hash"))]
    def tally(key, samples=None):
        d = {"helped": 0, "hurt": 0, "flat": 0}
        for r in rows if samples is None else samples:
            o = r.get(key)
            if o in d:
                d[o] += 1
        dec = d["helped"] + d["hurt"]
        d["hit_rate"] = round(d["helped"] / dec, 3) if dec else None
        return d
    main_names = sorted({str(r.get("brain") or "main") for r in rows})
    groups = {}
    for row in rows:
        key = (row["llm_model"], row["shadow_claude_model"], row["prompt_version"], row["horizon_min"])
        groups.setdefault(key, []).append(row)
    cohorts = [{"main_model": key[0], "claude_model": key[1], "prompt_version": key[2],
                "horizon_min": key[3], "samples": len(values), "main_net": tally("net_outcome", values),
                "claude_net": tally("shadow_claude_net_outcome", values)} for key, values in groups.items()]
    return {
        "ok": True,
        "days": days,
        "compared_calls": len(rows),
        "main_brain": ", ".join(main_names) or None,
        "main": tally("outcome"),
        "claude": tally("claude_outcome"),
        "main_net": tally("net_outcome"), "claude_net": tally("shadow_claude_net_outcome"),
        "cohorts": cohorts, "excluded_unverified": len(candidates) - len(rows),
        "metric": "Price direction plus modeled after-cost result; holds excluded from net statistics. Not broker returns.",
        "claude_configured": claude_brain.is_configured(),
        "claude_model": claude_brain.model_name(),
    }


@app.route("/api/backtest", methods=["GET", "POST"])
def api_backtest():
    """GET: progress + last result. POST: start a test on the watchlist (background)."""
    if request.method == "GET":
        return jsonify({"ok": True, "status": backtest.status(), "result": backtest.last_result(BACKTEST_PATH)})
    body = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    tickers = body.get("tickers") if isinstance(body.get("tickers"), list) else None
    if not tickers:
        tickers = prune_watchlist_for_trading_safe(cfg)[:20]
    tickers = [str(t).upper().strip() for t in tickers if re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,9}", str(t).strip())][:40]
    if not tickers:
        return jsonify({"ok": False, "error": "No stocks to test — add some to your watchlist."}), 400
    preset = get_preset(cfg.get("risk_preset"))
    equity = float(cfg.get("paper_equity") or 10_000)
    params = {
        "stop_pct": 0.8 * float(preset.get("stop_r") or 1.0),
        "target_r": float(preset.get("target_r") or 2.5),
        "slip_bps": float(cfg.get("slip_bps", 5) or 0),
        "fee_bps": _cfg_fee_bps(cfg),
        "position_usd": round(equity * float(preset.get("max_position_pct") or 2) / 100.0, 2),
    }
    started, message = backtest.start(tickers, BACKTEST_PATH, params)
    if not started:
        return jsonify({"ok": False, "error": message}), 409
    append_journal("backtest_started", {"tickers": tickers, "params": params})
    return jsonify({"ok": True, "started": True, "tickers": tickers})


def prune_watchlist_for_trading_safe(cfg: dict[str, Any]) -> list[str]:
    try:
        return paper_loop_mod.prune_watchlist_for_trading(
            list(cfg.get("watchlist") or []), focus=str(cfg.get("watchlist_focus") or "liquid")
        )
    except Exception:
        return list(cfg.get("watchlist") or [])


@app.route("/api/brains/scoreboard")
def api_brains_scoreboard():
    return jsonify(brain_scoreboard())


def _research_scope(cfg: dict) -> dict[str, Any]:
    brain = llm_trader.resolve_brain_mode(cfg)
    model = claude_brain.model_name() if brain == "claude" else _llm_cfg_for_calls(cfg).get("model") if brain == "gemini" else brain
    return {"brain": brain, "requested_model": model, "llm_model": model, "prompt_version": llm_trader.PROMPT_VERSION,
            "horizon_min": llm_trader.decision_horizon_min(cfg), "scoring_version": "horizon-net-v2"}


def _loop_trade_thesis(analysis: dict, cfg: dict) -> dict[str, Any]:
    return _research_thesis(analysis, cfg, source="loop")


def _research_thesis(analysis: dict, cfg: dict, *, source: str) -> dict[str, Any]:
    """Shared research/audit record; execution remains in the workspace adapters."""
    if source in ("moss_paper", "moss_notebook"):
        import research_library
        analysis = copy.deepcopy(analysis)
        context = dict(analysis.get("companion_context") or {})
        context["reference_library"] = research_library.context(DATA_DIR, "volume pullback execution spread uncertainty")
        analysis["companion_context"] = context
        if source == "moss_paper":
            import research_studio
            protocol = research_studio.active_protocol(__import__("sys").modules[__name__])
        else:
            protocol = None
    else:
        protocol = None
    started = _now_iso()
    brain = llm_trader.resolve_brain_mode(cfg)
    # mock always available; gemini/jev honor llm_enabled for paid brains
    if brain != "mock" and (
        not cfg.get("llm_enabled", True) or not cfg.get("llm_on_scan", True)
    ):
        return {
            "side": "flat",
            "confidence": 0.0,
            "thesis": "LLM disabled or llm_on_scan off — abstain",
            "error": "llm_disabled",
            "llm_model": None,
            "brain_mode": brain,
        }
    llm_cfg = _llm_cfg_for_calls(cfg)
    analysis = _with_lessons(analysis, cfg)
    analysis = dict(analysis, research_evidence=news_stream.cached_evidence(analysis.get("ticker") or ""))
    desk = dict(cfg)
    desk["api_key"] = llm_cfg.get("api_key")
    # Keep Gemini model name off resolve_brain_mode fallback (brain_mode is authoritative)
    desk["llm_model"] = llm_cfg.get("model")
    desk["configured"] = llm_cfg.get("configured")
    desk["brain_mode"] = brain
    # For trade_thesis_from_analysis which reads cfg["model"] as Gemini model id:
    if brain == "gemini":
        desk["model"] = llm_cfg.get("model")
    else:
        desk.pop("model", None)
    try:
        a2 = analysis
        hz = int(cfg.get("decision_horizon_min") or 20)
        shadow = None
        if cfg.get("claude_shadow") and brain != "claude" and claude_brain.is_configured():
            shadow = _ClaudeShadow(a2, hz)  # runs in parallel with the main brain
        main = llm_trader.decide_trade_thesis(a2, desk, timeout_sec=12)
        if shadow is not None:
            res = shadow.result(timeout=10)
            res = res or {"error": "claude_shadow_timeout"}
            main = dict(main)
            main["shadow_claude"] = {
                    "side": res.get("side"),
                    "confidence": res.get("confidence"),
                    "error": res.get("error"),
                    "model": res.get("llm_model"),
                    "cost_usd": res.get("model_cost_usd"),
                }
    except Exception as exc:  # noqa: BLE001
        main = {
            "side": "flat",
            "confidence": 0.0,
            "thesis": "Brain error — abstain",
            "error": f"brain_error:{type(exc).__name__}",
            "llm_model": llm_cfg.get("model"),
            "brain_mode": brain,
        }
    main = dict(main)
    main.update(prompt_version=llm_trader.PROMPT_VERSION, shadow=None, advisory=None)
    if not main.get("error"):
        side = str(main.get("side") or "flat")
        text = main.get("thesis") or ""
        try:
            if main.get("brain_mode") == "mock":
                main["shadow"] = llm_trader._heuristic_shadow_audit(side, text)
            else:
                main["shadow"] = llm_trader.shadow_audit_decision(side, text, analysis=analysis, cfg=cfg)
        except Exception as exc:
            main["shadow"] = {"coherent": None, "label": "unavailable", "reason": type(exc).__name__}
        try:
            audit_cfg = dict(cfg, brain_mode=main.get("brain_mode") or brain)
            main["advisory"] = llm_trader.run_advisory_panel(side, text, analysis=analysis, cfg=audit_cfg)
        except Exception as exc:
            main["advisory"] = llm_trader.unavailable_advisory("error", type(exc).__name__)
        if llm_trader.shadow_gate_enabled() and main["shadow"].get("coherent") is not True:
            main["execution_block"] = "shadow_not_verified"
        if llm_trader.advisory_soft_size_enabled():
            multiplier = _finite_float(main["advisory"].get("size_mult"), 0) or 0
            main["advisory_size_mult"] = max(0, min(1, multiplier))
            if main["advisory_size_mult"] == 0:
                main["execution_block"] = "advisory_kill_or_unavailable"
    if main.get("execution_block"):
        main["abstain"] = True

    facts = json.loads(llm_trader._analysis_context_blob(analysis))
    scope = _research_scope(cfg)
    quote = analysis.get("quote") or {}
    ev = {"event": "decision", "ts": started, "ticker": analysis.get("ticker"),
          "source": source, "workspace": "research", "execution_attempted": False,
          "brain_mode": main.get("brain_mode") or brain, **scope,
          "llm_model": main.get("llm_model"), "mock": main.get("brain_mode") == "mock",
          "routed": main.get("routed"), "router_reason": main.get("router_reason"),
          "intended_side": main.get("side"), "confidence": main.get("confidence"),
          "thesis": main.get("thesis"), "llm_raw": main.get("llm_raw"),
          "error": main.get("error"), "execution_block": main.get("execution_block"),
          "shadow": main.get("shadow"), "advisory": main.get("advisory"),
          "shadow_claude": main.get("shadow_claude"), "model_cost_usd": main.get("model_cost_usd"),
          "verdict": analysis.get("verdict"), "lateness_label": (analysis.get("entry_quality") or {}).get("label"),
          "mid": quote.get("price") or analysis.get("price"), "quote": copy.deepcopy(quote),
          "slip_bps": float(cfg.get("slip_bps", 5) or 0), "fee_bps": _cfg_fee_bps(cfg),
          "inputs": facts, "input_hash": hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest(),
          "outcome_tolerance_sec": 120, "paper_only": True, "filled": False}
    if quote.get("fresh") and not main.get("error"):
        if protocol:
            ev["research_hypothesis_id"] = protocol["hypothesis_id"]
            ev["research_protocol_id"] = protocol["id"]
        session_track.schedule_decision_outcome(ev)
    else:
        ev["outcome_status"] = "ineligible_error_or_unverified_quote"
    try:
        saved = _decision_ring.append(ev)
        main["decision_record_id"] = saved["id"]
        from desk_operations import EvidenceStore, scope as evidence_scope
        main["evidence_id"] = EvidenceStore(DATA_DIR).put("decision", evidence_scope(saved, "research"), saved["id"], saved)
    except Exception:
        main.update(side="flat", confidence=0, abstain=True, error="research_record_unavailable")
    return main


def _drain_pending_for_autofill(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """When switching to Auto fill: paper-approve pending or expire — no Waiting ghosts.

    Session active → try paper fill (preset brackets). Failures / no session → expire.
    Does not touch live_broker_place_order.
    """
    cfg = dict(cfg or load_config())
    with _lock:
        signals = load_signals()
        pending_ids = [s["id"] for s in signals if s.get("status") == "pending" and signal_workspace(s) == "paper"]
    if not pending_ids:
        return {"drained": 0, "filled": 0, "expired": 0}
    filled = 0
    expired = 0
    session_on = bool(cfg.get("session_active"))
    for sid in pending_ids:
        with _lock:
            signals = load_signals()
            sig = next((s for s in signals if s.get("id") == sid), None)
            if not sig or sig.get("status") != "pending":
                continue
            sig["status"] = "approving"
            for i, s in enumerate(signals):
                if s.get("id") == sid:
                    signals[i] = sig
                    break
            save_signals(signals)
            sig = dict(sig)
        # Strip stale absolute levels — blanks use fill_px + preset geometry
        sig.pop("stop", None)
        sig.pop("target", None)
        ok_fill = False
        if session_on:
            try:
                result = paper_fill(sig, cfg, source="drain_auto_paper")
                ok_fill = bool(result.get("ok"))
            except Exception:
                ok_fill = False
        with _lock:
            signals = load_signals()
            for i, s in enumerate(signals):
                if s.get("id") != sid:
                    continue
                if ok_fill:
                    signals[i] = sig
                    filled += 1
                else:
                    s["status"] = "expired"
                    s["reject_reason"] = "drained_on_autofill"
                    expired += 1
                break
            save_signals(signals)
    append_journal(
        "pending_drained_autofill",
        {"filled": filled, "expired": expired, "total": len(pending_ids)},
    )
    return {"drained": filled + expired, "filled": filled, "expired": expired}


def _cancel_pending_signals_for_ticker(ticker: str, reason: str) -> int:
    """Expire/reject pending queue signals for ticker (stale intent hygiene)."""
    ticker = str(ticker or "").upper()
    if not ticker:
        return 0
    n = 0
    with _lock:
        signals = load_signals()
        for s in signals:
            if s.get("status") != "pending" or signal_workspace(s) != "paper":
                continue
            if str(s.get("ticker") or "").upper() != ticker:
                continue
            s["status"] = "expired"
            s["reject_reason"] = reason
            n += 1
        if n:
            save_signals(signals)
            append_journal(
                "pending_intent_cancelled",
                {"ticker": ticker, "reason": reason, "count": n},
            )
    return n


def _shadow_audit_wrap(side: str, thesis: str, analysis=None, cfg=None):
    return llm_trader.shadow_audit_decision(side, thesis, analysis=analysis, cfg=cfg)


def _paper_loop_deps() -> dict[str, Any]:
    from screener_logic import analyze_ticker

    return {
        "load_config": load_config,
        "append_journal": append_journal,
        "load_ledger": load_ledger,
        "daily_target_progress": daily_target_progress,
        "get_preset": get_preset,
        "execute_loop_decision": execute_loop_decision,
        "analyze_ticker": analyze_ticker,
        "trade_thesis": _loop_trade_thesis,
        "fetch_last_price": fetch_last_price,
        "reverse_paper_fill": reverse_paper_fill,
        "session_pnl_with_mtm": _session_pnl_with_mtm,
        "bleed_status": bleed_status,
        "midday_risk_check": midday_risk_check,
        "shadow_audit": _shadow_audit_wrap,
        "shadow_gate_enabled": llm_trader.shadow_gate_enabled,
        "cancel_pending_signals": _cancel_pending_signals_for_ticker,
        "run_advisory_panel": (
            lambda side, thesis, analysis=None, cfg=None: llm_trader.run_advisory_panel(
                side, thesis, analysis=analysis, cfg=cfg
            )
        ),
        "advisory_soft_size_enabled": llm_trader.advisory_soft_size_enabled,
        "min_decision_confidence": llm_trader.min_decision_confidence,
        "check_paper_exit_intents": check_paper_exit_intents,
        "check_decision_outcomes": check_decision_outcomes,
        "maybe_refresh_equity_curve": maybe_refresh_equity_curve,
        "radar_hot_symbols": market_radar.hot_symbols,
        "radar_maybe_refresh": market_radar.maybe_refresh,
        "schedule_decision_outcome": (
            lambda event: session_track.schedule_decision_outcome(
                event,
                default_horizon_min=int(
                    (load_config() or {}).get("decision_horizon_min") or 20
                ),
            )
        ),
    }


def get_paper_loop() -> paper_loop_mod.PaperLoop:
    global _paper_loop
    if _paper_loop is None:
        _paper_loop = paper_loop_mod.PaperLoop(
            decisions=_decision_ring,
            get_deps=_paper_loop_deps,
        )
    return _paper_loop


def ensure_paper_loop_started() -> None:
    get_paper_loop().start()



# ---------------------------------------------------------------------------
# Background demo signal loop (daemon)
# ---------------------------------------------------------------------------

_bg_stop = threading.Event()


def _scheduled_scan_enabled(cfg: dict[str, Any]) -> bool:
    """Scheduled ideas obey the session clock; explicit research stays available."""
    return bool(cfg.get("session_active") or cfg.get("paper_research_enabled")) and (
        not cfg.get("rth_only", True) or paper_loop_mod.is_rth(datetime.now(timezone.utc))
    )


def _scheduled_scan_config(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Choose one authorized research owner without borrowing the live session."""
    moss_owns_paper = bool((cfg.get("moss_paper") or {}).get("enabled"))
    if cfg.get("session_active") and cfg.get("mode") in ("live_manual", "auto_live"):
        if cfg.get("mode") == "auto_live" or cfg.get("live_agent") is not None:
            return None  # The live agent is the sole scheduled broker owner, even when paused.
        selected = cfg
    elif cfg.get("session_active") and cfg.get("mode") in ("manual", "auto_paper"):
        if moss_owns_paper or cfg.get("loop_enabled"):
            return None
        selected = cfg
    elif cfg.get("paper_research_enabled") and not moss_owns_paper:
        selected = paper_research_config(cfg)
    else:
        return None
    return copy.deepcopy(selected) if _scheduled_scan_enabled(selected) else None


def _bg_loop() -> None:
    """Background watchlist scan loop (volume-screener rules).

    Hold `_lock` only for a brief local snapshot / decision. Run
    generate_scan_signal (yfinance + Gemini) outside the lock so /api/state
    and other routes stay responsive. ingest_signal takes its own lock.
    """
    next_scan_at = 0.0
    while not _bg_stop.wait(timeout=5):
        try:
            _reconcile_pending_broker_orders()
            # A slow provider/model call must not delay the next reconciliation.
            # The agent reserves its single-worker lock before launching.
            _live_agent.schedule_tick()
            cfg = None
            should = False
            with _lock:
                cfg = copy.deepcopy(load_config())
                signals_snap = list(load_signals())
            try:
                signals = expire_stale_signals(signals_snap)
            except Exception:
                signals = signals_snap
            with _lock:
                interval = int(
                    cfg.get("scan_interval_sec")
                    or cfg.get("demo_signal_interval_sec")
                    or 120
                )
                scan_cfg = _scheduled_scan_config(cfg)
                pending = [s for s in signals if _pending_scan_blocks(s, scan_cfg or cfg)]
                # A manual draft or malformed legacy timestamp cannot stop the
                # scheduler or reset its research cadence.
                now = datetime.now(timezone.utc)
                stamps = []
                expected = "live" if (scan_cfg or cfg).get("mode") in ("live_manual", "auto_live") else "paper"
                for signal in signals:
                    if signal.get("source") == "manual_ticket" or signal_workspace(signal) != expected:
                        continue
                    try:
                        stamp = datetime.fromisoformat(str(signal.get("ts") or signal.get("created_at") or "").replace("Z", "+00:00"))
                        if stamp.tzinfo is not None and stamp <= now:
                            stamps.append(stamp)
                    except (ValueError, TypeError):
                        continue
                due = not stamps or (now-max(stamps)).total_seconds() >= interval
                should = scan_cfg is not None and due and len(pending) < 8 and time.monotonic() >= next_scan_at
            # Network I/O must not hold `_lock`
            if cfg is not None and bool(cfg.get("radar_enabled")):
                try:
                    market_radar.maybe_refresh(cfg)
                except Exception:
                    pass
            if should and scan_cfg is not None:
                # Empty results and provider failures also consume a cycle.
                next_scan_at = time.monotonic() + max(5, interval)
                def still_authorized():
                    return _scheduled_scan_config(load_config()) == scan_cfg
                sig = generate_scan_signal(scan_cfg, force=False, still_authorized=still_authorized)
                # Stop, account, mode and other settings changes invalidate work
                # collected under the previous policy.
                if sig and still_authorized():
                    if scan_cfg.get("_paper_research"):
                        sig.update(workspace="paper", paper_research=True)
                    ingest_signal(sig, expected_config=cfg)
        except Exception as exc:  # noqa: BLE001
            try:
                append_journal("bg_error", {"error": str(exc)})
            except Exception:
                app.logger.exception("Background cycle failed and its journal could not be written")


_bg_thread: threading.Thread | None = None
_worker_start_lock = threading.RLock()

_INSTANCE_LOCK_PATH = Path(os.environ.get("TOMAHAWK_INSTANCE_LOCK", str(DATA_DIR / "tomahawk.pid")))
_INSTANCE_LOCK_FD: int | None = None


def _instance_pid_is_running(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return pid == os.getpid()
    if os.name == "nt":
        # os.kill(pid, 0) uses TerminateProcess on Windows; it is not a probe.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            return ctypes.get_last_error() != 87  # invalid PID; access denied stays live
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_instance_lock() -> None:
    """Prevent accidental double-starts that would corrupt JSON read-modify-write state."""
    global _INSTANCE_LOCK_FD
    if os.environ.get("TOMAHAWK_ALLOW_MULTI_INSTANCE", "").lower() in ("1", "true", "yes"):
        return
    _INSTANCE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _INSTANCE_LOCK_FD = os.open(str(_INSTANCE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(_INSTANCE_LOCK_FD, str(os.getpid()).encode("ascii"))
    except FileExistsError as exc:
        try:
            previous_pid = int(_INSTANCE_LOCK_PATH.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            previous_pid = 0
        if previous_pid > 0 and not _instance_pid_is_running(previous_pid):
            try:
                _INSTANCE_LOCK_PATH.unlink()
                _INSTANCE_LOCK_FD = os.open(
                    str(_INSTANCE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(_INSTANCE_LOCK_FD, str(os.getpid()).encode("ascii"))
                return
            except (FileExistsError, OSError):
                if _INSTANCE_LOCK_FD is not None:
                    os.close(_INSTANCE_LOCK_FD)
                    _INSTANCE_LOCK_FD = None
        raise RuntimeError(
            f"Another Tomahawk instance appears to be running ({_INSTANCE_LOCK_PATH}). "
            "Remove the lock only after confirming the previous process stopped."
        ) from exc


def release_instance_lock() -> None:
    global _INSTANCE_LOCK_FD
    if _INSTANCE_LOCK_FD is not None:
        os.close(_INSTANCE_LOCK_FD)
        _INSTANCE_LOCK_FD = None
        try:
            _INSTANCE_LOCK_PATH.unlink()
        except OSError:
            pass


def _start_scan_worker() -> None:
    global _bg_thread
    with _worker_start_lock:
        if not _bg_stop.is_set() and not (_bg_thread and _bg_thread.is_alive()):
            _bg_thread = threading.Thread(target=_bg_loop, name="signal-scan", daemon=True)
            _bg_thread.start()


def start_bg() -> None:
    _start_scan_worker()
    ensure_paper_loop_started()




def _screener_citations(analysis: dict | None) -> list[dict[str, Any]]:
    """Structured screener facts for thesis/chat UI (delegates to llm_trader)."""
    try:
        import llm_trader as _lt
        return list(_lt.screener_citations(analysis) or [])
    except Exception:
        return []


def _edge_sample_stats(limit: int = 40) -> dict[str, Any]:
    """Light sample stats from recent decisions — not proof of edge."""
    try:
        rows = list(_decision_ring.latest(limit) or [])
    except Exception:
        rows = []
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "win_rate": None,
            "hold_rate": None,
            "abstain_pct": None,
            "pass_rate": None,
            "label": "sample only — not proof of edge",
        }
    holds = 0
    abstains = 0
    passes = 0
    actionable = 0
    wins = 0
    for r in rows:
        side = (r.get("decision") or r.get("side") or "hold").lower()
        verdict = (r.get("verdict") or "").upper()
        if verdict == "PASS":
            passes += 1
        if r.get("abstain") or side in ("hold", "flat") or r.get("hold"):
            holds += 1
            abstains += 1
            continue
        actionable += 1
        pnl = r.get("pnl_delta")
        if pnl is None:
            pnl = r.get("realized_pnl")
        if pnl is not None:
            try:
                if float(pnl) > 0:
                    wins += 1
            except (TypeError, ValueError):
                pass
    return {
        "n": n,
        "win_rate": round(wins / actionable, 3) if actionable else None,
        "hold_rate": round(holds / n, 3),
        "abstain_pct": round(100.0 * abstains / n, 1),
        "pass_rate": round(passes / n, 3),
        "actionable": actionable,
        "wins": wins,
        "label": "sample only — not proof of edge",
    }


def _execution_realism(ledger: dict[str, Any], limit: int = 200) -> dict[str, Any]:
        """Summarize simulated/broker execution friction without implying edge."""
        fills = (
            list(ledger.get("fills") or [])
            + list(ledger.get("broker_fills") or [])
            + list(ledger.get("broker_fills_archive") or [])
        )[:limit]
        if not fills:
            return {"n": 0, "avg_slippage_bps": None, "total_slippage_usd": 0.0,
                    "total_fees_usd": 0.0, "broker_fills": 0, "paper_fills": 0}
        slip_bps = []
        total_slip = total_fee = 0.0
        broker = paper = 0
        for fill in fills:
            try:
                if fill.get("slip_bps") is not None:
                    slip_bps.append(abs(float(fill["slip_bps"])))
                total_slip += float(fill.get("slip_usd") or 0)
                total_fee += float(fill.get("fee_usd") or 0)
            except (TypeError, ValueError):
                continue
            if fill.get("simulated") or fill.get("fill_style") == "mid_or_taker":
                paper += 1
            elif fill.get("broker"):
                broker += 1
        return {
            "n": len(fills),
            "avg_slippage_bps": round(sum(slip_bps) / len(slip_bps), 3) if slip_bps else None,
            "total_slippage_usd": round(total_slip, 4),
            "total_fees_usd": round(total_fee, 4),
            "broker_fills": broker,
            "paper_fills": paper,
            "sample_only": True,
        }


def _risk_cockpit(cfg: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
        daily = daily_target_progress(cfg, ledger)
        positions = [p for p in (ledger.get("positions") or []) if (_finite_float(p.get("shares"), 0) or 0) > 0]
        exposure = 0.0
        for p in positions:
            exposure += abs((_finite_float(p.get("shares"), 0) or 0) * (_finite_float(p.get("avg_price"), 0) or 0))
        ks = cfg.get("kill_switch") or {}
        equity = _finite_float(cfg.get("paper_equity"), 100000.0) or 100000.0
        test_notional = equity * float(get_preset(cfg.get("risk_preset")).get("max_position_pct") or 0) / 100.0
        allowed, reason = can_take_trade(cfg, ledger, max(test_notional, 0.01)) if cfg.get("session_active") else (False, "session_not_active")
        broker_mode = cfg.get("mode") in ("auto_live", "live_manual")
        # General local limits cannot authorize an unspecified broker order.
        # The real gate checks the exact order and broker account at submission.
        broker_status = _broker_public_status()
        permission = "Paper limits clear" if allowed else "Blocked"
        if broker_mode:
            permission = "Unverified" if broker_status.get("paper_mode") is None or not broker_status.get("configured") else "Order checks required"
            allowed = False
            reason = "Verify broker account, holdings, working orders and the exact order before submission"
        return {
            "session_active": bool(cfg.get("session_active")),
            "mode": cfg.get("mode"),
            "trade_allowed": allowed,
            "trade_gate_reason": reason,
            "permission_label": permission,
            "scope": "broker" if broker_mode else "local_paper",
            "test_order_notional_usd": round(test_notional, 2),
            "open_positions": None if broker_mode else len(positions),
            "exposure_usd": None if broker_mode else round(exposure, 2),
            "daily_pnl_usd": None if broker_mode else round(_finite_float(daily.get("pnl"), 0) or 0, 2),
            "max_session_loss_usd": cfg.get("max_session_loss_usd"),
            "kill_switch_armed": bool(ks.get("armed")),
            "broker_position_check": "order_check_required" if broker_status.get("configured") else "not_configured",
            "data_files_healthy": not bool(_CORRUPT_PATHS),
        }


def _promotion_gate(cfg: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
        """Conservative paper-to-live checklist; this never enables live mode."""
        window_days = int(_finite_float(cfg.get("promotion_window_days"), 30) or 30)
        cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
        fills = []
        all_fills = (
            list(ledger.get("fills") or [])
            + list(ledger.get("fills_archive") or [])
            + list(ledger.get("broker_fills") or [])
            + list(ledger.get("broker_fills_archive") or [])
        )
        for fill in all_fills:
            try:
                ts = datetime.fromisoformat(str(fill.get("ts") or "").replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OSError):
                continue
            if ts >= cutoff:
                fills.append(fill)
        grouped: dict[str, float] = {}
        for fill in fills:
            key = str(fill.get("position_id") or fill.get("signal_id") or fill.get("id") or "")
            if key:
                pnl_value = _finite_float(fill.get("realized_pnl"), 0.0) or 0.0
                fee_value = _finite_float(fill.get("fee_usd"), 0.0) or 0.0
                grouped[key] = grouped.get(key, 0.0) + pnl_value - fee_value
        pnl = list(grouped.values())
        independent_tickers = {
            str(fill.get("ticker") or "").upper()
            for fill in fills
            if str(fill.get("ticker") or "").strip()
        }
        closed = [{"realized_pnl": value} for value in pnl]
        wins = [f for f in closed if (_finite_float(f.get("realized_pnl"), 0) or 0) > 0]
        running = peak = drawdown = 0.0
        for value in pnl:
            running += value
            peak = max(peak, running)
            drawdown = max(drawdown, peak - running)
        equity = max(_finite_float(cfg.get("paper_equity"), 1.0) or 1.0, 1.0)
        n = len(closed)
        win_rate = (len(wins) / n) if n else None
        gross_wins = sum(value for value in pnl if value > 0)
        gross_losses = -sum(value for value in pnl if value < 0)
        expectancy = (sum(pnl) / n) if n else None
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else None
        pf_threshold = float(cfg.get("promotion_min_profit_factor") or 1.05)
        checks = {
            "minimum_samples": n >= int(cfg.get("promotion_min_samples") or 30),
            "minimum_independent_tickers": len(independent_tickers) >= int(
                cfg.get("promotion_min_independent_tickers") or 3
            ),
            "minimum_win_rate": win_rate is not None and win_rate >= float(cfg.get("promotion_min_win_rate") or 0.52),
            "positive_expectancy": expectancy is not None and expectancy > float(cfg.get("promotion_min_expectancy_usd") or 0.0),
            "profit_factor": (
                (profit_factor is not None and profit_factor >= pf_threshold)
                or (gross_wins > 0 and gross_losses == 0)
            ),
            "maximum_drawdown": (drawdown / equity * 100) <= float(cfg.get("promotion_max_drawdown_pct") or 5.0),
            "data_healthy": not bool(_CORRUPT_PATHS),
        }
        return {
            "eligible": all(checks.values()),
            "checks": checks,
            "samples": n,
            "independent_tickers": len(independent_tickers),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "expectancy_usd": round(expectancy, 4) if expectancy is not None else None,
            "profit_factor": round(profit_factor, 4) if profit_factor is not None else ("infinite" if gross_wins > 0 else None),
            "drawdown_usd": round(drawdown, 2),
            "drawdown_pct": round(drawdown / equity * 100, 3),
            "window_days": window_days,
            "note": "Evidence gate only; it never changes broker mode or places orders.",
        }


def readiness_summary(
    cfg: dict[str, Any],
    ledger: dict[str, Any],
    *,
    loop: dict[str, Any] | None = None,
    promotion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One conservative summary of what the desk is currently ready to do."""
    loop = loop or {}
    promotion = promotion or _promotion_gate(cfg, ledger)
    blockers: list[str] = []
    cautions: list[str] = []
    if cfg.get("mode") in ("auto_live", "live_manual"):
        cautions.append("live broker mode requires explicit per-order approval")
    if not bool(cfg.get("session_active")):
        blockers.append("session inactive")
    if loop.get("automation_health", {}).get("error_streak", 0):
        blockers.append("automation errors need review")
    if not bool(_risk_cockpit(cfg, ledger).get("data_files_healthy")):
        blockers.append("data files need repair")
    if not promotion.get("eligible"):
        cautions.append("promotion evidence is not complete")
    if cfg.get("mode") == "auto_live":
        blockers.append("automatic live mode is not enabled by this readiness summary")
    if blockers:
        status = "blocked"
    elif cfg.get("mode") == "auto_paper" and cfg.get("session_active"):
        status = "paper_ready"
    else:
        status = "research_only"
    return {
        "status": status,
        "can_research": True,
        "can_paper_trade": status == "paper_ready",
        "can_live_trade": False,
        "blockers": blockers,
        "cautions": cautions,
        "priority": "resolve_blockers" if blockers else "review_high_priority_opportunities",
        "note": "Readiness is conservative and descriptive; it never changes mode or places orders.",
    }
def _ranked_opportunities(
    signals: list[dict],
    decisions: list[dict] | None = None,
    buzz: dict | None = None,
) -> list[dict]:
    """Pending + recent candidates ranked by one auditable priority policy."""
    rank = {"PASS": 0, "WATCH": 1, "AVOID": 2}

    def priority(row: dict[str, Any], buzz_mentions: Any) -> tuple[float, list[str]]:
        verdict = str(row.get("verdict") or "WATCH").upper()
        try:
            confidence = max(0.0, min(1.0, float(row.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        score = {"PASS": 60.0, "WATCH": 35.0, "AVOID": 10.0}.get(verdict, 0.0)
        reasons = [f"verdict:{verdict.lower()}"]
        score += confidence * 25.0
        if confidence >= 0.75:
            reasons.append("high_confidence")
        lateness = str(row.get("lateness_label") or "").lower()
        if lateness in ("late", "chasing"):
            score -= 25.0
            reasons.append("late_entry")
        flags = str(row.get("research_flag") or "") + " " + str(row.get("research_flags") or "")
        for flag in ("execution_quality_block", "weak_market_regime", "sector_dying"):
            if flag in flags:
                score -= 20.0
                reasons.append(flag)
        try:
            attention = float(buzz_mentions or 0)
        except (TypeError, ValueError):
            attention = 0.0
        if attention > 0:
            score += min(8.0, attention / 10.0)
            reasons.append("attention_context")
        if row.get("source") == "pending":
            score += 3.0
            reasons.append("needs_review")
        return round(score, 2), reasons
    out: list[dict] = []
    for s in signals or []:
        if s.get("status") != "pending":
            continue
        v = (s.get("verdict") or "WATCH").upper()
        bm = buzz_sources.buzz_mentions_for_ticker(str(s.get("ticker") or ""), buzz)
        view = _signal_ui_projection(s)
        row = {
            "id": s.get("id"),
            "workspace": view["workspace"],
            "source_signal_id": s.get("source_signal_id"),
            "ticker": s.get("ticker"),
            "side": view.get("side"),
            "verdict": v,
            "confidence": s.get("confidence"),
            "lateness_label": s.get("lateness_label") or (s.get("entry_quality") or {}).get("label"),
            "thesis": (s.get("llm_thesis") or s.get("reason") or "")[:180],
            "suggested_shares": s.get("suggested_shares"),
            "signal_price": s.get("signal_price"),
            "source": "pending",
            "research_flag": s.get("research_flag"),
            "research_flags": s.get("research_flags") or [],
            "actionable": view.get("actionable"),
            "execution_block": view.get("execution_block"),
            "llm_model": s.get("llm_model"),
            "brain_mode": s.get("brain_mode"),
            "routed": s.get("routed"),
            "quote": view.get("quote"),
            "ts": s.get("ts"),
            "expires_at": s.get("expires_at"),
            "gap_pct": s.get("gap_pct"),
        }
        if bm:
            row["buzz_mentions"] = bm
        row["priority_score"], row["priority_reasons"] = priority(row, bm)
        row["priority_tier"] = "act_now" if row["priority_score"] >= 70 else "review" if row["priority_score"] >= 35 else "research"
        out.append(row)
    for d in decisions or []:
        if d.get("event") != "decision":
            continue
        if d.get("filled"):
            continue
        v = (d.get("verdict") or "WATCH").upper()
        bm = buzz_sources.buzz_mentions_for_ticker(str(d.get("ticker") or ""), buzz)
        row = {
            "id": f"loop-{d.get('ts')}-{d.get('ticker')}",
            "workspace": "paper",
            "ticker": d.get("ticker"),
            "side": d.get("intended_side") or d.get("side") or "hold",
            "verdict": v,
            "confidence": d.get("confidence"),
            "lateness_label": d.get("lateness_label"),
            "thesis": (d.get("thesis") or "")[:180],
            "suggested_shares": None,
            "signal_price": d.get("mid"),
            "source": "loop",
            "abstain": bool(d.get("abstain") or d.get("hold")),
            "research_flag": d.get("research_flag"),
            "gap_pct": d.get("gap_pct"),
        }
        if bm:
            row["buzz_mentions"] = bm
        row["priority_score"], row["priority_reasons"] = priority(row, bm)
        row["priority_tier"] = "act_now" if row["priority_score"] >= 70 else "review" if row["priority_score"] >= 35 else "research"
        out.append(row)
    out.sort(key=lambda x: (
        -float(x.get("priority_score") or 0),
        rank.get(str(x.get("verdict") or "").upper(), 9),
        -float(x.get("confidence") or 0),
    ))
    return out[:40]


def paper_flatten_all(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Close local paper positions only; broker orders and records are out of scope."""
    cfg = paper_research_config(dict(cfg or load_config()))
    with _lock:
        ledger = load_ledger()
        positions = list(ledger.get("positions") or [])
    closed: list[dict[str, Any]] = []
    for pos in positions:
        ticker = pos.get("ticker")
        shares = float(pos.get("shares") or 0)
        side = (pos.get("side") or "long").lower()
        if not ticker or shares <= 0:
            continue
        px = fetch_last_price(ticker) or float(pos.get("avg_price") or 0)
        if not px or px <= 0:
            closed.append({"ticker": ticker, "ok": False, "error": "no_price"})
            continue
        opp_side = "sell" if side in ("long", "buy") else "buy"
        sig = {
            "id": str(__import__("uuid").uuid4()),
            "ticker": ticker,
            "side": opp_side,
            "signal_price": float(px),
            "suggested_shares": shares,
            "status": "pending",
            "workspace": "paper",
            "mode_at_create": cfg["mode"],
            "confidence": 1.0,
            "verdict": "WATCH",
            "reason": "force_flatten",
        }
        cfg2 = dict(cfg)
        cfg2["rth_only"] = False
        # Force flatten bypasses max-loss/target/kill/session gates for local paper
        result = paper_fill(sig, cfg2, source="force_flatten", bypass_gates=True, allow_demo_price=True)
        closed.append(
            {
                "ticker": ticker,
                "shares": shares,
                "side": side,
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "fill": result.get("fill"),
            }
        )

    broker_flat = {
        "ok": True, "attempted": False, "status": "out_of_scope",
        "message": "Paper flatten does not cancel or close broker orders or positions.",
    }
    local_ok = all(c.get("ok") for c in closed) if closed else True
    out = {
        "ok": local_ok,
        "workspace": "paper",
        "closed": closed,
        "count": len(closed),
        "local_ok": local_ok,
        "broker_flatten": broker_flat,
        "flatten_complete": local_ok,
    }
    if not local_ok:
        out["error"] = "Some local paper positions could not be closed."
    append_journal(
        "force_flatten",
        {
            "workspace": "paper",
            "closed": closed,
            "count": len(closed),
            "broker_flatten": broker_flat,
            "flatten_complete": local_ok,
        },
    )
    return out




def _open_position_summary(ledger: dict[str, Any]) -> dict[str, Any]:
    positions = list((ledger or {}).get("positions") or [])
    if not positions:
        return {"count": 0, "flat": True, "items": []}
    items = []
    for p in positions[:5]:
        item = {
            "ticker": p.get("ticker"),
            "side": p.get("side"),
            "shares": p.get("shares"),
            "avg_price": p.get("avg_price"),
        }
        if p.get("stop_price") is not None:
            item["stop_price"] = p.get("stop_price")
        if p.get("take_profit_price") is not None:
            item["take_profit_price"] = p.get("take_profit_price")
        if p.get("exit_bracket"):
            item["exit_bracket"] = True
        items.append(item)
    return {
        "count": len(positions),
        "flat": False,
        "items": items,
        "truncated": len(positions) > len(items),
    }


def _buzz_lite(buzz_sum: dict[str, Any] | None, *, heat_enabled: bool = True) -> dict[str, Any]:
    b = buzz_sum or {}
    top = (b.get("top") or [])[:5]
    hits = b.get("watchlist_hits") or []
    heat = (b.get("heat") or []) if heat_enabled else []
    return {
        "watchlist_hits": hits[:8],
        "top": top,
        "auth_mode": b.get("auth_mode"),
        "heat": heat[:12],
        "stale": bool(b.get("stale")),
        "cached_at": b.get("cached_at"),
    }


def _latest_desk_call(cfg: dict, signals: list, loop: dict) -> dict | None:
    """Use the active research path, retaining old calls only as dated history."""
    workspace = "live" if cfg.get("mode") in ("live_manual", "auto_live") else "paper"
    scanner = workspace == "live" or cfg.get("_paper_research")
    raw = next((s for s in signals if s.get("ticker") and signal_workspace(s) == workspace), None) if scanner else loop.get("last_decision")
    if not raw:
        return None
    call = dict(raw)
    if scanner:
        call.update(_signal_ui_projection(raw, cfg))
        call.update(event="decision", thesis=raw.get("llm_thesis") or raw.get("reason"),
                    decision="hold", intended_side=call.get("side"), filled=bool(raw.get("fill")))
    now = datetime.now(timezone.utc)
    try:
        stamp = datetime.fromisoformat(str(call.get("ts")).replace("Z", "+00:00"))
        age = max(0, (now - stamp).total_seconds())
    except (ValueError, TypeError):
        age = None
    stale = age is None or age > int(cfg.get("signal_ttl_sec") or 900)
    call.update(workspace=workspace, call_source=("Paper research" if cfg.get("_paper_research") else "Research scanner") if scanner else "Paper loop", age_sec=age, stale=stale)
    if stale:
        call.update(intended_side="hold", side="hold", decision="hold", confidence=0,
                    probs={"buy": 0, "sell": 0, "flat": 1}, abstain=True, filled=False)
    return call


def _startup_status() -> dict:
    try:
        status = json.loads((DATA_DIR / "launcher-status.json").read_text(encoding="utf-8-sig"))
        out = {k: status.get(k) for k in ("checked_at", "gateway", "message")}
    except (OSError, ValueError, AttributeError):
        out = {}
    try:
        code = code_version.status()
        out.update(code_stale=code["stale"], code_message=code["message"])
    except Exception:  # noqa: BLE001 - never block state on a file scan
        pass
    return out


def _build_state_lite() -> dict[str, Any]:
    """Lightweight snapshot for SSE — cache-only buzz, no Reddit/yfinance.

    Short lock for config/signals/ledger snapshot only. expire_stale off hot path.
    Never jsonify under lock.
    """
    with _lock:
        cfg = dict(load_config())
        signals = list(load_signals())
        ledger = dict(load_ledger())
        watchlist = list(cfg.get("watchlist") or DEFAULT_WATCHLIST)
        focus_liquid = str(cfg.get("watchlist_focus") or "liquid").lower() == "liquid"
        heat_on = bool(cfg.get("heat_enabled", True))
        session_active = bool(cfg.get("session_active"))

    # DecisionRing + expire + derived outside app _lock (no heavy disk under lock)
    try:
        signals = expire_stale_signals(signals)
    except Exception:
        pass
    loop_st = get_paper_loop().status(cfg)
    decisions_preview = _decision_ring.latest(15)
    pending = [s for s in signals if s.get("status") == "pending"]
    pending_count = sum(not signal_execution_block(s, broker=cfg.get("mode") in ("live_manual", "auto_live")) for s in pending)
    daily = daily_target_progress(cfg, ledger)
    open_pos = _open_position_summary(ledger)
    last_dec = None
    for d in decisions_preview:
        if d.get("event") in ("decision", "intent") and d.get("ticker"):
            last_dec = {
                "ticker": d.get("ticker"),
                "decision": d.get("decision") or d.get("side"),
                "confidence": d.get("confidence"),
                "verdict": d.get("verdict"),
                "ts": d.get("ts"),
                "seq": d.get("seq"),
            }
            break
    signals_for_opp = list(signals)

    buzz_sum = buzz_sources.buzz_summary_for_state(watchlist, focus_liquid=focus_liquid)
    if not heat_on:
        buzz_sum = dict(buzz_sum)
        buzz_sum["heat"] = []
    buzz_cache = buzz_sources.get_cached_buzz()
    opps = _ranked_opportunities(signals_for_opp, decisions_preview, buzz_cache)
    signal_bags = {status: [_signal_ui_projection(s, cfg) for s in signals if s.get("status", "pending") == status][:120]
                   for status in ("pending", "approving", "broker_pending", "approved", "rejected", "expired")}
    payload = {
        "ok": True,
        "sim": True,
        "dry_run": True,
        "ts": _now_iso(),
        "loop": loop_st,
        "desk_call": _latest_desk_call(cfg, signals, loop_st),
        "paper_desk_call": _latest_desk_call(paper_research_config(cfg), signals, loop_st),
        "paper_daily": daily_target_progress(paper_research_config(cfg), ledger),
        "paper_daily_recap": daily_recap(paper_research_config(cfg), ledger),
        "config": {key: cfg.get(key) for key in ("mode", "session_active", "paper_research_enabled", "paper_auto_approve", "paper_risk_preset", "risk_preset", "kill_switch", "rth_only", "broker_identity")},
        "signals": signal_bags,
        "broker_ledger": {"broker_fills": (ledger.get("broker_fills") or [])[:100], "pending_broker_orders": ledger.get("pending_broker_orders") or []},
        "paper_ledger": {key: ledger.get(key) for key in ("positions", "fills", "cash", "equity", "daily")},
        "startup": _startup_status(),
        "session_active": session_active,
        "daily": daily,
        "daily_recap": daily_recap(cfg, ledger),
        "pace": daily.get("pace") or {},
        "open_position": open_pos,
        "buzz": _buzz_lite(buzz_sum, heat_enabled=heat_on),
        "heat": (buzz_sum.get("heat") or []) if heat_on else [],
        "pending_count": pending_count,
        "opportunities": opps,
        "last_decision": last_dec,
        "heat_enabled": heat_on,
        "session_totals": loop_st.get("session_totals") or {},
        "brain_mode": cfg.get("brain_mode") or "gemini",
        # Friction knobs for UI sync (full config stays on /api/state)
        "slip_bps": float(cfg.get("slip_bps", 5) or 0),
        "fee_bps": float(cfg.get("fee_bps", 1.0) or 0),
        # Intentionally omit watchlist — lite must not imply empty list to UI
    }
    try:
        payload.update(_money_snapshot(cfg))
    except Exception:
        pass
    payload["corrupt_files"] = corrupt_files_status()
    payload["broker"] = _broker_public_status()
    # Cache-only on lite path (bg_loop /api/radar refresh the cache)
    try:
        payload["radar"] = market_radar.public_status(cfg)
        payload["radar_enabled"] = bool(payload["radar"].get("enabled"))
    except Exception:
        payload["radar"] = {
            "enabled": bool(cfg.get("radar_enabled")),
            "count": 0,
            "movers": [],
            "llm": False,
            "sim": True,
        }
        payload["radar_enabled"] = bool(cfg.get("radar_enabled"))
    try:
        maybe_refresh_equity_curve(cfg)
        with _lock:
            led2 = load_ledger()
        payload["scoreboard"] = _day_scoreboard(led2, cfg)
        payload["equity_curve"] = list((payload["scoreboard"] or {}).get("equity_curve") or [])
    except Exception:
        payload["scoreboard"] = {
            "equity_curve": [],
            "max_drawdown": 0,
            "fills_count": 0,
            "butler": "Quiet so far — watching the tape for you",
        }
        payload["equity_curve"] = []
    try:
        llm_st = _llm_public_status(cfg)
        totals = payload["session_totals"] or {}
        # Session brain $ (resets on START). Day rollup stays on model_cost.
        model_usd = float(totals.get("model_usd") or 0)
        payload["model_cost"] = llm_st.get("model_cost") or {}
        payload["ledger_brain"] = {
            "model_usd": round(model_usd, 4),
            "paper_pnl": float(totals.get("paper_pnl") or daily.get("pnl") or 0),
            "friction_usd": float(totals.get("friction_usd") or 0),
            "late_blocks": int(totals.get("late_blocks") or 0),
        }
    except Exception:
        payload["ledger_brain"] = {
            "model_usd": 0.0,
            "paper_pnl": float(daily.get("pnl") or 0),
            "friction_usd": 0.0,
            "late_blocks": 0,
        }
    # API pack lite: configured map + alert queue only (no outbound HTTP)
    try:
        payload["api_pack"] = api_providers.configured_map()
    except Exception:
        payload["api_pack"] = {}
    try:
        payload["alerts"] = desk_alerts.drain_for_state(8)
    except Exception:
        payload["alerts"] = {"items": []}
    return payload


_spark_cache: dict[str, Any] = {"at": 0.0, "by_ticker": {}}
_SPARK_TTL_SEC = 90
_SPARK_LOCK = threading.Lock()


def _sparks_for_tickers(tickers: list[str]) -> dict[str, Any]:
    """Intraday closes for up to 6 tickers. Cache 60–120s. Fail soft. No _lock across net."""
    import time as _time
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        t = str(raw or "").strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        cleaned.append(t)
        if len(cleaned) >= 6:
            break
    now = _time.time()
    out: dict[str, list] = {}
    need: list[str] = []
    with _SPARK_LOCK:
        by = _spark_cache.get("by_ticker") or {}
        for t in cleaned:
            entry = by.get(t)
            if entry and (now - float(entry.get("at") or 0)) < _SPARK_TTL_SEC:
                out[t] = list(entry.get("closes") or [])
            else:
                need.append(t)
    for t in need:
        closes: list = []
        try:
            import data_sources as _ds
            closes = _ds.yahoo_chart_intraday(t) or []
        except Exception:
            closes = []
        # downsample to ~40 points for SVG
        if len(closes) > 48:
            step = max(1, len(closes) // 40)
            closes = closes[::step][:40]
        with _SPARK_LOCK:
            _spark_cache.setdefault("by_ticker", {})[t] = {"at": now, "closes": closes}
        out[t] = closes
    return {"ok": True, "sparks": out, "ttl_sec": _SPARK_TTL_SEC}


_STATE_AUX_LOCK = threading.Lock()
_STATE_AUX: dict[str, Any] = {"key": None, "data_key": None, "at": 0.0, "data": None, "refreshing": False}
_STATE_AUX_TTL = 30.0


def _state_aux_key(cfg, watchlist, focus):
    return (tuple(watchlist), focus, bool(cfg.get("macro_gates_enabled", True)), bool(cfg.get("social_enabled")))


def _refresh_state_aux(cfg: dict[str, Any], watchlist: list[str], focus: str | None) -> None:
    """Refresh slow informational providers without blocking the primary state route."""
    try:
        providers = api_providers.public_pack_status()
    except Exception as exc:  # noqa: BLE001
        providers = {"configured": {}, "error": str(exc)[:120]}
    try:
        watchlist_news = news_stream.watchlist_news(watchlist, per_symbol=3, max_total=18)
    except Exception as exc:  # noqa: BLE001
        watchlist_news = {"ok": False, "items": [], "error": str(exc)[:120]}
    try:
        social = social_intelligence.snapshot(watchlist) if cfg.get("social_enabled") else {
            "ok": False, "enabled": False, "pulse": [], "items": [], "display_only": True,
        }
    except Exception as exc:  # noqa: BLE001
        social = {"ok": False, "enabled": bool(cfg.get("social_enabled")), "pulse": [], "items": [], "error": str(exc)[:120], "display_only": True}
    try:
        edgar = edgar_client.recent_filings(focus, limit=6) if focus else {"ok": False, "filings": [], "status": "no_focus"}
    except Exception as exc:  # noqa: BLE001
        edgar = {"ok": False, "filings": [], "error": str(exc)[:120]}
    try:
        options = options_flow.flow_for_ticker(focus) if focus else {"ok": False, "status": "no_focus", "auto_trade": False}
    except Exception as exc:  # noqa: BLE001
        options = {"ok": False, "error": str(exc)[:120], "auto_trade": False}
    try:
        macro = {
            "calendar": macro_calendar.calendar_snapshot(),
            "risk": macro_calendar.risk_adjustment(focus, cfg),
            "gates_enabled": bool(cfg.get("macro_gates_enabled", True)),
        }
    except Exception as exc:  # noqa: BLE001
        macro = {"error": str(exc)[:120]}
    try:
        alerts = desk_alerts.drain_for_state(12)
    except Exception as exc:  # noqa: BLE001
        alerts = {"items": [], "error": str(exc)[:120]}
    try:
        intelligence = news_intelligence.analyze_items(watchlist_news.get("items") or [])
        research_context = {
            "ticker": focus,
            "timeline": news_intelligence.build_timeline(
                watchlist_news.get("items") or [],
                (edgar or {}).get("filings") or [],
                ((macro or {}).get("calendar") or {}).get("release", {}).get("flags") or [],
            )[:40],
            "analysis": intelligence,
            "digest": intelligence.get("digest") or [],
            "alerts": intelligence.get("alerts") or [],
            "provider_reliability": news_intelligence.provider_reliability(),
            "display_only": True,
            "social": social,
        }
    except Exception as exc:  # noqa: BLE001
        research_context = {"ticker": focus, "timeline": [], "error": str(exc)[:120], "display_only": True}
    data = {
        "providers": providers,
        "api_pack": (providers or {}).get("configured") or {},
        "watchlist_news": watchlist_news,
        "edgar": edgar,
        "options_flow": options,
        "macro": macro,
        "alerts": alerts,
        "research_context": research_context,
        "social": social,
    }
    with _STATE_AUX_LOCK:
        _STATE_AUX.update(data=data, data_key=_state_aux_key(cfg, watchlist, focus),
                          refreshing=False, at=time.time())


def _refresh_state_aux_safe(cfg: dict[str, Any], watchlist: list[str], focus: str | None) -> None:
    try:
        _refresh_state_aux(cfg, watchlist, focus)
    except Exception as exc:  # noqa: BLE001
        with _STATE_AUX_LOCK:
            _STATE_AUX["refreshing"] = False
            _STATE_AUX["data_key"] = _state_aux_key(cfg, watchlist, focus)
            _STATE_AUX["data"] = {
                "providers": {"configured": {}, "error": str(exc)[:120]},
                "api_pack": {},
                "watchlist_news": {"ok": False, "items": [], "error": str(exc)[:120]},
                "edgar": {"ok": False, "filings": [], "error": str(exc)[:120]},
                "options_flow": {"ok": False, "error": str(exc)[:120], "auto_trade": False},
                "macro": {"error": str(exc)[:120]},
                "alerts": {"items": [], "error": str(exc)[:120]},
                "research_context": {"timeline": [], "error": str(exc)[:120], "display_only": True},
                "social": {"ok": False, "pulse": [], "items": [], "display_only": True},
            }


def _state_aux_snapshot(cfg: dict[str, Any], watchlist: list[str], focus: str | None) -> dict[str, Any]:
    key = _state_aux_key(cfg, watchlist, focus)
    now = __import__("time").time()
    with _STATE_AUX_LOCK:
        fresh = _STATE_AUX.get("data_key") == key and now - float(_STATE_AUX.get("at") or 0) < _STATE_AUX_TTL
        if not fresh and not _STATE_AUX.get("refreshing"):
            _STATE_AUX["key"] = key
            _STATE_AUX["refreshing"] = True
            try:
                threading.Thread(
                    target=_refresh_state_aux_safe,
                    args=(dict(cfg), list(watchlist), focus),
                    daemon=True,
                    name="state-aux-refresh",
                ).start()
            except Exception:
                # Optional context cannot take /api/state down or retain a
                # phantom worker. Preserve the cache and let the next poll retry.
                _STATE_AUX["refreshing"] = False
        cached = (_STATE_AUX.get("data") or {}) if _STATE_AUX.get("data_key") == key else {}
    return dict(cached)


@app.route("/")
def index():
    return _desk_page(
        "overview",
        "live",
        "Overview",
        "Start here — market pulse and account snapshot. Auto, Paper, and Research are separate pages.",
    )


# --- PAGE_SPLIT_DESK_ROUTES ---
def _desk_page(page: str, workspace: str, title: str, hint: str = ""):
    """Render a page-split desk shell. Reads only; never places orders."""
    return render_template(
        f"pages/{page}.html",
        banner=BANNER,
        desk_page=page,
        desk_workspace=workspace,
        page_title=title,
        page_hint=hint,
    )


@app.route("/desk")
@app.route("/desk/")
def desk_root():
    return _desk_page(
        "overview",
        "live",
        "Overview",
        "Start here — market pulse and account snapshot. Auto, Paper, and Research are separate pages.",
    )


@app.route("/desk/overview")
def desk_overview():
    return _desk_page(
        "overview",
        "live",
        "Overview",
        "Start here — market pulse and account snapshot. Auto, Paper, and Research are separate pages.",
    )


@app.route("/desk/auto")
@app.route("/desk/fox")
def desk_auto():
    return _desk_page(
        "auto",
        "live",
        "Fox’s workspace",
        "Fox owns automated trading decisions. Changing Woman brings research, calendar context and care for the desk.",
    )


@app.route("/desk/paper")
def desk_paper():
    return _desk_page(
        "paper",
        "paper",
        "Paper practice",
        "Simulated funds only — practice without touching your live broker account.",
    )


@app.route("/desk/research")
def desk_research():
    return _desk_page(
        "research",
        "live",
        "Research",
        "Ideas, rankings, scanner, and Moss notes. Research does not place live orders by itself.",
    )


@app.route("/desk/ticket")
def desk_ticket():
    return _desk_page(
        "ticket",
        "live",
        "Live ticket",
        "Buy or sell ticket and your broker positions / orders.",
    )


@app.route("/desk/settings")
def desk_settings():
    return _desk_page(
        "settings",
        "live",
        "Settings",
        "Watchlist, brains, and journal. Live execution switches stay labeled separately.",
    )


@app.route("/desk/all")
def desk_all_legacy():
    """Legacy single-page desk (pre page-split). Kept for recovery."""
    return render_template(
        "index_all.html",
        banner=BANNER,
        desk_page="all",
        desk_workspace="live",
        page_title="Full desk (legacy)",
        page_hint="Legacy single-page layout. Prefer Overview / Auto / Paper / Research.",
    )

# --- END PAGE_SPLIT_DESK_ROUTES ---


@app.route("/api/state")
def api_state():
    # Snapshot under short lock; expire/DecisionRing/jsonify outside lock.
    # Buzz uses in-memory/disk cache only — never hold _lock across Reddit HTTP.
    with _lock:
        cfg = dict(load_config())
        signals = list(load_signals())
        ledger = dict(load_ledger())
        journal = list(load_journal()[:40])
        watchlist = list(cfg.get("watchlist") or DEFAULT_WATCHLIST)
        focus_liquid = str(cfg.get("watchlist_focus") or "liquid").lower() == "liquid"

    try:
        signals = expire_stale_signals(signals)
    except Exception:
        pass
    preset = get_preset(cfg.get("risk_preset"))
    by_status = {"pending": [], "approving": [], "broker_pending": [], "approved": [], "rejected": [], "expired": []}
    for s in signals:
        st = s.get("status", "pending")
        if st in by_status:
            by_status[st].append(s)
    loop_st = get_paper_loop().status(cfg)
    decisions_preview = _decision_ring.latest(25)
    edge = _edge_sample_stats(40)
    promotion = _promotion_gate(cfg, ledger)

    buzz_sum = buzz_sources.buzz_summary_for_state(
        watchlist, focus_liquid=focus_liquid
    )
    if not bool(cfg.get("heat_enabled", True)):
        buzz_sum = dict(buzz_sum)
        buzz_sum["heat"] = []
    # Enrich signal copies with UI-safe fields and buzz counts (outside lock)
    buzz_cache = buzz_sources.get_cached_buzz()
    for st_key, lst in by_status.items():
        enriched = []
        for s in lst:
            s2 = _signal_ui_projection(s, cfg)
            bm = buzz_sources.buzz_mentions_for_ticker(str(s.get("ticker") or ""), buzz_cache)
            if bm:
                s2["buzz_mentions"] = bm
            enriched.append(s2)
        by_status[st_key] = enriched[:120]

    daily = daily_target_progress(cfg, ledger)
    llm_st = _llm_public_status(cfg)
    totals = loop_st.get("session_totals") or {}
    model_usd = float(totals.get("model_usd") or 0)
    try:
        if bool(cfg.get("radar_enabled")):
            market_radar.maybe_refresh(cfg)  # radar_maybe_refresh_state
    except Exception:
        pass
    payload = {
        "banner": BANNER,
        "config": cfg,
        "preset": preset,
        "presets": RISK_PRESETS,
        "signals": by_status,
        "ledger": ledger,
        "daily": daily,
        "journal": journal,
        "market_capture": market_capture.scorecard(
            decisions_preview,
            ledger.get("fills") or [],
        ),
        "live_locked": False,
        "research_draft": True,
        "broker": _broker_public_status(),
        "llm": llm_st,
        "loop": loop_st,
        "decisions_preview": decisions_preview,
        "desk_call": _latest_desk_call(cfg, signals, loop_st),
        "paper_desk_call": _latest_desk_call(paper_research_config(cfg), signals, loop_st),
        "paper_daily": daily_target_progress(paper_research_config(cfg), ledger),
        "paper_daily_recap": daily_recap(paper_research_config(cfg), ledger),
        "startup": _startup_status(),
        "opportunities": _ranked_opportunities(
            signals,
            decisions_preview,
            buzz_cache,
        ),
        "edge_sample": edge,
        "execution_realism": _execution_realism(ledger),
        "risk_cockpit": _risk_cockpit(cfg, ledger),
        "promotion_gate": promotion,
        "readiness": readiness_summary(cfg, ledger, loop=loop_st, promotion=promotion),
        "buzz": buzz_sum,
        "heat": buzz_sum.get("heat") or [],
        "heat_enabled": bool(cfg.get("heat_enabled", True)),
        "session_totals": totals,
        "brain_mode": cfg.get("brain_mode") or "gemini",
        "radar": market_radar.public_status(cfg),
        "radar_enabled": bool(cfg.get("radar_enabled", False)),
        "model_cost": llm_st.get("model_cost") or {},
        "ledger_brain": {
            "model_usd": round(model_usd, 4),
            "paper_pnl": float(totals.get("paper_pnl") or daily.get("pnl") or 0),
            "friction_usd": float(totals.get("friction_usd") or 0),
            "late_blocks": int(totals.get("late_blocks") or 0),
        },
    }
    try:
        payload["scoreboard"] = _day_scoreboard(ledger, cfg)
        payload["equity_curve"] = list((payload["scoreboard"] or {}).get("equity_curve") or [])
    except Exception:
        payload["scoreboard"] = {}
        payload["equity_curve"] = list(ledger.get("equity_curve") or [])
    payload["pace"] = (payload.get("daily") or {}).get("pace") or {}

    # --- API pack (edu): cached provider enrichment; never block /api/state on network I/O ---
    focus = None
    try:
        for d in decisions_preview:
            if d.get("ticker") and d.get("event") in ("decision", "intent", None):
                focus = str(d.get("ticker") or "").upper()
                if focus:
                    break
        if not focus and watchlist:
            focus = str(watchlist[0]).upper()
        payload["focus_ticker"] = focus
    except Exception:
        focus = None
    # Keep the auxiliary research focus separate from the latest-call identity.
    # A tickerless loop event is a status update, not a call for watchlist[0].
    latest_event = decisions_preview[0] if decisions_preview and isinstance(decisions_preview[0], dict) else None
    latest_ticker = str(latest_event.get("ticker") or "").upper() if latest_event else ""
    payload["latest_call"] = {
        "ticker": latest_ticker or None,
        "event": latest_event.get("event") if latest_event else None,
        "status_only": bool(latest_event and not latest_ticker),
    }
    payload.update(_state_aux_snapshot(cfg, watchlist, focus))
    payload.setdefault(
        "research_context",
        {
            "ticker": focus,
            "timeline": [],
            "digest": [],
            "alerts": [],
            "analysis": {"display_only": True},
            "display_only": True,
            "refreshing": True,
        },
    )
    payload["corrupt_files"] = corrupt_files_status()
    try:
        payload.update(_money_snapshot(cfg))
    except Exception:
        pass

    return jsonify(payload)


@app.route("/api/broker-identity", methods=["POST"])
def api_broker_identity():
    """Read-only account verification before the UI presents mode confirmation."""
    import broker_router
    status = broker_router.public_status()
    if status.get("broker") == "ibkr":
        result = broker_router.verify_execution_context()
        if not result.get("ok"):
            return jsonify(result), 400
        status = broker_router.public_status()
    return jsonify({"ok": True, "broker": status})




@app.route("/api/broker-ensure-gateway", methods=["POST"])
def api_broker_ensure_gateway():
    """Relaunch ibgateway.exe if the configured API port is down. Never places orders; 2FA may still be required after a full logout."""
    import broker_router
    status = broker_router.public_status()
    if status.get("broker") != "ibkr":
        return jsonify(ok=False, error="Ensure Gateway is only implemented for IBKR", broker=status), 400
    ensure = getattr(broker_router, "ensure_gateway", None)
    if not callable(ensure):
        return jsonify(ok=False, error="IBKR adapter missing ensure_gateway"), 500
    body = request.get_json(silent=True) or {}
    launch = True if not isinstance(body, dict) else bool(body.get("launch_if_down", True))
    # The desk button is an owner action; the upkeep watchdog sends automatic=true.
    automatic = isinstance(body, dict) and body.get("automatic") is True
    if automatic and launch:
        cfg = load_config()
        if not (cfg.get("session_active") and cfg.get("mode") in ("live_manual", "auto_live")):
            # Outside a live session a closed Gateway was most likely closed on purpose.
            return jsonify(ok=False, launched=False, automatic_launch_held=True,
                           note="No live session is running; Gateway is not reopened automatically.")
    try:
        result = ensure(launch_if_down=launch, automatic=automatic)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)[:200], gateway_restarted=False), 500
    return jsonify(result if isinstance(result, dict) else {"ok": False, "error": "unexpected ensure result"})


@app.route("/api/broker-pnl-refresh", methods=["POST"])
def api_broker_pnl_refresh():
    """Soft-refresh IBKR Daily P&L: bounce API client / re-subscribe. Never restarts Gateway or places orders."""
    import broker_router
    body = request.get_json(silent=True) or {}
    soft = True if not isinstance(body, dict) else bool(body.get("soft_reconnect", True))
    status = broker_router.public_status()
    if status.get("broker") != "ibkr":
        return jsonify(ok=False, error="Broker P&L refresh is only implemented for IBKR", broker=status), 400
    refresh = getattr(broker_router, "refresh_broker_pnl", None)
    if not callable(refresh):
        return jsonify(ok=False, error="IBKR adapter missing refresh_broker_pnl"), 500
    try:
        result = refresh(soft_reconnect=soft)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)[:200], risk_ready=False, account={}, refresh={"gateway_restarted": False}), 500
    # Bust cached broker book so /api/state reflects the refresh immediately.
    _BROKER_BOOK_CACHE.update(at=0.0, val=None, key=None)
    return jsonify(result if isinstance(result, dict) else {"ok": False, "error": "unexpected refresh result"})

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    with _BROKER_EXEC_LOCK, _lock:
        cfg = load_config()
        if request.method == "GET":
            return jsonify({"config": cfg, "presets": RISK_PRESETS, "banner": BANNER})

        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "JSON object required"}), 400
        try:
            res = _api_config_post(cfg, body)
        except BadNumber as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not (isinstance(res, tuple) and len(res) == 4):
            return res  # early (response, status) error
    return _api_config_after(*res)


@app.route("/api/paper-research", methods=["POST"])
def api_paper_research():
    """Independent simulator controls; never select or authorize a broker account."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body or set(body) - {"enabled", "auto_approve", "risk_preset", "fractional_enabled", "order_budget"}:
        return jsonify(ok=False, error="Use the displayed paper research settings"), 400
    for key in ("enabled", "auto_approve", "fractional_enabled"):
        if key in body and not isinstance(body[key], bool):
            return jsonify(ok=False, error=f"{key} must be true or false"), 400
    if "risk_preset" in body and (not isinstance(body["risk_preset"], str) or body["risk_preset"] not in RISK_PRESETS):
        return jsonify(ok=False, error="risk_preset must be low, mid, or high"), 400
    if "order_budget" in body:
        from trade_planner import number
        try:
            body["order_budget"] = float(number(body["order_budget"], "Paper dollars per order", 0, 100_000))
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
    with _lock:
        cfg = dict(load_config())
        for field, key in (("enabled", "paper_research_enabled"), ("auto_approve", "paper_auto_approve"), ("risk_preset", "paper_risk_preset"),
                           ("fractional_enabled", "paper_fractional_enabled"), ("order_budget", "paper_order_budget")):
            if field in body:
                cfg[key] = body[field]
        save_config(cfg)
        append_journal("paper_research_config", {key: cfg[key] for key in ("paper_research_enabled", "paper_auto_approve", "paper_risk_preset")})
    return jsonify(ok=True, config=cfg)


def _api_config_post(cfg: dict[str, Any], body: dict[str, Any]):
    """POST /api/config body handler (caller holds _lock; BadNumber → 400)."""
    if True:
        do_drain = False
        do_radar_refresh = False
        # Mode changes
        if "mode" in body:
            new_mode = body["mode"]
            if new_mode not in ("manual", "auto_paper", "auto_live", "live_manual"):
                return jsonify({"ok": False, "error": "Invalid mode"}), 400
            if new_mode in ("auto_live", "live_manual"):
                broker = _broker_public_status()
                if broker.get("configured"):
                    import broker_router
                    context = broker_router.verify_execution_context()
                    if not context.get("ok"):
                        return jsonify({"ok": False, "error": context.get("error")}), 400
                    broker = broker_router.public_status()
                    cfg["broker_identity"] = context["identity"]
                confirmation = str(body.get("live_confirm") or "").strip().upper()
                expected = (
                    "REAL"
                    if broker.get("configured") and broker.get("paper_mode") is False
                    else "AUTO_LIVE"
                )
                if confirmation != expected:
                    return jsonify({
                        "ok": False,
                        "error": f"live_confirm must be {expected} before enabling auto_live",
                    }), 400
            if new_mode == "auto_live":
                # No ENABLE LIVE AUTO unlock — optional kill-switch limits only.
                ks_in = body.get("kill_switch")
                if isinstance(ks_in, dict):
                    ks = cfg.setdefault("kill_switch", dict(DEFAULT_CONFIG["kill_switch"]))
                    clean = _clean_kill_switch_in(ks_in)
                    for k in ("max_daily_loss_usd", "max_trades_per_day", "max_position_size_usd"):
                        if clean.get(k) is not None:
                            ks[k] = clean[k]
                    limits_provided = any(
                        ks.get(k) not in (None, "", 0)
                        for k in ("max_daily_loss_usd", "max_trades_per_day", "max_position_size_usd")
                    )
                    if ks_in.get("armed") is False:
                        ks["armed"] = False
                    elif ks_in.get("armed") is True or limits_provided:
                        ks["armed"] = True
                # Alpaca: gate→submit; broker-only on success (no dual local paper_fill)
            cfg.pop("live_confirm_ok", None)
            if new_mode not in ("auto_live", "live_manual"):
                cfg.pop("broker_identity", None)
            prev_mode = cfg.get("mode")
            cfg["mode"] = new_mode
            if cfg.get("live_agent"):
                cfg["live_agent"]["enabled"] = False
            append_journal(
                "mode_change",
                {
                    "mode": new_mode,
                    "research_draft": True,
                    "broker": _broker_public_status(),
                },
            )
            # Keep decision loop alive for Ask me first (manual) + session
            if (
                cfg.get("session_active")
                and cfg.get("loop_enabled")
                and new_mode in ("manual", "auto_paper")
            ):
                get_paper_loop().start()
            # Auto fill On: drain Waiting ghosts AFTER lock (paper approve or expire)
            if new_mode == "auto_paper" and prev_mode != "auto_paper":
                do_drain = True

        if "risk_preset" in body:
            rp = body["risk_preset"]
            if rp not in RISK_PRESETS:
                return jsonify({"ok": False, "error": "Invalid preset"}), 400
            cfg["risk_preset"] = rp
            append_journal("preset_change", {"preset": rp})

        if "watchlist" in body:
            try:
                wl = parse_watchlist(body["watchlist"])
            except TypeError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            cfg["watchlist"] = wl
            try:
                buzz_sources.invalidate_buzz_cache()
            except Exception:
                pass
            append_journal("watchlist_update", {"watchlist": wl, "equity_focus": sanitize_watchlist(wl)})

        if "watchlist_focus" in body:
            focus = str(body.get("watchlist_focus") or "liquid").strip().lower()
            if focus not in ("liquid", "all"):
                return jsonify({"ok": False, "error": "watchlist_focus must be liquid or all"}), 400
            cfg["watchlist_focus"] = focus
            try:
                buzz_sources.invalidate_buzz_cache()
            except Exception:
                pass
            append_journal("watchlist_focus_set", {"watchlist_focus": focus})

        if "daily_profit_target_usd" in body:
            raw = body["daily_profit_target_usd"]
            if raw in (None, "", False):
                cfg["daily_profit_target_usd"] = None
            else:
                val = _num(raw, "Daily goal", lo=0, hi=1e9)
                cfg["daily_profit_target_usd"] = None if val == 0 else val
            append_journal("daily_target_set", {"daily_profit_target_usd": cfg["daily_profit_target_usd"]})

        
        if "loop_interval_sec" in body:
            cfg["loop_interval_sec"] = paper_loop_mod.clamp_interval(body.get("loop_interval_sec"))
            append_journal("loop_interval_set", {"loop_interval_sec": cfg["loop_interval_sec"]})
        if "loop_enabled" in body:
            cfg["loop_enabled"] = bool(body["loop_enabled"])
            if (
                cfg["loop_enabled"]
                and cfg.get("session_active")
                and cfg.get("mode") in ("auto_paper", "manual")
            ):
                get_paper_loop().start()
            elif not cfg["loop_enabled"]:
                get_paper_loop().stop()
            append_journal("loop_enabled_set", {"loop_enabled": cfg["loop_enabled"]})
        if "rth_only" in body:
            new_rth = bool(body["rth_only"])
            if (
                not new_rth
                and cfg.get("session_active")
                and not body.get("confirm_overnight")
            ):
                return jsonify({
                    "ok": False,
                    "error": "confirm_overnight required to disable rth_only while session active",
                }), 400
            cfg["rth_only"] = new_rth
            append_journal("rth_only_set", {"rth_only": cfg["rth_only"]})
        if "max_session_loss_usd" in body:
            raw = body["max_session_loss_usd"]
            if raw in (None, "", 0, "0"):
                cfg["max_session_loss_usd"] = None
            else:
                cfg["max_session_loss_usd"] = abs(_num(raw, "Max session loss", lo=-1e9, hi=1e9))
            append_journal("max_session_loss_set", {"max_session_loss_usd": cfg["max_session_loss_usd"]})

        for key in (
            "paper_equity", "signal_ttl_sec", "demo_signal_interval_sec",
            "scan_interval_sec", "slip_bps", "alert_cooldown_sec",
            "promotion_min_samples", "promotion_min_independent_tickers",
            "promotion_min_win_rate",
            "promotion_min_expectancy_usd", "promotion_min_profit_factor",
            "promotion_max_drawdown_pct",
            "promotion_window_days",
            "min_net_reward_risk", "evidence_min_samples", "giveback_stop_pct",
            "event_guard_before_min", "event_guard_after_min", "wsb_crowd_min_mentions",
        ):
            if key in body:
                lo, hi = _NUMERIC_CFG_LIMITS[key]
                cfg[key] = _num(body[key], key, lo=lo, hi=hi)
        for key in ("evidence_min_samples", "event_guard_before_min", "event_guard_after_min", "wsb_crowd_min_mentions"):
            if key in body:
                cfg[key] = int(cfg[key])
        for key in ("evidence_gate_enabled", "event_guard_enabled"):
            if key in body:
                cfg[key] = body[key] is True
        if "wsb_crowding_action" in body:
            if body["wsb_crowding_action"] not in ("off", "note", "half", "skip"):
                return jsonify({"ok": False, "error": "wsb_crowding_action must be off, note, half or skip"}), 400
            cfg["wsb_crowding_action"] = body["wsb_crowding_action"]
        if "custom_market_events" in body:
            try:
                cfg["custom_market_events"] = market_events.validate_custom_events(body["custom_market_events"])
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400

        if "heat_enabled" in body:
            cfg["heat_enabled"] = bool(body["heat_enabled"])
            append_journal("heat_enabled_set", {"heat_enabled": cfg["heat_enabled"]})

        if "radar_enabled" in body:
            cfg["radar_enabled"] = bool(body["radar_enabled"])
            append_journal("radar_enabled_set", {"radar_enabled": cfg["radar_enabled"]})
        if "social_enabled" in body:
            cfg["social_enabled"] = bool(body["social_enabled"])
            append_journal("social_enabled_set", {"social_enabled": cfg["social_enabled"]})
        # API pack macro gates
        for _mk, _cast in (
            ("macro_gates_enabled", bool),
            ("macro_force_ask_first", bool),
            ("macro_use_heuristics", bool),
        ):
            if _mk in body:
                cfg[_mk] = _cast(body[_mk])
        if "macro_size_mult" in body:
            cfg["macro_size_mult"] = max(0.1, min(1.0, _num(body["macro_size_mult"], "macro_size_mult", lo=0, hi=1)))

        if "radar_enabled" in body and cfg.get("radar_enabled"):
            # Network scan runs AFTER the lock is released (see api_config)
            do_radar_refresh = True
        if "radar_top_n" in body:
            cfg["radar_top_n"] = market_radar.clamp_top_n(body.get("radar_top_n"))
            append_journal("radar_top_n_set", {"radar_top_n": cfg["radar_top_n"]})
        if "radar_refresh_sec" in body:
            cfg["radar_refresh_sec"] = market_radar.clamp_refresh_sec(body.get("radar_refresh_sec"))
            append_journal("radar_refresh_sec_set", {"radar_refresh_sec": cfg["radar_refresh_sec"]})

        if "claude_shadow" in body:
            want = bool(body["claude_shadow"])
            if want and not claude_brain.is_configured():
                return jsonify({"ok": False, "error": "Add ANTHROPIC_API_KEY to .env (and restart) to run the Claude head-to-head"}), 400
            cfg["claude_shadow"] = want
            append_journal("claude_shadow_set", {"claude_shadow": want})
        if "llm_enabled" in body:
            cfg["llm_enabled"] = bool(body["llm_enabled"])
        if "llm_on_scan" in body:
            cfg["llm_on_scan"] = bool(body["llm_on_scan"])
        if "llm_model" in body:
            raw_m = body["llm_model"]
            cfg["llm_model"] = (str(raw_m).strip() or None) if raw_m not in (None, "") else None
        if "brain_mode" in body or "model" in body:
            raw_b = body.get("brain_mode", body.get("model"))
            mode = str(raw_b or "gemini").strip().lower()
            if mode not in ("gemini", "mock", "jev", "claude"):
                return jsonify({"ok": False, "error": "brain_mode must be gemini|mock|jev|claude"}), 400
            if mode == "claude" and not claude_brain.is_configured():
                return jsonify({"ok": False, "error": "Add ANTHROPIC_API_KEY to .env (and restart) to use Claude"}), 400
            if mode == "jev" and not llm_trader.typesafe_api_key():
                # Allow selecting jev; runtime falls back to mock if key missing
                pass
            cfg["brain_mode"] = mode
            append_journal("brain_mode_set", {"brain_mode": mode})
        if "decision_horizon_min" in body:
            try:
                hz = int(body.get("decision_horizon_min"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "decision_horizon_min must be int"}), 400
            cfg["decision_horizon_min"] = max(5, min(120, hz))
            append_journal("horizon_set", {"decision_horizon_min": cfg["decision_horizon_min"]})
        if "fee_bps" in body:
            cfg["fee_bps"] = _num(body.get("fee_bps"), "fee_bps", lo=0, hi=500)

        if "kill_switch" in body and cfg.get("mode") != "auto_live":
            # The enable flag controls these risk limits, not broker mode.
            clean = _clean_kill_switch_in(body["kill_switch"] or {})
            ks = cfg.setdefault("kill_switch", dict(DEFAULT_CONFIG["kill_switch"]))
            for k in ("armed", "max_daily_loss_usd", "max_trades_per_day", "max_position_size_usd"):
                if k in clean:
                    ks[k] = clean[k]

        save_config(cfg)
        cfg_out = dict(cfg)
        broker_out = _broker_public_status()
    return cfg_out, broker_out, do_drain, do_radar_refresh


def _api_config_after(cfg_out: dict[str, Any], broker_out: Any, do_drain: bool, do_radar_refresh: bool):
    if do_radar_refresh:
        try:
            market_radar.maybe_refresh(cfg_out, force=True)
        except Exception:
            pass
    if do_drain:
        try:
            _drain_pending_for_autofill(cfg_out)
        except Exception as exc:  # noqa: BLE001
            append_journal("pending_drain_error", {"error": str(exc)})

    return jsonify({"ok": True, "config": cfg_out, "banner": BANNER, "broker": broker_out})


@app.route("/api/radar", methods=["GET", "POST"])
def api_radar():
    """Whole-market mover radar — ranked movers, no LLM.

    GET: cached status. POST body `{force: true}` refreshes (or enable via config).
    Paper research only.
    """
    with _lock:
        cfg = dict(load_config())
    body = {}
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        force = bool(body.get("force"))
        # Optional inline enable
        if "radar_enabled" in body:
            with _lock:
                cfg2 = load_config()
                cfg2["radar_enabled"] = bool(body["radar_enabled"])
                if "radar_top_n" in body:
                    cfg2["radar_top_n"] = market_radar.clamp_top_n(body.get("radar_top_n"))
                if "radar_refresh_sec" in body:
                    cfg2["radar_refresh_sec"] = market_radar.clamp_refresh_sec(
                        body.get("radar_refresh_sec")
                    )
                save_config(cfg2)
                cfg = dict(cfg2)
            append_journal("radar_enabled_set", {"radar_enabled": cfg["radar_enabled"]})
            force = force or bool(cfg.get("radar_enabled"))
        try:
            if force or bool(cfg.get("radar_enabled")):
                market_radar.maybe_refresh(cfg, force=force)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc), "radar": market_radar.public_status(cfg)}), 500
    elif bool(cfg.get("radar_enabled")):
        try:
            market_radar.maybe_refresh(cfg)
        except Exception:
            pass
    st = market_radar.public_status(cfg)
    return jsonify({"ok": True, "radar": st, "config": {
        "radar_enabled": bool(cfg.get("radar_enabled")),
        "radar_top_n": market_radar.clamp_top_n(cfg.get("radar_top_n")),
        "radar_refresh_sec": market_radar.clamp_refresh_sec(cfg.get("radar_refresh_sec")),
    }, "sim": True, "paper_research": True})



# --- P1.7 NL watchlist find (local keyword maps + optional Gemini) ---

_WATCHLIST_FIND_NAME_ALIASES: dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "broadcom": "AVGO",
    "intel": "INTC",
    "amd": "AMD",
    "netflix": "NFLX",
    "costco": "COST",
    "walmart": "WMT",
    "disney": "DIS",
    "boeing": "BA",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "exxon": "XOM",
    "chevron": "CVX",
}

_WATCHLIST_FIND_KEYWORDS: dict[str, list[str]] = {
    "semi": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "AMAT", "SMH"],
    "semiconductor": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "AMAT", "SMH"],
    "semiconductors": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "AMAT", "SMH"],
    "chip": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "AMAT", "SMH"],
    "chips": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "AMAT", "SMH"],
    "tech": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "CRM", "ORCL", "ADBE", "XLK", "QQQ"],
    "technology": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "CRM", "ORCL", "ADBE", "XLK", "QQQ"],
    "mega": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"],
    "megacap": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"],
    "mega-cap": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"],
    "bank": ["JPM", "BAC", "GS", "XLF"],
    "banks": ["JPM", "BAC", "GS", "XLF"],
    "finance": ["JPM", "BAC", "GS", "V", "MA", "XLF"],
    "financial": ["JPM", "BAC", "GS", "V", "MA", "XLF"],
    "energy": ["XOM", "CVX", "XLE"],
    "oil": ["XOM", "CVX", "XLE"],
    "retail": ["WMT", "COST", "HD", "NKE", "SBUX", "AMZN"],
    "consumer": ["WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS"],
    "auto": ["TSLA", "F", "GM"],
    "etf": ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH"],
    "index": ["SPY", "QQQ", "IWM", "DIA"],
    "liquid": list(paper_loop_mod.CURATED_LIQUID_US),
}

_WATCHLIST_FIND_SUGGEST_CAP = 12
_TICKER_TOKEN_RE = re.compile(r"\b[A-Za-z]{1,5}\b")
_SUGGEST_HINT_RE = re.compile(
    r"\b(add|suggest|recommend|find me|give me|propose|ideas?|candidates?)\b",
    re.I,
)
_FILTER_HINT_RE = re.compile(
    r"\b(on my list|on the list|my watchlist|watchlist|where is|which .+ on|filter|show me)\b",
    re.I,
)

WATCHLIST_FIND_SYSTEM = (
    "You help an educational paper trading desk find US equity tickers. "
    "Return JSON only with keys matched, suggest, note. "
    "matched = subset of the provided watchlist that fit the query. "
    "suggest = up to 12 liquid US equity tickers not required to be on the list "
    "(prefer well-known liquid names; never invent obscure tickers). "
    "note = one short plain sentence. "
    "Research/educational only — never claim live trading advice or place orders. "
    "US equity tickers only (A-Z, optional class share like BRK.B)."
)


def _watchlist_find_normalize_tickers(raw: Any, *, allow_from: set[str] | None = None) -> list[str]:
    """Normalize ticker list; optionally intersect with allow_from; cap length."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tok = _normalize_watchlist_token(item)
        if not tok or tok in seen:
            continue
        if "." in tok and not re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", tok):
            continue
        if not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", tok):
            continue
        if allow_from is not None and tok not in allow_from:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= _WATCHLIST_FIND_SUGGEST_CAP:
            break
    return out


def _watchlist_find_detect_mode(q: str, mode: str | None) -> str:
    m = (mode or "").strip().lower()
    if m in ("filter", "suggest"):
        return m
    has_suggest = bool(_SUGGEST_HINT_RE.search(q))
    has_filter = bool(_FILTER_HINT_RE.search(q))
    if has_suggest and not has_filter:
        return "suggest"
    if has_filter and not has_suggest:
        return "filter"
    return "auto"


def watchlist_find_local(q: str, watchlist: list[str], *, mode: str = "auto") -> dict[str, Any]:
    """Deterministic substring/ticker/keyword match. Never invent obscure tickers."""
    q_raw = (q or "").strip()
    q_lower = q_raw.lower()
    wl = [str(s).upper() for s in (watchlist or []) if s]
    wl_set = set(wl)
    curated = [str(s).upper() for s in paper_loop_mod.CURATED_LIQUID_US]
    curated_set = set(curated)

    matched: list[str] = []
    matched_seen: set[str] = set()
    keyword_hits: list[str] = []

    # Exact / prefix ticker tokens from query
    for m in _TICKER_TOKEN_RE.finditer(q_raw):
        tok = m.group(0).upper()
        if tok.lower() in _WATCHLIST_FIND_KEYWORDS or tok.lower() in _WATCHLIST_FIND_NAME_ALIASES:
            continue
        if len(tok) < 1:
            continue
        for sym in wl:
            if sym == tok or (len(tok) >= 2 and sym.startswith(tok)):
                if sym not in matched_seen:
                    matched_seen.add(sym)
                    matched.append(sym)

    # Name aliases (word-boundary to avoid "amd" inside unrelated words)
    for alias, ticker in _WATCHLIST_FIND_NAME_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", q_lower):
            if ticker in wl_set and ticker not in matched_seen:
                matched_seen.add(ticker)
                matched.append(ticker)
            keyword_hits.append(ticker)

    # Keyword maps
    for key, tickers in _WATCHLIST_FIND_KEYWORDS.items():
        if re.search(rf"\b{re.escape(key)}\b", q_lower):
            for t in tickers:
                tu = t.upper()
                keyword_hits.append(tu)
                if tu in wl_set and tu not in matched_seen:
                    matched_seen.add(tu)
                    matched.append(tu)

    # Substring on symbols themselves (e.g. "NVDA" typed partially already covered)
    # Also: if query is a single short token matching a symbol substring on list
    compact = re.sub(r"[^a-z0-9]", "", q_lower)
    if compact and 2 <= len(compact) <= 5:
        for sym in wl:
            if compact in sym.lower() and sym not in matched_seen:
                matched_seen.add(sym)
                matched.append(sym)

    # Suggest: curated liquid not already on list, from keyword/alias hits or liquid map
    suggest_pool: list[str] = []
    suggest_seen: set[str] = set()
    # Expand deterministic discovery beyond the small liquid starter list.
    import market_universe
    directory = market_universe.load(__import__("sys").modules[__name__]).get("symbols", [])
    directory_hits = [r["symbol"] for r in directory if q_raw and
                      (r.get("symbol") == q_raw.upper() or
                       (len(q_raw) >= 3 and q_lower in str(r.get("name", "")).lower()))]
    for ticker in paper_loop_mod.equity_loop_symbols(directory_hits):
        if ticker in wl_set and ticker not in matched_seen:
            matched.append(ticker); matched_seen.add(ticker)
        elif ticker not in wl_set and ticker not in suggest_seen:
            suggest_pool.append(ticker); suggest_seen.add(ticker)
    for t in keyword_hits:
        tu = t.upper()
        if tu in curated_set and tu not in wl_set and tu not in suggest_seen:
            suggest_seen.add(tu)
            suggest_pool.append(tu)
    # If query asks for liquid / mega / tech without hits, fall back to curated tips
    if not suggest_pool and mode in ("suggest", "auto") and (
        _SUGGEST_HINT_RE.search(q_raw) or re.search(r"\b(liquid|mega|tech|semi)\b", q_lower)
    ):
        for t in curated:
            if t not in wl_set and t not in suggest_seen:
                suggest_seen.add(t)
                suggest_pool.append(t)
            if len(suggest_pool) >= _WATCHLIST_FIND_SUGGEST_CAP:
                break

    # Mode shaping
    if mode == "filter":
        suggest_pool = []
    elif mode == "suggest" and not suggest_pool and keyword_hits:
        for t in keyword_hits:
            tu = t.upper()
            if tu in curated_set and tu not in wl_set and tu not in suggest_seen:
                suggest_seen.add(tu)
                suggest_pool.append(tu)

    suggest = suggest_pool[:_WATCHLIST_FIND_SUGGEST_CAP]

    parts = []
    if matched:
        parts.append(f"{len(matched)} on your list")
    if suggest:
        parts.append(f"{len(suggest)} suggest")
    if not parts:
        parts.append("no local matches")
    note = f"Local match: {', '.join(parts)}."

    return {
        "ok": True,
        "query": q_raw,
        "matched": matched,
        "suggest": suggest,
        "note": note,
        "source": "local",
        "directory_matches": directory_hits[:_WATCHLIST_FIND_SUGGEST_CAP],
    }


def watchlist_find_gemini(q: str, watchlist: list[str], *, mode: str = "auto") -> dict[str, Any] | None:
    """Gemini JSON find; returns None on any failure so caller can fall back to local."""
    cfg = llm_trader.load_llm_config()
    if not cfg.get("configured") or not cfg.get("api_key"):
        return None
    wl = [str(s).upper() for s in (watchlist or []) if s][:200]
    curated = list(paper_loop_mod.CURATED_LIQUID_US)
    curated_set = set(curated)
    wl_set = set(wl)
    prompt = (
        f"Query: {q.strip()}\n"
        f"Mode hint: {mode}\n"
        f"Current watchlist ({len(wl)}): {', '.join(wl) if wl else '(empty)'}\n"
        f"Preferred liquid universe for suggest: {', '.join(curated)}\n"
        "Return JSON: {\"matched\":[...],\"suggest\":[...],\"note\":\"...\"}\n"
        "matched must be a subset of the current watchlist. "
        "suggest ≤12 liquid US tickers (prefer the liquid universe). "
        "Educational paper desk only."
    )
    try:
        raw = llm_trader.gemini_generate(
            prompt,
            system=WATCHLIST_FIND_SYSTEM,
            json_mode=True,
            cfg=cfg,
            timeout_sec=12,
            max_output_tokens=512,
        )
    except Exception:
        return None
    try:
        # Strip markdown fences if any
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    matched = _watchlist_find_normalize_tickers(data.get("matched"), allow_from=wl_set)
    # Suggest: allow curated + anything that looks like equity, but prefer curated
    suggest_raw = data.get("suggest") if isinstance(data.get("suggest"), list) else []
    suggest: list[str] = []
    seen: set[str] = set()
    for item in suggest_raw:
        tok = _normalize_watchlist_token(item)
        if not tok or tok in seen or tok in wl_set:
            continue
        if not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", tok):
            continue
        # Prefer curated; allow only curated liquid to avoid obscure inventions
        if tok not in curated_set:
            continue
        seen.add(tok)
        suggest.append(tok)
        if len(suggest) >= _WATCHLIST_FIND_SUGGEST_CAP:
            break
    note = str(data.get("note") or "").strip()[:240] or "Gemini watchlist find."
    if mode == "filter":
        suggest = []
    return {
        "ok": True,
        "query": q.strip(),
        "matched": matched,
        "suggest": suggest,
        "note": note,
        "source": "gemini",
    }


def watchlist_find(q: str, watchlist: list[str], *, mode: str | None = None) -> dict[str, Any]:
    """Try Gemini when configured; always fall back to local on failure."""
    detected = _watchlist_find_detect_mode(q, mode)
    local = watchlist_find_local(q, watchlist, mode=detected)
    if local.get("directory_matches"):
        return local
    gem = watchlist_find_gemini(q, watchlist, mode=detected)
    if gem is not None:
        return gem
    return local


@app.route("/api/watchlist/find", methods=["POST"])
def api_watchlist_find():
    """NL find on / for watchlist: filter matches + optional suggest (merge confirm in UI)."""
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be an object"}), 400
    q = body.get("q")
    if q is None or (isinstance(q, str) and not q.strip()):
        return jsonify({"ok": False, "error": "q required"}), 400
    q = str(q).strip()
    if len(q) > 400:
        return jsonify({"ok": False, "error": "q too long"}), 400
    mode_raw = body.get("mode")
    mode = None
    if mode_raw is not None and str(mode_raw).strip():
        mode = str(mode_raw).strip().lower()
        if mode not in ("filter", "suggest"):
            return jsonify({"ok": False, "error": "mode must be filter or suggest"}), 400
    with _lock:
        cfg = load_config()
        try:
            watchlist = parse_watchlist(cfg.get("watchlist") or [])
        except TypeError:
            watchlist = list(DEFAULT_WATCHLIST)
    result = watchlist_find(q, watchlist, mode=mode)
    return jsonify(result)


@app.route("/api/watchlist/import", methods=["POST"])
def api_watchlist_import():
    with _lock:
        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "request body must be an object"}), 400
        mode = str(body.get("mode") or "replace").strip().lower()
        source = str(body.get("source") or "paste").strip().lower()
        if mode not in ("replace", "merge"):
            return jsonify({"ok": False, "error": "mode must be replace or merge"}), 400
        if source not in ("paste", "csv", "google_finance"):
            return jsonify({"ok": False, "error": "source must be paste, csv, or google_finance"}), 400
        if "text" not in body:
            return jsonify({"ok": False, "error": "text required"}), 400
        try:
            imported = parse_watchlist_import(body.get("text"))
        except TypeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        cfg = load_config()
        try:
            current = parse_watchlist(cfg.get("watchlist") or [])
        except TypeError:
            current = []
        # Run sanitize-friendly normalize (keep pairs as tokens; UI may still show them;
        # loop/evaluate drop non-equities via equity_loop_symbols)
        imported = parse_watchlist(imported)
        if mode == "replace":
            watchlist = imported
            added = len(imported)
        else:
            watchlist = current + [ticker for ticker in imported if ticker not in set(current)]
            added = len(watchlist) - len(current)
        cfg["watchlist"] = watchlist
        try:
            buzz_sources.invalidate_buzz_cache()
        except Exception:
            pass
        save_config(cfg)
        append_journal(
            "watchlist_import",
            {"count": len(imported), "added": added, "source": source, "mode": mode},
        )
        return jsonify({"ok": True, "watchlist": watchlist, "added": added, "total": len(watchlist)})


@app.route("/api/watchlist/use-liquid", methods=["POST"])
def api_watchlist_use_liquid():
    """Replace saved watchlist with curated liquid US list (~40) and set focus=liquid."""
    with _lock:
        cfg = load_config()
        wl = list(paper_loop_mod.CURATED_LIQUID_US)
        cfg["watchlist"] = wl
        try:
            buzz_sources.invalidate_buzz_cache()
        except Exception:
            pass
        cfg["watchlist_focus"] = "liquid"
        save_config(cfg)
        append_journal(
            "watchlist_use_liquid",
            {"total": len(wl), "watchlist_focus": "liquid"},
        )
        return jsonify(
            {
                "ok": True,
                "watchlist": wl,
                "total": len(wl),
                "watchlist_focus": "liquid",
                "config": cfg,
            }
        )


@app.route("/api/watchlist/restore-google-finance", methods=["POST"])
def api_watchlist_restore_google_finance():
    """Restore watchlist from data/google_finance_watchlist.txt if present."""
    gf_path = DATA_DIR / "google_finance_watchlist.txt"
    if not gf_path.is_file():
        return jsonify({"ok": False, "error": "google_finance_watchlist.txt not found"}), 404
    try:
        text_body = gf_path.read_text(encoding="utf-8")
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    with _lock:
        cfg = load_config()
        try:
            wl = parse_watchlist_import(text_body)
            wl = parse_watchlist(wl)  # normalize AMEX/OTC + protect BTC/USD pairs
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not wl:
            return jsonify({"ok": False, "error": "no symbols parsed from google finance file"}), 400
        cfg["watchlist"] = wl
        try:
            buzz_sources.invalidate_buzz_cache()
        except Exception:
            pass
        # Keep focus as-is (often liquid) so scan/loop still intersect; user can switch to all.
        save_config(cfg)
        append_journal(
            "watchlist_restore_google_finance",
            {"total": len(wl), "watchlist_focus": cfg.get("watchlist_focus")},
        )
        return jsonify(
            {
                "ok": True,
                "watchlist": wl,
                "total": len(wl),
                "watchlist_focus": cfg.get("watchlist_focus"),
                "config": cfg,
            }
        )


@app.route("/api/watchlist/curated", methods=["GET"])
def api_watchlist_curated():
    """Return curated liquid list + effective focused symbols for current config."""
    cfg = load_config()
    curated = list(paper_loop_mod.CURATED_LIQUID_US)
    focused = paper_loop_mod.prune_watchlist_for_trading(
        list(cfg.get("watchlist") or []),
        focus=str(cfg.get("watchlist_focus") or "liquid"),
    )
    return jsonify(
        {
            "ok": True,
            "curated": curated,
            "curated_count": len(curated),
            "watchlist_focus": cfg.get("watchlist_focus") or "liquid",
            "focused": focused,
            "focused_count": len(focused),
            "saved_count": len(cfg.get("watchlist") or []),
            "google_finance_available": (DATA_DIR / "google_finance_watchlist.txt").is_file(),
        }
    )


_EVALUATION_SLOTS = threading.BoundedSemaphore(5)


def _evaluate_watchlist_ticker(ticker: str, cfg: dict[str, Any], *, llm: bool = True,
                             deadline_mono: float | None = None) -> dict[str, Any]:
    """Analyze one ticker; optional LLM thesis (honors llm_on_scan)."""
    from screener_logic import analyze_ticker

    if deadline_mono is not None and time.monotonic() >= deadline_mono:
        raise TimeoutError("Evaluation deadline expired before analysis")
    analysis = analyze_ticker(ticker)
    if deadline_mono is not None and time.monotonic() >= deadline_mono:
        raise TimeoutError("Evaluation deadline expired; model request skipped")
    if not isinstance(analysis, dict) or analysis.get("error"):
        raise RuntimeError(str((analysis or {}).get("error") or "analysis failed"))
    out: dict[str, Any] = {
        "ticker": ticker,
        "verdict": analysis.get("verdict"),
        "gap_pct": analysis.get("gap_pct"),
        "research_flags": analysis.get("research_flags") or [],
        "research_flag": analysis.get("research_flag"),
        "citations": _screener_citations(analysis),
        "entry_quality": analysis.get("entry_quality"),
        "price": analysis.get("price"),
    }
    if not llm:
        out.update({
            "llm_side": None,
            "llm_confidence": None,
            "llm_thesis": None,
            "screener_only": True,
        })
        return out
    thesis = _research_thesis(analysis, cfg, source="evaluation")
    out.update({
        "llm_side": thesis.get("side"),
        "llm_confidence": thesis.get("confidence"),
        "llm_thesis": thesis.get("thesis") or None,
    })
    if thesis.get("error"):
        out["error"] = thesis.get("error")
    return out


def _evaluate_one_with_timeout(
    ticker: str,
    cfg: dict[str, Any],
    timeout_sec: float = 18.0,
    *,
    llm: bool = True,
    deadline_mono: float | None = None,
    request_slots: threading.BoundedSemaphore | None = None,
) -> dict[str, Any]:
    """A timed-out waiter never releases an actual evaluation's concurrency slot."""
    import time as _time
    expires = min(_time.monotonic() + timeout_sec, deadline_mono or float("inf"))
    failed = {"ticker": ticker, "verdict": None, "llm_side": None,
              "llm_confidence": None, "llm_thesis": None}
    held = []
    for slots in (request_slots, _EVALUATION_SLOTS):
        if slots is None:
            continue
        if not slots.acquire(timeout=max(0, expires - _time.monotonic())):
            for acquired in reversed(held):
                acquired.release()
            return dict(failed, error="timeout waiting for available evaluation capacity")
        held.append(slots)

    result: list[dict[str, Any]] = []
    error: list[str] = []

    def worker() -> None:
        try:
            result.append(_evaluate_watchlist_ticker(ticker, cfg, llm=llm, deadline_mono=expires))
        except Exception as exc:  # noqa: BLE001
            error.append(str(exc)[:300] or "evaluation failed")
        finally:
            for acquired in reversed(held):
                acquired.release()

    thread = threading.Thread(target=worker, name=f"watchlist-eval-{ticker}", daemon=True)
    try:
        thread.start()
    except BaseException:
        for acquired in reversed(held):
            acquired.release()
        raise
    thread.join(max(0, expires - _time.monotonic()))
    if thread.is_alive():
        return {
            "ticker": ticker,
            "verdict": None,
            "llm_side": None,
            "llm_confidence": None,
            "llm_thesis": None,
            "error": f"timeout after {int(timeout_sec)}s",
        }
    if error:
        return {
            "ticker": ticker,
            "verdict": None,
            "llm_side": None,
            "llm_confidence": None,
            "llm_thesis": None,
            "error": error[0],
        }
    return result[0] if result else {
        "ticker": ticker,
        "verdict": None,
        "llm_side": None,
        "llm_confidence": None,
        "llm_thesis": None,
        "error": "evaluation returned no result",
    }


@app.route("/api/watchlist/evaluate", methods=["POST"])
def api_watchlist_evaluate():
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be an object"}), 400
    try:
        raw_tickers = body.get("tickers") if "tickers" in body else load_config().get("watchlist") or []
        all_tickers = parse_watchlist(raw_tickers)
        # Sanitize: drop .DJI / 2353 / 29M / BTC/USD from evaluate budget
        tickers_eq = sanitize_watchlist(all_tickers)
        total = len(all_tickers)
        tickers = tickers_eq[:25]
        capped = len(tickers_eq) > 25
        dropped = [t for t in all_tickers if t not in set(tickers_eq)]
    except TypeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not tickers:
        return jsonify({
            "ok": True, "results": [], "capped": False, "scored": 0, "total": total,
            "dropped": dropped, "sanitized": True,
        })

    cfg = load_config()
    llm = bool(cfg.get("llm_enabled", True)) and bool(cfg.get("llm_on_scan", True))
    try:
        per_timeout = _num(body.get("timeout_sec") or 18, "timeout_sec", lo=1, hi=60)
    except BadNumber as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        hard_deadline_sec = _num(body.get("deadline_sec", 90), "deadline_sec", lo=1, hi=120)
        pool_number = _num(body.get("pool", 4), "pool", lo=1, hi=5)
        if int(pool_number) != pool_number:
            raise BadNumber("pool must be a whole number")
        pool = int(pool_number)
    except BadNumber as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    deadline_mono = _time.monotonic() + hard_deadline_sec
    request_slots = threading.BoundedSemaphore(pool)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=pool) as ex:
        futs = {
            ex.submit(
                _evaluate_one_with_timeout,
                t,
                cfg,
                per_timeout,
                llm=llm,
                deadline_mono=deadline_mono,
                request_slots=request_slots,
            ): t
            for t in tickers
        }
        for fut in as_completed(futs):
            if _time.monotonic() > deadline_mono:
                # Don't wait unbounded after client timeout budget
                for f in futs:
                    f.cancel()
                break
            try:
                results.append(fut.result(timeout=max(0.1, deadline_mono - _time.monotonic())))
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "ticker": futs[fut],
                    "verdict": None,
                    "llm_side": None,
                    "llm_confidence": None,
                    "llm_thesis": None,
                    "error": str(exc)[:200],
                })

    # Preserve input order
    by_t = {r.get("ticker"): r for r in results}
    ordered = [by_t[t] for t in tickers if t in by_t]
    for t in tickers:
        if t not in by_t:
            ordered.append({
                "ticker": t, "verdict": None, "llm_side": None,
                "llm_confidence": None, "llm_thesis": None, "error": "hard_deadline",
            })

    append_journal(
        "watchlist_evaluate",
        {
            "count": len(tickers),
            "completed": sum(1 for item in ordered if not item.get("error")),
            "llm": llm,
            "pool": pool,
            "dropped": len(dropped),
        },
    )
    return jsonify({
        "ok": True,
        "results": ordered,
        "capped": capped,
        "scored": len(ordered),
        "total": total,
        "sanitized_count": len(tickers_eq),
        "dropped": dropped[:40],
        "llm": llm,
        "screener_only": not llm,
    })


@app.route("/api/signals/generate", methods=["POST"])
def api_generate():
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict):
        body = {}
    ticker = body.get("ticker")
    if ticker is not None and not isinstance(ticker, str):
        return jsonify({"ok": False, "error": "ticker must be text like AAPL"}), 400
    if ticker is not None:
        ticker = ticker.strip().upper() or None
        if ticker and not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
            return jsonify({"ok": False, "error": "ticker must look like AAPL or BRK.B"}), 400
    with _lock:
        cfg = load_config()
    workspace = body.get("workspace")
    if workspace not in (None, "live", "paper"):
        return jsonify(ok=False, error="workspace must be live or paper"), 400
    if workspace == "paper":
        cfg = paper_research_config(cfg)
    elif workspace == "live" and cfg.get("mode") not in ("live_manual", "auto_live"):
        return jsonify(ok=False, error="Select a broker trading mode before live research"), 400
    # Scan (network) outside lock; ingest_signal takes its own lock
    sig = generate_scan_signal(cfg, force=True, ticker=ticker)
    if not sig:
        return jsonify(
            {
                "ok": False,
                "error": "No setups on watchlist (PASS/WATCH/AVOID). See journal for scan notes.",
                "banner": BANNER,
            }
        ), 400
    if workspace:
        sig["workspace"] = workspace
    sig = ingest_signal(sig, research_only=True)  # the button researches; it never trades
    return jsonify({"ok": True, "signal": sig, "banner": BANNER})


@app.route("/api/signals/<sig_id>/review", methods=["POST"])
def api_order_review(sig_id: str):
    """Read-only broker verification; a short-lived ticket never submits an order."""
    return _create_broker_review(sig_id, request.get_json(silent=True) or {})


def _create_broker_review(sig_id: str, body: dict):
    """Shared review path for research ideas and explicit user stock tickets."""
    import broker_router as broker
    if not isinstance(body, dict):
        return jsonify(ok=False, error="JSON object required"), 400
    with _lock:
        cfg = dict(load_config())
        sig = next((dict(s) for s in load_signals() if s.get("id") == sig_id), None)
        if not sig or sig.get("status") != "pending" or signal_workspace(sig) != "live":
            return jsonify(ok=False, error="A pending live idea is required"), 400
        if cfg.get("mode") not in ("live_manual", "auto_live"):
            return jsonify(ok=False, error="Select a verified broker mode first"), 400
        terms = _review_terms(sig, cfg)
        try:
            ticket = order_terms.canonical_order(sig, body.get("order"))
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        if sig.get("manual_order") and (cfg.get("mode") != "live_manual" or ticket != sig["manual_order"]["order"]):
            return jsonify(ok=False, error="Create a new direct ticket to change its terms; manual mode is required"), 409
    context = broker.verify_execution_context()
    if not context.get("ok"):
        return jsonify(ok=False, error=context.get("error") or "Broker identity unavailable"), 400
    identity = context["identity"]
    estimate = {"commission_estimate": None, "guaranteed": False}
    if body.get("order") and identity.get("broker") == "ibkr":
        estimate = broker.estimate_order(dict(ticket, ticker=sig["ticker"], side=sig["side"], broker_identity=identity))
        if sig.get("manual_order") and (estimate.get("contract_verified") is not True
                or not isinstance(estimate.get("contract"), dict) or not estimate["contract"].get("con_id")):
            return jsonify(ok=False, error=estimate.get("error") or "IBKR could not verify this US-listed stock contract"), 409
    with _lock:
        current_cfg = load_config()
        current = next((s for s in load_signals() if s.get("id") == sig_id), None)
        if not current or current.get("status") != "pending" or _review_terms(current, current_cfg) != terms:
            return jsonify(ok=False, error="Idea or account changed during review; refresh"), 409
        if identity != current_cfg.get("broker_identity"):
            return jsonify(ok=False, error="Broker account changed; confirm the current broker mode first"), 409
        restriction = signal_execution_block(current, broker=True)
        if restriction:
            return jsonify(ok=False, error=restriction), 400
        expires = time.time() + 90
        try:
            if current.get("expires_at"):
                expires = min(expires, datetime.fromisoformat(current["expires_at"].replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Invalid signal expiry"), 400
        if expires <= time.time():
            return jsonify(ok=False, error="Signal expired"), 400
        for old_token, item in list(_BROKER_REVIEWS.items()):
            if item["expires"] <= time.time():
                _BROKER_REVIEWS.pop(old_token, None)
        if len(_BROKER_REVIEWS) >= 100:
            return jsonify(ok=False, error="Too many open order reviews; wait for old reviews to expire"), 429
        token = uuid.uuid4().hex + uuid.uuid4().hex
        _BROKER_REVIEWS[token] = {"terms": terms, "identity": identity, "expires": expires, "order": ticket,
                                  "contract": copy.deepcopy(estimate.get("contract"))}
    return jsonify(ok=True, review_token=token, identity=identity,
                   expires_at=datetime.fromtimestamp(expires, timezone.utc).isoformat(),
                   broker=broker.public_status(), order=ticket,
                   costs={"notional_bound": ticket.get("notional_bound"), **estimate,
                          "all_in_maximum": None, "note": "Broker commissions are unknown; no all-in cost guarantee"},
                   capabilities=order_terms.capabilities(identity.get("broker")))


@app.route("/api/signals/<sig_id>/approve", methods=["POST"])
def api_approve(sig_id: str):
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(ok=False, error="JSON object required"), 400
    with _lock:
        cfg = dict(load_config())
        signals = load_signals()
        sig = next((s for s in signals if s["id"] == sig_id), None)
        if not sig:
            return jsonify({"ok": False, "error": "That idea expired or was already handled. New ideas appear in Waiting."}), 404
        if sig.get("status") != "pending":
            return jsonify({"ok": False, "error": f"Signal is {sig.get('status')}"}), 400
        if sig.get("workspace") == "live" and cfg.get("mode") not in ("auto_live", "live_manual"):
            return jsonify({"ok": False, "error": "This is a live-account idea; review it in broker mode or create a separate paper copy"}), 400
        paper_research = signal_workspace(sig) == "paper"
        if paper_research and (cfg.get("mode") in ("auto_live", "live_manual")
                               or cfg.get("_paper_research") or sig.get("source_signal_id")):
            cfg = paper_research_config(cfg)
        broker_approval = not paper_research and cfg.get("mode") in ("auto_live", "live_manual")
        if broker_approval and any(body.get(key) not in (None, "", False) for key in (
                "stop_loss", "take_profit", "trail_pct", "stop_loss_pct", "take_profit_pct",
                "stop", "target", "stop_pct", "target_pct", "bracket")):
            return jsonify({"ok": False, "error": "Broker orders do not support automatic exits; remove the paper stop/target fields"}), 400
        restriction = signal_execution_block(sig, broker=broker_approval)
        if restriction:
            return jsonify({"ok": False, "error": restriction}), 400
        expires = sig.get("expires_at")
        if expires:
            try:
                expired = datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
            except (ValueError, TypeError):
                expired = True
            if expired:
                return jsonify({"ok": False, "error": "This idea has expired or has an invalid time; request a fresh scan"}), 400
        if broker_approval:
            token = body.get("review_token")
            review = _BROKER_REVIEWS.get(token) if isinstance(token, str) else None
            if not review or review["expires"] <= time.time() or review["terms"] != _review_terms(sig, cfg):
                return jsonify(ok=False, error="Order or account review missing, expired or changed; open a fresh review"), 409
            ack_target = str(sig.get("ack_symbol") or sig.get("ticker") or "").upper()
            if review["identity"].get("paper_mode") is False and str(body.get("ack_ticker") or "").strip().upper() != ack_target:
                return jsonify(ok=False, error="Confirm the exact OCC / local option symbol (or stock ticker) before submitting a live order"), 400
            _BROKER_REVIEWS.pop(token)
            sig["review_expires_at"] = datetime.fromtimestamp(review["expires"], timezone.utc).isoformat()
            sig["review_identity"] = review["identity"]
            sig["review_order"] = copy.deepcopy(review["order"])
            sig["review_contract"] = copy.deepcopy(review.get("contract"))
        # Atomic claim under lock — prevents double-approve races
        sig["status"] = "approving"
        for i, s in enumerate(signals):
            if s["id"] == sig_id:
                signals[i] = sig
                break
        save_signals(signals)
        sig = dict(sig)

    # P0.4 — optional paper stop / take-profit from approve body (price or %)
    # Ignore stale screener absolute stop/target unless user typed values.
    # Blank fields → fill_px + 0.8%×stop_r geometry in resolve_exit_prices.
    sig.pop("stop", None)
    sig.pop("target", None)
    for k in (
        "stop_loss",
        "take_profit",
        "stop_loss_pct",
        "take_profit_pct",
        "stop",
        "target",
        "stop_pct",
        "target_pct",
        "bracket",
        "bracket_off",
        "no_bracket",
        "use_bracket_defaults",
        "trail_pct",
    ):
        if k in body:
            sig[k] = body[k]
    if "stop" in body or "target" in body:
        sig["_user_bracket_abs"] = True

    # Manual approve: paper by default. auto_live → gate then broker (no dual-book).
    try:
        if broker_approval:
            result = execute_gated_broker_or_paper(
                sig, cfg, source="manual_approve", via="manual_approve_while_auto_live"
            )
        else:
            result = paper_fill(sig, cfg, source="manual_approve")
            if result.get("ok"):
                result = {
                    **result,
                    "book": "local_paper",
                    "broker": None,
                    "paper_fallback": False,
                }
    except Exception as exc:  # noqa: BLE001
        # Never leave the signal stuck in "approving". Don't put it back to pending either:
        # an order may already have gone out, and a retry could double it.
        err = f"Approve crashed ({type(exc).__name__}: {str(exc)[:160]}) — check the broker/ledger before retrying"
        with _lock:
            signals = load_signals()
            for i, s in enumerate(signals):
                if s["id"] == sig_id and s.get("status") == "approving":
                    unresolved = any((item.get("signal") or {}).get("id") == sig_id
                                     for item in load_ledger().get("pending_broker_orders") or [])
                    s["status"] = "broker_pending" if unresolved else "rejected"
                    s["reject_reason"] = err
                    signals[i] = s
                    break
            save_signals(signals)
            append_journal("approve_error", {"signal_id": sig_id, "error": err})
        return jsonify({"ok": False, "error": err, "banner": BANNER}), 500

    if not result.get("ok"):
        with _lock:
            signals = load_signals()
            for i, s in enumerate(signals):
                if s["id"] == sig_id and s.get("status") == "approving":
                    unresolved = result.get("pending") or any(
                        (item.get("signal") or {}).get("id") == sig_id
                        for item in load_ledger().get("pending_broker_orders") or [])
                    s["status"] = "broker_pending" if unresolved else "pending"
                    signals[i] = s
                    break
            save_signals(signals)
        return jsonify(
            {
                "ok": False,
                "error": result.get("error"),
                "broker": result.get("broker"),
                "book": result.get("book"),
                "pending": bool(result.get("pending")),
                "banner": BANNER,
            }
        ), 400

    with _lock:
        signals = load_signals()
        for i, s in enumerate(signals):
            if s["id"] == sig_id:
                if broker_approval:
                    # Broker execution/reconciliation already persisted under its
                    # submit lock. A local response may be older than that record.
                    sig = dict(s)
                    result["fill"] = sig.get("fill")
                else:
                    signals[i] = sig
                break
        save_signals(signals)
        append_journal(
            "approved",
            {
                "signal_id": sig_id,
                "ticker": sig["ticker"],
                "book": result.get("book"),
                "paper_fallback": bool(result.get("paper_fallback")),
            },
        )
    return jsonify(
        {
            "ok": True,
            "signal": sig,
            "fill": result.get("fill"),
            "broker": result.get("broker"),
            "book": result.get("book"),
            "paper_fallback": bool(result.get("paper_fallback")),
            "pending": bool(result.get("pending")),
            "banner": BANNER,
        }
    )


@app.route("/api/signals/<sig_id>/reject", methods=["POST"])
def api_reject(sig_id: str):
    with _lock:
        signals = load_signals()
        sig = next((s for s in signals if s["id"] == sig_id), None)
        if not sig:
            return jsonify({"ok": False, "error": "That idea expired or was already handled. New ideas appear in Waiting."}), 404
        if sig.get("status") != "pending":
            return jsonify({"ok": False, "error": f"Signal is {sig.get('status')}"}), 400
        body = request.get_json(force=True, silent=True) or {}
        sig["status"] = "rejected"
        sig["reject_reason"] = body.get("reason") or "User rejected"
        for i, s in enumerate(signals):
            if s["id"] == sig_id:
                signals[i] = sig
                break
        save_signals(signals)
        append_journal("rejected", {"signal_id": sig_id, "ticker": sig.get("ticker")})
        return jsonify({"ok": True, "signal": sig, "banner": BANNER})


@app.route("/api/session/start", methods=["POST"])
def api_session_start():
    """Start the selected main session; broker sessions never reset simulated funds."""
    with _BROKER_EXEC_LOCK, _lock:
        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "JSON object required"}), 400
        cfg = load_config()
        if cfg.get("mode") in ("live_manual", "auto_live"):
            if body.get("mode") not in (None, "", cfg["mode"]):
                return jsonify(ok=False, error="Use the paper research controls for simulation"), 400
            cfg.update(session_active=True, session_started_at=_now_iso())
            save_config(cfg)
            append_journal("session_start", {"mode": cfg["mode"], "workspace": "live"})
            return jsonify(ok=True, config=cfg, ledger=load_ledger(), banner=BANNER)
        try:
            make_today = _num(body.get("make_today_usd"), "Today's goal", lo=0.01, hi=1e9)
            beginning_bank = _num(body.get("beginning_bank_usd"), "Starting cash", lo=1, hi=1e9)
            max_loss_in = None
            if body.get("max_session_loss_usd") not in (None, ""):
                max_loss_in = abs(_num(body["max_session_loss_usd"], "Max session loss", lo=-1e9, hi=1e9))
        except BadNumber as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        req_mode = body.get("mode")
        if req_mode not in (None, "", "manual", "auto_paper"):
            return jsonify({"ok": False, "error": "mode must be manual (Ask me first) or auto_paper (Auto fill)"}), 400

        cfg = load_config()
        prev = load_ledger()
        day = _today_str()
        open_pos = [
            p for p in (prev.get("positions") or []) if float(p.get("shares") or 0) > 0
        ]
        # Start archives every fill in the current book, so count them all.
        fills_today = list(prev.get("fills") or [])
        if (open_pos or fills_today) and not body.get("confirm_reset"):
            # Start opens a fresh paper book. Never wipe open positions silently.
            return jsonify({
                "ok": False,
                "needs_confirm": True,
                "open_positions": len(open_pos),
                "fills_today": len(fills_today),
                "error": (
                    f"Starting fresh will archive {len(open_pos)} still-open paper position(s) "
                    f"without realizing them and move {len(fills_today)} of today's trade(s) to history."
                ),
            }), 409

        cfg["daily_profit_target_usd"] = make_today
        cfg["paper_equity"] = beginning_bank
        cfg["paper_cash"] = beginning_bank
        # Respect the user's fill choice (Ask me first stays Ask me first).
        if req_mode in ("manual", "auto_paper"):
            cfg["mode"] = req_mode
        elif cfg.get("mode") not in ("manual", "auto_paper", "auto_live", "live_manual"):
            cfg["mode"] = "manual"
        cfg["session_active"] = True
        cfg["session_started_at"] = _now_iso()
        cfg["loop_enabled"] = True
        cfg["rth_only"] = True  # START always RTH-gates; refuse overnight unsupervised
        # Prefer request override, else default = bank * preset loss pct
        if max_loss_in is not None:
            cfg["max_session_loss_usd"] = max_loss_in
        if cfg.get("max_session_loss_usd") is None:
            preset = get_preset(cfg.get("risk_preset"))
            cfg["max_session_loss_usd"] = round(
                beginning_bank * (float(preset.get("max_daily_loss_pct") or 2) / 100.0), 2
            )
        if body.get("loop_interval_sec") is not None:
            cfg["loop_interval_sec"] = paper_loop_mod.clamp_interval(body.get("loop_interval_sec"))
        else:
            cfg["loop_interval_sec"] = paper_loop_mod.clamp_interval(
                cfg.get("loop_interval_sec", 60)
            )
        save_config(cfg)
        ensure_paper_loop_started()
        get_paper_loop().reset_session_totals()
        get_paper_loop().start()  # ensure running flag on

        daily = dict(prev.get("daily") or {})
        daily[day] = {"trades": 0, "pnl": 0.0, "realized": 0.0}
        ledger = {
            "equity": beginning_bank,
            "cash": beginning_bank,
            "positions": [],
            "fills": [],
            "daily": daily,
        }
        _preserve_broker_state(prev, ledger)
        if open_pos:
            arch = list(prev.get("positions_archive") or [])
            stamp = _now_iso()
            arch = [
                dict(
                    p,
                    archived_at=stamp,
                    archived_by="session_start",
                    still_open=True,
                    realized_at_archive=False,
                )
                for p in open_pos
            ] + arch
            ledger["positions_archive"] = arch[:200]
        # Prefer clear fills for clean Today P&L; archive prior fills if present
        old_fills = prev.get("fills") or []
        if old_fills:
            archive = list(prev.get("fills_archive") or [])
            archive = (old_fills + archive)[:500]
            ledger["fills_archive"] = archive
        save_ledger(ledger)

        append_journal(
            "session_start",
            {
                "make_today_usd": make_today,
                "beginning_bank_usd": beginning_bank,
                "mode": cfg.get("mode"),
                "archived_positions": len(open_pos),
                "loop_enabled": True,
                "rth_only": cfg.get("rth_only", True),
                "loop_interval_sec": cfg.get("loop_interval_sec"),
                "max_session_loss_usd": cfg.get("max_session_loss_usd"),
            },
        )
        cfg_snap = cfg
        ledger_snap = ledger
        loop_st = get_paper_loop().status(cfg)

    # Benchmark anchor: SPY price when this session started
    try:
        spy0 = fetch_last_price("SPY")
        with _lock:
            c2 = load_config()
            c2["session_spy_start"] = float(spy0) if spy0 else None
            c2["session_bank_start"] = beginning_bank
            save_config(c2)
            cfg_snap = c2
    except Exception:
        pass

    # Scan outside lock so /api/state is not blocked during session start
    signal = None
    cfg = cfg_snap
    ledger = ledger_snap
    try:
        sig = generate_scan_signal(cfg_snap, force=True)
        if sig:
            signal = ingest_signal(sig)
            with _lock:
                ledger = load_ledger()
                cfg = load_config()
                loop_st = get_paper_loop().status(cfg)
    except Exception as exc:  # noqa: BLE001
        append_journal("session_start_scan_error", {"error": str(exc)})

    return jsonify(
        {
            "ok": True,
            "config": cfg,
            "ledger": ledger,
            "target": daily_target_progress(cfg, ledger),
            "banner": BANNER,
            "signal": signal,
            "loop": loop_st,
        }
    )


@app.route("/api/bleed/resume", methods=["POST"])
def api_bleed_resume():
    """User reviewed the losing streak and chooses to continue (count restarts now)."""
    with _lock:
        cfg = load_config()
        before = bleed_status(load_ledger(), cfg)
        cfg["bleed_ack_at"] = _now_iso()
        save_config(cfg)
        append_journal("bleed_resume", {"before": before})
        after = bleed_status(load_ledger(), cfg)
    return jsonify({"ok": True, "bleed": after})


@app.route("/api/session/stop", methods=["POST"])
def api_session_stop():
    """Stop paper session: session_active false, pause loop. Fill mode choice is kept."""
    # Stop the decision loop FIRST so a failed save can never leave it trading.
    get_paper_loop().stop()
    with _BROKER_EXEC_LOCK, _lock:
        cfg = load_config()
        cfg["session_active"] = False
        cfg["loop_enabled"] = False
        if cfg.get("live_agent"):
            cfg["live_agent"]["enabled"] = False
        # keep session_started_at / target / equity / mode for reference
        save_config(cfg)
        append_journal("session_stop", {"mode": cfg.get("mode"), "loop_enabled": False})
        ledger = load_ledger()
        return jsonify(
            {
                "ok": True,
                "config": cfg,
                "ledger": ledger,
                "target": daily_target_progress(cfg, ledger),
                "banner": BANNER,
                "loop": get_paper_loop().status(cfg),
            }
        )


@app.route("/api/ledger/reset", methods=["POST"])
def api_ledger_reset():
    with _lock:
        cfg = load_config()
        previous = load_ledger()
        equity = float(cfg.get("paper_equity", 100_000))
        ledger = {
            "equity": equity,
            "cash": equity,
            "positions": [],
            "fills": [],
            "daily": {},
        }
        _preserve_broker_state(previous, ledger)
        save_ledger(ledger)
        append_journal("ledger_reset", {"equity": equity})
        return jsonify({"ok": True, "ledger": ledger})



@app.route("/api/loop/feed")
def api_loop_feed():
    """Polling feed of recent paper-loop decisions. ?since=ISO|id|seq&limit=N"""
    since = request.args.get("since")
    since_seq = request.args.get("since_seq") or request.args.get("seq")
    limit = request.args.get("limit", 80)
    try:
        limit_i = int(limit)
    except (TypeError, ValueError):
        limit_i = 80
    since_iso = None
    since_id = None
    seq_i = None
    if since_seq is not None:
        try:
            seq_i = int(since_seq)
        except (TypeError, ValueError):
            seq_i = None
    elif since:
        # numeric → seq; UUID-ish → id; else ISO
        if since.isdigit():
            seq_i = int(since)
        elif len(since) >= 8 and "-" in since and not since[:4].isdigit():
            since_id = since
        elif since.count("-") >= 2 and ("T" in since or since[:4].isdigit()):
            # could be ISO or UUID; prefer ISO if starts with year
            if since[0].isdigit():
                since_iso = since
            else:
                since_id = since
        else:
            since_id = since
    events = _decision_ring.since(
        since_iso=since_iso, since_id=since_id, since_seq=seq_i, limit=limit_i
    )
    cfg = load_config()
    return jsonify(
        {
            "ok": True,
            "events": events,
            "seq": _decision_ring.seq,
            "loop": get_paper_loop().status(cfg),
            "sim": True,
            "dry_run": True,
        }
    )


_HTTP_WORKER_THREADS = 8
# Each synchronous SSE response holds a Waitress worker until it closes.
# Keep half the workers available for state, polling, and operator controls.
_SSE_STREAM_LIMIT = _HTTP_WORKER_THREADS // 2
_SSE_SLOTS = threading.BoundedSemaphore(_SSE_STREAM_LIMIT)


def _stream_state_payload(payload: dict, previous: dict) -> dict:
    """Omit unchanged terminal history per connection; always send live state.

    Pending ideas, approvals, broker orders, balances, and risk controls are never
    suppressed. Historical quote age alone does not resend an entire archive.
    Every new connection receives a complete history baseline.
    """
    out = dict(payload)
    if not isinstance(payload.get("signals"), dict):
        return out
    groups = dict(payload["signals"])
    for status in ("approved", "rejected", "expired"):
        if status not in groups:
            continue
        stable = []
        for row in groups[status]:
            item = dict(row)
            if isinstance(item.get("quote"), dict):
                item["quote"] = {k: v for k, v in item["quote"].items() if k != "age_sec"}
            stable.append(item)
        digest = hashlib.sha256(json.dumps(_json_safe(stable), sort_keys=True, default=str).encode()).hexdigest()
        if previous.get(status) == digest:
            groups.pop(status)
        previous[status] = digest
    out.update(signals=groups, signal_history_delta=True)
    return out


@app.route("/api/loop/stream")
def api_loop_stream():
    """SSE: decision + loop (~2s) and periodic state_lite (~4–6s). Cache-only buzz."""
    from flask import Response, stream_with_context
    import json as _json
    import time as _time

    slots = _SSE_SLOTS
    if not slots.acquire(blocking=False):
        return jsonify({"ok": False, "error": "stream_capacity", "fallback": "polling"}), 503, {"Retry-After": "15"}
    released = False
    release_lock = threading.Lock()

    def release_slot():
        nonlocal released
        with release_lock:
            if not released:
                released = True
                slots.release()

    def _dump(o, **_kw):
        return _json.dumps(_json_safe(o), default=str)

    def gen():
        last_seq = 0
        tick = 0
        last_lite_at = 0.0
        previous_history = {}
        compact = request.args.get("compact") == "1"
        raw = request.args.get("since_seq") or request.args.get("since")
        if raw and str(raw).isdigit():
            last_seq = int(raw)
        yield "retry: 3000\n\n"
        yield "event: hello\ndata: " + _dump({"ok": True, "sim": True, "dry_run": True}) + "\n\n"
        # Emit an immediate state_lite so the desk paints without waiting
        try:
            lite0 = _build_state_lite()
            if compact:
                lite0 = _stream_state_payload(lite0, previous_history)
            yield "event: state_lite\ndata: " + _dump(lite0, default=str) + "\n\n"
            last_lite_at = _time.time()
        except Exception as exc:  # noqa: BLE001
            yield "event: state_lite\ndata: " + _dump({"ok": False, "error": str(exc)}) + "\n\n"
        while True:
            try:
                events = _decision_ring.since(since_seq=last_seq, limit=20)
                for ev in reversed(events):
                    last_seq = max(last_seq, int(ev.get("seq") or 0))
                    ev_name = str(ev.get("event") or "decision")
                    if ev_name not in ("decision", "intent", "fill", "cancel") and not ev_name.startswith("loop_"):
                        ev_name = "decision"
                    yield f"event: {ev_name}\ndata: " + _dump(ev, default=str) + "\n\n"
                    # Also emit under decision for UI clients that only listen to decision
                    if ev_name in ("intent", "fill", "cancel"):
                        yield "event: decision\ndata: " + _dump(ev, default=str) + "\n\n"
                cfg = load_config()
                st = get_paper_loop().status(cfg)
                yield "event: loop\ndata: " + _dump(st, default=str) + "\n\n"
                now = _time.time()
                # state_lite every ~5s (2–3 loop ticks) — never blocks on Reddit/yfinance
                if now - last_lite_at >= 5.0:
                    try:
                        lite = _build_state_lite()
                        if compact:
                            lite = _stream_state_payload(lite, previous_history)
                        yield "event: state_lite\ndata: " + _dump(lite, default=str) + "\n\n"
                    except Exception as exc:  # noqa: BLE001
                        yield "event: state_lite\ndata: " + _dump({"ok": False, "error": str(exc)}) + "\n\n"
                    last_lite_at = now
            except GeneratorExit:
                raise
            except Exception as exc:  # noqa: BLE001
                yield "event: error\ndata: " + _dump({"ok": False, "error": str(exc)}) + "\n\n"
            tick += 1
            if tick % 5 == 0:
                yield ": ping\n\n"  # keepalive so clients can detect a dead stream
            _time.sleep(2)

    def bounded_stream():
        try:
            yield from gen()
        finally:
            release_slot()

    try:
        response = Response(
            stream_with_context(bounded_stream()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        # Also covers a response closed before its generator is first consumed.
        response.call_on_close(release_slot)
        return response
    except Exception:
        release_slot()
        raise



# --- BEGINNER_UX_MARKET_TODAY ---
_MARKET_TODAY_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_MARKET_TODAY_TTL = 18.0
_MARKET_TODAY_SYMBOLS: list[tuple[str, str, str]] = [
    ("SPY", "S&P 500", "index"),
    ("QQQ", "Nasdaq 100", "index"),
    ("IWM", "Russell 2000", "index"),
    ("DIA", "Dow 30", "index"),
    ("XLF", "Financials", "sector"),
    ("XLK", "Technology", "sector"),
    ("XLE", "Energy", "sector"),
    ("XLV", "Health Care", "sector"),
    ("XLI", "Industrials", "sector"),
    ("XLY", "Consumer Disc.", "sector"),
    ("XLP", "Consumer Staples", "sector"),
    ("XLU", "Utilities", "sector"),
    ("XLB", "Materials", "sector"),
    ("XLRE", "Real Estate", "sector"),
    ("XLC", "Communication", "sector"),
]


def _market_today_payload() -> dict[str, Any]:
    """Batch index/sector pulse for the live desk visualizer. Fail soft. No orders."""
    import time as _time
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except Exception:
        et = timezone.utc

    now = _time.time()
    cached = _MARKET_TODAY_CACHE.get("payload")
    if cached and (now - float(_MARKET_TODAY_CACHE.get("at") or 0)) < _MARKET_TODAY_TTL:
        return cached

    symbols = [s for s, _, _ in _MARKET_TODAY_SYMBOLS]
    meta = {s: (name, kind) for s, name, kind in _MARKET_TODAY_SYMBOLS}
    quotes: dict = {}
    source = "none"
    err = None
    try:
        import data_sources as _ds
        quotes = _ds.yahoo_quote_batch(symbols, timeout=10) or {}
        source = "yahoo_batch"
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:180]
        quotes = {}

    sparks_map: dict[str, list] = {}
    try:
        spark_syms = [s for s, _, k in _MARKET_TODAY_SYMBOLS if k == "index"][:6]
        sparks_map = (_sparks_for_tickers(spark_syms) or {}).get("sparks") or {}
    except Exception:
        sparks_map = {}

    items: list[dict[str, Any]] = []
    for sym in symbols:
        q = quotes.get(sym) or {}
        price = q.get("regularMarketPrice")
        prev = q.get("regularMarketPreviousClose") or q.get("previousClose")
        chg = q.get("regularMarketChangePercent")
        try:
            price_f = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_f = None
        try:
            prev_f = float(prev) if prev is not None else None
        except (TypeError, ValueError):
            prev_f = None
        try:
            chg_f = float(chg) if chg is not None else None
        except (TypeError, ValueError):
            chg_f = None
        if chg_f is None and price_f is not None and prev_f not in (None, 0):
            chg_f = ((price_f - prev_f) / prev_f) * 100.0
        name, kind = meta[sym]
        items.append({
            "symbol": sym,
            "name": name,
            "kind": kind,
            "price": price_f,
            "prev_close": prev_f,
            "change_pct": round(chg_f, 4) if isinstance(chg_f, float) else None,
            "spark": list(sparks_map.get(sym) or []),
            "market_state": q.get("marketState"),
        })

    as_of = datetime.now(timezone.utc).astimezone(et)
    payload = {
        "ok": any(it.get("price") is not None for it in items),
        "source": source,
        "as_of": as_of.isoformat(),
        "as_of_local": as_of.strftime("%I:%M:%S %p ET").lstrip("0"),
        "ttl_sec": _MARKET_TODAY_TTL,
        "items": items,
        "error": err,
    }
    _MARKET_TODAY_CACHE["at"] = now
    _MARKET_TODAY_CACHE["payload"] = payload
    return payload


@app.route("/api/market-today")
def api_market_today():
    """GET /api/market-today — index/sector % change pulse for the beginner desk."""
    try:
        return jsonify(_market_today_payload())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "items": [], "error": str(exc)[:200]})


# --- END BEGINNER_UX_MARKET_TODAY ---


@app.route("/api/sparks")
def api_sparks():
    """GET /api/sparks?tickers=A,B,C — intraday closes for sparklines (cap 6). Fail soft."""
    raw = request.args.get("tickers") or ""
    tickers = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    try:
        return jsonify(_sparks_for_tickers(tickers))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "sparks": {}, "error": str(exc)})



# Hard reject any broker-looking endpoints
@app.route("/api/broker/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE"])
def api_broker_blocked(subpath: str):
    body, code = _reject_broker_api_attempt(f"/api/broker/{subpath}")
    return jsonify(body), code


@app.route("/api/orders", methods=["GET", "POST"])
@app.route("/api/order", methods=["GET", "POST"])
@app.route("/api/alpaca/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE"])
@app.route("/api/ibkr/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE"])
@app.route("/api/tos/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE"])
def api_broker_aliases_blocked(subpath: str = ""):
    body, code = _reject_broker_api_attempt(request.path)
    return jsonify(body), code


@app.route("/api/llm/status")
def api_llm_status():
    with _lock:
        cfg = load_config()
        st = _llm_public_status(cfg)
        return jsonify(
            {
                "configured": st["configured"],
                "model": st["model"],
                "provider": st["provider"],
                "enabled": st["enabled"],
                "on_scan": st["on_scan"],
            }
        )


@app.route("/api/llm/chat", methods=["POST"])
def api_llm_chat():
    if _rate_limited("llm_chat", 12):
        return jsonify({"ok": False, "error": "rate limit exceeded; try again shortly"}), 429
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict) or not isinstance(body.get("message", ""), str) or not isinstance(body.get("ticker", ""), str):
        return jsonify(ok=False, error="Message and optional ticker must be text"), 400
    message = (body.get("message") or "").strip()
    ticker = (body.get("ticker") or "").strip().upper() or None
    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400

    with _lock:
        cfg = load_config()

    analysis = None
    analysis_snippet = None
    if ticker:
        try:
            from screener_logic import analyze_ticker

            analysis = analyze_ticker(ticker)
            if analysis and not analysis.get("error"):
                analysis_snippet = {
                    "ticker": analysis.get("ticker"),
                    "price": analysis.get("price"),
                    "verdict": analysis.get("verdict"),
                    "verdict_text": analysis.get("verdict_text"),
                    "rel_vol": (analysis.get("volume") or {}).get("rel_vol"),
                    "lateness": (analysis.get("entry_quality") or {}).get("label"),
                }
        except Exception as exc:  # noqa: BLE001
            analysis = {"ticker": ticker, "error": str(exc)}

    llm_cfg = _llm_cfg_for_calls(cfg)
    reply = llm_trader.chat(
        [{"role": "user", "content": message}],
        ticker=ticker,
        analysis=analysis,
        cfg=llm_cfg,
    )
    with _lock:
        append_journal(
            "llm_chat",
            {
                "ticker": ticker,
                "message": message[:400],
                "reply_preview": (reply or "")[:400],
                "configured": bool(llm_cfg.get("configured")),
            },
        )
    citations = _screener_citations(analysis) if analysis else []
    if str(reply or "").startswith("[error:"):
        return jsonify(ok=False, error=reply, reply=reply, ticker=ticker, citations=citations,
                       llm=_llm_public_status(cfg)), 503
    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "ticker": ticker,
            "analysis_snippet": analysis_snippet,
            "citations": citations,
            "llm": _llm_public_status(cfg),
        }
    )


@app.route("/api/llm/thesis", methods=["POST"])
def api_llm_thesis():
    if _rate_limited("llm_thesis", 12):
        return jsonify({"ok": False, "error": "rate limit exceeded; try again shortly"}), 429
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict) or not isinstance(body.get("ticker", ""), str):
        return jsonify(ok=False, error="Ticker must be text"), 400
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400
    with _lock:
        cfg = load_config()
    try:
        from screener_logic import analyze_ticker

        analysis = analyze_ticker(ticker)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "ticker": ticker}), 500
    if analysis.get("error"):
        return jsonify({"ok": False, "error": analysis["error"], "ticker": ticker}), 400

    thesis = _research_thesis(analysis, cfg, source="thesis_api")
    with _lock:
        append_journal(
            "llm_thesis_api",
            {
                "ticker": ticker,
                "side": thesis.get("side"),
                "confidence": thesis.get("confidence"),
                "error": thesis.get("error"),
                "model": thesis.get("llm_model"),
            },
        )
    citations = _screener_citations(analysis)
    if isinstance(thesis, dict):
        thesis = dict(thesis)
        thesis["citations"] = citations
    return jsonify(
        {
            "ok": True,
            "ticker": ticker,
            "analysis": {
                "verdict": analysis.get("verdict"),
                "verdict_text": analysis.get("verdict_text"),
                "price": analysis.get("price"),
                "checks": analysis.get("checks"),
                "entry_quality": analysis.get("entry_quality"),
                "rel_vol": (analysis.get("volume") or {}).get("rel_vol"),
                "gap_pct": analysis.get("gap_pct"),
                "research_flags": analysis.get("research_flags"),
            },
            "thesis": thesis,
            "citations": citations,
            "llm": _llm_public_status(cfg),
        }
    )




@app.route("/api/ledger/flatten", methods=["POST"])
def api_ledger_flatten():
    """Force-close local paper positions without broker I/O."""
    body = request.get_json(silent=True) or {}
    if body.get("workspace") not in (None, "paper"):
        return jsonify(ok=False, error="This endpoint closes local paper positions only."), 400
    with _lock:
        cfg = dict(load_config())
    result = paper_flatten_all(cfg)
    code = 200 if result.get("ok") else 207
    return jsonify({**result, "banner": BANNER}), code


@app.route("/api/edge/sample", methods=["GET"])
def api_edge_sample():
    """Light sample stats from recent decisions — sample only, not proof of edge."""
    try:
        limit = int(request.args.get("limit") or 40)
    except (TypeError, ValueError):
        limit = 40
    limit = max(5, min(limit, 200))
    return jsonify({"ok": True, "edge_sample": _edge_sample_stats(limit)})


@app.route("/api/data-quality")
def api_data_quality():
    """Provider/source readiness and freshness signals for the desk."""
    try:
        providers = api_providers.public_pack_status()
    except Exception as exc:  # noqa: BLE001
        providers = {"configured": {}, "error": str(exc)[:120]}
    configured = providers.get("configured") or {}
    buzz_cache = buzz_sources.get_cached_buzz() or {}
    cached_at = buzz_cache.get("cached_at_epoch")
    cache_age = None
    if cached_at:
        cache_age = max(0.0, round(__import__("time").time() - float(cached_at), 1))
    return jsonify({
        "ok": not bool(_CORRUPT_PATHS),
        "healthy": not bool(_CORRUPT_PATHS),
        "corrupt_files": corrupt_files_status(),
        "providers": configured,
        "configured_count": sum(bool(v) for v in configured.values()),
        "fallback_policy": "yfinance -> Yahoo/NASDAQ -> optional Finnhub",
        "quote_cache": {
            "kind": "buzz_disk_memory",
            "age_sec": cache_age,
            "stale": bool(buzz_cache.get("stale")) if buzz_cache else True,
        },
        "note": "Provider availability is not a guarantee of quote freshness; inspect source and timestamp on each signal.",
    })


@app.route("/api/risk/cockpit")
def api_risk_cockpit():
    with _lock:
        cfg, ledger = dict(load_config()), dict(load_ledger())
    return jsonify({"ok": True, "risk": _risk_cockpit(cfg, ledger)})


@app.route("/api/execution/realism")
def api_execution_realism():
    with _lock:
        ledger = dict(load_ledger())
    return jsonify({"ok": True, "execution": _execution_realism(ledger)})


@app.route("/api/strategy/evidence")
def api_strategy_evidence():
    with _lock:
        cfg, ledger = dict(load_config()), dict(load_ledger())
    return jsonify({"ok": True, "evidence": _promotion_gate(cfg, ledger),
                    "edge_sample": _edge_sample_stats(200)})



@app.route("/api/buzz")
def api_buzz():
    """Ticker buzz from Reddit OAuth or public JSON (+ optional Stocktwits)."""
    force = str(request.args.get("force") or "").strip().lower() in ("1", "true", "yes")
    cfg = load_config()
    watchlist = list(cfg.get("watchlist") or DEFAULT_WATCHLIST)
    focus_liquid = str(cfg.get("watchlist_focus") or "liquid").lower() == "liquid"
    # Serve cached evidence immediately; slow social providers run in one worker.
    try:
        payload = buzz_sources.get_cached_buzz()
        expected_key = buzz_sources._buzz_cache_key(watchlist, focus_liquid)
        if payload and payload.get("cache_key") != expected_key:
            payload = None
        refreshing = False
        if force or not payload or payload.get("stale"):
            refreshing = buzz_sources.kick_background_refresh(watchlist, focus_liquid=focus_liquid)
        payload = dict(payload or {"ok": True, "tickers": [], "top": [], "watchlist_hits": [],
                                  "megathreads": [], "errors": [], "stale": True, "cached_at": None})
        payload["refreshing"] = refreshing or buzz_sources._refresh_inflight
    except Exception as e:  # noqa: BLE001
        payload = {
            "ok": False,
            "tickers": [],
            "top": [],
            "watchlist_hits": [],
            "megathreads": [],
            "sources_ok": [],
            "errors": [str(e)],
            "stale": True,
            "cached_at": None,
            "auth_mode": "none",
        }
    # Always surface reddit_auth (no secrets) even on soft failure / cache hit
    try:
        payload = dict(payload)
        payload["reddit_auth"] = buzz_sources.reddit_auth_status()
        payload["auth_mode"] = payload["reddit_auth"].get("mode") or "none"
        payload["reddit_degraded"] = payload["reddit_auth"].get("state") in ("needs_credentials", "cooldown", "error")
    except Exception:
        payload = dict(payload)
        payload.setdefault("reddit_auth", {"configured": False, "mode": "none"})
    return jsonify(payload)


@app.route("/api/buzz/live-chat", methods=["POST"])
def api_buzz_live_chat():
    """Ingest pasted WSB Live Chat text (no Reddit auth). Paper research only."""
    body = request.get_json(silent=True) or {}
    paste = body.get("text") or body.get("paste") or ""
    cfg = load_config()
    watchlist = list(cfg.get("watchlist") or DEFAULT_WATCHLIST)
    focus_liquid = str(cfg.get("watchlist_focus") or "liquid").lower() == "liquid"
    try:
        result = buzz_sources.ingest_live_chat_paste(
            str(paste),
            watchlist=watchlist,
            focus_liquid=focus_liquid,
        )
        # Rebuild buzz so paste merges into cache (network may still fail soft)
        try:
            buzz = buzz_sources.fetch_ticker_buzz(
                watchlist, force=True, focus_liquid=focus_liquid
            )
        except Exception as e:  # noqa: BLE001
            buzz = {"ok": False, "errors": [str(e)], "live_chat": result}
            buzz.update(result)
        return jsonify({"ok": True, "live_chat": result, "buzz": buzz})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 400





@app.route("/api/providers")
def api_providers_status():
    """Which API pack providers are configured (no secrets)."""
    try:
        return jsonify({"ok": True, **api_providers.public_pack_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/research/edgar")
def api_research_edgar():
    sym = (request.args.get("symbol") or request.args.get("ticker") or "").strip().upper()
    try:
        return jsonify(edgar_client.recent_filings(sym, limit=8))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/research/options-flow")
def api_research_options_flow():
    sym = (request.args.get("symbol") or request.args.get("ticker") or "").strip().upper()
    try:
        return jsonify(options_flow.flow_for_ticker(sym))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "auto_trade": False}), 500


@app.route("/api/research/news")
def api_research_news():
    raw = request.args.get("symbols") or request.args.get("watchlist") or ""
    syms = [s.strip().upper() for s in raw.replace(";", ",").split(",") if s.strip()]
    if not syms:
        cfg = load_config()
        syms = list(cfg.get("watchlist") or DEFAULT_WATCHLIST)[:12]
    try:
        return jsonify(news_stream.watchlist_news(syms))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "items": [], "error": str(exc)}), 500


@app.route("/api/research/intelligence")
def api_research_intelligence():
    raw = request.args.get("symbols") or request.args.get("ticker") or ""
    syms = [s.strip().upper() for s in raw.replace(";", ",").split(",") if s.strip()]
    if not syms:
        syms = list((load_config().get("watchlist") or DEFAULT_WATCHLIST)[:12])
    try:
        news = news_stream.watchlist_news(syms, force=bool(request.args.get("force")))
        items = list(news.get("items") or [])
        filings = []
        focus = syms[0] if syms else None
        if focus:
            filings = (edgar_client.recent_filings(focus, limit=6) or {}).get("filings") or []
        timeline = news_intelligence.build_timeline(items, filings)
        analysis = news_intelligence.analyze_items(items)
        return jsonify(
            {
                "ok": True,
                "ticker": focus,
                "timeline": timeline[:40],
                "analysis": analysis,
                "digest": analysis.get("digest") or [],
                "alerts": analysis.get("alerts") or [],
                "provider_reliability": news_intelligence.provider_reliability(),
                "display_only": True,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:200], "display_only": True}), 500


@app.route("/api/research/reaction")
def api_research_reaction():
    symbol = (request.args.get("symbol") or request.args.get("ticker") or "").strip().upper()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol_required", "display_only": True}), 400
    try:
        news = news_stream.news_for_symbol(symbol, limit=20)
        import data_sources
        bars = data_sources.yahoo_chart_daily(symbol, days=180)
        return jsonify({"ok": True, "study": news_intelligence.reaction_study(symbol, news, bars)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:200], "display_only": True}), 500


@app.route("/api/research/link", methods=["POST"])
def api_research_link():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not body.get("signal_id") or not body.get("headline"):
        return jsonify({"ok": False, "error": "signal_id_and_headline_required"}), 400
    append_journal(
        "research_link",
        {
            "signal_id": str(body["signal_id"])[:120],
            "ticker": str(body.get("ticker") or "").upper()[:20],
            "headline": str(body["headline"])[:300],
            "link": str(body.get("link") or "")[:500],
            "source": str(body.get("source") or "")[:80],
            "display_only": True,
        },
    )
    return jsonify({"ok": True, "linked": True, "display_only": True})


@app.route("/api/research/macro")
def api_research_macro():
    sym = (request.args.get("symbol") or request.args.get("ticker") or "").strip().upper() or None
    cfg = load_config()
    try:
        return jsonify(
            {
                "ok": True,
                "calendar": macro_calendar.calendar_snapshot(force=bool(request.args.get("force"))),
                "risk": macro_calendar.risk_adjustment(sym, cfg),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/research/social")
def api_research_social():
    """Read-only WSB attention snapshot; never an execution recommendation."""
    cfg = load_config()
    if not cfg.get("social_enabled"):
        return jsonify({
            "ok": False,
            "enabled": False,
            "error": "social_disabled",
            "display_only": True,
            "attention_only": True,
        })
    watchlist = list(cfg.get("watchlist") or DEFAULT_WATCHLIST)
    try:
        return jsonify(social_intelligence.snapshot(
            watchlist,
            force=str(request.args.get("force") or "").lower() in {"1", "true", "yes"},
        ))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:200], "display_only": True}), 500

@app.route("/api/health")
def api_health():
    cfg = load_config()
    providers = {}
    try:
        providers = api_providers.public_pack_status()
    except Exception as exc:  # noqa: BLE001
        providers = {"error": str(exc)[:120], "configured": {}}
    return jsonify(
        {
            "ok": not _CORRUPT_PATHS and not getattr(_decision_ring, "_load_error", None),
            "app_id": "tomahawk-desk",
            "instance": {"source_root": str(APP_DIR.resolve()), "data_root": str(DATA_DIR.resolve())},
            "startup": _startup_status(),
            "code": code_version.status(),
            "recovery": worker_health(),
            "pid": os.getpid(),
            "banner": BANNER,
            "port": DESK_PORT,
            "corrupt_files": corrupt_files_status(),
            "live_locked": False,
            "research_draft": True,
            "broker": _broker_public_status(),
            "llm": _llm_public_status(cfg),
            "loop": get_paper_loop().status(cfg),
            "providers": providers,
            "api_pack": providers.get("configured") or {},
        }
    )


# True only in the process that serves the desk (set in __main__); the shutdown
# endpoint must never stop a test runner or an importing tool.
_SERVING = False


def worker_health():
    expected = bool(_SERVING)
    rows = {"signal-scan": bool(_bg_thread and _bg_thread.is_alive())}
    companion = globals().get("_research_companion")
    if companion and not companion.stop.is_set():
        rows.update({name: bool(companion._threads.get(name) and companion._threads[name].is_alive())
                     for name in ("moss-daily-schedule", "moss-paper-workday", "moss-broker-read-only")})
    if _bg_stop.is_set():
        rows.pop("signal-scan", None)
    return {"expected": expected, "workers": rows,
            "missing": [name for name, alive in rows.items() if expected and not alive],
            "memory_error": getattr(_decision_ring, "_load_error", None)}


@app.post("/api/desk/recover")
def api_desk_recover():
    body = request.get_json(silent=True) or {}
    if not _is_loopback_request():
        return jsonify(ok=False, error="Recovery is local-only"), 403
    if not _SERVING or not isinstance(body, dict) or body.get("expected_pid") != os.getpid():
        return jsonify(ok=False, error="Recovery requires the current serving process"), 409
    before = worker_health()
    if _CORRUPT_PATHS or before["memory_error"]:
        return jsonify(ok=False, error="Stored evidence needs recovery; no automatic reset performed"), 409
    with _worker_start_lock:
        if "signal-scan" in before["missing"]:
            _start_scan_worker()
        if any(name.startswith("moss-") for name in before["missing"]):
            _research_companion.start()
    after = worker_health()
    repaired = [name for name in before["missing"] if name not in after["missing"]]
    if repaired:
        append_journal("workers_recovered", {"workers": repaired})
    return jsonify(ok=not after["missing"], repaired=repaired, recovery=after)


def _automatic_restart_error():
    """No automatic process restart while a session or broker exposure needs supervision."""
    if load_config().get("session_active"):
        return "Active trading session; update restart deferred"
    if _CORRUPT_PATHS or getattr(_decision_ring, "_load_error", None):
        return "Stored evidence needs recovery; restart cannot repair it"
    if _live_agent.cycle_lock.locked():
        return "Agent cycle in progress; restart deferred"
    if _broker_is_configured():
        try:
            book = _broker_book_cached()
            import broker_router
            orders = broker_router.get_open_orders()
            if not isinstance(book, dict) or not book.get("ok") or book.get("risk_ready") is not True or not isinstance(book.get("positions"), list):
                return "Broker state unavailable; automatic restart deferred"
            if book["positions"] or not orders.get("ok") or not isinstance(orders.get("orders"), list) or orders["orders"]:
                return "Broker positions or orders are present or unverified; automatic restart deferred"
        except Exception:
            return "Broker state unavailable; automatic restart deferred"
    return None


def _exit_desk_process(automatic=False) -> None:
    """Stop the desk once no ledger write is in progress; the launcher restarts it."""
    time.sleep(0.8)  # let the HTTP response reach the launcher
    # _BROKER_SUBMIT_LOCK: no broker submission is in flight; _lock: no ledger write.
    with _BROKER_SUBMIT_LOCK, _lock:
        if automatic and _automatic_restart_error():
            append_journal("app_stop_cancelled", {"reason": "Automatic restart conditions changed"})
            return
        pending = load_ledger().get("pending_broker_orders") or []
        if pending:  # an order started after the request was accepted
            append_journal("app_stop_cancelled", {"reason": "broker order unresolved", "pending": len(pending)})
            return
        append_journal("app_stop", {"reason": "restart_requested", "pid": os.getpid()})
        release_instance_lock()
        os._exit(0)


@app.route("/api/desk/shutdown", methods=["POST"])
def api_desk_shutdown():
    """Stop this desk so the launcher can start updated code. Never while a broker order is unresolved."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or body.get("confirm") != "RESTART":
        return jsonify(ok=False, error="Send confirm=RESTART to stop the desk for a restart"), 400
    if not _is_loopback_request():
        return jsonify(ok=False, error="Only this computer can restart the desk"), 403
    if not _SERVING:
        return jsonify(ok=False, error="This process is not the running desk server"), 409
    automatic = body.get("automatic") is True
    if automatic:
        if body.get("expected_pid") != os.getpid():
            return jsonify(ok=False, error="Process changed; check health again"), 409
        error = _automatic_restart_error()
        if error:
            return jsonify(ok=False, error=error, deferred=True), 409
    with _lock:
        pending = load_ledger().get("pending_broker_orders") or []
    if pending:
        return jsonify(ok=False, error=f"{len(pending)} broker order(s) still unresolved; restart after reconciliation"), 409
    target = (lambda: _exit_desk_process(automatic=True)) if automatic else _exit_desk_process
    threading.Thread(target=target, name="desk-shutdown", daemon=True).start()
    return jsonify(ok=True, stopping=True, pid=os.getpid())


def recover_interrupted_approvals():
    """Restore broker evidence before any background scanner can run."""
    with _lock:
        ledger = load_ledger()
        pending = {p.get("signal", {}).get("id") for p in ledger.get("pending_broker_orders", [])}
        broker_fills = {f.get("signal_id"): f for f in ledger.get("broker_fills", [])}
        paper_fills = {f.get("signal_id"): f for f in reversed(ledger.get("fills", []))}
        signals = load_signals()
        changed = []
        for sig in signals:
            if sig.get("status") != "approving":
                continue
            fills = paper_fills if signal_workspace(sig) == "paper" else broker_fills
            if sig.get("id") in pending:
                sig["status"] = "broker_pending"
            elif sig.get("id") in fills:
                sig.update(status="approved", fill=fills[sig["id"]])
            else:
                sig.update(status="rejected", reject_reason="Review interrupted by restart; create a fresh idea")
            changed.append(sig.get("id"))
        if changed:
            save_signals(signals)
            append_journal("approving_recovered", {"signal_ids": changed})


import desk_workbench
desk_workbench.register(app, __import__("sys").modules[__name__])
import broker_controls
broker_controls.register(app, __import__("sys").modules[__name__])
import live_ticket
live_ticket.register(app, __import__("sys").modules[__name__])
import live_agent
_live_agent = live_agent.register(app, __import__("sys").modules[__name__])
import research_companion
_research_companion = research_companion.register(app, __import__("sys").modules[__name__])
import options_desk
_options_desk = options_desk.register(app, __import__("sys").modules[__name__])
import market_catalog
market_catalog.register(app, __import__("sys").modules[__name__])
import desk_operations
desk_operations.register(app, __import__("sys").modules[__name__], _research_companion)
import research_studio
research_studio.register(app, __import__("sys").modules[__name__], _research_companion)
import market_universe
market_radar.configure_discovery(lambda: market_universe.radar_candidates(__import__("sys").modules[__name__]))
import market_watch
market_watch.register(app, __import__("sys").modules[__name__])
market_events.configure(DATA_DIR)
market_events.start_background()
wsb_monitor.register(__import__("sys").modules[__name__])
import desk_day
desk_day.register(app, __import__("sys").modules[__name__])
import desk_backups
desk_backups.register(app, __import__("sys").modules[__name__])
import fox_workspace
fox_workspace.register(app, __import__("sys").modules[__name__])

# Claim the instance before starting any background work.
if __name__ == "__main__":
    acquire_instance_lock()
    atexit.register(release_instance_lock)
    import error_reporting
    error_reporting.init()  # no-op unless SENTRY_DSN is set; before background threads start
    recover_interrupted_approvals()

# Start background on import / run (TOMAHAWK_NO_BG=1 skips — used by tests)
if (os.environ.get("TOMAHAWK_NO_BG") or "").strip().lower() not in ("1", "true", "yes", "on"):
    start_bg()
    _research_companion.start()


if __name__ == "__main__":
    # Ensure data files exist
    load_config()
    load_ledger()
    load_signals()
    load_journal()
    append_journal("app_start", {"banner": BANNER, "port": DESK_PORT, "host": DESK_HOST,
                                 "code": code_version.mark_started()})
    _SERVING = True
    if os.environ.get("TOMAHAWK_DEV_SERVER", "").lower() in ("1", "true", "yes"):
        app.run(host=DESK_HOST, port=DESK_PORT, debug=False, use_reloader=False, threaded=True)
    else:
        try:
            from waitress import serve
        except ImportError as exc:
            raise RuntimeError("waitress is required for production startup; install requirements.txt") from exc
        serve(app, host=DESK_HOST, port=DESK_PORT, threads=_HTTP_WORKER_THREADS)
