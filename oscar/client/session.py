"""
Authenticated Banner 9 HTTP client.
    async with BannerClient.from_path(config.cookies_path) as client:
        avail = await client.get_availability("80168", "202608")

Starting, the client loads the registration page to grab the X-Synchronizer-Token and check the session is alive. 
Later reqs that redirects to SSO raises SessionExpiredError, the caller needs to catch this, alert, and pause until re-auth finishes.

Availability lookup:
  1. getSectionDetailsFromCRN → subject + courseNumber (cached per CRN)
  2. searchResults?subject=...&courseNumber=... → filter by CRN

The cache causes after the first CRN poll, later polls are 1 http request, not 2.
"""

from __future__ import annotations
import random
import string
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import ValidationError

from oscar.auth.cookie_store import as_httpx_cookies, load_cookies
from oscar.client.endpoints import (
    ADD_CRN_ITEMS,
    CLASS_REGISTRATION_PAGE,
    CLASS_SEARCH,
    CLASS_SEARCH_RESET,
    GET_SECTION_DETAILS,
    REGISTRATION_EVENTS,
    REGISTRATION_PAGE,
    SUBMIT_REGISTRATION,
    TERM_SEARCH,
)
from oscar.client.models import ClassAvailability, SectionDetails
from oscar.client.token_parser import parse_sync_token

log = structlog.get_logger()

_SSO_HOSTS = ("sso.gatech.edu", "duosecurity.com")

_BASE_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

class SessionExpiredError(Exception):
    """Session redirected to GT SSO. Re-auth required before next poll."""

class BannerError(Exception):
    """Banner returned an unexpected response or success=false."""

class SchemaDriftError(Exception):
    """Banner API response shape changed — a field is missing or has an unexpected type."""

def _make_session_id() -> str:
    prefix = "".join(random.choices(string.ascii_lowercase, k=5))
    return f"{prefix}{int(time.time() * 1000)}"

