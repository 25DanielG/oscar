"""
Headless Playwright session refresh. Loads existing browser profile and navigates to OSCAR.
Landing on the homepage without SSO redirect means the session is still valid, exports fresh cookies.
0 success, 1 expiry, run weekly or when needed to auth.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import structlog
from playwright.async_api import async_playwright

from oscar import log as applog
from oscar.auth.cookie_store import cookie_expiry_summary, save_cookies
from oscar.client.endpoints import OSCAR_HOME

log = structlog.get_logger()

_SSO_HOSTS = ("sso.gatech.edu", "duosecurity.com")

async def _check(profile_dir: Path, cookies_path: Path) -> bool:
    if not profile_dir.exists():
        log.warning("no_browser_profile", path=str(profile_dir))
        return False

    log.info("headless_session_check", profile=str(profile_dir))

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=True,
        )

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            await page.goto(OSCAR_HOME, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            log.error("navigation_failed", error=str(exc))
            await context.close()
            return False

        final_url = page.url

        if any(h in final_url for h in _SSO_HOSTS):
            log.warning("session_expired", redirected_to=final_url)
            await context.close()
            return False

        cookies = await context.cookies()
        await context.close()

    save_cookies(cookies, cookies_path)
    log.info("session_valid_cookies_refreshed", path=str(cookies_path))

    for entry in cookie_expiry_summary(cookies):
        log.info("cookie_expiry", **entry)

    return True

def main(profile_dir: Path | None = None, cookies_path: Path | None = None) -> int:
    from oscar.config import Settings

    settings = Settings()
    applog.configure()

    _profile = profile_dir or settings.browser_profile_dir
    try:
        _cookies = cookies_path or settings.load_config().cookies_path
    except FileNotFoundError:
        _cookies = Path("session.json")

    valid = asyncio.run(_check(_profile, _cookies))

    if valid:
        print("Session valid. Cookies refreshed.")
        return 0

    print("Session expired. Run: oscar auth refresh --headed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
