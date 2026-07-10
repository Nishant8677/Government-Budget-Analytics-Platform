"""
etl/extract.py
──────────────
Extract phase: read the raw CSV into a pandas DataFrame.

Responsibilities
────────────────
• Locate the raw data file (path from config, never hardcoded).
• Validate the file exists before opening it.
• Read with explicit NA handling so downstream code sees real NaN values.
• Log row/column counts for traceability.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from config.settings import RAW_DATA_PATH
from utils.logger import get_logger

logger = get_logger(__name__)


def extract(filepath: Path = RAW_DATA_PATH) -> pd.DataFrame:
    """
    Read the raw tax-revenue CSV file into a DataFrame.

    Parameters
    ──────────
    filepath : Path
        Path to the CSV.  Defaults to config.settings.RAW_DATA_PATH so the
        pipeline can run without arguments; tests can pass a custom path.

    Returns
    ───────
    pd.DataFrame
        Raw DataFrame with original column names and NaN for missing values.

    Raises
    ──────
    FileNotFoundError
        If the CSV is absent at *filepath*.
    """
    logger.info("Extracting data from: %s", filepath)

    if not filepath.exists():
        raise FileNotFoundError(
            f"Raw data file not found: {filepath}\n"
            f"Expected location: data/Details_of_Tax_Revenue.csv"
        )

    df = pd.read_csv(
        filepath,
        # Treat both "NA" and empty strings as missing values
        na_values=["NA", "N/A", "n/a", ""],
        keep_default_na=True,
    )

    logger.info(
        "Extracted %d rows × %d columns from '%s'.",
        len(df), len(df.columns), filepath.name
    )
    return df
