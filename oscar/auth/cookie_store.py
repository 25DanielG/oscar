from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
import httpx

# cookie names for expiry reporting
_TRACKED = {"CASTGC", "JSESSIONID", "BannerSessionId", "STSSESSIONID"}

def save_cookies(cookies: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies, indent=2, default=str), encoding="utf-8")

def load_cookies(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"No session file at {path}. Run: oscar auth refresh --headed"
        )
    return json.loads(path.read_text(encoding="utf-8"))

def as_httpx_cookies(cookies: list[dict[str, Any]]) -> httpx.Cookies:
    """Load Playwright cookies into a domain-aware httpx.Cookies jar. Playwrite exports domain as '.registration.banner.gatech.edu'.
    httpx.Cookies.set() wants the bare hostname. So strip the leading dot so httpx sends each cookie only to its correct host.
    JESSIONID is in both sso.gatech.edu and registration.banner.gatech.edu, need to send correct one to each.
    """
    jar = httpx.Cookies()
    for c in cookies:
        if not c.get("value"):
            continue
        domain = c.get("domain", "").lstrip(".")
        path = c.get("path", "/")
        jar.set(c["name"], c["value"], domain=domain, path=path)
    return jar

def castgc_hours_remaining(cookies: list[dict[str, Any]]) -> float | None:
    """Return hours until CASTGC expires, or None if not found or has no expiry."""
    now = datetime.now()
    for c in cookies:
        if c.get("name") == "CASTGC":
            ts = c.get("expires", -1)
            if not ts or float(ts) < 0:
                return None
            return (datetime.fromtimestamp(float(ts)) - now).total_seconds() / 3600
    return None

def cookie_expiry_summary(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now()
    out = []
    for c in cookies:
        if c.get("name") not in _TRACKED:
            continue
        ts = c.get("expires", -1)
        if not ts or float(ts) < 0:
            continue
        expires_dt = datetime.fromtimestamp(float(ts))
        out.append({
            "name": c["name"],
            "domain": c.get("domain", ""),
            "expires": expires_dt.isoformat(timespec="seconds"),
            "expired": expires_dt < now,
        })
    return sorted(out, key=lambda x: x["expires"])