class BannerClient:
    def __init__(self, cookies: httpx.Cookies, term: str) -> None:
        self._cookies = cookies
        self._term = term
        self._sync_token: str | None = None
        self._session_id: str = _make_session_id()
        self._section_cache: dict[tuple[str, str], SectionDetails] = {}
        self._http: httpx.AsyncClient | None = None

    @classmethod
    def from_path(cls, cookies_path: Path, term: str) -> "BannerClient":
        return cls(as_httpx_cookies(load_cookies(cookies_path)), term)

    async def __aenter__(self) -> "BannerClient":
        self._http = httpx.AsyncClient(
            cookies=self._cookies,
            headers=_BASE_HEADERS,
            follow_redirects=False,
            timeout=15.0,
        )
        await self._acquire_tokens()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    # helpers

    def _check(self, response: httpx.Response) -> None:
        """Raise SessionExpiredError on SSO redirect, BannerError on 4xx/5xx."""
        if response.is_redirect:
            location = response.headers.get("location", "")
            log.debug("redirect_detected", url=str(response.url), location=location, status=response.status_code)
            if any(h in location for h in _SSO_HOSTS):
                raise SessionExpiredError(f"Session expired → {location}")
            if "registration/registration" in location:
                raise BannerError(
                    f"Banner redirected to registration page — "
                    f"registration window may not be open yet for term {self._term}"
                )
            raise BannerError(f"Unexpected redirect → {location}")
        if response.is_error:
            raise BannerError(
                f"HTTP {response.status_code} from {response.url} "
                f"(registration window may be closed)"
            )

    def _headers(self) -> dict[str, str]:
        return {"X-Synchronizer-Token": self._sync_token or ""}

    async def _acquire_tokens(self) -> None:
        assert self._http is not None
        log.info("acquiring_session_tokens", session_id=self._session_id)

        # load registration page, follow CAS redirects to create Banner SSB session, registration page is the sync-token source and session entry point.
        reg_response = await self._http.get(REGISTRATION_PAGE, follow_redirects=True)
        if any(h in str(reg_response.url) for h in _SSO_HOSTS):
            raise SessionExpiredError(f"CAS session expired (CASTGC gone) → {reg_response.url}")

        # set term in both servlet contexts (search and registration are separate server-side sessions).
        await self._http.post(
            TERM_SEARCH,
            params={"mode": "search"},
            data={"term": self._term},
            headers=_BASE_HEADERS,
            follow_redirects=True,
        )
        await self._http.post(
            TERM_SEARCH,
            params={"mode": "registration"},
            data={"term": self._term},
            headers=_BASE_HEADERS,
            follow_redirects=True,
        )

        # trigger CAS ticket acquisition for the classRegistration servlet.
        # classRegistration has a separate CAS security context from registration.
        # first hit: 302 → SSO (TGC auto-validates) → login/cas?SAMLart → ajaxSuccess.
        step3 = await self._http.get(CLASS_REGISTRATION_PAGE, follow_redirects=True)

        # after an ajaxSuccess, the classRegistration servlet's term context is cleared.
        # post again term to restore it in the CAS-authed session.
        if "ajaxSuccess" in str(step3.url):
            await self._http.post(
                TERM_SEARCH,
                params={"mode": "registration"},
                data={"term": self._term},
                headers=_BASE_HEADERS,
                follow_redirects=True,
            )
            
            # classRegistration now creates the actual form and creates the server-side `registrations` session object needed by addCRNRegistrationItems.
            await self._http.get(CLASS_REGISTRATION_PAGE, follow_redirects=True)

        jar_cookies = {c.name: c.value for c in self._http.cookies.jar}
        token = parse_sync_token(reg_response.text, jar_cookies)

        if not token:
            raise BannerError(
                "Could not parse X-Synchronizer-Token from registration page. "
                "Capture the page source and update client/token_parser.py. "
                "See BANNER_ENDPOINTS.md §9."
            )

        self._sync_token = token
        log.info("tokens_acquired", sync_token=token[:8] + "…", session_id=self._session_id)

    async def _reinit_session(self) -> None:
        self._session_id = _make_session_id()
        self._section_cache.clear()
        log.info("session_reinit", new_session_id=self._session_id)
        await self._acquire_tokens()

    #  API

    async def get_section_details(self, crn: str, term: str) -> SectionDetails:
        """Fetch subject + course number for a CRN. Result cached for session lifetime."""
        key = (crn, term)
        if key in self._section_cache:
            return self._section_cache[key]

        assert self._http is not None
        response = await self._http.get(
            GET_SECTION_DETAILS,
            params={"courseReferenceNumber": crn, "term": term},
            headers=self._headers(),
        )
        self._check(response)

        data = response.json()
        if not data.get("success"):
            raise BannerError(f"getSectionDetailsFromCRN failed for CRN {crn}: {data}")

        try:
            details = SectionDetails(
                crn=crn,
                term=term,
                subject=data["subject"],
                course_number=data["courseNumber"],
                sequence_number=data["sequenceNumber"],
                course_title=data["courseTitle"],
                olr=data.get("olr", False),
            )
        except (KeyError, ValidationError) as exc:
            raise SchemaDriftError(
                f"SectionDetails schema mismatch for CRN {crn}: {exc}. "
                f"Response keys: {sorted(data.keys())}"
            ) from exc
        self._section_cache[key] = details
        log.debug("section_details_cached", crn=crn, subject=details.subject, course=details.course_number)
        return details

    async def get_availability(self, crn: str, term: str) -> ClassAvailability:
        """Return current seat/waitlist availability for a CRN."""
        details = await self.get_section_details(crn, term)

        assert self._http is not None

        await self._http.post(CLASS_SEARCH_RESET, headers=self._headers())

        response = await self._http.get(
            CLASS_SEARCH,
            params={
                "txt_subject": details.subject,
                "txt_courseNumber": details.course_number,
                "txt_term": term,
                "pageOffset": 0,
                "pageMaxSize": 500,
                "sortColumn": "subjectDescription",
                "sortDirection": "asc",
                "uniqueSessionId": self._session_id,
            },
            headers=self._headers(),
        )
        self._check(response)

        data = response.json()
        if not data.get("success"):
            raise BannerError(f"Class search failed: {data}")

        total = data.get("totalCount", "?")
        raw_sections = data.get("data")
        if raw_sections is None:
            log.warning("banner_data_null_reinit", crn=crn, msg="Banner returned null data — reinitialising session")
            await self._reinit_session()
            response = await self._http.get(
                CLASS_SEARCH,
                params={
                    "txt_subject": details.subject,
                    "txt_courseNumber": details.course_number,
                    "txt_term": term,
                    "pageOffset": 0,
                    "pageMaxSize": 500,
                    "sortColumn": "subjectDescription",
                    "sortDirection": "asc",
                    "uniqueSessionId": self._session_id,
                },
                headers=self._headers(),
            )
            self._check(response)
            data = response.json()
            raw_sections = data.get("data")
            if raw_sections is None:
                log.warning("banner_data_null", crn=crn, msg="Banner still null after reinit — system may be down")
        sections = raw_sections or []
        returned = len(sections)
        log.info("class_search_results", crn=crn, subject=details.subject, course=details.course_number, total=total, returned=returned)

        for section in sections:
            if section.get("courseReferenceNumber") == crn:
                try:
                    avail = ClassAvailability(
                        crn=section["courseReferenceNumber"],
                        term=section["term"],
                        course_title=section["courseTitle"],
                        subject=section["subject"],
                        course_number=section["courseNumber"],
                        seats_available=section["seatsAvailable"],
                        max_enrollment=section["maximumEnrollment"],
                        enrollment=section["enrollment"],
                        wait_capacity=section["waitCapacity"],
                        wait_count=section["waitCount"],
                        wait_available=section["waitAvailable"],
                        open_section=section["openSection"],
                    )
                except (KeyError, ValidationError) as exc:
                    raise SchemaDriftError(
                        f"ClassAvailability schema mismatch for CRN {crn}: {exc}. "
                        f"Response keys: {sorted(section.keys())}"
                    ) from exc
                log.debug(
                    "availability_fetched",
                    crn=crn,
                    seats=avail.seats_available,
                    wait=avail.wait_available,
                    open=avail.open_section,
                )
                return avail

        found_crns = [s.get("courseReferenceNumber") for s in (data.get("data") or [])]
        log.warning("crn_not_in_results", crn=crn, found_crns=found_crns)
        raise BannerError(
            f"CRN {crn} not found in search results "
            f"(searched {details.subject} {details.course_number}, term {term}). "
            f"Verify term code and that the section exists."
        )

    async def fetch_registration_model(self, crn: str, term: str) -> dict[str, Any]:
        """Registration step 1, fetch the full model object for a CRN.
        Call before submit_registration. The returned model contains
        all Banner fields required in the POST body, pass it through unchanged
        (with only courseRegistrationStatus overwritten).
        """
        assert self._http is not None
        response = await self._http.get(
            ADD_CRN_ITEMS,
            params={
                "term": term,
                "crnList": crn,
                "addAllLinkedSections": "true",
                "uniqueSessionId": self._session_id,
            },
            headers=self._headers(),
        )
        self._check(response)

        data = response.json()
        rows = data.get("aaData", [])
        if not rows:
            raise BannerError(f"addCRNRegistrationItems returned empty aaData for CRN {crn}")

        row = rows[0]
        if not row.get("success"):
            raise BannerError(f"addCRNRegistrationItems failed for CRN {crn}: {row}")

        model = row.get("model")
        if not model:
            raise BannerError(f"addCRNRegistrationItems returned no model for CRN {crn}: {row}")

        log.debug("registration_model_fetched", crn=crn, term=term)
        return model

    async def submit_registration(self, model: dict[str, Any], term: str) -> dict[str, Any]:
        """Registration step 2, POST the model to submitRegistration/batch.
        Caller must set model['courseRegistrationStatus'] to 'RW' (open seat)
        or 'WL' (waitlist) before calling this.
        Returns the raw response dict.  Caller is responsible for checking
        success + errorFlag + statusIndicator (Banner returns HTTP 200 for both
        success and failure — see BANNER_ENDPOINTS.md §10).
        """
        assert self._http is not None
        body: dict[str, Any] = {
            "create": [],
            "update": [model],
            "destroy": [],
            "uniqueSessionId": self._session_id,
        }
        response = await self._http.post(
            SUBMIT_REGISTRATION,
            json=body,
            headers=self._headers(),
        )
        self._check(response)
        return response.json()

    async def get_registration_events(self, term: str) -> list[dict[str, Any]]:
        """Return calendar events for all registered courses in a term."""
        assert self._http is not None
        response = await self._http.get(
            REGISTRATION_EVENTS,
            params={"termFilter": term},
            headers=self._headers(),
        )
        self._check(response)
        return response.json()
