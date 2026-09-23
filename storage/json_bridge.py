"""Bridge between the existing JSON files and the SQLite store.

Two directions:

* ``import_all_from_json`` reads the current ``data/*.json`` files (using the
  same tolerant/fail-closed rules ``app.py`` uses — a BOM is fine, a corrupt
  file is skipped rather than raising) and populates the SQLite tables. This
  is the "migration tooling" entry point: it never modifies or deletes the
  JSON files, so it can be re-run any number of times as a safe, idempotent
  resync.
* ``export_all_to_json`` writes the SQLite contents back out as the same
  JSON file shapes ``app.py`` expects, using an atomic temp-file-then-replace
  write so a crash mid-export can't corrupt a JSON file.
* ``verify_parity`` diffs JSON vs SQLite per domain without writing anything,
  for a "prove legacy data remains safe" pre-cutover check.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .repositories import (
    ConfigRepository,
    DecisionsRepository,
    FillsRepository,
    JournalRepository,
    LedgerMetaRepository,
    MarketCacheRepository,
    PositionsRepository,
    SignalsRepository,
)

_DEFAULT_LEDGER: dict[str, Any] = {
    "equity": 100_000.0,
    "cash": 100_000.0,
    "positions": [],
    "fills": [],
    "daily": {},
    "equity_curve": [],
    "equity_curve_day": None,
}


def _read_json(path: Path, default: Any) -> Any:
    """Tolerant read mirroring app.py's `_load_json`: BOM-safe, never raises."""
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{threading.get_ident()}.import_export.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, allow_nan=False)
    tmp.replace(path)


def import_all_from_json(conn: sqlite3.Connection, data_dir: Path) -> dict[str, int]:
    """Import every known JSON file into SQLite. Returns per-domain row counts.

    Purely additive/idempotent: JSON files are only read, never written or
    deleted. Missing files are treated as empty, matching each domain's
    existing "no file yet" default so a first-time import on a fresh data
    dir is harmless.
    """
    data_dir = Path(data_dir)
    counts: dict[str, int] = {}

    config = _read_json(data_dir / "config.json", {})
    if isinstance(config, dict):
        ConfigRepository(conn).replace_all(config)
        counts["config"] = len(config)

    signals = _read_json(data_dir / "signals.json", [])
    if isinstance(signals, list):
        clean = [s for s in signals if isinstance(s, dict)]
        SignalsRepository(conn).replace_all(clean)
        counts["signals"] = len(clean)

    ledger = _read_json(data_dir / "ledger.json", dict(_DEFAULT_LEDGER))
    if isinstance(ledger, dict):
        LedgerMetaRepository(conn).replace(ledger)
        positions = [p for p in (ledger.get("positions") or []) if isinstance(p, dict)]
        fills = [f for f in (ledger.get("fills") or []) if isinstance(f, dict)]
        PositionsRepository(conn).replace_all(positions)
        FillsRepository(conn).replace_all(fills)
        counts["positions"] = len(positions)
        counts["fills"] = len(fills)

    journal = _read_json(data_dir / "journal.json", [])
    if isinstance(journal, list):
        clean = [e for e in journal if isinstance(e, dict)]
        JournalRepository(conn).replace_all(clean)
        counts["journal"] = len(clean)

    decisions_raw = _read_json(data_dir / "decisions.json", {"seq": 0, "events": []})
    if isinstance(decisions_raw, dict):
        events = [e for e in (decisions_raw.get("events") or []) if isinstance(e, dict)]
        seq = int(decisions_raw.get("seq") or 0)
    elif isinstance(decisions_raw, list):
        events = [e for e in decisions_raw if isinstance(e, dict)]
        seq = max((int(e.get("seq") or 0) for e in events), default=0)
    else:
        events, seq = [], 0
    DecisionsRepository(conn).replace_all(seq, events)
    counts["decisions"] = len(events)

    buzz_cache = _read_json(data_dir / "buzz_cache.json", None)
    if isinstance(buzz_cache, dict):
        MarketCacheRepository(conn).set("buzz", buzz_cache)
        counts["market_cache"] = 1
    else:
        counts["market_cache"] = 0

    return counts


