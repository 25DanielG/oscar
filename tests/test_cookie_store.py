from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from oscar.auth.cookie_store import (
    as_httpx_cookies,
    castgc_hours_remaining,
    cookie_expiry_summary,
    load_cookies,
    save_cookies,
)

def test_round_trip(tmp_path: Path) -> None:
    cookies = [{"name": "CASTGC", "value": "abc", "domain": "sso.gatech.edu", "expires": 9999999999.0}]
    p = tmp_path / "session.json"
    save_cookies(cookies, p)
    assert load_cookies(p) == cookies

def test_load_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="oscar auth refresh"):
        load_cookies(tmp_path / "nope.json")

def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "dir" / "session.json"
    save_cookies([], p)
    assert p.exists()

def test_expiry_summary_filters_to_tracked(fake_cookies: Path) -> None:
    cookies = load_cookies(fake_cookies)
    summary = cookie_expiry_summary(cookies)
    names = {s["name"] for s in summary}
    assert "CASTGC" in names
    assert "JSESSIONID" in names
    assert "untracked" not in names

def test_expiry_summary_expired_flag(fake_cookies: Path) -> None:
    cookies = load_cookies(fake_cookies)
    summary = cookie_expiry_summary(cookies)
    by_name = {s["name"]: s for s in summary}
    assert not by_name["CASTGC"]["expired"]
    assert by_name["JSESSIONID"]["expired"]

def test_expiry_summary_sorted_by_expiry(fake_cookies: Path) -> None:
    cookies = load_cookies(fake_cookies)
    summary = cookie_expiry_summary(cookies)
    expires = [s["expires"] for s in summary]
    assert expires == sorted(expires)

def test_castgc_hours_remaining_future(future_ts: float) -> None:
    cookies = [{"name": "CASTGC", "value": "x", "domain": "sso.gatech.edu", "expires": future_ts}]
    hours = castgc_hours_remaining(cookies)
    assert hours is not None and hours > 0

def test_castgc_hours_remaining_expired(past_ts: float) -> None:
    cookies = [{"name": "CASTGC", "value": "x", "domain": "sso.gatech.edu", "expires": past_ts}]
    hours = castgc_hours_remaining(cookies)
    assert hours is not None and hours < 0

def test_castgc_hours_remaining_missing() -> None:
    assert castgc_hours_remaining([{"name": "OTHER", "value": "x", "domain": "x.com", "expires": 9999999999.0}]) is None

def test_castgc_hours_remaining_no_expiry() -> None:
    assert castgc_hours_remaining([{"name": "CASTGC", "value": "x", "domain": "x.com", "expires": -1}]) is None

def test_as_httpx_cookies(fake_cookies: Path) -> None:
    cookies = load_cookies(fake_cookies)
    flat = as_httpx_cookies(cookies)
    assert flat["CASTGC"] == "tgc_val"
    assert flat["JSESSIONID"] == "jsess_val"
    assert flat["untracked"] == "x"
