#!/usr/bin/env python3
"""Compact ledger dump -- status and deltas across runs."""
import duckdb
import json

DB = "/home/workspace/gate_errors.db"
con = duckdb.connect(DB, read_only=True)

print("=== ALL RUNS ===")
rows = con.execute("""
    SELECT r.run_id, r.started_at, r.duration_ms,
           r.gates_passed, r.gates_failed,
           (SELECT COUNT(*) FROM gate_checks c WHERE c.run_id = r.run_id AND c.status = 'pass') AS checks_pass,
           (SELECT COUNT(*) FROM gate_checks c WHERE c.run_id = r.run_id AND c.status != 'pass') AS checks_fail
    FROM gate_runs r
    ORDER BY r.started_at DESC
""").fetchall()
for run_id, started, dur, gp, gf, cp, cf in rows:
    status = "CLEAN" if cf == 0 else f"{cf} FAIL"
    print(f"  {run_id}  {started}  dur={dur}ms  checks: {cp} pass / {cf} fail  [{status}]")

print("\n=== ERROR LEDGER (all time) ===")
ledger = con.execute("""
    SELECT error_class, signature, is_novel, occurrence_count,
           first_seen_at, last_seen_at,
           COALESCE(SUBSTR(file, 40), '-') AS short_file
    FROM gate_errors
    ORDER BY last_seen_at DESC
""").fetchall()
if not ledger:
    print("  (empty)")
else:
    for cls, sig, novel, n, first, last, f in ledger:
        marker = "NOVEL" if novel else "known"
        print(f"  [{cls}] x{n}  {marker}  file={f}")
        print(f"      first: {first}")
        print(f"      last:  {last}")

print("\n=== CANARY HISTORY ===")
rows = con.execute("""
    SELECT run_id, cleanup_ok, final_state
    FROM canary_history
    ORDER BY captured_at DESC
    LIMIT 5
""").fetchall()
for run_id, ok, final in rows:
    try:
        fs = json.loads(final) if final else {}
        expected = fs.get('expected_composite')
        computed = fs.get('computed_composite')
        print(f"  {run_id}  cleanup_ok={ok}  expected={expected}  computed={computed}")
    except Exception:
        print(f"  {run_id}  cleanup_ok={ok}  (parse err)")

con.close()