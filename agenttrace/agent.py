"""AI Agent Action Interface (Part 6).

A high-level, *selector-free* API that an AI agent (Claude/Cline/…) can drive
from a plain instruction such as:

    "open the shop, go to the first product page, click Add to cart"

Everything the agent needs is exposed as JSON-serialisable functions:

- ``AgentSession.goto(url)``            — navigate (verified network capture)
- ``AgentSession.observe()``            — list interactive elements with refs
- ``AgentSession.find(target)``         — resolve natural language → element
- ``AgentSession.click(target)``        — click by ref *or* natural language
- ``AgentSession.capture_snapshot()``   — current page state (URL/title/elements)
- ``AgentSession.capture_page(url)``    — one-shot verified HAR (returns path now)
- ``AgentSession.finish()``             — close and emit per-action HAR files

``AGENT_TOOLS`` + ``dispatch_tool()`` describe/dispatch the same functions as
JSON-schema tools, so an LLM (or the Part 7 MCP server) can call them directly
without writing any code or CSS selectors.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .browser import BrowserEngine, BrowserError
from .auth import save_auth_state
from .har import (
    har_with_entries,
    load_har,
    save_har,
    validate_har_file,
)
from .verification import NetworkActivityTracker, capture_verified

_ELEMENT_COLLECT_JS = r"""
() => {
  const selector = 'a, button, input, select, textarea, [role="button"], [role="link"], [onclick]';
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(selector)) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
    if (!visible) continue;
    i += 1;
    const ref = 'e' + i;
    el.setAttribute('data-agenttrace-ref', ref);
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    const name = el.getAttribute('aria-label') || el.getAttribute('title') ||
      el.getAttribute('placeholder') || el.getAttribute('alt') || el.value || '';
    out.push({
      ref: ref,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      text: text.slice(0, 120),
      name: String(name).slice(0, 120),
      href: el.getAttribute('href') || '',
    });
  }
  return out;
}
"""


@dataclass(frozen=True)
class ElementInfo:
    """One interactive element discovered by :meth:`AgentSession.observe`."""

    ref: str  # stable handle the agent passes back to click(), e.g. "e3"
    tag: str
    role: str
    text: str
    name: str
    href: str

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "tag": self.tag,
            "role": self.role,
            "text": self.text,
            "name": self.name,
            "href": self.href,
        }


@dataclass(frozen=True)
class PageObservation:
    """What the agent can currently 'see' on the page."""

    url: str
    title: str
    text_excerpt: str
    elements: tuple

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "text_excerpt": self.text_excerpt,
            "elements": [e.to_dict() for e in self.elements],
        }


@dataclass(frozen=True)
class RequestInfo:
    """A single request dispatched during an action."""

    url: str
    method: str
    status: Optional[int]
    ok: bool

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "method": self.method,
            "status": self.status,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class ActionResult:
    """Outcome of one agent action."""

    action: str
    ok: bool
    detail: str
    url: str
    title: str
    status: Optional[int]
    requests: tuple
    new_urls: tuple
    idle_reached: bool
    waited_ms: int
    action_index: int
    observation: Optional[PageObservation] = None
    har_path: Optional[Path] = None

    def to_dict(self) -> dict:
        out = {
            "action": self.action,
            "action_index": self.action_index,
            "ok": self.ok,
            "detail": self.detail,
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "idle_reached": self.idle_reached,
            "waited_ms": self.waited_ms,
            "requests": [r.to_dict() for r in self.requests],
            "new_urls": list(self.new_urls),
            "har_path": str(self.har_path) if self.har_path else None,
        }
        if self.observation is not None:
            out["observation"] = self.observation.to_dict()
        return out


@dataclass(frozen=True)
class SessionSummary:
    """Result of :meth:`AgentSession.finish`."""

    session_dir: Path
    actions: tuple
    har_files: dict  # action label -> HAR path
    session_har: Optional[Path]
    summary_path: Optional[Path]
    duration_ms: int

    def to_dict(self) -> dict:
        return {
            "session_dir": str(self.session_dir),
            "actions": [_as_dict(a) for a in self.actions],
            "har_files": {k: str(v) for k, v in self.har_files.items()},
            "session_har": str(self.session_har) if self.session_har else None,
            "summary_path": str(self.summary_path) if self.summary_path else None,
            "duration_ms": self.duration_ms,
        }


def _as_dict(value) -> dict:
    """Serialise a result object or a plain dict into a JSON-ready dict."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in value.items()}
    return {"value": value}


