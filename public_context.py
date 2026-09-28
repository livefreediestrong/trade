"""Dated public reference data. Read-only research context, never order authority."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Blueprint, jsonify
import market_events

SOURCES = {
    "treasury": {"name": "US Treasury yield curve", "owner": "US Treasury", "seconds": 3600,
                 "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month={month}"},
    "sofr": {"name": "SOFR", "owner": "Federal Reserve Bank of New York", "seconds": 3600,
             "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json"},
    "vix": {"name": "VIX daily history", "owner": "Cboe", "seconds": 3600,
            "url": "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"},
    "halts": {"name": "Trading halts", "owner": "Nasdaq Trader", "seconds": 60,
              "url": "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"},
}


def finite(value):
    if isinstance(value, bool):
        raise ValueError("Boolean reference value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite reference value")
    return number


def observation(series, date, value, unit, **extra):
    date = datetime.strptime(str(date)[:10], "%Y-%m-%d").date().isoformat()
    return {"id": series + ":" + date, "series": series, "observation_date": date,
            "value": finite(value), "unit": unit, "published_at": None, **extra}


def xml_root(content):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("Unsupported XML declarations")
    return ET.fromstring(content)


def parse(source, content, now):
    rows = []
    if source == "sofr":
        for row in json.loads(content)["refRates"]:
            if row.get("type") == "SOFR":
                rows.append(observation("SOFR", row["effectiveDate"], row["percentRate"], "percent",
                                        publisher_revision=row.get("revisionIndicator") or None))
    elif source == "vix":
        for row in list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))[-45:]:
            date = datetime.strptime(row["DATE"], "%m/%d/%Y").date().isoformat()
            rows.append(observation("VIX close", date, row["CLOSE"], "index points"))
    elif source == "treasury":
        root = xml_root(content)
        for props in root.iter("{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties"):
            row = {e.tag.rsplit("}", 1)[-1]: e.text for e in props}
            for field, label in (("BC_3MONTH", "Treasury 3 month"), ("BC_2YEAR", "Treasury 2 year"), ("BC_10YEAR", "Treasury 10 year")):
                if row.get(field) is not None:
                    rows.append(observation(label, row["NEW_DATE"], row[field], "percent"))
    elif source == "halts":
        root = xml_root(content)
        if root.tag != "rss":
            raise ValueError("Not a Nasdaq RSS feed")
        for item in root.findall("./channel/item")[:1000]:
            row = {e.tag.rsplit("}", 1)[-1]: (e.text or "").strip() for e in item}
            date = datetime.strptime(row["HaltDate"], "%m/%d/%Y").date().isoformat()
            start = datetime.fromisoformat(date + "T" + row["HaltTime"]).replace(tzinfo=market_events.ET)
            resumed = None
            if row.get("ResumptionDate") and row.get("ResumptionTradeTime"):
                day = datetime.strptime(row["ResumptionDate"], "%m/%d/%Y").date().isoformat()
                resumed = datetime.fromisoformat(day + "T" + row["ResumptionTradeTime"]).replace(tzinfo=market_events.ET).astimezone(timezone.utc).isoformat()
            rows.append({"id": row["IssueSymbol"] + ":" + start.isoformat(), "symbol": row["IssueSymbol"],
                         "series": "Trading halt", "observation_date": date, "published_at": None,
                         "halt_at": start.astimezone(timezone.utc).isoformat(), "resumption_at": resumed,
                         "reason": row.get("ReasonCode"), "unit": None, "value": None})
    if not rows and source != "halts":
        raise ValueError("No reference observations parsed")
    # Future observation dates are not usable context; no guessed publication time.
    today = now.astimezone(market_events.ET).date().isoformat()
    return [r for r in rows if r["observation_date"] <= today]


def retain_versions(rows, previous, now):
    prior = {r["id"]: r for r in previous}
    for row in rows:
        fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:20]
        old = prior.get(row["id"], {})
        history = copy.deepcopy(old.get("revisions") or [])
        if old.get("vintage_id") and fingerprint != old["vintage_id"]:
            history.append({k: old.get(k) for k in ("vintage_id", "value", "unit", "first_seen_at", "last_seen_at", "resumption_at")})
        row.update(vintage_id=fingerprint, first_seen_at=(old.get("first_seen_at") if old.get("vintage_id") == fingerprint else None) or now.isoformat(),
                   last_seen_at=now.isoformat(), revisions=history[-5:])
    return rows


def session_brief(now, events, headlines):
    import research_companion
    opening = research_companion.next_session(now)
    day = opening[:10] if opening else None
    releases = [r for r in events if str(r.get("start", ""))[:10] == day and r.get("impact") in ("medium", "high")]
    # The brief links retained evidence; it does not fabricate an overnight gap.
    return {"next_session": opening, "events": releases[:20], "held_headlines": len(headlines),
            "note": "Pre-open checklist from retained sources. Calendar coverage can be partial. Verify current broker quotes for overnight gaps; daily reference closes cannot measure them. Short-interest and float measurements are unavailable until a dated issuer-matched source is connected. Short-sale volume is not short interest."}


class PublicContext:
    def __init__(self, desk):
        self.desk = desk
        self.lock = threading.Lock()
        self.path = desk.DATA_DIR / "public_context.json"

    def load(self):
        value = self.desk._load_json(self.path, {"version": 1, "sources": {}})
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("sources"), dict):
            raise ValueError("Public context needs recovery")
        return value

    def refresh(self, now=None):
        now = now or datetime.now(timezone.utc)
        if not self.lock.acquire(blocking=False):
            return
        try:
            raw = self.load()
            due = [key for key, spec in SOURCES.items() if now.timestamp() >= raw["sources"].get(key, {}).get("next_at", 0)]
            def fetch(key):
                spec = SOURCES[key]
                url = spec["url"].format(month=now.astimezone(market_events.ET).strftime("%Y%m"))
                content = market_events._fetch(url, max_bytes=4_000_000, read_timeout=12)
                return url, parse(key, content, now)
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="public-context") as pool:
                pending = {key: pool.submit(fetch, key) for key in due}
                for key, job in pending.items():
                    old = raw["sources"].get(key, {})
                    try:
                        url, rows = job.result()
                        raw["sources"][key] = {"ok": True, "url": url, "checked_at": now.isoformat(),
                            "last_ok_at": now.isoformat(), "next_at": now.timestamp() + SOURCES[key]["seconds"],
                            "rows": retain_versions(rows, old.get("rows", []), now)}
                    except Exception as exc:
                        raw["sources"][key] = {**old, "ok": False, "checked_at": now.isoformat(),
                            "error": market_events._short_error(exc), "next_at": now.timestamp() + 900}
            if due:
                self.desk._save_json(self.path, raw)
        finally:
            self.lock.release()

    def snapshot(self, now=None):
        now = now or datetime.now(timezone.utc)
        raw = self.load()
        sources = []
        for key, spec in SOURCES.items():
            row = copy.deepcopy(raw["sources"].get(key, {}))
            try:
                age = (now - datetime.fromisoformat(row["last_ok_at"])).total_seconds()
                fresh = row.get("ok") is True and 0 <= age <= spec["seconds"] * 2
            except (KeyError, ValueError, TypeError):
                fresh = False
            all_rows = row.pop("rows", [])
            latest = {}
            for value in sorted(all_rows, key=lambda r: r["observation_date"]):
                latest[value.get("symbol") or value["series"]] = value
                if key != "halts":
                    value["observation_age_days"] = (now.astimezone(market_events.ET).date() - datetime.fromisoformat(value["observation_date"]).date()).days
            sources.append({**row, "id": key, "name": spec["name"], "owner": spec["owner"], "fresh": fresh,
                            "rows": list(latest.values()), "observations": len(all_rows)})
        return {"ok": True, "as_of": now.isoformat(), "sources": sources,
                "note": "Daily reference values are not executable quotes. Publication time stays unknown when absent. First-seen times and up to five locally observed revisions are retained; earlier vintages are not reconstructed."}


def register(app, desk, service):
    bp = Blueprint("public_context", __name__)
    @bp.get("/api/public-context")
    def snapshot():
        try:
            result = service.snapshot()
            cfg = desk.load_config()
            from fox_workspace import broker_view
            book = broker_view(desk, cfg)
            held = []
            if book.get("connected"):
                held = [r.get("ticker") for r in desk._BROKER_BOOK_CACHE["val"].get("positions", []) if r.get("ticker")]
            result["relevant_symbols"] = sorted(set(held + list(cfg.get("watchlist") or [])))
            import news_stream
            items = []
            for symbol in held[:30]:
                for row in news_stream.cached_evidence(symbol).get("items", [])[:3]:
                    items.append({"symbol": symbol, **{k: row.get(k) for k in ("title", "url", "published_at")}})
            result["held_context"] = {"items": items, "note":
                "Cached, symbol-matched headlines for verified broker positions. Missing headlines are unknown coverage." if held else
                "No broker positions in the current verified snapshot." if book.get("connected") else "Broker positions unverified; held-position matching is unavailable."}
            now = datetime.now(timezone.utc)
            result["session_brief"] = session_brief(now, market_events.events(cfg, now), items)
            return jsonify(result)
        except Exception:
            return jsonify(ok=False, error="Reference data needs recovery; retained data was not overwritten"), 503
    app.register_blueprint(bp)
