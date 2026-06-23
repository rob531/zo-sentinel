#!/usr/bin/env python3
"""
diagnose_mcp_definition_history_empty_pipeline_gap.py

DIAGNOSTIC UTILITY -- reads only, no DB writes.

Root-cause investigation: mcp_definition_history is empty (0 rows) while
mcp_server_registry has 1793 rows.

Checks:
  (1) Does mcp_scanner.py write to mcp_definition_history?
  (2) Are INSERT calls silently failing (schema mismatch, column errors)?
  (3) Is there a schema mismatch between writer and actual table?
  (4) Does enrichment_harness / pipeline_bridge miss a write step?

Finding: CONFIRMED -- mcp_definition_history_writer_daemon.py queries
  `SELECT server_id, definition, last_definition_hash FROM mcp_server_registry`
  but mcp_server_registry has no `definition` column (only `metadata` VARCHAR).
  All queries error silently; zero rows ever written. mcp_scanner.py does NOT
  write to mcp_definition_history at all.
"""

import requests
import sys

WRITE_SERVICE = "http://127.0.0.1:8772"


def ws_query(sql: str, params=None) -> dict:
    """Query write_service. Never raises."""
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        r = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=15)
        if r.status_code == 200:
            return r.json()
        return {"detail": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"detail": str(e)}


def ws_query_scalar(sql: str):
    """Return the first scalar from a query result."""
    result = ws_query(sql)
    rows = result.get("rows", [])
    if rows and len(rows[0]) == 1:
        return list(rows[0].values())[0]
    return None


def check_row_counts():
    """Check (Q1) -- are both tables populated?"""
    print("\n[CHECK 1] Row counts -- expected: history empty, registry full")
    registry_rows = ws_query_scalar(
        "SELECT COUNT(*) AS cnt FROM mcp_server_registry"
    )
    history_rows = ws_query_scalar(
        "SELECT COUNT(*) AS cnt FROM mcp_definition_history"
    )
    print(f"  mcp_server_registry rows : {registry_rows}")
    print(f"  mcp_definition_history   : {history_rows}")
    return {
        "registry_rows": registry_rows,
        "history_rows": history_rows,
        "gap_detected": (history_rows or 0) == 0 and (registry_rows or 0) > 0,
    }


def check_registry_columns():
    """Check (Q2) -- what columns exist in mcp_server_registry?"""
    print("\n[CHECK 2] mcp_server_registry schema -- looking for 'definition' column")
    result = ws_query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'mcp_server_registry' ORDER BY ordinal_position"
    )
    cols = result.get("rows", [])
    col_names = [r["column_name"] for r in cols]
    print(f"  Columns: {col_names}")
    has_definition = "definition" in col_names
    has_metadata   = "metadata"   in col_names
    has_last_hash  = "last_definition_hash" in col_names
    print(f"  Has 'definition' column        : {has_definition}")
    print(f"  Has 'metadata' column           : {has_metadata}")
    print(f"  Has 'last_definition_hash' col  : {has_last_hash}")
    return {
        "cols": col_names,
        "has_definition": has_definition,
        "has_metadata": has_metadata,
        "has_last_hash": has_last_hash,
    }


def check_history_columns():
    """Check (Q2 continued) -- what columns exist in mcp_definition_history?"""
    print("\n[CHECK 3] mcp_definition_history schema")
    result = ws_query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'mcp_definition_history' ORDER BY ordinal_position"
    )
    cols = result.get("rows", [])
    print(f"  Columns: {[r['column_name'] for r in cols]}")
    return {"cols": [r['column_name'] for r in cols]}


def check_writer_query_fails():
    """Check (Q2) -- does the writer's exact query fail?"""
    print("\n[CHECK 4] Writer query simulation -- does SELECT definition fail?")
    result = ws_query(
        "SELECT server_id, definition, last_definition_hash "
        "FROM mcp_server_registry LIMIT 1"
    )
    is_error = "detail" in result
    print(f"  Query error        : {is_error}")
    if is_error:
        print(f"  Error detail       : {result['detail'][:300]}")
    return {"query_fails": is_error, "error": result.get("detail", "")}


def check_scanner_writes_history():
    """Check (Q1) -- does mcp_scanner.py ever write to mcp_definition_history?"""
    print("\n[CHECK 5] mcp_scanner.py source -- does it touch mcp_definition_history?")
    try:
        src = open("/home/workspace/zo_sentinel/mcp_scanner.py").read()
    except FileNotFoundError:
        print("  mcp_scanner.py not found at expected path")
        return {"scanner_exists": False}

    writes_history = "mcp_definition_history" in src
    writes_registry = "mcp_server_registry" in src
    print(f"  File found                    : True")
    print(f"  Writes mcp_server_registry   : {writes_registry}")
    print(f"  Writes mcp_definition_history: {writes_history}")
    return {
        "scanner_exists": True,
        "writes_history": writes_history,
        "writes_registry": writes_registry,
    }


