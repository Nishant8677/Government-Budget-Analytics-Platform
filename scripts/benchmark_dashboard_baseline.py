"""Re-measures the pre-fix dashboard queries that commit 9947a6d superseded.

That commit reported a page load of 217,076 ms falling to 29,971 ms. Only the
second number has an artifact. benchmark_dashboard.py writes to a fixed path, so
running it after the fix overwrote the run that produced the first, and
results/dashboard_benchmark.json has exactly one commit holding only the
after-state. The before-column survived in prose and in a commit message.

This script rebuilds that column. It is deliberately separate from
benchmark_dashboard.py and writes to a different file, because a single script
owning a single filename is what destroyed the evidence the first time.

Three of the four figures are recoverable read-only: v_scheme_summary was
bypassed by the fix, not modified, so the pre-fix queries against it still run
as they did. The fourth is not -- v_fiscal_year_totals had its sub_schemes join
removed, so reproducing its 11,144 ms needs the old definition back. This script
swaps it in, measures, and restores the definition it found, in a finally block,
including on error and on Ctrl-C.

It also captures two things the original run never did: EXPLAIN for the
equality, IN and OR variants side by side, which is the direct evidence for the
pushdown claim, and a row-identity check across all three forms.

    python scripts/benchmark_dashboard_baseline.py

Expect roughly 15 minutes. load_scheme_summary("All Years") alone runs about
205 s per execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mysql.connector

sys.path.append(str(Path(__file__).resolve().parent.parent))

from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER  # noqa: E402

OUTPUT = Path(__file__).resolve().parent.parent / "results" / "dashboard_baseline_benchmark.json"

# The v_fiscal_year_totals body as it stood at 9947a6d^, before the sub_schemes
# join was removed. Restoring it is the only way to reproduce the 11,144 ms.
VIEW_PREFIX = """CREATE OR REPLACE VIEW v_fiscal_year_totals AS
SELECT
    fy.fiscal_year,
    ROUND(SUM(bd.actuals),  2) AS total_actuals,
    ROUND(SUM(bd.budget),   2) AS total_budget,
    ROUND(SUM(bd.revised),  2) AS total_revised,
    COUNT(DISTINCT ss.sub_scheme_id) AS line_items
