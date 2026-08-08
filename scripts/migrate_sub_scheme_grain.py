"""Migrates sub_schemes to the four-column identity key and reloads the real data.

Why this is a reload and not a backfill
───────────────────────────────────────
The old unique key, (sub_scheme_name, scheme_id), merged line items the source
keeps separate. Nine levies under Customs > Import Duties share a sub-scheme
name, so eight of them were overwritten by ON DUPLICATE KEY UPDATE as the loader
walked past. Those rows are not damaged in the database -- they are absent. The
information only exists in data/Details_of_Tax_Revenue.csv, so the fix is to
widen the key and load the real slice again from source.

Scope
─────
Real and synthetic rows share zero sub_scheme_id values (verified, not assumed;
this script re-checks before deleting). So the destructive part touches 166
budget_data rows and 87 sub_schemes and leaves the 921,530-row synthetic
substrate that every figure in PERFORMANCE.md was measured against untouched.

Steps
─────
1. Verify the real/synthetic split is still disjoint. Abort if it is not.
2. ALTER sub_schemes: add programme_name, sub_programme_name; swap the unique
   key. Existing synthetic rows take '' for both, which cannot collide because
   they were already unique on the narrower key.
3. Delete the real budget_data rows, then the real sub_schemes. That order is
   forced: the foreign key is ON DELETE RESTRICT.
4. Re-run the ETL, which now carries both identity columns.
5. Report before/after and verify the recovered total against the source.

    python scripts/migrate_sub_scheme_grain.py --dry-run   (inspect only)
    python scripts/migrate_sub_scheme_grain.py

A backup of the affected slice is in backups/pre_grain_migration.json. Steps 2
and 3 are not reversible from the database alone.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent))

from etl.extract import extract  # noqa: E402
from etl.load import load  # noqa: E402
from etl.transform import get_fiscal_year_records, transform  # noqa: E402
from utils.db import get_connection  # noqa: E402

REAL_YEAR_FLOOR = "2020-2021"
CSV = Path(__file__).resolve().parent.parent / "data" / "Details_of_Tax_Revenue.csv"

ADD_COLUMNS = """
ALTER TABLE `sub_schemes`
  ADD COLUMN programme_name     VARCHAR(128) NOT NULL DEFAULT '' AFTER scheme_id,
  ADD COLUMN sub_programme_name VARCHAR(128) NOT NULL DEFAULT '' AFTER programme_name
"""
SWAP_KEY = """
ALTER TABLE `sub_schemes`
  DROP INDEX uq_sub_scheme_scheme,
  ADD UNIQUE KEY uq_sub_scheme_identity
      (sub_scheme_name, scheme_id, programme_name, sub_programme_name)