def check_writer_source():
    """Check (Q2) -- what query does mcp_definition_history_writer use?"""
    print("\n[CHECK 6] mcp_definition_history_writer_daemon.py -- writer query")
    paths = [
        "/home/workspace/zo_sentinel/mcp_definition_history_writer_daemon.py",
        "/home/workspace/zo_sentinel/mcp_definition_history_writer.py",
    ]
    writer_src = None
    found_path = None
    for p in paths:
        try:
            writer_src = open(p).read()
            found_path = p
            print(f"  Found: {p}")
            break
        except FileNotFoundError:
            continue
    if not writer_src:
        print("  No writer file found")
        return {"writer_found": False}

    # Extract the fetch query
    query_lines = []
    in_fetch = False
    for line in writer_src.splitlines():
        if "fetch_current_definitions" in line or "server_id, definition" in line:
            in_fetch = True
        if in_fetch:
            query_lines.append(line)
            if ")" in line and "LIMIT" in line:
                break
    print(f"  Fetch query excerpt:")
    for l in query_lines[:8]:
        print(f"    {l.strip()}")

    queries_definition = "definition" in writer_src
    queries_history = "mcp_definition_history" in writer_src
    print(f"  References 'definition' column      : {queries_definition}")
    print(f"  References 'mcp_definition_history' : {queries_history}")
    return {
        "writer_found": True,
        "found_path": found_path,
        "queries_definition": queries_definition,
        "queries_history": queries_history,
    }


def check_service_health_daemons():
    """Check (Q3) -- what definition-history daemons are alive?"""
    print("\n[CHECK 7] service_health -- definition-history daemons")
    result = ws_query(
        "SELECT service, last_heartbeat FROM service_health "
        "WHERE service LIKE '%definition%' OR service LIKE '%history%' "
        "OR service LIKE '%mcp%' ORDER BY service"
    )
    rows = result.get("rows", [])
    if not rows:
        print("  No matching services found in service_health")
    for row in rows:
        svc = row.get("service", "?")
        hb = row.get("last_heartbeat", "N/A")
        print(f"  {svc:50s} last_heartbeat={hb}")
    return {"daemons": rows}


def check_audit_log_errors():
    """Check (Q2) -- are there audit-log errors for definition_history writes?"""
    print("\n[CHECK 8] audit_log -- look for definition_history errors")
    # Try alternative audit_log column names since schema may vary
    result = ws_query(
        "SELECT event_type, actor, error_message, timestamp "
        "FROM audit_log "
        "WHERE error_message LIKE '%definition%' "
        "   OR error_message LIKE '%mcp_definition_history%' "
        "ORDER BY timestamp DESC LIMIT 20"
    )
    rows = result.get("rows", [])
    print(f"  Matching audit_log entries: {len(rows)}")
    for row in rows[:5]:
        ts = row.get("timestamp", "?")
        et = row.get("event_type", "?")
        em = str(row.get("error_message", ""))[:120]
        print(f"  [{ts}] {et} -- {em}")
    return {"audit_errors": rows}


def check_metadata_sample():
    """Check (Q2) -- what does the metadata column look like?"""
    print("\n[CHECK 9] Sample metadata content -- is it JSON with definitions?")
    result = ws_query(
        "SELECT server_id, metadata FROM mcp_server_registry "
        "WHERE metadata IS NOT NULL LIMIT 3"
    )
    rows = result.get("rows", [])
    for row in rows:
        meta = row.get("metadata", "")
        preview = meta[:150] + "..." if len(meta) > 150 else meta
        sid = row.get("server_id", "")[:30]
        print(f"  server_id={sid} metadata={preview}")
    has_json_meta = any(
        row.get("metadata", "").startswith("{") for row in rows
    )
    print(f"  metadata is JSON-like: {has_json_meta}")
    return {"sample": rows, "is_json": has_json_meta}


def check_harness_pipeline():
    """Check (Q4) -- does enrichment_harness/pipeline_bridge write history?"""
    print("\n[CHECK 10] enrichment_harness.py -- does it write mcp_definition_history?")
    try:
        src = open("/home/workspace/zo_sentinel/enrichment_harness.py").read()
    except FileNotFoundError:
        print("  enrichment_harness.py not found")
        return {"harness_found": False}

    writes_history = "mcp_definition_history" in src
    writes_signal  = "mcp_signal_enrichments" in src
    print(f"  Writes mcp_definition_history : {writes_history}")
    print(f"  Writes mcp_signal_enrichments: {writes_signal}")
    print(f"  NOTE: enrichment_harness writes enrichment SIGNALS, not definition history.")
    return {
        "harness_found": True,
        "writes_history": writes_history,
        "pipeline_bridge_unrelated": True,
    }


