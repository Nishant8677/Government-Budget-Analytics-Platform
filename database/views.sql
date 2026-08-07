-- =============================================================================
-- database/views.sql
-- BudgetIQ — Analytical Views
--
-- Views serve three purposes:
--   1. Encapsulate complex multi-table joins so the application layer
--      only needs simple SELECT queries.
--   2. Provide a stable query API — underlying table structure can change
--      without breaking the Streamlit dashboard.
--   3. Avoid code duplication across ETL validation and the dashboard.
-- =============================================================================

-- ── v_budget_overview ─────────────────────────────────────────────────────────
-- Full denormalised view: one row per sub-scheme × fiscal year.
-- Used by the Data Explorer and export feature.
CREATE OR REPLACE VIEW v_budget_overview AS
SELECT
    g.group_name,
    s.scheme_name,
    ss.sub_scheme_name,
    mh.major_head_code,
    fy.fiscal_year,
    bd.actuals,
    bd.budget,
    bd.revised,
    -- Budget utilisation: how much of the allocated budget was actually spent
    CASE
        WHEN bd.budget IS NOT NULL AND bd.budget > 0
        THEN ROUND((bd.actuals / bd.budget) * 100, 2)
        ELSE NULL
    END AS budget_utilization_pct
FROM        budget_data   bd
JOIN        sub_schemes   ss ON bd.sub_scheme_id  = ss.sub_scheme_id
JOIN        schemes        s ON ss.scheme_id       = s.scheme_id
JOIN        `groups`       g ON s.group_id         = g.group_id
LEFT JOIN   major_heads   mh ON ss.major_head_id   = mh.major_head_id
JOIN        fiscal_years  fy ON bd.fiscal_year_id  = fy.fiscal_year_id;


-- ── v_scheme_summary ──────────────────────────────────────────────────────────
-- Aggregated totals per scheme per fiscal year.
-- Used for scheme-comparison bar charts and year-over-year trend analysis.
CREATE OR REPLACE VIEW v_scheme_summary AS
SELECT
    s.scheme_name,
    g.group_name,
    fy.fiscal_year,
    ROUND(SUM(bd.actuals),  2) AS total_actuals,
    ROUND(SUM(bd.budget),   2) AS total_budget,
    ROUND(SUM(bd.revised),  2) AS total_revised,
    COUNT(DISTINCT ss.sub_scheme_id) AS sub_scheme_count
FROM        budget_data   bd
JOIN        sub_schemes   ss ON bd.sub_scheme_id  = ss.sub_scheme_id
JOIN        schemes        s ON ss.scheme_id       = s.scheme_id
JOIN        `groups`       g ON s.group_id         = g.group_id
JOIN        fiscal_years  fy ON bd.fiscal_year_id  = fy.fiscal_year_id
GROUP BY    s.scheme_name, g.group_name, fy.fiscal_year;


-- ── v_fiscal_year_totals ──────────────────────────────────────────────────────
-- Grand totals per fiscal year across all schemes.
-- Used for the KPI cards and the national budget trend line, so this runs on
-- every dashboard page load and cannot be narrowed by a filter -- it is grand
-- totals by definition. It is the most expensive query the application issues.
--
-- It does NOT join sub_schemes. An earlier version did, solely to compute
-- COUNT(DISTINCT ss.sub_scheme_id) -- but sub_scheme_id is already a column on
-- budget_data, so the join fetched 92,240 rows to read a value it had in hand.
-- Removing it is provably safe rather than a judgement call: the join was an
-- INNER JOIN on a NOT NULL foreign key with ON DELETE RESTRICT, so it could
-- neither filter rows nor duplicate them. Measured at 921,696 rows the removal
-- is worth 3,451 ms (9,762 ms -> 6,311 ms, 35.4%) and returns identical rows.
-- See PERFORMANCE.md and results/dashboard_benchmark.json.
CREATE OR REPLACE VIEW v_fiscal_year_totals AS
SELECT
    fy.fiscal_year,
    ROUND(SUM(bd.actuals),  2) AS total_actuals,
    ROUND(SUM(bd.budget),   2) AS total_budget,
    ROUND(SUM(bd.revised),  2) AS total_revised,
    COUNT(DISTINCT bd.sub_scheme_id) AS line_items
FROM        budget_data   bd
JOIN        fiscal_years  fy ON bd.fiscal_year_id  = fy.fiscal_year_id
GROUP BY    fy.fiscal_year
ORDER BY    fy.fiscal_year;
