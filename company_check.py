"""Company check: a research-only card for one ticker (docs/COMPANY_CHECK.md).

Owner decision: display only. Nothing here is an execution input; it never places,
sizes or blocks a trade. Each section loads independently, so one slow or missing
source leaves the others readable.

Sections and sources (all public except Congress):
- bottom_line: valuation, growth, profit and debt from Yahoo company data (yfinance).
- insiders: SEC Form 4 filings about the company (needs EDGAR_USER_AGENT).
- contracts: federal contract money from USAspending.gov (matched by company name).
- lobbying: federal lobbying filings from the Senate LDA database (matched by name).
- congress: disclosed Congress trades from Quiver Quantitative (needs QUIVER_API_KEY).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

CACHE_TTL_SEC = 6 * 3600
MAX_CACHED = 80
FORM4_LOOKBACK_DAYS = 90
FORM4_MAX_DOCS = 20
SEC_PAUSE_SEC = 0.12  # SEC fair access: at most 10 requests per second
USASPENDING = "https://api.usaspending.gov/api/v2/search"
LDA_FILINGS = "https://lda.senate.gov/api/v1/filings/"
QUIVER_CONGRESS = "https://api.quiverquant.com/beta/historical/congresstrading/{ticker}"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
NAME_SUFFIXES = re.compile(
    r"\b(INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|LTD|LIMITED|PLC|HOLDINGS?|GROUP|LLC|LP|NV|SA|AG|SE|"
    r"CLASS [A-Z]|THE)\b\.?", re.I)

_lock = threading.RLock()
_cache: dict[str, dict[str, Any]] = {}
_running: dict[str, threading.Event] = {}
_data_dir: Path | None = None

# Form 4 transaction codes, in plain words (SEC Form 4 General Instructions 8).
CODES = {
    "P": "bought on the open market", "S": "sold on the open market", "A": "received as pay (grant)",
    "M": "exercised options", "F": "gave shares to pay tax", "G": "gift", "D": "returned to the company",
    "C": "converted", "X": "exercised options", "J": "other",
}


def configure(data_dir: Path | None) -> None:
    global _data_dir
    _data_dir = Path(data_dir) if data_dir else None
    _load_cache()


def clean_ticker(value: Any) -> str | None:
    ticker = str(value or "").strip().upper()
    return ticker if TICKER_RE.match(ticker) else None


def search_name(title: str) -> str:
    """'LOCKHEED MARTIN CORP' -> 'LOCKHEED MARTIN' for name-matched public databases."""
    text = re.sub(r"/[A-Z]{2,3}/", " ", str(title or "").upper())
    text = NAME_SUFFIXES.sub(" ", text.replace(",", " ").replace(".", " "))
    return " ".join(text.split())


def _num(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _money(n: float | None) -> str:
    if n is None:
        return "unknown"
    sign, n = ("-" if n < 0 else ""), abs(n)
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            return f"{sign}${n / size:,.1f}{unit}"
    return f"{sign}${n:,.0f}"


def _pct(n: float | None) -> str:
    return "unknown" if n is None else f"{n * 100:+.0f}%"


def _item(key: str, label: str, value: str, plain: str, tone: str = "neutral") -> dict[str, str]:
    return {"key": key, "label": label, "value": value, "plain": plain, "tone": tone}


# --- bottom line ----------------------------------------------------------------

def bottom_line(ticker: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Four plain-language reads (value, growth, profit, debt) plus the analyst range."""
    if info is None:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    kind = str(info.get("quoteType") or "").upper()
    if kind in ("ETF", "MUTUALFUND", "INDEX"):
        return {"ok": True, "applies": False, "summary": f"{ticker} is a fund, so company profit and debt checks do not apply.",
                "items": [], "source": "Yahoo Finance company data"}
    items = []
    pe, fpe = _num(info.get("trailingPE")), _num(info.get("forwardPE"))
    if pe is not None and pe > 0:
        tone = "good" if pe < 15 else "watch" if pe > 35 else "neutral"
        items.append(_item("value", "Price vs profit", f"P/E {pe:.1f}",
                           f"You pay about ${pe:.0f} for each $1 of yearly profit. Lower is cheaper; above 35 means buyers expect fast growth.",
                           tone))
    elif _num(info.get("trailingEps")) is not None and _num(info.get("trailingEps")) <= 0:
        items.append(_item("value", "Price vs profit", "No profit", "The company lost money over the last year, so there is no P/E.", "watch"))
    if fpe is not None and fpe > 0 and pe:
        items.append(_item("value_next", "Next year's P/E", f"{fpe:.1f}",
                           f"Using analysts' profit forecast for next year, you pay about ${fpe:.0f} per $1 of profit"
                           + (" — they expect profit to grow." if fpe < pe else " — they expect profit to shrink." if fpe > pe else "."),
                           "neutral"))
    growth = _num(info.get("revenueGrowth"))
    if growth is not None:
        items.append(_item("growth", "Sales growth", _pct(growth),
                           f"Sales were {abs(growth) * 100:.0f}% {'higher' if growth >= 0 else 'lower'} than a year earlier.",
                           "good" if growth >= .1 else "watch" if growth < 0 else "neutral"))
    margin = _num(info.get("profitMargins"))
    if margin is not None:
        cents = round(margin * 100)
        items.append(_item("profit", "Profit per $1 of sales", f"{margin * 100:.0f}%",
                           f"It keeps about {cents} cents of profit from each $1 of sales." if cents >= 0
                           else f"It loses about {abs(cents)} cents on each $1 of sales.",
                           "good" if margin >= .15 else "watch" if margin < 0 else "neutral"))
    debt = _num(info.get("debtToEquity"))
    if debt is not None and debt >= 0:
        ratio = debt / 100  # Yahoo reports debt-to-equity as a percentage
        items.append(_item("debt", "Debt", f"{ratio:.2f}× equity",
                           f"It owes about ${ratio:.2f} for every $1 its shareholders own. Above $2 leaves less room for a bad year.",
                           "good" if ratio < .5 else "watch" if ratio > 2 else "neutral"))
    price, target = _num(info.get("currentPrice") or info.get("regularMarketPrice")), _num(info.get("targetMeanPrice"))
    low, high, analysts = _num(info.get("targetLowPrice")), _num(info.get("targetHighPrice")), _num(info.get("numberOfAnalystOpinions"))
    if price and target:
        gap = target / price - 1
        spread = f" Their guesses range from ${low:,.0f} to ${high:,.0f}." if low and high else ""
        items.append(_item("analysts", "Analyst price target", f"${target:,.2f}",
                           f"{int(analysts or 0) or 'Some'} analysts' average target is {abs(gap) * 100:.0f}% "
                           f"{'above' if gap >= 0 else 'below'} today's ${price:,.2f}.{spread} Targets are opinions and are often wrong.",
                           "neutral"))
    good = sum(1 for i in items if i["tone"] == "good")
    watch = sum(1 for i in items if i["tone"] == "watch")
    name = info.get("shortName") or info.get("longName") or ticker
    summary = (f"{name}: {good} strong point{'s' if good != 1 else ''} and {watch} to watch."
               if items else f"No company numbers were available for {ticker}.")
    return {"ok": bool(items), "applies": True, "name": name, "sector": info.get("sector"), "summary": summary,
            "items": items, "source": "Yahoo Finance company data", "error": None if items else "No company data available"}


