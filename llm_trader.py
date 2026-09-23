"""
Gemini-powered day-trade research LLM helpers.

Uses Generative Language REST API (no google-genai SDK required).
API key from GEMINI_API_KEY or GOOGLE_API_KEY (env / project .env).
Never logs or returns the raw API key.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests

# Prefer data_sources._load_env so search paths stay consistent.
try:
    import data_sources as _ds

    _ds._load_env()  # idempotent (setdefault)
except Exception:
    def _fallback_load_env() -> None:
        candidates = [Path.cwd() / ".env", Path(__file__).parent / ".env"]
        for env_path in candidates:
            if not env_path.exists():
                continue
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break

    _fallback_load_env()

DEFAULT_MODEL = "gemini-3.6-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


_SECRET_PARAM_RE = re.compile(r"((?:api_?key|key|token|apiKey|access_token)=)[^&\s'\"]+", re.I)
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


def redact_secrets(text: str, *known: str | None) -> str:
    """Strip API keys from error text before it is stored or shown."""
    out = str(text or "")
    for k in known:
        if k and len(k) >= 8:
            out = out.replace(k, "[redacted]")
    out = _SECRET_PARAM_RE.sub(r"\1[redacted]", out)
    return _GOOGLE_KEY_RE.sub("[redacted]", out)
RAW_TRUNCATE = 4000

def screener_citations(analysis: dict | None) -> list[dict]:
    """Structured screener fact citations for thesis/chat UI."""
    if not isinstance(analysis, dict):
        return []
    vol = analysis.get("volume") or {}
    sma = analysis.get("sma") or {}
    ts = analysis.get("as_of") or analysis.get("ts")
    try:
        from datetime import datetime, timezone
        ts = ts or datetime.now(timezone.utc).isoformat()
    except Exception:
        ts = ts or ""
    cites = [
        {"key": "price", "label": "Price", "value": analysis.get("price")},
        {"key": "rel_vol", "label": "Rel vol", "value": vol.get("rel_vol")},
        {"key": "verdict", "label": "Verdict", "value": analysis.get("verdict")},
        {
            "key": "sma",
            "label": "SMAs",
            "value": (
                f"10={sma.get('sma10')} 20={sma.get('sma20')} 50={sma.get('sma50')} "
                f"above={sma.get('above_10')}/{sma.get('above_20')}/{sma.get('above_50')}"
            ),
        },
        {"key": "gap_pct", "label": "Gap %", "value": analysis.get("gap_pct")},
        {"key": "as_of", "label": "As of", "value": ts},
    ]
    return [c for c in cites if c.get("value") not in (None, "", "None")]



THESIS_SYSTEM = (
    "You are a day-trade research LLM for a paper/research signal desk. "
    "Use ONLY the provided screener facts — do not invent prices, volume, or news. "
    "You may disagree with the playbook PASS/WATCH/AVOID verdict. "
    "Answer an explicit horizon question: will the ticker be higher, lower, or flat "
    "over the next N minutes (given in the prompt). Map higher→buy, lower→sell, flat→flat. "
    "This is a research draft only — no broker claims, no order placement, "
    "no guarantee of profits. Respond with a single JSON object matching the schema."
)

CHAT_SYSTEM = (
    "You are a day-trade research assistant for a paper/research desk. "
    "Be concise and practical. If screener analysis is provided, ground answers in it. "
    "Do not claim to place broker orders. Research draft only — not financial advice."
)

THESIS_SCHEMA_HINT = """
Return ONLY JSON with this shape:
{
  "horizon": "higher" | "lower" | "flat",
  "side": "buy" | "sell" | "flat",
  "confidence": <number 0-1>,
  "thesis": "<short trade thesis supporting the horizon>",
  "entry_plan": "<how/when to enter>",
  "stop_idea": "<stop idea>",
  "target_idea": "<target idea>",
  "risks": ["<risk1>", "..."],
  "playbook_agree": true | false,
  "notes": "<optional notes>"
}
Map horizon higher→side buy, lower→side sell, flat→side flat.
""".strip()


def load_llm_config() -> dict[str, Any]:
    """Load Gemini config from environment (after .env). Never expose key value."""
    try:
        import data_sources as ds

        ds._load_env()
    except Exception:
        pass

    api_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    model = (os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return {
        "provider": "gemini",
        "api_key": api_key,
        "configured": bool(api_key),
        "model": model,
    }


def _strip_json_fences(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", t, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    # Partial fence at start
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, count=1, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_json_object(text: str) -> Optional[dict]:
    cleaned = _strip_json_fences(text)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Find first { ... } block
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(cleaned[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
    return None



# Shared RPM / daily Gemini budget (process-wide, paper desk)
import threading as _threading
import time as _time

class _GeminiBudget:
    def __init__(self, rpm: int = 30, daily: int = 500) -> None:
        self.rpm = rpm
        self.daily = daily
        self._lock = _threading.Lock()
        self._window: list[float] = []
        self._day = ""
        self._day_count = 0

    def _roll(self, now: float) -> None:
        try:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo

            day = _dt.fromtimestamp(now, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            day = _time.strftime("%Y-%m-%d", _time.localtime(now))
        if day != self._day:
            self._day = day
            self._day_count = 0
        cutoff = now - 60.0
        self._window = [t for t in self._window if t >= cutoff]

    def acquire(self) -> None:
        with self._lock:
            now = _time.time()
            self._roll(now)
            if self._day_count >= self.daily:
                raise RuntimeError("gemini_daily_budget_exceeded")
            if len(self._window) >= self.rpm:
                raise RuntimeError("gemini_rpm_budget_exceeded")
            self._window.append(now)
            self._day_count += 1

    def status(self) -> dict:
        with self._lock:
            now = _time.time()
            self._roll(now)
            return {
                "rpm_used": len(self._window),
                "rpm_limit": self.rpm,
                "daily_used": self._day_count,
                "daily_limit": self.daily,
                "day": self._day,
            }

_GEMINI_BUDGET = _GeminiBudget(
    rpm=int(__import__("os").environ.get("GEMINI_RPM", "30") or 30),
    daily=int(__import__("os").environ.get("GEMINI_DAILY", "500") or 500),
)


def gemini_budget_status() -> dict:
    return _GEMINI_BUDGET.status()


def gemini_generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    json_mode: bool = False,
    cfg: Optional[dict] = None,
    timeout_sec: float = 45,
) -> str:
    """Call Gemini generateContent REST. Raises RuntimeError on hard failures."""
    cfg = cfg or load_llm_config()
    api_key = cfg.get("api_key") or ""
    if not api_key:
        raise RuntimeError("missing_gemini_api_key")

    _GEMINI_BUDGET.acquire()

    model = cfg.get("model") or DEFAULT_MODEL
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    contents: list[dict] = [{"role": "user", "parts": [{"text": prompt}]}]
    body: dict[str, Any] = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"] = {
            "responseMimeType": "application/json",
            "temperature": 0.4,
        }
    else:
        body["generationConfig"] = {"temperature": 0.5}

    try:
        timeout = float(timeout_sec) if timeout_sec is not None else 45.0
        if timeout <= 0:
            timeout = 45.0
    except (TypeError, ValueError):
        timeout = 45.0

    try:
        # Key goes in a header, never the URL: requests' error text includes the URL,
        # and that text flows into signals/journal/UI.
        r = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise RuntimeError("llm_timeout") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"gemini_request_failed: {redact_secrets(str(exc), api_key)}") from exc

    if r.status_code != 200:
        # Do not include request URL (has key) or full body secrets
        detail = ""
        try:
            err = r.json()
            detail = (err.get("error") or {}).get("message") or str(err)[:200]
        except Exception:
            detail = (r.text or "")[:200]
        raise RuntimeError(f"gemini_http_{r.status_code}: {redact_secrets(detail, api_key)}")

    try:
        data = r.json()
    except Exception as exc:
        _note_call_cost(estimate_gemini_cost(), "gemini_bad_json")
        raise RuntimeError("gemini_bad_json") from exc

    usage = data.get("usageMetadata") or {}
    _note_call_cost(
        estimate_gemini_cost(
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=(usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
            if usage else None,
        ),
        "gemini",
    )

    candidates = data.get("candidates") or []
    if not candidates:
        # blocked / empty
        feedback = data.get("promptFeedback") or {}
        raise RuntimeError(f"gemini_empty_candidates: {feedback}")

    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    out = "\n".join(t for t in texts if t).strip()
    if not out:
        raise RuntimeError("gemini_empty_text")
    return out


def _analysis_context_blob(analysis: dict) -> str:
    """Compact, JSON-safe screener facts for the prompt."""
    keep_keys = [
        "ticker",
        "price",
        "change_dollar",
        "change_pct",
        "verdict",
        "verdict_text",
        "sma",
        "volume",
        "sector_name",
        "sector",
        "entry_quality",
        "checks",
        "earnings",
        "sources",
    ]
    slim = {k: analysis.get(k) for k in keep_keys if k in analysis}
    try:
        return json.dumps(slim, default=str, indent=2)
    except Exception:
        return str(slim)


def safe_confidence(v: Any) -> Optional[float]:
    """Model confidence -> 0..1, or None when it can't be trusted.

    Rejects booleans, NaN/inf, negatives and values > 100. A value in (1, 100]
    is read as a percent (85 -> 0.85). "85%" strings are accepted as percents.
    """
    import math

    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        t = v.strip()
        pct = t.endswith("%")
        try:
            f = float(t.rstrip("%").strip())
        except ValueError:
            return None
        if pct:
            f = f / 100.0
    else:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(f) or f < 0:
        return None
    if f > 1.0:
        if f <= 100.0:
            f = f / 100.0
        else:
            return None
    return f


def _normalize_thesis(obj: dict, raw: str, model: str) -> dict[str, Any]:
    horizon = str(obj.get("horizon") or "").lower().strip()
    if horizon in ("higher", "up", "upside"):
        horizon = "higher"
    elif horizon in ("lower", "down", "downside"):
        horizon = "lower"
    elif horizon in ("flat", "unchanged", "sideways"):
        horizon = "flat"
    else:
        horizon = ""
    side = str(obj.get("side") or "").lower().strip()
    parse_error: Optional[str] = None
    # Schema: horizon maps to side. If the model gives BOTH and they disagree, the
    # output is self-contradictory -> hold (never trade the opposite of "side").
    if horizon:
        mapped = {"higher": "buy", "lower": "sell", "flat": "flat"}[horizon]
        if side in ("buy", "sell", "flat") and side != mapped:
            parse_error = "side_horizon_conflict"
            side = "flat"
        else:
            side = mapped
    elif side not in ("buy", "sell", "flat"):
        side = "flat"
    if not horizon:
        horizon = {"buy": "higher", "sell": "lower", "flat": "flat"}.get(side, "flat")
    conf_v = safe_confidence(obj.get("confidence", 0))
    if conf_v is None:
        parse_error = parse_error or "bad_confidence"
        conf = 0.0
        side = "flat"
    else:
        conf = conf_v
    if parse_error:
        conf = 0.0
        side = "flat"
        horizon = "flat"
    risks = obj.get("risks") or []
    if isinstance(risks, str):
        risks = [risks]
    if not isinstance(risks, list):
        risks = [str(risks)]
    risks = [str(r) for r in risks][:12]
    agree = obj.get("playbook_agree")
    if not isinstance(agree, bool):
        agree = str(agree).lower() in ("1", "true", "yes")

    return {
        "side": side,
        "confidence": round(conf, 3),
        "thesis": str(obj.get("thesis") or "").strip() or "(no thesis)",
        "entry_plan": str(obj.get("entry_plan") or "").strip(),
        "stop_idea": str(obj.get("stop_idea") or "").strip(),
        "target_idea": str(obj.get("target_idea") or "").strip(),
        "risks": risks,
        "playbook_agree": agree,
        "notes": str(obj.get("notes") or "").strip(),
        "horizon": horizon,
        "llm_model": model,
        "llm_raw": (raw or "")[:RAW_TRUNCATE],
        "error": parse_error,
        "model_cost_usd": 0.0,
    }


def trade_thesis_from_analysis(
    analysis: dict,
    cfg: Optional[dict] = None,
    *,
    timeout_sec: float = 45,
) -> dict[str, Any]:
    """Produce structured trade thesis JSON from screener analysis via Gemini."""
    cfg = cfg or load_llm_config()
    model = cfg.get("model") or DEFAULT_MODEL

    if not cfg.get("configured") or not cfg.get("api_key"):
        return {
            "side": "flat",
            "confidence": 0.0,
            "thesis": "",
            "entry_plan": "",
            "stop_idea": "",
            "target_idea": "",
            "risks": [],
            "playbook_agree": False,
            "notes": "",
            "llm_model": model,
            "llm_raw": "",
            "error": "missing_gemini_api_key",
        }

    ticker = (analysis.get("ticker") or "?").upper()
    horizon_min = decision_horizon_min(cfg if isinstance(cfg, dict) else None)
    prompt = (
        f"Screener analysis for {ticker}:\n"
        f"{_analysis_context_blob(analysis)}\n\n"
        f"HORIZON QUESTION: Over the next {horizon_min} minutes, is {ticker} more likely "
        f"higher, lower, or flat? Set horizon accordingly and map to side "
        f"(higher=buy, lower=sell, flat=flat).\n\n"
        f"{THESIS_SCHEMA_HINT}\n"
        "Base the thesis only on these facts. Research draft — no broker claims."
    )
    try:
        raw = gemini_generate(prompt, system=THESIS_SYSTEM, json_mode=True, cfg=cfg, timeout_sec=timeout_sec)
    except RuntimeError as exc:
        err = str(exc)
        if "missing_gemini_api_key" in err:
            code = "missing_gemini_api_key"
        elif "llm_timeout" in err or "Read timed out" in err or "timed out" in err.lower():
            code = "llm_timeout"
        else:
            code = "gemini_error"
        return {
            "side": "flat",
            "confidence": 0.0,
            "thesis": "",
            "entry_plan": "",
            "stop_idea": "",
            "target_idea": "",
            "risks": [],
            "playbook_agree": False,
            "notes": err[:300],
            "llm_model": model,
            "llm_raw": "",
            "error": code if code in ("missing_gemini_api_key", "llm_timeout") else err[:200],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "side": "flat",
            "confidence": 0.0,
            "thesis": "",
            "entry_plan": "",
            "stop_idea": "",
            "target_idea": "",
            "risks": [],
            "playbook_agree": False,
            "notes": str(exc)[:300],
            "llm_model": model,
            "llm_raw": "",
            "error": f"gemini_exception: {exc}"[:200],
        }

    parsed = _extract_json_object(raw)
    if not parsed:
        return {
            "side": "flat",
            "confidence": 0.0,
            "thesis": "",
            "entry_plan": "",
            "stop_idea": "",
            "target_idea": "",
            "risks": [],
            "playbook_agree": False,
            "notes": "Failed to parse model JSON",
            "llm_model": model,
            "llm_raw": (raw or "")[:RAW_TRUNCATE],
            "error": "parse_error",
        }
    out = _normalize_thesis(parsed, raw, model)
    out["horizon_min"] = decision_horizon_min(cfg if isinstance(cfg, dict) else None)
    out["model_cost_usd"] = last_call_cost()  # recorded in gemini_generate
    out["brain_mode"] = "gemini"
    return out


def chat(
    messages: list[dict[str, str]],
    *,
    ticker: Optional[str] = None,
    analysis: Optional[dict] = None,
    cfg: Optional[dict] = None,
) -> str:
    """Free-form research chat. Returns reply text or an error string."""
    cfg = cfg or load_llm_config()
    if not cfg.get("configured") or not cfg.get("api_key"):
        return "[error: missing_gemini_api_key] Set GEMINI_API_KEY in .env and restart."

    parts: list[str] = []
    if ticker:
        parts.append(f"User is asking about ticker: {ticker.upper()}")
    if analysis and not analysis.get("error"):
        parts.append("Screener context:\n" + _analysis_context_blob(analysis))
    elif analysis and analysis.get("error"):
        parts.append(f"Screener error for context: {analysis.get('error')}")

    transcript = []
    for m in messages or []:
        role = (m.get("role") or "user").lower()
        content = (m.get("content") or "").strip()
        if not content:
            continue
        label = "Assistant" if role in ("assistant", "model") else "User"
        transcript.append(f"{label}: {content}")
    if not transcript:
        return "[error: empty_message]"

    parts.append("Conversation:\n" + "\n".join(transcript))
    parts.append("Reply as the research assistant (plain text, concise).")
    prompt = "\n\n".join(parts)

    try:
        return gemini_generate(prompt, system=CHAT_SYSTEM, json_mode=False, cfg=cfg)
    except RuntimeError as exc:
        return f"[error: {exc}]"
    except Exception as exc:  # noqa: BLE001
        return f"[error: gemini_exception: {exc}]"


def status_public() -> dict[str, Any]:
    """Safe status for API — never includes the key."""
    cfg = load_llm_config()
    return {
        "configured": bool(cfg.get("configured")),
        "model": cfg.get("model") or DEFAULT_MODEL,
        "provider": "gemini",
    }

# ---------------------------------------------------------------------------
# Brain modes (gemini | mock | jev), horizon, shadow auditor, cost ledger
# ---------------------------------------------------------------------------

TYPESAFE_API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_HORIZON_MIN = 20
VALID_BRAIN_MODES = ("gemini", "mock", "jev")

# Process-wide model-cost ledger (paper research estimate)
_COST_LOCK = _threading.Lock()
_COST_DAY = ""
_COST_USD = 0.0
_COST_CALLS = 0


def _cost_day_roll() -> None:
    global _COST_DAY, _COST_USD, _COST_CALLS
    # Align with session PnL day boundary (America/New_York)
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        day = _dt.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        day = _time.strftime("%Y-%m-%d", _time.localtime())
    if day != _COST_DAY:
        _COST_DAY = day
        _COST_USD = 0.0
        _COST_CALLS = 0


_CALL_COST = _threading.local()


def _note_call_cost(usd: float, brain: str) -> None:
    """Record a billed call's cost once, at the HTTP layer (parse failures included)."""
    record_model_cost(usd, meta={"brain": brain})
    _CALL_COST.last = float(usd or 0.0)


