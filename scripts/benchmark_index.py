"""Measures what the covering index on budget_data actually buys.

The README credits `idx_budget_fy_sub_budget (fiscal_year_id, sub_scheme_id,
budget)` with an 83% latency reduction on the heavy JOIN query, but that index
has never existed in `database/schema.sql` and neither `benchmark.py` nor
`benchmark_cache.py` writes its results anywhere. This script exists so the
number has a file behind it.

What is actually being compared
───────────────────────────────
NOT "no index" versus "index". `budget_data` already carries
`idx_budget_fiscal_year (fiscal_year_id)` and the unique key
`uq_budget_sub_year (sub_scheme_id, fiscal_year_id)`, so the WHERE clause is
already served by an index today. The covering index adds `budget` as a
trailing column, which lets InnoDB satisfy the filter, the join key and the
SUM() from index leaf pages alone and skip the row lookups. The honest
comparison is therefore:

    before : the indexes the schema ships with
    after  : those, plus the covering index

Expect a smaller delta than a naive "before/after adding an index" framing
would suggest. Whatever it prints is the number.

Method
──────
Each trial drops the covering index, warms the buffer pool, times the query
`--repeats` times, then creates the index, runs ANALYZE TABLE, warms again and
re-times. Medians are reported across `--trials` trials, and every individual
sample is persisted so the spread can be inspected rather than taken on trust.

Caveat recorded in the output: the InnoDB buffer pool cannot be flushed from a
client connection without SUPER privileges or a server restart, so these are
warm-cache figures. Cold-cache latency would be higher for both arms. The
comparison between arms remains valid; the absolute numbers are best case.

    python scripts/benchmark_index.py
    python scripts/benchmark_index.py --trials 5 --repeats 11
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mysql.connector

sys.path.append(str(Path(__file__).resolve().parent.parent))

from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER  # noqa: E402
from utils.db import TRACKED_TABLES, table_sizes_mb  # noqa: E402

INDEX_NAME = "idx_budget_fy_sub_budget"
INDEX_DDL = (
    f"CREATE INDEX {INDEX_NAME} ON budget_data (fiscal_year_id, sub_scheme_id, budget)"
)

# Mirrors "Q2: Grouped Aggregation (Scheme Level)" in scripts/benchmark.py. It
# filters on fiscal_year_id, joins on sub_scheme_id and sums budget -- the three
# columns the covering index carries, in that order.
TARGET_QUERY = """
    SELECT s.scheme_name, SUM(b.budget) AS total_budget
    FROM budget_data b
    JOIN sub_schemes ss ON b.sub_scheme_id = ss.sub_scheme_id
    JOIN schemes s ON ss.scheme_id = s.scheme_id
    WHERE b.fiscal_year_id = (SELECT MAX(fiscal_year_id) FROM fiscal_years)
    GROUP BY s.scheme_name
    ORDER BY total_budget DESC
    LIMIT 10
"""

# Same query, but the optimizer is told to use the covering index. This arm
# exists because creating the index alone changes almost nothing: MySQL keeps
# its original plan, reaching budget_data by eq_ref on the pre-existing unique
# key uq_budget_sub_year (sub_scheme_id, fiscal_year_id). Measuring only
# "before/after CREATE INDEX" would report that the index is useless, which is
# not the same statement as "the index does not help this query".
FORCED_QUERY = TARGET_QUERY.replace(
    "FROM budget_data b",
    "FROM budget_data b FORCE INDEX (idx_budget_fy_sub_budget)",
)

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "results" / "index_benchmark.json"


def connect() -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
    )


def index_exists(cursor) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.statistics
        WHERE table_schema = %s AND table_name = 'budget_data' AND index_name = %s
        """,
        (DB_NAME, INDEX_NAME),
    )
    return cursor.fetchone()[0] > 0


def drop_index(cursor) -> None:
    if index_exists(cursor):
        cursor.execute(f"DROP INDEX {INDEX_NAME} ON budget_data")


def create_index(cursor) -> float:
    """Creates the index and returns how long the build took, in seconds."""
    start = time.perf_counter()
    cursor.execute(INDEX_DDL)
    return time.perf_counter() - start


def explain(cursor, query: str) -> dict[str, Any]:
    """Raw EXPLAIN FORMAT=JSON for `query`.

    Persisted verbatim: the optimizer's chosen access path is the part of this
    result that does not move between runs, unlike wall-clock timing.
    """
    cursor.execute(f"EXPLAIN FORMAT=JSON {query}")
    return json.loads(cursor.fetchone()[0])


def time_query(cursor, query: str, repeats: int) -> tuple[list[float], list]:
    """Times `query` `repeats` times after one discarded warm-up run.

    Returns the samples and the result set, so callers can confirm that a
    faster plan is still returning the same answer.
    """
    cursor.execute(query)
    rows = cursor.fetchall()  # warm-up, discarded from timing

    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        cursor.execute(query)
        rows = cursor.fetchall()
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples, rows


