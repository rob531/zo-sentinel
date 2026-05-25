#!/usr/bin/env python3
"""
fix_ui_risk_register_column.py

Fix: UI /api/risk-register SQL references `last_assessed` which doesn't exist
     on mcp_risk_register. The actual timestamp column is `computed_at`.
     The UI frontend doesn't use the timestamp field, so simplest fix is to
     drop it from the SELECT list.

Verified via live query:
  SELECT last_assessed FROM mcp_risk_register
  -> Binder Error: Referenced column "last_assessed" not found

Impact: /api/risk-register currently errors out -> UI shows
  "No entries in risk register yet." despite 200 rows in the table.

Idempotent. AST-validated. Backs up ui_server.py. Needs rebaseline + restart.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

UI = Path("/home/workspace/zo_sentinel/ui_server.py")

OLD = (
    'f"SELECT server_id, name, risk_rank, risk_tier, threat_count, "\n'
    '                f"staleness_days, last_assessed FROM mcp_risk_register "\n'
)
NEW = (
    'f"SELECT server_id, name, risk_rank, risk_tier, threat_count, "\n'
    '                f"staleness_days FROM mcp_risk_register "\n'
)


def main():
    print("=== Fix ui_server risk-register query ===")
    if not UI.exists():
        print(f"  [FAIL] {UI} missing"); return 2
    src = UI.read_text()

    if "staleness_days FROM mcp_risk_register" in src and "last_assessed FROM mcp_risk_register" not in src:
        print("  [skip] already patched"); return 0

    if OLD not in src:
        print("  [FAIL] exact OLD block not found")
        print("    -- the query may have been edited already.")
        print("    -- manual fix: remove 'last_assessed' from the SELECT in get_risk_register()")
        return 2

    src = src.replace(OLD, NEW, 1)
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid: {e}"); return 2

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = UI.with_suffix(f".py.bak.{ts}")
    shutil.copy2(UI, bak)
    UI.write_text(src)
    print(f"  [backup] {bak.name}")
    print(f"  [done] {UI.name} patched")
    print("\nRestart ui_server and rebaseline:")
    print("  pkill -9 -f 'python3 .*ui_server.py' 2>/dev/null")
    print("  sleep 2")
    print("  nohup python3 /home/workspace/zo_sentinel/ui_server.py "
          ">> /home/workspace/logs/sentinel_ui_server.log 2>&1 &")
    print("  python3 /home/workspace/zo_sentinel/tests/rebaseline_protected_files.py ui_server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())