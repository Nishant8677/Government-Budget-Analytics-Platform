"""
etl/load.py
───────────
Load phase: persist transformed records to MySQL.

Design decisions
────────────────
1. Upsert pattern (INSERT IGNORE + SELECT ... FOR SHARE):
   Running the pipeline twice will NOT create duplicates.  All reference
   tables (groups, schemes, major_heads, sub_schemes, fiscal_years) use
   INSERT IGNORE so existing rows are kept and the new ID is fetched.
   budget_data uses ON DUPLICATE KEY UPDATE so re-runs refresh the figures.

   The read-back is FOR SHARE, and that is load-bearing rather than cautious.
   With a plain SELECT this pattern loses rows to a concurrent loader under
   REPEATABLE READ: the snapshot is pinned at the transaction's first
   consistent read, INSERT IGNORE is a locking write that does not establish
   it, so a reference row committed by another worker afterwards collides with
   the INSERT while staying invisible to the SELECT.  fetchone() then returns
   None and None[0] raises TypeError mid-load.  FOR SHARE makes the read-back a
   locking read, which sees the latest committed row rather than the snapshot.
   scripts/test_upsert_race.py reproduces the failure and gates the fix.

   The cost is a shared lock held on each reference row until commit, which
   serialises concurrent loaders that touch the same group or scheme.  These
   tables have tiny cardinality and the locks are short, so this is cheap here.
   The alternative -- INSERT ... ON DUPLICATE KEY UPDATE with LAST_INSERT_ID()
   -- would remove the second round trip as well, at the cost of burning
   auto-increment values on every duplicate.  Worth revisiting if the reference
   lookups ever become the bottleneck; see design note 3.

2. One transaction per batch, but per-record errors are skipped, not fatal:
   An earlier version of this note claimed any failing record rolls the whole
   batch back.  That is not what the code does, and the difference matters
   enough to state precisely.

   Every record is written inside one transaction, committed once at the end.
   A mysql.connector.Error raised while processing a single record is caught
   inside the loop, logged as a warning, counted in `skipped`, and the loop
   continues -- so the batch commits with that record missing.  Only an
   exception that escapes the loop (a connection drop, a failure during
   commit, anything not a per-record Error) triggers the rollback.

   Skip-and-continue is the right default for this source: the input is a
   government CSV whose shape changes between publications, and one malformed
   line should not block an otherwise good load.  All-or-nothing would be
   right if partial state were worse than no state, which it is not here --
   the loader is idempotent, so a re-run repairs a short load.

   The real weakness is not the choice but its reporting.  A skipped record
   survives only as a log line, and load() returns None, so a caller cannot
   tell a clean load from one that dropped half its rows.  Fixing that means
   writing skipped records to a dead-letter file and failing the run when the
   skip rate crosses a threshold.  Until then, check the "Skipped:" count in
   the completion log -- silent partial success is the failure mode this
   design is exposed to.

3. executemany() NOT used for reference tables:
   Reference tables (groups, schemes, etc.) need get-or-create semantics —
   we must return the ID after insert.  executemany() does not support this
   pattern.  However, budget_data rows are fully resolved before the loop,
   so they could be batched; we leave that as a future optimisation because
   this pipeline handles 201 records per run.

   Measured, because two earlier attempts at this figure were wrong.  It first
   said "~300 rows", which was close enough to be misleading next to the
   921,696 rows in budget_data -- those count different things.  It then said
   261, which is the number of (sub-scheme, fiscal year) pairs that could
   exist: 87 sub-schemes x 3 years.  Neither is what the pipeline processes.

   The chain is: 87 sub-schemes x 3 years = 261 possible pairs; transform emits
   201 records, having skipped 60 pairs carrying no figure at all; those 201
   collapse onto 166 distinct (sub_scheme, fiscal_year) keys and 166 rows land.

   That last step is data loss, not deduplication -- see the WARNING below.

   Everything else in budget_data is backdated synthetic data written directly
   by scripts/generate_synthetic_data.py, which batches with executemany() and
   never imports this module.  PERFORMANCE.md documents the split.

5. KNOWN BUG -- duplicate sub-scheme names silently overwrite each other:
   35 of the 201 records collapse onto 8 keys that already exist, and
   ON DUPLICATE KEY UPDATE means the last one wins.  They are not duplicates.
   The source distinguishes them by "Programme Name", which transform() drops
   as irrelevant: under Customs > Import Duties there are nine separate levies
   -- Basic Duties, Social Welfare Surcharge, Health Cess, three education
   cesses and more -- all sharing a sub-scheme name and all carrying Major Head
   Code 37, so major_head_code cannot separate them either.

   Measured against the real CSV, the 8 conflicting keys hold 361,934.70 crore
   of actuals and store 123,583.06 -- so **238,351.64 crore is silently
   discarded**, and every scheme total the dashboard shows for those rows is
   understated.

   The fix is not in this module.  The grain of `sub_schemes` is one level too
   coarse: identity needs Programme Name, which means carrying that column
   through transform, adding it to the hierarchy, and changing
   uq_sub_scheme_scheme.  That is a schema migration plus a full reload, so it
   is recorded here rather than done quietly.

4. No f-string SQL:
   Every query uses %s placeholders.  Table and column names come from our
   own code (not user input), so they are safe — but we maintain the habit
   of parameterised queries throughout.
"""
from __future__ import annotations

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
        "SELECT group_id FROM `groups` WHERE group_name = %s FOR SHARE",
        (group_name,),
    )
    return cursor.fetchone()[0]


def _upsert_scheme(cursor, scheme_name: str, group_id: int) -> int:
    cursor.execute(
        "INSERT IGNORE INTO `schemes` (scheme_name, group_id) VALUES (%s, %s)",
        (scheme_name, group_id),
    )
    cursor.execute(
        "SELECT scheme_id FROM `schemes` WHERE scheme_name = %s AND group_id = %s "
        "FOR SHARE",
        (scheme_name, group_id),
    )
    return cursor.fetchone()[0]


def _upsert_major_head(cursor, major_head_code: int) -> int:
    cursor.execute(
        "INSERT IGNORE INTO `major_heads` (major_head_code) VALUES (%s)",
        (major_head_code,),
    )
    cursor.execute(
        "SELECT major_head_id FROM `major_heads` WHERE major_head_code = %s FOR SHARE",
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
        FOR SHARE
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
        "SELECT fiscal_year_id FROM `fiscal_years` WHERE fiscal_year = %s FOR SHARE",
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

    Records that fail individually are logged and skipped, not raised -- see
    design note 2.  The batch still commits, so a successful return does NOT
    mean every record landed.  The count that tells you is in the completion
    log line ("Inserted/updated: N  |  Skipped: M").

    Raises
    ──────
    mysql.connector.Error
        Only for a failure that escapes the per-record handler -- a lost
        connection, or an error during commit.  The transaction is rolled back
        before it propagates.  A single bad record does not reach here.
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
