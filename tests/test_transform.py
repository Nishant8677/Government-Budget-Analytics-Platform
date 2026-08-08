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
    def test_missing_figures_stay_missing(self, sample_clean_df: pd.DataFrame) -> None:
        """An absent figure must survive transform as NaN, not become 0.0.

        These two tests previously asserted the opposite -- that no NaN
        survived, because transform() filled with 0.0. That conflated "the
        government did not publish this" with "the government published zero",
        which budget_data models as NULL versus 0 and which AVG and the
        utilisation percentage treat differently.
        """
        assert sample_clean_df["Budget 2022-2023"].isna().sum() > 0

    def test_present_figures_are_numeric(self, sample_clean_df: pd.DataFrame) -> None:
        """Coercion still applies to the values that are there."""
        col = sample_clean_df["Actuals 2020-2021"]
        assert pd.api.types.is_float_dtype(col)
        assert col.notna().sum() > 0

    def test_unparseable_values_become_nan(self) -> None:
        """A non-numeric cell coerces to NaN rather than raising or persisting."""
        df = pd.DataFrame({
            "Group": ["Tax Revenue"], "Scheme": ["S"], "Sub Scheme Name": ["Sub"],
            "Programme Name": ["NA"], "Sub Programme Name": ["NA"],
            "Major Head Code": [20.0],
            "Actuals 2020-2021": ["not a number"],
            "Budget 2021-2022": [10.0], "Revised 2021-2022": [None],
            "Budget 2022-2023": [None],
        })
        out = transform(df)
        assert pd.isna(out["Actuals 2020-2021"].iloc[0])

    def test_major_head_code_is_integer_dtype(self, sample_clean_df: pd.DataFrame) -> None:
        """Major Head Code must be coerced to int (original CSV has floats/NaN)."""
        assert pd.api.types.is_integer_dtype(sample_clean_df["Major Head Code"])

    def test_keeps_programme_name_columns(self, sample_clean_df: pd.DataFrame) -> None:
        """Programme columns must survive transform -- they carry identity.

        This test previously asserted the opposite, calling them useless. They
        are the only thing distinguishing nine separate levies under
        Customs > Import Duties, and dropping them merged those into one row.
        See ADR 7.
        """
        assert "Programme Name" in sample_clean_df.columns
        assert "Sub Programme Name" in sample_clean_df.columns

    def test_blank_programme_becomes_empty_string_not_nan(
        self, sample_clean_df: pd.DataFrame
    ) -> None:
        """Blank identity cells normalise to '' so the unique key can constrain them."""
        for col in ("Programme Name", "Sub Programme Name"):
            assert sample_clean_df[col].isna().sum() == 0
            assert sample_clean_df[col].map(type).eq(str).all()


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

    def test_sibling_levies_survive_as_separate_records(self) -> None:
        """Line items sharing a sub-scheme name must not merge -- ADR 7.

        Modelled on Customs > Import Duties, where the source carries several
        levies under one sub-scheme name distinguished only by programme. The
        old key was (sub_scheme_name, scheme_id), so these collapsed to one row
        and the loader kept whichever arrived last.
        """
        df = pd.DataFrame({
            "Group": ["Tax Revenue"] * 3,
            "Scheme": ["Customs"] * 3,
            "Sub Scheme Name": ["Import Duties"] * 3,
            "Programme Name": ["Basic Duties", "Health Cess", "Social Welfare Surcharge"],
            "Sub Programme Name": ["Other than debits", "", ""],
            "Major Head Code": [37.0] * 3,
            "Actuals 2020-2021": [106525.93, -13.52, 13447.39],
            "Budget 2021-2022": [None] * 3,
            "Revised 2021-2022": [None] * 3,
            "Budget 2022-2023": [None] * 3,
        })
        records = get_fiscal_year_records(transform(df))
        assert len(records) == 3, "each levy must produce its own record"
        assert {r["programme_name"] for r in records} == {
            "Basic Duties", "Health Cess", "Social Welfare Surcharge"
        }
        assert sum(r["actuals"] for r in records) == pytest.approx(119959.80)

    def test_subtotal_rows_are_excluded(self) -> None:
        """"Total-" labelled rows are sums of their siblings, not line items.

        Loading them double-counts. Measured on the real dataset before this was
        fixed: 5 such rows inflated actuals by 10.5% and revised by 14.2%.
        """
        df = pd.DataFrame({
            "Group": ["Tax Revenue"] * 3,
            "Scheme": ["Customs"] * 3,
            "Sub Scheme Name": ["Import Duties", "Import Duties", "Total-Import Duties"],
            "Programme Name": ["Basic Duties", "Total-Basic Duties", ""],
            "Sub Programme Name": ["", "", ""],
            "Major Head Code": [37.0] * 3,
            "Actuals 2020-2021": [100.0, 100.0, 100.0],
            "Budget 2021-2022": [None] * 3,
            "Revised 2021-2022": [None] * 3,
            "Budget 2022-2023": [None] * 3,
        })
        records = get_fiscal_year_records(transform(df))
        assert len(records) == 1, "both subtotal rows must be dropped"
        assert records[0]["actuals"] == 100.0

    def test_no_nan_reaches_the_load_payload(self, sample_clean_df: pd.DataFrame) -> None:
        """Absent figures must be None, never NaN.

        The regression guard for dropping fillna(0.0): float('nan') is not None,
        so it passes every `is not None` check and would reach a DECIMAL column
        that cannot represent it.
        """
        for record in get_fiscal_year_records(sample_clean_df):
            for field in ("actuals", "budget", "revised"):
                value = record[field]
                assert value is None or not pd.isna(value), (
                    f"{field} is NaN in {record['sub_scheme_name']} "
                    f"{record['fiscal_year']} -- it should be None"
                )

    def test_missing_figure_becomes_none_not_zero(self) -> None:
        """A partly-published row keeps its figure and nulls the rest.

        The row below has a 2022-23 budget and no 2021-22 figures. The 2022-23
        record must survive with budget set; nothing may be invented as 0.0.
        """
        df = pd.DataFrame({
            "Group": ["Tax Revenue"], "Scheme": ["S"], "Sub Scheme Name": ["Sub"],
            "Programme Name": ["NA"], "Sub Programme Name": ["NA"],
            "Major Head Code": [20.0],
            "Actuals 2020-2021": [None],
            "Budget 2021-2022": [None],
            "Revised 2021-2022": [None],
            "Budget 2022-2023": [500.0],
        })
        records = get_fiscal_year_records(transform(df))
        assert [r["fiscal_year"] for r in records] == ["2022-2023"]
        assert records[0]["budget"] == 500.0
        assert records[0]["actuals"] is None
        assert records[0]["revised"] is None

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