def last_call_cost() -> float:
    """Cost of the most recent Gemini call on this thread (already recorded)."""
    return float(getattr(_CALL_COST, "last", 0.0) or 0.0)


def record_model_cost(usd: float, *, meta: Optional[dict] = None) -> float:
    """Accumulate estimated model USD for today. Returns new total."""
    global _COST_USD, _COST_CALLS
    try:
        add = max(0.0, float(usd or 0))
    except (TypeError, ValueError):
        add = 0.0
    with _COST_LOCK:
        _cost_day_roll()
        _COST_USD = round(_COST_USD + add, 6)
        _COST_CALLS += 1
        return _COST_USD


def model_cost_today() -> dict[str, Any]:
    with _COST_LOCK:
        _cost_day_roll()
        return {
            "day": _COST_DAY,
            "model_usd": round(_COST_USD, 4),
            "calls": _COST_CALLS,
        }


def estimate_gemini_cost(
    *,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> float:
    """Reasonable Gemini Flash-class estimate; flat fallback when tokens unknown."""
    try:
        in_rate = float(os.environ.get("GEMINI_USD_PER_1M_INPUT", "0.075") or 0.075)
        out_rate = float(os.environ.get("GEMINI_USD_PER_1M_OUTPUT", "0.30") or 0.30)
        flat = float(os.environ.get("GEMINI_FLAT_USD_PER_CALL", "0.0004") or 0.0004)
    except (TypeError, ValueError):
        in_rate, out_rate, flat = 0.075, 0.30, 0.0004
    if input_tokens is None and output_tokens is None:
        return flat
    inn = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    return round((inn / 1_000_000.0) * in_rate + (out / 1_000_000.0) * out_rate, 6) or flat


def estimate_jev_cost(
    *,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> float:
    try:
        flat = float(os.environ.get("JEV_FLAT_USD_PER_CALL", "0.001") or 0.001)
        in_rate = float(os.environ.get("JEV_USD_PER_1M_INPUT", "1.0") or 1.0)
        out_rate = float(os.environ.get("JEV_USD_PER_1M_OUTPUT", "1.0") or 1.0)
    except (TypeError, ValueError):
        flat, in_rate, out_rate = 0.001, 1.0, 1.0
    if input_tokens is None and output_tokens is None:
        return flat
    inn = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    est = (inn / 1_000_000.0) * in_rate + (out / 1_000_000.0) * out_rate
    return round(est, 6) or flat


def decision_horizon_min(cfg: Optional[dict] = None) -> int:
    cfg = cfg or {}
    raw = cfg.get("decision_horizon_min")
    if raw in (None, ""):
        raw = os.environ.get("DECISION_HORIZON_MIN", str(DEFAULT_HORIZON_MIN))
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = DEFAULT_HORIZON_MIN
    return max(5, min(120, v))


def resolve_brain_mode(cfg: Optional[dict] = None) -> str:
    """One brain selected: gemini | mock | jev. Env BRAIN_MODE / MODEL as fallback."""
    cfg = cfg or {}
    raw = (
        cfg.get("brain_mode")
        or cfg.get("model")
        or os.environ.get("BRAIN_MODE")
        or os.environ.get("MODEL")
        or "gemini"
    )
    mode = str(raw or "gemini").strip().lower()
    if mode in ("live", "gemini-live"):
        mode = "gemini"
    if mode not in VALID_BRAIN_MODES:
        mode = "gemini"
    return mode


def typesafe_api_key() -> str:
    return (
        os.environ.get("TYPESAFE_AI_API_KEY", "").strip()
        or os.environ.get("TYPESAFE_API_KEY", "").strip()
    )


def shadow_auditor_enabled() -> bool:
    v = (os.environ.get("SHADOW_AUDITOR", "1") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def shadow_gate_enabled() -> bool:
    v = (os.environ.get("SHADOW_GATE", "0") or "0").strip().lower()
    return v in ("1", "true", "on", "yes")


def _empty_thesis(model: str, error: str, notes: str = "") -> dict[str, Any]:
    return {
        "side": "flat",
        "confidence": 0.0,
        "thesis": "",
        "entry_plan": "",
        "stop_idea": "",
        "target_idea": "",
        "risks": [],
        "playbook_agree": False,
        "notes": notes or "",
        "horizon": None,
        "horizon_min": None,
        "llm_model": model,
        "llm_raw": "",
        "brain_mode": None,
        "error": error,
        "model_cost_usd": 0.0,
    }


def mock_trade_thesis(analysis: dict, cfg: Optional[dict] = None) -> dict[str, Any]:
    """Simple momentum/heuristic brain — no API. Fake probabilities via confidence."""
    cfg = cfg or {}
    horizon = decision_horizon_min(cfg)
    ticker = (analysis.get("ticker") or "?").upper()
    try:
        change_pct = float(analysis.get("change_pct") or 0)
    except (TypeError, ValueError):
        change_pct = 0.0
    vol = analysis.get("volume") or {}
    try:
        rel_vol = float(vol.get("rel_vol") or 1.0)
    except (TypeError, ValueError):
        rel_vol = 1.0
    sma = analysis.get("sma") or {}
    above_20 = bool(sma.get("above_20"))
    above_50 = bool(sma.get("above_50"))
    verdict = (analysis.get("verdict") or "").upper()
    entry = analysis.get("entry_quality") or {}
    late_lbl = str((entry.get("label") if isinstance(entry, dict) else "") or "").lower()

    score = 0.0
    score += max(-2.0, min(2.0, change_pct / 1.5))
    if rel_vol >= 1.5:
        score += 0.4
    elif rel_vol < 0.7:
        score -= 0.3
    if above_20:
        score += 0.35
    if above_50:
        score += 0.25
    if verdict == "PASS":
        score += 0.4
    elif verdict == "AVOID":
        score -= 0.8
    if late_lbl in ("late", "chasing"):
        score *= 0.5

    if score >= 0.55:
        side = "buy"
        conf = min(0.92, 0.55 + abs(score) * 0.12)
        thesis = (
            f"Mock momentum: {ticker} up {change_pct:.2f}% with rel_vol={rel_vol:.1f}; "
            f"expects higher over next {horizon} min."
        )
    elif score <= -0.55:
        side = "sell"
        conf = min(0.92, 0.55 + abs(score) * 0.12)
        thesis = (
            f"Mock fade: {ticker} down {change_pct:.2f}% / weak tape; "
            f"expects lower over next {horizon} min."
        )
    else:
        side = "flat"
        conf = max(0.45, 0.7 - abs(score) * 0.15)
        thesis = (
            f"Mock flat: {ticker} score={score:.2f} — no clear edge over {horizon} min."
        )

    return {
        "side": side,
        "confidence": round(conf, 3),
        "thesis": thesis,
        "entry_plan": "Paper only — mock heuristic entry near last",
        "stop_idea": "Mock stop ~0.6R",
        "target_idea": f"Mock target over ~{horizon} min horizon",
        "risks": ["mock_brain", "no_live_model"],
        "playbook_agree": verdict in ("PASS", "WATCH") if side != "flat" else True,
        "notes": f"brain=mock score={score:.3f}",
        "horizon": "higher" if side == "buy" else ("lower" if side == "sell" else "flat"),
        "horizon_min": horizon,
        "llm_model": "mock-momentum",
        "llm_raw": "",
        "brain_mode": "mock",
        "error": None,
        "model_cost_usd": 0.0,
        "probs": None,
    }


def jev_trade_thesis(
    analysis: dict,
    cfg: Optional[dict] = None,
    *,
    timeout_sec: float = 12,
) -> dict[str, Any]:
    """Optional TypeSafe Jev lane. Graceful error → caller may fall back."""
    cfg = cfg or {}
    horizon = decision_horizon_min(cfg)
    key = typesafe_api_key()
    if not key:
        return _empty_thesis("jev-latest", "missing_typesafe_api_key")

    ticker = (analysis.get("ticker") or "?").upper()
    state = (
        f"Day-trade paper desk research for {ticker}. Horizon: next {horizon} minutes.\n"
        f"Screener facts (JSON):\n{_analysis_context_blob(analysis)}\n"
        "Choose whether price is more likely higher, lower, or flat over that horizon. "
        "Paper research only — no broker orders."
    )
    body = {
        "model": "jev-latest",
        "state": state,
        "questions": {
            "side": {
                "type": "choice",
                "instructions": (
                    f"Over the next {horizon} minutes, is {ticker} more likely "
                    "higher (buy), lower (sell), or roughly flat?"
                ),
                "criteria": {
                    "buy": "Expect higher / long bias",
                    "sell": "Expect lower / short or exit bias",
                    "flat": "No clear directional edge — abstain",
                },
            },
            "confidence": {
                "type": "noul",
                "instructions": "How confident is the directional call? (0–1)",
            },
        },
    }
    try:
        r = requests.post(
            TYPESAFE_API_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=max(3.0, float(timeout_sec or 12)),
        )
    except requests.Timeout as exc:
        return _empty_thesis("jev-latest", "llm_timeout", str(exc)[:200])
    except requests.RequestException as exc:
        return _empty_thesis("jev-latest", f"jev_request_failed: {exc}"[:200])

    if r.status_code != 200:
        detail = (r.text or "")[:200]
        return _empty_thesis("jev-latest", f"jev_http_{r.status_code}: {detail}"[:200])

    try:
        data = r.json()
    except Exception as exc:
        return _empty_thesis("jev-latest", f"jev_bad_json: {exc}"[:200])

    answers = data.get("answers") or {}
    side_ans = answers.get("side") or {}
    conf_ans = answers.get("confidence") or {}
    side = str(side_ans.get("choice") or "flat").lower().strip()
    if side not in ("buy", "sell", "flat"):
        side = "flat"
    try:
        conf = float(conf_ans.get("noul") if conf_ans.get("noul") is not None else side_ans.get("confidence") or 0.5)
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    probs = side_ans.get("probabilities") if isinstance(side_ans.get("probabilities"), dict) else None
    usage = data.get("usage") or {}
    cost = estimate_jev_cost(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )
    record_model_cost(cost, meta={"brain": "jev", "ticker": ticker})
    horizon_label = "higher" if side == "buy" else ("lower" if side == "sell" else "flat")
    return {
        "side": side,
        "confidence": round(conf, 3),
        "thesis": (
            f"Jev: {ticker} likely {horizon_label} over next {horizon} min "
            f"(conf={conf:.2f})."
        ),
        "entry_plan": "Paper — Jev directional choice",
        "stop_idea": "",
        "target_idea": f"Horizon {horizon} min",
        "risks": ["jev_model"],
        "playbook_agree": True,
        "notes": "",
        "horizon": horizon_label,
        "horizon_min": horizon,
        "llm_model": str(data.get("model") or "jev-latest"),
        "llm_raw": json.dumps(answers)[:RAW_TRUNCATE],
        "brain_mode": "jev",
        "error": None,
        "model_cost_usd": cost,
        "probs": probs,
    }


def _kw_hit(text: str, phrase: str) -> bool:
    """Word-boundary / phrase match — avoid substring false positives (e.g. bid in forbid)."""
    t = text or ""
    p = (phrase or "").strip().lower()
    if not p:
        return False
    if " " in p:
        return p in t
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", t))


def _heuristic_shadow_audit(side: str, thesis: str) -> dict[str, Any]:
    """Keyword coherence: does the reason text support buy/sell?

    Prefer structured audit (gemini/jev) when available; this is the fallback.
    Uses word-boundary matching so e.g. 'bid' does not match inside 'forbid'.
    """
    t = (thesis or "").lower()
    s = (side or "flat").lower()
    buy_kw = ("higher", "upside", "breakout", "long", "buy", "bull", "strength", "bid", "rally", "momentum")
    sell_kw = ("lower", "downside", "breakdown", "short", "sell", "bear", "weak", "fade", "drop", "selloff")
    flat_kw = ("flat", "chop", "unclear", "no edge", "abstain", "hold", "range")
    buy_hits = sum(1 for k in buy_kw if _kw_hit(t, k))
    sell_hits = sum(1 for k in sell_kw if _kw_hit(t, k))
    flat_hits = sum(1 for k in flat_kw if _kw_hit(t, k))
    # Prefer JSON-ish structure if thesis looks like structured notes
    coherent = True
    reason = "heuristic_ok"
    if s == "buy":
        coherent = buy_hits >= sell_hits and buy_hits > 0
        if not coherent:
            reason = "thesis_lacks_buy_support"
    elif s == "sell":
        coherent = sell_hits >= buy_hits and sell_hits > 0
        if not coherent:
            reason = "thesis_lacks_sell_support"
    else:
        coherent = flat_hits > 0 or (buy_hits == 0 and sell_hits == 0) or abs(buy_hits - sell_hits) <= 1
        if not coherent:
            reason = "thesis_directional_for_flat"
    return {
        "coherent": bool(coherent),
        "label": "coherent" if coherent else "incoherent",
        "method": "heuristic",
        "reason": reason,
        "buy_hits": buy_hits,
        "sell_hits": sell_hits,
    }


def shadow_audit_decision(
    side: str,
    thesis: str,
    *,
    analysis: Optional[dict] = None,
    cfg: Optional[dict] = None,
) -> dict[str, Any]:
    """Observe-only coherence check. Gemini JSON when key present; else heuristic."""
    if not shadow_auditor_enabled():
        return {
            "coherent": True,
            "label": "skipped",
            "method": "off",
            "reason": "SHADOW_AUDITOR=0",
        }
    s = (side or "flat").lower()
    if s not in ("buy", "sell"):
        # Flat: still lightly check but always pass for gate purposes
        h = _heuristic_shadow_audit(s, thesis)
        h["label"] = "coherent" if h.get("coherent") else "incoherent"
        return h

    llm_cfg = load_llm_config()
    if llm_cfg.get("configured") and llm_cfg.get("api_key"):
        ticker = ((analysis or {}).get("ticker") or "?").upper()
        prompt = (
            f"Side claimed: {s}\nThesis: {thesis}\nTicker: {ticker}\n"
            "Does the thesis text support the claimed buy/sell side?\n"
            'Return ONLY JSON: {"pass": true|false, "reason": "<short>"}'
        )
        try:
            raw = gemini_generate(
                prompt,
                system="You are a coherence auditor. Check if reason supports the side. Research only.",
                json_mode=True,
                cfg=llm_cfg,
                timeout_sec=8,
            )
            shadow_cost = last_call_cost()  # recorded in gemini_generate
            parsed = _extract_json_object(raw) or {}
            passed = parsed.get("pass")
            if isinstance(passed, str):
                passed = passed.lower() in ("1", "true", "yes", "pass")
            coherent = bool(passed) if passed is not None else True
            return {
                "coherent": coherent,
                "label": "coherent" if coherent else "incoherent",
                "method": "gemini",
                "reason": str(parsed.get("reason") or "")[:200],
                "model_cost_usd": shadow_cost,
            }
        except Exception as exc:  # noqa: BLE001
            h = _heuristic_shadow_audit(s, thesis)
            h["fallback_error"] = str(exc)[:120]
            return h
    return _heuristic_shadow_audit(s, thesis)



# ---------------------------------------------------------------------------
# Prism-style advisory panel + confidence + cheap brain router (Jev roundup)
# ---------------------------------------------------------------------------

DEFAULT_MIN_DECISION_CONFIDENCE = 0.55


def _env_flag(name: str, default: str = "0") -> bool:
    v = (os.environ.get(name, default) or default).strip().lower()
    return v in ("1", "true", "on", "yes")


def advisory_panel_enabled() -> bool:
    """ADVISORY_PANEL default ON."""
    v = (os.environ.get("ADVISORY_PANEL", "1") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def advisory_soft_size_enabled() -> bool:
    """ADVISORY_SOFT_SIZE default OFF — observe-only unless opted in."""
    return _env_flag("ADVISORY_SOFT_SIZE", "0")


def brain_router_enabled() -> bool:
    """BRAIN_ROUTER default ON — cheap mock for junk tape when gemini selected."""
    v = (os.environ.get("BRAIN_ROUTER", "1") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def min_decision_confidence(cfg: Optional[dict] = None) -> float:
    cfg = cfg or {}
    raw = cfg.get("min_decision_confidence")
    if raw in (None, ""):
        raw = os.environ.get("MIN_DECISION_CONFIDENCE", str(DEFAULT_MIN_DECISION_CONFIDENCE))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = DEFAULT_MIN_DECISION_CONFIDENCE
    return max(0.0, min(1.0, v))


def _clamp01(x: Any, default: float = 0.5) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        v = default
    return max(0.0, min(1.0, v))


def policy_label_from_advisory(scores: dict[str, Any]) -> str:
    """Code-owned KILL/FIX/SHIP from advisory scores (never LLM-authored)."""
    coherent = _clamp01(scores.get("thesis_coherent"), 0.0)
    regime = _clamp01(scores.get("regime_ok"), 0.5)
    risk = _clamp01(scores.get("risk_ok"), 0.5)
    tradeable = _clamp01(scores.get("tradeable_now"), 0.0)
    if coherent < 0.45 or tradeable < 0.40:
        return "KILL"
    if coherent >= 0.55 and risk >= 0.55 and tradeable >= 0.55 and regime >= 0.45:
        return "SHIP"
    return "FIX"


def _advisory_size_mult(scores: dict[str, Any], policy: str) -> float:
    """Prism-like paper size cut only when soft size opted in."""
    if not advisory_soft_size_enabled():
        return 1.0
    risk = _clamp01(scores.get("risk_ok"), 0.5)
    regime = _clamp01(scores.get("regime_ok"), 0.5)
    if policy == "KILL":
        return 0.0  # hold path should already block; never open on KILL soft
    if policy == "FIX" or risk < 0.5 or regime < 0.45:
        return 0.5
    return 1.0


def mock_advisory_panel(
    side: str,
    thesis: str,
    analysis: Optional[dict] = None,
) -> dict[str, Any]:
    """Heuristic multi-question advisory (no API). Gemini/Jev paths preferred when keyed."""
    analysis = analysis or {}
    s = (side or "flat").lower()
    t = (thesis or "").lower()
    vol = analysis.get("volume") or {}
    entry = analysis.get("entry_quality") or {}
    try:
        rel_vol = float(vol.get("rel_vol") or 1.0)
    except (TypeError, ValueError):
        rel_vol = 1.0
    try:
        change_pct = abs(float(analysis.get("change_pct") or 0))
    except (TypeError, ValueError):
        change_pct = 0.0
    try:
        pos = float(entry.get("pos_in_30d") or 50)
    except (TypeError, ValueError):
        pos = 50.0
    late_lbl = str(entry.get("label") or "").lower()
    verdict = str(analysis.get("verdict") or "").upper()
    gap = abs(float(analysis.get("gap_pct") or 0)) if analysis.get("gap_pct") is not None else 0.0

    # 1 thesis_coherent — reason supports side
    h = _heuristic_shadow_audit(s, thesis)
    if s in ("buy", "sell"):
        thesis_coherent = 0.78 if h.get("coherent") else 0.28
        if h.get("buy_hits", 0) + h.get("sell_hits", 0) == 0 and thesis:
            thesis_coherent = 0.42  # text present but no clear support words
    else:
        thesis_coherent = 0.7 if h.get("coherent") else 0.45

    # 2 regime_ok — not choppy/illiquid for day trade
    regime = 0.7
    if rel_vol < 0.7:
        regime -= 0.35
    elif rel_vol < 1.0:
        regime -= 0.15
    if change_pct < 0.15 and rel_vol < 1.0:
        regime -= 0.2  # dead tape
    if verdict == "AVOID":
        regime -= 0.25
    elif verdict == "PASS":
        regime += 0.1
    regime_ok = _clamp01(regime)

    # 3 risk_ok — not stress / too extended for size
    risk = 0.75
    if late_lbl in ("late", "chasing"):
        risk -= 0.35 if late_lbl == "late" else 0.5
    if pos >= 95 or pos <= 5:
        risk -= 0.25
    if gap >= 8:
        risk -= 0.3
    elif gap >= 4:
        risk -= 0.15
    if change_pct >= 8:
        risk -= 0.2
    risk_ok = _clamp01(risk)

    # 4 tradeable_now — enough edge to act this tick
    tradeable = 0.55
    if s == "flat":
        tradeable = 0.25
    if not h.get("coherent") and s in ("buy", "sell"):
        tradeable -= 0.3
    if risk_ok < 0.4:
        tradeable -= 0.2
    if regime_ok < 0.4:
        tradeable -= 0.2
    if verdict == "PASS" and s in ("buy", "sell"):
        tradeable += 0.1
    if "no clear edge" in t or "mock flat" in t:
        tradeable = min(tradeable, 0.3)
    tradeable_now = _clamp01(tradeable)

    scores = {
        "thesis_coherent": round(thesis_coherent, 3),
        "regime_ok": round(regime_ok, 3),
        "risk_ok": round(risk_ok, 3),
        "tradeable_now": round(tradeable_now, 3),
    }
    policy = policy_label_from_advisory(scores)
    return {
        "enabled": True,
        "method": "mock",
        "scores": scores,
        "policy_label": policy,
        "observe_only": True,
        "size_mult": _advisory_size_mult(scores, policy),
        "model_cost_usd": 0.0,
        "notes": "prism-style advisory (heuristic)",
    }


def _parse_advisory_scores(raw: dict) -> dict[str, float]:
    out = {}
    for key in ("thesis_coherent", "regime_ok", "risk_ok", "tradeable_now"):
        node = raw.get(key)
        if isinstance(node, dict):
            val = node.get("noul")
            if val is None:
                val = node.get("score", node.get("value"))
        else:
            val = node
        out[key] = round(_clamp01(val, 0.5), 3)
    return out


def gemini_advisory_panel(
    side: str,
    thesis: str,
    analysis: Optional[dict] = None,
    *,
    timeout_sec: float = 8,
) -> dict[str, Any]:
    """Multi-question advisory via Gemini JSON (when key present)."""
    llm_cfg = load_llm_config()
    if not llm_cfg.get("configured") or not llm_cfg.get("api_key"):
        return mock_advisory_panel(side, thesis, analysis)
    ticker = ((analysis or {}).get("ticker") or "?").upper()
    blob = _analysis_context_blob(analysis or {})
    prompt = (
        f"Ticker {ticker}. Claimed side: {side}. Thesis: {thesis}\n"
        f"Screener facts:\n{blob}\n"
        "Score each question 0–1 (probability the statement is true):\n"
        "1 thesis_coherent — reason text supports the claimed side\n"
        "2 regime_ok — tape is not choppy/illiquid for a day trade\n"
        "3 risk_ok — not stress / too extended for full size\n"
        "4 tradeable_now — enough edge to act on this tick\n"
        'Return ONLY JSON: {"thesis_coherent":0-1,"regime_ok":0-1,'
        '"risk_ok":0-1,"tradeable_now":0-1}'
    )
    try:
        raw = gemini_generate(
            prompt,
            system=(
                "You are an advisory risk panel for paper day-trade research. "
                "Score only; do not flip the trade side. Research only."
            ),
            json_mode=True,
            cfg=llm_cfg,
            timeout_sec=timeout_sec,
        )
        cost = last_call_cost()  # recorded in gemini_generate
        parsed = _extract_json_object(raw) or {}
        scores = _parse_advisory_scores(parsed)
        policy = policy_label_from_advisory(scores)
        return {
            "enabled": True,
            "method": "gemini",
            "scores": scores,
            "policy_label": policy,
            "observe_only": True,
            "size_mult": _advisory_size_mult(scores, policy),
            "model_cost_usd": cost,
            "notes": "prism-style advisory (gemini json)",
        }
    except Exception as exc:  # noqa: BLE001
        m = mock_advisory_panel(side, thesis, analysis)
        m["fallback_error"] = str(exc)[:120]
        m["notes"] = "advisory gemini failed → mock"
        return m


def jev_advisory_panel(
    side: str,
    thesis: str,
    analysis: Optional[dict] = None,
    *,
    timeout_sec: float = 10,
) -> dict[str, Any]:
    """Real TypeSafe multi-question advisory when key present."""
    key = typesafe_api_key()
    if not key:
        return mock_advisory_panel(side, thesis, analysis)
    ticker = ((analysis or {}).get("ticker") or "?").upper()
    state = (
        f"Paper day-trade advisory for {ticker}. Claimed side: {side}.\n"
        f"Thesis: {thesis}\n"
        f"Screener:\n{_analysis_context_blob(analysis or {})}\n"
        "Answer the four noul questions. Do not change the claimed side."
    )
    body = {
        "model": "jev-latest",
        "state": state,
        "questions": {
            "thesis_coherent": {
                "type": "noul",
                "instructions": "Does the thesis/reason support the claimed side?",
            },
            "regime_ok": {
                "type": "noul",
                "instructions": "Is the regime OK for a day trade (not choppy/illiquid)?",
            },
            "risk_ok": {
                "type": "noul",
                "instructions": "Is risk OK (not stress / too extended for size)?",
            },
            "tradeable_now": {
                "type": "noul",
                "instructions": "Is there enough edge to act on this tick?",
            },
        },
    }
    try:
        r = requests.post(
            TYPESAFE_API_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=max(3.0, float(timeout_sec or 10)),
        )
    except requests.RequestException as exc:
        m = mock_advisory_panel(side, thesis, analysis)
        m["fallback_error"] = str(exc)[:120]
        return m
    if r.status_code != 200:
        m = mock_advisory_panel(side, thesis, analysis)
        m["fallback_error"] = f"jev_http_{r.status_code}"[:120]
        return m
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        m = mock_advisory_panel(side, thesis, analysis)
        m["fallback_error"] = str(exc)[:120]
        return m
    answers = data.get("answers") or {}
    scores = _parse_advisory_scores(answers)
    usage = data.get("usage") or {}
    cost = estimate_jev_cost(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )
    record_model_cost(cost, meta={"brain": "advisory_jev"})
    policy = policy_label_from_advisory(scores)
    return {
        "enabled": True,
        "method": "jev",
        "scores": scores,
        "policy_label": policy,
        "observe_only": True,
        "size_mult": _advisory_size_mult(scores, policy),
        "model_cost_usd": cost,
        "notes": "prism-style advisory (jev multi-q)",
    }


def run_advisory_panel(
    side: str,
    thesis: str,
    *,
    analysis: Optional[dict] = None,
    cfg: Optional[dict] = None,
) -> dict[str, Any]:
    """Prism pattern: multi-q advisory. Default observe-only; never flips side.

    - TypeSafe key + (brain=jev or ADVISORY_USE_JEV=1) → real Jev multi-q
    - ADVISORY_USE_GEMINI=1 + Gemini key → Gemini JSON scores
    - else → local gemini-JSON-shaped mock (no extra API cost)
    """
    if not advisory_panel_enabled():
        return {
            "enabled": False,
            "method": "off",
            "scores": {},
            "policy_label": None,
            "observe_only": True,
            "size_mult": 1.0,
            "model_cost_usd": 0.0,
            "reason": "ADVISORY_PANEL=0",
        }
    cfg = cfg or {}
    brain = resolve_brain_mode(cfg)
    if typesafe_api_key() and (brain == "jev" or _env_flag("ADVISORY_USE_JEV", "0")):
        return jev_advisory_panel(side, thesis, analysis)
    if _env_flag("ADVISORY_USE_GEMINI", "0"):
        llm_cfg = load_llm_config()
        if llm_cfg.get("configured") and llm_cfg.get("api_key") and brain != "mock":
            return gemini_advisory_panel(side, thesis, analysis)
    # Default: free mock with same score schema as gemini JSON
    return mock_advisory_panel(side, thesis, analysis)


def should_route_cheap(analysis: Optional[dict] = None, cfg: Optional[dict] = None) -> tuple[bool, str]:
    """Cheap brain router: mock for junk tape instead of Gemini.

    Route when (rel_vol weak AND range extreme) OR verdict AVOID.
    """
    if not brain_router_enabled():
        return False, "router_off"
    cfg = cfg or {}
    mode = resolve_brain_mode(cfg)
    if mode != "gemini":
        return False, f"mode_{mode}"
    analysis = analysis or {}
    verdict = str(analysis.get("verdict") or "").upper()
    vol = analysis.get("volume") or {}
    entry = analysis.get("entry_quality") or {}
    try:
        rel_vol = float(vol.get("rel_vol") or 1.0)
    except (TypeError, ValueError):
        rel_vol = 1.0
    try:
        pos = float(entry.get("pos_in_30d") or 50)
    except (TypeError, ValueError):
        pos = 50.0
    late_lbl = str(entry.get("label") or "").lower()
    try:
        change_pct = abs(float(analysis.get("change_pct") or 0))
    except (TypeError, ValueError):
        change_pct = 0.0
    weak_vol = rel_vol < 1.0
    range_extreme = pos >= 92 or pos <= 8 or late_lbl in ("late", "chasing") or change_pct >= 6.0
    if verdict == "AVOID":
        return True, "verdict_avoid"
    if weak_vol and range_extreme:
        return True, "weak_vol_range_extreme"
    return False, "pass"


def decide_trade_thesis(
    analysis: dict,
    cfg: Optional[dict] = None,
    *,
    timeout_sec: float = 12,
) -> dict[str, Any]:
    """Single brain dispatch by brain_mode. Jev/gemini errors → hard hold (no silent mock fills).

    Cheap brain router (BRAIN_ROUTER=1, default on): when gemini is selected and
    tape is junk (weak rel_vol + range extreme) or verdict AVOID → mock path
    (log routed: mock_cheap). Does not override brain_mode=mock or =jev.
    Set BRAIN_ROUTER=0 to force Gemini-only for eligible ticks.
    """
    cfg = cfg or {}
    mode = resolve_brain_mode(cfg)
    horizon = decision_horizon_min(cfg)

    if mode == "mock":
        out = mock_trade_thesis(analysis, cfg)
        out["brain_mode"] = "mock"
        return out

    if mode == "jev":
        out = jev_trade_thesis(analysis, cfg, timeout_sec=timeout_sec)
        if out.get("error"):
            # Hard hold — no silent mock fills when jev is configured
            return {
                "side": "hold",
                "confidence": 0.0,
                "thesis": f"jev error — hard hold ({out.get('error')})",
                "error": out.get("error"),
                "brain_mode": "jev",
                "fallback_from": "jev",
                "fallback_error": out.get("error"),
                "notes": f"jev_hard_hold: {out.get('error')}",
                "horizon_min": horizon,
                "abstain": True,
            }
        out["brain_mode"] = "jev"
        out["horizon_min"] = out.get("horizon_min") or horizon
        return out

    # gemini (default) — optional cheap router → mock
    route, route_why = should_route_cheap(analysis, cfg)
    if route:
        out = mock_trade_thesis(analysis, cfg)
        out["brain_mode"] = "mock"
        out["routed"] = "mock_cheap"
        out["router_reason"] = route_why
        out["notes"] = f"routed: mock_cheap ({route_why})"
        return out

    out = trade_thesis_from_analysis(analysis, cfg if cfg.get("api_key") else None, timeout_sec=timeout_sec)
    # If caller passed desk cfg without api_key, rebuild gemini cfg
    if out.get("error") == "missing_gemini_api_key" or (
        not (cfg or {}).get("api_key") and out.get("error")
    ):
        # Retry with load_llm_config if desk cfg lacked key fields
        gem_cfg = load_llm_config()
        if cfg.get("model") or cfg.get("llm_model"):
            gem_cfg = dict(gem_cfg)
            gem_cfg["model"] = str(cfg.get("llm_model") or cfg.get("model") or gem_cfg.get("model"))
        if gem_cfg.get("configured"):
            out = trade_thesis_from_analysis(analysis, gem_cfg, timeout_sec=timeout_sec)
    if out.get("error"):
        # Hard hold — no silent mock fills when gemini is configured
        return {
            "side": "hold",
            "confidence": 0.0,
            "thesis": f"gemini error — hard hold ({out.get('error')})",
            "error": out.get("error"),
            "brain_mode": "gemini",
            "fallback_from": "gemini",
            "fallback_error": out.get("error"),
            "notes": f"gemini_hard_hold: {out.get('error')}",
            "horizon_min": horizon,
            "abstain": True,
        }
    out["brain_mode"] = "gemini"
    out["horizon_min"] = out.get("horizon_min") or horizon
    # trade_thesis_from_analysis already records cost; only fill if missing (do not re-record)
    if out.get("model_cost_usd") in (None,):
        out["model_cost_usd"] = last_call_cost()
    elif out.get("model_cost_usd") in (0, 0.0):
        # Already recorded a zero/flat in callee — keep value, do not double-count
        pass
    return out


def status_public_extended(cfg: Optional[dict] = None) -> dict[str, Any]:
    """Safe status including brain mode + cost (never includes keys)."""
    base = status_public()
    mode = resolve_brain_mode(cfg)
    base["brain_mode"] = mode
    base["jev_key_present"] = bool(typesafe_api_key())
    base["shadow_auditor"] = shadow_auditor_enabled()
    base["shadow_gate"] = shadow_gate_enabled()
    base["horizon_min"] = decision_horizon_min(cfg)
    base["model_cost"] = model_cost_today()
    base["advisory_panel"] = advisory_panel_enabled()
    base["advisory_soft_size"] = advisory_soft_size_enabled()
    base["brain_router"] = brain_router_enabled()
    base["min_decision_confidence"] = min_decision_confidence(cfg)
    if mode == "mock":
        base["provider"] = "mock"
        base["configured"] = True
        base["model"] = "mock-momentum"
    elif mode == "jev":
        base["provider"] = "jev"
        base["configured"] = bool(typesafe_api_key())
        base["model"] = "jev-latest"
    return base