def summarise(samples: list[float]) -> dict[str, float]:
    return {
        "n": len(samples),
        "median_ms": round(statistics.median(samples), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "stdev_ms": round(statistics.stdev(samples), 3) if len(samples) > 1 else 0.0,
    }


def environment(cursor) -> dict[str, Any]:
    cursor.execute("SELECT VERSION()")
    version = cursor.fetchone()[0]

    cursor.execute("SELECT @@innodb_buffer_pool_size")
    pool_bytes = int(cursor.fetchone()[0])

    counts = {}
    for table in TRACKED_TABLES:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mysql_version": version,
        "innodb_buffer_pool_mb": round(pool_bytes / 1024 / 1024, 1),
        # platform.release() reports "10" on Windows 11; the build disambiguates.
        "os": f"{platform.system()} {platform.release()} (build {platform.version()})",
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "row_counts": counts,
        "table_sizes_mb": table_sizes_mb(cursor),
        "buffer_pool_flushed_between_arms": False,
        "note": (
            "Warm-cache measurements. The buffer pool cannot be flushed from a "
            "client connection without SUPER privileges or a server restart, so "
            "both arms run warm. The comparison between arms is valid; the "
            "absolute latencies are a best case."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3, help="drop/create cycles")
    parser.add_argument("--repeats", type=int, default=7, help="timed runs per arm")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    conn = connect()
    conn.autocommit = True
    cursor = conn.cursor()

    env = environment(cursor)
    print(f"MySQL {env['mysql_version']}  |  budget_data rows: "
          f"{env['row_counts']['budget_data']:,}")
    if env["row_counts"]["budget_data"] < 1000:
        print("\nWARNING: budget_data has under 1,000 rows. An index benchmark on a")
        print("table this small measures noise. Run scripts/generate_synthetic_data.py")
        print("first.\n")

    samples: dict[str, list[float]] = {"without_index": [], "with_index": [], "with_index_forced": []}
    explains: dict[str, Any] = {}
    answers: dict[str, list] = {}
    build_times: list[float] = []

    for trial in range(1, args.trials + 1):
        # ASCII only: the Windows console defaults to cp1252 and raises
        # UnicodeEncodeError on box-drawing characters.
        print(f"\n-- trial {trial}/{args.trials} " + "-" * 40)

        drop_index(cursor)
        explains.setdefault("without_index", explain(cursor, TARGET_QUERY))
        arm, rows = time_query(cursor, TARGET_QUERY, args.repeats)
        samples["without_index"].extend(arm)
        answers.setdefault("without_index", rows)
        print(f"  no index                : median {statistics.median(arm):8.1f} ms")

        build_times.append(create_index(cursor))
        cursor.execute("ANALYZE TABLE budget_data")
        cursor.fetchall()

        explains.setdefault("with_index", explain(cursor, TARGET_QUERY))
        arm, rows = time_query(cursor, TARGET_QUERY, args.repeats)
        samples["with_index"].extend(arm)
        answers.setdefault("with_index", rows)
        print(f"  index, optimizer's plan : median {statistics.median(arm):8.1f} ms"
              f"   (build {build_times[-1]:.1f}s)")

        explains.setdefault("with_index_forced", explain(cursor, FORCED_QUERY))
        arm, rows = time_query(cursor, FORCED_QUERY, args.repeats)
        samples["with_index_forced"].extend(arm)
        answers.setdefault("with_index_forced", rows)
        print(f"  index, FORCE INDEX      : median {statistics.median(arm):8.1f} ms")

    stats = {arm: summarise(vals) for arm, vals in samples.items()}
    baseline = stats["without_index"]["median_ms"]

    def improvement(arm: str) -> dict[str, float]:
        delta = baseline - stats[arm]["median_ms"]
        return {
            "absolute_ms": round(delta, 3),
            "percent": round((delta / baseline * 100.0) if baseline else 0.0, 2),
        }

    # A faster plan that returns a different answer is a bug, not an
    # optimisation. Compare every arm against the un-indexed baseline.
    reference = answers["without_index"]
    identical = {arm: (rows == reference) for arm, rows in answers.items()}

    results = {
        "environment": env,
        "index": {"name": INDEX_NAME, "ddl": INDEX_DDL,
                  "build_seconds_median": round(statistics.median(build_times), 2)},
        "query": TARGET_QUERY.strip(),
        "query_forced": FORCED_QUERY.strip(),
        "trials": args.trials,
        "repeats_per_trial": args.repeats,
        "results": stats,
        "improvement_vs_no_index": {
            "with_index": improvement("with_index"),
            "with_index_forced": improvement("with_index_forced"),
        },
        "all_arms_return_identical_rows": identical,
        "explain": explains,
        "raw_samples_ms": samples,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    for arm, label in (
        ("without_index", "no index"),
        ("with_index", "index, optimizer's plan"),
        ("with_index_forced", "index, FORCE INDEX"),
    ):
        s = stats[arm]
        line = f"  {label:<24} {s['median_ms']:8.1f} ms  (median of {s['n']})"
        if arm != "without_index":
            line += f"   {improvement(arm)['percent']:+6.1f}%"
        print(line)
    print("=" * 64)

    if not all(identical.values()):
        differing = [a for a, ok in identical.items() if not ok]
        print(f"\n  WARNING: these arms returned different rows: {differing}")
    else:
        print("  all three arms returned identical rows")

    print(f"\nwrote {args.output}")

    cursor.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