# --- SEC Form 4 insiders -----------------------------------------------------------

def _sec_headers() -> dict[str, str] | None:
    import edgar_client
    if not edgar_client.is_configured():
        return None
    return {"User-Agent": edgar_client.user_agent(), "Accept-Encoding": "gzip, deflate"}


def _text(node, path: str) -> str:
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_form4(xml_text: str) -> list[dict[str, Any]]:
    """Non-derivative transactions from one Form 4: who, their role, what and how much."""
    root = ET.fromstring(xml_text)
    owner = root.find("reportingOwner")
    name = _text(owner, "reportingOwnerId/rptOwnerName") if owner is not None else ""
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = []
    if rel is not None:
        if _text(rel, "isDirector") in ("1", "true"):
            roles.append("Director")
        if _text(rel, "isOfficer") in ("1", "true"):
            roles.append(_text(rel, "officerTitle") or "Officer")
        if _text(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% owner")
    rows = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _num(_text(tx, "transactionAmounts/transactionShares/value"))
        price = _num(_text(tx, "transactionAmounts/transactionPricePerShare/value"))
        when = _text(tx, "transactionDate/value")[:10]
        if not code or shares is None:
            continue
        rows.append({"name": name.title(), "role": ", ".join(roles) or "Insider", "date": when, "code": code,
                     "action": CODES.get(code, "other"), "shares": shares, "price": price,
                     "value": round(shares * price, 2) if price else None,
                     "planned": _text(root, "aff10b5One") in ("1", "true")})
    return rows


def insiders(ticker: str, now: datetime | None = None) -> dict[str, Any]:
    headers = _sec_headers()
    if headers is None:
        return {"ok": False, "error": "Set EDGAR_USER_AGENT with a contact email in .env to read SEC insider filings."}
    import edgar_client
    cik = edgar_client.cik_for_ticker(ticker)
    if not cik:
        return {"ok": True, "applies": False, "summary": f"{ticker} has no SEC company filings (funds do not file Form 4).", "rows": []}
    today = (now or datetime.now(timezone.utc)).date()
    since = today - timedelta(days=FORM4_LOOKBACK_DAYS)
    sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers, timeout=15)
    if sub.status_code != 200:
        return {"ok": False, "error": f"SEC filing list unavailable (HTTP {sub.status_code})."}
    recent = (sub.json().get("filings") or {}).get("recent") or {}
    picks = []
    for i, form in enumerate(recent.get("form") or []):
        try:
            filed = date.fromisoformat(recent["filingDate"][i])
        except (KeyError, IndexError, ValueError):
            continue
        if form == "4" and filed >= since:
            picks.append((filed, recent["accessionNumber"][i], recent["primaryDocument"][i]))
    rows = []
    for filed, accession, primary in picks[:FORM4_MAX_DOCS]:
        acc = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary.split('/')[-1]}"
        time.sleep(SEC_PAUSE_SEC)
        try:
            doc = requests.get(url, headers=headers, timeout=15)
            if doc.status_code != 200 or len(doc.content) > 1_000_000:
                continue
            for row in parse_form4(doc.text):
                row["url"] = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary}"
                rows.append(row)
        except (requests.RequestException, ET.ParseError):
            continue
    return summarize_insiders(ticker, rows, len(picks))


