#!/usr/bin/env python3
"""
setup_db.py
───────────
Step 1 of the setup sequence.

Validates .env configuration, then creates the MySQL database and applies
the schema + views.  Safe to run multiple times (idempotent).

Creates the MySQL database and applies the schema + views.
Safe to run multiple times (idempotent).

Usage:
    python setup_db.py
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path so all package imports resolve correctly
sys.path.insert(0, str(Path(__file__).parent))

from config.validator import validate  # noqa: E402
from database.setup import setup      # noqa: E402

if __name__ == "__main__":
    validate()   # exits cleanly if .env is misconfigured
    try:
        setup()
    except SystemExit:
        # setup() already logged the error; propagate the exit code
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected error during database setup: {exc}")
        sys.exit(1)
