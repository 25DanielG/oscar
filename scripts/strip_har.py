#!/usr/bin/env python3
"""
Redact fields from HAR files for privacy.
  - All cookie values, keeps names
  - Authorization header values
  - Credentials fields in POST body
  - GTIDs

--no-student-id, to skip body scan
"""

import argparse
import json
import re
import sys
from pathlib import Path

REDACTED = "REDACTED"

SENSITIVE_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "x-authorization",
    "x-csrf-token",
})

SENSITIVE_POST_PARAMS = frozenset({
    "password",
    "j_password",
    "pass",
    "passwd",
    "j_username", # GT SSO login username
    "username",
})

# GTIDs
_GT_ID_RE = re.compile(r"\b9\d{8}\b")

# POST body field pattern
_POST_FIELD_RE = {
    name: re.compile(rf"({re.escape(name)}=)([^&\s]+)", re.IGNORECASE)
    for name in SENSITIVE_POST_PARAMS
}

def _redact_cookie_header(value: str) -> str:
    """Cookies: a=REDACTED; b=REDACTED"""
    out = []
    for part in value.split(";"):
        part = part.strip()
        if "=" in part:
            name, _ = part.split("=", 1)
            out.append(f"{name}={REDACTED}")
        else:
            out.append(part)
    return "; ".join(out)

def _redact_set_cookie_header(value: str) -> str:
    """Set-Cookie: name=REDACTED; Path=/; Secure"""
    parts = value.split(";")
    if not parts:
        return value
    first = parts[0].strip()
    if "=" in first:
        name, _ = first.split("=", 1)
        parts[0] = f"{name}={REDACTED}"
    return ";".join(parts)

def _process_headers(headers: list[dict], stats: dict) -> list[dict]:
    out = []
    for h in headers:
        name_lower = h["name"].lower()
        if name_lower == "cookie":
            h = {**h, "value": _redact_cookie_header(h["value"])}
            stats["cookies"] += 1
        elif name_lower == "set-cookie":
            h = {**h, "value": _redact_set_cookie_header(h["value"])}
            stats["cookies"] += 1
        elif name_lower in SENSITIVE_HEADERS:
            h = {**h, "value": REDACTED}
            stats["headers"] += 1
        out.append(h)
    return out

def _process_cookie_list(cookies: list[dict], stats: dict) -> list[dict]:
    """Redact the cookies[] array."""
    out = []
    for c in cookies:
        if c.get("value"):
            c = {**c, "value": REDACTED}
            stats["cookies"] += 1
        out.append(c)
    return out

def _process_post_data(post_data: dict | None, stats: dict) -> dict | None:
    if not post_data:
        return post_data

    # structured params list
    if post_data.get("params"):
        new_params = []
        for p in post_data["params"]:
            if p.get("name", "").lower() in SENSITIVE_POST_PARAMS:
                p = {**p, "value": REDACTED}
                stats["post_params"] += 1
            new_params.append(p)
        post_data = {**post_data, "params": new_params}

    # raw text body
    if post_data.get("text"):
        text = post_data["text"]
        for name, pattern in _POST_FIELD_RE.items():
            new_text, n = pattern.subn(rf"\1{REDACTED}", text)
            if n:
                text = new_text
                stats["post_params"] += n
        post_data = {**post_data, "text": text}

    return post_data

def _process_entry(entry: dict, stats: dict, redact_student_id: bool) -> dict:
    req = entry.get("request", {})
    resp = entry.get("response", {})

    if req.get("headers"):
        req["headers"] = _process_headers(req["headers"], stats)
    if req.get("cookies"):
        req["cookies"] = _process_cookie_list(req["cookies"], stats)
    if req.get("postData"):
        req["postData"] = _process_post_data(req["postData"], stats)

    if resp.get("headers"):
        resp["headers"] = _process_headers(resp["headers"], stats)
    if resp.get("cookies"):
        resp["cookies"] = _process_cookie_list(resp["cookies"], stats)

    if redact_student_id:
        content = resp.get("content", {})
        text = content.get("text", "")
        if text:
            new_text, n = _GT_ID_RE.subn(REDACTED, text)
            if n:
                content["text"] = new_text
                stats["student_ids"] += n

    return entry

def strip_har(src: Path, redact_student_id: bool) -> tuple[Path, dict]:
    stats = {"cookies": 0, "headers": 0, "post_params": 0, "student_ids": 0}

    har = json.loads(src.read_text(encoding="utf-8"))

    entries = har.get("log", {}).get("entries", [])
    har["log"]["entries"] = [
        _process_entry(e, stats, redact_student_id) for e in entries
    ]

    dst = src.with_suffix(".redacted.har")
    dst.write_text(json.dumps(har, indent=2, ensure_ascii=False), encoding="utf-8")

    return dst, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Strip sensitive data from HAR files before sharing.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--no-student-id",
        dest="redact_student_id",
        action="store_false",
        default=True,
        help="Skip 9-digit GT student ID scan in response bodies",
    )
    args = parser.parse_args()

    any_error = False
    for path in args.files:
        if not path.exists():
            print(f"ERROR: {path} not found", file=sys.stderr)
            any_error = True
            continue

        print(f"{path.name}")
        try:
            dst, stats = strip_har(path, args.redact_student_id)
            print(f"  → {dst.name}")
            print(f"     cookies:      {stats['cookies']}")
            print(f"     headers:      {stats['headers']}")
            print(f"     post fields:  {stats['post_params']}")
            print(f"     student IDs:  {stats['student_ids']}")
        except json.JSONDecodeError as e:
            print(f"ERROR: {path} not valid JSON — {e}", file=sys.stderr)
            any_error = True
        except Exception as e:
            print(f"ERROR: {path} — {e}", file=sys.stderr)
            any_error = True

    sys.exit(1 if any_error else 0)


if __name__ == "__main__":
    main()