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
