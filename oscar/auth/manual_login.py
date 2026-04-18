"""
Headed Playwright login, manual authentication. Run if no session.json exists, duo re-auth, or headless fails.
Opens a real chromium window, prompting a manual auth.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import structlog
from playwright.async_api import BrowserContext, async_playwright

from oscar import log as applog
from oscar.auth.cookie_store import cookie_expiry_summary, save_cookies
from oscar.client.endpoints import OSCAR_HOME

log = structlog.get_logger()

_SSO_HOSTS = ("sso.gatech.edu", "duosecurity.com")

async def _run(profile_dir: Path, cookies_path: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)

    log.info("opening_browser", profile=str(profile_dir))

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            slow_mo=50,
            viewport={"width": 1280, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(OSCAR_HOME)

        print()
        print("=" * 60)
        print("Browser is open.")
        print("1. Complete GT SSO login.")
        print("2. In Duo, select \"Remember me\".")
        print("3. Wait until you are on the OSCAR homepage.")
        print("4. Press Enter here.")
        print("=" * 60)
        print()
        input("Press Enter once you are on the OSCAR homepage... ")

        await asyncio.sleep(1)

        # check all pages since OSCAR can open a new tab
        all_urls = [p.url for p in context.pages]
        log.debug("checking_pages", urls=all_urls)

        if not all_urls:
            log.error("browser_closed", msg="no pages open when Enter pressed")
            print("ERROR: Browser window was closed before pressing Enter.")
            print("Re-run the command and keep the browser open until you reach the OSCAR homepage.")
            await context.close()
            sys.exit(1)

        auth_pages = [u for u in all_urls if any(h in u for h in _SSO_HOSTS)]
        ok_pages = [u for u in all_urls if not any(h in u for h in _SSO_HOSTS)]

        if not ok_pages:
            log.error("login_incomplete", url=all_urls[0], all_urls=all_urls)
            print("ERROR: All open tabs still on SSO/Duo auth page.")
            for u in all_urls:
                print(f"  {u}")
            print("Complete the full login flow and land on the OSCAR homepage, then re-run.")
            await context.close()
            sys.exit(1)

        cookies = await context.cookies()
        await context.close()

    save_cookies(cookies, cookies_path)
    log.info("cookies_saved", path=str(cookies_path), total=len(cookies))

    summary = cookie_expiry_summary(cookies)
    if summary:
        print("\nKey cookie expiry:")
        for e in summary:
            tag = "EXPIRED" if e["expired"] else "OK"
            print(f"  {e['name']:20} {e['domain']:42} {e['expires']}  [{tag}]")

    print(f"\nsession.json written → {cookies_path}")

def main(profile_dir: Path | None = None, cookies_path: Path | None = None) -> None:
    from oscar.config import Settings

    settings = Settings()
    applog.configure()

    _profile = profile_dir or settings.browser_profile_dir
    _cookies = cookies_path or settings.load_config().cookies_path
    asyncio.run(_run(_profile, _cookies))

if __name__ == "__main__":
    main()
