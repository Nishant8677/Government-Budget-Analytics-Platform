"""
etl/transform.py
────────────────
Transform phase: clean, validate and reshape the raw DataFrame.

Responsibilities
────────────────
• Validate that required columns are present.
• Drop irrelevant columns.
• Forward-fill hierarchical categorical columns.
• Remove summary/subtotal rows that are not actual line items.
• Coerce financial values to float; fill missing with 0.0.
• Convert Major Head Code to integer.
• Explode the wide format (one column per year) into a long format
  (one row per sub-scheme × fiscal year) — this is the canonical
  representation for the relational schema.

Design note on the year-to-column mapping
──────────────────────────────────────────
The original code (pythoncode.py) had a critical bug: it used
  `if 'Actuals' in year_col`
where year_col was the *column name string* (e.g., "Budget 2021-2022").
This meant:
  • "Budget 2022-2023" correctly got Budget — because 'Budget' is in the name.
  • But "Revised 2021-2022" was ALWAYS inserted regardless of which iteration
    was active, so Budget 2022-2023 rows also received the Revised 2021-2022
    value — completely wrong.

The correct mapping (source: original government dataset structure) is:
  2020-2021 → actuals  = "Actuals 2020-2021",   budget = NULL, revised = NULL
  2021-2022 → actuals  = NULL,  budget = "Budget 2021-2022", revised = "Revised 2021-2022"
  2022-2023 → actuals  = NULL,  budget = "Budget 2022-2023", revised = NULL

This mapping is captured in FISCAL_YEAR_COLUMN_MAP and is now explicit,
documented, and tested independently of column name string matching.
"""
from __future__ import annotations

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Column requirements ───────────────────────────────────────────────────────

REQUIRED_COLUMNS: list[str] = [
    "Group",
    "Scheme",
    "Sub Scheme Name",
    "Major Head Code",
    "Actuals 2020-2021",
    "Budget 2021-2022",
    "Revised 2021-2022",
    "Budget 2022-2023",
]

COLUMNS_TO_DROP: list[str] = ["Programme Name", "Sub Programme Name"]

# ── Fiscal-year to column mapping ─────────────────────────────────────────────
# Each fiscal year maps to the exact source columns for actuals/budget/revised.
# None means the government did not publish that figure for that year.

FISCAL_YEAR_COLUMN_MAP: dict[str, dict[str, str | None]] = {
    "2020-2021": {
        "actuals": "Actuals 2020-2021",
        "budget":  None,
        "revised": None,
    },
    "2021-2022": {
        "actuals": None,
        "budget":  "Budget 2021-2022",
        "revised": "Revised 2021-2022",
    },
    "2022-2023": {
        "actuals": None,
        "budget":  "Budget 2022-2023",
        "revised": None,
    },
}


# ── Public API ────────────────────────────────────────────────────────────────

def transform(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and filter the raw budget DataFrame.

    Returns a DataFrame where every row is a real budget line item
    (no summary/total rows) with clean categorical and numeric columns.
    """
    logger.info("Starting transformation …")

    # 1. Validate columns
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(
            f"Raw data is missing required columns: {missing_cols}\n"
            f"Found columns: {df.columns.tolist()}"
        )

    # 2. Drop useless columns (ignore if already absent — allows re-runs)
    df = df.drop(columns=COLUMNS_TO_DROP, errors="ignore").copy()

    # 3. Forward-fill hierarchical categorical columns.
    #    The source CSV uses blank cells to continue the group/scheme from
    #    the previous row (a common spreadsheet convention).
    for col in ["Group", "Scheme", "Sub Scheme Name", "Major Head Code"]:
        df[col] = df[col].ffill()

    # 4. Remove summary / grand-total rows.
    #    These rows have Group values like "Total-Tax Revenue", "Grand Total",
    #    or are subtotal rows where Group is NaN (after ffill, shouldn't happen,
    #    but defensive check).
    original_len = len(df)
    df = df[
        df["Group"].notna()
        & (df["Group"].astype(str).str.strip() == "Tax Revenue")
        & df["Sub Scheme Name"].notna()
    ].copy()
    removed = original_len - len(df)
    logger.info(
        "Removed %d summary/total rows.  %d data rows remain.", removed, len(df)
    )

    # 5. Coerce financial columns to numeric, leaving unparseable values as NaN.
    #    Deliberately NOT filled with 0.0. The source omits figures the
    #    government did not publish, and budget_data models that as NULL -- a
    #    zero would assert the government budgeted nothing, which is a different
    #    claim. The distinction is invisible to SUM but not to AVG or to the
    #    utilisation percentage, where a fabricated zero drags the result down
    #    while a NULL is correctly excluded.
    financial_cols = [
        "Actuals 2020-2021",
        "Budget 2021-2022",
        "Revised 2021-2022",
        "Budget 2022-2023",
    ]
    for col in financial_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 6. Convert Major Head Code to integer (source has it as float after NA fills).
    df["Major Head Code"] = (
        pd.to_numeric(df["Major Head Code"], errors="coerce")
        .fillna(0)
        .astype(int)
    )

    # 7. Drop any rows still missing critical identifiers (defensive).
    df = df.dropna(subset=["Sub Scheme Name", "Scheme", "Group"])

    logger.info(
        "Transformation complete.  %d clean rows ready for fiscal-year expansion.",
        len(df),
    )
    return df.reset_index(drop=True)


def _figure(row: pd.Series, column: str | None) -> float | None:
    """Read one financial figure, mapping 'not published' to None.

    Two different absences collapse to the same result, which is intended.
    `column is None` means the government publishes no such figure for that
    fiscal year at all -- see FISCAL_YEAR_COLUMN_MAP. A NaN means the column
    exists but this row has no value in it. Both are "no figure", and
    budget_data stores both as NULL.

    Guarding NaN explicitly matters because float('nan') is not None and would
    survive every None check downstream, reaching the database as a value the
    DECIMAL column cannot represent.
    """
    if column is None:
        return None
    value = row[column]
    return None if pd.isna(value) else float(value)


def get_fiscal_year_records(df: pd.DataFrame) -> list[dict]:
    """
    Explode the wide-format cleaned DataFrame into one dict per
    (sub_scheme, fiscal_year) combination — ready for the Load phase.

    Each returned dict has keys:
        group_name, scheme_name, sub_scheme_name, major_head_code,
        fiscal_year, actuals, budget, revised
    """
    records: list[dict] = []

    for _, row in df.iterrows():
        for fiscal_year, col_map in FISCAL_YEAR_COLUMN_MAP.items():
            actuals = _figure(row, col_map["actuals"])
            budget  = _figure(row, col_map["budget"])
            revised = _figure(row, col_map["revised"])

            # Skip rows carrying no information: everything absent, or every
            # figure present but zero. A row that is partly absent and partly
            # non-zero is kept, and the absent parts stay None.
            values = [v for v in (actuals, budget, revised) if v is not None]
            if not values or all(v == 0.0 for v in values):
                continue

            records.append(
                {
                    "group_name":      str(row["Group"]).strip(),
                    "scheme_name":     str(row["Scheme"]).strip(),
                    "sub_scheme_name": str(row["Sub Scheme Name"]).strip(),
                    "major_head_code": int(row["Major Head Code"]),
                    "fiscal_year":     fiscal_year,
                    "actuals":         actuals,
                    "budget":          budget,
                    "revised":         revised,
                }
            )

    logger.info(
        "Fiscal-year expansion complete.  %d records generated for loading.",
        len(records),
    )
    return records
