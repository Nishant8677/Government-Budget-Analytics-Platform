"""Profiles the queries the Streamlit dashboard actually issues.

Every performance figure this project has ever published -- the covering index,
the buffer pool result, the original 80-85% claim -- describes
`Q2` in scripts/benchmark.py, which filters on MAX(fiscal_year_id). Nothing had
profiled the dashboard itself.

That distinction turns out to matter more than any of the tuning, because of how
the synthetic data is laid out:

    fiscal_year_id 1-3     2020-2021 .. 2022-2023   real data, 166 rows TOTAL
    fiscal_year_id 403-412 2000-2001 .. 2009-2010   synthetic, 92,153 rows each

The dashboard selects fiscal years by NAME, and the names it offers come from
the real rows. MAX(fiscal_year_id) is 412 -- a backdated synthetic year. So the
benchmark measures 92,153 rows and the dashboard reads 41.

This script times each dashboard query at the size it really runs, so the
repository has a number for the thing users actually wait on.

    python scripts/benchmark_dashboard.py
"""

from __future__ import annotations

import argparse
import json
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

OUTPUT = Path(__file__).resolve().parent.parent / "results" / "dashboard_benchmark.json"

# Lifted from app/dashboard.py. Names match the loader functions there so a
# reader can line them up.
QUERIES: list[tuple[str, str]] = [
    ("load_fiscal_years",
     "SELECT fiscal_year FROM fiscal_years ORDER BY fiscal_year"),
    ("load_schemes",
     "SELECT scheme_name FROM schemes ORDER BY scheme_name"),
    ("load_fiscal_year_totals",
     "SELECT * FROM v_fiscal_year_totals ORDER BY fiscal_year"),
    ("load_scheme_summary(2021-2022)",
     "SELECT * FROM v_scheme_summary WHERE fiscal_year = '2021-2022' "
     "ORDER BY total_actuals DESC"),
    ("load_scheme_summary(All Years)",
     "SELECT s.scheme_name, g.group_name, ROUND(SUM(bd.actuals),2) AS total_actuals, "
     "ROUND(SUM(bd.budget),2) AS total_budget, ROUND(SUM(bd.revised),2) AS total_revised "
     "FROM budget_data bd JOIN sub_schemes ss ON bd.sub_scheme_id=ss.sub_scheme_id "
     "JOIN schemes s ON ss.scheme_id=s.scheme_id JOIN `groups` g ON s.group_id=g.group_id "
     "GROUP BY s.scheme_name, g.group_name ORDER BY total_actuals DESC"),
    ("insights: top scheme",
     "SELECT scheme_name, ROUND(SUM(total_actuals),2) AS total_actuals "
     "FROM v_scheme_summary WHERE fiscal_year='2020-2021' "
     "GROUP BY scheme_name ORDER BY total_actuals DESC LIMIT 1"),
    ("insights: highest growth",
     "SELECT scheme_name, MAX(CASE WHEN fiscal_year='2021-2022' THEN total_budget END) AS b21, "
     "MAX(CASE WHEN fiscal_year='2022-2023' THEN total_budget END) AS b22 "
     "FROM ( SELECT * FROM v_scheme_summary WHERE fiscal_year='2021-2022' UNION ALL "
     "       SELECT * FROM v_scheme_summary WHERE fiscal_year='2022-2023' ) y "
     "GROUP BY scheme_name HAVING b21>0 AND b22>0 ORDER BY (b22-b21)/b21 DESC LIMIT 1"),
    ("insights: largest revision",
     "SELECT scheme_name, ROUND(total_budget,2) AS budget, ROUND(total_revised,2) AS revised, "
     "ROUND(ABS(total_revised-total_budget),2) AS deviation FROM v_scheme_summary "
     "WHERE fiscal_year='2021-2022' AND total_budget>0 AND total_revised IS NOT NULL "
     "ORDER BY deviation DESC LIMIT 1"),
    ("insights: avg utilisation",
     "SELECT ROUND(AVG((total_revised/total_budget)*100),2) AS avg_util FROM v_scheme_summary "
     "WHERE fiscal_year='2021-2022' AND total_budget>0 AND total_revised IS NOT NULL"),
    ("insights: schemes over budget",
     "SELECT COUNT(*) AS over_count FROM v_scheme_summary "
     "WHERE fiscal_year='2021-2022' AND total_revised > total_budget"),
    ("load_budget_overview(2021-2022)",
     "SELECT group_name, scheme_name, sub_scheme_name, major_head_code, fiscal_year, "
     "actuals, budget, revised, budget_utilization_pct FROM v_budget_overview "
     "WHERE fiscal_year = '2021-2022' "
     "ORDER BY group_name, scheme_name, sub_scheme_name, fiscal_year"),
]

