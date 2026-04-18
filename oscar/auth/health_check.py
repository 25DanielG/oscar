"""
Httpx session health check. Hits banner session validation endpoint, detects SSO redirect. Used by monitor loop to catch expiry mid-session before polling.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import structlog

from oscar.auth.cookie_store import as_httpx_cookies, load_cookies
from oscar.client.endpoints import OSCAR_SESSION_CHECK

log = structlog.get_logger()

_SSO_HOSTS = ("sso.gatech.edu", "duosecurity.com")

async def check_session(cookies_path: Path) -> bool:
    cookies = as_httpx_cookies(load_cookies(cookies_path))

    async with httpx.AsyncClient(
        cookies=cookies,
        follow_redirects=False,
        timeout=10.0,
    ) as client:
        try:
            response = await client.get(OSCAR_SESSION_CHECK)
        except httpx.RequestError as exc:
            log.error("session_check_network_error", error=str(exc))
            return False

    if response.is_redirect:
        location = response.headers.get("location", "")
        if any(h in location for h in _SSO_HOSTS):
            log.warning("session_expired_via_redirect", location=location)
            return False
        log.warning("session_check_unexpected_redirect", location=location)
        return False

    log.info("session_check_ok", status=response.status_code)
    return True

async def _main() -> int:
    from oscar.config import Settings
    from oscar import log as applog

    settings = Settings()
    applog.configure()
    config = settings.load_config()

    try:
        healthy = await check_session(config.cookies_path)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print("Session: OK" if healthy else "Session: EXPIRED")
    return 0 if healthy else 1

if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
