"""
Tests for Monitor._do_poll and the trigger/no-trigger decision.

We don't test the full run() loop here — that requires timing control and
is covered by integration testing against real OSCAR. These tests verify
the business logic inside _do_poll by wiring up a Monitor with:
  - an in-memory SQLite DB
  - a mock BannerClient
  - a captured on_trigger callback
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from oscar.client.models import ClassAvailability
from oscar.client.session import SessionExpiredError
from oscar.config import Config, CRNConfig, PollSettings
from oscar.db import init_db
from oscar.monitor.poller import Monitor
from oscar.monitor.state import RegistrationAction

def _make_config(tmp_path: Path) -> Config:
    session_json = tmp_path / "session.json"
    session_json.write_text("[]")
    return Config(
        term="202608",
        crns=[CRNConfig(crn="80168", label="Test")],
        poll=PollSettings(base_interval=10, jitter=0),
        cookies_path=session_json,
        db_path=tmp_path / "test.db",
        log_dir=tmp_path / "logs",
    )

def _avail(seats: int = 0, wait: int = 0) -> ClassAvailability:
    return ClassAvailability(
        crn="80168",
        term="202608",
        course_title="Test",
        subject="CS",
        course_number="4400",
        seats_available=seats,
        max_enrollment=50,
        enrollment=50 - seats,
        wait_capacity=10,
        wait_count=10 - wait,
        wait_available=wait,
        open_section=seats > 0,
    )

def _make_monitor(tmp_path: Path, on_trigger=None) -> tuple[Monitor, MagicMock]:
    config = _make_config(tmp_path)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    mon = Monitor(config=config, on_trigger=on_trigger)
    mon._session_ok = asyncio.Event()
    mon._session_ok.set()
    mon._expiry_lock = asyncio.Lock()
    mon._db = init_db(config.db_path)
    mon._client = mock_client
    return mon, mock_client


# _do_poll: trigger on first poll with open seat

async def test_do_poll_triggers_register_first_poll_open(tmp_path: Path) -> None:
    calls: list[tuple[ClassAvailability, RegistrationAction]] = []

    async def capture(avail: ClassAvailability, action: RegistrationAction) -> None:
        calls.append((avail, action))

    mon, mock_client = _make_monitor(tmp_path, on_trigger=capture)
    mock_client.get_availability.return_value = _avail(seats=5)

    await mon._do_poll("80168", "202608")

    assert len(calls) == 1
    assert calls[0][1] == RegistrationAction.REGISTER

async def test_do_poll_triggers_waitlist_first_poll_waitlist_only(tmp_path: Path) -> None:
    calls: list[tuple[ClassAvailability, RegistrationAction]] = []

    async def capture(avail: ClassAvailability, action: RegistrationAction) -> None:
        calls.append((avail, action))

    mon, mock_client = _make_monitor(tmp_path, on_trigger=capture)
    mock_client.get_availability.return_value = _avail(seats=0, wait=3)

    await mon._do_poll("80168", "202608")

    assert len(calls) == 1
    assert calls[0][1] == RegistrationAction.WAITLIST

async def test_do_poll_no_trigger_first_poll_full(tmp_path: Path) -> None:
    calls: list = []

    async def capture(avail, action) -> None:
        calls.append((avail, action))

    mon, mock_client = _make_monitor(tmp_path, on_trigger=capture)
    mock_client.get_availability.return_value = _avail(seats=0, wait=0)

    await mon._do_poll("80168", "202608")

    assert len(calls) == 0

# _do_poll: state transitions

async def test_do_poll_triggers_on_full_to_open(tmp_path: Path) -> None:
    calls: list[tuple[ClassAvailability, RegistrationAction]] = []

    async def capture(avail, action) -> None:
        calls.append((avail, action))

    mon, mock_client = _make_monitor(tmp_path, on_trigger=capture)

    # first poll: full
    mock_client.get_availability.return_value = _avail(seats=0, wait=0)
    await mon._do_poll("80168", "202608")
    assert len(calls) == 0

    # second poll: seat opened
    mock_client.get_availability.return_value = _avail(seats=2)
    await mon._do_poll("80168", "202608")
    assert len(calls) == 1
    assert calls[0][1] == RegistrationAction.REGISTER

async def test_do_poll_triggers_on_waitlist_to_open(tmp_path: Path) -> None:
    calls: list[tuple[ClassAvailability, RegistrationAction]] = []

    async def capture(avail, action) -> None:
        calls.append((avail, action))

    mon, mock_client = _make_monitor(tmp_path, on_trigger=capture)

    # start: waitlist only
    mock_client.get_availability.return_value = _avail(seats=0, wait=3)
    await mon._do_poll("80168", "202608")
    assert len(calls) == 1  # initial trigger for waitlist spot
    calls.clear()

    # next: seat opened
    mock_client.get_availability.return_value = _avail(seats=1)
    await mon._do_poll("80168", "202608")
    assert len(calls) == 1
    assert calls[0][1] == RegistrationAction.REGISTER

async def test_do_poll_no_trigger_when_already_open(tmp_path: Path) -> None:
    calls: list = []

    async def capture(avail, action) -> None:
        calls.append((avail, action))

    mon, mock_client = _make_monitor(tmp_path, on_trigger=capture)

    # first poll already open
    mock_client.get_availability.return_value = _avail(seats=5)
    await mon._do_poll("80168", "202608")
    calls.clear()

    # second poll still open (same state)
    mock_client.get_availability.return_value = _avail(seats=5)
    await mon._do_poll("80168", "202608")
    assert len(calls) == 0


# _do_poll: DB persists

async def test_do_poll_saves_state_to_db(tmp_path: Path) -> None:
    mon, mock_client = _make_monitor(tmp_path)
    mock_client.get_availability.return_value = _avail(seats=3)

    await mon._do_poll("80168", "202608")

    from oscar.monitor.state import get_state
    row = get_state(mon._db, "80168", "202608")  # type: ignore[arg-type]
    assert row is not None
    assert row.seats_available == 3

async def test_do_poll_logs_poll_to_db(tmp_path: Path) -> None:
    mon, mock_client = _make_monitor(tmp_path)
    mock_client.get_availability.return_value = _avail(seats=0)

    await mon._do_poll("80168", "202608")
    await mon._do_poll("80168", "202608")

    assert mon._db is not None
    count = mon._db.execute("SELECT COUNT(*) FROM poll_log").fetchone()[0]
    assert count == 2


# SessionExpiredError propagates, shouldnt trigger a notification or update db state

async def test_do_poll_propagates_session_expired(tmp_path: Path) -> None:
    mon, mock_client = _make_monitor(tmp_path)
    mock_client.get_availability.side_effect = SessionExpiredError("Session gone")

    with pytest.raises(SessionExpiredError):
        await mon._do_poll("80168", "202608")