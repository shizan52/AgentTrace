"""Auth/session handling (Part 8) — persist and reuse a logged-in state.

Playwright can serialise a browser context's authentication material
(cookies **and** per-origin ``localStorage``) to a JSON *storage state* file and
restore it in a brand-new context — even in a brand-new process. That is what
makes "log in once, reuse forever" possible across module restarts.

This module owns the file-level concerns:

* :func:`save_auth_state` — write the current context's state to disk
* :func:`inspect_auth_state` — read metadata (cookie count/names, origins)
  **without** a browser, e.g. to verify a saved profile
* :func:`auth_state_available` / :func:`storage_state_arg` — safe helpers used
  by :class:`agenttrace.browser.BrowserEngine` when restoring
* :func:`clear_auth_state` — delete a saved profile

Security: cookie *values* (and localStorage contents) are never logged — only
cookie names and counts are exposed, so a session token cannot leak into logs
or test summaries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AuthStateInfo:
    """Metadata about a saved auth/session state file."""

    path: Path
    profile: str
    cookies: int
    origins: int
    cookie_names: tuple
    size_bytes: int
    saved_at: str

    @property
    def has_cookies(self) -> bool:
        return self.cookies > 0

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "profile": self.profile,
            "cookies": self.cookies,
            "origins": self.origins,
            "cookie_names": list(self.cookie_names),
            "size_bytes": self.size_bytes,
            "saved_at": self.saved_at,
            "has_cookies": self.has_cookies,
        }


def _mtime_iso(path: Path) -> str:
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return stamp.isoformat()
    except OSError:
        return ""


def save_auth_state(
    context,
    path: str | Path,
    *,
    profile: str = "default",
) -> AuthStateInfo:
    """Persist ``context``'s cookies + origin storage to ``path``.

    Args:
        context: a live Playwright browser context (must not be closed).
        path: destination JSON file (parent directories are created).
        profile: free-form label stored next to the file name for humans.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(target))
    info = inspect_auth_state(target, profile=profile)
    return info


def inspect_auth_state(path: str | Path, *, profile: Optional[str] = None) -> AuthStateInfo:
    """Read a storage-state file and describe it (no browser needed)."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"auth state file not found: {target}")

    with open(target, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    cookies = data.get("cookies") or []
    origins = data.get("origins") or []
    names = tuple(sorted({str(c.get("name")) for c in cookies if isinstance(c, dict)}))

    return AuthStateInfo(
        path=target,
        profile=profile or target.stem,
        cookies=len(cookies),
        origins=len(origins),
        cookie_names=names,
        size_bytes=target.stat().st_size,
        saved_at=_mtime_iso(target),
    )


def auth_state_available(path: Optional[str | Path]) -> bool:
    """True when ``path`` points at an existing, readable storage-state file."""
    if not path:
        return False
    target = Path(path)
    if not target.is_file():
        return False
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data.get("cookies"), list) or isinstance(data.get("origins"), list)


def storage_state_arg(path: Optional[str | Path]) -> Optional[str]:
    """Return a usable ``storage_state=`` argument, or ``None`` when unavailable."""
    if auth_state_available(path):
        return str(Path(path))
    return None


def clear_auth_state(path: str | Path) -> bool:
    """Delete a saved auth state file; returns True when something was removed."""
    target = Path(path)
    if target.is_file():
        target.unlink()
        return True
    return False


def cookie_names(path: str | Path) -> tuple:
    """Convenience wrapper: cookie names present in a saved state file."""
    return inspect_auth_state(path).cookie_names