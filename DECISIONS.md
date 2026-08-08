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
**Decision:** **Atomic upserts, with a locking read-back**
**Consequences & Trade-offs:** Upserts delegate write serialisation to InnoDB. If
two ETL workers run simultaneously, row-level locks on the unique index serialise
the inserts, so no duplicate reference rows are created and no application-level
distributed lock (e.g. Redis) is needed.

**An earlier version of this ADR stopped there, and that was wrong.** It claimed
upserts delegate "race-condition prevention" to the storage engine. Serialising
the *write* is not the whole operation: `_upsert_group` and its four siblings
insert and then read the ID back, and it was the read that was unsafe.

This ADR interacts with ADR 5. Under `REPEATABLE READ` a transaction's
consistent-read snapshot is established at its first consistent read, and
`INSERT IGNORE` is a locking write that does not establish it. So from the second
record of a batch onward the snapshot is already fixed: a reference row committed
by another worker after that point collides with the `INSERT IGNORE` while
remaining invisible to the `SELECT`. `fetchone()` returns `None`, `None[0]`
raises `TypeError`, and the load dies partway through.

`scripts/test_upsert_race.py` reproduces this with a forced interleaving, using
`READ COMMITTED` as the control so the isolation level is the only variable:

| | REPEATABLE READ | READ COMMITTED |
|---|---|---|
| `INSERT IGNORE` warning | Duplicate entry | Duplicate entry |
| plain `SELECT` saw row | **False** | True |
| `_upsert_group` | **TypeError** | returned id |

The two middle cells are the mechanism, one statement apart in one transaction:
the write layer reports a duplicate key, so the row exists; the read layer
returns nothing, because its snapshot predates the commit.

The read-back is now `SELECT ... FOR SHARE` — a locking read, which sees the
latest committed row instead of the snapshot. Both arms pass. The cost is a
shared lock per reference row held until commit, which serialises workers
touching the same group or scheme; at this cardinality that is cheap.

**The transferable lesson:** two architecture decisions that are each defensible
alone were unsafe in combination, and nothing in the repository connected them.
The ADRs were written as independent entries and the interaction lived in the
gap between ADR 3 and ADR 5.

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

## ADR 4b: Server configuration beat schema tuning
**Context:** With the covering index measured at 0.8% (ADR 4), the question
became what actually constrains the query. Two candidates: the query text, and
the server configuration nobody had examined.
**Alternatives:** Rewrite the query to aggregate `budget_data` before joining;
size the InnoDB buffer pool; accept the latency.
**Decision:** **Recommend sizing the buffer pool. The query rewrite is not
worth adopting.**

**Consequences & Trade-offs:** The rewrite — aggregating to one row per scheme
in a derived table, so the outer join names 23 rows instead of walking 92,240 —
produced 1962 ms → 1705 ms, and `EXPLAIN` shows the optimizer collapses it back
to the same plan shape. Not worth the loss of readability.

`innodb_buffer_pool_size` was at its 128 MB default against a table of
comparable size.
Raising it to 1 GB is worth **32.7%** (1783.5 ms → 1200.7 ms, n=21 per cell,
arms interleaved). That is four times what the covering index achieves when
forced, from a configuration line rather than a schema change.

The mechanism is not caching. Every cell runs at a **100% buffer pool hit rate
with zero disk reads** — the data was already resident. The effect is entirely
mediated by the adaptive hash index, which is sized as a fraction of the buffer
pool: with the AHI disabled, pool size makes no difference (−1.5%). At 128 MB
the AHI is *actively harmful*, 7.2% slower than turning it off, because it
cannot cover the working set and pays maintenance without collecting the
benefit; at 1 GB the same feature is worth 28.9%.

This is a recommendation rather than a commit, because it is a server setting
and not a property of this repository. `scripts/benchmark_buffer_pool.py`
changes it dynamically for the duration of a run and restores it on exit.

**The transferable lesson:** the project's documentation described six
schema-level decisions in detail and never mentioned server configuration. The
largest available win was in the part nobody had written down.

## ADR 5: Isolation Level (REPEATABLE READ)
**Context:** When the Streamlit dashboard queries the database while an ETL pipeline is actively inserting 1M rows, what should the dashboard see?
**Alternatives:** READ UNCOMMITTED, READ COMMITTED, REPEATABLE READ, SERIALIZABLE.
**Decision:** **REPEATABLE READ (MySQL Default)**
**Consequences & Trade-offs:** By keeping the default, we utilize InnoDB's Multi-Version Concurrency Control (MVCC). The dashboard queries against a consistent snapshot of the data. It will not see "Dirty Reads" (partially loaded ETL data) nor will it experience "Non-Repeatable Reads" mid-query. The trade-off vs READ COMMITTED is a higher probability of gap locks during range updates, but our ETL pipeline strictly inserts unique hierarchical data, making gap-lock deadlocks virtually impossible.

**This decision is not free on the write path, and the cost was found late.** The
snapshot semantics that make the dashboard consistent are the same semantics that
broke the ETL's reference-ID read-back — see ADR 3, where a concurrent loader
could commit a row that the reading transaction could no longer see. The fix was
a locking read in the loader rather than a different isolation level, because the
dashboard is the reason `REPEATABLE READ` was chosen and it still benefits.

Stated plainly: `REPEATABLE READ` is right for the reader and hostile to a
read-after-write in the writer. Any code path that inserts and then reads its own
or a peer's row back needs a locking read, `READ COMMITTED`, or a pattern that
never reads back at all.

## ADR 6: Abstracting Complexity via SQL Views
**Context:** Streamlit relies on `pandas.read_sql()` to fetch data for visualizations.
**Alternatives:** Write raw 5-table JOINs inside the Python application code, or use an ORM (SQLAlchemy), or use Database Views.
**Decision:** **Database Views**
**Consequences & Trade-offs:** By defining `v_budget_overview` in the database, the Python application simply queries `SELECT * FROM v_budget_overview`. This enforces the "Thin Client, Fat Database" philosophy. If the underlying schema changes, we only update the View definition; the Python application remains untouched.
