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
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import requests

CACHE_TTL_SEC = 6 * 3600
CACHE_VERSION = 5
MAX_CACHED = 80
FORM4_LOOKBACK_DAYS = 90
FORM4_MAX_DOCS = 20
SEC_PAUSE_SEC = 0.12  # SEC fair access: at most 10 requests per second
USASPENDING = "https://api.usaspending.gov/api/v2/search"
LDA_FILINGS = "https://lda.gov/api/v1/filings/"
LDA_REQUEST_INTERVAL = 4.1  # anonymous API: 15 requests/minute, shared across company checks
LDA_MAX_PAGES = 2  # up to 50 source rows per year; coverage remains explicit
_lda_lock = threading.Lock()
_lda_next_request = 0.0
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
    "P": "purchase (market or private)", "S": "sale (market or private)", "A": "grant or award",
    "M": "exercised or converted derivatives", "F": "shares used for tax or exercise payment", "G": "gift", "D": "returned to the company",
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
    if isinstance(value, bool):
        return None
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
    """Non-derivative evidence; joint owners are not separate purchases."""
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise ValueError("Unsupported filing XML")
    root = ET.fromstring(xml_text)
    # Accept ordinary namespace-qualified ownership XML without accepting HTML.
    for node in root.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]
    if root.tag != "ownershipDocument":
        raise ValueError("Response is not an ownership filing")
    owners = []
    for owner in root.findall('reportingOwner'):
        rel = owner.find('reportingOwnerRelationship')
        roles = []
        officer = rel is not None and _text(rel, 'isOfficer').lower() in ('1', 'true')
        if rel is not None:
            if _text(rel, 'isDirector').lower() in ('1', 'true'): roles.append('Director')
            if officer: roles.append(_text(rel, 'officerTitle') or 'Officer')
            if _text(rel, 'isTenPercentOwner').lower() in ('1', 'true'): roles.append('10% owner')
        cik = _text(owner, 'reportingOwnerId/rptOwnerCik')
        owners.append(dict(name=_text(owner, 'reportingOwnerId/rptOwnerName').title() or 'Unnamed owner',
                           cik=cik.zfill(10) if re.fullmatch(r'\d{1,10}', cik) else None,
                           role=', '.join(roles) or 'Other reporting owner', officer=officer))
    raw_notes = {n.get('id'): ''.join(n.itertext()).strip() for n in root.findall('footnotes/footnote')}
    footnotes = {fid:text[:2000] for fid,text in raw_notes.items()}
    flag = _text(root, 'aff10b5One').lower()
    planned = True if flag in ('1', 'true') else False if flag in ('0', 'false') else None
    plan_label = ('Filing marks a 10b5-1 plan; check which rows it covers.' if planned is True else
                  'Plan box not checked; this does not prove a discretionary trade.' if planned is False else
                  'Plan checkbox unavailable; trading-plan status is unknown.')
    transactions = root.findall('nonDerivativeTable/nonDerivativeTransaction')
    rows = []
    for index, tx in enumerate(transactions):
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _num(_text(tx, "transactionAmounts/transactionShares/value"))
        price = _num(_text(tx, "transactionAmounts/transactionPricePerShare/value"))
        when = _text(tx, "transactionDate/value")[:10]
        direction = _text(tx, 'transactionAmounts/transactionAcquiredDisposedCode/value')
        issues = []
        if not code: issues.append('Transaction code missing')
        if shares is None or shares <= 0: issues.append('Positive share quantity unavailable')
        if price is not None and price < 0: price = None
        if code in ('P', 'S') and direction != {'P':'A', 'S':'D'}[code]: issues.append('Acquired/disposed flag missing or inconsistent')
        value = _num(shares * price) if shares is not None and price is not None else None
        refs = list(dict.fromkeys(n.get('id') for n in tx.findall('.//footnoteId') if n.get('id')))
        after = _num(_text(tx, 'postTransactionAmounts/sharesOwnedFollowingTransaction/value'))
        ownership = _text(tx, 'ownershipNature/directOrIndirectOwnership/value')
        security = _text(tx, 'securityTitle/value')
        same_holding = [n for n in transactions if _text(n, 'securityTitle/value') == security and
                        _text(n, 'ownershipNature/directOrIndirectOwnership/value') == ownership]
        before = after - shares if after is not None and shares is not None and direction == 'A' else (
                 after + shares if after is not None and shares is not None and direction == 'D' else None)
        change = None
        if len(same_holding) == len(owners) == 1 and ownership == 'D' and not issues and before is not None and before > 0 and after >= 0:
            change = round((after-before)/before*100, 2)
        rows.append({'row_index':index, 'owners':owners, 'name':'; '.join(o['name'] for o in owners) or 'Owner unavailable',
                     'role':'; '.join(o['role'] for o in owners) or 'Unknown', 'officer':any(o['officer'] for o in owners),
                     'owner_key':owners[0]['cik'] if len(owners) == 1 else None, 'joint':len(owners) > 1,
                     'date':when, 'code':code, 'action':CODES.get(code, 'other transaction'), 'shares':shares,
                     'price':price, 'value':round(value, 2) if value is not None and not issues else None,
                     'planned':planned, 'plan_label':plan_label, 'security':security or 'Security title unavailable',
                     'ownership':ownership, 'ownership_note':_text(tx, 'ownershipNature/natureOfOwnership/value'),
                     'shares_after':after, 'holding_change_pct':change, 'issues':issues,
                     'amended':_text(root, 'documentType') == '4/A', 'original_filed':_text(root, 'dateOfOriginalSubmission'),
                     'footnotes':[{'id':fid,'text':footnotes.get(fid, 'Footnote text unavailable')} for fid in refs[:12]],
                     'filing_notes':[{'id':fid,'text':text} for fid,text in list(footnotes.items())[:30]],
                     'notes_truncated':len(refs)>12 or len(footnotes)>30 or any(len(text)>2000 for text in raw_notes.values())})
    return rows


