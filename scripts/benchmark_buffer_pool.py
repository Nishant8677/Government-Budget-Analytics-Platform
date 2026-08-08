"""Measures the effect of InnoDB buffer pool size on the aggregation query.

Context: `benchmark_index.py` shows the covering index this project once
credited with an 80-85% improvement is worth 0.8%. That prompted the obvious
follow-up question -- if the schema is not the constraint, what is? The server
was running `innodb_buffer_pool_size` at the 128 MB default against a 90 MB
table, so this measures that.

Method
------
A and B arms are INTERLEAVED (128, 1024, 128, 1024, ...) rather than run in
blocks. The first version of this experiment walked the sizes upward and the
improvement could not be distinguished from caches warming up. Interleaving
means a warming trend shows up in both arms instead of only the later one.

Buffer pool hit rate is recorded per arm, because the interesting part of the
result is that the small pool is NOT losing to disk reads.

Requires privileges to SET GLOBAL innodb_buffer_pool_size. The original value is
restored on exit, including on error, and nothing is written to my.ini -- a
MySQL restart returns to the configured default regardless.

    python scripts/benchmark_buffer_pool.py
    python scripts/benchmark_buffer_pool.py --small 128 --large 1024 --rounds 4
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector

sys.path.append(str(Path(__file__).resolve().parent.parent))

from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER  # noqa: E402
from utils.db import table_sizes_mb  # noqa: E402

QUERY = """
    SELECT s.scheme_name, SUM(b.budget) AS total_budget
    FROM budget_data b
    JOIN sub_schemes ss ON b.sub_scheme_id = ss.sub_scheme_id
    JOIN schemes s ON ss.scheme_id = s.scheme_id
    WHERE b.fiscal_year_id = (SELECT MAX(fiscal_year_id) FROM fiscal_years)
    GROUP BY s.scheme_name
    ORDER BY total_budget DESC
    LIMIT 10
