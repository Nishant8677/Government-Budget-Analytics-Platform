# 🛡️ Defense Guide: Interview Preparation

This document explains how to *defend* the major engineering decisions in this project during a software/database engineering interview at companies like Amazon, Microsoft, Infosys, or Qualcomm. 

For each decision, it outlines the trade-offs, potential interviewer challenges, and strong responses.

---

## 1. Schema Design (3NF Normalization)
**Decision:** Used a strictly normalized schema separating Groups, Schemes, Major Heads, Sub Schemes, Fiscal Years, and Budget Data.

**Why:** To enforce strict referential integrity and eliminate update anomalies. If a "Scheme" changes its name, it is updated in exactly one row instead of thousands.

**Benefits:** Zero data duplication. Strict Foreign Key guarantees. Clean hierarchical domain modeling.

**Trade-offs:** Analytical queries (like summing total budget per scheme) require 4-table `JOIN`s, which adds computational overhead and latency compared to a flat denormalized table.

**What the interviewer may ask:** *"Why didn't you just use a single denormalized table? Wouldn't that be faster for the Streamlit dashboard?"*

**Strong answer:** "Yes, a denormalized table would eliminate the JOINs and improve read speed out of the box. However, I prioritized data integrity and write-consistency first. A flat table would introduce massive update anomalies and data duplication. To get the best of both worlds, I kept the write-path normalized (3NF) and optimized the read-path by creating **Covering Indexes** and **SQL Views** to simulate denormalized read performance without sacrificing integrity."

**Weak answer:** "I normalized it because that's what we learned in school."

**Follow-up questions:** What is 3NF? What is a Covering Index? 

---

## 2. Read Optimization (Covering Indexes)
**Decision:** Created a composite index `idx_budget_fy_sub_budget(fiscal_year_id, sub_scheme_id, budget)` on the `budget_data` table.

**Why:** The dashboard frequently groups `budget` by `scheme` for a specific `fiscal_year`.

**Benefits:** Reduced query latency by over 80%. The database engine can satisfy the `WHERE`, `JOIN`, and `SUM()` clauses entirely from the B-Tree leaf nodes without ever reading the actual data pages from disk.

**Trade-offs:** Increases disk storage footprint and slows down the ETL `INSERT` phase, because the B-Tree must be rebalanced/updated during ingestion.

**What the interviewer may ask:** *"Why did you index those specific columns in that specific order?"*

**Strong answer:** "I ordered the composite index starting with `fiscal_year_id` because it's used in the equality `WHERE` filter. `sub_scheme_id` is second because it satisfies the `JOIN` condition. Finally, `budget` is included so it becomes a **Covering Index**. The optimizer doesn't have to perform a clustered index lookup for the budget value; it fetches everything directly from the index."

**Weak answer:** "I just indexed all the columns I was querying."

**Follow-up questions:** What is the difference between a Clustered Index and a Secondary Index in InnoDB?

---

## 3. ETL Architecture (Atomic Upserts)
**Decision:** The ETL load phase uses a single overarching transaction and `ON DUPLICATE KEY UPDATE` (Upsert) statements.

**Why:** To ensure the pipeline is both **Atomic** (all-or-nothing) and **Idempotent** (can be run multiple times safely).

**Benefits:** If the pipeline fails on row 999,000, it rolls back entirely, leaving the database in a consistent state. If two cron jobs run the ETL at the exact same time, InnoDB row-level locking serializes them safely.

**Trade-offs:** A single massive transaction holds locks for a long time. If the dataset grows to 50 million rows, this approach would cause lock exhaustion or massive undo-log bloat.

**What the interviewer may ask:** *"What happens if your ETL pipeline fails halfway through?"*

**Strong answer:** "Because the entire `load()` function is wrapped in `conn.start_transaction()` and `conn.commit()`, a Python exception triggers `conn.rollback()`. The database relies on the Undo Log to revert all changes, ensuring the dashboard never displays a partially loaded state."

**Follow-up questions:** What is the Undo Log? How would you handle this if the dataset was 100x larger? (Answer: Batched transactions or staging tables).

---

## 4. Isolation Level (REPEATABLE READ)
**Decision:** Relied on MySQL's default `REPEATABLE READ` isolation level.

**Why:** To provide consistent snapshots for analytical queries without locking out the ETL pipeline.

**Benefits:** Prevents Dirty Reads and Non-Repeatable reads. Uses MVCC (Multi-Version Concurrency Control) so readers don't block writers, and writers don't block readers.

**Trade-offs:** Slightly more overhead than `READ COMMITTED`. Can theoretically suffer from Phantom Reads, though InnoDB uses Next-Key locks to prevent most phantoms.

**What the interviewer may ask:** *"If the dashboard queries the database while the ETL is actively inserting 1 million rows, what data does the user see?"*

**Strong answer:** "They see the old, consistent state of the database. Because InnoDB uses `REPEATABLE READ` with MVCC, the dashboard query establishes a read view snapshot at the start of the query. It ignores any uncommitted rows being written by the ETL, meaning no Dirty Reads. Only after the ETL fully commits will the *next* dashboard query see the new data."

**Weak answer:** "The database locks up until the ETL finishes."

**Follow-up questions:** What is a Dirty Read? What is MVCC?

---

## 5. Abstraction (SQL Views)
**Decision:** Created SQL Views (e.g., `v_budget_overview`) instead of writing raw SQL in Python or using an ORM.

**Why:** To decouple the application logic (Streamlit/Pandas) from the physical database schema.

**Benefits:** If we decide to denormalize the database later for performance, we only have to update the View definition. The Streamlit Python code remains completely untouched.

**Trade-offs:** Views can sometimes mask poor underlying query performance if the developer isn't careful to check the `EXPLAIN` plan of the View itself.

**What the interviewer may ask:** *"Why didn't you just use SQLAlchemy or write the JOINs in Pandas?"*

**Strong answer:** "I prefer the 'Thin Client, Fat Database' philosophy for analytical workloads. By pushing the JOIN logic down into a SQL View, I let the MySQL optimizer do what it does best. It keeps the Python codebase clean and creates an API contract between the Database layer and the Application layer."