def insiders(ticker: str, now: datetime | None = None) -> dict[str, Any]:
    headers = _sec_headers()
    if headers is None:
        return {"ok": False, "error": "Set EDGAR_USER_AGENT with a contact email in .env to read SEC insider filings."}
    import edgar_client
    cik = edgar_client.cik_for_ticker(ticker)
    if not cik:
        return {"ok": False, "error": f"Could not resolve {ticker} in the SEC company list. Insider activity is unknown.", "rows": []}
    today = (now or datetime.now(timezone.utc)).date()
    since = today - timedelta(days=FORM4_LOOKBACK_DAYS)
    sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers, timeout=15)
    if sub.status_code != 200:
        return {"ok": False, "error": f"SEC filing list unavailable (HTTP {sub.status_code})."}
    listing = sub.json().get('filings') or {}
    recent = listing.get('recent') or {}
    picks, seen, malformed, listed_dates = [], {}, 0, []
    for i, form in enumerate(recent.get("form") or []):
        try:
            filed = date.fromisoformat(recent["filingDate"][i])
        except (KeyError, IndexError, ValueError):
            if form in ('4', '4/A'): malformed += 1
            continue
        listed_dates.append(filed)
        if form in ("4", "4/A") and since <= filed <= today:
            try:
                accession, primary = recent['accessionNumber'][i], recent['primaryDocument'][i]
                if not re.fullmatch(r'\d[\d-]{3,25}', accession) or not re.fullmatch(r'[\w./-]+\.xml', primary, re.ASCII) or '..' in primary.split('/'):
                    malformed += 1
                    continue
                accepted = (recent.get('acceptanceDateTime') or [])[i] if i < len(recent.get('acceptanceDateTime') or []) else None
                metadata = (filed, primary, form, accepted)
                if accession in seen:
                    if seen[accession] != metadata: malformed += 1
                    continue
                seen[accession] = metadata
                picks.append((filed, accession, primary, form, accepted))
            except (KeyError, IndexError):
                malformed += 1
                continue
    picks.sort(reverse=True)
    history_truncated = bool(listing.get('files')) and (not listed_dates or min(listed_dates) > since)
    rows, reviewed, excluded_dates, amendments = [], 0, 0, sum(p[3] == '4/A' for p in picks)
    deadline = time.monotonic() + 30
    for filed, accession, primary, form, accepted in picks[:FORM4_MAX_DOCS]:
        if time.monotonic() >= deadline:
            break
        acc = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary.split('/')[-1]}"
        time.sleep(SEC_PAUSE_SEC)
        try:
            doc = requests.get(url, headers=headers, timeout=(3, max(0.1, min(10, deadline - time.monotonic()))))
            if doc.status_code != 200 or len(doc.content) > 1_000_000:
                continue
            parsed = parse_form4(doc.text)
            reviewed += 1
            for row in parsed:
                try:
                    transaction_day = date.fromisoformat(row["date"])
                except (TypeError, ValueError):
                    excluded_dates += 1
                    continue
                if not since <= transaction_day <= today:
                    continue
                if transaction_day > filed:
                    excluded_dates += 1
                    continue
                row.update(url=url, filed=filed.isoformat(), accepted_at=accepted, accession=accession,
                           delay_days=(filed-transaction_day).days, amended=form == '4/A' or row['amended'])
                rows.append(row)
        except (requests.RequestException, ET.ParseError, ValueError):
            continue
    if picks and not reviewed:
        return {"ok": False, "error": f"Found {len(picks)} Form 4 filings, but none could be read. Insider activity is unknown.",
                "filings": len(picks), "reviewed_filings": 0, "complete": False, "rows": []}
    complete = reviewed == len(picks) and not malformed and not history_truncated and not excluded_dates and not any(r['issues'] for r in rows)
    result = summarize_insiders(ticker, rows, len(picks), today=today, amendments=amendments, complete=complete)
    result.update(reviewed_filings=reviewed, complete=complete, malformed_filings=malformed,
                  excluded_dates=excluded_dates, history_truncated=history_truncated,
                  available_at=(now or datetime.now(timezone.utc)).isoformat())
    result['note'] = (f'Read {reviewed} of {len(picks)} listed Form 4 / 4/A filings, capped at {FORM4_MAX_DOCS} documents. '
                      f'Non-derivative transactions within {FORM4_LOOKBACK_DAYS} days only; derivatives and older submission pages are not read. '
                      'This is an on-demand, bounded sample, not a real-time feed or a complete insider audit. '
                      'Missing prices stay unknown; value estimates assume USD prices. Reporting-owner amounts may overlap. '
                      'Calendar-day filing delays are not legal lateness determinations. Use disclosure/availability time, not trade date, in any later backtest.')
    if reviewed < len(picks):
        result['summary'] = f'Partial filing coverage: {reviewed} of {len(picks)} read. ' + result['summary'] + ' Unread filings may contain additional activity.'
    if malformed or excluded_dates or history_truncated:
        result['summary'] = 'Coverage needs review: some filing metadata/dates or older submission pages were not resolved. ' + result['summary']
    return result


