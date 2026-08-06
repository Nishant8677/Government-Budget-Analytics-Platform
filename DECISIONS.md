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
**Consequences & Trade-offs:** Normalizing into `groups`, `schemes`, `sub_schemes`, and `fiscal_years` ensures 100% data integrity and eliminates update anomalies. The trade-off is read latency due to nested loop joins, and it is a real cost that has not been bought back: the covering index intended to recover it does not (ADR 4), and the grouped aggregation benchmark still takes ~1.8 s at ~920k rows. SQL Views (ADR 6) hide the join complexity from the application but do not make it cheaper.

## ADR 3: ETL Concurrency via Upserts
**Context:** The ETL pipeline might be triggered multiple times, potentially concurrently.
**Alternatives:** Truncate & Load, `SELECT` then `INSERT` (manual check), or `ON DUPLICATE KEY UPDATE` (Upsert).
**Decision:** **Atomic Upserts**
**Consequences & Trade-offs:** Using upserts delegates concurrency control and race-condition prevention directly to the InnoDB storage engine. If two ETL workers run simultaneously, InnoDB places row-level exclusive locks on the index, serializing the writes automatically. This prevents data corruption without requiring complex application-level distributed locks (e.g., Redis).

## ADR 4: Covering Index — designed, measured, and rejected
**Context:** At ~920k rows the grouped aggregation benchmark query (`Q2` in
`scripts/benchmark.py`) takes ~1.8 s. It filters on `fiscal_year_id`, joins on
`sub_scheme_id` and sums `budget`, which is the textbook shape for a covering
index.
**Alternatives:** Denormalize `scheme_id` into `budget_data`, cache in Redis, or
add a covering B-Tree index.
**Decision:** **Covering index built, benchmarked, and NOT adopted.**
`database/schema.sql` is unchanged.

**Consequences & Trade-offs:** An earlier version of this ADR asserted the index
reduced latency by over 80%. It was never built and never measured. Built and
measured (`scripts/benchmark_index.py`, results in
`results/index_benchmark.json`, analysis in `PERFORMANCE.md`):

| Arm | Median | vs baseline |
|---|---|---|
| No index | 1800.9 ms | — |
| Index added | 1785.9 ms | +0.8% (inside a ±133 ms spread — noise) |
| Index + `FORCE INDEX` | 1661.3 ms | +7.8% |

`EXPLAIN` is identical before and after `CREATE INDEX`. The optimizer never
considers the new index, because `budget_data` is already reached by `eq_ref` on
`uq_budget_sub_year (sub_scheme_id, fiscal_year_id)` — a UNIQUE key added in the
initial schema to enforce one row per sub-scheme per fiscal year. A covering
index cannot beat a single-row lookup on selectivity.

Forced into the plan it behaves exactly as designed — `budget` is read from the
index leaf and the clustered-row lookup disappears — but yields only 7.8%,
because those lookups were never the bottleneck. The plan walks 92,240
`sub_schemes` rows to return 10 grouped values.

Rejected because an index the optimizer declines to use earns nothing while
costing write amplification on every insert and additional disk. It pays only
when forced, on a query the dashboard does not run.

**The genuinely interesting finding** is that MySQL rates the forced plan 2.7×
*more* expensive (121,062 vs 45,442) than the one it picks, and the forced plan
is the faster of the two. On this schema the cost model mis-ranks the available
plans.

## ADR 4a: Reporting a negative result rather than restating it lower
**Context:** Having measured 0.8%, we could have quoted the 7.8% `FORCE INDEX`
figure instead, or added the index anyway so the claim had something behind it.
**Decision:** **State the negative result.**
**Consequences & Trade-offs:** Quoting a number that only appears under
`FORCE INDEX`, on a query the application never issues, would repeat the
original problem in a quieter voice. The measurement is more useful than the
optimisation would have been: it identifies the real bottleneck (the 92,240-row
`sub_schemes` walk) and documents a case where the optimizer's cost model is
inverted. Both are reproducible by running one script.

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
