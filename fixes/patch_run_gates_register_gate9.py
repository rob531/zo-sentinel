#!/usr/bin/env python3
"""
patch_run_gates_register_gate9.py  -- commit 4.3 completion

Register Gate 9 (signal diversity) in the orchestrator so gate_scheduler's
6h cadence runs it alongside the others.

Three edits:
  A. Import Gate9SignalDiversity
  B. Add {9: ('signal_diversity', Gate9SignalDiversity)} to GATES dict
  C. Append 9 to DEFAULT_ORDER (runs last -- cheapest check, no DB writes
     beyond read queries, no side effects)

Idempotent, AST-validated, backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/tests/gates/run_gates.py")

A_OLD = 'from gate_8_new_module      import Gate8NewModule'
A_NEW = (
    'from gate_8_new_module      import Gate8NewModule\n'
    'from gate_9_signal_diversity import Gate9SignalDiversity'
)

B_OLD = "    8: (\"new_module\",        Gate8NewModule),\n}"
B_NEW = (
    "    8: (\"new_module\",        Gate8NewModule),\n"
    "    9: (\"signal_diversity\",  Gate9SignalDiversity),\n}"
)

C_OLD = "DEFAULT_ORDER = [1, 2, 5, 7, 8]"
C_NEW = "DEFAULT_ORDER = [1, 2, 5, 7, 8, 9]"


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("run_gates: register Gate 9 signal_diversity (commit 4.3)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    if "Gate9SignalDiversity" in src:
        print("  [skip] Gate 9 already registered")
        return 0

    patches = [
        ("A", "import Gate9SignalDiversity", A_OLD, A_NEW),
        ("B", "GATES dict adds id=9",         B_OLD, B_NEW),
        ("C", "DEFAULT_ORDER appends 9",       C_OLD, C_NEW),
    ]

    for label, desc, old, new in patches:
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}")

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
    print("  # Should show gates 1, 2, 5, 7, 8, 9")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 9")
    print("  # Runs Gate 9 in isolation")
    return 0


if __name__ == "__main__":
    sys.exit(main())