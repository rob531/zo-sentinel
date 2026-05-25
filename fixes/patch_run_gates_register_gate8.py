#!/usr/bin/env python3
"""
patch_run_gates_register_gate8.py

Register Gate 8 (new module smoke) with the gate orchestrator.

Two edits to /home/workspace/zo_sentinel/tests/gates/run_gates.py:
  A. Add the import line for Gate8NewModule
  B. Add entry to GATES dict AND to DEFAULT_ORDER (at the end, so it runs last)

Gate 8 runs last on purpose:
  - Gate 1 (infra) first -- no point testing built files if write_service is down
  - Gate 2 (schema) next -- catches schema drift early
  - Gate 5 (synthesis) depends on Gate 2's schema check
  - Gate 7 (threat flow) depends on Gate 1's world_articles presence
  - Gate 8 (new modules) depends on being able to query mesh_memory via
    write_service, so Gate 1 must pass first anyway. Last is safest.

Idempotent. AST-validated. Backup on write.

Run with: python3 /home/workspace/zo_sentinel/fixes/patch_run_gates_register_gate8.py
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/tests/gates/run_gates.py")

# ---- Patch A: add import line ---------------------------------------------
# Anchor on the existing Gate7 import so we land directly after it.
A_OLD = "from gate_7_threat_flow      import Gate7ThreatFlow"
A_NEW = "from gate_7_threat_flow      import Gate7ThreatFlow\nfrom gate_8_new_module      import Gate8NewModule"

# ---- Patch B: GATES dict entry + DEFAULT_ORDER ----------------------------
# Anchor on the existing GATES dict literal closing brace + DEFAULT_ORDER line.
B_OLD = """GATES = {
    1: ("infrastructure",    Gate1Infrastructure),
    2: ("schema_contracts",  Gate2SchemaContracts),
    5: ("synthesis_flow",    Gate5SynthesisFlow),
    7: ("threat_flow",       Gate7ThreatFlow),
}

# Recommended execution order:
#   1 first -- no point running the rest if the infra is down
#   2 next  -- static checks, fast, finds schema drift early
#   5 then  -- synthesis flow depends on schema being correct (Gate 2)
#   7 last  -- threat flow depends on world_articles + registry (Gate 1)
DEFAULT_ORDER = [1, 2, 5, 7]"""

B_NEW = """GATES = {
    1: ("infrastructure",    Gate1Infrastructure),
    2: ("schema_contracts",  Gate2SchemaContracts),
    5: ("synthesis_flow",    Gate5SynthesisFlow),
    7: ("threat_flow",       Gate7ThreatFlow),
    8: ("new_module",        Gate8NewModule),
}

# Recommended execution order:
#   1 first -- no point running the rest if the infra is down
#   2 next  -- static checks, fast, finds schema drift early
#   5 then  -- synthesis flow depends on schema being correct (Gate 2)
#   7 next  -- threat flow depends on world_articles + registry (Gate 1)
#   8 last  -- new module smoke needs mesh_memory via write_service (Gate 1 must pass)
DEFAULT_ORDER = [1, 2, 5, 7, 8]"""


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("run_gates.py: register Gate 8 (new module smoke)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    # Patch A: import
    if "from gate_8_new_module" in src:
        print("  [skip A] Gate8 import already present")
    elif A_OLD in src:
        src = src.replace(A_OLD, A_NEW, 1)
        print("  [patch A] Gate8 import added after Gate7")
        changed = True
    else:
        print("  [FAIL A] Gate7 import anchor not found verbatim")
        return 2

    # Patch B: GATES dict + DEFAULT_ORDER
    if "8: (\"new_module\"" in src and "DEFAULT_ORDER = [1, 2, 5, 7, 8]" in src:
        print("  [skip B] Gate8 already registered in GATES and DEFAULT_ORDER")
    elif B_OLD in src:
        src = src.replace(B_OLD, B_NEW, 1)
        print("  [patch B] Gate8 added to GATES and DEFAULT_ORDER")
        changed = True
    else:
        print("  [FAIL B] GATES dict + DEFAULT_ORDER anchor not found verbatim")
        return 2

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify:")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py --list")
    print("  # expect Gate 8 in the output")
    print("")
    print("Dry-run Gate 8 alone first (safer than the full suite):")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 8")
    print("  # expect self-smoke PASS, then 7-ish file checks from today's backfill")
    print("")
    print("Full suite:")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())