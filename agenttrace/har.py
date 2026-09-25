"""HAR capture result & HAR 1.2 validation helpers (Part 2).

Playwright records a standards-compliant HAR directly (creator "Playwright").
This module adds:

- ``validate_har`` / ``validate_har_file`` — a strict structural validator for
  the HAR 1.2 format, returning a list of human-readable problems (an empty
  list means the HAR is valid).
- ``load_har`` / ``count_har_entries`` / ``summarize_entries`` — small helpers
  used by tests to verify correctness and to log what was captured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

HAR_VERSION = "1.2"

# HTTP methods are a fixed token set per RFC 9110.
_HTTP_METHODS = {
    "GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH",
}


@dataclass(frozen=True)
class HarCaptureResult:
    """Result of a capture-enabled navigation."""

    snapshot_url: str
    snapshot_title: str
    snapshot_status: Optional[int]
    har_path: Path
    request_events: int  # requests the browser actually dispatched
    request_urls: tuple  # URLs of those dispatched requests (for debugging)
    har_entries: int  # entries recorded in the HAR file
    matched: bool  # request_events == har_entries


def load_har(path: str | Path) -> dict:
    """Load a HAR file (UTF-8 JSON) into a dict."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def count_har_entries(har: dict) -> int:
    """Number of network entries in a HAR log dict (0 for malformed)."""
    log = har.get("log") if isinstance(har, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list):
        return 0
    return len(entries)


