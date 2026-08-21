# 🏛️ Government Budget Analytics Platform

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![MySQL](https://img.shields.io/badge/MySQL-8.0+-4479A1?style=for-the-badge&logo=mysql&logoColor=white)](https://mysql.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)](https://streamlit.io)
[![Plotly](https://img.shields.io/badge/Plotly-5.0+-3F4F75?style=for-the-badge&logo=plotly&logoColor=white)](https://plotly.com)
[![pytest](https://img.shields.io/badge/pytest-29%20passing-brightgreen?style=for-the-badge&logo=pytest)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

> A database engineering portfolio project, and a record of measuring its own claims.
> A synthetic generator scales the dataset to ~922K rows, against which a covering index was
> built, benchmarked at 0.8% — inside the noise — and **rejected**; the real wins were server
> configuration and a MySQL predicate-pushdown limit. Includes a modular Python ETL pipeline,
> 29 automated tests, reproduction tests for two concurrency bugs, and a 6-tab Streamlit dashboard.
> Every published figure is regenerable from a committed artifact in `results/`.
---

## 📸 Screenshots

> *Run `python manage.py dashboard` to see the live dashboard.*

![Dashboard Demo](assets/screenshots/demo.webp)

| Overview Tab | Insights Tab |
|---|---|
| ![Overview](assets/screenshots/overview_tab.png) | ![Insights](assets/screenshots/insights_tab.png) |
| *(KPI cards + revenue trend + donut chart)* | *(Auto-generated budget intelligence)* |

| Query Console | Data Explorer |
|---|---|
| ![Query Console](assets/screenshots/query_console_tab.png) | ![Data Explorer](assets/screenshots/data_explorer_tab.png) |
| *(5 pre-built SQL domain queries)* | *(Searchable table + CSV/Excel export)* |

---

## 🏗️ Architecture

```text
User ⇄ Streamlit Dashboard ⇄ SQL Views ⇄ MySQL Database ⇦ Python ETL ⇦ Raw CSV Data
```

**Why MySQL?**
- **ACID transactions**: Ensures safe, all-or-nothing data ingestion.
- **Mature optimizer**: Crucial for evaluating execution plans and index behavior.
- **InnoDB support**: Provides Row-Level Locking and MVCC for concurrency control.
- **Relational schema**: Enforces strict referential integrity across the 6-table domain model.

![Architecture Diagram](assets/screenshots/architecture.png)

| Layer | Technology | Purpose |
|---|---|---|
| Data Source | CSV | Government of India Statement 14 |
| Extract | `pandas.read_csv` | Validates file, returns DataFrame |
| Transform | pandas | Forward-fill, remove summaries, reshape |
| Load | `mysql-connector-python` | Upsert; bad rows skipped, systemic failure rolls back |
| Database | MySQL 8 / InnoDB | 6-table normalised schema |
| Views | SQL `CREATE VIEW` | Analytical abstraction layer |
| Dashboard | Streamlit + Plotly | 6-tab interactive analytics |

---

## 🗄️ Database Schema (ER Diagram)

![ER Diagram](assets/screenshots/er_diagram.png)

```
groups (1) ──── (∞) schemes (1) ──── (∞) sub_schemes ──── (∞) budget_data
                                         sub_schemes (∞) ──── (1) major_heads
                                         budget_data (∞) ──── (1) fiscal_years
```

| Table | Rows | Key Constraints |
|---|---|---|
| `groups` | 1 | `UNIQUE(group_name)` |
| `schemes` | 23 | `UNIQUE(scheme_name, group_id)` |
| `major_heads` | 19 | `UNIQUE(major_head_code)` |
| `sub_schemes` | 92,257 | `UNIQUE(sub_scheme_name, scheme_id, programme_name, sub_programme_name)` |
| `fiscal_years` | 13 | `UNIQUE(fiscal_year)`, FORMAT CHECK |
| `budget_data` | 921,720 | `UNIQUE(sub_scheme_id, fiscal_year_id)` |

All tables: `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`. Every FK column has an explicit index.

---

## ⚙️ Engineering Documentation

This project was built to demonstrate senior-level database engineering concepts. Please review the following architectural deep-dives:

- [**Architecture Decisions (ADR)**](DECISIONS.md): Why MySQL? Why 3NF? Why Upserts? Why the covering index was rejected. Isolation level and locking reasoning live here too.
- [**Synthetic Data Generator**](DATA_GENERATOR.md): Scaling the database for performance testing.
- [**Index Engineering & Performance**](PERFORMANCE.md): Measuring the covering index and why it was not adopted; the buffer pool / adaptive hash index result; the predicate-pushdown finding; and an attempt to reproduce every figure months later.
- [**ADR 7 — Sub-scheme grain**](docs/ADR-007-scope-sub-scheme-grain.md): A data-loss bug, the natural key that fixes it, and the double-counting it exposed.

Concurrency behaviour can be exercised directly with
`scripts/simulate_concurrency.py` and `scripts/test_constraints.py`.

---

## ⚡ Performance

Measured on **921,696 rows — 921,530 synthetic and 166 real** (MySQL 8.0.43,
Windows 11, 12 logical CPUs, InnoDB buffer pool at the 128 MB default). The
split matters; see the caveat below. ADR 7 later corrected the real slice to 190
rows over 92,257 sub-schemes; no figure here was re-measured against it, because
none of them describe real rows. Reproduce with:

```bash
python scripts/benchmark_index.py --trials 3 --repeats 7
```

### Covering index: built, measured, not adopted

| Arm | Median | vs baseline |
|---|---|---|
| No index | 1800.9 ms | — |
| `idx_budget_fy_sub_budget` added | 1785.9 ms | +0.8% |
| Same index, `FORCE INDEX` | 1661.3 ms | +7.8% |

The 0.8% sits inside a ±133 ms run-to-run spread, so it is noise. `EXPLAIN` is
identical before and after `CREATE INDEX`: the optimizer never uses the new
index, because `budget_data` is already reached by `eq_ref` on the pre-existing
UNIQUE key `uq_budget_sub_year`. The index is therefore **not** in
`database/schema.sql`.

Full method, all raw samples, and the `EXPLAIN FORMAT=JSON` for every arm are in
[PERFORMANCE.md](PERFORMANCE.md) and `results/index_benchmark.json`.

> Earlier versions of this README credited this index with an 80–85% reduction.
> That index had never existed in the schema and no script in the repository
> recorded a measurement. The figures above replace it.

### What actually helped: buffer pool sizing — 32.7%

If the schema was not the constraint, the next question was what is. The server
was running `innodb_buffer_pool_size` at its **128 MB default** against a table
of comparable size.

| Buffer pool | Median | n |
|---|---|---|
| 128 MB (default) | 1783.5 ms | 21 |
| 1024 MB | **1200.7 ms** | 21 |

**582.8 ms — 32.7%.** Four times what the covering index achieves even when
forced, from a configuration line rather than a schema change. Arms are
interleaved rather than run in blocks, so a warming trend cannot masquerade as
the result.

This is **not** an I/O effect: both arms report a 100.000% buffer pool hit rate
with zero disk reads. The 128 MB pool already holds the working set.

The mechanism is the **adaptive hash index**, which is sized as a fraction of the
buffer pool. Toggling it turns the effect on and off:

| | 128 MB | 1024 MB | pool effect |
|---|---|---|---|
| AHI on (default) | 1783.5 ms | **1200.7 ms** | **+32.7%** |
| AHI off | 1663.5 ms | 1688.0 ms | −1.5% |

With the AHI disabled, pool size stops mattering entirely. And at 128 MB the AHI
is *actively harmful* — **7.2% slower** than turning it off — because it cannot
cover the working set and pays maintenance without collecting the benefit. At
1 GB that same feature is worth 28.9%. So the fix is not "cache more data"; the
data was already fully cached. It is giving the adaptive hash index enough room
to be worth its upkeep.

```bash
python scripts/benchmark_buffer_pool.py --rounds 4 --repeats 7
```

### The dashboard itself: 3 min 37 s → 30 s

Everything above measures `Q2` from `scripts/benchmark.py`. Profiling what
`app/dashboard.py` actually issues found the real bottleneck somewhere else
entirely — and it is not an indexing problem.

| Loader | Before | After |
|---|---|---|
| `load_scheme_summary("All Years")` | 205,039 ms | **23,039 ms** |
| `load_fiscal_year_totals` | 11,144 ms | **6,915 ms** |
| `insights: highest growth` | 873 ms | **2.4 ms** |
| all 8 other queries | 0.3–3.4 ms | unchanged |
| **Page load** | **217,076 ms** | **29,971 ms** |

Two queries were 99.5% of the page load. Three changes, each verified to return
byte-identical rows before being applied, cut it **7.2×**.

The mechanism is **predicate pushdown**, not indexes. MySQL 8 pushes a single
equality into a `GROUP BY` view but will not push a disjunction. `EXPLAIN` puts
the `IN` and `OR` forms at an identical `query_cost` of 22,695 — materialising
201,714 rows from `v_scheme_summary` — against **11.50** for a `UNION ALL` of two
equality filters, which materialises 80. Two orders of magnitude, and unlike the
wall-clock it does not move between runs.

`v_fiscal_year_totals` separately joined `sub_schemes` just to count a column
`budget_data` already had — which cost far more than the wasted fetch suggests,
because it forces roughly 1.2 million `eq_ref` probes.

```bash
python scripts/benchmark_dashboard.py --repeats 3
python scripts/benchmark_dashboard_baseline.py   # re-measures the Before column
```

> **The *Before* column was never persisted**, and a later attempt to reproduce
> it recovered the page-load figures but not the two middle rows. Both runs are
> reported in
> [PERFORMANCE.md](PERFORMANCE.md#reproducing-the-pre-fix-figures); the `7.2×`
> and the `8.9×` reproduced within 4%, the `1.6×` and the growth-query ratio did
> not.

> **A caveat that matters more than the numbers.** The synthetic generator
> backdates its rows to 2000–2010 while the real data sits in 2020–2023, so the
> table holds two disjoint datasets: **166 real rows** and 921,530 synthetic
> ones. The dashboard selects fiscal years by name and shows the real ones;
> `Q2` filters `MAX(fiscal_year_id)`, a synthetic year. On the real data the
> dashboard is genuinely fast. The figures above are what happens at the
> advertised 1M-row scale.
>
> The generator also replicates one year's sub-schemes ten times, giving a
> fan-out of ~4,010 sub-schemes per scheme that a real budget hierarchy would
> not have. The predicate-pushdown finding is a property of the optimizer and
> holds regardless; the buffer pool percentages are specific to this shape.
> [What generalizes, and what does not](PERFORMANCE.md#what-generalizes-and-what-does-not)
> separates the three findings.

### Not currently measured

Two previously published figures — ETL throughput (~400 → ~20,800 rows/sec) and
dashboard load time (~420 ms → ~38 ms via `@st.cache_data`) — have been removed
rather than restated. `scripts/benchmark.py` and `scripts/benchmark_cache.py`
print their results and persist nothing, so neither number can be reproduced
from this repository. They will return if and when a script writes them to a
file the way `benchmark_index.py` does.

---

## 🛠️ Features

### 📊 Analytics Dashboard (6 Tabs)

| Tab | What It Shows |
|---|---|
| 🏠 Overview | KPI cards, national revenue trend line, scheme composition donut |
| 📈 Scheme Analysis | Top-N bar chart with slider, year-over-year grouped comparison |
| ⚖️ Budget vs Actuals | Scatter plot with 100% utilisation line, utilisation % bar |
| 💡 Insights | **Auto-generated** — top scheme, highest growth, largest revision, avg utilisation |
| 🗄️ Query Console | 5 pre-built (read-only) domain SQL queries with SQL viewer + CSV/Excel export |
| 🔍 Data Explorer | Searchable, sortable table with CSV + Excel download |

### 🏗️ Engineering Features

- **Modular ETL** — Extract / Transform / Load in separate modules, wired by an orchestrator
- **Idempotent pipeline** — `INSERT IGNORE` + `ON DUPLICATE KEY UPDATE`; safe to re-run
- **Transaction safety** — single commit; bad rows skipped and counted, systemic failure rolls back
- **29 automated tests** — covering all ETL logic, no database required
- **Config validation** — clear error messages before any DB operation
- **File logging** — every run appended to `logs/application.log`
- **manage.py CLI** — `setup`, `load`, `validate`, `dashboard`, `test`
- **Excel export** — download any query result or explorer view as `.xlsx`

---

## 🧠 What I Learned

Through this project I learned:
- **Designing normalized relational schemas** (3NF) to ensure absolute data integrity.
- **Using transactions safely** to prevent partial loads and data corruption.
- **Reading execution plans** (`EXPLAIN FORMAT=JSON`) to understand the MySQL query optimizer.
- **Benchmarking SQL queries** and engineering covering indexes to reduce latency.
- **Building reproducible ETL pipelines** using idempotent upserts and automated testing.

---

## 📁 Project Structure

```
Government-Budget-Analytics-Platform/
|
+-- app/
|   \-- dashboard.py            # 6-tab Streamlit analytics dashboard
|
+-- config/
|   +-- settings.py             # Environment-based configuration (reads .env)
|   \-- validator.py            # Config validation guard (nice errors, not crashes)
|
+-- database/
|   +-- schema.sql              # 6-table normalised schema, constraints, indexes
|   +-- views.sql               # 3 analytical SQL views
|   \-- setup.py                # Idempotent DB initialiser
|
+-- etl/
|   +-- extract.py              # Extract: read + validate raw CSV
|   +-- transform.py            # Transform: clean, filter summaries, reshape
|   +-- load.py                 # Load: upsert (FOR SHARE read-back), skip-and-continue
|   \-- pipeline.py             # Orchestrator: Extract -> Transform -> Load
|
+-- scripts/                    # Benchmarks, migrations, concurrency tests
|   +-- benchmark_index.py           # Covering index, 3 arms x 21 samples
|   +-- benchmark_buffer_pool.py     # Buffer pool x AHI 2x2, interleaved
|   +-- benchmark_dashboard.py       # What the dashboard actually issues
|   +-- benchmark_dashboard_baseline.py  # Re-measures the pre-fix figures
|   +-- benchmark.py / benchmark_cache.py
|   +-- generate_synthetic_data.py   # Scales the table to ~922K rows
|   +-- migrate_sub_scheme_grain.py  # ADR 7 migration, verified against source
|   +-- test_upsert_race.py          # Reproduces a REPEATABLE READ race
|   +-- test_pool_concurrency.py     # Shared connection vs pool, under load
|   \-- simulate_concurrency.py / test_constraints.py
|
+-- results/                    # Committed artifacts -- every published figure
|   +-- index_benchmark.json         # Raw samples + EXPLAIN per arm
|   +-- buffer_pool_benchmark.json   # 2x2 grid, hit rates, raw samples
|   +-- dashboard_benchmark.json     # Post-fix page load
|   +-- dashboard_baseline_benchmark.json  # Pre-fix re-measurement
|   \-- dashboard_benchmark_control_run.json  # Same-session control
|
+-- tests/
|   +-- conftest.py             # Shared pytest fixtures (no DB required)
|   \-- test_transform.py       # 29 tests: extract, transform, identity, NULLs
|
+-- utils/
|   +-- db.py                   # Connection factory, pool, table-size capture
|   \-- logger.py               # Logs to stdout + logs/application.log
|
+-- docs/
|   \-- ADR-007-scope-sub-scheme-grain.md  # Scope + outcome of the grain fix
|
+-- .github/workflows/ci.yml    # ruff + pytest on every push and PR
|
+-- data/Details_of_Tax_Revenue.csv   # Raw source (Government of India)
+-- assets/screenshots/         # Architecture, ER diagram, tab captures
+-- logs/                       # Auto-created -- application.log written here
|
+-- PERFORMANCE.md              # Index, buffer pool, AHI, pushdown, reproduction
+-- DECISIONS.md                # ADRs 1-7
+-- DATA_GENERATOR.md           # How the synthetic rows are made
+-- manage.py                   # CLI: setup / load / validate / dashboard / test
+-- setup_db.py / run_etl.py    # Standalone entry points
+-- pytest.ini / ruff.toml      # Test collection scope, lint rule set
\-- .env.example                # Template -- copy to .env and fill credentials
```

---

## ⚡ Quick Start

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| MySQL Server | 8.0+ | Must be running locally |

### 4-Command Setup

```bash
# 1. Clone
git clone https://github.com/Nishant8677/Government-Budget-Analytics-Platform.git
cd Government-Budget-Analytics-Platform

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env          # Then open .env and fill in DB_PASSWORD
python manage.py validate     # Confirms your config before touching the DB

# 4. Setup → Load → Dashboard
python manage.py setup        # Creates budgetiq DB + schema + views
python manage.py load         # ETL: CSV → MySQL (201 records)
python manage.py dashboard    # Launches http://localhost:8501
```

> ⚠️ Never commit `.env` — it is already excluded by `.gitignore`.

---

## 🖥️ manage.py Commands

```bash
python manage.py setup      # Step 1: create database + apply schema + views
python manage.py load       # Step 2: run ETL pipeline (Extract→Transform→Load)
python manage.py validate   # Check .env configuration without touching the DB
python manage.py dashboard  # Launch Streamlit dashboard
python manage.py test       # Run pytest test suite
python manage.py test -v    # Verbose test output
```

---

## 🧪 Running Tests

```bash
python manage.py test
```

Expected output:
```
============================= test session starts =============================
collected 29 items

tests/test_transform.py::TestExtract::test_raises_file_not_found_on_missing_csv PASSED
tests/test_transform.py::TestExtract::test_returns_dataframe_for_valid_csv PASSED
tests/test_transform.py::TestTransformRowFiltering::test_removes_total_rows PASSED
...
tests/test_transform.py::TestGetFiscalYearRecords::test_fiscal_year_values_are_valid_strings PASSED

============================== 29 passed in 0.28s ==============================
```

Tests are grouped into 7 classes:
- `TestExtract` — file validation
- `TestTransformRowFiltering` — summary row removal
- `TestTransformForwardFill` — sparse CSV fills
- `TestTransformNumericCoercion` — type coercion
- `TestTransformValidation` — missing column errors
- `TestFiscalYearColumnMap` — **bug-fix regression tests** (the critical ones)
- `TestGetFiscalYearRecords` — record expansion and structure

---

## 🔒 Security Notes

| Practice | Implementation |
|---|---|
| No hardcoded credentials | All credentials from `.env` via `python-dotenv` |
| No SQL injection | All queries use `%s` parameterised placeholders |
| Secret exclusion | `.gitignore` covers `.env`, `logs/`, `__pycache__` |
| Config validation | `validate()` exits cleanly with actionable errors |

---

## 📐 ETL Design Decisions

### Why separate Extract / Transform / Load modules?
Each phase has a single responsibility and can be tested in isolation. `extract()` accepts an optional `filepath` argument so tests pass a mock CSV without touching the filesystem. `load()` takes a plain list of dicts, making it database-agnostic for testing.

### Why upsert instead of delete-and-reload?
`INSERT IGNORE` on reference tables and `ON DUPLICATE KEY UPDATE` on `budget_data` means the pipeline is re-runnable without wiping data — critical when adding new fiscal years.

### Why a single transaction?
All records commit together. Individual bad rows are skipped with a warning. A systemic failure triggers `ROLLBACK`, leaving the database in a clean state for retry.

### What bug was fixed?
The original `pythoncode.py` used string-matching on column names to determine which fiscal year to populate, causing `Revised 2021-2022` values to be incorrectly written to 2022-2023 rows. The fix documents the exact column mapping explicitly in `FISCAL_YEAR_COLUMN_MAP` — protected by dedicated regression tests.

---

## 🛠️ Tech Stack

| Technology | Version | Use |
|---|---|---|
| Python | 3.11 | Core language |
| MySQL | 8.0 | Relational database |
| mysql-connector-python | 26.x | DB driver |
| pandas | 3.x | ETL data manipulation |
| python-dotenv | 1.x | Environment config |
| Streamlit | 1.35+ | Dashboard framework |
| Plotly | 5.x / 6.x | Interactive charts |
| openpyxl | 3.x | Excel export |
| pytest | 9.x | Test framework |

---

## 🔧 Troubleshooting

| Problem | Solution |
|---|---|
| `Access denied for user 'root'` | Check `DB_PASSWORD` in `.env` |
| `Unknown database 'budgetiq'` | Run `python manage.py setup` first |
| `No module named 'config'` | Run commands from the project root directory |
| `FileNotFoundError: Details_of_Tax_Revenue.csv` | Ensure file is in `data/` |
| Dashboard shows "Database not connected" | Confirm `.env` credentials and MySQL is running |
| `python manage.py validate` fails | Shows exactly which env variable is missing |

---

## 🔮 Future Work

- [x] ~~GitHub Actions CI~~ — done: `ruff` + `pytest` on every push and PR
- [ ] Support additional fiscal years (extend `FISCAL_YEAR_COLUMN_MAP`)
- [ ] State-level budget breakdown (add `states` table to schema)
- [ ] Streamlit authentication for multi-user deployment
- [ ] PDF export using `reportlab`
- [ ] Performance benchmark tab (cache vs no-cache timing)

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 👤 Author

Computer Science student | Building production-oriented engineering portfolio projects.

**Skills demonstrated in this project:**
Database normalisation · ETL pipeline engineering · SQL view architecture ·
Parameterised queries · Environment-based configuration · pytest testing ·
Interactive data visualisation · Transaction management · CLI tooling
