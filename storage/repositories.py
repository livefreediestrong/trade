"""Repository layer: one small class per domain table.

Repositories are intentionally thin — they know how to turn a Python dict
(the same shape already used in the JSON files) into rows and back. They do
not implement business logic (risk gates, fills math, etc.); that stays in
``app.py`` / ``paper_loop.py`` / etc. This keeps the SQLite layer a pure
persistence concern that call sites can adopt incrementally.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, allow_nan=False)


def _load(text: str) -> Any:
    return json.loads(text)


class ConfigRepository:
    """Mirrors config.json as one row per top-level key."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_all(self, config: dict[str, Any]) -> None:
        now = _now_iso()
        with self.conn:
            self.conn.execute("DELETE FROM config_kv")
            self.conn.executemany(
                "INSERT INTO config_kv (key, value, updated_at) VALUES (?, ?, ?)",
                [(k, _dump(v), now) for k, v in config.items()],
            )

    def as_dict(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, value FROM config_kv").fetchall()
        return {row["key"]: _load(row["value"]) for row in rows}


class SignalsRepository:
    """Mirrors signals.json (a newest-first list of signal dicts)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_all(self, signals: list[dict[str, Any]]) -> None:
        now = _now_iso()
        rows = []
        for idx, sig in enumerate(signals):
            if not isinstance(sig, dict):
                continue
            sig_id = str(sig.get("id") or idx)
            rows.append(
                (
                    sig_id,
                    idx,
                    sig.get("ticker"),
                    sig.get("side"),
                    sig.get("status"),
                    sig.get("ts"),
                    sig.get("created_at"),
                    sig.get("expires_at"),
                    _dump(sig),
                    now,
                )
            )
        with self.conn:
            self.conn.execute("DELETE FROM signals")
            self.conn.executemany(
                "INSERT INTO signals "
                "(id, seq, ticker, side, status, ts, created_at, expires_at, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def as_list(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT payload FROM signals ORDER BY seq ASC").fetchall()
        return [_load(row["payload"]) for row in rows]


class DecisionsRepository:
    """Mirrors decisions.json's ``{"seq": int, "events": [...]}`` ring buffer."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_all(self, seq: int, events: list[dict[str, Any]]) -> None:
        now = _now_iso()
        rows = [
            (
                str(ev.get("id") or f"seq-{ev.get('seq', idx)}"),
                ev.get("seq"),
                ev.get("ts"),
                _dump(ev),
                now,
            )
            for idx, ev in enumerate(events)
            if isinstance(ev, dict)
        ]
        with self.conn:
            self.conn.execute("DELETE FROM decisions")
            self.conn.executemany(
                "INSERT INTO decisions (id, seq, ts, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self.conn.execute(
                "INSERT INTO decisions_meta (id, seq) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET seq=excluded.seq",
                (seq,),
            )

    def append(self, event: dict[str, Any]) -> None:
        now = _now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO decisions (id, seq, ts, payload, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET seq=excluded.seq, ts=excluded.ts, payload=excluded.payload",
                (str(event.get("id")), event.get("seq"), event.get("ts"), _dump(event), now),
            )
            self.conn.execute(
                "INSERT INTO decisions_meta (id, seq) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET seq=MAX(decisions_meta.seq, excluded.seq)",
                (event.get("seq") or 0,),
            )

    def as_ring(self) -> tuple[int, list[dict[str, Any]]]:
        seq_row = self.conn.execute("SELECT seq FROM decisions_meta WHERE id = 1").fetchone()
        seq = int(seq_row["seq"]) if seq_row else 0
        rows = self.conn.execute(
            "SELECT payload FROM decisions ORDER BY seq DESC"
        ).fetchall()
        return seq, [_load(row["payload"]) for row in rows]


class OrdersRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, order: dict[str, Any]) -> None:
        now = _now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO orders (id, signal_id, ticker, side, status, ts, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET signal_id=excluded.signal_id, ticker=excluded.ticker, "
                "side=excluded.side, status=excluded.status, ts=excluded.ts, payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (
                    str(order.get("id")),
                    order.get("signal_id"),
                    order.get("ticker"),
                    order.get("side"),
                    order.get("status"),
                    order.get("ts"),
                    _dump(order),
                    now,
                ),
            )

    def as_list(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT payload FROM orders ORDER BY rowid ASC").fetchall()
        return [_load(row["payload"]) for row in rows]


class FillsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_all(self, fills: Iterable[dict[str, Any]]) -> None:
        now = _now_iso()
        rows = []
        for idx, fill in enumerate(fills):
            if not isinstance(fill, dict):
                continue
            rows.append(
                (
                    str(fill.get("id") or f"fill-{idx}"),
                    fill.get("order_id"),
                    idx,
                    fill.get("ticker"),
                    fill.get("side"),
                    fill.get("shares"),
                    fill.get("price"),
                    fill.get("ts"),
                    fill.get("source"),
                    _dump(fill),
                    now,
                )
            )
        with self.conn:
            self.conn.execute("DELETE FROM fills")
            self.conn.executemany(
                "INSERT INTO fills "
                "(id, order_id, seq, ticker, side, shares, price, ts, source, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def as_list(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT payload FROM fills ORDER BY seq ASC").fetchall()
        return [_load(row["payload"]) for row in rows]


class PositionsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_all(self, positions: Iterable[dict[str, Any]]) -> None:
        now = _now_iso()
        rows = []
        for pos in positions:
            if not isinstance(pos, dict) or not pos.get("ticker"):
                continue
            rows.append(
                (
                    pos["ticker"],
                    (pos.get("side") or "long"),
                    pos.get("shares"),
                    pos.get("avg_price"),
                    _dump(pos),
                    now,
                )
            )
        with self.conn:
            self.conn.execute("DELETE FROM positions")
            self.conn.executemany(
                "INSERT INTO positions (ticker, side, shares, avg_price, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def as_list(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload FROM positions ORDER BY ticker ASC, side ASC"
        ).fetchall()
        return [_load(row["payload"]) for row in rows]


class JournalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace_all(self, journal: list[dict[str, Any]]) -> None:
        now = _now_iso()
        rows = []
        for idx, entry in enumerate(journal):
            if not isinstance(entry, dict):
                continue
            rows.append(
                (
                    str(entry.get("id") or idx),
                    idx,
                    entry.get("ts"),
                    entry.get("action"),
                    _dump(entry),
                    now,
                )
            )
        with self.conn:
            self.conn.execute("DELETE FROM journal")
            self.conn.executemany(
                "INSERT INTO journal (id, seq, ts, action, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def as_list(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT payload FROM journal ORDER BY seq ASC").fetchall()
        return [_load(row["payload"]) for row in rows]


class MarketCacheRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def set(self, key: str, payload: Any, *, fetched_at: str | None = None, expires_at: str | None = None) -> None:
        now = _now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO market_cache (key, payload, fetched_at, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at, "
                "expires_at=excluded.expires_at, updated_at=excluded.updated_at",
                (key, _dump(payload), fetched_at, expires_at, now),
            )

    def get(self, key: str) -> Any | None:
        row = self.conn.execute("SELECT payload FROM market_cache WHERE key = ?", (key,)).fetchone()
        return _load(row["payload"]) if row else None

    def as_dict(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, payload FROM market_cache").fetchall()
        return {row["key"]: _load(row["payload"]) for row in rows}


class LedgerMetaRepository:
    """The parts of ledger.json that aren't positions/fills (equity, cash, ...)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def replace(self, ledger: dict[str, Any]) -> None:
        now = _now_iso()
        known = {"equity", "cash", "positions", "fills", "daily", "equity_curve", "equity_curve_day"}
        extra = {k: v for k, v in ledger.items() if k not in known}
        with self.conn:
            self.conn.execute(
                "INSERT INTO ledger_meta (id, equity, cash, daily, equity_curve, equity_curve_day, extra, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET equity=excluded.equity, cash=excluded.cash, "
                "daily=excluded.daily, equity_curve=excluded.equity_curve, "
                "equity_curve_day=excluded.equity_curve_day, extra=excluded.extra, updated_at=excluded.updated_at",
                (
                    ledger.get("equity"),
                    ledger.get("cash"),
                    _dump(ledger.get("daily") or {}),
                    _dump(ledger.get("equity_curve") or []),
                    ledger.get("equity_curve_day"),
                    _dump(extra),
                    now,
                ),
            )

    def as_dict(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT equity, cash, daily, equity_curve, equity_curve_day, extra FROM ledger_meta WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        out: dict[str, Any] = {
            "equity": row["equity"],
            "cash": row["cash"],
            "daily": _load(row["daily"]) if row["daily"] else {},
            "equity_curve": _load(row["equity_curve"]) if row["equity_curve"] else [],
            "equity_curve_day": row["equity_curve_day"],
        }
        if row["extra"]:
            out.update(_load(row["extra"]))
        return out