def summarize_insiders(ticker: str, rows: list[dict[str, Any]], filings: int) -> dict[str, Any]:
    buys = [r for r in rows if r["code"] == "P"]
    sells = [r for r in rows if r["code"] == "S"]
    buy_value = sum(r["value"] or 0 for r in buys)
    sell_value = sum(r["value"] or 0 for r in sells)
    buyers = sorted({r["name"] for r in buys})
    sellers = sorted({r["name"] for r in sells})
    if buys:
        summary = (f"{len(buyers)} insider{'s' if len(buyers) != 1 else ''} bought {ticker} with their own money in the last "
                   f"{FORM4_LOOKBACK_DAYS} days ({_money(buy_value)}). Open-market buys are rarer than sales and worth a closer look.")
    elif sells:
        summary = (f"No insider bought on the open market in {FORM4_LOOKBACK_DAYS} days; {len(sellers)} sold ({_money(sell_value)}). "
                   "Sales are common — pay, taxes and planned sales — so they say less than buys.")
    elif filings:
        summary = f"{filings} insider filing{'s' if filings != 1 else ''} in {FORM4_LOOKBACK_DAYS} days, all grants, option exercises or tax withholding — no open-market buys or sales."
    else:
        summary = f"No insider filings for {ticker} in the last {FORM4_LOOKBACK_DAYS} days."
    shown = sorted(buys, key=lambda r: -(r["value"] or 0))[:5] + sorted(sells, key=lambda r: -(r["value"] or 0))[:5]
    return {"ok": True, "applies": True, "summary": summary, "filings": filings,
            "buys": {"people": len(buyers), "value": round(buy_value, 2)},
            "sells": {"people": len(sellers), "value": round(sell_value, 2)},
            "rows": shown, "source": "SEC EDGAR Form 4", "lookback_days": FORM4_LOOKBACK_DAYS}


# --- federal contracts and lobbying (matched by company name) -------------------------

def contracts(name: str, now: datetime | None = None) -> dict[str, Any]:
    if len(name) < 4:
        return {"ok": False, "error": "Company name too short to match federal records reliably."}
    today = (now or datetime.now(timezone.utc)).date()
    period = {"start_date": (today - timedelta(days=365)).isoformat(), "end_date": today.isoformat()}
    base = {"recipient_search_text": [name], "award_type_codes": ["A", "B", "C", "D"]}
    totals = requests.post(f"{USASPENDING}/spending_by_category/recipient/",
                           json={"filters": {**base, "time_period": [period]}, "limit": 10}, timeout=40)
    awards = requests.post(f"{USASPENDING}/spending_by_award/", timeout=40, json={
        "filters": {**base, "time_period": [{**period, "date_type": "new_awards_only"}]},
        "fields": ["Recipient Name", "Award Amount", "Awarding Agency", "Start Date", "Description", "generated_internal_id"],
        "limit": 5, "sort": "Award Amount", "order": "desc"})
    if totals.status_code != 200 or awards.status_code != 200:
        return {"ok": False, "error": f"USAspending unavailable (HTTP {totals.status_code}/{awards.status_code})."}
    recipients = totals.json().get("results") or []
    total = sum(_num(r.get("amount")) or 0 for r in recipients)
    rows = [{"date": a.get("Start Date"), "amount": _num(a.get("Award Amount")), "agency": a.get("Awarding Agency"),
             "recipient": a.get("Recipient Name"), "what": str(a.get("Description") or "")[:140],
             "url": f"https://www.usaspending.gov/award/{a['generated_internal_id']}" if a.get("generated_internal_id") else None}
            for a in awards.json().get("results") or []]
    matched = sorted({str(r.get("name") or "").title() for r in recipients if r.get("name")})[:5]
    summary = (f"About {_money(total)} in federal contract money went to companies matching “{name.title()}” in the last 12 months."
               if total > 0 else f"No federal contract money found for “{name.title()}” in the last 12 months.")
    return {"ok": True, "summary": summary, "total_12m": round(total, 2), "matched_names": matched, "new_awards": rows,
            "source": "USAspending.gov", "note": "Matched by company name, so a similarly named firm can appear. Check the names listed."}


