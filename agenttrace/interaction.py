"""Interaction-aware network capture (Part 3) — before/after a click.

Strategy
--------
1. A dedicated browser context records the *whole* session (initial page load
   + click + resulting requests) into a single ``combined.har`` via
   Playwright's ``record_har_path``.
2. A ``page.on("request")`` handler buckets dispatched requests into a
   *pre-click* group and a *post-click* group; the stage flips immediately
   before the click is performed.
3. After the context closes (which flushes the HAR), the combined HAR is
   split by index into ``pre_click.har`` and ``post_click.har`` (Playwright
   writes entries in dispatch order, so index == chronological order).
4. Both HARs are structurally validated and the *new* requests introduced by
   the click (present in post-click, absent in pre-click) are reported.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .browser import BrowserEngine
from .har import har_with_entries, load_har, save_har, validate_har_file


@dataclass(frozen=True)
class BeforeAfterResult:
    """Result of a before/after-click capture (Part 3)."""

    url: str
    selector: str
    combined_har: Path
    pre_har: Path
    post_har: Path

    pre_entries: int  # HAR entries before the click
    post_entries: int  # HAR entries after the click
    count_matched: bool  # combined entries == pre_events + post_events

    pre_urls: tuple  # URLs captured before the click (dispatch order)
    post_urls: tuple  # URLs captured after the click (dispatch order)

    new_requests: tuple  # URLs in post-click HAR but NOT in pre-click HAR
    removed_requests: tuple  # URLs in pre-click HAR but NOT in post-click HAR

    pre_valid: bool  # pre-click HAR passed the validator
    post_valid: bool  # post-click HAR passed the validator
    pre_problems: tuple
    post_problems: tuple

    click_triggered_new_request: bool  # did the click introduce new network calls?
def _entry_urls(entries: list) -> list:
    urls = []
    for e in entries:
        if isinstance(e, dict):
            req = e.get("request") if isinstance(e.get("request"), dict) else {}
            urls.append(req.get("url"))
    return [u for u in urls if isinstance(u, str)]


def capture_click(
    engine: BrowserEngine,
    url: str,
    selector: str,
    *,
    har_dir: str | Path,
    click_timeout_ms: int = 15_000,
    wait_idle_ms: int = 6_000,
    settle_s: float = 0.5,
) -> BeforeAfterResult:
    """Record a single page-load + click into one combined HAR, then split it.

    Args:
        engine: an already-started :class:`BrowserEngine`.
        url: page to open.
        selector: CSS selector of the clickable element.
        har_dir: directory where ``combined.har``, ``pre_click.har`` and
            ``post_click.har`` are written.
        click_timeout_ms: max time to wait for the element to be clickable.
        wait_idle_ms: how long to wait for network idle after the click.
        settle_s: extra fixed settle time so the click's fetch resolves before
            the context is closed.
    """
    logger = logging.getLogger("agenttrace.interaction")
    har_dir = Path(har_dir)
    har_dir.mkdir(parents=True, exist_ok=True)

    combined = har_dir / "combined.har"
    pre_har = har_dir / "pre_click.har"
    post_har = har_dir / "post_click.har"
    for p in (combined, pre_har, post_har):
        if p.exists():
            p.unlink()

    logger.info("capture_click url=%s selector=%r har_dir=%s", url, selector, har_dir)

    context = engine.new_capture_context(record_har_path=str(combined))
    page = context.new_page()
    page.set_default_navigation_timeout(engine.navigation_timeout_ms)

    stage = {"name": "pre"}
    pre_events: list[str] = []
    post_events: list[str] = []

    def _on_request(request):
        url = str(request.url)
        if stage["name"] == "pre":
            pre_events.append(url)
        else:
            post_events.append(url)

    page.on("request", _on_request)

    try:
        # 1) initial navigation (still in the "pre" stage)
        engine._navigate(page, url, "load")

        # 2) flip the stage *before* the click so everything triggered by it
        #    lands in the post-click bucket.
        stage["name"] = "post"
        logger.info("Performing click on %r ...", selector)
        page.click(selector, timeout=click_timeout_ms)
        logger.info("Click performed (%r)", selector)

        # 3) let the request(s) triggered by the click complete
        try:
            page.wait_for_load_state("networkidle", timeout=wait_idle_ms)
            logger.info("Network idle reached after click for %s", url)
        except Exception:
            logger.warning(
                "Network did not reach idle within %sms after click; using settle.",
                wait_idle_ms,
            )
        if settle_s > 0:
            time.sleep(settle_s)

    finally:
        try:
            context.close()  # flushes combined.har
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error closing capture context: %s", exc)

    # ---- read combined HAR and split by request-event indices ----
    har = load_har(combined)
    entries = har["log"].get("entries") or []
    n_pre = len(pre_events)
    count_matched = len(entries) == n_pre + len(post_events)

    pre_entries = entries[:n_pre]
    post_entries = entries[n_pre:]

    save_har(pre_har, har_with_entries(har, pre_entries))
    save_har(post_har, har_with_entries(har, post_entries))

    pre_urls = tuple(_entry_urls(pre_entries))
    post_urls = tuple(_entry_urls(post_entries))

    pre_set = set(pre_urls)
    post_set = set(post_urls)
    new_requests = tuple(sorted(post_set - pre_set))
    removed_requests = tuple(sorted(pre_set - post_set))

    pre_valid, pre_problems = validate_har_file(pre_har)
    post_valid, post_problems = validate_har_file(post_har)

    result = BeforeAfterResult(
        url=url,
        selector=selector,
        combined_har=combined,
        pre_har=pre_har,
        post_har=post_har,
        pre_entries=len(pre_entries),
        post_entries=len(post_entries),
        count_matched=count_matched,
        pre_urls=pre_urls,
        post_urls=post_urls,
        new_requests=new_requests,
        removed_requests=removed_requests,
        pre_valid=pre_valid,
        post_valid=post_valid,
        pre_problems=tuple(pre_problems),
        post_problems=tuple(post_problems),
        click_triggered_new_request=bool(new_requests),
    )

    logger.info(
        "capture_click done | pre_entries=%d post_entries=%d count_matched=%s "
        "new_requests=%d",
        result.pre_entries,
        result.post_entries,
        result.count_matched,
        len(result.new_requests),
    )
    return result
