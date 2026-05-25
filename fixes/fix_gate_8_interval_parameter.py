#!/usr/bin/env python3
"""
fix_gate_8_interval_parameter.py

DuckDB rejects parameterized INTERVAL syntax. You can bind values, but
`INTERVAL ? HOUR` is a parse error -- INTERVAL expects a typed literal, not
a placeholder slot. My original SQL:

    AND created_at > now() - INTERVAL ? HOUR

fails with 'syntax error at or near "?"'.

Fix: inline the integer constant. Safe because LOOKBACK_HOURS is a module-
level int defined in the file, not user input -- no injection surface.

AST-validated. Idempotent. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/tests/gates/gate_8_new_module.py")

OLD = '''    rows = ws_query(
        "SELECT content, created_at FROM mesh_memory "
        "WHERE agent_id = 't1.zo_sentinel_builder' "
        "AND memory_type = 'build_artifact' "
        "AND created_at > now() - INTERVAL ? HOUR "
        "ORDER BY created_at ASC",
        params=[lookback_hours],
    )'''

NEW = '''    # DuckDB rejects parameterized INTERVAL (parse error on `?` inside
    # INTERVAL ? HOUR). Inline the int -- safe because lookback_hours is
    # a module-level constant, not user input.
    sql = (
        "SELECT content, created_at FROM mesh_memory "
        "WHERE agent_id = 't1.zo_sentinel_builder' "
        "AND memory_type = 'build_artifact' "
        f"AND created_at > now() - INTERVAL {int(lookback_hours)} HOUR "
        "ORDER BY created_at ASC"
    )
    rows = ws_query(sql)'''


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("gate_8_new_module: fix DuckDB INTERVAL parameter bug")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    # Idempotency -- new form already present?
    if "INTERVAL {int(lookback_hours)} HOUR" in src:
        print("  [skip] INTERVAL inlining already applied")
        return 0

    if OLD not in src:
        print("  [FAIL] anchor not found verbatim -- file may be edited")
        return 2

    src = src.replace(OLD, NEW, 1)
    print("  [patch] INTERVAL {lookback_hours} HOUR inlined as int literal")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nRe-run Gate 8:")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 8")
    return 0


if __name__ == "__main__":
    sys.exit(main())