def lobbying(name: str, now: datetime | None = None) -> dict[str, Any]:
    if len(name) < 4:
        return {"ok": False, "error": "Company name too short to match lobbying records reliably."}
    year = (now or datetime.now(timezone.utc)).year
    filings: list[dict[str, Any]] = []
    for filing_year in (year, year - 1):
        resp = requests.get(LDA_FILINGS, params={"client_name": name, "filing_year": filing_year, "page_size": 100},
                            headers={"Accept": "application/json"}, timeout=30)
        if resp.status_code != 200:
            return {"ok": False, "error": f"Senate lobbying database unavailable (HTTP {resp.status_code})."}
        filings = resp.json().get("results") or []
        if filings:
            year = filing_year
            break
    spend = sum((_num(f.get("income")) or 0) + (_num(f.get("expenses")) or 0) for f in filings)
    issues: dict[str, int] = {}
    for f in filings:
        for act in f.get("lobbying_activities") or []:
            label = act.get("general_issue_code_display")
            if label:
                issues[label] = issues.get(label, 0) + 1
    firms = sorted({(f.get("registrant") or {}).get("name", "") for f in filings} - {""})
    top = [k for k, _ in sorted(issues.items(), key=lambda kv: -kv[1])[:5]]
    summary = (f"{len(filings)} lobbying report{'s' if len(filings) != 1 else ''} in {year} naming “{name.title()}” as the client, "
               f"about {_money(spend)} reported, mostly on {', '.join(top[:3]).lower() or 'unlisted issues'}."
               if filings else f"No federal lobbying reports name “{name.title()}” as a client in {year - 1}–{year}.")
    return {"ok": True, "summary": summary, "year": year, "reports": len(filings), "spend": round(spend, 2),
            "top_issues": top, "firms": firms[:6], "source": "Senate Lobbying Disclosure (LDA)",
            "note": "Matched by client name; amounts are what the filings report for each quarter."}


# --- Congress trades (Quiver Quantitative, optional paid key) -------------------------

