#!/usr/bin/env python3
"""
fix_attestation_type_and_risk_ranker.py

A) mcp_attestations.confidence_level FLOAT -> VARCHAR (rename-swap)
B) risk_ranker.ws_query routes SELECT to /execute -> fix to /query
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SENTINEL = Path("/home/workspace/zo_sentinel")
RISK = SENTINEL / "risk_ranker.py"
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


def query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    r = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"query {r.status_code}: {r.text[:200]}")
    return r.json().get("rows", [])


def execute(sql):
    r = requests.post(f"{WRITE_SERVICE}/execute",
                      json={"sql": sql, "wait": True}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"execute {r.status_code}: {r.text[:200]}")


def fix_attestations_type():
    print("\n=== Fix mcp_attestations.confidence_level type ===")
    rows = query(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='main' AND table_name='mcp_attestations' "
        "AND column_name='confidence_level'"
    )
    if not rows:
        print("  [skip] column not found"); return False
    current_type = rows[0]["data_type"]
    if current_type.upper() in ("VARCHAR", "TEXT"):
        print(f"  [skip] confidence_level already {current_type}"); return True
    print(f"  current type: {current_type}")

    count = query("SELECT COUNT(*) AS n FROM mcp_attestations")[0]["n"]
    print(f"  current rows: {count}")

    cols = query(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='main' AND table_name='mcp_attestations' "
        "ORDER BY ordinal_position"
    )
    col_names_csv = ", ".join(c["column_name"] for c in cols)
    print(f"  columns: {col_names_csv}")

    def col_ddl(c):
        dt = "VARCHAR" if c["column_name"] == "confidence_level" else c["data_type"]
        nn = "" if c["is_nullable"] == "YES" else " NOT NULL"
        default = f" DEFAULT {c['column_default']}" if c["column_default"] is not None else ""
        return f"  {c['column_name']} {dt}{nn}{default}"

    create_body = ",\n".join(col_ddl(c) for c in cols)

    cons = query(
        "SELECT constraint_type, constraint_column_names "
        "FROM duckdb_constraints() WHERE table_name='mcp_attestations'"
    )
    pk_cols = []
    unique_cols = []
    for c in cons:
        names = c.get("constraint_column_names", [])
        if c["constraint_type"] == "PRIMARY KEY":
            pk_cols = names
        elif c["constraint_type"] == "UNIQUE":
            unique_cols.append(names)

    trailing = ""
    if pk_cols:
        trailing += f",\n  PRIMARY KEY ({', '.join(pk_cols)})"
    for u in unique_cols:
        trailing += f",\n  UNIQUE ({', '.join(u)})"

    new_ddl = f"CREATE TABLE mcp_attestations__new (\n{create_body}{trailing}\n)"

    try:
        execute("DROP TABLE IF EXISTS mcp_attestations__new")
        execute(new_ddl)
        print("  [ok] created mcp_attestations__new")
        if count > 0:
            execute(
                f"INSERT INTO mcp_attestations__new ({col_names_csv}) "
                f"SELECT {col_names_csv} FROM mcp_attestations"
            )
            print(f"  [ok] copied {count} rows")
        execute("DROP TABLE mcp_attestations")
        execute("ALTER TABLE mcp_attestations__new RENAME TO mcp_attestations")
        print("  [ok] swap complete")
    except Exception as e:
        print(f"  [FAIL] swap: {e}"); return False

    rows = query(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='main' AND table_name='mcp_attestations' "
        "AND column_name='confidence_level'"
    )
    new_type = rows[0]["data_type"] if rows else None
    print(f"  [verify] confidence_level type = {new_type}")
    return new_type and new_type.upper() in ("VARCHAR", "TEXT")


RISK_OLD_EXEC = 'EXECUTE_URL = "http://127.0.0.1:8772/execute"'
RISK_NEW_EXEC = (
    'EXECUTE_URL = "http://127.0.0.1:8772/execute"\n'
    'QUERY_URL = "http://127.0.0.1:8772/query"'
)

RISK_OLD_WS_QUERY = (
    'def ws_query(sql: str) -> List[Dict[str, Any]]:\n'
    '    """Execute SQL query via write_service execute endpoint."""\n'
    '    try:\n'
    "        response = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)\n"
    '        if response.status_code == 200:\n'
    '            result = response.json()\n'
    "            if 'result' in result and isinstance(result['result'], list):\n"
    "                return result['result']\n"
    "            elif 'rows' in result:\n"
    "                return result['rows']\n"
    "            return result.get('data', [])\n"
    '        else:\n'
    '            log.error(f"Query failed: {response.status_code} - {response.text}")\n'
    '            return []\n'
    '    except Exception as e:\n'
    '        log.error(f"Query exception: {e}")\n'
    '        return []\n'
)

RISK_NEW_WS_QUERY = (
    'def ws_query(sql: str) -> List[Dict[str, Any]]:\n'
    '    """Execute SELECT via /query endpoint (not /execute)."""\n'
    '    try:\n'
    "        response = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)\n"
    '        if response.status_code == 200:\n'
    '            result = response.json()\n'
    "            if 'result' in result and isinstance(result['result'], list):\n"
    "                return result['result']\n"
    "            elif 'rows' in result:\n"
    "                return result['rows']\n"
    "            return result.get('data', [])\n"
    '        else:\n'
    '            log.error(f"Query failed: {response.status_code} - {response.text}")\n'
    '            return []\n'
    '    except Exception as e:\n'
    '        log.error(f"Query exception: {e}")\n'
    '        return []\n'
)


def fix_risk_ranker():
    print("\n=== Fix risk_ranker.py ws_query routing ===")
    if not RISK.exists():
        print(f"  [FAIL] {RISK} missing"); return False
    src = RISK.read_text()
    changed = False

    if "QUERY_URL = " in src:
        print("  [skip] QUERY_URL already present")
    elif RISK_OLD_EXEC in src:
        src = src.replace(RISK_OLD_EXEC, RISK_NEW_EXEC, 1)
        print("  [patch] added QUERY_URL constant")
        changed = True
    else:
        print("  [WARN] EXECUTE_URL literal not found")

    if RISK_OLD_WS_QUERY in src:
        src = src.replace(RISK_OLD_WS_QUERY, RISK_NEW_WS_QUERY, 1)
        print("  [patch] ws_query() rewritten to use /query")
        changed = True
    elif "requests.post(QUERY_URL" in src:
        print("  [skip] ws_query already using QUERY_URL")
    else:
        print("  [WARN] ws_query body didn't match expected form")

    if not changed:
        print("  [noop] nothing to patch"); return False

    _ast_check(RISK, src)
    _backup(RISK)
    RISK.write_text(src)
    print(f"  [done] {RISK.name} patched")
    return True


def main():
    print("=" * 60)
    print("Fix: attestations.confidence_level type + risk_ranker ws_query")
    print("=" * 60)
    results = {}
    for label, fn in [("attestation_type", fix_attestations_type),
                      ("risk_ranker",      fix_risk_ranker)]:
        try:
            results[label] = fn()
        except Exception as e:
            print(f"  [EXCEPTION] {label}: {e}")
            results[label] = False

    print("\n" + "=" * 60)
    for k, v in results.items():
        print(f"  {k:<20} {'ok' if v else 'no-op-or-failed'}")
    print("=" * 60)

    if results.get("risk_ranker") or results.get("attestation_type"):
        print("\nRestart commands:")
    if results.get("risk_ranker"):
        print("  pkill -9 -f 'python3 .*risk_ranker.py' 2>/dev/null")
        print("  rm -f /tmp/risk_ranker.lock")
        print("  sleep 2")
        print("  nohup python3 /home/workspace/zo_sentinel/risk_ranker.py "
              ">> /home/workspace/logs/sentinel_risk_ranker.log 2>&1 &")
        print("  python3 /home/workspace/zo_sentinel/tests/"
              "rebaseline_protected_files.py risk_ranker.py")
    if results.get("attestation_type"):
        print("  pkill -9 -f 'python3 .*attestation_engine.py' 2>/dev/null")
        print("  rm -f /var/run/zo/attestation_engine.pid")
        print("  sleep 2")
        print("  nohup python3 /home/workspace/zo_sentinel/attestation_engine.py "
              ">> /home/workspace/logs/sentinel_attestation_engine.log 2>&1 &")

    return 0


if __name__ == "__main__":
    sys.exit(main())