def summarize_insiders(ticker: str, rows: list[dict[str, Any]], filings: int, *, today=None, amendments=0, complete=True) -> dict[str, Any]:
    amendments = max(amendments, int(any(r.get('amended') for r in rows)))
    clean = [r for r in rows if not r.get('issues') and not r.get('amended')]
    def totals(code):
        group = [r for r in clean if r['code'] == code]
        missing = sum(r.get('value') is None for r in group)
        subtotal = sum(r.get('value') or 0 for r in group)
        return dict(rows=len(group), people=len({r['owner_key'] for r in group if r.get('owner_key')}) if not amendments else None,
                    value=round(subtotal, 2) if not missing and not amendments else None,
                    known_value=round(subtotal, 2) if not amendments else None, unpriced_rows=missing,
                    ambiguous_owner_rows=sum(not r.get('owner_key') for r in group))
    buys, sells = totals('P'), totals('S')
    if amendments:
        summary = f'{amendments} amended filing(s) need comparison with the originals. Purchase/sale totals and cluster assessment are withheld until reconciled.'
    else:
        summary = f"{ticker}: {buys['rows']} purchase and {sells['rows']} sale rows with usable transaction fields in the filings read. P/S codes include both market and private transactions; they do not establish the insider's motive."
        if buys['unpriced_rows'] or sells['unpriced_rows']: summary += ' Some prices are missing, so complete dollar totals are unknown.'
        if not filings: summary = f'No Form 4 / 4/A filings found in the recent submission page for {ticker} within {FORM4_LOOKBACK_DAYS} days.'
    end = today or datetime.now(timezone.utc).date()
    since = end - timedelta(days=29)
    clustered = [r for r in clean if r['code'] == 'P' and r.get('owner_key') and not r.get('joint')
                 and r.get('ownership') == 'D' and since.isoformat() <= r.get('date', '') <= end.isoformat()]
    owners = {r['owner_key'] for r in clustered}
    uncertain = amendments or not complete
    cluster = dict(status='needs_review' if uncertain else 'observed' if len(owners) >= 2 else 'not_observed',
                   people=None if uncertain else len(owners), window_days=30,
                   method='At least two distinct SEC owner IDs reporting direct, single-owner P purchases within the latest 30 calendar days. Includes private purchases and plan-related filings. Joint/indirect ownership, missing IDs and invalid rows are excluded. This is a descriptive research flag, not a buy signal.')
    shown = sorted(rows, key=lambda r:(r.get('filed') or '', r.get('date') or '', r.get('accession') or ''), reverse=True)[:100]
    return dict(ok=True, applies=True, summary=summary, filings=filings, buys=buys, sells=sells, rows=shown,
                row_count=len(rows), rows_truncated=len(rows)>len(shown), amendments=amendments, cluster=cluster,
                flagged_plan_rows=sum(r.get('planned') is True for r in rows), unknown_plan_rows=sum(r.get('planned') is None for r in rows),
                issue_rows=sum(bool(r.get('issues')) for r in rows), source='SEC EDGAR Form 4 / 4/A', lookback_days=FORM4_LOOKBACK_DAYS)


