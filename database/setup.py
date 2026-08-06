"""
database/setup.py
─────────────────
Database initialisation script.

Responsibilities
────────────────
1. Connect to the MySQL *server* (no database selected yet).
2. Create the application database if it does not already exist.
3. Apply schema.sql  — table definitions, constraints, indexes.
4. Apply views.sql   — analytical views.

This script is idempotent:
  • schema.sql uses IF NOT EXISTS on every CREATE TABLE.
  • views.sql uses CREATE OR REPLACE VIEW.
  • Running setup twice does NOT wipe existing data.

Usage
─────
    python setup_db.py          # recommended entry point (project root)
    python -m database.setup    # alternative (from project root)
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── Make project root importable regardless of CWD ───────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import mysql.connector
from mysql.connector import Error

from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER
from utils.logger import get_logger

logger = get_logger(__name__)

DATABASE_DIR = Path(__file__).parent


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_sql(filename: str) -> str:
    filepath = DATABASE_DIR / filename
    if not filepath.exists():
        raise FileNotFoundError(f"SQL file not found: {filepath}")
    return filepath.read_text(encoding="utf-8")


def _run_script(cursor: mysql.connector.cursor.MySQLCursor, sql: str) -> None:
    """
    Execute a multi-statement SQL script.

    Splits on ';', strips whitespace/comments, and skips empty statements.
    We do NOT use multi=True to keep error messages unambiguous — each
    statement is executed separately so we know exactly which one failed.
    """
    for raw_stmt in sql.split(";"):
        # Strip inline -- comments (line-by-line)
        lines = [
            line for line in raw_stmt.splitlines()
            if not line.strip().startswith("--")
        ]
        stmt = "\n".join(lines).strip()
        if stmt:
            try:
                cursor.execute(stmt)
            except Exception as e:
                print(f"Failed to execute:\n{stmt}")
                raise e


# ── Steps ────────────────────────────────────────────────────────────────────

def create_database() -> None:
    """Create the application database on the MySQL server if absent."""
    logger.info("Connecting to MySQL server at %s:%s …", DB_HOST, DB_PORT)
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            charset="utf8mb4",
        )
    except Error as exc:
        logger.error(
            "Cannot connect to MySQL.  "
            "Check DB_HOST, DB_PORT, DB_USER and DB_PASSWORD in your .env file.  "
            "Error: %s", exc
        )
        raise SystemExit(1) from exc

    cursor = conn.cursor()
    try:
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            f"DEFAULT CHARACTER SET utf8mb4 "
            f"COLLATE utf8mb4_unicode_ci;"
        )
        conn.commit()
        logger.info("Database '%s' is ready.", DB_NAME)
    finally:
        cursor.close()
        conn.close()


def apply_schema() -> None:
    """Apply schema.sql and views.sql to the application database."""
    logger.info("Connecting to database '%s' …", DB_NAME)
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
        )
    except Error as exc:
        logger.error("Cannot connect to '%s': %s", DB_NAME, exc)
        raise SystemExit(1) from exc

    cursor = conn.cursor()
    try:
        for sql_file in ("schema.sql", "views.sql"):
            logger.info("Applying %s …", sql_file)
            sql = _read_sql(sql_file)
            _run_script(cursor, sql)
            conn.commit()
            logger.info("  ✓ %s applied successfully.", sql_file)
    except Error as exc:
        conn.rollback()
        logger.error("Schema application failed: %s", exc)
        raise
    finally:
        cursor.close()
        conn.close()


# ── Public entry point ───────────────────────────────────────────────────────

def setup() -> None:
    """Full database setup: create DB + apply schema + views."""
    logger.info("=" * 60)
    logger.info("BudgetIQ — Database Setup")
    logger.info("=" * 60)
    create_database()
    apply_schema()
    logger.info("Database setup complete.  You can now run the ETL pipeline.")
    logger.info("=" * 60)


if __name__ == "__main__":
    setup()