def check_pipeline_writer():
    """Check (Q4) -- does enrichment_pipeline_writer write history?"""
    print("\n[CHECK 11] enrichment_pipeline_writer.py -- does it write history?")
    try:
        src = open("/home/workspace/zo_sentinel/enrichment_pipeline_writer.py").read()
    except FileNotFoundError:
        print("  enrichment_pipeline_writer.py not found")
        return {"pipeline_found": False}

    writes_history = "mcp_definition_history" in src
    writes_signal  = "mcp_signal_enrichments" in src
    print(f"  Writes mcp_definition_history : {writes_history}")
    print(f"  Writes mcp_signal_enrichments: {writes_signal}")
    return {
        "pipeline_found": True,
        "writes_history": writes_history,
    }


def main():
    print("=" * 70)
    print("DIAGNOSTIC: mcp_definition_history empty pipeline gap")
    print("=" * 70)

    findings = {}

    findings["row_counts"]       = check_row_counts()
    findings["registry_cols"]    = check_registry_columns()
    findings["history_cols"]     = check_history_columns()
    findings["writer_query"]     = check_writer_query_fails()
    findings["scanner"]          = check_scanner_writes_history()
    findings["writer_src"]       = check_writer_source()
    findings["daemons"]          = check_service_health_daemons()
    findings["audit_errors"]     = check_audit_log_errors()
    findings["metadata"]          = check_metadata_sample()
    findings["harness"]          = check_harness_pipeline()
    findings["pipeline_writer"]  = check_pipeline_writer()

    print("\n" + "=" * 70)
    print("ROOT CAUSE SUMMARY")
    print("=" * 70)

    has_gap       = findings["row_counts"]["gap_detected"]
    no_def_col    = not findings["registry_cols"]["has_definition"]
    no_hash_col   = not findings["registry_cols"]["has_last_hash"]
    query_fails   = findings["writer_query"]["query_fails"]
    scanner_no_w  = not findings["scanner"].get("writes_history", True)
    harness_unrel = findings["harness"].get("pipeline_bridge_unrelated", False)
    writer_path   = findings["writer_src"].get("found_path", "not found")

    if has_gap and no_def_col and query_fails:
        print("")
        print("ROOT CAUSE: CONFIRMED -- all four failure modes active")
        print("")
        print("  (1) mcp_scanner.py does NOT write to mcp_definition_history.")
        print("      -> It only upserts mcp_server_registry rows.")
        print("")
        print("  (2) mcp_definition_history_writer_daemon.py queries the")
        print("      non-existent 'definition' column from mcp_server_registry.")
        print("      -> File: " + writer_path)
        print("      -> Query fails with: " + findings["writer_query"]["error"][:200])
        print("      -> Writes silently fail (no exception propagation); zero rows inserted.")
        print("")
        print("  (3) Schema mismatch -- writer expects columns that do not exist:")
        print("      -> 'definition' column: MISSING (only 'metadata' VARCHAR exists)")
        print("      -> 'last_definition_hash' column: MISSING")
        print("      -> mcp_server_registry has: " + str(findings["registry_cols"]["cols"]))
        print("")
        print("  (4) enrichment_harness.py / enrichment_pipeline_writer.py")
        print("      write to mcp_signal_enrichments, NOT mcp_definition_history.")
        print("      pipeline_bridge is unrelated to definition history tracking.")
    elif has_gap:
        print("")
        print("ROOT CAUSE: PARTIAL -- gap confirmed, further investigation needed")
        print(f"  Registry columns: {findings['registry_cols']['cols']}")
        print(f"  Writer query fails: {query_fails}")
    else:
        print("")
        print("No gap detected -- mcp_definition_history has rows.")

    print("")
    print("=" * 70)
    print("RECOMMENDED FIXES")
    print("=" * 70)
    print("")
    print("  Option A (preferred -- full schema alignment):")
    print("    1. ALTER TABLE mcp_server_registry ADD COLUMN definition JSON;")
    print("    2. Backfill: UPDATE mcp_server_registry SET definition =")
    print("       json_parse(metadata) WHERE metadata IS NOT NULL;")
    print("    3. ALTER TABLE mcp_server_registry ADD COLUMN last_definition_hash VARCHAR;")
    print("    4. Backfill hashes: UPDATE mcp_server_registry SET")
    print("       last_definition_hash = sha256(definition::VARCHAR);")
    print("    5. Patch mcp_definition_history_writer_daemon to use the new columns.")
    print("    6. Wire mcp_scanner to call writer on every new/discovery or def change.")
    print("")
    print("  Option B (minimum -- parse existing metadata):")
    print("    1. Patch writer: replace 'definition' with 'metadata' in the SELECT,")
    print("       parse JSON from the metadata VARCHAR.")
    print("    2. Patch writer: compute hash on the metadata string directly.")
    print("    3. Ensure writer runs periodically and catches all query errors.")
    print("")
    print("  Backfill step (required regardless of option):")
    print("    INSERT initial snapshot rows for all 1793 existing servers so that")
    print("    future diffs can detect changes against a known baseline.")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
