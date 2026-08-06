"""
etl/pipeline.py
───────────────
ETL orchestrator: wires together Extract → Transform → Load.

This module is the single entry point for the data pipeline.
Callers (run_etl.py, tests, CI) only need to call run_pipeline().
"""
from __future__ import annotations

from etl.extract import extract
from etl.load import load
from etl.transform import get_fiscal_year_records, transform
from utils.logger import get_logger

logger = get_logger(__name__)

_SEPARATOR = "=" * 60


def run_pipeline() -> None:
    """
    Execute the full ETL pipeline.

    Steps
    ─────
    1. Extract  — Read raw CSV from data/Details_of_Tax_Revenue.csv
    2. Transform — Clean, validate, reshape into fiscal-year records
    3. Load      — Upsert records into MySQL with full transaction support

    Raises
    ──────
    FileNotFoundError   If the raw data file is missing.
    ValueError          If required columns are absent in the CSV.
    mysql.connector.Error  If the database load fails.
    """
    logger.info(_SEPARATOR)
    logger.info("BudgetIQ ETL Pipeline — Starting")
    logger.info(_SEPARATOR)

    # ── Extract ───────────────────────────────────────────────────────────────
    raw_df = extract()

    # ── Transform ─────────────────────────────────────────────────────────────
    clean_df = transform(raw_df)
    records = get_fiscal_year_records(clean_df)

    # ── Load ──────────────────────────────────────────────────────────────────
    load(records)

    logger.info(_SEPARATOR)
    logger.info("Government Budget Analytics Platform ETL Pipeline - Complete [OK]")
    logger.info(_SEPARATOR)
