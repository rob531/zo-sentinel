#!/usr/bin/env python3
"""
fix_threat_associations_default.py -- v2

v1 crashed because it queried information_schema.sequences (doesn't exist in
DuckDB). v2 uses duckdb_sequences() and duckdb_indexes() throughout.

Ground truth verified directly before writing this script:
  - sequence mcp_threat_associations_id_seq EXISTS
  - mcp_threat_associations.id column_default IS NULL (DEFAULT not set)
  - two non-primary indexes present: idx_threats_server, idx_threats_severity

Goal: drop indexes -> ALTER DEFAULT -> recreate indexes.
"""
import sys
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
TABLE = "mcp_threat_associations"
SEQ   = "mcp_threat_associations_id_seq"

KNOWN_INDEX_DDL = {
    "idx_threats_server":
        "CREATE INDEX idx_threats_server ON mcp_threat_associations(server_id)",
    "idx_threats_severity":
        "CREATE INDEX idx_threats_severity ON mcp_threat_associations(severity)",
}


def query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    r = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"query {r.status_code}: {r.text[:300]}")
    return r.json().get("rows", [])


def execute(sql):
    r = requests.post(f"{WRITE_SERVICE}/execute",
                      json={"sql": sql, "wait": True}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"execute {r.status_code}: {r.text[:300]}")


def id_has_nextval_default():
    rows = query(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='main' AND table_name=? AND column_name='id'",
        [TABLE],
    )
    if not rows:
        return False
    d = rows[0].get("column_default")
    return d is not None and "nextval" in str(d).lower()


def sequence_exists():
    rows = query(
        "SELECT sequence_name FROM duckdb_sequences() WHERE sequence_name = ?",
        [SEQ],
    )
    return bool(rows)


def non_pk_indexes():
    rows = query(
        "SELECT index_name, sql FROM duckdb_indexes() "
        "WHERE table_name = ? AND is_primary = false",
        [TABLE],
    )
    return rows


def main():
    print("=" * 60)
    print("Fix v2: mcp_threat_associations id default")
    print("=" * 60)

    if id_has_nextval_default():
        print("  [skip] id already has nextval default")
        return 0

    if not sequence_exists():
        print(f"  [FAIL] sequence {SEQ} missing")
        return 2
    print(f"  [ok] sequence {SEQ} present")

    indexes = non_pk_indexes()
    names = [r["index_name"] for r in indexes]
    print(f"  found non-PK indexes to drop/recreate: {names}")

    # Capture DDL (prefer live; fall back to known canned DDL)
    live_ddl = {r["index_name"]: (r["sql"] or "").rstrip(";") for r in indexes}

    for name in names:
        try:
            execute(f"DROP INDEX IF EXISTS {name}")
            print(f"  [drop] {name}")
        except Exception as e:
            print(f"  [FAIL] drop {name}: {e}")
            return 2

    try:
        execute(f"ALTER TABLE {TABLE} ALTER COLUMN id SET DEFAULT nextval('{SEQ}')")
        print(f"  [ok] {TABLE}.id DEFAULT set to nextval('{SEQ}')")
    except Exception as e:
        print(f"  [FAIL] ALTER DEFAULT: {e}")
        # Best-effort restore
        for name in names:
            ddl = live_ddl.get(name) or KNOWN_INDEX_DDL.get(name)
            if ddl:
                try: execute(ddl); print(f"  [restore] {name}")
                except Exception as e2: print(f"  [FAIL restore] {name}: {e2}")
        return 2

    # Recreate indexes
    for name in names:
        ddl = live_ddl.get(name) or KNOWN_INDEX_DDL.get(name)
        if not ddl:
            print(f"  [WARN] no DDL available to recreate {name}")
            continue
        try:
            execute(ddl)
            print(f"  [recreate] {name}")
        except Exception as e:
            print(f"  [WARN] recreate {name}: {e}")

    # Canary
    try:
        execute(
            "INSERT INTO mcp_threat_associations (server_id, threat_type, evidence, severity) "
            "VALUES ('__gate_canary_default_test__', 'canary', 'default verify', 'LOW')"
        )
        rows = query(
            "SELECT id FROM mcp_threat_associations WHERE server_id='__gate_canary_default_test__'"
        )
        new_id = rows[0]["id"] if rows else None
        execute(
            "DELETE FROM mcp_threat_associations WHERE server_id='__gate_canary_default_test__'"
        )
        print(f"  [verify] canary insert got id={new_id}, delete succeeded")
    except Exception as e:
        print(f"  [WARN] canary failed: {e}")
        return 2

    print("\n[OK] mcp_threat_associations default is live")
    print("     Next threat_intel_ingestor cycle will write successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())