FROM        budget_data   bd
JOIN        sub_schemes   ss ON bd.sub_scheme_id  = ss.sub_scheme_id
JOIN        fiscal_years  fy ON bd.fiscal_year_id  = fy.fiscal_year_id
GROUP BY    fy.fiscal_year
ORDER BY    fy.fiscal_year"""

# Pre-fix loader bodies, lifted verbatim from app/dashboard.py at 9947a6d^.
PREFIX_ALL_YEARS = (
    "SELECT   scheme_name, group_name, "
    "         SUM(total_actuals) AS total_actuals, "
    "         SUM(total_budget)  AS total_budget, "
    "         SUM(total_revised) AS total_revised "
    "FROM     v_scheme_summary "
    "GROUP BY scheme_name, group_name "
    "ORDER BY total_actuals DESC"
)

_GROWTH = (
    "SELECT scheme_name, "
    "       MAX(CASE WHEN fiscal_year='2021-2022' THEN total_budget END) AS b21, "
    "       MAX(CASE WHEN fiscal_year='2022-2023' THEN total_budget END) AS b22 "
    "FROM   {source} "
    "GROUP  BY scheme_name HAVING b21>0 AND b22>0 "
    "ORDER  BY (b22-b21)/b21 DESC LIMIT 1"
)

# The three predicate forms. Same rows, three ways of asking -- the whole
# pushdown argument rests on IN and OR behaving alike and unlike the equality
# pair, so OR is a control, not a curiosity.
GROWTH_IN = _GROWTH.format(
    source="v_scheme_summary WHERE fiscal_year IN ('2021-2022','2022-2023')")
GROWTH_OR = _GROWTH.format(
    source="v_scheme_summary WHERE (fiscal_year='2021-2022' OR fiscal_year='2022-2023')")
GROWTH_UNION = _GROWTH.format(
    source="( SELECT * FROM v_scheme_summary WHERE fiscal_year='2021-2022' UNION ALL "
           "  SELECT * FROM v_scheme_summary WHERE fiscal_year='2022-2023' ) y")


def connect() -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                                   password=DB_PASSWORD, database=DB_NAME, charset="utf8mb4")


def fingerprint(rows: list[tuple]) -> str:
    """Order-insensitive digest, so two plans returning the same set compare equal."""
    payload = sorted(repr(r) for r in rows)
    return hashlib.sha256("\n".join(payload).encode()).hexdigest()[:16]


def measure(cur, sql: str, repeats: int) -> tuple[dict[str, Any], int, str]:
    cur.execute(sql)                      # discarded warm-up, as in benchmark_dashboard.py
    rows = cur.fetchall()
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        cur.execute(sql)
        rows = cur.fetchall()
        samples.append((time.perf_counter() - start) * 1000.0)
    stats = {
        "median_ms": round(statistics.median(samples), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "n": len(samples),
        "samples_ms": [round(s, 3) for s in samples],
    }
    return stats, len(rows), fingerprint(rows)


def explain(cur, sql: str) -> dict[str, Any]:
    cur.execute("EXPLAIN FORMAT=JSON " + sql)
    return json.loads(cur.fetchone()[0])


def current_view_ddl(cur, view: str) -> str:
    cur.execute(f"SHOW CREATE VIEW `{view}`")
    return cur.fetchone()[1]


def restore_view(cur, view: str, ddl: str) -> None:
    """Put back exactly the definition we found.

    SHOW CREATE VIEW returns a bare `CREATE ALGORITHM=... VIEW`, with no
    OR REPLACE, so replaying it verbatim fails with 1050 "table already exists"
    -- which is precisely how a restore silently does not happen. Inject
    OR REPLACE; if that still fails, drop and recreate rather than leave the
    view in its pre-fix state.
    """
    replaced = re.sub(r"^CREATE ", "CREATE OR REPLACE ", ddl, count=1)
    try:
        cur.execute(replaced)
    except mysql.connector.Error:
        cur.execute(f"DROP VIEW IF EXISTS `{view}`")
        cur.execute(ddl)


def environment(cur) -> dict[str, Any]:
    cur.execute("SELECT VERSION(), @@innodb_buffer_pool_size/1024/1024")
    version, pool_mb = cur.fetchone()
    cur.execute("""SELECT fy.fiscal_year, COUNT(*) FROM budget_data bd
                   JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
                   GROUP BY fy.fiscal_year ORDER BY COUNT(*)""")
    dist = {y: n for y, n in cur.fetchall()}
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mysql_version": version,
        # platform.release() reports "10" on Windows 11, so record the build too
        # rather than leave the artifact disagreeing with the docs.
        "os": f"{platform.system()} {platform.release()} (build {platform.version()})",
        "innodb_buffer_pool_mb": float(pool_mb),
        "rows_per_fiscal_year": dist,
        "real_data_rows_total": sum(n for n in dist.values() if n < 1000),
        "synthetic_rows_total": sum(n for n in dist.values() if n >= 1000),
        "note": (
            "Pre-fix measurements, reproducing the queries app/dashboard.py issued "
            "before commit 9947a6d. Compare against results/dashboard_benchmark.json, "
            "which holds the post-fix state of the same queries."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3,
                        help="timed runs per query after a discarded warm-up")
    parser.add_argument("--skip-view-swap", action="store_true",
                        help="skip load_fiscal_year_totals, the only query needing DDL")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    if args.output.resolve().name == "dashboard_benchmark.json":
        parser.error("refusing to overwrite the post-fix artifact; choose another --output")

    conn = connect()
    cur = conn.cursor()
    env = environment(cur)
    results: dict[str, Any] = {}

    print(f"MySQL {env['mysql_version']}  |  buffer pool {env['innodb_buffer_pool_mb']:.0f} MB  |  "
          f"{env['real_data_rows_total']} real + {env['synthetic_rows_total']:,} synthetic rows")
    print(f"{'query':<44} {'median':>12} {'rows':>7}  digest")
    print("-" * 82)

    def checkpoint() -> None:
        """Persist after every measurement.

        The first attempt at this run was killed partway through and every
        completed measurement went with it, because the script only wrote at the
        end. A query that costs 205 s to produce should survive the process that
        produced it.
        """
        args.output.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "environment": env,
            "repeats": args.repeats,
            "complete": False,
            "queries": {k: v for k, v in results.items()
                        if isinstance(v, dict) and "median_ms" in v},
            **{k: v for k, v in results.items() if not (isinstance(v, dict) and "median_ms" in v)},
        }
        args.output.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    def run(label: str, sql: str, repeats: int | None = None) -> str:
        stats, nrows, digest = measure(cur, sql, repeats or args.repeats)
        results[label] = {**stats, "rows_returned": nrows, "row_digest": digest,
                          "sql": " ".join(sql.split())}
        print(f"{label:<44} {stats['median_ms']:>10.2f}ms {nrows:>7,}  {digest}", flush=True)
        checkpoint()
        return digest

    # ── Read-only: the three figures recoverable without touching the schema ──
    d_in = run("insights: highest growth (pre-fix, IN)", GROWTH_IN)
    d_or = run("insights: highest growth (OR control)", GROWTH_OR)
    d_union = run("insights: highest growth (post-fix, UNION ALL)", GROWTH_UNION)

    cur.execute("SELECT COUNT(*) FROM v_scheme_summary")
    group_count = cur.fetchone()[0]
    results["v_scheme_summary_group_count"] = group_count
    print(f"{'v_scheme_summary groups':<44} {group_count:>12,}", flush=True)

    # EXPLAIN for all three forms. If the equality variants push the predicate
    # into the view and the disjunctions do not, it shows up here as a
    # difference in rows examined, not merely in wall-clock.
    results["explain"] = {
        "growth_in": explain(cur, GROWTH_IN),
        "growth_or": explain(cur, GROWTH_OR),
        "growth_union_all": explain(cur, GROWTH_UNION),
    }
    checkpoint()

    results["row_identity"] = {
        "in_vs_or": d_in == d_or,
        "in_vs_union_all": d_in == d_union,
        "note": "All three predicate forms must return the same rows; a faster "
                "plan returning different rows is a bug, not an optimisation.",
    }
    if not (d_in == d_or == d_union):
        print("\n!! row digests differ across predicate forms -- the rewrite is not "
              "equivalent and the speedup is meaningless", file=sys.stderr)

    print(f"\nrunning load_scheme_summary(All Years) pre-fix -- ~205 s per execution, "
          f"{args.repeats + 1} executions", flush=True)
    run("load_scheme_summary(All Years) (pre-fix)", PREFIX_ALL_YEARS)

    # ── The one figure that needs DDL ────────────────────────────────────────
    if args.skip_view_swap:
        print("\nskipping load_fiscal_year_totals (--skip-view-swap)")
    else:
        restore_ddl = current_view_ddl(cur, "v_fiscal_year_totals")
        print("\nswapping v_fiscal_year_totals to its pre-fix definition "
              "(sub_schemes join restored); it will be put back on exit", flush=True)
        try:
            cur.execute(VIEW_PREFIX)
            run("load_fiscal_year_totals (pre-fix)",
                "SELECT * FROM v_fiscal_year_totals ORDER BY fiscal_year")
        finally:
            restore_view(cur, "v_fiscal_year_totals", restore_ddl)
            after = current_view_ddl(cur, "v_fiscal_year_totals")
            restored = "sub_schemes" not in after
            results["view_swap"] = {
                "view": "v_fiscal_year_totals",
                "restored": restored,
                "restored_ddl_matches_original": after == restore_ddl,
            }
            print(f"restored v_fiscal_year_totals -- sub_schemes join absent: {restored}",
                  flush=True)
            if not restored:
                print("!! v_fiscal_year_totals did NOT restore; re-run "
                      "database/views.sql before using the dashboard", file=sys.stderr)

    page_load = sum(
        v["median_ms"] for k, v in results.items()
        if isinstance(v, dict) and "median_ms" in v and "(OR control)" not in k
        and "(post-fix," not in k
    )
    payload = {
        "environment": env,
        "repeats": args.repeats,
        "queries": {k: v for k, v in results.items() if isinstance(v, dict) and "median_ms" in v},
        "v_scheme_summary_group_count": group_count,
        "row_identity": results["row_identity"],
        "explain": results["explain"],
        "view_swap": results.get("view_swap"),
        "complete": True,
        "prefix_queries_sum_of_medians_ms": round(page_load, 2),
        "page_load_note": (
            "This sum covers only the queries this script re-measures. The full "
            "217,076 ms page load in PERFORMANCE.md also includes eight queries "
            "the fix did not touch, whose post-fix medians are in "
            "results/dashboard_benchmark.json and total 14.4 ms."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.output}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
