#!/usr/bin/env python3
"""
seed_taxonomy_additions.py -- Idempotent add of taxonomy classes for Gates 1 and 7.

Safe to run any time. Uses LEFT JOIN anti-insert pattern so re-runs are no-ops.
Connects via the same lock-retry profile as gate_errors_bootstrap.py.
"""
import duckdb
import sys
import time

DB = "/home/workspace/gate_errors.db"
RETRIES = 5
BACKOFF = 1.5

ADDITIONS = [
    ("heartbeat_stale",
     "Daemon heartbeat is present but older than expected_cycle + grace",
     "high", False,
     "Restart daemon; check its cycle log for stalls; adjust grace in "
     "daemon_cycle_config if the cycle is legitimately slow"),
    ("pattern_match_failed",
     "A daemon's regex patterns do not match a canary input they should match",
     "medium", True,
     "Sync gate's pattern constant to daemon's pattern constant (or vice versa)"),
]


def connect():
    for i in range(RETRIES):
        try:
            return duckdb.connect(DB)
        except duckdb.IOException as e:
            if "lock" in str(e).lower() and i < RETRIES - 1:
                time.sleep(BACKOFF * (i + 1))
                continue
            raise
    raise RuntimeError(f"could not acquire {DB} lock")


def main():
    con = connect()
    try:
        con.execute(
            "CREATE TEMP TABLE _seed_addl (class_name VARCHAR, description TEXT, "
            "severity VARCHAR, auto_fixable BOOLEAN, example_fix TEXT)"
        )
        con.executemany(
            "INSERT INTO _seed_addl VALUES (?, ?, ?, ?, ?)",
            ADDITIONS,
        )
        added = con.execute("""
            INSERT INTO error_taxonomy
                (class_name, description, severity, auto_fixable, example_fix)
            SELECT s.class_name, s.description, s.severity, s.auto_fixable, s.example_fix
            FROM _seed_addl s
            LEFT JOIN error_taxonomy t ON t.class_name = s.class_name
            WHERE t.class_name IS NULL
            RETURNING class_name
        """).fetchall()
        con.execute("DROP TABLE _seed_addl")
        print(f"[OK] added {len(added)} new taxonomy entries: {[r[0] for r in added] or '(none, all already present)'}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())