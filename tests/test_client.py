from __future__ import annotations

import httpx
import pytest
import respx

from oscar.client.endpoints import (
    CLASS_REGISTRATION_PAGE,
    CLASS_SEARCH,
    GET_SECTION_DETAILS,
    REGISTRATION_PAGE,
    TERM_SEARCH,
)
from oscar.client.session import BannerClient, BannerError, SchemaDriftError, SessionExpiredError

_SYNC_TOKEN = "13ab7c16-286f-4ead-a7a4-1687d5b1e7d2"
_COOKIES = {"CASTGC": "tgc_value", "JSESSIONID": "sess_value"}

_REG_PAGE_HTML = f'<meta name="synchronizerToken" content="{_SYNC_TOKEN}">'

_SECTION_DETAILS = {
    "subject": "CS",
    "courseTitle": "Database Systems",
    "sequenceNumber": "A",
    "courseNumber": "4400",
    "success": True,
    "olr": False,
}

_SEARCH_OPEN = {
    "success": True,
    "totalCount": 1,
    "data": [
        {
            "courseReferenceNumber": "80168",
            "term": "202608",
            "courseTitle": "Database Systems",
            "subject": "CS",
            "courseNumber": "4400",
            "seatsAvailable": 5,
            "maximumEnrollment": 50,
            "enrollment": 45,
            "waitCapacity": 10,
            "waitCount": 0,
            "waitAvailable": 10,
            "openSection": True,
        }
    ],
}

_SEARCH_WAITLIST_ONLY = {
    "success": True,
    "totalCount": 1,
    "data": [
        {
            "courseReferenceNumber": "80168",
            "term": "202608",
            "courseTitle": "Database Systems",
            "subject": "CS",
            "courseNumber": "4400",
            "seatsAvailable": 0,
            "maximumEnrollment": 50,
            "enrollment": 50,
            "waitCapacity": 10,
            "waitCount": 3,
            "waitAvailable": 7,
            "openSection": False,
        }
    ],
}

_SEARCH_FULL = {
    "success": True,
    "totalCount": 1,
    "data": [
        {
            "courseReferenceNumber": "80168",
            "term": "202608",
            "courseTitle": "Database Systems",
            "subject": "CS",
            "courseNumber": "4400",
            "seatsAvailable": 0,
            "maximumEnrollment": 50,
            "enrollment": 50,
            "waitCapacity": 10,
            "waitCount": 10,
            "waitAvailable": 0,
            "openSection": False,
        }
    ],
}

def _mock_acquire_tokens() -> None:
    respx.get(REGISTRATION_PAGE).mock(return_value=httpx.Response(200, text=_REG_PAGE_HTML))
    respx.post(TERM_SEARCH).mock(return_value=httpx.Response(200, text=""))
    respx.get(CLASS_REGISTRATION_PAGE).mock(return_value=httpx.Response(200, text="ok"))

def _mock_base(search_payload: dict) -> None:
    _mock_acquire_tokens()
    respx.get(GET_SECTION_DETAILS).mock(return_value=httpx.Response(200, json=_SECTION_DETAILS))
    respx.get(CLASS_SEARCH).mock(return_value=httpx.Response(200, json=search_payload))

@respx.mock
async def test_open_seat_flags() -> None:
    _mock_base(_SEARCH_OPEN)
    async with BannerClient(_COOKIES, "202608") as client:
        avail = await client.get_availability("80168", "202608")

    assert avail.crn == "80168"
    assert avail.seats_available == 5
    assert avail.has_open_seat
    assert not avail.has_waitlist_spot
    assert not avail.is_full

@respx.mock
async def test_waitlist_only_flags() -> None:
    _mock_base(_SEARCH_WAITLIST_ONLY)
    async with BannerClient(_COOKIES, "202608") as client:
        avail = await client.get_availability("80168", "202608")

    assert not avail.has_open_seat
    assert avail.has_waitlist_spot
    assert not avail.is_full

@respx.mock
async def test_full_section_flags() -> None:
    _mock_base(_SEARCH_FULL)
    async with BannerClient(_COOKIES, "202608") as client:
        avail = await client.get_availability("80168", "202608")

    assert not avail.has_open_seat
    assert not avail.has_waitlist_spot
    assert avail.is_full

