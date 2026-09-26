"""Paced US-directory research scan. Durable checkpoints; no broker dependencies."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Callable

import requests

SCOPE = "US exchange-listed stocks and ETFs in the Nasdaq-traded directory. Excludes OTC, preferred shares, warrants, rights, units and debt securities. Provider history coverage is separate."


class SaleScanner:
    def __init__(self, path: Path, directory: Callable, fetch: Callable, session: Callable,
                 clock: Callable = lambda: datetime.now(timezone.utc), pace: float = 1.0):
        self.path, self.directory, self.fetch, self.session = Path(path), directory, fetch, session
        self.clock, self.pace = clock, max(0, pace)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scan_meta (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scan_rows (
                    symbol TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, name TEXT NOT NULL, etf INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', payload TEXT, error TEXT,
                    discount REAL, liquidity REAL, attempts INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS scan_rank ON scan_rows(state,discount DESC);
            """)
            meta = self._meta(db)
            if meta.get("state") in ("running", "initializing", "pausing"):
                meta.update(state="paused", message="App restarted. Resume to continue the saved scan.")
                self._save(db, meta)

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA journal_mode=WAL")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _meta(db):
        row = db.execute("SELECT payload FROM scan_meta WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"state": "idle", "message": "Ready to scan the US directory."}

    @staticmethod
    def _save(db, meta):
        db.execute("INSERT OR REPLACE INTO scan_meta VALUES(1,?)", (json.dumps(meta, allow_nan=False),))

    def _update(self, **values):
        with self.db() as db:
            meta = self._meta(db)
            meta.update(values)
            self._save(db, meta)

    def start(self, resume: bool = False):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return self.snapshot()
            now = self.clock()
            target = self.session(now).isoformat()
            with self.db() as db:
                meta = self._meta(db)
                if resume and (meta.get("as_of") != target or not db.execute("SELECT 1 FROM scan_rows LIMIT 1").fetchone()):
                    raise ValueError("This scan belongs to an older session or has no directory. Start a new scan.")
                retry = meta.get("retry_after")
                if retry and now.timestamp() < retry:
                    raise ValueError("Provider cooling down. Retry after " + datetime.fromtimestamp(retry, timezone.utc).isoformat())
                if resume:
                    db.execute("UPDATE scan_rows SET state='pending',error=NULL WHERE state='provider_error'")
                    meta.update(state="running", message="Resuming saved coverage.", retry_after=None)
                else:
                    # Keep the previous results until the new directory is obtained successfully.
                    meta.update(state="initializing", message="Refreshing the US listing directory.", retry_after=None)
                self._save(db, meta)
            self.stop.clear()
            try:
                self.worker = threading.Thread(target=self._run, args=(target, not resume), daemon=True, name="sale-research")
                self.worker.start()
            except Exception:
                self._update(state="error", message="Could not start the scanner worker. Previous results retained; try again.")
            return self.snapshot()

    def pause(self):
        with self.lock:
            self.stop.set()
            if self.worker and self.worker.is_alive():
                self._update(state="pausing", message="Pausing after the current data request.")
            return self.snapshot()

    def _run(self, target: str, initialize: bool):
        try:
            if initialize:
                directory = self.directory()
                rows = directory.get("symbols") or []
                if not rows:
                    raise ValueError(directory.get("error") or "US directory unavailable; previous results retained.")
                with self.db() as db:
                    db.execute("DELETE FROM scan_rows")
                    db.executemany("INSERT INTO scan_rows(symbol,ordinal,name,etf) VALUES(?,?,?,?)",
                                   [(r["symbol"].replace(".", "-"), i, r["name"], int(r["etf"])) for i, r in enumerate(rows)])
                    self._save(db, {"state": "running", "as_of": target, "started_at": self.clock().isoformat(),
                                    "directory_at": directory.get("updated_at"), "directory_error": directory.get("error"),
                                    "message": "Checking adjusted daily history across the US directory.", "retry_after": None})
            failures = 0
            while not self.stop.is_set():
                now = self.clock()
                if self.session(now).isoformat() != target:
                    self._update(state="stale", message="A newer session has closed. Start a new scan; retained results are dated.")
                    return
                with self.db() as db:
                    item = db.execute("SELECT * FROM scan_rows WHERE state='pending' ORDER BY ordinal LIMIT 1").fetchone()
                if item is None:
                    with self.db() as db:
                        failed = db.execute("SELECT count(*) FROM scan_rows WHERE state='provider_error'").fetchone()[0]
                    self._update(state="partial" if failed else "complete", current_symbol=None,
                                 message="Pass finished with provider gaps. Resume to retry them." if failed else "Directory pass finished. See valid-history coverage and exclusions.")
                    return
                symbol = item["symbol"]
                self._update(current_symbol=symbol)
                row, error, status, code = None, None, "valid", None
                try:
                    row = self.fetch(symbol, now)
                    if row["symbol"] != symbol or row["as_of"] != target:
                        raise ValueError("History identity/session does not match the scan")
                    # Ensure malformed numeric output cannot enter the ranking.
                    json.dumps(row, allow_nan=False)
                    failures = 0
                except ValueError as exc:
                    from research_studio import RetryableHistoryError
                    if isinstance(exc, RetryableHistoryError):
                        status, error, failures = "provider_error", str(exc)[:240], failures+1
                    else:
                        status, error, failures = "excluded", str(exc)[:240], 0
                except requests.HTTPError as exc:
                    code = exc.response.status_code if exc.response is not None else None
                    status = "excluded" if code in (404, 410) else "provider_error"
                    error = "History unavailable for this listing" if status == "excluded" else f"Market provider returned HTTP {code or 'error'}"
                    failures = failures+1 if status == "provider_error" else 0
                except Exception:
                    status, error, failures = "provider_error", "Market provider unavailable; history not evaluated", failures+1
                with self.db() as db:
                    db.execute("UPDATE scan_rows SET state=?,payload=?,error=?,discount=?,liquidity=?,attempts=attempts+1 WHERE symbol=?",
                               (status, json.dumps(row, allow_nan=False) if status == "valid" else None, error,
                                row["below_typical_pct"] if status == "valid" else None,
                                row.get("avg_daily_dollar_volume") if status == "valid" else None, symbol))
                self._update(updated_at=self.clock().isoformat())
                if code in (401, 403, 429) or failures >= 3:
                    self._update(state="paused", message="Provider limit or repeated outage. Saved progress; resume after cooldown.",
                                 retry_after=self.clock().timestamp()+300, current_symbol=None)
                    return
                if self.stop.wait(self.pace):
                    break
            self._update(state="paused", message="Scan paused. Progress saved.", current_symbol=None)
        except Exception as exc:
            self._update(state="paused", message=str(exc)[:240] if isinstance(exc, ValueError) else "Scan interrupted; saved progress retained.", current_symbol=None)

    def snapshot(self, *, page: int = 1, kind: str = "all", query: str = "", minimum: float = 0,
                 liquid: bool = False, issues: bool = False, lens: str = "pullbacks"):
        if page < 1 or page > 10000 or kind not in ("all", "stock", "etf") or lens not in ("pullbacks", "momentum", "active", "all") or not 0 <= minimum <= 100 or len(query) > 80:
            raise ValueError("Invalid scan filter")
        size, args = 40, []
        where = "state IN ('excluded','provider_error')" if issues else "state='valid'"
        order = "discount DESC,symbol"
        if not issues and lens == "pullbacks":
            where += " AND discount>0 AND discount>=?"
            args.append(minimum)
        elif not issues and lens == "momentum":
            where += " AND json_extract(payload,'$.return_20_sessions_pct')>0"
            order = "json_extract(payload,'$.return_20_sessions_pct') DESC,symbol"
        elif not issues and lens == "active":
            order = "json_extract(payload,'$.completed_session_relative_volume') DESC,symbol"
        elif not issues:
            order = "liquidity DESC,symbol"
        if kind != "all":
            where += " AND etf=?"
            args.append(int(kind == "etf"))
        if liquid and not issues:
            where += " AND liquidity>=10000000"
        if query.strip():
            # Literal search; wildcard characters do not broaden the request.
            where += " AND (instr(lower(symbol),?)>0 OR instr(lower(name),?)>0)"
            args += [query.strip().lower()]*2
        with self.db() as db:
            meta = self._meta(db)
            counts = {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM scan_rows GROUP BY state")}
            total = sum(counts.values())
            matched = db.execute("SELECT count(*) FROM scan_rows WHERE "+where, args).fetchone()[0]
            page = min(page, max(1, (matched+size-1)//size))
            rows = db.execute("SELECT * FROM scan_rows WHERE "+where+" ORDER BY "+order+" LIMIT ? OFFSET ?", args+[size, (page-1)*size]).fetchall()
        results = [{**json.loads(r["payload"]), "listing_name": r["name"], "etf": bool(r["etf"])} if not issues else
                   {"symbol": r["symbol"], "name": r["name"], "state": r["state"], "error": r["error"]} for r in rows]
        checked = total-counts.get("pending", 0)
        current = self.session(self.clock()).isoformat()
        return {**meta, "scope": SCOPE, "latest_completed_session": current, "stale": bool(meta.get("as_of") and meta["as_of"] != current),
                "total": total, "checked": checked, "valid": counts.get("valid", 0), "excluded": counts.get("excluded", 0),
                "failed": counts.get("provider_error", 0), "pending": counts.get("pending", 0),
                "coverage_pct": round(100*counts.get("valid", 0)/total, 2) if total else 0,
                "results": results, "matched": matched, "page": page, "page_size": size, "lens": lens,
                "note": "Ranked by distance below the adjusted 60-session median. Partial coverage is not a market-wide ranking. A lower price is not verified fair value or a buy signal. Daily history is not a live quote."}