def congress(ticker: str, now: datetime | None = None) -> dict[str, Any]:
    key = (os.environ.get("QUIVER_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "connected": False,
                "error": "Not connected. Add QUIVER_API_KEY to .env (Quiver Quantitative API, from about $30/month) to see disclosed Congress trades."}
    resp = requests.get(QUIVER_CONGRESS.format(ticker=ticker), headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=20)
    if resp.status_code in (401, 403):
        return {"ok": False, "connected": False, "error": "Quiver rejected QUIVER_API_KEY; check the key and plan."}
    if resp.status_code != 200:
        return {"ok": False, "connected": True, "error": f"Quiver unavailable (HTTP {resp.status_code})."}
    today = (now or datetime.now(timezone.utc)).date()
    rows = []
    for r in resp.json() or []:
        try:
            traded = date.fromisoformat(str(r.get("TransactionDate"))[:10])
            reported = date.fromisoformat(str(r.get("ReportDate"))[:10])
        except ValueError:
            continue
        if (today - traded).days > 365:
            continue
        rows.append({"who": r.get("Representative"), "chamber": r.get("House"), "party": r.get("Party"),
                     "action": "bought" if "purchase" in str(r.get("Transaction")).lower() else "sold",
                     "range": r.get("Range"), "traded": traded.isoformat(), "reported": reported.isoformat(),
                     "delay_days": (reported - traded).days})
    rows.sort(key=lambda r: r["traded"], reverse=True)
    buys = sum(1 for r in rows if r["action"] == "bought")
    delays = sorted(r["delay_days"] for r in rows)
    delay = delays[len(delays) // 2] if delays else None
    summary = (f"{len(rows)} disclosed Congress trade{'s' if len(rows) != 1 else ''} in {ticker} over 12 months ({buys} buys). "
               + (f"Typical delay between the trade and the report: {delay} days, so the move may be long over." if delay is not None else "")
               if rows else f"No disclosed Congress trades in {ticker} over the last 12 months.")
    return {"ok": True, "connected": True, "summary": summary, "rows": rows[:10], "source": "Quiver Quantitative",
            "note": "Members report trades up to 45 days late, and studies disagree on whether copying them beats the market."}


# --- assembly, cache and route -----------------------------------------------------------

def _safe(fn, *args) -> dict[str, Any]:
    try:
        return fn(*args)
    except requests.RequestException as exc:
        return {"ok": False, "error": f"Source unreachable ({type(exc).__name__})."}
    except Exception as exc:  # noqa: BLE001 - one bad section never hides the others
        return {"ok": False, "error": f"Could not read this source ({type(exc).__name__})."}


def build(ticker: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    info: dict[str, Any] = {}
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001
        info = {}
    try:
        import edgar_client
        title = edgar_client.company_title(ticker)
    except Exception:  # noqa: BLE001
        title = ""
    name = search_name(title or info.get("longName") or info.get("shortName") or "")
    is_fund = str(info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND", "INDEX")
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="company-check") as pool:
        jobs = {
            "bottom_line": pool.submit(_safe, bottom_line, ticker, info) if info else pool.submit(_safe, bottom_line, ticker),
            "insiders": (pool.submit(lambda: {"ok": True, "applies": False, "summary": "Funds have no company insiders to track."})
                         if is_fund else pool.submit(_safe, insiders, ticker, now)),
            "congress": pool.submit(_safe, congress, ticker, now),
        }
        if name and not is_fund:
            jobs["contracts"] = pool.submit(_safe, contracts, name, now)
            jobs["lobbying"] = pool.submit(_safe, lobbying, name, now)
        sections = {k: f.result() for k, f in jobs.items()}
    for key in ("contracts", "lobbying"):
        sections.setdefault(key, {"ok": True, "applies": False,
                                  "summary": "Funds do not receive contracts or lobby." if is_fund else "No company name to search federal records with."})
    return {"ok": True, "ticker": ticker, "name": info.get("shortName") or title.title() or ticker,
            "search_name": name, "checked_at": now.isoformat(), "research_only": True, "sections": sections,
            "note": "Research only. Company check never places, sizes or blocks a trade."}


def check(ticker: str, *, refresh: bool = False) -> dict[str, Any]:
    ticker = clean_ticker(ticker)
    if not ticker:
        return {"ok": False, "error": "Enter a US stock symbol, like AAPL."}
    with _lock:
        hit = _cache.get(ticker)
        fresh = hit and time.time() - float(hit.get("_at") or 0) < CACHE_TTL_SEC
        if fresh and not refresh:
            return {k: v for k, v in hit.items() if k != "_at"} | {"cached": True}
        waiter = _running.get(ticker)
        owner = waiter is None
        if owner:
            waiter = _running[ticker] = threading.Event()
    if not owner:
        waiter.wait(90)
        with _lock:
            hit = _cache.get(ticker)
        return ({k: v for k, v in hit.items() if k != "_at"} | {"cached": True}) if hit else {"ok": False, "error": "Company check still running; try again."}
    try:
        result = build(ticker)
        with _lock:
            _cache[ticker] = {**result, "_at": time.time()}
            for old in sorted(_cache, key=lambda k: _cache[k].get("_at", 0))[:-MAX_CACHED]:
                _cache.pop(old, None)
        _save_cache()
        return result | {"cached": False}
    finally:
        with _lock:
            _running.pop(ticker, None)
        waiter.set()


def _cache_path() -> Path | None:
    return (_data_dir / "company_checks.json") if _data_dir else None


def _save_cache() -> None:
    path = _cache_path()
    if not path:
        return
    with _lock:
        data = json.dumps(_cache)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # optional cache


def _load_cache() -> None:
    path = _cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        with _lock:
            for k, v in data.items():
                if clean_ticker(k) and isinstance(v, dict) and isinstance(v.get("sections"), dict):
                    _cache[k] = v


def register(app, desk) -> None:
    from flask import jsonify, request
    configure(getattr(desk, "DATA_DIR", None))

    @app.get("/api/research/company")
    def company_check_route():
        refresh = str(request.args.get("refresh") or "").lower() in ("1", "true", "yes")
        result = check(request.args.get("ticker") or "", refresh=refresh)
        return jsonify(result), (200 if result.get("ok") else 400)
