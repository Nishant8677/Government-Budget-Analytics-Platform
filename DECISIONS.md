# 🏛️ Architecture Decisions (ADR)

This document records the major architectural decisions made during the engineering of the Government Budget Analytics Platform.

---

## ADR 1: Relational vs. NoSQL
**Context:** We need to store structured financial data (Groups, Schemes, Budgets) and expose it via a dashboard.
**Alternatives:** MongoDB (Document), Cassandra (Columnar), MySQL/PostgreSQL (Relational).
**Decision:** **MySQL (Relational)**
**Consequences & Trade-offs:** The dataset is highly structured and hierarchical. Using a document store like MongoDB would result in massive data duplication (embedding the scheme and group strings in every budget row). A relational database enforces consistency and allows us to change a scheme's name in exactly one place. The trade-off is that analytical queries require complex `JOIN`s, which we mitigated using SQL Views.

## ADR 2: Schema Normalization (3NF) vs. Denormalization
**Context:** The original dataset is a flat CSV.
**Alternatives:** Load the flat CSV directly into a single massive table (Denormalized/Data Warehouse style), or normalize into 3NF.
**Decision:** **Strict 3NF Normalization**
**Consequences & Trade-offs:** Normalizing into `groups`, `schemes`, `sub_schemes`, and `fiscal_years` ensures 100% data integrity and eliminates update anomalies. The trade-off is read latency due to nested loop joins. We accepted this write-optimized structure because it proves fundamental DB design skills, and we recovered the read performance by engineering a Covering Index (see ADR 4).

## ADR 3: ETL Concurrency via Upserts
**Context:** The ETL pipeline might be triggered multiple times, potentially concurrently.
**Alternatives:** Truncate & Load, `SELECT` then `INSERT` (manual check), or `ON DUPLICATE KEY UPDATE` (Upsert).
**Decision:** **Atomic Upserts**
**Consequences & Trade-offs:** Using upserts delegates concurrency control and race-condition prevention directly to the InnoDB storage engine. If two ETL workers run simultaneously, InnoDB places row-level exclusive locks on the index, serializing the writes automatically. This prevents data corruption without requiring complex application-level distributed locks (e.g., Redis).

## ADR 4: Read Performance via Covering Indexes
**Context:** At 1 million rows, dashboard queries aggregating budget data per scheme were taking >2 seconds.
**Alternatives:** Denormalize the `scheme_id` directly into the `budget_data` table, or use Redis caching, or create secondary indexes.
**Decision:** **Covering B-Tree Index** (`idx_budget_fy_sub_budget`)
**Consequences & Trade-offs:** We created an index on `(fiscal_year_id, sub_scheme_id, budget)`. This allows the MySQL optimizer to evaluate the WHERE clause, execute the JOIN, and compute the SUM() entirely from the index leaf nodes without touching the physical table data. Trade-off: Slower `INSERT` performance and higher disk footprint, which is a textbook OLAP vs OLTP trade-off.

## ADR 5: Isolation Level (REPEATABLE READ)
**Context:** When the Streamlit dashboard queries the database while an ETL pipeline is actively inserting 1M rows, what should the dashboard see?
**Alternatives:** READ UNCOMMITTED, READ COMMITTED, REPEATABLE READ, SERIALIZABLE.
**Decision:** **REPEATABLE READ (MySQL Default)**
**Consequences & Trade-offs:** By keeping the default, we utilize InnoDB's Multi-Version Concurrency Control (MVCC). The dashboard queries against a consistent snapshot of the data. It will not see "Dirty Reads" (partially loaded ETL data) nor will it experience "Non-Repeatable Reads" mid-query. The trade-off vs READ COMMITTED is a higher probability of gap locks during range updates, but our ETL pipeline strictly inserts unique hierarchical data, making gap-lock deadlocks virtually impossible.

## ADR 6: Abstracting Complexity via SQL Views
**Context:** Streamlit relies on `pandas.read_sql()` to fetch data for visualizations.
**Alternatives:** Write raw 5-table JOINs inside the Python application code, or use an ORM (SQLAlchemy), or use Database Views.
**Decision:** **Database Views**
**Consequences & Trade-offs:** By defining `v_budget_overview` in the database, the Python application simply queries `SELECT * FROM v_budget_overview`. This enforces the "Thin Client, Fat Database" philosophy. If the underlying schema changes, we only update the View definition; the Python application remains untouched.
