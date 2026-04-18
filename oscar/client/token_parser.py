"""
Parse the X-Synchronizer-Token from Banner's registration page HTML.
Banner 9 embeds in:
  1. synchronizerToken / XSRF-TOKEN / _csrf cookie
  2. <meta name="synchronizerToken" content="...">
  3. <input type="hidden" name="_csrf" value="...">  or name="synchronizerToken"
  4. <script> tags

raises BannerError with a note to re-capture the registration page source and update this parser if everything fails
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TOKEN_IN_JS_RE = re.compile(
    r"""(?:synchronizerToken|csrfToken|_csrf)\s*[=:]\s*['"]([0-9a-f\-]{36})['"]""",
    re.IGNORECASE,
)

_COOKIE_NAMES = ("synchronizerToken", "XSRF-TOKEN", "X-Synchronizer-Token", "_csrf")
_META_NAMES = ("synchronizerToken", "_csrf", "csrf-token")
_INPUT_SELECTORS = (
    'input[name="_csrf"]',
    'input[name="synchronizerToken"]',
    'input[id="synchronizerToken"]',
)

def _is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value.strip()))

def parse_sync_token(html: str, cookies: dict[str, str] | None = None) -> str | None:
    if cookies:
        for name in _COOKIE_NAMES:
            val = cookies.get(name, "")
            if val and _is_uuid(val):
                return val

    tree = HTMLParser(html)

    for name in _META_NAMES:
        node = tree.css_first(f'meta[name="{name}"]')
        if node:
            val = node.attributes.get("content", "")
            if val and _is_uuid(val):
                return val

    for selector in _INPUT_SELECTORS:
        node = tree.css_first(selector)
        if node:
            val = node.attributes.get("value", "")
            if val and _is_uuid(val):
                return val

    for script in tree.css("script"):
        text = script.text() or ""
        m = _TOKEN_IN_JS_RE.search(text)
        if m and _is_uuid(m.group(1)):
            return m.group(1)

    return None
