#!/usr/bin/env python3
"""
manage.py
─────────
Command-line interface for the Government Budget Analytics Platform.

Usage:
    python manage.py setup      # Step 1: create database + apply schema + views
    python manage.py load       # Step 2: run full ETL pipeline
    python manage.py validate   # Check .env configuration without touching DB
    python manage.py dashboard  # Launch the Streamlit analytics dashboard
    python manage.py test       # Run the pytest test suite
    python manage.py --help     # Show this help message

Examples:
    python manage.py setup
    python manage.py load
    python manage.py dashboard
    python manage.py test -v

All commands validate your .env configuration before running
(except 'validate' and 'test' which are safe without a DB).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Command implementations ───────────────────────────────────────────────────

def cmd_validate(args: argparse.Namespace) -> int:
    """Validate .env configuration and report status."""
    from config.validator import validate
    validate()   # exits with code 1 and a message on failure
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Create database and apply schema + views."""
    from config.validator import validate
    from database.setup import setup

    validate()
    setup()
    print("\n[OK] Database setup complete.")
    print("   Next step:  python manage.py load\n")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    """Run the full ETL pipeline (Extract → Transform → Load)."""
    from config.validator import validate
    from etl.pipeline import run_pipeline

    validate()
    run_pipeline()
    print("\n[OK] ETL pipeline complete.")
    print("   Next step:  python manage.py dashboard\n")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Launch the Streamlit analytics dashboard."""
    dashboard_path = PROJECT_ROOT / "app" / "dashboard.py"
    if not dashboard_path.exists():
        print(f"[ERROR] Dashboard not found at {dashboard_path}")
        return 1

    print("Launching Government Budget Analytics Platform dashboard …")
    print("Press Ctrl+C to stop.\n")
    result = subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(dashboard_path)],
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode


def cmd_test(args: argparse.Namespace) -> int:
    """Run the pytest test suite."""
    tests_path = PROJECT_ROOT / "tests"
    if not tests_path.exists():
        print("[ERROR] tests/ directory not found.")
        return 1

    extra = args.pytest_args or []
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_path), *extra],
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode


# ── CLI parser ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Government Budget Analytics Platform — management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  setup      Create the MySQL database and apply schema/views
  load       Run the ETL pipeline to load CSV data into MySQL
  validate   Check .env configuration without touching the database
  dashboard  Launch the Streamlit analytics dashboard
  test       Run the pytest test suite

Quick start for a fresh install:
  1.  cp .env.example .env          (fill in your credentials)
  2.  python manage.py setup
  3.  python manage.py load
  4.  python manage.py dashboard
""",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    sub.add_parser("validate",  help="Validate .env configuration")
    sub.add_parser("setup",     help="Create database + apply schema")
    sub.add_parser("load",      help="Run ETL pipeline")
    sub.add_parser("dashboard", help="Launch Streamlit dashboard")

    test_p = sub.add_parser("test", help="Run pytest test suite")
    test_p.add_argument(
        "pytest_args",
        nargs="*",
        help="Extra arguments forwarded to pytest (e.g., -v, -k test_name)",
    )

    return parser


COMMANDS = {
    "validate":  cmd_validate,
    "setup":     cmd_setup,
    "load":      cmd_load,
    "dashboard": cmd_dashboard,
    "test":      cmd_test,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    handler = COMMANDS[args.command]
    sys.exit(handler(args))


if __name__ == "__main__":
    main()