"""


def scalar(cur, sql: str, params: tuple = ()) -> Any:
    cur.execute(sql, params)
    return cur.fetchone()[0]


def survey(cur) -> dict[str, Any]:
    real_subs = scalar(cur, """
        SELECT COUNT(DISTINCT bd.sub_scheme_id) FROM budget_data bd
        JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
        WHERE fy.fiscal_year >= %s""", (REAL_YEAR_FLOOR,))
    real_rows = scalar(cur, """
        SELECT COUNT(*) FROM budget_data bd
        JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
        WHERE fy.fiscal_year >= %s""", (REAL_YEAR_FLOOR,))
    overlap = scalar(cur, """
        SELECT COUNT(*) FROM (
          SELECT bd.sub_scheme_id FROM budget_data bd
            JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
            WHERE fy.fiscal_year >= %s
          INTERSECT
          SELECT bd.sub_scheme_id FROM budget_data bd
            JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
            WHERE fy.fiscal_year < %s) x""", (REAL_YEAR_FLOOR, REAL_YEAR_FLOOR))
    actuals = scalar(cur, """
        SELECT COALESCE(SUM(bd.actuals), 0) FROM budget_data bd
        JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
        WHERE fy.fiscal_year >= %s""", (REAL_YEAR_FLOOR,))
    return {
        "sub_schemes_total": scalar(cur, "SELECT COUNT(*) FROM sub_schemes"),
        "budget_data_total": scalar(cur, "SELECT COUNT(*) FROM budget_data"),
        "real_sub_schemes": real_subs,
        "real_rows": real_rows,
        "real_actuals": float(actuals),
        "overlap": overlap,
    }


def has_column(cur, column: str) -> bool:
    return scalar(cur, """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sub_schemes'
          AND column_name = %s""", (column,)) > 0


def source_actuals_total() -> float:
    """What the CSV actually holds, independent of anything in the database."""
    records = get_fiscal_year_records(transform(extract(CSV)))
    return sum(r["actuals"] or 0.0 for r in records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="survey and report; change nothing")
    args = parser.parse_args()

    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()

    try:
        before = survey(cur)
        expected = source_actuals_total()

        print("=" * 72)
        print("sub_schemes grain migration")
        print("=" * 72)
        print(f"  sub_schemes            : {before['sub_schemes_total']:,}"
              f"  ({before['real_sub_schemes']} real)")
        print(f"  budget_data            : {before['budget_data_total']:,}"
              f"  ({before['real_rows']} real)")
        print(f"  real actuals in DB     : {before['real_actuals']:,.2f} crore")
        print(f"  real actuals in source : {expected:,.2f} crore")
        print(f"  discarded today        : {expected - before['real_actuals']:,.2f} crore")
        print(f"  real/synthetic overlap : {before['overlap']}")

        if before["overlap"] != 0:
            print("\n  ABORT: real and synthetic rows share sub_schemes. The delete "
                  "below would take synthetic data with it. Migration not safe.",
                  file=sys.stderr)
            return 1

        if args.dry_run:
            print("\n  --dry-run: nothing changed.")
            return 0

        print("\n  [1/3] altering sub_schemes", flush=True)
        if has_column(cur, "programme_name"):
            print("        columns already present, skipping ADD")
        else:
            cur.execute(ADD_COLUMNS)
            print("        added programme_name, sub_programme_name")
        try:
            cur.execute(SWAP_KEY)
            print("        swapped uq_sub_scheme_scheme -> uq_sub_scheme_identity")
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised
            if "check that column/key exists" in str(exc).lower():
                print("        key already swapped, skipping")
            else:
                raise

        print("  [2/3] deleting the real slice", flush=True)
        cur.execute("""
            DELETE bd FROM budget_data bd
            JOIN fiscal_years fy ON bd.fiscal_year_id = fy.fiscal_year_id
            WHERE fy.fiscal_year >= %s""", (REAL_YEAR_FLOOR,))
        print(f"        budget_data rows deleted : {cur.rowcount}")
        # Safe only because the overlap check above passed: any sub_scheme with
        # no remaining budget_data row belonged solely to the real slice.
        cur.execute("""
            DELETE ss FROM sub_schemes ss
            LEFT JOIN budget_data bd ON bd.sub_scheme_id = ss.sub_scheme_id
            WHERE bd.sub_scheme_id IS NULL""")
        print(f"        sub_schemes deleted      : {cur.rowcount}")

        print("  [3/3] reloading from source", flush=True)
        records = get_fiscal_year_records(transform(extract(CSV)))
        load(records)
        print(f"        records loaded           : {len(records)}")

        after = survey(cur)
        print("\n" + "=" * 72)
        print(f"  sub_schemes  : {before['sub_schemes_total']:,} -> {after['sub_schemes_total']:,}"
              f"   (real {before['real_sub_schemes']} -> {after['real_sub_schemes']})")
        print(f"  budget_data  : {before['budget_data_total']:,} -> {after['budget_data_total']:,}"
              f"   (real {before['real_rows']} -> {after['real_rows']})")
        print(f"  real actuals : {before['real_actuals']:,.2f} -> {after['real_actuals']:,.2f} crore")
        print(f"  recovered    : {after['real_actuals'] - before['real_actuals']:,.2f} crore")

        drift = abs(after["real_actuals"] - expected)
        if drift > 0.01:
            print(f"\n  FAIL: database holds {after['real_actuals']:,.2f} but the source "
                  f"has {expected:,.2f} (drift {drift:,.2f}). Rows are still being lost.",
                  file=sys.stderr)
            return 1
        print(f"\n  PASS: database total matches the source exactly ({expected:,.2f} crore).")
        print("=" * 72)
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