# --- federal contracts and lobbying (matched by company name) -------------------------

def _legal_name(value: Any) -> str:
    """Compare whole legal names, never shared keywords or guessed subsidiaries."""
    text = re.sub(r"/[A-Z]{2,3}/", " ", str(value or "").upper())
    text = " ".join(re.findall(r"[A-Z0-9]+", text.replace("&", " AND ")))
    for full, short in (("INCORPORATED", "INC"), ("CORPORATION", "CORP"),
                        ("LIMITED", "LTD"), ("COMPANY", "CO")):
        text = re.sub(r"\b" + full + r"\b", short, text)
    return text


def contracts(name: str, now: datetime | None = None) -> dict[str, Any]:
    if len(name) < 4 or len(search_name(name)) < 4:
        return {"ok": False, "error": "Company name too short to match federal records reliably."}
    today = (now or datetime.now(timezone.utc)).date()
    period = {"start_date": (today - timedelta(days=365)).isoformat(), "end_date": today.isoformat()}
    base = {"recipient_search_text": [search_name(name)], "award_type_codes": ["A", "B", "C", "D"]}
    totals = requests.post(f"{USASPENDING}/spending_by_category/recipient/",
                           json={"filters": {**base, "time_period": [period]}, "limit": 10}, timeout=40)
    awards = requests.post(f"{USASPENDING}/spending_by_award/", timeout=40, json={
        "filters": {**base, "time_period": [{**period, "date_type": "new_awards_only"}]},
        "fields": ["Recipient Name", "Recipient UEI", "recipient_id", "Award Amount", "Awarding Agency", "Start Date", "Description", "generated_internal_id"],
        "limit": 5, "sort": "Award Amount", "order": "desc"})
    if totals.status_code != 200 or awards.status_code != 200:
        return {"ok": False, "error": f"USAspending unavailable (HTTP {totals.status_code}/{awards.status_code})."}
    recipient_page, award_page = totals.json(), awards.json()
    recipients = recipient_page.get("results") or []
    all_awards = award_page.get("results") or []
    if (not isinstance(recipients, list) or not isinstance(all_awards, list)
            or any(not isinstance(r, dict) for r in recipients + all_awards)):
        raise ValueError("Malformed federal contract records")
    target = _legal_name(name)
    same_name = lambda value: bool(target) and _legal_name(value) == target
    candidates = [r for r in recipients if same_name(r.get("name"))]
    candidate_awards = [r for r in all_awards if same_name(r.get("Recipient Name"))]
    rejected = sorted({str(r.get("name") or "Name unavailable") for r in recipients if not same_name(r.get("name"))}
                      | {str(r.get("Recipient Name") or "Name unavailable") for r in all_awards if not same_name(r.get("Recipient Name"))})
    rows = [{"date": a.get("Start Date"), "amount": _num(a.get("Award Amount")), "agency": a.get("Awarding Agency"),
             "recipient": a.get("Recipient Name"), "recipient_uei": a.get("Recipient UEI"), "recipient_id": a.get("recipient_id"),
             "identity_verified": False, "match_status": "same_legal_name_unverified",
             "what": str(a.get("Description") or "")[:140],
             "url": f"https://www.usaspending.gov/award/{a['generated_internal_id']}" if a.get("generated_internal_id") else None}
            for a in candidate_awards]
    matched = sorted({str(r["name"]) for r in candidates} | {str(a["Recipient Name"]) for a in candidate_awards})
    amounts = [_num(r.get("amount")) for r in candidates]
    candidate_total = _num(sum(amounts)) if amounts and all(n is not None for n in amounts) else None
    candidate_total = round(candidate_total, 2) if candidate_total is not None else None
    summary = (f"Possible records use the same full name as {name}. Company identity still needs verification; its contract total is unknown."
               if matched else f"No same-name recipient was found in the records read for {name}; its contract total is unknown, not zero.")
    return {"ok": True, "summary": summary, "total_12m": None, "identity_verified": False,
            "match_status": "same_legal_name_unverified" if matched else "no_same_name_in_sample",
            "candidate_obligations_12m": candidate_total, "matched_names": matched, "new_awards": rows,
            "excluded_names": rejected[:15], "excluded_recipient_rows": len(recipients)-len(candidates),
            "excluded_award_rows": len(all_awards)-len(candidate_awards),
            "coverage": {"recipient_rows_read": len(recipients), "award_rows_read": len(all_awards),
                         "recipient_has_more": (recipient_page.get("page_metadata") or {}).get("hasNext"),
                         "award_has_more": (award_page.get("page_metadata") or {}).get("hasNext"),
                         "recipient_limit": 10, "award_limit": 5},
            "source": "USAspending.gov",
            "note": "Read up to 10 recipient groups and 5 new awards from a name search. Different full names are excluded, including unverified subsidiaries. Matching names and recipient IDs alone do not link a recipient to this stock. Any displayed obligations belong to these possible matches only; they are not verified company revenue or a complete company total."}


