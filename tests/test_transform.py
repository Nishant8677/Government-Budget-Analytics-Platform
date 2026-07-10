"""
tests/test_transform.py
────────────────────────
Unit tests for the ETL Transform and Extract phases.

These tests cover:
  • Extract:   FileNotFoundError on missing data file
  • Transform: Summary row removal, forward-fill, numeric coercion,
               column validation, required-column error handling
  • Fiscal-year column mapping: correct actuals/budget/revised per year
  • Fiscal-year record expansion: zero exclusion, key structure

No database connection is required — all tests are pure Python.

Run with:
    pytest tests/ -v
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from etl.extract import extract
from etl.transform import (
    FISCAL_YEAR_COLUMN_MAP,
    get_fiscal_year_records,
    transform,
)


# ══════════════════════════════════════════════════════════════════════════════
# Extract
# ══════════════════════════════════════════════════════════════════════════════

class TestExtract:
    def test_raises_file_not_found_on_missing_csv(self, tmp_path: Path) -> None:
        """extract() must raise FileNotFoundError with a helpful message."""
        missing = tmp_path / "nonexistent.csv"
        with pytest.raises(FileNotFoundError, match="Raw data file not found"):
            extract(filepath=missing)

    def test_returns_dataframe_for_valid_csv(self, tmp_path: Path) -> None:
        """extract() should return a DataFrame when the file exists."""
        csv = tmp_path / "test.csv"
        csv.write_text(
            "Group,Scheme,Sub Scheme Name,Programme Name,Sub Programme Name,"
            "Major Head Code,Actuals 2020-2021,Budget 2021-2022,Revised 2021-2022,Budget 2022-2023\n"
            "Tax Revenue,Corp Tax,Collections,NA,NA,20,1000,2000,1800,2200\n"
        )
        df = extract(filepath=csv)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Transform — Row Filtering
# ══════════════════════════════════════════════════════════════════════════════

class TestTransformRowFiltering:
    def test_removes_total_rows(self, sample_raw_df: pd.DataFrame) -> None:
        """Rows where Group starts with 'Total-' must be removed."""
        clean = transform(sample_raw_df)
        assert not any(
            str(g).startswith("Total") for g in clean["Group"]
        ), "Summary/Total rows should be filtered out"

    def test_removes_na_string_group_rows(self, sample_raw_df: pd.DataFrame) -> None:
        """Rows where Group is the literal string 'NA' must be removed."""
        clean = transform(sample_raw_df)
        assert "NA" not in clean["Group"].values

    def test_only_tax_revenue_rows_remain(self, sample_raw_df: pd.DataFrame) -> None:
        """After filtering, every remaining row must have Group == 'Tax Revenue'."""
        clean = transform(sample_raw_df)
        assert (clean["Group"] == "Tax Revenue").all()


# ══════════════════════════════════════════════════════════════════════════════
# Transform — Forward Fill
# ══════════════════════════════════════════════════════════════════════════════

class TestTransformForwardFill:
    def test_forward_fills_group_column(self, sample_clean_df: pd.DataFrame) -> None:
        """No NaN values should remain in the Group column after transform."""
        assert sample_clean_df["Group"].isna().sum() == 0

    def test_forward_fills_scheme_column(self, sample_clean_df: pd.DataFrame) -> None:
        """No NaN values should remain in the Scheme column after transform."""
        assert sample_clean_df["Scheme"].isna().sum() == 0

    def test_forward_fills_sub_scheme_name(self, sample_clean_df: pd.DataFrame) -> None:
        """No NaN values should remain in the Sub Scheme Name column."""
        assert sample_clean_df["Sub Scheme Name"].isna().sum() == 0


# ══════════════════════════════════════════════════════════════════════════════
# Transform — Numeric Coercion
# ══════════════════════════════════════════════════════════════════════════════

class TestTransformNumericCoercion:
    def test_fills_missing_actuals_with_zero(self, sample_clean_df: pd.DataFrame) -> None:
        assert sample_clean_df["Actuals 2020-2021"].isna().sum() == 0

    def test_fills_missing_budget_2022_with_zero(self, sample_clean_df: pd.DataFrame) -> None:
        assert sample_clean_df["Budget 2022-2023"].isna().sum() == 0

    def test_major_head_code_is_integer_dtype(self, sample_clean_df: pd.DataFrame) -> None:
        """Major Head Code must be coerced to int (original CSV has floats/NaN)."""
        assert pd.api.types.is_integer_dtype(sample_clean_df["Major Head Code"])

    def test_drops_programme_name_columns(self, sample_clean_df: pd.DataFrame) -> None:
        """Useless Programme Name columns should be absent after transform."""
        assert "Programme Name" not in sample_clean_df.columns
        assert "Sub Programme Name" not in sample_clean_df.columns


# ══════════════════════════════════════════════════════════════════════════════
# Transform — Validation
# ══════════════════════════════════════════════════════════════════════════════

class TestTransformValidation:
    def test_raises_value_error_on_missing_required_columns(
        self, missing_cols_df: pd.DataFrame
    ) -> None:
        """transform() must raise ValueError listing the missing columns."""
        with pytest.raises(ValueError, match="missing required columns"):
            transform(missing_cols_df)


# ══════════════════════════════════════════════════════════════════════════════
# Fiscal-Year Column Map — Correctness of the Bug Fix
# ══════════════════════════════════════════════════════════════════════════════

class TestFiscalYearColumnMap:
    """
    These tests document the correct year-to-column mapping and act as a
    regression guard against re-introducing the original pythoncode.py bug,
    where 'Revised 2021-2022' was incorrectly propagated to the 2022-2023 rows.
    """

    def test_2020_2021_has_actuals_column_only(self) -> None:
        fy = FISCAL_YEAR_COLUMN_MAP["2020-2021"]
        assert fy["actuals"] == "Actuals 2020-2021"
        assert fy["budget"] is None
        assert fy["revised"] is None

    def test_2021_2022_has_budget_and_revised_columns(self) -> None:
        fy = FISCAL_YEAR_COLUMN_MAP["2021-2022"]
        assert fy["actuals"] is None
        assert fy["budget"] == "Budget 2021-2022"
        assert fy["revised"] == "Revised 2021-2022"

    def test_2022_2023_has_budget_column_only(self) -> None:
        fy = FISCAL_YEAR_COLUMN_MAP["2022-2023"]
        assert fy["actuals"] is None
        assert fy["budget"] == "Budget 2022-2023"
        assert fy["revised"] is None

    def test_exactly_three_fiscal_years_defined(self) -> None:
        assert len(FISCAL_YEAR_COLUMN_MAP) == 3


# ══════════════════════════════════════════════════════════════════════════════
# get_fiscal_year_records — Record Expansion
# ══════════════════════════════════════════════════════════════════════════════

class TestGetFiscalYearRecords:
    def test_output_has_required_keys(self, sample_clean_df: pd.DataFrame) -> None:
        """Every record must contain the full set of required keys."""
        records = get_fiscal_year_records(sample_clean_df)
        assert len(records) > 0
        required = {
            "group_name", "scheme_name", "sub_scheme_name",
            "major_head_code", "fiscal_year",
            "actuals", "budget", "revised",
        }
        for record in records:
            assert required.issubset(record.keys()), (
                f"Record missing keys: {required - set(record.keys())}"
            )

    def test_all_zero_records_are_excluded(self, all_zero_df: pd.DataFrame) -> None:
        """Records where all financial values are 0 should not appear in output."""
        records = get_fiscal_year_records(all_zero_df)
        assert len(records) == 0, "All-zero records must be excluded from the load payload"

    def test_2020_records_have_null_budget_and_revised(
        self, sample_clean_df: pd.DataFrame
    ) -> None:
        """2020-2021 records must have actuals set and budget/revised as None."""
        records = get_fiscal_year_records(sample_clean_df)
        fy_2020 = [r for r in records if r["fiscal_year"] == "2020-2021"]
        assert len(fy_2020) > 0, "Expected at least one 2020-2021 record"
        for r in fy_2020:
            assert r["budget"] is None, f"budget should be None for 2020-2021, got {r['budget']}"
            assert r["revised"] is None, f"revised should be None for 2020-2021, got {r['revised']}"

    def test_2021_records_have_null_actuals(self, sample_clean_df: pd.DataFrame) -> None:
        """2021-2022 records must have budget/revised set and actuals as None."""
        records = get_fiscal_year_records(sample_clean_df)
        fy_2021 = [r for r in records if r["fiscal_year"] == "2021-2022"]
        assert len(fy_2021) > 0, "Expected at least one 2021-2022 record"
        for r in fy_2021:
            assert r["actuals"] is None, f"actuals should be None for 2021-2022, got {r['actuals']}"

    def test_2022_records_have_null_actuals_and_revised(
        self, sample_clean_df: pd.DataFrame
    ) -> None:
        """2022-2023 records must have budget set, actuals and revised as None."""
        records = get_fiscal_year_records(sample_clean_df)
        fy_2022 = [r for r in records if r["fiscal_year"] == "2022-2023"]
        assert len(fy_2022) > 0, "Expected at least one 2022-2023 record"
        for r in fy_2022:
            assert r["actuals"] is None
            assert r["revised"] is None

    def test_fiscal_year_values_are_valid_strings(
        self, sample_clean_df: pd.DataFrame
    ) -> None:
        """All fiscal_year values must be one of the known years."""
        records = get_fiscal_year_records(sample_clean_df)
        valid_years = set(FISCAL_YEAR_COLUMN_MAP.keys())
        for r in records:
            assert r["fiscal_year"] in valid_years
