#!/usr/bin/env python3
"""
fix_remaining_ws_query_plus_pk.py -- v2, regex-free.

v1 hung in a catastrophic-backtracking regex on ws_query body parse.
v2 uses plain string anchoring: exact-match signatures, no regex on source.

Fixes:
  1. threat_intel_ingestor.py  -- ws_query routes SELECT to /execute (404/500)
  2. attestation_engine.py     -- same pattern if present
  3. mcp_definition_history    -- missing PK constraint blocks upserts

Idempotent. AST-validated. Backs up each file it touches.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SENTINEL = Path("/home/workspace/zo_sentinel")
TIS      = SENTINEL / "threat_intel_ingestor.py"
ATT      = SENTINEL / "attestation_engine.py"
WRITE_SERVICE = "http://127.0.0.1:8772"


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def _ast_check(path, src):
    try:
        ast.parse(src)
    except SyntaxError as e:
        raise RuntimeError(f"AST invalid for {path.name}: {e}")


TIS_OLD_EXEC_CONST = "EXECUTE_URL = 'http://127.0.0.1:8772/execute'"
TIS_NEW_EXEC_CONST = (
    "EXECUTE_URL = 'http://127.0.0.1:8772/execute'\n"
    "QUERY_URL = 'http://127.0.0.1:8772/query'"
)

TIS_OLD_WS_QUERY = (
    "def ws_query(sql: str, params: Optional[List] = None) -> List[Dict]:\n"
    "    payload = {'sql': sql}\n"
    "    if params:\n"
    "        payload['params'] = params\n"
    "    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)\n"
    "    resp.raise_for_status()\n"
    "    result = resp.json()\n"
    "    if isinstance(result, list):\n"
    "        return result\n"
    "    if isinstance(result, dict) and 'results' in result:\n"
    "        return result['results']\n"
    "    return []\n"
)

TIS_NEW_WS_QUERY = (
    "def ws_query(sql: str, params: Optional[List] = None) -> List[Dict]:\n"
    '    """Execute SELECT via write_service /query endpoint.\n'
    "    Routes to /query (not /execute) so rows come back. /execute is\n"
    '    fire-and-forget and returns {ok:true} with no rows."""\n'
    "    payload = {'sql': sql}\n"
    "    if params:\n"
    "        payload['params'] = params\n"
    "    resp = requests.post(QUERY_URL, json=payload, timeout=30)\n"
    "    resp.raise_for_status()\n"
    "    body = resp.json()\n"
    "    if isinstance(body, list):\n"
    "        return body\n"
    "    if isinstance(body, dict):\n"
    "        if 'rows' in body:\n"
    "            return body['rows']\n"
    "        if 'results' in body:\n"
    "            return body['results']\n"
    "    return []\n"
)

TIS_OLD_WS_WRITE = (
    "def ws_write(table: str, rows: Any, wait: bool = True) -> Dict:\n"
    "    url = f'{WRITE_SERVICE_URL}/write'\n"
)
TIS_NEW_WS_WRITE = (
    "def ws_write(table: str, rows: Any, wait: bool = True) -> Dict:\n"
    "    url = WRITE_SERVICE_URL  # already ends in /write\n"
)


def fix_threat_intel():
    print("\n=== Fix threat_intel_ingestor.py ===")
    if not TIS.exists():
        print(f"  [FAIL] {TIS} missing"); return False
    src = TIS.read_text()
    changed = False

    if TIS_OLD_EXEC_CONST in src and "QUERY_URL = 'http://127.0.0.1:8772/query'" not in src:
        src = src.replace(TIS_OLD_EXEC_CONST, TIS_NEW_EXEC_CONST, 1)
        print("  [patch] added QUERY_URL constant"); changed = True
    else:
        print("  [skip] QUERY_URL constant already present")

    if TIS_OLD_WS_QUERY in src:
        src = src.replace(TIS_OLD_WS_QUERY, TIS_NEW_WS_QUERY, 1)
        print("  [patch] ws_query() rewritten to use /query"); changed = True
    elif "requests.post(QUERY_URL" in src:
        print("  [skip] ws_query already using QUERY_URL")
    else:
        print("  [WARN] ws_query signature did not match; leaving alone")

    if TIS_OLD_WS_WRITE in src:
        src = src.replace(TIS_OLD_WS_WRITE, TIS_NEW_WS_WRITE, 1)
        print("  [patch] ws_write() double-slash URL fixed"); changed = True
    elif "url = WRITE_SERVICE_URL  # already ends in /write" in src:
        print("  [skip] ws_write double-slash already fixed")

    if not changed:
        print("  [noop] nothing to patch"); return False

    _ast_check(TIS, src)
    _backup(TIS)
    TIS.write_text(src)
    print(f"  [done] {TIS.name} patched")
    return True


