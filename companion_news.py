"""Attributed public headlines for companion commentary, never execution inputs."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import requests

TTL = 300
MAX_AGE = 36 * 3600
MAX_BYTES = 1_000_000
# Ten complementary sources, not ten independent confirmations of a story.
SOURCES = (
    {"id":"cnbc", "name":"CNBC", "kind":"Reporting", "focus":"US markets and business", "group":"CNBC", "home":"https://www.cnbc.com/rss-feeds/", "feeds":["https://www.cnbc.com/id/100003114/device/rss/rss.html"], "domains":["cnbc.com"]},
    {"id":"marketwatch", "name":"MarketWatch", "kind":"Reporting / commentary", "focus":"Market context and investing", "group":"Dow Jones", "home":"https://www.marketwatch.com/rss/", "feeds":["https://feeds.content.dowjones.io/public/rss/mw_topstories"], "domains":["marketwatch.com"]},
    {"id":"wsj", "name":"The Wall Street Journal", "kind":"Reporting", "focus":"Markets and corporate developments", "group":"Dow Jones", "home":"https://www.wsj.com/news/markets", "feeds":["https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"], "domains":["wsj.com"]},
    {"id":"ft", "name":"Financial Times", "kind":"Reporting", "focus":"Global context for US markets", "group":"Financial Times", "home":"https://www.ft.com/", "feeds":["https://www.ft.com/rss/home/international"], "domains":["ft.com"]},
    {"id":"yahoo", "name":"Yahoo Finance", "kind":"Aggregation / reporting", "focus":"Broad company and market coverage", "group":"Yahoo Finance / syndicated", "home":"https://finance.yahoo.com/", "feeds":["https://finance.yahoo.com/news/rssindex"], "domains":["yahoo.com"]},
    {"id":"fed", "name":"Federal Reserve", "kind":"Official release", "focus":"Monetary policy and banking", "group":"Federal Reserve", "home":"https://www.federalreserve.gov/feeds/feeds.htm", "feeds":["https://www.federalreserve.gov/feeds/press_all.xml"], "domains":["federalreserve.gov"]},
    {"id":"sec", "name":"SEC", "kind":"Official release", "focus":"Securities regulation and enforcement", "group":"SEC", "home":"https://www.sec.gov/about/rss-feeds", "feeds":["https://www.sec.gov/news/pressreleases.rss"], "domains":["sec.gov"]},
    {"id":"bls", "name":"Bureau of Labor Statistics", "kind":"Official release", "focus":"Employment and consumer inflation", "group":"BLS", "home":"https://www.bls.gov/feed/", "feeds":["https://www.bls.gov/feed/empsit.rss","https://www.bls.gov/feed/cpi.rss"], "domains":["bls.gov"]},
    {"id":"eia", "name":"Energy Information Administration", "kind":"Official analysis", "focus":"Energy supply, demand and prices", "group":"EIA", "home":"https://www.eia.gov/tools/rssfeeds/", "feeds":["https://www.eia.gov/rss/todayinenergy.xml"], "domains":["eia.gov"]},
    {"id":"ftc", "name":"Federal Trade Commission", "kind":"Official release", "focus":"Competition and consumer protection", "group":"FTC", "home":"https://www.ftc.gov/stay-connected/rss", "feeds":["https://www.ftc.gov/feeds/press-release.xml"], "domains":["ftc.gov"]},
)


def safe_link(value: str, domains: list[str]) -> str | None:
    try:
        p = urlsplit(value.strip())
        if p.scheme not in ("http", "https") or p.username or p.password or p.port not in (None,80,443):
            return None
        if not any(p.hostname == d or (p.hostname or "").endswith("."+d) for d in domains):
            return None
        return urlunsplit((p.scheme,p.netloc,p.path,p.query,""))
    except (ValueError, AttributeError):
        return None


def published(value: str) -> float | None:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            stamp = parsedate_to_datetime(value)
        except (ValueError, TypeError, IndexError):
            return None
    if stamp.tzinfo is None:
        return None
    return stamp.timestamp()


def parse_feed(content: bytes, source: dict, now: float) -> tuple[list[dict], int]:
    if len(content) > MAX_BYTES or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("Unsupported or oversized feed")
    root = ET.fromstring(content)
    atom = "{http://www.w3.org/2005/Atom}"
    entries = root.findall(".//item") or root.findall(atom+"entry")
    if root.tag not in ("rss", atom+"feed", "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"):
        raise ValueError("Response is not a supported news feed")
    rows, rejected = [], 0
    for item in entries[:100]:
        title = item.findtext("title") or item.findtext(atom+"title") or ""
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip()[:280]
        link = item.findtext("link")
        if not link:
            link = next((n.get("href") for n in item.findall(atom+"link") if n.get("rel", "alternate") == "alternate"), "")
        link = safe_link(link or "", source["domains"])
        stamp = published(item.findtext("pubDate") or item.findtext(atom+"published") or item.findtext(atom+"updated") or "")
        if not title or not link or stamp is None or stamp > now:
            rejected += 1
            continue
        ident = hashlib.sha256(link.encode()).hexdigest()[:24]
        rows.append({"id":ident,"title":title,"url":link,"published_ts":stamp,
                     "published_at":datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
                     "source_id":source["id"],"source":source["name"],"kind":source["kind"],"group":source["group"]})
    return rows, rejected


def fetch_source(source: dict, now: float) -> dict:
    rows, errors, rejected = [], [], 0
    for url in source["feeds"]:
        try:
            # Fixed publisher endpoints only; no user-provided URL is fetched.
            with requests.get(url, timeout=(3,12), stream=True,
                              headers={"User-Agent":"NadzeelDesk/1.0 personal local RSS reader"}) as r:
                r.raise_for_status()
                chunks, total, began = [], 0, time.monotonic()
                for chunk in r.iter_content(16384):
                    total += len(chunk)
                    if total > MAX_BYTES or time.monotonic()-began > 15:
                        raise ValueError("Feed size or duration limit reached")
                    chunks.append(chunk)
            items, excluded = parse_feed(b"".join(chunks), source, now)
            rows.extend(items); rejected += excluded
        except requests.HTTPError as exc:
            errors.append("HTTP "+str(exc.response.status_code))
        except requests.RequestException:
            errors.append("Connection unavailable")
        except (ET.ParseError, ValueError):
            errors.append("Invalid or unsupported feed")
    return {"items":rows,"checked_at":now,"status":"partial" if rows and errors else "unavailable" if errors else "connected",
            "error":"; ".join(errors) or None,"rejected":rejected}


class HeadlineDesk:
    def __init__(self, desk):
        self.desk = desk
        self.lock = threading.RLock()
        self.busy = False
        self.last_attempt = 0.0
        # This disposable display cache is not trading state. A bad cache must
        # never mark account/ledger state corrupt or change trading eligibility.
        try:
            raw = json.loads((desk.DATA_DIR/"companion_news.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}
        self.cache = {k:v for k,v in raw.items() if isinstance(v,dict) and isinstance(v.get("items"),list) and all(isinstance(r,dict) for r in v["items"])} if isinstance(raw,dict) else {}
        self.error = None

    def refresh(self) -> bool:
        with self.lock:
            if self.busy or (self.last_attempt and time.monotonic()-self.last_attempt < TTL):
                return False
            self.busy = True
            self.last_attempt = time.monotonic()
        threading.Thread(target=self._work, name="companion-headlines", daemon=True).start()
        return True

    def _work(self):
        try:
            now = time.time()
            with ThreadPoolExecutor(max_workers=4,thread_name_prefix="headline-feed") as pool:
                results = list(pool.map(lambda s: fetch_source(s,now), SOURCES))
            with self.lock:
                for source,result in zip(SOURCES,results):
                    # A failed refresh never passes older cached articles off as newly fetched.
                    if result["status"] == "unavailable":
                        result["items"] = self.cache.get(source["id"],{}).get("items", [])
                    self.cache[source["id"]] = result
                self.desk._save_json(self.desk.DATA_DIR/"companion_news.json", self.cache)
                self.error = None
        except Exception:
            self.error = "Headline refresh unavailable; check source status."
        finally:
            with self.lock:
                self.busy = False

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            cache = copy.deepcopy(self.cache)
            busy, error = self.busy, self.error
        providers, selected = [], {}
        for source in SOURCES:
            result = cache.get(source["id"],{})
            checked = result.get("checked_at")
            current = isinstance(checked,(int,float)) and 0 <= now-checked <= 900
            eligible = []
            for row in result.get("items",[]):
                ts = row.get("published_ts")
                if not isinstance(ts,(int,float)) or not 0 <= now-ts <= MAX_AGE:
                    continue
                if not safe_link(row.get("url",""),source["domains"]):
                    continue
                fresh = current and result.get("status") in ("connected","partial")
                eligible.append(dict(row,fresh=fresh,checked_at=checked))
            providers.append({k:source[k] for k in ("id","name","kind","focus","group","home")}|{
                "status":result.get("status","waiting") if current or not checked else "stale",
                "error":result.get("error"),"checked_at":checked,"recent":len(eligible),
                "latest_published_at":max((r.get("published_at","") for r in result.get("items",[])),default=None),
                "rejected":result.get("rejected",0)})
            for row in eligible:
                key = re.sub(r"\W+","",row["title"].lower())
                prior = selected.get(key)
                if prior is None or (row["fresh"], row["published_ts"]) > (prior["fresh"], prior["published_ts"]):
                    selected[key] = row
        rows = list(selected.values())
        rows.sort(key=lambda r:r["published_ts"],reverse=True)
        return {"ok":True,"busy":busy,"error":error,"sources":providers,"items":rows[:80],"as_of":now,
                "refresh_seconds":TTL,"headline_max_age_hours":36,
                "note":"Publisher-feed headlines only. Article access may require a subscription. Publication time is not retrieval time. Shared ownership and syndication are not independent confirmation. Commentary never routes an order."}
