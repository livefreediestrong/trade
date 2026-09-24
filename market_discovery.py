"""Read-only shared candidate context. Daily observations never stand in for live quotes."""
from datetime import datetime, timezone
import json
import sqlite3


def candidates(desk, now=None, limit=40):
    """Liquid observations from this completed session; no fetch, orders or config writes."""
    from research_studio import completed_day
    now = now or datetime.now(timezone.utc)
    path = desk.DATA_DIR / "sale_scan.sqlite3"
    if not path.exists():
        return []
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)
        meta_row = db.execute("SELECT payload FROM scan_meta WHERE id=1").fetchone()
        if not meta_row or json.loads(meta_row[0]).get("as_of") != completed_day(now).isoformat():
            return []
        # Mixed daily momentum/volume attention, not a price-drop-only bias.
        records = db.execute("""SELECT payload FROM scan_rows WHERE state='valid' AND liquidity>=10000000
            ORDER BY json_extract(payload,'$.completed_session_relative_volume') DESC, liquidity DESC, symbol LIMIT ?""",
                             (max(1, min(200, int(limit))),)).fetchall()
        rows = []
        for (payload,) in records:
            row = json.loads(payload)
            stamp = datetime.fromisoformat(row["retrieved_at"])
            if stamp.tzinfo is None or stamp > now or row["as_of"] != completed_day(now).isoformat():
                continue
            rows.append(row)
        return rows
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return []  # Missing/corrupt research context cannot broaden any trading gate.
    finally:
        if db is not None:
            db.close()


def symbols(desk, now=None, limit=40):
    return [row["symbol"] for row in candidates(desk, now, limit)]
