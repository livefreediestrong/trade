"""Bounded discovery from Nasdaq's public US-listed symbol directory."""
import csv
from datetime import datetime, timezone
import io
import re
import threading
import requests

URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
_LOCK = threading.Lock()


def parse(text):
    rows = csv.DictReader(io.StringIO(text), delimiter="|")
    if not {"Symbol","Test Issue","Nasdaq Traded","Security Name","ETF","NextShares"} <= set(rows.fieldnames or []):
        raise ValueError("Unexpected Nasdaq directory columns")
    result = {}
    for row in rows:
        s = row.get("Symbol", "")
        name = row.get("Security Name") or ""
        if row.get("Nasdaq Traded") != "Y" or row.get("Test Issue") != "N" or row.get("NextShares") != "N":
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",s):
            continue
        if row.get("ETF") != "Y" and re.search(r"\b(warrants?|rights?|units?|preferred|preference|notes?|debentures?)\b",name,re.I):
            continue
        result[s] = {"symbol":s,"name":name,"etf":row.get("ETF")=="Y","exchange":row.get("Listing Exchange")}
    if not result:
        raise ValueError("No supported symbols in the directory")
    return sorted(result.values(),key=lambda r:r["symbol"])


def load(desk):
    return desk._load_json(desk.DATA_DIR/"market_universe.json", {"symbols":[],"updated_at":None,"error":None})


def status(desk):
    raw = load(desk)
    return {"count":len(raw.get("symbols",[])),"updated_at":raw.get("updated_at"),"error":raw.get("error"),
            "source":URL,"scope":"US-listed stocks and ETFs from the Nasdaq-traded directory; excludes test issues, warrants, rights, units, preferred shares and notes.",
            "cadence":"One focus candidate plus a bounded rotating batch per Moss cycle (configurable 2–8 total). Discovery does not certify liquidity, data availability or trading permission."}


def refresh(desk):
    if not _LOCK.acquire(blocking=False):
        return status(desk)
    try:
        attempted_at=datetime.now(timezone.utc).isoformat()
        response=requests.get(URL,timeout=15,headers={"User-Agent":"Tomahawk-LocalResearch/1.0"})
        response.raise_for_status()
        if len(response.content)>8_000_000:
            raise ValueError("Unexpectedly large symbol directory")
        rows=parse(response.text)
        # A partial server response must not replace a healthy universe.
        if len(rows)<1000:
            raise ValueError("Incomplete symbol directory; keeping previous universe")
        raw={"symbols":rows,"updated_at":attempted_at,"last_attempt_at":attempted_at,"error":None,"source":URL}
        path=desk.DATA_DIR/"market_universe.json"
        if str(path.resolve()) in desk._CORRUPT_PATHS:
            raise ValueError("Restore the unreadable universe file before refreshing")
        desk._save_json(path,raw)
    except Exception as exc:
        raw=load(desk)
        raw["last_attempt_at"]=attempted_at
        raw["error"]=str(exc)[:160] if isinstance(exc,ValueError) else f"Directory refresh unavailable: {type(exc).__name__}"
        if str((desk.DATA_DIR/"market_universe.json").resolve()) not in desk._CORRUPT_PATHS:
            desk._save_json(desk.DATA_DIR/"market_universe.json",raw)
    finally:
        _LOCK.release()
    return status(desk)


def candidates(desk, p, cursor, now):
    focus=p["symbols"]
    batch=max(2,min(8,int(p.get("candidates_per_cycle",2))))
    if p.get("universe") != "broad_us":
        return [focus[(cursor+i)%len(focus)] for i in range(min(batch,len(focus)))]
    raw=load(desk)
    from desk_workbench import timestamp
    stamp=timestamp(raw.get("updated_at"))
    # The scheduler calls from its single worker, never while holding the ledger lock.
    attempt=timestamp(raw.get("last_attempt_at"))
    needs_refresh=stamp is None or not 0 <= now.timestamp()-stamp <= 86400
    retry_due=attempt is None or not 0 <= now.timestamp()-attempt < 3600
    if needs_refresh and retry_due:
        refresh(desk);raw=load(desk)
    universe=[r["symbol"] for r in raw.get("symbols",[]) if r.get("symbol") not in focus]
    cycle=cursor//batch
    selected=[focus[cycle%len(focus)]]
    # Reserve discovery before choosing directory positions: replacing an emitted
    # directory slot while advancing its cursor permanently skipped some symbols.
    import market_discovery
    ranked = [s for s in market_discovery.symbols(desk, now) if s not in focus]
    shared_cycle = cycle if batch >= 3 else cycle//2
    shared = ranked[shared_cycle % len(ranked)] if ranked and (batch >= 3 or cycle % 2) else None
    directory_count = batch-1-bool(shared)
    directory_cursor = cycle*(batch-2) if ranked and batch >= 3 else cycle//2 if ranked else cycle*(batch-1)
    if universe:
        selected.extend(universe[(directory_cursor+i)%len(universe)] for i in range(min(directory_count,len(universe))))
    elif len(focus)>1:
        selected.extend(focus[(cycle+i)%len(focus)] for i in range(1,min(directory_count+1,len(focus))))
    # A bounded slot uses shared daily discovery, then Moss independently rechecks
    # current intraday data, liquidity, cost and risk. Preserve directory exploration.
    if shared and shared not in selected:
        selected.append(shared)
    return selected


def radar_candidates(desk, now=None):
    """Bounded rotating symbols for a fresh-quote radar fallback, never daily prices."""
    import market_discovery
    now = now or datetime.now(timezone.utc)
    ranked = market_discovery.symbols(desk, now, 20)
    directory = [r["symbol"].replace(".", "-") for r in load(desk).get("symbols", [])]
    if directory:
        offset = (int(now.timestamp())//300*60) % len(directory)
        ranked += [directory[(offset+i)%len(directory)] for i in range(min(60, len(directory)))]
    return list(dict.fromkeys(ranked))[:80]