@respx.mock
async def test_session_expired_on_registration_page() -> None:
    # follow_redirects=True means respx follows the 302 to SSO, mock the destination too.
    respx.get(REGISTRATION_PAGE).mock(
        return_value=httpx.Response(
            302, headers={"location": "https://sso.gatech.edu/cas/login?TARGET=..."}
        )
    )
    respx.get("https://sso.gatech.edu/cas/login").mock(
        return_value=httpx.Response(200, text="SSO login page")
    )
    with pytest.raises(SessionExpiredError, match="sso.gatech.edu"):
        async with BannerClient(_COOKIES, "202608"):
            pass

@respx.mock
async def test_session_expired_mid_request() -> None:
    _mock_acquire_tokens()
    respx.get(GET_SECTION_DETAILS).mock(
        return_value=httpx.Response(
            302, headers={"location": "https://sso.gatech.edu/cas/login"}
        )
    )
    with pytest.raises(SessionExpiredError):
        async with BannerClient(_COOKIES, "202608") as client:
            await client.get_availability("80168", "202608")

@respx.mock
async def test_crn_not_found_raises() -> None:
    _mock_base({**_SEARCH_OPEN, "data": []})
    with pytest.raises(BannerError, match="not found in search results"):
        async with BannerClient(_COOKIES, "202608") as client:
            await client.get_availability("80168", "202608")

@respx.mock
async def test_section_details_cached() -> None:
    _mock_acquire_tokens()
    details_route = respx.get(GET_SECTION_DETAILS).mock(
        return_value=httpx.Response(200, json=_SECTION_DETAILS)
    )
    respx.get(CLASS_SEARCH).mock(return_value=httpx.Response(200, json=_SEARCH_OPEN))

    async with BannerClient(_COOKIES, "202608") as client:
        await client.get_availability("80168", "202608")
        await client.get_availability("80168", "202608")

    # one details request even though two availability calls
    assert details_route.call_count == 1

@respx.mock
async def test_token_parse_failure_raises() -> None:
    respx.get(REGISTRATION_PAGE).mock(
        return_value=httpx.Response(200, text="<html><body>no token here</body></html>")
    )
    respx.post(TERM_SEARCH).mock(return_value=httpx.Response(200, text=""))
    respx.get(CLASS_REGISTRATION_PAGE).mock(return_value=httpx.Response(200, text="ok"))
    with pytest.raises(BannerError, match="X-Synchronizer-Token"):
        async with BannerClient(_COOKIES, "202608"):
            pass

@respx.mock
async def test_search_api_error_raises() -> None:
    _mock_acquire_tokens()
    respx.get(GET_SECTION_DETAILS).mock(return_value=httpx.Response(200, json=_SECTION_DETAILS))
    respx.get(CLASS_SEARCH).mock(
        return_value=httpx.Response(200, json={"success": False, "message": "Session timed out"})
    )
    with pytest.raises(BannerError, match="Class search failed"):
        async with BannerClient(_COOKIES, "202608") as client:
            await client.get_availability("80168", "202608")

# schema drift

@respx.mock
async def test_schema_drift_missing_field_in_search() -> None:
    section = {k: v for k, v in _SEARCH_OPEN["data"][0].items() if k != "seatsAvailable"}
    _mock_base({"success": True, "totalCount": 1, "data": [section]})
    with pytest.raises(SchemaDriftError, match="seatsAvailable"):
        async with BannerClient(_COOKIES, "202608") as client:
            await client.get_availability("80168", "202608")

@respx.mock
async def test_schema_drift_wrong_type_in_search() -> None:
    section = {**_SEARCH_OPEN["data"][0], "seatsAvailable": "not-an-int"}
    _mock_base({"success": True, "totalCount": 1, "data": [section]})
    with pytest.raises(SchemaDriftError, match="ClassAvailability schema mismatch"):
        async with BannerClient(_COOKIES, "202608") as client:
            await client.get_availability("80168", "202608")

@respx.mock
async def test_schema_drift_missing_field_in_section_details() -> None:
    broken = {k: v for k, v in _SECTION_DETAILS.items() if k != "courseNumber"}
    _mock_acquire_tokens()
    respx.get(GET_SECTION_DETAILS).mock(return_value=httpx.Response(200, json={**broken, "success": True}))
    with pytest.raises(SchemaDriftError, match="courseNumber"):
        async with BannerClient(_COOKIES, "202608") as client:
            await client.get_availability("80168", "202608")
