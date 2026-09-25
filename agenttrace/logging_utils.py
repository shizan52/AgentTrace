"""Centralized logging helpers for AgentTrace.

A single shared logger (name: ``agenttrace``) is used by every part of the
project.  ``setup_logging()`` can be called repeatedly; each call cleanly
switches the file handler, so per-test log files can be created from within
one process without duplicated handlers.

Console output uses a short format; file output includes the logger name so
the origin of every message is traceable.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

LOGGER_NAME = "agenttrace"
DEFAULT_LEVEL = logging.INFO

_CONSOLE_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"
_FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def timestamp(*, compact: bool = True) -> str:
    """Return current UTC time as a string, safe for filenames."""
    now = datetime.utcnow()
    if compact:
        return now.strftime("%Y%m%d_%H%M%S")
    return now.strftime("%Y-%m-%d_%H-%M-%S")


def setup_logging(
    log_dir: str | Path,
    *,
    filename: str | None = None,
    level: int = DEFAULT_LEVEL,
    console: bool = True,
    file: bool = True,
) -> logging.Logger:
    """Configure the shared ``agenttrace`` logger.

    Args:
        log_dir: Directory where the log file is written (created if missing).
        filename: Log file name (default: ``agenttrace_<UTC timestamp>.log``).
        level: Minimum level for file handler.
        console: Whether to attach a stream (console) handler.
        file: Whether to attach a file handler.
    """
    log_dir = Path(log_dir)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Remove previous handlers so re-calling this function switches targets.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(console_handler)

    if file:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_name = filename or f"agenttrace_{timestamp()}.log"
        file_handler = RotatingFileHandler(
            log_dir / log_name,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Return the shared logger or a child logger of it."""
    if name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def default_log_filename(prefix: str = "agenttrace") -> str:
    """Generate a timestamped log filename, e.g. ``agenttrace_20260101_120000.log``."""
    return f"{prefix}_{timestamp()}.log"