def export_all_to_json(conn: sqlite3.Connection, data_dir: Path) -> list[str]:
    """Write SQLite contents back out as the JSON files app.py reads.

    Returns the list of file names written. Existing files are overwritten
    atomically (temp file + replace), same as app.py's own `_save_json`.
    """
    data_dir = Path(data_dir)
    written: list[str] = []

    config = ConfigRepository(conn).as_dict()
    _write_json_atomic(data_dir / "config.json", config)
    written.append("config.json")

    signals = SignalsRepository(conn).as_list()
    _write_json_atomic(data_dir / "signals.json", signals)
    written.append("signals.json")

    ledger = LedgerMetaRepository(conn).as_dict() or dict(_DEFAULT_LEDGER)
    ledger["positions"] = PositionsRepository(conn).as_list()
    ledger["fills"] = FillsRepository(conn).as_list()
    _write_json_atomic(data_dir / "ledger.json", ledger)
    written.append("ledger.json")

    journal = JournalRepository(conn).as_list()
    _write_json_atomic(data_dir / "journal.json", journal)
    written.append("journal.json")

    seq, events = DecisionsRepository(conn).as_ring()
    _write_json_atomic(data_dir / "decisions.json", {"seq": seq, "events": events})
    written.append("decisions.json")

    buzz = MarketCacheRepository(conn).get("buzz")
    if buzz is not None:
        _write_json_atomic(data_dir / "buzz_cache.json", buzz)
        written.append("buzz_cache.json")

    return written


def verify_parity(conn: sqlite3.Connection, data_dir: Path) -> dict[str, dict[str, Any]]:
    """Compare JSON files against SQLite contents without writing anything.

    Returns ``{domain: {"match": bool, "json_count": int, "db_count": int}}``
    — a lightweight pre-cutover confidence check ("dual-read"): run this
    after a period in ``dual`` mode to prove SQLite has faithfully mirrored
    every JSON write before ever flipping reads over.
    """
    data_dir = Path(data_dir)
    result: dict[str, dict[str, Any]] = {}

    config = _read_json(data_dir / "config.json", {})
    db_config = ConfigRepository(conn).as_dict()
    result["config"] = {
        "match": config == db_config,
        "json_count": len(config) if isinstance(config, dict) else 0,
        "db_count": len(db_config),
    }

    signals = _read_json(data_dir / "signals.json", [])
    db_signals = SignalsRepository(conn).as_list()
    result["signals"] = {
        "match": signals == db_signals,
        "json_count": len(signals) if isinstance(signals, list) else 0,
        "db_count": len(db_signals),
    }

    ledger = _read_json(data_dir / "ledger.json", dict(_DEFAULT_LEDGER))
    db_positions = PositionsRepository(conn).as_list()
    db_fills = FillsRepository(conn).as_list()
    json_positions = ledger.get("positions") or [] if isinstance(ledger, dict) else []
    json_fills = ledger.get("fills") or [] if isinstance(ledger, dict) else []
    result["positions"] = {
        "match": json_positions == db_positions,
        "json_count": len(json_positions),
        "db_count": len(db_positions),
    }
    result["fills"] = {
        "match": json_fills == db_fills,
        "json_count": len(json_fills),
        "db_count": len(db_fills),
    }

    journal = _read_json(data_dir / "journal.json", [])
    db_journal = JournalRepository(conn).as_list()
    result["journal"] = {
        "match": journal == db_journal,
        "json_count": len(journal) if isinstance(journal, list) else 0,
        "db_count": len(db_journal),
    }

    decisions_raw = _read_json(data_dir / "decisions.json", {"seq": 0, "events": []})
    json_events = decisions_raw.get("events") or [] if isinstance(decisions_raw, dict) else []
    _, db_events = DecisionsRepository(conn).as_ring()
    result["decisions"] = {
        "match": json_events == db_events,
        "json_count": len(json_events),
        "db_count": len(db_events),
    }

    return result
