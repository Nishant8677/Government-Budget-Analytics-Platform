"""
config/validator.py
────────────────────
Configuration validation guard.

Call validate() before any database operation to produce clear,
actionable error messages instead of cryptic stack traces.

Example output on failure:
    [ERROR] Configuration validation failed. Issues found:
    [ERROR]   • DB_PASSWORD is not set — MySQL requires a password.
    [ERROR]
    [ERROR]   Fix:  cp .env.example .env
    [ERROR]         Then fill in your MySQL credentials.
    [ERROR]
    [ERROR]   Exiting.
"""
from __future__ import annotations

import sys

from utils.logger import get_logger

logger = get_logger(__name__)


def validate() -> None:
    """
    Validate that all required environment variables are present and sane.

    Raises SystemExit(1) with a clear error message if validation fails.
    Does NOT raise an exception — a clean exit is better than a traceback
    for a configuration error that the user needs to fix manually.
    """
    # Import here (after load_dotenv has run) to capture runtime values
    from config.settings import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

    errors: list[str] = []

    if not DB_HOST:
        errors.append("DB_HOST is not set (e.g., localhost)")

    try:
        port = int(DB_PORT)
        if not (1 <= port <= 65535):
            errors.append(f"DB_PORT must be 1–65535 (got {port})")
    except (ValueError, TypeError):
        errors.append(f"DB_PORT must be a valid integer (got {DB_PORT!r})")

    if not DB_USER:
        errors.append("DB_USER is not set (e.g., root)")

    if not DB_PASSWORD:
        errors.append(
            "DB_PASSWORD is not set — MySQL requires a password.\n"
            "           Leave it empty only if your MySQL server has no root password."
        )

    if not DB_NAME:
        errors.append("DB_NAME is not set (e.g., budgetiq)")

    if errors:
        logger.error("-" * 55)
        logger.error("Configuration validation failed.  Issues found:")
        for err in errors:
            logger.error("  *  %s", err)
        logger.error("")
        logger.error("  Fix:  Copy .env.example to .env and fill in your credentials.")
        logger.error("        Windows:  copy .env.example .env")
        logger.error("        macOS/Linux:  cp .env.example .env")
        logger.error("-" * 55)
        sys.exit(1)

    logger.info(
        "Config OK — host=%s  port=%s  db=%s  user=%s",
        DB_HOST, DB_PORT, DB_NAME, DB_USER,
    )
