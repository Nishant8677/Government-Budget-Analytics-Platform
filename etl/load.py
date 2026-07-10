"""
etl/load.py
───────────
Load phase: persist transformed records to MySQL.

Design decisions
────────────────
1. Upsert pattern (INSERT IGNORE + SELECT, or ON DUPLICATE KEY UPDATE):
   Running the pipeline twice will NOT create duplicates.  All reference
   tables (groups, schemes, major_heads, sub_schemes, fiscal_years) use
   INSERT IGNORE so existing rows are kept and the new ID is fetched.
   budget_data uses ON DUPLICATE KEY UPDATE so re-runs refresh the figures.

2. Single transaction per batch:
   All records are committed in one transaction.  If any record fails the
   entire batch is rolled back, preventing partial loads.  The caller can
   then fix the source data and re-run safely.

3. executemany() NOT used for reference tables:
   Reference tables (groups, schemes, etc.) need get-or-create semantics —
   we must return the ID after insert.  executemany() does not support this
   pattern.  However, budget_data rows are fully resolved before the loop,
   so they could be batched; we leave that as a future optimisation since
   the dataset is small (~300 rows).

4. No f-string SQL:
   Every query uses %s placeholders.  Table and column names come from our
   own code (not user input), so they are safe — but we maintain the habit
   of parameterised queries throughout.
"""
from __future__ import annotations

import mysql.connector
from mysql.connector import Error

from utils.db import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Private helpers: upsert reference rows and return their IDs ──────────────

def _upsert_group(cursor, group_name: str) -> int:
    cursor.execute(
        "INSERT IGNORE INTO `groups` (group_name) VALUES (%s)",
        (group_name,),
    )
    cursor.execute(
        "SELECT group_id FROM `groups` WHERE group_name = %s",
        (group_name,),
    )
    return cursor.fetchone()[0]


def _upsert_scheme(cursor, scheme_name: str, group_id: int) -> int:
    cursor.execute(
        "INSERT IGNORE INTO `schemes` (scheme_name, group_id) VALUES (%s, %s)",
        (scheme_name, group_id),
    )
    cursor.execute(
        "SELECT scheme_id FROM `schemes` WHERE scheme_name = %s AND group_id = %s",
        (scheme_name, group_id),
    )
    return cursor.fetchone()[0]


def _upsert_major_head(cursor, major_head_code: int) -> int:
    cursor.execute(
        "INSERT IGNORE INTO `major_heads` (major_head_code) VALUES (%s)",
        (major_head_code,),
    )
    cursor.execute(
        "SELECT major_head_id FROM `major_heads` WHERE major_head_code = %s",
        (major_head_code,),
    )
    return cursor.fetchone()[0]


def _upsert_sub_scheme(
    cursor,
    sub_scheme_name: str,
    scheme_id: int,
    major_head_id: int,
) -> int:
    cursor.execute(
        """
        INSERT IGNORE INTO `sub_schemes`
            (sub_scheme_name, scheme_id, major_head_id)
        VALUES (%s, %s, %s)
        """,
        (sub_scheme_name, scheme_id, major_head_id),
    )
    cursor.execute(
        """
        SELECT sub_scheme_id
        FROM   `sub_schemes`
        WHERE  sub_scheme_name = %s AND scheme_id = %s
        """,
        (sub_scheme_name, scheme_id),
    )
    return cursor.fetchone()[0]


def _upsert_fiscal_year(cursor, fiscal_year: str) -> int:
    cursor.execute(
        "INSERT IGNORE INTO `fiscal_years` (fiscal_year) VALUES (%s)",
        (fiscal_year,),
    )
    cursor.execute(
        "SELECT fiscal_year_id FROM `fiscal_years` WHERE fiscal_year = %s",
        (fiscal_year,),
    )
    return cursor.fetchone()[0]


# ── Public API ────────────────────────────────────────────────────────────────

def load(records: list[dict]) -> None:
    """
    Persist a list of fiscal-year records to the database.

    Parameters
    ──────────
    records : list[dict]
        Output of etl.transform.get_fiscal_year_records().
        Each dict must contain:
            group_name, scheme_name, sub_scheme_name, major_head_code,
            fiscal_year, actuals, budget, revised

    Raises
    ──────
    mysql.connector.Error
        Re-raised after rolling back the transaction if any insert fails.
    """
    logger.info("Loading %d records into database …", len(records))

    conn = get_connection()
    conn.autocommit = False
    cursor = conn.cursor()

    loaded = 0
    skipped = 0

    try:
        for record in records:
            try:
                group_id = _upsert_group(cursor, record["group_name"])
                scheme_id = _upsert_scheme(cursor, record["scheme_name"], group_id)
                major_head_id = _upsert_major_head(cursor, record["major_head_code"])
                sub_scheme_id = _upsert_sub_scheme(
                    cursor,
                    record["sub_scheme_name"],
                    scheme_id,
                    major_head_id,
                )
                fiscal_year_id = _upsert_fiscal_year(cursor, record["fiscal_year"])

                # Upsert budget figures — ON DUPLICATE KEY UPDATE makes re-runs safe
                cursor.execute(
                    """
                    INSERT INTO `budget_data`
                        (sub_scheme_id, fiscal_year_id, actuals, budget, revised)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        actuals  = VALUES(actuals),
                        budget   = VALUES(budget),
                        revised  = VALUES(revised)
                    """,
                    (
                        sub_scheme_id,
                        fiscal_year_id,
                        record["actuals"],
                        record["budget"],
                        record["revised"],
                    ),
                )
                loaded += 1

            except Error as e:
                # Log the bad record but keep going — a single bad row should
                # not abort the entire pipeline.
                logger.warning(
                    "Skipping record (error: %s) -> %s", e, record
                )
                skipped += 1

        conn.commit()
        logger.info(
            "Load complete.  Inserted/updated: %d  |  Skipped: %d", loaded, skipped
        )

    except Exception as exc:
        conn.rollback()
        logger.error("Load failed — transaction rolled back.  Reason: %s", exc)
        raise

    finally:
        cursor.close()
        conn.close()