def _lda_page(params: dict[str, Any], deadline: float) -> dict[str, Any]:
    """One fixed-host request, with a shared request interval and bounded waiting."""
    global _lda_next_request
    if not _lda_lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise requests.Timeout("Lobbying source queue is busy")
    try:
        wait = max(0, _lda_next_request - time.monotonic())
        if time.monotonic() + wait >= deadline:
            raise requests.Timeout("Lobbying source is cooling down")
        if wait:
            time.sleep(wait)
        _lda_next_request = time.monotonic() + LDA_REQUEST_INTERVAL
        resp = requests.get(LDA_FILINGS, params=params, headers={"Accept": "application/json"},
                            timeout=max(.1, min(15, deadline - time.monotonic())))
        if resp.status_code == 429:
            retry = _num(resp.headers.get("Retry-After"))
            if retry is None:
                try:
                    retry = (parsedate_to_datetime(resp.headers.get("Retry-After", ""))
                             - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    retry = 60
            _lda_next_request = max(_lda_next_request, time.monotonic() + max(60, retry))
        if resp.status_code != 200:
            raise ValueError(f"Lobbying source unavailable (HTTP {resp.status_code}).")
        data = resp.json()
        if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
                or any(not isinstance(f, dict) for f in data["results"])):
            raise ValueError("Lobbying source returned unreadable filing records.")
        return data
    finally:
        _lda_lock.release()


