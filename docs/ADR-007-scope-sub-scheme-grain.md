# Scope: fixing the sub-scheme grain

**Status:** proposed, not implemented
**Bug:** `etl/load.py` design note 5
**Blast radius measured, not estimated** — every figure below comes from a query
against the live database or the source CSV.

---

## The defect

`sub_schemes` is unique on `(sub_scheme_name, scheme_id)`. The source is not.

Under `Customs > Import Duties` the CSV carries nine separate levies — Basic
Duties, Social Welfare Surcharge, Health Cess, three education cesses and others
— that share a sub-scheme name and are distinguished only by `Programme Name`
and `Sub Programme Name`. `etl/transform.py` drops both columns as irrelevant
(`COLUMNS_TO_DROP`), after which the nine become one, and
`ON DUPLICATE KEY UPDATE` in `_upsert_sub_scheme` keeps whichever arrives last.

`Major Head Code` cannot separate them either: all nine carry code 37.

**Consequence:** 35 of the 201 records the pipeline emits collapse onto 8
existing keys. Across those keys the source holds **361,934.70 crore** of
actuals and the database stores **123,583.06** — **238,351.64 crore silently
discarded**, and every scheme total the dashboard shows for those rows is
understated.

## The natural key, measured

118 candidate rows in the real dataset:

| Candidate key | Distinct | Verdict |
|---|---|---|
| `scheme + sub_scheme_name` | 91 | **27 collisions** — today's key |
| `+ programme_name` | 117 | 1 collision — *not sufficient* |
| `+ sub_programme_name` | **118** | **unique** |
| `+ major_head_code` | 118 | adds nothing |

Both columns are required. `Programme Name` alone still collides, on the two
"Basic Duties" rows separated only by `Sub Programme Name` ("Other than debits
of Scrips" versus "Through Debit in Ledger"). Adding `major_head_code` buys
nothing and should not be in the key.

### The NULL problem

| Column | NULL | Distinct |
|---|---|---|
| `Programme Name` | 20 / 118 | 29 |
| `Sub Programme Name` | 30 / 118 | 3 |

This is the part that will bite anyone implementing it quickly. **MySQL's
`UNIQUE` treats `NULL` as distinct from every other `NULL`**, so a plain
`UNIQUE (sub_scheme_name, scheme_id, programme_name, sub_programme_name)`
enforces nothing on the 20 and 30 rows where those columns are empty — the
constraint would silently permit exactly the duplicates it was added to prevent,
and re-running the ETL would grow the table on every run.

Three ways out:

1. **`NOT NULL DEFAULT ''`** — simplest and it works. Costs the distinction
   between "no programme breakdown exists" and "unknown", which for a structural
   label is a smaller loss than it sounds.
2. **Functional unique index** on `COALESCE(programme_name, '')` etc. — MySQL
   8.0.13+. Keeps `NULL` in the column and still constrains. Consistent with the
   NULL semantics just restored for financial figures, though `NULL` means
   something different here: a missing *figure* is unpublished data, a missing
   *programme* is a structural fact about that line item.
3. **Generated hash column**, `UNIQUE` on it. Robust, opaque, hard to debug.

**Recommendation: option 1.** For identity columns the sentinel is honest and
every future reader understands it. Option 2 is the more principled choice and
the argument for it is real; it is rejected on the grounds that a functional
index is a surprising thing to meet in a schema this small.

## Resulting shape

| | Now | After |
|---|---|---|
| `sub_schemes` (real) | 87 | **118** |
| `budget_data` (real) | 166 | **201** |
| Actuals discarded | 238,351.64 crore | **0** |
| `sub_schemes` (synthetic) | 92,153 | unchanged |
| `budget_data` total | 921,696 | 921,731 |

**The synthetic data is untouched, and that is the whole reason this is
affordable.** Real and synthetic rows share zero `sub_scheme_id` values —
verified, not assumed — so the migration touches 166 budget rows and 87
sub-schemes and leaves the 921,530-row benchmark substrate alone. No figure in
`PERFORMANCE.md` describes real rows, so none of them move.

## Work required

**1. Schema** — `database/schema.sql`
Add `programme_name` and `sub_programme_name` to `sub_schemes`, `NOT NULL
DEFAULT ''`. Replace `uq_sub_scheme_scheme` with a four-column unique key.

The index length is tight enough to check rather than assume. Three
`VARCHAR(255)` columns at `utf8mb4` plus one `INT` is 3 x 1020 + 4 = **3064
bytes against InnoDB's 3072-byte limit** for a `DYNAMIC` row format. It fits,
with eight bytes to spare. Verified by building the table:

```sql
UNIQUE KEY uq_try (sub_scheme_name, scheme_id, programme_name, sub_programme_name)
-- accepted, utf8mb4, DYNAMIC
```

Eight bytes of headroom is not a margin, it is a tripwire — widening any of the
three columns by a single character breaks the schema. The real data does not
need the width: the longest values are 54, 87 and 58 characters. **Declare the
two new columns `VARCHAR(128)`**, which is comfortably above the data and drops
the key to roughly 2050 bytes.

**2. ETL** — `etl/transform.py`, `etl/load.py`
Stop dropping the two columns; forward-fill them like the other hierarchy
columns; carry them into the record dicts; extend `_upsert_sub_scheme` to key on
four columns. Note the `FOR SHARE` read-back stays as-is.

**3. Views** — `database/views.sql`
`v_budget_overview` exposes `sub_scheme_name`; after this change the Data
Explorer would show nine identical "Import Duties" rows. Expose
`programme_name` so the grain is visible. `v_scheme_summary` and
`v_fiscal_year_totals` aggregate above this level and need no change, but
`v_scheme_summary.sub_scheme_count` will rise — it counts what it says it
counts, and the new number is the correct one.

**4. Dashboard** — `app/dashboard.py`
Surface `programme_name` in the Data Explorer table and in the export. Check the
Query Console's pre-written SQL for anything grouping on `sub_scheme_name`.

**5. Migration** — new script, `scripts/migrate_sub_scheme_grain.py`
The discarded rows cannot be recovered from the database — only the last writer
survived — so this is a delete-and-reload of the real slice, not a backfill.
In one transaction: delete the 166 real `budget_data` rows, delete the 87 real
`sub_schemes` (FKs are `ON DELETE RESTRICT`, so budget rows must go first), run
the ETL. Take a `mysqldump` of both tables first.

**6. Tests** — `tests/test_transform.py`
The nine Import Duties levies are the obvious fixture: assert they survive as
nine records rather than one, and assert the summed actuals match the source.
That is the regression test for this bug, and it fails today.

**7. Documentation**
`DECISIONS.md` needs an ADR recording the grain change. `etl/load.py` design
note 5 becomes a record of a fixed bug. `README.md` and `PERFORMANCE.md` both
quote 921,696 — it becomes 921,731.

## Estimate

Half a day for schema, ETL and migration; another half for views, dashboard,
tests and docs. The risk is concentrated in the index-length limit and in the
NULL handling, both of which fail loudly and immediately rather than silently.

## Recommendation

**Do it, but not as a quick fix.** The bug is real and material — a fifth of the
actuals in the affected schemes is missing — and the fix is cleanly bounded
because the synthetic data is disjoint. But it changes the row count every
document in this repository quotes, so it needs the same discipline as the
performance work: measure before, measure after, and update the claims from the
artifacts rather than from memory.

If it is not done, the `KNOWN BUG` note in `etl/load.py` must stay. An
understated total that nobody has written down is the failure mode this project
exists to argue against.
