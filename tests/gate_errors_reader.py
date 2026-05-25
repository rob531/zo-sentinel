#!/usr/bin/env python3
"""
gate_errors_reader.py

Read-only inspection tool for /home/workspace/gate_errors.db. Opens a read-only
connection so it never conflicts with the gate runner's single writer.

Usage:
    python3 gate_errors_reader.py                 # summary of recent runs
    python3 gate_errors_reader.py novel           # novel errors never seen before
    python3 gate_errors_reader.py persistent      # errors occurring 3+ times
    python3 gate_errors_reader.py recent 6        # gate activity in last 6 hours
    python3 gate_errors_reader.py daemons         # last daemon state snapshot
    python3 gate_errors_reader.py taxonomy        # known error classes
    python3 gate_errors_reader.py run <run_id>    # details of a specific run
    python3 gate_errors_reader.py health          # one-line health assessment

Safe to run any time -- does not touch the writer's lock because it opens
with read_only=True.
"""
import duckdb
import sys
import json
from datetime import datetime, timezone, timedelta

DB_PATH = "/home/workspace/gate_errors.db"


def connect():
    try:
        return duckdb.connect(DB_PATH, read_only=True)
    except duckdb.IOException as e:
        print(f"[FAIL] Cannot open {DB_PATH}: {e}")
        print("       Run gate_errors_bootstrap.py first.")
        sys.exit(1)


def _print_table(headers, rows, max_width=None):
    if not rows:
        print("  (no rows)")
        return
    widths = [len(h) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(str(v) if v is not None else "-"))
    if max_width:
        widths = [min(w, max_width) for w in widths]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        cells = []
        for i, v in enumerate(r):
            s = str(v) if v is not None else "-"
            if max_width and len(s) > max_width:
                s = s[:max_width-1] + "\u2026"
            cells.append(s)
        print(fmt.format(*cells))


def cmd_summary(con):
    print("\n=== RECENT GATE RUNS (last 10) ===\n")
    rows = con.execute("""
        SELECT
            SUBSTR(run_id, 1, 16) AS run,
            started_at,
            COALESCE(CAST(duration_ms/1000 AS VARCHAR) || 's', '-') AS duration,
            trigger,
            gates_passed || '/' || gates_planned AS result,
            gates_failed AS failed
        FROM gate_runs
        ORDER BY started_at DESC
        LIMIT 10
    """).fetchall()
    _print_table(
        ["run_id", "started", "dur", "trigger", "passed", "failed"],
        rows, max_width=25
    )
    total = con.execute("SELECT COUNT(*) FROM gate_runs").fetchone()[0]
    err_total = con.execute("SELECT COUNT(*) FROM gate_errors").fetchone()[0]
    err_novel = con.execute("SELECT COUNT(*) FROM gate_errors WHERE is_novel").fetchone()[0]
    print(f"\nTotal runs: {total}  |  Unique errors: {err_total}  |  Novel: {err_novel}")


def cmd_novel(con):
    print("\n=== NOVEL ERRORS (never seen before, awaiting classification) ===\n")
    rows = con.execute("""
        SELECT
            error_class,
            COALESCE(file, '-') AS file,
            COALESCE(CAST(line_no AS VARCHAR), '-') AS line,
            first_seen_at,
            COALESCE(expected, '-') AS expected
        FROM gate_errors
        WHERE is_novel = true
        ORDER BY first_seen_at DESC
    """).fetchall()
    _print_table(
        ["class", "file", "line", "first_seen", "expected"],
        rows, max_width=35
    )


def cmd_persistent(con):
    print("\n=== PERSISTENT ERRORS (3+ occurrences) ===\n")
    rows = con.execute("""
        SELECT
            error_class,
            COALESCE(file, '-') AS file,
            occurrence_count AS n,
            first_seen_at AS first,
            last_seen_at AS last
        FROM gate_errors
        WHERE occurrence_count >= 3
        ORDER BY occurrence_count DESC, last_seen_at DESC
    """).fetchall()
    _print_table(
        ["class", "file", "n", "first", "last"],
        rows, max_width=30
    )


