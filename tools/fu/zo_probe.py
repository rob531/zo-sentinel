#!/usr/bin/env python3
"""Run an FU verify predicate ON THE ZOCOMPUTER RUNTIME, from the tower.

Why
---
`write_service` (port 8772) binds loopback on the runtime -- `start_all.sh`
sets `PYTHONPATH=/home/workspace/zo_sentinel`, which is the Modal box, not
the tower. It was never reachable from the tower and was never meant to be.
The bridge is EXECUTION, not routing: `zo_call.py bash "<cmd>"` runs the
command on the runtime via the Zo MCP API (`hostname` there returns `modal`).

This matters beyond plumbing: FU-115 prescribes
`curl 127.0.0.1:8772/query ... service_health` as "the reliable probe" for
daemon liveness, without saying which host. A tower-side task following that
advice gets connection-refused and would conclude the daemon is DEAD -- the
exact false-positive FU-115 exists to prevent, resurfacing on the other side
of the bridge.

Three-state contract across the bridge
--------------------------------------
`zo_call.py` prints a CmdResult repr and does not reliably propagate the
remote exit code, so exit codes cannot cross the bridge. The inner script
therefore prints a sentinel and this wrapper maps it:

    FU_PASS             -> exit 0   (GREEN: fixed)
    FU_FAIL             -> exit 1   (RED: still broken)
    FU_BROKEN_PREDICATE -> exit 1   (RED: the PREDICATE is wrong, not the system)
    FU_UNKNOWN          -> exit 2   (could not evaluate)
    no sentinel         -> exit 2   (bridge itself failed; never read as evidence)

UNKNOWN vs BROKEN PREDICATE (MERGE_AUDIT_2026-08-23 B1)
-------------------------------------------------------
UNKNOWN must mean exactly one thing: THE BUS COULD NOT ANSWER -- unreachable,
timed out, empty body. A query the bus DID answer, with a catalog error, is a
defect in the predicate and is RED. Before this distinction existed, seven FU
predicates querying tables that exist on no plane at all resolved to UNKNOWN
forever: permanently un-closeable, reported as "cannot evaluate" rather than
"broken", and therefore never routed to a human. A nonexistent table can no
longer masquerade as UNKNOWN.

Quoting is handled by base64 so nothing has to survive
PowerShell -> Python -> JSON -> MCP -> bash intact.

Usage
    python zo_probe.py --sql "SELECT ..." --assert "int(v)==0"
    python zo_probe.py --bash "test -f /home/workspace/foo && echo yes"
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys

ZO_CALL = os.environ.get("ZO_CALL", r"C:\Users\robin\zo_call.py")
BUS = os.environ.get("FU_QUERY_ENDPOINT", "http://127.0.0.1:8772/query")
BRIDGE_TIMEOUT_S = 120

INNER_SQL = r"""
set -u
OUT=$(curl -s --max-time 25 -X POST {bus} -H 'Content-Type: application/json' \
      -d "$(cat <<'JSONEOF'
{payload}
JSONEOF
)" 2>/dev/null)
if [ -z "$OUT" ]; then echo FU_UNKNOWN; exit 0; fi
printf '%s' "$OUT" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print("FU_UNKNOWN"); sys.exit(0)
# MERGE_AUDIT_2026-08-23 B1. The bus answers a bad table with
#   {{"detail": "Catalog Error: Table with name X does not exist!"}}
# and NO rows key. That used to fall through to `if not rows` and report
# FU_UNKNOWN, so seven FU items querying tables that exist on NO plane were
# permanently un-closeable and reported as "cannot evaluate" rather than
# "broken". UNKNOWN means the BUS could not answer; a query the bus answered
# with an error is a DEFECT IN THE PREDICATE and must be RED, needing a human.
if isinstance(d, dict) and "rows" not in d and d.get("detail"):
    sys.stderr.write("PREDICATE ERROR: " + str(d["detail"])[:400] + chr(10))
    print("FU_BROKEN_PREDICATE"); sys.exit(0)
try:
    rows=d.get("rows") or d.get("result") or d.get("data") or []
    if not rows: print("FU_UNKNOWN"); sys.exit(0)
    r=rows[0]
    v=(r[0] if isinstance(r,(list,tuple)) else list(r.values())[0])
    if v is None: print("FU_UNKNOWN"); sys.exit(0)
    print("FU_PASS" if ({assertion}) else "FU_FAIL")
except Exception:
    print("FU_UNKNOWN")
'
"""

INNER_BASH = r"""
set -u
if {cmd} >/dev/null 2>&1; then echo FU_PASS; else echo FU_FAIL; fi
"""


def bridge(inner: str) -> str:
    b64 = base64.b64encode(inner.encode()).decode()
    cmd = [sys.executable, ZO_CALL, "bash", "echo %s | base64 -d | bash" % b64]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=BRIDGE_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return (p.stdout or "") + (p.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql")
    ap.add_argument("--assert", dest="assertion", default="True")
    ap.add_argument("--bash")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.sql:
        inner = INNER_SQL.format(bus=BUS, payload=json.dumps({"sql": args.sql}),
                                 assertion=args.assertion)
    elif args.bash:
        inner = INNER_BASH.format(cmd=args.bash)
    else:
        print("need --sql or --bash", file=sys.stderr)
        return 2

    out = bridge(inner)
    if args.verbose:
        print(out.strip()[:600], file=sys.stderr)

    # Order matters. Most specific first: a BROKEN PREDICATE is a definite
    # finding about the predicate, so it outranks every other sentinel -- it must
    # never be softened into UNKNOWN (B1). After that, UNKNOWN wins over PASS if
    # both somehow appear, because an ambiguous probe must never read as success.
    if "FU_BROKEN_PREDICATE" in out:
        print("BROKEN PREDICATE (RED): the bus rejected this query -- the "
              "predicate names something that does not exist. A human must fix "
              "the predicate; this is NOT evidence about the system.",
              file=sys.stderr)
        return 1
    if "FU_UNKNOWN" in out:
        return 2
    if "FU_FAIL" in out:
        return 1
    if "FU_PASS" in out:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
