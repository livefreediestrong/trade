"""Bounded reading of official releases already discovered by authorized feeds."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import time

import requests
from companion_news import SOURCES, safe_link


class Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.capture, self.parts, self.rows = [], None, [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header", "form", "noscript", "svg"):
            self.skip.append(tag)
        if not self.skip and tag in ("p", "h1", "h2", "h3"):
            if self.parts:
                self.rows.append(" ".join(" ".join(self.parts).split()))
            self.capture, self.parts = tag, []

    def handle_endtag(self, tag):
        if self.skip and tag == self.skip[-1]:
            self.skip.pop()
        if tag == self.capture:
            text = " ".join(" ".join(self.parts).split())
            if text:
                self.rows.append(text)
            self.capture, self.parts = None, []

    def handle_data(self, data):
        if self.capture and not self.skip:
            self.parts.append(data)


def read_release(row):
    source = next((s for s in SOURCES if s["id"] == row.get("source_id") and s["kind"].startswith("Official")), None)
    link = safe_link(row.get("url", ""), source["domains"]) if source else None
    item = {"id": "document_"+row["id"], "label": row["title"], "url": link,
            "source": row.get("source"), "published_at": row.get("published_at"),
            "published_ts": row.get("published_ts"), "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "status": "unavailable", "text": None}
    if not link or not link.startswith("https://"):
        return dict(item, note="Release URL is not an approved official HTTPS source")
    try:
        # Redirects require a separate reviewed source, never an automatic hop to another host.
        with requests.get(link, timeout=(3, 10), stream=True, allow_redirects=False,
                          headers={"User-Agent": "NadzeelDesk/1.0 personal local research reader"}) as response:
            if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", "").lower():
                return dict(item, note=f"Release unavailable (HTTP {response.status_code} or unsupported format)")
            chunks, size, began = [], 0, time.monotonic()
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > 500_000 or time.monotonic()-began > 15:
                    raise ValueError("document_limit")
                chunks.append(chunk)
        parser = Paragraphs()
        parser.feed(b"".join(chunks).decode("utf-8", errors="replace"))
        text = "\n\n".join(dict.fromkeys(parser.rows))
        title_at = text.lower().find(" ".join(row["title"].split()).lower())
        if title_at >= 0:
            text = text[title_at:]
        if len(text) < 180 or any(x in text[:500].lower() for x in ("access denied", "verify you are human", "enable javascript")):
            raise ValueError("release_text_unavailable")
        return dict(item, status="read", text=text[:5000], truncated=len(text)>5000,
                    note="Automatically extracted official-page excerpt, up to 5,000 characters. Verify context at the linked source; no paywall or access controls bypassed.")
    except (requests.RequestException, ValueError):
        return dict(item, note="Release could not be read within the source or size/time limits")


def read_primary_releases(items, as_of):
    selected = [r for r in items if r.get("fresh") and str(r.get("kind", "")).startswith("Official")
                and 0 <= as_of-r.get("published_ts", 0) <= 36*3600][:4]
    return [read_release(row) for row in selected]
