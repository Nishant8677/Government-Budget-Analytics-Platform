#!/usr/bin/env python3
"""
run_etl.py
──────────
Step 2 of the setup sequence.

Runs the full ETL pipeline:
  Extract  → data/Details_of_Tax_Revenue.csv
  Transform → clean, validate, reshape
  Load      → MySQL database (budgetiq)

Run setup_db.py first to create the database and schema.

Usage:
    python run_etl.py
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path so all package imports resolve correctly
sys.path.insert(0, str(Path(__file__).parent))

from config.validator import validate  # noqa: E402
from etl.pipeline import run_pipeline  # noqa: E402

if __name__ == "__main__":
    validate()   # exits cleanly if .env is misconfigured
    try:
        run_pipeline()
    except FileNotFoundError as exc:
        print(f"\n[ERROR] {exc}")
        print("Make sure data/Details_of_Tax_Revenue.csv is present.")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] ETL pipeline failed: {exc}")
        sys.exit(1)
