# Index Engineering & Performance

Most figures below are read from a persisted artifact, and each section names
the one it comes from: `results/index_benchmark.json` for the index results,
`results/buffer_pool_benchmark.json` for the buffer pool and AHI grid,
`results/dashboard_benchmark.json` for the dashboard's post-fix state. Re-run
the scripts and you get new files.

The pre-fix dashboard figures were originally quoted from a run that was never
captured — `scripts/benchmark_dashboard.py` writes to a fixed path, so running
it after the fix overwrote the evidence for what came before. They have since
been re-measured by `scripts/benchmark_dashboard_baseline.py` into
`results/dashboard_baseline_benchmark.json`, and **three of the four did not
reproduce**. Both runs are reported side by side under
[Reproducing the pre-fix figures](#reproducing-the-pre-fix-figures); read that
section before quoting any millisecond value on this page.

One table remains unbacked — the query-rewrite comparison — and is marked
*(not persisted)* where it appears.

```bash
python scripts/benchmark_index.py --trials 3 --repeats 7
```

## Summary

**The covering index does not deliver the improvement this repository previously
claimed.** Earlier revisions of the README credited
`idx_budget_fy_sub_budget (fiscal_year_id, sub_scheme_id, budget)` with an
80–85% latency reduction. That index had never existed in `database/schema.sql`,
and no script in the repository persisted a measurement.

Built and measured, it delivers **0.8%** — statistically indistinguishable from
zero. Forced into the plan it delivers **7.8%**.

## Environment

| | |
|---|---|
| MySQL | 8.0.43 |
| OS | Windows 11 (build 26200), x86-64, 12 logical CPUs |
| `budget_data` rows | **921,696** |
| `sub_schemes` rows | 92,240 |
| `fiscal_years` rows | 13 |
| InnoDB buffer pool | 128 MB (server default) |

Two things follow from that last row. The buffer pool is the same order of
magnitude as the table, so pages compete for residence and run-to-run variance is
high — visible in the standard deviations below. And the pool cannot be flushed
from a client connection without `SUPER` or a server restart, so **both arms run
warm**. The comparison between arms is valid; the absolute latencies are a best
case.

> A previous revision of this table gave `budget_data` as 90.2 MB. That figure
> was typed in rather than captured, and a spot check returns a materially larger
> one — with no schema change, and the same four indexes as `schema.sql`. InnoDB's
> estimates drift with fragmentation and `ANALYZE TABLE`, so rather than swap one
> uncaptured number for another, the row is gone. What the argument actually needs
> is the sentence above: pool and table are the same order of magnitude. Every
> benchmark script now records `table_sizes_mb`, so the next run of any of them
> puts a real figure in its artifact.

## The query

Mirrors `Q2: Grouped Aggregation (Scheme Level)` in `scripts/benchmark.py`:

```sql
SELECT s.scheme_name, SUM(b.budget) AS total_budget
FROM budget_data b
JOIN sub_schemes ss ON b.sub_scheme_id = ss.sub_scheme_id
JOIN schemes s ON ss.scheme_id = s.scheme_id
WHERE b.fiscal_year_id = (SELECT MAX(fiscal_year_id) FROM fiscal_years)
GROUP BY s.scheme_name
ORDER BY total_budget DESC
LIMIT 10;
```

It filters on `fiscal_year_id`, joins on `sub_scheme_id` and aggregates `budget`
— the three columns the covering index carries, in that order. On paper it is
the ideal candidate.

> **This is a benchmark query, not an application query.** `app/dashboard.py`
> reads through the `v_scheme_summary` and `v_fiscal_year_totals` views and
> filters on `fiscal_year` as a string. No figure on this page describes
> user-visible dashboard latency.

## Results

Three arms, 3 trials × 7 timed runs each after a discarded warm-up, 21 samples
per arm. Each trial drops the index, measures, rebuilds it, runs
`ANALYZE TABLE`, and measures again.

| Arm | Median | Mean | Min | Max | Std dev | vs baseline |
|---|---|---|---|---|---|---|
| No index | 1800.9 ms | 1838.8 | 1633.4 | 2224.0 | 133.3 | — |
| Index added, optimizer's plan | 1785.9 ms | 1833.0 | 1493.2 | 2568.1 | 214.6 | **+0.8%** |
| Index added, `FORCE INDEX` | 1661.3 ms | 1684.7 | 1579.9 | 1888.2 | 81.5 | **+7.8%** |

Index build time: 4.75 s (median of 3).

**Read the standard deviations before the medians.** The 15 ms gained by adding
the index sits inside a ±133 ms spread — it is noise, not a result. The 140 ms
from forcing the index is roughly 1.7 standard deviations: real, but modest.

All three arms return byte-identical result sets. The script asserts this; a
faster plan that returns a different answer is a bug, not an optimisation.

## Why adding the index changes nothing

`EXPLAIN FORMAT=JSON` before and after `CREATE INDEX` is **identical**:

| Table | Access | Key | Rows | Covering |
|---|---|---|---|---|
| `s` (schemes) | `index` | `uq_scheme_group` | 23 | yes |
| `ss` (sub_schemes) | `ref` | `idx_sub_scheme_scheme` | 4385 | yes |
| `b` (budget_data) | `eq_ref` | `uq_budget_sub_year` | 1 | no |

The optimizer never considers the new index. It drives from `schemes`, expands
to `sub_schemes`, and reaches `budget_data` through
`uq_budget_sub_year (sub_scheme_id, fiscal_year_id)` — a **UNIQUE** key, giving
an `eq_ref` single-row lookup, the most selective access available. A covering
index cannot beat one row per lookup on selectivity, so it is never chosen.

That unique key was in the schema from the beginning, added to enforce "one row
per sub-scheme per fiscal year". Its existence is why the covering index has
nothing to win.

## What forcing it actually does

With `FORCE INDEX (idx_budget_fy_sub_budget)`:

| Table | Access | Key | Rows | Covering |
|---|---|---|---|---|
| `b` (budget_data) | `ref` | `idx_budget_fy_sub_budget` | 1 | **yes** |

The index does exactly what a covering index is supposed to do — `budget` is
read from the index leaf, and the lookup into the clustered row disappears. That
is worth 140 ms.

It is only 7.8% because **the row lookups were never the bottleneck**. The plan
walks 92,240 `sub_schemes` rows and aggregates; `budget_data` access is one row
per iteration either way. Removing a page lookup from an operation that is not
dominant produces a proportionally small gain.

### The optimizer's cost model is wrong here

| Arm | Estimated cost | Measured median |
|---|---|---|
| No index | 45,442 | 1800.9 ms |
| Index added | 58,001 | 1785.9 ms |
| `FORCE INDEX` | **121,062** | **1661.3 ms** |

The plan MySQL rates as **2.7× more expensive** is the fastest one measured.
Cost units are not milliseconds and the two are not required to correlate, but
the ordering is inverted, which is the useful observation: on this schema and
data distribution the cost model mis-ranks the available plans. That, rather
than the 7.8%, is the interesting result.

## If not the schema, then what?

Two follow-ups, because "the index does nothing" is a finding but not an answer.

### Rewriting the query does not help

The plan's cost is the ~92,000 `eq_ref` probes into `budget_data`, so the
obvious move is to aggregate `budget_data` down to one row per scheme *before*
joining to `schemes`, leaving the outer join 23 rows to name instead of 92,240
to walk. Measured against a derived-table rewrite, and again with the fiscal
year passed as a literal instead of a `MAX()` subquery:

| Variant | Median | |
|---|---|---|
| Baseline | 1962.7 ms | *(not persisted)* |
| Derived-table aggregation | 1777.2 ms | *(not persisted)* |
| Derived table + literal fiscal year | 1705.4 ms | *(not persisted)* |

No script in `scripts/` runs this comparison, so these three medians were taken
interactively and never written to a file. Treat them as indicative. The claim
they support does not rest on them: the committed
`explain.*.query_block` blocks in `results/index_benchmark.json` show the same
plan shape across every arm — drive from `schemes`, `ref` into `sub_schemes`,
`eq_ref` into `budget_data` — which is the actual evidence that **the query text
is not the constraint**.

### The server configuration is worth 32.7%

`innodb_buffer_pool_size` was at the **128 MB default** against a table of
comparable size.
Measured by `scripts/benchmark_buffer_pool.py`, results in
`results/buffer_pool_benchmark.json`:

| Buffer pool | Median | Std dev | n |
|---|---|---|---|
| 128 MB (default) | 1783.5 ms | 139.4 | 21 |
| 1024 MB | **1200.7 ms** | 40.2 | 21 |

**582.8 ms faster — 32.7%.** That is four times what the covering index achieves
even when forced, and it is a configuration line rather than a schema change.

The arms are **interleaved** (128, 1024, 128, 1024 …) rather than run in blocks.
An earlier version of this experiment walked the sizes upward and could not
distinguish the improvement from caches warming; interleaved, the effect tracks
the size and not the order, and it reproduced in every round.

### It is not disk I/O — it is the adaptive hash index

Both arms report a **100.000% buffer pool hit rate with zero disk page reads**,
in every round. The 128 MB pool already holds the working set, so the 583 ms is
not pages being fetched from disk.

Two hypotheses were tested. The first — buffer pool instance contention — is
wrong: `innodb_buffer_pool_instances` is fixed at startup and stayed at **1** at
every size, so the count never changed.

The second is correct. InnoDB's adaptive hash index is sized as a fraction of
the buffer pool, and this query issues ~92,000 `eq_ref` probes per execution,
which is precisely the access pattern the AHI exists to short-circuit. Toggling
it turns the pool-size effect on and off:

A full 2×2, 21 samples per cell, interleaved, all at a 100% hit rate:

| | 128 MB | 1024 MB | pool effect |
|---|---|---|---|
| **AHI on** (default) | 1783.5 ms | **1200.7 ms** | **+32.7%** |
| **AHI off** | 1663.5 ms | 1688.0 ms | −1.5% |

**With the AHI disabled, buffer pool size stops mattering.** The entire effect
is mediated by it.

The sharper observation is in the bottom-left cell: at 128 MB the AHI is
**actively harmful** — 1783.5 ms with it on versus 1663.5 ms with it off, so the
default configuration is **7.2% slower** than simply disabling the feature. At
1 GB the same feature is worth **28.9%**.

The AHI is not a free optimisation. It is a hash index over frequently accessed
B-tree pages, and it has to be built and maintained. Sized from a 128 MB pool it
cannot cover the table plus 92,240 `sub_schemes` rows, so it thrashes: the
maintenance cost is paid and the lookup benefit is not collected. Given enough
pool to cover the working set, it pays for itself several times over.

So "raise the buffer pool" is the fix, but not for the reason one would assume.
It is not about caching more data — the data was already fully cached. It is
about giving the adaptive hash index enough room to be worth its own upkeep.

## Conclusion

The index is not worth adding on this evidence.

Unused, it earns nothing while costing write amplification on every
`INSERT`/`UPDATE` to a 921,696-row table and additional disk. It only pays at
all when forced, and then by 7.8% on a query the application does not run.

`database/schema.sql` is therefore left unchanged, and the 80–85% claims have
been removed from the README rather than restated at a lower figure. Adding an
index that the optimizer declines to use, and quoting a number that only appears
under `FORCE INDEX`, would repeat the original problem in a quieter voice.

**The available win was in the server, not the schema.** Sizing the buffer pool
is worth 32.7%; the index the documentation celebrated is worth 0.8%. Nothing in
this repository had ever looked at server configuration, and every published
figure concerned an index that did not exist.

### Recommended change

Set the buffer pool explicitly rather than inheriting the 128 MB default. On a
machine with 16 GB of RAM, 1–2 GB is unremarkable:

```ini
# my.ini / my.cnf, under [mysqld]
innodb_buffer_pool_size = 1G
```

This is a server setting, not a repository change, so it is a recommendation
here rather than a commit. `scripts/benchmark_buffer_pool.py` changes the value
dynamically for the duration of a run and restores it on exit, including on
error — it never writes to `my.ini`.

## Profiling the dashboard — where the time actually goes

Everything above measures `Q2` from `scripts/benchmark.py`. Profiling what
`app/dashboard.py` really issues (`scripts/benchmark_dashboard.py`, results in
`results/dashboard_benchmark.json`) relocated the bottleneck entirely.

### First, the data is two disjoint datasets

| fiscal_year_id | Years | Rows |
|---|---|---|
| 1–3 | 2020-2021 … 2022-2023 (real) | 86, 41, 39 — **166 total** |
| 403–412 | 2000-2001 … 2009-2010 (synthetic) | 92,153 **each** |

`scripts/generate_synthetic_data.py` backdates its rows to 2000–2010 while the
real data sits in 2020–2023, and it replicates one year's sub-scheme set ten
times — hence the identical 92,153 counts. "1,000,000 rows" is 92,153 rows in
ten copies, not a million distinct budget lines.

This matters because **`Q2` filters on `MAX(fiscal_year_id)` = 412**, a backdated
synthetic year with 92,153 rows, while the dashboard selects years *by name* and
shows the real ones. Every performance figure this project has published —
including the covering index and buffer pool results above — measures a year the
dashboard never displays.

### The measured page load

Default filter state (`"All Years"` / `"All Schemes"`, which are the first
selectbox options at `dashboard.py:410-412`, so this is what loads with no user
interaction).

> **These are the original August medians, and they were never persisted.**
> `scripts/benchmark_dashboard.py` writes to a fixed path, so the post-fix run
> overwrote them; `results/dashboard_benchmark.json` holds only the after-state.
> They have since been re-measured — see
> [Reproducing the pre-fix figures](#reproducing-the-pre-fix-figures). The
> 205,039 ms reproduced within 3.7%. The other two did not.

| Loader | Median | Rows |
|---|---|---|
| `load_fiscal_years` | 1.0 ms | 13 |
| `load_schemes` | 0.9 ms | 23 |
| `load_scheme_summary("2021-2022")` | 1.3 ms | 18 |
| `load_budget_overview("2021-2022")` | 3.9 ms | 41 |
| insights × 4 | 2.4 – 3.1 ms | 1 each |
| insights: highest growth | 873 ms | 1 |
| `load_fiscal_year_totals` | **11,144 ms** | 13 |
| `load_scheme_summary("All Years")` | **205,039 ms** | 23 |
| **Total** | **217,076 ms — 3 min 37 s** | |

That total *excludes* `load_budget_overview("All Years","All Schemes")`, which
returns all 921,696 rows into a pandas DataFrame.

**Two queries are 99.5% of the page load.** Every other query is single-digit
milliseconds. The covering index, which the documentation celebrated, addresses
none of them.

### Why those two are slow

`v_fiscal_year_totals` is grand totals per year, so no `WHERE` can narrow it —
it aggregates the whole table by definition, and it runs on every page load.

`v_scheme_summary` groups by `(scheme_name, group_name, fiscal_year)` *and*
computes `COUNT(DISTINCT sub_scheme_id)` per group. Filtered to one year it costs
1.3 ms, because MySQL 8 pushes the predicate into the view. Unfiltered there is
nothing to push, so it computes `COUNT(DISTINCT)` for every
`(scheme, group, fiscal_year)` group across 921,696 rows — **287** of them — and
the outer query re-aggregates the result.

> Earlier revisions of this page said 299. That is 23 schemes × 13 fiscal years,
> which is a product, not a count: the ten synthetic years each carry all 23
> schemes, but the three real years carry only 17, 18 and 22, so twelve
> combinations do not exist. Measured, recorded in
> `results/dashboard_baseline_benchmark.json`.

The single-year path being 1.3 ms and the all-years path being 205 s is the same
view, and the difference is entirely whether a predicate can be pushed down.

### What was fixed

Three changes, each verified to return byte-identical rows *before* being
applied. Re-measured with the same script:

| | Before (Aug) | After (Aug) | Factor |
|---|---|---|---|
| `load_scheme_summary("All Years")` | 205,039 ms | 23,039 ms | **8.9×** |
| `load_fiscal_year_totals` | 11,144 ms | 6,915 ms | 1.6× |
| `insights: highest growth` | 873.1 ms | 2.37 ms | **368×** |
| **Page load** | **217,076 ms** | **29,971 ms** | **7.2×** |

Every figure in the *After* column is read from
`results/dashboard_benchmark.json`, and 29,971 ms is the exact sum of the eleven
medians it records. The *Before* column was never persisted. A later attempt to
reproduce it recovered the page-load figures but not the other two — the two
middle factors above should be read against
[Reproducing the pre-fix figures](#reproducing-the-pre-fix-figures) rather than
quoted on their own.

**1. `v_fiscal_year_totals` joined a table it did not need.** It joined
`sub_schemes` solely to compute `COUNT(DISTINCT ss.sub_scheme_id)`, but
`sub_scheme_id` is a column on `budget_data`. The join fetched 92,240 rows to
read a value already in hand. Safe to remove by construction, not by judgement:
an `INNER JOIN` on a `NOT NULL` foreign key can neither filter rows nor
duplicate them.

**2. `load_scheme_summary("All Years")` now aggregates the base tables
directly.** Going through `v_scheme_summary` unfiltered makes MySQL group by
`(scheme, group, fiscal_year)` and compute `COUNT(DISTINCT sub_scheme_id)` per
group, only for the outer query to throw the per-year split away.

**3. `insights: highest growth` splits its two years into a `UNION ALL`.** This
is the most interesting of the three. The query used
`WHERE fiscal_year IN ('2021-2022','2022-2023')` and cost 873 ms while its four
sibling insight queries — identical in shape but using `fiscal_year = '...'` —
cost 2–3 ms.

The timings that first suggested this are the least reliable evidence for it, so
the argument rests on the plans instead. `EXPLAIN FORMAT=JSON` for all three
forms is committed in `results/dashboard_baseline_benchmark.json`:

| | `IN (...)` | `OR` | `UNION ALL` of two `=` |
|---|---|---|---|
| `query_cost` | 22,695.32 | **22,695.32** | **11.50** |
| rows materialised from `v_scheme_summary` | 201,714 | 201,714 | **41 + 39 = 80** |
| `fiscal_years` access | `range` | `range` | **`const`** per branch |

**`IN` and `OR` produce byte-identical plans at identical cost.** That is what
rules out `IN` itself — not the two timings that happened to land 1.3 ms apart,
which on a re-run landed 481 ms apart. And the pushdown is directly visible: a
single equality collapses `fiscal_years` to a `const` and the view materialises
80 rows; a disjunction cannot be folded in, so it materialises 201,714.

**MySQL pushes a single equality predicate into a `GROUP BY` view but will not
push a disjunction.** 1,973× on estimated cost, 2,521× on rows materialised.
Neither number moves with machine load, which is why they are quoted here in
preference to the wall-clock. All three forms return byte-identical rows; the
digests are recorded alongside the plans.

That one mechanism explains both this and the 8.9× above, and it is the actual
performance story of this dashboard — not indexing.

### What remains

29,971 ms is still slow, and 97% of it is the two queries that cannot use
pushdown because they are unfiltered by definition.

The largest remaining lever is not a query change. `"All Years"` and
`"All Schemes"` are the **first** options in the selectbox
(`dashboard.py:410-412`), so they are the default and the 23-second aggregation
runs before the user touches anything. Defaulting to the most recent real fiscal
year would put the page load in single-digit milliseconds. That is a product
decision about what a user should see first, so it is recorded here rather than
made unilaterally.

### The README's two headline claims were never true together

"Benchmarks run on 1,000,000 rows" and "Dashboard Load Time ~38 ms" cannot both
hold. On the real 166-row data the dashboard genuinely is that fast. At the
advertised 1M-row scale its default page load is over three minutes.

## Reproducing the pre-fix figures

The *Before* column above was measured once, in August, by a script that writes
to a fixed path — so the post-fix run overwrote it and nothing survived except
prose. `scripts/benchmark_dashboard_baseline.py` re-measures it: three of the
four figures are recoverable read-only, because the fix bypassed
`v_scheme_summary` rather than modifying it, and the fourth restores the removed
join to `v_fiscal_year_totals` for the duration of the run and puts it back in a
`finally`. Results in `results/dashboard_baseline_benchmark.json`.

| | August | Re-run | |
|---|---|---|---|
| `load_scheme_summary("All Years")` pre-fix | 205,039 ms | 197,479 ms | −3.7% |
| `load_fiscal_year_totals` pre-fix | 11,144 ms | **26,724 ms** | 2.4× |
| growth `IN` | 873 ms | **1,550 ms** | 1.8× |
| growth `OR` | 874.8 ms | **1,069 ms** | 1.2× |
| growth `UNION ALL` | 2.37 ms | **6.50 ms** | 2.7× |
| `v_scheme_summary` groups | 299 | **287** | wrong, see above |

The post-fix state was re-measured in the same session as a control, and it
reproduces: `load_scheme_summary("All Years")` 23,039 → 23,500 ms, 
`load_fiscal_year_totals` 6,915 → 7,167 ms, page load 29,971 → 30,687 ms. All
within 4%. **So the machine is not simply slower, and the drift is confined to
the figures that were never written to a file.**

`load_fiscal_year_totals` is the one worth explaining. It was not an ordering
effect — measured first, before anything expensive, it gives 26,863 ms against
26,724 ms measured last. The plans put the pre-fix query at a `query_cost` of
1,736,195 against 132,309 for the post-fix one, a 13× gap: the removed join
forces `fy`(13) × `ss`(92,087) nested loops, roughly 1.2 million `eq_ref` probes
into `budget_data`, where the post-fix plan reaches `budget_data` once by `ref`.
That is a better account of why the join was expensive than "it fetched 92,240
rows to read a value it already had", and it makes 26.7 s easier to believe than
11.1 s. But this page cannot say the August figure was wrong — only that it does
not reproduce, and that no artifact exists to adjudicate.

Both sets are left standing rather than one being quietly replaced. Three of the
sub-second figures also vary run to run by 30–90%, so the honest reading is that
these queries were sampled at n=3 rather than measured, and that the mechanism —
which the plans establish exactly — was always the load-bearing part.

## What generalizes, and what does not

Every figure on this page was measured on the dataset described under
[First, the data is two disjoint datasets](#first-the-data-is-two-disjoint-datasets):
166 real rows and 921,530 synthetic ones — 92,153 replicated across ten
backdated years — spread over 92,240 `sub_schemes` under 23 `schemes`, a fan-out
of roughly 4,010 sub-schemes per scheme. That is not the shape of a real budget
hierarchy, and it is
load-bearing for some of the results above but not others. Quoting all three as
though they were equally portable would repeat, in a different form, the failure
this document exists to correct.

| Finding | Rests on | Portable? |
|---|---|---|
| `=` pushes into a `GROUP BY` view, `IN`/`OR` does not | MySQL's rewrite rules | **Yes** |
| The AHI mediates the buffer pool effect | working set vs. pool size | **Mechanism yes, numbers no** |
| The cost model mis-ranks the available plans | this schema's join shape | **Unknown** |

**Predicate pushdown is the robust one.** The magnitudes scale with row and group
counts, but the behaviour — one equality is pushed into the view, a disjunction
is not — is a property of the optimizer, not of this data. Any table large enough
for the aggregation to cost something will show it, which is why the growth
query and the 8.9× share a single explanation. It is also the finding that
survived re-measurement intact, because it is visible in the plan and not only
in the clock. This is the finding worth carrying to another
codebase.

**The AHI result reproduces in shape, not in percentages.** That the adaptive
hash index is sized as a fraction of the buffer pool, and thrashes when it cannot
cover the working set, is general. The specific split — 7.2% *harmful* at 128 MB,
worth 28.9% at 1 GB — is a function of the working-set-to-pool ratio and of the
~92,000 `eq_ref` probes this query issues per execution. That probe count is
itself an artifact of `sub_schemes` being nearly as large as one year of
`budget_data`. At a realistic fan-out the probe count falls and the AHI's
leverage falls with it. Expect the same curve, not the same numbers.

**The cost-model inversion is an observation, not a law.** `FORCE INDEX` was
rated 2.7× more expensive than the chosen plan and measured faster — on this
schema, at this cardinality, at this buffer pool size. It is recorded because an
inverted ordering is worth knowing can happen, not because it predicts anything
elsewhere.

One gap worth naming: because the generator replicates a single year ten times,
every backdated year has identical cardinality. Nothing here tests plan stability
across skewed partitions, which is the case real budget data would actually
present.

If the dataset is ever replaced with real data at a realistic fan-out, re-run all
three scripts before quoting any figure above. The pushdown result should hold;
the buffer pool percentages will move; the cost-model comparison may not survive
at all.

## Reproducing

```bash
python scripts/benchmark_index.py --trials 3 --repeats 7
```

```bash
python scripts/benchmark_buffer_pool.py --rounds 4 --repeats 7
```

```bash
python scripts/benchmark_dashboard.py --repeats 3
```

```bash
python scripts/benchmark_dashboard_baseline.py   # ~15 min; needs the database
```

These write `index_benchmark.json`, `buffer_pool_benchmark.json`,
`dashboard_benchmark.json` and `dashboard_baseline_benchmark.json` under
`results/`, containing every individual sample, the full `EXPLAIN FORMAT=JSON`
for each arm, the hit rates, and the environment the run was taken in. Different
hardware will produce different latencies; replace the tables above from your
own output rather than trusting these.

The baseline script is the only one that modifies the schema. It restores
`v_fiscal_year_totals` in a `finally`, verifies the restore, and reports whether
the recovered definition is byte-identical to the one it found. If it is ever
killed between the swap and the restore, re-run `database/views.sql`. It also
checkpoints after every measurement, because the first attempt at that run was
killed partway through and a 205-second query should outlive the process that
produced it.
