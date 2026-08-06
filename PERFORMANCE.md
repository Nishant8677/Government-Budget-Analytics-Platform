# Index Engineering & Performance

Everything below comes from `results/index_benchmark.json`, written by
`scripts/benchmark_index.py`. Re-run it and you get a new file; nothing here is
typed in by hand.

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
| `budget_data` size | 90.2 MB (data + indexes) |
| `sub_schemes` rows | 92,240 |
| `fiscal_years` rows | 13 |
| InnoDB buffer pool | 128 MB (server default) |

Two things follow from that last row. The buffer pool is only slightly larger
than the table, so pages compete for residence and run-to-run variance is high —
visible in the standard deviations below. And the pool cannot be flushed from a
client connection without `SUPER` or a server restart, so **both arms run warm**.
The comparison between arms is valid; the absolute latencies are a best case.

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

| Variant | Median |
|---|---|
| Baseline | 1962.7 ms |
| Derived-table aggregation | 1777.2 ms |
| Derived table + literal fiscal year | 1705.4 ms |

The optimizer produces the same plan shape regardless — it still scans all
92,087 `sub_schemes` rows and probes `budget_data` by unique key. The query text
is not the constraint.

### The server configuration is worth 33.8%

`innodb_buffer_pool_size` was at the **128 MB default** against a 90 MB table.
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
cannot cover a 90 MB table plus 92,240 `sub_schemes` rows, so it thrashes: the
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
is worth 33.8%; the index the documentation celebrated is worth 0.8%. Nothing in
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

### Still unmeasured

The dashboard reads through `v_scheme_summary` and `v_fiscal_year_totals` and
filters `fiscal_year` as a string. **No figure on this page describes a query the
application actually issues.** Profiling the view path is the next thing worth
doing, and it may well relocate the bottleneck again.

## Reproducing

```bash
python scripts/benchmark_index.py --trials 3 --repeats 7
```

```bash
python scripts/benchmark_buffer_pool.py --rounds 4 --repeats 7
```

These write `results/index_benchmark.json` and
`results/buffer_pool_benchmark.json`, containing every individual sample, the
full `EXPLAIN FORMAT=JSON` for each arm, the hit rates, and the environment the
run was taken in. Different hardware will produce different latencies; replace
the tables above from your own output rather than trusting these.