_REF_RE = re.compile(r"^e\d+$")


def _norm(value: str) -> str:
    """Lower-case, collapse whitespace and drop most punctuation."""
    text = (value or "").lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_element(intent: str, element: ElementInfo) -> int:
    """Score how well ``element`` matches a natural-language ``intent``.

    Deterministic and dependency-free: exact match > substring > all tokens
    present > partial token overlap.
    """
    query = _norm(intent)
    if not query:
        return 0
    texts = [_norm(element.text), _norm(element.name)]
    href = _norm(element.href)

    if query in texts:
        return 100
    for hay in texts:
        if hay and (query in hay or hay in query):
            return 85
    tokens = query.split()
    for hay in texts:
        if hay and all(token in hay for token in tokens):
            return 70
    best = 0
    for hay in texts + [href]:
        if not hay:
            continue
        hay_tokens = set(hay.split())
        overlap = len(hay_tokens & set(tokens)) / max(1, len(set(tokens)))
        best = max(best, int(50 * overlap))
    return best


MATCH_THRESHOLD = 40


class AgentSession:
    """Selector-free browser session designed to be driven by an AI agent.

    Example::

        session = AgentSession(out_dir="artifacts", headless=True)
        session.goto("https://example.com")
        print(session.observe().to_dict())
        session.click("More information")      # natural-language target
        summary = session.finish()
        print(summary.har_files)

    Every action returns JSON-serialisable objects so the agent can pass the
    results straight back into its own reasoning loop.
    """

    def __init__(
        self,
        *,
        out_dir: str | Path = "artifacts",
        headless: bool = True,
        browser_type: str = "chromium",
        click_timeout_ms: int = 15_000,
        quiet_ms: int = 500,
        max_wait_ms: int = 20_000,
        auth_state: Optional[str | Path] = None,
        stealth: bool = False,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.headless = headless
        self.browser_type = browser_type
        self.click_timeout_ms = click_timeout_ms
        self.quiet_ms = quiet_ms
        self.max_wait_ms = max_wait_ms
        self.auth_state = auth_state
        self.stealth = stealth

        self._logger = logging.getLogger("agenttrace.agent")
        self._engine: Optional[BrowserEngine] = None
        self._context = None
        self._page = None
        self._tracker: Optional[NetworkActivityTracker] = None
        self._session_har: Optional[Path] = None

        self._elements: tuple = ()
        self._selectors: dict = {}  # ref -> CSS selector
        self._observed_refs: dict = {}  # ref -> (tag, text) as seen by observe()
        self._actions: list = []  # per-action boundary records
        self._started_at: Optional[float] = None
        self._finished = False

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def start(self) -> "AgentSession":
        """Launch the browser and open the long-lived session page."""
        if self._page is not None or self._finished:
            return self
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._session_har = self.out_dir / "session.har"

        self._engine = BrowserEngine(
            browser_type=self.browser_type,
            headless=self.headless,
            auth_state=self.auth_state,
            stealth=self.stealth,
        )
        self._engine.start()
        # The agent session records into its own context; release the engine's
        # default (blank) context so we don't hold an unused page.
        self._engine.release_default_context()

        self._context = self._engine.new_capture_context(
            record_har_path=str(self._session_har)
        )
        self._page = self._context.new_page()
        self._page.set_default_navigation_timeout(self._engine.navigation_timeout_ms)
        self._tracker = NetworkActivityTracker(self._page).attach()
        self._started_at = time.monotonic()
        self._logger.info("AgentSession started (out_dir=%s)", self.out_dir)
        return self

    def _require_page(self):
        if self._finished:
            raise BrowserError("AgentSession is already finished.")
        if self._page is None:
            self.start()
        return self._page

    # ------------------------------------------------------------------
    # Internals: settling, observing, resolving
    # ------------------------------------------------------------------
    def _settle(self, expect_urls=()) -> "object":
        """Wait for the network to settle (Part 4 reliability layer)."""
        return self._tracker.wait_for_idle(
            quiet_ms=self.quiet_ms,
            max_wait_ms=self.max_wait_ms,
            expect_urls=expect_urls,
        )

    def _observe_elements(self) -> tuple:
        """Run the element-collection script and cache refs/selectors."""
        page = self._require_page()
        raw = page.evaluate(_ELEMENT_COLLECT_JS) or []
        elements = []
        selectors: dict = {}
        for item in raw:
            if not isinstance(item, dict) or not item.get("ref"):
                continue
            info = ElementInfo(
                ref=str(item.get("ref")),
                tag=str(item.get("tag") or ""),
                role=str(item.get("role") or ""),
                text=str(item.get("text") or ""),
                name=str(item.get("name") or ""),
                href=str(item.get("href") or ""),
            )
            elements.append(info)
            selectors[info.ref] = f'[data-agenttrace-ref="{info.ref}"]'
        self._elements = tuple(elements)
        self._selectors = selectors
        return self._elements

    def observe(self) -> PageObservation:
        """Return the current page: URL, title, text excerpt and elements."""
        page = self._require_page()
        elements = self._observe_elements()
        # Remember exactly what this observation showed so a later click(ref)
        # can detect a stale handle (e.g. after a navigation).
        self._observed_refs = {e.ref: (e.tag, e.text, e.name) for e in elements}
        try:
            title = page.title()
        except Exception:  # noqa: BLE001
            title = ""
        try:
            excerpt = page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 500) : ''"
            )
        except Exception:  # noqa: BLE001
            excerpt = ""
        return PageObservation(
            url=page.url,
            title=title or "",
            text_excerpt=str(excerpt or ""),
            elements=elements,
        )

    def find(self, target: str) -> ElementInfo:
        """Resolve a ref (``"e3"``) or a natural-language target to an element.

        Raises :class:`BrowserError` with the ranked candidates when nothing
        matches well enough, so the agent (or a human reading the log) can see
        what *was* available.
        """
        if not isinstance(target, str) or not target.strip():
            raise BrowserError("find() needs a non-empty target string.")
        target = target.strip()

        # 1) explicit ref from a previous observe()
        if _REF_RE.match(target):
            if target not in self._observed_refs:
                raise BrowserError(
                    f"Unknown element ref {target!r}. Call observe() first and use one "
                    f"of the returned refs."
                )
            elements = self._observe_elements()
            fresh = {e.ref: (e.tag, e.text, e.name) for e in elements}
            if target not in fresh:
                raise BrowserError(
                    f"Element ref {target!r} is no longer on the page "
                    f"(it disappeared). Call observe() again."
                )
            if fresh[target] != self._observed_refs[target]:
                raise BrowserError(
                    f"Stale element ref {target!r}: the page changed since observe() "
                    f"({self._observed_refs[target]!r} -> {fresh[target]!r}). "
                    f"Call observe() again."
                )
            for element in elements:
                if element.ref == target:
                    self._logger.info("find(%r) -> ref %s", target, target)
                    return element
            raise BrowserError(f"Could not resolve ref {target!r}.")

        # 2) natural language -> best scoring element
        elements = self._observe_elements()
        scored = sorted(
            ((score_element(target, e), e) for e in elements),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = (scored[0] if scored else (0, None))
        if best is None or best_score < MATCH_THRESHOLD:
            top = [f"{e.ref}:{e.text or e.name!r}" for _s, e in scored[:8]]
            raise BrowserError(
                f"No element matched {target!r} (best score {best_score}). "
                f"Candidates: {top}"
            )
        return best

    def _resolve_selector(self, target: str) -> tuple:
        """Return ``(selector, element)`` for a ref or natural-language target."""
        element = self.find(target)
        selector = self._selectors.get(element.ref)
        if selector is None:
            self._observe_elements()
            selector = self._selectors.get(element.ref)
        if selector is None:
            raise BrowserError(f"Could not build a selector for ref {element.ref!r}.")
        return selector, element

    def _record(self, action: str, detail: str, mark_start: int, mark_end: int,
                extra: Optional[dict] = None) -> None:
        record = {
            "index": len(self._actions) + 1,
            "action": action,
            "detail": detail,
            "mark_start": mark_start,
            "mark_end": mark_end,
        }
        if extra:
            record.update(extra)
        self._actions.append(record)

    # ------------------------------------------------------------------
    # Public actions (what the AI agent calls)
    # ------------------------------------------------------------------
    def _requests_between(self, mark_start: int, mark_end: int) -> tuple:
        """Requests dispatched between two dispatch marks (deduped)."""
        span = max(0, mark_end - mark_start)
        urls = self._tracker.urls_since(mark_start)[:span]
        out = []
        seen = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            status = self._tracker.status_of(url)
            ok = url not in self._tracker.failed_urls() and (
                status is None or 200 <= status < 400
            )
            out.append(
                RequestInfo(
                    url=url,
                    method=self._tracker.method_of(url),
                    status=status,
                    ok=ok,
                )
            )
        return tuple(out)

    def _base_result(self, action: str, ok: bool, detail: str, index: int) -> dict:
        page = self._page
        return {
            "action": action,
            "ok": ok,
            "detail": detail,
            "url": page.url if page else "",
            "title": "",
            "status": None,
            "requests": (),
            "new_urls": (),
            "idle_reached": False,
            "waited_ms": 0,
            "action_index": index,
        }

    def goto(self, url: str, *, wait_until: str = "load") -> ActionResult:
        """Navigate the session page and wait for the network to settle."""
        page = self._require_page()
        index = len(self._actions) + 1
        mark_start = self._tracker.mark()
        self._logger.info("action %d goto %s", index, url)
        try:
            snapshot = self._engine._navigate(page, url, wait_until, network_idle=False)
        except BrowserError as exc:
            self._record("goto", url, mark_start, mark_start, {"in_session_har": False})
            return ActionResult(**self._base_result("goto", False, str(exc), index))

        idle = self._settle()
        mark_end = self._tracker.mark()
        requests = self._requests_between(mark_start, mark_end)
        self._record("goto", url, mark_start, mark_end, {"url": url})
        self._logger.info(
            "action %d goto done | status=%s title=%r requests=%d idle=%s",
            index, snapshot.status_code, snapshot.title, len(requests), idle.idle,
        )
        return ActionResult(
            action="goto",
            ok=True,
            detail=f"navigated to {snapshot.url}",
            url=snapshot.url,
            title=snapshot.title,
            status=snapshot.status_code,
            requests=requests,
            new_urls=tuple(r.url for r in requests),
            idle_reached=idle.idle,
            waited_ms=idle.waited_ms,
            action_index=index,
        )

    def click(self, target: str) -> ActionResult:
        """Click an element by ref (``"e3"``) or natural-language description.

        No CSS selector is required from the caller: ``target`` may be
        ``"Add to cart"``, ``"next page"``, ``"the first product link"``, etc.
        """
        page = self._require_page()
        index = len(self._actions) + 1
        try:
            selector, element = self._resolve_selector(target)
        except BrowserError as exc:
            self._logger.error("action %d click failed to resolve %r: %s", index, target, exc)
            self._record("click", target, self._tracker.mark(), self._tracker.mark(),
                         {"in_session_har": False, "resolved": False})
            return ActionResult(**self._base_result("click", False, str(exc), index))

        mark_start = self._tracker.mark()
        self._logger.info(
            "action %d click %r -> ref=%s tag=%s text=%r",
            index, target, element.ref, element.tag, element.text,
        )
        try:
            page.click(selector, timeout=self.click_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            self._record("click", target, mark_start, mark_start, {"in_session_har": False})
            return ActionResult(
                **self._base_result("click", False, f"click on {target!r} failed: {exc}", index)
            )

        idle = self._settle()
        mark_end = self._tracker.mark()
        requests = self._requests_between(mark_start, mark_end)
        self._record("click", target, mark_start, mark_end,
                     {"resolved_ref": element.ref, "resolved_text": element.text})
        try:
            title = page.title()
        except Exception:  # noqa: BLE001
            title = ""
        self._logger.info(
            "action %d click done | url=%s requests=%d idle=%s new=%s",
            index, page.url, len(requests), idle.idle,
            [r.url for r in requests][:5],
        )
        return ActionResult(
            action="click",
            ok=True,
            detail=f"clicked {target!r} (matched {element.tag} {element.text!r})",
            url=page.url,
            title=title or "",
            status=None,
            requests=requests,
            new_urls=tuple(r.url for r in requests),
            idle_reached=idle.idle,
            waited_ms=idle.waited_ms,
            action_index=index,
        )

    def fill(self, target: str, text: str) -> ActionResult:
        """Type ``text`` into an input located by ref or natural language.

        Together with :meth:`click` this lets an AI agent log in to a site
        without writing any selector: ``fill("username", "demo")``,
        ``fill("password", "secret")``, ``click("Sign in")``.
        """
        page = self._require_page()
        index = len(self._actions) + 1
        try:
            selector, element = self._resolve_selector(target)
        except BrowserError as exc:
            self._logger.error("action %d fill failed to resolve %r: %s", index, target, exc)
            self._record("fill", target, self._tracker.mark(), self._tracker.mark(),
                         {"in_session_har": False, "resolved": False})
            return ActionResult(**self._base_result("fill", False, str(exc), index))

        mark_start = self._tracker.mark()
        self._logger.info(
            "action %d fill %r (%d chars) -> ref=%s tag=%s",
            index, target, len(text or ""), element.ref, element.tag,
        )
        try:
            page.fill(selector, text or "", timeout=self.click_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            self._record("fill", target, mark_start, mark_start, {"in_session_har": False})
            return ActionResult(
                **self._base_result("fill", False, f"fill {target!r} failed: {exc}", index)
            )

        idle = self._settle()
        mark_end = self._tracker.mark()
        requests = self._requests_between(mark_start, mark_end)
        self._record("fill", target, mark_start, mark_end,
                     {"resolved_ref": element.ref, "resolved_text": element.text})
        # never log the typed value - it may be a password
        self._logger.info(
            "action %d fill done | requests=%d idle=%s", index, len(requests), idle.idle
        )
        return ActionResult(
            action="fill",
            ok=True,
            detail=f"filled {target!r} (matched {element.tag} {element.name or element.text!r})",
            url=page.url,
            title="",
            status=None,
            requests=requests,
            new_urls=tuple(r.url for r in requests),
            idle_reached=idle.idle,
            waited_ms=idle.waited_ms,
            action_index=index,
        )

    def save_auth_state(
        self,
        path: Optional[str | Path] = None,
        *,
        profile: str = "default",
    ) -> dict:
        """Persist the current login state (cookies + localStorage) to a file.

        This is the Part 8 bridge: log in with ``goto``/``fill``/``click``, then
        call this once so future sessions (even in another process) start
        logged in by passing ``auth_state=<path>``.
        """
        self._require_page()
        target = Path(path) if path else (
            Path(self.auth_state) if self.auth_state else None
        )
        if target is None:
            raise BrowserError(
                "No auth state path configured; pass one to save_auth_state(path=...) "
                "or construct the AgentSession with auth_state=..."
            )
        # Save from the *agent* context - that is where the login cookies live.
        info = save_auth_state(self._context, target, profile=profile)
        self.auth_state = target
        self._engine.auth_state_path = target
        self._record("save_auth_state", str(info.path), self._tracker.mark(),
                     self._tracker.mark(), {"in_session_har": False, "auth": info.to_dict()})
        self._logger.info("saved auth state via agent session: %s", info.to_dict())
        return info.to_dict()

    def storage_state(self, path: Optional[str | Path] = None) -> dict:
        """Return (and optionally write) the agent context's storage state."""
        self._require_page()
        if path is not None:
            return self._context.storage_state(path=str(Path(path)))
        return self._context.storage_state()

    def auth_state_info(self) -> Optional[dict]:
        """Describe the configured auth state file, if any."""
        info = self._engine.auth_state_info()
        return info.to_dict() if info else None

    def capture_snapshot(self) -> ActionResult:
        """Return the current page state (URL, title, text, elements)."""
        page = self._require_page()
        index = len(self._actions) + 1
        mark = self._tracker.mark()
        observation = self.observe()
        self._record("capture_snapshot", page.url, mark, mark, {"in_session_har": False})
        self._logger.info(
            "action %d captureSnapshot | url=%s elements=%d",
            index, observation.url, len(observation.elements),
        )
        return ActionResult(
            action="capture_snapshot",
            ok=True,
            detail=f"{len(observation.elements)} interactive elements on {observation.url}",
            url=observation.url,
            title=observation.title,
            status=None,
            requests=(),
            new_urls=(),
            idle_reached=True,
            waited_ms=0,
            action_index=index,
            observation=observation,
        )

    def capture_page(self, url: str, *, name: Optional[str] = None) -> ActionResult:
        """One-shot verified HAR capture of ``url`` (Part 4 review + retry).

        Unlike :meth:`finish`, this returns the HAR path *immediately* — ideal
        when the agent only needs a single API call.
        """
        self._require_page()
        index = len(self._actions) + 1
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name or url)[:60] or "page"
        har_path = self.out_dir / f"capture_{index:02d}_{slug}.har"
        self._logger.info("action %d capturePage %s -> %s", index, url, har_path)
        mark = self._tracker.mark()
        try:
            captured = capture_verified(
                self._engine,
                url,
                har_path,
                expect_urls=(),
                max_attempts=3,
            )
        except BrowserError as exc:
            self._record("capture_page", url, mark, mark,
                         {"in_session_har": False, "har": None})
            return ActionResult(**self._base_result("capture_page", False, str(exc), index))

        self._record("capture_page", url, mark, mark,
                     {"in_session_har": False, "har": str(captured.har_path)})
        # capture_page runs in its own context, so its requests are described
        # by the HAR file rather than by this action's request list.
        ok = captured.ok
        detail = (
            f"captured {captured.entries} entries to {captured.har_path}"
            if ok
            else f"capture incomplete: {list(captured.verification.problems)}"
        )
        self._logger.info("action %d capturePage done | ok=%s %s", index, ok, detail)
        return ActionResult(
            action="capture_page",
            ok=ok,
            detail=detail,
            url=url,
            title=captured.title,
            status=captured.status,
            requests=(),
            new_urls=(),
            idle_reached=captured.idle_reached,
            waited_ms=captured.waited_ms,
            action_index=index,
            har_path=captured.har_path,
        )

    # ------------------------------------------------------------------
    # Finish / teardown
    # ------------------------------------------------------------------
    def _har_label(self, index: int, action: str) -> str:
        return f"{index:02d}_{action}"

    def finish(self) -> SessionSummary:
        """Close the session and materialise one HAR file per action.

        While the session runs, one combined HAR is recorded; closing the
        context flushes it. It is then split by the per-action request
        boundaries into ``NN_<action>.har`` files (each validated), so every
        agent action gets its own correct HAR.
        """
        if self._finished:
            raise BrowserError("AgentSession.finish() was already called.")

        har_files: dict = {}
        session_har: Optional[Path] = None
        summary_path: Optional[Path] = None

        # capture_page already wrote its own HAR files before teardown
        for record in self._actions:
            path = record.get("har")
            if path and not record.get("in_session_har", False):
                har_files[self._har_label(record["index"], record["action"])] = Path(path)

        if self._tracker is not None:
            self._tracker.detach()
        if self._context is not None:
            try:
                self._context.close()  # flushes session.har
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Error closing session context: %s", exc)

        if self._session_har and self._session_har.exists():
            session_har = self._session_har
            har = load_har(session_har)
            entries = (har.get("log") or {}).get("entries") or []
            total = self._tracker.total_requests if self._tracker else len(entries)
            if total != len(entries):
                self._logger.warning(
                    "session HAR has %d entries but %d requests were observed; "
                    "per-action split may be approximate.",
                    len(entries), total,
                )
            for record in self._actions:
                if not record.get("in_session_har", True):
                    continue
                start, end = record["mark_start"], record["mark_end"]
                if end < start:
                    end = start
                slice_entries = entries[start:end]
                label = self._har_label(record["index"], record["action"])
                if not slice_entries:
                    self._logger.info("action %s produced no network entries", label)
                    continue
                path = save_har(
                    self.out_dir / f"{label}.har",
                    har_with_entries(har, slice_entries),
                )
                valid, problems = validate_har_file(path)
                har_files[label] = path
                self._logger.info(
                    "wrote %s | entries=%d valid=%s%s",
                    path.name, len(slice_entries), valid,
                    "" if valid else f" problems={problems[:3]}",
                )

        if self._engine is not None:
            try:
                self._engine.close()
            except BrowserError as exc:
                self._logger.warning("Error closing engine: %s", exc)

        self._finished = True
        duration_ms = int((time.monotonic() - (self._started_at or time.monotonic())) * 1000)
        summary = SessionSummary(
            session_dir=self.out_dir,
            actions=tuple(self._actions),
            har_files=har_files,
            session_har=session_har,
            summary_path=None,
            duration_ms=duration_ms,
        )
        summary_path = self.out_dir / "agent_session.json"
        summary_path.write_text(
            json.dumps(summary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._logger.info(
            "AgentSession finished | actions=%d har_files=%d duration_ms=%d",
            len(self._actions), len(har_files), duration_ms,
        )
        return SessionSummary(
            session_dir=summary.session_dir,
            actions=summary.actions,
            har_files=summary.har_files,
            session_har=summary.session_har,
            summary_path=summary_path,
            duration_ms=summary.duration_ms,
        )

    def close(self) -> None:
        """Best-effort teardown (idempotent); safe to call from ``finally``."""
        if self._finished:
            return
        try:
            self.finish()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("finish() during close() failed: %s", exc)
            if self._tracker is not None:
                self._tracker.detach()
            if self._engine is not None:
                try:
                    self._engine.close()
                except BrowserError:
                    pass
            self._finished = True

    def __enter__(self) -> "AgentSession":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ----------------------------------------------------------------------
# Machine-readable tool catalog (used by AI clients and the Part 7 MCP server)
# ----------------------------------------------------------------------
AGENT_TOOLS = [
    {
        "name": "goto",
        "description": (
            "Open a URL in the shared browser session and wait until the page and "
            "its network activity have settled. Returns the final URL, title and "
            "the requests that were triggered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute URL to open."}
            },
            "required": ["url"],
        },
    },
    {
        "name": "observe",
        "description": (
            "List the interactive elements (links, buttons, inputs) currently "
            "visible on the page. Each element has a stable 'ref' (e.g. \"e3\") "
            "that can be passed to click()."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "find",
        "description": (
            "Resolve a natural-language target (e.g. \"Add to cart\", \"next page\") "
            "or an element ref (e.g. \"e3\") to one element, without clicking it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element ref or natural-language description.",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "click",
        "description": (
            "Click an element identified by a ref or a natural-language description "
            "(no CSS selector needed) and return the requests the click triggered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element ref (\"e3\") or description (\"Add to cart\").",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "fill",
        "description": (
            "Type text into an input/textarea identified by a ref or a natural-language "
            "description (e.g. fill the 'username' field). Use with click() to log in."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element ref or description (\"username\", \"e4\").",
                },
                "text": {"type": "string", "description": "Text to type into the field."},
            },
            "required": ["target", "text"],
        },
    },
    {
        "name": "capture_snapshot",
        "description": (
            "Return the current page state: URL, title, visible text excerpt and "
            "the interactive elements, without navigating."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "capture_page",
        "description": (
            "Navigate to a URL in a dedicated context and write a *verified* HAR "
            "file (waits for slow/delayed APIs, retries if the capture would be "
            "empty). Returns the HAR file path immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute URL to capture."},
                "name": {
                    "type": "string",
                    "description": "Optional friendly name used in the HAR file name.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Close the session and write one HAR file per action performed. Returns "
            "the HAR file paths for every action."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

_TOOL_NAMES = tuple(tool["name"] for tool in AGENT_TOOLS)


def tool_catalog() -> list:
    """Return a deep-ish copy of the tool catalog (safe to mutate)."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": json.loads(json.dumps(tool["input_schema"])),
        }
        for tool in AGENT_TOOLS
    ]


def dispatch_tool(session: AgentSession, name: str, arguments: Optional[dict] = None) -> dict:
    """Execute one agent tool by name; returns a JSON-serialisable result dict.

    This is the single entry point used by an LLM client and by the Part 7 MCP
    server, so both always behave identically.
    """
    args = dict(arguments or {})
    if name not in _TOOL_NAMES:
        raise BrowserError(
            f"Unknown tool {name!r}. Available tools: {list(_TOOL_NAMES)}"
        )

    if name == "goto":
        result = session.goto(str(args["url"]))
    elif name == "observe":
        result = session.observe()
    elif name == "find":
        result = session.find(str(args["target"]))
    elif name == "click":
        result = session.click(str(args["target"]))
    elif name == "fill":
        result = session.fill(str(args["target"]), str(args.get("text") or ""))
    elif name == "capture_snapshot":
        result = session.capture_snapshot()
    elif name == "capture_page":
        result = session.capture_page(str(args["url"]), name=args.get("name"))
    else:  # finish
        result = session.finish()

    return result.to_dict()