def summarize_entries(har: dict) -> list[dict]:
    """Return a compact per-entry list: method, url, status, mimeType."""
    out: list[dict] = []
    log = har.get("log") if isinstance(har, dict) else None
    if not isinstance(log, dict):
        return out
    for i, entry in enumerate(log.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        req = entry.get("request") or {}
        res = entry.get("response") or {}
        out.append(
            {
                "index": i,
                "method": req.get("method"),
                "url": req.get("url"),
                "status": res.get("status"),
                "mime": (res.get("content") or {}).get("mimeType"),
            }
        )
    return out


def har_with_entries(har: dict, entries: list) -> dict:
    """Return a copy of a HAR that keeps its metadata but replaces ``entries``.

    Used to split one recorded session HAR (Part 3 pre/post click, Part 6
    per-action) into several valid HAR files.
    """
    log = dict(har["log"])
    return {"log": {**log, "entries": entries}}


def save_har(path: str | Path, har: dict) -> Path:
    """Write a HAR dict to ``path`` as UTF-8 JSON; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(har, fh, indent=2, ensure_ascii=False)
    return path


# ----------------------------------------------------------------------
# HAR 1.2 structural validation
# ----------------------------------------------------------------------

def _is_object(value: object) -> bool:
    return isinstance(value, dict)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_iso_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True
def _check_headers(headers: object, where: str, errors: list[str]) -> None:
    if not isinstance(headers, list):
        errors.append(f"{where}.headers must be a list")
        return
    for i, h in enumerate(headers):
        if not _is_object(h):
            errors.append(f"{where}.headers[{i}] must be an object")
            continue
        if not isinstance(h.get("name"), str) or not isinstance(h.get("value"), str):
            errors.append(f"{where}.headers[{i}] must have string 'name' and 'value'")


def _check_cookies(cookies: object, where: str, errors: list[str]) -> None:
    if not isinstance(cookies, list):
        errors.append(f"{where}.cookies must be a list")
        return
    for i, c in enumerate(cookies):
        if not _is_object(c):
            errors.append(f"{where}.cookies[{i}] must be an object")
            continue
        if not isinstance(c.get("name"), str) or not isinstance(c.get("value"), str):
            errors.append(f"{where}.cookies[{i}] must have string 'name' and 'value'")


def _check_query_string(qs: object, where: str, errors: list[str]) -> None:
    if not isinstance(qs, list):
        errors.append(f"{where}.queryString must be a list")
        return
    for i, q in enumerate(qs):
        if not _is_object(q):
            errors.append(f"{where}.queryString[{i}] must be an object")
            continue
        if not isinstance(q.get("name"), str) or not isinstance(q.get("value"), str):
            errors.append(f"{where}.queryString[{i}] must have string 'name' and 'value'")


def _check_entry(entry: object, idx: int, errors: list[str]) -> None:
    where = f"log.entries[{idx}]"
    if not _is_object(entry):
        errors.append(f"{where} must be an object")
        return

    if not _is_iso_datetime(entry.get("startedDateTime")):
        errors.append(f"{where}.startedDateTime must be an ISO-8601 date-time")
    time_val = entry.get("time")
    # -1 is used by browsers (and Playwright) for failed/aborted requests.
    if not _is_number(time_val) or (time_val < 0 and time_val != -1):
        errors.append(f"{where}.time must be a non-negative number or -1 (got {time_val!r})")

    # ---- request ----
    req = entry.get("request")
    if not _is_object(req):
        errors.append(f"{where}.request must be an object")
    else:
        method = req.get("method")
        if not isinstance(method, str) or method.upper() not in _HTTP_METHODS:
            errors.append(f"{where}.request.method invalid: {method!r}")
        url = req.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
            errors.append(f"{where}.request.url must be an http(s) URL (got {url!r})")
        if not isinstance(req.get("httpVersion"), str):
            errors.append(f"{where}.request.httpVersion must be a string")
        _check_cookies(req.get("cookies"), f"{where}.request", errors)
        _check_headers(req.get("headers"), f"{where}.request", errors)
        _check_query_string(req.get("queryString"), f"{where}.request", errors)
        for key in ("headersSize", "bodySize"):
            val = req.get(key)
            if not _is_number(val):
                errors.append(f"{where}.request.{key} must be a number (got {val!r})")
        post = req.get("postData")
        if post is not None:
            if not _is_object(post) or not isinstance(post.get("mimeType"), str):
                errors.append(f"{where}.request.postData must be an object with mimeType")

    # ---- response ----
    res = entry.get("response")
    if not _is_object(res):
        errors.append(f"{where}.response must be an object")
    else:
        status = res.get("status")
        # Failed/aborted requests are recorded by browsers/Playwright with
        # status 0 or -1 - both are valid in real-world HAR files.
        if not _is_number(status) or not (
            (100 <= status <= 599) or status in (0, -1)
        ):
            errors.append(f"{where}.response.status invalid: {status!r}")
        if not isinstance(res.get("statusText"), str):
            errors.append(f"{where}.response.statusText must be a string")
        if not isinstance(res.get("httpVersion"), str):
            errors.append(f"{where}.response.httpVersion must be a string")
        if not isinstance(res.get("redirectURL"), str):
            errors.append(f"{where}.response.redirectURL must be a string")
        _check_cookies(res.get("cookies"), f"{where}.response", errors)
        _check_headers(res.get("headers"), f"{where}.response", errors)
        for key in ("headersSize", "bodySize"):
            val = res.get(key)
            if not _is_number(val):
                errors.append(f"{where}.response.{key} must be a number (got {val!r})")
        content = res.get("content")
        if not _is_object(content):
            errors.append(f"{where}.response.content must be an object")
        else:
            if not isinstance(content.get("mimeType"), str):
                errors.append(f"{where}.response.content.mimeType must be a string")
            if not _is_number(content.get("size")):
                errors.append(f"{where}.response.content.size must be a number")

    # ---- cache & timings ----
    if not _is_object(entry.get("cache")):
        errors.append(f"{where}.cache must be an object")
    timings = entry.get("timings")
    if not _is_object(timings):
        errors.append(f"{where}.timings must be an object")
    else:
        for key in ("blocked", "dns", "connect", "send", "wait", "receive"):
            val = timings.get(key)
            if val is not None and not _is_number(val):
                errors.append(f"{where}.timings.{key} must be a number (got {val!r})")
def validate_har(har: dict) -> list[str]:
    """Validate a HAR dict against HAR 1.2 structural rules.

    Returns a list of problems; an empty list means the HAR is valid.
    """
    errors: list[str] = []
    if not _is_object(har):
        return ["HAR root must be a JSON object"]
    log = har.get("log")
    if not _is_object(log):
        return ["log must be an object"]

    version = log.get("version")
    if version != HAR_VERSION:
        errors.append(f"log.version must be '1.2' (got {version!r})")
    creator = log.get("creator")
    if not _is_object(creator):
        errors.append("log.creator must be an object")
    elif not isinstance(creator.get("name"), str) or not isinstance(creator.get("version"), str):
        errors.append("log.creator must have string 'name' and 'version'")
    if "browser" in log and not _is_object(log["browser"]):
        errors.append("log.browser must be an object")

    pages = log.get("pages", [])
    if not isinstance(pages, list):
        errors.append("log.pages must be a list")
    else:
        for i, page in enumerate(pages):
            where = f"log.pages[{i}]"
            if not _is_object(page):
                errors.append(f"{where} must be an object")
                continue
            if not isinstance(page.get("id"), str):
                errors.append(f"{where}.id must be a string")
            if not _is_iso_datetime(page.get("startedDateTime")):
                errors.append(f"{where}.startedDateTime must be an ISO-8601 date-time")
            if not isinstance(page.get("title"), str):
                errors.append(f"{where}.title must be a string")
            pt = page.get("pageTimings")
            if not _is_object(pt):
                errors.append(f"{where}.pageTimings must be an object")
            else:
                for key in ("onContentLoad", "onLoad"):
                    val = pt.get(key)
                    if val is not None and not _is_number(val):
                        errors.append(f"{where}.pageTimings.{key} must be a number")

    entries = log.get("entries")
    if not isinstance(entries, list):
        errors.append("log.entries must be a list")
    else:
        for i, entry in enumerate(entries):
            _check_entry(entry, i, errors)

    return errors


def validate_har_file(path: str | Path) -> tuple[bool, list[str]]:
    """Load a HAR file and validate it; returns (is_valid, problems)."""
    try:
        har = load_har(path)
    except Exception as exc:  # noqa: BLE001
        return False, [f"could not read/parse HAR file: {exc}"]
    problems = validate_har(har)
    return (len(problems) == 0), problems