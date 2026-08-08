"""Tests whether etl.load._upsert_group has a lost-update race under REPEATABLE READ.

ADR 3 says the upsert pattern "delegates concurrency control and race-condition
prevention directly to the InnoDB storage engine". ADR 5 chose REPEATABLE READ.
This script checks whether those two decisions hold together.

The hypothesis
--------------
_upsert_group runs INSERT IGNORE, then a plain SELECT, and subscripts the result:

    cursor.execute("INSERT IGNORE INTO `groups` ...")
    cursor.execute("SELECT group_id FROM `groups` WHERE group_name = %s")
    return cursor.fetchone()[0]

Under REPEATABLE READ, a transaction's consistent-read snapshot is established at
its *first consistent read*, not at BEGIN. INSERT IGNORE is a locking write and
does not establish it. So from the second record onward in a load() batch the
snapshot is already fixed, and if a concurrent worker commits a new group in the
meantime:

  - INSERT IGNORE no-ops, because the row exists at the write layer
  - the plain SELECT reads the older snapshot and returns nothing
  - fetchone() returns None, and None[0] raises TypeError

That is a crash, not a silent corruption, which makes it testable exactly.

Method
------
Two arms, REPEATABLE READ and READ COMMITTED, run with a forced interleaving
rather than by hoping two threads collide. Worker A opens a transaction and
upserts a first group, which pins its snapshot. Worker B then inserts and
commits a *second* group. Only then does A upsert that second group. The
isolation level is the single variable between arms.

READ COMMITTED is the control: it takes a fresh snapshot per statement, so if
the mechanism is what the hypothesis claims, that arm must succeed while the
REPEATABLE READ arm fails. An arm that fails in both directions would mean
something other than isolation is responsible.

    python scripts/test_upsert_race.py

Writes nothing permanent: worker A rolls back, worker B's row is deleted in a
finally, and every name used is prefixed RACETEST- so a partial run is trivial
to identify and clean up by hand.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent))

from etl.load import _upsert_group  # noqa: E402
from utils.db import get_connection  # noqa: E402

PREFIX = "RACETEST-"  # no LIKE wildcards, so cleanup cannot over-match
STEP_TIMEOUT = 15.0


def cleanup(names: list[str]) -> int:
    conn = get_connection()
    cur = conn.cursor()
    removed = 0
    try:
        for name in names:
            cur.execute("DELETE FROM `groups` WHERE group_name = %s", (name,))
            removed += cur.rowcount
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return removed


def run_arm(isolation: str, tag: str) -> dict[str, Any]:
    """Force the interleaving described above and report what worker A got."""
    first = f"{PREFIX}{tag}-first"
    contended = f"{PREFIX}{tag}-contended"

    a_pinned = threading.Event()   # A has established its read snapshot
    b_committed = threading.Event()  # B has inserted and committed
    outcome: dict[str, Any] = {"isolation": isolation, "names": [first, contended]}

    def worker_b() -> None:
        if not a_pinned.wait(STEP_TIMEOUT):
            outcome["b_error"] = "timed out waiting for A to pin its snapshot"
            return
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO `groups` (group_name) VALUES (%s)", (contended,))
            outcome["b_inserted_id"] = cur.lastrowid
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            outcome["b_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            cur.close()
            conn.close()
            b_committed.set()

    thread = threading.Thread(target=worker_b, name=f"B-{tag}", daemon=True)
    thread.start()

    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        conn.start_transaction(isolation_level=isolation)

        # First upsert. Its SELECT is what pins the snapshot under REPEATABLE READ.
        outcome["first_id"] = _upsert_group(cur, first)
        a_pinned.set()

        if not b_committed.wait(STEP_TIMEOUT):
            outcome["result"] = "INCONCLUSIVE"
            outcome["detail"] = "worker B did not finish in time"
            return outcome

        # Diagnostic: does the write layer see B's row while the read layer does not?
        cur.execute("INSERT IGNORE INTO `groups` (group_name) VALUES (%s)", (contended,))
        cur.execute("SHOW WARNINGS")
        outcome["insert_ignore_warnings"] = [w[2] for w in cur.fetchall()]
        cur.execute("SELECT group_id FROM `groups` WHERE group_name = %s", (contended,))
        outcome["plain_select_saw_row"] = cur.fetchone() is not None

        # The real thing: the production helper, on the contended name.
        try:
            outcome["contended_id"] = _upsert_group(cur, contended)
            outcome["result"] = "NO RACE"
        except TypeError as exc:
            outcome["result"] = "RACE REPRODUCED"
            outcome["detail"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            outcome["result"] = "UNEXPECTED"
            outcome["detail"] = f"{type(exc).__name__}: {exc}"
        return outcome
    finally:
        conn.rollback()  # A's own rows must not persist
        cur.close()
        conn.close()
        thread.join(timeout=STEP_TIMEOUT)


def report(res: dict[str, Any]) -> None:
    print(f"\n  isolation level      : {res['isolation']}")
    print(f"  INSERT IGNORE warns  : {res.get('insert_ignore_warnings')}")
    print(f"  plain SELECT saw row : {res.get('plain_select_saw_row')}")
    print(f"  worker B committed   : id={res.get('b_inserted_id')} {res.get('b_error','')}")
    print(f"  RESULT               : {res['result']}")
    if res.get("detail"):
        print(f"  detail               : {res['detail']}")


def main() -> int:
    tag = str(int(time.time()))
    created: list[str] = []
    arms = ("REPEATABLE READ", "READ COMMITTED")
    results = []

    print("=" * 72)
    print("etl.load._upsert_group -- concurrent-insert race check")
    print("=" * 72)

    try:
        for i, iso in enumerate(arms):
            res = run_arm(iso, f"{tag}-{i}")
            created.extend(res["names"])
            results.append(res)
            report(res)
    finally:
        removed = cleanup(created)
        print(f"\n  cleanup: removed {removed} RACETEST- row(s)")

    print("\n" + "=" * 72)
    rr = next(r for r in results if r["isolation"] == "REPEATABLE READ")
    rc = next(r for r in results if r["isolation"] == "READ COMMITTED")
    print(f"  REPEATABLE READ : {rr['result']}")
    print(f"  READ COMMITTED  : {rc['result']}")

    if rr["result"] == "RACE REPRODUCED" and rc["result"] == "NO RACE":
        print("\n  CONFIRMED. The failure tracks the isolation level and nothing else,")
        print("  so ADR 3's claim that InnoDB handles this does not hold under ADR 5's")
        print("  choice of REPEATABLE READ.")
        verdict = 1
    elif rr["result"] == "NO RACE":
        print("\n  NOT REPRODUCED under this interleaving. The hypothesis is wrong, or")
        print("  the snapshot is not pinned where it was assumed to be. Do not claim")
        print("  this bug without a different test that does reproduce it.")
        verdict = 0
    else:
        print("\n  INCONCLUSIVE -- both arms behaved the same way, so isolation level is")
        print("  not the variable being measured. The test is at fault, not the code.")
        verdict = 0

    print("=" * 72)
    return verdict


if __name__ == "__main__":
    sys.exit(main())
