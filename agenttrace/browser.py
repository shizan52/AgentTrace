"""Core browser automation engine (Part 1).

Provides a thin, reliable wrapper around Playwright's synchronous API:

- launches Chromium/Firefox/WebKit (headless by default)
- navigates a page and waits until it is loaded
- waits a bounded amount for the network to become idle (graceful fallback
  for pages that never fully idle, e.g. streaming/analytics-heavy sites)
- returns the final URL and title after navigation (post-redirect)
- raises ``BrowserError`` with the original context so failures can be
  diagnosed from logs alone
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .auth import AuthStateInfo, inspect_auth_state, save_auth_state, storage_state_arg
from .har import HarCaptureResult, count_har_entries, load_har
from .stealth import (
    STEALTH_INIT_SCRIPT,
    stealth_context_options,
    stealth_launch_args,
    stealth_user_agent,
)

_BROWSER_TYPES = ("chromium", "firefox", "webkit")


def _describe_auth(path: Path) -> str:
    """Log-safe one-liner about an auth state file (never cookie values)."""
    try:
        info = inspect_auth_state(path)
    except Exception:  # noqa: BLE001
        return f"{path} (unreadable)"
    return (
        f"{path.name} cookies={info.cookies} origins={info.origins} "
        f"names={list(info.cookie_names)}"
    )


class BrowserError(RuntimeError):
    """Raised when a browser lifecycle or navigation operation fails."""


@dataclass(frozen=True)
class PageSnapshot:
    """The observable page state right after a navigation/load wait."""

    url: str
    title: str
    status_code: Optional[int]


class BrowserEngine:
    """Stateful browser session with ``start()``/``close()`` lifecycle.

    Use as a plain object::

        engine = BrowserEngine(headless=True)
        snap = engine.start().open("https://example.com")
        print(snap.title, snap.url)
        engine.close()

    or as a context manager::

        with BrowserEngine() as engine:
            snap = engine.open("https://example.com")
    """

    def __init__(
        self,
        *,
        browser_type: str = "chromium",
        headless: bool = True,
        executable_path: Optional[str] = None,
        navigation_timeout_ms: int = 60_000,
        network_idle_timeout_ms: int = 10_000,
        auth_state: Optional[str | Path] = None,
        stealth: bool = False,
    ) -> None:
        if browser_type not in _BROWSER_TYPES:
            raise ValueError(
                f"browser_type must be one of {_BROWSER_TYPES}, got {browser_type!r}"
            )
        if navigation_timeout_ms <= 0:
            raise ValueError("navigation_timeout_ms must be > 0")

        self.browser_type = browser_type
        self.headless = headless
        self.executable_path = executable_path
        self.navigation_timeout_ms = navigation_timeout_ms
        self.network_idle_timeout_ms = network_idle_timeout_ms
        # Part 8: a saved storage-state file with cookies/tokens to reuse.
        self.auth_state_path: Optional[Path] = Path(auth_state) if auth_state else None
        # Part 9: apply the anti-detection layer to every context.
        self.stealth = stealth

        self._pw = None  # playwright runtime
        self._browser = None
        self._context = None
        self._page = None

        self._logger = logging.getLogger("agenttrace.browser")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> "BrowserEngine":
        """Launch the configured browser and open a fresh page/context."""
        if self._browser is not None:
            raise BrowserError("Browser is already started; call close() first.")

        try:
            from playwright.sync_api import (
                sync_playwright,
                Error as PwError,
                TimeoutError as PwTimeoutError,
            )
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BrowserError(
                "Playwright is not installed. Run: pip install playwright "
                "and then: python -m playwright install chromium"
            ) from exc

        self._logger.info("Starting Playwright runtime ...")
        self._pw = sync_playwright().start()

        browser_factory = getattr(self._pw, self.browser_type, None)
        if browser_factory is None:
            self._pw.stop()
            self._pw = None
            raise BrowserError(f"Unsupported browser type: {self.browser_type}")

        launch_kwargs = {"headless": self.headless}
        if self.executable_path:
            launch_kwargs["executable_path"] = self.executable_path
        if self.stealth:
            launch_kwargs["args"] = stealth_launch_args()
            self._logger.info(
                "Stealth layer enabled (%d launch flags + init script)",
                len(launch_kwargs["args"]),
            )

        self._logger.info(
            "Launching %s browser (headless=%s) ...",
            self.browser_type,
            self.headless,
        )
        try:
            self._browser = browser_factory.launch(**launch_kwargs)
        except Exception as exc:
            # If the exact bundled Chromium revision is missing but another
            # local Playwright Chromium build exists, fall back to it.
            if self.browser_type == "chromium" and not self.executable_path:
                found = self._discover_chromium_binary()
                if found:
                    self._logger.warning(
                        "Bundled chromium revision unavailable; using local build: %s",
                        found,
                    )
                    launch_kwargs["executable_path"] = found
                    try:
                        self._browser = browser_factory.launch(**launch_kwargs)
                    except Exception as exc2:
                        self._pw.stop()
                        self._pw = None
                        raise BrowserError(
                            f"Failed to launch chromium browser (also tried {found}). "
                            f"Details: {exc2}"
                        ) from exc2
                else:
                    self._pw.stop()
                    self._pw = None
                    raise BrowserError(
                        f"Failed to launch {self.browser_type} browser. "
                        f"Make sure the browser binary is installed with "
                        f"'python -m playwright install {self.browser_type}'. Details: {exc}"
                    ) from exc
            else:
                self._pw.stop()
                self._pw = None
                raise BrowserError(
                    f"Failed to launch {self.browser_type} browser. "
                    f"Make sure the browser binary is installed with "
                    f"'python -m playwright install {self.browser_type}'. Details: {exc}"
                ) from exc

        self._context = self.new_capture_context()
        self._page = self._context.new_page()
        self._page.set_default_navigation_timeout(self.navigation_timeout_ms)
        if self.auth_state_path:
            self._logger.info(
                "Auth state: %s", _describe_auth(self.auth_state_path)
            )
        self._logger.info(
            "Browser launched and page context ready (timeout=%sms).",
            self.navigation_timeout_ms,
        )
        return self

    def close(self) -> None:
        """Stop the browser and release all resources (idempotent)."""
        failures = []
        for closer in (
            self._context.close if self._context else None,
            self._browser.close if self._browser else None,
            self._pw.stop if self._pw else None,
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception as exc:  # noqa: BLE001 - collect and report
                failures.append(f"{type(exc).__name__}: {exc}")

        self._context = None
        self._browser = None
        self._page = None
        self._pw = None

        if failures:
            raise BrowserError("Errors while closing: " + "; ".join(failures))
        self._logger.info("Browser closed.")

    # ------------------------------------------------------------------
    # Context factory (Part 8 auth + Part 9 stealth applied centrally)
    # ------------------------------------------------------------------
    def _context_options(self) -> dict:
        """Options shared by the session context and every capture context."""
        if self.stealth:
            version = None
            try:
                version = self._browser.version if self._browser else None
            except Exception:  # noqa: BLE001
                version = None
            options = stealth_context_options(stealth_user_agent(version))
        else:
            options = {"viewport": {"width": 1280, "height": 800}, "locale": "en-US"}

        state = storage_state_arg(self.auth_state_path)
        if state:
            options["storage_state"] = state
        return options

    def _apply_stealth(self, context) -> None:
        if self.stealth:
            try:
                context.add_init_script(STEALTH_INIT_SCRIPT)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Could not install stealth init script: %s", exc)

    def new_capture_context(self, **overrides):
        """Create a context that inherits this engine's auth state + stealth.

        Every capture path (``open_recording``, ``capture_verified``,
        ``capture_click``, ``AgentSession``) goes through here, so a logged-in
        profile and the anti-detection layer apply everywhere automatically.
        """
        if self._browser is None:
            raise BrowserError("Browser not started; call start() first.")
        options = self._context_options()
        options.update(overrides)
        context = self._browser.new_context(**options)
        self._apply_stealth(context)
        return context

    # ------------------------------------------------------------------
    # Auth / session state (Part 8)
    # ------------------------------------------------------------------
    def storage_state(self, path: Optional[str | Path] = None) -> dict:
        """Return the session context's storage state (cookies + origins).

        When ``path`` is given the state is also written to that file.
        """
        if self._context is None:
            raise BrowserError("Browser not started; call start() first.")
        if path is not None:
            return self._context.storage_state(path=str(Path(path)))
        return self._context.storage_state()

    def save_auth_state(
        self,
        path: Optional[str | Path] = None,
        *,
        profile: str = "default",
    ) -> AuthStateInfo:
        """Persist the current login state so it can be reused later.

        Defaults to this engine's ``auth_state`` path; the browser must still be
        running (the session context holds the live cookies).
        """
        if self._context is None:
            raise BrowserError("Browser not started; call start() first.")
        target = Path(path) if path else self.auth_state_path
        if target is None:
            raise BrowserError(
                "No auth state path configured; pass one to save_auth_state(path=...) "
                "or construct the engine with auth_state=..."
            )
        info = save_auth_state(self._context, target, profile=profile)
        self.auth_state_path = Path(target)
        self._logger.info("Saved auth state: %s", info.to_dict())
        return info

    def release_default_context(self) -> None:
        """Close the engine's default context/page.

        Used by callers that manage their own context (e.g. ``AgentSession``),
        so the browser is not left holding an unused blank page.
        """
        for closer in (
            self._context.close if self._context else None,
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Error closing default context: %s", exc)
        self._context = None
        self._page = None

    def auth_state_info(self) -> Optional[AuthStateInfo]:
        """Describe the configured auth state file (``None`` when not set)."""
        if not self.auth_state_path:
            return None
        try:
            return inspect_auth_state(self.auth_state_path)
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _discover_chromium_binary(self) -> Optional[str]:
        """Return a locally installed Playwright Chromium binary path.

        Scans ``%LOCALAPPDATA%/ms-playwright`` (or ``PLAYWRIGHT_BROWSERS_PATH``)
        for ``chromium-<rev>/chrome-win64/chrome.exe`` and returns the newest
        revision, or ``None`` when nothing usable is found.
        """
        import os

        base_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        base = (
            Path(base_env)
            if base_env
            else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
        )
        candidates: list[tuple[int, Path]] = []
        if base.is_dir():
            for exe in base.glob("chromium-*/chrome-win64/chrome.exe"):
                try:
                    rev = int(exe.parent.parent.name.split("-", 1)[1])
                except (IndexError, ValueError):
                    continue
                candidates.append((rev, exe))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return str(candidates[0][1])

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def open(self, url: str, *, wait_until: str = "load") -> PageSnapshot:
        """Navigate the session page to ``url`` and wait until it is loaded.

        Uses ``wait_until="load"`` as the primary signal (fires after the
        document and its sub-resources are loaded), then waits a bounded
        amount for ``network_idle`` and treats a timeout there as a warning,
        not a failure — some sites keep long-lived connections open.

        Returns a :class:`PageSnapshot` built from the page *after* any
        redirects, so both ``url`` (final) and ``title`` reflect reality.
        """
        if self._browser is None or self._page is None:
            raise BrowserError("Browser not started; call start() first.")
        return self._navigate(self._page, url, wait_until)

    def open_recording(
        self,
        url: str,
        *,
        har_path: str | Path,
        wait_until: str = "load",
    ) -> "HarCaptureResult":
        """Navigate a freshly created context to ``url`` and record a HAR.

        Each call produces one page-load HAR: a dedicated browser context is
        created with Playwright's ``record_har_path`` so the HAR file contains
        exactly the requests of this single page load. A ``page.on("request")``
        counter independently counts dispatched requests; after the context is
        closed (which flushes the HAR) the two numbers are compared.

        Returns a :class:`HarCaptureResult` with the page snapshot and the
        request/entry counts (``matched=True`` means the HAR entry count equals
        the number of requests the browser dispatched — i.e. what the DevTools
        Network tab would show).
        """
        if self._browser is None:
            raise BrowserError("Browser not started; call start() first.")

        har_path = Path(har_path)
        har_path.parent.mkdir(parents=True, exist_ok=True)
        self._logger.info(
            "open_recording -> %s | harness=%s", url, har_path
        )

        capture_context = self.new_capture_context(record_har_path=str(har_path))
        page = capture_context.new_page()
        page.set_default_navigation_timeout(self.navigation_timeout_ms)

        request_events: list = []
        page.on("request", lambda req: request_events.append(req.url))

        try:
            snap = self._navigate(page, url, wait_until)
        finally:
            # Closing the context flushes the HAR file to disk.
            try:
                capture_context.close()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Error while closing capture context: %s", exc)

        har = load_har(har_path)
        har_entries = count_har_entries(har)
        matched = har_entries == len(request_events)
        self._logger.info(
            "HAR written: %s | request_events=%d har_entries=%d matched=%s",
            har_path,
            len(request_events),
            har_entries,
            matched,
        )
        return HarCaptureResult(
            snapshot_url=snap.url,
            snapshot_title=snap.title,
            snapshot_status=snap.status_code,
            har_path=har_path,
            request_events=len(request_events),
            request_urls=tuple(request_events),
            har_entries=har_entries,
            matched=matched,
        )

    def _navigate(
        self, page, url: str, wait_until: str, *, network_idle: bool = True
    ) -> PageSnapshot:
        """Shared navigation routine used by ``open`` and ``open_recording``.

        ``network_idle=False`` skips the bounded ``networkidle`` wait so that a
        caller (e.g. the Part 4 reliability layer) can implement its own,
        stricter idle detection.
        """
        self._logger.info("Navigating to: %s (wait_until=%s)", url, wait_until)
        try:
            response = page.goto(
                url,
                wait_until=wait_until,
                timeout=self.navigation_timeout_ms,
            )
        except Exception as exc:
            raise BrowserError(f"Failed to navigate to {url}: {exc}") from exc

        if network_idle and self.network_idle_timeout_ms > 0:
            try:
                page.wait_for_load_state(
                    "networkidle", timeout=self.network_idle_timeout_ms
                )
                self._logger.info("Network idle reached after load for %s", url)
            except Exception:
                # Not a hard failure: the page simply never went fully idle.
                self._logger.warning(
                    "Network did not reach idle within %sms for %s "
                    "(common for streaming/analytics-heavy pages); continuing.",
                    self.network_idle_timeout_ms,
                    url,
                )

        status = response.status if response is not None else None
        try:
            title = page.title()
        except Exception as exc:
            raise BrowserError(
                f"Navigation succeeded but title could not be read for {url}: {exc}"
            ) from exc

        final_url = page.url
        self._logger.info(
            "Page loaded | status=%s | final_url=%s | title=%r",
            status,
            final_url,
            title,
        )
        return PageSnapshot(url=final_url, title=title, status_code=status)

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------
    def current_snapshot(self) -> PageSnapshot:
        """Return the current page URL/title without any waiting."""
        if self._page is None:
            raise BrowserError("Browser not started; call start() first.")
        return PageSnapshot(
            url=self._page.url,
            title=self._page.title(),
            status_code=None,
        )

    @property
    def page(self):
        """The underlying Playwright page (for advanced use in later parts)."""
        if self._page is None:
            raise BrowserError("Browser not started; call start() first.")
        return self._page

    @property
    def browser(self):
        """The underlying Playwright browser (needed for context-level capture)."""
        if self._browser is None:
            raise BrowserError("Browser not started; call start() first.")
        return self._browser

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------
    def __enter__(self) -> "BrowserEngine":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()