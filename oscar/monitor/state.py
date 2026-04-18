from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from oscar.client.models import ClassAvailability
from oscar.db import init_db

class RegistrationAction(Enum):
    REGISTER = "RW" # open seat
    WAITLIST = "WL" # join waitlist

@dataclass(frozen=True)
class CRNState:
    crn: str
    term: str
    seats_available: int
    wait_available: int
    open_section: bool
    last_seen: str

def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = init_db(db_path)
    return conn

def get_state(conn: sqlite3.Connection, crn: str, term: str) -> CRNState | None:
    row = conn.execute(
        "SELECT crn, term, seats_available, wait_available, open_section, last_seen "
        "FROM crn_state WHERE crn = ? AND term = ?",
        (crn, term),
    ).fetchone()
    if row is None:
        return None
    return CRNState(
        crn=row["crn"],
        term=row["term"],
        seats_available=row["seats_available"],
        wait_available=row["wait_available"],
        open_section=bool(row["open_section"]),
        last_seen=row["last_seen"],
    )

def upsert_state(conn: sqlite3.Connection, avail: ClassAvailability) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO crn_state (crn, term, seats_available, wait_available, open_section, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(crn, term) DO UPDATE SET
            seats_available = excluded.seats_available,
            wait_available  = excluded.wait_available,
            open_section    = excluded.open_section,
            last_seen       = excluded.last_seen
        """,
        (
            avail.crn,
            avail.term,
            avail.seats_available,
            avail.wait_available,
            int(avail.open_section),
            now,
        ),
    )
    conn.commit()

def log_poll(
    conn: sqlite3.Connection,
    avail: ClassAvailability,
    state_changed: bool,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO poll_log
            (crn, term, polled_at, seats_available, wait_available, open_section, state_changed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            avail.crn,
            avail.term,
            now,
            avail.seats_available,
            avail.wait_available,
            int(avail.open_section),
            int(state_changed),
        ),
    )
    conn.commit()

def detect_transition(
    prev: CRNState | None,
    curr: ClassAvailability,
) -> RegistrationAction | None:
    """Return the action if a registration opportunity appeared, else None.

    Triggers:
      - First poll and section already has a seat/waitlist spot
      - Previous state was fully closed and a seat/waitlist spot opened
      - Previous state had waitlist only and a seat opened (upgrade opportunity)
    """
    if prev is None:
        if curr.has_open_seat:
            return RegistrationAction.REGISTER
        if curr.has_waitlist_spot:
            return RegistrationAction.WAITLIST
        return None

    prev_full = prev.seats_available == 0 and prev.wait_available == 0
    prev_waitlist_only = prev.seats_available == 0 and prev.wait_available > 0

    if curr.has_open_seat and (prev_full or prev_waitlist_only):
        return RegistrationAction.REGISTER

    if curr.has_waitlist_spot and prev_full:
        return RegistrationAction.WAITLIST

    return None

def state_changed(prev: CRNState | None, curr: ClassAvailability) -> bool:
    if prev is None:
        return False
    return (
        prev.seats_available != curr.seats_available
        or prev.wait_available != curr.wait_available
        or prev.open_section != curr.open_section
    )