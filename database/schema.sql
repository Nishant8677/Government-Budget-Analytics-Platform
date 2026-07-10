-- =============================================================================
-- database/schema.sql
-- BudgetIQ — Indian Tax Revenue Analytics Database
--
-- Engine   : InnoDB   (ACID-compliant transactions, FK enforcement)
-- Charset  : utf8mb4  (full Unicode, including emoji / special chars)
-- Collation: utf8mb4_unicode_ci
--
-- Design: 3NF normalisation with a dedicated fiscal_years reference table
-- and a major_heads reference table extracted from SubSchemes for clarity.
--
-- Table hierarchy:
--   groups → schemes → sub_schemes ← major_heads
--                          ↓
--                      budget_data ← fiscal_years
-- =============================================================================

-- ── Reference: Tax Groups ─────────────────────────────────────────────────────
-- A Group is the top-level budgetary category (e.g., "Tax Revenue").
CREATE TABLE IF NOT EXISTS `groups` (
    group_id    INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    group_name  VARCHAR(255)     NOT NULL,
    created_at  TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (group_id),
    UNIQUE KEY  uq_group_name (group_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Top-level budget group (e.g., Tax Revenue)';


-- ── Reference: Schemes ────────────────────────────────────────────────────────
-- A Scheme belongs to exactly one Group (e.g., "Corporation Tax" → "Tax Revenue").
CREATE TABLE IF NOT EXISTS `schemes` (
    scheme_id   INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    scheme_name VARCHAR(255)     NOT NULL,
    group_id    INT UNSIGNED     NOT NULL,
    created_at  TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (scheme_id),
    -- Scheme names are unique within a group, but the same name CAN appear
    -- under different groups (rare but allowed by design).
    UNIQUE KEY  uq_scheme_group  (scheme_name, group_id),
    INDEX       idx_scheme_group (group_id),

    CONSTRAINT fk_scheme_group
        FOREIGN KEY (group_id) REFERENCES `groups` (group_id)
        ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Budget scheme (e.g., Corporation Tax, Customs)';


-- ── Reference: Major Heads ────────────────────────────────────────────────────
-- Major Head Code is a government accounting classification.
-- Extracted into its own table to eliminate redundancy and allow enrichment.
CREATE TABLE IF NOT EXISTS `major_heads` (
    major_head_id   INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    major_head_code SMALLINT UNSIGNED NOT NULL,

    PRIMARY KEY (major_head_id),
    UNIQUE KEY  uq_major_head_code (major_head_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Government accounting major head code (e.g., 20 = Corporation Tax)';


-- ── Core: Sub-Schemes ─────────────────────────────────────────────────────────
-- A Sub-Scheme is the lowest level of the budget hierarchy.
-- E.g., "Collections" under "Corporation Tax".
CREATE TABLE IF NOT EXISTS `sub_schemes` (
    sub_scheme_id   INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    sub_scheme_name VARCHAR(255)    NOT NULL,
    scheme_id       INT UNSIGNED    NOT NULL,
    major_head_id   INT UNSIGNED,           -- NULL if major head unknown
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (sub_scheme_id),
    UNIQUE KEY  uq_sub_scheme_scheme  (sub_scheme_name, scheme_id),
    INDEX       idx_sub_scheme_scheme (scheme_id),
    INDEX       idx_sub_scheme_head   (major_head_id),

    CONSTRAINT fk_sub_scheme_scheme
        FOREIGN KEY (scheme_id) REFERENCES `schemes` (scheme_id)
        ON DELETE RESTRICT ON UPDATE CASCADE,

    CONSTRAINT fk_sub_scheme_major_head
        FOREIGN KEY (major_head_id) REFERENCES `major_heads` (major_head_id)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Lowest-level budget line item (e.g., Collections, Surcharge)';


-- ── Reference: Fiscal Years ───────────────────────────────────────────────────
-- Normalised to avoid repeating the year string in every budget row.
-- Format enforced: "YYYY-YYYY" (e.g., "2021-2022").
CREATE TABLE IF NOT EXISTS `fiscal_years` (
    fiscal_year_id  INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    fiscal_year     VARCHAR(9)      NOT NULL,

    PRIMARY KEY (fiscal_year_id),
    UNIQUE KEY  uq_fiscal_year (fiscal_year),
    -- Enforce format at the DB level (application also validates before insert)
    CONSTRAINT chk_fiscal_year_format
        CHECK (fiscal_year REGEXP '^[0-9]{4}-[0-9]{4}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Fiscal year reference (e.g., 2020-2021, 2021-2022)';


-- ── Core: Budget Data ─────────────────────────────────────────────────────────
-- One row = one sub-scheme × one fiscal year.
-- Actuals, Budget, Revised may be NULL when the government did not publish
-- that figure for a particular year (common in the source dataset).
CREATE TABLE IF NOT EXISTS `budget_data` (
    budget_id       INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    sub_scheme_id   INT UNSIGNED    NOT NULL,
    fiscal_year_id  INT UNSIGNED    NOT NULL,
    actuals         DECIMAL(15,2),          -- in ₹ Crore (NULL = not published)
    budget          DECIMAL(15,2),
    revised         DECIMAL(15,2),
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (budget_id),
    -- Each sub-scheme can appear only once per fiscal year.
    UNIQUE KEY  uq_budget_sub_year        (sub_scheme_id, fiscal_year_id),
    INDEX       idx_budget_sub_scheme     (sub_scheme_id),
    INDEX       idx_budget_fiscal_year    (fiscal_year_id),

    CONSTRAINT fk_budget_sub_scheme
        FOREIGN KEY (sub_scheme_id) REFERENCES `sub_schemes` (sub_scheme_id)
        ON DELETE RESTRICT ON UPDATE CASCADE,

    CONSTRAINT fk_budget_fiscal_year
        FOREIGN KEY (fiscal_year_id) REFERENCES `fiscal_years` (fiscal_year_id)
        ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Budget figures per sub-scheme per fiscal year (₹ Crore)';
