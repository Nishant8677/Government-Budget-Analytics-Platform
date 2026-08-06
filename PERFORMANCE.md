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

## Conclusion

The index is not worth adding on this evidence.

Unused, it earns nothing while costing write amplification on every
`INSERT`/`UPDATE` to a 921,696-row table and additional disk. It only pays at
all when forced, and then by 7.8% on a query the application does not run.

`database/schema.sql` is therefore left unchanged, and the 80–85% claims have
been removed from the README rather than restated at a lower figure. Adding an
index that the optimizer declines to use, and quoting a number that only appears
under `FORCE INDEX`, would repeat the original problem in a quieter voice.

If this query ever became load-bearing, the honest next step is not the covering
index — it is to look at why the plan iterates 92,240 `sub_schemes` rows to
return 10 grouped values.

## Reproducing

```bash
python scripts/benchmark_index.py --trials 3 --repeats 7
```

Writes `results/index_benchmark.json` containing every individual sample, the
full `EXPLAIN FORMAT=JSON` for all three arms, and the environment the run was
taken in. Different hardware will produce different latencies; replace the
tables above from your own output rather than trusting these.
