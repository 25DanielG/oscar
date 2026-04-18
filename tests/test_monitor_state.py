from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oscar.client.models import ClassAvailability
from oscar.db import init_db
from oscar.monitor.state import (
    CRNState,
    RegistrationAction,
    detect_transition,
    get_state,
    log_poll,
    state_changed,
    upsert_state,
)

# Fixtures

@pytest.fixture
def db() -> sqlite3.Connection:
    return init_db(Path(":memory:"))

def _avail(crn: str = "80168", term: str = "202608", seats: int = 0, wait: int = 0, open_section: bool = False) -> ClassAvailability:
    return ClassAvailability(
        crn=crn,
        term=term,
        course_title="Test Course",
        subject="CS",
        course_number="4400",
        seats_available=seats,
        max_enrollment=50,
        enrollment=50 - seats,
        wait_capacity=10,
        wait_count=10 - wait,
        wait_available=wait,
        open_section=open_section or seats > 0,
    )

def _state(seats: int, wait: int) -> CRNState:
    return CRNState(
        crn="80168",
        term="202608",
        seats_available=seats,
        wait_available=wait,
        open_section=seats > 0,
        last_seen="2026-04-17T00:00:00+00:00",
    )

# detect_transition, first poll

def test_first_poll_open_seat_triggers_register() -> None:
    assert detect_transition(None, _avail(seats=5)) == RegistrationAction.REGISTER

def test_first_poll_waitlist_triggers_waitlist() -> None:
    assert detect_transition(None, _avail(seats=0, wait=3)) == RegistrationAction.WAITLIST

def test_first_poll_full_returns_none() -> None:
    assert detect_transition(None, _avail(seats=0, wait=0)) is None

# detect_transition, from full state

def test_full_to_open_seat_triggers_register() -> None:
    prev = _state(seats=0, wait=0)
    curr = _avail(seats=3)
    assert detect_transition(prev, curr) == RegistrationAction.REGISTER

def test_full_to_waitlist_triggers_waitlist() -> None:
    prev = _state(seats=0, wait=0)
    curr = _avail(seats=0, wait=2)
    assert detect_transition(prev, curr) == RegistrationAction.WAITLIST

def test_still_full_returns_none() -> None:
    prev = _state(seats=0, wait=0)
    curr = _avail(seats=0, wait=0)
    assert detect_transition(prev, curr) is None

# detect_transition, from waitlist-only state

def test_waitlist_to_open_seat_triggers_register() -> None:
    prev = _state(seats=0, wait=3)
    curr = _avail(seats=2)
    assert detect_transition(prev, curr) == RegistrationAction.REGISTER

def test_waitlist_to_still_waitlist_returns_none() -> None:
    prev = _state(seats=0, wait=3)
    curr = _avail(seats=0, wait=5)
    assert detect_transition(prev, curr) is None

def test_open_to_still_open_returns_none() -> None:
    prev = _state(seats=3, wait=0)
    curr = _avail(seats=2)
    assert detect_transition(prev, curr) is None

def test_open_to_full_returns_none() -> None:
    prev = _state(seats=3, wait=0)
    curr = _avail(seats=0, wait=0)
    assert detect_transition(prev, curr) is None

# state_changed

def test_state_changed_no_prev_is_false() -> None:
    assert state_changed(None, _avail(seats=5)) is False

def test_state_changed_same_values_is_false() -> None:
    prev = _state(seats=5, wait=0)
    curr = _avail(seats=5, wait=0)
    assert state_changed(prev, curr) is False

def test_state_changed_seats_diff_is_true() -> None:
    prev = _state(seats=5, wait=0)
    curr = _avail(seats=4, wait=0)
    assert state_changed(prev, curr) is True

def test_state_changed_wait_diff_is_true() -> None:
    prev = _state(seats=0, wait=2)
    curr = _avail(seats=0, wait=3)
    assert state_changed(prev, curr) is True

# SQLite operations

def test_get_state_returns_none_when_missing(db: sqlite3.Connection) -> None:
    assert get_state(db, "99999", "202608") is None

def test_upsert_and_get_state_round_trip(db: sqlite3.Connection) -> None:
    avail = _avail(seats=5, wait=0)
    upsert_state(db, avail)

    row = get_state(db, "80168", "202608")
    assert row is not None
    assert row.crn == "80168"
    assert row.seats_available == 5
    assert row.wait_available == 0
    assert row.open_section is True

def test_upsert_overwrites_previous_state(db: sqlite3.Connection) -> None:
    upsert_state(db, _avail(seats=5))
    upsert_state(db, _avail(seats=0, wait=2))

    row = get_state(db, "80168", "202608")
    assert row is not None
    assert row.seats_available == 0
    assert row.wait_available == 2

def test_log_poll_writes_row(db: sqlite3.Connection) -> None:
    avail = _avail(seats=3)
    log_poll(db, avail, state_changed=True)

    row = db.execute("SELECT * FROM poll_log WHERE crn = '80168'").fetchone()
    assert row is not None
    assert row["seats_available"] == 3
    assert row["state_changed"] == 1

def test_log_poll_multiple_rows(db: sqlite3.Connection) -> None:
    log_poll(db, _avail(seats=0), state_changed=False)
    log_poll(db, _avail(seats=3), state_changed=True)

    count = db.execute("SELECT COUNT(*) FROM poll_log WHERE crn = '80168'").fetchone()[0]
    assert count == 2