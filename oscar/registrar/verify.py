"""Vverification after registration with getRegistrationEvents."""

from __future__ import annotations
import asyncio
import structlog
from oscar.client.session import BannerClient, BannerError, SessionExpiredError

log = structlog.get_logger()

_POLL_INTERVAL = 2.0

async def verify_registered(client: BannerClient, crn: str, term: str, timeout: float = 10.0) -> bool:
    """Return True if CRN is in registration events within timeout seconds.
    Banner registration is async server-side, so the CRN might not appear immediately after a 200 POST.
    Poll every 2s up to timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout

    while True:
        try:
            events = await client.get_registration_events(term)
        except SessionExpiredError as exc:
            log.warning("verify_session_expired", crn=crn, error=str(exc))
            return False
        except BannerError as exc:
            log.warning("verify_events_error", crn=crn, error=str(exc))
            events = []

        if any(str(e.get("crn")) == crn for e in events):
            log.info("verify_confirmed", crn=crn, term=term)
            return True

        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            log.warning("verify_timeout", crn=crn, term=term, timeout=timeout)
            return False

        await asyncio.sleep(min(_POLL_INTERVAL, remaining))
