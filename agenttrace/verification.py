"""Reliability & verification layer (Part 4).

Three responsibilities:

1. ``NetworkActivityTracker`` — tracks in-flight requests and the timestamp of
   the last network activity for a page, and can *wait for the network to
   settle*: no in-flight request, a quiet window of no new activity, and all
   expected URLs observed. This is stricter than Playwright's ``networkidle``
   because a request that starts *after* the page briefly became idle (e.g. a
   timer-triggered ``fetch``) is still awaited.

2. ``verify_capture`` — inspects a written HAR and decides whether the capture
   is *complete*: non-empty, every expected request present, and its response
   actually finished (valid status + non-empty body). This detects
   premature/empty captures.

3. ``capture_verified`` — performs the capture, waits for the network to settle,
   verifies the HAR and retries with a fresh context until the capture verifies
   or ``max_attempts`` is exhausted. This is what guarantees "0% premature /
   empty HAR" on pages with delayed or slow APIs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .browser import BrowserEngine
from .har import count_har_entries, load_har

DEFAULT_QUIET_MS = 500
DEFAULT_MAX_WAIT_MS = 30_000
DEFAULT_POLL_MS = 50


@dataclass(frozen=True)
class IdleWaitResult:
    """Outcome of waiting for the network to settle."""

    idle: bool
    waited_ms: int
    pending: int  # requests still in flight when we stopped waiting
    missing_urls: tuple  # expected URLs that were never observed
    quiet_ms: int
    max_wait_ms: int


@dataclass(frozen=True)
class CaptureVerification:
    """Structural/completeness verdict for one captured HAR."""

    ok: bool
    har_path: str
    entries: int
    problems: tuple
    missing_urls: tuple
    matched_urls: tuple


@dataclass(frozen=True)
class VerifiedCaptureResult:
    """Result of a capture that was waited for, verified and retried if needed."""

    url: str
    har_path: Path
    ok: bool
    attempts: int
    entries: int
    waited_ms: int
    idle_reached: bool
    title: str
    status: Optional[int]
    verification: CaptureVerification
    attempt_log: tuple  # one dict per attempt (observability / root cause)


class NetworkActivityTracker:
    """Track in-flight requests / activity for a page and wait for settle.

    The tracker is attached to a Playwright page and observes
    ``request`` / ``requestfinished`` / ``requestfailed`` events.
    """

    def __init__(self, page) -> None:
        self._page = page
        self._pending = 0
        self._last_activity = time.monotonic()
        self._urls: dict[str, int] = {}
        self._order: list[str] = []  # every request URL, in dispatch order
        self._total = 0  # total requests dispatched
        self._status: dict[str, int] = {}  # last response status per URL
        self._method: dict[str, str] = {}  # HTTP method per URL
        self._failed: set[str] = set()  # URLs that failed/aborted
        self._attached = False

    # -- lifecycle -----------------------------------------------------
    def attach(self) -> "NetworkActivityTracker":
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self._page.on("requestfinished", self._on_request_done)
        self._page.on("requestfailed", self._on_request_failed)
        self._last_activity = time.monotonic()
        self._attached = True
        return self

    def detach(self) -> None:
        if not self._attached:
            return
        for event, handler in (
            ("request", self._on_request),
            ("response", self._on_response),
            ("requestfinished", self._on_request_done),
            ("requestfailed", self._on_request_failed),
        ):
            try:
                self._page.remove_listener(event, handler)
            except Exception:  # noqa: BLE001 - page may already be closed
                pass
        self._attached = False

    # -- observation ---------------------------------------------------
    def _on_request(self, request) -> None:
        self._pending += 1
        self._total += 1
        url = str(request.url)
        self._urls[url] = self._urls.get(url, 0) + 1
        self._order.append(url)
        try:
            self._method[url] = str(request.method)
        except Exception:  # noqa: BLE001
            pass
        self._last_activity = time.monotonic()

    def _on_response(self, response) -> None:
        try:
            self._status[str(response.url)] = response.status
        except Exception:  # noqa: BLE001 - response already detached
            pass
        self._last_activity = time.monotonic()

    def _on_request_done(self, request) -> None:
        self._pending = max(0, self._pending - 1)
        self._last_activity = time.monotonic()

    def _on_request_failed(self, request) -> None:
        self._failed.add(str(request.url))
        self._pending = max(0, self._pending - 1)
        self._last_activity = time.monotonic()

    # -- inspectors ----------------------------------------------------
    @property
    def pending(self) -> int:
        return self._pending

    @property
    def urls(self) -> tuple:
        return tuple(self._urls)

    @property
    def total_requests(self) -> int:
        return self._total

    def mark(self) -> int:
        """Capture the current dispatch position (index) for later slicing."""
        return self._total

    def urls_since(self, mark: int) -> list:
        """All request URLs dispatched since ``mark`` (dispatch order)."""
        return list(self._order[mark:])

    def status_of(self, url: str) -> Optional[int]:
        return self._status.get(url)

    def method_of(self, url: str) -> str:
        return self._method.get(url, "GET")

    def failed_urls(self) -> set:
        return set(self._failed)

    def seen(self, needle: str) -> bool:
        """True when any observed URL contains ``needle``."""
        return any(needle in url for url in self._urls)

    def wait_for_idle(
        self,
        *,
        quiet_ms: int = DEFAULT_QUIET_MS,
        max_wait_ms: int = DEFAULT_MAX_WAIT_MS,
        expect_urls: Sequence[str] = (),
        poll_ms: int = DEFAULT_POLL_MS,
    ) -> IdleWaitResult:
        """Block until the network has settled or ``max_wait_ms`` elapsed.

        Settled means all three hold: no request in flight, no new network
        activity for ``quiet_ms``, and every URL in ``expect_urls`` observed.
        """
        start = time.monotonic()
        while True:
            now = time.monotonic()
            elapsed_ms = int((now - start) * 1000)
            quiet_for_ms = int((now - self._last_activity) * 1000)
            missing = tuple(u for u in expect_urls if not self.seen(u))

            if self._pending == 0 and quiet_for_ms >= quiet_ms and not missing:
                return IdleWaitResult(True, elapsed_ms, 0, (), quiet_ms, max_wait_ms)
            if elapsed_ms >= max_wait_ms:
                return IdleWaitResult(
                    False, elapsed_ms, self._pending, missing, quiet_ms, max_wait_ms
                )
            try:
                # Playwright's sync API only dispatches page events while a
                # Playwright call is running - so wait through the page itself
                # instead of time.sleep(), otherwise our handlers never fire.
                self._page.wait_for_timeout(poll_ms)
            except Exception:  # noqa: BLE001 - page/context already closed
                time.sleep(poll_ms / 1000.0)


def verify_capture(
    har_path: str | Path,
    *,
    min_entries: int = 1,
    expect_urls: Sequence[str] = (),
    require_finished_responses: bool = True,
) -> CaptureVerification:
    """Check that a written HAR capture is complete.

    A capture is *incomplete* (premature/empty) when it has fewer entries than
    ``min_entries``, when an expected request is absent, or when an expected
    request's response has an error/unfinished status or an empty body.
    """
    path = Path(har_path)
    missing: tuple = tuple(expect_urls)
    if not path.exists():
        return CaptureVerification(
            False, str(path), 0, (f"HAR file does not exist: {path}",), missing, ()
        )
    try:
        har = load_har(path)
    except Exception as exc:  # noqa: BLE001
        return CaptureVerification(
            False, str(path), 0, (f"HAR file unreadable: {exc}",), missing, ()
        )

    entries = (har.get("log") or {}).get("entries") or []
    problems: list[str] = []
    if len(entries) < min_entries:
        problems.append(
            f"empty/premature capture: {len(entries)} entries < min_entries {min_entries}"
        )

    matched: list[str] = []
    missed: list[str] = []
    for needle in expect_urls:
        found = None
        for entry in entries:
            req = entry.get("request") or {}
            url = req.get("url")
            if isinstance(url, str) and needle in url:
                found = entry
                break
        if found is None:
            missed.append(needle)
            continue
        matched.append(needle)
        if not require_finished_responses:
            continue
        res = found.get("response") or {}
        status = res.get("status")
        if not isinstance(status, int) or not (200 <= status < 400):
            problems.append(
                f"expected request '{needle}' has unfinished/failed status {status!r} "
                f"(premature capture)"
            )
        content = res.get("content") or {}
        text = content.get("text")
        size = content.get("size")
        has_body = bool(text) or (isinstance(size, int) and size > 0)
        if not has_body:
            problems.append(
                f"expected request '{needle}' has an empty response body (premature capture)"
            )

    if missed:
        problems.append(f"expected requests missing from capture: {missed}")

    return CaptureVerification(
        ok=not problems,
        har_path=str(path),
        entries=len(entries),
        problems=tuple(problems),
        missing_urls=tuple(missed),
        matched_urls=tuple(matched),
    )


def capture_verified(
    engine: BrowserEngine,
    url: str,
    har_path: str | Path,
    *,
    min_entries: int = 1,
    expect_urls: Sequence[str] = (),
    max_attempts: int = 3,
    quiet_ms: int = DEFAULT_QUIET_MS,
    max_wait_ms: int = DEFAULT_MAX_WAIT_MS,
    poll_ms: int = DEFAULT_POLL_MS,
    wait_until: str = "load",
) -> VerifiedCaptureResult:
    """Capture one page load into ``har_path`` and make sure it is complete.

    Each attempt uses a fresh context with ``record_har_path``, waits for the
    network to *settle* (see :meth:`NetworkActivityTracker.wait_for_idle`),
    then verifies the written HAR. If verification fails the capture is retried
    (up to ``max_attempts``) so the result is never a premature/empty HAR.
    """
    logger = logging.getLogger("agenttrace.verify")
    har_path = Path(har_path)
    har_path.parent.mkdir(parents=True, exist_ok=True)

    attempt_log: list[dict] = []
    verification: Optional[CaptureVerification] = None
    idle: Optional[IdleWaitResult] = None
    title = ""
    status: Optional[int] = None
    attempts = 0

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        if har_path.exists():
            har_path.unlink()

        context = engine.new_capture_context(record_har_path=str(har_path))
        page = context.new_page()
        page.set_default_navigation_timeout(engine.navigation_timeout_ms)
        tracker = NetworkActivityTracker(page).attach()

        try:
            snap = engine._navigate(page, url, wait_until, network_idle=False)
            status = snap.status_code
            idle = tracker.wait_for_idle(
                quiet_ms=quiet_ms,
                max_wait_ms=max_wait_ms,
                expect_urls=expect_urls,
                poll_ms=poll_ms,
            )
            try:
                title = page.title()
            except Exception:  # noqa: BLE001
                title = snap.title
        finally:
            tracker.detach()
            try:
                context.close()  # flushes the HAR file
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing capture context: %s", exc)

        entries = count_har_entries(load_har(har_path))
        verification = verify_capture(
            har_path, min_entries=min_entries, expect_urls=expect_urls
        )
        attempt_log.append(
            {
                "attempt": attempt,
                "entries": entries,
                "idle_reached": idle.idle,
                "waited_ms": idle.waited_ms,
                "pending_at_stop": idle.pending,
                "missing_urls": list(idle.missing_urls),
                "ok": verification.ok,
                "problems": list(verification.problems),
            }
        )
        logger.info(
            "capture_verified attempt %d/%d | entries=%d idle=%s waited=%dms ok=%s",
            attempt,
            max_attempts,
            entries,
            idle.idle,
            idle.waited_ms,
            verification.ok,
        )
        if verification.ok:
            break
        logger.warning(
            "attempt %d did not verify (will retry): %s",
            attempt,
            list(verification.problems),
        )

    return VerifiedCaptureResult(
        url=url,
        har_path=har_path,
        ok=bool(verification and verification.ok),
        attempts=attempts,
        entries=verification.entries if verification else 0,
        waited_ms=idle.waited_ms if idle else 0,
        idle_reached=bool(idle and idle.idle),
        title=title,
        status=status,
        verification=verification,
        attempt_log=tuple(attempt_log),
    )