def cmd_recent(con, hours=6):
    print(f"\n=== ACTIVITY IN LAST {hours}H ===\n")
    rows = con.execute(f"""
        SELECT
            SUBSTR(run_id, 1, 16) AS run,
            gate_name,
            check_name,
            status,
            started_at
        FROM gate_checks
        WHERE started_at > (now() - INTERVAL '{int(hours)} hours')
        ORDER BY started_at DESC
        LIMIT 50
    """).fetchall()
    _print_table(
        ["run", "gate", "check", "status", "at"],
        rows, max_width=30
    )


def cmd_daemons(con):
    print("\n=== LAST DAEMON STATE (most recent gate run) ===\n")
    rows = con.execute("""
        SELECT
            daemon_name,
            heartbeat_age_sec AS age_s,
            expected_cycle_sec AS cycle_s,
            heartbeat_grace_sec AS grace_s,
            is_within_cycle AS ok
        FROM daemon_state_at_gate
        WHERE run_id = (SELECT run_id FROM gate_runs ORDER BY started_at DESC LIMIT 1)
        ORDER BY daemon_name
    """).fetchall()
    _print_table(
        ["daemon", "age_s", "cycle_s", "grace_s", "ok"],
        rows
    )


def cmd_taxonomy(con):
    print("\n=== ERROR TAXONOMY ===\n")
    rows = con.execute("""
        SELECT class_name, severity, auto_fixable, description
        FROM error_taxonomy
        ORDER BY
            CASE severity
                WHEN 'critical' THEN 0
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                ELSE 3
            END,
            class_name
    """).fetchall()
    _print_table(
        ["class", "severity", "auto", "description"],
        rows, max_width=55
    )


def cmd_run(con, run_id):
    print(f"\n=== RUN {run_id} ===\n")
    meta = con.execute(
        "SELECT * FROM gate_runs WHERE run_id LIKE ?",
        [run_id + "%"]
    ).fetchone()
    if not meta:
        print(f"  No run found matching '{run_id}'")
        return
    cols = [c[0] for c in con.description]
    for c, v in zip(cols, meta):
        print(f"  {c:<20}  {v}")

    print("\n  Checks:")
    check_rows = con.execute("""
        SELECT gate_name, check_name, status, COALESCE(CAST(duration_ms AS VARCHAR), '-') AS ms
        FROM gate_checks
        WHERE run_id LIKE ?
        ORDER BY started_at
    """, [run_id + "%"]).fetchall()
    _print_table(
        ["gate", "check", "status", "ms"],
        check_rows, max_width=40
    )


def cmd_health(con):
    last_run = con.execute(
        "SELECT run_id, started_at, gates_passed, gates_failed "
        "FROM gate_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not last_run:
        print("HEALTH: no gate runs recorded yet")
        return
    run_id, started_at, passed, failed = last_run
    age = datetime.now(timezone.utc) - started_at
    age_h = age.total_seconds() / 3600
    novel = con.execute(
        "SELECT COUNT(*) FROM gate_errors WHERE is_novel"
    ).fetchone()[0]
    persistent = con.execute(
        "SELECT COUNT(*) FROM gate_errors WHERE occurrence_count >= 3"
    ).fetchone()[0]
    if failed == 0 and novel == 0:
        status = "GREEN"
    elif novel > 0:
        status = "AMBER (novel errors present)"
    else:
        status = "RED"
    print(f"HEALTH: {status}  |  last run {age_h:.1f}h ago  |  "
          f"passed={passed} failed={failed}  |  "
          f"novel={novel} persistent={persistent}")


def main():
    argv = sys.argv[1:] or ["summary"]
    cmd = argv[0]
    con = connect()
    try:
        if cmd == "summary":
            cmd_summary(con)
        elif cmd == "novel":
            cmd_novel(con)
        elif cmd == "persistent":
            cmd_persistent(con)
        elif cmd == "recent":
            hours = int(argv[1]) if len(argv) > 1 else 6
            cmd_recent(con, hours)
        elif cmd == "daemons":
            cmd_daemons(con)
        elif cmd == "taxonomy":
            cmd_taxonomy(con)
        elif cmd == "run" and len(argv) > 1:
            cmd_run(con, argv[1])
        elif cmd == "health":
            cmd_health(con)
        else:
            print(f"unknown command: {cmd}")
            print(__doc__)
            sys.exit(2)
    finally:
        con.close()


if __name__ == "__main__":
    main()