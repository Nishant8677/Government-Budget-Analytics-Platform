# 🧬 Synthetic Data Generator

To properly demonstrate query optimization, execution plans, and index engineering, a database must have a substantial volume of data. The original 201-row dataset is too small—the MySQL query optimizer will consistently choose Full Table Scans over Index Scans because reading a single data page from disk is faster than traversing a B-Tree index for such a small table.

To simulate a production-scale workload, this project includes a **Synthetic Data Generator** (`scripts/generate_synthetic_data.py`) capable of scaling the dataset from 201 rows up to 1,000,000+ rows while maintaining strict referential integrity.

---

## 🛠️ Generator Design Principles

1. **Maintain Referential Integrity:** 
   The generator does not blindly insert random rows. It queries the existing `schemes` (e.g., "Corporation Tax", "Customs") and generates thousands of child `sub_schemes` that properly reference the parent `scheme_id`.
   
2. **Realistic Relationships:**
   Major Head codes (government accounting codes) are assigned dynamically by sampling from the existing valid codes in the `major_heads` table, ensuring foreign keys are not violated.

3. **Time-Series Expansion:**
   10 synthetic Fiscal Years (e.g., `2000-2001` through `2009-2010`) are generated and inserted into the `fiscal_years` table. 

4. **Batch Processing:**
   Inserting 1 million rows individually would take hours due to transaction overhead. The generator uses `cursor.executemany()` with a batch size of 5,000 to achieve bulk insert speeds.

---

## 🚀 Usage

You can run the generator via the CLI. It accepts a `--rows` argument so you can scale the database incrementally for benchmarking purposes.

```bash
# Generate 100,000 synthetic rows
python scripts/generate_synthetic_data.py --rows 100000

# Scale up to 1,000,000 rows
python scripts/generate_synthetic_data.py --rows 1000000
```

## ⚙️ How it Calculates Scale

If you request `1,000,000` rows:
- The script notes that there are exactly `10` synthetic fiscal years.
- Therefore, it needs `1,000,000 / 10 = 100,000` distinct `sub_schemes`.
- Since there are `23` existing schemes, it generates `100,000 / 23 ≈ 4,347` synthetic sub-schemes per scheme.
- It inserts these 4,347 sub-schemes into the DB, retrieves their auto-incremented `sub_scheme_id`s, and then inserts 10 budget rows for each.

## 📊 Impact on the Project

By scaling to 1 million rows, we unlock the ability to perform deep Database Engineering:
- **Execution Plans:** `EXPLAIN ANALYZE` will now reveal realistic optimizer choices.
- **Index Engineering:** We can measure the exact latency reduction provided by composite indexes (Phase 4).
- **Concurrency:** We can simulate transaction locks and race conditions during high-volume ETL inserts (Phase 7).