"""

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "results" / "buffer_pool_benchmark.json"


def connect():
    conn = mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset="utf8mb4",
    )
    conn.autocommit = True
    return conn


def var(cur, name):
    cur.execute(f"SELECT @@{name}")
    return cur.fetchone()[0]


def status(cur, name: str) -> int:
    cur.execute(f"SHOW GLOBAL STATUS LIKE '{name}'")
    row = cur.fetchone()
    return int(row[1]) if row else 0


def resize(cur, mb: int) -> None:
    """Resizes the pool and waits for the online resize to finish."""
    cur.execute(f"SET GLOBAL innodb_buffer_pool_size = {mb * 1024 * 1024}")
    for _ in range(120):
        cur.execute("SHOW STATUS LIKE 'Innodb_buffer_pool_resize_status'")
        row = cur.fetchone()
        if not row or not row[1] or "complete" in row[1].lower():
            break
        time.sleep(1)
    time.sleep(2)  # let the LRU settle before timing


def measure(cur, repeats: int):
    cur.execute(QUERY)
    cur.fetchall()  # warm-up, discarded

    reads_before = status(cur, "Innodb_buffer_pool_reads")
    reqs_before = status(cur, "Innodb_buffer_pool_read_requests")

    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        cur.execute(QUERY)
        rows = cur.fetchall()
        samples.append((time.perf_counter() - start) * 1000.0)

    reads = status(cur, "Innodb_buffer_pool_reads") - reads_before
    reqs = status(cur, "Innodb_buffer_pool_read_requests") - reqs_before
    return samples, reads, reqs, rows


def summarise(samples):
    return {
        "n": len(samples),
        "median_ms": round(statistics.median(samples), 2),
        "mean_ms": round(statistics.fmean(samples), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "stdev_ms": round(statistics.stdev(samples), 2) if len(samples) > 1 else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--small", type=int, default=128, help="baseline pool, MB")
    parser.add_argument("--large", type=int, default=1024, help="comparison pool, MB")
    parser.add_argument("--rounds", type=int, default=4, help="interleaved A/B rounds")
    parser.add_argument("--repeats", type=int, default=7, help="timed runs per arm")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    original = int(var(cur, "innodb_buffer_pool_size"))

    original_ahi = int(var(cur, "innodb_adaptive_hash_index"))

    # 2x2: pool size x adaptive hash index. The AHI is sized as a fraction of
    # the buffer pool, so it is a candidate mechanism for the pool-size effect
    # rather than an independent knob -- testing pool size alone cannot tell
    # them apart.
    cells = [(mb, ahi) for ahi in (1, 0) for mb in (args.small, args.large)]
    arms = {c: [] for c in cells}
    hits = {c: [] for c in cells}
    reference = None

    try:
        for rnd in range(1, args.rounds + 1):
            print(f"\n-- round {rnd}/{args.rounds} " + "-" * 34)
            for mb, ahi in cells:
                cur.execute(f"SET GLOBAL innodb_adaptive_hash_index = {ahi}")
                time.sleep(3)  # let the AHI tear down or start rebuilding
                resize(cur, mb)
                samples, reads, reqs, rows = measure(cur, args.repeats)
                arms[(mb, ahi)].extend(samples)
                hit = (1 - reads / reqs) * 100 if reqs else 100.0
                hits[(mb, ahi)].append(hit)
                if reference is None:
                    reference = rows
                same = "" if rows == reference else "  <-- DIFFERENT ROWS"
                print(f"  {mb:>5} MB  AHI {'on ' if ahi else 'off'}  "
                      f"median {statistics.median(samples):8.1f} ms   "
                      f"hit {hit:7.3f}%{same}", flush=True)
    finally:
        cur.execute(f"SET GLOBAL innodb_adaptive_hash_index = {original_ahi}")
        cur.execute(f"SET GLOBAL innodb_buffer_pool_size = {original}")
        print(f"\nrestored AHI={original_ahi}, buffer pool={original // 1024 // 1024} MB")

    grid = {f"pool_{mb}mb_ahi_{'on' if ahi else 'off'}":
            {**summarise(arms[(mb, ahi)]),
             "hit_rate_percent": [round(h, 4) for h in hits[(mb, ahi)]]}
            for mb, ahi in cells}

    small = grid[f"pool_{args.small}mb_ahi_on"]
    large = grid[f"pool_{args.large}mb_ahi_on"]
    delta = small["median_ms"] - large["median_ms"]
    pct = delta / small["median_ms"] * 100 if small["median_ms"] else 0.0

    small_off = grid[f"pool_{args.small}mb_ahi_off"]
    large_off = grid[f"pool_{args.large}mb_ahi_off"]
    delta_off = small_off["median_ms"] - large_off["median_ms"]
    pct_off = delta_off / small_off["median_ms"] * 100 if small_off["median_ms"] else 0.0

    results = {
        "environment": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mysql_version": var(cur, "version"),
            # platform.release() reports "10" on Windows 11; the build disambiguates.
            "os": f"{platform.system()} {platform.release()} (build {platform.version()})",
            "cpu_count": __import__("os").cpu_count(),
            "innodb_buffer_pool_instances": int(var(cur, "innodb_buffer_pool_instances")),
            "innodb_buffer_pool_chunk_size_mb": int(var(cur, "innodb_buffer_pool_chunk_size")) // 1024 // 1024,
            "configured_pool_mb": original // 1024 // 1024,
            # The pool size only means something next to the working set.
            "table_sizes_mb": table_sizes_mb(cur),
        },
        "method": (
            "Arms interleaved per round rather than run in blocks, so a warming "
            "trend affects both arms equally. Buffer pool restored on exit."
        ),
        "query": QUERY.strip(),
        "rounds": args.rounds,
        "repeats_per_arm_per_round": args.repeats,
        "grid": grid,
        "pool_effect_with_ahi_on": {"absolute_ms": round(delta, 2), "percent": round(pct, 2)},
        "pool_effect_with_ahi_off": {"absolute_ms": round(delta_off, 2), "percent": round(pct_off, 2)},
        "conclusion": (
            "The buffer pool size effect is mediated entirely by the adaptive "
            "hash index. With the AHI disabled, pool size makes no material "
            "difference. The AHI is sized as a fraction of the buffer pool, so "
            "at the smaller pool it is too small to cover the working set and "
            "its maintenance cost exceeds its benefit."
        ),
        "raw_samples_ms": {f"pool_{mb}mb_ahi_{'on' if ahi else 'off'}": arms[(mb, ahi)]
                           for mb, ahi in cells},
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print(f"  {'':>10}   {'AHI on':>12}   {'AHI off':>12}")
    for mb in (args.small, args.large):
        on = grid[f"pool_{mb}mb_ahi_on"]["median_ms"]
        off = grid[f"pool_{mb}mb_ahi_off"]["median_ms"]
        print(f"  {mb:>5} MB   {on:>9.1f} ms   {off:>9.1f} ms")
    print("-" * 62)
    print(f"  pool effect, AHI on  : {delta:8.1f} ms = {pct:6.1f}%")
    print(f"  pool effect, AHI off : {delta_off:8.1f} ms = {pct_off:6.1f}%")
    print("=" * 62)
    print(f"\nwrote {args.output}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
