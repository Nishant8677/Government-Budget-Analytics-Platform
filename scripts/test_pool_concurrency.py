"""Tests whether concurrent dashboard readers can share one connection safely.

app/dashboard.py caches its database handle with @st.cache_resource, which
Streamlit shares across every session and user in the process -- not per session,
as an earlier version of that module's docstring claimed. Streamlit runs each
session in its own thread, and a mysql-connector connection is not thread-safe.

So the question is not academic: with a bare cached connection, two people using
the dashboard at the same time issue queries down one socket. This script runs
both designs under deliberate overlap and reports what each does.

Method
------
Two arms, same workload, same query, one variable -- whether the threads share a
single connection or check out from a pool.

    shared  : one connection, every thread using it concurrently (old design)
    pooled  : utils.db.get_pool(), each thread checking out and returning (new)

The query is `SELECT SLEEP(...)` alongside a real read, so every thread is
guaranteed to be mid-statement while the others start. Without that the threads
would serialise by luck and the shared arm could pass by accident.

A pass for the pooled arm requires every thread to return the correct row count.
Errors, wrong results and exceptions are all failures -- a silently wrong result
set is worse than a crash, so the check is on returned data, not just on absence
of exceptions.

The shared arm runs in a subprocess, and that is not tidiness. mysql-connector's
C extension does not merely misbehave when threads share a connection: on the
first run of this script it took a SIGSEGV and killed the interpreter before any
result could be printed. An arm that can abort the process cannot be allowed to
host the test reporting it, so it is isolated and its exit status is the result.

    python scripts/test_pool_concurrency.py
    python scripts/test_pool_concurrency.py --arm shared   (internal, subprocess)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from utils.db import get_connection, get_pool  # noqa: E402

THREADS = 6
HOLD_SECONDS = 0.25
QUERY = f"SELECT SLEEP({HOLD_SECONDS}) AS slept, scheme_name FROM schemes ORDER BY scheme_name"

# pandas warns when handed a DBAPI connection instead of a SQLAlchemy engine.
# Irrelevant here and it would drown the output.
warnings.filterwarnings("ignore", message=".*only supports SQLAlchemy.*")


def expected_rows() -> int:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM schemes")
        return cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()


def run_arm(name: str, want: int) -> dict[str, Any]:
    """Run THREADS concurrent readers under one of the two designs."""
    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    start = threading.Barrier(THREADS)

    shared_conn = get_connection() if name == "shared" else None
    pool = get_pool() if name == "pooled" else None

    def worker(idx: int) -> None:
        outcome: dict[str, Any] = {"thread": idx}
        try:
            start.wait(timeout=30)  # release all threads at the same instant
            if name == "shared":
                df = pd.read_sql(QUERY, shared_conn)
            else:
                conn = pool.get_connection()
                try:
                    conn.ping(reconnect=True, attempts=2, delay=1)
                    df = pd.read_sql(QUERY, conn)
                finally:
                    conn.close()
            outcome["rows"] = len(df)
            outcome["ok"] = len(df) == want
        except Exception as exc:  # noqa: BLE001 - the point is to record these
            outcome["ok"] = False
            outcome["error"] = f"{type(exc).__name__}: {str(exc)[:90]}"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,), name=f"{name}-{i}")
               for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    if shared_conn is not None:
        try:
            shared_conn.close()
        except Exception:  # noqa: BLE001 - it may already be wedged
            pass

    ok = sum(1 for r in results if r.get("ok"))
    errors = sorted({r["error"] for r in results if "error" in r})
    wrong = [r for r in results if "error" not in r and not r.get("ok")]
    return {"arm": name, "ok": ok, "total": len(results),
            "errors": errors, "wrong_row_counts": [r["rows"] for r in wrong]}


def report(res: dict[str, Any]) -> None:
    print(f"\n  arm                  : {res['arm']}")
    print(f"  threads correct      : {res['ok']}/{res['total']}")
    if res["wrong_row_counts"]:
        print(f"  wrong row counts     : {res['wrong_row_counts']}")
    for err in res["errors"]:
        print(f"  error                : {err}")


def run_shared_isolated(want: int) -> dict[str, Any]:
    """Run the shared-connection arm where a crash cannot take this process down."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--arm", "shared",
         "--want", str(want)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    crashed = proc.returncode not in (0, 1)
    return {
        "returncode": proc.returncode,
        "crashed": crashed,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip()[-200:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["shared"], help=argparse.SUPPRESS)
    parser.add_argument("--want", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()

    # Subprocess mode: run the unsafe arm and report through the exit code.
    if args.arm == "shared":
        res = run_arm("shared", args.want)
        report(res)
        return 0 if res["ok"] == res["total"] else 1

    want = expected_rows()
    print("=" * 72)
    print("dashboard connection handling -- concurrent reader check")
    print(f"{THREADS} threads, released together, each holding a statement "
          f"for {HOLD_SECONDS}s")
    print(f"expected rows per query: {want}")
    print("=" * 72)

    print("\n  [arm 1] shared connection, isolated in a subprocess")
    shared = run_shared_isolated(want)
    for line in shared["stdout"].splitlines():
        print(f"  {line}")
    print(f"  exit code            : {shared['returncode']}"
          f"{'  <-- abnormal termination' if shared['crashed'] else ''}")
    if shared["crashed"] and shared["stderr"]:
        print(f"  stderr tail          : {shared['stderr']}")

    print("\n  [arm 2] pooled, in-process")
    pooled = run_arm("pooled", want)
    report(pooled)

    print("\n" + "=" * 72)
    if pooled["ok"] != pooled["total"]:
        print("  FAIL. The pool did not serve every reader correctly. Do not ship;")
        print("  a dashboard returning wrong rows under load is worse than a slow one.")
        return 1

    print(f"  pooled            : {pooled['ok']}/{pooled['total']} correct")
    if shared["crashed"]:
        print(f"  shared connection : process died, exit {shared['returncode']}")
        print("\n  PASS. Every pooled reader got the right rows. The shared connection")
        print("  did not merely return bad data -- it killed the interpreter, because")
        print("  mysql-connector's C extension is not thread-safe and concurrent use")
        print("  corrupts its internal state. That is what a bare connection cached by")
        print("  @st.cache_resource exposes to concurrent Streamlit sessions.")
    elif shared["returncode"] == 1:
        print("  shared connection : survived, but returned wrong or failed reads")
        print("\n  PASS. The pool served every reader correctly and the shared")
        print("  connection did not.")
    else:
        print("  shared connection : passed too")
        print("\n  PASS for the pool, but this run does not demonstrate the bug it was")
        print("  written to catch -- the driver may have serialised internally. Treat")
        print("  the pool as correct by construction, since connections are documented")
        print("  as not thread-safe, rather than as proven here.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
