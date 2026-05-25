#!/usr/bin/env python3
"""
fix_id_sequences.py -- Add auto-generating sequences for id columns on
mcp_threat_associations and mcp_definition_history so daemons don't need
to generate IDs via SELECT MAX(id)+1 (race-prone and broken when /query
is silent).

Strategy:
  1. Create a sequence per table with a start value above current MAX(id)
  2. Set the id column DEFAULT to nextval(sequence)
  3. Leave PK/NOT NULL intact
  4. Verify a canary insert works without specifying id

After this fix, daemons can omit 'id' from their ws_write payloads and
DuckDB will assign one. Non-disruptive: explicit ids still work.

Idempotent. Re-run is a no-op.
"""
import sys
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"

TABLES = {
    "mcp_threat_associations":  "mcp_threat_associations_id_seq",
    "mcp_definition_history":   "mcp_definition_history_id_seq",
    "mcp_attestations":         "mcp_attestations_id_seq",
}


def query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    r = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=15)
    r.raise_for_status()
    return r.json().get("rows", [])


def execute(sql):
    r = requests.post(f"{WRITE_SERVICE}/execute",
                      json={"sql": sql, "wait": True}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"execute {r.status_code}: {r.text[:200]}")


def table_exists(name):
    rows = query(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name = ?",
        [name],
    )
    return bool(rows)


def id_column_has_default(name):
    rows = query(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='main' AND table_name = ? AND column_name='id'",
        [name],
    )
    if not rows:
        return False
    default = rows[0].get("column_default")
    return default is not None and "nextval" in str(default).lower()


def get_max_id(name):
    rows = query(f"SELECT COALESCE(MAX(id), 0) AS m FROM {name}")
    return rows[0]["m"] if rows else 0


def fix_one(table, seq_name):
    print(f"\n-- {table} --")
    if not table_exists(table):
        print("  [skip] table does not exist")
        return False

    if id_column_has_default(table):
        print("  [skip] id already has nextval default")
        return True

    max_id = get_max_id(table)
    start = max_id + 1
    print(f"  current MAX(id)={max_id}, sequence will start at {start}")

    # DuckDB CREATE SEQUENCE IF NOT EXISTS + START
    try:
        execute(f"CREATE SEQUENCE IF NOT EXISTS {seq_name} START {start}")
        print(f"  [ok] sequence {seq_name} created/present")
    except Exception as e:
        print(f"  [FAIL] sequence create: {e}")
        return False

    # Set default. DuckDB syntax: ALTER TABLE ... ALTER COLUMN ... SET DEFAULT
    try:
        execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{seq_name}')")
        print(f"  [ok] {table}.id DEFAULT set to nextval('{seq_name}')")
    except Exception as e:
        print(f"  [FAIL] ALTER DEFAULT: {e}")
        return False

    # Verify with canary insert: insert one row WITHOUT id, confirm it lands
    try:
        if table == "mcp_threat_associations":
            execute(
                "INSERT INTO mcp_threat_associations (server_id, threat_type, evidence, severity) "
                "VALUES ('__gate_canary_seq_test__', 'canary', 'seq verify', 'LOW')"
            )
            execute("DELETE FROM mcp_threat_associations WHERE server_id = '__gate_canary_seq_test__'")
        elif table == "mcp_definition_history":
            execute(
                "INSERT INTO mcp_definition_history (server_id, snapshot_hash) "
                "VALUES ('__gate_canary_seq_test__', 'canary')"
            )
            execute("DELETE FROM mcp_definition_history WHERE server_id = '__gate_canary_seq_test__'")
        elif table == "mcp_attestations":
            execute(
                "INSERT INTO mcp_attestations (server_id, attestation_text) "
                "VALUES ('__gate_canary_seq_test__', 'canary')"
            )
            execute("DELETE FROM mcp_attestations WHERE server_id = '__gate_canary_seq_test__'")
        print(f"  [verify] canary insert + delete succeeded")
    except Exception as e:
        print(f"  [WARN] canary insert failed: {e}")
        print("  (sequence default may still work from daemons, but this is a red flag)")
        return False

    return True


def main():
    print("=" * 60)
    print("Fix: auto-generating id sequences")
    print("=" * 60)
    results = {}
    for table, seq in TABLES.items():
        try:
            results[table] = fix_one(table, seq)
        except Exception as e:
            print(f"  [EXCEPTION] {table}: {e}")
            results[table] = False

    print("\n" + "=" * 60)
    for t, ok in results.items():
        print(f"  {t:<28} {'ok' if ok else 'failed'}")
    print("=" * 60)

    if all(results.values()):
        print("\nNo daemon restart needed -- the sequence applies to new writes")
        print("automatically. Next cycle of each daemon should write successfully.")
        print("\nVerify in ~5 minutes:")
        print("  SELECT COUNT(*) FROM mcp_threat_associations")
        print("  SELECT COUNT(*) FROM mcp_definition_history")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())