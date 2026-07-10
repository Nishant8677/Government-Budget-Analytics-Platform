"""
utils/logger.py
───────────────
Centralised logging factory.

Every module gets a consistent log format via get_logger(__name__).
Logs are written to:
  1. stdout          — visible in terminal during ETL runs
  2. logs/application.log — persistent file log for debugging

Usage:
    from utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Hello")
"""
import logging
import sys
from pathlib import Path

from config.settings import LOG_LEVEL, PROJECT_ROOT

# ── Log directory ─────────────────────────────────────────────────────────────
_LOG_DIR = PROJECT_ROOT / "logs"
_LOG_FILE = _LOG_DIR / "application.log"

_FORMATTER = logging.Formatter(
    fmt="[%(asctime)s] %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler_attached = False   # guard: attach file handler only once


def _get_file_handler() -> logging.FileHandler:
    """Create (and cache) the single shared file handler."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setFormatter(_FORMATTER)
    fh.setLevel(logging.DEBUG)   # file captures everything; console respects LOG_LEVEL
    return fh


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger configured with console + file output.

    Multiple calls with the same *name* return the same Logger instance
    (standard Python logging behaviour) so handlers are never duplicated.
    """
    global _file_handler_attached   # noqa: PLW0603

    logger = logging.getLogger(name)

    if not logger.handlers:
        # ── Console handler ───────────────────────────────────────────────────
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(_FORMATTER)
        logger.addHandler(ch)

        # ── File handler (attached to root logger once) ───────────────────────
        root = logging.getLogger()
        if not _file_handler_attached:
            root.addHandler(_get_file_handler())
            _file_handler_attached = True

    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    return logger

