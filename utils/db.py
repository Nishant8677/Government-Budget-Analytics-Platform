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
from mysql.connector import Error, pooling

from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER
from utils.logger import get_logger

logger = get_logger(__name__)

# The four tables every benchmark reports on. Kept here so the scripts agree on
# what "the working set" means without each defining its own list.
TRACKED_TABLES = ("budget_data", "sub_schemes", "schemes", "fiscal_years")

# mysql-connector caps pool_size at 32. Eight is chosen against this dashboard's
# actual shape: the default page load issues eleven queries of which two run for
# tens of seconds, so a handful of simultaneous readers can hold connections for
# a long time. Raise it if PoolError appears in the logs; that error means
# concurrency exceeded the pool, not that the database is unhealthy.
DEFAULT_POOL_SIZE = 8


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


def get_pool(pool_name: str = "budgetiq", pool_size: int = DEFAULT_POOL_SIZE):
    """
    Return a MySQL connection pool sized for concurrent dashboard readers.

    Why a pool rather than a shared connection
    ──────────────────────────────────────────
    A mysql-connector connection is not thread-safe, and Streamlit runs each
    browser session in its own thread.  A single connection cached across
    sessions lets two users interleave on the same cursor, which surfaces as
    "Commands out of sync" or as one user receiving another's result set.  The
    pool hands each caller its own connection and takes it back on close().

    A pool also survives what a long-lived single connection does not.  MySQL
    closes idle connections after `wait_timeout` (8 hours by default), so a
    dashboard left open overnight wakes up holding a dead socket and fails every
    query until the process restarts.  Callers should ping on checkout -- see
    the usage note below.

    Sizing
    ──────
    `pool_size` bounds concurrent in-flight queries, not users; readers spend
    almost all their time idle between interactions.  The default is deliberately
    larger than the mysql-connector default of 5 because this dashboard has
    queries measured in tens of seconds, and a checkout that finds the pool
    exhausted raises PoolError rather than waiting.

    Usage
    ─────
        conn = get_pool().get_connection()
        try:
            conn.ping(reconnect=True, attempts=2, delay=1)
            ...
        finally:
            conn.close()   # returns the connection to the pool

    Raises
    ──────
    mysql.connector.Error
        If the pool cannot be created (bad credentials, server down).
    """
    try:
        return pooling.MySQLConnectionPool(
            pool_name=pool_name,
            pool_size=pool_size,
            pool_reset_session=True,
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
        )
    except Error as exc:
        logger.error("MySQL pool creation failed: %s", exc)
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
