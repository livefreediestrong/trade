"""Versioned SQL migrations for the Signal Desk SQLite store.

Each entry is ``(version, sql)``. Migrations are applied in order inside a
single transaction per version and recorded in ``schema_migrations`` so
re-running the migrator is a safe no-op. Never edit an already-shipped
migration's SQL — add a new version instead, the same way a normal
production migration chain works.
"""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        -- Config: one row per top-level config key, JSON-encoded value.
        -- Mirrors config.json without hard-coding its (frequently extended) schema.
        CREATE TABLE IF NOT EXISTS config_kv (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        -- Signals: full-fidelity payload plus indexed columns used for filtering.
        -- `seq` preserves the original signals.json list order (newest-first)
        -- so export can reconstruct the exact list.
        CREATE TABLE IF NOT EXISTS signals (
            id          TEXT PRIMARY KEY,
            seq         INTEGER NOT NULL,
            ticker      TEXT,
            side        TEXT,
            status      TEXT,
            ts          TEXT,
            created_at  TEXT,
            expires_at  TEXT,
            payload     TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signals_seq ON signals(seq);
        CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
        CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);

        -- Decisions: append-only ring buffer (mirrors decisions.json {seq, events}).
        CREATE TABLE IF NOT EXISTS decisions (
            id          TEXT PRIMARY KEY,
            seq         INTEGER,
            ts          TEXT,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_seq ON decisions(seq);

        -- Singleton counter for the decisions ring (mirrors the ring's `seq`).
        CREATE TABLE IF NOT EXISTS decisions_meta (
            id   INTEGER PRIMARY KEY CHECK (id = 1),
            seq  INTEGER NOT NULL DEFAULT 0
        );

        -- Orders: broker/paper order intents. Nullable FK to signals so a
        -- signal can be pruned from signals.json without orphaning history.
        CREATE TABLE IF NOT EXISTS orders (
            id          TEXT PRIMARY KEY,
            signal_id   TEXT REFERENCES signals(id) ON DELETE SET NULL,
            ticker      TEXT,
            side        TEXT,
            status      TEXT,
            ts          TEXT,
            payload     TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orders_signal_id ON orders(signal_id);
        CREATE INDEX IF NOT EXISTS idx_orders_ticker ON orders(ticker);

        -- Fills: executions (paper or broker). Nullable FK to orders because
        -- legacy ledger.json fills predate a normalized orders table.
        CREATE TABLE IF NOT EXISTS fills (
            id          TEXT PRIMARY KEY,
            order_id    TEXT REFERENCES orders(id) ON DELETE SET NULL,
            seq         INTEGER NOT NULL,
            ticker      TEXT,
            side        TEXT,
            shares      REAL,
            price       REAL,
            ts          TEXT,
            source      TEXT,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(order_id);
        CREATE INDEX IF NOT EXISTS idx_fills_seq ON fills(seq);
        CREATE INDEX IF NOT EXISTS idx_fills_ticker ON fills(ticker);

        -- Positions: current open paper positions, keyed by ticker+side
        -- (a desk can be long and short different clips of the same name).
        CREATE TABLE IF NOT EXISTS positions (
            ticker      TEXT NOT NULL,
            side        TEXT NOT NULL,
            shares      REAL,
            avg_price   REAL,
            payload     TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (ticker, side)
        );

        -- Journal: append-only action log (mirrors journal.json).
        CREATE TABLE IF NOT EXISTS journal (
            id          TEXT PRIMARY KEY,
            seq         INTEGER NOT NULL,
            ts          TEXT,
            action      TEXT,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_journal_seq ON journal(seq);

        -- Market cache: provider snapshots (buzz cache, radar, quotes, etc).
        CREATE TABLE IF NOT EXISTS market_cache (
            key         TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            fetched_at  TEXT,
            expires_at  TEXT,
            updated_at  TEXT NOT NULL
        );

        -- Ledger meta: the parts of ledger.json that aren't positions/fills
        -- (equity, cash, daily stats, equity curve).
        CREATE TABLE IF NOT EXISTS ledger_meta (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            equity            REAL,
            cash              REAL,
            daily             TEXT,
            equity_curve      TEXT,
            equity_curve_day  TEXT,
            extra             TEXT,
            updated_at        TEXT NOT NULL
        );
        """,
    ),
]


def latest_version() -> int:
    return max(v for v, _ in MIGRATIONS)
