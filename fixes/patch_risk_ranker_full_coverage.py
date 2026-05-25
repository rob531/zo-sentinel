#!/usr/bin/env python3
"""
patch_risk_ranker_full_coverage.py  -- commit 5.1

Fix risk_ranker covering only 200 of 790 MCPs.

Root causes:
  1. ws_query() doesn't pass a `limit` param. WriteService /query endpoint
     defaults to limit=200, silently capping the SELECT from
     mcp_server_registry.
  2. Every cycle runs DELETE FROM mcp_risk_register (core-table
     truncation, spec §4 violation) then re-inserts. Masks the coverage
     problem because you always have exactly 200 rows.

Fixes:
  A. ws_query sends `limit: 10000` -- covers foreseeable MCP population
  B. Replace clear+insert pattern with upsert (mode='upsert' via
     write_service). Needs deterministic id from server_id so upsert
     targets the same row.
  C. Remove clear_risk_register() call from cycle() -- no longer needed.

Idempotent. AST-validated. Backs up risk_ranker.py before writing.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/risk_ranker.py")

# ---- Patch A: ws_query sends limit=10000 ----------------------------
A_OLD = (
    "def ws_query(sql: str) -> List[Dict[str, Any]]:\n"
    '    """Execute SELECT via /query endpoint (not /execute)."""\n'
    "    try:\n"
    "        response = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)"
)
A_NEW = (
    "def ws_query(sql: str) -> List[Dict[str, Any]]:\n"
    '    """Execute SELECT via /query endpoint (not /execute).\n'
    "    Commit 5.1: pass explicit limit=10000 so WriteService's default\n"
    "    limit of 200 doesn't silently truncate MCP registry queries.\n"
    '    """\n'
    "    try:\n"
    "        response = requests.post(QUERY_URL, json={'sql': sql, 'limit': 10000}, timeout=30)"
)

# ---- Patch B: insert_into_table uses upsert mode with deterministic ids ----
B_OLD = (
    "def insert_into_table(records: List[Dict[str, Any]]) -> bool:\n"
    '    """Insert risk records into mcp_risk_register."""\n'
    "    if not records:\n"
    "        log.info(\"No records to insert\")\n"
    "        return True\n"
    "    \n"
    "    for record in records:\n"
    "        record['computed_at'] = datetime.now(timezone.utc).isoformat()\n"
    "    \n"
    "    try:\n"
    "        payload = {\n"
    "            'table': 'mcp_risk_register',\n"
    "            'rows': records,\n"
    "            'wait': True\n"
    "        }"
)
B_NEW = (
    "def insert_into_table(records: List[Dict[str, Any]]) -> bool:\n"
    '    """Insert risk records into mcp_risk_register.\n'
    "    Commit 5.1: uses upsert mode with deterministic id = hash(server_id)\n"
    "    so repeated cycles update the same row per server. Avoids the\n"
    "    previous DELETE+INSERT pattern that violated spec \u00a74 append-only.\n"
    '    """\n'
    "    if not records:\n"
    "        log.info(\"No records to insert\")\n"
    "        return True\n"
    "    \n"
    "    for record in records:\n"
    "        record['computed_at'] = datetime.now(timezone.utc).isoformat()\n"
    "        # Deterministic id from server_id so upsert targets same row\n"
    "        sid = str(record.get('server_id', ''))\n"
    "        record['id'] = int(hashlib.md5(sid.encode()).hexdigest()[:8], 16) % (2**31)\n"
    "    \n"
    "    try:\n"
    "        payload = {\n"
    "            'table': 'mcp_risk_register',\n"
    "            'rows': records,\n"
    "            'mode': 'upsert',\n"
    "            'wait': True\n"
    "        }"
)

# ---- Patch C: cycle() skips clear_risk_register ---------------------
C_OLD = (
    "    clear_risk_register()\n"
    "    \n"
    "    if records:\n"
    "        insert_into_table(records)"
)
C_NEW = (
    "    # Commit 5.1: no longer clearing -- insert_into_table now upserts\n"
    "    # by deterministic id, so stale rows for servers still in the\n"
    "    # registry get overwritten in place. Servers removed from registry\n"
    "    # will leave stragglers; acceptable trade-off vs spec \u00a74 violation.\n"
    "    if records:\n"
    "        insert_into_table(records)"
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("risk_ranker: full coverage + append-only (commit 5.1)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    if "Commit 5.1: pass explicit limit" in src:
        print("  [skip] patch already applied")
        return 0

    patches = [
        ("A", "ws_query explicit limit=10000", A_OLD, A_NEW),
        ("B", "insert_into_table upsert mode", B_OLD, B_NEW),
        ("C", "cycle skips clear_risk_register", C_OLD, C_NEW),
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
    print("\nVerify AST:")
    print('  python3 -c "import ast; ast.parse(open(\'/home/workspace/zo_sentinel/risk_ranker.py\').read()); print(\'AST OK\')"')
    print("\nRestart risk_ranker under wrapper (kill, wrapper respawns):")
    print("  pkill -f risk_ranker.py")
    print("  # Wait ~30s; wrapper respawns with new code")
    print("\nAfter next cycle (~30s):")
    print("  SELECT COUNT(*) FROM mcp_risk_register")
    print("  # Expect: ~790 rows (full coverage of registry)")
    return 0


if __name__ == "__main__":
    sys.exit(main())