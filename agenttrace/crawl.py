"""Multi-page site crawl orchestration (Part 5).

Visits every URL in a given list with the Part 4 reliability layer
(:func:`agenttrace.verification.capture_verified`), writes one distinctly named
HAR per page, never skips a page (a failing page is still *attempted* and
reported as failed), and returns a summary with visited/failed counts.

A machine-readable ``crawl_summary.json`` is written next to the HAR files.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlsplit

from .browser import BrowserEngine, BrowserError
from .verification import (
    DEFAULT_MAX_WAIT_MS,
    DEFAULT_QUIET_MS,
    capture_verified,
)

_HAR_NAME_MAX = 80


@dataclass(frozen=True)
class CrawlPageResult:
    """Outcome for a single crawled page."""

    index: int
    url: str
    har_name: str
    har_path: Optional[Path]
    ok: bool
    entries: int
    title: str
    status: Optional[int]
    attempts: int
    duration_ms: int
    error: str


@dataclass(frozen=True)
class CrawlSummary:
    """Aggregate outcome of a crawl run."""

    total: int
    visited: int
    failed: int
    skipped: int
    out_dir: Path
    duration_ms: int
    results: tuple

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "visited": self.visited,
            "failed": self.failed,
            "skipped": self.skipped,
            "out_dir": str(self.out_dir),
            "duration_ms": self.duration_ms,
            "pages": [
                {
                    "index": r.index,
                    "url": r.url,
                    "har_name": r.har_name,
                    "har_path": str(r.har_path) if r.har_path else None,
                    "ok": r.ok,
                    "entries": r.entries,
                    "title": r.title,
                    "status": r.status,
                    "attempts": r.attempts,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


def har_name_for(url: str, index: int) -> str:
    """Build a deterministic, filesystem-safe HAR file name for a URL."""
    parts = urlsplit(url)
    host = parts.netloc.lower().replace(":", "-")
    path = parts.path.strip("/") or "root"
    if parts.query:
        path = f"{path}_{parts.query}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{host}_{path}")
    slug = re.sub(r"_+", "_", slug).strip("_.")[:_HAR_NAME_MAX] or "page"
    return f"{index:03d}_{slug}.har"


def plan_har_names(urls: Sequence[str]) -> list[str]:
    """Return one unique HAR file name per URL (no collisions)."""
    names: list[str] = []
    used: set[str] = set()
    for index, url in enumerate(urls, start=1):
        base = har_name_for(url, index)[: -len(".har")]
        name = f"{base}.har"
        suffix = 2
        while name.lower() in used:
            name = f"{base}-{suffix}.har"
            suffix += 1
        used.add(name.lower())
        names.append(name)
    return names


def crawl_site(
    engine: BrowserEngine,
    urls: Sequence[str],
    *,
    out_dir: str | Path,
    min_entries: int = 1,
    expect_urls: Sequence[str] = (),
    max_attempts: int = 3,
    quiet_ms: int = DEFAULT_QUIET_MS,
    max_wait_ms: int = DEFAULT_MAX_WAIT_MS,
    continue_on_error: bool = True,
) -> CrawlSummary:
    """Visit every URL, capture one HAR per page, and summarise the run.

    Args:
        engine: an already-started :class:`BrowserEngine`.
        urls: pages to visit (order preserved).
        out_dir: directory for the per-page HAR files and ``crawl_summary.json``.
        min_entries: minimum expected HAR entries per page (passed to the
            reliability layer).
        expect_urls: URL substrings every page must have captured.
        max_attempts: capture retries per page before it is reported as failed.
        continue_on_error: keep crawling the remaining pages after a failure.
            A failing page is still *attempted*, so nothing is silently skipped.
    """
    logger = logging.getLogger("agenttrace.crawl")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    url_list = list(urls)
    names = plan_har_names(url_list)
    results: list[CrawlPageResult] = []
    started = time.monotonic()

    logger.info(
        "crawl start | pages=%d out_dir=%s", len(url_list), out_dir
    )
    for index, (url, name) in enumerate(zip(url_list, names), start=1):
        page_started = time.monotonic()
        har_path = out_dir / name
        logger.info("crawl page %d/%d | %s -> %s", index, len(url_list), url, name)
        try:
            captured = capture_verified(
                engine,
                url,
                har_path,
                min_entries=min_entries,
                expect_urls=expect_urls,
                max_attempts=max_attempts,
                quiet_ms=quiet_ms,
                max_wait_ms=max_wait_ms,
            )
            error = "" if captured.ok else "; ".join(captured.verification.problems)
            results.append(
                CrawlPageResult(
                    index=index,
                    url=url,
                    har_name=name,
                    har_path=har_path if har_path.exists() else None,
                    ok=captured.ok,
                    entries=captured.entries,
                    title=captured.title,
                    status=captured.status,
                    attempts=captured.attempts,
                    duration_ms=int((time.monotonic() - page_started) * 1000),
                    error=error,
                )
            )
            if captured.ok:
                logger.info(
                    "page %d/%d OK | entries=%d attempts=%d title=%r",
                    index,
                    len(url_list),
                    captured.entries,
                    captured.attempts,
                    captured.title,
                )
            else:
                logger.error("page %d/%d NOT VERIFIED | %s", index, len(url_list), error)
        except BrowserError as exc:
            logger.error("page %d/%d FAILED | %s", index, len(url_list), exc)
            if not continue_on_error:
                raise
            results.append(
                CrawlPageResult(
                    index=index,
                    url=url,
                    har_name=name,
                    har_path=har_path if har_path.exists() else None,
                    ok=False,
                    entries=0,
                    title="",
                    status=None,
                    attempts=0,
                    duration_ms=int((time.monotonic() - page_started) * 1000),
                    error=str(exc),
                )
            )

    visited = sum(1 for r in results if r.ok)
    summary = CrawlSummary(
        total=len(url_list),
        visited=visited,
        failed=len(results) - visited,
        skipped=len(url_list) - len(results),
        out_dir=out_dir,
        duration_ms=int((time.monotonic() - started) * 1000),
        results=tuple(results),
    )

    summary_path = out_dir / "crawl_summary.json"
    summary_path.write_text(
        json.dumps(summary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "crawl done | total=%d visited=%d failed=%d skipped=%d duration_ms=%d summary=%s",
        summary.total,
        summary.visited,
        summary.failed,
        summary.skipped,
        summary.duration_ms,
        summary_path,
    )
    return summary