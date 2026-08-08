"""
utils/db.py
───────────
Database connection factory.

Design decisions
────────────────
• Two connection helpers are provided:
    - get_connection()        — connects to the application database (DB_NAME)
    - get_server_connection() — connects to the MySQL server *without* selecting
                                a database (used during initial setup only)
• No connection pooling is implemented here because Streamlit's
  @st.cache_resource already manages a single shared connection per session.
• All connections are explicitly charset=utf8mb4 to support international chars.
• Credentials are never passed as literals — always read from config.settings.
"""
from __future__ import annotations

import mysql.connector
from mysql.connector import Error

from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER
from utils.logger import get_logger

logger = get_logger(__name__)

# The four tables every benchmark reports on. Kept here so the scripts agree on
# what "the working set" means without each defining its own list.
TRACKED_TABLES = ("budget_data", "sub_schemes", "schemes", "fiscal_years")


def get_connection(database: str = DB_NAME) -> mysql.connector.MySQLConnection:
    """
    Return a new MySQL connection to *database*.

    Raises
    ──────
    mysql.connector.Error
        If the connection cannot be established (bad credentials, server down,
        database does not exist, etc.).
    """
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=database,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
        )
        return conn
    except Error as exc:
        logger.error("MySQL connection failed: %s", exc)
        raise


def table_sizes_mb(cursor, tables: tuple[str, ...] = TRACKED_TABLES) -> dict[str, dict[str, float]]:
    """
    Return data/index/total size in MB for *tables*, from information_schema.

    Benchmarks record this alongside their timings because the buffer pool is
    only meaningful relative to the working set: a 128 MB pool against a table
    of comparable size behaves nothing like the same pool against a small one.
    PERFORMANCE.md previously quoted a table size typed in by hand, which later
    could not be reproduced -- capturing it per run removes the guesswork.

    These are InnoDB's own estimates. They drift with fragmentation and with
    ANALYZE TABLE, so treat them as the scale of the table rather than an exact
    byte count.
    """
    placeholders = ", ".join(["%s"] * len(tables))
    cursor.execute(
        f"""SELECT table_name,
                   ROUND(data_length  / 1024 / 1024, 1),
                   ROUND(index_length / 1024 / 1024, 1)
            FROM   information_schema.tables
            WHERE  table_schema = DATABASE() AND table_name IN ({placeholders})""",
        tables,
    )
    return {
        name: {"data_mb": float(data), "index_mb": float(idx),
               "total_mb": round(float(data) + float(idx), 1)}
        for name, data, idx in cursor.fetchall()
    }


def get_server_connection() -> mysql.connector.MySQLConnection:
    """
    Return a MySQL connection *without* selecting a database.

    Used only by the database setup script to issue CREATE DATABASE.
    """
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            charset="utf8mb4",
        )
        return conn
    except Error as exc:
        logger.error("MySQL server connection failed: %s", exc)
        raise