def fix_attestation():
    print("\n=== Fix attestation_engine.py ===")
    if not ATT.exists():
        print(f"  [FAIL] {ATT} missing"); return False
    src = ATT.read_text()
    if "def ws_query" not in src:
        print("  [skip] no ws_query in attestation_engine"); return False
    if "requests.post(QUERY_URL" in src:
        print("  [skip] already using QUERY_URL"); return False
    if "EXECUTE_URL" not in src:
        print("  [skip] no EXECUTE_URL constant"); return False

    lines = src.splitlines(keepends=True)
    inserted_const_already = (
        "QUERY_URL = 'http://127.0.0.1:8772/query'" in src
        or 'QUERY_URL = "http://127.0.0.1:8772/query"' in src
    )
    new_lines, did_insert = [], False
    for line in lines:
        new_lines.append(line)
        if not inserted_const_already and not did_insert and "EXECUTE_URL = " in line:
            new_lines.append("QUERY_URL = 'http://127.0.0.1:8772/query'\n")
            did_insert = True
    if did_insert:
        print("  [patch] added QUERY_URL constant")

    final_lines, in_ws_query, ws_indent, patched = [], False, 0, 0
    for line in new_lines:
        s = line.lstrip()
        if s.startswith("def ws_query("):
            in_ws_query = True
            ws_indent = len(line) - len(s)
            final_lines.append(line); continue
        if in_ws_query:
            cur = len(line) - len(line.lstrip())
            if line.strip() and cur <= ws_indent:
                in_ws_query = False
                final_lines.append(line); continue
            if "EXECUTE_URL" in line and "requests.post" in line:
                final_lines.append(line.replace("EXECUTE_URL", "QUERY_URL"))
                patched += 1
                continue
        final_lines.append(line)

    if patched == 0 and not did_insert:
        print("  [noop] nothing to patch in attestation_engine"); return False
    if patched:
        print(f"  [patch] rerouted {patched} ws_query request.post call(s)")

    new_src = "".join(final_lines)
    _ast_check(ATT, new_src)
    _backup(ATT)
    ATT.write_text(new_src)
    print(f"  [done] {ATT.name} patched")
    return True


def query(sql, params=None, timeout=15):
    payload = {"sql": sql}
    if params: payload["params"] = params
    r = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("rows", [])


def execute(sql, timeout=30):
    r = requests.post(f"{WRITE_SERVICE}/execute",
                      json={"sql": sql, "wait": True}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"execute failed: {r.status_code} {r.text[:200]}")


def fix_definition_history_pk():
    print("\n=== Fix mcp_definition_history PK ===")
    try:
        cons = query(
            "SELECT constraint_type FROM duckdb_constraints() "
            "WHERE table_name = 'mcp_definition_history'"
        )
    except Exception as e:
        print(f"  [FAIL] constraint lookup: {e}"); return False
    if any(c["constraint_type"] in ("PRIMARY KEY", "UNIQUE") for c in cons):
        print("  [skip] already has PK/UNIQUE"); return True

    try:
        row = query(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT id) AS distinct_n "
            "FROM mcp_definition_history"
        )[0]
        n, distinct_n = row["n"], row["distinct_n"]
        print(f"  current: rows={n}, distinct_ids={distinct_n}")
    except Exception as e:
        print(f"  [FAIL] count query: {e}"); return False

    try:
        cols = query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name='mcp_definition_history' "
            "ORDER BY ordinal_position"
        )
        col_list = ", ".join(c["column_name"] for c in cols)
        print(f"  columns: {col_list}")
    except Exception as e:
        print(f"  [FAIL] column lookup: {e}"); return False

    try:
        execute("DROP TABLE IF EXISTS mcp_definition_history__new")
        execute("""
            CREATE TABLE mcp_definition_history__new (
                id               BIGINT PRIMARY KEY,
                server_id        VARCHAR NOT NULL,
                snapshot_hash    VARCHAR,
                snapshot_content TEXT,
                captured_at      TIMESTAMPTZ DEFAULT now()
            )
        """)
        execute(f"""
            INSERT INTO mcp_definition_history__new ({col_list})
            SELECT {col_list} FROM (
              SELECT {col_list},
                     ROW_NUMBER() OVER (PARTITION BY id
                                        ORDER BY captured_at DESC) AS rn
              FROM mcp_definition_history
            ) WHERE rn = 1
        """)
        new_count = query("SELECT COUNT(*) AS n FROM mcp_definition_history__new")[0]["n"]
        print(f"  new table loaded: {new_count} dedup'd rows")
        if new_count != distinct_n:
            raise RuntimeError(f"count mismatch: expected {distinct_n}, got {new_count}")
        execute("DROP TABLE mcp_definition_history")
        execute("ALTER TABLE mcp_definition_history__new RENAME TO mcp_definition_history")
    except Exception as e:
        print(f"  [FAIL] swap: {e}"); return False

    cons_after = query(
        "SELECT constraint_type FROM duckdb_constraints() "
        "WHERE table_name = 'mcp_definition_history'"
    )
    has_pk = any(c["constraint_type"] in ("PRIMARY KEY", "UNIQUE") for c in cons_after)
    print(f"  [done] PK present: {has_pk}")
    return has_pk


def main():
    print("=" * 60)
    print("Fix v2: ws_query routing + mcp_definition_history PK")
    print("=" * 60)
    results = {}
    for label, fn in [("threat_intel", fix_threat_intel),
                      ("attestation",  fix_attestation),
                      ("pk",           fix_definition_history_pk)]:
        try:
            results[label] = fn()
        except Exception as e:
            print(f"  [EXCEPTION] {label}: {e}"); results[label] = False

    print("\n" + "=" * 60)
    for k, v in results.items():
        print(f"  {k:<15} {'ok' if v else 'no-op-or-failed'}")
    print("=" * 60)

    if results.get("threat_intel") or results.get("attestation"):
        print("\nRestart and rebaseline:")
        restart, rebase_files = [], []
        if results["threat_intel"]:
            restart.append("threat_intel_ingestor")
            rebase_files.append("threat_intel_ingestor.py")
        if results["attestation"]:
            restart.append("attestation_engine")
            rebase_files.append("attestation_engine.py")
        for s in restart:
            print(f"  pkill -9 -f 'python3 .*{s}.py' 2>/dev/null")
        print("  sleep 2")
        for s in restart:
            print(f"  nohup python3 /home/workspace/zo_sentinel/{s}.py "
                  f">> /home/workspace/logs/sentinel_{s}.log 2>&1 &")
        print(f"  python3 /home/workspace/zo_sentinel/tests/"
              f"rebaseline_protected_files.py {' '.join(rebase_files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())