def _lobbying_record(f: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Keep each disclosed amount and its meaning; never invent a company total."""
    client, reg = f.get("client") or {}, f.get("registrant") or {}
    issues = []
    try:
        fid = str(UUID(str(f.get("filing_uuid"))))
    except (ValueError, TypeError, AttributeError):
        fid = None
        issues.append("Filing ID missing or invalid")
    try:
        posted = datetime.fromisoformat(str(f.get("dt_posted")).replace('Z', '+00:00'))
        if posted.tzinfo is None or posted > now:
            raise ValueError()
        posted = posted.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        posted = None
        issues.append("Posted date missing, invalid or in the future")
    code = str(f.get("filing_type") or '')
    quarter = re.fullmatch(r'(?:Q([1-4])|([1-4])[TA@])(Y)?', code)
    period = f.get("filing_period")
    periods = ('first_quarter', 'second_quarter', 'third_quarter', 'fourth_quarter')
    if not quarter or period != periods[int(quarter[1] or quarter[2])-1]:
        issues.append("Quarterly filing type or period needs review")
    amended = bool(quarter and ('A' in code or '@' in code))
    income, expenses = _num(f.get("income")), _num(f.get("expenses"))
    # A non-empty but invalid other field must not disappear into a plausible amount.
    invalid = any(f.get(k) is not None and _num(f.get(k)) is None for k in ('income', 'expenses'))
    both = income is not None and expenses is not None
    if invalid or both:
        amount, kind = None, 'needs_review'
        issues.append("Income/expense fields conflict or contain an invalid amount")
    elif income is not None:
        amount, kind = income, 'firm_income'
    elif expenses is not None:
        amount, kind = expenses, 'organization_expenses'
    else:
        amount, kind = None, 'not_given'
    if amount is not None and amount < 0:
        issues.append("Negative reported amount; inspect the original")
    if amount == 0:
        issues.append("Zero in API; check original for a below-threshold amount")
    def public_id(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
    client_id, reg_id = public_id(client.get('id')), public_id(reg.get('id'))
    if not client_id or not reg_id:
        issues.append("Client or registrant ID missing")
    acts = f.get('lobbying_activities') or []
    if not isinstance(acts, list) or any(not isinstance(a, dict) for a in acts):
        acts = []
        issues.append("Issue details unreadable")
    return {"filing_uuid": fid, "client": str(client.get('name') or ''), "client_id": client_id,
            "client_filing_id": public_id(client.get('client_id')), "registrant": str(reg.get('name') or ''),
            "registrant_id": reg_id, "year": f.get('filing_year'), "period": period,
            "filing_type": code, "filing_type_label": str(f.get('filing_type_display') or code),
            "posted_at": posted, "amended": amended, "no_activity": bool(quarter and quarter[3]),
            "termination": bool(quarter and ('T' in code or '@' in code)),
            "income": income, "expenses": expenses, "amount": amount, "amount_kind": kind,
            "expense_method": str(f.get('expenses_method') or ''),
            "issue_areas": sorted({str(a['general_issue_code_display']) for a in acts if a.get('general_issue_code_display')}),
            "issues": issues, "identity_verified": False,
            "url": f"https://lda.gov/filings/public/filing/{fid}/print/" if fid else None}


def lobbying(name: str, now: datetime | None = None) -> dict[str, Any]:
    if len(name) < 4 or len(search_name(name)) < 4:
        return {"ok": False, "error": "Company name too short to match lobbying records reliably."}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    target, deadline = _legal_name(name), time.monotonic() + 65
    coverage, warnings, excluded = [], [], set()
    records, duplicates, excluded_types = [], 0, 0
    for year in (now.year, now.year - 1):
        raw, complete, count, pages = [], False, None, 0
        for page in range(1, LDA_MAX_PAGES + 1):
            try:
                data = _lda_page({'client_name':search_name(name), 'filing_year':year,
                                  'page_size':25, 'page':page}, deadline)
            except (requests.RequestException, ValueError) as exc:
                warnings.append(str(exc) if isinstance(exc, ValueError)
                                else f"Lobbying source did not finish ({type(exc).__name__}).")
                break
            pages += 1
            new_count = data.get('count') if type(data.get('count')) is int and data['count'] >= 0 else None
            if count is not None and new_count != count:
                warnings.append('The source result count changed while pages were being read.')
            count = new_count
            raw.extend(data['results'][:25])
            complete = ('next' in data and data['next'] is None and count is not None
                        and len(raw) >= count and len(data['results']) <= 25)
            if not data.get('next'):
                break
        unique_ids = {str(f.get('filing_uuid')) for f in raw if f.get('filing_uuid')}
        if complete and len(unique_ids) < count:
            complete = False
            warnings.append('Duplicate or missing filing IDs leave the source coverage uncertain.')
        coverage.append({'year':year, 'pages_read':pages, 'rows_read':len(raw),
                         'source_count':count, 'all_search_pages_read':complete})
        seen, records, duplicates = {}, [], 0
        for f in raw:
            client, reg = f.get('client'), f.get('registrant')
            if not isinstance(client, dict) or not isinstance(reg, dict):
                warnings.append('A filing had unreadable client or registrant details.')
                continue
            if _legal_name(client.get('name')) != target:
                excluded.add(str(client.get('name') or 'Name unavailable'))
                continue
            if type(f.get('filing_year')) is not int or f['filing_year'] != year:
                warnings.append('A same-name filing had a missing or unexpected reporting year.')
                continue
            # Registration filings carry no quarterly spending; do not count them.
            if f.get('filing_type') in ('RR', 'RA'):
                excluded_types += 1
                continue
            r = _lobbying_record(f, now)
            fid = r['filing_uuid']
            if fid and fid in seen and seen[fid] == r:
                duplicates += 1
                continue
            if fid and fid in seen:
                r['issues'].append('Conflicting copies of the same filing ID')
                seen[fid]['issues'].append('Conflicting copies of the same filing ID')
            if fid:
                seen[fid] = r
            records.append(r)
        # Only look at the prior year after a complete, readable current-year search
        # has no matching quarterly records. Never hide a current-year outage.
        if records or not complete or warnings:
            break
    groups: dict[tuple, list] = {}
    for i, r in enumerate(records):
        key = (r['registrant_id'], r['client_id'], r['year'], r['period'])
        if not r['registrant_id'] or not r['client_id']:
            key = ('unlinked', i)
        groups.setdefault(key, []).append(r)
    for group in groups.values():
        group.sort(key=lambda r:r['posted_at'] or '', reverse=True)
        # Latest is only among the downloaded filings, not a claim of full history.
        ambiguous = (any(r['issues'] for r in group)
                     or (len(group) > 1 and (not group[0]['amended']
                         or group[0]['posted_at'] == group[1]['posted_at'])))
        for index, r in enumerate(group):
            r['status'] = 'needs_review' if ambiguous else 'older_in_sample' if index else 'latest_in_sample'
            r['versions_in_sample'] = len(group)
            if ambiguous and not r['issues']:
                r['issues'].append('The versions of this quarterly report need review')
    records.sort(key=lambda r:r['posted_at'] or '', reverse=True)
    latest = [r for r in records if r['status'] == 'latest_in_sample']
    review = sum(r['status'] == 'needs_review' for r in records)
    issues: dict[str, int] = {}
    for r in latest:
        for label in r['issue_areas']:
            issues[label] = issues.get(label, 0) + 1
    summary = (f"{len(records)} possible same-name quarterly filings for {name} in {year}. "
               "Company identity is unverified; no combined spending total is calculated."
               if records else f"No same-name quarterly filings were found in the records read for {name}. "
               "That does not establish zero lobbying spending.")
    if not records and warnings and not any(c['pages_read'] for c in coverage):
        return {"ok":False, "complete":False, "error":warnings[0], "coverage":coverage}
    return {"ok":True, "complete":all(c['all_search_pages_read'] for c in coverage) and not warnings and not review,
            "summary":summary, "year":year, "reports":len(records), "latest_reports":len(latest),
            "spend":None, "identity_verified":False, "records":records, "review_rows":review,
            "duplicate_rows":duplicates, "registration_rows_excluded":excluded_types,
            "top_issues":[k for k,_ in sorted(issues.items(), key=lambda kv:(-kv[1],kv[0]))[:5]],
            "firms":sorted({r['registrant'] for r in latest if r['registrant']})[:6],
            "excluded_names":sorted(excluded), "coverage":coverage, "warnings":list(dict.fromkeys(warnings)),
            "retrieved_at":datetime.now(timezone.utc).isoformat(), "source":"Senate Lobbying Disclosure (LDA.gov)",
            "note":"Full-name matches are candidates, not verified issuer links. Firm income and organization expenses can overlap and are never added here. Latest means latest in this bounded sample; later amendments or other clients may be missing. Amounts are reported estimates; check original filings for rounding and below-threshold values. "
                   "Senate Office of Public Records cannot vouch for the data or analyses derived from these data after the data have been retrieved from LDA.gov."}


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
    legal_name = title or info.get("longName") or info.get("shortName") or ""
    name = search_name(legal_name)
    is_fund = str(info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND", "INDEX")
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="company-check") as pool:
        jobs = {
            "bottom_line": pool.submit(_safe, bottom_line, ticker, info) if info else pool.submit(_safe, bottom_line, ticker),
            "insiders": (pool.submit(lambda: {"ok": True, "applies": False, "summary": "Funds have no company insiders to track."})
                         if is_fund else pool.submit(_safe, insiders, ticker, now)),
            "congress": pool.submit(_safe, congress, ticker, now),
        }
        if name and not is_fund:
            jobs["contracts"] = pool.submit(_safe, contracts, legal_name, now)
            jobs["lobbying"] = pool.submit(_safe, lobbying, legal_name, now)
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
        try:
            age = time.time() - float((hit or {}).get("_at") or 0)
        except (TypeError, ValueError):
            age = float("inf")
        sections = (hit or {}).get("sections") or {}
        complete = all(isinstance(s, dict) and s.get("ok") and s.get("complete", True) for s in sections.values())
        fresh = hit and hit.get("_cache_version") == CACHE_VERSION and 0 <= age < (CACHE_TTL_SEC if complete else 60)
        if fresh and not refresh:
            return {k: v for k, v in hit.items() if not k.startswith("_")} | {"cached": True}
        waiter = _running.get(ticker)
        owner = waiter is None
        if owner:
            waiter = _running[ticker] = threading.Event()
    if not owner:
        if not waiter.wait(90):
            return {"ok": False, "error": "Company check still running; try again."}
        with _lock:
            hit = _cache.get(ticker)
        return ({k: v for k, v in hit.items() if not k.startswith("_")} | {"cached": True}) if hit else {"ok": False, "error": "Company check still running; try again."}
    try:
        result = build(ticker)
        with _lock:
            _cache[ticker] = {**result, "_at": time.time(), "_cache_version": CACHE_VERSION}
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
