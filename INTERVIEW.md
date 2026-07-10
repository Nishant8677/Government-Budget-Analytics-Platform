# 🎤 Interview Preparation (BudgetIQ)

This document contains 20 advanced database engineering interview questions specifically tailored to this project. If you can answer these confidently, you can pass a Senior/Specialist Database Engineering interview.

*(See `DEFENSE_GUIDE.md` and `DECISIONS.md` for the answers/talking points).*

---

### Database Design & Normalization
1. **Explain the normalization strategy of your schema.** Why did you stop at 3NF?
2. **Denormalization vs. Views:** If the dashboard requires a flat dataset, why didn't you just create a denormalized table instead of a SQL View? What are the trade-offs?
3. **Surrogate vs. Natural Keys:** You used `AUTO_INCREMENT` surrogate keys (e.g., `scheme_id`) but also placed `UNIQUE` constraints on natural keys (e.g., `scheme_name`). Why use both?
4. **Data Integrity:** How does your database prevent a sub-scheme from being assigned to a non-existent scheme? What happens if that scheme is deleted?

### Query Optimization & Indexing
5. **Optimizer Behavior:** Why did the MySQL optimizer choose a Full Table Scan when your dataset had 200 rows, but switched to an Index Scan at 1,000,000 rows?
6. **Index Architecture:** Explain the B-Tree structure of your `idx_budget_fy_sub_budget` composite index.
7. **Covering Indexes:** What is a Covering Index, and how did it reduce your query latency by 80%?
8. **Index Ordering:** If your query filters by `fiscal_year_id` and joins on `sub_scheme_id`, does it matter which column is first in your composite index? Why?
9. **Index Drawbacks:** If indexes make reads so much faster, why didn't you put an index on every single column in the `budget_data` table?

### Transactions & Isolation
10. **Atomicity:** Your ETL pipeline processes 1 million rows. What happens if the server crashes while inserting row 999,999?
11. **Isolation Levels:** Your database runs on `REPEATABLE READ`. What is a "Dirty Read", and how does your isolation level prevent it?
12. **MVCC:** Explain how Multi-Version Concurrency Control (MVCC) allows the Streamlit dashboard to read data without locking out the ETL pipeline.
13. **Phantom Reads:** Standard SQL says `REPEATABLE READ` is vulnerable to Phantom Reads. Does InnoDB suffer from this? Why or why not?

### Concurrency & Locks
14. **Race Conditions:** Two ETL cron jobs trigger at the exact same millisecond and attempt to insert the same `scheme_name`. How does your database handle this without corrupting data?
15. **Upserts:** Explain the locking mechanism behind `ON DUPLICATE KEY UPDATE`. Is it an exclusive lock or a shared lock?
16. **Deadlocks:** What is a deadlock? How does the strict hierarchical insertion order of your ETL pipeline prevent deadlocks?

### Scaling & Architecture
17. **Connection Pooling:** Your Python scripts open a new connection to MySQL for every run. At what scale would you need a Connection Pool, and why?
18. **Pagination vs. Streaming:** If the Streamlit dashboard needed to display all 1,000,000 rows in the `Data Explorer` tab, how would you change your SQL queries so the server doesn't run out of RAM?
19. **OLTP vs. OLAP:** Is this project an OLTP (Online Transaction Processing) or OLAP (Online Analytical Processing) system? How does that classification affect your indexing strategy?
20. **PostgreSQL Alternative:** If you had to rewrite this project in PostgreSQL, what architectural changes would you expect? (Hint: Check constraints, MVCC differences, Upsert syntax).