# Run separately: unfiltered, this is every row in the database.
UNFILTERED_EXPLORER = (
    "load_budget_overview(All Years, All Schemes)",
    "SELECT group_name, scheme_name, sub_scheme_name, major_head_code, fiscal_year, "
    "actuals, budget, revised, budget_utilization_pct FROM v_budget_overview "
    "ORDER BY group_name, scheme_name, sub_scheme_name, fiscal_year",
)


def connect():
    return mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                                   password=DB_PASSWORD, database=DB_NAME, charset="utf8mb4")


def measure(cur, sql: str, repeats: int) -> tuple[dict, int]:
    cur.execute(sql)
    rows = cur.fetchall()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        cur.execute(sql)
        rows = cur.fetchall()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {
        "median_ms": round(statistics.median(samples), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "n": len(samples),
    }, len(rows)


def environment(cur) -> dict[str, Any]:
    cur.execute("SELECT VERSION()")
    version = cur.fetchone()[0]
    cur.execute("""SELECT fy.fiscal_year, COUNT(*) FROM budget_data bd
                   JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
                   GROUP BY fy.fiscal_year ORDER BY COUNT(*)""")
    dist = {y: n for y, n in cur.fetchall()}
    real = {y: n for y, n in dist.items() if n < 1000}
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mysql_version": version,
        "os": f"{platform.system()} {platform.release()}",
        "rows_per_fiscal_year": dist,
        "real_data_rows_total": sum(real.values()),
        "note": (
            "Fiscal years with fewer than 1,000 rows are the real dataset "
            "(2020-2021 onward). The rest are backdated synthetic years created "
            "by scripts/generate_synthetic_data.py. The dashboard selects years "
            "by name and the real years are what it shows."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--skip-full-explorer", action="store_true",
                        help="skip the unfiltered Data Explorer query")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    env = environment(cur)

    print(f"MySQL {env['mysql_version']}  |  real data: "
          f"{env['real_data_rows_total']} rows across "
          f"{sum(1 for n in env['rows_per_fiscal_year'].values() if n < 1000)} fiscal years")
    print(f"{'query':<38} {'median':>10} {'rows':>9}")
    print("-" * 60)

    results: dict[str, Any] = {}
    total = 0.0
    for name, sql in QUERIES:
        stats, nrows = measure(cur, sql, args.repeats)
        results[name] = {**stats, "rows_returned": nrows, "sql": " ".join(sql.split())}
        total += stats["median_ms"]
        print(f"{name:<38} {stats['median_ms']:>8.2f}ms {nrows:>9,}", flush=True)

    print("-" * 60)
    print(f"{'PAGE LOAD (sum of medians)':<38} {total:>8.2f}ms")

    if not args.skip_full_explorer:
        name, sql = UNFILTERED_EXPLORER
        print(f"\nrunning {name} -- this returns every row in the database", flush=True)
        stats, nrows = measure(cur, sql, max(1, args.repeats // 3))
        results[name] = {**stats, "rows_returned": nrows, "sql": " ".join(sql.split())}
        print(f"{name:<38} {stats['median_ms']:>8.2f}ms {nrows:>9,}")

    payload = {
        "environment": env,
        "repeats": args.repeats,
        "queries": results,
        "page_load_sum_of_medians_ms": round(total, 2),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.output}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
