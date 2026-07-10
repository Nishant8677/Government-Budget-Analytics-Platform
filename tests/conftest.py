"""
tests/conftest.py
─────────────────
Shared pytest fixtures for the Government Budget Analytics Platform test suite.

All fixtures are pure Python — no database connection required.
The raw DataFrame mimics the actual CSV structure (sparse, with NA rows and
summary rows) so the fixtures are realistic regression anchors.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure project root is importable from test context
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ── Sample raw data mirrors the real CSV structure ────────────────────────────
# Key characteristics reproduced:
#   • None entries in Group/Scheme (forward-fill scenarios)
#   • Summary rows (Group = "Total-Tax Revenue", "Grand Total")
#   • NA-like rows (Group = None after forward-fill would be "Total")
#   • Missing financial values (NA → float NaN in pandas)

_RAW = {
    "Group": [
        "Tax Revenue",   # row 0  — Corporation Tax › Collections
        None,            # row 1  — ffill → Tax Revenue  › Surcharge
        None,            # row 2  — ffill → Tax Revenue  › Penalties
        "Tax Revenue",   # row 3  — GST › CGST
        None,            # row 4  — ffill → Tax Revenue  › IGST
        "NA",            # row 5  — stray NA string (summary-like)
        "Total-Tax Revenue",  # row 6  — grand total row
    ],
    "Scheme": [
        "Corporation Tax",
        None,            # ffill → Corporation Tax
        None,            # ffill → Corporation Tax
        "Goods and Services Tax (GST)",
        None,            # ffill → GST
        None,
        None,
    ],
    "Sub Scheme Name": [
        "Collections",
        "Surcharge",
        "Penalties",
        "Central Goods and Services Tax (CGST)",
        "Integrated Goods and Services Tax (IGST)",
        None,
        None,
    ],
    "Programme Name":     ["NA"] * 7,
    "Sub Programme Name": ["NA"] * 7,
    "Major Head Code": [
        20.0, 20.0, 20.0, 5.0, 8.0, None, None,
    ],
    "Actuals 2020-2021":  [412013.46, 14078.58, 79.47,  456333.97, 7251.43,  None,  1426287.08],
    "Budget 2021-2022":   [473365.39, 52596.15, None,   530000.00, None,     None,  1545396.53],
    "Revised 2021-2022":  [549519.23, 61057.69, None,   570000.00, None,     None,  1765144.65],
    "Budget 2022-2023":   [623076.92, 69230.77, None,   660000.00, None,     None,  1934770.66],
}

_ALL_ZERO_ROW = {
    "Group":              ["Tax Revenue"],
    "Scheme":             ["TestScheme"],
    "Sub Scheme Name":    ["ZeroSub"],
    "Programme Name":     ["NA"],
    "Sub Programme Name": ["NA"],
    "Major Head Code":    [20.0],
    "Actuals 2020-2021":  [0.0],
    "Budget 2021-2022":   [0.0],
    "Revised 2021-2022":  [0.0],
    "Budget 2022-2023":   [0.0],
}

_MISSING_COLS = {
    "A": [1, 2],
    "B": [3, 4],
}


@pytest.fixture(scope="session")
def sample_raw_df() -> pd.DataFrame:
    """Raw DataFrame mimicking the CSV before any transformation."""
    return pd.DataFrame(_RAW)


@pytest.fixture(scope="session")
def sample_clean_df(sample_raw_df: pd.DataFrame) -> pd.DataFrame:
    """Cleaned DataFrame produced by etl.transform.transform()."""
    from etl.transform import transform
    return transform(sample_raw_df)


@pytest.fixture
def all_zero_df() -> pd.DataFrame:
    """Single-row DataFrame where all financial values are zero."""
    return pd.DataFrame(_ALL_ZERO_ROW)


@pytest.fixture
def missing_cols_df() -> pd.DataFrame:
    """DataFrame missing all required columns — used to test error handling."""
    return pd.DataFrame(_MISSING_COLS)
