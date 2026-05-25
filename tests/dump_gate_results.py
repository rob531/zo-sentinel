#!/usr/bin/env python3
"""
dump_gate_results.py -- Read-only dump of the most recent gate run(s).

Usage:
    python3 dump_gate_results.py               # most recent run only
    python3 dump_gate_results.py --last 2     # last 2 runs
    python3 dump_gate_results.py --all        # all runs ever

Connects read-only so it's safe to run while a gate run or bootstrap is active.
Outputs in a form I can parse back via zo_read_file.
"""
import duckdb
import json
import sys

DB = "/home/workspace/gate_errors.db"

def main():
    args = sys.argv[1:]
    last_n = 1
    if "--all" in args:
        last_n = None
    elif "--last" in args:
        i = args.index("--last")
        if i + 1 < len(args):
            last_n = int(args[i + 1])

    con = duckdb.connect(DB, read_only=True)
    try:
        lim = "" if last_n is None else f"LIMIT {last_n}"
        runs = con.execute(f"""
            SELECT run_id, started_at, finished_at, trigger,
                   gates_planned, gates_passed, gates_failed, duration_ms
            FROM gate_runs
            ORDER BY started_at DESC
            {lim}
        """).fetchall()

        print(f"\n=== FOUND {len(runs)} RUN(S) ===\n")
        for run_id, started, finished, trigger, planned, passed, failed, dur in runs:
            print(f"RUN {run_id}")
            print(f"  started:  {started}")
            print(f"  finished: {finished}")
            print(f"  duration: {dur}ms")
            print(f"  trigger:  {trigger}")
            print(f"  result:   {passed}/{planned} passed, {failed} failed")

            # Checks in this run
            checks = con.execute("""
                SELECT gate_name, check_name, status, details
                FROM gate_checks
                WHERE run_id = ?
                ORDER BY started_at
            """, [run_id]).fetchall()
            print(f"\n  CHECKS ({len(checks)}):")
            for gate, check, status, details in checks:
                marker = "[OK]" if status == "pass" else "[FAIL]"
                print(f"    {marker} {gate} :: {check}")
                if status != "pass" and details:
                    print(f"           details: {details[:200]}")

            # Errors triggered by this run
            errors = con.execute("""
                SELECT e.error_class, e.file, e.line_no, e.expected, e.actual,
                       e.remediation, e.is_novel, e.occurrence_count,
                       c.gate_name, c.check_name
                FROM gate_errors e
                JOIN gate_checks c ON c.check_id = e.check_id
                WHERE c.run_id = ?
                ORDER BY c.started_at
            """, [run_id]).fetchall()
            if errors:
                print(f"\n  ERRORS ({len(errors)}):")
                for (err_class, f, ln, exp, act, rem, novel, n, gate, check) in errors:
                    marker = "NOVEL" if novel else f"seen {n}x"
                    print(f"    * [{err_class}] ({marker})")
                    print(f"      gate:   {gate} :: {check}")
                    if f:
                        print(f"      file:   {f}" + (f":{ln}" if ln else ""))
                    if exp:
                        print(f"      want:   {exp[:200]}")
                    if act:
                        print(f"      got:    {act[:200]}")
                    if rem:
                        print(f"      fix:    {rem[:200]}")
            else:
                print("\n  NO ERRORS")
            print()

        # Global error ledger
        print("=== GLOBAL ERROR LEDGER ===\n")
        ledger = con.execute("""
            SELECT error_class, COUNT(*) AS sigs,
                   SUM(occurrence_count) AS total_occurrences,
                   SUM(CASE WHEN is_novel THEN 1 ELSE 0 END) AS still_novel
            FROM gate_errors
            GROUP BY error_class
            ORDER BY total_occurrences DESC
        """).fetchall()
        for cls, sigs, total, novel in ledger:
            print(f"  {cls:<35} signatures={sigs}  occurrences={total}  still_novel={novel}")

        # Canary memorializations
        print("\n=== CANARY HISTORY ===\n")
        canaries = con.execute("""
            SELECT run_id, canary_spec, observed_by, final_state, cleanup_ok
            FROM canary_history
            ORDER BY captured_at DESC
            LIMIT 5
        """).fetchall()
        for run_id, spec, obs, final, ok in canaries:
            print(f"  run {run_id}  cleanup_ok={ok}")
            try:
                s = json.loads(spec) if spec else {}
                f = json.loads(final) if final else {}
                o = json.loads(obs) if obs else []
                print(f"    canary_id:  {s.get('canary_id', '?')}")
                print(f"    observed:   {o}")
                print(f"    composite:  expected={s.get('expected_composite')}  "
                      f"computed={f.get('computed_composite')}")
            except Exception as e:
                print(f"    (parse failed: {e})")

    finally:
        con.close()

if __name__ == "__main__":
    main()