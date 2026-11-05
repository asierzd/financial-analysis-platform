"""SQLite: esquema, conexión y datos iniciales. Los importes se guardan en céntimos (enteros)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .seed import CATEGORIES, DEFAULT_SETTINGS, iter_system_rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    bank TEXT NOT NULL DEFAULT '',
    iban TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    parser TEXT NOT NULL,
    rows_total INTEGER NOT NULL,
    rows_new INTEGER NOT NULL,
    rows_skipped INTEGER NOT NULL,
    date_from TEXT,
    date_to TEXT,
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income','expense','transfer')),
    grp TEXT NOT NULL CHECK (grp IN ('income','needs','wants','savings','internal','unknown')),
    is_system INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,
    category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    sign TEXT NOT NULL DEFAULT 'any' CHECK (sign IN ('any','in','out')),
    priority INTEGER NOT NULL DEFAULT 50,
    source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('system','user')),
    label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE (pattern, sign, source)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    value_date TEXT,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    balance_cents INTEGER,
    concept TEXT NOT NULL DEFAULT '',
    movement TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    merchant TEXT NOT NULL DEFAULT '',
    category_id TEXT NOT NULL REFERENCES categories(id),
    category_source TEXT NOT NULL DEFAULT 'rule' CHECK (category_source IN ('rule','user','default')),
    rule_id INTEGER REFERENCES rules(id) ON DELETE SET NULL,
    user_note TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_tx_cat ON transactions(category_id);
CREATE INDEX IF NOT EXISTS ix_tx_merchant ON transactions(merchant);
CREATE TABLE IF NOT EXISTS budgets (
    category_id TEXT PRIMARY KEY REFERENCES categories(id) ON DELETE CASCADE,
    monthly_cents INTEGER NOT NULL CHECK (monthly_cents > 0)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical','warning','info')),
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    amount_cents INTEGER,
    tx_ids TEXT NOT NULL DEFAULT '[]',
    account_id INTEGER,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','reviewed','dismissed')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_alert_status ON alerts(status, active);
CREATE TABLE IF NOT EXISTS transfer_decisions (
    pair_key TEXT PRIMARY KEY,
    out_fingerprint TEXT NOT NULL,
    in_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed','rejected')),
    decided_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def db_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "finanzas.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Crea el esquema y actualiza categorías y reglas del sistema. No toca reglas propias, ajustes ni
    qué reglas del sistema ha desactivado el usuario."""
    conn.executescript(f"BEGIN;{SCHEMA}COMMIT;")
    conn.executemany(
        "INSERT INTO categories (id, name, kind, grp, is_system) VALUES (?,?,?,?,1) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, grp=excluded.grp",
        CATEGORIES,
    )
    conn.executemany(
        "INSERT INTO rules (pattern, category_id, sign, priority, source, label) VALUES (?,?,?,?,'system',?) "
        "ON CONFLICT(pattern, sign, source) DO UPDATE SET "
        "category_id=excluded.category_id, priority=excluded.priority, label=excluded.label",
        list(iter_system_rules()),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
        [(k, str(v)) for k, v in DEFAULT_SETTINGS.items()],
    )
    conn.commit()


def get_settings(conn: sqlite3.Connection) -> dict[str, float]:
    merged = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in merged:
            merged[row["key"]] = float(row["value"])
    return merged
