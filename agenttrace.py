#!/usr/bin/env python3
"""AgentTrace - an AI helper module for web scraping, in one file.

Browser automation (Playwright/Chromium) with full network capture: verified
HARs per page / action / SPA route, selector-free actions for AI agents,
API discovery (REST/GraphQL) and export (Postman, OpenAPI, Python client),
site reconnaissance ("which scraping strategy fits this site?"), list
extraction + pagination, login-state reuse, CAPTCHA human-in-the-loop,
polite rate limiting, SSE/WebSocket capture, mocking, workflows with
checkpoints, record/replay, an MCP server and a real-world self-test.

    python agenttrace.py doctor                 # check the setup
    python agenttrace.py recon URL              # how should this site be scraped?
    python agenttrace.py extract URL --paginate --output data.csv
    python agenttrace.py mcp                    # tools for Claude / any MCP client
    python agenttrace.py selftest               # prove it works here

    import agenttrace as at
    with at.Session() as s:
        s.goto("https://books.toscrape.com/")
        rows = s.extract_list()["items"]

Requires Python 3.9+ and ``pip install playwright && python -m playwright install chromium``.
See README.md for the full guide (written for AI agents and humans).
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import datetime as _dt
import email.utils
import gzip
import hashlib
import hmac
import html
import http.client
import importlib
import importlib.util
import io
import json
import logging
import math
import mimetypes
import os
import platform
import random
import re
import secrets
import shlex
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import zlib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from difflib import SequenceMatcher
from fnmatch import fnmatch
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qs, parse_qsl, quote, quote_plus, unquote, urlencode, urljoin, urlsplit, urlunsplit


# ════════════════════════════════════════════════════════════════════════════
# core.py — core utilities: errors, time, ids, files, URLs, logging, event log, hooks & plugins (Parts 11, 32, 33)
# ════════════════════════════════════════════════════════════════════════════
# Core utilities shared by every part: errors, ids, time, JSON I/O, URL
# helpers, structured logging (Part 33), the execution timeline (Part 29) and
# the hook/plugin manager (Part 32).


__version__ = "2.0.0"

DEFAULT_RUNS_DIR = "agenttrace_runs"


# ═══════════════════════════════════════════════════════════════════════
# Errors — every error carries a machine-readable code + hint for AI agents
# ═══════════════════════════════════════════════════════════════════════
class AgentTraceError(Exception):
    """Base error. ``to_dict()`` gives an AI-friendly explanation."""

    code = "agenttrace_error"

    def __init__(self, message: str, *, details: Optional[dict] = None,
                 hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})
        self.hint = hint

    def to_dict(self) -> dict:
        out = {"error": self.code, "message": str(self)}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = to_jsonable(self.details)
        return out


class BrowserError(AgentTraceError):
    code = "browser_error"


class NavigationError(BrowserError):
    code = "navigation_error"


class ElementNotFoundError(AgentTraceError):
    code = "element_not_found"


class ActionError(AgentTraceError):
    code = "action_failed"


class GuardrailViolation(AgentTraceError):
    code = "guardrail_violation"


class BlockedError(AgentTraceError):
    code = "blocked"


class ActionVetoed(AgentTraceError):
    """A ``pre_action`` plugin refused the action (e.g. a destructive click)."""

    code = "action_vetoed"


class ConfigError(AgentTraceError):
    code = "config_error"


class ValidationFailed(AgentTraceError):
    code = "validation_failed"


class RateLimitError(AgentTraceError):
    code = "rate_limited"


# ═══════════════════════════════════════════════════════════════════════
# Time & ids
# ═══════════════════════════════════════════════════════════════════════
def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(ts: Optional[Any] = None) -> str:
    """ISO-8601 UTC timestamp with milliseconds, e.g. ``2026-09-25T21:30:00.123Z``.

    Accepts ``None`` (now), a ``datetime`` or epoch seconds.
    """
    if ts is None:
        dt = utc_now()
    elif isinstance(ts, (int, float)):
        dt = _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc)
    else:
        dt = ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)
        dt = dt.astimezone(_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(text: str) -> Optional[_dt.datetime]:
    if not isinstance(text, str) or not text.strip():
        return None
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(value)
    except ValueError:
        # Python < 3.11 cannot parse some fractional-second variants.
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", value)
        if not m:
            return None
        frac = (m.group(2) or ".0")[:7]
        try:
            dt = _dt.datetime.fromisoformat(m.group(1) + frac + (m.group(3) or ""))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def mono_ms() -> float:
    """Monotonic clock in milliseconds (for durations; never wall time)."""
    return time.monotonic() * 1000.0


def stamp() -> str:
    """Filesystem-safe UTC stamp (no ``:`` - valid on Windows)."""
    return utc_now().strftime("%Y%m%d-%H%M%S")


def new_id(prefix: str = "run") -> str:
    """Unique, sortable, filesystem-safe id: ``run-20260925-213000-a1b2c3``."""
    return f"{prefix}-{stamp()}-{secrets.token_hex(3)}"


def slugify(text: str, max_len: int = 60, default: str = "item") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)[:max_len].strip("-._")
    return slug or default


def sha256_hex(data: Any) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data or b"").hexdigest()


def keyed_digest(key: bytes, value: str, length: int = 8) -> str:
    return hmac.new(key, value.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:length]


# ═══════════════════════════════════════════════════════════════════════
# JSON I/O
# ═══════════════════════════════════════════════════════════════════════
def to_jsonable(obj: Any, *, _depth: int = 0) -> Any:
    """Convert dataclasses, Paths, bytes, sets, datetimes, objects with
    ``to_dict()`` … into plain JSON-serialisable structures."""
    if _depth > 60:
        return repr(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return to_jsonable(obj.to_dict(), _depth=_depth + 1)
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v, _depth=_depth + 1) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v, _depth=_depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = sorted(obj, key=repr) if isinstance(obj, (set, frozenset)) else obj
        return [to_jsonable(v, _depth=_depth + 1) for v in items]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        try:
            return bytes(obj).decode("utf-8")
        except UnicodeDecodeError:
            return {"base64": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, _dt.datetime):
        return iso(obj)
    return repr(obj)


def dump_json(data: Any, *, indent: Optional[int] = 2) -> str:
    return json.dumps(to_jsonable(data), indent=indent, ensure_ascii=False)


def write_json(path: Any, data: Any, *, indent: Optional[int] = 2) -> Path:
    """Atomically write JSON (temp file + ``os.replace``) so a crash never
    leaves a half-written state/manifest file behind."""
    return write_text(path, dump_json(data, indent=indent))


def write_text(path: Any, text: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    for attempt in range(6):
        try:
            os.replace(tmp, target)
            break
        except PermissionError:  # Windows: target briefly locked by a reader
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))
    return target


def write_bytes(path: Any, data: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def read_json(path: Any, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


_JSONL_LOCK = threading.Lock()


def append_jsonl(path: Any, obj: Any) -> None:
    line = json.dumps(to_jsonable(obj), ensure_ascii=False)
    target = Path(path)
    with _JSONL_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")


def read_jsonl(path: Any) -> list:
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# ═══════════════════════════════════════════════════════════════════════
# URL helpers
# ═══════════════════════════════════════════════════════════════════════
_MULTI_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "com.au", "net.au",
    "org.au", "edu.au", "gov.au", "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "ac.jp",
    "co.in", "net.in", "org.in", "gov.in", "ac.in", "com.bd", "net.bd", "org.bd", "gov.bd",
    "edu.bd", "ac.bd", "com.br", "net.br", "org.br", "com.cn", "net.cn", "org.cn", "gov.cn",
    "com.tr", "co.za", "org.za", "com.sg", "edu.sg", "com.my", "com.mx", "com.ar", "co.kr",
    "or.kr", "com.hk", "com.tw", "com.pk", "com.ng", "com.eg", "com.sa", "com.vn", "co.id",
    "co.th", "com.ph", "com.ua", "co.il", "com.pl", "com.co", "com.pe", "com.np", "com.lk",
    "github.io", "herokuapp.com", "vercel.app", "netlify.app", "pages.dev", "web.app",
    "firebaseapp.com", "appspot.com", "cloudfront.net", "azurewebsites.net",
}


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def site_of(host_or_url: str) -> str:
    """Approximate registrable domain (eTLD+1) without external data."""
    host = host_or_url
    if "://" in host_or_url:
        host = host_of(host_or_url)
    host = (host or "").lower().strip(".")
    if not host or re.match(r"^[\d.]+$", host) or ":" in host:
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_site(a: str, b: str) -> bool:
    return bool(site_of(a)) and site_of(a) == site_of(b)


def url_path(url: str) -> str:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return "/"


def query_pairs(url: str) -> list:
    try:
        return parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return []


def strip_query(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUM_RE = re.compile(r"^\d+$")
_EMAIL_RE = re.compile(r"^[^@/\s]+@[^@/\s]+\.[a-z]{2,}$", re.I)
_SLUG_ID_RE = re.compile(r"^(.+?)([-_])(\d+)(\.[A-Za-z0-9]{1,5})?$")
_TOKEN_RE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_-]{20,}$")


def template_segment(segment: str) -> str:
    """Generalise one path segment that looks like an identifier."""
    if not segment:
        return segment
    if _NUM_RE.match(segment):
        return "{id}"
    if _UUID_RE.match(segment):
        return "{uuid}"
    if _DATE_RE.match(segment):
        return "{date}"
    if _EMAIL_RE.match(segment):
        return "{email}"
    if _HEX_RE.match(segment):
        return "{hash}"
    m = _SLUG_ID_RE.match(segment)
    if m and re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", m.group(1)) \
            and re.search(r"[a-z]{2,}", m.group(1)):
        head, sep, _num, ext = m.groups()
        ext = ext or ""
        # "page-2.html" keeps its word; "a-light-in-the-attic_1000" becomes {slug}_{id}.
        if re.fullmatch(r"[a-z]{1,12}", head):
            return f"{head}{sep}{{n}}{ext}"
        return f"{{slug}}{sep}{{id}}{ext}"
    if _TOKEN_RE.match(segment):
        return "{token}"
    return segment


def path_template(path_or_url: str) -> str:
    """``/api/v1/products/123/reviews`` → ``/api/v1/products/{id}/reviews``."""
    path = url_path(path_or_url) if "://" in path_or_url else (path_or_url or "/")
    path = path.split("?", 1)[0]
    segments = path.split("/")
    return "/".join(template_segment(s) for s in segments) or "/"


def url_template(url: str) -> str:
    """Scheme + host + templated path (query dropped)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return f"{parts.scheme}://{parts.netloc}{path_template(parts.path or '/')}"


# ═══════════════════════════════════════════════════════════════════════
# Structured logging (Part 33 — observability & debug mode)
# ═══════════════════════════════════════════════════════════════════════
LOGGER_NAME = "agenttrace"
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())

_LOG_ID_FIELDS = ("session_id", "action_id", "request_id", "run_id", "task_id", "event")


class JsonlLogHandler(logging.Handler):
    """Writes one JSON object per log record (machine-parseable debug log).

    Extra fields passed via ``extra={"at": {...}}`` (see :func:`log_event`)
    are flattened into the line, so a failure can be traced from the log
    alone: ``session_id`` → ``action_id`` → ``request_id`` → ``error``.
    """

    def __init__(self, path: Any) -> None:
        super().__init__(logging.DEBUG)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8", newline="\n")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = {
                "ts": iso(record.created),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            fields = getattr(record, "at", None)
            if isinstance(fields, dict):
                for key, value in fields.items():
                    line[key] = to_jsonable(value)
            if record.exc_info and record.exc_info[1] is not None:
                line["exception"] = f"{type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            with _JSONL_LOCK:
                self._fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                self._fh.flush()
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)

    def close(self) -> None:
        try:
            self._fh.close()
        finally:
            super().close()


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname[0]} {record.getMessage()}"
        fields = getattr(record, "at", None)
        if isinstance(fields, dict):
            ids = [f"{k}={fields[k]}" for k in _LOG_ID_FIELDS if fields.get(k)]
            if ids:
                base += "  [" + " ".join(ids) + "]"
        if record.exc_info and record.exc_info[1] is not None:
            base += f"  ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
        return base


def configure_logging(level: Any = "INFO", *, console: bool = True, stream: Any = None,
                      log_file: Any = None, jsonl_file: Any = None) -> logging.Logger:
    """Configure the shared ``agenttrace`` logger (idempotent).

    Console output goes to **stderr** by default so stdout stays clean for
    JSON/MCP output.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    if console:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(_ConsoleFormatter())
        logger.addHandler(handler)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
        logger.addHandler(fh)
    if jsonl_file:
        logger.addHandler(JsonlLogHandler(jsonl_file))
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def log_event(logger: logging.Logger, level: int, event: str, msg: str, **fields: Any) -> None:
    """Log with structured fields (``event``, ``session_id``, ``action_id`` …)."""
    fields["event"] = event
    logger.log(level, msg, extra={"at": fields})


# ═══════════════════════════════════════════════════════════════════════
# Execution timeline (Part 29)
# ═══════════════════════════════════════════════════════════════════════
class EventLog:
    """Chronological, thread-safe event stream for one session/run.

    Every interesting thing (action start/end, navigation, request, response,
    download, console error, retry, validation …) becomes one event with a
    wall-clock ISO timestamp, a relative ``t_ms`` and optional ids.
    """

    def __init__(self, sink: Any = None) -> None:
        self._events: list = []
        self._lock = threading.Lock()
        self._t0 = mono_ms()
        self._seq = 0
        self.sink = Path(sink) if sink else None
        self.listeners: list = []

    def emit(self, kind: str, /, **data: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": iso(), "t_ms": round(mono_ms() - self._t0, 1),
                     "kind": kind}
            for key, value in data.items():
                if value is not None:
                    event[f"data_{key}" if key in ("seq", "ts", "t_ms", "kind") else key] = value
            self._events.append(event)
        if self.sink is not None:
            try:
                append_jsonl(self.sink, event)
            except OSError:
                pass
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - a broken listener must never break capture
                pass
        return event

    def events(self, kind: Optional[str] = None, **match: Any) -> list:
        with self._lock:
            items = list(self._events)
        if kind:
            kinds = {kind} if isinstance(kind, str) else set(kind)
            items = [e for e in items if e.get("kind") in kinds]
        for key, value in match.items():
            items = [e for e in items if e.get(key) == value]
        return items

    def __len__(self) -> int:
        return len(self._events)

    def write_jsonl(self, path: Any) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            for event in self.events():
                fh.write(json.dumps(to_jsonable(event), ensure_ascii=False) + "\n")
        return target


# ═══════════════════════════════════════════════════════════════════════
# Hooks & plugins (Part 32)
# ═══════════════════════════════════════════════════════════════════════
HOOK_EVENTS = (
    "session_start", "session_end", "pre_action", "post_action", "request", "response", "request_finished",
    "validation", "output", "error", "blocked", "page", "download",
)


class HookManager:
    """Register custom logic without touching the core engine.

    Ways to add behaviour:

    * ``hooks.on("pre_action", fn)`` or ``@hooks.on("post_action")``
    * ``hooks.register_plugin(obj)`` — methods named ``pre_action``/``on_pre_action``…
    * ``hooks.load("my_plugin.py")`` — a file/module exposing ``register(hooks)``,
      a ``PLUGIN`` object, or module-level ``on_<event>`` functions.

    Handlers receive keyword arguments (``session``, ``action``, ``record`` …)
    and may return a value; exceptions are isolated and recorded in
    ``errors`` (``strict=True`` re-raises them instead).  A ``pre_action``
    handler that returns ``{"veto": "reason"}`` stops that action (safety
    plugins); ``request_finished`` handlers see the record *with* its body.
    """

    def __init__(self, *, strict: bool = False) -> None:
        self.strict = strict
        self._handlers: dict = {name: [] for name in HOOK_EVENTS}
        self.errors: list = []
        self.plugins: list = []
        self._log = get_logger("hooks")

    def on(self, event: str, fn: Optional[Callable] = None):
        if event not in self._handlers:
            raise ConfigError(f"unknown hook event {event!r}", hint=f"use one of {HOOK_EVENTS}")
        if fn is None:
            def deco(func: Callable) -> Callable:
                self._handlers[event].append(func)
                return func
            return deco
        self._handlers[event].append(fn)
        return fn

    def has(self, event: str) -> bool:
        return bool(self._handlers.get(event))

    def register_plugin(self, plugin: Any) -> Any:
        found = 0
        for event in HOOK_EVENTS:
            for attr in (event, f"on_{event}"):
                fn = plugin.get(attr) if isinstance(plugin, dict) else getattr(plugin, attr, None)
                if callable(fn):
                    self._handlers[event].append(fn)
                    found += 1
                    break
        if not found:
            raise ConfigError(f"plugin {plugin!r} defines no hook handlers",
                              hint=f"define functions named like {HOOK_EVENTS[:4]}")
        self.plugins.append(plugin)
        return plugin

    def load(self, spec: str) -> Any:
        """Load a plugin from ``path/to/file.py[:Attr]`` or ``package.module[:Attr]``."""
        target, _, attr = str(spec).partition(":")
        if target.endswith(".py") or os.path.sep in target or "/" in target:
            path = Path(target).expanduser().resolve()
            if not path.exists():
                raise ConfigError(f"plugin file not found: {path}")
            mod_name = f"agenttrace_plugin_{slugify(path.stem, 40, 'p')}_{secrets.token_hex(2)}"
            module_spec = importlib.util.spec_from_file_location(mod_name, path)
            if module_spec is None or module_spec.loader is None:
                raise ConfigError(f"cannot import plugin file {path}")
            module = importlib.util.module_from_spec(module_spec)
            sys.modules[mod_name] = module
            module_spec.loader.exec_module(module)
        else:
            module = importlib.import_module(target)
        obj = getattr(module, attr) if attr else module
        if attr and isinstance(obj, type):
            obj = obj()
        if not attr and callable(getattr(module, "register", None)):
            module.register(self)
            self.plugins.append(module)
            self._log.info("plugin loaded via register(): %s", spec)
            return module
        if not attr and getattr(module, "PLUGIN", None) is not None:
            obj = module.PLUGIN
            if isinstance(obj, type):
                obj = obj()
        self.register_plugin(obj)
        self._log.info("plugin loaded: %s", spec)
        return obj

    def emit(self, event: str, **payload: Any) -> list:
        results = []
        for fn in list(self._handlers.get(event, ())):
            try:
                results.append(fn(**payload))
            except Exception as exc:  # noqa: BLE001 - plugin isolation
                self.errors.append({"event": event, "handler": getattr(fn, "__name__", repr(fn)),
                                    "error": f"{type(exc).__name__}: {exc}", "ts": iso()})
                self._log.warning("hook %s handler %s failed: %s", event,
                                  getattr(fn, "__name__", fn), exc)
                if self.strict:
                    raise
        return results


def chunks(items: Iterable, size: int) -> Iterable[list]:
    batch: list = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def truncate(text: Any, limit: int = 200) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def human_bytes(n: Optional[float]) -> str:
    if n is None:
        return "-"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"



# ════════════════════════════════════════════════════════════════════════════
# har.py — network records + HAR 1.2 builder / loader / validator + body integrity (Parts 2, 10, 44)
# ════════════════════════════════════════════════════════════════════════════
# Network record model + HAR 1.2 builder, loader, normaliser and validator
# (Part 2, Part 14 base, Part 44 body representation).
#
# ``NetRecord`` is the single in-memory representation of one HTTP exchange.
# Live captures produce records directly (see ``recorder.py``); any HAR file —
# ours, Chrome DevTools' or Firefox's — can be turned back into records with
# :func:`har_to_records`, so every analysis (noise, redaction, API discovery,
# export, regression) works on both live sessions and HAR files.


HAR_VERSION = "1.2"
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"}

_TEXT_MIME_RE = re.compile(
    r"^(text/|application/(json|[\w.+-]*\+json|javascript|ecmascript|x-javascript|xml|"
    r"[\w.+-]*\+xml|x-www-form-urlencoded|graphql|x-ndjson|ld\+json|manifest\+json|"
    r"x-yaml|yaml|csv)|image/svg\+xml)", re.I)


def is_text_mime(mime: str) -> bool:
    return bool(_TEXT_MIME_RE.match((mime or "").split(";")[0].strip()))


def mime_charset(content_type: str) -> str:
    m = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    return (m.group(1).strip("'\"") if m else "utf-8") or "utf-8"


def decode_body(data: Optional[bytes], content_type: str = "") -> Optional[str]:
    """Decode bytes using the declared charset (fallback utf-8, lossless-ish)."""
    if data is None:
        return None
    charset = mime_charset(content_type)
    for enc in (charset, "utf-8"):
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def header_value(headers: Iterable, name: str, default: str = "") -> str:
    lname = name.lower()
    for h in headers or ():
        if isinstance(h, dict) and str(h.get("name", "")).lower() == lname:
            return str(h.get("value", ""))
    return default


def header_values(headers: Iterable, name: str) -> list:
    lname = name.lower()
    return [str(h.get("value", "")) for h in headers or ()
            if isinstance(h, dict) and str(h.get("name", "")).lower() == lname]


# ═══════════════════════════════════════════════════════════════════════
# The record model
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class NetRecord:
    """One request/response exchange with full attribution.

    ``action_id`` links the request to the browser action that caused it
    (Part 13), ``page_id``/``frame_id`` to the tab/frame (Part 45) and
    ``segment_id`` to the (virtual) page it belongs to (Parts 2, 36).
    """

    id: str
    seq: int
    method: str
    url: str
    resource_type: str = "other"
    is_navigation: bool = False
    page_id: Optional[str] = None
    frame_id: Optional[str] = None
    action_id: Optional[str] = None
    segment_id: Optional[str] = None
    started: str = ""
    t_start: float = 0.0
    t_end: Optional[float] = None
    status: Optional[int] = None
    status_text: str = ""
    http_version: str = "HTTP/1.1"
    request_headers: list = field(default_factory=list)
    response_headers: list = field(default_factory=list)
    request_body: Optional[bytes] = None
    response_body: Optional[bytes] = None
    body_note: str = ""
    mime_type: str = ""
    response_size: Optional[int] = None
    transfer_size: Optional[int] = None
    failure: Optional[str] = None
    from_cache: bool = False
    from_service_worker: bool = False
    redirected_from: Optional[str] = None
    redirect_url: str = ""
    server_ip: str = ""
    timings: dict = field(default_factory=dict)
    category: str = ""
    tags: list = field(default_factory=list)
    mocked: Optional[dict] = None
    stream: Optional[dict] = None
    initiator: Optional[dict] = None
    extra: dict = field(default_factory=dict)

    # ---- convenience -------------------------------------------------
    @property
    def finished(self) -> bool:
        return self.t_end is not None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.t_end is None:
            return None
        return round(max(0.0, self.t_end - self.t_start), 1)

    @property
    def ok(self) -> bool:
        return self.failure is None and self.status is not None and 200 <= self.status < 400

    @property
    def host(self) -> str:
        try:
            return (urlsplit(self.url).hostname or "").lower()
        except ValueError:
            return ""

    @property
    def path(self) -> str:
        try:
            return urlsplit(self.url).path or "/"
        except ValueError:
            return "/"

    @property
    def query(self) -> list:
        try:
            return parse_qsl(urlsplit(self.url).query, keep_blank_values=True)
        except ValueError:
            return []

    def header(self, name: str, which: str = "response") -> str:
        return header_value(self.response_headers if which == "response" else self.request_headers,
                            name)

    @property
    def content_type(self) -> str:
        return self.mime_type or self.header("content-type")

    @property
    def request_content_type(self) -> str:
        return self.header("content-type", "request")

    @property
    def is_json(self) -> bool:
        ct = self.content_type.lower()
        return "json" in ct or (self.response_body or b"")[:1] in (b"{", b"[")

    def text(self) -> Optional[str]:
        return decode_body(self.response_body, self.content_type)

    def json(self) -> Any:
        body = self.text()
        if body is None:
            return None
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            return None

    def request_text(self) -> Optional[str]:
        return decode_body(self.request_body, self.request_content_type)

    def request_json(self) -> Any:
        body = self.request_text()
        if body is None:
            return None
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            return None

    def request_form(self) -> list:
        """Form fields sent (urlencoded or the text fields of multipart/form-data)."""
        ct = self.request_content_type.lower()
        if "multipart/form-data" in ct and self.request_body:
            return [(p["name"], p["data"].decode("utf-8", "replace")) for p in
                    parse_multipart(self.request_body, self.request_content_type) if p["filename"] is None]
        body = self.request_text()
        if body is not None and "x-www-form-urlencoded" in ct:
            return parse_qsl(body, keep_blank_values=True)
        return []

    def request_files(self) -> list:
        """Files uploaded in a multipart body: name, filename, content_type, size, sha256."""
        if not self.request_body or "multipart/form-data" not in self.request_content_type.lower():
            return []
        return [{"name": p["name"], "filename": p["filename"], "content_type": p["content_type"],
                 "size": len(p["data"]), "sha256": sha256_hex(p["data"])}
                for p in parse_multipart(self.request_body, self.request_content_type) if p["filename"] is not None]

    def integrity(self) -> dict:
        return body_integrity(self)

    def summary(self, *, preview: int = 160) -> dict:
        """Compact, AI-friendly description (no raw bodies, no secrets)."""
        out = {
            "id": self.id,
            "method": self.method,
            "url": self.url,
            "status": self.status,
            "type": self.resource_type,
        }
        if self.category:
            out["category"] = self.category
        if self.failure:
            out["failure"] = self.failure
        if self.duration_ms is not None:
            out["ms"] = self.duration_ms
        if self.action_id:
            out["action_id"] = self.action_id
        if self.page_id and self.page_id != "p1":
            out["page_id"] = self.page_id
        if self.mocked:
            out["mocked"] = self.mocked.get("action")
        ct = self.content_type.split(";")[0]
        if ct:
            out["mime"] = ct
        if self.response_size is not None:
            out["size"] = self.response_size
        shape = describe_payload(self.json()) if self.is_json else None
        if shape:
            out["response_shape"] = shape
        elif self.response_body and is_text_mime(ct) and "html" not in ct:
            out["response_preview"] = truncate(self.text(), preview)
        req_json = self.request_json() if self.request_body else None
        if req_json is not None:
            out["request_shape"] = describe_payload(req_json)
        return out

    def to_dict(self, *, bodies: bool = False) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}  # type: ignore[attr-defined]
        out["duration_ms"] = self.duration_ms
        for key in ("request_body", "response_body"):
            data = out.pop(key)
            if bodies and data is not None:
                ct = self.request_content_type if key == "request_body" else self.content_type
                if is_text_mime(ct):
                    out[key] = decode_body(data, ct)
                else:
                    out[key + "_base64"] = base64.b64encode(data).decode("ascii")
            elif data is not None:
                out[key + "_size"] = len(data)
                out[key + "_sha256"] = sha256_hex(data)
        return out


def pattern_key(rec: "NetRecord") -> str:
    """Method + host + templated path + query parameter names (request "shape")."""
    names = sorted({k for k, _ in rec.query})
    return f"{rec.method} {rec.host}{path_template(rec.path)}?{'&'.join(names)}"


def describe_payload(value: Any, depth: int = 0) -> Any:
    """Shape of a JSON payload: keys, array lengths and scalar types."""
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        items = list(value.items())[:25]
        out = {k: describe_payload(v, depth + 1) for k, v in items}
        if len(value) > 25:
            out["…"] = f"+{len(value) - 25} keys"
        return out
    if isinstance(value, list):
        if not value:
            return "array[0]"
        return {"array": len(value), "item": describe_payload(value[0], depth + 1)}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


# ═══════════════════════════════════════════════════════════════════════
# Records -> HAR
# ═══════════════════════════════════════════════════════════════════════
def _parse_cookie_header(value: str) -> list:
    cookies = []
    for part in (value or "").split(";"):
        if "=" in part:
            name, _, val = part.strip().partition("=")
            if name:
                cookies.append({"name": name, "value": val})
    return cookies


def parse_set_cookie(value: str) -> dict:
    parts = [p.strip() for p in (value or "").split(";")]
    name, _, val = (parts[0] if parts else "").partition("=")
    cookie = {"name": name.strip(), "value": val.strip()}
    for attr in parts[1:]:
        key, _, aval = attr.partition("=")
        key = key.strip().lower()
        if key == "path":
            cookie["path"] = aval
        elif key == "domain":
            cookie["domain"] = aval
        elif key == "expires":
            cookie["expires"] = aval
        elif key == "httponly":
            cookie["httpOnly"] = True
        elif key == "secure":
            cookie["secure"] = True
        elif key == "samesite":
            cookie["sameSite"] = aval
        elif key == "max-age":
            cookie["maxAge"] = aval
    return cookie


def _set_cookie_list(headers: list) -> list:
    out = []
    for value in header_values(headers, "set-cookie"):
        # Some stacks fold several cookies into one header joined by newlines.
        for line in value.split("\n"):
            if line.strip():
                out.append(parse_set_cookie(line))
    return out


def _post_data(rec: NetRecord) -> Optional[dict]:
    if rec.request_body is None:
        return None
    ct = rec.request_content_type or "application/octet-stream"
    post: dict = {"mimeType": ct}
    text = decode_body(rec.request_body, ct) if is_text_mime(ct) or "multipart" in ct.lower() else None
    if text is not None and ("multipart" not in ct.lower() or _is_mostly_text(rec.request_body)):
        post["text"] = text
    else:
        post["text"] = base64.b64encode(rec.request_body).decode("ascii")
        post["_encoding"] = "base64"
    if "x-www-form-urlencoded" in ct.lower() and text is not None:
        post["params"] = [{"name": k, "value": v} for k, v in parse_qsl(text, keep_blank_values=True)]
    elif "multipart/form-data" in ct.lower():
        post["params"] = parse_multipart_params(rec.request_body, ct)
    return post


def _is_mostly_text(data: bytes) -> bool:
    if not data:
        return True
    sample = data[:4096]
    bad = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return bad / max(1, len(sample)) < 0.02


def parse_multipart(data: bytes, content_type: str) -> list:
    """Parse a multipart/form-data body into parts (name, filename, type, bytes)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "", re.I)
    if not m or not data:
        return []
    boundary = b"--" + m.group(1).encode("latin-1")
    parts = []
    for chunk in data.split(boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk[2:] if chunk.startswith(b"\r\n") else chunk
        head, sep, body = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        headers = {}
        for line in head.decode("utf-8", "replace").split("\r\n"):
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        disp = headers.get("content-disposition", "")
        name = re.search(r'name="([^"]*)"', disp)
        fname = re.search(r'filename="([^"]*)"', disp)
        parts.append({"name": name.group(1) if name else "",
                      "filename": fname.group(1) if fname else None,
                      "content_type": headers.get("content-type", ""),
                      "data": body})
    return parts


def parse_multipart_params(data: bytes, content_type: str) -> list:
    params = []
    for part in parse_multipart(data, content_type):
        item = {"name": part["name"]}
        if part["filename"] is not None:
            item["fileName"] = part["filename"]
            item["contentType"] = part["content_type"] or "application/octet-stream"
            item["_size"] = len(part["data"])
            item["_sha256"] = sha256_hex(part["data"])
        else:
            item["value"] = part["data"].decode("utf-8", "replace")
        params.append(item)
    return params


# ═══════════════════════════════════════════════════════════════════════
# Part 44 — body integrity (complete? decodes as its type says?)
# ═══════════════════════════════════════════════════════════════════════
_MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/gif": b"GIF8",
          "application/pdf": b"%PDF-", "application/zip": b"PK\x03\x04", "application/gzip": b"\x1f\x8b",
          "image/webp": b"RIFF", "font/woff2": b"wOF2", "font/woff": b"wOFF"}
_TRAILERS = {"image/png": b"IEND\xaeB`\x82", "application/pdf": b"%%EOF", "image/jpeg": b"\xff\xd9"}
_CUT_FAILURES = ("CONTENT_LENGTH_MISMATCH", "INCOMPLETE_CHUNKED_ENCODING", "CONNECTION_RESET", "CONNECTION_CLOSED",
                 "EMPTY_RESPONSE", "CONTENT_DECODING_FAILED")


def body_integrity(rec: NetRecord) -> dict:
    """Check a captured response body: complete (vs Content-Length / transfer errors), and
    decodable as its declared type (JSON parses, PNG/JPEG/PDF magic + end markers)."""
    mime = rec.content_type.split(";")[0].strip().lower()
    body = rec.response_body
    declared = header_value(rec.response_headers, "content-length")
    encoding = header_value(rec.response_headers, "content-encoding").lower()
    wire = (rec.extra.get("sizes") or {}).get("responseBodySize")
    info = {"request_id": rec.id, "url": rec.url, "status": rec.status, "mime": mime,
            "size": len(body) if body is not None else None,
            "declared_length": int(declared) if declared.isdigit() else None,
            "content_encoding": encoding or None, "sha256": sha256_hex(body) if body is not None else None}
    problems = []
    failure = rec.failure or ""
    if failure:
        cut = any(x in failure.upper() for x in _CUT_FAILURES)
        problems.append(f"transfer failed: {failure}" + (" - body cut off (truncated)" if cut else ""))
    expect_body = (rec.status is not None and 200 <= rec.status < 300 and rec.status not in (204, 205)
                   and rec.method != "HEAD")
    if body is None:
        note = rec.body_note or ""
        if expect_body and not failure and not note.startswith("omitted"):
            problems.append("body missing" + (f" ({note})" if note else ""))
        return {"ok": not problems, "checked": False, "problems": problems, **info}
    size = info["declared_length"]
    if size is not None and rec.method != "HEAD":
        if not encoding and len(body) < size:
            problems.append(f"truncated: received {len(body)} of {size} bytes (Content-Length)")
        elif encoding and isinstance(wire, int) and 0 <= wire < size:
            problems.append(f"truncated: received {wire} of {size} {encoding} bytes (Content-Length)")
    if (rec.body_note or "").startswith("truncated"):
        problems.append(f"capture {rec.body_note} (raise max_body_bytes to keep it all)")
    if "json" in mime and expect_body:
        try:
            json.loads(decode_body(body, rec.content_type) or "")
        except ValueError as exc:
            problems.append(f"invalid JSON ({str(exc)[:70]}) - truncated or corrupt body")
    magic = _MAGIC.get(mime)
    if magic and expect_body:
        if not body.startswith(magic):
            problems.append(f"content is not {mime} (starts with {body[:8].hex()})")
        elif mime in _TRAILERS and _TRAILERS[mime] not in body[-64:]:
            problems.append(f"{mime} end marker missing - truncated")
    return {"ok": not problems, "checked": True, "problems": problems, **info}


def _har_timings(rec: NetRecord) -> dict:
    t = rec.timings or {}
    total = rec.duration_ms if rec.duration_ms is not None else 0.0

    def span(a: str, b: str) -> float:
        va, vb = t.get(a, -1), t.get(b, -1)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va >= 0 and vb >= va:
            return round(vb - va, 3)
        return -1

    dns = span("domainLookupStart", "domainLookupEnd")
    connect = span("connectStart", "connectEnd")
    ssl = span("secureConnectionStart", "connectEnd")
    wait = span("requestStart", "responseStart")
    receive = span("responseStart", "responseEnd")
    if wait < 0:
        wait = total if total else 0.0
    if receive < 0:
        receive = 0.0
    return {"blocked": -1, "dns": dns, "connect": connect, "ssl": ssl, "send": 0,
            "wait": max(0.0, wait), "receive": max(0.0, receive)}


def record_to_har_entry(rec: NetRecord, *, include_bodies: bool = True,
                        body_limit: Optional[int] = None) -> dict:
    ct = rec.content_type or "x-unknown"
    content: dict = {"size": rec.response_size if rec.response_size is not None
                     else (len(rec.response_body) if rec.response_body is not None else 0),
                     "mimeType": ct}
    if include_bodies and rec.response_body is not None:
        data = rec.response_body
        if body_limit is not None and len(data) > body_limit:
            data = data[:body_limit]
            content["comment"] = f"truncated to {body_limit} bytes"
        if is_text_mime(ct) or (not ct or ct == "x-unknown") and _is_mostly_text(data):
            content["text"] = decode_body(data, ct)
        else:
            content["text"] = base64.b64encode(data).decode("ascii")
            content["encoding"] = "base64"
    elif rec.body_note:
        content["comment"] = rec.body_note

    status = rec.status if rec.status is not None else 0
    request = {
        "method": rec.method,
        "url": rec.url,
        "httpVersion": rec.http_version or "HTTP/1.1",
        "cookies": _parse_cookie_header(header_value(rec.request_headers, "cookie")),
        "headers": [{"name": str(h.get("name", "")), "value": str(h.get("value", ""))}
                    for h in rec.request_headers],
        "queryString": [{"name": k, "value": v} for k, v in rec.query],
        "headersSize": -1,
        "bodySize": len(rec.request_body) if rec.request_body is not None else 0,
    }
    post = _post_data(rec) if include_bodies else None
    if post is not None:
        request["postData"] = post
    response = {
        "status": status,
        "statusText": rec.status_text or "",
        "httpVersion": rec.http_version or "HTTP/1.1",
        "cookies": _set_cookie_list(rec.response_headers),
        "headers": [{"name": str(h.get("name", "")), "value": str(h.get("value", ""))}
                    for h in rec.response_headers],
        "content": content,
        "redirectURL": rec.redirect_url or header_value(rec.response_headers, "location"),
        "headersSize": -1,
        "bodySize": rec.transfer_size if rec.transfer_size is not None else -1,
    }
    if rec.failure:
        response["_error"] = rec.failure
    timings = _har_timings(rec)
    total = rec.duration_ms
    if total is None:
        total = sum(v for k, v in timings.items() if isinstance(v, (int, float)) and v > 0
                    and k in ("blocked", "dns", "connect", "send", "wait", "receive"))
    entry = {
        "startedDateTime": rec.started or iso(),
        "time": round(max(0.0, float(total)), 3),
        "request": request,
        "response": response,
        "cache": {},
        "timings": timings,
        "_resourceType": rec.resource_type,
    }
    if rec.segment_id:
        entry["pageref"] = rec.segment_id
    if rec.server_ip:
        entry["serverIPAddress"] = rec.server_ip
    meta = {"id": rec.id, "seq": rec.seq, "action_id": rec.action_id, "page_id": rec.page_id,
            "frame_id": rec.frame_id, "is_navigation": rec.is_navigation}
    for key in ("category", "tags", "mocked", "stream", "initiator", "failure", "redirected_from"):
        value = getattr(rec, key)
        if value:
            meta[key] = value
    if rec.t_end is None and not rec.failure:
        meta["pending"] = True
        content.setdefault("comment", "request still in flight when the HAR was written")
    if rec.from_cache:
        meta["from_cache"] = True
    if rec.from_service_worker:
        meta["from_service_worker"] = True
    if rec.body_note:
        meta["body_note"] = rec.body_note
    if rec.response_body is not None:
        meta["body_sha256"] = sha256_hex(rec.response_body)
    if rec.response_body is not None or any(x in (rec.failure or "").upper() for x in _CUT_FAILURES):
        check = body_integrity(rec)
        if not check["ok"]:
            meta["integrity"] = {"ok": False, "problems": check["problems"]}
    if rec.extra:
        meta["extra"] = rec.extra
    entry["_agenttrace"] = {k: v for k, v in meta.items() if v is not None}
    return entry


def websocket_to_har_entry(conn: dict) -> dict:
    """A WebSocket connection as a DevTools-style HAR entry (``_webSocketMessages``)."""
    hs = conn.get("handshake") or {}
    opened = parse_iso(conn.get("opened") or "")
    closed = parse_iso(conn.get("closed") or "")
    msgs = []
    for m in conn.get("messages", []):
        ts = parse_iso(m.get("ts") or "")
        msgs.append({"type": "send" if m.get("direction") == "sent" else "receive",
                     "time": round(ts.timestamp(), 6) if ts else 0, "opcode": 1 if m.get("opcode") == "text" else 2,
                     "data": m.get("data") if m.get("opcode") == "text" else m.get("data_base64", "")})
    split = urlsplit(conn.get("url") or "")
    entry = {
        "startedDateTime": conn.get("opened") or iso(),
        "time": round((closed - opened).total_seconds() * 1000, 3) if opened and closed else 0,
        "request": {"method": "GET", "url": conn.get("url") or "", "httpVersion": "HTTP/1.1",
                    "headers": [{"name": k, "value": str(v)} for k, v in (hs.get("request_headers") or {}).items()],
                    "queryString": [{"name": k, "value": v} for k, v in parse_qsl(split.query, keep_blank_values=True)],
                    "cookies": [], "headersSize": -1, "bodySize": 0},
        "response": {"status": int(hs.get("status") or 101), "statusText": hs.get("status_text") or "Switching Protocols",
                     "httpVersion": "HTTP/1.1",
                     "headers": [{"name": k, "value": str(v)} for k, v in (hs.get("response_headers") or {}).items()],
                     "cookies": [], "content": {"size": 0, "mimeType": "x-unknown"}, "redirectURL": "",
                     "headersSize": -1, "bodySize": 0},
        "cache": {},
        "timings": {"blocked": -1, "dns": -1, "connect": -1, "ssl": -1, "send": 0, "wait": 0, "receive": 0},
        "_resourceType": "websocket",
        "_webSocketMessages": msgs,
        "_agenttrace": {k: v for k, v in {"ws_id": conn.get("id"), "page_id": conn.get("page_id"),
                                           "action_id": conn.get("action_id"), "opened": conn.get("opened"),
                                           "closed": conn.get("closed"), "error": conn.get("error"),
                                           "sent": sum(1 for m in msgs if m["type"] == "send"),
                                           "received": sum(1 for m in msgs if m["type"] == "receive")}.items()
                        if v is not None},
    }
    if conn.get("segment_id"):
        entry["pageref"] = conn["segment_id"]
    return entry


def build_har(records: Iterable[NetRecord], *, pages: Optional[list] = None,
              include_bodies: bool = True, body_limit: Optional[int] = None,
              browser: Optional[dict] = None, comment: str = "", websockets: Iterable[dict] = ()) -> dict:
    recs = sorted(records, key=lambda r: (r.t_start, r.seq))
    websockets = list(websockets or ())
    used_pages = {r.segment_id for r in recs if r.segment_id} | {w.get("segment_id") for w in websockets if w.get("segment_id")}
    har_pages = []
    for page in pages or []:
        if not used_pages or page.get("id") in used_pages:
            har_pages.append({
                "startedDateTime": page.get("startedDateTime") or iso(),
                "id": page["id"],
                "title": page.get("title") or page.get("url") or "",
                "pageTimings": {"onContentLoad": page.get("onContentLoad", -1),
                                "onLoad": page.get("onLoad", -1)},
                **({"_url": page["url"]} if page.get("url") else {}),
                **({"_virtual": True} if page.get("virtual") else {}),
                **({"_page_id": page["page_id"]} if page.get("page_id") else {}),
            })
    known = {p["id"] for p in har_pages}
    entries = []
    for rec in recs:
        entry = record_to_har_entry(rec, include_bodies=include_bodies, body_limit=body_limit)
        if entry.get("pageref") and entry["pageref"] not in known:
            entry.pop("pageref")
        entries.append(entry)
    for conn in websockets:
        entry = websocket_to_har_entry(conn)
        if entry.get("pageref") and entry["pageref"] not in known:
            entry.pop("pageref")
        entries.append(entry)
    if websockets:
        entries.sort(key=lambda e: e.get("startedDateTime") or "")
    log = {
        "version": HAR_VERSION,
        "creator": {"name": "AgentTrace", "version": __version__},
        "pages": har_pages,
        "entries": entries,
    }
    if browser:
        log["browser"] = browser
    if comment:
        log["comment"] = comment
    return {"log": log}


def save_har(path: Any, har: dict) -> Path:
    return write_json(path, har, indent=1)


def load_har(path: Any) -> dict:
    return read_json(path)


def har_entries(har: dict) -> list:
    log = har.get("log") if isinstance(har, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    return entries if isinstance(entries, list) else []


def har_with_entries(har: dict, entries: list) -> dict:
    log = dict(har.get("log") or {})
    refs = {e.get("pageref") for e in entries if isinstance(e, dict)}
    log["pages"] = [p for p in log.get("pages") or [] if p.get("id") in refs]
    log["entries"] = list(entries)
    return {"log": log}


# ═══════════════════════════════════════════════════════════════════════
# HAR -> records (works for DevTools/Firefox/Playwright/AgentTrace HARs)
# ═══════════════════════════════════════════════════════════════════════
def har_entry_to_record(entry: dict, seq: int) -> NetRecord:
    req = entry.get("request") or {}
    res = entry.get("response") or {}
    content = res.get("content") or {}
    meta = entry.get("_agenttrace") or {}
    body = None
    text = content.get("text")
    if isinstance(text, str):
        if content.get("encoding") == "base64":
            try:
                body = base64.b64decode(text)
            except (ValueError, TypeError):
                body = None
        else:
            body = text.encode("utf-8")
    req_body = None
    post = req.get("postData") or {}
    if isinstance(post.get("text"), str):
        if post.get("_encoding") == "base64" or post.get("encoding") == "base64":
            try:
                req_body = base64.b64decode(post["text"])
            except (ValueError, TypeError):
                req_body = None
        else:
            req_body = post["text"].encode("utf-8")
    elif post.get("params"):
        req_body = urlencode([(p.get("name", ""), p.get("value", "")) for p in post["params"]]).encode()
    started = entry.get("startedDateTime") or ""
    dt = parse_iso(started)
    t_start = dt.timestamp() * 1000.0 if dt else float(seq)
    total = entry.get("time")
    t_end = t_start + float(total) if isinstance(total, (int, float)) and total >= 0 else None
    status = res.get("status")
    failure = res.get("_error") or meta.get("failure") or entry.get("_error")
    if isinstance(status, (int, float)) and status <= 0:
        failure = failure or "failed"
        status = None
    headers = [{"name": h.get("name", ""), "value": h.get("value", "")}
               for h in req.get("headers") or [] if isinstance(h, dict)]
    rheaders = [{"name": h.get("name", ""), "value": h.get("value", "")}
                for h in res.get("headers") or [] if isinstance(h, dict)]
    if post.get("mimeType") and not header_value(headers, "content-type"):
        headers.append({"name": "Content-Type", "value": post["mimeType"]})
    rtype = entry.get("_resourceType") or meta.get("resource_type") or _guess_type(
        content.get("mimeType", ""), req.get("url", ""))
    initiator = entry.get("_initiator") if isinstance(entry.get("_initiator"), dict) else None
    rec = NetRecord(
        id=str(meta.get("id") or f"h{seq:05d}"),
        seq=int(meta.get("seq") or seq),
        method=str(req.get("method") or "GET").upper(),
        url=str(req.get("url") or ""),
        resource_type=str(rtype).lower(),
        is_navigation=bool(meta.get("is_navigation")) or (str(rtype).lower() == "document"),
        page_id=meta.get("page_id"),
        frame_id=meta.get("frame_id"),
        action_id=meta.get("action_id"),
        segment_id=entry.get("pageref"),
        started=started,
        t_start=t_start,
        t_end=t_end,
        status=int(status) if isinstance(status, (int, float)) else None,
        status_text=str(res.get("statusText") or ""),
        http_version=str(res.get("httpVersion") or req.get("httpVersion") or "HTTP/1.1"),
        request_headers=headers,
        response_headers=rheaders,
        request_body=req_body,
        response_body=body,
        body_note=str(content.get("comment") or meta.get("body_note") or ""),
        mime_type=str(content.get("mimeType") or ""),
        response_size=content.get("size") if isinstance(content.get("size"), int) else None,
        transfer_size=res.get("bodySize") if isinstance(res.get("bodySize"), int)
        and res.get("bodySize") >= 0 else entry.get("_transferSize"),
        failure=str(failure) if failure else None,
        from_cache=bool(meta.get("from_cache") or entry.get("_fromCache")),
        redirect_url=str(res.get("redirectURL") or ""),
        server_ip=str(entry.get("serverIPAddress") or ""),
        timings=dict(entry.get("timings") or {}),
        category=str(meta.get("category") or ""),
        tags=list(meta.get("tags") or []),
        mocked=meta.get("mocked"),
        stream=meta.get("stream"),
        initiator=meta.get("initiator") or initiator,
        extra=dict(meta.get("extra") or {}),
    )
    return rec


def _guess_type(mime: str, url: str) -> str:
    mime = (mime or "").lower()
    path = url.split("?", 1)[0].lower()
    if "html" in mime:
        return "document"
    if "css" in mime or path.endswith(".css"):
        return "stylesheet"
    if "javascript" in mime or path.endswith((".js", ".mjs")):
        return "script"
    if mime.startswith("image/") or path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp",
                                                  ".svg", ".ico", ".avif")):
        return "image"
    if "font" in mime or path.endswith((".woff", ".woff2", ".ttf", ".otf", ".eot")):
        return "font"
    if mime.startswith(("video/", "audio/")):
        return "media"
    if "event-stream" in mime:
        return "eventsource"
    if "json" in mime or "xml" in mime:
        return "fetch"
    return "other"


def har_to_records(har: Any) -> list:
    """Load a HAR dict or path and return ``NetRecord`` objects."""
    if not isinstance(har, dict):
        har = load_har(har)
    return [har_entry_to_record(e, i + 1) for i, e in enumerate(har_entries(har))
            if isinstance(e, dict)]


# ═══════════════════════════════════════════════════════════════════════
# HAR 1.2 structural validation (Part 2)
# ═══════════════════════════════════════════════════════════════════════
def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_name_values(items: Any, where: str, errors: list) -> None:
    if not isinstance(items, list):
        errors.append(f"{where} must be a list")
        return
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) \
                or not isinstance(item.get("value", ""), str):
            errors.append(f"{where}[{i}] must be an object with string name/value")


def _check_entry(entry: Any, idx: int, errors: list) -> None:
    where = f"log.entries[{idx}]"
    if not isinstance(entry, dict):
        errors.append(f"{where} must be an object")
        return
    if parse_iso(entry.get("startedDateTime", "")) is None:
        errors.append(f"{where}.startedDateTime must be ISO-8601")
    t = entry.get("time")
    if not _is_num(t) or (t < 0 and t != -1):
        errors.append(f"{where}.time must be a non-negative number (got {t!r})")
    req = entry.get("request")
    if not isinstance(req, dict):
        errors.append(f"{where}.request must be an object")
    else:
        if str(req.get("method", "")).upper() not in HTTP_METHODS:
            errors.append(f"{where}.request.method invalid: {req.get('method')!r}")
        url = req.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https", "ws", "wss"):
            errors.append(f"{where}.request.url must be an http(s) URL (got {truncate(url, 60)!r})")
        if not isinstance(req.get("httpVersion"), str):
            errors.append(f"{where}.request.httpVersion must be a string")
        _check_name_values(req.get("cookies"), f"{where}.request.cookies", errors)
        _check_name_values(req.get("headers"), f"{where}.request.headers", errors)
        _check_name_values(req.get("queryString"), f"{where}.request.queryString", errors)
        for key in ("headersSize", "bodySize"):
            if not _is_num(req.get(key)):
                errors.append(f"{where}.request.{key} must be a number")
        post = req.get("postData")
        if post is not None and (not isinstance(post, dict) or not isinstance(post.get("mimeType"), str)):
            errors.append(f"{where}.request.postData must have a mimeType")
    res = entry.get("response")
    if not isinstance(res, dict):
        errors.append(f"{where}.response must be an object")
    else:
        status = res.get("status")
        if not _is_num(status) or not (100 <= status <= 599 or status in (0, -1)):
            errors.append(f"{where}.response.status invalid: {status!r}")
        for key in ("statusText", "httpVersion", "redirectURL"):
            if not isinstance(res.get(key), str):
                errors.append(f"{where}.response.{key} must be a string")
        _check_name_values(res.get("cookies"), f"{where}.response.cookies", errors)
        _check_name_values(res.get("headers"), f"{where}.response.headers", errors)
        for key in ("headersSize", "bodySize"):
            if not _is_num(res.get(key)):
                errors.append(f"{where}.response.{key} must be a number")
        content = res.get("content")
        if not isinstance(content, dict):
            errors.append(f"{where}.response.content must be an object")
        else:
            if not isinstance(content.get("mimeType"), str):
                errors.append(f"{where}.response.content.mimeType must be a string")
            if not _is_num(content.get("size")):
                errors.append(f"{where}.response.content.size must be a number")
            if content.get("encoding") == "base64" and isinstance(content.get("text"), str):
                try:
                    base64.b64decode(content["text"], validate=True)
                except (ValueError, TypeError):
                    errors.append(f"{where}.response.content.text is not valid base64")
    if not isinstance(entry.get("cache"), dict):
        errors.append(f"{where}.cache must be an object")
    timings = entry.get("timings")
    if not isinstance(timings, dict):
        errors.append(f"{where}.timings must be an object")
    else:
        for key in ("send", "wait", "receive"):
            if not _is_num(timings.get(key)):
                errors.append(f"{where}.timings.{key} is required and must be a number")
        for key in ("blocked", "dns", "connect", "ssl"):
            if key in timings and not _is_num(timings[key]):
                errors.append(f"{where}.timings.{key} must be a number")


def validate_har(har: Any) -> list:
    """Validate against HAR 1.2 structure; returns problems (empty = valid)."""
    if not isinstance(har, dict):
        return ["HAR root must be a JSON object"]
    log = har.get("log")
    if not isinstance(log, dict):
        return ["log must be an object"]
    errors: list = []
    if log.get("version") not in ("1.1", "1.2"):
        errors.append(f"log.version must be '1.2' (got {log.get('version')!r})")
    creator = log.get("creator")
    if not isinstance(creator, dict) or not isinstance(creator.get("name"), str) \
            or not isinstance(creator.get("version"), str):
        errors.append("log.creator must have string name and version")
    pages = log.get("pages", [])
    page_ids = set()
    if not isinstance(pages, list):
        errors.append("log.pages must be a list")
    else:
        for i, page in enumerate(pages):
            where = f"log.pages[{i}]"
            if not isinstance(page, dict):
                errors.append(f"{where} must be an object")
                continue
            if not isinstance(page.get("id"), str):
                errors.append(f"{where}.id must be a string")
            else:
                page_ids.add(page["id"])
            if parse_iso(page.get("startedDateTime", "")) is None:
                errors.append(f"{where}.startedDateTime must be ISO-8601")
            if not isinstance(page.get("title"), str):
                errors.append(f"{where}.title must be a string")
            if not isinstance(page.get("pageTimings"), dict):
                errors.append(f"{where}.pageTimings must be an object")
    entries = log.get("entries")
    if not isinstance(entries, list):
        errors.append("log.entries must be a list")
    else:
        for i, entry in enumerate(entries):
            _check_entry(entry, i, errors)
            ref = entry.get("pageref") if isinstance(entry, dict) else None
            if ref is not None and page_ids and ref not in page_ids:
                errors.append(f"log.entries[{i}].pageref {ref!r} does not match any page id")
    return errors


def validate_har_file(path: Any) -> tuple:
    try:
        har = load_har(path)
    except Exception as exc:  # noqa: BLE001
        return False, [f"cannot read HAR: {exc}"]
    problems = validate_har(har)
    return not problems, problems



# ════════════════════════════════════════════════════════════════════════════
# stealth.py — anti-detection / stealth layer (Part 9)
# ════════════════════════════════════════════════════════════════════════════
# Anti-detection / stealth layer (Part 9).
#
# Measured on Chromium 141: the default *headless shell* leaks
# ``HeadlessChrome`` in the UA **and** the ``sec-ch-ua`` header, has
# ``navigator.webdriver === true``, zero plugins, no ``window.chrome`` and a
# SwiftShader WebGL renderer.  The layer therefore:
#
# 1. prefers Chromium's *new headless* mode (``channel="chromium"``) — real
#    browser build, clean client-hint brands, plugins present;
# 2. launches with ``--disable-blink-features=AutomationControlled``
#    (``navigator.webdriver`` becomes ``false`` natively);
# 3. sends a platform-consistent UA (the real one minus "Headless");
# 4. patches the remaining JS fingerprint surfaces with an init script that
#    runs before any page script, never breaking the page.
#
# ``DETECTION_PROBES_JS`` + :func:`analyse_probe` run the same checks bot
# detectors use, so stealth is *measured*, not assumed.


def stealth_launch_args() -> list:
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]


def _platform_token(system: Optional[str] = None) -> str:
    system = (system or platform.system()).lower()
    if system.startswith("win"):
        return "Windows NT 10.0; Win64; x64"
    if system == "darwin" or system.startswith("mac"):
        return "Macintosh; Intel Mac OS X 10_15_7"
    return "X11; Linux x86_64"


def stealth_user_agent(browser_version: Optional[str] = None, system: Optional[str] = None) -> str:
    """Realistic desktop Chrome UA for *this* OS (keeps UA, ``navigator.platform``
    and ``sec-ch-ua-platform`` consistent). Never contains ``Headless``."""
    major = "141"
    if browser_version:
        m = re.match(r"(\d+)", str(browser_version))
        if m:
            major = m.group(1)
    return (f"Mozilla/5.0 ({_platform_token(system)}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")


STEALTH_INIT_SCRIPT = r"""
(() => {
  if (window.__agenttraceStealth) return;
  Object.defineProperty(window, '__agenttraceStealth', { value: true, enumerable: false });
  const safe = (fn) => { try { fn(); } catch (e) { /* never break the page */ } };
  const nativeToString = Function.prototype.toString;
  const patched = new WeakSet();
  const asNative = (fn, name) => { patched.add(fn); try { Object.defineProperty(fn, 'name', { value: name }); } catch (e) {} return fn; };
  safe(() => {
    const toString = function toString() {
      if (patched.has(this)) return `function ${this.name || ''}() { [native code] }`;
      return nativeToString.call(this);
    };
    patched.add(toString);
    Function.prototype.toString = toString;
  });
  const defineGetter = (obj, prop, getter) => safe(() => {
    Object.defineProperty(obj, prop, { get: asNative(getter, 'get ' + prop), configurable: true, enumerable: true });
  });

  safe(() => { if (navigator.webdriver) defineGetter(Navigator.prototype, 'webdriver', () => false); });
  safe(() => {
    const langs = navigator.languages || [];
    if (!langs.length || langs.some(l => /@/.test(l))) defineGetter(Navigator.prototype, 'languages', () => ['en-US', 'en']);
  });
  safe(() => {
    if (navigator.plugins && navigator.plugins.length) return;
    const mime = { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' };
    const names = ['PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer', 'Microsoft Edge PDF Viewer', 'WebKit built-in PDF'];
    const plugins = names.map(name => ({ name, filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1, 0: mime }));
    plugins.item = (i) => plugins[i] || null; plugins.namedItem = (n) => plugins.find(p => p.name === n) || null; plugins.refresh = () => {};
    const mimes = [mime]; mimes.item = (i) => mimes[i] || null; mimes.namedItem = (n) => mimes.find(m => m.type === n) || null;
    defineGetter(Navigator.prototype, 'plugins', () => plugins);
    defineGetter(Navigator.prototype, 'mimeTypes', () => mimes);
  });
  safe(() => {
    const chrome = window.chrome || {};
    if (!chrome.runtime) {
      chrome.runtime = { OnInstalledReason: {}, OnRestartRequiredReason: {}, PlatformArch: {}, PlatformOs: {},
                         RequestUpdateCheckStatus: {}, connect: asNative(function connect() {}, 'connect'),
                         sendMessage: asNative(function sendMessage() {}, 'sendMessage') };
    }
    chrome.app = chrome.app || { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                                 RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };
    chrome.csi = chrome.csi || asNative(function csi() { return { onloadT: Date.now(), startE: Date.now(), pageT: 1200.5, tran: 15 }; }, 'csi');
    chrome.loadTimes = chrome.loadTimes || asNative(function loadTimes() {
      const t = Date.now() / 1000;
      return { requestTime: t, startLoadTime: t, commitLoadTime: t, finishDocumentLoadTime: t, finishLoadTime: t,
               firstPaintTime: t, firstPaintAfterLoadTime: 0, navigationType: 'Other', wasFetchedViaSpdy: true,
               wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false, connectionInfo: 'h2' };
    }, 'loadTimes');
    if (!window.chrome) Object.defineProperty(window, 'chrome', { value: chrome, configurable: true, writable: true });
  });
  safe(() => {
    const patch = (proto) => {
      if (!proto || !proto.getParameter) return;
      const original = proto.getParameter;
      proto.getParameter = asNative(function getParameter(p) {
        if (p === 37445) return 'Google Inc. (Intel)';
        if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)';
        return original.apply(this, arguments);
      }, 'getParameter');
    };
    patch(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
    patch(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  });
  safe(() => {
    if (window.Notification && Notification.permission === 'denied') defineGetter(Notification, 'permission', () => 'default');
  });
  safe(() => {
    const perms = navigator.permissions;
    if (!perms || !perms.query) return;
    const original = perms.query.bind(perms);
    perms.query = asNative(function query(params) {
      if (params && params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission === 'default' ? 'prompt' : Notification.permission, onchange: null });
      }
      return original(params);
    }, 'query');
  });
  safe(() => {
    if (!window.outerWidth) defineGetter(window, 'outerWidth', () => window.innerWidth);
    if (!window.outerHeight) defineGetter(window, 'outerHeight', () => window.innerHeight + 85);
  });
  safe(() => {
    const uad = navigator.userAgentData;
    if (uad && uad.brands && uad.brands.some(b => /Headless/i.test(b.brand))) {
      const brands = uad.brands.map(b => ({ brand: b.brand.replace(/HeadlessChrome/i, 'Google Chrome'), version: b.version }));
      defineGetter(Object.getPrototypeOf(uad), 'brands', () => brands);
    }
  });
})();
"""


DETECTION_PROBES_JS = r"""
async () => {
  const p = {};
  const safe = async (fn, fallback) => { try { return await fn(); } catch (e) { return fallback; } };
  p.userAgent = await safe(() => navigator.userAgent, '');
  p.headlessUserAgent = /Headless/i.test(p.userAgent);
  p.webdriver = await safe(() => navigator.webdriver, null);
  p.plugins = await safe(() => navigator.plugins.length, -1);
  p.languages = await safe(() => navigator.languages.length, -1);
  p.languageTags = await safe(() => Array.from(navigator.languages), []);
  p.platform = await safe(() => navigator.platform, '');
  p.brands = await safe(() => navigator.userAgentData ? navigator.userAgentData.brands.map(b => b.brand) : [], []);
  p.hasChrome = await safe(() => !!window.chrome, false);
  p.chromeRuntime = await safe(() => !!(window.chrome && window.chrome.runtime), false);
  p.outerHeight = await safe(() => window.outerHeight, -1);
  p.notification = await safe(() => Notification.permission, '');
  p.permissionNotifications = await safe(async () => (await navigator.permissions.query({ name: 'notifications' })).state, '');
  p.webglRenderer = await safe(() => {
    const gl = document.createElement('canvas').getContext('webgl');
    if (!gl) return '';
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    return info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  }, '');
  p.iframeWebdriver = await safe(() => {
    const f = document.createElement('iframe'); f.style.display = 'none'; document.body.appendChild(f);
    const v = f.contentWindow.navigator.webdriver; f.remove(); return v;
  }, null);
  p.toStringNative = await safe(() => /\[native code\]/.test(Function.prototype.toString.call(navigator.permissions.query)), true);
  return p;
}
"""


def analyse_probe(data: dict) -> list:
    """Return the automation tells found in a probe result (empty = clean)."""
    problems = []
    if data.get("headlessUserAgent"):
        problems.append(f"user-agent advertises headless: {data.get('userAgent')!r}")
    if data.get("webdriver"):
        problems.append("navigator.webdriver is true")
    if data.get("iframeWebdriver"):
        problems.append("navigator.webdriver is true inside iframes")
    if (data.get("plugins") or 0) < 1:
        problems.append("navigator.plugins is empty")
    if (data.get("languages") or 0) < 1 or any("@" in str(t) for t in data.get("languageTags") or []):
        problems.append(f"navigator.languages looks synthetic: {data.get('languageTags')!r}")
    if any("headless" in str(b).lower() for b in data.get("brands") or []):
        problems.append(f"client-hint brands advertise headless: {data.get('brands')!r}")
    if not data.get("hasChrome"):
        problems.append("window.chrome missing")
    elif not data.get("chromeRuntime"):
        problems.append("window.chrome.runtime missing")
    if "swiftshader" in str(data.get("webglRenderer", "")).lower():
        problems.append("WebGL renderer is SwiftShader (headless software GPU)")
    if (data.get("outerHeight") or 0) <= 0:
        problems.append("window.outerHeight is 0")
    if data.get("notification") == "denied" and data.get("permissionNotifications") == "prompt":
        problems.append("Notification.permission/permissions.query mismatch (headless tell)")
    if data.get("toStringNative") is False:
        problems.append("patched functions do not look native")
    return problems


def probe_summary(data: dict) -> str:
    return (f"webdriver={data.get('webdriver')!r} plugins={data.get('plugins')} "
            f"langs={data.get('languageTags')} brands={data.get('brands')} chrome={data.get('hasChrome')} "
            f"runtime={data.get('chromeRuntime')} headlessUA={data.get('headlessUserAgent')} "
            f"webgl={str(data.get('webglRenderer') or '')[:32]!r}")



# ════════════════════════════════════════════════════════════════════════════
# noise.py — network noise classification & smart filtering (Parts 10, 25)
# ════════════════════════════════════════════════════════════════════════════
# Network noise classification, smart filtering, dedup and tagging
# (Part 10 — output management, Part 25 — noise classification).
#
# Every record gets a ``category``:
#
# ``document``   top-level / frame HTML navigations
# ``api``        application data calls (XHR/fetch/JSON/GraphQL/SSE/WebSocket)
# ``static``     scripts, styles, images, fonts, media, CDN libraries
# ``analytics``  GA/GTM/Segment/Mixpanel/Amplitude/Hotjar/Clarity …
# ``ads``        DoubleClick/AdSense/Criteo/Taboola/Amazon ads …
# ``tracking``   conversion pixels (Facebook, LinkedIn, TikTok, Bing, Pinterest …)
# ``monitoring`` error/performance beacons (Sentry, New Relic, Datadog RUM …)
# ``social``     share/embed widgets
# ``consent``    cookie-consent platforms (OneTrust, Cookiebot …)
# ``antibot``    bot-protection sensors (PerimeterX, DataDome, Akamai, CF challenge)
# ``other``      anything else
#
# Rules are data (:class:`NoiseRules`) and can be extended from config files.


NOISE_CATEGORIES = ("analytics", "ads", "tracking", "monitoring", "social", "consent", "antibot")
ALL_CATEGORIES = ("document", "api", "static", *NOISE_CATEGORIES, "other")

NOISE_DOMAINS: dict = {
    "analytics": [
        "google-analytics.com", "analytics.google.com", "googletagmanager.com", "stats.g.doubleclick.net",
        "segment.io", "segment.com", "mixpanel.com", "mxpnl.com", "amplitude.com", "heap.io",
        "heapanalytics.com", "hotjar.com", "hotjar.io", "fullstory.com", "clarity.ms", "mouseflow.com",
        "crazyegg.com", "luckyorange.com", "luckyorange.net", "smartlook.com", "quantserve.com",
        "scorecardresearch.com", "chartbeat.com", "chartbeat.net", "parsely.com", "parse.ly",
        "piwik.pro", "matomo.cloud", "plausible.io", "usefathom.com", "statcounter.com", "woopra.com",
        "kissmetrics.com", "keen.io", "mc.yandex.ru", "metrika.yandex.ru", "hm.baidu.com", "cnzz.com",
        "umeng.com", "optimizely.com", "abtasty.com", "visualwebsiteoptimizer.com", "omtrdc.net",
        "2o7.net", "adobedtm.com", "tiqcdn.com", "tealiumiq.com", "ensighten.com", "branch.io",
        "app.link", "appsflyer.com", "adjust.com", "kochava.com", "braze.com", "hs-analytics.net",
        "hs-scripts.com", "hsadspixel.net", "hscollectedforms.net", "hubspot.com", "pardot.com",
        "mktoresp.com", "marketo.net", "cloudflareinsights.com", "vercel-insights.com", "pendo.io",
        "posthog.com", "i.posthog.com", "rudderstack.com", "rudderlabs.com", "snowplowanalytics.com",
        "contentsquare.net", "contentsquare.com", "clicktale.net", "quantummetric.com",
        "glassboxdigital.io", "mparticle.com", "customer.io", "klaviyo.com", "gstatic.com/firebasejs",
        "firebase-analytics", "app-measurement.com", "newrelic.com/analytics",
    ],
    "ads": [
        "doubleclick.net", "googlesyndication.com", "googleadservices.com", "adservice.google.com",
        "2mdn.net", "adnxs.com", "adsrvr.org", "amazon-adsystem.com", "criteo.com", "criteo.net",
        "taboola.com", "outbrain.com", "rubiconproject.com", "pubmatic.com", "openx.net",
        "casalemedia.com", "moatads.com", "adform.net", "advertising.com", "yieldmo.com",
        "sharethrough.com", "teads.tv", "smartadserver.com", "media.net", "bidswitch.net", "3lift.com",
        "indexww.com", "contextweb.com", "spotxchange.com", "adsafeprotected.com", "doubleverify.com",
        "serving-sys.com", "ads-twitter.com", "adroll.com", "zemanta.com", "revcontent.com", "mgid.com",
        "propellerads.com", "popads.net", "exoclick.com", "trafficjunky.net", "ads.yahoo.com",
        "adsystem.com", "adcolony.com", "applovin.com", "unityads.unity3d.com", "smaato.net",
        "inmobi.com", "yandex.ru/ads", "an.yandex.ru", "googletagservices.com", "pagead2.googlesyndication.com",
        "securepubads.g.doubleclick.net", "adsco.re", "lijit.com", "sovrn.com", "gumgum.com",
        "33across.com", "onetag-sys.com", "sonobi.com", "emxdgt.com", "e-planning.net", "improvedigital.com",
    ],
    "tracking": [
        "facebook.net", "connect.facebook.net", "px.ads.linkedin.com", "snap.licdn.com",
        "analytics.tiktok.com", "ads.tiktok.com", "ct.pinterest.com", "analytics.twitter.com",
        "static.ads-twitter.com", "t.co", "tr.snapchat.com", "sc-static.net", "bat.bing.com",
        "clarity.ms/collect", "fpjs.io", "fpcdn.io", "api.fpjs.io", "sift.com", "siftscience.com",
        "tapad.com", "bluekai.com", "krxd.net", "exelator.com", "agkn.com", "rlcdn.com",
        "demdex.net", "everesttech.net", "liadm.com", "id5-sync.com", "crwdcntrl.net", "addthis.com",
        "quora.com/_/ad", "redditstatic.com/ads", "alb.reddit.com", "pixel.wp.com", "stats.wp.com",
        "ib.adnxs.com", "match.adsrvr.org", "cm.g.doubleclick.net", "impactradius-event.com",
        "impact.com", "awin1.com", "zenaps.com", "linksynergy.com", "shareasale.com", "cj.com",
        "emxdgt.com/sync", "trustpilot.com/tracking", "nextdoor.com/pixel",
    ],
    "monitoring": [
        "sentry.io", "sentry-cdn.com", "ingest.sentry.io", "bugsnag.com", "nr-data.net",
        "js-agent.newrelic.com", "newrelic.com", "browser-intake-datadoghq.com", "datadoghq.com",
        "datadoghq-browser-agent.com", "datadoghq.eu", "rollbar.com", "raygun.io", "trackjs.com",
        "logrocket.io", "logrocket.com", "lr-ingest.io", "lr-in-prod.com", "speedcurve.com",
        "go-mpulse.net", "akstat.io", "dynatrace.com", "ruxit.com", "eum-appdynamics.com",
        "honeybadger.io", "instana.io", "elastic-cloud.com/rum", "rum.hlx.page", "stackify.com",
        "atatus.com", "appsignal.com", "highlight.io",
    ],
    "social": [
        "platform.twitter.com", "syndication.twitter.com", "platform.linkedin.com", "assets.pinterest.com",
        "widgets.pinterest.com", "sharethis.com", "s7.addthis.com", "apis.google.com/js/platform",
        "platform.instagram.com", "static.xx.fbcdn.net", "embed.tawk.to", "widget.intercom.io",
        "js.intercomcdn.com", "static.zdassets.com", "js.driftt.com", "code.tidio.co", "crisp.chat",
        "livechatinc.com", "olark.com",
    ],
    "consent": [
        "cookielaw.org", "onetrust.com", "cookiebot.com", "consentmanager.net", "consensu.org",
        "trustarc.com", "truste.com", "usercentrics.eu", "didomi.io", "cookieyes.com", "iubenda.com",
        "termly.io", "osano.com", "cookie-script.com", "cookiefirst.com", "privacy-center.org",
        "sourcepoint.com", "sp-prod.net", "fundingchoicesmessages.google.com",
    ],
    "antibot": [
        "px-cdn.net", "px-cloud.net", "perimeterx.net", "pxchk.net", "captcha.px-cdn.net",
        "datadome.co", "captcha-delivery.com", "js.datadome.co", "kasada.io", "ips.kasada.io",
        "hcaptcha.com", "recaptcha.net", "google.com/recaptcha", "gstatic.com/recaptcha",
        "challenges.cloudflare.com", "arkoselabs.com", "funcaptcha.com", "geo.captcha-delivery.com",
        "imperva.com", "incapsula.com", "distil.it", "shieldsquare.net", "botguard", "friendlycaptcha.com",
    ],
    "static": [
        "fonts.googleapis.com", "fonts.gstatic.com", "use.typekit.net", "p.typekit.net", "cdnjs.cloudflare.com",
        "cdn.jsdelivr.net", "unpkg.com", "ajax.googleapis.com", "code.jquery.com", "maxcdn.bootstrapcdn.com",
        "stackpath.bootstrapcdn.com", "use.fontawesome.com", "kit.fontawesome.com", "ka-f.fontawesome.com",
        "polyfill.io", "cdn.polyfill.io", "ajax.aspnetcdn.com", "cdn.shopify.com/s/files",
    ],
}

# (category, regex on "host/path?query") — catches first-party proxies of trackers too.
NOISE_URL_PATTERNS: list = [
    ("analytics", r"/(g|j|r)/collect(\?|$)|/collect\?v=\d|/mp/collect|/gtag/js|/gtm\.js|/analytics\.js(\?|$)|/ga\.js(\?|$)"),
    ("analytics", r"/__utm\.gif|/_vercel/insights|/cdn-cgi/zaraz|/v1/(t|track|identify|page|batch)(\?|$)"),
    ("analytics", r"/(hotjar|clarity|segment|mixpanel|amplitude|heap|matomo|piwik)(-[\w.]+)?(\.min)?\.js(\?|$)"),
    ("tracking", r"facebook\.com/tr(/|\?)|/tr/?\?id=\d+&ev=|/pixel(\.gif|\.png|/|\?)|/px\.gif|/1x1\.(gif|png)|/p\.gif\?|/beacon(\.gif)?(\?|$)"),
    ("tracking", r"/(fbevents|insight\.min|uwt|pixel)\.js"),
    ("ads", r"/(pagead|adsbygoogle|adserver|adview|adclick)\b|[?&](adunit|ad_slot|adslot)="),
    ("monitoring", r"/cdn-cgi/rum|/api/\d+/(envelope|store)/|/rum(\?|/|$)|/telemetry(\?|/|$)|/errors?/report"),
    ("antibot", r"/cdn-cgi/challenge-platform|/_Incapsula_Resource|/akam/\d+/|/_bm/|/149e9513-01fa-4fb0-aad4-566afd725d1b/|/px/client/"),
]

_STATIC_TYPES = {"script", "stylesheet", "image", "font", "media", "manifest", "texttrack", "imageset"}
_API_TYPES = {"xhr", "fetch", "eventsource", "websocket"}
_STATIC_EXT_RE = re.compile(
    r"\.(js|mjs|css|png|jpe?g|gif|webp|avif|svg|ico|bmp|woff2?|ttf|otf|eot|mp4|webm|mp3|ogg|wav|m4a|map)$", re.I)
_PING_TYPES = {"ping", "beacon", "csp_report", "cspviolationreport"}

_COMPILED_PATTERNS = [(cat, re.compile(rx, re.I)) for cat, rx in NOISE_URL_PATTERNS]


def _domain_hit(host: str, url_hostpath: str, domains: Iterable[str]) -> Optional[str]:
    for dom in domains:
        if "/" in dom:
            if dom in url_hostpath:
                return dom
        elif host == dom or host.endswith("." + dom):
            return dom
    return None


@dataclass
class NoiseRules:
    """Configurable filtering rules (JSON/YAML friendly)."""

    drop_categories: tuple = ("static", *NOISE_CATEGORIES)
    keep_patterns: tuple = ()          # regexes: always keep (wins over everything)
    drop_patterns: tuple = ()          # regexes: always drop
    extra_domains: dict = field(default_factory=dict)  # {"analytics": ["stats.example.com"]}
    first_party: tuple = ()            # hosts/sites treated as first party
    dedupe: bool = True
    keep_failed_api: bool = True

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "NoiseRules":
        data = dict(data or {})
        kwargs = {}
        for key in ("drop_categories", "keep_patterns", "drop_patterns", "first_party"):
            if key in data:
                kwargs[key] = tuple(data[key] or ())
        if "extra_domains" in data:
            kwargs["extra_domains"] = dict(data["extra_domains"] or {})
        for key in ("dedupe", "keep_failed_api"):
            if key in data:
                kwargs[key] = bool(data[key])
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return {"drop_categories": list(self.drop_categories), "keep_patterns": list(self.keep_patterns),
                "drop_patterns": list(self.drop_patterns), "extra_domains": self.extra_domains,
                "first_party": list(self.first_party), "dedupe": self.dedupe}


def classify_record(rec: NetRecord, *, first_party: Iterable[str] = (),
                    rules: Optional[NoiseRules] = None) -> tuple:
    """Return ``(category, reason)`` for one record."""
    rules = rules or NoiseRules()
    host = rec.host
    try:
        hostpath = host + rec.url.split(host, 1)[1] if host and host in rec.url else rec.url
    except IndexError:
        hostpath = rec.url
    for cat, domains in (rules.extra_domains or {}).items():
        hit = _domain_hit(host, hostpath, domains)
        if hit:
            return cat, f"rule:domain:{hit}"
    rtype = (rec.resource_type or "other").lower()
    first = {site_of(h) for h in (tuple(first_party) + tuple(rules.first_party)) if h}

    for cat in ("antibot", "ads", "tracking", "monitoring", "consent", "social", "analytics"):
        hit = _domain_hit(host, hostpath, NOISE_DOMAINS[cat])
        if hit:
            return cat, f"domain:{hit}"
    for cat, rx in _COMPILED_PATTERNS:
        if rx.search(hostpath):
            return cat, f"pattern:{rx.pattern[:40]}"
    if rtype in _PING_TYPES:
        return "analytics", "type:beacon"

    ct = (rec.content_type or "").lower()
    if rtype == "document" or (rec.is_navigation and "html" in ct):
        return "document", "type:document"
    if rtype in _API_TYPES:
        return "api", f"type:{rtype}"
    if _domain_hit(host, hostpath, NOISE_DOMAINS["static"]):
        return "static", "cdn"
    if rtype in _STATIC_TYPES or _STATIC_EXT_RE.search(rec.path or ""):
        return "static", f"type:{rtype}"
    if "json" in ct or "graphql" in ct or "event-stream" in ct:
        return "api", "mime:json"
    if rec.method not in ("GET", "HEAD") and rtype == "other":
        return "api", f"method:{rec.method}"
    if first and site_of(host) not in first and rtype == "other":
        return "other", "third-party"
    return "other", f"type:{rtype}"


def classify_records(records: Iterable[NetRecord], *, first_party: Iterable[str] = (),
                     rules: Optional[NoiseRules] = None) -> list:
    out = []
    first = tuple(first_party)
    for rec in records:
        cat, reason = classify_record(rec, first_party=first, rules=rules)
        rec.category = cat
        tag = f"noise:{reason}"
        if tag not in rec.tags:
            rec.tags = [t for t in rec.tags if not t.startswith("noise:")] + [tag]
        out.append(rec)
    return out


def is_noise(rec: NetRecord) -> bool:
    return rec.category in NOISE_CATEGORIES


def dedupe_key(rec: NetRecord) -> str:
    body = sha256_hex(rec.request_body)[:16] if rec.request_body else ""
    return f"{rec.method} {rec.url} {body}"


@dataclass
class FilterResult:
    kept: list
    dropped: list
    duplicates: int
    counts: dict  # category -> {"total", "kept", "dropped"}

    def to_dict(self) -> dict:
        return {"kept": len(self.kept), "dropped": len(self.dropped), "duplicates": self.duplicates,
                "by_category": self.counts}


def filter_records(records: Iterable[NetRecord], rules: Optional[NoiseRules] = None, *,
                   first_party: Iterable[str] = ()) -> FilterResult:
    """Classify, drop noise categories, apply keep/drop patterns and dedupe."""
    rules = rules or NoiseRules()
    keep_rx = [re.compile(p, re.I) for p in rules.keep_patterns]
    drop_rx = [re.compile(p, re.I) for p in rules.drop_patterns]
    kept, dropped = [], []
    counts: dict = {}
    seen: dict = {}
    duplicates = 0
    for rec in classify_records(list(records), first_party=first_party, rules=rules):
        c = counts.setdefault(rec.category, {"total": 0, "kept": 0, "dropped": 0})
        c["total"] += 1
        decision = rec.category not in rules.drop_categories
        if any(rx.search(rec.url) for rx in drop_rx):
            decision = False
        if any(rx.search(rec.url) for rx in keep_rx):
            decision = True
        if decision and rules.dedupe:
            key = dedupe_key(rec)
            if key in seen and rec.category != "document":
                first = seen[key]
                first.extra["duplicates"] = first.extra.get("duplicates", 0) + 1
                duplicates += 1
                decision = False
                if "dup" not in rec.tags:
                    rec.tags.append("dup")
            else:
                seen[key] = rec
        if decision:
            kept.append(rec)
            c["kept"] += 1
        else:
            dropped.append(rec)
            c["dropped"] += 1
    return FilterResult(kept=kept, dropped=dropped, duplicates=duplicates, counts=counts)


def noise_report(records: Iterable[NetRecord], rules: Optional[NoiseRules] = None, *,
                 first_party: Iterable[str] = ()) -> dict:
    """Page/request/fail counts + per-category breakdown (Part 10 summary)."""
    recs = list(records)
    res = filter_records(recs, rules, first_party=first_party)
    failed_all = [r for r in recs if r.failure or (r.status is not None and r.status >= 400)]
    failed = [r for r in failed_all if r.category not in NOISE_CATEGORIES and r.category != "static"]
    docs = [r for r in recs if r.category == "document"]
    vendors: dict = {}
    for rec in recs:
        if rec.category in NOISE_CATEGORIES:
            vendors.setdefault(rec.category, set()).add(site_of(rec.host))
    return {
        "requests_total": len(recs),
        "pages": len({r.url for r in docs}),
        "failed": len(failed),
        "failed_noise_or_static": len(failed_all) - len(failed),
        "failed_samples": [{"url": r.url, "status": r.status, "failure": r.failure} for r in failed[:10]],
        "kept": len(res.kept),
        "dropped": len(res.dropped),
        "duplicates_removed": res.duplicates,
        "by_category": res.counts,
        "noise_vendors": {k: sorted(v) for k, v in vendors.items()},
        "api_calls": [f"{r.method} {r.url}" for r in res.kept if r.category == "api"][:50],
    }


def looks_like_background(rec: NetRecord) -> bool:
    """Requests that should not block 'network idle' (beacons, streams, trackers)."""
    if rec.resource_type in ("eventsource", "websocket", "ping", "beacon"):
        return True
    if "event-stream" in (header_value(rec.response_headers, "content-type") or "").lower():
        return True
    cat, _ = classify_record(rec)
    return cat in NOISE_CATEGORIES


def category_counts(records: Iterable[Any]) -> dict:
    out: dict = {}
    for rec in records:
        out[rec.category or "unclassified"] = out.get(rec.category or "unclassified", 0) + 1
    return out



# ════════════════════════════════════════════════════════════════════════════
# redact.py — security & data redaction (Part 12)
# ════════════════════════════════════════════════════════════════════════════
# Security & data redaction (Part 12).
#
# Two-phase design so *no* raw secret survives anywhere in a HAR/report:
#
# 1. **learn** — collect every secret value: Cookie/Set-Cookie values,
#    Authorization/API-key headers, token/password/secret fields in JSON, form
#    and multipart bodies, sensitive query parameters, hidden CSRF inputs,
#    plus well-known token formats (JWT, AWS/Google/Stripe/GitHub keys …);
# 2. **mask** — replace them structurally (headers, cookies, query strings,
#    JSON/form bodies) *and* textually everywhere else (URLs, redirect targets,
#    HTML, SSE data, custom fields) including URL-encoded/JSON-escaped forms.
#
# Placeholders look like ``[REDACTED:cookie:1a2b3c4d]`` — the suffix is an
# HMAC with a per-process random key: equal secrets get equal placeholders
# (correlation inside one file) but values cannot be guessed offline.


SENSITIVE_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key", "apikey",
    "x-auth-token", "x-access-token", "x-csrf-token", "x-xsrf-token", "x-csrftoken", "x-amz-security-token",
    "x-goog-api-key", "x-session-id", "x-session-token", "x-refresh-token", "x-client-secret",
    "x-shopify-access-token", "x-algolia-api-key", "x-firebase-appcheck", "x-hub-signature",
    "x-hub-signature-256", "x-signature", "x-apikey", "x-token", "x-user-token", "x-id-token",
    "ocp-apim-subscription-key", "private-token", "x-amz-signature",
}

_SENSITIVE_EXACT = {
    "password", "passwd", "pwd", "pass", "passphrase", "secret", "clientsecret", "token", "accesstoken",
    "refreshtoken", "idtoken", "authtoken", "sessiontoken", "csrftoken", "xsrftoken", "csrf", "xsrf",
    "csrfmiddlewaretoken", "authenticitytoken", "apikey", "apisecret", "accesskey", "secretkey",
    "privatekey", "auth", "authorization", "bearer", "jwt", "session", "sessionid", "sid", "ssid",
    "otp", "totp", "pin", "cvv", "cvc", "cardnumber", "ccnumber", "creditcard", "ssn", "signature",
    "sig", "hmac", "newpassword", "oldpassword", "currentpassword", "confirmpassword", "passwordconfirm",
    "phpsessid", "jsessionid", "aspsessionid", "connectsid", "sessionkey", "securitytoken",
}
_SENSITIVE_SUFFIXES = ("token", "secret", "password", "passwd", "apikey", "sessionid", "privatekey",
                       "accesskey", "secretkey", "signature", "credential", "credentials")
_QUERY_ONLY_KEYS = {"key", "code", "access_token", "auth", "sig", "signature", "x-amz-signature",
                    "x-amz-credential", "x-amz-security-token"}

TOKEN_PATTERNS = [
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/-]{12,}=*")),
    ("aws_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe_secret", re.compile(r"\b(sk|rk)_(live|test)_[0-9A-Za-z]{16,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
]
_HIDDEN_INPUT_RE = re.compile(
    r"""<input\b[^>]*\bname\s*=\s*["']([^"']+)["'][^>]*\bvalue\s*=\s*["']([^"']*)["']|"""
    r"""<input\b[^>]*\bvalue\s*=\s*["']([^"']*)["'][^>]*\bname\s*=\s*["']([^"']+)["']""", re.I)
_META_TOKEN_RE = re.compile(
    r"""<meta\b[^>]*\bname\s*=\s*["'](csrf[-_]?token|xsrf[-_]?token|_token|csrf-param)["'][^>]*\bcontent\s*=\s*["']([^"']+)["']""",
    re.I)


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: str, *, in_query: bool = False, extra: Iterable[str] = ()) -> bool:
    raw = str(key).lower()
    k = _norm_key(key)
    if not k:
        return False
    if k in _SENSITIVE_EXACT or raw in {e.lower() for e in extra} or k in {_norm_key(e) for e in extra}:
        return True
    if in_query and raw in _QUERY_ONLY_KEYS:
        return True
    return any(k.endswith(sfx) and len(k) > len(sfx) - 1 for sfx in _SENSITIVE_SUFFIXES)


def is_sensitive_header(name: str, extra: Iterable[str] = ()) -> bool:
    lname = str(name).lower()
    return lname in SENSITIVE_HEADERS or lname in {e.lower() for e in extra} or (
        lname.startswith("x-") and is_sensitive_key(lname[2:]))


class Redactor:
    """Learns secrets, then masks them everywhere. Reusable across files."""

    def __init__(self, *, extra_headers: Iterable[str] = (), extra_keys: Iterable[str] = (),
                 known_secrets: Iterable[str] = (), min_secret_len: int = 6,
                 key: Optional[bytes] = None) -> None:
        self.extra_headers = tuple(extra_headers)
        self.extra_keys = tuple(extra_keys)
        self.min_secret_len = min_secret_len
        self._key = key or secrets.token_bytes(16)
        self._secrets: dict = {}  # value -> kind
        for value in known_secrets:
            self.learn(value, "secret")

    # ---- registry ----------------------------------------------------
    @property
    def secrets(self) -> dict:
        return dict(self._secrets)

    def learn(self, value: Any, kind: str = "secret") -> None:
        if value is None:
            return
        text = str(value).strip()
        if len(text) < self.min_secret_len or text.startswith("[REDACTED:"):
            return
        if text.lower() in ("true", "false", "null", "undefined", "none", "bearer"):
            return
        self._secrets.setdefault(text, kind)
        if text.lower().startswith("bearer "):
            self.learn(text[7:], kind)
        if text.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(text[6:]).decode("utf-8", "replace")
                user, _, pw = decoded.partition(":")
                self.learn(pw, "password")
            except (ValueError, TypeError):
                pass

    def placeholder(self, kind: str, value: str) -> str:
        return f"[REDACTED:{kind}:{keyed_digest(self._key, str(value))}]"

    # ---- learning ----------------------------------------------------
    def _learn_cookie_header(self, value: str) -> None:
        for part in value.split(";"):
            name, sep, val = part.strip().partition("=")
            if sep:
                self.learn(val, "cookie")

    def _learn_json(self, obj: Any, parent_sensitive: bool = False) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                sens = is_sensitive_key(k, extra=self.extra_keys)
                if isinstance(v, (dict, list)):
                    self._learn_json(v, sens or parent_sensitive)
                elif sens or parent_sensitive:
                    if isinstance(v, (str, int)) and not isinstance(v, bool):
                        self.learn(v, "field")
        elif isinstance(obj, list):
            for item in obj:
                self._learn_json(item, parent_sensitive)

    def learn_text(self, text: Optional[str], content_type: str = "") -> None:
        if not text:
            return
        ct = (content_type or "").lower()
        stripped = text.lstrip()
        if "json" in ct or stripped[:1] in ("{", "["):
            try:
                self._learn_json(json.loads(text))
            except ValueError:
                pass
        if "x-www-form-urlencoded" in ct or ("=" in text and "&" in text and "<" not in text[:200]):
            for k, v in parse_qsl(text, keep_blank_values=True):
                if is_sensitive_key(k, extra=self.extra_keys):
                    self.learn(v, "field")
        if "<" in text[:2000] or "html" in ct:
            for m in _HIDDEN_INPUT_RE.finditer(text):
                name = m.group(1) or m.group(4) or ""
                value = m.group(2) if m.group(1) else m.group(3)
                if is_sensitive_key(name, extra=self.extra_keys):
                    self.learn(value, "field")
            for m in _META_TOKEN_RE.finditer(text):
                self.learn(m.group(2), "field")
        for kind, rx in TOKEN_PATTERNS:
            for m in rx.finditer(text):
                self.learn(m.group(0), kind)

    def _learn_url(self, url: str) -> None:
        try:
            query = urlsplit(url).query
        except ValueError:
            return
        for k, v in parse_qsl(query, keep_blank_values=True):
            if is_sensitive_key(k, in_query=True, extra=self.extra_keys):
                self.learn(v, "query")

    def _learn_headers(self, headers: Iterable[dict]) -> None:
        for h in headers or ():
            name = str(h.get("name", "")).lower()
            value = str(h.get("value", ""))
            if name == "cookie":
                self._learn_cookie_header(value)
            elif name == "set-cookie":
                for line in value.split("\n"):
                    first = line.split(";", 1)[0]
                    _n, sep, val = first.partition("=")
                    if sep:
                        self.learn(val.strip(), "cookie")
            elif is_sensitive_header(name, self.extra_headers):
                self.learn(value, "header")

    def learn_records(self, records: Iterable[NetRecord]) -> "Redactor":
        for rec in records:
            self._learn_url(rec.url)
            if rec.redirect_url:
                self._learn_url(rec.redirect_url)
            self._learn_headers(rec.request_headers)
            self._learn_headers(rec.response_headers)
            if rec.request_body:
                ct = rec.request_content_type
                if "multipart" in ct.lower():
                    for part in parse_multipart(rec.request_body, ct):
                        if part["filename"] is None and is_sensitive_key(part["name"], extra=self.extra_keys):
                            self.learn(part["data"].decode("utf-8", "replace"), "field")
                else:
                    self.learn_text(rec.request_text(), ct)
            if rec.response_body:
                self.learn_text(rec.text(), rec.content_type)
        return self

    def learn_har(self, har: dict) -> "Redactor":
        for entry in (har.get("log") or {}).get("entries") or []:
            req = entry.get("request") or {}
            res = entry.get("response") or {}
            self._learn_url(req.get("url", ""))
            self._learn_url(res.get("redirectURL", ""))
            self._learn_headers(req.get("headers") or [])
            self._learn_headers(res.get("headers") or [])
            for c in (req.get("cookies") or []) + (res.get("cookies") or []):
                self.learn(c.get("value"), "cookie")
            post = req.get("postData") or {}
            if post.get("text") and not post.get("_encoding"):
                self.learn_text(post["text"], post.get("mimeType", ""))
            for p in post.get("params") or []:
                if is_sensitive_key(p.get("name", ""), extra=self.extra_keys):
                    self.learn(p.get("value"), "field")
            content = res.get("content") or {}
            if content.get("text") and content.get("encoding") != "base64":
                self.learn_text(content["text"], content.get("mimeType", ""))
        return self

    # ---- masking -----------------------------------------------------
    def _sorted_secrets(self) -> list:
        return sorted(self._secrets.items(), key=lambda kv: len(kv[0]), reverse=True)

    def mask_text(self, text: Optional[str]) -> Optional[str]:
        """Replace every known secret (raw, URL-encoded, JSON-escaped) + token patterns."""
        if not text:
            return text
        out = text
        for kind, rx in TOKEN_PATTERNS:
            out = rx.sub(lambda m, k=kind: self.placeholder(k, m.group(0)), out)
        for value, kind in self._sorted_secrets():
            ph = self.placeholder(kind, value)
            forms = {value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1]}
            for form in sorted(forms, key=len, reverse=True):
                if form and form in out:
                    out = out.replace(form, ph)
        return out

    def _mask_json(self, obj: Any, parent_sensitive: bool = False) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                sens = is_sensitive_key(k, extra=self.extra_keys) or parent_sensitive
                if isinstance(v, (dict, list)):
                    out[k] = self._mask_json(v, sens)
                elif sens and v is not None and not isinstance(v, bool) and str(v) != "":
                    out[k] = self.placeholder("field", str(v))
                elif isinstance(v, str):
                    out[k] = self.mask_text(v)
                else:
                    out[k] = v
            return out
        if isinstance(obj, list):
            return [self._mask_json(v, parent_sensitive) for v in obj]
        if isinstance(obj, str):
            return self.mask_text(obj)
        return obj

    def mask_body(self, text: Optional[str], content_type: str = "") -> Optional[str]:
        if not text:
            return text
        ct = (content_type or "").lower()
        stripped = text.lstrip()
        if "json" in ct or stripped[:1] in ("{", "["):
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if data is not None:
                return json.dumps(self._mask_json(data), ensure_ascii=False)
        if "x-www-form-urlencoded" in ct:
            pairs = parse_qsl(text, keep_blank_values=True)
            masked = [(k, self.placeholder("field", v) if is_sensitive_key(k, extra=self.extra_keys) and v
                       else (self.mask_text(v) or "")) for k, v in pairs]
            return urlencode(masked)
        return self.mask_text(text)

    def mask_url(self, url: str) -> str:
        if not url:
            return url
        try:
            parts = urlsplit(url)
        except ValueError:
            return self.mask_text(url) or url
        if parts.query:
            pairs = parse_qsl(parts.query, keep_blank_values=True)
            masked = [(k, self.placeholder("query", v) if v and is_sensitive_key(k, in_query=True, extra=self.extra_keys)
                       else v) for k, v in pairs]
            query = urlencode(masked, safe="[]:")
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
        return self.mask_text(url) or url

    def mask_headers(self, headers: Iterable[dict]) -> list:
        out = []
        for h in headers or ():
            name = str(h.get("name", ""))
            value = str(h.get("value", ""))
            lname = name.lower()
            if lname == "cookie":
                parts = []
                for part in value.split(";"):
                    n, sep, v = part.strip().partition("=")
                    parts.append(f"{n}={self.placeholder('cookie', v)}" if sep else part.strip())
                value = "; ".join(p for p in parts if p)
            elif lname == "set-cookie":
                lines = []
                for line in value.split("\n"):
                    first, sep, attrs = line.partition(";")
                    n, eq, v = first.partition("=")
                    lines.append(f"{n.strip()}={self.placeholder('cookie', v.strip())}" + (sep + attrs if sep else "")
                                 if eq else line)
                value = "\n".join(lines)
            elif is_sensitive_header(lname, self.extra_headers):
                scheme = ""
                if lname in ("authorization", "proxy-authorization") and " " in value:
                    scheme = value.split(" ", 1)[0] + " "
                value = scheme + self.placeholder("header", value)
            else:
                value = self.mask_text(value) or value
            out.append({"name": name, "value": value})
        return out

    def redact_record(self, rec: NetRecord) -> NetRecord:
        clone = copy.copy(rec)
        clone.url = self.mask_url(rec.url)
        clone.redirect_url = self.mask_url(rec.redirect_url)
        clone.request_headers = self.mask_headers(rec.request_headers)
        clone.response_headers = self.mask_headers(rec.response_headers)
        if rec.request_body is not None:
            ct = rec.request_content_type
            if "multipart" in ct.lower():
                clone.request_body = self._mask_multipart(rec.request_body, ct)
            else:
                text = rec.request_text()
                clone.request_body = (self.mask_body(text, ct) or "").encode("utf-8")
        if rec.response_body is not None and _texty(rec.content_type, rec.response_body):
            clone.response_body = (self.mask_body(rec.text(), rec.content_type) or "").encode("utf-8")
        if rec.stream:
            clone.stream = json.loads(self.mask_text(json.dumps(rec.stream)) or "{}")
        if rec.mocked:
            clone.mocked = json.loads(self.mask_text(json.dumps(rec.mocked, default=str)) or "{}")
        clone.tags = list(rec.tags) + ["redacted"]
        return clone

    def _mask_multipart(self, data: bytes, content_type: str) -> bytes:
        text = data.decode("utf-8", "replace")
        for part in parse_multipart(data, content_type):
            if part["filename"] is None and is_sensitive_key(part["name"], extra=self.extra_keys):
                raw = part["data"].decode("utf-8", "replace")
                if raw:
                    text = text.replace(raw, self.placeholder("field", raw))
        return (self.mask_text(text) or "").encode("utf-8")

    def redact_records(self, records: Iterable[NetRecord], *, learn: bool = True) -> list:
        recs = list(records)
        if learn:
            self.learn_records(recs)
        return [self.redact_record(r) for r in recs]

    def redact_har(self, har: dict, *, learn: bool = True) -> dict:
        """Return a redacted deep copy of a HAR dict (input is not modified)."""
        if learn:
            self.learn_har(har)
        out = copy.deepcopy(har)
        for entry in (out.get("log") or {}).get("entries") or []:
            req = entry.get("request") or {}
            res = entry.get("response") or {}
            req["url"] = self.mask_url(req.get("url", ""))
            req["headers"] = self.mask_headers(req.get("headers") or [])
            res["headers"] = self.mask_headers(res.get("headers") or [])
            for c in req.get("cookies") or []:
                c["value"] = self.placeholder("cookie", str(c.get("value", "")))
            for c in res.get("cookies") or []:
                c["value"] = self.placeholder("cookie", str(c.get("value", "")))
            for q in req.get("queryString") or []:
                if q.get("value") and is_sensitive_key(q.get("name", ""), in_query=True, extra=self.extra_keys):
                    q["value"] = self.placeholder("query", q["value"])
                else:
                    q["value"] = self.mask_text(q.get("value", "")) or ""
            post = req.get("postData")
            if isinstance(post, dict):
                if isinstance(post.get("text"), str) and not post.get("_encoding"):
                    post["text"] = self.mask_body(post["text"], post.get("mimeType", ""))
                for p in post.get("params") or []:
                    if "value" in p:
                        p["value"] = (self.placeholder("field", p["value"])
                                      if p["value"] and is_sensitive_key(p.get("name", ""), extra=self.extra_keys)
                                      else self.mask_text(p["value"]))
            res["redirectURL"] = self.mask_url(res.get("redirectURL", ""))
            content = res.get("content") or {}
            if isinstance(content.get("text"), str) and content.get("encoding") != "base64":
                content["text"] = self.mask_body(content["text"], content.get("mimeType", ""))
        # final textual sweep over everything (custom fields, page titles, comments)
        text = json.dumps(out, ensure_ascii=False)
        swept = self.mask_text(text) or text
        try:
            out = json.loads(swept)
        except ValueError:
            pass
        log = out.setdefault("log", {})
        log["comment"] = (log.get("comment", "") + " [redacted by AgentTrace]").strip()
        return out

    # ---- verification --------------------------------------------------
    def find_leaks(self, text: str) -> list:
        """Secrets (known + token patterns) still present in ``text``."""
        leaks = []
        for value, kind in self._secrets.items():
            forms = {value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1]}
            if any(f and f in text for f in forms):
                leaks.append({"kind": kind, "preview": value[:3] + "…", "length": len(value)})
        for kind, rx in TOKEN_PATTERNS:
            for m in rx.finditer(text):
                if "[REDACTED:" not in m.group(0):
                    leaks.append({"kind": kind, "preview": m.group(0)[:6] + "…"})
        return leaks


def _texty(content_type: str, data: Optional[bytes]) -> bool:
    if is_text_mime(content_type):
        return True
    if not data:
        return False
    sample = data[:2048]
    return sum(1 for b in sample if b < 9 or (13 < b < 32)) == 0


def redact_har(har: dict, **kwargs: Any) -> dict:
    return Redactor(**kwargs).redact_har(har)


def mask_headers_for_log(headers: Iterable[dict]) -> list:
    """Cheap masking for logs/summaries (no learning required)."""
    out = []
    for h in headers or ():
        name = str(h.get("name", ""))
        value = str(h.get("value", ""))
        if is_sensitive_header(name):
            value = f"[REDACTED:{len(value)} chars]"
        out.append({"name": name, "value": value})
    return out



# ════════════════════════════════════════════════════════════════════════════
# engine.py — browser engine + browser/network profiles (Parts 1, 47)
# ════════════════════════════════════════════════════════════════════════════
# Browser engine & environment profile (Part 1 core engine, Part 9 stealth
# wiring, Part 47 proxy / network profile / environment control).
#
# ``BrowserProfile`` is a plain, JSON/YAML-friendly description of *how* a
# browser should look and behave (stealth, proxy, headers, UA, locale,
# timezone, geolocation, throttling, host mapping …).  ``BrowserEngine``
# launches the browser once and creates isolated contexts from a profile, so
# two sessions with different profiles never leak into each other.


BROWSER_TYPES = ("chromium", "firefox", "webkit")


def parse_proxy(proxy: Any) -> Optional[dict]:
    """``"http://user:pass@host:3128"`` or a dict → Playwright proxy dict."""
    if not proxy:
        return None
    if isinstance(proxy, dict):
        if not proxy.get("server"):
            raise ConfigError("proxy dict needs a 'server' key", hint='{"server": "http://host:port"}')
        return {k: v for k, v in proxy.items() if k in ("server", "username", "password", "bypass") and v}
    text = str(proxy).strip()
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if not parts.hostname:
        raise ConfigError(f"invalid proxy URL {proxy!r}")
    server = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    out = {"server": server}
    if parts.username:
        out["username"] = unquote(parts.username)
    if parts.password:
        out["password"] = unquote(parts.password)
    return out


@dataclass
class BrowserProfile:
    """Everything about the browser environment, in one serialisable object."""

    browser: str = "chromium"
    channel: Optional[str] = None
    headless: bool = True
    executable_path: Optional[str] = None
    stealth: bool = False
    user_agent: Optional[str] = None
    locale: Optional[str] = "en-US"
    timezone_id: Optional[str] = None
    viewport: Optional[dict] = field(default_factory=lambda: {"width": 1366, "height": 850})
    device: Optional[str] = None
    extra_headers: dict = field(default_factory=dict)
    proxy: Any = None
    http_credentials: Optional[dict] = None
    geolocation: Optional[dict] = None
    permissions: list = field(default_factory=list)
    color_scheme: Optional[str] = None
    reduced_motion: Optional[str] = None
    java_script_enabled: bool = True
    ignore_https_errors: bool = False
    offline: bool = False
    throttle: Optional[dict] = None
    service_workers: str = "block"
    host_map: dict = field(default_factory=dict)
    launch_args: list = field(default_factory=list)
    no_proxy_server: bool = False
    slow_mo_ms: int = 0
    navigation_timeout_ms: int = 45_000
    action_timeout_ms: int = 15_000
    storage_state: Any = None
    downloads_dir: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "BrowserProfile":
        data = dict(data or {})
        known = {f.name for f in fields(cls)}
        aliases = {"headers": "extra_headers", "timezone": "timezone_id", "ua": "user_agent",
                   "useragent": "user_agent", "type": "browser", "headed": None}
        kwargs: dict = {}
        for key, value in data.items():
            k = aliases.get(key.lower(), key) if key.lower() in aliases else key
            if key.lower() == "headed":
                kwargs["headless"] = not bool(value)
                continue
            if k not in known:
                raise ConfigError(f"unknown browser profile option {key!r}",
                                  hint=f"valid options: {sorted(known)}")
            kwargs[k] = value
        profile = cls(**kwargs)
        profile.validate()
        return profile

    def to_dict(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "proxy" and isinstance(value, dict) and value.get("password"):
                value = {**value, "password": "***"}
            out[f.name] = value
        return out

    def merged(self, **overrides: Any) -> "BrowserProfile":
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        for key, value in overrides.items():
            if key not in data:
                raise ConfigError(f"unknown browser profile option {key!r}")
            data[key] = value
        return BrowserProfile(**data)

    def validate(self) -> None:
        if self.browser not in BROWSER_TYPES:
            raise ConfigError(f"browser must be one of {BROWSER_TYPES}, got {self.browser!r}")
        if self.navigation_timeout_ms <= 0 or self.action_timeout_ms <= 0:
            raise ConfigError("timeouts must be > 0")
        if self.throttle is not None and not isinstance(self.throttle, dict):
            raise ConfigError("throttle must be a dict like {'latency_ms': 200, 'download_kbps': 1500}")
        parse_proxy(self.proxy)


def _playwright_cache_dirs() -> list:
    dirs = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        dirs.append(Path(env))
    system = platform.system().lower()
    home = Path.home()
    if system.startswith("win"):
        dirs.append(Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "ms-playwright")
    elif system == "darwin":
        dirs.append(home / "Library" / "Caches" / "ms-playwright")
    else:
        dirs.append(home / ".cache" / "ms-playwright")
    return dirs


def discover_chromium_binaries() -> list:
    """Locally installed Playwright Chromium builds (newest first), any OS."""
    patterns = [
        "chromium-*/chrome-win*/chrome.exe",
        "chromium-*/chrome-linux*/chrome",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    ]
    found = []
    for base in _playwright_cache_dirs():
        if not base.is_dir():
            continue
        for pattern in patterns:
            for exe in base.glob(pattern):
                m = re.search(r"chromium-(\d+)", str(exe))
                if m and exe.exists():
                    found.append((int(m.group(1)), str(exe)))
    found.sort(reverse=True)
    return [path for _rev, path in found]


class BrowserEngine:
    """Owns the Playwright runtime + one browser; creates profile-driven contexts.

    >>> with BrowserEngine(BrowserProfile(stealth=True)) as engine:
    ...     ctx = engine.new_context()
    """

    def __init__(self, profile: Any = None, **overrides: Any) -> None:
        if profile is None:
            profile = BrowserProfile()
        elif isinstance(profile, dict):
            profile = BrowserProfile.from_dict(profile)
        if overrides:
            profile = profile.merged(**overrides)
        profile.validate()
        self.profile: BrowserProfile = profile
        self._pw = None
        self._browser = None
        self.launch_info: dict = {}
        self._log = get_logger("engine")

    # ---- lifecycle ---------------------------------------------------
    def start(self) -> "BrowserEngine":
        if self._browser is not None:
            return self
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BrowserError("Playwright is not installed",
                               hint="pip install playwright && python -m playwright install chromium") from exc
        try:
            self._pw = sync_playwright().start()
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"could not start Playwright: {exc}",
                               hint="Playwright's sync API cannot run inside an asyncio event loop "
                                    "(e.g. Jupyter). Run from a normal script/thread.") from exc
        p = self.profile
        btype = getattr(self._pw, p.browser)
        args = list(p.launch_args)
        if p.browser == "chromium":
            if p.stealth:
                args += [a for a in stealth_launch_args() if a not in args]
            if p.host_map:
                rules = ", ".join(f"EXCLUDE {h}" if t in (None, "", "EXCLUDE") else f"MAP {h} {t}"
                                  for h, t in p.host_map.items())
                args.append(f"--host-resolver-rules={rules}")
            if p.no_proxy_server:
                args.append("--no-proxy-server")
        kwargs: dict = {"headless": p.headless}
        if args:
            kwargs["args"] = args
        if p.slow_mo_ms:
            kwargs["slow_mo"] = p.slow_mo_ms
        if p.downloads_dir:
            kwargs["downloads_path"] = str(Path(p.downloads_dir).resolve())

        attempts = []
        if p.executable_path:
            attempts.append(("executable", {"executable_path": p.executable_path}))
        else:
            if p.channel:
                attempts.append((f"channel:{p.channel}", {"channel": p.channel}))
            elif p.browser == "chromium" and p.stealth and p.headless:
                attempts.append(("channel:chromium(new-headless)", {"channel": "chromium"}))
            attempts.append(("bundled", {}))
            if p.browser == "chromium":
                for exe in discover_chromium_binaries()[:3]:
                    attempts.append((f"local:{exe}", {"executable_path": exe}))
                for channel in ("chrome", "msedge"):
                    if channel != p.channel:
                        attempts.append((f"system:{channel}", {"channel": channel}))
        errors = []
        for label, extra in attempts:
            try:
                self._browser = btype.launch(**kwargs, **extra)
                self.launch_info = {"browser": p.browser, "how": label, "version": self._browser.version,
                                    "headless": p.headless, "args": args}
                if errors:
                    self._log.warning("browser launched via fallback %s (earlier: %s)", label,
                                      "; ".join(e.splitlines()[0][:120] for e in errors))
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")
        if self._browser is None:
            self._stop_pw()
            raise BrowserError(
                f"could not launch {p.browser}",
                hint=f"install it with: python -m playwright install {p.browser}",
                details={"attempts": [e.splitlines()[0][:300] for e in errors]})
        self._log.info("browser ready: %s %s (%s, headless=%s)", p.browser, self._browser.version,
                       self.launch_info["how"], p.headless)
        return self

    def _stop_pw(self) -> None:
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception as exc:  # noqa: BLE001
                self._log.debug("browser close: %s", exc)
            self._browser = None
        self._stop_pw()

    def __enter__(self) -> "BrowserEngine":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def started(self) -> bool:
        return self._browser is not None

    @property
    def browser(self):
        if self._browser is None:
            raise BrowserError("engine not started; call start() first")
        return self._browser

    @property
    def playwright(self):
        if self._pw is None:
            raise BrowserError("engine not started; call start() first")
        return self._pw

    @property
    def version(self) -> str:
        return self._browser.version if self._browser else ""

    # ---- contexts ----------------------------------------------------
    def context_options(self, profile: Optional[BrowserProfile] = None, **overrides: Any) -> dict:
        p = profile or self.profile
        opts: dict = {"accept_downloads": True}
        if p.device:
            try:
                opts.update(self.playwright.devices[p.device])
            except KeyError as exc:
                raise ConfigError(f"unknown device {p.device!r}",
                                  hint="see playwright.devices, e.g. 'iPhone 13', 'Pixel 7'") from exc
        elif p.viewport:
            opts["viewport"] = dict(p.viewport)
        if p.user_agent:
            opts["user_agent"] = p.user_agent
        elif p.stealth and p.browser == "chromium" and not p.device:
            opts["user_agent"] = stealth_user_agent(self.version)
        for key in ("locale", "timezone_id", "geolocation", "color_scheme", "reduced_motion",
                    "http_credentials"):
            value = getattr(p, key)
            if value:
                opts[key] = value
        if p.permissions:
            opts["permissions"] = list(p.permissions)
        if not p.java_script_enabled:
            opts["java_script_enabled"] = False
        if p.ignore_https_errors:
            opts["ignore_https_errors"] = True
        if p.offline:
            opts["offline"] = True
        if p.extra_headers:
            opts["extra_http_headers"] = {str(k): str(v) for k, v in p.extra_headers.items()}
        proxy = parse_proxy(p.proxy)
        if proxy:
            opts["proxy"] = proxy
        if p.service_workers in ("block", "allow"):
            opts["service_workers"] = p.service_workers
        state = p.storage_state
        if isinstance(state, (str, Path)):
            if Path(state).is_file():
                opts["storage_state"] = str(state)
        elif isinstance(state, dict):
            opts["storage_state"] = state
        opts.update(overrides)
        return opts

    def new_context(self, profile: Optional[BrowserProfile] = None, **overrides: Any):
        """New isolated context (own cookies/cache/storage) configured by profile."""
        p = profile or self.profile
        opts = self.context_options(p, **overrides)
        try:
            context = self.browser.new_context(**opts)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"could not create browser context: {exc}",
                               details={"options": sorted(opts)}) from exc
        context.set_default_timeout(p.action_timeout_ms)
        context.set_default_navigation_timeout(p.navigation_timeout_ms)
        if p.stealth:
            context.add_init_script(STEALTH_INIT_SCRIPT)
        return context

    def apply_page_profile(self, page, profile: Optional[BrowserProfile] = None) -> None:
        """Per-page settings that need CDP (network throttling)."""
        p = profile or self.profile
        if not p.throttle or p.browser != "chromium":
            return
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            t = p.throttle
            cdp.send("Network.emulateNetworkConditions", {
                "offline": bool(t.get("offline", False)),
                "latency": float(t.get("latency_ms", 0)),
                "downloadThroughput": float(t.get("download_kbps", -1)) * 1024 / 8
                if t.get("download_kbps") else -1,
                "uploadThroughput": float(t.get("upload_kbps", -1)) * 1024 / 8
                if t.get("upload_kbps") else -1,
            })
        except Exception as exc:  # noqa: BLE001
            self._log.warning("could not apply throttle profile: %s", exc)



# ════════════════════════════════════════════════════════════════════════════
# recorder.py — network recorder: pages, frames, SPA segments, SSE, WebSocket, console, downloads (Parts 2, 3, 19, 36, 41, 42, 45)
# ════════════════════════════════════════════════════════════════════════════
# Live capture: network + attribution + runtime monitoring.
#
# One :class:`NetworkRecorder` attaches to a Playwright **context** and records:
#
# * every request/response with headers, bodies, timings, redirects and
#   failures (Parts 2, 44) — attributed to the current action (Part 13), the
#   tab/popup (Part 45), the frame (Part 45) and the (virtual) page segment,
#   where SPA route changes start a new segment (Part 36);
# * console messages, uncaught exceptions, crashes, dialogs, failed resources
#   (Part 19) and downloads (Part 18);
# * WebSocket handshakes and frames (Part 42) and SSE / chunked streams
#   (Part 41, via CDP on Chromium);
# * stricter-than-``networkidle`` settling (Part 4): no relevant request in
#   flight + a quiet window + expected URLs seen, ignoring background beacons,
#   streams and long-polls.


_TEXTUAL_TYPES = {"document", "xhr", "fetch", "eventsource", "other", "manifest", "texttrack"}
_BINARY_TYPES = {"image", "font", "media", "imageset"}


def _headers_list(mapping: Any) -> list:
    if isinstance(mapping, list):
        return [{"name": str(h.get("name", "")), "value": str(h.get("value", ""))} for h in mapping]
    return [{"name": str(k), "value": str(v)} for k, v in (mapping or {}).items()]


def _route_like_fragment(url: str) -> str:
    """Fragment that represents a client-side route (``#/cart``, ``#!/x``)."""
    frag = url.split("#", 1)[1] if "#" in url else ""
    return frag if frag.startswith(("/", "!")) else ""


def _segment_key(url: str) -> str:
    base = url.split("#", 1)[0]
    return base + ("#" + _route_like_fragment(url) if _route_like_fragment(url) else "")


class IdleResult(dict):
    """dict with attribute access: idle, waited_ms, pending, missing."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


class NetworkRecorder:
    def __init__(self, context, *, events: Optional[EventLog] = None, hooks: Optional[HookManager] = None,
                 body_policy: str = "auto", max_body_bytes: int = 5_000_000,
                 max_total_body_bytes: int = 400_000_000, cdp: bool = True,
                 dialog_policy: str = "accept", on_page: Optional[Callable] = None,
                 session_ref: Any = None) -> None:
        self.context = context
        self.events = events if events is not None else EventLog()
        self.hooks = hooks if hooks is not None else HookManager()
        self.body_policy = body_policy
        self.max_body_bytes = max_body_bytes
        self.max_total_body_bytes = max_total_body_bytes
        self.use_cdp = cdp
        self.dialog_policy = dialog_policy
        self.on_page_cb = on_page
        self.session_ref = session_ref
        self.current_action: Optional[str] = None
        self.records: list = []
        self.pending: dict = {}
        self._by_req: dict = {}
        self._seq = 0
        self._body_bytes = 0
        self._last_activity = mono_ms()
        self._last_relevant = mono_ms()
        self._activity_floor = 0.0
        self.pages: dict = {}
        self._page_ids: dict = {}
        self._page_objs: dict = {}
        self.frames: dict = {}
        self._frame_ids: dict = {}
        self._frame_counter: dict = {}
        self.segments: list = []
        self._segment_of_page: dict = {}
        self._segment_counter = 0
        self._pending_nav: dict = {}
        self.console: list = []
        self.dialogs: list = []
        self._downloads: list = []
        self.downloads: list = []
        self.websockets: list = []
        self._ws_by_url: dict = {}
        self._cdp_sessions: dict = {}
        self._cdp_unmatched: dict = {}
        self._cdp_req: dict = {}
        self._cdp_id_of: dict = {}
        self._cdp_ws: dict = {}
        self._cdp_offset: Optional[float] = None
        self._pattern_times: dict = {}
        self.stream_notes: list = []
        self._log = get_logger("recorder")
        self._attached = False
        self._handlers: list = []

    # ═══ attach / detach ═════════════════════════════════════════════
    def attach(self) -> "NetworkRecorder":
        if self._attached:
            return self
        for event, handler in (("page", self._on_page), ("request", self._on_request),
                               ("response", self._on_response),
                               ("requestfinished", self._on_request_finished),
                               ("requestfailed", self._on_request_failed)):
            self.context.on(event, handler)
            self._handlers.append((event, handler))
        for page in list(self.context.pages):
            self._on_page(page)
        self._attached = True
        return self

    def detach(self) -> None:
        for event, handler in self._handlers:
            try:
                self.context.remove_listener(event, handler)
            except Exception:  # noqa: BLE001
                pass
        self._handlers.clear()
        self._attached = False

    # ═══ pages / frames / segments ═══════════════════════════════════
    def page_id(self, page) -> str:
        pid = self._page_ids.get(page)
        if pid is None:
            pid = self._on_page(page)
        return pid

    def page_by_id(self, page_id: str):
        return self._page_objs.get(page_id)

    def open_pages(self) -> list:
        return [p for p in self._page_objs.values() if not p.is_closed()]

    def _on_page(self, page) -> str:
        if page in self._page_ids:
            return self._page_ids[page]
        pid = f"p{len(self._page_ids) + 1}"
        self._page_ids[page] = pid
        self._page_objs[pid] = page
        opener_id = None
        try:
            opener = page.opener()
            if opener is not None:
                opener_id = self._page_ids.get(opener) or self._on_page(opener)
        except Exception:  # noqa: BLE001
            opener = None
        self.pages[pid] = {"id": pid, "opener": opener_id, "kind": "popup" if opener_id else "page",
                           "created": iso(), "closed": None, "urls": [], "action_id": self.current_action,
                           "_ready": False}
        self.frames[f"{pid}.f0"] = {"id": f"{pid}.f0", "page_id": pid, "parent": None, "name": "",
                                    "url": "", "attached": iso(), "detached": None, "urls": []}
        self._frame_ids[page.main_frame] = f"{pid}.f0"
        self._frame_counter[pid] = 0
        page.on("close", lambda p=page: self._on_page_close(p))
        page.on("framenavigated", lambda f, p=page: self._on_frame_navigated(p, f))
        page.on("frameattached", lambda f, p=page: self._frame_id(p, f))
        page.on("framedetached", lambda f, p=page: self._on_frame_detached(p, f))
        page.on("console", lambda m, p=page: self._on_console(p, m))
        page.on("pageerror", lambda e, p=page: self._on_page_error(p, e))
        page.on("crash", lambda p=page: self._on_crash(p))
        page.on("dialog", lambda d, p=page: self._on_dialog(p, d))
        page.on("download", lambda d, p=page: self._on_download(p, d))
        page.on("websocket", lambda ws, p=page: self._on_websocket(p, ws))
        try:  # popups/tabs may have committed their first URL before these listeners existed
            if not page.url.startswith("about:"):
                self._on_frame_navigated(page, page.main_frame)
        except Exception:  # noqa: BLE001
            pass
        if self.use_cdp:
            self._attach_cdp(page, pid)
        self.events.emit("page_opened", page_id=pid, opener=opener_id, action_id=self.current_action)
        self.hooks.emit("page", session=self.session_ref, page=page, page_id=pid)
        if self.on_page_cb is not None:
            try:
                self.on_page_cb(page, pid)
            except Exception as exc:  # noqa: BLE001
                self._log.debug("on_page callback failed: %s", exc)
        self.pages[pid]["_ready"] = True
        return pid

    def wait_ready(self, page, timeout_ms: float = 5000) -> bool:
        """Wait until the page's setup (CDP attach, profile) has really run.

        ``_on_page`` usually runs inside a Playwright event handler whose blocking
        CDP calls only complete while the main code is inside a Playwright call;
        without this, the first requests of a page could slip past CDP.
        """
        pid = self.page_id(page)
        start = mono_ms()
        while not self.pages.get(pid, {}).get("_ready") and mono_ms() - start < timeout_ms:
            try:
                page.wait_for_timeout(10)
            except Exception:  # noqa: BLE001
                break
        return bool(self.pages.get(pid, {}).get("_ready"))

    def _on_page_close(self, page) -> None:
        pid = self._page_ids.get(page)
        if pid and not self.pages[pid]["closed"]:
            self.pages[pid]["closed"] = iso()
            self.events.emit("page_closed", page_id=pid, action_id=self.current_action)

    def _frame_id(self, page, frame) -> str:
        fid = self._frame_ids.get(frame)
        if fid is not None:
            return fid
        pid = self.page_id(page)
        self._frame_counter[pid] = self._frame_counter.get(pid, 0) + 1
        fid = f"{pid}.f{self._frame_counter[pid]}"
        self._frame_ids[frame] = fid
        parent_id = None
        try:
            parent = frame.parent_frame
            if parent is not None:
                parent_id = self._frame_ids.get(parent) or self._frame_id(page, parent)
        except Exception:  # noqa: BLE001
            pass
        name = ""
        try:
            name = frame.name
        except Exception:  # noqa: BLE001
            pass
        self.frames[fid] = {"id": fid, "page_id": pid, "parent": parent_id, "name": name, "url": "",
                            "attached": iso(), "detached": None, "urls": []}
        self.events.emit("frame_attached", page_id=pid, frame_id=fid, parent=parent_id)
        return fid

    def _on_frame_detached(self, page, frame) -> None:
        fid = self._frame_ids.get(frame)
        if fid and not self.frames[fid]["detached"]:
            self.frames[fid]["detached"] = iso()
            self.events.emit("frame_detached", frame_id=fid, page_id=self.frames[fid]["page_id"])

    def _ids_for_request(self, request) -> tuple:
        try:
            frame = request.frame
        except Exception:  # noqa: BLE001 - service-worker requests have no frame
            return None, None, None
        try:
            page = frame.page
        except Exception:  # noqa: BLE001
            page = None
        if page is None:
            return None, None, frame
        pid = self.page_id(page)
        return pid, self._frame_id(page, frame), frame

    def _new_segment(self, page_id: str, url: str, kind: str) -> dict:
        self._segment_counter += 1
        seg = {"id": f"s{self._segment_counter}", "page_id": page_id, "url": url, "title": "",
               "kind": kind, "startedDateTime": iso(), "t_start": mono_ms(), "virtual": kind == "spa",
               "action_id": self.current_action}
        self.segments.append(seg)
        self._segment_of_page[page_id] = seg
        self.events.emit("route_change" if kind == "spa" else "navigation", page_id=page_id, url=url,
                         segment_id=seg["id"], action_id=self.current_action, nav_kind=kind)
        return seg

    def _on_frame_navigated(self, page, frame) -> None:
        pid = self.page_id(page)
        fid = self._frame_id(page, frame)
        try:
            url = frame.url
        except Exception:  # noqa: BLE001
            return
        info = self.frames.get(fid)
        if info is not None:
            info["url"] = url
            if not info["urls"] or info["urls"][-1] != url:
                info["urls"].append(url)
            if not info.get("name"):
                try:  # the name attribute is often only known once the frame navigated
                    info["name"] = frame.name or ""
                except Exception:  # noqa: BLE001
                    pass
        if frame != page.main_frame:
            return
        pinfo = self.pages[pid]
        if not pinfo["urls"] or pinfo["urls"][-1] != url:
            pinfo["urls"].append(url)
        seg = self._segment_of_page.get(pid)
        if seg is None:
            if not url.startswith("about:"):
                self._new_segment(pid, url, "navigation")
            return
        if seg.get("pending_nav"):
            seg["url"] = url
            seg.pop("pending_nav", None)
            self._pending_nav.pop(pid, None)
            return
        if _segment_key(seg["url"]) != _segment_key(url):
            if url.startswith("about:"):
                return
            self._new_segment(pid, url, "spa")

    def _rollback_nav(self, pid: Optional[str], url: str, reason: str) -> None:
        """A main-frame navigation that never committed (download, 204, abort)."""
        pending = self._pending_nav.get(pid or "")
        if not pending or (url and pending["url"] != url):
            return
        self._pending_nav.pop(pid, None)
        seg = pending["segment"]
        if self._segment_of_page.get(pid) is seg:
            if pending["prev"] is not None:
                self._segment_of_page[pid] = pending["prev"]
            else:
                self._segment_of_page.pop(pid, None)
        if seg in self.segments:
            self.segments.remove(seg)
        for rec in self.records:
            if rec.segment_id == seg["id"]:
                rec.segment_id = pending["prev"]["id"] if pending["prev"] else None
        self.events.emit("navigation_cancelled", page_id=pid, url=url, reason=reason)

    def set_segment_title(self, page, title: str) -> None:
        seg = self._segment_of_page.get(self._page_ids.get(page, ""))
        if seg is not None and title:
            seg["title"] = title

    def current_segment(self, page) -> Optional[dict]:
        return self._segment_of_page.get(self._page_ids.get(page, ""))

    # ═══ network events ══════════════════════════════════════════════
    def _on_request(self, request) -> None:
        now = mono_ms()
        self._seq += 1
        pid, fid, frame = self._ids_for_request(request)
        try:
            is_nav = bool(request.is_navigation_request())
        except Exception:  # noqa: BLE001
            is_nav = False
        main = False
        if frame is not None and pid is not None:
            try:
                main = frame == self._page_objs[pid].main_frame
            except Exception:  # noqa: BLE001
                main = False
        prev = None
        try:
            prev = request.redirected_from
        except Exception:  # noqa: BLE001
            pass
        if is_nav and main and pid is not None:
            if prev is not None and self._segment_of_page.get(pid):
                self._segment_of_page[pid]["url"] = request.url
                if pid in self._pending_nav:
                    self._pending_nav[pid]["url"] = request.url
            else:
                before = self._segment_of_page.get(pid)
                seg = self._new_segment(pid, request.url, "navigation")
                seg["pending_nav"] = True
                self._pending_nav[pid] = {"segment": seg, "prev": before, "url": request.url}
        try:
            post = request.post_data_buffer
        except Exception:  # noqa: BLE001
            post = None
        rec = NetRecord(
            id=f"r{self._seq:05d}", seq=self._seq, method=request.method, url=request.url,
            resource_type=request.resource_type or "other", is_navigation=is_nav,
            page_id=pid, frame_id=fid, action_id=self.current_action,
            segment_id=(self._segment_of_page.get(pid) or {}).get("id") if pid else None,
            started=iso(), t_start=now,
            request_headers=_headers_list(request.headers), request_body=post,
        )
        if prev is not None and prev in self._by_req:
            rec.redirected_from = self._by_req[prev].id
        rec.category = classify_record(rec)[0]
        self._track_periodic(rec)
        self._by_req[request] = rec
        self.records.append(rec)
        self.pending[rec.id] = rec
        self._last_activity = now
        if not self._is_background(rec, now):
            self._last_relevant = now
        if self.use_cdp and pid:
            self._cdp_unmatched.setdefault((pid, rec.url), []).append(rec)
        self.events.emit("request", request_id=rec.id, action_id=rec.action_id, page_id=pid,
                         method=rec.method, url=rec.url, type=rec.resource_type)
        if self.hooks.has("request"):
            self.hooks.emit("request", session=self.session_ref, record=rec, request=request)

    def add_manual(self, *, method: str, url: str, request_headers: Any = None, request_body: Optional[bytes] = None,
                   status: Optional[int] = None, status_text: str = "", response_headers: Any = None,
                   response_body: Optional[bytes] = None, started: str = "", t_start: float = 0.0,
                   t_end: Optional[float] = None, failure: Optional[str] = None, page=None,
                   initiator: Optional[dict] = None) -> NetRecord:
        """Record a request made outside the page (``Session.fetch``) so it lands in the HAR too."""
        self._seq += 1
        pid = self._page_ids.get(page) if page is not None else None
        rec = NetRecord(id=f"r{self._seq:05d}", seq=self._seq, method=method.upper(), url=url, resource_type="fetch",
                        page_id=pid, frame_id=f"{pid}.f0" if pid else None, action_id=self.current_action,
                        segment_id=(self._segment_of_page.get(pid) or {}).get("id") if pid else None,
                        started=started or iso(), t_start=t_start or mono_ms(), t_end=t_end or mono_ms(),
                        status=status, status_text=status_text, request_headers=_headers_list(request_headers),
                        response_headers=_headers_list(response_headers), request_body=request_body,
                        response_body=response_body, failure=failure,
                        initiator=initiator or {"type": "agenttrace.fetch"})
        rec.mime_type = header_value(rec.response_headers, "content-type")
        rec.response_size = len(response_body) if response_body is not None else None
        rec.category = classify_record(rec)[0]
        self.records.append(rec)
        self.events.emit("request", request_id=rec.id, action_id=rec.action_id, page_id=pid, method=rec.method,
                         url=rec.url, type="fetch")
        if failure:
            self.events.emit("request_failed", request_id=rec.id, action_id=rec.action_id, url=rec.url,
                             failure=failure, level="warning")
        else:
            self.events.emit("response", request_id=rec.id, action_id=rec.action_id, status=rec.status, url=rec.url)
        if self.hooks.has("request_finished"):
            self.hooks.emit("request_finished", session=self.session_ref, record=rec)
        return rec

    def _track_periodic(self, rec: NetRecord) -> None:
        """Flag request shapes that repeat at a regular interval (polling heartbeat)."""
        key = pattern_key(rec)
        times = self._pattern_times.setdefault(key, [])
        times.append(rec.t_start)
        if len(times) > 12:
            del times[:-12]
        recent = [t for t in times if rec.t_start - t <= 30_000]
        if len(recent) >= 4:
            gaps = [b - a for a, b in zip(recent, recent[1:])]
            mean = sum(gaps) / len(gaps)
            var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
            if 0 < mean <= 15_000 and (var ** 0.5) / mean < 0.35:
                rec.extra["periodic"] = True

    def _record_for(self, request) -> NetRecord:
        rec = self._by_req.get(request)
        if rec is None:  # request started before we attached
            self._on_request(request)
            rec = self._by_req[request]
        return rec

    def _on_response(self, response) -> None:
        try:
            rec = self._record_for(response.request)
        except Exception:  # noqa: BLE001
            return
        try:
            rec.status = response.status
            rec.status_text = response.status_text or ""
            rec.response_headers = _headers_list(response.headers)
            rec.mime_type = header_value(rec.response_headers, "content-type")
            rec.from_service_worker = bool(response.from_service_worker)
        except Exception:  # noqa: BLE001
            pass
        if "event-stream" in rec.mime_type.lower():
            rec.extra["streaming"] = True
        now = mono_ms()
        self._last_activity = now
        if not self._is_background(rec, now):
            self._last_relevant = now
        self.events.emit("response", request_id=rec.id, action_id=rec.action_id, status=rec.status,
                         url=rec.url)
        if rec.status is not None and rec.status >= 400:
            self._add_console("resource_error", f"{rec.status} {rec.status_text} {rec.method} {rec.url}",
                              page_id=rec.page_id, request_id=rec.id, action_id=rec.action_id)
        if self.hooks.has("response"):
            self.hooks.emit("response", session=self.session_ref, record=rec, response=response)

    def _want_body(self, rec: NetRecord) -> bool:
        if self.body_policy == "none":
            return False
        if self._body_bytes >= self.max_total_body_bytes:
            rec.body_note = "omitted: session body budget exhausted"
            return False
        length = header_value(rec.response_headers, "content-length")
        if length.isdigit() and int(length) > self.max_body_bytes:
            rec.body_note = f"omitted: {length} bytes > max_body_bytes"
            return False
        if self.body_policy == "all":
            return True
        ct = rec.mime_type
        if rec.resource_type in _BINARY_TYPES:
            rec.body_note = f"omitted: {rec.resource_type} (body_policy=auto)"
            return False
        if rec.resource_type in ("script", "stylesheet") and not ct.startswith("application/json"):
            rec.body_note = f"omitted: {rec.resource_type} (body_policy=auto)"
            return False
        return rec.resource_type in _TEXTUAL_TYPES or is_text_mime(ct)

    def _on_request_finished(self, request) -> None:
        try:
            rec = self._record_for(request)
        except Exception:  # noqa: BLE001
            return
        rec.t_end = mono_ms()
        self.pending.pop(rec.id, None)
        try:
            response = request.response()
        except Exception:  # noqa: BLE001
            response = None
        if response is not None:
            try:
                rec.request_headers = _headers_list(request.headers_array())
                rec.response_headers = _headers_list(response.headers_array())
                rec.mime_type = header_value(rec.response_headers, "content-type") or rec.mime_type
                if rec.status is None:
                    rec.status = response.status
                    rec.status_text = response.status_text or ""
            except Exception:  # noqa: BLE001
                pass
            try:
                addr = response.server_addr()
                if addr:
                    rec.server_ip = str(addr.get("ipAddress", ""))
            except Exception:  # noqa: BLE001
                pass
            try:
                rec.timings = dict(request.timing or {})
            except Exception:  # noqa: BLE001
                pass
            try:
                sizes = request.sizes()
                rec.transfer_size = int(sizes.get("responseBodySize", -1))
                rec.extra["sizes"] = sizes
            except Exception:  # noqa: BLE001
                pass
            if 300 <= (rec.status or 0) < 400:
                rec.redirect_url = header_value(rec.response_headers, "location")
                rec.body_note = rec.body_note or "redirect"
            elif self._want_body(rec):
                try:
                    body = response.body()
                    if len(body) > self.max_body_bytes:
                        rec.body_note = f"truncated to {self.max_body_bytes} bytes"
                        body = body[: self.max_body_bytes]
                    rec.response_body = body
                    rec.response_size = len(body)
                    self._body_bytes += len(body)
                except Exception as exc:  # noqa: BLE001
                    rec.body_note = f"unavailable: {str(exc).splitlines()[0][:120]}"
        if rec.request_body is None and rec.method in ("POST", "PUT", "PATCH", "DELETE"):
            self._fetch_post_data(rec)
        self._touch(rec)
        if rec.is_navigation and rec.status in (204, 205):
            self._rollback_nav(rec.page_id, rec.url, f"http {rec.status}")
        self.events.emit("request_finished", request_id=rec.id, action_id=rec.action_id,
                         status=rec.status, ms=rec.duration_ms)
        if self.hooks.has("request_finished"):
            self.hooks.emit("request_finished", session=self.session_ref, record=rec)

    def _fetch_post_data(self, rec: NetRecord) -> None:
        """Bodies Playwright cannot expose (FormData with files/Blobs) come from CDP instead."""
        ct = header_value(rec.request_headers, "content-type").lower()
        length = header_value(rec.request_headers, "content-length")
        if not ("multipart" in ct or (length.isdigit() and int(length) > 0)):
            return
        request_id = self._cdp_id_of.get(rec.id)
        cdp = self._cdp_sessions.get(rec.page_id or "")
        if not request_id or cdp is None:
            return
        try:
            res = cdp.send("Network.getRequestPostData", {"requestId": request_id})
        except Exception:  # noqa: BLE001 - body no longer available
            return
        data = res.get("postData")
        if data is None:
            return
        if res.get("base64Encoded"):
            body = base64.b64decode(data)
        else:  # older Chromium hands the raw bytes over as a latin-1 string
            try:
                body = data.encode("latin-1")
            except UnicodeEncodeError:
                body = data.encode("utf-8")
        rec.request_body = body
        rec.extra["post_data_via"] = "cdp"

    def _on_request_failed(self, request) -> None:
        try:
            rec = self._record_for(request)
        except Exception:  # noqa: BLE001
            return
        rec.t_end = mono_ms()
        self.pending.pop(rec.id, None)
        try:
            rec.failure = request.failure or "failed"
        except Exception:  # noqa: BLE001
            rec.failure = "failed"
        try:
            rec.timings = dict(request.timing or {})
        except Exception:  # noqa: BLE001
            pass
        self._touch(rec)
        if rec.is_navigation:
            self._rollback_nav(rec.page_id, rec.url, rec.failure or "failed")
        self.events.emit("request_failed", request_id=rec.id, action_id=rec.action_id, url=rec.url,
                         failure=rec.failure, level="warning")
        if self.hooks.has("request_finished"):
            self.hooks.emit("request_finished", session=self.session_ref, record=rec)
        if "ERR_ABORTED" not in (rec.failure or "") or rec.resource_type not in ("document",):
            self._add_console("request_failed", f"{rec.method} {rec.url} -> {rec.failure}",
                              page_id=rec.page_id, request_id=rec.id, action_id=rec.action_id)

    def _touch(self, rec: NetRecord) -> None:
        now = mono_ms()
        self._last_activity = now
        if not self._is_background(rec, now):
            self._last_relevant = now

    # ═══ runtime monitoring (Part 19) ════════════════════════════════
    def _add_console(self, kind: str, text: str, **fields: Any) -> dict:
        entry = {"ts": iso(), "t_ms": round(mono_ms(), 1), "kind": kind, "text": text}
        entry.update({k: v for k, v in fields.items() if v is not None})
        if "action_id" not in entry and self.current_action:
            entry["action_id"] = self.current_action
        self.console.append(entry)
        level = "error" if kind in ("pageerror", "crash", "request_failed", "resource_error") or \
            fields.get("level") == "error" else "info"
        self.events.emit("console", console_kind=kind, text=text[:300], level=level,
                         action_id=entry.get("action_id"), page_id=entry.get("page_id"))
        return entry

    def _on_console(self, page, msg) -> None:
        try:
            level = msg.type
            text = msg.text
            loc = msg.location or {}
        except Exception:  # noqa: BLE001
            return
        self._add_console("console", text, level=level, page_id=self._page_ids.get(page),
                          url=loc.get("url"), line=loc.get("lineNumber"), column=loc.get("columnNumber"))

    def _on_page_error(self, page, error) -> None:
        try:
            message = getattr(error, "message", None) or str(error)
            name = getattr(error, "name", None) or "Error"
            stack = getattr(error, "stack", None) or ""
        except Exception:  # noqa: BLE001
            message, name, stack = str(error), "Error", ""
        self._add_console("pageerror", f"{name}: {message}", level="error",
                          page_id=self._page_ids.get(page), stack=str(stack)[:2000])

    def _on_crash(self, page) -> None:
        self._add_console("crash", "page crashed", level="error", page_id=self._page_ids.get(page))

    def _on_dialog(self, page, dialog) -> None:
        info = {"ts": iso(), "page_id": self._page_ids.get(page), "action_id": self.current_action}
        try:
            info.update({"type": dialog.type, "message": dialog.message,
                         "default_value": dialog.default_value})
        except Exception:  # noqa: BLE001
            pass
        policy = self.dialog_policy
        try:
            if policy == "dismiss" and info.get("type") != "beforeunload":
                dialog.dismiss()
                info["handled"] = "dismissed"
            else:
                dialog.accept(info.get("default_value") or "") if info.get("type") == "prompt" else dialog.accept()
                info["handled"] = "accepted"
        except Exception as exc:  # noqa: BLE001
            info["handled"] = f"error: {exc}"
        self.dialogs.append(info)
        self.events.emit("dialog", **{k: v for k, v in info.items() if k != "ts"})

    # ═══ downloads (Part 18) ═════════════════════════════════════════
    def _on_download(self, page, download) -> None:
        item = {"download": download, "page_id": self._page_ids.get(page), "action_id": self.current_action,
                "started": iso(), "t_ms": mono_ms()}
        try:
            item["url"] = download.url
            item["suggested_filename"] = download.suggested_filename
        except Exception:  # noqa: BLE001
            pass
        self._downloads.append(item)
        self._rollback_nav(item["page_id"], item.get("url", ""), "download")
        self.events.emit("download_started", page_id=item["page_id"], action_id=item["action_id"],
                         url=item.get("url"), filename=item.get("suggested_filename"))

    def pending_downloads(self) -> int:
        return len(self._downloads)

    def save_downloads(self, directory: Any) -> list:
        """Persist finished downloads + metadata (filename, MIME, size, sha256, request)."""
        out = []
        directory = Path(directory)
        while self._downloads:
            item = self._downloads.pop(0)
            download = item.pop("download")
            name = slugify(item.get("suggested_filename") or "download", 120, "download")
            target = directory / f"{item.get('action_id') or 'bg'}_{len(self.downloads) + 1:02d}_{name}"
            meta = {k: v for k, v in item.items() if k != "t_ms"}
            meta["filename"] = item.get("suggested_filename")
            try:
                directory.mkdir(parents=True, exist_ok=True)
                download.save_as(str(target))
                failure = download.failure()
            except Exception as exc:  # noqa: BLE001
                failure = str(exc)
            if failure:
                meta["error"] = failure
            elif target.exists():
                data = target.read_bytes()
                meta.update({"path": str(target), "size": len(data), "sha256": sha256_hex(data)})
            rec = self._record_for_url(item.get("url", ""))
            ct = ""
            if rec is not None:
                meta["request_id"] = rec.id
                meta["request"] = {"method": rec.method, "url": rec.url, "status": rec.status,
                                   "content_disposition": header_value(rec.response_headers,
                                                                       "content-disposition")}
                ct = header_value(rec.response_headers, "content-type")
                if meta.get("size") is not None:
                    length = header_value(rec.response_headers, "content-length")
                    meta["content_length_matches"] = (not length.isdigit()) or int(length) == meta["size"]
            guessed = mimetypes.guess_type(meta.get("filename") or "")[0] or ""
            meta["mime_type"] = (ct.split(";")[0].strip() if ct else "") or guessed or "application/octet-stream"
            meta["network"] = "captured" if rec is not None else "none (generated in page, e.g. blob:)"
            self.downloads.append(meta)
            self.events.emit("download_saved", action_id=meta.get("action_id"), path=meta.get("path"),
                             size=meta.get("size"), mime=meta["mime_type"], request_id=meta.get("request_id"))
            self.hooks.emit("download", session=self.session_ref, download=meta)
            out.append(meta)
        return out

    def _record_for_url(self, url: str) -> Optional[NetRecord]:
        if not url:
            return None
        for rec in reversed(self.records):
            if rec.url == url:
                return rec
        return None

    # ═══ WebSocket (Part 42) ═════════════════════════════════════════
    def _on_websocket(self, page, ws) -> None:
        pid = self._page_ids.get(page)
        conn = {"id": f"ws{len(self.websockets) + 1}", "url": ws.url, "page_id": pid,
                "segment_id": (self._segment_of_page.get(pid) or {}).get("id"),
                "action_id": self.current_action, "opened": iso(), "closed": None, "error": None,
                "handshake": None, "messages": []}
        self.websockets.append(conn)
        self._ws_by_url.setdefault(ws.url, []).append(conn)
        self._last_activity = mono_ms()
        linked = {c["handshake"]["cdp_id"] for c in self.websockets if c.get("handshake")}
        for request_id, info in list(self._cdp_ws.items()):  # handshake seen by CDP before this event
            if request_id not in linked and info.get("url") in (ws.url, None):
                self._link_ws_handshake(request_id)
                if conn["handshake"] is not None:
                    break

        def add(direction: str, payload: Any) -> None:
            msg = {"seq": len(conn["messages"]) + 1, "conn_id": conn["id"], "direction": direction, "ts": iso(),
                   "t_ms": round(mono_ms(), 1), "action_id": self.current_action}
            if isinstance(payload, (bytes, bytearray)):
                msg.update({"opcode": "binary", "size": len(payload),
                            "data_base64": base64.b64encode(bytes(payload)).decode("ascii")})
            else:
                text = str(payload)
                msg.update({"opcode": "text", "size": len(text.encode("utf-8")), "data": text})
            conn["messages"].append(msg)

        ws.on("framesent", lambda payload: add("sent", payload))
        ws.on("framereceived", lambda payload: add("received", payload))
        ws.on("close", lambda _ws: conn.update(closed=iso()))
        ws.on("socketerror", lambda err: conn.update(error=str(err)))
        self.events.emit("websocket_opened", ws_id=conn["id"], url=ws.url, page_id=conn["page_id"],
                         action_id=self.current_action)

    # ═══ CDP: initiators, SSE, chunked streams, WS handshakes ════════
    def _attach_cdp(self, page, pid: str) -> None:
        try:
            cdp = self.context.new_cdp_session(page)
            cdp.send("Network.enable")
        except Exception:  # noqa: BLE001 - not Chromium, or page already gone
            return
        try:  # async stack traces let us tell setInterval polling from user-triggered requests
            cdp.send("Runtime.enable")
            cdp.send("Runtime.setAsyncCallStackDepth", {"maxDepth": 8})
        except Exception:  # noqa: BLE001
            pass
        self._cdp_sessions[pid] = cdp
        cdp.on("Network.requestWillBeSent", lambda e, p=pid: self._cdp_request(p, e))
        cdp.on("Network.responseReceived", lambda e, p=pid, s=cdp: self._cdp_response(p, s, e))
        cdp.on("Network.eventSourceMessageReceived", lambda e: self._cdp_sse(e))
        cdp.on("Network.dataReceived", lambda e: self._cdp_data(e))
        cdp.on("Network.webSocketWillSendHandshakeRequest", lambda e: self._cdp_ws_request(e))
        cdp.on("Network.webSocketHandshakeResponseReceived", lambda e: self._cdp_ws_response(e))
        cdp.on("Network.webSocketCreated", lambda e: self._cdp_ws.setdefault(e.get("requestId"), {})
               .update(url=e.get("url")))

    def _cdp_wall(self, ts: Optional[float]) -> Optional[str]:
        if ts is None or self._cdp_offset is None:
            return None
        return iso(ts + self._cdp_offset)

    def _pop_unmatched(self, pid: str, url: str) -> Optional[NetRecord]:
        """Oldest Playwright record for (page, url) still waiting for its CDP twin.

        Records whose CDP event was lost (a renderer swap can drop the first events
        of a new page) are skipped once stale, so they never steal a later request.
        """
        queue = self._cdp_unmatched.get((pid, url))
        now = mono_ms()
        while queue:
            rec = queue.pop(0)
            if now - rec.t_start < 10_000:
                return rec
        return None

    def _cdp_request(self, pid: str, e: dict) -> None:
        if e.get("wallTime") and e.get("timestamp"):
            self._cdp_offset = float(e["wallTime"]) - float(e["timestamp"])
        url = (e.get("request") or {}).get("url", "")
        rec = self._pop_unmatched(pid, url)
        if rec is None:
            # Playwright's request event may arrive after the CDP one: remember and match later.
            self._cdp_req[e.get("requestId")] = {"pending_url": url, "pid": pid, "initiator": e.get("initiator")}
            return
        self._bind_cdp(e.get("requestId"), rec, e.get("initiator"))

    def _bind_cdp(self, request_id: str, rec: NetRecord, initiator: Optional[dict]) -> None:
        self._cdp_req[request_id] = {"rec": rec}
        self._cdp_id_of[rec.id] = request_id
        if isinstance(initiator, dict):
            info = {"type": initiator.get("type")}
            if initiator.get("url"):
                info["url"] = initiator.get("url")
            stack = initiator.get("stack") or {}
            frames = stack.get("callFrames") or []
            if not frames and isinstance(stack.get("parent"), dict):
                frames = stack["parent"].get("callFrames") or []
            if frames:
                info["function"] = frames[0].get("functionName") or "(anonymous)"
                info["script"] = frames[0].get("url")
                info["line"] = frames[0].get("lineNumber")
                if stack.get("parent"):
                    info["async_parent"] = stack["parent"].get("description")
            rec.initiator = info
            if info.get("async_parent") == "setInterval":
                rec.extra["polling"] = True

    def _cdp_rec(self, request_id: str) -> Optional[NetRecord]:
        entry = self._cdp_req.get(request_id)
        if not entry:
            return None
        if "rec" in entry:
            return entry["rec"]
        rec = self._pop_unmatched(entry["pid"], entry["pending_url"])
        if rec is None:
            return None
        self._bind_cdp(request_id, rec, entry.get("initiator"))
        if entry.get("stream_init"):  # stream data that arrived before the record existed
            self._init_stream(rec, **entry["stream_init"])
            for kind, payload, ts in entry.get("stash", []):
                if kind == "data":
                    self._add_chunk(rec, payload, ts)
                else:
                    self._cdp_sse(payload)
        return rec

    def _init_stream(self, rec: NetRecord, type: str, cdp_request_id: str) -> None:
        rec.extra["streaming"] = True
        if not rec.stream:
            rec.stream = {"type": type, "cdp_request_id": cdp_request_id, "events": [], "chunks": []}

    def _cdp_response(self, pid: str, cdp, e: dict) -> None:
        resp = e.get("response") or {}
        rid = e.get("requestId")
        mime = str(resp.get("mimeType") or "").lower()
        rtype = str(e.get("type") or "").lower()
        rec = self._cdp_rec(rid)
        if rec is None and rid not in self._cdp_req:
            # requestWillBeSent never reached us (lost during a renderer swap): bind by URL now
            rec = self._pop_unmatched(pid, resp.get("url") or "")
            if rec is not None:
                self._bind_cdp(rid, rec, None)
            else:
                self._cdp_req[rid] = {"pending_url": resp.get("url") or "", "pid": pid, "initiator": None}
        streaming = "event-stream" in mime
        if not streaming and rtype in ("fetch", "xhr"):
            headers = {k.lower(): v for k, v in (resp.get("headers") or {}).items()}
            streaming = "chunked" in str(headers.get("transfer-encoding", "")).lower() and \
                "content-length" not in headers and ("ndjson" in mime or "stream" in mime or "text/plain" in mime)
        if not streaming:
            return
        init = {"type": "sse" if "event-stream" in mime else "chunked", "cdp_request_id": rid}
        if rec is not None:
            self._init_stream(rec, **init)
        else:
            self._cdp_req[rid].update(stream_init=init, stash=self._cdp_req[rid].get("stash", []))
        if rtype != "eventsource":
            try:
                result = cdp.send("Network.streamResourceContent", {"requestId": rid})
                buffered = result.get("bufferedData") or ""
                if buffered:
                    self._stream_data(rid, base64.b64decode(buffered), e.get("timestamp"))
            except Exception as exc:  # noqa: BLE001
                if rec is not None:
                    rec.stream["note"] = f"streamResourceContent unavailable: {exc}"

    def _stream_data(self, rid: str, data: bytes, ts: Optional[float]) -> None:
        rec = self._cdp_rec(rid)
        if rec is not None and rec.stream:
            self._add_chunk(rec, data, ts)
            return
        entry = self._cdp_req.get(rid)
        if entry is not None and entry.get("stream_init"):
            entry["stash"].append(("data", data, ts))

    def _add_chunk(self, rec: NetRecord, data: bytes, ts: Optional[float]) -> None:
        stream = rec.stream
        chunk = {"seq": len(stream["chunks"]) + 1, "ts": self._cdp_wall(ts) or iso(), "size": len(data),
                 "data": data.decode("utf-8", "replace")}
        stream["chunks"].append(chunk)
        if stream["type"] == "sse":
            buf = stream.get("_buf", "") + chunk["data"]
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = parse_sse_block(block)
                if ev is not None:
                    ev.update(seq=len(stream["events"]) + 1, ts=chunk["ts"])
                    stream["events"].append(ev)
            stream["_buf"] = buf
        self._last_activity = mono_ms()

    def _cdp_data(self, e: dict) -> None:
        data = e.get("data")
        if not data:
            return
        self._stream_data(e.get("requestId"), base64.b64decode(data), e.get("timestamp"))

    def _cdp_sse(self, e: dict) -> None:
        rid = e.get("requestId")
        rec = self._cdp_rec(rid)
        if rec is None:
            entry = self._cdp_req.get(rid)
            if entry is not None:
                entry.setdefault("stream_init", {"type": "sse", "cdp_request_id": rid})
                entry.setdefault("stash", []).append(("sse", e, None))
            return
        if not rec.stream:
            self._init_stream(rec, "sse", rid)
        rec.stream["events"].append({"seq": len(rec.stream["events"]) + 1,
                                     "ts": self._cdp_wall(e.get("timestamp")) or iso(),
                                     "event": e.get("eventName") or "message", "id": e.get("eventId") or "",
                                     "data": e.get("data", ""), "action_id": self.current_action})
        self._last_activity = mono_ms()

    def _cdp_ws_request(self, e: dict) -> None:
        info = self._cdp_ws.setdefault(e.get("requestId"), {})
        req = e.get("request") or {}
        info["request_headers"] = req.get("headers") or {}
        info["ts"] = self._cdp_wall(e.get("timestamp")) or iso()
        self._link_ws_handshake(e.get("requestId"))

    def _cdp_ws_response(self, e: dict) -> None:
        info = self._cdp_ws.setdefault(e.get("requestId"), {})
        resp = e.get("response") or {}
        info["status"] = resp.get("status")
        info["status_text"] = resp.get("statusText")
        info["response_headers"] = resp.get("headers") or {}
        self._link_ws_handshake(e.get("requestId"))

    def _link_ws_handshake(self, request_id: str) -> None:
        info = self._cdp_ws.get(request_id) or {}
        url = info.get("url")
        if url:
            candidates = self._ws_by_url.get(url, [])
        else:  # webSocketCreated was lost: match on the Host header of the handshake instead
            host = next((v for k, v in (info.get("request_headers") or {}).items() if k.lower() == "host"), "")
            candidates = [c for c in self.websockets if host and urlsplit(c["url"]).netloc == host]
        for conn in candidates:
            if conn["handshake"] is None or conn["handshake"].get("cdp_id") == request_id:
                conn["handshake"] = {"cdp_id": request_id, **{k: v for k, v in info.items() if k != "url"}}
                return

    def streams(self) -> list:
        out = []
        for rec in self.records:
            if rec.stream:
                stream = {k: v for k, v in rec.stream.items() if not k.startswith("_")}
                out.append({"request_id": rec.id, "url": rec.url, "action_id": rec.action_id,
                            "status": rec.status, **stream})
        return out

    # ═══ settling (Part 4 / 16) ══════════════════════════════════════
    def _is_background(self, rec: NetRecord, now: float, long_request_ms: float = 15_000) -> bool:
        if rec.extra.get("streaming") or rec.resource_type in ("eventsource", "websocket", "ping"):
            return True
        if rec.category in NOISE_CATEGORIES:
            return True
        if rec.extra.get("ignore_idle") or rec.extra.get("polling") or rec.extra.get("periodic"):
            return True
        return rec.t_end is None and now - rec.t_start > long_request_ms

    def _last_relevant_time(self, now: float, long_request_ms: float = 15_000) -> float:
        """Latest start/end of a non-background request (evaluated lazily, so a
        request later recognised as polling stops counting retroactively)."""
        latest = 0.0
        for rec in reversed(self.records[-400:]):
            if rec.t_start < now - 120_000 and (rec.t_end or 0) < now - 120_000:
                break
            if self._is_background(rec, now, long_request_ms):
                continue
            latest = max(latest, rec.t_start, rec.t_end or 0.0)
        return max(latest, self._activity_floor)

    def seen(self, pattern: str, since_seq: int = 0) -> bool:
        return any(self._match(pattern, r.url) for r in self.records if r.seq > since_seq)

    @staticmethod
    def _match(pattern: str, url: str) -> bool:
        if pattern.startswith("re:"):
            return re.search(pattern[3:], url) is not None
        return pattern in url

    def pump(self, ms: float, page=None) -> None:
        """Let Playwright dispatch events for ``ms`` milliseconds.

        The sync API only delivers events while a Playwright call is running,
        so waiting must go through a page (``time.sleep`` would starve handlers).
        """
        candidates = [page] if page is not None else []
        candidates += [p for p in self.open_pages() if p is not page]
        for p in candidates:
            try:
                if p is not None and not p.is_closed():
                    p.wait_for_timeout(ms)
                    return
            except Exception:  # noqa: BLE001
                continue
        time.sleep(ms / 1000.0)

    def wait_idle(self, *, quiet_ms: float = 500, max_wait_ms: float = 30_000, expect: Iterable[str] = (),
                  long_request_ms: float = 15_000, poll_ms: float = 50, page=None,
                  since_seq: int = 0) -> IdleResult:
        """Wait until the network settled (see module doc). Never raises."""
        start = mono_ms()
        expect = tuple(expect or ())
        while True:
            now = mono_ms()
            relevant = [r for r in self.pending.values() if not self._is_background(r, now, long_request_ms)]
            quiet_for = now - self._last_relevant_time(now, long_request_ms)
            missing = [e for e in expect if not self.seen(e, since_seq)]
            waited = now - start
            if not relevant and quiet_for >= quiet_ms and not missing:
                return IdleResult(idle=True, waited_ms=round(waited), pending=[], missing=[])
            if waited >= max_wait_ms:
                return IdleResult(idle=False, waited_ms=round(waited),
                                  pending=[f"{r.method} {r.url}" for r in relevant][:10], missing=missing)
            self.pump(poll_ms, page)

    # ═══ queries ═════════════════════════════════════════════════════
    def mark(self) -> int:
        return self._seq

    def touch_activity(self) -> None:
        """Start the quiet window now (called right after an action is performed)."""
        self._activity_floor = mono_ms()

    def since(self, mark: int) -> list:
        return [r for r in self.records if r.seq > mark]

    def for_action(self, action_id: str) -> list:
        return [r for r in self.records if r.action_id == action_id]

    def find(self, pattern: str = "", *, method: Optional[str] = None, status: Optional[int] = None,
             resource_type: Optional[str] = None, action_id: Optional[str] = None) -> list:
        out = []
        for rec in self.records:
            if pattern and not self._match(pattern, rec.url):
                continue
            if method and rec.method.upper() != method.upper():
                continue
            if status is not None and rec.status != status:
                continue
            if resource_type and rec.resource_type != resource_type:
                continue
            if action_id and rec.action_id != action_id:
                continue
            out.append(rec)
        return out

    def har(self, records: Optional[Iterable[NetRecord]] = None, *, include_bodies: bool = True,
            browser: Optional[dict] = None, websockets: Optional[Iterable[dict]] = None) -> dict:
        """HAR 1.2 of ``records`` (default: everything, WebSocket connections included)."""
        recs = list(self.records if records is None else records)
        if websockets is None:
            websockets = self.websockets if records is None else ()
        pages = [{"id": s["id"], "url": s["url"], "title": s.get("title") or s["url"],
                  "startedDateTime": s["startedDateTime"], "virtual": s.get("virtual"),
                  "page_id": s.get("page_id")} for s in self.segments]
        return build_har(recs, pages=pages, include_bodies=include_bodies, browser=browser, websockets=websockets)

    def pages_report(self) -> dict:
        per_page: dict = {}
        per_frame: dict = {}
        for rec in self.records:
            per_page.setdefault(rec.page_id or "service-worker", []).append(rec.id)
            per_frame.setdefault(rec.frame_id or "none", []).append(rec.id)
        pages = []
        for pid, info in self.pages.items():
            frames = [dict(f, requests=len(per_frame.get(f["id"], []))) for f in self.frames.values()
                      if f["page_id"] == pid]
            pages.append(dict({k: v for k, v in info.items() if not k.startswith("_")},
                              requests=len(per_page.get(pid, [])), frames=frames))
        return {"pages": pages, "segments": [{k: v for k, v in s.items() if k not in ("t_start", "pending_nav")}
                                             for s in self.segments]}

    def stats(self) -> dict:
        failed = [r for r in self.records if r.failure]
        return {"requests": len(self.records), "pending": len(self.pending), "failed": len(failed),
                "body_bytes": self._body_bytes, "pages": len(self.pages), "segments": len(self.segments),
                "console": len(self.console), "websockets": len(self.websockets),
                "downloads": len(self.downloads)}


def parse_sse_block(block: str) -> Optional[dict]:
    """Parse one SSE event block (``event:``/``id:``/``data:`` lines)."""
    event = {"event": "message", "id": "", "data": ""}
    data_lines = []
    has = False
    for line in block.replace("\r\n", "\n").split("\n"):
        if not line or line.startswith(":"):
            continue
        field_name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field_name == "data":
            data_lines.append(value)
            has = True
        elif field_name == "event":
            event["event"] = value
            has = True
        elif field_name == "id":
            event["id"] = value
            has = True
        elif field_name == "retry":
            event["retry"] = value
    if not has:
        return None
    event["data"] = "\n".join(data_lines)
    return event


def host_segment_label(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return slugify(url)
    return slugify(f"{parts.path or '/'}{('#' + parts.fragment) if parts.fragment else ''}", 50, "root")



# ════════════════════════════════════════════════════════════════════════════
# blocking.py — CAPTCHA / bot-wall detection + human-in-the-loop (Part 37)
# ════════════════════════════════════════════════════════════════════════════
# CAPTCHA / bot-wall / hard-block detection and human-in-the-loop escalation
# (Part 37).
#
# ``detect_block`` combines HTTP status, vendor headers, challenge widgets in
# the DOM (reCAPTCHA, hCaptcha, Turnstile, DataDome, PerimeterX, Arkose …) and
# short challenge-page texts.  ``HumanInTheLoop`` pauses the workflow, notifies
# the operator (stderr bell, callback, ``PAUSED.json`` marker), waits until the
# page is no longer blocked (or a ``RESUME`` file appears) and resumes in the
# *same* context, so cookies/session state are preserved.  In headless mode it
# can hand the page over to a visible browser window for the human and copy the
# resulting clearance cookies back.


BLOCK_MARKERS_JS = r"""
() => {
  const q = (s) => { try { return !!document.querySelector(s); } catch (e) { return false; } };
  const m = {};
  m.recaptcha = q('iframe[src*="recaptcha"],.g-recaptcha,#recaptcha,[data-sitekey][class*="recaptcha"]');
  m.hcaptcha = q('iframe[src*="hcaptcha"],.h-captcha');
  m.turnstile = q('iframe[src*="challenges.cloudflare.com"],.cf-turnstile,#cf-challenge-running,#challenge-stage,#challenge-form,.cf-browser-verification');
  m.datadome = q('iframe[src*="captcha-delivery.com"],script[src*="captcha-delivery.com"]');
  m.perimeterx = q('#px-captcha,[class*="px-captcha"]');
  m.arkose = q('iframe[src*="arkoselabs"],iframe[src*="funcaptcha"],#FunCaptcha');
  m.geetest = q('.geetest_holder,.geetest_panel');
  m.generic = q('form[id*="captcha" i],input[name*="captcha" i],img[src*="captcha" i],[id*="captcha" i]');
  m.title = document.title || '';
  m.text = (document.body ? document.body.innerText : '').slice(0, 3000);
  m.text_length = document.body ? document.body.innerText.length : 0;
  return m;
}
"""

_TITLE_PATTERNS = [
    (r"just a moment", "js_challenge", "cloudflare"),
    (r"attention required", "captcha", "cloudflare"),
    (r"verify (that )?you are (a )?human", "captcha", None),
    (r"are you a (ro)?bot", "captcha", None),
    (r"robot check", "captcha", "amazon"),
    (r"pardon our interruption", "captcha", "imperva"),
    (r"request unsuccessful", "access_denied", "imperva"),
    (r"^access denied", "access_denied", None),
    (r"security check", "captcha", None),
    (r"checking your browser", "js_challenge", None),
    (r"ddos-guard|ddos protection", "js_challenge", "ddos-guard"),
    (r"too many requests", "rate_limited", None),
    (r"^blocked$|you have been blocked", "access_denied", None),
]
_TEXT_PATTERNS = [
    (r"verify (that )?you are (a )?human|i'?m not a robot|complete the security check", "captcha"),
    (r"checking (if the site connection is secure|your browser)", "js_challenge"),
    (r"unusual traffic from your (computer|network)", "captcha"),
    (r"please enable (js|javascript) and disable any ad ?blocker", "captcha"),
    (r"access (to this page )?(has been )?denied|you don'?t have permission to access", "access_denied"),
    (r"incapsula incident id|request unsuccessful", "access_denied"),
    (r"automated (browser|access|queries) detected", "access_denied"),
]


def detect_block(page, *, status: Optional[int] = None, headers: Optional[dict] = None) -> Optional[dict]:
    """Return a description of the block/challenge on ``page`` or ``None``."""
    try:
        markers = page.evaluate(BLOCK_MARKERS_JS) or {}
    except Exception:  # noqa: BLE001 - page navigating
        return None
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    title = str(markers.get("title", ""))
    text = str(markers.get("text", ""))
    short = int(markers.get("text_length") or 0) < 3000
    signals = []
    kind = None
    vendor = None
    widget = [k for k in ("recaptcha", "hcaptcha", "turnstile", "datadome", "perimeterx", "arkose", "geetest", "generic")
              if markers.get(k)]
    # who is blocking us: the protection service named by response headers wins over the widget it shows
    if hdrs.get("cf-mitigated") or ("cloudflare" in hdrs.get("server", "").lower() and status in (403, 429, 503)):
        vendor = "cloudflare"
    elif "x-datadome" in hdrs or "x-dd-b" in hdrs:
        vendor = "datadome"
    elif any(h.startswith("x-px") for h in hdrs):
        vendor = "perimeterx"
    elif "x-iinfo" in hdrs or "incap_ses" in hdrs.get("set-cookie", "").lower():
        vendor = "imperva"
    elif "akamai" in hdrs.get("server", "").lower() and status == 403:
        vendor = "akamai"
    if widget:
        signals.append(f"widget:{','.join(widget)}")
        kind = "captcha" if not (widget == ["turnstile"] and "just a moment" in title.lower()) else "js_challenge"
        vendor = vendor or {"turnstile": "cloudflare", "datadome": "datadome", "perimeterx": "perimeterx",
                            "arkose": "arkose", "recaptcha": "google-recaptcha", "hcaptcha": "hcaptcha"}.get(widget[0])
    for rx, k, v in _TITLE_PATTERNS:
        if re.search(rx, title, re.I):
            signals.append(f"title:{title[:60]}")
            kind = kind or k
            vendor = vendor or v
            break
    if short:
        for rx, k in _TEXT_PATTERNS:
            if re.search(rx, text, re.I):
                signals.append(f"text:{re.search(rx, text, re.I).group(0)[:60]}")
                kind = kind or k
                break
    if hdrs.get("cf-mitigated") == "challenge":
        signals.append("header:cf-mitigated=challenge")
        kind = kind or "js_challenge"
        vendor = vendor or "cloudflare"
    if "x-datadome" in hdrs or "x-dd-b" in hdrs:
        signals.append("header:datadome")
        vendor = vendor or "datadome"
    if status in (403, 429, 503):
        signals.append(f"status:{status}")
        if status == 429:
            kind = kind or "rate_limited"
    strong = bool(widget) or any(s.startswith(("title:", "header:")) for s in signals)
    if status in (403, 429, 503) and (kind or short):
        strong = True
    if not strong or not kind:
        return None
    confidence = "high" if (widget and status in (403, 429, 503)) or len(signals) >= 3 else "medium"
    return {"blocked": True, "kind": kind, "vendor": vendor, "widget": widget[0] if widget else None,
            "confidence": confidence, "signals": signals, "url": page.url, "title": title, "status": status,
            "detected_at": iso()}


@dataclass
class HumanInTheLoop:
    """How to react when a CAPTCHA/bot wall is detected.

    ``mode``: ``"wait"`` (pause until a human solves it — default), ``"fail"``
    (raise :class:`BlockedError` immediately) or ``"off"`` (ignore).
    ``handoff``: when headless, open a *visible* browser with the same cookies
    for the human, then copy the clearance cookies back.
    ``on_pause``: callback(session, info) run once when pausing (automation /
    tests can solve the challenge here); ``notify``: callback(info) for alerts.
    """

    mode: str = "wait"
    timeout_s: float = 300.0
    poll_s: float = 1.0
    handoff: bool = True
    bell: bool = True
    notify: Optional[Callable] = None
    on_pause: Optional[Callable] = None

    @classmethod
    def from_any(cls, value: Any) -> "HumanInTheLoop":
        if isinstance(value, HumanInTheLoop):
            return value
        if value is None or value is True:
            return cls()
        if value is False:
            return cls(mode="off")
        if isinstance(value, str):
            return cls(mode=value)
        if isinstance(value, dict):
            return cls(**value)
        raise TypeError(f"unsupported hitl value {value!r}")

    def handle(self, session: Any, info: dict) -> dict:
        log = get_logger("hitl")
        marker = Path(session.out_dir) / "PAUSED.json"
        resume_file = Path(session.out_dir) / "RESUME"
        shot = None
        try:
            shot = session.screenshot(Path(session.out_dir) / "evidence" / f"blocked-{int(time.time())}.png")
        except Exception:  # noqa: BLE001
            pass
        payload = dict(info, screenshot=shot, mode=self.mode, resume_file=str(resume_file),
                       instructions=("Solve the challenge in the browser window (or run headed), then the run "
                                     f"continues automatically. Or create the file {resume_file} to resume."))
        session.events.emit("blocked", level="warning", block_kind=info.get("kind"), vendor=info.get("vendor"),
                            url=info.get("url"), action_id=session.recorder.current_action)
        session.hooks.emit("blocked", session=session, info=payload)
        if self.mode == "fail":
            raise BlockedError(f"blocked by {info.get('kind')} at {info.get('url')}", details=payload,
                               hint="retry with stealth=True, a slower rate limit, or hitl='wait' with headed=True")
        write_json(marker, payload)
        msg = (f"\n[agenttrace] BLOCKED ({info.get('kind')}, {info.get('vendor') or 'unknown vendor'}) at {info.get('url')}\n"
               f"[agenttrace] Waiting up to {int(self.timeout_s)}s for a human to solve it. Marker: {marker}\n")
        try:
            sys.stderr.write(("\a" if self.bell else "") + msg)
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        if self.notify:
            try:
                self.notify(payload)
            except Exception as exc:  # noqa: BLE001
                log.warning("notify callback failed: %s", exc)
        start = mono_ms()
        how = None
        if self.on_pause is not None:
            self.on_pause(session, payload)
        elif self.handoff and session.profile.headless:
            how = self._handoff(session, info, log)
        page = session.page
        while how is None:
            if resume_file.exists():
                how = "resume-file"
                break
            still = detect_block(page)
            if still is None:
                how = "solved"
                break
            if mono_ms() - start > self.timeout_s * 1000:
                try:
                    marker.unlink()
                except OSError:
                    pass
                raise BlockedError(f"challenge not solved within {int(self.timeout_s)}s", details=payload,
                                   hint="run with headed=True so a human can solve it, or raise hitl timeout")
            session.recorder.pump(self.poll_s * 1000, page)
        for f in (marker, resume_file):
            try:
                f.unlink()
            except OSError:
                pass
        waited = round((mono_ms() - start) / 1000.0, 2)
        session.events.emit("unblocked", how=how, waited_s=waited, url=session.page.url)
        return {"resolved": True, "how": how, "waited_s": waited, "url": session.page.url}

    def _handoff(self, session: Any, info: dict, log) -> Optional[str]:
        """Open a visible browser with the same cookies; copy clearance back."""
        try:
            pw = session.engine.playwright
            browser_type = getattr(pw, session.profile.browser)
            headed = browser_type.launch(headless=False)
        except Exception as exc:  # noqa: BLE001 - no display available
            log.warning("could not open a visible browser for the human (%s); waiting on RESUME file", exc)
            return None
        try:
            ctx = headed.new_context(storage_state=session.context.storage_state(),
                                     user_agent=session.evaluate("() => navigator.userAgent"))
            page = ctx.new_page()
            page.goto(info.get("url") or session.page.url)
            start = mono_ms()
            while mono_ms() - start < self.timeout_s * 1000:
                if detect_block(page) is None:
                    break
                page.wait_for_timeout(self.poll_s * 1000)
            else:
                return None
            session.context.add_cookies(ctx.cookies())
            session.page.reload()
            return "handoff"
        finally:
            try:
                headed.close()
            except Exception:  # noqa: BLE001
                pass



# ════════════════════════════════════════════════════════════════════════════
# concurrency.py — rate limits, Retry-After, polite HTTP client, task pool (Parts 34, 40)
# ════════════════════════════════════════════════════════════════════════════
# Target-site politeness (Part 40), a polite HTTP client, and concurrent task
# execution with resource limits (Part 34).
#
# ``RateLimiter`` enforces, per host: a minimum interval between requests, a
# maximum number of concurrent requests, and server back-pressure — a ``429`` /
# ``503`` with ``Retry-After`` blocks the host until that deadline.  A
# configured (hard-coded) delay can only make things *slower*, never override
# the server: ``wait = max(configured interval, Retry-After deadline)``.


def parse_retry_after(value: Any, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return max(0.0, dt.timestamp() - (now if now is not None else time.time()))


def _host(url_or_host: str) -> str:
    if "://" in url_or_host:
        return (urlsplit(url_or_host).hostname or "").lower()
    return url_or_host.lower()


@dataclass
class _HostState:
    interval: float
    base: float = 0.0
    next_allowed: float = 0.0
    blocked_until: float = 0.0
    active: int = 0
    requests: int = 0
    throttled_s: float = 0.0
    retry_after_events: int = 0
    last_status: Optional[int] = None
    last_start: float = 0.0
    prev_start: float = 0.0
    floor: float = 0.0
    peak_active: int = 0


class RateLimiter:
    """Per-domain politeness controller (thread-safe)."""

    def __init__(self, *, min_interval_s: float = 0.0, per_domain: Optional[dict] = None,
                 max_concurrency_per_domain: int = 2, adaptive: bool = True,
                 max_retry_after_s: float = 900.0, max_adaptive_interval_s: float = 10.0) -> None:
        self.min_interval_s = float(min_interval_s)
        self.max_adaptive_interval_s = float(max_adaptive_interval_s)
        self.per_domain = {k.lower(): float(v) for k, v in (per_domain or {}).items()}
        self.max_concurrency = max(1, int(max_concurrency_per_domain))
        self.adaptive = adaptive
        self.max_retry_after_s = max_retry_after_s
        self._hosts: dict = {}
        self._cond = threading.Condition()
        self._log = get_logger("ratelimit")

    @classmethod
    def from_any(cls, value: Any) -> Optional["RateLimiter"]:
        if value is None or value is False:
            return None
        if isinstance(value, RateLimiter):
            return value
        if isinstance(value, (int, float)):
            return cls(min_interval_s=float(value))
        if isinstance(value, dict):
            return cls(**value)
        raise TypeError(f"unsupported rate_limit value {value!r}")

    def _state(self, host: str) -> _HostState:
        st = self._hosts.get(host)
        if st is None:
            interval = self.per_domain.get(host, self.min_interval_s)
            for dom, iv in self.per_domain.items():
                if host.endswith("." + dom):
                    interval = iv
            st = self._hosts[host] = _HostState(interval=interval, base=interval)
        return st

    def delay_needed(self, url_or_host: str) -> float:
        host = _host(url_or_host)
        with self._cond:
            st = self._state(host)
            now = time.monotonic()
            return max(0.0, st.next_allowed - now, st.blocked_until - now)

    def acquire(self, url_or_host: str, *, sleeper: Optional[Callable[[float], None]] = None) -> float:
        """Block until a request to this host is allowed; returns seconds waited."""
        host = _host(url_or_host)
        waited = 0.0
        sleep = sleeper or (lambda s: time.sleep(s))
        while True:
            with self._cond:
                st = self._state(host)
                now = time.monotonic()
                gate = max(st.next_allowed, st.blocked_until)
                if now >= gate and st.active < self.max_concurrency:
                    st.active += 1
                    st.peak_active = max(st.peak_active, st.active)
                    st.requests += 1
                    st.prev_start, st.last_start = st.last_start, now
                    st.next_allowed = now + st.interval
                    st.throttled_s += waited
                    return waited
                delay = max(0.01, gate - now) if now < gate else 0.05
            delay = min(delay, 1.0)
            sleep(delay)
            waited += delay

    def release(self, url_or_host: str) -> None:
        host = _host(url_or_host)
        with self._cond:
            st = self._state(host)
            st.active = max(0, st.active - 1)
            self._cond.notify_all()

    def note_response(self, url_or_host: str, status: Optional[int], headers: Optional[dict] = None) -> Optional[float]:
        """Feed a response back; returns the imposed wait (s) for 429/503."""
        host = _host(url_or_host)
        hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}
        with self._cond:
            st = self._state(host)
            st.last_status = status
            if status not in (429, 503):
                low = max(st.base, st.floor)
                if status is not None and status < 400 and st.interval > low:
                    # the server is happy again: decay the adaptive delay back towards the configured
                    # one, but never below the pace that already earned a 429 (learned floor)
                    st.interval = st.interval * 0.7 if st.interval * 0.7 > low + 0.05 else low
                return None
            retry = parse_retry_after(hdrs.get("retry-after"))
            if retry is None:
                retry = max(1.0, st.interval * 2 or 1.0)
            if retry > self.max_retry_after_s:
                raise RateLimitError(f"{host} asked to wait {retry:.0f}s (> max_retry_after_s)",
                                     details={"host": host, "retry_after_s": retry},
                                     hint="increase max_retry_after_s or try later")
            now = time.monotonic()
            st.blocked_until = max(st.blocked_until, now + retry)
            st.retry_after_events += 1
            if status == 429 and st.prev_start and st.last_start > st.prev_start:
                # the gap between the last two requests was too short for this server: remember it
                st.floor = min(max(st.floor, (st.last_start - st.prev_start) * 1.25), self.max_adaptive_interval_s)
            if self.adaptive:
                grow = 2.0 if status == 429 else 1.5
                cap = max(st.base, self.max_adaptive_interval_s)
                st.interval = min(max(st.interval * grow, min(retry / 2, cap), 0.25), cap)
            self._log.info("rate limited by %s: status=%s retry_after=%.2fs new_interval=%.2fs",
                           host, status, retry, st.interval)
            return retry

    def stats(self) -> dict:
        with self._cond:
            return {h: {"requests": s.requests, "interval_s": round(s.interval, 3), "learned_floor_s": round(s.floor, 3),
                        "throttled_s": round(s.throttled_s, 2), "retry_after_events": s.retry_after_events,
                        "peak_concurrency": s.peak_active, "last_status": s.last_status} for h, s in self._hosts.items()}


@dataclass
class HttpResponse:
    status: int
    url: str
    headers: dict
    body: bytes
    elapsed_ms: float
    attempts: int
    history: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class HttpClient:
    """Polite urllib client: rate limits, Retry-After, retries, cookies, proxy."""

    def __init__(self, *, limiter: Optional[RateLimiter] = None, max_retries: int = 4, timeout: float = 30.0,
                 proxy: Optional[str] = None, headers: Optional[dict] = None, verify: bool = True,
                 user_agent: str = "Mozilla/5.0 (compatible; AgentTrace/2.0)") -> None:
        self.limiter = limiter or RateLimiter()
        self.max_retries = max_retries
        self.timeout = timeout
        self.headers = {"User-Agent": user_agent, **(headers or {})}
        self.cookies = CookieJar()
        handlers: list = [urllib.request.HTTPCookieProcessor(self.cookies)]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)
        self.log: list = []

    def request(self, method: str, url: str, *, headers: Optional[dict] = None, body: Any = None,
                json_body: Any = None) -> HttpResponse:
        data = body
        hdrs = dict(self.headers)
        hdrs.update(headers or {})
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        if isinstance(data, str):
            data = data.encode("utf-8")
        attempts = 0
        history = []
        start = mono_ms()
        while True:
            attempts += 1
            self.limiter.acquire(url)
            status, rheaders, rbody = 0, {}, b""
            err = None
            try:
                req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
                with self._opener.open(req, timeout=self.timeout) as resp:
                    status = resp.status
                    rheaders = {k: v for k, v in resp.headers.items()}
                    rbody = resp.read()
            except urllib.error.HTTPError as exc:
                status = exc.code
                rheaders = {k: v for k, v in (exc.headers or {}).items()}
                rbody = exc.read() if hasattr(exc, "read") else b""
            except (urllib.error.URLError, OSError) as exc:
                err = str(exc)
            finally:
                self.limiter.release(url)
            entry = {"ts": iso(), "method": method.upper(), "url": url, "status": status, "error": err}
            self.log.append(entry)
            history.append(entry)
            retry_after = self.limiter.note_response(url, status, rheaders) if status else None
            retryable = err is not None or status in (429, 500, 502, 503, 504)
            if not retryable or attempts > self.max_retries:
                if err is not None and status == 0:
                    raise ConnectionError(f"{method} {url} failed after {attempts} attempts: {err}")
                return HttpResponse(status=status, url=url, headers=rheaders, body=rbody,
                                    elapsed_ms=round(mono_ms() - start, 1), attempts=attempts, history=history)
            if retry_after is None:
                time.sleep(min(8.0, 0.4 * (2 ** (attempts - 1))) * (1 + random.random() * 0.2))

    def get(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("POST", url, **kw)


# ═══════════════════════════════════════════════════════════════════════
# concurrent tasks (Part 34)
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class TaskResult:
    name: str
    ok: bool
    value: Any
    error: Optional[str]
    started: str
    ended: str
    duration_s: float
    thread: str


class TaskPool:
    """Run independent tasks in parallel threads with a hard concurrency cap.

    Each task runs in its own thread; tasks that drive a browser must create
    their *own* :class:`Session` (Playwright sync objects are per-thread), which
    also guarantees state isolation between tasks.
    """

    def __init__(self, max_workers: int = 3, *, min_free_memory_mb: Optional[int] = None) -> None:
        self.max_workers = max(1, int(max_workers))
        self.min_free_memory_mb = min_free_memory_mb
        self._sem = threading.Semaphore(self.max_workers)
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.timeline: list = []

    def _memory_ok(self) -> bool:
        if not self.min_free_memory_mb:
            return True
        try:
            import psutil  # optional
        except ImportError:
            return True
        return psutil.virtual_memory().available / (1024 * 1024) >= self.min_free_memory_mb

    def run(self, tasks: Iterable[Any]) -> list:
        """``tasks``: callables or ``(name, callable)`` pairs. Returns TaskResults in order."""
        items = []
        for i, task in enumerate(tasks):
            if isinstance(task, tuple):
                items.append((str(task[0]), task[1]))
            else:
                items.append((getattr(task, "__name__", f"task{i + 1}"), task))
        results: list = [None] * len(items)
        threads = []

        def worker(index: int, name: str, fn: Callable) -> None:
            with self._sem:
                while not self._memory_ok():
                    time.sleep(0.5)
                with self._lock:
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                    self.timeline.append((time.time(), name, "start", self.active))
                started, t0 = iso(), time.monotonic()
                try:
                    value, ok, err = fn(), True, None
                except Exception as exc:  # noqa: BLE001 - reported per task
                    value, ok, err = None, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
                finally:
                    with self._lock:
                        self.active -= 1
                        self.timeline.append((time.time(), name, "end", self.active))
                results[index] = TaskResult(name=name, ok=ok, value=value, error=err, started=started, ended=iso(),
                                            duration_s=round(time.monotonic() - t0, 2),
                                            thread=threading.current_thread().name)

        for index, (name, fn) in enumerate(items):
            t = threading.Thread(target=worker, args=(index, name, fn), name=f"at-task-{name}", daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return results



# ════════════════════════════════════════════════════════════════════════════
# elements.py — selector-free element resolution + self-healing (Parts 6, 22)
# ════════════════════════════════════════════════════════════════════════════
# Selector-free element observation, natural-language matching and
# self-healing resolution (Parts 6, 15, 22).
#
# ``observe_elements`` tags every visible interactive element with a stable ref
# (``e12``; ``f2e5`` inside frame 2) and a rich *fingerprint* (role, accessible
# name, text, id/name/test-id attributes, label, placeholder, href, CSS path,
# ancestry, nearby row text, position).  Targets can then be given as:
#
# * a ref from ``observe()``                  → ``"e12"``
# * a CSS/XPath/text/role selector            → ``"css=#buy"``, ``"#buy"``, ``"xpath=//button"``
# * plain language                            → ``"Add to cart"``, ``"second product link"``,
#                                               ``"Buy button for Linen Throw Pillow"``,
#                                               ``"email field"``, ``"the 'Sign in' button"``
# * a recorded fingerprint dict (workflows)   → healed by weighted similarity when the
#                                               original selector no longer matches.


OBSERVE_JS = r"""
(opts) => {
  const MAX = opts.max || 400, PFX = opts.prefix || 'e';
  const SEL = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=checkbox],' +
    '[role=radio],[role=tab],[role=menuitem],[role=option],[role=switch],[role=combobox],[role=textbox],[role=searchbox],' +
    '[onclick],[contenteditable=""],[contenteditable=true],[tabindex]:not([tabindex="-1"])';
  const out = []; let counter = window.__atRefCounter || 0;
  const txt = (el) => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05; };
  const labelOf = (el) => {
    try { if (el.labels && el.labels.length) return txt(el.labels[0]); } catch (e) {}
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(txt).join(' '); if (t) return t; }
    const wrap = el.closest('label'); return wrap ? txt(wrap) : '';
  };
  const implicitRole = (el) => {
    const t = el.tagName.toLowerCase(), ty = (el.getAttribute('type') || '').toLowerCase();
    if (el.getAttribute('role')) return el.getAttribute('role');
    if (t === 'a') return 'link'; if (t === 'button' || t === 'summary') return 'button'; if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') { if (['checkbox', 'radio'].includes(ty)) return ty; if (['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'search') return 'searchbox'; if (ty === 'range') return 'slider'; return 'textbox'; }
    if (el.isContentEditable) return 'textbox'; return 'generic';
  };
  const accName = (el, role) => {
    const al = el.getAttribute('aria-label'); if (al) return al.trim();
    const lb = labelOf(el); const t = el.tagName.toLowerCase();
    if (t === 'input' || t === 'textarea' || t === 'select') {
      if (lb) return lb; if (el.placeholder) return el.placeholder; if (el.title) return el.title;
      if (['submit', 'button', 'reset'].includes((el.type || '').toLowerCase())) return el.value || '';
      return el.getAttribute('name') || '';
    }
    const it = txt(el); if (it) return it.slice(0, 120);
    const img = el.querySelector && el.querySelector('img[alt]'); if (img) return img.alt;
    return el.title || lb || '';
  };
  const cssPath = (el) => {
    if (el.id && /^[A-Za-z][\w-]*$/.test(el.id) && document.querySelectorAll('#' + el.id).length === 1) return '#' + el.id;
    const parts = []; let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 7 && cur !== document.body) {
      let part = cur.tagName.toLowerCase();
      if (cur.id && /^[A-Za-z][\w-]*$/.test(cur.id)) { parts.unshift('#' + cur.id); break; }
      const sib = cur.parentElement ? Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName) : [];
      if (sib.length > 1) part += ':nth-of-type(' + (sib.indexOf(cur) + 1) + ')';
      parts.unshift(part); cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  const ancestry = (el) => { const a = []; let cur = el.parentElement;
    while (cur && a.length < 4 && cur !== document.body) { a.push(cur.tagName.toLowerCase() + (cur.id ? '#' + cur.id : '') + (cur.classList[0] ? '.' + cur.classList[0] : '')); cur = cur.parentElement; }
    return a; };
  const near = (el) => {
    const box = el.closest('li,tr,article,section,.card,.row,[class*=item],[class*=row],[class*=product],fieldset,form');
    if (!box || box === el) return '';
    const own = txt(el); let t = txt(box); if (own) t = t.replace(own, ' ');
    return t.replace(/\s+/g, ' ').trim().slice(0, 140);
  };
  const landmark = (el) => { const l = el.closest('header,nav,main,footer,aside,[role=dialog],dialog,form');
    return l ? (l.getAttribute('role') || l.tagName.toLowerCase()) : ''; };
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (seen.has(el)) continue; seen.add(el);
    const vis = visible(el);
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (!vis) { const lab = (type === 'checkbox' || type === 'radio') && el.labels && el.labels.length && visible(el.labels[0]);
      if (!lab && !opts.includeHidden) continue; }
    let ref = el.getAttribute('data-at-ref');
    if (!ref || !ref.startsWith(PFX)) { counter += 1; ref = PFX + counter; el.setAttribute('data-at-ref', ref); }
    const r = el.getBoundingClientRect(); const role = implicitRole(el);
    const attrs = {};
    for (const a of ['name', 'placeholder', 'title', 'aria-label', 'href', 'data-testid', 'data-test', 'data-qa', 'data-cy',
                     'alt', 'for', 'value', 'autocomplete', 'rel', 'target']) {
      let v = el.getAttribute(a); if (v === null) continue; if (a === 'value' && type === 'password') v = '***'; attrs[a] = v.slice(0, 200); }
    const item = { ref, tag, role, type, name: accName(el, role).slice(0, 150), text: txt(el).slice(0, 150),
      id: el.id || '', classes: Array.from(el.classList).slice(0, 6), attrs, label: labelOf(el).slice(0, 120),
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'), visible: vis,
      checked: (type === 'checkbox' || type === 'radio') ? !!el.checked : (el.getAttribute('aria-checked') === 'true' ? true : null),
      selected: el.getAttribute('aria-selected') === 'true' ? true : null,
      bbox: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      in_viewport: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth,
      css: cssPath(el), ancestry: ancestry(el), near: near(el), landmark: landmark(el),
      editable: (['input', 'textarea'].includes(tag) && !['checkbox', 'radio', 'submit', 'button', 'reset', 'image', 'file'].includes(type)) || el.isContentEditable,
      value: (['input', 'textarea'].includes(tag) && type !== 'password') ? String(el.value || '').slice(0, 120) : undefined };
    if (tag === 'select') item.options = Array.from(el.options).slice(0, 40).map(o => ({ value: o.value, text: o.text.trim(), selected: o.selected }));
    out.push(item); if (out.length >= MAX) break;
  }
  window.__atRefCounter = counter;
  return out;
}
"""

SYNONYMS = [
    {"cart", "basket", "bag", "trolley"}, {"sign in", "log in", "login", "signin"},
    {"sign up", "register", "create account", "signup"}, {"search", "find", "lookup"},
    {"next", "more", "older", "›", "»", "→", "forward"}, {"previous", "prev", "newer", "‹", "«", "←", "back"},
    {"submit", "send", "continue", "proceed", "go"}, {"delete", "remove", "trash"},
    {"buy", "purchase", "checkout", "order"}, {"close", "dismiss", "×", "x", "no thanks"},
    {"subscribe", "sign up now", "join"}, {"email", "e-mail", "mail"}, {"password", "passcode", "pass"},
    {"username", "user name", "user", "login name"}, {"refresh", "reload"}, {"help", "support", "faq"},
    {"agree", "accept", "consent"}, {"sort", "order by"}, {"quantity", "qty", "amount"},
]
ROLE_WORDS = {
    "button": "button", "btn": "button", "link": "link", "field": "textbox", "input": "textbox", "box": "textbox",
    "textbox": "textbox", "textarea": "textbox", "searchbox": "searchbox", "checkbox": "checkbox", "tick": "checkbox",
    "radio": "radio", "dropdown": "combobox", "select": "combobox", "combobox": "combobox", "menu": "combobox",
    "tab": "tab", "option": "option", "switch": "switch", "toggle": "switch",
}
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
            "last": -1}
_STOP = {"the", "a", "an", "on", "to", "click", "press", "tap", "hit", "please", "at", "into", "in", "this", "that",
         "element", "item", "labelled", "labeled", "named", "called", "with", "text", "choose", "select", "open"}
_REF_RE = re.compile(r"^(f\d+)?e\d+$")


def norm_text(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^\w\s×›»‹«→←-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list:
    return [t for t in norm_text(text).split() if t]


def _syn_forms(phrase: str) -> set:
    base = norm_text(phrase)
    forms = {base}
    for group in SYNONYMS:
        for word in group:
            if re.search(rf"(^|\s){re.escape(word)}(\s|$)", base):
                for alt in group:
                    forms.add(re.sub(rf"(^|\s){re.escape(word)}(\s|$)", rf"\g<1>{alt}\g<2>", base).strip())
    return forms


@dataclass
class ElementInfo:
    ref: str
    tag: str
    role: str = ""
    type: str = ""
    name: str = ""
    text: str = ""
    id: str = ""
    classes: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    label: str = ""
    disabled: bool = False
    visible: bool = True
    checked: Optional[bool] = None
    selected: Optional[bool] = None
    bbox: dict = field(default_factory=dict)
    in_viewport: bool = True
    css: str = ""
    ancestry: list = field(default_factory=list)
    near: str = ""
    landmark: str = ""
    editable: bool = False
    value: Optional[str] = None
    options: Optional[list] = None
    frame_id: str = ""
    frame_index: int = 0
    order: int = 0

    @classmethod
    def from_js(cls, data: dict, frame_id: str, frame_index: int, order: int) -> "ElementInfo":
        known = cls.__dataclass_fields__  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known and v is not None}
        return cls(frame_id=frame_id, frame_index=frame_index, order=order, **kwargs)

    def describe(self) -> str:
        label = self.name or self.text or self.attrs.get("placeholder") or self.attrs.get("name") or self.id or self.css
        return f"{self.role or self.tag} {label[:60]!r}"

    def fingerprint(self) -> dict:
        """Stable features used to re-find this element later (self-healing)."""
        fp = {"tag": self.tag, "role": self.role, "type": self.type, "name": self.name, "text": self.text[:120],
              "id": self.id, "classes": self.classes[:6], "label": self.label, "css": self.css,
              "ancestry": self.ancestry[:4], "near": self.near[:120], "landmark": self.landmark,
              "bbox": self.bbox}
        for key in ("name", "placeholder", "data-testid", "data-test", "data-qa", "data-cy", "href", "aria-label",
                    "title", "alt"):
            if self.attrs.get(key):
                fp[f"attr:{key}"] = self.attrs[key]
        return {k: v for k, v in fp.items() if v not in (None, "", [], {})}

    def to_dict(self, compact: bool = True) -> dict:
        if compact:
            out = {"ref": self.ref, "role": self.role or self.tag}
            if self.name:
                out["name"] = self.name[:80]
            if self.text and self.text[:80] != out.get("name"):
                out["text"] = self.text[:80]
            for key in ("href", "placeholder", "name"):
                if self.attrs.get(key) and key != "name":
                    out[key] = self.attrs[key][:120]
            if self.value:
                out["value"] = self.value[:60]
            if self.disabled:
                out["disabled"] = True
            if self.checked is not None:
                out["checked"] = self.checked
            if self.options:
                out["options"] = [o["text"] for o in self.options[:12]]
            if self.near and self.role in ("button", "link", "checkbox"):
                out["context"] = self.near[:70]
            if not self.in_viewport:
                out["offscreen"] = True
            if self.frame_index:
                out["frame"] = self.frame_id
            return out
        return {k: getattr(self, k) for k in self.__dataclass_fields__}  # type: ignore[attr-defined]


def observe_elements(page, *, frame_ids: Optional[dict] = None, include_frames: bool = True,
                     include_hidden: bool = False, limit: int = 400) -> list:
    """Collect interactive elements from the page (and its frames)."""
    frames = list(page.frames) if include_frames else [page.main_frame]
    out: list = []
    order = 0
    for index, frame in enumerate(frames):
        if index and (frame.is_detached() if hasattr(frame, "is_detached") else False):
            continue
        prefix = "e" if frame == page.main_frame else f"f{index}e"
        try:
            raw = frame.evaluate(OBSERVE_JS, {"max": limit, "prefix": prefix, "includeHidden": include_hidden})
        except Exception:  # noqa: BLE001 - detached / navigating frame
            continue
        fid = (frame_ids or {}).get(frame, f"frame{index}")
        for item in raw or []:
            order += 1
            out.append(ElementInfo.from_js(item, fid, 0 if frame == page.main_frame else index, order))
    return out


# ═══════════════════════════════════════════════════════════════════════
# natural-language matching
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class TargetQuery:
    raw: str
    text: str
    quoted: Optional[str] = None
    role: Optional[str] = None
    ordinal: Optional[int] = None
    context: Optional[str] = None
    phrase: str = ""


def parse_target(target: str) -> TargetQuery:
    raw = target.strip()
    quoted = None
    m = re.search(r"[\"'“‘]([^\"'”’]+)[\"'”’]", raw)
    if m:
        quoted = m.group(1).strip()
    rest = raw
    context = None
    m2 = re.search(r"\s+(?:for|of|next to|beside|in the row (?:for|of)|in row)\s+(.+)$", rest, re.I)
    if m2 and len(rest[:m2.start()].split()) <= 5:
        context = m2.group(1).strip().strip("\"'")
        rest = rest[:m2.start()]
    words = norm_text(rest).split()
    role = None
    ordinal = None
    kept = []
    phrase_words = []
    for i, word in enumerate(words):
        if word in ORDINALS and ordinal is None:
            ordinal = ORDINALS[word]
            continue
        if re.fullmatch(r"#?\d+(st|nd|rd|th)", word) and ordinal is None:
            ordinal = int(re.sub(r"\D", "", word))
            continue
        if word in ROLE_WORDS and role is None and (i == len(words) - 1 or word not in ("select", "menu", "option")):
            if i == len(words) - 1 or len(words) > 1:
                role = ROLE_WORDS[word]
                continue
        phrase_words.append(word)
        if word in _STOP:
            continue
        kept.append(word)
    while phrase_words and phrase_words[0] in ("click", "press", "tap", "hit", "please", "the", "a", "an", "on", "open"):
        phrase_words.pop(0)
    text = quoted or " ".join(kept)
    phrase = quoted or " ".join(phrase_words)
    return TargetQuery(raw=raw, text=text, quoted=quoted, role=role, ordinal=ordinal, context=context, phrase=phrase)


def _role_compatible(want: str, el: ElementInfo) -> bool:
    have = el.role or el.tag
    if want == have:
        return True
    groups = [{"textbox", "searchbox", "combobox"}, {"button", "link", "menuitem", "tab"}, {"checkbox", "switch"}]
    if want == "textbox" and el.editable:
        return True
    return any(want in g and have in g for g in groups)


def score_element(query: TargetQuery, el: ElementInfo) -> tuple:
    """Score 0..100 for how well ``el`` matches the parsed query."""
    reasons = []
    fields = [("name", el.name), ("text", el.text), ("label", el.label), ("placeholder", el.attrs.get("placeholder", "")),
              ("title", el.attrs.get("title", "")), ("aria", el.attrs.get("aria-label", "")),
              ("value", el.attrs.get("value", "") if el.role == "button" else "")]
    attr_fields = [("id", el.id), ("name-attr", el.attrs.get("name", "")), ("testid", el.attrs.get("data-testid", "")
                   or el.attrs.get("data-test", "") or el.attrs.get("data-qa", "") or el.attrs.get("data-cy", "")),
                   ("href", el.attrs.get("href", ""))]
    q = norm_text(query.text)
    best = 0
    phrase = norm_text(query.phrase or query.text)
    if phrase and phrase != q:
        pforms = _syn_forms(phrase)
        for label, value in fields:
            v = norm_text(value)
            if v and v in pforms:
                score = 100 if v == phrase else 92
                if score > best:
                    best = score
                    reasons = [f"{label}={value[:40]!r}"]
    if q:
        forms = _syn_forms(q)
        qtokens = set(_tokens(q))
        for label, value in fields:
            v = norm_text(value)
            if not v:
                continue
            if v in forms:
                score = 100 if v == q else 92
            elif any(f and (f in v) for f in forms):
                ratio = len(q) / max(1, len(v))
                score = 80 + int(10 * min(1.0, ratio))
            elif len(v) >= 3 and v in q:
                score = 70
            else:
                vt = set(v.split())
                syn_hits = sum(1 for t in qtokens if t in vt or any(t in g and vt & g for g in SYNONYMS))
                overlap = syn_hits / max(1, len(qtokens))
                score = int(65 * overlap) if overlap < 1 else 72
                if score < 45:
                    score = max(score, int(55 * SequenceMatcher(None, q, v).ratio()) - 5)
            if score > best:
                best = score
                reasons = [f"{label}~{value[:40]!r}"]
        # container / class vocabulary ("first result link", "the product title") - a weaker signal
        struct_tokens = set()
        for chunk in list(el.classes) + list(el.ancestry) + [el.attrs.get("name", ""), el.id]:
            for piece in re.split(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])", str(chunk)):
                if len(piece) >= 3:
                    struct_tokens.add(piece.lower())
                    if piece.lower().endswith("s") and len(piece) > 4:
                        struct_tokens.add(piece.lower()[:-1])
        if qtokens and struct_tokens:
            hits = sum(1 for t in qtokens if t in struct_tokens or (t.endswith("s") and t[:-1] in struct_tokens))
            if hits:
                score = 58 if hits == len(qtokens) else int(45 * hits / len(qtokens))
                if score > best:
                    best = score
                    reasons = [f"structure~{','.join(sorted(qtokens & struct_tokens))[:40]}"]
        slug = q.replace(" ", "")
        for label, value in attr_fields:
            v = norm_text(value).replace(" ", "")
            if v and slug and len(slug) >= 3 and (slug in v or v in slug):
                score = 68 if label != "testid" else 78
                if score > best:
                    best = score
                    reasons = [f"{label}~{value[:40]!r}"]
    elif query.role:
        best = 50
        reasons = ["role-only"]
    raw_exact = (query.quoted or query.raw).strip()
    if raw_exact and raw_exact in (el.name, el.text, el.attrs.get("value", ""), el.attrs.get("aria-label", "")):
        best += 4
        reasons.append("exact-case")
    if query.role:
        if _role_compatible(query.role, el):
            best += 8
            reasons.append(f"role={el.role}")
        else:
            best -= 30
    if query.context:
        ctx = norm_text(query.context)
        hay = norm_text(el.near) + " " + norm_text(" ".join(el.ancestry))
        if ctx and ctx in hay:
            best += 20
            reasons.append("context")
        elif ctx and set(ctx.split()) <= set(hay.split()):
            best += 14
            reasons.append("context~")
        else:
            best -= 40
    if el.disabled:
        best -= 8
    if not el.visible:
        best -= 15
    return max(0, min(130, best)), reasons


MATCH_THRESHOLD = 45


def rank_elements(target: str, elements: Iterable[ElementInfo]) -> list:
    query = parse_target(target)
    scored = []
    for el in elements:
        score, reasons = score_element(query, el)
        if score > 0:
            scored.append((score, el, reasons))
    scored.sort(key=lambda item: (-item[0], item[1].order))
    if query.ordinal is not None and scored:
        top = scored[0][0]
        pool = [s for s in scored if s[0] >= max(MATCH_THRESHOLD, top - 12)]
        pool.sort(key=lambda item: item[1].order)
        idx = query.ordinal - 1 if query.ordinal > 0 else len(pool) - 1
        if 0 <= idx < len(pool):
            chosen = pool[idx]
            scored = [chosen] + [s for s in scored if s is not chosen]
    return scored


# ═══════════════════════════════════════════════════════════════════════
# fingerprint similarity (self-healing, Part 22)
# ═══════════════════════════════════════════════════════════════════════
_FP_WEIGHTS = {
    "text": 3.0, "name": 3.0, "attr:data-testid": 3.0, "attr:data-test": 3.0, "attr:data-qa": 3.0,
    "attr:data-cy": 3.0, "id": 2.0, "attr:name": 2.0, "attr:placeholder": 2.0, "label": 2.0,
    "attr:aria-label": 2.0, "attr:href": 1.5, "role": 1.5, "tag": 1.0, "type": 1.0, "near": 2.5,
    "classes": 0.8, "ancestry": 0.8, "landmark": 0.5, "css": 0.7, "bbox": 0.6, "attr:title": 1.0,
    "attr:alt": 1.0,
}


def _sim(a: str, b: str) -> float:
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in _syn_forms(b) or b in _syn_forms(a):
        return 0.9
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    containment = 0.8 if (a in b or b in a) else 0.0
    return max(jac, containment, SequenceMatcher(None, a, b).ratio() * 0.9)


def fingerprint_similarity(fp: dict, el: ElementInfo) -> tuple:
    cand = el.fingerprint()
    total = 0.0
    got = 0.0
    details = {}
    for key, weight in _FP_WEIGHTS.items():
        if key not in fp:
            continue
        want = fp[key]
        have = cand.get(key)
        if key == "bbox":
            if not have or not want:
                continue
            dist = abs(have.get("x", 0) - want.get("x", 0)) + abs(have.get("y", 0) - want.get("y", 0))
            s = max(0.0, 1.0 - dist / 600.0)
        elif key in ("classes", "ancestry"):
            wa, ha = set(want or []), set(have or [])
            s = len(wa & ha) / max(1, len(wa | ha)) if wa or ha else 0.0
        elif key in ("tag", "role", "type", "landmark"):
            s = 1.0 if want == have else 0.0
            if key == "role" and s == 0.0 and have and want and _role_compatible(want, el):
                s = 0.6
        elif key == "near":
            s = _sim(want, have or "")
            wn = norm_text(want)
            if have and wn and len(wn) > 6:
                s = max(s, 0.95 if wn in norm_text(have) or norm_text(have) in wn else s)
        else:
            s = _sim(str(want), str(have or ""))
        total += weight
        got += weight * s
        details[key] = round(s, 2)
    # text-like evidence present in the fingerprint but absent on the candidate is a strong negative
    score = got / total if total else 0.0
    return round(score, 4), details


def heal(fp: dict, elements: Iterable[ElementInfo], *, min_score: float = 0.5, min_margin: float = 0.04) -> tuple:
    """Best candidate for a fingerprint → (element|None, score, runner_up, details)."""
    ranked = []
    for el in elements:
        if fp.get("tag") and fp["tag"] in ("input", "select", "textarea") and el.tag not in ("input", "select", "textarea"):
            continue
        score, details = fingerprint_similarity(fp, el)
        ranked.append((score, el, details))
    ranked.sort(key=lambda item: (-item[0], item[1].order))
    if not ranked:
        return None, 0.0, 0.0, {}
    best_score, best, details = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < min_score or (best_score - runner < min_margin and best_score < 0.8):
        return None, best_score, runner, details
    return best, best_score, runner, details


def looks_like_selector(target: str) -> bool:
    t = target.strip()
    if re.match(r"^(css|xpath|text|role|id|data-testid)=", t):
        return True
    if t.startswith(("//", "(//", "./")):
        return True
    if re.match(r"^[#.\[][\w\-\[\]=\"':#.\s>~+*()]+$", t):
        return True
    if re.match(r"^[a-z][a-z0-9]*([#.\[][^\s]+)+$", t) or re.match(r"^[a-z][a-z0-9]*(\s*>\s*[a-z#.\[][^\s]*)+$", t):
        return True
    return False


def is_ref(target: str) -> bool:
    return bool(_REF_RE.match(target.strip()))



# ════════════════════════════════════════════════════════════════════════════
# waits.py — intelligent waits & event synchronisation (Part 16)
# ════════════════════════════════════════════════════════════════════════════
# Intelligent waiting & synchronisation (Part 4 settle + Part 16).
#
# No fixed ``sleep()``: every wait observes a real signal —
#
# * network settled (``NetworkRecorder.wait_idle``: nothing relevant in flight,
#   quiet window, background beacons/streams ignored),
# * DOM quiescent (MutationObserver: no mutations for N ms),
# * no visible loading indicator (``aria-busy``, spinners, skeletons, progress bars),
# * explicit conditions: selector state, text, URL, response, request, JS
#   predicate, element count, download.


DOM_WATCH_JS = r"""
() => {
  if (window.__atDom) return window.__atDom.count;
  const st = { count: 0, last: performance.now() };
  window.__atDom = st;
  try {
    new MutationObserver((muts) => { st.count += muts.length; st.last = performance.now(); })
      .observe(document.documentElement || document, { subtree: true, childList: true, attributes: true, characterData: true });
  } catch (e) {}
  return 0;
}
"""

DOM_QUIET_JS = r"""
() => { const st = window.__atDom; if (!st) return 1e9; return performance.now() - st.last; }
"""

TIMER_TRACK_JS = r"""
(() => {
  if (window.__atPendingTimers) return;
  const origST = window.setTimeout, origCT = window.clearTimeout, pending = new Map();
  let inEvent = 0;
  for (const type of ['click', 'input', 'change', 'keydown', 'keyup', 'submit', 'pointerup', 'mouseup', 'dblclick']) {
    window.addEventListener(type, () => { inEvent++; origST.call(window, () => { inEvent = Math.max(0, inEvent - 1); }, 0); }, true);
  }
  const wrapped = function setTimeout(fn, delay, ...rest) {
    const ms = Number(delay) || 0;
    const track = inEvent > 0 && ms > 30 && ms <= 3000;
    let id;
    const cb = typeof fn === 'function' ? function () { pending.delete(id); return fn.apply(this, arguments); } : fn;
    id = origST.call(this, cb, delay, ...rest);
    if (track) pending.set(id, performance.now() + ms);
    return id;
  };
  const clear = function clearTimeout(id) { pending.delete(id); return origCT.call(this, id); };
  for (const [f, name] of [[wrapped, 'setTimeout'], [clear, 'clearTimeout']]) {
    try { Object.defineProperty(f, 'toString', { value: () => 'function ' + name + '() { [native code] }' }); } catch (e) {}
  }
  window.setTimeout = wrapped; window.clearTimeout = clear;
  Object.defineProperty(window, '__atPendingTimers', { value: () => { const now = performance.now(); let left = 0;
    pending.forEach((due, id) => { if (due < now - 5000) pending.delete(id); else left = Math.max(left, due - now); });
    return { count: pending.size, max_left_ms: Math.max(0, Math.round(left)) }; }, enumerable: false });
})();
"""

LOADING_JS = r"""
() => {
  const sel = '[aria-busy="true"],.spinner,.loading,.loader,.is-loading,[class*="skeleton"],progress:not([value]),[role="progressbar"],.lds-ring,.sk-spinner';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity || '1') < 0.05) continue;
    out.push((el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.split(/\s+/)[0] : '')).slice(0, 60));
    if (out.length > 5) break;
  }
  return out;
}
"""


def _eval(page, js: str, default: Any = None) -> Any:
    try:
        return page.evaluate(js)
    except Exception:  # noqa: BLE001 - navigation in progress / page closed
        return default


def ensure_dom_watch(page) -> None:
    _eval(page, DOM_WATCH_JS)


def wait_dom_stable(page, pump, *, quiet_ms: float = 300, max_wait_ms: float = 8000, poll_ms: float = 60) -> dict:
    """Wait until no DOM mutation happened for ``quiet_ms``."""
    start = mono_ms()
    ensure_dom_watch(page)
    while True:
        quiet = _eval(page, DOM_QUIET_JS, 1e9)
        waited = mono_ms() - start
        if quiet is None:
            ensure_dom_watch(page)
            quiet = 0
        if quiet >= quiet_ms:
            return {"dom_stable": True, "waited_ms": round(waited)}
        if waited >= max_wait_ms:
            return {"dom_stable": False, "waited_ms": round(waited)}
        pump(poll_ms)


def pending_timers(page) -> dict:
    """Timers scheduled by user-event handlers that have not fired yet (debounce etc.)."""
    return _eval(page, "() => window.__atPendingTimers ? window.__atPendingTimers() : {count: 0, max_left_ms: 0}",
                 {"count": 0, "max_left_ms": 0}) or {"count": 0, "max_left_ms": 0}


def loading_indicators(page) -> list:
    return _eval(page, LOADING_JS, []) or []


def wait_loading_gone(page, pump, *, max_wait_ms: float = 15000, poll_ms: float = 80) -> dict:
    start = mono_ms()
    while True:
        found = loading_indicators(page)
        waited = mono_ms() - start
        if not found:
            return {"loading_gone": True, "waited_ms": round(waited)}
        if waited >= max_wait_ms:
            return {"loading_gone": False, "waited_ms": round(waited), "indicators": found}
        pump(poll_ms)


def settle_page(page, recorder, *, since_seq: int = 0, quiet_ms: float = 400, max_wait_ms: float = 20000,
                dom_quiet_ms: float = 250, expect: tuple = (), check_loading: bool = True) -> dict:
    """Composite post-action settle: network → loading indicators → DOM → network again."""
    start = mono_ms()

    def pump(ms: float) -> None:
        recorder.pump(ms, page)

    net = recorder.wait_idle(quiet_ms=quiet_ms, max_wait_ms=max_wait_ms, expect=expect, page=page, since_seq=since_seq)
    remaining = max(500.0, max_wait_ms - (mono_ms() - start))
    loading = wait_loading_gone(page, pump, max_wait_ms=min(remaining, 15000)) if check_loading else {}
    dom = wait_dom_stable(page, pump, quiet_ms=dom_quiet_ms, max_wait_ms=min(remaining, 8000))
    again = loading.get("waited_ms", 0) > 50 or dom.get("waited_ms", 0) > dom_quiet_ms + 150
    timers_waited = 0
    for _ in range(3):  # handler-scheduled timers (debounced searches, delayed fetches)
        info = pending_timers(page)
        if not info.get("count"):
            break
        left = float(info.get("max_left_ms") or 0)
        if left > 3000 or mono_ms() - start + left > max_wait_ms:
            break
        pump(left + 60)
        timers_waited += int(left + 60)
        again = True
    if again:
        net = recorder.wait_idle(quiet_ms=quiet_ms, max_wait_ms=max(500.0, max_wait_ms - (mono_ms() - start)),
                                 page=page, since_seq=since_seq)
        dom = wait_dom_stable(page, pump, quiet_ms=dom_quiet_ms, max_wait_ms=min(max(500.0, max_wait_ms - (mono_ms() - start)), 8000))
    out = {"network_idle": bool(net.get("idle")), "pending": net.get("pending", []), "missing": net.get("missing", []),
           "dom_stable": dom.get("dom_stable", True), "loading_gone": loading.get("loading_gone", True),
           "waited_ms": round(mono_ms() - start)}
    if timers_waited:
        out["timers_waited_ms"] = timers_waited
    return out


def _url_matches(pattern: str, url: str) -> bool:
    if pattern.startswith("re:"):
        return re.search(pattern[3:], url) is not None
    if any(ch in pattern for ch in "*?") and not pattern.startswith("http"):
        rx = "^" + re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", ".") + "$"
        return re.search(rx, url) is not None or pattern.strip("*") in url
    return pattern in url


def wait_for_condition(session: Any, condition: Any = None, *, timeout_ms: float = 15000, poll_ms: float = 80,
                       **kw: Any) -> dict:
    """Generic wait. ``condition`` may be a dict or keyword arguments:

    ``selector`` (+``state`` visible|hidden|attached|detached), ``text`` (appears),
    ``text_gone``, ``url`` (substring / glob / ``re:``), ``response`` (URL pattern
    of a finished response, optional ``status``), ``request``, ``function`` (JS
    returning truthy), ``count`` (+``min``) CSS count, ``idle`` (network),
    ``dom_stable`` (ms), ``download`` (True).
    """
    cond = dict(condition or {}) if isinstance(condition, dict) else ({"text": condition} if condition else {})
    cond.update(kw)
    page = session.page
    rec = session.recorder
    start = mono_ms()
    mark = cond.pop("since_seq", None)
    since = rec.mark() if mark is None else mark
    if cond.get("idle"):
        res = rec.wait_idle(quiet_ms=float(cond.get("quiet_ms", 500)), max_wait_ms=timeout_ms, page=page)
        if not res["idle"]:
            raise ActionError("network did not become idle", details=dict(res), hint="use a longer timeout_ms or wait for a specific response")
        return {"ok": True, "condition": "idle", "waited_ms": res["waited_ms"]}
    if cond.get("dom_stable"):
        res = wait_dom_stable(page, lambda ms: rec.pump(ms, page), quiet_ms=float(cond["dom_stable"]),
                              max_wait_ms=timeout_ms)
        return {"ok": res["dom_stable"], "condition": "dom_stable", "waited_ms": res["waited_ms"]}
    downloads_before = len(rec.downloads) + rec.pending_downloads()
    describe = ", ".join(f"{k}={v!r}" for k, v in cond.items())
    while True:
        ok = True
        if "selector" in cond:
            state = cond.get("state", "visible")
            try:
                loc = page.locator(cond["selector"])
                count = loc.count()
                if state in ("detached",):
                    ok = ok and count == 0
                elif state == "attached":
                    ok = ok and count > 0
                elif state == "hidden":
                    ok = ok and (count == 0 or not loc.first.is_visible())
                else:
                    ok = ok and count > 0 and loc.first.is_visible()
            except Exception:  # noqa: BLE001
                ok = False
        if ok and "text" in cond:
            body = _eval(page, "() => document.body ? document.body.innerText : ''", "") or ""
            ok = str(cond["text"]).lower() in body.lower()
        if ok and "text_gone" in cond:
            body = _eval(page, "() => document.body ? document.body.innerText : ''", "") or ""
            ok = str(cond["text_gone"]).lower() not in body.lower()
        if ok and "url" in cond:
            ok = _url_matches(str(cond["url"]), page.url)
        if ok and "response" in cond:
            want_status = cond.get("status")
            ok = any(r.finished and rec._match(str(cond["response"]), r.url) and
                     (want_status is None or r.status == int(want_status)) for r in rec.since(since))
        if ok and "request" in cond:
            ok = any(rec._match(str(cond["request"]), r.url) for r in rec.since(since))
        if ok and "function" in cond:
            try:
                ok = bool(page.evaluate(cond["function"]))
            except Exception:  # noqa: BLE001
                ok = False
        if ok and "count" in cond:
            try:
                ok = page.locator(cond["count"]).count() >= int(cond.get("min", 1))
            except Exception:  # noqa: BLE001
                ok = False
        if ok and cond.get("download"):
            ok = len(rec.downloads) + rec.pending_downloads() > downloads_before
        waited = mono_ms() - start
        if ok:
            return {"ok": True, "condition": describe, "waited_ms": round(waited)}
        if waited >= timeout_ms:
            raise ActionError(f"timed out after {int(waited)}ms waiting for {describe}",
                              details={"condition": cond, "url": page.url},
                              hint="check the condition with observe()/requests(), or raise timeout_ms")
        rec.pump(poll_ms, page)


def sleep_pumped(recorder, page, ms: float) -> None:
    """Sleep while still dispatching Playwright events."""
    end = time.monotonic() + ms / 1000.0
    while True:
        left = (end - time.monotonic()) * 1000.0
        if left <= 0:
            return
        recorder.pump(min(left, 200.0), page)


def page_ready_state(page) -> Optional[str]:
    return _eval(page, "() => document.readyState", None)



# ════════════════════════════════════════════════════════════════════════════
# analysis.py — correlation, expectations, API discovery, regression diff, fingerprints (Parts 13, 14, 24, 30, 49)
# ════════════════════════════════════════════════════════════════════════════
# Traffic analysis: correlation (Part 13), advanced validation (Part 14),
# API discovery (Part 24), regression detection (Part 30) and deterministic run
# fingerprints (Part 49).  Everything works on ``NetRecord`` lists, i.e. on live
# sessions *and* on any HAR file (``har_to_records``).


# ═══════════════════════════════════════════════════════════════════════
# Part 13 — action ↔ request correlation
# ═══════════════════════════════════════════════════════════════════════
def background_patterns(records: Iterable[NetRecord]) -> set:
    """Patterns that recur on their own (polling/heartbeats), i.e. seen at
    least twice while *no* action was running, or scheduled by setInterval."""
    idle_counts: dict = {}
    out = set()
    for rec in records:
        key = pattern_key(rec)
        if rec.extra.get("polling") or (rec.initiator or {}).get("async_parent") == "setInterval":
            out.add(key)
        if rec.action_id is None:
            idle_counts[key] = idle_counts.get(key, 0) + 1
    out.update(k for k, n in idle_counts.items() if n >= 2)
    return out


def score_related(rec: NetRecord, *, action_start: float, background: set) -> tuple:
    s = 0.0
    reasons = []
    rt = rec.resource_type
    if rt in ("fetch", "xhr"):
        s += 40
    elif rt == "document":
        main_nav = rec.is_navigation and (rec.frame_id or "").endswith(".f0")
        s += 75 if main_nav else (45 if rec.is_navigation else 25)
        if main_nav:
            reasons.append("navigation")
    elif rt in ("eventsource", "websocket"):
        s += 15
    elif rt == "script":
        s += 2
    else:
        s -= 15
    if rec.method not in ("GET", "HEAD", "OPTIONS"):
        s += 25
        reasons.append(f"method:{rec.method}")
    if rec.method == "OPTIONS":
        s -= 30
    if rec.category in NOISE_CATEGORIES:
        s -= 60
        reasons.append(f"noise:{rec.category}")
    elif rec.category == "static":
        s -= 20
    if pattern_key(rec) in background or rec.extra.get("polling"):
        s -= 50
        reasons.append("background")
    init = rec.initiator or {}
    if init.get("async_parent") == "setInterval":
        s -= 25
    elif init.get("function") and re.match(r"^on[a-z]+$", str(init.get("function"))):
        s += 8
        reasons.append(f"initiator:{init.get('function')}")
    dt = max(0.0, rec.t_start - action_start)
    s += 20 * math.exp(-dt / 1500.0)
    if rec.is_json:
        s += 5
    return round(s, 1), reasons


def correlate(records: Iterable[NetRecord], *, action_id: str, action_start: float,
              history: Iterable[NetRecord] = ()) -> dict:
    """Rank the requests of one action: primary target, related, background."""
    window = [r for r in records if r.action_id == action_id]
    background = background_patterns(list(history) + window)
    scored = []
    for rec in window:
        s, reasons = score_related(rec, action_start=action_start, background=background)
        scored.append((s, rec, reasons))
    scored.sort(key=lambda x: (-x[0], x[1].t_start))
    primary = scored[0][1] if scored and scored[0][0] >= 20 else None
    return {
        "primary": primary,
        "related": [r for s, r, _ in scored if s >= 10],
        "background": [r for s, r, why in scored if "background" in why or r.category in NOISE_CATEGORIES],
        "ranking": [{"id": r.id, "score": s, "method": r.method, "url": truncate(r.url, 120), "why": why}
                    for s, r, why in scored[:8]],
    }


# ═══════════════════════════════════════════════════════════════════════
# Part 14 — advanced HAR / request validation
# ═══════════════════════════════════════════════════════════════════════
_TYPE_MARKERS = {"<number>": (int, float), "<string>": (str,), "<bool>": (bool,), "<array>": (list,),
                 "<object>": (dict,), "<null>": (type(None),)}


def _json_subset(expected: Any, actual: Any, path: str = "$") -> list:
    """Mismatch messages when ``expected`` is not a subset of ``actual``."""
    if isinstance(expected, str) and expected in _TYPE_MARKERS:
        ok = isinstance(actual, _TYPE_MARKERS[expected]) and not (expected == "<number>" and isinstance(actual, bool))
        return [] if ok else [f"{path}: expected {expected[1:-1]}, got {type(actual).__name__} {truncate(actual, 40)!r}"]
    if expected == "*":
        return [] if actual is not None else [f"{path}: missing"]
    if isinstance(expected, str) and expected.startswith("re:"):
        return [] if isinstance(actual, (str, int, float)) and re.search(expected[3:], str(actual)) else \
            [f"{path}: {truncate(actual, 40)!r} does not match /{expected[3:]}/"]
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        out = []
        for key, value in expected.items():
            if key not in actual:
                out.append(f"{path}.{key}: missing")
            else:
                out.extend(_json_subset(value, actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected array, got {type(actual).__name__}"]
        out = []
        for i, item in enumerate(expected):
            if i >= len(actual):
                out.append(f"{path}[{i}]: missing (array has {len(actual)} items)")
            else:
                out.extend(_json_subset(item, actual[i], f"{path}[{i}]"))
        return out
    if expected != actual:
        return [f"{path}: expected {expected!r}, got {truncate(actual, 60)!r}"]
    return []


def _get_path(obj: Any, dotted: str) -> tuple:
    cur = obj
    for part in re.split(r"\.(?![^\[]*\])", dotted):
        m = re.match(r"^([^\[]*)((?:\[\d+\])*)$", part)
        if not m:
            return False, None
        name, idxs = m.groups()
        if name:
            if not isinstance(cur, dict) or name not in cur:
                return False, None
            cur = cur[name]
        for idx in re.findall(r"\[(\d+)\]", idxs or ""):
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return False, None
            cur = cur[int(idx)]
    return True, cur


def _status_ok(want: Any, status: Optional[int]) -> bool:
    if want is None:
        return True
    if status is None:
        return False
    if isinstance(want, (list, tuple, set)):
        return any(_status_ok(w, status) for w in want)
    text = str(want).lower()
    if re.fullmatch(r"[1-5]xx", text):
        return str(status)[0] == text[0]
    return int(want) == status


def url_matches(pattern: str, url: str) -> bool:
    if not pattern:
        return True
    if pattern.startswith("re:"):
        return re.search(pattern[3:], url) is not None
    if "*" in pattern:
        rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/?#]*")
        return re.search(rx, url) is not None
    return pattern in url


@dataclass
class Expectation:
    """Declarative expectation on captured traffic (JSON/YAML friendly)."""

    url: str = ""
    method: Optional[str] = None
    status: Any = None
    request_json: Any = None
    request_has: list = field(default_factory=list)
    request_form: Optional[dict] = None
    request_headers: Optional[dict] = None
    response_json: Any = None
    response_has: list = field(default_factory=list)
    response_contains: Optional[str] = None
    response_headers: Optional[dict] = None
    min_count: int = 1
    max_count: Optional[int] = None
    require_body: bool = True
    require_timings: bool = False
    body_complete: bool = False
    response_sha256: Optional[str] = None
    response_size: Optional[int] = None
    request_files: Optional[dict] = None
    name: str = ""

    @classmethod
    def from_any(cls, value: Any) -> "Expectation":
        if isinstance(value, Expectation):
            return value
        if isinstance(value, str):
            method, _, url = value.partition(" ")
            if url and method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                return cls(url=url.strip(), method=method.upper())
            return cls(url=value)
        known = cls.__dataclass_fields__  # type: ignore[attr-defined]
        data = {("request_json" if k == "payload" else k): v for k, v in dict(value).items()}
        unknown = [k for k in data if k not in known]
        if unknown:
            raise ValueError(f"unknown expectation keys {unknown}; valid: {sorted(known)}")
        return cls(**data)

    def label(self) -> str:
        return self.name or f"{self.method or '*'} {self.url or '*'}"


def _check_candidate(exp: Expectation, rec: NetRecord) -> list:
    problems = []
    if exp.method and rec.method.upper() != exp.method.upper():
        problems.append(f"method: expected {exp.method.upper()}, got {rec.method}")
    if rec.failure:
        problems.append(f"request failed: {rec.failure}")
    if not _status_ok(exp.status, rec.status):
        problems.append(f"status: expected {exp.status}, got {rec.status}")
    req_obj = rec.request_json()
    if exp.request_json is not None:
        if rec.request_body is None:
            problems.append("request payload: missing (no request body captured)")
        elif req_obj is None:
            problems.append("request payload: not valid JSON")
        else:
            problems.extend("request payload " + m for m in _json_subset(exp.request_json, req_obj))
    for key in exp.request_has or []:
        source = req_obj if req_obj is not None else dict(rec.request_form() or [])
        if source is None or not _get_path(source, key)[0]:
            problems.append(f"request payload: required field '{key}' missing")
    if exp.request_form:
        form = dict(rec.request_form() or [])
        for k, v in exp.request_form.items():
            if k not in form:
                problems.append(f"request form: field '{k}' missing")
            elif v != "*" and str(form[k]) != str(v):
                problems.append(f"request form: {k}={form[k]!r}, expected {v!r}")
    for name, want in (exp.request_headers or {}).items():
        have = header_value(rec.request_headers, name)
        if not have:
            problems.append(f"request header '{name}' missing")
        elif want not in ("*", None) and not re.search(str(want), have):
            problems.append(f"request header '{name}'={truncate(have, 40)!r} does not match {want!r}")
    for name, want in (exp.response_headers or {}).items():
        have = header_value(rec.response_headers, name)
        if not have:
            problems.append(f"response header '{name}' missing")
        elif want not in ("*", None) and not re.search(str(want), have):
            problems.append(f"response header '{name}'={truncate(have, 40)!r} does not match {want!r}")
    needs_body = exp.require_body and (exp.response_json is not None or exp.response_has or exp.response_contains)
    if needs_body and not rec.response_body:
        problems.append(f"response body: missing ({rec.body_note or 'not captured'})")
    else:
        res_obj = rec.json() if (exp.response_json is not None or exp.response_has) else None
        if exp.response_json is not None:
            if res_obj is None:
                problems.append("response body: not valid JSON")
            else:
                problems.extend("response " + m for m in _json_subset(exp.response_json, res_obj))
        for key in exp.response_has or []:
            if res_obj is None or not _get_path(res_obj, key)[0]:
                problems.append(f"response: required field '{key}' missing")
        if exp.response_contains and exp.response_contains not in (rec.text() or ""):
            problems.append(f"response body does not contain {exp.response_contains!r}")
    if exp.body_complete:
        problems.extend("body integrity: " + p for p in body_integrity(rec)["problems"])
    if exp.response_sha256:
        if rec.response_body is None:
            problems.append(f"response body: missing, cannot verify sha256 ({rec.body_note or rec.failure or 'not captured'})")
        elif sha256_hex(rec.response_body) != str(exp.response_sha256).lower():
            problems.append(f"response sha256 {sha256_hex(rec.response_body)[:16]}… differs from expected "
                            f"{str(exp.response_sha256)[:16]}… ({len(rec.response_body)} bytes)")
    if exp.response_size is not None and (rec.response_body is None or len(rec.response_body) != int(exp.response_size)):
        problems.append(f"response size: expected {exp.response_size} bytes, got "
                        f"{len(rec.response_body) if rec.response_body is not None else 'no body'}")
    if exp.request_files:
        files = {f["name"]: f for f in rec.request_files()}
        for name, want in exp.request_files.items():
            got = files.get(name)
            if got is None:
                problems.append(f"request file '{name}' missing (multipart parts: {sorted(files) or 'none'})")
                continue
            for key, value in (want if isinstance(want, dict) else {"sha256": want}).items():
                if str(got.get(key)) != str(value):
                    problems.append(f"request file '{name}': {key}={truncate(got.get(key), 20)!r}, expected {truncate(value, 20)!r}")
    if exp.require_timings:
        t = rec.timings or {}
        if rec.duration_ms is None or rec.duration_ms <= 0:
            problems.append("timing: request duration missing/zero")
        raw_ok = isinstance(t.get("responseStart"), (int, float)) and t["responseStart"] >= 0
        har_ok = isinstance(t.get("wait"), (int, float)) and t["wait"] > 0
        if not (raw_ok or har_ok):
            problems.append("timing: server wait/responseStart missing (incomplete timings)")
    return problems


def validate_expectations(records: Iterable[NetRecord], expectations: Iterable[Any]) -> dict:
    """Validate expectations; every failure carries a clear, specific reason."""
    recs = list(records)
    results = []
    for raw in expectations:
        exp = Expectation.from_any(raw)
        cands = [r for r in recs if url_matches(exp.url, r.url)]
        if exp.method:
            by_method = [r for r in cands if r.method.upper() == exp.method.upper()]
        else:
            by_method = cands
        passing = []
        best = None
        for rec in by_method or cands:
            problems = _check_candidate(exp, rec)
            if not problems:
                passing.append(rec)
            elif best is None or len(problems) < len(best[1]):
                best = (rec, problems)
        failures = []
        if not cands:
            closest = sorted(recs, key=lambda r: -SequenceMatcher(None, exp.url, r.url).ratio())[:3]
            failures.append(f"no request matched url {exp.url!r}"
                            + (f" (closest: {', '.join(f'{r.method} {truncate(r.url, 70)}' for r in closest)})" if closest else ""))
        elif len(passing) < exp.min_count:
            if best is not None:
                failures.extend(f"{best[0].method} {truncate(best[0].url, 80)} ({best[0].id}): {p}" for p in best[1])
            if passing:
                failures.append(f"count: {len(passing)} matching request(s), expected at least {exp.min_count}")
        if exp.max_count is not None and len(passing) > exp.max_count:
            failures.append(f"count: {len(passing)} matching requests, expected at most {exp.max_count}")
        res = {"expectation": exp.label(), "ok": not failures, "matched": [r.id for r in passing], "failures": failures}
        if failures and best is not None:
            res["request_id"] = best[0].id  # the closest (offending) request
        results.append(res)
    return {"ok": all(r["ok"] for r in results), "checks": results,
            "passed": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}


# ═══════════════════════════════════════════════════════════════════════
# Part 24 — API & endpoint discovery (with JSON schema inference)
# ═══════════════════════════════════════════════════════════════════════
def infer_schema(value: Any, depth: int = 0) -> dict:
    if depth > 6:
        return {"type": "any"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        items: Optional[dict] = None
        for item in value[:20]:
            s = infer_schema(item, depth + 1)
            items = s if items is None else merge_schemas(items, s)
        return {"type": "array", "items": items or {}}
    if isinstance(value, dict):
        props = {str(k): infer_schema(v, depth + 1) for k, v in list(value.items())[:80]}
        return {"type": "object", "properties": props, "required": sorted(props)}
    return {"type": "any"}


def _types(schema: dict) -> set:
    t = schema.get("type")
    return set(t) if isinstance(t, list) else ({t} if t else set())


def merge_schemas(a: Optional[dict], b: Optional[dict]) -> dict:
    if not a:
        return dict(b or {})
    if not b:
        return dict(a)
    ta, tb = _types(a), _types(b)
    if ta == {"integer"} and tb == {"number"} or ta == {"number"} and tb == {"integer"}:
        return {"type": "number"}
    if ta == tb == {"object"}:
        props = dict(a.get("properties", {}))
        for k, v in b.get("properties", {}).items():
            props[k] = merge_schemas(props.get(k), v) if k in props else v
        req = sorted(set(a.get("required", [])) & set(b.get("required", [])))
        return {"type": "object", "properties": props, "required": req}
    if ta == tb == {"array"}:
        return {"type": "array", "items": merge_schemas(a.get("items"), b.get("items"))}
    if ta == tb:
        return dict(a)
    union = sorted((ta | tb) - {"any"})
    if "null" in union and len(union) == 2:
        base = a if "null" not in ta else b
        merged = dict(base)
        merged["nullable"] = True
        return merged
    return {"type": union}


def flatten_schema(schema: Optional[dict], prefix: str = "") -> dict:
    out: dict = {}
    if not schema:
        return out
    types = _types(schema)
    if "object" in types and "properties" in schema:
        for k, v in schema["properties"].items():
            out.update(flatten_schema(v, f"{prefix}.{k}" if prefix else k))
        if not schema["properties"]:
            out[prefix or "$"] = "object"
    elif "array" in types:
        out[(prefix or "$") + "[]"] = "array"
        out.update(flatten_schema(schema.get("items"), (prefix or "") + "[]"))
    else:
        t = schema.get("type")
        out[prefix or "$"] = "|".join(t) if isinstance(t, list) else str(t)
        if schema.get("nullable"):
            out[prefix or "$"] += "|null"
    return out


_PAGE_PARAMS = {"page", "p", "pg", "pagenum", "page_number", "pagenumber", "offset", "start", "skip", "limit",
                "per_page", "perpage", "size", "pagesize", "page_size", "cursor", "after", "before", "from",
                "max_id", "since_id", "continuation", "next", "token", "pagetoken", "page_token"}
_PAGE_FIELDS = {"next", "next_cursor", "nextcursor", "nextpagetoken", "next_page_token", "has_more", "hasmore",
                "hasnextpage", "has_next_page", "total", "total_pages", "totalpages", "pages", "count", "next_page",
                "nextpage", "cursor", "links", "pagination", "page", "per_page"}


def _value_type(value: str) -> str:
    if re.fullmatch(r"-?\d+", value):
        return "integer"
    if re.fullmatch(r"-?\d+\.\d+", value):
        return "number"
    if value.lower() in ("true", "false"):
        return "boolean"
    return "string"


def _graphql_info(rec: NetRecord) -> Optional[dict]:
    body = rec.request_json()
    if isinstance(body, dict) and ("query" in body or "operationName" in body or "extensions" in body):
        q = body.get("query") or ""
        op = body.get("operationName")
        m = re.search(r"\b(query|mutation|subscription)\s+(\w+)", q or "")
        kind = m.group(1) if m else ("query" if q.strip().startswith("{") else None)
        return {"operation": op or (m.group(2) if m else None), "type": kind,
                "persisted": bool(((body.get("extensions") or {}).get("persistedQuery"))),
                "variables": body.get("variables")}
    if rec.method == "GET" and "graphql" in rec.path.lower():
        q = dict(rec.query)
        if "query" in q or "operationName" in q:
            m = re.search(r"\b(query|mutation)\s+(\w+)", q.get("query", ""))
            return {"operation": q.get("operationName") or (m.group(2) if m else None), "type": "query",
                    "persisted": "extensions" in q, "variables": q.get("variables")}
    return None


def discover_endpoints(records: Iterable[NetRecord], *, include_noise: bool = False,
                       first_party: Iterable[str] = ()) -> list:
    """Group API traffic into endpoints with params, payload/response schemas,
    auth hints, pagination hints and examples."""
    recs = classify_records(list(records), first_party=first_party)
    groups: dict = {}
    for rec in recs:
        if rec.category in NOISE_CATEGORIES and not include_noise:
            continue
        ct = rec.content_type.lower()
        is_data_doc = rec.category == "document" and ("json" in ct or "xml" in ct)
        is_form_post = rec.category == "document" and rec.method not in ("GET", "HEAD")
        if rec.category not in ("api", "other") and not is_data_doc and not is_form_post:
            continue
        if rec.category == "other" and not (rec.is_json or rec.method not in ("GET", "HEAD")):
            continue
        gql = _graphql_info(rec)
        template = path_template(rec.path)
        key = f"{rec.method} {rec.host}{template}"
        if gql and gql.get("operation"):
            key += f"#{gql['operation']}"
        g = groups.setdefault(key, {"key": key, "method": rec.method, "host": rec.host, "path": template,
                                    "scheme": rec.url.split(":", 1)[0],
                                    "kind": "graphql" if gql else ("form" if is_form_post else "rest"),
                                    "graphql": None, "count": 0, "statuses": {}, "query_params": {},
                                    "path_params": {}, "request_content_types": set(),
                                    "response_content_types": set(), "request_schema": None, "response_schema": None,
                                    "form_fields": set(), "auth": set(), "headers": set(), "examples": [],
                                    "rate_limit_headers": set(), "sse": False, "records": []})
        g["count"] += 1
        g["records"].append(rec.id)
        g["statuses"][str(rec.status)] = g["statuses"].get(str(rec.status), 0) + 1
        if gql:
            g["graphql"] = {k: v for k, v in gql.items() if k != "variables"}
            if gql.get("variables") is not None:
                g["request_schema"] = merge_schemas(g["request_schema"], infer_schema({"variables": gql["variables"]}))
        for name, value in rec.query:
            qp = g["query_params"].setdefault(name, {"examples": [], "types": set(), "seen": 0})
            qp["seen"] += 1
            qp["types"].add(_value_type(value))
            if value not in qp["examples"] and len(qp["examples"]) < 5:
                qp["examples"].append(value)
        segs, tsegs = rec.path.split("/"), template.split("/")
        for seg, tseg in zip(segs, tsegs):
            if tseg.startswith("{") or "{" in tseg:
                pp = g["path_params"].setdefault(tseg, [])
                if seg not in pp and len(pp) < 5:
                    pp.append(seg)
        rct = rec.request_content_type.split(";")[0].strip()
        if rct:
            g["request_content_types"].add(rct)
        body = rec.request_json()
        if body is not None and not gql:
            g["request_schema"] = merge_schemas(g["request_schema"], infer_schema(body))
        for k, _v in rec.request_form():
            g["form_fields"].add(k)
        resp_ct = rec.content_type.split(";")[0].strip()
        if resp_ct:
            g["response_content_types"].add(resp_ct)
        if "event-stream" in resp_ct or rec.stream:
            g["sse"] = True
        data = rec.json()
        if data is not None:
            g["response_schema"] = merge_schemas(g["response_schema"], infer_schema(data))
        for h in rec.request_headers:
            lname = str(h.get("name", "")).lower()
            if lname == "authorization":
                g["auth"].add("bearer" if str(h.get("value", "")).lower().startswith("bearer") else "authorization")
            elif lname == "cookie":
                g["auth"].add("cookie")
            elif re.search(r"api[-_]?key|token|auth", lname):
                g["auth"].add(f"header:{lname}")
            elif lname.startswith("x-") and lname not in ("x-requested-with",):
                g["headers"].add(lname)
        for h in rec.response_headers:
            lname = str(h.get("name", "")).lower()
            if "ratelimit" in lname or lname == "retry-after":
                g["rate_limit_headers"].add(lname)
        if len(g["examples"]) < 2:
            ex = {"url": rec.url, "status": rec.status}
            if rec.request_body is not None:
                ex["request_body"] = truncate(rec.request_text(), 500)
            if data is not None:
                ex["response_preview"] = truncate(json.dumps(data, ensure_ascii=False), 600)
                ex["response_shape"] = describe_payload(data)
            g["examples"].append(ex)
    out = []
    for g in groups.values():
        qparams = {k: {"examples": v["examples"], "type": sorted(v["types"])[0] if len(v["types"]) == 1 else "string",
                       "required": v["seen"] == g["count"]} for k, v in g["query_params"].items()}
        page_params = [k for k in qparams if k.lower() in _PAGE_PARAMS]
        resp_fields = set()
        for path in flatten_schema(g["response_schema"]):
            resp_fields.add(path.split(".")[-1].split("[")[0].lower())
        page_fields = sorted(f for f in resp_fields if f in _PAGE_FIELDS)
        auth = sorted(g["auth"])
        out.append({
            "key": g["key"], "method": g["method"], "url_template": f"{g['scheme']}://{g['host']}{g['path']}",
            "host": g["host"], "path": g["path"], "kind": "sse" if g["sse"] else g["kind"], "graphql": g["graphql"],
            "count": g["count"], "statuses": g["statuses"], "query_params": qparams, "path_params": g["path_params"],
            "request_content_types": sorted(g["request_content_types"]), "form_fields": sorted(g["form_fields"]),
            "request_schema": g["request_schema"], "response_content_types": sorted(g["response_content_types"]),
            "response_schema": g["response_schema"], "auth": auth, "custom_headers": sorted(g["headers"]),
            "rate_limit_headers": sorted(g["rate_limit_headers"]),
            "pagination": {"params": page_params, "response_fields": page_fields} if page_params or page_fields else None,
            "examples": g["examples"], "record_ids": g["records"][:20],
        })
    out.sort(key=lambda e: (e["host"], e["path"], e["method"]))
    return out


def endpoints_markdown(endpoints: list, *, title: str = "Discovered API endpoints") -> str:
    lines = [f"# {title}", "", f"{len(endpoints)} endpoint(s).", ""]
    for ep in endpoints:
        gql = f" — GraphQL `{ep['graphql'].get('type') or ''} {ep['graphql'].get('operation') or ''}`" if ep.get("graphql") else ""
        lines.append(f"## `{ep['method']} {ep['url_template']}`{gql}")
        lines.append(f"- kind: **{ep['kind']}**, seen {ep['count']}×, statuses {ep['statuses']}")
        if ep["query_params"]:
            lines.append("- query params: " + ", ".join(f"`{k}` ({v['type']}{', required' if v['required'] else ''}; e.g. {v['examples'][:2]})"
                                                       for k, v in ep["query_params"].items()))
        if ep["path_params"]:
            lines.append("- path params: " + ", ".join(f"`{k}` e.g. {v[:3]}" for k, v in ep["path_params"].items()))
        if ep["request_schema"]:
            lines.append("- request fields: " + ", ".join(f"`{k}`:{t}" for k, t in list(flatten_schema(ep["request_schema"]).items())[:20]))
        if ep["form_fields"]:
            lines.append("- form fields: " + ", ".join(f"`{k}`" for k in ep["form_fields"]))
        if ep["response_schema"]:
            lines.append("- response fields: " + ", ".join(f"`{k}`:{t}" for k, t in list(flatten_schema(ep["response_schema"]).items())[:25]))
        if ep["auth"]:
            lines.append(f"- auth: {', '.join(ep['auth'])}")
        if ep["pagination"]:
            lines.append(f"- pagination: params {ep['pagination']['params']}, response fields {ep['pagination']['response_fields']}")
        if ep["examples"]:
            lines.append(f"- example: `{truncate(ep['examples'][0]['url'], 150)}`")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Part 30 — regression & change detection
# ═══════════════════════════════════════════════════════════════════════
def endpoint_signature(ep: dict) -> dict:
    return {"key": ep["key"], "method": ep["method"], "path": ep["host"] + ep["path"],
            "query_params": sorted(ep.get("query_params") or {}),
            "request": flatten_schema(ep.get("request_schema")),
            "response": flatten_schema(ep.get("response_schema")),
            "statuses": sorted((ep.get("statuses") or {}).keys())}


def compare_endpoints(old: Iterable[dict], new: Iterable[dict]) -> dict:
    """Diff two discovery results → added/removed/changed endpoints with reasons."""
    a = {e["key"]: endpoint_signature(e) for e in old}
    b = {e["key"]: endpoint_signature(e) for e in new}
    changes = []
    for key in sorted(set(a) - set(b)):
        path = a[key]["path"]
        alt = [k for k in b if b[k]["path"] == path and k not in a]
        if alt:
            changes.append({"endpoint": key, "change": "method_changed", "severity": "breaking",
                            "detail": f"{a[key]['method']} → {b[alt[0]]['method']} for {path}"})
        else:
            changes.append({"endpoint": key, "change": "removed", "severity": "breaking",
                            "detail": f"endpoint no longer called: {key}"})
    for key in sorted(set(b) - set(a)):
        path = b[key]["path"]
        if any(a[k]["path"] == path for k in a if k not in b):
            continue
        changes.append({"endpoint": key, "change": "added", "severity": "info", "detail": f"new endpoint: {key}"})
    for key in sorted(set(a) & set(b)):
        x, y = a[key], b[key]
        qa, qb = set(x["query_params"]), set(y["query_params"])
        for p in sorted(qb - qa):
            changes.append({"endpoint": key, "change": "query_param_added", "severity": "info", "detail": f"query param '{p}' added"})
        for p in sorted(qa - qb):
            changes.append({"endpoint": key, "change": "query_param_removed", "severity": "warning", "detail": f"query param '{p}' removed"})
        for side, label in (("request", "request field"), ("response", "response field")):
            fa, fb = x[side], y[side]
            for f in sorted(set(fa) - set(fb)):
                changes.append({"endpoint": key, "change": f"{side}_field_removed", "severity": "breaking",
                                "detail": f"{label} '{f}' ({fa[f]}) removed"})
            for f in sorted(set(fb) - set(fa)):
                changes.append({"endpoint": key, "change": f"{side}_field_added", "severity": "info",
                                "detail": f"{label} '{f}' ({fb[f]}) added"})
            for f in sorted(set(fa) & set(fb)):
                if fa[f] != fb[f] and not ({fa[f], fb[f]} <= {"integer", "number"}):
                    changes.append({"endpoint": key, "change": f"{side}_type_changed", "severity": "breaking",
                                    "detail": f"{label} '{f}' type {fa[f]} → {fb[f]}"})
        if x["statuses"] != y["statuses"]:
            changes.append({"endpoint": key, "change": "status_changed", "severity": "warning",
                            "detail": f"statuses {x['statuses']} → {y['statuses']}"})
    breaking = [c for c in changes if c["severity"] == "breaking"]
    return {"changed": bool(changes), "breaking": len(breaking), "changes": changes,
            "endpoints_before": len(a), "endpoints_after": len(b)}


def regression_markdown(diff: dict) -> str:
    lines = ["# Network regression report", "",
             f"endpoints: {diff['endpoints_before']} → {diff['endpoints_after']}, changes: {len(diff['changes'])}, "
             f"breaking: {diff['breaking']}", ""]
    for c in diff["changes"]:
        lines.append(f"- **{c['severity']}** `{c['endpoint']}` — {c['change']}: {c['detail']}")
    if not diff["changes"]:
        lines.append("No changes detected.")
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════
# Part 49 — deterministic run fingerprint
# ═══════════════════════════════════════════════════════════════════════
def run_fingerprint(actions: Iterable[dict], records: Iterable[NetRecord]) -> dict:
    steps = [{"kind": a.get("action"), "target": a.get("target"), "ok": a.get("ok"),
              "validation": (a.get("validation") or {}).get("ok")} for a in actions]
    recs = classify_records(list(records))
    net = set()
    for r in recs:
        if r.category not in ("api", "document"):
            continue
        data = r.json()
        schema = sha256_hex(dump_json(flatten_schema(infer_schema(data)), indent=None))[:12] if data is not None else "-"
        net.add((r.method, site_of(r.host), path_template(r.path), r.status, schema))
    network = sorted(" ".join(str(x) for x in item) for item in net)
    digest = sha256_hex(dump_json({"steps": steps, "network": network}, indent=None))
    return {"hash": digest, "steps": steps, "network": network}


def diff_fingerprints(a: dict, b: dict) -> dict:
    out = {"same": a.get("hash") == b.get("hash"), "step_changes": [], "network_added": [], "network_removed": []}
    for i, (x, y) in enumerate(zip(a.get("steps", []), b.get("steps", []))):
        if x != y:
            out["step_changes"].append({"index": i, "before": x, "after": y})
    if len(a.get("steps", [])) != len(b.get("steps", [])):
        out["step_changes"].append({"index": "count", "before": len(a.get("steps", [])), "after": len(b.get("steps", []))})
    na, nb = set(a.get("network", [])), set(b.get("network", []))
    out["network_added"] = sorted(nb - na)
    out["network_removed"] = sorted(na - nb)
    return out


def query_dict(url: str) -> dict:
    try:
        return dict(parse_qsl(url.split("?", 1)[1], keep_blank_values=True)) if "?" in url else {}
    except ValueError:
        return {}



# ════════════════════════════════════════════════════════════════════════════
# crawl.py — verified capture, crawl, item detection, pagination (Parts 4, 5, 23)
# ════════════════════════════════════════════════════════════════════════════
# Verified capture (Part 4), multi-page crawl (Part 5), pagination &
# dynamic content (Part 23) and automatic list/item detection for extraction.


# ═══════════════════════════════════════════════════════════════════════
# Part 4 — capture verification
# ═══════════════════════════════════════════════════════════════════════
def verify_capture(records: Iterable[NetRecord], *, expect: Iterable[str] = (), min_entries: int = 1,
                   require_bodies: bool = True) -> dict:
    """Is a capture complete? (non-empty, expected requests present & finished,
    JSON/API responses have bodies, nothing relevant still in flight)."""
    recs = list(records)
    problems = []
    if len(recs) < min_entries:
        problems.append(f"empty/premature capture: {len(recs)} entries < {min_entries}")
    for needle in expect:
        hits = [r for r in recs if (re.search(needle[3:], r.url) if needle.startswith("re:") else needle in r.url)]
        if not hits:
            problems.append(f"expected request missing: {needle}")
            continue
        done = [r for r in hits if r.finished and not r.failure]
        if not done:
            problems.append(f"expected request {needle} never finished (premature capture)")
            continue
        last = done[-1]
        if last.status is None or not (200 <= last.status < 400):
            problems.append(f"expected request {needle} has status {last.status}")
        if require_bodies and not last.response_body and not (300 <= (last.status or 0) < 400):
            problems.append(f"expected request {needle} has an empty/missing body ({last.body_note or 'no body'})")
    pending = [r for r in recs if not r.finished and r.category not in NOISE_CATEGORIES
               and not r.extra.get("streaming") and not r.extra.get("polling")]
    if pending:
        problems.append(f"{len(pending)} request(s) still in flight: {[truncate(r.url, 80) for r in pending[:3]]}")
    api_empty = [r for r in recs if r.category == "api" and r.finished and not r.failure and r.status == 200
                 and r.response_body is not None and len(r.response_body) == 0 and r.method != "HEAD"]
    if require_bodies and api_empty:
        problems.append(f"{len(api_empty)} API response(s) with empty body")
    return {"ok": not problems, "entries": len(recs), "problems": problems}


def capture_url(session: Any, url: str, *, har_path: Any = None, quiet_ms: float = 1000,
                max_wait_ms: float = 30_000, expect: Iterable[str] = (), min_entries: int = 1,
                max_attempts: int = 3) -> dict:
    """Navigate + wait until the capture is provably complete; retry if not.

    Returns the HAR path immediately (one-shot use by agents / MCP)."""
    expect = tuple(expect or ())
    attempts = []
    result: dict = {}
    for attempt in range(1, max_attempts + 1):
        res = session.goto(url, wait={"quiet_ms": quiet_ms, "max_wait_ms": max_wait_ms, "expect": expect},
                           retry=1)
        recs = session.recorder.for_action(res["id"])
        ver = verify_capture(recs, expect=expect, min_entries=min_entries)
        attempts.append({"attempt": attempt, "action_id": res["id"], "entries": ver["entries"], "ok": ver["ok"],
                         "problems": ver["problems"], "waited_ms": (res.get("wait") or {}).get("waited_ms")})
        result = {"action": res, "records": recs, "verification": ver}
        if ver["ok"]:
            break
    recs = result["records"]
    path = Path(har_path) if har_path else Path(session.out_dir) / "captures" / f"{slugify(url, 70, 'page')}.har"
    save_har(path, session._har_for(recs))
    act = result["action"]
    return {"ok": result["verification"]["ok"], "url": url, "final_url": act.get("url"), "title": act.get("title"),
            "status": act.get("status"), "har_path": str(path), "entries": len(recs), "attempts": len(attempts),
            "attempt_log": attempts, "problems": result["verification"]["problems"],
            "waited_ms": (act.get("wait") or {}).get("waited_ms")}


# ═══════════════════════════════════════════════════════════════════════
# Part 5 — multi-page crawl
# ═══════════════════════════════════════════════════════════════════════
def har_name_for(url: str, index: int) -> str:
    parts = urlsplit(url)
    path = parts.path.strip("/") or "root"
    if parts.query:
        path = f"{path}_{parts.query}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{parts.netloc.replace(':', '-')}_{path}")
    slug = re.sub(r"_+", "_", slug).strip("_.")[:90] or "page"
    return f"{index:03d}_{slug}.har"


def plan_har_names(urls: Iterable[str]) -> list:
    names, used = [], set()
    for i, url in enumerate(urls, start=1):
        base = har_name_for(url, i)[:-4]
        name, n = f"{base}.har", 2
        while name.lower() in used:
            name = f"{base}-{n}.har"
            n += 1
        used.add(name.lower())
        names.append(name)
    return names


def crawl(session: Any, urls: Iterable[str], *, out_dir: Any = None, expect: Iterable[str] = (),
          min_entries: int = 1, quiet_ms: float = 800, max_attempts: int = 3, extract: Optional[dict] = None,
          item: Optional[str] = None, continue_on_error: bool = True,
          on_page: Optional[Callable] = None) -> dict:
    """Visit every URL (none skipped), one verified HAR per page, summary with counts."""
    url_list = list(urls)
    out = Path(out_dir) if out_dir else Path(session.out_dir) / "crawl"
    out.mkdir(parents=True, exist_ok=True)
    names = plan_har_names(url_list)
    pages = []
    t0 = mono_ms()
    log = get_logger("crawl")
    for index, (url, name) in enumerate(zip(url_list, names), start=1):
        started = mono_ms()
        entry = {"index": index, "url": url, "har_name": name, "har_path": None, "ok": False}
        try:
            cap = capture_url(session, url, har_path=out / name, quiet_ms=quiet_ms, expect=expect,
                              min_entries=min_entries, max_attempts=max_attempts)
            entry.update(ok=cap["ok"], har_path=cap["har_path"], entries=cap["entries"], status=cap["status"],
                         title=cap["title"], attempts=cap["attempts"], problems=cap["problems"])
            if cap["ok"] and (extract or item):
                entry["data"] = session.extract(extract or {}, item=item)
            if on_page is not None:
                on_page(session, entry)
        except Exception as exc:  # noqa: BLE001 - a failing page is reported, never skipped
            entry.update(ok=False, error=f"{type(exc).__name__}: {truncate(str(exc), 300)}")
            log.warning("crawl page %d failed: %s", index, exc)
            if not continue_on_error:
                pages.append(entry)
                break
        entry["duration_ms"] = round(mono_ms() - started)
        pages.append(entry)
    summary = {"total": len(url_list), "attempted": len(pages), "visited": sum(1 for p in pages if p["ok"]),
               "failed": sum(1 for p in pages if not p["ok"]), "skipped": len(url_list) - len(pages),
               "duration_ms": round(mono_ms() - t0), "out_dir": str(out), "pages": pages}
    summary["summary_path"] = str(write_json(out / "crawl_summary.json", summary))
    return summary


# ═══════════════════════════════════════════════════════════════════════
# automatic list detection (repeated structures) — used by recon & paginate
# ═══════════════════════════════════════════════════════════════════════
DETECT_ITEMS_JS = r"""
(opts) => {
  const txt = (e) => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const sig = (e) => e.tagName.toLowerCase() + Array.from(e.classList).filter(c => !/^(active|selected|odd|even|first|last|col-|clearfix)/.test(c)).slice(0, 2).map(c => '.' + CSS.escape(c)).join('');
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const PRICE = /(?:[$£€¥₹৳]|US\$|Tk\.?|BDT|USD|EUR|GBP)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|BDT|৳|€|\$)/;
  const cands = [];
  for (const parent of document.querySelectorAll('body *')) {
    const kids = Array.from(parent.children); if (kids.length < 3) continue;
    const groups = {};
    for (const k of kids) { if (!vis(k)) continue; const s = sig(k); (groups[s] = groups[s] || []).push(k); }
    for (const [s, els] of Object.entries(groups)) {
      if (els.length < 3 || /^(option|br|hr|script|style|meta|link|td|th|tr|col|source|path|g)\b/.test(s)) continue;
      const texts = els.map(txt).filter(t => t.length > 1);
      if (texts.length < els.length * 0.7) continue;
      const avg = texts.reduce((a, t) => a + t.length, 0) / texts.length;
      if (avg < 8) continue;
      const links = els.filter(e => e.matches('a[href]') || e.querySelector('a[href]')).length;
      const imgs = els.filter(e => e.querySelector('img')).length;
      const prices = els.filter(e => PRICE.test(txt(e))).length;
      const nav = parent.closest('nav,header,footer,[role=navigation],[role=menu],.pagination,.pager');
      const uniq = new Set(texts).size / texts.length;
      const score = els.length * Math.min(avg, 180) / 25 + links * 1.5 + imgs * 2 + prices * 4 + uniq * els.length * 2 - (nav ? els.length * 10 : 0);
      let selector = s;
      if (document.querySelectorAll(s).length !== els.length) {
        const ps = parent.id ? '#' + CSS.escape(parent.id) : sig(parent);
        selector = ps + ' > ' + s;
        if (document.querySelectorAll(selector).length !== els.length) selector = null;
      }
      if (!selector) continue;
      cands.push({ selector, count: els.length, score: Math.round(score), sample: texts[0].slice(0, 160), el: els[0] });
    }
  }
  cands.sort((a, b) => b.score - a.score);
  const rel = (root, el) => { if (el === root) return ''; const parts = []; let cur = el;
    while (cur && cur !== root && parts.length < 4) { let p = cur.tagName.toLowerCase(); const c = Array.from(cur.classList).filter(x => !/^(active|selected)$/.test(x))[0];
      if (c) p += '.' + CSS.escape(c); parts.unshift(p); cur = cur.parentElement; } return parts.join(' '); };
  const fieldsFor = (item) => {
    const f = {};
    const heading = item.querySelector('h1 a,h2 a,h3 a,h4 a,h1,h2,h3,h4,[class*=title] a,[class*=title],[class*=name]');
    if (heading) { const a = heading.closest('a') || heading.querySelector('a');
      if (a && a.getAttribute('title') && a.getAttribute('title').length > txt(a).length) f.title = rel(item, a) + '@title'; else f.title = rel(item, heading); }
    const link = item.matches('a[href]') ? item : item.querySelector('a[href]');
    if (link) f.url = (link === item ? '' : rel(item, link)) + '@href';
    if (!f.title && link && txt(link).length >= 3 && !PRICE.test(txt(link))) f.title = link === item ? '' : rel(item, link);
    const all = Array.from(item.querySelectorAll('*'));
    const price = all.find(e => e.children.length === 0 && PRICE.test(txt(e))) || all.find(e => /price/i.test(e.className) && PRICE.test(txt(e)));
    if (price) f.price = rel(item, price);
    const img = item.querySelector('img'); if (img) f.image = rel(item, img) + (img.getAttribute('src') ? '@src' : '@data-src');
    const rating = item.querySelector('[class*=rating],[class*=star]'); if (rating) f.rating = rel(item, rating) + '@class';
    const stock = item.querySelector('[class*=stock],[class*=availab]'); if (stock) f.availability = rel(item, stock);
    const date = item.querySelector('time'); if (date) f.date = rel(item, date) + (date.getAttribute('datetime') ? '@datetime' : '');
    const summary = item.querySelector('p:not([class*=price]):not([class*=stock])'); if (summary && !f.summary && txt(summary).length > 20) f.summary = rel(item, summary);
    return f;
  };
  const out = [];
  const seenSel = new Set();
  for (const c of cands) {
    if (seenSel.has(c.selector)) continue; seenSel.add(c.selector);
    out.push({ selector: c.selector, count: c.count, score: c.score, sample: c.sample, fields: fieldsFor(c.el) });
    if (out.length >= (opts && opts.top || 3)) break;
  }
  return out;
}
"""


def detect_items(session: Any, *, top: int = 3) -> list:
    """Candidate repeated-item lists (product cards, search results, posts …)."""
    session._ensure()
    return session.page.evaluate(DETECT_ITEMS_JS, {"top": top}) or []


def clean_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value).replace(" ", " "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def extract_items(session: Any, *, item: Optional[str] = None, fields: Optional[dict] = None,
                  limit: Optional[int] = None) -> dict:
    """Extract a list; auto-detects the item selector + fields when not given."""
    detected = None
    if not item:
        cands = detect_items(session)
        if not cands:
            return {"item": None, "fields": fields or {}, "items": [], "detected": False}
        detected = cands[0]
        item = detected["selector"]
        fields = fields or detected["fields"]
    rows = session.extract(fields or {}, item=item, limit=limit)
    return {"item": item, "fields": fields or {}, "items": rows, "count": len(rows), "detected": bool(detected),
            "alternatives": [] if not detected else None}


# ═══════════════════════════════════════════════════════════════════════
# Part 23 — pagination & dynamic content
# ═══════════════════════════════════════════════════════════════════════
PAGINATION_JS = r"""
() => {
  const txt = (e) => ((e && (e.innerText || e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = { next: null, load_more: null, numbered: null, url_param: null };
  const mark = (el, kind) => { el.setAttribute('data-at-pager', kind); return '[data-at-pager="' + kind + '"]'; };
  const relNext = document.querySelector('a[rel~=next]');
  const nextRx = /^(next|next page|next ›|next »|›|»|→|older posts?|older|more results|next results|পরবর্তী|siguiente|suivant|weiter)$/i;
  let next = relNext && vis(relNext) ? relNext : null;
  if (!next) for (const el of document.querySelectorAll('a[href],button,[role=button],[role=link]')) {
    if (!vis(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const label = (txt(el) || el.getAttribute('aria-label') || el.getAttribute('title') || '').replace(/[←]/g, '').trim();
    if (nextRx.test(label) || /^next\b/i.test(el.getAttribute('aria-label') || '') || /(^|\s)next(\s|$)/.test((el.closest('li') || {}).className || '')) { next = el; break; }
  }
  if (next) out.next = { selector: mark(next, 'next'), text: txt(next) || next.getAttribute('aria-label') || '', href: next.href || null };
  const moreRx = /(load|show|see|view)\s+more|more (products|results|items|deals|posts|reviews)|আরও/i;
  for (const el of document.querySelectorAll('button,a,[role=button]')) {
    if (!vis(el) || el.disabled) continue; const label = txt(el);
    if (label.length < 40 && moreRx.test(label)) { out.load_more = { selector: mark(el, 'more'), text: label }; break; }
  }
  const pag = document.querySelector('.pagination,.pager,nav[aria-label*=pagina i],[class*=pagination]');
  if (pag) { const nums = Array.from(pag.querySelectorAll('a,li,span')).map(e => parseInt(txt(e), 10)).filter(n => !isNaN(n));
    const cur = txt(pag).match(/page\s+(\d+)\s+of\s+(\d+)/i);
    out.numbered = { max: cur ? parseInt(cur[2], 10) : (nums.length ? Math.max.apply(null, nums) : null), current: cur ? parseInt(cur[1], 10) : null }; }
  const u = new URL(location.href);
  for (const k of ['page', 'p', 'pg', 'paged', 'offset', 'start']) if (u.searchParams.has(k)) { out.url_param = { name: k, value: u.searchParams.get(k) }; break; }
  if (!out.url_param) { const m = u.pathname.match(/(page[-/_]?)(\d+)/i); if (m) out.url_param = { path: m[0], value: m[2] }; }
  out.scroll_height = document.documentElement.scrollHeight; out.viewport = innerHeight;
  return out;
}
"""


def detect_pagination(session: Any) -> dict:
    """What kind of pagination does the current page use?"""
    session._ensure()
    info = session.page.evaluate(PAGINATION_JS) or {}
    kind = "none"
    if info.get("next"):
        kind = "next"
    elif info.get("load_more"):
        kind = "load_more"
    elif info.get("url_param"):
        kind = "url"
    elif (info.get("scroll_height") or 0) > (info.get("viewport") or 0) * 1.5:
        kind = "scroll?"
    info["type"] = kind
    return info


def _next_url(url: str, info: dict) -> Optional[str]:
    parts = urlsplit(url)
    up = info.get("url_param") or {}
    if up.get("name"):
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        try:
            q[up["name"]] = str(int(q.get(up["name"], "1")) + 1)
        except ValueError:
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
    if up.get("path"):
        m = re.search(r"(page[-/_]?)(\d+)", parts.path, re.I)
        if m:
            new_path = parts.path[:m.start(2)] + str(int(m.group(2)) + 1) + parts.path[m.end(2):]
            return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))
    return None


def paginate(session: Any, *, mode: str = "auto", max_pages: int = 50, item: Optional[str] = None,
             fields: Optional[dict] = None, key: Optional[str] = None, stop_after_no_new: int = 2,
             max_items: Optional[int] = None, on_page: Optional[Callable] = None) -> dict:
    """Walk through all pages / load-more / infinite scroll and collect items.

    ``mode``: ``auto`` | ``next`` | ``load_more`` | ``scroll`` | ``url``.
    Every page step is an action (own network capture); ``key`` names the
    field used to de-duplicate items (default: url/title/full row).
    """
    session._ensure()
    if not item:
        cands = detect_items(session)
        if cands:
            item = cands[0]["selector"]
            fields = fields or cands[0]["fields"]
    info = detect_pagination(session)
    if mode == "auto":
        mode = {"next": "next", "load_more": "load_more", "url": "url"}.get(info["type"], "scroll")
    seen: set = set()
    items: list = []
    pages: list = []
    no_new = 0
    stopped = "max_pages"

    def collect(step: int, action: Optional[dict]) -> int:
        rows = session.extract(fields or {}, item=item) if item else []
        new = 0
        for row in rows:
            k = str(row.get(key)) if key and isinstance(row, dict) else str(row.get("url") or row.get("title") or row) \
                if isinstance(row, dict) else str(row)
            if k in seen:
                continue
            seen.add(k)
            items.append(row)
            new += 1
        rec = {"step": step, "url": session.page.url, "items_on_page": len(rows), "new_items": new,
               "action_id": action.get("id") if action else None,
               "requests": (action or {}).get("network", {}).get("requests"),
               "primary": ((action or {}).get("network", {}).get("primary") or {}).get("url")}
        pages.append(rec)
        if on_page is not None:
            on_page(session, rec)
        return new

    collect(1, None)
    for step in range(2, max_pages + 1):
        if max_items and len(items) >= max_items:
            stopped = "max_items"
            break
        action = None
        if mode == "next":
            info = detect_pagination(session)
            if not info.get("next"):
                stopped = "no_next_link"
                break
            action = session.click(info["next"]["selector"])
        elif mode == "load_more":
            info = detect_pagination(session)
            if not info.get("load_more"):
                stopped = "no_load_more_button"
                break
            action = session.click(info["load_more"]["selector"])
        elif mode == "url":
            nxt = _next_url(session.page.url, info)
            if not nxt:
                stopped = "no_next_url"
                break
            action = session.goto(nxt)
            if (action.get("status") or 200) >= 400:
                stopped = f"http_{action.get('status')}"
                break
        else:
            action = session.scroll(to="bottom")
        new = collect(step, action)
        if new == 0:
            no_new += 1
            if no_new >= stop_after_no_new:
                stopped = "no_new_items"
                break
        else:
            no_new = 0
    return {"mode": mode, "item": item, "fields": fields, "pages": pages, "page_count": len(pages),
            "items": items, "total_items": len(items), "stopped_because": stopped}



# ════════════════════════════════════════════════════════════════════════════
# session.py — Session - the high-level, AI-friendly browser API (Parts 3, 6, 8, 15, 17, 18, 26, 28, 29, 43, 46)
# ════════════════════════════════════════════════════════════════════════════
# ``Session`` — the object an AI agent (or a generated scraper) drives.
#
# Every action (goto, click, fill, select, scroll, download …) runs through one
# pipeline::
#
#     guardrails → pre_action hooks → execute (+ retry & recovery, Part 17)
#       → smart settle (Parts 4/16) → block detection / human-in-the-loop (37)
#       → expectations (14) → request correlation (13) → evidence (28)
#       → timeline + structured log (29/33) → post_action hooks (32)
#
# and returns a compact, JSON-serialisable result (Part 26).  ``finish()``
# writes the full HAR, a noise-free HAR, per-action HARs, discovered endpoints,
# console/download/stream/WebSocket logs, the timeline, an AI-readable
# ``report.json`` + ``report.md`` and a manifest.


# ═══════════════════════════════════════════════════════════════════════
# retry policy (Part 17) & guardrails (Part 38)
# ═══════════════════════════════════════════════════════════════════════
RETRYABLE_KINDS = ("timeout", "intercepted", "detached", "not_found", "network", "http_5xx", "rate_limited",
                   "expectation", "crash", "closed", "blocked")


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_ms: float = 400.0
    factor: float = 2.0
    max_backoff_ms: float = 6000.0
    jitter: float = 0.25
    retry_on: tuple = RETRYABLE_KINDS

    @classmethod
    def from_any(cls, value: Any) -> "RetryPolicy":
        if isinstance(value, RetryPolicy):
            return value
        if value is None:
            return cls()
        if isinstance(value, int):
            return cls(max_attempts=max(1, value))
        if isinstance(value, dict):
            data = dict(value)
            if "retry_on" in data:
                data["retry_on"] = tuple(data["retry_on"])
            return cls(**data)
        raise ConfigError(f"unsupported retry policy {value!r}")

    def delay_ms(self, attempt: int) -> float:
        base = min(self.max_backoff_ms, self.backoff_ms * (self.factor ** max(0, attempt - 1)))
        return base * (1 + random.uniform(-self.jitter, self.jitter))


@dataclass
class Guardrails:
    """Hard limits so an agent can never loop forever or burn unbounded time."""

    max_actions: int = 1000
    max_duration_s: float = 3600.0
    max_consecutive_failures: int = 5
    max_repeats: int = 6
    max_failures_per_target: int = 3
    _failures: int = 0
    _per_target: dict = field(default_factory=dict)
    _signatures: list = field(default_factory=list)

    @classmethod
    def from_any(cls, value: Any) -> "Guardrails":
        if isinstance(value, Guardrails):
            return value
        if value is None:
            return cls()
        if isinstance(value, dict):
            return cls(**value)
        raise ConfigError(f"unsupported guardrails {value!r}")

    def before(self, session: "Session", kind: str, target: str) -> None:
        elapsed = time.monotonic() - session._t0
        stats = {"actions": len(session._actions), "elapsed_s": round(elapsed, 1),
                 "consecutive_failures": self._failures}
        if len(session._actions) >= self.max_actions:
            raise GuardrailViolation(f"action budget exhausted ({self.max_actions} actions)", details=stats,
                                     hint="the task is taking too many steps; stop and report what was achieved")
        if elapsed > self.max_duration_s:
            raise GuardrailViolation(f"time budget exhausted ({self.max_duration_s:.0f}s)", details=stats,
                                     hint="stop, save partial results and report")
        if self._failures >= self.max_consecutive_failures:
            last = session._actions[-1] if session._actions else {}
            raise GuardrailViolation(
                f"{self._failures} consecutive failed actions - aborting to avoid an endless loop",
                details={**stats, "last_error": last.get("error"), "last_target": last.get("target")},
                hint="re-plan: observe() the page, check the target exists, or stop")
        key = f"{kind}|{target}"
        if self._per_target.get(key, 0) >= self.max_failures_per_target:
            raise GuardrailViolation(f"target {target!r} already failed {self._per_target[key]} times for '{kind}'",
                                     details=stats, hint="this target cannot be reached - choose another approach")
        sig = f"{key}|{session.page.url if session.page else ''}|{session._state_hash()}"
        self._signatures.append(sig)
        recent = self._signatures[-(self.max_repeats + 1):]
        if len(recent) > self.max_repeats and len(set(recent)) == 1:
            raise GuardrailViolation(f"loop detected: '{kind} {target}' repeated {self.max_repeats + 1}× with no page change",
                                     details=stats, hint="the action has no effect; stop repeating it")

    def after(self, kind: str, target: str, ok: bool) -> None:
        key = f"{kind}|{target}"
        if ok:
            self._failures = 0
        else:
            self._failures += 1
            self._per_target[key] = self._per_target.get(key, 0) + 1


class ExpectationFailed(AgentTraceError):
    code = "expectation_failed"


class HttpStatusError(NavigationError):
    code = "http_error"

    def __init__(self, status: int, url: str, retry_after: Optional[float] = None) -> None:
        super().__init__(f"HTTP {status} for {url}", details={"status": status, "retry_after_s": retry_after})
        self.status = status
        self.retry_after = retry_after


STORAGE_JS = r"""
async (withValues) => {
  const out = {origin: location.origin, local_storage: {}, session_storage: {}, indexed_db: [], cache_storage: []};
  const val = (v) => withValues ? v : (v == null ? 0 : String(v).length);
  try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); out.local_storage[k] = val(localStorage.getItem(k)); } } catch (e) {}
  try { for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); out.session_storage[k] = val(sessionStorage.getItem(k)); } } catch (e) {}
  try {
    const dbs = indexedDB.databases ? await indexedDB.databases() : [];
    for (const d of dbs) {
      const info = {name: d.name, version: d.version, stores: []};
      await new Promise((res) => {
        const r = indexedDB.open(d.name); r.onerror = () => res(); r.onblocked = () => res();
        r.onsuccess = () => {
          const db = r.result; const names = Array.from(db.objectStoreNames);
          if (!names.length) { db.close(); return res(); }
          const tx = db.transaction(names, 'readonly'); let left = names.length;
          const done = () => { if (--left === 0) { db.close(); res(); } };
          for (const n of names) {
            const store = tx.objectStore(n); const q = store.getAllKeys();
            q.onsuccess = () => { const keys = q.result.map(String);
              const entry = {name: n, count: keys.length, keys: keys.slice(0, 20)};
              if (withValues) { const g = store.getAll(); g.onsuccess = () => { entry.values = g.result.slice(0, 20); info.stores.push(entry); done(); }; g.onerror = () => { info.stores.push(entry); done(); }; }
              else { info.stores.push(entry); done(); } };
            q.onerror = done;
          }
        };
      });
      out.indexed_db.push(info);
    }
  } catch (e) { out.indexed_db_error = String(e); }
  try { out.cache_storage = await caches.keys(); } catch (e) {}
  return out;
}
"""


COMMIT_OK_JS = r"""
el => {
  if (document.activeElement !== el) return false;
  const type = (el.type || '').toLowerCase(), role = (el.getAttribute('role') || '').toLowerCase();
  if (type === 'search' || role === 'combobox' || role === 'searchbox' || el.getAttribute('aria-autocomplete') ||
      el.getAttribute('list') || el.getAttribute('aria-haspopup') === 'listbox') return false;
  const hint = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('autocomplete')].join(' ').toLowerCase();
  return !/search|query|suggest|autocomplete|typeahead|\bq\b/.test(hint);
}
"""


def classify_error(exc: BaseException) -> str:
    if isinstance(exc, GuardrailViolation):
        return "guardrail"
    if isinstance(exc, ActionVetoed):
        return "vetoed"
    if isinstance(exc, BlockedError):
        return "blocked"
    if isinstance(exc, ElementNotFoundError):
        return "not_found"
    if isinstance(exc, ExpectationFailed):
        return "expectation"
    if isinstance(exc, RateLimitError):
        return "fatal"
    if isinstance(exc, HttpStatusError):
        return "rate_limited" if exc.status == 429 else ("http_5xx" if exc.status >= 500 else "http_4xx")
    msg = str(exc)
    low = msg.lower()
    if "intercepts pointer events" in msg or "outside of the viewport" in low or "not visible" in low \
            or "element is not stable" in low:
        return "intercepted"
    if "not attached" in low or "detached" in low:
        return "detached"
    if "net::err_" in low or "ns_error" in low or "connection refused" in low:
        return "network"
    if "timeout" in low and ("exceeded" in low or "timed out" in low) or type(exc).__name__ == "TimeoutError":
        return "timeout"
    if "crash" in low:
        return "crash"
    if "closed" in low and ("target" in low or "page" in low or "context" in low):
        return "closed"
    return "error"


DISMISS_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity || '1') > 0.05; };
  const overlays = Array.from(document.querySelectorAll('[role=dialog],[aria-modal=true],dialog[open],.modal,.modal-backdrop,.overlay,.popup,' +
    '[class*=modal],[class*=overlay],[class*=popup],[id*=cookie],[class*=cookie],[id*=consent],[class*=consent],#onetrust-banner-sdk,[class*=newsletter]')).filter(vis);
  const center = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
  if (center) { let c = center; while (c && c !== document.body) { const s = getComputedStyle(c);
      if ((s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex || '0', 10) >= 10 && vis(c)) { if (!overlays.includes(c)) overlays.push(c); break; } c = c.parentElement; } }
  const words = /^(×|x|✕|✖|close|dismiss|no thanks|not now|maybe later|skip|accept all cookies|accept all|accept|allow all|got it|i agree|agree|ok|continue|reject all)$/i;
  const found = [];
  for (const ov of overlays) {
    for (const b of ov.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]')) {
      if (!vis(b)) continue;
      const label = (b.getAttribute('aria-label') || b.innerText || b.value || '').trim();
      if (/close|dismiss/i.test(b.getAttribute('aria-label') || '') || words.test(label)) {
        b.setAttribute('data-at-dismiss', String(found.length + 1)); found.push(label.slice(0, 40)); break; }
    }
  }
  return found;
}
"""

EXTRACT_JS = r"""
(args) => {
  const abs = (v) => { try { return new URL(v, location.href).href; } catch (e) { return v; } };
  const getv = (root, spec) => {
    let sel = spec, attr = null, mode = 'text';
    const at = spec.lastIndexOf('@'); if (at > -1 && !spec.slice(at).includes(']')) { sel = spec.slice(0, at).trim(); attr = spec.slice(at + 1).trim(); }
    if (sel.endsWith('::html')) { mode = 'html'; sel = sel.slice(0, -6); } else if (sel.endsWith('::text')) { sel = sel.slice(0, -6); }
    const el = sel ? root.querySelector(sel) : root; if (!el) return null;
    if (attr) { const v = el.getAttribute(attr); return v === null ? null : (['href', 'src', 'action', 'data-src'].includes(attr) ? abs(v) : v); }
    if (mode === 'html') return el.innerHTML.trim();
    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const fields = args.fields || {};
  if (args.item) {
    const out = [];
    for (const it of document.querySelectorAll(args.item)) {
      const row = {}; for (const [k, spec] of Object.entries(fields)) row[k] = getv(it, spec);
      if (!Object.keys(fields).length) row.text = (it.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300);
      out.push(row); if (args.limit && out.length >= args.limit) break;
    }
    return out;
  }
  const row = {}; for (const [k, spec] of Object.entries(fields)) row[k] = getv(document, spec); return row;
}
"""

INSPECT_JS = r"""
() => {
  const txt = (el) => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3')).slice(0, 25).map(h => ({ level: h.tagName, text: txt(h).slice(0, 100) }));
  const forms = Array.from(document.forms).slice(0, 10).map(f => ({ id: f.id || '', name: f.getAttribute('name') || '', action: f.getAttribute('action') || '',
    method: (f.getAttribute('method') || 'get').toLowerCase(), fields: Array.from(f.elements).filter(e => e.name || e.id).slice(0, 30).map(e => ({
      name: e.name || e.id, type: e.type || e.tagName.toLowerCase(), required: !!e.required,
      label: (e.labels && e.labels[0] ? txt(e.labels[0]) : (e.placeholder || e.getAttribute('aria-label') || '')).slice(0, 60),
      filled: e.type === 'password' ? !!e.value : undefined, value: (e.type === 'password' || e.type === 'hidden') ? undefined : String(e.value || '').slice(0, 60) })) }));
  const meta = {}; for (const m of document.querySelectorAll('meta[name],meta[property]')) { const k = m.getAttribute('name') || m.getAttribute('property');
    if (/^(description|og:|twitter:|robots|keywords)/.test(k)) meta[k] = (m.getAttribute('content') || '').slice(0, 160); }
  const canonical = document.querySelector('link[rel=canonical]');
  const ld = Array.from(document.querySelectorAll('script[type="application/ld+json"]')).map(s => { try { const j = JSON.parse(s.textContent); return (Array.isArray(j) ? j : [j]).map(x => x['@type']).join(','); } catch (e) { return 'invalid'; } });
  const hydration = ['__NEXT_DATA__', '__NUXT__', '__INITIAL_STATE__', '__APOLLO_STATE__', '__PRELOADED_STATE__', '__remixContext']
    .filter(k => (k === '__NEXT_DATA__' ? !!document.getElementById('__NEXT_DATA__') : typeof window[k] !== 'undefined'));
  const dialogs = Array.from(document.querySelectorAll('[role=dialog],[aria-modal=true],dialog[open]')).filter(d => d.getBoundingClientRect().height > 0).map(d => (d.getAttribute('aria-label') || txt(d)).slice(0, 80));
  const tab_widgets = Array.from(document.querySelectorAll('[role=tab]')).map(t => ({ text: txt(t).slice(0, 50), selected: t.getAttribute('aria-selected') === 'true' }));
  const ls = {}; try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); ls[k] = (localStorage.getItem(k) || '').length; } } catch (e) {}
  const ss = {}; try { for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); ss[k] = (sessionStorage.getItem(k) || '').length; } } catch (e) {}
  return { ready_state: document.readyState, lang: document.documentElement.lang || '', headings, forms, meta,
    canonical: canonical ? canonical.href : '', json_ld_types: ld, hydration_data: hydration, open_dialogs: dialogs, tab_widgets,
    scroll: { x: Math.round(scrollX), y: Math.round(scrollY), height: document.documentElement.scrollHeight, viewport_h: innerHeight },
    dom_nodes: document.getElementsByTagName('*').length, local_storage: ls, session_storage: ss,
    text_excerpt: txt(document.body).slice(0, 1200) };
}
"""

HIT_TEST_JS = r"""
(el) => {
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return { covered: false, visible: false };
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return { covered: false, offscreen: true };
  const top = document.elementFromPoint(x, y);
  if (!top || top === el || el.contains(top) || top.contains(el) && top.tagName === 'LABEL') return { covered: false };
  const lab = el.labels && Array.from(el.labels).some(l => l === top || l.contains(top));
  if (lab) return { covered: false };
  const d = top.closest('[role=dialog],[aria-modal=true],dialog,.modal,.modal-backdrop,.overlay,[class*=modal],[class*=overlay],[class*=popup],[id*=cookie],[class*=cookie],[id*=consent],[class*=consent]');
  return { covered: true, by: (top.tagName.toLowerCase() + (top.id ? '#' + top.id : '') + (top.className && typeof top.className === 'string' ? '.' + top.className.split(/\s+/)[0] : '')).slice(0, 60), overlay: !!d };
}
"""

DETERMINISTIC_JS = r"""
(() => { let s = 1234567; Math.random = function () { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; }; })();
"""

SESSION_STORAGE_RESTORE_JS = r"""
((data) => { try { const items = data[location.origin]; if (!items) return;
  for (const [k, v] of Object.entries(items)) { if (sessionStorage.getItem(k) === null) sessionStorage.setItem(k, v); } } catch (e) {} })
"""


@dataclass
class Resolved:
    element: Optional[ElementInfo]
    locator: Any
    strategy: str
    score: float = 0.0
    healed: bool = False
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {"strategy": self.strategy, "score": round(self.score, 2)}
        if self.healed:
            out["healed"] = True
        if self.element is not None:
            out.update(self.element.to_dict())
        if self.reasons:
            out["why"] = self.reasons[:3]
        return out


def load_state_file(path: Any) -> tuple:
    """Split an AgentTrace state file into (playwright_state, session_storage)."""
    data = read_json(path)
    if not isinstance(data, dict):
        raise ConfigError(f"invalid storage state file {path}")
    session_storage = data.pop("sessionStorage", None) or {}
    data.pop("agenttrace", None)
    data.setdefault("cookies", [])
    data.setdefault("origins", [])
    return data, session_storage


class Session:
    """High-level, selector-free browser session with full network capture.

    >>> with Session(stealth=True) as s:
    ...     s.goto("https://example.com")
    ...     s.click("More information")
    ...     print(s.requests("api"))
    """

    def __init__(self, *, profile: Any = None, engine: Optional[BrowserEngine] = None, out_dir: Any = None,
                 name: str = "session", headless: Optional[bool] = None, stealth: Optional[bool] = None,
                 hooks: Optional[HookManager] = None, plugins: Iterable[Any] = (), guardrails: Any = None,
                 retry: Any = None, rate_limit: Any = None, hitl: Any = "wait", evidence: str = "failures",
                 redact: bool = False, body_policy: str = "auto", strict: bool = True, trace: bool = False,
                 deterministic: bool = False, storage_state: Any = None, split_har: bool = True,
                 settle_quiet_ms: float = 400, max_settle_ms: float = 20_000, noise_rules: Any = None,
                 dialog_policy: str = "accept", debug: bool = False, replay_har: Any = None,
                 replay_not_found: str = "abort", **profile_overrides: Any) -> None:
        if profile is None:
            prof = BrowserProfile()
        elif isinstance(profile, dict):
            prof = BrowserProfile.from_dict(profile)
        elif isinstance(profile, BrowserProfile):
            prof = profile
        else:
            raise ConfigError(f"profile must be a BrowserProfile or dict, got {type(profile).__name__}")
        overrides = dict(profile_overrides)
        if headless is not None:
            overrides["headless"] = headless
        if stealth is not None:
            overrides["stealth"] = stealth
        if storage_state is not None:
            overrides["storage_state"] = storage_state
        if deterministic:
            overrides.setdefault("locale", "en-US")
            overrides.setdefault("timezone_id", "UTC")
            overrides.setdefault("viewport", {"width": 1280, "height": 800})
            overrides.setdefault("reduced_motion", "reduce")
        self.profile = prof.merged(**overrides) if overrides else prof
        self.name = slugify(name, 40, "session")
        self.id = new_id(self.name)
        self.out_dir = Path(out_dir) if out_dir else Path(DEFAULT_RUNS_DIR) / self.id
        self.hooks = hooks if hooks is not None else HookManager()
        for plugin in plugins or ():
            self.hooks.load(plugin) if isinstance(plugin, (str, Path)) else self.hooks.register_plugin(plugin)
        self.guardrails = Guardrails.from_any(guardrails)
        self.retry = RetryPolicy.from_any(retry)
        self.limiter = RateLimiter.from_any(rate_limit) or RateLimiter(min_interval_s=0.0, max_concurrency_per_domain=4)
        self.hitl = HumanInTheLoop.from_any(hitl)
        self.evidence_mode = evidence
        self.redact = redact
        self.body_policy = body_policy
        self.strict = strict
        self.trace = trace
        self.debug = debug
        self.replay_har = replay_har
        self.replay_not_found = replay_not_found
        self.deterministic = deterministic
        self.split_har = split_har
        self.settle_quiet_ms = settle_quiet_ms
        self.max_settle_ms = max_settle_ms
        self.noise_rules = noise_rules if isinstance(noise_rules, NoiseRules) else NoiseRules.from_dict(noise_rules)
        self.dialog_policy = dialog_policy
        self.engine = engine
        self._own_engine = engine is None
        self.context = None
        self.page = None
        self.recorder: Optional[NetworkRecorder] = None
        self.events = EventLog()
        self._actions: list = []
        self._observed: dict = {}
        self._routes: list = []
        self._annotations: dict = {}
        self._t0 = time.monotonic()
        self._started = False
        self._finished = False
        self._summary: Optional[dict] = None
        self._log = get_logger("session")
        self._session_storage: dict = {}
        self._last_form: Optional[str] = None
        self._last_mark: Optional[int] = None

    # ═══ lifecycle ═══════════════════════════════════════════════════
    def start(self) -> "Session":
        if self._started:
            return self
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.events.sink = self.out_dir / "timeline.jsonl"
        if self.engine is None:
            self.engine = BrowserEngine(self.profile)
        if not self.engine.started:
            self.engine.start()
        profile = self.profile
        state = profile.storage_state
        if isinstance(state, (str, Path)) and Path(state).is_file():
            pw_state, self._session_storage = load_state_file(state)
            profile = profile.merged(storage_state=pw_state)
        elif isinstance(state, dict):
            pw_state = dict(state)
            self._session_storage = pw_state.pop("sessionStorage", None) or {}
            pw_state.pop("agenttrace", None)
            profile = profile.merged(storage_state=pw_state)
        self.context = self.engine.new_context(profile)
        self.context.add_init_script(f"({DOM_WATCH_JS})();")
        self.context.add_init_script(TIMER_TRACK_JS)
        if self._session_storage:
            self.context.add_init_script(f"{SESSION_STORAGE_RESTORE_JS}({json.dumps(self._session_storage)});")
        if self.deterministic:
            self.context.add_init_script(DETERMINISTIC_JS)
        if self.replay_har:
            # offline, reproducible runs: every request is answered from a recorded HAR
            # (record it with body_policy="all" so scripts/styles/images are in it too)
            self.context.route_from_har(str(self.replay_har), not_found=self.replay_not_found)
        if self.trace:
            try:
                self.context.tracing.start(screenshots=True, snapshots=True, sources=False)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("tracing unavailable: %s", exc)
                self.trace = False
        self.recorder = NetworkRecorder(self.context, events=self.events, hooks=self.hooks,
                                        body_policy=self.body_policy, dialog_policy=self.dialog_policy,
                                        on_page=self._on_new_page, session_ref=self).attach()
        self.page = self.context.new_page()
        self.recorder.wait_ready(self.page)  # CDP attached + profile applied *before* the first navigation
        if self.deterministic:
            try:
                self.context.clock.install(time=1767225600)  # 2026-01-01T00:00:00Z
            except Exception as exc:  # noqa: BLE001
                self._log.debug("clock.install unavailable: %s", exc)
        if self.debug:
            self.events.listeners.append(self._debug_log)
        self._started = True
        self._t0 = time.monotonic()
        self.events.emit("session_start", session_id=self.id, browser=self.engine.launch_info)
        self._jlog("info", "session_start", f"session {self.id} started ({self.engine.launch_info.get('how')})",
                   profile=self.profile.to_dict())
        self.hooks.emit("session_start", session=self)
        return self

    def __enter__(self) -> "Session":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.finish()
        except Exception as err:  # noqa: BLE001 - never mask the original exception
            self._log.warning("finish() failed during __exit__: %s", err)
            self.close()

    def _on_new_page(self, page, pid: str) -> None:
        if self.engine is not None:
            self.engine.apply_page_profile(page, self.profile)
        page.on("close", lambda p=page: self._on_page_closed(p))

    def _on_page_closed(self, page) -> None:
        if page is self.page:
            others = [p for p in self.recorder.open_pages() if p is not page]
            opener = None
            try:
                opener = page.opener()
            except Exception:  # noqa: BLE001
                pass
            self.page = opener if opener is not None and not opener.is_closed() else (others[-1] if others else None)

    def _ensure(self) -> None:
        if self._finished:
            raise BrowserError("session already finished", hint="create a new Session")
        if not self._started:
            self.start()
        if self.page is None or self.page.is_closed():
            open_pages = self.recorder.open_pages()
            self.page = open_pages[-1] if open_pages else self.context.new_page()

    def _jlog(self, level: str, event: str, msg: str, **fields: Any) -> None:
        fields.setdefault("session_id", self.id)
        try:
            append_jsonl(self.out_dir / "log.jsonl", {"ts": iso(), "level": level, "event": event, "msg": msg,
                                                      **to_jsonable(fields)})
        except OSError:
            pass
        log_event(self._log, {"debug": 10, "info": 20, "warning": 30, "error": 40}.get(level, 20), event, msg,
                  **{k: v for k, v in fields.items() if k in ("session_id", "action_id", "request_id")})

    _DEBUG_KINDS = ("request", "response", "request_finished", "request_failed", "console", "dialog", "route_change",
                    "navigation", "navigation_cancelled", "page_opened", "page_closed", "download_saved",
                    "websocket_opened", "retry", "blocked")

    def _debug_log(self, event: dict) -> None:
        """Verbose tracing (``debug=True``): network/runtime events go to log.jsonl too."""
        if event.get("kind") not in self._DEBUG_KINDS:
            return
        fields = {k: v for k, v in event.items() if k not in ("seq", "ts", "t_ms", "kind", "level")}
        desc = " ".join(str(event.get(k)) for k in ("method", "status", "url", "failure", "text") if event.get(k))
        self._jlog("warning" if event.get("level") in ("warning", "error") else "debug", event["kind"],
                   truncate(desc, 300), **fields)

    def _sleep(self, seconds: float) -> None:
        sleep_pumped(self.recorder, self.page, seconds * 1000.0)

    def _state_hash(self) -> str:
        try:
            text = self.page.evaluate("() => (document.title||'') + '|' + (document.body ? document.body.innerText.length : 0)")
        except Exception:  # noqa: BLE001
            text = ""
        return sha256_hex(f"{self.page.url if self.page else ''}|{text}")[:12]

    # ═══ the action pipeline ═════════════════════════════════════════
    def _act(self, kind: str, target: Any, fn: Callable[[int], Optional[dict]], *, expect: Any = None,
             wait: Any = "auto", args: Optional[dict] = None, retry: Any = None, navigation: bool = False) -> dict:
        self._ensure()
        tdesc = target if isinstance(target, str) else (json.dumps(target)[:120] if target is not None else "")
        try:
            self.guardrails.before(self, kind, tdesc)
        except GuardrailViolation as exc:
            self.events.emit("guardrail", level="error", reason=str(exc))
            self._jlog("error", "guardrail", str(exc), details=exc.details)
            raise
        aid = f"a{len(self._actions) + 1:03d}"
        started_iso, t0 = iso(), mono_ms()
        url_before = self.page.url
        args = args if args is not None else {}
        action = {"id": aid, "action": kind, "target": tdesc, "args": args, "started": started_iso}
        veto = next((r for r in self.hooks.emit("pre_action", session=self, action=action)
                     if isinstance(r, dict) and r.get("veto")), None)
        self.events.emit("action_start", action_id=aid, action=kind, target=tdesc)
        self._jlog("info", "action_start", f"{kind} {tdesc}", action_id=aid)
        self.recorder.current_action = aid
        mark = self.recorder.mark()
        self._last_mark = mark
        policy = RetryPolicy.from_any(retry) if retry is not None else self.retry
        attempts: list = []
        result: dict = {}
        error: Optional[BaseException] = None
        settle: dict = {}
        blocked_info = None
        validation = None
        if veto is not None:
            error = ActionVetoed(f"vetoed by plugin: {veto['veto']}",
                                 hint=veto.get("hint") or "a plugin blocked this action on purpose; do not retry it")
            attempts.append({"attempt": 1, "ts": iso(), "ok": False, "error": str(error), "error_kind": "vetoed"})
            self.events.emit("action_vetoed", action_id=aid, reason=str(veto["veto"]), level="warning")
            self._jlog("warning", "action_vetoed", str(error), action_id=aid)
        for attempt in range(1, (0 if veto is not None else policy.max_attempts) + 1):
            att = {"attempt": attempt, "ts": iso()}
            try:
                result = fn(attempt) or {}
                settle = self._settle(wait, since=mark)
                if self.hitl.mode != "off" and (navigation or self._navigated_since(mark)):
                    info = self._detect_block_now(mark)
                    if info is not None and info.get("kind") != "rate_limited":
                        blocked_info = info
                        resolved = self.hitl.handle(self, info)
                        att["hitl"] = resolved
                        settle = self._settle(wait, since=mark)
                if expect:
                    validation = validate_expectations(self.recorder.for_action(aid), _as_list(expect))
                    if not validation["ok"]:
                        raise ExpectationFailed("expectation failed: " + "; ".join(
                            f for c in validation["checks"] for f in c["failures"])[:600], details=validation)
                att["ok"] = True
                attempts.append(att)
                error = None
                break
            except Exception as exc:  # noqa: BLE001 - classified below
                kind_err = classify_error(exc)
                att.update(ok=False, error=f"{type(exc).__name__}: {truncate(str(exc).splitlines()[0] if str(exc) else '', 300)}",
                           error_kind=kind_err)
                attempts.append(att)
                error = exc
                self.events.emit("action_error", action_id=aid, level="warning", error_kind=kind_err,
                                 attempt=attempt, error=att["error"])
                self._jlog("warning", "action_error", att["error"], action_id=aid, attempt=attempt, error_kind=kind_err)
                if kind_err in ("guardrail", "fatal") or kind_err not in policy.retry_on or attempt >= policy.max_attempts:
                    break
                if isinstance(exc, BlockedError):
                    break
                att["recovery"] = self._recover(kind_err, exc)
                self.events.emit("retry", action_id=aid, attempt=attempt + 1, recovery=att["recovery"],
                                 error_kind=kind_err)
                if kind_err != "rate_limited":
                    self._sleep(policy.delay_ms(attempt) / 1000.0)
        self.recorder.current_action = None
        if self._navigated_since(mark) and kind != "fill":
            self._last_form = None
        downloads = []
        if self.recorder.pending_downloads():
            self.recorder.pump(200, self.page)
            downloads = self.recorder.save_downloads(self.out_dir / "downloads")
        ok = error is None
        records = self.recorder.for_action(aid)
        history = [r for r in self.recorder.records if r.seq <= mark]
        corr = correlate(records, action_id=aid, action_start=t0, history=history[-400:])
        console_errors = [c for c in self.recorder.console if c.get("action_id") == aid and
                          c["kind"] in ("pageerror", "crash") or (c.get("action_id") == aid and c.get("level") == "error")]
        title = ""
        try:
            title = self.page.title() if self.page and not self.page.is_closed() else ""
            if title:
                self.recorder.set_segment_title(self.page, title)
        except Exception:  # noqa: BLE001
            pass
        new_pages = [p["id"] for p in self.recorder.pages.values() if p.get("action_id") == aid]
        summary = {
            "id": aid, "action": kind, "target": tdesc, "ok": ok,
            "url": self.page.url if self.page and not self.page.is_closed() else "",
            "title": title, "navigated": (self.page.url if self.page and not self.page.is_closed() else "") != url_before,
            "started": started_iso, "ended": iso(), "duration_ms": round(mono_ms() - t0),
            "attempts": len(attempts),
        }
        if args:
            summary["args"] = args
        summary.update({k: v for k, v in result.items() if v is not None})
        summary["network"] = {
            "requests": len(records),
            "primary": corr["primary"].summary() if corr["primary"] is not None else None,
            "related": [r.summary() for r in corr["related"][:6] if corr["primary"] is None or r.id != corr["primary"].id],
            "background_or_noise": len(corr["background"]),
            "failed": [r.summary() for r in records if r.failure or (r.status or 0) >= 400][:6],
        }
        summary["wait"] = settle
        if console_errors:
            summary["console_errors"] = [{"kind": c["kind"], "text": truncate(c["text"], 200)} for c in console_errors[:5]]
        dialogs = [d for d in self.recorder.dialogs if d.get("action_id") == aid]
        if dialogs:
            summary["dialogs"] = [{"type": d.get("type"), "message": d.get("message"), "handled": d.get("handled")} for d in dialogs]
        if downloads:
            summary["downloads"] = downloads
        if new_pages:
            summary["new_pages"] = new_pages
        if validation is not None:
            summary["validation"] = validation
            self.hooks.emit("validation", session=self, action=summary, validation=validation)
        if blocked_info:
            summary["blocked"] = {k: blocked_info.get(k) for k in ("kind", "vendor", "widget", "confidence", "signals")}
        if len(attempts) > 1:
            summary["retries"] = [{k: a.get(k) for k in ("attempt", "error_kind", "error", "recovery")} for a in attempts[:-1]]
        if error is not None:
            summary["error"] = f"{type(error).__name__}: {truncate(str(error), 400)}"
            summary["error_kind"] = classify_error(error)
            hint = getattr(error, "hint", None)
            if hint:
                summary["hint"] = hint
            details = getattr(error, "details", None)
            if isinstance(details, dict) and details.get("candidates"):
                summary["candidates"] = details["candidates"][:8]
        want_evidence = self.evidence_mode == "all" or (self.evidence_mode == "failures" and not ok) or (
            self.evidence_mode == "important" and (not ok or summary.get("navigated") or downloads or validation or
                                                   kind in ("goto", "submit", "download")))
        if want_evidence:
            summary["evidence"] = self._capture_evidence(aid, summary, records)
        summary["_ranking"] = corr["ranking"]
        self._actions.append(summary)
        self.guardrails.after(kind, tdesc, ok)
        self.events.emit("action_end", action_id=aid, action=kind, ok=ok, duration_ms=summary["duration_ms"],
                         primary=(summary["network"]["primary"] or {}).get("url"), level="info" if ok else "error")
        self._jlog("info" if ok else "error", "action_end", f"{kind} {'ok' if ok else 'FAILED'} ({summary['duration_ms']}ms)",
                   action_id=aid, request_id=(summary["network"]["primary"] or {}).get("id"),
                   error=summary.get("error"))
        if not ok:
            self._log_failure(summary, records, validation)
        self.hooks.emit("post_action", session=self, action=summary)
        public = {k: v for k, v in summary.items() if not k.startswith("_")}
        if error is not None:
            self.hooks.emit("error", session=self, action=public, error=error)
            if self.strict:
                if isinstance(error, (GuardrailViolation, BlockedError)):
                    raise error
                raise ActionError(f"{kind} {tdesc!r} failed: {truncate(str(error), 300)}", details=public,
                                  hint=getattr(error, "hint", None) or _default_hint(summary.get("error_kind", ""))) from error
        return public

    def _log_failure(self, summary: dict, records: list, validation: Optional[dict]) -> None:
        """One self-contained log line that explains a failed action (Part 33):
        which action, which request(s), which error — no replay needed."""
        culprits = [r for r in records if r.failure or (r.status or 0) >= 400]
        prim = summary["network"].get("primary") or {}
        failed_checks = [{"expect": c.get("expectation"), "failures": c.get("failures"), "request_id": c.get("request_id")}
                         for c in (validation or {}).get("checks", []) if not c.get("ok")]
        blamed = next((c["request_id"] for c in failed_checks if c.get("request_id")), None) or (
            culprits[-1].id if culprits else prim.get("id"))
        self._jlog("error", "action_failed", f"{summary['action']} {summary['target']!r} failed: "
                   f"{truncate(summary.get('error') or '', 240)}",
                   action_id=summary["id"], request_id=blamed, action=summary["action"], target=summary["target"],
                   page_url=summary.get("url"), error=summary.get("error"), error_kind=summary.get("error_kind"),
                   hint=summary.get("hint"), attempts=summary.get("attempts"),
                   retries=summary.get("retries"), failed_checks=failed_checks[:5],
                   failed_requests=[{"request_id": r.id, "method": r.method, "url": truncate(r.url, 300),
                                     "status": r.status, "failure": r.failure,
                                     "response_excerpt": truncate(r.text() or "", 300) if r.response_body else None}
                                    for r in culprits[:6]],
                   primary_request=prim or None, console_errors=summary.get("console_errors"),
                   evidence=summary.get("evidence"))

    def _navigated_since(self, mark: int) -> bool:
        return any(r.is_navigation and r.frame_id and r.frame_id.endswith(".f0") for r in self.recorder.since(mark))

    def _detect_block_now(self, mark: int) -> Optional[dict]:
        docs = [r for r in self.recorder.since(mark) if r.is_navigation and (r.frame_id or "").endswith(".f0")]
        status, headers = None, {}
        if docs:
            status = docs[-1].status
            headers = {h["name"]: h["value"] for h in docs[-1].response_headers}
        return detect_block(self.page, status=status, headers=headers)

    def _settle(self, wait: Any, *, since: int) -> dict:
        if wait in (None, False, "none"):
            return {"skipped": True}
        page = self.page
        if page is None or page.is_closed():
            return {"page_closed": True}
        out: dict = {}
        if self._navigated_since(since):
            try:
                page.wait_for_load_state("load", timeout=min(self.profile.navigation_timeout_ms, 15000))
            except Exception:  # noqa: BLE001 - slow third-party subresources; network settle below still applies
                out["load_timeout"] = True
        if isinstance(wait, dict):
            if set(wait) <= {"quiet_ms", "max_wait_ms", "expect", "dom_quiet_ms"}:
                out.update(settle_page(page, self.recorder, since_seq=since,
                                       quiet_ms=float(wait.get("quiet_ms", self.settle_quiet_ms)),
                                       max_wait_ms=float(wait.get("max_wait_ms", self.max_settle_ms)),
                                       dom_quiet_ms=float(wait.get("dom_quiet_ms", 250)),
                                       expect=tuple(wait.get("expect") or ())))
                return out
            cond = {k: v for k, v in wait.items() if k != "timeout_ms"}
            out.update(wait_for_condition(self, cond, since_seq=since, timeout_ms=float(wait.get("timeout_ms", 15000))))
            return out
        if wait == "network":
            res = self.recorder.wait_idle(quiet_ms=self.settle_quiet_ms, max_wait_ms=self.max_settle_ms, page=page,
                                          since_seq=since)
            out.update(network_idle=res["idle"], waited_ms=res["waited_ms"], pending=res["pending"])
            return out
        if wait == "load":
            return out
        out.update(settle_page(page, self.recorder, since_seq=since, quiet_ms=self.settle_quiet_ms,
                               max_wait_ms=self.max_settle_ms))
        return out

    def _recover(self, kind: str, exc: BaseException) -> str:
        page = self.page
        try:
            if kind == "intercepted":
                dismissed = self.dismiss_overlays()
                return f"dismissed overlay {dismissed}" if dismissed else "pressed Escape"
            if kind in ("detached", "not_found"):
                self._observed.clear()
                settle_page(page, self.recorder, quiet_ms=250, max_wait_ms=4000)
                return "re-observed page"
            if kind == "crash":
                page.reload()
                return "reloaded crashed page"
            if kind == "closed":
                self._ensure()
                return "switched to an open page"
            if kind == "rate_limited":
                return "waiting for Retry-After"
            if kind in ("network", "http_5xx", "timeout", "blocked"):
                return "backoff"
            if kind == "expectation":
                return "retrying action"
        except Exception as err:  # noqa: BLE001
            return f"recovery failed: {err}"
        return "none"

    def dismiss_overlays(self) -> list:
        """Close cookie banners / modals / newsletter pop-ups covering the page."""
        page = self.page
        try:
            labels = page.evaluate(DISMISS_JS) or []
        except Exception:  # noqa: BLE001
            labels = []
        clicked = []
        for i, label in enumerate(labels, start=1):
            try:
                page.locator(f'[data-at-dismiss="{i}"]').first.click(timeout=2000)
                clicked.append(label)
            except Exception:  # noqa: BLE001
                continue
        if not clicked:
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
        self.events.emit("overlay_dismissed", labels=clicked)
        return clicked

    def _capture_evidence(self, aid: str, summary: dict, records: list) -> dict:
        folder = self.out_dir / "evidence" / aid
        out = {}
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if self.page is not None and not self.page.is_closed():
                shot = folder / "screenshot.png"
                self.page.screenshot(path=str(shot), full_page=False, timeout=8000)
                out["screenshot"] = str(shot)
                dom = folder / "dom.html"
                write_text(dom, self.page.content())
                out["dom"] = str(dom)
            har_path = folder / "network.har"
            save_har(har_path, self._har_for(records))
            out["network_har"] = str(har_path)
            meta = folder / "meta.json"
            write_json(meta, {k: v for k, v in summary.items() if not k.startswith("_")})
            out["meta"] = str(meta)
        except Exception as exc:  # noqa: BLE001 - evidence must never break the run
            out["error"] = str(exc)
        return out

    # ═══ element resolution ══════════════════════════════════════════
    def _frame_ids(self) -> dict:
        return dict(self.recorder._frame_ids) if self.recorder else {}

    def _observe_raw(self, *, frames: bool = True, include_hidden: bool = False, limit: int = 400) -> list:
        els = observe_elements(self.page, frame_ids=self._frame_ids(), include_frames=frames,
                               include_hidden=include_hidden, limit=limit)
        for el in els:
            self._observed[el.ref] = el
        return els

    def _locator_for(self, el: ElementInfo):
        frame = self.page.main_frame
        if el.frame_index:
            frames = self.page.frames
            if el.frame_index < len(frames):
                frame = frames[el.frame_index]
        return frame.locator(f'[data-at-ref="{el.ref}"]')

    def _resolve(self, target: Any, *, purpose: str = "click") -> Resolved:
        page = self.page
        if isinstance(target, dict):
            sel = target.get("selector") or target.get("css")
            if sel:
                try:
                    loc = page.locator(sel)
                    if loc.count() >= 1 and loc.first.is_visible():
                        return Resolved(None, loc.first, "selector", 1.0)
                except Exception:  # noqa: BLE001
                    pass
            fp = target.get("fingerprint") or target
            els = self._observe_raw()
            best, score, runner, details = heal(fp, els)
            if best is not None:
                return Resolved(best, self._locator_for(best), "healed", score, healed=bool(sel),
                                reasons=[f"fingerprint {score:.2f} (runner-up {runner:.2f})"])
            text = target.get("text") or target.get("name") or target.get("description")
            if text:
                return self._resolve(str(text), purpose=purpose)
            raise ElementNotFoundError("recorded element could not be found (self-healing failed)",
                                       details={"best_score": score, "candidates": [e.describe() for e in els[:8]]},
                                       hint="the page changed too much; observe() and pick a new target")
        text = str(target).strip()
        if not text:
            raise ElementNotFoundError("empty target")
        if is_ref(text):
            info = self._observed.get(text)
            frame_index = int(text[1:text.index("e")]) if text.startswith("f") else 0
            frames = page.frames
            frame = frames[frame_index] if 0 < frame_index < len(frames) else page.main_frame
            loc = frame.locator(f'[data-at-ref="{text}"]')
            try:
                if loc.count() == 1:
                    return Resolved(info, loc, "ref", 1.0)
            except Exception:  # noqa: BLE001
                pass
            if info is not None:
                els = self._observe_raw()
                best, score, runner, _ = heal(info.fingerprint(), els)
                if best is not None:
                    return Resolved(best, self._locator_for(best), "ref-healed", score, healed=True,
                                    reasons=[f"ref {text} was stale; re-found by fingerprint ({score:.2f})"])
            raise ElementNotFoundError(f"element ref {text!r} is no longer on the page",
                                       hint="refs expire when the page changes - call observe() again")
        if looks_like_selector(text):
            try:
                loc = page.locator(text)
                count = loc.count()
            except Exception as exc:  # noqa: BLE001
                raise ElementNotFoundError(f"invalid selector {text!r}: {exc}") from exc
            if count >= 1:
                chosen = loc.first
                for i in range(min(count, 10)):
                    if loc.nth(i).is_visible():
                        chosen = loc.nth(i)
                        break
                return Resolved(None, chosen, "selector", 1.0, reasons=[f"{count} match(es)"] if count > 1 else [])
            words = " ".join(re.findall(r"[A-Za-z][A-Za-z0-9]+", re.sub(r"(css|xpath|text|role)=", "", text)))
            words = re.sub(r"\b(btn|button|div|span|input|link|nav|li|ul|id|class|data|testid)\b", " ", words).strip()
            if not words:
                raise ElementNotFoundError(f"selector {text!r} matched nothing")
            text = words
        els = self._observe_raw()
        ranked = rank_elements(text, els)
        if purpose in ("fill", "type"):
            ranked = [(s + (15 if e.editable else -45), e, r) for s, e, r in ranked]
        elif purpose == "select":
            ranked = [(s + (15 if e.tag == "select" or e.role in ("combobox", "listbox") else -20), e, r) for s, e, r in ranked]
        elif purpose in ("check", "uncheck"):
            ranked = [(s + (15 if e.role in ("checkbox", "radio", "switch") else -25), e, r) for s, e, r in ranked]
        elif purpose == "upload":
            ranked = [(s + (20 if e.type == "file" else -40), e, r) for s, e, r in ranked]
        if purpose == "click" and self._last_form:
            ranked = [(s + (6 if self._last_form in e.ancestry else 0), e, r) for s, e, r in ranked]
        ranked.sort(key=lambda item: (-item[0], item[1].order))
        from_ordinal = re.search(r"\b(first|second|third|fourth|fifth|last|\d+(st|nd|rd|th))\b", text.lower())
        if from_ordinal:
            ranked = rank_elements(text, [e for _s, e, _r in ranked if _s >= MATCH_THRESHOLD]) or ranked
        if ranked and ranked[0][0] >= MATCH_THRESHOLD:
            score, el, reasons = ranked[0]
            return Resolved(el, self._locator_for(el), "text", score / 100.0, reasons=reasons)
        cands = [f"{e.ref}: {e.describe()}" for _s, e, _r in ranked[:8]] or [f"{e.ref}: {e.describe()}" for e in els[:8]]
        raise ElementNotFoundError(f"no element matches {text!r}", details={"candidates": cands},
                                   hint="call observe() and pass an element ref (e.g. 'e12'), or quote the exact visible text")

    # ═══ navigation ══════════════════════════════════════════════════
    def _abs(self, url: str) -> str:
        if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
            return url
        base = self.page.url if self.page and self.page.url.startswith("http") else ""
        if not base:
            return "https://" + url.lstrip("/") if "." in url.split("/")[0] else url
        return urljoin(base, url)

    def goto(self, url: str, *, expect: Any = None, wait: Any = "auto", timeout_ms: Optional[int] = None,
             retry: Any = None) -> dict:
        """Navigate and wait until the page (and its API calls) settled."""
        self._ensure()
        target = self._abs(url)

        def run(attempt: int) -> dict:
            if self.limiter is not None:
                self.limiter.acquire(target, sleeper=self._sleep)
            try:
                resp = self.page.goto(target, wait_until="domcontentloaded",
                                      timeout=timeout_ms or self.profile.navigation_timeout_ms)
            finally:
                if self.limiter is not None:
                    self.limiter.release(target)
            self.recorder.touch_activity()
            status = resp.status if resp is not None else None
            headers = dict(resp.headers) if resp is not None else {}
            retry_after = self.limiter.note_response(target, status, headers) if self.limiter is not None else None
            if status == 429:
                raise HttpStatusError(status, target, retry_after)
            if status in (500, 502, 503, 504):
                if self.hitl.mode != "off":
                    info = detect_block(self.page, status=status, headers=headers)
                    if info is not None and info.get("kind") != "rate_limited":
                        return {"status": status}
                raise HttpStatusError(status, target, retry_after)
            out = {"status": status}
            if status is not None and status >= 400:
                out["warning"] = f"HTTP {status}"
            return out

        return self._act("goto", target, run, expect=expect, wait=wait, retry=retry, navigation=True)

    def back(self, **kw: Any) -> dict:
        return self._act("back", "", lambda a: {"status": _status(self.page.go_back())}, navigation=True, **kw)

    def forward(self, **kw: Any) -> dict:
        return self._act("forward", "", lambda a: {"status": _status(self.page.go_forward())}, navigation=True, **kw)

    def reload(self, **kw: Any) -> dict:
        return self._act("reload", self.page.url if self.page else "", lambda a: {"status": _status(self.page.reload())},
                         navigation=True, **kw)

    # ═══ interaction ═════════════════════════════════════════════════
    def click(self, target: Any, *, expect: Any = None, wait: Any = "auto", timeout_ms: Optional[int] = None,
              button: str = "left", double: bool = False, force: bool = False, retry: Any = None) -> dict:
        """Click by ref (``"e12"``), selector or plain language (``"Add to cart"``)."""
        def run(attempt: int) -> dict:
            r, notes = self._robust_click(target, timeout_ms or self.profile.action_timeout_ms, button=button,
                                          click_count=2 if double else 1, force=force)
            self.recorder.touch_activity()
            out = {"resolved": r.to_dict()}
            if notes:
                out["recovered_in_attempt"] = notes
            return out
        return self._act("click", target, run, expect=expect, wait=wait, retry=retry)

    def _robust_click(self, target: Any, timeout_ms: float, **click_kw: Any) -> tuple:
        """Resolve + click in short slices: re-resolves re-rendered (detached)
        elements at once and clears overlays that cover the target, instead of
        burning the whole timeout before the retry policy kicks in."""
        deadline = mono_ms() + timeout_ms
        notes: list = []
        last_exc: Optional[BaseException] = None
        while True:
            r = self._resolve(target, purpose="click")
            try:
                try:
                    r.locator.scroll_into_view_if_needed(timeout=1500)
                except Exception:  # noqa: BLE001
                    pass
                hit = r.locator.evaluate(HIT_TEST_JS) if not click_kw.get("force") else {}
                if hit.get("covered"):
                    dismissed = self.dismiss_overlays()
                    notes.append(f"target covered by {hit.get('by')}; dismissed {dismissed or 'via Escape'}")
                remaining = deadline - mono_ms()
                r.locator.click(timeout=max(300.0, min(1000.0, remaining)), **click_kw)
                return r, notes
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                kind = classify_error(exc)
                if mono_ms() >= deadline or kind not in ("timeout", "detached", "intercepted"):
                    raise
                try:
                    gone = r.locator.count() == 0
                except Exception:  # noqa: BLE001
                    gone = True
                if gone:
                    notes.append("element re-rendered (detached); re-resolved")
                    self._observed.clear()
                elif kind == "intercepted":
                    self.dismiss_overlays()
                    notes.append("click intercepted; dismissed overlay")
                self.recorder.pump(80, self.page)

    def fill(self, target: Any, text: str, *, submit: bool = False, commit: Any = "auto", expect: Any = None,
             wait: Any = "auto", retry: Any = None) -> dict:
        """Type into a field found by label/placeholder/name/ref (replaces content).

        ``commit`` leaves the field afterwards like a person moving on, so ``change``
        listeners run (quantity boxes, filters, blur validation).  ``"auto"`` skips
        that for search/autocomplete boxes, whose suggestion lists a blur would close.
        """
        secret = bool(re.search(r"pass|secret|token|pin|cvv|card", str(target), re.I))
        args = {"text": "***" if secret else truncate(text, 60), "submit": submit}

        def run(attempt: int) -> dict:
            r = self._resolve(target, purpose="fill")
            if r.element is not None and (r.element.type == "password" or
                                          re.search(r"pass|secret|token|card", r.element.name or "", re.I)):
                args["text"] = "***"
            elif r.element is None:
                try:
                    if r.locator.evaluate("el => el.type") == "password":
                        args["text"] = "***"
                except Exception:  # noqa: BLE001
                    args["text"] = "***"
            r.locator.fill(str(text), timeout=self.profile.action_timeout_ms)
            if r.element is not None:
                self._last_form = next((a for a in r.element.ancestry if a.startswith("form")), None)
            committed = False
            if submit:
                r.locator.press("Enter")
            elif commit and (commit != "auto" or r.locator.evaluate(COMMIT_OK_JS)):
                r.locator.evaluate("el => el.blur()")
                committed = True
            self.recorder.touch_activity()
            return {"resolved": r.to_dict(), **({"committed": True} if committed else {})}
        return self._act("fill", target, run, expect=expect, wait=wait, retry=retry, args=args, navigation=submit)

    def type(self, target: Any, text: str, *, delay_ms: int = 35, submit: bool = False, **kw: Any) -> dict:
        def run(attempt: int) -> dict:
            r = self._resolve(target, purpose="type")
            r.locator.click(timeout=self.profile.action_timeout_ms)
            r.locator.press_sequentially(str(text), delay=delay_ms)
            if submit:
                r.locator.press("Enter")
            self.recorder.touch_activity()
            return {"resolved": r.to_dict()}
        return self._act("type", target, run, args={"text": truncate(text, 60)}, navigation=submit, **kw)

    def press(self, key: str, target: Any = None, **kw: Any) -> dict:
        def run(attempt: int) -> dict:
            if target is not None:
                r = self._resolve(target, purpose="fill")
                r.locator.press(key)
                self.recorder.touch_activity()
                return {"resolved": r.to_dict()}
            self.page.keyboard.press(key)
            self.recorder.touch_activity()
            return {}
        return self._act("press", key if target is None else f"{key} @ {target}", run, **kw)

    def select(self, target: Any, value: Any = None, *, label: Optional[str] = None, index: Optional[int] = None,
               **kw: Any) -> dict:
        def run(attempt: int) -> dict:
            r = self._resolve(target, purpose="select")
            tag = r.element.tag if r.element else r.locator.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                if label is not None:
                    chosen = r.locator.select_option(label=str(label))
                elif index is not None:
                    chosen = r.locator.select_option(index=int(index))
                else:
                    try:
                        chosen = r.locator.select_option(value=str(value))
                    except Exception:  # noqa: BLE001 - fall back to visible text
                        chosen = r.locator.select_option(label=str(value))
            else:
                r.locator.click()
                opt = self._resolve(str(label or value), purpose="click")
                opt.locator.click()
                chosen = [str(label or value)]
            self.recorder.touch_activity()
            return {"resolved": r.to_dict(), "selected": chosen}
        return self._act("select", target, run, args={"value": value, "label": label, "index": index}, **kw)

    def check(self, target: Any, checked: bool = True, **kw: Any) -> dict:
        def run(attempt: int) -> dict:
            r = self._resolve(target, purpose="check")
            if checked:
                r.locator.check(timeout=self.profile.action_timeout_ms)
            else:
                r.locator.uncheck(timeout=self.profile.action_timeout_ms)
            self.recorder.touch_activity()
            return {"resolved": r.to_dict(), "checked": checked}
        return self._act("check" if checked else "uncheck", target, run, **kw)

    def uncheck(self, target: Any, **kw: Any) -> dict:
        return self.check(target, False, **kw)

    def hover(self, target: Any, **kw: Any) -> dict:
        def run(attempt: int) -> dict:
            r = self._resolve(target, purpose="click")
            r.locator.hover(timeout=self.profile.action_timeout_ms)
            return {"resolved": r.to_dict()}
        return self._act("hover", target, run, **kw)

    def upload(self, target: Any, files: Any, **kw: Any) -> dict:
        paths = [str(Path(f)) for f in (files if isinstance(files, (list, tuple)) else [files])]

        def run(attempt: int) -> dict:
            r = self._resolve(target, purpose="upload")
            r.locator.set_input_files(paths)
            self.recorder.touch_activity()
            return {"resolved": r.to_dict(), "files": paths}
        return self._act("upload", target, run, **kw)

    def scroll(self, direction: str = "down", *, amount: Optional[int] = None, to: Optional[str] = None,
               target: Any = None, wait: Any = "auto") -> dict:
        """Scroll the page (``to="bottom"`` triggers infinite scroll loaders)."""
        def run(attempt: int) -> dict:
            before = self.page.evaluate("() => [scrollY, document.documentElement.scrollHeight]")
            if target is not None:
                r = self._resolve(target, purpose="click")
                r.locator.scroll_into_view_if_needed()
            elif to == "bottom":
                self.page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
            elif to == "top":
                self.page.evaluate("() => window.scrollTo(0, 0)")
            else:
                dy = amount or 800
                self.page.mouse.wheel(0, dy if direction == "down" else -dy)
            self.recorder.touch_activity()
            return {"scroll_before": {"y": before[0], "height": before[1]}}
        res = self._act("scroll", to or direction if target is None else str(target), run, wait=wait)
        try:
            y, h = self.page.evaluate("() => [scrollY, document.documentElement.scrollHeight]")
            res["scroll_after"] = {"y": y, "height": h}
            res["grew"] = h > res.get("scroll_before", {}).get("height", h)
        except Exception:  # noqa: BLE001
            pass
        return res

    def download(self, target: Any, *, timeout_ms: int = 30_000, **kw: Any) -> dict:
        """Click something that downloads a file; returns file + metadata."""
        def run(attempt: int) -> dict:
            r = self._resolve(target, purpose="click")
            with self.page.expect_download(timeout=timeout_ms):
                r.locator.click(timeout=self.profile.action_timeout_ms)
            self.recorder.touch_activity()
            return {"resolved": r.to_dict()}
        return self._act("download", target, run, **kw)

    def submit(self, target: Any = None, **kw: Any) -> dict:
        """Submit a form (press Enter in ``target`` or click its submit button)."""
        if target is None:
            return self.press("Enter", **kw)
        return self.click(target, **kw)

    def wait_for(self, condition: Any = None, *, timeout_ms: float = 15_000, **kw: Any) -> dict:
        """Wait for text/selector/url/response/request/function/idle/…; ``response``/``request``
        conditions also count traffic caused by the previous action (it may already be there)."""
        self._ensure()
        if "since_seq" not in kw and not (isinstance(condition, dict) and "since_seq" in condition) \
                and self._last_mark is not None:
            kw["since_seq"] = self._last_mark
        return wait_for_condition(self, condition, timeout_ms=timeout_ms, **kw)

    def settle(self, **kw: Any) -> dict:
        self._ensure()
        return settle_page(self.page, self.recorder, quiet_ms=kw.get("quiet_ms", self.settle_quiet_ms),
                           max_wait_ms=kw.get("max_wait_ms", self.max_settle_ms))

    # ═══ observation (Parts 6 & 15) ══════════════════════════════════
    def observe(self, *, limit: int = 150, frames: bool = True, include_hidden: bool = False,
                text_chars: int = 800) -> dict:
        """What the page offers right now: interactive elements with refs."""
        self._ensure()
        els = self._observe_raw(frames=frames, include_hidden=include_hidden)
        try:
            text = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:  # noqa: BLE001
            text = ""
        out = {"url": self.page.url, "title": self.page.title(), "page_id": self.recorder.page_id(self.page),
               "elements": [e.to_dict() for e in els[:limit]], "element_count": len(els),
               "text": re.sub(r"\n{3,}", "\n\n", text)[:text_chars]}
        if len(els) > limit:
            out["truncated"] = f"{len(els) - limit} more elements (raise limit=)"
        open_pages = self.recorder.open_pages()
        if len(open_pages) > 1:
            out["tabs"] = [{"page_id": self.recorder.page_id(p), "url": p.url} for p in open_pages]
        loading = loading_indicators(self.page)
        if loading:
            out["loading"] = loading
        return out

    def find(self, target: Any) -> dict:
        self._ensure()
        return self._resolve(target).to_dict()

    def inspect(self, *, cookie_values: bool = False, storage_values: bool = False) -> dict:
        """Structured browser + page state snapshot (Part 15)."""
        self._ensure()
        page = self.page
        try:
            state = page.evaluate(INSPECT_JS) or {}
        except Exception as exc:  # noqa: BLE001
            state = {"error": str(exc)}
        cookies = []
        for c in self.context.cookies():
            item = {k: c.get(k) for k in ("name", "domain", "path", "httpOnly", "secure", "sameSite", "expires")}
            item["value" if cookie_values else "value_length"] = c.get("value") if cookie_values else len(c.get("value", ""))
            cookies.append(item)
        if storage_values:
            try:
                state["local_storage"] = page.evaluate("() => Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)]))")
                state["session_storage"] = page.evaluate("() => Object.fromEntries(Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)]))")
            except Exception:  # noqa: BLE001
                pass
        els = self._observe_raw()
        frames = [dict(f) for f in self.recorder.frames.values() if f["page_id"] == self.recorder.page_id(page)]
        tabs = []
        for pid, info in self.recorder.pages.items():
            p = self.recorder.page_by_id(pid)
            tabs.append({"page_id": pid, "url": p.url if p and not p.is_closed() else None, "opener": info["opener"],
                         "closed": info["closed"] is not None, "active": p is page})
        seg = self.recorder.current_segment(page) or {}
        return {"url": page.url, "title": page.title(), "active_page": self.recorder.page_id(page),
                "navigation": {"segment": seg.get("id"), "kind": seg.get("kind"), "history": self.recorder.pages.get(
                    self.recorder.page_id(page), {}).get("urls", [])[-10:]},
                "tabs": tabs, "frames": frames, "viewport": page.viewport_size, "cookies": cookies,
                "elements": [e.to_dict() for e in els[:120]], "element_count": len(els),
                "blocked": detect_block(page), **state}

    def text(self, target: Any = None) -> str:
        self._ensure()
        if target is None:
            return self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        return self._resolve(target).locator.inner_text()

    def html(self) -> str:
        self._ensure()
        return self.page.content()

    def evaluate(self, script: str, arg: Any = None) -> Any:
        self._ensure()
        return self.page.evaluate(script, arg) if arg is not None else self.page.evaluate(script)

    def screenshot(self, path: Any = None, *, full_page: bool = False, target: Any = None) -> str:
        self._ensure()
        path = Path(path) if path else self.out_dir / "screenshots" / f"{int(time.time() * 1000)}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if target is not None:
            self._resolve(target).locator.screenshot(path=str(path))
        else:
            self.page.screenshot(path=str(path), full_page=full_page)
        return str(path)

    # ═══ data extraction helpers ═════════════════════════════════════
    def extract(self, fields: Optional[dict] = None, *, item: Optional[str] = None, limit: Optional[int] = None) -> Any:
        """Extract structured data with CSS specs: ``"h3 a@title"``, ``".price"``, ``"a@href"``.

        With ``item`` → list of dicts (one per matching element); without → one dict.
        """
        self._ensure()
        return self.page.evaluate(EXTRACT_JS, {"fields": fields or {}, "item": item, "limit": limit})

    def extract_list(self, *, item: Optional[str] = None, fields: Optional[dict] = None,
                     limit: Optional[int] = None) -> dict:
        """Extract a repeated list (auto-detects item selector + fields if omitted)."""
        self._ensure()
        return extract_items(self, item=item, fields=fields, limit=limit)

    def detect_items(self, top: int = 3) -> list:
        return detect_items(self, top=top)

    def detect_pagination(self) -> dict:
        return detect_pagination(self)

    def paginate(self, **kw: Any) -> dict:
        """Follow next-links / load-more / infinite scroll and collect all items."""
        return paginate(self, **kw)

    def capture(self, url: str, **kw: Any) -> dict:
        """One-shot *verified* page capture (waits for slow/delayed APIs, retries
        premature captures) → returns the HAR path immediately."""
        self._ensure()
        return capture_url(self, url, **kw)

    def jsonld(self) -> list:
        self._ensure()
        raw = self.page.evaluate("() => Array.from(document.querySelectorAll('script[type=\"application/ld+json\"]')).map(s => s.textContent)")
        out = []
        for text in raw or []:
            try:
                data = json.loads(text)
                out.extend(data if isinstance(data, list) else [data])
            except ValueError:
                continue
        return out

    def hydration_data(self) -> dict:
        """Framework state blobs (``__NEXT_DATA__``, ``__NUXT__``, …) — often the full page data."""
        self._ensure()
        return self.page.evaluate("""() => { const out = {};
          const nd = document.getElementById('__NEXT_DATA__'); if (nd) { try { out.__NEXT_DATA__ = JSON.parse(nd.textContent); } catch (e) {} }
          for (const k of ['__NUXT__', '__INITIAL_STATE__', '__APOLLO_STATE__', '__PRELOADED_STATE__']) {
            try { if (window[k] !== undefined) out[k] = JSON.parse(JSON.stringify(window[k])); } catch (e) {} }
          return out; }""") or {}

    # ═══ network access ══════════════════════════════════════════════
    def requests(self, pattern: str = "", *, method: Optional[str] = None, status: Optional[int] = None,
                 category: Optional[str] = None, action_id: Optional[str] = None, include_noise: bool = False,
                 limit: int = 100) -> list:
        """Compact summaries of captured requests (noise hidden by default)."""
        self._ensure()
        recs = self.recorder.find(pattern, method=method, status=status, action_id=action_id)
        if category:
            recs = [r for r in recs if r.category == category]
        elif not include_noise:
            recs = [r for r in recs if r.category in ("api", "document", "other")]
        return [r.summary() for r in recs[-limit:]]

    def records(self, pattern: str = "", **kw: Any) -> list:
        self._ensure()
        return self.recorder.find(pattern, **kw)

    def streams(self) -> list:
        """SSE / chunked streams captured so far: every event/chunk with sequence + timestamp."""
        return self.recorder.streams() if self.recorder else []

    def websockets(self) -> list:
        """WebSocket connections: handshake, lifecycle and every message (direction, ts, seq)."""
        return list(self.recorder.websockets) if self.recorder else []

    def fetch(self, url: str, *, method: str = "GET", params: Optional[dict] = None, json_body: Any = None,
              data: Any = None, headers: Optional[dict] = None, expect: Any = None, retry: Any = None,
              timeout_ms: Optional[int] = None, accept_status: Iterable[int] = ()) -> dict:
        """Call a URL/API with the *browser's* cookies, headers, proxy and politeness (logged-in API
        scraping after ``recon`` found the JSON endpoint).  Recorded in the HAR like page traffic.
        Returns the action result plus ``status``, ``json`` (or ``text``) and ``headers``; HTTP errors
        make the action fail (``ok: False``) unless listed in ``accept_status`` (e.g. ``[404]``)."""
        self._ensure()
        target = self._abs(url)
        if params:
            target += ("&" if "?" in target else "?") + urlencode(params, doseq=True)
        hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
        body: Any = None
        if json_body is not None:
            body = json.dumps(json_body)
            hdrs.setdefault("content-type", "application/json")
        elif data is not None:
            body = data if isinstance(data, (str, bytes)) else urlencode(data, doseq=True)
            if not isinstance(data, (str, bytes)):
                hdrs.setdefault("content-type", "application/x-www-form-urlencoded")
        holder: dict = {}

        def run(attempt: int) -> dict:
            self.limiter.acquire(target, sleeper=self._sleep)
            started, t0 = iso(), mono_ms()
            status, rh, raw, failure = None, {}, None, None
            try:
                resp = self._api_request(target, method, hdrs, body, timeout_ms or self.profile.navigation_timeout_ms)
                status, rh, raw = resp.status, dict(resp.headers), resp.body()
                status_text = resp.status_text
            except Exception as exc:  # noqa: BLE001 - recorded + classified below
                failure, status_text = str(exc).splitlines()[0][:300], ""
            finally:
                self.limiter.release(target)
            retry_after = self.limiter.note_response(target, status, rh) if status else None
            req_body = body.encode() if isinstance(body, str) else body
            holder["rec"] = self.recorder.add_manual(method=method, url=target, request_headers=hdrs,
                                                     request_body=req_body, status=status, status_text=status_text,
                                                     response_headers=rh, response_body=raw, started=started,
                                                     t_start=t0, t_end=mono_ms(), failure=failure, page=self.page)
            if failure:
                raise NavigationError(f"fetch {method} {target} failed: {failure}",
                                      hint="check the URL; for logged-in APIs save/restore state first")
            if status is not None and status >= 400 and status not in accepted:
                raise HttpStatusError(status, target, retry_after)
            return {"status": status}
        accepted = {int(x) for x in accept_status}
        summary = self._act("fetch", target, run, expect=expect, wait=None, retry=retry, args={"method": method.upper()})
        rec = holder.get("rec")
        if rec is not None:
            summary["status"] = rec.status
            summary["headers"] = {h["name"].lower(): h["value"] for h in rec.response_headers}
            data_ = rec.json() if rec.response_body is not None else None
            if data_ is not None:
                summary["json"] = data_
            elif rec.response_body is not None:
                summary["text"] = rec.text()
        return summary

    def _api_request(self, url: str, method: str, headers: dict, body: Any, timeout_ms: float):
        """Playwright's APIRequestContext of this browser context (shares cookies + proxy);
        honours host_map because that request stack resolves DNS itself."""
        req = self.context.request
        try:
            return req.fetch(url, method=method.upper(), headers=headers, data=body, timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            mapped = self._mapped_url(url)
            if mapped is None or not re.search(r"ENOTFOUND|getaddrinfo|EAI_AGAIN|ERR_NAME", str(exc)):
                raise
            cookie = "; ".join(f"{c['name']}={c['value']}" for c in self.context.cookies(url))
            extra = {"host": mapped[1], **({"cookie": cookie} if cookie else {})}
            return req.fetch(mapped[0], method=method.upper(), headers={**headers, **extra}, data=body,
                             timeout=timeout_ms)

    def integrity(self, pattern: str = "", *, problems_only: bool = False) -> list:
        """Body integrity of captured responses (complete? decodes as its type says?)."""
        recs = self.recorder.records if self.recorder else []
        out = [body_integrity(r) for r in recs if (not pattern or pattern in r.url)
               and (r.response_body is not None or r.failure or r.status is not None)]
        return [x for x in out if not x["ok"]] if problems_only else out

    def response_json(self, pattern: str, *, index: int = -1) -> Any:
        recs = [r for r in self.records(pattern) if r.response_body]
        if not recs:
            raise ActionError(f"no captured response with a body matches {pattern!r}",
                              hint="check requests() for the exact URL; bodies of images/scripts are not kept by default")
        return recs[index].json()

    def wait_for_response(self, pattern: str, *, status: Optional[int] = None, timeout_ms: float = 15_000) -> dict:
        res = self.wait_for({"response": pattern, **({"status": status} if status else {})}, timeout_ms=timeout_ms,
                            since_seq=0)
        recs = [r for r in self.records(pattern) if r.finished]
        return {**res, "response": recs[-1].summary() if recs else None}

    # ═══ interception / mocking (Part 43) ════════════════════════════
    def _route_matcher(self, pattern: str) -> Any:
        if pattern.startswith("re:"):
            return re.compile(pattern[3:])
        if "*" in pattern:
            return pattern
        return lambda url, p=pattern: p in url

    def _add_route(self, kind: str, pattern: str, handler: Callable, times: Optional[int], meta: dict) -> str:
        self._ensure()
        rule = {"id": f"route{len(self._routes) + 1}", "kind": kind, "pattern": pattern, "hits": [], **meta}
        matcher = self._route_matcher(pattern)

        def wrapped(route, request) -> None:
            if times is not None and len(rule["hits"]) >= times:
                route.fallback()
                return
            hit = {"ts": iso(), "method": request.method, "url": request.url, "action_id": self.recorder.current_action,
                   "request_headers": dict(request.headers), "post_data": request.post_data}
            try:
                handler(route, request, hit)
            except Exception as exc:  # noqa: BLE001
                hit["error"] = str(exc)
                try:
                    route.fallback()
                except Exception:  # noqa: BLE001
                    pass
            rule["hits"].append(hit)
            rec = self.recorder._by_req.get(request)
            note = {"action": kind, "rule": rule["id"], "pattern": pattern,
                    **{k: v for k, v in hit.items() if k in ("injected_response", "original_response", "modified_request")}}
            if rec is not None:
                rec.mocked = note
            else:
                self._annotations[request] = note
            self.events.emit("route", rule=rule["id"], route_kind=kind, url=request.url, action_id=hit["action_id"])

        rule["_handler"] = wrapped
        rule["_matcher"] = matcher
        self.context.route(matcher, wrapped)
        self._routes.append(rule)
        return rule["id"]

    def mock(self, pattern: str, *, status: int = 200, json_body: Any = None, body: Any = None,
             headers: Optional[dict] = None, content_type: Optional[str] = None, times: Optional[int] = None) -> str:
        """Answer matching requests with a fake response (the app uses it)."""
        if json_body is not None:
            payload = json.dumps(json_body)
            content_type = content_type or "application/json"
        else:
            payload = body if isinstance(body, (str, bytes)) else ("" if body is None else json.dumps(body))
        hdrs = dict(headers or {})

        def handler(route, request, hit) -> None:
            route.fulfill(status=status, headers=hdrs, body=payload, content_type=content_type)
            hit["injected_response"] = {"status": status, "headers": hdrs, "content_type": content_type,
                                        "body": payload if isinstance(payload, str) else f"<{len(payload)} bytes>"}
        return self._add_route("mock", pattern, handler, times, {"status": status})

    def block(self, pattern: str, *, error: str = "blockedbyclient", times: Optional[int] = None) -> str:
        def handler(route, request, hit) -> None:
            route.abort(error)
            hit["injected_response"] = {"aborted": error}
        return self._add_route("block", pattern, handler, times, {})

    def modify_request(self, pattern: str, *, headers: Optional[dict] = None, remove_headers: Iterable[str] = (),
                       method: Optional[str] = None, post_data: Any = None, url: Optional[str] = None,
                       times: Optional[int] = None) -> str:
        drop = {h.lower() for h in remove_headers}

        def handler(route, request, hit) -> None:
            new_headers = {k: v for k, v in request.headers.items() if k.lower() not in drop}
            new_headers.update(headers or {})
            kwargs: dict = {"headers": new_headers}
            if method:
                kwargs["method"] = method
            if post_data is not None:
                kwargs["post_data"] = post_data if isinstance(post_data, (str, bytes)) else json.dumps(post_data)
            if url:
                kwargs["url"] = url
            route.continue_(**kwargs)
            hit["modified_request"] = {k: (v if k != "post_data" else truncate(v, 300)) for k, v in kwargs.items()}
        return self._add_route("modify_request", pattern, handler, times, {})

    def _mapped_url(self, url: str) -> Optional[tuple]:
        """(url on the mapped address, original Host) when ``profile.host_map`` redirects this host."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        rules = list((self.profile.host_map or {}).items())
        if any(dest in (None, "", "EXCLUDE") and fnmatch(host, pat) for pat, dest in rules):
            return None
        dest = next((d for pat, d in rules if d not in (None, "", "EXCLUDE") and fnmatch(host, pat)), None)
        if not dest:
            return None
        netloc = dest if ":" in dest else f"{dest}:{parts.port or (443 if parts.scheme == 'https' else 80)}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")), parts.netloc

    def _fetch_real(self, route, request):
        """route.fetch() that also honours the browser's host_map (Playwright's fetch resolves DNS itself)."""
        try:
            return route.fetch()
        except Exception:  # noqa: BLE001
            mapped = self._mapped_url(request.url)
            if mapped is None:
                raise
            return route.fetch(url=mapped[0], headers={**request.headers, "host": mapped[1]})

    def modify_response(self, pattern: str, *, transform: Optional[Callable] = None, json_patch: Optional[dict] = None,
                        status: Optional[int] = None, headers: Optional[dict] = None, times: Optional[int] = None) -> str:
        """Fetch the real response, then change it (``transform(json_or_text) -> new``)."""
        def handler(route, request, hit) -> None:
            real = self._fetch_real(route, request)
            raw = real.body()
            text = raw.decode("utf-8", "replace")
            try:
                data: Any = json.loads(text)
                is_json = True
            except ValueError:
                data, is_json = text, False
            new = data
            if json_patch and isinstance(data, dict):
                new = {**data, **json_patch}
            if transform is not None:
                new = transform(new)
            new_body = json.dumps(new) if is_json or isinstance(new, (dict, list)) else str(new)
            hdrs = dict(real.headers)
            hdrs.pop("content-length", None)
            hdrs.pop("content-encoding", None)
            hdrs.update(headers or {})
            route.fulfill(status=status or real.status, headers=hdrs, body=new_body)
            hit["original_response"] = {"status": real.status, "headers": dict(real.headers), "size": len(raw),
                                        "sha256": sha256_hex(raw), "body": text if len(text) <= 500_000 else truncate(text, 500_000)}
            hit["injected_response"] = {"status": status or real.status, "size": len(new_body.encode()),
                                        "body": new_body if len(new_body) <= 500_000 else truncate(new_body, 500_000)}
        return self._add_route("modify_response", pattern, handler, times, {})

    def unroute(self, rule_id: Optional[str] = None) -> int:
        removed = 0
        for rule in list(self._routes):
            if rule_id is None or rule["id"] == rule_id:
                try:
                    self.context.unroute(rule["_matcher"], rule["_handler"])
                except Exception:  # noqa: BLE001
                    pass
                rule["removed"] = True
                removed += 1
        return removed

    def interceptions(self) -> list:
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in self._routes]

    # ═══ state (Parts 8 & 46) ════════════════════════════════════════
    def save_state(self, path: Any, *, session_storage: bool = True) -> dict:
        """Persist cookies + localStorage + IndexedDB (+ sessionStorage) to a file."""
        self._ensure()
        try:
            state = self.context.storage_state(indexed_db=True)
        except TypeError:  # older Playwright without IndexedDB support
            state = self.context.storage_state()
        if session_storage:
            ss: dict = {}
            for p in self.recorder.open_pages():
                try:
                    origin = p.evaluate("() => location.origin")
                    items = p.evaluate("() => Object.fromEntries(Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)]))")
                    if items and origin and origin != "null":
                        ss.setdefault(origin, {}).update(items)
                except Exception:  # noqa: BLE001
                    continue
            if ss:
                state["sessionStorage"] = ss
        state["agenttrace"] = {"saved_at": iso(), "session_id": self.id, "url": self.page.url}
        target = write_json(path, state)
        info = {"path": str(target), "cookies": len(state.get("cookies", [])),
                "cookie_names": sorted({c.get("name") for c in state.get("cookies", [])}),
                "origins": [o.get("origin") for o in state.get("origins", [])],
                "local_storage_keys": sum(len(o.get("localStorage", [])) for o in state.get("origins", [])),
                "indexed_db": sum(len(o.get("indexedDB", []) or []) for o in state.get("origins", [])),
                "session_storage_origins": sorted(state.get("sessionStorage", {}))}
        self.events.emit("state_saved", **{k: v for k, v in info.items() if k != "cookie_names"})
        self._jlog("info", "state_saved", f"saved state to {target}", **info)
        return info

    def storage(self, *, values: bool = False) -> dict:
        """Every client-side store of the current origin: cookies, localStorage, sessionStorage,
        IndexedDB (databases → stores → keys) and Cache Storage (values only when asked)."""
        self._ensure()
        try:
            snap = self.page.evaluate(STORAGE_JS, values) or {}
        except Exception as exc:  # noqa: BLE001
            snap = {"error": str(exc)}
        return {"cookies": self.cookies(values=values), **snap}

    def cookies(self, *, values: bool = False) -> list:
        self._ensure()
        return [{**{k: c.get(k) for k in ("name", "domain", "path", "httpOnly", "secure", "sameSite")},
                 **({"value": c.get("value")} if values else {"value_length": len(c.get("value", ""))})}
                for c in self.context.cookies()]

    # ═══ tabs / popups (Part 45) ═════════════════════════════════════
    def pages(self) -> list:
        self._ensure()
        out = []
        for pid, info in self.recorder.pages.items():
            p = self.recorder.page_by_id(pid)
            out.append({"page_id": pid, "url": (p.url if p and not p.is_closed() else None), "opener": info["opener"],
                        "created": info["created"], "closed": info["closed"], "active": p is self.page})
        return out

    def switch_to(self, which: str = "latest") -> dict:
        self._ensure()
        pages = self.recorder.open_pages()
        chosen = None
        if which == "latest":
            chosen = pages[-1] if pages else None
        elif which == "opener":
            try:
                chosen = self.page.opener()
            except Exception:  # noqa: BLE001
                chosen = None
        elif re.fullmatch(r"p\d+", which):
            chosen = self.recorder.page_by_id(which)
        else:
            chosen = next((p for p in pages if which in p.url), None)
        if chosen is None or chosen.is_closed():
            raise ActionError(f"no open page matches {which!r}", details={"pages": self.pages()})
        self.page = chosen
        self.recorder.wait_ready(chosen)
        try:
            chosen.bring_to_front()
            chosen.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        self._observed.clear()
        self.events.emit("switch_page", page_id=self.recorder.page_id(chosen), url=chosen.url)
        return {"page_id": self.recorder.page_id(chosen), "url": chosen.url, "title": chosen.title()}

    def close_page(self, page_id: Optional[str] = None) -> dict:
        self._ensure()
        p = self.recorder.page_by_id(page_id) if page_id else self.page
        if p is None:
            raise ActionError(f"unknown page {page_id!r}")
        pid = self.recorder.page_id(p)
        p.close()
        self.recorder.pump(100, None)
        self._ensure()
        return {"closed": pid, "active": self.recorder.page_id(self.page)}

    # ═══ blocking (Part 37) ══════════════════════════════════════════
    def detect_block(self) -> Optional[dict]:
        self._ensure()
        return detect_block(self.page)

    def wait_for_human(self, reason: str = "manual step", *, timeout_s: Optional[float] = None) -> dict:
        info = {"kind": "manual", "reason": reason, "url": self.page.url}
        hitl = HumanInTheLoop(**{**self.hitl.__dict__, "timeout_s": timeout_s or self.hitl.timeout_s})
        return hitl.handle(self, info)

    # ═══ outputs ═════════════════════════════════════════════════════
    def _har_for(self, records: Iterable[NetRecord], *, redact: Optional[bool] = None,
                 websockets: Iterable[dict] = ()) -> dict:
        recs = list(records)
        browser = {"name": self.profile.browser, "version": self.engine.version if self.engine else ""}
        har = self.recorder.har(recs, browser=browser, websockets=list(websockets))
        if (self.redact if redact is None else redact):
            har = Redactor().learn_records(self.recorder.records).redact_har(har)
        return har

    def har(self, *, action_id: Optional[str] = None, clean: bool = False, redact: Optional[bool] = None) -> dict:
        self._ensure()
        recs = self.recorder.for_action(action_id) if action_id else list(self.recorder.records)
        if clean:
            recs = filter_records(recs, self.noise_rules).kept
        ws = [w for w in self.recorder.websockets if not action_id or w.get("action_id") == action_id]
        return self._har_for(recs, redact=redact, websockets=ws)

    def save_har(self, path: Any, **kw: Any) -> str:
        return str(save_har(path, self.har(**kw)))

    def endpoints(self) -> list:
        self._ensure()
        return discover_endpoints(self.recorder.records)

    # ═══ SPA routes / virtual pages (Part 36) ═══════════════════════
    def routes(self) -> list:
        """Every page load *and* client-side route change (virtual page), in order, with its request counts."""
        if self.recorder is None:
            return []
        by_seg: dict = {}
        for r in self.recorder.records:
            by_seg.setdefault(r.segment_id, []).append(r)
        out = []
        for seg in self.recorder.segments:
            recs = by_seg.get(seg["id"], [])
            out.append({"id": seg["id"], "url": seg["url"], "title": seg.get("title") or "", "kind": seg["kind"],
                        "virtual": bool(seg.get("virtual")), "page_id": seg.get("page_id"),
                        "started": seg["startedDateTime"], "action_id": seg.get("action_id"), "requests": len(recs),
                        "documents": sum(1 for r in recs if r.is_navigation and (r.frame_id or "").endswith(".f0")),
                        "api_calls": sum(1 for r in recs if r.category == "api")})
        return out

    def save_route_hars(self, out_dir: Any) -> list:
        """Write one HAR per page/route segment (+ ``index.json``); SPA routes get their own HAR."""
        folder = Path(out_dir)
        recs = list(self.recorder.records) if self.recorder else []
        browser = {"name": self.profile.browser, "version": self.engine.version if self.engine else ""}
        segs = {s["id"]: s for s in (self.recorder.segments if self.recorder else [])}
        index = []
        for i, route in enumerate(self.routes(), start=1):
            seg = segs[route["id"]]
            page = {"id": seg["id"], "url": seg["url"], "title": seg.get("title") or seg["url"],
                    "startedDateTime": seg["startedDateTime"], "virtual": seg.get("virtual"), "page_id": seg.get("page_id")}
            har = build_har([r for r in recs if r.segment_id == seg["id"]], pages=[page], browser=browser,
                            websockets=[w for w in self.recorder.websockets if w.get("segment_id") == seg["id"]])
            if self.redact:
                har = Redactor().learn_records(recs).redact_har(har)
            path_part = slugify(re.sub(r"^https?://[^/]+", "", seg["url"]).split("?")[0].strip("/#") or "root", 40, "root")
            index.append({**route, "har": str(save_har(folder / f"{i:02d}_{seg['id']}_{path_part}.har", har))})
        write_json(folder / "index.json", index)
        return index

    def actions(self) -> list:
        return [{k: v for k, v in a.items() if not k.startswith("_")} for a in self._actions]

    def report(self) -> dict:
        """AI-friendly structured report (Part 26) — no raw HAR parsing needed."""
        recs = list(self.recorder.records) if self.recorder else []
        nrep = noise_report(recs, self.noise_rules) if recs else {}
        actions = self.actions()
        errors = [c for c in (self.recorder.console if self.recorder else []) if c["kind"] in ("pageerror", "crash")
                  or c.get("level") == "error"]
        return {
            "session": {"id": self.id, "name": self.name, "out_dir": str(self.out_dir),
                        "browser": self.engine.launch_info if self.engine else {},
                        "duration_s": round(time.monotonic() - self._t0, 2)},
            "summary": {"actions": len(actions), "ok": sum(1 for a in actions if a["ok"]),
                        "failed": sum(1 for a in actions if not a["ok"]),
                        "pages_visited": sorted({s["url"] for s in (self.recorder.segments if self.recorder else [])})[:50],
                        "requests": len(recs), "api_calls": sum(1 for r in recs if r.category == "api"),
                        "noise_filtered": nrep.get("dropped", 0), "failed_requests": nrep.get("failed", 0),
                        "console_errors": len(errors), "downloads": len(self.recorder.downloads) if self.recorder else 0,
                        "integrity_problems": sum(1 for r in recs if r.response_body is not None and not body_integrity(r)["ok"]),
                        "blocked_events": len(self.events.events("blocked"))},
            "actions": actions,
            "noise": {k: nrep.get(k) for k in ("by_category", "noise_vendors", "duplicates_removed")},
            **({"interceptions": [{"rule": r["id"], "kind": r["kind"], "pattern": r["pattern"], "hits": len(r["hits"]),
                                   "requests": [_hit_brief(h) for h in r["hits"][:10]]} for r in self._routes]}
               if self._routes else {}),
            "console_errors": [{k: c.get(k) for k in ("ts", "kind", "text", "action_id", "page_id")} for c in errors[:30]],
        }

    def finish(self) -> dict:
        """Close the browser and write every artifact. Returns paths + stats."""
        if self._finished:
            return self._summary or {}
        if not self._started:
            self._finished = True
            return {}
        out = self.out_dir
        paths: dict = {}
        try:
            if self.recorder.pending_downloads():
                self.recorder.save_downloads(out / "downloads")
            if self.page is not None and not self.page.is_closed():
                try:
                    self.recorder.set_segment_title(self.page, self.page.title())
                except Exception:  # noqa: BLE001
                    pass
            if self.trace:
                try:
                    self.context.tracing.stop(path=str(out / "trace.zip"))
                    paths["trace"] = str(out / "trace.zip")
                except Exception as exc:  # noqa: BLE001
                    self._log.warning("could not save trace: %s", exc)
        finally:
            for request, note in list(self._annotations.items()):
                rec = self.recorder._by_req.get(request)
                if rec is not None and rec.mocked is None:
                    rec.mocked = note
            try:
                self.context.close()
            except Exception:  # noqa: BLE001
                pass
            self.recorder.detach()
        recs = list(self.recorder.records)
        wss = list(self.recorder.websockets)
        paths["har"] = str(save_har(out / "session.har", self._har_for(recs, websockets=wss)))
        filt = filter_records(recs, self.noise_rules)
        paths["clean_har"] = str(save_har(out / "clean.har", self._har_for(filt.kept, websockets=wss)))
        if self.split_har:
            for a in self._actions:
                arecs = [r for r in recs if r.action_id == a["id"]]
                aws = [w for w in wss if w.get("action_id") == a["id"]]
                if arecs or aws:
                    p = save_har(out / "actions" / f"{a['id']}_{a['action']}.har", self._har_for(arecs, websockets=aws))
                    a["har"] = str(p)
            if len(self.recorder.segments) > 1:
                self.save_route_hars(out / "routes")
                paths["routes"] = str(out / "routes" / "index.json")
        eps = discover_endpoints(recs)
        paths["endpoints"] = str(write_json(out / "endpoints.json", eps))
        write_text(out / "endpoints.md", endpoints_markdown(eps))
        paths["console"] = str(write_json(out / "console.json", {"console": self.recorder.console,
                                                                 "dialogs": self.recorder.dialogs}))
        if self.recorder.downloads:
            paths["downloads"] = str(write_json(out / "downloads" / "downloads.json", self.recorder.downloads))
        streams = self.recorder.streams()
        if streams:
            paths["streams"] = str(write_json(out / "streams.json", streams))
        if self.recorder.websockets:
            paths["websockets"] = str(write_json(out / "websockets.json", self.recorder.websockets))
        paths["pages"] = str(write_json(out / "pages.json", self.recorder.pages_report()))
        if self._routes:
            paths["interceptions"] = str(write_json(out / "interceptions.json", self.interceptions()))
        paths["timeline"] = str(self.events.write_jsonl(out / "timeline.jsonl"))
        report = self.report()
        report["endpoints"] = [{k: e[k] for k in ("method", "url_template", "kind", "count", "statuses")} for e in eps]
        report["artifacts"] = paths
        paths["report_json"] = str(write_json(out / "report.json", report))
        paths["report_md"] = str(write_text(out / "report.md", session_markdown(report, self.events.events())))
        self.hooks.emit("output", session=self, paths=paths, report=report)
        manifest = {"session_id": self.id, "created": iso(), "artifacts": []}
        for f in sorted(out.rglob("*")):
            if f.is_file() and f.name not in ("manifest.json",) and not f.name.startswith("."):
                data = f.read_bytes()
                manifest["artifacts"].append({"path": str(f.relative_to(out)).replace("\\", "/"), "bytes": len(data),
                                              "sha256": sha256_hex(data)})
        paths["manifest"] = str(write_json(out / "manifest.json", manifest))
        self.events.emit("session_end", session_id=self.id)
        self.hooks.emit("session_end", session=self, paths=paths)
        if self._own_engine and self.engine is not None:
            self.engine.close()
        self._finished = True
        self._summary = {"session_id": self.id, "out_dir": str(out), "paths": paths, "summary": report["summary"]}
        durations = sorted(a.get("duration_ms") or 0 for a in self._actions)
        metrics = {**report["summary"], "pages_visited": len(report["summary"].get("pages_visited") or [])}
        self._jlog("info", "session_metrics", "execution metrics", **metrics,
                   retries=sum(max(0, (a.get("attempts") or 1) - 1) for a in self._actions),
                   action_ms_p50=durations[len(durations) // 2] if durations else 0,
                   action_ms_p95=durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0,
                   body_bytes=self.recorder.stats()["body_bytes"], duration_s=report["session"]["duration_s"],
                   hook_errors=len(self.hooks.errors))
        self._jlog("info", "session_end", f"session finished -> {out}")
        return self._summary

    def close(self) -> None:
        """Best-effort teardown without writing artifacts (use finish() normally)."""
        if self._finished:
            return
        try:
            if self.context is not None:
                self.context.close()
        except Exception:  # noqa: BLE001
            pass
        if self._own_engine and self.engine is not None:
            self.engine.close()
        self._finished = True


def _hit_brief(hit: dict) -> dict:
    """Interception hit for report.json: request + injected/original response, bodies shortened."""
    out = {k: hit.get(k) for k in ("ts", "method", "url", "action_id", "modified_request") if hit.get(k)}
    for key in ("injected_response", "original_response"):
        resp = hit.get(key)
        if resp:
            out[key] = {k: (truncate(v, 300) if k == "body" and isinstance(v, str) else v)
                        for k, v in resp.items() if k != "headers"}
    return out


def _status(resp: Any) -> Optional[int]:
    return resp.status if resp is not None else None


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _default_hint(kind: str) -> str:
    return {
        "not_found": "call observe() to see available elements and use a ref, or quote the exact visible text",
        "intercepted": "something covers the element - try dismiss_overlays() or scroll into view",
        "timeout": "the page is slow - raise timeout_ms or wait_for() a specific condition first",
        "network": "network error - check the URL/proxy; the site may be down or blocking you",
        "http_5xx": "server error persisted after retries - try later or reduce request rate",
        "rate_limited": "the site is rate limiting - use rate_limit={'min_interval_s': 2}",
        "expectation": "the expected request/response did not happen - inspect requests() for what did",
        "detached": "the element was re-rendered - retry, or wait_for() the UI to stabilise first",
    }.get(kind, "inspect the page with observe() / inspect() and the action's network summary")


def session_markdown(report: dict, events: list) -> str:
    s = report["summary"]
    lines = [f"# AgentTrace session report — {report['session']['name']}", "",
             f"- session: `{report['session']['id']}`  ·  duration: {report['session']['duration_s']}s  ·  out: `{report['session']['out_dir']}`",
             f"- actions: {s['actions']} ({s['ok']} ok, {s['failed']} failed) · requests: {s['requests']} "
             f"(api {s['api_calls']}, noise filtered {s['noise_filtered']}, failed {s['failed_requests']}) · "
             f"console errors: {s['console_errors']} · downloads: {s['downloads']}", "",
             "## Actions", "", "| # | action | target | start | ms | ok | primary request | error |",
             "|---|---|---|---|---|---|---|---|"]
    for a in report["actions"]:
        prim = (a.get("network") or {}).get("primary") or {}
        req = f"{prim.get('method', '')} {truncate(prim.get('url', ''), 60)} → {prim.get('status', '')}" if prim else ""
        lines.append(f"| {a['id']} | {a['action']} | {truncate(a['target'], 40)} | {a['started'][11:23]} | "
                     f"{a['duration_ms']} | {'✅' if a['ok'] else '❌'} | {req} | {truncate(a.get('error') or '', 60)} |")
    lines += ["", "## Timeline", "", "| t (ms) | time | event | action | detail |", "|---|---|---|---|---|"]
    keep = ("action_start", "action_end", "navigation", "route_change", "download_saved", "console", "retry", "blocked",
            "unblocked", "dialog", "page_opened", "page_closed", "guardrail", "action_error", "route", "state_saved")
    for e in events:
        if e.get("kind") not in keep:
            continue
        detail = {k: v for k, v in e.items() if k not in ("seq", "ts", "t_ms", "kind", "action_id", "level")}
        lines.append(f"| {e['t_ms']} | {e['ts'][11:23]} | {e['kind']} | {e.get('action_id', '')} | {truncate(json.dumps(detail, ensure_ascii=False), 110)} |")
    if report.get("endpoints"):
        lines += ["", "## API endpoints", ""]
        for ep in report["endpoints"][:40]:
            lines.append(f"- `{ep['method']} {ep['url_template']}` ({ep['kind']}, {ep['count']}×, {ep['statuses']})")
    if report.get("console_errors"):
        lines += ["", "## Console errors", ""]
        for c in report["console_errors"][:20]:
            lines.append(f"- [{c.get('action_id') or '-'}] {c['kind']}: {truncate(c['text'], 150)}")
    if report.get("artifacts"):
        lines += ["", "## Artifacts", ""]
        for k, v in report["artifacts"].items():
            lines.append(f"- {k}: `{v}`")
    return "\n".join(lines) + "\n"



# ════════════════════════════════════════════════════════════════════════════
# export.py — Postman / OpenAPI / Python client / curl export (Part 39)
# ════════════════════════════════════════════════════════════════════════════
# Structured API export (Part 39): Postman collection, OpenAPI spec, Python
# client stub, curl — straight from captured traffic, plus a Postman-semantics
# replayer used to prove the collection actually works.


_DROP_HEADERS = {"host", "connection", "content-length", "accept-encoding", "upgrade-insecure-requests", "te",
                 "pragma", "cache-control", "priority", "keep-alive", "proxy-connection", "origin", "if-none-match",
                 "if-modified-since"}


def _keep_header(name: str) -> bool:
    n = name.lower()
    return not (n in _DROP_HEADERS or n.startswith(("sec-", ":")))


def api_records(records: Iterable[NetRecord], *, include_documents: bool = False) -> list:
    recs = classify_records(list(records))
    out = []
    seen = set()
    for r in recs:
        if r.category in NOISE_CATEGORIES or r.category == "static":
            continue
        if r.category == "document" and not include_documents:
            continue
        if r.status is None:  # never answered (a body aborted after the headers still counts)
            continue
        key = (r.method, r.url, r.request_body or b"")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ═══════════════════════════════════════════════════════════════════════
# Postman collection v2.1
# ═══════════════════════════════════════════════════════════════════════
def to_postman(records: Iterable[NetRecord], *, name: str = "AgentTrace capture", include_cookies: bool = True,
               include_documents: bool = False) -> dict:
    recs = api_records(records, include_documents=include_documents)
    hosts: dict = {}
    folders: dict = {}
    for r in recs:
        parts = urlsplit(r.url)
        base = f"{parts.scheme}://{parts.netloc}"
        var = hosts.setdefault(base, f"baseUrl{'' if not hosts else len(hosts) + 1}")
        path_segs = [s for s in parts.path.split("/") if s]
        headers = [{"key": h["name"], "value": h["value"], "type": "text"} for h in r.request_headers
                   if _keep_header(h["name"]) and (include_cookies or h["name"].lower() != "cookie")]
        item_req: dict = {
            "method": r.method,
            "header": headers,
            "url": {"raw": f"{{{{{var}}}}}{parts.path}" + (f"?{parts.query}" if parts.query else ""),
                    "host": [f"{{{{{var}}}}}"], "path": path_segs,
                    **({"query": [{"key": k, "value": v} for k, v in parse_qsl(parts.query, keep_blank_values=True)]}
                       if parts.query else {})},
        }
        if r.request_body is not None:
            ct = r.request_content_type.lower()
            text = r.request_text() or ""
            if "x-www-form-urlencoded" in ct:
                item_req["body"] = {"mode": "urlencoded",
                                    "urlencoded": [{"key": k, "value": v, "type": "text"} for k, v in parse_qsl(text, keep_blank_values=True)]}
            elif "json" in ct:
                item_req["body"] = {"mode": "raw", "raw": text, "options": {"raw": {"language": "json"}}}
            else:
                item_req["body"] = {"mode": "raw", "raw": text}
        folder = path_segs[0] if path_segs else "root"
        test = [f"pm.test('status is {r.status}', function () {{ pm.response.to.have.status({r.status}); }});"]
        folders.setdefault(folder, []).append({
            "name": f"{r.method} {parts.path}" + (f" ({(r.request_json() or {}).get('operationName')})"
                                                  if isinstance(r.request_json(), dict) and (r.request_json() or {}).get("operationName") else ""),
            "request": item_req,
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": test}}],
            "_agenttrace": {"record_id": r.id, "status": r.status},
        })
    return {
        "info": {"name": name, "_postman_id": slugify(name + iso(), 36), "description": f"Generated by AgentTrace {__version__} from captured traffic",
                 "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "item": [{"name": f, "item": items} for f, items in folders.items()],
        "variable": [{"key": v, "value": base} for base, v in hosts.items()],
    }


def _pm_resolve(text: str, variables: dict) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), text or "")


def iter_postman_items(collection: dict) -> Iterable[dict]:
    for item in collection.get("item", []):
        if "item" in item:
            yield from iter_postman_items(item)
        else:
            yield item


def replay_postman(collection: dict, *, variables: Optional[dict] = None, client: Optional[HttpClient] = None,
                   proxy: Optional[str] = None) -> dict:
    """Replay every request of a collection (Postman semantics) → per-request results."""
    vars_ = {v["key"]: v["value"] for v in collection.get("variable", [])}
    vars_.update(variables or {})
    client = client or HttpClient(limiter=RateLimiter(min_interval_s=0.0), max_retries=2, proxy=proxy)
    results = []
    for item in iter_postman_items(collection):
        req = item["request"]
        url = _pm_resolve(req["url"]["raw"] if isinstance(req["url"], dict) else req["url"], vars_)
        headers = {h["key"]: _pm_resolve(h["value"], vars_) for h in req.get("header", []) if not h.get("disabled")}
        body = None
        b = req.get("body")
        if b:
            if b.get("mode") == "urlencoded":
                body = urlencode([(p["key"], _pm_resolve(p["value"], vars_)) for p in b["urlencoded"]]).encode()
            elif b.get("mode") == "raw":
                body = _pm_resolve(b.get("raw", ""), vars_).encode()
        expected = (item.get("_agenttrace") or {}).get("status")
        try:
            resp = client.request(req["method"], url, headers=headers, body=body)
            ok = resp.status == expected if expected else resp.ok
            results.append({"name": item["name"], "method": req["method"], "url": url, "status": resp.status,
                            "expected": expected, "ok": ok})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": item["name"], "method": req["method"], "url": url, "status": None,
                            "expected": expected, "ok": False, "error": str(exc)})
    passed = sum(1 for r in results if r["ok"])
    return {"total": len(results), "passed": passed, "rate": round(passed / max(1, len(results)), 3), "results": results}


# ═══════════════════════════════════════════════════════════════════════
# OpenAPI 3.0
# ═══════════════════════════════════════════════════════════════════════
def _oa_schema(schema: Optional[dict]) -> dict:
    if not schema:
        return {}
    t = schema.get("type")
    if isinstance(t, list):
        types = [x for x in t if x != "null"]
        out = {"oneOf": [{"type": x} for x in types]} if len(types) > 1 else {"type": types[0] if types else "string"}
        if "null" in t:
            out["nullable"] = True
        return out
    if t == "object":
        return {"type": "object", "properties": {k: _oa_schema(v) for k, v in schema.get("properties", {}).items()},
                **({"required": schema["required"]} if schema.get("required") else {})}
    if t == "array":
        return {"type": "array", "items": _oa_schema(schema.get("items")) or {}}
    if t in ("null", "any"):
        return {"nullable": True}
    out = {"type": t}
    if schema.get("nullable"):
        out["nullable"] = True
    return out


def to_openapi(endpoints: list, *, title: str = "Discovered API", version: str = "1.0.0") -> dict:
    servers = sorted({f"{urlsplit(e['url_template']).scheme}://{e['host']}" for e in endpoints})
    paths: dict = {}
    for ep in endpoints:
        path = re.sub(r"\{slug\}_\{id\}", "{slug}_{id}", ep["path"])
        names = {}
        for ph in re.findall(r"\{(\w+)\}", path):
            names[ph] = names.get(ph, 0) + 1
        seen: dict = {}

        def uniq(m, seen=seen, names=names):
            n = m.group(1)
            seen[n] = seen.get(n, 0) + 1
            return "{" + (f"{n}{seen[n]}" if names[n] > 1 else n) + "}"
        path = re.sub(r"\{(\w+)\}", uniq, path)
        op: dict = {"summary": f"{ep['method']} {ep['path']}" + (f" ({ep['graphql'].get('operation')})" if ep.get("graphql") else ""),
                    "operationId": slugify(f"{ep['method']}_{ep['path']}_{(ep.get('graphql') or {}).get('operation') or ''}", 80).replace("-", "_").replace(".", "_"),
                    "parameters": [], "responses": {}}
        for ph in re.findall(r"\{(\w+)\}", path):
            op["parameters"].append({"name": ph, "in": "path", "required": True, "schema": {"type": "string"}})
        for name, info in (ep.get("query_params") or {}).items():
            op["parameters"].append({"name": name, "in": "query", "required": bool(info.get("required")),
                                     "schema": {"type": info.get("type", "string")},
                                     **({"example": info["examples"][0]} if info.get("examples") else {})})
        if ep.get("request_schema"):
            op["requestBody"] = {"content": {(ep.get("request_content_types") or ["application/json"])[0]:
                                             {"schema": _oa_schema(ep["request_schema"])}}}
        elif ep.get("form_fields"):
            op["requestBody"] = {"content": {"application/x-www-form-urlencoded": {"schema": {
                "type": "object", "properties": {f: {"type": "string"} for f in ep["form_fields"]}}}}}
        for status in ep.get("statuses") or {"200": 1}:
            ct = (ep.get("response_content_types") or ["application/json"])[0]
            op["responses"][str(status)] = {"description": f"HTTP {status}", **({"content": {ct: {"schema": _oa_schema(ep["response_schema"])}}}
                                                                                  if ep.get("response_schema") else {})}
        if "bearer" in (ep.get("auth") or []):
            op["security"] = [{"bearerAuth": []}]
        key = path if not ep.get("graphql") else f"{path}#{ep['graphql'].get('operation')}"
        paths.setdefault(key, {})[ep["method"].lower()] = op
    return {"openapi": "3.0.3", "info": {"title": title, "version": version,
                                         "description": f"Inferred by AgentTrace {__version__} from captured traffic."},
            "servers": [{"url": s} for s in servers], "paths": paths,
            "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}}}


def to_yaml(obj: Any, indent: int = 0) -> str:
    """Minimal YAML emitter (dicts/lists/scalars) — no external dependency."""
    pad = "  " * indent

    def scalar(v: Any) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        s = str(v)
        if s == "" or re.search(r"[:#{}\[\],&*?|<>=!%@`'\"\n]|^\s|\s$|^(true|false|null|yes|no|on|off|\d.*)$", s, re.I):
            return json.dumps(s, ensure_ascii=False)
        return s

    lines = []
    if isinstance(obj, dict):
        if not obj:
            return pad + "{}\n"
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{scalar(k)}:\n" + to_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{scalar(k)}: {scalar(v) if not isinstance(v, (dict, list)) else ('{}' if isinstance(v, dict) else '[]')}\n")
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)) and v:
                inner = to_yaml(v, indent + 1).lstrip()
                lines.append(f"{pad}- {inner}")
            else:
                lines.append(f"{pad}- {scalar(v) if not isinstance(v, (dict, list)) else '[]'}\n")
    else:
        lines.append(f"{pad}{scalar(obj)}\n")
    return "".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Python client stub + curl
# ═══════════════════════════════════════════════════════════════════════
_CLIENT_HEADER = '''"""API client generated by AgentTrace {version} on {date}.

Standalone (standard library only). Polite by default: minimum delay between
requests, honours 429/503 Retry-After, retries transient errors.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class {cls}:
    def __init__(self, base_url={base!r}, headers=None, min_interval_s=0.5, max_retries=4, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.headers = {{"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; AgentTraceClient)"}}
        self.headers.update(headers or {{}})
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.timeout = timeout
        self._next = 0.0

    def _request(self, method, path, params=None, json_body=None, form=None):
        url = self.base_url + path
        if params:
            params = {{k: v for k, v in params.items() if v is not None}}
            if params:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        data, headers = None, dict(self.headers)
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        for attempt in range(self.max_retries + 1):
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + self.min_interval_s
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    ctype = resp.headers.get("Content-Type", "")
                    return json.loads(body) if "json" in ctype else body.decode("utf-8", "replace")
            except urllib.error.HTTPError as err:
                if err.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    retry_after = err.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                    self._next = time.monotonic() + max(delay, self.min_interval_s)
                    continue
                raise
            except urllib.error.URLError:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise
'''


def _ident(name: str) -> str:
    ident = re.sub(r"\W", "_", str(name))
    return ident if ident and not ident[0].isdigit() else f"p_{ident}"


def to_python_client(endpoints: list, *, class_name: str = "ApiClient") -> str:
    hosts = sorted({f"{urlsplit(e['url_template']).scheme}://{e['host']}" for e in endpoints}) or ["http://localhost"]
    out = [_CLIENT_HEADER.format(version=__version__, date=iso()[:10], cls=class_name, base=hosts[0])]
    used = set()
    for ep in endpoints:
        if f"{urlsplit(ep['url_template']).scheme}://{ep['host']}" != hosts[0]:
            continue
        op = (ep.get("graphql") or {}).get("operation")
        base_name = re.sub(r"\W+", "_", f"{ep['method'].lower()}_{ep['path'].strip('/')}" + (f"_{op}" if op else "")).strip("_").lower()
        base_name = re.sub(r"_+", "_", base_name.replace("{", "").replace("}", ""))[:60] or "call"
        name, n = base_name, 2
        while name in used:
            name, n = f"{base_name}_{n}", n + 1
        used.add(name)
        path_params = re.findall(r"\{(\w+)\}", ep["path"])
        args = []
        seen_args = set()
        for p in path_params:
            a = p if p not in seen_args else f"{p}{len(seen_args)}"
            seen_args.add(a)
            args.append(a)
        qnames = [_ident(k) for k in (ep.get("query_params") or {})]
        sig = ", ".join(["self"] + args + [f"{q}=None" for q in qnames if q not in args] +
                        (["json_body=None"] if ep.get("request_schema") or ep["method"] in ("POST", "PUT", "PATCH") else []))
        it = iter(args)
        path_expr = re.sub(r"\{(\w+)\}", lambda m: "{" + next(it) + "}", ep["path"])
        doc = f"{ep['method']} {ep['path']}" + (f" (GraphQL {op})" if op else "")
        fields = list(flatten_schema(ep.get("response_schema")).items())[:8]
        if fields:
            doc += " -> " + ", ".join(f"{k}:{t}" for k, t in fields)
        params = "{" + ", ".join(json.dumps(k) + ": " + _ident(k) for k in (ep.get("query_params") or {})) + "}"
        body = "json_body" if "json_body=None" in sig else "None"
        out.append(f"    def {name}({sig}):\n        \"\"\"{doc}\"\"\"\n"
                   f"        return self._request({ep['method']!r}, f{path_expr!r}, params={params}, json_body={body})\n")
    page_names = ("page", "p", "pg", "pagenum", "page_number", "pagenumber")
    pag = [e for e in endpoints if e.get("pagination") and e["method"] == "GET" and e["host"] == urlsplit(hosts[0]).netloc
           and any(p.lower() in page_names for p in e["pagination"]["params"])]
    if pag:
        ep = pag[0]
        pname = next((p for p in ep["pagination"]["params"] if p.lower() in page_names), None)
        if pname:
            out.append(f'''
    def iterate_pages(self, path={ep['path']!r}, page_param={pname!r}, start=1, max_pages=1000, **params):
        """Yield every page of a paginated endpoint until it returns no items / says there is no more."""
        for page in range(start, start + max_pages):
            data = self._request("GET", path, params={{**params, page_param: page}})
            items = data.get("items", data.get("results", data.get("data"))) if isinstance(data, dict) else data
            if not items:
                return
            yield data
            if isinstance(data, dict) and (("next" in data and data.get("next") is None) or data.get("has_more") is False
                                           or page >= int(data.get("pages") or data.get("total_pages") or 10**9)):
                return
''')
    out.append(f'''

if __name__ == "__main__":
    client = {class_name}()
    print("methods:", [m for m in dir(client) if not m.startswith("_")])
''')
    return "".join(out)


def to_curl(rec: NetRecord) -> str:
    parts = ["curl", "-X", rec.method, shlex.quote(rec.url)]
    for h in rec.request_headers:
        if _keep_header(h["name"]):
            parts += ["-H", shlex.quote(f"{h['name']}: {h['value']}")]
    if rec.request_body is not None:
        parts += ["--data-raw", shlex.quote(rec.request_text() or "")]
    return " ".join(parts)


def export_all(records: Iterable[NetRecord], out_dir: Any, *, name: str = "AgentTrace capture") -> dict:
    recs = list(records)
    out = Path(out_dir)
    eps = discover_endpoints(recs)
    paths = {
        "postman": str(write_json(out / "postman_collection.json", to_postman(recs, name=name))),
        "openapi_json": str(write_json(out / "openapi.json", to_openapi(eps, title=name))),
        "openapi_yaml": str(write_text(out / "openapi.yaml", to_yaml(to_openapi(eps, title=name)))),
        "python_client": str(write_text(out / "api_client.py", to_python_client(eps))),
        "curl": str(write_text(out / "requests.sh", "#!/bin/sh\n" + "\n\n".join(to_curl(r) for r in api_records(recs)) + "\n")),
    }
    return paths



# ════════════════════════════════════════════════════════════════════════════
# recon.py — site reconnaissance: which scraping strategy fits this site?
# ════════════════════════════════════════════════════════════════════════════
# Site reconnaissance for scraping: *what is on this site and what is the
# best way to extract it?*  Produces ``recon.json`` + ``recon.md`` with a
# recommended strategy (API-first, hydration data, JSON-LD, HTML list) and the
# exact endpoints / selectors / pagination to use.


ROBOTS_JS = r"""
async () => { try { const r = await fetch('/robots.txt', { credentials: 'omit', cache: 'no-store' });
  return [r.status, r.status === 200 ? (await r.text()).slice(0, 20000) : '']; } catch (e) { return [0, String(e)]; } }
"""


def parse_robots(text: str, user_agent: str = "*") -> dict:
    groups: list = []
    current: Optional[dict] = None
    sitemaps = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if current is None or current["rules"]:
                current = {"agents": [], "rules": [], "crawl_delay": None}
                groups.append(current)
            current["agents"].append(value.lower())
        elif key in ("disallow", "allow") and current is not None:
            current["rules"].append((key, value))
        elif key == "crawl-delay" and current is not None:
            try:
                current["crawl_delay"] = float(value)
            except ValueError:
                pass
        elif key == "sitemap":
            sitemaps.append(value)
    ua = user_agent.lower()
    chosen = next((g for g in groups if any(a != "*" and a in ua for a in g["agents"])), None) or \
        next((g for g in groups if "*" in g["agents"]), None)
    return {"disallow": [v for k, v in (chosen or {}).get("rules", []) if k == "disallow" and v],
            "allow": [v for k, v in (chosen or {}).get("rules", []) if k == "allow" and v],
            "crawl_delay": (chosen or {}).get("crawl_delay"), "sitemaps": sitemaps}


def robots_allows(robots: dict, path: str) -> bool:
    best, allowed = -1, True
    for rule, is_allow in [(r, False) for r in robots.get("disallow", [])] + [(r, True) for r in robots.get("allow", [])]:
        rx = "^" + re.escape(rule).replace(r"\*", ".*").replace(r"\$", "$")
        if re.match(rx, path) and len(rule) > best:
            best, allowed = len(rule), is_allow
    return allowed


def _largest_array(schema: Optional[dict]) -> Optional[str]:
    paths = flatten_schema(schema)
    arrays = {}
    for p in paths:
        if p.endswith("[]") and "[]." not in p[:-2].split("[]")[-1]:
            fields = [q for q in paths if q.startswith(p + ".") and q.count("[]") == p.count("[]")]
            arrays[p] = len(fields)
    if not arrays:
        return None
    return max(arrays, key=lambda k: arrays[k])


def find_item_lists(obj: Any, path: str = "", *, min_items: int = 3, depth: int = 0) -> list:
    """Arrays of similar objects inside any JSON (API body, __NEXT_DATA__ …) → path, count, fields."""
    found: list = []
    if depth > 12:
        return found
    if isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        if len(dicts) >= min_items and len(dicts) >= 0.8 * len(obj):
            keys: dict = {}
            for d in dicts:
                for k in d:
                    keys[k] = keys.get(k, 0) + 1
            common = [k for k, n in keys.items() if n >= 0.8 * len(dicts)]
            if common:
                found.append({"path": path or "$", "count": len(obj), "fields": common[:20]})
        for i, x in enumerate(obj[:3]):
            found += find_item_lists(x, f"{path}[{i}]", min_items=min_items, depth=depth + 1)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found += find_item_lists(v, f"{path}.{k}" if path else str(k), min_items=min_items, depth=depth + 1)
    if depth == 0:
        found.sort(key=lambda f: -(f["count"] * len(f["fields"])))
    return found


def _string_values(obj: Any, depth: int = 0) -> list:
    if depth > 8:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _string_values(v, depth + 1)]
    if isinstance(obj, list):
        return [s for v in obj[:200] for s in _string_values(v, depth + 1)]
    return []


def _number_values(obj: Any, depth: int = 0) -> list:
    if depth > 8:
        return []
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [obj]
    if isinstance(obj, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", obj.strip()):
        return [float(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _number_values(v, depth + 1)]
    if isinstance(obj, list):
        return [n for v in obj[:200] for n in _number_values(v, depth + 1)]
    return []


def _to_number(value: Any) -> Optional[float]:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value or ""))
    try:
        return float(m.group(0).replace(",", "")) if m else None
    except ValueError:
        return None


def _py_path(path: str) -> str:
    """'a.b[0].c' → "['a']['b'][0]['c']" (how to index the parsed JSON in Python)."""
    out = []
    for part in re.findall(r"\[\d+\]|[^.\[\]]+", path):
        out.append(part if part.startswith("[") else f"[{part!r}]")
    return "".join(out)


def recon(session: Any, url: str, *, scroll_rounds: int = 2, write: bool = True) -> dict:
    """Load ``url`` in ``session`` and analyse how to scrape it."""
    nav = session.goto(url)
    block = session.detect_block()
    state = session.inspect()
    growth = []
    for _ in range(max(0, scroll_rounds)):
        try:
            r = session.scroll(to="bottom")
            growth.append({"grew": r.get("grew"), "requests": r["network"]["requests"],
                           "primary": (r["network"].get("primary") or {}).get("url")})
        except Exception:  # noqa: BLE001
            break
    pagination = session.detect_pagination()
    items = session.detect_items(top=3)
    sample = []
    if items:
        try:
            sample = session.extract(items[0]["fields"], item=items[0]["selector"], limit=5)
        except Exception:  # noqa: BLE001
            sample = []
    jsonld = session.jsonld()
    hydration = session.hydration_data()
    try:
        status, robots_txt = session.page.evaluate(ROBOTS_JS)
    except Exception:  # noqa: BLE001
        status, robots_txt = 0, ""
    robots = parse_robots(robots_txt) if status == 200 else {"disallow": [], "allow": [], "crawl_delay": None, "sitemaps": []}
    path = urlsplit(session.page.url).path or "/"
    robots["url_allowed"] = robots_allows(robots, path)
    robots["status"] = status
    eps = discover_endpoints(session.recorder.records)
    visible = []
    for row in sample:
        if isinstance(row, dict):
            label = next((row[k] for k in ("title", "name", "headline", "text") if row.get(k)), None) or \
                next((v for v in row.values() if isinstance(v, str) and len(v) > 3 and not v.startswith("http")), "")
            label = norm_text(str(label)).rstrip("…. ")[:40]
            if len(label) >= 4:
                visible.append(label)
    prices = {round(v, 2) for v in (_to_number(row.get("price")) for row in sample if isinstance(row, dict)) if v is not None}
    by_id = {r.id: r for r in session.recorder.records}
    data_eps = []
    for ep in eps:
        best_list, strings, numbers = None, set(), set()
        for rid in ep.get("record_ids", []):
            rec = by_id.get(rid)
            data = rec.json() if rec is not None else None
            if data is None:
                continue
            lists = find_item_lists(data, min_items=1)
            if lists and (best_list is None or lists[0]["count"] > best_list["count"]):
                best_list = lists[0]
            for value in _string_values(data):
                strings.add(norm_text(value)[:120])
            numbers |= {round(float(n), 2) for n in _number_values(data)}
        overlap = sum(1 for v in visible if any(v[:25] in x for x in strings))
        if not overlap and len(prices & numbers) >= 3:  # no names on the page: same prices are good evidence too
            overlap = len(prices & numbers)
        size = best_list["count"] if best_list else 0
        score = overlap * 25 + (10 if size >= 3 else 3 if size else 0) + (5 if ep["method"] == "GET" else 0) + \
            (5 if ep.get("pagination") else 0)
        if best_list or overlap:
            data_eps.append({"endpoint": f"{ep['method']} {ep['url_template']}", "items_path": best_list and best_list["path"],
                             "items_per_response": size, "matches_visible_items": overlap, "of_visible": len(visible),
                             "score": score, "query_params": list(ep["query_params"]), "pagination": ep.get("pagination"),
                             "auth": ep.get("auth"), "kind": ep["kind"],
                             "example_url": (ep.get("examples") or [{}])[0].get("url"),
                             "fields": (best_list or {}).get("fields", [])[:15]})
    data_eps.sort(key=lambda d: -d["score"])
    hyd_summary = {k: (list(v.keys())[:15] if isinstance(v, dict) else type(v).__name__) for k, v in hydration.items()}
    ld_types = sorted({str(x.get("@type")) for x in jsonld if isinstance(x, dict)})
    strategy: dict
    if block:
        strategy = {"approach": "unblock-first", "why": f"{block['kind']} detected ({block.get('vendor') or 'unknown vendor'})",
                    "steps": ["retry with Session(stealth=True, headless=False)", "add rate_limit={'min_interval_s': 3}",
                              "keep hitl='wait' so a human can solve the challenge once, then save_state() and reuse it"]}
    elif data_eps and (data_eps[0]["matches_visible_items"] >= max(1, min(3, len(visible) // 2)) or
                       (not items and data_eps[0]["items_per_response"] >= 3)):
        best = data_eps[0]
        strategy = {"approach": "api", "why": "the visible data is served by a JSON endpoint - call it directly (faster, stable, complete)",
                    "endpoint": best["endpoint"], "items_path": best["items_path"], "pagination": best["pagination"],
                    "auth": best["auth"], "example_url": best["example_url"],
                    "steps": ["export an API client: python agenttrace.py export <run>/session.har --out api/",
                              f"page through {best['endpoint']} using {((best['pagination'] or {}).get('params') or ['(no page param seen)'])}",
                              "keep the captured headers/cookies if auth is listed"]}
    elif hydration:
        lists = find_item_lists(hydration)
        access = f"data{_py_path(lists[0]['path'])}" if lists else None
        strategy = {"approach": "hydration", "why": "the page embeds its data as a JSON state blob (no extra requests needed)",
                    "keys": hyd_summary, "items_path": lists[0]["path"] if lists else None,
                    "fields": lists[0]["fields"] if lists else None, "count": lists[0]["count"] if lists else None,
                    "steps": [f"data = session.hydration_data(); rows = {access}" if access else
                              "session.hydration_data() and read the list from the blob",
                              "other pages of the site usually carry their data at the same blob path"]}
    elif any(t in ("ItemList", "Product", "NewsArticle", "Article", "Recipe", "Event", "JobPosting") for t in ld_types) and not items:
        strategy = {"approach": "jsonld", "why": f"structured data present: {ld_types}", "steps": ["session.jsonld()"]}
    elif items:
        pag_kind = pagination.get("type")
        strategy = {"approach": "html-list", "why": f"repeated item structure '{items[0]['selector']}' ({items[0]['count']} items)",
                    "item": items[0]["selector"], "fields": items[0]["fields"], "pagination": pag_kind,
                    "steps": [f"session.paginate(mode='{ {'next': 'next', 'load_more': 'load_more', 'url': 'url'}.get(pag_kind, 'scroll')}', "
                              f"item={items[0]['selector']!r}, fields={json.dumps(items[0]['fields'])})"]}
        if ld_types:
            strategy["also"] = f"JSON-LD on detail pages: {ld_types}"
    else:
        strategy = {"approach": "interactive", "why": "no list/API detected on this page",
                    "steps": ["session.observe() and navigate to the listing/search page first"]}
    if robots.get("crawl_delay"):
        strategy.setdefault("politeness", f"robots.txt Crawl-delay: {robots['crawl_delay']}s → rate_limit={{'min_interval_s': {robots['crawl_delay']}}}")
    if not robots["url_allowed"]:
        strategy["robots_warning"] = f"robots.txt disallows {path} for generic crawlers"
    report = {
        "url": url, "final_url": session.page.url, "status": nav.get("status"), "title": state.get("title"),
        "generated": iso(), "blocked": block, "strategy": strategy,
        "page": {"headings": state.get("headings", [])[:10], "forms": state.get("forms", []),
                 "json_ld_types": ld_types, "hydration": hyd_summary, "meta": state.get("meta", {}),
                 "canonical": state.get("canonical"), "lang": state.get("lang")},
        "lists": items, "sample_items": sample, "pagination": pagination, "scroll": growth,
        "data_endpoints": data_eps[:8], "endpoints": [{"endpoint": f"{e['method']} {e['url_template']}", "kind": e["kind"],
                                                        "count": e["count"], "statuses": e["statuses"]} for e in eps[:40]],
        "robots": robots,
        "network": {"requests": len(session.recorder.records),
                    "noise": sum(1 for r in session.recorder.records if r.category in
                                 ("analytics", "ads", "tracking", "monitoring", "social", "consent", "antibot"))},
        "cookies": session.cookies(),
    }
    if write:
        report["paths"] = {"json": str(write_json(session.out_dir / "recon.json", report)),
                           "md": str(write_text(session.out_dir / "recon.md", recon_markdown(report)))}
    return report


def recon_markdown(r: dict) -> str:
    s = r["strategy"]
    lines = [f"# Recon: {r.get('title') or r['url']}", "", f"- url: {r['url']} → {r['final_url']} (HTTP {r.get('status')})",
             f"- requests captured: {r['network']['requests']} (noise {r['network']['noise']})",
             f"- blocked: {r['blocked']['kind'] if r.get('blocked') else 'no'}", "",
             f"## Recommended strategy: **{s['approach']}**", "", f"{s['why']}", ""]
    for key in ("endpoint", "items_path", "item", "pagination", "example_url", "politeness", "robots_warning", "also"):
        if s.get(key):
            lines.append(f"- {key}: `{s[key] if not isinstance(s[key], (dict, list)) else json.dumps(s[key])}`")
    if s.get("fields"):
        lines.append(f"- fields: `{json.dumps(s['fields'])}`")
    for step in s.get("steps", []):
        lines.append(f"  1. {step}")
    if r["data_endpoints"]:
        lines += ["", "## Data endpoints (JSON)", ""]
        for d in r["data_endpoints"]:
            lines.append(f"- `{d['endpoint']}` items at `{d['items_path']}`, matches visible items: {d['matches_visible_items']}, "
                         f"params: {d['query_params']}, pagination: {d['pagination']}")
    if r["lists"]:
        lines += ["", "## Repeated lists on the page", ""]
        for c in r["lists"]:
            lines.append(f"- `{c['selector']}` × {c['count']} — e.g. {truncate(c['sample'], 80)!r}")
            lines.append(f"  fields: `{json.dumps(c['fields'])}`")
    if r["sample_items"]:
        lines += ["", "## Sample items", "", "```json", json.dumps(r["sample_items"][:3], indent=1, ensure_ascii=False)[:1500], "```"]
    p = r["pagination"]
    lines += ["", "## Pagination", "", f"- type: **{p.get('type')}**" + (f", next: {p['next']['text']!r}" if p.get("next") else "")
              + (f", load more: {p['load_more']['text']!r}" if p.get("load_more") else "")
              + (f", numbered max: {p['numbered'].get('max')}" if p.get("numbered") else "")
              + (f", url param: {p['url_param']}" if p.get("url_param") else "")]
    pg = r["page"]
    lines += ["", "## Page structure", "", f"- JSON-LD types: {pg['json_ld_types'] or 'none'}",
              f"- hydration blobs: {list(pg['hydration']) or 'none'}", f"- forms: {[f.get('id') or f.get('action') for f in pg['forms']]}",
              f"- headings: {[h['text'] for h in pg['headings'][:6]]}"]
    rb = r["robots"]
    lines += ["", "## robots.txt", "", f"- status {rb.get('status')}, url allowed: {rb.get('url_allowed')}, crawl-delay: {rb.get('crawl_delay')}",
              f"- disallow: {rb.get('disallow')[:10]}"]
    if r["endpoints"]:
        lines += ["", "## All API endpoints seen", ""] + [f"- `{e['endpoint']}` ({e['kind']}, {e['count']}×)" for e in r["endpoints"][:25]]
    return "\n".join(lines) + "\n"



# ════════════════════════════════════════════════════════════════════════════
# mcp.py — AI tool catalog + MCP server (Parts 6, 7)
# ════════════════════════════════════════════════════════════════════════════
# AI tool catalog + MCP stdio server (Parts 6 & 7).
#
# ``TOOLS`` describes every agent tool with a JSON schema; ``dispatch_tool``
# executes one against a :class:`Session`.  The same catalog powers the MCP
# server (``python agenttrace.py mcp``) used by Claude Code / Claude Desktop /
# Cline, and can be handed to any LLM tool-use API directly.


_T = {"type": "string"}
TOOLS = [
    {"name": "goto", "description": "Open a URL in the browser session and wait until the page and its API calls settled. "
     "Returns status, title, and the main request.", "input_schema": {"type": "object", "properties": {"url": _T}, "required": ["url"]}},
    {"name": "observe", "description": "List visible interactive elements (links, buttons, inputs) with refs like 'e12', plus a text excerpt. "
     "Call this before clicking when unsure.", "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "click", "description": "Click an element by ref ('e12'), CSS selector or plain description ('Add to cart', "
     "'Buy button for Linen Pillow', 'second result link'). Returns the network requests the click caused.",
     "input_schema": {"type": "object", "properties": {"target": _T}, "required": ["target"]}},
    {"name": "fill", "description": "Type text into a field found by label/placeholder/name/ref. submit=true presses Enter.",
     "input_schema": {"type": "object", "properties": {"target": _T, "text": _T, "submit": {"type": "boolean"}}, "required": ["target", "text"]}},
    {"name": "select", "description": "Choose an option in a dropdown (by value or visible label).",
     "input_schema": {"type": "object", "properties": {"target": _T, "value": _T}, "required": ["target", "value"]}},
    {"name": "check", "description": "Tick (or untick with checked=false) a checkbox/radio.",
     "input_schema": {"type": "object", "properties": {"target": _T, "checked": {"type": "boolean"}}, "required": ["target"]}},
    {"name": "press", "description": "Press a key (Enter, Escape, Tab, ArrowDown…), optionally inside a target element.",
     "input_schema": {"type": "object", "properties": {"key": _T, "target": _T}, "required": ["key"]}},
    {"name": "scroll", "description": "Scroll the page. to='bottom' triggers infinite-scroll loading.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string", "enum": ["bottom", "top"]}, "direction": {"type": "string", "enum": ["down", "up"]}}}},
    {"name": "wait_for", "description": "Wait for a condition: text, selector, url, or response (URL pattern).",
     "input_schema": {"type": "object", "properties": {"text": _T, "selector": _T, "url": _T, "response": _T, "timeout_ms": {"type": "integer"}}}},
    {"name": "inspect", "description": "Structured page/browser state: forms, headings, tabs, frames, cookies (names), storage keys, "
     "JSON-LD types, hydration blobs, open dialogs, block status.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "extract", "description": "Extract data. With item (CSS) + fields ({name: 'css' | 'css@attr'}) returns rows; "
     "with nothing, auto-detects the main repeated list (products/results/posts) and its fields.",
     "input_schema": {"type": "object", "properties": {"item": _T, "fields": {"type": "object"}, "limit": {"type": "integer"}}}},
    {"name": "paginate", "description": "Collect items across pages: follows next links / load-more / infinite scroll automatically.",
     "input_schema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["auto", "next", "load_more", "scroll", "url"]},
                                                      "max_pages": {"type": "integer"}, "item": _T, "fields": {"type": "object"}}}},
    {"name": "requests", "description": "Captured network requests (noise like analytics/ads hidden unless include_noise). "
     "Filter by URL substring pattern.", "input_schema": {"type": "object", "properties": {"pattern": _T, "method": _T, "include_noise": {"type": "boolean"}, "limit": {"type": "integer"}}}},
    {"name": "response_json", "description": "Parsed JSON body of the latest captured response whose URL contains pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": _T}, "required": ["pattern"]}},
    {"name": "endpoints", "description": "API endpoints discovered so far (REST/GraphQL) with params, schemas, auth and pagination hints.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "capture_page", "description": "Navigate to a URL and write a verified HAR (waits for slow/delayed APIs, retries empty "
     "captures). Returns the HAR file path.", "input_schema": {"type": "object", "properties": {"url": _T, "expect": {"type": "array", "items": _T}}, "required": ["url"]}},
    {"name": "recon", "description": "Analyse a site for scraping: blocking, lists, pagination, JSON APIs behind the page, robots.txt, "
     "and a recommended strategy. Writes recon.md.", "input_schema": {"type": "object", "properties": {"url": _T}, "required": ["url"]}},
    {"name": "page_data", "description": "Data embedded in the page: JSON-LD + framework state (__NEXT_DATA__, __NUXT__, "
     "__APOLLO_STATE__ …). Without path: a summary of the item lists found (path, count, fields). With path "
     "(e.g. '__NEXT_DATA__.props.pageProps.products'): that part of the data (first `limit` items).",
     "input_schema": {"type": "object", "properties": {"path": _T, "limit": {"type": "integer"}}}},
    {"name": "fetch", "description": "HTTP request with the browser's cookies, headers and proxy - e.g. page through the JSON "
     "API that recon found, while logged in. Rate-limited (obeys Retry-After) and recorded in the HAR. "
     "Returns status + json (or text).",
     "input_schema": {"type": "object", "properties": {"url": _T, "method": _T, "params": {"type": "object"},
                                                      "json": {"type": "object"}, "headers": {"type": "object"}},
                      "required": ["url"]}},
    {"name": "screenshot", "description": "Save a screenshot; returns its path.", "input_schema": {"type": "object", "properties": {"full_page": {"type": "boolean"}}}},
    {"name": "back", "description": "Go back in history.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "switch_tab", "description": "Switch to another tab/popup: 'latest', 'opener', a page id (p2) or URL substring.",
     "input_schema": {"type": "object", "properties": {"which": _T}}},
    {"name": "save_state", "description": "Save login state (cookies, localStorage, IndexedDB, sessionStorage) to a file for reuse.",
     "input_schema": {"type": "object", "properties": {"path": _T}, "required": ["path"]}},
    {"name": "detect_block", "description": "Is the page a CAPTCHA / bot wall / access-denied page?", "input_schema": {"type": "object", "properties": {}}},
    {"name": "export", "description": "Export captured API traffic as Postman collection, OpenAPI spec, Python client and curl. Returns file paths.",
     "input_schema": {"type": "object", "properties": {"out_dir": _T}}},
    {"name": "finish", "description": "Close the browser and write all artifacts (HARs, report.md, endpoints, timeline). Returns paths.",
     "input_schema": {"type": "object", "properties": {}}},
]
TOOL_NAMES = tuple(t["name"] for t in TOOLS)


def tool_catalog(fmt: str = "anthropic") -> list:
    """Tool definitions for LLM APIs (``anthropic`` → input_schema, ``mcp`` → inputSchema,
    ``openai`` → function tools)."""
    out = []
    for t in TOOLS:
        if fmt == "mcp":
            out.append({"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]})
        elif fmt == "openai":
            out.append({"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                         "parameters": t["input_schema"]}})
        else:
            out.append({"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]})
    return out


def _compact(result: Any) -> Any:
    """Keep tool results small for the model (no ranking internals)."""
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if not k.startswith("_")}
    return result


def dispatch_tool(session: Any, name: str, args: Optional[dict] = None) -> Any:
    args = dict(args or {})
    if name not in TOOL_NAMES:
        raise AgentTraceError(f"unknown tool {name!r}", hint=f"available: {', '.join(TOOL_NAMES)}")
    if name == "goto":
        return _compact(session.goto(args["url"]))
    if name == "observe":
        return session.observe(limit=int(args.get("limit") or 80))
    if name == "click":
        return _compact(session.click(args["target"]))
    if name == "fill":
        return _compact(session.fill(args["target"], args.get("text", ""), submit=bool(args.get("submit"))))
    if name == "select":
        return _compact(session.select(args["target"], args.get("value")))
    if name == "check":
        return _compact(session.check(args["target"], args.get("checked", True) is not False))
    if name == "press":
        return _compact(session.press(args["key"], args.get("target")))
    if name == "scroll":
        return _compact(session.scroll(args.get("direction", "down"), to=args.get("to")))
    if name == "wait_for":
        cond = {k: v for k, v in args.items() if k in ("text", "selector", "url", "response") and v}
        return session.wait_for(cond, timeout_ms=float(args.get("timeout_ms") or 15000))
    if name == "inspect":
        return session.inspect()
    if name == "extract":
        if args.get("item") or args.get("fields"):
            rows = session.extract(args.get("fields") or {}, item=args.get("item"), limit=args.get("limit"))
            return {"items": rows, "count": len(rows) if isinstance(rows, list) else 1}
        return session.extract_list(limit=args.get("limit"))
    if name == "paginate":
        res = session.paginate(mode=args.get("mode", "auto"), max_pages=int(args.get("max_pages") or 10),
                               item=args.get("item"), fields=args.get("fields"))
        res["items"] = res["items"][:200]
        return res
    if name == "requests":
        return session.requests(args.get("pattern", ""), method=args.get("method"),
                                include_noise=bool(args.get("include_noise")), limit=int(args.get("limit") or 50))
    if name == "response_json":
        return session.response_json(args["pattern"])
    if name == "endpoints":
        eps = session.endpoints()
        return [{k: e[k] for k in ("method", "url_template", "kind", "count", "statuses", "query_params", "auth", "pagination")}
                for e in eps]
    if name == "capture_page":
        return session.capture(args["url"], expect=tuple(args.get("expect") or ()))
    if name == "recon":
        rep = recon(session, args["url"])
        return {k: rep[k] for k in ("url", "final_url", "status", "title", "blocked", "strategy", "pagination",
                                    "data_endpoints", "lists", "sample_items", "robots", "paths") if k in rep}
    if name == "page_data":
        data = {"jsonld": session.jsonld(), **session.hydration_data()}
        if not args.get("path"):
            return {"keys": sorted(data), "jsonld_types": sorted({str(x.get("@type")) for x in data["jsonld"] if isinstance(x, dict)}),
                    "item_lists": find_item_lists(data)[:8]}
        node: Any = data
        for part in re.findall(r"[^.\[\]]+", str(args["path"])):
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node[: int(args.get("limit") or 50)] if isinstance(node, list) else node
    if name == "fetch":
        res = session.fetch(args["url"], method=args.get("method") or "GET", params=args.get("params"),
                            json_body=args.get("json"), headers=args.get("headers"))
        out = {k: res[k] for k in ("id", "ok", "status", "json", "text", "error", "hint") if k in res}
        if isinstance(out.get("text"), str) and len(out["text"]) > 20_000:
            out["text"] = out["text"][:20_000] + " …(truncated)"
        return out
    if name == "screenshot":
        return {"path": session.screenshot(full_page=bool(args.get("full_page")))}
    if name == "back":
        return _compact(session.back())
    if name == "switch_tab":
        return session.switch_to(args.get("which") or "latest")
    if name == "save_state":
        return session.save_state(args["path"])
    if name == "detect_block":
        return session.detect_block() or {"blocked": False}
    if name == "export":
        out = Path(args.get("out_dir") or (Path(session.out_dir) / "export"))
        return export_all(session.recorder.records, out)
    return session.finish()


# ═══════════════════════════════════════════════════════════════════════
# MCP stdio server
# ═══════════════════════════════════════════════════════════════════════
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


class MCPServer:
    """Minimal, dependency-free MCP server (JSON-RPC 2.0 over stdio)."""

    def __init__(self, *, session_factory: Any = None, out_dir: Any = "agenttrace_runs") -> None:
        self.session_factory = session_factory
        self.out_dir = out_dir
        self.session = None
        self._log = get_logger("mcp")

    def _session(self):
        if self.session is None:
            if self.session_factory is not None:
                self.session = self.session_factory()
            else:
                self.session = Session(name="mcp", strict=False)
            self.session.start()
        return self.session

    def handle(self, msg: Any) -> Optional[dict]:
        if not isinstance(msg, dict):
            return _rpc_error(None, -32600, "invalid request")
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        is_note = "id" not in msg
        if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
            return None
        try:
            if method == "initialize":
                wanted = params.get("protocolVersion")
                version = wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
                result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "agenttrace", "version": __version__},
                          "instructions": "Browser automation + network capture for web scraping. Start with recon(url) "
                                          "or goto(url)+observe(); click/fill by plain description; requests()/endpoints() "
                                          "show the APIs; finish() writes HAR + report."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_catalog("mcp")}
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                if name not in TOOL_NAMES:
                    return None if is_note else _rpc_error(mid, -32602, f"unknown tool: {name}")
                if not isinstance(args, dict):
                    return None if is_note else _rpc_error(mid, -32602, "arguments must be an object")
                result = self._call(name, args)
            elif method in ("resources/list", "prompts/list"):
                result = {"resources": []} if method == "resources/list" else {"prompts": []}
            else:
                return None if is_note else _rpc_error(mid, -32601, f"method not found: {method}")
        except Exception as exc:  # noqa: BLE001
            self._log.exception("internal error")
            return None if is_note else _rpc_error(mid, -32603, f"{type(exc).__name__}: {exc}")
        return None if is_note else {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call(self, name: str, args: dict) -> dict:
        try:
            session = self._session()
            payload = dispatch_tool(session, name, args)
            if name == "finish":
                self.session = None
            is_error = isinstance(payload, dict) and payload.get("ok") is False
            data = to_jsonable(payload)
        except AgentTraceError as exc:
            data, is_error = exc.to_dict(), True
        except Exception as exc:  # noqa: BLE001
            data, is_error = {"error": type(exc).__name__, "message": str(exc),
                              "trace": traceback.format_exc(limit=3)}, True
        text = json.dumps(data, ensure_ascii=False, default=str)
        out = {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}
        if isinstance(data, dict):
            out["structuredContent"] = data
        return out

    def serve(self, stdin: Any = None, stdout: Any = None) -> int:
        stdin = stdin or sys.stdin
        out = stdout or getattr(sys.stdout, "buffer", sys.stdout)
        self._log.info("agenttrace MCP server ready (stdio)")
        try:
            for raw in stdin:
                line = raw.strip() if isinstance(raw, str) else raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    resp: Optional[dict] = _rpc_error(None, -32700, f"parse error: {exc}")
                else:
                    if isinstance(msg, list):
                        batch = [r for r in (self.handle(m) for m in msg) if r is not None]
                        resp = batch or None  # type: ignore[assignment]
                    else:
                        resp = self.handle(msg)
                if resp is not None:
                    data = (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
                    if hasattr(out, "write") and not hasattr(out, "encoding"):
                        out.write(data)
                    else:
                        out.write(data.decode("utf-8"))
                    out.flush()
        except (KeyboardInterrupt, BrokenPipeError):
            pass
        finally:
            if self.session is not None:
                try:
                    self.session.finish()
                except Exception:  # noqa: BLE001
                    pass
        return 0


def _rpc_error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def run_mcp_server(*, out_dir: Any = "agenttrace_runs", headless: bool = True, stealth: bool = False,
                   log_file: Any = None, profile: Optional[dict] = None) -> int:
    configure_logging("INFO", console=True, stream=sys.stderr, log_file=log_file)

    def factory():
        return Session(name="mcp", out_dir=Path(out_dir) / f"mcp-{time.strftime('%Y%m%d-%H%M%S')}",
                       headless=headless, stealth=stealth, profile=profile, strict=False)
    return MCPServer(session_factory=factory, out_dir=out_dir).serve()



# ════════════════════════════════════════════════════════════════════════════
# workflow.py — configs, checkpointed runner, record / replay, natural-language goals (Parts 11, 20, 21, 27, 35)
# ════════════════════════════════════════════════════════════════════════════
# Workflows: site config (Part 11), checkpointed execution & resume (27),
# recording of manual interactions (20), replay (21) and natural-language
# goal execution (35).
#
# A workflow is plain data (JSON / YAML / TOML)::
#
#     name: shoplab-cart
#     start_url: http://shop.example/
#     profile: {stealth: true}
#     auth: {state_file: auth/shop.json, check: {text: "Welcome"},
#            login: [{goto: /login}, {fill: {target: Username, text: "${env:SHOP_USER}"}},
#                    {fill: {target: Password, text: "${secret:SHOP_PASS}"}}, {click: Sign in}]}
#     steps:
#       - goto: /search?q=lamp
#       - click: first result link
#       - click: Add to cart
#         expect: {url: /api/v1/cart, method: POST, status: 2xx}
#       - extract_list: {save_as: products}


STEP_ACTIONS = ("goto", "click", "fill", "type", "select", "check", "uncheck", "press", "hover", "scroll", "wait_for",
                "extract", "extract_list", "paginate", "download", "screenshot", "save_state", "capture", "assert",
                "back", "reload", "switch_tab", "close_tab", "mock", "block", "sleep_ms", "upload", "submit", "set",
                "dismiss_overlays", "wait_for_human")
_STEP_OPTIONS = ("id", "name", "expect", "wait", "optional", "retry", "save_as", "comment", "timeout_ms")
_PRIMARY_PARAM = {"goto": "url", "click": "target", "hover": "target", "check": "target", "uncheck": "target",
                  "download": "target", "submit": "target", "fill": "target", "type": "target", "select": "target",
                  "press": "key", "scroll": "to", "wait_for": "text", "screenshot": "path", "save_state": "path",
                  "capture": "url", "switch_tab": "which", "close_tab": "page_id", "mock": "pattern", "block": "pattern",
                  "sleep_ms": "ms", "upload": "target", "assert": "text", "extract": "item", "extract_list": "item",
                  "paginate": "mode", "wait_for_human": "reason"}


# ═══════════════════════════════════════════════════════════════════════
# config loading & validation (Part 11)
# ═══════════════════════════════════════════════════════════════════════
def load_config(source: Any) -> dict:
    """Load a workflow/site config from a dict, JSON, YAML (needs PyYAML) or TOML file."""
    if isinstance(source, dict):
        return dict(source)
    path = Path(source)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ConfigError("YAML configs need PyYAML", hint="pip install pyyaml, or use a .json config") from exc
        data = yaml.safe_load(text)
    elif suffix == ".toml":
        try:
            import tomllib  # type: ignore
        except ImportError as exc:  # Python < 3.11
            raise ConfigError("TOML configs need Python 3.11+", hint="use JSON or YAML") from exc
        data = tomllib.loads(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config {path} must be a mapping at top level")
    data.setdefault("_source", str(path))
    return data


def normalize_step(raw: Any, index: int) -> dict:
    if isinstance(raw, str):
        verb, _, rest = raw.strip().partition(" ")
        verb = verb.lower()
        if verb not in STEP_ACTIONS:
            raise ConfigError(f"step {index}: unknown action {verb!r}", hint=f"valid: {STEP_ACTIONS}")
        raw = {verb: rest.strip()}
    if not isinstance(raw, dict):
        raise ConfigError(f"step {index}: must be a mapping or 'action target' string, got {type(raw).__name__}")
    actions = [k for k in raw if k in STEP_ACTIONS]
    if len(actions) != 1:
        raise ConfigError(f"step {index}: needs exactly one action key, found {actions or list(raw)}",
                          hint=f"valid actions: {STEP_ACTIONS}")
    action = actions[0]
    value = raw[action]
    primary = _PRIMARY_PARAM.get(action, "value")
    if isinstance(value, dict) and primary == "target" and "target" not in value and \
            ({"selector", "fingerprint"} & set(value) or (set(value) <= {"text", "css", "name", "description"})):
        value = {"target": value}  # a recorded element (selector + fingerprint) given directly as the step value
    params = dict(value) if isinstance(value, dict) else ({primary: value} if value is not None else {})
    for key, v in raw.items():
        if key not in _STEP_OPTIONS and key != action:
            params.setdefault(key, v)
    step = {"id": str(raw.get("id") or f"s{index:03d}"), "action": action, "params": params}
    for key in _STEP_OPTIONS:
        if key in raw and key != "id":
            step[key] = raw[key]
    return step


def validate_config(cfg: dict) -> list:
    problems = []
    known = {"name", "description", "start_url", "base_url", "profile", "auth", "rate_limit", "noise", "retry",
             "guardrails", "hitl", "plugins", "steps", "pages", "outputs", "vars", "evidence", "settle_quiet_ms",
             "deterministic", "expect", "_source", "version", "secrets_file", "debug", "trace", "redact",
             "body_policy"}
    for key in cfg:
        if key not in known:
            problems.append(f"unknown top-level key {key!r}")
    if not cfg.get("steps") and not cfg.get("pages") and not cfg.get("start_url"):
        problems.append("nothing to do: define 'steps', 'pages' or 'start_url'")
    for i, raw in enumerate(cfg.get("steps") or [], start=1):
        try:
            normalize_step(raw, i)
        except ConfigError as exc:
            problems.append(str(exc))
    auth = cfg.get("auth") or {}
    for i, raw in enumerate(auth.get("login") or [], start=1):
        try:
            normalize_step(raw, i)
        except ConfigError as exc:
            problems.append(f"auth.login: {exc}")
    return problems


def workflow_version(cfg: dict) -> str:
    core = {k: cfg.get(k) for k in ("name", "start_url", "steps", "pages", "auth") if cfg.get(k) is not None}
    return sha256_hex(dump_json(core, indent=None))[:12]


_VAR_RE = re.compile(r"\$\{(env|secret|var)?:?([A-Za-z_][\w.]*)\}")


def substitute(value: Any, variables: dict, secrets: Optional[dict] = None) -> Any:
    """``${env:NAME}``, ``${secret:NAME}``, ``${var}`` interpolation (recursive)."""
    if isinstance(value, dict):
        return {k: substitute(v, variables, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables, secrets) for v in value]
    if not isinstance(value, str) or "${" not in value:
        return value

    def repl(m: re.Match) -> str:
        kind, name = m.group(1), m.group(2)
        if kind == "env":
            if name not in os.environ:
                raise ConfigError(f"environment variable {name} is not set", hint=f"export {name}=...")
            return os.environ[name]
        if kind == "secret":
            if secrets and name in secrets:
                return str(secrets[name])
            env = os.environ.get(f"AGENTTRACE_SECRET_{name.upper()}") or os.environ.get(name)
            if env is None:
                raise ConfigError(f"secret {name} not provided", hint=f"set AGENTTRACE_SECRET_{name.upper()} or a secrets file")
            return env
        cur: Any = variables
        for part in name.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise ConfigError(f"unknown variable {name!r}")
        return str(cur)
    return _VAR_RE.sub(repl, value)


# ═══════════════════════════════════════════════════════════════════════
# step execution + checkpointed runner (Part 27)
# ═══════════════════════════════════════════════════════════════════════
def execute_step(session: Session, step: dict, variables: dict, secrets: Optional[dict] = None,
                 base_url: Optional[str] = None) -> dict:
    """Run one normalised step on a session; returns a JSON-able result."""
    action = step["action"]
    p = substitute(step.get("params") or {}, variables, secrets)
    opts = {k: substitute(step[k], variables, secrets) for k in ("expect", "wait", "retry") if k in step}
    if action == "goto":
        url = p["url"]
        if base_url and not re.match(r"^[a-z]+:", url):
            url = urljoin(base_url, url)
        return session.goto(url, **opts)
    if action == "click":
        return session.click(p["target"], **opts)
    if action == "fill":
        return session.fill(p["target"], str(p.get("text", p.get("value", ""))), submit=bool(p.get("submit")), **opts)
    if action == "type":
        return session.type(p["target"], str(p.get("text", p.get("value", ""))), submit=bool(p.get("submit")), **opts)
    if action == "select":
        return session.select(p["target"], p.get("value"), label=p.get("label"), index=p.get("index"), **opts)
    if action in ("check", "uncheck"):
        return session.check(p["target"], action == "check", **opts)
    if action == "press":
        return session.press(p["key"], p.get("target"), **opts)
    if action == "hover":
        return session.hover(p["target"], **opts)
    if action == "scroll":
        return session.scroll(p.get("direction", "down"), to=p.get("to") if p.get("to") in ("top", "bottom") else None,
                              amount=p.get("amount"), target=p.get("target"))
    if action == "wait_for":
        cond = {k: v for k, v in p.items() if k != "timeout_ms"}
        return session.wait_for(cond, timeout_ms=float(p.get("timeout_ms") or step.get("timeout_ms") or 15000))
    if action == "extract":
        return {"ok": True, "data": session.extract(p.get("fields") or {}, item=p.get("item"), limit=p.get("limit"))}
    if action == "extract_list":
        res = session.extract_list(item=p.get("item"), fields=p.get("fields"), limit=p.get("limit"))
        return {"ok": True, "data": res["items"], "item": res["item"], "fields": res["fields"]}
    if action == "paginate":
        res = session.paginate(mode=p.get("mode", "auto"), max_pages=int(p.get("max_pages") or 20), item=p.get("item"),
                               fields=p.get("fields"), max_items=p.get("max_items"))
        return {"ok": True, "data": res["items"], "pages": res["page_count"], "stopped_because": res["stopped_because"],
                "mode": res["mode"]}
    if action == "download":
        return session.download(p["target"], **opts)
    if action == "screenshot":
        return {"ok": True, "path": session.screenshot(p.get("path"), full_page=bool(p.get("full_page")))}
    if action == "save_state":
        return {"ok": True, **session.save_state(p["path"])}
    if action == "capture":
        res = session.capture(p["url"], expect=tuple(p.get("expect") or ()))
        return res
    if action == "assert":
        cond = dict(p)
        if "request" in cond:
            v = validate_expectations(session.recorder.records, [cond.pop("request")])
            if not v["ok"]:
                raise ActionError("assertion failed: " + "; ".join(f for c in v["checks"] for f in c["failures"]))
            return {"ok": True, "validation": v}
        return session.wait_for(cond, timeout_ms=float(p.get("timeout_ms") or 5000))
    if action == "back":
        return session.back()
    if action == "reload":
        return session.reload()
    if action == "switch_tab":
        return session.switch_to(p.get("which") or "latest")
    if action == "close_tab":
        return session.close_page(p.get("page_id"))
    if action == "mock":
        pattern = p.pop("pattern")
        return {"ok": True, "rule": session.mock(pattern, json_body=p.get("json"), body=p.get("body"),
                                                 status=int(p.get("status", 200)), headers=p.get("headers"))}
    if action == "block":
        return {"ok": True, "rule": session.block(p["pattern"])}
    if action == "sleep_ms":
        session._sleep(float(p.get("ms") or 0) / 1000.0)
        return {"ok": True}
    if action == "upload":
        return session.upload(p["target"], p.get("files") or p.get("file"))
    if action == "submit":
        return session.submit(p.get("target"))
    if action == "set":
        variables.update({k: v for k, v in p.items()})
        return {"ok": True}
    if action == "dismiss_overlays":
        return {"ok": True, "dismissed": session.dismiss_overlays()}
    if action == "wait_for_human":
        return session.wait_for_human(p.get("reason") or "manual step")
    raise ConfigError(f"unsupported action {action!r}")


class WorkflowRunner:
    """Run steps with a persistent checkpoint; ``resume=True`` continues after
    the last completed step (completed steps are never executed twice)."""

    def __init__(self, workflow: dict, session: Session, *, state_path: Any = None, resume: bool = False,
                 variables: Optional[dict] = None, secrets: Optional[dict] = None, continue_on_error: bool = False,
                 steps_key: str = "steps", checkpoint_storage: bool = True) -> None:
        self.workflow = workflow
        self.session = session
        self.steps = [normalize_step(s, i) for i, s in enumerate(workflow.get(steps_key) or [], start=1)]
        self.state_path = Path(state_path) if state_path else None
        self.resume = resume
        self.variables = dict(workflow.get("vars") or {})
        self.variables.update(variables or {})
        self.secrets = secrets or {}
        self.continue_on_error = continue_on_error
        self.checkpoint_storage = checkpoint_storage
        self.version = workflow_version(workflow)
        self.base_url = workflow.get("base_url") or workflow.get("start_url")
        self._log = get_logger("workflow")

    def _fresh_state(self) -> dict:
        return {"workflow": self.workflow.get("name", "workflow"), "version": self.version, "created": iso(),
                "updated": iso(), "status": "running", "resumes": 0,
                "steps": [{"id": s["id"], "action": s["action"], "status": "pending", "attempts": 0} for s in self.steps],
                "vars": {}, "checkpoint": None}

    def _save(self, state: dict) -> None:
        if self.state_path is not None:
            state["updated"] = iso()
            write_json(self.state_path, state)

    def load_state(self) -> Optional[dict]:
        if self.state_path is None or not self.state_path.exists():
            return None
        state = read_json(self.state_path)
        if state.get("version") != self.version:
            raise ConfigError(f"checkpoint {self.state_path} belongs to a different workflow version "
                              f"({state.get('version')} != {self.version})",
                              hint="delete the state file or run without --resume")
        return state

    def run(self) -> dict:
        state = self.load_state() if self.resume else None
        resumed = state is not None
        if state is None:
            state = self._fresh_state()
        else:
            state["resumes"] = state.get("resumes", 0) + 1
            state["status"] = "running"
            self.variables.update(state.get("vars") or {})
        self._save(state)
        session = self.session
        if resumed and state.get("checkpoint"):
            cp = state["checkpoint"]
            if cp.get("url") and cp["url"].startswith("http"):
                session.goto(cp["url"])
        results = []
        t0 = mono_ms()
        for index, step in enumerate(self.steps):
            entry = state["steps"][index]
            if entry["status"] == "done":
                results.append({"id": step["id"], "action": step["action"], "status": "skipped (already done)"})
                continue
            entry.update(status="running", started=iso(), attempts=entry.get("attempts", 0) + 1)
            self._save(state)
            n_actions = len(session._actions)
            session._jlog("info", "step_start", f"step {index + 1}/{len(self.steps)} {step['id']} {step['action']}",
                          step=index + 1, step_id=step["id"], step_action=step["action"],
                          workflow=self.workflow.get("name"))
            try:
                res = execute_step(session, step, self.variables, self.secrets, self.base_url)
                ok = res.get("ok", True) if isinstance(res, dict) else True
                if step.get("save_as") and isinstance(res, dict) and "data" in res:
                    self.variables[step["save_as"]] = res["data"]
                    state["vars"][step["save_as"]] = res["data"]
                entry.update(status="done" if ok else "failed", ended=iso(),
                             summary=_step_summary(res))
                results.append({"id": step["id"], "action": step["action"], "status": entry["status"], "result": res})
            except GuardrailViolation as exc:
                entry.update(status="failed", ended=iso(), error="guardrail")
                state["status"] = "failed"
                self._save(state)
                session._jlog("error", "step_end", f"step {index + 1} {step['id']} stopped by guardrail: {exc}",
                              step=index + 1, step_id=step["id"], step_action=step["action"], status="failed",
                              error=str(exc), details=exc.details)
                raise
            except AgentTraceError as exc:
                entry.update(status="skipped" if step.get("optional") else "failed", ended=iso(),
                             error=truncate(str(exc), 400))
                results.append({"id": step["id"], "action": step["action"], "status": entry["status"],
                                "error": truncate(str(exc), 400), "details": getattr(exc, "details", None)})
                if not step.get("optional") and not self.continue_on_error:
                    state["status"] = "failed"
                    self._save(state)
                    self._log_step_end(index, step, entry, n_actions)
                    break
            self._log_step_end(index, step, entry, n_actions)
            if entry["status"] == "done":
                cp = {"step_index": index, "step_id": step["id"], "url": session.page.url if session.page else None, "ts": iso()}
                if self.checkpoint_storage and self.state_path is not None:
                    try:
                        cp["storage_state"] = str(self.state_path.with_name(self.state_path.stem + ".storage.json"))
                        session.save_state(cp["storage_state"])
                    except Exception:  # noqa: BLE001
                        cp.pop("storage_state", None)
                state["checkpoint"] = cp
            self._save(state)
        if state["status"] == "running":
            state["status"] = "done" if all(s["status"] in ("done", "skipped") for s in state["steps"]) else "failed"
        self._save(state)
        return {"workflow": state["workflow"], "version": self.version, "ok": state["status"] == "done",
                "status": state["status"], "resumed": resumed, "resumes": state.get("resumes", 0),
                "steps": results, "variables": {k: v for k, v in self.variables.items() if not k.startswith("_")},
                "duration_ms": round(mono_ms() - t0), "state_path": str(self.state_path) if self.state_path else None}


    def _log_step_end(self, index: int, step: dict, entry: dict, n_actions: int) -> None:
        acts = [a["id"] for a in self.session._actions[n_actions:]]
        self.session._jlog("info" if entry["status"] in ("done", "skipped") else "error", "step_end",
                           f"step {index + 1} {step['id']} {entry['status']}", step=index + 1, step_id=step["id"],
                           step_action=step["action"], status=entry["status"], action_ids=acts,
                           action_id=acts[-1] if acts else None, error=entry.get("error"))


def _step_summary(res: Any) -> dict:
    if not isinstance(res, dict):
        return {}
    out = {k: res.get(k) for k in ("id", "ok", "url", "status") if k in res}
    prim = (res.get("network") or {}).get("primary")
    if prim:
        out["primary"] = f"{prim.get('method')} {truncate(prim.get('url'), 100)} → {prim.get('status')}"
    return out


def resume_session_kwargs(state_path: Any) -> dict:
    """Session kwargs that restore browser state from a runner checkpoint."""
    path = Path(state_path)
    if not path.exists():
        return {}
    state = read_json(path)
    cp = state.get("checkpoint") or {}
    storage = cp.get("storage_state")
    return {"storage_state": storage} if storage and Path(storage).exists() else {}


def run_config(config: Any, *, out_dir: Any = None, resume: bool = False, headless: Optional[bool] = None,
               secrets: Optional[dict] = None, variables: Optional[dict] = None, session_kwargs: Optional[dict] = None) -> dict:
    """Execute a site config end-to-end (auth → steps → pages → outputs)."""
    cfg = load_config(config)
    problems = validate_config(cfg)
    if problems:
        raise ConfigError("invalid config: " + "; ".join(problems), details={"problems": problems})
    name = slugify(cfg.get("name") or "workflow", 40)
    out = Path(out_dir) if out_dir else Path((cfg.get("outputs") or {}).get("dir") or "agenttrace_runs") / name
    state_path = out / "state.json"
    if cfg.get("secrets_file") and Path(cfg["secrets_file"]).exists():
        secrets = {**read_json(cfg["secrets_file"]), **(secrets or {})}
    kw = dict(session_kwargs or {})
    kw.setdefault("profile", cfg.get("profile") or {})
    for key in ("rate_limit", "retry", "guardrails", "hitl", "evidence", "deterministic", "settle_quiet_ms", "debug",
                "trace", "redact", "body_policy"):
        if key in cfg:
            kw.setdefault(key, cfg[key])
    if cfg.get("noise"):
        kw.setdefault("noise_rules", NoiseRules.from_dict(cfg["noise"]))
    if cfg.get("plugins"):
        base = Path(cfg.get("_source", ".")).parent
        kw.setdefault("plugins", [str((base / p).resolve()) if not Path(p).is_absolute() else p for p in cfg["plugins"]])
    if headless is not None:
        kw["headless"] = headless
    auth = cfg.get("auth") or {}
    auth_file = Path(auth["state_file"]) if auth.get("state_file") else None
    if auth_file is not None and not auth_file.is_absolute() and cfg.get("_source"):
        auth_file = Path(cfg["_source"]).parent / auth_file
    if resume:
        kw.update(resume_session_kwargs(state_path))
    elif auth_file is not None and auth_file.exists():
        kw["storage_state"] = str(auth_file)
    kw.setdefault("name", name)
    summary: dict = {"name": name, "out_dir": str(out)}
    session = Session(out_dir=out / "session", **kw)
    with session:
        vars_ = dict(cfg.get("vars") or {})
        vars_.update(variables or {})
        if auth and not resume:
            summary["auth"] = _ensure_login(session, cfg, auth, auth_file, vars_, secrets)
        runner = WorkflowRunner(cfg, session, state_path=state_path, resume=resume, variables=vars_, secrets=secrets)
        if cfg.get("start_url") and not cfg.get("steps"):
            session.goto(cfg["start_url"])
        try:
            summary["run"] = runner.run() if cfg.get("steps") else {"ok": True, "steps": []}
        except GuardrailViolation as exc:  # stopped on purpose: report why instead of crashing
            summary["run"] = {"ok": False, "status": "stopped", "stopped_by": "guardrail", "reason": str(exc),
                              "hint": exc.hint, "details": exc.details, "steps": [], "state_path": str(state_path)}
        if cfg.get("pages"):
            summary["crawl"] = crawl(session, cfg["pages"], out_dir=out / "pages")
        data = {k: v for k, v in runner.variables.items() if isinstance(v, (list, dict)) and k not in (cfg.get("vars") or {})}
        if data:
            summary["data_path"] = str(write_json(out / "data.json", data))
    summary["session"] = session.finish()
    outputs = cfg.get("outputs") or {}
    if outputs.get("export"):
        summary["exports"] = export_all(session.recorder.records, out / "export", name=name)
    if outputs.get("redact"):
        red = Redactor().learn_records(session.recorder.records)
        summary["redacted_har"] = str(write_json(out / "session.redacted.har",
                                                 red.redact_har(session.recorder.har())))
    ok = summary["run"].get("ok", True) and (summary.get("crawl") or {}).get("failed", 0) == 0
    summary["ok"] = ok
    write_json(out / "run_summary.json", summary)
    return summary


def _ensure_login(session: Session, cfg: dict, auth: dict, auth_file: Optional[Path], variables: dict,
                  secrets: Optional[dict]) -> dict:
    check = auth.get("check") or {}
    base = cfg.get("base_url") or cfg.get("start_url")

    def logged_in() -> bool:
        if check.get("url"):
            session.goto(check["url"] if re.match(r"^[a-z]+:", check["url"]) else (base.rstrip("/") + "/" + check["url"].lstrip("/")))
        try:
            cond = {k: v for k, v in check.items() if k in ("text", "selector", "url_contains")}
            if "url_contains" in cond:
                return cond["url_contains"] in session.page.url
            if cond:
                session.wait_for(cond, timeout_ms=4000)
            return True
        except AgentTraceError:
            return False

    if auth_file is not None and auth_file.exists() and check and logged_in():
        return {"method": "saved_state", "state_file": str(auth_file)}
    if not auth.get("login"):
        return {"method": "none"}
    runner = WorkflowRunner({"name": "login", "steps": auth["login"], "base_url": base}, session,
                            variables=variables, secrets=secrets, steps_key="steps", checkpoint_storage=False)
    res = runner.run()
    if not res["ok"]:
        raise ActionError("login steps failed", details={"steps": res["steps"]})
    if check and not logged_in():
        raise ActionError("login finished but the logged-in check failed", details={"check": check})
    info = session.save_state(auth_file) if auth_file is not None else {}
    return {"method": "login", "state_file": str(auth_file) if auth_file else None, "saved": info}


# ═══════════════════════════════════════════════════════════════════════
# manual recording (Part 20)
# ═══════════════════════════════════════════════════════════════════════
RECORDER_JS = r"""
(() => {
  if (window.__atRecorderInstalled) return; window.__atRecorderInstalled = true;
  const send = (ev) => { try { ev.ts = Date.now(); ev.url = location.href; window.__atRecord(ev); } catch (e) {} };
  const txt = (el) => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const labelOf = (el) => { try { if (el.labels && el.labels.length) return txt(el.labels[0]); } catch (e) {}
    const w = el.closest && el.closest('label'); return w ? txt(w) : ''; };
  const role = (el) => { const t = el.tagName.toLowerCase(), ty = (el.getAttribute('type') || '').toLowerCase();
    if (el.getAttribute('role')) return el.getAttribute('role'); if (t === 'a') return 'link'; if (t === 'button') return 'button';
    if (t === 'select') return 'combobox'; if (t === 'textarea') return 'textbox';
    if (t === 'input') { if (['checkbox', 'radio'].includes(ty)) return ty; if (['submit', 'button', 'reset'].includes(ty)) return 'button'; if (ty === 'search') return 'searchbox'; return 'textbox'; }
    return 'generic'; };
  const css = (el) => { if (el.id && /^[A-Za-z][\w-]*$/.test(el.id)) return '#' + el.id; const parts = []; let c = el;
    while (c && c.nodeType === 1 && c !== document.body && parts.length < 6) { let p = c.tagName.toLowerCase();
      const sib = c.parentElement ? Array.from(c.parentElement.children).filter(x => x.tagName === c.tagName) : [];
      if (sib.length > 1) p += ':nth-of-type(' + (sib.indexOf(c) + 1) + ')'; parts.unshift(p); c = c.parentElement; } return parts.join(' > '); };
  const near = (el) => { const b = el.closest('li,tr,article,section,.card,.row,[class*=item],[class*=row],[class*=product],fieldset,form');
    if (!b || b === el) return ''; let t = txt(b); const own = txt(el); if (own) t = t.replace(own, ' '); return t.replace(/\s+/g, ' ').trim().slice(0, 140); };
  const anc = (el) => { const a = []; let c = el.parentElement; while (c && a.length < 4 && c !== document.body) {
    a.push(c.tagName.toLowerCase() + (c.id ? '#' + c.id : '') + (c.classList[0] ? '.' + c.classList[0] : '')); c = c.parentElement; } return a; };
  const describe = (el) => { const r = el.getBoundingClientRect(); const attrs = {};
    for (const a of ['name', 'placeholder', 'title', 'aria-label', 'href', 'data-testid', 'data-test', 'data-qa', 'data-cy', 'alt'])
      if (el.getAttribute(a) !== null) attrs[a] = el.getAttribute(a).slice(0, 200);
    const name = el.getAttribute('aria-label') || labelOf(el) || el.placeholder || txt(el).slice(0, 120) || el.value || el.title || '';
    return { tag: el.tagName.toLowerCase(), role: role(el), type: (el.getAttribute('type') || '').toLowerCase(), name: String(name).slice(0, 150),
      text: txt(el).slice(0, 150), id: el.id || '', classes: Array.from(el.classList).slice(0, 6), attrs, label: labelOf(el).slice(0, 120),
      css: css(el), near: near(el), ancestry: anc(el), bbox: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }; };
  const interactive = (el) => el && el.closest && el.closest('a,button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=tab],[role=menuitem],[onclick],label');
  const isText = (el) => el && ((el.tagName === 'INPUT' && !['checkbox', 'radio', 'submit', 'button', 'reset', 'file', 'image'].includes((el.type || '').toLowerCase())) || el.tagName === 'TEXTAREA' || el.isContentEditable);
  document.addEventListener('click', (e) => { if (e.detail === 0) return; /* keyboard/implicit-submit synthetic click: the key press is recorded */
    let el = interactive(e.target); if (!el) return;
    if (el.tagName === 'LABEL' && el.control) return; if (isText(el) || el.tagName === 'SELECT') return;
    if (el.tagName === 'INPUT' && ['checkbox', 'radio'].includes((el.type || '').toLowerCase())) return;
    send({ type: 'click', target: describe(el) }); }, true);
  document.addEventListener('input', (e) => { const el = e.target; if (!isText(el)) return;
    send({ type: 'input', target: describe(el), value: el.isContentEditable ? txt(el) : el.value, secret: (el.type || '') === 'password' }); }, true);
  document.addEventListener('change', (e) => { const el = e.target; if (!el || !el.tagName) return;
    if (el.tagName === 'SELECT') { const o = el.options[el.selectedIndex]; send({ type: 'select', target: describe(el), value: el.value, label: o ? o.text.trim() : '' }); }
    else if (['checkbox', 'radio'].includes((el.type || '').toLowerCase())) send({ type: el.checked ? 'check' : 'uncheck', target: describe(el) });
    else if (isText(el)) send({ type: 'input', target: describe(el), value: el.value, secret: (el.type || '') === 'password', final: true }); }, true);
  document.addEventListener('keydown', (e) => { if (['Enter', 'Escape', 'Tab'].includes(e.key) && e.target && e.target.tagName)
    send({ type: 'press', key: e.key, target: describe(e.target), in_text: isText(e.target) }); }, true);
})();
"""


class WorkflowRecorder:
    """Records what a human does in the (headed) browser into a replayable,
    self-healing workflow with per-step network expectations."""

    def __init__(self, session: Session, *, name: str = "recorded") -> None:
        self.session = session
        self.name = name
        self.events: list = []
        self.navigations: list = []
        self._counter = 0
        self._started = False

    def start(self, url: Optional[str] = None) -> "WorkflowRecorder":
        s = self.session
        s._ensure()
        s.context.expose_binding("__atRecord", self._on_event)
        s.context.add_init_script(RECORDER_JS)
        for p in s.recorder.open_pages():
            try:
                p.evaluate(RECORDER_JS)
            except Exception:  # noqa: BLE001
                pass
        s.page.on("framenavigated", self._on_nav)
        self._started = True
        if url:
            self.navigations.append({"ts": time.time() * 1000, "url": url, "typed": True})
            self._mark("goto")
            s.page.goto(url, wait_until="domcontentloaded")
        return self

    def _mark(self, kind: str) -> str:
        self._counter += 1
        aid = f"rec{self._counter:03d}"
        self.session.recorder.current_action = aid
        return aid

    def _on_event(self, source: Any, event: dict) -> None:
        if not isinstance(event, dict):
            return
        event["action_id"] = self._mark(event.get("type", "event"))
        event["t_ms"] = mono_ms()
        self.events.append(event)

    def _on_nav(self, frame) -> None:
        try:
            if frame != self.session.page.main_frame:
                return
            self.navigations.append({"ts": time.time() * 1000, "url": frame.url, "t_ms": mono_ms()})
        except Exception:  # noqa: BLE001
            pass

    def wait_until_closed(self, *, timeout_s: Optional[float] = None, stop: Optional[Callable[[], bool]] = None) -> None:
        """Pump events until the human closes the page/browser (or ``stop()`` is true)."""
        start = time.monotonic()
        while True:
            page = self.session.page
            if page is None or page.is_closed():
                return
            if stop is not None and stop():
                return
            if timeout_s is not None and time.monotonic() - start > timeout_s:
                return
            try:
                page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001
                return

    def stop(self) -> dict:
        """Convert raw events into workflow steps."""
        self.session.recorder.current_action = None
        steps: list = []
        secrets: dict = {}
        first_nav = self.navigations[0]["url"] if self.navigations else None
        if first_nav:
            steps.append({"id": "s001", "goto": first_nav, "_ts": float(self.navigations[0]["ts"])})
        pending_fill: Optional[dict] = None
        last_ts = 0.0
        committed: dict = {}

        def target_of(d: dict) -> dict:
            el = ElementInfo.from_js({**d, "ref": "rec"}, "", 0, 0)
            return {"selector": d.get("css"), "text": d.get("name") or d.get("text"), "fingerprint": el.fingerprint()}

        def flush() -> None:
            nonlocal pending_fill
            if pending_fill is not None:
                committed[pending_fill["_key"]] = pending_fill["fill"]["text"]
                steps.append(pending_fill)
                pending_fill = None

        for ev in self.events:
            et = ev.get("type")
            tgt = ev.get("target") or {}
            key = tgt.get("css")
            ts = float(ev.get("ts") or 0)
            if et == "input":
                if pending_fill is not None and pending_fill["_key"] != key:
                    flush()
                value = ev.get("value", "")
                if pending_fill is None and committed.get(key) == value and ev.get("final"):
                    continue  # 'change' fired after the value was already committed (e.g. Enter)
                if ev.get("secret"):
                    sname = slugify(tgt.get("attrs", {}).get("name") or tgt.get("name") or "password", 30).replace("-", "_").upper()
                    secrets[sname] = value
                    value = f"${{secret:{sname}}}"
                if pending_fill is None:
                    pending_fill = {"fill": {"target": target_of(tgt), "text": value}, "_key": key,
                                    "_action_ids": [ev["action_id"]], "_ts": ts}
                else:
                    pending_fill["fill"]["text"] = value
                    pending_fill["_action_ids"].append(ev["action_id"])
                continue
            if et == "press":
                if ev.get("key") == "Tab":
                    continue
                if ev.get("key") == "Enter" and pending_fill is not None and pending_fill["_key"] == key:
                    pending_fill["_action_ids"].append(ev["action_id"])
                    flush()
                    steps.append({"press": {"key": "Enter", "target": target_of(tgt)}, "_action_ids": [ev["action_id"]], "_ts": ts})
                    continue
                flush()
                steps.append({"press": {"key": ev.get("key"), "target": target_of(tgt)}, "_action_ids": [ev["action_id"]], "_ts": ts})
                continue
            flush()
            if et == "click":
                steps.append({"click": target_of(tgt), "_action_ids": [ev["action_id"]], "_ts": ts})
            elif et == "select":
                steps.append({"select": {"target": target_of(tgt), "value": ev.get("value"), "label": ev.get("label")},
                              "_action_ids": [ev["action_id"]], "_ts": ts})
            elif et in ("check", "uncheck"):
                steps.append({et: target_of(tgt), "_action_ids": [ev["action_id"]], "_ts": ts})
            last_ts = ev.get("ts", last_ts)
        flush()
        # typed-URL navigations (not caused by a click/press within 2.5s) become goto steps, in time order
        action_times = [float(ev.get("ts", 0)) for ev in self.events
                        if ev.get("type") in ("click", "press", "select", "check", "uncheck", "input")]
        start_ts = float(self.navigations[0]["ts"]) if self.navigations else 0.0
        prev_url = first_nav
        for nav in self.navigations[1:]:
            url = nav.get("url", "")
            caused = any(0 <= nav["ts"] - t <= 2500 for t in action_times)
            duplicate_start = url.split("#")[0] == (prev_url or "").split("#")[0] and nav["ts"] - start_ts < 5000
            if not caused and not duplicate_start and url.startswith("http"):
                steps.append({"goto": url, "_ts": float(nav["ts"])})
            prev_url = url
        steps.sort(key=lambda st: st.get("_ts", 0.0))
        # expectations from the network each step caused
        recs = self.session.recorder.records
        downloads = {d.get("action_id") for d in self.session.recorder.downloads}
        for step in steps:
            ids = step.get("_action_ids") or []
            if any(i in downloads for i in ids) and "click" in step:
                step["download"] = step.pop("click")
            for aid in ids[-1:]:
                corr = correlate(recs, action_id=aid, action_start=min([r.t_start for r in recs if r.action_id == aid] or [0]))
                prim = corr["primary"]
                if prim is not None and prim.category in ("api", "document") and prim.status and prim.status < 400:
                    step["expect"] = {"url": prim.path, "method": prim.method,
                                      "status": f"{str(prim.status)[0]}xx"}
        clean_steps = []
        for i, step in enumerate(steps, start=1):
            step = {k: v for k, v in step.items() if not k.startswith("_")}
            step["id"] = f"s{i:03d}"
            clean_steps.append(step)
        workflow = {"name": self.name, "start_url": first_nav, "recorded_at": iso(), "steps": clean_steps}
        return {"workflow": workflow, "secrets": secrets, "raw_events": len(self.events)}


def save_workflow(result: dict, path: Any) -> dict:
    target = Path(path)
    wf = write_json(target, result["workflow"])
    out = {"workflow": str(wf), "steps": len(result["workflow"]["steps"])}
    if result.get("secrets"):
        sec = write_json(target.with_name(target.stem + ".secrets.json"), result["secrets"])
        out["secrets"] = str(sec)
        out["note"] = "keep the .secrets.json file private (add it to .gitignore)"
    return out


# ═══════════════════════════════════════════════════════════════════════
# replay (Part 21)
# ═══════════════════════════════════════════════════════════════════════
def replay(workflow: Any, *, times: int = 1, secrets: Optional[dict] = None, out_dir: Any = None,
           session_factory: Optional[Callable[[int], Session]] = None, **session_kwargs: Any) -> dict:
    """Replay a workflow ``times`` times; every run validates each step's expectations."""
    wf = load_config(workflow)
    if secrets is None and isinstance(workflow, (str, Path)):
        sec_path = Path(workflow).with_name(Path(workflow).stem + ".secrets.json")
        if sec_path.exists():
            secrets = read_json(sec_path)
    base = Path(out_dir) if out_dir else Path("agenttrace_runs") / f"replay-{slugify(wf.get('name', 'wf'), 30)}"
    runs = []
    for i in range(1, times + 1):
        session = session_factory(i) if session_factory else Session(out_dir=base / f"run{i:02d}", name=f"replay{i}",
                                                                      **session_kwargs)
        with session:
            runner = WorkflowRunner(wf, session, secrets=secrets)
            try:
                res = runner.run()
            except AgentTraceError as exc:
                res = {"ok": False, "steps": [], "error": str(exc)}
            order = [s["id"] for s in res.get("steps", []) if s.get("status") == "done"]
            validations = []
            for s in res.get("steps", []):
                r = s.get("result") or {}
                if isinstance(r, dict) and r.get("validation") is not None:
                    validations.append({"step": s["id"], "ok": r["validation"]["ok"]})
            fp = run_fingerprint(session.actions(), session.recorder.records)
        runs.append({"run": i, "ok": res.get("ok", False), "step_order": order, "validations": validations,
                     "fingerprint": fp["hash"], "out_dir": str(session.out_dir),
                     "failed_steps": [s for s in res.get("steps", []) if s.get("status") == "failed"]})
    orders = [tuple(r["step_order"]) for r in runs]
    return {"workflow": wf.get("name"), "times": times, "passed": sum(1 for r in runs if r["ok"]),
            "consistent_order": len(set(orders)) == 1, "runs": runs}


# ═══════════════════════════════════════════════════════════════════════
# natural-language goals (Part 35)
# ═══════════════════════════════════════════════════════════════════════
_URL = r"(?P<url>https?://\S+|www\.\S+|[\w-]+(?:\.[\w-]+)+(?:/\S*)?)"
_Q = r"[\"'“”‘’]"
_GOAL_RULES: list = [
    (rf"^(?:go to|open|visit|navigate to|browse to|load)\s+{_URL}$", lambda m: [{"goto": m.group("url")}]),
    (r"^(?:log ?in|sign ?in)(?:\s+as|\s+with\s+(?:username|user|email))?\s+(?P<user>\S+?)\s+(?:with|and|using)\s+(?:the\s+)?(?:password|pass)\s+(?P<pw>\S+)$",
     lambda m: [{"click": "Login"}, {"fill": {"target": "username", "text": m.group("user").strip("\"'")}},
                {"fill": {"target": "password", "text": m.group("pw").strip("\"'")}}, {"click": "Sign in"}]),
    (rf"^search(?:\s+for)?\s+{_Q}?(?P<q>.+?){_Q}?$", lambda m: [{"fill": {"target": "search", "text": m.group("q"), "submit": True}}]),
    (r"^(?:set|change)\s+(?:the\s+)?(?P<field>quantity|qty|size|colou?r|amount)\s+to\s+(?P<value>\S+)$",
     lambda m: [{"select": {"target": m.group("field"), "value": m.group("value")}}]),
    (rf"^(?:fill|type|enter|put)\s+{_Q}?(?P<value>.+?){_Q}?\s+(?:in|into)\s+(?:the\s+)?(?P<field>.+?)(?:\s+(?:field|box|input))?$",
     lambda m: [{"fill": {"target": m.group("field"), "text": m.group("value")}}]),
    (rf"^(?:fill|set)\s+(?:the\s+)?(?P<field>.+?)\s+(?:field\s+)?(?:with|to)\s+{_Q}?(?P<value>.+?){_Q}?$",
     lambda m: [{"fill": {"target": m.group("field"), "text": m.group("value")}}]),
    (rf"^(?:select|choose|pick)\s+{_Q}?(?P<value>.+?){_Q}?\s+(?:from|in|as)\s+(?:the\s+)?(?P<field>.+?)(?:\s+dropdown)?$",
     lambda m: [{"select": {"target": m.group("field"), "value": m.group("value")}}]),
    (r"^(?:uncheck|untick|disable)\s+(?:the\s+)?(?P<t>.+)$", lambda m: [{"uncheck": m.group("t")}]),
    (r"^(?:check|tick|enable|accept)\s+(?:the\s+)?(?P<t>.+?)(?:\s+checkbox)?$", lambda m: [{"check": m.group("t")}]),
    (r"^add\s+(?:it|this|that|the\s+(?:item|product))?\s*(?:to\s+(?:the\s+)?(?P<w>cart|basket|bag))$", lambda m: [{"click": "Add to cart"}]),
    (r"^add\s+(?:it|this|that|the\s+(?:item|product))?\s*to\s+(?:the\s+|my\s+)?wishlist$", lambda m: [{"click": "wishlist"}]),
    (r"^(?:go to |open |click )?(?:the )?next page$", lambda m: [{"click": "next"}]),
    (r"^(?:open|click|go to|view|select)\s+(?:on\s+)?(?:the\s+)?(?P<ord>first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))\s+(?P<thing>.+)$",
     lambda m: [{"click": f"{m.group('ord')} {m.group('thing')}"}]),
    (r"^(?:download|export)\s+(?:the\s+|my\s+)?(?P<what>.+)$", lambda m: [{"download": m.group("what")}]),
    (r"^(?:take|capture|grab)\s+(?:a\s+)?screenshot$", lambda m: [{"screenshot": None}]),
    (r"^scroll(?:\s+down)?(?:\s+to\s+the\s+bottom)?$", lambda m: [{"scroll": {"to": "bottom"}}]),
    (rf"^wait\s+(?:for|until)\s+{_Q}?(?P<t>.+?){_Q}?(?:\s+(?:appears|is shown|shows up|is visible))?$", lambda m: [{"wait_for": {"text": m.group("t")}}]),
    (rf"^(?:verify|ensure|check that|confirm|make sure)\s+(?:that\s+)?(?:the\s+page\s+(?:shows|says)\s+)?{_Q}?(?P<t>.+?){_Q}?(?:\s+(?:is shown|appears|is visible|is displayed|exists))?$",
     lambda m: [{"assert": {"text": m.group("t")}}]),
    (r"^(?:extract|scrape|collect|get|capture|grab|read|list)\s+(?:all\s+|the\s+)?(?P<what>.+)$",
     lambda m: [{"extract_list": {"hint": m.group("what")}, "save_as": "extracted"}]),
    (r"^(?:go\s+)?back$", lambda m: [{"back": None}]),
    (r"^submit(?:\s+the\s+form)?$", lambda m: [{"press": "Enter"}]),
    (r"^(?:go to|open|view|visit|show)\s+(?:the\s+|my\s+)?(?P<page>.+?)(?:\s+page)?$", lambda m: [{"click": m.group("page")}]),
    (r"^(?:click|press|tap|hit)\s+(?:on\s+)?(?:the\s+)?(?P<t>.+?)(?:\s+(?:button|link))?$", lambda m: [{"click": m.group("t")}]),
]


def split_goal(goal: str) -> list:
    """Split a goal into clauses on newlines, ';', '. ', 'then', ', and' (quote-aware)."""
    placeholders = {}

    def protect(m: re.Match) -> str:
        key = f"\x00{len(placeholders)}\x00"
        placeholders[key] = m.group(0)
        return key
    text = re.sub(r"\"[^\"]*\"|'[^']*'|“[^”]*”|https?://[^\s,;]*[^\s,;.)]", protect, goal)
    verbs = (r"extract|scrape|collect|click|open|go|add|search|fill|type|enter|select|choose|pick|set|verify|ensure|confirm|"
             r"download|export|take|scroll|wait|log|sign|check|tick|untick|uncheck|submit|press|view|visit|navigate|save")
    parts = re.split(r"\n+|;|\.\s+|\s*,?\s*\b(?:and then|then|after that|afterwards|finally|next,)\b\s*|,\s*(?:and\s+)?|"
                     rf"\s+and\s+(?=(?:{verbs})\b)", text, flags=re.I)
    out = []
    for part in parts:
        if not part:
            continue
        for key, value in placeholders.items():
            part = part.replace(key, value)
        part = part.strip(" .,")
        part = re.sub(r"^(?:and|please|first|now)\s+", "", part, flags=re.I).strip()
        if part:
            out.append(part)
    return out


def plan_goal(goal: str, *, start_url: Optional[str] = None) -> list:
    """Rule-based plan: natural-language goal → workflow steps (no selectors)."""
    steps: list = []
    if start_url:
        steps.append({"goto": start_url})
    for clause in split_goal(goal):
        for rx, build in _GOAL_RULES:
            m = re.match(rx, clause.strip(), re.I)
            if m:
                for st in build(m):
                    st.setdefault("comment", clause)
                    steps.append(st)
                break
        else:
            steps.append({"click": clause, "comment": f"(unparsed clause, tried as click target) {clause}"})
    return steps


def _apply_hint(fields: dict, hint: str) -> dict:
    words = set(re.findall(r"[a-z]+", (hint or "").lower()))
    wanted = {"title" if w in ("name", "names", "title", "titles", "product", "products") else
              "price" if w in ("price", "prices", "cost", "costs") else
              "url" if w in ("link", "links", "url", "urls") else
              "rating" if w in ("rating", "ratings", "stars") else
              "image" if w in ("image", "images", "photo", "photos") else None for w in words} - {None}
    if not wanted:
        return fields
    picked = {k: v for k, v in fields.items() if k in wanted}
    return picked or fields


def run_goal(goal: str, *, start_url: Optional[str] = None, session: Optional[Session] = None, out_dir: Any = None,
             planner: Optional[Callable] = None, max_steps: int = 60, **session_kwargs: Any) -> dict:
    """Execute a natural-language goal end-to-end and return a report with evidence.

    ``planner(goal, start_url) -> steps`` may replace the built-in rule-based
    planner (e.g. an LLM call); execution, capture, validation and reporting
    stay the same.
    """
    steps = planner(goal, start_url) if planner is not None else plan_goal(goal, start_url=start_url)
    own = session is None
    session = session or Session(out_dir=out_dir, name="goal", evidence="important",
                                 guardrails={"max_actions": max_steps}, **session_kwargs)
    session.start()
    results = []
    variables: dict = {}
    ok = True
    for i, raw in enumerate(steps, start=1):
        step = normalize_step(raw, i)
        try:
            if step["action"] == "extract_list":
                hint = step["params"].pop("hint", "")
                res = session.extract_list(item=step["params"].get("item"), fields=step["params"].get("fields"))
                fields = _apply_hint(res["fields"], hint)
                rows = session.extract(fields, item=res["item"]) if res["item"] else []
                variables[step.get("save_as") or "extracted"] = rows
                res = {"ok": bool(rows), "data": rows, "item": res["item"], "fields": fields}
            else:
                res = execute_step(session, step, variables)
            results.append({"step": i, "action": step["action"], "params": step["params"], "comment": raw.get("comment"),
                            "ok": res.get("ok", True) if isinstance(res, dict) else True, "result": _step_summary(res),
                            **({"data": res.get("data")} if isinstance(res, dict) and "data" in res else {})})
        except AgentTraceError as exc:
            ok = False
            results.append({"step": i, "action": step["action"], "params": step["params"], "comment": raw.get("comment"),
                            "ok": False, "error": truncate(str(exc), 300), "hint": exc.hint})
            break
    summary = session.finish() if own else {"out_dir": str(session.out_dir), "paths": {}}
    report = {"goal": goal, "ok": ok and all(r["ok"] for r in results), "plan": steps, "results": results,
              "extracted": variables.get("extracted"), "artifacts": summary.get("paths"), "out_dir": summary.get("out_dir")}
    if summary.get("out_dir"):
        write_json(Path(summary["out_dir"]) / "goal_report.json", report)
        lines = [f"# Goal report", "", f"**Goal:** {goal}", "", f"**Result:** {'SUCCESS' if report['ok'] else 'FAILED'}", "",
                 "| # | step | ok | detail |", "|---|---|---|---|"]
        for r in results:
            detail = r.get("error") or (r.get("result") or {}).get("primary") or ""
            lines.append(f"| {r['step']} | {r['action']} {truncate(json.dumps(r['params']), 50)} | {'✅' if r['ok'] else '❌'} | {truncate(detail, 80)} |")
        if report["extracted"]:
            lines += ["", "## Extracted data", "", "```json", json.dumps(report["extracted"][:10], indent=1, ensure_ascii=False), "```"]
        lines += ["", "## Evidence", ""] + [f"- {k}: `{v}`" for k, v in (report["artifacts"] or {}).items()]
        write_text(Path(summary["out_dir"]) / "goal_report.md", "\n".join(lines) + "\n")
    return report


def run_agent_loop(goal: str, decide: Callable[[str, dict, list], Optional[dict]], *, start_url: Optional[str] = None,
                   max_steps: int = 30, **session_kwargs: Any) -> dict:
    """observe → decide → act loop for LLM planners.

    ``decide(goal, observation, history)`` returns the next tool call
    ``{"tool": "click", "args": {...}}`` or ``None`` when done.  Guardrails
    stop runaway loops.
    """
    history: list = []
    with Session(name="agent", guardrails={"max_actions": max_steps}, strict=False, **session_kwargs) as s:
        if start_url:
            s.goto(start_url)
        for _ in range(max_steps):
            obs = s.observe(limit=60)
            call = decide(goal, obs, history)
            if not call:
                break
            try:
                result = dispatch_tool(s, call["tool"], call.get("args") or {})
            except GuardrailViolation as exc:
                history.append({"call": call, "result": exc.to_dict()})
                break
            except AgentTraceError as exc:
                result = exc.to_dict()
            history.append({"call": call, "result": result})
        summary = s.finish()
    return {"goal": goal, "steps": len(history), "history": history, "artifacts": summary.get("paths")}



# ════════════════════════════════════════════════════════════════════════════
# artifacts.py — project isolation, artifact store, manifests, run history (Parts 31, 48)
# ════════════════════════════════════════════════════════════════════════════
# Project isolation (Part 31) and artifact storage with manifests and
# versioned execution history (Part 48).
#
# Layout::
#
#     <store>/index.json                      history of every run (all workflows)
#     <store>/<workflow>/<run_id>/manifest.json
#     <store>/<workflow>/<run_id>/session/...  HARs, screenshots, logs, reports


_INDEX_LOCK = threading.Lock()

KIND_RULES = [
    (".har", "har"), (".png", "screenshot"), (".jpg", "screenshot"), ("dom.html", "dom"), ("log.jsonl", "log"),
    ("timeline.jsonl", "timeline"), ("report.md", "report"), ("report.json", "report"), ("endpoints.json", "api"),
    ("endpoints.md", "api"), ("manifest.json", "manifest"), ("meta.json", "evidence-meta"), ("trace.zip", "trace"),
    ("console.json", "console"), ("pages.json", "lifecycle"), ("streams.json", "streams"),
    ("websockets.json", "websockets"), ("data.json", "data"), ("state.json", "state"), ("workflow.json", "workflow"),
    ("fingerprint.json", "fingerprint"),
]


def artifact_kind(path: str) -> str:
    low = path.lower()
    if "/downloads/" in low or low.startswith("downloads/"):
        return "download"
    for suffix, kind in KIND_RULES:
        if low.endswith(suffix):
            return kind
    return "other"


def _action_of(path: str) -> Optional[str]:
    parts = path.replace("\\", "/").split("/")
    for i, part in enumerate(parts):
        if part in ("evidence", "actions") and i + 1 < len(parts):
            name = parts[i + 1]
            return name.split("_", 1)[0] if name.startswith("a") else None
    return None


class _FileLock:
    """Cross-process lock via O_EXCL lock file (works on Windows and POSIX)."""

    def __init__(self, path: Path, timeout: float = 30.0) -> None:
        self.path = path
        self.timeout = timeout

    def __enter__(self) -> "_FileLock":
        start = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() - start > self.timeout:
                    try:  # stale lock
                        if time.time() - self.path.stat().st_mtime > 120:
                            self.path.unlink()
                            continue
                    except OSError:
                        pass
                    raise TimeoutError(f"could not lock {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


class RunHandle:
    def __init__(self, store: "ArtifactStore", workflow: str, version: str, run_id: str, definition: Any,
                 tags: Iterable[str]) -> None:
        self.store = store
        self.workflow = workflow
        self.version = version
        self.run_id = run_id
        self.definition = definition
        self.tags = list(tags)
        self.dir = store.root / workflow / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.started = iso()
        self.extra: list = []
        if definition is not None:
            write_json(self.dir / "workflow.json", definition)

    def session(self, **kwargs: Any) -> Session:
        kwargs.setdefault("name", self.workflow)
        return Session(out_dir=self.dir / "session", **kwargs)

    def add(self, path: Any, kind: Optional[str] = None, *, action_id: Optional[str] = None, meta: Optional[dict] = None) -> None:
        self.extra.append({"path": str(Path(path)), "kind": kind, "action_id": action_id, "meta": meta or {}})

    def finalize(self, status: str = "done", *, fingerprint: Optional[dict] = None, summary: Optional[dict] = None) -> dict:
        artifacts = []
        for f in sorted(self.dir.rglob("*")):
            if not f.is_file() or f.name == "manifest.json" or f.name.startswith("."):
                continue
            rel = str(f.relative_to(self.dir)).replace("\\", "/")
            data = f.read_bytes()
            item = {"path": rel, "kind": artifact_kind("/" + rel), "bytes": len(data), "sha256": sha256_hex(data)}
            act = _action_of(rel)
            if act:
                item["action_id"] = act
            artifacts.append(item)
        for extra in self.extra:
            for a in artifacts:
                if Path(extra["path"]).name == Path(a["path"]).name and extra.get("kind"):
                    a["kind"] = extra["kind"]
                    if extra.get("action_id"):
                        a["action_id"] = extra["action_id"]
        manifest = {"run_id": self.run_id, "workflow": self.workflow, "version": self.version, "status": status,
                    "started": self.started, "ended": iso(), "tags": self.tags, "fingerprint": (fingerprint or {}).get("hash"),
                    "summary": summary or {}, "artifacts": artifacts,
                    "counts": {k: sum(1 for a in artifacts if a["kind"] == k) for k in sorted({a["kind"] for a in artifacts})}}
        write_json(self.dir / "manifest.json", manifest)
        if fingerprint:
            write_json(self.dir / "fingerprint.json", fingerprint)
        self.store._index_add({"run_id": self.run_id, "workflow": self.workflow, "version": self.version,
                               "status": status, "started": self.started, "ended": manifest["ended"],
                               "path": str(self.dir.relative_to(self.store.root)).replace("\\", "/"),
                               "fingerprint": manifest["fingerprint"], "artifacts": len(artifacts)})
        return manifest


class ArtifactStore:
    def __init__(self, root: Any = "agenttrace_store") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def _index(self) -> dict:
        if self.index_path.exists():
            try:
                return read_json(self.index_path)
            except (ValueError, OSError):
                pass
        return {"runs": []}

    def _index_add(self, entry: dict) -> None:
        with _INDEX_LOCK, _FileLock(self.root / ".index.lock"):
            idx = self._index()
            idx["runs"] = [r for r in idx["runs"] if r["run_id"] != entry["run_id"]] + [entry]
            idx["updated"] = iso()
            write_json(self.index_path, idx)

    def start_run(self, workflow: str, definition: Any = None, *, tags: Iterable[str] = ()) -> RunHandle:
        wf = slugify(workflow, 50, "workflow")
        version = sha256_hex(dump_json(definition, indent=None))[:12] if definition is not None else "unversioned"
        run_id = new_id("run")
        return RunHandle(self, wf, version, run_id, definition, tags)

    def runs(self, workflow: Optional[str] = None) -> list:
        runs = self._index()["runs"]
        if workflow:
            runs = [r for r in runs if r["workflow"] == slugify(workflow, 50, "workflow")]
        return sorted(runs, key=lambda r: r["started"])

    def run_dir(self, run_id: str) -> Path:
        for r in self._index()["runs"]:
            if r["run_id"] == run_id:
                return self.root / r["path"]
        matches = list(self.root.glob(f"*/{run_id}"))
        if matches:
            return matches[0]
        raise ConfigError(f"unknown run id {run_id!r}")

    def manifest(self, run_id: str) -> dict:
        return read_json(self.run_dir(run_id) / "manifest.json")

    def locate(self, run_id: str, kind: Optional[str] = None, action_id: Optional[str] = None) -> list:
        base = self.run_dir(run_id)
        return [str(base / a["path"]) for a in self.manifest(run_id)["artifacts"]
                if (kind is None or a["kind"] == kind) and (action_id is None or a.get("action_id") == action_id)]

    def verify(self, run_id: str) -> dict:
        base = self.run_dir(run_id)
        bad = []
        man = self.manifest(run_id)
        for a in man["artifacts"]:
            f = base / a["path"]
            if not f.exists():
                bad.append({"path": a["path"], "problem": "missing"})
            elif sha256_hex(f.read_bytes()) != a["sha256"]:
                bad.append({"path": a["path"], "problem": "hash mismatch"})
        return {"run_id": run_id, "ok": not bad, "checked": len(man["artifacts"]), "problems": bad}

    def history(self, workflow: str) -> dict:
        runs = self.runs(workflow)
        versions: dict = {}
        for r in runs:
            versions.setdefault(r["version"], []).append(r["run_id"])
        return {"workflow": workflow, "runs": len(runs), "versions": versions,
                "timeline": [(r["started"], r["run_id"], r["version"], r["status"]) for r in runs]}

    def compare(self, run_a: str, run_b: str) -> dict:
        out: dict = {"a": run_a, "b": run_b}
        da, db = self.run_dir(run_a), self.run_dir(run_b)
        for name in ("session/endpoints.json",):
            fa, fb = da / name, db / name
            if fa.exists() and fb.exists():
                out["api"] = compare_endpoints(read_json(fa), read_json(fb))
        fa, fb = da / "fingerprint.json", db / "fingerprint.json"
        if fa.exists() and fb.exists():
            out["fingerprint"] = diff_fingerprints(read_json(fa), read_json(fb))
        return out


class Project:
    """Isolated workspace: own browser profile, auth state, runs and exports."""

    def __init__(self, name: str, root: Any = "agenttrace_projects", profile: Optional[dict] = None) -> None:
        self.name = slugify(name, 50, "project")
        self.dir = Path(root) / self.name
        for sub in ("state", "runs", "exports"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.profile = dict(profile or {})
        cfg = self.dir / "project.json"
        if cfg.exists() and not profile:
            self.profile = read_json(cfg).get("profile", {})
        else:
            write_json(cfg, {"name": self.name, "profile": self.profile, "created": iso()})

    @property
    def state_file(self) -> Path:
        return self.dir / "state" / "auth.json"

    def session(self, **kwargs: Any) -> Session:
        prof = dict(self.profile)
        prof.update(kwargs.pop("profile", {}) or {})
        if self.state_file.exists():
            kwargs.setdefault("storage_state", str(self.state_file))
        kwargs.setdefault("name", self.name)
        return Session(profile=prof, out_dir=self.dir / "runs" / new_id(self.name), **kwargs)

    def save_state(self, session: Session) -> dict:
        return session.save_state(self.state_file)

    def runs(self) -> list:
        return sorted(p.name for p in (self.dir / "runs").iterdir() if p.is_dir())


def fingerprint_session(session: Session) -> dict:
    return run_fingerprint(session.actions(), session.recorder.records)


def fingerprint_har(path: Any, actions: Optional[list] = None) -> dict:
    return run_fingerprint(actions or [], har_to_records(path))



# ════════════════════════════════════════════════════════════════════════════
# fixtures.py — test fixtures: realistic sites for the self-test (shared helpers)
# ════════════════════════════════════════════════════════════════════════════
# Deterministic, realistic local test websites (used by ``selftest``).
#
# ``FixtureServer`` serves several virtual hosts from one local HTTP server;
# the browser is pointed at it with Chromium host-resolver rules, so pages use
# *real-looking* hostnames without touching the internet:
#
# * ``shop.agenttrace.test``  — "ShopLab": catalogue (400 products / 20 pages),
#   product pages with JSON-LD, cart, checkout, login with CSRF, account +
#   CSV/JSON/PDF exports, search with suggestions, infinite-scroll feed,
#   load-more deals, REST ``/api/v1/*`` + GraphQL — and a realistic tracker
#   stack (GA4/GTM, Facebook pixel, Hotjar, Segment, DoubleClick, Sentry,
#   New Relic, Bing, LinkedIn, TikTok, Clarity, OneTrust, Cloudflare RUM)
#   served on their **real** hostnames (mapped to the fixture);
# * ``cdn.agenttrace.test`` — CSS/JS/images;
# * ``lab.agenttrace.test`` — focused scenario pages (slow APIs, flaky
#   endpoints, CAPTCHA wall, rate limits, SSE, WebSocket, frames/popups,
#   downloads, console errors, body integrity, mocking …);
# * ``spa.agenttrace.test`` (React Router SPA), ``vault.agenttrace.test``
#   (cookie + localStorage + IndexedDB app), ``news.agenttrace.test``
#   (WordPress-like second site).


FIXTURE_DOMAIN = "agenttrace.test"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ═══════════════════════════════════════════════════════════════════════
# small helpers
# ═══════════════════════════════════════════════════════════════════════
def tpl(text: str, **vals: Any) -> str:
    for key, value in vals.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def png_bytes(width: int = 8, height: int = 8, rgb: tuple = (200, 80, 40)) -> bytes:
    """Tiny valid PNG (solid colour)."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def pdf_bytes(title: str, lines: list) -> bytes:
    """Minimal valid single-page PDF with text lines."""
    text_ops = ["BT", "/F1 16 Tf", "50 780 Td", f"({title}) Tj", "/F1 11 Tf"]
    for line in lines:
        safe = str(line).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        text_ops += ["0 -18 Td", f"({safe}) Tj"]
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def fake_jwt(sub: str, secret: str = "shoplab-secret") -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": sub, "iat": 1760000000, "scope": "shop"}).encode())
    sig = b64(hashlib.sha256(f"{header}.{payload}.{secret}".encode()).digest())
    return f"{header}.{payload}.{sig}"


# ═══════════════════════════════════════════════════════════════════════
# deterministic catalogue
# ═══════════════════════════════════════════════════════════════════════
_ADJ = ["Walnut", "Linen", "Copper", "Ceramic", "Velvet", "Oak", "Marble", "Bamboo", "Wool", "Glass",
        "Brass", "Cotton", "Leather", "Rattan", "Stone", "Maple", "Silk", "Jute", "Teak", "Terrazzo"]
_NOUNS = {
    "lighting": ["Desk Lamp", "Floor Lamp", "Pendant Light", "Wall Sconce", "Table Lamp"],
    "textiles": ["Throw Pillow", "Blanket", "Rug", "Curtain", "Table Runner"],
    "kitchen": ["Mug", "Serving Bowl", "Cutting Board", "Teapot", "Salad Spoon"],
    "furniture": ["Side Table", "Bookshelf", "Stool", "Armchair", "Bench"],
    "decor": ["Vase", "Picture Frame", "Candle Holder", "Mirror", "Wall Clock"],
    "office": ["Notebook", "Pen Holder", "Desk Organizer", "Monitor Stand", "Paper Tray"],
    "garden": ["Planter", "Watering Can", "Bird Feeder", "Garden Stool", "Plant Stand"],
    "bath": ["Towel Set", "Soap Dispenser", "Bath Mat", "Storage Basket", "Shower Caddy"],
}
_RATING_WORDS = ["Zero", "One", "Two", "Three", "Four", "Five"]


class Catalog:
    def __init__(self, count: int = 400, seed: int = 20260925) -> None:
        rng = random.Random(seed)
        cats = list(_NOUNS)
        self.products: list = []
        seen: dict = {}
        for pid in range(1, count + 1):
            cat = cats[(pid - 1) % len(cats)]
            noun = _NOUNS[cat][((pid - 1) // len(cats)) % len(_NOUNS[cat])]
            adj = _ADJ[((pid - 1) // (len(cats) * len(_NOUNS[cat]))) % len(_ADJ)]
            name = f"{adj} {noun}"
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                name = f"{name} {['', '', 'II', 'III', 'IV', 'V', 'VI'][min(seen[name], 6)]}".strip()
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            price = round(rng.uniform(6, 240), 2)
            self.products.append({
                "id": pid, "name": name, "slug": slug, "category": cat, "price": price,
                "rating": rng.randint(1, 5), "stock": rng.randint(0, 40), "featured": pid % 17 == 0,
                "sku": f"SL-{pid:05d}",
                "description": f"The {name.lower()} is made from {adj.lower()} and designed for everyday "
                               f"{cat}. Handmade in small batches.",
            })
        self.by_id = {p["id"]: p for p in self.products}
        self.categories = cats

    def url(self, p: dict) -> str:
        return f"/product/{p['slug']}_{p['id']}/"

    def search(self, q: str) -> list:
        terms = [t for t in re.split(r"\W+", (q or "").lower()) if t]
        if not terms:
            return []
        return [p for p in self.products if all(t in p["name"].lower() or t in p["category"] for t in terms)]


# ═══════════════════════════════════════════════════════════════════════
# request / response plumbing
# ═══════════════════════════════════════════════════════════════════════
class Req:
    def __init__(self, method: str, host: str, target: str, headers: dict, body: bytes, client: str) -> None:
        self.method = method
        self.host = host
        parts = urlsplit(target)
        self.path = parts.path or "/"
        self.query = parse_qs(parts.query, keep_blank_values=True)
        self.raw_query = parts.query
        self.headers = headers
        self.body = body
        self.client = client
        self.cookies: dict = {}
        for part in headers.get("cookie", "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep:
                self.cookies[name] = value

    def q(self, key: str, default: str = "") -> str:
        values = self.query.get(key)
        return values[0] if values else default

    def qi(self, key: str, default: int = 0) -> int:
        try:
            return int(self.q(key, str(default)))
        except ValueError:
            return default

    @property
    def form(self) -> dict:
        ct = self.headers.get("content-type", "")
        if "x-www-form-urlencoded" in ct:
            return dict(parse_qsl(self.body.decode("utf-8", "replace"), keep_blank_values=True))
        if "multipart/form-data" in ct:
            out = {}
            for part in parse_multipart(self.body, ct):
                out[part["name"]] = part if part["filename"] is not None else part["data"].decode("utf-8", "replace")
            return out
        return {}

    @property
    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8")) if self.body else None
        except ValueError:
            return None


class Resp:
    def __init__(self, status: int = 200, body: Any = b"", ctype: str = "text/html; charset=utf-8",
                 headers: Optional[list] = None) -> None:
        self.status = status
        self.body = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        self.headers = [("Content-Type", ctype)] if ctype else []
        self.headers += list(headers or [])
        self.stream: Optional[Callable] = None  # fn(write, flush) for streaming bodies
        self.mode = "normal"  # normal | reset | truncate | stream
        self.delay = 0.0

    def cookie(self, name: str, value: str, *, http_only: bool = True, path: str = "/", max_age: Optional[int] = None,
               same_site: str = "Lax") -> "Resp":
        attrs = [f"{name}={value}", f"Path={path}", f"SameSite={same_site}"]
        if http_only:
            attrs.append("HttpOnly")
        if max_age is not None:
            attrs.append(f"Max-Age={max_age}")
        self.headers.append(("Set-Cookie", "; ".join(attrs)))
        return self

    def header(self, name: str, value: str) -> "Resp":
        self.headers.append((name, value))
        return self


def jresp(obj: Any, status: int = 200, headers: Optional[list] = None) -> Resp:
    return Resp(status, json.dumps(obj), "application/json; charset=utf-8", headers)


def redirect(location: str, status: int = 302) -> Resp:
    return Resp(status, b"", "text/plain", [("Location", location)])


GIF_1X1 = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


# ═══════════════════════════════════════════════════════════════════════
# third-party tracker stack (served on real hostnames)
# ═══════════════════════════════════════════════════════════════════════
NOISE_SNIPPET = """
<script async src="http://www.googletagmanager.com/gtag/js?id=G-SHOPLAB1"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-SHOPLAB1');</script>
<script async src="http://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>
<script async src="http://connect.facebook.net/en_US/fbevents.js"></script>
<script async src="http://static.hotjar.com/c/hotjar-3141592.js?sv=6"></script>
<script async src="http://cdn.segment.com/analytics.js/v1/wk_shoplab/analytics.min.js"></script>
<script async src="http://securepubads.g.doubleclick.net/tag/js/gpt.js"></script>
<script async src="http://browser.sentry-cdn.com/7.119.0/bundle.min.js"></script>
<script async src="http://js-agent.newrelic.com/nr-loader-spa-1.260.0.min.js"></script>
<script async src="http://bat.bing.com/bat.js"></script>
<script async src="http://snap.licdn.com/li.lms-analytics/insight.min.js"></script>
<script async src="http://analytics.tiktok.com/i18n/pixel/events.js?sdkid=SHOPLAB"></script>
<script async src="http://www.clarity.ms/tag/shoplab42"></script>
<script async src="http://cdn.cookielaw.org/scripttemplates/otSDKStub.js"></script>
<script defer src="http://static.cloudflareinsights.com/beacon.min.js"></script>
"""

NOISE_HOSTS = (
    "www.googletagmanager.com", "www.google-analytics.com", "region1.google-analytics.com", "connect.facebook.net",
    "www.facebook.com", "static.hotjar.com", "script.hotjar.com", "in.hotjar.com", "cdn.segment.com",
    "api.segment.io", "securepubads.g.doubleclick.net", "tpc.googlesyndication.com", "browser.sentry-cdn.com",
    "o450000.ingest.sentry.io", "js-agent.newrelic.com", "bam.nr-data.net", "bat.bing.com", "snap.licdn.com",
    "px.ads.linkedin.com", "analytics.tiktok.com", "www.clarity.ms", "k.clarity.ms", "cdn.cookielaw.org",
    "static.cloudflareinsights.com", "cloudflareinsights.com",
)

_JS = {
    ("www.googletagmanager.com", "/gtag/js"): r"""
(function(){var tid=(document.currentScript&&new URL(document.currentScript.src).searchParams.get('id'))||'G-X';
function hit(en,x){var u='http://www.google-analytics.com/g/collect?v=2&tid='+tid+'&cid=555.'+Math.floor(Date.now()/1000)+'&en='+en+'&dl='+encodeURIComponent(location.href)+(x||'');
try{if(!navigator.sendBeacon(u)){new Image().src=u}}catch(e){new Image().src=u}}
hit('page_view');var n=0;var t=setInterval(function(){n++;hit('user_engagement','&_et='+(n*2000));if(n>=3)clearInterval(t)},2000);
document.addEventListener('click',function(e){var a=e.target&&e.target.closest&&e.target.closest('a,button');if(a)hit('click','&ep.text='+encodeURIComponent((a.textContent||'').trim().slice(0,30)))},true);})();""",
    ("www.googletagmanager.com", "/gtm.js"): r"""
(function(){window.google_tag_manager=window.google_tag_manager||{};var i=new Image();i.src='http://www.google-analytics.com/j/collect?v=1&t=pageview&tid=UA-1234-1&dl='+encodeURIComponent(location.href);})();""",
    ("connect.facebook.net", "/en_US/fbevents.js"): r"""
(function(){window.fbq=window.fbq||function(){};var i=new Image();i.src='http://www.facebook.com/tr?id=1234567890&ev=PageView&dl='+encodeURIComponent(location.href)+'&ts='+Date.now();})();""",
    ("static.hotjar.com", "/c/"): r"""
(function(){var s=document.createElement('script');s.async=true;s.src='http://script.hotjar.com/modules.8f3a2b1c.js';document.head.appendChild(s);})();""",
    ("script.hotjar.com", "/modules"): r"""
(function(){fetch('http://in.hotjar.com/api/v2/client/sites/3141592/visit-data?sv=7',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({u:location.href,t:Date.now()})}).catch(function(){});})();""",
    ("cdn.segment.com", "/analytics.js/"): r"""
(function(){function send(path,obj){fetch('http://api.segment.io/v1/'+path,{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify(Object.assign({writeKey:'wk_shoplab',sentAt:new Date().toISOString()},obj))}).catch(function(){})}
send('p',{type:'page',properties:{url:location.href}});window.addEventListener('shoplab:add',function(e){send('t',{type:'track',event:'Product Added',properties:e.detail||{}})});})();""",
    ("securepubads.g.doubleclick.net", "/tag/js/gpt.js"): r"""
(function(){fetch('http://securepubads.g.doubleclick.net/gampad/ads?iu=/1234/shoplab&sz=300x250&correlator='+Date.now()).then(function(r){return r.json()}).then(function(ad){var slot=document.getElementById('ad-slot');if(slot){var img=new Image();img.src=ad.creative;img.alt='Advertisement';img.width=300;img.height=60;slot.appendChild(img);}}).catch(function(){});})();""",
    ("browser.sentry-cdn.com", "/"): r"""
(function(){window.Sentry={captureException:function(){}};fetch('http://o450000.ingest.sentry.io/api/4500000/envelope/?sentry_key=0123456789abcdef&sentry_version=7',{method:'POST',headers:{'Content-Type':'text/plain'},body:'{"type":"session","status":"ok"}'}).catch(function(){});})();""",
    ("js-agent.newrelic.com", "/"): r"""
(function(){setTimeout(function(){try{navigator.sendBeacon('http://bam.nr-data.net/events/1/NRJS-shoplab?a=987654&t=Unnamed')}catch(e){}},300);})();""",
    ("bat.bing.com", "/bat.js"): r"""
(function(){var i=new Image();i.src='http://bat.bing.com/action/0?ti=5555555&evt=pageLoad&tl='+encodeURIComponent(document.title);})();""",
    ("snap.licdn.com", "/"): r"""
(function(){var i=new Image();i.src='http://px.ads.linkedin.com/collect/?pid=4444&fmt=gif&url='+encodeURIComponent(location.href);})();""",
    ("analytics.tiktok.com", "/i18n/pixel/events.js"): r"""
(function(){fetch('http://analytics.tiktok.com/api/v2/pixel',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({event:'Pageview',url:location.href})}).catch(function(){});})();""",
    ("www.clarity.ms", "/tag/"): r"""
(function(){setTimeout(function(){fetch('http://k.clarity.ms/collect',{method:'POST',headers:{'Content-Type':'text/plain'},body:'1|'+Date.now()}).catch(function(){})},200);})();""",
    ("cdn.cookielaw.org", "/scripttemplates/otSDKStub.js"): r"""
(function(){fetch('http://cdn.cookielaw.org/consent/0190-shoplab/0190-shoplab.json').then(function(r){return r.json()}).then(function(cfg){
if(document.cookie.indexOf('OptanonAlertBoxClosed=')>=0||!document.body||document.body.dataset.consent!=='1')return;
var b=document.createElement('div');b.id='onetrust-banner-sdk';b.setAttribute('role','dialog');b.setAttribute('aria-label','Cookie consent');
b.innerHTML='<p>'+cfg.text+'</p><button id="onetrust-accept-btn-handler">Accept All Cookies</button><button id="onetrust-reject-all-handler">Reject All</button>';
document.body.appendChild(b);b.querySelectorAll('button').forEach(function(x){x.onclick=function(){document.cookie='OptanonAlertBoxClosed='+new Date().toISOString()+';path=/';b.remove()}})}).catch(function(){});})();""",
    ("static.cloudflareinsights.com", "/beacon.min.js"): r"""
(function(){window.addEventListener('load',function(){setTimeout(function(){try{navigator.sendBeacon('http://cloudflareinsights.com/cdn-cgi/rum',JSON.stringify({pageloadId:'x',location:location.href}))}catch(e){}},150)});})();""",
}


def third_party_response(req: Req) -> Resp:
    host, path = req.host, req.path
    cors = [("Access-Control-Allow-Origin", "*"), ("Cache-Control", "no-store")]
    for (h, prefix), code in _JS.items():
        if host == h and path.startswith(prefix):
            return Resp(200, code, "application/javascript", cors)
    if host == "securepubads.g.doubleclick.net" and path.startswith("/gampad/ads"):
        return jresp({"creative": "http://tpc.googlesyndication.com/simgad/9876543210", "size": "300x60"}, headers=cors)
    if host == "tpc.googlesyndication.com":
        return Resp(200, png_bytes(30, 6, (250, 200, 0)), "image/png", cors)
    if host == "fonts.googleapis.com":
        return Resp(200, "@font-face{font-family:'Inter';font-style:normal;font-weight:400;"
                         "src:url(http://fonts.gstatic.com/s/inter/v13/inter-latin.woff2) format('woff2');}",
                    "text/css", cors)
    if host == "k.clarity.ms":
        return jresp({"ok": 1}, headers=cors)
    if host == "cdn.cookielaw.org" and path.endswith(".json"):
        return jresp({"text": "We use cookies to improve your experience.", "version": "202409.1.0"}, headers=cors)
    if path.endswith((".gif",)) or "/tr" in path or "/collect" in path and req.method == "GET" or host == "bat.bing.com":
        return Resp(200, GIF_1X1, "image/gif", cors)
    if req.method in ("POST", "PUT") and host in ("in.hotjar.com", "api.segment.io", "analytics.tiktok.com",
                                                  "o450000.ingest.sentry.io"):
        return jresp({"success": True, "id": secrets.token_hex(8)}, headers=cors)
    if req.method == "OPTIONS":
        return Resp(204, b"", "", cors + [("Access-Control-Allow-Headers", "*"), ("Access-Control-Allow-Methods", "GET,POST")])
    return Resp(204, b"", "", cors)


# ═══════════════════════════════════════════════════════════════════════
# CDN assets (css / js / images)
# ═══════════════════════════════════════════════════════════════════════
SHOP_CSS = """
*{box-sizing:border-box}body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1d2330;background:#f6f7fb}
header.site{display:flex;align-items:center;gap:18px;padding:12px 28px;background:#1d2330;color:#fff}
header.site a{color:#fff;text-decoration:none}header.site nav{display:flex;gap:16px;flex:1}
.logo{font-weight:800;font-size:20px;letter-spacing:.5px}.search-form{display:flex;gap:6px}
.search-form input{padding:6px 10px;border-radius:6px;border:0;width:220px}
.btn{display:inline-block;padding:8px 14px;border-radius:6px;border:1px solid #c8cbd6;background:#fff;cursor:pointer}
.btn-primary{background:#3355ff;color:#fff;border-color:#3355ff}main{max-width:1100px;margin:22px auto;padding:0 20px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.card,.product_pod{background:#fff;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.product_pod h3{font-size:15px;margin:8px 0}.price_color,.price{font-weight:700;color:#1b7f3b}
.pager{display:flex;gap:12px;list-style:none;padding:0;justify-content:center;margin:24px 0}
.star-rating{color:#e7a300}.loading{padding:12px;text-align:center;color:#666}
.feed-item{background:#fff;margin:10px 0;padding:14px;border-radius:8px;min-height:90px}
#onetrust-banner-sdk{position:fixed;bottom:0;left:0;right:0;background:#222;color:#fff;padding:10px 20px;display:flex;gap:10px;align-items:center;z-index:50}
.suggestions{position:absolute;background:#fff;color:#000;list-style:none;margin:0;padding:0;border:1px solid #ccc;z-index:20}
.suggestions li{padding:4px 10px}.toast{position:fixed;top:12px;right:12px;background:#1b7f3b;color:#fff;padding:8px 12px;border-radius:6px}
table{border-collapse:collapse;width:100%;background:#fff}td,th{padding:8px;border-bottom:1px solid #eee;text-align:left}
footer{padding:30px;text-align:center;color:#777}
"""

SHOP_JS = r"""
(function(){
  var $=function(s,r){return (r||document).querySelector(s)};
  function csrf(){var m=document.cookie.match(/csrftoken=([^;]+)/);return m?m[1]:''}
  function api(path,opts){opts=opts||{};opts.headers=Object.assign({'Accept':'application/json'},opts.headers||{});
    return fetch(path,opts).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()})}
  function money(v){return typeof v==='number'?'£'+v.toFixed(2):String(v)}
  function toast(msg){var t=document.createElement('div');t.className='toast';t.setAttribute('role','status');t.textContent=msg;document.body.appendChild(t);setTimeout(function(){t.remove()},1500)}
  function updateCartCount(){return api('/api/v1/cart').then(function(c){var el=$('#cart-count');if(el)el.textContent=c.count;return c})}
  function addToCart(id,qty,extra){return api('/api/v1/cart',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(Object.assign({product_id:id,qty:qty||1},extra||{}))})
    .then(function(r){toast('Added to cart');window.dispatchEvent(new CustomEvent('shoplab:add',{detail:{product_id:id}}));updateCartCount();var s=$('#cart-status');if(s)s.textContent='In cart: '+r.count+' item(s)';return r})}
  function card(p){return '<article class="card product-card" data-product-id="'+p.id+'"><img src="http://cdn.agenttrace.test/img/p/'+p.id+'.png" alt="'+p.name+'" width="48" height="48">'+
      '<h3><a href="'+p.url+'">'+p.name+'</a></h3><p class="price">'+money(p.price)+'</p><button class="btn add-to-cart" data-product-id="'+p.id+'">Add to cart</button></article>'}
  function bindAdd(root){(root||document).querySelectorAll('button.add-to-cart').forEach(function(b){if(b.dataset.bound)return;b.dataset.bound='1';b.addEventListener('click',function(){addToCart(parseInt(b.dataset.productId,10),1)})})}
  function initHome(){api('/api/v1/products?featured=1&limit=8').then(function(d){$('#featured').innerHTML=d.items.map(card).join('');bindAdd($('#featured'))});
    api('/api/v1/categories').then(function(d){$('#categories').innerHTML=d.categories.map(function(c){return '<li><a href="/catalogue/category/'+c.slug+'/">'+c.name+'</a> ('+c.count+')</li>'}).join('')})}
  function initSuggest(){var input=$('#q');if(!input)return;var box=document.createElement('ul');box.className='suggestions';box.id='suggestions';input.parentNode.style.position='relative';input.parentNode.appendChild(box);var t=null;
    input.addEventListener('input',function(){clearTimeout(t);var v=input.value.trim();if(v.length<2){box.innerHTML='';return}
      t=setTimeout(function(){api('/api/v1/suggest?q='+encodeURIComponent(v)).then(function(d){box.innerHTML=d.suggestions.map(function(s){return '<li>'+s+'</li>'}).join('')})},250)})}
  function initSearch(q){var res=$('#results');res.setAttribute('aria-busy','true');res.innerHTML='<li class="loading">Searching…</li>';
    api('/api/v1/search?q='+encodeURIComponent(q)).then(function(d){res.removeAttribute('aria-busy');$('#result-count').textContent=d.total+' results for "'+q+'"';
      res.innerHTML=d.items.map(function(p){return '<li class="result"><a class="result-link" href="'+p.url+'">'+p.name+'</a> <span class="price">'+money(p.price)+'</span></li>'}).join('')||'<li class="empty">No products found</li>'})}
  function initProduct(id){var btn=$('#add-to-cart');btn.addEventListener('click',function(){var q=parseInt(($('#qty')||{}).value||'1',10);var g=$('#gift');addToCart(id,q,{gift_wrap:!!(g&&g.checked)})});
    setTimeout(function(){api('/api/v1/products/'+id+'/reviews').then(function(d){$('#reviews').innerHTML=d.reviews.map(function(r){return '<li class="review"><b>'+r.author+'</b> '+r.stars+'/5 — '+r.text+'</li>'}).join('');$('#review-count').textContent=d.total+' reviews'})},250);
    fetch('/graphql',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({operationName:'GetRecommendations',query:'query GetRecommendations($productId: ID!) { recommendations(productId: $productId) { id name price url } }',variables:{productId:String(id)}})})
      .then(function(r){return r.json()}).then(function(d){$('#recommendations').innerHTML=d.data.recommendations.map(function(p){return '<li><a href="'+p.url+'">'+p.name+'</a></li>'}).join('')});
    var w=$('#wishlist');if(w)w.addEventListener('click',function(){fetch('/graphql',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operationName:'AddToWishlist',query:'mutation AddToWishlist($productId: ID!) { addToWishlist(productId: $productId) { ok count } }',variables:{productId:String(id)}})}).then(function(r){return r.json()}).then(function(d){w.textContent='Saved ('+d.data.addToWishlist.count+')'})})}
  function initCart(){function render(){api('/api/v1/cart').then(function(c){var rows=c.items.map(function(it){return '<tr class="cart-row" data-item-id="'+it.item_id+'"><td class="item-name">'+it.name+'</td><td><input class="qty" type="number" min="1" value="'+it.qty+'" aria-label="Quantity for '+it.name+'"></td><td class="line-total">'+money(it.line_total)+'</td><td><button class="btn remove">Remove</button></td></tr>'}).join('');
      $('#cart-body').innerHTML=rows||'<tr><td colspan="4" class="empty">Your cart is empty</td></tr>';$('#cart-total').textContent=money(c.total);var cc=$('#cart-count');if(cc)cc.textContent=c.count;
      document.querySelectorAll('tr.cart-row').forEach(function(tr){var id=tr.dataset.itemId;tr.querySelector('button.remove').onclick=function(){api('/api/v1/cart/'+id,{method:'DELETE',headers:{'X-CSRF-Token':csrf()}}).then(render)};
        tr.querySelector('input.qty').onchange=function(e){api('/api/v1/cart/'+id,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify({qty:parseInt(e.target.value,10)})}).then(render)}})})}render()}
  function initCheckout(){var f=$('#checkout-form');f.addEventListener('submit',function(e){e.preventDefault();var data={};new FormData(f).forEach(function(v,k){data[k]=v});
    api('/api/v1/orders',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(data)}).then(function(o){$('#checkout').innerHTML='<div class="order-confirmation" role="status">Order confirmed #'+o.order_id+' — total '+money(o.total)+'</div>'})
      .catch(function(err){$('#checkout-error').textContent='Could not place order: '+err.message})})}
  function initTrending(){var feed=$('#feed'),loading=$('#feed-loading'),cursor=0,busy=false,done=false;
    function more(){if(busy||done)return;busy=true;loading.style.display='block';api('/api/v1/feed?cursor='+cursor+'&limit=10').then(function(d){
      d.items.forEach(function(it){var a=document.createElement('article');a.className='feed-item';a.dataset.id=it.id;a.innerHTML='<h3 class="feed-title">'+it.title+'</h3><p>'+it.summary+'</p>';feed.appendChild(a)});
      cursor=d.next_cursor;busy=false;loading.style.display='none';if(d.next_cursor===null){done=true;$('#feed-end').style.display='block'}})}
    more();window.addEventListener('scroll',function(){if(window.innerHeight+window.scrollY>=document.body.scrollHeight-200)more()})}
  function initDeals(){var page=0,list=$('#deals'),btn=$('#load-more');function load(){page++;btn.disabled=true;btn.textContent='Loading…';api('/api/v1/deals?page='+page).then(function(d){
      d.items.forEach(function(p){var li=document.createElement('li');li.className='deal';li.innerHTML='<a href="'+p.url+'">'+p.name+'</a> <span class="price">'+money(p.price)+'</span> <s>'+money(p.was)+'</s>';list.appendChild(li)});
      btn.disabled=false;btn.textContent='Load more deals';if(!d.has_more)btn.style.display='none'})}btn.addEventListener('click',load);load()}
  window.ShopLab={api:api,addToCart:addToCart,updateCartCount:updateCartCount,initHome:initHome,initSuggest:initSuggest,initSearch:initSearch,
    initProduct:initProduct,initCart:initCart,initCheckout:initCheckout,initTrending:initTrending,initDeals:initDeals,bindAdd:bindAdd};
  document.addEventListener('DOMContentLoaded',function(){initSuggest();bindAdd();updateCartCount()});
})();
"""


def cdn_response(req: Req) -> Resp:
    path = req.path
    cache = [("Cache-Control", "public, max-age=3600"), ("Access-Control-Allow-Origin", "*")]
    if path == "/static/app.css":
        return Resp(200, SHOP_CSS, "text/css", cache)
    if path == "/static/app.js":
        return Resp(200, SHOP_JS, "application/javascript", cache)
    m = re.match(r"^/img/p/(\d+)\.png$", path)
    if m:
        pid = int(m.group(1))
        return Resp(200, png_bytes(12, 12, ((pid * 53) % 256, (pid * 97) % 256, (pid * 31) % 256)), "image/png", cache)
    if path == "/img/logo.png":
        return Resp(200, png_bytes(24, 8, (51, 85, 255)), "image/png", cache)
    if path.startswith("/fonts/"):
        return Resp(200, b"@font-face{font-family:ShopLab;src:local('Arial')}", "text/css", cache)
    return Resp(404, "not found", "text/plain")



# ════════════════════════════════════════════════════════════════════════════
# fixture_shop.py — test fixtures: ShopLab e-commerce site
# ════════════════════════════════════════════════════════════════════════════
# ShopLab — the realistic e-commerce fixture site (``shop.agenttrace.test``).


USERS = {"demo": {"password": "demo123", "name": "Demo Customer", "email": "demo@shoplab.test"},
         "alice": {"password": "wonderland", "name": "Alice Liddell", "email": "alice@shoplab.test"},
         "bob": {"password": "builder42", "name": "Bob Builder", "email": "bob@shoplab.test"}}


class FixtureState:
    """All mutable fixture state (thread-safe)."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.catalog = Catalog()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.carts: dict = {}
            self.sessions: dict = {}
            self.tokens: dict = {}
            self.orders: dict = {u: [] for u in USERS}
            self.next_order = 1001
            self.wishlist: dict = {}
            self.steps: dict = {}
            self.flaky: dict = {}
            self.flaky_done: dict = {}
            self.rate = {"last_ok": 0.0, "blocked_until": 0.0, "violations": 0, "log": []}
            self.rate_min_interval = 1.2
            self.rate_retry_after = 2
            self.captcha_tokens: set = set()
            self.captcha_solves = 0
            self.request_log: list = []
            self.variant: dict = {"api_change": False}
            self.events: list = []
            self.waits_adds: list = []
            self.botchecks: list = []
            self.uploads: list = []
            self.vault_sessions: dict = {}
            self.news_sessions: set = set()
            self.proxy_logs: dict = {}
            self.counters: dict = {}
            self.slow_inflight = 0
            self.slow_peak = 0

    def log_request(self, req: Req) -> None:
        with self.lock:
            if len(self.request_log) > 50_000:
                del self.request_log[:10_000]
            self.request_log.append({"t": time.time(), "method": req.method, "host": req.host, "path": req.path,
                                     "query": req.raw_query, "ua": req.headers.get("user-agent", ""),
                                     "headers": {k: v for k, v in req.headers.items()
                                                 if k.startswith("x-") or k in ("accept-language", "user-agent",
                                                                                "authorization", "cookie")}})

    def count(self, key: str) -> int:
        with self.lock:
            self.counters[key] = self.counters.get(key, 0) + 1
            return self.counters[key]

    def event(self, kind: str, **data: Any) -> None:
        with self.lock:
            self.events.append({"t": time.time(), "kind": kind, **data})


# ═══════════════════════════════════════════════════════════════════════
# layout
# ═══════════════════════════════════════════════════════════════════════
LAYOUT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{{title}} | ShopLab</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="ShopLab - handmade homeware">
<link rel="stylesheet" href="http://cdn.agenttrace.test/static/app.css">
<link rel="stylesheet" href="http://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap">
{{noise}}{{head}}
</head><body data-consent="{{consent}}">
<header class="site"><a class="logo" href="/"><img src="http://cdn.agenttrace.test/img/logo.png" alt="" width="24" height="8"> ShopLab</a>
<nav aria-label="Main"><a href="/">Home</a><a href="/catalogue/">Catalogue</a><a href="/deals">Deals</a><a href="/trending">Trending</a></nav>
<form class="search-form" action="/search" method="get" role="search"><input id="q" name="q" type="search" placeholder="Search products" aria-label="Search products" autocomplete="off"><button class="btn" type="submit">Search</button></form>
<a id="cart-link" href="/cart">Cart (<span id="cart-count">0</span>)</a>
{{account}}
</header>
<main id="main">{{body}}</main>
<div id="ad-slot" aria-label="Advertisement"></div>
<footer>&copy; 2026 ShopLab &middot; <a href="/about">About us</a> &middot; <a href="/robots.txt">robots.txt</a></footer>
<script src="http://cdn.agenttrace.test/static/app.js"></script>
{{scripts}}
</body></html>"""


def _user_of(state: FixtureState, req: Req) -> Optional[str]:
    sid = req.cookies.get("sessionid")
    with state.lock:
        user = state.sessions.get(sid) if sid else None
    if user is None:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            with state.lock:
                user = state.tokens.get(auth[7:].strip())
    return user


def shop_page(state: FixtureState, req: Req, title: str, body: str, *, scripts: str = "", head: str = "",
              status: int = 200, consent: bool = False, noise: bool = True) -> Resp:
    user = _user_of(state, req)
    account = (f'<a id="account-link" href="/account">Account ({esc(user)})</a> <a href="/logout">Log out</a>'
               if user else '<a id="login-link" href="/login">Login</a>')
    page = tpl(LAYOUT, title=esc(title), body=body, scripts=scripts, head=head, account=account,
               noise=NOISE_SNIPPET if noise and req.q("noise") != "0" else "",
               consent="1" if consent else "0")
    resp = Resp(status, page)
    return _ensure_cookies(state, req, resp)


def _ensure_cookies(state: FixtureState, req: Req, resp: Resp) -> Resp:
    if "cart_id" not in req.cookies:
        resp.cookie("cart_id", "c" + secrets.token_hex(6))
    if "csrftoken" not in req.cookies:
        resp.cookie("csrftoken", secrets.token_hex(16), http_only=False)
    return resp


def _cart(state: FixtureState, req: Req) -> dict:
    cid = req.cookies.get("cart_id") or "anonymous"
    with state.lock:
        return state.carts.setdefault(cid, {"items": [], "next_item": 1})


def _cart_json(state: FixtureState, cart: dict) -> dict:
    items = []
    total = 0.0
    count = 0
    for it in cart["items"]:
        p = state.catalog.by_id[it["product_id"]]
        line = round(p["price"] * it["qty"], 2)
        total += line
        count += it["qty"]
        items.append({"item_id": it["item_id"], "product_id": p["id"], "name": p["name"], "qty": it["qty"],
                      "price": p["price"], "line_total": line, "gift_wrap": it.get("gift_wrap", False)})
    return {"items": items, "count": count, "total": round(total, 2), "currency": "GBP"}


def _product_json(state: FixtureState, p: dict) -> dict:
    out = {"id": p["id"], "name": p["name"], "slug": p["slug"], "category": p["category"], "price": p["price"],
           "rating": p["rating"], "stock": p["stock"], "sku": p["sku"], "url": state.catalog.url(p),
           "image": f"http://cdn.agenttrace.test/img/p/{p['id']}.png"}
    if state.variant.get("api_change"):
        out["price"] = f"{p['price']:.2f}"
        out["currency"] = "GBP"
        out["stars"] = out.pop("rating")
    return out


def _csrf_ok(req: Req) -> bool:
    token = req.headers.get("x-csrf-token") or (req.form.get("csrf_token") if isinstance(req.form, dict) else "")
    return bool(token) and token == req.cookies.get("csrftoken")


def _stars(n: int) -> str:
    return ["Zero", "One", "Two", "Three", "Four", "Five"][max(0, min(5, n))]


# ═══════════════════════════════════════════════════════════════════════
# HTML pages
# ═══════════════════════════════════════════════════════════════════════
def _home(state, req):
    body = """<section class="hero card"><h1>Handmade homeware, delivered.</h1><p>Browse <a href="/catalogue/">400 products</a> or check today's <a href="/deals">deals</a>.</p></section>
<h2>Featured products</h2><div id="featured" class="grid" aria-live="polite"><p class="loading">Loading featured products…</p></div>
<h2>Categories</h2><ul id="categories"></ul>"""
    return shop_page(state, req, "Home", body, scripts="<script>ShopLab.initHome()</script>", consent=True)


def _catalogue_page(state, req, n: int):
    per = 20
    products = state.catalog.products
    pages = math.ceil(len(products) / per)
    if n < 1 or n > pages:
        return shop_page(state, req, "Not found", "<h1>404 - page not found</h1>", status=404)
    items = products[(n - 1) * per: n * per]
    arts = []
    for p in items:
        arts.append(
            f'<li class="col"><article class="product_pod" data-product-id="{p["id"]}">'
            f'<div class="image_container"><a href="{state.catalog.url(p)}"><img src="http://cdn.agenttrace.test/img/p/{p["id"]}.png" '
            f'alt="{esc(p["name"])}" class="thumbnail" width="48" height="48"></a></div>'
            f'<p class="star-rating {_stars(p["rating"])}" aria-label="{p["rating"]} out of 5 stars">★</p>'
            f'<h3><a href="{state.catalog.url(p)}" title="{esc(p["name"])}">{esc(p["name"][:28])}</a></h3>'
            f'<div class="product_price"><p class="price_color">£{p["price"]:.2f}</p>'
            f'<p class="instock availability">{"In stock" if p["stock"] else "Out of stock"}</p>'
            f'<button class="btn add-to-cart" data-product-id="{p["id"]}">Add to basket</button></div></article></li>')
    prev_link = f'<li class="previous"><a href="page-{n - 1}.html">previous</a></li>' if n > 1 else ""
    next_link = f'<li class="next"><a href="page-{n + 1}.html" rel="next">next</a></li>' if n < pages else ""
    body = (f'<div class="page-header"><h1>All products</h1></div><form class="form-horizontal"><strong>{len(products)}</strong> results - '
            f'showing <strong>{(n - 1) * per + 1}</strong> to <strong>{min(n * per, len(products))}</strong>.</form>'
            f'<ol class="row grid">{"".join(arts)}</ol>'
            f'<div><ul class="pager">{prev_link}<li class="current">Page {n} of {pages}</li>{next_link}</ul></div>')
    return shop_page(state, req, f"All products - Page {n}", body)


def _category_page(state, req, slug: str):
    items = [p for p in state.catalog.products if p["category"] == slug]
    if not items:
        return shop_page(state, req, "Not found", "<h1>404 - category not found</h1>", status=404)
    lis = "".join(f'<li class="product"><a href="{state.catalog.url(p)}">{esc(p["name"])}</a> '
                  f'<span class="price">£{p["price"]:.2f}</span></li>' for p in items)
    return shop_page(state, req, slug.title(), f'<h1>{esc(slug.title())}</h1><ul class="category-list">{lis}</ul>')


def _product_page(state, req, pid: int):
    p = state.catalog.by_id.get(pid)
    if p is None:
        return shop_page(state, req, "Not found", "<h1>404 - product not found</h1>", status=404)
    ld = {"@context": "https://schema.org", "@type": "Product", "name": p["name"], "sku": p["sku"],
          "description": p["description"], "image": f"http://cdn.agenttrace.test/img/p/{p['id']}.png",
          "offers": {"@type": "Offer", "price": f"{p['price']:.2f}", "priceCurrency": "GBP",
                     "availability": "https://schema.org/InStock" if p["stock"] else "https://schema.org/OutOfStock"},
          "aggregateRating": {"@type": "AggregateRating", "ratingValue": p["rating"], "reviewCount": 3 + p["id"] % 6}}
    head = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    opts = "".join(f'<option value="{i}">{i}</option>' for i in range(1, 6))
    body = f"""<nav class="breadcrumb" aria-label="Breadcrumb"><a href="/">Home</a> › <a href="/catalogue/category/{p['category']}/">{esc(p['category'].title())}</a> › <span>{esc(p['name'])}</span></nav>
<article class="product-page card" data-product-id="{p['id']}">
<img src="http://cdn.agenttrace.test/img/p/{p['id']}.png" alt="{esc(p['name'])}" width="96" height="96">
<h1 class="product-title">{esc(p['name'])}</h1>
<p class="price" itemprop="price">£{p['price']:.2f}</p>
<p class="availability">{('In stock (' + str(p['stock']) + ' available)') if p['stock'] else 'Out of stock'}</p>
<p class="description">{esc(p['description'])}</p>
<table class="product-info"><tr><th>SKU</th><td class="sku">{p['sku']}</td></tr><tr><th>Category</th><td>{esc(p['category'])}</td></tr></table>
<label for="qty">Quantity</label> <select id="qty" name="qty" aria-label="Quantity">{opts}</select>
<label><input type="checkbox" id="gift" name="gift_wrap"> Gift wrap</label>
<button id="add-to-cart" class="btn btn-primary" data-product-id="{p['id']}">Add to cart</button>
<button id="wishlist" class="btn">Save to wishlist</button>
<p id="cart-status" role="status"></p>
</article>
<section><h2>Reviews <small id="review-count"></small></h2><ul id="reviews"><li class="loading">Loading reviews…</li></ul></section>
<section><h2>You may also like</h2><ul id="recommendations"></ul></section>"""
    return shop_page(state, req, p["name"], body, head=head, scripts=f"<script>ShopLab.initProduct({p['id']})</script>")


def _search_page(state, req):
    q = req.q("q")
    body = f"""<h1>Search</h1><p id="result-count" role="status"></p><ol id="results" class="results"></ol>"""
    return shop_page(state, req, f"Search: {q}", body, scripts=f"<script>ShopLab.initSearch({json.dumps(q)})</script>")


def _cart_page(state, req):
    body = """<h1>Your cart</h1><table id="cart"><thead><tr><th>Product</th><th>Qty</th><th>Total</th><th></th></tr></thead>
<tbody id="cart-body"><tr><td colspan="4" class="loading">Loading cart…</td></tr></tbody></table>
<p>Total: <strong id="cart-total">£0.00</strong></p><a id="checkout-link" class="btn btn-primary" href="/checkout">Proceed to checkout</a>"""
    return shop_page(state, req, "Cart", body, scripts="<script>ShopLab.initCart()</script>")


def _checkout_page(state, req):
    countries = "".join(f'<option value="{c}">{c}</option>' for c in ("United Kingdom", "Bangladesh", "Germany", "United States"))
    body = f"""<h1>Checkout</h1><div id="checkout"><form id="checkout-form" novalidate>
<fieldset><legend>Shipping</legend>
<label for="full_name">Full name</label><input id="full_name" name="full_name" required>
<label for="email">Email</label><input id="email" name="email" type="email" required>
<label for="address">Address</label><input id="address" name="address" required>
<label for="city">City</label><input id="city" name="city" required>
<label for="country">Country</label><select id="country" name="country">{countries}</select></fieldset>
<fieldset><legend>Payment</legend><label for="card">Card number</label><input id="card" name="card_number" inputmode="numeric" autocomplete="cc-number"></fieldset>
<label><input type="checkbox" id="terms" name="terms" value="yes"> I accept the terms and conditions</label>
<button type="submit" class="btn btn-primary">Place order</button><p id="checkout-error" role="alert"></p></form></div>"""
    return shop_page(state, req, "Checkout", body, scripts="<script>ShopLab.initCheckout()</script>")


def _login_page(state, req, error: str = "", status: int = 200):
    token = req.cookies.get("csrftoken") or secrets.token_hex(16)
    nxt = esc(req.q("next") or "/account")
    err = f'<p class="error" role="alert">{esc(error)}</p>' if error else ""
    body = f"""<h1>Sign in</h1>{err}<form id="login-form" method="post" action="/login?next={nxt}" class="card">
<input type="hidden" name="csrf_token" value="{token}">
<label for="username">Username</label><input id="username" name="username" autocomplete="username" required>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit" class="btn btn-primary">Sign in</button></form><p>Demo account: demo / demo123</p>"""
    resp = shop_page(state, req, "Login", body, status=status, noise=False)
    if "csrftoken" not in req.cookies:
        resp.headers = [h for h in resp.headers if not h[1].startswith("csrftoken=")]
        resp.cookie("csrftoken", token, http_only=False)
    return resp


def _login_post(state, req):
    form = req.form
    if not form.get("csrf_token") or form.get("csrf_token") != req.cookies.get("csrftoken"):
        return _login_page(state, req, "Security token expired, please try again.", 403)
    user = form.get("username", "").strip()
    if USERS.get(user, {}).get("password") != form.get("password"):
        state.event("login_failed", user=user)
        return _login_page(state, req, "Invalid username or password.", 401)
    sid = secrets.token_urlsafe(24)
    with state.lock:
        state.sessions[sid] = user
    state.event("login", user=user)
    nxt = req.q("next") or "/account"
    if not nxt.startswith("/"):
        nxt = "/account"
    return redirect(nxt, 303).cookie("sessionid", sid)


def _account_page(state, req):
    user = _user_of(state, req)
    if not user:
        return redirect("/login?next=/account")
    with state.lock:
        orders = list(state.orders.get(user, []))
    rows = "".join(f'<tr class="order"><td>#{o["order_id"]}</td><td>{o["items"]}</td><td>£{o["total"]:.2f}</td></tr>'
                   for o in orders) or '<tr><td colspan="3">No orders yet</td></tr>'
    body = f"""<h1>Welcome, {esc(USERS[user]['name'])}</h1><p class="account-email">{esc(USERS[user]['email'])}</p>
<h2>Your orders</h2><table id="orders"><tr><th>Order</th><th>Items</th><th>Total</th></tr>{rows}</table>
<h2>Export</h2><ul class="exports"><li><a id="export-csv" href="/account/orders.csv">Download orders (CSV)</a></li>
<li><a id="export-json" href="/account/orders.json">Download orders (JSON)</a></li>
<li><a id="export-pdf" href="/account/invoice.pdf">Download latest invoice (PDF)</a></li></ul>"""
    return shop_page(state, req, "Your account", body)


def _account_export(state, req, kind: str):
    user = _user_of(state, req)
    if not user:
        return Resp(401, "login required", "text/plain")
    with state.lock:
        orders = list(state.orders.get(user, [])) or [{"order_id": 1000, "items": 2, "total": 42.5}]
    if kind == "csv":
        body = "order_id,items,total\n" + "".join(f'{o["order_id"]},{o["items"]},{o["total"]:.2f}\n' for o in orders)
        return Resp(200, body, "text/csv; charset=utf-8", [("Content-Disposition", 'attachment; filename="orders.csv"')])
    if kind == "json":
        return Resp(200, json.dumps({"user": user, "orders": orders}, indent=1), "application/json",
                    [("Content-Disposition", 'attachment; filename="orders.json"')])
    data = pdf_bytes("ShopLab invoice", [f"Customer: {USERS[user]['name']}"] +
                     [f"Order #{o['order_id']}: {o['items']} items, GBP {o['total']:.2f}" for o in orders])
    return Resp(200, data, "application/pdf", [("Content-Disposition", 'attachment; filename="invoice-latest.pdf"')])


def _trending_page(state, req):
    body = """<h1>Trending now</h1><div id="feed" class="feed"></div><div id="feed-loading" class="loading" aria-busy="true">Loading more…</div>
<p id="feed-end" style="display:none">You're all caught up!</p>"""
    return shop_page(state, req, "Trending", body, scripts="<script>ShopLab.initTrending()</script>")


def _deals_page(state, req):
    body = """<h1>Deals of the day</h1><ul id="deals" class="deals"></ul><button id="load-more" class="btn">Load more deals</button>"""
    return shop_page(state, req, "Deals", body, scripts="<script>ShopLab.initDeals()</script>")


def _about_page(state, req):
    body = "<h1>About ShopLab</h1><p>ShopLab is a fictional store used to test AgentTrace.</p>"
    return shop_page(state, req, "About", body, noise=False)


ROBOTS = """User-agent: *
Disallow: /account
Disallow: /checkout
Crawl-delay: 1
Sitemap: http://shop.agenttrace.test/sitemap.xml
"""


def _sitemap(state):
    urls = [f"http://shop.agenttrace.test/catalogue/page-{n}.html" for n in range(1, 21)]
    urls += [f"http://shop.agenttrace.test{state.catalog.url(p)}" for p in state.catalog.products[:50]]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    body += "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n"
    return Resp(200, body, "application/xml")


# ═══════════════════════════════════════════════════════════════════════
# REST + GraphQL API
# ═══════════════════════════════════════════════════════════════════════
def _reviews(p: dict) -> list:
    authors = ["Maya", "Rahim", "Sofia", "Tanvir", "Lena", "Omar", "Priya", "Jonas"]
    n = 3 + p["id"] % 6
    return [{"author": authors[(p["id"] + i) % len(authors)], "stars": 1 + (p["id"] * (i + 3)) % 5,
             "text": f"Review {i + 1} of the {p['name'].lower()}."} for i in range(n)]


def _graphql(state, req):
    body = req.json or {}
    op = body.get("operationName") or ""
    variables = body.get("variables") or {}
    if not op:
        m = re.search(r"(query|mutation)\s+(\w+)", body.get("query") or "")
        op = m.group(2) if m else ""
    if op == "GetRecommendations":
        pid = int(variables.get("productId") or 1)
        p = state.catalog.by_id.get(pid) or state.catalog.products[0]
        recs = [q for q in state.catalog.products if q["category"] == p["category"] and q["id"] != pid][:4]
        return jresp({"data": {"recommendations": [{"id": str(q["id"]), "name": q["name"], "price": q["price"],
                                                    "url": state.catalog.url(q)} for q in recs]}})
    if op == "AddToWishlist":
        cid = req.cookies.get("cart_id") or "anonymous"
        with state.lock:
            wl = state.wishlist.setdefault(cid, set())
            wl.add(str(variables.get("productId")))
            count = len(wl)
        return jresp({"data": {"addToWishlist": {"ok": True, "count": count}}})
    if op == "GetProduct":
        p = state.catalog.by_id.get(int(variables.get("id") or 0))
        return jresp({"data": {"product": _product_json(state, p) if p else None}})
    return jresp({"errors": [{"message": f"Unknown operation {op!r}"}]}, 400)


def shop_api(state: FixtureState, req: Req) -> Optional[Resp]:
    path, method = req.path, req.method
    cat = state.catalog
    if path == "/api/v1/products" and method == "GET":
        items = cat.products
        if req.q("category"):
            items = [p for p in items if p["category"] == req.q("category")]
        if req.q("featured"):
            items = [p for p in items if p["featured"]]
        limit = max(1, min(100, req.qi("limit", 20)))
        page = max(1, req.qi("page", 1))
        pages = max(1, math.ceil(len(items) / limit))
        chunk = items[(page - 1) * limit: page * limit]
        nxt = f"/api/v1/products?page={page + 1}&limit={limit}" if page < pages else None
        return jresp({"items": [_product_json(state, p) for p in chunk], "page": page, "pages": pages,
                      "total": len(items), "next": nxt},
                     headers=[("X-RateLimit-Limit", "120"), ("X-RateLimit-Remaining", "119")])
    m = re.match(r"^/api/v1/products/(\d+)$", path)
    if m and method == "GET":
        p = cat.by_id.get(int(m.group(1)))
        return jresp(_product_json(state, p)) if p else jresp({"error": "not found"}, 404)
    m = re.match(r"^/api/v1/products/(\d+)/reviews$", path)
    if m and method == "GET":
        p = cat.by_id.get(int(m.group(1)))
        if p is None:
            return jresp({"error": "not found"}, 404)
        revs = _reviews(p)
        return jresp({"product_id": p["id"], "total": len(revs), "reviews": revs})
    if path == "/api/v1/categories":
        return jresp({"categories": [{"slug": c, "name": c.title(),
                                      "count": sum(1 for p in cat.products if p["category"] == c)} for c in cat.categories]})
    if path == "/api/v1/search":
        time.sleep(0.15)
        res = cat.search(req.q("q"))
        limit = max(1, min(50, req.qi("limit", 20)))
        return jresp({"query": req.q("q"), "total": len(res), "items": [_product_json(state, p) for p in res[:limit]]})
    if path == "/api/v1/suggest":
        res = cat.search(req.q("q"))[:6]
        return jresp({"query": req.q("q"), "suggestions": [p["name"] for p in res]})
    if path == "/api/v1/cart":
        cart = _cart(state, req)
        if method == "GET":
            return jresp(_cart_json(state, cart))
        if method == "POST":
            if not _csrf_ok(req):
                return jresp({"error": "CSRF token missing or invalid"}, 403)
            body = req.json or {}
            pid = int(body.get("product_id") or 0)
            if pid not in cat.by_id:
                return jresp({"error": "unknown product"}, 400)
            qty = max(1, int(body.get("qty") or 1))
            with state.lock:
                for it in cart["items"]:
                    if it["product_id"] == pid:
                        it["qty"] += qty
                        break
                else:
                    cart["items"].append({"item_id": cart["next_item"], "product_id": pid, "qty": qty,
                                          "gift_wrap": bool(body.get("gift_wrap"))})
                    cart["next_item"] += 1
            state.event("cart_add", product_id=pid, qty=qty, cart=req.cookies.get("cart_id"))
            return jresp({"ok": True, **_cart_json(state, cart)}, 201)
    m = re.match(r"^/api/v1/cart/(\d+)$", path)
    if m and method in ("DELETE", "PATCH"):
        if not _csrf_ok(req):
            return jresp({"error": "CSRF token missing or invalid"}, 403)
        cart = _cart(state, req)
        item_id = int(m.group(1))
        with state.lock:
            if method == "DELETE":
                cart["items"] = [it for it in cart["items"] if it["item_id"] != item_id]
            else:
                qty = int((req.json or {}).get("qty") or 1)
                for it in cart["items"]:
                    if it["item_id"] == item_id:
                        it["qty"] = max(1, qty)
        return jresp({"ok": True, **_cart_json(state, cart)})
    if path == "/api/v1/orders" and method == "POST":
        if not _csrf_ok(req):
            return jresp({"error": "CSRF token missing or invalid"}, 403)
        data = req.json or {}
        missing = [k for k in ("full_name", "email", "address", "city") if not str(data.get(k, "")).strip()]
        if missing or data.get("terms") != "yes":
            return jresp({"error": "validation failed", "missing": missing, "terms": data.get("terms")}, 422)
        cart = _cart(state, req)
        summary = _cart_json(state, cart)
        if not summary["items"]:
            return jresp({"error": "cart is empty"}, 400)
        with state.lock:
            oid = state.next_order
            state.next_order += 1
            order = {"order_id": oid, "items": summary["count"], "total": summary["total"]}
            user = _user_of(state, req)
            if user:
                state.orders[user].append(order)
            cart["items"] = []
        state.event("order", order_id=oid, user=user)
        return jresp({"ok": True, **order}, 201)
    if path == "/api/v1/auth/login" and method == "POST":
        body = req.json or {}
        user = str(body.get("username") or "")
        if USERS.get(user, {}).get("password") != body.get("password"):
            return jresp({"error": "invalid credentials"}, 401)
        token = fake_jwt(user)
        with state.lock:
            state.tokens[token] = user
        return jresp({"access_token": token, "token_type": "bearer", "expires_in": 3600,
                      "refresh_token": "rt_" + secrets.token_hex(12)})
    if path == "/api/v1/me":
        user = _user_of(state, req)
        if not user:
            return jresp({"error": "unauthorized"}, 401, [("WWW-Authenticate", "Bearer")])
        return jresp({"username": user, "name": USERS[user]["name"], "email": USERS[user]["email"]})
    if path == "/api/v1/me/preferences" and method == "PUT":
        user = _user_of(state, req)
        if not user:
            return jresp({"error": "unauthorized"}, 401)
        return jresp({"ok": True, "preferences": req.json or {}})
    if path == "/api/v1/feed":
        time.sleep(0.2)
        cursor = max(0, req.qi("cursor", 0))
        limit = max(1, min(20, req.qi("limit", 10)))
        items = [{"id": i + 1, "title": f"Trending #{i + 1}: {cat.products[(i * 7) % len(cat.products)]['name']}",
                  "summary": f"People love this {cat.products[(i * 7) % len(cat.products)]['category']} pick."}
                 for i in range(cursor, min(100, cursor + limit))]
        nxt = cursor + limit if cursor + limit < 100 else None
        return jresp({"items": items, "next_cursor": nxt, "total": 100})
    if path == "/api/v1/deals":
        time.sleep(0.1)
        page = max(1, req.qi("page", 1))
        deals = [p for p in cat.products if p["id"] % 9 == 0][:40]
        chunk = deals[(page - 1) * 8: page * 8]
        return jresp({"page": page, "has_more": page * 8 < len(deals),
                      "items": [{**_product_json(state, p), "was": round(p["price"] * 1.3, 2)} for p in chunk]})
    if path == "/graphql" and method == "POST":
        return _graphql(state, req)
    return None


def shop_response(state: FixtureState, req: Req) -> Resp:
    path, method = req.path, req.method
    if path.startswith("/api/") or path == "/graphql":
        resp = shop_api(state, req)
        return resp if resp is not None else jresp({"error": "no such endpoint", "path": path}, 404)
    if path == "/":
        return _home(state, req)
    if path in ("/catalogue", "/catalogue/"):
        return redirect("/catalogue/page-1.html")
    m = re.match(r"^/catalogue/page-(\d+)\.html$", path)
    if m:
        return _catalogue_page(state, req, int(m.group(1)))
    m = re.match(r"^/catalogue/category/([a-z]+)/?$", path)
    if m:
        return _category_page(state, req, m.group(1))
    m = re.match(r"^/product/[a-z0-9-]+_(\d+)/?$", path)
    if m:
        return _product_page(state, req, int(m.group(1)))
    if path == "/search":
        return _search_page(state, req)
    if path == "/cart":
        return _cart_page(state, req)
    if path == "/checkout":
        return _checkout_page(state, req)
    if path == "/login":
        return _login_post(state, req) if method == "POST" else _login_page(state, req)
    if path == "/logout":
        return redirect("/").cookie("sessionid", "", max_age=0)
    if path == "/account":
        return _account_page(state, req)
    m = re.match(r"^/account/(orders\.csv|orders\.json|invoice\.pdf)$", path)
    if m:
        return _account_export(state, req, {"orders.csv": "csv", "orders.json": "json", "invoice.pdf": "pdf"}[m.group(1)])
    if path == "/trending":
        return _trending_page(state, req)
    if path == "/deals":
        return _deals_page(state, req)
    if path == "/about":
        return _about_page(state, req)
    if path == "/robots.txt":
        return Resp(200, ROBOTS, "text/plain")
    if path == "/sitemap.xml":
        return _sitemap(state)
    if path == "/favicon.ico":
        return Resp(204, b"", "")
    return shop_page(state, req, "Not found", "<h1>404 - page not found</h1>", status=404, noise=False)



# ════════════════════════════════════════════════════════════════════════════
# fixture_lab.py — test fixtures: lab scenarios (SPA, SSE, WebSocket, CAPTCHA, rate limits, integrity, frames …)
# ════════════════════════════════════════════════════════════════════════════
# Scenario pages for focused tests (``lab.agenttrace.test``) plus the SPA,
# vault (storage) and news (second site) fixtures.


LAB_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{{title}}</title>
<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:960px}button,.btn{padding:6px 12px;margin:4px}
.modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:100}
.modal{background:#fff;padding:24px;border-radius:10px;min-width:320px}[hidden]{display:none!important}</style>{{head}}</head>
<body>{{body}}{{scripts}}</body></html>"""


def lab_page(title: str, body: str, scripts: str = "", head: str = "", status: int = 200,
             headers: Optional[list] = None) -> Resp:
    return Resp(status, tpl(LAB_HEAD, title=esc(title), body=body, scripts=scripts, head=head),
                headers=headers)


def _lat(profile: str, rng: random.Random) -> float:
    base = {"fast": 0.03, "medium": 0.7, "slow": 2.4}.get(profile, 0.3)
    return base + rng.uniform(0, base * 0.25)


# ═══════════════════════════════════════════════════════════════════════
# lab pages
# ═══════════════════════════════════════════════════════════════════════
def lab_response(state: FixtureState, req: Req) -> Resp:
    p, method = req.path, req.method
    if p.startswith("/lab/api/"):
        resp = lab_api(state, req)
        if resp is not None:
            return resp
        return jresp({"error": "no such lab endpoint", "path": p}, 404)

    if p == "/lab/click":
        return lab_page("Click lab", """<h1>Stats dashboard</h1><button id="load-stats">Load stats</button><pre id="out"></pre>""",
                        """<script>document.getElementById('load-stats').onclick=function(){fetch('/lab/api/stats?ts='+Date.now()).then(r=>r.json()).then(d=>{document.getElementById('out').textContent=JSON.stringify(d)})}</script>""")

    if p == "/lab/slow":
        delay = req.qi("delay", 800)
        ms = req.qi("ms", 2000)
        return lab_page("Slow API lab", f"""<h1>Report</h1><p id="state">waiting for data…</p>""",
                        f"""<script>setTimeout(function(){{fetch('/lab/api/slow?ms={ms}').then(r=>r.json()).then(d=>{{document.getElementById('state').textContent='Slow data loaded: '+d.rows+' rows'}})}},{delay})</script>""")

    if p == "/lab/correlation":
        return lab_page("Correlation lab", """<h1>Product</h1>
<button id="reviews">Load reviews</button> <button id="save">Save item</button> <button id="details">Show details</button>
<label>Filter <select id="filter"><option value="all">All</option><option value="new">New</option><option value="sale">Sale</option></select></label>
<div id="out"></div><div id="imgs"></div>""", """<script>
var seed=parseInt(new URLSearchParams(location.search).get('seed')||'1',10);function rnd(){seed=(seed*1103515245+12345)%2147483648;return seed/2147483648}
setInterval(function(){fetch('/lab/api/poll?since='+Date.now()).then(r=>r.json())},400);
setInterval(function(){try{navigator.sendBeacon('http://www.google-analytics.com/g/collect?v=2&en=heartbeat&_p='+Date.now())}catch(e){}},650);
var imgN=0;setInterval(function(){if(imgN<60){var i=new Image(12,12);i.src='/lab/img/'+(imgN++)+'.png';document.getElementById('imgs').appendChild(i)}},500);
var clicks=0;
document.getElementById('reviews').onclick=function(){clicks++;var k=clicks;setTimeout(function(){fetch('/lab/api/reviews?item='+k).then(r=>r.json()).then(d=>{document.getElementById('out').textContent=d.count+' reviews'})},Math.floor(rnd()*700))};
document.getElementById('save').onclick=function(){setTimeout(function(){fetch('/lab/api/wishlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item:clicks})}).then(r=>r.json())},Math.floor(rnd()*400))};
document.getElementById('details').onclick=function(){var id=1+Math.floor(rnd()*50);setTimeout(function(){fetch('/lab/api/details/'+id).then(r=>r.json()).then(function(){return fetch('/lab/api/details/'+id+'/stock')})},Math.floor(rnd()*500))};
document.getElementById('filter').onchange=function(e){var v=e.target.value;setTimeout(function(){fetch('/lab/api/filter?c='+v).then(r=>r.json())},300)};
</script>""")

    if p == "/lab/waits":
        return lab_page("Waits lab", """<h1>Catalog search</h1>
<input id="term" aria-label="Search term" placeholder="Search term"> <button id="go">Search</button>
<p id="status" role="status"></p>
<ul id="results" aria-busy="false"><li class="res"><a href="#" data-id="stale-1">Stale result from last visit</a></li></ul>
<section id="detail" hidden><h2 id="detail-title"></h2><p id="detail-state"></p><button id="add" disabled>Add to list</button></section>""",
                        """<script>
var P=new URLSearchParams(location.search).get('profile')||'medium';var cur=null;
function bindResults(){document.querySelectorAll('#results a').forEach(function(a){a.onclick=function(e){e.preventDefault();openItem(a.dataset.id)}})}
bindResults();
document.getElementById('go').onclick=function(){var t=document.getElementById('term').value;var r=document.getElementById('results');r.setAttribute('aria-busy','true');
 document.getElementById('status').textContent='Searching…';
 fetch('/lab/api/waits/search?q='+encodeURIComponent(t)+'&profile='+P).then(x=>x.json()).then(function(d){
  r.innerHTML=d.items.map(function(it){return '<li class="res"><a href="#" data-id="'+it.id+'">'+it.name+'</a></li>'}).join('');r.setAttribute('aria-busy','false');
  document.getElementById('status').textContent=d.items.length+' results';bindResults()})};
function openItem(id){var d=document.getElementById('detail'),b=document.getElementById('add');d.hidden=false;b.disabled=true;cur=null;
 document.getElementById('detail-state').textContent='Loading…';
 fetch('/lab/api/waits/item/'+encodeURIComponent(id)+'?profile='+P).then(x=>x.json()).then(function(it){document.getElementById('detail-title').textContent=it.name;
  document.getElementById('detail-state').textContent='Ready';cur=it.id;setTimeout(function(){b.disabled=false},Math.floor(Math.random()*300))})}
document.getElementById('add').onclick=function(){fetch('/lab/api/waits/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:cur})}).then(x=>x.json()).then(function(r){document.getElementById('status').textContent='Added '+r.id})};
</script>""")

    m = re.match(r"^/lab/flaky/step/(\d+)$", p)
    if m:
        return _flaky_step(state, req, int(m.group(1)))

    if p == "/lab/downloads":
        return lab_page("Downloads lab", """<h1>Exports</h1><ul>
<li><a id="dl-csv" href="/lab/files/report.csv">Download CSV report</a></li>
<li><a id="dl-json" href="/lab/files/data.json">Download JSON data</a></li>
<li><a id="dl-pdf" href="/lab/files/invoice.pdf">Download PDF invoice</a></li>
<li><button id="dl-blob">Export generated TXT</button></li>
<li><form method="post" action="/lab/files/export"><input type="hidden" name="format" value="csv"><button id="dl-post" type="submit">Export via form (POST)</button></form></li></ul>""",
                        """<script>document.getElementById('dl-blob').onclick=function(){var b=new Blob(['generated in the page\\n'],{type:'text/plain'});var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='generated.txt';document.body.appendChild(a);a.click();a.remove()}</script>""")
    if p.startswith("/lab/files/"):
        return _lab_file(req)

    if p == "/lab/console":
        return lab_page("Console lab", """<h1>Widgets</h1><img src="/lab/missing-image.png" alt="missing"><button id="broken">Run broken widget</button><p id="ok">ready</p>""",
                        """<script>console.log('page ready');console.warn('deprecated api used');console.error('Failed to init widget: missing config');
setTimeout(function(){throw new Error('Uncaught lab failure')},30);Promise.reject(new Error('unhandled rejection in lab'));
document.getElementById('broken').onclick=function(){console.error('clicked broken button');var x=null;x.explode()};</script>""")

    if p == "/lab/heal":
        return _heal_page(req.q("variant", "a"))

    if p == "/lab/frames":
        return lab_page("Frames lab", """<h1>Frames lab</h1><iframe id="frame-a" name="frame-a" src="/lab/frame-a" width="600" height="220"></iframe>
<p><button id="open-popup" onclick="window.open('/lab/popup','labpopup','width=420,height=320')">Open popup</button>
<a id="open-tab" href="/lab/tab" target="_blank">Open report in new tab</a></p>""", "<script>fetch('/lab/api/main-data')</script>")
    if p == "/lab/frame-a":
        return lab_page("Frame A", """<p>Frame A</p><iframe name="frame-b" src="/lab/frame-b" width="400" height="100"></iframe>""",
                        "<script>fetch('/lab/api/frame-a-data')</script>")
    if p == "/lab/frame-b":
        return lab_page("Frame B", "<p>Frame B (nested)</p>", "<script>fetch('/lab/api/frame-b-data')</script>")
    if p == "/lab/popup":
        return lab_page("Popup", """<p>Popup window</p><button id="close-popup" onclick="window.close()">Close</button>""",
                        "<script>fetch('/lab/api/popup-data')</script>")
    if p == "/lab/tab":
        return lab_page("Report tab", "<h1>Report</h1>", "<script>fetch('/lab/api/tab-data')</script>")

    if p == "/lab/botcheck":
        return _botcheck_page(state, req)

    m = re.match(r"^/lab/protected/([\w-]+)$", p)
    if m:
        return _protected(state, req, m.group(1))
    if p == "/lab/captcha/verify" and method == "POST":
        return _captcha_verify(state, req)

    m = re.match(r"^/lab/rl/page/(\d+)$", p)
    if m:
        return _rate_limited(state, req, f"<h1>Listing page {m.group(1)}</h1><p class='rl-ok'>ok</p>")

    if p == "/lab/sse":
        return lab_page("Live prices", """<h1>Live prices</h1><ul id="ticks"></ul><p id="done"></p><pre id="chunks"></pre>""",
                        """<script>var es=new EventSource('/lab/stream/sse?count=10&interval=120');var n=0;
function add(t){var li=document.createElement('li');li.textContent=t;document.getElementById('ticks').appendChild(li)}
es.onmessage=function(e){add('message '+e.data);n++};es.addEventListener('update',function(e){add('update '+e.data);n++});
es.addEventListener('end',function(){es.close();document.getElementById('done').textContent='stream closed';
 fetch('/lab/stream/chunked?count=5&interval=100').then(async function(r){var rd=r.body.getReader(),s='';while(true){var x=await rd.read();if(x.done)break;s+=new TextDecoder().decode(x.value)}document.getElementById('chunks').textContent=s})});</script>""")
    if p == "/lab/stream/sse":
        return _sse(req)
    if p == "/lab/stream/chunked":
        return _chunked(req)

    if p == "/lab/ws":
        port = req.headers.get("x-fixture-port", "")
        return lab_page("Chat", """<h1>Chat</h1><ul id="log"></ul><p id="status">connecting</p>""",
                        tpl("""<script>var ws=new WebSocket('ws://127.0.0.1:{{port}}/ws/chat?room=lab');var got=0;
ws.onopen=function(){document.getElementById('status').textContent='open';for(var i=1;i<=10;i++){ws.send(JSON.stringify({type:'msg',n:i,text:'hello '+i}))}};
ws.onmessage=function(e){got++;var li=document.createElement('li');li.textContent=e.data;document.getElementById('log').appendChild(li);if(got>=13){ws.close(1000,'done')}};
ws.onclose=function(){document.getElementById('status').textContent='closed'};</script>""", port=port))

    if p == "/lab/mock":
        return lab_page("Recommendations", """<h1>Recommended for you</h1><ul id="recs"><li>loading…</li></ul><div id="banner"></div>""",
                        """<script>fetch('/lab/api/recommendations').then(r=>r.json()).then(function(d){document.getElementById('recs').innerHTML=d.items.map(function(i){return '<li class="rec">'+i.name+'</li>'}).join('')});
fetch('/lab/api/banner').then(r=>r.json()).then(function(d){document.getElementById('banner').textContent=d.text}).catch(function(){document.getElementById('banner').textContent='banner unavailable'})</script>""")

    if p == "/lab/integrity":
        return lab_page("Integrity lab", """<h1>Payload lab</h1><pre id="out"></pre>
<form id="upload" method="post" action="/lab/upload" enctype="multipart/form-data"><input name="title" value="quarterly report"><input type="file" name="file" id="file"><button>Upload</button></form>""",
                        """<script>async function run(){var out={};
var j=await (await fetch('/lab/api/data-gzip.json')).json();out.gzip_items=j.items.length;
var img=await (await fetch('/lab/api/image.png')).arrayBuffer();out.png_bytes=img.byteLength;
var fd=new FormData();fd.append('title','quarterly report');var bytes=new Uint8Array(4096);for(var i=0;i<bytes.length;i++){bytes[i]=(i*7+3)%256}
fd.append('file',new Blob([bytes],{type:'application/octet-stream'}),'payload.bin');
var up=await (await fetch('/lab/upload',{method:'POST',body:fd})).json();out.upload=up;
try{await (await fetch('/lab/api/truncated.bin')).arrayBuffer();out.trunc='no error'}catch(e){out.trunc='error: '+e.message}
try{await (await fetch('/lab/api/truncated.json')).json();out.tjson='parsed'}catch(e){out.tjson='bad json'}
var ch=await (await fetch('/lab/api/chunked.txt')).text();out.chunked=ch.length;
document.getElementById('out').textContent=JSON.stringify(out)}run()</script>""")
    if p == "/lab/upload" and method == "POST":
        return _upload(state, req)

    if p == "/lab/steps":
        run = esc(req.q("run", "default"))
        buttons = "".join(f'<button class="step" id="step-{i}" data-step="{i}">Step {i}</button>' for i in range(1, 31))
        return lab_page("Steps", f"<h1>30-step workflow</h1><div id='steps'>{buttons}</div><p id='last' role='status'></p>",
                        tpl("""<script>document.querySelectorAll('button.step').forEach(function(b){b.onclick=function(){fetch('/lab/api/steps/'+b.dataset.step+'?run={{run}}',{method:'POST'}).then(r=>r.json()).then(function(d){b.dataset.done='1';b.textContent='Step '+b.dataset.step+' ✓';document.getElementById('last').textContent='done '+b.dataset.step})}})</script>""", run=run))

    if p == "/lab/wizard":
        return _wizard_page()

    if p == "/lab/next-store":
        return _next_store_page()

    m = re.match(r"^/lab/img/(\d+)\.png$", p)
    if m:
        return Resp(200, png_bytes(4, 4, (int(m.group(1)) % 255, 90, 160)), "image/png")
    if p == "/lab/missing-image.png":
        return Resp(404, "not found", "text/plain")
    if p == "/lab/robots-demo":
        return lab_page("ok", "<p>ok</p>")
    return lab_page("Not found", "<h1>404</h1>", status=404)


# ---------------------------------------------------------------------
def _flaky_step(state: FixtureState, req: Req, step: int) -> Resp:
    run = req.q("run", "r")
    key = f"flaky:{run}:{step}"
    n = state.count(key)
    done_js = tpl("""<script>function done(){fetch('/lab/api/flaky/done?step={{step}}&run={{run}}',{method:'POST'}).then(r=>r.json()).then(function(){document.getElementById('result').textContent='Step {{step}} complete'})}</script>""",
                  step=step, run=esc(run))
    result = '<p id="result" role="status"></p>'
    if step == 1 and n == 1:
        return Resp(503, "<html><head><title>503 Service Unavailable</title></head><body><h1>Service Unavailable</h1><p>Please retry.</p></body></html>",
                    headers=[("Retry-After", "1")])
    if step == 2 and n <= 2:  # twice: Chrome silently retries one reset on a reused connection
        r = Resp(200, b"")
        r.mode = "reset"
        return r
    if step == 3 and n == 1:
        time.sleep(6.0)
    if step in (1, 2, 3):
        return lab_page(f"Step {step}", f"<h1>Step {step}</h1><button id='confirm' onclick='done()'>Confirm step {step}</button>{result}", done_js)
    if step == 4:  # virtualised list: scrolling re-creates the row nodes (the resolved element detaches)
        return lab_page("Step 4", f"""<h1>Step 4</h1><div style="height:1600px">(long list above)</div><div id="holder"></div>{result}""",
                        done_js + """<script>
var swapped=false;function render(){document.getElementById('holder').innerHTML='<button id="confirm" class="confirm-btn">Confirm step 4</button>';
document.getElementById('confirm').onclick=done}
render();window.addEventListener('scroll',function(){if(!swapped){swapped=true;render()}});</script>""")
    if step == 5:
        return lab_page("Step 5", f"""<h1>Step 5</h1><button id="confirm" onclick="done()">Confirm step 5</button>{result}
<div id="newsletter" class="modal-backdrop" hidden><div class="modal" role="dialog" aria-label="Newsletter"><h2>Join our newsletter!</h2><p>Get 10% off.</p>
<button class="close" aria-label="Close" onclick="document.getElementById('newsletter').hidden=true">×</button></div></div>""",
                        done_js + "<script>setTimeout(function(){document.getElementById('newsletter').hidden=false},250)</script>")
    if step == 6:
        return lab_page("Step 6", f"<h1>Step 6</h1><button id='confirm' onclick='done()'>Confirm step 6</button>{result}",
                        done_js + "<script>setTimeout(function(){alert('Your session will expire soon')},50)</script>")
    if step == 7:
        return lab_page("Step 7", f"""<h1>Step 7</h1><button id="confirm">Confirm step 7</button>{result}""", tpl("""<script>
document.getElementById('confirm').onclick=function(){fetch('/lab/api/flaky/save?run={{run}}',{method:'POST'}).then(function(r){if(!r.ok)throw new Error('save failed '+r.status);return r.json()})
.then(function(){fetch('/lab/api/flaky/done?step=7&run={{run}}',{method:'POST'}).then(function(){document.getElementById('result').textContent='Step 7 complete'})})
.catch(function(e){document.getElementById('result').textContent='Error: '+e.message+' - please retry'})}</script>""", run=esc(run)))
    return lab_page("Unknown step", "<h1>unknown</h1>", status=404)


def _lab_file(req: Req) -> Resp:
    name = req.path.rsplit("/", 1)[-1]
    if name == "report.csv":
        body = "id,name,price\n" + "".join(f"{i},Item {i},{i * 1.5:.2f}\n" for i in range(1, 51))
        return Resp(200, body, "text/csv; charset=utf-8", [("Content-Disposition", 'attachment; filename="report.csv"')])
    if name == "data.json":
        body = json.dumps({"generated": "2026-09-25", "rows": [{"id": i, "v": i * i} for i in range(1, 21)]})
        return Resp(200, body, "application/json", [("Content-Disposition", 'attachment; filename="data.json"')])
    if name == "invoice.pdf":
        return Resp(200, pdf_bytes("Invoice #4411", ["Item A  x2  GBP 20.00", "Item B  x1  GBP 5.50", "Total GBP 25.50"]),
                    "application/pdf", [("Content-Disposition", 'attachment; filename="invoice-4411.pdf"')])
    if name == "export" and req.method == "POST":
        body = "format,rows\ncsv,3\n"
        return Resp(200, body, "text/csv", [("Content-Disposition", 'attachment; filename="export-post.csv"')])
    return Resp(404, "no such file", "text/plain")


HEAL_ITEMS = ["Walnut Desk Lamp", "Linen Throw Pillow", "Copper Mug", "Oak Side Table", "Marble Vase"]


def _heal_page(variant: str) -> Resp:
    if variant == "b":
        items = list(reversed(HEAL_ITEMS))
        rows = "".join(f'<div class="row-wrap"><div class="catalog-row" data-sku="{HEAL_ITEMS.index(n) + 1}"><span class="name-cell">{n}</span>'
                       f'<span class="actions"><button class="btn-x purchase" data-item="{HEAL_ITEMS.index(n) + 1}">Buy</button></span></div></div>'
                       for n in items)
        body = f"""<div class="topbar"><div class="left"><button class="c-btn" data-testid="refresh" title="Reload list">Refresh list</button>
<button class="c-btn danger" data-act="clear">Clear filters</button></div></div>
<div class="grid-wrapper"><section class="catalog">{rows}</section></div>
<div class="forms"><form class="nl"><div class="field"><input class="text" name="email" placeholder="Email address" type="email"></div><button class="primary" type="submit">Subscribe now</button></form></div>
<div class="sorting"><label for="sortSel">Sort products</label><select id="sortSel" name="sort"><option value="name">Name</option><option value="price">Price</option></select></div>
<div class="legal"><input type="checkbox" id="terms-checkbox" name="agree"><label for="terms-checkbox">I agree to the terms</label></div>
<div class="searchbar"><input class="q" name="query" placeholder="Search catalog" aria-label="Search catalog"></div>
<footer><a class="lnk" href="#help">Help centre</a> <a class="lnk" href="#p2" rel="next">Next page ›</a></footer>"""
    else:
        rows = "".join(f'<li class="item" id="item-{i + 1}"><a href="#" class="title">{n}</a> '
                       f'<button class="buy" id="buy-{i + 1}" data-item="{i + 1}">Buy now</button></li>' for i, n in enumerate(HEAL_ITEMS))
        body = f"""<div id="toolbar"><button id="btn-refresh" class="btn refresh" data-testid="refresh">Refresh</button>
<button id="btn-clear" class="btn clear">Clear filters</button></div>
<ul id="products">{rows}</ul>
<form id="newsletter"><input id="email" name="email" placeholder="Email address" type="email"><button id="subscribe" type="submit">Subscribe</button></form>
<label for="sort">Sort by</label><select id="sort" name="sort"><option value="name">Name</option><option value="price">Price</option></select>
<p><input type="checkbox" id="agree" name="agree"><label for="agree">I agree to the terms</label></p>
<p><input id="search" name="query" placeholder="Search catalog"></p>
<p><a id="help-link" href="#help">Help center</a> <a id="next-link" href="#p2">Next page</a></p>"""
    js = """<script>
function hit(a,q){fetch('/lab/api/heal/'+a+(q?'?'+q:''),{method:'POST'})}
document.querySelectorAll('[data-testid=refresh],#btn-refresh').forEach(function(b){b.onclick=function(){hit('refresh')}});
document.querySelectorAll('#btn-clear,[data-act=clear]').forEach(function(b){b.onclick=function(){hit('clear')}});
document.querySelectorAll('button.buy,button.purchase').forEach(function(b){b.onclick=function(){hit('buy','item='+b.dataset.item)}});
document.querySelector('form').onsubmit=function(e){e.preventDefault();hit('subscribe','email='+encodeURIComponent(this.querySelector('input').value))};
document.querySelector('select').onchange=function(e){hit('sort','by='+e.target.value)};
document.querySelector('input[type=checkbox]').onchange=function(e){hit('agree','v='+e.target.checked)};
document.querySelector('input[name=query]').onkeydown=function(e){if(e.key==='Enter')hit('search','q='+encodeURIComponent(e.target.value))};
document.querySelectorAll('a[href="#help"]').forEach(function(a){a.onclick=function(e){e.preventDefault();hit('help')}});
document.querySelectorAll('a[href="#p2"]').forEach(function(a){a.onclick=function(e){e.preventDefault();hit('next')}});
</script>"""
    return lab_page(f"Heal lab ({variant})", body, js)


def _botcheck_page(state: FixtureState, req: Req) -> Resp:
    ua = req.headers.get("user-agent", "")
    ch = req.headers.get("sec-ch-ua", "")
    if "headless" in ua.lower() or "headless" in ch.lower():
        state.event("botcheck_block", reason="headless user-agent/client hints")
        return lab_page("Access denied", "<h1 id='verdict'>Access denied</h1><p>Automated browser detected (headless signature).</p>",
                        status=403, headers=[("Server", "cloudflare"), ("cf-mitigated", "challenge")])
    return lab_page("Bot check", """<h1 id="verdict">Checking your browser…</h1><ul id="reasons"></ul>""", """<script>
(async function(){var s={webdriver:navigator.webdriver===true,plugins:navigator.plugins.length,languages:(navigator.languages||[]).length,
chrome:!!window.chrome,runtime:!!(window.chrome&&window.chrome.runtime),ua:navigator.userAgent,
brands:navigator.userAgentData?navigator.userAgentData.brands.map(function(b){return b.brand}):[]};
try{var g=document.createElement('canvas').getContext('webgl');var i=g.getExtension('WEBGL_debug_renderer_info');s.gpu=g.getParameter(i.UNMASKED_RENDERER_WEBGL)}catch(e){s.gpu=''}
var r=await (await fetch('/lab/api/botcheck',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(s)})).json();
document.getElementById('verdict').textContent=r.verdict==='human'?'Welcome, human':'Bot detected';
document.getElementById('reasons').innerHTML=r.reasons.map(function(x){return '<li>'+x+'</li>'}).join('')})();</script>""")


def _protected(state: FixtureState, req: Req, name: str) -> Resp:
    token = req.cookies.get("cf_clearance")
    with state.lock:
        ok = token in state.captcha_tokens
    if ok:
        return lab_page(f"Protected {name}", f"<h1 id='protected-title'>Protected content: {esc(name)}</h1><p class='secret-data'>price-list-{esc(name)}</p>",
                        "<script>fetch('/lab/api/protected-data?page=" + esc(name) + "')</script>")
    a, b = 3 + len(name) % 5, 4 + len(name) % 3
    body = f"""<div id="challenge-stage"><h1>Verify you are human</h1><p>Checking if the site connection is secure. Please complete the security check to access <b>{esc(name)}</b>.</p>
<form id="challenge-form" class="g-recaptcha" data-sitekey="6LcTESTKEY000000000000000" method="post" action="/lab/captcha/verify">
<input type="hidden" name="return_to" value="/lab/protected/{esc(name)}">
<label><input type="checkbox" id="not-robot" name="not_robot" value="1"> I'm not a robot</label>
<p><label for="captcha-answer">What is {a} + {b}?</label> <input id="captcha-answer" name="answer" autocomplete="off"></p>
<input type="hidden" name="expected" value="{a + b}"><button id="verify" type="submit">Verify</button></form></div>
<p class="ray">Ray ID: 8c1f{secrets.token_hex(4)} &middot; Performance &amp; security by AgentTrace Shield</p>"""
    return lab_page("Attention Required! | Security check", body, status=403,
                    headers=[("Server", "cloudflare"), ("cf-mitigated", "challenge"), ("Cache-Control", "no-store")])


def _captcha_verify(state: FixtureState, req: Req) -> Resp:
    form = req.form
    back = form.get("return_to") or "/lab/protected/home"
    if form.get("not_robot") == "1" and form.get("answer", "").strip() == form.get("expected"):
        token = secrets.token_hex(16)
        with state.lock:
            state.captcha_tokens.add(token)
            state.captcha_solves += 1
        return redirect(back, 303).cookie("cf_clearance", token)
    return redirect(back, 303)


def _rate_limited(state: FixtureState, req: Req, body: str) -> Resp:
    now = time.time()
    with state.lock:
        rl = state.rate
        if now < rl["blocked_until"]:
            rl["violations"] += 1
            wait = max(1, int(rl["blocked_until"] - now + 0.999))
            rl["log"].append({"t": now, "path": req.path, "status": 429, "violation": True})
            return lab_page("Too Many Requests", "<h1>429 Too Many Requests</h1>", status=429,
                            headers=[("Retry-After", str(wait))])
        if now - rl["last_ok"] < state.rate_min_interval:
            rl["blocked_until"] = now + state.rate_retry_after
            rl["log"].append({"t": now, "path": req.path, "status": 429, "violation": False})
            return lab_page("Too Many Requests", "<h1>429 Too Many Requests</h1><p>Slow down.</p>", status=429,
                            headers=[("Retry-After", str(state.rate_retry_after))])
        rl["last_ok"] = now
        rl["log"].append({"t": now, "path": req.path, "status": 200, "violation": False})
    if req.path.startswith("/lab/api/"):
        return jresp({"ok": True, "path": req.path})
    return lab_page("Listing", body)


def _sse(req: Req) -> Resp:
    count = max(1, min(50, req.qi("count", 10)))
    interval = max(0, req.qi("interval", 100)) / 1000.0
    resp = Resp(200, b"", "text/event-stream", [("Cache-Control", "no-cache"), ("X-Accel-Buffering", "no")])

    def stream(write):
        write(b": stream opened\n\n")
        names = ["message", "update", "price"]
        for i in range(1, count + 1):
            time.sleep(interval)
            name = names[(i - 1) % 3]
            data = json.dumps({"n": i, "price": round(100 + i * 1.25, 2), "server_ts": time.time()})
            head = f"event: {name}\n" if name != "message" else ""
            write(f"id: {i}\n{head}data: {data}\n\n".encode())
        write(b"event: end\ndata: {}\n\n")
        time.sleep(0.05)

    resp.stream = stream
    resp.mode = "stream"
    return resp


def _chunked(req: Req) -> Resp:
    count = max(1, min(20, req.qi("count", 5)))
    interval = max(0, req.qi("interval", 100)) / 1000.0
    resp = Resp(200, b"", "text/plain; charset=utf-8", [("Cache-Control", "no-cache")])

    def stream(write):
        for i in range(1, count + 1):
            time.sleep(interval)
            write(f"chunk-{i}|{time.time():.3f};".encode())

    resp.stream = stream
    resp.mode = "chunked"
    return resp


def integrity_payloads() -> dict:
    items = [{"id": i, "name": f"Row {i}", "tags": ["a", "b"], "value": i * 3.5} for i in range(1, 201)]
    json_bytes = json.dumps({"items": items}).encode()
    png = png_bytes(64, 64, (10, 120, 230))
    upload = bytes((i * 7 + 3) % 256 for i in range(4096))
    return {"json": json_bytes, "png": png, "upload": upload,
            "chunked": "".join(f"line-{i:04d}\n" for i in range(300)).encode()}


def _upload(state: FixtureState, req: Req) -> Resp:
    form = req.form
    file_part = form.get("file") if isinstance(form.get("file"), dict) else None
    info = {"title": form.get("title"), "body_sha256": hashlib.sha256(req.body).hexdigest(), "body_size": len(req.body),
            "file_name": file_part["filename"] if file_part else None,
            "file_size": len(file_part["data"]) if file_part else 0,
            "file_sha256": hashlib.sha256(file_part["data"]).hexdigest() if file_part else None}
    with state.lock:
        state.uploads.append(info)
    return jresp(info)


NEXT_STORE_ITEMS = [{"id": 700 + i, "title": f"Ceramic mug no. {i}", "price": round(8.5 + i * 1.75, 2), "sku": f"MUG-{i:03d}",
                     "stock": (i * 7) % 11, "colour": ["sand", "sage", "slate", "rose"][i % 4]} for i in range(1, 13)]


def _next_store_page() -> Resp:
    """Next.js-style page: server-rendered cards + the full data in a __NEXT_DATA__ blob (no JSON API)."""
    cards = "".join(f'<div class="card" data-id="{x["id"]}"><h3>{esc(x["title"])}</h3><span class="price">£{x["price"]:.2f}</span></div>'
                    for x in NEXT_STORE_ITEMS)
    blob = json.dumps({"props": {"pageProps": {"category": "mugs", "total": len(NEXT_STORE_ITEMS), "products": NEXT_STORE_ITEMS}},
                       "page": "/store/[category]", "query": {"category": "mugs"}, "buildId": "b7x2", "isFallback": False})
    return Resp(200, f"""<!doctype html><html><head><meta charset="utf-8"><title>Mugs – Next Store</title></head><body>
<div id="__next"><h1>Mugs</h1><div class="grid">{cards}</div></div>
<script id="__NEXT_DATA__" type="application/json">{blob}</script></body></html>""")


def _wizard_page() -> Resp:
    body = """<h1>Account setup</h1><div role="tablist" aria-label="Setup steps">
<button role="tab" id="tab-account" aria-selected="true" aria-controls="panel-account">1. Account</button>
<button role="tab" id="tab-profile" aria-selected="false" aria-controls="panel-profile">2. Profile</button>
<button role="tab" id="tab-confirm" aria-selected="false" aria-controls="panel-confirm">3. Confirm</button></div>
<p id="progress">Step 1 of 3</p>
<section role="tabpanel" id="panel-account"><form id="account-form"><label for="w-email">Email</label><input id="w-email" name="email" type="email" required>
<label for="w-pass">Password</label><input id="w-pass" name="password" type="password" required>
<button type="button" id="next-1">Next</button></form></section>
<section role="tabpanel" id="panel-profile" hidden><form id="profile-form"><label for="w-name">Display name</label><input id="w-name" name="display_name">
<label for="w-country">Country</label><select id="w-country" name="country"><option>Bangladesh</option><option>Germany</option></select>
<button type="button" id="next-2">Next</button></form></section>
<section role="tabpanel" id="panel-confirm" hidden><p id="summary"></p><button id="finish">Create account</button></section>
<iframe title="Help widget" src="/lab/frame-b" width="300" height="80"></iframe>"""
    js = """<script>localStorage.setItem('wizard_draft','{"step":1}');sessionStorage.setItem('wizard_tab','t-42');document.cookie='wizard_seen=1;path=/';
function show(n){['account','profile','confirm'].forEach(function(k,i){document.getElementById('panel-'+k).hidden=(i!==n-1);document.getElementById('tab-'+k).setAttribute('aria-selected',String(i===n-1))});
document.getElementById('progress').textContent='Step '+n+' of 3';localStorage.setItem('wizard_draft',JSON.stringify({step:n}))}
document.getElementById('next-1').onclick=function(){show(2)};document.getElementById('next-2').onclick=function(){document.getElementById('summary').textContent='Ready to create '+document.getElementById('w-email').value;show(3)};
document.getElementById('finish').onclick=function(){fetch('/lab/api/wizard/finish',{method:'POST'}).then(function(){document.getElementById('summary').textContent='Account created'})};</script>"""
    return lab_page("Account setup wizard", body, js)


# ═══════════════════════════════════════════════════════════════════════
# lab JSON API
# ═══════════════════════════════════════════════════════════════════════
def lab_api(state: FixtureState, req: Req) -> Optional[Resp]:
    p, method = req.path, req.method
    if p == "/lab/api/stats":
        return jresp({"ok": True, "from": "click", "visitors": 1284, "orders": 37})
    if p == "/lab/api/slow":
        with state.lock:
            state.slow_inflight += 1
            state.slow_peak = max(state.slow_peak, state.slow_inflight)
        try:
            time.sleep(min(10.0, req.qi("ms", 2000) / 1000.0))
        finally:
            with state.lock:
                state.slow_inflight -= 1
        return jresp({"ok": True, "rows": 42, "slow": True, "payload": "x" * 256})
    if p == "/lab/api/poll":
        return jresp({"unread": 0})
    m = re.match(r"^/lab/api/(reviews|wishlist|filter)$", p)
    if m:
        return jresp({"ok": True, "kind": m.group(1), "count": 7})
    m = re.match(r"^/lab/api/details/(\d+)(/stock)?$", p)
    if m:
        return jresp({"id": int(m.group(1)), "stock": 5 if m.group(2) else None})
    if p == "/lab/api/waits/search":
        rng = random.Random(req.q("q") + req.q("profile"))
        time.sleep(_lat(req.q("profile"), rng))
        q = re.sub(r"[^a-z0-9]+", "-", req.q("q").lower()).strip("-") or "empty"
        return jresp({"items": [{"id": f"q-{q}-{i}", "name": f"{req.q('q').title()} result {i}"} for i in range(1, 6)]})
    m = re.match(r"^/lab/api/waits/item/(.+)$", p)
    if m:
        rng = random.Random(m.group(1))
        time.sleep(_lat(req.q("profile"), rng))
        return jresp({"id": m.group(1), "name": f"Item {m.group(1)}"})
    if p == "/lab/api/waits/add" and method == "POST":
        data = req.json or {}
        with state.lock:
            state.waits_adds.append(data.get("id"))
        return jresp({"ok": True, "id": data.get("id")})
    if p == "/lab/api/flaky/done" and method == "POST":
        with state.lock:
            state.flaky_done.setdefault(req.q("run"), set()).add(req.qi("step"))
        return jresp({"ok": True})
    if p == "/lab/api/flaky/save" and method == "POST":
        n = state.count(f"flaky-save:{req.q('run')}")
        if n == 1:
            return jresp({"error": "temporary database error"}, 500)
        return jresp({"ok": True})
    if p in ("/lab/api/main-data", "/lab/api/frame-a-data", "/lab/api/frame-b-data", "/lab/api/popup-data",
             "/lab/api/tab-data", "/lab/api/protected-data", "/lab/api/wizard/finish"):
        return jresp({"ok": True, "source": p.rsplit("/", 1)[-1]})
    if p == "/lab/api/botcheck" and method == "POST":
        s = req.json or {}
        reasons = []
        if s.get("webdriver"):
            reasons.append("navigator.webdriver is true")
        if not s.get("plugins"):
            reasons.append("no plugins")
        if not s.get("languages"):
            reasons.append("no languages")
        if not s.get("chrome"):
            reasons.append("window.chrome missing")
        if "headless" in str(s.get("ua", "")).lower() or any("headless" in str(b).lower() for b in s.get("brands") or []):
            reasons.append("headless user agent")
        if "swiftshader" in str(s.get("gpu", "")).lower():
            reasons.append("software GPU (SwiftShader)")
        verdict = "bot" if reasons else "human"
        with state.lock:
            state.botchecks.append({"verdict": verdict, "reasons": reasons})
        return jresp({"verdict": verdict, "reasons": reasons})
    if p == "/lab/api/recommendations":
        return jresp({"items": [{"id": 1, "name": "Server pick A"}, {"id": 2, "name": "Server pick B"}]})
    if p == "/lab/api/banner":
        return jresp({"text": "Summer sale!"})
    if p == "/lab/api/data-gzip.json":
        body = gzip.compress(integrity_payloads()["json"], mtime=0)
        return Resp(200, body, "application/json", [("Content-Encoding", "gzip")])
    if p == "/lab/api/image.png":
        return Resp(200, integrity_payloads()["png"], "image/png")
    if p == "/lab/api/truncated.bin":
        r = Resp(200, bytes(range(256)) * 20, "application/octet-stream")
        r.mode = "truncate"
        return r
    if p == "/lab/api/truncated.json":
        full = json.dumps({"items": [{"id": i, "name": f"row {i}"} for i in range(50)]})
        return Resp(200, full[: len(full) // 2], "application/json")
    if p == "/lab/api/chunked.txt":
        data = integrity_payloads()["chunked"]
        r = Resp(200, b"", "text/plain")

        def stream(write, data=data):
            for i in range(0, len(data), 700):
                write(data[i:i + 700])
                time.sleep(0.01)

        r.stream = stream
        r.mode = "chunked"
        return r
    m = re.match(r"^/lab/api/steps/(\d+)$", p)
    if m and method == "POST":
        with state.lock:
            runs = state.steps.setdefault(req.q("run"), {})
            runs[int(m.group(1))] = runs.get(int(m.group(1)), 0) + 1
        return jresp({"ok": True, "step": int(m.group(1))})
    if p == "/lab/api/steps/report":
        with state.lock:
            return jresp({"run": req.q("run"), "counts": {str(k): v for k, v in sorted(state.steps.get(req.q("run"), {}).items())}})
    m = re.match(r"^/lab/api/heal/(\w+)$", p)
    if m:
        state.event("heal", action=m.group(1), query=req.raw_query)
        return jresp({"ok": True, "action": m.group(1)})
    m = re.match(r"^/lab/api/rl/(\w+)$", p)
    if m:
        return _rate_limited(state, req, "")
    return None


# ═══════════════════════════════════════════════════════════════════════
# SPA (React Router when available, History-API fallback otherwise)
# ═══════════════════════════════════════════════════════════════════════
REACT_PACKAGES = [
    ("react", "18.3.1", "package/umd/react.production.min.js", "react.js"),
    ("react-dom", "18.3.1", "package/umd/react-dom.production.min.js", "react-dom.js"),
    ("@remix-run/router", "1.23.0", "package/dist/router.umd.min.js", "remix-router.js"),
    ("react-router", "6.30.1", "package/dist/umd/react-router.production.min.js", "react-router.js"),
    ("react-router-dom", "6.30.1", "package/dist/umd/react-router-dom.production.min.js", "react-router-dom.js"),
]


def react_cache_dir() -> Path:
    return Path.home() / ".cache" / "agenttrace" / "react-umd-18.3.1-rr6.30.1"


def ensure_react_bundle(timeout: float = 20.0) -> Optional[Path]:
    """Download React 18 + React Router 6 UMD builds from the npm registry (cached)."""
    target = react_cache_dir()
    if all((target / name).exists() for *_x, name in REACT_PACKAGES):
        return target
    try:
        target.mkdir(parents=True, exist_ok=True)
        for pkg, version, member, name in REACT_PACKAGES:
            if (target / name).exists():
                continue
            base = pkg.split("/")[-1]
            url = f"https://registry.npmjs.org/{pkg}/-/{base}-{version}.tgz"
            data = urllib.request.urlopen(url, timeout=timeout).read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                fh = tf.extractfile(member)
                if fh is None:
                    return None
                (target / name).write_bytes(fh.read())
        return target
    except Exception:  # noqa: BLE001 - offline: caller falls back to the vanilla SPA
        return None


SPA_DATA = {
    "/api/spa/home": {"headline": "Welcome to SPA Shop", "promos": 3},
    "/api/spa/products": {"items": [{"id": i, "name": f"SPA product {i}"} for i in range(1, 7)]},
    "/api/spa/cart": {"items": [], "count": 0},
    "/api/spa/about": {"company": "SPA Shop Ltd", "founded": 2019},
}

REACT_APP_JS = r"""
const e = React.createElement; const RR = ReactRouterDOM;
function useData(url){const [d,setD]=React.useState(null);React.useEffect(()=>{setD(null);fetch(url).then(r=>r.json()).then(setD)},[url]);return d;}
function Nav(){return e('nav',{'aria-label':'SPA'},e(RR.Link,{to:'/',id:'nav-home'},'Home'),' ',e(RR.Link,{to:'/products',id:'nav-products'},'Products'),' ',e(RR.Link,{to:'/cart',id:'nav-cart'},'Cart'),' ',e(RR.Link,{to:'/about',id:'nav-about'},'About'));}
function Home(){const d=useData('/api/spa/home');React.useEffect(()=>{document.title='Home - SPA'});return e('h1',{id:'view'},d?d.headline:'Loading…');}
function Products(){const d=useData('/api/spa/products');React.useEffect(()=>{document.title='Products - SPA'});return e('div',null,e('h1',{id:'view'},'Products'),d&&e('ul',null,d.items.map(p=>e('li',{key:p.id},e(RR.Link,{to:'/products/'+p.id,className:'product-link'},p.name)))));}
function Product(){const {id}=RR.useParams();const d=useData('/api/spa/products/'+id);React.useEffect(()=>{document.title='Product '+id+' - SPA'});return e('h1',{id:'view'},d?d.name:'Loading…');}
function Cart(){const d=useData('/api/spa/cart');React.useEffect(()=>{document.title='Cart - SPA'});return e('h1',{id:'view'},'Cart ('+(d?d.count:'…')+')');}
function About(){const d=useData('/api/spa/about');React.useEffect(()=>{document.title='About - SPA'});return e('h1',{id:'view'},d?'About '+d.company:'Loading…');}
function App(){return e(RR.BrowserRouter,null,e(Nav),e(RR.Routes,null,e(RR.Route,{path:'/',element:e(Home)}),e(RR.Route,{path:'/products',element:e(Products)}),e(RR.Route,{path:'/products/:id',element:e(Product)}),e(RR.Route,{path:'/cart',element:e(Cart)}),e(RR.Route,{path:'/about',element:e(About)})));}
ReactDOM.createRoot(document.getElementById('root')).render(e(App));
window.__SPA_FRAMEWORK='react-router';
"""

VANILLA_APP_JS = r"""
function load(url,cb){fetch(url).then(r=>r.json()).then(cb)}
var routes=[[/^\/$/,function(){document.title='Home - SPA';load('/api/spa/home',function(d){view(d.headline)})}],
[/^\/products$/,function(){document.title='Products - SPA';load('/api/spa/products',function(d){view('Products','<ul>'+d.items.map(function(p){return '<li><a class="product-link" href="/products/'+p.id+'">'+p.name+'</a></li>'}).join('')+'</ul>')})}],
[/^\/products\/(\d+)$/,function(m){document.title='Product '+m[1]+' - SPA';load('/api/spa/products/'+m[1],function(d){view(d.name)})}],
[/^\/cart$/,function(){document.title='Cart - SPA';load('/api/spa/cart',function(d){view('Cart ('+d.count+')')})}],
[/^\/about$/,function(){document.title='About - SPA';load('/api/spa/about',function(d){view('About '+d.company)})}]];
function view(h,extra){document.getElementById('root').innerHTML=nav()+'<h1 id="view">'+h+'</h1>'+(extra||'')}
function nav(){return '<nav aria-label="SPA"><a id="nav-home" href="/">Home</a> <a id="nav-products" href="/products">Products</a> <a id="nav-cart" href="/cart">Cart</a> <a id="nav-about" href="/about">About</a></nav>'}
function render(){for(var i=0;i<routes.length;i++){var m=location.pathname.match(routes[i][0]);if(m){routes[i][1](m);return}}view('Not found')}
document.addEventListener('click',function(e){var a=e.target.closest('a');if(a&&a.getAttribute('href').charAt(0)==='/'){e.preventDefault();history.pushState({},'',a.getAttribute('href'));render()}});
window.addEventListener('popstate',render);render();window.__SPA_FRAMEWORK='vanilla-history';
"""


def spa_response(state: FixtureState, req: Req, react_dir: Optional[Path]) -> Resp:
    p = req.path
    if p.startswith("/vendor/") and react_dir is not None:
        f = react_dir / p.split("/")[-1]
        if f.exists():
            return Resp(200, f.read_bytes(), "application/javascript", [("Cache-Control", "max-age=3600")])
        return Resp(404, "no such vendor file", "text/plain")
    if p in SPA_DATA:
        time.sleep(0.05)
        return jresp(SPA_DATA[p])
    m = re.match(r"^/api/spa/products/(\d+)$", p)
    if m:
        return jresp({"id": int(m.group(1)), "name": f"SPA product {m.group(1)}", "price": 10 + int(m.group(1))})
    if p.startswith("/api/"):
        return jresp({"error": "not found"}, 404)
    use_react = react_dir is not None and req.q("framework") != "vanilla"
    if use_react:
        scripts = "".join(f'<script src="/vendor/{name}"></script>' for *_x, name in REACT_PACKAGES)
        scripts += f"<script>{REACT_APP_JS}</script>"
    else:
        scripts = f"<script>{VANILLA_APP_JS}</script>"
    return Resp(200, f"""<!doctype html><html><head><meta charset="utf-8"><title>SPA</title></head><body><div id="root"></div>{scripts}</body></html>""")


# ═══════════════════════════════════════════════════════════════════════
# vault (cookie + localStorage + IndexedDB + sessionStorage app)
# ═══════════════════════════════════════════════════════════════════════
VAULT_JS = r"""
function idb(){return new Promise(function(res,rej){var r=indexedDB.open('vault',1);r.onupgradeneeded=function(){r.result.createObjectStore('tokens')};r.onsuccess=function(){res(r.result)};r.onerror=rej})}
function idbGet(k){return idb().then(function(db){return new Promise(function(res){var tx=db.transaction('tokens','readonly');var q=tx.objectStore('tokens').get(k);q.onsuccess=function(){res(q.result||null)};q.onerror=function(){res(null)}})})}
function idbPut(k,v){return idb().then(function(db){return new Promise(function(res){var tx=db.transaction('tokens','readwrite');tx.objectStore('tokens').put(v,k);tx.oncomplete=function(){res(true)}})})}
async function status(){var token=await idbGet('access');var profile=localStorage.getItem('vault_profile');var st=document.getElementById('state');
 if(!token||!profile){st.textContent='Logged out';st.dataset.state='out';return}
 var r=await fetch('/api/me',{headers:{'Authorization':'Bearer '+token}});if(!r.ok){st.textContent='Session invalid ('+r.status+')';st.dataset.state='invalid';return}
 var me=await r.json();st.textContent='Logged in as '+me.user+' ('+JSON.parse(profile).plan+')';st.dataset.state='in';
 document.getElementById('tabinfo').textContent='tab nonce: '+(sessionStorage.getItem('vault_tab')||'none')}
async function login(){var r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user:document.getElementById('user').value,pass:document.getElementById('pass').value})});
 if(!r.ok){document.getElementById('state').textContent='Login failed';return}var d=await r.json();await idbPut('access',d.token);localStorage.setItem('vault_profile',JSON.stringify(d.profile));sessionStorage.setItem('vault_tab',d.tab_nonce);status()}
document.getElementById('login').onclick=login;status();
"""


def vault_response(state: FixtureState, req: Req) -> Resp:
    p = req.path
    if p == "/api/login" and req.method == "POST":
        body = req.json or {}
        if body.get("user") != "keeper" or body.get("pass") != "open-sesame":
            return jresp({"error": "bad credentials"}, 401)
        sid = secrets.token_hex(12)
        token = "vt_" + secrets.token_hex(16)
        with state.lock:
            state.vault_sessions[sid] = token
        return jresp({"token": token, "profile": {"user": "keeper", "plan": "gold"}, "tab_nonce": secrets.token_hex(4)}
                     ).cookie("vault_sid", sid, max_age=86400)
    if p == "/api/me":
        sid = req.cookies.get("vault_sid")
        auth = req.headers.get("authorization", "")
        with state.lock:
            expected = state.vault_sessions.get(sid or "")
        if not expected or auth != f"Bearer {expected}":
            return jresp({"error": "unauthorized", "cookie": bool(expected)}, 401)
        return jresp({"user": "keeper", "plan": "gold"})
    return Resp(200, f"""<!doctype html><html><head><meta charset="utf-8"><title>Vault</title></head><body>
<h1>Vault</h1><p id="state" data-state="unknown">checking…</p><p id="tabinfo"></p>
<input id="user" placeholder="user"><input id="pass" type="password" placeholder="password"><button id="login">Log in</button>
<script>{VAULT_JS}</script></body></html>""")


# ═══════════════════════════════════════════════════════════════════════
# news — WordPress-like second site (config-only onboarding test)
# ═══════════════════════════════════════════════════════════════════════
NEWS_POSTS = [{"id": 100 + i, "slug": f"story-{i}-{w}", "title": f"{w.title()} story number {i}",
               "date": f"2026-0{1 + i % 9}-{10 + i % 18:02d}", "author": ["Nadia", "Karim", "Eva"][i % 3],
               "excerpt": f"An in-depth look at {w} (part {i})."}
              for i, w in enumerate(["markets", "science", "climate", "football", "cinema", "travel", "health", "tech",
                                     "music", "books", "startups", "space", "energy", "cricket", "food", "design",
                                     "history", "ai", "ocean", "cities", "mobility", "art", "education", "jobs",
                                     "housing", "water", "farming", "crypto", "games", "fashion", "robots", "rail",
                                     "privacy", "chess", "weather", "birds", "coffee", "tea", "maps", "radio",
                                     "phones", "cameras", "bridges", "rivers", "forests", "deserts", "islands",
                                     "volcanoes", "music-2", "poetry"], start=1)]


def news_response(state: FixtureState, req: Req) -> Resp:
    p = req.path
    per = 10
    if p == "/wp-json/wp/v2/posts":
        page = max(1, req.qi("page", 1))
        per_page = max(1, min(50, req.qi("per_page", per)))
        chunk = NEWS_POSTS[(page - 1) * per_page: page * per_page]
        total_pages = (len(NEWS_POSTS) + per_page - 1) // per_page
        return jresp([{"id": x["id"], "date": x["date"], "slug": x["slug"], "title": {"rendered": x["title"]},
                       "excerpt": {"rendered": f"<p>{x['excerpt']}</p>"}, "link": f"http://news.agenttrace.test/{x['slug']}/"}
                      for x in chunk], headers=[("X-WP-Total", str(len(NEWS_POSTS))), ("X-WP-TotalPages", str(total_pages))])
    if p in ("/wp-login.php",):
        if req.method == "POST":
            form = req.form
            if form.get("log") == "editor" and form.get("pwd") == "press-pass":
                token = secrets.token_hex(10)
                with state.lock:
                    state.news_sessions.add(token)
                return redirect(form.get("redirect_to") or "/members/", 302).cookie("wordpress_logged_in_a1b2", token)
            return Resp(200, _news_layout("Log In", "<div id='login_error'>Error: incorrect password.</div>" + _news_login_form()))
        return Resp(200, _news_layout("Log In", _news_login_form()))
    if p == "/members/":
        with state.lock:
            ok = req.cookies.get("wordpress_logged_in_a1b2") in state.news_sessions
        if not ok:
            return redirect("/wp-login.php?redirect_to=/members/")
        return Resp(200, _news_layout("Members", "<h1 class='entry-title'>Members area</h1><p class='members-only'>Premium analysis archive</p>"))
    m = re.match(r"^/page/(\d+)/$", p)
    page = int(m.group(1)) if m else (1 if p == "/" else None)
    if page is not None:
        pages = (len(NEWS_POSTS) + per - 1) // per
        if page > pages:
            return Resp(404, _news_layout("Not found", "<h1>Page not found</h1>"))
        chunk = NEWS_POSTS[(page - 1) * per: page * per]
        arts = "".join(f"""<article class="post type-post" id="post-{x['id']}"><header class="entry-header"><h2 class="entry-title"><a href="/{x['slug']}/" rel="bookmark">{esc(x['title'])}</a></h2>
<div class="entry-meta"><time class="entry-date" datetime="{x['date']}">{x['date']}</time> by <span class="author vcard">{x['author']}</span></div></header>
<div class="entry-summary"><p>{esc(x['excerpt'])}</p></div></article>""" for x in chunk)
        nav = ""
        if page < pages:
            nav += f'<div class="nav-previous"><a href="/page/{page + 1}/" rel="next">← Older posts</a></div>'
        if page > 1:
            nav += f'<div class="nav-next"><a href="{"/" if page == 2 else f"/page/{page - 1}/"}" rel="prev">Newer posts →</a></div>'
        return Resp(200, _news_layout("The Daily Fixture", f'<main id="primary">{arts}<nav class="navigation posts-navigation" aria-label="Posts">{nav}</nav></main>'))
    for x in NEWS_POSTS:
        if p == f"/{x['slug']}/":
            ld = json.dumps({"@context": "https://schema.org", "@type": "NewsArticle", "headline": x["title"],
                             "datePublished": x["date"], "author": {"@type": "Person", "name": x["author"]}})
            return Resp(200, _news_layout(x["title"], f"""<article class="post"><h1 class="entry-title">{esc(x['title'])}</h1><time datetime="{x['date']}">{x['date']}</time>
<div class="entry-content"><p>{esc(x['excerpt'])} Full text of the story.</p></div></article>""", head=f'<script type="application/ld+json">{ld}</script>'))
    return Resp(404, _news_layout("Not found", "<h1>Page not found</h1>"))


def _news_login_form() -> str:
    return """<form name="loginform" id="loginform" action="/wp-login.php" method="post"><p><label for="user_login">Username or Email Address</label>
<input type="text" name="log" id="user_login" class="input"></p><p><label for="user_pass">Password</label><input type="password" name="pwd" id="user_pass" class="input"></p>
<input type="hidden" name="redirect_to" value="/members/"><p class="submit"><input type="submit" name="wp-submit" id="wp-submit" class="button button-primary" value="Log In"></p></form>"""


def _news_layout(title: str, body: str, head: str = "") -> str:
    return f"""<!doctype html><html lang="en-US"><head><meta charset="UTF-8"><title>{esc(title)} &#8211; The Daily Fixture</title>
<link rel="alternate" type="application/json" href="http://news.agenttrace.test/wp-json/wp/v2/posts">{head}</head>
<body class="home blog"><header id="masthead" class="site-header"><p class="site-title"><a href="/">The Daily Fixture</a></p>
<nav id="site-navigation"><a href="/">News</a> <a href="/members/">Members</a> <a href="/wp-login.php">Log in</a></nav></header>{body}
<footer class="site-footer">Proudly powered by WordPress (fixture)</footer></body></html>"""



# ════════════════════════════════════════════════════════════════════════════
# fixture_server.py — test fixtures: HTTP server + recording proxies
# ════════════════════════════════════════════════════════════════════════════
# The fixture HTTP server: virtual hosts, streaming, fault injection,
# WebSocket (RFC 6455) and recording forward proxies.


def _ws_read_frame(rfile) -> tuple:
    head = rfile.read(2)
    if len(head) < 2:
        return 8, b""
    b1, b2 = head[0], head[1]
    opcode = b1 & 0x0F
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    mask = rfile.read(4) if b2 & 0x80 else b"\x00\x00\x00\x00"
    data = rfile.read(length)
    return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def _ws_frame(data: bytes, opcode: int = 1) -> bytes:
    n = len(data)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + data


class _FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    fixture: "FixtureServer"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 90
    server: _FixtureHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default stderr logging
        return

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = b""
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line.split(b";")[0] or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    break
                out += self.rfile.read(size)
                self.rfile.readline()
            return out
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def _handle(self) -> None:
        fx = self.server.fixture
        headers = {k.lower(): v for k, v in self.headers.items()}
        host = headers.get("host", "").split(":")[0].lower()
        body = self._read_body() if self.command in ("POST", "PUT", "PATCH", "DELETE") else b""
        req = Req(self.command, host, self.path, headers, body, self.client_address[0])
        req.headers["x-fixture-port"] = str(fx.port)
        fx.state.log_request(req)
        if req.path.startswith("/ws/") and headers.get("upgrade", "").lower() == "websocket":
            self._websocket(req)
            return
        try:
            resp = fx.route(req)
        except Exception:  # noqa: BLE001 - surface fixture bugs to the test output
            resp = Resp(500, "fixture error:\n" + traceback.format_exc(), "text/plain")
        self._send(resp)

    def _send(self, resp: Resp) -> None:
        if resp.delay:
            time.sleep(resp.delay)
        if resp.mode == "reset":
            try:
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            except OSError:
                pass
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return
        self.send_response(resp.status)
        for name, value in resp.headers:
            self.send_header(name, value)
        if resp.mode in ("stream", "chunked"):
            if resp.mode == "chunked":
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def write(data: bytes) -> None:
                if resp.mode == "chunked":
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                else:
                    self.wfile.write(data)
                self.wfile.flush()

            try:
                resp.stream(write)
                if resp.mode == "chunked":
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        body = resp.body if self.command != "HEAD" else b""
        self.send_header("Content-Length", str(len(resp.body)))
        if resp.mode == "truncate":
            self.send_header("Connection", "close")
        self.end_headers()
        if resp.mode == "truncate":
            self.wfile.write(body[: max(1, len(body) // 4)])
            self.wfile.flush()
            self.close_connection = True
            try:
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                self.connection.close()
            except OSError:
                pass
            return
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _websocket(self, req: Req) -> None:
        fx = self.server.fixture
        key = req.headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        conn_id = f"srv-ws-{len(fx.ws_log) + 1}"
        log = {"id": conn_id, "path": req.path, "messages": []}
        fx.ws_log.append(log)

        def send(obj: dict) -> None:
            data = json.dumps(obj).encode()
            log["messages"].append({"dir": "server->client", "t": time.time(), "data": obj})
            self.wfile.write(_ws_frame(data))
            self.wfile.flush()

        try:
            send({"type": "welcome", "conn": conn_id})
            received = 0
            while True:
                opcode, data = _ws_read_frame(self.rfile)
                if opcode == 8:
                    self.wfile.write(_ws_frame(data[:2] or b"\x03\xe8", 8))
                    self.wfile.flush()
                    break
                if opcode == 9:
                    self.wfile.write(_ws_frame(data, 10))
                    self.wfile.flush()
                    continue
                if opcode not in (1, 2):
                    continue
                received += 1
                text = data.decode("utf-8", "replace")
                log["messages"].append({"dir": "client->server", "t": time.time(), "data": text})
                send({"type": "echo", "n": received, "echo": text})
                if received in (5, 10):
                    send({"type": "tick", "after": received, "server_time": time.time()})
        except (ConnectionResetError, BrokenPipeError, OSError, ValueError):
            pass
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle


class RecordingProxy:
    """Plain HTTP forward proxy that records what each client sent through it."""

    def __init__(self, tag: str, fixture: "FixtureServer") -> None:
        self.tag = tag
        self.fixture = fixture
        self.log: list = []
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = 60

            def log_message(self, *a: Any) -> None:
                return

            def _forward(self) -> None:
                target = urlsplit(self.path)
                host = (target.hostname or self.headers.get("Host", "")).lower()
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ("proxy-connection", "proxy-authorization")}
                proxy.log.append({"t": time.time(), "method": self.command, "url": self.path, "host": host,
                                  "headers": {k.lower(): v for k, v in headers.items()},
                                  "proxy_auth": bool(self.headers.get("Proxy-Authorization"))})
                if not (host.endswith(FIXTURE_DOMAIN) or host in NOISE_HOSTS):
                    self.send_response(502)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                headers["X-Proxy-Tag"] = proxy.tag
                path = target.path + (f"?{target.query}" if target.query else "")
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", proxy.fixture.port, timeout=30)
                    conn.request(self.command, path or "/", body=body or None, headers=headers)
                    resp = conn.getresponse()
                    data = resp.read()
                    self.send_response(resp.status, resp.reason)
                    for k, v in resp.getheaders():
                        if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                            self.send_header(k, v)
                    self.send_header("Via", f"1.1 agenttrace-proxy-{proxy.tag}")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    conn.close()
                except OSError as exc:
                    msg = f"proxy error: {exc}".encode()
                    self.send_response(502)
                    self.send_header("Content-Length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)

            def do_CONNECT(self) -> None:
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name=f"proxy-{tag}", daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class FixtureServer:
    """Start once per test run; point browsers at it with :meth:`profile`."""

    def __init__(self, *, react: Optional[bool] = None) -> None:
        self.state = FixtureState()
        self._server: Optional[_FixtureHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.ws_log: list = []
        self.proxies: list = []
        self._react_pref = react
        self.react_dir: Optional[Path] = None
        self._log = get_logger("fixture")

    def start(self) -> "FixtureServer":
        if self._server is not None:
            return self
        if self._react_pref is not False:
            self.react_dir = ensure_react_bundle()
        self._server = _FixtureHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fixture = self
        self._thread = threading.Thread(target=self._server.serve_forever, name="fixture", daemon=True)
        self._thread.start()
        self._log.info("fixture server on 127.0.0.1:%s (react=%s)", self.port, bool(self.react_dir))
        return self

    def stop(self) -> None:
        for proxy in self.proxies:
            try:
                proxy.stop()
            except Exception:  # noqa: BLE001
                pass
        self.proxies.clear()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "FixtureServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("fixture server not started")
        return self._server.server_address[1]

    def url(self, path: str = "/", site: str = "shop") -> str:
        return f"http://{site}.{FIXTURE_DOMAIN}{path if path.startswith('/') else '/' + path}"

    def ws_url(self, path: str) -> str:
        return f"ws://127.0.0.1:{self.port}{path}"

    def host_map(self) -> dict:
        return {"*": f"127.0.0.1:{self.port}", "127.0.0.1": None, "localhost": None}

    def profile(self, **overrides: Any) -> BrowserProfile:
        base = dict(host_map=self.host_map(), no_proxy_server=True, navigation_timeout_ms=20_000,
                    action_timeout_ms=8_000)
        base.update(overrides)
        return BrowserProfile(**base)

    def reset(self) -> None:
        self.state.reset()
        self.ws_log.clear()

    def start_proxy(self, tag: str) -> RecordingProxy:
        proxy = RecordingProxy(tag, self)
        self.proxies.append(proxy)
        return proxy

    def requests(self, *, host: Optional[str] = None, path_prefix: str = "", since: float = 0.0) -> list:
        with self.state.lock:
            items = list(self.state.request_log)
        return [r for r in items if r["t"] >= since and (host is None or r["host"] == host)
                and r["path"].startswith(path_prefix)]

    def route(self, req: Req) -> Resp:
        host = req.host
        if host == f"shop.{FIXTURE_DOMAIN}":
            return shop_response(self.state, req)
        if host == f"cdn.{FIXTURE_DOMAIN}":
            return cdn_response(req)
        if host == f"lab.{FIXTURE_DOMAIN}":
            return lab_response(self.state, req)
        if host == f"spa.{FIXTURE_DOMAIN}":
            return spa_response(self.state, req, self.react_dir)
        if host == f"vault.{FIXTURE_DOMAIN}":
            return vault_response(self.state, req)
        if host == f"news.{FIXTURE_DOMAIN}":
            return news_response(self.state, req)
        if host in ("127.0.0.1", "localhost"):
            body = "<h1>AgentTrace fixture</h1><ul>" + "".join(
                f"<li>http://{s}.{FIXTURE_DOMAIN}/</li>" for s in ("shop", "lab", "spa", "vault", "news")) + "</ul>"
            return Resp(200, body)
        return third_party_response(req)



# ════════════════════════════════════════════════════════════════════════════
# selftest.py — built-in real-world self-test (python agenttrace.py selftest)
# ════════════════════════════════════════════════════════════════════════════
# Built-in, real-world test-suite — ``python agenttrace.py selftest``.
#
# Every test maps to one knowledge.md part and checks that part's *success
# criteria* against realistic local sites (``FixtureServer``: real tracker
# hostnames, SPA, CAPTCHA wall, 429s, SSE, WebSocket, flaky endpoints …).
# Tests marked ``live`` hit real websites and run automatically when the
# internet is reachable (``--live on|off|auto``).  Each test writes its HARs,
# logs and reports to its own folder for inspection.


@dataclass
class SelfTest:
    part: int
    name: str
    title: str
    fn: Callable
    live: bool = False


SELFTESTS: list = []


def selftest(part: int, title: str, *, live: bool = False) -> Callable:
    def deco(fn: Callable) -> Callable:
        SELFTESTS.append(SelfTest(part, fn.__name__[2:] if fn.__name__.startswith("t_") else fn.__name__, title, fn, live))
        return fn
    return deco


class TestFailed(AssertionError):
    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class TestSkipped(Exception):
    pass


def self_command() -> list:
    """Command that starts *this* module's CLI in a child process."""
    if __package__:
        return [sys.executable, "-m", __package__]
    return [sys.executable, os.path.abspath(__file__)]


def module_root() -> str:
    here = Path(__file__).resolve()
    return str(here.parents[1] if __package__ else here.parent)


def child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = module_root() + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_child(code: str, *, timeout: float = 120) -> dict:
    """Run python code in a fresh process with the module importable as ``at``."""
    prelude = f"import sys, json; sys.path.insert(0, {module_root()!r}); import agenttrace as at\n"
    proc = subprocess.run([sys.executable, "-c", prelude + code], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          env=child_env())
    out = proc.stdout.strip().splitlines()
    try:
        data = json.loads(out[-1]) if out else {}
    except json.JSONDecodeError:
        data = {"raw": proc.stdout[-2000:]}
    data["_returncode"] = proc.returncode
    if proc.returncode != 0:
        data["_stderr"] = proc.stderr[-3000:]
    return data


class T:
    """Context handed to every test."""

    def __init__(self, case: SelfTest, fx: FixtureServer, out: Path, quick: bool) -> None:
        self.case = case
        self.fx = fx
        self.out = out
        self.quick = quick
        self.lines: list = []
        self.metrics: dict = {}
        self.checks = 0
        self._cleanups: list = []
        out.mkdir(parents=True, exist_ok=True)

    def check(self, cond: Any, message: str, **details: Any) -> None:
        self.checks += 1
        if cond:
            self.lines.append(f"  ok   {message}")
            return
        extra = json.dumps(details, default=str, ensure_ascii=False)[:1500] if details else ""
        self.lines.append(f"  FAIL {message} {extra}")
        raise TestFailed(message, details)

    def note(self, message: str) -> None:
        self.lines.append(f"  ..   {message}")

    def metric(self, key: str, value: Any) -> None:
        self.metrics[key] = value

    def skip(self, reason: str) -> None:
        raise TestSkipped(reason)

    def reps(self, full: int, quick: int) -> int:
        return quick if self.quick else full

    def session(self, name: str = "s", *, profile_overrides: Optional[dict] = None, **kw: Any) -> Session:
        profile = kw.pop("profile", None) or self.fx.profile(**(profile_overrides or {}))
        kw.setdefault("hitl", "off")
        s = Session(profile=profile, out_dir=self.out / name, name=name, **kw)
        self._cleanups.append(lambda: s.finish() if s._started and not s._finished else None)
        return s

    def live_session(self, name: str = "live", **kw: Any) -> Session:
        kw.setdefault("hitl", "off")
        s = Session(out_dir=self.out / name, name=name, **kw)
        self._cleanups.append(lambda: s.finish() if s._started and not s._finished else None)
        return s

    def add_cleanup(self, fn: Callable) -> None:
        self._cleanups.append(fn)

    def cleanup(self) -> None:
        for fn in reversed(self._cleanups):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self.lines.append(f"  ..   cleanup error: {exc}")


def _check_live(mode: str) -> tuple:
    if mode == "off":
        return False, "disabled (--live off)"
    try:
        urllib.request.urlopen("https://books.toscrape.com/", timeout=10).read(200)
        return True, "internet reachable"
    except Exception as exc:  # noqa: BLE001
        reason = f"internet not reachable ({str(exc)[:120]})"
        return (mode == "on"), reason


def run_selftests(*, pattern: Optional[str] = None, parts: Optional[list] = None, live: str = "auto",
                  quick: bool = False, out_dir: Any = None, fail_fast: bool = False, list_only: bool = False) -> int:
    configure_logging("CRITICAL", console=True, stream=sys.stderr)
    chosen = [t for t in SELFTESTS if (not parts or t.part in parts)
              and (not pattern or pattern.lower() in t.name.lower() or pattern.lower() in t.title.lower())]
    chosen.sort(key=lambda t: (t.part, SELFTESTS.index(t)))
    if list_only:
        for t in chosen:
            sys.stdout.write(f"P{t.part:02d}  {t.name:40} {'[live] ' if t.live else ''}{t.title}\n")
        return 0
    out = (Path(out_dir) if out_dir else Path("agenttrace_selftest") / stamp()).resolve()
    out.mkdir(parents=True, exist_ok=True)
    fx = FixtureServer().start()
    live_ok, live_reason = _check_live(live) if any(t.live for t in chosen) else (False, "no live tests selected")
    sys.stderr.write(f"AgentTrace selftest: {len(chosen)} tests, fixture on 127.0.0.1:{fx.port}, "
                     f"react-router={'yes' if fx.react_dir else 'no (vanilla SPA)'}, live={live_ok} ({live_reason})\n"
                     f"artifacts: {out.resolve()}\n\n")
    results = []
    t_all = time.monotonic()
    for case in chosen:
        folder = out / f"p{case.part:02d}_{case.name}"
        if case.live and not live_ok:
            results.append({"part": case.part, "name": case.name, "title": case.title, "status": "skipped",
                            "live": True, "reason": live_reason, "seconds": 0})
            sys.stderr.write(f"P{case.part:02d} SKIP   {case.name:40} (live: {live_reason[:60]})\n")
            continue
        ctx = T(case, fx, folder, quick)
        fx.reset()
        t0 = time.monotonic()
        status, error, tb, details = "passed", None, None, None
        try:
            case.fn(ctx)
        except TestSkipped as exc:
            status, error = "skipped", str(exc)
        except TestFailed as exc:
            status, error, details = "failed", str(exc), exc.details
            tb = traceback.format_exc(limit=6)
        except Exception as exc:  # noqa: BLE001
            status, error, tb = "error", f"{type(exc).__name__}: {str(exc)[:600]}", traceback.format_exc(limit=12)
        finally:
            ctx.cleanup()
        secs = round(time.monotonic() - t0, 1)
        log = [f"P{case.part:02d} {case.name}: {case.title}", f"status: {status} ({secs}s)", *ctx.lines]
        if ctx.metrics:
            log.append("metrics: " + json.dumps(ctx.metrics, default=str, ensure_ascii=False))
        if error:
            log.append(f"error: {error}")
        if tb:
            log.append(tb)
        write_text(folder / "test.log", "\n".join(log) + "\n")
        results.append({"part": case.part, "name": case.name, "title": case.title, "status": status, "live": case.live,
                        "seconds": secs, "checks": ctx.checks, "metrics": ctx.metrics, "error": error,
                        "details": details, "log": str(folder / "test.log")})
        brief = error if error else ", ".join(f"{k}={v}" for k, v in list(ctx.metrics.items())[:4])
        sys.stderr.write(f"P{case.part:02d} {status.upper():6} {case.name:40} {secs:6.1f}s  {str(brief)[:110]}\n")
        sys.stderr.flush()
        if fail_fast and status in ("failed", "error"):
            break
    fx.stop()
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("passed", "failed", "error", "skipped")}
    by_part: dict = {}
    for r in results:
        by_part.setdefault(r["part"], []).append(r)
    part_status = {p: ("passed" if all(x["status"] in ("passed", "skipped") for x in rs) and any(x["status"] == "passed" for x in rs)
                       else "skipped" if all(x["status"] == "skipped" for x in rs) else "failed")
                   for p, rs in sorted(by_part.items())}
    report = {"generated": iso(), "python": sys.version.split()[0], "platform": sys.platform, "live": live_ok,
              "live_reason": live_reason, "duration_s": round(time.monotonic() - t_all, 1), "counts": counts,
              "parts": part_status, "results": results}
    write_json(out / "selftest_report.json", report)
    md = ["# AgentTrace selftest report", "", f"- when: {report['generated']} · python {report['python']} · {sys.platform}",
          f"- result: **{counts['passed']} passed, {counts['failed']} failed, {counts['error']} errors, {counts['skipped']} skipped** "
          f"in {report['duration_s']}s", f"- live tests: {'ran' if live_ok else 'skipped'} ({live_reason})", "",
          "| part | test | status | time | key metrics |", "|---|---|---|---|---|"]
    for r in results:
        m = ", ".join(f"{k}={v}" for k, v in list((r.get("metrics") or {}).items())[:5])
        md.append(f"| {r['part']} | {r['name']}{' (live)' if r['live'] else ''} | {r['status']} | {r['seconds']}s | "
                  f"{(r.get('error') or m)[:160]} |")
    write_text(out / "selftest_report.md", "\n".join(md) + "\n")
    sys.stderr.write(f"\n{counts['passed']} passed, {counts['failed']} failed, {counts['error']} errors, "
                     f"{counts['skipped']} skipped in {report['duration_s']}s — report: {out / 'selftest_report.md'}\n")
    return 0 if counts["failed"] == 0 and counts["error"] == 0 else 1


# ═══════════════════════════════════════════════════════════════════════
# Part 1 — core browser engine
# ═══════════════════════════════════════════════════════════════════════
@selftest(1, "Launch browser, open 6 different sites, wait for full load, report title/URL correctly")
def t_engine_six_sites(t: T) -> None:
    fx = t.fx
    p2 = fx.state.catalog.by_id[2]
    pages = [("shop-home", fx.url("/"), "Home | ShopLab"),
             ("catalogue", fx.url("/catalogue/page-2.html"), "All products - Page 2 | ShopLab"),
             ("product", fx.url(fx.state.catalog.url(p2)), f"{p2['name']} | ShopLab"),
             ("news", fx.url("/", "news"), "The Daily Fixture – The Daily Fixture"),
             ("lab", fx.url("/lab/click", "lab"), "Click lab"),
             ("spa", fx.url("/", "spa"), "Home - SPA")]
    with BrowserEngine(fx.profile()) as eng:
        t.check(eng.version, f"browser launched ({eng.launch_info.get('how')} {eng.version})")
        ctx = eng.new_context()
        page = ctx.new_page()
        for label, url, want in pages:
            resp = page.goto(url, wait_until="load")
            page.wait_for_function("() => document.readyState === 'complete'")
            if label == "spa":
                page.wait_for_function("() => document.title.endsWith('- SPA')")
            t.check(resp is not None and resp.status == 200, f"{label}: HTTP 200", status=resp.status if resp else None)
            t.check(page.url.split("#")[0].rstrip("/") == url.rstrip("/") or page.url.startswith(url.rstrip("/")),
                    f"{label}: final URL {page.url}")
            t.check(page.title() == want, f"{label}: title {page.title()!r}", want=want)
        ctx.close()
    t.metric("sites", len(pages))


@selftest(1, "Live: 5 real websites load with correct title/URL (no errors)", live=True)
def t_engine_live_sites(t: T) -> None:
    sites = [("https://example.com/", "Example Domain", "example.com"),
             ("https://books.toscrape.com/", "Books to Scrape", "books.toscrape.com"),
             ("https://quotes.toscrape.com/", "Quotes to Scrape", "quotes.toscrape.com"),
             ("https://www.python.org/", "Python", "python.org"),
             ("https://en.wikipedia.org/wiki/Web_scraping", "Web scraping", "wikipedia.org")]
    ok = 0
    with t.live_session() as s:
        for url, title_part, host in sites:
            r = s.goto(url)
            good = r["ok"] and r.get("status") == 200 and title_part.lower() in (r["title"] or "").lower() and host in r["url"]
            t.note(f"{url} -> {r.get('status')} {r['title']!r}")
            ok += bool(good)
    t.metric("sites_ok", f"{ok}/{len(sites)}")
    t.check(ok == len(sites), f"{ok}/{len(sites)} live sites loaded with correct title/URL")


# ═══════════════════════════════════════════════════════════════════════
# Part 2 — basic network capture
# ═══════════════════════════════════════════════════════════════════════
@selftest(2, "Page visit → valid HAR 1.2 whose entry count equals the DevTools (CDP) request count")
def t_har_matches_devtools(t: T) -> None:
    fx = t.fx
    urls = [fx.url("/"), fx.url("/catalogue/page-3.html"), fx.url(fx.state.catalog.url(fx.state.catalog.by_id[5])),
            fx.url("/", "news"), fx.url("/search?q=lamp")]
    totals = []
    for i, url in enumerate(urls, start=1):
        with t.session(f"page{i}") as s:
            s.start()
            ids: list = []
            cdp = s.context.new_cdp_session(s.page)
            cdp.send("Network.enable")
            cdp.on("Network.requestWillBeSent", lambda e: ids.append(e["requestId"]))
            s.goto(url)
            n_rec, n_cdp = -1, -2
            for _ in range(8):
                s.recorder.pump(250, s.page)
                n_rec, n_cdp = len(s.recorder.records), len(ids)
                if n_rec == n_cdp:
                    break
            har = s.har()
            path = s.save_har(t.out / f"page{i}.har")
            problems = validate_har(har)
            entries = har["log"]["entries"]
            t.check(not problems, f"{url}: HAR 1.2 valid", problems=problems[:5])
            t.check(len(entries) == n_cdp, f"{url}: HAR entries {len(entries)} == DevTools requests {n_cdp}")
            api = [e for e in entries if ".agenttrace.test/api/" in e["request"]["url"] and e["response"]["status"] == 200]
            t.check(all(e["response"]["content"].get("text") for e in api), f"{url}: site API responses carry bodies ({len(api)})")
            pend = [e for e in entries if (e.get("_agenttrace") or {}).get("pending")]
            t.check(all(e["response"]["content"].get("comment") for e in pend), f"{url}: {len(pend)} in-flight entries are flagged")
            totals.append(len(entries))
            t.note(f"{Path(path).name}: {len(entries)} entries")
    t.metric("entries_per_page", totals)


# ═══════════════════════════════════════════════════════════════════════
# Part 3 — before/after click
# ═══════════════════════════════════════════════════════════════════════
@selftest(3, "Click-triggered API call appears in the post-click HAR and not in the pre-click HAR")
def t_click_before_after(t: T) -> None:
    fx = t.fx
    scenarios = [("stats-button", fx.url("/lab/click", "lab"), "Load stats", "/lab/api/stats", "GET"),
                 ("add-to-cart", fx.url(fx.state.catalog.url(fx.state.catalog.by_id[9])), "Add to cart", "/api/v1/cart", "POST"),
                 ("pagination-next", fx.url("/catalogue/page-1.html"), "next", "/catalogue/page-2.html", "GET")]
    for name, url, target, api, method in scenarios:
        with t.session(name) as s:
            pre = s.goto(url)
            post = s.click(target)
            pre_path = s.save_har(t.out / f"{name}_pre_click.har", action_id=pre["id"])
            post_path = s.save_har(t.out / f"{name}_post_click.har", action_id=post["id"])
            pre_recs, post_recs = har_to_records(pre_path), har_to_records(post_path)
            t.check(validate_har_file(pre_path)[0] and validate_har_file(post_path)[0], f"{name}: both HARs valid")
            in_pre = [r for r in pre_recs if api in r.url and r.method == method]
            in_post = [r for r in post_recs if api in r.url and r.method == method]
            t.check(not in_pre, f"{name}: {method} {api} NOT in pre-click HAR ({len(pre_recs)} entries)")
            t.check(in_post and in_post[0].status and in_post[0].status < 400,
                    f"{name}: {method} {api} IS in post-click HAR ({len(post_recs)} entries)")
            prim = post["network"]["primary"] or {}
            t.check(api in prim.get("url", ""), f"{name}: click's primary request identified as {prim.get('url')}")


# ═══════════════════════════════════════════════════════════════════════
# Part 4 — reliability & verification
# ═══════════════════════════════════════════════════════════════════════
@selftest(4, "Delayed + slow API page: 10 consecutive captures are complete (0% premature/empty HAR)")
def t_reliability_slow_api(t: T) -> None:
    url = t.fx.url("/lab/slow?delay=800&ms=2000", "lab")
    with t.session("control") as s:
        r = s.goto(url, wait="none")
        recs = s.recorder.for_action(r["id"])
        done = [x for x in recs if "/lab/api/slow" in x.url and x.finished and x.response_body]
        t.check(not done, "control: naive capture right after load misses the slow API (hazard is real)",
                entries=len(recs))
    runs = t.reps(10, 3)
    good = 0
    waits = []
    with t.session("verified") as s:
        for i in range(1, runs + 1):
            res = s.capture(url, har_path=t.out / f"run_{i:02d}.har")
            recs = har_to_records(res["har_path"])
            slow = [x for x in recs if "/lab/api/slow" in x.url]
            ok = (res["ok"] and slow and slow[-1].status == 200 and slow[-1].response_body and
                  json.loads(slow[-1].response_body).get("rows") == 42 and validate_har_file(res["har_path"])[0])
            good += bool(ok)
            waits.append(res.get("waited_ms"))
            t.note(f"run {i}: ok={bool(ok)} entries={len(recs)} attempts={res['attempts']} waited={res.get('waited_ms')}ms")
    t.metric("complete", f"{good}/{runs}")
    t.metric("premature_rate", f"{(runs - good) / runs:.0%}")
    t.metric("waited_ms", f"{min(waits)}-{max(waits)}")
    t.check(good == runs, f"{good}/{runs} captures complete, 0% premature/empty")


# ═══════════════════════════════════════════════════════════════════════
# Part 5 — multi-page crawl
# ═══════════════════════════════════════════════════════════════════════
@selftest(5, "Crawl 8 pages → 8 distinctly named HARs, none skipped, visited/failed summary")
def t_crawl_eight_pages(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    urls = [fx.url(f"/catalogue/page-{n}.html") for n in (1, 2, 3, 4)] + \
           [fx.url(cat.url(cat.by_id[11])), fx.url(cat.url(cat.by_id[12])), fx.url("/", "news"), fx.url("/page/2/", "news")]
    with t.session("crawl") as s:
        res = crawl(s, urls, out_dir=t.out / "hars")
    names = [p["har_name"] for p in res["pages"]]
    t.check(res["total"] == 8 and res["visited"] == 8 and res["failed"] == 0 and res["skipped"] == 0,
            f"summary: total={res['total']} visited={res['visited']} failed={res['failed']} skipped={res['skipped']}")
    t.check(len(set(names)) == 8, "8 distinct HAR file names", names=names)
    for p in res["pages"]:
        t.check(p["har_path"] and Path(p["har_path"]).exists() and validate_har_file(p["har_path"])[0] and p["entries"] > 0,
                f"{p['har_name']}: exists, valid, {p.get('entries')} entries")
    t.check(Path(res["summary_path"]).exists(), "crawl_summary.json written")
    t.metric("pages", res["visited"])


@selftest(5, "Crawl with an unreachable page: it is attempted and reported as failed, nothing skipped")
def t_crawl_with_failure(t: T) -> None:
    fx = t.fx
    urls = [fx.url(f"/catalogue/page-{n}.html") for n in (5, 6, 7)] + ["http://127.0.0.1:9/unreachable"] + \
           [fx.url("/page/3/", "news"), fx.url("/about")]
    with t.session("crawl", retry={"max_attempts": 2, "backoff_ms": 100}) as s:
        res = crawl(s, urls, out_dir=t.out / "hars", max_attempts=1)
    bad = [p for p in res["pages"] if not p["ok"]]
    t.check(res["total"] == 6 and res["visited"] == 5 and res["failed"] == 1 and res["skipped"] == 0,
            f"summary: visited={res['visited']} failed={res['failed']} skipped={res['skipped']}")
    t.check(bad and "127.0.0.1:9" in bad[0]["url"] and bad[0].get("error"), f"failed page has a clear error: {bad[0].get('error', '')[:80]}")


# ═══════════════════════════════════════════════════════════════════════
# Part 6 — AI agent action interface
# ═══════════════════════════════════════════════════════════════════════
@selftest(6, "Plain instruction via tool calls only (no selectors): product page → add to cart → correct HAR")
def t_agent_tool_calls(t: T) -> None:
    fx = t.fx
    for fmt, key in (("anthropic", "input_schema"), ("mcp", "inputSchema")):
        cat = tool_catalog(fmt)
        t.check(len(cat) >= 20 and all(c[key]["type"] == "object" and c["description"] for c in cat),
                f"{fmt} tool catalog: {len(cat)} tools with JSON schemas")
    oa = tool_catalog("openai")
    t.check(all(c["type"] == "function" and c["function"]["parameters"]["type"] == "object" for c in oa), "openai tool format ok")
    with t.session("agent", strict=False) as s:
        dispatch_tool(s, "goto", {"url": fx.url("/catalogue/page-1.html")})
        obs = dispatch_tool(s, "observe", {"limit": 120})
        prod = next(e for e in obs["elements"] if e["role"] == "link" and "/product/" in e.get("href", ""))
        t.note(f"agent picked {prod['ref']} {prod.get('name')!r} from observe()")
        r1 = dispatch_tool(s, "click", {"target": prod["ref"]})
        t.check(r1["ok"] and "/product/" in r1["url"], f"navigated to product page {r1['url']}")
        r2 = dispatch_tool(s, "click", {"target": "add to cart"})
        prim = r2["network"]["primary"] or {}
        t.check(r2["ok"] and prim.get("method") == "POST" and "/api/v1/cart" in prim.get("url", "") and prim.get("status") == 201,
                f"'add to cart' → {prim.get('method')} {prim.get('url')} → {prim.get('status')}")
        reqs = dispatch_tool(s, "requests", {"pattern": "/api/v1/cart", "method": "POST"})
        t.check(len(reqs) == 1, "requests() lists exactly one cart POST")
        fin = dispatch_tool(s, "finish", {})
    har_file = next((a["har"] for a in s.actions() if a["id"] == r2["id"] and a.get("har")), None)
    recs = har_to_records(har_file) if har_file else []
    t.check(har_file and any(r.method == "POST" and "/api/v1/cart" in r.url for r in recs) and validate_har_file(har_file)[0],
            f"per-action HAR {Path(har_file).name if har_file else None} holds the cart POST")
    t.check(Path(fin["paths"]["report_md"]).exists(), "report.md written")


# ═══════════════════════════════════════════════════════════════════════
# Part 7 — MCP server
# ═══════════════════════════════════════════════════════════════════════
class _MCPClient:
    def __init__(self, cmd: list, env: dict) -> None:
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.lines: list = []
        self.stderr: list = []
        self._cv = threading.Condition()
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        self._id = 0

    def _read_out(self) -> None:
        for raw in self.proc.stdout:
            with self._cv:
                self.lines.append(raw.decode("utf-8", "replace").rstrip("\n"))
                self._cv.notify_all()

    def _read_err(self) -> None:
        for raw in self.proc.stderr:
            self.stderr.append(raw.decode("utf-8", "replace").rstrip())

    def send_raw(self, text: str) -> None:
        self.proc.stdin.write((text + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: Optional[dict] = None, timeout: float = 90) -> dict:
        self._id += 1
        mid = self._id
        self.send_raw(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}))
        return self.wait_for(mid, timeout)

    def wait_for(self, mid: Any, timeout: float = 90) -> dict:
        end = time.monotonic() + timeout
        with self._cv:
            while True:
                for line in self.lines:
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == mid:
                        return msg
                left = end - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"no MCP response for id {mid}; stderr tail: {self.stderr[-5:]}")
                self._cv.wait(left)

    def call(self, name: str, args: dict, timeout: float = 120) -> dict:
        resp = self.request("tools/call", {"name": name, "arguments": args}, timeout)
        res = resp.get("result") or {}
        payload = json.loads(res["content"][0]["text"]) if res.get("content") else None
        return {"raw": resp, "isError": res.get("isError"), "data": payload}

    def close(self) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return -9


@selftest(7, "MCP stdio server: handshake, tools/list, navigate+capture returns a HAR path, error handling")
def t_mcp_server(t: T) -> None:
    fx = t.fx
    prof = json.dumps({"host_map": fx.host_map(), "no_proxy_server": True})
    cmd = self_command() + ["mcp", "--out", str(t.out / "runs"), "--profile", prof, "--log-file", str(t.out / "mcp.log")]
    client = _MCPClient(cmd, child_env())
    t.add_cleanup(lambda: client.proc.poll() is None and client.proc.kill())
    init = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "selftest-client", "version": "1"}})
    res = init.get("result") or {}
    t.check(res.get("protocolVersion") == "2025-06-18" and res.get("serverInfo", {}).get("name") == "agenttrace"
            and "tools" in res.get("capabilities", {}), "initialize: protocol negotiated, serverInfo=agenttrace, tools capability")
    client.send_raw(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    old = client.request("initialize", {"protocolVersion": "2024-11-05"})
    t.check(old["result"]["protocolVersion"] == "2024-11-05", "older client protocol 2024-11-05 accepted")
    tools = client.request("tools/list")["result"]["tools"]
    t.check(len(tools) >= 20 and all(x["inputSchema"]["type"] == "object" for x in tools), f"tools/list: {len(tools)} tools")
    t.check(client.request("ping")["result"] == {}, "ping")
    cap = client.call("capture_page", {"url": fx.url("/lab/slow?delay=300&ms=600", "lab")})
    har = (cap["data"] or {}).get("har_path")
    t.check(cap["isError"] is False and har and Path(har).exists(), f"capture_page → HAR path {har}")
    recs = har_to_records(har)
    t.check(validate_har_file(har)[0] and any("/lab/api/slow" in r.url and r.status == 200 for r in recs),
            f"returned HAR is valid and contains the delayed API ({len(recs)} entries)")
    g = client.call("goto", {"url": fx.url(fx.state.catalog.url(fx.state.catalog.by_id[3]))})
    t.check(g["isError"] is False and g["data"]["status"] == 200, "goto product page via MCP")
    obs = client.call("observe", {"limit": 40})
    t.check(any(e.get("name") == "Add to cart" for e in obs["data"]["elements"]), "observe lists the 'Add to cart' button")
    c = client.call("click", {"target": "Add to cart"})
    t.check(c["isError"] is False and "/api/v1/cart" in (c["data"]["network"]["primary"] or {}).get("url", ""),
            "click('Add to cart') → cart API captured")
    bad = client.call("click", {"target": "the purple unicorn button"})
    t.check(bad["isError"] is True and "no element" in json.dumps(bad["data"]).lower(), "failed tool call reported as isError with hint")
    unknown_method = client.request("does/not/exist")
    t.check(unknown_method.get("error", {}).get("code") == -32601, "unknown method → -32601")
    unknown_tool = client.request("tools/call", {"name": "fly_to_moon", "arguments": {}})
    t.check(unknown_tool.get("error", {}).get("code") == -32602, "unknown tool → -32602")
    client.send_raw("{this is not json")
    time.sleep(0.5)
    t.check(any('"code": -32700' in line for line in client.lines), "malformed JSON → -32700")
    t.check(client.request("ping")["result"] == {}, "server still alive after errors")
    fin = client.call("finish", {})
    t.check(fin["isError"] is False and Path(fin["data"]["paths"]["har"]).exists(), "finish → session.har written")
    code = client.close()
    t.check(code == 0, f"server exits cleanly on EOF (code {code})")
    bad_lines = [ln for ln in client.lines if ln.strip() and not _is_jsonrpc(ln)]
    t.check(not bad_lines, f"stdout carries only JSON-RPC ({len(client.lines)} lines)", bad=bad_lines[:3])
    t.check(any("MCP server ready" in ln for ln in client.stderr), "logs go to stderr")
    t.metric("tools", len(tools))


def _is_jsonrpc(line: str) -> bool:
    try:
        return json.loads(line).get("jsonrpc") == "2.0"
    except (json.JSONDecodeError, AttributeError):
        return False


# ═══════════════════════════════════════════════════════════════════════
# Part 8 — auth / session reuse across restarts
# ═══════════════════════════════════════════════════════════════════════
@selftest(8, "Log in once, save state, restart (new process) → logged in without logging in again")
def t_auth_restart(t: T) -> None:
    fx = t.fx
    state = t.out / "auth" / "shop-state.json"
    with t.session("login") as s:
        s.goto(fx.url("/login"))
        s.fill("Username", "demo")
        s.fill("Password", "demo123")
        r = s.click("Sign in")
        t.check("/account" in r["url"] and "Welcome, Demo Customer" in s.text(), "logged in through the real form (CSRF)")
        info = s.save_state(state)
        t.check("sessionid" in info["cookie_names"], f"state saved: cookies {info['cookie_names']}")
    logins_before = sum(1 for e in fx.state.events if e["kind"] == "login")
    code = f"""
prof = at.BrowserProfile(host_map={fx.host_map()!r}, no_proxy_server=True)
state = sys.argv[1] if len(sys.argv) > 1 else None
s = at.Session(profile=prof, out_dir={str(t.out / 'child')!r} + ('-with' if STATE else '-without'), storage_state=STATE, hitl='off')
r = s.goto({fx.url('/account')!r})
print(json.dumps({{'url': r['url'], 'title': r['title'], 'text': s.text()[:300]}}))
s.finish()
"""
    without = run_child(code.replace("STATE", "None"))
    t.check(without.get("_returncode") == 0 and "/login" in without.get("url", ""),
            f"new process WITHOUT state → redirected to login ({without.get('url')})", stderr=without.get("_stderr"))
    with_state = run_child(code.replace("STATE", repr(str(state))))
    t.check(with_state.get("_returncode") == 0 and "/account" in with_state.get("url", "") and
            "Welcome, Demo Customer" in with_state.get("text", ""),
            f"new process WITH state → {with_state.get('url')} 'Welcome, Demo Customer'", stderr=with_state.get("_stderr"))
    logins_after = sum(1 for e in fx.state.events if e["kind"] == "login")
    t.check(logins_after == logins_before == 1, f"server saw exactly one login ({logins_after})")


# ═══════════════════════════════════════════════════════════════════════
# Part 9 — stealth
# ═══════════════════════════════════════════════════════════════════════
@selftest(9, "Bot-detection page: plain headless is blocked, stealth profile passes with 0 automation tells")
def t_stealth_botcheck(t: T) -> None:
    url = t.fx.url("/lab/botcheck", "lab")
    with t.session("plain") as s:
        r = s.goto(url)
        probe = s.evaluate(DETECTION_PROBES_JS)
        tells = analyse_probe(probe)
        t.check(r.get("status") == 403 or "Bot detected" in s.text(), f"plain headless detected (HTTP {r.get('status')})")
        t.note(f"plain headless tells: {tells}")
    with t.session("stealth", profile_overrides={"stealth": True}) as s:
        r = s.goto(url)
        s.wait_for({"text": "Welcome, human"}, timeout_ms=8000)
        probe = s.evaluate(DETECTION_PROBES_JS)
        tells2 = analyse_probe(probe)
        verdict = [x for x in s.records("/lab/api/botcheck") if x.finished]
        t.check(r.get("status") == 200 and "Welcome, human" in s.text(), "stealth profile passes the bot check")
        t.check(verdict and (verdict[-1].json() or {}).get("verdict") == "human", "verdict API captured in HAR: human")
        t.note(f"stealth tells: {tells2}")
        t.check(len(tells2) == 0, f"stealth probe: {len(tells2)} automation tells (plain had {len(tells)})", tells=tells2)
    t.metric("tells_plain", len(tells))
    t.metric("tells_stealth", len(tells2))


@selftest(9, "Live: stealth capture on real bot-detection sites without block/CAPTCHA (≥2 of 3)", live=True)
def t_stealth_live(t: T) -> None:
    sites = ["https://bot.sannysoft.com/", "https://www.scrapingcourse.com/antibot-challenge",
             "https://arh.antoinevastel.com/bots/areyouheadless"]
    passed = 0
    with t.live_session("stealth-live", stealth=True) as s:
        for url in sites:
            try:
                r = s.goto(url)
                s.settle()
                block = s.detect_block()
                recs = s.recorder.for_action(r["id"])
                ok = r["ok"] and not block and len(recs) > 0 and (r.get("status") or 0) < 400
                t.note(f"{url}: status={r.get('status')} blocked={bool(block)} requests={len(recs)} title={r['title']!r}")
                passed += bool(ok)
            except AgentTraceError as exc:
                t.note(f"{url}: error {exc}")
    t.metric("passed", f"{passed}/{len(sites)}")
    t.check(passed >= 2, f"{passed}/3 bot-protected sites captured without block")


# ═══════════════════════════════════════════════════════════════════════
# Part 10 — output management (dedup, tagging, report)
# ═══════════════════════════════════════════════════════════════════════
@selftest(10, "Analytics-heavy site: clean HAR keeps only relevant calls (deduped, tagged) + counts summary")
def t_output_clean_har(t: T) -> None:
    fx = t.fx
    with t.session("shop") as s:
        s.goto(fx.url("/"))
        s.goto(fx.url(fx.state.catalog.url(fx.state.catalog.by_id[17])))
        s.click("Add to cart")
        s.goto(fx.url("/cart"))
        summary = s.finish()
    full = har_to_records(summary["paths"]["har"])
    clean = har_to_records(summary["paths"]["clean_har"])
    third = [r for r in full if not r.host.endswith("agenttrace.test")]
    first_api = {(r.method, r.url, r.request_body or b"") for r in full
                 if r.host == "shop.agenttrace.test" and (r.path.startswith("/api/") or r.path == "/graphql") and r.finished}
    kept_api = {(r.method, r.url, r.request_body or b"") for r in clean}
    t.check(all(r.category in ("api", "document", "other") for r in clean), "clean HAR has no analytics/ads/tracking/static entries")
    t.check(not [r for r in clean if not r.host.endswith("agenttrace.test")], "no third-party tracker survives in clean HAR")
    t.check(first_api <= kept_api, f"all {len(first_api)} distinct first-party API calls retained",
            missing=[f"{m} {u}" for m, u, _ in first_api - kept_api])
    dups = sum(1 for r in clean if r.extra.get("duplicates"))
    t.check(all(r.category for r in full), "every entry is tagged with a category")
    rep = read_json(summary["paths"]["report_json"])
    s2 = rep["summary"]
    t.check(s2["requests"] == len(full) and s2["api_calls"] > 0 and len(s2["pages_visited"]) >= 3 and "failed_requests" in s2,
            f"report: pages={len(s2['pages_visited'])} requests={s2['requests']} api={s2['api_calls']} failed={s2['failed_requests']}")
    bycat = rep["noise"]["by_category"]
    t.check(bycat.get("analytics", {}).get("dropped", 0) > 0 and bycat.get("tracking", {}).get("dropped", 0) > 0,
            "summary shows analytics/tracking dropped", by_category=bycat)
    t.metric("full_entries", len(full))
    t.metric("clean_entries", len(clean))
    t.metric("third_party_noise", len(third))
    t.metric("dedup_groups", dups)


# ═══════════════════════════════════════════════════════════════════════
# Part 11 — config & extensibility
# ═══════════════════════════════════════════════════════════════════════
@selftest(11, "Config-only onboarding: a brand-new site (WordPress-like) works from a config file, no code changes")
def t_config_new_site(t: T) -> None:
    fx = t.fx
    cfg = {
        "name": "daily-fixture-news",
        "base_url": fx.url("/", "news"),
        "profile": {"host_map": fx.host_map(), "no_proxy_server": True},
        "hitl": "off",
        "auth": {"state_file": "news-auth.json", "check": {"url": "/members/", "text": "Members area"},
                 "login": [{"goto": "/wp-login.php"},
                           {"fill": {"target": "Username or Email Address", "text": "${env:NEWS_USER}"}},
                           {"fill": {"target": "Password", "text": "${secret:NEWS_PASS}"}},
                           {"click": "Log In"}]},
        "steps": [{"goto": "/"},
                  {"paginate": {"mode": "next", "max_pages": 5, "item": "article.post",
                                "fields": {"title": "h2.entry-title a", "url": "h2.entry-title a@href",
                                           "date": "time@datetime", "author": ".author"}}, "save_as": "articles"},
                  {"goto": "/members/", "expect": {"url": "/members/", "status": 200}},
                  {"extract": {"fields": {"headline": "h1.entry-title", "premium": ".members-only"}}, "save_as": "members"}],
        "pages": [fx.url("/wp-json/wp/v2/posts?page=1", "news"), fx.url("/page/5/", "news")],
        "outputs": {"export": True},
    }
    path = t.out / "news.json"
    write_json(path, cfg)
    os.environ["NEWS_USER"] = "editor"
    os.environ["AGENTTRACE_SECRET_NEWS_PASS"] = "press-pass"
    res = run_config(path, out_dir=t.out / "run")
    t.check(res["ok"], f"config run ok (status={res['run'].get('status')})", steps=[(s['id'], s['status'], s.get('error')) for s in res['run']['steps']])
    t.check(res["auth"]["method"] == "login" and (path.parent / "news-auth.json").exists(),
            "logged in via config steps; state saved next to the config (relative paths resolve against the config file)")
    data = read_json(res["data_path"])
    arts = data.get("articles") or []
    t.check(len(arts) == 50 and all(a.get("title") and a.get("url", "").startswith("http") for a in arts),
            f"paginated extraction: {len(arts)} articles with title+url")
    t.check((data.get("members") or {}).get("premium") == "Premium analysis archive", "members-only content extracted")
    t.check(res["crawl"]["visited"] == 2, "extra pages captured")
    t.check(Path(res["exports"]["postman"]).exists(), "exports produced from config outputs")
    res2 = run_config(path, out_dir=t.out / "run2")
    t.check(res2["ok"] and res2["auth"]["method"] == "saved_state", "second run reuses the saved login (no re-login)")
    toml_text = _to_toml_minimal(cfg)
    if toml_text is not None:
        (t.out / "news.toml").write_text(toml_text, encoding="utf-8")
        loaded = load_config(t.out / "news.toml")
        t.check(loaded["steps"][1]["paginate"]["item"] == "article.post", "same config also loads from TOML")
    t.metric("articles", len(arts))


def _to_toml_minimal(cfg: dict) -> Optional[str]:
    try:
        import tomllib  # noqa: F401
    except ImportError:
        return None
    def val(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return json.dumps(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{" + ", ".join(f"{json.dumps(k)} = {val(x)}" for k, x in v.items()) + "}"
        return json.dumps(str(v))
    return "\n".join(f"{k} = {val(v)}" for k, v in cfg.items()) + "\n"


# ═══════════════════════════════════════════════════════════════════════
# Part 12 — security & redaction
# ═══════════════════════════════════════════════════════════════════════
# documentation-style fake keys, assembled at runtime so secret scanners do not flag this file
_FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
_FAKE_STRIPE_KEY = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"


@selftest(12, "Auth-protected capture → redacted HAR contains no raw password/token/cookie values")
def t_redaction(t: T) -> None:
    fx = t.fx
    with t.session("auth") as s:
        s.goto(fx.url("/login"))
        s.fill("Username", "demo")
        s.fill("Password", "demo123")
        s.click("Sign in")
        tok = s.evaluate("""async ([aws, stripe]) => { const r = await fetch('/api/v1/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({username: 'demo', password: 'demo123'})}); const d = await r.json();
                  await fetch('/api/v1/me?api_key=' + aws + '&page=1', {headers: {'Authorization': 'Bearer ' + d.access_token, 'X-API-Key': stripe}});
                  return d; }""", [_FAKE_AWS_KEY, _FAKE_STRIPE_KEY])
        s.settle()
        cookies = {c["name"]: c["value"] for c in s.context.cookies()}
        har = s.har(redact=False)
    raw_text = json.dumps(har)
    secrets_ = {"password": "demo123", "sessionid": cookies["sessionid"], "csrftoken": cookies["csrftoken"],
                "jwt": tok["access_token"], "refresh": tok["refresh_token"], "aws": _FAKE_AWS_KEY,
                "stripe": _FAKE_STRIPE_KEY}
    t.check(all(v in raw_text for v in secrets_.values()), "raw HAR really contains all secrets (test is meaningful)")
    redactor = Redactor()
    red = redactor.redact_har(har)
    out = t.out / "shared.har"
    write_json(out, red)
    text = out.read_text(encoding="utf-8")
    leaked = {k: v for k, v in secrets_.items() if v in text}
    t.check(not leaked, f"no raw secret in the shareable HAR ({len(secrets_)} checked)", leaked=list(leaked))
    t.check(text.count("[REDACTED:") >= 10, f"masked placeholders present ({text.count('[REDACTED:')})")
    t.check(not redactor.find_leaks(text), "redactor self-scan finds no leaks")
    t.check(not validate_har(red), "redacted HAR is still valid HAR 1.2")
    me = [e for e in red["log"]["entries"] if "/api/v1/me" in e["request"]["url"]]
    t.check(me and "page=1" in me[0]["request"]["url"] and "api_key=%5BREDACTED" in me[0]["request"]["url"].replace("[", "%5B"),
            "non-secret query params kept, secret ones masked")
    t.metric("secrets_masked", len(redactor.secrets))


# ═══════════════════════════════════════════════════════════════════════
# Part 13 — action ↔ network correlation
# ═══════════════════════════════════════════════════════════════════════
@selftest(13, "Background polling + trackers + lazy images: click→target request identified in ≥95% of trials")
def t_correlation_accuracy(t: T) -> None:
    import random as _random
    rng = _random.Random(13)
    trials = t.reps(40, 12)
    kinds = [("Load reviews", r"/lab/api/reviews\?item=\d+$", "GET"), ("Save item", r"/lab/api/wishlist$", "POST"),
             ("Show details", r"/lab/api/details/\d+$", "GET"), ("filter", r"/lab/api/filter\?c=", "GET")]
    correct = 0
    misses = []
    with t.session("corr", settle_quiet_ms=450) as s:
        s.goto(t.fx.url("/lab/correlation?seed=7", "lab"))
        options = ["new", "sale", "all"]
        for i in range(trials):
            target, rx, method = kinds[rng.randrange(len(kinds))]
            if target == "filter":
                res = s.select("Filter", options[i % 3])
            else:
                res = s.click(target)
            prim = res["network"]["primary"] or {}
            hit = bool(prim) and re.search(rx, prim.get("url", "")) is not None and prim.get("method") == method
            correct += hit
            if not hit:
                misses.append({"trial": i + 1, "action": target, "got": f"{prim.get('method')} {prim.get('url')}",
                               "ranking": [a for a in s._actions[-1]["_ranking"][:4]]})
            s._sleep(rng.uniform(0, 0.6))
        bg = [r for r in s.recorder.records if "/lab/api/poll" in r.url]
    acc = correct / trials
    t.metric("accuracy", f"{acc:.1%} ({correct}/{trials})")
    t.metric("background_polls", len(bg))
    for m in misses[:5]:
        t.note(f"miss: {m}")
    t.check(len(bg) > trials, f"background polling really ran during the test ({len(bg)} polls)")
    t.check(acc >= 0.95, f"target request identified in {acc:.1%} of trials (criterion ≥95%)", misses=misses[:5])


# ═══════════════════════════════════════════════════════════════════════
# Part 14 — advanced HAR validation
# ═══════════════════════════════════════════════════════════════════════
@selftest(14, "Expected request/payload/response/timings validate; each missing field yields a clear reason")
def t_advanced_validation(t: T) -> None:
    import copy as _copy
    fx = t.fx
    p = fx.state.catalog.by_id[21]
    with t.session("wf") as s:
        s.goto(fx.url(fx.state.catalog.url(p)))
        s.click("Add to cart")
        path = s.save_har(t.out / "workflow.har")
    har = load_har(path)
    exp = {"url": "/api/v1/cart", "method": "POST", "status": 201, "request_json": {"product_id": p["id"], "qty": 1},
           "request_has": ["product_id", "qty"], "response_json": {"ok": True}, "response_has": ["items", "total", "count"],
           "request_headers": {"content-type": "application/json"}, "require_timings": True}
    ok = validate_expectations(har_to_records(har), [exp])
    t.check(ok["ok"], "known workflow passes: URL, method, payload, response and timings present", report=ok)

    def entry(h: dict) -> dict:
        return next(e for e in h["log"]["entries"] if "/api/v1/cart" in e["request"]["url"] and e["request"]["method"] == "POST")

    mutations = {
        "method": (lambda h: entry(h)["request"].update(method="GET"), "method: expected POST, got GET"),
        "payload_missing": (lambda h: entry(h)["request"].pop("postData"), "request payload: missing"),
        "payload_field": (lambda h: entry(h)["request"]["postData"].update(text=json.dumps({"product_id": p["id"]})),
                          "request payload $.qty: missing"),
        "status": (lambda h: entry(h)["response"].update(status=500), "status: expected 201, got 500"),
        "response_body": (lambda h: entry(h)["response"]["content"].pop("text"), "response body: missing"),
        "response_field": (lambda h: entry(h)["response"]["content"].update(text=json.dumps({"ok": True, "items": []})),
                           "response: required field 'total' missing"),
        "timings": (lambda h: (entry(h).update(time=0), entry(h)["timings"].update(wait=0, receive=0)), "timing:"),
        "request_absent": (lambda h: h["log"].update(entries=[e for e in h["log"]["entries"] if "/api/v1/cart" not in e["request"]["url"]
                                                             or e["request"]["method"] != "POST"]), "method: expected POST, got GET"),
    }
    reasons = {}
    for name, (mutate, expected_reason) in mutations.items():
        h = _copy.deepcopy(har)
        mutate(h)
        res = validate_expectations(har_to_records(h), [exp])
        text = " | ".join(res["checks"][0]["failures"])
        reasons[name] = text
        t.check(not res["ok"] and expected_reason in text, f"{name}: fails with clear reason → {text[:110]}", got=text)
    t.metric("mutations_detected", f"{len(reasons)}/{len(mutations)}")
    write_json(t.out / "failure_reasons.json", reasons)


# ═══════════════════════════════════════════════════════════════════════
# Part 15 — browser & page state inspection
# ═══════════════════════════════════════════════════════════════════════
@selftest(15, "Structured state snapshot (tabs, frames, forms, cookies, storage, elements) identifies target + step")
def t_state_inspection(t: T) -> None:
    fx = t.fx
    with t.session("wizard") as s:
        s.goto(fx.url("/lab/wizard", "lab"))
        st = s.inspect()
        t.check(st["url"].endswith("/lab/wizard") and st["title"] == "Account setup wizard", "url + title")
        t.check(len(st["tabs"]) == 1 and st["tabs"][0]["active"], "one active tab reported")
        t.check(len(st["frames"]) >= 2 and any(f["parent"] for f in st["frames"]), f"frames tree ({len(st['frames'])} frames)")
        t.check(any(tb["text"].startswith("1.") and tb["selected"] for tb in st["tab_widgets"])
                and "Step 1 of 3" in st["text_excerpt"], "navigation state: tab '1. Account' selected, step 1 of 3")
        form = next(f for f in st["forms"] if f["id"] == "account-form")
        t.check({x["name"] for x in form["fields"]} >= {"email", "password"} and
                any(x["label"] == "Email" for x in form["fields"]), "form fields with labels")
        t.check(any(c["name"] == "wizard_seen" and "value_length" in c for c in st["cookies"]), "cookies listed (values hidden)")
        t.check("wizard_draft" in st["local_storage"] and "wizard_tab" in st["session_storage"], "local/session storage keys")
        nxt = next(e for e in st["elements"] if e.get("name") == "Next")
        t.check(nxt["ref"].startswith("e"), f"target element 'Next' identified as {nxt['ref']}")
        s.fill("Email", "ai@agenttrace.test")
        s.fill("Password", "s3cret-pass")
        s.click(nxt["ref"])
        st2 = s.inspect(storage_values=True)
        t.check("Step 2 of 3" in st2["text_excerpt"] and any(tb["text"].startswith("2.") and tb["selected"] for tb in st2["tab_widgets"]),
                "after action: snapshot shows tab 2 selected, step 2 of 3")
        t.check(json.loads(st2["local_storage"]["wizard_draft"])["step"] == 2, "storage reflects new state")
        name_field = next(e for e in st2["elements"] if e.get("name") == "Display name")
        s.fill(name_field["ref"], "Agent")
        s.click("Next")
        s.click("Create account")
        t.check("Account created" in s.text(), "workflow completed using refs from the snapshots")
    t.metric("elements_step1", st["element_count"])


# ═══════════════════════════════════════════════════════════════════════
# Part 16 — intelligent wait & synchronisation
# ═══════════════════════════════════════════════════════════════════════
@selftest(16, "Same workflow ×20 with fast/medium/slow backends: no premature action, no race (stale results)")
def t_intelligent_waits(t: T) -> None:
    import random as _random
    rng = _random.Random(16)
    runs = t.reps(20, 6)
    words = ["lamp", "chair", "vase", "mug", "rug", "clock", "bowl", "mirror", "stool", "towel"]
    expected = []
    timings = {"fast": [], "medium": [], "slow": []}
    with t.session("waits") as s:
        for i in range(runs):
            profile = ["fast", "medium", "slow"][i % 3]
            term = rng.choice(words) + str(i)
            t0 = time.monotonic()
            s.goto(t.fx.url(f"/lab/waits?profile={profile}", "lab"))
            s.fill("Search term", term)
            s.click("Search")
            s.click("first result link")
            s.click("Add to list")
            timings[profile].append(round(time.monotonic() - t0, 2))
            expected.append(f"q-{term}-1")
    got = list(t.fx.state.waits_adds)
    wrong = [(e, g) for e, g in zip(expected, got) if e != g]
    t.metric("runs", runs)
    t.metric("avg_s", {k: round(sum(v) / len(v), 2) for k, v in timings.items() if v})
    t.check(len(got) == runs, f"{len(got)}/{runs} workflows completed")
    t.check(not wrong and "stale-1" not in got, f"0 premature/stale actions across {runs} runs", wrong=wrong[:5])


# ═══════════════════════════════════════════════════════════════════════
# Part 17 — retry, recovery & failure handling
# ═══════════════════════════════════════════════════════════════════════
@selftest(17, "Injected 503/reset/timeout/detached/overlay/alert/500 failures ×20 runs → ≥95% recover automatically")
def t_retry_recovery(t: T) -> None:
    runs = t.reps(20, 5)
    recovered = 0
    kinds_seen: dict = {}
    with t.session("flaky", profile_overrides={"navigation_timeout_ms": 3000, "action_timeout_ms": 4000},
                   retry={"max_attempts": 4, "backoff_ms": 150}) as s:
        for i in range(runs):
            run = f"r{i + 1}"
            try:
                for step in range(1, 8):
                    s.goto(t.fx.url(f"/lab/flaky/step/{step}?run={run}", "lab"))
                    expect = {"url": "/lab/api/flaky/save", "status": "2xx"} if step == 7 else None
                    s.click(f"Confirm step {step}", expect=expect)
                done = t.fx.state.flaky_done.get(run, set())
                ok = done == set(range(1, 8))
            except AgentTraceError as exc:
                ok = False
                t.note(f"run {run} failed: {str(exc)[:150]}")
            recovered += ok
        in_attempt: dict = {}
        for a in s.actions():
            for rtry in a.get("retries", []):
                kinds_seen[rtry.get("error_kind")] = kinds_seen.get(rtry.get("error_kind"), 0) + 1
            for note in a.get("recovered_in_attempt", []):
                key = "overlay" if ("covered" in note or "intercepted" in note) else ("detached" if "re-rendered" in note else "other")
                in_attempt[key] = in_attempt.get(key, 0) + 1
        dialogs = sum(len(a.get("dialogs", [])) for a in s.actions())
    rate = recovered / runs
    t.metric("recovered", f"{recovered}/{runs} ({rate:.0%})")
    t.metric("retries_by_kind", kinds_seen)
    t.metric("in_attempt_recoveries", in_attempt)
    t.check({"http_5xx", "network", "timeout", "expectation"} <= set(kinds_seen),
            f"503 / connection reset / navigation timeout / failed API (500) were retried: {sorted(kinds_seen)}")
    t.check(in_attempt.get("overlay", 0) >= runs and in_attempt.get("detached", 0) >= runs,
            f"overlay + detached element recovered inside the action: {in_attempt}")
    t.check(dialogs >= runs, f"unexpected alert() dialogs auto-handled ({dialogs})")
    t.check(rate >= 0.95, f"{rate:.0%} of runs recovered without manual intervention (criterion ≥95%)")


# ═══════════════════════════════════════════════════════════════════════
# Part 18 — downloads
# ═══════════════════════════════════════════════════════════════════════
@selftest(18, "CSV / JSON / PDF (+blob, +POST, +auth exports) downloads saved with filename/MIME/size/network metadata")
def t_downloads(t: T) -> None:
    fx = t.fx
    with t.session("dl") as s:
        s.goto(fx.url("/lab/downloads", "lab"))
        got = {}
        for target in ("Download CSV report", "Download JSON data", "Download PDF invoice", "Export generated TXT",
                       "Export via form (POST)"):
            r = s.download(target)
            got[target] = (r.get("downloads") or [{}])[0]
        s.goto(fx.url("/login"))
        s.fill("Username", "alice")
        s.fill("Password", "wonderland")
        s.click("Sign in")
        acct = {t_: (s.download(t_).get("downloads") or [{}])[0] for t_ in
                ("Download orders (CSV)", "Download orders (JSON)", "Download latest invoice (PDF)")}
        summary = s.finish()
    csv_d, json_d, pdf_d = got["Download CSV report"], got["Download JSON data"], got["Download PDF invoice"]
    t.check(csv_d.get("filename") == "report.csv" and csv_d.get("mime_type") == "text/csv" and
            Path(csv_d["path"]).read_text(encoding="utf-8").count("\n") == 51, "CSV saved: report.csv, text/csv, 50 rows")
    t.check(json_d.get("mime_type") == "application/json" and len(json.loads(Path(json_d["path"]).read_text(encoding="utf-8"))["rows"]) == 20,
            "JSON saved and parses (20 rows)")
    pdf = Path(pdf_d["path"]).read_bytes()
    t.check(pdf_d.get("mime_type") == "application/pdf" and pdf.startswith(b"%PDF") and b"Invoice #4411" in pdf,
            "PDF saved (valid header, content present)")
    for d in (csv_d, json_d, pdf_d):
        t.check(d.get("request_id") and d["request"]["status"] == 200 and "attachment" in d["request"]["content_disposition"]
                and d.get("content_length_matches") and d["size"] > 0 and len(d["sha256"]) == 64,
                f"{d['filename']}: network request {d.get('request_id')} + size/sha256 metadata")
    blob = got["Export generated TXT"]
    t.check(blob.get("filename") == "generated.txt" and blob.get("network", "").startswith("none"),
            "in-page generated (blob:) download saved and flagged as having no network request")
    post = got["Export via form (POST)"]
    t.check(post.get("request", {}).get("method") == "POST" and post.get("filename") == "export-post.csv", "POST form download")
    t.check(all(d.get("path") and Path(d["path"]).exists() for d in acct.values()), "authenticated account exports saved")
    listing = read_json(summary["paths"]["downloads"])
    t.check(len(listing) == 8, f"downloads.json report lists all {len(listing)} files")
    t.metric("downloads", len(listing))


# ═══════════════════════════════════════════════════════════════════════
# Part 19 — console, errors & runtime monitoring
# ═══════════════════════════════════════════════════════════════════════
@selftest(19, "Console errors + uncaught exceptions + failed resources captured with timestamp & action context")
def t_console_monitoring(t: T) -> None:
    _parse_iso = parse_iso
    with t.session("console") as s:
        a1 = s.goto(t.fx.url("/lab/console", "lab"))
        a2 = s.click("Run broken widget")
        summary = s.finish()
    data = read_json(summary["paths"]["console"])["console"]
    by = lambda kind, text: [c for c in data if c["kind"] == kind and text in c["text"]]  # noqa: E731
    err = by("console", "Failed to init widget")
    unc = by("pageerror", "Uncaught lab failure")
    rej = by("pageerror", "unhandled rejection in lab")
    res = by("resource_error", "/lab/missing-image.png")
    clk = by("pageerror", "explode") or by("pageerror", "null")
    clk_log = by("console", "clicked broken button")
    t.check(err and err[0].get("level") == "error" and err[0]["action_id"] == a1["id"], "console.error captured in goto context")
    t.check(unc and unc[0]["action_id"] == a1["id"] and "stack" in unc[0], "uncaught exception with stack, goto context")
    t.check(rej, "unhandled promise rejection captured")
    t.check(res and res[0].get("request_id"), "failed resource (404) captured and linked to its request")
    t.check(clk and clk[0]["action_id"] == a2["id"] and clk_log and clk_log[0]["action_id"] == a2["id"],
            "click-triggered TypeError + console.error attributed to the click action")
    start, end = _parse_iso(a2["started"]), _parse_iso(a2["ended"])
    ts = _parse_iso(clk[0]["ts"])
    t.check(start <= ts <= end, f"error timestamp {clk[0]['ts'][11:23]} lies inside the click window")
    t.check(all(c.get("page_id") == "p1" and c.get("ts") for c in data if c["kind"] != "request_failed"),
            "every entry has page context + ISO timestamp")
    md = Path(summary["paths"]["report_md"]).read_text(encoding="utf-8")
    t.check("Uncaught lab failure" in md and "## Console errors" in md, "execution report lists the errors")
    t.metric("console_entries", len(data))


# ═══════════════════════════════════════════════════════════════════════
# Part 20 — workflow recording of manual interaction
# ═══════════════════════════════════════════════════════════════════════
def _human_click(page, locator, jitter: float = 3.0) -> None:
    """Move the real mouse to the element and click (what a person does)."""
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    x = box["x"] + box["width"] / 2 + (hash(str(box)) % 7 - 3) * jitter / 3
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y, steps=6)
    page.wait_for_timeout(60)
    page.mouse.click(x, y)


def _record_shop_flow(t: T, s: Session) -> dict:
    fx = t.fx
    rec = WorkflowRecorder(s, name="shop-cart-flow").start(fx.url("/"))
    page = s.page
    page.wait_for_selector("#featured .product-card")
    _human_click(page, page.get_by_role("link", name="Login"))
    page.wait_for_url("**/login*")
    _human_click(page, page.locator("#username"))
    page.keyboard.type("demo", delay=35)
    _human_click(page, page.locator("#password"))
    page.keyboard.type("demo123", delay=35)
    _human_click(page, page.get_by_role("button", name="Sign in"))
    page.wait_for_url("**/account")
    _human_click(page, page.locator("#q"))
    page.keyboard.type("lamp", delay=45)
    page.wait_for_timeout(400)
    page.keyboard.press("Enter")
    page.wait_for_url("**/search?q=lamp")
    page.wait_for_selector("a.result-link")
    _human_click(page, page.locator("a.result-link").first)
    page.wait_for_url("**/product/**")
    page.wait_for_selector("#reviews li.review")
    _human_click(page, page.locator("label[for=qty]"))
    page.keyboard.press("ArrowDown")
    _human_click(page, page.locator("#gift"))
    _human_click(page, page.locator("#add-to-cart"))
    page.wait_for_selector("#cart-status:has-text('In cart')")
    _human_click(page, page.locator("#cart-link"))
    page.wait_for_url("**/cart")
    page.wait_for_selector("tr.cart-row")
    s.recorder.pump(500, page)
    return rec.stop()


def _step_key(step: dict) -> tuple:
    action = next(k for k in step if k not in ("id", "expect", "comment", "wait", "optional"))
    val = step[action]
    tgt = val.get("target", val) if isinstance(val, dict) else val
    text = (tgt.get("text") if isinstance(tgt, dict) else str(tgt)) or ""
    return action, re.sub(r"\s*\(\d+\)", "", text).strip().lower()


@selftest(20, "Manual 10-step session (real mouse/keyboard) → ≥90% of actions recorded in order with targets; replayable")
def t_workflow_recording(t: T) -> None:
    fx = t.fx
    lamp = fx.state.catalog.search("lamp")[0]
    with t.session("record") as s:
        result = _record_shop_flow(t, s)
    wf = result["workflow"]
    write_json(t.out / "recorded_workflow.json", wf)
    expected = [("click", "login"), ("fill", "username"), ("fill", "password"), ("click", "sign in"),
                ("fill", "search products"), ("click", lamp["name"].lower()), ("select", "quantity"),
                ("check", "gift wrap"), ("click", "add to cart"), ("click", "cart")]
    got = [_step_key(st) for st in wf["steps"] if "goto" not in st and "press" not in st]
    # longest common subsequence = meaningful actions recorded in the right order
    m, n = len(expected), len(got)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            dp[i + 1][j + 1] = dp[i][j] + 1 if expected[i] == got[j] else max(dp[i][j + 1], dp[i + 1][j])
    matched = dp[m][n]
    t.note(f"recorded steps: {got}")
    t.metric("recorded_in_order", f"{matched}/{m}")
    t.check(matched / m >= 0.9, f"{matched}/{m} meaningful actions recorded in correct order with target info")
    t.check(wf["steps"][0].get("goto") == fx.url("/"), "start URL recorded as goto")
    t.check(result["secrets"] and all("demo123" not in json.dumps(st) for st in wf["steps"]),
            "password recorded as ${secret:…} placeholder, value kept in a separate secrets file")
    with_fp = [st for st in wf["steps"] if any(isinstance(v, dict) and isinstance(v.get("target", v), dict) and
                                               (v.get("target", v) or {}).get("fingerprint") for k, v in st.items() if k != "expect")]
    t.check(len(with_fp) >= 9, f"{len(with_fp)} steps carry selector + fingerprint for self-healing replay")
    add = next(st for st in wf["steps"] if _step_key(st) == ("click", "add to cart"))
    t.check((add.get("expect") or {}).get("url") == "/api/v1/cart" and add["expect"]["method"] == "POST",
            "network expectation recorded for 'Add to cart' (POST /api/v1/cart)")
    fx.reset()
    rep = replay(wf, times=1, secrets=result["secrets"], out_dir=t.out / "replay", profile=fx.profile(), hitl="off")
    t.check(rep["passed"] == 1, "recorded workflow replays successfully", failed=rep["runs"][0]["failed_steps"])
    carts = [c for c in fx.state.carts.values() if c["items"]]
    t.check(carts and carts[0]["items"][0]["product_id"] == lamp["id"] and carts[0]["items"][0]["qty"] == 2
            and carts[0]["items"][0]["gift_wrap"] is True, "replay reproduced the end state: lamp ×2 with gift wrap in cart")


# ═══════════════════════════════════════════════════════════════════════
# Part 21 — replay
# ═══════════════════════════════════════════════════════════════════════
@selftest(21, "Replay a recorded workflow ×10: identical step order and all network validations pass every time")
def t_replay_ten_times(t: T) -> None:
    fx = t.fx
    with t.session("record") as s:
        result = _record_shop_flow(t, s)
    wf = result["workflow"]
    times = t.reps(10, 3)
    fx.reset()
    rep = replay(wf, times=times, secrets=result["secrets"], out_dir=t.out / "replays", profile=fx.profile(), hitl="off")
    orders = {tuple(r["step_order"]) for r in rep["runs"]}
    vals = [v for r in rep["runs"] for v in r["validations"]]
    t.metric("passed", f"{rep['passed']}/{times}")
    t.metric("validations", f"{sum(1 for v in vals if v['ok'])}/{len(vals)}")
    t.check(rep["passed"] == times, f"{rep['passed']}/{times} replays succeeded")
    t.check(rep["consistent_order"] and len(orders) == 1 and len(next(iter(orders))) == len(wf["steps"]),
            f"step order identical in all runs ({len(wf['steps'])} steps)")
    t.check(vals and all(v["ok"] for v in vals), f"all {len(vals)} per-step network validations passed")
    t.check(len({r["fingerprint"] for r in rep["runs"]}) == 1, "run fingerprints identical (deterministic network behaviour)")


# ═══════════════════════════════════════════════════════════════════════
# Part 22 — self-healing element resolution
# ═══════════════════════════════════════════════════════════════════════
@selftest(22, "Selectors/ids/classes/structure changed: ≥90% of recorded actions still hit the right element")
def t_self_healing(t: T) -> None:
    fx = t.fx
    plan = [("click", "Refresh", None, ("refresh", "")), ("click", "Clear filters", None, ("clear", "")),
            ("click", "Buy button for Linen Throw Pillow", None, ("buy", "item=2")),
            ("click", "Buy button for Oak Side Table", None, ("buy", "item=4")),
            ("fill", "Email address", "me@example.org", None), ("click", "Subscribe", None, ("subscribe", "email=me%40example.org")),
            ("select", "Sort by", "price", ("sort", "by=price")), ("check", "I agree to the terms", None, ("agree", "v=true")),
            ("fill_enter", "Search catalog", "lamps", ("search", "q=lamps")), ("click", "Help center", None, ("help", "")),
            ("click", "Next page", None, ("next", ""))]
    steps = []
    with t.session("variant-a") as s:
        s.goto(fx.url("/lab/heal?variant=a", "lab"))
        for kind, target, value, _ in plan:
            el = s._resolve(target, purpose="fill" if kind.startswith("fill") else ("select" if kind == "select" else
                                                                                     "check" if kind == "check" else "click")).element
            steps.append({"kind": kind, "target": {"selector": el.css, "fingerprint": el.fingerprint(), "text": el.name},
                          "value": value})
    fx.state.events.clear()
    ok = 0
    detail = []
    with t.session("variant-b") as s:
        s.goto(fx.url("/lab/heal?variant=b", "lab"))
        for (kind, target, value, effect), st in zip(plan, steps):
            before = len(fx.state.events)
            try:
                if kind == "click":
                    r = s.click(st["target"])
                elif kind == "fill":
                    r = s.fill(st["target"], value)
                elif kind == "fill_enter":
                    r = s.fill(st["target"], value, submit=True)
                elif kind == "select":
                    r = s.select(st["target"], value)
                else:
                    r = s.check(st["target"])
                healed = (r.get("resolved") or {}).get("strategy")
                new = fx.state.events[before:]
                if effect is None:
                    good = r["ok"]
                else:
                    good = any(e["kind"] == "heal" and e["action"] == effect[0] and effect[1] in e["query"] for e in new)
                detail.append((target, healed, good))
                ok += bool(good)
            except AgentTraceError as exc:
                detail.append((target, "error", str(exc)[:80]))
    rate = ok / len(plan)
    for d in detail:
        t.note(f"{d}")
    t.metric("healed_ok", f"{ok}/{len(plan)} ({rate:.0%})")
    t.check(sum(1 for d in detail if d[1] == "healed") >= 8, "original selectors were broken → resolution went through healing")
    t.check(rate >= 0.9, f"{rate:.0%} of actions executed on the right element without selector updates (criterion ≥90%)")


# ═══════════════════════════════════════════════════════════════════════
# Part 23 — pagination & dynamic content
# ═══════════════════════════════════════════════════════════════════════
@selftest(23, "20-page pagination (400 items) + infinite scroll (100 items) + load-more: all content, per-step capture")
def t_pagination(t: T) -> None:
    fx = t.fx
    with t.session("pages") as s:
        s.goto(fx.url("/catalogue/page-1.html"))
        det = s.detect_pagination()
        res = s.paginate(max_pages=25)
        t.check(det["type"] == "next" and res["mode"] == "next", "pagination type detected: next link")
        t.check(res["page_count"] == 20 and res["stopped_because"] == "no_next_link", f"visited {res['page_count']} pages, stopped: {res['stopped_because']}")
        t.check(res["total_items"] == 400 and len({i["url"] for i in res["items"]}) == 400, f"{res['total_items']} unique items collected")
        steps = [p for p in res["pages"] if p["action_id"]]
        t.check(len(steps) == 19 and all(p["requests"] and "page-" in (p["primary"] or "") for p in steps),
                "each page navigation captured as its own action (primary = next page document)")
        t.check(res["items"][0].get("title") and res["items"][0].get("price") and res["items"][0].get("url"),
                f"fields auto-detected: {sorted(res['fields'])}")
    with t.session("scroll") as s:
        s.goto(fx.url("/trending"))
        res = s.paginate(mode="scroll", max_pages=30)
        feed = [r for r in s.recorder.records if "/api/v1/feed" in r.url]
        scroll_actions = [p for p in res["pages"] if p["action_id"]]
        t.check(res["total_items"] == 100, f"infinite scroll: {res['total_items']}/100 items")
        t.check(len(feed) == 10 and all(r.status == 200 for r in feed), f"{len(feed)} feed API calls captured")
        t.check(sum(1 for p in scroll_actions if "/api/v1/feed" in (p["primary"] or "")) >= 9,
                "each scroll step's feed request identified as its primary request")
    with t.session("deals") as s:
        s.goto(fx.url("/deals"))
        res = s.paginate(mode="load_more", max_pages=10)
        t.check(res["total_items"] == 40 and res["stopped_because"] in ("no_load_more_button", "no_new_items"),
                f"load-more: {res['total_items']}/40 deals, stopped: {res['stopped_because']}")
    t.metric("catalogue_items", 400)
    t.metric("feed_items", 100)


# ═══════════════════════════════════════════════════════════════════════
# Part 24 — API & endpoint discovery
# ═══════════════════════════════════════════════════════════════════════
@selftest(24, "Discovery report finds ≥90% of the app's known REST + GraphQL endpoints with method/URL/params")
def t_api_discovery(t: T) -> None:
    fx = t.fx
    with t.session("explore") as s:
        s.goto(fx.url("/login"))
        s.fill("Username", "demo")
        s.fill("Password", "demo123")
        s.click("Sign in")
        s.goto(fx.url("/"))
        s.fill("Search products", "lamp")
        s.press("Enter", "Search products")
        s.click("first result link")
        s.click("Save to wishlist")
        s.click("Add to cart")
        s.goto(fx.url("/cart"))
        row = s.find("Quantity for")
        s.fill(row["ref"], "3")
        s.press("Tab")
        s.click("Remove")
        s.goto(fx.url(fx.state.catalog.url(fx.state.catalog.by_id[40])))
        s.click("Add to cart")
        s.goto(fx.url("/checkout"))
        for label, value in (("Full name", "Ada Lovelace"), ("Email", "ada@example.org"), ("Address", "1 Analytical St"),
                             ("City", "London")):
            s.fill(label, value)
        s.check("I accept the terms and conditions")
        s.click("Place order")
        s.goto(fx.url("/trending"))
        s.scroll(to="bottom")
        s.goto(fx.url("/deals"))
        s.click("Load more deals")
        eps = s.endpoints()
    keys = {(e["method"], e["path"], (e.get("graphql") or {}).get("operation")) for e in eps if e["host"] == "shop.agenttrace.test"}
    known = [("GET", "/api/v1/products", None), ("GET", "/api/v1/products/{id}/reviews", None), ("GET", "/api/v1/categories", None),
             ("GET", "/api/v1/search", None), ("GET", "/api/v1/suggest", None), ("GET", "/api/v1/cart", None),
             ("POST", "/api/v1/cart", None), ("PATCH", "/api/v1/cart/{id}", None), ("DELETE", "/api/v1/cart/{id}", None),
             ("POST", "/api/v1/orders", None), ("GET", "/api/v1/feed", None), ("GET", "/api/v1/deals", None),
             ("POST", "/graphql", "GetRecommendations"), ("POST", "/graphql", "AddToWishlist"), ("POST", "/login", None)]
    found = [k for k in known if k in keys]
    missing = [k for k in known if k not in keys]
    rate = len(found) / len(known)
    by = {(e["method"], e["path"], (e.get("graphql") or {}).get("operation")): e for e in eps}
    t.metric("found", f"{len(found)}/{len(known)} ({rate:.0%})")
    t.check(rate >= 0.9, f"{len(found)}/{len(known)} known endpoints discovered", missing=missing)
    search = by.get(("GET", "/api/v1/search", None)) or {}
    t.check("q" in (search.get("query_params") or {}), "search endpoint: query param 'q' detected")
    feed = by.get(("GET", "/api/v1/feed", None)) or {}
    t.check({"cursor", "limit"} <= set(feed.get("query_params") or {}) and feed.get("pagination"), "feed: cursor/limit + pagination hint")
    cart = by.get(("POST", "/api/v1/cart", None)) or {}
    _fs = flatten_schema
    t.check({"product_id", "qty"} <= set(_fs(cart.get("request_schema"))), "POST cart request payload schema inferred")
    prods = by.get(("GET", "/api/v1/products", None)) or {}
    t.check(_fs(prods.get("response_schema")).get("items[].price") == "number", "products response schema: items[].price is number")
    t.check(all(e.get("examples") for e in by.values()), "every endpoint has an example request")
    login = by.get(("POST", "/login", None)) or {}
    t.check(login.get("kind") == "form" and {"username", "password", "csrf_token"} <= set(login.get("form_fields") or []),
            "login form POST discovered with its fields (username, password, csrf_token)")
    write_text(t.out / "endpoints.md", endpoints_markdown(eps))


# ═══════════════════════════════════════════════════════════════════════
# Part 25 — noise classification & smart filtering
# ═══════════════════════════════════════════════════════════════════════
@selftest(25, "Analytics-heavy pages: 100% of app API calls kept, ≥95% of noise filtered; rules are configurable")
def t_noise_filtering(t: T) -> None:
    _NH, _NR, _filter = NOISE_HOSTS, NoiseRules, filter_records
    fx = t.fx
    with t.session("noisy") as s:
        s.goto(fx.url("/"))
        s.goto(fx.url(fx.state.catalog.url(fx.state.catalog.by_id[7])))
        s.click("Add to cart")
        s.goto(fx.url("/search?q=vase"))
        recs = list(s.recorder.records)
    noise = [r for r in recs if r.host in _NH]
    app = [r for r in recs if r.host == "shop.agenttrace.test" and (r.path.startswith("/api/") or r.path == "/graphql")]
    res = _filter(recs, _NR(dedupe=False))
    kept = set(id(r) for r in res.kept)
    noise_dropped = sum(1 for r in noise if id(r) not in kept)
    app_kept = sum(1 for r in app if id(r) in kept)
    rate = noise_dropped / max(1, len(noise))
    t.metric("noise_requests", len(noise))
    t.metric("noise_filtered", f"{rate:.1%}")
    t.metric("app_api_kept", f"{app_kept}/{len(app)}")
    t.check(len(noise) >= 60, f"test page really is analytics-heavy ({len(noise)} tracker requests from {len({r.host for r in noise})} hosts)")
    t.check(app_kept == len(app), f"all {len(app)} application API calls retained")
    t.check(rate >= 0.95, f"{rate:.1%} of noise filtered (criterion ≥95%)",
            leaked=[r.url for r in noise if id(r) in kept][:5])
    cats = {r.category for r in noise}
    t.check({"analytics", "tracking", "ads", "monitoring", "consent"} <= cats, f"noise classified into categories: {sorted(cats)}")
    custom = _NR(keep_patterns=(r"api\.segment\.io",), drop_patterns=(r"/api/v1/cart$",),
                 extra_domains={"analytics": ["shop.agenttrace.test/api/v1/suggest"]}, dedupe=False)
    res2 = _filter(recs, custom)
    k2 = set(id(r) for r in res2.kept)
    t.check(any("api.segment.io" in r.url for r in res2.kept), "keep_patterns rule: segment calls retained on request")
    t.check(not any(r.url.endswith("/api/v1/cart") and id(r) in k2 for r in recs), "drop_patterns rule: cart API dropped on request")


# ═══════════════════════════════════════════════════════════════════════
# Part 26 — AI-friendly structured output
# ═══════════════════════════════════════════════════════════════════════
@selftest(26, "report.json alone (no HAR parsing) gives each action's target, related request and validation status")
def t_ai_output(t: T) -> None:
    fx = t.fx
    p = fx.state.catalog.by_id[33]
    with t.session("ai", strict=False) as s:
        s.goto(fx.url(fx.state.catalog.url(p)))
        s.click("Add to cart", expect={"url": "/api/v1/cart", "method": "POST", "status": 201})
        s.click("Save to wishlist", expect={"url": "/graphql", "method": "POST", "request_json": {"operationName": "AddToWishlist"}})
        s.fill("Search products", "mirror", submit=True)
        s.click("Checkout", expect={"url": "/api/v1/orders"})
        summary = s.finish()
    rep = read_json(summary["paths"]["report_json"])
    acts = rep["actions"]
    truth = [("goto", "GET", "/product/", None), ("click", "POST", "/api/v1/cart", True),
             ("click", "POST", "/graphql", True), ("fill", "GET", "/search?q=mirror", None), ("click", None, None, False)]
    for a, (kind, method, url, valid) in zip(acts, truth):
        prim = (a.get("network") or {}).get("primary") or {}
        t.check(a["action"] == kind and a.get("target"), f"{a['id']}: action={a['action']} target={a['target']!r}")
        if method:
            t.check(prim.get("method") == method and url in prim.get("url", ""), f"{a['id']}: related request {prim.get('method')} {prim.get('url', '')[:60]}")
        if valid is not None:
            t.check(bool((a.get("validation") or {}).get("ok")) == valid, f"{a['id']}: validation ok={valid}")
    t.check(acts[4]["ok"] is False and acts[4].get("error") and acts[4].get("hint"), "failed action carries error + hint for the AI")
    t.check(all("resolved" in a for a in acts if a["action"] in ("click", "fill") and a["ok"]),
            "resolved element (role/name/ref) included for every interaction")
    har_size = Path(summary["paths"]["har"]).stat().st_size
    rep_size = Path(summary["paths"]["report_json"]).stat().st_size
    t.metric("report_vs_har", f"{rep_size / har_size:.1%}")
    t.check(rep_size < har_size / 3, f"compact: report.json is {rep_size / har_size:.0%} of the raw HAR size")


# ═══════════════════════════════════════════════════════════════════════
# Part 27 — orchestration, checkpoint & resume
# ═══════════════════════════════════════════════════════════════════════
@selftest(27, "30-step workflow killed mid-run, restarted with --resume: continues from checkpoint, no completed step repeated")
def t_checkpoint_resume(t: T) -> None:
    fx = t.fx
    run_id = f"resume{int(time.time())}"
    cfg = {"name": "thirty-steps", "profile": {"host_map": fx.host_map(), "no_proxy_server": True}, "hitl": "off",
           "steps": [{"goto": fx.url(f"/lab/steps?run={run_id}", "lab")}] +
                    [{"click": f"Step {i}", "expect": {"url": f"/lab/api/steps/{i}", "method": "POST", "status": 200}}
                     for i in range(1, 31)]}
    path = t.out / "thirty.json"
    write_json(path, cfg)
    out = t.out / "run"
    state_file = out / "state.json"
    cmd = self_command() + ["run", str(path), "--out", str(out)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_env())
    killed_at = None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if state_file.exists():
            try:
                st = read_json(state_file)
            except Exception:  # noqa: BLE001
                continue
            done = sum(1 for x in st["steps"] if x["status"] == "done")
            if done >= 12:
                proc.kill()
                killed_at = done
                break
        if proc.poll() is not None:
            break
    proc.wait(timeout=30)
    t.check(killed_at is not None, f"process killed after {killed_at} completed steps (hard kill)")
    before = read_json(state_file)
    done_before = [i for i, x in enumerate(before["steps"]) if x["status"] == "done"]
    counts_before = dict(fx.state.steps.get(run_id, {}))
    res = subprocess.run(self_command() + ["run", str(path), "--out", str(out), "--resume"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=child_env(), timeout=300)
    t.check(res.returncode == 0, "resume run finished OK", stderr=res.stderr[-1500:], stdout=res.stdout[-1500:])
    after = read_json(state_file)
    counts = fx.state.steps.get(run_id, {})
    completed_before = [i for i in done_before if i >= 1]
    dup_completed = [i for i in completed_before if counts.get(i, 0) != 1]
    t.check(after["status"] == "done" and after["resumes"] == 1, "state: done after exactly one resume")
    t.check(sorted(counts) == list(range(1, 31)) and all(v >= 1 for v in counts.values()), "all 30 steps executed")
    t.check(not dup_completed, f"0 of {len(completed_before)} already-completed steps were executed again", dup=dup_completed)
    extra = sum(v - 1 for v in counts.values())
    t.check(extra <= 1, f"at most the single in-flight step re-ran ({extra})")
    t.metric("killed_after_steps", killed_at)
    t.metric("server_counts_before_resume", len(counts_before))


# ═══════════════════════════════════════════════════════════════════════
# Part 28 — screenshot & evidence capture
# ═══════════════════════════════════════════════════════════════════════
@selftest(28, "Every important action has screenshot + DOM + metadata + network HAR under the same identifier")
def t_evidence(t: T) -> None:
    fx = t.fx
    with t.session("evidence", evidence="important", strict=False) as s:
        s.goto(fx.url("/login"))
        s.fill("Username", "demo")
        s.fill("Password", "demo123")
        s.click("Sign in")
        s.goto(fx.url("/account"))
        s.download("Download orders (CSV)")
        s.click("Nonexistent magic button")
        summary = s.finish()
    rep = read_json(summary["paths"]["report_json"])
    important = [a for a in rep["actions"] if a["action"] in ("goto", "download") or a.get("navigated") or not a["ok"]]
    t.check(len(important) >= 5, f"{len(important)} important actions (navigations, download, failure)")
    for a in important:
        ev = a.get("evidence") or {}
        folder = Path(summary["out_dir"]) / "evidence" / a["id"]
        files = {p.name for p in folder.glob("*")}
        t.check({"screenshot.png", "dom.html", "meta.json", "network.har"} <= files, f"{a['id']} ({a['action']}): {sorted(files)}")
        meta = read_json(folder / "meta.json")
        t.check(meta["id"] == a["id"], f"{a['id']}: metadata carries the same action id")
        entries = load_har(folder / "network.har")["log"]["entries"]
        t.check(all(e["_agenttrace"]["action_id"] == a["id"] for e in entries), f"{a['id']}: {len(entries)} network entries belong to it")
        png = (folder / "screenshot.png").read_bytes()
        t.check(png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000, f"{a['id']}: screenshot is a real PNG ({len(png)} bytes)")
    failed = [a for a in rep["actions"] if not a["ok"]]
    t.check(failed and failed[0].get("evidence", {}).get("screenshot"), "failure evidence captured for the failed action")
    man = read_json(summary["paths"]["manifest"])
    t.check(sum(1 for x in man["artifacts"] if x["path"].startswith("evidence/")) >= 4 * len(important),
            "manifest indexes all evidence files")


# ═══════════════════════════════════════════════════════════════════════
# Part 29 — execution timeline & audit report
# ═══════════════════════════════════════════════════════════════════════
@selftest(29, "Timeline/report give start, end, action, network activity and final status of any step")
def t_timeline(t: T) -> None:
    _pi, _rj = parse_iso, read_jsonl
    fx = t.fx
    with t.session("audit", strict=False, retry={"max_attempts": 2, "backoff_ms": 100}) as s:
        s.goto(fx.url("/lab/downloads", "lab"))
        s.download("Download CSV report")
        s.goto(fx.url("/lab/console", "lab"))
        s.click("Run broken widget")
        s.goto(fx.url("/lab/flaky/step/1?run=audit", "lab"))
        s.click("Confirm step 1")
        s.click("Button that does not exist")
        summary = s.finish()
    events = _rj(summary["paths"]["timeline"])
    rep = read_json(summary["paths"]["report_json"])
    kinds = {e["kind"] for e in events}
    t.check({"action_start", "action_end", "request", "response", "navigation", "download_saved", "console", "retry",
             "action_error"} <= kinds, f"timeline event kinds: {sorted(kinds)}")
    ts = [_pi(e["ts"]) for e in events]
    t.check(all(a <= b for a, b in zip(ts, ts[1:])), f"{len(events)} events in chronological order")
    for a in rep["actions"]:
        st = [e for e in events if e["kind"] == "action_start" and e.get("action_id") == a["id"]]
        en = [e for e in events if e["kind"] == "action_end" and e.get("action_id") == a["id"]]
        reqs = [e for e in events if e["kind"] == "request" and e.get("action_id") == a["id"]]
        t.check(len(st) == 1 and len(en) == 1 and st[0]["ts"] <= en[0]["ts"] and en[0]["ok"] == a["ok"],
                f"{a['id']} {a['action']}: start {st[0]['ts'][11:23]} → end {en[0]['ts'][11:23]}, "
                f"{len(reqs)} requests, status {'ok' if a['ok'] else 'failed'}")
        if a["action"] in ("goto", "download"):
            t.check(reqs, f"{a['id']}: network activity attributed")
    md = Path(summary["paths"]["report_md"]).read_text(encoding="utf-8")
    t.check("## Actions" in md and "## Timeline" in md and "retry" in md and "download_saved" in md, "report.md holds actions + timeline")
    t.metric("events", len(events))


# ═══════════════════════════════════════════════════════════════════════
# Part 30 — network regression & change detection
# ═══════════════════════════════════════════════════════════════════════
@selftest(30, "A known API change (price number→string, rating→stars, +currency) is pinpointed; identical runs report nothing")
def t_regression(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog

    def journey(name: str) -> dict:
        with t.session(name) as s:
            s.goto(fx.url("/"))
            s.goto(fx.url(cat.url(cat.by_id[12])))
            s.fill("Search products", "lamp", submit=True)
            s.goto(fx.url("/deals"))
            return s.finish()

    base, again = journey("baseline"), journey("baseline_again")
    fx.state.variant["api_change"] = True
    try:
        changed = journey("changed")
    finally:
        fx.state.variant["api_change"] = False
    eps = {name: read_json(summ["paths"]["endpoints"]) for name, summ in
           (("base", base), ("again", again), ("changed", changed))}
    same = compare_endpoints(eps["base"], eps["again"])
    t.check(not same["changes"], f"identical runs → 0 changes over {same['endpoints_before']} endpoints (no false positives)",
            changes=same["changes"][:6])
    diff = compare_endpoints(eps["base"], eps["changed"])
    by_ep: dict = {}
    for c in diff["changes"]:
        by_ep.setdefault(c["endpoint"], []).append(c)
    for key, changes in sorted(by_ep.items()):
        t.note(f"{key}: " + "; ".join(c["detail"] for c in changes))

    def product_shaped(ep: dict) -> bool:
        fields = flatten_schema(ep.get("response_schema"))
        return any(f.endswith("price") and ty in ("number", "integer") for f, ty in fields.items()) and \
            any(f.endswith("rating") for f in fields)
    expected = {e["key"] for e in eps["base"] if product_shaped(e)}
    t.check(len(expected) >= 3, f"{len(expected)} endpoints return product JSON in the baseline: {sorted(expected)}")
    t.check(set(by_ep) == expected, "exactly the endpoints serving product JSON are flagged (nothing else)",
            flagged=sorted(by_ep), expected=sorted(expected))
    for key in sorted(expected):
        det = [(c["change"], c["detail"]) for c in by_ep.get(key, [])]
        t.check(any(ch == "response_type_changed" and "price" in d and "string" in d for ch, d in det),
                f"{key}: price type number → string")
        t.check(any(ch == "response_field_removed" and "rating" in d for ch, d in det), f"{key}: 'rating' removed")
        t.check(any(ch == "response_field_added" and "stars" in d for ch, d in det) and
                any(ch == "response_field_added" and "currency" in d for ch, d in det), f"{key}: 'stars' + 'currency' added")
    t.check(diff["breaking"] >= 2 * len(expected), f"{diff['breaking']} breaking changes classified")
    res = subprocess.run(self_command() + ["diff", base["out_dir"], changed["out_dir"], "--md"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env=child_env(), timeout=120)
    write_text(t.out / "regression.md", res.stdout)
    t.check(res.returncode == 4 and "breaking" in res.stdout and "price" in res.stdout and "string" in res.stdout,
            "CLI `diff A B --md` exits 4 and the report names the changed field", rc=res.returncode, err=res.stderr[-500:])
    res0 = subprocess.run(self_command() + ["diff", base["out_dir"], again["out_dir"]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=child_env(), timeout=120)
    t.check(res0.returncode == 0, "CLI `diff` exits 0 for two identical runs", rc=res0.returncode)
    t.metric("endpoints", len(eps["base"]))
    t.metric("flagged", f"{len(by_ep)}/{len(expected)}")
    t.metric("breaking", diff["breaking"])


# ═══════════════════════════════════════════════════════════════════════
# Part 31 — project & session isolation
# ═══════════════════════════════════════════════════════════════════════
@selftest(31, "3 projects run concurrently (own login, cart, workflow, HAR, outputs): nothing leaks between them")
def t_isolation(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    root = t.out / "projects"
    prof = {"host_map": fx.host_map(), "no_proxy_server": True, "navigation_timeout_ms": 20_000, "action_timeout_ms": 8_000}
    specs = {"north": ("demo", "demo123", 11), "south": ("alice", "wonderland", 22), "east": ("bob", "builder42", 33)}
    emails = {name: f"{u}@shoplab.test" for name, (u, _p, _i) in specs.items()}

    def job(name: str):
        user, pw, pid = specs[name]

        def run() -> dict:
            proj = Project(name, root=root, profile=prof)
            wf = {"name": f"{name}-order", "steps": [
                {"goto": fx.url("/login")},
                {"fill": {"target": "Username", "text": user}},
                {"fill": {"target": "Password", "text": pw}},
                {"click": "Sign in"},
                {"goto": fx.url(cat.url(cat.by_id[pid]))},
                {"click": "Add to cart", "expect": {"url": "/api/v1/cart", "method": "POST", "status": 201}},
                {"goto": fx.url("/cart")},
                {"goto": fx.url("/account")}]}
            with proj.session(hitl="off") as s:
                res = WorkflowRunner(wf, s, state_path=proj.dir / "state" / "workflow.json").run()
                cart = s.evaluate("() => fetch('/api/v1/cart').then(r => r.json())")
                cookies = {c["name"]: c["value"] for c in s.cookies(values=True)}
                proj.save_state(s)
                summary = s.finish()
            return {"ok": res["ok"], "cart": cart, "cookies": cookies, "summary": summary, "dir": str(proj.dir)}
        return (name, run)

    pool = TaskPool(max_workers=3)
    results = {r.name: r for r in pool.run([job(n) for n in specs])}
    for name, r in results.items():
        t.check(r.ok and r.value["ok"], f"project {name}: workflow finished in {r.duration_s}s on thread {r.thread}",
                error=(r.error or "")[:800])
    starts = [ts for ts, _n, ev, _a in pool.timeline if ev == "start"]
    ends = [ts for ts, _n, ev, _a in pool.timeline if ev == "end"]
    t.check(pool.peak == 3 and max(starts) < min(ends), "all 3 projects really ran at the same time")
    vals = {n: r.value for n, r in results.items()}
    for name, v in vals.items():
        user, _pw, pid = specs[name]
        t.check([i["product_id"] for i in v["cart"]["items"]] == [pid], f"{name}: cart holds only its own product #{pid}",
                cart=v["cart"])
        with fx.state.lock:
            owner = fx.state.sessions.get(v["cookies"].get("sessionid", ""))
        t.check(owner == user, f"{name}: its session cookie belongs to '{user}' on the server")
    for cookie in ("sessionid", "cart_id", "csrftoken"):
        values = [v["cookies"].get(cookie) for v in vals.values()]
        t.check(all(values) and len(set(values)) == 3, f"'{cookie}' is different in every project")
    text_ext = {".har", ".json", ".jsonl", ".md", ".html", ".txt", ".csv"}
    for name, v in vals.items():
        foreign = [x for other, ov in vals.items() if other != name
                   for x in (ov["cookies"]["sessionid"], ov["cookies"]["cart_id"], emails[other])]
        files = [f for f in Path(v["dir"]).rglob("*") if f.is_file() and f.suffix in text_ext]
        leaks = [(str(f.relative_to(root)), x[:12]) for f in files for x in foreign
                 if x in f.read_text(encoding="utf-8", errors="replace")]
        own = sum(1 for f in files if emails[name] in f.read_text(encoding="utf-8", errors="replace"))
        t.check(not leaks and own > 0, f"{name}: 0 foreign cookies/emails in its {len(files)} files "
                f"(its own email appears in {own})", leaks=leaks[:5])
        st = read_json(Path(v["dir"]) / "state" / "workflow.json")
        t.check(st["workflow"] == f"{name}-order" and all(x["status"] == "done" for x in st["steps"]),
                f"{name}: own workflow checkpoint ({len(st['steps'])} steps done)")
        t.check(Path(v["summary"]["out_dir"]).resolve().is_relative_to(Path(v["dir"]).resolve()),
                f"{name}: run output stays inside its project folder")

    def restart(name: str):
        def run() -> str:
            proj = Project(name, root=root)
            with proj.session(hitl="off") as s:
                s.goto(fx.url("/account"))
                return s.evaluate("() => document.querySelector('h1').innerText")
        return (name, run)
    again = {r.name: r for r in TaskPool(max_workers=3).run([restart(n) for n in specs])}
    for name, r in again.items():
        want = f"Welcome, {fx_user_name(specs[name][0])}"
        t.check(r.ok and r.value == want, f"{name}: restart with its saved state → '{r.value}'", error=(r.error or "")[:500])
    t.metric("projects", 3)
    t.metric("peak_parallel", pool.peak)


def fx_user_name(user: str) -> str:
    return {"demo": "Demo Customer", "alice": "Alice Liddell", "bob": "Bob Builder"}[user]


# ═══════════════════════════════════════════════════════════════════════
# Part 32 — hooks & plugins
# ═══════════════════════════════════════════════════════════════════════
AUDIT_PLUGIN_SRC = r'''
"""Audit & safety plugin — loaded from a workflow config, no AgentTrace code changes.

pre-click : blocks destructive clicks (veto) and snapshots the cart badge
post-request: tags every API call and pulls the cart total out of JSON bodies
output    : writes plugin_audit.json next to the session artifacts
"""
import json
import re
from pathlib import Path

DESTRUCTIVE = re.compile(r"\b(remove|delete|cancel order|close account)\b", re.I)
AUDIT = {"pre_click": [], "post_request": []}


def pre_action(session, action):
    if action["action"] != "click":
        return None
    if DESTRUCTIVE.search(action.get("target") or ""):
        AUDIT["pre_click"].append({"action_id": action["id"], "target": action["target"], "decision": "veto"})
        return {"veto": "destructive click blocked by audit plugin"}
    badge = session.page.evaluate("() => (document.querySelector('#cart-count') || {}).textContent || null")
    AUDIT["pre_click"].append({"action_id": action["id"], "target": action["target"], "decision": "allow",
                               "cart_badge_before": badge, "url": session.page.url})
    return None


def request_finished(session, record):
    if "/api/" not in record.url or record.failure:
        return
    record.tags.append("audited")
    entry = {"request_id": record.id, "method": record.method, "url": record.url, "status": record.status}
    data = record.json()
    if isinstance(data, dict) and {"items", "total", "count"} <= set(data):
        entry["cart_total"] = data["total"]
        record.extra["audit_cart_total"] = data["total"]
    AUDIT["post_request"].append(entry)


def output(session, paths, report):
    target = Path(session.out_dir) / "plugin_audit.json"
    target.write_text(json.dumps(AUDIT, indent=1), encoding="utf-8")
    paths["plugin_audit"] = str(target)
'''


@selftest(32, "Plugin file (config only, no core change) adds pre-click + post-request + output logic that really runs")
def t_plugins(t: T) -> None:
    fx = t.fx
    p = fx.state.catalog.by_id[27]
    write_text(t.out / "audit_plugin.py", AUDIT_PLUGIN_SRC)
    cfg = {"name": "plugin-demo", "profile": {"host_map": fx.host_map(), "no_proxy_server": True}, "hitl": "off",
           "plugins": ["audit_plugin.py"],
           "steps": [{"goto": fx.url(fx.state.catalog.url(p))},
                     {"click": "Add to cart", "expect": {"url": "/api/v1/cart", "method": "POST", "status": 201}},
                     {"goto": fx.url("/cart")},
                     {"click": "Remove", "optional": True},
                     {"click": "Proceed to checkout"}]}
    write_json(t.out / "plugin_demo.json", cfg)
    since = time.time()
    out = t.out / "run"
    res = subprocess.run(self_command() + ["run", str(t.out / "plugin_demo.json"), "--out", str(out)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env=child_env(), timeout=300)
    t.check(res.returncode == 0, "workflow with the plugin finished OK", rc=res.returncode, stderr=res.stderr[-1500:],
            stdout=res.stdout[-800:])
    audit = read_json(out / "session" / "plugin_audit.json")
    decisions = [(x["target"], x["decision"], x.get("cart_badge_before")) for x in audit["pre_click"]]
    t.note(f"pre-click decisions: {decisions}")
    t.check(decisions == [("Add to cart", "allow", "0"), ("Remove", "veto", None), ("Proceed to checkout", "allow", "1")],
            "pre-click hook ran before every click (badge 0 → 1) and vetoed the destructive one")
    rep = read_json(out / "session" / "report.json")
    removed = [a for a in rep["actions"] if a["target"] == "Remove"]
    t.check(removed and not removed[0]["ok"] and "vetoed by plugin" in removed[0]["error"],
            "vetoed click is reported as failed with the plugin's reason")
    deletes = [r for r in fx.requests(path_prefix="/api/v1/cart/", since=since) if r["method"] == "DELETE"]
    t.check(not deletes, "the Remove request never reached the server (cart item kept)")
    entries = load_har(out / "session" / "session.har")["log"]["entries"]
    api = [e for e in entries if "/api/" in e["request"]["url"] and not e["_agenttrace"].get("failure")
           and not e["_agenttrace"].get("pending")]
    tagged = [e for e in api if "audited" in (e["_agenttrace"].get("tags") or [])]
    t.check(api and len(tagged) == len(api), f"post-request hook tagged {len(tagged)}/{len(api)} API calls in the HAR")
    t.check(len(audit["post_request"]) == len(api), f"post-request hook saw every API response ({len(audit['post_request'])})")
    totals = [e["_agenttrace"].get("extra", {}).get("audit_cart_total") for e in api if "/api/v1/cart" in e["request"]["url"]]
    t.check(any(x and x > 0 for x in totals), f"plugin enriched cart responses with their totals {totals[:4]}")
    man = read_json(out / "session" / "manifest.json")
    t.check(any(a["path"] == "plugin_audit.json" for a in man["artifacts"]), "output hook's file is indexed in the manifest")
    log = read_jsonl(out / "session" / "log.jsonl")
    t.check(any(e["event"] == "action_vetoed" for e in log) and
            next(e for e in log if e["event"] == "session_metrics")["hook_errors"] == 0, "veto logged; 0 plugin errors")
    t.metric("api_calls_tagged", len(tagged))


# ═══════════════════════════════════════════════════════════════════════
# Part 33 — observability & debug mode
# ═══════════════════════════════════════════════════════════════════════
@selftest(33, "Failed workflow: log.jsonl alone names the failing step, action, request and error (no replay needed)")
def t_observability(t: T) -> None:
    fx = t.fx
    p = fx.state.catalog.by_id[7]
    cfg = {"name": "checkout-broken", "profile": {"host_map": fx.host_map(), "no_proxy_server": True}, "hitl": "off",
           "retry": {"max_attempts": 2, "backoff_ms": 200},
           "steps": [{"goto": fx.url(fx.state.catalog.url(p))},
                     {"click": "Add to cart", "expect": {"url": "/api/v1/cart", "method": "POST", "status": 201}},
                     {"goto": fx.url("/checkout")},
                     {"fill": {"target": "Full name", "text": "Ada Lovelace"}},
                     {"fill": {"target": "Email", "text": "ada@example.org"}},
                     {"fill": {"target": "Address", "text": "12 Analytical Street"}},
                     {"fill": {"target": "City", "text": "London"}},
                     {"click": "Place order", "expect": {"url": "/api/v1/orders", "method": "POST", "status": 201}},
                     {"goto": fx.url("/account")}]}
    write_json(t.out / "checkout.json", cfg)
    out = t.out / "run"
    res = subprocess.run(self_command() + ["run", str(t.out / "checkout.json"), "--out", str(out), "--debug", "--trace"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env(), timeout=300)
    t.check(res.returncode == 2, "the broken workflow fails (exit code 2)", rc=res.returncode, stderr=res.stderr[-800:])
    # ── from here on only log.jsonl is used ──
    log = read_jsonl(out / "session" / "log.jsonl")
    t.check(log and all({"ts", "level", "event", "msg", "session_id"} <= set(e) for e in log) and
            len({e["session_id"] for e in log}) == 1, f"{len(log)} structured lines, each with ts/level/event/session_id")
    failed_steps = [e for e in log if e["event"] == "step_end" and e.get("status") == "failed"]
    t.check(len(failed_steps) == 1, "exactly one failed workflow step in the log")
    step = failed_steps[0]
    t.note(f"failed step: #{step['step']} {step['step_id']} ({step['step_action']}) → action {step.get('action_id')}")
    t.check(step["step"] == 8 and step["step_action"] == "click", "failing step identified: #8 (click)")
    fails = [e for e in log if e["event"] == "action_failed"]
    t.check(len(fails) == 1 and fails[0]["action_id"] == step["action_id"], "step → action id link is in the log")
    f = fails[0]
    t.note(f"action_failed: {f['msg'][:200]}")
    t.check(f["action"] == "click" and "Place order" in f["target"] and f["page_url"].endswith("/checkout"),
            "failing action identified: click 'Place order' on /checkout")
    blamed = next((r for r in f.get("failed_requests") or [] if r["request_id"] == f["request_id"]), None)
    t.check(blamed is not None and blamed["method"] == "POST" and "/api/v1/orders" in blamed["url"] and
            blamed["status"] == 422, f"offending request identified: {blamed and blamed['method']} "
            f"{blamed and blamed['url'][-20:]} → {blamed and blamed['status']} ({f['request_id']})")
    t.check("terms" in (blamed.get("response_excerpt") or ""), "server's error body (terms not accepted) is in the log line")
    t.check(any("expected 201" in x and "422" in x for c in f["failed_checks"] for x in c["failures"]),
            "failed expectation says what was expected and what came back")
    t.check(f["attempts"] == 2 and f.get("retries"), "both attempts (retry) are visible")
    reqlines = [e for e in log if e["event"] == "request"]
    t.check(any(e.get("request_id") == f["request_id"] and e.get("action_id") == f["action_id"] for e in reqlines),
            f"debug trace: {len(reqlines)} request lines, the failing one carries request_id + action_id")
    metrics = next((e for e in log if e["event"] == "session_metrics"), None)
    t.check(metrics and metrics["failed"] == 1 and metrics["requests"] > 10 and metrics["action_ms_p95"] > 0,
            "execution metrics logged (actions, failures, requests, p50/p95 action time)")
    trace = out / "session" / "trace.zip"
    t.check(trace.exists() and trace.stat().st_size > 20_000, f"Playwright trace.zip saved ({trace.stat().st_size if trace.exists() else 0} bytes)")
    t.metric("log_lines", len(log))


# ═══════════════════════════════════════════════════════════════════════
# Part 34 — concurrent tasks & resource limits
# ═══════════════════════════════════════════════════════════════════════
@selftest(34, "5 independent browser tasks with max 3 in parallel: separate results, HARs and reports, no state mixing")
def t_concurrency(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    prod44 = cat.url(cat.by_id[44])

    def task(name: str, body: Callable, marker: str):
        def run() -> dict:
            s = Session(profile=fx.profile(), out_dir=t.out / name, name=name, hitl="off")
            with s:
                value = body(s)
                summary = s.finish()
            return {"value": value, "summary": summary, "marker": marker, "session_id": s.id}
        return (name, run)

    def catalogue(s):
        s.goto(fx.url("/catalogue/page-3.html"))
        return [r["title"] for r in s.extract_list()["items"]]

    def search(s):
        s.goto(fx.url("/"))
        s.fill("Search products", "mirror", submit=True)
        s.wait_for({"text": "results for"})
        return s.evaluate("() => document.querySelector('#result-count').innerText")

    def cart(s):
        s.goto(fx.url(prod44))
        s.click("Add to cart", expect={"url": "/api/v1/cart", "method": "POST", "status": 201})
        return [i["product_id"] for i in s.evaluate("() => fetch('/api/v1/cart').then(r => r.json())")["items"]]

    def news(s):
        s.goto(fx.url("/", "news"))
        return len(s.extract_list()["items"])

    def login(s):
        s.goto(fx.url("/login"))
        s.fill("Username", "alice")
        s.fill("Password", "wonderland")
        s.click("Sign in")
        return s.evaluate("() => document.querySelector('h1').innerText")

    tasks = [task("catalogue", catalogue, "/catalogue/page-3.html"), task("search", search, "q=mirror"),
             task("cart", cart, prod44), task("news", news, "news.agenttrace.test"), task("login", login, "/login")]
    pool = TaskPool(max_workers=3)
    t0 = time.monotonic()
    results = pool.run(tasks)
    wall = time.monotonic() - t0
    for r in results:
        t.check(r.ok, f"task {r.name}: ok in {r.duration_s}s ({r.thread})", error=(r.error or "")[:800])
    t.check(pool.peak == 3, f"peak parallel tasks = {pool.peak} (limit 3 respected and used)")
    serial = sum(r.duration_s for r in results)
    t.check(wall < serial * 0.8, f"parallel speed-up: wall {wall:.1f}s vs {serial:.1f}s serial")
    val = {r.name: r.value for r in results}
    want_titles = [p["name"] for p in cat.products[40:60]]
    t.check(len(val["catalogue"]["value"]) == 20 and all(any(w.startswith(x.rstrip("…. ")[:20]) for w in want_titles)
                                                         for x in val["catalogue"]["value"]),
            "catalogue task: its own 20 products")
    t.check(val["search"]["value"].startswith(f"{len(cat.search('mirror'))} results"), f"search task: '{val['search']['value']}'")
    t.check(val["cart"]["value"] == [44], "cart task: its own cart [44]")
    t.check(val["news"]["value"] >= 8, f"news task: {val['news']['value']} articles")
    t.check(val["login"]["value"] == "Welcome, Alice Liddell", "login task: logged in as Alice")
    ids = {v["session_id"] for v in val.values()}
    dirs = {v["summary"]["out_dir"] for v in val.values()}
    t.check(len(ids) == 5 and len(dirs) == 5, "5 distinct session ids and output folders")
    for name, v in val.items():
        urls = [e["request"]["url"] for e in load_har(v["summary"]["paths"]["har"])["log"]["entries"]]
        foreign = [o["marker"] for other, o in val.items() if other != name and any(o["marker"] in u for u in urls)]
        t.check(any(v["marker"] in u for u in urls) and not foreign and Path(v["summary"]["paths"]["report_json"]).exists(),
                f"{name}: own HAR ({len(urls)} entries, no other task's traffic) + report.json", foreign=foreign)
    t.metric("wall_s", round(wall, 1))
    t.metric("serial_s", round(serial, 1))
    t.metric("peak", pool.peak)


# ═══════════════════════════════════════════════════════════════════════
# Part 35 — end-to-end AI task execution
# ═══════════════════════════════════════════════════════════════════════
@selftest(35, "Plain-language multi-page goal → plan, act, capture, validate, report with evidence (no selectors/browser code)")
def t_goal_e2e(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    goal = ("Log in as demo with password demo123, then search for 'lamp', open the first result, add it to the cart, "
            "open the cart, verify 'Your cart' is shown, go to the catalogue and extract all product names and prices")
    plan = plan_goal(goal, start_url=fx.url("/"))
    t.note("plan: " + " | ".join(json.dumps({k: v for k, v in st.items() if k != "comment"}, ensure_ascii=False) for st in plan))
    targets = [str(v.get("target") if isinstance(v, dict) else v) for st in plan for k, v in st.items()
               if k in ("click", "fill", "select")]
    t.check(len(plan) >= 9 and not any(re.search(r"[#\[\]>=]|^\.|//", x) for x in targets),
            f"{len(plan)} planned steps, all targets in plain words (no CSS/XPath)")
    rep = run_goal(goal, start_url=fx.url("/"), out_dir=t.out / "goal", profile=fx.profile(), hitl="off")
    for r in rep["results"]:
        t.note(f"step {r['step']} {r['action']} ok={r['ok']} {r.get('error') or (r.get('result') or {}).get('primary') or ''}"[:160])
    t.check(rep["ok"] and len(rep["results"]) == len(plan), f"goal completed: {len(rep['results'])}/{len(plan)} steps ok")
    report = read_json(rep["artifacts"]["report_json"])
    visited = " ".join(report["summary"]["pages_visited"])
    for part in ("/login", "/account", "/search?q=lamp", "/product/", "/cart", "/catalogue/page-1.html"):
        t.check(part in visited, f"visited {part}")
    events = list(fx.state.events)
    t.check(any(e.get("kind") == "login" and e.get("user") == "demo" for e in events), "server saw the login as 'demo'")
    adds = [e for e in events if e.get("kind") == "cart_add"]
    t.check(len(adds) == 1 and "lamp" in cat.by_id[adds[0]["product_id"]]["name"].lower(),
            f"server saw exactly one add-to-cart of a lamp ({adds and cat.by_id[adds[0]['product_id']]['name']})")
    rows = rep["extracted"] or []
    names = [p["name"] for p in cat.products[:20]]
    t.check(len(rows) == 20 and all({"title", "price"} <= set(r) for r in rows), f"extracted {len(rows)} rows with title + price")
    t.check(all(re.match(r"^£\d+\.\d\d$", r["price"]) for r in rows) and
            all(any(n.startswith(r["title"].rstrip("….")[:20]) for n in names) for r in rows),
            "rows are the 20 products of catalogue page 1 with £ prices")
    problems = validate_har_file(rep["artifacts"]["har"])[1]
    entries = load_har(rep["artifacts"]["har"])["log"]["entries"]
    t.check(not problems and len(entries) > 40, f"valid HAR 1.2 with {len(entries)} entries", problems=problems[:3])
    t.check(any(e["request"]["method"] == "POST" and "/api/v1/cart" in e["request"]["url"] and e["response"]["status"] == 201
                for e in entries), "HAR holds the POST /api/v1/cart → 201")
    md = (Path(rep["out_dir"]) / "goal_report.md").read_text(encoding="utf-8")
    shots = list((Path(rep["out_dir"]) / "evidence").rglob("screenshot.png"))
    t.check("**Result:** SUCCESS" in md and "## Evidence" in md and "## Extracted data" in md and len(shots) >= 4,
            f"goal_report.md: SUCCESS + evidence ({len(shots)} screenshots) + extracted data")
    bad = run_goal("search for 'lamp', then click the 'Pay with crypto' button, then open the cart",
                   start_url=fx.url("/"), out_dir=t.out / "goal_fail", profile=fx.profile(), hitl="off",
                   retry={"max_attempts": 2, "backoff_ms": 100})
    last = bad["results"][-1]
    t.check(not bad["ok"] and last["action"] == "click" and "Pay with crypto" in json.dumps(last["params"]) and last.get("error")
            and last.get("hint") and len(bad["results"]) == 3, "impossible goal → FAILED at the right step with error + hint; stops there")
    t.check("**Result:** FAILED" in (Path(bad["out_dir"]) / "goal_report.md").read_text(encoding="utf-8") and
            validate_har_file(bad["artifacts"]["har"])[0], "failure report + valid HAR still produced")
    t.metric("steps", len(plan))
    t.metric("rows", len(rows))


# ═══════════════════════════════════════════════════════════════════════
# Part 36 — SPA client-side routes
# ═══════════════════════════════════════════════════════════════════════
@selftest(36, "React Router SPA: 6 route changes without reload → 6 virtual pages, each with its own network segment + HAR")
def t_spa_routes(t: T) -> None:
    fx = t.fx
    with t.session("spa") as s:
        s.goto(fx.url("/", "spa"))
        s.wait_for({"text": "Welcome to SPA Shop"})
        framework = s.evaluate("() => window.__SPA_FRAMEWORK")
        s.click("Products")
        s.click("SPA product 3")
        s.click("Cart")
        s.click("About")
        s.back()
        s.click("Home")
        s.wait_for({"text": "Welcome to SPA Shop"})
        routes = s.routes()
        summary = s.finish()
    t.note(f"framework: {framework}")
    t.check(framework == ("react-router" if fx.react_dir else "vanilla-history"), f"app runs on {framework}")
    for r in routes:
        t.note(f"{r['id']} {r['kind']:10} {r['url'][len(fx.url('', 'spa')) - 1:]:14} {r['title']!r:22} requests={r['requests']} api={r['api_calls']}")
    base = fx.url("", "spa").rstrip("/")
    want = [("navigation", "/", "Home - SPA", "/api/spa/home"), ("spa", "/products", "Products - SPA", "/api/spa/products"),
            ("spa", "/products/3", "Product 3 - SPA", "/api/spa/products/3"), ("spa", "/cart", "Cart - SPA", "/api/spa/cart"),
            ("spa", "/about", "About - SPA", "/api/spa/about"), ("spa", "/cart", "Cart - SPA", "/api/spa/cart"),
            ("spa", "/", "Home - SPA", "/api/spa/home")]
    got = [(r["kind"], r["url"][len(base):] or "/", r["title"]) for r in routes]
    t.check(got == [w[:3] for w in want], f"{len(routes)} (virtual) pages in order, 6 of them client-side routes", got=got)
    docs = sum(r["documents"] for r in routes)
    t.check(docs == 1, f"exactly {docs} full document load (no reloads)")
    recs = har_to_records(summary["paths"]["har"])
    for r, (_k, path, _title, api) in zip(routes, want):
        own = [x.url for x in recs if x.segment_id == r["id"] and "/api/spa/" in x.url]
        t.check(any(u.endswith(api) for u in own) and all(u.endswith(api) for u in own),
                f"{r['id']} {path}: its segment holds its own API call {api} (and nothing else)", own=own)
    index = read_json(summary["paths"]["routes"])
    t.check(len(index) == len(routes), f"routes/index.json lists {len(index)} per-route HARs")
    for item, (_k, path, title, api) in zip(index, want):
        har = load_har(item["har"])
        pages = har["log"]["pages"]
        ents = har["log"]["entries"]
        t.check(not validate_har(har) and len(pages) == 1 and pages[0]["id"] == item["id"] and pages[0]["title"] == title
                and ents and all(e["pageref"] == item["id"] for e in ents) and any(e["request"]["url"].endswith(api) for e in ents),
                f"{Path(item['har']).name}: valid HAR, 1 page '{title}', {len(ents)} entries")
    tl = read_jsonl(summary["paths"]["timeline"])
    t.check(sum(1 for e in tl if e["kind"] == "route_change") == 6, "timeline has 6 route_change events")
    t.metric("framework", framework)
    t.metric("virtual_pages", sum(1 for r in routes if r["virtual"]))


# ═══════════════════════════════════════════════════════════════════════
# Part 37 — CAPTCHA / bot wall with human-in-the-loop
# ═══════════════════════════════════════════════════════════════════════
@selftest(37, "CAPTCHA wall → detected, paused, operator notified; after a human solve the same session resumes the workflow")
def t_captcha_hitl(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    p5 = cat.by_id[5]
    notified: list = []
    seen_marker: list = []

    def human(session, info) -> None:
        """Stand-in for the operator: reads the pause marker, then solves the challenge in the same tab."""
        marker = Path(session.out_dir) / "PAUSED.json"
        seen_marker.append(read_json(marker)["url"] if marker.exists() else None)
        page = session.page
        page.wait_for_timeout(700)
        a, b = map(int, re.findall(r"\d+", page.locator("label[for=captcha-answer]").inner_text())[:2])
        page.check("#not-robot")
        page.fill("#captcha-answer", str(a + b))
        page.click("#verify")

    hitl = {"mode": "wait", "timeout_s": 60, "poll_s": 0.3, "handoff": False, "bell": False,
            "notify": notified.append, "on_pause": human}
    wf = {"name": "protected-prices", "steps": [
        {"goto": fx.url(cat.url(p5))},
        {"click": "Add to cart", "expect": {"url": "/api/v1/cart", "method": "POST", "status": 201}},
        {"goto": fx.url("/lab/protected/prices", "lab")},
        {"assert": {"text": "Protected content: prices"}},
        {"goto": fx.url("/lab/protected/stock", "lab")},
        {"assert": {"text": "Protected content: stock"}},
        {"goto": fx.url("/cart")},
        {"assert": {"text": p5["name"]}}]}
    with t.session("hitl", hitl=hitl) as s:
        res = WorkflowRunner(wf, s, state_path=t.out / "state.json").run()
        sid = s.id
        summary = s.finish()
    t.check(res["ok"] and [x["status"] for x in res["steps"]] == ["done"] * 8, "all 8 workflow steps done in one session",
            steps=[(x["id"], x["status"], x.get("error")) for x in res["steps"]])
    t.check(len(notified) == 1 and notified[0]["kind"] == "captcha" and notified[0]["vendor"] == "cloudflare" and
            notified[0]["url"].endswith("/lab/protected/prices") and Path(notified[0]["screenshot"]).exists(),
            f"operator notified once: {notified and notified[0]['kind']} by {notified and notified[0]['vendor']} + screenshot")
    t.check(seen_marker and seen_marker[0].endswith("/lab/protected/prices") and not (Path(summary["out_dir"]) / "PAUSED.json").exists(),
            "PAUSED.json marker present while paused, removed after resume")
    tl = read_jsonl(summary["paths"]["timeline"])
    kinds = [e["kind"] for e in tl if e["kind"] in ("blocked", "unblocked")]
    unb = next((e for e in tl if e["kind"] == "unblocked"), {})
    t.check(kinds == ["blocked", "unblocked"] and unb.get("how") == "solved", f"timeline: blocked → unblocked ({unb.get('how')}, "
            f"waited {unb.get('waited_s')}s)")
    t.check(fx.state.captcha_solves == 1, "challenge solved once; the 2nd protected page reused the clearance cookie")
    adds = [e for e in fx.state.events if e.get("kind") == "cart_add"]
    t.check(len(adds) == 1, "steps before the pause were not repeated (1 add-to-cart on the server)")
    rep = read_json(summary["paths"]["report_json"])
    blocked = [a for a in rep["actions"] if a.get("blocked")]
    t.check(len(blocked) == 1 and blocked[0]["ok"] and rep["session"]["id"] == sid,
            "the blocked goto is marked, succeeded after the solve, same session id throughout")
    with t.session("hitl_fail", hitl="fail", strict=False) as s:
        t0 = time.monotonic()
        r = s.goto(fx.url("/lab/protected/catalog", "lab"))
        el = time.monotonic() - t0
    t.check(not r["ok"] and r["error_kind"] == "blocked" and "captcha" in r["error"] and el < 15,
            f"hitl='fail': stops at once with BlockedError ({el:.1f}s)")
    with t.session("hitl_timeout", hitl={"mode": "wait", "timeout_s": 2, "poll_s": 0.2, "handoff": False, "bell": False},
                   strict=False) as s:
        t0 = time.monotonic()
        r = s.goto(fx.url("/lab/protected/archive", "lab"))
        el = time.monotonic() - t0
        out_dir = s.out_dir
    t.check(not r["ok"] and "not solved within 2s" in r["error"] and 2 <= el < 20 and not (out_dir / "PAUSED.json").exists(),
            f"nobody solves it: gives up after the timeout with a clear error ({el:.1f}s)")
    t.metric("hitl_waited_s", unb.get("waited_s"))


# ═══════════════════════════════════════════════════════════════════════
# Part 38 — guardrails: loop prevention & budgets
# ═══════════════════════════════════════════════════════════════════════
@selftest(38, "Unsolvable target, no-op loops, step/time budgets: execution stops by itself with a clear reason")
def t_guardrails(t: T) -> None:
    fx = t.fx
    with t.session("unsolvable", strict=False, retry={"max_attempts": 3, "backoff_ms": 150}) as s:
        s.goto(fx.url("/"))
        t0 = time.monotonic()
        r = s.click("Launch the rocket")
        el = time.monotonic() - t0
    t.check(not r["ok"] and r["attempts"] == 3 and r["error_kind"] == "not_found" and r.get("hint") and r.get("candidates"),
            f"unsolvable click: 3 attempts, then 'not_found' + hint + {len(r.get('candidates') or [])} candidates ({el:.1f}s)")
    decisions: list = []

    def stubborn(goal, obs, history):
        decisions.append(len(history))
        return {"tool": "click", "args": {"target": "Launch the rocket"}}
    t0 = time.monotonic()
    res = run_agent_loop("launch the rocket", stubborn, start_url=fx.url("/"), max_steps=50, profile=fx.profile(),
                         hitl="off", out_dir=t.out / "agent", retry={"max_attempts": 1})
    el = time.monotonic() - t0
    last = res["history"][-1]["result"]
    t.check(res["steps"] == 4 and last.get("error") == "guardrail_violation" and "already failed 3 times" in last.get("message", ""),
            f"stubborn agent stopped after {res['steps']} of 50 allowed steps: {last.get('message')!r} ({el:.1f}s)")
    with t.session("noop_loop", strict=False) as s:
        s.goto(fx.url("/lab/steps?run=noop", "lab"))
        clicks, reason = 0, None
        for _ in range(30):
            try:
                s.click("Step 1")
                clicks += 1
            except GuardrailViolation as exc:
                reason = str(exc)
                break
    t.check(reason and "loop detected" in reason and clicks <= 8, f"no-op loop detected after {clicks} clicks: {reason!r}")
    with t.session("step_budget", strict=False, guardrails={"max_actions": 5}) as s:
        done, reason = 0, None
        try:
            for i in range(1, 20):
                s.goto(fx.url(f"/catalogue/page-{i}.html"))
                done += 1
        except GuardrailViolation as exc:
            reason = str(exc)
    t.check(done == 5 and reason and "action budget exhausted" in reason, f"step budget: stopped after {done} actions ({reason!r})")
    with t.session("time_budget", strict=False, guardrails={"max_duration_s": 3}) as s:
        t0, reason = time.monotonic(), None
        try:
            for _ in range(200):
                s.goto(fx.url("/lab/steps?run=time", "lab"))
        except GuardrailViolation as exc:
            reason = str(exc)
        el = time.monotonic() - t0
    t.check(reason and "time budget exhausted" in reason and el < 15, f"time budget: stopped after {el:.1f}s ({reason!r})")
    cfg = {"name": "doomed", "profile": {"host_map": fx.host_map(), "no_proxy_server": True}, "hitl": "off",
           "retry": {"max_attempts": 1}, "guardrails": {"max_consecutive_failures": 3},
           "steps": [{"goto": fx.url("/")}] + [{"click": f"Imaginary button {i}", "optional": True} for i in range(1, 9)]}
    write_json(t.out / "doomed.json", cfg)
    out = t.out / "doomed_run"
    t0 = time.monotonic()
    proc = subprocess.run(self_command() + ["run", str(t.out / "doomed.json"), "--out", str(out)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=child_env(), timeout=300)
    el = time.monotonic() - t0
    printed = json.loads(proc.stdout)
    summ = read_json(out / "run_summary.json")
    t.check(proc.returncode == 2 and printed.get("stopped_by") == "guardrail" and "consecutive failed actions" in printed["reason"]
            and summ["run"]["status"] == "stopped", f"CLI workflow: 3 failures in a row → stopped with reason "
            f"{printed.get('reason')!r} ({el:.1f}s)")
    st = read_json(out / "state.json")
    t.check([x["status"] for x in st["steps"]] == ["done"] + ["skipped"] * 3 + ["failed"] + ["pending"] * 4 and
            st["steps"][4].get("error") == "guardrail", "checkpoint: 3 failed clicks, the 4th stopped by the guardrail, "
            "the remaining 4 steps never executed")
    log = read_jsonl(out / "session" / "log.jsonl")
    t.check(any(e["event"] == "guardrail" for e in log), "guardrail stop is in log.jsonl")
    t.metric("agent_steps", res["steps"])


# ═══════════════════════════════════════════════════════════════════════
# Part 39 — Postman / OpenAPI / client export
# ═══════════════════════════════════════════════════════════════════════
@selftest(39, "Captured workflow → Postman collection replays ≥90% (built-in + real Newman); OpenAPI + Python client + curl work")
def t_export(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    p19 = cat.by_id[19]
    with t.session("capture", strict=False) as s:
        s.goto(fx.url("/"))
        s.fill("Search products", "vase", submit=True)
        s.goto(fx.url(cat.url(p19)))
        s.click("Add to cart", expect={"url": "/api/v1/cart", "method": "POST", "status": 201})
        s.click("Save to wishlist", expect={"url": "/graphql", "method": "POST"})
        s.goto(fx.url("/cart"))
        s.fill(f"Quantity for {p19['name']}", "3", expect={"url": "/api/v1/cart/", "method": "PATCH"})
        s.goto(fx.url("/deals"))
        s.click("Load more deals", expect={"url": "/api/v1/deals?page=2"})
        s.evaluate("""async () => { const r = await fetch('/api/v1/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify({username: 'alice', password: 'wonderland'})}); const d = await r.json();
                       await (await fetch('/api/v1/me', {headers: {Authorization: 'Bearer ' + d.access_token}})).json(); }""")
        s.goto(fx.url("/checkout"))
        s.fill("Full name", "Grace Hopper")
        s.fill("Email", "grace@example.org")
        s.fill("Address", "1 Compiler Way")
        s.fill("City", "Arlington")
        s.check("I accept the terms and conditions")
        s.click("Place order", expect={"url": "/api/v1/orders", "method": "POST", "status": 201})
        summary = s.finish()
    exp_dir = t.out / "export"
    proc = subprocess.run(self_command() + ["export", summary["paths"]["har"], "--out", str(exp_dir), "--name", "ShopLab API"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env(), timeout=120)
    t.check(proc.returncode == 0, "CLI `export` wrote Postman, OpenAPI, Python client and curl files", err=proc.stderr[-800:])
    coll = read_json(exp_dir / "postman_collection.json")
    items = [it for folder in coll["item"] for it in folder.get("item", [folder])]
    methods = sorted({it["request"]["method"] for it in items})
    t.check(coll["info"]["schema"].endswith("v2.1.0/collection.json") and len(items) >= 12 and
            {"GET", "POST", "PATCH"} <= set(methods), f"Postman v2.1 collection: {len(items)} requests, methods {methods}")
    t.check(all(it["request"]["url"]["raw"].startswith("{{baseUrl") for it in items), "URLs use {{baseUrl}} variables")
    proxy = fx.start_proxy("postman")
    rep = replay_postman(coll, proxy=proxy.url)
    for r in rep["results"]:
        if not r["ok"]:
            t.note(f"replay miss: {r['method']} {r['url']} → {r['status']} (captured {r['expected']}) {r.get('error', '')}")
    t.check(rep["rate"] >= 0.9, f"built-in Postman-semantics replay: {rep['passed']}/{rep['total']} requests OK ({rep['rate']:.0%})")
    newman = shutil.which("newman")
    if newman:
        env = dict(os.environ, HTTP_PROXY=proxy.url, http_proxy=proxy.url, HTTPS_PROXY=proxy.url, https_proxy=proxy.url,
                   NO_PROXY="", no_proxy="")
        before = len(proxy.log)
        nres = subprocess.run([newman, "run", str(exp_dir / "postman_collection.json"), "--reporters", "json",
                               "--reporter-json-export", str(t.out / "newman.json"), "--timeout-request", "20000"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300)
        run = read_json(t.out / "newman.json")["run"]
        total = run["stats"]["requests"]["total"]
        failed_items = {f.get("source", {}).get("name") for f in run.get("failures", [])}
        ok_n = total - len(failed_items)
        via_newman = proxy.log[before:]  # every Newman request really went over the wire (through the proxy)
        newman_version = subprocess.run([newman, "--version"], capture_output=True, text=True, encoding="utf-8",
                                        errors="replace").stdout.strip()
        for f in run.get("failures", [])[:6]:
            t.note(f"newman failure: {f.get('source', {}).get('name')}: {f.get('error', {}).get('message')}")
        t.check(total == len(items) and ok_n / total >= 0.9 and len(via_newman) == total,
                f"real Newman {newman_version}: "
                f"{ok_n}/{total} requests pass their status test ({ok_n / max(1, total):.0%})", rc=nres.returncode,
                err=nres.stderr[-600:])
        t.metric("newman", f"{ok_n}/{total}")
    else:
        t.note("newman not installed (npm i -g newman) — real-Postman check skipped, built-in replay used")
    spec = read_json(exp_dir / "openapi.json")
    eps = read_json(summary["paths"]["endpoints"])
    ops = [(path, m, op) for path, item in spec["paths"].items() for m, op in item.items()]
    t.check(spec["openapi"].startswith("3.") and len(ops) == len(eps) and all(op["responses"] and op["operationId"] for _p, _m, op in ops),
            f"OpenAPI {spec['openapi']}: {len(ops)} operations = {len(eps)} discovered endpoints, each with responses")
    for path, _m, op in ops:
        declared = {p["name"] for p in op["parameters"] if p["in"] == "path"}
        if set(re.findall(r"\{(\w+)\}", path.split("#")[0])) != declared:
            t.check(False, f"path params of {path} declared", declared=sorted(declared))
    posted = [op for _p, m, op in ops if m in ("post", "patch") and "requestBody" in op]
    t.check(len(posted) >= 3, f"{len(posted)} write operations carry a requestBody schema")
    try:
        import yaml  # optional: proves the dependency-free YAML emitter is valid YAML
        t.check(yaml.safe_load((exp_dir / "openapi.yaml").read_text(encoding="utf-8")) == spec, "openapi.yaml parses to the same spec")
    except ImportError:
        t.note("PyYAML not installed — YAML round-trip check skipped")
    client_code = f"""
import json, sys
sys.path.insert(0, {str(exp_dir)!r})
from api_client import ApiClient
c = ApiClient(min_interval_s=0.05)
out = {{"featured": len(c.get_api_v1_products(featured=1, limit=3)["items"]),
       "search": c.get_api_v1_search(q="lamp")["total"],
       "deals_p2": len(c.get_api_v1_deals(page=2)["items"]),
       "pages": sum(1 for _ in c.iterate_pages(max_pages=3))}}
print(json.dumps(out))
"""
    env = dict(os.environ, HTTP_PROXY=proxy.url, http_proxy=proxy.url, NO_PROXY="", no_proxy="")
    cres = subprocess.run([sys.executable, "-c", client_code], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    try:
        got = json.loads(cres.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        got = {}
    t.check(got.get("featured") == 3 and got.get("search", 0) > 0 and got.get("deals_p2", 0) > 0 and got.get("pages", 0) >= 2,
            f"generated Python client works against the live API: {got}", err=cres.stderr[-800:])
    curl = shutil.which("curl")
    sh = shutil.which("sh")
    script = (exp_dir / "requests.sh").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in script.split("\n", 1)[1].split("\n\n") if ln.strip().startswith("curl ")]
    t.check(len(lines) == len(items), f"requests.sh holds {len(lines)} curl commands")
    if curl and sh:
        codes = []
        for ln in lines:
            r = subprocess.run([sh, "-c", ln + " -s -o /dev/null -w '%{http_code}'"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=dict(env, HTTPS_PROXY="", https_proxy=""), timeout=30)
            codes.append(r.stdout.strip())
        good = sum(1 for c in codes if c[:1] in ("2", "3"))
        t.check(good / len(codes) >= 0.9, f"curl replay: {good}/{len(codes)} commands succeed", codes=codes)
    t.metric("postman_items", len(items))
    t.metric("replay_rate", rep["rate"])


# ═══════════════════════════════════════════════════════════════════════
# Part 40 — target-site load & rate respect
# ═══════════════════════════════════════════════════════════════════════
@selftest(40, "429 + Retry-After is obeyed over a too-fast configured delay: 0 requests inside the ban, per-host cap holds")
def t_rate_limits(t: T) -> None:
    fx = t.fx
    rate = fx.state.rate
    retry_after = fx.state.rate_retry_after
    with t.session("browser", rate_limit={"min_interval_s": 0.2}, retry={"max_attempts": 6, "backoff_ms": 100}) as s:
        t0 = time.monotonic()
        results = [s.goto(fx.url(f"/lab/rl/page/{i}", "lab")) for i in range(1, 9)]
        el = time.monotonic() - t0
        stats = s.limiter.stats().get(f"lab.agenttrace.test", {})
    log = [x for x in rate["log"] if x["path"].startswith("/lab/rl/page/")]
    t.note(" ".join(f"{x['path'].rsplit('/', 1)[-1]}:{x['status']}" for x in log))
    t.check(all(r["ok"] and r["status"] == 200 for r in results), f"all 8 listing pages loaded (in {el:.1f}s)")
    t.check(rate["violations"] == 0, "0 requests sent while the server's Retry-After ban was active")
    gaps = [round(b["t"] - a["t"], 2) for a, b in zip(log, log[1:]) if a["status"] == 429]
    t.check(gaps and all(g >= retry_after - 0.05 for g in gaps),
            f"after each of the {len(gaps)} 429s the next request waited ≥ Retry-After={retry_after}s: {gaps}")
    later = [x["status"] for x in log[len(log) // 2:]]
    t.check(429 not in later and stats.get("learned_floor_s", 0) >= 1.2,
            f"pace learned: no 429 in the second half, learned per-host floor {stats.get('learned_floor_s')}s ≥ server's 1.2s")
    proxy = fx.start_proxy("api")
    before = rate["violations"]
    client = HttpClient(limiter=RateLimiter(min_interval_s=0.0), proxy=proxy.url, max_retries=6)
    t0 = time.monotonic()
    got = [client.get(fx.url(f"/lab/api/rl/items?page={i}", "lab")) for i in range(1, 7)]
    t.check(all(r.status == 200 for r in got) and rate["violations"] == before,
            f"HttpClient: 6/6 API pages OK, 0 ban violations, {sum(r.attempts - 1 for r in got)} polite retries "
            f"({time.monotonic() - t0:.1f}s)")
    shared = RateLimiter(max_concurrency_per_domain=2)
    fx.state.slow_peak = 0
    pool = TaskPool(max_workers=6)
    pool.run([(f"slow{i}", lambda: HttpClient(limiter=shared, proxy=proxy.url).get(fx.url("/lab/api/slow?ms=700", "lab")))
              for i in range(6)])
    capped = fx.state.slow_peak
    fx.state.slow_peak = 0
    TaskPool(max_workers=6).run([(f"free{i}", lambda: HttpClient(limiter=RateLimiter(max_concurrency_per_domain=6),
                                                                   proxy=proxy.url).get(fx.url("/lab/api/slow?ms=700", "lab")))
                                 for i in range(6)])
    t.check(capped == 2 and shared.stats()["lab.agenttrace.test"]["peak_concurrency"] == 2 and fx.state.slow_peak > 2,
            f"per-domain concurrency cap: server saw at most {capped} parallel requests (uncapped control: {fx.state.slow_peak})")
    when = time.time()
    http_date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(when + 30))
    t.check(abs(parse_retry_after(http_date, now=when) - 30) < 1.5 and parse_retry_after("7") == 7,
            "Retry-After parsed in both forms (seconds and HTTP-date)")
    try:
        RateLimiter(max_retry_after_s=60).note_response("slow.example", 429, {"Retry-After": "3600"})
        refused = None
    except RateLimitError as exc:
        refused = exc
    t.check(refused is not None and refused.hint, "a 1-hour Retry-After stops the run with a clear hint instead of hammering")
    t.metric("violations", rate["violations"])
    t.metric("retry_after_gaps", gaps)


# ═══════════════════════════════════════════════════════════════════════
# Part 41 — SSE / streaming capture
# ═══════════════════════════════════════════════════════════════════════
@selftest(41, "SSE + chunked stream: every event/chunk captured in sequence with real-time timestamps (HAR + streams.json)")
def t_sse_streams(t: T) -> None:
    fx = t.fx
    with t.session("sse") as s:
        t0 = time.monotonic()
        s.goto(fx.url("/lab/sse", "lab"))
        goto_s = time.monotonic() - t0
        s.wait_for({"text": "stream closed"}, timeout_ms=20_000)
        s.wait_for({"function": "() => document.getElementById('chunks').textContent.length > 20"}, timeout_ms=20_000)
        shown = s.evaluate("() => Array.from(document.querySelectorAll('#ticks li')).map(li => li.textContent)")
        streams = s.streams()
        summary = s.finish()
    sse = next((x for x in streams if x["type"] == "sse"), None)
    t.check(sse is not None and "/lab/stream/sse" in sse["url"], "SSE stream recognised")
    ev = sse["events"]
    names = [e["event"] for e in ev]
    t.check(len(ev) == 11 and names[:10] == ["message", "update", "price"] * 3 + ["message"] and names[10] == "end",
            f"{len(ev)} events in order: {names}")
    data = [json.loads(e["data"]) for e in ev[:10]]
    t.check([d["n"] for d in data] == list(range(1, 11)) and [e["id"] for e in ev[:10]] == [str(i) for i in range(1, 11)],
            "event ids and payload sequence 1..10 intact")
    t.check([e["seq"] for e in ev] == list(range(1, 12)), "capture sequence numbers 1..11")
    ts = [parse_iso(e["ts"]).timestamp() for e in ev]
    gaps = [round(b - a, 3) for a, b in zip(ts, ts[1:10])]
    lag = [round(ts[i] - data[i]["server_ts"], 3) for i in range(10)]
    t.check(all(g >= 0.06 for g in gaps) and all(-0.05 <= x < 1.0 for x in lag),
            f"timestamps are real-time: gaps {min(gaps)}–{max(gaps)}s (server sends every 0.12s), lag vs server clock ≤ {max(lag)}s")
    t.check(len(shown) < 11 and all(e["data"] for e in ev), f"captured all 11 events although the page only listened to "
            f"{len(shown)} of them")
    ch = next((x for x in streams if x["type"] == "chunked"), None)
    parts = "".join(c["data"] for c in (ch or {}).get("chunks", [])).split(";")
    t.check(ch and [p.split("|")[0] for p in parts if p] == [f"chunk-{i}" for i in range(1, 6)],
            f"chunked fetch: {len(ch['chunks']) if ch else 0} network chunks → chunk-1..chunk-5 in order")
    cts = [parse_iso(c["ts"]).timestamp() for c in ch["chunks"]]
    t.check(all(b >= a for a, b in zip(cts, cts[1:])) and cts[-1] - cts[0] >= 0.25, "chunk timestamps increase over the stream")
    saved = read_json(summary["paths"]["streams"])
    har_sse = [e for e in load_har(summary["paths"]["har"])["log"]["entries"] if "/lab/stream/sse" in e["request"]["url"]]
    t.check(len(saved) == 2 and har_sse and len(har_sse[0]["_agenttrace"]["stream"]["events"]) == 11,
            "streams.json + the HAR entry carry the full event list")
    t.check(goto_s < 8, f"page load did not hang on the open stream ({goto_s:.1f}s)")
    t.metric("sse_events", len(ev))
    t.metric("chunks", len(ch["chunks"]))


# ═══════════════════════════════════════════════════════════════════════
# Part 42 — WebSocket capture
# ═══════════════════════════════════════════════════════════════════════
@selftest(42, "WebSocket: handshake, lifecycle and ≥10 messages each way with direction, timestamp and connection id")
def t_websocket(t: T) -> None:
    fx = t.fx
    with t.session("ws") as s:
        s.goto(fx.url("/lab/ws", "lab"))
        s.wait_for({"function": "() => document.getElementById('status').textContent === 'closed'"}, timeout_ms=15_000)
        s.settle()
        conns = s.websockets()
        summary = s.finish()
    t.check(len(conns) == 1 and conns[0]["url"].startswith("ws://127.0.0.1:") and "/ws/chat?room=lab" in conns[0]["url"],
            f"1 connection {conns and conns[0]['id']} → {conns and conns[0]['url']}")
    c = conns[0]
    hs = c.get("handshake") or {}
    req_h = {k.lower(): v for k, v in (hs.get("request_headers") or {}).items()}
    resp_h = {k.lower(): v for k, v in (hs.get("response_headers") or {}).items()}
    want_accept = base64.b64encode(hashlib.sha1((req_h.get("sec-websocket-key", "") +
                                                 "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    t.check(hs.get("status") == 101 and resp_h.get("sec-websocket-accept") == want_accept,
            "handshake captured: 101 Switching Protocols, Sec-WebSocket-Accept matches the key", handshake=hs)
    sent = [m for m in c["messages"] if m["direction"] == "sent"]
    recv = [m for m in c["messages"] if m["direction"] == "received"]
    t.check(len(sent) == 10 and len(recv) == 13, f"{len(sent)} client→server and {len(recv)} server→client messages")
    t.check(all(m["conn_id"] == c["id"] and m["ts"] and m["seq"] for m in c["messages"]),
            "every message has connection id, direction, timestamp and sequence number")
    srv = next(x for x in fx.ws_log if x["path"].startswith("/ws/chat"))
    srv_in = [m["data"] for m in srv["messages"] if m["dir"] == "client->server"]
    srv_out = [m["data"] for m in srv["messages"] if m["dir"] == "server->client"]
    t.check([m["data"] for m in sent] == srv_in, "sent messages match what the server received, in order")
    t.check([json.loads(m["data"]) for m in recv] == srv_out, "received messages match what the server sent, in order")
    ts = {m["seq"]: parse_iso(m["ts"]).timestamp() for m in c["messages"]}
    echo_after = all(ts[r["seq"]] >= ts[s_["seq"]] for s_, r in zip(sent, [m for m in recv if json.loads(m["data"]).get("type") == "echo"]))
    t.check(echo_after and all(a["seq"] < b["seq"] for a, b in zip(c["messages"], c["messages"][1:])),
            "each echo is timestamped after the message it answers")
    t.check(c["opened"] and c["closed"] and parse_iso(c["opened"]) <= parse_iso(c["closed"]), "lifecycle: opened → closed")
    har_ws = [e for e in load_har(summary["paths"]["har"])["log"]["entries"] if e.get("_resourceType") == "websocket"]
    msgs = har_ws[0]["_webSocketMessages"] if har_ws else []
    t.check(len(har_ws) == 1 and har_ws[0]["response"]["status"] == 101 and len(msgs) == 23 and
            sum(1 for m in msgs if m["type"] == "send") == 10, "HAR has a DevTools-style websocket entry with all 23 messages")
    t.check(read_json(summary["paths"]["websockets"])[0]["id"] == c["id"], "websockets.json written")
    t.metric("sent", len(sent))
    t.metric("received", len(recv))


# ═══════════════════════════════════════════════════════════════════════
# Part 43 — interception, mocking & controlled responses
# ═══════════════════════════════════════════════════════════════════════
@selftest(43, "Mock / block / modify: the app uses the injected data; report shows original request + injected response")
def t_mocking(t: T) -> None:
    fx = t.fx
    since = time.time()
    mocked = {"items": [{"id": 9, "name": "Mocked pick X"}, {"id": 8, "name": "Mocked pick Y"}]}
    with t.session("mock") as s:
        s.mock("/lab/api/recommendations", json_body=mocked, headers={"X-Mocked-By": "agenttrace"})
        s.block("/lab/api/banner")
        s.goto(fx.url("/lab/mock", "lab"))
        s.wait_for({"text": "Mocked pick Y"})
        recs_text = s.evaluate("() => Array.from(document.querySelectorAll('.rec')).map(x => x.textContent)")
        banner = s.evaluate("() => document.getElementById('banner').textContent")
        s.modify_response("/api/v1/products?featured", transform=lambda d: {**d, "items": d["items"][:2]})
        s.modify_request("/api/v1/categories", headers={"X-Debug": "on"})
        s.goto(fx.url("/"))
        s.wait_for({"function": "() => document.querySelectorAll('#featured .product_pod, #featured article').length > 0"})
        cards = s.evaluate("() => document.querySelectorAll('#featured .product_pod, #featured article').length")
        summary = s.finish()
    t.check(recs_text == ["Mocked pick X", "Mocked pick Y"], f"page rendered the mocked recommendations {recs_text}")
    t.check(not fx.requests(path_prefix="/lab/api/recommendations", since=since), "the real API was never called")
    t.check(banner == "banner unavailable" and not fx.requests(path_prefix="/lab/api/banner", since=since),
            "blocked banner request → the app's error path ran; nothing reached the server")
    real = fx.requests(path_prefix="/api/v1/products", since=since)
    t.check(cards == 2 and real, f"modify_response: real API called ({len(real)}×), app received only {cards} of 8 products")
    cats = fx.requests(path_prefix="/api/v1/categories", since=since)
    t.check(cats and cats[-1]["headers"].get("x-debug") == "on", "modify_request: the server received the added header")
    entries = load_har(summary["paths"]["har"])["log"]["entries"]
    rec_e = next(e for e in entries if "/lab/api/recommendations" in e["request"]["url"])
    meta = rec_e["_agenttrace"].get("mocked") or {}
    t.check(rec_e["request"]["method"] == "GET" and json.loads(rec_e["response"]["content"]["text"]) == mocked and
            meta.get("action") == "mock" and meta.get("injected_response", {}).get("status") == 200,
            "HAR: original request + injected response + mock marker on the same entry")
    feat = next(e for e in entries if "/api/v1/products?featured" in e["request"]["url"])
    fmeta = feat["_agenttrace"].get("mocked") or {}
    orig = json.loads(fmeta.get("original_response", {}).get("body") or "{}")
    t.check(len(orig.get("items", [])) == 8 and len(json.loads(feat["response"]["content"]["text"])["items"]) == 2,
            "HAR keeps both the server's original response (8 items) and what the app got (2)")
    ban = next(e for e in entries if "/lab/api/banner" in e["request"]["url"])
    t.check("blocked_by_client" in (ban["_agenttrace"].get("failure") or "").lower() and ban["_agenttrace"]["mocked"]["action"] == "block",
            "blocked request recorded as blocked (not silently missing)")
    rep = read_json(summary["paths"]["report_json"])
    kinds = sorted(r["kind"] for r in rep.get("interceptions", []))
    t.check(kinds == ["block", "mock", "modify_request", "modify_response"] and
            all(r["hits"] >= 1 and r["requests"][0].get("url") for r in rep["interceptions"]),
            "report.json lists every interception rule with the requests it touched")
    t.check(Path(summary["paths"]["interceptions"]).exists(), "interceptions.json written")
    t.metric("rules", len(kinds))


# ═══════════════════════════════════════════════════════════════════════
# Part 44 — body integrity & decoding
# ═══════════════════════════════════════════════════════════════════════
@selftest(44, "gzip JSON, PNG, multipart upload, chunked text: byte-exact; truncated bodies fail validation with a clear reason")
def t_body_integrity(t: T) -> None:
    fx = t.fx
    pay = integrity_payloads()
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    with t.session("integrity", body_policy="all") as s:
        s.goto(fx.url("/lab/integrity", "lab"))
        s.wait_for({"function": "() => document.getElementById('out').textContent.length > 10"}, timeout_ms=20_000)
        page_out = json.loads(s.evaluate("() => document.getElementById('out').textContent"))
        s.settle()
        problems = s.integrity("/lab/", problems_only=True)
        summary = s.finish()
    t.note(f"page saw: {page_out}")
    good = [{"url": "/lab/api/data-gzip.json", "response_sha256": sha(pay["json"]), "body_complete": True,
             "response_headers": {"content-encoding": "gzip"}},
            {"url": "/lab/api/image.png", "response_sha256": sha(pay["png"]), "response_size": len(pay["png"]),
             "body_complete": True},
            {"url": "/lab/upload", "method": "POST", "request_form": {"title": "quarterly report"}, "body_complete": True,
             "request_files": {"file": {"sha256": sha(pay["upload"]), "size": len(pay["upload"]), "filename": "payload.bin"}}},
            {"url": "/lab/api/chunked.txt", "response_sha256": sha(pay["chunked"]), "body_complete": True}]
    for label, recs in (("live capture", har_to_records(summary["paths"]["har"])),):
        res = validate_expectations(recs, good)
        for c in res["checks"]:
            t.check(c["ok"], f"{label}: {c['expectation']} byte-exact", failures=c["failures"])
    srv = fx.state.uploads[-1]
    t.check(srv["file_sha256"] == sha(pay["upload"]) and srv["title"] == "quarterly report",
            "server received exactly the multipart file the HAR shows")
    bad = validate_expectations(har_to_records(summary["paths"]["har"]),
                                [{"url": "/lab/api/truncated.bin", "body_complete": True, "min_count": 1},
                                 {"url": "/lab/api/truncated.json", "body_complete": True}])
    for c in bad["checks"]:
        t.note(f"{c['expectation']}: {c['failures']}")
    t.check(not bad["checks"][0]["ok"] and any("truncated" in f or "cut off" in f for f in bad["checks"][0]["failures"]),
            "truncated binary transfer → validation fails and says 'truncated'")
    t.check(not bad["checks"][1]["ok"] and any("invalid JSON" in f for f in bad["checks"][1]["failures"]),
            "half-sent JSON → validation fails with 'invalid JSON … truncated or corrupt'")
    flagged = sorted({p["url"].rsplit("/", 1)[-1] for p in problems})
    t.check(flagged == ["truncated.bin", "truncated.json"], f"integrity scan flags exactly the 2 broken responses: {flagged}")
    ann = [e for e in load_har(summary["paths"]["har"])["log"]["entries"] if e["_agenttrace"].get("integrity")]
    t.check(sorted(e["request"]["url"].rsplit("/", 1)[-1] for e in ann) == ["truncated.bin", "truncated.json"],
            "HAR entries of the broken responses carry an integrity note")
    t.metric("byte_exact", len(good))
    t.metric("flagged", len(flagged))


# ═══════════════════════════════════════════════════════════════════════
# Part 45 — tabs, popups, windows & frames
# ═══════════════════════════════════════════════════════════════════════
@selftest(45, "Popup + new tab + nested iframes: lifecycle tracked and every request tied to its page/frame")
def t_tabs_frames(t: T) -> None:
    fx = t.fx
    with t.session("frames") as s:
        s.goto(fx.url("/lab/frames", "lab"))
        s.wait_for({"response": "/lab/api/frame-b-data"})
        opened = s.click("Open popup")
        s.switch_to("latest")
        s.wait_for({"text": "Popup window"})
        s.click("Close")
        tab = s.click("Open report in new tab")
        s.switch_to("latest")
        s.wait_for({"text": "Report"})
        s.close_page()
        pages = s.pages()
        summary = s.finish()
    info = read_json(summary["paths"]["pages"])
    by_id = {p["id"]: p for p in info["pages"]}
    t.check(sorted(by_id) == ["p1", "p2", "p3"], f"3 pages tracked: {sorted(by_id)}")
    live = {p["page_id"]: p for p in pages}
    t.check(by_id["p2"]["opener"] == "p1" and by_id["p2"]["kind"] == "popup" and by_id["p2"]["closed"] and
            by_id["p3"]["opener"] == "p1" and by_id["p3"]["closed"] and not live["p1"]["closed"] and live["p1"]["active"],
            "popup p2 and tab p3: opener p1, both closed during the run; p1 stayed open and active")
    t.check(by_id["p2"]["urls"] == [fx.url("/lab/popup", "lab")] and by_id["p3"]["urls"] == [fx.url("/lab/tab", "lab")],
            "each window's URL history recorded")
    t.check(opened.get("new_pages") == ["p2"] and tab.get("new_pages") == ["p3"], "the clicks that opened them are linked")
    frames = {f["id"]: f for f in by_id["p1"]["frames"]}
    fa = next((f for f in frames.values() if f["name"] == "frame-a"), None)
    fb = next((f for f in frames.values() if f["name"] == "frame-b"), None)
    t.check(fa and fb and fa["parent"] == "p1.f0" and fb["parent"] == fa["id"],
            f"frame tree: p1.f0 → {fa and fa['id']} (frame-a) → {fb and fb['id']} (frame-b)")
    recs = har_to_records(summary["paths"]["har"])
    where = {name: next(((r.page_id, r.frame_id) for r in recs if r.url.endswith(f"/lab/api/{name}")), None)
             for name in ("main-data", "frame-a-data", "frame-b-data", "popup-data", "tab-data")}
    t.note(f"attribution: {where}")
    t.check(where == {"main-data": ("p1", "p1.f0"), "frame-a-data": ("p1", fa["id"]), "frame-b-data": ("p1", fb["id"]),
                      "popup-data": ("p2", "p2.f0"), "tab-data": ("p3", "p3.f0")},
            "every API call is attributed to the right page and frame")
    tl = read_jsonl(summary["paths"]["timeline"])
    opened_ev = [(e["page_id"], e.get("opener")) for e in tl if e["kind"] == "page_opened"]
    closed_ev = [e["page_id"] for e in tl if e["kind"] == "page_closed"]
    t.check(("p2", "p1") in opened_ev and ("p3", "p1") in opened_ev and closed_ev[:2] == ["p2", "p3"],
            f"timeline: opened {opened_ev}, closed {closed_ev}")
    t.check(sum(1 for e in tl if e["kind"] == "frame_attached" and e["page_id"] == "p1") >= 2, "frame_attached events logged")
    t.metric("pages", len(by_id))
    t.metric("frames_p1", len(frames))


# ═══════════════════════════════════════════════════════════════════════
# Part 46 — complete browser storage state
# ═══════════════════════════════════════════════════════════════════════
@selftest(46, "Cookie + localStorage + IndexedDB (+sessionStorage) login survives a browser restart via exported state")
def t_storage_state(t: T) -> None:
    fx = t.fx
    vault = fx.url("/", "vault")
    state_file = t.out / "vault_state.json"
    with t.session("login") as s:
        s.goto(vault)
        s.fill("user", "keeper")
        s.fill("password", "open-sesame")
        s.click("Log in")
        s.wait_for({"text": "Logged in as keeper"})
        nonce = s.evaluate("() => sessionStorage.getItem('vault_tab')")
        snap = s.storage()
        info = s.save_state(state_file)
    idb = {(d["name"], st["name"]): st for d in snap.get("indexed_db", []) for st in d["stores"]}
    t.check(any(c["name"] == "vault_sid" for c in snap["cookies"]) and "vault_profile" in snap["local_storage"] and
            ("vault", "tokens") in idb and "access" in idb[("vault", "tokens")]["keys"] and "vault_tab" in snap["session_storage"],
            "storage() sees the cookie, localStorage, IndexedDB (vault/tokens/access) and sessionStorage")
    t.check(info["cookies"] >= 1 and info["local_storage_keys"] >= 1 and info["indexed_db"] >= 1 and info["session_storage_origins"],
            f"exported: {info['cookies']} cookies, {info['local_storage_keys']} localStorage keys, "
            f"{info['indexed_db']} IndexedDB databases, sessionStorage for {info['session_storage_origins']}")

    def open_vault(name: str, state: Any) -> tuple:
        with t.session(name, storage_state=state) as s2:
            s2.goto(vault)
            s2.wait_for({"function": "() => document.getElementById('state').dataset.state !== 'unknown'"})
            return (s2.evaluate("() => document.getElementById('state').textContent"),
                    s2.evaluate("() => document.getElementById('tabinfo').textContent"))
    state_text, tab_text = open_vault("restored", str(state_file))
    t.check(state_text == "Logged in as keeper (gold)" and tab_text == f"tab nonce: {nonce}",
            f"fresh browser + exported state → '{state_text}', {tab_text}")
    full = read_json(state_file)

    def without(fn) -> str:
        data = json.loads(json.dumps(full))
        fn(data)
        path = t.out / f"partial_{len(list(t.out.glob('partial_*')))}.json"
        write_json(path, data)
        return str(path)
    no_idb = without(lambda d: [o.pop("indexedDB", None) for o in d.get("origins", [])])
    no_ls = without(lambda d: [o.update(localStorage=[]) for o in d.get("origins", [])])
    no_cookie = without(lambda d: d.update(cookies=[]))
    results = {"without IndexedDB": open_vault("no_idb", no_idb)[0], "without localStorage": open_vault("no_ls", no_ls)[0],
               "without cookies": open_vault("no_cookie", no_cookie)[0], "no state": open_vault("fresh", None)[0]}
    t.note(f"controls: {results}")
    t.check(results["without IndexedDB"] == "Logged out" and results["without localStorage"] == "Logged out" and
            results["without cookies"].startswith("Session invalid") and results["no state"] == "Logged out",
            "controls prove each store matters: dropping IndexedDB / localStorage / cookies breaks the login")
    t.metric("stores", ["cookie", "localStorage", "IndexedDB", "sessionStorage"])


# ═══════════════════════════════════════════════════════════════════════
# Part 47 — proxy & network profiles
# ═══════════════════════════════════════════════════════════════════════
@selftest(47, "Two sessions, two proxies/headers/UA/locale/timezone/throttle profiles at once: each goes out as configured, no leak")
def t_network_profiles(t: T) -> None:
    fx = t.fx
    pa, pb = fx.start_proxy("alpha"), fx.start_proxy("beta")
    base = {"host_map": fx.host_map(), "navigation_timeout_ms": 30_000, "action_timeout_ms": 10_000}
    profiles = {
        "alpha": {**base, "proxy": pa.url, "extra_headers": {"X-Team": "alpha"}, "user_agent": "Mozilla/5.0 AgentTraceAlpha/1.0",
                  "locale": "de-DE", "timezone_id": "Europe/Berlin"},
        "beta": {**base, "proxy": pb.url, "extra_headers": {"X-Team": "beta"}, "user_agent": "Mozilla/5.0 AgentTraceBeta/2.0",
                 "locale": "bn-BD", "timezone_id": "Asia/Dhaka", "throttle": {"latency_ms": 400, "download_kbps": 800}},
    }

    def job(name: str):
        def run() -> dict:
            with Session(profile=profiles[name], out_dir=t.out / name, name=name, hitl="off") as s:
                s.goto(fx.url("/"))
                s.goto(fx.url("/catalogue/page-2.html"))
                env = s.evaluate("() => ({lang: navigator.language, tz: Intl.DateTimeFormat().resolvedOptions().timeZone, "
                                 "ua: navigator.userAgent, hour: new Date(Date.UTC(2026, 0, 1, 12)).getHours()})")
                api = s.records("/api/v1/cart")
                summary = s.finish()
            return {"env": env, "api_ms": [r.duration_ms for r in api], "summary": summary}
        return (name, run)
    since = time.time()
    res = {r.name: r for r in TaskPool(max_workers=2).run([job("alpha"), job("beta")])}
    for name, r in res.items():
        t.check(r.ok, f"session {name} ran", error=(r.error or "")[:600])
    env = {n: r.value["env"] for n, r in res.items()}
    t.check(env["alpha"]["lang"] == "de-DE" and env["alpha"]["tz"] == "Europe/Berlin" and env["alpha"]["hour"] == 13 and
            env["beta"]["lang"] == "bn-BD" and env["beta"]["tz"] == "Asia/Dhaka" and env["beta"]["hour"] == 18,
            f"in-page environment: alpha {env['alpha']['lang']}/{env['alpha']['tz']}, beta {env['beta']['lang']}/{env['beta']['tz']}")
    for name, proxy, other in (("alpha", pa, "beta"), ("beta", pb, "alpha")):
        prof = profiles[name]
        log = proxy.log
        preflights = [x for x in log if x["method"] == "OPTIONS"]
        wrong = [x for x in log if x["headers"].get("user-agent") != prof["user_agent"] or (
            "x-team" not in x["headers"].get("access-control-request-headers", "") if x["method"] == "OPTIONS" else
            x["headers"].get("x-team") != name or not x["headers"].get("accept-language", "").startswith(prof["locale"]))]
        t.check(len(log) > 20 and not wrong, f"proxy {name}: {len(log)} requests, all with its UA, X-Team={name} and "
                f"Accept-Language {prof['locale']} ({len(preflights)} CORS preflights announce X-Team)",
                wrong=[(w["method"], w["url"], w["headers"].get("x-team")) for w in wrong[:3]])
    srv = [r for r in fx.requests(since=since) if r["host"].endswith("agenttrace.test")]
    tags = {}
    for r in srv:
        tag = r["headers"].get("x-proxy-tag", "none")
        team = r["headers"].get("x-team", "none")
        tags.setdefault(tag, set()).add(team)
    t.check(tags.get("alpha") == {"alpha"} and tags.get("beta") == {"beta"} and "none" not in tags,
            f"server side: every request came through its own session's proxy {({k: sorted(v) for k, v in tags.items()})}")
    fast = min(res["alpha"].value["api_ms"] or [0])
    slow = min(res["beta"].value["api_ms"] or [0])
    t.check(slow >= 250 and slow > 5 * max(fast, 1), f"throttle (400 ms latency) only on beta: fastest cart API "
            f"{fast:.0f} ms (alpha) vs {slow:.0f} ms (beta)")
    t.metric("alpha_requests", len(pa.log))
    t.metric("beta_requests", len(pb.log))


# ═══════════════════════════════════════════════════════════════════════
# Part 48 — artifact store, manifests, versioned history
# ═══════════════════════════════════════════════════════════════════════
@selftest(48, "Workflow run 5× (2 versions): every run's HAR/screenshot/log/report locatable; history rebuilt from manifests")
def t_artifact_store(t: T) -> None:
    fx = t.fx
    store = ArtifactStore(t.out / "store")
    v1 = {"name": "price-watch", "steps": [{"goto": fx.url("/catalogue/page-2.html")},
                                           {"extract_list": {}, "save_as": "products"},
                                           {"click": "next", "expect": {"url": "/catalogue/page-3.html"}}]}
    v2 = {**v1, "steps": v1["steps"] + [{"goto": fx.url("/deals")}]}
    runs = []
    for i in range(5):
        wf = v1 if i < 3 else v2
        h = store.start_run("price-watch", wf, tags=[f"run{i + 1}"])
        with h.session(profile=fx.profile(), hitl="off", evidence="important") as s:
            res = WorkflowRunner(wf, s, state_path=h.dir / "state.json").run()
            fp = fingerprint_session(s)
            summ = s.finish()
        man = h.finalize("done" if res["ok"] else "failed", fingerprint=fp, summary=summ["summary"])
        runs.append((h.run_id, h.version, man))
    ids = [r[0] for r in runs]
    t.check(len(set(ids)) == 5 and all(m["status"] == "done" for _i, _v, m in runs), f"5 unique run ids: {ids[0]} …")
    for run_id, version, _m in runs:
        found = {k: store.locate(run_id, k) for k in ("har", "screenshot", "log", "report", "api", "workflow")}
        t.check(all(found.values()) and all(Path(p).exists() for ps in found.values() for p in ps),
                f"{run_id}: " + ", ".join(f"{k}×{len(v)}" for k, v in found.items()))
        v = store.verify(run_id)
        t.check(v["ok"] and v["checked"] > 10, f"{run_id}: manifest hashes verify ({v['checked']} files)")
    hist = store.history("price-watch")
    versions = list(hist["versions"].items())
    t.check(hist["runs"] == 5 and len(versions) == 2 and [len(x[1]) for x in versions] == [3, 2] and
            [r[1] for r in hist["timeline"]] == ids, f"history from index: 5 runs, versions {[(k, len(v)) for k, v in versions]}")
    shot = store.locate(ids[0], "screenshot")[0]
    action = next(a for a in store.manifest(ids[0])["artifacts"] if a["kind"] == "screenshot").get("action_id")
    t.check(action and store.locate(ids[0], "screenshot", action_id=action), f"screenshot ↔ action link ({action})")
    Path(shot).write_bytes(Path(shot).read_bytes() + b"tamper")
    bad = store.verify(ids[0])
    t.check(not bad["ok"] and bad["problems"][0]["problem"] == "hash mismatch", "tampered artifact detected by verify()")
    cmp_same = store.compare(ids[0], ids[1])
    cmp_ver = store.compare(ids[2], ids[3])
    t.check(cmp_same["fingerprint"]["same"] and not cmp_ver["fingerprint"]["same"] and cmp_ver["fingerprint"]["step_changes"],
            "same-version runs compare identical; the v1→v2 change shows up as a step difference")
    t.metric("runs", 5)
    t.metric("versions", len(versions))


# ═══════════════════════════════════════════════════════════════════════
# Part 49 — deterministic fixtures & reproducible replay
# ═══════════════════════════════════════════════════════════════════════
@selftest(49, "Same fixture + workflow ×20: identical actions, network and validations; an intentional change shows only itself")
def t_deterministic(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    wf = {"name": "deterministic", "steps": [
        {"goto": fx.url("/catalogue/page-3.html")},
        {"click": "next", "expect": {"url": "/catalogue/page-4.html", "status": 200}},
        {"goto": fx.url(cat.url(cat.by_id[70]))},
        {"click": "Add to cart", "expect": {"url": "/api/v1/cart", "method": "POST", "status": 201, "response_has": ["total"]}},
        {"goto": fx.url("/cart"), "expect": {"url": "/api/v1/cart", "response_has": ["items"]}},
        {"goto": fx.url("/"), "expect": {"url": "/api/v1/products?featured", "response_has": ["items"]}}]}
    n = t.reps(20, 5)

    def one(name: str, **kw: Any) -> tuple:
        with Session(profile=fx.profile(), out_dir=t.out / name, name=name, hitl="off", deterministic=True, **kw) as s:
            res = WorkflowRunner(wf, s).run()
            fp = fingerprint_session(s)
            vals = [bool((a.get("validation") or {}).get("ok")) for a in s.actions() if a.get("validation") is not None]
            summ = s.finish()
        return res, fp, vals, summ
    base = one("run01", body_policy="all")
    hashes, orders, vals = [base[1]["hash"]], [[x["action"] for x in base[0]["steps"]]], [base[2]]
    for i in range(2, n + 1):
        res, fp, v, _s = one(f"run{i:02d}")
        hashes.append(fp["hash"])
        orders.append([x["action"] for x in res["steps"]])
        vals.append(v)
    t.check(len(set(hashes)) == 1, f"{n} runs → 1 distinct fingerprint ({hashes[0][:16]})", distinct=len(set(hashes)))
    t.check(len({tuple(o) for o in orders}) == 1 and all(v == [True] * 4 for v in vals),
            f"identical action sequence and 4/4 network validations passing in all {n} runs")
    fx.state.variant["api_change"] = True
    try:
        _r, changed, cv, _s = one("changed")
        _r2, replayed, rv, _s2 = one("replayed_from_har", replay_har=base[3]["paths"]["har"], replay_not_found="fallback")
    finally:
        fx.state.variant["api_change"] = False
    d = diff_fingerprints(base[1], changed)
    t.note(f"intentional change → added {d['network_added']} removed {d['network_removed']}")
    t.check(not d["same"] and not d["step_changes"] and cv == [True] * 4, "fixture change detected; steps + validations unchanged")
    touched = {x.split()[2] for x in d["network_added"] + d["network_removed"]}
    t.check(touched == {"/api/v1/products"}, f"only the expected difference: {sorted(touched)} (featured products schema)")
    t.check(replayed["hash"] == base[1]["hash"] and rv == [True] * 4,
            "replaying run 1's HAR (offline) reproduces the baseline fingerprint even with the live fixture changed")
    t.metric("runs", n)
    t.metric("distinct_fingerprints", len(set(hashes)))


# ═══════════════════════════════════════════════════════════════════════
# Part 50 (extra) — the AI scraping assistant: doctor → recon → strategy → data
# ═══════════════════════════════════════════════════════════════════════
@selftest(50, "AI assistant: doctor OK; recon picks the right strategy on 8 kinds of pages and each strategy yields the data")
def t_ai_assistant(t: T) -> None:
    fx = t.fx
    cat = fx.state.catalog
    res = subprocess.run(self_command() + ["doctor", "--out", str(t.out)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env(),
                         timeout=180)
    doc = json.loads(res.stdout or "{}")
    t.check(res.returncode == 0 and doc.get("ok") and any(c["check"] == "chromium launch" and c["ok"] for c in doc["checks"]),
            "doctor: python, playwright and chromium are ready", checks=doc.get("checks"))
    cases = {"catalogue": fx.url("/catalogue/page-1.html"), "feed": fx.url("/trending"), "deals": fx.url("/deals"),
             "spa": fx.url("/products", "spa"), "next": fx.url("/lab/next-store", "lab"),
             "captcha": fx.url("/lab/protected/prices", "lab"), "news": fx.url("/", "news"), "search": fx.url("/search?q=lamp")}

    def job(name: str):
        def run() -> dict:
            with Session(profile=fx.profile(), out_dir=t.out / f"recon_{name}", name=name, hitl="off", strict=False) as s:
                rep = recon(s, cases[name])
                if name == "next":
                    data = s.hydration_data()
                    node = data
                    for part in re.findall(r"[^.\[\]]+", rep["strategy"]["items_path"]):
                        node = node[part]
                    rep["_rows"] = node
            return rep
        return (name, run)
    reps = {r.name: r for r in TaskPool(max_workers=3).run([job(n) for n in cases])}
    for name, r in reps.items():
        t.check(r.ok, f"recon {name} ran", error=(r.error or "")[:600])
    st = {n: r.value["strategy"] for n, r in reps.items()}
    for name in cases:
        s_ = st[name]
        t.note(f"{name:9} → {s_['approach']:13} {s_.get('endpoint') or s_.get('item') or s_.get('items_path') or ''}")
    t.check(st["catalogue"]["approach"] == "html-list" and st["catalogue"]["item"] == "li.col" and
            st["catalogue"]["pagination"] == "next", "server-rendered catalogue → html-list li.col + next-page links")
    t.check(st["news"]["approach"] == "html-list" and st["news"]["pagination"] == "next", "WordPress-style news → html-list + next")
    t.check(st["feed"]["approach"] == "api" and st["feed"]["endpoint"].endswith("/api/v1/feed") and
            "cursor" in st["feed"]["pagination"]["params"], "infinite-scroll feed → its JSON API with cursor paging")
    t.check(st["deals"]["approach"] == "api" and st["deals"]["endpoint"].endswith("/api/v1/deals") and
            "page" in st["deals"]["pagination"]["params"], "load-more deals → JSON API with page param")
    t.check(st["search"]["approach"] == "api" and st["search"]["endpoint"].endswith("/api/v1/search"),
            "JS-rendered search results → /api/v1/search")
    t.check(st["spa"]["approach"] == "api" and st["spa"]["endpoint"].endswith("/api/spa/products"), "React SPA → its JSON API")
    t.check(st["next"]["approach"] == "hydration" and st["next"]["items_path"] == "__NEXT_DATA__.props.pageProps.products"
            and reps["next"].value["_rows"] == NEXT_STORE_ITEMS, "Next.js-style page → __NEXT_DATA__ path gives all 12 full rows")
    t.check(st["captcha"]["approach"] == "unblock-first" and reps["captcha"].value["blocked"]["kind"] == "captcha",
            "CAPTCHA wall → unblock-first advice (stealth, headed, human once, save_state)")
    t.check(all(Path(r.value["paths"]["md"]).read_text(encoding="utf-8").count("Recommended strategy") == 1 for r in reps.values()),
            "recon.md written for every site")
    proxy = fx.start_proxy("recon")
    client = HttpClient(proxy=proxy.url, limiter=RateLimiter(min_interval_s=0.0))
    base = fx.url("", "shop").rstrip("/")
    deals, page = [], 1
    while page < 20:
        d = client.get(f"{base}/api/v1/deals?page={page}").json()
        deals += d["items"]
        if not d.get("has_more"):
            break
        page += 1
    want = [p["id"] for p in cat.products if p["id"] % 9 == 0][:40]
    t.check([x["id"] for x in deals] == want, f"following the deals advice (page=1..{page}) returns all {len(want)} deals")
    feed, cursor = [], 0
    while cursor is not None and len(feed) < 500:
        d = client.get(f"{base}/api/v1/feed?cursor={cursor}&limit=20").json()
        feed += d["items"]
        cursor = d.get("next_cursor")
    t.check(len(feed) == 100 and len({x["id"] for x in feed}) == 100, f"following the feed advice (cursor) returns all {len(feed)} items")
    prof = json.dumps({"host_map": fx.host_map(), "no_proxy_server": True})
    out_csv = t.out / "catalogue.csv"
    res = subprocess.run(self_command() + ["extract", cases["catalogue"], "--paginate", "--max-pages", "3", "--output",
                                           str(out_csv), "--profile", prof, "--out", str(t.out / "extract_run")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env(), timeout=300)
    rows = list(csv.DictReader(open(out_csv, encoding="utf-8-sig"))) if out_csv.exists() else []
    names = [p["name"] for p in cat.products[:60]]
    t.check(res.returncode == 0 and len(rows) == 60 and {"title", "price", "url"} <= set(rows[0]) and
            all(a.startswith(r["title"].rstrip("…. ")[:20]) for r, a in zip(rows, names)) and
            all(r["price"].startswith("£") for r in rows), f"CLI extract --paginate: {len(rows)} rows (3 pages) → CSV, "
            f"in catalogue order", err=res.stderr[-600:])
    with Session(profile=fx.profile(), out_dir=t.out / "tools", name="tools", hitl="off", strict=False) as s:
        dispatch_tool(s, "goto", {"url": fx.url("/login")})
        dispatch_tool(s, "fill", {"target": "Username", "text": "demo"})
        dispatch_tool(s, "fill", {"target": "Password", "text": "demo123"})
        dispatch_tool(s, "click", {"target": "Sign in"})
        me = dispatch_tool(s, "fetch", {"url": "/api/v1/me"})
        page2 = dispatch_tool(s, "fetch", {"url": "/api/v1/deals", "params": {"page": 2}})
        dispatch_tool(s, "goto", {"url": cases["next"]})
        blob = dispatch_tool(s, "page_data", {})
        first = dispatch_tool(s, "page_data", {"path": blob["item_lists"][0]["path"], "limit": 5})
        fin = dispatch_tool(s, "finish", {})
    t.check(me.get("status") == 200 and me["json"]["username"] == "demo" and page2["json"]["items"][0]["id"] == want[8],
            "tool 'fetch': API calls reuse the browser login (GET /api/v1/me → demo) and paging params")
    t.check(blob["item_lists"][0]["path"].endswith("pageProps.products") and first == NEXT_STORE_ITEMS[:5],
            "tool 'page_data': finds the embedded product list and returns it by path")
    recs = har_to_records(fin["paths"]["har"])
    t.check(any(r.url.endswith("/api/v1/me") and (r.initiator or {}).get("type") == "agenttrace.fetch" for r in recs),
            "fetched API calls are recorded in the session HAR")
    with Session(profile=fx.profile(), out_dir=t.out / "tools_anon", name="anon", hitl="off", strict=False) as s:
        s.goto(fx.url("/"))
        anon = s.fetch("/api/v1/me")
    t.check(anon["status"] == 401 and not anon["ok"], "same call without the login → 401 (cookies really come from the browser)")
    for fmt, key in (("anthropic", "input_schema"), ("openai", "function"), ("mcp", "inputSchema")):
        out = subprocess.run(self_command() + ["tools", "--format", fmt], capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env(), timeout=60)
        tools = json.loads(out.stdout)
        names_ = [x["function"]["name"] if fmt == "openai" else x["name"] for x in tools]
        t.check(len(tools) >= 20 and len(set(names_)) == len(names_) and all(key in x for x in tools) and
                {"recon", "extract", "paginate", "endpoints", "export", "capture_page", "fetch", "page_data"} <= set(names_),
                f"tools --format {fmt}: {len(tools)} tools, unique names, schemas present")
    t.metric("strategies", {n: s_["approach"] for n, s_ in st.items()})



# ════════════════════════════════════════════════════════════════════════════
# cli.py — command line interface
# ════════════════════════════════════════════════════════════════════════════
# Command line interface: ``python agenttrace.py <command> ...``


def _profile_from_args(args: Any) -> dict:
    prof: dict = {}
    raw = getattr(args, "profile", None)
    if raw:
        prof.update(read_json(raw) if Path(raw).exists() else json.loads(raw))
    if getattr(args, "headed", False):
        prof["headless"] = False
    if getattr(args, "stealth", False):
        prof["stealth"] = True
    if getattr(args, "no_stealth", False):
        prof["stealth"] = False
    if getattr(args, "proxy", None):
        prof["proxy"] = args.proxy
    if getattr(args, "ua", None):
        prof["user_agent"] = args.ua
    if getattr(args, "locale", None):
        prof["locale"] = args.locale
    if getattr(args, "timezone", None):
        prof["timezone_id"] = args.timezone
    headers = {}
    for h in getattr(args, "header", None) or []:
        name, _, value = h.partition(":")
        headers[name.strip()] = value.strip()
    if headers:
        prof["extra_headers"] = headers
    if getattr(args, "state", None):
        prof["storage_state"] = args.state
    return prof


def _session(args: Any, name: str, **kw: Any) -> Session:
    rate = getattr(args, "rate", None)
    return Session(profile=_profile_from_args(args), out_dir=getattr(args, "out", None) or None, name=name,
                   rate_limit={"min_interval_s": rate} if rate else None, **kw)


def _print(obj: Any) -> None:
    text = obj if isinstance(obj, str) else dump_json(obj)
    try:
        sys.stdout.write(text + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")
    sys.stdout.flush()


def _write_rows(rows: list, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        keys = sorted({k for r in rows if isinstance(r, dict) for k in r})
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # BOM: Excel opens £/৳/€ correctly
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r if isinstance(r, dict) else {"value": r})
    elif path.suffix.lower() == ".jsonl":
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        write_json(path, rows)
    return str(path)


def _wait_enter(session: Session, prompt: str) -> None:
    """Wait for Enter on stdin (or the page closing) while pumping browser events."""
    done = threading.Event()

    def reader() -> None:
        try:
            sys.stdin.readline()
        except Exception:  # noqa: BLE001
            pass
        done.set()
    sys.stderr.write(prompt + "\n")
    sys.stderr.flush()
    threading.Thread(target=reader, daemon=True).start()
    while not done.is_set():
        page = session.page
        if page is None or page.is_closed():
            break
        try:
            page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            break


def cmd_doctor(args: Any) -> int:
    checks = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, **({"fix": fix} if fix and not ok else {})})

    add("python", sys.version_info >= (3, 9), platform.python_version(), "use Python 3.9+")
    try:
        import importlib.metadata as md
        add("playwright package", True, md.version("playwright"))
    except Exception as exc:  # noqa: BLE001
        add("playwright package", False, str(exc), "pip install playwright")
    try:
        with BrowserEngine(BrowserProfile(headless=True)) as eng:
            add("chromium launch", True, f"{eng.version} via {eng.launch_info.get('how')}")
    except AgentTraceError as exc:
        add("chromium launch", False, str(exc), exc.hint or "python -m playwright install chromium")
    try:
        probe = Path(args.out or ".") / ".agenttrace_write_test"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("write access", True, str(Path(args.out or ".").resolve()))
    except OSError as exc:
        add("write access", False, str(exc), "run from a writable directory or pass --out")
    for mod, why in (("yaml", "YAML workflow configs"), ("psutil", "memory-aware task pool")):
        try:
            __import__(mod)
            add(f"optional: {mod}", True, "installed")
        except ImportError:
            checks.append({"check": f"optional: {mod}", "ok": None, "detail": f"not installed ({why})", "fix": f"pip install {mod}"})
    if args.online:
        import urllib.request
        try:
            urllib.request.urlopen("https://example.com", timeout=10).read(100)
            add("internet", True, "https://example.com reachable")
        except Exception as exc:  # noqa: BLE001
            add("internet", False, str(exc), "check proxy / network policy")
    for c in checks:
        mark = "OK  " if c["ok"] else ("--  " if c["ok"] is None else "FAIL")
        sys.stderr.write(f"[{mark}] {c['check']}: {c['detail']}" + (f"  -> {c['fix']}" if c.get("fix") and c["ok"] is False else "") + "\n")
    _print({"ok": all(c["ok"] is not False for c in checks), "checks": checks})
    return 0 if all(c["ok"] is not False for c in checks) else 1


def cmd_recon(args: Any) -> int:
    s = _session(args, "recon")
    with s:
        rep = recon(s, args.url, scroll_rounds=args.scroll)
    _print({"strategy": rep["strategy"], "blocked": rep["blocked"], "pagination": rep["pagination"].get("type"),
            "lists": [{k: c[k] for k in ("selector", "count", "fields")} for c in rep["lists"]],
            "data_endpoints": rep["data_endpoints"][:5], "robots": rep["robots"], "report": rep.get("paths"),
            "session_dir": str(s.out_dir)})
    return 0


def cmd_capture(args: Any) -> int:
    s = _session(args, "capture")
    with s:
        res = s.capture(args.url, expect=tuple(args.expect or ()), quiet_ms=args.quiet_ms, har_path=args.har)
    _print({**res, "session": str(s.out_dir)})
    return 0 if res["ok"] else 2


def cmd_crawl(args: Any) -> int:
    urls = list(args.urls or [])
    if args.file:
        urls += [u.strip() for u in Path(args.file).read_text(encoding="utf-8").splitlines() if u.strip() and not u.startswith("#")]
    if not urls:
        raise AgentTraceError("no URLs given", hint="pass URLs or --file urls.txt")
    s = _session(args, "crawl")
    with s:
        res = crawl(s, urls)
    _print({k: res[k] for k in ("total", "visited", "failed", "skipped", "out_dir", "summary_path")})
    return 0 if res["failed"] == 0 else 2


def cmd_extract(args: Any) -> int:
    fields = {}
    for f in args.field or []:
        name, _, spec = f.partition("=")
        fields[name.strip()] = spec.strip()
    s = _session(args, "extract")
    with s:
        s.goto(args.url)
        if args.paginate:
            res = s.paginate(mode=args.mode, max_pages=args.max_pages, item=args.item, fields=fields or None)
            rows, meta = res["items"], {k: res[k] for k in ("mode", "item", "fields", "page_count", "stopped_because")}
        else:
            res = s.extract_list(item=args.item, fields=fields or None, limit=args.limit)
            rows, meta = res["items"], {k: res[k] for k in ("item", "fields")}
    out = _write_rows(rows, Path(args.output)) if args.output else None
    _print({"count": len(rows), "output": out, **meta, "sample": rows[:3], "session": str(s.out_dir)})
    return 0 if rows else 2


def cmd_run(args: Any) -> int:
    kw = {k: True for k in ("debug", "trace") if getattr(args, k, False)}
    res = run_config(args.config, out_dir=args.out, resume=args.resume, headless=False if args.headed else None,
                     session_kwargs=kw)
    _print({"ok": res["ok"], "out_dir": res["out_dir"], "status": res["run"].get("status"),
            **({"stopped_by": res["run"]["stopped_by"], "reason": res["run"].get("reason"), "hint": res["run"].get("hint")}
               if res["run"].get("stopped_by") else {}),
            "steps": [{"id": s["id"], "action": s["action"], "status": s["status"]} for s in res["run"].get("steps", [])],
            "crawl": {k: res["crawl"][k] for k in ("visited", "failed")} if res.get("crawl") else None,
            "data": res.get("data_path"), "exports": res.get("exports")})
    return 0 if res["ok"] else 2


def cmd_record(args: Any) -> int:
    prof = _profile_from_args(args)
    prof["headless"] = False
    s = Session(profile=prof, name="record", out_dir=args.session_out)
    with s:
        rec = WorkflowRecorder(s, name=Path(args.out).stem).start(args.url)
        _wait_enter(s, "[agenttrace] Recording. Use the browser normally; press Enter here (or close the window) to finish.")
        result = rec.stop()
    info = save_workflow(result, args.out)
    _print({**info, "raw_events": result["raw_events"]})
    return 0


def cmd_replay(args: Any) -> int:
    res = replay(args.workflow, times=args.times, profile=_profile_from_args(args))
    _print({k: res[k] for k in ("workflow", "times", "passed", "consistent_order")} |
           {"runs": [{k: r[k] for k in ("run", "ok", "fingerprint", "out_dir")} for r in res["runs"]]})
    return 0 if res["passed"] == res["times"] else 2


def cmd_goal(args: Any) -> int:
    res = run_goal(args.goal, start_url=args.url, out_dir=args.out, profile=_profile_from_args(args))
    _print({"ok": res["ok"], "steps": [{k: r.get(k) for k in ("step", "action", "ok", "error")} for r in res["results"]],
            "extracted": (res.get("extracted") or [])[:5], "report": (res.get("out_dir") or "") + "/goal_report.md"})
    return 0 if res["ok"] else 2


def cmd_login(args: Any) -> int:
    prof = _profile_from_args(args)
    prof["headless"] = False
    with Session(profile=prof, name="login") as s:
        s.goto(args.url)
        _wait_enter(s, "[agenttrace] Log in in the browser window, then press Enter here to save the session.")
        info = s.save_state(args.save)
    _print(info)
    return 0


def cmd_endpoints(args: Any) -> int:
    eps = discover_endpoints(har_to_records(args.har))
    if args.out:
        write_json(args.out, eps)
    _print(endpoints_markdown(eps) if args.md else eps)
    return 0


def cmd_export(args: Any) -> int:
    _print(export_all(har_to_records(args.har), args.out, name=args.name))
    return 0


def cmd_redact(args: Any) -> int:
    har = load_har(args.har)
    red = Redactor()
    out = red.redact_har(har)
    path = save_har(args.out, out)
    leaks = red.find_leaks(Path(path).read_text(encoding="utf-8"))
    _print({"output": str(path), "secrets_masked": len(red.secrets), "leaks": leaks})
    return 0 if not leaks else 3


def cmd_validate(args: Any) -> int:
    exps: list = []
    if args.spec:
        exps += read_json(args.spec)
    for e in args.expect or []:
        exps.append(e)
    if not exps:
        problems = validate_har(load_har(args.har))
        _print({"har_valid": not problems, "problems": problems[:50]})
        return 0 if not problems else 2
    res = validate_expectations(har_to_records(args.har), exps)
    _print(res)
    return 0 if res["ok"] else 2


def cmd_diff(args: Any) -> int:
    def eps(path: str) -> list:
        p = Path(path)
        if p.is_dir():
            p = next((c for c in (p / "endpoints.json", p / "session" / "endpoints.json") if c.exists()), p)
        if p.suffix == ".har":
            return discover_endpoints(har_to_records(p))
        return read_json(p)
    res = compare_endpoints(eps(args.a), eps(args.b))
    _print(regression_markdown(res) if args.md else res)
    return 0 if not res["breaking"] else 4


def cmd_noise(args: Any) -> int:
    recs = har_to_records(args.har)
    rep = noise_report(recs)
    if args.out:
        kept = filter_records(recs).kept
        save_har(args.out, build_har(kept))
        rep["clean_har"] = args.out
    _print(rep)
    return 0


def cmd_mcp(args: Any) -> int:
    prof = _profile_from_args(args)
    return run_mcp_server(out_dir=args.out or "agenttrace_runs", headless=not args.headed, stealth=bool(args.stealth),
                          log_file=args.log_file, profile=prof or None)


def cmd_tools(args: Any) -> int:
    _print(tool_catalog(args.format))
    return 0


def cmd_selftest(args: Any) -> int:
    return run_selftests(pattern=args.k, parts=args.part, live=args.live, quick=args.quick, out_dir=args.out,
                fail_fast=args.fail_fast, list_only=args.list)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agenttrace", description="AI helper for web scraping: browser automation, "
                                "network/HAR capture, API discovery and export. Run 'doctor' first, then 'recon URL'.")
    p.add_argument("--version", action="version", version=f"agenttrace {__version__}")
    p.add_argument("--log-level", default="WARNING", help="DEBUG/INFO/WARNING (logs go to stderr)")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    def browser_flags(sp: argparse.ArgumentParser, out: bool = True) -> None:
        sp.add_argument("--headed", action="store_true", help="show the browser window")
        sp.add_argument("--stealth", action="store_true", help="anti-detection profile")
        sp.add_argument("--proxy", help="http://user:pass@host:port")
        sp.add_argument("--profile", help="browser profile JSON (string or file)")
        sp.add_argument("--state", help="saved login state file to reuse")
        sp.add_argument("--header", action="append", help="extra header 'Name: value' (repeatable)")
        sp.add_argument("--ua", help="user agent")
        sp.add_argument("--locale")
        sp.add_argument("--timezone")
        sp.add_argument("--rate", type=float, help="minimum seconds between navigations to a host")
        if out:
            sp.add_argument("--out", help="output directory")

    sp = sub.add_parser("doctor", help="check Python/Playwright/browser setup")
    sp.add_argument("--out")
    sp.add_argument("--online", action="store_true", help="also test internet access")
    sp.set_defaults(fn=cmd_doctor)
    sp = sub.add_parser("recon", help="analyse a site and recommend a scraping strategy")
    sp.add_argument("url")
    sp.add_argument("--scroll", type=int, default=2, help="scroll rounds to trigger lazy loading")
    sp.add_argument("--no-stealth", action="store_true")
    browser_flags(sp)
    sp.set_defaults(fn=cmd_recon, stealth=True)
    sp = sub.add_parser("capture", help="verified HAR capture of one page")
    sp.add_argument("url")
    sp.add_argument("--expect", action="append", help="URL substring that must be captured (repeatable)")
    sp.add_argument("--quiet-ms", type=float, default=1000)
    sp.add_argument("--har", help="HAR output path")
    browser_flags(sp)
    sp.set_defaults(fn=cmd_capture)
    sp = sub.add_parser("crawl", help="capture a list of pages (one HAR each)")
    sp.add_argument("urls", nargs="*")
    sp.add_argument("--file", help="text file with one URL per line")
    browser_flags(sp)
    sp.set_defaults(fn=cmd_crawl)
    sp = sub.add_parser("extract", help="extract a list (auto-detected or --item/--field), optionally across pages")
    sp.add_argument("url")
    sp.add_argument("--item", help="CSS selector of one item")
    sp.add_argument("--field", action="append", help="name=css or name=css@attr (repeatable)")
    sp.add_argument("--paginate", action="store_true")
    sp.add_argument("--mode", default="auto", choices=["auto", "next", "load_more", "scroll", "url"])
    sp.add_argument("--max-pages", type=int, default=20)
    sp.add_argument("--limit", type=int)
    sp.add_argument("--output", help="data.json / data.csv / data.jsonl")
    browser_flags(sp)
    sp.set_defaults(fn=cmd_extract)
    sp = sub.add_parser("run", help="run a workflow/site config (JSON/YAML/TOML)")
    sp.add_argument("config")
    sp.add_argument("--resume", action="store_true", help="continue from the last checkpoint")
    sp.add_argument("--headed", action="store_true")
    sp.add_argument("--debug", action="store_true", help="verbose tracing: every request/response/console event in log.jsonl")
    sp.add_argument("--trace", action="store_true", help="also save a Playwright trace.zip (open with 'playwright show-trace')")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("record", help="record your manual browsing into a replayable workflow")
    sp.add_argument("url")
    sp.add_argument("--out", required=True, help="workflow.json path")
    sp.add_argument("--session-out")
    browser_flags(sp, out=False)
    sp.set_defaults(fn=cmd_record)
    sp = sub.add_parser("replay", help="replay a recorded workflow (validates network per step)")
    sp.add_argument("workflow")
    sp.add_argument("--times", type=int, default=1)
    browser_flags(sp)
    sp.set_defaults(fn=cmd_replay)
    sp = sub.add_parser("goal", help="execute a plain-language goal")
    sp.add_argument("goal")
    sp.add_argument("--url", help="start URL")
    browser_flags(sp)
    sp.set_defaults(fn=cmd_goal)
    sp = sub.add_parser("login", help="log in manually once (headed) and save the session state")
    sp.add_argument("url")
    sp.add_argument("--save", required=True)
    browser_flags(sp)
    sp.set_defaults(fn=cmd_login)
    sp = sub.add_parser("endpoints", help="discover API endpoints in a HAR (any HAR, e.g. from DevTools)")
    sp.add_argument("har")
    sp.add_argument("--md", action="store_true")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_endpoints)
    sp = sub.add_parser("export", help="HAR → Postman collection, OpenAPI, Python client, curl")
    sp.add_argument("har")
    sp.add_argument("--out", required=True)
    sp.add_argument("--name", default="AgentTrace capture")
    sp.set_defaults(fn=cmd_export)
    sp = sub.add_parser("redact", help="mask cookies/tokens/passwords in a HAR for sharing")
    sp.add_argument("har")
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=cmd_redact)
    sp = sub.add_parser("validate", help="validate a HAR (structure, or expectations like 'POST /api/cart')")
    sp.add_argument("har")
    sp.add_argument("--expect", action="append")
    sp.add_argument("--spec", help="JSON list of expectation objects")
    sp.set_defaults(fn=cmd_validate)
    sp = sub.add_parser("diff", help="API regression diff between two captures (HAR / endpoints.json / run dir)")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("--md", action="store_true")
    sp.set_defaults(fn=cmd_diff)
    sp = sub.add_parser("noise", help="noise report for a HAR; --out writes a clean HAR")
    sp.add_argument("har")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_noise)
    sp = sub.add_parser("mcp", help="run the MCP server over stdio (Claude Code / Desktop / Cline)")
    sp.add_argument("--log-file")
    browser_flags(sp)
    sp.set_defaults(fn=cmd_mcp)
    sp = sub.add_parser("tools", help="print the AI tool catalog (JSON schemas)")
    sp.add_argument("--format", default="anthropic", choices=["anthropic", "mcp", "openai"])
    sp.set_defaults(fn=cmd_tools)
    sp = sub.add_parser("selftest", help="run the built-in real-world test-suite")
    sp.add_argument("-k", help="only tests whose name contains this")
    sp.add_argument("--part", type=int, action="append", help="only this knowledge.md part (repeatable)")
    sp.add_argument("--live", default="auto", choices=["auto", "on", "off"], help="tests against real websites")
    sp.add_argument("--quick", action="store_true", help="fewer repetitions")
    sp.add_argument("--fail-fast", action="store_true")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_selftest)
    return p


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 1
    if args.cmd != "mcp":
        configure_logging(args.log_level, console=True, stream=sys.stderr)
    try:
        return int(args.fn(args) or 0)
    except AgentTraceError as exc:
        _print(exc.to_dict())
        return 2
    except KeyboardInterrupt:
        return 130



if __name__ == "__main__":
    sys.exit(main())
