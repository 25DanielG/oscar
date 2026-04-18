from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crn_state (
    crn             TEXT NOT NULL,
    term            TEXT NOT NULL,
    seats_available INTEGER NOT NULL DEFAULT 0,
    wait_available  INTEGER NOT NULL DEFAULT 0,
    open_section    INTEGER NOT NULL DEFAULT 0,
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (crn, term)
);

CREATE TABLE IF NOT EXISTS poll_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    crn             TEXT NOT NULL,
    term            TEXT NOT NULL,
    polled_at       TEXT NOT NULL,
    seats_available INTEGER NOT NULL,
    wait_available  INTEGER NOT NULL,
    open_section    INTEGER NOT NULL,
    state_changed   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS registration_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    crn             TEXT NOT NULL,
    term            TEXT NOT NULL,
    attempted_at    TEXT NOT NULL,
    action          TEXT NOT NULL,
    dry_run         INTEGER NOT NULL DEFAULT 0,
    success         INTEGER,
    error_flag      TEXT,
    error_message   TEXT,
    response_json   TEXT
);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = get_connection(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
