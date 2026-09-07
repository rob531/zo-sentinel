"""Run all nine rewritten predicates through zo_probe.py and report rc for each.

Exit code: 0 if ALL nine return rc=0 or rc=1 (never rc=2).
           1 if any return rc=2 (UNKNOWN -- rewrite failed to fix the route).

Usage:
    python D:\zo\_cc\fu-242\tools\fu242_validate_rewrites.py
"""
from __future__ import annotations

import subprocess
import sys

ZO_PROBE = r"D:\zo\Zocomputer Agents\_tools\zo_probe.py"

REWRITES = [
    ("FU-001", "SELECT COUNT(*) FROM agent_runs WHERE started_at > now() - INTERVAL '14 days'", "v is not None and int(v)>0"),
    ("FU-024", "SELECT COALESCE(EXTRACT(EPOCH FROM (now()-MAX(created_at)))/3600, 9999) FROM mesh_events WHERE event_type LIKE '%backup%'", "v is not None and float(v)<36"),
    ("FU-054", "SELECT COUNT(*) FROM mcp_server_registry WHERE first_seen > now() - INTERVAL '24 hours'", "v is not None and int(v)>=100"),
    ("FU-058", "SELECT ROUND(100.0*COUNT(*) FILTER (WHERE risk_tier IN ('HIGH','CRITICAL'))/NULLIF(COUNT(*),0),2) FROM mcp_server_registry", "v is not None and float(v)<90"),
    ("FU-090", "SELECT ROUND(100.0*(SELECT COUNT(DISTINCT server_id) FROM mcp_signal_scores)/NULLIF((SELECT COUNT(*) FROM mcp_server_registry),0),2)", "v is not None and float(v)>60"),
    ("FU-093", "SELECT ROUND(100.0*COUNT(*) FILTER (WHERE risk_tier IS NOT NULL AND confidence IS NOT NULL)/NULLIF(COUNT(*),0),2) FROM mcp_server_registry", "v is not None and float(v)>50"),
    ("FU-104", "SELECT COUNT(*) FROM agent_runs WHERE status IS NULL OR status=''", "v is not None and int(v)==0"),
    ("FU-107", "SELECT COALESCE(EXTRACT(EPOCH FROM (now()-MAX(created_at)))/3600, 9999) FROM mesh_events WHERE event_type LIKE '%backup%' AND event_type NOT LIKE '%fail%'", "v is not None and float(v)<36"),
    ("FU-108", "SELECT COUNT(*) FROM mcp_signal_scores WHERE scored_at > '2026-07-26'", "v is not None and int(v)>0"),
]

RC_NAMES = {0: "GREEN", 1: "RED", 2: "UNKNOWN"}

def main() -> int:
    results = []
    any_unknown = False

    for fu, sql, assertion in REWRITES:
        cmd = [sys.executable, ZO_PROBE, "--sql", sql, "--assert", assertion]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = 2
            print(f"{fu}: TIMEOUT (counted as UNKNOWN)", file=sys.stderr)

        label = RC_NAMES.get(rc, f"rc={rc}")
        ok = rc in (0, 1)
        marker = "OK" if ok else "FAIL"
        print(f"[{marker}] {fu}: {label} (rc={rc})")
        results.append((fu, rc, ok))
        if not ok:
            any_unknown = True

    print()
    greens = sum(1 for _, rc, _ in results if rc == 0)
    reds = sum(1 for _, rc, _ in results if rc == 1)
    unknowns = sum(1 for _, rc, _ in results if rc == 2)
    print(f"Summary: {greens} GREEN / {reds} RED / {unknowns} UNKNOWN out of {len(results)}")

    if any_unknown:
        print("FAIL: one or more predicates still return UNKNOWN -- rewrite incomplete")
        return 1

    print("PASS: all nine predicates return rc=0 or rc=1 (FU-242 verify condition met)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
